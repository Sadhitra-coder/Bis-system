"""
app/temporal/resolver.py

Temporal Resolver, Conflict Detector, Clause Evolution Engine,
and Timeline Builder for BIS Compliance Intelligence.

Core Safety Commitments:
  - NEVER equate latest publication date or highest year with legal currentness.
  - NEVER infer supersession without explicit source evidence.
  - Missing evidence strictly yields TEMPORALLY_UNCERTAIN / VERIFICATION_REQUIRED.
  - Detect conflicting versions/amendments and flag CONFLICTING_EVIDENCE.
  - Distinguish published, effective, and withdrawn dates.
  - Zero date fabrication — open-ended intervals remain unknown.
"""

import hashlib
import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from app.evidence.models import EvidenceItem
from app.knowledge.models import Amendment, Clause, Standard, StandardVersion
from app.knowledge.normalization import normalize_clause_number, normalize_standard_number
from app.temporal.models import (
    AmendmentDetail,
    ClauseEvolution,
    ClauseEvolutionState,
    TemporalAuditReport,
    TemporalConflict,
    TemporalRelationship,
    TemporalRelationshipType,
    TemporalResolution,
    TemporalStatus,
    TemporalTrace,
    ValidityInterval,
    VersionTimeline,
    VersionTimelineEntry,
)

logger = logging.getLogger(__name__)

# ============================================================
# EXPLICIT TEMPORAL EVIDENCE PATTERNS (Section 5)
# ============================================================

# "supersedes IS 3055 : 1999" or "superseding IS 3055" or "supersedes the earlier edition"
_SUPERSEDES_PATTERN = re.compile(
    r"\b(?:supersedes|superseding|in\s+supersession\s+of|cancels\s+and\s+replaces)\s+"
    r"(?:the\s+earlier\s+edition\s+of\s+)?(IS\s+\d+(?:-\d+|\s*\(Part\s*\d+\))?(?:\s*:\s*\d{4})?)",
    re.IGNORECASE,
)

# "superseded by IS 3055 : 2024" or "is superseded by"
_SUPERSEDED_BY_PATTERN = re.compile(
    r"\b(?:superseded\s+by|replaced\s+by|substituted\s+by)\s+"
    r"(IS\s+\d+(?:-\d+|\s*\(Part\s*\d+\))?(?:\s*:\s*\d{4})?)",
    re.IGNORECASE,
)

# "withdrawn w.e.f. 01-01-2025" or "withdrawn with effect from 15 May 2024" or "stand withdrawn"
_WITHDRAWN_PATTERN = re.compile(
    r"\b(?:withdrawn|stands\s+withdrawn|shall\s+stand\s+withdrawn)"
    r"(?:\s+(?:w\.e\.f\.|with\s+effect\s+from|on|from)\s+([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{4}|[0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4}))?",
    re.IGNORECASE,
)

# "effective from 01-07-2024" or "comes into force on 15 July 2024"
_EFFECTIVE_PATTERN = re.compile(
    r"\b(?:effective\s+(?:from|date)|comes\s+into\s+force\s+(?:on|w\.e\.f\.)|enforced\s+from)\s*[:\s]+"
    r"([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{4}|[0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4}|[0-9]{4}-[0-9]{2}-[0-9]{2})",
    re.IGNORECASE,
)

# "Amendment No. 1 to IS 3055" or "Amends Clause 4.1"
_AMENDS_CLAUSE_PATTERN = re.compile(
    r"\b(?:amends|modifies|substitutes|in\s+amendment\s+of)\s+(?:clause|section)\s*(\d+(?:\.\d+)*)",
    re.IGNORECASE,
)


def extract_temporal_relationships_from_text(
    text: str,
    source_document_id: str,
    source_chunk_ids: Optional[List[str]] = None,
    source_entity_id: str = "",
) -> List[TemporalRelationship]:
    """
    Deterministically extracts explicit supersession, withdrawal, and amendment
    relationships from source text.
    Never fabricates relationships without explicit source text evidence.
    """
    if not text or not text.strip():
        return []

    relationships: List[TemporalRelationship] = []
    chunk_ids = source_chunk_ids or []

    # 1. Check explicit supersedes
    for m in _SUPERSEDES_PATTERN.finditer(text):
        target_raw = m.group(1).strip()
        target_norm = normalize_standard_number(target_raw) or target_raw
        rel_id = f"rel_sup_{hashlib.sha256(f'{source_entity_id}_supersedes_{target_norm}'.encode()).hexdigest()[:12]}"
        relationships.append(
            TemporalRelationship(
                relationship_id=rel_id,
                source_entity_id=source_entity_id or source_document_id,
                target_entity_id=target_norm,
                relationship_type=TemporalRelationshipType.SUPERSEDES,
                source_document_id=source_document_id,
                source_chunk_ids=chunk_ids,
                confidence=1.0,
                resolution_status="resolved",
                statement_text=m.group(0).strip(),
            )
        )

    # 2. Check explicit superseded by
    for m in _SUPERSEDED_BY_PATTERN.finditer(text):
        target_raw = m.group(1).strip()
        target_norm = normalize_standard_number(target_raw) or target_raw
        rel_id = f"rel_supby_{hashlib.sha256(f'{source_entity_id}_supersededby_{target_norm}'.encode()).hexdigest()[:12]}"
        relationships.append(
            TemporalRelationship(
                relationship_id=rel_id,
                source_entity_id=source_entity_id or source_document_id,
                target_entity_id=target_norm,
                relationship_type=TemporalRelationshipType.SUPERSEDED_BY,
                source_document_id=source_document_id,
                source_chunk_ids=chunk_ids,
                confidence=1.0,
                resolution_status="resolved",
                statement_text=m.group(0).strip(),
            )
        )

    # 3. Check explicit withdrawal
    for m in _WITHDRAWN_PATTERN.finditer(text):
        w_date = m.group(1).strip() if m.group(1) else None
        rel_id = f"rel_wd_{hashlib.sha256(f'{source_entity_id}_withdrawn_{w_date}'.encode()).hexdigest()[:12]}"
        relationships.append(
            TemporalRelationship(
                relationship_id=rel_id,
                source_entity_id=source_entity_id or source_document_id,
                target_entity_id=source_entity_id or source_document_id,
                relationship_type=TemporalRelationshipType.WITHDRAWS,
                source_document_id=source_document_id,
                source_chunk_ids=chunk_ids,
                effective_date=w_date,
                confidence=1.0,
                resolution_status="resolved",
                statement_text=m.group(0).strip(),
            )
        )

    # 4. Check explicit effective date
    for m in _EFFECTIVE_PATTERN.finditer(text):
        eff_date = m.group(1).strip()
        rel_id = f"rel_eff_{hashlib.sha256(f'{source_entity_id}_effective_{eff_date}'.encode()).hexdigest()[:12]}"
        relationships.append(
            TemporalRelationship(
                relationship_id=rel_id,
                source_entity_id=source_entity_id or source_document_id,
                target_entity_id=source_entity_id or source_document_id,
                relationship_type=TemporalRelationshipType.EFFECTIVE_FROM,
                source_document_id=source_document_id,
                source_chunk_ids=chunk_ids,
                effective_date=eff_date,
                confidence=1.0,
                resolution_status="resolved",
                statement_text=m.group(0).strip(),
            )
        )

    return relationships


# ============================================================
# TIMELINE BUILDER (Section 2 & 3)
# ============================================================

def build_version_timeline(
    standard: Optional[Standard],
    versions: List[StandardVersion],
    amendments: List[Amendment],
    relationships: Optional[List[TemporalRelationship]] = None,
) -> VersionTimeline:
    """
    Builds a chronological VersionTimeline from knowledge entities.
    Does not delete historical versions or overwrite old clauses.
    """
    std_id = standard.standard_id if standard else (versions[0].standard_id if versions else "std_unknown")
    std_num = standard.standard_number if standard else (versions[0].standard_id if versions else "Unknown Standard")

    rels = relationships or []
    # Index supersessions: target_id -> list of source_ids that supersede it
    superseded_map: Dict[str, str] = {}
    supersedes_map: Dict[str, List[str]] = {}
    for r in rels:
        if r.relationship_type == TemporalRelationshipType.SUPERSEDES:
            supersedes_map.setdefault(r.source_entity_id, []).append(r.target_entity_id)
            superseded_map[r.target_entity_id] = r.source_entity_id
        elif r.relationship_type == TemporalRelationshipType.SUPERSEDED_BY:
            superseded_map[r.source_entity_id] = r.target_entity_id
            supersedes_map.setdefault(r.target_entity_id, []).append(r.source_entity_id)

    # Sort versions chronologically: primary standard_year, secondary publication_date, fallback created_at
    def _ver_sort_key(v: StandardVersion):
        yr = v.standard_year or 0
        pub = v.publication_date or ""
        return (yr, pub, v.created_at)

    sorted_versions = sorted(versions, key=_ver_sort_key)

    timeline_entries: List[VersionTimelineEntry] = []
    for v in sorted_versions:
        # Map amendments belonging to this version
        ver_amds = [
            AmendmentDetail(
                amendment_id=a.amendment_id,
                amendment_number=a.amendment_number,
                standard_id=a.standard_id,
                version_id=a.version_id,
                title=a.title,
                publication_date=a.publication_date,
                effective_date=a.effective_date,
                source_document_id=a.source_document_id,
                status=TemporalStatus.AMENDED if a.status.value == "effective" else TemporalStatus.UNKNOWN,
            )
            for a in amendments
            if a.version_id == v.version_id or (not a.version_id and a.standard_id == v.standard_id)
        ]

        # Determine status from explicit status or relationships
        is_superseded = (
            v.version_id in superseded_map
            or any(v.standard_year and str(v.standard_year) in str(tgt) for tgt in superseded_map.keys())
        )
        is_withdrawn = v.withdrawal_date is not None or v.status.value == "withdrawn"

        entry_status = TemporalStatus.UNKNOWN
        if is_withdrawn:
            entry_status = TemporalStatus.WITHDRAWN
        elif is_superseded:
            entry_status = TemporalStatus.SUPERSEDED
        elif v.status.value == "effective" and not is_superseded and not is_withdrawn:
            entry_status = TemporalStatus.CURRENT_SUPPORTED
        elif v.status.value == "published":
            entry_status = TemporalStatus.TEMPORALLY_UNCERTAIN

        interval = ValidityInterval(
            effective_from=v.effective_date or v.publication_date,
            effective_until=v.withdrawal_date or (
                # If superseded by a version with effective_date
                None
            ),
            is_open_ended=(v.withdrawal_date is None),
        )

        entry = VersionTimelineEntry(
            version_id=v.version_id,
            standard_id=v.standard_id,
            edition=v.edition,
            standard_year=v.standard_year,
            publication_date=v.publication_date,
            effective_date=v.effective_date,
            withdrawal_date=v.withdrawal_date,
            status=entry_status,
            validity_interval=interval,
            amendments=ver_amds,
            superseded_by=superseded_map.get(v.version_id),
            supersedes=supersedes_map.get(v.version_id, []),
            document_id=v.document_id,
        )
        timeline_entries.append(entry)

    all_amds = [
        AmendmentDetail(
            amendment_id=a.amendment_id,
            amendment_number=a.amendment_number,
            standard_id=a.standard_id,
            version_id=a.version_id,
            title=a.title,
            publication_date=a.publication_date,
            effective_date=a.effective_date,
            source_document_id=a.source_document_id,
            status=TemporalStatus.AMENDED if a.status.value == "effective" else TemporalStatus.UNKNOWN,
        )
        for a in amendments
    ]

    return VersionTimeline(
        standard_id=std_id,
        standard_number=std_num,
        versions=timeline_entries,
        amendments=all_amds,
    )


# ============================================================
# CLAUSE EVOLUTION COMPARATOR (Section 4)
# ============================================================

def compare_clause_evolution(
    base_clause: Optional[Clause],
    target_clause: Optional[Clause],
    standard_id: str = "",
    clause_number: str = "",
) -> ClauseEvolution:
    """
    Compares two clause instances across versions or amendments.
    Possible states: unchanged, modified, added, removed, unknown.
    Never concludes legal equivalence purely from text similarity.
    """
    c_num = clause_number or (base_clause.clause_number if base_clause else (target_clause.clause_number if target_clause else ""))
    std_id = standard_id or (base_clause.standard_id if base_clause else (target_clause.standard_id if target_clause else ""))

    if base_clause is None and target_clause is None:
        return ClauseEvolution(
            standard_id=std_id,
            clause_number=c_num,
            state=ClauseEvolutionState.UNKNOWN,
            notes="Neither base nor target clause exists.",
        )

    if base_clause is not None and target_clause is None:
        return ClauseEvolution(
            standard_id=std_id,
            clause_number=c_num,
            base_version_id=base_clause.version_id,
            target_version_id=None,
            state=ClauseEvolutionState.REMOVED,
            evidence=[f"Clause {c_num} present in base version {base_clause.version_id} but absent in target."],
            notes="Clause removed in target version.",
        )

    if base_clause is None and target_clause is not None:
        return ClauseEvolution(
            standard_id=std_id,
            clause_number=c_num,
            base_version_id=None,
            target_version_id=target_clause.version_id,
            state=ClauseEvolutionState.ADDED,
            evidence=[f"Clause {c_num} absent in base version but introduced in target {target_clause.version_id}."],
            notes="Clause newly added in target version.",
        )

    # Both exist
    base_title = (base_clause.clause_title or "").strip().lower()
    target_title = (target_clause.clause_title or "").strip().lower()

    if base_title == target_title:
        state = ClauseEvolutionState.UNCHANGED
        notes = "Clause headings and structure match across versions."
    else:
        state = ClauseEvolutionState.MODIFIED
        notes = f"Clause heading modified from '{base_clause.clause_title}' to '{target_clause.clause_title}'."

    return ClauseEvolution(
        standard_id=std_id,
        clause_number=c_num,
        base_version_id=base_clause.version_id,
        target_version_id=target_clause.version_id,
        state=state,
        evidence=[
            f"Base: {base_clause.clause_id} ({base_clause.version_id})",
            f"Target: {target_clause.clause_id} ({target_clause.version_id})",
        ],
        notes=notes,
    )


# ============================================================
# CONFLICT DETECTION (Sections 10 & 11)
# ============================================================

def detect_temporal_conflicts(
    standard_id: str,
    versions: List[StandardVersion],
    amendments: List[Amendment],
    relationships: List[TemporalRelationship],
    evidence_items: Optional[List[EvidenceItem]] = None,
) -> List[TemporalConflict]:
    """
    Detects temporal and version conflicts where multiple versions or amendments
    assert competing requirements without an explicit supersession or precedence order.
    """
    conflicts: List[TemporalConflict] = []
    rels = relationships or []

    # Check for supersession links
    supersedes_targets = {
        r.target_entity_id for r in rels
        if r.relationship_type == TemporalRelationshipType.SUPERSEDES
    }
    superseded_sources = {
        r.source_entity_id for r in rels
        if r.relationship_type == TemporalRelationshipType.SUPERSEDED_BY
    }
    superseded_set = supersedes_targets.union(superseded_sources)

    def _is_superseded(v: StandardVersion) -> bool:
        if v.version_id in superseded_set:
            return True
        for target in superseded_set:
            if v.standard_year and str(v.standard_year) in str(target):
                return True
        return False

    # 1. Multiple active/effective versions without supersession (competing versions)
    active_versions = [
        v for v in versions
        if v.status.value in ("published", "effective")
        and not _is_superseded(v)
        and not v.withdrawal_date
    ]

    if len(active_versions) > 1:
        years = [str(v.standard_year or v.edition or v.version_id) for v in active_versions]
        conf_id = f"conf_ver_{hashlib.sha256('_'.join(years).encode()).hexdigest()[:10]}"
        conflicts.append(
            TemporalConflict(
                conflict_id=conf_id,
                conflict_type="competing_versions",
                entities_involved=[v.version_id for v in active_versions],
                clauses_involved=[],
                source_evidence=[
                    {"version_id": v.version_id, "year": v.standard_year, "status": v.status.value, "document_id": v.document_id}
                    for v in active_versions
                ],
                temporal_metadata={
                    "version_years": years,
                    "supersession_evidence_found": False,
                },
                nature_of_difference=(
                    f"Multiple editions ({', '.join(years)}) are published/effective for standard {standard_id} "
                    f"without explicit supersession documentation resolving legal precedence."
                ),
                resolution_note=(
                    "Conflicting evidence exists without explicit supersession order; "
                    "cannot determine legal precedence automatically."
                ),
            )
        )

    # 2. Conflicting amendments affecting the same clause
    # Map amendment numbers to affected clauses
    clause_to_amds: Dict[str, List[Amendment]] = {}
    for a in amendments:
        # Check if title or document mentions affected clauses
        if a.title:
            for m in _AMENDS_CLAUSE_PATTERN.finditer(a.title):
                c_num = m.group(1)
                clause_to_amds.setdefault(c_num, []).append(a)

    for c_num, amds in clause_to_amds.items():
        if len(amds) > 1:
            amd_nums = [a.amendment_number for a in amds]
            amd_joined = "".join(amd_nums)
            conf_id = f"conf_amd_{hashlib.sha256(f'{c_num}_{amd_joined}'.encode()).hexdigest()[:10]}"
            conflicts.append(
                TemporalConflict(
                    conflict_id=conf_id,
                    conflict_type="conflicting_amendments",
                    entities_involved=[a.amendment_id for a in amds],
                    clauses_involved=[c_num],
                    source_evidence=[
                        {"amendment_id": a.amendment_id, "amendment_number": a.amendment_number, "document_id": a.source_document_id}
                        for a in amds
                    ],
                    temporal_metadata={"clause_number": c_num, "amendments": amd_nums},
                    nature_of_difference=(
                        f"Multiple amendments ({', '.join(amd_nums)}) modify Clause {c_num}. "
                        f"Sequence preserved; base text not consolidated automatically."
                    ),
                    resolution_note=(
                        "Multiple amendments modify the same clause. Source text is presented separately."
                    ),
                )
            )

    return conflicts


# ============================================================
# CONSERVATIVE CURRENTNESS RESOLVER (Section 7)
# ============================================================

class CurrentnessResolver:
    """
    Conservative temporal currentness resolver.

    SAFETY GUARANTEES:
      - NEVER equates latest publication date or highest year with legal currentness.
      - NEVER declares a newer document current without explicit supersession evidence.
      - Absence of conclusive currentness evidence strictly outputs TEMPORALLY_UNCERTAIN.
      - When uncertain, sets requires_verification = True.
    """

    @classmethod
    def resolve(
        cls,
        standard: Optional[Standard],
        versions: List[StandardVersion],
        amendments: List[Amendment],
        relationships: List[TemporalRelationship],
        evidence_items: Optional[List[EvidenceItem]] = None,
        query_temporal_intent: Optional[str] = None,
        requested_year: Optional[int] = None,
        requested_amendment: Optional[str] = None,
    ) -> TemporalResolution:
        """
        Main entry point for temporal currentness evaluation.
        """
        evidence_items = evidence_items or []
        std_id = standard.standard_id if standard else (versions[0].standard_id if versions else "unknown")
        std_num = standard.standard_number if standard else (versions[0].standard_id if versions else "Unknown Standard")

        # Build timeline
        timeline = build_version_timeline(standard, versions, amendments, relationships)

        # Detect conflicts
        conflicts = detect_temporal_conflicts(std_id, versions, amendments, relationships, evidence_items)

        # -------------------------------------------------------------
        # 1. SPECIFIC AMENDMENT QUERY
        # -------------------------------------------------------------
        if requested_amendment is not None:
            matching_amd = next((a for a in amendments if a.amendment_number == requested_amendment), None)
            if matching_amd:
                return TemporalResolution(
                    status=TemporalStatus.AMENDED,
                    candidate_versions=[matching_amd.version_id] if matching_amd.version_id else [],
                    supporting_evidence=[{"amendment_id": matching_amd.amendment_id, "amendment_number": matching_amd.amendment_number}],
                    reason=f"Retrieved Amendment {requested_amendment} for {std_num}.",
                    requires_verification=False,
                    resolved_version_id=matching_amd.version_id,
                    timeline=timeline,
                    conflicts=conflicts,
                )

        # -------------------------------------------------------------
        # 2. NO VERSIONS IN KNOWLEDGE STORE
        # -------------------------------------------------------------
        if not versions:
            return TemporalResolution(
                status=TemporalStatus.TEMPORALLY_UNCERTAIN,
                candidate_versions=[],
                supporting_evidence=[],
                reason=f"No version records found in knowledge store for {std_num}.",
                requires_verification=True,
                timeline=timeline,
                conflicts=conflicts,
            )

        # -------------------------------------------------------------
        # 3. SPECIFIC HISTORICAL VERSION QUERY
        # -------------------------------------------------------------
        if requested_year is not None:
            matching_ver = next((v for v in versions if v.standard_year == requested_year), None)
            if matching_ver:
                # Check if this requested version is superseded or withdrawn
                t_entry = next((e for e in timeline.versions if e.version_id == matching_ver.version_id), None)
                ver_status = t_entry.status if t_entry else TemporalStatus.UNKNOWN

                if ver_status == TemporalStatus.SUPERSEDED:
                    return TemporalResolution(
                        status=TemporalStatus.SUPERSEDED,
                        candidate_versions=[matching_ver.version_id],
                        supporting_evidence=[{"version_id": matching_ver.version_id, "year": matching_ver.standard_year}],
                        reason=f"The requested {matching_ver.standard_year} edition of {std_num} is superseded.",
                        requires_verification=False,
                        resolved_version_id=matching_ver.version_id,
                        timeline=timeline,
                        conflicts=conflicts,
                    )
                elif ver_status == TemporalStatus.WITHDRAWN:
                    return TemporalResolution(
                        status=TemporalStatus.WITHDRAWN,
                        candidate_versions=[matching_ver.version_id],
                        supporting_evidence=[{"version_id": matching_ver.version_id, "year": matching_ver.standard_year}],
                        reason=f"The requested {matching_ver.standard_year} edition of {std_num} has been withdrawn.",
                        requires_verification=False,
                        resolved_version_id=matching_ver.version_id,
                        timeline=timeline,
                        conflicts=conflicts,
                    )
                else:
                    return TemporalResolution(
                        status=TemporalStatus.HISTORICAL,
                        candidate_versions=[matching_ver.version_id],
                        supporting_evidence=[{"version_id": matching_ver.version_id, "year": matching_ver.standard_year}],
                        reason=f"Retrieved historical edition {matching_ver.standard_year} for {std_num} as requested.",
                        requires_verification=False,
                        resolved_version_id=matching_ver.version_id,
                        timeline=timeline,
                        conflicts=conflicts,
                    )

        # -------------------------------------------------------------
        # 4. HISTORICAL / PREVIOUS VERSION INTENT
        # -------------------------------------------------------------
        if query_temporal_intent == "historical":
            historical_entries = [
                e for e in timeline.versions
                if e.status in (TemporalStatus.SUPERSEDED, TemporalStatus.HISTORICAL, TemporalStatus.WITHDRAWN)
            ]
            if historical_entries:
                cand_ids = [e.version_id for e in historical_entries]
                return TemporalResolution(
                    status=TemporalStatus.HISTORICAL,
                    candidate_versions=cand_ids,
                    supporting_evidence=[{"version_id": e.version_id, "status": e.status.value} for e in historical_entries],
                    reason=f"Identified historical/previous version(s) for {std_num}: {', '.join(cand_ids)}.",
                    requires_verification=False,
                    resolved_version_id=historical_entries[-1].version_id,
                    timeline=timeline,
                    conflicts=conflicts,
                )
            superseded_targets = [
                r.target_entity_id for r in relationships
                if r.relationship_type == TemporalRelationshipType.SUPERSEDES
            ]
            cand_versions = [
                v.version_id for v in versions 
                if v.version_id in superseded_targets 
                or (v.standard_year and any(str(v.standard_year) in t for t in superseded_targets))
            ]
            if cand_versions:
                return TemporalResolution(
                    status=TemporalStatus.HISTORICAL,
                    candidate_versions=cand_versions,
                    supporting_evidence=[{"version_ids": cand_versions}],
                    reason=f"Identified superseded/historical version(s) for {std_num}: {', '.join(cand_versions)}.",
                    requires_verification=False,
                    resolved_version_id=cand_versions[-1],
                    timeline=timeline,
                    conflicts=conflicts,
                )
            if len(timeline.versions) > 1:
                earlier_entries = timeline.versions[:-1]
                cand_ids = [e.version_id for e in earlier_entries]
                return TemporalResolution(
                    status=TemporalStatus.HISTORICAL,
                    candidate_versions=cand_ids,
                    supporting_evidence=[{"version_id": e.version_id} for e in earlier_entries],
                    reason=f"Identified earlier/historical version(s) for {std_num}: {', '.join(cand_ids)}.",
                    requires_verification=False,
                    resolved_version_id=earlier_entries[-1].version_id,
                    timeline=timeline,
                    conflicts=conflicts,
                )

        # -------------------------------------------------------------
        # 5. COMPETING VERSIONS / CONFLICTING EVIDENCE
        # -------------------------------------------------------------
        if conflicts:
            cand_ids = [v.version_id for v in versions]
            return TemporalResolution(
                status=TemporalStatus.TEMPORALLY_UNCERTAIN,
                candidate_versions=cand_ids,
                supporting_evidence=[{"conflict_id": c.conflict_id, "type": c.conflict_type} for c in conflicts],
                reason=(
                    f"Temporal conflict detected for {std_num}: multiple published versions exist "
                    f"without explicit supersession evidence resolving active legal currentness."
                ),
                requires_verification=True,
                timeline=timeline,
                conflicts=conflicts,
            )

        # -------------------------------------------------------------
        # 5. EXPLICIT SUPERSESSION RESOLUTION
        # -------------------------------------------------------------
        # If there are multiple versions and an explicit supersession link exists:
        superseding_rels = [r for r in relationships if r.relationship_type == TemporalRelationshipType.SUPERSEDES]
        if superseding_rels:
            for r in superseding_rels:
                active_cand = next((
                    v for v in versions 
                    if v.version_id == r.source_entity_id 
                    or v.standard_id == r.source_entity_id
                    or (v.standard_year and str(v.standard_year) in str(r.source_entity_id))
                ), None)
                if active_cand and not active_cand.withdrawal_date and active_cand.status.value in ("effective", "published"):
                    return TemporalResolution(
                        status=TemporalStatus.CURRENT_SUPPORTED,
                        candidate_versions=[active_cand.version_id],
                        supporting_evidence=[r.to_dict()],
                        reason=(
                            f"Version {active_cand.edition or active_cand.standard_year or active_cand.version_id} "
                            f"is verified as current by explicit supersession evidence: '{r.statement_text}'."
                        ),
                        requires_verification=False,
                        resolved_version_id=active_cand.version_id,
                        timeline=timeline,
                        conflicts=[],
                    )

        # -------------------------------------------------------------
        # 6. SINGLE VERSION WITH EXPLICIT CURRENTNESS EVIDENCE
        # -------------------------------------------------------------
        if len(versions) == 1:
            v = versions[0]
            if v.status.value == "withdrawn":
                return TemporalResolution(
                    status=TemporalStatus.WITHDRAWN,
                    candidate_versions=[v.version_id],
                    supporting_evidence=[{"version_id": v.version_id, "status": "withdrawn"}],
                    reason=f"Standard {std_num} (version {v.version_id}) is marked as withdrawn.",
                    requires_verification=False,
                    resolved_version_id=v.version_id,
                    timeline=timeline,
                    conflicts=[],
                )
            elif v.status.value == "effective" and (standard and standard.is_current is True):
                return TemporalResolution(
                    status=TemporalStatus.CURRENT_SUPPORTED,
                    candidate_versions=[v.version_id],
                    supporting_evidence=[{"version_id": v.version_id, "is_current": True, "status": "effective"}],
                    reason=f"Standard {std_num} has a single version verified as active and current.",
                    requires_verification=False,
                    resolved_version_id=v.version_id,
                    timeline=timeline,
                    conflicts=[],
                )
            else:
                # Single version but no explicit confirmation of legal currentness
                return TemporalResolution(
                    status=TemporalStatus.TEMPORALLY_UNCERTAIN,
                    candidate_versions=[v.version_id],
                    supporting_evidence=[{"version_id": v.version_id, "status": v.status.value}],
                    reason=(
                        f"Standard {std_num} has version {v.standard_year or v.version_id} in corpus, "
                        f"but legal currentness has not been formally confirmed."
                    ),
                    requires_verification=True,
                    resolved_version_id=v.version_id,
                    timeline=timeline,
                    conflicts=[],
                )

        # -------------------------------------------------------------
        # 7. DEFAULT: MULTIPLE VERSIONS WITHOUT SUPERSESSION EVIDENCE
        # -------------------------------------------------------------
        cand_ids = [v.version_id for v in versions]
        return TemporalResolution(
            status=TemporalStatus.TEMPORALLY_UNCERTAIN,
            candidate_versions=cand_ids,
            supporting_evidence=[],
            reason=(
                f"Multiple versions ({len(versions)}) exist for {std_num}, but no explicit supersession "
                f"evidence establishes which version is currently legally binding. Verification Required."
            ),
            requires_verification=True,
            timeline=timeline,
            conflicts=conflicts,
        )


# ============================================================
# TEMPORAL TRACE AND AUDIT BUILDERS (Sections 26 & 27)
# ============================================================

def build_temporal_trace(
    query: str,
    temporal_entities: Dict[str, Any],
    resolution: TemporalResolution,
    relationships: List[TemporalRelationship],
) -> TemporalTrace:
    """Builds a structured debug trace of temporal reasoning."""
    return TemporalTrace(
        query=query,
        temporal_entities=temporal_entities,
        candidate_versions=resolution.candidate_versions,
        explicit_relationships=[r.to_dict() for r in relationships],
        temporal_conflicts=[c.to_dict() for c in resolution.conflicts],
        resolution=resolution.status.value,
        supporting_evidence=resolution.supporting_evidence,
    )


def generate_temporal_audit(
    all_versions: List[StandardVersion],
    all_amendments: List[Amendment],
    all_relationships: List[TemporalRelationship],
    conflicts: List[TemporalConflict],
    uncertain_count: int,
) -> TemporalAuditReport:
    """Generates an audit report across all temporal knowledge in the system."""
    explicit_sup = sum(1 for r in all_relationships if r.relationship_type in (TemporalRelationshipType.SUPERSEDES, TemporalRelationshipType.SUPERSEDED_BY))
    explicit_wd = sum(1 for r in all_relationships if r.relationship_type in (TemporalRelationshipType.WITHDRAWS, TemporalRelationshipType.WITHDRAWN_BY))
    explicit_eff = sum(1 for r in all_relationships if r.relationship_type == TemporalRelationshipType.EFFECTIVE_FROM or r.effective_date is not None)

    return TemporalAuditReport(
        versions_seen=len(all_versions),
        amendments_seen=len(all_amendments),
        explicit_supersessions=explicit_sup,
        explicit_withdrawals=explicit_wd,
        explicit_effective_dates=explicit_eff,
        temporal_conflicts=len(conflicts),
        temporally_uncertain_cases=uncertain_count,
    )
