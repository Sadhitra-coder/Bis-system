"""
Phase 6 sections 17 and 18 — API endpoint integration tests.

WHAT THIS FILE EXISTS TO CATCH
------------------------------
Before Phase 6 the only /upload test mocked `_ingest_background` away, so the
suite never once executed the ingestion path that production runs, and /query
accepted `top_k` and `document_ids` and then discarded both while returning a
200 that looked correct. Two classes of defect were therefore invisible:
crashes inside the background task, and request fields that are silently inert.

Section C runs the REAL ingestion path end to end — real Docling extraction,
real chunking, real metadata serialization, real index-write validation, real
embedding with BGE-large, real writes to a real persisted Chroma directory.
Two things are substituted, both stated plainly rather than hidden:

  * LLM structuring degrades to the deterministic classifier, because no
    OPENAI_API_KEY is present in the test environment. This is the project's
    own documented fallback (STRUCTURE_ALLOW_FALLBACK), not a test-only path.
  * Answer generation uses a strict stub of the OpenAI client, for the same
    reason. Retrieval, reranking, and source formatting are real.
"""

import hashlib
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.jobs import create_job, get_job
from app.models import JobStatus
from app.steps.registry import IngestionRegistry
from tests.pdf_fixture import build_pdf, write_bis_standard_pdf


# A byte string that begins with the PDF signature, enough for every test that
# only needs to get past validation.
MINIMAL_PDF_BYTES = b"%PDF-1.4 minimal body for validation tests"


def _make_app(with_query: bool = False) -> FastAPI:
    """A bare app carrying the routers under test and empty state."""
    from app.api import jobs as jobs_mod
    from app.api import query as query_mod
    from app.api import upload as upload_mod

    app = FastAPI()
    app.include_router(upload_mod.router)
    app.include_router(jobs_mod.router)
    if with_query:
        app.include_router(query_mod.router)

    app.state.embedder = None
    app.state.collection = None
    app.state.reranker = None
    app.state.generator = None
    app.state.rag_pipeline = None
    return app


# ===========================================================================
# SECTION A — section 18: upload size, PDF magic bytes, ingestion concurrency
# ===========================================================================

def test_upload_rejects_bytes_that_are_not_a_pdf(tmp_path):
    """
    A .pdf extension is caller-supplied text.

    THE HOLE THIS CLOSES: the old check was
    `file.filename.endswith(".pdf")`. Renaming payload.exe to payload.pdf
    passed it, and the bytes went straight to Docling.
    """
    app = _make_app()
    with patch("app.api.upload.UPLOAD_DIR", tmp_path), \
         patch("app.api.upload.registry", IngestionRegistry(registry_file=tmp_path / "r.json")), \
         patch("app.api.upload._ingest_background"):
        client = TestClient(app)
        r = client.post(
            "/upload",
            files={"file": ("payload.pdf", b"MZ\x90\x00" + b"\x00" * 200, "application/pdf")},
        )
    assert r.status_code == 400
    assert "not a PDF" in r.json()["detail"]


def test_upload_accepts_a_pdf_whose_signature_is_slightly_offset(tmp_path):
    """
    Real PDFs occasionally carry a few leading bytes before '%PDF-'.

    Readers tolerate it, so rejecting it would be a functional regression
    dressed up as a security control.
    """
    app = _make_app()
    data = b"\n\n" + build_pdf((("Offset signature fixture.",),))
    with patch("app.api.upload.UPLOAD_DIR", tmp_path), \
         patch("app.api.upload.registry", IngestionRegistry(registry_file=tmp_path / "r.json")), \
         patch("app.api.upload._ingest_background"):
        client = TestClient(app)
        r = client.post("/upload", files={"file": ("offset.pdf", data, "application/pdf")})
    assert r.status_code == 200
    assert r.json()["status"] == "queued"


def test_upload_rejects_a_file_over_the_size_limit(tmp_path, monkeypatch):
    """Oversized uploads must be refused with 413, not ingested."""
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_MB", 1)
    oversize = MINIMAL_PDF_BYTES + b"\x00" * (2 * 1024 * 1024)

    app = _make_app()
    with patch("app.api.upload.UPLOAD_DIR", tmp_path), \
         patch("app.api.upload.registry", IngestionRegistry(registry_file=tmp_path / "r.json")), \
         patch("app.api.upload._ingest_background") as ingest:
        client = TestClient(app)
        r = client.post("/upload", files={"file": ("big.pdf", oversize, "application/pdf")})

    assert r.status_code == 413
    assert "maximum upload size" in r.json()["detail"]
    ingest.assert_not_called()


def test_read_bounded_stops_before_buffering_the_whole_body():
    """
    The limit must be enforced DURING the read, not after it.

    `await file.read()` allocates the entire body first, so a post-hoc size
    check rejects an upload whose memory cost has already been paid. This
    asserts the stream is abandoned early: the stub counts how much was
    actually pulled.

    Driven with asyncio.run rather than an async test, so the assertion does
    not depend on an async pytest plugin being configured.
    """
    import asyncio

    from fastapi import HTTPException

    from app.api.upload import _read_bounded

    class CountingFile:
        """Yields an effectively endless body, recording bytes served."""

        def __init__(self):
            self.served = 0

        async def read(self, size: int) -> bytes:
            self.served += size
            return b"\x00" * size

    stub = CountingFile()
    limit = 4 * 1024 * 1024
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_read_bounded(stub, limit))

    assert exc.value.status_code == 413
    # One chunk past the limit is the worst case; anything more means the
    # loop kept reading after it already knew the upload was too large.
    assert stub.served <= limit + (1024 * 1024)


def test_upload_refuses_when_ingestion_concurrency_is_saturated(tmp_path, monkeypatch):
    """
    Each ingestion loads a Docling converter, so unbounded concurrency turns
    a burst of uploads into memory exhaustion. Saturation must produce an
    explicit 429 rather than another converter.
    """
    from app.api import upload as upload_mod

    slots = threading.BoundedSemaphore(1)
    assert slots.acquire(blocking=False)  # occupy the only permit
    monkeypatch.setattr(upload_mod, "_INGESTION_SLOTS", slots)
    monkeypatch.setattr(settings, "MAX_CONCURRENT_INGESTIONS", 1)

    app = _make_app()
    with patch("app.api.upload.UPLOAD_DIR", tmp_path), \
         patch("app.api.upload.registry", IngestionRegistry(registry_file=tmp_path / "r.json")), \
         patch("app.api.upload._ingest_background") as ingest:
        client = TestClient(app)
        r = client.post("/upload", files={"file": ("a.pdf", MINIMAL_PDF_BYTES, "application/pdf")})

    assert r.status_code == 429
    assert "already processing" in r.json()["detail"]
    ingest.assert_not_called()


def test_ingestion_slot_is_released_even_when_ingestion_fails(monkeypatch):
    """
    A crashed ingestion must not hold its permit forever.

    Without the finally block, one exception during conversion permanently
    reduced capacity, and enough of them would reject every future upload
    with 429 while nothing was actually running.
    """
    from app.api import upload as upload_mod

    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(upload_mod, "_INGESTION_SLOTS", slots)
    assert slots.acquire(blocking=False)

    app = _make_app()
    job = create_job("doc_slot_release", "boom.pdf")
    with patch("app.api.upload.process_pdf", side_effect=RuntimeError("boom")):
        upload_mod._ingest_background(
            job_id=job.job_id,
            content_hash="deadbeef",
            safe_path=Path("nonexistent.pdf"),
            original_filename="boom.pdf",
            app_state=app.state,
            release_slot=True,
        )

    assert get_job(job.job_id).status == JobStatus.FAILED
    assert slots.acquire(blocking=False), "permit was not returned after failure"


# ===========================================================================
# SECTION B — section 17: /query request fields must not be silently ignored
# ===========================================================================

def _standard_result(chunk_id: str = "c1", relation: str = "identity") -> dict:
    """
    A retrieval result shaped the way the real retriever emits them.

    `standard_relation` is a required canonical index field, so a real chunk
    always carries it; omitting it here would let the test pass against a
    formatter that ignored the identity/citation distinction.
    """
    return {
        "chunk_id": chunk_id,
        "document_id": "doc_abc",
        "content": "The maximum permissible error shall be plus or minus 0.1 degrees.",
        "metadata": {
            "standard_number": "IS 3055",
            "standard_title": "Specification for Clinical Thermometers",
            "standard_year": 2024,
            "standard_id": "std_IS_3055_abc",
            "version_id": "ver_std_IS_3055_abc_def",
            "standard_relation": relation,
            "clause_id": "4.1",
            "clause_title": "Calibration and Accuracy",
            "document_type": "indian_standard",
            "page_start": 3,
            "page_end": 3,
            "source_file": "standards/IS_3055_2024.pdf",
        },
    }


class RecordingPipeline:
    """
    Stands in for RAGPipeline, recording the arguments it was called with.

    The sources it returns are produced by the REAL source_format.build_sources,
    so an assertion about standard identity in the response is an assertion
    about the production formatter rather than about this stub.
    """

    def __init__(self):
        self.calls = []

    def query(self, query, retrieval_top_k=None, rerank_top_k=None):
        from app.rag.source_format import build_sources

        self.calls.append(
            {
                "query": query,
                "retrieval_top_k": retrieval_top_k,
                "rerank_top_k": rerank_top_k,
            }
        )
        n = rerank_top_k or settings.RERANK_TOP_K
        results = [_standard_result(f"c{i}") for i in range(n)]
        return {
            "query": query,
            "answer": "The permissible error is +/- 0.1 degrees Celsius.",
            "sources": build_sources(results),
            "retrieved_chunks": len(results),
            "reranked_chunks": len(results),
            "model": settings.OPENAI_MODEL,
        }


def _query_client(pipeline=None) -> TestClient:
    app = _make_app(with_query=True)
    app.state.rag_pipeline = pipeline or RecordingPipeline()
    return TestClient(app)


def test_query_returns_a_populated_response():
    """
    A successful query must carry its own echoed query text and sources.

    `query` is a required field on QueryResponse and was omitted, so every
    otherwise-successful query returned HTTP 500 from a validation error
    raised while serialising the response.
    """
    client = _query_client()
    r = client.post("/query", json={"query": "What is the permissible error in IS 3055?"})

    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "What is the permissible error in IS 3055?"
    assert body["answer"]
    assert body["sources"], "sources must not be empty for a matched query"
    assert body["retrieved_chunks"] >= 1


def test_query_sources_expose_standard_identity():
    """
    Source objects must name the standard using the canonical fields.

    generator.py and pipeline.py previously read metadata['standard'] and
    metadata['title'], neither of which the serializer writes, so every
    source rendered as an unknown document.
    """
    client = _query_client()
    r = client.post("/query", json={"query": "IS 3055 clause 4.1"})
    source = r.json()["sources"][0]

    assert source["standard_number"] == "IS 3055"
    assert source["standard_title"] == "Specification for Clinical Thermometers"
    assert source["clause_id"] == "4.1"
    assert source["standard_id"] == "std_IS_3055_abc"
    assert source["version_id"] == "ver_std_IS_3055_abc_def"
    # A document that IS the standard, not one that merely cites it. Always
    # present, so a consumer never has to infer the distinction.
    assert source["standard_relation"] == "identity"
    assert "IS 3055" in source["citation"]


def test_query_sources_distinguish_a_citing_document_from_the_standard():
    """
    A manual quoting IS 3055 must not be presented as being IS 3055.

    This is the section 16 guarantee at the presentation layer: the API is
    the last place the identity/citation distinction can be lost, and losing
    it here would attribute a manual's wording to the standard itself.
    """
    class CitingPipeline(RecordingPipeline):
        def query(self, query, retrieval_top_k=None, rerank_top_k=None):
            from app.rag.source_format import build_sources

            self.calls.append({"query": query})
            return {
                "query": query,
                "answer": "See the referenced standard.",
                "sources": build_sources([_standard_result("c1", relation="reference")]),
                "retrieved_chunks": 1,
                "reranked_chunks": 1,
                "model": settings.OPENAI_MODEL,
            }

    client = _query_client(CitingPipeline())
    source = client.post("/query", json={"query": "IS 3055"}).json()["sources"][0]

    assert source["standard_relation"] == "reference"
    assert source["standard_number"] == "IS 3055"
    # The citation must lead with the citing document and state the
    # relationship, not present the passage under the standard's title.
    citation = source["citation"]
    assert citation.startswith("standards/IS_3055_2024.pdf"), citation
    assert "references IS 3055" in citation, citation
    assert not citation.startswith("Specification for"), citation


def test_query_honours_top_k():
    """
    top_k was parsed and dropped. A caller asking for 3 got the configured
    default and had no way to tell.
    """
    pipeline = RecordingPipeline()
    client = _query_client(pipeline)
    r = client.post("/query", json={"query": "calibration", "top_k": 3})

    assert r.status_code == 200
    assert pipeline.calls[0]["rerank_top_k"] == 3
    assert len(r.json()["sources"]) <= 3


def test_query_top_k_does_not_exceed_the_candidate_pool():
    """
    Asking for more final chunks than the retrieval stage fetches would cap
    the result silently. retrieval_top_k must rise with top_k.
    """
    pipeline = RecordingPipeline()
    client = _query_client(pipeline)
    client.post("/query", json={"query": "calibration", "top_k": 25})

    call = pipeline.calls[0]
    assert call["rerank_top_k"] == 25
    assert call["retrieval_top_k"] >= 25


@pytest.mark.parametrize("bad", [0, -1, 10_000])
def test_query_rejects_an_out_of_range_top_k(bad):
    """An unusable top_k is a client error, not something to quietly coerce."""
    pipeline = RecordingPipeline()
    client = _query_client(pipeline)
    r = client.post("/query", json={"query": "calibration", "top_k": bad})

    assert r.status_code == 400
    assert pipeline.calls == [], "the pipeline must not run for a rejected request"


def test_query_rejects_document_ids_explicitly():
    """
    document_ids is not implemented and must say so.

    Section 17 allows either implementing a request field or rejecting it,
    but not ignoring it. Scoping needs BOTH retrieval arms constrained; BM25
    is built once over the whole corpus and takes no filter, so a best-effort
    implementation would return a 200 with silently degraded recall — worse
    than a refusal, because nothing in the response reveals it.
    """
    pipeline = RecordingPipeline()
    client = _query_client(pipeline)
    r = client.post(
        "/query",
        json={"query": "calibration", "document_ids": ["doc_abc"]},
    )

    assert r.status_code == 400
    assert "document_ids" in r.json()["detail"]
    assert pipeline.calls == []


def test_query_ignores_an_empty_document_ids_list():
    """`document_ids: []` scopes nothing, so it is not a scoping request."""
    client = _query_client()
    r = client.post("/query", json={"query": "calibration", "document_ids": []})
    assert r.status_code == 200


# ===========================================================================
# SECTION C — section 17: the REAL ingestion path, not a mock of it
# ===========================================================================

@pytest.fixture(scope="module")
def real_ingestion(tmp_path_factory):
    """
    Upload a PDF through /upload and let the actual background task run.

    `_ingest_background` is deliberately NOT patched. Everything it calls
    runs for real against directories and a Chroma store under tmp_path, so
    the live index is untouched. Module-scoped because Docling and BGE-large
    each cost tens of seconds to load and the result is read-only.
    """
    import chromadb
    from sentence_transformers import SentenceTransformer

    from app.api import upload as upload_mod
    from app.steps import pipeline as pipeline_mod

    root = tmp_path_factory.mktemp("real_ingest")
    raw = root / "raw"
    chroma_path = root / "vector_db"
    pdf_bytes = write_bis_standard_pdf(root / "fixture" / "IS_3055_2024.pdf")

    client_chroma = chromadb.PersistentClient(path=str(chroma_path))
    collection = client_chroma.get_or_create_collection(name="test_real_ingest")
    embedder = SentenceTransformer(settings.EMBEDDING_MODEL)

    app = _make_app()
    app.state.embedder = embedder
    app.state.collection = collection

    test_registry = IngestionRegistry(registry_file=root / "registry.json")

    # Structuring calls OpenAI. No key is present in the test environment, so
    # the documented deterministic fallback is enabled explicitly rather
    # than letting ingestion fail on an unrelated missing credential.
    with patch.object(settings, "STRUCTURE_ALLOW_FALLBACK", True), \
         patch.object(settings, "LLM_ENABLED", False), \
         patch.object(upload_mod, "UPLOAD_DIR", raw), \
         patch.object(upload_mod, "registry", test_registry), \
         patch.object(pipeline_mod, "RAW_DIR", raw), \
         patch.object(pipeline_mod, "MARKDOWN_DIR", root / "markdown"), \
         patch.object(pipeline_mod, "CLEANED_DIR", root / "cleaned"), \
         patch.object(pipeline_mod, "STRUCTURED_DIR", root / "structured"), \
         patch.object(pipeline_mod, "NORMALIZED_DIR", root / "normalized"), \
         patch.object(pipeline_mod, "CHUNKS_DIR", root / "chunks"):

        http = TestClient(app)
        # TestClient runs BackgroundTasks before returning, so ingestion has
        # already finished by the time this call comes back.
        response = http.post(
            "/upload",
            files={"file": ("IS_3055_2024.pdf", pdf_bytes, "application/pdf")},
        )
        job_id = response.json().get("job_id")
        job = get_job(job_id) if job_id else None

    return {
        "response": response,
        "job": job,
        "collection": collection,
        "chroma_path": chroma_path,
        "embedder": embedder,
        "registry": test_registry,
        "content_hash": hashlib.sha256(pdf_bytes).hexdigest(),
    }


def test_real_upload_creates_a_job(real_ingestion):
    response = real_ingestion["response"]
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["job_id"]
    assert body["content_hash"] == real_ingestion["content_hash"]


def test_real_upload_job_reaches_completion(real_ingestion):
    """
    The background task must finish, not merely be scheduled.

    This is the assertion the old suite could not make: it patched
    `_ingest_background` out, so a crash inside it — such as the
    `JobStatus.RUNNING` typo that raised AttributeError before the try block
    and left the job QUEUED forever — was undetectable.
    """
    job = real_ingestion["job"]
    assert job is not None
    assert job.status == JobStatus.COMPLETED, f"job failed: {job.error}"
    assert job.chunks_indexed and job.chunks_indexed > 0


def test_real_upload_populates_the_index(real_ingestion):
    collection = real_ingestion["collection"]
    assert collection.count() > 0


def test_real_upload_writes_the_current_schema_version(real_ingestion):
    """Every chunk the current pipeline writes must carry the current schema."""
    from app.index_schema import CHUNK_INDEX_SCHEMA_VERSION

    stored = real_ingestion["collection"].get(include=["metadatas"])
    metadatas = stored["metadatas"]
    assert metadatas

    versions = {m.get("schema_version") for m in metadatas}
    assert versions == {CHUNK_INDEX_SCHEMA_VERSION}


def test_real_upload_produces_an_index_that_passes_integrity(real_ingestion):
    """
    The freshly written index must be judged healthy by the same check that
    guards startup readiness — not by this test's own opinion of it.
    """
    from app.index_integrity import check_index_integrity

    report = check_index_integrity(real_ingestion["collection"])
    assert report.is_healthy, f"{report.state}: {report.message}"


def test_real_upload_registers_the_document_for_idempotency(real_ingestion):
    entry = real_ingestion["registry"].get_entry(real_ingestion["content_hash"])
    assert entry is not None
    assert entry.get("chunks_indexed", 0) > 0


def test_real_ingestion_metadata_carries_provenance(real_ingestion):
    """
    Required provenance must survive the whole real path onto disk.

    A chunk with no document_id or no page range cannot be cited, and the
    audit found exactly that in the live index because those chunks predated
    the current serializer.
    """
    from app.index_schema import decode_optional_int

    stored = real_ingestion["collection"].get(include=["metadatas"])
    metadatas = stored["metadatas"]

    for meta in metadatas:
        assert meta.get("chunk_id")
        assert meta.get("document_id")
        assert meta.get("source_hash")
        assert meta.get("source_file")

    pages = [decode_optional_int(m.get("page_start")) for m in metadatas]
    assert any(p is not None for p in pages), "no chunk carries a known page"
