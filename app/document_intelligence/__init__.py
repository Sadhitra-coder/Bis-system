"""
app/document_intelligence/__init__.py

Public API for Phase 14 Document Intelligence + Requirement Matching.
"""

from app.document_intelligence.classifier import (
    classify_document,
)
from app.document_intelligence.extractor import (
    extract_business_document,
    extract_dates,
    extract_document_number,
    extract_issuer,
    extract_standard_references,
    extract_test_results,
    parse_date_safely,
)
from app.document_intelligence.matcher import (
    build_evidence_gap_report,
    evaluate_requirement_against_documents,
)
from app.document_intelligence.models import (
    BusinessDocument,
    DocumentMatchStatus,
    DocumentRequirementMatch,
    DocumentType,
    EvidenceGapReport,
)

__all__ = [
    "BusinessDocument",
    "DocumentMatchStatus",
    "DocumentRequirementMatch",
    "DocumentType",
    "EvidenceGapReport",
    "classify_document",
    "extract_business_document",
    "extract_dates",
    "extract_document_number",
    "extract_issuer",
    "extract_standard_references",
    "extract_test_results",
    "parse_date_safely",
    "evaluate_requirement_against_documents",
    "build_evidence_gap_report",
]
