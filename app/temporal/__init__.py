"""
app/temporal/__init__.py

BIS Temporal, Version, and Amendment Intelligence package.
"""

from app.temporal.models import (
    TemporalRelationshipType,
    TemporalStatus,
    ClauseEvolutionState,
    ValidityInterval,
    TemporalRelationship,
    AmendmentDetail,
    VersionTimelineEntry,
    VersionTimeline,
    ClauseEvolution,
    TemporalConflict,
    TemporalResolution,
    TemporalTrace,
    TemporalAuditReport,
)

from app.temporal.resolver import (
    CurrentnessResolver,
    extract_temporal_relationships_from_text,
    build_version_timeline,
    compare_clause_evolution,
    detect_temporal_conflicts,
    build_temporal_trace,
    generate_temporal_audit,
)

__all__ = [
    "TemporalRelationshipType",
    "TemporalStatus",
    "ClauseEvolutionState",
    "ValidityInterval",
    "TemporalRelationship",
    "AmendmentDetail",
    "VersionTimelineEntry",
    "VersionTimeline",
    "ClauseEvolution",
    "TemporalConflict",
    "TemporalResolution",
    "TemporalTrace",
    "TemporalAuditReport",
    "CurrentnessResolver",
    "extract_temporal_relationships_from_text",
    "build_version_timeline",
    "compare_clause_evolution",
    "detect_temporal_conflicts",
    "build_temporal_trace",
    "generate_temporal_audit",
]
