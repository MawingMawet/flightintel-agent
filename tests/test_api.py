"""Contract tests for the FastAPI service (PHASE3_PLAN Q5 layer a):
routes, schemas, and health degradation - no LLM, no network."""

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from flightintel.api import create_app
from flightintel.config import Settings

DB = "evals/fixtures/sample.db"


class FakeGraph:
    def invoke(self, state, config=None):
        return {
            "messages": [
                AIMessage(
                    content="There were 42 flights at the od_hourly grain.",
                    usage_metadata={
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "total_tokens": 15,
                    },
                )
            ]
        }


@pytest.fixture()
def client():
    settings = Settings(flight_db_path=DB, _env_file=None)
    return TestClient(create_app(settings=settings, graph=FakeGraph()))


def test_ask_returns_the_full_agent_answer(client):
    resp = client.post("/ask", json={"question": "How many flights?"})
    assert resp.status_code == 200
    body = resp.json()
    assert "42 flights" in body["answer"]
    assert body["could_answer"] is True
    assert body["prompt_version"]
    assert body["llm_requests"] == 1
    assert body["trace_id"] is None  # no Langfuse keys in this Settings


def test_ask_rejects_an_empty_question(client):
    resp = client.post("/ask", json={"question": ""})
    assert resp.status_code == 422


def test_health_degrades_without_vector_store(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["products_db"] == "ok"
    assert body["vector_store"] == "not configured"
    assert body["status"] == "degraded"


def test_trace_metadata_parses_the_tags_env():
    from flightintel.api import _trace_metadata

    assert _trace_metadata(Settings(flight_db_path=DB, _env_file=None)) is None
    tagged = Settings(flight_db_path=DB, trace_tags="env:cloud, demo", _env_file=None)
    assert _trace_metadata(tagged) == {"langfuse_tags": ["env:cloud", "demo"]}
    blank = Settings(flight_db_path=DB, trace_tags=" , ", _env_file=None)
    assert _trace_metadata(blank) is None


def test_health_checks_the_selected_bigquery_backend(monkeypatch):
    settings = Settings(flight_db_path=DB, bq_vector_table="p.d.t", _env_file=None)
    bq_client = TestClient(create_app(settings=settings, graph=FakeGraph()))

    monkeypatch.setattr(
        "flightintel.tools.docs_bq.check_table", lambda s, timeout: None
    )
    body = bq_client.get("/health").json()
    assert body["vector_store"] == "ok"
    assert body["status"] == "ok"

    def boom(s, timeout):
        raise RuntimeError("no credentials")

    monkeypatch.setattr("flightintel.tools.docs_bq.check_table", boom)
    body = bq_client.get("/health").json()
    assert body["vector_store"].startswith("unreachable:")
    assert body["status"] == "degraded"


def test_health_reports_down_when_products_db_breaks(client, monkeypatch):
    def boom(_path):
        raise RuntimeError("db gone")

    monkeypatch.setattr("flightintel.api.open_products_db", boom)
    body = client.get("/health").json()
    assert body["status"] == "down"
    assert body["products_db"].startswith("error:")
