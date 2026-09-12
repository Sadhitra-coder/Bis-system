"""
tests/test_index_integrity.py

Phase 6 sections 8 and 14: schema-drift detection.

The regression these tests exist to prevent is the exact live-index state
the Phase 6 audit found — a populated collection whose metadata came from
two superseded contracts, passing `collection.count() > 0` while every
metadata-dependent feature was inert.

The stale stores here are built by WRITING REAL CHUNKS to a real persisted
Chroma directory with pre-Phase-6 metadata shapes, then reopening it. A
mock collection could not catch a drift that only manifests on disk.
"""

import chromadb
import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from app.index_integrity import (
    INDEX_CORRUPT,
    INDEX_EMPTY,
    INDEX_READY,
    INDEX_SCHEMA_MISMATCH,
    INDEX_UNAVAILABLE,
    IndexIntegrityReport,
    check_index_integrity,
)
from app.index_schema import (
    CHUNK_INDEX_SCHEMA_VERSION,
    build_chunk_index_metadata,
)

DIM = 8


def _persist(tmp_path, name="drift_test"):
    """A real on-disk Chroma collection. No embedding model needed: we
    supply vectors explicitly, because this is a metadata test."""
    client = chromadb.PersistentClient(path=str(tmp_path / "vdb"))
    return client, client.get_or_create_collection(name=name)


def _vec(seed: int):
    return [float((seed + i) % 7) / 7.0 for i in range(DIM)]


def _current_metadata(chunk_id="chunk_current", **overrides):
    """Metadata produced by the real Phase 6 writer."""
    chunk = {
        "chunk_id": chunk_id,
        "content": "Body text.",
        "metadata": {
            "chunk_id": chunk_id,
            "document_id": "doc_current",
            "source_file": "standards/IS_3055_2024.pdf",
            "source_hash": "hash_current",
            "section": "4.1 Requirements",
            "heading_context": ["Section 4"],
            "clause_id": "4.1",
            "page_start": 7,
            "page_end": 7,
            "standard_number": "IS 3055",
            "standard_title": "Code of practice",
            "standard_year": 2024,
            "edition_or_version": "Third Edition",
            "document_type": "indian_standard",
            "authority": "BIS",
        },
    }
    metadata = build_chunk_index_metadata(
        chunk,
        source_content="Body text.",
        contextualized_content="Body text.",
        context_generation_method="structural",
        context_generation_version="1.0",
    )
    metadata.update(overrides)
    return metadata


#: The 4-key shape seven live chunks were found in: pre-provenance.
STALE_4_KEY = {
    "chunk_id": "chunk_stale_4",
    "document_id": "Product-Manual-30551-V2_cleaned_structured",
    "section": "Introduction",
    "source_file": "Product-Manual.pdf",
}

#: The 28-key shape: post-provenance, pre-contextual-retrieval. Abbreviated
#: to the fields that matter here — what makes it stale is the ABSENCE of
#: schema_version, which is exactly how the real stale chunks present.
STALE_28_KEY = {
    "chunk_id": "chunk_stale_28",
    "document_id": "doc_90d46c947dc18a22",
    "source_file": "sample_test.pdf",
    "section": "Scope",
    "page_start": 1,
    "page_end": 1,
    "standard_number": "",
    "standard_year": 0,
    "clause_id": "",
    "parser_version": "2.0",
}


# ------------------------------------------------------------------
# The state machine
# ------------------------------------------------------------------

def test_empty_collection_is_empty_not_unhealthy(tmp_path):
    client, collection = _persist(tmp_path)
    try:
        report = check_index_integrity(collection)
        assert report.state == INDEX_EMPTY
        assert report.total_chunks == 0
        # An empty index is a fresh install, not a fault.
        assert report.is_healthy is True
        # But there is nothing to retrieve from.
        assert report.is_retrievable is False
    finally:
        del client


def test_current_schema_index_is_ready(tmp_path):
    client, collection = _persist(tmp_path)
    try:
        metadata = _current_metadata()
        collection.add(
            ids=[metadata["chunk_id"]],
            documents=["Body text."],
            embeddings=[_vec(1)],
            metadatas=[metadata],
        )

        report = check_index_integrity(collection)

        assert report.state == INDEX_READY, report.message
        assert report.is_healthy and report.is_retrievable
        assert report.schema_versions == {CHUNK_INDEX_SCHEMA_VERSION: 1}
        assert report.violations == []
        assert report.missing_required == {}
    finally:
        del client


def test_stale_schema_index_is_detected_after_reopen(tmp_path):
    """
    The core section 14 regression.

    Write pre-Phase-6 chunks, CLOSE the client, reopen the persisted store,
    and confirm the check reports a mismatch rather than accepting
    'non-empty therefore healthy'.
    """
    client, collection = _persist(tmp_path)
    collection.add(
        ids=["chunk_stale_4", "chunk_stale_28"],
        documents=["Old text A.", "Old text B."],
        embeddings=[_vec(1), _vec(2)],
        metadatas=[dict(STALE_4_KEY), dict(STALE_28_KEY)],
    )
    del collection
    del client

    # Reopen from disk — a fresh process would see exactly this.
    reopened_client = chromadb.PersistentClient(path=str(tmp_path / "vdb"))
    reopened = reopened_client.get_collection("drift_test")

    # The old readiness test would have passed here.
    assert reopened.count() == 2

    report = check_index_integrity(reopened)

    assert report.state == INDEX_SCHEMA_MISMATCH
    assert report.is_healthy is False
    assert report.total_chunks == 2
    assert report.schema_versions == {"<absent>": 2}
    assert CHUNK_INDEX_SCHEMA_VERSION in report.message
    assert "reindex" in report.remediation


def test_mixed_schema_index_is_unhealthy(tmp_path):
    """
    One current chunk alongside stale ones must not launder the index into
    looking healthy. This is the shape the live index was actually in.
    """
    client, collection = _persist(tmp_path)
    try:
        current = _current_metadata()
        collection.add(
            ids=[current["chunk_id"], "chunk_stale_4", "chunk_stale_28"],
            documents=["New text.", "Old A.", "Old B."],
            embeddings=[_vec(1), _vec(2), _vec(3)],
            metadatas=[current, dict(STALE_4_KEY), dict(STALE_28_KEY)],
        )

        report = check_index_integrity(collection)

        assert report.state == INDEX_SCHEMA_MISMATCH
        assert report.is_healthy is False
        assert report.schema_versions == {CHUNK_INDEX_SCHEMA_VERSION: 1, "<absent>": 2}
        # The histogram that revealed the problem in the live store.
        assert sorted(report.key_count_histogram.values()) == [1, 1, 1]
    finally:
        del client


def test_wrong_schema_version_value_is_a_mismatch(tmp_path):
    """A future or hand-edited version string is still a mismatch."""
    client, collection = _persist(tmp_path)
    try:
        metadata = _current_metadata(schema_version="5.9")
        collection.add(
            ids=[metadata["chunk_id"]],
            documents=["Body text."],
            embeddings=[_vec(1)],
            metadatas=[metadata],
        )
        report = check_index_integrity(collection)
        assert report.state == INDEX_SCHEMA_MISMATCH
        assert report.schema_versions == {"5.9": 1}
    finally:
        del client


def test_current_version_with_broken_contract_is_corrupt(tmp_path):
    """
    Claiming the current schema while violating it is a different fault
    from being stale: the writer produced bad rows. It must not be reported
    as merely out of date.
    """
    client, collection = _persist(tmp_path)
    try:
        metadata = _current_metadata()
        # Inverted page range — impossible provenance.
        metadata["page_start"] = 9
        metadata["page_end"] = 2
        collection.add(
            ids=[metadata["chunk_id"]],
            documents=["Body text."],
            embeddings=[_vec(1)],
            metadatas=[metadata],
        )

        report = check_index_integrity(collection)

        assert report.state == INDEX_CORRUPT
        assert report.is_healthy is False
        assert any("page" in v for v in report.violations)
    finally:
        del client


def test_identity_ids_without_relation_is_corrupt(tmp_path):
    """
    standard_id present while standard_relation says 'reference' means the
    index is asserting an identity the classifier refused. Flag it.
    """
    client, collection = _persist(tmp_path)
    try:
        metadata = _current_metadata()
        metadata["standard_relation"] = "reference"
        collection.add(
            ids=[metadata["chunk_id"]],
            documents=["Body text."],
            embeddings=[_vec(1)],
            metadatas=[metadata],
        )
        report = check_index_integrity(collection)
        assert report.state == INDEX_CORRUPT
        assert report.violations
    finally:
        del client


def test_missing_collection_is_unavailable():
    report = check_index_integrity(None)
    assert report.state == INDEX_UNAVAILABLE
    assert report.is_healthy is False
    assert report.is_retrievable is False


def test_unreadable_collection_is_unavailable_not_an_exception():
    """A damaged store must degrade the service, not prevent startup."""
    class Broken:
        def count(self):
            raise RuntimeError("sqlite file is not a database")

    report = check_index_integrity(Broken())
    assert report.state == INDEX_UNAVAILABLE
    assert "not a database" in report.message


def test_report_never_mutates_the_index(tmp_path):
    """
    The check must be strictly read-only. A stale index may be the only
    copy of an expensive ingestion.
    """
    client, collection = _persist(tmp_path)
    try:
        collection.add(
            ids=["chunk_stale_4"],
            documents=["Old text."],
            embeddings=[_vec(1)],
            metadatas=[dict(STALE_4_KEY)],
        )
        before = collection.get(include=["metadatas", "documents"])

        report = check_index_integrity(collection)
        assert report.state == INDEX_SCHEMA_MISMATCH

        after = collection.get(include=["metadatas", "documents"])
        assert after["ids"] == before["ids"]
        assert after["metadatas"] == before["metadatas"]
        assert after["documents"] == before["documents"]
        assert collection.count() == 1
    finally:
        del client


def test_to_dict_is_serializable_and_names_the_expected_version(tmp_path):
    client, collection = _persist(tmp_path)
    try:
        collection.add(
            ids=["chunk_stale_4"],
            documents=["Old."],
            embeddings=[_vec(1)],
            metadatas=[dict(STALE_4_KEY)],
        )
        payload = check_index_integrity(collection).to_dict()

        import json
        json.dumps(payload)  # must not raise

        assert payload["state"] == INDEX_SCHEMA_MISMATCH
        assert payload["healthy"] is False
        assert payload["expected_schema_version"] == CHUNK_INDEX_SCHEMA_VERSION
    finally:
        del client


# ------------------------------------------------------------------
# Readiness endpoint
# ------------------------------------------------------------------

def _readiness_app(report):
    """The /ready handler from app.main, wired to a chosen report."""
    from app.main import readiness_check

    app = FastAPI()
    app.state.index_integrity = report
    app.state.rag_pipeline = object() if report is not None else None
    app.add_api_route("/ready", readiness_check, methods=["GET"])
    return app


def test_ready_returns_503_on_schema_mismatch(tmp_path):
    client, collection = _persist(tmp_path)
    try:
        collection.add(
            ids=["chunk_stale_4"],
            documents=["Old."],
            embeddings=[_vec(1)],
            metadatas=[dict(STALE_4_KEY)],
        )
        report = check_index_integrity(collection)
    finally:
        del client

    with TestClient(_readiness_app(report)) as tc:
        response = tc.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["index"]["state"] == INDEX_SCHEMA_MISMATCH
    assert body["index"]["healthy"] is False


def test_ready_returns_200_when_index_is_current(tmp_path):
    client, collection = _persist(tmp_path)
    try:
        metadata = _current_metadata()
        collection.add(
            ids=[metadata["chunk_id"]],
            documents=["Body text."],
            embeddings=[_vec(1)],
            metadatas=[metadata],
        )
        report = check_index_integrity(collection)
    finally:
        del client

    with TestClient(_readiness_app(report)) as tc:
        response = tc.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["index"]["state"] == INDEX_READY


def test_ready_returns_503_before_the_check_has_run():
    app = FastAPI()
    app.state.index_integrity = None
    app.state.rag_pipeline = None
    from app.main import readiness_check
    app.add_api_route("/ready", readiness_check, methods=["GET"])

    with TestClient(app) as tc:
        response = tc.get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_health_stays_liveness_only():
    """
    /health must not go red on a stale index: that is a data problem, and
    failing liveness would make an orchestrator restart-loop the container.
    """
    from app.main import health_check
    assert health_check() == {"status": "ok"}
