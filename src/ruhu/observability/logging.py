from __future__ import annotations

import logging
import re
import uuid

import structlog
from starlette.types import ASGIApp, Receive, Scope, Send


_SENSITIVE_LOG_KEYS = {
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "token",
    "authorization",
    "auth_header",
    "credential",
    "private_key",
    "card_number",
    "cvv",
    "ssn",
    "x-api-key",
    "x_api_key",
}


def redact_sensitive_keys(_: object, __: str, event_dict: dict) -> dict:
    """Redact obvious secret-bearing top-level keys before rendering."""
    for key in list(event_dict.keys()):
        normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower().replace("-", "_")
        if normalized in _SENSITIVE_LOG_KEYS:
            event_dict[key] = "[REDACTED]"
    return event_dict


def inject_otel_trace_id(_: object, __: str, event_dict: dict) -> dict:
    """Inject the active OpenTelemetry trace_id when a span is present."""
    if "otel_trace_id" in event_dict:
        return event_dict
    try:
        from opentelemetry import trace
    except Exception:
        return event_dict
    span = trace.get_current_span()
    context = span.get_span_context()
    if not getattr(context, "is_valid", False):
        return event_dict
    event_dict["otel_trace_id"] = f"{context.trace_id:032x}"
    return event_dict


def configure_structlog(*, environment: str = "production") -> None:
    """Configure structlog for JSON output with ISO timestamps.

    Must be called once at application startup, before any loggers are created.
    Safe to call multiple times (structlog.configure is idempotent).
    """
    level = logging.DEBUG if environment == "development" else logging.INFO
    logging.basicConfig(format="%(message)s")
    logging.getLogger().setLevel(level)
    renderer = (
        structlog.dev.ConsoleRenderer()
        if environment == "development"
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            inject_otel_trace_id,
            redact_sensitive_keys,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


class RequestIDMiddleware:
    """Pure ASGI middleware.

    Reads ``X-Request-ID`` from the inbound request if present; otherwise
    generates a new UUID4.  Binds ``request_id`` into structlog context-vars so
    every log statement emitted during the request lifetime carries it
    automatically.  Appends ``X-Request-ID`` to the response headers.

    This must be the *outermost* middleware so the binding is established before
    any inner middleware or route handler emits a log line.  In Starlette,
    "outermost" means registered *last* via ``app.add_middleware()``.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # scope["headers"] is list[tuple[bytes, bytes]] — never a dict
        raw_headers: dict[bytes, bytes] = dict(scope.get("headers", []))
        inbound = raw_headers.get(b"x-request-id", b"").decode("ascii", errors="replace").strip()
        request_id = inbound if inbound else str(uuid.uuid4())

        structlog.contextvars.clear_contextvars()
        bindings = {"request_id": request_id}
        otel_event = inject_otel_trace_id(None, "info", {})
        if "otel_trace_id" in otel_event:
            bindings["otel_trace_id"] = otel_event["otel_trace_id"]
        structlog.contextvars.bind_contextvars(**bindings)

        async def send_with_id(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_id)
