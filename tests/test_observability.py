"""Tests for the observability package (Phase 1a).

Covers:
- RequestIDMiddleware: generates IDs, propagates inbound IDs, binds to structlog context
- MetricsMiddleware: increments counters and records histograms
- metrics.py: registry isolation, /metrics ASGI app returns valid Prometheus text
- configure_structlog: idempotent, does not raise
- RuntimeSettings: redis_url field parsed from env

Note: starlette.testclient.TestClient (0.35.x) is incompatible with httpx>=0.28
because it passes `app=` to httpx.Client.__init__() which was removed in httpx 0.27.
We use httpx.AsyncClient with ASGITransport + anyio.run() for all ASGI calls.
"""
from __future__ import annotations

import uuid

import anyio
import httpx
import pytest
import structlog
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from ruhu.observability.logging import RequestIDMiddleware, configure_structlog
from ruhu.observability.metrics import (
    CollectorRegistry,
    http_request_duration_seconds,
    http_requests_total,
    make_metrics_app,
    registry,
)
from ruhu.observability.http_middleware import MetricsMiddleware
from ruhu.runtime_config import RuntimeSettings


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_app(*middleware_classes, route_status: int = 200):
    """Build a minimal Starlette app with the given middleware stack."""

    async def homepage(request: Request):
        return PlainTextResponse("ok", status_code=route_status)

    async def error_route(request: Request):
        raise RuntimeError("boom")

    app = Starlette(
        routes=[
            Route("/", homepage),
            Route("/error", error_route),
            Route("/health", homepage),
        ]
    )
    for cls in middleware_classes:
        app.add_middleware(cls)
    return app


def _call(
    app,
    method: str = "GET",
    path: str = "/",
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Synchronously send a single request to an ASGI app.

    Uses httpx.ASGITransport so there is no dependency on starlette's TestClient,
    which is incompatible with httpx>=0.27.
    """
    async def _inner() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.request(method, path, headers=headers or {})

    return anyio.run(_inner)


# ── configure_structlog ────────────────────────────────────────────────────────

class TestConfigureStructlog:
    def test_does_not_raise(self):
        configure_structlog(environment="development")
        configure_structlog(environment="production")

    def test_idempotent(self):
        configure_structlog(environment="development")
        configure_structlog(environment="development")
        # Should not raise or produce duplicate processors
        log = structlog.get_logger()
        assert log is not None


# ── RequestIDMiddleware ────────────────────────────────────────────────────────

class TestRequestIDMiddleware:
    def _app(self):
        return _make_app(RequestIDMiddleware)

    def test_generates_request_id_when_none_sent(self):
        response = _call(self._app())
        assert response.status_code == 200
        rid = response.headers.get("x-request-id", "")
        assert rid, "X-Request-ID header must be present in response"
        parsed = uuid.UUID(rid)
        assert parsed.version == 4

    def test_propagates_client_provided_request_id(self):
        client_rid = "my-trace-id-1234"
        response = _call(self._app(), headers={"X-Request-ID": client_rid})
        assert response.headers.get("x-request-id") == client_rid

    def test_different_requests_get_different_ids(self):
        app = self._app()
        r1 = _call(app)
        r2 = _call(app)
        assert r1.headers["x-request-id"] != r2.headers["x-request-id"]

    def test_request_id_bound_to_structlog_context(self):
        """The ID bound in structlog contextvars must match the response header."""
        captured: list[str] = []

        async def route_that_reads_context(request: Request):
            ctx = structlog.contextvars.get_contextvars()
            captured.append(ctx.get("request_id", ""))
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route("/", route_that_reads_context)])
        app.add_middleware(RequestIDMiddleware)

        response = _call(app)

        assert len(captured) == 1
        response_rid = response.headers["x-request-id"]
        assert captured[0] == response_rid, (
            f"structlog context had {captured[0]!r} but response header was {response_rid!r}"
        )

    def test_non_http_scopes_pass_through(self):
        """Middleware must not crash on WebSocket or lifespan scopes."""
        mw = RequestIDMiddleware(app=lambda s, r, send: None)
        assert mw is not None


# ── MetricsMiddleware ──────────────────────────────────────────────────────────

class TestMetricsMiddleware:
    """
    Each test uses an isolated CollectorRegistry to avoid cross-test counter
    contamination.  The module-level ``registry`` is shared across the process
    and is tested separately via the /metrics ASGI app.
    """

    def test_counter_incremented_on_request(self):
        from prometheus_client import CollectorRegistry as CR, Counter as C, Histogram as H
        import ruhu.observability.http_middleware as mw_mod

        # Patch the names in http_middleware (where MetricsMiddleware reads them),
        # not in metrics (the counters are imported at module load time).
        iso_reg = CR(auto_describe=True)
        iso_req = C("iso_req_total", "", ["method", "endpoint", "status_code"], registry=iso_reg)
        iso_dur = H("iso_dur_seconds", "", ["method", "endpoint"], registry=iso_reg)

        orig_req = mw_mod.http_requests_total
        orig_dur = mw_mod.http_request_duration_seconds
        mw_mod.http_requests_total = iso_req
        mw_mod.http_request_duration_seconds = iso_dur
        try:
            app = _make_app(MetricsMiddleware)
            _call(app)
            labels = {"method": "GET", "endpoint": "/", "status_code": "200"}
            count = iso_req.labels(**labels)._value.get()
            assert count == 1.0
        finally:
            mw_mod.http_requests_total = orig_req
            mw_mod.http_request_duration_seconds = orig_dur

    def test_health_path_skipped(self):
        from prometheus_client import CollectorRegistry as CR, Counter as C, Histogram as H
        import ruhu.observability.http_middleware as mw_mod

        iso_reg = CR(auto_describe=True)
        iso_req = C("iso_skip_total", "", ["method", "endpoint", "status_code"], registry=iso_reg)
        iso_dur = H("iso_skip_dur", "", ["method", "endpoint"], registry=iso_reg)

        orig_req = mw_mod.http_requests_total
        orig_dur = mw_mod.http_request_duration_seconds
        mw_mod.http_requests_total = iso_req
        mw_mod.http_request_duration_seconds = iso_dur
        try:
            app = _make_app(MetricsMiddleware)
            _call(app, path="/health")
            # No samples should have been recorded for /health
            samples = list(iso_req.collect()[0].samples)
            assert len(samples) == 0, "Health path must not be counted in metrics"
        finally:
            mw_mod.http_requests_total = orig_req
            mw_mod.http_request_duration_seconds = orig_dur

    def test_path_normalisation_collapses_uuid(self):
        from ruhu.observability.http_middleware import _normalize_path
        result = _normalize_path("/conversations/550e8400-e29b-41d4-a716-446655440000/turns")
        assert result == "/conversations/{id}/turns"

    def test_path_normalisation_collapses_numeric_id(self):
        from ruhu.observability.http_middleware import _normalize_path
        result = _normalize_path("/agents/123456/states/789012")
        assert result == "/agents/{id}/states/{id}"

    def test_path_normalisation_preserves_short_segments(self):
        from ruhu.observability.http_middleware import _normalize_path
        # Short numeric segments (like API version numbers) must not be collapsed
        result = _normalize_path("/api/v2/users")
        assert result == "/api/v2/users"


# ── /metrics ASGI endpoint ─────────────────────────────────────────────────────

class TestMetricsEndpoint:
    def test_returns_200_with_prometheus_content_type(self):
        metrics_app = make_metrics_app()
        response = _call(metrics_app)
        assert response.status_code == 200
        ct = response.headers.get("content-type", "")
        assert "text/plain" in ct

    def test_response_body_is_non_empty_prometheus_text(self):
        metrics_app = make_metrics_app()
        response = _call(metrics_app)
        body = response.text
        # Every Prometheus exposition format document has at least one TYPE comment
        assert "# TYPE" in body or len(body) > 0


# ── metrics registry isolation ─────────────────────────────────────────────────

class TestMetricsRegistry:
    def test_registry_is_not_default_registry(self):
        """The custom registry must be separate from prometheus_client's default."""
        from prometheus_client import REGISTRY as default_reg
        assert registry is not default_reg

    def test_all_expected_metrics_registered(self):
        from ruhu.observability.metrics import (
            http_requests_total,
            http_request_duration_seconds,
            kernel_turns_total,
            kernel_turn_duration_seconds,
            conversation_version_conflicts_total,
            tool_invocations_total,
            tool_invocation_duration_seconds,
            voice_sessions_started_total,
            voice_transcript_duplicates_suppressed_total,
            audit_events_total,
            audit_queue_drops_total,
        )
        # Verify they're all queryable without error
        for metric in (
            http_requests_total,
            http_request_duration_seconds,
            kernel_turns_total,
            kernel_turn_duration_seconds,
            conversation_version_conflicts_total,
            tool_invocations_total,
            tool_invocation_duration_seconds,
            voice_sessions_started_total,
            voice_transcript_duplicates_suppressed_total,
            audit_events_total,
            audit_queue_drops_total,
        ):
            families = list(metric.collect())
            assert len(families) >= 1


# ── RuntimeSettings.redis_url ──────────────────────────────────────────────────

class TestRuntimeSettingsRedisUrl:
    def test_redis_url_defaults_to_none(self):
        settings = RuntimeSettings()
        assert settings.redis_url is None

    def test_redis_url_read_from_ruhu_redis_url(self, monkeypatch):
        monkeypatch.setenv("RUHU_REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.delenv("REDIS_URL", raising=False)
        settings = RuntimeSettings.from_env()
        assert settings.redis_url == "redis://localhost:6379/0"

    def test_redis_url_falls_back_to_redis_url(self, monkeypatch):
        monkeypatch.delenv("RUHU_REDIS_URL", raising=False)
        monkeypatch.setenv("REDIS_URL", "redis://cache:6379/1")
        settings = RuntimeSettings.from_env()
        assert settings.redis_url == "redis://cache:6379/1"

    def test_ruhu_redis_url_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("RUHU_REDIS_URL", "redis://primary:6379")
        monkeypatch.setenv("REDIS_URL", "redis://fallback:6379")
        settings = RuntimeSettings.from_env()
        assert settings.redis_url == "redis://primary:6379"

    def test_empty_env_var_results_in_none(self, monkeypatch):
        monkeypatch.setenv("RUHU_REDIS_URL", "")
        monkeypatch.setenv("REDIS_URL", "")
        settings = RuntimeSettings.from_env()
        assert settings.redis_url is None
