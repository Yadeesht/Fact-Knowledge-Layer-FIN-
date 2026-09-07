from typing import Tuple, Optional
from backend.models.schema import Observation
from backend.core.canonicalizer import entities_match, concepts_match
from backend.core.scope import compare_scope
from backend.core.temporal import compare_time


class CandidateMatcher:
    """
    Candidate Matcher: Separates candidate comparability from relationship determination.
    Answers: 'Could these two observations be about the same underlying measurement?'
    Enforces the quarantine policy: observations requiring review are barred from contaminating
    the reconciliation engine.
    """

    @staticmethod
    def is_comparable_candidate(a: Observation, b: Observation) -> Tuple[bool, Optional[str]]:
        # -------------------------------------------------------------
        # Gate 0: Quarantine Policy (Section 26)
        # -------------------------------------------------------------
        if a.needs_review or b.needs_review:
            return False, "quarantined_needs_review"

        # -------------------------------------------------------------
        # Gate 1: Entity Gate (Section 31)
        # -------------------------------------------------------------
        if not entities_match(a, b):
            return False, "entity_mismatch"

        # -------------------------------------------------------------
        # Gate 2: Concept Gate (Section 31)
        # -------------------------------------------------------------
        if not concepts_match(a, b):
            return False, "concept_mismatch"

        # If entity and concept match, they are comparable candidates
        # (Reconciliation engine will subsequently determine whether differing
        # periods or scopes contextualize, corroborate, or conflict).
        return True, "comparable_candidate"
