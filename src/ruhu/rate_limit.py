"""
Rate limiting — two layers:

Layer 1 (PublicRateLimitMiddleware):
  Pure ASGI. Only targets unauthenticated ingress paths (/auth/*, /public/*, /channels/*).
  Keys by client IP (SHA-256 truncated to avoid storing raw IPs in Redis).
  Fails closed (returns 503) on Redis unavailability for these high-risk paths.

Layer 2 (make_org_rate_limiter):
  FastAPI Depends(). Targets authenticated endpoints.
  Keys by verified organization_id from request.state.auth_context.
  AuthContextMiddleware has already validated the token before this dependency
  runs — no raw JWT decoding here.
  Fails open (passes) on Redis unavailability for authenticated routes.
"""
from __future__ import annotations

import hashlib
import ipaddress
import secrets
import time
from typing import Optional, Sequence

from fastapi import Depends, HTTPException, Request, Response
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

import structlog

# Module-level import (not inside make_org_rate_limiter) so `typing.get_type_hints()`
# can resolve the RequestAuthContext annotation on the nested _rate_limit dependency.
# See tests/test_fastapi_signature_resolution.py for the PEP 563 trap background.
from ruhu.api_auth import RequestAuthContext, require_authenticated_context

_log = structlog.get_logger(__name__)

# ── Sliding window Lua script ──────────────────────────────────────────────────
#
# Uses a Redis sorted set (key = rate-limit key, score = timestamp, member = unique
# token).  Atomically removes stale entries, checks the count, and records the
# current request if within the limit.
#
# Returns: [allowed(0|1), current_count, retry_after_seconds]

_SLIDING_WINDOW_LUA = """
local key        = KEYS[1]
local now        = tonumber(ARGV[1])
local window     = tonumber(ARGV[2])
local limit      = tonumber(ARGV[3])
local window_start = now - window

redis.call('ZREMRANGEBYSCORE', key, 0, window_start)
local count = redis.call('ZCARD', key)

if count >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_after = 0
    if oldest and #oldest >= 2 then
        retry_after = math.ceil(tonumber(oldest[2]) + window - now)
    end
    return {0, count, retry_after}
end

-- Unique member: timestamp + random to allow multiple requests in the same millisecond
redis.call('ZADD', key, now, tostring(now) .. '-' .. tostring(math.random(1000000)))
redis.call('EXPIRE', key, math.ceil(window) + 1)
return {1, count + 1, 0}
"""


async def _check_limit(
    redis,
    key: str,
    *,
    limit: int,
    window_seconds: int,
) -> tuple[bool, int, int]:
    """Execute the sliding window check.  Returns (allowed, remaining, retry_after)."""
    now = time.time()
    result = await redis.eval(
        _SLIDING_WINDOW_LUA,
        1,           # numkeys
        key,         # KEYS[1]
        str(now),
        str(window_seconds),
        str(limit),
    )
    allowed_raw, count_raw, retry_after_raw = result
    count = int(count_raw)
    remaining = max(0, limit - count)
    return bool(int(allowed_raw)), remaining, int(retry_after_raw)


def _rl_response_headers(
    limit: int,
    remaining: int,
    window_seconds: int,
    retry_after: int = 0,
) -> list[tuple[bytes, bytes]]:
    """Standard rate-limit headers as raw ASGI byte tuples."""
    reset_at = int(time.time()) + window_seconds
    headers: list[tuple[bytes, bytes]] = [
        (b"x-ratelimit-limit",     str(limit).encode()),
        (b"x-ratelimit-remaining", str(remaining).encode()),
        (b"x-ratelimit-reset",     str(reset_at).encode()),
    ]
    if retry_after > 0:
        headers.append((b"retry-after", str(retry_after).encode()))
    return headers


# ─── Tier-based rate limiting (SaaS multi-tenant) ──────────────────────────────

# Plan slug → (requests_per_window, window_seconds)
_TIER_LIMITS: dict[str, tuple[int, int]] = {
    "free":         (60,   60),   # 1 request per second
    "starter":      (150,  60),   # 2.5 requests per second
    "professional": (300,  60),   # 5 requests per second
    "enterprise":   (600,  60),   # 10 requests per second
}
_TIER_DEFAULT = (60, 60)  # no active subscription → free tier defaults

# Hard caps — no tier can exceed these (security & cost protection)
# Path prefixes matched against request.url.path; earliest match wins
_ENDPOINT_HARD_LIMITS: dict[str, int] = {
    "/agent-templates": 20,  # template clone/create (LLM-heavy, cost-sensitive)
    "/knowledge":       30,  # document ingestion (embedding API cost)
    "/rules":           40,  # rules compilation (LLM-heavy)
    "/billing":         30,  # prevent rapid subscription changes
}


class _TierCache:
    """
    Two-level cache for org → plan_slug resolution.

    Layer 1 (local): Process-local dict with 30s TTL — zero latency cache hit.
    Layer 2 (redis): Distributed cache, 60s TTL — covers the intent of the tier
                     lookup (plan changes are ~infrequent within 60s).
    Layer 3 (store): Fallback to billing store via run_in_executor; sync-to-async bridge.

    In practice, after the first request per org, cache hits are >99%. Falls back
    gracefully on any error (returns None → free tier defaults).
    """

    def __init__(self, redis_url: Optional[str], billing_store) -> None:
        self._redis_url = redis_url
        self._billing_store = billing_store
        self._redis = None  # lazy init
        # {org_id: (plan_slug, rate_limit_multiplier, expiry_unix_time)}
        self._local: dict[str, tuple[str, float, float]] = {}

    async def _get_redis(self):
        if self._redis is None and self._redis_url:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=False,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
        return self._redis

    @staticmethod
    def _parse_redis_value(raw: bytes) -> tuple[str, float]:
        """Parse Redis value 'slug:multiplier' (e.g. 'enterprise:2.0').
        Falls back to slug-only for old format, returns (slug, 1.0)."""
        try:
            value = raw.decode()
            if ':' in value:
                slug, mult = value.rsplit(':', 1)
                return slug, float(mult)
            return value, 1.0
        except (ValueError, UnicodeDecodeError):
            return None, 1.0

    async def get_plan_info(self, org_id: str) -> tuple[Optional[str], float]:
        """Resolve plan info (slug, rate_limit_multiplier) for org.
        Returns (slug, multiplier) or (None, 1.0) if no active subscription."""
        now = time.time()

        # Layer 1: Local process cache (TTL 30s)
        cached = self._local.get(org_id)
        if cached:
            slug, multiplier, expiry = cached
            if now < expiry:
                return slug, multiplier
            # Expired — clear and continue to next layer
            del self._local[org_id]

        # Layer 2: Redis cache (TTL 60s)
        redis = await self._get_redis()
        if redis is not None:
            try:
                key = f"rl:plan_slug:{org_id}"
                value_bytes = await redis.get(key)
                if value_bytes:
                    slug, multiplier = self._parse_redis_value(value_bytes)
                    self._local[org_id] = (slug, multiplier, now + 30)
                    return slug, multiplier
            except Exception:
                pass  # Redis error — fall through to store

        # Layer 3: Billing store (sync, run in executor to avoid blocking)
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            sub = await loop.run_in_executor(
                None,
                self._billing_store.get_active_subscription,
                org_id,
            )
            if sub is None:
                # No active subscription
                return None, 1.0
            plan = await loop.run_in_executor(None, self._billing_store.get_plan, sub.plan_id)
            if plan is None:
                return None, 1.0
            slug = plan.slug
            multiplier = plan.rate_limit_multiplier

            # Populate Redis and local caches
            if redis is not None:
                try:
                    key = f"rl:plan_slug:{org_id}"
                    await redis.set(key, f"{slug}:{multiplier}", ex=60)
                except Exception:
                    pass  # Cache write failure — non-fatal
            self._local[org_id] = (slug, multiplier, now + 30)
            return slug, multiplier
        except Exception:
            # Store error — degrade gracefully
            pass

        return None, 1.0

    async def invalidate(self, org_id: str) -> None:
        """Invalidate cache for org (called on subscription change)."""
        self._local.pop(org_id, None)
        redis = await self._get_redis()
        if redis is not None:
            try:
                await redis.delete(f"rl:plan_slug:{org_id}")
            except Exception:
                pass


# ─── Layer 1: IP-keyed ASGI middleware for public/auth paths ──────────────────

# path_prefix → (requests_per_window, window_seconds)
_PUBLIC_PREFIX_LIMITS: dict[str, tuple[int, int]] = {
    "/auth":          (10,  60),    # auth endpoints — strict per IP
    "/public/widget": (120, 60),    # widget sessions — per IP
    "/channels":      (60,  60),    # public channel ingress before route auth
}


class PublicRateLimitMiddleware:
    """
    IP-based rate limiting for public, unauthenticated paths only.

    Authenticated routes are handled by ``make_org_rate_limiter()``.
    Fails closed on Redis unavailability — those paths warrant extra caution.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        redis_url: Optional[str] = None,
        trusted_proxy_cidrs: Sequence[str] = (),
    ) -> None:
        self.app = app
        self._redis_url = redis_url
        self._redis = None   # lazy-initialised on first request
        self._trusted_networks: list[ipaddress._BaseNetwork] = []
        for cidr in trusted_proxy_cidrs:
            cidr = cidr.strip()
            if not cidr:
                continue
            try:
                self._trusted_networks.append(ipaddress.ip_network(cidr, strict=False))
            except ValueError:
                # Silently ignore malformed CIDRs — better to lose one proxy
                # than to refuse service over a config typo.
                continue

    async def _get_redis(self):
        if self._redis is None and self._redis_url:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=False,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
        return self._redis

    def _match_limit(self, path: str) -> Optional[tuple[str, int, int]]:
        for prefix, (limit, window) in _PUBLIC_PREFIX_LIMITS.items():
            if path.startswith(prefix):
                return prefix, limit, window
        return None

    def _client_ip(self, scope: Scope) -> str:
        """Extract the client IP from the ASGI scope.

        Only consult ``X-Forwarded-For`` when the direct socket peer is
        in ``trusted_proxy_cidrs`` — otherwise an attacker trivially
        cycles fake IPs by rotating the header per request, defeating
        per-IP rate limits on ``/auth/*``, ``/public/*``, and ``/channels/*``.
        """
        client = scope.get("client") or ("0.0.0.0", 0)
        direct_ip = client[0]
        if not self._trusted_networks:
            # No trusted proxies configured → ignore XFF entirely.
            return direct_ip
        try:
            direct_addr = ipaddress.ip_address(direct_ip)
        except ValueError:
            return direct_ip
        if not any(direct_addr in network for network in self._trusted_networks):
            return direct_ip
        raw_headers: dict[bytes, bytes] = dict(scope.get("headers", []))
        xff = raw_headers.get(b"x-forwarded-for", b"").decode("ascii", errors="replace").strip()
        if xff:
            return xff.split(",")[0].strip()
        return direct_ip

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope["path"]
        match = self._match_limit(path)
        if match is None:
            await self.app(scope, receive, send)
            return

        prefix, limit, window = match

        redis = await self._get_redis()
        if redis is None:
            # Redis not configured at all — skip rate limiting and pass through.
            # Fail-closed only applies when Redis is configured but unreachable.
            await self.app(scope, receive, send)
            return

        ip = self._client_ip(scope)
        # Hash the IP so we don't store raw IP addresses in Redis
        ip_key = hashlib.sha256(ip.encode()).hexdigest()[:16]
        key = f"rl:public:{prefix.lstrip('/')}:{ip_key}"

        try:
            allowed, remaining, retry_after = await _check_limit(
                redis, key, limit=limit, window_seconds=window
            )
        except Exception:
            # Redis error — fail closed on public paths
            resp = JSONResponse(
                {"detail": "rate_limiting_unavailable"}, status_code=503
            )
            await resp(scope, receive, send)
            return

        if not allowed:
            rl_headers = {
                "X-RateLimit-Limit":     str(limit),
                "X-RateLimit-Remaining": "0",
                "Retry-After":           str(retry_after),
            }
            resp = JSONResponse(
                {"detail": "rate_limit_exceeded", "retry_after": retry_after},
                status_code=429,
                headers=rl_headers,
            )
            await resp(scope, receive, send)
            return

        async def _inject_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(_rl_response_headers(limit, remaining, window))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, _inject_headers)


# ─── Layer 2: Org-keyed FastAPI dependency for authenticated paths ─────────────

# route_prefix → (requests_per_window, window_seconds)
_ORG_ROUTE_LIMITS: dict[str, tuple[int, int]] = {
    "/conversations": (300, 60),
    "/agents":        (120, 60),
}
_ORG_DEFAULT_LIMIT = (200, 60)


def make_org_rate_limiter(redis_url: Optional[str], *, billing_store=None, bypass_secret: Optional[str] = None):
    """
    Return a FastAPI ``Depends()`` that enforces per-organization rate limits.

    Tier-aware: when billing_store is provided, limits are pulled from the org's
    active subscription plan (free/starter/professional/enterprise).
    Endpoint hard limits are hard caps that no tier can exceed (security/cost).
    Admin bypass: when bypass_secret is provided, requests with matching X-Ruhu-Internal-Secret
    header skip rate limiting entirely (for internal health checks, debugging, etc).

    Usage::

        org_rate_limiter = make_org_rate_limiter(
            settings.redis_url,
            billing_store=billing_store,
            bypass_secret=settings.internal_api_secret,
        )

        router = APIRouter(
            prefix="/conversations",
            dependencies=[org_rate_limiter],
        )

    The dependency reads ``organization_id`` from ``request.state.auth_context``,
    which ``AuthContextMiddleware`` has already verified — no JWT decoding here.
    Fails open on Redis unavailability (authenticated traffic continues).
    Fails open on billing store errors (tier lookup errors degrade to free tier defaults).
    """
    # Per-dependency Redis connection — encapsulated, not a module global
    _redis_conn = None
    # Tier cache — 2-level: local dict + Redis, falls back to billing_store
    _tier_cache = _TierCache(redis_url, billing_store) if billing_store else None

    async def _get_redis():
        nonlocal _redis_conn
        if _redis_conn is None and redis_url:
            import redis.asyncio as aioredis
            _redis_conn = aioredis.from_url(
                redis_url,
                decode_responses=False,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
        return _redis_conn

    async def _rate_limit(
        request: Request,
        response: Response,
        ctx: RequestAuthContext = Depends(require_authenticated_context),
    ) -> None:
        # Imports kept local — avoids hard dependency on observability package
        # when rate_limit module is imported standalone (e.g. in unit tests).
        from .observability.metrics import (
            rate_limit_decisions_total,
            rate_limit_bypass_total,
            rate_limit_tier_lookup_seconds,
            safe_observe,
        )

        path = request.url.path
        route_key = path.split("/")[1] if "/" in path else "default"

        # ─ Admin bypass ─
        if bypass_secret:
            incoming = request.headers.get("X-Ruhu-Internal-Secret", "")
            if secrets.compare_digest(incoming.encode(), bypass_secret.encode()):
                safe_observe(
                    "rate_limit_bypass_total",
                    rate_limit_bypass_total.labels(endpoint=route_key).inc,
                )
                _log.info(
                    "rate_limit_bypass_used",
                    endpoint=route_key,
                    path=path,
                )
                return  # skip rate limiting for internal/admin requests

        redis = await _get_redis()
        if redis is None:
            return  # No Redis — fail open for authenticated routes

        # Already verified by AuthContextMiddleware — no raw decoding
        org_id: str = ctx.principal.organization.organization_id

        # ─ Resolve effective rate limit ─
        # 1. Check for endpoint hard cap (security/cost limits)
        hard_cap = next(
            (v for k, v in _ENDPOINT_HARD_LIMITS.items() if path.startswith(k)),
            None,
        )

        # 2. Resolve tier RPM (from plan or defaults) — timed for cache efficiency
        tier_rpm = None
        window = 60
        slug: Optional[str] = None
        if _tier_cache:
            lookup_start = time.monotonic()
            try:
                slug, multiplier = await _tier_cache.get_plan_info(org_id)
                base_rpm, window = _TIER_LIMITS.get(slug or "", _TIER_DEFAULT)
                tier_rpm = max(1, int(base_rpm * multiplier))
            except Exception:
                # Tier lookup error — degrade to defaults
                tier_rpm, window = _TIER_DEFAULT
            finally:
                safe_observe(
                    "rate_limit_tier_lookup_seconds",
                    rate_limit_tier_lookup_seconds.observe,
                    time.monotonic() - lookup_start,
                )
        else:
            # No tier cache — use static route limits (legacy behavior)
            tier_rpm, window = next(
                (v for k, v in _ORG_ROUTE_LIMITS.items() if path.startswith(k)),
                _ORG_DEFAULT_LIMIT,
            )

        # 3. Effective limit: hard cap is a ceiling that no tier exceeds
        if hard_cap is not None:
            limit = min(tier_rpm, hard_cap)
        else:
            limit = tier_rpm

        tier_label = slug or "unknown"

        # ─ Check rate limit ─
        key = f"rl:org:{route_key}:{org_id}"

        try:
            allowed, remaining, retry_after = await _check_limit(
                redis, key, limit=limit, window_seconds=window
            )
        except Exception:
            return  # Redis error — fail open for authenticated routes

        # ─ Inject headers (success case) ─
        reset_at = int(time.time()) + window
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_at)

        # ─ Raise 429 if over limit ─
        if not allowed:
            safe_observe(
                "rate_limit_decisions_total",
                rate_limit_decisions_total.labels(
                    tier=tier_label, endpoint=route_key, decision="blocked"
                ).inc,
            )
            _log.warning(
                "rate_limit_exceeded",
                endpoint=route_key,
                tier=tier_label,
                limit=limit,
                hard_cap=hard_cap,
                retry_after=retry_after,
                org_id=org_id,
            )
            raise HTTPException(
                status_code=429,
                detail={"detail": "rate_limit_exceeded", "retry_after": retry_after},
                headers={
                    "X-RateLimit-Limit":     str(limit),
                    "X-RateLimit-Remaining": "0",
                    "Retry-After":           str(retry_after),
                },
            )

        safe_observe(
            "rate_limit_decisions_total",
            rate_limit_decisions_total.labels(
                tier=tier_label, endpoint=route_key, decision="allowed"
            ).inc,
        )

    return Depends(_rate_limit)


# ─── Layer 3: Widget-scoped rate limiting for /public/widget ────────────────

# Widget endpoints receive X-Ruhu-Widget-Session-Token which identifies the
# widget session.  We extract a stable fingerprint from it to scope limits
# per-session in addition to per-IP, preventing a single bot from starving
# widget capacity for other sessions or tenants.

_WIDGET_SESSION_LIMITS: dict[str, tuple[int, int]] = {
    "messages":     (30, 60),     # 30 messages per session per minute
    "voice":        (5,  60),     # 5 voice session starts per session per minute
    "default":      (60, 60),     # 60 requests per session per minute
}


class WidgetSessionRateLimitMiddleware:
    """
    Widget-session-scoped rate limiting for /public/widget paths.

    Runs after PublicRateLimitMiddleware (IP layer).  Keys by widget session
    token fingerprint so one compromised session cannot exhaust the IP limit
    shared with legitimate sessions from the same IP/network.

    Fails open if no session token is present (the endpoint itself will reject
    unauthenticated requests).
    """

    def __init__(self, app: ASGIApp, *, redis_url: Optional[str] = None) -> None:
        self.app = app
        self._redis_url = redis_url
        self._redis = None

    async def _get_redis(self):
        if self._redis is None and self._redis_url:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=False,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
        return self._redis

    @staticmethod
    def _session_fingerprint(scope: Scope) -> Optional[str]:
        """Extract a stable fingerprint from the widget session token header."""
        raw_headers: dict[bytes, bytes] = dict(scope.get("headers", []))
        token = raw_headers.get(b"x-ruhu-widget-session-token", b"").decode("ascii", errors="replace").strip()
        if not token:
            return None
        return hashlib.sha256(token.encode()).hexdigest()[:16]

    @staticmethod
    def _resolve_limit(path: str) -> tuple[int, int]:
        """Choose the limit based on the widget sub-path."""
        lower = path.lower()
        if "/messages" in lower or "/stream" in lower:
            return _WIDGET_SESSION_LIMITS["messages"]
        if "/voice" in lower:
            return _WIDGET_SESSION_LIMITS["voice"]
        return _WIDGET_SESSION_LIMITS["default"]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope["path"]
        if not path.startswith("/public/widget"):
            await self.app(scope, receive, send)
            return

        fingerprint = self._session_fingerprint(scope)
        if fingerprint is None:
            # No session token — let the endpoint handle auth rejection
            await self.app(scope, receive, send)
            return

        redis = await self._get_redis()
        if redis is None:
            await self.app(scope, receive, send)
            return

        limit, window = self._resolve_limit(path)
        key = f"rl:widget:session:{fingerprint}"

        try:
            allowed, remaining, retry_after = await _check_limit(
                redis, key, limit=limit, window_seconds=window
            )
        except Exception:
            # Redis error — fail open for widget (IP layer already fail-closed)
            await self.app(scope, receive, send)
            return

        if not allowed:
            rl_headers = {
                "X-RateLimit-Limit":     str(limit),
                "X-RateLimit-Remaining": "0",
                "Retry-After":           str(retry_after),
            }
            resp = JSONResponse(
                {"detail": "widget_session_rate_limit_exceeded", "retry_after": retry_after},
                status_code=429,
                headers=rl_headers,
            )
            await resp(scope, receive, send)
            return

        await self.app(scope, receive, send)
