"""Tests for Phase 3: distributed conversation state.

Covers:
- ConversationState.version field: default 0, persists through serialisation
- protocols.py: OptimisticLockError, AsyncConversationStore/AsyncTraceStore are
  runtime-checkable protocols
- outbox.py: enqueue() pushes+trims correctly; run_flusher() drains entries
  and calls archive_store.save(); handles Redis errors; respects stop_event
- RedisConversationStore.load(): Redis hit, Redis miss falls through to archive,
  Redis error falls through to archive
- RedisConversationStore.save(): CAS result=2 (new), result=1 (updated),
  result=0 raises OptimisticLockError and increments Prometheus counter
- RedisConversationStore.save(): enqueue() is called after successful CAS
- RedisConversationStore.list_conversations(): delegates to archive

All tests mock the Redis client — no live Redis required.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from ruhu.schemas import ConversationState
from ruhu.conversations.protocols import (
    AsyncConversationStore,
    AsyncTraceStore,
    OptimisticLockError,
)
from ruhu.conversations.outbox import _shard_key, _wrap_entry, enqueue, run_flusher
from ruhu.conversations.redis_store import RedisConversationStore


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_state(
    conversation_id: str = "conv-001",
    organization_id: str | None = "org-123",
    version: int = 0,
) -> ConversationState:
    return ConversationState(
        conversation_id=conversation_id,
        organization_id=organization_id,
        agent_id="agent-1",
        agent_version_id="v1",
        step_id="s0",
        updated_at=datetime.now(timezone.utc),
        version=version,
    )


# ── ConversationState.version field ───────────────────────────────────────────

class TestConversationStateVersion:
    def test_default_version_is_zero(self):
        state = _make_state()
        assert state.version == 0

    def test_version_round_trips_json(self):
        state = _make_state(version=7)
        reloaded = ConversationState.model_validate_json(state.model_dump_json())
        assert reloaded.version == 7

    def test_existing_snapshots_without_version_deserialise_as_zero(self):
        """Snapshots created before the version field was added should load safely."""
        import json
        payload = {
            "conversation_id": "conv-legacy",
            "organization_id": None,
            "agent_id": "g",
            "agent_version_id": "v",
            "step_id": "s",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            # no 'version' key
        }
        state = ConversationState.model_validate(payload)
        assert state.version == 0


# ── protocols: runtime-checkable ─────────────────────────────────────────────

class TestProtocols:
    def test_optimistic_lock_error_is_exception(self):
        with pytest.raises(OptimisticLockError):
            raise OptimisticLockError("conflict")

    def test_async_conversation_store_is_runtime_checkable(self):
        class FakeStore:
            async def load(self, cid, *, organization_id=None): ...
            async def save(self, state): ...
            async def list_conversations(self, *, organization_id=None): ...

        assert isinstance(FakeStore(), AsyncConversationStore)

    def test_async_conversation_store_rejects_missing_method(self):
        class Incomplete:
            async def load(self, cid, *, organization_id=None): ...
            # missing save and list_conversations

        assert not isinstance(Incomplete(), AsyncConversationStore)

    def test_async_trace_store_is_runtime_checkable(self):
        class FakeTraceStore:
            async def append(self, trace): ...
            async def by_conversation(self, cid, *, organization_id=None): ...

        assert isinstance(FakeTraceStore(), AsyncTraceStore)


# ── outbox: _shard_key ────────────────────────────────────────────────────────

class TestOutboxShardKey:
    def test_same_id_always_maps_to_same_shard(self):
        k1 = _shard_key("conv-abc")
        k2 = _shard_key("conv-abc")
        assert k1 == k2

    def test_shard_key_uses_prefix(self):
        assert _shard_key("x").startswith("conv:outbox:")

    def test_shard_is_within_bounds(self):
        for cid in ["a", "bb", "ccc", "dddd", "eeeee"]:
            key = _shard_key(cid)
            shard = int(key.split(":")[-1])
            assert 0 <= shard < 4


# ── outbox: enqueue ───────────────────────────────────────────────────────────

class TestOutboxEnqueue:
    def test_enqueue_calls_lpush_and_ltrim(self):
        mock_pipe = AsyncMock()
        mock_pipe.lpush = MagicMock()
        mock_pipe.ltrim = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[1, 1])

        mock_redis = MagicMock()
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        async def _inner():
            await enqueue(mock_redis, '{"conversation_id":"c"}', "c")

        anyio.run(_inner)
        mock_pipe.lpush.assert_called_once()
        mock_pipe.ltrim.assert_called_once()
        mock_pipe.execute.assert_awaited_once()

    def test_enqueue_uses_correct_key(self):
        received_keys: list[str] = []

        mock_pipe = AsyncMock()
        mock_pipe.execute = AsyncMock(return_value=[1, 1])

        def capture_lpush(key, *args):
            received_keys.append(key)

        mock_pipe.lpush = capture_lpush
        mock_pipe.ltrim = MagicMock()
        mock_redis = MagicMock()
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        async def _inner():
            await enqueue(mock_redis, '{}', "conv-test-123")

        anyio.run(_inner)
        assert received_keys[0] == _shard_key("conv-test-123")


# ── outbox: run_flusher ───────────────────────────────────────────────────────

class TestRunFlusher:
    """Flusher tests drive stop_event from inside fake_brpop to avoid
    timing-sensitive asyncio.sleep() coordination that can starve the loop."""

    def test_flusher_calls_archive_save_on_valid_entry(self):
        state = _make_state()
        entry = _wrap_entry(state.model_dump_json(), state.conversation_id)

        archive = AsyncMock()
        archive.save = AsyncMock()
        stop = asyncio.Event()
        call_count = 0

        async def fake_brpop(keys, timeout):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (b"conv:outbox:0", entry)
            # Signal stop on second call so the flusher exits cleanly
            stop.set()
            return None

        mock_redis = AsyncMock()
        mock_redis.brpop = fake_brpop

        async def _inner():
            await run_flusher(mock_redis, archive, stop_event=stop)

        anyio.run(_inner)
        archive.save.assert_awaited()

    def test_flusher_stops_on_stop_event(self):
        """Pre-set stop_event means the flusher exits without calling brpop."""
        mock_redis = AsyncMock()
        mock_redis.brpop = AsyncMock(return_value=None)
        stop = asyncio.Event()
        stop.set()  # Already set before starting

        async def _inner():
            await run_flusher(mock_redis, AsyncMock(), stop_event=stop)

        anyio.run(_inner)  # Should return immediately

    def test_flusher_survives_archive_error(self):
        """Flush failure (archive.save raises) must not crash the flusher."""
        state = _make_state()
        entry = _wrap_entry(state.model_dump_json(), state.conversation_id)

        stop = asyncio.Event()
        call_count = 0

        async def fake_brpop(keys, timeout):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (b"conv:outbox:0", entry)
            stop.set()
            return None

        mock_redis = AsyncMock()
        mock_redis.brpop = fake_brpop

        archive = AsyncMock()
        archive.save = AsyncMock(side_effect=RuntimeError("DB down"))

        async def _inner():
            await run_flusher(mock_redis, archive, stop_event=stop)

        anyio.run(_inner)  # Must not raise
        archive.save.assert_awaited()
        mock_redis.lpush.assert_awaited()  # failed entry re-enqueued for retry


# ── RedisConversationStore.load ───────────────────────────────────────────────

def _inject_mock_redis(store: RedisConversationStore, mock_redis) -> None:
    """Pre-inject a mock Redis client.  _loop_id must be set inside an async context."""
    store._redis = mock_redis
    # _loop_id is intentionally left as None here; it is set inside each _inner()
    # coroutine where asyncio.get_running_loop() is valid.


class TestRedisConversationStoreLoad:
    def test_returns_state_on_redis_hit(self):
        state = _make_state()
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=state.model_dump_json())
        store = RedisConversationStore("redis://fake:6379")
        _inject_mock_redis(store, mock_redis)

        async def _inner():
            store._loop_id = id(asyncio.get_running_loop())
            return await store.load("conv-001", organization_id="org-123")

        result = anyio.run(_inner)
        assert result is not None
        assert result.conversation_id == "conv-001"

    def test_falls_back_to_archive_on_redis_miss(self):
        state = _make_state()
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        archive = AsyncMock()
        archive.load = AsyncMock(return_value=state)
        store = RedisConversationStore("redis://fake:6379", archive_store=archive)
        _inject_mock_redis(store, mock_redis)

        async def _inner():
            store._loop_id = id(asyncio.get_running_loop())
            return await store.load("conv-001", organization_id="org-123")

        result = anyio.run(_inner)
        archive.load.assert_awaited_once_with("conv-001", organization_id="org-123")
        assert result.conversation_id == "conv-001"

    def test_falls_back_to_archive_on_redis_error(self):
        state = _make_state()
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=ConnectionError("Redis down"))
        archive = AsyncMock()
        archive.load = AsyncMock(return_value=state)
        store = RedisConversationStore("redis://fake:6379", archive_store=archive)
        _inject_mock_redis(store, mock_redis)

        async def _inner():
            store._loop_id = id(asyncio.get_running_loop())
            return await store.load("conv-001", organization_id="org-123")

        result = anyio.run(_inner)
        assert result.conversation_id == "conv-001"

    def test_returns_none_without_archive_on_miss(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        store = RedisConversationStore("redis://fake:6379")
        _inject_mock_redis(store, mock_redis)

        async def _inner():
            store._loop_id = id(asyncio.get_running_loop())
            return await store.load("conv-missing")

        result = anyio.run(_inner)
        assert result is None


# ── RedisConversationStore.save ───────────────────────────────────────────────

def _make_store_with_mock(cas_result: int, archive=None):
    """Return (store, mock_redis) with a pre-wired mock that returns cas_result."""
    store = RedisConversationStore("redis://fake:6379", archive_store=archive)
    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value=cas_result)
    pipe = AsyncMock()
    pipe.lpush = MagicMock()
    pipe.ltrim = MagicMock()
    pipe.execute = AsyncMock(return_value=[1, 1])
    mock_redis.pipeline = MagicMock(return_value=pipe)
    _inject_mock_redis(store, mock_redis)
    return store, mock_redis


class TestRedisConversationStoreSave:
    def test_saves_new_conversation(self):
        """CAS result=2 means new key — save must succeed."""
        store, _ = _make_store_with_mock(cas_result=2)
        state = _make_state()

        async def _inner():
            store._loop_id = id(asyncio.get_running_loop())
            await store.save(state)

        anyio.run(_inner)  # Must not raise

    def test_saves_existing_conversation_version_match(self):
        """CAS result=1 means version matched — save must succeed."""
        store, _ = _make_store_with_mock(cas_result=1)
        state = _make_state(version=3)

        async def _inner():
            store._loop_id = id(asyncio.get_running_loop())
            await store.save(state)

        anyio.run(_inner)  # Must not raise

    def test_raises_optimistic_lock_error_on_conflict(self):
        """CAS result=0 means version mismatch — OptimisticLockError required."""
        store, _ = _make_store_with_mock(cas_result=0)
        state = _make_state(version=2)

        async def _inner():
            store._loop_id = id(asyncio.get_running_loop())
            with pytest.raises(OptimisticLockError):
                await store.save(state)

        anyio.run(_inner)

    def test_increments_prometheus_counter_on_conflict(self):
        """Version conflict must increment the Prometheus version-conflicts counter."""
        from ruhu.observability.metrics import conversation_version_conflicts_total

        store, _ = _make_store_with_mock(cas_result=0)
        state = _make_state()
        before = conversation_version_conflicts_total._value.get()

        async def _inner():
            store._loop_id = id(asyncio.get_running_loop())
            with pytest.raises(OptimisticLockError):
                await store.save(state)

        anyio.run(_inner)
        after = conversation_version_conflicts_total._value.get()
        assert after == before + 1

    def test_version_is_incremented_in_payload(self):
        """The CAS script must receive version+1 in the payload, not version."""
        store, mock_redis = _make_store_with_mock(cas_result=1)
        state = _make_state(version=5)
        received_payloads: list[str] = []

        async def capture_eval(script, numkeys, key, *argv):
            received_payloads.append(argv[1])  # ARGV[2] = new payload JSON
            return 1

        async def _inner():
            store._loop_id = id(asyncio.get_running_loop())
            mock_redis.eval = capture_eval
            await store.save(state)

        anyio.run(_inner)
        import json
        saved = json.loads(received_payloads[0])
        assert saved["version"] == 6  # version+1

    def test_enqueue_called_after_successful_save(self):
        """After CAS success, the store must enqueue a snapshot into the outbox."""
        store, mock_redis = _make_store_with_mock(cas_result=1)
        state = _make_state()
        pipe_calls: list[str] = []

        async def _inner():
            store._loop_id = id(asyncio.get_running_loop())
            pipe = AsyncMock()
            pipe.lpush = MagicMock(side_effect=lambda *a, **k: pipe_calls.append("lpush"))
            pipe.ltrim = MagicMock()
            pipe.execute = AsyncMock(return_value=[1, 1])
            mock_redis.pipeline = MagicMock(return_value=pipe)
            await store.save(state)

        anyio.run(_inner)
        assert "lpush" in pipe_calls, "enqueue() must call lpush to add entry to outbox"


# ── RedisConversationStore.list_conversations ─────────────────────────────────

class TestRedisConversationStoreList:
    def test_delegates_to_archive(self):
        state = _make_state()
        archive = AsyncMock()
        archive.list_conversations = AsyncMock(return_value=[state])
        store = RedisConversationStore("redis://fake:6379", archive_store=archive)
        _inject_mock_redis(store, AsyncMock())

        async def _inner():
            store._loop_id = id(asyncio.get_running_loop())
            return await store.list_conversations(organization_id="org-123")

        result = anyio.run(_inner)
        archive.list_conversations.assert_awaited_once_with(organization_id="org-123")
        assert result == [state]

    def test_returns_empty_without_archive(self):
        store = RedisConversationStore("redis://fake:6379")
        _inject_mock_redis(store, AsyncMock())

        async def _inner():
            store._loop_id = id(asyncio.get_running_loop())
            return await store.list_conversations()

        result = anyio.run(_inner)
        assert result == []
