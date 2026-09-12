"""
tests/test_e2e_prompt7.py

Phase 7 Section 23: End-to-End Tests against real / persisted pipeline components.

Covers:
  A. 'IS 3055 clause 4.1' -> strong evidence, high confidence / answerable
  B. 'calibration accuracy requirements' -> answerable with strong semantic evidence
  C. Absent standard query -> verification_required (NOT 'no such standard exists')
  D. Ambiguous query -> ambiguous / verification_required
  E. Conflicting evidence -> verification_required
"""

import os
os.environ.setdefault("USE_TF", "0")

import chromadb
from fastapi.testclient import TestClient
import pytest

from app.config import settings
from app.confidence import (
    ConfidenceLevel,
    ConfidenceResult,
    Decision,
    EvidenceEvaluator,
    EvidenceItem,
    QueryState,
)
from app.index_schema import build_chunk_index_metadata
from app.main import app
from app.rag.pipeline import RAGPipeline
from app.rag.query import RetrievalResult
from app.rag.reranker import Reranker
from app.rag.retriever import HybridRetriever


@pytest.fixture(scope="module")
def persistent_test_setup(tmp_path_factory):
    """
    Sets up a realistic Persistent ChromaDB vector index containing:
      - Header chunk for IS 3055 (page 1)
      - Explicit clause 4.1 chunk for IS 3055 (page 3)
      - Conflicting mandate chunks for testing conflict detection
    """
    tmp_dir = tmp_path_factory.mktemp("e2e_p7_vdb")
    client = chromadb.PersistentClient(path=str(tmp_dir))
    col = client.get_or_create_collection(name="e2e_p7_col")

    m_head = build_chunk_index_metadata(
        {
            "chunk_id": "c_head",
            "document_id": "doc_3055",
            "standard_number": "IS 3055",
            "standard_title": "Specification for Clinical Thermometers",
            "source_file": "standards/IS_3055.pdf",
            "page_start": 1,
            "authority": "BIS",
            "document_type": "indian_standard",
        },
        source_content="IS 3055 (Part 1) : 2024 Clinical Thermometers - Specification.",
        contextualized_content="[Standard: IS 3055] IS 3055 (Part 1) : 2024 Clinical Thermometers - Specification.",
        context_generation_method="structural",
        context_generation_version="1.0",
    )

    m_c41 = build_chunk_index_metadata(
        {
            "chunk_id": "c_41",
            "document_id": "doc_3055",
            "standard_number": "IS 3055",
            "standard_title": "Specification for Clinical Thermometers",
            "clause_id": "4.1",
            "clause_title": "Calibration and Accuracy",
            "source_file": "standards/IS_3055.pdf",
            "page_start": 3,
            "authority": "BIS",
            "document_type": "indian_standard",
        },
        source_content="Clause 4.1 Calibration and Accuracy. The maximum permissible error shall not exceed 0.1 C under laboratory testing.",
        contextualized_content="[Standard: IS 3055 | Clause: 4.1] Clause 4.1 Calibration and Accuracy. The maximum permissible error shall not exceed 0.1 C under laboratory testing.",
        context_generation_method="structural",
        context_generation_version="1.0",
    )

    col.add(
        ids=["c_head", "c_41"],
        embeddings=[[0.05] * 768, [0.08] * 768],
        documents=[
            "IS 3055 (Part 1) : 2024 Clinical Thermometers - Specification.",
            "Clause 4.1 Calibration and Accuracy. The maximum permissible error shall not exceed 0.1 C under laboratory testing.",
        ],
        metadatas=[m_head, m_c41],
    )

    retriever = HybridRetriever(collection=col)
    reranker = Reranker()
    pipeline = RAGPipeline(retriever=retriever, reranker=reranker)
    return {"col": col, "pipeline": pipeline}


# ---------------------------------------------------------------------------
# TEST A: 'IS 3055 clause 4.1' -> STRONG EVIDENCE, HIGH CONFIDENCE
# ---------------------------------------------------------------------------
def test_e2e_strong_clause_evidence(persistent_test_setup):
    pipeline = persistent_test_setup["pipeline"]
    res = pipeline.query("IS 3055 clause 4.1")

    assert res["decision"] == Decision.ANSWER.value
    assert res["confidence_level"] == ConfidenceLevel.HIGH.value
    assert res["confidence_score"] >= 0.70
    assert res["verification_required"] is False
    assert res["query_state"] == QueryState.ANSWERABLE.value

    # Check top source
    assert len(res["sources"]) > 0
    top_src = res["sources"][0]
    assert top_src["clause_id"] == "4.1"
    assert top_src["standard_number"] == "IS 3055"


# ---------------------------------------------------------------------------
# TEST B: 'calibration accuracy requirements' -> ANSWERABLE SEMANTIC EVIDENCE
# ---------------------------------------------------------------------------
def test_e2e_semantic_evidence_answerable(persistent_test_setup):
    pipeline = persistent_test_setup["pipeline"]
    res = pipeline.query("calibration accuracy requirements")

    assert res["query_state"] == QueryState.ANSWERABLE.value
    assert res["decision"] in (Decision.ANSWER.value, Decision.QUALIFIED_ANSWER.value)
    assert res["verification_required"] is False
    assert res["confidence_score"] >= 0.50
    assert len(res["sources"]) > 0


# ---------------------------------------------------------------------------
# TEST C: ABSENT STANDARD -> VERIFICATION REQUIRED, NOT 'NO SUCH STANDARD EXISTS'
# ---------------------------------------------------------------------------
def test_e2e_absent_standard_abstention(persistent_test_setup):
    pipeline = persistent_test_setup["pipeline"]
    res = pipeline.query("IS 99999 requirements")

    assert res["verification_required"] is True
    assert res["decision"] == Decision.VERIFICATION_REQUIRED.value
    assert res["query_state"] == QueryState.INSUFFICIENT_EVIDENCE.value

    # Epistemic honesty check: must NOT claim the standard does not exist in reality
    verif_reason = res.get("verification_reason", "").lower()
    answer_text = res.get("answer", "").lower()
    assert "no such standard exists" not in verif_reason
    assert "no such standard exists" not in answer_text
    assert "not found in the ingested documentation" in verif_reason or "verification required" in answer_text


# ---------------------------------------------------------------------------
# TEST D: AMBIGUOUS QUERY -> AMBIGUOUS / VERIFICATION REQUIRED
# ---------------------------------------------------------------------------
def test_e2e_ambiguous_query(persistent_test_setup):
    pipeline = persistent_test_setup["pipeline"]
    res = pipeline.query("rule")

    assert res["verification_required"] is True
    assert res["decision"] == Decision.VERIFICATION_REQUIRED.value
    assert res["query_state"] == QueryState.AMBIGUOUS_QUERY.value
    assert "Verification Required" in res["answer"]


# ---------------------------------------------------------------------------
# TEST E: CONFLICTING EVIDENCE -> VERIFICATION REQUIRED
# ---------------------------------------------------------------------------
def test_e2e_conflicting_evidence():
    # Build two contradictory items
    c_mand = EvidenceItem(
        chunk_id="c_mand",
        document_id="doc_x",
        standard_number="IS 3055",
        clause_id="5.2",
        source_file="std.pdf",
        content="Testing shall be mandatory and complied with for all units.",
        source_content="Testing shall be mandatory and complied with for all units.",
        reranker_score=3.5,
    )
    c_proh = EvidenceItem(
        chunk_id="c_proh",
        document_id="doc_y",
        standard_number="IS 3055",
        clause_id="5.2",
        source_file="std.pdf",
        content="Testing shall not be performed; testing is strictly prohibited.",
        source_content="Testing shall not be performed; testing is strictly prohibited.",
        reranker_score=3.4,
    )
    conf = EvidenceEvaluator.evaluate("IS 3055 clause 5.2", [c_mand, c_proh])
    assert conf.verification_required is True
    assert conf.decision == Decision.VERIFICATION_REQUIRED
    assert conf.query_state == QueryState.CONFLICTING_EVIDENCE
    assert conf.conflicting_evidence_count >= 2


# ---------------------------------------------------------------------------
# TEST F: HTTP ENDPOINT VERIFICATION (/query returns 200 with confidence fields)
# ---------------------------------------------------------------------------
def test_e2e_api_query_endpoint(persistent_test_setup):
    client = TestClient(app)
    app.state.rag_pipeline = persistent_test_setup["pipeline"]

    resp = client.post("/query", json={"query": "IS 3055 clause 4.1"})
    assert resp.status_code == 200
    data = resp.json()

    assert "confidence_score" in data
    assert "confidence_level" in data
    assert "decision" in data
    assert "verification_required" in data
    assert data["confidence_level"] == "high"
    assert data["decision"] == "answer"
    assert data["verification_required"] is False
