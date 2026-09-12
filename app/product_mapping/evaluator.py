"""
app/product_mapping/evaluator.py

Evaluation harness and metrics calculation for Product-to-Standard Mapping (Phase 11).

METRICS (Section 33):
  - Precision@1
  - Precision@3
  - Recall@3
  - MRR@5
  - False strong-candidate rate: fraction of STRONG_CANDIDATE mappings where ground truth != 'strong'
  - Unsupported mapping rate: fraction of candidate reasons lacking traceable evidence
  - No-candidate false-negative rate: queries returning 0 candidates when a candidate existed
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from app.product_mapping.extractor import extract_product_context
from app.product_mapping.engine import discover_standard_candidates
from app.product_mapping.models import MappingStatus, ProductStandardCandidate

logger = logging.getLogger(__name__)


def evaluate_product_mapping(
    dataset_path: str,
    retriever: Any = None,
    knowledge_repo: Any = None,
    temporal_resolver: Any = None,
) -> Dict[str, Any]:
    """
    Evaluates product mapping against an annotated dataset.
    Returns calculated ranking and safety metrics.
    """
    p = Path(dataset_path)
    if not p.exists():
        raise FileNotFoundError(f"Dataset not found at {dataset_path}")

    records = json.loads(p.read_text(encoding="utf-8"))

    total_queries = len(records)
    if total_queries == 0:
        return {"total_queries": 0}

    p_at_1_hits = 0
    p_at_3_hits = 0
    recall_at_3_numerators = 0
    total_relevant_at_3 = 0
    rr_sum = 0.0

    total_strong_candidates_surfaced = 0
    false_strong_candidates = 0

    total_reasons_checked = 0
    unsupported_reasons = 0

    no_candidate_count = 0

    detailed_results = []

    for record in records:
        q_text = record["product_description"]
        expected_stds = set(record.get("expected_candidate_standard_ids", []))
        relevance_labels = record.get("relevance_labels", {})

        pctx = extract_product_context(query=q_text)
        candidates = discover_standard_candidates(
            product_context=pctx,
            retriever=retriever,
            knowledge_repo=knowledge_repo,
            temporal_resolver=temporal_resolver,
            top_k=5,
        )

        cand_stds = [c.standard_number for c in candidates]

        if not cand_stds:
            no_candidate_count += 1

        # Precision @ 1
        if cand_stds:
            top_1 = cand_stds[0]
            if top_1 in expected_stds or relevance_labels.get(top_1) in ("strong", "possible"):
                p_at_1_hits += 1

        # Precision @ 3 & Recall @ 3
        top_3 = cand_stds[:3]
        rel_in_top_3 = sum(1 for s in top_3 if s in expected_stds or relevance_labels.get(s) in ("strong", "possible"))
        p_at_3_hits += rel_in_top_3
        recall_at_3_numerators += rel_in_top_3
        total_relevant_at_3 += max(1, len(expected_stds))

        # MRR @ 5
        mrr_hit = 0.0
        for rank_idx, s in enumerate(cand_stds[:5], start=1):
            if s in expected_stds or relevance_labels.get(s) in ("strong", "possible"):
                mrr_hit = 1.0 / rank_idx
                break
        rr_sum += mrr_hit

        # Safety: False strong-candidate rate
        for c in candidates:
            if c.mapping_status == MappingStatus.STRONG_CANDIDATE:
                total_strong_candidates_surfaced += 1
                truth = relevance_labels.get(c.standard_number)
                if truth is not None and truth != "strong":
                    false_strong_candidates += 1

            # Check explanation traceability
            for r in c.mapping_reasons:
                total_reasons_checked += 1
                # If reason is DIRECT_TITLE_MATCH but no title matched term or no title exists
                if r.reason_type.value == "DIRECT_TITLE_MATCH" and not r.matched_term:
                    unsupported_reasons += 1
                elif r.reason_type.value == "CLAUSE_SUPPORT" and not c.supporting_clause_ids:
                    unsupported_reasons += 1

        detailed_results.append({
            "product_id": record.get("product_id"),
            "product": pctx.primary_identifier,
            "candidates": cand_stds,
            "top_mapping_status": candidates[0].mapping_status.value if candidates else None,
            "top_mapping_score": candidates[0].mapping_score if candidates else None,
        })

    p_at_1 = round(p_at_1_hits / total_queries, 4)
    p_at_3 = round(p_at_3_hits / (total_queries * 3), 4)
    recall_at_3 = round(recall_at_3_numerators / total_relevant_at_3, 4) if total_relevant_at_3 > 0 else 0.0
    mrr_5 = round(rr_sum / total_queries, 4)

    false_strong_rate = (
        round(false_strong_candidates / total_strong_candidates_surfaced, 4)
        if total_strong_candidates_surfaced > 0 else 0.0
    )
    unsupported_rate = (
        round(unsupported_reasons / total_reasons_checked, 4)
        if total_reasons_checked > 0 else 0.0
    )
    no_candidate_rate = round(no_candidate_count / total_queries, 4)

    return {
        "total_queries": total_queries,
        "precision_at_1": p_at_1,
        "precision_at_3": p_at_3,
        "recall_at_3": recall_at_3,
        "mrr_at_5": mrr_5,
        "false_strong_candidate_rate": false_strong_rate,
        "unsupported_mapping_rate": unsupported_rate,
        "no_candidate_rate": no_candidate_rate,
        "total_strong_candidates_surfaced": total_strong_candidates_surfaced,
        "false_strong_candidates": false_strong_candidates,
        "detailed_results": detailed_results,
    }
