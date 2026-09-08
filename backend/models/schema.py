from datetime import date
from enum import Enum
from typing import Optional, Union, List, Dict, Any
from pydantic import BaseModel, Field, field_validator


# -------------------------
# Enums
# -------------------------

class ValueType(str, Enum):
    NUMBER = "number"
    PERCENTAGE = "percentage"
    CURRENCY = "currency"
    TEXT = "text"
    BOOLEAN = "boolean"


class PeriodType(str, Enum):
    INSTANT = "instant"
    MONTH = "month"
    QUARTER = "quarter"
    HALF_YEAR = "half_year"
    FISCAL_YEAR = "fiscal_year"
    CUSTOM = "custom"
    UNKNOWN = "unknown"


class AssertionStatus(str, Enum):
    ACTUAL = "actual"
    ESTIMATE = "estimate"
    FORECAST = "forecast"
    PROJECTED = "projected"
    TARGET = "target"
    RESTATED = "restated"
    UNKNOWN = "unknown"


class RelationshipType(str, Enum):
    CORROBORATED = "corroborated"
    CONTRADICTED = "contradicted"
    CONTEXTUALIZED = "contextualized"
    APPARENT_CONTRADICTION = "apparent_contradiction"
    NOT_COMPARABLE = "not_comparable"
    UNRESOLVED = "unresolved"


# -------------------------
# Value
# -------------------------

class FactValue(BaseModel):
    type: ValueType

    # For numerical values
    amount: Optional[float] = None

    # Example: INR, USD, %, crore, lakh, million
    unit: Optional[str] = None

    # Normalized representation used by comparison logic
    normalized_amount: Optional[float] = None
    normalized_unit: Optional[str] = None

    # For semantic / textual facts
    text: Optional[str] = None


# -------------------------
# Time
# -------------------------

class TimeContext(BaseModel):
    period_type: PeriodType = PeriodType.CUSTOM

    start_date: Optional[date] = None
    end_date: Optional[date] = None

    # Preserve original expression from document
    # e.g. "H1 FY25", "FY2024", "as of December 31, 2021"
    label: Optional[str] = None

    @field_validator("period_type", mode="before")
    @classmethod
    def coerce_period_type(cls, v: Any) -> PeriodType:
        if v is None:
            return PeriodType.CUSTOM
        if isinstance(v, PeriodType):
            return v
        s = str(v).lower().strip().replace("-", "_").replace(" ", "_")
        MAPPING = {
            "instant": PeriodType.INSTANT,
            "point_in_time": PeriodType.INSTANT,
            "as_of": PeriodType.INSTANT,
            "month": PeriodType.MONTH,
            "monthly": PeriodType.MONTH,
            "quarter": PeriodType.QUARTER,
            "quarterly": PeriodType.QUARTER,
            "half_year": PeriodType.HALF_YEAR,
            "half_yearly": PeriodType.HALF_YEAR,
            "semi_annual": PeriodType.HALF_YEAR,
            "fiscal_year": PeriodType.FISCAL_YEAR,
            "annual": PeriodType.FISCAL_YEAR,
            "yearly": PeriodType.FISCAL_YEAR,
            "year": PeriodType.FISCAL_YEAR,
            "custom": PeriodType.CUSTOM,
            "unknown": PeriodType.UNKNOWN,
        }
        return MAPPING.get(s, PeriodType.CUSTOM)


# -------------------------
# Entity
# -------------------------

class Entity(BaseModel):
    id: Optional[str] = None
    canonical_name: str
    entity_type: Optional[str] = None

    # Names actually found in source
    aliases: List[str] = Field(default_factory=list)


# -------------------------
# Concept
# -------------------------

class Concept(BaseModel):
    id: Optional[str] = None
    canonical_name: str

    # Original wording from document
    source_label: Optional[str] = None

    definition: Optional[str] = None


# -------------------------
# Dynamic Resolution Schemas
# -------------------------

class ResolutionStatus(str, Enum):
    MATCHED = "matched"
    NEW = "new"
    AMBIGUOUS = "ambiguous"


class ConceptResolution(BaseModel):
    canonical_concept_id: Optional[str] = None
    canonical_name: str
    status: ResolutionStatus
    similarity: Optional[float] = None
    matched_via: str  # "exact", "alias", "embedding_high", "llm_judge", "new_concept"
    candidates: List[Dict[str, Any]] = Field(default_factory=list)
    explanation: Optional[str] = None


class EntityResolution(BaseModel):
    canonical_entity_id: Optional[str] = None
    canonical_name: str
    entity_type: Optional[str] = None
    status: ResolutionStatus
    similarity: Optional[float] = None
    matched_via: str  # "exact", "alias", "embedding_high", "llm_judge", "new_entity"
    candidates: List[Dict[str, Any]] = Field(default_factory=list)
    explanation: Optional[str] = None


# -------------------------
# Scope
# -------------------------

class Scope(BaseModel):
    geography: Optional[str] = None

    # company / subsidiary / group / industry / economy etc.
    level: Optional[str] = None

    # consolidated / standalone / unknown
    consolidation: Optional[str] = "unknown"


# -------------------------
# Evidence
# -------------------------

class Evidence(BaseModel):
    document_id: str
    page_number: int

    section: Optional[str] = None
    chunk_id: Optional[str] = None

    # Short supporting excerpt
    quote: str

    # Optional character offsets / bounding box
    locator: Optional[Dict[str, Any]] = None


# -------------------------
# Observation (Atomic Extracted Claim)
# -------------------------

class Observation(BaseModel):
    id: str

    entity: Entity
    concept: Concept

    value: FactValue
    time: TimeContext
    scope: Scope = Field(default_factory=Scope)

    assertion_status: AssertionStatus = AssertionStatus.UNKNOWN

    evidence: List[Evidence] = Field(default_factory=list)

    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    needs_review: bool = False
    review_reason: Optional[str] = None


# -------------------------
# Relationship & Comparability
# -------------------------

class ComparabilitySignature(BaseModel):
    entity_match: str         # "match", "mismatch"
    concept_match: str        # "match", "related_sub_metric", "mismatch"
    period_match: str         # "match", "nested", "different_aggregation", "different_period"
    scope_match: str          # "match", "different_consolidation", "different_geography"
    unit_match: str           # "match", "normalized_match", "incompatible"
    status_relation: str      # "identical", "actual_vs_estimate", "actual_vs_forecast", "different"


class NumericComparison(BaseModel):
    value_a: Optional[float] = None
    value_b: Optional[float] = None
    difference: Optional[float] = None
    relative_difference: Optional[float] = None


class Relationship(BaseModel):
    id: str

    observation_a: str
    observation_b: str

    relationship_type: RelationshipType

    confidence: float = Field(ge=0.0, le=1.0)

    # Human-readable explanation
    explanation: str

    # Machine-readable reasons
    reasons: List[str] = Field(default_factory=list)

    # Structured reconciliation properties
    comparability: Optional[ComparabilitySignature] = None
    numeric: Optional[NumericComparison] = None


# -------------------------
# Extraction Candidates (LLM Extractor output)
# -------------------------

class ExtractedObservation(BaseModel):
    entity_name: str
    entity_type: Optional[str] = None

    concept_name: str
    source_label: Optional[str] = None

    value_type: ValueType
    value: Union[float, str]
    unit: Optional[str] = None

    period_label: Optional[str] = None
    period_type: Optional[PeriodType] = None

    scope: Scope = Field(default_factory=Scope)
    assertion_status: AssertionStatus = AssertionStatus.UNKNOWN

    evidence_quote: str
    page_number: int
    section: Optional[str] = None
    chunk_id: Optional[str] = None

    confidence: float = 0.95

    @field_validator("period_type", mode="before")
    @classmethod
    def coerce_period_type(cls, v: Any) -> Optional[PeriodType]:
        if v is None:
            return PeriodType.CUSTOM
        if isinstance(v, PeriodType):
            return v
        s = str(v).lower().strip().replace("-", "_").replace(" ", "_")
        MAPPING = {
            "instant": PeriodType.INSTANT,
            "point_in_time": PeriodType.INSTANT,
            "as_of": PeriodType.INSTANT,
            "month": PeriodType.MONTH,
            "monthly": PeriodType.MONTH,
            "quarter": PeriodType.QUARTER,
            "quarterly": PeriodType.QUARTER,
            "half_year": PeriodType.HALF_YEAR,
            "half_yearly": PeriodType.HALF_YEAR,
            "semi_annual": PeriodType.HALF_YEAR,
            "fiscal_year": PeriodType.FISCAL_YEAR,
            "annual": PeriodType.FISCAL_YEAR,
            "yearly": PeriodType.FISCAL_YEAR,
            "year": PeriodType.FISCAL_YEAR,
            "custom": PeriodType.CUSTOM,
            "unknown": PeriodType.UNKNOWN,
        }
        return MAPPING.get(s, PeriodType.CUSTOM)

    @field_validator("value_type", mode="before")
    @classmethod
    def coerce_value_type(cls, v: Any) -> ValueType:
        if isinstance(v, ValueType):
            return v
        s = str(v).lower().strip()
        if s in ("number", "numeric", "float", "int", "integer", "amount", "ratio"):
            return ValueType.NUMBER
        if s in ("percentage", "percent", "%", "pct"):
            return ValueType.PERCENTAGE
        if s in ("currency", "money", "monetary", "inr", "usd"):
            return ValueType.CURRENCY
        if s in ("boolean", "bool"):
            return ValueType.BOOLEAN
        return ValueType.TEXT

    @field_validator("assertion_status", mode="before")
    @classmethod
    def coerce_assertion_status(cls, v: Any) -> AssertionStatus:
        if isinstance(v, AssertionStatus):
            return v
        s = str(v).lower().strip()
        MAPPING = {
            "actual": AssertionStatus.ACTUAL,
            "estimate": AssertionStatus.ESTIMATE,
            "estimated": AssertionStatus.ESTIMATE,
            "forecast": AssertionStatus.FORECAST,
            "forecasted": AssertionStatus.FORECAST,
            "projected": AssertionStatus.PROJECTED,
            "projection": AssertionStatus.PROJECTED,
            "target": AssertionStatus.TARGET,
            "targeted": AssertionStatus.TARGET,
            "restated": AssertionStatus.RESTATED,
            "unknown": AssertionStatus.UNKNOWN,
        }
        return MAPPING.get(s, AssertionStatus.UNKNOWN)


# -------------------------
# Structured LLM Fallback Result
# -------------------------

class LLMRelationshipResult(BaseModel):
    relationship_type: RelationshipType
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: List[str] = Field(default_factory=list)
    explanation: str
