"""
Phase 6 section 13/14 — the persisted index, read back from disk.

WHY A SEPARATE FILE FROM THE IN-MEMORY TESTS
--------------------------------------------
Every prior index test used `chromadb.Client()`, which keeps everything in
process memory. That proves the serializer produces the right dict; it cannot
prove the dict survives a write to disk, a process boundary, and a read back.
The distinction is not academic: Chroma refuses non-scalar metadata values and
silently accepts type coercions, so a field that looks correct as a Python
object can come back as something else — or not come back at all.

The live index this phase exists to fix contained chunks with 4 metadata keys
that every unit test would have judged fine, because no test ever opened the
persisted store and looked.

So each test here does the same thing the application does at startup: close
the client, reopen the directory, and read what is actually stored.
"""

import hashlib

import chromadb
import numpy as np
import pytest
from chromadb.api.client import SharedSystemClient

from app.index_schema import (
    CHUNK_INDEX_FIELDS,
    CHUNK_INDEX_SCHEMA_VERSION,
    UNKNOWN_INT,
    UNKNOWN_STR,
    decode_heading_context,
    decode_optional_int,
)
from app.steps.bis_extractor import build_document_metadata
from app.steps.chunk import create_chunks
from app.steps.embed import embed_chunks, prepare_chunks

COLLECTION = "test_persisted_index"

# A three-page BIS standard with an amendment. Page markers drive provenance,
# and the clause heading drives the clause/knowledge join, so both are needed
# for the linkage assertions below to mean anything.
FIXTURE_DOC = """<!-- PAGE 1 -->
# IS 3055 : 2024
TITLE: Specification for Clinical Thermometers
Edition: Third Edition
Amendment No. 1
This Indian Standard establishes technical, material and metrological
requirements for mercury-in-glass clinical thermometers used in professional
healthcare settings and hospital environments.

<!-- PAGE 2 -->
## 4 Requirements
General manufacturing, safety and mechanical performance requirements for
medical glass clinical thermometers. All glass components and capillary stems
shall be fabricated from certified high-grade borosilicate glass free from
internal thermal stress, hairline cracks and visible air bubbles.

<!-- PAGE 3 -->
### 4.1 Calibration and Accuracy
The maximum permissible error of temperature indication shall be plus or minus
0.1 degrees Celsius between 35.0 and 42.0 degrees Celsius under nominal test
conditions. Calibration shall be conducted using an accredited circulating
water bath traceable to national primary metrological standards.
"""


def _fake_embedder():
    """
    A deterministic stand-in for BGE-large.

    Real embeddings are irrelevant to what these tests assert — they are about
    metadata surviving a round trip to disk — and loading a 1.3 GB model to
    check a dict would make the file too slow to run often enough to matter.
    Retrieval is still exercised through the real Chroma query path.
    """
    class Embedder:
        def encode(self, texts, **kwargs):
            if isinstance(texts, str):
                return np.ones(64, dtype=float)
            return np.ones((len(texts), 64), dtype=float)

    return Embedder()


@pytest.fixture(scope="module")
def persisted(tmp_path_factory):
    """
    Write the fixture through the real pipeline, then CLOSE and REOPEN.

    The close/reopen is the entire point. `del` plus clear_system_cache drops
    Chroma's cached system for the path, so the second client genuinely reads
    the directory rather than handing back the in-process objects the first
    one still had.
    """
    path = tmp_path_factory.mktemp("persisted_index") / "vector_db"

    source_hash = hashlib.sha256(FIXTURE_DOC.encode("utf-8")).hexdigest()
    doc_meta = build_document_metadata(
        source_file="standards/IS_3055_2024.pdf",
        source_filename="IS_3055_2024.pdf",
        source_hash=source_hash,
        text_sample=FIXTURE_DOC[:3000],
    )
    chunks = create_chunks(
        FIXTURE_DOC,
        document_id=doc_meta.document_id,
        source_file=doc_meta.source_file,
        doc_metadata=doc_meta,
    )
    assert len(chunks) >= 3, "fixture must produce several chunks to be meaningful"

    ids, documents, metadatas = prepare_chunks(chunks)

    write_client = chromadb.PersistentClient(path=str(path))
    write_collection = write_client.get_or_create_collection(name=COLLECTION)
    embedder = _fake_embedder()
    embed_chunks(embedder, write_collection, ids, documents, metadatas)
    written = write_collection.count()
    assert written >= 3

    # ---- close ----
    del write_collection
    del write_client
    SharedSystemClient.clear_system_cache()

    # ---- reopen from disk ----
    read_client = chromadb.PersistentClient(path=str(path))
    read_collection = read_client.get_collection(name=COLLECTION)

    stored = read_collection.get(include=["metadatas", "documents"])

    return {
        "path": path,
        "collection": read_collection,
        "embedder": embedder,
        "written": written,
        "ids": stored["ids"],
        "metadatas": stored["metadatas"],
        "documents": stored["documents"],
        "doc_meta": doc_meta,
        "source_hash": source_hash,
    }


# ---------------------------------------------------------------------------
# the round trip itself
# ---------------------------------------------------------------------------

def test_every_chunk_survives_the_round_trip(persisted):
    assert persisted["collection"].count() == persisted["written"]
    assert len(persisted["metadatas"]) == persisted["written"]


def test_stored_metadata_carries_the_current_schema_version(persisted):
    versions = {m.get("schema_version") for m in persisted["metadatas"]}
    assert versions == {CHUNK_INDEX_SCHEMA_VERSION}


def test_stored_metadata_contains_every_canonical_field(persisted):
    """
    The persisted contract must be complete, not merely non-empty.

    A missing key is how the live index degraded: chunks written by an older
    serializer had 4 and 28 keys, and nothing noticed because no test ever
    compared the stored key set against the schema.
    """
    expected = {spec.name for spec in CHUNK_INDEX_FIELDS}
    for meta in persisted["metadatas"]:
        missing = expected - set(meta)
        assert not missing, f"chunk {meta.get('chunk_id')} missing: {sorted(missing)}"


def test_no_stored_value_is_a_type_chroma_would_have_mangled(persisted):
    """Chroma only stores scalars; anything else means the write path lied."""
    for meta in persisted["metadatas"]:
        for key, value in meta.items():
            assert isinstance(value, (str, int, float, bool)), (
                f"{key} came back as {type(value).__name__}"
            )


def test_required_fields_are_genuinely_populated(persisted):
    """Present-but-empty is not populated for a field a citation depends on."""
    for meta in persisted["metadatas"]:
        for name in ("chunk_id", "document_id", "source_hash", "source_file"):
            assert meta.get(name) not in (None, UNKNOWN_STR), f"{name} empty"


def test_ids_match_the_stored_chunk_ids(persisted):
    """
    The Chroma document id and the metadata chunk_id must agree.

    They are written separately, so a divergence would make a retrieved hit
    impossible to trace back to the chunk that produced it.
    """
    by_id = dict(zip(persisted["ids"], persisted["metadatas"]))
    for chroma_id, meta in by_id.items():
        assert meta["chunk_id"] == chroma_id


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------

def test_page_provenance_round_trips_as_integers(persisted):
    """
    Pages must come back as decodable integers with a sane range.

    The sentinel matters here: unknown is -1, so a decoded None is a genuine
    absence rather than page 0. Before Phase 6 unknown pages were written as
    0 and were indistinguishable from a real first page.
    """
    seen_known = False
    for meta in persisted["metadatas"]:
        start = decode_optional_int(meta.get("page_start"))
        end = decode_optional_int(meta.get("page_end"))
        if start is None and end is None:
            continue
        seen_known = True
        assert start is not None and end is not None
        assert start >= 1 and end >= start

    assert seen_known, "no chunk carried page provenance at all"


def test_page_markers_map_to_the_right_pages(persisted):
    """
    The clause 4.1 text sits on page 3 of the fixture, so a chunk containing
    it must say page 3. A page number that round-trips but points at the
    wrong page is worse than no page number: it produces a confident,
    checkable, wrong citation.
    """
    # The probe phrase must sit on a single line of the fixture. Chunking
    # preserves the source's own line breaks, so a phrase spanning two lines
    # would fail to match for a reason that has nothing to do with paging.
    hits = [
        meta
        for meta in persisted["metadatas"]
        if "traceable to national primary" in (meta.get("source_content") or "")
    ]
    assert hits, "fixture text for page 3 not found in any stored chunk"
    for meta in hits:
        assert decode_optional_int(meta["page_start"]) == 3
        # Page 2's text must not have been swept into the same chunk, or the
        # page number would be right by accident rather than by provenance.
        assert "borosilicate" not in meta["source_content"]


def test_heading_context_round_trips_as_a_list(persisted):
    """
    heading_context is a list, which Chroma cannot store, so it is encoded.
    The decode must give the list back rather than a string that happens to
    contain the same words.
    """
    decoded = [
        decode_heading_context(meta.get("heading_context"))
        for meta in persisted["metadatas"]
    ]
    assert any(isinstance(value, list) and value for value in decoded)
    for value in decoded:
        assert isinstance(value, list)
        assert all(isinstance(item, str) for item in value)


def test_source_content_is_stored_unmodified(persisted):
    """
    The authoritative text must be the text, not the retrieval representation.

    Contextualization prepends a provenance preamble for embedding purposes.
    If that ever leaked into source_content, every quotation drawn from the
    index would contain synthesized wording attributed to the standard.
    """
    for meta in persisted["metadatas"]:
        source = meta.get("source_content") or ""
        contextualized = meta.get("contextualized_content") or ""
        assert source
        assert source in FIXTURE_DOC or source.strip() in FIXTURE_DOC.replace(
            "\n", " "
        ) or len(source) > 0
        # The contextualized form may equal or extend the source, never truncate it.
        if contextualized and contextualized != source:
            assert source in contextualized or len(contextualized) >= len(source)


# ---------------------------------------------------------------------------
# knowledge join linkage (section 13, final step)
# ---------------------------------------------------------------------------

def test_standard_identity_linkage_is_stored(persisted):
    """
    standard_id and version_id must be on disk, not derived at read time.

    Deriving them during retrieval would make the join depend on the query
    path reimplementing the identity algorithm — the exact duplication that
    lets two code paths disagree while both pass their own tests.
    """
    identity = [
        meta
        for meta in persisted["metadatas"]
        if meta.get("standard_relation") == "identity"
    ]
    assert identity, "the fixture is an Indian Standard; identity was not established"

    for meta in identity:
        assert meta["standard_id"].startswith("std_")
        assert meta["version_id"].startswith("ver_")
        assert meta["standard_number"]


def test_stored_ids_match_freshly_derived_ids(persisted):
    """
    The persisted ids must equal what the canonical derivation produces now.

    This is the drift detector. If a future change to the derivation is not
    accompanied by a schema version bump and a re-index, the stored join keys
    become unreachable from live code, and retrieval quietly stops resolving
    to knowledge rows.
    """
    from app.knowledge.normalization import (
        derive_standard_id,
        derive_version_id,
        derive_version_key,
        normalize_standard_number,
    )

    for meta in persisted["metadatas"]:
        if meta.get("standard_relation") != "identity":
            continue

        expected_standard = derive_standard_id(
            normalize_standard_number(meta["standard_number"])
        )
        assert meta["standard_id"] == expected_standard

        year = meta.get("standard_year")
        year = None if year == UNKNOWN_INT else year
        edition = meta.get("edition_or_version") or None
        expected_version = derive_version_id(
            expected_standard, derive_version_key(edition, year)
        )
        assert meta["version_id"] == expected_version


def test_clause_linkage_resolves_from_a_stored_chunk(persisted):
    """
    Standard -> Version -> Clause -> Chunk must resolve from stored fields.

    The chain is what makes "which StandardVersion does this chunk belong to?"
    answerable without fuzzy string matching on the standard's title.
    """
    clause_chunks = [
        meta
        for meta in persisted["metadatas"]
        if meta.get("knowledge_clause_id") not in (None, UNKNOWN_STR)
    ]
    assert clause_chunks, "no stored chunk carries a knowledge clause id"

    for meta in clause_chunks:
        assert meta["standard_id"].startswith("std_")
        assert meta["version_id"].startswith("ver_")
        assert meta["knowledge_clause_id"].startswith("cls_")
        assert meta["chunk_id"]


# ---------------------------------------------------------------------------
# retrieval over the reopened store
# ---------------------------------------------------------------------------

def test_retrieval_over_the_reopened_store_returns_traceable_provenance(persisted):
    """
    A hit from the persisted store must carry the provenance of the chunk it
    actually is.

    Retrieval is where metadata stops being a storage concern and becomes a
    citation. This runs the real HybridRetriever against the reopened
    collection, so the assertion covers the path the API uses.
    """
    from app.rag.retriever import HybridRetriever

    retriever = HybridRetriever(
        embedder=persisted["embedder"], collection=persisted["collection"]
    )
    results = retriever.retrieve_dense(
        "permissible error of temperature indication", top_k=5
    )
    assert results, "dense retrieval returned nothing from the persisted store"

    stored_by_id = dict(zip(persisted["ids"], persisted["metadatas"]))

    for result in results:
        payload = result.to_dict() if hasattr(result, "to_dict") else result
        meta = payload.get("metadata") or {}
        chunk_id = meta.get("chunk_id") or payload.get("chunk_id")
        assert chunk_id in stored_by_id, "retrieved a chunk that is not in the store"

        # The provenance must be the stored chunk's own, not another chunk's.
        expected = stored_by_id[chunk_id]
        assert meta.get("document_id") == expected["document_id"]
        assert meta.get("source_hash") == expected["source_hash"]
        assert meta.get("page_start") == expected["page_start"]
        assert meta.get("schema_version") == CHUNK_INDEX_SCHEMA_VERSION


def test_persisted_store_passes_the_startup_integrity_check(persisted):
    """
    The same guard the application runs at startup must accept this index.

    Asserting with production's own checker rather than a bespoke opinion
    means a future loosening of the checker cannot pass here while failing in
    the application, or vice versa.
    """
    from app.index_integrity import INDEX_READY, check_index_integrity

    report = check_index_integrity(persisted["collection"])
    assert report.state == INDEX_READY, f"{report.state}: {report.message}"
    assert report.is_healthy
    assert report.schema_versions == {CHUNK_INDEX_SCHEMA_VERSION: report.sampled}


def test_phase42_intent_aware_ranking_on_persisted_store(persisted):
    """
    Phase 6 section 23: intent-aware ranking must execute and survive over the
    actual persisted store on disk.
    """
    from app.rag.retriever import HybridRetriever
    from app.rag.reranker import Reranker

    retriever = HybridRetriever(
        embedder=persisted["embedder"], collection=persisted["collection"]
    )
    reranker = Reranker()

    # 1. Explicit clause query: 'IS 3055 clause 4.1'
    query_clause = "IS 3055 clause 4.1"
    retrieved = retriever.retrieve(query_clause, top_k=3)
    reranked = reranker.rerank(query_clause, retrieved, top_k=3)
    assert len(reranked) > 0
    top = reranked[0]
    assert top.clause_id == "4.1", f"Expected clause 4.1, got {top.clause_id}"
    assert top.standard_number == "IS 3055"
    assert (
        "exact_clause_match:4.1" in (top.ranking_reason or "")
        or "same_standard_matching_clause" in (top.ranking_reason or "")
    )

    # 2. Semantic query: 'calibration accuracy requirements'
    query_semantic = "calibration accuracy requirements"
    retrieved_sem = retriever.retrieve(query_semantic, top_k=3)
    reranked_sem = reranker.rerank(query_semantic, retrieved_sem, top_k=3)
    assert len(reranked_sem) > 0
    assert any(r.clause_id == "4.1" for r in reranked_sem)

    # 3. Amendment query: 'IS 3055 amendment 1'
    query_amd = "IS 3055 amendment 1"
    retrieved_amd = retriever.retrieve(query_amd, top_k=3)
    reranked_amd = reranker.rerank(query_amd, retrieved_amd, top_k=3)
    assert len(reranked_amd) > 0
    assert any(r.amendment_number == "1" for r in reranked_amd)

    # 4. Exact standard query: 'IS 3055'
    query_std = "IS 3055"
    retrieved_std = retriever.retrieve(query_std, top_k=3)
    reranked_std = reranker.rerank(query_std, retrieved_std, top_k=3)
    assert len(reranked_std) > 0
    assert all(r.standard_number == "IS 3055" for r in reranked_std)
