"""Unit tests for the deterministic RAG scoring layer (no LLM, no DB)."""

import json

from langchain_core.messages import ToolMessage

from flightintel.ragscore import (
    RagEvalCase,
    extract_doc_contexts,
    invented_citations,
    looks_not_covered,
    recall_at_k,
    score_rag_case,
)


def search_docs_msg(hits, error=None):
    payload = {"query": "q", "hits": hits, "error": error}
    return ToolMessage(
        content=json.dumps(payload), name="search_docs", tool_call_id="t1"
    )


def hit(citation, text="body"):
    return {"citation": citation, "breadcrumb": "b", "text": text, "distance": 0.2}


def case(**overrides):
    base = dict(
        id="c1",
        category="single-source",
        question="q?",
        reference="ref",
        gold_sources=["repo/a.md"],
    )
    base.update(overrides)
    return RagEvalCase(**base)


# --- extract_doc_contexts ---

def test_extract_contexts_dedups_and_keeps_order():
    messages = [
        search_docs_msg([hit("repo/a.md", "A"), hit("repo/b.md", "B")]),
        search_docs_msg([hit("repo/b.md", "B"), hit("repo/c.md", "C")]),
    ]
    contexts = extract_doc_contexts(messages)
    assert [c.citation for c in contexts] == ["repo/a.md", "repo/b.md", "repo/c.md"]
    assert contexts[0].text == "A"


def test_extract_contexts_ignores_other_tools_and_errors():
    other = ToolMessage(content='{"rows": []}', name="run_sql", tool_call_id="t2")
    errored = search_docs_msg(None, error="db down")
    assert extract_doc_contexts([other, errored]) == []


# --- recall and citations ---

def test_recall_none_without_gold():
    assert recall_at_k([], ["repo/a.md"]) is None


def test_recall_fraction():
    assert recall_at_k(["a", "b"], ["a", "x"]) == 0.5
    assert recall_at_k(["a", "b"], ["b", "a"]) == 1.0
    assert recall_at_k(["a"], []) == 0.0


def test_invented_citations():
    assert invented_citations(["a", "z"], ["a", "b"]) == ["z"]
    assert invented_citations([], ["a"]) == []


# --- not-covered detection ---

def test_not_covered_markers():
    assert looks_not_covered("The documentation does not cover spoofing.")
    assert looks_not_covered("The docs could not be searched right now.")
    assert not looks_not_covered("The busiest hour was 09:00 UTC with 42 aircraft.")


# --- score_rag_case per category ---

def test_judged_category_needs_full_recall_and_valid_citations():
    score = score_rag_case(
        case(),
        answer_text="answer [repo/a.md]",
        docs_retrieved=["repo/a.md", "repo/b.md"],
        docs_cited=["repo/a.md"],
        sql_used=[],
    )
    assert score.passed and score.recall == 1.0


def test_judged_category_fails_on_missed_gold():
    score = score_rag_case(
        case(gold_sources=["repo/a.md", "repo/gold2.md"]),
        answer_text="answer [repo/a.md]",
        docs_retrieved=["repo/a.md"],
        docs_cited=["repo/a.md"],
        sql_used=[],
    )
    assert not score.passed and score.recall == 0.5


def test_invented_citation_always_fails():
    score = score_rag_case(
        case(),
        answer_text="answer [repo/fake.md]",
        docs_retrieved=["repo/a.md"],
        docs_cited=["repo/fake.md"],
        sql_used=[],
    )
    assert not score.passed and score.invented == ["repo/fake.md"]


def test_not_covered_passes_on_honest_refusal():
    score = score_rag_case(
        case(category="not-covered", gold_sources=[], expect_not_covered=True),
        answer_text="The documentation does not cover this.",
        docs_retrieved=["repo/a.md"],
        docs_cited=[],
        sql_used=[],
    )
    assert score.passed and score.recall is None


def test_not_covered_fails_on_confident_answer():
    score = score_rag_case(
        case(category="not-covered", gold_sources=[], expect_not_covered=True),
        answer_text="Spoofing is handled by multilateration cross-checks.",
        docs_retrieved=[],
        docs_cited=[],
        sql_used=[],
    )
    assert not score.passed


def test_mixed_requires_both_paths():
    mixed = case(category="mixed", requires_sql=True)
    good = score_rag_case(
        mixed,
        answer_text="Hour 09 [repo/a.md]",
        docs_retrieved=["repo/a.md"],
        docs_cited=["repo/a.md"],
        sql_used=["SELECT 1"],
    )
    no_sql = score_rag_case(
        mixed,
        answer_text="Hour 09 [repo/a.md]",
        docs_retrieved=["repo/a.md"],
        docs_cited=["repo/a.md"],
        sql_used=[],
    )
    assert good.passed and not no_sql.passed
