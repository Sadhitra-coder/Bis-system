"""
app/applicability/models.py

Canonical Domain Models for Applicability & Compliance Readiness (Phase 15).

DESIGN PRINCIPLES:
  - Synthesizes Product-to-Standard candidate mapping (Phase 11), Technical Specification
    analysis (Phase 12), Tender Gap analysis (Phase 13), and Document Intelligence (Phase 14).
  - Applicability States:
      APPLICABLE, POTENTIALLY_APPLICABLE, NOT_APPLICABLE,
      INSUFFICIENT_EVIDENCE, VERIFICATION_REQUIRED.
  - Compliance Readiness States:
      READY_FOR_HUMAN_REVIEW, EVIDENCE_INCOMPLETE, TECHNICAL_GAPS_FOUND,
      DOCUMENT_GAPS_FOUND, TEMPORAL_UNCERTAINTY, VERIFICATION_REQUIRED.
  - ABSOLUTE PROHIBITION:
      Never outputs 'COMPLIANT', 'NON_COMPLIANT', 'APPROVED', 'CERTIFIED', or 'LEGALLY_VALID'.
  - Conservative negative facts:
      'NOT_APPLICABLE' requires explicit positive exclusion evidence.
      Lack of evidence never implies non-applicability; it maps to INSUFFICIENT_EVIDENCE.
  - Disclaimers mandatory:
      Every readiness report carries an explicit non-certification disclaimer.
"""

from enum import Enum
import hashlib
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ApplicabilityStatus(str, Enum):
    """
    Standard applicability classification for a specific product.
    CRITICAL: Never uses 'COMPLIANT' or 'CERTIFIED'.
    """
    APPLICABLE = "APPLICABLE"
    POTENTIALLY_APPLICABLE = "POTENTIALLY_APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    VERIFICATION_REQUIRED = "VERIFICATION_REQUIRED"


class ComplianceReadinessState(str, Enum):
    """
    Overall product compliance readiness state for review and certification pathway.
    CRITICAL: Strictly forbids 'COMPLIANT', 'NON_COMPLIANT', 'APPROVED', 'CERTIFIED'.
    """
    READY_FOR_HUMAN_REVIEW = "READY_FOR_HUMAN_REVIEW"
    EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"
    TECHNICAL_GAPS_FOUND = "TECHNICAL_GAPS_FOUND"
    DOCUMENT_GAPS_FOUND = "DOCUMENT_GAPS_FOUND"
    TEMPORAL_UNCERTAINTY = "TEMPORAL_UNCERTAINTY"
    VERIFICATION_REQUIRED = "VERIFICATION_REQUIRED"


class ApplicabilityAssessment(BaseModel):
    """
    Rigorous evaluation of a single Indian Standard's applicability to a product.
    """
    assessment_id: str
    standard_id: str
    standard_number: str
    standard_title: Optional[str] = None
    version_id: Optional[str] = None
    status: ApplicabilityStatus
    reason: str
    positive_inclusions: List[str] = Field(default_factory=list)
    explicit_exclusions: List[str] = Field(default_factory=list)
    required_clauses: List[str] = Field(default_factory=list)
    confidence_score: float = 1.0
    temporal_status: str = "ACTIVE"
    verification_required: bool = False
    verification_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "assessment_id": self.assessment_id,
            "standard_id": self.standard_id,
            "standard_number": self.standard_number,
            "standard_title": self.standard_title,
            "version_id": self.version_id,
            "status": self.status.value,
            "reason": self.reason,
            "positive_inclusions": self.positive_inclusions,
            "explicit_exclusions": self.explicit_exclusions,
            "required_clauses": self.required_clauses,
            "confidence_score": round(self.confidence_score, 4),
            "temporal_status": self.temporal_status,
            "verification_required": self.verification_required,
            "verification_reason": self.verification_reason,
        }


class ComplianceReadinessReport(BaseModel):
    """
    Comprehensive multi-standard product readiness synthesis report.
    Integrates product context, candidate standards, technical parameters,
    tender gaps, and documentary evidence.
    """
    readiness_id: str
    product_context_id: Optional[str] = None
    overall_readiness_state: ComplianceReadinessState
    readiness_score: float = 0.0
    applicability_assessments: List[ApplicabilityAssessment] = Field(default_factory=list)
    technical_analysis_summary: Optional[Dict[str, Any]] = None
    tender_gap_summary: Optional[Dict[str, Any]] = None
    evidence_gap_summary: Optional[Dict[str, Any]] = None
    actionable_next_steps: List[str] = Field(default_factory=list)
    disclaimer: str = (
        "This readiness assessment is an automated characterization based on available product specifications "
        "and documentary evidence. It does NOT constitute a legal certification, government approval, or "
        "official BIS licence. Final compliance determinations require review by an accredited testing laboratory "
        "and authorized BIS certification personnel."
    )
    created_at: float = Field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "readiness_id": self.readiness_id,
            "product_context_id": self.product_context_id,
            "overall_readiness_state": self.overall_readiness_state.value,
            "readiness_score": round(self.readiness_score, 4),
            "applicability_assessments": [a.to_dict() for a in self.applicability_assessments],
            "technical_analysis_summary": self.technical_analysis_summary,
            "tender_gap_summary": self.tender_gap_summary,
            "evidence_gap_summary": self.evidence_gap_summary,
            "actionable_next_steps": self.actionable_next_steps,
            "disclaimer": self.disclaimer,
            "created_at": self.created_at,
        }
