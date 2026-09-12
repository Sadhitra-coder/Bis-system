"""
app/applicability/evaluator.py

Applicability Evaluation and Compliance Readiness Engine (Phase 15).

DESIGN PRINCIPLES:
  - Evaluates standard scope applicability against product characteristics.
  - Positive exclusion check: 'NOT_APPLICABLE' requires explicit exclusion evidence.
  - Absence of evidence maps strictly to 'INSUFFICIENT_EVIDENCE', never negative legal fact.
  - Multi-standard readiness synthesis:
      Combines Phase 11 (product candidates), Phase 12 (spec matches),
      Phase 13 (tender gaps), and Phase 14 (evidence gaps).
  - States:
      READY_FOR_HUMAN_REVIEW, EVIDENCE_INCOMPLETE, TECHNICAL_GAPS_FOUND,
      DOCUMENT_GAPS_FOUND, TEMPORAL_UNCERTAINTY, VERIFICATION_REQUIRED.
  - STRICT PROHIBITION:
      Never outputs 'COMPLIANT', 'NON_COMPLIANT', 'APPROVED', 'CERTIFIED', or 'LEGALLY_VALID'.
  - Actionable guidance:
      Generates precise next steps for engineering and compliance teams.
"""

import hashlib
import re
import time
from typing import Any, Dict, List, Optional

from app.applicability.models import (
    ApplicabilityAssessment,
    ApplicabilityStatus,
    ComplianceReadinessReport,
    ComplianceReadinessState,
)
from app.document_intelligence.models import EvidenceGapReport
from app.product_mapping.models import ProductContext, ProductStandardCandidate
from app.technical_specs.models import MatchState, TechnicalAnalysisReport
from app.tender_analysis.models import TenderGapAnalysisReport


# Scope exclusion trigger patterns
_EXCLUSION_PATTERNS = [
    re.compile(r"(?:does\s+not\s+apply\s+to|shall\s+not\s+apply\s+to|excludes?|not\s+applicable\s+to|excluding)\s+([^.\n;]+)", re.IGNORECASE),
    re.compile(r"(?:specifically\s+excluded|scope\s+excludes?)\s*[:\-]?\s*([^.\n;]+)", re.IGNORECASE),
]

_INCLUSION_PATTERNS = [
    re.compile(r"(?:applies\s+to|covers?|specifies\s+the\s+requirements\s+for|scope\s+of\s+this\s+standard\s+covers?)\s+([^.\n;]+)", re.IGNORECASE),
]


def evaluate_standard_applicability(
    product: Optional[ProductContext],
    candidate: ProductStandardCandidate,
    scope_text: Optional[str] = None,
    clause_texts: Optional[List[str]] = None,
) -> ApplicabilityAssessment:
    """
    Evaluates whether a specific Indian Standard applies to a given product.
    Checks positive inclusion, explicit exclusion clauses, temporal currentness,
    and sufficiency of evidence.
    """
    p_name = (getattr(product, "product_name", None) or "none") if product else "none"
    ass_id = f"app_{hashlib.sha256((candidate.standard_id + p_name).encode('utf-8')).hexdigest()[:12]}"

    # Combine text for scope analysis
    corpus_text = (
        (candidate.standard_title or "") + " " +
        (scope_text or "") + " " +
        (" ".join(clause_texts or []))
    ).lower()

    # 1. Temporal gating
    temporal_status = candidate.temporal_status or "ACTIVE"
    if temporal_status in ("WITHDRAWN", "SUPERSEDED", "TEMPORALLY_UNCERTAIN"):
        return ApplicabilityAssessment(
            assessment_id=ass_id,
            standard_id=candidate.standard_id,
            standard_number=candidate.standard_number,
            standard_title=candidate.standard_title,
            version_id=candidate.version_id,
            status=ApplicabilityStatus.VERIFICATION_REQUIRED,
            reason=f"Standard '{candidate.standard_number}' is marked as {temporal_status} in the timeline. Manual regulatory verification required.",
            positive_inclusions=[],
            explicit_exclusions=[],
            required_clauses=[],
            confidence_score=0.40,
            temporal_status=temporal_status,
            verification_required=True,
            verification_reason=f"Temporal status is '{temporal_status}'.",
        )

    # 2. Check sufficiency of product information
    if not product or (not getattr(product, "product_name", None) and not getattr(product, "product_category", None) and not getattr(product, "raw_query", None)):
        return ApplicabilityAssessment(
            assessment_id=ass_id,
            standard_id=candidate.standard_id,
            standard_number=candidate.standard_number,
            standard_title=candidate.standard_title,
            version_id=candidate.version_id,
            status=ApplicabilityStatus.INSUFFICIENT_EVIDENCE,
            reason="Insufficient product details provided. Product name, intended use, and technical characteristics are required.",
            positive_inclusions=[],
            explicit_exclusions=[],
            required_clauses=[],
            confidence_score=0.20,
            temporal_status=temporal_status,
            verification_required=True,
            verification_reason="Missing product business context.",
        )

    # 3. Explicit Exclusion Checking (Negative Facts)
    positive_inclusions: List[str] = []
    explicit_exclusions: List[str] = []

    prod_name = (getattr(product, "product_name", None) or "").lower()
    prod_cat = (getattr(product, "product_category", None) or getattr(product, "category", None) or "").lower()
    prod_env = (getattr(product, "use_environment", None) or "").lower()
    if not prod_env and hasattr(product, "technical_characteristics") and isinstance(product.technical_characteristics, dict):
        prod_env = (product.technical_characteristics.get("use_environment") or "").lower()

    # Search for exclusion clauses in scope text
    for pat in _EXCLUSION_PATTERNS:
        for m in pat.finditer(corpus_text):
            excl_phrase = m.group(1).strip()
            explicit_exclusions.append(excl_phrase)

            # Check if product attributes match the exclusion
            if prod_env and prod_env in excl_phrase:
                return ApplicabilityAssessment(
                    assessment_id=ass_id,
                    standard_id=candidate.standard_id,
                    standard_number=candidate.standard_number,
                    standard_title=candidate.standard_title,
                    version_id=candidate.version_id,
                    status=ApplicabilityStatus.NOT_APPLICABLE,
                    reason=f"Product use environment '{prod_env}' is explicitly excluded by standard scope: '{excl_phrase}'.",
                    positive_inclusions=[],
                    explicit_exclusions=[excl_phrase],
                    required_clauses=[],
                    confidence_score=0.90,
                    temporal_status=temporal_status,
                    verification_required=False,
                )
            if prod_cat and prod_cat in excl_phrase and "commercial" in prod_cat:
                return ApplicabilityAssessment(
                    assessment_id=ass_id,
                    standard_id=candidate.standard_id,
                    standard_number=candidate.standard_number,
                    standard_title=candidate.standard_title,
                    version_id=candidate.version_id,
                    status=ApplicabilityStatus.NOT_APPLICABLE,
                    reason=f"Product category '{prod_cat}' matches explicit scope exclusion: '{excl_phrase}'.",
                    positive_inclusions=[],
                    explicit_exclusions=[excl_phrase],
                    required_clauses=[],
                    confidence_score=0.90,
                    temporal_status=temporal_status,
                    verification_required=False,
                )

    # 4. Positive Inclusion Checking
    for pat in _INCLUSION_PATTERNS:
        for m in pat.finditer(corpus_text):
            positive_inclusions.append(m.group(1).strip())

    # Direct match on title or keywords
    title_lower = (candidate.standard_title or "").lower()
    cand_score = float(getattr(candidate, "mapping_score", getattr(candidate, "match_score", 0.5)))
    direct_title_match = any(token in title_lower for token in [prod_name, prod_cat] if token and len(token) > 3)

    if direct_title_match or cand_score >= 0.75:
        return ApplicabilityAssessment(
            assessment_id=ass_id,
            standard_id=candidate.standard_id,
            standard_number=candidate.standard_number,
            standard_title=candidate.standard_title,
            version_id=candidate.version_id,
            status=ApplicabilityStatus.APPLICABLE,
            reason=f"Product '{product.product_name or product.category}' directly aligns with standard title and mandatory scope.",
            positive_inclusions=positive_inclusions or [candidate.standard_title or candidate.standard_number],
            explicit_exclusions=explicit_exclusions,
            required_clauses=[],
            confidence_score=round(max(0.75, cand_score), 4),
            temporal_status=temporal_status,
            verification_required=False,
        )

    # 5. Partial / Broad Family Match
    prod_tokens = [w for w in (prod_name + " " + prod_cat).split() if len(w) > 3]
    if cand_score >= 0.45 or any(k in corpus_text for k in prod_tokens):
        return ApplicabilityAssessment(
            assessment_id=ass_id,
            standard_id=candidate.standard_id,
            standard_number=candidate.standard_number,
            standard_title=candidate.standard_title,
            version_id=candidate.version_id,
            status=ApplicabilityStatus.POTENTIALLY_APPLICABLE,
            reason=f"Standard '{candidate.standard_number}' covers the broader product family, but specific variant or capacity limits require engineering verification.",
            positive_inclusions=positive_inclusions,
            explicit_exclusions=explicit_exclusions,
            required_clauses=[],
            confidence_score=round(cand_score, 4),
            temporal_status=temporal_status,
            verification_required=True,
            verification_reason="Product classification aligns with general category, but specific clause applicability must be confirmed.",
        )

    # Default to INSUFFICIENT_EVIDENCE
    return ApplicabilityAssessment(
        assessment_id=ass_id,
        standard_id=candidate.standard_id,
        standard_number=candidate.standard_number,
        standard_title=candidate.standard_title,
        version_id=candidate.version_id,
        status=ApplicabilityStatus.INSUFFICIENT_EVIDENCE,
        reason=f"Low similarity match ({cand_score:.2f}) between product and standard '{candidate.standard_number}'.",
        positive_inclusions=[],
        explicit_exclusions=explicit_exclusions,
        required_clauses=[],
        confidence_score=round(cand_score, 4),
        temporal_status=temporal_status,
        verification_required=True,
        verification_reason="Evidence is insufficient to confirm positive scope applicability.",
    )


def synthesize_compliance_readiness(
    product: Optional[ProductContext],
    applicability_assessments: List[ApplicabilityAssessment],
    technical_analysis: Optional[TechnicalAnalysisReport] = None,
    tender_analysis: Optional[TenderGapAnalysisReport] = None,
    evidence_report: Optional[EvidenceGapReport] = None,
) -> ComplianceReadinessReport:
    """
    Synthesizes multi-dimensional compliance findings into a holistic readiness report.
    Never declares 'COMPLIANT' or 'CERTIFIED'.
    """
    readiness_id = f"readiness_{int(time.time())}_{hashlib.sha256(str(len(applicability_assessments)).encode()).hexdigest()[:8]}"
    actionable_steps: List[str] = []

    # 1. State Analysis
    has_temporal_uncertainty = any(
        a.temporal_status in ("WITHDRAWN", "SUPERSEDED", "TEMPORALLY_UNCERTAIN")
        for a in applicability_assessments
    )
    has_conflicts = (
        (evidence_report and evidence_report.conflicting_count > 0) or
        (tender_analysis and tender_analysis.conflict_count > 0)
    )
    has_expired_docs = (evidence_report and evidence_report.expired_count > 0)
    has_tech_mismatches = (
        technical_analysis and
        (bool(getattr(technical_analysis, "technical_gaps", [])) or getattr(technical_analysis, "mismatch_count", 0) > 0)
    )
    has_missing_evidence = (
        evidence_report and
        (evidence_report.missing_count > 0 or evidence_report.unverified_count > 0)
    )
    has_applicable_standards = any(
        a.status == ApplicabilityStatus.APPLICABLE for a in applicability_assessments
    )

    # 2. Determine Overall Readiness State
    if has_conflicts:
        overall_state = ComplianceReadinessState.VERIFICATION_REQUIRED
        actionable_steps.append("Resolve contradictory test reports / parameters before proceeding.")
    elif has_temporal_uncertainty:
        overall_state = ComplianceReadinessState.TEMPORAL_UNCERTAINTY
        actionable_steps.append("Verify active regulatory status and transition timelines for superseded standard(s).")
    elif has_tech_mismatches:
        overall_state = ComplianceReadinessState.TECHNICAL_GAPS_FOUND
        if technical_analysis:
            for m in getattr(technical_analysis, "technical_gaps", []):
                actionable_steps.append(f"Modify product engineering to address '{m.parameter_name}' mismatch.")
    elif has_expired_docs:
        overall_state = ComplianceReadinessState.DOCUMENT_GAPS_FOUND
        actionable_steps.append("Renew expired calibration certificates or test reports.")
    elif has_missing_evidence or not evidence_report:
        overall_state = ComplianceReadinessState.EVIDENCE_INCOMPLETE
        if evidence_report and evidence_report.missing_requirements:
            for miss in evidence_report.missing_requirements[:5]:
                actionable_steps.append(f"Commission accredited laboratory testing for '{miss.parameter_name}'.")
        else:
            actionable_steps.append("Submit valid accredited test reports and manufacturer declarations.")
    elif has_applicable_standards and not has_missing_evidence and not has_tech_mismatches:
        overall_state = ComplianceReadinessState.READY_FOR_HUMAN_REVIEW
        actionable_steps.append("All technical and documentary prerequisites met. Submit complete dossier to accredited certification body for human review.")
    else:
        overall_state = ComplianceReadinessState.VERIFICATION_REQUIRED
        actionable_steps.append("Verify product categorization against candidate standards.")

    # 3. Calculate Normalized Readiness Score (0.0 to 1.0)
    # Dimension 1: Applicability (25%)
    app_score = 0.0
    if applicability_assessments:
        app_score = sum(
            1.0 if a.status == ApplicabilityStatus.APPLICABLE else (
                0.5 if a.status == ApplicabilityStatus.POTENTIALLY_APPLICABLE else 0.0
            ) for a in applicability_assessments
        ) / len(applicability_assessments)

    # Dimension 2: Technical Specification Alignment (35%)
    tech_score = 0.0
    if technical_analysis:
        m_cnt = len(getattr(technical_analysis, "requirement_matches", [])) or getattr(technical_analysis, "match_count", 0)
        g_cnt = len(getattr(technical_analysis, "technical_gaps", [])) or getattr(technical_analysis, "mismatch_count", 0)
        tot = m_cnt + g_cnt
        if tot > 0:
            tech_score = m_cnt / tot
        else:
            tech_score = 0.5
    else:
        tech_score = 0.3

    # Dimension 3: Documentary Evidence Coverage (40%)
    doc_score = 0.0
    if evidence_report and evidence_report.total_requirements > 0:
        doc_score = evidence_report.supported_count / evidence_report.total_requirements
    elif evidence_report and evidence_report.total_requirements == 0:
        doc_score = 0.5
    else:
        doc_score = 0.2

    # Weighted composite score
    base_score = (0.25 * app_score) + (0.35 * tech_score) + (0.40 * doc_score)

    # Penalties
    penalty = 0.0
    if has_conflicts:
        penalty += 0.30
    if has_expired_docs:
        penalty += 0.20
    if has_temporal_uncertainty:
        penalty += 0.15

    final_score = max(0.0, min(1.0, base_score - penalty))

    return ComplianceReadinessReport(
        readiness_id=readiness_id,
        product_context_id=product.product_context_id if product else None,
        overall_readiness_state=overall_state,
        readiness_score=final_score,
        applicability_assessments=applicability_assessments,
        technical_analysis_summary=technical_analysis.to_dict() if technical_analysis else None,
        tender_gap_summary=tender_analysis.to_dict() if tender_analysis else None,
        evidence_gap_summary=evidence_report.to_dict() if evidence_report else None,
        actionable_next_steps=actionable_steps,
        created_at=time.time(),
    )
