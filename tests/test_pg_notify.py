"""Tests for PgNotifyDispatcher — the Postgres LISTEN/NOTIFY adapter.

These are unit tests using mocks. Integration tests with a real Postgres
connection should be run manually or in CI with a live database.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from ruhu.realtime.pg_notify import PgNotifyDispatcher, NOTIFY_CHANNEL


class TestPgNotifyDispatcherInit:
    def test_default_channel_name(self):
        assert NOTIFY_CHANNEL == "ruhu_realtime_events"

    def test_not_running_before_start(self):
        d = PgNotifyDispatcher(direct_url="postgresql://localhost/test")
        assert not d.is_running

    def test_no_url_warns_and_stays_stopped(self):
        async def _inner():
            d = PgNotifyDispatcher(direct_url="")
            await d.start()
            return d.is_running

        assert anyio.run(_inner) is False

    def test_is_running_requires_live_connection(self):
        d = PgNotifyDispatcher(direct_url="postgresql://localhost/test")
        d._running = True
        d._listener_task = MagicMock()

        assert d.is_running is False

        d._set_connected(True)
        assert d.is_running is True

        d._set_connected(False)
        assert d.is_running is False


class TestPgNotifyDispatcherDispatch:
    def test_on_notification_dispatches_to_subscriber(self):
        async def _inner():
            d = PgNotifyDispatcher(direct_url="postgresql://localhost/test")
            d._running = True

            # Create a subscriber queue
            queue: asyncio.Queue[int] = asyncio.Queue(maxsize=64)
            d._subscribers["conv_123"].append(queue)

            # Simulate a NOTIFY
            d._on_notification("conv_123:42")

            # Check the queue received the sequence
            assert not queue.empty()
            seq = queue.get_nowait()
            assert seq == 42

        anyio.run(_inner)

    def test_on_notification_ignores_other_conversations(self):
        async def _inner():
            d = PgNotifyDispatcher(direct_url="postgresql://localhost/test")
            d._running = True

            queue: asyncio.Queue[int] = asyncio.Queue(maxsize=64)
            d._subscribers["conv_123"].append(queue)

            # NOTIFY for a different conversation
            d._on_notification("conv_999:10")

            assert queue.empty()

        anyio.run(_inner)

    def test_on_notification_handles_malformed_payload(self):
        async def _inner():
            d = PgNotifyDispatcher(direct_url="postgresql://localhost/test")
            d._running = True

            queue: asyncio.Queue[int] = asyncio.Queue(maxsize=64)
            d._subscribers["conv_123"].append(queue)

            # Malformed payloads should be silently ignored
            d._on_notification("no_colon")
            d._on_notification("conv_123:not_a_number")
            d._on_notification("")

            assert queue.empty()

        anyio.run(_inner)

    def test_on_notification_drops_when_queue_full(self):
        async def _inner():
            d = PgNotifyDispatcher(direct_url="postgresql://localhost/test")
            d._running = True

            # Queue with size 1
            queue: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
            d._subscribers["conv_123"].append(queue)

            # Fill the queue
            d._on_notification("conv_123:1")
            assert not queue.empty()

            # Second notification should be dropped (no error)
            d._on_notification("conv_123:2")

            # Queue still has exactly 1 item
            assert queue.qsize() == 1
            assert queue.get_nowait() == 1

        anyio.run(_inner)


class TestPgNotifyDispatcherSubscribe:
    def test_subscribe_unsubscribe_cleanup(self):
        async def _inner():
            d = PgNotifyDispatcher(direct_url="postgresql://localhost/test")
            d._running = True

            received = []

            async def _consume():
                async for seq in d.subscribe("conv_abc"):
                    received.append(seq)
                    break  # consume one then exit

            # Schedule the consumer — it will register and wait
            task = asyncio.create_task(_consume())
            await asyncio.sleep(0.05)  # let the generator enter the loop

            # Subscriber should be registered
            assert "conv_abc" in d._subscribers
            assert len(d._subscribers["conv_abc"]) == 1

            # Push a notification
            d._on_notification("conv_abc:5")

            # Wait for consumer to finish
            await asyncio.wait_for(task, timeout=2.0)
            assert received == [5]

            # After generator exit, subscriber should be cleaned up
            await asyncio.sleep(0.05)
            assert "conv_abc" not in d._subscribers or len(d._subscribers.get("conv_abc", [])) == 0

        anyio.run(_inner)

    def test_multiple_subscribers_same_conversation(self):
        async def _inner():
            d = PgNotifyDispatcher(direct_url="postgresql://localhost/test")
            d._running = True

            q1: asyncio.Queue[int] = asyncio.Queue(maxsize=64)
            q2: asyncio.Queue[int] = asyncio.Queue(maxsize=64)
            d._subscribers["conv_multi"].append(q1)
            d._subscribers["conv_multi"].append(q2)

            d._on_notification("conv_multi:10")

            assert q1.get_nowait() == 10
            assert q2.get_nowait() == 10

        anyio.run(_inner)


class TestPgNotifyStoreIntegration:
    def test_notify_channel_matches_between_store_and_dispatcher(self):
        from ruhu.realtime.store import _NOTIFY_CHANNEL
        assert _NOTIFY_CHANNEL == NOTIFY_CHANNEL


class TestPgNotifyDispatcherHealth:
    def test_listener_failure_marks_disconnected_and_counts_reconnect(self):
        async def _inner():
            d = PgNotifyDispatcher(direct_url="postgresql://localhost/test")
            d._running = True
            d._set_connected(True)

            async def _boom():
                d._running = False
                raise RuntimeError("boom")

            reconnects = MagicMock()
            with (
                patch.object(d, "_connect_and_listen", side_effect=_boom),
                patch("ruhu.realtime.pg_notify.asyncio.sleep", new=AsyncMock()),
                patch("ruhu.observability.metrics.pg_notify_reconnects_total", reconnects),
            ):
                await d._listen_loop()

            reconnects.inc.assert_called_once()
            assert d.is_running is False

        anyio.run(_inner)
