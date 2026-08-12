# flight-intel-agent

An AI application layer over the [AirflowOSky](https://github.com/MawingMawet/AirflowOSky)
data platform. AirflowOSky (separate repo) ingests live OpenSky flight data
over Bangkok and builds data products; this repo consumes those products
read-only and adds a Gen AI layer: natural-language analytics, automated
data-quality narration, and (later) semantic search over reference data and
project documentation.

The split mirrors a real organization: a platform/data team owns the
pipeline, an AI/application team owns the agent, and the contract between
them is the products database schema, not shared code (ADR 0001).

## Status

All four build phases are complete. The agent is live on Cloud Run:

**https://flightintel-464910078459.asia-southeast1.run.app** - `GET
/health`, interactive docs at `/docs`, `POST /ask` with
`{"question": "..."}` (expect tens of seconds; one question is several
LLM round trips).

This is the public release of the project; day-to-day development
happens in a private working repo (plans, session logs, and decision
records live there). Data derives from
[The OpenSky Network](https://opensky-network.org) (research /
non-commercial use).

| Phase | Deliverable                                                        | Status  |
|-------|--------------------------------------------------------------------|---------|
| 0     | Repo + docs skeleton, governance files                             | done    |
| 1     | LangGraph agent core (SQL path) + trap-eval harness (SQLite)       | done    |
| 2     | RAG path: pgvector, embeddings, docs corpus with citations, RAGAS  | done    |
| 3     | Langfuse tracing + FastAPI + Docker                                | done    |
| 4     | GCP deployment: Cloud Run + BigQuery vector search, traced         | done    |

New reader? Start at docs/USER_GUIDE.md, then docs/PRESENTATION.md.

## Eval results (phase 1, final)

Accuracy per trap category, `gemini-3.5-flash`, 20 cases, 2026-08-10.
Prompt v1 (morning, original dataset) vs prompt v2 (evening: hour-format +
conversion recipe, step-budget discipline, one-query coverage checks;
clean full run on the repaired dataset, see PHASE1_PLAN Q11 for the
repair):

| category | prompt v1 | prompt v2 |
|----------|-----------|-----------|
| baseline | 100%      | 100%      |
| grain    | 100%      | 100%      |
| gate     | 67%       | 100%      |
| measure  | 67%       | 100%      |
| grid     | 0%        | 100%      |
| timezone | 0%        | 67%       |
| refusal  | 67%       | 67%       |
| **all**  | 60%       | 90%      |

The two v2 failures share one root cause, conclusion paralysis: the trace
shows the model computing the correct evidence (the right converted-hours
SUM for tz_03, a proven coverage gap for refuse_01), then re-verifying
until the step budget aborts it. refuse_01 fails this way in every run;
tz_03 is stochastic - it passed a same-day targeted re-run at identical
settings (temperature 0 is not determinism; under-the-hood.md entry 3).
Both failures are honest aborts, not wrong numbers. A v3 prompt
experiment with hard sequencing rules (PHASE1_PLAN Q12) left the
sentinel cases clean but still failed refuse_01 identically, and was
reverted per its pre-registered decision rule: refuse_01 stands as a
recorded model-capability finding, not a patched-away failure. Cost per
full run: ~90 LLM requests, ~230k input tokens (evals/results/ has
per-case JSONL).

## Eval results (phase 2, RAG path, 2026-08-12)

All runs: `gemini-3.5-flash`, prompt v3, Flash judge, ragas 0.4.3;
per-run manifests in evals/results/ pin every version.

| measure | corpus v1 (12 cases) | corpus v2 (13 cases) |
|---|---|---|
| deterministic pass | 9/12 | 9/13 |
| invented citations | 0 | 0 |
| RAGAS faithfulness | 0.870 | 0.849 |
| RAGAS answer relevancy | 0.891 | 0.902 |
| RAGAS context precision | 0.928 | 0.928 |
| RAGAS context recall | 0.927 | 0.898 |
| seed probe: gold rank (raw) | 9th | **1st** |
| seed_02 context precision | 0.143 | **1.000** |

The headline: corpus v2 (bold-label Q&A re-chunking) moved the recorded
retrieval failure from rank 9 to rank 1 and its context precision from
0.143 to 1.000, with no metric collapsing elsewhere. The remaining
failures are recorded findings, not bugs: the not-covered honesty gap
(adjacency bait), corpus-negative paralysis, and a gold-as-AND eval
design flaw. Trap v2 (SQL path): coverage-proof 0/3 reproduces the
phase 1 refuse_01 model finding with a new proof-query check; phase 1
regression under prompt v3: 16/20 with abort variance recorded
(phase 2 plan, step 7 results). Full findings: docs/PRESENTATION.md.

## Final pass: the generation retest (phase 3, 2026-08-12)

The whole eval suite re-run with the agent model as the only changed
variable (judge pinned to gemini-3.5-flash; every case traced in
Langfuse; manifests pin the rest):

| suite | gemini-3.5-flash | gemini-3.6-flash |
|---|---|---|
| SQL traps (20 cases) | 90% (prompt v2) | **100%** |
| refuse_01 (the standing finding) | failed every run | **passed** |
| RAG deterministic (13 cases) | 9/13 | **11/13** |
| not-covered honesty (adjacency bait) | 0/2 | **2/2** |
| RAGAS means (same Flash judge) | .849/.902/.928/.898 | .869/.880/.967/.935 |

The recorded model-capability findings behaved exactly as findings
should: the paralysis family (would not commit to proven negatives)
resolved 2.5 of 3 members with one model generation, confirming it was
a model capability, not a prompt problem. The two remaining RAG
failures are the recorded gold-as-AND eval design flaw and fail
identically on both models. Remaining open: one coverage-proof abort
(cp_02), and the CLARIFY retest awaits a servable pro-class model
(gemini-2.5-pro is listed but 404s for new accounts - the phantom-model
incident, PHASE3_PLAN Q6).

## Cloud re-proof: pgvector vs BigQuery vector search (phase 4, 2026-08-13)

The cloud deployment swaps the vector store behind the unchanged
search_docs contract; the vectors travel by export (never re-embedded),
so the comparison isolates the engine. Same 13 cases, same model, same
judge, corpus v2 pinned:

| measure | pgvector (local) | BigQuery (cloud backend) |
|---|---|---|
| deterministic pass | 9/13 | 9/13 (identical failure set) |
| RAGAS context precision | 0.928 | 0.965 |
| RAGAS context recall | 0.898 | 0.935 |
| fixed-query rank parity | - | same top-5, distances equal to 4 decimals |
| cost per doc search | a WSL Postgres kept alive | ~$0.00006 (10 MB billing minimum; $0 in free tier) |

Verdict in ADR 0010: the engines are interchangeable at this corpus
size; the choice is decided by cost and operational shape, not
retrieval quality. The deployed smoke answered a knowledge question
with a valid citation in 11.5s / 3 LLM requests, trace in Langfuse
tagged env:cloud.

## Repo layout

```
docs/                              start here: user guide, architecture, presentation
src/                               agent code (LangGraph loop, typed tools, RAG, API)
evals/                             eval datasets + harness + result summaries
evals/fixtures/sample.db           committed real-data subset; set FLIGHT_DB_PATH
                                   to it to run without the upstream repo
corpus/                            corpus + index manifests (versioned retrieval builds)
scripts/                           setup and deploy scripts (WSL Postgres, Docker, GCP)
```

## Design in one paragraph

The agent is a LangGraph tool-calling loop (ADR 0005), typed at every
boundary with Pydantic, with Gemini as the dev model behind LangChain's
model-agnostic chat interface (ADR 0006). It reads the upstream SQLite
`products.db` read-only via `FLIGHT_DB_PATH` (ADR 0003), through a small tool
set: schema-with-grain-sentences, guarded SELECT-only SQL, build-audit lookup
(both audit tables), and airport resolution. An eval harness ships in the
same week as the agent, built around trap categories derived from the
upstream data contract: grain traps, timezone (UTC vs. Bangkok +7) traps,
gate traps, measure traps (obs_count vs. aircraft_count), grid traps
(rounded grids do not nest), and refusal traps. Phase 2 adds the RAG path:
pgvector, embeddings over both repos' committed docs with citations, and a
RAGAS evaluation pass.

See `docs/ARCHITECTURE_OVERVIEW.md` for the full design and roadmap.
