"""
app/confidence/models.py

Domain models and controlled vocabularies for Evidence Confidence,
Abstention, and Verification-Required policies.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.evidence.models import EvidenceItem


# ============================================================
# CONTROLLED VOCABULARIES
# ============================================================

class ConfidenceLevel(str, Enum):
    """Categorical confidence level assigned to retrieved evidence."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Decision(str, Enum):
    """Actionable operational decision for answer generation and API behavior."""
    ANSWER = "answer"
    QUALIFIED_ANSWER = "qualified_answer"
    VERIFICATION_REQUIRED = "verification_required"


class QueryState(str, Enum):
    """Epistemic state of the system regarding the user query and evidence."""
    ANSWERABLE = "ANSWERABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    AMBIGUOUS_QUERY = "AMBIGUOUS_QUERY"
    VERIFICATION_REQUIRED = "VERIFICATION_REQUIRED"


# ============================================================
# MEASURABLE EVIDENCE FEATURES
# ============================================================

class ConfidenceFeatures(BaseModel):
    """
    Observable system features extracted from retrieval, knowledge, and query entities.
    Never relies on subjective LLM self-confidence.
    """
    # Query intent
    has_standard_query: bool = False
    has_clause_query: bool = False
    has_amendment_query: bool = False
    has_version_query: bool = False
    query_type: str = "general_semantic"
    is_ambiguous_query: bool = False
    intent: Optional[str] = None
    intent_confidence: Optional[float] = None
    missing_required_context: bool = False

    # Evidence volume & redundancy
    candidate_count: int = 0
    supporting_evidence_count: int = 0
    unique_documents_count: int = 0
    duplicate_chunks_detected: int = 0

    # Retrieval scores & agreement
    top_reranker_score: Optional[float] = None
    top_fusion_score: float = 0.0
    dense_bm25_agreement: bool = False

    # Entity & Scope matches
    exact_standard_match: bool = False
    standard_conflict: bool = False
    exact_clause_match: bool = False
    exact_amendment_match: bool = False
    exact_version_match: bool = False

    # Provenance & Knowledge
    provenance_completeness: float = 0.0
    knowledge_resolved: bool = False
    source_authority: Optional[str] = None

    # Uncertainty & Conflict
    conflict_detected: bool = False
    conflicting_evidence_count: int = 0


# ============================================================
# CONFIDENCE RESULT & TRACE
# ============================================================

class ConfidenceResult(BaseModel):
    """
    Structured outcome of the evidence confidence evaluation.
    Documented as an 'evidence confidence score', never a probability.
    """
    score: float = Field(..., ge=0.0, le=1.0, description="Deterministic evidence confidence score in [0.0, 1.0].")
    level: ConfidenceLevel
    decision: Decision
    query_state: QueryState
    reasons: List[str] = Field(default_factory=list)

    supporting_evidence_count: int = 0
    conflicting_evidence_count: int = 0

    verification_required: bool = False
    verification_reason: Optional[str] = None
    unresolved_aspects: List[str] = Field(default_factory=list)
    required_information: List[str] = Field(default_factory=list)

    # Detailed trace for debugging and audit logging
    trace: Optional[Dict[str, Any]] = None
