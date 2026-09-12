"""
app/knowledge/normalization.py

Conservative normalization and deterministic identity generation for BIS entities.
Never alters identity-bearing semantic information.
Always retains original text where appropriate.
"""

import hashlib
import re
from typing import Any, List, Optional, Tuple


def normalize_standard_number(raw_text: str) -> str:
    """
    Normalize Indian Standard designations to canonical format: 'IS <digits>[-<part>]'.

    Examples:
        'IS 15644 : 2024'   -> 'IS 15644'
        'IS15644:2024'      -> 'IS 15644'
        'IS 3055-1 : 2020'  -> 'IS 3055-1'
        'IS 3055 (Part 1)'  -> 'IS 3055-1'
        'IS No. 3055'       -> 'IS 3055'
    """
    if not raw_text or not raw_text.strip():
        return ""

    m = re.search(
        r'\bIS(?:\s+No\.?)?\s*(\d+)(?:\s*(?:-|\(Part\s*)\s*(\d+)\)?)?',
        raw_text.strip(),
        re.IGNORECASE
    )
    if m:
        num = m.group(1)
        part = m.group(2)
        if part:
            return f"IS {num}-{part}"
        return f"IS {num}"

    # Clean redundant whitespace if pattern didn't match cleanly
    cleaned = re.sub(r'\s+', ' ', raw_text.strip())
    return cleaned


def normalize_clause_number(raw_text: Optional[str]) -> Optional[str]:
    """
    Extract and normalize hierarchical clause number (e.g. '4.1.2').

    Examples:
        'Clause 4.1.2'  -> '4.1.2'
        '4.1.2.'        -> '4.1.2'
        'Section 5'     -> '5'
        'Preamble'      -> None
    """
    if not raw_text or not raw_text.strip():
        return None

    m = re.match(r'^(?:Clause\s+|Section\s+)?(\d+(?:\.\d+)*)', raw_text.strip(), re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def derive_clause_hierarchy(clause_number: Optional[str], heading_level: int = 1) -> Tuple[int, Optional[str]]:
    """
    Derive the structural level and parent clause number from a clause number.

    Examples:
        '4'     -> (1, None)
        '4.1'   -> (2, '4')
        '4.1.2' -> (3, '4.1')
        None    -> (heading_level, None)
    """
    if clause_number and re.match(r'^\d+(\.\d+)*$', clause_number):
        parts = clause_number.split('.')
        level = len(parts)
        parent_number = '.'.join(parts[:-1]) if len(parts) > 1 else None
        return level, parent_number

    return max(1, heading_level), None


# ============================================================
# ID SEMANTICS DOCUMENTATION
# ============================================================
#
# The following ID derivation functions implement a deliberate
# semantic separation between four identity concepts:
#
#   standard_id
#       = LOGICAL identity of the Standard (e.g. "IS 3055").
#         Derived from the NORMALIZED standard number only.
#         Identical for every source document that refers to the
#         same standard, regardless of when or how it was ingested.
#         Example: std_IS_3055_6371bc8d00ea4611
#
#   version_id
#       = Identity of a specific EDITION or YEAR of a standard.
#         Derived from standard_id + edition/year string.
#         Different editions of the same standard → different version_id.
#         Example: ver_std_IS_3055_..._ThirdEdition
#
#   clause_id
#       = Identity of a CLAUSE within a specific VERSION of a standard.
#         Derived from standard_id + version_id + clause number/path.
#         SAME clause number across different versions → different clause_id.
#         This prevents cross-version collisions when Clause 4 is revised.
#
#   source_hash / document_id
#       = Identity of SOURCE BYTES / SOURCE FILE respectively.
#         source_hash = SHA-256 of file contents (exact-byte identity).
#         document_id = Derived from source_hash for traceability.
#         These are NOT used for knowledge-object IDs; they are metadata
#         for provenance and idempotency only.
#
# ============================================================


# ============================================================
# STANDARD IDENTITY vs STANDARD REFERENCE
# ============================================================
#
# A document that MENTIONS "IS 3055" is not necessarily the
# standard IS 3055. A product manual, a Gazette order, a tender
# document and a training deck all routinely cite IS numbers.
#
# The conservative BIS extractor searches the document header for
# an IS pattern, so `standard_number` alone is evidence of a
# CITATION, not of identity. Promoting a citation to identity
# fabricates a Standard entity and silently corrupts the
# knowledge model (two unrelated documents collapse into one
# logical standard).
#
# Identity therefore requires BOTH:
#   1. an explicit standard number, AND
#   2. a document type that affirmatively declares the document
#      to be a standard (or an amendment to one).
#
# Everything else keeps `standard_number` as a recorded reference
# and receives no standard_id / version_id.
# ============================================================

#: Document types that affirmatively declare standard identity.
STANDARD_IDENTITY_DOCUMENT_TYPES = frozenset({
    "indian_standard",
    "amendment",
    "draft",
})

#: Relationship a document has to the standard number it carries.
STANDARD_RELATION_IDENTITY = "identity"
STANDARD_RELATION_REFERENCE = "reference"
STANDARD_RELATION_NONE = "none"


def classify_standard_relation(
    document_type: Optional[str],
    standard_number: Optional[str],
) -> str:
    """
    Classify how a document relates to the standard number it carries.

    Returns one of:
        STANDARD_RELATION_NONE      — no standard number present at all.
        STANDARD_RELATION_IDENTITY  — the document IS the standard.
        STANDARD_RELATION_REFERENCE — the document cites a standard.

    This is the single decision point for standard identity. Both the
    knowledge service and the vector-index serializer call it, so the
    knowledge model and the retrieval index can never disagree about
    whether a document is a standard.
    """
    if not (standard_number or "").strip():
        return STANDARD_RELATION_NONE
    if (document_type or "").strip().lower() in STANDARD_IDENTITY_DOCUMENT_TYPES:
        return STANDARD_RELATION_IDENTITY
    return STANDARD_RELATION_REFERENCE


def establishes_standard_identity(
    document_type: Optional[str],
    standard_number: Optional[str],
) -> bool:
    """True only when the document IS the standard it names."""
    return classify_standard_relation(document_type, standard_number) == STANDARD_RELATION_IDENTITY


def derive_version_key(
    edition_or_version: Optional[str],
    standard_year: Optional[int] = None,
) -> str:
    """
    Build the edition/year key that identifies a standard version.

    This exists so that the knowledge service and the vector-index
    serializer derive version_id from the SAME input expression.
    Divergence here silently breaks the knowledge <-> retrieval join
    while every unit test still passes, so the expression lives in
    exactly one place.
    """
    edition = (edition_or_version or "").strip()
    if edition:
        return edition
    if standard_year:
        return str(standard_year)
    return "v1"


def _as_heading_list(heading_context: Any) -> List[str]:
    """
    Coerce a heading context into a list of headings.

    The knowledge service receives it as a list. The vector index stores
    it flattened to a ' > '-joined string because Chroma metadata cannot
    hold lists. Both must produce the SAME list here, or the two callers
    derive different clause keys from the same chunk.
    """
    if heading_context is None:
        return []
    if isinstance(heading_context, (list, tuple)):
        return [str(h) for h in heading_context if str(h).strip()]
    text = str(heading_context).strip()
    if not text:
        return []
    return [part.strip() for part in text.split(">") if part.strip()]


def derive_clause_key(
    section: Optional[str],
    heading_context: Any = None,
    clause_number_hint: Optional[str] = None,
) -> Tuple[Optional[str], str]:
    """
    Build the (clause_number, heading_path) pair that identifies a clause.

    Returns exactly the two arguments `derive_clause_id` consumes, so the
    knowledge service and the vector-index serializer cannot drift apart.
    The membership test below is deliberately against the heading LIST,
    not the joined string: 'Intro' is a substring of 'Introduction' but
    is not the same heading, and a substring test would silently produce
    a different heading_path — and therefore a different clause_id — for
    the same chunk depending on which caller asked.
    """
    sec_heading = (section or "").strip() or "Section"
    ctx = _as_heading_list(heading_context)

    clause_number = normalize_clause_number(
        (clause_number_hint or "").strip() or sec_heading
    )

    if ctx:
        heading_path = " > ".join(ctx)
        if sec_heading and sec_heading not in ctx:
            heading_path += f" > {sec_heading}"
    else:
        heading_path = sec_heading

    return clause_number, heading_path


#: A section heading that is really the document masthead, e.g. 'IS 3055 : 2024'.
_DOCUMENT_HEADER_RE = re.compile(r'^\s*IS\s*\d+', re.IGNORECASE)


def is_document_header_section(
    section: Optional[str],
    clause_number: Optional[str],
) -> bool:
    """
    True when a section heading is the document masthead, not a clause.

    The knowledge service refuses to create a Clause for these, so the
    vector index must refuse to claim a knowledge_clause_id for them too.
    Otherwise the index points at a Clause row that was never written and
    the join dangles.
    """
    if clause_number:
        return False
    return bool(section and _DOCUMENT_HEADER_RE.search(section))


def derive_standard_id(normalized_standard_number: str) -> str:
    """
    Stable logical identifier for a standard (IS XXXX).

    Derived from the normalized standard number only — NOT from source bytes.
    Two different source documents covering the same IS number share
    the same standard_id. This is intentional: a Standard is a logical
    entity, not a physical document.
    """
    clean = re.sub(r'[^a-zA-Z0-9_-]', '_', normalized_standard_number.strip().upper())
    h = hashlib.sha256(normalized_standard_number.strip().encode('utf-8')).hexdigest()[:16]
    return f"std_{clean}_{h}"


def derive_version_id(standard_id: str, edition_or_year: Optional[str]) -> str:
    """
    Stable identifier for a specific edition or year of a standard.

    Different editions → different version_id.
    Same edition re-ingested → same version_id (idempotent).
    NOT derived from source bytes — two PDFs of the same edition
    produce the same version_id.
    """
    raw = (edition_or_year or "v1").strip().lower()
    h = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]
    return f"ver_{standard_id}_{h}"


def derive_part_id(standard_id: str, part_number: str) -> str:
    """Stable deterministic identifier for a standard part."""
    clean_part = re.sub(r'[^a-zA-Z0-9_-]', '_', str(part_number).strip())
    return f"prt_{standard_id}_{clean_part}"


def derive_clause_id(standard_id: str, version_id: str, clause_number: Optional[str], heading_path: str) -> str:
    """
    Stable version-scoped identifier for a clause within a specific standard version.

    IMPORTANT: version_id is included in the key so that Clause 4 in Edition 2
    and Clause 4 in Edition 3 of the same standard produce DIFFERENT clause_ids.
    This prevents cross-version collisions on INSERT OR REPLACE.

    Key components:
        standard_id   — logical standard identity
        version_id    — version/edition identity
        clause_number — normalized numeric clause (e.g. '4.1'), or None
        heading_path  — fallback when clause_number is absent
    """
    key = f"{version_id}::{clause_number if clause_number else heading_path}"
    h = hashlib.sha256(key.strip().encode('utf-8')).hexdigest()[:16]
    return f"cls_{standard_id}_{h}"


def derive_amendment_id(standard_id: str, amendment_number: str) -> str:
    """Stable deterministic identifier for an amendment."""
    clean_num = re.sub(r'[^a-zA-Z0-9_-]', '_', str(amendment_number).strip())
    return f"amd_{standard_id}_{clean_num}"


def derive_reference_id(
    source_standard_id: str,
    target_standard_number: str,
    source_clause_id: Optional[str] = None
) -> str:
    """Stable deterministic identifier for a citation / reference relationship."""
    key = f"{source_standard_id}->{target_standard_number}:{source_clause_id or ''}"
    h = hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]
    return f"ref_{h}"
