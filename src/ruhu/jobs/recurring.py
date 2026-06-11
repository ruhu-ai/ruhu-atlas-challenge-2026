"""Recurring ticks on the jobs table (RP-2.2).

Periodic work (sweeps, retention, pollers) is modeled as one job per time
slot. Every worker's poll loop calls :func:`enqueue_due_ticks`; the slot is
quantized to the interval, so any number of workers computes the same
``dedupe_key`` and the partial unique index collapses concurrent inserts to
one job per slot. A worker that was down simply starts enqueueing at the
current slot — no chain to break, nothing to re-seed.

Tick jobs run with ``max_attempts=1``: a failed tick is logged and dead-
lettered for visibility, and the next slot's tick proceeds regardless.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .models import Job
from .store import JobStore

logger = logging.getLogger("ruhu.jobs")


@dataclass(frozen=True)
class RecurringSchedule:
    job_type: str
    interval_seconds: float
    payload: dict[str, Any] = field(default_factory=dict)
    organization_id: str | None = None


def tick_slot(now: datetime, interval_seconds: float) -> datetime:
    """The current slot boundary: ``now`` quantized down to the interval."""
    interval = max(1.0, float(interval_seconds))
    slot_epoch = int(now.timestamp() // interval) * interval
    return datetime.fromtimestamp(slot_epoch, tz=timezone.utc)


def tick_dedupe_key(job_type: str, slot: datetime) -> str:
    return f"{job_type}@{int(slot.timestamp())}"


@dataclass(frozen=True)
class RecurringTickStatus:
    """Diagnostics view of a recurring tick, derived from the jobs table.

    Replaces the in-process ``worker.status()`` calls: the API process no
    longer hosts workers, so liveness is "a tick job is queued or running"
    and history is the most recently finished tick.
    """

    scheduled: bool
    last_tick_at: datetime | None
    last_tick_status: str | None
    last_error: str | None

    def model_dump(self) -> dict[str, object]:
        return {
            "scheduled": self.scheduled,
            "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
            "last_tick_status": self.last_tick_status,
            "last_error": self.last_error,
        }


def recurring_tick_status(store: JobStore, job_type: str) -> RecurringTickStatus:
    recent = store.list_jobs(job_type=job_type, limit=50, newest_first=True)
    scheduled = any(job.status in ("queued", "running") for job in recent)
    finished = next((job for job in recent if job.finished_at is not None), None)
    return RecurringTickStatus(
        scheduled=scheduled,
        last_tick_at=finished.finished_at if finished else None,
        last_tick_status=finished.status if finished else None,
        last_error=finished.last_error if finished else None,
    )


def enqueue_due_ticks(
    store: JobStore,
    schedules: list[RecurringSchedule],
    *,
    now: datetime | None = None,
) -> int:
    """Ensure the current slot's tick exists for every schedule.

    Idempotent across workers and polls: an existing job for the slot — in
    any status, including already succeeded — means the slot is covered.
    Races between the existence check and insert are resolved by the partial
    unique index on active jobs.
    """
    effective_now = now or datetime.now(timezone.utc)
    enqueued = 0
    for schedule in schedules:
        slot = tick_slot(effective_now, schedule.interval_seconds)
        dedupe_key = tick_dedupe_key(schedule.job_type, slot)
        if store.has_job(schedule.job_type, dedupe_key):
            continue
        try:
            store.enqueue(
                Job(
                    job_type=schedule.job_type,
                    organization_id=schedule.organization_id,
                    payload=dict(schedule.payload),
                    run_at=slot,
                    dedupe_key=dedupe_key,
                    max_attempts=1,
                )
            )
            enqueued += 1
        except Exception:
            logger.exception("failed to enqueue recurring tick", extra={"job_type": schedule.job_type})
    return enqueued
