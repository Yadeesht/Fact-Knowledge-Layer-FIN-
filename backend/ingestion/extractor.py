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
from backend.core.prompts import EXTRACTION_SYSTEM_PROMPT, get_extraction_user_prompt
from backend.core.grounding_validator import validate_grounding
from backend.ingestion.batcher import LLMBatch
from backend.core.llm_client import call_llm


def resolve_chunk_for_observation(
    candidate_chunk_id: Optional[str],
    candidate_page: Optional[int],
    quote: str,
    batch_chunks: List[Dict[str, Any]],
) -> tuple:
    """
    Deterministically maps an extracted claim to its exact originating chunk and page.
    1. Tests provided chunk_id against batch chunks.
    2. Fuzzy-matches evidence quote substring across chunks.
    3. Falls back to matching page number.
    4. Falls back to first chunk in batch.
    """
    if candidate_chunk_id:
        for chk in batch_chunks:
            if chk.get("id") == candidate_chunk_id:
                return chk.get("id"), chk.get("page_number", candidate_page or 1), chk.get("section")

    clean_q = quote.strip()[:80] if quote else ""
    if clean_q:
        clean_lower = clean_q.lower()
        for chk in batch_chunks:
            if clean_lower in chk.get("text", "").lower():
                return chk.get("id"), chk.get("page_number", candidate_page or 1), chk.get("section")

    if candidate_page:
        for chk in batch_chunks:
            if chk.get("page_number") == candidate_page:
                return chk.get("id"), chk.get("page_number", candidate_page), chk.get("section")

    first_chk = batch_chunks[0] if batch_chunks else {}
    return first_chk.get("id", "chk_unknown"), first_chk.get("page_number", 1), first_chk.get("section")


def extract_observations_from_batch(
    batch: LLMBatch,
    document_id: str,
) -> List[Observation]:
    """
    Unified extraction engine: extracts atomic observations from an LLM batch
    containing structured chunks (paragraphs, tables, narrative context).
    Grounds each observation to its specific originating chunk_id and page.
    """
    from backend.ingestion.pipeline import is_cancellation_requested
    if is_cancellation_requested():
        raise InterruptedError("Extraction cancelled by user before batch LLM call.")

    formatted_batch = batch.format_for_llm()
    prompt = get_extraction_user_prompt(formatted_batch)

    llm_output = call_llm(prompt, system_instruction=EXTRACTION_SYSTEM_PROMPT)
    print(llm_output)
    if is_cancellation_requested():
        raise InterruptedError("Extraction cancelled by user during batch LLM call.")

    observations: List[Observation] = []
    if llm_output:
        try:
            clean_json = llm_output.strip()
            if clean_json.startswith("```"):
                clean_json = re.sub(r"^```(?:json)?\n?", "", clean_json)
                clean_json = re.sub(r"\n?```$", "", clean_json)

            data = json.loads(clean_json)
            if isinstance(data, dict) and "observations" in data:
                items = data["observations"]
            elif isinstance(data, list):
                items = data
            else:
                items = [data]

            for item in items:
                # Resolve precise chunk_id and page_number
                target_chunk_id, target_page, target_sec = resolve_chunk_for_observation(
                    candidate_chunk_id=item.get("chunk_id"),
                    candidate_page=item.get("page_number"),
                    quote=item.get("evidence_quote", ""),
                    batch_chunks=batch.chunks,
                )

                item["chunk_id"] = target_chunk_id
                item["page_number"] = target_page
                if target_sec and not item.get("section"):
                    item["section"] = target_sec

                candidate = ExtractedObservation(**item)

                # ── Grounding validation gate ──
                grounding_passed, grounding_reasons = validate_grounding(candidate)

                obs = process_extracted_candidate(
                    candidate,
                    document_id=document_id,
                    chunk_id=target_chunk_id,
                    grounding_reasons=grounding_reasons,
                )
                observations.append(obs)

            if observations:
                return observations
        except Exception as e:
            print(f"[Batch LLM Extraction Warning]: {e}. Falling back to rule-assisted parser.")

    # Offline / pattern extraction fallback across all chunks in the batch
    for chk in batch.chunks:
        chk_obs = _pattern_extract_fallback(
            text=chk.get("text", ""),
            page_number=chk.get("page_number", 1),
            document_id=document_id,
            chunk_id=chk.get("id"),
        )
        observations.extend(chk_obs)

    return observations


def extract_observations_from_text(
    text: str,
    page_number: int,
    document_id: str,
    chunk_id: Optional[str] = None,
) -> List[Observation]:
    """
    Convenience wrapper that packages single text into an LLMBatch,
    delegating to the unified extract_observations_from_batch function.
    """
    cid = chunk_id or f"chk_{document_id}_p{page_number}_0"
    single_batch = LLMBatch(
        batch_id="batch_01",
        document_id=document_id,
        section="General Information",
        page_start=page_number,
        page_end=page_number,
        chunks=[{
            "id": cid,
            "page_number": page_number,
            "section": "General Information",
            "content_type": "text",
            "text": text,
        }],
        total_chars=len(text),
    )
    return extract_observations_from_batch(single_batch, document_id=document_id)


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
    grounding_reasons: Optional[List[str]] = None,
) -> Observation:
    """
    Transforms an LLM-proposed candidate into a standardized Observation:
    1. Canonicalizes entity and concept names
    2. Normalizes numerical value and scale multipliers
    3. Normalizes reporting periods
    4. Evaluates whether human review is required (incl. grounding checks)
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

    # Grounding validation reasons (from post-LLM validator)
    if grounding_reasons:
        needs_review = True
        review_reasons.extend(grounding_reasons)

    # Dimensionless metrics (indices, scores, rankings, ratios, statistical moments)
    # do not have units and should not be flagged for missing units
    DIMENSIONLESS_KEYWORDS = (
        "index", "fi-index", "pmi", "ccpi", "ratio", "score", "rank", "ranking",
        "standard deviation", "std dev", "skewness", "kurtosis", "parameter",
        "elasticity", "multiplier"
    )
    concept_lower = (candidate.concept_name or "").lower()
    source_lower = (candidate.source_label or "").lower()
    is_dimensionless = any(kw in concept_lower or kw in source_lower for kw in DIMENSIONLESS_KEYWORDS)

    if candidate.value_type in (ValueType.NUMBER, ValueType.CURRENCY) and not candidate.unit and not is_dimensionless:
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
        chunk_id=chunk_id or candidate.chunk_id,
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
