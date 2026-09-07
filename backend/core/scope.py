from backend.models.schema import Scope


def compare_scope(a: Scope, b: Scope) -> str:
    """
    Compares two Scopes:
    Returns:
    - 'different': explicitly conflicting consolidation or geography
    - 'compatible': same or compatible scope
    """
    # Check consolidation (e.g. consolidated financial statements vs standalone)
    if (
        a.consolidation
        and b.consolidation
        and a.consolidation != "unknown"
        and b.consolidation != "unknown"
        and a.consolidation.lower() != b.consolidation.lower()
    ):
        return "different"

    # Check geography
    if (
        a.geography
        and b.geography
        and a.geography.lower().strip() != b.geography.lower().strip()
    ):
        return "different"

    # Check level (e.g. company vs subsidiary vs segment)
    if (
        a.level
        and b.level
        and a.level.lower().strip() != b.level.lower().strip()
    ):
        return "different"

    return "compatible"
