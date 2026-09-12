"""
app/query_intelligence/strategy.py

Retrieval Strategy Selection, Query Lifecycle State Determination,
and Conservative Query Variant Generation (Phase 10).

DESIGN PRINCIPLES:
  - Intent directly determines retrieval strategy prior to execution.
  - Query state identifies ambiguous and missing-context conditions upfront.
  - Multi-query variants are conservative and directly traceable to original input.
"""

from typing import Any, Dict, List, Optional

from app.rag.query import QueryEntities
from app.query_intelligence.models import (
    BusinessContext,
    IntentClassification,
    QueryIntentType,
    QueryLifecycleState,
    RetrievalStrategy,
)


def select_retrieval_strategy(
    intent: IntentClassification,
    entities: QueryEntities,
) -> RetrievalStrategy:
    """
    Selects the operational retrieval strategy according to the classified intent.
    """
    t = intent.intent

    if t == QueryIntentType.CLAUSE_LOOKUP:
        return RetrievalStrategy.IDENTIFIER_HEAVY

    elif t == QueryIntentType.STANDARD_LOOKUP:
        return RetrievalStrategy.STANDARD_METADATA

    elif t in (QueryIntentType.CURRENTNESS_QUERY, QueryIntentType.VERSION_LOOKUP, QueryIntentType.AMENDMENT_LOOKUP):
        return RetrievalStrategy.TEMPORAL_AWARE

    elif t == QueryIntentType.REQUIREMENT_DISCOVERY:
        return RetrievalStrategy.SEMANTIC_CONTEXTUAL

    elif t == QueryIntentType.STANDARD_DISCOVERY:
        return RetrievalStrategy.STANDARD_DISCOVERY

    elif t == QueryIntentType.REFERENCE_LOOKUP:
        return RetrievalStrategy.REFERENCE_GRAPH

    elif t == QueryIntentType.APPLICABILITY_QUERY:
        return RetrievalStrategy.APPLICABILITY_EVALUATION

    elif t == QueryIntentType.AMBIGUOUS_QUERY:
        return RetrievalStrategy.BROAD_FALLBACK

    return RetrievalStrategy.SEMANTIC_CONTEXTUAL


def determine_query_state(
    intent: IntentClassification,
    entities: QueryEntities,
    business_context: BusinessContext,
) -> QueryLifecycleState:
    """
    Determines the lifecycle state of the query.
    Enforces MISSING_REQUIRED_CONTEXT when an applicability inquiry lacks product details.
    """
    # 1. Ambiguous query state
    if intent.is_ambiguous or intent.intent == QueryIntentType.AMBIGUOUS_QUERY:
        return QueryLifecycleState.AMBIGUOUS

    # 2. Missing context for applicability inquiries (Section 17)
    if intent.intent == QueryIntentType.APPLICABILITY_QUERY:
        if not business_context.has_explicit_product:
            return QueryLifecycleState.MISSING_REQUIRED_CONTEXT

    # 3. Temporal query state
    if (
        intent.intent in (QueryIntentType.CURRENTNESS_QUERY, QueryIntentType.VERSION_LOOKUP)
        or bool(getattr(entities, "relative_temporal", None))
    ):
        return QueryLifecycleState.TEMPORAL

    # 4. Identifier specific
    if bool(entities.standard_number or entities.clause_id or entities.amendment_number):
        return QueryLifecycleState.IDENTIFIER_SPECIFIC

    # 5. Semantic discovery
    if intent.intent in (QueryIntentType.REQUIREMENT_DISCOVERY, QueryIntentType.STANDARD_DISCOVERY):
        return QueryLifecycleState.SEMANTIC

    return QueryLifecycleState.NORMAL


def generate_query_variants(
    query: str,
    intent: Any,
    entities: QueryEntities,
    business_context: Optional[BusinessContext] = None,
) -> List[str]:
    """
    Generates conservative, traceable retrieval query variants without hallucinating
    unsupported domain mappings or synonyms (Section 13, 14).
    """
    variants: List[str] = [query]
    seen = {query.strip().lower()}

    target_intent = intent.intent if hasattr(intent, "intent") else intent
    b_ctx = business_context or BusinessContext()

    def _add_variant(v: str):
        cleaned = v.strip()
        k = cleaned.lower()
        if cleaned and k not in seen:
            seen.add(k)
            variants.append(cleaned)

    # Variant for Standard Discovery with explicit product
    if target_intent == QueryIntentType.STANDARD_DISCOVERY and b_ctx.product_category:
        prod = b_ctx.product_category
        _add_variant(f"Indian Standard specification {prod}")
        _add_variant(f"Quality Control Order {prod}")

    # Variant for Applicability Query
    elif target_intent == QueryIntentType.APPLICABILITY_QUERY:
        if entities.standard_number and b_ctx.product_category:
            _add_variant(f"{entities.standard_number} scope {b_ctx.product_category}")
            _add_variant(f"{entities.standard_number} clause 1 scope")

    # Variant for Clause Lookup
    elif target_intent == QueryIntentType.CLAUSE_LOOKUP:
        if entities.standard_number and entities.clause_id:
            _add_variant(f"{entities.standard_number} clause {entities.clause_id}")
            _add_variant(f"{entities.standard_number} section {entities.clause_id}")

    return variants
