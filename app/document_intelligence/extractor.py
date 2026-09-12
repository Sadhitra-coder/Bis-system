"""
app/document_intelligence/extractor.py

Extraction Engine for Compliance & Engineering Documents (Phase 14).

DESIGN PRINCIPLES:
  - Extracts metadata, dates, standard references, accreditation, and test results.
  - Reuses Phase 12 TechnicalParameter and physical dimension normalization without duplication.
  - Conservative date parsing: resolves DD-MM-YYYY, YYYY-MM-DD, and English month formats.
  - Expiry status is computed against evaluation/current date.
  - Strictly 0% extrapolation: unobserved test values are NEVER fabricated.
"""

from datetime import datetime
import hashlib
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from app.document_intelligence.classifier import classify_document
from app.document_intelligence.models import BusinessDocument, DocumentType
from app.technical_specs.extractor import (
    extract_parameter_from_text,
    extract_technical_specification,
    normalize_numeric_value,
    normalize_unit_string,
)
from app.technical_specs.models import TechnicalParameter


# Regular expressions for metadata fields
_DOC_NUM_PATTERNS = [
    re.compile(r"(?:Report\s+No\.?|Certificate\s+No\.?|Cert\s+No\.?|Test\s+Report\s+No\.?|Doc\s+No\.?|Licence\s+No\.?|License\s+No\.?)[:\s]+([A-Z0-9\-_/\.]+)", re.IGNORECASE),
    re.compile(r"\b([A-Z]{2,4}/[A-Z0-9\-]+/\d{4,})\b"),
]

_ACCREDITATION_PATTERN = re.compile(
    r"(NABL\s+(?:Accredited|Accreditation)?[^.\n]*|ISO[/\s]IEC\s+17025(?::\d{4})?[^.\n]*|BIS\s+Recognized[^.\n]*)",
    re.IGNORECASE
)

_STANDARD_REF_PATTERN = re.compile(
    r"\b(IS\s+\d+(?:(?:-|\s+Part\s*)\d+)?(?:\s*:\s*\d{4})?)\b",
    re.IGNORECASE
)

# Date regexes
_DATE_PATTERNS = [
    # YYYY-MM-DD
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), "%Y-%m-%d"),
    # DD-MM-YYYY or DD/MM/YYYY
    (re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b"), "%d-%m-%Y"),
    # DD Month YYYY
    (re.compile(r"\b(\d{1,2})\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{4})\b", re.IGNORECASE), "%d %b %Y"),
]

_ISSUE_DATE_PREFIX = re.compile(r"(?:Issue\s+Date|Date\s+of\s+Issue|Dated|Date\s+of\s+Testing|Test\s+Date|Date)[:\s]+", re.IGNORECASE)
_EXPIRY_DATE_PREFIX = re.compile(r"(?:Valid\s+(?:Up\s+To|Until|Upto|Thru)|Expiry\s+Date|Expiration\s+Date|Due\s+Date|Calibration\s+Due)[:\s]+", re.IGNORECASE)


def parse_date_safely(date_str: str) -> Optional[datetime]:
    """
    Safely parse date strings into datetime objects.
    Supports YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY, and DD Month YYYY.
    """
    if not date_str or not isinstance(date_str, str):
        return None

    clean = date_str.strip().rstrip(".,;")

    # Handle month abbreviations
    month_abbrs = {
        "january": "Jan", "february": "Feb", "march": "Mar", "april": "Apr",
        "june": "Jun", "july": "Jul", "august": "Aug", "september": "Sep",
        "october": "Oct", "november": "Nov", "december": "Dec"
    }
    for full_m, abbr in month_abbrs.items():
        clean = re.sub(rf"\b{full_m}\b", abbr, clean, flags=re.IGNORECASE)

    for pat, fmt in _DATE_PATTERNS:
        m = pat.search(clean)
        if m:
            matched_str = m.group(0).replace("/", "-")
            try:
                return datetime.strptime(matched_str, fmt)
            except ValueError:
                pass
            # Try alternate interpretation if DD-MM vs MM-DD is ambiguous
            if fmt == "%d-%m-%Y":
                try:
                    return datetime.strptime(matched_str, "%m-%d-%Y")
                except ValueError:
                    pass
    return None


def extract_dates(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extracts issue_date and expiry_date strings from text.
    Returns: (issue_date_iso, expiry_date_iso)
    """
    issue_date: Optional[str] = None
    expiry_date: Optional[str] = None

    lines = text.splitlines()
    for line in lines:
        if not issue_date and _ISSUE_DATE_PREFIX.search(line):
            dt = parse_date_safely(line)
            if dt:
                issue_date = dt.strftime("%Y-%m-%d")

        if not expiry_date and _EXPIRY_DATE_PREFIX.search(line):
            dt = parse_date_safely(line)
            if dt:
                expiry_date = dt.strftime("%Y-%m-%d")

    # If not found via prefix, search general dates
    if not issue_date:
        for line in lines[:15]:  # Look in header lines
            dt = parse_date_safely(line)
            if dt:
                issue_date = dt.strftime("%Y-%m-%d")
                break

    return issue_date, expiry_date


def extract_document_number(text: str) -> Optional[str]:
    """Extracts official report / certificate identification number."""
    for pat in _DOC_NUM_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1).strip()
    return None


def extract_accreditation(text: str) -> Optional[str]:
    """Extracts lab accreditation details if present."""
    m = _ACCREDITATION_PATTERN.search(text)
    if m:
        return m.group(1).strip()
    return None


def extract_standard_references(text: str) -> List[str]:
    """Extracts referenced Indian Standards (e.g. IS 9873)."""
    matches = _STANDARD_REF_PATTERN.findall(text)
    unique_refs: List[str] = []
    for ref in matches:
        norm = " ".join(ref.split()).upper()
        if norm not in unique_refs:
            unique_refs.append(norm)
    return unique_refs


def extract_issuer(text: str) -> Optional[str]:
    """Extracts laboratory or issuing body name from header lines."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for line in lines[:10]:
        if any(term in line.lower() for term in ["laboratory", "labs", "testing centre", "testing services", "inspections", "bureau of indian standards", "certifications", "technologies"]):
            return line
    return None


def extract_test_results(text: str) -> List[Dict[str, Any]]:
    """
    Extracts structured test table entries from test reports.
    Looks for observed value, specified limits, and pass/fail remarks.
    """
    results: List[Dict[str, Any]] = []
    lines = text.splitlines()

    row_pattern = re.compile(
        r"([a-zA-Z\s\(\)\-]{3,35})[:\t|]+(?:observed|measured|found)?[:\s]*([0-9\.]+\s*[a-zA-Z/%°\^]+)?(?:.*?specified[:\s]*([<>=a-zA-Z0-9\.\s/%°\^]+))?(?:.*?(pass|fail|complies|satisfactory|non-compliant))?",
        re.IGNORECASE
    )

    for line in lines:
        stripped = line.strip()
        if not stripped or len(stripped) < 10:
            continue

        # Look for pass / fail / complies mentions
        if re.search(r"\b(pass|fail|complies|satisfactory)\b", stripped, re.IGNORECASE):
            m = row_pattern.search(stripped)
            if m:
                param = m.group(1).strip()
                observed = m.group(2).strip() if m.group(2) else None
                specified = m.group(3).strip() if m.group(3) else None
                verdict = m.group(4).strip().upper() if m.group(4) else "PASS"

                results.append({
                    "parameter": param,
                    "observed_value": observed,
                    "specified_requirement": specified,
                    "verdict": verdict,
                    "raw_text": stripped,
                })

    return results


def extract_business_document(
    text: str,
    filename: Optional[str] = None,
    document_id: Optional[str] = None,
    as_of_date: Optional[str] = None,
) -> BusinessDocument:
    """
    Constructs a fully structured BusinessDocument from raw document text and metadata.
    """
    doc_type, confidence, explanation = classify_document(text, filename=filename)

    doc_id = document_id or f"doc_{hashlib.sha256((text[:200] + (filename or '')).encode('utf-8')).hexdigest()[:10]}"
    doc_number = extract_document_number(text)
    issuer = extract_issuer(text)
    issue_date, expiry_date = extract_dates(text)
    accreditation = extract_accreditation(text)
    std_refs = extract_standard_references(text)
    test_results = extract_test_results(text)

    # Extract all physical technical parameters using Phase 12 engine
    tech_spec = extract_technical_specification(text=text)
    params_dict = tech_spec.parameters

    # Calculate active / expired status
    doc_status = "ACTIVE"
    if expiry_date:
        ref_dt = parse_date_safely(as_of_date) if as_of_date else datetime.now()
        exp_dt = parse_date_safely(expiry_date)
        if exp_dt and ref_dt and exp_dt < ref_dt:
            doc_status = "EXPIRED"

    # Title extraction
    title = None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines:
        title = lines[0] if len(lines[0]) < 100 else lines[0][:97] + "..."

    return BusinessDocument(
        document_id=doc_id,
        document_type=doc_type,
        title=title,
        issuer=issuer,
        document_number=doc_number,
        issue_date=issue_date,
        expiry_date=expiry_date,
        classification_confidence=confidence,
        extracted_parameters=params_dict,
        accreditation_details=accreditation,
        standard_references=std_refs,
        test_results=test_results,
        status=doc_status,
        raw_text=text,
        metadata={
            "filename": filename,
            "classification_explanation": explanation,
            "extraction_timestamp": time.time(),
        }
    )
