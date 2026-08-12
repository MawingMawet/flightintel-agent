# FlightIntel Agent - Project Knowledge Summary

What this project demonstrates about Gen AI engineering, with the
concrete evidence behind each claim. All numbers come from real runs
committed in this repository (`evals/results/`, reproducible traces via
the CLI's `--trace` mode).

## 1. The agent loop is a plain loop, and context is a budget

An LLM agent is a stateless chat API called in a loop: the model receives
the full message list every turn and returns either text or structured
tool calls; the client executes the named tool locally, appends the
result, and sends again. Observed on a real four-round answer: input
tokens grew 1005 -> 2104 -> 2655 -> 2944 per round because the whole list
is re-sent, totaling 8708 input tokens for one question. Consequences:
loop cost grows superlinearly with rounds, and every unnecessary
verification query costs a full round at the current context size.

## 2. Tools are typed contracts, not functions the model runs

The model only ever emits a tool name plus JSON arguments; the
application executes. Each tool boundary is Pydantic-typed with a
deliberate two-layer error contract: malformed inputs raise at the
boundary (a caller bug, not something the model negotiates with), while
correctable failures (bad SQL, timeout, guard rejection) return
structured error results the model may fix and retry once. Tool
implementations must be thread-safe: the framework executes tool calls in
a thread pool, which surfaced immediately as a SQLite thread-affinity
crash and was fixed with per-call read-only connections.

## 3. Evals are regression tests for behavior

The 20-case dataset derives one category per documented data-contract
rule (grain, timezone, gate, measure, grid, refusal), so the eval is a
checklist of the contract's sharp edges. Ground truth is executable SQL
run against the same database in the same session, which survives data
growth where frozen numbers would rot. Scoring is deterministic: exact
match for counts, keyword-checked reasons for refusals, and a
FINAL_ANSWER line contract so the scorer never mines numbers out of
prose. LLM-as-judge was deliberately deferred: judging numeric answers
with a second model adds cost and a second error source without signal.

## 4. Improvement must be measured, and the instrument can be the fault

One iteration day, every step a committed results file: 60% (prompt v1,
recursion limit 10) -> 75% (limit raised to 16 after traces showed the
step budget, not the model, aborting legitimate investigations) -> 80%
(prompt v2) -> 90% confirmed after an eval-question repair. Two of the
gains came from fixing the harness and the questions, not the model:

- Three timezone cases had multiple defensible answers each; the model
  spent its budget exploring exactly that ambiguity. Principle adopted:
  one trap per case, everything else anchored explicitly. An eval a
  well-reasoning agent fails for a defensible reason measures the
  question, not the agent.
- One failure was a scorer false negative: a correct refusal worded
  outside the accepted keyword list.

## 5. Temperature 0 is not determinism

The same eval case passed and failed on the same day with model, prompt,
question, and temperature 0 all pinned. Sampling temperature narrows
token choice; it does not pin the serving stack, and in a multi-step
agent an early one-token divergence compounds into a different
trajectory. Method consequences: a single run is a sample, one-case flips
are variance until they repeat, and reporting the best of several runs is
cherry-picking.

## 6. Prompt iteration has a failure mode: overfitting the eval set

After two failures shared one root cause (the model computes the correct
evidence, then keeps verifying instead of committing until the step
budget aborts), the third prompt iteration was run as a pre-registered
experiment: a structurally different mechanism (a sequencing constraint
rather than a restated intent), sentinel cases guarding against the
opposite failure (premature commitment), and a decision rule fixed before
measuring. The experiment failed its primary case and the prompt was
reverted per that rule. The failure is recorded as a model capability
finding: a findings column that never has entries means the evals are
being iterated until green.

## 7. Free-tier operations are real operations

Provider quotas meter requests per minute, tokens per minute, and
requests per day simultaneously, and an agent loop multiplies request
count: one answer costs 4-7 requests, a 20-case run ~90 requests and
~230-260k input tokens, and one iteration day ~440 requests. The daily
meter is the binding constraint. Engineering responses: client-side
throttling below published limits, exponential backoff honoring 429s,
incremental results with resume so an interrupted run continues the next
day, targeted re-runs by case id to keep diagnosis cheap, and raising a
loop budget is a spend decision (the higher recursion limit cost ~45%
more tokens per run because failing cases burn more rounds before
aborting).

## 8. Reproducibility is versioning everything that shapes behavior

Every eval result records the exact model id and prompt version that
produced it. Model ids are pinned, never `-latest` aliases: an alias can
change silently under the evals, and model retirement is real (the
originally chosen model 404s for accounts created after its retirement
date). When a prompt experiment is rejected, the exact rejected prompt
remains in version history alongside the results it produced.

## 9. The data contract is the spine of the whole application

Every layer of the Gen AI application inherits its shape from the
upstream data contract: the schema assertion pins it, the tools expose it
with grain sentences, the system prompt carries its caveats, the eval
categories test its violations, and the hardest open finding (proving a
date uncovered in a sparse multi-table schema) is a contract-shaped
reasoning problem. Model-side technique matters, but the leverage came
from treating the data platform's guarantees as first-class inputs to
prompt, tools, and evals alike.

## 10. Citation honesty is structural; grounding honesty is behavioral

Two different problems, two different defenses. Invented citations are
solved structurally: the tool returns chunk ids, the prompt cites only
returned ids, and the harness asserts cited is a subset of retrieved -
zero invented citations across every recorded run, by construction. But
the adjacency-bait failure shows structure cannot solve grounding: asked
about a topic the docs do not cover, the model built a confident answer
from near-topic chunks with perfectly VALID citations. A citation that
resolves is not a citation that answers; the second problem needs judged
metrics and prompt work, and it is recorded, not yet solved.

## 11. Retrieval is wording-sensitive, and the corpus is a design surface

Twin questions asking the same thing ranked the same gold chunk 1st and
9th, purely on phrasing. The cause was a corpus defect (one chunk
blending six unrelated questions dilutes its vector), and the fix was a
measured re-chunk: corpus v2 splits Q&A blocks at bold labels, and the
9th-ranked probe became 1st. Method points: the corpus is versioned like
a prompt (numbers compare only within a version), the fix had to prove
itself in the metric that recorded the failure, and the agent partially
masks retrieval weakness by reformulating queries - visible as a
context-precision cost, which is why retrieval is measured raw as well
as through the agent.

## 12. One model weakness, three costumes: the paralysis family

The same failure shape appeared on three different surfaces: proving a
date uncovered (runs queries after the proof is in hand until the step
budget aborts), proving the docs do not cover a topic (keeps searching
instead of saying "not covered"), and choosing between defensible
readings when a clarify option exists (investigates every reading
instead of asking). Commitment, not knowledge, is the gap: in each case
the correct evidence was already in the trace. It survived intent
statements, a sequencing constraint, and two suffix wordings - all
reverted by pre-registered rules. The value is precision: knowing
exactly which question shapes this model cannot close lets the system
route around them and gives the stronger-model retest a sharp target.

## 13. The eval instrument is part of the system and can break it

Adding one answer-form option to the eval suffix collapsed the agent
into step-limit aborts on every case, including trivially answerable
ones - the instruction meant to elicit a behavior changed the behavior
under measurement. Caught by sentinel cases, killed mid-run, diagnosed
from SQL trails, reverted by a rule fixed before measuring. The layered
harness design follows: deterministic checks run first and free, judged
metrics only where they are meaningful (refusals and mixed answers
punish the judge's assumptions), and any instrument change re-baselines
every number produced under it.
