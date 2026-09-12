"""
app/knowledge/join.py

The knowledge <-> retrieval join.

Chroma is the retrieval index. SQLite is the domain source of truth. This
module is the only bridge between them, and it crosses that bridge using
explicit identifiers only:

    Standard  --std_*-->  StandardVersion  --ver_*-->  Clause  --cls_*-->  Chunk

WHY EXPLICIT IDS AND NOTHING ELSE
---------------------------------
The obvious shortcut is to match a chunk to a standard by comparing
standard_number strings. That is wrong in ways that do not show up in
tests:

  * 'IS 3055' and 'IS 3055-1' are different standards that share a prefix.
  * The same standard number spans multiple editions. A string match cannot
    say WHICH StandardVersion a chunk belongs to, which is precisely the
    question retrieval has to answer.
  * Clause '4.1' exists in every edition of every standard. Only a
    version-scoped cls_* id distinguishes them.

So the join reads the ids the index writer already derived
(app.index_schema.derive_join_keys) from the same canonical functions the
knowledge service used when it wrote the rows. Both sides compute the same
bytes or the join fails loudly here — it never degrades into a fuzzy guess.

WHAT 'FAILS LOUDLY' MEANS
-------------------------
A chunk that claims no standard identity is not a defect. A product manual
citing IS 3055 legitimately has standard_relation='reference' and no
standard_id. That is `unlinked`.

A chunk that claims identity but whose rows are absent from SQLite IS a
defect — the index and the domain model have diverged. That is `dangling`,
and it is reported rather than silently rendered as an unlinked chunk. The
readiness check surfaces it; retrieval keeps working with degraded
provenance instead of crashing.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

from app.knowledge.models import Clause, Standard, StandardVersion
from app.knowledge.normalization import (
    STANDARD_RELATION_IDENTITY,
    STANDARD_RELATION_NONE,
)
from app.knowledge.repository import KnowledgeRepository, default_repository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metadata keys this module reads.
#
# These mirror app.index_schema.CHUNK_INDEX_FIELDS. They are restated rather
# than imported because app.index_schema imports app.knowledge.normalization,
# and app/knowledge/__init__.py imports this module — importing index_schema
# here would close that loop into a genuine circular import.
#
# tests/test_knowledge_join.py asserts every name below is a declared field of
# the canonical schema, so a rename cannot silently desynchronize the two.
# ---------------------------------------------------------------------------

KEY_CHUNK_ID = "chunk_id"
KEY_DOCUMENT_ID = "document_id"
KEY_STANDARD_ID = "standard_id"
KEY_VERSION_ID = "version_id"
KEY_KNOWLEDGE_CLAUSE_ID = "knowledge_clause_id"
KEY_STANDARD_RELATION = "standard_relation"

JOIN_METADATA_KEYS = (
    KEY_CHUNK_ID,
    KEY_DOCUMENT_ID,
    KEY_STANDARD_ID,
    KEY_VERSION_ID,
    KEY_KNOWLEDGE_CLAUSE_ID,
    KEY_STANDARD_RELATION,
)


#: Identity claimed and every claimed row was found.
RESOLUTION_RESOLVED = "resolved"

#: No identity claimed. Legitimate for non-standard documents.
RESOLUTION_UNLINKED = "unlinked"

#: Identity claimed but the knowledge rows are missing. An integrity defect.
RESOLUTION_DANGLING = "dangling"


@dataclass
class ChunkKnowledgeLink:
    """
    The resolved knowledge position of one retrieved chunk.

    `resolution` is the field callers should branch on. `standard`,
    `version` and `clause` are the hydrated domain objects, present only
    when the corresponding row actually exists in SQLite.
    """

    chunk_id: Optional[str]
    document_id: Optional[str]
    relation: str
    resolution: str
    standard_id: Optional[str] = None
    version_id: Optional[str] = None
    clause_id: Optional[str] = None
    standard: Optional[Standard] = None
    version: Optional[StandardVersion] = None
    clause: Optional[Clause] = None
    #: Which claimed ids could not be found. Empty unless resolution is dangling.
    missing: List[str] = field(default_factory=list)

    @property
    def is_resolved(self) -> bool:
        return self.resolution == RESOLUTION_RESOLVED

    @property
    def is_dangling(self) -> bool:
        return self.resolution == RESOLUTION_DANGLING

    def describe(self) -> str:
        """Human-readable position, for logs and integrity reports."""
        if self.resolution == RESOLUTION_UNLINKED:
            return f"{self.chunk_id}: unlinked (relation={self.relation})"
        if self.resolution == RESOLUTION_DANGLING:
            return f"{self.chunk_id}: dangling, missing {', '.join(self.missing)}"
        parts = []
        if self.standard is not None:
            parts.append(self.standard.standard_number)
        if self.version is not None:
            parts.append(self.version.edition or str(self.version.standard_year or "?"))
        if self.clause is not None:
            parts.append(f"Clause {self.clause.clause_number or self.clause.clause_title}")
        return f"{self.chunk_id}: " + " / ".join(p for p in parts if p)


def _clean(value: Any) -> Optional[str]:
    """Index metadata encodes 'absent' as an empty string, never as null."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def resolve_chunk(
    metadata: Mapping[str, Any],
    repo: Optional[KnowledgeRepository] = None,
) -> ChunkKnowledgeLink:
    """
    Answer 'which StandardVersion and Clause does this chunk belong to?'

    `metadata` is the persisted Chroma metadata dict (or a RetrievalResult,
    which is a Mapping over the same keys). No string matching is performed:
    if the ids are absent the chunk is reported unlinked, and if they are
    present but unresolvable it is reported dangling.
    """
    repo = repo or default_repository

    chunk_id = _clean(metadata.get(KEY_CHUNK_ID))
    document_id = _clean(metadata.get(KEY_DOCUMENT_ID))
    relation = _clean(metadata.get(KEY_STANDARD_RELATION)) or STANDARD_RELATION_NONE

    standard_id = _clean(metadata.get(KEY_STANDARD_ID))
    version_id = _clean(metadata.get(KEY_VERSION_ID))
    clause_id = _clean(metadata.get(KEY_KNOWLEDGE_CLAUSE_ID))

    link = ChunkKnowledgeLink(
        chunk_id=chunk_id,
        document_id=document_id,
        relation=relation,
        resolution=RESOLUTION_UNLINKED,
        standard_id=standard_id,
        version_id=version_id,
        clause_id=clause_id,
    )

    # No identity claimed. This is the correct, expected state for a manual,
    # a Gazette order, or any document that merely cites a standard.
    if relation != STANDARD_RELATION_IDENTITY or not standard_id:
        if standard_id or version_id:
            # Identity fields without an identity relation means the writer
            # and the classifier disagreed — flag it rather than trusting it.
            link.resolution = RESOLUTION_DANGLING
            link.missing = [f"relation={relation!r} carries identity ids"]
        return link

    missing: List[str] = []

    link.standard = repo.get_standard(standard_id)
    if link.standard is None:
        missing.append(f"standard {standard_id}")

    if version_id:
        link.version = repo.get_version(version_id)
        if link.version is None:
            missing.append(f"version {version_id}")
    else:
        missing.append("version_id absent under identity relation")

    # An empty clause id is legitimate: a document masthead chunk owns no
    # normative clause, and the knowledge service deliberately writes no
    # Clause row for it. Only a claimed-but-absent clause is a defect.
    if clause_id:
        link.clause = repo.get_clause(clause_id)
        if link.clause is None:
            missing.append(f"clause {clause_id}")

    if missing:
        link.resolution = RESOLUTION_DANGLING
        link.missing = missing
        logger.warning(
            "Chunk %s claims standard identity but knowledge rows are missing: %s",
            chunk_id, "; ".join(missing),
        )
    else:
        link.resolution = RESOLUTION_RESOLVED

    return link


def resolve_results(
    results: Iterable[Mapping[str, Any]],
    repo: Optional[KnowledgeRepository] = None,
) -> List[ChunkKnowledgeLink]:
    """Resolve a list of retrieval results in order."""
    repo = repo or default_repository
    return [resolve_chunk(result, repo=repo) for result in results]


def summarize_links(links: Iterable[ChunkKnowledgeLink]) -> Dict[str, Any]:
    """
    Aggregate resolution outcomes.

    Used by the re-index integrity report and the readiness check, which
    care about the dangling count specifically: unlinked chunks are normal,
    dangling chunks mean the index and the knowledge model have diverged.
    """
    counts = {
        RESOLUTION_RESOLVED: 0,
        RESOLUTION_UNLINKED: 0,
        RESOLUTION_DANGLING: 0,
    }
    dangling: List[str] = []

    total = 0
    for link in links:
        total += 1
        counts[link.resolution] = counts.get(link.resolution, 0) + 1
        if link.is_dangling:
            dangling.append(link.describe())

    return {
        "total": total,
        "resolved": counts[RESOLUTION_RESOLVED],
        "unlinked": counts[RESOLUTION_UNLINKED],
        "dangling": counts[RESOLUTION_DANGLING],
        "dangling_details": dangling,
    }


# ---------------------------------------------------------------------------
# Reverse direction: knowledge -> chunks
#
# Clause.source_chunk_ids is written by the knowledge service during
# ingestion, so the graph can be walked downward to retrieval without
# scanning the vector store.
# ---------------------------------------------------------------------------


def chunk_ids_for_clause(
    clause_id: str,
    repo: Optional[KnowledgeRepository] = None,
) -> List[str]:
    """Chunk ids belonging to one clause. Empty list if the clause is unknown."""
    repo = repo or default_repository
    clause = repo.get_clause(clause_id)
    if clause is None:
        logger.debug("No clause row for %s", clause_id)
        return []
    return list(clause.source_chunk_ids or [])


def chunk_ids_for_version(
    standard_id: str,
    version_id: Optional[str] = None,
    repo: Optional[KnowledgeRepository] = None,
) -> List[str]:
    """
    Chunk ids belonging to one StandardVersion, in clause order.

    Passing version_id=None yields every version of the standard, which is
    what a version-agnostic query ('what does IS 3055 say about X') needs.
    Duplicates are removed while preserving first-seen order.
    """
    repo = repo or default_repository
    seen = set()
    ordered: List[str] = []
    for clause in repo.get_standard_clauses(standard_id, version_id=version_id):
        for chunk_id in clause.source_chunk_ids or []:
            if chunk_id and chunk_id not in seen:
                seen.add(chunk_id)
                ordered.append(chunk_id)
    return ordered


def resolve_version_for_chunk(
    metadata: Mapping[str, Any],
    repo: Optional[KnowledgeRepository] = None,
) -> Optional[StandardVersion]:
    """
    The single question section 5 of the work order names explicitly:
    'Which StandardVersion does this chunk belong to?'

    Returns None when the chunk claims no identity or the row is missing.
    Callers needing to tell those two cases apart should use resolve_chunk.
    """
    return resolve_chunk(metadata, repo=repo).version
