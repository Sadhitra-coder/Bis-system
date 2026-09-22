"""
app/knowledge/models.py

Canonical Domain Models for BIS Compliance Intelligence:
- Standard
- StandardVersion
- StandardPart
- Clause
- Amendment
- StandardReference
- Controlled vocabularies for status and relationships

Preserves full source provenance down to source_document_id, source_chunk_ids, and pages.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ============================================================
# CONTROLLED VOCABULARIES
# ============================================================

class StandardStatus(str, Enum):
    """Controlled vocabulary for BIS standard / version / amendment status."""
    DRAFT = "draft"
    PUBLISHED = "published"
    EFFECTIVE = "effective"
    WITHDRAWN = "withdrawn"
    SUPERSEDED = "superseded"
    UNKNOWN = "unknown"


class ReferenceType(str, Enum):
    """Relationship type between BIS standards or standard clauses."""
    REFERENCES = "REFERENCES"
    NORMATIVE_REFERENCE = "NORMATIVE_REFERENCE"
    INFORMATIVE_REFERENCE = "INFORMATIVE_REFERENCE"
    SUPERSEDES = "SUPERSEDES"
    AMENDS = "AMENDS"


class ResolutionStatus(str, Enum):
    """Whether a referenced target standard ID has been resolved in the store."""
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"


# ============================================================
# CANONICAL KNOWLEDGE ENTITIES
# ============================================================

class Standard(BaseModel):
    """
    Canonical Bureau of Indian Standards (BIS) Standard entity.
    Maintains persistent identity across multiple versions and source documents.
    """
    standard_id: str
    standard_number: str            # Normalized canonical identifier (e.g. 'IS 3055')
    standard_title: Optional[str] = None
    authority: Optional[str] = "BIS"
    standard_year: Optional[int] = None
    part_number: Optional[str] = None
    document_id: str                # Traceability: initial or current source document
    edition_or_version: Optional[str] = None
    publication_date: Optional[str] = None
    effective_date: Optional[str] = None
    withdrawal_date: Optional[str] = None
    status: StandardStatus = StandardStatus.UNKNOWN
    source_url: Optional[str] = None
    is_current: Optional[bool] = None
    created_at: float
    updated_at: float


class StandardVersion(BaseModel):
    """
    Explicit version or edition of a standard over time.
    Prevents historical standard editions from being overwritten.
    """
    version_id: str                 # Deterministic ID for this specific version
    standard_id: str                # Parent standard relationship
    edition: Optional[str] = None   # e.g. 'Third Edition'
    standard_year: Optional[int] = None
    publication_date: Optional[str] = None
    effective_date: Optional[str] = None
    withdrawal_date: Optional[str] = None
    status: StandardStatus = StandardStatus.UNKNOWN
    document_id: str                # Traceability to source PDF
    source_url: Optional[str] = None
    created_at: float


class StandardPart(BaseModel):
    """
    Represents a discrete part of a multi-part standard (e.g. IS 1234 Part 1).
    """
    part_id: str
    standard_id: str
    part_number: str                # e.g. '1', '2'
    part_title: Optional[str] = None
    document_id: str
    source_url: Optional[str] = None
    created_at: float


class Clause(BaseModel):
    """
    Canonical hierarchical clause model within a standard or version.
    Supports both numeric ('4.1.2') and non-numeric section headings.
    """
    clause_id: str                  # Deterministic ID within standard
    standard_id: str
    version_id: Optional[str] = None
    part_id: Optional[str] = None
    parent_clause_id: Optional[str] = None  # None for top-level clauses
    clause_number: Optional[str] = None     # e.g. '4.1.2'
    clause_title: str                       # Human-readable title
    level: int = 1                          # Depth: 1 for '4', 2 for '4.1', etc.
    document_id: str                        # Traceability
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    heading_path: str                       # e.g. '4 Requirements > 4.1 Calibration'
    source_chunk_ids: List[str] = Field(default_factory=list)
    created_at: float


class Amendment(BaseModel):
    """
    Explicit amendment document modifying or updating a parent standard.
    """
    amendment_id: str
    standard_id: str
    version_id: Optional[str] = None
    amendment_number: str                   # e.g. '1', '2'
    title: Optional[str] = None
    publication_date: Optional[str] = None
    effective_date: Optional[str] = None
    source_document_id: str                 # Traceability to amendment source doc
    source_url: Optional[str] = None
    status: StandardStatus = StandardStatus.UNKNOWN
    created_at: float


class StandardReference(BaseModel):
    """
    Explicit citation / reference relationship between standards.
    Supports unresolved targets when target standard is not yet ingested.
    """
    relationship_id: str
    source_standard_id: str
    target_standard_number: str             # Normalized referenced number (e.g. 'IS 4984')
    target_standard_id: Optional[str] = None # Nullable until resolved
    relationship_type: ReferenceType = ReferenceType.REFERENCES
    source_document_id: str
    source_clause_id: Optional[str] = None
    source_chunk_id: Optional[str] = None
    resolution_status: ResolutionStatus = ResolutionStatus.UNRESOLVED
    confidence: float = 1.0
    created_at: float


class KnowledgeDiagnostics(BaseModel):
    """Observability diagnostics for a knowledge extraction run."""
    standards_created: int = 0
    versions_created: int = 0
    amendments_created: int = 0
    parts_created: int = 0
    clauses_created: int = 0
    references_created: int = 0
    unresolved_references: int = 0
    validation_warnings: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)


# ============================================================
# EXPANDED COMPLIANCE GRAPH ENTITIES (Requirement 6 & 7)
# ============================================================

class RelationshipType(str, Enum):
    """Expanded canonical knowledge graph relationship types."""
    APPLIES_TO = "APPLIES_TO"
    AMENDS = "AMENDS"
    SUPERSEDES = "SUPERSEDES"
    WITHDRAWN_BY = "WITHDRAWN_BY"
    REFERENCES = "REFERENCES"
    REQUIRES = "REQUIRES"
    CERTIFIED_UNDER = "CERTIFIED_UNDER"
    TESTED_BY = "TESTED_BY"
    EFFECTIVE_FROM = "EFFECTIVE_FROM"
    EFFECTIVE_TO = "EFFECTIVE_TO"


class QCO(BaseModel):
    """Quality Control Order statutory regulation issued by central ministry."""
    qco_id: str
    qco_number: str                 # e.g. 'S.O. 4345(E)'
    title: str
    issuing_ministry: str           # e.g. 'Ministry of Consumer Affairs'
    order_date: Optional[str] = None
    enforcement_date: Optional[str] = None
    document_id: str
    is_mandatory: bool = True
    created_at: float


class ProductEntity(BaseModel):
    """Industrial or consumer product category covered by standards/QCOs."""
    product_id: str
    product_name: str
    category: str
    hs_code: Optional[str] = None
    created_at: float


class CertificationScheme(BaseModel):
    """BIS conformity assessment scheme (Scheme I - ISI Mark, Scheme II - CRS, Scheme IV - Hallmarking)."""
    scheme_id: str
    scheme_name: str
    scheme_code: str
    description: Optional[str] = None
    created_at: float


class TestMethod(BaseModel):
    """Laboratory test method and sampling standard."""
    test_method_id: str
    test_number: str
    title: str
    sampling_procedure: Optional[str] = None
    created_at: float


class ProductManual(BaseModel):
    """BIS Product Certification Manual / Scheme of Testing & Inspection (STI)."""
    manual_id: str
    standard_id: str
    title: str
    edition_or_year: Optional[str] = None
    document_id: str
    source_url: Optional[str] = None
    created_at: float


class Laboratory(BaseModel):
    """BIS recognized or NABL accredited testing laboratory."""
    lab_id: str
    name: str
    city: str
    state: str
    accreditation_number: Optional[str] = None
    scope_of_testing: List[str] = Field(default_factory=list)
    created_at: float


class AuthorityEntity(BaseModel):
    """Statutory or regulatory governing authority."""
    authority_id: str
    name: str
    jurisdiction: str
    portal_url: Optional[str] = None
    created_at: float


class JurisdictionEntity(BaseModel):
    """First-class geographical jurisdiction."""
    jurisdiction_code: str          # e.g. 'INDIA:NATIONAL', 'UK:ENGLAND'
    country: str                    # 'INDIA', 'UK'
    level: str                      # 'NATIONAL', 'STATE'
    name: str
    created_at: float


class KnowledgeRelationship(BaseModel):
    """Multi-hop directed graph relationship between any two knowledge entities."""
    relationship_id: str
    source_entity_type: str         # 'standard', 'qco', 'product', 'manual'
    source_entity_id: str
    target_entity_type: str         # 'product', 'standard', 'scheme', 'lab'
    target_entity_id: str
    relationship_type: RelationshipType
    metadata: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    created_at: float

