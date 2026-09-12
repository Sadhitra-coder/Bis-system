"""
app/document_intelligence/models.py

Canonical Domain Models for Document Intelligence + Requirement Matching (Phase 14).

DESIGN PRINCIPLES:
  - Classifies customer compliance, tender documents, test reports, datasheets, declarations.
  - Reuses Phase 12 TechnicalParameter and StandardTechnicalRequirement without duplication.
  - Reuses Phase 13 TenderRequirement without duplication.
  - Match States: SUPPORTED, PARTIAL, MISSING, EXPIRED, CONFLICTING, UNVERIFIED.
  - Strictly forbids declaring legal compliance or government approval.
  - Rigorous evidence tracking: zero extrapolation of unstated parameters.
  - Expiry and conflict detection: expired certificates or conflicting test data
    set verification_required=True and flag clear audit warnings.
"""

from enum import Enum
import hashlib
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.technical_specs.models import (
    ComparisonOperator,
    StandardTechnicalRequirement,
    TechnicalParameter,
    TechnicalSpecification,
)
from app.tender_analysis.models import TenderRequirement


class DocumentType(str, Enum):
    """
    Taxonomy of business, laboratory, and engineering compliance documents.
    """
    TEST_REPORT = "TEST_REPORT"
    CALIBRATION_REPORT = "CALIBRATION_REPORT"
    CERTIFICATE = "CERTIFICATE"
    DECLARATION = "DECLARATION"
    MANUAL = "MANUAL"
    TECHNICAL_SPECIFICATION = "TECHNICAL_SPECIFICATION"
    DRAWING = "DRAWING"
    QUALITY_RECORD = "QUALITY_RECORD"
    LAB_REPORT = "LAB_REPORT"
    OTHER = "OTHER"


class DocumentMatchStatus(str, Enum):
    """
    Evaluation status of a standard/tender requirement against document evidence.
    CRITICAL: Never uses 'COMPLIANT' or 'NON_COMPLIANT'.
    """
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    EXPIRED = "EXPIRED"
    CONFLICTING = "CONFLICTING"
    UNVERIFIED = "UNVERIFIED"


class BusinessDocument(BaseModel):
    """
    Canonical structured representation of an uploaded compliance or technical document.
    """
    document_id: str
    document_type: DocumentType = DocumentType.OTHER
    title: Optional[str] = None
    issuer: Optional[str] = None
    document_number: Optional[str] = None
    issue_date: Optional[str] = None
    expiry_date: Optional[str] = None
    product_id: Optional[str] = None
    source_hash: Optional[str] = None
    classification_confidence: float = 1.0
    source_chunk_ids: List[str] = Field(default_factory=list)
    extracted_parameters: Dict[str, TechnicalParameter] = Field(default_factory=dict)
    accreditation_details: Optional[str] = None
    standard_references: List[str] = Field(default_factory=list)
    test_results: List[Dict[str, Any]] = Field(default_factory=list)
    status: str = "ACTIVE"  # ACTIVE, EXPIRED, UNKNOWN
    raw_text: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "document_type": self.document_type.value,
            "title": self.title,
            "issuer": self.issuer,
            "document_number": self.document_number,
            "issue_date": self.issue_date,
            "expiry_date": self.expiry_date,
            "product_id": self.product_id,
            "source_hash": self.source_hash,
            "classification_confidence": self.classification_confidence,
            "source_chunk_ids": self.source_chunk_ids,
            "extracted_parameters": {k: v.to_dict() for k, v in self.extracted_parameters.items()},
            "accreditation_details": self.accreditation_details,
            "standard_references": self.standard_references,
            "test_results": self.test_results,
            "status": self.status,
            "raw_text": self.raw_text,
            "metadata": self.metadata,
        }


class DocumentRequirementMatch(BaseModel):
    """
    Result of evaluating a specific requirement (from standard or tender)
    against the evidence provided by one or more business documents.
    """
    requirement_id: str
    parameter_name: str
    document_id: Optional[str] = None
    extracted_value: Optional[Any] = None
    required_value: Optional[Any] = None
    status: DocumentMatchStatus
    reason: str
    provenance: Dict[str, Any] = Field(default_factory=dict)
    verification_required: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "parameter_name": self.parameter_name,
            "document_id": self.document_id,
            "extracted_value": self.extracted_value,
            "required_value": self.required_value,
            "status": self.status.value,
            "reason": self.reason,
            "provenance": self.provenance,
            "verification_required": self.verification_required,
        }


class EvidenceGapReport(BaseModel):
    """
    Comprehensive report summarizing evidence coverage, missing documents,
    expired certificates, and contradictory test claims.
    """
    report_id: str
    total_requirements: int = 0
    supported_count: int = 0
    partial_count: int = 0
    missing_count: int = 0
    expired_count: int = 0
    conflicting_count: int = 0
    unverified_count: int = 0
    supported_requirements: List[DocumentRequirementMatch] = Field(default_factory=list)
    partial_requirements: List[DocumentRequirementMatch] = Field(default_factory=list)
    missing_requirements: List[DocumentRequirementMatch] = Field(default_factory=list)
    expired_requirements: List[DocumentRequirementMatch] = Field(default_factory=list)
    conflicting_requirements: List[DocumentRequirementMatch] = Field(default_factory=list)
    unverified_requirements: List[DocumentRequirementMatch] = Field(default_factory=list)
    overall_evidence_state: str = "INSUFFICIENT_EVIDENCE"
    audit_warnings: List[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "total_requirements": self.total_requirements,
            "supported_count": self.supported_count,
            "partial_count": self.partial_count,
            "missing_count": self.missing_count,
            "expired_count": self.expired_count,
            "conflicting_count": self.conflicting_count,
            "unverified_count": self.unverified_count,
            "supported_requirements": [r.to_dict() for r in self.supported_requirements],
            "partial_requirements": [r.to_dict() for r in self.partial_requirements],
            "missing_requirements": [r.to_dict() for r in self.missing_requirements],
            "expired_requirements": [r.to_dict() for r in self.expired_requirements],
            "conflicting_requirements": [r.to_dict() for r in self.conflicting_requirements],
            "unverified_requirements": [r.to_dict() for r in self.unverified_requirements],
            "overall_evidence_state": self.overall_evidence_state,
            "audit_warnings": self.audit_warnings,
            "created_at": self.created_at,
        }
