from fastapi import APIRouter, Request

from app.config import settings

router = APIRouter(prefix="/status", tags=["status"])


@router.get("")
def get_status(request: Request):
    collection = getattr(request.app.state, "collection", None)
    chunks_count = collection.count() if collection is not None else 0

    return {
        "status": "ready" if chunks_count > 0 else "empty",
        "collection_name": settings.CHROMA_COLLECTION_NAME,
        "chunks_indexed": chunks_count,
        "llm_available": settings.llm_available,
        "models": {
            "embedding_model": settings.EMBEDDING_MODEL,
            "reranker_model": settings.RERANKER_MODEL,
            "generator_model": settings.GROQ_MODEL if settings.llm_available else None
        }
    }

