"""Eval runner: executes the dataset against the live agent and scores it.

Run from the project root:
    .venv/Scripts/python.exe evals/run_evals.py [--limit N] [--resume FILE]

Free-tier discipline (under-the-hood.md entry 2): one case at a time, a
delay between cases, exponential backoff with jitter on 429, results
written incrementally as JSONL so an interrupted run resumes tomorrow
without redoing finished cases.
"""

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from flightintel.agent import ask, build_agent
from flightintel.config import load_settings
from flightintel.db import open_products_db
from flightintel.evalscore import EVAL_SUFFIX, EvalCase, score_case
from flightintel.prompts import PROMPT_VERSION

RESULTS_DIR = Path("evals/results")
MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 20


def load_cases(path: Path) -> list[EvalCase]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [EvalCase(**c) for c in data["cases"]]


def is_rate_limit(exc: Exception) -> bool:
    # "rate limit" (not bare "rate"): a 404 whose message contained
    # "migrate-to-interactions" once matched "rate" and burned 4 backoff
    # attempts per case (2026-08-12, the gemini-2.5-pro incident).
    text = str(exc)
    return "429" in text or "RESOURCE_EXHAUSTED" in text or "rate limit" in text.lower()


def run_case_with_backoff(graph, case: EvalCase, settings, trace_metadata=None):
    for attempt in range(MAX_ATTEMPTS):
        try:
            return ask(
                graph, case.question + EVAL_SUFFIX, settings, trace_metadata
            ), None
        except Exception as exc:  # backoff only on quota; anything else is real
            if not is_rate_limit(exc) or attempt == MAX_ATTEMPTS - 1:
                return None, exc
            wait = BACKOFF_BASE_SECONDS * (2**attempt) + random.uniform(0, 5)
            print(f"    429 -> backing off {wait:.0f}s (attempt {attempt + 1})")
            time.sleep(wait)
    return None, RuntimeError("unreachable")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="evals/datasets/phase1.yaml")
    parser.add_argument("--delay", type=float, default=10.0,
                        help="Seconds between cases (RPM budget).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Run only the first N pending cases (smoke).")
    parser.add_argument("--only", default=None, help="Comma-separated case ids.")
    parser.add_argument("--resume", default=None,
                        help="Existing results .jsonl to continue into.")
    parser.add_argument("--model", default=None,
                        help="Agent model id override (final pass, Q6).")
    args = parser.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8")
    settings = load_settings()
    if args.model:
        settings = settings.model_copy(update={"gemini_model": args.model})
    con = open_products_db(settings.flight_db_path)  # ground truth + drift check
    graph = build_agent(settings)
    cases = load_cases(Path(args.dataset))
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c.id in wanted]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.resume:
        out_path = Path(args.resume)
        done = {
            json.loads(line)["id"]
            for line in out_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        dataset_tag = Path(args.dataset).stem
        out_path = (
            RESULTS_DIR
            / f"{stamp}_{dataset_tag}_{settings.gemini_model}_{PROMPT_VERSION}.jsonl"
        )
        done = set()

    pending = [c for c in cases if c.id not in done]
    if args.limit:
        pending = pending[: args.limit]
    print(
        f"{len(pending)} case(s) to run -> {out_path} "
        f"[{settings.gemini_model} | prompt {PROMPT_VERSION}]"
    )

    with out_path.open("a", encoding="utf-8") as out:
        for i, case in enumerate(pending):
            expected = None
            if case.ground_truth_sql:
                expected = con.execute(case.ground_truth_sql).fetchone()[0]

            started = time.monotonic()
            meta = {
                "langfuse_tags": [
                    "eval",
                    f"dataset:{Path(args.dataset).stem}",
                    f"case:{case.id}",
                    f"prompt:{PROMPT_VERSION}",
                ]
            }
            result, exc = run_case_with_backoff(graph, case, settings, meta)
            duration = round(time.monotonic() - started, 1)

            if result is None:
                row = {
                    "id": case.id, "category": case.category,
                    "status": "rate_limited" if is_rate_limit(exc) else "error",
                    "error": str(exc)[:500], "expected": expected,
                    "model": settings.gemini_model,
                    "prompt_version": PROMPT_VERSION, "duration_s": duration,
                }
                print(f"[{i + 1}/{len(pending)}] {case.id}: {row['status'].upper()}")
            else:
                answer, _messages = result
                score = score_case(case, expected, answer.answer, answer.sql_used)
                row = {
                    "id": case.id, "category": case.category, "status": "scored",
                    "passed": score.passed, "detail": score.detail,
                    "expected": expected, "final_line": score.final_line,
                    "answer": answer.answer, "sql_used": answer.sql_used,
                    "could_answer": answer.could_answer,
                    "llm_requests": answer.llm_requests,
                    "input_tokens": answer.input_tokens,
                    "output_tokens": answer.output_tokens,
                    "model": answer.model_id, "prompt_version": answer.prompt_version,
                    "duration_s": duration,
                }
                mark = "PASS" if score.passed else "FAIL"
                print(f"[{i + 1}/{len(pending)}] {case.id}: {mark} ({score.detail})")
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            if i < len(pending) - 1:
                time.sleep(args.delay)

    write_summary(out_path)


def write_summary(out_path: Path) -> None:
    rows = [
        json.loads(line)
        for line in out_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    scored = [r for r in rows if r["status"] == "scored"]
    by_cat: dict[str, list] = {}
    for r in scored:
        by_cat.setdefault(r["category"], []).append(r)

    lines = [
        f"# Eval summary: {out_path.name}",
        "",
        f"- Model: {rows[0]['model']} | prompt {rows[0]['prompt_version']}"
        if rows else "",
        f"- Cases scored: {len(scored)} / {len(rows)} "
        f"(unscored = rate-limited or errored)",
        f"- Total LLM requests: {sum(r.get('llm_requests', 0) for r in scored)}, "
        f"tokens in={sum(r.get('input_tokens', 0) for r in scored)} "
        f"out={sum(r.get('output_tokens', 0) for r in scored)}",
        "",
        "| category | passed | total | accuracy |",
        "|----------|--------|-------|----------|",
    ]
    total_pass = 0
    for cat in sorted(by_cat):
        group = by_cat[cat]
        passed = sum(1 for r in group if r["passed"])
        total_pass += passed
        lines.append(
            f"| {cat} | {passed} | {len(group)} | {passed / len(group):.0%} |"
        )
    if scored:
        lines.append(
            f"| **all** | {total_pass} | {len(scored)} | "
            f"{total_pass / len(scored):.0%} |"
        )
    lines += [
        "",
        "Failures:",
        *(
            f"- {r['id']}: {r['detail']}"
            for r in scored
            if not r["passed"]
        ),
    ]
    summary_path = out_path.with_suffix(".summary.md")
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nSummary -> {summary_path}")
    for line in lines:
        print(line)


if __name__ == "__main__":
    main()
