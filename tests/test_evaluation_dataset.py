"""
tests/test_evaluation_dataset.py

Phase 6 section 22/23: Real retrieval evaluation dataset tests.
Validates the evaluation dataset schema, category coverage, and evaluation harness.
"""

import json
from pathlib import Path
import pytest
from app.rag.retriever import evaluate_retrieval, recall_at_k, mrr_at_k

DATASET_PATH = Path("data/evaluation/dataset.json")

REQUIRED_CATEGORIES = {
    "exact standard",
    "standard + year",
    "clause",
    "amendment",
    "semantic requirement",
    "reference",
    "Hindi/English",
    "mixed language",
    "distractor numeric queries",
}


def test_dataset_file_exists_and_is_valid_json():
    assert DATASET_PATH.exists(), f"{DATASET_PATH} does not exist"
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) >= 20, f"Expected at least 20 records, got {len(data)}"


def test_dataset_covers_all_required_categories():
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    present_categories = {item.get("category") for item in data}
    missing = REQUIRED_CATEGORIES - present_categories
    assert not missing, f"Missing required evaluation categories: {missing}"


def test_dataset_record_contracts():
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    valid_decisions = {"answer", "qualified_answer", "verification_required"}
    for item in data:
        assert "id" in item and item["id"].strip()
        assert "query" in item and item["query"].strip()
        assert "category" in item and item["category"] in REQUIRED_CATEGORIES
        assert "expected_chunk_ids" in item and isinstance(item["expected_chunk_ids"], list)
        assert "expected_decision" in item and item["expected_decision"] in valid_decisions


def test_evaluation_metric_functions():
    # Perfect match at 1
    assert recall_at_k(["c1"], ["c1", "c2", "c3"], k=1) == 1.0
    assert mrr_at_k(["c1"], ["c1", "c2", "c3"], k=5) == 1.0

    # Match at rank 2
    assert recall_at_k(["c2"], ["c1", "c2", "c3"], k=1) == 0.0
    assert recall_at_k(["c2"], ["c1", "c2", "c3"], k=3) == 1.0
    assert mrr_at_k(["c2"], ["c1", "c2", "c3"], k=5) == 0.5

    # Distractor / empty expected
    assert recall_at_k([], ["c1", "c2"], k=1) == 0.0
    assert mrr_at_k([], ["c1", "c2"], k=5) == 0.0
