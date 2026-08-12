"""CLI entry point: ask one question, see the answer, the SQL the agent
ran, and what the run cost. --trace prints the raw message list annotated
with the five loop mechanics from working-docs/knowledge/under-the-hood.md.
"""

import argparse
import sys

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from flightintel.agent import ask, build_agent, is_graph_injected, message_text
from flightintel.config import load_settings
from flightintel.db import open_products_db

_PREVIEW = 700


def _clip(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _PREVIEW else text[:_PREVIEW] + " ...[clipped]"


def format_trace(messages: list[BaseMessage]) -> str:
    lines = [
        "",
        "=== RAW TRACE: the agent's entire memory ===",
        "Mechanic 1: the model is stateless; this whole list is re-sent on",
        "every turn. (The system prompt is prepended by the graph at each",
        "model call; it lives in the graph, not in this state.)",
        "",
    ]
    for i, msg in enumerate(messages):
        if isinstance(msg, HumanMessage):
            lines.append(f"[{i}] HUMAN: {_clip(message_text(msg))}")
        elif isinstance(msg, AIMessage):
            usage = msg.usage_metadata or {}
            meter = (
                f" (tokens in={usage.get('input_tokens', '?')}"
                f" out={usage.get('output_tokens', '?')})"
            )
            if msg.tool_calls:
                lines.append(
                    f"[{i}] AI -> TOOL CALL{meter}  "
                    "[mechanic 2: the model EMITS a call, executes nothing]"
                )
                for tc in msg.tool_calls:
                    lines.append(f"      {tc['name']}({tc['args']})")
            elif is_graph_injected(msg):
                lines.append(
                    f"[{i}] AI (GRAPH-INJECTED, no API call)  "
                    "[the step-budget guard spoke, not the model]"
                )
                lines.append(f"      {_clip(message_text(msg))}")
            else:
                lines.append(
                    f"[{i}] AI -> FINAL TEXT{meter}  "
                    "[mechanic 4: no tool call = stop condition, loop ends]"
                )
                lines.append(f"      {_clip(message_text(msg))}")
        elif isinstance(msg, ToolMessage):
            lines.append(
                f"[{i}] TOOL RESULT ({msg.name})  "
                "[mechanic 3: executed locally, appended, sent back]"
            )
            lines.append(f"      {_clip(message_text(msg))}")
        else:
            lines.append(f"[{i}] {type(msg).__name__}: {_clip(message_text(msg))}")
    lines.append("")
    lines.append(
        "Mechanic 5 (error path): a failed tool returns its error as a TOOL "
        "RESULT above, so the model can correct itself. The recursion_limit "
        "guard caps the loop if it never converges."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="flightintel", description="Ask the Bangkok Airspace Analyst."
    )
    parser.add_argument("question", help="Natural-language question to answer.")
    parser.add_argument(
        "--trace", action="store_true", help="Print the raw annotated message list."
    )
    parser.add_argument(
        "--json", action="store_true", help="Print the structured answer as JSON."
    )
    args = parser.parse_args(argv)

    # Windows consoles default to a legacy codepage; model output is UTF-8.
    sys.stdout.reconfigure(encoding="utf-8")

    settings = load_settings()
    # Startup contract check: fail loudly on drift before any LLM call.
    open_products_db(settings.flight_db_path).close()
    graph = build_agent(settings)
    answer, messages = ask(graph, args.question, settings)

    if args.json:
        print(answer.model_dump_json(indent=2))
    else:
        print(answer.answer)
        if answer.sql_used:
            print("\nSQL used:")
            for stmt in answer.sql_used:
                print(f"  {stmt}")
        if answer.docs_cited:
            print("\nDocs cited:")
            for cid in answer.docs_cited:
                print(f"  [{cid}]")
            invented = [c for c in answer.docs_cited if c not in answer.docs_retrieved]
            if invented:
                print("  WARNING - cited but never retrieved this run (invented):")
                for cid in invented:
                    print(f"    [{cid}]")
        elif answer.docs_retrieved:
            print(f"\n[searched docs: {len(answer.docs_retrieved)} chunks retrieved, none cited]")
        print(
            f"\n[{answer.model_id} | prompt {answer.prompt_version} | "
            f"{answer.llm_requests} LLM requests | "
            f"tokens in={answer.input_tokens} out={answer.output_tokens}]"
        )
        if answer.trace_id:
            from flightintel.tracing import trace_url

            url = trace_url(settings, answer.trace_id)
            print(f"[trace: {url or answer.trace_id}]")
    if args.trace:
        print(format_trace(messages))


if __name__ == "__main__":
    main()
