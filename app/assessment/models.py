"""
app/assessment/models.py

Product Passport Assessment Lifecycle and Domain Models (Requirement 4 & 10).
Provides isolated state tracking, lifecycle transitions, and strict context binding
to prevent cross-product leakage.
"""

from enum import Enum
import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.query_intelligence.models import QueryIntentType, StructuredQueryIntent
from app.product_mapping.models import ProductContext


class AssessmentLifecycleState(str, Enum):
    """
    Clean assessment lifecycle states (Requirement 4).
    NEW -> CLASSIFYING -> RETRIEVING -> VERIFYING -> ASSESSED -> NEEDS_VERIFICATION -> NO_EVIDENCE
    """
    NEW = "NEW"
    CLASSIFYING = "CLASSIFYING"
    RETRIEVING = "RETRIEVING"
    VERIFYING = "VERIFYING"
    ASSESSED = "ASSESSED"
    NEEDS_VERIFICATION = "NEEDS_VERIFICATION"
    NO_EVIDENCE = "NO_EVIDENCE"


class VerificationLevel(str, Enum):
    """
    Safe answer policy verification levels (Requirement 12).
    - VERIFIED: official evidence clearly supports the answer
    - PARTIALLY_VERIFIED: some evidence exists but applicability is incomplete
    - VERIFICATION_REQUIRED: insufficient or conflicting evidence
    """
    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    VERIFICATION_REQUIRED = "VERIFICATION_REQUIRED"


@dataclass
class ProductPassportAssessment:
    """
    Independent assessment container representing one distinct product evaluation.
    Never shares retrieval context, evidence, or cached findings across different products.
    """
    assessment_id: str
    product_id: str
    product_identity: Dict[str, Any]
    normalized_product_context: Optional[ProductContext] = None
    query_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    raw_query: str = ""
    query_intent: Optional[StructuredQueryIntent] = None
    lifecycle_state: AssessmentLifecycleState = AssessmentLifecycleState.NEW
    retrieval_context: Dict[str, Any] = field(default_factory=dict)
    evidence_set: List[Any] = field(default_factory=list)
    decision: str = "verification_required"
    verification_level: VerificationLevel = VerificationLevel.VERIFICATION_REQUIRED
    verification_reason: Optional[str] = None
    generated_answer: str = ""
    timestamp: float = field(default_factory=time.time)
    knowledge_version: str = "v1.0-official"
    trace: Dict[str, Any] = field(default_factory=dict)
    claims: List[Dict[str, Any]] = field(default_factory=list)

    def transition_to(self, new_state: AssessmentLifecycleState, reason: Optional[str] = None) -> None:
        """Advance lifecycle state with optional reason."""
        self.lifecycle_state = new_state
        if reason:
            self.verification_reason = reason

    def update_state(self, new_state: AssessmentLifecycleState, reason: Optional[str] = None) -> None:
        """Advance lifecycle state."""
        self.transition_to(new_state, reason)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "assessment_id": self.assessment_id,
            "product_id": self.product_id,
            "product_identity": self.product_identity,
            "normalized_product_context": self.normalized_product_context.to_dict() if self.normalized_product_context else None,
            "query_id": self.query_id,
            "raw_query": self.raw_query,
            "query_intent": self.query_intent.to_dict() if self.query_intent else None,
            "lifecycle_state": self.lifecycle_state.value,
            "retrieval_context": self.retrieval_context,
            "evidence_count": len(self.evidence_set),
            "decision": self.decision,
            "verification_level": self.verification_level.value,
            "verification_reason": self.verification_reason,
            "generated_answer": self.generated_answer,
            "timestamp": self.timestamp,
            "knowledge_version": self.knowledge_version,
            "trace": self.trace,
            "claims": self.claims,
        }
