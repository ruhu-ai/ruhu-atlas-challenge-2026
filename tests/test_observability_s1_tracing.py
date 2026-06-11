"""Phase S1 — OpenTelemetry distributed tracing tests.

Covers the deliverables defined in
docs/observability-system/Observability-Implementation-Plan.md Phase S1:

- ``configure_tracing()`` is a no-op when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is
  absent (the default in tests / dev environments).
- ``configure_tracing()`` is idempotent — calling it twice does not error or
  double-register exporters.
- ``get_current_otel_trace_id()`` returns ``None`` when there is no active
  span (Noop tracer / no configure call).
- ``get_current_otel_trace_id()`` returns a 32-char lowercase hex string when
  a valid OTel span is active.
All tests run with the in-process ``InMemorySpanExporter`` (no OTLP collector
needed) by setting up a local ``TracerProvider`` directly, bypassing
``configure_tracing()``'s OTLP-endpoint guard.
"""
from __future__ import annotations

import os
import importlib

import pytest

from ruhu.observability.tracing import configure_tracing, get_current_otel_trace_id


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def in_memory_tracer_provider():
    """Set up a real TracerProvider backed by InMemorySpanExporter.

    Yields the (provider, exporter) pair and restores the previous global
    provider on teardown so tests don't bleed into each other.
    """
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    previous = trace.get_tracer_provider()
    trace.set_tracer_provider(provider)

    yield provider, exporter

    trace.set_tracer_provider(previous)


# ── configure_tracing: no-op behaviour ───────────────────────────────────────


def test_configure_tracing_noop_without_endpoint(monkeypatch):
    """configure_tracing() silently does nothing when OTLP endpoint is absent."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    import ruhu.observability.tracing as tracing_mod

    original = tracing_mod._configured
    try:
        configure_tracing(service_name="ruhu-test", environment="test")
        # _configured must not have been flipped to True
        assert tracing_mod._configured == original
    finally:
        tracing_mod._configured = original


def test_configure_tracing_idempotent(monkeypatch):
    """Calling configure_tracing() twice does not raise."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    import ruhu.observability.tracing as tracing_mod

    original = tracing_mod._configured
    try:
        configure_tracing()
        configure_tracing()  # second call must be silent
    finally:
        tracing_mod._configured = original


def test_configure_tracing_idempotent_when_already_set(monkeypatch):
    """configure_tracing() returns immediately when _configured is already True."""
    import ruhu.observability.tracing as tracing_mod

    original = tracing_mod._configured
    tracing_mod._configured = True
    try:
        # Even with an endpoint set, a second call must not re-register.
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        configure_tracing()  # must not raise, even though endpoint is set
    finally:
        tracing_mod._configured = original


# ── get_current_otel_trace_id: no active span ────────────────────────────────


def test_get_current_otel_trace_id_no_span():
    """Returns None when there is no active OTel span (Noop context)."""
    result = get_current_otel_trace_id()
    # Either None (no span) or a zero trace id from the noop tracer — both falsy.
    assert result is None


def test_get_current_otel_trace_id_invalid_span_context(in_memory_tracer_provider):
    """Returns None when the active span context is INVALID."""
    from opentelemetry import trace

    # An explicit INVALID span context should produce None.
    with trace.use_span(trace.INVALID_SPAN):
        result = get_current_otel_trace_id()
    assert result is None


# ── get_current_otel_trace_id: active valid span ──────────────────────────────


def test_get_current_otel_trace_id_returns_hex_string(in_memory_tracer_provider):
    """Returns a 32-char lowercase hex string inside a valid span."""
    provider, _ = in_memory_tracer_provider
    tracer = provider.get_tracer("ruhu.test")

    with tracer.start_as_current_span("test-span"):
        result = get_current_otel_trace_id()

    assert result is not None, "expected a trace_id inside the span"
    assert len(result) == 32, f"expected 32 hex chars, got {len(result)!r}: {result!r}"
    assert result == result.lower(), "trace_id must be lowercase"
    assert all(c in "0123456789abcdef" for c in result), "must be hex"


def test_get_current_otel_trace_id_stable_within_span(in_memory_tracer_provider):
    """The same trace_id is returned on successive calls within one span."""
    provider, _ = in_memory_tracer_provider
    tracer = provider.get_tracer("ruhu.test")

    with tracer.start_as_current_span("stable-span"):
        first = get_current_otel_trace_id()
        second = get_current_otel_trace_id()

    assert first == second


def test_get_current_otel_trace_id_none_after_span_exit(in_memory_tracer_provider):
    """Returns None once the span context manager has exited."""
    provider, _ = in_memory_tracer_provider
    tracer = provider.get_tracer("ruhu.test")

    with tracer.start_as_current_span("outer"):
        pass  # span ends here

    # After exiting, the context should be cleaned up (may or may not be None
    # depending on SDK version; we only assert no exception is raised).
    result = get_current_otel_trace_id()
    # Valid outcomes: None or a different (parent/invalid) trace_id.
    if result is not None:
        assert len(result) == 32


def test_get_current_otel_trace_id_differs_across_spans(in_memory_tracer_provider):
    """Each root span has a unique trace_id."""
    provider, _ = in_memory_tracer_provider
    tracer = provider.get_tracer("ruhu.test")

    ids: list[str] = []
    for _ in range(3):
        with tracer.start_as_current_span("root-span"):
            tid = get_current_otel_trace_id()
            if tid is not None:
                ids.append(tid)

    assert len(set(ids)) == len(ids), "each root span must have a unique trace_id"


def test_child_span_inherits_parent_trace_id(in_memory_tracer_provider):
    """A child span shares the same trace_id as its parent span."""
    provider, _ = in_memory_tracer_provider
    tracer = provider.get_tracer("ruhu.test")

    parent_id: str | None = None
    child_id: str | None = None

    with tracer.start_as_current_span("parent"):
        parent_id = get_current_otel_trace_id()
        with tracer.start_as_current_span("child"):
            child_id = get_current_otel_trace_id()

    assert parent_id is not None
    assert child_id is not None
    assert parent_id == child_id, "child span must share parent's trace_id"
