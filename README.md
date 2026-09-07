# Financial Fact Knowledge Layer & Rules-First Reconciliation Engine

An evidence-grounded knowledge layer for financial and macroeconomic filings that standardizes claims into atomic **Observations** and resolves pairwise **Relationships** (`corroborated`, `contradicted`, `contextualized`, `unresolved`) using a **deterministic rules-first cascade** with a structured LLM fallback.

---

## 1. Core Engineering Principle: Rules-First Deterministic Cascade

Rather than relying on non-deterministic LLM prompting to decide whether financial figures conflict, this system employs a **progressively stronger deterministic cascade**:

```
                 Candidate Pair (Observation A, Observation B)
                                      │
                                      ▼
                           1. Entity Compatibility? ──── No ───► None
                                      │ Yes
                           2. Concept Compatibility? ─── No ───► None
                                      │ Yes
                           3. Scope Compatibility? ──── Differ ─► CONTEXTUALIZED (0.96)
                                      │ Compatible
                           4. Time / Period Check? ──── Differ ─► CONTEXTUALIZED (0.97)
                                      │ Match / Nested
                           5. Unit & Currency Check? ── Differ ─► UNRESOLVED (No FX)
                                      │ Normalizable
                           6. Value Equivalence? ────── Equal ──► CORROBORATED (0.99)
                                      │ Differ (> 0.1%)
                           7. Assertion Status Check? ─ Differ ─► CONTEXTUALIZED (0.90)
                                      │ Match (Actual vs Actual)
                                      ▼
                                CONTRADICTED (0.93)
                                      │
                         If None / Qualitative Semantic
                                      │
                                      ▼
                             Structured LLM Judge
```

### Key Engineering Decisions & Trade-offs:
1. **`Observation` vs "Fact"**: An extracted claim from a document is an *Observation* grounded in an exact evidence quote and page locator. A *Fact* is a consensus emerging from reconciled observations.
2. **Materiality Tolerance**: Financial reports round figures (e.g. ₹8,142.16 Crore vs ₹81,424 Million). We enforce a **0.1% relative tolerance** threshold (`approximately_equal`) to prevent false contradictions on standard rounding.
3. **Currency Restraint**: Cross-currency comparisons (e.g. INR vs USD) are explicitly marked `UNRESOLVED` unless an explicit exchange rate is provided in the filings.
4. **Assertion Status Sensitivity**: An estimate or forecast (e.g. Economic Survey estimate of 6.4%) and an IMF projection (6.5%) are classified as `CONTEXTUALIZED`, not contradictions.

---

## 2. Core Data Model & Pydantic Schemas

- **`Observation`**:
  - `id`: unique observation ID
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
  - `observation_a`, `observation_b`: observation IDs
  - `relationship_type`: `RelationshipType(corroborated, contradicted, contextualized, unresolved)`
  - `confidence`: float between 0.0 and 1.0
  - `explanation`: human-readable explanation
  - `reasons`: list of machine-readable tags (e.g. `["equivalent_after_normalization", "same_period"]`)

---

## 3. Four Showcase Cases (Assignment Walkthrough)

| Case | Status | Observation A | Observation B | Engine Resolution |
| :--- | :--- | :--- | :--- | :--- |
| **Case 1** | `CORROBORATED` | **Delhivery FY24 Revenue**: ₹8,142.16 Cr *(Annual Report FY24, p. 110)* | **Delhivery FY24 Revenue**: ₹81,424 Mn *(Earnings Presentation, p. 7)* | Normalization scales crore and million into base INR; relative difference < 0.03% matches within 0.1% materiality. |
| **Case 2** | `CONTRADICTED` | **India FY25 Real GDP Growth**: 6.4% Actual *(Economic Survey, p. 48)* | **India FY25 Real GDP Growth**: 7.2% Actual *(RBI Table 1.1, p. 31)* | Identical entity, concept, period, scope, and actual status, but 12.5% material divergence $\rightarrow$ `CONTRADICTED`. |
| **Case 3** | `CONTEXTUALIZED` | **India FY25 Real GDP Growth**: 6.4% Estimate *(Economic Survey, p. 49)* | **India FY25 Real GDP Growth**: 6.5% Forecast *(IMF Article IV, p. 14)* | Differing assertion status (`estimate` vs `forecast`) $\rightarrow$ contextual reconciliation rather than false conflict. |
| **Case 4** | `NEEDS_REVIEW` | **Delhivery PIN codes**: 18,600+ *(Prospectus, p. 28)* | *N/A (Single Observation Anomaly)* | Missing unit and ambiguous timeframe; engine safely escalates to human review queue. |

---

## 4. Project Directory Structure

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
│   │   ├── cascade.py             # Deterministic rules cascade
│   │   └── llm_judge.py           # Structured fallback LLM judge
│   ├── ingestion/
│   │   ├── pdf_parser.py          # PDF reader & chunker with page numbers
│   │   └── extractor.py           # Candidate extraction pipeline
│   ├── db/
│   │   ├── database.py            # SQLite schema initialization
│   │   └── repository.py          # CRUD operations for observations & relationships
│   └── tests/
│       └── test_reconciliation.py # Automated test suite for normalization & cascade
├── frontend/
│   ├── index.html                 # Interactive Single Page Dashboard
│   ├── style.css                  # Dark glassmorphism styling
│   └── app.js                     # Interactive controller with 4 showcase cases
├── delhivery/                     # Curated Delhivery filings (Prospectus, AR, Presentation)
├── india-macroeconomy/            # Curated Macro filings (Economic Survey, RBI, IMF)
└── requirements.txt               # Dependencies (FastAPI, uvicorn, pydantic, pypdf, pytest)
```

---

## 5. How to Run

### Run Automated Unit Tests:
```powershell
python -m backend.tests.test_reconciliation
```

### Start the Application (FastAPI & Dashboard):
```powershell
uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
```

Once started, open **`http://127.0.0.1:8000`** in any browser to explore the interactive showcase cases, knowledge graph, observation browser, and pipeline observability.
