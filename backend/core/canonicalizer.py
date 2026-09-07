from typing import Tuple
from backend.models.schema import Observation, Entity, Concept

ENTITY_ALIASES = {
    "delhivery": "delhivery limited",
    "delhivery ltd": "delhivery limited",
    "delhivery limited": "delhivery limited",
    "rbi": "reserve bank of india",
    "reserve bank of india": "reserve bank of india",
    "imf": "international monetary fund",
    "international monetary fund": "international monetary fund",
    "fund": "international monetary fund",
    "goi": "government of india",
    "government of india": "government of india",
    "ministry of finance": "government of india",
    "mof": "government of india",
    "india": "india",
    "republic of india": "india",
    "indian economy": "india",
}

CONCEPT_ALIASES = {
    "sales": "revenue",
    "turnover": "revenue",
    "total turnover": "revenue",
    "total sales": "revenue",
    "total revenue": "revenue",
    "revenue": "revenue",
    "revenue from operations": "revenue from operations",
    "revenue from contracts with customers": "revenue from contracts with customers",
    "revenue from traded goods": "revenue from traded goods",
    "real gdp growth": "real gdp growth",
    "real gdp growth rate": "real gdp growth",
    "gdp growth": "real gdp growth",
    "gdp growth rate": "real gdp growth",
    "nominal gdp growth": "nominal gdp growth",
    "headline cpi inflation": "headline cpi inflation",
    "cpi inflation": "headline cpi inflation",
    "consumer price index inflation": "headline cpi inflation",
    "core cpi inflation": "core cpi inflation",
    "current account deficit": "current account deficit",
    "cad": "current account deficit",
    "express parcel volume": "express parcel volume",
    "express parcel shipment volume": "express parcel volume",
    "active customers": "active customers",
    "pin codes serviced": "pin codes serviced",
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
