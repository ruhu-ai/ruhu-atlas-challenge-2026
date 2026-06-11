"""Tests for Phase 1b: rate limiting.

Covers:
- PublicRateLimitMiddleware:
  - Passes through non-rate-limited paths
  - Returns 503 when Redis is not configured (fail-closed for public paths)
  - Returns 503 when Redis raises an error
  - Returns 429 with Retry-After when limit is exceeded
  - Injects X-RateLimit-* headers on allowed requests
  - Correctly matches /auth/*, /public/widget/*, and /channels/* prefixes
  - Ignores paths outside the public prefix list
- _normalize_path pass-through (no change in rate_limit)
- make_org_rate_limiter:
  - Returns a FastAPI Depends instance
  - Fails open when Redis is None
- _check_limit (unit-level with a mock Redis):
  - Allowed on first call, increments count
  - Denied when count >= limit, returns retry_after
- IP extraction:
  - Uses X-Forwarded-For when present
  - Falls back to scope["client"]

Note: real Redis integration is tested manually / in CI with a live instance.
Unit tests mock the Redis client to avoid external dependencies.
"""
from __future__ import annotations

import hashlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from ruhu.rate_limit import (
    PublicRateLimitMiddleware,
    WidgetSessionRateLimitMiddleware,
    _check_limit,
    _rl_response_headers,
    make_org_rate_limiter,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_app(*middleware_args, route_status: int = 200):
    async def homepage(request: Request):
        return PlainTextResponse("ok", status_code=route_status)

    app = Starlette(routes=[
        Route("/", homepage),
        Route("/auth/magic-link/request", homepage),
        Route("/auth/refresh", homepage),
        Route("/public/widget/sessions", homepage),
        Route("/channels/whatsapp/messages", homepage),
        Route("/conversations", homepage),
        Route("/agents", homepage),
        Route("/health", homepage),
    ])
    for cls, kwargs in middleware_args:
        app.add_middleware(cls, **kwargs)
    return app


def _call(app, path: str = "/", *, headers: dict[str, str] | None = None) -> httpx.Response:
    async def _inner():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path, headers=headers or {})
    return anyio.run(_inner)


def _mw(redis_url: str | None = None):
    """Return (PublicRateLimitMiddleware, kwargs) tuple for _make_app."""
    return (PublicRateLimitMiddleware, {"redis_url": redis_url})


# ── _rl_response_headers ──────────────────────────────────────────────────────

class TestRlResponseHeaders:
    def test_returns_three_headers_when_no_retry(self):
        headers = _rl_response_headers(100, 50, 60)
        assert len(headers) == 3
        keys = {h[0] for h in headers}
        assert b"x-ratelimit-limit" in keys
        assert b"x-ratelimit-remaining" in keys
        assert b"x-ratelimit-reset" in keys

    def test_adds_retry_after_when_nonzero(self):
        headers = _rl_response_headers(100, 0, 60, retry_after=30)
        keys = {h[0] for h in headers}
        assert b"retry-after" in keys

    def test_limit_value_encoded(self):
        headers = dict(_rl_response_headers(200, 100, 60))
        assert headers[b"x-ratelimit-limit"] == b"200"


# ── PublicRateLimitMiddleware: pass-through paths ─────────────────────────────

class TestPublicRateLimitPassThrough:
    def test_non_rate_limited_path_passes_without_redis(self):
        app = _make_app(_mw(redis_url=None))
        response = _call(app, "/")
        assert response.status_code == 200

    def test_health_path_passes_without_redis(self):
        app = _make_app(_mw(redis_url=None))
        response = _call(app, "/health")
        assert response.status_code == 200

    def test_conversations_path_passes_without_redis(self):
        app = _make_app(_mw(redis_url=None))
        response = _call(app, "/conversations")
        assert response.status_code == 200

    def test_agents_path_passes_without_redis(self):
        app = _make_app(_mw(redis_url=None))
        response = _call(app, "/agents")
        assert response.status_code == 200


# ── PublicRateLimitMiddleware: no Redis configured → pass through ─────────────
# When redis_url is None the middleware has no rate-limiting capability but
# should not block requests — fail-closed only applies when Redis is configured
# but becomes temporarily unreachable (a Redis *error*, not absence of config).

class TestPublicRateLimitNoRedis:
    def test_auth_path_passes_without_redis(self):
        app = _make_app(_mw(redis_url=None))
        response = _call(app, "/auth/magic-link/request")
        assert response.status_code == 200

    def test_public_widget_path_passes_without_redis(self):
        app = _make_app(_mw(redis_url=None))
        response = _call(app, "/public/widget/sessions")
        assert response.status_code == 200

    def test_auth_refresh_passes_without_redis(self):
        app = _make_app(_mw(redis_url=None))
        response = _call(app, "/auth/refresh")
        assert response.status_code == 200

    def test_channels_path_passes_without_redis(self):
        app = _make_app(_mw(redis_url=None))
        response = _call(app, "/channels/whatsapp/messages")
        assert response.status_code == 200


# ── PublicRateLimitMiddleware: Redis error → fail closed ──────────────────────

class TestPublicRateLimitRedisError:
    def test_redis_error_returns_503_on_auth_path(self):
        async def _inner():
            mock_redis = AsyncMock()
            mock_redis.eval.side_effect = ConnectionError("Redis down")

            mw = PublicRateLimitMiddleware(
                app=MagicMock(), redis_url="redis://fake:6379"
            )
            mw._redis = mock_redis  # inject pre-built mock

            transport = httpx.ASGITransport(app=mw)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.get("/auth/magic-link/request")

        response = anyio.run(_inner)
        assert response.status_code == 503
        assert response.json()["detail"] == "rate_limiting_unavailable"

    def test_redis_error_returns_503_on_channels_path(self):
        async def _inner():
            mock_redis = AsyncMock()
            mock_redis.eval.side_effect = ConnectionError("Redis down")

            mw = PublicRateLimitMiddleware(
                app=MagicMock(), redis_url="redis://fake:6379"
            )
            mw._redis = mock_redis

            transport = httpx.ASGITransport(app=mw)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.get("/channels/whatsapp/messages")

        response = anyio.run(_inner)
        assert response.status_code == 503
        assert response.json()["detail"] == "rate_limiting_unavailable"


# ── PublicRateLimitMiddleware: rate limit exceeded ────────────────────────────

class TestPublicRateLimitExceeded:
    def test_returns_429_when_limit_exceeded(self):
        async def _inner():
            mock_redis = AsyncMock()
            # Lua script returns [0, 10, 45] → denied, 10 requests in window, retry in 45s
            mock_redis.eval = AsyncMock(return_value=[0, 10, 45])

            mw = PublicRateLimitMiddleware(
                app=MagicMock(), redis_url="redis://fake:6379"
            )
            mw._redis = mock_redis

            transport = httpx.ASGITransport(app=mw)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.get("/auth/magic-link/request")

        response = anyio.run(_inner)
        assert response.status_code == 429
        data = response.json()
        assert data["detail"] == "rate_limit_exceeded"
        assert data["retry_after"] == 45
        assert "retry-after" in {k.lower() for k in response.headers}


# ── PublicRateLimitMiddleware: allowed request headers ───────────────────────

class TestPublicRateLimitAllowed:
    def test_injects_ratelimit_headers_on_allowed_request(self):
        async def _inner():
            async def echo_ok(scope, receive, send):
                resp = PlainTextResponse("ok")
                await resp(scope, receive, send)

            mock_redis = AsyncMock()
            # Lua returns [1, 3, 0] → allowed, 3 requests used, no retry
            mock_redis.eval = AsyncMock(return_value=[1, 3, 0])

            mw = PublicRateLimitMiddleware(app=echo_ok, redis_url="redis://fake:6379")
            mw._redis = mock_redis

            transport = httpx.ASGITransport(app=mw)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.get("/auth/magic-link/request")

        response = anyio.run(_inner)
        assert response.status_code == 200
        header_keys = {k.lower() for k in response.headers}
        assert "x-ratelimit-limit" in header_keys
        assert "x-ratelimit-remaining" in header_keys
        assert "x-ratelimit-reset" in header_keys


# ── IP extraction ─────────────────────────────────────────────────────────────

class TestClientIpExtraction:
    """Trust-aware XFF handling.

    The middleware only honours ``X-Forwarded-For`` when the direct
    socket peer is in ``trusted_proxy_cidrs``.  Default (empty list) →
    always use the socket IP so an attacker can't rotate fake source
    IPs by setting XFF per request.
    """

    def _middleware(self, trusted_proxy_cidrs=()):
        return PublicRateLimitMiddleware(
            app=lambda scope, receive, send: None,
            trusted_proxy_cidrs=trusted_proxy_cidrs,
        )

    def test_ignores_forwarded_for_when_no_trusted_proxies(self):
        scope = {
            "type": "http",
            "headers": [(b"x-forwarded-for", b"203.0.113.5, 10.0.0.1")],
            "client": ("10.0.0.1", 12345),
        }
        mw = self._middleware()
        assert mw._client_ip(scope) == "10.0.0.1"

    def test_honours_forwarded_for_when_peer_is_trusted(self):
        scope = {
            "type": "http",
            "headers": [(b"x-forwarded-for", b"203.0.113.5, 10.0.0.1")],
            "client": ("10.0.0.1", 12345),
        }
        mw = self._middleware(trusted_proxy_cidrs=("10.0.0.0/8",))
        assert mw._client_ip(scope) == "203.0.113.5"

    def test_ignores_forwarded_for_when_peer_is_not_trusted(self):
        scope = {
            "type": "http",
            "headers": [(b"x-forwarded-for", b"203.0.113.5")],
            "client": ("198.51.100.7", 12345),
        }
        mw = self._middleware(trusted_proxy_cidrs=("10.0.0.0/8",))
        assert mw._client_ip(scope) == "198.51.100.7"

    def test_falls_back_to_scope_client(self):
        scope = {
            "type": "http",
            "headers": [],
            "client": ("192.168.1.100", 9876),
        }
        mw = self._middleware()
        assert mw._client_ip(scope) == "192.168.1.100"

    def test_handles_missing_client(self):
        scope = {
            "type": "http",
            "headers": [],
        }
        mw = self._middleware()
        assert mw._client_ip(scope) == "0.0.0.0"

    def test_ignores_malformed_cidr_entries(self):
        # A typo in the config shouldn't blow up the middleware.
        mw = self._middleware(trusted_proxy_cidrs=("not-a-cidr", "10.0.0.0/8"))
        scope = {
            "type": "http",
            "headers": [(b"x-forwarded-for", b"203.0.113.5")],
            "client": ("10.1.2.3", 12345),
        }
        assert mw._client_ip(scope) == "203.0.113.5"


# ── _check_limit unit tests ───────────────────────────────────────────────────

class TestCheckLimit:
    def test_allowed_returns_true_and_remaining(self):
        async def _inner():
            mock_redis = AsyncMock()
            mock_redis.eval = AsyncMock(return_value=[1, 5, 0])
            return await _check_limit(mock_redis, "test:key", limit=10, window_seconds=60)

        allowed, remaining, retry_after = anyio.run(_inner)
        assert allowed is True
        assert remaining == 5
        assert retry_after == 0

    def test_denied_returns_false_with_retry(self):
        async def _inner():
            mock_redis = AsyncMock()
            mock_redis.eval = AsyncMock(return_value=[0, 10, 30])
            return await _check_limit(mock_redis, "test:key", limit=10, window_seconds=60)

        allowed, remaining, retry_after = anyio.run(_inner)
        assert allowed is False
        assert remaining == 0
        assert retry_after == 30

    def test_eval_called_with_correct_numkeys(self):
        async def _inner():
            mock_redis = AsyncMock()
            mock_redis.eval = AsyncMock(return_value=[1, 1, 0])
            await _check_limit(mock_redis, "mykey", limit=5, window_seconds=30)
            return mock_redis.eval.call_args

        call_args = anyio.run(_inner)
        # First positional arg after script is numkeys=1
        assert call_args[0][1] == 1
        # Second positional is the key
        assert call_args[0][2] == "mykey"


# ── make_org_rate_limiter ─────────────────────────────────────────────────────

class TestMakeOrgRateLimiter:
    def test_returns_depends_instance(self):
        from fastapi.params import Depends
        result = make_org_rate_limiter(None)
        assert isinstance(result, Depends)

    def test_fails_open_when_redis_url_is_none(self):
        """With redis_url=None the inner dependency coroutine returns None immediately.

        We call the inner function directly rather than going through FastAPI's full
        DI machinery, which in FastAPI 0.109 doesn't auto-inject `Request` into
        Depends() sub-dependencies in all configurations.
        """
        dep = make_org_rate_limiter(None)
        # dep.dependency is the actual async _rate_limit function
        rate_limit_fn = dep.dependency

        async def _inner():
            mock_request = MagicMock()
            mock_response = MagicMock()
            mock_ctx = MagicMock()
            mock_ctx.principal.organization.organization_id = "org-test-123"
            # Should return None (pass through) when no Redis is configured
            return await rate_limit_fn(request=mock_request, response=mock_response, ctx=mock_ctx)

        result = anyio.run(_inner)
        assert result is None


# ── WidgetSessionRateLimitMiddleware ─────────────────────────────────────────

class TestWidgetSessionRateLimitMiddleware:
    def test_passes_through_non_widget_paths(self):
        async def _inner():
            async def echo_ok(scope, receive, send):
                resp = PlainTextResponse("ok")
                await resp(scope, receive, send)

            mock_redis = AsyncMock()
            mw = WidgetSessionRateLimitMiddleware(app=echo_ok, redis_url="redis://fake:6379")
            mw._redis = mock_redis

            transport = httpx.ASGITransport(app=mw)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.get("/conversations")

        response = anyio.run(_inner)
        assert response.status_code == 200

    def test_passes_through_when_no_session_token(self):
        async def _inner():
            async def echo_ok(scope, receive, send):
                resp = PlainTextResponse("ok")
                await resp(scope, receive, send)

            mock_redis = AsyncMock()
            mw = WidgetSessionRateLimitMiddleware(app=echo_ok, redis_url="redis://fake:6379")
            mw._redis = mock_redis

            transport = httpx.ASGITransport(app=mw)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.post("/public/widget/sessions/test/messages")

        response = anyio.run(_inner)
        assert response.status_code == 200

    def test_returns_429_when_session_limit_exceeded(self):
        async def _inner():
            async def echo_ok(scope, receive, send):
                resp = PlainTextResponse("ok")
                await resp(scope, receive, send)

            mock_redis = AsyncMock()
            mock_redis.eval = AsyncMock(return_value=[0, 30, 45])

            mw = WidgetSessionRateLimitMiddleware(app=echo_ok, redis_url="redis://fake:6379")
            mw._redis = mock_redis

            transport = httpx.ASGITransport(app=mw)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.post(
                    "/public/widget/sessions/test/messages",
                    headers={"X-Ruhu-Widget-Session-Token": "tok_abc123"},
                )

        response = anyio.run(_inner)
        assert response.status_code == 429
        assert "retry_after" in response.json()
        assert response.headers.get("retry-after") == "45"

    def test_allows_when_under_limit(self):
        async def _inner():
            async def echo_ok(scope, receive, send):
                resp = PlainTextResponse("ok")
                await resp(scope, receive, send)

            mock_redis = AsyncMock()
            mock_redis.eval = AsyncMock(return_value=[1, 5, 0])

            mw = WidgetSessionRateLimitMiddleware(app=echo_ok, redis_url="redis://fake:6379")
            mw._redis = mock_redis

            transport = httpx.ASGITransport(app=mw)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.post(
                    "/public/widget/sessions/test/messages",
                    headers={"X-Ruhu-Widget-Session-Token": "tok_abc123"},
                )

        response = anyio.run(_inner)
        assert response.status_code == 200

    def test_uses_message_limit_for_message_paths(self):
        async def _inner():
            async def echo_ok(scope, receive, send):
                resp = PlainTextResponse("ok")
                await resp(scope, receive, send)

            mock_redis = AsyncMock()
            mock_redis.eval = AsyncMock(return_value=[1, 1, 0])

            mw = WidgetSessionRateLimitMiddleware(app=echo_ok, redis_url="redis://fake:6379")
            mw._redis = mock_redis

            transport = httpx.ASGITransport(app=mw)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                await client.post(
                    "/public/widget/sessions/test/messages",
                    headers={"X-Ruhu-Widget-Session-Token": "tok_abc123"},
                )

            call_args = mock_redis.eval.call_args
            # Positional: script, numkeys, key, now_str, window_str, limit_str
            limit_arg = call_args[0][5]
            return int(limit_arg)

        limit = anyio.run(_inner)
        assert limit == 30  # messages limit from _WIDGET_SESSION_LIMITS

    def test_uses_voice_limit_for_voice_paths(self):
        async def _inner():
            async def echo_ok(scope, receive, send):
                resp = PlainTextResponse("ok")
                await resp(scope, receive, send)

            mock_redis = AsyncMock()
            mock_redis.eval = AsyncMock(return_value=[1, 1, 0])

            mw = WidgetSessionRateLimitMiddleware(app=echo_ok, redis_url="redis://fake:6379")
            mw._redis = mock_redis

            transport = httpx.ASGITransport(app=mw)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                await client.post(
                    "/public/widget/sessions/test/voice",
                    headers={"X-Ruhu-Widget-Session-Token": "tok_abc123"},
                )

            call_args = mock_redis.eval.call_args
            limit_arg = call_args[0][5]
            return int(limit_arg)

        limit = anyio.run(_inner)
        assert limit == 5  # voice limit from _WIDGET_SESSION_LIMITS

    def test_session_fingerprint_is_stable(self):
        scope = {
            "type": "http",
            "headers": [(b"x-ruhu-widget-session-token", b"tok_test_123")],
        }
        fp1 = WidgetSessionRateLimitMiddleware._session_fingerprint(scope)
        fp2 = WidgetSessionRateLimitMiddleware._session_fingerprint(scope)
        assert fp1 is not None
        assert fp1 == fp2
        assert len(fp1) == 16

    def test_different_tokens_produce_different_fingerprints(self):
        scope_a = {"type": "http", "headers": [(b"x-ruhu-widget-session-token", b"tok_aaa")]}
        scope_b = {"type": "http", "headers": [(b"x-ruhu-widget-session-token", b"tok_bbb")]}
        fp_a = WidgetSessionRateLimitMiddleware._session_fingerprint(scope_a)
        fp_b = WidgetSessionRateLimitMiddleware._session_fingerprint(scope_b)
        assert fp_a != fp_b

    def test_fails_open_on_redis_error(self):
        async def _inner():
            async def echo_ok(scope, receive, send):
                resp = PlainTextResponse("ok")
                await resp(scope, receive, send)

            mock_redis = AsyncMock()
            mock_redis.eval = AsyncMock(side_effect=ConnectionError("Redis down"))

            mw = WidgetSessionRateLimitMiddleware(app=echo_ok, redis_url="redis://fake:6379")
            mw._redis = mock_redis

            transport = httpx.ASGITransport(app=mw)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.post(
                    "/public/widget/sessions/test/messages",
                    headers={"X-Ruhu-Widget-Session-Token": "tok_abc123"},
                )

        response = anyio.run(_inner)
        assert response.status_code == 200  # fails open
