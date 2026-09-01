import logging
from typing import Any, Dict, List, Optional

from sentence_transformers import CrossEncoder


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# ============================================================
# CROSS-ENCODER RERANKER
# ============================================================

class Reranker:
    """
    Generic Cross-Encoder reranker.

    This class can rerank results from any retriever as long as
    every result contains a textual field.

    Default expected structure:

    {
        "chunk_id": "...",
        "content": "...",
        ...
    }

    The reranker does not depend on:
        - BIS
        - clinical thermometers
        - PDF type
        - document structure
        - ChromaDB
        - BM25

    It only needs:
        1. a query
        2. a list of result dictionaries
        3. text inside the configured content field
    """

    def __init__(
        self,
        model_name: str = (
            "cross-encoder/ms-marco-MiniLM-L-6-v2"
        ),
        max_length: int = 512,
        content_key: str = "content"
    ):
        """
        Initialize the Cross-Encoder reranker.

        Parameters
        ----------
        model_name:
            Hugging Face CrossEncoder model name.

        max_length:
            Maximum token length processed by the model.

        content_key:
            Dictionary key containing the text to rerank.
        """

        self.model_name = model_name
        self.max_length = max_length
        self.content_key = content_key

        logger.info(
            "Loading reranker model: %s",
            self.model_name
        )

        try:

            self.model = CrossEncoder(
                self.model_name,
                max_length=self.max_length
            )

        except Exception as error:

            logger.exception(
                "Failed to load reranker model."
            )

            raise RuntimeError(
                "Could not initialize the "
                "Cross-Encoder reranker."
            ) from error

        logger.info(
            "Reranker model loaded successfully."
        )

    # ========================================================
    # VALIDATE RESULTS
    # ========================================================

    def _prepare_pairs(
        self,
        query: str,
        results: List[Dict[str, Any]]
    ):
        """
        Create query-document pairs.

        Invalid or empty result entries are skipped.

        Returns
        -------
        pairs:
            List of (query, document) tuples.

        valid_results:
            Original valid result dictionaries.
        """

        pairs = []

        valid_results = []

        for result in results:

            if not isinstance(
                result,
                dict
            ):

                logger.warning(
                    "Skipping non-dictionary result."
                )

                continue

            content = result.get(
                self.content_key,
                ""
            )

            # --------------------------------------------
            # HANDLE NONE
            # --------------------------------------------

            if content is None:

                continue

            # --------------------------------------------
            # CONVERT NON-STRING CONTENT
            # --------------------------------------------

            if not isinstance(
                content,
                str
            ):

                content = str(
                    content
                )

            # --------------------------------------------
            # SKIP EMPTY CONTENT
            # --------------------------------------------

            content = content.strip()

            if not content:

                continue

            # --------------------------------------------
            # CREATE PAIR
            # --------------------------------------------

            pairs.append(
                (
                    query,
                    content
                )
            )

            valid_results.append(
                result
            )

        return (
            pairs,
            valid_results
        )

    # ========================================================
    # RERANK
    # ========================================================

    def rerank(
        self,
        query: str,
        results: List[Dict[str, Any]],
        top_k: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Rerank candidate results using a Cross-Encoder.

        Parameters
        ----------
        query:
            User's question or search query.

        results:
            Candidate results returned by a retriever.

            Each result should contain text in the field
            specified by `content_key`.

        top_k:
            Maximum number of reranked results to return.

            If None, returns all valid reranked results.

        Returns
        -------
        List[Dict[str, Any]]

        Results are sorted from most relevant to least relevant.

        Each returned result contains:

            "reranker_score"
        """

        # ----------------------------------------------------
        # VALIDATE QUERY
        # ----------------------------------------------------

        if not isinstance(
            query,
            str
        ):

            logger.warning(
                "Query must be a string."
            )

            return []

        query = query.strip()

        if not query:

            logger.warning(
                "Empty query provided."
            )

            return []

        # ----------------------------------------------------
        # VALIDATE RESULTS
        # ----------------------------------------------------

        if not results:

            logger.warning(
                "No results provided for reranking."
            )

            return []

        if not isinstance(
            results,
            list
        ):

            raise TypeError(
                "results must be a list of dictionaries."
            )

        # ----------------------------------------------------
        # PREPARE QUERY-DOCUMENT PAIRS
        # ----------------------------------------------------

        pairs, valid_results = (
            self._prepare_pairs(
                query=query,
                results=results
            )
        )

        if not pairs:

            logger.warning(
                "No valid text content found "
                "for reranking."
            )

            return []

        logger.info(
            "Reranking %d candidate chunks...",
            len(pairs)
        )

        # ----------------------------------------------------
        # PREDICT RELEVANCE SCORES
        # ----------------------------------------------------

        try:

            scores = self.model.predict(
                pairs,
                show_progress_bar=False
            )

        except Exception as error:

            logger.exception(
                "Reranking failed."
            )

            raise RuntimeError(
                "Failed to generate reranking scores."
            ) from error

        # ----------------------------------------------------
        # ATTACH SCORES
        # ----------------------------------------------------

        reranked_results = []

        for result, score in zip(
            valid_results,
            scores
        ):

            # Copy result.
            # Do not mutate the original result.

            updated_result = dict(
                result
            )

            updated_result[
                "reranker_score"
            ] = float(
                score
            )

            reranked_results.append(
                updated_result
            )

        # ----------------------------------------------------
        # SORT BY RELEVANCE
        # ----------------------------------------------------

        reranked_results.sort(
            key=lambda item: item.get(
                "reranker_score",
                float("-inf")
            ),
            reverse=True
        )

        # ----------------------------------------------------
        # APPLY TOP-K
        # ----------------------------------------------------

        if top_k is not None:

            if top_k <= 0:

                return []

            reranked_results = (
                reranked_results[
                    :top_k
                ]
            )

        return reranked_results