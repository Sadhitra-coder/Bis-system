"""
app/technical_specs/__init__.py

Public interfaces for Technical Specification Analyzer (Phase 12).
"""

from app.technical_specs.models import (
    ComparisonOperator,
    MatchState,
    SpecificationComparisonResult,
    StandardTechnicalRequirement,
    TechnicalAnalysisReport,
    TechnicalParameter,
    TechnicalSpecification,
)

from app.technical_specs.extractor import (
    are_units_compatible,
    extract_parameter_from_text,
    extract_requirements_from_clause_text,
    extract_technical_specification,
    get_unit_details,
    normalize_numeric_value,
    normalize_unit_string,
)

from app.technical_specs.matcher import (
    analyze_technical_specification,
    compare_parameter_to_requirement,
)

__all__ = [
    "ComparisonOperator",
    "MatchState",
    "SpecificationComparisonResult",
    "StandardTechnicalRequirement",
    "TechnicalAnalysisReport",
    "TechnicalParameter",
    "TechnicalSpecification",
    "are_units_compatible",
    "extract_parameter_from_text",
    "extract_requirements_from_clause_text",
    "extract_technical_specification",
    "get_unit_details",
    "normalize_numeric_value",
    "normalize_unit_string",
    "analyze_technical_specification",
    "compare_parameter_to_requirement",
]
