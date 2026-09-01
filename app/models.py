from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field


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
# CHUNK MODELS
# ============================================================

class ChunkMetadata(BaseModel):
    """
    Metadata associated with one document chunk.
    """

    document_id: str

    chunk_index: int

    heading: Optional[str] = None

    section: Optional[str] = None

    clause: Optional[str] = None

    page_reference: Optional[int] = None

    content_type: str = "text"


class DocumentChunk(BaseModel):
    """
    A semantic chunk generated from the document.
    """

    chunk_id: str

    content: str

    metadata: ChunkMetadata


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
# QUERY MODELS
# ============================================================

class QueryRequest(BaseModel):
    """
    Request sent to the RAG system.
    """

    query: str

    document_ids: Optional[List[str]] = None


class QueryResponse(BaseModel):
    """
    Final answer returned by the RAG system.
    """

    answer: str

    sources: List[Dict[str, Any]]

    retrieved_chunks: int