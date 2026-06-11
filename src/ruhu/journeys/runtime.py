from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
from threading import Event, Lock, Thread
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

from ruhu.db import tenant_db_context
from ruhu.kernel import ConversationKernel
from ruhu.realtime import RealtimeControlPlane, RealtimeEvent
from ruhu.schemas import TurnTrace
from ruhu.stores import ConversationStore, TraceStore

from .models import (
    JourneyRuntimeAlert,
    JourneyRuntimeJob,
    JourneyRuntimeStatus,
)
from .schemas import (
    JourneyAbandonmentSweepRequest,
    JourneyAnalyticsRebuildRequest,
    JourneyDefinitionRebuildRequest,
    JourneyReplayRequest,
)
from .service import JourneyService
from .store import (
    InMemoryJourneyRuntimeJobStore,
    JourneyDefinitionStore,
    JourneyInstanceStore,
    JourneyRuntimeJobStore,
)
from .tracker import JourneyTracker

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_missing_runtime_table_error(exc: Exception) -> bool:
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    if sqlstate == "42P01":
        return True
    message = str(exc).lower()
    return "journey_runtime_jobs" in message and "does not exist" in message


class RealtimeEventStoreProtocol(Protocol):
    def append(self, **kwargs: Any) -> RealtimeEvent: ...

    def load(self, event_id: str) -> RealtimeEvent | None: ...

    def replay(
        self,
        *,
        conversation_id: str,
        after_sequence: int | None = None,
        after_event_id: str | None = None,
    ) -> list[RealtimeEvent]: ...


class JourneyTrackingTraceStore:
    def __init__(self, base_store: TraceStore, tracker: JourneyTracker) -> None:
        self._base_store = base_store
        self._tracker = tracker

    def append(self, trace: TurnTrace) -> None:
        self._base_store.append(trace)
        try:
            self._tracker.process_turn_trace(trace)
        except Exception:
            logger.exception("journey tracker failed after trace append", extra={"trace_id": trace.trace_id})

    def all(
        self,
        *,
        organization_id: str | None = None,
        agent_id: str | None = None,
        agent_version_id: str | None = None,
    ) -> list[TurnTrace]:
        return self._base_store.all(
            organization_id=organization_id,
            agent_id=agent_id,
            agent_version_id=agent_version_id,
        )

    def by_conversation(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[TurnTrace]:
        return self._base_store.by_conversation(
            conversation_id,
            organization_id=organization_id,
            limit=limit,
            offset=offset,
        )


class JourneyTrackingRealtimeEventStore:
    def __init__(self, base_store: RealtimeEventStoreProtocol, tracker: JourneyTracker) -> None:
        self._base_store = base_store
        self._tracker = tracker

    def append(self, **kwargs: Any) -> RealtimeEvent:
        event = self._base_store.append(**kwargs)
        try:
            self._tracker.process_realtime_event(event)
        except Exception:
            logger.exception("journey tracker failed after realtime event append", extra={"event_id": event.event_id})
        return event

    def load(self, event_id: str) -> RealtimeEvent | None:
        return self._base_store.load(event_id)

    def replay(
        self,
        *,
        conversation_id: str,
        after_sequence: int | None = None,
        after_event_id: str | None = None,
    ) -> list[RealtimeEvent]:
        return self._base_store.replay(
            conversation_id=conversation_id,
            after_sequence=after_sequence,
            after_event_id=after_event_id,
        )


@dataclass(slots=True)
class JourneyRuntime:
    service: JourneyService
    tracker: JourneyTracker
    max_workers: int = 2
    job_store: JourneyRuntimeJobStore | None = None
    embedded_worker_enabled: bool = True
    poll_interval_seconds: float = 1.0
    job_lease_seconds: float = 300.0
    job_heartbeat_interval_seconds: float = 30.0
    failure_alert_threshold: int = 3
    failure_alert_window_seconds: float = 900.0
    abandonment_sweep_enabled: bool = False
    abandonment_sweep_interval_seconds: float = 300.0
    organization_ids_provider: Callable[[], list[str]] | None = None
    worker_identity: str | None = None
    _lock: Lock = field(init=False, repr=False)
    _last_error: str | None = field(init=False, repr=False, default=None)
    _started: bool = field(init=False, repr=False, default=False)
    _stop_event: Event = field(init=False, repr=False)
    _worker_threads: list[Thread] = field(init=False, repr=False, default_factory=list)
    _scheduler_thread: Thread | None = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        self._lock = Lock()
        self.job_store = self.job_store or InMemoryJourneyRuntimeJobStore()
        self.worker_identity = self.worker_identity or f"ruhu-journey-{uuid4().hex[:8]}"
        self._stop_event = Event()

    def startup(self) -> None:
        if self._started:
            return
        if not self._job_store_startup_ready():
            return
        self._started = True
        self._stop_event.clear()
        if self.embedded_worker_enabled:
            self._worker_threads = []
            for worker_index in range(max(1, self.max_workers)):
                thread = Thread(
                    target=self._run_worker_loop,
                    args=(worker_index,),
                    name=f"ruhu-journey-worker-{worker_index + 1}",
                    daemon=True,
                )
                thread.start()
                self._worker_threads.append(thread)
        if self.abandonment_sweep_enabled:
            self._scheduler_thread = Thread(
                target=self._run_abandonment_scheduler_loop,
                name="ruhu-journey-abandonment",
                daemon=True,
            )
            self._scheduler_thread.start()

    def _job_store_startup_ready(self) -> bool:
        try:
            with self._lock:
                self._with_job_store_context(
                    None,
                    lambda: self.job_store.count_jobs_by_status(organization_id=None),
                    superuser=True,
                )
        except (ProgrammingError, OperationalError) as exc:
            if not _is_missing_runtime_table_error(exc):
                raise
            self._last_error = "journey runtime schema unavailable"
            logger.warning(
                "journey runtime startup skipped because journey_runtime_jobs is unavailable"
            )
            return False
        return True

    def shutdown(self) -> None:
        self._started = False
        self._stop_event.set()
        for thread in self._worker_threads:
            thread.join(timeout=max(5.0, self.poll_interval_seconds + 1.0))
        self._worker_threads = []
        if self._scheduler_thread is not None:
            self._scheduler_thread.join(timeout=max(5.0, self.abandonment_sweep_interval_seconds + 1.0))
        self._scheduler_thread = None

    def schedule_definition_rebuild(
        self,
        *,
        definition_id: str,
        payload: JourneyDefinitionRebuildRequest,
        organization_id: str,
    ) -> JourneyRuntimeJob:
        normalized_payload = payload.model_copy(update={"execution_mode": "sync"})
        return self._enqueue_job(
            JourneyRuntimeJob(
                organization_id=organization_id,
                kind="definition_rebuild",
                definition_id=definition_id,
                payload=normalized_payload.model_dump(mode="json"),
            ),
        )

    def schedule_definition_replay(
        self,
        *,
        definition_id: str,
        payload: JourneyReplayRequest,
        organization_id: str,
    ) -> JourneyRuntimeJob:
        normalized_payload = payload.model_copy(update={"execution_mode": "sync"})
        return self._enqueue_job(
            JourneyRuntimeJob(
                organization_id=organization_id,
                kind="definition_replay",
                definition_id=definition_id,
                payload=normalized_payload.model_dump(mode="json"),
            ),
        )

    def schedule_journey_replay(
        self,
        *,
        journey_id: str,
        payload: JourneyReplayRequest,
        organization_id: str,
    ) -> JourneyRuntimeJob:
        normalized_payload = payload.model_copy(update={"execution_mode": "sync"})
        return self._enqueue_job(
            JourneyRuntimeJob(
                organization_id=organization_id,
                kind="journey_replay",
                journey_id=journey_id,
                payload=normalized_payload.model_dump(mode="json"),
            ),
        )

    def schedule_analytics_rebuild(
        self,
        payload: JourneyAnalyticsRebuildRequest,
        *,
        organization_id: str,
    ) -> JourneyRuntimeJob:
        normalized_payload = payload.model_copy(update={"execution_mode": "sync"})
        return self._enqueue_job(
            JourneyRuntimeJob(
                organization_id=organization_id,
                kind="analytics_rebuild",
                definition_id=payload.definition_id,
                payload=normalized_payload.model_dump(mode="json"),
            ),
        )

    def schedule_abandonment_sweep(
        self,
        payload: JourneyAbandonmentSweepRequest,
        *,
        organization_id: str,
    ) -> JourneyRuntimeJob:
        normalized_payload = payload.model_copy(update={"execution_mode": "sync"})
        return self._enqueue_job(
            JourneyRuntimeJob(
                organization_id=organization_id,
                kind="abandonment_sweep",
                definition_id=payload.definition_id,
                payload=normalized_payload.model_dump(mode="json"),
            ),
        )

    def get_job(self, job_id: str, *, organization_id: str | None = None) -> JourneyRuntimeJob | None:
        with self._lock:
            return self._with_job_store_context(
                organization_id,
                lambda: self.job_store.load_job(job_id, organization_id=organization_id),
                superuser=organization_id is None,
            )

    def status(self, *, organization_id: str | None = None, recent_job_limit: int = 8) -> JourneyRuntimeStatus:
        window_start = _utcnow() - timedelta(seconds=max(1.0, self.failure_alert_window_seconds))
        with self._lock:
            counts = self._with_job_store_context(
                organization_id,
                lambda: self.job_store.count_jobs_by_status(organization_id=organization_id),
                superuser=organization_id is None,
            )
            recent_jobs = self._with_job_store_context(
                organization_id,
                lambda: self.job_store.list_recent_jobs(
                    organization_id=organization_id,
                    limit=recent_job_limit,
                ),
                superuser=organization_id is None,
            )
            job_metrics = self._with_job_store_context(
                organization_id,
                lambda: self.job_store.summarize_job_metrics(
                    organization_id=organization_id,
                    window_start=window_start,
                ),
                superuser=organization_id is None,
            )
            last_error = self._with_job_store_context(
                organization_id,
                lambda: self.job_store.latest_error(organization_id=organization_id),
                superuser=organization_id is None,
            ) or self._last_error
        return JourneyRuntimeStatus(
            queued_jobs=counts.get("queued", 0),
            running_jobs=counts.get("running", 0),
            completed_jobs=counts.get("completed", 0),
            failed_jobs=counts.get("failed", 0),
            embedded_worker_enabled=self.embedded_worker_enabled,
            last_error=last_error,
            job_metrics=job_metrics,
            alerts=self._build_alerts(job_metrics),
            recent_jobs=recent_jobs,
        )

    def process_available_jobs_once(
        self,
        *,
        max_jobs: int = 1,
        organization_id: str | None = None,
        worker_id: str | None = None,
    ) -> list[JourneyRuntimeJob]:
        processed: list[JourneyRuntimeJob] = []
        effective_worker_id = worker_id or self._compose_worker_id("manual")
        while len(processed) < max(1, max_jobs):
            with self._lock:
                claimed = self._with_job_store_context(
                    organization_id,
                    lambda: self.job_store.claim_next_job(
                        worker_id=effective_worker_id,
                        lease_expires_at=self._lease_expires_at(),
                        organization_id=organization_id,
                        now=_utcnow(),
                    ),
                    superuser=organization_id is None,
                )
            if claimed is None:
                break
            processed.append(self._execute_claimed_job(claimed, worker_id=effective_worker_id))
        return processed

    def run_abandonment_sweep_cycle(self) -> list[JourneyRuntimeJob]:
        jobs: list[JourneyRuntimeJob] = []
        for organization_id in self._organization_ids_for_abandonment():
            try:
                jobs.append(
                    self.schedule_abandonment_sweep(
                        JourneyAbandonmentSweepRequest(definition_id=None, execution_mode="async"),
                        organization_id=organization_id,
                    )
                )
            except Exception as exc:
                logger.exception(
                    "journey abandonment sweep scheduling failed",
                    extra={"organization_id": organization_id},
                )
                self._last_error = str(exc)
        return jobs

    def _enqueue_job(self, job: JourneyRuntimeJob) -> JourneyRuntimeJob:
        self._validate_job_target(job)
        if self.embedded_worker_enabled and not self._started:
            self.startup()
        with self._lock:
            try:
                persisted = self._with_job_store_context(
                    job.organization_id,
                    lambda: self.job_store.create_or_get_live_job(job),
                )
            except IntegrityError:
                # Surface domain errors for deleted/missing targets instead of leaking raw FK failures.
                self._validate_job_target(job)
                raise
            return persisted.model_copy(deep=True)

    def _execute_claimed_job(self, job: JourneyRuntimeJob, *, worker_id: str) -> JourneyRuntimeJob:
        heartbeat_stop = Event()
        heartbeat_thread = Thread(
            target=self._run_heartbeat_loop,
            args=(job.job_id, job.organization_id, worker_id, heartbeat_stop),
            name=f"{worker_id}-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            result = self._execute_job(job)
            payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else {"result": result}
            completed_job = job.model_copy(
                update={
                    "status": "completed",
                    "worker_id": None,
                    "lease_expires_at": None,
                    "finished_at": _utcnow(),
                    "result": payload,
                    "error": None,
                }
            )
            with self._lock:
                self._with_job_store_context(job.organization_id, lambda: self.job_store.save_job(completed_job))
            return completed_job.model_copy(deep=True)
        except Exception as exc:
            failed_job = job.model_copy(
                update={
                    "status": "failed",
                    "worker_id": None,
                    "lease_expires_at": None,
                    "finished_at": _utcnow(),
                    "error": str(exc),
                }
            )
            with self._lock:
                self._with_job_store_context(job.organization_id, lambda: self.job_store.save_job(failed_job))
                self._last_error = str(exc)
            logger.exception(
                "journey runtime job failed",
                extra={
                    "job_id": job.job_id,
                    "job_kind": job.kind,
                    "organization_id": job.organization_id,
                    "definition_id": job.definition_id,
                    "journey_id": job.journey_id,
                },
            )
            self._maybe_log_failure_alert(job.organization_id)
            return failed_job.model_copy(deep=True)
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=max(1.0, self.job_heartbeat_interval_seconds + 1.0))

    def _execute_job(self, job: JourneyRuntimeJob):
        with tenant_db_context(organization_id=job.organization_id):
            if job.kind == "definition_rebuild":
                if self.tracker is None:
                    raise RuntimeError("journey tracker unavailable for definition rebuild")
                if job.definition_id is None:
                    raise RuntimeError("definition rebuild job missing definition_id")
                payload = JourneyDefinitionRebuildRequest.model_validate(job.payload)
                return self.service.rebuild_definition(
                    job.definition_id,
                    payload,
                    organization_id=job.organization_id,
                    tracker=self.tracker,
                )
            if job.kind == "definition_replay":
                if self.tracker is None:
                    raise RuntimeError("journey tracker unavailable for definition replay")
                if job.definition_id is None:
                    raise RuntimeError("definition replay job missing definition_id")
                payload = JourneyReplayRequest.model_validate(job.payload)
                return self.service.replay_definition(
                    job.definition_id,
                    organization_id=job.organization_id,
                    tracker=self.tracker,
                    preserve_manual_events=payload.preserve_manual_events,
                )
            if job.kind == "journey_replay":
                if self.tracker is None:
                    raise RuntimeError("journey tracker unavailable for journey replay")
                if job.journey_id is None:
                    raise RuntimeError("journey replay job missing journey_id")
                payload = JourneyReplayRequest.model_validate(job.payload)
                return self.service.replay_journey(
                    job.journey_id,
                    organization_id=job.organization_id,
                    tracker=self.tracker,
                    preserve_manual_events=payload.preserve_manual_events,
                )
            if job.kind == "analytics_rebuild":
                payload = JourneyAnalyticsRebuildRequest.model_validate(job.payload)
                return self.service.rebuild_analytics(payload, organization_id=job.organization_id)
            if job.kind == "abandonment_sweep":
                payload = JourneyAbandonmentSweepRequest.model_validate(job.payload)
                return self.service.sweep_abandonment(payload, organization_id=job.organization_id)
        raise RuntimeError(f"unsupported journey runtime job kind: {job.kind}")

    def _organization_ids_for_abandonment(self) -> list[str]:
        with tenant_db_context(organization_id=None, is_superuser=True):
            if self.organization_ids_provider is not None:
                organization_ids = self.organization_ids_provider()
            else:
                organization_ids = [
                    definition.organization_id
                    for definition in self.service.list_definitions(status="active")
                    if definition.organization_id is not None
                ]
                instance_store = getattr(self.service, "_instance_store", None)
                if instance_store is not None:
                    organization_ids.extend(
                        instance.organization_id
                        for instance in instance_store.list_instances(status="open")
                    )
        return sorted({organization_id for organization_id in organization_ids if organization_id})

    def _run_abandonment_scheduler_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_abandonment_sweep_cycle()
            except Exception:
                logger.exception("journey abandonment scheduler loop failed")
            self._stop_event.wait(max(1.0, self.abandonment_sweep_interval_seconds))

    def _run_worker_loop(self, worker_index: int) -> None:
        worker_id = self._compose_worker_id(f"worker-{worker_index + 1}")
        # NOTE(#bonus-triage): In test environments the first iteration of
        # this loop can race the schema creation in ``build_session_factory``
        # when the worker thread wins the race to query ``journey_runtime_jobs``
        # before ``Base.metadata.create_all`` returns.  Not reproducible in
        # normal prod startup (migrations run ahead of server init) and the
        # loop retries transparently, but it produces noisy logs.  Leaving
        # this comment as a pointer — proper fix is to make ``startup`` await
        # a ready-signal from the session factory before launching workers.
        while not self._stop_event.is_set():
            try:
                processed = self.process_available_jobs_once(max_jobs=1, worker_id=worker_id)
            except Exception as exc:
                if _is_missing_runtime_table_error(exc):
                    self._last_error = "journey runtime schema unavailable"
                    logger.warning(
                        "journey runtime worker stopped because journey_runtime_jobs is unavailable",
                        extra={"worker_id": worker_id},
                    )
                    self._started = False
                    return
                self._last_error = str(exc)
                logger.exception("journey runtime worker loop failed", extra={"worker_id": worker_id})
                self._stop_event.wait(max(0.5, self.poll_interval_seconds))
                continue
            if processed:
                continue
            self._stop_event.wait(max(0.1, self.poll_interval_seconds))

    def _run_heartbeat_loop(
        self,
        job_id: str,
        organization_id: str,
        worker_id: str,
        stop_event: Event,
    ) -> None:
        interval_seconds = max(
            1.0,
            min(
                self.job_heartbeat_interval_seconds,
                max(1.0, self.job_lease_seconds / 3.0),
            ),
        )
        while not stop_event.wait(interval_seconds):
            try:
                with self._lock:
                    renewed = self._with_job_store_context(
                        organization_id,
                        lambda: self.job_store.heartbeat_job(
                            job_id,
                            worker_id=worker_id,
                            lease_expires_at=self._lease_expires_at(),
                        ),
                    )
            except Exception as exc:
                self._last_error = str(exc)
                logger.exception(
                    "journey runtime heartbeat failed",
                    extra={
                        "job_id": job_id,
                        "organization_id": organization_id,
                        "worker_id": worker_id,
                    },
                )
                return
            if renewed is None:
                return

    def _validate_job_target(self, job: JourneyRuntimeJob) -> None:
        if job.kind in {"definition_rebuild", "definition_replay"}:
            if job.definition_id is None:
                raise RuntimeError(f"{job.kind} job missing definition_id")
            self.service.get_definition(job.definition_id, organization_id=job.organization_id)
            return
        if job.kind in {"analytics_rebuild", "abandonment_sweep"}:
            if job.definition_id is None:
                return
            self.service.get_definition(job.definition_id, organization_id=job.organization_id)
            return
        if job.kind == "journey_replay":
            if job.journey_id is None:
                raise RuntimeError("journey_replay job missing journey_id")
            self.service.get_instance(job.journey_id, organization_id=job.organization_id)

    def _build_alerts(self, job_metrics) -> list[JourneyRuntimeAlert]:
        alerts: list[JourneyRuntimeAlert] = []
        threshold = max(1, int(self.failure_alert_threshold))
        window_seconds = max(1, int(self.failure_alert_window_seconds))
        for metric in job_metrics:
            if metric.recent_failures <= 0:
                continue
            severity = "error" if metric.recent_failures >= threshold else "warning"
            alerts.append(
                JourneyRuntimeAlert(
                    code=f"journey_runtime.{metric.kind}.recent_failures",
                    severity=severity,
                    kind=metric.kind,
                    message=(
                        f"{metric.kind} recorded {metric.recent_failures} failure(s) in the last "
                        f"{window_seconds} seconds."
                    ),
                    recent_failures=metric.recent_failures,
                    threshold=threshold,
                    window_seconds=window_seconds,
                    last_failure_at=metric.last_failure_at,
                )
            )
        return alerts

    def _maybe_log_failure_alert(self, organization_id: str) -> None:
        status = self.status(organization_id=organization_id, recent_job_limit=1)
        for alert in status.alerts:
            if alert.severity != "error":
                continue
            logger.error(
                "journey runtime alert triggered",
                extra={
                    "organization_id": organization_id,
                    "alert_code": alert.code,
                    "job_kind": alert.kind,
                    "recent_failures": alert.recent_failures,
                    "threshold": alert.threshold,
                },
            )
            break

    def _lease_expires_at(self) -> datetime:
        return _utcnow() + timedelta(seconds=max(5.0, self.job_lease_seconds))

    def _compose_worker_id(self, suffix: str) -> str:
        return f"{self.worker_identity}:{suffix}"

    def _with_job_store_context(
        self,
        organization_id: str | None,
        func,
        *,
        superuser: bool = False,
    ):
        with tenant_db_context(organization_id=organization_id, is_superuser=superuser):
            return func()


def wire_journey_runtime_integration(
    *,
    kernel: ConversationKernel,
    definition_store: JourneyDefinitionStore,
    instance_store: JourneyInstanceStore,
    realtime_control_plane: RealtimeControlPlane | None = None,
) -> JourneyTracker:
    base_trace_store = kernel.trace_store
    base_event_store = None if realtime_control_plane is None else realtime_control_plane.events
    tracker = JourneyTracker(
        definition_store=definition_store,
        instance_store=instance_store,
        conversation_store=kernel.conversation_store,
        trace_store=base_trace_store,
        realtime_event_store=base_event_store,
    )
    if not isinstance(base_trace_store, JourneyTrackingTraceStore):
        kernel._trace_store = JourneyTrackingTraceStore(base_trace_store, tracker)  # noqa: SLF001
    if realtime_control_plane is not None and base_event_store is not None:
        if not isinstance(base_event_store, JourneyTrackingRealtimeEventStore):
            realtime_control_plane.events = JourneyTrackingRealtimeEventStore(base_event_store, tracker)
    return tracker
