"""
app/tender_analysis/__init__.py

Public interfaces for Tender / Customer Specification Gap Analyzer (Phase 13).
"""

from app.tender_analysis.models import (
    StandardLinkState,
    TenderDocument,
    TenderGapAnalysisReport,
    TenderGapState,
    TenderRequirement,
    TenderStandardMatch,
    TripartiteComparison,
)

from app.tender_analysis.extractor import (
    extract_tender_document,
    extract_tender_requirement_from_clause,
    infer_tender_category,
)

from app.tender_analysis.matcher import (
    analyze_tender_gaps,
    build_tripartite_comparison,
    match_tender_requirement_to_standards,
)

__all__ = [
    "StandardLinkState",
    "TenderDocument",
    "TenderGapAnalysisReport",
    "TenderGapState",
    "TenderRequirement",
    "TenderStandardMatch",
    "TripartiteComparison",
    "extract_tender_document",
    "extract_tender_requirement_from_clause",
    "infer_tender_category",
    "analyze_tender_gaps",
    "build_tripartite_comparison",
    "match_tender_requirement_to_standards",
]
