"""
tests/test_e2e_prompt10.py

Section 24: End-to-End Live Evaluation Queries A, B, C, D, E
for Phase 10: Query Intelligence & Business Context Foundation.
"""

import os
os.environ.setdefault("USE_TF", "0")

import pytest
from app.confidence.models import Decision, QueryState
from app.evidence.models import EvidenceItem
from app.query_intelligence.models import (
    QueryIntentType,
    QueryLifecycleState,
    RetrievalStrategy,
)
from app.rag.pipeline import RAGPipeline


class MockRetriever:
    """Deterministic mock retriever providing synthetic evidence for live pipeline tests."""
    def __init__(self, items):
        self.items = items

    def retrieve(self, query, top_k=5, dense_k=5, bm25_k=5, deduplicate=False):
        return self.items[:top_k]


class MockReranker:
    """Pass-through mock reranker."""
    def rerank(self, query, results, top_k=5):
        return results[:top_k]


@pytest.fixture
def mock_pipeline():
    evidence = [
        EvidenceItem(
            chunk_id="chunk_is_302_2_3_base",
            content="IS 302-2-3 (2000): Safety of household and similar electrical appliances - Part 2-3: Particular requirements for electric irons.",
            document_id="doc_is_302_2_3",
            standard_number="IS 302-2-3",
            standard_year=2000,
            reranker_score=2.8,
            authority="BIS",
            document_type="indian_standard",
            provenance_completeness=1.0,
        ),
        EvidenceItem(
            chunk_id="chunk_is_10124_cls_4_1",
            content="IS 10124 Clause 4.1 specifies construction and mechanical strength tests for clinical thermometers.",
            document_id="doc_is_10124",
            standard_number="IS 10124",
            clause_id="4.1",
            standard_year=2017,
            reranker_score=3.1,
            authority="BIS",
            document_type="indian_standard",
            provenance_completeness=1.0,
        ),
    ]
    retriever = MockRetriever(evidence)
    reranker = MockReranker()
    return RAGPipeline(retriever=retriever, reranker=reranker, generator=None)


def test_query_a_exact_standard_lookup(mock_pipeline):
    """
    Query A: Exact standard lookup ("What is IS 302-2-3?")
    Must classify as STANDARD_LOOKUP with high confidence, select STANDARD_METADATA strategy,
    and return structured response containing query_context.
    """
    q = "What is IS 302-2-3?"
    res = mock_pipeline.query(q)

    assert res["intent"] == QueryIntentType.STANDARD_LOOKUP.value
    assert res["intent_confidence"] >= 0.85
    assert res["query_context"]["retrieval_strategy"] == RetrievalStrategy.STANDARD_METADATA.value
    assert res["query_context"]["query_state"] in (
        QueryLifecycleState.NORMAL.value,
        QueryLifecycleState.IDENTIFIER_SPECIFIC.value,
    )
    assert res["retrieved_chunks"] > 0
    assert "query_context" in res


def test_query_b_clause_lookup(mock_pipeline):
    """
    Query B: Clause lookup ("Clause 4.1 of IS 10124")
    Must classify as CLAUSE_LOOKUP, select IDENTIFIER_HEAVY strategy,
    and preserve clause entity "4.1".
    """
    q = "Clause 4.1 of IS 10124"
    res = mock_pipeline.query(q)

    assert res["intent"] == QueryIntentType.CLAUSE_LOOKUP.value
    assert res["intent_confidence"] >= 0.90
    assert res["query_context"]["retrieval_strategy"] == RetrievalStrategy.IDENTIFIER_HEAVY.value
    assert res["query_context"]["entities"]["clause_id"] == "4.1"
    assert res["query_context"]["entities"]["standard_number"] == "IS 10124"


def test_query_c_currentness_query(mock_pipeline):
    """
    Query C: Currentness query ("Is IS 302-2-3 current?")
    Must classify as CURRENTNESS_QUERY, select TEMPORAL_AWARE strategy,
    and execute temporal resolution.
    """
    q = "Is IS 302-2-3 current?"
    res = mock_pipeline.query(q)

    assert res["intent"] == QueryIntentType.CURRENTNESS_QUERY.value
    assert res["query_context"]["retrieval_strategy"] == RetrievalStrategy.TEMPORAL_AWARE.value
    assert "temporal_status" in res
    assert "temporal_resolution" in res


def test_query_d_applicability_missing_context(mock_pipeline):
    """
    Query D: Applicability query missing business context ("Does BIS apply to my product?")
    Must classify as APPLICABILITY_QUERY, transition to MISSING_REQUIRED_CONTEXT,
    and trigger Hard Trigger I forcing VERIFICATION_REQUIRED with score <= 0.30.
    """
    q = "Does BIS apply to my product?"
    res = mock_pipeline.query(q)

    assert res["intent"] == QueryIntentType.APPLICABILITY_QUERY.value
    assert res["query_context"]["query_state"] == QueryLifecycleState.MISSING_REQUIRED_CONTEXT.value
    assert res["decision"] == Decision.VERIFICATION_REQUIRED.value
    assert res["verification_required"] is True
    assert res["confidence_score"] <= 0.30
    assert "missing required business context" in res["verification_reason"].lower()


def test_query_e_ambiguous_single_word(mock_pipeline):
    """
    Query E: Ambiguous single-word query ("lity")
    Must classify as AMBIGUOUS_QUERY, mark is_ambiguous=True,
    and force VERIFICATION_REQUIRED with score <= 0.35.
    """
    q = "lity"
    res = mock_pipeline.query(q)

    assert res["intent"] == QueryIntentType.AMBIGUOUS_QUERY.value
    assert res["query_context"]["intent"]["is_ambiguous"] is True
    assert res["decision"] == Decision.VERIFICATION_REQUIRED.value
    assert res["verification_required"] is True
    assert res["confidence_score"] <= 0.35


def test_query_f_applicability_with_explicit_context(mock_pipeline):
    """
    Query F: Applicability query with explicit business context.
    Must extract product category and location, avoid MISSING_REQUIRED_CONTEXT state,
    and preserve explicit attributes.
    """
    q = "We manufacture clinical thermometers in Kolkata. Does BIS apply?"
    res = mock_pipeline.query(q)

    assert res["intent"] == QueryIntentType.APPLICABILITY_QUERY.value
    b_ctx = res["query_context"]["business_context"]
    assert b_ctx["product_category"] == "clinical thermometers"
    assert b_ctx["manufacturing_activity"] == "manufacturing"
    assert b_ctx["manufacturing_location"] == "Kolkata"
    assert b_ctx["has_explicit_product"] is True
    assert res["query_context"]["query_state"] != QueryLifecycleState.MISSING_REQUIRED_CONTEXT.value
