"""
Test LLM Connectivity
=====================
Simple test script to verify Gemini API connection and response format
using call_llm from backend.core.llm_client.

Usage:
    python backend/tests/test_llm_connection.py
"""

import os
import sys
import json
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.llm_client import call_llm, get_llm_credentials


def test_llm_connectivity():
    print("=" * 60)
    print("  Testing Gemini LLM Connectivity")
    print("=" * 60)

    # 1. Check API Key
    creds = get_llm_credentials()
    api_key = creds.get("gemini_key")
    if not api_key:
        print("[ERROR] No GEMINI_API_KEY found in environment or .env file!")
        print("Please ensure GEMINI_API_KEY is set in your .env file.")
        return False

    masked_key = api_key[:4] + "..." + api_key[-4:] if len(api_key) > 8 else "***"
    model = os.getenv("EXTRACTOR_MODEL", "gemini-1.5-flash")
    print(f"Model   : {model}")
    print(f"API Key : {masked_key}")
    print("-" * 60)

    # 2. Make simple test call
    test_prompt = "Return a JSON object with keys 'status' (value 'connected') and 'message' (value 'Gemini is operational')."
    system_instruction = "You are a connectivity test assistant. Respond ONLY in valid JSON format."

    print("Sending test request to Gemini...")
    response = call_llm(test_prompt, system_instruction=system_instruction)

    if not response:
        print("[FAILED] Received no response from Gemini.")
        return False

    print(f"\n[RAW RESPONSE]:\n{response}\n")

    # 3. Validate JSON output
    try:
        data = json.loads(response)
        print("[PARSED JSON SUCCESS]:")
        print(json.dumps(data, indent=2))
        print("\n--> Gemini LLM is successfully connected and responding!")
        return True
    except json.JSONDecodeError as e:
        print(f"[WARNING] Response was received but failed JSON parse: {e}")
        return False


if __name__ == "__main__":
    success = test_llm_connectivity()
    sys.exit(0 if success else 1)
