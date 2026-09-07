import re
import json
from typing import Optional, Dict, Any, List
from backend.models.schema import EntityResolution, ResolutionStatus
from backend.core.embedding_client import get_embedding, cosine_similarity
from backend.core.canonicalizer import ENTITY_ALIASES
from backend.core.llm_client import call_llm


HIGH_CONFIDENCE_THRESHOLD = 0.92
LOW_CONFIDENCE_THRESHOLD = 0.75


def _judge_entity_disambiguation(
    raw_name: str,
    context: str,
    candidate_name: str,
) -> Optional[bool]:
    """
    Uses targeted LLM evaluation to decide if an ambiguous entity mention
    refers to an existing canonical entity or a distinct entity.
    """
    prompt = f"""\
You are an expert entity resolution auditor.
Determine whether the extracted entity mention refers to the EXACT SAME organization/country/institution as an existing candidate entity.

Entity mention: "{raw_name}"
Context from document: "{context[:300]}"

Candidate canonical entity: "{candidate_name}"

Rules:
1. Distinct entities (e.g. "State Bank of India" vs "Reserve Bank of India", "World Bank" vs "IMF") are DISTINCT (is_same: false).
2. Generic mentions (e.g. "Bank" or "Government") without clear single referent are DISTINCT (is_same: false).
3. Acronyms, formal names, and known aliases (e.g. "RBI" vs "Reserve Bank of India", "Delhivery" vs "Delhivery Limited") are SAME (is_same: true).

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


def resolve_entity(
    raw_name: str,
    entity_type: Optional[str] = None,
    context: Optional[str] = None,
    repo: Optional[Any] = None,
) -> EntityResolution:
    """
    Dynamic Entity Resolver:
    1. Normalizes raw text
    2. Checks bootstrap fast-path dictionary
    3. Retrieves semantic candidates via embeddings from database entities table
    4. Evaluates confidence thresholds
    5. Dispatches medium-similarity candidates to LLM judge
    6. Registers genuinely new entities into the dynamic database
    """
    cleaned = re.sub(r"\s+", " ", (raw_name or "").lower().strip())
    if not cleaned:
        return EntityResolution(
            canonical_name="unspecified entity",
            status=ResolutionStatus.NEW,
            matched_via="fallback",
        )

    ctx = (context or "").strip()

    # ─────────────────────────────────────────────────────────────
    # Tier 1: Fast-Path Bootstrap Aliases
    # ─────────────────────────────────────────────────────────────
    if cleaned in ENTITY_ALIASES and ENTITY_ALIASES[cleaned] != cleaned:
        canonical = ENTITY_ALIASES[cleaned]
        return EntityResolution(
            canonical_name=canonical,
            entity_type=entity_type,
            status=ResolutionStatus.MATCHED,
            matched_via="alias",
            similarity=1.0,
            candidates=[{"name": canonical, "similarity": 1.0}],
            explanation="Exact match in bootstrap entity alias dictionary",
        )

    # ─────────────────────────────────────────────────────────────
    # Tier 2: Exact Match in Dynamic Entities Table
    # ─────────────────────────────────────────────────────────────
    existing_entities: List[Dict[str, Any]] = []
    if repo and hasattr(repo, "list_all_entities"):
        try:
            existing_entities = repo.list_all_entities()
        except Exception:
            existing_entities = []

    for e in existing_entities:
        e_name = e["canonical_name"].lower()
        aliases = [a.lower() for a in e.get("aliases", [])]
        if cleaned == e_name or cleaned in aliases:
            return EntityResolution(
                canonical_entity_id=e["id"],
                canonical_name=e["canonical_name"],
                entity_type=e.get("entity_type") or entity_type,
                status=ResolutionStatus.MATCHED,
                matched_via="exact",
                similarity=1.0,
                candidates=[{"name": e["canonical_name"], "similarity": 1.0}],
                explanation="Exact match in persistent entity repository",
            )

    # If no existing entities in DB to compare against, register as new
    if not existing_entities:
        emb = get_embedding(cleaned)
        new_id = None
        if repo and hasattr(repo, "get_or_create_entity"):
            new_id = repo.get_or_create_entity(
                canonical_name=cleaned,
                entity_type=entity_type,
                embedding=emb,
            )
        return EntityResolution(
            canonical_entity_id=new_id,
            canonical_name=cleaned,
            entity_type=entity_type,
            status=ResolutionStatus.NEW,
            matched_via="new_entity",
            similarity=None,
            explanation="Initial entity created in dynamic repository",
        )

    # ─────────────────────────────────────────────────────────────
    # Tier 3: Semantic Embedding Candidate Retrieval
    # ─────────────────────────────────────────────────────────────
    query_emb = get_embedding(cleaned)
    ranked_candidates = []

    for e in existing_entities:
        e_name = e["canonical_name"]
        e_emb = e.get("embedding")
        if not e_emb:
            e_emb = get_embedding(e_name)
            if repo and hasattr(repo, "update_entity_embedding") and e.get("id"):
                repo.update_entity_embedding(e["id"], e_emb)

        sim = cosine_similarity(query_emb, e_emb)
        ranked_candidates.append({
            "id": e.get("id"),
            "name": e_name,
            "entity_type": e.get("entity_type"),
            "similarity": round(sim, 4),
        })

    ranked_candidates.sort(key=lambda x: x["similarity"], reverse=True)
    top_cand = ranked_candidates[0] if ranked_candidates else None
    top_sim = top_cand["similarity"] if top_cand else 0.0

    # ─────────────────────────────────────────────────────────────
    # Tier 4: Decision Gating
    # ─────────────────────────────────────────────────────────────
    # Case A: Low similarity -> Definitely New Entity
    if top_sim < LOW_CONFIDENCE_THRESHOLD:
        new_id = None
        if repo and hasattr(repo, "get_or_create_entity"):
            new_id = repo.get_or_create_entity(
                canonical_name=cleaned,
                entity_type=entity_type,
                embedding=query_emb,
            )
        return EntityResolution(
            canonical_entity_id=new_id,
            canonical_name=cleaned,
            entity_type=entity_type,
            status=ResolutionStatus.NEW,
            matched_via="new_entity",
            similarity=top_sim,
            candidates=ranked_candidates[:3],
            explanation=f"No close entity found (top similarity {top_sim:.2f} < {LOW_CONFIDENCE_THRESHOLD}). Registered new entity.",
        )

    # Case B: High similarity -> Confident Match
    if top_sim >= HIGH_CONFIDENCE_THRESHOLD:
        if repo and hasattr(repo, "get_or_create_entity"):
            repo.get_or_create_entity(
                canonical_name=top_cand["name"],
                entity_type=top_cand.get("entity_type") or entity_type,
                aliases=[cleaned],
            )
        return EntityResolution(
            canonical_entity_id=top_cand["id"],
            canonical_name=top_cand["name"],
            entity_type=top_cand.get("entity_type") or entity_type,
            status=ResolutionStatus.MATCHED,
            matched_via="embedding_high",
            similarity=top_sim,
            candidates=ranked_candidates[:3],
            explanation=f"High semantic similarity ({top_sim:.2f} >= {HIGH_CONFIDENCE_THRESHOLD}) to '{top_cand['name']}'",
        )

    # Case C: Medium similarity (0.75 - 0.92) -> LLM Judge
    llm_decision = _judge_entity_disambiguation(
        raw_name=cleaned,
        context=ctx,
        candidate_name=top_cand["name"],
    )

    if llm_decision is True:
        if repo and hasattr(repo, "get_or_create_entity"):
            repo.get_or_create_entity(
                canonical_name=top_cand["name"],
                entity_type=top_cand.get("entity_type") or entity_type,
                aliases=[cleaned],
            )
        return EntityResolution(
            canonical_entity_id=top_cand["id"],
            canonical_name=top_cand["name"],
            entity_type=top_cand.get("entity_type") or entity_type,
            status=ResolutionStatus.MATCHED,
            matched_via="llm_judge",
            similarity=top_sim,
            candidates=ranked_candidates[:3],
            explanation=f"LLM judge confirmed match with '{top_cand['name']}' (similarity: {top_sim:.2f})",
        )
    elif llm_decision is False:
        new_id = None
        if repo and hasattr(repo, "get_or_create_entity"):
            new_id = repo.get_or_create_entity(
                canonical_name=cleaned,
                entity_type=entity_type,
                embedding=query_emb,
            )
        return EntityResolution(
            canonical_entity_id=new_id,
            canonical_name=cleaned,
            entity_type=entity_type,
            status=ResolutionStatus.NEW,
            matched_via="new_entity",
            similarity=top_sim,
            candidates=ranked_candidates[:3],
            explanation=f"LLM judge confirmed '{cleaned}' is distinct from '{top_cand['name']}'. Registered new entity.",
        )

    # Cautious Ambiguity: do not silently merge
    return EntityResolution(
        canonical_entity_id=top_cand["id"],
        canonical_name=cleaned,
        entity_type=entity_type,
        status=ResolutionStatus.AMBIGUOUS,
        matched_via="ambiguous",
        similarity=top_sim,
        candidates=ranked_candidates[:3],
        explanation=f"Ambiguous entity match with '{top_cand['name']}' (similarity: {top_sim:.2f}). Kept separate for review.",
    )
