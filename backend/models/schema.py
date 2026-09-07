from datetime import date
from enum import Enum
from typing import Optional, Union, List, Dict, Any
from pydantic import BaseModel, Field


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
# Relationship
# -------------------------

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


# -------------------------
# Structured LLM Fallback Result
# -------------------------

class LLMRelationshipResult(BaseModel):
    relationship_type: RelationshipType
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: List[str] = Field(default_factory=list)
    explanation: str
