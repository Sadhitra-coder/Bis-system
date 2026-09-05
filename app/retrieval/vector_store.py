from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.database.models import Document, DocumentChunk
from app.database.session import SessionLocal
from app.retrieval.embeddings import embed_texts


logger = logging.getLogger(__name__)


def calculate_chunk_hash(text: str, heading: str, chunk_index: int) -> str:
    """Generate deterministic hash for a chunk."""
    raw = f"{heading}::{chunk_index}::{text.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def is_document_embedded(document_id: int, session: Optional[Session] = None) -> bool:
    """Check if document has chunks embedded and stored in PostgreSQL."""
    db = session or SessionLocal()
    close_db = session is None
    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc or doc.embedding_status != "EMBEDDED":
            return False

        chunk_count = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == document_id)
            .count()
        )
        return chunk_count > 0
    finally:
        if close_db:
            db.close()


def store_document_chunks(
    document_id: int,
    chunks_path: Path,
    session: Optional[Session] = None,
    batch_size: int = 32,
) -> int:
    """
    Read chunks from chunks_path, embed missing chunks, and store in PostgreSQL.
    Updates Document.embedding_status.
    Returns count of stored chunks.
    """
    chunks_path = Path(chunks_path)
    if not chunks_path.exists() or chunks_path.stat().st_size == 0:
        raise FileNotFoundError(f"Chunks file not found or empty: {chunks_path}")

    with open(chunks_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    chunks = data.get("chunks", [])
    if not chunks:
        logger.warning("No chunks found in %s for document %s", chunks_path, document_id)
        return 0

    db = session or SessionLocal()
    close_db = session is None

    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc:
            doc.embedding_status = "PROCESSING"
            db.commit()

        # Query existing chunk_ids for this document to avoid duplicate insertions
        existing_chunks = (
            db.query(DocumentChunk.chunk_id)
            .filter(DocumentChunk.document_id == document_id)
            .all()
        )
        existing_chunk_ids = {c[0] for c in existing_chunks}

        chunks_to_insert = []
        texts_to_embed = []

        for idx, chunk in enumerate(chunks):
            chunk_id = chunk.get("chunk_id")
            if not chunk_id:
                heading = chunk.get("section", "Section")
                h = calculate_chunk_hash(chunk.get("content", ""), heading, idx)
                chunk_id = f"doc_{document_id}_chunk_{idx}_{h}"

            if chunk_id in existing_chunk_ids:
                continue

            text_content = chunk.get("content", "").strip()
            if not text_content:
                continue

            chunks_to_insert.append((chunk_id, chunk, text_content))
            texts_to_embed.append(text_content)

        if texts_to_embed:
            logger.info(
                "Embedding %s new chunks for document %s",
                len(texts_to_embed),
                document_id,
            )
            embeddings = embed_texts(texts_to_embed, batch_size=batch_size)

            for (chunk_id, raw_chunk, text_content), emb in zip(
                chunks_to_insert, embeddings
            ):
                heading_ctx = raw_chunk.get("heading_context", [])
                metadata_dict = {
                    "heading_context": heading_ctx,
                    "character_count": raw_chunk.get("character_count", len(text_content)),
                }

                chunk_obj = DocumentChunk(
                    document_id=document_id,
                    chunk_id=chunk_id,
                    text=text_content,
                    page_start=raw_chunk.get("page_start"),
                    page_end=raw_chunk.get("page_end"),
                    section=raw_chunk.get("section"),
                    heading=raw_chunk.get("heading") or (heading_ctx[-1] if heading_ctx else None),
                    source_file=raw_chunk.get("source_file"),
                    embedding=emb,
                    chunk_metadata=json.dumps(metadata_dict),
                )
                db.add(chunk_obj)

            db.commit()
            logger.info(
                "Stored %s chunks for document %s in PostgreSQL",
                len(chunks_to_insert),
                document_id,
            )

        if doc:
            doc.embedding_status = "EMBEDDED"
            db.commit()

        total_stored = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == document_id)
            .count()
        )
        return total_stored

    except Exception as exc:
        logger.exception("Embedding failed for document %s: %s", document_id, exc)
        db.rollback()
        if doc:
            try:
                doc.embedding_status = "FAILED"
                db.commit()
            except Exception:
                db.rollback()
        raise
    finally:
        if close_db:
            db.close()
