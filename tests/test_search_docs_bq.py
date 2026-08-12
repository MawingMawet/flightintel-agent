"""The BigQuery backend must honor the exact search_docs contract the
pgvector path defined (PHASE4_PLAN Q2c); these mirror test_search_docs."""

from flightintel.config import Settings, vector_backend
from flightintel.tools.docs import SearchDocsInput
from flightintel.tools.docs_bq import search_docs_bq

DB = "evals/fixtures/sample.db"


def embed_ok(query):
    return [1.0, 0.0, 0.0]


class FakeRunner:
    def __init__(self, rows=None, raises=None):
        self.rows = rows or []
        self.raises = raises
        self.last_vec = None
        self.last_top_k = None

    def __call__(self, vec, top_k):
        if self.raises is not None:
            raise self.raises
        self.last_vec = vec
        self.last_top_k = top_k
        return self.rows


def test_hits_map_rows_in_order():
    rows = [
        ("R/docs/a.md#x", "A > X", "text one", 0.11),
        ("R/docs/b.md", "B", "text two", 0.42),
    ]
    result = search_docs_bq(embed_ok, FakeRunner(rows), SearchDocsInput(query="why?"))
    assert result.error is None
    assert [h.citation for h in result.hits] == ["R/docs/a.md#x", "R/docs/b.md"]
    assert result.hits[0].distance == 0.11
    assert result.hits[1].text == "text two"


def test_top_k_and_vector_reach_the_runner():
    runner = FakeRunner([])
    search_docs_bq(embed_ok, runner, SearchDocsInput(query="q", top_k=7))
    assert runner.last_vec == [1.0, 0.0, 0.0]
    assert runner.last_top_k == 7


def test_empty_query_rejected_without_embedding():
    calls = []

    def embed_counting(q):
        calls.append(q)
        return [1.0]

    result = search_docs_bq(embed_counting, FakeRunner(), SearchDocsInput(query="   "))
    assert result.error == "Empty query."
    assert calls == []


def test_embedding_failure_returns_structured_error():
    def embed_boom(q):
        raise RuntimeError("quota")

    result = search_docs_bq(embed_boom, FakeRunner(), SearchDocsInput(query="q"))
    assert result.hits == []
    assert "embedding failed" in result.error.lower()


def test_query_failure_returns_honest_error():
    runner = FakeRunner(raises=ConnectionError("job timeout"))
    result = search_docs_bq(embed_ok, runner, SearchDocsInput(query="q"))
    assert result.hits == []
    assert "vector store unavailable" in result.error.lower()
    assert "could not be searched" in result.error.lower()


def test_backend_selection_precedence():
    base = {"flight_db_path": DB, "_env_file": None}
    assert vector_backend(Settings(**base)) is None
    assert vector_backend(Settings(**base, pg_dsn="postgresql://x")) == "pgvector"
    assert (
        vector_backend(
            Settings(**base, pg_dsn="postgresql://x", bq_vector_table="p.d.t")
        )
        == "bigquery"
    )
