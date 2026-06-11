"""Pure ASGI audit middleware — captures all mutating HTTP requests.

Only POST/PUT/PATCH/DELETE are captured. GETs are noise.
Infrastructure paths (/health, /metrics, /docs, etc.) are skipped.

The middleware does NOT write to the database. It builds an AuditEvent and
hands it to the AuditEventRouter, which decides sync vs async write path
based on event type.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import structlog
from starlette.types import ASGIApp, Receive, Scope, Send

from .events import (
    AuditEvent,
    method_to_event_type,
    path_to_resource_type,
    redact_sensitive,
)

log = structlog.get_logger(__name__)

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_SKIP_PATHS = frozenset({
    "/health", "/live", "/ready", "/metrics", "/docs", "/redoc", "/openapi.json",
})

# Paths where auth context is managed by the endpoint itself (login/logout/refresh).
# The middleware still captures these — they produce auth.* events via the router.
_AUTH_PATHS = frozenset({
    "/auth/refresh",
    "/auth/logout",
    "/auth/magic-link/request",
    "/auth/magic-link/verify",
    "/auth/oauth/google/start",
    "/auth/oauth/sso/start",
    "/auth/oauth/callback",
    "/auth/invitations/accept",
})


class AuditMiddleware:
    """Pure ASGI middleware — zero BaseHTTPMiddleware overhead.

    Wraps the ``send`` callable to capture the response status code,
    then emits an AuditEvent in the ``finally`` block so crashes are
    still recorded.
    """

    def __init__(self, app: ASGIApp, *, router: Any) -> None:
        self.app = app
        self._router = router

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method: str = scope.get("method", "")
        path: str = scope["path"]

        if method not in _MUTATING_METHODS or path in _SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        status_code = 500
        start = time.perf_counter()

        async def capture(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            self._emit(scope, method, path, status_code, duration_ms)

    def _emit(
        self,
        scope: Scope,
        method: str,
        path: str,
        status_code: int,
        duration_ms: int,
    ) -> None:
        # Extract auth context from request state (set by AuthContextMiddleware)
        state = scope.get("state", {})
        auth_ctx = getattr(state, "auth_context", None)
        org_id: Optional[str] = None
        user_id: Optional[str] = None
        session_id: Optional[str] = None

        if auth_ctx and auth_ctx.principal:
            org = auth_ctx.principal.organization
            user = auth_ctx.principal.user
            session = auth_ctx.principal.session
            if org:
                org_id = org.organization_id
            if user:
                user_id = user.user_id
            if session:
                session_id = session.session_id

        # Without an org, the event cannot be tenant-scoped — skip.
        if org_id is None:
            return

        raw_headers: dict[bytes, bytes] = dict(scope.get("headers", []))
        request_id = structlog.contextvars.get_contextvars().get("request_id", "")

        outcome = "success" if status_code < 400 else ("denied" if status_code == 403 else "failure")

        event = AuditEvent(
            event_type=method_to_event_type(method),
            organization_id=org_id,
            outcome=outcome,
            actor_id=user_id,
            actor_ip=(scope.get("client") or ("",))[0],
            actor_session_id=session_id,
            resource_type=path_to_resource_type(path),
            http_method=method,
            http_path=path,
            http_status=status_code,
            duration_ms=duration_ms,
            request_id=request_id,
        )

        try:
            self._router.route(event)
        except Exception:
            log.warning("audit_middleware_route_failed", path=path, exc_info=True)
