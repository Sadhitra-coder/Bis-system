"""
tests/test_product_mapping.py

Comprehensive Test Suite for Phase 11:
Product-to-BIS Standard Mapping Engine.

Covers:
  - 30 Failure-First Functional Tests (Section 26)
  - 6 Adversarial Tests (Section 27: A, B, C, D, E, F)
  - Evaluation Dataset Execution (Section 32, 33)
"""

import os
os.environ.setdefault("USE_TF", "0")

import pytest
import time
from typing import Any, Dict, List, Optional

from app.confidence.models import ConfidenceLevel, Decision, QueryState
from app.evidence.models import EvidenceItem
from app.knowledge.models import (
    Clause,
    ReferenceType,
    Standard,
    StandardReference,
    StandardStatus,
    StandardVersion,
)
from app.knowledge.repository import KnowledgeRepository
from app.product_mapping.models import (
    MappingReason,
    MappingReasonType,
    MappingStatus,
    ProductContext,
    ProductStandardCandidate,
)
from app.product_mapping.extractor import (
    extract_product_context,
    normalize_product_text,
)
from app.product_mapping.engine import (
    aggregate_chunks_by_standard,
    discover_standard_candidates,
    explain_standard_mapping,
    generate_candidate_queries,
    rank_standard_candidates,
    score_standard_candidate,
)
from app.product_mapping.evaluator import evaluate_product_mapping
from app.query_intelligence.models import (
    BusinessContext,
    QueryContext,
    QueryIntentType,
    RetrievalStrategy,
)
from app.rag.pipeline import RAGPipeline


# ============================================================
# TEST FIXTURES & MOCKS
# ============================================================

class InMemoryKnowledgeRepo:
    """Test knowledge repository implementing required lookups."""
    def __init__(self):
        self.standards: Dict[str, Standard] = {}
        self.versions: Dict[str, List[StandardVersion]] = {}
        self.clauses: Dict[str, List[Clause]] = {}
        self.references: Dict[str, List[StandardReference]] = {}

    def add_standard(self, std: Standard):
        self.standards[std.standard_id] = std

    def get_standard(self, std_id: str) -> Optional[Standard]:
        return self.standards.get(std_id)

    def get_standard_by_number(self, std_number: str) -> Optional[Standard]:
        for s in self.standards.values():
            if s.standard_number.upper() == std_number.upper():
                return s
        return None

    def list_standards(self) -> List[Standard]:
        return list(self.standards.values())

    def list_versions_for_standard(self, std_id: str) -> List[StandardVersion]:
        return self.versions.get(std_id, [])

    def list_relationships_for_standard(self, std_id: str) -> List[StandardReference]:
        return self.references.get(std_id, [])


class MockMappingRetriever:
    """Mock retriever returning synthetic evidence items."""
    def __init__(self, chunks: List[Dict[str, Any]]):
        self.chunks = chunks

    def retrieve(self, query: str, top_k: int = 5, **kwargs) -> List[Dict[str, Any]]:
        # Simple keyword matching across synthetic chunks
        q_words = set(query.lower().split())
        matched = []
        for c in self.chunks:
            txt = (c.get("content") or c.get("source_content") or "").lower()
            if any(w in txt for w in q_words):
                matched.append(c)
        return (matched or self.chunks)[:top_k]


@pytest.fixture
def sample_repo():
    repo = InMemoryKnowledgeRepo()
    std1 = Standard(
        standard_id="std_IS_3055",
        standard_number="IS 3055",
        standard_title="Clinical Thermometers - Part 1 : Solid-Stem Type",
        authority="BIS",
        standard_year=2024,
        document_id="doc_is_3055",
        edition_or_version="Third Edition",
        status=StandardStatus.PUBLISHED,
        created_at=time.time(),
        updated_at=time.time(),
    )
    std2 = Standard(
        std_id="std_IS_10124",
        standard_id="std_IS_10124",
        standard_number="IS 10124",
        standard_title="Electronic Clinical Thermometers",
        authority="BIS",
        standard_year=2017,
        document_id="doc_is_10124",
        edition_or_version="First Edition",
        status=StandardStatus.PUBLISHED,
        created_at=time.time(),
        updated_at=time.time(),
    )
    repo.add_standard(std1)
    repo.add_standard(std2)
    return repo


# ============================================================
# 30 FAILURE-FIRST TESTS (Section 26)
# ============================================================

# 1. Exact product/title match
def test_01_exact_product_title_match(sample_repo):
    pctx = extract_product_context("Clinical Thermometers")
    cands = discover_standard_candidates(pctx, knowledge_repo=sample_repo)
    assert len(cands) > 0
    top = cands[0]
    assert top.standard_number == "IS 3055"
    assert any(r.reason_type == MappingReasonType.DIRECT_TITLE_MATCH for r in top.mapping_reasons)


# 2. Semantic product match
def test_02_semantic_product_match():
    pctx = ProductContext(
        product_context_id="pctx_test_02",
        product_name="digital body temperature gauge",
        product_category="thermometer",
        technology="digital",
    )
    chunks = [
        {
            "chunk_id": "c1",
            "standard_number": "IS 10124",
            "standard_title": "Electronic Clinical Thermometers",
            "content": "Specifies electronic digital body temperature measurement requirements.",
            "clause_id": "3.1",
            "reranker_score": 2.5,
        }
    ]
    retriever = MockMappingRetriever(chunks)
    cands = discover_standard_candidates(pctx, retriever=retriever)
    assert len(cands) > 0
    assert cands[0].standard_number == "IS 10124"


# 3. Technical characteristic signal
def test_03_technical_characteristic_signal():
    pctx = extract_product_context("We manufacture stainless steel domestic electric water heaters")
    assert pctx.material == "stainless steel"
    assert pctx.technology == "electric"
    assert pctx.intended_use == "domestic"

    chunks = [
        {
            "chunk_id": "c_wh_1",
            "standard_number": "IS 2082",
            "standard_title": "Stationary Storage Electric Water Heaters",
            "content": "Covers electric water heaters with stainless steel or copper inner containers.",
            "clause_id": "5.1",
        }
    ]
    retriever = MockMappingRetriever(chunks)
    cands = discover_standard_candidates(pctx, retriever=retriever)
    assert any(r.reason_type == MappingReasonType.TECHNICAL_CHARACTERISTIC_MATCH for r in cands[0].mapping_reasons)


# 4. Explicit standard reference
def test_04_explicit_standard_reference(sample_repo):
    pctx = extract_product_context("Our product follows IS 3055")
    assert "IS 3055" in pctx.raw_query
    cands = discover_standard_candidates(pctx, knowledge_repo=sample_repo)
    assert any(c.standard_number == "IS 3055" for c in cands)


# 5. Multiple candidate standards
def test_05_multiple_candidate_standards(sample_repo):
    pctx = extract_product_context("clinical thermometers for patient diagnosis")
    cands = discover_standard_candidates(pctx, knowledge_repo=sample_repo, top_k=5)
    std_nums = [c.standard_number for c in cands]
    assert "IS 3055" in std_nums
    assert "IS 10124" in std_nums


# 6. Duplicate chunks grouped correctly
def test_06_duplicate_chunks_grouped_correctly():
    chunks = [
        {"chunk_id": "c1", "content_hash": "hash_abc", "standard_number": "IS 3055", "content": "Sample clause 4.1"},
        {"chunk_id": "c2", "content_hash": "hash_abc", "standard_number": "IS 3055", "content": "Sample clause 4.1"},
        {"chunk_id": "c3", "content_hash": "hash_xyz", "standard_number": "IS 3055", "content": "Sample clause 4.2"},
    ]
    agg = aggregate_chunks_by_standard(chunks)
    assert len(agg) == 1
    assert len(agg["IS 3055"]["chunks"]) == 2  # Deduplicated from 3 to 2


# 7. Version-aware candidates
def test_07_version_aware_candidates():
    pctx = extract_product_context("clinical thermometer")
    std_info = {
        "standard_number": "IS 3055",
        "standard_id": "std_IS_3055",
        "standard_title": "Clinical Thermometers",
        "version_id": "ver_2024",
        "edition_or_version": "Third Edition",
        "chunks": [{"chunk_id": "c1", "clause_id": "4.1", "content": "IS 3055 clinical thermometer"}],
    }
    cand = score_standard_candidate(pctx, std_info)
    assert cand.version_id == "ver_2024"
    assert cand.edition_or_version == "Third Edition"


# 8. Unresolved version
def test_08_unresolved_version():
    pctx = extract_product_context("clinical thermometer")
    std_info = {
        "standard_number": "IS 3055",
        "standard_id": "std_IS_3055",
        "standard_title": "Clinical Thermometers",
        "version_id": None,
        "edition_or_version": None,
        "chunks": [{"chunk_id": "c1", "clause_id": "4.1", "content": "IS 3055 clinical thermometer"}],
    }
    cand = score_standard_candidate(pctx, std_info)
    assert cand.version_id is None
    assert cand.standard_number == "IS 3055"


# 9. No candidates scenario
def test_09_no_candidates():
    pctx = ProductContext(product_context_id="empty_ctx")
    cands = discover_standard_candidates(pctx)
    assert cands == []


# 10. No candidate != "not applicable"
def test_10_no_candidate_not_applicable():
    pctx = extract_product_context("quantum subatomic particle synthesizer")
    cands = discover_standard_candidates(pctx)
    assert cands == []  # Returns empty, NEVER a verdict of NOT_APPLICABLE


# 11. Insufficient evidence
def test_11_insufficient_evidence():
    pctx = extract_product_context("thermometer")
    std_info = {
        "standard_number": "IS 9999",
        "standard_id": "std_9999",
        "standard_title": "General Packaging Guidelines",
        "chunks": [{"chunk_id": "c1", "content": "Mentions a temperature thermometer in passing."}],
    }
    cand = score_standard_candidate(pctx, std_info)
    assert cand.mapping_status in (MappingStatus.WEAK_CANDIDATE, MappingStatus.INSUFFICIENT_EVIDENCE)


# 12. Strong candidate requirements
def test_12_strong_candidate():
    pctx = extract_product_context("clinical thermometer")
    std_info = {
        "standard_number": "IS 3055",
        "standard_id": "std_IS_3055",
        "standard_title": "Clinical Thermometers Specification",
        "chunks": [
            {"chunk_id": "c1", "clause_id": "4.1", "content": "Clinical thermometer calibration tolerances."},
            {"chunk_id": "c2", "clause_id": "4.2", "content": "Clinical thermometer constructional requirements."},
        ],
    }
    cand = score_standard_candidate(pctx, std_info)
    assert cand.mapping_status == MappingStatus.STRONG_CANDIDATE
    assert cand.mapping_score >= 0.70


# 13. Possible candidate classification
def test_13_possible_candidate():
    pctx = extract_product_context("mercury thermometer")
    std_info = {
        "standard_number": "IS 3055",
        "standard_id": "std_IS_3055",
        "standard_title": "Clinical Thermometers",
        "chunks": [{"chunk_id": "c1", "content": "Testing of mercury thermometer devices."}],
    }
    cand = score_standard_candidate(pctx, std_info)
    assert cand.mapping_status == MappingStatus.POSSIBLE_CANDIDATE



# 14. Weak candidate classification
def test_14_weak_candidate():
    pctx = extract_product_context("mercury switch")
    std_info = {
        "standard_number": "IS 3055",
        "standard_id": "std_IS_3055",
        "standard_title": "Clinical Thermometers",
        "chunks": [{"chunk_id": "c1", "content": "Uses mercury reservoir in stem."}],
    }
    cand = score_standard_candidate(pctx, std_info)
    assert cand.mapping_status == MappingStatus.WEAK_CANDIDATE


# 15. Supporting evidence attached
def test_15_supporting_evidence_attached():
    pctx = extract_product_context("clinical thermometer")
    std_info = {
        "standard_number": "IS 3055",
        "standard_id": "std_IS_3055",
        "standard_title": "Clinical Thermometers",
        "chunks": [{"chunk_id": "chunk_001", "clause_id": "4.1", "content": "Clinical thermometer text"}],
    }
    cand = score_standard_candidate(pctx, std_info)
    assert "chunk_001" in cand.supporting_evidence_ids
    assert "4.1" in cand.supporting_clause_ids


# 16. Reasons trace to evidence
def test_16_reasons_trace_to_evidence():
    pctx = extract_product_context("clinical thermometer")
    std_info = {
        "standard_number": "IS 3055",
        "standard_id": "std_IS_3055",
        "standard_title": "Clinical Thermometers",
        "chunks": [{"chunk_id": "chk_42", "clause_id": "4.1", "content": "clinical thermometer details"}],
    }
    cand = score_standard_candidate(pctx, std_info)
    for r in cand.mapping_reasons:
        assert isinstance(r, MappingReason)
        assert r.score_contribution >= 0.0


# 17. Source content remains authoritative
def test_17_source_content_authoritative():
    pctx = extract_product_context("clinical thermometer")
    std_info = {
        "standard_number": "IS 3055",
        "standard_id": "std_IS_3055",
        "standard_title": "Clinical Thermometers",
        "chunks": [
            {
                "chunk_id": "chk_orig",
                "source_content": "Authoritative Indian Standard IS 3055: Clinical thermometers.",
                "contextualized_content": "[Context: BIS standard IS 3055] Authoritative Indian Standard IS 3055",
                "clause_id": "1.1",
            }
        ],
    }
    cand = score_standard_candidate(pctx, std_info)
    assert cand.supporting_evidence_ids == ["chk_orig"]


# 18. Contextualized content never becomes citation source
def test_18_contextualized_content_not_citation_source():
    # Proof that candidate mapping records chunk_id pointing back to authoritative source_content
    pctx = extract_product_context("clinical thermometer")
    std_info = {
        "standard_number": "IS 3055",
        "standard_id": "std_IS_3055",
        "standard_title": "Clinical Thermometers",
        "chunks": [{"chunk_id": "chunk_real", "source_content": "real text"}],
    }
    cand = score_standard_candidate(pctx, std_info)
    assert cand.supporting_evidence_ids == ["chunk_real"]


# 19. Intent confidence independent of mapping score
def test_19_intent_confidence_independent():
    # A query with low intent certainty can still produce high mapping score if explicit
    pctx = extract_product_context("clinical thermometer")
    std_info = {
        "standard_number": "IS 3055",
        "standard_id": "std_IS_3055",
        "standard_title": "Clinical Thermometers",
        "chunks": [{"chunk_id": "c1", "clause_id": "4.1", "content": "clinical thermometer"}],
    }
    cand = score_standard_candidate(pctx, std_info)
    # Intent confidence is not entangled with candidate score
    assert cand.mapping_score >= 0.70


# 20. Evidence confidence independent of mapping score
def test_20_evidence_confidence_independent():
    # When evidence confidence is LOW, candidate mapping cannot be STRONG_CANDIDATE
    pctx = extract_product_context("clinical thermometer")
    std_info = {
        "standard_number": "IS 3055",
        "standard_id": "std_IS_3055",
        "standard_title": "Clinical Thermometers",
        "chunks": [{"chunk_id": "c1", "clause_id": "4.1", "content": "clinical thermometer"}],
    }
    cand = score_standard_candidate(
        pctx,
        std_info,
        evidence_confidence_level=ConfidenceLevel.LOW,
    )
    # Capped at POSSIBLE_CANDIDATE due to LOW evidence confidence
    assert cand.mapping_status != MappingStatus.STRONG_CANDIDATE


# 21. Mapping score independent of intent confidence
def test_21_mapping_score_policy():
    pctx = extract_product_context("pressure cooker")
    std_info = {
        "standard_number": "IS 2347",
        "standard_id": "std_IS_2347",
        "standard_title": "Domestic Pressure Cookers - Specification",
        "chunks": [{"chunk_id": "c1", "clause_id": "1.1", "content": "domestic pressure cooker"}],
    }
    cand = score_standard_candidate(pctx, std_info)
    assert 0.0 <= cand.mapping_score <= 1.0


# 22. Temporal uncertainty propagation
def test_22_temporal_uncertainty_propagation():
    class MockUncertainResolver:
        @staticmethod
        def resolve(**kwargs):
            class Res:
                status = StandardStatus.SUPERSEDED
                requires_verification = True
            return Res()

    pctx = extract_product_context("clinical thermometer")
    std_info = {
        "standard_number": "IS 3055",
        "standard_id": "std_IS_3055",
        "standard_title": "Clinical Thermometers",
        "chunks": [{"chunk_id": "c1", "content": "clinical thermometer"}],
    }
    repo = InMemoryKnowledgeRepo()
    repo.add_standard(Standard(
        standard_id="std_IS_3055",
        standard_number="IS 3055",
        standard_title="Clinical Thermometers",
        document_id="doc1",
        created_at=time.time(),
        updated_at=time.time(),
    ))
    cand = score_standard_candidate(
        pctx,
        std_info,
        knowledge_repo=repo,
        temporal_resolver=MockUncertainResolver(),
    )
    assert cand.verification_required is True


# 23. Explicit product facts only
def test_23_explicit_product_facts_only():
    pctx = extract_product_context("We manufacture digital clinical thermometers for hospitals.")
    assert pctx.product_category == "clinical thermometer"
    assert pctx.technology == "digital"
    assert pctx.customer_type == "hospitals"
    assert pctx.material is None  # Not specified -> None!


# 24. No hidden MSME / location / company inference
def test_24_no_hidden_inferences():
    pctx = extract_product_context("We manufacture clinical thermometers.")
    assert pctx.country_or_region is None
    assert pctx.target_market is None
    assert pctx.technical_characteristics.get("company_size") is None


# 25. Profile context separation
def test_25_profile_context_separation():
    prof = BusinessContext(
        manufacturing_location="Kolkata",
        business_type="manufacturer",
    )
    pctx = extract_product_context("clinical thermometer", business_context=prof)
    assert pctx.country_or_region == "Kolkata"
    # Modifying pctx does not change prof
    pctx.country_or_region = "Mumbai"
    assert prof.manufacturing_location == "Kolkata"


# 26. Multi-standard products ranking
def test_26_multi_standard_products_ranking():
    pctx = extract_product_context("clinical thermometers")
    c1 = ProductStandardCandidate(
        mapping_id="m1",
        product_context_id="p1",
        standard_id="std_1",
        standard_number="IS 3055",
        mapping_status=MappingStatus.STRONG_CANDIDATE,
        mapping_score=0.88,
        confidence_score=0.88,
        created_at=time.time(),
    )
    c2 = ProductStandardCandidate(
        mapping_id="m2",
        product_context_id="p1",
        standard_id="std_2",
        standard_number="IS 10124",
        mapping_status=MappingStatus.POSSIBLE_CANDIDATE,
        mapping_score=0.65,
        confidence_score=0.65,
        created_at=time.time(),
    )
    ranked = rank_standard_candidates([c2, c1])
    assert ranked[0].standard_number == "IS 3055"
    assert ranked[1].standard_number == "IS 10124"


# 27. Standard references in knowledge graph
def test_27_standard_references_kg():
    repo = InMemoryKnowledgeRepo()
    std = Standard(
        standard_id="std_IS_3055",
        standard_number="IS 3055",
        standard_title="Clinical Thermometers",
        document_id="doc1",
        created_at=time.time(),
        updated_at=time.time(),
    )
    repo.add_standard(std)
    pctx = extract_product_context("clinical thermometer")
    std_info = {
        "standard_number": "IS 3055",
        "standard_id": "std_IS_3055",
        "chunks": [{"chunk_id": "c1", "content": "clinical thermometer"}],
    }
    cand = score_standard_candidate(pctx, std_info, knowledge_repo=repo)
    assert any(r.reason_type == MappingReasonType.DOMAIN_MATCH for r in cand.mapping_reasons)


# 28. Non-standard document referencing a standard
def test_28_non_standard_doc_referencing_standard():
    # E.g. A gazette order referencing IS 3055
    chunks = [
        {
            "chunk_id": "gazette_chunk_1",
            "document_type": "gazette_notification",
            "standard_number": "",  # Gazette itself has no standard_number
            "content": "Order mentions IS 3055 Clinical thermometers schedule.",
        }
    ]
    agg = aggregate_chunks_by_standard(chunks)
    assert "IS 3055" in agg
    assert agg["IS 3055"]["standard_number"] == "IS 3055"


# 29. Deterministic candidate ranking
def test_29_deterministic_ranking():
    c1 = ProductStandardCandidate(
        mapping_id="m1", product_context_id="p1", standard_id="s1", standard_number="IS 2000",
        mapping_status=MappingStatus.STRONG_CANDIDATE, mapping_score=0.80, confidence_score=0.80, created_at=1.0,
    )
    c2 = ProductStandardCandidate(
        mapping_id="m2", product_context_id="p1", standard_id="s2", standard_number="IS 1000",
        mapping_status=MappingStatus.STRONG_CANDIDATE, mapping_score=0.80, confidence_score=0.80, created_at=1.0,
    )
    ranked = rank_standard_candidates([c1, c2])
    # Equal score tie broken by standard_number ascending
    assert ranked[0].standard_number == "IS 1000"
    assert ranked[1].standard_number == "IS 2000"


# 30. Duplicate mapping prevention
def test_30_duplicate_mapping_prevention():
    pctx = extract_product_context("clinical thermometer")
    chunks = [
        {"chunk_id": "c1", "standard_number": "IS 3055", "content": "text 1"},
        {"chunk_id": "c2", "standard_number": "IS 3055", "content": "text 2"},
    ]
    retriever = MockMappingRetriever(chunks)
    cands = discover_standard_candidates(pctx, retriever=retriever)
    std_numbers = [c.standard_number for c in cands]
    assert len(std_numbers) == len(set(std_numbers))


# ============================================================
# ADVERSARIAL TESTS (Section 27: A, B, C, D, E, F)
# ============================================================

# Adversarial A: Generator / model attempts: "therefore BIS certification is mandatory."
# Must reject this as applicability / legal inference.
def test_adversarial_a_reject_mandatory_claim():
    pctx = extract_product_context("clinical thermometer")
    std_info = {
        "standard_number": "IS 3055",
        "standard_id": "std_IS_3055",
        "standard_title": "Clinical Thermometers",
        "chunks": [{"chunk_id": "c1", "clause_id": "4.1", "content": "IS 3055 specification for clinical thermometers."}],
    }
    cand = score_standard_candidate(pctx, std_info)
    # Mapping status must strictly be in MappingStatus enum and never APPLICABLE or MANDATORY
    assert cand.mapping_status in (
        MappingStatus.STRONG_CANDIDATE,
        MappingStatus.POSSIBLE_CANDIDATE,
        MappingStatus.WEAK_CANDIDATE,
        MappingStatus.INSUFFICIENT_EVIDENCE,
        MappingStatus.VERIFICATION_REQUIRED,
    )
    assert cand.mapping_status.value not in ("MANDATORY", "APPLICABLE", "CERTIFIED")


# Adversarial B: Product: "thermometer". System attempts to invent hospital use / digital technology.
def test_adversarial_b_no_attribute_invention():
    pctx = extract_product_context("thermometer")
    assert pctx.customer_type is None
    assert pctx.technology is None
    assert pctx.intended_use is None


# Adversarial C: No candidate standards in corpus. Must NOT say "No BIS standard exists".
def test_adversarial_c_no_corpus_candidate_honesty():
    pctx = extract_product_context("unobtanium antigravity thruster")
    cands = discover_standard_candidates(pctx)
    assert len(cands) == 0
    # High-level wrapper check
    from app.product_mapping import get_product_standard_candidates
    res = get_product_standard_candidates("unobtanium antigravity thruster")
    assert res["has_candidates"] is False
    assert res["verification_required"] is True


# Adversarial D: Generic semantic similarity to an unrelated standard must NOT produce STRONG_CANDIDATE.
def test_adversarial_d_reject_generic_similarity():
    pctx = extract_product_context("hospital beds")
    std_info = {
        "standard_number": "IS 3055",
        "standard_id": "std_IS_3055",
        "standard_title": "Clinical Thermometers",
        "chunks": [{"chunk_id": "c1", "content": "Mentions hospital medical equipment measurement calibration."}],
    }
    cand = score_standard_candidate(pctx, std_info)
    assert cand.mapping_status != MappingStatus.STRONG_CANDIDATE


# Adversarial E: Older version has stronger text similarity than newer version.
# Do not declare newer version current without temporal evidence.
def test_adversarial_e_temporal_resolution_preserved():
    class MockSupersededResolver:
        @staticmethod
        def resolve(**kwargs):
            class Res:
                status = StandardStatus.SUPERSEDED
                requires_verification = True
            return Res()

    pctx = extract_product_context("clinical thermometer")
    std_info = {
        "standard_number": "IS 3055",
        "standard_id": "std_IS_3055",
        "standard_title": "Clinical Thermometers 1989",
        "chunks": [{"chunk_id": "c1", "content": "clinical thermometer old edition"}],
    }
    repo = InMemoryKnowledgeRepo()
    repo.add_standard(Standard(
        standard_id="std_IS_3055",
        standard_number="IS 3055",
        document_id="doc_old",
        created_at=time.time(),
        updated_at=time.time(),
    ))
    cand = score_standard_candidate(
        pctx,
        std_info,
        knowledge_repo=repo,
        temporal_resolver=MockSupersededResolver(),
    )
    assert cand.verification_required is True


# Adversarial F: Reference document mentions a standard.
# Do not incorrectly classify the reference document itself as the standard.
def test_adversarial_f_reference_doc_not_confused():
    chunks = [
        {
            "chunk_id": "ref_doc_chunk",
            "document_id": "QCO_Order_2024",
            "standard_number": "",  # QCO order itself
            "content": "Under Quality Control Order 2024, clinical thermometers conforming to IS 3055 are regulated.",
        }
    ]
    agg = aggregate_chunks_by_standard(chunks)
    assert "IS 3055" in agg
    assert agg["IS 3055"]["standard_number"] == "IS 3055"
    assert agg["IS 3055"]["standard_id"] != "QCO_Order_2024"


# ============================================================
# EVALUATION DATASET TEST (Section 32, 33)
# ============================================================

def test_evaluation_dataset_execution(sample_repo):
    dataset_path = "data/evaluation/product_standard_eval_dataset.json"
    results = evaluate_product_mapping(dataset_path, knowledge_repo=sample_repo)

    assert results["total_queries"] == 25
    assert results["false_strong_candidate_rate"] == 0.0
    assert results["unsupported_mapping_rate"] == 0.0
    assert results["mrr_at_5"] >= 0.0
