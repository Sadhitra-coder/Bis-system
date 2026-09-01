import logging
from typing import Any, Dict, List, Optional

from app.rag.retriever import HybridRetriever
from app.rag.reranker import Reranker
from app.rag.generator import AnswerGenerator


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# ============================================================
# RAG PIPELINE
# ============================================================

class RAGPipeline:
    """
    Complete Retrieval-Augmented Generation pipeline.

    Flow:

        User Query
            ↓
        Hybrid Retrieval
            ↓
        Candidate Chunks
            ↓
        Cross-Encoder Reranking
            ↓
        Best Context Chunks
            ↓
        Answer Generation
            ↓
        Final Answer
    """

    def __init__(
        self,
        retriever: Optional[HybridRetriever] = None,
        reranker: Optional[Reranker] = None,
        generator: Optional[AnswerGenerator] = None,
        retrieval_top_k: int = 10,
        dense_k: int = 15,
        bm25_k: int = 15,
        rerank_top_k: int = 5
    ):

        print("\n" + "=" * 70)
        print("INITIALIZING RAG PIPELINE")
        print("=" * 70 + "\n")

        # ----------------------------------------------------
        # VALIDATE CONFIGURATION
        # ----------------------------------------------------

        if retrieval_top_k <= 0:

            raise ValueError(
                "retrieval_top_k must be greater than 0."
            )

        if dense_k <= 0:

            raise ValueError(
                "dense_k must be greater than 0."
            )

        if bm25_k <= 0:

            raise ValueError(
                "bm25_k must be greater than 0."
            )

        if rerank_top_k <= 0:

            raise ValueError(
                "rerank_top_k must be greater than 0."
            )

        # ----------------------------------------------------
        # STORE CONFIGURATION
        # ----------------------------------------------------

        self.retrieval_top_k = (
            retrieval_top_k
        )

        self.dense_k = (
            dense_k
        )

        self.bm25_k = (
            bm25_k
        )

        self.rerank_top_k = (
            rerank_top_k
        )

        # ----------------------------------------------------
        # INITIALIZE RETRIEVER
        # ----------------------------------------------------

        logger.info(
            "Initializing retriever..."
        )

        self.retriever = (
            retriever
            if retriever is not None
            else HybridRetriever()
        )

        # ----------------------------------------------------
        # INITIALIZE RERANKER
        # ----------------------------------------------------

        logger.info(
            "Initializing reranker..."
        )

        self.reranker = (
            reranker
            if reranker is not None
            else Reranker()
        )

        # ----------------------------------------------------
        # INITIALIZE GENERATOR
        # ----------------------------------------------------

        logger.info(
            "Initializing answer generator..."
        )

        self.generator = (
            generator
            if generator is not None
            else AnswerGenerator()
        )

        print("\n" + "=" * 70)
        print("RAG PIPELINE READY")
        print("=" * 70)

        logger.info(
            "Pipeline configuration:"
        )

        logger.info(
            f"Retrieval candidates: "
            f"{self.retrieval_top_k}"
        )

        logger.info(
            f"Dense candidates: "
            f"{self.dense_k}"
        )

        logger.info(
            f"BM25 candidates: "
            f"{self.bm25_k}"
        )

        logger.info(
            f"Final reranked chunks: "
            f"{self.rerank_top_k}"
        )

    # ========================================================
    # BUILD SOURCES
    # ========================================================

    def _build_sources(
        self,
        results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Extract clean source information.

        This prevents internal retrieval information from
        being unnecessarily exposed to the application.
        """

        sources = []

        seen = set()

        for result in results:

            if not isinstance(
                result,
                dict
            ):

                continue

            metadata = result.get(
                "metadata",
                {}
            )

            if not isinstance(
                metadata,
                dict
            ):

                metadata = {}

            # ------------------------------------------------
            # EXTRACT SOURCE INFORMATION
            # ------------------------------------------------

            document_id = (
                metadata.get(
                    "document_id"
                )
                or result.get(
                    "document_id"
                )
            )

            section = (
                metadata.get(
                    "section"
                )
                or result.get(
                    "section"
                )
            )

            standard = (
                metadata.get(
                    "standard"
                )
                or result.get(
                    "standard"
                )
            )

            title = (
                metadata.get(
                    "title"
                )
                or result.get(
                    "title"
                )
            )

            source_file = (
                metadata.get(
                    "source_file"
                )
                or result.get(
                    "source_file"
                )
            )

            # ------------------------------------------------
            # DEDUPLICATE SOURCES
            # ------------------------------------------------

            source_key = (
                document_id,
                section,
                standard,
                source_file
            )

            if source_key in seen:

                continue

            seen.add(
                source_key
            )

            # ------------------------------------------------
            # BUILD SOURCE OBJECT
            # ------------------------------------------------

            source = {}

            if document_id:

                source[
                    "document_id"
                ] = document_id

            if title:

                source[
                    "title"
                ] = title

            if standard:

                source[
                    "standard"
                ] = standard

            if section:

                source[
                    "section"
                ] = section

            if source_file:

                source[
                    "source_file"
                ] = source_file

            # Only add meaningful sources

            if source:

                sources.append(
                    source
                )

        return sources

    # ========================================================
    # EMPTY RESPONSE
    # ========================================================

    def _empty_response(
        self,
        query: str
    ) -> Dict[str, Any]:
        """
        Standard response when no relevant information
        is retrieved.
        """

        return {
            "query": query,

            "answer": (
                "I couldn't find relevant information "
                "in the available documentation to answer "
                "this question."
            ),

            "sources": [],

            "retrieved_chunks": 0,

            "reranked_chunks": 0,

            "model": getattr(
                self.generator,
                "model",
                None
            )
        }

    # ========================================================
    # QUERY PIPELINE
    # ========================================================

    def query(
        self,
        query: str,
        retrieval_top_k: Optional[int] = None,
        rerank_top_k: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Run the complete RAG pipeline.

        Parameters
        ----------

        query:
            User's natural language question.

        retrieval_top_k:
            Optional override for number of retrieved
            candidate chunks.

        rerank_top_k:
            Optional override for number of final
            context chunks.

        Returns
        -------

        {
            "query": "...",
            "answer": "...",
            "sources": [...],
            "retrieved_chunks": ...,
            "reranked_chunks": ...,
            "model": "..."
        }
        """

        # ----------------------------------------------------
        # VALIDATE QUERY
        # ----------------------------------------------------

        if not isinstance(
            query,
            str
        ):

            raise TypeError(
                "Query must be a string."
            )

        query = query.strip()

        if not query:

            raise ValueError(
                "Query cannot be empty."
            )

        # ----------------------------------------------------
        # RESOLVE TOP K VALUES
        # ----------------------------------------------------

        final_retrieval_top_k = (
            retrieval_top_k
            if retrieval_top_k is not None
            else self.retrieval_top_k
        )

        final_rerank_top_k = (
            rerank_top_k
            if rerank_top_k is not None
            else self.rerank_top_k
        )

        if final_retrieval_top_k <= 0:

            raise ValueError(
                "retrieval_top_k must be greater than 0."
            )

        if final_rerank_top_k <= 0:

            raise ValueError(
                "rerank_top_k must be greater than 0."
            )

        logger.info(
            "=" * 60
        )

        logger.info(
            f"Processing query: {query}"
        )

        logger.info(
            "=" * 60
        )

        # ====================================================
        # STEP 1: RETRIEVAL
        # ====================================================

        logger.info(
            "Step 1/3: Retrieving candidate chunks..."
        )

        retrieved_results = (
            self.retriever.retrieve(
                query=query,

                top_k=
                    final_retrieval_top_k,

                dense_k=
                    self.dense_k,

                bm25_k=
                    self.bm25_k,

                deduplicate=False
            )
        )

        logger.info(
            f"Retrieved "
            f"{len(retrieved_results)} "
            f"candidate chunks."
        )

        # ----------------------------------------------------
        # NO RETRIEVAL RESULTS
        # ----------------------------------------------------

        if not retrieved_results:

            logger.warning(
                "No relevant chunks retrieved."
            )

            return self._empty_response(
                query
            )

        # ====================================================
        # STEP 2: RERANKING
        # ====================================================

        logger.info(
            "Step 2/3: Reranking candidate chunks..."
        )

        reranked_results = (
            self.reranker.rerank(
                query=query,

                results=
                    retrieved_results,

                top_k=
                    final_rerank_top_k
            )
        )

        logger.info(
            f"Selected "
            f"{len(reranked_results)} "
            f"reranked chunks."
        )

        # ----------------------------------------------------
        # RERANKING FALLBACK
        # ----------------------------------------------------

        # If reranker somehow returns no results,
        # use the best retrieved results.

        if not reranked_results:

            logger.warning(
                "Reranker returned no results. "
                "Using retrieved chunks as fallback."
            )

            reranked_results = (
                retrieved_results[
                    :final_rerank_top_k
                ]
            )

        # ====================================================
        # STEP 3: GENERATION
        # ====================================================

        logger.info(
            "Step 3/3: Generating grounded answer..."
        )

        generation_result = (
            self.generator.generate(
                query=query,

                results=
                    reranked_results
            )
        )

        answer = (
            generation_result.get(
                "answer",
                ""
            )
        )

        # ====================================================
        # BUILD SOURCES
        # ====================================================

        sources = (
            self._build_sources(
                reranked_results
            )
        )

        # ====================================================
        # FINAL RESPONSE
        # ====================================================

        response = {

            "query":
                query,

            "answer":
                answer,

            "sources":
                sources,

            "retrieved_chunks":
                len(
                    retrieved_results
                ),

            "reranked_chunks":
                len(
                    reranked_results
                ),

            "model":
                generation_result.get(
                    "model"
                )
        }

        logger.info(
            "Pipeline completed successfully."
        )

        return response


# ============================================================
# OPTIONAL SIMPLE INTERACTIVE TEST
# ============================================================

if __name__ == "__main__":

    try:

        pipeline = RAGPipeline()

        print("\n" + "=" * 70)
        print("BIS RAG PIPELINE")
        print("=" * 70)

        print(
            "\nType a question."
        )

        print(
            "Type 'exit' to quit.\n"
        )

        while True:

            user_query = input(
                "Question: "
            ).strip()

            if user_query.lower() in (
                "exit",
                "quit"
            ):

                print(
                    "\nExiting pipeline."
                )

                break

            if not user_query:

                print(
                    "\nPlease enter a question.\n"
                )

                continue

            print("\n")

            print(
                "Processing..."
            )

            print("\n")

            result = (
                pipeline.query(
                    user_query
                )
            )

            print(
                "=" * 70
            )

            print(
                "ANSWER"
            )

            print(
                "=" * 70
            )

            print(
                "\n"
                + result["answer"]
            )

            # ------------------------------------------------
            # SOURCES
            # ------------------------------------------------

            sources = result.get(
                "sources",
                []
            )

            if sources:

                print("\n")

                print(
                    "-" * 70
                )

                print(
                    "SOURCES"
                )

                print(
                    "-" * 70
                )

                for index, source in enumerate(
                    sources,
                    start=1
                ):

                    print(
                        f"\n{index}."
                    )

                    for key, value in (
                        source.items()
                    ):

                        print(
                            f"{key}: {value}"
                        )

            print("\n")

    except KeyboardInterrupt:

        print(
            "\n\nPipeline stopped."
        )

    except Exception as error:

        print("\n" + "=" * 70)
        print("PIPELINE ERROR")
        print("=" * 70)

        logger.exception(
            error
        )