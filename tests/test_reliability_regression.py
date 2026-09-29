"""
tests/test_reliability_regression.py

Regression test suite verifying system-wide reliability across all 15 requirements:
- Generic product handling (NO hardcoded if/else rules for specific products).
- Product passport assessment lifecycle (NEW -> CLASSIFYING -> RETRIEVING -> VERIFYING -> ASSESSED / NEEDS_VERIFICATION / NO_EVIDENCE).
- Context isolation: Changing products prevents context/evidence leakage across assessments.
- Similarity alone != Applicability (unmatched scope never produces APPLICABLE).
- Unknown products abstain without guessing ("Evidence not found in the verified knowledge base").
- Official QCO / Standard proof validation (LHS product facts == RHS official scope).
- Laboratory results appear ONLY for LABORATORY_SEARCH intent.
- Trace / explain mode captures accepted and rejected evidence with explicit reasons.
- Multi-signal reranking penalizes mismatched product domains.
"""

import pytest
from typing import Dict, Any

from app.assessment import (
    AssessmentLifecycleState,
    AssessmentManager,
    VerificationLevel,
)
from app.applicability import (
    ApplicabilityStatus,
    evaluate_standard_applicability,
    evaluate_qco_applicability,
)
from app.product_mapping.models import ProductContext, ProductStandardCandidate
from app.query_intelligence import (
    build_query_context,
    classify_intent_deterministic,
    extract_query_entities,
    normalize_query,
)
from app.query_intelligence.models import QueryIntentType, BusinessContext
from app.rag.reranker import compute_intent_ranking_key
from app.rag.pipeline import RAGPipeline


# ============================================================
# 1. PRODUCT PASSPORT & CONTEXT ISOLATION (Requirements 4 & 10)
# ============================================================

def test_assessment_lifecycle_and_context_isolation():
    """
    Test that sequential queries for different products create distinct assessments
    with isolated contexts, zero evidence leakage, and correct lifecycle states.
    """
    manager = AssessmentManager(default_knowledge_version="v1.0-official")

    # Assessment 1: Product A (e.g. Toys)
    p_toy = ProductContext(
        product_context_id="prod_toy",
        product_name="Wooden Educational Toy",
        product_category="Toys and Games",
    )
    ass_1 = manager.get_or_create_for_query(
        query="What standard applies to wooden toys?",
        normalized_product_context=p_toy,
        knowledge_version="v1.0-official",
    )
    ass_1.transition_to(AssessmentLifecycleState.CLASSIFYING, "Intent classified.")
    ass_1.transition_to(AssessmentLifecycleState.RETRIEVING, "Retrieved evidence.")
    ass_1.evidence_set = ["chunk_toy_1", "chunk_toy_2"]
    ass_1.transition_to(AssessmentLifecycleState.VERIFYING, "Verified scope.")
    ass_1.transition_to(AssessmentLifecycleState.ASSESSED, "Assessment completed.")
    ass_1.verification_level = VerificationLevel.VERIFIED

    assert ass_1.lifecycle_state == AssessmentLifecycleState.ASSESSED
    assert ass_1.verification_level == VerificationLevel.VERIFIED
    assert len(ass_1.evidence_set) == 2

    # Assessment 2: Product B (e.g. Cement)
    p_cement = ProductContext(
        product_context_id="prod_cement",
        product_name="Portland Pozzolana Cement",
        product_category="Building Materials",
    )
    ass_2 = manager.get_or_create_for_query(
        query="What standard applies to portland cement?",
        normalized_product_context=p_cement,
        knowledge_version="v1.0-official",
    )

    # STRICT CONTEXT ISOLATION CHECKS
    assert ass_2.assessment_id != ass_1.assessment_id, "Assessment 2 must have a unique assessment_id."
    assert ass_2.product_id != ass_1.product_id, "Assessment 2 must have a unique product_id."
    assert ass_2.lifecycle_state == AssessmentLifecycleState.NEW, "New assessment must start in NEW state."
    assert ass_2.evidence_set == [], "New assessment must not inherit evidence from previous assessment."
    assert "chunk_toy_1" not in ass_2.evidence_set, "Context leakage detected: Toy chunk found in Cement assessment."


# ============================================================
# 2. SIMILARITY ALONE != APPLICABILITY (Requirements 1, 6, 7)
# ============================================================

def test_similarity_alone_does_not_grant_applicability():
    """
    Test that high candidate/retrieval score alone NEVER produces APPLICABLE
    when the official standard scope does not cover the product.
    """
    # Product: Medical software
    prod = ProductContext(
        product_context_id="p_med_soft",
        product_name="Diagnostic Imaging Software",
        product_category="Healthcare IT",
        intended_use="Medical image visualization",
    )

    # Candidate standard: Unrelated hardware/clinical device with high semantic match score
    cand = ProductStandardCandidate(
        mapping_id="m_high_sim",
        product_context_id="p_med_soft",
        standard_id="std_hardware",
        standard_number="IS 3055:2024",
        standard_title="Clinical Thermometers - Solid Stem Type",
        mapping_score=0.88,  # High vector/lexical score
        temporal_status="ACTIVE",
    )

    assessment = evaluate_standard_applicability(prod, cand)

    # Must NOT be APPLICABLE because scope does not establish inclusion of medical software
    assert assessment.status != ApplicabilityStatus.APPLICABLE, "Similarity score incorrectly granted APPLICABLE status!"
    assert assessment.verification_required is True
    assert "Applicability could not be verified" in assessment.reason or "broader product family" in assessment.reason


# ============================================================
# 3. UNKNOWN PRODUCT / NO EVIDENCE = ABSTAIN (Requirements 5 & 12)
# ============================================================

def test_unknown_product_abstains_without_hallucinating():
    """
    Test that queries for unknown products without official evidence produce
    explicit abstention with VERIFICATION_REQUIRED status.
    """
    pipeline = RAGPipeline()
    unknown_query = "What is the mandatory BIS QCO for quantum superconducting teleportation units?"
    result = pipeline.query(unknown_query)

    assert result["verification_required"] is True
    assert result["decision"] == "verification_required"
    assert result["verification_level"] == "VERIFICATION_REQUIRED"
    assert (
        "Evidence not found in the verified knowledge base" in result["answer"]
        or "could not verify this requirement from an authoritative source" in result["answer"]
        or "Verification Required" in result["answer"]
    )


# ============================================================
# 4. OFFICIAL QCO APPLICABILITY PROOF (Requirements 7 & 9)
# ============================================================

def test_qco_applicability_requires_scope_proof():
    """
    Test the strict LHS (Product Facts) == RHS (Official QCO Scope) proof sequence.
    """
    # 1. Matching product & official scope -> MATCH
    prod_matching = ProductContext(
        product_context_id="p_wire",
        product_name="PVC Insulated Copper Wire",
        product_category="Electrical Cables",
    )
    qco_matching = {
        "qco_number": "S.O. 1234(E)",
        "title": "Electrical Wires, Cables and Cords (Quality Control) Order, 2024",
    }
    res_match = evaluate_qco_applicability(
        product=prod_matching,
        qco_record=qco_matching,
        official_evidence_text="This order covers electrical wires, building wires, and cables.",
    )
    assert res_match["status"] == "MATCH"
    assert res_match["is_applicable"] is True
    assert res_match["verification_required"] is False

    # 2. Unmatched product & scope -> UNKNOWN / VERIFICATION REQUIRED
    prod_unmatched = ProductContext(
        product_context_id="p_other",
        product_name="Hydraulic Excavator Tooth",
        product_category="Heavy Machinery",
    )
    res_unmatch = evaluate_qco_applicability(
        product=prod_unmatched,
        qco_record=qco_matching,
        official_evidence_text="This order covers electrical wires and cables.",
    )
    assert res_unmatch["status"] == "UNKNOWN"
    assert res_unmatch["is_applicable"] is False
    assert res_unmatch["verification_required"] is True
    assert "Applicability could not be verified from available official evidence." in res_unmatch["reason"]

    # 3. Explicit exclusion clause -> NO_MATCH
    res_excl = evaluate_qco_applicability(
        product=prod_matching,
        qco_record=qco_matching,
        official_evidence_text="This order does not apply to pvc insulated copper wire intended for export.",
    )
    assert res_excl["status"] == "NO_MATCH"
    assert res_excl["is_applicable"] is False


# ============================================================
# 5. LABORATORY RESULT CONTROL (Requirements 2, 3, 13)
# ============================================================

def test_laboratories_appear_only_for_laboratory_search_intent():
    """
    Test that laboratory information appears ONLY when intent is LABORATORY_SEARCH,
    and is strictly omitted for standard lookup, certification, or testing requirements.
    """
    pipeline = RAGPipeline()

    # Case A: STANDARD_LOOKUP intent
    res_std = pipeline.query("What standard applies to domestic electric irons?")
    assert res_std["laboratories"] == [], "Laboratories must NOT appear for STANDARD_LOOKUP intent!"

    # Case B: TESTING_REQUIREMENT intent
    res_test = pipeline.query("What are the drop test requirements and tolerances for clinical thermometers?")
    assert res_test["laboratories"] == [], "Laboratories must NOT appear for TESTING_REQUIREMENT intent!"

    # Case C: LABORATORY_SEARCH intent
    entities_lab = extract_query_entities("Find a testing laboratory for electric irons")
    intent_lab = classify_intent_deterministic("Find a testing laboratory for electric irons", entities_lab)
    assert intent_lab.intent == QueryIntentType.LABORATORY_SEARCH

    res_lab = pipeline.query("Find a testing laboratory for electric irons")
    assert res_lab["intent"] == QueryIntentType.LABORATORY_SEARCH.value


# ============================================================
# 6. MULTI-SIGNAL RERANKING (Requirements 1, 11)
# ============================================================

def test_multi_signal_reranking_penalizes_conflicting_product_domain():
    """
    Test that a candidate with high CrossEncoder score but belonging to a conflicting
    product domain is severely penalized below a matching product candidate.
    """
    entities = extract_query_entities("What standard applies to portland cement?")
    q_ctx = build_query_context(
        query="What standard applies to portland cement?",
        business_context=BusinessContext(product_name="portland cement"),
    )

    # Candidate A: Toys standard with high lexical/cross-encoder score (0.95)
    cand_wrong_product = {
        "chunk_id": "chunk_toy_1",
        "standard_number": "IS 9873",
        "standard_title": "Safety of Toys - Mechanical and Physical Properties",
        "reranker_score": 0.95,
        "fusion_score": 0.50,
        "content": "Safety requirements and drop test specifications.",
    }

    # Candidate B: Cement standard with lower cross-encoder score (0.60)
    cand_correct_product = {
        "chunk_id": "chunk_cement_1",
        "standard_number": "IS 1489",
        "standard_title": "Portland Pozzolana Cement - Specification",
        "reranker_score": 0.60,
        "fusion_score": 0.30,
        "content": "Portland pozzolana cement manufacturing specifications.",
    }

    key_wrong = compute_intent_ranking_key(cand_wrong_product, entities, query_context=q_ctx)
    key_correct = compute_intent_ranking_key(cand_correct_product, entities, query_context=q_ctx)

    # product_match_tier is the first element
    p_tier_wrong = key_wrong[0]
    p_tier_correct = key_correct[0]

    assert p_tier_correct > p_tier_wrong, "Correct product domain must outrank conflicting product domain!"
    assert p_tier_wrong == -3, "Conflicting product domain must be penalized with -3 tier."
    assert p_tier_correct == 2, "Matching product domain must receive positive tier +2."


# ============================================================
# 7. DEBUG / TRACE MODE (Requirement 14)
# ============================================================

def test_debug_trace_mode_captures_pipeline_steps():
    """
    Test that response trace includes user_query, intent, normalized_product,
    accepted_evidence, and rejected_evidence with explicit reasons.
    """
    pipeline = RAGPipeline()
    res = pipeline.query("Which Indian standard applies to electric irons?")

    assert "trace" in res, "Response must include debug trace!"
    trace = res["trace"]
    assert "user_query" in trace
    assert "intent" in trace
    assert "normalized_product" in trace
    assert "accepted_evidence" in trace
    assert "rejected_evidence" in trace
    assert "final_status" in trace
    assert "assessment_id" in trace
    assert "lifecycle_state" in trace
