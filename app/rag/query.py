"""
app/rag/query.py

Phase 4 — Query Normalization, BIS Entity Extraction, and Retrieval Result Contract.

DESIGN PRINCIPLES:
  - Never modify the user's original query text (original_query is preserved).
  - Normalization is conservative: whitespace and obvious OCR artifacts only.
  - Entity extraction is conservative: only emit fields with high-confidence patterns.
  - RetrievalResult is the ONE canonical result schema for all downstream consumers.
  - Unicode (Devanagari, Hindi) is preserved — normalization never corrupts it.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ============================================================
# CANONICAL RETRIEVAL RESULT
# ============================================================

@dataclass
class RetrievalResult(Mapping):
    """
    Canonical schema for one retrieved knowledge passage.

    Provenance fields:
        chunk_id, document_id, source_hash, source_file,
        page_start, page_end

    Content:
        content

    Structure:
        section, heading_context, clause_id, clause_title

    Standard identity:
        standard_id, standard_number, standard_title,
        version_id, edition_or_version, amendment_number,
        standard_year, part_number, authority, document_type,
        source_url

    Retrieval signals:
        retrieval_methods  — which methods produced this candidate ('dense', 'bm25', 'identifier')
        dense_rank         — rank in dense results (1-based; None if not retrieved by dense)
        bm25_rank          — rank in BM25 results (1-based; None if not retrieved by BM25)
        identifier_match   — True if identifier-aware path boosted this result
        fusion_score       — RRF score after fusion + identifier boost
        reranker_score     — CrossEncoder score (None until reranking is run)

    Observability:
        metadata           — raw Chroma metadata dict, preserved for debugging

    Compatibility:
        Inherits from Mapping so both object attribute access (result.chunk_id)
        and dict subscript access (result["chunk_id"]) work seamlessly.
    """
    # Provenance
    chunk_id: str = ""
    document_id: str = ""
    source_hash: str = ""
    source_file: str = ""
    page_start: Optional[int] = None
    page_end: Optional[int] = None

    # Content (authoritative source content)
    content: str = ""
    source_content: str = ""
    contextualized_content: Optional[str] = None
    context_generation_method: Optional[str] = None
    context_generation_version: Optional[str] = None

    # Structure
    section: str = ""
    heading_context: List[str] = field(default_factory=list)
    clause_id: Optional[str] = None
    clause_title: Optional[str] = None

    # Standard identity
    standard_id: Optional[str] = None
    standard_number: Optional[str] = None
    standard_title: Optional[str] = None
    version_id: Optional[str] = None
    edition_or_version: Optional[str] = None
    amendment_number: Optional[str] = None
    standard_year: Optional[int] = None
    part_number: Optional[str] = None
    authority: Optional[str] = None
    document_type: Optional[str] = None
    source_url: Optional[str] = None

    # Retrieval signals
    retrieval_methods: List[str] = field(default_factory=list)
    dense_rank: Optional[int] = None
    bm25_rank: Optional[int] = None
    identifier_match: bool = False
    fusion_score: float = 0.0
    reranker_score: Optional[float] = None
    ranking_reason: Optional[str] = None

    # Raw metadata for debugging
    metadata: Dict[str, Any] = field(default_factory=dict)


    # ---------------- Mapping Protocol Implementation ----------------
    def __getitem__(self, key: str) -> Any:
        d = self.to_dict()
        if key in d:
            return d[key]
        if key in self.metadata:
            return self.metadata[key]
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if hasattr(self, key):
            setattr(self, key, value)
        else:
            self.metadata[key] = value

    def __iter__(self):
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def __contains__(self, key: object) -> bool:
        return key in self.to_dict() or (isinstance(key, str) and key in self.metadata)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain dict for API responses and test assertions."""
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "source_hash": self.source_hash,
            "source_file": self.source_file,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "content": self.content,
            "source_content": self.source_content or self.content,
            "contextualized_content": self.contextualized_content,
            "context_generation_method": self.context_generation_method,
            "context_generation_version": self.context_generation_version,
            "section": self.section,
            "heading_context": self.heading_context,
            "clause_id": self.clause_id,
            "clause_title": self.clause_title,
            "standard_id": self.standard_id,
            "standard_number": self.standard_number,
            "standard_title": self.standard_title,
            "version_id": self.version_id,
            "edition_or_version": self.edition_or_version,
            "amendment_number": self.amendment_number,
            "standard_year": self.standard_year,
            "part_number": self.part_number,
            "authority": self.authority,
            "document_type": self.document_type,
            "source_url": self.source_url,
            "retrieval_methods": list(self.retrieval_methods),
            "dense_rank": self.dense_rank,
            "bm25_rank": self.bm25_rank,
            "identifier_match": self.identifier_match,
            "fusion_score": self.fusion_score,
            "reranker_score": self.reranker_score,
            "ranking_reason": self.ranking_reason,
            # Backward-compatible aliases
            "score": self.reranker_score if self.reranker_score is not None else self.fusion_score,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RetrievalResult":
        """Reconstruct from a plain dict (for testing convenience)."""
        content = d.get("content", "")
        return cls(
            chunk_id=d.get("chunk_id", ""),
            document_id=d.get("document_id", ""),
            source_hash=d.get("source_hash", ""),
            source_file=d.get("source_file", ""),
            page_start=d.get("page_start"),
            page_end=d.get("page_end"),
            content=content,
            source_content=d.get("source_content") or content,
            contextualized_content=d.get("contextualized_content"),
            context_generation_method=d.get("context_generation_method"),
            context_generation_version=d.get("context_generation_version"),
            section=d.get("section", ""),
            heading_context=d.get("heading_context") or [],
            clause_id=d.get("clause_id"),
            clause_title=d.get("clause_title"),
            standard_id=d.get("standard_id"),
            standard_number=d.get("standard_number"),
            standard_title=d.get("standard_title"),
            version_id=d.get("version_id"),
            edition_or_version=d.get("edition_or_version"),
            amendment_number=d.get("amendment_number"),
            standard_year=d.get("standard_year"),
            part_number=d.get("part_number"),
            authority=d.get("authority"),
            document_type=d.get("document_type"),
            source_url=d.get("source_url"),
            retrieval_methods=d.get("retrieval_methods") or [],
            dense_rank=d.get("dense_rank"),
            bm25_rank=d.get("bm25_rank"),
            identifier_match=d.get("identifier_match", False),
            fusion_score=d.get("fusion_score", 0.0),
            reranker_score=d.get("reranker_score"),
            ranking_reason=d.get("ranking_reason"),
            metadata=d.get("metadata") or {},
        )


# ============================================================
# RETRIEVAL TRACE
# ============================================================

@dataclass
class RetrievalTrace:
    """
    Lightweight diagnostic trace of the full retrieval pipeline.
    Not exposed on public endpoints by default.
    Available via debug/service interface.
    """
    original_query: str = ""
    normalized_query: str = ""
    strategy: Optional[str] = None
    entities: Dict[str, Any] = field(default_factory=dict)
    filters: Dict[str, Any] = field(default_factory=dict)
    dense_candidates: List[Dict] = field(default_factory=list)
    bm25_candidates: List[Dict] = field(default_factory=list)
    rrf_candidates: List[Dict] = field(default_factory=list)
    boosted_candidates: List[Dict] = field(default_factory=list)
    reranked_candidates: List[Dict] = field(default_factory=list)
    # Phase 5 contextual trace
    contextual_representation_used: bool = True
    context_mode: str = "structural"
    context_generation_version: str = "1.0"
    dense_latency_ms: float = 0.0
    bm25_latency_ms: float = 0.0
    rrf_latency_ms: float = 0.0
    boost_latency_ms: float = 0.0
    rerank_latency_ms: float = 0.0
    contextualization_latency_ms: float = 0.0
    total_latency_ms: float = 0.0



# ============================================================
# EXTRACTED QUERY ENTITIES
# ============================================================

@dataclass
class QueryEntities:
    """
    Structured BIS identifiers extracted from a query.
    All fields are Optional — None means not detected.
    Never extracts arbitrary numbers as BIS identifiers.
    """
    standard_number: Optional[str] = None   # e.g. "IS 3055"
    standard_year: Optional[int] = None     # e.g. 2024
    part_number: Optional[str] = None       # e.g. "1"
    clause_id: Optional[str] = None         # e.g. "4.1"
    amendment_number: Optional[str] = None  # e.g. "1"
    edition_or_version: Optional[str] = None # e.g. "Third Edition"
    # Phase 9 temporal qualifiers
    relative_temporal: Optional[str] = None # e.g. "current", "latest", "historical", "previous edition"
    temporal_intent: Optional[str] = None   # "current" | "historical" | "exact_version" | "exact_amendment" | "unspecified"

    def has_any(self) -> bool:
        has_temporal = bool(self.relative_temporal) or (bool(self.temporal_intent) and self.temporal_intent not in (None, "unspecified"))
        return any([
            self.standard_number, self.standard_year, self.part_number,
            self.clause_id, self.amendment_number, self.edition_or_version,
            has_temporal,
        ])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "standard_number": self.standard_number,
            "standard_year": self.standard_year,
            "part_number": self.part_number,
            "clause_id": self.clause_id,
            "amendment_number": self.amendment_number,
            "edition_or_version": self.edition_or_version,
            "relative_temporal": self.relative_temporal,
            "temporal_intent": self.temporal_intent,
        }


# ============================================================
# QUERY NORMALIZATION
# ============================================================

# Conservative: only normalize spacing issues and IS-number formatting.
# Never rewrite natural-language content.
# Never destroy Unicode (Hindi/Devanagari is preserved).

# OCR artifacts: "IS3055" "IS-3055" → "IS 3055"
_IS_NO_SPACE = re.compile(r'\bIS\s*[-:]?\s*(\d+)', re.IGNORECASE)
# Colon spacing: "IS 3055:2024" → "IS 3055 : 2024"
_IS_COLON_YEAR = re.compile(r'\bIS\s+(\d+)\s*:\s*(\d{4})\b', re.IGNORECASE)
# Clause normalization: "clause4.1" → "clause 4.1"
_CLAUSE_NO_SPACE = re.compile(r'\b(clause|section)\s*(\d+(?:\.\d+)*)', re.IGNORECASE)
# Amendment normalization: "AMD1" → "Amendment 1"
_AMD_COMPACT = re.compile(r'\bAMD\s*\.?\s*(\d+)\b', re.IGNORECASE)
# Multiple whitespace → single space (Unicode-safe: only collapses ASCII whitespace runs)
_MULTI_SPACE = re.compile(r'[ \t]+')


def normalize_query(query: str) -> str:
    """
    Normalize a BIS query for improved retrieval without destroying content.

    Rules applied (in order):
    1. Strip leading/trailing whitespace
    2. Collapse consecutive spaces/tabs to one space
    3. Normalize IS-number spacing ("IS3055" → "IS 3055")
    4. Normalize IS colon-year format ("IS 3055:2024" → "IS 3055 : 2024")
    5. Normalize clause keyword spacing ("clause4.1" → "clause 4.1")
    6. Normalize AMD abbreviation ("AMD1" → "Amendment 1")

    NOT applied:
    - Lowercase (preserves proper nouns and Unicode)
    - Punctuation stripping (preserves clause dots, Devanagari punctuation)
    - Any rewrite of natural-language content
    """
    if not query:
        return ""

    q = query.strip()

    # Step 1: normalize IS spacing (before colon-year so both patterns work)
    q = _IS_NO_SPACE.sub(lambda m: f"IS {m.group(1)}", q)
    # Step 2: normalize IS colon-year
    q = _IS_COLON_YEAR.sub(lambda m: f"IS {m.group(1)} : {m.group(2)}", q)
    # Step 3: clause spacing
    q = _CLAUSE_NO_SPACE.sub(lambda m: f"{m.group(1).capitalize()} {m.group(2)}", q)
    # Step 4: AMD abbreviation
    q = _AMD_COMPACT.sub(lambda m: f"Amendment {m.group(1)}", q)
    # Step 5: collapse multiple spaces/tabs (not newlines — preserves structure if needed)
    q = _MULTI_SPACE.sub(' ', q).strip()

    return q


# ============================================================
# BIS ENTITY EXTRACTION
# ============================================================

# IS number: IS 3055, IS 3055-1, IS 3055 (Part 1), IS No. 3055, IS 3055 : 2024, IS 3055 2024
_IS_PATTERN = re.compile(
    r'\bIS(?:\s+No\.?)?\s+(\d+)(?:\s*[-]\s*(\d+(?:-\d+)*)|\s*\(Part\s*(\d+)\))?'
    r'(?:\s*[:\s]+\s*(\d{4}))?',
    re.IGNORECASE
)

# Year standalone: " : 2024" or "2024" after IS number (captured by IS_PATTERN)
_YEAR_PATTERN = re.compile(r'\b(19|20)\d{2}\b')

# Clause: "clause 4.1", "Clause 4.1.2", "section 4", "4.1 of IS", leading numeric
_CLAUSE_PATTERN = re.compile(
    r'(?:(?:clause|section)\s+(\d+(?:\.\d+)*)|\b(\d+(?:\.\d+){1,3})\s+(?:of\s+IS|requirements|specifications))',
    re.IGNORECASE
)

# Amendment: "Amendment 1", "Amd. 2", "AMD 3"
_AMD_PATTERN = re.compile(r'\b(?:Amendment|Amd\.?)\s*(\d+)\b', re.IGNORECASE)

# Edition: "Third Edition", "2nd Edition", "First Edition"
_EDITION_PATTERN = re.compile(
    r'\b(First|Second|Third|Fourth|Fifth|Sixth|\d+(?:st|nd|rd|th)?)\s+Edition\b',
    re.IGNORECASE
)

# Part number (standalone after "Part")
_PART_PATTERN = re.compile(r'\bPart\s+(\d+)\b', re.IGNORECASE)

# Relative temporal patterns (Section 14)
_HISTORICAL_PATTERN = re.compile(
    r'\b(?:previous\s+edition|previous\s+version|earlier\s+edition|earlier\s+version|old\s+edition|old\s+version|superseded|prior\s+edition|former\s+version)\b',
    re.IGNORECASE
)
_CURRENT_PATTERN = re.compile(
    r'\b(?:current|latest|present|active|in\s+force|currently|newest)\b',
    re.IGNORECASE
)


def extract_query_entities(query: str) -> QueryEntities:
    """
    Extract BIS-specific identifiers from a (normalized) query.

    Conservative rules:
    - IS numbers must match the IS + digits pattern explicitly
    - Years must be 4-digit in plausible BIS range (1947-2099)
    - Clause numbers: only extracted when prefixed by "clause"/"section" keyword
      OR when appearing as "X.Y of IS"
    - Arbitrary digit sequences are NOT classified as IS numbers or clauses
    - Temporal words: "latest", "current", "previous edition", etc.
    """
    entities = QueryEntities()

    if not query:
        return entities

    # Standard number (and optionally year + part)
    m = _IS_PATTERN.search(query)
    if m:
        num = m.group(1)
        part_dash = m.group(2)
        part_paren = m.group(3)
        year_str = m.group(4)

        part = part_dash or part_paren
        if part:
            entities.standard_number = f"IS {num}-{part}"
            entities.part_number = part
        else:
            entities.standard_number = f"IS {num}"

        if year_str:
            y = int(year_str)
            if 1947 <= y <= 2099:
                entities.standard_year = y
        elif not entities.standard_year:
            ym = _YEAR_PATTERN.search(query)
            if ym:
                y = int(ym.group(0))
                if 1947 <= y <= 2099:
                    entities.standard_year = y

    # Clause
    m = _CLAUSE_PATTERN.search(query)
    if m:
        clause_num = m.group(1) or m.group(2)
        if clause_num:
            entities.clause_id = clause_num

    # Amendment
    m = _AMD_PATTERN.search(query)
    if m:
        entities.amendment_number = m.group(1)

    # Edition
    m = _EDITION_PATTERN.search(query)
    if m:
        entities.edition_or_version = m.group(0).strip()

    # Standalone part (if not captured from IS pattern)
    if not entities.part_number:
        m = _PART_PATTERN.search(query)
        if m:
            entities.part_number = m.group(1)

    # Relative temporal words & intent (Phase 9)
    if _HISTORICAL_PATTERN.search(query):
        entities.relative_temporal = "historical"
        entities.temporal_intent = "historical"
    elif _CURRENT_PATTERN.search(query):
        entities.relative_temporal = "current"
        entities.temporal_intent = "current"
    elif entities.standard_year is not None:
        entities.temporal_intent = "exact_version"
    elif entities.edition_or_version is not None:
        entities.temporal_intent = "exact_edition"
    elif entities.amendment_number is not None:
        entities.temporal_intent = "exact_amendment"
    else:
        entities.temporal_intent = "unspecified"

    return entities


# ============================================================
# METADATA FILTER BUILDER
# ============================================================

# Semantic distinctions:
#   HARD CONSTRAINT — must match; reduces Chroma result set
#   SOFT BOOST — influences ranking; not a hard filter
#   INFORMATIONAL — extracted but not used for filtering
#
# Current strategy:
#   standard_number → SOFT BOOST (used in identifier boosting, not Chroma where clause)
#     Reason: Chroma metadata filters are string-exact; normalization variants
#             ("IS 3055" vs "IS3055") could cause false negatives.
#   standard_year → SOFT BOOST
#   clause_id → SOFT BOOST
#   amendment_number → SOFT BOOST
#
# Hard Chroma filters (from explicit caller-supplied constraints):
#   authority, document_type — when explicitly constrained by API caller

def build_chroma_filters(
    authority: Optional[str] = None,
    document_type: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Build Chroma 'where' clause for HARD constraints only.

    Only authority and document_type are hard-filtered automatically.
    Standard number, year, clause — these are soft signals used in boosting.

    Returns None if no hard filters apply.
    """
    conditions = []

    if authority:
        conditions.append({"authority": {"$eq": authority}})
    if document_type:
        conditions.append({"document_type": {"$eq": document_type}})

    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def build_full_normalized_query(original_query: str) -> Dict[str, Any]:
    """
    Entry point for the query pre-processing layer.

    Returns a dict with:
        original_query   — unmodified user input
        normalized_query — normalized for retrieval
        entities         — extracted BIS identifiers
        filters          — Chroma hard filters (may be None)
    """
    normalized = normalize_query(original_query)
    entities = extract_query_entities(normalized)
    return {
        "original_query": original_query,
        "normalized_query": normalized,
        "entities": entities,
        "filters": None,  # hard filters populated by caller if needed
    }
