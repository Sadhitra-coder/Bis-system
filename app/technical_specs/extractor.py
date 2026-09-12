"""
app/technical_specs/extractor.py

Parameter and Requirement Extractor and Unit Normalizer for Technical Specifications (Phase 12).

DESIGN PRINCIPLES:
  - Extract only EXPLICIT parameters and values.
  - ZERO inference: Never invent tolerances, nominal values, or thresholds.
  - Safe unit normalization: Converts within the same physical dimension family.
  - Incompatible units are marked incompatible, never silently cross-converted.
"""

import hashlib
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from app.technical_specs.models import (
    ComparisonOperator,
    StandardTechnicalRequirement,
    TechnicalParameter,
    TechnicalSpecification,
)


# ============================================================
# UNIT NORMALIZATION SYSTEM
# ============================================================

# Dimension families: mapping unit alias -> (canonical_unit, factor, offset, dimension)
# val_in_canonical = (val_in_unit * factor) + offset
UNIT_REGISTRY: Dict[str, Tuple[str, float, float, str]] = {
    # Volume (canonical: L)
    "l": ("L", 1.0, 0.0, "volume"),
    "litre": ("L", 1.0, 0.0, "volume"),
    "litres": ("L", 1.0, 0.0, "volume"),
    "liter": ("L", 1.0, 0.0, "volume"),
    "liters": ("L", 1.0, 0.0, "volume"),
    "ml": ("L", 0.001, 0.0, "volume"),
    "millilitre": ("L", 0.001, 0.0, "volume"),
    "millilitres": ("L", 0.001, 0.0, "volume"),
    "m3": ("L", 1000.0, 0.0, "volume"),
    "cubic metre": ("L", 1000.0, 0.0, "volume"),

    # Length (canonical: mm)
    "mm": ("mm", 1.0, 0.0, "length"),
    "millimetre": ("mm", 1.0, 0.0, "length"),
    "millimeter": ("mm", 1.0, 0.0, "length"),
    "cm": ("mm", 10.0, 0.0, "length"),
    "centimetre": ("mm", 10.0, 0.0, "length"),
    "m": ("mm", 1000.0, 0.0, "length"),
    "metre": ("mm", 1000.0, 0.0, "length"),
    "meter": ("mm", 1000.0, 0.0, "length"),
    "in": ("mm", 25.4, 0.0, "length"),
    "inch": ("mm", 25.4, 0.0, "length"),
    "inches": ("mm", 25.4, 0.0, "length"),

    # Mass (canonical: kg)
    "g": ("kg", 0.001, 0.0, "mass"),
    "gram": ("kg", 0.001, 0.0, "mass"),
    "grams": ("kg", 0.001, 0.0, "mass"),
    "kg": ("kg", 1.0, 0.0, "mass"),
    "kilogram": ("kg", 1.0, 0.0, "mass"),
    "kilograms": ("kg", 1.0, 0.0, "mass"),
    "tonne": ("kg", 1000.0, 0.0, "mass"),
    "tonnes": ("kg", 1000.0, 0.0, "mass"),
    "t": ("kg", 1000.0, 0.0, "mass"),

    # Temperature (canonical: °C)
    "°c": ("°C", 1.0, 0.0, "temperature"),
    "deg c": ("°C", 1.0, 0.0, "temperature"),
    "c": ("°C", 1.0, 0.0, "temperature"),
    "celsius": ("°C", 1.0, 0.0, "temperature"),
    "k": ("°C", 1.0, -273.15, "temperature"),
    "kelvin": ("°C", 1.0, -273.15, "temperature"),

    # Pressure (canonical: bar)
    "bar": ("bar", 1.0, 0.0, "pressure"),
    "bars": ("bar", 1.0, 0.0, "pressure"),
    "kpa": ("bar", 0.01, 0.0, "pressure"),
    "mpa": ("bar", 10.0, 0.0, "pressure"),
    "pa": ("bar", 1e-5, 0.0, "pressure"),
    "psi": ("bar", 0.0689476, 0.0, "pressure"),

    # Electrical Power (canonical: W)
    "w": ("W", 1.0, 0.0, "power"),
    "watt": ("W", 1.0, 0.0, "power"),
    "watts": ("W", 1.0, 0.0, "power"),
    "kw": ("W", 1000.0, 0.0, "power"),
    "kilowatt": ("W", 1000.0, 0.0, "power"),
    "hp": ("W", 745.7, 0.0, "power"),

    # Electrical Voltage (canonical: V)
    "v": ("V", 1.0, 0.0, "voltage"),
    "volt": ("V", 1.0, 0.0, "voltage"),
    "volts": ("V", 1.0, 0.0, "voltage"),
    "kv": ("V", 1000.0, 0.0, "voltage"),
    "mv": ("V", 0.001, 0.0, "voltage"),

    # Electrical Current (canonical: A)
    "a": ("A", 1.0, 0.0, "current"),
    "amp": ("A", 1.0, 0.0, "current"),
    "ampere": ("A", 1.0, 0.0, "current"),
    "amps": ("A", 1.0, 0.0, "current"),
    "ma": ("A", 0.001, 0.0, "current"),

    # Frequency (canonical: Hz)
    "hz": ("Hz", 1.0, 0.0, "frequency"),
    "hertz": ("Hz", 1.0, 0.0, "frequency"),
    "khz": ("Hz", 1000.0, 0.0, "frequency"),
    "mhz": ("Hz", 1e6, 0.0, "frequency"),

    # Percentage
    "%": ("%", 1.0, 0.0, "percentage"),
    "percent": ("%", 1.0, 0.0, "percentage"),
}


def normalize_unit_string(unit: Optional[str]) -> Optional[str]:
    """Cleans up unit string for lookup."""
    if not unit:
        return None
    cleaned = unit.strip().lower()
    cleaned = re.sub(r'[\s_]+', ' ', cleaned)
    return cleaned


def get_unit_details(unit: Optional[str]) -> Optional[Tuple[str, float, float, str]]:
    """Returns (canonical_unit, factor, offset, dimension) or None."""
    cleaned = normalize_unit_string(unit)
    if not cleaned:
        return None
    return UNIT_REGISTRY.get(cleaned)


def are_units_compatible(unit1: Optional[str], unit2: Optional[str]) -> bool:
    """Checks whether two units belong to the same physical dimension."""
    if not unit1 and not unit2:
        return True
    if not unit1 or not unit2:
        return False
    u1 = get_unit_details(unit1)
    u2 = get_unit_details(unit2)
    if not u1 or not u2:
        # Fallback to direct string equality
        return normalize_unit_string(unit1) == normalize_unit_string(unit2)
    return u1[3] == u2[3]  # Compare dimension


def normalize_numeric_value(val: float, unit: Optional[str]) -> Tuple[float, Optional[str]]:
    """
    Normalizes a numeric value into the canonical unit for its dimension.
    Returns (normalized_value, canonical_unit).
    """
    u_details = get_unit_details(unit)
    if not u_details:
        return val, unit
    canonical_u, factor, offset, _ = u_details
    norm_val = (val * factor) + offset
    return norm_val, canonical_u


# ============================================================
# PARAMETER EXTRACTION
# ============================================================

PARAM_EXTRACTION_REGEXES = [
    # Parameter with tolerance: "length: 10 ± 0.5 mm" or "length = 10 +/- 0.5 mm"
    re.compile(
        r'(?P<param>[a-zA-Z_][a-zA-Z0-9_\s]{1,30}?)\s*(?:[:=]|\bis\b)\s*'
        r'(?P<val>\d+(?:\.\d+)?)\s*(?:±|\+/-)\s*(?P<tol>\d+(?:\.\d+)?)\s*'
        r'(?P<unit>[°%a-zA-Z/]+)?',
        re.IGNORECASE
    ),
    # Parameter with range: "operating temperature: 10 to 40 °C" or "10 - 40 °C"
    re.compile(
        r'(?P<param>[a-zA-Z_][a-zA-Z0-9_\s]{1,30}?)\s*(?:[:=]|\bis\b)\s*'
        r'(?:between\s+)?(?P<min>\d+(?:\.\d+)?)\s*(?:to|-|and)\s*(?P<max>\d+(?:\.\d+)?)\s*'
        r'(?P<unit>[°%a-zA-Z/]+)?',
        re.IGNORECASE
    ),
    # Parameter with inequality: "capacity >= 500 L" or "temperature <= 40 °C"
    re.compile(
        r'(?P<param>[a-zA-Z_][a-zA-Z0-9_\s]{1,30}?)\s*(?:[:=]|\bis\b)?\s*'
        r'(?P<op><=|>=|<|>|=)\s*(?P<val>\d+(?:\.\d+)?)\s*'
        r'(?P<unit>[°%a-zA-Z/]+)?',
        re.IGNORECASE
    ),
    # Parameter simple value: "capacity: 500 L" or "material: stainless steel"
    re.compile(
        r'(?P<param>[a-zA-Z_][a-zA-Z0-9_\s]{1,30}?)\s*(?:[:=])\s*'
        r'(?P<val>\d+(?:\.\d+)?|[a-zA-Z0-9_\s\-/]+)\s*'
        r'(?P<unit>[°%a-zA-Z/]+)?',
        re.IGNORECASE
    ),
]


def extract_parameter_from_text(text: str, param_name_hint: Optional[str] = None) -> Optional[TechnicalParameter]:
    """
    Extracts an explicit technical parameter from a snippet of text.
    Zero-inference: Only parses explicit numbers, tolerances, and units.
    """
    if not text or not text.strip():
        return None

    # Strip leading bullet/list markers e.g. "1. ", "1) ", "(a) ", "- "
    text = re.sub(r'^(?:\(?\d+[\.\)]|[a-zA-Z][\.\)]|\-|\*)\s*', '', text.strip()).strip()
    if not text:
        return None

    # Check for tolerance match
    m_tol = re.search(
        r'(?:(?P<param>[a-zA-Z_\s]+)[:=]\s*)?(?P<val>\d+(?:\.\d+)?)\s*(?:±|\+/-)\s*(?P<tol>\d+(?:\.\d+)?)\s*(?P<unit>[°%a-zA-Z/]+)?',
        text, re.IGNORECASE
    )
    if m_tol and m_tol.group("tol"):
        pname = (m_tol.group("param") or param_name_hint or "parameter").strip()
        val = float(m_tol.group("val"))
        tol = float(m_tol.group("tol"))
        unit = m_tol.group("unit").strip() if m_tol.group("unit") else None
        norm_val, norm_u = normalize_numeric_value(val, unit)
        norm_tol = tol
        if unit:
            u_details = get_unit_details(unit)
            if u_details:
                norm_tol = tol * u_details[1]

        return TechnicalParameter(
            parameter_name=pname,
            value=val,
            normalized_value=norm_val,
            unit=unit,
            normalized_unit=norm_u,
            nominal_value=val,
            min_value=val - tol,
            max_value=val + tol,
            tolerance=tol,
            tolerance_unit=unit,
            raw_text=text.strip(),
        )

    # Check for range match
    m_range = re.search(
        r'(?:(?P<param>[a-zA-Z_\s]+)[:=]\s*)?(?:between\s+)?(?P<min>\d+(?:\.\d+)?)\s*(?:to|-|and)\s*(?P<max>\d+(?:\.\d+)?)\s*(?P<unit>[°%a-zA-Z/]+)?',
        text, re.IGNORECASE
    )
    if m_range and m_range.group("min") and m_range.group("max"):
        pname = (m_range.group("param") or param_name_hint or "parameter").strip()
        min_v = float(m_range.group("min"))
        max_v = float(m_range.group("max"))
        unit = m_range.group("unit").strip() if m_range.group("unit") else None
        norm_min, norm_u = normalize_numeric_value(min_v, unit)
        norm_max, _ = normalize_numeric_value(max_v, unit)
        return TechnicalParameter(
            parameter_name=pname,
            min_value=min_v,
            max_value=max_v,
            unit=unit,
            normalized_unit=norm_u,
            raw_text=text.strip(),
        )

    # Check for simple numeric / operator match
    m_num = re.search(
        r'(?:(?P<param>[a-zA-Z_\s]+)[:=]\s*)?(?P<op><=|>=|<|>|=)?\s*(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>[°%a-zA-Z/]+)?',
        text, re.IGNORECASE
    )
    if m_num and m_num.group("val"):
        pname = (m_num.group("param") or param_name_hint or "parameter").strip()
        val = float(m_num.group("val"))
        unit = m_num.group("unit").strip() if m_num.group("unit") else None
        norm_val, norm_u = normalize_numeric_value(val, unit)
        op = m_num.group("op")

        min_val = val if op in (">=", ">") else None
        max_val = val if op in ("<=", "<") else None
        nom_val = val if (op == "=" or not op) else None

        return TechnicalParameter(
            parameter_name=pname,
            value=val,
            normalized_value=norm_val,
            unit=unit,
            normalized_unit=norm_u,
            nominal_value=nom_val,
            min_value=min_val,
            max_value=max_val,
            raw_text=text.strip(),
        )

    # String value
    if param_name_hint:
        return TechnicalParameter(
            parameter_name=param_name_hint,
            value=text.strip(),
            raw_text=text.strip(),
        )

    return None


def extract_technical_specification(
    text: Optional[str] = None,
    structured_dict: Optional[Dict[str, Any]] = None,
    product_name: Optional[str] = None,
    product_id: Optional[str] = None,
    source_document_id: Optional[str] = None,
    source_chunk_ids: Optional[List[str]] = None,
    page_provenance: Optional[Dict[str, Any]] = None,
) -> TechnicalSpecification:
    """
    Extracts a canonical TechnicalSpecification from text and/or structured dictionaries.
    Zero-inference: Preserves exact values without hallucinating unstated attributes.
    """
    spec_id = f"tspec_{hashlib.sha256(((text or '') + str(structured_dict or '') + str(time.time())).encode('utf-8')).hexdigest()[:12]}"
    params: Dict[str, TechnicalParameter] = {}

    material = None
    technology = None
    dimensions = None
    capacity = None
    rating = None
    operating_conditions = None
    performance: Dict[str, Any] = {}

    # 1. Process structured dictionary if provided
    if structured_dict:
        for k, v in structured_dict.items():
            k_clean = str(k).strip()
            k_lower = k_clean.lower()

            if k_lower == "material":
                material = str(v).strip()
            elif k_lower in ("technology", "tech"):
                technology = str(v).strip()
            elif k_lower in ("dimensions", "dimension", "size"):
                dimensions = str(v).strip()
            elif k_lower in ("capacity", "volume"):
                capacity = str(v).strip()
            elif k_lower in ("rating", "rated_output"):
                rating = str(v).strip()
            elif k_lower in ("operating_conditions", "conditions"):
                operating_conditions = str(v).strip()

            if isinstance(v, (int, float)):
                norm_v, norm_u = normalize_numeric_value(float(v), None)
                params[k_clean] = TechnicalParameter(
                    parameter_name=k_clean,
                    value=v,
                    normalized_value=norm_v,
                    nominal_value=float(v),
                    raw_text=str(v),
                )
            elif isinstance(v, str):
                p = extract_parameter_from_text(v, param_name_hint=k_clean)
                if p:
                    params[k_clean] = p
                else:
                    params[k_clean] = TechnicalParameter(
                        parameter_name=k_clean,
                        value=v.strip(),
                        raw_text=v.strip(),
                    )
            elif isinstance(v, dict):
                # Nested parameter details
                p = TechnicalParameter(
                    parameter_name=k_clean,
                    value=v.get("value"),
                    unit=v.get("unit"),
                    min_value=v.get("min_value"),
                    max_value=v.get("max_value"),
                    nominal_value=v.get("nominal_value"),
                    tolerance=v.get("tolerance"),
                    raw_text=str(v),
                )
                if p.value is not None and isinstance(p.value, (int, float)):
                    p.normalized_value, p.normalized_unit = normalize_numeric_value(float(p.value), p.unit)
                params[k_clean] = p

    # 2. Process unstructured text if provided
    if text:
        # Split into lines or clause phrases
        lines = [line.strip() for line in re.split(r'[\r\n;,]+', text) if line.strip()]
        for line in lines:
            # Common patterns: "parameter: value"
            if ":" in line or "=" in line:
                parts = re.split(r'[:=]', line, maxsplit=1)
                if len(parts) == 2:
                    k_cand = parts[0].strip()
                    v_cand = parts[1].strip()
                    if k_cand and v_cand:
                        p = extract_parameter_from_text(v_cand, param_name_hint=k_cand)
                        if p:
                            params[k_cand] = p
                            # Also check top-level properties
                            k_l = k_cand.lower()
                            if "material" in k_l and not material:
                                material = v_cand
                            elif "dimension" in k_l and not dimensions:
                                dimensions = v_cand
                            elif "capacity" in k_l and not capacity:
                                capacity = v_cand
                            elif "rating" in k_l and not rating:
                                rating = v_cand
                            continue

            # Fallback inline regex matching
            for reg in PARAM_EXTRACTION_REGEXES:
                m = reg.search(line)
                if m and "param" in m.groupdict() and m.group("param"):
                    pname = m.group("param").strip()
                    p = extract_parameter_from_text(line, param_name_hint=pname)
                    if p and pname not in params:
                        params[pname] = p
                        break

    return TechnicalSpecification(
        specification_id=spec_id,
        product_id=product_id,
        product_name=product_name,
        parameters=params,
        material=material,
        technology=technology,
        dimensions=dimensions,
        capacity=capacity,
        rating=rating,
        operating_conditions=operating_conditions,
        performance_characteristics=performance,
        source_document_id=source_document_id,
        source_chunk_ids=source_chunk_ids or [],
        page_provenance=page_provenance,
        raw_text=text,
        created_at=time.time(),
    )


# ============================================================
# STANDARD REQUIREMENT EXTRACTION FROM CLAUSES
# ============================================================

def extract_requirements_from_clause_text(
    clause_text: str,
    standard_id: str,
    standard_number: Optional[str] = None,
    version_id: Optional[str] = None,
    clause_id: Optional[str] = None,
    clause_number: Optional[str] = None,
    clause_title: Optional[str] = None,
    source_chunk_ids: Optional[List[str]] = None,
    page_start: Optional[int] = None,
    page_end: Optional[int] = None,
    temporal_status: Optional[str] = None,
) -> List[StandardTechnicalRequirement]:
    """
    Extracts explicit technical requirements from a clause's text.
    Detects threshold values, bounds, inequalities, ranges, and tolerances.
    Always maintains full source provenance.
    """
    if not clause_text or not clause_text.strip():
        return []

    requirements: List[StandardTechnicalRequirement] = []
    lines = [l.strip() for l in re.split(r'[\r\n;]+|(?<!\d)\.(?!\d)', clause_text) if l.strip()]

    # Patterns for normative requirements
    # 1. "shall not exceed X [unit]" / "shall be at most X [unit]" / "maximum X [unit]" -> LE (<=)
    pat_le = re.compile(
        r'(?P<param>[a-zA-Z_\s]{2,30}?)\s*(?:shall\s+not\s+exceed|shall\s+be\s+(?:at\s+most|no\s+more\s+than)|maximum|max\.?)\s+'
        r'(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>[°%a-zA-Z/]+)?',
        re.IGNORECASE
    )

    # 2. "shall be at least X [unit]" / "shall not be less than X [unit]" / "minimum X [unit]" -> GE (>=)
    pat_ge = re.compile(
        r'(?P<param>[a-zA-Z_\s]{2,30}?)\s*(?:shall\s+be\s+at\s+least|shall\s+not\s+be\s+less\s+than|minimum|min\.?)\s+'
        r'(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>[°%a-zA-Z/]+)?',
        re.IGNORECASE
    )

    # 3. "shall be between X and Y [unit]" / "from X to Y [unit]" -> RANGE
    pat_range = re.compile(
        r'(?P<param>[a-zA-Z_\s]{2,30}?)\s*(?:shall\s+be\s+between|between|from)\s+'
        r'(?P<min>\d+(?:\.\d+)?)\s*(?:and|to|-)\s*(?P<max>\d+(?:\.\d+)?)\s*(?P<unit>[°%a-zA-Z/]+)?',
        re.IGNORECASE
    )

    # 4. "shall be X ± Y [unit]" -> EQ with tolerance
    pat_tol = re.compile(
        r'(?P<param>[a-zA-Z_\s]{2,30}?)\s*(?:shall\s+be|shall\s+equal|=)\s*'
        r'(?P<val>\d+(?:\.\d+)?)\s*(?:±|\+/-)\s*(?P<tol>\d+(?:\.\d+)?)\s*(?P<unit>[°%a-zA-Z/]+)?',
        re.IGNORECASE
    )

    # 5. Generic operator in text: "<= 40 °C", ">= 500 L"
    pat_generic_op = re.compile(
        r'(?P<param>[a-zA-Z_\s]{2,30}?)\s*(?P<op><=|>=|<|>|=)\s*'
        r'(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>[°%a-zA-Z/]+)?',
        re.IGNORECASE
    )

    for idx, line in enumerate(lines):
        # Check range
        m_r = pat_range.search(line)
        if m_r:
            pname = m_r.group("param").strip()
            min_v = float(m_r.group("min"))
            max_v = float(m_r.group("max"))
            unit = m_r.group("unit").strip() if m_r.group("unit") else None
            _, norm_u = normalize_numeric_value(min_v, unit)
            req_id = f"req_{hashlib.sha256((standard_id + (clause_id or '') + pname + str(idx)).encode('utf-8')).hexdigest()[:12]}"
            requirements.append(
                StandardTechnicalRequirement(
                    requirement_id=req_id,
                    standard_id=standard_id,
                    standard_number=standard_number,
                    version_id=version_id,
                    clause_id=clause_id,
                    clause_number=clause_number,
                    clause_title=clause_title,
                    requirement_text=line,
                    parameter_name=pname,
                    operator=ComparisonOperator.RANGE,
                    min_threshold=min_v,
                    max_threshold=max_v,
                    unit=unit,
                    normalized_unit=norm_u,
                    source_chunk_ids=source_chunk_ids or [],
                    page_start=page_start,
                    page_end=page_end,
                    temporal_status=temporal_status,
                )
            )
            continue

        # Check tolerance
        m_t = pat_tol.search(line)
        if m_t:
            pname = m_t.group("param").strip()
            val = float(m_t.group("val"))
            tol = float(m_t.group("tol"))
            unit = m_t.group("unit").strip() if m_t.group("unit") else None
            norm_v, norm_u = normalize_numeric_value(val, unit)
            req_id = f"req_{hashlib.sha256((standard_id + (clause_id or '') + pname + str(idx)).encode('utf-8')).hexdigest()[:12]}"
            requirements.append(
                StandardTechnicalRequirement(
                    requirement_id=req_id,
                    standard_id=standard_id,
                    standard_number=standard_number,
                    version_id=version_id,
                    clause_id=clause_id,
                    clause_number=clause_number,
                    clause_title=clause_title,
                    requirement_text=line,
                    parameter_name=pname,
                    operator=ComparisonOperator.EQ,
                    threshold=val,
                    min_threshold=val - tol,
                    max_threshold=val + tol,
                    tolerance=tol,
                    unit=unit,
                    normalized_unit=norm_u,
                    source_chunk_ids=source_chunk_ids or [],
                    page_start=page_start,
                    page_end=page_end,
                    temporal_status=temporal_status,
                )
            )
            continue

        # Check <=
        m_le = pat_le.search(line)
        if m_le:
            pname = m_le.group("param").strip()
            val = float(m_le.group("val"))
            unit = m_le.group("unit").strip() if m_le.group("unit") else None
            norm_v, norm_u = normalize_numeric_value(val, unit)
            req_id = f"req_{hashlib.sha256((standard_id + (clause_id or '') + pname + str(idx)).encode('utf-8')).hexdigest()[:12]}"
            requirements.append(
                StandardTechnicalRequirement(
                    requirement_id=req_id,
                    standard_id=standard_id,
                    standard_number=standard_number,
                    version_id=version_id,
                    clause_id=clause_id,
                    clause_number=clause_number,
                    clause_title=clause_title,
                    requirement_text=line,
                    parameter_name=pname,
                    operator=ComparisonOperator.LE,
                    threshold=val,
                    max_threshold=val,
                    unit=unit,
                    normalized_unit=norm_u,
                    source_chunk_ids=source_chunk_ids or [],
                    page_start=page_start,
                    page_end=page_end,
                    temporal_status=temporal_status,
                )
            )
            continue

        # Check >=
        m_ge = pat_ge.search(line)
        if m_ge:
            pname = m_ge.group("param").strip()
            val = float(m_ge.group("val"))
            unit = m_ge.group("unit").strip() if m_ge.group("unit") else None
            norm_v, norm_u = normalize_numeric_value(val, unit)
            req_id = f"req_{hashlib.sha256((standard_id + (clause_id or '') + pname + str(idx)).encode('utf-8')).hexdigest()[:12]}"
            requirements.append(
                StandardTechnicalRequirement(
                    requirement_id=req_id,
                    standard_id=standard_id,
                    standard_number=standard_number,
                    version_id=version_id,
                    clause_id=clause_id,
                    clause_number=clause_number,
                    clause_title=clause_title,
                    requirement_text=line,
                    parameter_name=pname,
                    operator=ComparisonOperator.GE,
                    threshold=val,
                    min_threshold=val,
                    unit=unit,
                    normalized_unit=norm_u,
                    source_chunk_ids=source_chunk_ids or [],
                    page_start=page_start,
                    page_end=page_end,
                    temporal_status=temporal_status,
                )
            )
            continue

        # Check generic operator
        m_gen = pat_generic_op.search(line)
        if m_gen:
            pname = m_gen.group("param").strip()
            op_str = m_gen.group("op")
            val = float(m_gen.group("val"))
            unit = m_gen.group("unit").strip() if m_gen.group("unit") else None
            norm_v, norm_u = normalize_numeric_value(val, unit)

            op = ComparisonOperator.EQ
            if op_str == "<=":
                op = ComparisonOperator.LE
            elif op_str == ">=":
                op = ComparisonOperator.GE
            elif op_str == "<":
                op = ComparisonOperator.LT
            elif op_str == ">":
                op = ComparisonOperator.GT

            req_id = f"req_{hashlib.sha256((standard_id + (clause_id or '') + pname + str(idx)).encode('utf-8')).hexdigest()[:12]}"
            requirements.append(
                StandardTechnicalRequirement(
                    requirement_id=req_id,
                    standard_id=standard_id,
                    standard_number=standard_number,
                    version_id=version_id,
                    clause_id=clause_id,
                    clause_number=clause_number,
                    clause_title=clause_title,
                    requirement_text=line,
                    parameter_name=pname,
                    operator=op,
                    threshold=val,
                    unit=unit,
                    normalized_unit=norm_u,
                    source_chunk_ids=source_chunk_ids or [],
                    page_start=page_start,
                    page_end=page_end,
                    temporal_status=temporal_status,
                )
            )

    return requirements
