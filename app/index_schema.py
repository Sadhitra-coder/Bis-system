"""
app/index_schema.py

THE canonical definition of Chroma chunk metadata.

Every chunk written to the vector index is serialized by
`build_chunk_index_metadata()` in this module. No other module may
hand-assemble index metadata. Before Phase 6 the field list was
written out by hand inside `prepare_chunks`, which meant the contract
existed only as a side effect of one function body: nothing could
validate it, nothing could version it, and a stale index was
indistinguishable from a current one.

Three separate concerns live here on purpose:

1. THE SCHEMA — `CHUNK_INDEX_FIELDS`, a declarative field list.
2. THE SERIALIZER — `build_chunk_index_metadata()`, the only writer.
3. THE VALIDATOR — `validate_chunk_index_metadata()`, run before every
   Chroma write and when auditing a persisted collection.


UNKNOWN IS NOT ZERO
-------------------
Chroma metadata values must be scalars; `None` cannot be stored. The
previous code resolved this with `int(value or 0)`, which made "page
unknown" indistinguishable from "page 0" and "year unknown"
indistinguishable from "year 0". Provenance claims built on that are
fabricated.

This module encodes unknown integers as `UNKNOWN_INT` (-1), a value no
real page number, year, offset or ordinal can take. Readers call
`decode_optional_int()` to get `None` back. Unknown strings remain `""`
and unknown tri-state booleans remain `""` — both already
distinguishable from a real value.


STANDARD IDENTITY
-----------------
`standard_id` / `version_id` are populated ONLY when the source document
IS the standard it names. A product manual that cites IS 3055 keeps
`standard_number` (the citation) and `standard_relation="reference"`,
and receives no standard identity. The decision is delegated to
`app.knowledge.normalization.classify_standard_relation` so the index
and the knowledge model can never disagree.

`clause_id` deliberately keeps its existing meaning — the clause NUMBER
as printed in the document, e.g. "4.1" — because intent-aware ranking
matches query clause numbers against it. The derived, version-scoped
graph identifier is a separate field, `knowledge_clause_id`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from app.knowledge.normalization import (
    STANDARD_RELATION_IDENTITY,
    STANDARD_RELATION_NONE,
    STANDARD_RELATION_REFERENCE,
    classify_standard_relation,
    derive_clause_id,
    derive_clause_key,
    derive_standard_id,
    derive_version_id,
    derive_version_key,
    is_document_header_section,
    normalize_standard_number,
)

logger = logging.getLogger(__name__)


# ============================================================
# SCHEMA VERSION
# ============================================================

#: Canonical version of the persisted chunk metadata contract.
#:
#: Bump this ONLY on an incompatible change: a required field added or
#: removed, a field's type changed, or the meaning of an existing value
#: changed. Adding a purely optional field that readers already tolerate
#: does not require a bump.
#:
#: History
#: -------
#: (unversioned)  Pre-Phase-2. 4 metadata keys. No provenance.
#: (unversioned)  Phase 2-4. 28 keys. Page provenance, no contextualization.
#: (unversioned)  Phase 5. 32 keys. Adds source_content /
#:                contextualized_content / context_generation_*.
#: "6.0"          Phase 6. Adds schema_version itself, standard_id,
#:                version_id, knowledge_clause_id, standard_relation,
#:                source_url. Encodes unknown integers as UNKNOWN_INT
#:                instead of 0, and is_current as a tri-state string
#:                instead of a boolean that conflated unknown with false.
#:
#: Chunks written before Phase 6 carry no schema_version at all. That
#: absence is the signal used to detect a stale index — see
#: app/index_integrity.py.
CHUNK_INDEX_SCHEMA_VERSION = "6.0"

#: Sentinel for an integer whose true value is unknown.
UNKNOWN_INT = -1

#: Sentinel for a string whose true value is unknown.
UNKNOWN_STR = ""


# ============================================================
# FIELD DECLARATIONS
# ============================================================

KIND_STR = "str"
KIND_INT = "int"


@dataclass(frozen=True)
class FieldSpec:
    """One field of the persisted chunk metadata contract."""

    name: str
    kind: str
    #: Keys to read from the source chunk, in priority order.
    #: Empty means the field is computed, not copied.
    sources: Tuple[str, ...] = ()
    #: A required field must be present AND non-empty in every chunk.
    required: bool = False
    #: Human explanation, surfaced in schema reports.
    doc: str = ""

    @property
    def unknown(self) -> Any:
        return UNKNOWN_INT if self.kind == KIND_INT else UNKNOWN_STR


CHUNK_INDEX_FIELDS: Tuple[FieldSpec, ...] = (
    # ---- contract identity ----
    FieldSpec("schema_version", KIND_STR, required=True,
              doc="Version of this metadata contract. Absence means pre-Phase-6."),

    # ---- chunk / document identity ----
    FieldSpec("chunk_id", KIND_STR, ("chunk_id",), required=True,
              doc="Content-addressed chunk identity. Primary key in Chroma."),
    FieldSpec("document_id", KIND_STR, ("document_id",), required=True,
              doc="Derived from source_hash. Identity of the source document."),
    FieldSpec("source_hash", KIND_STR, ("source_hash",),
              doc="SHA-256 of the source file bytes."),
    FieldSpec("source_file", KIND_STR, ("source_file",),
              doc="Human-facing relative path of the source document."),
    FieldSpec("content_hash", KIND_STR, ("content_hash",),
              doc="SHA-256 of this chunk's text."),
    FieldSpec("chunk_index", KIND_INT, ("chunk_index",),
              doc="Ordinal position within the document."),

    # ---- retrieval text (Phase 5) ----
    FieldSpec("source_content", KIND_STR, (), required=True,
              doc="Authoritative chunk text. Never modified. Used for grounding."),
    FieldSpec("contextualized_content", KIND_STR, (), required=True,
              doc="Retrieval representation: context prefix + source_content."),
    FieldSpec("context_generation_method", KIND_STR, (),
              doc="How the context prefix was produced: structural | llm | none."),
    FieldSpec("context_generation_version", KIND_STR, (),
              doc="Version of the context generation logic."),

    # ---- page provenance ----
    FieldSpec("page_start", KIND_INT, ("page_start", "page_number"),
              doc="First source page. UNKNOWN_INT when genuinely unknown."),
    FieldSpec("page_end", KIND_INT, ("page_end", "page_number"),
              doc="Last source page. UNKNOWN_INT when genuinely unknown."),
    FieldSpec("page_number", KIND_INT, ("page_number", "page_start"),
              doc="Legacy alias of page_start, retained for compatibility."),
    FieldSpec("char_offset_start", KIND_INT, ("char_offset_start",),
              doc="Character offset of the chunk in the normalized document."),
    FieldSpec("char_offset_end", KIND_INT, ("char_offset_end",),
              doc="End character offset in the normalized document."),

    # ---- document structure ----
    FieldSpec("section", KIND_STR, ("section",),
              doc="Heading text of the section this chunk belongs to."),
    FieldSpec("heading_context", KIND_STR, (),
              doc="Ancestor headings joined with ' > '."),
    FieldSpec("clause_id", KIND_STR, ("clause_id",),
              doc="Clause NUMBER as printed, e.g. '4.1'. Matched by intent ranking."),
    FieldSpec("clause_title", KIND_STR, ("clause_title",),
              doc="Human-readable clause heading."),

    # ---- knowledge join keys (Phase 6) ----
    FieldSpec("standard_id", KIND_STR, (),
              doc="Derived std_* identity. Empty unless the document IS a standard."),
    FieldSpec("version_id", KIND_STR, (),
              doc="Derived ver_* edition identity. Empty unless identity established."),
    FieldSpec("knowledge_clause_id", KIND_STR, (),
              doc="Derived, version-scoped cls_* identity of the owning Clause."),
    FieldSpec("standard_relation", KIND_STR, (), required=True,
              doc="identity | reference | none. How the document relates to standard_number."),

    # ---- BIS bibliographic metadata ----
    FieldSpec("standard_number", KIND_STR, ("standard_number",),
              doc="Standard number as extracted, e.g. 'IS 3055'."),
    FieldSpec("standard_title", KIND_STR, ("standard_title",),
              doc="Title of the standard."),
    FieldSpec("standard_year", KIND_INT, ("standard_year",),
              doc="Year of publication. UNKNOWN_INT when unknown."),
    FieldSpec("edition_or_version", KIND_STR, ("edition_or_version",),
              doc="Edition string, e.g. 'Third Edition'."),
    FieldSpec("part", KIND_STR, ("part", "part_number"),
              doc="Legacy part field."),
    FieldSpec("part_number", KIND_STR, ("part_number", "part"),
              doc="Part number, e.g. '1'."),
    FieldSpec("amendment", KIND_STR, ("amendment", "amendment_number"),
              doc="Legacy amendment field."),
    FieldSpec("amendment_number", KIND_STR, ("amendment_number", "amendment"),
              doc="Amendment number, e.g. '1'."),
    FieldSpec("authority", KIND_STR, ("authority",),
              doc="Issuing authority, e.g. 'BIS'."),
    FieldSpec("document_type", KIND_STR, ("document_type",),
              doc="indian_standard | amendment | guideline | draft | ..."),
    FieldSpec("source_url", KIND_STR, ("source_url",),
              doc="Canonical URL of the source document, when known."),
    FieldSpec("effective_date", KIND_STR, ("effective_date",),
              doc="Effective date string, when known."),
    FieldSpec("is_current", KIND_STR, (),
              doc="Tri-state: 'true' | 'false' | '' (unknown). Never a bare bool."),

    # ---- parser provenance ----
    FieldSpec("parser_version", KIND_STR, ("parser_version",),
              doc="Version of the ingestion parser that produced this chunk."),
)

FIELD_BY_NAME: Dict[str, FieldSpec] = {f.name: f for f in CHUNK_INDEX_FIELDS}

CHUNK_INDEX_FIELD_NAMES: Tuple[str, ...] = tuple(f.name for f in CHUNK_INDEX_FIELDS)

REQUIRED_FIELD_NAMES: Tuple[str, ...] = tuple(f.name for f in CHUNK_INDEX_FIELDS if f.required)


# ============================================================
# ENCODING / DECODING
# ============================================================

def encode_optional_int(value: Any) -> int:
    """Encode a possibly-unknown integer for storage. Unknown -> UNKNOWN_INT."""
    if value is None or value == "":
        return UNKNOWN_INT
    try:
        return int(value)
    except (TypeError, ValueError):
        return UNKNOWN_INT


def decode_optional_int(value: Any) -> Optional[int]:
    """Decode a stored integer. UNKNOWN_INT (or junk) -> None."""
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return None if parsed == UNKNOWN_INT else parsed


def encode_tristate_bool(value: Any) -> str:
    """Encode an Optional[bool] as 'true' / 'false' / '' (unknown)."""
    if value is None or value == "":
        return UNKNOWN_STR
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes"):
            return "true"
        if lowered in ("false", "0", "no"):
            return "false"
        return UNKNOWN_STR
    return "true" if bool(value) else "false"


def decode_tristate_bool(value: Any) -> Optional[bool]:
    """Decode 'true' / 'false' / '' back to Optional[bool]."""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    lowered = str(value).strip().lower()
    if lowered in ("true", "1", "yes"):
        return True
    if lowered in ("false", "0", "no"):
        return False
    return None


def encode_heading_context(value: Any) -> str:
    """Flatten a heading path to a single string. Chroma cannot store lists."""
    if value is None:
        return UNKNOWN_STR
    if isinstance(value, (list, tuple)):
        return " > ".join(str(v) for v in value if str(v).strip())
    return str(value)


def decode_heading_context(value: Any) -> List[str]:
    """Reconstruct a heading path from its flattened form."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.split(">") if part.strip()]


# ============================================================
# JOIN KEY DERIVATION
# ============================================================

@dataclass(frozen=True)
class JoinKeys:
    """
    Knowledge-model identity for one chunk.

    `relation` records WHY the identity fields are populated or empty,
    so an empty standard_id is never ambiguous between "not a standard"
    and "we failed to derive it".
    """

    relation: str
    standard_id: Optional[str] = None
    version_id: Optional[str] = None
    knowledge_clause_id: Optional[str] = None


def derive_join_keys(meta: Mapping[str, Any]) -> JoinKeys:
    """
    Derive standard / version / clause identity for a chunk.

    Reuses the canonical knowledge derivation functions verbatim. No ID
    algorithm is reimplemented here, and the version key comes from
    `derive_version_key` so it matches what KnowledgeService stores.

    Returns empty identity (with `relation` explaining why) whenever the
    document does not establish standard identity.
    """
    raw_std_num = meta.get("standard_number")
    doc_type = meta.get("document_type")

    relation = classify_standard_relation(doc_type, raw_std_num)
    if relation != STANDARD_RELATION_IDENTITY:
        return JoinKeys(relation=relation)

    norm_std_num = normalize_standard_number(str(raw_std_num))
    if not norm_std_num:
        # A standard number that survives extraction but not normalization
        # is not usable identity. Record it as a reference, not identity.
        logger.info(
            "Chunk %s: standard number %r did not normalize; treating as reference.",
            meta.get("chunk_id"), raw_std_num,
        )
        return JoinKeys(relation=STANDARD_RELATION_REFERENCE)

    standard_id = derive_standard_id(norm_std_num)

    year = meta.get("standard_year")
    if year == UNKNOWN_INT:
        year = None
    version_id = derive_version_id(
        standard_id,
        derive_version_key(meta.get("edition_or_version"), year),
    )

    # The clause key comes from the SAME canonical function KnowledgeService
    # uses, so the derived cls_* id here is byte-identical to the Clause row
    # that ingestion wrote. Anything else is a join that only looks connected.
    clause_num, heading_path = derive_clause_key(
        section=meta.get("section"),
        heading_context=meta.get("heading_context"),
        clause_number_hint=meta.get("clause_id"),
    )

    knowledge_clause_id: Optional[str] = None
    if is_document_header_section(meta.get("section"), clause_num):
        # KnowledgeService writes no Clause for a document masthead, so
        # claiming one here would leave the index pointing at a row that
        # does not exist.
        logger.debug(
            "Chunk %s is a document header; no knowledge clause claimed.",
            meta.get("chunk_id"),
        )
    else:
        knowledge_clause_id = derive_clause_id(
            standard_id, version_id, clause_num, heading_path
        )

    return JoinKeys(
        relation=STANDARD_RELATION_IDENTITY,
        standard_id=standard_id,
        version_id=version_id,
        knowledge_clause_id=knowledge_clause_id,
    )


# ============================================================
# SERIALIZER — the only writer of index metadata
# ============================================================

def _read_source(meta: Mapping[str, Any], chunk: Mapping[str, Any], spec: FieldSpec) -> Any:
    """Read a field from the chunk, honouring source-key priority order."""
    for key in spec.sources:
        for container in (meta, chunk):
            if key in container:
                value = container[key]
                if value is not None and value != "":
                    return value
    return None


def build_chunk_index_metadata(
    chunk: Mapping[str, Any],
    *,
    source_content: str,
    contextualized_content: str,
    context_generation_method: str,
    context_generation_version: str,
) -> Dict[str, Any]:
    """
    Serialize one chunk into persisted index metadata.

    The caller supplies the contextual-retrieval outputs because
    producing them may invoke an LLM, which is not this module's job.
    Everything else is derived from the canonical schema.

    Every field in CHUNK_INDEX_FIELDS is present in the result, with
    unknown values encoded as UNKNOWN_INT / UNKNOWN_STR rather than
    fabricated as 0 / False.
    """
    meta: Mapping[str, Any] = chunk.get("metadata") or chunk

    metadata: Dict[str, Any] = {}

    for spec in CHUNK_INDEX_FIELDS:
        if spec.sources:
            raw = _read_source(meta, chunk, spec)
            if spec.kind == KIND_INT:
                metadata[spec.name] = encode_optional_int(raw)
            else:
                metadata[spec.name] = UNKNOWN_STR if raw is None else str(raw)
        else:
            metadata[spec.name] = spec.unknown  # filled in below

    # ---- computed fields ----
    metadata["schema_version"] = CHUNK_INDEX_SCHEMA_VERSION
    metadata["heading_context"] = encode_heading_context(
        meta.get("heading_context") or chunk.get("heading_context")
    )
    metadata["is_current"] = encode_tristate_bool(
        meta.get("is_current") if "is_current" in meta else chunk.get("is_current")
    )

    metadata["source_content"] = str(source_content)
    metadata["contextualized_content"] = str(contextualized_content)
    metadata["context_generation_method"] = str(context_generation_method)
    metadata["context_generation_version"] = str(context_generation_version)

    # ---- knowledge join keys ----
    keys = derive_join_keys(metadata)
    metadata["standard_relation"] = keys.relation
    metadata["standard_id"] = keys.standard_id or UNKNOWN_STR
    metadata["version_id"] = keys.version_id or UNKNOWN_STR
    metadata["knowledge_clause_id"] = keys.knowledge_clause_id or UNKNOWN_STR

    return metadata


# ============================================================
# VALIDATOR — run before every write, and when auditing
# ============================================================

class IndexMetadataError(ValueError):
    """Raised when metadata cannot legally be written to the index."""


def validate_chunk_index_metadata(metadata: Mapping[str, Any]) -> List[str]:
    """
    Return a list of contract violations. Empty list means valid.

    Checks, in order:
      - every declared field is present
      - no unexpected extra fields
      - required fields are non-empty
      - schema_version matches the current contract
      - value types match the declared kind (Chroma-storable scalars only)
      - page range is not inverted
      - standard / version relationship is internally consistent
    """
    problems: List[str] = []

    missing = [name for name in CHUNK_INDEX_FIELD_NAMES if name not in metadata]
    if missing:
        problems.append(f"missing fields: {sorted(missing)}")

    extra = [name for name in metadata if name not in FIELD_BY_NAME]
    if extra:
        problems.append(f"unexpected fields: {sorted(extra)}")

    for name in REQUIRED_FIELD_NAMES:
        if name in metadata and (metadata[name] is None or metadata[name] == ""):
            problems.append(f"required field '{name}' is empty")

    version = metadata.get("schema_version")
    if version is not None and version != CHUNK_INDEX_SCHEMA_VERSION:
        problems.append(
            f"schema_version {version!r} != current {CHUNK_INDEX_SCHEMA_VERSION!r}"
        )

    for spec in CHUNK_INDEX_FIELDS:
        if spec.name not in metadata:
            continue
        value = metadata[spec.name]
        if value is None:
            problems.append(f"field '{spec.name}' is None; Chroma cannot store null")
            continue
        if spec.kind == KIND_INT:
            if isinstance(value, bool) or not isinstance(value, int):
                problems.append(
                    f"field '{spec.name}' must be int, got {type(value).__name__}"
                )
        elif not isinstance(value, str):
            problems.append(
                f"field '{spec.name}' must be str, got {type(value).__name__}"
            )

    start = decode_optional_int(metadata.get("page_start"))
    end = decode_optional_int(metadata.get("page_end"))
    if start is not None and start < 1:
        problems.append(f"page_start {start} is not a valid page number")
    if end is not None and end < 1:
        problems.append(f"page_end {end} is not a valid page number")
    if start is not None and end is not None and end < start:
        problems.append(f"inverted page range: page_start={start} > page_end={end}")

    relation = metadata.get("standard_relation")
    std_id = metadata.get("standard_id") or ""
    ver_id = metadata.get("version_id") or ""
    if relation == STANDARD_RELATION_IDENTITY:
        if not std_id:
            problems.append("standard_relation='identity' but standard_id is empty")
        if not ver_id:
            problems.append("standard_relation='identity' but version_id is empty")
    elif relation in (STANDARD_RELATION_NONE, STANDARD_RELATION_REFERENCE):
        if std_id:
            problems.append(
                f"standard_relation={relation!r} must not carry standard_id {std_id!r}"
            )
        if ver_id:
            problems.append(
                f"standard_relation={relation!r} must not carry version_id {ver_id!r}"
            )
    elif relation is not None:
        problems.append(f"unknown standard_relation {relation!r}")

    if ver_id and not std_id:
        problems.append("version_id present without standard_id")
    if std_id and ver_id and not ver_id.startswith(f"ver_{std_id}_"):
        problems.append(
            f"version_id {ver_id!r} does not belong to standard_id {std_id!r}"
        )

    return problems


def describe_schema() -> Dict[str, Any]:
    """Machine-readable schema description, for integrity reports."""
    return {
        "schema_version": CHUNK_INDEX_SCHEMA_VERSION,
        "field_count": len(CHUNK_INDEX_FIELDS),
        "required_fields": list(REQUIRED_FIELD_NAMES),
        "unknown_int_sentinel": UNKNOWN_INT,
        "fields": [
            {"name": f.name, "kind": f.kind, "required": f.required, "doc": f.doc}
            for f in CHUNK_INDEX_FIELDS
        ],
    }
