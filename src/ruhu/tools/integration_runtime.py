from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from ruhu.observability.metrics import (
    tool_integration_callbacks_total,
    tool_integration_job_duration_seconds,
    tool_integration_jobs_active,
    tool_integration_jobs_stuck,
    tool_integration_jobs_total,
    tool_integration_retries_total,
)

from .integration_store import InMemoryToolIntegrationJobStore, ToolIntegrationJobStore
from .specs import ToolSpec
from .store import ToolInvocationStore
from .types import ToolCall, ToolIntegrationJob, ToolInvocation, ToolResult


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ToolIntegrationRuntime:
    def __init__(
        self,
        *,
        job_store: ToolIntegrationJobStore | None = None,
        invocation_store: ToolInvocationStore,
    ) -> None:
        self._job_store = job_store or InMemoryToolIntegrationJobStore()
        self._invocation_store = invocation_store

    @property
    def store(self) -> ToolIntegrationJobStore:
        return self._job_store

    def submit(
        self,
        *,
        spec: ToolSpec,
        call: ToolCall,
        invocation: ToolInvocation,
    ) -> ToolIntegrationJob:
        now = _utcnow()
        resolution_mode = str(
            spec.executor_config.get("deferred_resolution_mode")
            or spec.executor_config.get("resolution_mode")
            or "manual"
        ).lower()
        if resolution_mode not in {"manual", "polling", "webhook"}:
            resolution_mode = "manual"
        queue_name = str(spec.executor_config.get("deferred_queue") or "default").strip() or "default"
        job = ToolIntegrationJob(
            job_id=str(uuid4()),
            organization_id=invocation.caller.tenant_id,
            invocation_id=invocation.invocation_id,
            tool_ref=invocation.tool_ref,
            executor_kind=invocation.executor_kind,
            resolution_mode=resolution_mode,
            status="queued",
            queue_name=queue_name,
            max_attempts=max(1, int(spec.executor_config.get("max_attempts", 1))),
            dedupe_key=invocation.dedupe_key,
            callback_correlation_id=str(spec.executor_config.get("callback_correlation_id") or invocation.invocation_id),
            payload={
                "tool_call": {
                    "invocation_id": call.invocation_id,
                    "tool_ref": call.tool_ref,
                    "args": dict(call.args),
                    "caller": call.caller.model_dump(mode="json"),
                    "dedupe_key": call.dedupe_key,
                    "metadata": dict(call.metadata),
                    "requested_at": call.requested_at.isoformat(),
                },
                "tool_spec": spec.model_dump(mode="json"),
            },
            metadata={
                "submission_mode": "deferred",
                "queue_name": queue_name,
                "provider": self._provider_for_spec(spec),
                "deferred_timeout_seconds": self._timeout_seconds_for_spec(spec),
                "callback_replay_mode": "terminal_replay",
            },
            submitted_at=now,
            last_progress_at=now,
        )
        created = self._job_store.create_or_get_for_invocation(job)
        invocation.status = "queued"
        invocation.updated_at = now
        invocation.metadata.update(
            {
                "deferred": True,
                "integration_job_id": created.job_id,
                "integration_status": created.status,
                "integration_resolution_mode": created.resolution_mode,
                "integration_queue_name": created.queue_name,
            }
        )
        self._invocation_store.save(invocation)
        tool_integration_jobs_total.labels(resolution_mode=created.resolution_mode, status="queued").inc()
        self.observe_operational_metrics(organization_id=created.organization_id)
        return created

    def load_job(self, job_id: str, *, organization_id: str | None = None) -> ToolIntegrationJob | None:
        return self._job_store.load(job_id, organization_id=organization_id)

    def load_job_for_invocation(
        self,
        invocation_id: str,
        *,
        organization_id: str | None = None,
    ) -> ToolIntegrationJob | None:
        return self._job_store.load_by_invocation(invocation_id, organization_id=organization_id)

    def claim_next_job(
        self,
        *,
        worker_id: str,
        lease_expires_at: datetime,
        organization_id: str | None = None,
        now: datetime | None = None,
    ) -> ToolIntegrationJob | None:
        return self._job_store.claim_next_job(
            worker_id=worker_id,
            lease_expires_at=lease_expires_at,
            organization_id=organization_id,
            now=now,
        )

    def heartbeat_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> ToolIntegrationJob | None:
        return self._job_store.heartbeat_job(
            job_id,
            worker_id=worker_id,
            lease_expires_at=lease_expires_at,
        )

    def mark_waiting_poll(
        self,
        job_id: str,
        *,
        next_poll_at: datetime,
        external_job_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolIntegrationJob:
        job = self._require_job(job_id)
        now = _utcnow()
        job.status = "waiting_poll"
        job.worker_id = None
        job.lease_expires_at = None
        job.next_poll_at = next_poll_at
        job.external_job_id = external_job_id or job.external_job_id
        job.last_progress_at = now
        if metadata:
            job.metadata.update(metadata)
        self._job_store.save(job)
        self._sync_invocation_status(job, status="waiting_poll")
        tool_integration_jobs_total.labels(resolution_mode=job.resolution_mode, status="waiting_poll").inc()
        self.observe_operational_metrics(organization_id=job.organization_id)
        return job

    def mark_waiting_webhook(
        self,
        job_id: str,
        *,
        external_job_id: str | None = None,
        callback_correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolIntegrationJob:
        job = self._require_job(job_id)
        now = _utcnow()
        job.status = "waiting_webhook"
        job.worker_id = None
        job.lease_expires_at = None
        job.external_job_id = external_job_id or job.external_job_id
        job.callback_correlation_id = callback_correlation_id or job.callback_correlation_id
        job.last_progress_at = now
        if metadata:
            job.metadata.update(metadata)
        self._job_store.save(job)
        self._sync_invocation_status(job, status="waiting_webhook")
        tool_integration_jobs_total.labels(resolution_mode=job.resolution_mode, status="waiting_webhook").inc()
        self.observe_operational_metrics(organization_id=job.organization_id)
        return job

    def schedule_retry(
        self,
        job_id: str,
        *,
        next_retry_at: datetime | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolIntegrationJob:
        job = self._require_job(job_id)
        now = _utcnow()
        effective_next_retry = next_retry_at or self.compute_next_retry_at(job, now=now)
        job.status = "retry_scheduled"
        job.worker_id = None
        job.lease_expires_at = None
        job.next_retry_at = effective_next_retry
        job.error = error
        job.last_progress_at = now
        if metadata:
            job.metadata.update(metadata)
        self._job_store.save(job)
        self._sync_invocation_status(job, status="retry_scheduled", error=error)
        tool_integration_jobs_total.labels(resolution_mode=job.resolution_mode, status="retry_scheduled").inc()
        tool_integration_retries_total.labels(
            provider=self._provider_for_job(job),
            outcome="scheduled",
        ).inc()
        self.observe_operational_metrics(organization_id=job.organization_id)
        return job

    def complete_job(self, job_id: str, result: ToolResult) -> ToolIntegrationJob:
        job = self._require_job(job_id)
        now = _utcnow()
        job.status = "completed"
        job.result = {
            "status": result.status,
            "output": dict(result.output),
            "error": result.error,
            "latency_ms": result.latency_ms,
            "metadata": dict(result.metadata),
        }
        job.error = result.error
        job.worker_id = None
        job.lease_expires_at = None
        job.finished_at = now
        job.last_progress_at = now
        self._job_store.save(job)

        invocation = self._require_invocation(job.invocation_id)
        invocation.status = "completed"
        invocation.output = dict(result.output)
        invocation.error = result.error
        invocation.latency_ms = result.latency_ms
        invocation.updated_at = now
        invocation.metadata.update(dict(result.metadata))
        invocation.metadata.update(
            {
                "integration_job_id": job.job_id,
                "integration_status": job.status,
                "deferred": True,
                "deferred_completed": True,
            }
        )
        self._invocation_store.save(invocation)
        self._observe_terminal(job, outcome="completed")
        self.observe_operational_metrics(organization_id=job.organization_id)
        return job

    def fail_job(
        self,
        job_id: str,
        *,
        error: str,
        retryable: bool = False,
        next_retry_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolIntegrationJob:
        job = self._require_job(job_id)
        if retryable and next_retry_at is not None and job.attempt_count < job.max_attempts:
            return self.schedule_retry(
                job_id,
                next_retry_at=next_retry_at,
                error=error,
                metadata=metadata,
            )

        now = _utcnow()
        terminal_status = "dead_lettered" if retryable and job.attempt_count >= job.max_attempts else "failed"
        job.status = terminal_status
        job.error = error
        job.worker_id = None
        job.lease_expires_at = None
        job.finished_at = now
        job.last_progress_at = now
        if metadata:
            job.metadata.update(metadata)
        self._job_store.save(job)

        invocation = self._require_invocation(job.invocation_id)
        invocation.status = "dead_lettered" if terminal_status == "dead_lettered" else "failed"
        invocation.error = error
        invocation.updated_at = now
        invocation.metadata.update(
            {
                "integration_job_id": job.job_id,
                "integration_status": job.status,
                "deferred": True,
            }
        )
        self._invocation_store.save(invocation)
        self._observe_terminal(job, outcome=terminal_status)
        if terminal_status == "dead_lettered":
            tool_integration_retries_total.labels(
                provider=self._provider_for_job(job),
                outcome="exhausted",
            ).inc()
        self.observe_operational_metrics(organization_id=job.organization_id)
        return job

    def dead_letter_job(
        self,
        job_id: str,
        *,
        error: str,
        metadata: dict[str, Any] | None = None,
    ) -> ToolIntegrationJob:
        job = self._require_job(job_id)
        now = _utcnow()
        job.status = "dead_lettered"
        job.error = error
        job.worker_id = None
        job.lease_expires_at = None
        job.finished_at = now
        job.last_progress_at = now
        if metadata:
            job.metadata.update(metadata)
        self._job_store.save(job)

        invocation = self._require_invocation(job.invocation_id)
        invocation.status = "dead_lettered"
        invocation.error = error
        invocation.updated_at = now
        invocation.metadata.update(
            {
                "integration_job_id": job.job_id,
                "integration_status": job.status,
                "deferred": True,
            }
        )
        self._invocation_store.save(invocation)
        self._observe_terminal(job, outcome="dead_lettered")
        tool_integration_retries_total.labels(
            provider=self._provider_for_job(job),
            outcome="dead_lettered",
        ).inc()
        self.observe_operational_metrics(organization_id=job.organization_id)
        return job

    def cancel_job(self, job_id: str, *, reason: str = "cancelled") -> ToolIntegrationJob | None:
        job = self._job_store.load(job_id)
        if job is None:
            return None
        if job.status not in {"queued", "waiting_poll", "waiting_webhook", "retry_scheduled"}:
            return None
        now = _utcnow()
        job.status = "cancelled"
        job.error = reason
        job.worker_id = None
        job.lease_expires_at = None
        job.finished_at = now
        job.last_progress_at = now
        self._job_store.save(job)

        invocation = self._require_invocation(job.invocation_id)
        invocation.status = "cancelled"
        invocation.error = reason
        invocation.updated_at = now
        invocation.metadata.update(
            {
                "integration_job_id": job.job_id,
                "integration_status": job.status,
                "deferred": True,
            }
        )
        self._invocation_store.save(invocation)
        self._observe_terminal(job, outcome="cancelled")
        self.observe_operational_metrics(organization_id=job.organization_id)
        return job

    def count_jobs_by_status(self, *, organization_id: str | None = None) -> dict[str, int]:
        return self._job_store.count_jobs_by_status(organization_id=organization_id)

    def list_jobs(
        self,
        *,
        organization_id: str | None = None,
        status: str | None = None,
        conversation_id: str | None = None,
        limit: int = 50,
        include_terminal: bool = True,
    ) -> list[ToolIntegrationJob]:
        return self._job_store.list_jobs(
            organization_id=organization_id,
            status=status,
            conversation_id=conversation_id,
            limit=limit,
            include_terminal=include_terminal,
        )

    def list_recent_jobs(
        self,
        *,
        organization_id: str | None = None,
        limit: int = 8,
    ) -> list[ToolIntegrationJob]:
        return self._job_store.list_recent_jobs(organization_id=organization_id, limit=limit)

    def list_stuck_jobs(
        self,
        *,
        organization_id: str | None = None,
        now: datetime | None = None,
        limit: int = 50,
    ) -> list[ToolIntegrationJob]:
        effective_now = now or _utcnow()
        candidates = self._job_store.list_stuck_jobs(
            stale_before=effective_now,
            organization_id=organization_id,
            limit=max(1, limit * 4),
        )
        stuck: list[ToolIntegrationJob] = []
        for job in candidates:
            if self.is_job_stuck(job, now=effective_now):
                stuck.append(job)
                if len(stuck) >= max(1, limit):
                    break
        return stuck

    def sweep_stuck_jobs(
        self,
        *,
        organization_id: str | None = None,
        now: datetime | None = None,
        limit: int = 25,
    ) -> list[ToolIntegrationJob]:
        effective_now = now or _utcnow()
        swept: list[ToolIntegrationJob] = []
        for job in self.list_stuck_jobs(
            organization_id=organization_id,
            now=effective_now,
            limit=limit,
        ):
            timeout_seconds = self.stuck_timeout_seconds_for_job(job)
            swept.append(
                self.dead_letter_job(
                    job.job_id,
                    error="integration job exceeded progress timeout",
                    metadata={
                        "stuck_detected_at": effective_now.isoformat(),
                        "stuck_timeout_seconds": timeout_seconds,
                        "dead_letter_reason": "stuck_timeout",
                    },
                )
            )
        if swept:
            self.observe_operational_metrics(organization_id=organization_id, now=effective_now)
        return swept

    def compute_next_retry_at(
        self,
        job: ToolIntegrationJob,
        *,
        now: datetime | None = None,
    ) -> datetime:
        effective_now = now or _utcnow()
        base_seconds = max(1, int(job.metadata.get("retry_backoff_base_seconds") or 5))
        max_seconds = max(base_seconds, int(job.metadata.get("retry_backoff_max_seconds") or 300))
        step = max(job.attempt_count, 1) - 1
        raw_delay = min(base_seconds * (2**step), max_seconds)
        # ±20% jitter prevents thundering-herd on provider recovery — a fleet of
        # jobs all backing off identically would retry at the same instant.
        # Can be disabled per-job via metadata["retry_jitter"] = False.
        if job.metadata.get("retry_jitter", True):
            jitter_factor = random.uniform(0.8, 1.2)
            delay_seconds = max(1.0, raw_delay * jitter_factor)
        else:
            delay_seconds = float(raw_delay)
        return effective_now + timedelta(seconds=delay_seconds)

    def is_job_stuck(
        self,
        job: ToolIntegrationJob,
        *,
        now: datetime | None = None,
    ) -> bool:
        if job.status in {"completed", "failed", "cancelled", "dead_lettered"}:
            return False
        effective_now = now or _utcnow()
        last_progress = job.last_progress_at or job.submitted_at
        return last_progress + timedelta(seconds=self.stuck_timeout_seconds_for_job(job)) <= effective_now

    def stuck_timeout_seconds_for_job(self, job: ToolIntegrationJob) -> int:
        return max(30, int(job.metadata.get("deferred_timeout_seconds") or 1800))

    def observe_callback_outcome(self, *, job: ToolIntegrationJob, outcome: str) -> None:
        tool_integration_callbacks_total.labels(
            provider=self._provider_for_job(job),
            outcome=outcome,
        ).inc()

    def observe_operational_metrics(
        self,
        *,
        organization_id: str | None = None,
        now: datetime | None = None,
    ) -> None:
        effective_now = now or _utcnow()
        jobs = self.list_jobs(
            organization_id=organization_id,
            limit=1000,
            include_terminal=True,
        )
        active_counts: dict[tuple[str, str], int] = {}
        stuck_counts: dict[tuple[str, str], int] = {}
        for job in jobs:
            provider = self._provider_for_job(job)
            active_counts[(provider, job.status)] = active_counts.get((provider, job.status), 0) + 1
            if self.is_job_stuck(job, now=effective_now):
                stuck_counts[(provider, job.status)] = stuck_counts.get((provider, job.status), 0) + 1
        if hasattr(tool_integration_jobs_active, "clear"):
            tool_integration_jobs_active.clear()
        if hasattr(tool_integration_jobs_stuck, "clear"):
            tool_integration_jobs_stuck.clear()
        for (provider, status), count in active_counts.items():
            tool_integration_jobs_active.labels(provider=provider, status=status).set(count)
        for (provider, status), count in stuck_counts.items():
            tool_integration_jobs_stuck.labels(provider=provider, status=status).set(count)

    def _require_job(self, job_id: str) -> ToolIntegrationJob:
        job = self._job_store.load(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def _require_invocation(self, invocation_id: str) -> ToolInvocation:
        invocation = self._invocation_store.load(invocation_id)
        if invocation is None:
            raise KeyError(invocation_id)
        return invocation

    def _sync_invocation_status(
        self,
        job: ToolIntegrationJob,
        *,
        status: str,
        error: str | None = None,
    ) -> None:
        invocation = self._require_invocation(job.invocation_id)
        invocation.status = status
        invocation.error = error
        invocation.updated_at = _utcnow()
        invocation.metadata.update(
            {
                "integration_job_id": job.job_id,
                "integration_status": job.status,
                "integration_resolution_mode": job.resolution_mode,
                "external_job_id": job.external_job_id,
                "callback_correlation_id": job.callback_correlation_id,
                "next_poll_at": None if job.next_poll_at is None else job.next_poll_at.isoformat(),
                "next_retry_at": None if job.next_retry_at is None else job.next_retry_at.isoformat(),
                "deferred": True,
            }
        )
        self._invocation_store.save(invocation)

    @staticmethod
    def _provider_for_spec(spec: ToolSpec) -> str:
        provider = spec.executor_config.get("provider")
        if isinstance(provider, str) and provider.strip():
            return provider.strip()
        return spec.kind

    @staticmethod
    def _timeout_seconds_for_spec(spec: ToolSpec) -> int:
        return max(30, int(spec.executor_config.get("deferred_timeout_seconds") or 1800))

    @staticmethod
    def _provider_for_job(job: ToolIntegrationJob) -> str:
        provider = job.metadata.get("provider")
        if isinstance(provider, str) and provider.strip():
            return provider.strip()
        payload = dict(job.payload.get("tool_spec") or {})
        executor_config = dict(payload.get("executor_config") or {})
        candidate = executor_config.get("provider")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        return job.executor_kind

    def _observe_terminal(self, job: ToolIntegrationJob, *, outcome: str) -> None:
        tool_integration_jobs_total.labels(resolution_mode=job.resolution_mode, status=outcome).inc()
        if job.finished_at is None:
            return
        started = job.started_at or job.submitted_at
        duration_seconds = max((job.finished_at - started).total_seconds(), 0.0)
        tool_integration_job_duration_seconds.labels(
            resolution_mode=job.resolution_mode,
            outcome=outcome,
        ).observe(duration_seconds)
