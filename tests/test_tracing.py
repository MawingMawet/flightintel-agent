"""Unit tests for the env-gated Langfuse seam (PHASE3_PLAN Q2): no keys
means no callbacks and an unchanged invoke config; keys mean one handler
plus a client-side trace id."""

from flightintel.config import Settings
from flightintel.tracing import make_trace_config, trace_url, tracing_enabled

DB = "evals/fixtures/sample.db"


def settings_without_keys():
    return Settings(flight_db_path=DB, _env_file=None)


def settings_with_keys():
    return Settings(
        flight_db_path=DB,
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key="sk-lf-test",
        _env_file=None,
    )


def test_no_keys_means_no_tracing():
    s = settings_without_keys()
    assert not tracing_enabled(s)
    callbacks, trace_id = make_trace_config(s)
    assert callbacks == [] and trace_id is None
    assert trace_url(s, "anything") is None


def test_partial_keys_stay_off():
    s = Settings(
        flight_db_path=DB, langfuse_public_key="pk-lf-test", _env_file=None
    )
    assert not tracing_enabled(s)
    assert make_trace_config(s) == ([], None)


def test_keys_yield_handler_and_trace_id():
    callbacks, trace_id = make_trace_config(settings_with_keys())
    assert len(callbacks) == 1
    assert isinstance(trace_id, str) and len(trace_id) == 32
    # a second run gets a fresh trace id
    _, second = make_trace_config(settings_with_keys())
    assert second != trace_id
