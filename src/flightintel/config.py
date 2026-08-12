"""Application settings, loaded from the environment and the git-ignored .env."""

from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    flight_db_path: Path
    # SecretStr keeps the key out of repr/logs; call .get_secret_value() at the
    # single point of use.
    gemini_api_key: SecretStr | None = None
    # Pinned id, never a -latest alias: eval results must record the exact
    # model, and an alias can change silently underneath them.
    gemini_model: str = "gemini-3.5-flash"
    # Pinned per plan Q4, chosen against the live model list; recorded in the
    # index manifest with every build.
    embedding_model: str = "gemini-embedding-2"
    # Local AirflowOSky checkout; only the corpus builder needs it.
    upstream_repo_path: Path | None = None
    # Local pgvector store DSN (WSL2 Postgres, port 5433; plan Q7). SecretStr
    # because the DSN embeds the database password.
    pg_dsn: SecretStr | None = None
    # Langfuse tracing (PHASE3_PLAN Q1/Q2). All optional: without keys,
    # tracing is off and the agent behaves exactly as before. The SDK is
    # constructed from these fields explicitly, never from os.environ,
    # because pydantic-settings reads .env without exporting it.
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    # Extra Langfuse tags on every trace from this process, comma
    # separated. The cloud service sets TRACE_TAGS=env:cloud (PHASE4_PLAN
    # Q5) so cloud traffic filters cleanly from local traffic.
    trace_tags: str | None = None
    # Cloud vector store (PHASE4_PLAN Q2c): fully qualified BigQuery table
    # id, project.dataset.table. When set it wins over PG_DSN; with both
    # unset the docs path degrades honestly, unchanged.
    bq_vector_table: str | None = None
    # ADC credentials file for LOCAL BigQuery access (Q2d). Unset in the
    # cloud, where ambient ADC is the runtime service account.
    bq_credentials: Path | None = None

    @field_validator("flight_db_path")
    @classmethod
    def db_must_exist(cls, v: Path) -> Path:
        if not v.is_file():
            raise ValueError(
                f"FLIGHT_DB_PATH does not point to a file: {v}. "
                "Set it in .env (see .env.example)."
            )
        return v


def load_settings() -> Settings:
    return Settings()


def vector_backend(settings: Settings) -> str | None:
    """Which docs backend this configuration selects (PHASE4_PLAN Q2c):
    'bigquery' beats 'pgvector' beats None (honest degradation)."""
    if settings.bq_vector_table is not None:
        return "bigquery"
    if settings.pg_dsn is not None:
        return "pgvector"
    return None
