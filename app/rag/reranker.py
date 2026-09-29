import logging
import re
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


def compute_intent_ranking_key(
    item: Any,
    entities: QueryEntities,
    query_context: Optional[Any] = None,
) -> tuple:
    """
    Computes a multi-tier sorting key and ranking reason implementing the
    multi-signal ranking policy (Requirements 1 & 11):
        1. Product domain compatibility (product_match_tier)
        2. Query intent compatibility (intent_match_tier)
        3. Hard standard scope validity (scope_valid)
        4. Exact identifier satisfaction (identifier_tier)
        5. Version satisfaction (version_tier)
        6. Source authority tier (authority_tier)
        7. Currentness tier (currentness_tier)
        8. CrossEncoder relevance (reranker_score)
        9. Fused RRF retrieval relevance (fusion_score)

    Returns:
        (product_match_tier, intent_match_tier, scope_valid, identifier_tier,
         version_tier, authority_tier, currentness_tier, reranker_score, fusion_score, reason)
    """
    cand_std = str(_get_field(item, "standard_number", "") or "").strip().upper()
    cand_clause = str(_get_field(item, "clause_id", "") or "").strip()
    cand_clause_title = str(_get_field(item, "clause_title", "") or "").strip().lower()
    cand_amd = str(_get_field(item, "amendment_number", "") or "").strip()
    cand_year = _get_field(item, "standard_year")
    cand_ver = str(_get_field(item, "edition_or_version", "") or "").strip().lower()
    cand_title = str(_get_field(item, "standard_title", "") or "").strip().lower()
    cand_doc_type = str(_get_field(item, "document_type", "") or "").strip().lower()
    cand_content = str(_get_field(item, "content", "") or "").lower()
    cand_meta = _get_field(item, "metadata", {}) or {}
    reranker_score = float(_get_field(item, "reranker_score", float("-inf")))
    fusion_score = float(_get_field(item, "fusion_score", 0.0))
    reason = ""

    # 1. Product domain matching (Requirement 1 & 11)
    target_product = ""
    if query_context is not None:
        if getattr(query_context, "structured_intent", None) and getattr(query_context.structured_intent, "product", None):
            target_product = str(query_context.structured_intent.product).lower().strip()
        elif getattr(query_context, "business_context", None) and getattr(query_context.business_context, "product_name", None):
            target_product = str(query_context.business_context.product_name).lower().strip()

    product_match_tier = 0
    if target_product:
        # Extract meaningful product tokens (len >= 3)
        stop_words = {"what", "does", "standard", "apply", "product", "need", "test", "testing", "india", "indian", "order", "with", "from", "under"}
        target_tokens = [w for w in re.findall(r"\b[a-zA-Z]{3,}\b", target_product) if w not in stop_words]
        
        cand_prods_str = str(cand_meta.get("applicable_products", "") or "").lower()
        full_cand_text = f"{cand_title} {cand_prods_str} {cand_content[:300]}"

        has_target_token = any(tok in full_cand_text for tok in target_tokens)
        if has_target_token:
            product_match_tier = 2
            reason = "target_product_match"
        elif cand_title:
            # Check if cand_title specifies an explicit different product domain
            cand_title_tokens = [w for w in re.findall(r"\b[a-zA-Z]{4,}\b", cand_title) if w not in stop_words and w not in ("specification", "requirements", "method", "safety", "general", "part", "section")]
            if cand_title_tokens and not any(tok in cand_title for tok in target_tokens):
                # Strong conflict: candidate belongs to an unrelated product
                product_match_tier = -3
                reason = "conflicting_product_domain"
            else:
                product_match_tier = 0
                reason = "neutral_product"
        else:
            product_match_tier = 0
            reason = "neutral_product"
    else:
        product_match_tier = 0
        reason = "unspecified_product"

    # 2. Query intent compatibility (Requirement 3 & 13)
    intent_val = None
    if query_context is not None and getattr(query_context, "intent", None):
        raw_intent = getattr(query_context.intent, "intent", None)
        intent_val = getattr(raw_intent, "value", str(raw_intent)).upper()

    intent_match_tier = 1
    if intent_val == "LABORATORY_SEARCH":
        if cand_doc_type == "laboratory" or "testing laboratory" in cand_content or "nabl" in cand_content:
            intent_match_tier = 2
            reason += ";lab_intent_match"
        else:
            intent_match_tier = -1
    else:
        # Non-laboratory intents must penalize standalone laboratory directory chunks
        if cand_doc_type == "laboratory" or "directory of laboratories" in cand_content or "testing facilities recognized" in cand_content:
            intent_match_tier = -3
            reason += ";suppressed_laboratory_chunk"
        elif intent_val in ("QCO_APPLICABILITY", "APPLICABILITY_QUERY"):
            if "qco" in cand_doc_type or "order" in cand_doc_type or "order" in cand_title or "scope" in cand_clause_title:
                intent_match_tier = 2
                reason += ";qco_intent_match"
        elif intent_val in ("TESTING_REQUIREMENT", "REQUIREMENT_DISCOVERY"):
            if "test" in cand_clause_title or "requirement" in cand_clause_title or "sampling" in cand_clause_title:
                intent_match_tier = 2
                reason += ";testing_intent_match"

    # 3. Hard scope validity
    if entities.standard_number:
        req_std = entities.standard_number.strip().upper()
        if cand_std:
            if cand_std == req_std:
                scope_valid = 2
                reason += ";exact_standard_match"
            else:
                scope_valid = -1  # Conflicting standard
                reason += ";conflicting_standard"
        else:
            scope_valid = 1  # Standard unknown / unannotated
    else:
        scope_valid = 1

    # 4. Exact identifier satisfaction
    if entities.clause_id:
        req_clause = entities.clause_id.strip()
        if cand_clause and cand_clause == req_clause:
            identifier_tier = 3
            reason += f";exact_clause_match:{req_clause}"
        elif cand_clause and cand_clause.startswith(req_clause + "."):
            identifier_tier = 2
            reason += f";subclause_match:{cand_clause}"
        elif not cand_clause:
            identifier_tier = 1
            reason += ";same_standard_header_null_clause"
        else:
            identifier_tier = 0
            reason += f";different_clause:{cand_clause}"

    elif entities.amendment_number:
        req_amd = entities.amendment_number.strip()
        if cand_amd and cand_amd == req_amd:
            identifier_tier = 3
            reason += f";exact_amendment_match:{req_amd}"
        elif not cand_amd:
            identifier_tier = 1
            reason += ";base_standard_no_amendment"
        else:
            identifier_tier = 0
            reason += f";different_amendment:{cand_amd}"

    elif entities.standard_number:
        identifier_tier = 1
        reason += ";standard_level_match"

    else:
        identifier_tier = 1
        reason += ";semantic_match"

    # 5. Version satisfaction
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

    # 6. Source authority tier
    cand_auth = str(_get_field(item, "authority", "") or cand_meta.get("authority", "") or "").upper()
    if any(a in cand_auth for a in ["BIS", "DPIIT", "GOI", "GOVERNMENT OF INDIA", "MINISTRY"]) or cand_doc_type in ("standard", "qco"):
        authority_tier = 1
        reason += ";official_authority"
    else:
        authority_tier = 0

    # 7. Currentness tier
    cand_status = str(cand_meta.get("status", "") or _get_field(item, "status", "") or "").lower()
    is_curr = cand_meta.get("is_current")
    if cand_status in ("withdrawn", "superseded") or is_curr is False:
        currentness_tier = -1
        reason += ";outdated_version"
    else:
        currentness_tier = 1

    return (
        product_match_tier,
        intent_match_tier,
        scope_valid,
        identifier_tier,
        version_tier,
        authority_tier,
        currentness_tier,
        reranker_score,
        fusion_score,
        reason,
    )



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
        top_k: Optional[int] = None,
        query_context: Optional[Any] = None,
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
            (
                p_tier,
                i_tier,
                scope_valid,
                id_tier,
                ver_tier,
                auth_tier,
                curr_tier,
                r_score,
                f_score,
                reason,
            ) = compute_intent_ranking_key(item, entities, query_context=query_context)
            if hasattr(item, "ranking_reason"):
                item.ranking_reason = reason
            elif isinstance(item, dict):
                item["ranking_reason"] = reason
            return (p_tier, i_tier, scope_valid, id_tier, ver_tier, auth_tier, curr_tier, r_score, f_score)

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