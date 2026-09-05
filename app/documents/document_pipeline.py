from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.config import (
    MARKDOWN_DATA_DIR,
    CLEANED_DATA_DIR,
    STRUCTURED_DATA_DIR,
    NORMALIZED_DATA_DIR,
    CHUNKS_DATA_DIR,
)
from app.database.models import Document
from app.database.session import SessionLocal
from app.steps.extract import extract_pdf
from app.steps.clean import clean_markdown_file
from app.steps.structure import structure_markdown_file
from app.steps.normalize import normalize_markdown_file
from app.steps.chunk import chunk_markdown_file


logger = logging.getLogger(__name__)


@dataclass
class ProcessingResult:
    """Result of single document processing."""

    document_id: int
    pdf_path: Path
    extracted_path: Optional[Path] = None
    cleaned_path: Optional[Path] = None
    structured_path: Optional[Path] = None
    normalized_path: Optional[Path] = None
    chunks_path: Optional[Path] = None
    chunk_count: int = 0
    quality_score: float = 0.0
    success: bool = False
    error: Optional[str] = None


def get_document_processed_paths(document_id: int) -> dict[str, Path]:
    """Return deterministic paths for each processing stage."""
    doc_id_str = str(document_id)

    return {
        "extracted": MARKDOWN_DATA_DIR / doc_id_str / "document.md",
        "cleaned": CLEANED_DATA_DIR / doc_id_str / "document_cleaned.md",
        "structured": STRUCTURED_DATA_DIR / doc_id_str / "document_structured.md",
        "normalized": NORMALIZED_DATA_DIR / doc_id_str / "document_normalized.md",
        "chunks": CHUNKS_DATA_DIR / doc_id_str / "document_chunks.json",
    }


def is_document_processed(document_id: int) -> bool:
    """Check if Level-2 processed artifacts already exist."""
    paths = get_document_processed_paths(document_id)
    chunks_path = paths["chunks"]
    return chunks_path.exists() and chunks_path.stat().st_size > 0


def calculate_quality_score(markdown_text: str, chunk_count: int) -> float:
    """
    Calculate a deterministic, real quality score (0.0 to 1.0)
    based on character volume, non-empty content, and chunk granularity.
    """
    if not markdown_text or not markdown_text.strip():
        return 0.0

    char_len = len(markdown_text.strip())
    if char_len < 50 or chunk_count == 0:
        return 0.1

    # Base score for valid extraction
    score = 0.50

    # Score component for content length (up to +0.25)
    score += min(0.25, (char_len / 5000.0) * 0.25)

    # Score component for structured chunking (up to +0.25)
    score += min(0.25, (chunk_count / 10.0) * 0.25)

    return round(min(1.0, score), 3)


def process_document(
    document_id: int,
    pdf_path: Path,
    session: Optional[Session] = None,
    force: bool = False,
) -> ProcessingResult:
    """
    Process a single BIS document through the full pipeline:
    extract -> clean -> structure -> normalize -> chunk.

    LEVEL 2 CACHE:
    If valid chunks already exist and not force, reuses them.

    Updates Document.parse_status, parse_quality_score, and last_parsed_at in PostgreSQL.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        return ProcessingResult(
            document_id=document_id,
            pdf_path=pdf_path,
            success=False,
            error=f"PDF file does not exist or is empty: {pdf_path}",
        )

    paths = get_document_processed_paths(document_id)
    extracted_path = paths["extracted"]
    cleaned_path = paths["cleaned"]
    structured_path = paths["structured"]
    normalized_path = paths["normalized"]
    chunks_path = paths["chunks"]

    db = session or SessionLocal()
    close_db = session is None

    try:
        # -------------------------------------------------------------
        # LEVEL 2 CACHE CHECK
        # -------------------------------------------------------------
        if not force and is_document_processed(document_id):
            try:
                with open(chunks_path, "r", encoding="utf-8") as f:
                    chunk_data = json.load(f)
                chunks = chunk_data.get("chunks", [])
                chunk_count = len(chunks)

                raw_md = (
                    extracted_path.read_text(encoding="utf-8")
                    if extracted_path.exists()
                    else ""
                )
                quality_score = calculate_quality_score(raw_md, chunk_count)

                logger.info(
                    "Level-2 cache hit for document %s: %s chunks",
                    document_id,
                    chunk_count,
                )

                # Ensure DB record reflects PARSED
                doc = db.query(Document).filter(Document.id == document_id).first()
                if doc:
                    doc.parse_status = "PARSED"
                    doc.parse_quality_score = quality_score
                    doc.last_parsed_at = datetime.utcnow()
                    db.commit()

                return ProcessingResult(
                    document_id=document_id,
                    pdf_path=pdf_path,
                    extracted_path=extracted_path,
                    cleaned_path=cleaned_path,
                    structured_path=structured_path,
                    normalized_path=normalized_path,
                    chunks_path=chunks_path,
                    chunk_count=chunk_count,
                    quality_score=quality_score,
                    success=True,
                )
            except Exception as cache_err:
                logger.warning(
                    "Cached chunks corrupted for %s, re-processing: %s",
                    document_id,
                    cache_err,
                )

        # -------------------------------------------------------------
        # MARK STATUS: PROCESSING
        # -------------------------------------------------------------
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc:
            doc.parse_status = "PROCESSING"
            db.commit()

        # -------------------------------------------------------------
        # STEP 1: EXTRACT (Docling)
        # -------------------------------------------------------------
        logger.info("Extracting document %s from %s", document_id, pdf_path)
        extracted_path = extract_pdf(pdf_path, extracted_path)

        # -------------------------------------------------------------
        # STEP 2: CLEAN (Lossless formatting)
        # -------------------------------------------------------------
        logger.info("Cleaning document %s", document_id)
        clean_markdown_file(extracted_path, cleaned_path)

        # -------------------------------------------------------------
        # STEP 3: STRUCTURE
        # -------------------------------------------------------------
        logger.info("Structuring document %s", document_id)
        structure_markdown_file(
            cleaned_path,
            structured_path,
            document_id=str(document_id),
        )

        # -------------------------------------------------------------
        # STEP 4: NORMALIZE
        # -------------------------------------------------------------
        logger.info("Normalizing document %s", document_id)
        normalize_markdown_file(structured_path, normalized_path)

        # -------------------------------------------------------------
        # STEP 5: CHUNK
        # -------------------------------------------------------------
        logger.info("Chunking document %s", document_id)
        chunk_markdown_file(
            normalized_path,
            chunks_path,
            document_id=str(document_id),
            source_file=str(pdf_path.name),
        )

        # Read generated chunks
        with open(chunks_path, "r", encoding="utf-8") as f:
            chunk_data = json.load(f)
        chunks = chunk_data.get("chunks", [])
        chunk_count = len(chunks)

        raw_md = (
            extracted_path.read_text(encoding="utf-8")
            if extracted_path.exists()
            else ""
        )
        quality_score = calculate_quality_score(raw_md, chunk_count)

        # -------------------------------------------------------------
        # UPDATE DATABASE
        # -------------------------------------------------------------
        if doc:
            doc.parse_status = "PARSED"
            doc.parse_quality_score = quality_score
            doc.last_parsed_at = datetime.utcnow()
            db.commit()

        logger.info(
            "Document %s processed successfully: %s chunks, quality=%.2f",
            document_id,
            chunk_count,
            quality_score,
        )

        return ProcessingResult(
            document_id=document_id,
            pdf_path=pdf_path,
            extracted_path=extracted_path,
            cleaned_path=cleaned_path,
            structured_path=structured_path,
            normalized_path=normalized_path,
            chunks_path=chunks_path,
            chunk_count=chunk_count,
            quality_score=quality_score,
            success=True,
        )

    except Exception as exc:
        logger.exception("Document %s processing failed: %s", document_id, exc)
        if doc:
            try:
                doc.parse_status = "FAILED"
                doc.parse_quality_score = 0.0
                db.commit()
            except Exception:
                db.rollback()

        return ProcessingResult(
            document_id=document_id,
            pdf_path=pdf_path,
            success=False,
            error=str(exc),
        )

    finally:
        if close_db:
            db.close()
