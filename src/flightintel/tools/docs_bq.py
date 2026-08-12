"""search_docs BigQuery backend (PHASE4_PLAN Q2): the SAME typed contract
as the pgvector path in flightintel.tools.docs, served by BigQuery
VECTOR_SEARCH. The query runner is injected so the logic is testable
with fakes, mirroring the docs.search_docs design; the real runner is
built from settings (Q2b for the query, Q2d for credentials).

Manual probe from the project root (needs BQ_VECTOR_TABLE and, locally,
BQ_CREDENTIALS in .env; one embedding call plus one BigQuery job):
    .venv/Scripts/python.exe -m flightintel.tools.docs_bq "your question"
"""

from typing import Callable

from flightintel.tools.docs import DocHit, SearchDocsInput, SearchDocsResult

# base.* is VECTOR_SEARCH's alias for the searched table's row. COSINE
# distance is 1 - cosine similarity, the same quantity pgvector's <=>
# returns, so the two backends' numbers compare directly (Q2b). Only the
# table id is formatted in, and it comes from config, never from model
# output; the values travel as query parameters.
_SEARCH_SQL = """
SELECT base.id, base.breadcrumb, base.text, distance
FROM VECTOR_SEARCH(TABLE `{table}`, 'embedding',
    (SELECT @qvec AS embedding),
    top_k => @top_k, distance_type => 'COSINE')
ORDER BY distance
"""

QueryRunner = Callable[[list[float], int], list[tuple]]


def search_docs_bq(
    embed_query: Callable[[str], list[float]],
    run_query: QueryRunner,
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
        rows = run_query(vec, params.top_k)
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


def make_bq_client(settings):
    """Real BigQuery client; imports stay out of the pure path. A local
    run loads the ADC file named in settings explicitly (Q2d, never by
    mutating os.environ); in the cloud that setting is absent and the
    ambient ADC is the runtime service account."""
    from google.cloud import bigquery

    project = settings.bq_vector_table.split(".", 1)[0]
    if settings.bq_credentials is not None:
        import google.auth

        creds, _ = google.auth.load_credentials_from_file(
            str(settings.bq_credentials), quota_project_id=project
        )
        return bigquery.Client(project=project, credentials=creds)
    return bigquery.Client(project=project)


def make_bq_runner(settings) -> QueryRunner:
    """Build the real query runner once; the client is thread-safe, so
    sharing it across ToolNode's threads is fine (unlike the pg path,
    where each call opens its own connection)."""
    from google.cloud import bigquery

    client = make_bq_client(settings)
    sql = _SEARCH_SQL.format(table=settings.bq_vector_table)

    def run(vec: list[float], top_k: int) -> list[tuple]:
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("qvec", "FLOAT64", vec),
                bigquery.ScalarQueryParameter("top_k", "INT64", top_k),
            ]
        )
        job = client.query(sql, job_config=job_config)
        return [tuple(row) for row in job.result()]

    return run


def check_table(settings, timeout: float = 5.0) -> None:
    """Health probe: a tables.get metadata fetch, free (no query job)."""
    make_bq_client(settings).get_table(settings.bq_vector_table, timeout=timeout)


def _main() -> None:
    import argparse

    from flightintel.config import load_settings
    from flightintel.tools.docs import make_query_embedder

    parser = argparse.ArgumentParser(description="Probe the BigQuery docs index.")
    parser.add_argument("query")
    parser.add_argument("--top-k", type=int, default=4)
    args = parser.parse_args()

    settings = load_settings()
    if settings.bq_vector_table is None:
        raise SystemExit("Set BQ_VECTOR_TABLE in .env (see .env.example).")
    result = search_docs_bq(
        make_query_embedder(settings),
        make_bq_runner(settings),
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
