"""
Grounding Validator
===================
Post-LLM validation stage that checks each candidate observation
against its evidence quote before acceptance.

Architecture:
    LLM → candidate observation → GROUNDING VALIDATION → normalization → accepted observation

Each check returns (passed: bool, reason: str | None).
Observations that fail any check get needs_review=True with specific reasons,
but are NOT discarded — they go to the human review queue.
"""

import re
from typing import List, Tuple, Optional
from backend.models.schema import ExtractedObservation


# ──────────────────────────────────────────────────
# Individual grounding checks
# ──────────────────────────────────────────────────

def _normalize_minus_signs(text: str) -> str:
    """Normalizes various typographic minuses and parenthesized minuses to a standard hyphen-minus."""
    # Parenthesized minuses: e.g. "(-) 2.5", "(- )2.5", "(-)" -> "-"
    text = re.sub(r"\(\s*-\s*\)\s*", "-", text)
    # Unicode minuses: en-dash, em-dash, minus sign (\u2212, \u2013, \u2014)
    text = re.sub(r"[\u2212\u2013\u2014]", "-", text)
    # Spaced minuses before digits: "- 2.5" -> "-2.5"
    text = re.sub(r"-\s+(?=\d)", "-", text)
    return text


def _check_value_in_evidence(obs: ExtractedObservation) -> Tuple[bool, Optional[str]]:
    """
    Check 1: The evidence quote should contain the numeric value.
    Tolerates comma-formatting differences (e.g. 8,142.16 vs 8142.16)
    and semantic negation (e.g. "contracting by 1.0%" -> -1.0%, "deflation of 2.5%" -> -2.5%,
    "(-) 2.5%" -> -2.5%).
    """
    if obs.value_type == "text":
        # Text observations don't have numeric values to verify
        return True, None

    quote = (obs.evidence_quote or "").strip()
    if not quote:
        return False, "Evidence quote is empty"

    try:
        num_val = float(obs.value)
    except (TypeError, ValueError):
        return True, None  # Non-numeric value, skip this check

    # Normalize typographical/parenthesized minuses in quote
    quote_clean = _normalize_minus_signs(quote.replace("\n", " ").replace("\r", " "))

    # Build candidate string representations of the number
    candidates = set()

    # Raw number as string (e.g. "8142.16")
    if num_val == int(num_val):
        candidates.add(str(int(num_val)))
    candidates.add(str(num_val))

    # Remove trailing zeros for decimals (e.g. "6.40" → "6.4")
    formatted = f"{num_val:g}"
    candidates.add(formatted)

    # With Indian/Western comma formatting
    int_part = int(abs(num_val))
    int_str = str(int_part)

    # Western comma formatting: 8,142
    if len(int_str) > 3:
        western = ""
        for i, digit in enumerate(reversed(int_str)):
            if i > 0 and i % 3 == 0:
                western = "," + western
            western = digit + western
        candidates.add(western)

    # Indian comma formatting: 8,142 (same for <5 digits), 81,42,163 etc.
    if len(int_str) > 3:
        indian = int_str[-3:]
        remaining = int_str[:-3]
        while remaining:
            indian = remaining[-2:] + "," + indian
            remaining = remaining[:-2]
        candidates.add(indian)

    # Also check with decimal part appended
    dec_part = ""
    if num_val != int(num_val):
        dec_str = str(num_val)
        if "." in dec_str:
            dec_part = dec_str[dec_str.index("."):]

    if dec_part:
        expanded = set()
        for c in candidates:
            expanded.add(c + dec_part)
        candidates.update(expanded)

    # 1. Direct search in quote (including normalized minuses like "-2.5")
    for candidate in candidates:
        if candidate in quote_clean:
            return True, None

    # 2. Semantic negation check:
    # If the observation is negative, the quote might use economic words
    # (contracting, contraction, deflation, decline, fell, drop, negative)
    # alongside the positive absolute value (e.g. "contracting by 1.0 per cent" -> value -1.0).
    if num_val < 0:
        abs_candidates = set()
        abs_val = abs(num_val)
        if abs_val == int(abs_val):
            abs_candidates.add(str(int(abs_val)))
        abs_candidates.add(str(abs_val))
        abs_candidates.add(f"{abs_val:g}")

        NEGATION_INDICATORS = [
            "contracting", "contracted", "contraction",
            "deflation", "deflated",
            "declined", "decline", "declining",
            "fell", "fall", "falling",
            "dropped", "drop", "dropping",
            "decrease", "decreased", "decreasing",
            "negative", "down by", "depreciated", "decelerated"
        ]

        quote_lower = quote_clean.lower()
        has_negation_word = any(neg in quote_lower for neg in NEGATION_INDICATORS)

        for abs_cand in abs_candidates:
            if abs_cand in quote_clean and has_negation_word:
                return True, None
            # Or formatted with a minus sign right before it
            if re.search(rf"-\s*{re.escape(abs_cand)}\b", quote_clean):
                return True, None

    return False, "Value not found in evidence quote"


def _check_period_in_evidence(obs: ExtractedObservation) -> Tuple[bool, Optional[str]]:
    """
    Check 2: If a period_label is claimed, the evidence quote should contain
    or strongly imply that period.

    Special cases:
    - Table rows often don't repeat the period (it's in the column header),
      so if the evidence contains NO temporal info at all, we pass softly.
    - If the evidence contains a MORE SPECIFIC period (e.g. Q1:2024-25)
      but the claimed period is broader (e.g. FY25), that's a coercion and should fail.
    """
    if not obs.period_label:
        return True, None  # No period claimed, nothing to validate

    quote = (obs.evidence_quote or "").strip().lower()
    if not quote:
        return False, "Period claimed but evidence quote is empty"

    period = obs.period_label.strip().lower()

    # Direct containment check
    if period in quote:
        return True, None

    # ── Anti-coercion check ──
    # If the evidence contains a sub-annual period (Q1, Q2, H1, etc.)
    # but the claimed period is a full fiscal year, flag it.
    # This catches "Q1:2024-25" → "FY25" coercion.
    claimed_is_fy = bool(re.match(r"fy\s*\d{2,4}$", period))
    evidence_has_quarter = bool(re.search(r"\bq[1-4]\b", quote))
    evidence_has_half = bool(re.search(r"\bh[12]\b", quote))
    if claimed_is_fy and (evidence_has_quarter or evidence_has_half):
        return False, "Period not grounded in evidence"

    # Normalize common FY patterns for comparison
    # "FY24" should match "FY 24", "FY2024", "2023-24", "FY2023-24"
    fy_match = re.match(r"fy\s*(\d{2,4})", period)
    if fy_match:
        yr = fy_match.group(1)
        short_yr = yr[-2:]
        full_yr = f"20{short_yr}" if len(yr) == 2 else yr

        variants = [
            f"fy{short_yr}",
            f"fy {short_yr}",
            f"fy{full_yr}",
            f"fy {full_yr}",
            f"fy20{short_yr}" if len(yr) == 2 else None,
            f"20{int(short_yr)-1}-{short_yr}" if short_yr.isdigit() else None,
            f"20{int(short_yr)-1:02d}-{short_yr}" if short_yr.isdigit() else None,
            full_yr,
        ]

        for v in variants:
            if v and v in quote:
                # Double-check: make sure the matched year-range isn't preceded
                # by a quarter/half prefix that would make it a sub-annual period
                if v in [f"20{int(short_yr)-1}-{short_yr}", f"20{int(short_yr)-1:02d}-{short_yr}"]:
                    # Check if Q or H prefix exists right before this year range
                    idx = quote.find(v)
                    prefix_region = quote[max(0, idx - 5):idx].strip()
                    if re.search(r"q[1-4]", prefix_region) or re.search(r"h[12]", prefix_region):
                        continue  # Skip — this is a quarter/half, not FY
                    return True, None

    # Quarter patterns: "Q1:2024-25" should match "Q1" + "2024-25"
    q_match = re.match(r"(q[1-4])\s*[:\-]?\s*(.+)", period)
    if q_match:
        q_part = q_match.group(1)
        yr_part = q_match.group(2).strip()
        if q_part in quote and yr_part in quote:
            return True, None
        if q_part in quote:
            return True, None

    # Half-year patterns
    h_match = re.match(r"(h[12])\s*(.+)?", period)
    if h_match:
        h_part = h_match.group(1)
        if h_part in quote:
            return True, None

    # Date range patterns: "2000-19" should match "2000" and possibly "19" or "2019"
    range_match = re.match(r"(\d{4})\s*[-–]\s*(\d{2,4})", period)
    if range_match:
        start_yr = range_match.group(1)
        end_yr = range_match.group(2)
        if start_yr in quote and end_yr in quote:
            return True, None

    # ── Soft pass for table rows ──
    # If the evidence contains NO temporal information at all,
    # the period likely comes from a column header not in this quote.
    # This is common for table-extracted values.
    has_any_temporal = bool(re.search(
        r"(fy\s*\d{2,4}|20\d{2}|q[1-4]|h[12]|january|february|march|april|may|june|"
        r"july|august|september|october|november|december|\d{4}\s*[-–]\s*\d{2,4})",
        quote,
    ))
    if not has_any_temporal:
        # No temporal info in the evidence at all — likely a table row
        return True, None

    return False, "Period not grounded in evidence"


def _check_concept_plausibility(obs: ExtractedObservation) -> Tuple[bool, Optional[str]]:
    """
    Check 3: The source_label, evidence quote, and concept_name should be semantically compatible.
    Catches:
    - Metric broadening: "services sector growth" → "real GDP growth"
    - Cross-subject broadening: "R&D expenditure in GDP" → "real GDP growth"
    - Ungrounded rate qualifiers: concept claims "growth" when evidence quote has no growth/change wording.
    """
    concept = (obs.concept_name or "").strip().lower()
    source = (obs.source_label or "").strip().lower()
    quote = (obs.evidence_quote or "").strip().lower()

    if not concept:
        return True, None

    # Check text comprising both source label and quote
    check_text = f"{source} {quote}"

    # Known problematic broadening patterns
    BROADENING_PAIRS = [
        # (context contains, concept contains, concept must not contain)
        ("services", "gdp", "services"),
        ("services sector", "gdp growth", "services"),
        ("agriculture", "gdp", "agriculture"),
        ("manufacturing", "gdp", "manufacturing"),
        ("industrial", "gdp", "industrial"),
        ("r&d", "gdp growth", "r&d"),
        ("research & development", "gdp growth", "research"),
        ("research and development", "gdp growth", "research"),
        ("expenditure in gdp", "gdp growth", "expenditure"),
        ("share in gdp", "gdp growth", "share"),
        ("as % of gdp", "gdp growth", "as %"),
    ]

    for context_kw, concept_kw, exempt_kw in BROADENING_PAIRS:
        if context_kw in check_text and concept_kw in concept and exempt_kw not in concept:
            return False, f"Concept '{obs.concept_name}' may not match source evidence context ('{context_kw}')"

    # Rate of change / growth qualifier verification:
    # If concept claims a delta/rate qualifier like "growth" or "expansion",
    # the evidence quote MUST ground this qualifier.
    DELTA_QUALIFIERS = ["growth", "expansion", "contraction", "deflation", "decline"]
    for q in DELTA_QUALIFIERS:
        if q in concept:
            growth_indicators = [
                "growth", "grew", "grow", "expanded", "expansion", "contracting",
                "contracted", "contraction", "deflation", "deflated", "declined",
                "decline", "declining", "fell", "fall", "falling", "increase",
                "increased", "decreasing", "decreased", "decrease", "dropped",
                "drop", "uptick", "moderated", "moderating", "y-o-y", "yoy", "m-o-m", "mom", "q-o-q", "qoq"
            ]
            if not any(gi in quote for gi in growth_indicators):
                return False, f"Concept claims '{q}' but no growth or change terms found in evidence quote"

    return True, None


def _check_assertion_status(obs: ExtractedObservation) -> Tuple[bool, Optional[str]]:
    """
    Check 4: If evidence language contains forecast/projection markers but
    assertion_status is "actual", flag for review.

    Context-aware:
    - Historical baselines (e.g. 2024, FY24, 2000-19, historical average) remain 'actual'
      even if the evidence quote contains future projection markers for subsequent periods
      (e.g. 'projected to moderate from 5.7% in 2024 to 4.3% in 2025').
    - Values in comparative baseline positions ('from X', 'below X', 'compared with X')
      are historical points of reference, not forecasts.
    """
    if obs.assertion_status != "actual":
        return True, None

    quote = (obs.evidence_quote or "").strip().lower()
    if not quote:
        return True, None

    # Check 1: Historical period exemption
    period = (obs.period_label or "").strip().lower()
    concept = (obs.concept_name or "").strip().lower()

    is_historical = False
    if any(h in concept for h in ["historical", "previous year", "a year ago"]):
        is_historical = True
    elif period:
        # Check for historical years (e.g. 2024, 2023, FY24, FY23, 2000-19, or ranges ending in <= 24)
        if re.search(r"\b(20[0-1]\d|202[0-4]|fy[0-1]\d|fy2[0-4])\b", period):
            is_historical = True
        elif re.search(r"\b\d{4}\s*[-–]\s*(?:19|20|21|22|23|24)\b", period):
            is_historical = True

    if is_historical:
        # The observation refers to an explicit past/historical baseline period
        return True, None

    # Check 2: Comparative baseline syntax in quote
    try:
        val_str = f"{float(obs.value):g}"
    except (TypeError, ValueError):
        val_str = str(obs.value)

    baseline_pattern = rf"(?:from|below|compared (?:with|to)|as against)\s+[^.;]*?\b{re.escape(val_str)}\b"
    if re.search(baseline_pattern, quote):
        # Value appears as a baseline starting point in comparative structure
        return True, None

    FORECAST_MARKERS = [
        "likely to",
        "expected to",
        "projected",
        "projection",
        "forecast",
        "anticipated",
        "estimated to",
        "is likely",
        "are likely",
        "will likely",
    ]

    ESTIMATE_MARKERS = [
        "advance estimate",
        "provisional",
        "revised estimate",
        "estimated at",
    ]

    TARGET_MARKERS = [
        "budget estimate",
        "budgeted",
        "target",
    ]

    for marker in FORECAST_MARKERS:
        if marker in quote:
            return False, f"Assertion status 'actual' conflicts with evidence language: '{marker}'"

    for marker in ESTIMATE_MARKERS:
        if marker in quote:
            return False, f"Assertion status 'actual' conflicts with evidence language: '{marker}'"

    for marker in TARGET_MARKERS:
        if marker in quote:
            return False, f"Assertion status 'actual' conflicts with evidence language: '{marker}'"

    return True, None


def _check_metadata_value(obs: ExtractedObservation) -> Tuple[bool, Optional[str]]:
    """
    Check 5: Detect values likely extracted from document metadata headers
    rather than actual financial data points.
    Example: "Income Statement for the year ended March 31, 2025" → value=31 is wrong.
    """
    quote = (obs.evidence_quote or "").strip().lower()

    METADATA_PATTERNS = [
        r"income statement for the",
        r"balance sheet as at",
        r"statement of .+ for the",
        r"notes to .+ financial statements",
        r"cash flow statement for the",
        r"for the year ended",
        r"for the period ended",
        r"as at \w+ \d{1,2},?\s*\d{4}",
        r"annual report \d{4}",
        r"table of contents",
        r"financial highlights",
    ]

    is_metadata_line = False
    for pattern in METADATA_PATTERNS:
        if re.search(pattern, quote):
            is_metadata_line = True
            break

    if not is_metadata_line:
        return True, None

    # If the quote is primarily a metadata/header line and short,
    # the value is likely extracted from the header itself
    # Check if the quote is very short (likely just a header)
    non_header_content = re.sub(
        r"(income statement|balance sheet|statement of|for the year ended|"
        r"for the period ended|as at|annual report|table of contents|"
        r"financial highlights|notes to|cash flow statement).*",
        "",
        quote,
        flags=re.IGNORECASE,
    ).strip()

    # If removing the header pattern leaves very little text,
    # the observation was probably extracted from metadata
    if len(non_header_content) < 30:
        return False, "Value likely extracted from document metadata, not a data point"

    return True, None


# ──────────────────────────────────────────────────
# Main validation entry point
# ──────────────────────────────────────────────────

def validate_grounding(obs: ExtractedObservation) -> Tuple[bool, List[str]]:
    """
    Runs all grounding checks on a candidate observation.

    Returns:
        (passed: bool, reasons: List[str])
        - passed is True only if ALL checks pass
        - reasons lists all failure reasons (empty if passed)
    """
    checks = [
        _check_value_in_evidence,
        _check_period_in_evidence,
        _check_concept_plausibility,
        _check_assertion_status,
        _check_metadata_value,
    ]

    reasons: List[str] = []
    all_passed = True

    for check_fn in checks:
        passed, reason = check_fn(obs)
        if not passed and reason:
            all_passed = False
            reasons.append(reason)

    return all_passed, reasons
