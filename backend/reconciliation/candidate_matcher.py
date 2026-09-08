import re
from typing import Tuple, Optional
from backend.models.schema import Observation, ValueType
from backend.core.canonicalizer import entities_match, concepts_match
from backend.core.temporal import compare_time
from backend.core.scope import compare_scope
from backend.core.normalizer import normalize_fact_value


MACRO_AGGREGATES = {
    "real gdp growth", "gdp growth", "real gdp growth rate",
    "real gva growth", "gva growth", "aggregate gdp growth", "economic growth"
}
MACRO_SECTOR_KEYWORDS = {"services", "manufacturing", "agriculture", "industry", "construction", "mining"}

REVENUE_AGGREGATES = {"revenue", "revenue from operations", "total revenue", "total turnover", "sales"}
REVENUE_SEGMENT_KEYWORDS = {"traded goods", "express parcel", "part truckload", "ptl", "supply chain", "freight"}

INFLATION_AGGREGATES = {"headline cpi inflation", "cpi inflation", "headline inflation"}
INFLATION_COMPONENTS = {"food inflation", "core cpi inflation", "core inflation", "fuel inflation"}


def are_concepts_related_siblings(a: Observation, b: Observation) -> bool:
    """
    Strict domain check for genuine component-aggregate or sub-metric pairs:
    1. Sector output (e.g. Services GVA growth) vs Aggregate Real GDP/GVA growth
    2. Segment revenue (e.g. Revenue from traded goods) vs Total Revenue from operations
    3. Component inflation (e.g. Food inflation) vs Headline CPI inflation
    Generic token overlaps (e.g. sharing the word 'growth' or 'volume') are strictly rejected.
    """
    c_a = (a.concept.canonical_name or "").lower().strip()
    c_b = (b.concept.canonical_name or "").lower().strip()
    if not c_a or not c_b or c_a == c_b:
        return False

    # 1. Macroeconomic sector growth vs Aggregate GDP/GVA growth
    if (
        (c_a in MACRO_AGGREGATES and any(k in c_b for k in MACRO_SECTOR_KEYWORDS) and any(w in c_b for w in ("gva", "growth", "output")))
        or (c_b in MACRO_AGGREGATES and any(k in c_a for k in MACRO_SECTOR_KEYWORDS) and any(w in c_a for w in ("gva", "growth", "output")))
    ):
        return True

    # 2. Segment revenue vs Aggregate revenue from operations
    if (
        (c_a in REVENUE_AGGREGATES and any(k in c_b for k in REVENUE_SEGMENT_KEYWORDS))
        or (c_b in REVENUE_AGGREGATES and any(k in c_a for k in REVENUE_SEGMENT_KEYWORDS))
    ):
        return True

    # 3. Component inflation vs Headline inflation
    if (
        (c_a in INFLATION_AGGREGATES and c_b in INFLATION_COMPONENTS)
        or (c_b in INFLATION_AGGREGATES and c_a in INFLATION_COMPONENTS)
    ):
        return True

    return False


class CandidateMatcher:
    """
    Candidate Matcher: Strict hierarchical eligibility funnel.
    Answers: 'Could these two observations be meaningfully comparable?'
    Enforces hard gates: Quarantine, Entity, Unit/Dimension, Scope, Time, and Concept.
    Pairs that fail any hard gate are discarded (return False).
    """

    @staticmethod
    def is_comparable_candidate(a: Observation, b: Observation) -> Tuple[bool, Optional[str]]:
        # -------------------------------------------------------------
        # Gate 0: Quarantine Policy (flagged observations never enter)
        # -------------------------------------------------------------
        if a.needs_review or b.needs_review:
            return False, "quarantined_needs_review"

        # -------------------------------------------------------------
        # Gate 1: Entity Gate (must be identical canonical entity)
        # -------------------------------------------------------------
        if not entities_match(a, b):
            return False, "entity_mismatch"

        # -------------------------------------------------------------
        # Gate 2: Unit / Value Type Gate
        # -------------------------------------------------------------
        if a.value.type != b.value.type:
            return False, "value_type_mismatch"

        if a.value.type in (ValueType.NUMBER, ValueType.PERCENTAGE, ValueType.CURRENCY):
            norm_a = normalize_fact_value(a.value)
            norm_b = normalize_fact_value(b.value)
            if norm_a.normalized_unit and norm_b.normalized_unit:
                if norm_a.normalized_unit != norm_b.normalized_unit:
                    # e.g. INR vs USD might reach unresolved if concepts match, but % vs INR is discarded
                    if not ({norm_a.normalized_unit, norm_b.normalized_unit} <= {"INR", "USD", "EUR", "GBP"}):
                        return False, "unit_incompatible"

        # -------------------------------------------------------------
        # Gate 3: Scope Gate (geography must not conflict)
        # -------------------------------------------------------------
        geo_a = (a.scope.geography or "").lower().strip()
        geo_b = (b.scope.geography or "").lower().strip()
        if geo_a and geo_b and geo_a != geo_b:
            return False, "geography_mismatch"

        # -------------------------------------------------------------
        # Gate 4: Temporal Gate (distinct fiscal years are not comparable)
        # -------------------------------------------------------------
        time_res = compare_time(a.time, b.time)
        if time_res == "different":
            return False, "different_period"

        # -------------------------------------------------------------
        # Gate 5: Concept Gate
        # -------------------------------------------------------------
        if concepts_match(a, b):
            return True, "identical_concept"

        # Related sub-component / sibling metrics (e.g. Services GVA vs GDP)
        if are_concepts_related_siblings(a, b):
            return True, "related_sibling_concept"

        return False, "concept_mismatch"

