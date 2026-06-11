from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from threading import Event, Lock, Thread
from typing import Any, Callable
from uuid import uuid4

from .deferred import DeferredToolExecutor, DeferredToolTransition
from .integration_runtime import ToolIntegrationRuntime
from .types import ToolCall, ToolIntegrationJob, ToolResult

logger = logging.getLogger(__name__)

TOOL_INTEGRATION_JOB_TYPE = "tool_integration.tick"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class ToolIntegrationWebhookProcessResult:
    job: ToolIntegrationJob
    result: ToolResult
    replayed: bool = False


@dataclass(slots=True)
class ToolIntegrationWorkerRuntime:
    tool_runtime: Any
    integration_runtime: ToolIntegrationRuntime
    on_job_transition: Callable[[ToolIntegrationJob], None] | None = None
    max_workers: int = 1
    embedded_worker_enabled: bool = True
    poll_interval_seconds: float = 1.0
    job_lease_seconds: float = 300.0
    job_heartbeat_interval_seconds: float = 30.0
    stuck_job_check_interval_seconds: float = 60.0
    stuck_job_limit: int = 25
    worker_identity: str | None = None
    _lock: Lock = field(init=False, repr=False)
    _stop_event: Event = field(init=False, repr=False)
    _worker_threads: list[Thread] = field(init=False, repr=False, default_factory=list)
    _started: bool = field(init=False, repr=False, default=False)
    _last_error: str | None = field(init=False, repr=False, default=None)
    _last_stuck_sweep_at: datetime | None = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        self._lock = Lock()
        self._stop_event = Event()
        self.worker_identity = self.worker_identity or f"ruhu-tool-integration-{uuid4().hex[:8]}"

    def startup(self) -> None:
        if self._started or not self.embedded_worker_enabled:
            return
        self._started = True
        self._stop_event.clear()
        self._worker_threads = []
        for worker_index in range(max(1, self.max_workers)):
            thread = Thread(
                target=self._run_worker_loop,
                args=(worker_index,),
                name=f"ruhu-tool-integration-worker-{worker_index + 1}",
                daemon=True,
            )
            thread.start()
            self._worker_threads.append(thread)

    def shutdown(self) -> None:
        self._started = False
        self._stop_event.set()
        for thread in self._worker_threads:
            thread.join(timeout=max(5.0, self.poll_interval_seconds + 1.0))
        self._worker_threads = []

    def process_available_jobs_once(
        self,
        *,
        max_jobs: int = 1,
        organization_id: str | None = None,
        worker_id: str | None = None,
    ) -> list[ToolIntegrationJob]:
        processed: list[ToolIntegrationJob] = []
        effective_worker_id = worker_id or self._compose_worker_id("manual")
        while len(processed) < max(1, max_jobs):
            with self._lock:
                claimed = self.integration_runtime.claim_next_job(
                    worker_id=effective_worker_id,
                    lease_expires_at=self._lease_expires_at(),
                    organization_id=organization_id,
                    now=_utcnow(),
                )
            if claimed is None:
                break
            updated = self._execute_claimed_job(claimed, worker_id=effective_worker_id)
            processed.append(updated)
            self._notify_job_transition(updated)
        return processed

    def sweep_stuck_jobs_once(
        self,
        *,
        organization_id: str | None = None,
        limit: int | None = None,
    ) -> list[ToolIntegrationJob]:
        swept = self.integration_runtime.sweep_stuck_jobs(
            organization_id=organization_id,
            limit=limit or self.stuck_job_limit,
        )
        for job in swept:
            self._notify_job_transition(job)
        return swept

    def process_webhook_callback(
        self,
        callback_correlation_id: str,
        *,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
        organization_id: str | None = None,
    ) -> ToolIntegrationWebhookProcessResult:
        with self._lock:
            job = self.integration_runtime.store.load_by_callback_correlation_id(
                callback_correlation_id,
                organization_id=organization_id,
            )
        if job is None:
            raise KeyError(callback_correlation_id)
        callback_fingerprint = self._callback_fingerprint(
            callback_correlation_id=callback_correlation_id,
            payload=payload,
        )
        if job.status in {"completed", "failed", "cancelled", "dead_lettered"}:
            stored_fingerprint = str(job.metadata.get("last_callback_fingerprint") or "")
            if stored_fingerprint and stored_fingerprint == callback_fingerprint:
                result = self.tool_runtime.load_result(job.invocation_id)
                if result is None:
                    result = ToolResult(
                        invocation_id=job.invocation_id,
                        tool_ref=job.tool_ref,
                        status="error",
                        error=job.error or "callback already processed",
                    )
                self.integration_runtime.observe_callback_outcome(job=job, outcome="replayed")
                return ToolIntegrationWebhookProcessResult(job=job, result=result, replayed=True)
            self.integration_runtime.observe_callback_outcome(job=job, outcome="duplicate_ignored")
            raise ValueError("integration job already handled this callback")
        if job.status != "waiting_webhook":
            self.integration_runtime.observe_callback_outcome(job=job, outcome="conflict")
            raise ValueError("integration job is not waiting for a webhook callback")

        spec, call = self._load_spec_and_call(job)
        executor = self._require_deferred_executor(spec.kind)
        transition = executor.handle_deferred_callback(
            spec,
            call,
            job,
            payload=payload,
            headers=headers,
            raw_body=raw_body,
        )
        applied = self._apply_transition(job, transition)
        self._record_callback_fingerprint(
            applied,
            callback_fingerprint=callback_fingerprint,
        )
        result = self.tool_runtime.load_result(job.invocation_id)
        if result is None:
            result = ToolResult(
                invocation_id=job.invocation_id,
                tool_ref=job.tool_ref,
                status="error",
                error=applied.error or "integration callback reached a terminal error state",
            )
        self.integration_runtime.observe_callback_outcome(job=applied, outcome="processed")
        return ToolIntegrationWebhookProcessResult(job=applied, result=result)

    def _execute_claimed_job(self, job: ToolIntegrationJob, *, worker_id: str) -> ToolIntegrationJob:
        heartbeat_stop = Event()
        heartbeat_thread = Thread(
            target=self._run_heartbeat_loop,
            args=(job.job_id, worker_id, heartbeat_stop),
            name=f"{worker_id}-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            spec, call = self._load_spec_and_call(job)
            executor = self._require_deferred_executor(spec.kind)
            claimed_from_status = str(job.metadata.get("claimed_from_status") or "queued")
            if claimed_from_status == "waiting_poll":
                transition = executor.poll_deferred(spec, call, job)
            else:
                transition = executor.submit_deferred(spec, call, job)
            return self._apply_transition(job, transition)
        except Exception as exc:
            self._last_error = str(exc)
            logger.exception(
                "tool integration worker job failed",
                extra={"job_id": job.job_id, "invocation_id": job.invocation_id, "tool_ref": job.tool_ref},
            )
            if job.attempt_count < job.max_attempts:
                return self.integration_runtime.schedule_retry(
                    job.job_id,
                    error=str(exc),
                    metadata={"retry_reason": "worker_exception"},
                )
            return self.integration_runtime.dead_letter_job(
                job.job_id,
                error=str(exc),
                metadata={"dead_letter_reason": "worker_exception"},
            )
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=max(1.0, self.job_heartbeat_interval_seconds + 1.0))

    def _apply_transition(
        self,
        job: ToolIntegrationJob,
        transition: DeferredToolTransition,
    ) -> ToolIntegrationJob:
        if transition.action == "complete":
            return self.integration_runtime.complete_job(job.job_id, transition.result)
        if transition.action == "wait_poll":
            return self.integration_runtime.mark_waiting_poll(
                job.job_id,
                next_poll_at=transition.next_poll_at,
                external_job_id=transition.external_job_id,
                metadata=transition.metadata,
            )
        if transition.action == "wait_webhook":
            return self.integration_runtime.mark_waiting_webhook(
                job.job_id,
                external_job_id=transition.external_job_id,
                callback_correlation_id=transition.callback_correlation_id,
                metadata=transition.metadata,
            )
        if transition.action == "retry":
            return self.integration_runtime.schedule_retry(
                job.job_id,
                next_retry_at=transition.next_retry_at,
                error=transition.error or (transition.result.error if transition.result else None),
                metadata=transition.metadata,
            )
        if transition.action == "fail":
            return self.integration_runtime.fail_job(
                job.job_id,
                error=transition.error or (transition.result.error if transition.result else "integration job failed"),
                metadata=transition.metadata,
            )
        raise RuntimeError(f"unsupported deferred transition action: {transition.action}")

    def _load_spec_and_call(self, job: ToolIntegrationJob) -> tuple[Any, ToolCall]:
        payload = dict(job.payload)
        spec_payload = dict(payload.get("tool_spec") or {})
        call_payload = dict(payload.get("tool_call") or {})
        if not spec_payload or not call_payload:
            raise ValueError("integration job payload missing tool_spec or tool_call")
        from .specs import ToolSpec

        return ToolSpec.model_validate(spec_payload), ToolCall.model_validate(call_payload)

    def _require_deferred_executor(self, kind: str) -> DeferredToolExecutor:
        executor = self.tool_runtime.get_executor(kind)
        if executor is None:
            raise RuntimeError(f"no executor registered for kind {kind}")
        if not isinstance(executor, DeferredToolExecutor):
            raise RuntimeError(f"executor kind {kind} does not support deferred execution")
        return executor

    def _run_worker_loop(self, worker_index: int) -> None:
        worker_id = self._compose_worker_id(f"worker-{worker_index + 1}")
        while not self._stop_event.is_set():
            try:
                processed = self.process_available_jobs_once(max_jobs=1, worker_id=worker_id)
            except Exception as exc:
                self._last_error = str(exc)
                logger.exception("tool integration worker loop failed", extra={"worker_id": worker_id})
                self._stop_event.wait(max(0.5, self.poll_interval_seconds))
                continue
            if processed:
                continue
            self._maybe_sweep_stuck_jobs()
            self._stop_event.wait(max(0.1, self.poll_interval_seconds))

    def _run_heartbeat_loop(self, job_id: str, worker_id: str, stop_event: Event) -> None:
        interval_seconds = max(1.0, min(self.job_heartbeat_interval_seconds, self.job_lease_seconds / 2))
        while not stop_event.wait(interval_seconds):
            with self._lock:
                heartbeat = self.integration_runtime.heartbeat_job(
                    job_id,
                    worker_id=worker_id,
                    lease_expires_at=self._lease_expires_at(),
                )
            if heartbeat is None:
                return

    def _lease_expires_at(self) -> datetime:
        return _utcnow() + timedelta(seconds=max(1.0, self.job_lease_seconds))

    def _compose_worker_id(self, suffix: str) -> str:
        return f"{self.worker_identity}:{suffix}"

    def _maybe_sweep_stuck_jobs(self) -> None:
        now = _utcnow()
        if (
            self._last_stuck_sweep_at is not None
            and (now - self._last_stuck_sweep_at).total_seconds() < max(1.0, self.stuck_job_check_interval_seconds)
        ):
            return
        self._last_stuck_sweep_at = now
        try:
            swept = self.integration_runtime.sweep_stuck_jobs(now=now, limit=self.stuck_job_limit)
        except Exception as exc:
            self._last_error = str(exc)
            logger.exception("tool integration stuck-job sweep failed")
            return
        for job in swept:
            self._notify_job_transition(job)

    @staticmethod
    def _callback_fingerprint(
        *,
        callback_correlation_id: str,
        payload: dict[str, Any],
    ) -> str:
        encoded = json.dumps(
            {"callback_correlation_id": callback_correlation_id, "payload": payload},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _record_callback_fingerprint(
        self,
        job: ToolIntegrationJob,
        *,
        callback_fingerprint: str,
    ) -> None:
        updated = job.model_copy(deep=True)
        updated.metadata["last_callback_fingerprint"] = callback_fingerprint
        updated.metadata["last_callback_received_at"] = _utcnow().isoformat()
        self.integration_runtime.store.save(updated)

    def _notify_job_transition(self, job: ToolIntegrationJob) -> None:
        if self.on_job_transition is None:
            return
        try:
            self.on_job_transition(job)
        except Exception as exc:
            self._last_error = str(exc)
            logger.exception(
                "tool integration worker transition callback failed",
                extra={"job_id": job.job_id, "invocation_id": job.invocation_id, "tool_ref": job.tool_ref},
            )
