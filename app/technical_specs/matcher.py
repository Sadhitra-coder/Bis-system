"""
app/technical_specs/matcher.py

Safe Numeric and Characterization Matcher for Technical Specifications (Phase 12).

DESIGN PRINCIPLES:
  - Compares product technical specifications against BIS standard technical requirements.
  - Strictly characterization matching: MATCH, MISMATCH, PARTIAL_MATCH, MISSING, UNSPECIFIED, UNVERIFIABLE.
  - STRICTLY FORBIDDEN to use 'COMPLIANT' or 'NON_COMPLIANT'.
  - Unit-safe: Only compares compatible physical dimensions. Incompatible units -> UNVERIFIABLE.
  - Safe range, tolerance, and inequality comparisons.
  - Preserves full evidence provenance and temporal status.
"""

import hashlib
import math
from typing import Any, Dict, List, Optional

from app.technical_specs.extractor import (
    are_units_compatible,
    get_unit_details,
    normalize_numeric_value,
)
from app.technical_specs.models import (
    ComparisonOperator,
    MatchState,
    SpecificationComparisonResult,
    StandardTechnicalRequirement,
    TechnicalAnalysisReport,
    TechnicalParameter,
    TechnicalSpecification,
)


def compare_parameter_to_requirement(
    param: Optional[TechnicalParameter],
    req: StandardTechnicalRequirement,
) -> SpecificationComparisonResult:
    """
    Compares a single product parameter against a standard requirement.
    Produces an evidence-backed SpecificationComparisonResult without legal claims.
    """
    res_id = f"cmp_{hashlib.sha256((req.requirement_id + (param.parameter_name if param else 'missing')).encode('utf-8')).hexdigest()[:12]}"
    page_prov = {"page_start": req.page_start, "page_end": req.page_end}

    # 1. Parameter is missing in specification
    if param is None or (param.value is None and param.min_value is None and param.max_value is None):
        return SpecificationComparisonResult(
            result_id=res_id,
            parameter_name=req.parameter_name,
            match_state=MatchState.MISSING,
            spec_parameter=param,
            standard_requirement=req,
            reason=f"Parameter '{req.parameter_name}' is specified in the standard requirement ({req.operator.value} {req.threshold or req.min_threshold} {req.unit or ''}) but is missing from the product specification.",
            evidence_chunk_ids=req.source_chunk_ids,
            standard_id=req.standard_id,
            standard_number=req.standard_number,
            clause_id=req.clause_id,
            page_provenance=page_prov,
            temporal_status=req.temporal_status,
            verification_required=(req.temporal_status in ("WITHDRAWN", "SUPERSEDED", "TEMPORALLY_UNCERTAIN")),
            verification_reason=f"Temporal status is '{req.temporal_status}'." if req.temporal_status in ("WITHDRAWN", "SUPERSEDED", "TEMPORALLY_UNCERTAIN") else None,
        )

    # 2. Check unit compatibility
    if req.unit and param.unit:
        if not are_units_compatible(param.unit, req.unit):
            return SpecificationComparisonResult(
                result_id=res_id,
                parameter_name=req.parameter_name,
                match_state=MatchState.UNVERIFIABLE,
                spec_parameter=param,
                standard_requirement=req,
                reason=f"Incompatible units: specification unit '{param.unit}' cannot be compared against requirement unit '{req.unit}'.",
                evidence_chunk_ids=req.source_chunk_ids,
                standard_id=req.standard_id,
                standard_number=req.standard_number,
                clause_id=req.clause_id,
                page_provenance=page_prov,
                temporal_status=req.temporal_status,
                verification_required=True,
                verification_reason="Incompatible physical dimensions prevent automated mathematical comparison.",
            )

    # 3. Check for non-numeric specification value when requirement expects numeric
    spec_val = None
    if param.normalized_value is not None:
        spec_val = param.normalized_value
    elif isinstance(param.value, (int, float)):
        norm_v, _ = normalize_numeric_value(float(param.value), param.unit)
        spec_val = norm_v
    elif param.min_value is not None or param.max_value is not None:
        pass
    else:
        # Value is non-numeric string (e.g. "high", "normal", "stainless steel")
        # Check if requirement is text-based comparison
        if isinstance(param.value, str) and req.operator in (ComparisonOperator.EQ, ComparisonOperator.CONTAINS):
            if req.threshold is None:
                # Text match
                if param.value.strip().lower() in req.requirement_text.lower():
                    return SpecificationComparisonResult(
                        result_id=res_id,
                        parameter_name=req.parameter_name,
                        match_state=MatchState.MATCH,
                        spec_parameter=param,
                        standard_requirement=req,
                        reason=f"Textual characteristic matches the cited requirement clause: '{param.value}'.",
                        evidence_chunk_ids=req.source_chunk_ids,
                        standard_id=req.standard_id,
                        standard_number=req.standard_number,
                        clause_id=req.clause_id,
                        page_provenance=page_prov,
                        temporal_status=req.temporal_status,
                    )
                else:
                    return SpecificationComparisonResult(
                        result_id=res_id,
                        parameter_name=req.parameter_name,
                        match_state=MatchState.MISMATCH,
                        spec_parameter=param,
                        standard_requirement=req,
                        reason=f"Textual characteristic '{param.value}' does not match cited requirement requirement.",
                        evidence_chunk_ids=req.source_chunk_ids,
                        standard_id=req.standard_id,
                        standard_number=req.standard_number,
                        clause_id=req.clause_id,
                        page_provenance=page_prov,
                        temporal_status=req.temporal_status,
                    )

        return SpecificationComparisonResult(
            result_id=res_id,
            parameter_name=req.parameter_name,
            match_state=MatchState.UNVERIFIABLE,
            spec_parameter=param,
            standard_requirement=req,
            reason=f"Parameter value '{param.value}' is non-numeric and cannot be mathematically compared against threshold {req.threshold or req.min_threshold}.",
            evidence_chunk_ids=req.source_chunk_ids,
            standard_id=req.standard_id,
            standard_number=req.standard_number,
            clause_id=req.clause_id,
            page_provenance=page_prov,
            temporal_status=req.temporal_status,
            verification_required=True,
            verification_reason="Qualitative or unparseable value requires manual engineering verification.",
        )

    # 4. Normalize requirement threshold to canonical unit
    req_thresh = None
    if req.threshold is not None:
        req_thresh, _ = normalize_numeric_value(req.threshold, req.unit)
    req_min = None
    if req.min_threshold is not None:
        req_min, _ = normalize_numeric_value(req.min_threshold, req.unit)
    req_max = None
    if req.max_threshold is not None:
        req_max, _ = normalize_numeric_value(req.max_threshold, req.unit)

    # 5. Tolerance normalization
    spec_tol = 0.0
    if param.tolerance is not None:
        u_det = get_unit_details(param.unit) if param.unit else None
        factor = u_det[1] if u_det else 1.0
        spec_tol = param.tolerance * factor

    if req.tolerance is not None and req_min is None and req_thresh is not None:
        req_u_det = get_unit_details(req.unit) if req.unit else None
        req_factor = req_u_det[1] if req_u_det else 1.0
        req_tol = req.tolerance * req_factor
        req_min = req_thresh - req_tol
        req_max = req_thresh + req_tol

    # 6. Evaluate based on operator
    verification_req = (req.temporal_status in ("WITHDRAWN", "SUPERSEDED", "TEMPORALLY_UNCERTAIN"))
    verif_reason = f"Temporal status is '{req.temporal_status}'." if verification_req else None

    # Helper epsilon for float comparisons
    eps = 1e-6

    if req.operator in (ComparisonOperator.LE, ComparisonOperator.LT):
        limit = req_thresh if req_thresh is not None else req_max
        if limit is None:
            state = MatchState.UNVERIFIABLE
            reason = "Requirement upper limit threshold is unspecified."
        elif spec_val is not None:
            max_spec = spec_val + spec_tol
            if max_spec <= limit + eps:
                state = MatchState.MATCH
                reason = f"Parameter value {param.value} {param.unit or ''} matches the cited requirement ({req.operator.value} {req.threshold} {req.unit or ''})."
            elif spec_val <= limit + eps and max_spec > limit:
                state = MatchState.PARTIAL_MATCH
                reason = f"Nominal value {spec_val} matches threshold <= {limit}, but upper tolerance window ({max_spec}) exceeds the requirement threshold."
            else:
                state = MatchState.MISMATCH
                reason = f"Parameter value {param.value} {param.unit or ''} exceeds the cited requirement upper bound ({req.operator.value} {req.threshold} {req.unit or ''})."
        else:
            state = MatchState.UNVERIFIABLE
            reason = "Specification value is unspecified or invalid."

    elif req.operator in (ComparisonOperator.GE, ComparisonOperator.GT):
        limit = req_thresh if req_thresh is not None else req_min
        if limit is None:
            state = MatchState.UNVERIFIABLE
            reason = "Requirement lower limit threshold is unspecified."
        elif spec_val is not None:
            min_spec = spec_val - spec_tol
            if min_spec >= limit - eps:
                state = MatchState.MATCH
                reason = f"Parameter value {param.value} {param.unit or ''} matches the cited requirement ({req.operator.value} {req.threshold} {req.unit or ''})."
            elif spec_val >= limit - eps and min_spec < limit:
                state = MatchState.PARTIAL_MATCH
                reason = f"Nominal value {spec_val} matches threshold >= {limit}, but lower tolerance window ({min_spec}) drops below requirement threshold."
            else:
                state = MatchState.MISMATCH
                reason = f"Parameter value {param.value} {param.unit or ''} falls below the cited requirement lower bound ({req.operator.value} {req.threshold} {req.unit or ''})."
        else:
            state = MatchState.UNVERIFIABLE
            reason = "Specification value is unspecified or invalid."

    elif req.operator == ComparisonOperator.RANGE:
        if req_min is None or req_max is None:
            state = MatchState.UNVERIFIABLE
            reason = "Requirement range bounds are incompletely specified."
        elif spec_val is not None:
            min_spec = spec_val - spec_tol
            max_spec = spec_val + spec_tol
            if min_spec >= req_min - eps and max_spec <= req_max + eps:
                state = MatchState.MATCH
                reason = f"Parameter value {param.value} {param.unit or ''} is within the required range [{req.min_threshold}, {req.max_threshold}] {req.unit or ''}."
            elif (min_spec <= req_max and max_spec >= req_min):
                state = MatchState.PARTIAL_MATCH
                reason = f"Parameter range [{min_spec}, {max_spec}] partially overlaps the required range [{req_min}, {req_max}]."
            else:
                state = MatchState.MISMATCH
                reason = f"Parameter value {param.value} {param.unit or ''} is outside the required range [{req.min_threshold}, {req.max_threshold}] {req.unit or ''}."
        elif param.min_value is not None and param.max_value is not None:
            p_min, _ = normalize_numeric_value(param.min_value, param.unit)
            p_max, _ = normalize_numeric_value(param.max_value, param.unit)
            if p_min >= req_min - eps and p_max <= req_max + eps:
                state = MatchState.MATCH
                reason = f"Parameter range [{param.min_value}, {param.max_value}] {param.unit or ''} matches required range [{req.min_threshold}, {req.max_threshold}] {req.unit or ''}."
            elif (p_min <= req_max and p_max >= req_min):
                state = MatchState.PARTIAL_MATCH
                reason = f"Parameter range [{param.min_value}, {param.max_value}] partially overlaps required range [{req_min}, {req_max}]."
            else:
                state = MatchState.MISMATCH
                reason = f"Parameter range [{param.min_value}, {param.max_value}] {param.unit or ''} falls outside required range [{req.min_threshold}, {req.max_threshold}] {req.unit or ''}."
        else:
            state = MatchState.UNVERIFIABLE
            reason = "Specification value is unspecified."

    elif req.operator == ComparisonOperator.EQ:
        # Exact comparison or tolerance range
        if req_min is not None and req_max is not None:
            # Standard specified nominal with tolerance
            if spec_val is not None:
                if req_min - eps <= spec_val <= req_max + eps:
                    state = MatchState.MATCH
                    reason = f"Parameter value {param.value} {param.unit or ''} matches required nominal threshold with tolerance [{req.min_threshold}, {req.max_threshold}] {req.unit or ''}."
                else:
                    state = MatchState.MISMATCH
                    reason = f"Parameter value {param.value} {param.unit or ''} does not match required threshold with tolerance [{req.min_threshold}, {req.max_threshold}] {req.unit or ''}."
            else:
                state = MatchState.UNVERIFIABLE
                reason = "Specification value is unspecified."
        elif req_thresh is not None and spec_val is not None:
            if math.isclose(spec_val, req_thresh, rel_tol=1e-4, abs_tol=1e-4):
                state = MatchState.MATCH
                reason = f"Parameter value {param.value} {param.unit or ''} matches cited requirement exact value {req.threshold} {req.unit or ''}."
            else:
                state = MatchState.MISMATCH
                reason = f"Parameter value {param.value} {param.unit or ''} does not match cited requirement exact value {req.threshold} {req.unit or ''}."
        else:
            state = MatchState.UNVERIFIABLE
            reason = "Requirement threshold is unspecified."
    else:
        state = MatchState.UNVERIFIABLE
        reason = f"Unsupported comparison operator: {req.operator}"

    return SpecificationComparisonResult(
        result_id=res_id,
        parameter_name=req.parameter_name,
        match_state=state,
        spec_parameter=param,
        standard_requirement=req,
        reason=reason,
        evidence_chunk_ids=req.source_chunk_ids,
        standard_id=req.standard_id,
        standard_number=req.standard_number,
        clause_id=req.clause_id,
        page_provenance=page_prov,
        temporal_status=req.temporal_status,
        verification_required=verification_req,
        verification_reason=verif_reason,
    )


def analyze_technical_specification(
    spec: TechnicalSpecification,
    requirements: List[StandardTechnicalRequirement],
    product_context_id: Optional[str] = None,
) -> TechnicalAnalysisReport:
    """
    Compares a product technical specification against a list of standard requirements.
    Groups results into requirement_matches and technical_gaps.
    Ensures zero unsupported compliance claims.
    """
    report_id = f"tech_rpt_{hashlib.sha256((spec.specification_id + str(len(requirements))).encode('utf-8')).hexdigest()[:12]}"
    matches: List[SpecificationComparisonResult] = []
    gaps: List[SpecificationComparisonResult] = []
    verif_reasons: List[str] = []

    # Map spec parameters for fast case-insensitive lookup
    spec_param_map = {k.strip().lower(): v for k, v in spec.parameters.items()}

    for req in requirements:
        req_pname_lower = req.parameter_name.strip().lower()
        # Find matching parameter in spec
        matched_param = spec_param_map.get(req_pname_lower)

        # Fallback substring lookup
        if not matched_param:
            for k, p in spec_param_map.items():
                if k in req_pname_lower or req_pname_lower in k:
                    matched_param = p
                    break

        # Fallback synonym / token overlap lookup
        if not matched_param:
            SYNONYM_GROUPS = [
                {"capacity", "volume", "storage"},
                {"temperature", "temp"},
                {"pressure"},
                {"thickness"},
                {"diameter"},
                {"efficiency"},
                {"voltage"},
                {"current"},
                {"power"},
                {"frequency"},
                {"conductivity", "resistance"},
                {"scale", "range"},
            ]
            req_tokens = set(req_pname_lower.split())
            stop_words = {"the", "of", "and", "shall", "be", "at", "not", "nominal", "maximum", "minimum", "operating", "working", "allowable"}
            for k, p in spec_param_map.items():
                k_tokens = set(k.split())
                common = (req_tokens & k_tokens) - stop_words
                if common:
                    matched_param = p
                    break
                for syn in SYNONYM_GROUPS:
                    if (req_tokens & syn) and (k_tokens & syn):
                        matched_param = p
                        break
                if matched_param:
                    break

        res = compare_parameter_to_requirement(matched_param, req)

        if res.verification_required and res.verification_reason:
            verif_reasons.append(f"{req.parameter_name}: {res.verification_reason}")

        if res.match_state in (MatchState.MATCH, MatchState.PARTIAL_MATCH):
            matches.append(res)
        else:
            gaps.append(res)

    is_verif = bool(verif_reasons) or any(g.match_state == MatchState.UNVERIFIABLE for g in gaps)
    match_count = len([m for m in matches if m.match_state == MatchState.MATCH])
    partial_count = len([m for m in matches if m.match_state == MatchState.PARTIAL_MATCH])
    mismatch_count = len([g for g in gaps if g.match_state == MatchState.MISMATCH])
    missing_count = len([g for g in gaps if g.match_state == MatchState.MISSING])

    summary = (
        f"Technical specification analysis against {len(requirements)} requirement(s): "
        f"{match_count} match(es), {partial_count} partial match(es), "
        f"{mismatch_count} mismatch(es), {missing_count} missing parameter(s). "
        f"{'Verification required due to unresolved conditions.' if is_verif else 'Analysis complete.'}"
    )

    return TechnicalAnalysisReport(
        analysis_id=report_id,
        product_context_id=product_context_id,
        technical_specification=spec,
        requirement_matches=matches,
        technical_gaps=gaps,
        verification_required=is_verif,
        verification_reasons=verif_reasons,
        summary=summary,
    )
