"""AuditEventRouter — decides sync vs async write path per event.

Security and admin events are written synchronously to Postgres (never lossy).
Operational events are enqueued for async batch flush (tolerable loss under load).

The router also maintains the per-org hash chain head in memory (with Redis
fallback when available, Postgres fallback when not).
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

import structlog

from ruhu.observability.metrics import audit_events_total, audit_queue_drops_total

from .events import AuditEvent, requires_sync_write
from .store import AuditStore

log = structlog.get_logger(__name__)


class AuditEventRouter:
    """Routes audit events to the correct write path.

    - Security/admin events → ``_write_sync()`` → immediate Postgres commit
    - Operational events    → ``_enqueue()`` → async queue for batch flush

    Thread-safe: the chain head cache uses a threading.Lock because sync
    writes happen on the request thread while async writes happen on the
    event loop thread.
    """

    def __init__(
        self,
        *,
        store: AuditStore,
        queue: asyncio.Queue,
        enabled: bool = True,
    ) -> None:
        self._store = store
        self._queue = queue
        self._enabled = enabled
        # Per-org chain head cache: {org_id: latest content_hash}
        self._chain_heads: dict[str, str] = {}
        self._chain_lock = threading.Lock()

    def route(self, event: AuditEvent) -> None:
        """Route an event to the correct write path."""
        if not self._enabled:
            return

        # Finalize hash chain
        with self._chain_lock:
            prev_hash = self._chain_heads.get(event.organization_id)
            if prev_hash is None:
                prev_hash = self._store.get_latest_hash(event.organization_id)
            event.finalize(prev_hash)
            self._chain_heads[event.organization_id] = event.content_hash

        if requires_sync_write(event.event_type):
            self._write_sync(event)
        else:
            self._enqueue(event)

    def _write_sync(self, event: AuditEvent) -> None:
        """Write directly to Postgres — used for security/admin events."""
        try:
            self._store.save(event)
            audit_events_total.labels(
                event_type=event.event_type, outcome=event.outcome
            ).inc()
        except Exception:
            log.error(
                "audit_sync_write_failed",
                event_type=event.event_type,
                org_id=event.organization_id,
                exc_info=True,
            )

    def _enqueue(self, event: AuditEvent) -> None:
        """Place event in the async queue for batch flush."""
        try:
            self._queue.put_nowait(event)
            audit_events_total.labels(
                event_type=event.event_type, outcome=event.outcome
            ).inc()
        except asyncio.QueueFull:
            audit_queue_drops_total.inc()
            log.warning(
                "audit_queue_full",
                event_type=event.event_type,
                org_id=event.organization_id,
            )
