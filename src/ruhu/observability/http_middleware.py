from __future__ import annotations

import re
import time

from starlette.types import ASGIApp, Receive, Scope, Send

from .metrics import http_request_duration_seconds, http_requests_total

# Paths that add no value to request metrics (health probes, schema introspection).
_SKIP_PATHS: frozenset[str] = frozenset(
    {"/health", "/live", "/ready", "/metrics", "/docs", "/redoc", "/openapi.json"}
)

# Patterns that collapse high-cardinality path segments to a fixed placeholder.
_UUID_RE = re.compile(
    r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
_NUMERIC_ID_RE = re.compile(r"/\d{5,}")  # 5+ digit runs (avoids collapsing short enums)


def _normalize_path(path: str) -> str:
    """Collapse UUIDs and long numeric IDs to ``{id}`` to bound label cardinality."""
    path = _UUID_RE.sub("/{id}", path)
    path = _NUMERIC_ID_RE.sub("/{id}", path)
    return path


class MetricsMiddleware:
    """Pure ASGI middleware that records HTTP request counts and latency.

    Skips health-check and introspection paths.  All paths are normalised before
    being used as Prometheus label values to prevent cardinality explosion from
    dynamic URL segments.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope["path"]
        if path in _SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        method: str = scope.get("method", "UNKNOWN")
        endpoint = _normalize_path(path)
        start = time.perf_counter()
        status_code = 500  # default if the app crashes before sending a response

        async def capture_status(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capture_status)
        finally:
            duration = time.perf_counter() - start
            http_requests_total.labels(
                method=method,
                endpoint=endpoint,
                status_code=str(status_code),
            ).inc()
            http_request_duration_seconds.labels(
                method=method,
                endpoint=endpoint,
            ).observe(duration)
