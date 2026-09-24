import logging
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request, Response

from app.auth import CorrelationIdMiddleware, verify_internal_service_key
from app.config import ensure_directories, settings
from app.api import upload, query, status, jobs
from app.index_integrity import (
    INDEX_UNAVAILABLE,
    IndexIntegrityReport,
    check_index_integrity,
    log_integrity_report,
)
from app.steps.embed import get_collection, load_embedding_model
from app.rag.reranker import Reranker
from app.rag.generator import AnswerGenerator
from app.rag.retriever import HybridRetriever
from app.rag.pipeline import RAGPipeline

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Initialize heavyweight components (embedding model, vector store,
    reranker, generator) once at application startup.
    """
    logger.info("Starting up BIS RAG Engine...")
    ensure_directories()

    # 1. Load embedder and vector store collection
    logger.info("Initializing embedding model...")
    try:
        embedder = load_embedding_model()
        app.state.embedder = embedder
    except Exception as e:
        logger.warning("Could not initialize embedding model at startup: %s", e)
        app.state.embedder = None

    logger.info("Initializing ChromaDB collection...")
    try:
        collection = get_collection()
        app.state.collection = collection
    except Exception as e:
        logger.warning("Could not initialize Chroma collection at startup: %s", e)
        app.state.collection = None

    # 2. Load reranker
    logger.info("Initializing reranker...")
    try:
        reranker = Reranker()
        app.state.reranker = reranker
    except Exception as e:
        logger.warning("Could not initialize reranker at startup: %s", e)
        app.state.reranker = None

    # 3. Load generator
    app.state.llm_last_error = None
    if settings.llm_available:
        try:
            generator = AnswerGenerator()
            app.state.generator = generator
            logger.info("AnswerGenerator initialized successfully at startup.")
        except Exception as e:
            logger.error("Could not initialize generator at startup: %s", e, exc_info=True)
            app.state.generator = None
            app.state.llm_last_error = f"{type(e).__name__}: {str(e)}"
    else:
        logger.info("LLM is disabled or OPENAI_API_KEY is not set.")
        app.state.generator = None
        app.state.llm_last_error = "LLM is disabled (LLM_ENABLED=False) or OPENAI_API_KEY is not set."

    # 4. Inspect the persisted index, then initialize the RAG pipeline.
    try:
        if collection is not None:
            report = check_index_integrity(collection)
        else:
            report = IndexIntegrityReport(
                state=INDEX_UNAVAILABLE,
                message="ChromaDB collection was not loaded at startup.",
                remediation="Inspect ChromaDB path and collection initialization.",
            )
    except Exception as e:
        logger.warning("Index integrity check failed to run: %s", e)
        report = IndexIntegrityReport(
            state=INDEX_UNAVAILABLE,
            message=f"Integrity check raised: {e}",
            remediation="Inspect the ChromaDB path.",
        )
    log_integrity_report(report)
    app.state.index_integrity = report

    try:
        if report.is_retrievable and app.state.embedder is not None:
            # A stale or partly-invalid index is still wired up: refusing to
            # serve would turn degraded provenance into a total outage, and
            # /health already reports the truth about it.
            if not report.is_healthy:
                logger.warning(
                    "Building retriever over an index in state %s. Answers will "
                    "carry degraded provenance until a re-index is run.",
                    report.state,
                )
            retriever = HybridRetriever(
                embedder=app.state.embedder,
                collection=app.state.collection
            )
            app.state.rag_pipeline = RAGPipeline(
                retriever=retriever,
                reranker=app.state.reranker,
                generator=app.state.generator
            )
            logger.info("RAGPipeline initialized and ready on startup.")
        else:
            logger.info(
                "Index state %s or embedder not yet ready; pipeline will initialize upon first query/ingestion.",
                report.state,
            )
            app.state.rag_pipeline = None
    except Exception as e:
        logger.warning("Could not initialize HybridRetriever on startup: %s", e)
        app.state.rag_pipeline = None

    yield

    logger.info("Shutting down BIS RAG Engine...")


def ensure_generator(target_app: FastAPI) -> AnswerGenerator | None:
    """
    Dynamic health-recheck and recovery mechanism for the AnswerGenerator.

    If the generator was uninitialized at startup due to transient failure or
    missing credentials at container boot, this re-evaluates configuration and
    attempts instantiation on-demand so transient errors don't permanently disable
    generation.
    """
    if getattr(target_app.state, "generator", None) is not None:
        return target_app.state.generator

    if not settings.llm_available:
        target_app.state.llm_last_error = (
            "LLM is disabled (LLM_ENABLED=False) or OPENAI_API_KEY is not set."
        )
        return None

    try:
        generator = AnswerGenerator()
        target_app.state.generator = generator
        target_app.state.llm_last_error = None
        logger.info("AnswerGenerator successfully initialized via dynamic recovery.")

        pipeline = getattr(target_app.state, "rag_pipeline", None)
        if pipeline is not None:
            pipeline.generator = generator

        return generator
    except Exception as e:
        logger.error(
            "Dynamic initialization of AnswerGenerator failed: %s",
            e,
            exc_info=True,
        )
        target_app.state.generator = None
        target_app.state.llm_last_error = f"{type(e).__name__}: {str(e)}"
        return None


app = FastAPI(
    title="BIS RAG Engine",
    lifespan=lifespan
)

app.add_middleware(CorrelationIdMiddleware)

from app.playground.router import router as playground_router

# Mount API routers (protect upload and query with internal service key)
app.include_router(upload.router, dependencies=[Depends(verify_internal_service_key)])
app.include_router(query.router, dependencies=[Depends(verify_internal_service_key)])
app.include_router(status.router)
app.include_router(jobs.router)
app.include_router(playground_router)



@app.get("/health")
def health_check():
    """
    Liveness. The process is up and serving.

    Deliberately does NOT consider index state: a stale index is a data
    problem, not a reason for an orchestrator to restart the container.
    Use /ready for that.
    """
    return {"status": "ok"}


@app.get("/ready")
def readiness_check(request: Request, response: Response):
    """
    Readiness, including whether the persisted index matches the schema the
    running code expects.

    Returns 503 when the index cannot be trusted. The previous behaviour —
    reporting 'ok' whenever the collection was non-empty — let an index full
    of superseded metadata look healthy indefinitely.

    State is read from request.app rather than the module-level `app` so the
    handler reports on whichever application is actually serving it.
    """
    state = request.app.state
    report = getattr(state, "index_integrity", None)

    if report is None:
        response.status_code = 503
        return {
            "status": "not_ready",
            "reason": "Index integrity has not been evaluated yet.",
            "index": None,
        }

    if not report.is_healthy:
        response.status_code = 503

    return {
        "status": "ready" if report.is_healthy else "degraded",
        "pipeline_ready": getattr(state, "rag_pipeline", None) is not None,
        "index": report.to_dict(),
    }

