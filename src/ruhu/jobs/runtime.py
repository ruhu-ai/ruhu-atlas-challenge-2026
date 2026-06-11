from __future__ import annotations

import logging
import threading
from typing import Callable

from .models import Job
from .policy import RetryPolicy
from .recurring import RecurringSchedule, enqueue_due_ticks
from .store import JobStore

logger = logging.getLogger("ruhu.jobs")

JobHandler = Callable[[Job], None]


class JobHandlerRegistry:
    """Maps ``job_type`` to its handler and retry policy."""

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}
        self._policies: dict[str, RetryPolicy] = {}

    def register(
        self,
        job_type: str,
        handler: JobHandler,
        *,
        policy: RetryPolicy | None = None,
    ) -> None:
        if job_type in self._handlers:
            raise ValueError(f"handler already registered for job type: {job_type}")
        self._handlers[job_type] = handler
        if policy is not None:
            self._policies[job_type] = policy

    def handler_for(self, job_type: str) -> JobHandler | None:
        return self._handlers.get(job_type)

    def policy_for(self, job_type: str) -> RetryPolicy:
        return self._policies.get(job_type, RetryPolicy())

    @property
    def job_types(self) -> list[str]:
        return sorted(self._handlers)


class JobRuntime:
    """Claim-execute loop over the jobs table.

    Runs in the dedicated worker process (``python -m ruhu.worker``), never in
    the API process. ``run_once`` drains up to ``batch_size`` due jobs and is
    the unit tests and cron-style callers use; ``run_forever`` polls until
    ``stop_event`` is set.
    """

    def __init__(
        self,
        store: JobStore,
        registry: JobHandlerRegistry,
        *,
        worker_id: str,
        lease_seconds: float = 60.0,
        poll_interval_seconds: float = 2.0,
        schedules: list["RecurringSchedule"] | None = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._schedules = list(schedules or [])

    def run_once(self, *, batch_size: int = 10) -> int:
        if self._schedules:
            enqueue_due_ticks(self._store, self._schedules)
        processed = 0
        for _ in range(batch_size):
            job = self._store.claim_next(
                worker_id=self._worker_id,
                lease_seconds=self._lease_seconds,
                job_types=self._registry.job_types,
            )
            if job is None:
                break
            self._execute(job)
            processed += 1
        return processed

    def run_forever(self, stop_event: threading.Event) -> None:
        logger.info(
            "job runtime started",
            extra={"worker_id": self._worker_id, "job_types": self._registry.job_types},
        )
        while not stop_event.is_set():
            try:
                processed = self.run_once()
            except Exception:
                logger.exception("job runtime poll failed")
                processed = 0
            if processed == 0:
                stop_event.wait(self._poll_interval_seconds)
        logger.info("job runtime stopped", extra={"worker_id": self._worker_id})

    def _execute(self, job: Job) -> None:
        handler = self._registry.handler_for(job.job_type)
        if handler is None:
            # Claimed a type we no longer handle (e.g. stale deploy) — fail it
            # back to the queue so a worker that does handle it can pick it up.
            self._store.fail(
                job.job_id,
                worker_id=self._worker_id,
                error=f"no handler registered for job type: {job.job_type}",
                policy=self._registry.policy_for(job.job_type),
            )
            return
        try:
            handler(job)
        except Exception as exc:
            logger.exception(
                "job handler failed",
                extra={"job_id": job.job_id, "job_type": job.job_type, "attempt": job.attempt_count},
            )
            retryable = getattr(exc, "retryable", True)
            self._store.fail(
                job.job_id,
                worker_id=self._worker_id,
                error=f"{type(exc).__name__}: {exc}",
                retryable=bool(retryable),
                policy=self._registry.policy_for(job.job_type),
            )
            return
        self._store.complete(job.job_id, worker_id=self._worker_id)
