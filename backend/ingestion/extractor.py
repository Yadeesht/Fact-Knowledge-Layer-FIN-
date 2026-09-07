import re
import uuid
import json
from typing import List, Optional, Dict, Any
from backend.models.schema import (
    ExtractedObservation,
    Observation,
    Entity,
    Concept,
    FactValue,
    TimeContext,
    Scope,
    Evidence,
    ValueType,
    PeriodType,
    AssertionStatus,
)
from backend.core.normalizer import normalize_fact_value
from backend.core.canonicalizer import canonicalize_entity_name, canonicalize_concept_name
from backend.core.temporal import normalize_fiscal_year
from backend.core.llm_client import call_llm


EXTRACTION_SYSTEM_PROMPT = """You are an expert financial and macroeconomic analyst.
Your task is to extract atomic, evidence-grounded claims (Observations) from financial and macroeconomic excerpts.

For every extracted claim, you MUST provide:
- entity_name: Canonical or formal reporting entity (e.g. 'Delhivery Limited', 'India', 'Reserve Bank of India')
- entity_type: 'company', 'country', 'central_bank', or 'agency'
- concept_name: Normalized metric name (e.g. 'Revenue from operations', 'Real GDP growth', 'Headline CPI inflation')
- source_label: Original verbatim label used in the text
- value_type: 'number', 'percentage', 'currency', or 'text'
- value: Numeric amount (e.g. 8142.16, 6.4, 740) or textual fact
- unit: Raw unit reported (e.g. 'INR crore', 'INR million', '%', 'million parcels')
- period_label: Original timeframe expressed (e.g. 'FY24', 'H1 FY25', 'FY25')
- period_type: 'fiscal_year', 'half_year', 'quarter', 'month', 'instant', or 'custom'
- scope: {"geography": "India", "level": "company"|"economy", "consolidation": "consolidated"|"standalone"|"unknown"}
- assertion_status: 'actual', 'estimate', 'forecast', 'projected', 'target', 'restated', or 'unknown'
- evidence_quote: Exact supporting excerpt from the text (verbatim quote)
- confidence: Float between 0.0 and 1.0 reflecting clarity of disclosure

Return ONLY a JSON array of extracted observation objects:
[
  {
    "entity_name": "...",
    "entity_type": "...",
    "concept_name": "...",
    "source_label": "...",
    "value_type": "currency",
    "value": 8142.16,
    "unit": "INR crore",
    "period_label": "FY24",
    "period_type": "fiscal_year",
    "scope": {"geography": "India", "level": "company", "consolidation": "consolidated"},
    "assertion_status": "actual",
    "evidence_quote": "...",
    "confidence": 0.98
  }
]
"""


def extract_observations_from_text(
    text: str,
    page_number: int,
    document_id: str,
    chunk_id: Optional[str] = None,
) -> List[Observation]:
    """
    Executes live LLM extraction against text using Gemini/OpenAI if API credentials exist,
    falling back to standard pattern extraction when running offline.
    """
    prompt = f"Document excerpt (Page {page_number}):\n\"\"\"\n{text}\n\"\"\"\n\nExtract all grounded atomic financial or macroeconomic observations."

    observations: List[Observation] = []
    llm_output = call_llm(prompt, system_instruction=EXTRACTION_SYSTEM_PROMPT)

    if llm_output:
        try:
            # Handle potential markdown code fencing in output
            clean_json = llm_output.strip()
            if clean_json.startswith("```"):
                clean_json = re.sub(r"^```(?:json)?\n?", "", clean_json)
                clean_json = re.sub(r"\n?```$", "", clean_json)

            data = json.loads(clean_json)
            # If wrapped in an object
            if isinstance(data, dict) and "observations" in data:
                items = data["observations"]
            elif isinstance(data, list):
                items = data
            else:
                items = [data]

            for item in items:
                item["page_number"] = page_number
                candidate = ExtractedObservation(**item)
                obs = process_extracted_candidate(candidate, document_id=document_id, chunk_id=chunk_id)
                observations.append(obs)

            if observations:
                return observations
        except Exception as e:
            print(f"[LLM Extraction Parse Warning]: {e}. Falling back to rule-assisted parser.")

    # Fallback pattern extraction if offline or parse failure
    return _pattern_extract_fallback(text, page_number, document_id, chunk_id)


def _pattern_extract_fallback(
    text: str,
    page_number: int,
    document_id: str,
    chunk_id: Optional[str] = None,
) -> List[Observation]:
    """
    Heuristic rule-based extractor that runs if no LLM key is configured.
    Finds percentages, crores, and millions grounded in sentence excerpts.
    """
    results: List[Observation] = []
    sentences = re.split(r"(?<=[.!?])\s+", text)

    for s in sentences:
        s_clean = s.strip()
        # Look for percentages
        pct_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|per\s*cent)", s_clean, re.I)
        if pct_match and ("gdp" in s_clean.lower() or "inflation" in s_clean.lower() or "growth" in s_clean.lower()):
            val = float(pct_match.group(1))
            concept = "real gdp growth" if "gdp" in s_clean.lower() else "headline cpi inflation"
            status = AssertionStatus.ESTIMATE if "estimate" in s_clean.lower() else (
                AssertionStatus.FORECAST if ("forecast" in s_clean.lower() or "project" in s_clean.lower()) else AssertionStatus.ACTUAL
            )
            fy_match = re.search(r"(?:fy|20)?(\d{2})", s_clean, re.I)
            period = f"FY{fy_match.group(1)}" if fy_match else "FY25"

            candidate = ExtractedObservation(
                entity_name="India",
                entity_type="country",
                concept_name=concept,
                source_label=concept,
                value_type=ValueType.PERCENTAGE,
                value=val,
                unit="%",
                period_label=period,
                period_type=PeriodType.FISCAL_YEAR,
                scope=Scope(geography="India", level="economy"),
                assertion_status=status,
                evidence_quote=s_clean,
                page_number=page_number,
                confidence=0.85,
            )
            results.append(process_extracted_candidate(candidate, document_id, chunk_id))

    return results


def process_extracted_candidate(
    candidate: ExtractedObservation,
    document_id: str,
    chunk_id: Optional[str] = None,
) -> Observation:
    """
    Transforms an LLM-proposed candidate into a standardized Observation:
    1. Canonicalizes entity and concept names
    2. Normalizes numerical value and scale multipliers
    3. Normalizes reporting periods
    4. Evaluates whether human review is required
    """
    obs_id = f"obs_{uuid.uuid4().hex[:8]}"

    # Entity canonicalization
    canon_entity_name = canonicalize_entity_name(candidate.entity_name)
    entity = Entity(
        canonical_name=canon_entity_name,
        entity_type=candidate.entity_type,
        aliases=[candidate.entity_name] if candidate.entity_name != canon_entity_name else [],
    )

    # Concept canonicalization
    canon_concept_name = canonicalize_concept_name(candidate.concept_name)
    concept = Concept(
        canonical_name=canon_concept_name,
        source_label=candidate.source_label or candidate.concept_name,
    )

    # FactValue creation and normalization
    amt = float(candidate.value) if isinstance(candidate.value, (int, float)) else None
    raw_val = FactValue(
        type=candidate.value_type,
        amount=amt,
        unit=candidate.unit,
        text=str(candidate.value) if amt is None else None,
    )
    normalized_val = normalize_fact_value(raw_val)

    # TimeContext normalization
    period_type = candidate.period_type or PeriodType.CUSTOM
    label = candidate.period_label
    if label and ("fy" in label.lower() or re.search(r"\d{4}", label)):
        label = normalize_fiscal_year(label)

    time_ctx = TimeContext(
        period_type=period_type,
        label=label,
    )

    # Review heuristics
    needs_review = False
    review_reasons = []

    if candidate.value_type in (ValueType.NUMBER, ValueType.CURRENCY) and not candidate.unit:
        needs_review = True
        review_reasons.append("Missing numerical unit")

    if not candidate.period_label:
        needs_review = True
        review_reasons.append("Unspecified time period")

    if candidate.confidence < 0.70:
        needs_review = True
        review_reasons.append("Low extraction confidence score")

    # Evidence binding
    evidence_item = Evidence(
        document_id=document_id,
        page_number=candidate.page_number,
        section=candidate.section,
        chunk_id=chunk_id,
        quote=candidate.evidence_quote,
    )

    return Observation(
        id=obs_id,
        entity=entity,
        concept=concept,
        value=normalized_val,
        time=time_ctx,
        scope=candidate.scope,
        assertion_status=candidate.assertion_status,
        evidence=[evidence_item],
        confidence=candidate.confidence,
        needs_review=needs_review,
        review_reason="; ".join(review_reasons) if review_reasons else None,
    )
