"""
Tests for Parts A, B, C, F, G:
  A - Path traversal / filename sanitization
  B - Async background job creation
  C - Retriever deduplication by chunk_id
  F - BM25 rebuild after ingestion (mock)
  G - Idempotency registry
"""

import hashlib
import io
import json
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Part A — filename sanitization
# ---------------------------------------------------------------------------

from app.api.upload import _safe_filename


def test_safe_filename_strips_directory():
    assert _safe_filename("../../etc/passwd.pdf") == "passwd.pdf"


def test_safe_filename_normal():
    assert _safe_filename("IS_3055_2024.pdf") == "IS_3055_2024.pdf"


def test_safe_filename_null_byte_stripped():
    result = _safe_filename("some\x00file.pdf")
    assert "\x00" not in result


def test_safe_filename_invalid_raises():
    with pytest.raises(ValueError):
        _safe_filename("../../")


# ---------------------------------------------------------------------------
# Part G — IngestionRegistry
# ---------------------------------------------------------------------------

from app.steps.registry import IngestionRegistry


def test_registry_register_and_get(tmp_path):
    reg = IngestionRegistry(registry_file=tmp_path / "reg.json")
    h = "abc123"
    reg.register(h, "doc_1", "test.pdf", 42)
    entry = reg.get_entry(h)
    assert entry is not None
    assert entry["document_id"] == "doc_1"
    assert entry["chunks_indexed"] == 42


def test_registry_persists_to_disk(tmp_path):
    path = tmp_path / "reg.json"
    reg1 = IngestionRegistry(registry_file=path)
    reg1.register("hash1", "doc_x", "a.pdf", 5)

    reg2 = IngestionRegistry(registry_file=path)
    assert reg2.get_entry("hash1") is not None


def test_registry_unknown_hash_returns_none(tmp_path):
    reg = IngestionRegistry(registry_file=tmp_path / "reg.json")
    assert reg.get_entry("nonexistent") is None


def test_registry_duplicate_hash_overwrites(tmp_path):
    reg = IngestionRegistry(registry_file=tmp_path / "reg.json")
    reg.register("h1", "doc_a", "a.pdf", 10)
    reg.register("h1", "doc_b", "b.pdf", 20)
    entry = reg.get_entry("h1")
    assert entry["document_id"] == "doc_b"
    assert entry["chunks_indexed"] == 20


# ---------------------------------------------------------------------------
# Part B — job manager
# ---------------------------------------------------------------------------

from app.jobs import create_job, get_job, list_jobs, update_job_status
from app.models import JobStatus


def test_create_and_get_job():
    job = create_job("doc_test", "test.pdf")
    assert job.job_id.startswith("job_")
    fetched = get_job(job.job_id)
    assert fetched is not None
    assert fetched.filename == "test.pdf"
    assert fetched.status == JobStatus.QUEUED


def test_update_job_status_completed():
    job = create_job("doc_update", "update.pdf")
    updated = update_job_status(job.job_id, JobStatus.COMPLETED, chunks_indexed=15)
    assert updated.status == JobStatus.COMPLETED
    assert updated.chunks_indexed == 15
    assert updated.completed_at is not None


def test_update_job_status_failed():
    job = create_job("doc_fail", "fail.pdf")
    updated = update_job_status(job.job_id, JobStatus.FAILED, error="Something broke")
    assert updated.status == JobStatus.FAILED
    assert updated.error == "Something broke"


def test_get_nonexistent_job():
    assert get_job("job_doesnotexist") is None


def test_list_jobs_newest_first():
    j1 = create_job("d1", "a.pdf")
    time.sleep(0.01)
    j2 = create_job("d2", "b.pdf")
    jobs = list_jobs(limit=10)
    ids = [j.job_id for j in jobs]
    # j2 created later, should appear before j1
    assert ids.index(j2.job_id) < ids.index(j1.job_id)


# ---------------------------------------------------------------------------
# Part C — retriever deduplication
# ---------------------------------------------------------------------------

from app.rag.retriever import HybridRetriever


# Use the method directly without instantiating the full retriever
# (which needs a live Chroma collection). Extract it as an unbound call.
def deduplicate(chunks):
    """Thin wrapper so tests call the real dedup logic without a live Chroma."""
    # Build a minimal stand-in that has the method
    obj = object.__new__(HybridRetriever)
    return HybridRetriever.deduplicate(obj, chunks)



def _make_chunk(chunk_id, section="sec1", source="doc.pdf"):
    return {
        "chunk_id": chunk_id,
        "section": section,
        "source_file": source,
        "content": f"content for {chunk_id}",
        "score": 0.9,
    }


def test_deduplicate_removes_exact_duplicate_chunk_ids():
    chunks = [_make_chunk("id1"), _make_chunk("id1"), _make_chunk("id2")]
    result = deduplicate(chunks)
    ids = [c["chunk_id"] for c in result]
    assert ids.count("id1") == 1
    assert "id2" in ids


def test_deduplicate_keeps_different_sections_same_doc():
    """Two chunks from the same document but different sections must both survive."""
    chunks = [
        _make_chunk("id1", section="sec1"),
        _make_chunk("id2", section="sec2"),
    ]
    result = deduplicate(chunks)
    assert len(result) == 2


def test_deduplicate_preserves_order():
    chunks = [_make_chunk(f"id{i}") for i in range(5)]
    result = deduplicate(chunks)
    assert [c["chunk_id"] for c in result] == [f"id{i}" for i in range(5)]


# ---------------------------------------------------------------------------
# Part A/B — FastAPI endpoint smoke tests (no real Chroma/model)
# ---------------------------------------------------------------------------

def _make_test_app():
    """Build a minimal app with upload + jobs routers, mocked state."""
    from fastapi import FastAPI
    from app.api import upload as upload_mod
    from app.api import jobs as jobs_mod

    app = FastAPI()
    app.include_router(upload_mod.router)
    app.include_router(jobs_mod.router)

    # Minimal fake state
    app.state.embedder = None
    app.state.collection = None
    app.state.reranker = None
    app.state.generator = None
    app.state.rag_pipeline = None
    return app


def test_upload_rejects_non_pdf():
    app = _make_test_app()
    client = TestClient(app, raise_server_exceptions=True)
    response = client.post(
        "/upload",
        files={"file": ("malicious.exe", b"MZ\x00\x00", "application/octet-stream")},
    )
    assert response.status_code == 400


def test_upload_rejects_empty_file():
    app = _make_test_app()
    client = TestClient(app, raise_server_exceptions=True)
    response = client.post(
        "/upload",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert response.status_code == 400


def test_upload_queues_job_and_idempotency(tmp_path):
    """Uploading same bytes twice returns 'already_ingested' on second call."""
    app = _make_test_app()

    pdf_bytes = b"%PDF-1.4 fake pdf content for testing"
    content_hash = hashlib.sha256(pdf_bytes).hexdigest()

    # Patch registry and process_pdf so we don't need a real PDF
    fake_registry_path = tmp_path / "reg.json"
    test_registry = IngestionRegistry(registry_file=fake_registry_path)

    with patch("app.api.upload.registry", test_registry), \
         patch("app.api.upload.UPLOAD_DIR", tmp_path), \
         patch("app.api.upload._ingest_background"):  # don't actually ingest

        client = TestClient(app, raise_server_exceptions=True)

        # First upload — should be queued
        r1 = client.post(
            "/upload",
            files={"file": ("IS_3055.pdf", pdf_bytes, "application/pdf")},
        )
        assert r1.status_code == 200
        data1 = r1.json()
        assert data1["status"] == "queued"
        job_id = data1["job_id"]

        # Simulate completed ingestion so registry has entry
        test_registry.register(content_hash, "doc_001", "IS_3055.pdf", 10)

        # Second upload of same bytes — should be idempotent
        r2 = client.post(
            "/upload",
            files={"file": ("IS_3055.pdf", pdf_bytes, "application/pdf")},
        )
        assert r2.status_code == 200
        data2 = r2.json()
        assert data2["status"] == "already_ingested"


def test_jobs_endpoint_returns_404_for_unknown():
    app = _make_test_app()
    client = TestClient(app, raise_server_exceptions=True)
    r = client.get("/jobs/job_nonexistent")
    assert r.status_code == 404


def test_jobs_endpoint_returns_job():
    app = _make_test_app()
    job = create_job("doc_endpoint_test", "endpoint.pdf")

    client = TestClient(app, raise_server_exceptions=True)
    r = client.get(f"/jobs/{job.job_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["job_id"] == job.job_id
    assert data["status"] == JobStatus.QUEUED


# ---------------------------------------------------------------------------
# Part C — API contract regressions
#
# These two paths were previously unreachable at runtime while the whole
# suite still passed, because the only /upload test mocked out
# _ingest_background and no test exercised /query at all.
# ---------------------------------------------------------------------------

def test_ingest_background_uses_a_defined_job_status():
    """
    _ingest_background must mark the job in-progress using a JobStatus
    constant that actually exists. A typo'd constant raises AttributeError
    before the try block, so ingestion dies and the job is never marked
    FAILED — it stays QUEUED forever.
    """
    from app.api.upload import _ingest_background

    app = _make_test_app()
    job = create_job("doc_bg_status", "bg.pdf")

    # process_pdf is patched to fail, so the job must end FAILED (not QUEUED).
    with patch("app.api.upload.process_pdf", side_effect=RuntimeError("boom")):
        _ingest_background(
            job_id=job.job_id,
            content_hash="deadbeef",
            safe_path=Path("nonexistent.pdf"),
            original_filename="bg.pdf",
            app_state=app.state,
        )

    assert get_job(job.job_id).status == JobStatus.FAILED


def test_query_endpoint_returns_valid_response_model():
    """
    /query must build a QueryResponse that satisfies its own response model.
    Omitting a required field turns every successful query into an HTTP 500.
    """
    from fastapi import FastAPI
    from app.api import query as query_mod

    app = FastAPI()
    app.include_router(query_mod.router)

    fake_pipeline = MagicMock()
    fake_pipeline.query.return_value = {
        "query": "IS 3055 clause 4.1",
        "answer": "The maximum permissible error is +/-0.1 C.",
        "sources": [{"document_id": "doc_1", "section": "4.1"}],
        "retrieved_chunks": 3,
        "reranked_chunks": 2,
        "model": "openai/gpt-oss-20b",
    }
    app.state.rag_pipeline = fake_pipeline

    client = TestClient(app, raise_server_exceptions=True)
    r = client.post("/query", json={"query": "IS 3055 clause 4.1"})

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["query"] == "IS 3055 clause 4.1"
    assert data["answer"].startswith("The maximum permissible error")
    assert data["retrieved_chunks"] == 3
