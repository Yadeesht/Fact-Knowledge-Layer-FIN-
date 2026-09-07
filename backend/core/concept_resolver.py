import re
import json
from typing import Optional, Dict, Any, List
from backend.models.schema import ConceptResolution, ResolutionStatus
from backend.core.embedding_client import get_embedding, cosine_similarity
from backend.core.canonicalizer import CONCEPT_ALIASES
from backend.core.llm_client import call_llm


HIGH_CONFIDENCE_THRESHOLD = 0.92
LOW_CONFIDENCE_THRESHOLD = 0.75

DELTA_QUALIFIERS = {"growth", "expansion", "contraction", "deflation", "decline", "increase", "decrease"}


def _has_divergent_qualifiers(name_a: str, name_b: str) -> bool:
    """
    Returns True if one concept contains rate-of-change or sub-component qualifiers
    that the other lacks, indicating they should not be merged on embedding similarity alone.
    """
    words_a = set(re.findall(r"\w+", name_a.lower()))
    words_b = set(re.findall(r"\w+", name_b.lower()))

    # Rate of change check: "growth" in one but not the other
    has_delta_a = bool(words_a & DELTA_QUALIFIERS)
    has_delta_b = bool(words_b & DELTA_QUALIFIERS)
    if has_delta_a != has_delta_b:
        return True

    # Sub-component keywords: if one specifies sub-sources or categories that the other omits or differs on
    SPECIFICITY_KEYWORDS = {
        "traded", "goods", "contract", "contracts", "customer", "customers",
        "food", "fuel", "core", "services", "agriculture", "manufacturing",
        "industrial", "mining", "utilities", "rural", "urban", "primary",
        "headline", "wholesale", "consumer", "retail", "effective",
    }
    spec_a = words_a & SPECIFICITY_KEYWORDS
    spec_b = words_b & SPECIFICITY_KEYWORDS
    if spec_a != spec_b:
        return True

    return False


def _judge_concept_disambiguation(
    raw_name: str,
    source_label: str,
    context: str,
    candidate_name: str,
    candidate_desc: Optional[str] = None,
) -> Optional[bool]:
    """
    Uses targeted LLM evaluation to decide if two closely related concepts
    refer to the identical underlying economic measurement or distinct sub-components.
    """
    prompt = f"""\
You are an expert financial fact auditor.
Determine whether a newly extracted financial concept is the EXACT SAME metric as an existing canonical concept, or a DISTINCT / SUB-METRIC.

New extracted concept: "{raw_name}"
Source label: "{source_label}"
Context from text: "{context[:300]}"

Existing candidate canonical concept: "{candidate_name}"
Candidate description: "{candidate_desc or candidate_name}"

Rules:
1. Sub-components (e.g. "revenue from traded goods" vs "revenue") are DISTINCT (is_same: false).
2. Rates vs Levels (e.g. "effective capital expenditure growth" vs "effective capital expenditure") are DISTINCT (is_same: false).
3. Ratios vs Levels (e.g. "R&D expenditure as % of GDP" vs "real GDP") are DISTINCT (is_same: false).
4. Morphological / syntactic variants (e.g. "growth in real GDP" vs "real GDP growth", "economic expansion" vs "real GDP growth") are SAME (is_same: true).

Return ONLY valid JSON:
{{
  "is_same": true,
  "explanation": "concise explanation"
}}
"""
    try:
        resp = call_llm(prompt)
        if not resp:
            return None
        clean = resp.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```(?:json)?\n?", "", clean)
            clean = re.sub(r"\n?```$", "", clean)
        data = json.loads(clean)
        return bool(data.get("is_same"))
    except Exception:
        return None


def resolve_concept(
    raw_name: str,
    source_label: Optional[str] = None,
    context: Optional[str] = None,
    unit: Optional[str] = None,
    repo: Optional[Any] = None,
) -> ConceptResolution:
    """
    Dynamic Concept Resolver:
    1. Normalizes raw text
    2. Checks bootstrap fast-path dictionary
    3. Retrieves semantic candidates via embeddings from database concepts table
    4. Applies anti-conflation guardrails
    5. Dispatches medium-similarity candidates to LLM judge
    6. Registers genuinely new concepts into the dynamic vocabulary
    """
    cleaned = re.sub(r"\s+", " ", (raw_name or "").lower().strip())
    if not cleaned:
        return ConceptResolution(
            canonical_name="unspecified concept",
            status=ResolutionStatus.NEW,
            matched_via="fallback",
        )

    src_label = (source_label or cleaned).strip()
    ctx = (context or "").strip()

    # ─────────────────────────────────────────────────────────────
    # Tier 1: Fast-Path Bootstrap Aliases
    # ─────────────────────────────────────────────────────────────
    if cleaned in CONCEPT_ALIASES and CONCEPT_ALIASES[cleaned] != cleaned:
        canonical = CONCEPT_ALIASES[cleaned]
        return ConceptResolution(
            canonical_name=canonical,
            status=ResolutionStatus.MATCHED,
            matched_via="alias",
            similarity=1.0,
            candidates=[{"name": canonical, "similarity": 1.0}],
            explanation="Exact match in bootstrap concept alias dictionary",
        )

    # ─────────────────────────────────────────────────────────────
    # Tier 2: Exact Match in Dynamic Concepts Table
    # ─────────────────────────────────────────────────────────────
    existing_concepts: List[Dict[str, Any]] = []
    if repo and hasattr(repo, "list_all_concepts"):
        try:
            existing_concepts = repo.list_all_concepts()
        except Exception:
            existing_concepts = []

    for c in existing_concepts:
        c_name = c["canonical_name"].lower()
        aliases = [a.lower() for a in c.get("aliases", [])]
        if cleaned == c_name or cleaned in aliases:
            return ConceptResolution(
                canonical_concept_id=c["id"],
                canonical_name=c["canonical_name"],
                status=ResolutionStatus.MATCHED,
                matched_via="exact",
                similarity=1.0,
                candidates=[{"name": c["canonical_name"], "similarity": 1.0}],
                explanation="Exact match in persistent concept repository",
            )

    # If no existing concepts in DB to compare against, register as new
    if not existing_concepts:
        emb = get_embedding(cleaned)
        new_id = None
        if repo and hasattr(repo, "get_or_create_concept"):
            new_id = repo.get_or_create_concept(
                canonical_name=cleaned,
                description=f"Source label: '{src_label}'",
                embedding=emb,
            )
        return ConceptResolution(
            canonical_concept_id=new_id,
            canonical_name=cleaned,
            status=ResolutionStatus.NEW,
            matched_via="new_concept",
            similarity=None,
            explanation="Initial concept created in dynamic repository",
        )

    # ─────────────────────────────────────────────────────────────
    # Tier 3: Semantic Embedding Candidate Retrieval
    # ─────────────────────────────────────────────────────────────
    query_emb = get_embedding(cleaned)
    ranked_candidates = []

    for c in existing_concepts:
        c_name = c["canonical_name"]
        c_emb = c.get("embedding")
        if not c_emb:
            c_emb = get_embedding(c_name)
            if repo and hasattr(repo, "update_concept_embedding") and c.get("id"):
                repo.update_concept_embedding(c["id"], c_emb)

        sim = cosine_similarity(query_emb, c_emb)
        ranked_candidates.append({
            "id": c.get("id"),
            "name": c_name,
            "similarity": round(sim, 4),
            "description": c.get("description"),
        })

    ranked_candidates.sort(key=lambda x: x["similarity"], reverse=True)
    top_cand = ranked_candidates[0] if ranked_candidates else None
    top_sim = top_cand["similarity"] if top_cand else 0.0

    # ─────────────────────────────────────────────────────────────
    # Tier 4: Decision Gating (High / Ambiguous / Low)
    # ─────────────────────────────────────────────────────────────
    # Case A: Low similarity -> Definitely New Concept
    if top_sim < LOW_CONFIDENCE_THRESHOLD:
        new_id = None
        if repo and hasattr(repo, "get_or_create_concept"):
            new_id = repo.get_or_create_concept(
                canonical_name=cleaned,
                description=f"Source label: '{src_label}'",
                embedding=query_emb,
            )
        return ConceptResolution(
            canonical_concept_id=new_id,
            canonical_name=cleaned,
            status=ResolutionStatus.NEW,
            matched_via="new_concept",
            similarity=top_sim,
            candidates=ranked_candidates[:3],
            explanation=f"No close concept found (top similarity {top_sim:.2f} < {LOW_CONFIDENCE_THRESHOLD}). Registered new concept.",
        )

    # Check for sub-component or rate divergence (anti-conflation)
    has_divergence = _has_divergent_qualifiers(cleaned, top_cand["name"])

    # Case B: High similarity AND passes anti-conflation guardrails
    if top_sim >= HIGH_CONFIDENCE_THRESHOLD and not has_divergence:
        # Confident semantic match
        if repo and hasattr(repo, "get_or_create_concept"):
            repo.get_or_create_concept(
                canonical_name=top_cand["name"],
                aliases=[cleaned],
            )
        return ConceptResolution(
            canonical_concept_id=top_cand["id"],
            canonical_name=top_cand["name"],
            status=ResolutionStatus.MATCHED,
            matched_via="embedding_high",
            similarity=top_sim,
            candidates=ranked_candidates[:3],
            explanation=f"High semantic similarity ({top_sim:.2f} >= {HIGH_CONFIDENCE_THRESHOLD}) to '{top_cand['name']}'",
        )

    # Case C: Medium similarity OR divergent qualifiers -> Context-Aware LLM Judge
    # Try LLM disambiguation judge if available
    llm_decision = _judge_concept_disambiguation(
        raw_name=cleaned,
        source_label=src_label,
        context=ctx,
        candidate_name=top_cand["name"],
        candidate_desc=top_cand.get("description"),
    )

    if llm_decision is True:
        # LLM confirmed they are the exact same measurement
        if repo and hasattr(repo, "get_or_create_concept"):
            repo.get_or_create_concept(canonical_name=top_cand["name"], aliases=[cleaned])
        return ConceptResolution(
            canonical_concept_id=top_cand["id"],
            canonical_name=top_cand["name"],
            status=ResolutionStatus.MATCHED,
            matched_via="llm_judge",
            similarity=top_sim,
            candidates=ranked_candidates[:3],
            explanation=f"LLM judge confirmed match with '{top_cand['name']}' (similarity: {top_sim:.2f})",
        )
    elif llm_decision is False:
        # LLM explicitly determined it is a distinct sub-metric or specific concept
        new_id = None
        if repo and hasattr(repo, "get_or_create_concept"):
            new_id = repo.get_or_create_concept(
                canonical_name=cleaned,
                description=f"Source label: '{src_label}' (distinct from {top_cand['name']})",
                embedding=query_emb,
            )
        return ConceptResolution(
            canonical_concept_id=new_id,
            canonical_name=cleaned,
            status=ResolutionStatus.NEW,
            matched_via="new_concept",
            similarity=top_sim,
            candidates=ranked_candidates[:3],
            explanation=f"LLM judge confirmed '{cleaned}' is distinct from '{top_cand['name']}'. Created new concept.",
        )

    # Fallback if LLM judge is unavailable: cautious separation
    # If divergent, do NOT silently merge!
    if has_divergence:
        new_id = None
        if repo and hasattr(repo, "get_or_create_concept"):
            new_id = repo.get_or_create_concept(
                canonical_name=cleaned,
                description=f"Source label: '{src_label}'",
                embedding=query_emb,
            )
        return ConceptResolution(
            canonical_concept_id=new_id,
            canonical_name=cleaned,
            status=ResolutionStatus.NEW,
            matched_via="new_concept",
            similarity=top_sim,
            candidates=ranked_candidates[:3],
            explanation=f"Divergent specificity detected against '{top_cand['name']}'. Guarded as distinct concept.",
        )

    # Ambiguous candidate
    return ConceptResolution(
        canonical_concept_id=top_cand["id"],
        canonical_name=cleaned,
        status=ResolutionStatus.AMBIGUOUS,
        matched_via="ambiguous",
        similarity=top_sim,
        candidates=ranked_candidates[:3],
        explanation=f"Ambiguous match with '{top_cand['name']}' (similarity: {top_sim:.2f}). Kept separate for review.",
    )
