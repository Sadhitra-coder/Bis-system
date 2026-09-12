"""
tests/test_temporal.py

Section 22: Failure-First Unit Tests for BIS Temporal, Version, and Amendment Intelligence.
Covers all 30 mandatory test cases.
"""

import os
os.environ.setdefault("USE_TF", "0")

import time
import pytest
from pydantic import ValidationError

from app.confidence import ConfidenceLevel, Decision, EvidenceEvaluator
from app.evidence.models import EvidenceItem
from app.grounding.models import AnswerClaim, GroundingStatus, SupportStatus
from app.grounding.validator import GroundingValidator
from app.knowledge.models import Amendment, Clause, Standard, StandardStatus, StandardVersion
from app.knowledge.repository import KnowledgeRepository
from app.temporal.models import (
    AmendmentDetail,
    ClauseEvolution,
    ClauseEvolutionState,
    TemporalConflict,
    TemporalRelationship,
    TemporalRelationshipType,
    TemporalResolution,
    TemporalStatus,
    ValidityInterval,
    VersionTimeline,
    VersionTimelineEntry,
)
from app.temporal.resolver import (
    CurrentnessResolver,
    build_version_timeline,
    compare_clause_evolution,
    detect_temporal_conflicts,
    extract_temporal_relationships_from_text,
)


@pytest.fixture
def memory_repo():
    return KnowledgeRepository(":memory:")


# ---------------------------------------------------------------------------
# Test 1: Multiple versions coexist
# ---------------------------------------------------------------------------
def test_01_multiple_versions_coexist():
    v1 = StandardVersion(
        version_id="ver_2020",
        standard_id="std_3055",
        standard_year=2020,
        status=StandardStatus.PUBLISHED,
        document_id="doc_2020",
        created_at=100.0,
    )
    v2 = StandardVersion(
        version_id="ver_2024",
        standard_id="std_3055",
        standard_year=2024,
        status=StandardStatus.PUBLISHED,
        document_id="doc_2024",
        created_at=200.0,
    )
    timeline = build_version_timeline(None, [v1, v2], [])
    assert len(timeline.versions) == 2
    assert timeline.versions[0].version_id == "ver_2020"
    assert timeline.versions[1].version_id == "ver_2024"
    # Neither deleted or overwritten
    assert timeline.versions[0].standard_year == 2020
    assert timeline.versions[1].standard_year == 2024


# ---------------------------------------------------------------------------
# Test 2: Explicit supersession
# ---------------------------------------------------------------------------
def test_02_explicit_supersession():
    text = "This Indian Standard supersedes IS 3055 : 1999 upon official publication."
    rels = extract_temporal_relationships_from_text(text, source_document_id="doc_2024", source_entity_id="ver_2024")
    assert len(rels) >= 1
    sup = rels[0]
    assert sup.relationship_type == TemporalRelationshipType.SUPERSEDES
    assert "IS 3055" in sup.target_entity_id
    assert sup.statement_text is not None


# ---------------------------------------------------------------------------
# Test 3: Explicit withdrawal
# ---------------------------------------------------------------------------
def test_03_explicit_withdrawal():
    text = "IS 1234 stands withdrawn w.e.f. 15-08-2024."
    rels = extract_temporal_relationships_from_text(text, source_document_id="doc_gazette", source_entity_id="std_1234")
    assert len(rels) >= 1
    wd = rels[0]
    assert wd.relationship_type == TemporalRelationshipType.WITHDRAWS
    assert wd.effective_date == "15-08-2024"


# ---------------------------------------------------------------------------
# Test 4: Explicit amendment
# ---------------------------------------------------------------------------
def test_04_explicit_amendment():
    amd = Amendment(
        amendment_id="amd_1",
        standard_id="std_3055",
        version_id="ver_2024",
        amendment_number="1",
        title="Amendment No. 1 to IS 3055",
        source_document_id="doc_amd1",
        status=StandardStatus.EFFECTIVE,
        created_at=time.time(),
    )
    v2024 = StandardVersion(
        version_id="ver_2024",
        standard_id="std_3055",
        standard_year=2024,
        status=StandardStatus.PUBLISHED,
        document_id="doc_2024",
        created_at=time.time(),
    )
    timeline = build_version_timeline(None, [v2024], [amd])
    assert len(timeline.amendments) == 1
    assert timeline.versions[0].amendments[0].amendment_number == "1"


# ---------------------------------------------------------------------------
# Test 5: Effective date extraction
# ---------------------------------------------------------------------------
def test_05_effective_date_extraction():
    text = "The provisions of this standard are effective from 01-07-2024."
    rels = extract_temporal_relationships_from_text(text, source_document_id="doc_2024")
    assert len(rels) >= 1
    eff = next(r for r in rels if r.relationship_type == TemporalRelationshipType.EFFECTIVE_FROM)
    assert eff.effective_date == "01-07-2024"


# ---------------------------------------------------------------------------
# Test 6: Publication vs effective distinction
# ---------------------------------------------------------------------------
def test_06_publication_vs_effective_distinction():
    v = StandardVersion(
        version_id="ver_2024",
        standard_id="std_3055",
        publication_date="2024-01-15",
        effective_date="2024-07-01",
        document_id="doc_2024",
        created_at=time.time(),
    )
    assert v.publication_date != v.effective_date
    timeline = build_version_timeline(None, [v], [])
    entry = timeline.versions[0]
    assert entry.publication_date == "2024-01-15"
    assert entry.effective_date == "2024-07-01"


# ---------------------------------------------------------------------------
# Test 7: Currentness supported by evidence
# ---------------------------------------------------------------------------
def test_07_currentness_supported_by_evidence():
    v2020 = StandardVersion(version_id="ver_2020", standard_id="std_3055", standard_year=2020, document_id="doc_2020", created_at=100.0)
    v2024 = StandardVersion(version_id="ver_2024", standard_id="std_3055", standard_year=2024, status=StandardStatus.EFFECTIVE, document_id="doc_2024", created_at=200.0)
    sup_rel = TemporalRelationship(
        relationship_id="rel_1",
        source_entity_id="ver_2024",
        target_entity_id="ver_2020",
        relationship_type=TemporalRelationshipType.SUPERSEDES,
        source_document_id="doc_2024",
        statement_text="supersedes IS 3055 : 2020",
    )
    res = CurrentnessResolver.resolve(
        standard=None,
        versions=[v2020, v2024],
        amendments=[],
        relationships=[sup_rel],
    )
    assert res.status == TemporalStatus.CURRENT_SUPPORTED
    assert res.requires_verification is False
    assert res.resolved_version_id == "ver_2024"


# ---------------------------------------------------------------------------
# Test 8: Currentness unknown
# ---------------------------------------------------------------------------
def test_08_currentness_unknown():
    res = CurrentnessResolver.resolve(standard=None, versions=[], amendments=[], relationships=[])
    assert res.status == TemporalStatus.TEMPORALLY_UNCERTAIN
    assert res.requires_verification is True


# ---------------------------------------------------------------------------
# Test 9: Newest version without supersession evidence -> NOT automatically current
# ---------------------------------------------------------------------------
def test_09_newest_version_without_supersession_not_automatically_current():
    v2020 = StandardVersion(version_id="ver_2020", standard_id="std_3055", standard_year=2020, status=StandardStatus.PUBLISHED, document_id="doc_2020", created_at=100.0)
    v2024 = StandardVersion(version_id="ver_2024", standard_id="std_3055", standard_year=2024, status=StandardStatus.PUBLISHED, document_id="doc_2024", created_at=200.0)
    res = CurrentnessResolver.resolve(
        standard=None,
        versions=[v2020, v2024],
        amendments=[],
        relationships=[],  # Zero supersession evidence
    )
    assert res.status == TemporalStatus.TEMPORALLY_UNCERTAIN
    assert res.requires_verification is True
    assert len(res.conflicts) > 0  # Competing versions conflict detected


# ---------------------------------------------------------------------------
# Test 10: Historical version query
# ---------------------------------------------------------------------------
def test_10_historical_version_query():
    v2020 = StandardVersion(version_id="ver_2020", standard_id="std_3055", standard_year=2020, document_id="doc_2020", created_at=100.0)
    v2024 = StandardVersion(version_id="ver_2024", standard_id="std_3055", standard_year=2024, document_id="doc_2024", created_at=200.0)
    res = CurrentnessResolver.resolve(
        standard=None,
        versions=[v2020, v2024],
        amendments=[],
        relationships=[],
        requested_year=2020,
    )
    assert res.status == TemporalStatus.HISTORICAL
    assert res.resolved_version_id == "ver_2020"
    assert res.requires_verification is False


# ---------------------------------------------------------------------------
# Test 11: Exact amendment query
# ---------------------------------------------------------------------------
def test_11_exact_amendment_query():
    amd = Amendment(
        amendment_id="amd_2",
        standard_id="std_3055",
        version_id="ver_2024",
        amendment_number="2",
        source_document_id="doc_amd2",
        created_at=time.time(),
    )
    res = CurrentnessResolver.resolve(
        standard=None,
        versions=[],
        amendments=[amd],
        relationships=[],
        requested_amendment="2",
    )
    assert res.status == TemporalStatus.AMENDED
    assert res.requires_verification is False


# ---------------------------------------------------------------------------
# Test 12: Version query
# ---------------------------------------------------------------------------
def test_12_version_query():
    v = StandardVersion(version_id="ver_1999", standard_id="std_3055", standard_year=1999, document_id="doc_1999", created_at=100.0)
    res = CurrentnessResolver.resolve(standard=None, versions=[v], amendments=[], relationships=[], requested_year=1999)
    assert res.status == TemporalStatus.HISTORICAL
    assert res.resolved_version_id == "ver_1999"


# ---------------------------------------------------------------------------
# Test 13: Current/latest query without supersession evidence -> verification required
# ---------------------------------------------------------------------------
def test_13_current_latest_query_uncertain():
    v1 = StandardVersion(version_id="ver_2022", standard_id="std_3055", standard_year=2022, status=StandardStatus.PUBLISHED, document_id="d1", created_at=1.0)
    v2 = StandardVersion(version_id="ver_2024", standard_id="std_3055", standard_year=2024, status=StandardStatus.PUBLISHED, document_id="d2", created_at=2.0)
    res = CurrentnessResolver.resolve(
        standard=None,
        versions=[v1, v2],
        amendments=[],
        relationships=[],
        query_temporal_intent="current",
    )
    assert res.status == TemporalStatus.TEMPORALLY_UNCERTAIN
    assert res.requires_verification is True


# ---------------------------------------------------------------------------
# Test 14: Conflicting versions detected
# ---------------------------------------------------------------------------
def test_14_conflicting_versions():
    v1 = StandardVersion(version_id="ver_a", standard_id="std_1", standard_year=2021, status=StandardStatus.PUBLISHED, document_id="da", created_at=1.0)
    v2 = StandardVersion(version_id="ver_b", standard_id="std_1", standard_year=2023, status=StandardStatus.PUBLISHED, document_id="db", created_at=2.0)
    conflicts = detect_temporal_conflicts("std_1", [v1, v2], [], [])
    assert len(conflicts) >= 1
    assert conflicts[0].conflict_type == "competing_versions"
    assert "ver_a" in conflicts[0].entities_involved
    assert "ver_b" in conflicts[0].entities_involved


# ---------------------------------------------------------------------------
# Test 15: Conflicting amendments
# ---------------------------------------------------------------------------
def test_15_conflicting_amendments():
    a1 = Amendment(amendment_id="amd_1", standard_id="std_1", amendment_number="1", title="Amends Clause 4.1 for calibration", source_document_id="d1", created_at=1.0)
    a2 = Amendment(amendment_id="amd_2", standard_id="std_1", amendment_number="2", title="Amends Clause 4.1 for tolerances", source_document_id="d2", created_at=2.0)
    conflicts = detect_temporal_conflicts("std_1", [], [a1, a2], [])
    assert len(conflicts) >= 1
    assert conflicts[0].conflict_type == "conflicting_amendments"
    assert "4.1" in conflicts[0].clauses_involved


# ---------------------------------------------------------------------------
# Test 16: Clause evolution comparator
# ---------------------------------------------------------------------------
def test_16_clause_evolution_modified():
    c_base = Clause(clause_id="c_4_1_v1", standard_id="std_1", version_id="v1", clause_number="4.1", clause_title="Calibration Accuracy", level=2, document_id="d1", heading_path="4.1 Calibration Accuracy", created_at=1.0)
    c_target = Clause(clause_id="c_4_1_v2", standard_id="std_1", version_id="v2", clause_number="4.1", clause_title="Calibration and Traceability", level=2, document_id="d2", heading_path="4.1 Calibration and Traceability", created_at=2.0)
    evo = compare_clause_evolution(c_base, c_target)
    assert evo.state == ClauseEvolutionState.MODIFIED
    assert "modified" in evo.notes.lower()


# ---------------------------------------------------------------------------
# Test 17: Removed clause
# ---------------------------------------------------------------------------
def test_17_removed_clause():
    c_base = Clause(clause_id="c_5_v1", standard_id="std_1", version_id="v1", clause_number="5", clause_title="Obsolete Requirement", level=1, document_id="d1", heading_path="5 Obsolete Requirement", created_at=1.0)
    evo = compare_clause_evolution(c_base, None)
    assert evo.state == ClauseEvolutionState.REMOVED


# ---------------------------------------------------------------------------
# Test 18: Added clause
# ---------------------------------------------------------------------------
def test_18_added_clause():
    c_target = Clause(clause_id="c_6_v2", standard_id="std_1", version_id="v2", clause_number="6", clause_title="New Digital Display", level=1, document_id="d2", heading_path="6 New Digital Display", created_at=2.0)
    evo = compare_clause_evolution(None, c_target)
    assert evo.state == ClauseEvolutionState.ADDED


# ---------------------------------------------------------------------------
# Test 19: Unchanged clause
# ---------------------------------------------------------------------------
def test_19_unchanged_clause():
    c1 = Clause(clause_id="c_4_1_a", standard_id="std_1", version_id="v1", clause_number="4.1", clause_title="Dimensions", level=2, document_id="d1", heading_path="4.1 Dimensions", created_at=1.0)
    c2 = Clause(clause_id="c_4_1_b", standard_id="std_1", version_id="v2", clause_number="4.1", clause_title="Dimensions", level=2, document_id="d2", heading_path="4.1 Dimensions", created_at=2.0)
    evo = compare_clause_evolution(c1, c2)
    assert evo.state == ClauseEvolutionState.UNCHANGED


# ---------------------------------------------------------------------------
# Test 20: Temporal uncertainty lowers confidence
# ---------------------------------------------------------------------------
def test_20_temporal_uncertainty_lowers_confidence():
    ev = EvidenceItem(chunk_id="ch_1", document_id="doc_1", content="Clause 4.1 requires accuracy +/- 0.1 C.", standard_number="IS 3055", clause_id="4.1", reranker_score=1.5)
    temporal_res = TemporalResolution(
        status=TemporalStatus.TEMPORALLY_UNCERTAIN,
        requires_verification=True,
        reason="Multiple versions exist without supersession evidence.",
    )
    conf = EvidenceEvaluator.evaluate(
        query="what is the current requirement for Clause 4.1 in IS 3055?",
        evidence_items=[ev],
        temporal_resolution=temporal_res,
    )
    assert conf.decision == Decision.VERIFICATION_REQUIRED
    assert conf.verification_required is True
    assert any("Temporal uncertainty" in r for r in conf.reasons)


# ---------------------------------------------------------------------------
# Test 21: Currentness claim requires temporal evidence
# ---------------------------------------------------------------------------
def test_21_currentness_claim_requires_temporal_evidence():
    ev = EvidenceItem(
        chunk_id="ch_1",
        document_id="doc_1",
        content="Published in 2024. Testing sample size is 10 units.",
        standard_number="IS 3055",
        metadata={"publication_date": "2024-01-01"},  # Only publication date, not current
    )
    claim = AnswerClaim(
        claim_id="C1",
        text="The 2024 edition is the current edition of IS 3055.",
        claim_type="fact",
        citation_ids=["EV1"],
    )
    val = GroundingValidator.validate(
        answer="The 2024 edition is the current edition of IS 3055. [EV1]",
        evidence_items=[ev],
        raw_claims=[claim.to_dict()],
    )
    assert val.status == GroundingStatus.UNSUPPORTED
    assert any("unsupported_currentness_claim" in iss for iss in val.claims[0].issues)


# ---------------------------------------------------------------------------
# Test 22: Grounding validation catches unsupported currentness claim
# ---------------------------------------------------------------------------
def test_22_grounding_catches_unsupported_currentness():
    ev = EvidenceItem(chunk_id="c1", document_id="d1", content="Clause 4.1 applies to thermometers.", standard_number="IS 3055")
    claim = AnswerClaim(claim_id="C1", text="The current requirement is Clause 4.1.", citation_ids=["EV1"])
    val = GroundingValidator.validate(answer="The current requirement is Clause 4.1. [EV1]", evidence_items=[ev], raw_claims=[claim.to_dict()])
    assert val.claims[0].support_status == SupportStatus.UNSUPPORTED
    assert any("unsupported_currentness_claim" in iss for iss in val.claims[0].issues)


# ---------------------------------------------------------------------------
# Test 23: No date fabrication
# ---------------------------------------------------------------------------
def test_23_no_date_fabrication():
    interval = ValidityInterval(effective_from="2024-01-01", effective_until=None)
    assert interval.is_open_ended is True
    assert interval.effective_until is None  # Never manufactured


# ---------------------------------------------------------------------------
# Test 24: Provenance preservation
# ---------------------------------------------------------------------------
def test_24_provenance_preservation():
    rel = TemporalRelationship(
        relationship_id="r1",
        source_entity_id="ver_2024",
        target_entity_id="ver_2020",
        relationship_type=TemporalRelationshipType.SUPERSEDES,
        source_document_id="doc_2024",
        source_chunk_ids=["chunk_123"],
        source_url="https://bis.gov.in/std/3055",
    )
    d = rel.to_dict()
    assert d["source_document_id"] == "doc_2024"
    assert d["source_chunk_ids"] == ["chunk_123"]
    assert d["source_url"] == "https://bis.gov.in/std/3055"


# ---------------------------------------------------------------------------
# Test 25: Deterministic temporal resolution
# ---------------------------------------------------------------------------
def test_25_deterministic_temporal_resolution():
    v1 = StandardVersion(version_id="v1", standard_id="s1", standard_year=2020, document_id="d1", created_at=1.0)
    v2 = StandardVersion(version_id="v2", standard_id="s1", standard_year=2024, document_id="d2", created_at=2.0)
    r1 = CurrentnessResolver.resolve(None, [v1, v2], [], [])
    r2 = CurrentnessResolver.resolve(None, [v1, v2], [], [])
    assert r1.status == r2.status
    assert r1.requires_verification == r2.requires_verification
    assert r1.reason == r2.reason


# ---------------------------------------------------------------------------
# Test 26: Duplicate temporal relationships
# ---------------------------------------------------------------------------
def test_26_duplicate_temporal_relationships(memory_repo):
    rel = TemporalRelationship(
        relationship_id="rel_dup_1",
        source_entity_id="v2",
        target_entity_id="v1",
        relationship_type=TemporalRelationshipType.SUPERSEDES,
        source_document_id="doc_2",
    )
    memory_repo.save_temporal_relationship(rel)
    memory_repo.save_temporal_relationship(rel)  # Re-insert should not fail or duplicate
    rels = memory_repo.get_temporal_relationships("v2")
    assert len(rels) == 1


# ---------------------------------------------------------------------------
# Test 27: Orphan amendment
# ---------------------------------------------------------------------------
def test_27_orphan_amendment():
    amd = Amendment(
        amendment_id="amd_orphan",
        standard_id="std_unknown_orphan",
        amendment_number="1",
        source_document_id="doc_orphan",
        created_at=time.time(),
    )
    timeline = build_version_timeline(None, [], [amd])
    assert len(timeline.versions) == 0
    assert len(timeline.amendments) == 1


# ---------------------------------------------------------------------------
# Test 28: Amendment for wrong version
# ---------------------------------------------------------------------------
def test_28_amendment_for_wrong_version():
    v2020 = StandardVersion(version_id="ver_2020", standard_id="std_1", standard_year=2020, document_id="d1", created_at=1.0)
    amd_for_2024 = Amendment(
        amendment_id="amd_1",
        standard_id="std_1",
        version_id="ver_2024",  # targets 2024, but only 2020 is ingested
        amendment_number="1",
        source_document_id="da",
        created_at=1.0,
    )
    timeline = build_version_timeline(None, [v2020], [amd_for_2024])
    # Should not attach to ver_2020
    assert len(timeline.versions[0].amendments) == 0


# ---------------------------------------------------------------------------
# Test 29: Cross-version clause mismatch
# ---------------------------------------------------------------------------
def test_29_cross_version_clause_mismatch():
    c_old = Clause(clause_id="c_4_1_old", standard_id="std_1", version_id="ver_2020", clause_number="4.1", clause_title="Scale Markings", level=2, document_id="d1", heading_path="4.1 Scale Markings", created_at=1.0)
    c_new = Clause(clause_id="c_4_1_new", standard_id="std_1", version_id="ver_2024", clause_number="4.1", clause_title="Digital Scale Units", level=2, document_id="d2", heading_path="4.1 Digital Scale Units", created_at=2.0)
    evo = compare_clause_evolution(c_old, c_new)
    assert evo.state == ClauseEvolutionState.MODIFIED
    assert evo.base_version_id == "ver_2020"
    assert evo.target_version_id == "ver_2024"


# ---------------------------------------------------------------------------
# Test 30: Non-standard document referencing a standard
# ---------------------------------------------------------------------------
def test_30_non_standard_document_referencing_standard():
    ev = EvidenceItem(
        chunk_id="ch_manual",
        document_id="manual_doc",
        content="This thermometer satisfies IS 3055 requirements.",
        standard_number="IS 3055",
        standard_relation="reference",  # Merely references standard
    )
    # Resolver should not declare manual as standard version
    res = CurrentnessResolver.resolve(None, [], [], [], evidence_items=[ev])
    assert res.status == TemporalStatus.TEMPORALLY_UNCERTAIN
    assert res.requires_verification is True
