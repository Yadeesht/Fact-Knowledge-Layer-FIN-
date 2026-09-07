import tempfile
from pathlib import Path
import pytest

from backend.models.schema import (
    ResolutionStatus,
    ExtractedObservation,
    ValueType,
    PeriodType,
    Observation,
    Entity,
    Concept,
    FactValue,
    TimeContext,
    Scope,
)
from backend.core.concept_resolver import resolve_concept, _has_divergent_qualifiers
from backend.core.entity_resolver import resolve_entity
from backend.db.database import init_db
from backend.db.repository import Repository
from backend.ingestion.extractor import process_extracted_candidate
from backend.reconciliation.candidate_matcher import CandidateMatcher
from backend.core.canonicalizer import concepts_match, entities_match


@pytest.fixture
def temp_repo():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_knowledge.db"
        init_db(db_path)
        repo = Repository(db_path)
        yield repo
        repo.close()


def test_fast_path_concept_alias():
    res = resolve_concept("sales")
    assert res.status == ResolutionStatus.MATCHED
    assert res.canonical_name == "revenue"
    assert res.matched_via == "alias"

    res_cad = resolve_concept("CAD")
    assert res_cad.status == ResolutionStatus.MATCHED
    assert res_cad.canonical_name == "current account deficit"


def test_fast_path_entity_alias():
    res = resolve_entity("rbi")
    assert res.status == ResolutionStatus.MATCHED
    assert res.canonical_name == "reserve bank of india"
    assert res.matched_via == "alias"

    res_del = resolve_entity("Delhivery Ltd")
    assert res_del.status == ResolutionStatus.MATCHED
    assert res_del.canonical_name == "delhivery limited"


def test_exact_match_in_repository(temp_repo):
    # Pre-populate dynamic repository with a concept and an alias
    c_id = temp_repo.get_or_create_concept(
        canonical_name="gross value added",
        description="Total economic output minus intermediate consumption",
        aliases=["gva", "overall gva"],
    )

    res = resolve_concept("gva", repo=temp_repo)
    assert res.status == ResolutionStatus.MATCHED
    assert res.canonical_name == "gross value added"
    assert res.canonical_concept_id == c_id
    assert res.matched_via == "exact"


def test_anti_conflation_guardrails():
    # Test rate-of-change divergence
    assert _has_divergent_qualifiers("effective capital expenditure growth", "effective capital expenditure") is True
    assert _has_divergent_qualifiers("real gdp growth", "real gdp") is True
    assert _has_divergent_qualifiers("real gdp growth", "economic expansion") is False

    # Test sub-component specificity divergence
    assert _has_divergent_qualifiers("revenue from traded goods", "revenue") is True
    assert _has_divergent_qualifiers("food inflation", "headline cpi inflation") is True
    assert _has_divergent_qualifiers("core cpi inflation", "headline cpi inflation") is True


def test_anti_conflation_in_resolution(temp_repo):
    # Pre-populate DB with parent metric "revenue"
    temp_repo.get_or_create_concept(
        canonical_name="revenue",
        description="Total top-line income",
    )

    # Resolve a sub-component: "revenue from traded goods"
    res = resolve_concept(
        raw_name="revenue from traded goods",
        source_label="Sale of traded goods",
        context="Revenue from traded goods stood at Rs 450 crore.",
        repo=temp_repo,
    )

    # Must NOT silently merge into parent "revenue"
    assert res.canonical_name != "revenue"
    assert res.status == ResolutionStatus.NEW
    assert "traded goods" in res.canonical_name


def test_dynamic_registration_of_new_concept(temp_repo):
    res = resolve_concept(
        raw_name="green hydrogen mission capital outlay",
        source_label="Capital outlay under NGHM",
        context="The outlay for National Green Hydrogen Mission was Rs 19,744 crore.",
        repo=temp_repo,
    )

    assert res.status == ResolutionStatus.NEW
    assert res.canonical_name == "green hydrogen mission capital outlay"
    assert res.canonical_concept_id is not None

    # Verify it was persisted in SQLite concepts table
    all_concepts = temp_repo.list_all_concepts()
    names = [c["canonical_name"] for c in all_concepts]
    assert "green hydrogen mission capital outlay" in names


def test_extractor_candidate_processing_with_dynamic_resolver(temp_repo):
    candidate = ExtractedObservation(
        entity_name="RBI",
        entity_type="central_bank",
        concept_name="sales",
        source_label="Total sales",
        value_type=ValueType.CURRENCY,
        value=12000.0,
        unit="INR crore",
        period_label="FY2024",
        period_type=PeriodType.FISCAL_YEAR,
        evidence_quote="RBI total sales reached INR 12,000 crore in FY2024.",
        page_number=12,
        chunk_id="chk_01",
        confidence=0.98,
    )

    obs = process_extracted_candidate(
        candidate=candidate,
        document_id="doc_test",
        chunk_id="chk_01",
        grounding_reasons=None,
        repo=temp_repo,
    )

    assert obs.entity.canonical_name == "reserve bank of india"
    assert obs.concept.canonical_name == "revenue"
    assert obs.needs_review is False


def test_reconciliation_matcher_concept_isolation():
    # Two observations with different registered concept IDs must NOT match
    obs_a = Observation(
        id="obs_a",
        entity=Entity(id="ent_01", canonical_name="delhivery limited"),
        concept=Concept(id="cpt_revenue", canonical_name="revenue"),
        value=FactValue(type=ValueType.CURRENCY, amount=100.0, unit="INR crore", normalized_amount=100.0, normalized_unit="INR crore"),
        time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY2024"),
        scope=Scope(),
        evidence=[],
    )

    obs_b = Observation(
        id="obs_b",
        entity=Entity(id="ent_01", canonical_name="delhivery limited"),
        concept=Concept(id="cpt_revenue_from_traded_goods", canonical_name="revenue from traded goods"),
        value=FactValue(type=ValueType.CURRENCY, amount=20.0, unit="INR crore", normalized_amount=20.0, normalized_unit="INR crore"),
        time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY2024"),
        scope=Scope(),
        evidence=[],
    )

    # Concepts should not match because IDs are different
    assert concepts_match(obs_a, obs_b) is False

    # Candidate matcher should flag concept mismatch
    is_comparable, reason = CandidateMatcher.is_comparable_candidate(obs_a, obs_b)
    assert is_comparable is False
    assert reason == "concept_mismatch"
