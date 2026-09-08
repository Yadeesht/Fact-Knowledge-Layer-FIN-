import hashlib
import sqlite3
import pytest
from backend.db.database import SCHEMA_SQL
from backend.db.repository import Repository
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
    Relationship,
    RelationshipType,
)


def test_document_sha256_caching():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    repo = Repository(db_conn=conn)

    sample_bytes = b"%PDF-1.4 Fake PDF Content for Test"
    file_hash = hashlib.sha256(sample_bytes).hexdigest()

    # First save
    repo.save_document(
        doc_id="doc_test_1",
        filename="test_report.pdf",
        dataset="test",
        document_type="annual_report",
        page_count=10,
        file_hash=file_hash,
        file_size=len(sample_bytes),
    )

    # Check cache hit by hash
    cached = repo.get_document_by_hash(file_hash)
    assert cached is not None
    assert cached["id"] == "doc_test_1"
    assert cached["filename"] == "test_report.pdf"
    assert cached["file_size"] == len(sample_bytes)

    # Different bytes -> cache miss
    diff_hash = hashlib.sha256(b"Different content").hexdigest()
    assert repo.get_document_by_hash(diff_hash) is None

    repo.close()


def test_relational_observation_linking():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    repo = Repository(db_conn=conn)

    repo.save_document("doc_rbi", "rbi.pdf", dataset="macro", document_type="report", page_count=5)
    repo.save_document("doc_imf", "imf.pdf", dataset="macro", document_type="report", page_count=5)

    obs_rbi = Observation(
        id="obs_rbi_gdp",
        entity=Entity(canonical_name="India"),
        concept=Concept(canonical_name="real gdp growth"),
        value=FactValue(type=ValueType.PERCENTAGE, amount=6.5, unit="%"),
        time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY25"),
        assertion_status=AssertionStatus.ACTUAL,
    )
    obs_imf = Observation(
        id="obs_imf_gdp",
        entity=Entity(canonical_name="India"),
        concept=Concept(canonical_name="real gdp growth"),
        value=FactValue(type=ValueType.PERCENTAGE, amount=6.5, unit="%"),
        time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY25"),
        assertion_status=AssertionStatus.ACTUAL,
    )

    # Save with direct document_id
    repo.save_observation(obs_rbi, document_id="doc_rbi")
    repo.save_observation(obs_imf, document_id="doc_imf")

    # Filter observations by document_id
    rbi_facts = repo.list_observations(document_id="doc_rbi")
    assert len(rbi_facts) == 1
    assert rbi_facts[0].id == "obs_rbi_gdp"

    imf_facts = repo.list_observations(document_id="doc_imf")
    assert len(imf_facts) == 1
    assert imf_facts[0].id == "obs_imf_gdp"

    repo.close()


def test_scoped_comparison_candidates():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    repo = Repository(db_conn=conn)

    repo.save_document("doc_new", "new_excerpt.pdf")
    repo.save_document("doc_baseline", "imf_baseline.pdf")
    repo.save_document("doc_unrelated", "delhivery.pdf")

    obs_new = Observation(
        id="obs_1",
        entity=Entity(canonical_name="India"),
        concept=Concept(canonical_name="real gdp growth"),
        value=FactValue(type=ValueType.PERCENTAGE, amount=6.5, unit="%"),
        time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY25"),
    )
    obs_base = Observation(
        id="obs_2",
        entity=Entity(canonical_name="India"),
        concept=Concept(canonical_name="real gdp growth"),
        value=FactValue(type=ValueType.PERCENTAGE, amount=6.4, unit="%"),
        time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY25"),
    )
    obs_unrelated = Observation(
        id="obs_3",
        entity=Entity(canonical_name="Delhivery Limited"),
        concept=Concept(canonical_name="revenue from operations"),
        value=FactValue(type=ValueType.CURRENCY, amount=8142.0, unit="INR crore"),
        time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY24"),
    )

    repo.save_observation(obs_new, document_id="doc_new")
    repo.save_observation(obs_base, document_id="doc_baseline")
    repo.save_observation(obs_unrelated, document_id="doc_unrelated")

    # In cross-document mode, doc_new should NOT compare against itself
    candidates = repo.get_comparison_candidates(current_doc_id="doc_new", comparison_mode="cross_document")
    cand_ids = {c.id for c in candidates}
    assert "obs_1" not in cand_ids
    assert "obs_2" in cand_ids
    assert "obs_3" in cand_ids

    # With targeted document scope, only doc_baseline is returned
    targeted = repo.get_comparison_candidates(
        current_doc_id="doc_new",
        comparison_mode="cross_document",
        target_doc_ids=["doc_baseline"],
    )
    assert len(targeted) == 1
    assert targeted[0].id == "obs_2"

    repo.close()


def test_analysis_session_crud():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    repo = Repository(db_conn=conn)

    sess = repo.create_session(
        session_id="sess_macro_01",
        name="RBI vs IMF Comparison",
        document_ids=["doc_rbi", "doc_imf"],
        comparison_mode="cross_document",
        baseline_doc_ids=["doc_imf"],
        description="Comparing FY25 GDP projections",
    )
    assert sess is not None
    assert sess["id"] == "sess_macro_01"
    assert sess["document_ids"] == ["doc_rbi", "doc_imf"]
    assert sess["baseline_doc_ids"] == ["doc_imf"]

    all_sessions = repo.list_sessions()
    assert len(all_sessions) == 1
    assert all_sessions[0]["name"] == "RBI vs IMF Comparison"

    repo.close()


if __name__ == "__main__":
    test_document_sha256_caching()
    test_relational_observation_linking()
    test_scoped_comparison_candidates()
    test_analysis_session_crud()
    print("All session and caching tests passed successfully!")
