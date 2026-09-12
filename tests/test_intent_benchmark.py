"""
tests/test_intent_benchmark.py

Section 25: Intent Classification Benchmark Harness
Evaluates the intent classification engine on data/evaluation/intent_dataset.json.
Reports and enforces accuracy, macro-F1, ambiguity detection rate, and false-confident rate.
"""

import json
from pathlib import Path
from collections import defaultdict
import pytest

from app.query_intelligence.models import QueryIntentType, QueryLifecycleState
from app.query_intelligence.classifier import classify_intent_deterministic
from app.query_intelligence import build_query_context
from app.rag.query import extract_query_entities, normalize_query

BENCHMARK_PATH = Path("data/evaluation/intent_dataset.json")


def test_intent_dataset_file_exists():
    assert BENCHMARK_PATH.exists(), f"Benchmark file {BENCHMARK_PATH} missing"
    data = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) >= 35, f"Expected at least 35 benchmark queries, got {len(data)}"


def test_intent_benchmark_metrics():
    """
    Run the intent benchmark over all 35 annotated queries and compute metrics:
      - Accuracy >= 85%
      - Ambiguity Detection Rate >= 90%
      - False Confident Rate <= 5% (0% on ambiguous queries)
    """
    data = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))

    correct = 0
    total = len(data)

    # For Macro-F1: track TP, FP, FN per intent class
    tp = defaultdict(int)
    fp = defaultdict(int)
    fn = defaultdict(int)

    ambiguity_actual = 0
    ambiguity_detected = 0
    false_confidents = 0

    results = []

    for item in data:
        qid = item["id"]
        q = item["query"]
        expected_intent_str = item["expected_intent"]
        expected_ambiguous = item.get("is_ambiguous", False)

        q_context = build_query_context(q)
        pred_intent_str = q_context.intent.intent.value
        pred_ambiguous = q_context.intent.is_ambiguous

        is_match = (pred_intent_str == expected_intent_str)
        if is_match:
            correct += 1
            tp[expected_intent_str] += 1
        else:
            fp[pred_intent_str] += 1
            fn[expected_intent_str] += 1

        if expected_ambiguous:
            ambiguity_actual += 1
            if pred_ambiguous or pred_intent_str == "AMBIGUOUS_QUERY":
                ambiguity_detected += 1
            # Check false confidence: if ambiguous query got high confidence (> 0.6)
            if q_context.intent.intent_confidence > 0.60:
                false_confidents += 1

        results.append({
            "id": qid,
            "query": q,
            "expected": expected_intent_str,
            "predicted": pred_intent_str,
            "confidence": q_context.intent.intent_confidence,
            "match": is_match,
        })

    accuracy = correct / total
    ambiguity_rate = ambiguity_detected / ambiguity_actual if ambiguity_actual > 0 else 1.0
    false_confident_rate = false_confidents / ambiguity_actual if ambiguity_actual > 0 else 0.0

    # Calculate Macro-F1 across all expected classes
    classes = set(item["expected_intent"] for item in data)
    f1_scores = []
    for cls_name in classes:
        c_tp = tp[cls_name]
        c_fp = fp[cls_name]
        c_fn = fn[cls_name]
        prec = c_tp / (c_tp + c_fp) if (c_tp + c_fp) > 0 else 0.0
        rec = c_tp / (c_tp + c_fn) if (c_tp + c_fn) > 0 else 0.0
        f1 = (2 * prec * rec) / (prec + rec) if (prec + rec) > 0 else 0.0
        f1_scores.append(f1)

    macro_f1 = sum(f1_scores) / len(f1_scores) if f1_scores else 0.0

    print("\n--- PHASE 10 INTENT BENCHMARK REPORT ---")
    print(f"Total Test Queries: {total}")
    print(f"Accuracy: {accuracy:.4f} ({correct}/{total})")
    print(f"Macro-F1: {macro_f1:.4f}")
    print(f"Ambiguity Detection Rate: {ambiguity_rate:.4f} ({ambiguity_detected}/{ambiguity_actual})")
    print(f"False Confident Rate: {false_confident_rate:.4f} ({false_confidents}/{ambiguity_actual})")

    # Enforce performance boundaries
    assert accuracy >= 0.85, f"Accuracy {accuracy:.4f} below threshold 0.85"
    assert macro_f1 >= 0.80, f"Macro-F1 {macro_f1:.4f} below threshold 0.80"
    assert ambiguity_rate >= 0.90, f"Ambiguity detection rate {ambiguity_rate:.4f} below threshold 0.90"
    assert false_confident_rate == 0.0, f"False confident rate {false_confident_rate} must be 0.0"
