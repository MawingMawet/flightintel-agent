"""LangGraph agent assembly: the framework owns ONLY the loop (ADR 0005).

The four domain tools stay plain functions in flightintel.tools; this module
wraps them as LangChain tools closing over one read-only connection, builds
the prebuilt ReAct-style graph, and turns the raw message list into an
AgentAnswer that records what the run cost (model, requests, tokens) next
to what it produced (CLAUDE.md cost policy).
"""

import json
import re

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel

from flightintel import tracing
from flightintel.config import Settings, vector_backend
from flightintel.db import connect_readonly
from flightintel.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from flightintel.tools import airports, audit, docs, docs_bq, schema, sql

# PHASE1_PLAN Q4, amended after eval run 1 (2026-08-10): 10 allowed only ~4
# model rounds and aborted 6/20 legitimate multi-query investigations; 16
# allows ~7 rounds. The guard still exists to stop true runaways.
RECURSION_LIMIT = 16

COULD_NOT_ANSWER = (
    "Could not answer: the agent hit its step limit before reaching a "
    "final answer."
)


def is_graph_injected(msg: BaseMessage) -> bool:
    """A real Gemini response always carries usage_metadata; the prebuilt
    graph injects a canned 'need more steps' AIMessage (no API call, no
    usage) when the step budget runs out before the model finishes."""
    return (
        isinstance(msg, AIMessage)
        and not msg.tool_calls
        and not msg.usage_metadata
    )


class AgentAnswer(BaseModel):
    question: str
    answer: str
    could_answer: bool
    sql_used: list[str]
    docs_retrieved: list[str] = []
    docs_cited: list[str] = []
    model_id: str
    prompt_version: str
    llm_requests: int
    input_tokens: int
    output_tokens: int
    trace_id: str | None = None


def build_tools(settings: Settings) -> list:
    db_path = settings.flight_db_path
    # LangGraph's ToolNode executes tool calls in a thread pool (parallel
    # calls are possible), and sqlite3 connections are bound to their
    # creating thread. Each call therefore opens its own short-lived
    # read-only connection; opening a SQLite file is negligible next to an
    # LLM round trip, and it keeps run_sql's per-connection timeout handler
    # isolated per call.
    @tool
    def get_schema() -> str:
        """Return every table's columns AND its grain sentence (what one row
        means). Call this before writing SQL if you are not certain of a
        table's grain or columns."""
        con = connect_readonly(db_path)
        try:
            return schema.get_schema(con).model_dump_json()
        finally:
            con.close()

    @tool
    def run_sql(sql_query: str, max_rows: int = 50) -> str:
        """Execute one read-only SELECT (or WITH...SELECT) statement against
        the products database. Returns columns, rows, and a truncated flag;
        on failure returns an error message you should correct and retry
        once. max_rows caps returned rows (limit 200)."""
        con = connect_readonly(db_path)
        try:
            params = sql.RunSqlInput(sql=sql_query, max_rows=max_rows)
            return sql.run_sql(con, params).model_dump_json()
        finally:
            con.close()

    @tool
    def get_build_audit(limit: int = 5) -> str:
        """Return the most recent pipeline build runs from BOTH audit
        tables: the flights half (build_audit) and the states half
        (build_audit_states). Use for data-quality and completeness
        questions."""
        con = connect_readonly(db_path)
        try:
            params = audit.GetBuildAuditInput(limit=limit)
            return audit.get_build_audit(con, params).model_dump_json()
        finally:
            con.close()

    @tool
    def resolve_airport(query: str) -> str:
        """Resolve an airport name, city, or ICAO code to ICAO code(s),
        e.g. 'Suvarnabhumi' -> VTBS. May return multiple candidates
        (ambiguous city) or none (unknown; the ICAO may still be valid in
        the data)."""
        params = airports.ResolveAirportInput(query=query)
        return airports.resolve_airport(params).model_dump_json()

    # Built once per agent; the embedder is a stateless HTTP client, safe
    # to share across ToolNode's threads. The DB connection is NOT shared:
    # one short-lived connection per call (Q9), so a WSL idle shutdown
    # between calls fails one call honestly instead of the whole session.
    backend = vector_backend(settings)
    embed_query = docs.make_query_embedder(settings) if backend else None
    # The BigQuery runner is built lazily on first call: a local
    # credential problem then fails one call honestly (the pg connect
    # pattern) instead of downing the whole service at startup.
    bq_run: docs_bq.QueryRunner | None = None

    @tool
    def search_docs(query: str, top_k: int = 4) -> str:
        """Semantic search over both projects' committed documentation:
        design decisions (ADRs), architecture, data caveats, and findings.
        Use for why/how/what-was-decided questions, not for querying
        flight data. Returns chunks with citation ids; cite those exact
        ids in your answer."""
        nonlocal bq_run
        if embed_query is None:
            return docs.SearchDocsResult(
                query=query,
                error=(
                    "The docs index is not configured (no BQ_VECTOR_TABLE "
                    "or PG_DSN); say the documentation could not be "
                    "searched."
                ),
            ).model_dump_json()
        params = docs.SearchDocsInput(query=query, top_k=top_k)

        if backend == "bigquery":
            try:
                if bq_run is None:
                    bq_run = docs_bq.make_bq_runner(settings)
            except Exception as exc:
                return docs.SearchDocsResult(
                    query=query,
                    error=(
                        f"Vector store unavailable: {exc}. Say the docs "
                        "could not be searched; answer only what other "
                        "tools support."
                    ),
                ).model_dump_json()
            return docs_bq.search_docs_bq(
                embed_query, bq_run, params
            ).model_dump_json()

        import psycopg

        try:
            conn = psycopg.connect(
                settings.pg_dsn.get_secret_value(), connect_timeout=5
            )
        except psycopg.Error as exc:
            return docs.SearchDocsResult(
                query=query,
                error=(
                    f"Vector store unavailable: {exc}. Say the docs could "
                    "not be searched; answer only what other tools support."
                ),
            ).model_dump_json()
        try:
            return docs.search_docs(embed_query, conn, params).model_dump_json()
        finally:
            conn.close()

    return [get_schema, run_sql, get_build_audit, resolve_airport, search_docs]


def build_agent(settings: Settings):
    if settings.gemini_api_key is None:
        raise RuntimeError(
            "GEMINI_API_KEY is not set; add it to .env before running the agent."
        )
    llm = ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        temperature=0,
        google_api_key=settings.gemini_api_key.get_secret_value(),
    )
    return create_react_agent(llm, build_tools(settings), prompt=SYSTEM_PROMPT)


def message_text(msg: BaseMessage) -> str:
    """Message content is either a plain string or a list of typed parts
    (Gemini returns parts); flatten to the text either way."""
    if isinstance(msg.content, str):
        return msg.content
    parts = []
    for part in msg.content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and part.get("type") == "text":
            parts.append(part.get("text", ""))
    return "".join(parts)


def extract_sql_used(messages: list[BaseMessage]) -> list[str]:
    calls = []
    for msg in messages:
        if isinstance(msg, AIMessage):
            for tc in msg.tool_calls:
                if tc["name"] == "run_sql" and "sql_query" in tc["args"]:
                    calls.append(tc["args"]["sql_query"])
    return calls


# A citation id always contains a path separator and no spaces; prose in
# brackets ("[see below]") and bare numbers ("[1]") do not match.
_CITATION_PATTERN = re.compile(r"\[([^\[\]\s]+/[^\[\]\s]+)\]")


def extract_citations(answer_text: str) -> list[str]:
    """Bracketed citation ids in the answer, unique, in first-seen order."""
    seen: list[str] = []
    for match in _CITATION_PATTERN.findall(answer_text):
        if match not in seen:
            seen.append(match)
    return seen


def extract_docs_retrieved(messages: list[BaseMessage]) -> list[str]:
    """Citation ids search_docs actually returned in this run, unique, in
    order. The Q5 honesty check compares cited ids against this list."""
    seen: list[str] = []
    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.name == "search_docs":
            result = json.loads(message_text(msg))
            for hit in result.get("hits") or []:
                if hit["citation"] not in seen:
                    seen.append(hit["citation"])
    return seen


def summarize_usage(messages: list[BaseMessage]) -> tuple[int, int, int]:
    """(llm_requests, input_tokens, output_tokens) from the message list.
    Every AIMessage is one round-trip to the model; this is the number the
    free-tier RPM/RPD meters see."""
    requests = input_tokens = output_tokens = 0
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.usage_metadata:
            requests += 1
            input_tokens += msg.usage_metadata.get("input_tokens", 0)
            output_tokens += msg.usage_metadata.get("output_tokens", 0)
    return requests, input_tokens, output_tokens


def ask(
    graph,
    question: str,
    settings: Settings,
    trace_metadata: dict | None = None,
) -> tuple[AgentAnswer, list[BaseMessage]]:
    """Run one question through the agent. Returns the structured answer and
    the raw message list (the trace) for inspection. When Langfuse keys are
    configured, the run is traced (PHASE3_PLAN Q2) and trace_metadata (e.g.
    langfuse_tags, eval case ids) rides along; without keys the config is
    identical to the untraced original."""
    callbacks, trace_id = tracing.make_trace_config(settings)
    config: dict = {"recursion_limit": RECURSION_LIMIT}
    if callbacks:
        config["callbacks"] = callbacks
        config["metadata"] = trace_metadata or {}
    try:
        result = graph.invoke({"messages": [("user", question)]}, config=config)
        messages = result["messages"]
        if messages and is_graph_injected(messages[-1]):
            answer = COULD_NOT_ANSWER
            could_answer = False
        else:
            answer = message_text(messages[-1]) if messages else ""
            could_answer = bool(answer.strip())
    except GraphRecursionError:
        # The structured honest-failure path (Q4): evals reward this over
        # a hallucinated answer.
        messages = []
        answer = COULD_NOT_ANSWER
        could_answer = False
    finally:
        # Batched spans drain now, so short-lived CLI and eval processes
        # cannot exit before the trace ships.
        if callbacks:
            tracing.flush(settings)

    requests, tokens_in, tokens_out = summarize_usage(messages)
    return (
        AgentAnswer(
            question=question,
            answer=answer,
            could_answer=could_answer,
            sql_used=extract_sql_used(messages),
            docs_retrieved=extract_docs_retrieved(messages),
            docs_cited=extract_citations(answer),
            model_id=settings.gemini_model,
            prompt_version=PROMPT_VERSION,
            llm_requests=requests,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            trace_id=trace_id,
        ),
        messages,
    )
