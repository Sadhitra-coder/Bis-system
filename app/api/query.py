"""
POST /query — grounded retrieval + generation.

REQUEST FIELDS (Phase 6 section 17)
-----------------------------------
`QueryRequest` accepts `top_k` and `document_ids`. Both were previously
parsed and then dropped on the floor: the handler called
`rag_pipeline.query(payload.query)` and nothing else. A caller asking for 3
results got the configured default, and a caller scoping to one document
searched the entire corpus while receiving a 200 that looked correct.

  * `top_k` is now IMPLEMENTED. It bounds the number of context chunks and
    is clamped to settings.MAX_QUERY_TOP_K.
  * `document_ids` is now REJECTED with 400. It is not implemented, and
    saying so is the only honest option — see _UNSUPPORTED_SCOPE_DETAIL.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.models import QueryRequest, QueryResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/query", tags=["query"])


#: Why document_ids is refused rather than best-effort filtered.
#:
#: Scoping needs BOTH retrieval arms constrained. The dense arm can take a
#: Chroma `where` clause, but BM25 is built once over the whole corpus at
#: startup, so the lexical arm would have to be post-filtered — and its
#: global top-k may contain nothing from the requested documents at all.
#: The caller would get a 200 with silently degraded recall, which is
#: strictly worse than a refusal, because nothing in the response reveals
#: that half the retrieval strategy stopped contributing.
_UNSUPPORTED_SCOPE_DETAIL = (
    "document_ids scoping is not supported. The lexical (BM25) retrieval arm "
    "is built over the entire corpus, so filtering by document would silently "
    "reduce recall rather than restrict scope. Omit the field to search all "
    "ingested documents."
)


def _resolve_top_k(requested: int | None) -> int | None:
    """
    Validate a caller-supplied top_k.

    None means "use the configured default", which is the pipeline's own
    behaviour. A non-positive value is a client error rather than something
    to silently coerce: 0 results is never what a caller meant to ask for.
    """
    if requested is None:
        return None
    if requested < 1:
        raise HTTPException(
            status_code=400,
            detail="top_k must be a positive integer.",
        )
    if requested > settings.MAX_QUERY_TOP_K:
        raise HTTPException(
            status_code=400,
            detail=(
                f"top_k must not exceed {settings.MAX_QUERY_TOP_K} "
                f"(requested {requested})."
            ),
        )
    return requested


@router.post("", response_model=QueryResponse)
def query_documents(payload: QueryRequest, request: Request):
    """
    Execute hybrid retrieval, reranking, and generation over ingested documents.
    """
    if payload.document_ids:
        raise HTTPException(status_code=400, detail=_UNSUPPORTED_SCOPE_DETAIL)

    top_k = _resolve_top_k(payload.top_k)

    rag_pipeline = getattr(request.app.state, "rag_pipeline", None)
    if rag_pipeline is None:
        from app.rag.pipeline import RAGPipeline
        from app.rag.retriever import HybridRetriever
        try:
            retriever = HybridRetriever(
                embedder=getattr(request.app.state, "embedder", None),
                collection=getattr(request.app.state, "collection", None)
            )
            rag_pipeline = RAGPipeline(
                retriever=retriever,
                reranker=getattr(request.app.state, "reranker", None),
                generator=getattr(request.app.state, "generator", None)
            )
            request.app.state.rag_pipeline = rag_pipeline
        except Exception as e:
            logger.warning("RAG pipeline not available: %s", e)
            raise HTTPException(
                status_code=503,
                detail=f"RAG pipeline not ready. Ensure documents are ingested: {e}"
            )

    try:
        # top_k is the number of chunks the caller wants grounding the answer,
        # so it maps to rerank_top_k. retrieval_top_k is raised alongside it:
        # asking for 20 final chunks while retrieving 10 candidates would cap
        # the result at 10 and report success.
        kwargs = {}
        if payload.business_context is not None:
            kwargs["business_context"] = payload.business_context
        if payload.profile_context is not None:
            kwargs["profile_context"] = payload.profile_context
        if payload.technical_specification is not None:
            kwargs["technical_specification"] = payload.technical_specification
        if payload.tender_specification is not None:
            kwargs["tender_specification"] = payload.tender_specification
        if payload.compliance_documents is not None:
            kwargs["compliance_documents"] = payload.compliance_documents

        if top_k is None:
            result = rag_pipeline.query(payload.query, **kwargs)
        else:
            result = rag_pipeline.query(
                payload.query,
                retrieval_top_k=max(top_k, settings.RETRIEVAL_TOP_K),
                rerank_top_k=top_k,
                **kwargs,
            )

        cid = payload.correlation_id or getattr(request.state, "correlation_id", None)
        return QueryResponse(
            query_id=result.get("query_id"),
            correlation_id=cid,
            query=result.get("query", payload.query),
            answer=result["answer"],
            sources=result["sources"],
            retrieved_chunks=result["retrieved_chunks"],
            reranked_chunks=result.get("reranked_chunks"),
            model=result.get("model"),
            confidence_score=result.get("confidence_score"),
            confidence_level=result.get("confidence_level"),
            decision=result.get("decision"),
            query_state=result.get("query_state"),
            verification_required=result.get("verification_required", False),
            verification_reason=result.get("verification_reason"),
            evidence_summary=result.get("evidence_summary"),
            confidence_trace=result.get("confidence_trace"),
            citations=result.get("citations"),
            claims=result.get("claims"),
            citation_coverage=result.get("citation_coverage"),
            grounding_status=result.get("grounding_status"),
            grounding_reason=result.get("grounding_reason"),
            groundedness_score=result.get("groundedness_score"),
            temporal_status=result.get("temporal_status"),
            temporal_resolution=result.get("temporal_resolution"),
            candidate_versions=result.get("candidate_versions"),
            temporal_conflict=result.get("temporal_conflict", False),
            temporal_verification_required=result.get("temporal_verification_required", False),
            temporal_trace=result.get("temporal_trace"),
            intent=result.get("intent"),
            intent_confidence=result.get("intent_confidence"),
            query_context=result.get("query_context"),
            product_context=result.get("product_context"),
            candidate_standards=result.get("candidate_standards"),
            technical_specification_data=result.get("technical_specification_data"),
            technical_analysis=result.get("technical_analysis"),
            tender_analysis=result.get("tender_analysis"),
            evidence_gap_report=result.get("evidence_gap_report"),
            compliance_readiness=result.get("compliance_readiness"),
        )


    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error executing query: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
