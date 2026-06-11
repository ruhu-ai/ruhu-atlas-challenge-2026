"""Per-tool rate limiting using the same sliding-window algorithm as ``rate_limit.py``.

Reuses the Redis sorted-set Lua script for consistency with the existing
IP-based and org-based rate limiters.  Keyed by ``(tool_ref, tenant_id)``
so each organisation gets its own quota per tool.

Provides an in-memory fallback when Redis is unavailable — the tool runtime
**fails open** (allows the call) on Redis errors, but the in-memory counter
still provides soft-limiting within the current process.

Usage::

    limiter = ToolRateLimiter(redis_url="redis://localhost")
    result = await limiter.check("my_tool", tenant_id="org-1", limit=10, window_seconds=60)
    if not result.allowed:
        # reject or delay
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# Same Lua script as rate_limit.py — sliding window on a sorted set.
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

redis.call('ZADD', key, now, tostring(now) .. '-' .. tostring(math.random(1000000)))
redis.call('EXPIRE', key, math.ceil(window) + 1)
return {1, count + 1, 0}
"""


@dataclass(slots=True, frozen=True)
class ToolRateLimitResult:
    allowed: bool
    current_count: int
    retry_after: int = 0


class ToolRateLimiter:
    """Per-tool, per-tenant sliding-window rate limiter.

    Falls open on Redis errors — tool execution continues but the in-memory
    fallback tracks counts within the current process.
    """

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url
        self._redis = None  # lazy init

        # In-memory fallback: {key: [(timestamp, ...)]}
        self._local_counts: dict[str, list[float]] = {}
        self._local_lock = asyncio.Lock()

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

    async def check(
        self,
        tool_ref: str,
        *,
        tenant_id: str | None = None,
        limit: int = 60,
        window_seconds: int = 60,
    ) -> ToolRateLimitResult:
        """Check whether a tool call should be allowed.

        Returns a ``ToolRateLimitResult`` — never raises on Redis errors.
        Falls back to in-memory counting when Redis is unavailable.
        """
        scope = tenant_id or "global"
        key = f"rl:tool:{tool_ref}:{scope}"

        redis = await self._get_redis()
        if redis is not None:
            try:
                return await self._check_redis(redis, key, limit=limit, window_seconds=window_seconds)
            except Exception as exc:
                # Fall back to in-memory limiter so requests aren't blocked by Redis outages,
                # but log loudly so operators see Redis health issues. Note: in multi-instance
                # deployments the local fallback only enforces per-process limits.
                logger.warning(
                    "tool_rate_limiter: Redis check failed for key=%s, falling back to in-memory: %s",
                    key,
                    exc,
                )

        return await self._check_local(key, limit=limit, window_seconds=window_seconds)

    async def _check_redis(
        self,
        redis,
        key: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> ToolRateLimitResult:
        now = time.time()
        result = await redis.eval(
            _SLIDING_WINDOW_LUA,
            1,
            key,
            str(now),
            str(window_seconds),
            str(limit),
        )
        allowed_raw, count_raw, retry_after_raw = result
        return ToolRateLimitResult(
            allowed=bool(int(allowed_raw)),
            current_count=int(count_raw),
            retry_after=int(retry_after_raw),
        )

    async def _check_local(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> ToolRateLimitResult:
        """In-memory sliding window fallback.

        Uses an asyncio.Lock to prevent race conditions between concurrent
        coroutines that could read-then-write the same key non-atomically.
        """
        now = time.time()
        cutoff = now - window_seconds
        async with self._local_lock:
            timestamps = self._local_counts.get(key, [])
            # Prune expired entries
            timestamps = [t for t in timestamps if t > cutoff]
            count = len(timestamps)
            if count >= limit:
                retry_after = int(timestamps[0] + window_seconds - now) + 1 if timestamps else 1
                self._local_counts[key] = timestamps
                return ToolRateLimitResult(
                    allowed=False,
                    current_count=count,
                    retry_after=max(1, retry_after),
                )
            timestamps.append(now)
            self._local_counts[key] = timestamps
            return ToolRateLimitResult(
                allowed=True,
                current_count=count + 1,
            )
