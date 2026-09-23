import shutil
from pathlib import Path
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


@router.get("/disk")
def get_disk_status():
    total, used, free = shutil.disk_usage("/")

    def get_dir_size_mb(path_str: str) -> float:
        p = Path(path_str)
        if not p.exists():
            return 0.0
        total_size = 0
        try:
            for item in p.rglob("*"):
                if item.is_file():
                    total_size += item.stat().st_size
        except Exception:
            pass
        return round(total_size / (1024 * 1024), 2)

    return {
        "root_disk": {
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
            "total_gb": round(total / (1024**3), 2),
            "used_gb": round(used / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
            "free_percent": round((free / total) * 100, 1),
        },
        "directories_mb": {
            "/app": get_dir_size_mb("/app"),
            "/app/model_cache": get_dir_size_mb("/app/model_cache"),
            "/app/data": get_dir_size_mb("/app/data"),
            "/tmp": get_dir_size_mb("/tmp"),
            "/root/.cache": get_dir_size_mb("/root/.cache"),
        }
    }

