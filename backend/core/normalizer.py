from typing import Optional, Tuple
import re
from backend.models.schema import FactValue, ValueType

# Unit multipliers for quantity and scale
UNIT_MULTIPLIERS = {
    "": 1.0,
    "unit": 1.0,
    "units": 1.0,
    "thousand": 1_000.0,
    "thousands": 1_000.0,
    "k": 1_000.0,
    "lakh": 100_000.0,
    "lakhs": 100_000.0,
    "lac": 100_000.0,
    "lacs": 100_000.0,
    "million": 1_000_000.0,
    "millions": 1_000_000.0,
    "mn": 1_000_000.0,
    "m": 1_000_000.0,
    "crore": 10_000_000.0,
    "crores": 10_000_000.0,
    "cr": 10_000_000.0,
    "billion": 1_000_000_000.0,
    "billions": 1_000_000_000.0,
    "bn": 1_000_000_000.0,
    "b": 1_000_000_000.0,
    "trillion": 1_000_000_000_000.0,
    "trillions": 1_000_000_000_000.0,
    "tn": 1_000_000_000_000.0,
}

KNOWN_CURRENCIES = {
    "inr": "INR",
    "₹": "INR",
    "rs": "INR",
    "rs.": "INR",
    "rupees": "INR",
    "usd": "USD",
    "$": "USD",
    "dollars": "USD",
    "eur": "EUR",
    "€": "EUR",
    "gbp": "GBP",
    "£": "GBP",
}


def parse_unit_components(raw_unit: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Parses a raw unit string into (currency, scale_multiplier_key).
    e.g., '₹ Cr' -> ('INR', 'crore')
    e.g., 'INR Million' -> ('INR', 'million')
    e.g., 'crore' -> (None, 'crore')
    e.g., '%' -> ('%', None)
    """
    if not raw_unit:
        return None, None

    cleaned = raw_unit.lower().strip()
    if cleaned in ("%", "pct", "percent", "percentage"):
        return "%", None

    currency = None
    scale = None

    # Check for known currencies
    for sym, curr in KNOWN_CURRENCIES.items():
        if sym in cleaned:
            currency = curr
            cleaned = cleaned.replace(sym, " ").strip()
            break

    # Look for scale multiplier in remainder
    tokens = re.split(r"[\s\-_/]+", cleaned)
    for token in tokens:
        if token in UNIT_MULTIPLIERS:
            scale = token
            break

    return currency, scale


def normalize_fact_value(value: FactValue) -> FactValue:
    """
    Normalizes a FactValue in place / returns enriched copy:
    - If percentage, amount stays 10.0 (not 0.10) with normalized_unit='%'
    - If currency/scale, computes normalized_amount = amount * multiplier,
      normalized_unit = currency or base scale
    """
    if value.type == ValueType.PERCENTAGE:
        # Keep percentages in human readable 10.0 format with unit='%'
        amt = value.amount
        if amt is None and value.text:
            try:
                amt = float(re.sub(r"[^\d.-]", "", value.text))
            except ValueError:
                amt = None
        return FactValue(
            type=ValueType.PERCENTAGE,
            amount=amt,
            unit="%",
            normalized_amount=amt,
            normalized_unit="%",
            text=value.text,
        )

    if value.type in (ValueType.CURRENCY, ValueType.NUMBER):
        amt = value.amount
        if amt is None and value.text:
            try:
                clean_text = value.text.replace(",", "")
                matches = re.findall(r"[-+]?\d*\.?\d+", clean_text)
                if matches:
                    amt = float(matches[0])
            except ValueError:
                amt = None

        if amt is None:
            return value

        currency, scale = parse_unit_components(value.unit)

        multiplier = 1.0
        if scale and scale in UNIT_MULTIPLIERS:
            multiplier = UNIT_MULTIPLIERS[scale]

        normalized_amt = amt * multiplier

        if currency:
            norm_unit = currency
        elif scale:
            norm_unit = "unit"
        else:
            norm_unit = value.unit.lower().strip() if value.unit else "unit"

        return FactValue(
            type=value.type,
            amount=amt,
            unit=value.unit,
            normalized_amount=normalized_amt,
            normalized_unit=norm_unit,
            text=value.text,
        )

    # For text or boolean, keep as is
    return value
