from __future__ import annotations

import time
from datetime import datetime, timezone

from ruhu.db import build_session_factory, tenant_db_context
from ruhu.journeys import (
    InMemoryJourneyRuntimeJobStore,
    JourneyAbandonmentSweepResponse,
    JourneyAnalyticsRebuildRequest,
    JourneyAnalyticsRebuildResponse,
    JourneyDefinition,
    JourneyDefinitionReplayResponse,
    JourneyReplayRequest,
    JourneyReplayResponse,
    JourneyRuntime,
    JourneyRuntimeJob,
    SQLAlchemyJourneyRuntimeJobStore,
    SQLAlchemyJourneyDefinitionStore,
    SubjectKeyStrategy,
)


class _StubJourneyService:
    def __init__(self, *, fail_kinds: set[str] | None = None) -> None:
        self.sweep_calls: list[str] = []
        self.definition_replay_calls: list[str] = []
        self.journey_replay_calls: list[str] = []
        self.fail_kinds = set(fail_kinds or set())

    def get_definition(self, definition_id, *, organization_id=None):  # type: ignore[no-untyped-def]
        return JourneyDefinition(
            definition_id=definition_id,
            organization_id=organization_id,
            slug=f"journey-{definition_id}",
            name=f"Journey {definition_id}",
            subject_strategy=SubjectKeyStrategy(kind="external_ref", value="subject.ref"),
        )

    def get_instance(self, journey_id, *, organization_id):  # type: ignore[no-untyped-def]
        return {"journey_id": journey_id, "organization_id": organization_id}

    def rebuild_definition(self, definition_id, payload, *, organization_id, tracker):  # type: ignore[no-untyped-def]
        if "definition_rebuild" in self.fail_kinds:
            raise RuntimeError("synthetic definition rebuild failure")
        return JourneyDefinitionReplayResponse(definition_id=definition_id)

    def replay_definition(self, definition_id, *, organization_id, tracker, preserve_manual_events):  # type: ignore[no-untyped-def]
        self.definition_replay_calls.append(definition_id)
        if "definition_replay" in self.fail_kinds:
            raise RuntimeError("synthetic definition replay failure")
        return JourneyDefinitionReplayResponse(definition_id=definition_id, replayed_journey_ids=["journey-1"])

    def replay_journey(self, journey_id, *, organization_id, tracker, preserve_manual_events):  # type: ignore[no-untyped-def]
        self.journey_replay_calls.append(journey_id)
        if "journey_replay" in self.fail_kinds:
            raise RuntimeError("synthetic journey replay failure")
        return JourneyReplayResponse(
            journey_id=journey_id,
            definition_id="definition-1",
            definition_version_id="version-1",
            conversation_ids=["conversation-1"],
        )

    def rebuild_analytics(self, payload, *, organization_id):  # type: ignore[no-untyped-def]
        if "analytics_rebuild" in self.fail_kinds:
            raise RuntimeError("synthetic analytics rebuild failure")
        return JourneyAnalyticsRebuildResponse(definition_id=payload.definition_id, rebuilt_views=["funnel"])

    def sweep_abandonment(self, payload, *, organization_id):  # type: ignore[no-untyped-def]
        self.sweep_calls.append(organization_id)
        if "abandonment_sweep" in self.fail_kinds:
            raise RuntimeError("synthetic abandonment failure")
        return JourneyAbandonmentSweepResponse(
            definition_id=payload.definition_id,
            abandoned_journey_ids=[f"{organization_id}:abandoned"],
        )

    def list_definitions(self, *, organization_id=None, status=None):  # type: ignore[no-untyped-def]
        return []


def test_journey_runtime_reclaims_expired_running_jobs(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    job_store = SQLAlchemyJourneyRuntimeJobStore(session_factory)
    submitted_at = datetime.now(timezone.utc)

    with tenant_db_context(organization_id="org-1"):
        job_store.save_job(
            JourneyRuntimeJob(
                job_id="stale-job",
                organization_id="org-1",
                kind="analytics_rebuild",
                status="running",
                worker_id="worker-old",
                lease_expires_at=submitted_at,
                attempt_count=1,
                payload=JourneyAnalyticsRebuildRequest(definition_id="journey-def-1").model_dump(mode="json"),
                submitted_at=submitted_at,
                started_at=submitted_at,
            )
        )

    runtime = JourneyRuntime(
        service=_StubJourneyService(),  # type: ignore[arg-type]
        tracker=object(),  # type: ignore[arg-type]
        job_store=job_store,
        embedded_worker_enabled=False,
        job_lease_seconds=30.0,
    )
    processed = runtime.process_available_jobs_once(max_jobs=1)

    reconciled = runtime.get_job("stale-job", organization_id="org-1")

    assert len(processed) == 1
    assert reconciled is not None
    assert reconciled.status == "completed"
    assert reconciled.attempt_count == 2
    assert reconciled.finished_at is not None


def test_journey_runtime_scheduler_runs_abandonment_sweeps() -> None:
    service = _StubJourneyService()
    runtime = JourneyRuntime(
        service=service,  # type: ignore[arg-type]
        tracker=None,  # type: ignore[arg-type]
        job_store=InMemoryJourneyRuntimeJobStore(),
        abandonment_sweep_enabled=True,
        abandonment_sweep_interval_seconds=0.05,
        organization_ids_provider=lambda: ["org-1", "public"],
    )

    runtime.startup()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        status = runtime.status()
        if status.completed_jobs >= 2:
            break
        time.sleep(0.02)
    runtime.shutdown()

    assert set(service.sweep_calls) >= {"org-1", "public"}
    assert status.completed_jobs >= 2


def test_journey_runtime_persists_completed_jobs_in_store(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    job_store = SQLAlchemyJourneyRuntimeJobStore(session_factory)
    runtime = JourneyRuntime(
        service=_StubJourneyService(),  # type: ignore[arg-type]
        tracker=None,  # type: ignore[arg-type]
        job_store=job_store,
    )

    queued = runtime.schedule_analytics_rebuild(
        JourneyAnalyticsRebuildRequest(),
        organization_id="org-1",
    )

    deadline = time.monotonic() + 2.0
    completed = None
    while time.monotonic() < deadline:
        completed = runtime.get_job(queued.job_id, organization_id="org-1")
        if completed is not None and completed.status == "completed":
            break
        time.sleep(0.02)
    runtime.shutdown()

    reloaded_runtime = JourneyRuntime(
        service=_StubJourneyService(),  # type: ignore[arg-type]
        tracker=None,  # type: ignore[arg-type]
        job_store=job_store,
    )
    persisted = reloaded_runtime.get_job(queued.job_id, organization_id="org-1")

    assert completed is not None
    assert completed.status == "completed"
    assert persisted is not None
    assert persisted.status == "completed"
    assert persisted.result == {
        "definition_id": None,
        "definition_version_id": None,
        "period_start": None,
        "period_end": None,
        "rebuilt_views": ["funnel"],
        "snapshot_count": 0,
    }


def test_journey_runtime_processes_queued_jobs_with_external_worker_mode(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    definition_store = SQLAlchemyJourneyDefinitionStore(session_factory)
    job_store = SQLAlchemyJourneyRuntimeJobStore(session_factory)
    service = _StubJourneyService()
    definition_store.save_definition(
        JourneyDefinition(
            definition_id="journey-def-1",
            organization_id="org-1",
            slug="journey-def-1",
            name="Journey Definition 1",
            subject_strategy=SubjectKeyStrategy(kind="external_ref", value="subject.ref"),
        )
    )
    runtime = JourneyRuntime(
        service=service,  # type: ignore[arg-type]
        tracker=object(),  # type: ignore[arg-type]
        job_store=job_store,
        embedded_worker_enabled=False,
    )

    queued = runtime.schedule_definition_replay(
        definition_id="journey-def-1",
        payload=JourneyReplayRequest(execution_mode="async"),
        organization_id="org-1",
    )
    processed = runtime.process_available_jobs_once(max_jobs=1)
    completed = runtime.get_job(queued.job_id, organization_id="org-1")

    assert len(processed) == 1
    assert completed is not None
    assert completed.status == "completed"
    assert service.definition_replay_calls == ["journey-def-1"]


def test_journey_runtime_status_surfaces_failure_metrics_and_alerts() -> None:
    runtime = JourneyRuntime(
        service=_StubJourneyService(fail_kinds={"definition_replay"}),  # type: ignore[arg-type]
        tracker=object(),  # type: ignore[arg-type]
        job_store=InMemoryJourneyRuntimeJobStore(),
        embedded_worker_enabled=False,
        failure_alert_threshold=2,
        failure_alert_window_seconds=3600,
    )

    runtime.schedule_definition_replay(
        definition_id="journey-def-1",
        payload=JourneyReplayRequest(execution_mode="async"),
        organization_id="org-1",
    )
    runtime.process_available_jobs_once(max_jobs=1)
    runtime.schedule_definition_replay(
        definition_id="journey-def-2",
        payload=JourneyReplayRequest(execution_mode="async"),
        organization_id="org-1",
    )
    runtime.process_available_jobs_once(max_jobs=1)

    status = runtime.status(organization_id="org-1")
    metrics = {item.kind: item for item in status.job_metrics}

    assert status.failed_jobs == 2
    assert metrics["definition_replay"].recent_failures == 2
    assert metrics["definition_replay"].failed_jobs == 2
    assert status.alerts
    assert status.alerts[0].kind == "definition_replay"
    assert status.alerts[0].severity == "error"
