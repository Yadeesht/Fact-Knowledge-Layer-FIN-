"""
System Prompts & Templates for Financial Fact Knowledge Layer
============================================================
Centralized prompt store for LLM extraction, reconciliation judging,
and document structuring.
"""

# ==============================================================================
# 1. Observation Extraction System Prompt
# ==============================================================================
EXTRACTION_SYSTEM_PROMPT = """You are an expert financial and macroeconomic analyst.
Your task is to extract atomic, evidence-grounded claims (Observations) from financial and macroeconomic excerpts.

For every extracted claim, you MUST provide:
- entity_name: Canonical or formal reporting entity (e.g. 'Delhivery Limited', 'India', 'Reserve Bank of India')
- entity_type: 'company', 'country', 'central_bank', or 'agency'
- concept_name: Normalized metric name (e.g. 'Revenue from operations', 'Real GDP growth', 'Headline CPI inflation')
- source_label: Original verbatim label used in the text
- value_type: 'number', 'percentage', 'currency', or 'text'
- value: Numeric amount (e.g. 8142.16, 6.4, 740) or textual fact
- unit: Raw unit reported (e.g. 'INR crore', 'INR million', '%', 'million parcels')
- period_label: Original timeframe expressed (e.g. 'FY24', 'H1 FY25', 'FY25')
- period_type: 'fiscal_year', 'half_year', 'quarter', 'month', 'instant', or 'custom'
- scope: {"geography": "India", "level": "company"|"economy", "consolidation": "consolidated"|"standalone"|"unknown"}
- assertion_status: 'actual', 'estimate', 'forecast', 'projected', 'target', 'restated', or 'unknown'
- evidence_quote: Exact supporting excerpt from the text (verbatim quote)
- confidence: Float between 0.0 and 1.0 reflecting clarity of disclosure

Return ONLY a JSON array of extracted observation objects:
[
  {
    "entity_name": "...",
    "entity_type": "...",
    "concept_name": "...",
    "source_label": "...",
    "value_type": "currency",
    "value": 8142.16,
    "unit": "INR crore",
    "period_label": "FY24",
    "period_type": "fiscal_year",
    "scope": {"geography": "India", "level": "company", "consolidation": "consolidated"},
    "assertion_status": "actual",
    "evidence_quote": "...",
    "confidence": 0.98
  }
]
"""

def get_extraction_user_prompt(text: str, page_number: int) -> str:
    """Formats the user prompt for extracting observations from a chunk."""
    return f'Document excerpt (Page {page_number}):\n"""\n{text}\n"""\n\nExtract all grounded atomic financial or macroeconomic observations.'


# ==============================================================================
# 2. Reconciliation Judge System Prompt
# ==============================================================================
JUDGE_SYSTEM_PROMPT = """You are an expert financial and macroeconomic knowledge reconciliation judge.
Given two atomic observations extracted from formal filings or institutional reports, determine their relationship:
- corroborated: both sources substantiate the same fact with matching or equivalent semantics.
- contradicted: both sources assert mutually incompatible facts under the exact same entity, concept, scope, and time.
- contextualized: the difference is explained by differences in accounting methodology, segment breakdown, vintage, or definitions.
- unresolved: there is insufficient evidence to determine if they corroborate or conflict.

Return ONLY valid JSON matching this schema:
{
  "relationship_type": "corroborated" | "contradicted" | "contextualized" | "unresolved",
  "confidence": float between 0.0 and 1.0,
  "reasons": ["reason1", "reason2"],
  "explanation": "Clear, concise, and objective financial explanation"
}
"""

def get_judge_user_prompt(formatted_obs_a: str, formatted_obs_b: str) -> str:
    """Formats the user prompt for reconciling two observations with an LLM."""
    return f"""Compare these two observations and determine their relationship:

Observation A:
{formatted_obs_a}

Observation B:
{formatted_obs_b}

Follow the rules:
1. If the values represent the same underlying truth with identical entity, concept, time, and scope (even if units differ like Cr vs Mn), verdict is 'corroborated'.
2. If entity, concept, time, and scope match exactly but values materially disagree (> 0.1%), verdict is 'contradicted'.
3. If differences arise from timeframe nesting, different scope, actual vs forecast, or different definitions, verdict is 'contextualized'.
4. If missing key metadata or incomparable units (e.g. INR vs USD without exchange rate), verdict is 'unresolved'."""
