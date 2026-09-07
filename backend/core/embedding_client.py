import os
import json
import math
import re
import urllib.request
from typing import List, Optional, Dict

_EMBEDDING_CACHE: Dict[str, List[float]] = {}


def _fallback_ngram_vector(text: str, dim: int = 128) -> List[float]:
    """
    Deterministic zero-dependency fallback vector for testing and offline environments.
    Uses character 3-grams and word tokens mapped into fixed dimensional space with L2 normalization.
    """
    clean = re.sub(r"[^\w\s]", " ", text.lower()).strip()
    words = clean.split()
    tokens = list(words)
    # Character 3-grams
    for word in words:
        if len(word) >= 3:
            for i in range(len(word) - 2):
                tokens.append(word[i : i + 3])

    vec = [0.0] * dim
    for tok in tokens:
        # Fowler-Noll-Vo hash variant for deterministic distribution
        h = 2166136261
        for char in tok:
            h = (h ^ ord(char)) * 16777619
        idx = (h & 0xFFFFFFFF) % dim
        vec[idx] += 1.0

    # L2 normalize
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


def get_embedding(text: str) -> List[float]:
    """
    Computes an embedding vector for a given text.
    Uses Gemini text-embedding-004 if API key is available,
    falling back to deterministic semantic token vectorizer.
    """
    text_clean = text.strip().lower()
    if not text_clean:
        return [0.0] * 128

    if text_clean in _EMBEDDING_CACHE:
        return _EMBEDDING_CACHE[text_clean]

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if api_key:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={api_key}"
            payload = {
                "model": "models/text-embedding-004",
                "content": {"parts": [{"text": text.strip()}]},
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                vec = data.get("embedding", {}).get("values")
                if vec:
                    _EMBEDDING_CACHE[text_clean] = vec
                    return vec
        except Exception:
            # Silently fall back to deterministic n-gram vectorizer
            pass

    vec = _fallback_ngram_vector(text_clean, dim=128)
    _EMBEDDING_CACHE[text_clean] = vec
    return vec


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Computes the cosine similarity between two numeric vectors."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0

    dot = sum(a * b for a, b in zip(v1, v2))
    norm_a = math.sqrt(sum(a * a for a in v1))
    norm_b = math.sqrt(sum(b * b for b in v2))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))
