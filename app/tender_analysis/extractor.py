"""
app/tender_analysis/extractor.py

Tender and Customer Specification Requirement Extractor (Phase 13).

DESIGN PRINCIPLES:
  - Extracts explicit customer requirements from tender / RFQ text.
  - Reuses Phase 12 extract_parameter_from_text for physical parameters.
  - Preserves exact source text without hallucination.
  - Identifies customer mandatory wording ('shall', 'must') without equating it to statutory law.
"""

import hashlib
import re
import time
from typing import Any, Dict, List, Optional

from app.technical_specs.extractor import (
    extract_parameter_from_text,
    normalize_numeric_value,
)
from app.technical_specs.models import ComparisonOperator, TechnicalParameter
from app.tender_analysis.models import TenderDocument, TenderRequirement


# Regex patterns for mandatory customer phrases
MANDATORY_PHRASES_REGEX = re.compile(
    r'\b(shall|must|mandatory|strictly\s+required|compulsory|is\s+required\s+to\s+be)\b',
    re.IGNORECASE
)

# Regex patterns for categories
CATEGORY_PATTERNS = [
    ("certification", re.compile(r'\b(bis|isi\s+mark|certification|certified|license|licence|conformance)\b', re.IGNORECASE)),
    ("documentation", re.compile(r'\b(test\s+report|certificate|calibration\s+report|manual|datasheet|drawing|warranty)\b', re.IGNORECASE)),
    ("test", re.compile(r'\b(test|testing|inspection|sampled|routine\s+test|type\s+test|hydraulic\s+test|tensile)\b', re.IGNORECASE)),
    ("material", re.compile(r'\b(material|grade|stainless\s+steel|polyethylene|copper|aluminum|mild\s+steel)\b', re.IGNORECASE)),
    ("capacity", re.compile(r'\b(capacity|volume|storage|rated\s+capacity)\b', re.IGNORECASE)),
    ("dimension", re.compile(r'\b(dimension|dimensions|diameter|thickness|height|width|length|size)\b', re.IGNORECASE)),
    ("performance", re.compile(r'\b(temperature|pressure|efficiency|voltage|power|frequency|speed|rating)\b', re.IGNORECASE)),
]


def infer_tender_category(text: str) -> str:
    """Categorizes a tender clause based on explicit keywords."""
    for cat, pat in CATEGORY_PATTERNS:
        if pat.search(text):
            return cat
    return "general"


def extract_tender_requirement_from_clause(
    text: str,
    source_chunk_id: Optional[str] = None,
    page: Optional[int] = None,
    idx: int = 0,
) -> Optional[TenderRequirement]:
    """
    Extracts a single TenderRequirement from a line/clause of customer tender text.
    """
    cleaned = text.strip()
    if not cleaned or len(cleaned) < 5:
        return None

    # Clean numbering/bullet prefixes like "1. ", "1) ", "(a) ", "- "
    cleaned = re.sub(r'^(?:\(?\d+[\.\)]|[a-zA-Z][\.\)]|\-|\*)\s*', '', cleaned).strip()
    if not cleaned or len(cleaned) < 5:
        return None

    # Check mandatory language
    m_mand = MANDATORY_PHRASES_REGEX.search(cleaned)
    mand_lang = m_mand.group(1).lower() if m_mand else None

    # Determine category
    category = infer_tender_category(cleaned)

    # Subject extraction from clause prefix before verb/number
    subject_match = re.split(
        r'\b(?:shall|must|is|should|<=|>=|<|>|=|:|is\s+required|has\s+to\s+be)\b',
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE
    )
    subject_hint = subject_match[0].strip() if subject_match and len(subject_match[0].strip()) > 2 else None
    if subject_hint:
        # Clean numbering prefixes like "1. ", "a) "
        subject_hint = re.sub(r'^(?:\d+[\.\)]|[a-zA-Z][\.\)]|\-)\s*', '', subject_hint).strip()

    # Extract technical parameter if applicable
    tech_param = extract_parameter_from_text(cleaned, param_name_hint=subject_hint)

    # Extract parameter name or use category
    param_name = subject_hint or "general_requirement"
    val = None
    unit = None
    op = ComparisonOperator.EQ

    if "shall not exceed" in cleaned.lower() or "maximum" in cleaned.lower() or "up to" in cleaned.lower():
        op = ComparisonOperator.LE
    elif "shall be at least" in cleaned.lower() or "shall not be less than" in cleaned.lower() or "minimum" in cleaned.lower():
        op = ComparisonOperator.GE
    elif "between" in cleaned.lower():
        op = ComparisonOperator.RANGE

    if tech_param:
        if subject_hint:
            tech_param.parameter_name = subject_hint
        param_name = tech_param.parameter_name
        val = tech_param.value
        unit = tech_param.unit
        if tech_param.min_value is not None and tech_param.max_value is not None:
            op = ComparisonOperator.RANGE
        elif tech_param.min_value is not None:
            op = ComparisonOperator.GE
        elif tech_param.max_value is not None:
            op = ComparisonOperator.LE
        elif "shall not exceed" in cleaned.lower() or "up to" in cleaned.lower():
            op = ComparisonOperator.LE
            tech_param.max_value = float(val) if val is not None else None
        elif "shall be at least" in cleaned.lower() or "shall not be less than" in cleaned.lower() or "minimum" in cleaned.lower():
            op = ComparisonOperator.GE
            tech_param.min_value = float(val) if val is not None else None
        else:
            op = ComparisonOperator.EQ
    else:
        # Check if certification requirement: e.g. "BIS certification as per IS 12701"
        m_is = re.search(r'\bIS\s+(\d+(?:-\d+)?)\b', cleaned, re.IGNORECASE)
        if m_is:
            param_name = f"standard_conformance_{m_is.group(0).upper()}"
            val = m_is.group(0).upper()
        else:
            # Extract heading or first 3 words
            words = cleaned.split()[:4]
            param_name = "_".join(w.lower() for w in words if re.match(r'^[a-zA-Z0-9]+$', w)) or "tender_clause"

    req_id = f"treq_{hashlib.sha256((cleaned + str(idx)).encode('utf-8')).hexdigest()[:12]}"

    return TenderRequirement(
        requirement_id=req_id,
        text=cleaned,
        parameter_name=param_name,
        parameter=tech_param,
        value=val,
        unit=unit,
        operator=op,
        mandatory_language=mand_lang,
        category=category,
        source_chunk_id=source_chunk_id,
        page=page,
    )


def extract_tender_document(
    text: str,
    tender_id: Optional[str] = None,
    title: Optional[str] = None,
    issuer: Optional[str] = None,
    source_document_id: Optional[str] = None,
) -> TenderDocument:
    """
    Parses a raw customer tender or specification text into a structured TenderDocument.
    """
    t_id = tender_id or f"tnd_{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}"
    lines = [l.strip() for l in re.split(r'[\r\n;]+|(?<!\d)\.(?!\d)', text) if l.strip()]

    requirements: List[TenderRequirement] = []
    for idx, line in enumerate(lines):
        req = extract_tender_requirement_from_clause(
            text=line,
            source_chunk_id=source_document_id,
            idx=idx,
        )
        if req:
            requirements.append(req)

    return TenderDocument(
        tender_id=t_id,
        title=title or "Customer Tender / Specification",
        issuer=issuer,
        source_document_id=source_document_id,
        requirements=requirements,
        raw_text=text,
        created_at=time.time(),
    )
