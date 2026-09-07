import os
import json
import urllib.request
from typing import Optional, Dict, Any
from dotenv import load_dotenv

load_dotenv()


def get_llm_credentials() -> Dict[str, Optional[str]]:
    return {"gemini_key": os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")}


def call_llm(prompt: str, system_instruction: Optional[str] = None) -> Optional[str]:
    """
    Calls Gemini API with automatic fallback.
    Tries the primary model specified in EXTRACTOR_MODEL, and falls back to secondary models
    if rate-limited, not found, or quota is exceeded.
    """
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None

    primary_model = os.getenv("EXTRACTOR_MODEL", "gemini-3.5-flash-lite")
    fallback_models = [
        primary_model,
        "gemini-3.1-flash-lite",
    ]
    # Remove duplicates while preserving order
    candidate_models = list(dict.fromkeys(fallback_models))

    for model in candidate_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload: Dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8") if e.fp else ""
            print(f"Model {model} failed (HTTP {e.code}). Trying fallback... Error: {err_body or e.reason}")
            continue
        except Exception as e:
            print(f"Model {model} failed: {e}. Trying fallback...")
            continue

    print("All Gemini candidate models failed.")
    return None

