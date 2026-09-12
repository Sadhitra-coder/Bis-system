"""
tests/test_knowledge_join.py

Phase 6 sections 5, 15 and 16: the knowledge <-> retrieval join.

These tests exercise the join the way retrieval does — starting from the
metadata the index writer actually persists, not from hand-built dicts —
so a divergence between the serializer and the knowledge service fails
here instead of silently producing a join that resolves to nothing.
"""

import pytest

from app.index_schema import (
    CHUNK_INDEX_FIELD_NAMES,
    build_chunk_index_metadata,
)
from app.knowledge.join import (
    JOIN_METADATA_KEYS,
    RESOLUTION_DANGLING,
    RESOLUTION_RESOLVED,
    RESOLUTION_UNLINKED,
    chunk_ids_for_clause,
    chunk_ids_for_version,
    resolve_chunk,
    resolve_results,
    resolve_version_for_chunk,
    summarize_links,
)
from app.knowledge.repository import KnowledgeRepository
from app.knowledge.service import KnowledgeService
from app.models import DocumentMetadata


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path):
    r = KnowledgeRepository(db_path=tmp_path / "join.db")
    yield r
    r.close()


@pytest.fixture
def service(repo):
    return KnowledgeService(repository=repo)


def make_chunk(chunk_id, section, headings, clause_number, page, document_id):
    """A chunk in the shape the chunker emits: dict + parallel metadata dict."""
    meta = {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "section": section,
        "heading_context": headings,
        "clause_id": clause_number,
        "page_start": page,
        "page_end": page,
    }
    return {
        "chunk_id": chunk_id,
        "content": f"Normative body text of {section}.",
        "section": section,
        "heading_context": headings,
        "clause_id": clause_number,
        "metadata": meta,
    }


def index_metadata_for(chunk, doc_metadata):
    """
    Serialize a chunk exactly as the ingestion write path does.

    Going through build_chunk_index_metadata is the point: the ids the join
    reads must be the ids the real writer produces.
    """
    merged = dict(chunk["metadata"])
    merged.update({
        "source_file": doc_metadata.source_file,
        "source_hash": doc_metadata.source_hash,
        "standard_number": doc_metadata.standard_number,
        "standard_title": doc_metadata.standard_title,
        "standard_year": doc_metadata.standard_year,
        "edition_or_version": doc_metadata.edition_or_version,
        "document_type": doc_metadata.document_type,
        "authority": doc_metadata.authority,
    })
    payload = dict(chunk)
    payload["metadata"] = merged
    return build_chunk_index_metadata(
        payload,
        source_content=chunk["content"],
        contextualized_content=chunk["content"],
        context_generation_method="structural",
        context_generation_version="1.0",
    )


IS_3055_V3 = DocumentMetadata(
    document_id="doc_is3055_v3",
    source_file="standards/IS_3055_2024.pdf",
    source_filename="IS_3055_2024.pdf",
    source_hash="hash_v3",
    standard_number="IS 3055",
    standard_title="Code of practice for water supply",
    standard_year=2024,
    edition_or_version="Third Edition",
    document_type="indian_standard",
    authority="BIS",
)

IS_3055_V2 = DocumentMetadata(
    document_id="doc_is3055_v2",
    source_file="standards/IS_3055_2015.pdf",
    source_filename="IS_3055_2015.pdf",
    source_hash="hash_v2",
    standard_number="IS 3055",
    standard_title="Code of practice for water supply",
    standard_year=2015,
    edition_or_version="Second Edition",
    document_type="indian_standard",
    authority="BIS",
)


# ------------------------------------------------------------------
# Contract between the join and the canonical schema
# ------------------------------------------------------------------

def test_join_keys_are_declared_schema_fields():
    """
    app/knowledge/join.py restates its metadata keys instead of importing
    them from app.index_schema, because importing would create a circular
    import. This test is what keeps the two in sync: rename a field in the
    canonical schema and this fails immediately.
    """
    unknown = [k for k in JOIN_METADATA_KEYS if k not in CHUNK_INDEX_FIELD_NAMES]
    assert unknown == [], f"join reads keys absent from canonical schema: {unknown}"


# ------------------------------------------------------------------
# Section 15: Standard -> Version -> Clause -> Chunk
# ------------------------------------------------------------------

def test_full_chain_resolves_from_persisted_index_metadata(service, repo):
    chunk = make_chunk(
        "chunk_is3055_clause41", "4.1 Requirements", ["Section 4"], "4.1", 7,
        IS_3055_V3.document_id,
    )
    service.build_knowledge_from_ingestion(IS_3055_V3, [chunk])

    link = resolve_chunk(index_metadata_for(chunk, IS_3055_V3), repo=repo)

    assert link.resolution == RESOLUTION_RESOLVED, link.missing
    assert link.chunk_id == "chunk_is3055_clause41"

    # Every hop of the chain hydrated from a real SQLite row.
    assert link.standard is not None and link.standard.standard_number == "IS 3055"
    assert link.version is not None and link.version.edition == "Third Edition"
    assert link.clause is not None and link.clause.clause_number == "4.1"

    # And the ids are structurally nested, not merely present.
    assert link.standard_id.startswith("std_")
    assert link.version_id.startswith(f"ver_{link.standard_id}_")
    assert link.clause_id.startswith(f"cls_{link.standard_id}_")
    assert link.version.standard_id == link.standard_id
    assert link.clause.version_id == link.version_id


def test_resolve_version_for_chunk_answers_the_version_question(service, repo):
    chunk = make_chunk(
        "chunk_v3_51", "5.1 Testing", ["Section 5"], "5.1", 9, IS_3055_V3.document_id
    )
    service.build_knowledge_from_ingestion(IS_3055_V3, [chunk])

    version = resolve_version_for_chunk(index_metadata_for(chunk, IS_3055_V3), repo=repo)

    assert version is not None
    assert version.edition == "Third Edition"
    assert version.standard_year == 2024


def test_two_versions_each_own_clause_41_without_collision(service, repo):
    """
    Clause 4.1 exists in both editions. Version-scoped clause ids are the
    only thing that keeps them apart; a standard-scoped or number-only id
    would silently merge two different requirements.
    """
    chunk_v3 = make_chunk(
        "chunk_v3_clause41", "4.1 Requirements", ["Section 4"], "4.1", 7,
        IS_3055_V3.document_id,
    )
    chunk_v2 = make_chunk(
        "chunk_v2_clause41", "4.1 Requirements", ["Section 4"], "4.1", 5,
        IS_3055_V2.document_id,
    )
    service.build_knowledge_from_ingestion(IS_3055_V3, [chunk_v3])
    service.build_knowledge_from_ingestion(IS_3055_V2, [chunk_v2])

    link_v3 = resolve_chunk(index_metadata_for(chunk_v3, IS_3055_V3), repo=repo)
    link_v2 = resolve_chunk(index_metadata_for(chunk_v2, IS_3055_V2), repo=repo)

    assert link_v3.resolution == RESOLUTION_RESOLVED, link_v3.missing
    assert link_v2.resolution == RESOLUTION_RESOLVED, link_v2.missing

    # Same standard, different versions, different clauses.
    assert link_v3.standard_id == link_v2.standard_id
    assert link_v3.version_id != link_v2.version_id
    assert link_v3.clause_id != link_v2.clause_id

    # Both really are clause 4.1, on their own pages.
    assert link_v3.clause.clause_number == "4.1"
    assert link_v2.clause.clause_number == "4.1"
    assert link_v3.clause.page_start == 7
    assert link_v2.clause.page_start == 5


def test_masthead_chunk_resolves_without_claiming_a_clause(service, repo):
    """
    The knowledge service writes no Clause for a document header, so the
    index must not claim one either — otherwise the join points at a row
    that was never written.
    """
    header = make_chunk(
        "chunk_header", "IS 3055 : 2024", [], None, 1, IS_3055_V3.document_id
    )
    body = make_chunk(
        "chunk_body", "4.1 Requirements", ["Section 4"], "4.1", 7,
        IS_3055_V3.document_id,
    )
    service.build_knowledge_from_ingestion(IS_3055_V3, [header, body])

    link = resolve_chunk(index_metadata_for(header, IS_3055_V3), repo=repo)

    assert link.resolution == RESOLUTION_RESOLVED, link.missing
    assert link.standard is not None
    assert link.version is not None
    assert link.clause_id is None
    assert link.clause is None


# ------------------------------------------------------------------
# Reverse direction: knowledge -> chunks
# ------------------------------------------------------------------

def test_reverse_join_clause_and_version_to_chunks(service, repo):
    chunk_v3 = make_chunk(
        "chunk_v3_clause41", "4.1 Requirements", ["Section 4"], "4.1", 7,
        IS_3055_V3.document_id,
    )
    chunk_v2 = make_chunk(
        "chunk_v2_clause41", "4.1 Requirements", ["Section 4"], "4.1", 5,
        IS_3055_V2.document_id,
    )
    service.build_knowledge_from_ingestion(IS_3055_V3, [chunk_v3])
    service.build_knowledge_from_ingestion(IS_3055_V2, [chunk_v2])

    link_v3 = resolve_chunk(index_metadata_for(chunk_v3, IS_3055_V3), repo=repo)
    link_v2 = resolve_chunk(index_metadata_for(chunk_v2, IS_3055_V2), repo=repo)

    assert chunk_ids_for_clause(link_v3.clause_id, repo=repo) == ["chunk_v3_clause41"]
    assert chunk_ids_for_clause(link_v2.clause_id, repo=repo) == ["chunk_v2_clause41"]

    # Scoped to one version.
    assert chunk_ids_for_version(
        link_v3.standard_id, link_v3.version_id, repo=repo
    ) == ["chunk_v3_clause41"]

    # Version-agnostic: every version of the standard.
    every = chunk_ids_for_version(link_v3.standard_id, None, repo=repo)
    assert set(every) == {"chunk_v3_clause41", "chunk_v2_clause41"}


def test_reverse_join_on_unknown_clause_returns_empty(repo):
    assert chunk_ids_for_clause("cls_does_not_exist", repo=repo) == []
    assert chunk_ids_for_version("std_does_not_exist", None, repo=repo) == []


# ------------------------------------------------------------------
# Section 16: a document that REFERENCES a standard is not that standard
# ------------------------------------------------------------------

@pytest.mark.parametrize("doc_type", ["product_manual", "gazette_order", "tender", None])
def test_referencing_document_is_not_promoted_to_a_standard(service, repo, doc_type):
    """
    A pump manual and a Gazette order both quote 'IS 3055'. Neither IS
    IS 3055. Promoting the citation would fabricate a Standard entity and
    collapse unrelated documents into one logical standard.
    """
    doc = DocumentMetadata(
        document_id=f"doc_{doc_type or 'untyped'}",
        source_file=f"other/{doc_type or 'untyped'}.pdf",
        source_filename=f"{doc_type or 'untyped'}.pdf",
        source_hash=f"hash_{doc_type or 'untyped'}",
        standard_number="IS 3055",
        document_type=doc_type,
    )
    chunk = make_chunk(
        "chunk_ref_1", "Compliance", ["Introduction"], None, 2, doc.document_id
    )

    diagnostics = service.build_knowledge_from_ingestion(doc, [chunk])

    # No Standard entity fabricated from a citation.
    assert diagnostics.standards_created == 0
    assert repo.get_standard_by_number("IS 3055") is None

    metadata = index_metadata_for(chunk, doc)

    # The citation is still RECORDED — suppressing it would lose information.
    assert metadata["standard_number"] == "IS 3055"
    # But no identity is claimed.
    assert metadata["standard_relation"] == "reference"
    assert metadata["standard_id"] == ""
    assert metadata["version_id"] == ""
    assert metadata["knowledge_clause_id"] == ""

    link = resolve_chunk(metadata, repo=repo)
    assert link.resolution == RESOLUTION_UNLINKED
    assert link.relation == "reference"
    assert link.standard is None and link.version is None and link.clause is None


def test_document_with_no_standard_number_is_unlinked(service, repo):
    doc = DocumentMetadata(
        document_id="doc_generic",
        source_file="other/notes.pdf",
        source_filename="notes.pdf",
        source_hash="hash_generic",
    )
    chunk = make_chunk("chunk_generic", "Overview", [], None, 1, doc.document_id)

    diagnostics = service.build_knowledge_from_ingestion(doc, [chunk])
    assert diagnostics.standards_created == 0

    metadata = index_metadata_for(chunk, doc)
    assert metadata["standard_relation"] == "none"

    link = resolve_chunk(metadata, repo=repo)
    assert link.resolution == RESOLUTION_UNLINKED
    assert link.relation == "none"


# ------------------------------------------------------------------
# Integrity: a claimed-but-absent row is a defect, not an unlinked chunk
# ------------------------------------------------------------------

def test_identity_without_knowledge_rows_is_reported_dangling(tmp_path):
    """
    Index and knowledge model diverged. This must be distinguishable from a
    legitimately unlinked chunk, because one is normal and the other means
    a re-index is required.
    """
    empty = KnowledgeRepository(db_path=tmp_path / "empty.db")
    try:
        chunk = make_chunk(
            "chunk_orphan", "4.1 Requirements", ["Section 4"], "4.1", 7,
            IS_3055_V3.document_id,
        )
        link = resolve_chunk(index_metadata_for(chunk, IS_3055_V3), repo=empty)

        assert link.resolution == RESOLUTION_DANGLING
        assert link.standard_id and link.version_id and link.clause_id
        assert link.standard is None
        assert len(link.missing) == 3
        assert "dangling" in link.describe()
    finally:
        empty.close()


def test_identity_ids_without_identity_relation_is_dangling(repo):
    """
    A relation of 'reference' carrying std_/ver_ ids means the serializer
    and the classifier disagreed. Trusting either half would be worse than
    flagging it.
    """
    link = resolve_chunk(
        {
            "chunk_id": "chunk_inconsistent",
            "document_id": "doc_x",
            "standard_relation": "reference",
            "standard_id": "std_IS_3055_abcdef0123456789",
            "version_id": "",
            "knowledge_clause_id": "",
        },
        repo=repo,
    )
    assert link.resolution == RESOLUTION_DANGLING
    assert link.missing


def test_summarize_links_separates_unlinked_from_dangling(service, repo):
    body = make_chunk(
        "chunk_body", "4.1 Requirements", ["Section 4"], "4.1", 7,
        IS_3055_V3.document_id,
    )
    service.build_knowledge_from_ingestion(IS_3055_V3, [body])

    manual = DocumentMetadata(
        document_id="doc_manual",
        source_file="manuals/pump.pdf",
        source_filename="pump.pdf",
        source_hash="hash_manual",
        standard_number="IS 3055",
        document_type="product_manual",
    )
    manual_chunk = make_chunk(
        "chunk_manual", "Compliance", ["Introduction"], None, 2, manual.document_id
    )

    orphan = dict(index_metadata_for(body, IS_3055_V3))
    orphan["chunk_id"] = "chunk_orphan"
    orphan["version_id"] = "ver_std_IS_3055_deadbeef_000000000000"

    summary = summarize_links(resolve_results(
        [
            index_metadata_for(body, IS_3055_V3),
            index_metadata_for(manual_chunk, manual),
            orphan,
        ],
        repo=repo,
    ))

    assert summary["total"] == 3
    assert summary["resolved"] == 1
    assert summary["unlinked"] == 1
    assert summary["dangling"] == 1
    assert len(summary["dangling_details"]) == 1
    assert "chunk_orphan" in summary["dangling_details"][0]
