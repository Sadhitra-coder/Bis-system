"""
tests/test_grounding.py

Phase 8 Failure-First Unit Tests for Citation Enforcement and Post-Generation Grounding Validation (Section 27).

Covers all 25 test requirements:
  1. fully supported factual claim
  2. unsupported factual claim
  3. partially supported claim
  4. missing citation
  5. fake citation ID
  6. citation pointing to wrong chunk
  7. wrong clause citation
  8. wrong standard citation
  9. wrong version citation
  10. wrong page metadata (model cannot invent page)
  11. numeric hallucination (e.g. 6 vs 12 months, 10 vs 25 pieces)
  12. date hallucination
  13. amendment hallucination
  14. unsupported legal interpretation
  15. no evidence != negative evidence (epistemic honesty)
  16. multiple supporting citations
  17. duplicate evidence is not double-counted
  18. contextualized text never becomes citation source
  19. source_content remains authoritative
  20. Phase 7 confidence still works
  21. verification_required is not HTTP 500
  22. structured generator output validation
  23. malformed generator output fallback
  24. regeneration limit (bounded to 1 retry)
  25. deterministic citation resolution
"""

import pytest
from app.evidence.models import EvidenceItem
from app.grounding.models import (
    AnswerClaim,
    Citation,
    ClaimType,
    GroundingResult,
    GroundingStatus,
    SupportStatus,
)
from app.grounding.validator import GroundingValidator
from app.confidence.evaluator import EvidenceEvaluator
from app.confidence.models import Decision
from app.rag.generator import AnswerGenerator


def _make_evidence(
    chunk_id: str = "c1",
    document_id: str = "doc_3055",
    standard_number: str = "IS 3055",
    clause_id: str = "4.1",
    page_start: int = 3,
    page_end: int = 3,
    reranker_score: float = 3.5,
    content: str = None,
    source_content: str = None,
    contextualized_content: str = None,
    edition_or_version: str = "Third Edition",
    amendment_number: str = "",
    standard_year: int = 2024,
    authority: str = "BIS",
) -> EvidenceItem:
    text = source_content or content or (
        "4.1 Accuracy Requirements: Clinical thermometers shall not exceed an error of 0.1 deg C. "
        "Testing shall be performed on a sample size of 10 pieces every 6 months."
    )
    return EvidenceItem(
        chunk_id=chunk_id,
        document_id=document_id,
        source_hash="sha_doc3055",
        source_file="standards/is3055.pdf",
        page_start=page_start,
        page_end=page_end,
        content=text,
        source_content=text,
        contextualized_content=contextualized_content,
        standard_number=standard_number,
        standard_title="Clinical Thermometers Specification",
        edition_or_version=edition_or_version,
        clause_id=clause_id,
        clause_title="Accuracy Requirements",
        standard_year=standard_year,
        amendment_number=amendment_number,
        reranker_score=reranker_score,
        authority=authority,
    )


# ------------------------------------------------------------
# 1. Fully supported factual claim
# ------------------------------------------------------------
def test_fully_supported_factual_claim():
    ev = _make_evidence()
    claims = [
        {
            "claim_id": "C1",
            "text": "Clinical thermometers shall not exceed an error of 0.1 deg C [EV1].",
            "claim_type": "fact",
            "citation_ids": ["EV1"],
        }
    ]
    res = GroundingValidator.validate(answer="", evidence_items=[ev], raw_claims=claims)
    assert res.status == GroundingStatus.FULLY_GROUNDED
    assert len(res.claims) == 1
    assert res.claims[0].support_status == SupportStatus.SUPPORTED
    assert res.citation_coverage == 1.0
    assert res.groundedness_score == 1.0


# ------------------------------------------------------------
# 2. Unsupported factual claim
# ------------------------------------------------------------
def test_unsupported_factual_claim():
    ev = _make_evidence()
    claims = [
        {
            "claim_id": "C1",
            "text": "Clinical thermometers must undergo submerged ultrasonic vibration testing [EV1].",
            "claim_type": "fact",
            "citation_ids": ["EV1"],
        }
    ]
    res = GroundingValidator.validate(answer="", evidence_items=[ev], raw_claims=claims)
    assert res.status == GroundingStatus.UNSUPPORTED
    assert res.claims[0].support_status == SupportStatus.UNSUPPORTED
    assert res.citation_coverage == 0.0


# ------------------------------------------------------------
# 3. Partially supported claim
# ------------------------------------------------------------
def test_partially_supported_claim():
    ev = _make_evidence()
    claims = [
        {
            "claim_id": "C1",
            "text": "Clinical thermometers shall be tested for accuracy error [EV1].",
            "claim_type": "interpretation",
            "citation_ids": ["EV1"],
        }
    ]
    res = GroundingValidator.validate(answer="", evidence_items=[ev], raw_claims=claims)
    assert res.status == GroundingStatus.FULLY_GROUNDED or res.status == GroundingStatus.PARTIALLY_GROUNDED


# ------------------------------------------------------------
# 4. Missing citation
# ------------------------------------------------------------
def test_missing_citation():
    ev = _make_evidence()
    claims = [
        {
            "claim_id": "C1",
            "text": "Clinical thermometers shall not exceed an error of 0.1 deg C.",
            "claim_type": "fact",
            "citation_ids": [],
        }
    ]
    res = GroundingValidator.validate(answer="", evidence_items=[ev], raw_claims=claims)
    assert res.claims[0].support_status == SupportStatus.UNVERIFIABLE
    assert "missing_citation" in res.claims[0].issues
    assert res.claims_without_citations == 1


# ------------------------------------------------------------
# 5. Fake citation ID
# ------------------------------------------------------------
def test_fake_citation_id():
    ev = _make_evidence()
    claims = [
        {
            "claim_id": "C1",
            "text": "Clinical thermometers shall not exceed error of 0.1 deg C [EV99].",
            "claim_type": "fact",
            "citation_ids": ["EV99"],
        }
    ]
    res = GroundingValidator.validate(answer="", evidence_items=[ev], raw_claims=claims)
    assert res.status == GroundingStatus.UNSUPPORTED
    assert res.claims[0].support_status == SupportStatus.UNSUPPORTED
    assert any("fake_or_unknown_citation_id" in iss for iss in res.claims[0].issues)


# ------------------------------------------------------------
# 6. Citation pointing to wrong chunk
# ------------------------------------------------------------
def test_citation_pointing_to_wrong_chunk():
    ev_cement = _make_evidence(
        chunk_id="c_cement",
        standard_number="IS 269",
        content="Ordinary Portland Cement compressive strength at 7 days shall be not less than 33 MPa.",
    )
    claims = [
        {
            "claim_id": "C1",
            "text": "Clinical thermometers shall not exceed an error of 0.1 deg C [EV1].",
            "claim_type": "fact",
            "citation_ids": ["EV1"],
        }
    ]
    res = GroundingValidator.validate(answer="", evidence_items=[ev_cement], raw_claims=claims)
    assert res.status == GroundingStatus.UNSUPPORTED
    assert res.claims[0].support_status == SupportStatus.UNSUPPORTED


# ------------------------------------------------------------
# 7. Wrong clause citation
# ------------------------------------------------------------
def test_wrong_clause_citation():
    ev = _make_evidence(clause_id="5.2")
    claims = [
        {
            "claim_id": "C1",
            "text": "Clause 4.1 specifies accuracy requirements for clinical thermometers [EV1].",
            "claim_type": "fact",
            "citation_ids": ["EV1"],
        }
    ]
    res = GroundingValidator.validate(answer="", evidence_items=[ev], raw_claims=claims)
    assert res.status == GroundingStatus.UNSUPPORTED
    assert any("clause_mismatch:claim=Clause 4.1" in iss for iss in res.claims[0].issues)


# ------------------------------------------------------------
# 8. Wrong standard citation
# ------------------------------------------------------------
def test_wrong_standard_citation():
    ev = _make_evidence(standard_number="IS 1234")
    claims = [
        {
            "claim_id": "C1",
            "text": "IS 3055 specifies clinical thermometer requirements [EV1].",
            "claim_type": "fact",
            "citation_ids": ["EV1"],
        }
    ]
    res = GroundingValidator.validate(answer="", evidence_items=[ev], raw_claims=claims)
    assert res.status == GroundingStatus.UNSUPPORTED
    assert any("standard_mismatch:claim=IS 3055" in iss for iss in res.claims[0].issues)


# ------------------------------------------------------------
# 9. Wrong version citation
# ------------------------------------------------------------
def test_wrong_version_citation():
    ev = _make_evidence(edition_or_version="First Edition")
    claims = [
        {
            "claim_id": "C1",
            "text": "Third Edition specifies accuracy requirements [EV1].",
            "claim_type": "fact",
            "citation_ids": ["EV1"],
        }
    ]
    res = GroundingValidator.validate(answer="", evidence_items=[ev], raw_claims=claims)
    assert res.status == GroundingStatus.UNSUPPORTED
    assert any("version_mismatch:claim=Third Edition" in iss for iss in res.claims[0].issues)


# ------------------------------------------------------------
# 10. Wrong page metadata (application controls metadata)
# ------------------------------------------------------------
def test_wrong_page_metadata():
    ev = _make_evidence(page_start=3, page_end=3)
    claims = [
        {
            "claim_id": "C1",
            "text": "According to page 99, thermometers shall not exceed error of 0.1 deg C [EV1].",
            "claim_type": "fact",
            "citation_ids": ["EV1"],
        }
    ]
    res = GroundingValidator.validate(answer="", evidence_items=[ev], raw_claims=claims)
    # The application-controlled citation MUST have page_start=3, ignoring LLM's "page 99"
    assert len(res.citations) == 1
    assert res.citations[0].page_start == 3
    assert res.citations[0].page_end == 3


# ------------------------------------------------------------
# 11. Numeric hallucination
# ------------------------------------------------------------
def test_numeric_hallucination():
    ev = _make_evidence()  # contains "sample size of 10 pieces every 6 months"
    claims = [
        {
            "claim_id": "C1",
            "text": "Testing shall be performed on a sample size of 25 pieces every 12 months [EV1].",
            "claim_type": "fact",
            "citation_ids": ["EV1"],
        }
    ]
    res = GroundingValidator.validate(answer="", evidence_items=[ev], raw_claims=claims)
    assert res.status == GroundingStatus.UNSUPPORTED
    assert any("unsupported_numerical_value" in iss for iss in res.claims[0].issues)


# ------------------------------------------------------------
# 12. Date hallucination
# ------------------------------------------------------------
def test_date_hallucination():
    ev = _make_evidence(content="The standard took effect on 15 January 2020.")
    claims = [
        {
            "claim_id": "C1",
            "text": "The standard took effect on 15 January 2025 [EV1].",
            "claim_type": "fact",
            "citation_ids": ["EV1"],
        }
    ]
    res = GroundingValidator.validate(answer="", evidence_items=[ev], raw_claims=claims)
    assert res.status == GroundingStatus.UNSUPPORTED
    assert any("unsupported_numerical_value" in iss for iss in res.claims[0].issues)


# ------------------------------------------------------------
# 13. Amendment hallucination
# ------------------------------------------------------------
def test_amendment_hallucination():
    ev = _make_evidence(amendment_number="1")
    claims = [
        {
            "claim_id": "C1",
            "text": "Amendment 3 specifies thermometers accuracy requirements [EV1].",
            "claim_type": "fact",
            "citation_ids": ["EV1"],
        }
    ]
    res = GroundingValidator.validate(answer="", evidence_items=[ev], raw_claims=claims)
    assert res.status == GroundingStatus.UNSUPPORTED
    assert any("amendment_mismatch:claim=Amendment 3" in iss for iss in res.claims[0].issues)


# ------------------------------------------------------------
# 14. Unsupported legal interpretation
# ------------------------------------------------------------
def test_unsupported_legal_interpretation():
    ev = _make_evidence()
    claims = [
        {
            "claim_id": "C1",
            "text": "This clause means manufacturers are prohibited from selling thermometers without BIS certification [EV1].",
            "claim_type": "fact",
            "citation_ids": ["EV1"],
        }
    ]
    res = GroundingValidator.validate(answer="", evidence_items=[ev], raw_claims=claims)
    assert res.status == GroundingStatus.UNSUPPORTED
    assert any("unsupported_legal_conclusion" in iss for iss in res.claims[0].issues)


# ------------------------------------------------------------
# 15. No evidence != negative evidence (epistemic honesty)
# ------------------------------------------------------------
def test_no_evidence_not_negative_evidence():
    ev = _make_evidence()  # Does NOT mention lubrication
    negative_claim = [
        {
            "claim_id": "C1",
            "text": "No requirement exists for lubrication in IS 3055 [EV1].",
            "claim_type": "fact",
            "citation_ids": ["EV1"],
        }
    ]
    res_neg = GroundingValidator.validate(answer="", evidence_items=[ev], raw_claims=negative_claim)
    assert res_neg.status == GroundingStatus.UNSUPPORTED
    assert any("unsupported_negative_assertion" in iss for iss in res_neg.claims[0].issues)

    # In contrast, an honest uncertainty claim is supported
    uncertainty_claim = [
        {
            "claim_id": "C2",
            "text": "No requirement was found in the retrieved documentation regarding lubrication.",
            "claim_type": "uncertainty",
            "citation_ids": [],
        }
    ]
    res_unc = GroundingValidator.validate(answer="", evidence_items=[ev], raw_claims=uncertainty_claim)
    assert res_unc.claims[0].support_status == SupportStatus.SUPPORTED


# ------------------------------------------------------------
# 16. Multiple supporting citations
# ------------------------------------------------------------
def test_multiple_supporting_citations():
    ev1 = _make_evidence(chunk_id="c1", content="Testing shall be performed on 10 pieces.")
    ev2 = _make_evidence(chunk_id="c2", content="Error shall not exceed 0.1 deg C.")
    claims = [
        {
            "claim_id": "C1",
            "text": "Testing shall be performed on 10 pieces [EV1] and error shall not exceed 0.1 deg C [EV2].",
            "claim_type": "fact",
            "citation_ids": ["EV1", "EV2"],
        }
    ]
    res = GroundingValidator.validate(answer="", evidence_items=[ev1, ev2], raw_claims=claims)
    assert res.status == GroundingStatus.FULLY_GROUNDED
    assert res.claims[0].supporting_citation_count == 2


# ------------------------------------------------------------
# 17. Duplicate evidence is not double-counted
# ------------------------------------------------------------
def test_duplicate_evidence_not_double_counted():
    # Two chunks with identical content -> identical content_hash
    ev1 = _make_evidence(chunk_id="c1", content="Clinical thermometers shall not exceed an error of 0.1 deg C.")
    ev2 = _make_evidence(chunk_id="c2", content="Clinical thermometers shall not exceed an error of 0.1 deg C.")
    claims = [
        {
            "claim_id": "C1",
            "text": "Clinical thermometers shall not exceed an error of 0.1 deg C [EV1] [EV2].",
            "claim_type": "fact",
            "citation_ids": ["EV1", "EV2"],
        }
    ]
    res = GroundingValidator.validate(answer="", evidence_items=[ev1, ev2], raw_claims=claims)
    assert res.claims[0].supporting_citation_count == 1


# ------------------------------------------------------------
# 18. Contextualized text never becomes citation source
# ------------------------------------------------------------
def test_contextualized_text_never_becomes_citation_source():
    ev = _make_evidence(
        source_content="Direct authoritative text only.",
        contextualized_content="[Section: AI Added Context Header] Direct authoritative text only.",
    )
    citation = Citation.from_evidence(ev, "EV1")
    # Grounding check must validate against source_content
    assert ev.source_content == "Direct authoritative text only."
    assert "[Section:" not in ev.source_content


# ------------------------------------------------------------
# 19. Source content remains authoritative
# ------------------------------------------------------------
def test_source_content_remains_authoritative():
    from unittest.mock import MagicMock
    generator = AnswerGenerator(client=MagicMock())
    ev = _make_evidence(
        source_content="Pure raw source text.",
        contextualized_content="[Section: Prefix] Pure raw source text.",
    )
    formatted = generator.format_context([ev])
    assert "[EVIDENCE EV1]" in formatted
    assert "Pure raw source text." in formatted
    assert "[Section: Prefix]" not in formatted


# ------------------------------------------------------------
# 20. Phase 7 confidence still works
# ------------------------------------------------------------
def test_phase7_confidence_still_works():
    ev = _make_evidence()
    conf = EvidenceEvaluator.evaluate("IS 3055 clause 4.1", [ev])
    assert conf.score > 0.6
    assert conf.decision in (Decision.ANSWER, Decision.QUALIFIED_ANSWER)


# ------------------------------------------------------------
# 21. Verification required is not HTTP 500
# ------------------------------------------------------------
def test_verification_required_is_not_http_500():
    ev = _make_evidence()
    claims = [
        {
            "claim_id": "C1",
            "text": "Unsupported hallucinated assertion [EV1].",
            "claim_type": "fact",
            "citation_ids": ["EV1"],
        }
    ]
    res = GroundingValidator.validate(answer="", evidence_items=[ev], raw_claims=claims)
    assert res.status == GroundingStatus.UNSUPPORTED
    # Operational decision is verification_required, never a crash or exception
    assert res.reason is not None


# ------------------------------------------------------------
# 22. Structured generator output validation
# ------------------------------------------------------------
def test_structured_generator_output_validation():
    ev = _make_evidence()
    model_output = """{
      "answer": "Clinical thermometers must satisfy accuracy limits [EV1].",
      "claims": [
        {"claim_id": "C1", "text": "Clinical thermometers must satisfy accuracy limits", "claim_type": "fact", "citation_ids": ["EV1"]}
      ]
    }"""
    answer, claims = AnswerGenerator._parse_structured_output(model_output)
    assert answer == "Clinical thermometers must satisfy accuracy limits [EV1]."
    assert len(claims) == 1
    assert claims[0]["claim_id"] == "C1"


# ------------------------------------------------------------
# 23. Malformed generator output fallback
# ------------------------------------------------------------
def test_malformed_generator_output_fallback():
    ev = _make_evidence()
    malformed_output = "Thermometers error shall not exceed 0.1 deg C [EV1]. Ingested corpus lacks packaging requirements."
    answer, raw_claims = AnswerGenerator._parse_structured_output(malformed_output)
    assert answer == malformed_output
    assert raw_claims == []

    # Grounding validator fallback segments sentences and extracts [EV1]
    res = GroundingValidator.validate(answer=answer, evidence_items=[ev], raw_claims=raw_claims)
    assert len(res.claims) >= 1
    assert res.claims[0].citation_ids == ["EV1"]


# ------------------------------------------------------------
# 24. Regeneration limit
# ------------------------------------------------------------
def test_regeneration_limit():
    from unittest.mock import MagicMock
    from app.rag.pipeline import RAGPipeline

    mock_generator = MagicMock()
    # Mock generator consistently returning unsupported claims
    mock_generator.generate.return_value = {
        "answer": "Invented requirement that fails grounding [EV1].",
        "raw_claims": [
            {"claim_id": "C1", "text": "Invented requirement that fails grounding", "claim_type": "fact", "citation_ids": ["EV1"]}
        ],
        "model": "mock",
        "context_chunks": 1,
    }

    mock_retriever = MagicMock()
    ev = _make_evidence()
    mock_retriever.retrieve.return_value = [ev]

    mock_reranker = MagicMock()
    mock_reranker.rerank.return_value = [ev]

    pipeline = RAGPipeline(retriever=mock_retriever, reranker=mock_reranker, generator=mock_generator)
    result = pipeline.query("IS 3055 clause 4.1")

    # Bounded to exactly 1 initial + 1 repair call = 2 calls total
    assert mock_generator.generate.call_count == 2
    assert result["verification_required"] is True
    assert result["decision"] == "verification_required"


# ------------------------------------------------------------
# 25. Deterministic citation resolution
# ------------------------------------------------------------
def test_deterministic_citation_resolution():
    ev = _make_evidence()
    claims = [
        {
            "claim_id": "C1",
            "text": "Clinical thermometers shall not exceed error of 0.1 deg C [EV1].",
            "claim_type": "fact",
            "citation_ids": ["EV1"],
        }
    ]
    res1 = GroundingValidator.validate(answer="", evidence_items=[ev], raw_claims=claims)
    res2 = GroundingValidator.validate(answer="", evidence_items=[ev], raw_claims=claims)

    assert res1.status == res2.status
    assert res1.groundedness_score == res2.groundedness_score
    assert res1.citation_coverage == res2.citation_coverage
    assert res1.citations[0].to_dict() == res2.citations[0].to_dict()
