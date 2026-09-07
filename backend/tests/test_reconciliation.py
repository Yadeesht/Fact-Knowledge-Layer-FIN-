import pytest
from backend.models.schema import (
    Observation,
    Entity,
    Concept,
    FactValue,
    TimeContext,
    Scope,
    ValueType,
    PeriodType,
    AssertionStatus,
    RelationshipType,
)
from backend.core.normalizer import normalize_fact_value
from backend.core.materiality import approximately_equal
from backend.core.temporal import compare_time
from backend.reconciliation.cascade import reconcile_deterministically


def test_unit_normalization():
    # 8,142.16 Crore
    val_cr = FactValue(
        type=ValueType.CURRENCY,
        amount=8142.16,
        unit="INR crore",
    )
    norm_cr = normalize_fact_value(val_cr)
    assert norm_cr.normalized_amount == 81421600000.0
    assert norm_cr.normalized_unit == "INR"

    # 81,424 Million
    val_mn = FactValue(
        type=ValueType.CURRENCY,
        amount=81424.0,
        unit="INR million",
    )
    norm_mn = normalize_fact_value(val_mn)
    assert norm_mn.normalized_amount == 81424000000.0
    assert norm_mn.normalized_unit == "INR"

    # Materiality equivalence check (< 0.1% tolerance)
    assert approximately_equal(norm_cr.normalized_amount, norm_mn.normalized_amount)


def test_percentage_normalization():
    pct_val = FactValue(type=ValueType.PERCENTAGE, amount=6.4, unit="%")
    norm_pct = normalize_fact_value(pct_val)
    # Must remain human-scaled 6.4%, not converted to 0.064
    assert norm_pct.normalized_amount == 6.4
    assert norm_pct.normalized_unit == "%"


def test_temporal_nesting_and_different():
    t_fy = TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY24")
    t_h1 = TimeContext(period_type=PeriodType.HALF_YEAR, label="H1 FY24")
    t_fy25 = TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY25")

    assert compare_time(t_h1, t_fy) == "nested"
    assert compare_time(t_fy, t_fy25) == "different"


def test_cascade_corroboration():
    obs_a = Observation(
        id="a",
        entity=Entity(canonical_name="Delhivery Limited", aliases=["Delhivery"]),
        concept=Concept(canonical_name="revenue from operations"),
        value=FactValue(type=ValueType.CURRENCY, amount=8142.16, unit="INR crore"),
        time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY24"),
        assertion_status=AssertionStatus.ACTUAL,
    )
    obs_b = Observation(
        id="b",
        entity=Entity(canonical_name="Delhivery Limited"),
        concept=Concept(canonical_name="revenue from operations"),
        value=FactValue(type=ValueType.CURRENCY, amount=81424.0, unit="INR million"),
        time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY24"),
        assertion_status=AssertionStatus.ACTUAL,
    )

    rel = reconcile_deterministically(obs_a, obs_b)
    assert rel is not None
    assert rel.relationship_type == RelationshipType.CORROBORATED
    assert "equivalent_after_normalization" in rel.reasons


def test_cascade_status_aware_contextualization():
    obs_a = Observation(
        id="a",
        entity=Entity(canonical_name="India"),
        concept=Concept(canonical_name="real gdp growth"),
        value=FactValue(type=ValueType.PERCENTAGE, amount=6.4, unit="%"),
        time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY25"),
        assertion_status=AssertionStatus.ESTIMATE,
    )
    obs_b = Observation(
        id="b",
        entity=Entity(canonical_name="India"),
        concept=Concept(canonical_name="real gdp growth"),
        value=FactValue(type=ValueType.PERCENTAGE, amount=6.5, unit="%"),
        time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY25"),
        assertion_status=AssertionStatus.FORECAST,
    )

    rel = reconcile_deterministically(obs_a, obs_b)
    assert rel is not None
    assert rel.relationship_type == RelationshipType.CONTEXTUALIZED
    assert "different_assertion_status" in rel.reasons


def test_cascade_genuine_contradiction():
    obs_a = Observation(
        id="a",
        entity=Entity(canonical_name="India"),
        concept=Concept(canonical_name="real gdp growth"),
        value=FactValue(type=ValueType.PERCENTAGE, amount=6.4, unit="%"),
        time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY25"),
        assertion_status=AssertionStatus.ACTUAL,
    )
    obs_b = Observation(
        id="b",
        entity=Entity(canonical_name="India"),
        concept=Concept(canonical_name="real gdp growth"),
        value=FactValue(type=ValueType.PERCENTAGE, amount=7.2, unit="%"),
        time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY25"),
        assertion_status=AssertionStatus.ACTUAL,
    )

    rel = reconcile_deterministically(obs_a, obs_b)
    assert rel is not None
    assert rel.relationship_type == RelationshipType.CONTRADICTED
    assert "material_value_difference" in rel.reasons


def test_currency_unresolved_without_fx():
    obs_a = Observation(
        id="a",
        entity=Entity(canonical_name="Delhivery Limited"),
        concept=Concept(canonical_name="revenue"),
        value=FactValue(type=ValueType.CURRENCY, amount=100.0, unit="INR crore"),
        time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY24"),
        assertion_status=AssertionStatus.ACTUAL,
    )
    obs_b = Observation(
        id="b",
        entity=Entity(canonical_name="Delhivery Limited"),
        concept=Concept(canonical_name="revenue"),
        value=FactValue(type=ValueType.CURRENCY, amount=12.0, unit="USD million"),
        time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY24"),
        assertion_status=AssertionStatus.ACTUAL,
    )

    rel = reconcile_deterministically(obs_a, obs_b)
    assert rel is not None
    assert rel.relationship_type == RelationshipType.UNRESOLVED
    assert "different_currency_no_fx" in rel.reasons


def test_candidate_matcher_quarantine():
    from backend.reconciliation.candidate_matcher import CandidateMatcher

    obs_valid = Observation(
        id="valid_1",
        entity=Entity(canonical_name="Delhivery Limited"),
        concept=Concept(canonical_name="revenue from operations"),
        value=FactValue(type=ValueType.CURRENCY, amount=8142.16, unit="INR crore"),
        time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY24"),
        needs_review=False,
    )
    obs_quarantined = Observation(
        id="quarantine_1",
        entity=Entity(canonical_name="Delhivery Limited"),
        concept=Concept(canonical_name="pin codes serviced"),
        value=FactValue(type=ValueType.NUMBER, amount=18600.0, unit=None),
        time=TimeContext(period_type=PeriodType.CUSTOM, label=None),
        needs_review=True,
        review_reason="Missing unit and ambiguous period",
    )

    is_candidate, reason = CandidateMatcher.is_comparable_candidate(obs_valid, obs_quarantined)
    assert is_candidate is False
    assert reason == "quarantined_needs_review"


def test_candidate_matcher_gates():
    from backend.reconciliation.candidate_matcher import CandidateMatcher

    obs_a = Observation(
        id="a",
        entity=Entity(canonical_name="India"),
        concept=Concept(canonical_name="real gdp growth"),
        value=FactValue(type=ValueType.PERCENTAGE, amount=6.4, unit="%"),
        time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY25"),
        needs_review=False,
    )
    obs_b_diff_concept = Observation(
        id="b",
        entity=Entity(canonical_name="India"),
        concept=Concept(canonical_name="headline cpi inflation"),
        value=FactValue(type=ValueType.PERCENTAGE, amount=5.4, unit="%"),
        time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY25"),
        needs_review=False,
    )

    is_cand, reason = CandidateMatcher.is_comparable_candidate(obs_a, obs_b_diff_concept)
    assert is_cand is False
    assert reason == "concept_mismatch"


def test_repository_grouped_facts_and_search():
    import sqlite3
    from backend.db.database import SCHEMA_SQL
    from backend.db.repository import Repository

    # In-memory database for isolated repository test
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    repo = Repository(db_conn=conn)

    # Save document and chunk
    repo.save_document("doc_test", "test.pdf", dataset="test_set", document_type="prospectus", page_count=5)
    repo.save_chunk("chk_1", "doc_test", 1, "Intro", "paragraph", "Delhivery revenue was 8142 crore.")

    doc_detail = repo.get_document_detail("doc_test")
    assert doc_detail is not None
    assert doc_detail["chunk_count"] == 1

    # Save observation
    obs = Observation(
        id="obs_test_1",
        entity=Entity(canonical_name="Delhivery Limited"),
        concept=Concept(canonical_name="revenue"),
        value=FactValue(type=ValueType.CURRENCY, amount=8142.0, unit="INR crore"),
        time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY24"),
    )
    repo.save_observation(obs)

    # Search
    search_res = repo.search_facts("Delhivery")
    assert len(search_res["entities"]) >= 1
    assert len(search_res["observations"]) >= 1

    # Grouped facts
    grouped = repo.get_grouped_facts()
    assert len(grouped) == 1
    assert grouped[0]["entity_name"] == "Delhivery Limited"
    assert grouped[0]["concept_name"] == "revenue"

    repo.close()


if __name__ == "__main__":
    test_unit_normalization()
    test_percentage_normalization()
    test_temporal_nesting_and_different()
    test_cascade_corroboration()
    test_cascade_status_aware_contextualization()
    test_cascade_genuine_contradiction()
    test_currency_unresolved_without_fx()
    test_candidate_matcher_quarantine()
    test_candidate_matcher_gates()
    test_repository_grouped_facts_and_search()
    print("All unit tests passed successfully!")
