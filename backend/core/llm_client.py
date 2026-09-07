import os
import json
import urllib.request
import urllib.error
from typing import Optional, Dict, Any, List
from backend.models.schema import (
    ExtractedObservation,
    ValueType,
    PeriodType,
    AssertionStatus,
    Scope,
    RelationshipType,
    LLMRelationshipResult,
)


def get_llm_credentials() -> Dict[str, Optional[str]]:
    """
    Checks for Gemini, OpenAI, or other available LLM keys from environment variables or .env.
    """
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    # Check for local .env file if not in system environment
    if not (gemini_key or openai_key):
        env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("GEMINI_API_KEY="):
                            gemini_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        elif line.startswith("GOOGLE_API_KEY="):
                            gemini_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        elif line.startswith("OPENAI_API_KEY="):
                            openai_key = line.split("=", 1)[1].strip().strip('"').strip("'")
            except Exception:
                pass

    return {
        "gemini_key": gemini_key,
        "openai_key": openai_key,
    }


def call_gemini_api(prompt: str, api_key: str, system_instruction: Optional[str] = None) -> str:
    """
    Direct HTTP call to Gemini API using standard library urllib (no heavy external SDK required).
    Uses gemini-1.5-flash or gemini-2.0-flash with JSON mode.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"

    payload: Dict[str, Any] = {
        "contents": [
            {
                "parts": [{"text": prompt}]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        }
    }

    if system_instruction:
        payload["systemInstruction"] = {
            "parts": [{"text": system_instruction}]
        }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        res_data = json.loads(resp.read().decode("utf-8"))
        candidates = res_data.get("candidates", [])
        if not candidates:
            raise RuntimeError("Gemini returned no candidates.")
        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            raise RuntimeError("Gemini candidate has no content parts.")
        return parts[0].get("text", "")


def call_openai_api(prompt: str, api_key: str, system_instruction: Optional[str] = None) -> str:
    """
    Direct HTTP call to OpenAI chat completion API.
    """
    url = "https://api.openai.com/v1/chat/completions"
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": "gpt-4o-mini",
        "messages": messages,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        },
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        res_data = json.loads(resp.read().decode("utf-8"))
        return res_data["choices"][0]["message"]["content"]


def call_llm(prompt: str, system_instruction: Optional[str] = None) -> Optional[str]:
    """
    Calls the available LLM (Gemini preferred, then OpenAI). Returns JSON string or None.
    """
    creds = get_llm_credentials()
    if creds["gemini_key"]:
        return call_gemini_api(prompt, creds["gemini_key"], system_instruction)
    elif creds["openai_key"]:
        return call_openai_api(prompt, creds["openai_key"], system_instruction)
    return None
