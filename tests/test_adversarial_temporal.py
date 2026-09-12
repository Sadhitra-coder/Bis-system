"""
tests/test_adversarial_temporal.py

Section 23: Adversarial Temporal Tests for BIS Compliance Intelligence.
Verifies rejection of ungrounded currentness claims, fabricated supersessions,
and unverified amendment consolidations.
"""

import os
os.environ.setdefault("USE_TF", "0")

import pytest
from app.evidence.models import EvidenceItem
from app.grounding.models import AnswerClaim, GroundingStatus, SupportStatus
from app.grounding.validator import GroundingValidator
from app.temporal.models import (
    TemporalRelationship,
    TemporalRelationshipType,
    TemporalResolution,
    TemporalStatus,
)
from app.temporal.resolver import CurrentnessResolver
from app.knowledge.models import StandardVersion, StandardStatus


# ---------------------------------------------------------------------------
# Adversarial Attack A:
# "The 2024 edition is the current edition." when evidence only shows publication date.
# ---------------------------------------------------------------------------
def test_adversarial_a_publication_date_not_current():
    ev = EvidenceItem(
        chunk_id="ev_2024",
        document_id="doc_2024",
        content="Published by Bureau of Indian Standards in January 2024. Clinical thermometer specs.",
        standard_number="IS 3055",
        standard_year=2024,
        metadata={"publication_date": "2024-01-01", "is_current": None, "status": "published"},
    )
    claim = AnswerClaim(
        claim_id="C_adv_A",
        text="The 2024 edition is the current edition of IS 3055.",
        claim_type="fact",
        citation_ids=["EV1"],
    )
    val = GroundingValidator.validate(
        answer="The 2024 edition is the current edition of IS 3055. [EV1]",
        evidence_items=[ev],
        raw_claims=[claim.to_dict()],
    )
    # Must strictly reject currentness
    assert val.status == GroundingStatus.UNSUPPORTED
    assert val.claims[0].support_status == SupportStatus.UNSUPPORTED
    assert any("unsupported_currentness_claim" in iss for iss in val.claims[0].issues)


# ---------------------------------------------------------------------------
# Adversarial Attack B:
# "This edition supersedes the 2022 edition." without supersession evidence.
# ---------------------------------------------------------------------------
def test_adversarial_b_fabricated_supersession():
    ev = EvidenceItem(
        chunk_id="ev_text",
        document_id="doc_text",
        content="This second edition specifies performance requirements for medical devices.",
        standard_number="IS 3055",
        metadata={},
    )
    claim = AnswerClaim(
        claim_id="C_adv_B",
        text="This edition supersedes the 2022 edition of IS 3055.",
        claim_type="fact",
        citation_ids=["EV1"],
    )
    val = GroundingValidator.validate(
        answer="This edition supersedes the 2022 edition of IS 3055. [EV1]",
        evidence_items=[ev],
        raw_claims=[claim.to_dict()],
    )
    assert val.status == GroundingStatus.UNSUPPORTED
    assert val.claims[0].support_status == SupportStatus.UNSUPPORTED
    assert any("unsupported_supersession_claim" in iss for iss in val.claims[0].issues)


# ---------------------------------------------------------------------------
# Adversarial Attack C:
# "Amendment 2 replaces Clause 4.1." when evidence only references amendment generally.
# ---------------------------------------------------------------------------
def test_adversarial_c_unsupported_amendment_clause_replacement():
    ev = EvidenceItem(
        chunk_id="ev_amd2",
        document_id="doc_amd2",
        content="Amendment 2 issued by the Petroleum, Coal and Related Products Division.",
        standard_number="IS 3055",
        amendment_number="2",
        metadata={"amendment_number": "2"},
    )
    claim = AnswerClaim(
        claim_id="C_adv_C",
        text="Amendment 2 replaces Clause 4.1 for clinical thermometers.",
        claim_type="fact",
        citation_ids=["EV1"],
    )
    val = GroundingValidator.validate(
        answer="Amendment 2 replaces Clause 4.1 for clinical thermometers. [EV1]",
        evidence_items=[ev],
        raw_claims=[claim.to_dict()],
    )
    assert val.status == GroundingStatus.UNSUPPORTED
    assert val.claims[0].support_status == SupportStatus.UNSUPPORTED
    assert any("unsupported_amendment_clause_modification" in iss for iss in val.claims[0].issues)


# ---------------------------------------------------------------------------
# Adversarial Attack D:
# "Requirement X is currently mandatory." when only historical evidence exists.
# ---------------------------------------------------------------------------
def test_adversarial_d_historical_evidence_claimed_as_currently_mandatory():
    ev = EvidenceItem(
        chunk_id="ev_hist",
        document_id="doc_1999",
        content="Clause 4.1 requires mercury bulb volume of 0.2 ml. Indian Standard IS 3055 : 1999.",
        standard_number="IS 3055",
        standard_year=1999,
        metadata={"status": "superseded", "is_current": False},
    )
    claim = AnswerClaim(
        claim_id="C_adv_D",
        text="The mercury bulb volume requirement of 0.2 ml is currently mandatory under IS 3055.",
        claim_type="fact",
        citation_ids=["EV1"],
    )
    val = GroundingValidator.validate(
        answer="The mercury bulb volume requirement of 0.2 ml is currently mandatory under IS 3055. [EV1]",
        evidence_items=[ev],
        raw_claims=[claim.to_dict()],
    )
    assert val.status == GroundingStatus.UNSUPPORTED
    assert val.claims[0].support_status == SupportStatus.UNSUPPORTED
    assert any("unsupported_currentness_claim" in iss for iss in val.claims[0].issues)


# ---------------------------------------------------------------------------
# Adversarial Attack E:
# "Latest version" query where highest year is not explicitly current.
# ---------------------------------------------------------------------------
def test_adversarial_e_latest_version_query_highest_year_not_current():
    v1 = StandardVersion(version_id="ver_2020", standard_id="std_3055", standard_year=2020, status=StandardStatus.PUBLISHED, document_id="d1", created_at=1.0)
    v2 = StandardVersion(version_id="ver_2024", standard_id="std_3055", standard_year=2024, status=StandardStatus.PUBLISHED, document_id="d2", created_at=2.0)

    # User queries: "latest version of IS 3055"
    res = CurrentnessResolver.resolve(
        standard=None,
        versions=[v1, v2],
        amendments=[],
        relationships=[],  # No supersession evidence
        query_temporal_intent="current",
    )
    # Must NOT select 2024 as current! Must return TEMPORALLY_UNCERTAIN and requires_verification
    assert res.status == TemporalStatus.TEMPORALLY_UNCERTAIN
    assert res.requires_verification is True
    assert res.resolved_version_id is None
