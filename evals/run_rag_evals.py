"""RAG eval runner (phase 2 step 6; design PHASE2_PLAN Q10-Q12).

Run from the project root (WSL Postgres must be up for retrieval):
    .venv/Scripts/python.exe evals/run_rag_evals.py [--limit N] [--skip-ragas]

Two layers, in order:
  A. Every case runs through the REAL agent; the deterministic layer
     (recall@k, cited-subset-of-retrieved, refusal honesty) is scored and
     written incrementally as JSONL, resume-friendly like run_evals.py.
  B. Unless --skip-ragas, the judged layer runs RAGAS (faithfulness,
     answer relevancy, context precision, context recall) on the judged
     categories only (Q12), with the judge model id recorded.
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
from flightintel.config import load_settings, vector_backend
from flightintel.db import open_products_db
from flightintel.prompts import PROMPT_VERSION
from flightintel.ragscore import RagEvalCase, extract_doc_contexts, score_rag_case

RESULTS_DIR = Path("evals/results")
INDEX_MANIFEST = Path("corpus/index_manifest.json")
JUDGED_CATEGORIES = {"single-source", "cross-doc", "caveat", "seed-finding"}
MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 20


def load_cases(path: Path) -> list[RagEvalCase]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [RagEvalCase(**c) for c in data["cases"]]


def is_rate_limit(exc: Exception) -> bool:
    # "rate limit" (not bare "rate"): see run_evals.py, the 2026-08-12
    # gemini-2.5-pro 404 that matched "rate" inside "migrate".
    text = str(exc)
    return "429" in text or "RESOURCE_EXHAUSTED" in text or "rate limit" in text.lower()


def run_case_with_backoff(graph, case: RagEvalCase, settings, trace_metadata=None):
    for attempt in range(MAX_ATTEMPTS):
        try:
            return ask(graph, case.question, settings, trace_metadata), None
        except Exception as exc:  # backoff only on quota; anything else is real
            if not is_rate_limit(exc) or attempt == MAX_ATTEMPTS - 1:
                return None, exc
            wait = BACKOFF_BASE_SECONDS * (2**attempt) + random.uniform(0, 5)
            print(f"    429 -> backing off {wait:.0f}s (attempt {attempt + 1})")
            time.sleep(wait)
    return None, RuntimeError("unreachable")


def run_phase_a(args, settings, cases, out_path: Path, done: set) -> None:
    graph = build_agent(settings)
    pending = [c for c in cases if c.id not in done]
    if args.limit:
        pending = pending[: args.limit]
    print(
        f"{len(pending)} case(s) to run -> {out_path} "
        f"[{settings.gemini_model} | prompt {PROMPT_VERSION}]"
    )
    with out_path.open("a", encoding="utf-8") as out:
        for i, case in enumerate(pending):
            started = time.monotonic()
            # Trace tags (PHASE3_PLAN Q2): eval traffic filters cleanly
            # from interactive traffic in the Langfuse UI.
            meta = {
                "langfuse_tags": [
                    "eval",
                    f"dataset:{Path(args.dataset).stem}",
                    f"case:{case.id}",
                    f"prompt:{PROMPT_VERSION}",
                    f"backend:{vector_backend(settings) or 'none'}",
                ]
            }
            result, exc = run_case_with_backoff(graph, case, settings, meta)
            duration = round(time.monotonic() - started, 1)

            if result is None:
                row = {
                    "id": case.id, "category": case.category,
                    "status": "rate_limited" if is_rate_limit(exc) else "error",
                    "error": str(exc)[:500],
                    "model": settings.gemini_model,
                    "prompt_version": PROMPT_VERSION, "duration_s": duration,
                }
                print(f"[{i + 1}/{len(pending)}] {case.id}: {row['status'].upper()}")
            else:
                answer, messages = result
                score = score_rag_case(
                    case,
                    answer_text=answer.answer,
                    docs_retrieved=answer.docs_retrieved,
                    docs_cited=answer.docs_cited,
                    sql_used=answer.sql_used,
                )
                contexts = extract_doc_contexts(messages)
                row = {
                    "id": case.id, "category": case.category, "status": "scored",
                    "passed": score.passed, "detail": score.detail,
                    "recall_at_k": score.recall, "invented": score.invented,
                    "question": case.question, "reference": case.reference,
                    "gold_sources": case.gold_sources,
                    "answer": answer.answer,
                    "docs_retrieved": answer.docs_retrieved,
                    "docs_cited": answer.docs_cited,
                    "contexts": [c.model_dump() for c in contexts],
                    "sql_used": answer.sql_used,
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


def run_phase_b(rows: list[dict], settings, judge_model: str, out_path: Path):
    """RAGAS over the judged categories; returns the sidecar dict."""
    import ragas_compat  # noqa: F401  (must precede ragas; see its docstring)
    import ragas
    from langchain_google_genai import (
        ChatGoogleGenerativeAI,
        GoogleGenerativeAIEmbeddings,
    )
    from ragas import evaluate
    from ragas.dataset_schema import EvaluationDataset, SingleTurnSample
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import AnswerRelevancy, ContextPrecision, Faithfulness

    try:
        from ragas.metrics import LLMContextRecall as ContextRecallMetric
    except ImportError:
        from ragas.metrics import ContextRecall as ContextRecallMetric

    judged = [
        r for r in rows
        if r["status"] == "scored"
        and r["category"] in JUDGED_CATEGORIES
        and r["contexts"]
    ]
    if not judged:
        print("No judged-category rows with contexts; skipping RAGAS.")
        return None

    samples = [
        SingleTurnSample(
            user_input=r["question"],
            retrieved_contexts=[c["text"] for c in r["contexts"]],
            response=r["answer"],
            reference=r["reference"],
        )
        for r in judged
    ]
    api_key = settings.gemini_api_key.get_secret_value()
    llm = LangchainLLMWrapper(
        ChatGoogleGenerativeAI(model=judge_model, temperature=0, google_api_key=api_key)
    )
    embeddings = LangchainEmbeddingsWrapper(
        GoogleGenerativeAIEmbeddings(
            model=f"models/{settings.embedding_model}", google_api_key=api_key
        )
    )
    print(f"RAGAS: {len(samples)} sample(s), judge={judge_model} ...")
    result = evaluate(
        dataset=EvaluationDataset(samples=samples),
        metrics=[Faithfulness(), AnswerRelevancy(), ContextPrecision(),
                 ContextRecallMetric()],
        llm=llm,
        embeddings=embeddings,
    )
    df = result.to_pandas()
    meta_cols = {"user_input", "retrieved_contexts", "response", "reference"}
    metric_cols = [c for c in df.columns if c not in meta_cols]
    per_case = {
        judged[i]["id"]: {
            c: (None if df.iloc[i][c] != df.iloc[i][c] else float(df.iloc[i][c]))
            for c in metric_cols
        }
        for i in range(len(judged))
    }
    means = {
        c: round(float(df[c].mean()), 4) for c in metric_cols
        if df[c].dtype.kind in "fi"
    }
    sidecar = {
        "judge_model": judge_model,
        "ragas_version": ragas.__version__,
        "means": means,
        "per_case": per_case,
    }
    sidecar_path = out_path.with_suffix(".ragas.json")
    sidecar_path.write_text(
        json.dumps(sidecar, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"RAGAS sidecar -> {sidecar_path}")
    return sidecar


def write_manifest(out_path: Path, settings, args, judge_model: str | None) -> None:
    index = json.loads(INDEX_MANIFEST.read_text(encoding="utf-8"))
    manifest = {
        "dataset": args.dataset,
        "model": settings.gemini_model,
        "prompt_version": PROMPT_VERSION,
        "judge_model": judge_model,
        "embedding_model": index["embedding_model"],
        "corpus_version": index["corpus_version"],
        # A retrieval result that does not say which store served it
        # cannot be compared honestly (PHASE4_PLAN Q2c).
        "vector_backend": vector_backend(settings) or "none",
        "started_utc": datetime.now(timezone.utc).isoformat(),
    }
    path = out_path.with_suffix(".manifest.json")
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Run manifest -> {path}")


def write_summary(out_path: Path, ragas_sidecar: dict | None) -> None:
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
        f"# RAG eval summary: {out_path.name}",
        "",
        f"- Model: {rows[0]['model']} | prompt {rows[0]['prompt_version']}"
        if rows else "",
        f"- Cases scored: {len(scored)} / {len(rows)}",
        f"- Total LLM requests: {sum(r.get('llm_requests', 0) for r in scored)}, "
        f"tokens in={sum(r.get('input_tokens', 0) for r in scored)} "
        f"out={sum(r.get('output_tokens', 0) for r in scored)}",
        "",
        "## Deterministic layer",
        "",
        "| category | passed | total | mean recall@k |",
        "|----------|--------|-------|---------------|",
    ]
    total_pass = 0
    for cat in sorted(by_cat):
        group = by_cat[cat]
        passed = sum(1 for r in group if r["passed"])
        total_pass += passed
        recalls = [r["recall_at_k"] for r in group if r["recall_at_k"] is not None]
        mean_recall = f"{sum(recalls) / len(recalls):.2f}" if recalls else "n/a"
        lines.append(f"| {cat} | {passed} | {len(group)} | {mean_recall} |")
    if scored:
        lines.append(f"| **all** | {total_pass} | {len(scored)} | |")
    if ragas_sidecar:
        lines += [
            "",
            f"## Judged layer (judge: {ragas_sidecar['judge_model']}, "
            f"ragas {ragas_sidecar['ragas_version']})",
            "",
            "| metric | mean |",
            "|--------|------|",
            *(
                f"| {name} | {value:.3f} |"
                for name, value in ragas_sidecar["means"].items()
            ),
        ]
    lines += [
        "",
        "Failures (deterministic):",
        *(f"- {r['id']}: {r['detail']}" for r in scored if not r["passed"]),
    ]
    summary_path = out_path.with_suffix(".summary.md")
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nSummary -> {summary_path}")
    for line in lines:
        print(line)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="evals/datasets/rag_v1.yaml")
    parser.add_argument("--delay", type=float, default=3.0,
                        help="Seconds between cases.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Run only the first N pending cases (smoke).")
    parser.add_argument("--only", default=None, help="Comma-separated case ids.")
    parser.add_argument("--resume", default=None,
                        help="Existing results .jsonl to continue into.")
    parser.add_argument("--skip-ragas", action="store_true",
                        help="Deterministic layer only (no judge calls).")
    parser.add_argument("--judge-model", default=None,
                        help="Judge model id (default: the dev model).")
    parser.add_argument("--model", default=None,
                        help="Agent model id override (final pass, Q6).")
    args = parser.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8")
    settings = load_settings()
    if args.model:
        settings = settings.model_copy(update={"gemini_model": args.model})
    open_products_db(settings.flight_db_path).close()  # drift check up front
    judge_model = args.judge_model or settings.gemini_model

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
        out_path = RESULTS_DIR / f"{stamp}_rag_{settings.gemini_model}_{PROMPT_VERSION}.jsonl"
        done = set()

    write_manifest(out_path, settings, args,
                   None if args.skip_ragas else judge_model)
    run_phase_a(args, settings, cases, out_path, done)

    rows = [
        json.loads(line)
        for line in out_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    sidecar = None
    if not args.skip_ragas:
        sidecar = run_phase_b(rows, settings, judge_model, out_path)
    write_summary(out_path, sidecar)


if __name__ == "__main__":
    main()
