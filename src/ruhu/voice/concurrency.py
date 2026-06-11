"""
Per-organization voice session concurrency limiter.

Uses a Redis SADD/SCARD Lua script to atomically reserve a slot. Each voice
session holds one slot for its lifetime; a TTL on the set prevents leaked slots
from crashed workers from accumulating indefinitely.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_RESERVE_LUA = """
local key = KEYS[1]
local token = ARGV[1]
local max_active = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local count = redis.call('SCARD', key)
if count >= max_active and redis.call('SISMEMBER', key, token) == 0 then
    return 0
end
redis.call('SADD', key, token)
redis.call('EXPIRE', key, ttl)
return 1
"""


class VoiceCapacityExceededError(Exception):
    """Raised when an organization has reached its concurrent voice session limit."""


class VoiceConcurrencyLimiter:
    """Redis-backed per-organization voice session slot manager.

    Each voice session calls ``reserve()`` on entry and ``release()`` on exit
    (even if the session errors out). The Redis set key is scoped per org so
    that one org's traffic cannot crowd out another.

    The TTL argument (default 3600 s) acts as a dead-man's switch: if a worker
    crashes before calling ``release()``, the slot is reclaimed after TTL expires.

    Usage::

        limiter = VoiceConcurrencyLimiter(redis_url, max_per_org=10)
        token = str(uuid.uuid4())
        try:
            await limiter.reserve(org_id, token)
        except VoiceCapacityExceededError:
            await room.disconnect()
            return
        try:
            ...  # run session
        finally:
            await limiter.release(org_id, token)
    """

    def __init__(
        self,
        redis_url: str,
        *,
        max_per_org: int = 10,
        slot_ttl_seconds: int = 3600,
    ) -> None:
        self._redis_url = redis_url
        self._max = max_per_org
        self._ttl = slot_ttl_seconds
        self._redis = None

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    def _key(self, org_id: str) -> str:
        return f"voice_active:{org_id}"

    async def reserve(self, org_id: str, session_token: str) -> None:
        """Reserve a slot for the session.

        Raises ``VoiceCapacityExceededError`` if the org is at capacity.
        """
        r = await self._get_redis()
        allowed = int(await r.eval(
            _RESERVE_LUA, 1, self._key(org_id),
            session_token, str(self._max), str(self._ttl),
        ))
        if not allowed:
            raise VoiceCapacityExceededError(
                f"Organization {org_id} has reached the voice session limit ({self._max})."
            )

    async def release(self, org_id: str, session_token: str) -> None:
        """Release the slot held by the session.

        Errors are logged rather than raised so that a Redis outage cannot prevent
        the session from completing its cleanup.
        """
        try:
            r = await self._get_redis()
            await r.srem(self._key(org_id), session_token)
        except Exception as exc:
            log.error("voice_slot_release_failed", extra={"org_id": org_id, "error": str(exc)})

    async def active_count(self, org_id: str) -> int:
        """Return the number of currently-active sessions for an org."""
        r = await self._get_redis()
        return int(await r.scard(self._key(org_id)))
