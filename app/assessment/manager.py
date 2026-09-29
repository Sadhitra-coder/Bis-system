"""
app/assessment/manager.py

Assessment Lifecycle Management & Context Isolation (Requirement 4 & 10).
Guarantees:
  - Every new product assessment creates a new assessment/version/state.
  - No reuse of cached retrieval results across different product assessments.
  - Context binding to (assessment_id, product_id, query_id, profile_version, knowledge_version).
"""

import hashlib
import logging
import threading
import uuid
from typing import Any, Dict, Optional

from app.assessment.models import (
    AssessmentLifecycleState,
    ProductPassportAssessment,
    VerificationLevel,
)
from app.product_mapping.models import ProductContext
from app.query_intelligence.models import StructuredQueryIntent

logger = logging.getLogger(__name__)


def derive_product_id(product_name: Optional[str], category: Optional[str] = None) -> str:
    """Derives a deterministic product identifier from normalized product facts."""
    tokens = []
    if product_name:
        tokens.append(product_name.strip().lower())
    if category:
        tokens.append(category.strip().lower())
    key = "::".join(tokens) if tokens else f"unknown_product_{uuid.uuid4().hex[:8]}"
    return f"prod_{hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]}"


class AssessmentManager:
    """
    Thread-safe manager for Product Passport assessments.
    Enforces clean assessment lifecycles and context isolation across queries.
    """

    def __init__(self, default_knowledge_version: str = "v1.0-official"):
        self._lock = threading.Lock()
        self._assessments: Dict[str, ProductPassportAssessment] = {}
        self._active_assessment: Optional[ProductPassportAssessment] = None
        self.default_knowledge_version = default_knowledge_version

    def start_assessment(
        self,
        product_name: Optional[str] = None,
        product_category: Optional[str] = None,
        raw_query: str = "",
        normalized_product_context: Optional[ProductContext] = None,
        query_intent: Optional[StructuredQueryIntent] = None,
        knowledge_version: Optional[str] = None,
        profile_version: Optional[str] = None,
    ) -> ProductPassportAssessment:
        """
        Creates and registers a brand new, isolated ProductPassportAssessment (Requirement 4).
        """
        p_name = product_name or (normalized_product_context.product_name if normalized_product_context else None)
        p_cat = product_category or (normalized_product_context.product_category if normalized_product_context else None)
        product_id = derive_product_id(p_name, p_cat)

        assessment_id = f"ass_{uuid.uuid4().hex[:12]}"
        query_id = f"qry_{uuid.uuid4().hex[:12]}"
        kv = knowledge_version or self.default_knowledge_version

        identity = {
            "product_name": p_name,
            "product_category": p_cat,
            "product_id": product_id,
            "profile_version": profile_version or "v1.0",
        }

        assessment = ProductPassportAssessment(
            assessment_id=assessment_id,
            product_id=product_id,
            product_identity=identity,
            normalized_product_context=normalized_product_context,
            query_id=query_id,
            raw_query=raw_query,
            query_intent=query_intent,
            lifecycle_state=AssessmentLifecycleState.NEW,
            retrieval_context={
                "bound_context": {
                    "assessment_id": assessment_id,
                    "product_id": product_id,
                    "query_id": query_id,
                    "knowledge_version": kv,
                    "profile_version": profile_version or "v1.0",
                }
            },
            evidence_set=[],
            decision="verification_required",
            verification_level=VerificationLevel.VERIFICATION_REQUIRED,
            knowledge_version=kv,
        )

        with self._lock:
            self._assessments[assessment_id] = assessment
            self._active_assessment = assessment

        logger.info(
            "Created new ProductPassportAssessment [%s] for product_id [%s] (product: '%s')",
            assessment_id,
            product_id,
            p_name or p_cat or "unnamed",
        )
        return assessment

    def get_or_create_for_query(
        self,
        query: str,
        product_context: Optional[ProductContext] = None,
        query_intent: Optional[StructuredQueryIntent] = None,
        force_new: bool = False,
        normalized_product_context: Optional[ProductContext] = None,
        knowledge_version: Optional[str] = None,
        profile_version: Optional[str] = None,
    ) -> ProductPassportAssessment:
        """
        Retrieves active assessment if product context matches, or initiates
        a new assessment if product has changed or force_new is requested (Requirement 10).
        """
        p_ctx = normalized_product_context if normalized_product_context is not None else product_context
        p_name = getattr(p_ctx, "product_name", None) or getattr(p_ctx, "primary_identifier", None)
        p_cat = getattr(p_ctx, "product_category", None)
        incoming_prod_id = derive_product_id(p_name, p_cat)

        with self._lock:
            if not force_new and self._active_assessment is not None:
                # Check if current active assessment belongs to EXACT same product
                if self._active_assessment.product_id == incoming_prod_id:
                    # Same product, reuse assessment context or update query
                    self._active_assessment.raw_query = query
                    if query_intent:
                        self._active_assessment.query_intent = query_intent
                    return self._active_assessment

        # Product changed or no active assessment -> create brand new assessment
        return self.start_assessment(
            product_name=p_name,
            product_category=p_cat,
            raw_query=query,
            normalized_product_context=p_ctx,
            query_intent=query_intent,
            knowledge_version=knowledge_version,
            profile_version=profile_version,
        )

    def get_assessment(self, assessment_id: str) -> Optional[ProductPassportAssessment]:
        with self._lock:
            return self._assessments.get(assessment_id)

    @property
    def active_assessment(self) -> Optional[ProductPassportAssessment]:
        with self._lock:
            return self._active_assessment

    def clear(self) -> None:
        """Clears all assessments and active state."""
        with self._lock:
            self._assessments.clear()
            self._active_assessment = None


# Global default assessment manager instance
default_assessment_manager = AssessmentManager()
