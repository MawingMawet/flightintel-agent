import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from flightintel.agent import (
    extract_citations,
    extract_docs_retrieved,
    extract_sql_used,
    is_graph_injected,
    message_text,
    summarize_usage,
)
from flightintel.cli import format_trace


def _tool_call(name, args, call_id="c1"):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _messages():
    return [
        HumanMessage("How many flights?"),
        AIMessage(
            "",
            tool_calls=[_tool_call("run_sql", {"sql_query": "SELECT COUNT(*) FROM flights"})],
            usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
        ),
        ToolMessage('{"rows": [[42]]}', name="run_sql", tool_call_id="c1"),
        AIMessage(
            "There are 42 flights (grain: one row per flight).",
            usage_metadata={"input_tokens": 150, "output_tokens": 20, "total_tokens": 170},
        ),
    ]


def test_extract_sql_used_finds_only_run_sql_calls():
    messages = _messages()
    messages.insert(
        1,
        AIMessage("", tool_calls=[_tool_call("resolve_airport", {"query": "BKK"}, "c0")]),
    )
    assert extract_sql_used(messages) == ["SELECT COUNT(*) FROM flights"]


def test_summarize_usage_counts_requests_and_tokens():
    requests, tokens_in, tokens_out = summarize_usage(_messages())
    assert requests == 2
    assert tokens_in == 250
    assert tokens_out == 30


def test_graph_injected_message_is_not_a_request():
    messages = _messages()
    messages.append(AIMessage("Sorry, need more steps to process this request."))
    assert is_graph_injected(messages[-1]) is True
    assert is_graph_injected(messages[-2]) is False
    requests, _, _ = summarize_usage(messages)
    assert requests == 2


def test_message_text_flattens_part_lists():
    msg = AIMessage([{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}])
    assert message_text(msg) == "hello world"


def test_format_trace_labels_the_mechanics():
    trace = format_trace(_messages())
    assert "TOOL CALL" in trace
    assert "FINAL TEXT" in trace
    assert "run_sql" in trace
    assert "recursion_limit" in trace


def _search_docs_result(citations, error=None):
    return json.dumps(
        {
            "query": "q",
            "hits": [
                {"citation": c, "breadcrumb": "B", "text": "t", "distance": 0.2}
                for c in citations
            ],
            "error": error,
        }
    )


def test_extract_citations_dedupes_and_ignores_prose_brackets():
    text = (
        "The gate drops nulls [AirflowOSky/docs/ARCHITECTURE.md#gates] and "
        "biases long-haul [AirflowOSky/docs/ARCHITECTURE.md#gates], see "
        "[the docs] and item [1] for more; also "
        "[FlightIntelAgent/README.md]."
    )
    assert extract_citations(text) == [
        "AirflowOSky/docs/ARCHITECTURE.md#gates",
        "FlightIntelAgent/README.md",
    ]


def test_extract_citations_empty_answer():
    assert extract_citations("No citations here.") == []


def test_extract_docs_retrieved_collects_in_order_across_calls():
    messages = [
        ToolMessage(_search_docs_result(["R/a.md#x", "R/b.md"]), name="search_docs", tool_call_id="c1"),
        ToolMessage('{"rows": [[1]]}', name="run_sql", tool_call_id="c2"),
        ToolMessage(_search_docs_result(["R/b.md", "R/c.md#y"]), name="search_docs", tool_call_id="c3"),
    ]
    assert extract_docs_retrieved(messages) == ["R/a.md#x", "R/b.md", "R/c.md#y"]


def test_extract_docs_retrieved_handles_error_result():
    messages = [
        ToolMessage(
            '{"query": "q", "hits": [], "error": "Vector store unavailable"}',
            name="search_docs",
            tool_call_id="c1",
        ),
    ]
    assert extract_docs_retrieved(messages) == []
