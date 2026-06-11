"""OpenTelemetry distributed tracing — Phase S1.

Responsible for:
  - Configuring the global TracerProvider with an OTLP gRPC exporter.
  - Registering the W3C TraceContext + Baggage composite propagator so that
    ``traceparent`` / ``tracestate`` headers are honoured on inbound requests
    and injected on outbound httpx calls.
  - Auto-instrumenting FastAPI, SQLAlchemy, and httpx so every request and DB
    query becomes a child span of the inbound trace — no hand-rolled spans
    needed in application code.
  - Providing ``get_current_otel_trace_id()`` so the kernel can stamp
    ``TurnTrace.otel_trace_id`` on every turn, giving ops staff a two-hop
    pivot path:  Prometheus alert exemplar → Tempo span → source TurnTrace.

Design constraints
------------------
* **No-op when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset.**  Tests and
  development environments that don't need a collector run untouched.
* **Idempotent.**  Calling ``configure_tracing()`` more than once (e.g. in
  test teardown / re-setup) is safe — subsequent calls are silent no-ops.
* **Graceful import errors.**  Instrumentation wrappers are imported lazily
  and wrapped in ``try/except ImportError`` so that optional packages
  missing from a slim install don't crash startup.

Middleware wiring
-----------------
``configure_tracing(app=app)`` adds ``OpenTelemetryMiddleware`` (via
``FastAPIInstrumentor``) as the *outermost* middleware layer, so the
OTel span is established before ``RequestIDMiddleware`` runs.  This
means the trace_id is available for Phase S2 structured-log correlation.

Propagation
-----------
Uses W3C TraceContext (``traceparent`` / ``tracestate``) + W3C Baggage.
Downstream services that speak W3C propagation get automatic parent-span
injection with no extra headers to maintain.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

_configured = False


def configure_tracing(
    *,
    service_name: str = "ruhu",
    environment: str = "production",
    app: "FastAPI | None" = None,
) -> None:
    """Configure OpenTelemetry tracing.  No-op when OTLP endpoint is absent.

    Parameters
    ----------
    service_name:
        Populates the ``service.name`` OTel resource attribute.
    environment:
        Populates the ``deployment.environment`` resource attribute
        (e.g. ``"production"``, ``"staging"``, ``"development"``).
    app:
        When provided, ``FastAPIInstrumentor`` wraps this app instance so
        every HTTP request receives its own span under the trace.  If omitted,
        HTTP-level instrumentation is skipped (useful for worker processes that
        don't serve HTTP).
    """
    global _configured

    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not otlp_endpoint:
        return  # No collector configured — skip silently.

    if _configured:
        return  # Idempotent.

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.propagators.composite import CompositePropagator
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    from opentelemetry.baggage.propagation import W3CBaggagePropagator

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": _service_version(),
            "deployment.environment": environment,
        }
    )

    exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # W3C TraceContext + Baggage: honours ``traceparent``/``tracestate`` headers
    # on inbound requests and propagates them on outbound httpx calls.
    set_global_textmap(
        CompositePropagator(
            [
                TraceContextTextMapPropagator(),
                W3CBaggagePropagator(),
            ]
        )
    )

    # ── FastAPI auto-instrumentation ──────────────────────────────────────────
    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor().instrument_app(
                app,
                excluded_urls="health,metrics",
            )
        except ImportError:
            pass

    # ── SQLAlchemy auto-instrumentation ───────────────────────────────────────
    # Hooks into the SQLAlchemy event system — no engine reference needed here.
    # Engines created *after* this call are automatically instrumented.
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument(enable_commenter=False)
    except ImportError:
        pass

    # ── httpx auto-instrumentation ────────────────────────────────────────────
    # Wraps ``httpx.Client`` and ``httpx.AsyncClient`` globally so outbound
    # calls to LLM providers, webhooks, and phone APIs emit child spans and
    # inject ``traceparent`` headers automatically.
    # Suppress request/response bodies and headers to prevent PII leakage into traces.
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        def _suppress_body_hook(span, _) -> None:
            """Suppress HTTP request/response bodies and headers to prevent PII in traces."""
            if span.is_recording():
                for attr in ("http.request.body", "http.response.body",
                             "http.request.headers", "http.response.headers"):
                    span.set_attribute(attr, "[SUPPRESSED]")

        HTTPXClientInstrumentor().instrument(
            request_hook=_suppress_body_hook,
            response_hook=_suppress_body_hook,
        )
    except ImportError:
        pass

    _configured = True


def get_current_otel_trace_id() -> str | None:
    """Return the W3C trace-id of the active OTel span as a 32-hex string.

    Returns ``None`` when:
    - There is no active span in the current execution context.
    - The active span context is invalid (e.g. the NoopTracer is in use
      because ``configure_tracing`` was never called or skipped as a no-op).
    - ``opentelemetry-api`` is not importable.

    The returned string is 32 lowercase hex characters (128-bit trace_id),
    matching the format emitted by Tempo / Jaeger and stored in
    ``TurnTrace.otel_trace_id``.
    """
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx is None or not ctx.is_valid:
            return None
        return format(ctx.trace_id, "032x")
    except Exception:
        return None


def _service_version() -> str:
    try:
        from importlib.metadata import version

        return version("ruhu")
    except Exception:
        return "unknown"
