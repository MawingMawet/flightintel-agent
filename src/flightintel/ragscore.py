"""Pure scoring pieces for the phase 2 RAG evals (PHASE2_PLAN Q10-Q12).

Everything here runs without an LLM or a database so it is unit-testable
the same way evalscore.py is. The judged (RAGAS) layer lives in
evals/run_rag_evals.py; this module owns the deterministic layer:
recall@k against gold sources and the citation-validity check (Q5).
"""

import json

from langchain_core.messages import BaseMessage, ToolMessage
from pydantic import BaseModel

from flightintel.agent import message_text


class RagEvalCase(BaseModel):
    id: str
    category: str
    question: str
    reference: str
    gold_sources: list[str] = []
    expect_not_covered: bool = False
    requires_sql: bool = False
    notes: str | None = None


class DocContext(BaseModel):
    citation: str
    text: str


class RagScore(BaseModel):
    passed: bool
    detail: str
    recall: float | None
    invented: list[str]


def extract_doc_contexts(messages: list[BaseMessage]) -> list[DocContext]:
    """Chunk texts search_docs returned this run, unique by citation, in
    order. These are the RAGAS retrieved_contexts; ids alone (agent.py's
    extract_docs_retrieved) are not enough because the judge reads text."""
    seen: dict[str, str] = {}
    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.name == "search_docs":
            result = json.loads(message_text(msg))
            for hit in result.get("hits") or []:
                if hit["citation"] not in seen:
                    seen[hit["citation"]] = hit["text"]
    return [DocContext(citation=c, text=t) for c, t in seen.items()]


def recall_at_k(gold: list[str], retrieved: list[str]) -> float | None:
    """Fraction of gold sources present in the retrieved ids; None when
    the case declares no gold (not-covered cases)."""
    if not gold:
        return None
    return sum(1 for g in gold if g in retrieved) / len(gold)


def invented_citations(cited: list[str], retrieved: list[str]) -> list[str]:
    """Cited ids that were never retrieved this run (Q5: must be empty)."""
    return [c for c in cited if c not in retrieved]


# Markers the honest paths actually produce: prompt v3 rule 9 phrasing and
# the tool's own degraded-path wording. Deterministic and therefore blunt;
# a false negative here shows up as a failed case to read, not a silent pass.
NOT_COVERED_MARKERS = (
    "not cover",
    "not covered",
    "no documentation",
    "documentation does not",
    "docs do not",
    "could not be searched",
    "unavailable",
)


def looks_not_covered(answer: str) -> bool:
    lowered = answer.lower()
    return any(marker in lowered for marker in NOT_COVERED_MARKERS)


def score_rag_case(
    case: RagEvalCase,
    *,
    answer_text: str,
    docs_retrieved: list[str],
    docs_cited: list[str],
    sql_used: list[str],
) -> RagScore:
    """Deterministic verdict per PHASE2_PLAN Q12. Judged metrics come on
    top for the judged categories; they never override this layer."""
    recall = recall_at_k(case.gold_sources, docs_retrieved)
    invented = invented_citations(docs_cited, docs_retrieved)

    if case.expect_not_covered:
        honest = looks_not_covered(answer_text)
        passed = honest and not invented
        detail = (
            "honest not-covered" if passed
            else f"expected refusal; honest={honest}, invented={invented}"
        )
    elif case.category == "mixed":
        searched = bool(docs_retrieved)
        sql_ok = bool(sql_used) or not case.requires_sql
        passed = searched and sql_ok and not invented
        detail = (
            "both paths used, citations valid" if passed
            else f"searched={searched}, sql={bool(sql_used)}, invented={invented}"
        )
    else:
        passed = not invented and recall == 1.0
        detail = (
            "gold retrieved, citations valid" if passed
            else f"recall@k={recall}, invented={invented}"
        )
    return RagScore(passed=passed, detail=detail, recall=recall, invented=invented)
