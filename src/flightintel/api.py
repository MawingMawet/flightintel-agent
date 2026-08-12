"""FastAPI service (PHASE3_PLAN Q3): two endpoints over the same
build_agent/ask path the CLI uses - one generation path keeps every eval
valid for the service.

Run locally (factory mode, so importing this module never builds an app):
    .venv/Scripts/python.exe -m uvicorn flightintel.api:create_app --factory --port 8000
"""

from importlib.metadata import version as pkg_version

from fastapi import FastAPI
from pydantic import BaseModel, Field

from flightintel.agent import AgentAnswer, ask, build_agent
from flightintel.config import Settings, load_settings, vector_backend
from flightintel.db import open_products_db


class AskRequest(BaseModel):
    question: str = Field(min_length=1, description="Natural-language question.")


class HealthReport(BaseModel):
    status: str
    products_db: str
    vector_store: str


def _check_products_db(settings: Settings) -> str:
    try:
        open_products_db(settings.flight_db_path).close()
        return "ok"
    except Exception as exc:
        return f"error: {str(exc)[:200]}"


def _trace_metadata(settings: Settings) -> dict | None:
    if settings.trace_tags is None:
        return None
    tags = [t.strip() for t in settings.trace_tags.split(",") if t.strip()]
    return {"langfuse_tags": tags} if tags else None


def _check_vector_store(settings: Settings) -> str:
    backend = vector_backend(settings)
    if backend is None:
        return "not configured"
    if backend == "bigquery":
        from flightintel.tools import docs_bq

        try:
            docs_bq.check_table(settings, timeout=2)
            return "ok"
        except Exception as exc:
            return f"unreachable: {str(exc)[:200]}"
    import psycopg

    try:
        with psycopg.connect(
            settings.pg_dsn.get_secret_value(), connect_timeout=2
        ) as conn:
            conn.execute("SELECT 1")
        return "ok"
    except Exception as exc:
        return f"unreachable: {str(exc)[:200]}"


def create_app(settings: Settings | None = None, graph=None) -> FastAPI:
    """App factory. Tests inject settings and a fake graph; production
    builds the real agent once at startup and fails loudly on schema
    drift, the same startup contract as the CLI."""
    settings = settings or load_settings()
    open_products_db(settings.flight_db_path).close()
    graph = graph if graph is not None else build_agent(settings)
    app = FastAPI(title="FlightIntelAgent", version=pkg_version("flightintel"))

    # Plain def endpoints on purpose: FastAPI runs them in a thread pool,
    # so the seconds-long blocking agent call never stalls the event loop.
    @app.post("/ask", response_model=AgentAnswer)
    def ask_endpoint(body: AskRequest) -> AgentAnswer:
        answer, _messages = ask(
            graph, body.question, settings, trace_metadata=_trace_metadata(settings)
        )
        return answer

    @app.get("/health", response_model=HealthReport)
    def health() -> HealthReport:
        products = _check_products_db(settings)
        vector = _check_vector_store(settings)
        if products != "ok":
            status = "down"
        elif vector == "ok":
            status = "ok"
        else:
            # The docs path is additive (PHASE2_PLAN Q9): a missing or dead
            # vector store degrades the service, never downs it.
            status = "degraded"
        return HealthReport(status=status, products_db=products, vector_store=vector)

    return app
