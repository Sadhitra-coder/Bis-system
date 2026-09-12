"""
app/product_mapping/extractor.py

Conservative Product Normalization and Explicit Attribute Extraction (Phase 11).

DESIGN PRINCIPLES:
  - Product facts must be explicit: zero inference of unstated details.
  - Conservative normalization: handles whitespace, safe singular/plural,
    common OCR artifacts, and formatting without uncontrolled synonym proliferation.
  - Extracts explicit technical characteristics: material, technology, operating principle,
    intended use, customer type, manufacturing activity, capacity/dimension.
  - Preserves original raw product description unaltered.
"""

import hashlib
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from app.query_intelligence.models import BusinessContext
from app.product_mapping.models import ProductContext


# ============================================================
# 1. NORMALIZATION RULES (Section 3)
# ============================================================

# Safe singularization for common industrial and commercial product classes
_SAFE_PLURAL_MAP = {
    "thermometers": "thermometer",
    "heaters": "heater",
    "cables": "cable",
    "batteries": "battery",
    "pipes": "pipe",
    "helmets": "helmet",
    "cookers": "cooker",
    "bars": "bar",
    "toys": "toy",
    "bottles": "bottle",
    "cylinders": "cylinder",
    "valves": "valve",
    "meters": "meter",
    "pumps": "pump",
    "transformers": "transformer",
    "appliances": "appliance",
    "switches": "switch",
    "connectors": "connector",
    "articles": "article",
    "artefacts": "artefact",
    "artifacts": "artifact",
    "wires": "wire",
    "tubes": "tube",
    "sheets": "sheet",
    "plates": "plate",
    "fittings": "fitting",
}

# Common OCR / formatting artifact patterns
_OCR_HYPHEN_PATTERN = re.compile(r'(\b[a-zA-Z]{3,})-\s+([a-zA-Z]{3,}\b)')
_MULTIPLE_SPACES = re.compile(r'\s+')
_PUNCTUATION_NOISE = re.compile(r'["\'`_]')


def normalize_product_text(text: str) -> str:
    """
    Conservatively normalizes product text without altering core semantics.
    Handles OCR line-break hyphens, safe plurals, and excessive punctuation.
    """
    if not text or not text.strip():
        return ""

    norm = text.strip()

    # 1. Repair common OCR broken hyphenation: "thermo- meter" -> "thermometer"
    norm = _OCR_HYPHEN_PATTERN.sub(r'\1\2', norm)

    # 2. Strip stray quote marks
    norm = _PUNCTUATION_NOISE.sub(' ', norm)

    # 3. Collapse whitespace
    norm = _MULTIPLE_SPACES.sub(' ', norm).strip()

    # 4. Safe word-level singularization
    words = norm.split()
    singularized = []
    for w in words:
        w_lower = w.lower()
        if w_lower in _SAFE_PLURAL_MAP:
            # Match case of original word if possible
            replacement = _SAFE_PLURAL_MAP[w_lower]
            if w.isupper():
                singularized.append(replacement.upper())
            elif w[0].isupper():
                singularized.append(replacement.capitalize())
            else:
                singularized.append(replacement)
        else:
            singularized.append(w)

    return " ".join(singularized)


# ============================================================
# 2. EXPLICIT ATTRIBUTE PATTERNS (Section 4)
# ============================================================

# Materials (Strictly explicit keywords)
_MATERIAL_PATTERNS = [
    (re.compile(r'\b(?:stainless\s+steel|ss)\b', re.IGNORECASE), "stainless steel"),
    (re.compile(r'\b(?:carbon\s+steel)\b', re.IGNORECASE), "carbon steel"),
    (re.compile(r'\b(?:galvanized\s+steel|gi)\b', re.IGNORECASE), "galvanized steel"),
    (re.compile(r'\b(?:structural\s+steel)\b', re.IGNORECASE), "structural steel"),
    (re.compile(r'\b(?:mild\s+steel|ms)\b', re.IGNORECASE), "mild steel"),
    (re.compile(r'\b(?:steel)\b', re.IGNORECASE), "steel"),
    (re.compile(r'\b(?:pvc|upvc|cpvc)\b', re.IGNORECASE), "PVC"),
    (re.compile(r'\b(?:polyethylene|pe|hdpe|ldpe)\b', re.IGNORECASE), "polyethylene"),
    (re.compile(r'\b(?:polypropylene|pp)\b', re.IGNORECASE), "polypropylene"),
    (re.compile(r'\b(?:mercury)\b', re.IGNORECASE), "mercury"),
    (re.compile(r'\b(?:glass)\b', re.IGNORECASE), "glass"),
    (re.compile(r'\b(?:copper)\b', re.IGNORECASE), "copper"),
    (re.compile(r'\b(?:alumin(?:i)?um)\b', re.IGNORECASE), "aluminum"),
    (re.compile(r'\b(?:gold)\b', re.IGNORECASE), "gold"),
    (re.compile(r'\b(?:silver)\b', re.IGNORECASE), "silver"),
    (re.compile(r'\b(?:lead\s+acid)\b', re.IGNORECASE), "lead acid"),
    (re.compile(r'\b(?:lithium(?:\s+ion)?)\b', re.IGNORECASE), "lithium"),
    (re.compile(r'\b(?:portland\s+cement)\b', re.IGNORECASE), "portland cement"),
    (re.compile(r'\b(?:rubber)\b', re.IGNORECASE), "rubber"),
]

# Technologies (Strictly explicit keywords)
_TECHNOLOGY_PATTERNS = [
    (re.compile(r'\b(?:digital|electronic)\b', re.IGNORECASE), "digital"),
    (re.compile(r'\b(?:electric|electrical)\b', re.IGNORECASE), "electric"),
    (re.compile(r'\b(?:infrared|ir)\b', re.IGNORECASE), "infrared"),
    (re.compile(r'\b(?:analog|mechanical)\b', re.IGNORECASE), "mechanical/analog"),
    (re.compile(r'\b(?:hydraulic)\b', re.IGNORECASE), "hydraulic"),
    (re.compile(r'\b(?:pneumatic)\b', re.IGNORECASE), "pneumatic"),
    (re.compile(r'\b(?:solar|photovoltaic)\b', re.IGNORECASE), "solar"),
    (re.compile(r'\b(?:wireless|bluetooth|rfid)\b', re.IGNORECASE), "wireless"),
    (re.compile(r'\b(?:manual)\b', re.IGNORECASE), "manual"),
]

# Operating principles
_OPERATING_PRINCIPLE_PATTERNS = [
    (re.compile(r'\b(?:induction)\b', re.IGNORECASE), "induction"),
    (re.compile(r'\b(?:convection)\b', re.IGNORECASE), "convection"),
    (re.compile(r'\b(?:resistance\s+heating)\b', re.IGNORECASE), "resistance heating"),
    (re.compile(r'\b(?:compression)\b', re.IGNORECASE), "compression"),
    (re.compile(r'\b(?:thermal\s+expansion)\b', re.IGNORECASE), "thermal expansion"),
    (re.compile(r'\b(?:pressuri[zs]ed)\b', re.IGNORECASE), "pressurized"),
]

# Intended use
_INTENDED_USE_PATTERNS = [
    (re.compile(r'\b(?:clinical(?:\s+use)?|medical(?:\s+use)?|hospital(?:\s+use)?)\b', re.IGNORECASE), "clinical"),
    (re.compile(r'\b(?:domestic(?:\s+use)?|household(?:\s+use)?|home(?:\s+use)?)\b', re.IGNORECASE), "domestic"),
    (re.compile(r'\b(?:industrial(?:\s+use)?|factory(?:\s+use)?)\b', re.IGNORECASE), "industrial"),
    (re.compile(r'\b(?:laboratory(?:\s+use)?|lab(?:\s+use)?)\b', re.IGNORECASE), "laboratory"),
    (re.compile(r'\b(?:agricultural(?:\s+use)?|farming)\b', re.IGNORECASE), "agricultural"),
    (re.compile(r'\b(?:commercial(?:\s+use)?)\b', re.IGNORECASE), "commercial"),
]

# Capacity / Dimensions / Ratings (e.g. 5 litre, 100 mm, 240 V, 10 kW)
_CAPACITY_PATTERN = re.compile(
    r'\b(\d+(?:\.\d+)?\s*(?:litre|liter|l|ml|kg|g|mm|cm|m|v|volt|kw|w|bar|kpa|mpa|sq\s*mm|mm2))\b',
    re.IGNORECASE
)

# Explicit target product phrases from natural queries
_TARGET_PHRASE_PATTERNS = [
    re.compile(r'\b(?:standards?\s+(?:for|covering|governing|on)|which\s+standard\s+covers|is\s+there\s+a\s+standard\s+for|compliance\s+for|requirements\s+for)\s+([a-zA-Z0-9\s-]+?)(?:\s+(?:in|under|dated|\?|\.|$))', re.IGNORECASE),
    re.compile(r'\b(?:we\s+manufacture|manufacture\s+of|producing|we\s+produce|we\s+make|makes?)\s+([a-zA-Z0-9\s-]+?)(?:\s+(?:for|in|at|under|with|\?|\.|$))', re.IGNORECASE),
    re.compile(r'\b(?:applicable\s+to|applies\s+to|apply\s+to)\s+([a-zA-Z0-9\s-]+?)(?:\s+(?:in|under|dated|\?|\.|$))', re.IGNORECASE),
]

_COMMON_PRODUCT_NAMES = [
    (re.compile(r'\b(?:clinical\s+thermometers?|mercury\s+thermometers?|digital\s+thermometers?)\b', re.IGNORECASE), "clinical thermometer"),
    (re.compile(r'\b(?:thermometers?)\b', re.IGNORECASE), "thermometer"),
    (re.compile(r'\b(?:gold\s+jeweller?y|gold\s+artefacts?|gold\s+articles?)\b', re.IGNORECASE), "gold jewellery"),
    (re.compile(r'\b(?:domestic\s+electric\s+water\s+heaters?|electric\s+water\s+heaters?|water\s+heaters?|geysers?)\b', re.IGNORECASE), "electric water heater"),
    (re.compile(r'\b(?:domestic\s+pressure\s+cookers?|pressure\s+cookers?)\b', re.IGNORECASE), "pressure cooker"),
    (re.compile(r'\b(?:safety\s+helmets?|protective\s+helmets?|industrial\s+helmets?)\b', re.IGNORECASE), "safety helmet"),
    (re.compile(r'\b(?:pvc\s+pipes?|upvc\s+pipes?|cpvc\s+pipes?|polyethylene\s+pipes?)\b', re.IGNORECASE), "PVC pipe"),
    (re.compile(r'\b(?:electric\s+cables?|power\s+cables?|pvc\s+cables?)\b', re.IGNORECASE), "electric cable"),
    (re.compile(r'\b(?:electric\s+irons?)\b', re.IGNORECASE), "electric iron"),
    (re.compile(r'\b(?:packaged\s+drinking\s+water|mineral\s+water|bottled\s+water)\b', re.IGNORECASE), "packaged drinking water"),
    (re.compile(r'\b(?:lead\s+acid\s+batteries?|storage\s+batteries?)\b', re.IGNORECASE), "lead acid battery"),
    (re.compile(r'\b(?:portland\s+cement|cement)\b', re.IGNORECASE), "cement"),
    (re.compile(r'\b(?:steel\s+bars?|tmt\s+bars?|reinforcement\s+bars?)\b', re.IGNORECASE), "steel bar"),
    (re.compile(r'\b(?:electric\s+toys?|non-electric\s+toys?|toys?)\b', re.IGNORECASE), "toy"),
    (re.compile(r'\b(?:lpg\s+cylinders?|gas\s+cylinders?)\b', re.IGNORECASE), "LPG cylinder"),
]


# ============================================================
# 3. EXTRACTION SERVICE (Section 1, 2, 4)
# ============================================================

def extract_product_context(
    query: str,
    business_context: Optional[BusinessContext] = None,
    context_id: Optional[str] = None,
) -> ProductContext:
    """
    Extracts explicit product facts from input text and optional business context.
    Zero-inference: unstated attributes remain None.
    """
    q_norm = normalize_product_text(query or "")
    tech_chars: Dict[str, Any] = {}

    # Seed with business context if available, or extract explicit business facts from query
    if business_context is None and query:
        from app.query_intelligence.business import extract_business_context_from_query
        business_context = extract_business_context_from_query(query)

    if business_context:
        ctx = ProductContext.from_business_context(
            business_context,
            raw_query=query,
            context_id=context_id,
        )
        tech_chars.update(ctx.technical_characteristics)
    else:
        cid = context_id or f"pctx_{hashlib.sha256((query or '').encode('utf-8')).hexdigest()[:12]}"
        ctx = ProductContext(product_context_id=cid, raw_query=query)

    # Extract explicit standard references (e.g. "IS 3055")
    std_refs = re.findall(r'\bIS\s+(\d+(?:-\d+)?)\b', query or "", re.IGNORECASE)
    for s in std_refs:
        canon = f"IS {s.upper()}"
        if canon not in ctx.existing_standards:
            ctx.existing_standards.append(canon)

    # 1. Detect Product Name / Category from explicit known product categories
    detected_cat = None
    for pat, canonical_name in _COMMON_PRODUCT_NAMES:
        if pat.search(query or ""):
            detected_cat = canonical_name
            break

    # If not found via known list, try target phrase extraction
    if not detected_cat:
        for t_pat in _TARGET_PHRASE_PATTERNS:
            m = t_pat.search(query or "")
            if m:
                cand = m.group(1).strip()
                cand_clean = re.sub(r'^(?:our|the|a|an)\s+', '', cand, flags=re.IGNORECASE).strip()
                # Exclude if it mentions a standard identifier directly
                if cand_clean and not re.search(r'\bIS\s+\d+\b', cand_clean, re.IGNORECASE):
                    if 2 < len(cand_clean) < 60:
                        detected_cat = normalize_product_text(cand_clean)
                        break

    if detected_cat:
        ctx.product_category = detected_cat
        if not ctx.product_name:
            ctx.product_name = detected_cat
    elif ctx.product_category:
        ctx.product_category = normalize_product_text(ctx.product_category)
    if ctx.product_name:
        ctx.product_name = normalize_product_text(ctx.product_name)


    # 2. Extract Material
    if not ctx.material:
        for pat, mat_val in _MATERIAL_PATTERNS:
            if pat.search(query or ""):
                ctx.material = mat_val
                tech_chars["material"] = mat_val
                break

    # 3. Extract Technology
    if not ctx.technology:
        for pat, tech_val in _TECHNOLOGY_PATTERNS:
            if pat.search(query or ""):
                ctx.technology = tech_val
                tech_chars["technology"] = tech_val
                break

    # 4. Extract Operating Principle
    if not ctx.operating_principle:
        for pat, op_val in _OPERATING_PRINCIPLE_PATTERNS:
            if pat.search(query or ""):
                ctx.operating_principle = op_val
                tech_chars["operating_principle"] = op_val
                break

    # 5. Extract Intended Use
    if not ctx.intended_use:
        for pat, use_val in _INTENDED_USE_PATTERNS:
            if pat.search(query or ""):
                ctx.intended_use = use_val
                tech_chars["intended_use"] = use_val
                break

    # 6. Extract Capacity / Dimensions
    caps = _CAPACITY_PATTERN.findall(query or "")
    if caps:
        tech_chars["capacity_or_rating"] = [c.strip() for c in caps]

    # 7. Update combined attributes
    ctx.technical_characteristics = tech_chars
    ctx.extracted_attributes = dict(tech_chars)
    ctx.normalized_product_description = normalize_product_text(
        ctx.primary_identifier or query or ""
    )

    return ctx
