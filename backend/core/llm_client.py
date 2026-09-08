import os
import json
import urllib.request
import urllib.error
import socket
import time
from typing import Optional, Dict, Any, Tuple
from dotenv import load_dotenv

load_dotenv()


def get_llm_credentials() -> Dict[str, Optional[str]]:
    return {"gemini_key": os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")}


def _is_timeout_or_transient(e: Exception) -> Tuple[bool, str]:
    """
    Checks if an exception is a timeout, rate limit, or transient network error.
    Returns (is_transient, reason_description).
    """
    if isinstance(e, (socket.timeout, TimeoutError)):
        return True, "timed out"
    if isinstance(e, urllib.error.HTTPError):
        if e.code == 429:
            return True, "rate limited (HTTP 429)"
        if e.code in (500, 502, 503, 504):
            return True, f"server temporary error (HTTP {e.code})"
        return False, f"HTTP {e.code}"
    if isinstance(e, urllib.error.URLError):
        err_msg = str(e.reason).lower()
        if "timed out" in err_msg or isinstance(e.reason, (socket.timeout, TimeoutError)):
            return True, "timed out"
        if "connection reset" in err_msg or "remotedisconnected" in err_msg:
            return True, "connection reset"
        return False, str(e.reason)
    err_str = str(e).lower()
    if "timed out" in err_str or "timeout" in err_str:
        return True, "timed out"
    return False, str(e)


def call_llm(
    prompt: str,
    system_instruction: Optional[str] = None,
    timeout: Optional[int] = None,
    max_retries_per_model: int = 3,
    max_cycles: int = 2,
) -> Optional[str]:
    """
    Calls Gemini API with resilient retry and automatic candidate fallback.
    - Generous timeout (default 90s, configurable via LLM_TIMEOUT) for large batch processing.
    - Sleeps and retries on read timeouts, rate limits (HTTP 429), and temporary server errors.
    - Never breaks the pipeline on timeout; falls back gracefully.
    """
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("[LLM] Warning: No API key configured (GEMINI_API_KEY or GOOGLE_API_KEY).")
        return None

    # Default timeout increased to 90s to comfortably handle large multi-page batch prompts
    call_timeout = timeout or int(os.getenv("LLM_TIMEOUT", "90"))

    primary_model = os.getenv("EXTRACTOR_MODEL", "gemini-3.5-flash-lite")
    fallback_models = [
        primary_model,
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
    ]
    # Remove duplicates while preserving preference order
    candidate_models = list(dict.fromkeys(fallback_models))

    def _sleep_safe(seconds: float) -> bool:
        """Sleeps while checking for pipeline cancellation. Returns True if cancelled."""
        try:
            from backend.ingestion.pipeline import sleep_or_cancel
            return sleep_or_cancel(seconds)
        except Exception:
            time.sleep(seconds)
            return False

    for cycle in range(1, max_cycles + 1):
        for model in candidate_models:
            for attempt in range(1, max_retries_per_model + 1):
                # Respect cancellation before initiating network call
                try:
                    from backend.ingestion.pipeline import is_cancellation_requested
                    if is_cancellation_requested():
                        return None
                except Exception:
                    pass

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
                    method="POST",
                )

                try:
                    with urllib.request.urlopen(req, timeout=call_timeout) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        return data["candidates"][0]["content"]["parts"][0]["text"]

                except Exception as e:
                    # Non-retryable client errors (e.g. 400 Bad Request or 404 Model Not Found)
                    if isinstance(e, urllib.error.HTTPError) and e.code in (400, 404):
                        err_body = e.read().decode("utf-8") if e.fp else ""
                        print(f"[LLM] Model {model} unavailable (HTTP {e.code}: {err_body[:80] or e.reason}). Trying fallback model...")
                        break  # Move to next candidate model immediately

                    is_transient, reason = _is_timeout_or_transient(e)
                    if is_transient:
                        sleep_time = min(25.0, 4.0 * (1.8 ** (attempt - 1)))
                        print(
                            f"[LLM] Model {model} {reason}. Sleeping {sleep_time:.1f}s before retrying... (Attempt {attempt}/{max_retries_per_model})"
                        )
                        if _sleep_safe(sleep_time):
                            return None
                        continue
                    else:
                        sleep_time = 5.0
                        print(
                            f"[LLM] Model {model} encountered {e}. Sleeping {sleep_time:.1f}s before retrying... (Attempt {attempt}/{max_retries_per_model})"
                        )
                        if _sleep_safe(sleep_time):
                            return None
                        continue

        if cycle < max_cycles:
            cycle_sleep = 5.0 * cycle
            print(f"[LLM] Retrying candidate models with {cycle_sleep:.1f}s backoff (Cycle {cycle + 1}/{max_cycles})...")
            if _sleep_safe(cycle_sleep):
                return None

    print("[LLM] Notice: All LLM retry cycles completed without response. Continuing gracefully.")
    return None

