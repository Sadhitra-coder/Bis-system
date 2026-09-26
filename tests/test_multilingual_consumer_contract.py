"""
tests/test_multilingual_consumer_contract.py

Comprehensive tests for:
1. Multilingual Interaction (PRD R8) - Hindi Grounding, Devanagari numerals, sentence splitting.
2. Post-Translation Integrity Verification Layer (TranslationIntegrityVerifier).
3. Consumer Mode (PRD R5) - Clause suppression, BIS Care pointer, visual hierarchy.
4. Runtime API Contract - Field naming, enum casing, schema parity.
"""

import re
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
from app.rag.translation_verifier import TranslationIntegrityVerifier
from app.confidence.models import Decision, ConfidenceLevel, QueryState
from app.temporal.models import TemporalStatus
from app.models import QueryResponse
from app.rag.source_format import SourceIdentity, source_to_dict


def _make_evidence_item(
    chunk_id: str = "c1",
    standard_number: str = "IS 1293",
    clause_id: str = "4.1",
    clause_title: str = "Electrical Safety & Ratings",
    content: str = None,
    page_start: int = 5,
) -> EvidenceItem:
    text = content or (
        "IS 1293 specifies domestic plugs and socket-outlets rated at 16 A, 250 V, 50 Hz. "
        "Plugs shall be constructed to prevent accidental contact with live parts."
    )
    return EvidenceItem(
        chunk_id=chunk_id,
        document_id="doc_1293",
        source_hash="hash_1293",
        source_file="standards/IS_1293_2019.pdf",
        page_start=page_start,
        page_end=page_start,
        content=text,
        source_content=text,
        standard_number=standard_number,
        standard_title="Plugs and Socket-Outlets Specification",
        edition_or_version="Fourth Edition",
        clause_id=clause_id,
        clause_title=clause_title,
        standard_year=2019,
        reranker_score=4.0,
        authority="BIS",
    )


# ============================================================
# 1. HINDI GROUNDING & UNBOUND LOCAL ERROR REGRESSION TEST
# ============================================================

def test_hindi_claim_validation_no_unbound_local_error():
    """Validating a Hindi claim must NOT crash with UnboundLocalError."""
    ev = _make_evidence_item()
    claims = [
        {
            "claim_id": "C1",
            "text": "घरेलू प्लग 16 A और 250 V के लिए रेट किए गए हैं [EV1]।",
            "claim_type": "fact",
            "citation_ids": ["EV1"],
        }
    ]
    res = GroundingValidator.validate(answer="", evidence_items=[ev], raw_claims=claims)
    assert res.status == GroundingStatus.FULLY_GROUNDED
    assert len(res.claims) == 1
    assert res.claims[0].support_status == SupportStatus.SUPPORTED
    assert res.claims[0].issues == []


def test_devanagari_numerals_translated_and_validated():
    """Devanagari digits (१६ A, २५० V) must be parsed and matched against English evidence."""
    ev = _make_evidence_item()
    # १६ = 16, २५० = 250 in Devanagari numerals
    claims = [
        {
            "claim_id": "C1",
            "text": "प्लग १६ A और २५० V रेटिंग के अनुरूप होना चाहिए [EV1]।",
            "claim_type": "fact",
            "citation_ids": ["EV1"],
        }
    ]
    res = GroundingValidator.validate(answer="", evidence_items=[ev], raw_claims=claims)
    assert res.status == GroundingStatus.FULLY_GROUNDED
    assert res.claims[0].support_status == SupportStatus.SUPPORTED


def test_hindi_claim_with_hallucinated_numbers_is_unsupported():
    """A Hindi claim with incorrect numbers must be flagged as unsupported."""
    ev = _make_evidence_item()
    claims = [
        {
            "claim_id": "C1",
            "text": "प्लग 99 A और 999 V रेटिंग के अनुरूप होना चाहिए [EV1]।",
            "claim_type": "fact",
            "citation_ids": ["EV1"],
        }
    ]
    res = GroundingValidator.validate(answer="", evidence_items=[ev], raw_claims=claims)
    assert res.status == GroundingStatus.UNSUPPORTED
    assert any("unsupported_numerical_value" in iss for iss in res.claims[0].issues)


def test_hindi_sentence_splitting_with_purna_viram():
    """Hindi sentence terminators (। and ॥) must correctly delimit claims."""
    hindi_text = (
        "**IS 1293 प्लग आवश्यकताओं को निर्दिष्ट करता है।**\n\n"
        "पहला वाक्य यह है [EV1]। दूसरा वाक्य यह है [EV1]॥\n\n"
        "Sources: [EV1]"
    )
    claims = GroundingValidator.extract_claims_from_text(hindi_text)
    texts = [c.text for c in claims]
    assert any("पहला वाक्य यह है" in t for t in texts)
    assert any("दूसरा वाक्य यह है" in t for t in texts)


# ============================================================
# 2. POST-TRANSLATION INTEGRITY VERIFICATION TESTS
# ============================================================

def test_translation_verifier_valid():
    """A faithful Hindi translation must pass all integrity checks."""
    en_answer = (
        "**IS 1293 specifies household plug requirements.**\n\n"
        "IS 1293 specifies domestic plugs rated at 16 A, 250 V.\n\n"
        "- Protection from electric shock\n\n"
        "Sources: [EV1]"
    )
    hi_answer = (
        "**IS 1293 घरेलू प्लग आवश्यकताओं को निर्दिष्ट करता है।**\n\n"
        "IS 1293 16 A, 250 V रेटिंग वाले घरेलू प्लग को निर्दिष्ट करता है।\n\n"
        "- बिजली के झटके से सुरक्षा\n\n"
        "Sources: [EV1]"
    )
    res = TranslationIntegrityVerifier.verify_and_repair(en_answer, hi_answer)
    assert res.is_valid is True
    assert res.devanagari_ratio >= 0.20
    assert len(res.issues) == 0


def test_translation_verifier_catches_missing_standard_number():
    """Translation dropping the Indian Standard designation must fail integrity."""
    en_answer = "**IS 1293 specifies plug safety.**\n\nSources: [EV1]"
    hi_corrupt = "**प्लग सुरक्षा विनिर्देश।**\n\nSources: [EV1]"
    res = TranslationIntegrityVerifier.verify_and_repair(en_answer, hi_corrupt)
    assert res.is_valid is False
    assert any("missing_standard_numbers" in iss for iss in res.issues)


def test_translation_verifier_catches_corrupted_numbers():
    """Translation corrupting numerical values must fail integrity."""
    en_answer = "**IS 1293 covers 16 A, 250 V plugs.**\n\nSources: [EV1]"
    hi_corrupt = "**IS 1293 में 5 A, 100 V प्लग शामिल हैं।**\n\nSources: [EV1]"
    res = TranslationIntegrityVerifier.verify_and_repair(en_answer, hi_corrupt)
    assert res.is_valid is False
    assert any("missing_numerical_values" in iss for iss in res.issues)


def test_translation_verifier_restores_dropped_citations():
    """Translation dropping the separated Sources line must be deterministically auto-repaired."""
    en_answer = "**IS 1293 specifies plug safety.**\n\nIS 1293 covers safety.\n\nSources: [EV1], [EV2]"
    hi_answer_no_cits = "**IS 1293 प्लग सुरक्षा को निर्दिष्ट करता है।**\n\nIS 1293 सुरक्षा को कवर करता है।"
    res = TranslationIntegrityVerifier.verify_and_repair(en_answer, hi_answer_no_cits)
    assert res.repaired_citations is True
    assert "[EV1]" in res.repaired_hindi_answer
    assert "[EV2]" in res.repaired_hindi_answer
    assert "Sources:" in res.repaired_hindi_answer


def test_translation_verifier_catches_lack_of_devanagari():
    """A response with no Devanagari script must fail integrity verification."""
    en_answer = "**IS 1293 specifies plug safety.**\n\nSources: [EV1]"
    hi_fake = "**IS 1293 specifies plug safety in English.**\n\nSources: [EV1]"
    res = TranslationIntegrityVerifier.verify_and_repair(en_answer, hi_fake)
    assert res.is_valid is False
    assert any("insufficient_devanagari_script" in iss for iss in res.issues)


# ============================================================
# 3. CONSUMER MODE (PRD R5) VERIFICATION TESTS
# ============================================================

def test_consumer_mode_strips_clause_leaks():
    """Consumer mode translations with leaked clause numbers must be sanitized."""
    en_ans = "**Plugs and sockets under IS 1293 ensure household electrical safety.**\n\nSources: [EV1]"
    hi_with_clause = (
        "**IS 1293 के तहत प्लग और सॉकेट सुरक्षा सुनिश्चित करते हैं।**\n\n"
        "Clause 4.1 के अनुसार परीक्षण आवश्यक है।\n\n"
        "Sources: [EV1]"
    )
    res = TranslationIntegrityVerifier.verify_and_repair(en_ans, hi_with_clause, audience="consumer")
    assert res.repaired_clause_leaks is True
    assert "Clause 4.1" not in res.repaired_hindi_answer


def test_consumer_mode_inserts_bis_care_pointer_if_missing():
    """Consumer mode translations must include the official BIS Care tip."""
    en_ans = "**Plugs and sockets under IS 1293.**\n\nSources: [EV1]"
    hi_ans = "**IS 1293 के तहत प्लग और सॉकेट।**\n\nSources: [EV1]"
    res = TranslationIntegrityVerifier.verify_and_repair(en_ans, hi_ans, audience="consumer")
    assert res.repaired_consumer_tip is True
    assert "बीआईएस केयर" in res.repaired_hindi_answer or "BIS Care" in res.repaired_hindi_answer


# ============================================================
# 4. RUNTIME API CONTRACT & SCHEMA TESTS
# ============================================================

def test_enum_casings_and_definitions():
    """Verify controlled vocabulary casing contracts."""
    # Decision must be lowercase
    assert Decision.ANSWER.value == "answer"
    assert Decision.QUALIFIED_ANSWER.value == "qualified_answer"
    assert Decision.VERIFICATION_REQUIRED.value == "verification_required"

    # ConfidenceLevel must be lowercase
    assert ConfidenceLevel.HIGH.value == "high"
    assert ConfidenceLevel.MEDIUM.value == "medium"
    assert ConfidenceLevel.LOW.value == "low"

    # QueryState must be UPPERCASE
    assert QueryState.ANSWERABLE.value == "ANSWERABLE"
    assert QueryState.INSUFFICIENT_EVIDENCE.value == "INSUFFICIENT_EVIDENCE"
    assert QueryState.VERIFICATION_REQUIRED.value == "VERIFICATION_REQUIRED"

    # GroundingStatus must be lowercase
    assert GroundingStatus.FULLY_GROUNDED.value == "fully_grounded"
    assert GroundingStatus.UNSUPPORTED.value == "unsupported"

    # TemporalStatus must be lowercase
    assert TemporalStatus.CURRENT_SUPPORTED.value == "current_supported"
    assert TemporalStatus.TEMPORALLY_UNCERTAIN.value == "temporally_uncertain"


def test_sources_contains_page_and_clause_aliases():
    """source_to_dict must provide page_number and clause_number aliases for frontend."""
    identity = SourceIdentity(
        document_id="doc1",
        standard_number="IS 1293",
        page_start=7,
        clause_id="4.1",
        standard_relation="identity",
    )
    s_dict = source_to_dict(identity)
    assert s_dict["page_start"] == 7
    assert s_dict["page_number"] == 7
    assert s_dict["clause_id"] == "4.1"
    assert s_dict["clause_number"] == "4.1"


def test_query_response_pydantic_schema_validation():
    """QueryResponse model must serialize and validate all canonical fields without error."""
    resp = QueryResponse(
        query="What are plug requirements under IS 1293?",
        answer="IS 1293 specifies plug requirements.",
        sources=[{"standard_number": "IS 1293", "page_number": 5, "clause_number": "4.1"}],
        retrieved_chunks=3,
        decision=Decision.ANSWER.value,
        query_state=QueryState.ANSWERABLE.value,
        confidence_level=ConfidenceLevel.HIGH.value,
        confidence_score=0.92,
        grounding_status=GroundingStatus.FULLY_GROUNDED.value,
        temporal_status=TemporalStatus.CURRENT_SUPPORTED.value,
        language="hi",
        claims=[{"claim_id": "C1", "text": "Claim 1", "citation_ids": ["EV1"]}],
    )
    d = resp.model_dump()
    assert d["decision"] == "answer"
    assert d["query_state"] == "ANSWERABLE"
    assert d["confidence_level"] == "high"
    assert d["grounding_status"] == "fully_grounded"
    assert d["temporal_status"] == "current_supported"
    assert d["language"] == "hi"
