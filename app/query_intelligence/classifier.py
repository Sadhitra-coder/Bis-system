"""
app/query_intelligence/classifier.py

Canonical Query Intent Classification for the BIS Compliance Engine (Phase 10).

DESIGN PRINCIPLES:
  - Deterministic First: obvious and explicit patterns are resolved without LLM.
  - Ambiguity Detection: vague, single-word fragments ("lity") or unmapped queries
    return AMBIGUOUS_QUERY or low confidence.
  - Decoupled Confidence: intent confidence reflects classification certainty,
    never evidence confidence.
  - Strict Guardrails: LLM cannot decide confidence or invent arbitrary intent names.
  - Safe Fallback: LLM failure never breaks query handling.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.llm_client import OpenAIClient
from app.rag.query import QueryEntities, normalize_query
from app.query_intelligence.models import IntentClassification, QueryIntentType


logger = logging.getLogger(__name__)


# ============================================================
# DETERMINISTIC INTENT REGEX PATTERNS
# ============================================================

# 1. Applicability intent: "is IS 3055 applicable...", "does this apply to...", "mandatory for..."
_APPLICABILITY_PATTERN = re.compile(
    r'\b(?:applicable\s+(?:to|for|under)|applies\s+(?:to|for)|apply\s+(?:to|for)|'
    r'applicability|is\s+(?:bis\s+)?(?:certification\s+)?mandatory\s*(?:for|to|\?|$)|'
    r'do\s+(?:we|i)\s+need\s+(?:bis|isi|standard|certification|a\s+license)|'
    r'is\s+(?:bis\s+)?(?:certification\s+)?required\s+(?:for|to)|covered\s+under\s+bis|'
    r'does\s+bis\s+apply|under\s+the\s+scope\s+of|does\s+qco\s+apply)\b',
    re.IGNORECASE
)

# 2. Standard discovery: "which standard...", "what standard...", "find standard for..."
_STANDARD_DISCOVERY_PATTERN = re.compile(
    r'\b(?:which\s+(?:indian\s+)?(?:bis\s+)?standards?|what\s+(?:indian\s+)?(?:bis\s+)?standards?|'
    r'find\s+(?:indian\s+)?(?:bis\s+)?standards?|standards?\s+for\b|standards?\s+governing|'
    r'standards?\s+covering|which\s+is\s+covers?|what\s+standard\s+governs?|'
    r'which\s+(?:is\s+)?standard\s+should\s+be\s+followed|standards?\s+applicable\s+to)\b',
    re.IGNORECASE
)

# 3. Document requirements: "what documents required", "forms needed", "application checklist"
_DOCUMENT_REQ_PATTERN = re.compile(
    r'\b(?:documents?\s+required|documentation\s+required|forms?\s+required|'
    r'checklist|application\s+documents?|test\s+reports?\s+required|filing\s+requirements?|'
    r'documents?\s+needed|papers?\s+required|what\s+(?:technical\s+)?documents?|'
    r'test\s+reports?\s+and\s+factory\s+layout|documentation\s+needed|'
    r'(?:documents?|documentation|forms?|test\s+reports?)\s+.*?\s+(?:required|needed|to\s+apply))\b',
    re.IGNORECASE
)

# 4. Reference lookup: "normative references", "referred standards", "cited in"
_REFERENCE_PATTERN = re.compile(
    r'\b(?:normative\s+references?|referred\s+standards?|cited\s+standards?|'
    r'references?\s+in\b|reference\s+to\s+other\s+standards?|cross\s+references?|'
    r'referenced\s+or\s+cited|standards?\s+are\s+referenced|standards?\s+are\s+cited|'
    r'cited\s+by|referenced\s+by)\b',
    re.IGNORECASE
)

# 5. Explanation intent: "what does X mean", "explain clause...", "definition of..."
_EXPLANATION_PATTERN = re.compile(
    r'\b(?:what\s+does\s+.*mean|explain\b|definition\s+of|clarify\b|meaning\s+of|why\s+was\b|why\s+is\b|rationale\s+(?:behind|for|of)|purpose\s+of)\b',
    re.IGNORECASE
)

# 6. Comparison intent: "difference between...", "compare ... and ..."
_COMPARISON_PATTERN = re.compile(
    r'\b(?:difference\s+between|differences\s+between|compare\b|comparison\b|versus\b|\bvs\b|distinction\s+between)\b',
    re.IGNORECASE
)

# 7. Requirement discovery: "testing requirements", "permissible error", "specifications for"
_REQUIREMENT_DISCOVERY_PATTERN = re.compile(
    r'\b(?:requirements?\s+(?:for|of)|testing\s+requirements?|test\s+requirements?|'
    r'permissible\s+error|tolerances?|sampling\s+plan|calibration\s+requirements?|'
    r'inspection\s+requirements?|marking\s+requirements?|packaging\s+requirements?|'
    r'performance\s+requirements?|technical\s+specifications?|specifications?\s+for|'
    r'procedure\s+and\s+impact\s+limits|limits\s+specified)\b',
    re.IGNORECASE
)

# 8. Scheme guidance: "which scheme applies", "what certification scheme", "scheme for toys"
_SCHEME_GUIDANCE_PATTERN = re.compile(
    r'\b(?:which\s+(?:certification\s+)?scheme|what\s+(?:certification\s+)?scheme|'
    r'scheme\s+(?:applies|applicable|governs|required)|'
    r'applicable\s+(?:certification\s+)?scheme|'
    r'scheme\s+i\b|scheme\s+ii\b|scheme\s+iv\b|scheme\s+x\b|'
    r'isi\s+or\s+crs|crs\s+or\s+isi|fmcs\s+route|hallmarking\s+scheme|'
    r'certification\s+scheme\s+(?:for|under))\b',
    re.IGNORECASE
)

# 9. Process explanation / checklist: "how do I get certification", "steps for certification", "how to get certified"
_PROCESS_EXPLANATION_PATTERN = re.compile(
    r'\b(?:how\s+(?:do\s+i|can\s+i|to)\s+(?:get|obtain|apply\s+for)\s+(?:bis\s+)?(?:certification|license|licence|cml|isi\s+mark)|'
    r'how\s+(?:do\s+i|can\s+i|to)\s+get\s+certified|'
    r'certification\s+process|certification\s+procedure|certification\s+journey|'
    r'steps\s+(?:to|for)\s+(?:certification|bis\s+license|get\s+certified)|'
    r'procedure\s+(?:to|for)\s+(?:get|obtain)\s+certification|'
    r'certification\s+checklist|how\s+to\s+get\s+isi|process\s+for\s+getting\s+isi|'
    r'(?:step\s+by\s+step\s+)?process\s+(?:to|for)\s+(?:get\s+certified|obtain\s+certification|certification))\b',
    re.IGNORECASE
)

# 10. General information: "what is BIS", "overview of BIS"
_GENERAL_INFO_PATTERN = re.compile(
    r'\b(?:what\s+is\s+(?:the\s+)?(?:bis|bureau\s+of\s+indian\s+standards)|'
    r'how\s+does\s+bis\s+(?:work|operate)|overview\s+of\s+bis|about\s+bis|'
    r'role\s+and\s+organizational\s+function|functions?\s+of\s+(?:the\s+)?(?:bis|bureau)|'
    r'bureau\s+of\s+indian\s+standards\s+and\s+its\s+functions?)\b',
    re.IGNORECASE
)

# 9. Clause explicit keywords
_CLAUSE_KEYWORD_PATTERN = re.compile(r'\b(?:clause|section|sub-clause|subclause)\s+\d+', re.IGNORECASE)

# 10. Amendment explicit keywords
_AMENDMENT_KEYWORD_PATTERN = re.compile(r'\b(?:amendment|amd\.?)\s+\d+', re.IGNORECASE)

# 11. Hindi / Devanagari patterns
_HINDI_CLAUSE_PATTERN = re.compile(r'(?:खंड|धारा|अनुभाग)\s*(\d+(?:\.\d+)*)', re.UNICODE)
_HINDI_AMENDMENT_PATTERN = re.compile(r'(?:संशोधन|संशोधन\s+आदेश)\s*(\d+)?', re.UNICODE)
_HINDI_APPLICABILITY_PATTERN = re.compile(r'(?:लागू\s+होता|लागू\s+है|क्या\s+लागू|अनिवार्य|आवश्यकता\s+है)', re.UNICODE)
_HINDI_STANDARD_DISCOVERY_PATTERN = re.compile(r'(?:कौन\s+सा\s+मानक|मानक\s+कौन|कौन\s+से\s+मानक)', re.UNICODE)
_HINDI_REQUIREMENT_DISCOVERY_PATTERN = re.compile(r'(?:आवश्यकताएं|ज़रूरतें|परीक्षण\s+प्रक्रिया|मापदंड)', re.UNICODE)
_HINDI_GENERAL_INFO_PATTERN = re.compile(r'(?:भारतीय\s+मानक\s+ब्यूरो|बीआईएस|मानक\s+क्या\s+है|मानक\s+के\s+बारे\s+में)', re.UNICODE)


# ============================================================
# DETERMINISTIC CLASSIFIER (Section 4, 5)
# ============================================================

def classify_intent_deterministic(
    query: str,
    entities: QueryEntities,
) -> IntentClassification:
    """
    Deterministically classifies query intent based on explicit structural
    signals and keyword patterns. Never forces a high-confidence intent on
    ambiguous, truncated, or vague queries.
    """
    raw_q = query.strip()
    norm_q = normalize_query(query)
    words = [w for w in re.split(r'\W+', norm_q) if len(w) > 0]
    word_count = len(words)

    signals: List[str] = []

    # -----------------------------------------------------------------------
    # 0. AMBIGUOUS / TRUNCATED / FRAGMENT QUERY GUARD (Section 5)
    # -----------------------------------------------------------------------
    if not raw_q:
        return IntentClassification(
            intent=QueryIntentType.AMBIGUOUS_QUERY,
            intent_confidence=0.10,
            signals=["empty_query"],
            is_ambiguous=True,
            classifier_type="deterministic",
            reasoning="Query is empty.",
        )

    # Single-word fragments (e.g. "lity", "thermometer", "compliance") without explicit standard
    if word_count <= 1 and not (entities.standard_number or re.search(r'\d+', norm_q)):
        return IntentClassification(
            intent=QueryIntentType.AMBIGUOUS_QUERY,
            intent_confidence=0.30,
            signals=["single_word_fragment"],
            is_ambiguous=True,
            classifier_type="deterministic",
            reasoning=f"Query '{raw_q}' is an isolated word fragment without sufficient context.",
        )

    # -----------------------------------------------------------------------
    # 1. DETECT ALL SIGNALS AND BUILD CANDIDATE INTENT MAP
    # -----------------------------------------------------------------------
    cand_map: Dict[QueryIntentType, float] = {}

    has_comparison = bool(_COMPARISON_PATTERN.search(norm_q))
    if has_comparison:
        cand_map[QueryIntentType.COMPARISON_QUERY] = 0.90
        signals.append("keyword:comparison")

    has_std_discovery = bool(_STANDARD_DISCOVERY_PATTERN.search(norm_q))
    if has_std_discovery:
        cand_map[QueryIntentType.STANDARD_DISCOVERY] = 0.95
        signals.append("keyword:standard_discovery")

    has_ref_lookup = bool(_REFERENCE_PATTERN.search(norm_q))
    if has_ref_lookup:
        cand_map[QueryIntentType.REFERENCE_LOOKUP] = 0.92
        signals.append("keyword:reference_lookup")

    has_doc_req = bool(_DOCUMENT_REQ_PATTERN.search(norm_q))
    if has_doc_req:
        cand_map[QueryIntentType.DOCUMENT_REQUIREMENT_QUERY] = 0.92
        signals.append("keyword:document_requirement")

    has_explanation = bool(_EXPLANATION_PATTERN.search(norm_q))
    if has_explanation:
        cand_map[QueryIntentType.EXPLANATION_QUERY] = 0.88
        signals.append("keyword:explanation")

    has_amendment = bool(
        entities.amendment_number
        or _AMENDMENT_KEYWORD_PATTERN.search(norm_q)
        or _HINDI_AMENDMENT_PATTERN.search(norm_q)
        or "amendment order" in norm_q.lower()
    )
    if has_amendment:
        cand_map[QueryIntentType.AMENDMENT_LOOKUP] = 0.98 if entities.standard_number else 0.90
        signals.append("keyword:amendment")

    has_currentness = (
        getattr(entities, "relative_temporal", None) == "current"
        or getattr(entities, "temporal_intent", None) == "current"
        or bool(re.search(r'\b(?:current\b|latest\b|active\b|in\s+force\b|present\s+edition|newest\b)\b', norm_q, re.IGNORECASE))
    )
    if has_currentness:
        cand_map[QueryIntentType.CURRENTNESS_QUERY] = 0.96 if entities.standard_number else 0.85
        signals.append("temporal:currentness")

    has_version = (
        entities.standard_year is not None
        or bool(entities.edition_or_version)
        or getattr(entities, "relative_temporal", None) == "historical"
        or getattr(entities, "temporal_intent", None) == "historical"
        or bool(re.search(r'\b(?:previous\s+edition|earlier\s+edition|old\s+version|superseded)\b', norm_q, re.IGNORECASE))
    )
    if has_version and not has_currentness and not has_comparison:
        cand_map[QueryIntentType.VERSION_LOOKUP] = 0.95 if entities.standard_number else 0.85
        signals.append("temporal:version_signal")
    elif has_version:
        cand_map[QueryIntentType.VERSION_LOOKUP] = 0.80

    has_clause = bool(
        entities.clause_id
        or _CLAUSE_KEYWORD_PATTERN.search(norm_q)
        or _HINDI_CLAUSE_PATTERN.search(norm_q)
    )
    if has_clause:
        cand_map[QueryIntentType.CLAUSE_LOOKUP] = 0.98 if entities.standard_number else 0.88
        signals.append("keyword:clause")

    has_applicability = bool(
        _APPLICABILITY_PATTERN.search(norm_q)
        or _HINDI_APPLICABILITY_PATTERN.search(norm_q)
    )
    if has_applicability and not has_std_discovery:
        cand_map[QueryIntentType.APPLICABILITY_QUERY] = 0.95 if entities.standard_number else 0.88
        signals.append("keyword:applicability")
    elif has_applicability:
        cand_map[QueryIntentType.APPLICABILITY_QUERY] = 0.70

    if entities.standard_number:
        cand_map[QueryIntentType.STANDARD_LOOKUP] = 0.96
        signals.append("entity:standard_number")

    has_req_discovery = bool(
        _REQUIREMENT_DISCOVERY_PATTERN.search(norm_q)
        or _HINDI_REQUIREMENT_DISCOVERY_PATTERN.search(norm_q)
    )
    if has_req_discovery:
        req_conf = 0.86 if entities.standard_number else (0.65 if word_count <= 3 else 0.75)
        cand_map[QueryIntentType.REQUIREMENT_DISCOVERY] = req_conf
        signals.append("keyword:requirement_discovery")

    has_scheme_guidance = bool(_SCHEME_GUIDANCE_PATTERN.search(norm_q))
    if has_scheme_guidance:
        cand_map[QueryIntentType.SCHEME_GUIDANCE] = 0.96
        signals.append("keyword:scheme_guidance")

    has_process_explanation = bool(_PROCESS_EXPLANATION_PATTERN.search(norm_q))
    if has_process_explanation:
        cand_map[QueryIntentType.PROCESS_EXPLANATION] = 0.96
        signals.append("keyword:process_explanation")

    has_general_info = bool(
        _GENERAL_INFO_PATTERN.search(norm_q)
        or _HINDI_GENERAL_INFO_PATTERN.search(norm_q)
    )
    if has_general_info:
        cand_map[QueryIntentType.GENERAL_INFORMATION] = 0.85
        signals.append("keyword:general_information")

    # -----------------------------------------------------------------------
    # 2. SELECT PRIMARY INTENT ACCORDING TO HIERARCHICAL PRECEDENCE
    # -----------------------------------------------------------------------
    primary_intent: QueryIntentType
    primary_confidence: float
    primary_reasoning: str

    if has_comparison:
        primary_intent = QueryIntentType.COMPARISON_QUERY
        primary_confidence = 0.90
        primary_reasoning = "Inquiry comparing multiple standards, editions, or requirements."
    elif has_scheme_guidance:
        primary_intent = QueryIntentType.SCHEME_GUIDANCE
        primary_confidence = 0.96
        primary_reasoning = "Inquiry seeking guidance on applicable BIS certification scheme."
    elif has_process_explanation:
        primary_intent = QueryIntentType.PROCESS_EXPLANATION
        primary_confidence = 0.96
        primary_reasoning = "Inquiry seeking structured steps and process for obtaining BIS certification."
    elif has_std_discovery:
        primary_intent = QueryIntentType.STANDARD_DISCOVERY
        primary_confidence = 0.95
        primary_reasoning = "Inquiry seeking which standard governs a product or activity."
    elif has_ref_lookup:
        primary_intent = QueryIntentType.REFERENCE_LOOKUP
        primary_confidence = 0.92
        primary_reasoning = "Inquiry examining cross-references or normative citations."
    elif has_doc_req:
        primary_intent = QueryIntentType.DOCUMENT_REQUIREMENT_QUERY
        primary_confidence = 0.92
        primary_reasoning = "Inquiry seeking required forms, documentation, or application filings."
    elif has_explanation:
        primary_intent = QueryIntentType.EXPLANATION_QUERY
        primary_confidence = 0.88
        primary_reasoning = "Conceptual inquiry seeking explanation or rationale behind a requirement."
    elif has_amendment:
        primary_intent = QueryIntentType.AMENDMENT_LOOKUP
        primary_confidence = 0.98 if entities.standard_number else 0.90
        primary_reasoning = "Amendment lookup targeting specific amendment provisions."
    elif has_currentness:
        primary_intent = QueryIntentType.CURRENTNESS_QUERY
        primary_confidence = 0.96 if entities.standard_number else 0.85
        primary_reasoning = "Inquiry regarding the active currentness or validity of a standard."
    elif has_version and not entities.clause_id:
        primary_intent = QueryIntentType.VERSION_LOOKUP
        primary_confidence = 0.95 if entities.standard_number else 0.85
        primary_reasoning = "Lookup targeting a specific version, edition, or historical state."
    elif has_clause:
        primary_intent = QueryIntentType.CLAUSE_LOOKUP
        primary_confidence = 0.98 if entities.standard_number else 0.88
        primary_reasoning = "Exact clause lookup within an identified standard."
    elif has_applicability:
        primary_intent = QueryIntentType.APPLICABILITY_QUERY
        primary_confidence = 0.95 if entities.standard_number else 0.85
        primary_reasoning = "Inquiry asking whether BIS certification or a standard applies."
    elif entities.standard_number and not has_req_discovery:
        primary_intent = QueryIntentType.STANDARD_LOOKUP
        primary_confidence = 0.96
        primary_reasoning = f"Direct lookup of standard {entities.standard_number}."
    elif has_req_discovery:
        primary_intent = QueryIntentType.REQUIREMENT_DISCOVERY
        primary_confidence = 0.86 if entities.standard_number else (0.65 if word_count <= 3 else 0.75)
        primary_reasoning = "Discovery of specific technical, testing, or performance requirements."
    elif has_general_info:
        primary_intent = QueryIntentType.GENERAL_INFORMATION
        primary_confidence = 0.85
        primary_reasoning = "General informational question regarding regulatory processes."
    elif entities.standard_number:
        primary_intent = QueryIntentType.STANDARD_LOOKUP
        primary_confidence = 0.96
        primary_reasoning = f"Direct lookup of standard {entities.standard_number}."
    elif "requirement" in norm_q.lower():
        primary_intent = QueryIntentType.REQUIREMENT_DISCOVERY
        primary_confidence = 0.60
        primary_reasoning = "Requirement discovery without specific standard identifier."
    elif re.search(r'[\u0900-\u097F]', norm_q) and word_count >= 2:
        return IntentClassification(
            intent=QueryIntentType.GENERAL_INFORMATION,
            intent_confidence=0.65,
            signals=["devanagari_query"],
            candidate_intents=[],
            is_ambiguous=False,
            classifier_type="deterministic",
            reasoning="Informational query in Hindi/Devanagari script.",
        )
    else:
        return IntentClassification(
            intent=QueryIntentType.AMBIGUOUS_QUERY,
            intent_confidence=0.40,
            signals=["no_distinct_signal"],
            candidate_intents=[],
            is_ambiguous=True,
            classifier_type="deterministic",
            reasoning="Query lacks specific identifiers, domain keywords, or clear intent.",
        )

    # Candidate intents list (excluding primary)
    candidate_list = [
        {"intent": intent_type.value, "score": score}
        for intent_type, score in cand_map.items()
        if intent_type != primary_intent
    ]

    return IntentClassification(
        intent=primary_intent,
        intent_confidence=primary_confidence,
        signals=signals,
        candidate_intents=candidate_list,
        is_ambiguous=False,
        classifier_type="deterministic",
        reasoning=primary_reasoning,
    )


# ============================================================
# OPTIONAL LLM INTENT CLASSIFIER (Section 6)
# ============================================================

_ALLOWED_INTENT_VALUES = {i.value for i in QueryIntentType}

def classify_intent_llm(
    query: str,
    entities: QueryEntities,
    client: Optional[OpenAIClient] = None,
) -> Optional[IntentClassification]:
    """
    Optional LLM classifier using strictly structured output.
    Feature-flagged via settings.ENABLE_LLM_INTENT_CLASSIFIER.
    The LLM never determines confidence; confidence is assigned by application logic.
    """
    if not getattr(settings, "ENABLE_LLM_INTENT_CLASSIFIER", False):
        return None

    if not getattr(settings, "OPENAI_API_KEY", None):
        return None

    openai_client = client or OpenAIClient()
    prompt = f"""
You are an intent classifier for Indian Standards compliance intelligence.
Classify the user query into EXACTLY one of the allowed categories:
{', '.join(sorted(_ALLOWED_INTENT_VALUES))}

User Query: "{query}"
Extracted Identifiers: {json.dumps(entities.to_dict())}

Return valid JSON matching this schema:
{{
  "intent": "<EXACT_INTENT_NAME>",
  "reasoning": "<brief justification>"
}}
Do NOT output anything else.
"""
    try:
        raw_res = openai_client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=settings.OPENAI_MODEL,
            temperature=0.0,
            max_tokens=200,
        )
        data = json.loads(raw_res)
        intent_name = data.get("intent", "").strip().upper()
        if intent_name in _ALLOWED_INTENT_VALUES:
            intent_enum = QueryIntentType(intent_name)
            # Application assigns confidence; LLM does NOT decide confidence
            conf = 0.85 if intent_enum != QueryIntentType.AMBIGUOUS_QUERY else 0.40
            return IntentClassification(
                intent=intent_enum,
                intent_confidence=conf,
                signals=["llm_classification"],
                is_ambiguous=(intent_enum == QueryIntentType.AMBIGUOUS_QUERY),
                classifier_type="llm",
                reasoning=data.get("reasoning", "Classified via structured LLM prompt."),
            )
        else:
            logger.warning("LLM emitted invalid intent name: %s. Falling back.", intent_name)
            return None
    except Exception as e:
        logger.warning("LLM intent classification failed: %s. Falling back to deterministic.", e)
        return None


# ============================================================
# UNIFIED INTENT CLASSIFIER (Section 4, 6)
# ============================================================

def classify_intent(
    query: str,
    entities: QueryEntities,
    client: Optional[OpenAIClient] = None,
) -> IntentClassification:
    """
    Unified entry point. Evaluates deterministic classifier first.
    If ambiguous and LLM classification is enabled, queries LLM classifier with safe fallback.
    """
    deterministic_res = classify_intent_deterministic(query, entities)

    # If unambiguous with high confidence, return deterministic result directly
    if not deterministic_res.is_ambiguous and deterministic_res.intent_confidence >= 0.85:
        return deterministic_res

    # If ambiguous or low confidence, and LLM classifier is enabled, try LLM
    if getattr(settings, "ENABLE_LLM_INTENT_CLASSIFIER", False):
        llm_res = classify_intent_llm(query, entities, client=client)
        if llm_res is not None:
            return llm_res

    return deterministic_res
