import uuid
import re
from typing import Optional
from backend.models.schema import (
    Observation,
    Relationship,
    RelationshipType,
    AssertionStatus,
    ValueType,
    PeriodType,
    ComparabilitySignature,
    NumericComparison,
)
from backend.core.canonicalizer import entities_match, concepts_match
from backend.core.scope import compare_scope
from backend.core.temporal import compare_time
from backend.core.materiality import approximately_equal, calculate_relative_difference
from backend.core.normalizer import normalize_fact_value
from backend.reconciliation.candidate_matcher import are_concepts_related_siblings


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


def build_comparability_signature(
    a: Observation,
    b: Observation,
    is_same_concept: bool,
    is_sibling_concept: bool,
) -> ComparabilitySignature:
    # Entity
    entity_m = "match" if entities_match(a, b) else "mismatch"

    # Concept
    if is_same_concept:
        concept_m = "match"
    elif is_sibling_concept:
        concept_m = "related_sub_metric"
    else:
        concept_m = "mismatch"

    # Period
    time_res = compare_time(a.time, b.time)
    p_a = a.time.period_type
    p_b = b.time.period_type
    if (
        p_a != p_b
        and {p_a, p_b} & {PeriodType.QUARTER, PeriodType.MONTH, PeriodType.HALF_YEAR}
        and {p_a, p_b} & {PeriodType.FISCAL_YEAR}
    ):
        period_m = "different_aggregation"
    elif time_res == "nested":
        period_m = "nested"
    elif time_res == "different":
        period_m = "different_period"
    else:
        period_m = "match"

    # Scope
    scope_res = compare_scope(a.scope, b.scope)
    if scope_res == "different":
        if (a.scope.consolidation or "").lower() != (b.scope.consolidation or "").lower():
            scope_m = "different_consolidation"
        else:
            scope_m = "different_geography"
    else:
        scope_m = "match"

    # Unit
    if is_numeric(a) and is_numeric(b):
        norm_a = normalize_fact_value(a.value)
        norm_b = normalize_fact_value(b.value)
        if norm_a.normalized_unit == norm_b.normalized_unit:
            unit_m = "normalized_match" if (a.value.unit or "") != (b.value.unit or "") else "match"
        else:
            unit_m = "incompatible"
    else:
        unit_m = "match"

    # Status relation
    if a.assertion_status == b.assertion_status:
        status_rel = "identical"
    elif {a.assertion_status, b.assertion_status} == {AssertionStatus.ACTUAL, AssertionStatus.ESTIMATE}:
        status_rel = "actual_vs_estimate"
    elif {a.assertion_status, b.assertion_status} == {AssertionStatus.ACTUAL, AssertionStatus.FORECAST}:
        status_rel = "actual_vs_forecast"
    elif {a.assertion_status, b.assertion_status} == {AssertionStatus.ACTUAL, AssertionStatus.TARGET}:
        status_rel = "actual_vs_target"
    else:
        status_rel = "different"

    return ComparabilitySignature(
        entity_match=entity_m,
        concept_match=concept_m,
        period_match=period_m,
        scope_match=scope_m,
        unit_match=unit_m,
        status_relation=status_rel,
    )


def build_numeric_comparison(a: Observation, b: Observation) -> Optional[NumericComparison]:
    if not is_numeric(a) or not is_numeric(b):
        return None
    norm_a = normalize_fact_value(a.value)
    norm_b = normalize_fact_value(b.value)
    val_a = norm_a.normalized_amount
    val_b = norm_b.normalized_amount
    if val_a is None or val_b is None:
        return None
    diff = round(val_a - val_b, 4)
    rel_diff = round(calculate_relative_difference(val_a, val_b), 6)
    return NumericComparison(
        value_a=val_a,
        value_b=val_b,
        difference=diff,
        relative_difference=rel_diff,
    )


def reconcile_deterministically(
    a: Observation,
    b: Observation,
    candidate_type: Optional[str] = None,
) -> Optional[Relationship]:
    """
    Deterministic Reconciliation Decision Matrix:
    Evaluates two observations against rigorous domain rules to produce:
    - CORROBORATED (materially equivalent under comparable conditions)
    - CONTRADICTED (material discrepancy on matching actuals)
    - APPARENT_CONTRADICTION (differing numbers caused by distinct sub-components/metrics)
    - NOT_COMPARABLE (differing time aggregation, e.g. quarterly vs full-year, or differing years)
    - CONTEXTUALIZED (scope boundary, nested period, or estimate vs actual outturn)
    - UNRESOLVED (currency conversion without explicit FX)
    """
    rel_id = make_relationship_id(a.id, b.id)

    # 1. Entity check
    if not entities_match(a, b):
        return None

    # 2. Concept comparability
    is_same = concepts_match(a, b)
    is_sibling = False
    if not is_same:
        is_sibling = candidate_type == "related_sibling_concept" or are_concepts_related_siblings(a, b)
        if not is_sibling:
            return None

    comparability = build_comparability_signature(a, b, is_same, is_sibling)
    numeric = build_numeric_comparison(a, b)

    # -------------------------------------------------------------
    # Rule 1: APPARENT_CONTRADICTION (Sibling / Sub-Component Metrics)
    # -------------------------------------------------------------
    if is_sibling:
        # Sibling metrics must share entity, period, geography, and unit to be comparable
        if (
            comparability.period_match != "match"
            or comparability.scope_match == "different_geography"
            or comparability.unit_match == "incompatible"
        ):
            return None

        val_a_str = f"{a.value.amount} {a.value.unit or ''}".strip()
        val_b_str = f"{b.value.amount} {b.value.unit or ''}".strip()
        explanation = (
            f"The reported figures differ ({val_a_str} vs {val_b_str}), but this is an apparent contradiction: "
            f"they measure distinct metrics ('{a.concept.canonical_name}' vs '{b.concept.canonical_name}') "
            f"under the same reporting entity ('{a.entity.canonical_name}') for '{a.time.label or 'same period'}'. "
            f"Differences reflect differing measurement scope or sub-component granularity rather than a conflict."
        )
        return Relationship(
            id=rel_id,
            observation_a=a.id,
            observation_b=b.id,
            relationship_type=RelationshipType.APPARENT_CONTRADICTION,
            confidence=0.96,
            explanation=explanation,
            reasons=["same_entity", "related_sub_metric_concept", "different_measurement_basis"],
            comparability=comparability,
            numeric=numeric,
        )

    # -------------------------------------------------------------
    # Rule 2: Hard Discard for Non-Comparable Dimensions (Return None)
    # -------------------------------------------------------------
    if (
        comparability.period_match in ("different_period", "different_aggregation")
        or comparability.scope_match == "different_geography"
        or comparability.unit_match == "incompatible"
    ):
        return None

    # -------------------------------------------------------------
    # Rule 3: CONTEXTUALIZED (Nested Timeframe or Scope Mismatch)
    # -------------------------------------------------------------
    if comparability.period_match == "nested":
        explanation = (
            f"Observations describe '{a.entity.canonical_name}' - '{a.concept.canonical_name}' "
            f"over nested reporting periods ('{a.time.label}' nested within '{b.time.label}'). "
            f"The difference contextualizes interim progress rather than contradicting."
        )
        return Relationship(
            id=rel_id,
            observation_a=a.id,
            observation_b=b.id,
            relationship_type=RelationshipType.CONTEXTUALIZED,
            confidence=0.95,
            explanation=explanation,
            reasons=["same_entity", "same_concept", "nested_period"],
            comparability=comparability,
            numeric=numeric,
        )

    if comparability.scope_match != "match":
        scope_a = a.scope.consolidation or a.scope.geography or "unspecified"
        scope_b = b.scope.consolidation or b.scope.geography or "unspecified"
        explanation = (
            f"Both observations describe '{a.entity.canonical_name}' - '{a.concept.canonical_name}', "
            f"but differ in reporting scope ('{scope_a}' vs '{scope_b}'). "
            f"The difference is contextual rather than contradictory."
        )
        return Relationship(
            id=rel_id,
            observation_a=a.id,
            observation_b=b.id,
            relationship_type=RelationshipType.CONTEXTUALIZED,
            confidence=0.96,
            explanation=explanation,
            reasons=["same_entity", "same_concept", "different_scope"],
            comparability=comparability,
            numeric=numeric,
        )

    # -------------------------------------------------------------
    # Rule 4: Numerical & Currency Evaluation
    # -------------------------------------------------------------
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
                comparability=comparability,
                numeric=numeric,
            )

        val_a = norm_val_a.normalized_amount
        val_b = norm_val_b.normalized_amount

        if val_a is None or val_b is None:
            return None

        # Status-aware contextualization (Actual vs Estimate / Forecast / Target)
        if assertion_status_differs(a, b):
            explanation = (
                f"The reported figures differ ({val_a} vs {val_b}), but they represent different assertion "
                f"statuses: '{a.assertion_status.value}' vs '{b.assertion_status.value}'. "
                f"An estimate or projection contextualizes an actual outturn rather than contradicting."
            )
            return Relationship(
                id=rel_id,
                observation_a=a.id,
                observation_b=b.id,
                relationship_type=RelationshipType.CONTEXTUALIZED,
                confidence=0.90,
                explanation=explanation,
                reasons=[
                    "same_entity",
                    "same_concept",
                    "same_period",
                    "different_assertion_status",
                ],
                comparability=comparability,
                numeric=numeric,
            )

        # Exact / near-exact equality within materiality tolerance (0.1%)
        if approximately_equal(val_a, val_b):
            delta_str = f"difference: {abs(numeric.difference):.2f}" if numeric and numeric.difference is not None else ""
            explanation = (
                f"Both observations describe '{a.entity.canonical_name}' - '{a.concept.canonical_name}' "
                f"for '{a.time.label or 'same period'}'. Their values ({a.value.amount} {a.value.unit or ''} and "
                f"{b.value.amount} {b.value.unit or ''}) are equivalent after unit normalization "
                f"({val_a:.2f} {norm_val_a.normalized_unit or ''}, {delta_str})."
            )
            return Relationship(
                id=rel_id,
                observation_a=a.id,
                observation_b=b.id,
                relationship_type=RelationshipType.CORROBORATED,
                confidence=0.99,
                explanation=explanation,
                reasons=[
                    "same_entity",
                    "same_concept",
                    "same_period",
                    "equivalent_after_normalization",
                ],
                comparability=comparability,
                numeric=numeric,
            )

        # Material Contradiction
        rel_diff_pct = (numeric.relative_difference * 100) if numeric and numeric.relative_difference is not None else 0.0
        explanation = (
            f"Material value discrepancy ({val_a} vs {val_b}, relative difference: {rel_diff_pct:.1f}%) "
            f"for identical entity ('{a.entity.canonical_name}'), concept ('{a.concept.canonical_name}'), "
            f"scope, and period ('{a.time.label or 'same period'}') under matching '{a.assertion_status.value}' assertion status."
        )
        return Relationship(
            id=rel_id,
            observation_a=a.id,
            observation_b=b.id,
            relationship_type=RelationshipType.CONTRADICTED,
            confidence=0.93,
            explanation=explanation,
            reasons=[
                "same_entity",
                "same_concept",
                "same_period",
                "same_scope",
                "matching_assertion_status",
                "material_value_difference",
            ],
            comparability=comparability,
            numeric=numeric,
        )

    return None
