from __future__ import annotations

from typing import Any, TypedDict

from app.schemas.queries import QueryUnderstanding


class BISGraphState(TypedDict, total=False):

    # ---------------------------------------------------------
    # USER INPUT
    # ---------------------------------------------------------

    query: str

    # ---------------------------------------------------------
    # QUERY UNDERSTANDING
    # ---------------------------------------------------------

    query_understanding: QueryUnderstanding

    # ---------------------------------------------------------
    # METADATA RETRIEVAL
    # ---------------------------------------------------------

    candidate_documents: list[dict[str, Any]]

    # ---------------------------------------------------------
    # DOCUMENT SELECTION
    # ---------------------------------------------------------

    selected_document_ids: list[int]

    # ---------------------------------------------------------
    # CACHE / DOWNLOAD
    # ---------------------------------------------------------

    cached_documents: list[int]

    documents_to_process: list[int]

    downloaded_paths: dict[int, str]

    processed_documents: list[int]

    # ---------------------------------------------------------
    # RETRIEVAL
    # ---------------------------------------------------------

    retrieved_chunks: list[dict[str, Any]]

    reranked_chunks: list[dict[str, Any]]

    # ---------------------------------------------------------
    # EVIDENCE
    # ---------------------------------------------------------

    validated_evidence: list[dict[str, Any]]

    evidence_sufficient: bool

    # ---------------------------------------------------------
    # FINAL ANSWER
    # ---------------------------------------------------------

    answer: str

    # ---------------------------------------------------------
    # DEBUGGING
    # ---------------------------------------------------------

    errors: list[str]

    debug: dict[str, Any]