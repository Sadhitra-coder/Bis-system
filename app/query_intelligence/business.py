"""
app/query_intelligence/business.py

Business and Product Context Extraction for Query Intelligence (Phase 10).

DESIGN PRINCIPLES:
  - Deterministic and conservative: only extracts explicitly stated facts.
  - Zero hidden inference: never infer unstated company size (MSME, large scale),
    factory location, revenue, or certifications from vague statements.
  - Privacy preserving: no extraction of sensitive personal data.
  - Decoupled: per-query context never mutates profile context permanently.
"""

import re
from typing import Any, Dict, List, Optional

from app.query_intelligence.models import BusinessContext


# ============================================================
# EXPLICIT BUSINESS CONTEXT PATTERNS
# ============================================================

# Manufacturing activities
_ACTIVITY_PATTERNS = [
    (re.compile(r'\b(?:we\s+manufacture|manufactures?|manufacturing(?:\s+of)?|we\s+make|makes?|we\s+produce|produces?|production(?:\s+of)?)\b', re.IGNORECASE), "manufacturing"),
    (re.compile(r'\b(?:we\s+import|imports?|importing(?:\s+of)?|we\s+source\s+from\s+abroad)\b', re.IGNORECASE), "importing"),
    (re.compile(r'\b(?:we\s+distribute|distributes?|distribution(?:\s+of)?|we\s+trade|trades?)\b', re.IGNORECASE), "distribution"),
    (re.compile(r'\b(?:we\s+assemble|assembles?|assembly(?:\s+of)?)\b', re.IGNORECASE), "assembly"),
]

# Explicit business types (nouns)
_BUSINESS_TYPE_PATTERNS = [
    (re.compile(r'\b(?:manufacturer\s+of|as\s+a\s+manufacturer|we\s+are\s+manufacturers?)\b', re.IGNORECASE), "manufacturer"),
    (re.compile(r'\b(?:importer\s+of|as\s+an\s+importer|we\s+are\s+importers?)\b', re.IGNORECASE), "importer"),
    (re.compile(r'\b(?:distributor\s+of|as\s+a\s+distributor|trader\s+of|we\s+are\s+distributors?)\b', re.IGNORECASE), "distributor"),
    (re.compile(r'\b(?:assembler\s+of|as\s+an\s+assembler|we\s+are\s+assemblers?)\b', re.IGNORECASE), "assembler"),
]

# Explicit company size (STRICT: only when explicitly mentioned)
_COMPANY_SIZE_PATTERNS = [
    (re.compile(r'\b(?:msme|micro\s+enterprise|micro\s+unit)\b', re.IGNORECASE), "MSME (Micro)"),
    (re.compile(r'\b(?:small\s+scale|small\s+enterprise|ssi)\b', re.IGNORECASE), "Small Scale Enterprise"),
    (re.compile(r'\b(?:medium\s+enterprise)\b', re.IGNORECASE), "Medium Enterprise"),
    (re.compile(r'\b(?:large\s+scale|large\s+manufacturer|multinational|mnc)\b', re.IGNORECASE), "Large Scale Enterprise"),
    (re.compile(r'\b(?:startup|start-up)\b', re.IGNORECASE), "Startup"),
]

# Explicit location keywords (conservative: city/state after factory/plant/unit/located in/in)
_LOCATION_PATTERN = re.compile(
    r'\b(?:factory\s+in|plant\s+in|unit\s+in|facility\s+in|located\s+in|based\s+in|in|at)\s+'
    r'([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\b'
)

# Common Indian cities/states to validate extracted location
_KNOWN_LOCATIONS = {
    "kolkata", "mumbai", "delhi", "new delhi", "bengaluru", "bangalore",
    "chennai", "hyderabad", "ahmedabad", "pune", "surat", "jaipur", "kanpur",
    "lucknow", "nagpur", "indore", "patna", "bhopal", "ludhiana", "vadodara",
    "faridabad", "gurugram", "gurgaon", "noida", "ghaziabad", "coimbatore",
    "west bengal", "maharashtra", "gujarat", "karnataka", "tamil nadu",
    "telangana", "uttar pradesh", "rajasthan", "punjab", "haryana", "kerala",
    "bihar", "odisha", "madhya pradesh", "assam", "jharkhand", "chhattisgarh",
    "uttarakhand", "himachal pradesh", "goa", "india"
}

# Exclusion words that follow "in" or "at" but are not locations
_NON_LOCATION_WORDS = {
    "is", "clause", "section", "part", "table", "annex", "amendment",
    "order", "standard", "standards", "compliance", "specification",
    "specifications", "bis", "qco", "isi", "india", "january", "february",
    "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december", "detail", "details", "brief",
    "summary", "english", "hindi", "terms", "force", "effect"
}

# Explicit product category patterns
_PRODUCT_CATEGORY_PATTERNS = [
    re.compile(r'\b(?:clinical\s+thermometers?|mercury\s+thermometers?|digital\s+thermometers?|medical\s+thermometers?)\b', re.IGNORECASE),
    re.compile(r'\b(?:gold\s+jewellery|gold\s+artefacts?|gold\s+articles?|hallmarked\s+gold)\b', re.IGNORECASE),
    re.compile(r'\b(?:pvc\s+pipes?|upvc\s+pipes?|cpvc\s+pipes?|polyethylene\s+pipes?)\b', re.IGNORECASE),
    re.compile(r'\b(?:pressure\s+cookers?|domestic\s+pressure\s+cookers?)\b', re.IGNORECASE),
    re.compile(r'\b(?:electric\s+cables?|power\s+cables?|pvc\s+cables?)\b', re.IGNORECASE),
    re.compile(r'\b(?:safety\s+helmets?|protective\s+helmets?|industrial\s+helmets?)\b', re.IGNORECASE),
    re.compile(r'\b(?:lead\s+acid\s+batteries?|lithium\s+batteries?|storage\s+batteries?)\b', re.IGNORECASE),
    re.compile(r'\b(?:cement|portland\s+cement|pozzolana\s+cement)\b', re.IGNORECASE),
    re.compile(r'\b(?:toys?|electric\s+toys?|non-electric\s+toys?)\b', re.IGNORECASE),
    re.compile(r'\b(?:steel\s+bars?|tmt\s+bars?|reinforcement\s+bars?|structural\s+steel)\b', re.IGNORECASE),
    re.compile(r'\b(?:bottled\s+water|packaged\s+drinking\s+water|mineral\s+water)\b', re.IGNORECASE),
]

# Contextual product capture: "our <product>", "for <product>", "applicable to <product>"
_APPLICABILITY_PRODUCT_PATTERN = re.compile(
    r'\b(?:applicable\s+to|applies\s+to|apply\s+to|for\s+our|for\s+the|covers?|manufacture\s+|producing\s+|importing\s+|regarding\s+our|about\s+our)\s+'
    r'([a-zA-Z0-9\s-]+?)(?:\s+(?:in|at|with|under|according|dated|\?|\.|$))',
    re.IGNORECASE
)


def extract_business_context_from_query(query: str) -> BusinessContext:
    """
    Conservatively extracts explicit business and product context from query text.
    Never fabricates or assumes unstated details (e.g. MSME, location).
    """
    if not query or not query.strip():
        return BusinessContext()

    q_clean = query.strip()
    ctx = BusinessContext()
    signals: List[str] = []

    # 1. Activity
    for pattern, activity in _ACTIVITY_PATTERNS:
        if pattern.search(q_clean):
            ctx.manufacturing_activity = activity
            signals.append(f"activity:{activity}")
            break

    # 1b. Business Type (Strictly explicit nouns)
    for pattern, b_type in _BUSINESS_TYPE_PATTERNS:
        if pattern.search(q_clean):
            ctx.business_type = b_type
            signals.append(f"business_type:{b_type}")
            break

    # 2. Company Size (Only if explicitly stated)
    for pattern, size_label in _COMPANY_SIZE_PATTERNS:
        if pattern.search(q_clean):
            ctx.company_size = size_label
            signals.append(f"company_size:{size_label}")
            break

    # 3. Location (Strictly validated against known regions or location context)
    for m in _LOCATION_PATTERN.finditer(q_clean):
        candidate_loc = m.group(1).strip()
        loc_lower = candidate_loc.lower()
        if loc_lower in _NON_LOCATION_WORDS:
            continue
        if loc_lower in _KNOWN_LOCATIONS or "factory in" in m.group(0).lower() or "plant in" in m.group(0).lower():
            ctx.manufacturing_location = candidate_loc
            signals.append(f"location:{candidate_loc}")
            break

    # 4. Product Category
    for pat in _PRODUCT_CATEGORY_PATTERNS:
        m = pat.search(q_clean)
        if m:
            ctx.product_category = m.group(0).strip()
            signals.append(f"product_category:{ctx.product_category}")
            break

    # If not matched by predefined categories, try applicability target extraction
    if not ctx.product_category:
        m_app = _APPLICABILITY_PRODUCT_PATTERN.search(q_clean)
        if m_app:
            target = m_app.group(1).strip()
            # Clean unwanted prepositions or pronouns
            target = re.sub(r'^(?:our|the|a|an)\s+', '', target, flags=re.IGNORECASE)
            # Ensure it's not a standard or clause identifier
            if target and not re.search(r'\b(?:IS\s+\d+|clause|section|standard)\b', target, re.IGNORECASE):
                # Only use if reasonable length
                if 2 < len(target) < 60:
                    ctx.product_category = target
                    signals.append(f"inferred_product_target:{target}")

    # 5. Customer Type
    _customer_patterns = [
        re.compile(r'\b(?:sell\s+(?:them\s+)?to|sold\s+to|supply\s+(?:them\s+)?to|supplied\s+to|distribute\s+(?:them\s+)?to|to\s+customers\s+in)\s+([a-zA-Z]+(?:\s+[a-zA-Z]+)?)\b', re.IGNORECASE),
        re.compile(r'\bfor\s+(?:use\s+in\s+)?(hospitals?|clinics?|retailers?|consumers?|dealers?|laboratories|labs|distributors|governments?|schools?)\b', re.IGNORECASE),
    ]
    for pat in _customer_patterns:
        m_cust = pat.search(q_clean)
        if m_cust:
            cand_cust = m_cust.group(1).strip()
            cand_cust = re.sub(r'[^\w\s]', '', cand_cust).strip()
            if cand_cust and cand_cust.lower() not in _NON_LOCATION_WORDS:
                ctx.customer_type = cand_cust
                signals.append(f"customer_type:{cand_cust}")
                break

    # 6. Existing Certifications
    if re.search(r'\b(?:isi\s+mark|isi\s+certified)\b', q_clean, re.IGNORECASE):
        ctx.existing_certifications.append("ISI Mark")
        signals.append("certification:ISI")
    if re.search(r'\b(?:hallmark|hallmarked)\b', q_clean, re.IGNORECASE):
        ctx.existing_certifications.append("Hallmark")
        signals.append("certification:Hallmark")

    ctx.raw_signals = signals
    return ctx


def merge_business_contexts(
    profile_context: Optional[BusinessContext],
    query_context: BusinessContext,
) -> BusinessContext:
    """
    Merges profile context with per-query context.
    Query-explicit attributes override profile attributes for this query only,
    without permanently mutating the underlying profile.
    """
    if profile_context is None or profile_context.is_empty:
        return query_context

    if query_context.is_empty:
        return profile_context

    # Clean overlay: take query value if present, otherwise fallback to profile
    merged = BusinessContext(
        business_type=query_context.business_type or profile_context.business_type,
        industry=query_context.industry or profile_context.industry,
        product_name=query_context.product_name or profile_context.product_name,
        product_category=query_context.product_category or profile_context.product_category,
        product_description=query_context.product_description or profile_context.product_description,
        manufacturing_activity=query_context.manufacturing_activity or profile_context.manufacturing_activity,
        manufacturing_location=query_context.manufacturing_location or profile_context.manufacturing_location,
        company_size=query_context.company_size or profile_context.company_size,
        target_market=query_context.target_market or profile_context.target_market,
        intended_use=query_context.intended_use or profile_context.intended_use,
        customer_type=query_context.customer_type or profile_context.customer_type,
        existing_certifications=list(set(profile_context.existing_certifications + query_context.existing_certifications)),
        existing_standards=list(set(profile_context.existing_standards + query_context.existing_standards)),
        technical_characteristics={**profile_context.technical_characteristics, **query_context.technical_characteristics},
        raw_signals=list(set(profile_context.raw_signals + query_context.raw_signals)),
    )
    return merged
