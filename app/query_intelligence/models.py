"""
app/query_intelligence/models.py

Canonical domain models for Query Intelligence, Intent Classification,
Business Context, and Structured Query Context (Phase 10).

DESIGN PRINCIPLES:
  - Intent classification occurs before retrieval strategy selection.
  - Intent confidence is strictly separate from evidence confidence.
  - Business context is explicit: never infer unstated business facts.
  - Profile context is decoupled from per-query context.
  - All controlled taxonomies are small, operational, and unambiguous.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from app.rag.query import QueryEntities


# ============================================================
# 1. CONTROLLED INTENT TAXONOMY (Section 1)
# ============================================================

class QueryIntentType(str, Enum):
    """
    Canonical, operational query intent categories.
    Keeps the taxonomy functional and precise.
    """
    # Canonical intents per system specification
    STANDARD_LOOKUP = "STANDARD_LOOKUP"                     # e.g. "What standard applies to this product?" or "IS 3055"
    QCO_APPLICABILITY = "QCO_APPLICABILITY"                 # e.g. "Does QCO X apply to my product?"
    CERTIFICATION = "CERTIFICATION"                         # e.g. "certification workflow / scheme / authority"
    TESTING_REQUIREMENT = "TESTING_REQUIREMENT"             # e.g. "testing requirements for product"
    LABORATORY_SEARCH = "LABORATORY_SEARCH"                 # e.g. "Find a testing laboratory for my product"
    PRODUCT_COMPLIANCE = "PRODUCT_COMPLIANCE"               # e.g. "product compliance requirements"
    REQUIREMENT_EXPLANATION = "REQUIREMENT_EXPLANATION"     # e.g. "what does permissible error mean?"
    SOURCE_LOOKUP = "SOURCE_LOOKUP"                         # e.g. "where is this gazette notification published?"
    REGULATORY_UPDATE = "REGULATORY_UPDATE"                 # e.g. "recent updates to quality orders"
    GENERAL_INFORMATION = "GENERAL_INFORMATION"             # e.g. "what is BIS?"
    UNSUPPORTED = "UNSUPPORTED"                             # e.g. out-of-domain or invalid query
    UNKNOWN = "UNKNOWN"                                     # e.g. unrecognized or insufficient intent

    # Retained for backward compatibility with existing tests and modules
    CLAUSE_LOOKUP = "CLAUSE_LOOKUP"                         # e.g. "IS 3055 clause 4.1"
    AMENDMENT_LOOKUP = "AMENDMENT_LOOKUP"                   # e.g. "IS 3055 amendment 1"
    VERSION_LOOKUP = "VERSION_LOOKUP"                       # e.g. "IS 3055 2024 edition"
    CURRENTNESS_QUERY = "CURRENTNESS_QUERY"                 # e.g. "current IS 3055 requirement"
    REQUIREMENT_DISCOVERY = "TESTING_REQUIREMENT"         # alias for REQUIREMENT_DISCOVERY / TESTING_REQUIREMENT
    STANDARD_DISCOVERY = "STANDARD_DISCOVERY"               # alias for STANDARD_LOOKUP
    APPLICABILITY_QUERY = "APPLICABILITY_QUERY"             # alias for QCO_APPLICABILITY / PRODUCT_COMPLIANCE
    DOCUMENT_REQUIREMENT_QUERY = "DOCUMENT_REQUIREMENT_QUERY" # e.g. "what documents are required for certification?"
    REFERENCE_LOOKUP = "REFERENCE_LOOKUP"                   # e.g. "normative references in IS 3055"
    EXPLANATION_QUERY = "EXPLANATION_QUERY"                 # alias for REQUIREMENT_EXPLANATION
    COMPARISON_QUERY = "COMPARISON_QUERY"                   # e.g. "difference between 2020 and 2024 editions"
    SCHEME_GUIDANCE = "SCHEME_GUIDANCE"                     # alias for CERTIFICATION
    PROCESS_EXPLANATION = "PROCESS_EXPLANATION"             # alias for CERTIFICATION
    AMBIGUOUS_QUERY = "AMBIGUOUS_QUERY"                     # alias for UNKNOWN / AMBIGUOUS


# ============================================================
# 2. QUERY LIFECYCLE STATE (Section 8)
# ============================================================

class QueryLifecycleState(str, Enum):
    """
    State of the query context driving downstream retrieval and verification behavior.
    """
    NORMAL = "NORMAL"
    AMBIGUOUS = "AMBIGUOUS"
    MISSING_REQUIRED_CONTEXT = "MISSING_REQUIRED_CONTEXT"
    TEMPORAL = "TEMPORAL"
    IDENTIFIER_SPECIFIC = "IDENTIFIER_SPECIFIC"
    SEMANTIC = "SEMANTIC"


# ============================================================
# 3. RETRIEVAL STRATEGY TAXONOMY (Section 12)
# ============================================================

class RetrievalStrategy(str, Enum):
    """
    Operational retrieval strategy selected on the basis of intent and entities.
    """
    IDENTIFIER_HEAVY = "IDENTIFIER_HEAVY"
    STANDARD_METADATA = "STANDARD_METADATA"
    TEMPORAL_AWARE = "TEMPORAL_AWARE"
    SEMANTIC_CONTEXTUAL = "SEMANTIC_CONTEXTUAL"
    STANDARD_DISCOVERY = "STANDARD_DISCOVERY"
    REFERENCE_GRAPH = "REFERENCE_GRAPH"
    APPLICABILITY_EVALUATION = "APPLICABILITY_EVALUATION"
    BROAD_FALLBACK = "BROAD_FALLBACK"


# ============================================================
# 4. STRUCTURED INTENT CLASSIFICATION (Section 2, 7)
# ============================================================

@dataclass
class IntentClassification:
    """
    Structured outcome of query intent classification.
    Intent confidence is strictly decoupled from evidence confidence.
    """
    intent: QueryIntentType
    intent_confidence: float                                # in [0.0, 1.0]
    signals: List[str] = field(default_factory=list)
    candidate_intents: List[Dict[str, Any]] = field(default_factory=list)
    is_ambiguous: bool = False
    classifier_type: str = "deterministic"                  # "deterministic" | "llm" | "fallback"
    reasoning: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent.value if isinstance(self.intent, QueryIntentType) else str(self.intent),
            "intent_confidence": round(float(self.intent_confidence), 4),
            "signals": self.signals,
            "candidate_intents": self.candidate_intents,
            "is_ambiguous": self.is_ambiguous,
            "classifier_type": self.classifier_type,
            "reasoning": self.reasoning,
        }


@dataclass
class StructuredQueryIntent:
    """
    Structured outcome of query intent classification and entity extraction (Requirement 2).
    Captures:
      - intent (STANDARD_LOOKUP, QCO_APPLICABILITY, CERTIFICATION, TESTING_REQUIREMENT,
        LABORATORY_SEARCH, PRODUCT_COMPLIANCE, REQUIREMENT_EXPLANATION, SOURCE_LOOKUP,
        REGULATORY_UPDATE, GENERAL_INFORMATION, UNSUPPORTED, UNKNOWN)
      - product identity (product, product_category)
      - intended use and technical characteristics
      - standard and QCO identifiers
      - jurisdiction and requested entity/service
      - constraints and diagnostic signals
    """
    intent: QueryIntentType
    intent_confidence: float = 1.0
    product: Optional[str] = None
    product_category: Optional[str] = None
    intended_use: Optional[str] = None
    technical_characteristics: Dict[str, Any] = field(default_factory=dict)
    standard_identifiers: List[str] = field(default_factory=list)
    qco_identifiers: List[str] = field(default_factory=list)
    jurisdiction: Optional[str] = None
    requested_entity_or_service: Optional[str] = None
    constraints: Dict[str, Any] = field(default_factory=dict)
    signals: List[str] = field(default_factory=list)
    candidate_intents: List[Dict[str, Any]] = field(default_factory=list)
    is_ambiguous: bool = False
    classifier_type: str = "deterministic"
    raw_query: str = ""
    reasoning: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent.value if isinstance(self.intent, QueryIntentType) else str(self.intent),
            "intent_confidence": round(float(self.intent_confidence), 4),
            "product": self.product,
            "product_category": self.product_category,
            "intended_use": self.intended_use,
            "technical_characteristics": self.technical_characteristics,
            "standard_identifiers": self.standard_identifiers,
            "qco_identifiers": self.qco_identifiers,
            "jurisdiction": self.jurisdiction,
            "requested_entity_or_service": self.requested_entity_or_service,
            "constraints": self.constraints,
            "signals": self.signals,
            "candidate_intents": self.candidate_intents,
            "is_ambiguous": self.is_ambiguous,
            "classifier_type": self.classifier_type,
            "raw_query": self.raw_query,
            "reasoning": self.reasoning,
        }


# ============================================================
# 5. BUSINESS CONTEXT MODEL (Section 9, 10, 18, 19)
# ============================================================

@dataclass
class BusinessContext:
    """
    Canonical representation of explicit business and product context.
    All fields are optional. Never infers hidden business facts (e.g. MSME,
    factory size, location) unless explicitly stated in input text or profile.
    """
    business_type: Optional[str] = None                     # e.g. "manufacturer", "importer", "distributor"
    industry: Optional[str] = None                          # e.g. "medical devices", "metallurgy"
    product_name: Optional[str] = None                      # e.g. "Digital Clinical Thermometer Model X"
    product_category: Optional[str] = None                  # e.g. "clinical thermometers"
    product_description: Optional[str] = None               # e.g. "mercury-in-glass thermometer"
    manufacturing_activity: Optional[str] = None            # e.g. "manufacturing", "packaging", "assembly"
    manufacturing_location: Optional[str] = None            # e.g. "Kolkata", "Mumbai"
    company_size: Optional[str] = None                      # ONLY if explicitly stated (e.g. "large", "MSME")
    target_market: Optional[str] = None                     # e.g. "domestic", "export"
    intended_use: Optional[str] = None                      # e.g. "clinical diagnosis", "industrial measurement"
    customer_type: Optional[str] = None                     # e.g. "hospitals", "retail"
    existing_certifications: List[str] = field(default_factory=list)
    existing_standards: List[str] = field(default_factory=list)
    technical_characteristics: Dict[str, Any] = field(default_factory=dict)
    raw_signals: List[str] = field(default_factory=list)

    @property
    def product(self) -> Optional[str]:
        """Convenience alias for product category or name."""
        return self.product_name or self.product_category or self.product_description

    @property
    def location(self) -> Optional[str]:
        """Convenience alias for manufacturing location."""
        return self.manufacturing_location

    @property
    def has_explicit_product(self) -> bool:
        """True if any product identification is explicitly present."""
        return bool(
            (self.product_name and self.product_name.strip())
            or (self.product_category and self.product_category.strip())
            or (self.product_description and self.product_description.strip())
        )

    @property
    def is_empty(self) -> bool:
        """True if no business context fields are populated."""
        return not any([
            self.business_type,
            self.industry,
            self.product_name,
            self.product_category,
            self.product_description,
            self.manufacturing_activity,
            self.manufacturing_location,
            self.company_size,
            self.target_market,
            self.intended_use,
            self.customer_type,
            self.existing_certifications,
            self.existing_standards,
            self.technical_characteristics,
        ])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "business_type": self.business_type,
            "industry": self.industry,
            "product": self.product,
            "product_name": self.product_name,
            "product_category": self.product_category,
            "product_description": self.product_description,
            "manufacturing_activity": self.manufacturing_activity,
            "location": self.location,
            "manufacturing_location": self.manufacturing_location,
            "company_size": self.company_size,
            "target_market": self.target_market,
            "intended_use": self.intended_use,
            "customer_type": self.customer_type,
            "existing_certifications": self.existing_certifications,
            "existing_standards": self.existing_standards,
            "technical_characteristics": self.technical_characteristics,
            "has_explicit_product": self.has_explicit_product,
        }

    @classmethod
    def from_dict(cls, d: Optional[Any]) -> "BusinessContext":
        if isinstance(d, cls):
            return d
        if not d or not isinstance(d, dict):
            return cls()
        return cls(
            business_type=d.get("business_type"),
            industry=d.get("industry"),
            product_name=d.get("product_name"),
            product_category=d.get("product_category") or d.get("product"),
            product_description=d.get("product_description"),
            manufacturing_activity=d.get("manufacturing_activity"),
            manufacturing_location=d.get("manufacturing_location") or d.get("location"),
            company_size=d.get("company_size"),
            target_market=d.get("target_market"),
            intended_use=d.get("intended_use"),
            customer_type=d.get("customer_type"),
            existing_certifications=d.get("existing_certifications") or [],
            existing_standards=d.get("existing_standards") or [],
            technical_characteristics=d.get("technical_characteristics") or {},
            raw_signals=d.get("raw_signals") or [],
        )


# ============================================================
# 6. STRUCTURED QUERY CONTEXT (Section 11)
# ============================================================

@dataclass
class QueryContext:
    """
    Canonical, structured input to all downstream retrieval, temporal,
    confidence, and generation application logic.
    """
    original_query: str
    normalized_query: str
    intent: IntentClassification
    entities: QueryEntities
    business_context: BusinessContext
    profile_context: Optional[BusinessContext] = None
    query_state: QueryLifecycleState = QueryLifecycleState.NORMAL
    retrieval_strategy: RetrievalStrategy = RetrievalStrategy.SEMANTIC_CONTEXTUAL
    retrieval_query_variants: List[str] = field(default_factory=list)
    structured_intent: Optional[StructuredQueryIntent] = None
    trace: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_query": self.original_query,
            "normalized_query": self.normalized_query,
            "intent": self.intent.to_dict(),
            "structured_intent": self.structured_intent.to_dict() if self.structured_intent else None,
            "entities": self.entities.to_dict(),
            "business_context": self.business_context.to_dict(),
            "profile_context": self.profile_context.to_dict() if self.profile_context else None,
            "query_state": self.query_state.value if isinstance(self.query_state, QueryLifecycleState) else str(self.query_state),
            "retrieval_strategy": self.retrieval_strategy.value if isinstance(self.retrieval_strategy, RetrievalStrategy) else str(self.retrieval_strategy),
            "retrieval_query_variants": self.retrieval_query_variants,
            "trace": self.trace,
        }
