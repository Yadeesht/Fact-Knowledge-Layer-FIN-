# Financial Fact Knowledge Layer & Rules-First Reconciliation Engine

An evidence-grounded knowledge layer for financial and macroeconomic filings that standardizes claims into atomic **Observations** and resolves pairwise **Relationships** (`corroborated`, `contradicted`, `apparent_contradiction`, `contextualized`, `unresolved`) using a **strict hierarchical rules-first cascade**.

---

## 1. Observation Attribute Specifications & Determination Nature

The table below details every attribute an Observation can possess, whether its value is **Content-Extracted** (derived from unstructured text by the extractor) or **Deterministic** (coerced, normalized, or computed via deterministic domain logic), and the exact constraints used to eliminate ambiguity.

| Attribute | Type & Permitted Values | Determination Mode | Nature (Content vs. Deterministic) | Ambiguity Elimination & Constraint Rules |
| :--- | :--- | :--- | :--- | :--- |
| **`entity`** | `Entity` (`canonical_name: str`, `entity_type: str`, `aliases: List[str]`) | **Deterministic Canonicalization** | Extracted from text context, then mapped via canonical entity dictionary (`ENTITY_ALIASES`). | Resolves aliases (`"RBI"` $\rightarrow$ `"Reserve Bank of India"`, `"GoI"` $\rightarrow$ `"Government of India"`, `"Delhivery Ltd"` $\rightarrow$ `"Delhivery Limited"`). Mismatched entities exit immediately (`return None`). |
| **`concept`** | `Concept` (`canonical_name: str`, `source_label: str`, `definition: str`) | **Deterministic Canonicalization** | Extracted from paragraph/table headers, then resolved via canonical concept dictionary (`CONCEPT_ALIASES`). | Normalizes synonyms (`"turnover"` $\rightarrow$ `"revenue"`, `"sales"` $\rightarrow$ `"revenue"`, `"gdp growth rate"` $\rightarrow$ `"real gdp growth"`). Anti-conflation guardrails prevent merging distinct concepts. |
| **`value.amount`** | `Optional[float]` | **Content-Extracted** | Primary numerical value parsed directly from the source text chunk or table cell. | `None` for purely qualitative claims. Strips commas, footnote symbols, and currency symbols before parsing. |
| **`value.unit`** | `Optional[str]` (e.g. `"%"` , `"INR crore"`, `"INR million"`, `"USD billion"`) | **Content-Extracted** | Extracted from table column headers, units notes (e.g. `(₹ in Cr)`), or surrounding sentence context. | If missing or ambiguous for a numeric metric, the observation is automatically quarantined (`needs_review=True`). |
| **`value.normalized_amount`** | `Optional[float]` | **Deterministic Normalization** | Computed deterministically by applying standard scale multipliers (`crore` $\times 10^7$, `lakh` $\times 10^5$, `million` $\times 10^6$, `billion` $\times 10^9$). | Percentages remain human-scaled (`6.4%` stays `6.4`, never converted to `0.064`). Currency amounts converted to base units (INR, USD). |
| **`value.normalized_unit`** | `Optional[str]` (e.g. `"INR"`, `"USD"`, `"%"`, `"units"`) | **Deterministic Normalization** | Derived by stripping multiplier prefixes (`"INR crore"` $\rightarrow$ `"INR"`, `"USD million"` $\rightarrow$ `"USD"`). | Enables scale-independent numerical equivalence checks without floating-point distortion. |
| **`time.period_type`** | `PeriodType` (`fiscal_year`, `calendar_year`, `quarter`, `half_year`, `month`, `custom`, `point_in_time`, `unknown`) | **Deterministic Normalization** | Classified from raw label using regular expressions and domain patterns. | Normalizes strings (`"FY24"`, `"FY 2023-24"`, `"2023-24"` $\rightarrow$ `PeriodType.FISCAL_YEAR`). Unrecognized strings coerce safely to `unknown`. |
| **`time.label`** | `Optional[str]` (e.g. `"FY24"`, `"FY25"`, `"Q1:2024-25"`, `"H1 FY24"`) | **Content-Extracted** | Explicit temporal token extracted from text, table headers, or footnote scopes. | Parsed into exact calendar boundary dates (`start_date`, `end_date`) using Indian fiscal calendar rules (April 1 – March 31). |
| **`scope.geography`** | `Optional[str]` (e.g. `"India"`, `"Global"`, `"Advanced Economies"`) | **Content-Extracted** | Identified from document section headers, chapter titles, or table subtitles. | Defaults to entity jurisdiction if unspecified. Conflicting geographies (e.g. Global vs India) trigger immediate candidate discard. |
| **`scope.consolidation`** | `Optional[str]` (`"consolidated"`, `"standalone"`, `"unknown"`) | **Content-Extracted** | Detected from financial statements (`"Consolidated Financial Results"` vs `"Standalone"`). | When different under the same entity and period, triggers contextual relationship rather than false contradiction. |
| **`assertion_status`** | `AssertionStatus` (`actual`, `estimate`, `forecast`, `projected`, `target`, `restated`, `unknown`) | **Content-Extracted** | Extracted from modal verbs ("projected to grow", "estimated at") or table footnotes ("PE", "RE", "BE"). | Prevents treating future projections or budget estimates against actual historical outturns as contradictions. |
| **`evidence.quote`** | `str` (verbatim excerpt) | **Content-Extracted & Grounded** | Exact character substring extracted from chunk text with page locator. | Validated against raw chunk text. If character substring is hallucinated or ungrounded, observation is flagged. |
| **`needs_review`** | `bool` (`True` / `False`) | **Deterministic Rule** | Computed automatically based on completeness: missing units, missing time labels, or ungrounded quotes. | **Quarantine Policy**: If `True`, the observation is barred from contaminating the candidate comparison pool. |

---

## 2. Pairwise Reconciliation Comparison Constraints & Disambiguation Matrix

When evaluating two candidate observations ($A$ and $B$) to assign a relationship label, the engine enforces the following **hard constraints** across all dimensions. Ambiguity is systematically ruled out: if a pair fails basic comparability, it is **discarded immediately (`return None`)** without creating spurious graph edges.

| Relationship Label | Entity Constraint | Concept Constraint | Temporal Constraint | Scope Constraint | Unit Constraint | Assertion Status Constraint | Value / Numeric Constraint | Ambiguity Elimination & Early Exit Rules |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`CORROBORATED`** | **Identical** (canonical match) | **Identical** (canonical or alias match) | **Identical** (same period label or matching calendar bounds) | **Identical** (geography & consolidation match) | **Normalized Match** (same base unit after scale conversion) | **Identical** (`actual` vs `actual`, or matching statuses) | Relative diff $\le 0.1\%$ (`approximately_equal`) | Ambiguity in units eliminated by normalization; minor decimal rounding differences ($\le 0.1\%$) accepted as equivalence. |
| **`CONTRADICTED`** | **Identical** (canonical match) | **Identical** (canonical or alias match) | **Identical** (same period label or matching calendar bounds) | **Identical** (geography & consolidation match) | **Normalized Match** (same base unit) | **Identical Actuals** (`actual` vs `actual`) | Relative diff $> 0.1\%$ (material discrepancy) | **No ambiguity permitted**: Discrepancy must be between two matching actual figures measuring the exact same concept in the exact same timeframe. |
| **`APPARENT_CONTRADICTION`** | **Identical** (canonical match) | **Strict Component-Aggregate Sibling** (Sector GVA vs GDP, Segment vs Total Revenue, Food vs Headline CPI) | **Identical** (same fiscal period) | **Identical** (same geography & consolidation) | **Normalized Match** (same base unit, e.g. both `%` or both `INR`) | Any status | Differing numerical figures | **Generic word overlap (e.g. sharing "growth" or "volume") is strictly rejected**. Only verified sub-component/aggregate pairs qualify; non-matching periods/scopes return `None`. |
| **`CONTEXTUALIZED`** | **Identical** (canonical match) | **Identical** (canonical or alias match) | **Nested** (e.g. `H1 FY24` within `FY24`) OR **Same** | **Differing Consolidation** (`consolidated` vs `standalone`) under same geography | **Normalized Match** (same base unit) | **Differing Statuses** (`actual` vs `estimate`, `forecast`, or `target`) | Differing numerical figures | Explains known structural divergence (interim progress, accounting consolidation boundary, or projection vs actual outturn) without contradiction. |
| **`UNRESOLVED`** | **Identical** (canonical match) | **Identical** (canonical match) | **Identical** (same period) | **Identical** (same scope) | **Incompatible Currencies** (e.g. `INR` vs `USD`, `EUR` vs `GBP`) | Any status | Any numerical figures | **Currency conversion without explicit FX rate is strictly forbidden**. The engine flags this as ambiguous and awaits an explicit document exchange rate. |
| **`DISCARDED (NO RELATIONSHIP)`** *(Return `None`)* | **Mismatch** OR **Quarantined** (`needs_review`) | **Concept Mismatch** (arbitrary or unrelated concepts) | **Different Periods** (distinct fiscal years, e.g. `FY24` vs `FY25`, or unaligned aggregations) | **Conflicting Geographies** (e.g. `Global` vs `India`) | **Incompatible Units** (e.g. `%` vs `INR`, headcount vs currency) | Any status | Any numerical figures | **Early Exit**: Any pair failing basic comparability is immediately dropped (`return None`). **The engine never creates noisy `NOT_COMPARABLE` edges in the graph.** |

---

## 3. Strict Hierarchical Reconciliation Funnel

```
               Candidate Pair (Observation A, Observation B)
                                    │
                                    ▼
                         0. Quarantined Check? ────── True ────► DISCARD (None)
                                    │ Clean
                         1. Entity Match? ─────────── No ──────► DISCARD (None)
                                    │ Match
                         2. Unit / Dimension Match? ─ Incompat ─► DISCARD (None)
                                    │ Compatible
                         3. Geography Match? ──────── Differ ──► DISCARD (None)
                                    │ Match
                         4. Time Compatibility? ───── Differ ──► DISCARD (None)
                                    │ Same / Nested
                                    ▼
                         5. Concept Relationship?
                                    │
            ┌───────────────────────┴───────────────────────┐
            ▼                                               ▼
      Direct Match                               Strict Sibling Concept
            │                                 (Services GVA vs Real GDP)
            │                                               │
            ├─ Nested Period ──► CONTEXTUALIZED             ├─ Same Period, Scope, Unit ──► APPARENT_CONTRADICTION
            ├─ Differing Scope ─► CONTEXTUALIZED            └─ Different Period/Scope ────► DISCARD (None)
            ├─ Differing Currencies (No FX) ─► UNRESOLVED
            ├─ Differing Status (Actual vs Est) ─► CONTEXTUALIZED
            │
            ▼ (Matching Actuals)
    Numeric Difference?
            │
            ├─ Equal within 0.1% tolerance ──► CORROBORATED
            └─ Material Difference (> 0.1%) ─► CONTRADICTED
```

---

## 4. Core Data Model & Pydantic Schemas

- **`Observation`**:
  - `id`: unique observation ID
  - `document_id`: foreign key to parent document
  - `session_id`: foreign key to analysis session
  - `entity`: `Entity(canonical_name, entity_type, aliases)`
  - `concept`: `Concept(canonical_name, source_label, definition)`
  - `value`: `FactValue(type, amount, unit, normalized_amount, normalized_unit, text)`
  - `time`: `TimeContext(period_type, start_date, end_date, label)`
  - `scope`: `Scope(geography, level, consolidation)`
  - `assertion_status`: `AssertionStatus(actual, estimate, forecast, projected, target, restated, unknown)`
  - `evidence`: `List[Evidence(document_id, page_number, section, quote, locator)]`
  - `confidence`: float between 0.0 and 1.0
  - `needs_review`: boolean flag for human review escalation
- **`Relationship`**:
  - `id`: `rel_<obsA>_<obsB>`
  - `session_id`: foreign key to analysis session
  - `observation_a`, `observation_b`: observation IDs
  - `relationship_type`: `RelationshipType(corroborated, contradicted, apparent_contradiction, contextualized, unresolved)`
  - `confidence`: float between 0.0 and 1.0
  - `explanation`: human-readable explanation
  - `reasons`: list of machine-readable tags (e.g. `["equivalent_after_normalization", "same_period"]`)
  - `comparability`: `ComparabilitySignature(entity_match, concept_match, period_match, scope_match, unit_match, status_relation)`
  - `numeric`: `NumericComparison(value_a, value_b, difference, relative_difference)`

---

## 5. Four Showcase Cases (Assignment Walkthrough)

| Case | Status | Observation A | Observation B | Engine Resolution |
| :--- | :--- | :--- | :--- | :--- |
| **Case 1** | `CORROBORATED` | **Delhivery FY24 Revenue**: ₹8,142.16 Cr *(Annual Report FY24, p. 110)* | **Delhivery FY24 Revenue**: ₹81,424 Mn *(Earnings Presentation, p. 7)* | Normalization scales crore and million into base INR; relative difference < 0.03% matches within 0.1% materiality. |
| **Case 2** | `CONTRADICTED` | **India FY25 Real GDP Growth**: 6.4% Actual *(Economic Survey, p. 48)* | **India FY25 Real GDP Growth**: 7.2% Actual *(RBI Table 1.1, p. 31)* | Identical entity, concept, period, scope, and actual status, but 12.5% material divergence $\rightarrow$ `CONTRADICTED`. |
| **Case 3** | `CONTEXTUALIZED` | **India FY25 Real GDP Growth**: 6.4% Estimate *(Economic Survey, p. 49)* | **India FY25 Real GDP Growth**: 6.5% Forecast *(IMF Article IV, p. 14)* | Differing assertion status (`estimate` vs `forecast`) $\rightarrow$ contextual reconciliation rather than false conflict. |
| **Case 4** | `NEEDS_REVIEW` | **Delhivery PIN codes**: 18,600+ *(Prospectus, p. 28)* | *N/A (Single Observation Anomaly)* | Missing unit and ambiguous timeframe; engine safely escalates to human review queue. |

---

## 6. Project Directory Structure

```
starter-datasets/
├── backend/
│   ├── app.py                     # FastAPI web application & static file server
│   ├── seed_data.py               # Pre-extracted verified observations & runs
│   ├── models/
│   │   └── schema.py              # Pydantic schemas & Enums
│   ├── core/
│   │   ├── normalizer.py          # Multipliers (crore, lakh, million, etc.) & currency isolation
│   │   ├── canonicalizer.py       # Entity & concept canonical resolution
│   │   ├── temporal.py            # Period normalization & nesting comparison
│   │   ├── materiality.py         # 0.1% financial tolerance check
│   │   └── scope.py               # Consolidation and geography comparison
│   ├── reconciliation/
│   │   ├── candidate_matcher.py   # Strict hierarchical eligibility gating
│   │   ├── cascade.py             # Deterministic rules cascade
│   │   └── llm_judge.py           # Structured fallback LLM judge
│   ├── ingestion/
│   │   ├── pdf_parser.py          # PDF reader & chunker with page numbers
│   │   ├── batcher.py             # Hierarchical LLM chunk batcher
│   │   ├── extractor.py           # Candidate extraction pipeline
│   │   └── pipeline.py            # SHA-256 caching & scoped reconciliation pipeline
│   ├── db/
│   │   ├── database.py            # SQLite schema initialization & migrations
│   │   └── repository.py          # CRUD operations for observations, sessions & relationships
│   └── tests/
│       ├── test_reconciliation.py # Automated test suite for normalization & cascade
│       └── test_sessions.py       # Automated test suite for caching & sessions
├── frontend/
│   ├── index.html                 # Interactive Single Page Dashboard
│   ├── style.css                  # Dark glassmorphism styling
│   └── app.js                     # Interactive controller with 4 showcase cases
├── delhivery/                     # Curated Delhivery filings (Prospectus, AR, Presentation)
├── india-macroeconomy/            # Curated Macro filings (Economic Survey, RBI, IMF)
└── requirements.txt               # Dependencies (FastAPI, uvicorn, pydantic, pymupdf, pytest)
```

---

## 7. How to Run

### Run Automated Unit Tests:
```powershell
.venv\Scripts\pytest backend/tests/test_sessions.py backend/tests/test_reconciliation.py
```

### Start the Application (FastAPI & Dashboard):
```powershell
uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
```

Once started, open **`http://127.0.0.1:8000`** in any browser to explore the interactive showcase cases, knowledge graph, observation browser, and pipeline observability.
