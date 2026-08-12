"""Probe a model id before any run.

The phantom-model rule (PHASE3_PLAN Q6, amended): the model LIST is
advertising; one real generateContent call is the contract. Run this
before pointing any eval run or deployment at a model id.

Usage (from the project root):
  .venv\\Scripts\\python.exe scripts\\probe_model.py
  .venv\\Scripts\\python.exe scripts\\probe_model.py --model gemini-2.5-pro
  .venv\\Scripts\\python.exe scripts\\probe_model.py --list
"""

from __future__ import annotations

import argparse
import json
import urllib.request

from flightintel.config import load_settings

LIST_URL = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"


def list_model_names(api_key: str) -> list[str]:
    # Key goes in a header, not the URL, so it cannot leak into logs.
    req = urllib.request.Request(LIST_URL, headers={"x-goog-api-key": api_key})
    with urllib.request.urlopen(req) as resp:
        data = json.load(resp)
    return sorted(
        m["name"].removeprefix("models/")
        for m in data.get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="probe a Gemini model id")
    parser.add_argument("--model", default=None,
                        help="model id to probe (default: the pinned dev model)")
    parser.add_argument("--list", action="store_true",
                        help="list generateContent-capable models instead of probing")
    args = parser.parse_args()

    settings = load_settings()
    if settings.gemini_api_key is None:
        raise SystemExit("GEMINI_API_KEY is not set in .env.")
    api_key = settings.gemini_api_key.get_secret_value()

    if args.list:
        names = list_model_names(api_key)
        print(f"{len(names)} generateContent-capable models on this key:")
        for name in names:
            print(f"  {name}")
        return

    model_id = args.model or settings.gemini_model
    # Deferred import: the probe path should stay fast when only listing.
    from langchain_google_genai import ChatGoogleGenerativeAI

    llm = ChatGoogleGenerativeAI(
        model=model_id, temperature=0, google_api_key=api_key
    )
    reply = llm.invoke("Reply with exactly: OK")
    print(f"model: {model_id}")
    print(f"reply: {reply.content!r}")
    print(f"usage: {getattr(reply, 'usage_metadata', None)}")
    print("PROBE PASS: this model id serves generateContent on this key.")


if __name__ == "__main__":
    main()
