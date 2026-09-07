"""
Regression Tests for Grounding Validator
=========================================
Test cases derived from observed extraction failures in RBI Article IV output.
Each test represents a real error pattern that the validator must catch.
"""

import pytest
from backend.core.grounding_validator import (
    validate_grounding,
    _check_value_in_evidence,
    _check_period_in_evidence,
    _check_concept_plausibility,
    _check_assertion_status,
    _check_metadata_value,
)
from backend.models.schema import ExtractedObservation, Scope


# ──────────────────────────────────────────────────
# Test 1: Wrong evidence — value extracted from metadata
# ──────────────────────────────────────────────────
# Observation: Revenue = 31 crore
# Evidence: "Income Statement for the year ended March 31, 2025"
# Expected: NEEDS_REVIEW (value 31 comes from the date, not financial data)

class TestWrongEvidence:
    def _make_obs(self):
        return ExtractedObservation(
            entity_name="Delhivery Limited",
            entity_type="company",
            concept_name="Revenue from operations",
            source_label="Revenue from operations",
            value_type="currency",
            value=31,
            unit="INR crore",
            period_label="FY25",
            period_type="fiscal_year",
            scope=Scope(geography="India", level="company", consolidation="consolidated"),
            assertion_status="actual",
            evidence_quote="Income Statement for the year ended March 31, 2025",
            page_number=1,
            confidence=0.90,
        )

    def test_value_not_in_evidence_as_financial_data(self):
        obs = self._make_obs()
        passed, reasons = validate_grounding(obs)
        assert not passed, "Should flag: '31' comes from date in metadata header"
        assert any("metadata" in r.lower() or "value not found" in r.lower() for r in reasons)

    def test_metadata_check_catches_header(self):
        obs = self._make_obs()
        passed, reason = _check_metadata_value(obs)
        assert not passed, "Should detect metadata header"
        assert "metadata" in reason.lower()


# ──────────────────────────────────────────────────
# Test 2: Wrong concept — LLM broadened the metric
# ──────────────────────────────────────────────────
# Evidence: "services sector growth = 7.5%"
# Extracted: concept "real GDP growth", value 7.5%
# Expected: NEEDS_REVIEW

class TestWrongConcept:
    def _make_obs(self):
        return ExtractedObservation(
            entity_name="India",
            entity_type="country",
            concept_name="real GDP growth",
            source_label="services sector growth",
            value_type="percentage",
            value=7.5,
            unit="%",
            period_label="FY25",
            period_type="fiscal_year",
            scope=Scope(geography="India", level="economy", consolidation="unknown"),
            assertion_status="actual",
            evidence_quote="The services sector growth was 7.5 per cent in FY25",
            page_number=24,
            confidence=0.92,
        )

    def test_concept_broadened(self):
        obs = self._make_obs()
        passed, reasons = validate_grounding(obs)
        assert not passed, "Should flag: services sector growth ≠ real GDP growth"
        assert any("concept" in r.lower() for r in reasons)

    def test_concept_plausibility_catches_broadening(self):
        obs = self._make_obs()
        passed, reason = _check_concept_plausibility(obs)
        assert not passed
        assert "concept" in reason.lower()


# ──────────────────────────────────────────────────
# Test 3: Lost quarter — Q1:2024-25 coerced to FY25
# ──────────────────────────────────────────────────
# Evidence: "Q1:2024-25"
# Extracted: period_label "FY25"
# Expected: NEEDS_REVIEW

class TestLostQuarter:
    def _make_obs(self):
        return ExtractedObservation(
            entity_name="India",
            entity_type="country",
            concept_name="real GDP growth",
            source_label="real GDP growth",
            value_type="percentage",
            value=6.7,
            unit="%",
            period_label="FY25",
            period_type="fiscal_year",
            scope=Scope(geography="India", level="economy", consolidation="unknown"),
            assertion_status="actual",
            evidence_quote="Real GDP growth in Q1:2024-25 was 6.7 per cent",
            page_number=15,
            confidence=0.88,
        )

    def test_period_lost_quarter(self):
        obs = self._make_obs()
        passed, reasons = validate_grounding(obs)
        # The evidence says "Q1:2024-25" but period_label is "FY25"
        # The period check should fail because "FY25" is not in the evidence
        # but "Q1:2024-25" is — the LLM coerced the period.
        assert not passed, "Should flag: evidence says Q1:2024-25, not FY25"
        assert any("period" in r.lower() for r in reasons)


# ──────────────────────────────────────────────────
# Test 4: Historical period — "2000-19" coerced to "FY19"
# ──────────────────────────────────────────────────
# Evidence: "2000-19"
# Extracted: period_label "FY19"
# Expected: NEEDS_REVIEW

class TestHistoricalPeriod:
    def _make_obs(self):
        return ExtractedObservation(
            entity_name="India",
            entity_type="country",
            concept_name="average GDP growth",
            source_label="average GDP growth",
            value_type="percentage",
            value=6.3,
            unit="%",
            period_label="FY19",
            period_type="fiscal_year",
            scope=Scope(geography="India", level="economy", consolidation="unknown"),
            assertion_status="actual",
            evidence_quote="India's average GDP growth during 2000-19 was 6.3 per cent",
            page_number=10,
            confidence=0.85,
        )

    def test_period_historical_range_coerced(self):
        obs = self._make_obs()
        passed, reasons = validate_grounding(obs)
        # Evidence says "2000-19" (a 19-year range), but period_label is "FY19" (one year)
        # The period validator should catch this discrepancy
        assert not passed, "Should flag: evidence says 2000-19 range, not FY19"
        assert any("period" in r.lower() for r in reasons)


# ──────────────────────────────────────────────────
# Test 5: Forecast incorrectly marked actual
# ──────────────────────────────────────────────────
# Evidence: "growth is likely to remain robust"
# Extracted: assertion_status "actual"
# Expected: NEEDS_REVIEW

class TestForecastMarkedActual:
    def _make_obs(self):
        return ExtractedObservation(
            entity_name="India",
            entity_type="country",
            concept_name="real GDP growth",
            source_label="real GDP growth",
            value_type="percentage",
            value=6.5,
            unit="%",
            period_label="FY26",
            period_type="fiscal_year",
            scope=Scope(geography="India", level="economy", consolidation="unknown"),
            assertion_status="actual",
            evidence_quote="Real GDP growth is likely to remain at 6.5 per cent in FY26",
            page_number=30,
            confidence=0.90,
        )

    def test_forecast_flagged(self):
        obs = self._make_obs()
        passed, reasons = validate_grounding(obs)
        assert not passed, "Should flag: 'likely to' contradicts assertion_status 'actual'"
        assert any("assertion" in r.lower() for r in reasons)

    def test_assertion_check_catches_likely(self):
        obs = self._make_obs()
        passed, reason = _check_assertion_status(obs)
        assert not passed
        assert "likely to" in reason.lower()


# ──────────────────────────────────────────────────
# Positive tests — well-grounded observations should PASS
# ──────────────────────────────────────────────────

class TestWellGroundedObservations:
    def test_proper_currency_observation(self):
        obs = ExtractedObservation(
            entity_name="Delhivery Limited",
            entity_type="company",
            concept_name="Revenue from operations",
            source_label="Revenue from contracts with customers",
            value_type="currency",
            value=8142.16,
            unit="INR crore",
            period_label="FY24",
            period_type="fiscal_year",
            scope=Scope(geography="India", level="company", consolidation="consolidated"),
            assertion_status="actual",
            evidence_quote="Revenue from contracts with customers 8,142.16",
            page_number=21,
            confidence=0.98,
        )
        passed, reasons = validate_grounding(obs)
        assert passed, f"Well-grounded observation should pass, got reasons: {reasons}"

    def test_proper_percentage_observation(self):
        obs = ExtractedObservation(
            entity_name="India",
            entity_type="country",
            concept_name="real GDP growth",
            source_label="Real GDP growth",
            value_type="percentage",
            value=6.4,
            unit="%",
            period_label="FY24",
            period_type="fiscal_year",
            scope=Scope(geography="India", level="economy", consolidation="unknown"),
            assertion_status="actual",
            evidence_quote="Real GDP growth for FY24 was 6.4 per cent",
            page_number=24,
            confidence=0.95,
        )
        passed, reasons = validate_grounding(obs)
        assert passed, f"Well-grounded observation should pass, got reasons: {reasons}"

    def test_proper_estimate_observation(self):
        obs = ExtractedObservation(
            entity_name="India",
            entity_type="country",
            concept_name="real GDP growth",
            source_label="Real GDP growth",
            value_type="percentage",
            value=6.5,
            unit="%",
            period_label="FY25",
            period_type="fiscal_year",
            scope=Scope(geography="India", level="economy", consolidation="unknown"),
            assertion_status="estimate",
            evidence_quote="As per the advance estimate, real GDP growth for FY25 is 6.5 per cent",
            page_number=5,
            confidence=0.92,
        )
        passed, reasons = validate_grounding(obs)
        assert passed, f"Estimate observation should pass, got reasons: {reasons}"

    def test_comma_formatted_value_passes(self):
        """Value 81,42,163 in Indian format should be found in evidence."""
        obs = ExtractedObservation(
            entity_name="Delhivery Limited",
            entity_type="company",
            concept_name="Total income",
            source_label="Total income",
            value_type="currency",
            value=8142163,
            unit="INR thousand",
            period_label="FY24",
            period_type="fiscal_year",
            scope=Scope(geography="India", level="company", consolidation="consolidated"),
            assertion_status="actual",
            evidence_quote="Total income 81,42,163 thousands",
            page_number=45,
            confidence=0.95,
        )
        passed, reasons = validate_grounding(obs)
        assert passed, f"Indian comma format should be recognized, got reasons: {reasons}"

    def test_text_value_observation_passes(self):
        """Text-type observations skip value-in-evidence check."""
        obs = ExtractedObservation(
            entity_name="Reserve Bank of India",
            entity_type="central_bank",
            concept_name="monetary policy stance",
            source_label="Monetary policy stance",
            value_type="text",
            value="neutral",
            unit=None,
            period_label="FY25",
            period_type="fiscal_year",
            scope=Scope(geography="India", level="economy", consolidation="unknown"),
            assertion_status="actual",
            evidence_quote="The RBI maintained a neutral monetary policy stance in FY25",
            page_number=12,
            confidence=0.90,
        )
        passed, reasons = validate_grounding(obs)
        assert passed, f"Text observation should pass, got reasons: {reasons}"
