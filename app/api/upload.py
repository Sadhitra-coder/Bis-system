"""
POST /upload — accept a PDF and start asynchronous ingestion.

SECURITY (Phase 6 section 18)
-----------------------------
Implemented here: bounded upload size (streamed, so an oversized body is
never fully allocated), PDF magic-byte validation, and bounded ingestion
concurrency.

NOT implemented, and a known production blocker: there is NO AUTHENTICATION
on this or any other endpoint. Anyone able to reach the port can ingest
documents, consume disk, and spend OpenAI credits. Adding an auth system was
explicitly out of scope for this phase; the limits below reduce the blast
radius but do not close the hole.
"""

import hashlib
import logging
import threading
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile

from app.config import UPLOAD_DIR, settings
from app.jobs import create_job, update_job_status
from app.models import JobStatus
from app.steps.pipeline import process_pdf
from app.steps.registry import registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/upload", tags=["upload"])


#: Every PDF begins with this signature. Checked against the actual bytes
#: because an extension is caller-supplied text: `payload.exe` renamed to
#: `payload.pdf` passed the old check and was handed straight to Docling.
_PDF_MAGIC = b"%PDF-"

#: The signature is required within this prefix rather than strictly at
#: offset 0. Real-world PDFs sometimes carry a few leading bytes, and readers
#: tolerate it; scanning a bounded prefix accepts those without accepting a
#: file that merely contains "%PDF-" somewhere in its payload.
_PDF_MAGIC_SEARCH_WINDOW = 1024

#: Bytes per read while streaming the body.
_UPLOAD_CHUNK_SIZE = 1024 * 1024


#: Bounds concurrent background ingestions.
#:
#: Each ingestion loads a Docling converter and may call the structuring LLM,
#: so N simultaneous uploads meant N simultaneous converters. Acquired in the
#: request handler (non-blocking, so the caller learns immediately) and
#: released by the background task. Module level because BackgroundTasks run
#: in this process's threadpool; it is a per-process limit, not a cluster one.
_INGESTION_SLOTS = threading.BoundedSemaphore(settings.MAX_CONCURRENT_INGESTIONS)


def _safe_filename(original: str) -> str:
    """Return only the basename, rejecting path separators and null bytes."""
    name = Path(original).name  # strips any directory components
    name = name.replace("\x00", "")
    if not name or name in (".", ".."):
        raise ValueError("Invalid filename")
    return name


def _compute_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _read_bounded(file: UploadFile, limit: int) -> bytes:
    """
    Read the upload, refusing to buffer more than `limit` bytes.

    `await file.read()` with no argument allocates the whole body first and
    can only be checked afterwards, by which point the memory is already
    committed. Reading in chunks and stopping one byte past the limit means
    an oversized upload costs `limit + 1` bytes, not the sender's choice.
    """
    parts: list[bytes] = []
    total = 0

    while True:
        chunk = await file.read(_UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"File exceeds the maximum upload size of "
                    f"{settings.MAX_UPLOAD_SIZE_MB} MB."
                ),
            )
        parts.append(chunk)

    return b"".join(parts)


def _validate_pdf_bytes(data: bytes) -> None:
    """Reject anything whose bytes are not a PDF, whatever it is named."""
    offset = data[:_PDF_MAGIC_SEARCH_WINDOW].find(_PDF_MAGIC)
    if offset < 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "File content is not a PDF. The PDF signature '%PDF-' was not "
                "found; a .pdf extension alone is not sufficient."
            ),
        )
    if offset > 0:
        logger.warning(
            "PDF signature found at offset %d rather than 0; accepting.", offset
        )


def _rebuild_rag_pipeline(app_state) -> None:
    """Rebuild the RAG pipeline after ingestion so BM25 reflects new chunks."""
    try:
        from app.rag.retriever import HybridRetriever
        from app.rag.pipeline import RAGPipeline

        retriever = HybridRetriever(
            embedder=app_state.embedder,
            collection=app_state.collection,
        )
        app_state.rag_pipeline = RAGPipeline(
            retriever=retriever,
            reranker=getattr(app_state, "reranker", None),
            generator=getattr(app_state, "generator", None),
        )
        logger.info("RAGPipeline rebuilt after ingestion.")
    except Exception as exc:
        logger.warning("Could not rebuild RAGPipeline after ingestion: %s", exc)
        app_state.rag_pipeline = None


def _ingest_background(
    job_id: str,
    content_hash: str,
    safe_path: Path,
    original_filename: str,
    app_state,
    release_slot: bool = False,
) -> None:
    """
    Run ingestion in a background thread. Updates job status on completion.

    `release_slot` returns the concurrency permit acquired by the request
    handler. It is released in a finally block so a crashed ingestion cannot
    permanently consume a slot and starve every later upload.
    """
    update_job_status(job_id, JobStatus.PROCESSING)
    try:
        embedder = getattr(app_state, "embedder", None)
        collection = getattr(app_state, "collection", None)

        result = process_pdf(safe_path, embedding_model=embedder, collection=collection)
        chunks_indexed = result.get("chunks_indexed", 0)
        document_id = result.get("document_id", safe_path.stem)

        # Persist to idempotency registry
        registry.register(
            content_hash=content_hash,
            document_id=document_id,
            filename=original_filename,
            chunks_indexed=chunks_indexed,
            outputs={"safe_path": str(safe_path)},
        )

        update_job_status(job_id, JobStatus.COMPLETED, chunks_indexed=chunks_indexed)
        logger.info("Ingestion complete for %s — %d chunks.", original_filename, chunks_indexed)

        # Rebuild so BM25 picks up new chunks
        _rebuild_rag_pipeline(app_state)

    except Exception as exc:
        logger.exception("Background ingestion failed for %s: %s", original_filename, exc)
        update_job_status(job_id, JobStatus.FAILED, error=str(exc))

    finally:
        if release_slot:
            try:
                _INGESTION_SLOTS.release()
            except ValueError:
                # BoundedSemaphore guards against an over-release, which would
                # mean a double-release bug rather than a capacity change.
                logger.error("Ingestion slot released more times than acquired.")


@router.post("")
async def upload_document(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """
    Upload a PDF document and start asynchronous ingestion.

    Returns a job_id that can be polled via GET /jobs/{job_id}.
    """
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    # -- Security: sanitize filename --
    try:
        safe_name = _safe_filename(file.filename)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename.")

    # -- Cheap pre-check. Advisory only: the header is caller-supplied and
    #    may be absent under chunked encoding, so _read_bounded remains the
    #    actual guarantee. --
    max_bytes = settings.max_upload_size_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File exceeds the maximum upload size of "
                f"{settings.MAX_UPLOAD_SIZE_MB} MB."
            ),
        )

    # -- Read file bytes (needed for hash and idempotency check) --
    try:
        data = await _read_bounded(file, max_bytes)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read uploaded file: {exc}")

    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # -- Security: the bytes must actually be a PDF --
    _validate_pdf_bytes(data)

    content_hash = _compute_hash(data)

    # -- Idempotency: skip if already ingested --
    existing = registry.get_entry(content_hash)
    if existing:
        return {
            "status": "already_ingested",
            "document_id": existing.get("document_id"),
            "filename": existing.get("filename"),
            "chunks_indexed": existing.get("chunks_indexed", 0),
            "content_hash": content_hash,
        }

    # -- Save to disk with content-hash prefix to prevent collisions deterministically --
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    server_filename = f"{content_hash[:16]}_{safe_name}"
    safe_path = UPLOAD_DIR / server_filename

    try:
        safe_path.write_bytes(data)
    except Exception as exc:
        logger.exception("Failed to save file to disk: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to save file: {exc}")

    # -- Security: bounded ingestion concurrency --
    #
    # Acquired last, after every other rejection path, so a request that was
    # going to fail anyway never holds a permit. Non-blocking: a caller is
    # told to retry rather than left waiting on a connection for the duration
    # of somebody else's document conversion.
    if not _INGESTION_SLOTS.acquire(blocking=False):
        logger.warning(
            "Rejected upload of %s: %d concurrent ingestions already running.",
            safe_name,
            settings.MAX_CONCURRENT_INGESTIONS,
        )
        raise HTTPException(
            status_code=429,
            detail=(
                f"Server is already processing {settings.MAX_CONCURRENT_INGESTIONS} "
                f"documents. Retry shortly."
            ),
        )

    try:
        # -- Create job with deterministic document_id based on content_hash --
        document_id = f"doc_{content_hash[:16]}"
        job = create_job(document_id=document_id, filename=safe_name)

        background_tasks.add_task(
            _ingest_background,
            job_id=job.job_id,
            content_hash=content_hash,
            safe_path=safe_path,
            original_filename=safe_name,
            app_state=request.app.state,
            release_slot=True,
        )
    except Exception:
        # The permit is only handed to the background task once add_task has
        # accepted it. Failing in between would leak a slot permanently.
        _INGESTION_SLOTS.release()
        raise

    logger.info("Queued ingestion job %s for %s", job.job_id, safe_name)

    return {
        "status": "queued",
        "job_id": job.job_id,
        "document_id": document_id,
        "filename": safe_name,
        "content_hash": content_hash,
    }
