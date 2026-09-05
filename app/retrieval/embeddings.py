from __future__ import annotations

import logging
from typing import Optional

from sentence_transformers import SentenceTransformer

from app.config import settings


logger = logging.getLogger(__name__)

_EMBEDDING_MODEL_INSTANCE: Optional[SentenceTransformer] = None


def get_embedding_model() -> SentenceTransformer:
    """
    Get or initialize the singleton SentenceTransformer embedding model.
    Uses model specified in settings.EMBEDDING_MODEL.
    """
    global _EMBEDDING_MODEL_INSTANCE

    if _EMBEDDING_MODEL_INSTANCE is None:
        model_name = settings.EMBEDDING_MODEL or "BAAI/bge-small-en-v1.5"
        logger.info("Initializing embedding model: %s", model_name)
        try:
            _EMBEDDING_MODEL_INSTANCE = SentenceTransformer(model_name)
            logger.info("Embedding model loaded successfully: %s", model_name)
        except Exception as exc:
            logger.warning(
                "Failed to load configured embedding model %s: %s. Trying fallback BAAI/bge-small-en-v1.5",
                model_name,
                exc,
            )
            _EMBEDDING_MODEL_INSTANCE = SentenceTransformer("BAAI/bge-small-en-v1.5")

    return _EMBEDDING_MODEL_INSTANCE


def get_embedding_dimension() -> int:
    """Return the output dimension of the configured embedding model."""
    model = get_embedding_model()
    dim = model.get_sentence_embedding_dimension()
    return int(dim) if dim is not None else 384


def embed_texts(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """
    Generate normalized dense vector embeddings for a list of texts.
    Returns list of float vectors.
    """
    if not texts:
        return []

    model = get_embedding_model()
    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=False,
        normalize_embeddings=True,
    )

    return [embedding.tolist() for embedding in embeddings]


def embed_query(query: str) -> list[float]:
    """
    Generate normalized dense vector embedding for a single user query.
    """
    if not query or not query.strip():
        raise ValueError("Query string cannot be empty for embedding")

    model = get_embedding_model()
    embedding = model.encode(
        query.strip(),
        normalize_embeddings=True,
    )

    return embedding.tolist()
