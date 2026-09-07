import re
from typing import Tuple, Optional
from backend.models.schema import Observation
from backend.core.canonicalizer import entities_match, concepts_match


def are_concepts_related_siblings(a: Observation, b: Observation) -> bool:
    """
    Checks whether two concepts belong to the same economic/financial domain or metric family,
    meaning they can be compared to evaluate apparent contradictions or sub-component distinctions.
    """
    c_a = (a.concept.canonical_name or "").lower().strip()
    c_b = (b.concept.canonical_name or "").lower().strip()
    if not c_a or not c_b:
        return False

    words_a = set(re.findall(r"\w+", c_a))
    words_b = set(re.findall(r"\w+", c_b))

    # Shared economic/financial domains
    SHARED_DOMAIN_TOKENS = {
        "gdp", "gva", "growth", "inflation", "cpi", "wpi", "deflator",
        "revenue", "sales", "turnover", "income", "profit", "ebitda",
        "expenditure", "outlay", "capex", "opex", "deficit", "surplus",
        "cad", "export", "exports", "import", "imports", "shipment", "shipments",
        "volume", "debt", "borrowing", "borrowings", "credit", "deposit", "deposits",
        "tax", "taxes", "remittance", "remittances", "reserves", "forex"
    }

    shared_domains = (words_a & words_b) & SHARED_DOMAIN_TOKENS
    if shared_domains:
        return True

    # Check macroeconomic output sibling relationship (e.g. services GVA vs GDP growth)
    macro_output_keywords = {"gdp", "gva", "output", "services", "manufacturing", "agriculture", "industry"}
    if bool(words_a & macro_output_keywords) and bool(words_b & macro_output_keywords):
        return True

    # Substring containment (e.g. "revenue" in "revenue from traded goods")
    if c_a in c_b or c_b in c_a:
        return True

    return False


class CandidateMatcher:
    """
    Candidate Matcher: Separates candidate comparability from relationship determination.
    Answers: 'Could these two observations be about the same underlying measurement or related metric family?'
    Enforces the quarantine policy: observations requiring review are barred from contaminating
    the reconciliation engine.
    """

    @staticmethod
    def is_comparable_candidate(a: Observation, b: Observation) -> Tuple[bool, Optional[str]]:
        # -------------------------------------------------------------
        # Gate 0: Quarantine Policy
        # -------------------------------------------------------------
        if a.needs_review or b.needs_review:
            return False, "quarantined_needs_review"

        # -------------------------------------------------------------
        # Gate 1: Entity Gate
        # -------------------------------------------------------------
        if not entities_match(a, b):
            return False, "entity_mismatch"

        # -------------------------------------------------------------
        # Gate 2: Concept Gate
        # -------------------------------------------------------------
        if concepts_match(a, b):
            return True, "identical_concept"

        # Related sibling / sub-component metrics under same entity
        if are_concepts_related_siblings(a, b):
            return True, "related_sibling_concept"

        return False, "concept_mismatch"
