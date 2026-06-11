"""
Redis-backed conversation store with optimistic concurrency control.

Architecture
------------
- **Hot store (Redis):** canonical state for active conversations.
  Keyed by ``conv:{org_id}:{conversation_id}``, TTL 30 min (refreshed on write).
- **Archive (Postgres):** eventually-consistent backup via the outbox.
  On Redis miss, the store falls back to ``archive_store.load()``.
- **Optimistic locking:** every ``save()`` runs a Lua CAS script that compares
  the stored ``version`` field with the caller's expected version and atomically
  increments it on match.  Callers must reload and retry on ``OptimisticLockError``.

Outbox writes
-------------
``save()`` calls ``enqueue()`` after a successful CAS write.  This is
best-effort — a crash between the CAS and enqueue() leaves a Postgres snapshot
that lags the Redis state.  See ``outbox.py`` for the full durability model.
"""
from __future__ import annotations

import asyncio
from typing import Optional

import structlog

from ruhu.observability.metrics import conversation_version_conflicts_total
from ruhu.schemas import ConversationState

from .outbox import enqueue
from .protocols import AsyncConversationStore, OptimisticLockError

log = structlog.get_logger(__name__)

_STATE_TTL = 1800  # seconds; refreshed on every save

# CAS Lua script.
# Returns:
#   2  — key was absent (new conversation); write succeeded unconditionally
#   1  — version matched; write succeeded
#   0  — version mismatch (optimistic lock conflict); write rejected
_CAS_LUA = """
local existing = redis.call('GET', KEYS[1])
if existing == false then
    redis.call('SETEX', KEYS[1], ARGV[3], ARGV[2])
    return 2
end
local ok, data = pcall(cjson.decode, existing)
if not ok then
    -- Corrupt entry — overwrite to recover
    redis.call('SETEX', KEYS[1], ARGV[3], ARGV[2])
    return 1
end
local stored_ver = tonumber(data['version'] or 0)
local expected   = tonumber(ARGV[1])
if stored_ver ~= expected then
    return 0
end
redis.call('SETEX', KEYS[1], ARGV[3], ARGV[2])
return 1
"""


class RedisConversationStore:
    """Async conversation store backed by Redis with optimistic concurrency.

    Parameters
    ----------
    redis_url:
        ``redis://host:port/db`` connection string.
    archive_store:
        Optional ``AsyncConversationStore`` (Postgres).  Used for:
        - Cache-miss fallback in ``load()``.
        - Outbox target for ``run_flusher()``.
    """

    def __init__(
        self,
        redis_url: str,
        *,
        archive_store: Optional[AsyncConversationStore] = None,
    ) -> None:
        self._redis_url = redis_url
        self._archive = archive_store
        self._redis = None
        self._loop_id: Optional[int] = None

    async def _get_redis(self):
        """Return a lazy-initialised async Redis client.

        Detects event-loop replacement (common in test environments) and
        creates a fresh connection when the loop has changed.

        The ``redis`` package is only imported when a real connection must be
        created, so tests that pre-inject a mock via ``store._redis = mock``
        never trigger the optional dependency import.
        """
        current_loop_id = id(asyncio.get_running_loop())

        # Stale connection from a previous event loop — close it and recreate.
        if self._redis is not None and self._loop_id != current_loop_id:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None

        if self._redis is None:
            # Deferred import: redis is an optional dependency.  Tests inject a
            # mock directly into store._redis to avoid loading this package.
            import redis.asyncio as aioredis  # noqa: PLC0415

            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=2,
            )
            self._loop_id = current_loop_id

        return self._redis

    @staticmethod
    def _key(organization_id: Optional[str], conversation_id: str) -> str:
        return f"conv:{organization_id or 'global'}:{conversation_id}"

    # ── AsyncConversationStore protocol ───────────────────────────────────────

    async def load(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> ConversationState | None:
        """Load from Redis hot cache; fall back to archive on miss or error."""
        try:
            r = await self._get_redis()
            raw = await r.get(self._key(organization_id, conversation_id))
            if raw:
                return ConversationState.model_validate_json(raw)
        except Exception as exc:
            log.warning(
                "redis_load_failed",
                conversation_id=conversation_id,
                error=str(exc),
            )

        if self._archive is not None:
            return await self._archive.load(
                conversation_id, organization_id=organization_id
            )
        return None

    async def save(self, state: ConversationState) -> None:
        """Persist state with optimistic locking.

        Raises:
            OptimisticLockError: if the stored version has advanced past
                ``state.version`` since the caller loaded it.
        """
        expected_version = state.version
        new_state = state.model_copy(update={"version": expected_version + 1})
        payload = new_state.model_dump_json()
        key = self._key(new_state.organization_id, new_state.conversation_id)

        r = await self._get_redis()
        result = int(
            await r.eval(
                _CAS_LUA,
                1,                       # numkeys
                key,                     # KEYS[1]
                str(expected_version),   # ARGV[1]
                payload,                 # ARGV[2]
                str(_STATE_TTL),         # ARGV[3]
            )
        )

        if result == 0:
            conversation_version_conflicts_total.inc()
            raise OptimisticLockError(
                f"Version conflict on conversation {state.conversation_id}: "
                f"expected version {expected_version}."
            )

        # Best-effort enqueue into the Postgres archive outbox.
        # Failure is logged but does not cause save() to raise — Redis is the
        # source of truth; the archive is eventually consistent.
        try:
            await enqueue(r, payload, new_state.conversation_id)
        except Exception as exc:
            log.error(
                "outbox_enqueue_failed",
                conversation_id=new_state.conversation_id,
                error=str(exc),
            )

    async def list_conversations(
        self,
        *,
        organization_id: str | None = None,
    ) -> list[ConversationState]:
        """Delegate to archive — Redis keys are not enumerable efficiently."""
        if self._archive is not None:
            return await self._archive.list_conversations(
                organization_id=organization_id
            )
        return []
