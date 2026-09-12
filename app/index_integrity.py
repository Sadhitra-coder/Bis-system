"""
app/index_integrity.py

Whether the persisted vector index can be trusted.

WHY `collection.count() > 0` IS NOT A HEALTH CHECK
--------------------------------------------------
Before Phase 6 readiness was decided by asking Chroma whether the
collection had any rows. The live index passed that check with 16 chunks
while carrying metadata from two superseded contracts:

    metadata-key-count histogram: {4: 7, 28: 9}

Seven chunks predated page provenance entirely. Nine predated contextual
retrieval. Zero were at the then-current contract. Every downstream feature
that reads metadata was therefore inert — identifier boosting had no
standard_number to boost on, intent-aware tiering collapsed to the reranker
score, and the contextualized-content fallbacks in the retriever quietly
substituted raw text. The application reported itself healthy throughout,
because 16 > 0.

A non-empty collection says nothing about whether its contents match the
contract the code expects. This module asks that question instead, by
sampling real persisted metadata and comparing it against the canonical
schema.

WHAT THIS MODULE DOES NOT DO
----------------------------
It never deletes, truncates, migrates or rewrites anything. A stale index
is still readable data and may be the only copy of an expensive ingestion.
The check reports a state and tells the operator what to run; rebuilding is
an explicit, separately-invoked decision (app/scripts/reindex.py).
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.index_schema import (
    CHUNK_INDEX_SCHEMA_VERSION,
    REQUIRED_FIELD_NAMES,
    validate_chunk_index_metadata,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# States
#
# Ordered from usable to unusable. `READY` is the only state in which the
# index may be described as healthy.
# ---------------------------------------------------------------------------

#: Collection exists, is populated, and sampled chunks match the current schema.
INDEX_READY = "INDEX_READY"

#: Collection exists and is legitimately empty. Not an error — a fresh install.
INDEX_EMPTY = "INDEX_EMPTY"

#: Populated, but sampled chunks were written under a different schema version.
#: Retrieval will return degraded provenance. A re-index is required.
INDEX_SCHEMA_MISMATCH = "INDEX_SCHEMA_MISMATCH"

#: Schema version is current but the metadata itself violates the contract.
#: Rarer and more serious than a mismatch: the writer produced bad rows.
INDEX_CORRUPT = "INDEX_CORRUPT"

#: The collection could not be opened or read at all.
INDEX_UNAVAILABLE = "INDEX_UNAVAILABLE"

#: States in which the application must not claim to be healthy.
UNHEALTHY_STATES = frozenset({
    INDEX_SCHEMA_MISMATCH,
    INDEX_CORRUPT,
    INDEX_UNAVAILABLE,
})

#: States in which retrieval should not be wired up.
NON_RETRIEVABLE_STATES = frozenset({
    INDEX_EMPTY,
    INDEX_UNAVAILABLE,
})

#: How many chunks to sample. Large enough to catch a mixed-contract index
#: like the one above, small enough to keep startup fast on a big collection.
DEFAULT_SAMPLE_SIZE = 50


@dataclass
class IndexIntegrityReport:
    """The outcome of inspecting a persisted collection."""

    state: str
    total_chunks: int = 0
    sampled: int = 0
    #: schema_version value -> count. Absent versions appear as '<absent>'.
    schema_versions: Dict[str, int] = field(default_factory=dict)
    #: metadata key count -> number of sampled chunks with that many keys.
    key_count_histogram: Dict[int, int] = field(default_factory=dict)
    #: Required field name -> how many sampled chunks are missing or empty for it.
    missing_required: Dict[str, int] = field(default_factory=dict)
    #: Contract violations found in sampled chunks, capped for legibility.
    violations: List[str] = field(default_factory=list)
    #: standard_relation value -> count.
    relation_counts: Dict[str, int] = field(default_factory=dict)
    #: One-line operator-facing explanation.
    message: str = ""
    #: What to do about it, or empty when nothing is needed.
    remediation: str = ""

    @property
    def is_healthy(self) -> bool:
        return self.state not in UNHEALTHY_STATES

    @property
    def is_retrievable(self) -> bool:
        """Whether it makes sense to build a retriever over this index."""
        return self.state not in NON_RETRIEVABLE_STATES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "healthy": self.is_healthy,
            "expected_schema_version": CHUNK_INDEX_SCHEMA_VERSION,
            "total_chunks": self.total_chunks,
            "sampled": self.sampled,
            "schema_versions": dict(self.schema_versions),
            "key_count_histogram": {str(k): v for k, v in self.key_count_histogram.items()},
            "missing_required": dict(self.missing_required),
            "relation_counts": dict(self.relation_counts),
            "violations": list(self.violations),
            "message": self.message,
            "remediation": self.remediation,
        }


MAX_REPORTED_VIOLATIONS = 20

REINDEX_HINT = "Run: python -m app.scripts.reindex --rebuild"


def check_index_integrity(
    collection: Any,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> IndexIntegrityReport:
    """
    Inspect a persisted Chroma collection against the canonical schema.

    Reads real stored metadata via collection.get(). Never writes, never
    deletes. Any exception while reading the store is reported as
    INDEX_UNAVAILABLE rather than raised, so a damaged index degrades the
    service instead of preventing it from starting at all.
    """
    report = IndexIntegrityReport(state=INDEX_UNAVAILABLE)

    if collection is None:
        report.message = "No vector collection was provided."
        report.remediation = "Check ChromaDB initialization at startup."
        return report

    try:
        report.total_chunks = int(collection.count())
    except Exception as exc:
        report.message = f"Could not read collection count: {exc}"
        report.remediation = "Verify the ChromaDB path is readable and not corrupt."
        logger.error("Index integrity check could not count the collection: %s", exc)
        return report

    if report.total_chunks == 0:
        report.state = INDEX_EMPTY
        report.message = "Vector collection is empty."
        report.remediation = "Ingest a document, or run a re-index if data was expected."
        return report

    try:
        payload = collection.get(
            limit=min(sample_size, report.total_chunks),
            include=["metadatas"],
        )
        metadatas = payload.get("metadatas") or []
    except Exception as exc:
        report.message = f"Could not read collection metadata: {exc}"
        report.remediation = "Verify the ChromaDB path is readable and not corrupt."
        logger.error("Index integrity check could not read metadata: %s", exc)
        return report

    metadatas = [m for m in metadatas if isinstance(m, dict)]
    report.sampled = len(metadatas)

    if report.sampled == 0:
        # count() said there were rows but none came back with metadata.
        report.state = INDEX_CORRUPT
        report.message = (
            f"Collection reports {report.total_chunks} chunks but returned no "
            f"readable metadata."
        )
        report.remediation = REINDEX_HINT
        return report

    stale = 0
    for metadata in metadatas:
        version = str(metadata.get("schema_version") or "").strip() or "<absent>"
        report.schema_versions[version] = report.schema_versions.get(version, 0) + 1

        key_count = len(metadata)
        report.key_count_histogram[key_count] = (
            report.key_count_histogram.get(key_count, 0) + 1
        )

        relation = str(metadata.get("standard_relation") or "").strip() or "<absent>"
        report.relation_counts[relation] = report.relation_counts.get(relation, 0) + 1

        if version != CHUNK_INDEX_SCHEMA_VERSION:
            stale += 1
            # Do not validate a chunk against a contract it never claimed to
            # meet; that would bury the one real finding under 30 field errors.
            continue

        for name in REQUIRED_FIELD_NAMES:
            if not str(metadata.get(name) or "").strip():
                report.missing_required[name] = report.missing_required.get(name, 0) + 1

        for problem in validate_chunk_index_metadata(metadata):
            if len(report.violations) < MAX_REPORTED_VIOLATIONS:
                report.violations.append(
                    f"{metadata.get('chunk_id') or '<no chunk_id>'}: {problem}"
                )

    if stale:
        report.state = INDEX_SCHEMA_MISMATCH
        observed = ", ".join(
            f"{v}={n}" for v, n in sorted(report.schema_versions.items())
        )
        report.message = (
            f"{stale} of {report.sampled} sampled chunks were written under a "
            f"schema other than {CHUNK_INDEX_SCHEMA_VERSION} (observed: {observed}). "
            f"Retrieval will return degraded provenance and identifier-aware "
            f"ranking will not fire on these chunks."
        )
        report.remediation = REINDEX_HINT
        logger.error("INDEX_SCHEMA_MISMATCH: %s %s", report.message, report.remediation)
        return report

    if report.violations or report.missing_required:
        report.state = INDEX_CORRUPT
        detail = report.violations[0] if report.violations else (
            f"missing required fields {sorted(report.missing_required)}"
        )
        report.message = (
            f"Sampled chunks claim schema {CHUNK_INDEX_SCHEMA_VERSION} but violate "
            f"it. First problem: {detail}"
        )
        report.remediation = REINDEX_HINT
        logger.error("INDEX_CORRUPT: %s %s", report.message, report.remediation)
        return report

    report.state = INDEX_READY
    report.message = (
        f"{report.total_chunks} chunks indexed; {report.sampled} sampled all at "
        f"schema {CHUNK_INDEX_SCHEMA_VERSION}."
    )
    logger.info("INDEX_READY: %s", report.message)
    return report


def log_integrity_report(report: IndexIntegrityReport) -> None:
    """
    Emit the report at a severity matching its state.

    Written as one call site so a stale index cannot be logged at DEBUG in
    one place and ERROR in another.
    """
    if report.state == INDEX_READY:
        logger.info("Vector index integrity: %s — %s", report.state, report.message)
        return

    if report.state == INDEX_EMPTY:
        logger.info("Vector index integrity: %s — %s", report.state, report.message)
        return

    logger.error(
        "Vector index integrity: %s — %s Remediation: %s",
        report.state, report.message, report.remediation,
    )
    if report.schema_versions:
        logger.error("  schema_version distribution: %s", report.schema_versions)
    if report.key_count_histogram:
        logger.error("  metadata key-count histogram: %s", report.key_count_histogram)
    for violation in report.violations:
        logger.error("  violation: %s", violation)
