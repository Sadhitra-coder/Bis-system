"""
app/confidence/evaluator.py

Observable Evidence Confidence Evaluator, Abstention Engine,
and Verification-Required Decision Logic.

Design principles:
  1. Never trust LLM self-confidence — calculated purely from observable signals.
  2. Bounded mathematical formulation — no raw score sums.
  3. Strict epistemic honesty — absence of evidence != negative evidence.
  4. Deduplicates chunks before evaluating independent support.
  5. Knowledge-model & provenance completeness aware.
"""

import logging
import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.confidence.models import (
    ConfidenceFeatures,
    ConfidenceLevel,
    ConfidenceResult,
    Decision,
    QueryState,
)
from app.evidence.models import EvidenceItem
from app.knowledge.join import resolve_chunk
from app.knowledge.normalization import (
    classify_standard_relation,
    normalize_clause_number,
    normalize_standard_number,
    STANDARD_RELATION_IDENTITY,
)
from app.knowledge.repository import KnowledgeRepository
from app.rag.query import extract_query_entities, normalize_query, QueryEntities

logger = logging.getLogger(__name__)

# Prototype policy thresholds (Section 7)
THRESHOLD_HIGH = 0.70
THRESHOLD_MEDIUM = 0.45


def _sigmoid(x: Optional[float]) -> float:
    """Bounded sigmoid mapping real cross-encoder scores to [0.0, 1.0]."""
    if x is None:
        return 0.5
    try:
        # Clamping input to [-10, 10] avoids floating point overflow
        clamped = max(-10.0, min(10.0, float(x)))
        return 1.0 / (1.0 + math.exp(-clamped))
    except (ValueError, TypeError, OverflowError):
        return 0.5


class EvidenceEvaluator:
    """
    Evaluates retrieved evidence items against the user query to compute
    a deterministic evidence confidence score and operational decision.
    """

    @classmethod
    def evaluate(
        cls,
        query: str,
        evidence_items: List[EvidenceItem],
        repo: Optional[KnowledgeRepository] = None,
        temporal_resolution: Optional[Any] = None,
        query_context: Optional[Any] = None,
    ) -> ConfidenceResult:
        """
        Main entry point. Evaluates evidence and returns a ConfidenceResult.
        """
        norm_q = normalize_query(query)
        entities = extract_query_entities(norm_q)

        # -------------------------------------------------------------------
        # 0. QUERY CONTEXT / INTENT EXTRACTION
        # -------------------------------------------------------------------
        ctx_intent_str = None
        ctx_intent_conf = None
        is_missing_context = False
        is_ctx_ambiguous = False
        if query_context is not None:
            if hasattr(query_context, "intent") and query_context.intent is not None:
                q_intent = getattr(query_context.intent, "intent", None)
                ctx_intent_str = getattr(q_intent, "value", str(q_intent)) if q_intent else None
                ctx_intent_conf = getattr(query_context.intent, "intent_confidence", None)
                if getattr(query_context.intent, "is_ambiguous", False) or ctx_intent_str == "AMBIGUOUS_QUERY":
                    is_ctx_ambiguous = True

            q_state = getattr(query_context, "query_state", None)
            q_state_str = getattr(q_state, "value", str(q_state)) if q_state else ""
            if q_state_str == "MISSING_REQUIRED_CONTEXT":
                is_missing_context = True
            elif q_state_str == "AMBIGUOUS":
                is_ctx_ambiguous = True

        # -------------------------------------------------------------------
        # 1. EMPTY / ZERO CANDIDATE HANDLING (Section 8, 9, 10)
        # -------------------------------------------------------------------
        if not evidence_items:
            if is_missing_context:
                return ConfidenceResult(
                    score=0.0,
                    level=ConfidenceLevel.LOW,
                    decision=Decision.VERIFICATION_REQUIRED,
                    query_state=QueryState.VERIFICATION_REQUIRED,
                    reasons=["Missing required business context: applicability evaluation requires explicit product specification."],
                    supporting_evidence_count=0,
                    conflicting_evidence_count=0,
                    verification_required=True,
                    verification_reason="Missing required business context: applicability evaluation requires explicit product specification.",
                    unresolved_aspects=["Applicability determination cannot proceed without knowing the product name, category, or specifications."],
                    required_information=["Product name, product category, or explicit business activity."],
                    trace={"candidate_count": 0, "entities": entities.__dict__, "missing_required_context": True},
                )

            req_info = []
            if entities.standard_number:
                req_info.append(f"Official text of standard {entities.standard_number}")
            if entities.clause_id:
                req_info.append(f"Text of clause {entities.clause_id}")
            if not req_info:
                req_info.append("Authoritative technical documentation matching query topic")

            return ConfidenceResult(
                score=0.0,
                level=ConfidenceLevel.LOW,
                decision=Decision.VERIFICATION_REQUIRED,
                query_state=QueryState.INSUFFICIENT_EVIDENCE,
                reasons=["No candidate documentation was retrieved for this query."],
                supporting_evidence_count=0,
                conflicting_evidence_count=0,
                verification_required=True,
                verification_reason="No relevant authoritative documentation was retrieved from the ingested corpus.",
                unresolved_aspects=["The requested subject could not be located in the current database."],
                required_information=req_info,
                trace={"candidate_count": 0, "entities": entities.__dict__},
            )

        # -------------------------------------------------------------------
        # 2. QUERY AMBIGUITY CHECK (Section 10)
        # -------------------------------------------------------------------
        words = [w for w in re.split(r"\W+", norm_q) if len(w) > 1]
        is_ambiguous = is_ctx_ambiguous or (
            len(words) <= 1 and not (
                entities.standard_number
                or entities.clause_id
                or re.search(r"\d+", norm_q)
                or "s.o." in query.lower()
                or "so " in query.lower()
                or "f. no" in query.lower()
            )
        )

        # -------------------------------------------------------------------
        # 3. EVIDENCE DEDUPLICATION (Section 11)
        # -------------------------------------------------------------------
        # Duplicate chunks with identical content or hash must NOT falsely
        # inflate the independent evidence count.
        seen_hashes: Set[str] = set()
        unique_evidence: List[EvidenceItem] = []
        duplicate_count = 0

        for item in evidence_items:
            h = item.content_hash
            if h in seen_hashes:
                duplicate_count += 1
            else:
                seen_hashes.add(h)
                unique_evidence.append(item)

        top_item = unique_evidence[0] if unique_evidence else evidence_items[0]

        # -------------------------------------------------------------------
        # 4. KNOWLEDGE MODEL RESOLUTION (Section 12)
        # -------------------------------------------------------------------
        knowledge_resolved = False
        if repo is not None and top_item.standard_id:
            try:
                link = resolve_chunk(top_item.metadata or top_item.to_dict(), repo=repo)
                knowledge_resolved = link.is_resolved
                top_item.knowledge_resolved = knowledge_resolved
            except Exception as e:
                logger.debug("Knowledge resolution check failed: %s", e)

        # -------------------------------------------------------------------
        # 5. QUERY ENTITY & SCOPE MATCHING (Section 12, 15)
        # -------------------------------------------------------------------
        has_std_query = bool(entities.standard_number)
        has_cls_query = bool(entities.clause_id)
        has_amd_query = bool(entities.amendment_number)
        has_ver_query = bool(entities.standard_year)

        req_std = entities.standard_number.strip().upper() if has_std_query else ""
        req_cls = entities.clause_id.strip() if has_cls_query else ""
        req_amd = entities.amendment_number.strip() if has_amd_query else ""

        # Check top evidence matching
        cand_std = (top_item.standard_number or "").strip().upper()
        cand_cls = (top_item.clause_id or "").strip()
        cand_amd = (top_item.amendment_number or "").strip()

        def _match_std(cand: str, req: str) -> bool:
            if not cand or not req:
                return False
            c = cand.replace("-", " ").replace(":", " ").strip().upper()
            r = req.replace("-", " ").replace(":", " ").strip().upper()
            return c == r or c.startswith(r) or r.startswith(c)

        exact_std_match = bool(has_std_query and _match_std(cand_std, req_std))
        standard_conflict = bool(has_std_query and cand_std and not _match_std(cand_std, req_std))
        exact_cls_match = bool(has_cls_query and cand_cls == req_cls)
        exact_amd_match = bool(has_amd_query and cand_amd == req_amd)
        exact_ver_match = bool(has_ver_query and top_item.standard_year == entities.standard_year)

        # Did ANY chunk in the retrieved pool match the requested standard?
        pool_matches_std = False
        if has_std_query:
            for item in unique_evidence:
                item_std = (item.standard_number or "").strip().upper()
                if _match_std(item_std, req_std):
                    pool_matches_std = True
                    break

        # Did ANY chunk in the retrieved pool match the requested clause?
        pool_matches_cls = False
        if has_cls_query:
            for item in unique_evidence:
                if (item.clause_id or "").strip() == req_cls:
                    pool_matches_cls = True
                    break
            if not pool_matches_cls:
                # Text check: if clause is unannotated in metadata, check chunk content
                pat = rf"\b(?:section|clause|उपधारा|धारा)?\s*{re.escape(req_cls)}\b"
                if any(re.search(pat, item.content, re.I) for item in unique_evidence[:3]):
                    pool_matches_cls = True
                    exact_cls_match = True

        # Did ANY chunk in the retrieved pool match the requested amendment?
        pool_matches_amd = False
        if has_amd_query:
            for item in unique_evidence:
                if (item.amendment_number or "").strip() == req_amd:
                    pool_matches_amd = True
                    break

        # -------------------------------------------------------------------
        # 6. CONFLICT SIGNAL DETECTION (Section 16)
        # -------------------------------------------------------------------
        # Detect materially contradictory or conflicting provisions
        conflict_detected = False
        conflicting_count = 0
        conflict_reasons = []

        # Check for multiple chunks with contradictory negation vs assertion
        # or differing numerical requirements for the same clause
        if len(unique_evidence) >= 2:
            texts = [c.content for c in unique_evidence[:5]]
            has_shall = any(re.search(r"\bshall\s+(?:not\b|never\b|prohibit)", t, re.I) or "prohibit" in t.lower() for t in texts)
            has_must = any(re.search(r"\bshall\s+be\s+(?:required|mandatory|complied)", t, re.I) or "mandatory" in t.lower() for t in texts)
            if has_shall and has_must:
                conflict_detected = True
                conflicting_count = 2
                conflict_reasons.append("Retrieved passages contain contradictory mandatory vs prohibitory provisions.")

        # -------------------------------------------------------------------
        # 7. MEASURABLE FEATURES EXTRACTION (Section 5)
        # -------------------------------------------------------------------
        top_reranker_score = top_item.reranker_score
        dense_bm25_agreement = bool(
            ("dense" in top_item.retrieval_methods and "bm25" in top_item.retrieval_methods)
            or (top_item.dense_rank is not None and top_item.bm25_rank is not None)
        )

        unique_docs = len({c.document_id for c in unique_evidence if c.document_id})
        prov_completeness = top_item.provenance_completeness

        # Source authority factor (Section 14)
        doc_type = (top_item.document_type or "").lower()
        auth = (top_item.authority or "").upper()
        if doc_type in ("indian_standard", "amendment") or auth == "BIS":
            source_authority_factor = 1.0
            source_authority_label = "authoritative_bis"
        elif "gazette" in doc_type or "order" in doc_type:
            source_authority_factor = 0.95
            source_authority_label = "government_gazette"
        elif "guideline" in doc_type or "manual" in doc_type:
            source_authority_factor = 0.85
            source_authority_label = "guideline_or_manual"
        else:
            source_authority_factor = 0.75
            source_authority_label = "unknown_authority"

        features = ConfidenceFeatures(
            has_standard_query=has_std_query,
            has_clause_query=has_cls_query,
            has_amendment_query=has_amd_query,
            has_version_query=has_ver_query,
            query_type="clause_specific" if has_cls_query else ("standard_specific" if has_std_query else "general_semantic"),
            is_ambiguous_query=is_ambiguous,
            candidate_count=len(evidence_items),
            supporting_evidence_count=len(unique_evidence),
            unique_documents_count=unique_docs,
            duplicate_chunks_detected=duplicate_count,
            top_reranker_score=top_reranker_score,
            top_fusion_score=top_item.fusion_score,
            dense_bm25_agreement=dense_bm25_agreement,
            exact_standard_match=exact_std_match,
            standard_conflict=standard_conflict,
            exact_clause_match=exact_cls_match,
            exact_amendment_match=exact_amd_match,
            exact_version_match=exact_ver_match,
            provenance_completeness=prov_completeness,
            knowledge_resolved=knowledge_resolved,
            source_authority=source_authority_label,
            conflict_detected=conflict_detected,
            conflicting_evidence_count=conflicting_count,
            intent=ctx_intent_str,
            intent_confidence=ctx_intent_conf,
            missing_required_context=is_missing_context,
        )

        # -------------------------------------------------------------------
        # 8. BOUNDED SCORE CALCULATION (Section 6)
        # -------------------------------------------------------------------
        base_relevance = _sigmoid(top_reranker_score)

        # Broad/summary query detection (Issue A: queries discovering standard requirements / overview)
        is_broad_query = (
            not has_cls_query and (
                has_std_query
                or ctx_intent_str in (
                    "REQUIREMENT_DISCOVERY",
                    "STANDARD_DISCOVERY",
                    "EXPLANATION_QUERY",
                    "GENERAL_INFORMATION",
                    "APPLICABILITY_QUERY",
                )
            )
        )

        if is_broad_query and unique_evidence:
            valid_scores = [c.reranker_score for c in unique_evidence[:5] if c.reranker_score is not None]
            if valid_scores:
                # Aggregate cross-encoder relevance across top evidence chunks
                agg_score = 0.6 * max(valid_scores) + 0.4 * (sum(valid_scores) / len(valid_scores))
                base_relevance = _sigmoid(agg_score)

        # Component 1: Retrieval strength (0.0 to 0.35)
        c_retrieval = (base_relevance * 0.30) + (0.05 if dense_bm25_agreement else 0.0)

        # Component 2: Scope & Identifier alignment (0.0 to 0.35)
        if has_std_query or has_cls_query or has_amd_query or has_ver_query:
            c_scope = 0.0
            if exact_std_match or (is_broad_query and pool_matches_std):
                c_scope += 0.15
            elif not has_std_query:
                c_scope += 0.10
            elif standard_conflict:
                c_scope -= 0.30  # Hard penalty for standard conflict

            if exact_cls_match:
                c_scope += 0.15
            elif not has_cls_query:
                # For broad/summary queries targeting a standard without a specific clause,
                # grant full scope alignment (0.15) if standard matches either top chunk or pool
                c_scope += 0.15 if (is_broad_query and (exact_std_match or pool_matches_std)) else 0.10

            if exact_amd_match:
                c_scope += 0.05
            elif not has_amd_query:
                c_scope += 0.05
            else:
                c_scope -= 0.10

            if exact_ver_match:
                c_scope += 0.05
            elif not has_ver_query:
                c_scope += 0.05
            else:
                c_scope -= 0.10

            c_scope = max(0.0, min(0.35, c_scope))
        else:
            # Semantic query: scope alignment tracks pure semantic relevance
            c_scope = base_relevance * 0.35
        # Component 3: Provenance completeness & Knowledge join (0.0 to 0.15)
        c_prov = (prov_completeness * 0.10) + (0.05 if knowledge_resolved else 0.0)

        # Component 4: Evidence volume & independence (0.0 to 0.15)
        n_unique = len(unique_evidence)
        if n_unique >= 3:
            c_volume = 0.15
        elif n_unique == 2:
            c_volume = 0.10
        elif n_unique == 1:
            c_volume = 0.05
        else:
            c_volume = 0.0

        raw_score = (c_retrieval + c_scope + c_prov + c_volume) * source_authority_factor
        score = max(0.0, min(1.0, round(raw_score, 4)))

        # -------------------------------------------------------------------
        # 9. HARD OVERRIDES & ABSTENTION TRIGGERS (Sections 8, 9, 10, 14, 15, 16)
        # -------------------------------------------------------------------
        reasons: List[str] = []
        unresolved: List[str] = []
        required_info: List[str] = []
        hard_verification = False
        query_state = QueryState.ANSWERABLE

        # Hard Trigger I: Missing required business context (e.g. applicability query with no product)
        if is_missing_context:
            hard_verification = True
            query_state = QueryState.VERIFICATION_REQUIRED
            score = min(score, 0.30)
            reasons.append("Missing required business context: applicability evaluation requires explicit product specification.")
            unresolved.append("Applicability determination cannot proceed without knowing the product name, category, or specifications.")
            required_info.append("Product name, product category, or explicit business activity.")

        # Hard Trigger A: Conflict detected
        elif conflict_detected:
            hard_verification = True
            query_state = QueryState.CONFLICTING_EVIDENCE
            score = min(score, 0.28)
            reasons.extend(conflict_reasons)
            unresolved.append("Conflicting requirements detected between retrieved chunks.")
            required_info.append("Official clarification or latest consolidated version of the standard.")

        # Hard Trigger B: Explicit standard queried, but absent from corpus
        elif has_std_query and not pool_matches_std:
            hard_verification = True
            query_state = QueryState.INSUFFICIENT_EVIDENCE
            score = min(score, 0.20)
            reasons.append(f"Standard '{req_std}' was not found in the ingested documentation.")
            unresolved.append(f"Provisions for {req_std} could not be established.")
            required_info.append(f"Official publication of {req_std}")

        # Hard Trigger C: Explicit clause queried, but clause text absent
        elif has_cls_query and not exact_cls_match and not pool_matches_cls:
            hard_verification = True
            query_state = QueryState.INSUFFICIENT_EVIDENCE
            score = min(score, 0.40)
            reasons.append(f"Clause '{req_cls}' text was not retrieved; only general context was found.")
            unresolved.append(f"Specific requirements of Clause {req_cls} could not be established.")
            required_info.append(f"Text of Clause {req_cls}")

        # Hard Trigger D: Ambiguous query
        elif is_ambiguous:
            hard_verification = True
            query_state = QueryState.AMBIGUOUS_QUERY
            score = min(score, 0.35)
            reasons.append("Query is underspecified or ambiguous.")
            unresolved.append("User intent cannot be uniquely mapped to a standard or clause.")
            required_info.append("A more specific query specifying an Indian Standard number, clause, or subject.")

        # Hard Trigger E: Very weak reranker relevance
        check_score = top_reranker_score
        if is_broad_query and unique_evidence:
            valid_scores = [c.reranker_score for c in unique_evidence[:5] if c.reranker_score is not None]
            if valid_scores:
                check_score = max(valid_scores)
        if check_score is not None and check_score < -4.5:
            hard_verification = True
            query_state = QueryState.INSUFFICIENT_EVIDENCE
            score = min(score, 0.30)
            reasons.append("Retrieved passages have low semantic relevance to the query.")
            unresolved.append("No directly applicable requirements were identified in retrieved text.")
            required_info.append("Authoritative documentation directly addressing the search terms.")

        # Trigger F: Specific amendment queried, but not matched in top evidence
        if has_amd_query and not exact_amd_match and not pool_matches_amd:
            score = min(score, 0.60)
            reasons.append(f"Amendment '{req_amd}' was requested, but matching amendment evidence was not found.")

        # Trigger G: Specific year/version queried, but exact version did not match
        if has_ver_query and not exact_ver_match:
            score = min(score, 0.65)
            reasons.append(f"Standard year '{entities.standard_year}' was requested, but retrieved evidence is dated {top_item.standard_year or 'unknown'}.")

        # Trigger H: Phase 9 Temporal uncertainty check (Section 18)
        is_currentness_query = (
            getattr(entities, "relative_temporal", None) == "current"
            or getattr(entities, "temporal_intent", None) == "current"
            or bool(re.search(r"\b(?:current|latest|present|active|in\s+force)\b", norm_q, re.IGNORECASE))
        )
        if is_currentness_query and temporal_resolution is not None:
            t_status = getattr(temporal_resolution, "status", None)
            t_status_val = getattr(t_status, "value", str(t_status)) if t_status else ""
            t_req_verif = getattr(temporal_resolution, "requires_verification", False)
            t_reason = getattr(temporal_resolution, "reason", "Current legal status is temporally uncertain.")
            if t_req_verif or t_status_val == "temporally_uncertain":
                hard_verification = True
                query_state = QueryState.INSUFFICIENT_EVIDENCE
                score = min(score, 0.40)
                reasons.append(f"Temporal uncertainty: {t_reason}")
                unresolved.append("Legal currentness cannot be established from corpus evidence.")
                required_info.append("Official gazette or BIS supersession documentation confirming active legal edition.")

        # -------------------------------------------------------------------
        # 10. DECISION & LEVEL ASSIGNMENT (Section 7)
        # -------------------------------------------------------------------
        if hard_verification or score < THRESHOLD_MEDIUM:
            level = ConfidenceLevel.LOW
            decision = Decision.VERIFICATION_REQUIRED
            if query_state == QueryState.ANSWERABLE:
                query_state = QueryState.INSUFFICIENT_EVIDENCE
            verif_required = True
            verif_reason = reasons[0] if reasons else "Evidence is insufficient to answer the query safely."
        elif score >= THRESHOLD_HIGH:
            level = ConfidenceLevel.HIGH
            decision = Decision.ANSWER
            query_state = QueryState.ANSWERABLE
            verif_required = False
            verif_reason = None
            if exact_cls_match:
                reasons.append(f"Exact match for Clause {cand_cls} in {cand_std or 'standard'}.")
            elif exact_std_match:
                reasons.append(f"Exact match for standard {cand_std}.")
            else:
                reasons.append("Strong semantic agreement across retrieved passages.")
        else:
            level = ConfidenceLevel.MEDIUM
            decision = Decision.QUALIFIED_ANSWER
            query_state = QueryState.ANSWERABLE
            verif_required = False
            verif_reason = None
            reasons.append("Moderate evidence confidence; answer should be qualified with known context boundaries.")

        # -------------------------------------------------------------------
        # STRICT DETERMINISTIC POLICY CONSISTENCY (Phase 6 / PRD)
        # -------------------------------------------------------------------
        # 1. If query_state indicates insufficiency or conflict, verification MUST be required
        if query_state in (
            QueryState.INSUFFICIENT_EVIDENCE,
            QueryState.CONFLICTING_EVIDENCE,
            QueryState.AMBIGUOUS_QUERY,
            QueryState.VERIFICATION_REQUIRED,
        ):
            verif_required = True
            decision = Decision.VERIFICATION_REQUIRED
            if level == ConfidenceLevel.HIGH:
                level = ConfidenceLevel.LOW
            if not verif_reason:
                verif_reason = reasons[0] if reasons else "Evidence is insufficient to answer the query safely."

        # 2. If verification is required, decision CANNOT be ANSWER
        if verif_required:
            decision = Decision.VERIFICATION_REQUIRED
            if query_state == QueryState.ANSWERABLE:
                query_state = QueryState.INSUFFICIENT_EVIDENCE
            if not verif_reason:
                verif_reason = reasons[0] if reasons else "Regulatory verification is required."

        # 3. If decision is VERIFICATION_REQUIRED, verif_required MUST be True
        if decision == Decision.VERIFICATION_REQUIRED:
            verif_required = True
            if query_state == QueryState.ANSWERABLE:
                query_state = QueryState.INSUFFICIENT_EVIDENCE

        # 4. If level is LOW, verification MUST be required and decision CANNOT be ANSWER
        if level == ConfidenceLevel.LOW:
            verif_required = True
            decision = Decision.VERIFICATION_REQUIRED
            if query_state == QueryState.ANSWERABLE:
                query_state = QueryState.INSUFFICIENT_EVIDENCE

        trace_dict = {
            "query": query,
            "normalized_query": norm_q,
            "entities": entities.__dict__,
            "features": features.model_dump(),
            "score_components": {
                "c_retrieval": round(c_retrieval, 4),
                "c_scope": round(c_scope, 4),
                "c_prov": round(c_prov, 4),
                "c_volume": round(c_volume, 4),
                "source_authority_factor": source_authority_factor,
                "raw_score": round(raw_score, 4),
            },
            "score": score,
            "level": level.value,
            "decision": decision.value,
            "query_state": query_state.value,
        }

        return ConfidenceResult(
            score=score,
            level=level,
            decision=decision,
            query_state=query_state,
            reasons=reasons,
            supporting_evidence_count=len(unique_evidence),
            conflicting_evidence_count=conflicting_count,
            verification_required=verif_required,
            verification_reason=verif_reason,
            unresolved_aspects=unresolved,
            required_information=required_info,
            trace=trace_dict,
        )
