"""search_docs: typed semantic retrieval over the embedded docs corpus.

The tool returns chunk text plus provenance; the agent's citations render
from the returned citation ids, never from model memory (PHASE2_PLAN Q5).
The query embedder and the database connection are injected so the logic
is testable with fakes (design in Q8); errors come back in the result,
not as exceptions, because a missing vector store is a state the agent
should report honestly.

Manual probe from the project root (needs .env and a running WSL Postgres):
    .venv/Scripts/python.exe -m flightintel.tools.docs "your question"
"""

from typing import Callable

from pydantic import BaseModel, Field

from flightintel.rag.build_index import TASK_TYPE_QUERIES, vector_literal

TOP_K_CAP = 10

_SEARCH_SQL = """
SELECT id, breadcrumb, text, embedding <=> %s::vector AS distance
FROM doc_chunks
ORDER BY distance
LIMIT %s
"""


class SearchDocsInput(BaseModel):
    query: str = Field(
        description=(
            "A natural-language question about the two projects' design, "
            "decisions, or data caveats."
        )
    )
    top_k: int = Field(default=4, ge=1, le=TOP_K_CAP)


class DocHit(BaseModel):
    citation: str = Field(description="Chunk id; cite this exact string.")
    breadcrumb: str
    text: str
    distance: float


class SearchDocsResult(BaseModel):
    query: str
    hits: list[DocHit] = []
    error: str | None = None


def search_docs(
    embed_query: Callable[[str], list[float]],
    conn,
    params: SearchDocsInput,
) -> SearchDocsResult:
    query = params.query.strip()
    if not query:
        return SearchDocsResult(query=params.query, error="Empty query.")

    try:
        vec = embed_query(query)
    except Exception as exc:
        return SearchDocsResult(
            query=query, error=f"Query embedding failed: {exc}"
        )

    try:
        rows = conn.execute(_SEARCH_SQL, (vector_literal(vec), params.top_k)).fetchall()
    except Exception as exc:
        return SearchDocsResult(
            query=query,
            error=(
                f"Vector store unavailable: {exc}. The docs index may be "
                "down; answer only what other tools can support and say "
                "the docs could not be searched."
            ),
        )

    return SearchDocsResult(
        query=query,
        hits=[
            DocHit(citation=r[0], breadcrumb=r[1], text=r[2], distance=r[3])
            for r in rows
        ],
    )


def make_query_embedder(settings) -> Callable[[str], list[float]]:
    """Build the runtime embedder; imported models stay out of the pure path."""
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    embedder = GoogleGenerativeAIEmbeddings(
        model=f"models/{settings.embedding_model}",
        google_api_key=settings.gemini_api_key,
    )
    return lambda q: embedder.embed_query(q, task_type=TASK_TYPE_QUERIES)


def _main() -> None:
    import argparse

    import psycopg

    from flightintel.config import load_settings

    parser = argparse.ArgumentParser(description="Probe the docs index.")
    parser.add_argument("query")
    parser.add_argument("--top-k", type=int, default=4)
    args = parser.parse_args()

    settings = load_settings()
    if settings.pg_dsn is None:
        raise SystemExit("Set PG_DSN in .env (scripts/setup_pgvector.sh writes it).")
    with psycopg.connect(settings.pg_dsn.get_secret_value()) as conn:
        result = search_docs(
            make_query_embedder(settings),
            conn,
            SearchDocsInput(query=args.query, top_k=args.top_k),
        )
    if result.error:
        raise SystemExit(result.error)
    for hit in result.hits:
        preview = " ".join(hit.text.split())[:100]
        print(f"{hit.distance:.4f}  {hit.citation}")
        print(f"        {preview}...")


if __name__ == "__main__":
    _main()
