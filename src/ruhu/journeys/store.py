from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ruhu.db_models import (
    JourneyAnalyticsSnapshotRecord,
    JourneyDefinitionRecord,
    JourneyDefinitionVersionRecord,
    JourneyEventRecord,
    JourneyInstanceRecord,
    JourneyRuntimeJobRecord,
    JourneyTouchpointRecord,
)

from .models import (
    JOURNEY_RUNTIME_JOB_KINDS,
    JourneyAnalyticsSnapshot,
    JourneyDefinition,
    JourneyDefinitionVersion,
    JourneyEvent,
    JourneyInstance,
    JourneyRuntimeKindMetrics,
    JourneyRuntimeJob,
    JourneyTouchpoint,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JourneyDefinitionStore(Protocol):
    def load_definition(self, definition_id: str, *, organization_id: str | None = None) -> JourneyDefinition | None: ...

    def save_definition(self, definition: JourneyDefinition) -> None: ...

    def list_definitions(
        self,
        *,
        organization_id: str | None = None,
        status: str | None = None,
    ) -> list[JourneyDefinition]: ...

    def load_version(
        self,
        definition_version_id: str,
        *,
        organization_id: str | None = None,
    ) -> JourneyDefinitionVersion | None: ...

    def save_version(self, version: JourneyDefinitionVersion) -> None: ...

    def list_versions(
        self,
        definition_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[JourneyDefinitionVersion]: ...

    def set_current_draft(
        self,
        definition_id: str,
        definition_version_id: str | None,
        *,
        organization_id: str | None = None,
    ) -> JourneyDefinition | None: ...

    def publish_version(
        self,
        definition_id: str,
        definition_version_id: str,
        *,
        organization_id: str | None = None,
    ) -> JourneyDefinitionVersion | None: ...


class JourneyInstanceStore(Protocol):
    def load_instance(self, journey_id: str, *, organization_id: str | None = None) -> JourneyInstance | None: ...

    def save_instance(self, instance: JourneyInstance) -> None: ...

    def delete_instance(self, journey_id: str, *, organization_id: str | None = None) -> None: ...

    def list_instances(
        self,
        *,
        organization_id: str | None = None,
        definition_id: str | None = None,
        status: str | None = None,
        subject_key: str | None = None,
    ) -> list[JourneyInstance]: ...

    def find_open_by_subject(
        self,
        *,
        organization_id: str | None,
        definition_id: str,
        subject_key: str,
    ) -> JourneyInstance | None: ...

    def save_touchpoint(self, touchpoint: JourneyTouchpoint) -> None: ...

    def list_touchpoints(self, journey_id: str, *, organization_id: str | None = None) -> list[JourneyTouchpoint]: ...

    def append_events(self, events: list[JourneyEvent]) -> None: ...

    def list_events(self, journey_id: str, *, organization_id: str | None = None) -> list[JourneyEvent]: ...

    def save_snapshot(self, snapshot: JourneyAnalyticsSnapshot) -> None: ...

    def list_snapshots(
        self,
        *,
        organization_id: str | None = None,
        view_kind: str | None = None,
        definition_id: str | None = None,
    ) -> list[JourneyAnalyticsSnapshot]: ...


class JourneyRuntimeJobStore(Protocol):
    def create_or_get_live_job(self, job: JourneyRuntimeJob) -> JourneyRuntimeJob: ...

    def claim_next_job(
        self,
        *,
        worker_id: str,
        lease_expires_at: datetime,
        organization_id: str | None = None,
        now: datetime | None = None,
    ) -> JourneyRuntimeJob | None: ...

    def heartbeat_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> JourneyRuntimeJob | None: ...

    def load_job(
        self,
        job_id: str,
        *,
        organization_id: str | None = None,
    ) -> JourneyRuntimeJob | None: ...

    def save_job(self, job: JourneyRuntimeJob) -> None: ...

    def count_jobs_by_status(self, *, organization_id: str | None = None) -> dict[str, int]: ...

    def list_recent_jobs(
        self,
        *,
        organization_id: str | None = None,
        limit: int = 8,
    ) -> list[JourneyRuntimeJob]: ...

    def summarize_job_metrics(
        self,
        *,
        organization_id: str | None = None,
        window_start: datetime | None = None,
    ) -> list[JourneyRuntimeKindMetrics]: ...

    def latest_error(self, *, organization_id: str | None = None) -> str | None: ...

    def fail_live_jobs(self, error: str, *, finished_at: datetime | None = None) -> int: ...


class InMemoryJourneyDefinitionStore:
    def __init__(self) -> None:
        self._definitions: dict[str, JourneyDefinition] = {}
        self._versions: dict[str, JourneyDefinitionVersion] = {}

    def load_definition(self, definition_id: str, *, organization_id: str | None = None) -> JourneyDefinition | None:
        item = self._definitions.get(definition_id)
        if item is None:
            return None
        if organization_id is not None and item.organization_id != organization_id:
            return None
        return item.model_copy(deep=True)

    def save_definition(self, definition: JourneyDefinition) -> None:
        self._definitions[definition.definition_id] = definition.model_copy(deep=True)

    def list_definitions(
        self,
        *,
        organization_id: str | None = None,
        status: str | None = None,
    ) -> list[JourneyDefinition]:
        items = [
            item
            for item in self._definitions.values()
            if (organization_id is None or item.organization_id == organization_id)
            and (status is None or item.status == status)
        ]
        return [item.model_copy(deep=True) for item in sorted(items, key=lambda item: item.updated_at, reverse=True)]

    def load_version(
        self,
        definition_version_id: str,
        *,
        organization_id: str | None = None,
    ) -> JourneyDefinitionVersion | None:
        item = self._versions.get(definition_version_id)
        if item is None:
            return None
        if organization_id is not None and item.organization_id != organization_id:
            return None
        return item.model_copy(deep=True)

    def save_version(self, version: JourneyDefinitionVersion) -> None:
        self._versions[version.definition_version_id] = version.model_copy(deep=True)

    def list_versions(
        self,
        definition_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[JourneyDefinitionVersion]:
        items = [
            item
            for item in self._versions.values()
            if item.definition_id == definition_id
            and (organization_id is None or item.organization_id == organization_id)
        ]
        return [item.model_copy(deep=True) for item in sorted(items, key=lambda item: item.version_number, reverse=True)]

    def set_current_draft(
        self,
        definition_id: str,
        definition_version_id: str | None,
        *,
        organization_id: str | None = None,
    ) -> JourneyDefinition | None:
        definition = self._definitions.get(definition_id)
        if definition is None:
            return None
        if organization_id is not None and definition.organization_id != organization_id:
            return None
        if definition_version_id is not None:
            version = self._versions.get(definition_version_id)
            if version is None or version.definition_id != definition_id:
                return None
            if organization_id is not None and version.organization_id != organization_id:
                return None
        updated = definition.model_copy(deep=True)
        updated.current_draft_version_id = definition_version_id
        updated.updated_at = _utcnow()
        self._definitions[definition_id] = updated
        return updated.model_copy(deep=True)

    def publish_version(
        self,
        definition_id: str,
        definition_version_id: str,
        *,
        organization_id: str | None = None,
    ) -> JourneyDefinitionVersion | None:
        definition = self._definitions.get(definition_id)
        version = self._versions.get(definition_version_id)
        if definition is None or version is None or version.definition_id != definition_id:
            return None
        if organization_id is not None and (definition.organization_id != organization_id or version.organization_id != organization_id):
            return None
        now = _utcnow()
        updated_definition = definition.model_copy(deep=True)
        updated_definition.current_published_version_id = definition_version_id
        if updated_definition.current_draft_version_id == definition_version_id:
            updated_definition.current_draft_version_id = None
        updated_definition.updated_at = now
        self._definitions[definition_id] = updated_definition
        updated_version = version.model_copy(deep=True)
        updated_version.status = "published"
        updated_version.published_at = now
        updated_version.updated_at = now
        self._versions[definition_version_id] = updated_version
        return updated_version.model_copy(deep=True)


class InMemoryJourneyInstanceStore:
    def __init__(self) -> None:
        self._instances: dict[str, JourneyInstance] = {}
        self._touchpoints: dict[str, JourneyTouchpoint] = {}
        self._events: dict[str, JourneyEvent] = {}
        self._snapshots: dict[str, JourneyAnalyticsSnapshot] = {}

    def load_instance(self, journey_id: str, *, organization_id: str | None = None) -> JourneyInstance | None:
        item = self._instances.get(journey_id)
        if item is None:
            return None
        if organization_id is not None and item.organization_id != organization_id:
            return None
        return item.model_copy(deep=True)

    def save_instance(self, instance: JourneyInstance) -> None:
        self._instances[instance.journey_id] = instance.model_copy(deep=True)

    def delete_instance(self, journey_id: str, *, organization_id: str | None = None) -> None:
        instance = self._instances.get(journey_id)
        if instance is None:
            return
        if organization_id is not None and instance.organization_id != organization_id:
            return
        self._instances.pop(journey_id, None)
        self._touchpoints = {
            touchpoint_id: item
            for touchpoint_id, item in self._touchpoints.items()
            if item.journey_id != journey_id
        }
        self._events = {
            event_id: item
            for event_id, item in self._events.items()
            if item.journey_id != journey_id
        }

    def list_instances(
        self,
        *,
        organization_id: str | None = None,
        definition_id: str | None = None,
        status: str | None = None,
        subject_key: str | None = None,
    ) -> list[JourneyInstance]:
        items = [
            item
            for item in self._instances.values()
            if (organization_id is None or item.organization_id == organization_id)
            and (definition_id is None or item.definition_id == definition_id)
            and (status is None or item.status == status)
            and (subject_key is None or item.subject_key == subject_key)
        ]
        return [item.model_copy(deep=True) for item in sorted(items, key=lambda item: item.last_activity_at, reverse=True)]

    def find_open_by_subject(
        self,
        *,
        organization_id: str | None,
        definition_id: str,
        subject_key: str,
    ) -> JourneyInstance | None:
        for item in self._instances.values():
            if (
                item.organization_id == organization_id
                and item.definition_id == definition_id
                and item.subject_key == subject_key
                and item.status == "open"
            ):
                return item.model_copy(deep=True)
        return None

    def save_touchpoint(self, touchpoint: JourneyTouchpoint) -> None:
        self._touchpoints[touchpoint.touchpoint_id] = touchpoint.model_copy(deep=True)

    def list_touchpoints(self, journey_id: str, *, organization_id: str | None = None) -> list[JourneyTouchpoint]:
        items = [
            item
            for item in self._touchpoints.values()
            if item.journey_id == journey_id
            and (organization_id is None or item.organization_id == organization_id)
        ]
        return [item.model_copy(deep=True) for item in sorted(items, key=lambda item: item.started_at)]

    def append_events(self, events: list[JourneyEvent]) -> None:
        for event in events:
            self._events[event.journey_event_id] = event.model_copy(deep=True)

    def list_events(self, journey_id: str, *, organization_id: str | None = None) -> list[JourneyEvent]:
        items = [
            item
            for item in self._events.values()
            if item.journey_id == journey_id
            and (organization_id is None or item.organization_id == organization_id)
        ]
        return [
            item.model_copy(deep=True)
            for item in sorted(items, key=lambda item: (item.occurred_at, item.created_at, item.journey_event_id))
        ]

    def save_snapshot(self, snapshot: JourneyAnalyticsSnapshot) -> None:
        self._snapshots[snapshot.snapshot_id] = snapshot.model_copy(deep=True)

    def list_snapshots(
        self,
        *,
        organization_id: str | None = None,
        view_kind: str | None = None,
        definition_id: str | None = None,
    ) -> list[JourneyAnalyticsSnapshot]:
        items = [
            item
            for item in self._snapshots.values()
            if (organization_id is None or item.organization_id == organization_id)
            and (view_kind is None or item.view_kind == view_kind)
            and (definition_id is None or item.definition_id == definition_id)
        ]
        return [item.model_copy(deep=True) for item in sorted(items, key=lambda item: item.updated_at, reverse=True)]


class InMemoryJourneyRuntimeJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, JourneyRuntimeJob] = {}

    def create_or_get_live_job(self, job: JourneyRuntimeJob) -> JourneyRuntimeJob:
        candidate_live_key = _runtime_job_live_key(job)
        for existing in self._jobs.values():
            if existing.status not in {"queued", "running"}:
                continue
            if _runtime_job_live_key(existing) == candidate_live_key:
                return existing.model_copy(deep=True)
        self._jobs[job.job_id] = job.model_copy(deep=True)
        return job.model_copy(deep=True)

    def claim_next_job(
        self,
        *,
        worker_id: str,
        lease_expires_at: datetime,
        organization_id: str | None = None,
        now: datetime | None = None,
    ) -> JourneyRuntimeJob | None:
        effective_now = now or _utcnow()
        candidates = [
            item
            for item in self._jobs.values()
            if (organization_id is None or item.organization_id == organization_id)
            and (
                item.status == "queued"
                or (
                    item.status == "running"
                    and item.lease_expires_at is not None
                    and item.lease_expires_at <= effective_now
                )
            )
        ]
        if not candidates:
            return None
        queued_first = sorted(
            candidates,
            key=lambda item: (
                0 if item.status == "queued" else 1,
                item.submitted_at,
                item.job_id,
            ),
        )[0]
        claimed = queued_first.model_copy(
            update={
                "status": "running",
                "worker_id": worker_id,
                "lease_expires_at": lease_expires_at,
                "attempt_count": max(0, int(queued_first.attempt_count)) + 1,
                "started_at": effective_now,
                "finished_at": None,
                "error": None,
            }
        )
        self._jobs[claimed.job_id] = claimed
        return claimed.model_copy(deep=True)

    def heartbeat_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> JourneyRuntimeJob | None:
        existing = self._jobs.get(job_id)
        if existing is None or existing.status != "running" or existing.worker_id != worker_id:
            return None
        updated = existing.model_copy(update={"lease_expires_at": lease_expires_at})
        self._jobs[job_id] = updated
        return updated.model_copy(deep=True)

    def load_job(
        self,
        job_id: str,
        *,
        organization_id: str | None = None,
    ) -> JourneyRuntimeJob | None:
        item = self._jobs.get(job_id)
        if item is None:
            return None
        if organization_id is not None and item.organization_id != organization_id:
            return None
        return item.model_copy(deep=True)

    def save_job(self, job: JourneyRuntimeJob) -> None:
        self._jobs[job.job_id] = job.model_copy(deep=True)

    def count_jobs_by_status(self, *, organization_id: str | None = None) -> dict[str, int]:
        counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0}
        for job in self._jobs.values():
            if organization_id is not None and job.organization_id != organization_id:
                continue
            counts[job.status] = counts.get(job.status, 0) + 1
        return counts

    def list_recent_jobs(
        self,
        *,
        organization_id: str | None = None,
        limit: int = 8,
    ) -> list[JourneyRuntimeJob]:
        items = [
            item.model_copy(deep=True)
            for item in self._jobs.values()
            if organization_id is None or item.organization_id == organization_id
        ]
        return sorted(items, key=lambda item: (item.submitted_at, item.job_id), reverse=True)[: max(1, limit)]

    def summarize_job_metrics(
        self,
        *,
        organization_id: str | None = None,
        window_start: datetime | None = None,
    ) -> list[JourneyRuntimeKindMetrics]:
        metrics = {
            kind: JourneyRuntimeKindMetrics(kind=kind)
            for kind in JOURNEY_RUNTIME_JOB_KINDS
        }
        for job in self._jobs.values():
            if organization_id is not None and job.organization_id != organization_id:
                continue
            metric = metrics.setdefault(job.kind, JourneyRuntimeKindMetrics(kind=job.kind))
            if job.status == "queued":
                metric.queued_jobs += 1
            elif job.status == "running":
                metric.running_jobs += 1
            elif job.status == "completed":
                metric.completed_jobs += 1
                if metric.last_success_at is None or (
                    job.finished_at is not None and job.finished_at > metric.last_success_at
                ):
                    metric.last_success_at = job.finished_at
            elif job.status == "failed":
                metric.failed_jobs += 1
                if metric.last_failure_at is None or (
                    job.finished_at is not None and job.finished_at > metric.last_failure_at
                ):
                    metric.last_failure_at = job.finished_at
                if window_start is None or (job.finished_at is not None and job.finished_at >= window_start):
                    metric.recent_failures += 1
        return [metrics[kind] for kind in JOURNEY_RUNTIME_JOB_KINDS if kind in metrics]

    def latest_error(self, *, organization_id: str | None = None) -> str | None:
        failed = [
            item
            for item in self._jobs.values()
            if item.error is not None and (organization_id is None or item.organization_id == organization_id)
        ]
        if not failed:
            return None
        return max(failed, key=lambda item: ((item.finished_at or item.submitted_at), item.job_id)).error

    def fail_live_jobs(self, error: str, *, finished_at: datetime | None = None) -> int:
        effective_finished_at = finished_at or _utcnow()
        updated = 0
        for job_id, job in list(self._jobs.items()):
            if job.status not in {"queued", "running"}:
                continue
            self._jobs[job_id] = job.model_copy(
                update={
                    "status": "failed",
                    "finished_at": effective_finished_at,
                    "error": error,
                    "worker_id": None,
                    "lease_expires_at": None,
                }
            )
            updated += 1
        return updated


class SQLAlchemyJourneyDefinitionStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def load_definition(self, definition_id: str, *, organization_id: str | None = None) -> JourneyDefinition | None:
        with self._session_factory() as session:
            record = session.get(JourneyDefinitionRecord, definition_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            return _record_to_definition(record)

    def save_definition(self, definition: JourneyDefinition) -> None:
        with self._session_factory() as session:
            record = session.get(JourneyDefinitionRecord, definition.definition_id)
            if record is None:
                session.add(_definition_to_record(definition))
            else:
                _update_definition_record(record, definition)
            session.commit()

    def list_definitions(
        self,
        *,
        organization_id: str | None = None,
        status: str | None = None,
    ) -> list[JourneyDefinition]:
        statement = select(JourneyDefinitionRecord).order_by(JourneyDefinitionRecord.updated_at.desc())
        if organization_id is not None:
            statement = statement.where(JourneyDefinitionRecord.organization_id == organization_id)
        if status is not None:
            statement = statement.where(JourneyDefinitionRecord.status == status)
        with self._session_factory() as session:
            return [_record_to_definition(record) for record in session.execute(statement).scalars().all()]

    def load_version(
        self,
        definition_version_id: str,
        *,
        organization_id: str | None = None,
    ) -> JourneyDefinitionVersion | None:
        with self._session_factory() as session:
            record = session.get(JourneyDefinitionVersionRecord, definition_version_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            return _record_to_version(record)

    def save_version(self, version: JourneyDefinitionVersion) -> None:
        with self._session_factory() as session:
            record = session.get(JourneyDefinitionVersionRecord, version.definition_version_id)
            if record is None:
                session.add(_version_to_record(version))
            else:
                _update_version_record(record, version)
            session.commit()

    def list_versions(
        self,
        definition_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[JourneyDefinitionVersion]:
        statement = (
            select(JourneyDefinitionVersionRecord)
            .where(JourneyDefinitionVersionRecord.definition_id == definition_id)
            .order_by(JourneyDefinitionVersionRecord.version_number.desc())
        )
        if organization_id is not None:
            statement = statement.where(JourneyDefinitionVersionRecord.organization_id == organization_id)
        with self._session_factory() as session:
            return [_record_to_version(record) for record in session.execute(statement).scalars().all()]

    def set_current_draft(
        self,
        definition_id: str,
        definition_version_id: str | None,
        *,
        organization_id: str | None = None,
    ) -> JourneyDefinition | None:
        with self._session_factory() as session:
            record = session.get(JourneyDefinitionRecord, definition_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            if definition_version_id is not None:
                version = session.get(JourneyDefinitionVersionRecord, definition_version_id)
                if version is None or version.definition_id != definition_id:
                    return None
                if organization_id is not None and version.organization_id != organization_id:
                    return None
            record.current_draft_version_id = definition_version_id
            record.updated_at = _utcnow()
            session.commit()
            session.refresh(record)
            return _record_to_definition(record)

    def publish_version(
        self,
        definition_id: str,
        definition_version_id: str,
        *,
        organization_id: str | None = None,
    ) -> JourneyDefinitionVersion | None:
        with self._session_factory() as session:
            definition = session.get(JourneyDefinitionRecord, definition_id)
            version = session.get(JourneyDefinitionVersionRecord, definition_version_id)
            if definition is None or version is None or version.definition_id != definition_id:
                return None
            if organization_id is not None and (
                definition.organization_id != organization_id or version.organization_id != organization_id
            ):
                return None
            now = _utcnow()
            definition.current_published_version_id = definition_version_id
            if definition.current_draft_version_id == definition_version_id:
                definition.current_draft_version_id = None
            definition.updated_at = now
            version.status = "published"
            version.published_at = now
            version.updated_at = now
            session.commit()
            session.refresh(version)
            return _record_to_version(version)


class SQLAlchemyJourneyInstanceStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def load_instance(self, journey_id: str, *, organization_id: str | None = None) -> JourneyInstance | None:
        with self._session_factory() as session:
            record = session.get(JourneyInstanceRecord, journey_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            return _record_to_instance(record)

    def save_instance(self, instance: JourneyInstance) -> None:
        with self._session_factory() as session:
            record = session.get(JourneyInstanceRecord, instance.journey_id)
            if record is None:
                session.add(_instance_to_record(instance))
            else:
                _update_instance_record(record, instance)
            session.commit()

    def delete_instance(self, journey_id: str, *, organization_id: str | None = None) -> None:
        with self._session_factory() as session:
            record = session.get(JourneyInstanceRecord, journey_id)
            if record is None:
                return
            if organization_id is not None and record.organization_id != organization_id:
                return
            session.execute(
                delete(JourneyEventRecord).where(JourneyEventRecord.journey_id == journey_id)
            )
            session.execute(
                delete(JourneyTouchpointRecord).where(JourneyTouchpointRecord.journey_id == journey_id)
            )
            session.execute(
                delete(JourneyInstanceRecord).where(JourneyInstanceRecord.journey_id == journey_id)
            )
            session.commit()

    def list_instances(
        self,
        *,
        organization_id: str | None = None,
        definition_id: str | None = None,
        status: str | None = None,
        subject_key: str | None = None,
    ) -> list[JourneyInstance]:
        statement = select(JourneyInstanceRecord).order_by(JourneyInstanceRecord.last_activity_at.desc())
        if organization_id is not None:
            statement = statement.where(JourneyInstanceRecord.organization_id == organization_id)
        if definition_id is not None:
            statement = statement.where(JourneyInstanceRecord.definition_id == definition_id)
        if status is not None:
            statement = statement.where(JourneyInstanceRecord.status == status)
        if subject_key is not None:
            statement = statement.where(JourneyInstanceRecord.subject_key == subject_key)
        with self._session_factory() as session:
            return [_record_to_instance(record) for record in session.execute(statement).scalars().all()]

    def find_open_by_subject(
        self,
        *,
        organization_id: str | None,
        definition_id: str,
        subject_key: str,
    ) -> JourneyInstance | None:
        org_filter = (
            JourneyInstanceRecord.organization_id.is_(None)
            if organization_id is None
            else JourneyInstanceRecord.organization_id == organization_id
        )
        statement = (
            select(JourneyInstanceRecord)
            .where(
                org_filter,
                JourneyInstanceRecord.definition_id == definition_id,
                JourneyInstanceRecord.subject_key == subject_key,
                JourneyInstanceRecord.status == "open",
            )
            .limit(1)
        )
        with self._session_factory() as session:
            record = session.execute(statement).scalars().one_or_none()
            return None if record is None else _record_to_instance(record)

    def save_touchpoint(self, touchpoint: JourneyTouchpoint) -> None:
        with self._session_factory() as session:
            record = session.get(JourneyTouchpointRecord, touchpoint.touchpoint_id)
            if record is None:
                session.add(_touchpoint_to_record(touchpoint))
            else:
                _update_touchpoint_record(record, touchpoint)
            session.commit()

    def list_touchpoints(self, journey_id: str, *, organization_id: str | None = None) -> list[JourneyTouchpoint]:
        statement = (
            select(JourneyTouchpointRecord)
            .where(JourneyTouchpointRecord.journey_id == journey_id)
            .order_by(JourneyTouchpointRecord.started_at.asc())
        )
        if organization_id is not None:
            statement = statement.where(JourneyTouchpointRecord.organization_id == organization_id)
        with self._session_factory() as session:
            return [_record_to_touchpoint(record) for record in session.execute(statement).scalars().all()]

    def append_events(self, events: list[JourneyEvent]) -> None:
        with self._session_factory() as session:
            for event in events:
                record = session.get(JourneyEventRecord, event.journey_event_id)
                if record is None:
                    session.add(_event_to_record(event))
                else:
                    _update_event_record(record, event)
            session.commit()

    def list_events(self, journey_id: str, *, organization_id: str | None = None) -> list[JourneyEvent]:
        statement = (
            select(JourneyEventRecord)
            .where(JourneyEventRecord.journey_id == journey_id)
            .order_by(JourneyEventRecord.occurred_at.asc(), JourneyEventRecord.created_at.asc())
        )
        if organization_id is not None:
            statement = statement.where(JourneyEventRecord.organization_id == organization_id)
        with self._session_factory() as session:
            return [_record_to_event(record) for record in session.execute(statement).scalars().all()]

    def save_snapshot(self, snapshot: JourneyAnalyticsSnapshot) -> None:
        with self._session_factory() as session:
            record = session.get(JourneyAnalyticsSnapshotRecord, snapshot.snapshot_id)
            if record is None:
                session.add(_snapshot_to_record(snapshot))
            else:
                _update_snapshot_record(record, snapshot)
            session.commit()

    def list_snapshots(
        self,
        *,
        organization_id: str | None = None,
        view_kind: str | None = None,
        definition_id: str | None = None,
    ) -> list[JourneyAnalyticsSnapshot]:
        statement = select(JourneyAnalyticsSnapshotRecord).order_by(JourneyAnalyticsSnapshotRecord.updated_at.desc())
        if organization_id is not None:
            statement = statement.where(JourneyAnalyticsSnapshotRecord.organization_id == organization_id)
        if view_kind is not None:
            statement = statement.where(JourneyAnalyticsSnapshotRecord.view_kind == view_kind)
        if definition_id is not None:
            statement = statement.where(JourneyAnalyticsSnapshotRecord.definition_id == definition_id)
        with self._session_factory() as session:
            return [_record_to_snapshot(record) for record in session.execute(statement).scalars().all()]


class SQLAlchemyJourneyRuntimeJobStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_or_get_live_job(self, job: JourneyRuntimeJob) -> JourneyRuntimeJob:
        live_key = _runtime_job_live_key(job)
        with self._session_factory() as session:
            record = _runtime_job_to_record(job)
            record.live_key = live_key
            session.add(record)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.execute(
                    select(JourneyRuntimeJobRecord)
                    .where(JourneyRuntimeJobRecord.live_key == live_key)
                    .limit(1)
                ).scalars().one_or_none()
                if existing is None:
                    raise
                return _record_to_runtime_job(existing)
            return job.model_copy(deep=True)

    def claim_next_job(
        self,
        *,
        worker_id: str,
        lease_expires_at: datetime,
        organization_id: str | None = None,
        now: datetime | None = None,
    ) -> JourneyRuntimeJob | None:
        effective_now = now or _utcnow()
        with self._session_factory() as session:
            statement = (
                select(JourneyRuntimeJobRecord)
                .where(
                    or_(
                        JourneyRuntimeJobRecord.status == "queued",
                        and_(
                            JourneyRuntimeJobRecord.status == "running",
                            JourneyRuntimeJobRecord.lease_expires_at.is_not(None),
                            JourneyRuntimeJobRecord.lease_expires_at <= effective_now,
                        ),
                    )
                )
                .order_by(
                    case((JourneyRuntimeJobRecord.status == "queued", 0), else_=1),
                    JourneyRuntimeJobRecord.submitted_at.asc(),
                    JourneyRuntimeJobRecord.job_id.asc(),
                )
                .limit(1)
            )
            if organization_id is not None:
                statement = statement.where(JourneyRuntimeJobRecord.organization_id == organization_id)
            bind = session.get_bind()
            if bind is not None and bind.dialect.name == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            record = session.execute(statement).scalars().one_or_none()
            if record is None:
                return None
            record.status = "running"
            record.worker_id = worker_id
            record.lease_expires_at = lease_expires_at
            record.attempt_count = int(record.attempt_count or 0) + 1
            record.started_at = effective_now
            record.finished_at = None
            record.error = None
            session.commit()
            session.refresh(record)
            return _record_to_runtime_job(record)

    def heartbeat_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> JourneyRuntimeJob | None:
        with self._session_factory() as session:
            record = session.get(JourneyRuntimeJobRecord, job_id)
            if record is None or record.status != "running" or record.worker_id != worker_id:
                return None
            record.lease_expires_at = lease_expires_at
            session.commit()
            session.refresh(record)
            return _record_to_runtime_job(record)

    def load_job(
        self,
        job_id: str,
        *,
        organization_id: str | None = None,
    ) -> JourneyRuntimeJob | None:
        with self._session_factory() as session:
            record = session.get(JourneyRuntimeJobRecord, job_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            return _record_to_runtime_job(record)

    def save_job(self, job: JourneyRuntimeJob) -> None:
        with self._session_factory() as session:
            record = session.get(JourneyRuntimeJobRecord, job.job_id)
            if record is None:
                session.add(_runtime_job_to_record(job))
            else:
                _update_runtime_job_record(record, job)
            session.commit()

    def count_jobs_by_status(self, *, organization_id: str | None = None) -> dict[str, int]:
        statement = select(JourneyRuntimeJobRecord.status, func.count()).group_by(JourneyRuntimeJobRecord.status)
        if organization_id is not None:
            statement = statement.where(JourneyRuntimeJobRecord.organization_id == organization_id)
        counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0}
        with self._session_factory() as session:
            for status, count in session.execute(statement).all():
                counts[str(status)] = int(count)
        return counts

    def list_recent_jobs(
        self,
        *,
        organization_id: str | None = None,
        limit: int = 8,
    ) -> list[JourneyRuntimeJob]:
        statement = select(JourneyRuntimeJobRecord).order_by(
            JourneyRuntimeJobRecord.submitted_at.desc(),
            JourneyRuntimeJobRecord.job_id.desc(),
        )
        if organization_id is not None:
            statement = statement.where(JourneyRuntimeJobRecord.organization_id == organization_id)
        statement = statement.limit(max(1, limit))
        with self._session_factory() as session:
            return [_record_to_runtime_job(record) for record in session.execute(statement).scalars().all()]

    def summarize_job_metrics(
        self,
        *,
        organization_id: str | None = None,
        window_start: datetime | None = None,
    ) -> list[JourneyRuntimeKindMetrics]:
        metrics = {
            kind: JourneyRuntimeKindMetrics(kind=kind)
            for kind in JOURNEY_RUNTIME_JOB_KINDS
        }
        counts_statement = (
            select(
                JourneyRuntimeJobRecord.kind,
                JourneyRuntimeJobRecord.status,
                func.count(),
            )
            .group_by(JourneyRuntimeJobRecord.kind, JourneyRuntimeJobRecord.status)
        )
        last_failure_statement = (
            select(
                JourneyRuntimeJobRecord.kind,
                func.max(JourneyRuntimeJobRecord.finished_at),
            )
            .where(JourneyRuntimeJobRecord.status == "failed")
            .group_by(JourneyRuntimeJobRecord.kind)
        )
        last_success_statement = (
            select(
                JourneyRuntimeJobRecord.kind,
                func.max(JourneyRuntimeJobRecord.finished_at),
            )
            .where(JourneyRuntimeJobRecord.status == "completed")
            .group_by(JourneyRuntimeJobRecord.kind)
        )
        recent_failures_statement = (
            select(
                JourneyRuntimeJobRecord.kind,
                func.count(),
            )
            .where(JourneyRuntimeJobRecord.status == "failed")
            .group_by(JourneyRuntimeJobRecord.kind)
        )
        if window_start is not None:
            recent_failures_statement = recent_failures_statement.where(
                JourneyRuntimeJobRecord.finished_at >= window_start,
            )
        if organization_id is not None:
            counts_statement = counts_statement.where(JourneyRuntimeJobRecord.organization_id == organization_id)
            last_failure_statement = last_failure_statement.where(JourneyRuntimeJobRecord.organization_id == organization_id)
            last_success_statement = last_success_statement.where(JourneyRuntimeJobRecord.organization_id == organization_id)
            recent_failures_statement = recent_failures_statement.where(JourneyRuntimeJobRecord.organization_id == organization_id)
        with self._session_factory() as session:
            for kind, status, count in session.execute(counts_statement).all():
                metric = metrics.setdefault(str(kind), JourneyRuntimeKindMetrics(kind=str(kind)))
                if status == "queued":
                    metric.queued_jobs = int(count)
                elif status == "running":
                    metric.running_jobs = int(count)
                elif status == "completed":
                    metric.completed_jobs = int(count)
                elif status == "failed":
                    metric.failed_jobs = int(count)
            for kind, finished_at in session.execute(last_failure_statement).all():
                metrics.setdefault(str(kind), JourneyRuntimeKindMetrics(kind=str(kind))).last_failure_at = finished_at
            for kind, finished_at in session.execute(last_success_statement).all():
                metrics.setdefault(str(kind), JourneyRuntimeKindMetrics(kind=str(kind))).last_success_at = finished_at
            for kind, count in session.execute(recent_failures_statement).all():
                metrics.setdefault(str(kind), JourneyRuntimeKindMetrics(kind=str(kind))).recent_failures = int(count)
        return [metrics[kind] for kind in JOURNEY_RUNTIME_JOB_KINDS if kind in metrics]

    def latest_error(self, *, organization_id: str | None = None) -> str | None:
        statement = (
            select(JourneyRuntimeJobRecord.error)
            .where(JourneyRuntimeJobRecord.error.is_not(None))
            .order_by(
                JourneyRuntimeJobRecord.finished_at.desc(),
                JourneyRuntimeJobRecord.submitted_at.desc(),
                JourneyRuntimeJobRecord.job_id.desc(),
            )
            .limit(1)
        )
        if organization_id is not None:
            statement = statement.where(JourneyRuntimeJobRecord.organization_id == organization_id)
        with self._session_factory() as session:
            return session.execute(statement).scalar_one_or_none()

    def fail_live_jobs(self, error: str, *, finished_at: datetime | None = None) -> int:
        effective_finished_at = finished_at or _utcnow()
        updated = 0
        with self._session_factory() as session:
            statement = select(JourneyRuntimeJobRecord).where(JourneyRuntimeJobRecord.live_key.is_not(None))
            records = session.execute(statement).scalars().all()
            for record in records:
                record.status = "failed"
                record.finished_at = effective_finished_at
                record.error = error
                record.live_key = None
                record.worker_id = None
                record.lease_expires_at = None
                updated += 1
            session.commit()
        return updated


def _record_to_definition(record: JourneyDefinitionRecord) -> JourneyDefinition:
    return JourneyDefinition(
        definition_id=record.definition_id,
        organization_id=record.organization_id,
        slug=record.slug,
        name=record.name,
        description=record.description,
        subject_strategy=record.subject_strategy_json or {},
        scope=record.scope_json or {},
        status=record.status,
        tags=list(record.tags_json or []),
        settings=dict(record.settings_json or {}),
        current_draft_version_id=record.current_draft_version_id,
        current_published_version_id=record.current_published_version_id,
        created_by_user_id=record.created_by_user_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _definition_to_record(definition: JourneyDefinition) -> JourneyDefinitionRecord:
    return JourneyDefinitionRecord(
        definition_id=definition.definition_id,
        organization_id=definition.organization_id,
        slug=definition.slug,
        name=definition.name,
        description=definition.description,
        subject_strategy_json=definition.subject_strategy.model_dump(mode="json"),
        scope_json=definition.scope.model_dump(mode="json"),
        status=definition.status,
        tags_json=list(definition.tags),
        settings_json=dict(definition.settings),
        current_draft_version_id=definition.current_draft_version_id,
        current_published_version_id=definition.current_published_version_id,
        created_by_user_id=definition.created_by_user_id,
        created_at=definition.created_at,
        updated_at=definition.updated_at,
    )


def _update_definition_record(record: JourneyDefinitionRecord, definition: JourneyDefinition) -> None:
    record.organization_id = definition.organization_id
    record.slug = definition.slug
    record.name = definition.name
    record.description = definition.description
    record.subject_strategy_json = definition.subject_strategy.model_dump(mode="json")
    record.scope_json = definition.scope.model_dump(mode="json")
    record.status = definition.status
    record.tags_json = list(definition.tags)
    record.settings_json = dict(definition.settings)
    record.current_draft_version_id = definition.current_draft_version_id
    record.current_published_version_id = definition.current_published_version_id
    record.created_by_user_id = definition.created_by_user_id
    record.created_at = definition.created_at
    record.updated_at = definition.updated_at


def _record_to_version(record: JourneyDefinitionVersionRecord) -> JourneyDefinitionVersion:
    return JourneyDefinitionVersion(
        definition_version_id=record.definition_version_id,
        organization_id=record.organization_id,
        definition_id=record.definition_id,
        version_number=record.version_number,
        status=record.status,
        based_on_version_id=record.based_on_version_id,
        rules=record.rules_json or {},
        compiled_rules=dict(record.compiled_rules_json or {}),
        review_summary=dict(record.review_summary_json or {}),
        created_by_user_id=record.created_by_user_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
        published_at=record.published_at,
    )


def _version_to_record(version: JourneyDefinitionVersion) -> JourneyDefinitionVersionRecord:
    return JourneyDefinitionVersionRecord(
        definition_version_id=version.definition_version_id,
        organization_id=version.organization_id,
        definition_id=version.definition_id,
        version_number=version.version_number,
        status=version.status,
        based_on_version_id=version.based_on_version_id,
        rules_json=version.rules.model_dump(mode="json"),
        compiled_rules_json=dict(version.compiled_rules),
        review_summary_json=dict(version.review_summary),
        created_by_user_id=version.created_by_user_id,
        created_at=version.created_at,
        updated_at=version.updated_at,
        published_at=version.published_at,
    )


def _update_version_record(record: JourneyDefinitionVersionRecord, version: JourneyDefinitionVersion) -> None:
    record.organization_id = version.organization_id
    record.definition_id = version.definition_id
    record.version_number = version.version_number
    record.status = version.status
    record.based_on_version_id = version.based_on_version_id
    record.rules_json = version.rules.model_dump(mode="json")
    record.compiled_rules_json = dict(version.compiled_rules)
    record.review_summary_json = dict(version.review_summary)
    record.created_by_user_id = version.created_by_user_id
    record.created_at = version.created_at
    record.updated_at = version.updated_at
    record.published_at = version.published_at


def _record_to_instance(record: JourneyInstanceRecord) -> JourneyInstance:
    return JourneyInstance(
        journey_id=record.journey_id,
        organization_id=record.organization_id,
        definition_id=record.definition_id,
        definition_version_id=record.definition_version_id,
        subject_key=record.subject_key,
        subject_summary=dict(record.subject_summary_json or {}),
        status=record.status,
        outcome=record.outcome,
        current_milestone_id=record.current_milestone_id,
        current_milestone_order=record.current_milestone_order,
        milestone_path=list(record.milestone_path_json or []),
        first_conversation_id=record.first_conversation_id,
        latest_conversation_id=record.latest_conversation_id,
        first_agent_id=record.first_agent_id,
        first_agent_version_id=record.first_agent_version_id,
        latest_agent_id=record.latest_agent_id,
        latest_agent_version_id=record.latest_agent_version_id,
        started_at=record.started_at,
        last_activity_at=record.last_activity_at,
        ended_at=record.ended_at,
        metadata=dict(record.metadata_json or {}),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _instance_to_record(instance: JourneyInstance) -> JourneyInstanceRecord:
    return JourneyInstanceRecord(
        journey_id=instance.journey_id,
        organization_id=instance.organization_id,
        definition_id=instance.definition_id,
        definition_version_id=instance.definition_version_id,
        subject_key=instance.subject_key,
        subject_summary_json=dict(instance.subject_summary),
        status=instance.status,
        outcome=instance.outcome,
        current_milestone_id=instance.current_milestone_id,
        current_milestone_order=instance.current_milestone_order,
        milestone_path_json=list(instance.milestone_path),
        first_conversation_id=instance.first_conversation_id,
        latest_conversation_id=instance.latest_conversation_id,
        first_agent_id=instance.first_agent_id,
        first_agent_version_id=instance.first_agent_version_id,
        latest_agent_id=instance.latest_agent_id,
        latest_agent_version_id=instance.latest_agent_version_id,
        started_at=instance.started_at,
        last_activity_at=instance.last_activity_at,
        ended_at=instance.ended_at,
        metadata_json=dict(instance.metadata),
        created_at=instance.created_at,
        updated_at=instance.updated_at,
    )


def _update_instance_record(record: JourneyInstanceRecord, instance: JourneyInstance) -> None:
    record.organization_id = instance.organization_id
    record.definition_id = instance.definition_id
    record.definition_version_id = instance.definition_version_id
    record.subject_key = instance.subject_key
    record.subject_summary_json = dict(instance.subject_summary)
    record.status = instance.status
    record.outcome = instance.outcome
    record.current_milestone_id = instance.current_milestone_id
    record.current_milestone_order = instance.current_milestone_order
    record.milestone_path_json = list(instance.milestone_path)
    record.first_conversation_id = instance.first_conversation_id
    record.latest_conversation_id = instance.latest_conversation_id
    record.first_agent_id = instance.first_agent_id
    record.first_agent_version_id = instance.first_agent_version_id
    record.latest_agent_id = instance.latest_agent_id
    record.latest_agent_version_id = instance.latest_agent_version_id
    record.started_at = instance.started_at
    record.last_activity_at = instance.last_activity_at
    record.ended_at = instance.ended_at
    record.metadata_json = dict(instance.metadata)
    record.created_at = instance.created_at
    record.updated_at = instance.updated_at


def _record_to_touchpoint(record: JourneyTouchpointRecord) -> JourneyTouchpoint:
    return JourneyTouchpoint(
        touchpoint_id=record.touchpoint_id,
        organization_id=record.organization_id,
        journey_id=record.journey_id,
        conversation_id=record.conversation_id,
        agent_id=record.agent_id,
        agent_version_id=record.agent_version_id,
        channel=record.channel,
        mode=record.mode,
        entry_reason=record.entry_reason,
        metadata=dict(record.metadata_json or {}),
        started_at=record.started_at,
        ended_at=record.ended_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _touchpoint_to_record(touchpoint: JourneyTouchpoint) -> JourneyTouchpointRecord:
    return JourneyTouchpointRecord(
        touchpoint_id=touchpoint.touchpoint_id,
        organization_id=touchpoint.organization_id,
        journey_id=touchpoint.journey_id,
        conversation_id=touchpoint.conversation_id,
        agent_id=touchpoint.agent_id,
        agent_version_id=touchpoint.agent_version_id,
        channel=touchpoint.channel,
        mode=touchpoint.mode,
        entry_reason=touchpoint.entry_reason,
        metadata_json=dict(touchpoint.metadata),
        started_at=touchpoint.started_at,
        ended_at=touchpoint.ended_at,
        created_at=touchpoint.created_at,
        updated_at=touchpoint.updated_at,
    )


def _update_touchpoint_record(record: JourneyTouchpointRecord, touchpoint: JourneyTouchpoint) -> None:
    record.organization_id = touchpoint.organization_id
    record.journey_id = touchpoint.journey_id
    record.conversation_id = touchpoint.conversation_id
    record.agent_id = touchpoint.agent_id
    record.agent_version_id = touchpoint.agent_version_id
    record.channel = touchpoint.channel
    record.mode = touchpoint.mode
    record.entry_reason = touchpoint.entry_reason
    record.metadata_json = dict(touchpoint.metadata)
    record.started_at = touchpoint.started_at
    record.ended_at = touchpoint.ended_at
    record.created_at = touchpoint.created_at
    record.updated_at = touchpoint.updated_at


def _record_to_event(record: JourneyEventRecord) -> JourneyEvent:
    return JourneyEvent(
        journey_event_id=record.journey_event_id,
        organization_id=record.organization_id,
        journey_id=record.journey_id,
        touchpoint_id=record.touchpoint_id,
        conversation_id=record.conversation_id,
        turn_trace_id=record.turn_trace_id,
        realtime_event_id=record.realtime_event_id,
        tool_invocation_id=record.tool_invocation_id,
        event_type=record.event_type,
        milestone_id=record.milestone_id,
        source=record.source,
        idempotency_key=record.idempotency_key,
        payload=dict(record.payload_json or {}),
        occurred_at=record.occurred_at,
        created_at=record.created_at,
    )


def _event_to_record(event: JourneyEvent) -> JourneyEventRecord:
    return JourneyEventRecord(
        journey_event_id=event.journey_event_id,
        organization_id=event.organization_id,
        journey_id=event.journey_id,
        touchpoint_id=event.touchpoint_id,
        conversation_id=event.conversation_id,
        turn_trace_id=event.turn_trace_id,
        realtime_event_id=event.realtime_event_id,
        tool_invocation_id=event.tool_invocation_id,
        event_type=event.event_type,
        milestone_id=event.milestone_id,
        source=event.source,
        idempotency_key=event.idempotency_key,
        payload_json=dict(event.payload),
        occurred_at=event.occurred_at,
        created_at=event.created_at,
    )


def _update_event_record(record: JourneyEventRecord, event: JourneyEvent) -> None:
    record.organization_id = event.organization_id
    record.journey_id = event.journey_id
    record.touchpoint_id = event.touchpoint_id
    record.conversation_id = event.conversation_id
    record.turn_trace_id = event.turn_trace_id
    record.realtime_event_id = event.realtime_event_id
    record.tool_invocation_id = event.tool_invocation_id
    record.event_type = event.event_type
    record.milestone_id = event.milestone_id
    record.source = event.source
    record.idempotency_key = event.idempotency_key
    record.payload_json = dict(event.payload)
    record.occurred_at = event.occurred_at
    record.created_at = event.created_at


def _runtime_job_live_key(job: JourneyRuntimeJob) -> str:
    payload = {
        "organization_id": job.organization_id,
        "kind": job.kind,
        "definition_id": job.definition_id,
        "journey_id": job.journey_id,
        "payload": job.payload,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _record_to_runtime_job(record: JourneyRuntimeJobRecord) -> JourneyRuntimeJob:
    return JourneyRuntimeJob(
        job_id=record.job_id,
        organization_id=record.organization_id,
        kind=record.kind,
        definition_id=record.definition_id,
        journey_id=record.journey_id,
        status=record.status,
        worker_id=record.worker_id,
        lease_expires_at=record.lease_expires_at,
        attempt_count=int(record.attempt_count or 0),
        payload=dict(record.payload_json or {}),
        result=None if record.result_json is None else dict(record.result_json or {}),
        error=record.error,
        submitted_at=record.submitted_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
    )


def _runtime_job_to_record(job: JourneyRuntimeJob) -> JourneyRuntimeJobRecord:
    return JourneyRuntimeJobRecord(
        job_id=job.job_id,
        organization_id=job.organization_id,
        kind=job.kind,
        definition_id=job.definition_id,
        journey_id=job.journey_id,
        status=job.status,
        live_key=_runtime_job_live_key(job) if job.status in {"queued", "running"} else None,
        worker_id=job.worker_id,
        lease_expires_at=job.lease_expires_at,
        attempt_count=max(0, int(job.attempt_count)),
        payload_json=dict(job.payload),
        result_json=None if job.result is None else dict(job.result),
        error=job.error,
        submitted_at=job.submitted_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _update_runtime_job_record(record: JourneyRuntimeJobRecord, job: JourneyRuntimeJob) -> None:
    record.organization_id = job.organization_id
    record.kind = job.kind
    record.definition_id = job.definition_id
    record.journey_id = job.journey_id
    record.status = job.status
    record.live_key = _runtime_job_live_key(job) if job.status in {"queued", "running"} else None
    record.worker_id = job.worker_id
    record.lease_expires_at = job.lease_expires_at
    record.attempt_count = max(0, int(job.attempt_count))
    record.payload_json = dict(job.payload)
    record.result_json = None if job.result is None else dict(job.result)
    record.error = job.error
    record.submitted_at = job.submitted_at
    record.started_at = job.started_at
    record.finished_at = job.finished_at


def _record_to_snapshot(record: JourneyAnalyticsSnapshotRecord) -> JourneyAnalyticsSnapshot:
    return JourneyAnalyticsSnapshot(
        snapshot_id=record.snapshot_id,
        organization_id=record.organization_id,
        view_kind=record.view_kind,
        definition_id=record.definition_id,
        definition_version_id=record.definition_version_id,
        period_start=record.period_start,
        period_end=record.period_end,
        granularity=record.granularity,
        filter_key=record.filter_key,
        filters=dict(record.filters_json or {}),
        metrics=dict(record.metrics_json or {}),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _snapshot_to_record(snapshot: JourneyAnalyticsSnapshot) -> JourneyAnalyticsSnapshotRecord:
    return JourneyAnalyticsSnapshotRecord(
        snapshot_id=snapshot.snapshot_id,
        organization_id=snapshot.organization_id,
        view_kind=snapshot.view_kind,
        definition_id=snapshot.definition_id,
        definition_version_id=snapshot.definition_version_id,
        period_start=snapshot.period_start,
        period_end=snapshot.period_end,
        granularity=snapshot.granularity,
        filter_key=snapshot.filter_key,
        filters_json=dict(snapshot.filters),
        metrics_json=dict(snapshot.metrics),
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
    )


def _update_snapshot_record(record: JourneyAnalyticsSnapshotRecord, snapshot: JourneyAnalyticsSnapshot) -> None:
    record.organization_id = snapshot.organization_id
    record.view_kind = snapshot.view_kind
    record.definition_id = snapshot.definition_id
    record.definition_version_id = snapshot.definition_version_id
    record.period_start = snapshot.period_start
    record.period_end = snapshot.period_end
    record.granularity = snapshot.granularity
    record.filter_key = snapshot.filter_key
    record.filters_json = dict(snapshot.filters)
    record.metrics_json = dict(snapshot.metrics)
    record.created_at = snapshot.created_at
    record.updated_at = snapshot.updated_at
