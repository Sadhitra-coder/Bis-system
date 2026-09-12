"""
app/tender_analysis/matcher.py

Cross-Standard Tender Gap and Tripartite Analysis Engine (Phase 13).

DESIGN PRINCIPLES:
  - Links buyer/tender requirements against BIS standard requirements and product specifications.
  - Multi-standard aggregation: A tender requirement can be supported across multiple standards.
  - States: MATCH, GAP, CONFLICT, UNSPECIFIED, UNVERIFIED.
  - Tripartite synthesis: Customer Requirement vs Product Specification vs BIS Standard Evidence.
  - Temporal awareness: Superseded or uncertain standards force verification_required.
  - ZERO legal compliance claims: strictly characterization and customer gap reporting.
"""

import hashlib
from typing import Any, Dict, List, Optional

from app.technical_specs.matcher import compare_parameter_to_requirement
from app.technical_specs.models import (
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


def match_tender_requirement_to_standards(
    tender_req: TenderRequirement,
    standard_requirements: List[StandardTechnicalRequirement],
) -> List[TenderStandardMatch]:
    """
    Links a single tender requirement against candidate standard technical requirements.
    A single customer requirement may link to one or more standards.
    """
    matches: List[TenderStandardMatch] = []
    t_pname_lower = tender_req.parameter_name.strip().lower()

    SYNONYM_GROUPS = [
        {"capacity", "volume", "storage"},
        {"temperature", "temp", "thermal"},
        {"pressure", "hydrostatic"},
        {"thickness"},
        {"diameter"},
        {"efficiency"},
        {"noise", "acoustic"},
        {"material", "grade", "polyethylene", "steel"},
        {"test", "testing", "inspection"},
        {"certification", "conformance", "license", "licence", "isi"},
        {"dimension", "size", "width", "length", "height"},
        {"warranty"},
        {"delivery"},
        {"weight"},
    ]

    stop_words = {
        "the", "of", "and", "shall", "be", "at", "not", "is", "a", "an", "for", "with", "in", "to",
        "exceed", "least", "less", "than", "between", "maximum", "minimum", "must", "as", "per",
        "from", "unit", "total", "only", "all", "strictly", "required", "submitted", "maintained",
        "records", "testing", "order", "timeline", "valid", "routine"
    }
    t_tokens = (set(t_pname_lower.split()) | set(tender_req.text.lower().split())) - stop_words

    relevant_reqs = []
    for s_req in standard_requirements:
        s_pname_lower = s_req.parameter_name.strip().lower()
        s_tokens = (set(s_pname_lower.split()) | set(s_req.requirement_text.lower().split())) - stop_words

        is_rel = False
        if t_pname_lower in s_pname_lower or s_pname_lower in t_pname_lower:
            is_rel = True
        elif any(len(tok) >= 4 and tok in s_tokens for tok in t_tokens):
            is_rel = True
        elif any(syn & t_tokens and syn & s_tokens for syn in SYNONYM_GROUPS):
            is_rel = True
        elif tender_req.category and (tender_req.category in s_pname_lower or tender_req.category in s_req.requirement_text.lower()):
            is_rel = True

        if is_rel:
            relevant_reqs.append(s_req)

    if not relevant_reqs:
        # Not found in candidate standard evidence -> GAP
        m_id = f"tmatch_{hashlib.sha256((tender_req.requirement_id + 'not_found').encode('utf-8')).hexdigest()[:12]}"
        std_id = standard_requirements[0].standard_id if standard_requirements else "none"
        std_num = standard_requirements[0].standard_number or "BIS Standards" if standard_requirements else "Unknown"
        temp_st = standard_requirements[0].temporal_status if standard_requirements else None
        is_temp_v = temp_st in ("WITHDRAWN", "SUPERSEDED", "TEMPORALLY_UNCERTAIN")

        matches.append(
            TenderStandardMatch(
                match_id=m_id,
                tender_requirement_id=tender_req.requirement_id,
                standard_id=std_id,
                standard_number=std_num,
                link_state=StandardLinkState.NOT_FOUND_IN_STANDARD,
                gap_state=TenderGapState.GAP,
                reason=f"Customer requirement '{tender_req.text}' is not explicitly defined in the candidate standard requirements.",
                supporting_evidence_chunk_ids=[],
                temporal_status=temp_st,
                verification_required=is_temp_v,
                verification_reason=f"Temporal status is '{temp_st}'." if is_temp_v else None,
            )
        )
        return matches

    for s_req in relevant_reqs:
        m_id = f"tmatch_{hashlib.sha256((tender_req.requirement_id + s_req.requirement_id).encode('utf-8')).hexdigest()[:12]}"

        # Check if text-based category match (certification, documentation, material)
        is_text_cat = tender_req.category in ("certification", "documentation", "material") or (
            tender_req.parameter and tender_req.parameter.unit is None and s_req.unit is None
        )

        if is_text_cat and (not tender_req.parameter or not tender_req.parameter.unit):
            t_words = [w for w in tender_req.text.lower().split() if len(w) > 3 and w not in stop_words]
            is_match = any(w in s_req.requirement_text.lower() for w in t_words) or (tender_req.category and tender_req.category in s_req.requirement_text.lower())
            link_st = StandardLinkState.SUPPORTED_BY_STANDARD if is_match else StandardLinkState.NOT_FOUND_IN_STANDARD
            gap_st = TenderGapState.MATCH if is_match else TenderGapState.GAP
            reason = f"Customer clause linked to standard requirement: {s_req.requirement_text}"
            is_temp_verif = s_req.temporal_status in ("WITHDRAWN", "SUPERSEDED", "TEMPORALLY_UNCERTAIN")

            matches.append(
                TenderStandardMatch(
                    match_id=m_id,
                    tender_requirement_id=tender_req.requirement_id,
                    standard_id=s_req.standard_id,
                    standard_number=s_req.standard_number or s_req.standard_id,
                    clause_id=s_req.clause_id,
                    clause_number=s_req.clause_number,
                    clause_title=s_req.clause_title,
                    standard_requirement=s_req,
                    link_state=link_st,
                    gap_state=gap_st,
                    reason=reason,
                    supporting_evidence_chunk_ids=s_req.source_chunk_ids,
                    page_start=s_req.page_start,
                    page_end=s_req.page_end,
                    temporal_status=s_req.temporal_status,
                    confidence=s_req.confidence,
                    verification_required=is_temp_verif,
                    verification_reason=f"Temporal status is '{s_req.temporal_status}'." if is_temp_verif else None,
                )
            )
        elif tender_req.parameter:
            cmp_res = compare_parameter_to_requirement(tender_req.parameter, s_req)

            if cmp_res.match_state == MatchState.MATCH:
                link_st = StandardLinkState.SUPPORTED_BY_STANDARD
                gap_st = TenderGapState.MATCH
                reason = f"Customer parameter requirement matches cited standard requirement: {cmp_res.reason}"
            elif cmp_res.match_state == MatchState.PARTIAL_MATCH:
                link_st = StandardLinkState.PARTIALLY_SUPPORTED
                gap_st = TenderGapState.MATCH
                reason = f"Customer parameter requirement is partially supported by standard: {cmp_res.reason}"
            elif cmp_res.match_state == MatchState.MISMATCH:
                link_st = StandardLinkState.CONFLICTS_WITH_STANDARD
                gap_st = TenderGapState.CONFLICT
                reason = f"Customer parameter requirement conflicts with standard threshold: {cmp_res.reason}"
            elif cmp_res.match_state == MatchState.UNVERIFIABLE:
                link_st = StandardLinkState.UNVERIFIED
                gap_st = TenderGapState.UNVERIFIED
                reason = f"Parameter comparison cannot be mathematically verified: {cmp_res.reason}"
            else:
                link_st = StandardLinkState.NOT_FOUND_IN_STANDARD
                gap_st = TenderGapState.GAP
                reason = f"Parameter is not addressed in standard: {cmp_res.reason}"

            is_temp_verif = s_req.temporal_status in ("WITHDRAWN", "SUPERSEDED", "TEMPORALLY_UNCERTAIN")
            is_verif = cmp_res.verification_required or is_temp_verif
            verif_reason = cmp_res.verification_reason or (f"Temporal status is '{s_req.temporal_status}'." if is_temp_verif else None)

            matches.append(
                TenderStandardMatch(
                    match_id=m_id,
                    tender_requirement_id=tender_req.requirement_id,
                    standard_id=s_req.standard_id,
                    standard_number=s_req.standard_number or s_req.standard_id,
                    clause_id=s_req.clause_id,
                    clause_number=s_req.clause_number,
                    clause_title=s_req.clause_title,
                    standard_requirement=s_req,
                    link_state=link_st,
                    gap_state=gap_st,
                    reason=reason,
                    supporting_evidence_chunk_ids=s_req.source_chunk_ids,
                    page_start=s_req.page_start,
                    page_end=s_req.page_end,
                    temporal_status=s_req.temporal_status,
                    confidence=s_req.confidence,
                    verification_required=is_verif,
                    verification_reason=verif_reason,
                )
            )
        else:
            # Text / category based match
            is_match = any(w in s_req.requirement_text.lower() for w in tender_req.text.lower().split() if len(w) > 4)
            link_st = StandardLinkState.SUPPORTED_BY_STANDARD if is_match else StandardLinkState.PARTIALLY_SUPPORTED
            gap_st = TenderGapState.MATCH if is_match else TenderGapState.GAP
            reason = f"Customer clause linked to standard clause '{s_req.clause_number or ''} {s_req.clause_title or ''}': {s_req.requirement_text}"

            is_verif = s_req.temporal_status in ("WITHDRAWN", "SUPERSEDED", "TEMPORALLY_UNCERTAIN")

            matches.append(
                TenderStandardMatch(
                    match_id=m_id,
                    tender_requirement_id=tender_req.requirement_id,
                    standard_id=s_req.standard_id,
                    standard_number=s_req.standard_number or s_req.standard_id,
                    clause_id=s_req.clause_id,
                    clause_number=s_req.clause_number,
                    clause_title=s_req.clause_title,
                    standard_requirement=s_req,
                    link_state=link_st,
                    gap_state=gap_st,
                    reason=reason,
                    supporting_evidence_chunk_ids=s_req.source_chunk_ids,
                    page_start=s_req.page_start,
                    page_end=s_req.page_end,
                    temporal_status=s_req.temporal_status,
                    confidence=s_req.confidence,
                    verification_required=is_verif,
                    verification_reason=f"Temporal status is '{s_req.temporal_status}'." if is_verif else None,
                )
            )

    return matches


def build_tripartite_comparison(
    tender_req: TenderRequirement,
    product_spec: Optional[TechnicalSpecification],
    standard_req: Optional[StandardTechnicalRequirement],
    tender_match: Optional[TenderStandardMatch],
) -> TripartiteComparison:
    """
    Synthesizes Tripartite Comparison:
    Buyer Requirement vs Product Specification vs BIS Standard Evidence.
    """
    spec_param: Optional[TechnicalParameter] = None
    if product_spec:
        pname_lower = tender_req.parameter_name.strip().lower()
        for k, p in product_spec.parameters.items():
            if k.lower() in pname_lower or pname_lower in k.lower():
                spec_param = p
                break

    # 1. Tender vs Standard
    t_vs_s = tender_match.gap_state if tender_match else TenderGapState.UNSPECIFIED

    # 2. Product vs Tender
    prod_vs_tender = MatchState.UNSPECIFIED
    if spec_param and tender_req.parameter:
        # Pretend tender requirement is a requirement and compare product parameter against it
        from app.technical_specs.models import ComparisonOperator
        op = tender_req.operator or ComparisonOperator.EQ
        t_thresh = tender_req.value
        min_thresh = tender_req.parameter.min_value
        max_thresh = tender_req.parameter.max_value
        if op in (ComparisonOperator.GE, ComparisonOperator.GT) and (t_thresh is None or t_thresh == ""):
            t_thresh = min_thresh
        elif op in (ComparisonOperator.LE, ComparisonOperator.LT) and (t_thresh is None or t_thresh == ""):
            t_thresh = max_thresh

        pseudo_req = StandardTechnicalRequirement(
            requirement_id="pseudo_tender_req",
            standard_id="tender",
            requirement_text=tender_req.text,
            parameter_name=tender_req.parameter_name,
            operator=op,
            threshold=float(t_thresh) if isinstance(t_thresh, (int, float)) else None,
            min_threshold=float(min_thresh) if min_thresh is not None else (float(t_thresh) if op in (ComparisonOperator.GE, ComparisonOperator.GT) and t_thresh is not None else None),
            max_threshold=float(max_thresh) if max_thresh is not None else (float(t_thresh) if op in (ComparisonOperator.LE, ComparisonOperator.LT) and t_thresh is not None else None),
            unit=tender_req.unit or tender_req.parameter.unit,
        )
        cmp_pt = compare_parameter_to_requirement(spec_param, pseudo_req)
        prod_vs_tender = cmp_pt.match_state
    elif not spec_param:
        prod_vs_tender = MatchState.MISSING

    # 3. Product vs Standard
    prod_vs_std = MatchState.UNSPECIFIED
    if spec_param and standard_req:
        cmp_ps = compare_parameter_to_requirement(spec_param, standard_req)
        prod_vs_std = cmp_ps.match_state
    elif not spec_param and standard_req:
        prod_vs_std = MatchState.MISSING

    notes = (
        f"Tripartite evaluation for '{tender_req.parameter_name}': "
        f"Tender vs Standard={t_vs_s.value}; "
        f"Product vs Tender={prod_vs_tender.value}; "
        f"Product vs Standard={prod_vs_std.value}."
    )

    return TripartiteComparison(
        tender_requirement=tender_req,
        spec_parameter=spec_param,
        standard_requirement=standard_req,
        tender_vs_standard=t_vs_s,
        product_vs_tender=prod_vs_tender,
        product_vs_standard=prod_vs_std,
        synthesis_notes=notes,
    )


def analyze_tender_gaps(
    tender: TenderDocument,
    standard_requirements: Optional[List[StandardTechnicalRequirement]] = None,
    product_spec: Optional[TechnicalSpecification] = None,
    requirements: Optional[List[StandardTechnicalRequirement]] = None,
) -> TenderGapAnalysisReport:
    """
    Executes cross-standard gap analysis for a tender document.
    """
    std_reqs = standard_requirements if standard_requirements is not None else (requirements or [])
    rpt_id = f"tnd_rpt_{hashlib.sha256((tender.tender_id + str(len(std_reqs))).encode('utf-8')).hexdigest()[:12]}"
    matches: List[TenderStandardMatch] = []
    gaps: List[TenderStandardMatch] = []
    conflicts: List[TenderStandardMatch] = []
    tripartite_list: List[TripartiteComparison] = []
    matched_stds_set = set()
    verif_reasons: List[str] = []

    for t_req in tender.requirements:
        t_matches = match_tender_requirement_to_standards(t_req, std_reqs)

        # Classify matches
        primary_match = None
        for m in t_matches:
            if m.standard_number:
                matched_stds_set.add(m.standard_number)

            if m.verification_required and m.verification_reason:
                verif_reasons.append(f"{t_req.parameter_name}: {m.verification_reason}")

            if m.gap_state == TenderGapState.MATCH:
                matches.append(m)
                if not primary_match:
                    primary_match = m
            elif m.gap_state == TenderGapState.CONFLICT:
                conflicts.append(m)
                if not primary_match:
                    primary_match = m
            else:
                gaps.append(m)
                if not primary_match:
                    primary_match = m

        # Tripartite comparison
        s_req = primary_match.standard_requirement if primary_match else None
        tri = build_tripartite_comparison(
            tender_req=t_req,
            product_spec=product_spec,
            standard_req=s_req,
            tender_match=primary_match,
        )
        tripartite_list.append(tri)

    is_verif = bool(verif_reasons) or len(conflicts) > 0
    summary = (
        f"Tender gap analysis completed across {len(tender.requirements)} requirement(s) and "
        f"{len(matched_stds_set)} candidate standard(s): "
        f"{len(matches)} supported, {len(gaps)} gap(s), {len(conflicts)} conflict(s). "
        f"{'Verification required due to conflicts or uncertain standards.' if is_verif else 'Review complete.'}"
    )

    return TenderGapAnalysisReport(
        report_id=rpt_id,
        tender=tender,
        matched_standards=sorted(list(matched_stds_set)),
        matches=matches,
        gaps=gaps,
        conflicts=conflicts,
        tripartite_comparisons=tripartite_list,
        verification_required=is_verif,
        verification_reasons=verif_reasons,
        summary=summary,
    )
