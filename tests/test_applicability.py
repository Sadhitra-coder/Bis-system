"""
tests/test_applicability.py

Unit and Integration Tests for Phase 15: Applicability & Compliance Readiness.
Verifies positive inclusion, explicit exclusion handling, negative facts safety,
multi-standard readiness synthesis, strict ban on 'COMPLIANT'/'CERTIFIED',
and non-certification disclaimers.
"""

import json
from pathlib import Path
import pytest

from app.applicability import (
    ApplicabilityAssessment,
    ApplicabilityStatus,
    ComplianceReadinessReport,
    ComplianceReadinessState,
    evaluate_standard_applicability,
    synthesize_compliance_readiness,
)
from app.document_intelligence.models import (
    DocumentMatchStatus,
    DocumentRequirementMatch,
    EvidenceGapReport,
)
from app.product_mapping.models import ProductContext, ProductStandardCandidate
from app.technical_specs.models import (
    MatchState,
    SpecificationComparisonResult,
    StandardTechnicalRequirement,
    TechnicalAnalysisReport,
    TechnicalParameter,
    TechnicalSpecification,
)


def test_models_and_enums():
    """Verify ApplicabilityStatus and ComplianceReadinessState enums."""
    ass = ApplicabilityAssessment(
        assessment_id="ass_1",
        standard_id="std_1",
        standard_number="IS 302-2-3",
        status=ApplicabilityStatus.APPLICABLE,
        reason="Direct scope match",
        positive_inclusions=["electric irons"],
    )
    d = ass.to_dict()
    assert d["status"] == "APPLICABLE"
    assert d["standard_number"] == "IS 302-2-3"

    rep = ComplianceReadinessReport(
        readiness_id="rep_1",
        overall_readiness_state=ComplianceReadinessState.READY_FOR_HUMAN_REVIEW,
        readiness_score=0.85,
    )
    rd = rep.to_dict()
    assert rd["overall_readiness_state"] == "READY_FOR_HUMAN_REVIEW"
    assert "disclaimer" in rd
    assert "NOT constitute a legal certification" in rd["disclaimer"]


def test_positive_scope_applicability():
    """Verify product in direct standard scope produces APPLICABLE status."""
    prod = ProductContext(
        product_context_id="p1",
        product_name="Electric Dry Iron",
        product_category="Household Appliance",
        intended_use="Domestic garment ironing",
        technical_characteristics={"use_environment": "household"},
    )
    cand = ProductStandardCandidate(
        mapping_id="m1",
        product_context_id="p1",
        standard_id="std_302",
        standard_number="IS 302-2-3",
        standard_title="Safety of household electrical appliances - Electric irons",
        mapping_score=0.92,
        temporal_status="ACTIVE",
    )
    ass = evaluate_standard_applicability(prod, cand)
    assert ass.status == ApplicabilityStatus.APPLICABLE
    assert not ass.verification_required


def test_explicit_exclusion_not_applicable():
    """Verify explicit exclusion clause produces NOT_APPLICABLE."""
    prod = ProductContext(
        product_context_id="p2",
        product_name="Industrial Steam Finishing Table",
        product_category="commercial laundry equipment",
        intended_use="Commercial laundry pressing",
        technical_characteristics={"use_environment": "commercial"},
    )
    cand = ProductStandardCandidate(
        mapping_id="m2",
        product_context_id="p2",
        standard_id="std_302",
        standard_number="IS 302-2-3",
        standard_title="Safety of household electrical appliances - Electric irons",
        mapping_score=0.60,
        temporal_status="ACTIVE",
    )
    scope_text = "This standard applies to domestic electric irons. This standard does not apply to commercial laundry appliances."
    ass = evaluate_standard_applicability(prod, cand, scope_text=scope_text)
    assert ass.status == ApplicabilityStatus.NOT_APPLICABLE
    assert len(ass.explicit_exclusions) > 0
    assert "commercial" in ass.reason.lower()


def test_absence_of_evidence_maps_to_insufficient():
    """Verify that lack of evidence NEVER maps to NOT_APPLICABLE."""
    cand = ProductStandardCandidate(
        mapping_id="m_toy",
        product_context_id="p_toy",
        standard_id="std_toy",
        standard_number="IS 9873",
        standard_title="Safety of Toys",
        mapping_score=0.10,
        temporal_status="ACTIVE",
    )
    # Empty product
    ass = evaluate_standard_applicability(None, cand)
    assert ass.status == ApplicabilityStatus.INSUFFICIENT_EVIDENCE
    assert ass.verification_required
    assert ass.status != ApplicabilityStatus.NOT_APPLICABLE


def test_potentially_applicable_family_match():
    """Verify broad family match requires verification."""
    prod = ProductContext(
        product_context_id="p3",
        product_name="Heavy Duty Heating Element",
        product_category="heating equipment",
    )
    cand = ProductStandardCandidate(
        mapping_id="m3",
        product_context_id="p3",
        standard_id="std_immersion",
        standard_number="IS 302-2-201",
        standard_title="Electric immersion water heaters",
        mapping_score=0.50,
        temporal_status="ACTIVE",
    )
    ass = evaluate_standard_applicability(prod, cand)
    assert ass.status == ApplicabilityStatus.POTENTIALLY_APPLICABLE
    assert ass.verification_required


def test_superseded_standard_verification():
    """Verify superseded standards require verification regardless of score."""
    prod = ProductContext(
        product_context_id="p4",
        product_name="PVC Cable",
        product_category="Cables",
    )
    cand = ProductStandardCandidate(
        mapping_id="m4",
        product_context_id="p4",
        standard_id="std_cable_old",
        standard_number="IS 694 : 1990",
        standard_title="PVC insulated cables",
        mapping_score=0.95,
        temporal_status="SUPERSEDED",
    )
    ass = evaluate_standard_applicability(prod, cand)
    assert ass.status == ApplicabilityStatus.VERIFICATION_REQUIRED
    assert ass.verification_required
    assert "SUPERSEDED" in ass.reason


def test_ready_for_human_review_synthesis():
    """Verify perfect alignment leads to READY_FOR_HUMAN_REVIEW."""
    prod = ProductContext(product_context_id="p1", product_name="Electric Iron")
    ass = ApplicabilityAssessment(
        assessment_id="a1",
        standard_id="s1",
        standard_number="IS 302",
        status=ApplicabilityStatus.APPLICABLE,
        reason="Matches scope",
        temporal_status="ACTIVE",
    )

    dummy_param = TechnicalParameter(parameter_name="voltage", value=230.0, unit="V")
    dummy_req = StandardTechnicalRequirement(
        requirement_id="req1",
        standard_id="s1",
        requirement_text="Voltage must be 230 V",
        parameter_name="voltage",
    )
    spec_cmp = SpecificationComparisonResult(
        result_id="c1",
        parameter_name="voltage",
        match_state=MatchState.MATCH,
        reason="Voltage matches 230 V",
        spec_parameter=dummy_param,
        standard_requirement=dummy_req,
    )
    tech_rep = TechnicalAnalysisReport(
        analysis_id="tr1",
        technical_specification=TechnicalSpecification(specification_id="s1"),
        requirement_matches=[spec_cmp],
    )

    doc_match = DocumentRequirementMatch(
        requirement_id="r1",
        parameter_name="voltage",
        status=DocumentMatchStatus.SUPPORTED,
        reason="Test report verified 230 V",
    )
    ev_rep = EvidenceGapReport(
        report_id="er1",
        total_requirements=1,
        supported_count=1,
        supported_requirements=[doc_match],
        overall_evidence_state="EVIDENCE_COMPLETE",
    )

    readiness = synthesize_compliance_readiness(
        product=prod,
        applicability_assessments=[ass],
        technical_analysis=tech_rep,
        evidence_report=ev_rep,
    )

    assert readiness.overall_readiness_state == ComplianceReadinessState.READY_FOR_HUMAN_REVIEW
    assert readiness.readiness_score >= 0.70
    assert len(readiness.actionable_next_steps) > 0


def test_technical_gaps_found_synthesis():
    """Verify technical mismatches drive TECHNICAL_GAPS_FOUND state."""
    prod = ProductContext(product_context_id="p1", product_name="Electric Iron")
    ass = ApplicabilityAssessment(
        assessment_id="a1",
        standard_id="s1",
        standard_number="IS 302",
        status=ApplicabilityStatus.APPLICABLE,
        reason="Matches scope",
        temporal_status="ACTIVE",
    )

    spec_mismatch = SpecificationComparisonResult(
        result_id="c2",
        parameter_name="operating_temperature",
        match_state=MatchState.MISMATCH,
        reason="Temperature 120 C exceeds limit 100 C",
        spec_parameter=TechnicalParameter(parameter_name="operating_temperature", value=120.0),
        standard_requirement=StandardTechnicalRequirement(
            requirement_id="r_temp", standard_id="s1", requirement_text="Max 100 C", parameter_name="operating_temperature"
        ),
    )
    tech_rep = TechnicalAnalysisReport(
        analysis_id="tr2",
        technical_specification=TechnicalSpecification(specification_id="s2"),
        technical_gaps=[spec_mismatch],
    )

    readiness = synthesize_compliance_readiness(
        product=prod,
        applicability_assessments=[ass],
        technical_analysis=tech_rep,
    )

    assert readiness.overall_readiness_state == ComplianceReadinessState.TECHNICAL_GAPS_FOUND
    assert any("operating_temperature" in step for step in readiness.actionable_next_steps)


def test_document_gaps_and_conflicts_synthesis():
    """Verify expired documents and contradictory evidence drive proper states."""
    prod = ProductContext(product_context_id="p1", product_name="Electric Iron")
    ass = ApplicabilityAssessment(
        assessment_id="a1",
        standard_id="s1",
        standard_number="IS 302",
        status=ApplicabilityStatus.APPLICABLE,
        reason="Matches scope",
        temporal_status="ACTIVE",
    )

    # 1. Expired document
    ev_rep_exp = EvidenceGapReport(
        report_id="er_exp",
        total_requirements=1,
        expired_count=1,
        overall_evidence_state="EXPIRED_EVIDENCE_FOUND",
    )
    readiness_exp = synthesize_compliance_readiness(
        product=prod,
        applicability_assessments=[ass],
        evidence_report=ev_rep_exp,
    )
    assert readiness_exp.overall_readiness_state == ComplianceReadinessState.DOCUMENT_GAPS_FOUND

    # 2. Conflicting evidence
    ev_rep_conf = EvidenceGapReport(
        report_id="er_conf",
        total_requirements=1,
        conflicting_count=1,
        overall_evidence_state="CONFLICTS_DETECTED",
    )
    readiness_conf = synthesize_compliance_readiness(
        product=prod,
        applicability_assessments=[ass],
        evidence_report=ev_rep_conf,
    )
    assert readiness_conf.overall_readiness_state == ComplianceReadinessState.VERIFICATION_REQUIRED


def test_strict_ban_on_forbidden_strings():
    """
    CRITICAL SAFETY AUDIT: Ensure 'COMPLIANT' or 'CERTIFIED' or 'APPROVED'
    are NEVER used as readiness or applicability state names.
    """
    for state in ComplianceReadinessState:
        val = state.value
        assert "COMPLIANT" not in val, f"Forbidden substring in state: {val}"
        assert "CERTIFIED" not in val, f"Forbidden substring in state: {val}"
        assert "APPROVED" not in val, f"Forbidden substring in state: {val}"
        assert "LEGALLY" not in val, f"Forbidden substring in state: {val}"

    for status in ApplicabilityStatus:
        val = status.value
        assert "COMPLIANT" not in val, f"Forbidden substring in status: {val}"
        assert "CERTIFIED" not in val, f"Forbidden substring in status: {val}"


def test_applicability_benchmark_dataset():
    """Run all scenarios from data/evaluation/applicability_eval_dataset.json."""
    dataset_path = Path("data/evaluation/applicability_eval_dataset.json")
    assert dataset_path.exists()

    with open(dataset_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    scenarios = data.get("scenarios", [])
    assert len(scenarios) >= 10

    for sc in scenarios:
        sc_id = sc["id"]

        if "expected_applicability_status" in sc:
            prod_data = sc.get("product")
            prod = None
            if prod_data:
                prod = ProductContext(
                    product_context_id="p_" + sc_id,
                    product_name=prod_data.get("product_name"),
                    product_category=prod_data.get("category"),
                    intended_use=prod_data.get("intended_use"),
                    technical_characteristics={"use_environment": prod_data.get("use_environment")},
                )
            cand_data = sc["candidate"]
            cand = ProductStandardCandidate(
                mapping_id="m_" + sc_id,
                product_context_id="p_" + sc_id,
                standard_id=cand_data["standard_id"],
                standard_number=cand_data["standard_number"],
                standard_title=cand_data.get("standard_title"),
                mapping_score=cand_data.get("match_score", 0.5),
                temporal_status=cand_data.get("temporal_status", "ACTIVE"),
            )
            scope = sc.get("scope_text")

            ass = evaluate_standard_applicability(prod, cand, scope_text=scope)
            assert ass.status.value == sc["expected_applicability_status"], (
                f"Scenario {sc_id} expected {sc['expected_applicability_status']}, got {ass.status.value}"
            )

        if "expected_readiness_state" in sc:
            r_in = sc["readiness_inputs"]
            prod = ProductContext(product_context_id="p1", product_name="Test Product")
            ass_status = ApplicabilityStatus.APPLICABLE if r_in["has_applicable"] else ApplicabilityStatus.INSUFFICIENT_EVIDENCE
            ass = ApplicabilityAssessment(
                assessment_id="a1",
                standard_id="s1",
                standard_number="IS TEST",
                status=ass_status,
                reason="Test",
                temporal_status="ACTIVE",
            )

            tech_rep = None
            if r_in["has_tech_mismatches"]:
                dummy_gap = SpecificationComparisonResult(
                    result_id="gap_dummy",
                    parameter_name="dummy_param",
                    match_state=MatchState.MISMATCH,
                    reason="mismatch",
                )
                tech_rep = TechnicalAnalysisReport(
                    analysis_id="tr1",
                    technical_specification=TechnicalSpecification(specification_id="s_dummy"),
                    technical_gaps=[dummy_gap],
                )

            ev_rep = None
            if r_in["has_conflicts"]:
                ev_rep = EvidenceGapReport(report_id="er1", total_requirements=1, conflicting_count=1)
            elif r_in["has_expired"]:
                ev_rep = EvidenceGapReport(report_id="er1", total_requirements=1, expired_count=1)
            elif r_in["has_missing_evidence"]:
                ev_rep = EvidenceGapReport(report_id="er1", total_requirements=1, missing_count=1)
            else:
                ev_rep = EvidenceGapReport(report_id="er1", total_requirements=1, supported_count=1)

            readiness = synthesize_compliance_readiness(
                product=prod,
                applicability_assessments=[ass],
                technical_analysis=tech_rep,
                evidence_report=ev_rep,
            )
            assert readiness.overall_readiness_state.value == sc["expected_readiness_state"], (
                f"Scenario {sc_id} expected readiness {sc['expected_readiness_state']}, got {readiness.overall_readiness_state.value}"
            )
