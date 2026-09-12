"""
app/evidence/models.py

Canonical Evidence representation for the BIS compliance intelligence backend.
Consolidates provenance, document identity, version-scoped clause identity,
retrieval signals, ranking reasons, and authoritative source content into a
single unified, citable evidence structure.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import re
from typing import Any, Dict, Iterator, List, Optional


def compute_provenance_completeness(
    document_id: Optional[str],
    source_file: Optional[str],
    page_start: Optional[int],
    standard_number: Optional[str] = None,
    clause_id: Optional[str] = None,
) -> float:
    """
    Deterministically computes a normalized provenance completeness score in [0.0, 1.0].

    Weighted components:
      - Document traceability (document_id + source_file): 0.25
      - Page provenance (page_start > 0): 0.25
      - Standard identity (standard_number): 0.25
      - Clause/Structural identity (clause_id): 0.25
    """
    score = 0.0
    if document_id and str(document_id).strip() and source_file and str(source_file).strip():
        score += 0.25
    if page_start is not None and isinstance(page_start, (int, float)) and page_start > 0:
        score += 0.25
    if standard_number and str(standard_number).strip():
        score += 0.25
    if clause_id and str(clause_id).strip():
        score += 0.25
    return round(score, 2)


@dataclass
class EvidenceItem(Mapping):
    """
    Canonical Evidence representation.

    Guarantees access to:
      1. Source provenance (document_id, source_file, source_hash, page range)
      2. Domain knowledge identity (standard_id, version_id, clause_id)
      3. Authoritative content (unadorned source text)
      4. Retrieval & Ranking signals (dense/bm25 ranks, fusion score, reranker score)
      5. Quality metrics (provenance completeness, authority, document type)
    """

    # Provenance
    chunk_id: str
    document_id: str
    source_hash: str = ""
    source_file: str = ""
    page_start: Optional[int] = None
    page_end: Optional[int] = None

    # Content
    content: str = ""  # Authoritative source content
    source_content: str = ""
    contextualized_content: Optional[str] = None

    # Knowledge & Structure
    standard_id: Optional[str] = None
    standard_number: Optional[str] = None
    standard_title: Optional[str] = None
    version_id: Optional[str] = None
    edition_or_version: Optional[str] = None
    amendment_number: Optional[str] = None
    standard_year: Optional[int] = None
    part_number: Optional[str] = None

    section: Optional[str] = None
    heading_context: List[str] = field(default_factory=list)
    clause_id: Optional[str] = None
    clause_title: Optional[str] = None

    # Retrieval signals
    retrieval_methods: List[str] = field(default_factory=list)
    dense_rank: Optional[int] = None
    bm25_rank: Optional[int] = None
    fusion_score: float = 0.0
    reranker_score: Optional[float] = None
    identifier_match: bool = False
    ranking_reason: Optional[str] = None

    # Authority & Integrity
    provenance_completeness: float = 0.0
    authority: Optional[str] = None
    document_type: Optional[str] = None
    standard_relation: Optional[str] = None
    knowledge_resolved: bool = False

    # Raw metadata dict for observability
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Authoritative content rule: Prefer source_content if available
        if self.source_content and not self.content:
            self.content = self.source_content
        elif self.content and not self.source_content:
            self.source_content = self.content

        # Compute provenance completeness if not explicitly set
        if self.provenance_completeness == 0.0:
            self.provenance_completeness = compute_provenance_completeness(
                document_id=self.document_id,
                source_file=self.source_file,
                page_start=self.page_start,
                standard_number=self.standard_number,
                clause_id=self.clause_id,
            )

    @property
    def content_hash(self) -> str:
        """Deterministic SHA-256 digest of authoritative source text."""
        raw = (self.source_content or self.content or "").encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceItem":
        """Build EvidenceItem from a RetrievalResult or arbitrary dict."""
        meta = data.get("metadata", {}) if isinstance(data.get("metadata"), Mapping) else {}

        def _get(field_name: str, default: Any = None) -> Any:
            val = getattr(data, field_name, None)
            if val is not None and val != "":
                return val
            val = data.get(field_name)
            if val is not None and val != "":
                return val
            val = meta.get(field_name)
            if val is not None and val != "":
                return val
            return default

        # Clean integer helpers
        def _int_val(field_name: str) -> Optional[int]:
            v = _get(field_name)
            if v is None or v == "":
                return None
            try:
                iv = int(v)
                return iv if iv >= 0 else None
            except (ValueError, TypeError):
                return None

        # Clean float helpers
        def _float_val(field_name: str, default: float = 0.0) -> float:
            v = _get(field_name)
            if v is None or v == "":
                return default
            try:
                return float(v)
            except (ValueError, TypeError):
                return default

        heading_ctx = _get("heading_context", [])
        if isinstance(heading_ctx, str):
            heading_ctx = [p.strip() for p in heading_ctx.split(">") if p.strip()]
        elif not isinstance(heading_ctx, list):
            heading_ctx = []

        ret_methods = _get("retrieval_methods", [])
        if isinstance(ret_methods, str):
            ret_methods = [m.strip() for m in ret_methods.split(",") if m.strip()]
        elif not isinstance(ret_methods, list):
            ret_methods = []

        src_content = str(_get("source_content", "") or "")
        content = str(_get("content", "") or "")
        authoritative = src_content or content

        p_start = _int_val("page_start") or _int_val("page_number")
        p_end = _int_val("page_end") or p_start

        return cls(
            chunk_id=str(_get("chunk_id", "") or ""),
            document_id=str(_get("document_id", "") or ""),
            source_hash=str(_get("source_hash", "") or ""),
            source_file=str(_get("source_file", "") or ""),
            page_start=p_start,
            page_end=p_end,
            content=authoritative,
            source_content=src_content or authoritative,
            contextualized_content=_get("contextualized_content"),
            standard_id=_get("standard_id"),
            standard_number=_get("standard_number"),
            standard_title=_get("standard_title"),
            version_id=_get("version_id"),
            edition_or_version=_get("edition_or_version"),
            amendment_number=_get("amendment_number"),
            standard_year=_int_val("standard_year"),
            part_number=_get("part_number"),
            section=_get("section"),
            heading_context=heading_ctx,
            clause_id=_get("clause_id"),
            clause_title=_get("clause_title"),
            retrieval_methods=ret_methods,
            dense_rank=_int_val("dense_rank"),
            bm25_rank=_int_val("bm25_rank"),
            fusion_score=_float_val("fusion_score", 0.0),
            reranker_score=_get("reranker_score"),
            identifier_match=bool(_get("identifier_match", False)),
            ranking_reason=_get("ranking_reason"),
            provenance_completeness=_float_val("provenance_completeness", 0.0),
            authority=_get("authority"),
            document_type=_get("document_type"),
            standard_relation=_get("standard_relation"),
            knowledge_resolved=bool(_get("knowledge_resolved", False)),
            metadata=dict(meta),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert EvidenceItem to standard dict representation."""
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "source_hash": self.source_hash,
            "source_file": self.source_file,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "content": self.content,
            "source_content": self.source_content,
            "contextualized_content": self.contextualized_content,
            "standard_id": self.standard_id,
            "standard_number": self.standard_number,
            "standard_title": self.standard_title,
            "version_id": self.version_id,
            "edition_or_version": self.edition_or_version,
            "amendment_number": self.amendment_number,
            "standard_year": self.standard_year,
            "part_number": self.part_number,
            "section": self.section,
            "heading_context": list(self.heading_context),
            "clause_id": self.clause_id,
            "clause_title": self.clause_title,
            "retrieval_methods": list(self.retrieval_methods),
            "dense_rank": self.dense_rank,
            "bm25_rank": self.bm25_rank,
            "fusion_score": self.fusion_score,
            "reranker_score": self.reranker_score,
            "identifier_match": self.identifier_match,
            "ranking_reason": self.ranking_reason,
            "provenance_completeness": self.provenance_completeness,
            "authority": self.authority,
            "document_type": self.document_type,
            "standard_relation": self.standard_relation,
            "knowledge_resolved": self.knowledge_resolved,
            "content_hash": self.content_hash,
            "metadata": dict(self.metadata),
        }

    # --- Mapping ABC protocol for backward compatibility ---
    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict().keys())

    def __len__(self) -> int:
        return len(self.to_dict())

    def get(self, key: str, default: Any = None) -> Any:
        try:
            val = getattr(self, key)
            return val if val is not None else default
        except AttributeError:
            return self.metadata.get(key, default)
