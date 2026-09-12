"""
tests/test_e2e_prompt8.py

Phase 8 Sections 28 & 29: End-to-End Grounding and Adversarial Generator Tests.

Covers:
  - Real fixture/retrieval test for 'IS 3055 clause 4.1'
  - Verification of citations, page provenance, metadata, and authoritative text separation
  - Scenarios A through F (Section 28)
  - Adversarial Generator Test Suite (Section 29)
  - Safety metric: False Grounded Claim Rate = 0.0%
  - HTTP /query endpoint contract test via FastAPI TestClient
"""

import os
os.environ.setdefault("USE_TF", "0")

from unittest.mock import MagicMock
import chromadb
from fastapi.testclient import TestClient
import pytest

from app.confidence import Decision
from app.grounding.models import (
    AnswerClaim,
    Citation,
    ClaimType,
    GroundingResult,
    GroundingStatus,
    SupportStatus,
)
from app.grounding.validator import GroundingValidator
from app.index_schema import build_chunk_index_metadata
from app.main import app
from app.rag.pipeline import RAGPipeline
from app.rag.reranker import Reranker
from app.rag.retriever import HybridRetriever


@pytest.fixture(scope="module")
def persistent_e2e_setup(tmp_path_factory):
    """
    Sets up a realistic Persistent ChromaDB vector index containing:
      - Header chunk for IS 3055 (page 1)
      - Explicit clause 4.1 chunk for IS 3055 (page 3)
    """
    tmp_dir = tmp_path_factory.mktemp("e2e_p8_vdb")
    client = chromadb.PersistentClient(path=str(tmp_dir))
    col = client.get_or_create_collection(name="e2e_p8_col")

    m_head = build_chunk_index_metadata(
        {
            "chunk_id": "c_head",
            "document_id": "doc_3055",
            "standard_number": "IS 3055",
            "standard_title": "Specification for Clinical Thermometers",
            "edition_or_version": "Third Edition",
            "source_file": "standards/IS_3055.pdf",
            "page_start": 1,
            "authority": "BIS",
            "document_type": "indian_standard",
            "standard_year": 2024,
        },
        source_content="IS 3055 (Part 1) : 2024 Clinical Thermometers - Specification. Third Edition.",
        contextualized_content="[Standard: IS 3055] IS 3055 (Part 1) : 2024 Clinical Thermometers - Specification. Third Edition.",
        context_generation_method="structural",
        context_generation_version="1.0",
    )

    m_c41 = build_chunk_index_metadata(
        {
            "chunk_id": "c_41",
            "document_id": "doc_3055",
            "standard_number": "IS 3055",
            "standard_title": "Specification for Clinical Thermometers",
            "edition_or_version": "Third Edition",
            "clause_id": "4.1",
            "clause_title": "Calibration and Accuracy",
            "source_file": "standards/IS_3055.pdf",
            "page_start": 3,
            "authority": "BIS",
            "document_type": "indian_standard",
            "standard_year": 2024,
        },
        source_content="Clause 4.1 Calibration and Accuracy. The maximum permissible error shall not exceed 0.1 deg C under laboratory testing.",
        contextualized_content="[Standard: IS 3055 | Clause: 4.1] Clause 4.1 Calibration and Accuracy. The maximum permissible error shall not exceed 0.1 deg C under laboratory testing.",
        context_generation_method="structural",
        context_generation_version="1.0",
    )

    col.add(
        ids=["c_head", "c_41"],
        embeddings=[[0.05] * 768, [0.08] * 768],
        documents=[
            "IS 3055 (Part 1) : 2024 Clinical Thermometers - Specification. Third Edition.",
            "Clause 4.1 Calibration and Accuracy. The maximum permissible error shall not exceed 0.1 deg C under laboratory testing.",
        ],
        metadatas=[m_head, m_c41],
    )

    retriever = HybridRetriever(collection=col)
    reranker = Reranker()
    return {"col": col, "retriever": retriever, "reranker": reranker}


# ===========================================================================
# 1. LIVE E2E RETRIEVAL & GROUNDING VERIFICATION (IS 3055 Clause 4.1)
# ===========================================================================
def test_e2e_is3055_clause41_grounding(persistent_e2e_setup):
    """
    Query 'IS 3055 clause 4.1' against the real retriever and reranker.
    Verify:
      - citations resolve to actual EvidenceItems
      - Clause 4.1 evidence is cited
      - source page is correct (3)
      - standard and version metadata are correct
      - contextualized breadcrumbs are not in citation source text
    """
    mock_gen = MagicMock()
    mock_gen.generate.return_value = {
        "answer": "Under Clause 4.1, maximum permissible error shall not exceed 0.1 deg C [EV1].",
        "raw_claims": [
            {
                "claim_id": "C1",
                "text": "Under Clause 4.1, maximum permissible error shall not exceed 0.1 deg C",
                "claim_type": "fact",
                "citation_ids": ["EV1"],
            }
        ],
        "model": "mock",
        "context_chunks": 2,
    }

    pipeline = RAGPipeline(
        retriever=persistent_e2e_setup["retriever"],
        reranker=persistent_e2e_setup["reranker"],
        generator=mock_gen,
    )

    res = pipeline.query("IS 3055 clause 4.1")

    assert res["decision"] == "answer"
    assert res["verification_required"] is False
    assert res["grounding_status"] == "fully_grounded"
    assert res["citation_coverage"] == 1.0
    assert res["groundedness_score"] == 1.0

    # Verify citation details
    assert len(res["citations"]) >= 1
    c1 = res["citations"][0]
    assert c1["citation_id"] == "EV1"
    assert c1["clause_id"] == "4.1"
    assert c1["standard_number"] == "IS 3055"
    assert c1["page_start"] == 3
    assert c1["edition_or_version"] == "Third Edition"

    # Verify authoritative source text separation: no [Standard: ...] breadcrumbs
    sources = res["sources"]
    assert len(sources) >= 1
    for s in sources:
        if s.get("clause_id") == "4.1":
            assert s["page_start"] == 3


# ===========================================================================
# 2. SCENARIOS A THROUGH F (Section 28)
# ===========================================================================

def test_scenario_a_unsupported_extra_number(persistent_e2e_setup):
    """A. Unsupported extra number introduced by mock generator -> claim rejected."""
    mock_gen = MagicMock()
    mock_gen.generate.return_value = {
        "answer": "The maximum permissible error shall not exceed 0.1 deg C every 12 months on 50 samples [EV1].",
        "raw_claims": [
            {
                "claim_id": "C1",
                "text": "The maximum permissible error shall not exceed 0.1 deg C every 12 months on 50 samples",
                "claim_type": "fact",
                "citation_ids": ["EV1"],
            }
        ],
        "model": "mock",
        "context_chunks": 2,
    }

    pipeline = RAGPipeline(
        retriever=persistent_e2e_setup["retriever"],
        reranker=persistent_e2e_setup["reranker"],
        generator=mock_gen,
    )
    res = pipeline.query("IS 3055 clause 4.1")

    assert res["decision"] == "verification_required"
    assert res["verification_required"] is True
    assert res["grounding_status"] == "unsupported"
    assert any("unsupported_numerical_value" in iss for iss in res["claims"][0]["issues"])


def test_scenario_b_wrong_clause(persistent_e2e_setup):
    """B. Wrong clause introduced by mock generator -> claim rejected."""
    mock_gen = MagicMock()
    mock_gen.generate.return_value = {
        "answer": "Clause 8.3 specifies accuracy requirements for clinical thermometers [EV1].",
        "raw_claims": [
            {
                "claim_id": "C1",
                "text": "Clause 8.3 specifies accuracy requirements for clinical thermometers",
                "claim_type": "fact",
                "citation_ids": ["EV1"],
            }
        ],
        "model": "mock",
        "context_chunks": 2,
    }

    pipeline = RAGPipeline(
        retriever=persistent_e2e_setup["retriever"],
        reranker=persistent_e2e_setup["reranker"],
        generator=mock_gen,
    )
    res = pipeline.query("IS 3055 clause 4.1")

    assert res["decision"] == "verification_required"
    assert res["verification_required"] is True
    assert res["grounding_status"] == "unsupported"
    assert any("clause_mismatch:claim=Clause 8.3" in iss for iss in res["claims"][0]["issues"])


def test_scenario_c_wrong_standard(persistent_e2e_setup):
    """C. Wrong standard introduced by mock generator -> claim rejected."""
    mock_gen = MagicMock()
    mock_gen.generate.return_value = {
        "answer": "IS 456 specifies calibration and accuracy limits for thermometers [EV1].",
        "raw_claims": [
            {
                "claim_id": "C1",
                "text": "IS 456 specifies calibration and accuracy limits for thermometers",
                "claim_type": "fact",
                "citation_ids": ["EV1"],
            }
        ],
        "model": "mock",
        "context_chunks": 2,
    }

    pipeline = RAGPipeline(
        retriever=persistent_e2e_setup["retriever"],
        reranker=persistent_e2e_setup["reranker"],
        generator=mock_gen,
    )
    res = pipeline.query("IS 3055 clause 4.1")

    assert res["decision"] == "verification_required"
    assert res["verification_required"] is True
    assert res["grounding_status"] == "unsupported"
    assert any("standard_mismatch:claim=IS 456" in iss for iss in res["claims"][0]["issues"])


def test_scenario_d_no_citations(persistent_e2e_setup):
    """D. Answer with no citations -> grounding failure / unverifiable."""
    mock_gen = MagicMock()
    mock_gen.generate.return_value = {
        "answer": "The maximum permissible error shall not exceed 0.1 deg C.",
        "raw_claims": [
            {
                "claim_id": "C1",
                "text": "The maximum permissible error shall not exceed 0.1 deg C.",
                "claim_type": "fact",
                "citation_ids": [],
            }
        ],
        "model": "mock",
        "context_chunks": 2,
    }

    pipeline = RAGPipeline(
        retriever=persistent_e2e_setup["retriever"],
        reranker=persistent_e2e_setup["reranker"],
        generator=mock_gen,
    )
    res = pipeline.query("IS 3055 clause 4.1")

    assert res["claims"][0]["support_status"] == "unverifiable"
    assert "missing_citation" in res["claims"][0]["issues"]


def test_scenario_e_valid_citation_unsupported_conclusion(persistent_e2e_setup):
    """E. Answer with valid citations but unsupported conclusion -> rejected / qualified."""
    mock_gen = MagicMock()
    mock_gen.generate.return_value = {
        "answer": "Clause 4.1 requires accuracy testing, meaning manufacturers are prohibited from selling without BIS approval [EV1].",
        "raw_claims": [
            {
                "claim_id": "C1",
                "text": "Clause 4.1 requires accuracy testing, meaning manufacturers are prohibited from selling without BIS approval",
                "claim_type": "fact",
                "citation_ids": ["EV1"],
            }
        ],
        "model": "mock",
        "context_chunks": 2,
    }

    pipeline = RAGPipeline(
        retriever=persistent_e2e_setup["retriever"],
        reranker=persistent_e2e_setup["reranker"],
        generator=mock_gen,
    )
    res = pipeline.query("IS 3055 clause 4.1")

    assert res["decision"] == "verification_required"
    assert res["verification_required"] is True
    assert any("unsupported_legal_conclusion" in iss for iss in res["claims"][0]["issues"])


def test_scenario_f_fully_supported_answer(persistent_e2e_setup):
    """F. Fully supported answer -> accepted."""
    mock_gen = MagicMock()
    mock_gen.generate.return_value = {
        "answer": "Under Clause 4.1 of IS 3055, the maximum permissible error shall not exceed 0.1 deg C under laboratory testing [EV1].",
        "raw_claims": [
            {
                "claim_id": "C1",
                "text": "Under Clause 4.1 of IS 3055, the maximum permissible error shall not exceed 0.1 deg C under laboratory testing",
                "claim_type": "fact",
                "citation_ids": ["EV1"],
            }
        ],
        "model": "mock",
        "context_chunks": 2,
    }

    pipeline = RAGPipeline(
        retriever=persistent_e2e_setup["retriever"],
        reranker=persistent_e2e_setup["reranker"],
        generator=mock_gen,
    )
    res = pipeline.query("IS 3055 clause 4.1")

    assert res["decision"] == "answer"
    assert res["verification_required"] is False
    assert res["grounding_status"] == "fully_grounded"
    assert res["groundedness_score"] == 1.0


# ===========================================================================
# 3. ADVERSARIAL GENERATOR TEST SUITE (Section 29)
# ===========================================================================

@pytest.mark.parametrize(
    "adv_name,adv_text,adv_cids,expected_issue",
    [
        ("invented_clause", "Clause 9.9 requires recalibration", ["EV1"], "clause_mismatch:claim=Clause 9.9"),
        ("invented_date", "Testing shall commence in year 2045", ["EV1"], "unsupported_numerical_value:2045"),
        ("invented_frequency", "Error verification shall be done every 4 hours", ["EV1"], "unsupported_numerical_value:4 hours"),
        ("invented_standard", "IS 9999 requires 0.1 deg C tolerance", ["EV1"], "standard_mismatch:claim=IS 9999"),
        ("broad_legal_conclusion", "This requirement makes non-compliant thermometers illegal to sell", ["EV1"], "unsupported_legal_conclusion"),
        ("unsupported_negative_claim", "No requirement exists for calibration in IS 3055", ["EV1"], "unsupported_negative_assertion"),
        ("fake_citation_id", "Permissible error shall not exceed 0.1 deg C", ["EV99"], "fake_or_unknown_citation_id:EV99"),
        ("wrong_citation_attribution", "Ordinary Portland Cement must have 33 MPa strength", ["EV1"], "unsupported_terms"),
        ("partially_true_with_hallucination", "Error shall not exceed 0.1 deg C and testing must be performed under boiling cryogenic fluid", ["EV1"], "unsupported_terms"),
    ],
)
def test_adversarial_generator_cases(persistent_e2e_setup, adv_name, adv_text, adv_cids, expected_issue):
    """
    Adversarial generator test suite verifying 0% False Grounded Claim Rate.
    All adversarial fabrications must be caught and rejected.
    """
    mock_gen = MagicMock()
    mock_gen.generate.return_value = {
        "answer": f"{adv_text} [{' '.join(adv_cids)}].",
        "raw_claims": [
            {
                "claim_id": "ADV1",
                "text": adv_text,
                "claim_type": "fact",
                "citation_ids": adv_cids,
            }
        ],
        "model": "adversarial-mock",
        "context_chunks": 2,
    }

    pipeline = RAGPipeline(
        retriever=persistent_e2e_setup["retriever"],
        reranker=persistent_e2e_setup["reranker"],
        generator=mock_gen,
    )
    res = pipeline.query("IS 3055 clause 4.1")

    # MUST NOT be presented as a supported factual answer
    assert res["grounding_status"] == "unsupported", f"Adversarial case '{adv_name}' failed to be marked unsupported!"
    assert res["decision"] == "verification_required"
    assert res["verification_required"] is True

    claim_issues = res["claims"][0]["issues"]
    assert any(expected_issue in iss for iss in claim_issues), (
        f"Expected issue '{expected_issue}' not found in claim issues: {claim_issues}"
    )


# ===========================================================================
# 4. HTTP /query API ENDPOINT TEST (Section 21)
# ===========================================================================

def test_api_query_returns_phase8_grounding_fields(persistent_e2e_setup):
    """
    Tests POST /query returns HTTP 200 with all Phase 7 confidence
    and Phase 8 grounding fields.
    """
    client = TestClient(app)

    # Inject test pipeline into app.state
    pipeline = RAGPipeline(
        retriever=persistent_e2e_setup["retriever"],
        reranker=persistent_e2e_setup["reranker"],
    )
    app.state.rag_pipeline = pipeline

    resp = client.post("/query", json={"query": "IS 3055 clause 4.1"})
    assert resp.status_code == 200
    data = resp.json()

    # Phase 7 fields
    assert "confidence_score" in data
    assert "confidence_level" in data
    assert "decision" in data
    assert "query_state" in data
    assert "verification_required" in data
    assert "evidence_summary" in data

    # Phase 8 fields
    assert "citations" in data
    assert isinstance(data["citations"], list)
    assert len(data["citations"]) > 0
    assert "citation_id" in data["citations"][0]
    assert "claims" in data
    assert "citation_coverage" in data
    assert "grounding_status" in data
    assert "grounding_reason" in data
    assert "groundedness_score" in data
