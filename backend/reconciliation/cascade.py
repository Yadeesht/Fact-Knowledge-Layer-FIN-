import uuid
from typing import Optional
from backend.models.schema import (
    Observation,
    Relationship,
    RelationshipType,
    AssertionStatus,
    ValueType,
)
from backend.core.canonicalizer import entities_match, concepts_match
from backend.core.scope import compare_scope
from backend.core.temporal import compare_time
from backend.core.materiality import approximately_equal, calculate_relative_difference
from backend.core.normalizer import normalize_fact_value


def make_relationship_id(obs_a_id: str, obs_b_id: str) -> str:
    sorted_ids = sorted([obs_a_id, obs_b_id])
    return f"rel_{sorted_ids[0]}_{sorted_ids[1]}"


def is_numeric(obs: Observation) -> bool:
    return obs.value.type in (ValueType.NUMBER, ValueType.PERCENTAGE, ValueType.CURRENCY)


def assertion_status_differs(a: Observation, b: Observation) -> bool:
    if (
        a.assertion_status == AssertionStatus.UNKNOWN
        or b.assertion_status == AssertionStatus.UNKNOWN
    ):
        return False
    return a.assertion_status != b.assertion_status


def reconcile_deterministically(
    a: Observation,
    b: Observation,
) -> Optional[Relationship]:
    """
    Progressively evaluates two observations with deterministic domain rules:
    1. Entity compatibility
    2. Concept compatibility
    3. Scope compatibility
    4. Time / Period compatibility
    5. Unit & Currency compatibility
    6. Value equivalence vs assertion status vs contradiction

    Returns:
        Relationship if deterministically resolved, or None if insufficient evidence / semantic.
    """
    rel_id = make_relationship_id(a.id, b.id)

    # --------------------------------------------------
    # 1. Entity check
    # --------------------------------------------------
    if not entities_match(a, b):
        return None

    # --------------------------------------------------
    # 2. Concept check
    # --------------------------------------------------
    if not concepts_match(a, b):
        return None

    # --------------------------------------------------
    # 3. Scope comparison
    # --------------------------------------------------
    scope_result = compare_scope(a.scope, b.scope)
    if scope_result == "different":
        return Relationship(
            id=rel_id,
            observation_a=a.id,
            observation_b=b.id,
            relationship_type=RelationshipType.CONTEXTUALIZED,
            confidence=0.96,
            explanation=(
                f"Both observations describe '{a.entity.canonical_name}' and '{a.concept.canonical_name}', "
                f"but differ in reporting scope (consolidation/geography: '{a.scope.consolidation}' vs "
                f"'{b.scope.consolidation}'). The difference is contextual rather than contradictory."
            ),
            reasons=["same_entity", "same_concept", "different_scope"],
        )

    # --------------------------------------------------
    # 4. Time comparison
    # --------------------------------------------------
    time_result = compare_time(a.time, b.time)
    if time_result in ("different", "nested"):
        reason_label = "nested_period" if time_result == "nested" else "different_period"
        explanation = (
            f"The observations refer to different reporting timeframes "
            f"('{a.time.label or 'Period A'}' vs '{b.time.label or 'Period B'}'). "
            f"Therefore, differing values represent distinct periods rather than a contradiction."
        )
        return Relationship(
            id=rel_id,
            observation_a=a.id,
            observation_b=b.id,
            relationship_type=RelationshipType.CONTEXTUALIZED,
            confidence=0.97,
            explanation=explanation,
            reasons=["same_entity", "same_concept", reason_label],
        )

    # --------------------------------------------------
    # 5. Numerical / Unit Comparison
    # --------------------------------------------------
    if is_numeric(a) and is_numeric(b):
        norm_val_a = normalize_fact_value(a.value)
        norm_val_b = normalize_fact_value(b.value)

        # Currency incompatibility check
        if (
            norm_val_a.normalized_unit
            and norm_val_b.normalized_unit
            and norm_val_a.normalized_unit in ("INR", "USD", "EUR", "GBP")
            and norm_val_b.normalized_unit in ("INR", "USD", "EUR", "GBP")
            and norm_val_a.normalized_unit != norm_val_b.normalized_unit
        ):
            return Relationship(
                id=rel_id,
                observation_a=a.id,
                observation_b=b.id,
                relationship_type=RelationshipType.UNRESOLVED,
                confidence=0.95,
                explanation=(
                    f"Different currencies reported ({norm_val_a.normalized_unit} vs {norm_val_b.normalized_unit}). "
                    f"Conversion cannot be safely performed without an explicit document-provided exchange rate."
                ),
                reasons=["same_entity", "same_concept", "different_currency_no_fx"],
            )

        val_a = norm_val_a.normalized_amount
        val_b = norm_val_b.normalized_amount

        if val_a is None or val_b is None:
            return None

        # Exact / near-exact equality within materiality tolerance (0.1%)
        if approximately_equal(val_a, val_b):
            return Relationship(
                id=rel_id,
                observation_a=a.id,
                observation_b=b.id,
                relationship_type=RelationshipType.CORROBORATED,
                confidence=0.99,
                explanation=(
                    f"The observations describe the same entity ('{a.entity.canonical_name}'), "
                    f"concept ('{a.concept.canonical_name}'), and period ('{a.time.label or 'current'}'). "
                    f"Their values ({a.value.amount} {a.value.unit or ''} and {b.value.amount} {b.value.unit or ''}) "
                    f"are equivalent after unit normalization ({val_a:.2f} {norm_val_a.normalized_unit or ''})."
                ),
                reasons=[
                    "same_entity",
                    "same_concept",
                    "same_period",
                    "equivalent_after_normalization",
                ],
            )

        # --------------------------------------------------
        # 6. Status-aware contextualization (Actual vs Estimate / Forecast)
        # --------------------------------------------------
        if assertion_status_differs(a, b):
            return Relationship(
                id=rel_id,
                observation_a=a.id,
                observation_b=b.id,
                relationship_type=RelationshipType.CONTEXTUALIZED,
                confidence=0.90,
                explanation=(
                    f"The reported figures differ ({val_a} vs {val_b}), but they represent different assertion "
                    f"statuses: '{a.assertion_status.value}' vs '{b.assertion_status.value}'. "
                    f"An estimate or projection is not in direct contradiction with an actual outcome."
                ),
                reasons=[
                    "same_entity",
                    "same_concept",
                    "same_period",
                    "different_assertion_status",
                ],
            )

        # --------------------------------------------------
        # 7. Material Contradiction
        # --------------------------------------------------
        rel_diff = calculate_relative_difference(val_a, val_b) * 100
        return Relationship(
            id=rel_id,
            observation_a=a.id,
            observation_b=b.id,
            relationship_type=RelationshipType.CONTRADICTED,
            confidence=0.93,
            explanation=(
                f"Material value discrepancy ({val_a} vs {val_b}, relative difference: {rel_diff:.1f}%) "
                f"for identical entity ('{a.entity.canonical_name}'), concept ('{a.concept.canonical_name}'), "
                f"scope, and period ('{a.time.label or 'same period'}') under matching assertion statuses."
            ),
            reasons=[
                "same_entity",
                "same_concept",
                "same_period",
                "same_scope",
                "matching_assertion_status",
                "material_value_difference",
            ],
        )

    # For qualitative/semantic observations, leave to LLM judge
    return None
