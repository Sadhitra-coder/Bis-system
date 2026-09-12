"""
app/product_mapping/__init__.py

Phase 11: Product-to-BIS Standard Mapping Engine.

Public service interfaces:
  - ProductContext
  - ProductStandardCandidate
  - MappingStatus
  - MappingReason
  - MappingReasonType
  - normalize_product_text
  - extract_product_context
  - generate_candidate_queries
  - discover_standard_candidates
  - score_standard_candidate
  - rank_standard_candidates
  - explain_standard_mapping
  - get_product_standard_candidates
"""

from typing import Any, Dict, List, Optional

from app.confidence.models import ConfidenceLevel
from app.product_mapping.models import (
    MappingReason,
    MappingReasonType,
    MappingStatus,
    ProductContext,
    ProductStandardCandidate,
)
from app.product_mapping.extractor import (
    extract_product_context,
    normalize_product_text,
)
from app.product_mapping.engine import (
    aggregate_chunks_by_standard,
    discover_standard_candidates,
    explain_standard_mapping,
    generate_candidate_queries,
    rank_standard_candidates,
    score_standard_candidate,
)


def get_product_standard_candidates(
    product_description: str,
    retriever: Any = None,
    reranker: Any = None,
    knowledge_repo: Any = None,
    temporal_resolver: Any = None,
    top_k: int = 5,
    evidence_confidence_level: Optional[ConfidenceLevel] = None,
) -> Dict[str, Any]:
    """
    High-level service interface for discovering candidate standards from a
    natural language product description.
    """
    pctx = extract_product_context(query=product_description)
    candidates = discover_standard_candidates(
        product_context=pctx,
        retriever=retriever,
        reranker=reranker,
        knowledge_repo=knowledge_repo,
        temporal_resolver=temporal_resolver,
        top_k=top_k,
        evidence_confidence_level=evidence_confidence_level,
    )
    return {
        "product_context": pctx.to_dict(),
        "candidates": [c.to_dict() for c in candidates],
        "total_candidates": len(candidates),
        "has_candidates": len(candidates) > 0,
        "verification_required": len(candidates) == 0 or any(c.verification_required for c in candidates),
    }


__all__ = [
    "MappingReason",
    "MappingReasonType",
    "MappingStatus",
    "ProductContext",
    "ProductStandardCandidate",
    "aggregate_chunks_by_standard",
    "discover_standard_candidates",
    "explain_standard_mapping",
    "extract_product_context",
    "generate_candidate_queries",
    "get_product_standard_candidates",
    "normalize_product_text",
    "rank_standard_candidates",
    "score_standard_candidate",
]
