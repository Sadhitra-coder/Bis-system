"""
tests/test_retrieval.py

Phase 4 — Advanced Retrieval Engineering Tests.

Tests are organized as per the Phase 4 specification:

A. Query normalization
B. Entity extraction
C. Identifier retrieval (via mock retriever)
D. Metadata filtering (build_chroma_filters)
E. RRF fusion
F. Identifier boosting
G. Knowledge-model retrieval (via mock)
H. Provenance regression
I. Multilingual / Unicode regression
J. End-to-end retrieval (in-memory Chroma + synthetic BIS fixture)
K. Evaluation metrics (Recall@K, MRR)
L. Error handling / edge cases
M. Latency measurement
"""

import time
import pytest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

from app.rag.query import (
    normalize_query,
    extract_query_entities,
    build_chroma_filters,
    build_full_normalized_query,
    RetrievalResult,
    RetrievalTrace,
    QueryEntities,
)
from app.rag.retriever import (
    tokenize,
    normalize_scores,
    reciprocal_rank_fusion,
    apply_identifier_boost,
    build_retrieval_result,
    recall_at_k,
    mrr_at_k,
    evaluate_retrieval,
    HybridRetriever,
    flatten_provenance,
)


# ===========================================================
# FIXTURES
# ===========================================================

def make_result(
    chunk_id: str = "c1",
    content: str = "content",
    standard_number: Optional[str] = None,
    standard_year: Optional[int] = None,
    clause_id: Optional[str] = None,
    amendment_number: Optional[str] = None,
    page_start: Optional[int] = None,
    page_end: Optional[int] = None,
    document_id: str = "doc_1",
    source_hash: str = "abc123",
    source_file: str = "IS_test.pdf",
    fusion_score: float = 0.1,
    dense_rank: Optional[int] = None,
    bm25_rank: Optional[int] = None,
    retrieval_methods: Optional[List[str]] = None,
) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        content=content,
        standard_number=standard_number,
        standard_year=standard_year,
        clause_id=clause_id,
        amendment_number=amendment_number,
        page_start=page_start,
        page_end=page_end,
        document_id=document_id,
        source_hash=source_hash,
        source_file=source_file,
        fusion_score=fusion_score,
        dense_rank=dense_rank,
        bm25_rank=bm25_rank,
        retrieval_methods=list(retrieval_methods or []),
    )


def make_mock_retriever(chunks: List[Dict[str, Any]]) -> HybridRetriever:
    """
    Build a HybridRetriever backed by an in-memory Chroma mock.
    Uses real BM25 and real RRF on the supplied chunk list.
    """
    # Build mock collection
    collection = MagicMock()
    collection.count.return_value = len(chunks)

    # get() returns paged corpus
    def mock_get(limit=1000, offset=0, include=None):
        page = chunks[offset:offset + limit]
        return {
            "ids": [c["chunk_id"] for c in page],
            "documents": [c.get("content", "") for c in page],
            "metadatas": [c.get("metadata", {}) for c in page],
        }
    collection.get.side_effect = mock_get

    # query() returns dense results — return top-N by content length (deterministic mock)
    def mock_query(query_embeddings=None, n_results=10, include=None, where=None, **kwargs):
        ranked = sorted(chunks, key=lambda c: len(c.get("content", "")), reverse=True)
        top = ranked[:n_results]
        return {
            "ids": [[c["chunk_id"] for c in top]],
            "documents": [[c.get("content", "") for c in top]],
            "metadatas": [[c.get("metadata", {}) for c in top]],
            "distances": [[0.1 * (i + 1) for i in range(len(top))]],
        }
    collection.query.side_effect = mock_query

    # Patch SentenceTransformer to avoid loading model
    import numpy as np
    embedder = MagicMock()
    embedder.encode.return_value = np.zeros(768, dtype=float)

    retriever = HybridRetriever(
        collection=collection,
        embedder=embedder,
        rrf_k=60,
        candidate_limit=50,
    )
    return retriever


# ===========================================================
# A. QUERY NORMALIZATION
# ===========================================================

class TestQueryNormalization:

    def test_basic_whitespace_normalization(self):
        assert normalize_query("  IS 3055  ") == "IS 3055"

    def test_multi_space_collapse(self):
        result = normalize_query("IS   3055   clinical   thermometers")
        assert "  " not in result

    def test_is_number_no_space(self):
        # "IS3055" → "IS 3055"
        result = normalize_query("IS3055 requirements")
        assert "IS 3055" in result

    def test_is_colon_year_spacing(self):
        result = normalize_query("IS 3055:2024")
        assert "IS 3055 : 2024" in result

    def test_is_colon_year_already_spaced(self):
        result = normalize_query("IS 3055 : 2024")
        assert "IS 3055 : 2024" in result

    def test_clause_no_space(self):
        result = normalize_query("clause4.1 requirements")
        assert "Clause 4.1" in result

    def test_amd_abbreviation(self):
        result = normalize_query("AMD1 to IS 3055")
        assert "Amendment 1" in result

    def test_original_query_preserved(self):
        original = "IS3055:2024 clause4.1"
        info = build_full_normalized_query(original)
        assert info["original_query"] == original
        assert info["normalized_query"] != original  # was normalized

    def test_unicode_preservation_hindi(self):
        # Hindi text must not be corrupted
        hindi = "IS 3055 गुणवत्ता मानक"
        result = normalize_query(hindi)
        assert "गुणवत्ता" in result
        assert "मानक" in result

    def test_unicode_preservation_mixed(self):
        mixed = "IS 3055 quality मानक 2024"
        result = normalize_query(mixed)
        assert "मानक" in result
        assert "IS 3055" in result

    def test_devanagari_not_destroyed(self):
        q = "क्लॉज 4.1 IS 3055"
        result = normalize_query(q)
        assert "क्लॉज" in result

    def test_empty_query(self):
        assert normalize_query("") == ""
        assert normalize_query("   ") == ""


# ===========================================================
# B. ENTITY EXTRACTION
# ===========================================================

class TestEntityExtraction:

    def test_standard_number_basic(self):
        e = extract_query_entities("IS 3055 thermometers")
        assert e.standard_number == "IS 3055"

    def test_standard_number_with_year(self):
        e = extract_query_entities("IS 3055 : 2024")
        assert e.standard_number == "IS 3055"
        assert e.standard_year == 2024

    def test_standard_number_with_part(self):
        e = extract_query_entities("IS 3055-1 requirements")
        assert e.standard_number == "IS 3055-1"
        assert e.part_number == "1"

    def test_clause_keyword(self):
        e = extract_query_entities("Clause 4.1 of IS 3055")
        assert e.clause_id == "4.1"
        assert e.standard_number == "IS 3055"

    def test_clause_section_keyword(self):
        e = extract_query_entities("Section 5 requirements")
        assert e.clause_id == "5"

    def test_amendment_number(self):
        e = extract_query_entities("Amendment 1 to IS 3055")
        assert e.amendment_number == "1"
        assert e.standard_number == "IS 3055"

    def test_edition(self):
        e = extract_query_entities("Third Edition of IS 3055")
        assert e.edition_or_version is not None
        assert "Third" in e.edition_or_version

    def test_no_false_positive_plain_number(self):
        # "3055" without IS prefix must not become a standard number
        e = extract_query_entities("temperature range 3055 to 4000")
        assert e.standard_number is None

    def test_no_false_positive_small_number(self):
        e = extract_query_entities("use 42 samples for testing")
        assert e.standard_number is None
        assert e.clause_id is None

    def test_year_out_of_range_ignored(self):
        e = extract_query_entities("IS 3055 : 1800")
        assert e.standard_year is None  # 1800 < 1947

    def test_entities_has_any_false(self):
        e = extract_query_entities("what are calibration requirements")
        assert not e.has_any()

    def test_entities_has_any_true(self):
        e = extract_query_entities("IS 3055 clause 4.1")
        assert e.has_any()

    def test_to_dict(self):
        e = extract_query_entities("IS 3055 : 2024 clause 4.1 Amendment 1")
        d = e.to_dict()
        assert d["standard_number"] == "IS 3055"
        assert d["standard_year"] == 2024
        assert d["clause_id"] == "4.1"
        assert d["amendment_number"] == "1"


# ===========================================================
# C. IDENTIFIER RETRIEVAL (via mock retriever)
# ===========================================================

class TestIdentifierRetrieval:

    def setup_method(self):
        self.chunks = [
            {
                "chunk_id": "c_is3055",
                "content": "IS 3055 clinical thermometers specification",
                "metadata": {"standard_number": "IS 3055", "standard_year": 2024, "clause_id": "4", "document_id": "doc1", "source_hash": "aaa"},
            },
            {
                "chunk_id": "c_clause41",
                "content": "Clause 4.1 calibration and accuracy requirements for IS 3055",
                "metadata": {"standard_number": "IS 3055", "clause_id": "4.1", "document_id": "doc1", "source_hash": "aaa"},
            },
            {
                "chunk_id": "c_unrelated",
                "content": "Bureau of Indian Standards certification process overview",
                "metadata": {"document_id": "doc2", "source_hash": "bbb"},
            },
        ]
        self.retriever = make_mock_retriever(self.chunks)

    def test_exact_standard_number_gets_boost(self):
        results = self.retriever.retrieve("IS 3055", top_k=3)
        ids = [r.chunk_id for r in results]
        # IS 3055 chunks should rank before unrelated
        is3055_pos = next((i for i, r in enumerate(results) if r.standard_number == "IS 3055"), None)
        unrelated_pos = next((i for i, r in enumerate(results) if r.chunk_id == "c_unrelated"), None)
        if is3055_pos is not None and unrelated_pos is not None:
            assert is3055_pos < unrelated_pos

    def test_standard_year_combination_query(self):
        results = self.retriever.retrieve("IS 3055 2024", top_k=3)
        # c_is3055 has year 2024 — should get extra boost
        top = results[0]
        assert top.identifier_match or top.standard_number == "IS 3055"

    def test_clause_query(self):
        results = self.retriever.retrieve("Clause 4.1 of IS 3055", top_k=3)
        ids = [r.chunk_id for r in results]
        assert "c_clause41" in ids

    def test_amendment_query(self):
        chunks = self.chunks + [{
            "chunk_id": "c_amd",
            "content": "Amendment 1 to IS 3055 effective from 2024",
            "metadata": {"standard_number": "IS 3055", "amendment_number": "1", "document_id": "doc1", "source_hash": "aaa"},
        }]
        retriever = make_mock_retriever(chunks)
        results = retriever.retrieve("Amendment 1 to IS 3055", top_k=4)
        ids = [r.chunk_id for r in results]
        assert "c_amd" in ids


# ===========================================================
# D. METADATA FILTERING
# ===========================================================

class TestMetadataFiltering:

    def test_no_hard_filters(self):
        result = build_chroma_filters()
        assert result is None

    def test_authority_filter(self):
        result = build_chroma_filters(authority="BIS")
        assert result == {"authority": {"$eq": "BIS"}}

    def test_document_type_filter(self):
        result = build_chroma_filters(document_type="indian_standard")
        assert result == {"document_type": {"$eq": "indian_standard"}}

    def test_both_filters_uses_and(self):
        result = build_chroma_filters(authority="BIS", document_type="amendment")
        assert "$and" in result
        conditions = result["$and"]
        keys = {list(c.keys())[0] for c in conditions}
        assert "authority" in keys
        assert "document_type" in keys


# ===========================================================
# E. RRF FUSION
# ===========================================================

class TestRRF:

    def test_dense_only_candidate(self):
        scores = reciprocal_rank_fusion([["c1", "c2", "c3"], []])
        assert "c1" in scores
        assert scores["c1"] > scores["c2"]

    def test_bm25_only_candidate(self):
        scores = reciprocal_rank_fusion([[], ["c1", "c2"]])
        assert "c1" in scores
        assert scores["c1"] > scores["c2"]

    def test_candidate_in_both_scores_higher(self):
        # "c1" appears in both lists
        scores = reciprocal_rank_fusion([["c1", "c2"], ["c1", "c3"]])
        # c1 should have highest score (appears twice)
        assert scores["c1"] > scores["c2"]
        assert scores["c1"] > scores["c3"]

    def test_deterministic_with_same_input(self):
        r1 = reciprocal_rank_fusion([["c1", "c2", "c3"], ["c2", "c1"]])
        r2 = reciprocal_rank_fusion([["c1", "c2", "c3"], ["c2", "c1"]])
        assert r1 == r2

    def test_rrf_k_parameter_effect(self):
        # Higher k → less steep discounting → lower score difference between ranks
        scores_k1 = reciprocal_rank_fusion([["c1", "c2"]], k=1)
        scores_k60 = reciprocal_rank_fusion([["c1", "c2"]], k=60)
        # With k=1: 1/2 vs 1/3 = 0.167 difference
        # With k=60: 1/61 vs 1/62 = 0.00027 difference
        diff_k1 = scores_k1["c1"] - scores_k1["c2"]
        diff_k60 = scores_k60["c1"] - scores_k60["c2"]
        assert diff_k1 > diff_k60

    def test_empty_lists(self):
        scores = reciprocal_rank_fusion([[], []])
        assert scores == {}

    def test_ordering_stable_retriever_level(self):
        chunks = [
            {"chunk_id": "c_a", "content": "aaa " * 50, "metadata": {}},
            {"chunk_id": "c_b", "content": "bbb " * 50, "metadata": {}},
        ]
        retriever = make_mock_retriever(chunks)
        r1 = retriever.retrieve("test", top_k=2)
        r2 = retriever.retrieve("test", top_k=2)
        assert [r.chunk_id for r in r1] == [r.chunk_id for r in r2]


# ===========================================================
# F. IDENTIFIER BOOSTING
# ===========================================================

class TestIdentifierBoosting:

    def test_exact_standard_boost(self):
        candidates = [
            make_result("c1", standard_number="IS 3055", fusion_score=0.1),
            make_result("c2", standard_number=None, fusion_score=0.1),
        ]
        entities = QueryEntities(standard_number="IS 3055")
        boosted = apply_identifier_boost(candidates, entities)
        c1 = next(c for c in boosted if c.chunk_id == "c1")
        c2 = next(c for c in boosted if c.chunk_id == "c2")
        assert c1.fusion_score > c2.fusion_score
        assert c1.identifier_match is True

    def test_standard_year_combo_boost(self):
        candidates = [
            make_result("c1", standard_number="IS 3055", standard_year=2024, fusion_score=0.1),
            make_result("c2", standard_number="IS 3055", standard_year=None, fusion_score=0.1),
        ]
        entities = QueryEntities(standard_number="IS 3055", standard_year=2024)
        boosted = apply_identifier_boost(candidates, entities)
        c1 = next(c for c in boosted if c.chunk_id == "c1")
        c2 = next(c for c in boosted if c.chunk_id == "c2")
        # c1 should get both standard + year boost, c2 only standard boost
        assert c1.fusion_score > c2.fusion_score

    def test_clause_boost(self):
        candidates = [
            make_result("c1", clause_id="4.1", fusion_score=0.1),
            make_result("c2", clause_id="5", fusion_score=0.1),
        ]
        entities = QueryEntities(clause_id="4.1")
        boosted = apply_identifier_boost(candidates, entities)
        c1 = next(c for c in boosted if c.chunk_id == "c1")
        c2 = next(c for c in boosted if c.chunk_id == "c2")
        assert c1.fusion_score > c2.fusion_score

    def test_boost_does_not_eliminate_strong_semantic(self):
        # A semantically strong candidate (high fusion score) should survive even
        # if it doesn't match the identifier
        candidates = [
            make_result("c_semantic", standard_number=None, fusion_score=0.9),
            make_result("c_identifier", standard_number="IS 3055", fusion_score=0.01),
        ]
        entities = QueryEntities(standard_number="IS 3055")
        boosted = apply_identifier_boost(candidates, entities)
        ids = [c.chunk_id for c in boosted]
        # c_semantic must still be in top results
        assert "c_semantic" in ids

    def test_no_entities_no_boost(self):
        candidates = [
            make_result("c1", standard_number="IS 3055", fusion_score=0.5),
        ]
        entities = QueryEntities()  # no entities
        result = apply_identifier_boost(candidates, entities)
        assert result[0].fusion_score == 0.5  # unchanged
        assert result[0].identifier_match is False

    def test_boost_order_deterministic(self):
        candidates = [
            make_result("c_z", standard_number="IS 3055", fusion_score=0.1),
            make_result("c_a", standard_number="IS 3055", fusion_score=0.1),
        ]
        entities = QueryEntities(standard_number="IS 3055")
        r1 = apply_identifier_boost(list(candidates), entities)
        r2 = apply_identifier_boost(list(candidates), entities)
        assert [c.chunk_id for c in r1] == [c.chunk_id for c in r2]


# ===========================================================
# G. KNOWLEDGE-MODEL RETRIEVAL (mock)
# ===========================================================

class TestKnowledgeModelRetrieval:

    def test_version_scoped_clause_retrieval(self):
        """Chunks with correct version metadata should be prioritized via boost."""
        chunks = [
            {
                "chunk_id": "c_v3_c4",
                "content": "IS 3055 Third Edition Clause 4 Requirements",
                "metadata": {"standard_number": "IS 3055", "clause_id": "4", "edition_or_version": "Third Edition"},
            },
            {
                "chunk_id": "c_v2_c4",
                "content": "IS 3055 Second Edition Clause 4 older requirements",
                "metadata": {"standard_number": "IS 3055", "clause_id": "4", "edition_or_version": "Second Edition"},
            },
        ]
        retriever = make_mock_retriever(chunks)
        results = retriever.retrieve("IS 3055 clause 4", top_k=2)
        ids = [r.chunk_id for r in results]
        # Both should be returned
        assert len(results) == 2
        # Both should have identifier_match from IS 3055 + clause 4 boost
        matches = [r for r in results if r.identifier_match]
        assert len(matches) >= 1

    def test_amendment_retrieval(self):
        chunks = [
            {
                "chunk_id": "c_amd1",
                "content": "Amendment 1 to IS 3055 changes the glass composition requirement",
                "metadata": {"standard_number": "IS 3055", "amendment_number": "1"},
            },
            {
                "chunk_id": "c_base",
                "content": "IS 3055 general scope and applicability",
                "metadata": {"standard_number": "IS 3055"},
            },
        ]
        retriever = make_mock_retriever(chunks)
        results = retriever.retrieve("amendment 1 to IS 3055", top_k=2)
        amd = next((r for r in results if r.chunk_id == "c_amd1"), None)
        assert amd is not None
        assert amd.identifier_match is True


# ===========================================================
# H. PROVENANCE REGRESSION
# ===========================================================

class TestProvenanceRegression:

    def test_retrieval_result_carries_provenance(self):
        chunk = {
            "chunk_id": "c_prov",
            "content": "Requirements text",
            "metadata": {
                "document_id": "doc_hash_123",
                "source_hash": "sha256abc",
                "source_file": "IS_3055.pdf",
                "page_start": 5,
                "page_end": 6,
                "standard_number": "IS 3055",
                "clause_id": "4.1",
            },
        }
        rr = build_retrieval_result(chunk, fusion_score=0.5)
        assert rr.chunk_id == "c_prov"
        assert rr.document_id == "doc_hash_123"
        assert rr.source_hash == "sha256abc"
        assert rr.source_file == "IS_3055.pdf"
        assert rr.page_start == 5
        assert rr.page_end == 6
        assert rr.standard_number == "IS 3055"
        assert rr.clause_id == "4.1"

    def test_retrieval_result_to_dict_preserves_all_fields(self):
        rr = make_result(
            chunk_id="c1", document_id="doc1", source_hash="hash1",
            source_file="f.pdf", page_start=2, page_end=3,
            standard_number="IS 3055", clause_id="4.1",
        )
        d = rr.to_dict()
        for field_name in ["chunk_id", "document_id", "source_hash", "source_file",
                           "page_start", "page_end", "standard_number", "clause_id"]:
            assert field_name in d, f"Missing field: {field_name}"

    def test_retrieval_result_from_dict_roundtrip(self):
        rr = make_result(chunk_id="c1", document_id="doc1", page_start=5)
        d = rr.to_dict()
        rr2 = RetrievalResult.from_dict(d)
        assert rr2.chunk_id == rr.chunk_id
        assert rr2.document_id == rr.document_id
        assert rr2.page_start == rr.page_start

    def test_mock_retriever_preserves_provenance(self):
        chunks = [{
            "chunk_id": "c_prov_test",
            "content": "Some requirements content",
            "metadata": {
                "document_id": "doc_abc",
                "source_hash": "sha256xyz",
                "source_file": "IS_999.pdf",
                "page_start": 7,
                "page_end": 8,
                "standard_number": "IS 999",
                "clause_id": "3.2",
            },
        }]
        retriever = make_mock_retriever(chunks)
        results = retriever.retrieve("IS 999 requirements", top_k=1)
        assert len(results) == 1
        r = results[0]
        assert r.document_id == "doc_abc"
        assert r.source_hash == "sha256xyz"
        assert r.page_start == 7
        assert r.page_end == 8


# ===========================================================
# I. MULTILINGUAL / UNICODE REGRESSION
# ===========================================================

class TestMultilingualRegression:

    def test_hindi_query_normalization_safe(self):
        query = "IS 3055 के अनुसार कैलिब्रेशन"
        result = normalize_query(query)
        assert "कैलिब्रेशन" in result
        assert "IS 3055" in result

    def test_english_query_unchanged_content(self):
        query = "IS 3055 calibration requirements"
        result = normalize_query(query)
        assert "calibration" in result
        assert "requirements" in result

    def test_mixed_language_query(self):
        query = "IS 3055 clinical thermometer मानक"
        result = normalize_query(query)
        assert "मानक" in result
        assert "IS 3055" in result

    def test_tokenize_unicode_safe(self):
        tokens = tokenize("IS 3055 गुणवत्ता 4.1")
        assert "3055" in tokens
        assert "4.1" in tokens
        # Devanagari token preserved
        assert any("गुणवत्ता" in t for t in tokens)

    def test_tokenize_empty(self):
        assert tokenize("") == []
        assert tokenize(None) == []


# ===========================================================
# J. END-TO-END RETRIEVAL (in-memory Chroma mock + fixture)
# ===========================================================

# Synthetic BIS fixture matching Phase 2/3 fixture structure
SYNTHETIC_CHUNKS = [
    {
        "chunk_id": "chunk_is3055_page1",
        "content": (
            "IS 3055 : 2024 Specification for Clinical Thermometers Third Edition "
            "Bureau of Indian Standards this standard specifies requirements for clinical "
            "glass thermometers used for measurement of human body temperature."
        ),
        "metadata": {
            "document_id": "doc_is3055_2024",
            "source_hash": "sha256_fixture_001",
            "source_file": "IS_3055_2024.pdf",
            "standard_number": "IS 3055",
            "standard_year": 2024,
            "edition_or_version": "Third Edition",
            "clause_id": None,
            "page_start": 1,
            "page_end": 1,
            "section": "IS 3055 : 2024",
        },
    },
    {
        "chunk_id": "chunk_is3055_clause4",
        "content": (
            "4 Requirements All glass thermometers covered by this standard shall conform "
            "to the following general requirements. The glass used shall be borosilicate "
            "or neutral glass conforming to IS 4984. The thermometric fluid shall be "
            "mercury or a non-toxic alternative. Thermometers shall be graduated in "
            "degrees Celsius with minimum graduation intervals of 0.1 degree."
        ),
        "metadata": {
            "document_id": "doc_is3055_2024",
            "source_hash": "sha256_fixture_001",
            "source_file": "IS_3055_2024.pdf",
            "standard_number": "IS 3055",
            "standard_year": 2024,
            "edition_or_version": "Third Edition",
            "clause_id": "4",
            "clause_title": "Requirements",
            "section": "4 Requirements",
            "page_start": 2,
            "page_end": 2,
        },
    },
    {
        "chunk_id": "chunk_is3055_clause41",
        "content": (
            "4.1 Calibration and Accuracy Requirements Clinical thermometers shall be "
            "calibrated against a certified reference thermometer. The maximum permissible "
            "error shall not exceed plus or minus 0.1 degree Celsius across the measurement "
            "range 35 to 42 degrees Celsius. Accuracy testing shall be performed at "
            "37 degrees and 40 degrees Celsius. Refer to IS 1391 for testing methodology."
        ),
        "metadata": {
            "document_id": "doc_is3055_2024",
            "source_hash": "sha256_fixture_001",
            "source_file": "IS_3055_2024.pdf",
            "standard_number": "IS 3055",
            "standard_year": 2024,
            "edition_or_version": "Third Edition",
            "clause_id": "4.1",
            "clause_title": "Calibration and Accuracy",
            "section": "4.1 Calibration and Accuracy Requirements",
            "page_start": 3,
            "page_end": 3,
        },
    },
    {
        "chunk_id": "chunk_is3055_amd1",
        "content": (
            "Amendment No. 1 to IS 3055 : 2024 Specification for Clinical Thermometers "
            "Clause 4.1 Calibration and Accuracy Requirements is amended to include "
            "digital clinical thermometers and additional sensor calibration standards."
        ),
        "metadata": {
            "document_id": "doc_is3055_amd1",
            "source_hash": "sha256_fixture_amd1",
            "source_file": "IS_3055_2024_AMD1.pdf",
            "standard_number": "IS 3055",
            "standard_year": 2024,
            "edition_or_version": "Third Edition",
            "amendment_number": "1",
            "clause_id": "4.1",
            "clause_title": "Amendment 1",
            "section": "Amendment No. 1",
            "page_start": 1,
            "page_end": 1,
        },
    },
]



@pytest.fixture
def bis_retriever():
    return make_mock_retriever(SYNTHETIC_CHUNKS)


class TestEndToEndRetrieval:

    def test_query_is3055_2024(self, bis_retriever):
        """'IS 3055 2024' should retrieve IS 3055 chunks with identifier match."""
        results = bis_retriever.retrieve("IS 3055 2024", top_k=3)
        assert len(results) > 0
        ids = [r.chunk_id for r in results]
        assert any("is3055" in cid for cid in ids)
        # At least one identifier match (year + standard)
        assert any(r.identifier_match for r in results)

    def test_query_clause41(self, bis_retriever):
        """'IS 3055 clause 4.1' should have chunk_is3055_clause41 in top results."""
        results = bis_retriever.retrieve("IS 3055 clause 4.1", top_k=3)
        ids = [r.chunk_id for r in results]
        assert "chunk_is3055_clause41" in ids

    def test_query_calibration_semantic(self, bis_retriever):
        """Semantic query 'calibration accuracy requirements' should retrieve clause 4.1."""
        results = bis_retriever.retrieve("calibration accuracy requirements", top_k=3)
        ids = [r.chunk_id for r in results]
        # clause 4.1 contains calibration content
        assert "chunk_is3055_clause41" in ids

    def test_top_result_has_all_provenance(self, bis_retriever):
        """Every returned result must carry complete provenance."""
        results = bis_retriever.retrieve("IS 3055 requirements", top_k=1)
        assert len(results) == 1
        r = results[0]
        assert r.chunk_id
        assert r.document_id
        assert r.source_hash
        assert r.source_file
        # standard metadata
        assert r.standard_number == "IS 3055"

    def test_retrieval_trace(self, bis_retriever):
        """return_trace=True returns (results, trace) with observable stages."""
        results, trace = bis_retriever.retrieve("IS 3055 clause 4.1", top_k=3, return_trace=True)
        assert isinstance(trace, RetrievalTrace)
        assert trace.original_query == "IS 3055 clause 4.1"
        assert trace.normalized_query  # should be non-empty
        assert trace.entities.get("standard_number") == "IS 3055"
        assert trace.entities.get("clause_id") == "4.1"
        assert isinstance(trace.dense_candidates, list)
        assert isinstance(trace.bm25_candidates, list)
        assert isinstance(trace.rrf_candidates, list)
        assert trace.total_latency_ms > 0


# ===========================================================
# K. EVALUATION METRICS
# ===========================================================

class TestEvaluationMetrics:

    def test_recall_at_k_perfect(self):
        assert recall_at_k(["c1", "c2"], ["c1", "c2", "c3"], k=3) == 1.0

    def test_recall_at_k_none(self):
        assert recall_at_k(["c99"], ["c1", "c2", "c3"], k=3) == 0.0

    def test_recall_at_k_partial(self):
        score = recall_at_k(["c1", "c2"], ["c1", "c3"], k=2)
        assert score == 0.5

    def test_recall_at_k_empty_expected(self):
        assert recall_at_k([], ["c1"], k=1) == 0.0

    def test_mrr_at_k_first_hit(self):
        score = mrr_at_k(["c1"], ["c1", "c2"], k=2)
        assert score == 1.0

    def test_mrr_at_k_second_hit(self):
        score = mrr_at_k(["c2"], ["c1", "c2"], k=2)
        assert abs(score - 0.5) < 1e-9

    def test_mrr_at_k_no_hit(self):
        assert mrr_at_k(["c99"], ["c1", "c2"], k=2) == 0.0

    def test_evaluate_retrieval_with_fixture(self):
        """Run evaluation against the synthetic BIS fixture."""
        retriever = make_mock_retriever(SYNTHETIC_CHUNKS)

        eval_dataset = [
            {"query": "IS 3055 2024", "expected_chunk_ids": ["chunk_is3055_page1", "chunk_is3055_clause4"]},
            {"query": "calibration accuracy requirements", "expected_chunk_ids": ["chunk_is3055_clause41"]},
            {"query": "IS 3055 clause 4.1", "expected_chunk_ids": ["chunk_is3055_clause41"]},
        ]
        metrics = evaluate_retrieval(retriever, eval_dataset, k_values=[1, 3, 5])

        assert "Recall@1" in metrics
        assert "Recall@3" in metrics
        assert "Recall@5" in metrics
        assert "MRR@5" in metrics
        # With synthetic fixture and real BM25, at Recall@5 we expect to find relevant chunks
        assert metrics["Recall@5"] > 0.0, f"Recall@5 should be > 0, got {metrics}"


# ===========================================================
# L. ERROR HANDLING / EDGE CASES
# ===========================================================

class TestErrorHandling:

    def test_retrieve_empty_query(self):
        chunks = [{"chunk_id": "c1", "content": "test", "metadata": {}}]
        retriever = make_mock_retriever(chunks)
        results = retriever.retrieve("", top_k=5)
        assert results == []

    def test_retrieve_whitespace_query(self):
        chunks = [{"chunk_id": "c1", "content": "test", "metadata": {}}]
        retriever = make_mock_retriever(chunks)
        results = retriever.retrieve("   ", top_k=5)
        assert results == []

    def test_rrf_empty_input(self):
        scores = reciprocal_rank_fusion([[], []])
        assert scores == {}

    def test_bm25_retrieval_empty_query(self):
        chunks = [{"chunk_id": "c1", "content": "test", "metadata": {}}]
        retriever = make_mock_retriever(chunks)
        results = retriever.retrieve_bm25("")
        assert results == []

    def test_normalize_scores_empty(self):
        result = normalize_scores([])
        assert len(result) == 0

    def test_normalize_scores_all_zero(self):
        import numpy as np
        result = normalize_scores([0.0, 0.0, 0.0])
        assert np.allclose(result, [0.0, 0.0, 0.0])

    def test_normalize_scores_all_equal_nonzero(self):
        import numpy as np
        result = normalize_scores([5.0, 5.0, 5.0])
        assert np.allclose(result, [1.0, 1.0, 1.0])

    def test_build_chroma_filters_no_args(self):
        assert build_chroma_filters() is None


# ===========================================================
# M. LATENCY MEASUREMENT
# ===========================================================

class TestLatencyMeasurement:

    def test_retrieval_completes_under_reasonable_time(self):
        """Retrieval on small corpus should complete in < 2 seconds."""
        chunks = [
            {
                "chunk_id": f"c{i}",
                "content": f"IS {3000 + i} requirements for product specification number {i}",
                "metadata": {"standard_number": f"IS {3000 + i}"},
            }
            for i in range(20)
        ]
        retriever = make_mock_retriever(chunks)
        t0 = time.perf_counter()
        results = retriever.retrieve("IS 3055 requirements", top_k=5)
        elapsed = time.perf_counter() - t0
        assert elapsed < 2.0, f"Retrieval too slow: {elapsed:.3f}s"

    def test_retrieval_trace_has_latency_fields(self):
        chunks = [{"chunk_id": "c1", "content": "IS 3055 spec", "metadata": {}}]
        retriever = make_mock_retriever(chunks)
        _, trace = retriever.retrieve("IS 3055", top_k=1, return_trace=True)
        assert trace.dense_latency_ms >= 0
        assert trace.bm25_latency_ms >= 0
        assert trace.rrf_latency_ms >= 0
        assert trace.total_latency_ms > 0


# ===========================================================
# BACKWARD COMPATIBILITY
# ===========================================================

class TestBackwardCompatibility:

    def test_tokenize_basic(self):
        tokens = tokenize("IS 3055: Clinical thermometers, Section 4.2-A")
        assert "is" in tokens
        assert "3055" in tokens
        assert "clinical" in tokens
        assert "4.2-a" in tokens or "4.2" in tokens

    def test_normalize_scores_still_works(self):
        import numpy as np
        res = normalize_scores([0.0, 5.0, 10.0])
        assert np.isclose(res[0], 0.0)
        assert np.isclose(res[2], 1.0)

    def test_flatten_provenance_backward_compat(self):
        chunk = {
            "chunk_id": "c1",
            "content": "text",
            "metadata": {"document_id": "doc1", "page_start": 3},
        }
        result = flatten_provenance(chunk)
        assert result["document_id"] == "doc1"
        assert result["page_start"] == 3


# ===========================================================
# PROMPT 4.1 — VERIFICATION CHECKPOINT
# ===========================================================

@pytest.fixture(scope="module")
def real_reranker():
    from app.rag.reranker import Reranker
    return Reranker()


class TestPrompt41Verification:

    def test_actual_crossencoder_pipeline_execution(self, bis_retriever, real_reranker):
        """CrossEncoder runs on retrieved candidates and attaches reranker_score."""
        retrieved = bis_retriever.retrieve("IS 3055 clause 4.1", top_k=3)
        assert len(retrieved) > 0
        reranked = real_reranker.rerank(query="IS 3055 clause 4.1", results=retrieved, top_k=3)
        assert len(reranked) > 0
        top = reranked[0]
        assert isinstance(top, RetrievalResult)
        assert top.reranker_score is not None
        assert isinstance(top.reranker_score, float)

    def test_reranker_score_appears_in_final_retrieval_result(self, bis_retriever, real_reranker):
        """reranker_score is accessible as attribute and dict key."""
        retrieved = bis_retriever.retrieve("calibration accuracy requirements", top_k=2)
        reranked = real_reranker.rerank(query="calibration accuracy requirements", results=retrieved, top_k=2)
        top = reranked[0]
        assert top.reranker_score is not None
        assert top["reranker_score"] == top.reranker_score
        assert "reranker_score" in top.to_dict()

    def test_final_ranking_preserves_provenance(self, bis_retriever, real_reranker):
        """All provenance fields survive CrossEncoder reranking."""
        retrieved = bis_retriever.retrieve("calibration accuracy requirements", top_k=3)
        reranked = real_reranker.rerank(query="calibration accuracy requirements", results=retrieved, top_k=3)
        for r in reranked:
            assert r.chunk_id
            assert r.document_id
            assert r.source_hash
            assert r.source_file
            assert r.page_start is not None

    def test_warm_latency_measurement(self, bis_retriever, real_reranker):
        """Warm latency measurement over multiple runs executes stably."""
        query = "IS 3055 2024"
        # warm-up
        r = bis_retriever.retrieve(query, top_k=3)
        _ = real_reranker.rerank(query=query, results=r, top_k=3)

        durations = []
        for _ in range(5):
            t0 = time.perf_counter()
            retrieved = bis_retriever.retrieve(query, top_k=3)
            reranked = real_reranker.rerank(query=query, results=retrieved, top_k=3)
            durations.append((time.perf_counter() - t0) * 1000)

        assert len(durations) == 5
        # Each warm run on 3 chunks should be well under 1000ms
        for d in durations:
            assert d < 1000.0

    def test_bm25_rebuild_behavior(self):
        """Adding new chunk to collection and calling build_bm25_index() updates BM25."""
        chunks = [
            {"chunk_id": "c1", "content": "initial document text about steel", "metadata": {}},
            {"chunk_id": "c2", "content": "second document text about copper", "metadata": {}},
        ]
        retriever = make_mock_retriever(chunks)
        assert len(retriever.bm25_chunks) == 2
        # Before rebuild: c3 is not in the BM25 index
        assert not any(c["chunk_id"] == "c3" for c in retriever.bm25_chunks)

        # Simulate incremental ingestion into collection
        new_chunk = {"chunk_id": "c3", "content": "aluminum alloy specification for aerospace", "metadata": {}}
        chunks.append(new_chunk)
        retriever.collection.count.return_value = len(chunks)

        # Rebuild BM25
        retriever.build_bm25_index()
        assert len(retriever.bm25_chunks) == 3
        assert any(c["chunk_id"] == "c3" for c in retriever.bm25_chunks)

        # Search for aluminum - c3 is now top hit with positive score
        res = retriever.retrieve_bm25("aluminum")
        assert len(res) > 0
        assert res[0]["chunk_id"] == "c3"
        assert res[0]["bm25_score"] > 0.0


# ===========================================================
# PROMPT 4.2 — INTENT-AWARE RANKING TESTS
# ===========================================================

class TestPrompt42IntentAwareRanking:

    def test_exact_clause_query_beats_generic_header(self, bis_retriever, real_reranker):
        """Requested clause 4.1 evidence must rank ahead of generic header chunk."""
        query = "IS 3055 clause 4.1"
        retrieved = bis_retriever.retrieve(query, top_k=4)
        reranked = real_reranker.rerank(query=query, results=retrieved, top_k=4)
        clause_positions = [i for i, r in enumerate(reranked) if r.clause_id == "4.1"]
        header_positions = [i for i, r in enumerate(reranked) if r.clause_id is None]
        assert len(clause_positions) > 0, "No clause 4.1 chunk returned"
        assert len(header_positions) > 0, "No header chunk in results"
        assert clause_positions[0] < header_positions[0], "Clause 4.1 should rank ahead of null-clause header"

    def test_exact_standard_and_clause_cannot_be_displaced_by_null_clause(self, bis_retriever, real_reranker):
        """Top result for exact standard+clause must have matching clause_id."""
        query = "IS 3055 clause 4.1"
        retrieved = bis_retriever.retrieve(query, top_k=4)
        reranked = real_reranker.rerank(query=query, results=retrieved, top_k=4)
        top = reranked[0]
        assert top.clause_id == "4.1"
        assert top.standard_number == "IS 3055"

    def test_exact_standard_only_query_may_return_header(self, bis_retriever, real_reranker):
        """For exact standard-only query, document header/title chunk legitimately ranks first."""
        query = "IS 3055 2024"
        retrieved = bis_retriever.retrieve(query, top_k=4)
        reranked = real_reranker.rerank(query=query, results=retrieved, top_k=4)
        top = reranked[0]
        assert top.standard_number == "IS 3055"
        assert top.chunk_id == "chunk_is3055_page1"

    def test_exact_amendment_query_prefers_amendment_evidence(self, bis_retriever, real_reranker):
        """Query specifying amendment 1 must return amendment chunk ahead of base standard chunks."""
        query = "IS 3055 amendment 1"
        retrieved = bis_retriever.retrieve(query, top_k=4)
        reranked = real_reranker.rerank(query=query, results=retrieved, top_k=4)
        top = reranked[0]
        assert top.amendment_number == "1"

    def test_explicit_version_query_prefers_matching_version(self):
        """Explicit version query prefers matching version chunk over mismatched version."""
        chunks = [
            {"chunk_id": "c_v2", "content": "IS 3055 Second Edition text", "metadata": {"standard_number": "IS 3055", "edition_or_version": "Second Edition", "standard_year": 2010}},
            {"chunk_id": "c_v3", "content": "IS 3055 Third Edition text", "metadata": {"standard_number": "IS 3055", "edition_or_version": "Third Edition", "standard_year": 2024}},
        ]
        retriever = make_mock_retriever(chunks)
        from app.rag.reranker import Reranker
        reranker = Reranker()
        query = "IS 3055 2024"
        retrieved = retriever.retrieve(query, top_k=2)
        reranked = reranker.rerank(query=query, results=retrieved, top_k=2)
        assert reranked[0].chunk_id == "c_v3"
        assert reranked[0].standard_year == 2024

    def test_unrelated_clause_does_not_outrank_exact_clause(self, bis_retriever, real_reranker):
        """Candidate with clause 4.1 must outrank candidate with clause 4 for query asking for 4.1."""
        query = "IS 3055 clause 4.1"
        retrieved = bis_retriever.retrieve(query, top_k=4)
        reranked = real_reranker.rerank(query=query, results=retrieved, top_k=4)
        c41_ranks = [i for i, r in enumerate(reranked) if r.clause_id == "4.1"]
        c4_ranks = [i for i, r in enumerate(reranked) if r.clause_id == "4"]
        assert len(c41_ranks) > 0 and len(c4_ranks) > 0
        assert c41_ranks[0] < c4_ranks[0]

    def test_provenance_remains_intact_after_final_reranking(self, bis_retriever, real_reranker):
        """All provenance fields are preserved after intent-aware reranking."""
        query = "IS 3055 clause 4.1"
        retrieved = bis_retriever.retrieve(query, top_k=4)
        reranked = real_reranker.rerank(query=query, results=retrieved, top_k=4)
        for r in reranked:
            assert r.chunk_id
            assert r.document_id
            assert r.source_hash
            assert r.source_file
            assert r.page_start is not None
            assert r.page_end is not None

    def test_edition_version_metadata_remains_consistent(self, bis_retriever, real_reranker):
        """Metadata audit: all chunks from Third Edition consistently report 'Third Edition'."""
        query = "calibration accuracy requirements"
        retrieved = bis_retriever.retrieve(query, top_k=4)
        reranked = real_reranker.rerank(query=query, results=retrieved, top_k=4)
        for r in reranked:
            assert r.edition_or_version == "Third Edition"

    def test_deterministic_final_ranking(self, bis_retriever, real_reranker):
        """Executing reranking multiple times produces identical ordering and reasons."""
        query = "IS 3055 clause 4.1"
        retrieved = bis_retriever.retrieve(query, top_k=4)
        r1 = real_reranker.rerank(query=query, results=retrieved, top_k=4)
        r2 = real_reranker.rerank(query=query, results=retrieved, top_k=4)
        assert [r.chunk_id for r in r1] == [r.chunk_id for r in r2]
        assert [r.ranking_reason for r in r1] == [r.ranking_reason for r in r2]




