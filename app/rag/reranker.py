import logging
from collections.abc import Mapping
from typing import Any, Dict, List, Optional

from sentence_transformers import CrossEncoder

from app.config import settings
from app.rag.query import (
    RetrievalResult,
    normalize_query,
    extract_query_entities,
    QueryEntities,
)


# ============================================================
# LOGGING
# =========================================================

logger = logging.getLogger(__name__)


# ============================================================
# INTENT-AWARE FINAL RANKING POLICY (Prompt 4.2)
# ============================================================

def _get_field(item: Any, key: str, default: Any = None) -> Any:
    if hasattr(item, key):
        val = getattr(item, key)
        return val if val is not None else default
    if isinstance(item, dict):
        val = item.get(key)
        return val if val is not None else default
    return default


def compute_intent_ranking_key(item: Any, entities: QueryEntities) -> tuple:
    """
    Computes a multi-tier sorting key and ranking reason implementing the
    Phase 4.2 intent-aware final ranking contract:
        1. Hard scope validity (scope_valid)
        2. Exact identifier satisfaction (identifier_tier)
        3. Version satisfaction (version_tier)
        4. CrossEncoder relevance (reranker_score)
        5. Fused RRF retrieval relevance (fusion_score)

    Returns:
        (scope_valid, identifier_tier, version_tier, reranker_score, fusion_score, reason)
    """
    cand_std = str(_get_field(item, "standard_number", "") or "").strip().upper()
    cand_clause = str(_get_field(item, "clause_id", "") or "").strip()
    cand_amd = str(_get_field(item, "amendment_number", "") or "").strip()
    cand_year = _get_field(item, "standard_year")
    cand_ver = str(_get_field(item, "edition_or_version", "") or "").strip().lower()
    reranker_score = float(_get_field(item, "reranker_score", float("-inf")))
    fusion_score = float(_get_field(item, "fusion_score", 0.0))

    # 1. Hard scope validity
    if entities.standard_number:
        req_std = entities.standard_number.strip().upper()
        if cand_std:
            if cand_std == req_std:
                scope_valid = 2
            else:
                scope_valid = -1  # Conflicting standard
        else:
            scope_valid = 1  # Standard unknown / unannotated
    else:
        scope_valid = 1

    # 2. Exact identifier satisfaction
    if entities.clause_id:
        req_clause = entities.clause_id.strip()
        if cand_clause and cand_clause == req_clause:
            identifier_tier = 3
            reason = f"exact_clause_match:{req_clause}"
        elif cand_clause and cand_clause.startswith(req_clause + "."):
            identifier_tier = 2
            reason = f"subclause_match:{cand_clause}"
        elif not cand_clause:
            identifier_tier = 1
            reason = "same_standard_header_null_clause"
        else:
            identifier_tier = 0
            reason = f"different_clause:{cand_clause}"

    elif entities.amendment_number:
        req_amd = entities.amendment_number.strip()
        if cand_amd and cand_amd == req_amd:
            identifier_tier = 3
            reason = f"exact_amendment_match:{req_amd}"
        elif not cand_amd:
            identifier_tier = 1
            reason = "base_standard_no_amendment"
        else:
            identifier_tier = 0
            reason = f"different_amendment:{cand_amd}"

    elif entities.standard_number:
        # Standard-only query (e.g. "IS 3055 2024")
        # Header/title chunks legitimately compete on CrossEncoder score
        identifier_tier = 1
        reason = "standard_level_match"

    else:
        # Pure semantic query (e.g. "calibration accuracy requirements")
        identifier_tier = 1
        reason = "semantic_match"

    # 3. Version satisfaction
    if entities.standard_year or entities.edition_or_version:
        matches_year = (entities.standard_year is not None and cand_year == entities.standard_year)
        matches_ed = bool(entities.edition_or_version and entities.edition_or_version.lower() in cand_ver)
        if matches_year or matches_ed:
            version_tier = 1
            reason += ";version_match"
        elif cand_year or cand_ver:
            version_tier = -1  # Explicitly different version/year
        else:
            version_tier = 0
    else:
        version_tier = 0

    return scope_valid, identifier_tier, version_tier, reranker_score, fusion_score, reason


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
        model_name: Optional[str] = None,
        max_length: int = 512,
        content_key: str = "content"
    ):
        """
        Initialize the Cross-Encoder reranker.

        Parameters
        ----------
        model_name:
            Hugging Face CrossEncoder model name. Defaults to settings.RERANKER_MODEL.

        max_length:
            Maximum token length processed by the model.

        content_key:
            Dictionary key containing the text to rerank.
        """

        self.model_name = model_name or settings.RERANKER_MODEL
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
                (dict, Mapping)
            ):

                logger.warning(
                    "Skipping non-dictionary result."
                )


                continue

            content = result.get("contextualized_content") or result.get(
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
            if isinstance(result, RetrievalResult):
                updated_result = RetrievalResult.from_dict(result.to_dict())
                updated_result.reranker_score = float(score)
            else:
                updated_result = dict(result)
                updated_result["reranker_score"] = float(score)

            reranked_results.append(
                updated_result
            )


        # ----------------------------------------------------
        # INTENT-AWARE FINAL RANKING (Prompt 4.2)
        # ----------------------------------------------------
        entities = extract_query_entities(normalize_query(query))

        # Hard exclusion: If query explicitly requested standard AND clause,
        # exclude candidates from conflicting standards
        if entities.standard_number and entities.clause_id:
            req_std = entities.standard_number.strip().upper()
            filtered = [
                item for item in reranked_results
                if not _get_field(item, "standard_number") or str(_get_field(item, "standard_number")).strip().upper() == req_std
            ]
            if filtered:
                reranked_results = filtered

        # 1. Deterministic tie-break baseline: chunk_id ascending
        reranked_results.sort(key=lambda item: str(_get_field(item, "chunk_id", "")))

        # 2. Multi-tier intent-aware sort (stable sort in reverse)
        def _sort_key(item):
            scope_valid, id_tier, ver_tier, r_score, f_score, reason = compute_intent_ranking_key(item, entities)
            if hasattr(item, "ranking_reason"):
                item.ranking_reason = reason
            elif isinstance(item, dict):
                item["ranking_reason"] = reason
            return (scope_valid, id_tier, ver_tier, r_score, f_score)

        reranked_results.sort(key=_sort_key, reverse=True)


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