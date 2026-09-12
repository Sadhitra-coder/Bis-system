"""
app/technical_specs/models.py

Canonical Domain Models for Technical Specification Analyzer (Phase 12).

DESIGN PRINCIPLES:
  - Compares product technical specifications against BIS standard technical requirements.
  - Matches are strictly characterization checks, NEVER legal compliance.
  - States: MATCH, MISMATCH, PARTIAL_MATCH, MISSING, UNSPECIFIED, UNVERIFIABLE.
  - Strictly forbids declaring 'COMPLIANT' or 'NON_COMPLIANT'.
  - Every match or mismatch must trace to source evidence chunks and page provenance.
  - Temporal status must be preserved: superseded/uncertain standards trigger verification_required.
"""

from enum import Enum
import hashlib
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class MatchState(str, Enum):
    """
    State of technical specification comparison against standard requirements.
    CRITICAL: Never uses 'COMPLIANT' or 'NON_COMPLIANT'.
    """
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    PARTIAL_MATCH = "PARTIAL_MATCH"
    MISSING = "MISSING"
    UNSPECIFIED = "UNSPECIFIED"
    UNVERIFIABLE = "UNVERIFIABLE"


class ComparisonOperator(str, Enum):
    """
    Comparison operators for numeric and range requirements.
    """
    EQ = "="
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="
    RANGE = "range"
    CONTAINS = "contains"


class TechnicalParameter(BaseModel):
    """
    Represents an explicit technical parameter of a product or standard.
    Never fabricates nominal values or tolerances if unstated.
    """
    parameter_name: str
    value: Optional[Any] = None
    normalized_value: Optional[float] = None
    unit: Optional[str] = None
    normalized_unit: Optional[str] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    nominal_value: Optional[float] = None
    tolerance: Optional[float] = None
    tolerance_unit: Optional[str] = None
    raw_text: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "parameter_name": self.parameter_name,
            "value": self.value,
            "normalized_value": self.normalized_value,
            "unit": self.unit,
            "normalized_unit": self.normalized_unit,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "nominal_value": self.nominal_value,
            "tolerance": self.tolerance,
            "tolerance_unit": self.tolerance_unit,
            "raw_text": self.raw_text,
        }


class TechnicalSpecification(BaseModel):
    """
    Canonical representation of a product's technical specification.
    Built from explicit text, structured attributes, or uploaded documents.
    """
    specification_id: str
    product_id: Optional[str] = None
    product_name: Optional[str] = None
    parameters: Dict[str, TechnicalParameter] = Field(default_factory=dict)
    material: Optional[str] = None
    technology: Optional[str] = None
    dimensions: Optional[str] = None
    capacity: Optional[str] = None
    rating: Optional[str] = None
    operating_conditions: Optional[str] = None
    performance_characteristics: Dict[str, Any] = Field(default_factory=dict)
    source_document_id: Optional[str] = None
    source_chunk_ids: List[str] = Field(default_factory=list)
    page_provenance: Optional[Dict[str, Any]] = None
    raw_text: Optional[str] = None
    created_at: float = Field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "specification_id": self.specification_id,
            "product_id": self.product_id,
            "product_name": self.product_name,
            "parameters": {k: p.to_dict() for k, p in self.parameters.items()},
            "material": self.material,
            "technology": self.technology,
            "dimensions": self.dimensions,
            "capacity": self.capacity,
            "rating": self.rating,
            "operating_conditions": self.operating_conditions,
            "performance_characteristics": self.performance_characteristics,
            "source_document_id": self.source_document_id,
            "source_chunk_ids": self.source_chunk_ids,
            "page_provenance": self.page_provenance,
            "raw_text": self.raw_text,
            "created_at": self.created_at,
        }


class StandardTechnicalRequirement(BaseModel):
    """
    Canonical representation of an explicit technical requirement derived from a BIS standard clause.
    Always grounded in source evidence and clause provenance.
    """
    requirement_id: str
    standard_id: str
    standard_number: Optional[str] = None
    version_id: Optional[str] = None
    clause_id: Optional[str] = None
    clause_number: Optional[str] = None
    clause_title: Optional[str] = None
    requirement_text: str
    parameter_name: str
    operator: ComparisonOperator = ComparisonOperator.EQ
    threshold: Optional[float] = None
    min_threshold: Optional[float] = None
    max_threshold: Optional[float] = None
    unit: Optional[str] = None
    normalized_unit: Optional[str] = None
    tolerance: Optional[float] = None
    source_chunk_ids: List[str] = Field(default_factory=list)
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    temporal_status: Optional[str] = None
    confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "standard_id": self.standard_id,
            "standard_number": self.standard_number,
            "version_id": self.version_id,
            "clause_id": self.clause_id,
            "clause_number": self.clause_number,
            "clause_title": self.clause_title,
            "requirement_text": self.requirement_text,
            "parameter_name": self.parameter_name,
            "operator": self.operator.value if isinstance(self.operator, ComparisonOperator) else str(self.operator),
            "threshold": self.threshold,
            "min_threshold": self.min_threshold,
            "max_threshold": self.max_threshold,
            "unit": self.unit,
            "normalized_unit": self.normalized_unit,
            "tolerance": self.tolerance,
            "source_chunk_ids": self.source_chunk_ids,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "temporal_status": self.temporal_status,
            "confidence": round(float(self.confidence), 4),
        }


class SpecificationComparisonResult(BaseModel):
    """
    Detailed evidence-backed comparison result for one technical parameter/requirement pair.
    """
    result_id: str
    parameter_name: str
    match_state: MatchState
    spec_parameter: Optional[TechnicalParameter] = None
    standard_requirement: Optional[StandardTechnicalRequirement] = None
    reason: str
    evidence_chunk_ids: List[str] = Field(default_factory=list)
    standard_id: Optional[str] = None
    standard_number: Optional[str] = None
    clause_id: Optional[str] = None
    page_provenance: Optional[Dict[str, Any]] = None
    temporal_status: Optional[str] = None
    verification_required: bool = False
    verification_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "result_id": self.result_id,
            "parameter_name": self.parameter_name,
            "match_state": self.match_state.value if isinstance(self.match_state, MatchState) else str(self.match_state),
            "spec_parameter": self.spec_parameter.to_dict() if self.spec_parameter else None,
            "standard_requirement": self.standard_requirement.to_dict() if self.standard_requirement else None,
            "reason": self.reason,
            "evidence_chunk_ids": self.evidence_chunk_ids,
            "standard_id": self.standard_id,
            "standard_number": self.standard_number,
            "clause_id": self.clause_id,
            "page_provenance": self.page_provenance,
            "temporal_status": self.temporal_status,
            "verification_required": self.verification_required,
            "verification_reason": self.verification_reason,
        }


class TechnicalAnalysisReport(BaseModel):
    """
    Aggregate technical specification analysis report against candidate standards.
    """
    analysis_id: str
    product_context_id: Optional[str] = None
    technical_specification: TechnicalSpecification
    requirement_matches: List[SpecificationComparisonResult] = Field(default_factory=list)
    technical_gaps: List[SpecificationComparisonResult] = Field(default_factory=list)
    verification_required: bool = False
    verification_reasons: List[str] = Field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "product_context_id": self.product_context_id,
            "technical_specification": self.technical_specification.to_dict(),
            "requirement_matches": [m.to_dict() for m in self.requirement_matches],
            "technical_gaps": [g.to_dict() for g in self.technical_gaps],
            "verification_required": self.verification_required,
            "verification_reasons": self.verification_reasons,
            "summary": self.summary,
        }
