"""
app/steps/provenance.py

Provenance validation and quality reporting utilities.
Does NOT modify data. Flags issues only.
"""
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

REQUIRED_CHUNK_FIELDS = ["chunk_id", "document_id", "source_file", "content"]


def validate_chunk(chunk: Dict[str, Any]) -> List[str]:
    """
    Return a list of provenance issues for a single chunk.
    Empty list means no issues found.
    """
    issues = []
    meta = chunk.get("metadata") or chunk

    for field in REQUIRED_CHUNK_FIELDS:
        val = chunk.get(field) or meta.get(field)
        if not val:
            issues.append(f"missing_required_field:{field}")

    # page sanity
    page_start = meta.get("page_start") if meta.get("page_start") is not None else chunk.get("page_start")
    page_end = meta.get("page_end") if meta.get("page_end") is not None else chunk.get("page_end")
    if page_start is not None and page_end is not None:
        try:
            if int(page_start) > int(page_end):
                issues.append(f"invalid_page_range:page_start={page_start}>page_end={page_end}")
            if int(page_start) <= 0 or int(page_end) <= 0:
                issues.append(f"invalid_page_number:page_start={page_start},page_end={page_end}")
        except (TypeError, ValueError):
            issues.append("invalid_page_values")

    # chunk_id must be non-empty
    cid = chunk.get("chunk_id") or meta.get("chunk_id", "")
    if not str(cid).strip():
        issues.append("empty_chunk_id")

    return issues


def validate_chunks(chunks: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """
    Validate all chunks. Returns {chunk_id: [issues]} for chunks with issues.
    """
    report = {}
    for chunk in chunks:
        issues = validate_chunk(chunk)
        if issues:
            cid = chunk.get("chunk_id", "<unknown>")
            report[str(cid)] = issues
    return report


def provenance_quality_report(chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Generate a provenance quality summary for an ingestion run.
    Observability tool - does not modify chunks.

    Accurately distinguishes:
    - page provenance: physical page location in document
    - clause provenance: clause identification (e.g. 4.1)
    - standard identity: formal standard identifier (e.g. IS 3055)
    - version metadata: amendment or edition information
    """
    total = len(chunks)
    with_page = 0
    without_page = 0
    with_clause = 0
    with_standard = 0
    with_version = 0

    for chunk in chunks:
        meta = chunk.get("metadata") or chunk
        page = meta.get("page_start") or meta.get("page_number") or chunk.get("page_number")
        clause = meta.get("clause_id") or chunk.get("clause_id")
        std = meta.get("standard_number") or chunk.get("standard_number")
        ver = meta.get("edition_or_version") or meta.get("amendment_number")

        if page:
            with_page += 1
        else:
            without_page += 1
        if clause:
            with_clause += 1
        if std:
            with_standard += 1
        if ver:
            with_version += 1

    page_pct = round((with_page / total * 100), 1) if total else 0.0
    clause_pct = round((with_clause / total * 100), 1) if total else 0.0
    standard_pct = round((with_standard / total * 100), 1) if total else 0.0
    version_pct = round((with_version / total * 100), 1) if total else 0.0

    return {
        "chunks_created": total,
        "chunks_with_page_provenance": with_page,
        "chunks_without_page_provenance": without_page,
        "chunks_with_clause_ids": with_clause,
        "chunks_with_standard_numbers": with_standard,
        "chunks_with_versions": with_version,
        "page_provenance_pct": page_pct,
        "clause_provenance_pct": clause_pct,
        "standard_identity_pct": standard_pct,
        "version_metadata_pct": version_pct,
        # Backward compatibility alias
        "provenance_completeness_pct": page_pct,
    }
