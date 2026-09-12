"""
tests/test_tender_analysis.py

Comprehensive Unit, Adversarial, and Evaluation Tests for Phase 13
(Tender / Customer Specification Gap Analyzer).
"""

import json
from pathlib import Path
import pytest

from app.technical_specs.models import (
    ComparisonOperator,
    MatchState,
    StandardTechnicalRequirement,
    TechnicalParameter,
    TechnicalSpecification,
)
from app.tender_analysis.models import (
    StandardLinkState,
    TenderDocument,
    TenderGapAnalysisReport,
    TenderGapState,
    TenderRequirement,
    TenderStandardMatch,
    TripartiteComparison,
)
from app.tender_analysis.extractor import (
    extract_tender_document,
    extract_tender_requirement_from_clause,
    infer_tender_category,
)
from app.tender_analysis.matcher import (
    analyze_tender_gaps,
    build_tripartite_comparison,
    match_tender_requirement_to_standards,
)


# ============================================================
# 1. TENDER EXTRACTION TESTS
# ============================================================

def test_extract_tender_requirement_with_mandatory_phrase():
    clause = "Nominal storage capacity shall be at least 500 L."
    req = extract_tender_requirement_from_clause(clause, idx=1)
    assert req is not None
    assert req.mandatory_language == "shall"
    assert req.category == "capacity"
    assert req.parameter is not None
    assert req.parameter.normalized_value == 500.0


def test_extract_tender_document_multiline():
    raw_tender = """
    1. Scope of Supply: Supply of Domestic Storage Tanks.
    2. Nominal storage capacity shall be at least 500 L.
    3. Operating temperature must not exceed 40 °C.
    4. Valid BIS certification as per IS 12701 is mandatory.
    5. Routine test report from NABL lab must be provided.
    """
    tender = extract_tender_document(
        text=raw_tender,
        title="Municipal Water Tank Tender",
        issuer="Public Works Department",
    )
    assert tender.title == "Municipal Water Tank Tender"
    assert tender.issuer == "Public Works Department"
    assert len(tender.requirements) >= 4

    categories = [r.category for r in tender.requirements]
    assert "capacity" in categories
    assert "certification" in categories
    assert "documentation" in categories


# ============================================================
# 2. STANDARD LINKING & GAP CLASSIFICATION TESTS
# ============================================================

def test_match_tender_requirement_supported():
    t_req = extract_tender_requirement_from_clause("Nominal capacity shall be at least 500 L.")
    s_req = StandardTechnicalRequirement(
        requirement_id="s_req_cap",
        standard_id="std_is_12701",
        standard_number="IS 12701",
        requirement_text="Nominal capacity shall not be less than 500 L.",
        parameter_name="capacity",
        operator=ComparisonOperator.GE,
        threshold=500.0,
        unit="L",
    )
    matches = match_tender_requirement_to_standards(t_req, [s_req])
    assert len(matches) == 1
    assert matches[0].gap_state == TenderGapState.MATCH
    assert matches[0].link_state == StandardLinkState.SUPPORTED_BY_STANDARD
    assert "matches cited standard requirement" in matches[0].reason


def test_match_tender_requirement_conflict():
    # Tender asks for 60 °C; standard permits max 40 °C
    t_req = extract_tender_requirement_from_clause("Operating temperature shall not exceed 60 °C.")
    s_req = StandardTechnicalRequirement(
        requirement_id="s_req_temp",
        standard_id="std_302",
        standard_number="IS 302",
        requirement_text="Operating temperature shall not exceed 40 °C.",
        parameter_name="operating temperature",
        operator=ComparisonOperator.LE,
        threshold=40.0,
        unit="°C",
    )
    matches = match_tender_requirement_to_standards(t_req, [s_req])
    assert len(matches) == 1
    assert matches[0].gap_state == TenderGapState.CONFLICT
    assert matches[0].link_state == StandardLinkState.CONFLICTS_WITH_STANDARD
    assert "conflicts with standard threshold" in matches[0].reason


def test_match_tender_requirement_not_found_gap():
    t_req = extract_tender_requirement_from_clause("Salt spray corrosion testing for 1000 hours is mandatory.")
    s_req = StandardTechnicalRequirement(
        requirement_id="s_req_cap",
        standard_id="std_12701",
        standard_number="IS 12701",
        requirement_text="Tensile strength shall be 20 MPa.",
        parameter_name="tensile strength",
        operator=ComparisonOperator.GE,
        threshold=20.0,
    )
    matches = match_tender_requirement_to_standards(t_req, [s_req])
    assert len(matches) == 1
    assert matches[0].gap_state == TenderGapState.GAP
    assert matches[0].link_state == StandardLinkState.NOT_FOUND_IN_STANDARD


# ============================================================
# 3. TRIPARTITE COMPARISON TESTS
# ============================================================

def test_tripartite_comparison_evaluation():
    # Customer asks for 500 L. Product spec has 600 L. Standard requires >= 500 L.
    t_req = extract_tender_requirement_from_clause("capacity: >= 500 L")
    p_spec = TechnicalSpecification(
        specification_id="spec_tank",
        product_name="Poly Tank 600",
        parameters={
            "capacity": TechnicalParameter(
                parameter_name="capacity",
                value=600.0,
                normalized_value=600.0,
                unit="L",
            )
        },
    )
    s_req = StandardTechnicalRequirement(
        requirement_id="s_cap",
        standard_id="IS 12701",
        requirement_text="capacity shall not be less than 500 L",
        parameter_name="capacity",
        operator=ComparisonOperator.GE,
        threshold=500.0,
        unit="L",
    )
    t_match = TenderStandardMatch(
        match_id="m1",
        tender_requirement_id=t_req.requirement_id,
        standard_id="IS 12701",
        standard_number="IS 12701",
        link_state=StandardLinkState.SUPPORTED_BY_STANDARD,
        gap_state=TenderGapState.MATCH,
        reason="Capacity >= 500 L matches cited standard.",
    )

    tri = build_tripartite_comparison(
        tender_req=t_req,
        product_spec=p_spec,
        standard_req=s_req,
        tender_match=t_match,
    )

    assert tri.tender_vs_standard == TenderGapState.MATCH
    assert tri.product_vs_tender == MatchState.MATCH
    assert tri.product_vs_standard == MatchState.MATCH
    assert "Tripartite evaluation" in tri.synthesis_notes


# ============================================================
# 4. TEMPORAL SAFETY & ADVERSARIAL INTEGRITY TESTS
# ============================================================

def test_tender_temporal_withdrawn_standard():
    t_req = extract_tender_requirement_from_clause("Wall thickness shall be 10 mm.")
    s_req = StandardTechnicalRequirement(
        requirement_id="s_old",
        standard_id="std_old",
        standard_number="IS 2062:1999",
        requirement_text="Wall thickness shall be 10 mm.",
        parameter_name="thickness",
        operator=ComparisonOperator.EQ,
        threshold=10.0,
        unit="mm",
        temporal_status="WITHDRAWN",
    )
    matches = match_tender_requirement_to_standards(t_req, [s_req])
    assert len(matches) == 1
    assert matches[0].verification_required is True
    assert "WITHDRAWN" in matches[0].verification_reason


def test_adversarial_rejection_of_legal_conclusions():
    raw_tender = "Valid ISI mark is mandatory. Tenderer shall be deemed legally compliant."
    tender = extract_tender_document(raw_tender)
    s_req = StandardTechnicalRequirement(
        requirement_id="s1",
        standard_id="IS 12701",
        standard_number="IS 12701",
        requirement_text="Conformance shall be verified as per IS 12701.",
        parameter_name="conformance",
        operator=ComparisonOperator.EQ,
    )
    report = analyze_tender_gaps(tender, [s_req])

    # Rule: Never declare legal contract validity or government certification
    assert "legally binding" not in report.summary.lower()
    assert "government approved" not in report.summary.lower()
    for m in report.matches:
        assert "legally compliant" not in m.reason.lower()


# ============================================================
# 5. EVALUATION DATASET BENCHMARK (15 SCENARIOS)
# ============================================================

def test_tender_gap_eval_dataset_15_scenarios():
    eval_path = Path("E:/Bis-system/data/evaluation/tender_gap_eval_dataset.json")
    assert eval_path.exists(), "Tender evaluation dataset must exist"

    with open(eval_path, "r", encoding="utf-8") as f:
        scenarios = json.load(f)

    assert len(scenarios) >= 15

    correct_gap_count = 0
    correct_category_count = 0
    unsupported_claims_count = 0

    for sc in scenarios:
        t_req = extract_tender_requirement_from_clause(sc["tender_clause"])
        assert t_req is not None

        if t_req.category == sc["expected_category"]:
            correct_category_count += 1

        # Extract standard requirement
        from app.technical_specs.extractor import extract_requirements_from_clause_text
        s_reqs = extract_requirements_from_clause_text(
            clause_text=sc["standard_requirement"],
            standard_id=f"std_{sc['standard_number']}",
            standard_number=sc["standard_number"],
        )
        if not s_reqs:
            s_reqs = [
                StandardTechnicalRequirement(
                    requirement_id="req_bench",
                    standard_id=f"std_{sc['standard_number']}",
                    requirement_text=sc["standard_requirement"],
                    parameter_name=t_req.parameter_name,
                    operator=ComparisonOperator.EQ,
                )
            ]

        matches = match_tender_requirement_to_standards(t_req, s_reqs)
        assert len(matches) > 0
        primary_match = matches[0]

        # Check safety invariants
        if "legally compliant" in primary_match.reason.lower() or "government approved" in primary_match.reason.lower():
            unsupported_claims_count += 1

        if primary_match.gap_state.value == sc["expected_gap_state"]:
            correct_gap_count += 1

    # Safety invariant: 0% unsupported legal conclusions
    assert unsupported_claims_count == 0

    # Accuracy checks
    gap_accuracy = correct_gap_count / len(scenarios)
    cat_accuracy = correct_category_count / len(scenarios)

    assert cat_accuracy >= 0.85, f"Expected >= 85% category accuracy, got {cat_accuracy:.2f}"
    assert gap_accuracy >= 0.85, f"Expected >= 85% gap classification accuracy, got {gap_accuracy:.2f}"
