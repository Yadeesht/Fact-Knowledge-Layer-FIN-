import json
from datetime import datetime
from backend.models.schema import (
    Observation,
    Evidence,
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
from backend.db.repository import Repository
from backend.reconciliation.cascade import reconcile_deterministically


def populate_seed_data(repo: Repository):
    print("Populating starter documents and seed knowledge...")

    # 1. Documents
    docs = [
        # Delhivery
        (
            "doc_delhivery_prospectus",
            "01-delhivery-prospectus-2022-excerpt.pdf",
            "delhivery",
            "prospectus",
            100,
        ),
        (
            "doc_delhivery_annual_report_fy24",
            "02-delhivery-annual-report-fy24-excerpt.pdf",
            "delhivery",
            "annual_report",
            100,
        ),
        (
            "doc_delhivery_q4_fy24_presentation",
            "03-delhivery-q4-fy24-earnings-presentation.pdf",
            "delhivery",
            "earnings_presentation",
            27,
        ),
        # India Macroeconomy
        (
            "doc_economic_survey_24_25",
            "01-india-economic-survey-2024-25-excerpt.pdf",
            "india-macroeconomy",
            "economic_survey",
            89,
        ),
        (
            "doc_rbi_annual_report_24_25",
            "02-rbi-annual-report-2024-25-excerpt.pdf",
            "india-macroeconomy",
            "central_bank_report",
            100,
        ),
        (
            "doc_imf_india_2025_article_iv",
            "03-imf-india-2025-article-iv-excerpt.pdf",
            "india-macroeconomy",
            "imf_consultation",
            95,
        ),
    ]

    for doc_id, filename, dataset, doc_type, pages in docs:
        repo.save_document(doc_id, filename, dataset, doc_type, pages)

    # 2. Observations
    observations = [
        # -------------------------------------------------------------
        # CASE 1: CORROBORATION (Delhivery FY24 Revenue in Cr vs Mn)
        # -------------------------------------------------------------
        Observation(
            id="obs_del_rev_fy24_ar",
            entity=Entity(
                canonical_name="Delhivery Limited",
                entity_type="company",
                aliases=["Delhivery", "Delhivery Ltd"],
            ),
            concept=Concept(
                canonical_name="revenue from operations",
                source_label="Revenue from operations",
                definition="Total revenue generated from contract logistics, express parcel, and freight services.",
            ),
            value=FactValue(
                type=ValueType.CURRENCY,
                amount=8142.16,
                unit="INR crore",
                normalized_amount=81421600000.0,
                normalized_unit="INR",
            ),
            time=TimeContext(
                period_type=PeriodType.FISCAL_YEAR,
                label="FY24",
            ),
            scope=Scope(
                geography="India",
                level="company",
                consolidation="consolidated",
            ),
            assertion_status=AssertionStatus.ACTUAL,
            confidence=0.99,
            needs_review=False,
            evidence=[
                Evidence(
                    document_id="doc_delhivery_annual_report_fy24",
                    page_number=110,
                    section="Consolidated Statement of Profit and Loss",
                    quote="Revenue from operations for the financial year ended March 31, 2024 stood at ₹8,142.16 Crores compared to ₹7,225.30 Crores in the previous year.",
                )
            ],
        ),
        Observation(
            id="obs_del_rev_fy24_pres",
            entity=Entity(
                canonical_name="Delhivery Limited",
                entity_type="company",
                aliases=["Delhivery"],
            ),
            concept=Concept(
                canonical_name="revenue from operations",
                source_label="Revenue from operations",
            ),
            value=FactValue(
                type=ValueType.CURRENCY,
                amount=81424.0,
                unit="INR million",
                normalized_amount=81424000000.0,
                normalized_unit="INR",
            ),
            time=TimeContext(
                period_type=PeriodType.FISCAL_YEAR,
                label="FY24",
            ),
            scope=Scope(
                geography="India",
                level="company",
                consolidation="consolidated",
            ),
            assertion_status=AssertionStatus.ACTUAL,
            confidence=0.98,
            needs_review=False,
            evidence=[
                Evidence(
                    document_id="doc_delhivery_q4_fy24_presentation",
                    page_number=7,
                    section="FY24 Financial & Operational Highlights",
                    quote="FY24 Revenue from Operations reached ₹81,424 Mn, demonstrating strong 13% YoY growth driven by Express Parcel and PTL volumes.",
                )
            ],
        ),
        # -------------------------------------------------------------
        # CASE 2: CONTRADICTION (India FY25 Real GDP Growth conflicting actuals)
        # -------------------------------------------------------------
        Observation(
            id="obs_ind_gdp_fy25_survey",
            entity=Entity(
                canonical_name="India",
                entity_type="country",
                aliases=["Indian Economy", "Republic of India"],
            ),
            concept=Concept(
                canonical_name="real gdp growth",
                source_label="Real GDP Growth",
                definition="Annual percentage growth rate of Gross Domestic Product at constant prices.",
            ),
            value=FactValue(
                type=ValueType.PERCENTAGE,
                amount=6.4,
                unit="%",
                normalized_amount=6.4,
                normalized_unit="%",
            ),
            time=TimeContext(
                period_type=PeriodType.FISCAL_YEAR,
                label="FY25",
            ),
            scope=Scope(
                geography="India",
                level="economy",
                consolidation="consolidated",
            ),
            assertion_status=AssertionStatus.ACTUAL,
            confidence=0.96,
            needs_review=False,
            evidence=[
                Evidence(
                    document_id="doc_economic_survey_24_25",
                    page_number=48,
                    section="State of the Economy",
                    quote="India's real GDP growth for FY25 is recorded at 6.4 per cent, supported by domestic investment and resilient manufacturing momentum.",
                )
            ],
        ),
        Observation(
            id="obs_ind_gdp_fy25_conflict",
            entity=Entity(
                canonical_name="India",
                entity_type="country",
                aliases=["India"],
            ),
            concept=Concept(
                canonical_name="real gdp growth",
                source_label="Real GDP Growth Rate",
            ),
            value=FactValue(
                type=ValueType.PERCENTAGE,
                amount=7.2,
                unit="%",
                normalized_amount=7.2,
                normalized_unit="%",
            ),
            time=TimeContext(
                period_type=PeriodType.FISCAL_YEAR,
                label="FY25",
            ),
            scope=Scope(
                geography="India",
                level="economy",
                consolidation="consolidated",
            ),
            assertion_status=AssertionStatus.ACTUAL,
            confidence=0.94,
            needs_review=False,
            evidence=[
                Evidence(
                    document_id="doc_rbi_annual_report_24_25",
                    page_number=31,
                    section="Macroeconomic Appendix - Table 1.1",
                    quote="Real GDP growth rate for FY25 stands confirmed at 7.2 per cent based on provisional national accounts revisions.",
                )
            ],
        ),
        # -------------------------------------------------------------
        # CASE 3: CONTEXTUALIZED (Estimate vs Forecast vs Different Periods)
        # -------------------------------------------------------------
        Observation(
            id="obs_ind_gdp_fy25_imf_forecast",
            entity=Entity(
                canonical_name="India",
                entity_type="country",
                aliases=["India"],
            ),
            concept=Concept(
                canonical_name="real gdp growth",
                source_label="Real GDP Growth Projection",
            ),
            value=FactValue(
                type=ValueType.PERCENTAGE,
                amount=6.5,
                unit="%",
                normalized_amount=6.5,
                normalized_unit="%",
            ),
            time=TimeContext(
                period_type=PeriodType.FISCAL_YEAR,
                label="FY25",
            ),
            scope=Scope(
                geography="India",
                level="economy",
                consolidation="consolidated",
            ),
            assertion_status=AssertionStatus.FORECAST,
            confidence=0.95,
            needs_review=False,
            evidence=[
                Evidence(
                    document_id="doc_imf_india_2025_article_iv",
                    page_number=14,
                    section="Executive Summary & Staff Report",
                    quote="Staff projects real GDP growth of 6.5 percent in FY2024/25, slightly moderating from the previous year as cyclical tailwinds normalize.",
                )
            ],
        ),
        Observation(
            id="obs_ind_gdp_fy25_survey_est",
            entity=Entity(
                canonical_name="India",
                entity_type="country",
                aliases=["India"],
            ),
            concept=Concept(
                canonical_name="real gdp growth",
                source_label="Real GDP Growth Estimate",
            ),
            value=FactValue(
                type=ValueType.PERCENTAGE,
                amount=6.4,
                unit="%",
                normalized_amount=6.4,
                normalized_unit="%",
            ),
            time=TimeContext(
                period_type=PeriodType.FISCAL_YEAR,
                label="FY25",
            ),
            scope=Scope(
                geography="India",
                level="economy",
                consolidation="consolidated",
            ),
            assertion_status=AssertionStatus.ESTIMATE,
            confidence=0.96,
            needs_review=False,
            evidence=[
                Evidence(
                    document_id="doc_economic_survey_24_25",
                    page_number=49,
                    section="Macroeconomic Outlook",
                    quote="India's real GDP is estimated to grow by 6.4 per cent in FY25 in the baseline scenario outlined in the survey.",
                )
            ],
        ),
        # Contextualized by Period (FY24 vs H1 FY24)
        Observation(
            id="obs_del_rev_h1_fy24",
            entity=Entity(
                canonical_name="Delhivery Limited",
                entity_type="company",
            ),
            concept=Concept(
                canonical_name="revenue from operations",
                source_label="Revenue from operations",
            ),
            value=FactValue(
                type=ValueType.CURRENCY,
                amount=3875.5,
                unit="INR crore",
                normalized_amount=38755000000.0,
                normalized_unit="INR",
            ),
            time=TimeContext(
                period_type=PeriodType.HALF_YEAR,
                label="H1 FY24",
            ),
            scope=Scope(
                geography="India",
                level="company",
                consolidation="consolidated",
            ),
            assertion_status=AssertionStatus.ACTUAL,
            confidence=0.97,
            needs_review=False,
            evidence=[
                Evidence(
                    document_id="doc_delhivery_annual_report_fy24",
                    page_number=42,
                    section="Management Discussion & Analysis",
                    quote="In H1 FY24, consolidated revenue from operations was ₹3,875.50 Cr, showing seasonal acceleration heading into festive quarters.",
                )
            ],
        ),
        # -------------------------------------------------------------
        # CASE 4: EXTRACTION FAILURE / NEEDS REVIEW
        # -------------------------------------------------------------
        Observation(
            id="obs_del_pincodes_ambiguous",
            entity=Entity(
                canonical_name="Delhivery Limited",
                entity_type="company",
            ),
            concept=Concept(
                canonical_name="pin codes serviced",
                source_label="Network reach",
                definition="Total unique postal PIN codes reachable across India.",
            ),
            value=FactValue(
                type=ValueType.NUMBER,
                amount=18600.0,
                unit=None,  # Missing unit
                normalized_amount=18600.0,
                normalized_unit="unit",
            ),
            time=TimeContext(
                period_type=PeriodType.CUSTOM,
                label=None,  # Ambiguous timeframe
            ),
            scope=Scope(
                geography="India",
                level="company",
            ),
            assertion_status=AssertionStatus.UNKNOWN,
            confidence=0.58,
            needs_review=True,
            review_reason="Missing explicit time period anchor and ungrounded unit qualification in footnote extraction.",
            evidence=[
                Evidence(
                    document_id="doc_delhivery_prospectus",
                    page_number=28,
                    section="Prospectus Summary - Footnote 3",
                    quote="Coverage expanded across more than 18,600 pincodes as of recent operational updates (exact verification date omitted in table subtext).",
                )
            ],
        ),
        # -------------------------------------------------------------
        # ADDITIONAL REALISTIC OBSERVATIONS (Inflation, Parcel Volumes, etc.)
        # -------------------------------------------------------------
        Observation(
            id="obs_ind_cpi_survey_fy24",
            entity=Entity(canonical_name="India", entity_type="country"),
            concept=Concept(canonical_name="headline cpi inflation"),
            value=FactValue(
                type=ValueType.PERCENTAGE,
                amount=5.4,
                unit="%",
                normalized_amount=5.4,
                normalized_unit="%",
            ),
            time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY24"),
            scope=Scope(geography="India", level="economy"),
            assertion_status=AssertionStatus.ACTUAL,
            confidence=0.98,
            needs_review=False,
            evidence=[
                Evidence(
                    document_id="doc_economic_survey_24_25",
                    page_number=132,
                    section="Prices and Inflation",
                    quote="Headline consumer price inflation averaged 5.4 per cent in FY24, anchored within the target tolerance band despite food price shocks.",
                )
            ],
        ),
        Observation(
            id="obs_ind_cpi_rbi_fy24",
            entity=Entity(canonical_name="India", entity_type="country"),
            concept=Concept(canonical_name="headline cpi inflation"),
            value=FactValue(
                type=ValueType.PERCENTAGE,
                amount=5.4,
                unit="%",
                normalized_amount=5.4,
                normalized_unit="%",
            ),
            time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY24"),
            scope=Scope(geography="India", level="economy"),
            assertion_status=AssertionStatus.ACTUAL,
            confidence=0.99,
            needs_review=False,
            evidence=[
                Evidence(
                    document_id="doc_rbi_annual_report_24_25",
                    page_number=45,
                    section="Price Situation and Monetary Policy Review",
                    quote="Average CPI inflation for 2023-24 (FY24) stood at 5.4 per cent, declining from 6.7 per cent in 2022-23.",
                )
            ],
        ),
        Observation(
            id="obs_del_parcel_vol_fy24_ar",
            entity=Entity(canonical_name="Delhivery Limited", entity_type="company"),
            concept=Concept(canonical_name="express parcel volume"),
            value=FactValue(
                type=ValueType.NUMBER,
                amount=740.0,
                unit="million",
                normalized_amount=740000000.0,
                normalized_unit="unit",
            ),
            time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY24"),
            scope=Scope(geography="India", level="company"),
            assertion_status=AssertionStatus.ACTUAL,
            confidence=0.98,
            needs_review=False,
            evidence=[
                Evidence(
                    document_id="doc_delhivery_annual_report_fy24",
                    page_number=24,
                    section="Key Operating Metrics",
                    quote="Express parcel shipment volumes grew to 740 million parcels in FY24, up 11% compared to 663 million parcels in FY23.",
                )
            ],
        ),
        Observation(
            id="obs_del_parcel_vol_fy24_pres",
            entity=Entity(canonical_name="Delhivery Limited", entity_type="company"),
            concept=Concept(canonical_name="express parcel volume"),
            value=FactValue(
                type=ValueType.NUMBER,
                amount=74.0,
                unit="crore",
                normalized_amount=740000000.0,
                normalized_unit="unit",
            ),
            time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY24"),
            scope=Scope(geography="India", level="company"),
            assertion_status=AssertionStatus.ACTUAL,
            confidence=0.98,
            needs_review=False,
            evidence=[
                Evidence(
                    document_id="doc_delhivery_q4_fy24_presentation",
                    page_number=11,
                    section="Operational Volume Trends",
                    quote="Express parcel volumes reached 74 crore shipments in FY24, reinforcing market leadership across e-commerce fulfillment.",
                )
            ],
        ),
        # Concept boundary distinction: Revenue from Operations vs Revenue from Traded Goods
        Observation(
            id="obs_del_traded_goods_fy24",
            entity=Entity(canonical_name="Delhivery Limited", entity_type="company"),
            concept=Concept(
                canonical_name="revenue from traded goods",
                source_label="Sale of traded goods",
                definition="Proceeds specifically from trading activities, distinct from primary logistics services.",
            ),
            value=FactValue(
                type=ValueType.CURRENCY,
                amount=14.8,
                unit="INR crore",
                normalized_amount=148000000.0,
                normalized_unit="INR",
            ),
            time=TimeContext(period_type=PeriodType.FISCAL_YEAR, label="FY24"),
            scope=Scope(geography="India", level="company"),
            assertion_status=AssertionStatus.ACTUAL,
            confidence=0.97,
            needs_review=False,
            evidence=[
                Evidence(
                    document_id="doc_delhivery_annual_report_fy24",
                    page_number=112,
                    section="Notes to Consolidated Accounts - Note 28",
                    quote="Revenue from traded goods accounted for ₹14.80 Crores in FY24 (FY23: ₹18.20 Crores), representing a minor auxiliary line item.",
                )
            ],
        ),
    ]

    for obs in observations:
        repo.save_observation(obs)

    # 3. Compute and store relationships through the deterministic cascade
    print("Computing candidate pairs through deterministic cascade...")
    obs_list = repo.list_observations()
    reconciled_count = 0

    for i in range(len(obs_list)):
        for j in range(i + 1, len(obs_list)):
            a = obs_list[i]
            b = obs_list[j]
            rel = reconcile_deterministically(a, b)
            if rel:
                repo.save_relationship(rel)
                reconciled_count += 1

    # 4. Processing runs log (demonstrating end-to-end pipeline observability)
    runs = [
        {
            "id": "run_delhivery_ingest_001",
            "document_id": "doc_delhivery_annual_report_fy24",
            "status": "COMPLETED",
            "extractor_model": "gemini-1.5-pro",
            "metrics": {
                "pdf_pages_parsed": 100,
                "chunks_created": 47,
                "observations_extracted": 14,
                "observations_normalized": 14,
                "needs_review": 0,
                "relationships_generated": 6,
            },
            "started_at": "2026-09-07T20:10:00Z",
            "completed_at": "2026-09-07T20:10:42Z",
        },
        {
            "id": "run_macro_ingest_002",
            "document_id": "doc_economic_survey_24_25",
            "status": "COMPLETED",
            "extractor_model": "gemini-1.5-pro",
            "metrics": {
                "pdf_pages_parsed": 89,
                "chunks_created": 41,
                "observations_extracted": 18,
                "observations_normalized": 17,
                "needs_review": 1,
                "relationships_generated": 8,
            },
            "started_at": "2026-09-07T20:12:00Z",
            "completed_at": "2026-09-07T20:12:38Z",
        },
    ]

    for r in runs:
        repo.log_processing_run(
            run_id=r["id"],
            document_id=r["document_id"],
            status=r["status"],
            extractor_model=r["extractor_model"],
            metrics=r["metrics"],
            started_at=r["started_at"],
            completed_at=r["completed_at"],
        )

    print(f"Seed data initialized: {len(observations)} observations, {reconciled_count} relationships.")
