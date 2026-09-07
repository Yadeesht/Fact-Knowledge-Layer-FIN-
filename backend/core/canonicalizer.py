from typing import Tuple
from backend.models.schema import Observation, Entity, Concept

ENTITY_ALIASES = {
    "delhivery": "delhivery limited",
    "delhivery ltd": "delhivery limited",
    "rbi": "reserve bank of india",
    "imf": "international monetary fund",
    "fund": "international monetary fund",
    "goi": "government of india",
    "ministry of finance": "government of india",
    "mof": "government of india",
    "republic of india": "india",
    "indian economy": "india",
}

CONCEPT_ALIASES = {
    "sales": "revenue",
    "turnover": "revenue",
    "total turnover": "revenue",
    "total sales": "revenue",
    "total revenue": "revenue",
    "real gdp growth rate": "real gdp growth",
    "gdp growth": "real gdp growth",
    "gdp growth rate": "real gdp growth",
    "cpi inflation": "headline cpi inflation",
    "consumer price index inflation": "headline cpi inflation",
    "cad": "current account deficit",
    "express parcel shipment volume": "express parcel volume",
    "team size": "total workforce",
    "employees": "total workforce",
    "total employees": "total workforce",
}


def canonicalize_entity_name(raw_name: str) -> str:
    cleaned = raw_name.lower().strip()
    return ENTITY_ALIASES.get(cleaned, cleaned)


def canonicalize_concept_name(raw_name: str) -> str:
    cleaned = raw_name.lower().strip()
    return CONCEPT_ALIASES.get(cleaned, cleaned)


def entities_match(a: Observation, b: Observation) -> bool:
    if a.entity.id and b.entity.id:
        if a.entity.id == b.entity.id:
            return True
        return False

    name_a = canonicalize_entity_name(a.entity.canonical_name)
    name_b = canonicalize_entity_name(b.entity.canonical_name)
    if name_a == name_b:
        return True

    # Check aliases
    aliases_a = {canonicalize_entity_name(x) for x in a.entity.aliases}
    aliases_b = {canonicalize_entity_name(x) for x in b.entity.aliases}

    if name_a in aliases_b or name_b in aliases_a:
        return True

    return bool(aliases_a & aliases_b)


def concepts_match(a: Observation, b: Observation) -> bool:
    if a.concept.id and b.concept.id:
        if a.concept.id == b.concept.id:
            return True
        return False

    concept_a = canonicalize_concept_name(a.concept.canonical_name)
    concept_b = canonicalize_concept_name(b.concept.canonical_name)
    if concept_a == concept_b:
        return True

    # If source label matches canonical
    if a.concept.source_label and canonicalize_concept_name(a.concept.source_label) == concept_b:
        return True
    if b.concept.source_label and canonicalize_concept_name(b.concept.source_label) == concept_a:
        return True

    return False
