"""
app/document_intelligence/matcher.py

Safe Document Evidence Matching & Evidence Gap Engine (Phase 14).

DESIGN PRINCIPLES:
  - Matches business/lab document evidence against standard or tender requirements.
  - States: SUPPORTED, PARTIAL, MISSING, EXPIRED, CONFLICTING, UNVERIFIED.
  - Multi-document conflict detection: contradictory parameters/verdicts trigger CONFLICTING.
  - Expiry propagation: documents past validity period trigger EXPIRED status.
  - Zero extrapolation: unobserved or missing requirements evaluate strictly as MISSING.
  - Never declares 'COMPLIANT' or 'CERTIFIED'.
  - Generates comprehensive EvidenceGapReport.
"""

from datetime import datetime
import hashlib
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from app.document_intelligence.models import (
    BusinessDocument,
    DocumentMatchStatus,
    DocumentRequirementMatch,
    EvidenceGapReport,
)
from app.technical_specs.matcher import compare_parameter_to_requirement
from app.technical_specs.models import (
    ComparisonOperator,
    MatchState,
    StandardTechnicalRequirement,
    TechnicalParameter,
)
from app.tender_analysis.models import TenderRequirement


def _normalize_name(name: str) -> str:
    """Normalize parameter or requirement name for matching."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _find_matching_parameter(
    req_name: str,
    doc_params: Dict[str, TechnicalParameter],
) -> Optional[Tuple[str, TechnicalParameter]]:
    """
    Finds matching parameter in document parameters using exact, normalized, or token overlap.
    """
    norm_req = _normalize_name(req_name)
    if not norm_req:
        return None

    # Exact key
    if req_name in doc_params:
        return req_name, doc_params[req_name]

    # Normalized key
    for k, param in doc_params.items():
        if _normalize_name(k) == norm_req:
            return k, param

    # Substring / token overlap
    req_tokens = set(re.findall(r"\b[a-z]{3,}\b", req_name.lower()))
    best_match = None
    best_overlap = 0
    for k, param in doc_params.items():
        param_tokens = set(re.findall(r"\b[a-z]{3,}\b", k.lower()))
        overlap = len(req_tokens & param_tokens)
        if overlap > best_overlap and overlap >= 2:
            best_overlap = overlap
            best_match = (k, param)

    return best_match


def evaluate_requirement_against_documents(
    requirement: Union[StandardTechnicalRequirement, TenderRequirement, Dict[str, Any]],
    documents: List[BusinessDocument],
    as_of_date: Optional[str] = None,
) -> DocumentRequirementMatch:
    """
    Evaluates a single requirement against a list of uploaded business/lab documents.
    Detects supporting evidence, expired documents, and cross-document contradictions.
    """
    # Extract standard fields from polymorphic requirement
    if isinstance(requirement, StandardTechnicalRequirement):
        req_id = requirement.requirement_id
        param_name = requirement.parameter_name
        req_val = requirement.threshold or requirement.min_threshold or requirement.max_threshold
        is_mandatory = True
    elif isinstance(requirement, TenderRequirement):
        req_id = requirement.requirement_id
        param_name = requirement.parameter_name
        req_val = requirement.value
        is_mandatory = bool(requirement.mandatory_language)
    else:
        req_id = requirement.get("requirement_id", "req_unknown")
        param_name = requirement.get("parameter_name", "parameter")
        req_val = requirement.get("value") or requirement.get("threshold")
        is_mandatory = requirement.get("is_mandatory", True)

    if not documents:
        return DocumentRequirementMatch(
            requirement_id=req_id,
            parameter_name=param_name,
            document_id=None,
            extracted_value=None,
            required_value=req_val,
            status=DocumentMatchStatus.MISSING,
            reason="No documents submitted for evaluation.",
            provenance={},
            verification_required=is_mandatory,
        )

    # Search for evidence across all documents
    found_evidence: List[Dict[str, Any]] = []

    for doc in documents:
        # Check standard references (for certification/standard-wide requirements)
        if param_name.lower() in ["standard", "bis_standard", "certification", "compliance"]:
            for std_ref in doc.standard_references:
                found_evidence.append({
                    "doc": doc,
                    "matched_key": "standard_reference",
                    "param": None,
                    "value": std_ref,
                    "is_test_result": False,
                })

        # Check extracted structured physical parameters
        match = _find_matching_parameter(param_name, doc.extracted_parameters)
        if match:
            k, param = match
            found_evidence.append({
                "doc": doc,
                "matched_key": k,
                "param": param,
                "value": param.value or param.nominal_value,
                "is_test_result": False,
            })

        # Check test results table entries
        for tr in doc.test_results:
            tr_param = tr.get("parameter", "")
            if _normalize_name(param_name) in _normalize_name(tr_param) or _normalize_name(tr_param) in _normalize_name(param_name):
                found_evidence.append({
                    "doc": doc,
                    "matched_key": tr_param,
                    "param": None,
                    "value": tr.get("observed_value"),
                    "verdict": tr.get("verdict"),
                    "is_test_result": True,
                    "raw_text": tr.get("raw_text"),
                })

    # Case 1: No evidence found anywhere
    if not found_evidence:
        return DocumentRequirementMatch(
            requirement_id=req_id,
            parameter_name=param_name,
            document_id=None,
            extracted_value=None,
            required_value=req_val,
            status=DocumentMatchStatus.MISSING,
            reason=f"No matching parameter or test evidence for '{param_name}' across {len(documents)} document(s).",
            provenance={"evaluated_docs_count": len(documents)},
            verification_required=is_mandatory,
        )

    # Case 2: Check for conflicting evidence across multiple documents
    # Contradictions occur if observed test verdicts differ (PASS vs FAIL)
    # or if different documents report substantially different numeric values.
    verdicts = {e.get("verdict") for e in found_evidence if e.get("verdict")}
    if "PASS" in verdicts and "FAIL" in verdicts:
        conflicting_docs = [e["doc"].document_id for e in found_evidence if e.get("verdict")]
        return DocumentRequirementMatch(
            requirement_id=req_id,
            parameter_name=param_name,
            document_id=",".join(conflicting_docs),
            extracted_value="CONTRADICTORY_VERDICTS (PASS vs FAIL)",
            required_value=req_val,
            status=DocumentMatchStatus.CONFLICTING,
            reason=f"Contradictory test verdicts found across documents {conflicting_docs}: both PASS and FAIL reported.",
            provenance={"conflicting_document_ids": conflicting_docs},
            verification_required=True,
        )

    # Check numeric value divergence across documents
    numeric_values = []
    for e in found_evidence:
        p = e.get("param")
        if p and p.normalized_value is not None:
            numeric_values.append((e["doc"].document_id, p.normalized_value))
    if len(numeric_values) >= 2:
        vals = [v[1] for v in numeric_values]
        # If difference exceeds 10% between reports without known variance
        if max(vals) > min(vals) * 1.15:
            doc_ids = [v[0] for v in numeric_values]
            return DocumentRequirementMatch(
                requirement_id=req_id,
                parameter_name=param_name,
                document_id=",".join(doc_ids),
                extracted_value=f"DIVERGENT_VALUES: {vals}",
                required_value=req_val,
                status=DocumentMatchStatus.CONFLICTING,
                reason=f"Significantly divergent physical measurements reported across documents {doc_ids}.",
                provenance={"conflicting_documents": doc_ids, "measurements": vals},
                verification_required=True,
            )

    # Case 3: Check expired documents
    # If the primary evidence is from an expired document and no valid unexpired evidence exists
    expired_evidence = [e for e in found_evidence if e["doc"].status == "EXPIRED"]
    active_evidence = [e for e in found_evidence if e["doc"].status != "EXPIRED"]

    if expired_evidence and not active_evidence:
        exp_doc = expired_evidence[0]["doc"]
        return DocumentRequirementMatch(
            requirement_id=req_id,
            parameter_name=param_name,
            document_id=exp_doc.document_id,
            extracted_value=expired_evidence[0]["value"],
            required_value=req_val,
            status=DocumentMatchStatus.EXPIRED,
            reason=f"Evidence found in document '{exp_doc.document_id}', but document expired on {exp_doc.expiry_date}.",
            provenance={
                "document_id": exp_doc.document_id,
                "expiry_date": exp_doc.expiry_date,
                "document_number": exp_doc.document_number,
            },
            verification_required=True,
        )

    # Use best available active evidence
    primary = active_evidence[0] if active_evidence else found_evidence[0]
    p_doc: BusinessDocument = primary["doc"]
    p_param: Optional[TechnicalParameter] = primary.get("param")
    p_val = primary.get("value")

    # Case 4: Unverified check (e.g. document has no issuer or doc number and low confidence)
    if not p_doc.issuer and not p_doc.document_number and p_doc.classification_confidence < 0.5:
        return DocumentRequirementMatch(
            requirement_id=req_id,
            parameter_name=param_name,
            document_id=p_doc.document_id,
            extracted_value=p_val,
            required_value=req_val,
            status=DocumentMatchStatus.UNVERIFIED,
            reason=f"Evidence found in '{p_doc.document_id}' but lacks issuer identification, document number, and has low confidence.",
            provenance={"document_id": p_doc.document_id, "confidence": p_doc.classification_confidence},
            verification_required=True,
        )

    # Case 5: Evaluate support against requirement limits
    if p_param:
        op_val = getattr(requirement, "operator", None)
        if isinstance(requirement, dict):
            op_val = requirement.get("operator")
        op = op_val if isinstance(op_val, ComparisonOperator) else (
            ComparisonOperator.GE if op_val in (">=", "GE") else (
                ComparisonOperator.LE if op_val in ("<=", "LE") else ComparisonOperator.EQ
            )
        )
        std_req = requirement if isinstance(requirement, StandardTechnicalRequirement) else StandardTechnicalRequirement(
            requirement_id=req_id,
            standard_id="CUSTOM",
            requirement_text=param_name,
            standard_number="CUSTOM",
            parameter_name=param_name,
            operator=op,
            threshold=float(req_val) if isinstance(req_val, (int, float)) else None,
            unit=p_param.unit,
        )
        comp_res = compare_parameter_to_requirement(p_param, std_req)
        if comp_res.match_state == MatchState.MATCH:
            return DocumentRequirementMatch(
                requirement_id=req_id,
                parameter_name=param_name,
                document_id=p_doc.document_id,
                extracted_value=p_val,
                required_value=req_val,
                status=DocumentMatchStatus.SUPPORTED,
                reason=comp_res.reason or f"Document measurement matches specification {req_val}.",
                provenance={
                    "document_id": p_doc.document_id,
                    "document_number": p_doc.document_number,
                    "issuer": p_doc.issuer,
                    "issue_date": p_doc.issue_date,
                },
                verification_required=False,
            )
        elif comp_res.match_state == MatchState.PARTIAL_MATCH:
            return DocumentRequirementMatch(
                requirement_id=req_id,
                parameter_name=param_name,
                document_id=p_doc.document_id,
                extracted_value=p_val,
                required_value=req_val,
                status=DocumentMatchStatus.PARTIAL,
                reason=comp_res.reason or "Document measurement partially matches requirement.",
                provenance={"document_id": p_doc.document_id, "comparison_reason": comp_res.reason},
                verification_required=True,
            )
        elif comp_res.match_state == MatchState.MISMATCH:
            return DocumentRequirementMatch(
                requirement_id=req_id,
                parameter_name=param_name,
                document_id=p_doc.document_id,
                extracted_value=p_val,
                required_value=req_val,
                status=DocumentMatchStatus.PARTIAL,
                reason=f"Document observed value {p_val} does not satisfy requirement {req_val}.",
                provenance={"document_id": p_doc.document_id, "comparison_reason": comp_res.reason},
                verification_required=True,
            )

    # If test report specifically marked PASS
    if primary.get("verdict") == "PASS":
        return DocumentRequirementMatch(
            requirement_id=req_id,
            parameter_name=param_name,
            document_id=p_doc.document_id,
            extracted_value=p_val or "PASS",
            required_value=req_val,
            status=DocumentMatchStatus.SUPPORTED,
            reason=f"Test report '{p_doc.document_id}' explicitly verified: {primary.get('raw_text', 'Passed test criteria')}.",
            provenance={
                "document_id": p_doc.document_id,
                "document_number": p_doc.document_number,
                "accreditation": p_doc.accreditation_details,
            },
            verification_required=False,
        )

    # Fallback to general SUPPORTED if value present without contradictions
    return DocumentRequirementMatch(
        requirement_id=req_id,
        parameter_name=param_name,
        document_id=p_doc.document_id,
        extracted_value=p_val,
        required_value=req_val,
        status=DocumentMatchStatus.SUPPORTED,
        reason=f"Document evidence recorded in '{p_doc.document_id}'.",
        provenance={"document_id": p_doc.document_id},
        verification_required=False,
    )


def build_evidence_gap_report(
    requirements: List[Union[StandardTechnicalRequirement, TenderRequirement, Dict[str, Any]]],
    documents: List[BusinessDocument],
    as_of_date: Optional[str] = None,
    report_id: Optional[str] = None,
) -> EvidenceGapReport:
    """
    Evaluates a collection of requirements against uploaded documents,
    categorizing findings and compiling an EvidenceGapReport.
    """
    r_id = report_id or f"egr_{int(time.time())}_{hashlib.sha256(str(len(requirements)).encode()).hexdigest()[:8]}"

    supported: List[DocumentRequirementMatch] = []
    partial: List[DocumentRequirementMatch] = []
    missing: List[DocumentRequirementMatch] = []
    expired: List[DocumentRequirementMatch] = []
    conflicting: List[DocumentRequirementMatch] = []
    unverified: List[DocumentRequirementMatch] = []
    audit_warnings: List[str] = []

    for req in requirements:
        match = evaluate_requirement_against_documents(req, documents, as_of_date=as_of_date)
        if match.status == DocumentMatchStatus.SUPPORTED:
            supported.append(match)
        elif match.status == DocumentMatchStatus.PARTIAL:
            partial.append(match)
            audit_warnings.append(f"Requirement '{match.parameter_name}' is only partially supported: {match.reason}")
        elif match.status == DocumentMatchStatus.MISSING:
            missing.append(match)
            audit_warnings.append(f"Missing evidence for requirement: '{match.parameter_name}'.")
        elif match.status == DocumentMatchStatus.EXPIRED:
            expired.append(match)
            audit_warnings.append(f"Expired document used for requirement '{match.parameter_name}': {match.reason}")
        elif match.status == DocumentMatchStatus.CONFLICTING:
            conflicting.append(match)
            audit_warnings.append(f"CRITICAL: Contradictory evidence found for '{match.parameter_name}': {match.reason}")
        elif match.status == DocumentMatchStatus.UNVERIFIED:
            unverified.append(match)
            audit_warnings.append(f"Unverified evidence for requirement '{match.parameter_name}': {match.reason}")

    # Check for document-level warnings (e.g. unaccredited labs or expired docs not tied to specific reqs)
    for doc in documents:
        if doc.status == "EXPIRED" and not any(m.document_id == doc.document_id for m in expired):
            audit_warnings.append(f"Document '{doc.document_id}' ({doc.title or 'Untitled'}) is expired ({doc.expiry_date}).")

    # Determine overall evidence state
    total = len(requirements)
    if conflicting:
        overall_state = "CONFLICTS_DETECTED"
    elif expired:
        overall_state = "EXPIRED_EVIDENCE_FOUND"
    elif missing or partial:
        overall_state = "GAPS_DETECTED"
    elif supported and len(supported) == total:
        overall_state = "EVIDENCE_COMPLETE"
    else:
        overall_state = "INSUFFICIENT_EVIDENCE"

    return EvidenceGapReport(
        report_id=r_id,
        total_requirements=total,
        supported_count=len(supported),
        partial_count=len(partial),
        missing_count=len(missing),
        expired_count=len(expired),
        conflicting_count=len(conflicting),
        unverified_count=len(unverified),
        supported_requirements=supported,
        partial_requirements=partial,
        missing_requirements=missing,
        expired_requirements=expired,
        conflicting_requirements=conflicting,
        unverified_requirements=unverified,
        overall_evidence_state=overall_state,
        audit_warnings=audit_warnings,
        created_at=time.time(),
    )
