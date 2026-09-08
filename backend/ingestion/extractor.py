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
    ResolutionStatus,
)
from backend.core.normalizer import normalize_fact_value
from backend.core.canonicalizer import canonicalize_entity_name, canonicalize_concept_name
from backend.core.concept_resolver import resolve_concept
from backend.core.entity_resolver import resolve_entity
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
    repo: Optional[Any] = None,
) -> List[Observation]:
    """
    Unified extraction engine: extracts atomic observations from an LLM batch
    containing structured chunks (paragraphs, tables, narrative context).
    Grounds each observation to its specific originating chunk_id and page.
    Requires an active online LLM API key (GEMINI_API_KEY or GOOGLE_API_KEY).
    """
    from backend.core.llm_client import get_llm_credentials
    creds = get_llm_credentials()
    if not creds.get("gemini_key"):
        raise RuntimeError("LLM API key is missing. Please add GEMINI_API_KEY (or GOOGLE_API_KEY) to your .env file to process documents.")

    from backend.ingestion.pipeline import is_cancellation_requested
    if is_cancellation_requested():
        raise InterruptedError("Extraction cancelled by user before batch LLM call.")

    formatted_batch = batch.format_for_llm()
    prompt = get_extraction_user_prompt(formatted_batch)

    llm_output = call_llm(prompt, system_instruction=EXTRACTION_SYSTEM_PROMPT)
    if is_cancellation_requested():
        raise InterruptedError("Extraction cancelled by user during batch LLM call.")

    if not llm_output:
        raise RuntimeError(f"LLM extraction failed for batch {batch.batch_id}. Please verify your API key, network connection, or quota limits.")

    observations: List[Observation] = []
    clean_json = llm_output.strip()
    if clean_json.startswith("```"):
        clean_json = re.sub(r"^```(?:json)?\n?", "", clean_json)
        clean_json = re.sub(r"\n?```$", "", clean_json)

    try:
        data = json.loads(clean_json)
    except Exception as e:
        raise RuntimeError(f"Failed to parse LLM extraction response as valid JSON for batch {batch.batch_id}: {e}\nRaw output: {llm_output[:200]}")

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

        try:
            candidate = ExtractedObservation(**item)
        except Exception as e:
            print(f"[Extractor] Warning: Skipping malformed candidate in batch {batch.batch_id}: {e}")
            continue

        # ── Grounding validation gate ──
        grounding_passed, grounding_reasons = validate_grounding(candidate)

        obs = process_extracted_candidate(
            candidate,
            document_id=document_id,
            chunk_id=target_chunk_id,
            grounding_reasons=grounding_reasons,
            repo=repo,
        )
        observations.append(obs)

    return observations


def extract_observations_from_text(
    text: str,
    page_number: int,
    document_id: str,
    chunk_id: Optional[str] = None,
    repo: Optional[Any] = None,
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
    return extract_observations_from_batch(single_batch, document_id=document_id, repo=repo)


def _pattern_extract_fallback(*args, **kwargs) -> List[Observation]:
    """Deprecated: The system requires an active LLM key and does not use heuristic regex fallback."""
    raise RuntimeError("Offline pattern extraction has been disabled. Please configure GEMINI_API_KEY in .env.")



def process_extracted_candidate(
    candidate: ExtractedObservation,
    document_id: str,
    chunk_id: Optional[str] = None,
    grounding_reasons: Optional[List[str]] = None,
    repo: Optional[Any] = None,
) -> Observation:
    """
    Transforms an LLM-proposed candidate into a standardized Observation:
    1. Resolves dynamic entity and concept (via dynamic semantic resolver)
    2. Normalizes numerical value and scale multipliers
    3. Normalizes reporting periods
    4. Evaluates whether human review is required (incl. grounding checks and resolution ambiguity)
    """
    obs_id = f"obs_{uuid.uuid4().hex[:8]}"

    # Dynamic Entity resolution
    entity_res = resolve_entity(
        raw_name=candidate.entity_name,
        entity_type=candidate.entity_type,
        context=candidate.evidence_quote,
        repo=repo,
    )
    entity = Entity(
        id=entity_res.canonical_entity_id,
        canonical_name=entity_res.canonical_name,
        entity_type=entity_res.entity_type or candidate.entity_type,
        aliases=[candidate.entity_name] if candidate.entity_name != entity_res.canonical_name else [],
    )

    # Dynamic Concept resolution
    concept_res = resolve_concept(
        raw_name=candidate.concept_name,
        source_label=candidate.source_label,
        context=candidate.evidence_quote,
        unit=candidate.unit,
        repo=repo,
    )
    concept = Concept(
        id=concept_res.canonical_concept_id,
        canonical_name=concept_res.canonical_name,
        source_label=candidate.source_label or candidate.concept_name,
        definition=concept_res.explanation,
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

    # Dynamic resolution ambiguity checks
    if entity_res.status == ResolutionStatus.AMBIGUOUS:
        needs_review = True
        review_reasons.append(f"Ambiguous entity resolution: {entity_res.explanation or candidate.entity_name}")

    if concept_res.status == ResolutionStatus.AMBIGUOUS:
        needs_review = True
        review_reasons.append(f"Ambiguous concept resolution: {concept_res.explanation or candidate.concept_name}")

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
