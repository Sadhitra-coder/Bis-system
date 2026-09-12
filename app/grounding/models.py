"""
app/grounding/models.py

Canonical Citation, Claim, and Grounding models for Phase 8.
Ensures rigorous traceability: document -> chunk -> exact source evidence,
and deterministic validation of generated answer claims.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ClaimType(str, Enum):
    FACT = "fact"
    INTERPRETATION = "interpretation"
    UNCERTAINTY = "uncertainty"


class SupportStatus(str, Enum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    UNVERIFIABLE = "unverifiable"


class GroundingStatus(str, Enum):
    FULLY_GROUNDED = "fully_grounded"
    PARTIALLY_GROUNDED = "partially_grounded"
    UNSUPPORTED = "unsupported"
    UNVERIFIABLE = "unverifiable"


@dataclass
class Citation:
    """
    Canonical citation contract.
    Points to an exact, verified EvidenceItem in the retrieval set.
    """
    citation_id: str  # e.g. "EV1"
    chunk_id: str
    document_id: str
    source_hash: str
    source_file: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    standard_id: Optional[str] = None
    standard_number: Optional[str] = None
    standard_title: Optional[str] = None
    version_id: Optional[str] = None
    edition_or_version: Optional[str] = None
    clause_id: Optional[str] = None
    clause_title: Optional[str] = None
    source_url: Optional[str] = None
    authority: Optional[str] = None
    content_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "citation_id": self.citation_id,
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "source_hash": self.source_hash,
            "source_file": self.source_file,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "standard_id": self.standard_id,
            "standard_number": self.standard_number,
            "standard_title": self.standard_title,
            "version_id": self.version_id,
            "edition_or_version": self.edition_or_version,
            "clause_id": self.clause_id,
            "clause_title": self.clause_title,
            "source_url": self.source_url,
            "authority": self.authority,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_evidence(cls, evidence: Any, citation_id: str) -> "Citation":
        """Builds a Citation from an EvidenceItem or dict-like object."""
        meta = getattr(evidence, "metadata", {}) if hasattr(evidence, "metadata") else (evidence.get("metadata", {}) if isinstance(evidence, dict) else {})
        def g(field_name: str, default: Any = None) -> Any:
            if hasattr(evidence, field_name):
                v = getattr(evidence, field_name)
                if v is not None and v != "":
                    return v
            if isinstance(evidence, dict) and field_name in evidence:
                v = evidence[field_name]
                if v is not None and v != "":
                    return v
            if isinstance(meta, dict) and field_name in meta:
                v = meta[field_name]
                if v is not None and v != "":
                    return v
            return default

        chunk_id = str(g("chunk_id", ""))
        doc_id = str(g("document_id", ""))
        source_hash = str(g("source_hash", ""))
        source_file = str(g("source_file", ""))
        
        # Determine content hash
        content_hash = g("content_hash", None)
        if not content_hash and hasattr(evidence, "content_hash"):
            content_hash = getattr(evidence, "content_hash")

        return cls(
            citation_id=citation_id,
            chunk_id=chunk_id,
            document_id=doc_id,
            source_hash=source_hash,
            source_file=source_file,
            page_start=g("page_start", g("page_number", None)),
            page_end=g("page_end", g("page_number", None)),
            standard_id=g("standard_id", None),
            standard_number=g("standard_number", None),
            standard_title=g("standard_title", None),
            version_id=g("version_id", None),
            edition_or_version=g("edition_or_version", None),
            clause_id=g("clause_id", None),
            clause_title=g("clause_title", None),
            source_url=g("source_url", None),
            authority=g("authority", None),
            content_hash=str(content_hash) if content_hash else None,
        )


@dataclass
class AnswerClaim:
    """
    Canonical answer claim representation.
    """
    claim_id: str
    text: str
    claim_type: ClaimType = ClaimType.FACT
    citation_ids: List[str] = field(default_factory=list)
    support_status: SupportStatus = SupportStatus.UNVERIFIABLE
    validation_notes: Optional[str] = None
    supporting_citation_count: int = 0
    issues: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "claim_type": self.claim_type.value if isinstance(self.claim_type, ClaimType) else str(self.claim_type),
            "citation_ids": self.citation_ids,
            "support_status": self.support_status.value if isinstance(self.support_status, SupportStatus) else str(self.support_status),
            "validation_notes": self.validation_notes,
            "supporting_citation_count": self.supporting_citation_count,
            "issues": self.issues,
        }


@dataclass
class GroundingResult:
    """
    Outcome of post-generation grounding validation.
    """
    status: GroundingStatus
    groundedness_score: float
    citation_coverage: float
    claims: List[AnswerClaim] = field(default_factory=list)
    citations: List[Citation] = field(default_factory=list)
    supported_claim_count: int = 0
    unsupported_claim_count: int = 0
    partial_claim_count: int = 0
    claims_with_valid_citations: int = 0
    claims_without_citations: int = 0
    reason: Optional[str] = None
    unsupported_claims_summary: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "groundedness_score": round(self.groundedness_score, 4),
            "citation_coverage": round(self.citation_coverage, 4),
            "claims": [c.to_dict() for c in self.claims],
            "citations": [c.to_dict() for c in self.citations],
            "supported_claim_count": self.supported_claim_count,
            "unsupported_claim_count": self.unsupported_claim_count,
            "partial_claim_count": self.partial_claim_count,
            "claims_with_valid_citations": self.claims_with_valid_citations,
            "claims_without_citations": self.claims_without_citations,
            "reason": self.reason,
            "unsupported_claims_summary": self.unsupported_claims_summary,
        }
