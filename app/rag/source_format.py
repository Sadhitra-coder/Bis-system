"""
app/rag/source_format.py

One definition of "what a retrieved source is called".

THE BUG THIS REPLACES
---------------------
`AnswerGenerator.format_context` and `RAGPipeline._build_sources` each read
their own idea of the metadata contract:

    metadata.get("standard")    # never written by any writer
    metadata.get("title")       # never written by any writer

The canonical fields are `standard_number` and `standard_title`. Both call
sites therefore resolved to None on every chunk ever indexed, which meant:

  * the LLM grounding context never told the model WHICH standard a passage
    came from, so it could not attribute a requirement to IS 3055 rather
    than to some other standard in the same context window; and
  * the API `sources` array never carried standard identity at all, so a
    caller could not cite the answer.

Two independent copies of a formatting rule is how that happened. This
module is the single copy: identity is extracted once, and the LLM block
and the API object are two renderings of the same extracted value.

IDENTITY VS CITATION
--------------------
`standard_relation` distinguishes a document that IS a standard from one
that merely cites one (see app/knowledge/normalization.py). The rendering
respects that difference — a pump manual quoting IS 3055 is labelled as
referencing it, not as being it — because a source line reading
"Standard: IS 3055" on a manual would invite exactly the false attribution
the knowledge model is designed to prevent.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from app.index_schema import UNKNOWN_INT

#: standard_relation values, restated to avoid importing the knowledge
#: package into the RAG layer for two string constants.
RELATION_IDENTITY = "identity"
RELATION_REFERENCE = "reference"


def _text(*values: Any) -> Optional[str]:
    """
    First genuinely present string among the candidates.

    The index encodes absence as an empty string (Chroma cannot store null),
    so "" is absence and must not become the literal text "".
    """
    for value in values:
        if value is None:
            continue
        text = value.strip() if isinstance(value, str) else str(value).strip()
        if text:
            return text
    return None


def _number(*values: Any) -> Optional[int]:
    """
    First genuinely present integer, decoding the unknown sentinel.

    UNKNOWN_INT (-1) means "not known"; 0 is a real value and is kept.
    """
    for value in values:
        if value is None or value == "":
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number == UNKNOWN_INT:
            continue
        return number
    return None


@dataclass(frozen=True)
class SourceIdentity:
    """
    Everything needed to name and cite one retrieved passage.

    Absent values are None, never "" and never 0, so a caller can tell
    "page unknown" from "page 0" and "no clause" from "clause ''".
    """

    document_id: Optional[str] = None
    source_file: Optional[str] = None
    section: Optional[str] = None
    clause_id: Optional[str] = None
    clause_title: Optional[str] = None
    standard_number: Optional[str] = None
    standard_title: Optional[str] = None
    standard_year: Optional[int] = None
    edition_or_version: Optional[str] = None
    part_number: Optional[str] = None
    amendment_number: Optional[str] = None
    authority: Optional[str] = None
    document_type: Optional[str] = None
    source_url: Optional[str] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    standard_id: Optional[str] = None
    version_id: Optional[str] = None
    standard_relation: str = "none"
    chunk_id: Optional[str] = None

    # -- semantics -------------------------------------------------------

    @property
    def is_standard(self) -> bool:
        """The passage comes from the standard itself, not from a citation."""
        return self.standard_relation == RELATION_IDENTITY

    @property
    def cites_standard(self) -> bool:
        return (
            self.standard_relation == RELATION_REFERENCE
            and self.standard_number is not None
        )

    @property
    def has_any_identity(self) -> bool:
        return bool(
            self.standard_number
            or self.standard_title
            or self.document_id
            or self.source_file
        )

    # -- rendering -------------------------------------------------------

    def standard_label(self) -> Optional[str]:
        """
        The standard as a human would write it: 'IS 3055 (Part 2) : 2024'.

        None when no standard number is known — an unnumbered document must
        not be given a fabricated designation.
        """
        if not self.standard_number:
            return None
        label = self.standard_number
        if self.part_number:
            label = f"{label} (Part {self.part_number})"
        if self.standard_year is not None:
            label = f"{label} : {self.standard_year}"
        return label

    def locator(self) -> Optional[str]:
        """Where in the document: 'Clause 4.1, p. 7-8'."""
        parts: List[str] = []
        if self.clause_id:
            parts.append(f"Clause {self.clause_id}")
        page = self.page_label()
        if page:
            parts.append(page)
        return ", ".join(parts) if parts else None

    def page_label(self) -> Optional[str]:
        """'p. 7' or 'p. 7-9'. None when page provenance is unknown."""
        if self.page_start is None and self.page_end is None:
            return None
        start = self.page_start if self.page_start is not None else self.page_end
        end = self.page_end if self.page_end is not None else self.page_start
        if start == end:
            return f"p. {start}"
        return f"p. {start}-{end}"

    def citation(self) -> str:
        """
        One-line citation, e.g.
        'IS 3055 (Part 2) : 2024, Third Edition, Clause 4.1, p. 7'.

        Falls back through title, then filename, then chunk id, so a source
        is always identifiable even without standard identity.

        The lead segment depends on relation. `standard_title` is only the
        document's own title when the document IS the standard; on a manual
        that merely cites IS 3055, the extractor may have picked up the
        CITED standard's title, and leading with it would present the
        manual's wording under the standard's name. For a citing document
        the file or document id leads instead, and the citation ends by
        naming the standard it references — stating the relationship rather
        than impersonating it.
        """
        segments: List[str] = []
        label = self.standard_label()

        if self.is_standard:
            lead = label or self.standard_title
        else:
            lead = self.source_file or self.document_id or self.standard_title

        if not lead:
            return self.chunk_id or "unidentified source"
        segments.append(lead)

        if self.edition_or_version and self.is_standard:
            segments.append(self.edition_or_version)
        if self.amendment_number:
            segments.append(f"Amendment {self.amendment_number}")

        locator = self.locator()
        if locator:
            segments.append(locator)

        if label and not self.is_standard:
            segments.append(f"references {label}")

        return ", ".join(segments)

    def dedup_key(self) -> Tuple:
        """
        Identity for collapsing duplicate sources.

        Includes page range and clause: two passages from the same section
        on different pages are genuinely different citations, and merging
        them would misattribute a requirement to the wrong page.
        """
        return (
            self.document_id,
            self.standard_number,
            self.version_id,
            self.section,
            self.clause_id,
            self.page_start,
            self.page_end,
            self.source_file,
        )


def extract_source_identity(result: Mapping[str, Any]) -> SourceIdentity:
    """
    Read identity from one retrieval result.

    Results arrive as RetrievalResult.to_dict() — canonical field names at
    the top level, with the raw index metadata retained under "metadata".
    Top level wins; metadata is the fallback for chunks that came through a
    path which did not build a full RetrievalResult.
    """
    if not isinstance(result, Mapping):
        return SourceIdentity()

    meta = result.get("metadata")
    if not isinstance(meta, Mapping):
        meta = {}

    def s(*names: str) -> Optional[str]:
        candidates: List[Any] = []
        for name in names:
            candidates.append(result.get(name))
            candidates.append(meta.get(name))
        return _text(*candidates)

    def n(*names: str) -> Optional[int]:
        candidates: List[Any] = []
        for name in names:
            candidates.append(result.get(name))
            candidates.append(meta.get(name))
        return _number(*candidates)

    return SourceIdentity(
        document_id=s("document_id"),
        source_file=s("source_file"),
        section=s("section"),
        clause_id=s("clause_id"),
        clause_title=s("clause_title"),
        standard_number=s("standard_number"),
        standard_title=s("standard_title"),
        standard_year=n("standard_year"),
        edition_or_version=s("edition_or_version"),
        part_number=s("part_number"),
        amendment_number=s("amendment_number"),
        authority=s("authority"),
        document_type=s("document_type"),
        source_url=s("source_url"),
        page_start=n("page_start"),
        page_end=n("page_end"),
        standard_id=s("standard_id"),
        version_id=s("version_id"),
        standard_relation=s("standard_relation") or "none",
        chunk_id=s("chunk_id"),
    )


def format_source_header(identity: SourceIdentity) -> str:
    """
    The provenance block placed above a passage in the LLM grounding context.

    Field labels are explicit rather than a compact citation string because
    the model must be able to attribute a specific requirement to a specific
    standard, edition and clause when several standards share a context
    window. 'Standard' is used only where identity was established; a
    citation is labelled as such.
    """
    lines: List[str] = []

    label = identity.standard_label()
    if label:
        if identity.is_standard:
            lines.append(f"Standard: {label}")
        else:
            lines.append(f"References standard: {label}")

    if identity.standard_title:
        # Labelled by relation for the same reason the line above is: an
        # unqualified "Title:" under "References standard:" reads as the
        # citing document's own title.
        if identity.is_standard or not label:
            lines.append(f"Title: {identity.standard_title}")
        else:
            lines.append(f"Referenced standard title: {identity.standard_title}")

    if identity.edition_or_version and identity.is_standard:
        lines.append(f"Edition: {identity.edition_or_version}")

    if identity.amendment_number:
        lines.append(f"Amendment: {identity.amendment_number}")

    if identity.clause_id:
        clause = identity.clause_id
        if identity.clause_title:
            clause = f"{clause} {identity.clause_title}"
        lines.append(f"Clause: {clause}")

    if identity.section:
        lines.append(f"Section: {identity.section}")

    page = identity.page_label()
    if page:
        lines.append(f"Page: {page}")

    if identity.authority:
        lines.append(f"Authority: {identity.authority}")

    if identity.document_id:
        lines.append(f"Document: {identity.document_id}")

    if identity.source_file:
        lines.append(f"Source file: {identity.source_file}")

    if not lines:
        return "Source information not available."

    return "\n".join(lines)


def source_to_dict(identity: SourceIdentity) -> Dict[str, Any]:
    """
    The API `sources[]` object.

    Only genuinely known values are included, so an absent field means
    "unknown" rather than "empty". `standard_relation` is always present:
    a consumer must be able to tell a standard from a document citing one
    without inspecting the other fields.
    """
    source: Dict[str, Any] = {"standard_relation": identity.standard_relation}

    for key, value in (
        ("document_id", identity.document_id),
        ("source_file", identity.source_file),
        ("section", identity.section),
        ("clause_id", identity.clause_id),
        ("clause_title", identity.clause_title),
        ("standard_number", identity.standard_number),
        ("standard_title", identity.standard_title),
        ("standard_year", identity.standard_year),
        ("edition_or_version", identity.edition_or_version),
        ("part_number", identity.part_number),
        ("amendment_number", identity.amendment_number),
        ("authority", identity.authority),
        ("document_type", identity.document_type),
        ("source_url", identity.source_url),
        ("page_start", identity.page_start),
        ("page_end", identity.page_end),
        ("standard_id", identity.standard_id),
        ("version_id", identity.version_id),
        ("chunk_id", identity.chunk_id),
    ):
        if value is not None:
            source[key] = value

    # Aliases for frontend/consumer compatibility
    if identity.page_start is not None:
        source["page_number"] = identity.page_start
    if identity.clause_id is not None:
        source["clause_number"] = identity.clause_id

    source["citation"] = identity.citation()
    return source


def build_sources(results: Any) -> List[Dict[str, Any]]:
    """
    Deduplicated API source objects for a list of retrieval results.

    Results carrying no identifying information at all are dropped: an
    object consisting solely of a relation tells a caller nothing.
    """
    sources: List[Dict[str, Any]] = []
    seen = set()

    for result in results or []:
        if not isinstance(result, Mapping):
            continue
        identity = extract_source_identity(result)
        if not identity.has_any_identity:
            continue
        key = identity.dedup_key()
        if key in seen:
            continue
        seen.add(key)
        sources.append(source_to_dict(identity))

    return sources
