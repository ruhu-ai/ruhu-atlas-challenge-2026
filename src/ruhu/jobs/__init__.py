"""Unified background-job runtime (RP-1.3 / RP-2.1).

One jobs table, one retry policy, one worker loop. Producers enqueue inside
their own transaction (transactional outbox: pass the open ``session`` to
``enqueue`` so the job commits atomically with the state change that caused
it). The worker process claims with ``FOR UPDATE SKIP LOCKED`` + heartbeat
leases, so any number of workers can run against the same database.

Every background concern in the codebase (retries, sweeps, retention,
dispatch, scheduling) is a registered handler on this runtime — do not add
new ``threading.Thread`` workers.
"""

from .models import Job, JobStatus
from .policy import RetryPolicy, next_retry_at
from .recurring import (
    RecurringSchedule,
    RecurringTickStatus,
    enqueue_due_ticks,
    recurring_tick_status,
    tick_dedupe_key,
    tick_slot,
)
from .runtime import JobHandlerRegistry, JobRuntime
from .store import InMemoryJobStore, JobStore, SQLAlchemyJobStore

__all__ = [
    "InMemoryJobStore",
    "Job",
    "JobHandlerRegistry",
    "JobRuntime",
    "JobStatus",
    "JobStore",
    "RecurringSchedule",
    "RecurringTickStatus",
    "RetryPolicy",
    "SQLAlchemyJobStore",
    "enqueue_due_ticks",
    "next_retry_at",
    "recurring_tick_status",
    "tick_dedupe_key",
    "tick_slot",
]
