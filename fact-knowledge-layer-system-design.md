# Fact Knowledge Layer — System Design

## 1. Design philosophy

Three decisions drive everything else in this system:

1. **Observation ≠ Fact.** A document never produces a "fact" directly. It produces an *observation* — a single source-grounded claim tied to one piece of evidence. A "fact" (as shown in the UI) is just a group of observations that refer to the same entity and concept. Relationships (corroborated / contradicted / contextualized / unresolved) are computed *between* observations, never asserted at extraction time.
2. **Rules before LLM.** Every comparison between two observations first passes through a deterministic cascade (entity match, concept match, scope match, time match, normalized value match). The LLM is only invoked for the residue the rules cannot resolve, and even then it receives structured observations, not raw text.
3. **Small and legible beats large and impressive.** No graph database, no job queue, no five-dimensional confidence scores, no probabilistic entity-resolution model. SQLite, a single confidence score, and alias tables that grow on demand are enough for a defensible v1. Everything beyond that is a named item in Limitations and Next Steps, not a half-built corner of the system.

## 2. Components

**Upload & query API (FastAPI)**
Two responsibilities: accept a PDF and hand it to the document pipeline; serve read queries (list observations, list relationships, fetch evidence, search by entity/concept) back to the UI. Ingestion is synchronous for v1 — the request blocks until the document is processed. This is a stated trade-off, not an oversight (see Limitations).

**Document parser**
Converts a PDF into layout-aware chunks using a library such as `pdfplumber` or `PyMuPDF`. Each chunk retains `document_id`, `page_number`, `section` (if detectable from headers), `chunk_type` (paragraph vs table), and the raw text. Tables are extracted as structured rows where possible, not flattened into prose — this matters because a table like "Revenue / 2023 / 12,400 / 2024 / 15,800" loses its row-column relationship if flattened to plain text.

**Fact extractor**
An LLM call per chunk (or per small batch of chunks) that proposes candidate observations. The extractor's output is a *different, looser* schema (`ExtractedObservation`) than the final stored `Observation` — the LLM proposes an interpretation, the application decides whether to trust it. The extractor is told to skip a chunk rather than force a fact if no clear entity/metric/value triple is present.

**Normalizer**
Three independent, mostly deterministic sub-steps that run in code, not via the LLM:
- **Unit normalization** — currency multipliers (crore, lakh, million, billion) converted to a single base unit, using a lookup table, never LLM arithmetic.
- **Time normalization** — period labels ("FY25", "H1 FY24", "nine months ended Dec 31, 2021") converted into `period_type` + `start_date`/`end_date` where derivable, with the original label preserved for display.
- **Entity/concept resolution** — canonicalization through a small alias table (`"RBI" → "Reserve Bank of India"`), not a probabilistic matching model. New entities/concepts are created on demand when nothing matches.

**Relationship engine**
The core reconciliation logic. Runs a deterministic cascade first (below); falls back to a structured LLM comparison only for the pairs the cascade cannot decide, and only for pairs that already passed an entity + concept compatibility pre-filter (see §5).

**SQLite storage**
Seven tables: `documents`, `chunks`, `entities`, `concepts`, `observations`, `evidence`, `relationships`, plus `processing_runs` for lightweight run status. No graph database — relationships are rows referencing two observation IDs, which is sufficient because the UI never needs multi-hop traversal, only "what does this observation relate to."

**Evidence explorer (UI)**
A flat, expandable list, not a graph visualization: canonical facts (grouped observations) with a status badge (corroborated / contradicted / contextualized / unresolved / needs review); expanding a row shows every underlying observation, its source quote, page number, and — for relationships — the specific field that was compared (period, scope, unit, status) and the explanation.

## 3. Data model (Pydantic)

```python
from datetime import date
from enum import Enum
from typing import Optional, Union
from pydantic import BaseModel, Field


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


class FactValue(BaseModel):
    type: ValueType
    amount: Optional[float] = None
    unit: Optional[str] = None
    normalized_amount: Optional[float] = None
    normalized_unit: Optional[str] = None
    text: Optional[str] = None


class TimeContext(BaseModel):
    period_type: PeriodType
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    label: Optional[str] = None  # original expression, e.g. "H1 FY25"


class Entity(BaseModel):
    canonical_name: str
    entity_type: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)


class Concept(BaseModel):
    canonical_name: str
    source_label: Optional[str] = None
    definition: Optional[str] = None


class Scope(BaseModel):
    geography: Optional[str] = None
    level: Optional[str] = None            # company / subsidiary / economy / etc.
    consolidation: Optional[str] = None    # consolidated / standalone / unknown


class Evidence(BaseModel):
    document_id: str
    page_number: int
    section: Optional[str] = None
    chunk_id: Optional[str] = None
    quote: str
    locator: Optional[dict] = None


class Observation(BaseModel):
    id: str
    entity: Entity
    concept: Concept
    value: FactValue
    time: TimeContext
    scope: Scope = Field(default_factory=Scope)
    assertion_status: AssertionStatus = AssertionStatus.UNKNOWN
    evidence: list[Evidence]
    confidence: float = Field(ge=0.0, le=1.0)
    needs_review: bool = False


class Relationship(BaseModel):
    id: str
    observation_a: str
    observation_b: str
    relationship_type: RelationshipType
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str
    reasons: list[str] = Field(default_factory=list)


class ExtractedObservation(BaseModel):
    """What the LLM returns — looser than Observation, validated before promotion."""
    entity_name: str
    entity_type: Optional[str]
    concept_name: str
    source_label: Optional[str]
    value_type: ValueType
    value: Union[float, str]
    unit: Optional[str]
    period_label: Optional[str]
    period_type: Optional[PeriodType]
    scope: Scope
    assertion_status: AssertionStatus
    evidence_quote: str
    page_number: int
    confidence: float
```

## 4. SQLite schema

```sql
CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    document_type TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    page_number INTEGER NOT NULL,
    section TEXT,
    chunk_type TEXT,
    text TEXT NOT NULL
);

CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    entity_type TEXT,
    aliases_json TEXT
);

CREATE TABLE concepts (
    id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    description TEXT,
    aliases_json TEXT
);

CREATE TABLE observations (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(id),
    concept_id TEXT NOT NULL REFERENCES concepts(id),
    value_type TEXT NOT NULL,
    value REAL,
    unit TEXT,
    normalized_value REAL,
    normalized_unit TEXT,
    period_type TEXT,
    period_start TEXT,
    period_end TEXT,
    period_label TEXT,
    geography TEXT,
    scope_level TEXT,
    consolidation TEXT,
    assertion_status TEXT,
    confidence REAL,
    needs_review INTEGER DEFAULT 0
);

CREATE TABLE evidence (
    id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES observations(id),
    document_id TEXT NOT NULL REFERENCES documents(id),
    chunk_id TEXT REFERENCES chunks(id),
    page_number INTEGER,
    quote TEXT NOT NULL
);

CREATE TABLE relationships (
    id TEXT PRIMARY KEY,
    observation_a TEXT NOT NULL REFERENCES observations(id),
    observation_b TEXT NOT NULL REFERENCES observations(id),
    relationship_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    explanation TEXT,
    reasons_json TEXT
);

CREATE TABLE processing_runs (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    status TEXT NOT NULL,
    extractor_model TEXT,
    started_at TEXT,
    completed_at TEXT,
    error TEXT
);
```

## 5. Processing pipeline (ingestion)

1. `POST /documents` receives a PDF; a `documents` row and a `processing_runs` row (`status = "started"`) are created.
2. Document parser produces chunks; each is written to `chunks`.
3. Fact extractor runs per chunk, returning zero or more `ExtractedObservation` candidates.
4. Each candidate is Pydantic-validated. Anything that fails validation, or comes back with a null `concept_name`/`value`/`period_label`, is marked `needs_review = True` and stored without a relationship being attempted against it — this is the designed failure path, not an exception handler.
5. Normalizer resolves entity and concept aliases (creating new `entities`/`concepts` rows on first encounter), normalizes units/currency deterministically, and normalizes time labels into `period_start`/`period_end` where derivable.
6. The resulting `Observation` is stored with its `Evidence`.
7. `processing_runs.status` is updated to `completed` (or `failed`, with `error` populated) once the document finishes.

## 6. Candidate pairing (before reconciliation)

Comparing every observation against every other observation is O(n²) and mostly wasted work. Before any reconciliation runs, candidate pairs are generated by bucketing:

- Group observations by `(entity_id, concept_id)`.
- Only observations inside the same bucket are ever compared.
- A pair is only escalated to the LLM fallback (step 7 below) if it passes this bucketing *and* a cheap concept-compatibility check — exact concept match or alias match. Loosely related concepts (e.g. "revenue" vs "revenue from traded goods") are never sent to the LLM to adjudicate; they're treated as distinct concepts unless an alias explicitly says otherwise.

## 7. Deterministic reconciliation cascade

Applied to every candidate pair, in order. The first step that reaches a conclusion returns; if nothing resolves, the pair is escalated to the LLM.

1. **Entity mismatch** → not comparable, discard the pair.
2. **Concept mismatch** → not comparable, discard the pair.
3. **Scope check** — if scope (consolidation level, geography, entity level) differs → `CONTEXTUALIZED`, reason `different_scope`, confidence ~0.96.
4. **Time check** — if periods differ (by date range or by label) → `CONTEXTUALIZED`, reason `different_period`, confidence ~0.97.
5. **Numeric comparison** (only if both observations are numeric):
   - Normalize both values to a common unit. If normalization isn't possible (e.g. mismatched currencies with no trusted FX source) → `UNRESOLVED`, reason `no_trusted_conversion`. Do not guess an exchange rate.
   - If normalized values are equal within a materiality threshold → `CORROBORATED`, reason `equivalent_after_normalization`, confidence ~0.99.
   - If values differ but `assertion_status` differs (e.g. estimate vs actual) → `CONTEXTUALIZED`, reason `different_assertion_status`, confidence ~0.90.
   - Otherwise → `CONTRADICTED`, reason `material_value_difference`, confidence ~0.93.
6. **Non-numeric (semantic) facts** — never resolved deterministically; always escalate to the LLM.

Materiality threshold: absolute difference under a tiny epsilon, or relative difference ≤ 0.1%, counts as equal. This prevents floating-point or rounding noise from being reported as a contradiction.

## 8. LLM fallback (only for what rules cannot resolve)

- Only invoked for pairs that passed the candidate-pairing pre-filter and were not resolved by the deterministic cascade.
- Input is the two structured `Observation` objects (entity, concept, value, period, status, scope) — never raw PDF text.
- Output is constrained to a `LLMRelationshipResult` schema: `relationship_type`, `confidence`, `reasons`, `explanation`. The prompt explicitly instructs the model not to infer facts not present in the observations.
- If the LLM's output fails schema validation, the pair is stored as `UNRESOLVED` rather than retried indefinitely.

## 9. Normalization rules in detail

- **Units** — a lookup table (`crore → 10,000,000`, `lakh → 100,000`, `million → 1,000,000`, `billion → 1,000,000,000`) applied in code. Never delegated to the LLM.
- **Currency** — tracked as a separate field from magnitude. Cross-currency comparison is refused by default (see §7 step 5) unless an explicit FX source is added later.
- **Percentages** — stored as the displayed percentage value (e.g. `10.0` with `unit = "%"`), not as a proportion, to avoid comparison-logic ambiguity.
- **Time** — every period label is preserved verbatim (`label` field) alongside any dates that could be derived from it. Comparison prefers dates when both are present; falls back to exact label match otherwise.
- **Entities/concepts** — resolved through small, explicit alias dictionaries that grow as new aliases are encountered, not a trained matching model. New entities/concepts are created rather than forced into an existing bucket when no alias matches.

## 10. Failure handling (`needs_review`)

`needs_review` is set to `True`, and no relationship is generated for that observation, when any of the following hold at extraction/normalization time:
- A required field (`concept_name`, `value`, or `period_label`) came back null or empty from the extractor.
- Confidence returned by the extractor is below a fixed threshold.
- The value or unit could not be normalized (e.g. an unrecognized unit string).

This is a designed feature, not a caught exception — it's what makes the required "extraction or reasoning failure" demo case something the system produces naturally rather than something staged.

## 11. API surface

- `POST /documents` — upload a PDF, triggers synchronous ingestion.
- `GET /documents` — list uploaded documents and their processing status.
- `GET /documents/{id}` — document detail + chunk count + observation count.
- `GET /observations` — filterable by entity, concept, `needs_review`.
- `GET /observations/{id}` — single observation with its evidence.
- `GET /relationships` — filterable by `relationship_type`.
- `GET /relationships/{id}` — single relationship with both observations and their evidence.
- `GET /evidence/{id}` — full quote + document/page/section locator.
- `GET /search?q=...` — free-text search over entities/concepts (for the UI's fact list).

## 12. Tech stack

- **Backend**: Python, FastAPI.
- **PDF parsing**: `pdfplumber` (or `PyMuPDF`) for layout-aware text and table extraction.
- **Extraction/comparison LLM**: GPT-4.1 (or equivalent), called with structured-output prompts and Pydantic validation on the response.
- **Storage**: SQLite (single file, zero setup).
- **Frontend**: a minimal React (or plain HTML/JS) three-panel view — documents list, fact list with status badges, selected-fact detail with evidence and reasoning.

## 13. What this system deliberately does not do (v1)

- No graph database — relationships are rows referencing two observation IDs; the UI never needs multi-hop traversal.
- No asynchronous job queue — ingestion is a single blocking request per document.
- No probabilistic entity-resolution model — alias tables only, expanded on demand.
- No multi-dimensional confidence scoring — one confidence score per observation and per relationship.
- No automatic currency conversion — cross-currency pairs are marked `UNRESOLVED` rather than guessed.
- No pre-built financial concept ontology — concepts are created dynamically as they're encountered in documents.

## 14. Limitations and next steps (for the README)

- Ingestion is synchronous; large PDFs or many concurrent uploads would need a job queue (Celery/RQ) and async status polling.
- Entity/concept alias tables are hand-seeded and grow reactively; a fuzzy/embedding-based resolver would help once documents with heavier entity overlap are introduced.
- Materiality thresholds are a single global constant; in practice they should vary by concept (e.g. GDP growth tolerates 0.1 percentage point, revenue tolerates 0.5%).
- No FX conversion; cross-currency facts are currently always `UNRESOLVED`.
- Confidence is a single score per observation/relationship; a multi-dimensional breakdown (extraction, entity-match, temporal, relationship confidence) would let the UI surface *which* part of the pipeline was uncertain.
- Adding a new document currently re-runs candidate generation against all existing observations in the same entity/concept buckets — sufficient at this scale, but a production version would maintain an incremental index instead of recomputing bucket membership per upload.
