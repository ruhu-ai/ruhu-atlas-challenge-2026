"""Background coroutine that drains the audit queue and writes to Postgres.

Runs as an asyncio.Task started in the app lifespan. Drains events in
batches and persists them via the AuditStore. Tolerates store errors
without crashing — logs and continues.

Start with: ``asyncio.create_task(run_audit_flusher(...))``
Stop with:  ``stop_event.set()`` then ``await task``
"""
from __future__ import annotations

import asyncio

import structlog

from .events import AuditEvent
from .store import AuditStore

log = structlog.get_logger(__name__)


async def run_audit_flusher(
    queue: asyncio.Queue,
    store: AuditStore,
    *,
    stop_event: asyncio.Event,
    batch_size: int = 100,
    flush_interval: float = 5.0,
) -> None:
    """Drain the audit queue in batches and persist to the store.

    Runs until ``stop_event`` is set. After the stop signal, drains any
    remaining entries before returning.
    """
    while not stop_event.is_set():
        batch: list[AuditEvent] = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + flush_interval

        while len(batch) < batch_size:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=remaining)
                batch.append(event)
                queue.task_done()
            except asyncio.TimeoutError:
                break
            except asyncio.CancelledError:
                return

        if batch:
            try:
                store.save_batch(batch)
            except Exception as exc:
                log.error("audit_flush_failed", count=len(batch), error=str(exc))

        if stop_event.is_set() and queue.empty():
            break

    # Final drain after stop
    final_batch: list[AuditEvent] = []
    while not queue.empty():
        try:
            final_batch.append(queue.get_nowait())
            queue.task_done()
        except asyncio.QueueEmpty:
            break
    if final_batch:
        try:
            store.save_batch(final_batch)
        except Exception as exc:
            log.error("audit_final_flush_failed", count=len(final_batch), error=str(exc))
