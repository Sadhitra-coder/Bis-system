"""
tests/test_e2e_prompt11.py

End-to-End Integration and Verification for Phase 11:
Product-to-BIS Standard Mapping in the RAG Pipeline.

Verifies:
  1. STANDARD_DISCOVERY query flow with candidate standards discovery
  2. APPLICABILITY_QUERY preparation flow stopping before legal applicability verdict
  3. Exact product/title match candidate ranking
  4. Semantic product match candidate ranking
  5. No-candidate scenario (returns empty candidate list with VERIFICATION_REQUIRED)
  6. Multi-standard candidate ranking
  7. API response serialization for product_context and candidate_standards
"""

import os
os.environ.setdefault("USE_TF", "0")

import pytest
from app.confidence.models import Decision, QueryState
from app.evidence.models import EvidenceItem
from app.product_mapping.models import (
    MappingStatus,
    ProductContext,
    ProductStandardCandidate,
)
from app.query_intelligence.models import (
    BusinessContext,
    QueryIntentType,
    RetrievalStrategy,
)
from app.rag.pipeline import RAGPipeline


class MockE2ERetriever:
    """Mock retriever returning realistic standard chunks."""
    def __init__(self, items: list):
        self.items = items

    def retrieve(self, query: str, top_k: int = 5, **kwargs):
        # Return relevant items
        q_lower = query.lower()
        matched = [
            it for it in self.items
            if any(term in (it.get("content") or "").lower() for term in q_lower.split())
        ]
        return (matched or self.items)[:top_k]


class MockE2EReranker:
    """Pass-through reranker."""
    def rerank(self, query: str, results: list, top_k: int = 5):
        return results[:top_k]


@pytest.fixture
def mock_pipeline_p11():
    evidence = [
        {
            "chunk_id": "chunk_is_3055_title",
            "content": "IS 3055 (Part 1): 2024 Clinical Thermometers - Part 1 : Solid-Stem Type. Bureau of Indian Standards.",
            "document_id": "doc_is_3055",
            "standard_number": "IS 3055",
            "standard_title": "Clinical Thermometers - Part 1 : Solid-Stem Type",
            "clause_id": "Preamble",
            "standard_year": 2024,
            "reranker_score": 3.2,
            "authority": "BIS",
            "document_type": "indian_standard",
            "provenance_completeness": 1.0,
        },
        {
            "chunk_id": "chunk_is_3055_cls_4_1",
            "content": "IS 3055 Clause 4.1 specifies calibration tolerances and accuracy limits for clinical thermometers.",
            "document_id": "doc_is_3055",
            "standard_number": "IS 3055",
            "standard_title": "Clinical Thermometers - Part 1 : Solid-Stem Type",
            "clause_id": "4.1",
            "standard_year": 2024,
            "reranker_score": 3.0,
            "authority": "BIS",
            "document_type": "indian_standard",
            "provenance_completeness": 1.0,
        },
        {
            "chunk_id": "chunk_is_10124_base",
            "content": "IS 10124 : 2017 Electronic Clinical Thermometers. Covers digital temperature indicators.",
            "document_id": "doc_is_10124",
            "standard_number": "IS 10124",
            "standard_title": "Electronic Clinical Thermometers",
            "clause_id": "Scope",
            "standard_year": 2017,
            "reranker_score": 2.8,
            "authority": "BIS",
            "document_type": "indian_standard",
            "provenance_completeness": 1.0,
        },
    ]
    retriever = MockE2ERetriever(evidence)
    reranker = MockE2EReranker()
    return RAGPipeline(retriever=retriever, reranker=reranker, generator=None)


def test_e2e_standard_discovery_query(mock_pipeline_p11):
    """
    Live Query 1: STANDARD_DISCOVERY
    "Which BIS standard covers digital clinical thermometers?"
    Must identify candidate standards and return product_context and candidate_standards.
    """
    q = "Which BIS standard covers digital clinical thermometers?"
    res = mock_pipeline_p11.query(q)

    assert res["intent"] == QueryIntentType.STANDARD_DISCOVERY.value
    assert res["product_context"] is not None
    assert res["product_context"]["product_category"] == "clinical thermometer"

    # Verify candidate standards are returned
    assert len(res["candidate_standards"]) > 0
    top_candidate = res["candidate_standards"][0]
    assert top_candidate["standard_number"] in ("IS 3055", "IS 10124")
    assert top_candidate["mapping_status"] in (MappingStatus.STRONG_CANDIDATE.value, MappingStatus.POSSIBLE_CANDIDATE.value)
    assert top_candidate["mapping_score"] > 0.5


def test_e2e_applicability_query_preparation(mock_pipeline_p11):
    """
    Live Query 2: APPLICABILITY_QUERY
    "Is IS 3055 applicable to our clinical thermometers?"
    Must prepare candidate standards, but stop before final legal applicability verdict.
    Must enforce verification_required = True and reject final mandatory claims.
    """
    q = "Is IS 3055 applicable to our clinical thermometers?"
    res = mock_pipeline_p11.query(q)

    assert res["intent"] == QueryIntentType.APPLICABILITY_QUERY.value
    assert res["product_context"] is not None
    assert len(res["candidate_standards"]) > 0

    # Scope Boundary: Never claim mandatory or certified!
    assert res["verification_required"] is True
    assert res["decision"] == Decision.VERIFICATION_REQUIRED.value
    assert "Legal applicability" in res["verification_reason"] or "verification" in res["verification_reason"].lower()
    assert "mandatory" not in res["answer"].lower() or "cannot be determined" in res["answer"].lower() or "require verification" in res["answer"].lower()


def test_e2e_no_candidate_scenario():
    """
    Live Query 5: No candidate scenario
    "Which BIS standard covers quantum hoverboards?"
    Must return empty candidate list, NOT claim 'No standard exists'.
    """
    empty_retriever = MockE2ERetriever([])
    pipe = RAGPipeline(retriever=empty_retriever, reranker=MockE2EReranker(), generator=None)

    q = "Which BIS standard covers quantum hoverboards?"
    res = pipe.query(q)

    assert res["intent"] == QueryIntentType.STANDARD_DISCOVERY.value
    assert res["verification_required"] is True
    assert "No candidate standards found" in res["verification_reason"]
    # Must NOT claim that no standard exists in reality
    assert "no bis standard exists" not in res["answer"].lower()
    assert "no bis standard applies" not in res["answer"].lower()


def test_e2e_multi_standard_candidate_ranking(mock_pipeline_p11):
    """
    Live Query 6: Multi-standard scenario
    "We manufacture clinical thermometers both mercury and digital."
    Must return multiple candidate standards in ranked order.
    """
    q = "We manufacture clinical thermometers both mercury and digital."
    res = mock_pipeline_p11.query(q)

    candidates = res["candidate_standards"]
    assert len(candidates) >= 2
    cand_std_nums = [c["standard_number"] for c in candidates]
    assert "IS 3055" in cand_std_nums
    assert "IS 10124" in cand_std_nums
