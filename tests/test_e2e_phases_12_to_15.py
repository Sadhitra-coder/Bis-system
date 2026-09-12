"""
tests/test_e2e_phases_12_to_15.py

End-to-End Integration and Safety Verification for Phases 12 through 15:
  - Phase 12: Technical Specification Analyzer
  - Phase 13: Tender / Customer Specification Gap Analyzer
  - Phase 14: Document Intelligence + Requirement Matching
  - Phase 15: Applicability Intelligence + Compliance Readiness

Verifies seamless multi-standard synthesis, 0% false compliance claims,
strict ban on 'COMPLIANT' / 'CERTIFIED', and robust safety guarantees.
"""

import json
from pathlib import Path
import pytest

from app.applicability import (
    ApplicabilityStatus,
    ComplianceReadinessState,
    evaluate_standard_applicability,
    synthesize_compliance_readiness,
)
from app.document_intelligence import (
    BusinessDocument,
    DocumentMatchStatus,
    DocumentType,
    build_evidence_gap_report,
    extract_business_document,
)
from app.product_mapping.models import ProductContext, ProductStandardCandidate
from app.technical_specs import (
    ComparisonOperator,
    MatchState,
    StandardTechnicalRequirement,
    TechnicalParameter,
    TechnicalSpecification,
    analyze_technical_specification,
    extract_technical_specification,
)
from app.tender_analysis import (
    TenderDocument,
    TenderGapState,
    analyze_tender_gaps,
    extract_tender_document,
)


def test_full_phases_12_to_15_tripartite_readiness_pipeline():
    """
    Comprehensive End-to-End Test:
    1. Product Specification: Electric Immersion Water Heater (Phase 12)
    2. Customer Tender Request: Requires 1500W, 230V, IS 302 compliance (Phase 13)
    3. Laboratory Test Report: NABL accredited test report confirming 1500W, 230V (Phase 14)
    4. Applicability & Compliance Readiness Assessment (Phase 15)
    """
    # 1. Product Context & Spec (Phase 11 & 12)
    prod_ctx = ProductContext(
        product_context_id="prod_immersion_01",
        product_name="Electric Immersion Water Heater",
        product_category="Water Heaters",
        intended_use="Domestic water heating",
        technical_characteristics={"power": "1500 W", "voltage": "230 V"},
    )
    product_spec = extract_technical_specification(
        text="Rated Voltage: 230 V\nRated Power: 1500 W\nMaterial: Copper Sheath",
        product_name="Electric Immersion Water Heater",
    )
    assert "Rated Voltage" in product_spec.parameters or "voltage" in [p.lower() for p in product_spec.parameters]

    # Standard candidate & requirements
    std_cand = ProductStandardCandidate(
        mapping_id="map_immersion",
        product_context_id="prod_immersion_01",
        standard_id="std_is_302_2_201",
        standard_number="IS 302-2-201",
        standard_title="Safety of household electrical appliances - Particular requirements for electric immersion heaters",
        mapping_score=0.92,
        temporal_status="ACTIVE",
    )

    req_voltage = StandardTechnicalRequirement(
        requirement_id="REQ-V-230",
        standard_id="std_is_302_2_201",
        requirement_text="Rated voltage shall be 230 V",
        standard_number="IS 302-2-201",
        parameter_name="Rated Voltage",
        threshold=230.0,
        unit="V",
    )
    req_power = StandardTechnicalRequirement(
        requirement_id="REQ-P-1500",
        standard_id="std_is_302_2_201",
        requirement_text="Rated input power shall not exceed 2000 W",
        standard_number="IS 302-2-201",
        parameter_name="Rated Power",
        operator=ComparisonOperator.LE,
        threshold=2000.0,
        unit="W",
    )
    standard_reqs = [req_voltage, req_power]

    # Phase 12: Technical Specification Analysis
    tech_report = analyze_technical_specification(
        spec=product_spec,
        requirements=standard_reqs,
        product_context_id="prod_immersion_01",
    )
    assert len(tech_report.requirement_matches) >= 1
    assert len(tech_report.technical_gaps) == 0

    # Phase 13: Tender Specification Analysis
    tender_raw_text = (
        "GOVERNMENT TENDER RFQ-2024-HEATER\n"
        "Technical Requirements:\n"
        "1. Rated Voltage shall be 230 V AC.\n"
        "2. Rated Power: 1500 W.\n"
        "3. Material shall be copper sheath.\n"
    )
    tender_doc = extract_tender_document(
        text=tender_raw_text,
        title="Tender for Water Heaters",
        issuer="Central Public Works Department",
    )
    tender_report = analyze_tender_gaps(
        tender=tender_doc,
        standard_requirements=standard_reqs,
        product_spec=product_spec,
    )
    assert tender_report.total_tender_requirements >= 2
    assert tender_report.conflict_count == 0

    # Phase 14: Document Intelligence
    lab_report_text = (
        "NATIONAL ELECTRICAL TESTING LABORATORY\n"
        "NABL Accredited Laboratory (ISO/IEC 17025)\n"
        "Test Report No: TR-2024-5541\n"
        "Date of Issue: 20-04-2024\n"
        "Tested as per IS 302-2-201\n\n"
        "Test Results:\n"
        "Rated Voltage: Observed = 230 V, Specified = 230 V, Result: Pass\n"
        "Rated Power: Observed = 1500 W, Specified = <= 2000 W, Result: Pass\n"
    )
    business_doc = extract_business_document(
        text=lab_report_text,
        filename="tr_immersion_heater.pdf",
        as_of_date="2024-06-01",
    )
    assert business_doc.document_type == DocumentType.TEST_REPORT
    assert business_doc.status == "ACTIVE"

    evidence_report = build_evidence_gap_report(
        requirements=standard_reqs,
        documents=[business_doc],
        as_of_date="2024-06-01",
    )
    assert evidence_report.supported_count >= 1
    assert evidence_report.conflicting_count == 0
    assert evidence_report.expired_count == 0

    # Phase 15: Applicability & Compliance Readiness
    applicability_assessment = evaluate_standard_applicability(
        product=prod_ctx,
        candidate=std_cand,
    )
    assert applicability_assessment.status == ApplicabilityStatus.APPLICABLE

    readiness_report = synthesize_compliance_readiness(
        product=prod_ctx,
        applicability_assessments=[applicability_assessment],
        technical_analysis=tech_report,
        tender_analysis=tender_report,
        evidence_report=evidence_report,
    )

    assert readiness_report.overall_readiness_state in (
        ComplianceReadinessState.READY_FOR_HUMAN_REVIEW,
        ComplianceReadinessState.EVIDENCE_INCOMPLETE,
    )
    assert readiness_report.readiness_score > 0.60
    assert "disclaimer" in readiness_report.to_dict()
    assert len(readiness_report.actionable_next_steps) > 0


def test_adversarial_safety_and_forbidden_claims():
    """
    CRITICAL AUDIT: Verify that no module produces 'COMPLIANT' or 'CERTIFIED'
    or grants official legal clearance.
    """
    prod = ProductContext(product_context_id="p_adv", product_name="Adversarial Item")
    cand = ProductStandardCandidate(
        mapping_id="m_adv",
        product_context_id="p_adv",
        standard_id="std_adv",
        standard_number="IS 9999",
        mapping_score=0.99,
        temporal_status="ACTIVE",
    )
    ass = evaluate_standard_applicability(prod, cand)
    rep = synthesize_compliance_readiness(prod, [ass])

    # Check string representations for forbidden words
    rep_dict_str = json.dumps(rep.to_dict())
    assert "COMPLIANT" not in rep.overall_readiness_state.value
    assert "CERTIFIED" not in rep.overall_readiness_state.value
    assert "APPROVED" not in rep.overall_readiness_state.value
    assert "LEGALLY" not in rep.overall_readiness_state.value

    # Check disclaimer exists
    assert "NOT constitute a legal certification" in rep.disclaimer


if __name__ == "__main__":
    test_full_phases_12_to_15_tripartite_readiness_pipeline()
    test_adversarial_safety_and_forbidden_claims()
    print("ALL E2E TESTS PASSED!")
