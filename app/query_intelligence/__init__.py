"""
app/query_intelligence/__init__.py

Public package interface for Query Intelligence, Intent Classification,
Business Context, and Structured Query Context (Phase 10).
"""

from typing import Any, Dict, List, Optional

from app.rag.query import extract_query_entities, normalize_query
from app.query_intelligence.models import (
    BusinessContext,
    IntentClassification,
    QueryContext,
    QueryIntentType,
    QueryLifecycleState,
    RetrievalStrategy,
)
from app.query_intelligence.business import (
    extract_business_context_from_query,
    merge_business_contexts,
)
from app.query_intelligence.classifier import (
    classify_intent,
    classify_intent_deterministic,
    classify_intent_llm,
)
from app.query_intelligence.strategy import (
    determine_query_state,
    generate_query_variants,
    select_retrieval_strategy,
)


def build_query_context(
    query: str,
    business_context: Optional[Dict[str, Any]] = None,
    profile_context: Optional[Dict[str, Any]] = None,
    client: Optional[Any] = None,
) -> QueryContext:
    """
    Constructs the canonical QueryContext for downstream retrieval,
    confidence evaluation, and answer generation.
    """
    normalized_q = normalize_query(query)
    entities = extract_query_entities(normalized_q)

    # 1. Extract explicit business context from query text
    extracted_ctx = extract_business_context_from_query(query)

    # 2. Parse caller-supplied business context (if provided via API)
    caller_ctx = BusinessContext.from_dict(business_context) if business_context else BusinessContext()

    # 3. Parse user profile context (if provided)
    parsed_profile = BusinessContext.from_dict(profile_context) if profile_context else None

    # 4. Merge query-extracted with caller-supplied context
    effective_query_ctx = BusinessContext(
        business_type=caller_ctx.business_type or extracted_ctx.business_type,
        industry=caller_ctx.industry or extracted_ctx.industry,
        product_name=caller_ctx.product_name or extracted_ctx.product_name,
        product_category=caller_ctx.product_category or extracted_ctx.product_category,
        product_description=caller_ctx.product_description or extracted_ctx.product_description,
        manufacturing_activity=caller_ctx.manufacturing_activity or extracted_ctx.manufacturing_activity,
        manufacturing_location=caller_ctx.manufacturing_location or extracted_ctx.manufacturing_location,
        company_size=caller_ctx.company_size or extracted_ctx.company_size,
        target_market=caller_ctx.target_market or extracted_ctx.target_market,
        intended_use=caller_ctx.intended_use or extracted_ctx.intended_use,
        customer_type=caller_ctx.customer_type or extracted_ctx.customer_type,
        existing_certifications=list(set(caller_ctx.existing_certifications + extracted_ctx.existing_certifications)),
        existing_standards=list(set(caller_ctx.existing_standards + extracted_ctx.existing_standards)),
        technical_characteristics={**caller_ctx.technical_characteristics, **extracted_ctx.technical_characteristics},
        raw_signals=list(set(caller_ctx.raw_signals + extracted_ctx.raw_signals)),
    )

    # 5. Non-destructively overlay profile context
    merged_business_ctx = merge_business_contexts(parsed_profile, effective_query_ctx)

    # 6. Intent Classification
    intent = classify_intent(query=query, entities=entities, client=client)

    # 7. Retrieval Strategy & Query State
    strategy = select_retrieval_strategy(intent=intent, entities=entities)
    query_state = determine_query_state(intent=intent, entities=entities, business_context=merged_business_ctx)

    # 8. Query Variants
    variants = generate_query_variants(
        query=query,
        intent=intent,
        entities=entities,
        business_context=merged_business_ctx,
    )

    # 9. Diagnostic Trace
    trace = {
        "original_query": query,
        "normalized_query": normalized_q,
        "entities": entities.to_dict(),
        "intent": intent.to_dict(),
        "query_state": query_state.value,
        "retrieval_strategy": strategy.value,
        "business_context_signals": merged_business_ctx.raw_signals,
        "variants_count": len(variants),
    }

    return QueryContext(
        original_query=query,
        normalized_query=normalized_q,
        intent=intent,
        entities=entities,
        business_context=merged_business_ctx,
        profile_context=parsed_profile,
        query_state=query_state,
        retrieval_strategy=strategy,
        retrieval_query_variants=variants,
        trace=trace,
    )


__all__ = [
    "QueryIntentType",
    "QueryLifecycleState",
    "RetrievalStrategy",
    "IntentClassification",
    "BusinessContext",
    "QueryContext",
    "build_query_context",
    "classify_intent",
    "classify_intent_deterministic",
    "classify_intent_llm",
    "extract_business_context_from_query",
    "merge_business_contexts",
    "select_retrieval_strategy",
    "determine_query_state",
    "generate_query_variants",
]
