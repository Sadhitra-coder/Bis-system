"""
tests/test_document_intelligence.py

Unit and Integration Tests for Phase 14: Document Intelligence + Requirement Matching.
Verifies classification, extraction, date parsing, expiry detection,
anti-spoofing adversarial resilience, multi-document conflict detection,
and 0% false-positive support claims.
"""

from datetime import datetime, timedelta
import json
from pathlib import Path
import pytest

from app.document_intelligence import (
    BusinessDocument,
    DocumentMatchStatus,
    DocumentRequirementMatch,
    DocumentType,
    EvidenceGapReport,
    build_evidence_gap_report,
    classify_document,
    evaluate_requirement_against_documents,
    extract_business_document,
    extract_dates,
    extract_document_number,
    extract_issuer,
    extract_standard_references,
    extract_test_results,
    parse_date_safely,
)
from app.technical_specs.models import (
    ComparisonOperator,
    StandardTechnicalRequirement,
    TechnicalParameter,
)
from app.tender_analysis.models import TenderRequirement


def test_document_models_and_enums():
    """Verify document types and serialization."""
    doc = BusinessDocument(
        document_id="doc-test-1",
        document_type=DocumentType.TEST_REPORT,
        title="Sample Test Report",
        issuer="Central Testing Lab",
        document_number="TR-9901",
        issue_date="2024-01-15",
        expiry_date="2025-01-15",
        status="ACTIVE",
    )
    d = doc.to_dict()
    assert d["document_id"] == "doc-test-1"
    assert d["document_type"] == "TEST_REPORT"
    assert d["issuer"] == "Central Testing Lab"
    assert d["status"] == "ACTIVE"


def test_classification_types():
    """Verify classification across key compliance document types."""
    # Test Report
    tr_text = "Testing laboratory test report of sample tested. Test results and test method per NABL accredited lab."
    doc_type, conf, _ = classify_document(tr_text, filename="sample_tr.pdf")
    assert doc_type == DocumentType.TEST_REPORT
    assert conf >= 0.6

    # Calibration Report
    cal_text = "Calibration certificate for pressure gauge. Calibrated on 10-10-2023. Traceability to NPL India. Standard used for calibration."
    doc_type, conf, _ = classify_document(cal_text, filename="cal_gauge.pdf")
    assert doc_type == DocumentType.CALIBRATION_REPORT
    assert conf >= 0.6

    # Declaration
    dec_text = "Manufacturer Declaration of Conformity. We hereby declare under our sole responsibility that model XYZ conforms with IS 302."
    doc_type, conf, _ = classify_document(dec_text)
    assert doc_type == DocumentType.DECLARATION

    # User Manual
    man_text = "Electric Mixer User Manual. Operating instructions and troubleshooting guide. Maintenance instructions."
    doc_type, conf, _ = classify_document(man_text)
    assert doc_type == DocumentType.MANUAL

    # Engineering Drawing
    dwg_text = "Engineering Drawing. Drawing No: DWG-4412. All dimensions in mm. Scale: 1:5. Third angle projection."
    doc_type, conf, _ = classify_document(dwg_text)
    assert doc_type == DocumentType.DRAWING

    # Chemical Lab Report
    lab_text = "Laboratory analysis report. Chemical analysis report for steel billet. Spectroscopy results and chemical composition."
    doc_type, conf, _ = classify_document(lab_text)
    assert doc_type == DocumentType.LAB_REPORT

    # Quality Record
    qc_text = "Quality Inspection Report. Batch inspection report and factory inspection QAP. Pre-dispatch inspection."
    doc_type, conf, _ = classify_document(qc_text)
    assert doc_type == DocumentType.QUALITY_RECORD


def test_anti_spoofing_classification():
    """
    Adversarial test: Filename indicates one thing, but content is clearly another.
    Content must override filename.
    """
    # Filename says test_report.pdf, but content is purely a User Manual
    spoofed_manual = "USER MANUAL - FOOD PROCESSOR\nOperating instructions and safety guidelines. Keep away from water."
    doc_type, conf, expl = classify_document(spoofed_manual, filename="test_report_official.pdf")
    assert doc_type == DocumentType.MANUAL
    assert expl.get("anti_spoofing_note") is not None

    # Filename says certificate.pdf, but content is an engineering drawing
    spoofed_dwg = "ENGINEERING DRAWING\nDrawing No: 1002-A\nScale: 1:1\nAll dimensions in mm"
    doc_type, conf, expl = classify_document(spoofed_dwg, filename="certificate_of_compliance.pdf")
    assert doc_type == DocumentType.DRAWING


def test_date_parsing_formats():
    """Verify safe date parsing across Indian, ISO, and English formats."""
    # ISO
    d1 = parse_date_safely("2024-05-18")
    assert d1 == datetime(2024, 5, 18)

    # DD-MM-YYYY
    d2 = parse_date_safely("18-05-2024")
    assert d2 == datetime(2024, 5, 18)

    # DD/MM/YYYY
    d3 = parse_date_safely("18/05/2024")
    assert d3 == datetime(2024, 5, 18)

    # DD Month YYYY
    d4 = parse_date_safely("18 May 2024")
    assert d4 == datetime(2024, 5, 18)

    # Invalid / garbage date
    assert parse_date_safely("not-a-date") is None
    assert parse_date_safely("") is None


def test_expiry_date_calculation():
    """Verify active vs expired document determination."""
    text_active = (
        "NATIONAL TESTING LABORATORY\nTest Report No: TR-101\n"
        "Issue Date: 10-01-2024\nValid Up To: 31-12-2028\nVoltage: 230 V"
    )
    doc_active = extract_business_document(text_active, as_of_date="2025-01-01")
    assert doc_active.status == "ACTIVE"
    assert doc_active.expiry_date == "2028-12-31"

    text_expired = (
        "CALIBRATION CENTRE\nCalibration Certificate No: CAL-505\n"
        "Date of Issue: 01-01-2021\nCalibration Due: 01-01-2022\nPressure: 10 bar"
    )
    doc_expired = extract_business_document(text_expired, as_of_date="2024-01-01")
    assert doc_expired.status == "EXPIRED"
    assert doc_expired.expiry_date == "2022-01-01"


def test_metadata_extraction():
    """Verify report number, accreditation, and standard references extraction."""
    text = (
        "SHIVA TESTING LABORATORIES\n"
        "NABL Accredited Laboratory (ISO/IEC 17025:2017)\n"
        "Test Report No: STL/2024/TR-9801\n"
        "Issue Date: 12-04-2024\n"
        "Tested as per IS 9873 (Part 1) : 2019\n"
        "Also referenced IS 302-1 : 2008\n"
    )
    doc_num = extract_document_number(text)
    assert doc_num == "STL/2024/TR-9801"

    std_refs = extract_standard_references(text)
    assert any("IS 9873" in ref for ref in std_refs)
    assert any("IS 302" in ref for ref in std_refs)

    doc = extract_business_document(text)
    assert "NABL Accredited" in (doc.accreditation_details or "")


def test_matching_supported_and_partial():
    """Verify SUPPORTED vs PARTIAL matching."""
    doc_text = (
        "NATIONAL LABS\nTest Report TR-1\n"
        "Insulation Resistance: Observed = 50 MOhm, Specified = >= 10 MOhm, Result: Pass\n"
        "Operating Voltage: 230 V\n"
    )
    doc = extract_business_document(doc_text, document_id="doc_1")

    # Requirement 1: Operating Voltage = 230 V (Supported)
    req1 = StandardTechnicalRequirement(
        requirement_id="REQ-V-01",
        standard_id="std_1",
        requirement_text="Operating voltage shall be 230 V",
        standard_number="IS 302",
        parameter_name="operating_voltage",
        threshold=230.0,
        unit="V",
    )
    match1 = evaluate_requirement_against_documents(req1, [doc])
    assert match1.status == DocumentMatchStatus.SUPPORTED
    assert not match1.verification_required

    # Requirement 2: Operating Voltage = 415 V (Mismatch / Partial)
    req2 = StandardTechnicalRequirement(
        requirement_id="REQ-V-02",
        standard_id="std_1",
        requirement_text="Operating voltage shall be 415 V",
        standard_number="IS 302",
        parameter_name="operating_voltage",
        threshold=415.0,
        unit="V",
    )
    match2 = evaluate_requirement_against_documents(req2, [doc])
    assert match2.status == DocumentMatchStatus.PARTIAL
    assert match2.verification_required


def test_matching_expired_document():
    """Verify that expired documents result in EXPIRED status and require verification."""
    doc_text = (
        "CALIBRATION LAB\nCertificate No: C-99\n"
        "Calibration Due: 2022-01-01\n"
        "Operating Pressure: 15 bar"
    )
    doc = extract_business_document(doc_text, document_id="cal_exp", as_of_date="2025-01-01")
    assert doc.status == "EXPIRED"

    req = StandardTechnicalRequirement(
        requirement_id="REQ-P-01",
        standard_id="std_1",
        requirement_text="Operating pressure shall be 15 bar",
        standard_number="IS 123",
        parameter_name="operating_pressure",
        threshold=15.0,
        unit="bar",
    )
    match = evaluate_requirement_against_documents(req, [doc], as_of_date="2025-01-01")
    assert match.status == DocumentMatchStatus.EXPIRED
    assert match.verification_required
    assert "expired" in match.reason.lower()


def test_matching_conflicting_documents():
    """Verify that contradictory reports across documents trigger CONFLICTING status."""
    doc1_text = (
        "LABORATORY ALPHA\nTest Report No: TR-A\n"
        "Flame Retardance: Observed = Passed, Result: Pass"
    )
    doc2_text = (
        "LABORATORY BETA\nTest Report No: TR-B\n"
        "Flame Retardance: Observed = Ignited within 5s, Result: Fail"
    )
    doc1 = extract_business_document(doc1_text, document_id="doc_alpha")
    doc2 = extract_business_document(doc2_text, document_id="doc_beta")

    req = StandardTechnicalRequirement(
        requirement_id="REQ-FLAME-01",
        standard_id="std_1",
        requirement_text="Flame retardance test",
        standard_number="IS 1500",
        parameter_name="flame_retardance",
    )
    match = evaluate_requirement_against_documents(req, [doc1, doc2])
    assert match.status == DocumentMatchStatus.CONFLICTING
    assert match.verification_required
    assert "contradictory" in match.reason.lower() or "conflicting" in match.reason.lower()


def test_matching_missing_requirement():
    """Verify that an unmentioned parameter produces MISSING status."""
    doc_text = "TEST LAB\nReport TR-99\nRated Voltage: 230 V"
    doc = extract_business_document(doc_text, document_id="doc_v")

    req = StandardTechnicalRequirement(
        requirement_id="REQ-NOISE-01",
        standard_id="std_1",
        requirement_text="Acoustic noise level limit",
        standard_number="IS 1500",
        parameter_name="acoustic_noise_level",
        threshold=60.0,
    )
    match = evaluate_requirement_against_documents(req, [doc])
    assert match.status == DocumentMatchStatus.MISSING
    assert match.verification_required


def test_evidence_gap_report_synthesis():
    """Verify comprehensive EvidenceGapReport building."""
    doc_text = (
        "CENTRAL TESTING LABORATORY\n"
        "Test Report No: TR-2024-001\n"
        "Operating Voltage: 230 V\n"
        "Insulation Resistance: 50 MOhm\n"
    )
    doc = extract_business_document(doc_text, document_id="doc_central")

    req_v = StandardTechnicalRequirement(
        requirement_id="R1",
        standard_id="std_1",
        requirement_text="Operating voltage requirement",
        standard_number="IS 1",
        parameter_name="operating_voltage",
        threshold=230.0,
        unit="V",
    )
    req_missing = StandardTechnicalRequirement(
        requirement_id="R2",
        standard_id="std_1",
        requirement_text="Hydraulic burst pressure requirement",
        standard_number="IS 1",
        parameter_name="hydraulic_burst_pressure",
        threshold=50.0,
        unit="bar",
    )

    report = build_evidence_gap_report([req_v, req_missing], [doc])
    assert report.total_requirements == 2
    assert report.supported_count == 1
    assert report.missing_count == 1
    assert report.overall_evidence_state == "GAPS_DETECTED"
    assert len(report.audit_warnings) > 0


def test_document_intelligence_benchmark_dataset():
    """Run all scenarios from the evaluation dataset and verify expectations."""
    dataset_path = Path("data/evaluation/document_intelligence_eval_dataset.json")
    assert dataset_path.exists()

    with open(dataset_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    scenarios = data.get("scenarios", [])
    assert len(scenarios) >= 10

    for sc in scenarios:
        sc_id = sc["id"]
        doc_text = sc.get("document_text")
        fname = sc.get("filename")

        if doc_text:
            doc = extract_business_document(doc_text, filename=fname, as_of_date="2024-06-01")

            if "expected_doc_type" in sc:
                assert doc.document_type.value == sc["expected_doc_type"], (
                    f"Scenario {sc_id} expected type {sc['expected_doc_type']}, got {doc.document_type.value}"
                )

            if "expected_is_expired" in sc:
                is_expired = (doc.status == "EXPIRED")
                assert is_expired == sc["expected_is_expired"], (
                    f"Scenario {sc_id} expected expired={sc['expected_is_expired']}, got {is_expired}"
                )

            if "requirement" in sc:
                req_data = sc["requirement"]
                op_str = req_data.get("operator", "=")
                op = ComparisonOperator.GE if op_str in (">=", "GE") else (
                    ComparisonOperator.LE if op_str in ("<=", "LE") else ComparisonOperator.EQ
                )
                req = StandardTechnicalRequirement(
                    requirement_id=req_data["requirement_id"],
                    standard_id="std_test",
                    requirement_text=req_data["parameter_name"],
                    standard_number="IS TEST",
                    parameter_name=req_data["parameter_name"],
                    operator=op,
                    threshold=req_data.get("nominal_value") if isinstance(req_data.get("nominal_value"), (int, float)) else None,
                )
                match = evaluate_requirement_against_documents(req, [doc], as_of_date="2024-06-01")
                assert match.status.value == sc["expected_status"], (
                    f"Scenario {sc_id} expected match status {sc['expected_status']}, got {match.status.value}"
                )
