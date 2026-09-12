"""
app/temporal/models.py

Canonical Domain Models for BIS Temporal, Version, and Amendment Intelligence.

Core principles:
  1. Detect and represent temporal relationships — NEVER guess legal currentness.
  2. Latest publication date != legally current.
  3. Highest year != active version.
  4. Missing evidence => TEMPORALLY_UNCERTAIN or VERIFICATION_REQUIRED.
  5. Distinguish published, effective, and withdrawn dates.
  6. Zero date fabrication — open-ended intervals remain unknown.
"""

from enum import Enum
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ============================================================
# CONTROLLED TEMPORAL VOCABULARIES
# ============================================================

class TemporalRelationshipType(str, Enum):
    """Controlled vocabulary for relationships between standards, versions, and amendments."""
    SUPERSEDES = "SUPERSEDES"
    SUPERSEDED_BY = "SUPERSEDED_BY"
    AMENDS = "AMENDS"
    AMENDED_BY = "AMENDED_BY"
    WITHDRAWS = "WITHDRAWS"
    WITHDRAWN_BY = "WITHDRAWN_BY"
    REVISES = "REVISES"
    REVISED_BY = "REVISED_BY"
    EFFECTIVE_FROM = "EFFECTIVE_FROM"
    WITHDRAWN_FROM = "WITHDRAWN_FROM"


class TemporalStatus(str, Enum):
    """
    Canonical temporal status vocabulary.
    Never silently map UNKNOWN or TEMPORALLY_UNCERTAIN to CURRENT_SUPPORTED.
    """
    CURRENT_SUPPORTED = "current_supported"
    HISTORICAL = "historical"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"
    AMENDED = "amended"
    TEMPORALLY_UNCERTAIN = "temporally_uncertain"
    UNKNOWN = "unknown"


class ClauseEvolutionState(str, Enum):
    """Comparison state between clause versions across editions or amendments."""
    UNCHANGED = "unchanged"
    MODIFIED = "modified"
    ADDED = "added"
    REMOVED = "removed"
    UNKNOWN = "unknown"


# ============================================================
# CANONICAL TEMPORAL ENTITIES
# ============================================================

class ValidityInterval(BaseModel):
    """
    Date validity interval supported by explicit source evidence.
    If effective_until is not known from withdrawal/supersession, it is None.
    Never manufacture end dates.
    """
    effective_from: Optional[str] = None
    effective_until: Optional[str] = None
    is_open_ended: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "effective_from": self.effective_from,
            "effective_until": self.effective_until,
            "is_open_ended": self.is_open_ended,
        }


class TemporalRelationship(BaseModel):
    """
    Explicit temporal relationship between standards, versions, or amendments.
    Must always retain provenance and evidence trace.
    """
    relationship_id: str
    source_entity_id: str
    target_entity_id: str
    relationship_type: TemporalRelationshipType
    source_document_id: str
    source_chunk_ids: List[str] = Field(default_factory=list)
    source_url: Optional[str] = None
    effective_date: Optional[str] = None
    publication_date: Optional[str] = None
    confidence: float = 1.0
    resolution_status: str = "resolved"
    statement_text: Optional[str] = None
    created_at: float = Field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "relationship_id": self.relationship_id,
            "source_entity_id": self.source_entity_id,
            "target_entity_id": self.target_entity_id,
            "relationship_type": self.relationship_type.value,
            "source_document_id": self.source_document_id,
            "source_chunk_ids": self.source_chunk_ids,
            "source_url": self.source_url,
            "effective_date": self.effective_date,
            "publication_date": self.publication_date,
            "confidence": self.confidence,
            "resolution_status": self.resolution_status,
            "statement_text": self.statement_text,
            "created_at": self.created_at,
        }


class AmendmentDetail(BaseModel):
    """
    Chronological amendment detail modifying a parent standard or version.
    affected_clause_numbers are populated only when explicitly supported by source.
    """
    amendment_id: str
    amendment_number: str
    standard_id: str
    version_id: Optional[str] = None
    title: Optional[str] = None
    publication_date: Optional[str] = None
    effective_date: Optional[str] = None
    affected_clause_numbers: List[str] = Field(default_factory=list)
    source_document_id: str = ""
    source_chunk_ids: List[str] = Field(default_factory=list)
    status: TemporalStatus = TemporalStatus.UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        return {
            "amendment_id": self.amendment_id,
            "amendment_number": self.amendment_number,
            "standard_id": self.standard_id,
            "version_id": self.version_id,
            "title": self.title,
            "publication_date": self.publication_date,
            "effective_date": self.effective_date,
            "affected_clause_numbers": self.affected_clause_numbers,
            "source_document_id": self.source_document_id,
            "source_chunk_ids": self.source_chunk_ids,
            "status": self.status.value,
        }


class VersionTimelineEntry(BaseModel):
    """
    A single node in a Standard's chronological version timeline.
    """
    version_id: str
    standard_id: str
    edition: Optional[str] = None
    standard_year: Optional[int] = None
    publication_date: Optional[str] = None
    effective_date: Optional[str] = None
    withdrawal_date: Optional[str] = None
    status: TemporalStatus = TemporalStatus.UNKNOWN
    validity_interval: Optional[ValidityInterval] = None
    amendments: List[AmendmentDetail] = Field(default_factory=list)
    superseded_by: Optional[str] = None
    supersedes: List[str] = Field(default_factory=list)
    document_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version_id": self.version_id,
            "standard_id": self.standard_id,
            "edition": self.edition,
            "standard_year": self.standard_year,
            "publication_date": self.publication_date,
            "effective_date": self.effective_date,
            "withdrawal_date": self.withdrawal_date,
            "status": self.status.value,
            "validity_interval": self.validity_interval.to_dict() if self.validity_interval else None,
            "amendments": [a.to_dict() for a in self.amendments],
            "superseded_by": self.superseded_by,
            "supersedes": self.supersedes,
            "document_id": self.document_id,
        }


class VersionTimeline(BaseModel):
    """
    Chronological sequence of versions and amendments for a BIS Standard.
    Preserves historical versions and does not overwrite old clauses.
    """
    standard_id: str
    standard_number: str
    versions: List[VersionTimelineEntry] = Field(default_factory=list)
    amendments: List[AmendmentDetail] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "standard_id": self.standard_id,
            "standard_number": self.standard_number,
            "versions": [v.to_dict() for v in self.versions],
            "amendments": [a.to_dict() for a in self.amendments],
        }


class ClauseEvolution(BaseModel):
    """
    Evolution of a clause between versions or amendments.
    Where mapping or legal effect is uncertain, state is UNKNOWN.
    """
    standard_id: str
    clause_number: str
    base_version_id: Optional[str] = None
    target_version_id: Optional[str] = None
    state: ClauseEvolutionState = ClauseEvolutionState.UNKNOWN
    evidence: List[str] = Field(default_factory=list)
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "standard_id": self.standard_id,
            "clause_number": self.clause_number,
            "base_version_id": self.base_version_id,
            "target_version_id": self.target_version_id,
            "state": self.state.value,
            "evidence": self.evidence,
            "notes": self.notes,
        }


class TemporalConflict(BaseModel):
    """
    Represents a detected temporal or version conflict where multiple requirements
    or amendments claim the same scope without explicit precedence evidence.
    """
    conflict_id: str
    conflict_type: str
    entities_involved: List[str]
    clauses_involved: List[str] = Field(default_factory=list)
    source_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    temporal_metadata: Dict[str, Any] = Field(default_factory=dict)
    nature_of_difference: str
    resolution_note: str = (
        "Conflicting evidence exists without explicit supersession order; "
        "cannot determine legal precedence automatically."
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "conflict_type": self.conflict_type,
            "entities_involved": self.entities_involved,
            "clauses_involved": self.clauses_involved,
            "source_evidence": self.source_evidence,
            "temporal_metadata": self.temporal_metadata,
            "nature_of_difference": self.nature_of_difference,
            "resolution_note": self.resolution_note,
        }


class TemporalResolution(BaseModel):
    """
    Conservative output of the CurrentnessResolver.
    Only returns CURRENT_SUPPORTED when evidence explicitly supports it.
    """
    status: TemporalStatus = TemporalStatus.TEMPORALLY_UNCERTAIN
    candidate_versions: List[str] = Field(default_factory=list)
    supporting_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    reason: str
    requires_verification: bool = True
    resolved_version_id: Optional[str] = None
    timeline: Optional[VersionTimeline] = None
    conflicts: List[TemporalConflict] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "candidate_versions": self.candidate_versions,
            "supporting_evidence": self.supporting_evidence,
            "reason": self.reason,
            "requires_verification": self.requires_verification,
            "resolved_version_id": self.resolved_version_id,
            "timeline": self.timeline.to_dict() if self.timeline else None,
            "conflicts": [c.to_dict() for c in self.conflicts],
        }


class TemporalTrace(BaseModel):
    """
    Debug observability trace for temporal reasoning.
    Kept separate from public response payload.
    """
    query: str
    temporal_entities: Dict[str, Any] = Field(default_factory=dict)
    candidate_versions: List[str] = Field(default_factory=list)
    explicit_relationships: List[Dict[str, Any]] = Field(default_factory=list)
    temporal_conflicts: List[Dict[str, Any]] = Field(default_factory=list)
    resolution: str
    supporting_evidence: List[Dict[str, Any]] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "temporal_entities": self.temporal_entities,
            "candidate_versions": self.candidate_versions,
            "explicit_relationships": self.explicit_relationships,
            "temporal_conflicts": self.temporal_conflicts,
            "resolution": self.resolution,
            "supporting_evidence": self.supporting_evidence,
        }


class TemporalAuditReport(BaseModel):
    """
    Diagnostics summary for temporal knowledge and extraction audits.
    """
    versions_seen: int = 0
    amendments_seen: int = 0
    explicit_supersessions: int = 0
    explicit_withdrawals: int = 0
    explicit_effective_dates: int = 0
    temporal_conflicts: int = 0
    temporally_uncertain_cases: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "versions_seen": self.versions_seen,
            "amendments_seen": self.amendments_seen,
            "explicit_supersessions": self.explicit_supersessions,
            "explicit_withdrawals": self.explicit_withdrawals,
            "explicit_effective_dates": self.explicit_effective_dates,
            "temporal_conflicts": self.temporal_conflicts,
            "temporally_uncertain_cases": self.temporally_uncertain_cases,
        }
