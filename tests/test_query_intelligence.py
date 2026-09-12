"""
tests/test_query_intelligence.py

Comprehensive test suite for Phase 10:
Query Intelligence, Intent Classification, Business Context Foundation,
Retrieval Strategy Selection, and Epistemic Confidence Decoupling.
"""

import pytest
from typing import List

from app.confidence.models import Decision, QueryState
from app.confidence.evaluator import EvidenceEvaluator
from app.evidence.models import EvidenceItem
from app.query_intelligence.models import (
    BusinessContext,
    IntentClassification,
    QueryContext,
    QueryIntentType,
    QueryLifecycleState,
    RetrievalStrategy,
)
from app.query_intelligence.business import (
    extract_business_context_from_query,
    merge_business_contexts,
)
from app.query_intelligence.classifier import (
    classify_intent,
    classify_intent_deterministic,
)
from app.query_intelligence.strategy import (
    determine_query_state,
    generate_query_variants,
    select_retrieval_strategy,
)
from app.query_intelligence import build_query_context
from app.rag.query import extract_query_entities, normalize_query


# ============================================================
# 1. INTENT CLASSIFICATION TESTS (All 14 Intents)
# ============================================================

def test_intent_standard_lookup():
    q = "What is IS 302-2-3?"
    entities = extract_query_entities(normalize_query(q))
    intent = classify_intent_deterministic(q, entities)
    assert intent.intent == QueryIntentType.STANDARD_LOOKUP
    assert intent.intent_confidence >= 0.85
    assert not intent.is_ambiguous


def test_intent_clause_lookup():
    q = "Show me Clause 4.1 of IS 10124"
    entities = extract_query_entities(normalize_query(q))
    intent = classify_intent_deterministic(q, entities)
    assert intent.intent == QueryIntentType.CLAUSE_LOOKUP
    assert intent.intent_confidence >= 0.90
    assert not intent.is_ambiguous


def test_intent_amendment_lookup():
    q = "What changes were introduced in Amendment 2 to IS 10124?"
    entities = extract_query_entities(normalize_query(q))
    intent = classify_intent_deterministic(q, entities)
    assert intent.intent == QueryIntentType.AMENDMENT_LOOKUP
    assert intent.intent_confidence >= 0.85
    assert not intent.is_ambiguous


def test_intent_version_lookup():
    q = "Provide details on the 2009 edition of IS 10124"
    entities = extract_query_entities(normalize_query(q))
    intent = classify_intent_deterministic(q, entities)
    assert intent.intent == QueryIntentType.VERSION_LOOKUP
    assert intent.intent_confidence >= 0.80
    assert not intent.is_ambiguous


def test_intent_currentness_query():
    q = "Is IS 302-2-3 currently in force or superseded?"
    entities = extract_query_entities(normalize_query(q))
    intent = classify_intent_deterministic(q, entities)
    assert intent.intent == QueryIntentType.CURRENTNESS_QUERY
    assert intent.intent_confidence >= 0.85


def test_intent_requirement_discovery():
    q = "What are the drop test requirements and tolerances for clinical thermometers?"
    entities = extract_query_entities(normalize_query(q))
    intent = classify_intent_deterministic(q, entities)
    assert intent.intent == QueryIntentType.REQUIREMENT_DISCOVERY
    assert intent.intent_confidence >= 0.70


def test_intent_standard_discovery():
    q = "Which Indian standard applies to electric irons?"
    entities = extract_query_entities(normalize_query(q))
    intent = classify_intent_deterministic(q, entities)
    assert intent.intent == QueryIntentType.STANDARD_DISCOVERY
    assert intent.intent_confidence >= 0.80


def test_intent_applicability_query():
    q = "Do we need BIS certification for imported electric kettles?"
    entities = extract_query_entities(normalize_query(q))
    intent = classify_intent_deterministic(q, entities)
    assert intent.intent == QueryIntentType.APPLICABILITY_QUERY
    assert intent.intent_confidence >= 0.80


def test_intent_document_requirement():
    q = "What technical documents and test reports are required for BIS license application?"
    entities = extract_query_entities(normalize_query(q))
    intent = classify_intent_deterministic(q, entities)
    assert intent.intent == QueryIntentType.DOCUMENT_REQUIREMENT_QUERY
    assert intent.intent_confidence >= 0.75


def test_intent_reference_lookup():
    q = "What other standards are referenced or cited by IS 10124?"
    entities = extract_query_entities(normalize_query(q))
    intent = classify_intent_deterministic(q, entities)
    assert intent.intent == QueryIntentType.REFERENCE_LOOKUP
    assert intent.intent_confidence >= 0.80


def test_intent_explanation_query():
    q = "Can you explain the rationale and meaning of clause 5 in IS 302-2-3?"
    entities = extract_query_entities(normalize_query(q))
    intent = classify_intent_deterministic(q, entities)
    assert intent.intent == QueryIntentType.EXPLANATION_QUERY
    assert intent.intent_confidence >= 0.75


def test_intent_comparison_query():
    q = "Compare the changes between IS 10124:2009 and IS 10124:2017"
    entities = extract_query_entities(normalize_query(q))
    intent = classify_intent_deterministic(q, entities)
    assert intent.intent == QueryIntentType.COMPARISON_QUERY
    assert intent.intent_confidence >= 0.80


def test_intent_general_information():
    q = "What is the role and organizational function of the Bureau of Indian Standards?"
    entities = extract_query_entities(normalize_query(q))
    intent = classify_intent_deterministic(q, entities)
    assert intent.intent == QueryIntentType.GENERAL_INFORMATION
    assert intent.intent_confidence >= 0.70


# ============================================================
# 2. AMBIGUITY HANDLING TESTS
# ============================================================

def test_intent_ambiguous_single_fragment():
    q = "lity"
    entities = extract_query_entities(normalize_query(q))
    intent = classify_intent_deterministic(q, entities)
    assert intent.intent == QueryIntentType.AMBIGUOUS_QUERY
    assert intent.is_ambiguous is True
    assert intent.intent_confidence <= 0.40


def test_intent_ambiguous_generic_keyword():
    q = "compliance"
    entities = extract_query_entities(normalize_query(q))
    intent = classify_intent_deterministic(q, entities)
    assert intent.intent == QueryIntentType.AMBIGUOUS_QUERY
    assert intent.is_ambiguous is True


def test_intent_ambiguous_short_whitespace():
    q = "standard?"
    entities = extract_query_entities(normalize_query(q))
    intent = classify_intent_deterministic(q, entities)
    assert intent.intent == QueryIntentType.AMBIGUOUS_QUERY
    assert intent.is_ambiguous is True


# ============================================================
# 3. BUSINESS CONTEXT EXTRACTION & ZERO-INFERENCE TESTS
# ============================================================

def test_business_context_explicit_extraction():
    q = "We manufacture clinical thermometers in Kolkata. Does BIS apply?"
    ctx = extract_business_context_from_query(q)
    assert ctx.manufacturing_activity == "manufacturing"
    assert ctx.manufacturing_location == "Kolkata"
    assert ctx.product_category == "clinical thermometers"
    assert ctx.has_explicit_product is True


def test_business_context_negative_no_unstated_inferences():
    """
    CRITICAL: Never guess or hallucinate unstated business facts
    (e.g., MSME status, factory area, turnover, employee count).
    """
    q = "We manufacture clinical thermometers in Kolkata."
    ctx = extract_business_context_from_query(q)
    assert ctx.company_size is None
    assert ctx.industry is None
    assert ctx.business_type is None
    assert ctx.target_market is None
    assert "msme" not in str(ctx.to_dict()).lower()
    assert "small" not in str(ctx.to_dict()).lower()


def test_business_context_merge():
    """
    Profile context is preserved; query-level explicit context non-destructively overlays.
    """
    profile = BusinessContext(
        industry="medical devices",
        business_type="manufacturer",
        manufacturing_location="Mumbai",
    )
    query_ctx = BusinessContext(
        product_name="Mercury Thermometer Deluxe",
        manufacturing_location="Kolkata",  # overrides Mumbai for this query
    )
    merged = merge_business_contexts(profile, query_ctx)
    assert merged.industry == "medical devices"
    assert merged.business_type == "manufacturer"
    assert merged.product_name == "Mercury Thermometer Deluxe"
    assert merged.manufacturing_location == "Kolkata"


# ============================================================
# 4. QUERY LIFECYCLE STATE & RETRIEVAL STRATEGY
# ============================================================

def test_lifecycle_missing_required_context_applicability():
    """
    Applicability query without product must transition to MISSING_REQUIRED_CONTEXT.
    """
    q = "Do I need BIS certification for my factory?"
    entities = extract_query_entities(normalize_query(q))
    intent = classify_intent_deterministic(q, entities)
    ctx = BusinessContext()  # empty
    state = determine_query_state(intent, entities, ctx)
    assert state == QueryLifecycleState.MISSING_REQUIRED_CONTEXT


def test_lifecycle_normal_applicability_with_product():
    """
    Applicability query with explicit product has normal/semantic lifecycle.
    """
    q = "Do I need BIS certification for clinical thermometers?"
    entities = extract_query_entities(normalize_query(q))
    intent = classify_intent_deterministic(q, entities)
    ctx = extract_business_context_from_query(q)
    state = determine_query_state(intent, entities, ctx)
    assert state != QueryLifecycleState.MISSING_REQUIRED_CONTEXT
    assert ctx.has_explicit_product is True


def test_retrieval_strategy_selection():
    # Identifier heavy for clause lookup
    cls_intent = IntentClassification(intent=QueryIntentType.CLAUSE_LOOKUP, intent_confidence=0.95)
    entities = extract_query_entities("clause 4.1 of is 10124")
    strat = select_retrieval_strategy(cls_intent, entities)
    assert strat == RetrievalStrategy.IDENTIFIER_HEAVY

    # Temporal aware for currentness
    curr_intent = IntentClassification(intent=QueryIntentType.CURRENTNESS_QUERY, intent_confidence=0.90)
    strat = select_retrieval_strategy(curr_intent, entities)
    assert strat == RetrievalStrategy.TEMPORAL_AWARE

    # Standard discovery
    disc_intent = IntentClassification(intent=QueryIntentType.STANDARD_DISCOVERY, intent_confidence=0.85)
    strat = select_retrieval_strategy(disc_intent, entities)
    assert strat == RetrievalStrategy.STANDARD_DISCOVERY


def test_generate_query_variants():
    q = "Clause 4.1 of IS 10124"
    entities = extract_query_entities(normalize_query(q))
    variants = generate_query_variants(q, QueryIntentType.CLAUSE_LOOKUP, entities)
    assert len(variants) >= 2
    assert any("IS 10124" in v for v in variants)


# ============================================================
# 5. EPISTEMIC CONFIDENCE DECOUPLING & HARD TRIGGERS
# ============================================================

def test_intent_confidence_decoupled_from_evidence_confidence():
    """
    High intent confidence (0.95) + zero evidence candidate must yield
    zero evidence confidence (0.0) and VERIFICATION_REQUIRED.
    """
    q = "Clause 99.9 of IS 99999"
    q_context = build_query_context(q)
    assert q_context.intent.intent == QueryIntentType.CLAUSE_LOOKUP
    assert q_context.intent.intent_confidence >= 0.85

    # Evaluate against zero evidence items
    result = EvidenceEvaluator.evaluate(
        query=q,
        evidence_items=[],
        query_context=q_context,
    )
    assert result.decision == Decision.VERIFICATION_REQUIRED
    assert result.score == 0.0
    assert result.verification_required is True
    assert result.query_state == QueryState.INSUFFICIENT_EVIDENCE


def test_evaluator_hard_trigger_i_missing_required_context():
    """
    Hard Trigger I: When lifecycle state is MISSING_REQUIRED_CONTEXT,
    evaluator must force VERIFICATION_REQUIRED with score <= 0.30.
    """
    q = "Is BIS certification mandatory for my goods?"
    q_context = build_query_context(q)
    assert q_context.query_state == QueryLifecycleState.MISSING_REQUIRED_CONTEXT

    # Even if mock evidence is present, Hard Trigger I must fire
    mock_evidence = [
        EvidenceItem(
            chunk_id="chunk_1",
            content="General BIS certification guidelines for Indian manufacturers.",
            document_id="doc_bis_guidelines",
            reranker_score=1.5,
        )
    ]

    result = EvidenceEvaluator.evaluate(
        query=q,
        evidence_items=mock_evidence,
        query_context=q_context,
    )
    assert result.decision == Decision.VERIFICATION_REQUIRED
    assert result.verification_required is True
    assert result.score <= 0.30
    assert result.query_state == QueryState.VERIFICATION_REQUIRED
    assert any("missing required business context" in r.lower() for r in result.reasons)


def test_evaluator_hard_trigger_d_ambiguous_query():
    """
    Hard Trigger D: When query is classified as AMBIGUOUS_QUERY,
    evaluator forces VERIFICATION_REQUIRED with score <= 0.35.
    """
    q = "lity"
    q_context = build_query_context(q)
    assert q_context.intent.is_ambiguous is True

    mock_evidence = [
        EvidenceItem(
            chunk_id="chunk_1",
            content="Quality requirements are defined across various standards.",
            document_id="doc_quality",
            reranker_score=2.0,
        )
    ]

    result = EvidenceEvaluator.evaluate(
        query=q,
        evidence_items=mock_evidence,
        query_context=q_context,
    )
    assert result.decision == Decision.VERIFICATION_REQUIRED
    assert result.verification_required is True
    assert result.score <= 0.35
    assert result.query_state == QueryState.AMBIGUOUS_QUERY
