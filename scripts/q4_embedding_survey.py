"""Q4 evidence gathering, run against the live Gemini API.

1. Lists the model ids the API actually serves that support embedContent
   (phase 1 lesson: never assume an id from documentation).
2. Counts real tokens for the largest corpus chunk to check the chars/4
   estimate the Q3 cap was sized with.

Reads GEMINI_API_KEY via the project settings; prints no secrets.
Run from the project root: .venv/Scripts/python.exe scripts/q4_embedding_survey.py
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, "src")
from flightintel.config import load_settings

BASE = "https://generativelanguage.googleapis.com/v1beta"


def api(path: str, key: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{BASE}/{path}",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        data=json.dumps(payload).encode() if payload else None,
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main() -> None:
    key = load_settings().gemini_api_key.get_secret_value()

    models: list[dict] = []
    page = api("models?pageSize=200", key)
    models.extend(page.get("models", []))
    while token := page.get("nextPageToken"):
        page = api(f"models?pageSize=200&pageToken={token}", key)
        models.extend(page.get("models", []))

    embedders = [
        m for m in models if "embedContent" in m.get("supportedGenerationMethods", [])
    ]
    print(f"API serves {len(models)} models; {len(embedders)} support embedContent:\n")
    for m in embedders:
        print(
            f"  {m['name']:40s} inputTokenLimit={m.get('inputTokenLimit')}"
            f"  ({m.get('displayName', '')})"
        )

    chunks = [
        json.loads(line)
        for line in Path("corpus/chunks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    biggest = max(chunks, key=lambda c: c["chars"])
    embed_text = f"{biggest['breadcrumb']}\n\n{biggest['text']}"
    est = len(embed_text) // 4
    print(f"\nLargest chunk: {biggest['id']} ({biggest['chars']} chars)")
    print(f"chars/4 estimate for breadcrumb + text: {est} tokens")

    for m in embedders:
        try:
            counted = api(
                f"{m['name']}:countTokens",
                key,
                {"contents": [{"parts": [{"text": embed_text}]}]},
            )
            real = counted.get("totalTokens")
            ratio = est / real if real else float("nan")
            print(f"real tokenizer ({m['name']}): {real} tokens (estimate/real = {ratio:.2f})")
        except urllib.error.HTTPError as e:
            print(f"countTokens not available for {m['name']}: HTTP {e.code}")

    print()
    for m in embedders:
        if "preview" in m["name"]:
            continue
        try:
            out = api(
                f"{m['name']}:embedContent",
                key,
                {"content": {"parts": [{"text": "dimension probe"}]}},
            )
            dim = len(out["embedding"]["values"])
            print(f"default output dimensionality ({m['name']}): {dim}")
        except urllib.error.HTTPError as e:
            print(f"embedContent failed for {m['name']}: HTTP {e.code}")


if __name__ == "__main__":
    main()
