from datetime import date
from typing import Tuple, Optional
import re
from backend.models.schema import TimeContext, PeriodType


def normalize_fiscal_year(label: str) -> str:
    """
    Normalizes fiscal year variants:
    e.g. 'FY 2024', 'FY24', '2023-24', 'FY 23-24' -> 'FY24'
    Preserves trailing qualifiers like '(up to February 2025)' or 'as of March 2025'.
    """
    if not label:
        return label

    raw = label.strip()

    # Look for parenthetical or qualifying suffix text: e.g. "(up to February 2025)", "as on March 31"
    qualifier = ""
    q_match = re.search(r"(\(.*?\)|(?:up to|as on|as of|end-)\s*.+)$", raw, re.IGNORECASE)
    if q_match:
        qualifier = " " + q_match.group(1).strip()
        cleaned_core = raw[:q_match.start()].lower().strip()
    else:
        cleaned_core = raw.lower()

    match_fy_4 = re.search(r"fy\s*(20)?(\d{2})", cleaned_core)
    if match_fy_4:
        return f"FY{match_fy_4.group(2)}{qualifier}"

    match_range = re.search(r"20(\d{2})[-/](\d{2})", cleaned_core)
    if match_range:
        return f"FY{match_range.group(2)}{qualifier}"

    return raw


def parse_dates_from_label(label: Optional[str]) -> Tuple[Optional[date], Optional[date], PeriodType]:
    """
    Deterministically computes start and end calendar dates for Indian fiscal periods:
    - 'FY24' / '2023-24'  -> (2023-04-01, 2024-03-31, FISCAL_YEAR)
    - 'FY25' / '2024-25'  -> (2024-04-01, 2025-03-31, FISCAL_YEAR)
    - 'H1 FY24'           -> (2023-04-01, 2023-09-30, HALF_YEAR)
    - 'H1 FY25'           -> (2024-04-01, 2024-09-30, HALF_YEAR)
    - 'Q4 FY24'           -> (2024-01-01, 2024-03-31, QUARTER)
    """
    if not label:
        return None, None, PeriodType.CUSTOM

    raw = label.strip()

    # Half Year: e.g. H1 FY24, H1 FY25
    h_match = re.search(r"h([1-2])\s*(?:fy)?\s*(20)?(\d{2})", raw, re.I)
    if h_match:
        half_num = int(h_match.group(1))
        fy_end_yy = int(h_match.group(3))
        start_year = 2000 + fy_end_yy - 1
        if half_num == 1:
            return date(start_year, 4, 1), date(start_year, 9, 30), PeriodType.HALF_YEAR
        else:
            return date(start_year, 10, 1), date(2000 + fy_end_yy, 3, 31), PeriodType.HALF_YEAR

    # Quarter: e.g. Q4 FY24
    q_match = re.search(r"q([1-4])\s*(?:fy)?\s*(20)?(\d{2})", raw, re.I)
    if q_match:
        q_num = int(q_match.group(1))
        fy_end_yy = int(q_match.group(3))
        start_year = 2000 + fy_end_yy - 1
        end_year = 2000 + fy_end_yy
        q_ranges = {
            1: (date(start_year, 4, 1), date(start_year, 6, 30)),
            2: (date(start_year, 7, 1), date(start_year, 9, 30)),
            3: (date(start_year, 10, 1), date(start_year, 12, 31)),
            4: (date(end_year, 1, 1), date(end_year, 3, 31)),
        }
        start_d, end_d = q_ranges[q_num]
        return start_d, end_d, PeriodType.QUARTER

    # Full Fiscal Year: e.g. FY24, FY25, 2023-24
    fy_norm = normalize_fiscal_year(raw)
    fy_match = re.search(r"FY(\d{2})", fy_norm, re.I)
    if fy_match:
        end_yy = int(fy_match.group(1))
        start_year = 2000 + end_yy - 1
        end_year = 2000 + end_yy
        return date(start_year, 4, 1), date(end_year, 3, 31), PeriodType.FISCAL_YEAR

    return None, None, PeriodType.CUSTOM


def normalize_period_label(time_ctx: TimeContext) -> str:
    if not time_ctx.label:
        if time_ctx.start_date and time_ctx.end_date:
            return f"{time_ctx.start_date}_{time_ctx.end_date}"
        return "unknown"

    raw = time_ctx.label.strip()

    # Quarter check
    q_match = re.search(r"q([1-4])\s*(?:fy)?\s*(20)?(\d{2})", raw, re.I)
    if q_match:
        return f"Q{q_match.group(1)}_FY{q_match.group(3)}"

    # Half year check
    h_match = re.search(r"h([1-2])\s*(?:fy)?\s*(20)?(\d{2})", raw, re.I)
    if h_match:
        return f"H{h_match.group(1)}_FY{h_match.group(3)}"

    # Fiscal year check
    if "fy" in raw.lower() or re.search(r"\d{4}-\d{2}", raw):
        return normalize_fiscal_year(raw)

    return raw.lower().replace(" ", "")


def compare_time(a: TimeContext, b: TimeContext) -> str:
    """
    Returns:
    - 'same': exact period match
    - 'nested': one is a sub-period of the other (e.g. H1 FY24 vs FY24)
    - 'different': different reporting periods
    """
    start_a, end_a, _ = (a.start_date, a.end_date, a.period_type)
    if not start_a and a.label:
        start_a, end_a, _ = parse_dates_from_label(a.label)

    start_b, end_b, _ = (b.start_date, b.end_date, b.period_type)
    if not start_b and b.label:
        start_b, end_b, _ = parse_dates_from_label(b.label)

    if (
        start_a is not None
        and start_b is not None
        and end_a is not None
        and end_b is not None
    ):
        if start_a == start_b and end_a == end_b:
            return "same"
        if (start_a >= start_b and end_a <= end_b) or (
            start_b >= start_a and end_b <= end_a
        ):
            return "nested"
        return "different"

    norm_a = normalize_period_label(a)
    norm_b = normalize_period_label(b)

    if norm_a == norm_b and norm_a != "unknown":
        return "same"

    # Check for quarterly/half-year vs full-year nesting
    for prefix in ("Q1_", "Q2_", "Q3_", "Q4_", "H1_", "H2_"):
        if norm_a.startswith(prefix) and norm_a.replace(prefix, "") == norm_b:
            return "nested"
        if norm_b.startswith(prefix) and norm_b.replace(prefix, "") == norm_a:
            return "nested"

    return "different"
