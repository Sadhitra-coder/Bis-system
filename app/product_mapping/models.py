"""
app/product_mapping/models.py

Canonical Domain Models for Product-to-BIS Standard Mapping (Phase 11).

DESIGN PRINCIPLES:
  - This phase identifies and ranks CANDIDATE BIS STANDARDS for a product.
  - This phase does NOT decide legal applicability.
  - Never claim 'mandatory', 'legally required', 'certified', 'compliant',
    'approved', or 'licence required' at the candidate mapping stage.
  - Product facts must be explicit: zero inference of unstated attributes
    (e.g., no guessing company size, MSME, location, medical device class).
  - Candidates are aggregated at (standard_id, version_id) level to prevent
    duplicate chunk proliferation.
  - Status taxonomy is conservative and explicitly excludes legal verdicts:
    STRONG_CANDIDATE, POSSIBLE_CANDIDATE, WEAK_CANDIDATE,
    INSUFFICIENT_EVIDENCE, VERIFICATION_REQUIRED.
"""

from enum import Enum
import hashlib
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.query_intelligence.models import BusinessContext


# ============================================================
# 1. CONTROLLED VOCABULARIES (Sections 5, 6, 13)
# ============================================================

class MappingStatus(str, Enum):
    """
    Conservative status for product-to-standard candidate mappings.
    Explicitly excludes legal applicability terms ('APPLICABLE', 'MANDATORY', 'NOT_APPLICABLE').
    """
    STRONG_CANDIDATE = "STRONG_CANDIDATE"
    POSSIBLE_CANDIDATE = "POSSIBLE_CANDIDATE"
    WEAK_CANDIDATE = "WEAK_CANDIDATE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    VERIFICATION_REQUIRED = "VERIFICATION_REQUIRED"


class MappingReasonType(str, Enum):
    """
    Controlled categories of explainable, evidence-backed mapping reasons.
    Every reason must link back to observable retrieved evidence.
    """
    DIRECT_TITLE_MATCH = "DIRECT_TITLE_MATCH"
    PRODUCT_TERM_MATCH = "PRODUCT_TERM_MATCH"
    SEMANTIC_CONTENT_MATCH = "SEMANTIC_CONTENT_MATCH"
    EXPLICIT_REFERENCE = "EXPLICIT_REFERENCE"
    CLAUSE_SUPPORT = "CLAUSE_SUPPORT"
    TECHNICAL_CHARACTERISTIC_MATCH = "TECHNICAL_CHARACTERISTIC_MATCH"
    DOMAIN_MATCH = "DOMAIN_MATCH"


# ============================================================
# 2. MAPPING REASON (Section 13, 14, 34)
# ============================================================

class MappingReason(BaseModel):
    """
    Structured, evidence-backed reason for surfacing a candidate standard.
    Must trace to authoritative source content or knowledge graph relations.
    """
    reason_type: MappingReasonType
    description: str
    evidence_id: Optional[str] = None          # chunk_id, clause_id, or standard_id
    matched_term: Optional[str] = None         # Exact lexical term matched
    score_contribution: float = 0.0            # Contribution to overall mapping_score

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reason_type": self.reason_type.value if isinstance(self.reason_type, MappingReasonType) else str(self.reason_type),
            "description": self.description,
            "evidence_id": self.evidence_id,
            "matched_term": self.matched_term,
            "score_contribution": round(float(self.score_contribution), 4),
        }


# ============================================================
# 3. CANONICAL PRODUCT CONTEXT (Sections 1, 2, 4)
# ============================================================

class ProductContext(BaseModel):
    """
    Canonical representation of a product for standard discovery and mapping.
    All attributes are strictly explicit: never infer unstated facts.
    """
    product_context_id: str
    product_name: Optional[str] = None
    product_category: Optional[str] = None
    product_description: Optional[str] = None

    manufacturing_activity: Optional[str] = None    # e.g. "manufacturing", "importing", "assembly"
    intended_use: Optional[str] = None              # e.g. "clinical diagnosis", "domestic heating"
    customer_type: Optional[str] = None             # e.g. "hospitals", "retail"

    technical_characteristics: Dict[str, Any] = Field(default_factory=dict)
    material: Optional[str] = None                  # e.g. "stainless steel", "mercury", "pvc"
    technology: Optional[str] = None                # e.g. "digital", "electric", "infrared"
    operating_principle: Optional[str] = None       # e.g. "compression", "convection"

    target_market: Optional[str] = None             # e.g. "domestic", "export"
    country_or_region: Optional[str] = None         # e.g. "India", "West Bengal"

    existing_standards: List[str] = Field(default_factory=list)
    existing_certifications: List[str] = Field(default_factory=list)

    raw_query: Optional[str] = None
    normalized_product_description: Optional[str] = None
    extracted_attributes: Dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)

    @property
    def primary_identifier(self) -> str:
        """Best available text identifier for lexical matching."""
        return (
            self.product_name
            or self.product_category
            or self.product_description
            or self.normalized_product_description
            or ""
        ).strip()

    @property
    def is_empty(self) -> bool:
        """True if no meaningful product identity is present."""
        return not bool(self.primary_identifier)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_context_id": self.product_context_id,
            "product_name": self.product_name,
            "product_category": self.product_category,
            "product_description": self.product_description,
            "manufacturing_activity": self.manufacturing_activity,
            "intended_use": self.intended_use,
            "customer_type": self.customer_type,
            "technical_characteristics": self.technical_characteristics,
            "material": self.material,
            "technology": self.technology,
            "operating_principle": self.operating_principle,
            "target_market": self.target_market,
            "country_or_region": self.country_or_region,
            "existing_standards": self.existing_standards,
            "existing_certifications": self.existing_certifications,
            "raw_query": self.raw_query,
            "normalized_product_description": self.normalized_product_description,
            "extracted_attributes": self.extracted_attributes,
            "primary_identifier": self.primary_identifier,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "ProductContext":
        if not d:
            ctx_id = f"pctx_empty_{int(time.time())}"
            return cls(product_context_id=ctx_id)
        return cls(
            product_context_id=d.get("product_context_id") or f"pctx_{hashlib.sha256(str(d).encode('utf-8')).hexdigest()[:12]}",
            product_name=d.get("product_name"),
            product_category=d.get("product_category"),
            product_description=d.get("product_description"),
            manufacturing_activity=d.get("manufacturing_activity"),
            intended_use=d.get("intended_use"),
            customer_type=d.get("customer_type"),
            technical_characteristics=d.get("technical_characteristics") or {},
            material=d.get("material"),
            technology=d.get("technology"),
            operating_principle=d.get("operating_principle"),
            target_market=d.get("target_market"),
            country_or_region=d.get("country_or_region"),
            existing_standards=d.get("existing_standards") or [],
            existing_certifications=d.get("existing_certifications") or [],
            raw_query=d.get("raw_query"),
            normalized_product_description=d.get("normalized_product_description"),
            extracted_attributes=d.get("extracted_attributes") or {},
            created_at=d.get("created_at") or time.time(),
        )

    @classmethod
    def from_business_context(
        cls,
        bc: Optional[BusinessContext],
        raw_query: Optional[str] = None,
        context_id: Optional[str] = None,
    ) -> "ProductContext":
        """
        Creates a ProductContext from an existing BusinessContext without loss of explicit facts.
        """
        if bc is None:
            cid = context_id or f"pctx_adhoc_{hashlib.sha256((raw_query or '').encode('utf-8')).hexdigest()[:12]}"
            return cls(product_context_id=cid, raw_query=raw_query)

        cid = context_id or f"pctx_{hashlib.sha256((str(bc.to_dict()) + (raw_query or '')).encode('utf-8')).hexdigest()[:12]}"
        tech = dict(bc.technical_characteristics)
        material = tech.get("material")
        technology = tech.get("technology")
        operating_principle = tech.get("operating_principle")

        return cls(
            product_context_id=cid,
            product_name=bc.product_name,
            product_category=bc.product_category,
            product_description=bc.product_description,
            manufacturing_activity=bc.manufacturing_activity,
            intended_use=bc.intended_use,
            customer_type=bc.customer_type,
            technical_characteristics=tech,
            material=material,
            technology=technology,
            operating_principle=operating_principle,
            target_market=bc.target_market,
            country_or_region=bc.manufacturing_location,
            existing_standards=list(bc.existing_standards),
            existing_certifications=list(bc.existing_certifications),
            raw_query=raw_query,
            normalized_product_description=bc.product,
            extracted_attributes=tech,
            created_at=time.time(),
        )


# ============================================================
# 4. CANONICAL PRODUCT-STANDARD CANDIDATE (Sections 5, 9, 14, 15)
# ============================================================

class ProductStandardCandidate(BaseModel):
    """
    Canonical representation of a candidate BIS standard matched to a product.
    Aggregated at (standard_id, version_id) level with multi-signal evidence trace.
    """
    mapping_id: str
    product_context_id: str

    standard_id: str
    standard_number: str
    standard_title: Optional[str] = None

    version_id: Optional[str] = None
    edition_or_version: Optional[str] = None

    mapping_status: MappingStatus = MappingStatus.POSSIBLE_CANDIDATE
    mapping_score: float = Field(..., ge=0.0, le=1.0, description="Observable evidence-based mapping score in [0.0, 1.0].")

    mapping_reasons: List[MappingReason] = Field(default_factory=list)

    supporting_evidence_ids: List[str] = Field(default_factory=list)    # chunk_ids
    supporting_clause_ids: List[str] = Field(default_factory=list)      # clause_ids
    source_document_ids: List[str] = Field(default_factory=list)        # document_ids

    temporal_status: Optional[str] = None                               # from Phase 9 TemporalStatus
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)        # from Phase 7 EvidenceEvaluator
    verification_required: bool = False
    verification_reason: Optional[str] = None

    created_at: float = Field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mapping_id": self.mapping_id,
            "product_context_id": self.product_context_id,
            "standard_id": self.standard_id,
            "standard_number": self.standard_number,
            "standard_title": self.standard_title,
            "version_id": self.version_id,
            "edition_or_version": self.edition_or_version,
            "mapping_status": self.mapping_status.value if isinstance(self.mapping_status, MappingStatus) else str(self.mapping_status),
            "mapping_score": round(float(self.mapping_score), 4),
            "mapping_reasons": [r.to_dict() for r in self.mapping_reasons],
            "supporting_evidence_ids": self.supporting_evidence_ids,
            "supporting_clause_ids": self.supporting_clause_ids,
            "source_document_ids": self.source_document_ids,
            "temporal_status": self.temporal_status,
            "confidence_score": round(float(self.confidence_score), 4),
            "verification_required": self.verification_required,
            "verification_reason": self.verification_reason,
            "created_at": self.created_at,
        }
