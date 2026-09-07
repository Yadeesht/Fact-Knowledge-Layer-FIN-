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
    Extracts financial metrics, operational volumes, and macroeconomic figures
    with evidence grounding from filing excerpts.
    """
    results: List[Observation] = []
    lines_and_sentences = [s.strip() for s in re.split(r"(?:\n+|(?<=[.!?])\s+)", text) if len(s.strip()) > 15]

    for s_clean in lines_and_sentences:
        lower_s = s_clean.lower()

        # 1. Entity detection
        entity_name = "India"
        entity_type = "country"
        if "delhivery" in lower_s:
            entity_name = "Delhivery Limited"
            entity_type = "company"
        elif "rbi" in lower_s or "reserve bank" in lower_s:
            entity_name = "Reserve Bank of India"
            entity_type = "central_bank"
        elif "imf" in lower_s or "article iv" in lower_s:
            entity_name = "International Monetary Fund"
            entity_type = "agency"
        elif any(w in lower_s for w in ["economic survey", "ministry of finance", "union budget", "government of india"]):
            entity_name = "India"
            entity_type = "country"

        # 2. Scope & Status detection
        scope_consol = "consolidated" if "consolidated" in lower_s else ("standalone" if "standalone" in lower_s else "consolidated")
        scope_level = "company" if entity_type == "company" else "economy"

        status = AssertionStatus.ACTUAL
        if any(w in lower_s for w in ["forecast", "project", "projected", "projection"]):
            status = AssertionStatus.FORECAST
        elif any(w in lower_s for w in ["estimate", "estimated", "advance estimate", "provisional"]):
            status = AssertionStatus.ESTIMATE
        elif any(w in lower_s for w in ["target", "budgeted"]):
            status = AssertionStatus.TARGET

        # 3. Fiscal year / Period detection
        period_label = None
        period_type = PeriodType.FISCAL_YEAR
        fy_m = re.search(r"(?:(?:FY|20)\s?(\d{2,4})|20(\d{2})[-–/](\d{2}))", s_clean, re.I)
        if fy_m:
            if fy_m.group(1):
                raw_yr = fy_m.group(1)
                period_label = f"FY{raw_yr[-2:]}"
            elif fy_m.group(2) and fy_m.group(3):
                period_label = f"FY{fy_m.group(3)}"
        elif "h1" in lower_s:
            period_label = "H1 FY25"
            period_type = PeriodType.HALF_YEAR
        elif "q4" in lower_s:
            period_label = "Q4 FY24"
            period_type = PeriodType.QUARTER

        # 4. Financial Currency Metrics (e.g. Revenue, EBITDA, Net Profit/Loss)
        curr_pattern = re.search(
            r"(?:revenue|ebitda|profit|loss|turnover|pat|income).*?(?:₹|rs\.?|inr)?\s*([\d,]+(?:\.\d+)?)\s*(crore|cr|million|mn|lakh|bn|billion)?",
            s_clean,
            re.I,
        )
        if curr_pattern:
            raw_num_str = curr_pattern.group(1).replace(",", "")
            raw_unit = curr_pattern.group(2) or "crore"
            raw_unit_norm = raw_unit.lower()
            if raw_unit_norm in ["cr", "crore", "crores"]:
                unit_label = "INR crore"
            elif raw_unit_norm in ["mn", "million"]:
                unit_label = "INR million"
            elif raw_unit_norm in ["lakh", "lakhs"]:
                unit_label = "INR lakh"
            else:
                unit_label = "INR"

            concept_name = "revenue from operations"
            if "ebitda" in lower_s:
                concept_name = "adjusted ebitda"
            elif "loss" in lower_s or "profit" in lower_s or "pat" in lower_s:
                concept_name = "net profit after tax"

            try:
                num_val = float(raw_num_str)
                if num_val > 0:
                    candidate = ExtractedObservation(
                        entity_name=entity_name,
                        entity_type=entity_type,
                        concept_name=concept_name,
                        source_label=concept_name,
                        value_type=ValueType.CURRENCY,
                        value=num_val,
                        unit=unit_label,
                        period_label=period_label or ("FY24" if entity_type == "company" else "FY25"),
                        period_type=period_type,
                        scope=Scope(geography="India", level=scope_level, consolidation=scope_consol),
                        assertion_status=status,
                        evidence_quote=s_clean[:280],
                        page_number=page_number,
                        confidence=0.92,
                    )
                    results.append(process_extracted_candidate(candidate, document_id, chunk_id))
            except ValueError:
                pass

        # 5. Percentage Metrics (GDP, Inflation, Deficit, Margins)
        pct_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|per\s*cent)", s_clean, re.I)
        if pct_match:
            try:
                pct_val = float(pct_match.group(1))
                if 0.1 <= pct_val <= 100.0:
                    concept_name = None
                    if any(k in lower_s for k in ["gdp", "growth", "gross domestic product"]):
                        concept_name = "real gdp growth"
                    elif any(k in lower_s for k in ["inflation", "cpi", "consumer price"]):
                        concept_name = "headline cpi inflation"
                    elif any(k in lower_s for k in ["fiscal deficit", "deficit"]):
                        concept_name = "gross fiscal deficit"
                    elif any(k in lower_s for k in ["margin", "ebitda margin"]):
                        concept_name = "ebitda margin"

                    if concept_name:
                        candidate = ExtractedObservation(
                            entity_name=entity_name,
                            entity_type=entity_type,
                            concept_name=concept_name,
                            source_label=concept_name,
                            value_type=ValueType.PERCENTAGE,
                            value=pct_val,
                            unit="%",
                            period_label=period_label or "FY25",
                            period_type=period_type,
                            scope=Scope(geography="India", level=scope_level, consolidation=scope_consol),
                            assertion_status=status,
                            evidence_quote=s_clean[:280],
                            page_number=page_number,
                            confidence=0.90,
                        )
                        results.append(process_extracted_candidate(candidate, document_id, chunk_id))
            except ValueError:
                pass

        # 6. Volumes & Operational Metrics (Express Parcels, Pincodes)
        vol_match = re.search(
            r"([\d,]+(?:\.\d+)?)\s*(million|mn|lakh|thousand)?\s*(parcels|shipments|express parcels|pincodes|serviceable pincodes|centers)",
            s_clean,
            re.I,
        )
        if vol_match:
            try:
                raw_vol = float(vol_match.group(1).replace(",", ""))
                multiplier = vol_match.group(2)
                item_name = vol_match.group(3).lower()

                concept_name = "express parcel volume" if ("parcel" in item_name or "shipment" in item_name) else "serviceable pincodes"
                unit_label = f"{multiplier or ''} {item_name}".strip()

                # Missing timeframe triggers anomaly escalation (Case 4)
                candidate = ExtractedObservation(
                    entity_name=entity_name,
                    entity_type=entity_type,
                    concept_name=concept_name,
                    source_label=concept_name,
                    value_type=ValueType.NUMBER,
                    value=raw_vol,
                    unit=unit_label,
                    period_label=period_label,  # None triggers needs_review policy
                    period_type=period_type if period_label else PeriodType.CUSTOM,
                    scope=Scope(geography="India", level=scope_level, consolidation=scope_consol),
                    assertion_status=status,
                    evidence_quote=s_clean[:280],
                    page_number=page_number,
                    confidence=0.60 if not period_label else 0.88,
                )
                results.append(process_extracted_candidate(candidate, document_id, chunk_id))
            except ValueError:
                pass

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
