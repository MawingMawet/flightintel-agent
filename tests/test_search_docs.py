import pytest
from pydantic import ValidationError

from flightintel.tools.docs import SearchDocsInput, search_docs


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, rows=None, raises=None):
        self.rows = rows or []
        self.raises = raises
        self.last_sql = None
        self.last_params = None

    def execute(self, sql, params):
        if self.raises is not None:
            raise self.raises
        self.last_sql = sql
        self.last_params = params
        return FakeCursor(self.rows)


def embed_ok(query):
    return [1.0, 0.0, 0.0]


def test_hits_map_rows_in_order():
    rows = [
        ("R/docs/a.md#x", "A > X", "text one", 0.11),
        ("R/docs/b.md", "B", "text two", 0.42),
    ]
    result = search_docs(embed_ok, FakeConn(rows), SearchDocsInput(query="why?"))
    assert result.error is None
    assert [h.citation for h in result.hits] == ["R/docs/a.md#x", "R/docs/b.md"]
    assert result.hits[0].distance == 0.11
    assert result.hits[1].text == "text two"


def test_top_k_reaches_sql_limit():
    conn = FakeConn([])
    search_docs(embed_ok, conn, SearchDocsInput(query="q", top_k=7))
    assert conn.last_params == ("[1.0,0.0,0.0]", 7)


def test_empty_query_rejected_without_embedding():
    calls = []

    def embed_counting(q):
        calls.append(q)
        return [1.0]

    result = search_docs(embed_counting, FakeConn(), SearchDocsInput(query="   "))
    assert result.error == "Empty query."
    assert calls == []


def test_embedding_failure_returns_structured_error():
    def embed_boom(q):
        raise RuntimeError("quota")

    result = search_docs(embed_boom, FakeConn(), SearchDocsInput(query="q"))
    assert result.hits == []
    assert "embedding failed" in result.error.lower()


def test_db_failure_returns_honest_error():
    conn = FakeConn(raises=ConnectionError("connection timeout expired"))
    result = search_docs(embed_ok, conn, SearchDocsInput(query="q"))
    assert result.hits == []
    assert "vector store unavailable" in result.error.lower()
    assert "could not be searched" in result.error.lower()


def test_top_k_bounds_enforced_at_the_boundary():
    with pytest.raises(ValidationError):
        SearchDocsInput(query="q", top_k=0)
    with pytest.raises(ValidationError):
        SearchDocsInput(query="q", top_k=11)
