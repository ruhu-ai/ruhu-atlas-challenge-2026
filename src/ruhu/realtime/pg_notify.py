"""
Postgres LISTEN/NOTIFY adapter for realtime event push.

Replaces the 250ms polling loop with push-based notification.  A single
persistent Postgres connection listens on the ``ruhu_realtime_events``
channel and dispatches notifications to in-process subscriber coroutines.

Deployment constraint:
  The listener connection MUST be a direct connection to Postgres, not
  routed through PgBouncer or any transaction-mode connection pooler.
  LISTEN requires a session-level persistent connection.  Configure
  ``RUHU_PG_DIRECT_URL`` to bypass the pooler for listener use only.

Architecture:
  One listener connection → one in-process dispatcher → N subscriber
  coroutines (one per SSE client).

Usage::

    dispatcher = PgNotifyDispatcher(direct_url)
    await dispatcher.start()

    # In SSE endpoint:
    async for conversation_id, sequence in dispatcher.subscribe("conv_123"):
        events = event_store.replay(conversation_id=conversation_id, after_sequence=last_seq)
        ...

    await dispatcher.stop()
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from typing import AsyncIterator

logger = logging.getLogger(__name__)

NOTIFY_CHANNEL = "ruhu_realtime_events"


class PgNotifyDispatcher:
    """Listens on a Postgres NOTIFY channel and fans out to subscribers."""

    def __init__(self, direct_url: str | None = None) -> None:
        self._direct_url = direct_url or os.getenv("RUHU_PG_DIRECT_URL", "")
        self._connection = None
        self._listener_task: asyncio.Task | None = None
        self._subscribers: dict[str, list[asyncio.Queue[int]]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._running = False
        self._connected = False

    def _set_connected(self, value: bool) -> None:
        self._connected = value
        try:
            from ruhu.observability.metrics import pg_notify_connected

            pg_notify_connected.set(1 if value else 0)
        except Exception:
            pass

    async def start(self) -> None:
        """Start the background listener.  Call once at app startup."""
        if self._running:
            return
        if not self._direct_url:
            logger.warning(
                "PgNotifyDispatcher: no RUHU_PG_DIRECT_URL configured, "
                "LISTEN/NOTIFY will not be available"
            )
            return
        self._running = True
        self._set_connected(False)
        self._listener_task = asyncio.create_task(self._listen_loop())
        logger.info("PgNotifyDispatcher started on channel %s", NOTIFY_CHANNEL)

    async def stop(self) -> None:
        """Stop the listener and close the connection."""
        self._running = False
        self._set_connected(False)
        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except (asyncio.CancelledError, Exception):
                pass
            self._listener_task = None
        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception:
                pass
            self._connection = None
        logger.info("PgNotifyDispatcher stopped")

    @property
    def is_running(self) -> bool:
        return self._running and self._listener_task is not None and self._connected

    async def subscribe(self, conversation_id: str) -> AsyncIterator[int]:
        """Yield sequence numbers as they arrive for the given conversation.

        The caller should replay events from the store after each yield.
        Unsubscribes automatically when the async generator is closed.
        """
        queue: asyncio.Queue[int] = asyncio.Queue(maxsize=64)
        async with self._lock:
            self._subscribers[conversation_id].append(queue)
        try:
            while self._running:
                try:
                    sequence = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield sequence
                except asyncio.TimeoutError:
                    # Periodic wake-up for liveness — caller can check connection
                    continue
                except asyncio.CancelledError:
                    break
        finally:
            async with self._lock:
                subs = self._subscribers.get(conversation_id, [])
                if queue in subs:
                    subs.remove(queue)
                if not subs:
                    self._subscribers.pop(conversation_id, None)

    async def _listen_loop(self) -> None:
        """Persistent listener loop.  Reconnects on failure."""
        while self._running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                break
            except Exception:
                self._set_connected(False)
                try:
                    from ruhu.observability.metrics import pg_notify_reconnects_total

                    pg_notify_reconnects_total.inc()
                except Exception:
                    pass
                logger.exception("PgNotifyDispatcher listener error, reconnecting in 2s")
                await asyncio.sleep(2)

    async def _connect_and_listen(self) -> None:
        """Establish a direct connection and listen for notifications."""
        import psycopg

        url = self._direct_url
        # psycopg (libpq) expects a plain postgresql:// URL — keep accepting
        # it directly (H7: RUHU_PG_DIRECT_URL uses the plain scheme) and
        # strip SQLAlchemy driver qualifiers if a driver-qualified URL was
        # passed by mistake.
        if "+asyncpg" in url:
            url = url.replace("+asyncpg", "")
        if "+psycopg" in url:
            url = url.replace("+psycopg", "")

        # autocommit: LISTEN must take effect immediately, outside any
        # transaction, and notifications are only delivered between
        # transactions on a non-autocommit connection.
        self._connection = await psycopg.AsyncConnection.connect(url, autocommit=True)
        try:
            await self._connection.execute(f'LISTEN "{NOTIFY_CHANNEL}"')
            self._set_connected(True)
            logger.info("PgNotifyDispatcher listening on %s", NOTIFY_CHANNEL)
            # Blocks until the connection drops or the listener task is
            # cancelled (stop()); cancellation propagates out of the
            # generator and through _listen_loop.
            async for notification in self._connection.notifies():
                if not self._running:
                    break
                self._on_notification(notification.payload)
        finally:
            self._set_connected(False)
            try:
                await self._connection.close()
            except Exception:
                pass
            self._connection = None

    def _on_notification(self, payload: str) -> None:
        """Dispatch one NOTIFY payload to in-process subscribers."""
        # Payload format: "conversation_id:sequence"
        parts = payload.rsplit(":", 1)
        if len(parts) != 2:
            return
        conversation_id, seq_str = parts
        try:
            sequence = int(seq_str)
        except (ValueError, TypeError):
            return

        subscribers = self._subscribers.get(conversation_id, [])
        for queue in subscribers:
            try:
                queue.put_nowait(sequence)
            except asyncio.QueueFull:
                # Subscriber is slow — drop the notification.  The subscriber
                # will catch up on the next replay.
                pass
