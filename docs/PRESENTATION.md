# FlightIntelAgent - presentation walkthrough

Status: v2, updated 2026-08-13 (phase 4 deployed: Cloud Run + BigQuery
vector search, all four phases complete). The companion to ARCHITECTURE_OVERVIEW.md: that document
describes the system, this one tells its story. Written to be spoken
from: a demo path, the narrative beats, the findings, the numbers, and
the questions to expect. The reading flow for a newcomer is
USER_GUIDE.md (hands-on) -> this file (the story) ->
ARCHITECTURE_OVERVIEW.md (the design, including the decision index).

## The pitch (30 seconds)

FlightIntelAgent is the Gen AI application layer over the AirflowOSky
data platform: a LangGraph tool-calling agent that answers plain-English
questions about Bangkok airspace two ways. Data questions become live,
guarded SQL against the flight products; knowledge questions become
semantic search over both projects' committed documentation, answered
with citations that resolve to real, retrieved chunks. Everything is
measured: a trap dataset built from the data contract's sharp edges for
the SQL path, RAGAS plus deterministic citation checks for the RAG path,
and every run records the model, prompt, judge, and corpus versions that
produced it. The findings are real and kept: a measured model weakness
in three costumes, a classic RAG failure reproduced in-house, and an
instrument incident that cost 125 requests and taught more than it cost.
Since phase 4 the same agent runs serverless on Google Cloud Run behind
a public URL, with doc search served by BigQuery vector search over the
exact same vectors as the local store - a swap proven by a side-by-side
retrieval re-proof, not assumed.

## The demo path (in order)

| step | show | say | it proves |
| --- | --- | --- | --- |
| 0 | the public URL: /health, then POST /ask from any machine | no setup, no keys, a cited answer in ~10-20s; /health reports every dependency honestly | the whole system ships: serverless, scale-to-zero, secrets in Secret Manager, data snapshot versioned into the image |
| 1 | CLI data question with `--trace` | the raw message list, annotated: the model emits tool calls, the app executes, results append, no tool call means stop | an agent is a plain loop over a stateless API; the trace is the debugging surface |
| 2 | a knowledge question ("why do vectors live in pgvector?") | the answer cites chunk ids returned by search_docs this run; the CLI lists cited docs and flags any citation that was never retrieved | citations are structural, not generative; hallucinated citations fail deterministically |
| 3 | stop the WSL Postgres, re-ask | the agent still answers SQL questions and says the docs could not be searched | degradation is designed and tested, not accidental |
| 4 | `evals/results/`: a summary table and a run manifest | per-category accuracy, RAGAS means, and the manifest pinning model, prompt, judge, corpus, dataset | evals are regression tests for behavior; unpinned numbers are anecdotes |
| 5 | the Langfuse dashboard (us.cloud.langfuse.com): filter traces by tag `eval`, open one | the span tree: AGENT/CHAIN structure, GENERATION nodes with tokens and latency, TOOL executions | the trace is the debugging surface; it even caught an API call our own client accounting missed |
| 6 | `docker compose up` inside WSL, then /health and /ask from Windows | the same agent, containerized: app-only image, DB mounted read-only, secrets at runtime | serving is decoupled and shippable; the image is phase 4's deployment input |
| 7 | BigQuery console: query history, open one VECTOR_SEARCH job | bytes billed 10 MB (the on-demand minimum) against ~3 MB scanned; first 1 TiB/month free | the cloud vector store costs ~$0.00006 per search on paper and $0 in practice - a cost claim you can verify in the console, not a slide number |

## The story, in five beats

1. **The data contract is the spine.** The upstream schema is pinned and
   asserted at startup; the contract's documented caveats (grain, UTC,
   gates, measures, sparse coverage) become the system prompt's rules
   AND the eval dataset's trap categories. The AI layer inherits its
   shape from the data platform's guarantees (ADR 0001).
2. **The framework owns only the loop.** LangGraph runs the ReAct-style
   loop; the five tools are framework-independent, Pydantic-typed
   functions with a two-layer error contract. The abstraction cost is
   paid deliberately: a ledger maps the framework onto the raw wire
   format, and every run is traceable (ADR 0005).
3. **Evals ship with features.** The agent and its trap dataset landed
   the same week; the RAG path and its RAGAS harness landed the same
   day. A prompt or model change without an eval run is not done, and
   an honest refusal scores as a correct answer.
4. **RAG honesty is structural.** The corpus is a pinned, versioned
   snapshot (only git-tracked files, so private notes are structurally
   excluded); citations are chunk ids the tool returned this run; the
   harness asserts cited is a subset of retrieved before any LLM judge
   opines. Across every recorded run: zero invented citations.
5. **Improvement is measured or it did not happen.** Prompt v1 to v2:
   60% to a confirmed 90% on the SQL traps. Corpus v1 to v2: the seed
   probe's gold source moved from rank 9 to rank 1 by splitting one
   diluted chunk. And two experiments FAILED their pre-registered rules
   and were reverted, results committed: that findings column having
   entries is what makes the green numbers credible.

## Findings only real measurement gives

- **The paralysis trilogy.** One model weakness in three costumes: it
  proves a date uncovered and keeps verifying instead of refusing
  (refuse_01), retrieves 13 chunks and keeps searching instead of
  saying "not covered" (nc_02), and given a clarify option it
  investigates every reading instead of asking (the CLARIFY incident).
  Commitment, not knowledge, is the gap; it survived prompt statements,
  a sequencing constraint, and two suffix wordings.
- **The adjacency bait.** Asked about GPS spoofing (which no doc
  covers), the model built a confident answer from adjacent
  data-validation chunks, with perfectly valid citations. The classic
  production RAG failure, reproduced and measured in-house: honesty
  rules lose to helpfulness pressure exactly when retrieval returns
  near-topic content.
- **Wording sensitivity is real.** Twin questions asking the same thing
  rank the same gold chunk 1st or 9th depending on phrasing; the agent
  partially rescues weak retrieval by reformulating queries, at a
  context-precision cost the judge can see (0.143 on the rescued case).
- **The instrument can break the system it measures.** Adding one
  answer-form option to the eval suffix collapsed the agent into
  step-limit aborts on every case, including trivial ones. Caught by
  sentinels, killed mid-run, diagnosed from SQL trails, reverted by a
  pre-registered rule. Same lesson at smaller scale in phase 1: the
  neutral suffix wording exists because an earlier wording invited
  self-verification.
- **Temperature 0 is not determinism.** Paralysis aborts move between
  cases run to run with everything pinned; single-run flips are
  variance until they repeat.

## Numbers to remember (phases 1-4, as of 2026-08-13)

| number | meaning |
| --- | --- |
| 60% -> 90% | SQL trap accuracy, prompt v1 -> v2 (confirmed run, 20 cases) |
| 9/12 | RAG baseline deterministic pass, corpus v1, with all 3 failures being real findings |
| 0.870 / 0.891 / 0.928 / 0.927 | RAGAS means: faithfulness / answer relevancy / context precision / context recall (Flash judge) |
| 0 | invented citations across every recorded run |
| 9th -> 1st | seed probe's gold-source rank, corpus v1 -> v2 (the re-chunk, measured) |
| 136 -> 178 | chunks, corpus v1 -> v2 (bold-label Q&A splitting, Q14) |
| 0/3 | coverage-proof traps at baseline: refuse_01 reproduced, now with a proof-query check waiting for a better model |
| **100%** | SQL traps on gemini-3.6-flash: refuse_01's first-ever pass; the paralysis was a model capability, fixed by one generation |
| **2/2** | not-covered honesty on 3.6-flash: the adjacency bait that fooled 3.5 fooled 3.6 not at all |
| 8 vs 7 | LLM calls in the first Langfuse trace vs our client-side count: the trace caught a call our own accounting missed |
| ~350 requests, ~1.7M tokens, ~US$2 | the phase 2 measurement day; the phase 3 final pass added ~283 requests under US$1 |
| 4 decimals | fixed-query distance parity between pgvector and BigQuery on identical exported vectors: the engine swap changed nothing measurable |
| 9/13 = 9/13 | rag_v1.2 deterministic pass, pgvector vs BigQuery, identical failure set: the re-proof that let the cloud backend serve |
| 9.2s / 11.5s | Cloud Run boot-to-serving (log-bracketed) / a warm cited answer, 3 LLM requests (trace-verified) |
| ~$0.00006 | BigQuery cost per doc search at the 10 MB billing minimum; $0 inside the free tier |

## Questions to expect, and where the answers live

- *How do you prevent citation hallucination?* Structurally: the tool
  returns chunk ids, the prompt cites only returned ids, and the
  harness asserts cited is a subset of retrieved per run; the CLI
  flags violations immediately (PHASE2_PLAN Q5, Q8).
- *Why LangGraph and not a hand-rolled loop?* Role-relevant evidence in
  phase 1, one framework learned deeply; the mechanics learning moved
  into a ledger and traced runs instead of being deleted (ADR 0005).
- *Why local pgvector AND BigQuery vector search?* Different jobs:
  pgvector is the free, always-editable dev store; BigQuery serves the
  cloud with zero instances to keep alive. The swap sits behind one
  typed tool contract, selected by environment, and it was allowed to
  serve only after a side-by-side retrieval re-proof on identical
  exported vectors - never re-embedded, so any rank difference would
  have been the engine's fault alone (ADRs 0007, 0008, 0010).
- *What does the cloud demo cost to run?* Nearly nothing, by
  construction: scale-to-zero when idle, max-instances 1 as the wallet
  cap on a public URL, Gemini free tier for the LLM, BigQuery inside
  its free tier, billing alerts behind all of it. Cost per query is a
  recorded metric (tokens per answer in every response; bytes billed
  per search in the console).
- *What broke and what did you learn?* The CLARIFY suffix incident
  (instrument sensitivity, pre-registered revert), the ragas import
  crash pinned with a one-class stub, and WSL killing Postgres on idle
  (now an operational ritual in the USER_GUIDE). All documented as they
  happened.
- *Why not patch the adjacency-bait failure immediately?* House rule: a
  prompt change is a version bump plus a full eval re-run, and one
  failure category is not worth overfitting a prompt on the same day it
  was discovered. It is recorded as prompt v4 candidate material.
- *How do I know your numbers are comparable?* Every results file sits
  next to a manifest pinning model id, prompt version, judge model,
  embedding model, corpus version, and dataset; numbers are compared
  only within matching pins.

## What's next

All four phases are complete and the project is parked at a stable
point: the agent is live on Cloud Run, the pgvector-vs-BigQuery
comparison is closed with deployment evidence (ADR 0010), and every
number in this document traces to a committed result or a verifiable
trace. Open threads, deliberately small: a request-cold latency trace
once the demo sits idle for a day, the CLARIFY ask-vs-investigate
retest on the first servable pro-class model, and whatever paralysis
remnants survive newer model generations. The demo URL stays public
while the project is being shown; if it goes idle for a long stretch,
it flips to IAM-only auth with one command.
