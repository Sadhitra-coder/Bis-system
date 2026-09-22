"""app/playground/router.py

FastAPI Router for the BIS Agent Playground and Knowledge Coverage Dashboard.
Provides:
- GET /playground (Interactive Chat Playground SPA)
- GET /playground/dashboard (Administrative Diagnostic Dashboard)
- GET /playground/api/stats (Live corpus and coverage statistics)
- POST /playground/api/query (Safe browser-to-agent conversational endpoint)
"""

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.config import DATA_DIR, RAW_DATA_DIR, VECTOR_DB_DIR, settings
from app.models import QueryRequest, QueryResponse
from app.release.manifest import get_latest_release, list_releases, load_release_manifest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/playground", tags=["playground"])

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_DB_PATH = DATA_DIR / "knowledge" / "bis_knowledge.db"


class PlaygroundQueryRequest(BaseModel):
    query: str = Field(..., min_length=2, description="Compliance question")
    top_k: Optional[int] = Field(default=5, ge=1, le=20)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def serve_playground():
    """Serves the BIS Agent Playground UI."""
    html_file = STATIC_DIR / "index.html"
    if not html_file.exists():
        raise HTTPException(status_code=404, detail="Playground UI assets not found")
    with open(html_file, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@router.get("/dashboard", response_class=HTMLResponse)
async def serve_dashboard():
    """Serves the Knowledge Coverage Dashboard view."""
    html_file = STATIC_DIR / "index.html"
    if not html_file.exists():
        raise HTTPException(status_code=404, detail="Dashboard UI assets not found")
    with open(html_file, "r", encoding="utf-8") as f:
        content = f.read().replace('data-initial-tab="chat"', 'data-initial-tab="dashboard"')
        return HTMLResponse(content=content)


@router.get("/api/stats")
def get_coverage_stats(request: Request) -> Dict[str, Any]:
    """
    Returns administrative and diagnostic coverage metrics for the BIS knowledge factory.
    """
    stats: Dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "ONLINE",
        "jurisdiction": "INDIA",
        "counts": {
            "discovered_documents": 0,
            "standards_metadata": 0,
            "eligible_documents": 0,
            "downloaded_documents": 0,
            "indexed_chunks": 0,
            "clauses": 0,
            "qcos": 0,
            "amendments": 0,
            "schemes": 0,
            "laboratories": 0,
            "test_methods": 0,
            "products": 0,
            "knowledge_relationships": 0,
            "sources": 0,
            "blocked_by_rights": 0,
            "failed_downloads": 0,
        },
        "rights_breakdown": {},
        "sectors": [],
        "current_corpus_release": None,
        "azure_deployment": {
            "deployed_release": "corpus-release-0001",
            "environment": "complywise-env",
            "region": "eastasia",
            "target": "bis-system-v5.bravesand-4d6fbaeb.eastasia.azurecontainerapps.io",
            "status": "RUNNING",
        },
    }

    # 1. Query SQLite
    if DEFAULT_DB_PATH.exists():
        try:
            with sqlite3.connect(str(DEFAULT_DB_PATH)) as conn:
                conn.row_factory = sqlite3.Row
                c = conn.cursor()

                # Table counts
                def get_count(t):
                    try:
                        return c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                    except Exception:
                        return 0

                stats["counts"]["discovered_documents"] = get_count("discovered_documents")
                stats["counts"]["standards_metadata"] = get_count("standards_metadata_catalog")
                stats["counts"]["clauses"] = get_count("clauses")
                stats["counts"]["qcos"] = get_count("qcos")
                stats["counts"]["amendments"] = get_count("amendments")
                stats["counts"]["schemes"] = get_count("certification_schemes")
                stats["counts"]["laboratories"] = get_count("laboratories")
                stats["counts"]["test_methods"] = get_count("test_methods")
                stats["counts"]["products"] = get_count("products")
                stats["counts"]["knowledge_relationships"] = get_count("knowledge_relationships")
                stats["counts"]["sources"] = get_count("source_registry")

                # Rights breakdown
                try:
                    for row in c.execute("SELECT rights_status, count(*) as cnt FROM discovered_documents GROUP BY rights_status").fetchall():
                        stats["rights_breakdown"][row["rights_status"]] = row["cnt"]
                except Exception:
                    pass

                # Blocked / failed
                try:
                    stats["counts"]["blocked_by_rights"] = c.execute("SELECT count(*) FROM discovered_documents WHERE download_status = 'BLOCKED_ACCESS'").fetchone()[0]
                    stats["counts"]["failed_downloads"] = c.execute("SELECT count(*) FROM discovered_documents WHERE download_status = 'FAILED'").fetchone()[0]
                except Exception:
                    pass

                # Sectors
                try:
                    sectors = [r[0] for r in c.execute("SELECT DISTINCT category FROM standards_metadata_catalog WHERE category IS NOT NULL ORDER BY category").fetchall()]
                    stats["sectors"] = sectors
                except Exception:
                    pass

        except Exception as exc:
            logger.error("Error reading database stats: %s", exc)

    # 2. Vector chunks count
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(VECTOR_DB_DIR))
        total_chunks = sum(col.count() for col in client.list_collections())
        stats["counts"]["indexed_chunks"] = total_chunks
    except Exception:
        # Fallback to sqlite
        try:
            with sqlite3.connect(str(VECTOR_DB_DIR / "chroma.sqlite3")) as conn:
                stats["counts"]["indexed_chunks"] = conn.execute("SELECT count(*) FROM embeddings").fetchone()[0]
        except Exception:
            pass

    # 3. Downloaded files count
    bis_raw = RAW_DATA_DIR / "bis"
    if bis_raw.exists():
        pdf_count = len(list(bis_raw.glob("doc_*/*.pdf")))
        stats["counts"]["downloaded_documents"] = pdf_count
        stats["counts"]["eligible_documents"] = stats["counts"]["discovered_documents"] - stats["counts"]["blocked_by_rights"]

    # 4. Corpus release information
    latest = get_latest_release()
    if latest:
        stats["current_corpus_release"] = {
            "release_id": latest.release_id,
            "created_at": latest.created_at,
            "git_commit": latest.git_commit,
            "manifest_version": latest.manifest_version,
            "metrics": latest.metrics,
        }

    return stats


@router.post("/api/query")
async def execute_playground_query(payload: PlaygroundQueryRequest, request: Request):
    """
    Executes a query through the internal BIS RAG pipeline.
    Eliminates client-side secret exposure while returning full evidence and citations.
    """
    rag_pipeline = getattr(request.app.state, "rag_pipeline", None)
    if not rag_pipeline:
        from app.rag.pipeline import RAGPipeline
        from app.rag.retriever import HybridRetriever
        try:
            embedder = getattr(request.app.state, "embedder", None)
            if not embedder:
                from app.steps.embed import load_embedding_model
                embedder = load_embedding_model()
                request.app.state.embedder = embedder

            collection = getattr(request.app.state, "collection", None)
            if not collection:
                from app.steps.embed import get_collection
                collection = get_collection()
                request.app.state.collection = collection

            retriever = HybridRetriever(
                embedder=embedder,
                collection=collection
            )
            rag_pipeline = RAGPipeline(
                retriever=retriever,
                reranker=getattr(request.app.state, "reranker", None),
                generator=getattr(request.app.state, "generator", None)
            )
            request.app.state.rag_pipeline = rag_pipeline
        except Exception as exc:
            logger.error("RAG pipeline failed to initialize for playground query: %s", exc)
            raise HTTPException(
                status_code=503,
                detail=f"BIS Intelligence Engine is initializing. Please retry in a few moments: {exc}"
            )

    top_k = payload.top_k or 5
    try:
        result = rag_pipeline.query(
            payload.query,
            retrieval_top_k=max(top_k, settings.RETRIEVAL_TOP_K),
            rerank_top_k=top_k,
        )

        return {
            "query_id": result.get("query_id"),
            "query": payload.query,
            "answer": result.get("answer", ""),
            "decision": result.get("decision", "ANSWERED"),
            "confidence_score": result.get("confidence_score", 0.0),
            "confidence_level": result.get("confidence_level", "MEDIUM"),
            "grounding_status": result.get("grounding_status", "GROUNDED"),
            "verification_required": result.get("verification_required", False),
            "verification_reason": result.get("verification_reason"),
            "evidence_summary": result.get("evidence_summary"),
            "sources": result.get("sources", []),
            "citations": result.get("citations", []),
            "claims": result.get("claims", []),
            "candidate_standards": result.get("candidate_standards", []),
            "temporal_status": result.get("temporal_status", "CURRENT"),
            "model": result.get("model", "HybridRetriever+Reranker"),
            "retrieved_chunks_count": (
                len(result["retrieved_chunks"])
                if isinstance(result.get("retrieved_chunks"), list)
                else int(result.get("retrieved_chunks", 0) or 0)
            ),
        }
    except Exception as exc:
        logger.exception("Error executing playground query: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
