"""
tests/test_knowledge.py

Phase 3 BIS Knowledge Model, Clause Hierarchy, and Version/Amendment Foundation Tests:
1. Standard creation
2. Deterministic standard ID
3. Multiple versions (historical versions preserved without overwrite)
4. Amendments
5. Parts
6. Nested clauses and parent relationships
7. Non-numeric headings
8. Clause parent relationships
9. Clause <-> chunk relationship (many chunks mapped to clause)
10. Standard <-> document relationship
11. Standard references (citation detection)
12. Unresolved target reference handling
13. Normalization (standards, parts, clauses)
14. Validation failures detection
15. Historical versions are not overwritten on re-ingestion
16. Deterministic re-ingestion
17. Provenance survives knowledge-object creation
18. End-to-end knowledge hierarchy from multi-page fixture
19. Non-standard Gazette document is NOT classified as Standard
20. Service query operations (get_standard, get_standard_clauses, etc.)
"""

import time
import pytest
from app.models import DocumentMetadata
from app.knowledge.models import (
    Standard,
    StandardVersion,
    StandardPart,
    Clause,
    Amendment,
    StandardReference,
    StandardStatus,
    ReferenceType,
    ResolutionStatus,
)
from app.knowledge.normalization import (
    normalize_standard_number,
    normalize_clause_number,
    derive_clause_hierarchy,
    derive_standard_id,
    derive_version_id,
    derive_part_id,
    derive_clause_id,
    derive_amendment_id,
    derive_reference_id,
)
from app.knowledge.validator import validate_knowledge_graph
from app.knowledge.repository import KnowledgeRepository
from app.knowledge.service import KnowledgeService


@pytest.fixture
def mem_repo():
    """In-memory SQLite repository for isolated testing."""
    return KnowledgeRepository(db_path=":memory:")


@pytest.fixture
def service(mem_repo):
    """KnowledgeService backed by in-memory repository."""
    return KnowledgeService(repository=mem_repo)


# ============================================================
# 1. Standard Creation & Deterministic IDs
# ============================================================

def test_standard_creation_and_deterministic_id(mem_repo):
    id1 = derive_standard_id("IS 3055")
    id2 = derive_standard_id("IS 3055")
    assert id1 == id2
    assert id1.startswith("std_IS_3055_")

    std = Standard(
        standard_id=id1,
        standard_number="IS 3055",
        standard_title="Clinical Thermometers",
        authority="BIS",
        standard_year=2024,
        document_id="doc_abc123",
        edition_or_version="Third Edition",
        status=StandardStatus.PUBLISHED,
        created_at=time.time(),
        updated_at=time.time(),
    )
    mem_repo.save_standard(std)

    fetched = mem_repo.get_standard(id1)
    assert fetched is not None
    assert fetched.standard_number == "IS 3055"
    assert fetched.standard_title == "Clinical Thermometers"
    assert fetched.document_id == "doc_abc123"
    assert fetched.status == StandardStatus.PUBLISHED


# ============================================================
# 2. Multiple Versions (Historical Preservation)
# ============================================================

def test_multiple_versions_not_overwritten(mem_repo):
    std_id = derive_standard_id("IS 3055")
    std = Standard(
        standard_id=std_id,
        standard_number="IS 3055",
        standard_title="Clinical Thermometers",
        document_id="doc_base",
        created_at=100.0,
        updated_at=100.0,
    )
    mem_repo.save_standard(std)

    # Version 1 (1989)
    v1_id = derive_version_id(std_id, "First Edition")
    v1 = StandardVersion(
        version_id=v1_id,
        standard_id=std_id,
        edition="First Edition",
        standard_year=1989,
        document_id="doc_1989",
        status=StandardStatus.SUPERSEDED,
        created_at=100.0,
    )
    mem_repo.save_version(v1)

    # Version 2 (2024)
    v2_id = derive_version_id(std_id, "Second Edition")
    v2 = StandardVersion(
        version_id=v2_id,
        standard_id=std_id,
        edition="Second Edition",
        standard_year=2024,
        document_id="doc_2024",
        status=StandardStatus.PUBLISHED,
        created_at=200.0,
    )
    mem_repo.save_version(v2)

    versions = mem_repo.get_standard_versions(std_id)
    assert len(versions) == 2
    assert {v.edition for v in versions} == {"First Edition", "Second Edition"}
    assert {v.document_id for v in versions} == {"doc_1989", "doc_2024"}


# ============================================================
# 3. Amendments & Parts
# ============================================================

def test_amendments_and_parts_relationship(mem_repo):
    std_id = derive_standard_id("IS 3055")

    # Part
    part_id = derive_part_id(std_id, "1")
    part = StandardPart(
        part_id=part_id,
        standard_id=std_id,
        part_number="1",
        part_title="Solid Stem Thermometers",
        document_id="doc_p1",
        created_at=time.time(),
    )
    mem_repo.save_part(part)

    # Amendment
    amd_id = derive_amendment_id(std_id, "2")
    amd = Amendment(
        amendment_id=amd_id,
        standard_id=std_id,
        amendment_number="2",
        title="Amendment No. 2 to IS 3055",
        source_document_id="doc_amd2",
        created_at=time.time(),
    )
    mem_repo.save_amendment(amd)

    amds = mem_repo.get_amendments(std_id)
    assert len(amds) == 1
    assert amds[0].amendment_number == "2"
    assert amds[0].source_document_id == "doc_amd2"


# ============================================================
# 4. Nested Clauses, Parent Hierarchy & Non-numeric Headings
# ============================================================

def test_nested_clauses_and_parent_links(mem_repo):
    std_id = derive_standard_id("IS 3055")
    ver_id = derive_version_id(std_id, "Third Edition")

    # Clause 4 (Level 1)
    c4_id = derive_clause_id(std_id, ver_id, "4", "4 Requirements")
    c4 = Clause(
        clause_id=c4_id,
        standard_id=std_id,
        version_id=ver_id,
        parent_clause_id=None,
        clause_number="4",
        clause_title="Requirements",
        level=1,
        document_id="doc_1",
        page_start=2,
        page_end=2,
        heading_path="4 Requirements",
        source_chunk_ids=["chunk_001"],
        created_at=time.time(),
    )
    mem_repo.save_clause(c4)

    # Clause 4.1 (Level 2, parent is Clause 4)
    c41_id = derive_clause_id(std_id, ver_id, "4.1", "4 Requirements > 4.1 Calibration")
    c41 = Clause(
        clause_id=c41_id,
        standard_id=std_id,
        version_id=ver_id,
        parent_clause_id=c4_id,
        clause_number="4.1",
        clause_title="Calibration",
        level=2,
        document_id="doc_1",
        page_start=3,
        page_end=3,
        heading_path="4 Requirements > 4.1 Calibration",
        source_chunk_ids=["chunk_002", "chunk_003"],  # Multiple chunks
        created_at=time.time(),
    )
    mem_repo.save_clause(c41)

    # Non-numeric Annex heading (Level 1)
    annex_id = derive_clause_id(std_id, ver_id, None, "Annex A Test Equipment")
    c_annex = Clause(
        clause_id=annex_id,
        standard_id=std_id,
        version_id=ver_id,
        parent_clause_id=None,
        clause_number=None,
        clause_title="Annex A Test Equipment",
        level=1,
        document_id="doc_1",
        page_start=4,
        page_end=5,
        heading_path="Annex A Test Equipment",
        source_chunk_ids=["chunk_004"],
        created_at=time.time(),
    )
    mem_repo.save_clause(c_annex)

    clauses = mem_repo.get_standard_clauses(std_id)
    assert len(clauses) == 3

    fetched_c41 = mem_repo.get_clause(c41_id)
    assert fetched_c41.parent_clause_id == c4_id
    assert len(fetched_c41.source_chunk_ids) == 2
    assert fetched_c41.level == 2



# ============================================================
# 5. Standard References (Resolved & Unresolved)
# ============================================================

def test_standard_references_resolution(mem_repo):
    std_a_id = derive_standard_id("IS 3055")
    std_b_id = derive_standard_id("IS 4984")

    # Target standard B exists
    std_b = Standard(
        standard_id=std_b_id,
        standard_number="IS 4984",
        standard_title="HDPE Pipes",
        document_id="doc_b",
        created_at=time.time(),
        updated_at=time.time(),
    )
    mem_repo.save_standard(std_b)

    # Reference from A to B (Resolved)
    ref1_id = derive_reference_id(std_a_id, "IS 4984", "4.1")
    ref1 = StandardReference(
        relationship_id=ref1_id,
        source_standard_id=std_a_id,
        target_standard_number="IS 4984",
        target_standard_id=std_b_id,
        relationship_type=ReferenceType.NORMATIVE_REFERENCE,
        source_document_id="doc_a",
        source_clause_id="4.1",
        resolution_status=ResolutionStatus.RESOLVED,
        created_at=time.time(),
    )
    mem_repo.save_reference(ref1)

    # Reference from A to C (Unresolved, C not in store)
    ref2_id = derive_reference_id(std_a_id, "IS 9999", "5.2")
    ref2 = StandardReference(
        relationship_id=ref2_id,
        source_standard_id=std_a_id,
        target_standard_number="IS 9999",
        target_standard_id=None,
        relationship_type=ReferenceType.REFERENCES,
        source_document_id="doc_a",
        source_clause_id="5.2",
        resolution_status=ResolutionStatus.UNRESOLVED,
        created_at=time.time(),
    )
    mem_repo.save_reference(ref2)

    refs = mem_repo.get_standard_references(std_a_id)
    assert len(refs) == 2
    res_refs = [r for r in refs if r.resolution_status == ResolutionStatus.RESOLVED]
    unres_refs = [r for r in refs if r.resolution_status == ResolutionStatus.UNRESOLVED]
    assert len(res_refs) == 1
    assert res_refs[0].target_standard_id == std_b_id
    assert len(unres_refs) == 1
    assert unres_refs[0].target_standard_id is None


# ============================================================
# 6. Normalization Rules
# ============================================================

def test_normalization_rules():
    assert normalize_standard_number("IS 15644 : 2024") == "IS 15644"
    assert normalize_standard_number("IS15644:2024") == "IS 15644"
    assert normalize_standard_number("IS 3055-1 : 2020") == "IS 3055-1"
    assert normalize_standard_number("IS 3055 (Part 1)") == "IS 3055-1"
    assert normalize_standard_number("IS No. 2062") == "IS 2062"

    assert normalize_clause_number("Clause 4.1.2") == "4.1.2"
    assert normalize_clause_number("4.1.2.") == "4.1.2"
    assert normalize_clause_number("Section 5") == "5"
    assert normalize_clause_number("Preamble") is None

    lvl, parent = derive_clause_hierarchy("4.1.2")
    assert lvl == 3
    assert parent == "4.1"

    lvl, parent = derive_clause_hierarchy("4")
    assert lvl == 1
    assert parent is None


# ============================================================
# 7. Validator Integrity Checks
# ============================================================

def test_validator_detects_issues():
    std_id = "std_1"
    std = Standard(
        standard_id=std_id,
        standard_number="IS 100",
        document_id="doc_1",
        created_at=1.0,
        updated_at=1.0,
    )

    # Version pointing to non-existent parent standard
    bad_ver = StandardVersion(
        version_id="ver_bad",
        standard_id="std_missing",
        document_id="doc_1",
        created_at=1.0,
    )

    # Orphan clause (parent does not exist)
    orphan_cls = Clause(
        clause_id="cls_orphan",
        standard_id=std_id,
        parent_clause_id="cls_does_not_exist",
        clause_title="Test",
        level=2,
        document_id="doc_1",
        heading_path="4 > 4.1",
        source_chunk_ids=["chunk_1"],
        created_at=1.0,
    )

    errors, warnings = validate_knowledge_graph(
        standards=[std],
        versions=[bad_ver],
        parts=[],
        clauses=[orphan_cls],
        amendments=[],
        references=[],
    )

    assert any("version_missing_parent_standard" in e for e in errors)
    assert any("orphan_clause" in w for w in warnings)



# ============================================================
# 8. Service Ingestion Mapping: BIS Standard vs Gazette Order
# ============================================================

def test_service_ingestion_distinguishes_standard_from_gazette(service):
    # 1. Non-standard Gazette document -> Should NOT produce a Standard entity
    gazette_meta = DocumentMetadata(
        document_id="doc_gazette_4345",
        source_file="gazette.pdf",
        source_filename="gazette.pdf",
        document_type="gazette_order",
        standard_number=None,
    )
    diag1 = service.build_knowledge_from_ingestion(gazette_meta, chunks=[{"content": "Order details"}])
    assert diag1.standards_created == 0
    assert service.get_standard_by_number("IS 4345") is None

    # 2. BIS Standard document -> Produces complete knowledge hierarchy
    bis_meta = DocumentMetadata(
        document_id="doc_is_3055",
        source_file="IS_3055.pdf",
        source_filename="IS_3055.pdf",
        document_type="indian_standard",
        standard_number="IS 3055",
        standard_title="Clinical Thermometers",
        standard_year=2024,
        edition_or_version="Third Edition",
        amendment_number="1",
    )
    chunks = [
        {
            "chunk_id": "c1",
            "section": "4 Requirements",
            "heading_context": [],
            "clause_id": "4",
            "page_start": 2,
            "page_end": 2,
            "content": "General requirements. Refer to IS 4984 for plastics.",
        },
        {
            "chunk_id": "c2",
            "section": "4.1 Calibration",
            "heading_context": ["4 Requirements"],
            "clause_id": "4.1",
            "page_start": 3,
            "page_end": 3,
            "content": "Calibration specifications.",
        },
    ]
    diag2 = service.build_knowledge_from_ingestion(bis_meta, chunks=chunks)
    assert diag2.standards_created == 1
    assert diag2.versions_created == 1
    assert diag2.amendments_created == 1
    assert diag2.clauses_created == 2
    assert diag2.references_created == 1  # IS 4984 detected in chunk text
    assert diag2.unresolved_references == 1  # IS 4984 not yet in store

    # Verify query service retrieves hierarchy
    std = service.get_standard_by_number("IS 3055")
    assert std is not None
    assert std.standard_number == "IS 3055"

    clauses = service.get_standard_clauses(std.standard_id)
    assert len(clauses) == 2
    c4 = next(c for c in clauses if c.clause_number == "4")
    c41 = next(c for c in clauses if c.clause_number == "4.1")
    assert c41.parent_clause_id == c4.clause_id
    assert c41.page_start == 3
    assert "c2" in c41.source_chunk_ids

    # References
    refs = service.get_standard_references(std.standard_id)
    assert len(refs) == 1
    assert refs[0].target_standard_number == "IS 4984"
    assert refs[0].resolution_status == ResolutionStatus.UNRESOLVED


# ============================================================
# 9. Deterministic Re-ingestion
# ============================================================

def test_deterministic_reingestion(service):
    meta = DocumentMetadata(
        document_id="doc_std_123",
        source_file="IS_123.pdf",
        source_filename="IS_123.pdf",
        standard_number="IS 123",
        edition_or_version="First Edition",
        # Phase 6: a standard number alone is a CITATION. Standard identity
        # additionally requires a document_type that declares the document to
        # be a standard, otherwise a product manual quoting "IS 123" would
        # fabricate a Standard entity. See classify_standard_relation().
        document_type="indian_standard",
    )
    chunks = [{"chunk_id": "ch1", "section": "1 Scope", "clause_id": "1", "content": "Scope text"}]

    # Ingest twice
    d1 = service.build_knowledge_from_ingestion(meta, chunks)
    d2 = service.build_knowledge_from_ingestion(meta, chunks)

    # Second run updates existing standard instead of creating a duplicate standard
    assert d1.standards_created == 1
    assert d2.standards_created == 0

    std = service.get_standard_by_number("IS 123")
    assert std is not None
    clauses = service.get_standard_clauses(std.standard_id)
    assert len(clauses) == 1


# ============================================================
# Phase 3.1 Regression Tests — Version-Scoped Clauses & Semantics
# ============================================================

def test_same_clause_number_two_versions_no_collision(mem_repo):
    """Same clause number in two different versions → different clause_ids, both persist."""
    std_id = derive_standard_id("IS 3055")
    ver1_id = derive_version_id(std_id, "Second Edition")
    ver2_id = derive_version_id(std_id, "Third Edition")

    c4_v1_id = derive_clause_id(std_id, ver1_id, "4", "4 Requirements")
    c4_v2_id = derive_clause_id(std_id, ver2_id, "4", "4 Requirements")
    assert c4_v1_id != c4_v2_id, "Same clause number across versions must yield different IDs"

    for cid, vid, title in [
        (c4_v1_id, ver1_id, "Requirements v1"),
        (c4_v2_id, ver2_id, "Requirements v2 revised"),
    ]:
        mem_repo.save_clause(Clause(
            clause_id=cid, standard_id=std_id, version_id=vid,
            clause_number="4", clause_title=title, level=1,
            document_id="doc_x", heading_path="4 Requirements",
            source_chunk_ids=["ch_" + vid[:8]], created_at=time.time(),
        ))

    clauses = mem_repo.get_standard_clauses(std_id)
    assert len(clauses) == 2
    titles = {c.clause_title for c in clauses}
    assert "Requirements v1" in titles and "Requirements v2 revised" in titles


def test_parent_child_hierarchy_within_one_version(mem_repo):
    """3-level hierarchy in one version: 4 → 4.1 → 4.1.2, links must be correct."""
    std_id = derive_standard_id("IS 8888")
    ver_id = derive_version_id(std_id, "First Edition")

    c4_id = derive_clause_id(std_id, ver_id, "4", "4 General")
    c41_id = derive_clause_id(std_id, ver_id, "4.1", "4 General > 4.1 Scope")
    c412_id = derive_clause_id(std_id, ver_id, "4.1.2", "4 General > 4.1 Scope > 4.1.2 Details")

    for cid, cnum, ctitle, clvl, parent in [
        (c4_id, "4", "General", 1, None),
        (c41_id, "4.1", "Scope", 2, c4_id),
        (c412_id, "4.1.2", "Details", 3, c41_id),
    ]:
        mem_repo.save_clause(Clause(
            clause_id=cid, standard_id=std_id, version_id=ver_id,
            clause_number=cnum, clause_title=ctitle, level=clvl,
            parent_clause_id=parent, document_id="doc_x",
            heading_path=ctitle, source_chunk_ids=["ch_x"], created_at=time.time(),
        ))

    fetched = mem_repo.get_clause(c412_id)
    assert fetched.parent_clause_id == c41_id
    assert fetched.level == 3


def test_same_clause_number_different_standards():
    """Clause 4 in IS 3055 and Clause 4 in IS 8888 must have different clause_ids."""
    std_a = derive_standard_id("IS 3055")
    std_b = derive_standard_id("IS 8888")
    ver_a = derive_version_id(std_a, "Third Edition")
    ver_b = derive_version_id(std_b, "First Edition")

    c4_a = derive_clause_id(std_a, ver_a, "4", "4 Requirements")
    c4_b = derive_clause_id(std_b, ver_b, "4", "4 Requirements")
    assert c4_a != c4_b


def test_header_text_not_classified_as_clause(service):
    """Section heading matching IS number pattern must not produce a Clause entity."""
    meta = DocumentMetadata(
        document_id="doc_header_guard",
        source_file="IS_3055.pdf",
        source_filename="IS_3055.pdf",
        document_type="indian_standard",
        standard_number="IS 3055",
        edition_or_version="Third Edition",
    )
    chunks = [
        # Header chunk — must be skipped
        {
            "chunk_id": "hdr",
            "section": "IS 3055 : 2024",
            "heading_context": [],
            "clause_id": None,
            "content": "SPECIFICATION FOR CLINICAL THERMOMETERS Third Edition",
        },
        # Real normative clause — must produce a Clause
        {
            "chunk_id": "req",
            "section": "4 Requirements",
            "heading_context": [],
            "clause_id": "4",
            "content": "All glass components shall conform to borosilicate standard." * 5,
        },
    ]
    diag = service.build_knowledge_from_ingestion(meta, chunks)
    std = service.get_standard_by_number("IS 3055")
    clauses = service.get_standard_clauses(std.standard_id)

    assert len(clauses) == 1, (
        f"Expected exactly 1 clause (no header), got {len(clauses)}: "
        f"{[c.clause_title for c in clauses]}"
    )
    assert clauses[0].clause_number == "4"


def test_clause_provenance_survives_ingestion(service):
    """page_start, page_end, source_chunk_ids, document_id must survive round-trip."""
    meta = DocumentMetadata(
        document_id="doc_prov_test",
        source_file="IS_prov.pdf",
        source_filename="IS_prov.pdf",
        document_type="indian_standard",
        standard_number="IS 9991",
        edition_or_version="First Edition",
    )
    chunks = [{
        "chunk_id": "ch_prov_001",
        "section": "5 Testing",
        "clause_id": "5",
        "heading_context": [],
        "page_start": 7,
        "page_end": 8,
        "content": "Testing procedures for the standard." * 10,
    }]
    service.build_knowledge_from_ingestion(meta, chunks)
    std = service.get_standard_by_number("IS 9991")
    clauses = service.get_standard_clauses(std.standard_id)

    assert len(clauses) == 1
    c = clauses[0]
    assert c.page_start == 7
    assert c.page_end == 8
    assert "ch_prov_001" in c.source_chunk_ids
    assert c.document_id == "doc_prov_test"


def test_standard_id_semantics_same_number_different_sources():
    """
    Two different source documents (different bytes) for same IS number
    → same standard_id, different version_id, different clause_id per version.
    """
    # Simulate two different PDFs of IS 3055
    std_id_1 = derive_standard_id("IS 3055")
    std_id_2 = derive_standard_id("IS 3055")
    assert std_id_1 == std_id_2, "Same IS number → same logical standard_id"

    ver_ed2 = derive_version_id(std_id_1, "Second Edition")
    ver_ed3 = derive_version_id(std_id_1, "Third Edition")
    assert ver_ed2 != ver_ed3, "Different editions → different version_id"

    c4_ed2 = derive_clause_id(std_id_1, ver_ed2, "4", "4 Requirements")
    c4_ed3 = derive_clause_id(std_id_1, ver_ed3, "4", "4 Requirements")
    assert c4_ed2 != c4_ed3, "Same clause in different editions → different clause_id"
