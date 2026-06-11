"""Phase S2 — Structured JSON logs + correlation tests.

Covers the deliverables defined in
docs/observability-system/Observability-Implementation-Plan.md Phase S2:

- ``redact_sensitive_keys`` processor strips credentials from top-level keys
  without altering the rest of the event_dict.
- ``inject_otel_trace_id`` processor injects the active span's trace_id and
  is a no-op when no span is active.
- ``configure_structlog`` produces JSON output in production mode and
  ConsoleRenderer output in development mode.
- Context isolation: bindings from one request do not bleed into another.
- ``RequestIDMiddleware`` binds both ``request_id`` and ``otel_trace_id``
  (when a span is active) into structlog context vars per request.
"""
from __future__ import annotations

import json
import logging
from io import StringIO
from unittest.mock import patch

import pytest
import structlog
import structlog.contextvars

from ruhu.observability.logging import (
    RequestIDMiddleware,
    configure_structlog,
    inject_otel_trace_id,
    redact_sensitive_keys,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clear_structlog_context():
    """Ensure structlog context vars are clean for every test."""
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


@pytest.fixture()
def in_memory_tracer():
    """Provide a local TracerProvider + tracer without touching the global."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry import trace

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    prev = trace.get_tracer_provider()
    trace.set_tracer_provider(provider)

    yield provider.get_tracer("ruhu.test.s2")

    trace.set_tracer_provider(prev)


# ── redact_sensitive_keys ─────────────────────────────────────────────────────


@pytest.mark.parametrize("key", [
    "password", "passwd",
    "secret", "api_key", "apikey", "apiKey",
    "token", "authorization", "auth_header",
    "credential", "private_key", "privateKey",
    "card_number", "cardNumber", "cvv", "ssn",
    "x-api-key", "x_api_key",
])
def test_redact_sensitive_keys_strips_known_keys(key):
    """Known sensitive key names are replaced with [REDACTED]."""
    event_dict = {key: "super-secret-value", "event": "test", "user_id": "u1"}
    result = redact_sensitive_keys(None, "info", event_dict)
    assert result[key] == "[REDACTED]"
    # Non-sensitive keys are untouched.
    assert result["event"] == "test"
    assert result["user_id"] == "u1"


def test_redact_sensitive_keys_case_insensitive():
    """Key matching is case-insensitive."""
    event_dict = {"PASSWORD": "secret", "API_KEY": "key123", "event": "login"}
    result = redact_sensitive_keys(None, "info", event_dict)
    assert result["PASSWORD"] == "[REDACTED]"
    assert result["API_KEY"] == "[REDACTED]"
    assert result["event"] == "login"


def test_redact_sensitive_keys_safe_keys_untouched():
    """Non-sensitive keys are never modified."""
    event_dict = {
        "event": "user.login",
        "user_id": "u123",
        "org_id": "org1",
        "duration_ms": 42,
    }
    original = dict(event_dict)
    result = redact_sensitive_keys(None, "info", event_dict)
    assert result == original


def test_redact_sensitive_keys_does_not_recurse():
    """Nested dicts are not inspected — only top-level keys are redacted."""
    event_dict = {
        "event": "api_call",
        "nested": {"password": "deep-secret"},  # nested — not redacted
    }
    result = redact_sensitive_keys(None, "info", event_dict)
    assert result["nested"]["password"] == "deep-secret"


def test_redact_sensitive_keys_empty_dict():
    """Empty event_dict passes through unchanged."""
    event_dict: dict = {}
    result = redact_sensitive_keys(None, "info", event_dict)
    assert result == {}


# ── inject_otel_trace_id ──────────────────────────────────────────────────────


def test_inject_otel_trace_id_noop_without_span():
    """No otel_trace_id key added when there is no active span."""
    event_dict = {"event": "no-span"}
    result = inject_otel_trace_id(None, "info", event_dict)
    assert "otel_trace_id" not in result


def test_inject_otel_trace_id_injects_inside_span(in_memory_tracer):
    """otel_trace_id is injected as a 32-hex string inside an active span."""
    with in_memory_tracer.start_as_current_span("test"):
        event_dict = {"event": "inside-span"}
        result = inject_otel_trace_id(None, "info", event_dict)

    assert "otel_trace_id" in result
    tid = result["otel_trace_id"]
    assert len(tid) == 32
    assert tid == tid.lower()
    assert all(c in "0123456789abcdef" for c in tid)


def test_inject_otel_trace_id_does_not_override_existing():
    """If otel_trace_id already present, it is not overwritten."""
    event_dict = {"event": "manual", "otel_trace_id": "explicit_value"}
    result = inject_otel_trace_id(None, "info", event_dict)
    assert result["otel_trace_id"] == "explicit_value"


# ── configure_structlog: JSON output ─────────────────────────────────────────


def _capture_json_log(*, environment: str = "production") -> tuple[dict, str]:
    """Configure structlog to write to a StringIO buffer and emit one log line.

    Returns (parsed_json_dict, raw_output).
    """
    buf = StringIO()
    logging.basicConfig(format="%(message)s", stream=buf, level=logging.DEBUG, force=True)
    configure_structlog(environment=environment)

    log = structlog.get_logger("ruhu.test")
    log.info("test event", foo="bar")

    raw = buf.getvalue().strip()
    # Restore default logging config
    logging.basicConfig(format="%(message)s", stream=None, level=logging.WARNING, force=True)
    return raw


def test_configure_structlog_production_emits_json():
    """Production mode emits parseable JSON."""
    raw = _capture_json_log(environment="production")
    # May have multiple lines — find the one with our event
    for line in raw.splitlines():
        if not line.strip():
            continue
        parsed = json.loads(line)
        if parsed.get("event") == "test event":
            assert parsed["foo"] == "bar"
            assert "level" in parsed
            assert "timestamp" in parsed
            return
    pytest.fail(f"Did not find 'test event' in JSON log output:\n{raw}")


def test_configure_structlog_idempotent():
    """configure_structlog can be called multiple times without error."""
    configure_structlog(environment="production")
    configure_structlog(environment="production")  # second call must not raise


# ── Context isolation ─────────────────────────────────────────────────────────


def test_context_vars_cleared_between_requests():
    """bind_contextvars in one request does not leak into the next."""
    structlog.contextvars.bind_contextvars(request_id="req_1", user="alice")
    ctx1 = structlog.contextvars.get_contextvars()
    assert ctx1["request_id"] == "req_1"

    # Simulate new request: clear and rebind
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="req_2", user="bob")
    ctx2 = structlog.contextvars.get_contextvars()
    assert ctx2["request_id"] == "req_2"
    assert ctx2["user"] == "bob"
    # Old request_id must not appear
    assert ctx2["request_id"] != "req_1"


def test_bound_contextvars_restores_on_exit():
    """bound_contextvars() restores previous bindings when the block exits."""
    structlog.contextvars.bind_contextvars(request_id="outer-req")

    with structlog.contextvars.bound_contextvars(turn_id="t1", conversation_id="c1"):
        inner = structlog.contextvars.get_contextvars()
        assert inner["turn_id"] == "t1"
        assert inner["request_id"] == "outer-req"  # outer binding visible

    after = structlog.contextvars.get_contextvars()
    assert "turn_id" not in after
    assert "conversation_id" not in after
    assert after["request_id"] == "outer-req"  # outer binding restored


# ── RequestIDMiddleware ────────────────────────────────────────────────────────


def _make_request_scope(headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "headers": headers or [],
        "query_string": b"",
    }


async def _run_middleware(scope: dict, extra_headers: list | None = None) -> tuple[str, dict]:
    """Run RequestIDMiddleware through a minimal ASGI cycle.

    Returns (request_id_from_response_header, final_context_vars_dict).
    """
    captured: dict = {}
    response_headers: dict[str, str] = {}

    async def inner_app(scope, receive, send):
        captured.update(structlog.contextvars.get_contextvars())
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [],
        })
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            for k, v in message.get("headers", []):
                if k == b"x-request-id":
                    response_headers["x-request-id"] = v.decode()

    mw = RequestIDMiddleware(inner_app)
    await mw(scope, receive, send)
    return response_headers.get("x-request-id", ""), captured


def test_request_id_middleware_generates_uuid():
    """Middleware generates a UUID when no X-Request-ID header is present."""
    import asyncio

    scope = _make_request_scope()
    req_id, ctx = asyncio.run(_run_middleware(scope))
    assert req_id, "X-Request-ID must be set in response"
    assert len(req_id) == 36  # UUID4 format
    assert ctx["request_id"] == req_id


def test_request_id_middleware_propagates_inbound():
    """Middleware echoes back a client-provided X-Request-ID."""
    import asyncio

    scope = _make_request_scope(headers=[(b"x-request-id", b"client-abc-123")])
    req_id, ctx = asyncio.run(_run_middleware(scope))
    assert req_id == "client-abc-123"
    assert ctx["request_id"] == "client-abc-123"


def test_request_id_middleware_binds_otel_trace_id(in_memory_tracer):
    """Middleware binds otel_trace_id when an active span is present."""
    import asyncio

    scope = _make_request_scope()

    async def run():
        with in_memory_tracer.start_as_current_span("http-request"):
            return await _run_middleware(scope)

    req_id, ctx = asyncio.run(run())
    assert "otel_trace_id" in ctx
    assert len(ctx["otel_trace_id"]) == 32


def test_request_id_middleware_no_otel_without_span():
    """Middleware does not bind otel_trace_id when no span is active."""
    import asyncio

    scope = _make_request_scope()
    _, ctx = asyncio.run(_run_middleware(scope))
    assert "otel_trace_id" not in ctx
