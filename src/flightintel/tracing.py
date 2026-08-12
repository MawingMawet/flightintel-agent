"""Langfuse tracing: one integration point, env-gated (PHASE3_PLAN Q2).

The SDK client is constructed explicitly from Settings because
pydantic-settings reads .env WITHOUT exporting it to os.environ; the
SDK's own env-var lookup would silently find nothing. Missing keys mean
no callback and byte-identical agent behavior (the PG_DSN degradation
pattern). The trace id is generated client-side and handed to the
handler, so the answer can carry it without depending on handler
internals.
"""

from flightintel.config import Settings


def tracing_enabled(settings: Settings) -> bool:
    return (
        settings.langfuse_public_key is not None
        and settings.langfuse_secret_key is not None
    )


def make_trace_config(settings: Settings) -> tuple[list, str | None]:
    """(callbacks, trace_id) for one agent run; ([], None) when tracing
    is not configured. Imports stay inside so the keyless path never
    touches the SDK."""
    if not tracing_enabled(settings):
        return [], None
    from langfuse import Langfuse
    from langfuse.langchain import CallbackHandler

    # The SDK caches clients per public_key; constructing again is cheap.
    Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        host=settings.langfuse_host,
    )
    trace_id = Langfuse.create_trace_id()
    handler = CallbackHandler(
        public_key=settings.langfuse_public_key,
        trace_context={"trace_id": trace_id},
    )
    return [handler], trace_id


def flush(settings: Settings) -> None:
    """Drain buffered spans now. The SDK batches in a background thread;
    a short-lived CLI or eval process can exit before it sends."""
    if not tracing_enabled(settings):
        return
    from langfuse import get_client

    get_client(public_key=settings.langfuse_public_key).flush()


def trace_url(settings: Settings, trace_id: str | None) -> str | None:
    if trace_id is None or not tracing_enabled(settings):
        return None
    from langfuse import get_client

    return get_client(public_key=settings.langfuse_public_key).get_trace_url(
        trace_id=trace_id
    )
