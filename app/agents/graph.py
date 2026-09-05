from __future__ import annotations

import sys
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agents.state import BISGraphState
from app.agents.query_understanding import understand_query
from app.database.repositories.document import DocumentRepository
from app.database.session import SessionLocal
from app.documents.cache import is_cached
from app.documents.downloader import DocumentDownloader
from app.config import settings


# ============================================================
# 1. QUERY UNDERSTANDING
# ============================================================

def query_understanding_node(
    state: BISGraphState,
) -> dict[str, Any]:

    query = state["query"]

    try:
        understanding = understand_query(query)

        print("\nQUERY UNDERSTANDING")
        print(understanding.model_dump())

        return {
            "query_understanding": understanding,
            "debug": {
                **state.get("debug", {}),
                "query_understanding":
                    understanding.model_dump(),
            },
        }

    except Exception as exc:

        error = (
            f"Query understanding failed: {exc}"
        )

        return {
            "errors": [
                *state.get("errors", []),
                error,
            ],
            "answer":
                "I could not understand the query.",
        }


# ============================================================
# 2. METADATA RETRIEVAL
# ============================================================

def metadata_retrieval_node(
    state: BISGraphState,
) -> dict[str, Any]:

    understanding = state.get(
        "query_understanding"
    )

    if understanding is None:

        return {
            "candidate_documents": [],
            "errors": [
                *state.get("errors", []),
                "Query understanding is missing.",
            ],
        }

    db = SessionLocal()

    try:

        repository = DocumentRepository(db)

        candidates = repository.search_from_query(
            understanding
        )

        documents: list[dict[str, Any]] = []

        for document in candidates:

            documents.append(
                {
                    "id": document.id,
                    "title": document.title,
                    "document_url":
                        document.document_url,
                    "canonical_url":
                        document.canonical_url,
                    "document_type":
                        document.document_type,
                    "category":
                        document.category,
                    "subcategory":
                        document.subcategory,
                    "standard_number":
                        document.standard_number,
                    "revision":
                        document.revision,
                    "edition":
                        document.edition,
                    "description":
                        document.description,
                    "keywords":
                        document.keywords,
                    "language":
                        document.language,

                    "publication_date":
                        (
                            document.publication_date.isoformat()
                            if document.publication_date
                            else None
                        ),

                    "effective_date":
                        (
                            document.effective_date.isoformat()
                            if document.effective_date
                            else None
                        ),

                    "crawl_status":
                        document.crawl_status,

                    "parse_status":
                        document.parse_status,

                    "embedding_status":
                        document.embedding_status,
                }
            )

        print("\nCANDIDATE DOCUMENTS")

        for document in documents:

            print(
                f"\n[{document['id']}] "
                f"{document['title']}"
            )

            print(
                f"  Standard: "
                f"{document.get('standard_number')}"
            )

            print(
                f"  Type: "
                f"{document.get('document_type')}"
            )

            print(
                f"  Category: "
                f"{document.get('category')}"
            )

            print(
                f"  URL: "
                f"{document.get('document_url')}"
            )

        return {
            "candidate_documents":
                documents,

            "debug": {
                **state.get("debug", {}),
                "candidate_count":
                    len(documents),
            },
        }

    except Exception as exc:

        error = (
            f"Metadata retrieval failed: {exc}"
        )

        return {
            "candidate_documents": [],

            "errors": [
                *state.get("errors", []),
                error,
            ],
        }

    finally:
        db.close()


# ============================================================
# 3. DOCUMENT SELECTION
# ============================================================

def document_selection_node(
    state: BISGraphState,
) -> dict[str, Any]:

    candidates = state.get(
        "candidate_documents",
        [],
    )

    if not candidates:

        return {
            "selected_document_ids": [],
            "documents_to_process": [],
            "answer":
                "I could not find relevant BIS documents "
                "in the metadata registry.",
        }

    # --------------------------------------------------------
    # IMPORTANT
    #
    # We don't want to download 10 PDFs just because
    # metadata retrieval returned 10 candidates.
    #
    # Start with top 3.
    # Later we will replace this with semantic document
    # selection/reranking.
    # --------------------------------------------------------

    understanding = state.get(
        "query_understanding"
    )

    selection_limit = 3

    if understanding:

        selection_limit = min(
            understanding.limit,
            3,
        )

    selected = candidates[
        :selection_limit
    ]

    selected_ids = [
        int(document["id"])
        for document in selected
    ]

    print(
        "\nSELECTED DOCUMENT IDS"
    )

    print(
        selected_ids
    )

    return {
        "selected_document_ids":
            selected_ids,

        "debug": {
            **state.get("debug", {}),

            "selected_documents":
                selected,
        },
    }


# ============================================================
# 4. CACHE CHECK
# ============================================================

def cache_check_node(
    state: BISGraphState,
) -> dict[str, Any]:

    selected_ids = state.get(
        "selected_document_ids",
        [],
    )

    cached: list[int] = []
    to_process: list[int] = []

    errors = list(
        state.get(
            "errors",
            [],
        )
    )

    for document_id in selected_ids:

        try:

            if is_cached(
                settings.RAW_DATA_DIR,
                document_id,
            ):

                cached.append(
                    document_id
                )

            else:

                to_process.append(
                    document_id
                )

        except Exception as exc:

            # If cache check fails, let downloader
            # attempt the document.
            to_process.append(
                document_id
            )

            errors.append(
                (
                    f"Cache check failed for "
                    f"document {document_id}: "
                    f"{exc}"
                )
            )

    print("\nCACHE")

    print(
        "Cached:",
        cached,
    )

    print(
        "To process:",
        to_process,
    )

    return {
        "cached_documents":
            cached,

        "documents_to_process":
            to_process,

        "errors":
            errors,

        "debug": {
            **state.get("debug", {}),

            "cache": {
                "cached": cached,
                "to_process": to_process,
            },
        },
    }


# ============================================================
# 5. DOWNLOAD SELECTED DOCUMENTS
# ============================================================

def document_processing_node(
    state: BISGraphState,
) -> dict[str, Any]:

    selected_ids = state.get(
        "selected_document_ids",
        [],
    )

    cached_ids = set(
        state.get(
            "cached_documents",
            [],
        )
    )

    to_process = [
        document_id
        for document_id in selected_ids
        if document_id not in cached_ids
    ]

    downloaded_paths: dict[int, str] = {}

    processed_documents = list(
        cached_ids
    )

    errors = list(
        state.get(
            "errors",
            [],
        )
    )

    # --------------------------------------------------------
    # Nothing new to download
    # --------------------------------------------------------

    if not to_process:

        print(
            "\nNo new documents need downloading."
        )

        return {
            "documents_to_process": [],

            "downloaded_paths":
                downloaded_paths,

            "processed_documents":
                processed_documents,

            "errors":
                errors,

            "debug": {
                **state.get("debug", {}),

                "document_processing": {
                    "cached":
                        list(cached_ids),

                    "downloaded":
                        {},
                },
            },
        }

    # --------------------------------------------------------
    # Downloader
    # --------------------------------------------------------

    downloader = DocumentDownloader()

    failed_documents: list[int] = []

    try:

        for document_id in to_process:

            print(
                f"\nDownloading document "
                f"{document_id}..."
            )

            try:

                pdf_path = downloader.download(
                    document_id
                )

                # ------------------------------------------------
                # None means the downloader failed.
                # Do NOT treat it as a successful document.
                # ------------------------------------------------

                if pdf_path is None:

                    failed_documents.append(
                        document_id
                    )

                    errors.append(
                        (
                            f"Failed to download "
                            f"document {document_id}"
                        )
                    )

                    print(
                        f"Download failed: "
                        f"{document_id}"
                    )

                    continue

                downloaded_paths[
                    document_id
                ] = str(pdf_path)

                processed_documents.append(
                    document_id
                )

                print(
                    f"Downloaded: {pdf_path}"
                )

            except Exception as exc:

                failed_documents.append(
                    document_id
                )

                errors.append(
                    (
                        f"Download failed for "
                        f"document {document_id}: "
                        f"{exc}"
                    )
                )

                print(
                    f"Download failed for "
                    f"document {document_id}: "
                    f"{exc}"
                )

    finally:

        downloader.close()

    print(
        "\nDOWNLOADED"
    )

    if downloaded_paths:

        for document_id, path in (
            downloaded_paths.items()
        ):

            print(
                f"[{document_id}] {path}"
            )

    else:

        print(
            "No new documents downloaded."
        )

    print(
        "\nFAILED DOWNLOADS"
    )

    print(
        failed_documents
    )

    return {
        "documents_to_process":
            to_process,

        "downloaded_paths":
            downloaded_paths,

        "processed_documents":
            processed_documents,

        "errors":
            errors,

        "debug": {
            **state.get("debug", {}),

            "document_processing": {
                "cached":
                    list(cached_ids),

                "downloaded":
                    downloaded_paths,

                "failed":
                    failed_documents,
            },
        },
    }


# ============================================================
# 6. RETRIEVAL
# ============================================================

def retrieval_node(
    state: BISGraphState,
) -> dict[str, Any]:

    processed_documents = state.get(
        "processed_documents",
        [],
    )

    if not processed_documents:

        return {
            "retrieved_chunks": [],

            "errors": [
                *state.get("errors", []),
                (
                    "No documents were successfully "
                    "downloaded or cached."
                ),
            ],
        }

    # --------------------------------------------------------
    # TEMPORARY
    #
    # The actual chunk/vector retrieval will be connected
    # here next.
    # --------------------------------------------------------

    print(
        "\nRETRIEVAL"
    )

    print(
        "Documents available:",
        processed_documents,
    )

    return {
        "retrieved_chunks": [],

        "debug": {
            **state.get("debug", {}),

            "retrieval": {
                "documents_available":
                    processed_documents,
            },
        },
    }


# ============================================================
# 7. RERANKING
# ============================================================

def reranking_node(
    state: BISGraphState,
) -> dict[str, Any]:

    chunks = state.get(
        "retrieved_chunks",
        [],
    )

    # Temporary pass-through.
    # Cross-encoder / semantic reranker comes next.

    return {
        "reranked_chunks":
            chunks,
    }


# ============================================================
# 8. EVIDENCE VALIDATION
# ============================================================

def evidence_validation_node(
    state: BISGraphState,
) -> dict[str, Any]:

    chunks = state.get(
        "reranked_chunks",
        [],
    )

    sufficient = (
        len(chunks) > 0
    )

    return {
        "validated_evidence":
            chunks,

        "evidence_sufficient":
            sufficient,
    }


# ============================================================
# 9. DECIDE WHETHER TO ANSWER
# ============================================================

def evidence_router(
    state: BISGraphState,
) -> str:

    if state.get(
        "evidence_sufficient",
        False,
    ):

        return "answer"

    return "answer"


# ============================================================
# 10. ANSWER GENERATION
# ============================================================

def answer_generation_node(
    state: BISGraphState,
) -> dict[str, Any]:

    evidence = state.get(
        "validated_evidence",
        [],
    )

    processed = state.get(
        "processed_documents",
        [],
    )

    errors = state.get(
        "errors",
        [],
    )

    # --------------------------------------------------------
    # Real evidence exists
    # --------------------------------------------------------

    if evidence:

        answer = (
            "Evidence was retrieved from the selected "
            "BIS documents. Final answer generation "
            "will be connected next."
        )

    # --------------------------------------------------------
    # Documents exist but vector retrieval isn't wired yet
    # --------------------------------------------------------

    elif processed:

        answer = (
            "The relevant BIS documents were identified "
            "and successfully downloaded. The document "
            "content retrieval layer is the next step."
        )

    # --------------------------------------------------------
    # Nothing usable
    # --------------------------------------------------------

    else:

        answer = (
            "I could not retrieve sufficient evidence "
            "from the available BIS documents."
        )

    return {
        "answer":
            answer,
    }


# ============================================================
# BUILD LANGGRAPH
# ============================================================

def build_graph():

    workflow = StateGraph(
        BISGraphState
    )

    # --------------------------------------------------------
    # Nodes
    # --------------------------------------------------------

    workflow.add_node(
        "query_understanding",
        query_understanding_node,
    )

    workflow.add_node(
        "metadata_retrieval",
        metadata_retrieval_node,
    )

    workflow.add_node(
        "document_selection",
        document_selection_node,
    )

    workflow.add_node(
        "cache_check",
        cache_check_node,
    )

    workflow.add_node(
        "document_processing",
        document_processing_node,
    )

    workflow.add_node(
        "retrieval",
        retrieval_node,
    )

    workflow.add_node(
        "reranking",
        reranking_node,
    )

    workflow.add_node(
        "evidence_validation",
        evidence_validation_node,
    )

    workflow.add_node(
        "answer_generation",
        answer_generation_node,
    )

    # --------------------------------------------------------
    # Flow
    # --------------------------------------------------------

    workflow.add_edge(
        START,
        "query_understanding",
    )

    workflow.add_edge(
        "query_understanding",
        "metadata_retrieval",
    )

    workflow.add_edge(
        "metadata_retrieval",
        "document_selection",
    )

    workflow.add_edge(
        "document_selection",
        "cache_check",
    )

    workflow.add_edge(
        "cache_check",
        "document_processing",
    )

    workflow.add_edge(
        "document_processing",
        "retrieval",
    )

    workflow.add_edge(
        "retrieval",
        "reranking",
    )

    workflow.add_edge(
        "reranking",
        "evidence_validation",
    )

    workflow.add_conditional_edges(
        "evidence_validation",
        evidence_router,
        {
            "answer":
                "answer_generation",
        },
    )

    workflow.add_edge(
        "answer_generation",
        END,
    )

    return workflow.compile()


# ============================================================
# GRAPH INSTANCE
# ============================================================

graph = build_graph()


# ============================================================
# CLI
# ============================================================

def main():

    if len(sys.argv) < 2:

        print(
            'Usage: '
            'python -m app.agents.graph '
            '"your BIS question"'
        )

        sys.exit(1)

    query = " ".join(
        sys.argv[1:]
    )

    print(
        "\n"
        "=================================================="
    )

    print(
        "BIS GPT"
    )

    print(
        "=================================================="
    )

    print(
        f"\nQuery: {query}"
    )

    initial_state: BISGraphState = {
        "query": query,
        "errors": [],
        "debug": {},
    }

    result = graph.invoke(
        initial_state
    )

    # --------------------------------------------------------
    # Selected
    # --------------------------------------------------------

    print(
        "\nSELECTED DOCUMENT IDS"
    )

    print(
        result.get(
            "selected_document_ids",
            [],
        )
    )

    # --------------------------------------------------------
    # Processed
    # --------------------------------------------------------

    print(
        "\nPROCESSED DOCUMENTS"
    )

    print(
        result.get(
            "processed_documents",
            [],
        )
    )

    # --------------------------------------------------------
    # Answer
    # --------------------------------------------------------

    print(
        "\nANSWER"
    )

    print(
        result.get(
            "answer",
            "No answer generated.",
        )
    )

    # --------------------------------------------------------
    # Errors
    # --------------------------------------------------------

    errors = result.get(
        "errors",
        [],
    )

    if errors:

        print(
            "\nERRORS"
        )

        for error in errors:

            print(
                f" - {error}"
            )

    print(
        "\n"
        "=================================================="
    )


if __name__ == "__main__":
    main()