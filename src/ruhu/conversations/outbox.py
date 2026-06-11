"""
Outbox: Redis list → Postgres upsert pipeline.

Write path (hot — called inside save()):
  1. Caller serialises ConversationState to JSON.
  2. LPUSH into a sharded Redis list: conv:outbox:{shard}
     where shard = abs(hash(conversation_id)) % NUM_SHARDS
  3. LTRIM caps the list at MAX_OUTBOX_LEN entries per shard.
     Entries beyond the cap are silently dropped.  Under sustained overload
     this is intentional: the Redis hot state is current; the archive falling
     behind is preferable to OOMing the Redis instance.

Flush path (background coroutine, started in app lifespan):
  1. BRPOP with a short timeout across all shard lists.
  2. Deserialise and upsert into the Postgres conversations table via the
     archive_store (an AsyncConversationStore backed by Postgres).
  3. On flush failure: log the error and continue.  Re-enqueueing is not
     automatic in this implementation — add a retry counter in the payload
     wrapper for a production hardening pass.

Durability properties
---------------------
Redis is the primary store of truth for active conversation state.
Postgres is a bounded, eventually-consistent archive.

  - The outbox guarantees that every Redis CAS write is followed by a
    best-effort enqueue into the archive pipeline.
  - It does NOT guarantee Postgres is always up-to-date.  A crash between the
    Redis write and enqueue(), a Redis restart without AOF enabled, or LTRIM
    eviction under extreme load can all produce a stale or missing Postgres
    snapshot.
  - Configure Redis with ``appendfsync everysec`` (minimum) or
    ``appendfsync always`` (maximum) for AOF persistence so that the outbox
    entries survive a Redis restart.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

log = logging.getLogger(__name__)

_OUTBOX_KEY_PREFIX = "conv:outbox"
_NUM_SHARDS = 4
_MAX_OUTBOX_LEN = 10_000    # per-shard cap; LTRIM drops entries beyond this
_BATCH_TIMEOUT = 1.0        # seconds to block-wait for new entries (BRPOP timeout)
_MAX_RETRY_COUNT = 5         # entries exceeding this go to dead-letter
_DEAD_LETTER_KEY = "conv:outbox:dead_letter"
_DEAD_LETTER_MAX_LEN = 1_000


def _shard_key(conversation_id: str) -> str:
    shard = abs(hash(conversation_id)) % _NUM_SHARDS
    return f"{_OUTBOX_KEY_PREFIX}:{shard}"


def _wrap_entry(state_json: str, conversation_id: str) -> str:
    """Wrap a state snapshot with retry metadata."""
    return json.dumps({
        "conversation_id": conversation_id,
        "state_json": state_json,
        "retry_count": 0,
        "enqueued_at": time.time(),
    })


def _unwrap_entry(raw: bytes | str) -> tuple[str, str, int, float]:
    """Unwrap an outbox entry. Returns (state_json, conversation_id, retry_count, enqueued_at)."""
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    try:
        envelope = json.loads(text)
        if isinstance(envelope, dict) and "state_json" in envelope:
            return (
                envelope["state_json"],
                envelope.get("conversation_id", ""),
                int(envelope.get("retry_count", 0)),
                float(envelope.get("enqueued_at", 0)),
            )
    except (json.JSONDecodeError, TypeError, ValueError):
        raise ValueError("invalid outbox entry envelope") from None
    raise ValueError("invalid outbox entry envelope")


async def enqueue(redis, state_json: str, conversation_id: str) -> None:
    """Push a serialised state snapshot into the outbox list.

    Uses a pipeline so LPUSH + LTRIM are sent in a single round-trip.
    """
    key = _shard_key(conversation_id)
    entry = _wrap_entry(state_json, conversation_id)
    pipe = redis.pipeline()
    pipe.lpush(key, entry)
    pipe.ltrim(key, 0, _MAX_OUTBOX_LEN - 1)
    await pipe.execute()


async def run_flusher(
    redis,
    archive_store,          # AsyncConversationStore backed by Postgres
    *,
    stop_event: asyncio.Event,
) -> None:
    """Background coroutine that drains the outbox and upserts into Postgres.

    Start with ``asyncio.create_task(run_flusher(...))`` inside the app lifespan.
    Shuts down cleanly when ``stop_event`` is set.

    Retry behavior:
      - On flush failure, the entry is re-enqueued with an incremented retry_count.
      - Entries exceeding _MAX_RETRY_COUNT are moved to a dead-letter list.

    Args:
        redis: An async redis client (redis.asyncio).
        archive_store: An AsyncConversationStore.  ``save()`` must be idempotent
            (upsert semantics) because the same snapshot may be delivered more
            than once under retry scenarios.
        stop_event: Signal from the lifespan to stop processing.
    """
    shard_keys = [f"{_OUTBOX_KEY_PREFIX}:{i}" for i in range(_NUM_SHARDS)]

    while not stop_event.is_set():
        try:
            result = await redis.brpop(shard_keys, timeout=_BATCH_TIMEOUT)
            if result is None:
                continue
            _key, raw = result
            try:
                state_json, conversation_id, retry_count, enqueued_at = _unwrap_entry(raw)
            except ValueError as exc:
                log.error("invalid outbox entry moved to dead letter: %s", str(exc))
                await _dead_letter(redis, raw)
                continue
            try:
                from ruhu.schemas import ConversationState
                state = ConversationState.model_validate_json(state_json)
                await archive_store.save(state)
                _record_flush_success(enqueued_at)
            except Exception as exc:
                retry_count += 1
                if retry_count > _MAX_RETRY_COUNT:
                    # Dead-letter: entry exceeded max retries
                    log.error(
                        "outbox entry moved to dead letter after %d retries",
                        retry_count,
                        extra={
                            "conversation_id": conversation_id,
                            "retry_count": retry_count,
                        },
                    )
                    await _dead_letter(redis, raw)
                else:
                    # Re-enqueue with incremented retry count
                    retry_entry = json.dumps({
                        "conversation_id": conversation_id,
                        "state_json": state_json,
                        "retry_count": retry_count,
                        "enqueued_at": enqueued_at or time.time(),
                    })
                    retry_key = _shard_key(conversation_id)
                    await redis.lpush(retry_key, retry_entry)
                    log.warning(
                        "outbox flush failed, re-enqueued (retry %d/%d)",
                        retry_count, _MAX_RETRY_COUNT,
                        extra={
                            "conversation_id": conversation_id,
                            "error": str(exc),
                        },
                    )
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error("outbox_flusher_error: %s", str(exc))
            await asyncio.sleep(1)


async def _dead_letter(redis, raw: bytes | str) -> None:
    """Move a failed entry to the dead-letter list."""
    try:
        pipe = redis.pipeline()
        pipe.lpush(_DEAD_LETTER_KEY, raw)
        pipe.ltrim(_DEAD_LETTER_KEY, 0, _DEAD_LETTER_MAX_LEN - 1)
        await pipe.execute()
        _record_dead_letter()
    except Exception:
        log.exception("failed to write to dead-letter list")


def _record_flush_success(enqueued_at: float) -> None:
    """Record flush lag metric."""
    try:
        from ruhu.observability.metrics import registry
        # Lazy-create metrics to avoid import-time registry issues
        if not hasattr(_record_flush_success, "_lag"):
            from prometheus_client import Histogram, Counter
            _record_flush_success._lag = Histogram(
                "ruhu_outbox_flush_lag_seconds",
                "Time between outbox enqueue and successful flush",
                [],
                buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60],
                registry=registry,
            )
        if enqueued_at > 0:
            lag = time.time() - enqueued_at
            _record_flush_success._lag.observe(lag)
    except Exception:
        pass


def _record_dead_letter() -> None:
    """Increment dead-letter counter."""
    try:
        from ruhu.observability.metrics import registry
        if not hasattr(_record_dead_letter, "_counter"):
            from prometheus_client import Counter
            _record_dead_letter._counter = Counter(
                "ruhu_outbox_dead_letter_total",
                "Outbox entries moved to dead-letter after max retries",
                [],
                registry=registry,
            )
        _record_dead_letter._counter.inc()
    except Exception:
        pass
