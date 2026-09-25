from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field


# ============================================================
# PROVENANCE / DOCUMENT-LEVEL METADATA
# ============================================================

class DocumentMetadata(BaseModel):
    """
    Document-level provenance metadata.
    Built once per PDF ingestion run. Never fabricated — all
    Optional fields remain None when the signal is absent.
    """
    document_id: str
    source_file: str
    source_filename: str
    source_hash: Optional[str] = None
    document_title: Optional[str] = None
    document_type: Optional[str] = None          # e.g. 'indian_standard', 'amendment'
    authority: Optional[str] = None              # e.g. 'BIS'
    standard_number: Optional[str] = None
    standard_title: Optional[str] = None
    standard_year: Optional[int] = None
    edition_or_version: Optional[str] = None
    part_number: Optional[str] = None
    amendment_number: Optional[str] = None
    publication_date: Optional[str] = None
    effective_date: Optional[str] = None
    withdrawal_date: Optional[str] = None
    is_current: Optional[bool] = None
    source_url: Optional[str] = None
    ingestion_timestamp: Optional[float] = None
    parser_version: str = '1.0'


# ============================================================
# DOCUMENT MODELS
# ============================================================

class DocumentCreate(BaseModel):
    """
    Basic information about a document.
    """

    document_id: str

    filename: str


class DocumentStatus(BaseModel):
    """
    Tracks the current ingestion status.
    """

    document_id: str

    status: str

    message: Optional[str] = None

    current_step: Optional[str] = None

    error: Optional[str] = None


# ============================================================
# EXTRACTION MODELS
# ============================================================

class ExtractionResult(BaseModel):
    """
    Result returned after PDF extraction.
    """

    document_id: str

    source_path: str

    markdown_path: str

    markdown_content: str

    page_count: Optional[int] = None

    status: str = "extracted"


# ============================================================
# CLEANING MODELS
# ============================================================

class CleaningResult(BaseModel):
    """
    Result after lightweight Markdown cleaning.

    IMPORTANT:
    Cleaning must never intentionally remove
    meaningful document information.
    """

    document_id: str

    markdown_content: str

    changes_made: List[str] = Field(
        default_factory=list
    )

    status: str = "cleaned"


# ============================================================
# DOCUMENT STRUCTURING MODELS
# ============================================================

class StructuringResult(BaseModel):
    """
    Result returned by the AI document structuring agent.
    """

    document_id: str

    structured_markdown: str

    model_used: str

    status: str = "structured"

    warnings: List[str] = Field(
        default_factory=list
    )


# ============================================================
# VALIDATION MODELS
# ============================================================

class ValidationResult(BaseModel):
    """
    Result after validating the structured document.
    """

    document_id: str

    is_valid: bool

    issues: List[str] = Field(
        default_factory=list
    )

    warnings: List[str] = Field(
        default_factory=list
    )

    status: str = "validated"


# ============================================================
# CHUNK MODELS (CANONICAL METADATA CONTRACT)
# ============================================================

class ChunkMetadata(BaseModel):
    """
    Canonical metadata contract for a document chunk.
    All stages (structure, chunk, embed, retrieve) conform to this schema.
    Unknown values remain None; values are never fabricated.
    """
    chunk_id: str
    document_id: str
    source_file: str
    section: Optional[str] = None
    heading_context: Optional[List[str]] = None
    standard_number: Optional[str] = None
    standard_year: Optional[int] = None
    part: Optional[str] = None
    clause_id: Optional[str] = None
    page_number: Optional[int] = None          # kept for backward compat; alias of page_start
    effective_date: Optional[str] = None
    amendment: Optional[str] = None
    is_current: Optional[bool] = None
    char_offset_start: Optional[int] = None
    char_offset_end: Optional[int] = None
    # ---- Phase 2 provenance fields ----
    page_start: Optional[int] = None           # first page this chunk appears on
    page_end: Optional[int] = None             # last page (same as page_start for single-page)
    content_hash: Optional[str] = None         # SHA-256[:32] of chunk content
    chunk_index: Optional[int] = None          # ordinal position within document
    standard_title: Optional[str] = None       # full title of the standard
    clause_title: Optional[str] = None         # human-readable heading of this clause
    part_number: Optional[str] = None          # Part N from IS XXXX-N or (Part N)
    amendment_number: Optional[str] = None     # Amendment/Amd number if present
    edition_or_version: Optional[str] = None   # e.g. 'Second Edition'
    publication_date: Optional[str] = None
    withdrawal_date: Optional[str] = None
    authority: Optional[str] = None            # e.g. 'BIS'
    document_type: Optional[str] = None        # e.g. 'indian_standard'
    source_url: Optional[str] = None
    source_hash: Optional[str] = None          # SHA-256 of source PDF
    parser_version: str = '1.0'
    # ---- Phase 5 contextual retrieval fields ----
    source_content: Optional[str] = None       # authoritative unaltered source text
    contextualized_content: Optional[str] = None # retrieval-aid text with verified structural preface
    context_generation_method: Optional[str] = None # "structural" | "llm"
    context_generation_version: Optional[str] = None # e.g. "1.0"
    # ---- Phase 6 index contract + knowledge join fields ----
    # schema_version records which persisted-metadata contract this chunk was
    # written under. Its ABSENCE is how a stale index is detected: a chunk
    # written before Phase 6 carries no version at all.
    schema_version: Optional[str] = None
    # standard_id / version_id are the knowledge-model join keys. They are
    # populated ONLY when the source document IS the standard it names — a
    # manual that merely cites IS 3055 keeps standard_number and gets
    # standard_relation='reference' with no identity. See
    # app.knowledge.normalization.classify_standard_relation.
    standard_id: Optional[str] = None
    version_id: Optional[str] = None
    # Version-scoped cls_* identity of the owning Clause. Distinct from
    # clause_id above, which is the clause NUMBER as printed ('4.1') and is
    # what intent-aware ranking matches against.
    knowledge_clause_id: Optional[str] = None
    # 'identity' | 'reference' | 'none' — why the identity fields are set or empty.
    standard_relation: Optional[str] = None


class DocumentChunk(BaseModel):
    """
    A semantic chunk generated from the document.
    """
    chunk_id: str
    content: str
    metadata: ChunkMetadata
    source_content: Optional[str] = None
    contextualized_content: Optional[str] = None


class ContextualChunk(BaseModel):
    """
    Canonical Phase 5 representation of a chunk with dual representation:
    authoritative source_content vs retrieval-aid contextualized_content.
    """
    chunk_id: str
    document_id: str
    source_content: str
    contextualized_content: str
    standard_number: Optional[str] = None
    standard_title: Optional[str] = None
    standard_year: Optional[int] = None
    standard_id: Optional[str] = None
    version_id: Optional[str] = None
    edition_or_version: Optional[str] = None
    part_number: Optional[str] = None
    section: Optional[str] = None
    heading_context: Optional[List[str]] = None
    clause_id: Optional[str] = None
    clause_title: Optional[str] = None
    amendment_number: Optional[str] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    source_hash: Optional[str] = None
    source_file: Optional[str] = None
    context_generation_method: str = "structural"
    context_generation_version: str = "1.0"



class ChunkingResult(BaseModel):
    """
    Result after structure-aware chunking.
    """
    document_id: str
    total_chunks: int
    chunks: List[DocumentChunk]
    status: str = "chunked"


# ============================================================
# EMBEDDING MODELS
# ============================================================

class EmbeddedChunk(BaseModel):
    """
    A document chunk with its vector embedding.
    """
    chunk: DocumentChunk
    embedding: List[float]


class EmbeddingResult(BaseModel):
    """
    Result after embeddings are generated.
    """
    document_id: str
    total_embeddings: int
    embedded_chunks: List[EmbeddedChunk]
    status: str = "embedded"


# ============================================================
# RETRIEVAL MODELS
# ============================================================

class RetrievedChunk(BaseModel):
    """
    A chunk returned from initial vector retrieval.
    """
    chunk_id: str
    content: str
    similarity: float
    metadata: Dict[str, Any]


class RerankedChunk(BaseModel):
    """
    A chunk after reranking.
    """
    chunk_id: str
    content: str
    retrieval_score: float
    rerank_score: float
    metadata: Dict[str, Any]


# ============================================================
# JOB / BACKGROUND TASK MODELS
# ============================================================

class JobStatus(str):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class JobInfo(BaseModel):
    job_id: str
    document_id: str
    filename: str
    status: str
    chunks_indexed: int = 0
    error: Optional[str] = None
    created_at: float
    completed_at: Optional[float] = None


# ============================================================
# QUERY MODELS
# ============================================================

class QueryRequest(BaseModel):
    """
    Request sent to the RAG system.
    """
    query: str
    correlation_id: Optional[str] = None
    document_ids: Optional[List[str]] = None
    top_k: Optional[int] = None
    # Phase 10 Business & Profile Context fields
    business_context: Optional[Dict[str, Any]] = None
    profile_context: Optional[Dict[str, Any]] = None
    # Phase 12 Technical Specification fields
    technical_specification: Optional[Dict[str, Any]] = None
    # Phase 13 Tender Specification fields
    tender_specification: Optional[Dict[str, Any]] = None
    # Phase 14 Compliance & Business Documents fields
    compliance_documents: Optional[List[Dict[str, Any]]] = None
    audience: Optional[str] = "technical"


class QueryResponse(BaseModel):
    """
    Final answer returned by the RAG system.
    """
    query_id: Optional[str] = None
    correlation_id: Optional[str] = None
    query: str
    answer: str
    sources: List[Dict[str, Any]]
    retrieved_chunks: int
    reranked_chunks: Optional[int] = None
    model: Optional[str] = None
    language: Optional[str] = "en"
    laboratories: Optional[List[Dict[str, Any]]] = None
    # Phase 7 Evidence & Confidence fields
    confidence_score: Optional[float] = None
    confidence_level: Optional[str] = None
    decision: Optional[str] = None
    query_state: Optional[str] = None
    verification_required: bool = False
    verification_reason: Optional[str] = None
    evidence_summary: Optional[str] = None
    confidence_trace: Optional[Dict[str, Any]] = None
    # Phase 8 Citations & Grounding Validation fields
    citations: Optional[List[Dict[str, Any]]] = None
    claims: Optional[List[Dict[str, Any]]] = None
    citation_coverage: Optional[float] = None
    grounding_status: Optional[str] = None
    grounding_reason: Optional[str] = None
    groundedness_score: Optional[float] = None
    # Phase 9 Temporal & Version Intelligence fields
    temporal_status: Optional[str] = None
    temporal_resolution: Optional[Dict[str, Any]] = None
    candidate_versions: Optional[List[str]] = None
    temporal_conflict: bool = False
    temporal_verification_required: bool = False
    temporal_trace: Optional[Dict[str, Any]] = None
    # Phase 10 Query Intelligence & Business Context fields
    intent: Optional[str] = None
    intent_confidence: Optional[float] = None
    query_context: Optional[Dict[str, Any]] = None
    # Phase 11 Product-to-Standard Candidate Mapping fields
    product_context: Optional[Dict[str, Any]] = None
    candidate_standards: Optional[List[Dict[str, Any]]] = None
    # Phase 12 Technical Specification Analysis fields
    technical_specification_data: Optional[Dict[str, Any]] = None
    technical_analysis: Optional[Dict[str, Any]] = None
    # Phase 13 Tender Specification Analysis fields
    tender_analysis: Optional[Dict[str, Any]] = None
    # Phase 14 Document Intelligence & Evidence Gap fields
    evidence_gap_report: Optional[Dict[str, Any]] = None
    # Phase 15 Applicability Intelligence & Compliance Readiness fields
    compliance_readiness: Optional[Dict[str, Any]] = None


# ============================================================
# BIS KNOWLEDGE GRAPH RE-EXPORTS (Phase 3)
# ============================================================

from app.knowledge.models import (
    Standard,
    StandardVersion,
    StandardPart,
    Clause,
    Amendment,
    StandardReference,
    StandardStatus,
    ReferenceType,
    ResolutionStatus,
    KnowledgeDiagnostics,
)

from app.grounding.models import (
    Citation,
    AnswerClaim,
    ClaimType,
    SupportStatus,
    GroundingStatus,
    GroundingResult,
)

from app.temporal.models import (
    TemporalRelationshipType,
    TemporalStatus,
    ClauseEvolutionState,
    ValidityInterval,
    TemporalRelationship,
    AmendmentDetail,
    VersionTimelineEntry,
    VersionTimeline,
    ClauseEvolution,
    TemporalConflict,
    TemporalResolution,
    TemporalTrace,
    TemporalAuditReport,
)

from app.query_intelligence.models import (
    QueryIntentType,
    QueryLifecycleState,
    RetrievalStrategy,
    IntentClassification,
    BusinessContext,
    QueryContext,
)

from app.product_mapping.models import (
    MappingReason,
    MappingReasonType,
    MappingStatus,
    ProductContext,
    ProductStandardCandidate,
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

from app.tender_analysis.models import (
    StandardLinkState,
    TenderDocument,
    TenderGapAnalysisReport,
    TenderGapState,
    TenderRequirement,
    TenderStandardMatch,
    TripartiteComparison,
)

from app.document_intelligence.models import (
    BusinessDocument,
    DocumentMatchStatus,
    DocumentRequirementMatch,
    DocumentType,
    EvidenceGapReport,
)

from app.applicability.models import (
    ApplicabilityAssessment,
    ApplicabilityStatus,
    ComplianceReadinessReport,
    ComplianceReadinessState,
)




