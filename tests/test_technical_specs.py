"""
tests/test_technical_specs.py

Comprehensive Unit, Adversarial, and Evaluation Tests for Phase 12
(Technical Specification Analyzer).
"""

import json
from pathlib import Path
import pytest

from app.technical_specs.models import (
    ComparisonOperator,
    MatchState,
    SpecificationComparisonResult,
    StandardTechnicalRequirement,
    TechnicalAnalysisReport,
    TechnicalParameter,
    TechnicalSpecification,
)
from app.technical_specs.extractor import (
    are_units_compatible,
    extract_parameter_from_text,
    extract_requirements_from_clause_text,
    extract_technical_specification,
    get_unit_details,
    normalize_numeric_value,
    normalize_unit_string,
)
from app.technical_specs.matcher import (
    analyze_technical_specification,
    compare_parameter_to_requirement,
)


# ============================================================
# 1. UNIT NORMALIZATION & COMPATIBILITY TESTS
# ============================================================

def test_unit_normalization_volume():
    val_l, u_l = normalize_numeric_value(500.0, "L")
    assert val_l == 500.0
    assert u_l == "L"

    val_ml, u_ml = normalize_numeric_value(5000.0, "ml")
    assert val_ml == 5.0
    assert u_ml == "L"

    val_m3, u_m3 = normalize_numeric_value(2.0, "m3")
    assert val_m3 == 2000.0
    assert u_m3 == "L"


def test_unit_normalization_length():
    val_cm, u_cm = normalize_numeric_value(15.0, "cm")
    assert val_cm == 150.0
    assert u_cm == "mm"

    val_in, u_in = normalize_numeric_value(2.0, "inch")
    assert val_in == 50.8
    assert u_in == "mm"


def test_unit_normalization_mass_and_pressure():
    val_g, u_g = normalize_numeric_value(2500.0, "g")
    assert val_g == 2.5
    assert u_g == "kg"

    val_kpa, u_kpa = normalize_numeric_value(100.0, "kPa")
    assert val_kpa == 1.0
    assert u_kpa == "bar"


def test_unit_compatibility():
    assert are_units_compatible("L", "ml") is True
    assert are_units_compatible("mm", "cm") is True
    assert are_units_compatible("kg", "g") is True
    assert are_units_compatible("bar", "kPa") is True

    # Incompatible dimensions
    assert are_units_compatible("L", "mm") is False
    assert are_units_compatible("kg", "bar") is False
    assert are_units_compatible("°C", "V") is False


# ============================================================
# 2. SPECIFICATION EXTRACTION TESTS (ZERO-INFERENCE)
# ============================================================

def test_extract_parameter_with_tolerance():
    text = "thickness: 10 ± 0.5 mm"
    param = extract_parameter_from_text(text)
    assert param is not None
    assert param.nominal_value == 10.0
    assert param.tolerance == 0.5
    assert param.min_value == 9.5
    assert param.max_value == 10.5
    assert param.unit == "mm"
    assert param.normalized_unit == "mm"


def test_extract_parameter_zero_inference_tolerance():
    # When tolerance is unstated, tolerance MUST be None, never fabricated
    text = "capacity: 500 L"
    param = extract_parameter_from_text(text)
    assert param is not None
    assert param.nominal_value == 500.0
    assert param.tolerance is None
    assert param.min_value is None
    assert param.max_value is None
    assert param.unit == "L"


def test_extract_technical_specification_multiline():
    raw = """
    capacity: 500 L;
    operating temperature: 55 °C;
    material: Stainless Steel;
    dimensions: 1000 x 500 mm
    """
    spec = extract_technical_specification(text=raw, product_name="Test Tank")
    assert spec.product_name == "Test Tank"
    assert spec.material == "Stainless Steel"
    assert "capacity" in spec.parameters
    assert spec.parameters["capacity"].normalized_value == 500.0
    assert "operating temperature" in spec.parameters
    assert spec.parameters["operating temperature"].normalized_value == 55.0


# ============================================================
# 3. REQUIREMENT EXTRACTION FROM CLAUSES
# ============================================================

def test_extract_requirements_from_clause():
    clause = "The nominal capacity shall not be less than 500 L. Maximum operating temperature shall not exceed 40 °C."
    reqs = extract_requirements_from_clause_text(
        clause_text=clause,
        standard_id="std_is_12701",
        standard_number="IS 12701",
        clause_id="clause_4_1",
        page_start=3,
        page_end=3,
    )
    assert len(reqs) == 2

    # Check capacity requirement (GE)
    r_cap = [r for r in reqs if "capacity" in r.parameter_name.lower()][0]
    assert r_cap.operator == ComparisonOperator.GE
    assert r_cap.threshold == 500.0
    assert r_cap.unit == "L"
    assert r_cap.page_start == 3

    # Check temperature requirement (LE)
    r_temp = [r for r in reqs if "temperature" in r.parameter_name.lower()][0]
    assert r_temp.operator == ComparisonOperator.LE
    assert r_temp.threshold == 40.0
    assert r_temp.unit == "°C"


# ============================================================
# 4. SAFE NUMERIC MATCHING TESTS
# ============================================================

def test_comparison_match_le():
    req = StandardTechnicalRequirement(
        requirement_id="req_01",
        standard_id="std_01",
        requirement_text="Temperature shall not exceed 40 °C",
        parameter_name="temperature",
        operator=ComparisonOperator.LE,
        threshold=40.0,
        unit="°C",
    )
    # 35 °C matches <= 40 °C
    param_ok = TechnicalParameter(parameter_name="temperature", value=35.0, normalized_value=35.0, unit="°C")
    res_ok = compare_parameter_to_requirement(param_ok, req)
    assert res_ok.match_state == MatchState.MATCH
    assert "matches the cited requirement" in res_ok.reason
    assert "compliant" not in res_ok.reason.lower()

    # 45 °C mismatches <= 40 °C
    param_bad = TechnicalParameter(parameter_name="temperature", value=45.0, normalized_value=45.0, unit="°C")
    res_bad = compare_parameter_to_requirement(param_bad, req)
    assert res_bad.match_state == MatchState.MISMATCH
    assert "exceeds the cited requirement" in res_bad.reason


def test_comparison_tolerance_partial_match():
    # Limit <= 11 bar. Spec is 10 ± 2 bar (min=8, max=12).
    # Nominal 10 is <= 11, but upper window 12 exceeds 11.
    req = StandardTechnicalRequirement(
        requirement_id="req_press",
        standard_id="std_press",
        requirement_text="Working pressure shall not exceed 11 bar",
        parameter_name="pressure",
        operator=ComparisonOperator.LE,
        threshold=11.0,
        unit="bar",
    )
    param = TechnicalParameter(
        parameter_name="pressure",
        value=10.0,
        normalized_value=10.0,
        unit="bar",
        tolerance=2.0,
    )
    res = compare_parameter_to_requirement(param, req)
    assert res.match_state == MatchState.PARTIAL_MATCH
    assert "tolerance window" in res.reason


def test_comparison_incompatible_units():
    req = StandardTechnicalRequirement(
        requirement_id="req_incompat",
        standard_id="std_01",
        requirement_text="Pressure shall not exceed 10 bar",
        parameter_name="pressure",
        operator=ComparisonOperator.LE,
        threshold=10.0,
        unit="bar",
    )
    param = TechnicalParameter(
        parameter_name="pressure",
        value=200.0,
        normalized_value=200.0,
        unit="mm",  # Length unit for pressure parameter
    )
    res = compare_parameter_to_requirement(param, req)
    assert res.match_state == MatchState.UNVERIFIABLE
    assert res.verification_required is True
    assert "Incompatible units" in res.reason


def test_comparison_missing_parameter():
    req = StandardTechnicalRequirement(
        requirement_id="req_miss",
        standard_id="std_01",
        requirement_text="Efficiency shall not be less than 85 %",
        parameter_name="efficiency",
        operator=ComparisonOperator.GE,
        threshold=85.0,
        unit="%",
    )
    res = compare_parameter_to_requirement(None, req)
    assert res.match_state == MatchState.MISSING
    assert "missing from the product specification" in res.reason


def test_comparison_temporal_safety():
    # If requirement is from a WITHDRAWN standard, verification_required must be True
    req = StandardTechnicalRequirement(
        requirement_id="req_temp",
        standard_id="std_old",
        requirement_text="Thickness shall be 10 mm",
        parameter_name="thickness",
        operator=ComparisonOperator.EQ,
        threshold=10.0,
        unit="mm",
        temporal_status="WITHDRAWN",
    )
    param = TechnicalParameter(parameter_name="thickness", value=10.0, normalized_value=10.0, unit="mm")
    res = compare_parameter_to_requirement(param, req)
    assert res.match_state == MatchState.MATCH
    assert res.verification_required is True
    assert "WITHDRAWN" in res.verification_reason


# ============================================================
# 5. ADVERSARIAL INTEGRITY & ZERO FALSE COMPLIANCE TESTS
# ============================================================

def test_adversarial_rejection_of_compliant_word():
    spec = extract_technical_specification("capacity: 500 L", product_name="Tank")
    req = StandardTechnicalRequirement(
        requirement_id="r1",
        standard_id="s1",
        requirement_text="capacity shall not be less than 500 L",
        parameter_name="capacity",
        operator=ComparisonOperator.GE,
        threshold=500.0,
        unit="L",
    )
    report = analyze_technical_specification(spec, [req])

    # Rule: Never declare legal compliance or use 'COMPLIANT' / 'NON_COMPLIANT'
    for m in report.requirement_matches:
        assert m.match_state != "COMPLIANT"
        assert "legally compliant" not in m.reason.lower()
        assert "certified" not in m.reason.lower()
        assert "approved" not in m.reason.lower()

    for g in report.technical_gaps:
        assert g.match_state != "NON_COMPLIANT"


def test_adversarial_qualitative_string_value():
    # "heat resistance: high" cannot be compared with numeric threshold
    req = StandardTechnicalRequirement(
        requirement_id="r_num",
        standard_id="s1",
        requirement_text="Thermal conductivity <= 0.04 W/mK",
        parameter_name="heat resistance",
        operator=ComparisonOperator.LE,
        threshold=0.04,
        unit="W/mK",
    )
    param = TechnicalParameter(parameter_name="heat resistance", value="high", unit=None)
    res = compare_parameter_to_requirement(param, req)
    assert res.match_state == MatchState.UNVERIFIABLE
    assert res.verification_required is True


# ============================================================
# 6. EVALUATION DATASET BENCHMARK (10 SCENARIOS)
# ============================================================

def test_evaluation_dataset_10_scenarios():
    eval_path = Path(__file__).resolve().parent.parent / "data" / "evaluation" / "technical_spec_eval_dataset.json"
    assert eval_path.exists(), "Evaluation dataset must exist"

    with open(eval_path, "r", encoding="utf-8") as f:
        scenarios = json.load(f)

    assert len(scenarios) >= 10

    unsupported_compliance_count = 0
    correct_classification_count = 0

    for sc in scenarios:
        spec = extract_technical_specification(sc["specification_text"], product_name=sc["product_name"])
        reqs = extract_requirements_from_clause_text(
            clause_text=sc["clause_requirement"],
            standard_id=f"std_{sc['standard_number']}",
            standard_number=sc["standard_number"],
        )
        if not reqs:
            # Fallback direct requirement if regex didn't trigger
            req = StandardTechnicalRequirement(
                requirement_id="req_eval",
                standard_id=f"std_{sc['standard_number']}",
                requirement_text=sc["clause_requirement"],
                parameter_name=sc["expected_parameter"],
                operator=ComparisonOperator.EQ,
            )
            reqs = [req]

        report = analyze_technical_specification(spec, reqs)

        # Invariant check: FALSE UNSUPPORTED COMPLIANCE RATE = 0%
        for m in report.requirement_matches:
            if "compliant" in m.reason.lower() or "certified" in m.reason.lower():
                unsupported_compliance_count += 1

        all_results = report.requirement_matches + report.technical_gaps
        matched_result = None
        for r in all_results:
            if sc["expected_parameter"].lower() in r.parameter_name.lower():
                matched_result = r
                break
        if not matched_result and all_results:
            matched_result = all_results[0]

        if matched_result and matched_result.match_state.value == sc["expected_state"]:
            correct_classification_count += 1

    # Safety invariant: strictly 0% unsupported compliance conclusions
    assert unsupported_compliance_count == 0

    # Accuracy check across all 10 evaluation scenarios
    accuracy = correct_classification_count / len(scenarios)
    assert accuracy >= 0.90, f"Expected >= 90% accuracy on eval dataset, got {accuracy:.2f}"
