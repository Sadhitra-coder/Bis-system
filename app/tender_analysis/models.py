"""
app/tender_analysis/models.py

Canonical Domain Models for Tender / Customer Specification Gap Analyzer (Phase 13).

DESIGN PRINCIPLES:
  - Reuses Phase 12 TechnicalParameter and StandardTechnicalRequirement without duplication.
  - Compares buyer/customer/tender specifications against relevant candidate BIS requirements.
  - States: MATCH, GAP, CONFLICT, UNSPECIFIED, UNVERIFIED.
  - Standard Link States: SUPPORTED_BY_STANDARD, PARTIALLY_SUPPORTED, NOT_FOUND_IN_STANDARD,
    CONFLICTS_WITH_STANDARD, UNVERIFIED.
  - Mandatory language (e.g. "shall", "must") represents customer desire only, never legal obligation.
  - Strictly forbids declaring legal compliance or contract validity.
  - Multi-standard support: A tender requirement may link to multiple standards.
"""

from enum import Enum
import hashlib
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.technical_specs.models import (
    ComparisonOperator,
    MatchState,
    StandardTechnicalRequirement,
    TechnicalParameter,
    TechnicalSpecification,
)


class TenderGapState(str, Enum):
    """
    Classification of gap between tender requirement and standard evidence.
    """
    MATCH = "MATCH"
    GAP = "GAP"
    CONFLICT = "CONFLICT"
    UNSPECIFIED = "UNSPECIFIED"
    UNVERIFIED = "UNVERIFIED"


class StandardLinkState(str, Enum):
    """
    State of linking a customer tender requirement to BIS standard evidence.
    """
    SUPPORTED_BY_STANDARD = "SUPPORTED_BY_STANDARD"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    NOT_FOUND_IN_STANDARD = "NOT_FOUND_IN_STANDARD"
    CONFLICTS_WITH_STANDARD = "CONFLICTS_WITH_STANDARD"
    UNVERIFIED = "UNVERIFIED"


class TenderRequirement(BaseModel):
    """
    Explicit buyer/customer requirement extracted from a tender or RFQ document.
    Reuses Phase 12 TechnicalParameter for structured physical parameters.
    """
    requirement_id: str
    text: str
    parameter_name: str
    parameter: Optional[TechnicalParameter] = None
    value: Optional[Any] = None
    unit: Optional[str] = None
    operator: Optional[ComparisonOperator] = None
    mandatory_language: Optional[str] = None   # e.g. "shall", "must", "mandatory"
    category: Optional[str] = None           # "dimension", "capacity", "performance", "material", "test", "certification", "documentation"
    source_chunk_id: Optional[str] = None
    page: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "text": self.text,
            "parameter_name": self.parameter_name,
            "parameter": self.parameter.to_dict() if self.parameter else None,
            "value": self.value,
            "unit": self.unit,
            "operator": self.operator.value if self.operator else None,
            "mandatory_language": self.mandatory_language,
            "category": self.category,
            "source_chunk_id": self.source_chunk_id,
            "page": self.page,
        }


class TenderDocument(BaseModel):
    """
    Canonical representation of a tender, tender section, or buyer RFQ.
    """
    tender_id: str
    title: Optional[str] = None
    issuer: Optional[str] = None
    source_document_id: Optional[str] = None
    requirements: List[TenderRequirement] = Field(default_factory=list)
    raw_text: Optional[str] = None
    created_at: float = Field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tender_id": self.tender_id,
            "title": self.title,
            "issuer": self.issuer,
            "source_document_id": self.source_document_id,
            "requirements": [r.to_dict() for r in self.requirements],
            "raw_text": self.raw_text,
            "created_at": self.created_at,
        }


class TenderStandardMatch(BaseModel):
    """
    Evidence-backed linkage between one tender requirement and standard evidence.
    """
    match_id: str
    tender_requirement_id: str
    standard_id: str
    standard_number: str
    clause_id: Optional[str] = None
    clause_number: Optional[str] = None
    clause_title: Optional[str] = None
    standard_requirement: Optional[StandardTechnicalRequirement] = None
    link_state: StandardLinkState
    gap_state: TenderGapState
    reason: str
    supporting_evidence_chunk_ids: List[str] = Field(default_factory=list)
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    temporal_status: Optional[str] = None
    confidence: float = 1.0
    verification_required: bool = False
    verification_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "match_id": self.match_id,
            "tender_requirement_id": self.tender_requirement_id,
            "standard_id": self.standard_id,
            "standard_number": self.standard_number,
            "clause_id": self.clause_id,
            "clause_number": self.clause_number,
            "clause_title": self.clause_title,
            "standard_requirement": self.standard_requirement.to_dict() if self.standard_requirement else None,
            "link_state": self.link_state.value if isinstance(self.link_state, StandardLinkState) else str(self.link_state),
            "gap_state": self.gap_state.value if isinstance(self.gap_state, TenderGapState) else str(self.gap_state),
            "reason": self.reason,
            "supporting_evidence_chunk_ids": self.supporting_evidence_chunk_ids,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "temporal_status": self.temporal_status,
            "confidence": round(float(self.confidence), 4),
            "verification_required": self.verification_required,
            "verification_reason": self.verification_reason,
        }


class TripartiteComparison(BaseModel):
    """
    Tripartite evaluation: Customer Tender Request vs Product Specification vs BIS Standard Evidence.
    Ensures clear separation between customer desires and regulatory standards.
    """
    tender_requirement: TenderRequirement
    spec_parameter: Optional[TechnicalParameter] = None
    standard_requirement: Optional[StandardTechnicalRequirement] = None
    tender_vs_standard: TenderGapState
    product_vs_tender: MatchState
    product_vs_standard: MatchState
    synthesis_notes: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tender_requirement": self.tender_requirement.to_dict(),
            "spec_parameter": self.spec_parameter.to_dict() if self.spec_parameter else None,
            "standard_requirement": self.standard_requirement.to_dict() if self.standard_requirement else None,
            "tender_vs_standard": self.tender_vs_standard.value if isinstance(self.tender_vs_standard, TenderGapState) else str(self.tender_vs_standard),
            "product_vs_tender": self.product_vs_tender.value if isinstance(self.product_vs_tender, MatchState) else str(self.product_vs_tender),
            "product_vs_standard": self.product_vs_standard.value if isinstance(self.product_vs_standard, MatchState) else str(self.product_vs_standard),
            "synthesis_notes": self.synthesis_notes,
        }


class TenderGapAnalysisReport(BaseModel):
    """
    Comprehensive gap analysis report comparing a tender against BIS standard requirements and product specs.
    """
    report_id: str
    tender: TenderDocument
    matched_standards: List[str] = Field(default_factory=list)
    matches: List[TenderStandardMatch] = Field(default_factory=list)
    gaps: List[TenderStandardMatch] = Field(default_factory=list)
    conflicts: List[TenderStandardMatch] = Field(default_factory=list)
    tripartite_comparisons: List[TripartiteComparison] = Field(default_factory=list)
    verification_required: bool = False
    verification_reasons: List[str] = Field(default_factory=list)
    summary: str = ""

    @property
    def total_tender_requirements(self) -> int:
        return len(self.tender.requirements)

    @property
    def match_count(self) -> int:
        return len(self.matches)

    @property
    def gap_count(self) -> int:
        return len(self.gaps)

    @property
    def conflict_count(self) -> int:
        return len(self.conflicts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "tender": self.tender.to_dict(),
            "matched_standards": self.matched_standards,
            "matches": [m.to_dict() for m in self.matches],
            "gaps": [g.to_dict() for g in self.gaps],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "tripartite_comparisons": [t.to_dict() for t in self.tripartite_comparisons],
            "verification_required": self.verification_required,
            "verification_reasons": self.verification_reasons,
            "summary": self.summary,
        }
