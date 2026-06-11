from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ruhu.db_models import ToolIntegrationJobRecord

from .types import ToolIntegrationJob


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ToolIntegrationJobStore(Protocol):
    def create_or_get_for_invocation(self, job: ToolIntegrationJob) -> ToolIntegrationJob: ...

    def load(self, job_id: str, *, organization_id: str | None = None) -> ToolIntegrationJob | None: ...

    def load_by_invocation(
        self,
        invocation_id: str,
        *,
        organization_id: str | None = None,
    ) -> ToolIntegrationJob | None: ...

    def load_by_callback_correlation_id(
        self,
        callback_correlation_id: str,
        *,
        organization_id: str | None = None,
    ) -> ToolIntegrationJob | None: ...

    def save(self, job: ToolIntegrationJob) -> None: ...

    def claim_next_job(
        self,
        *,
        worker_id: str,
        lease_expires_at: datetime,
        organization_id: str | None = None,
        now: datetime | None = None,
    ) -> ToolIntegrationJob | None: ...

    def heartbeat_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> ToolIntegrationJob | None: ...

    def count_jobs_by_status(self, *, organization_id: str | None = None) -> dict[str, int]: ...

    def list_recent_jobs(
        self,
        *,
        organization_id: str | None = None,
        limit: int = 8,
    ) -> list[ToolIntegrationJob]: ...

    def list_jobs(
        self,
        *,
        organization_id: str | None = None,
        status: str | None = None,
        conversation_id: str | None = None,
        limit: int = 50,
        include_terminal: bool = True,
    ) -> list[ToolIntegrationJob]: ...

    def list_stuck_jobs(
        self,
        *,
        stale_before: datetime,
        organization_id: str | None = None,
        limit: int = 50,
    ) -> list[ToolIntegrationJob]: ...


class InMemoryToolIntegrationJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, ToolIntegrationJob] = {}
        self._job_ids_by_invocation: dict[str, str] = {}

    def create_or_get_for_invocation(self, job: ToolIntegrationJob) -> ToolIntegrationJob:
        existing_id = self._job_ids_by_invocation.get(job.invocation_id)
        if existing_id is not None:
            existing = self._jobs.get(existing_id)
            if existing is not None:
                return existing.model_copy(deep=True)
        self._jobs[job.job_id] = job.model_copy(deep=True)
        self._job_ids_by_invocation[job.invocation_id] = job.job_id
        return job.model_copy(deep=True)

    def load(self, job_id: str, *, organization_id: str | None = None) -> ToolIntegrationJob | None:
        item = self._jobs.get(job_id)
        if item is None:
            return None
        if organization_id is not None and item.organization_id != organization_id:
            return None
        return item.model_copy(deep=True)

    def load_by_invocation(
        self,
        invocation_id: str,
        *,
        organization_id: str | None = None,
    ) -> ToolIntegrationJob | None:
        job_id = self._job_ids_by_invocation.get(invocation_id)
        if job_id is None:
            return None
        return self.load(job_id, organization_id=organization_id)

    def load_by_callback_correlation_id(
        self,
        callback_correlation_id: str,
        *,
        organization_id: str | None = None,
    ) -> ToolIntegrationJob | None:
        for job in self._jobs.values():
            if job.callback_correlation_id != callback_correlation_id:
                continue
            if organization_id is not None and job.organization_id != organization_id:
                continue
            return job.model_copy(deep=True)
        return None

    def save(self, job: ToolIntegrationJob) -> None:
        self._jobs[job.job_id] = job.model_copy(deep=True)
        self._job_ids_by_invocation[job.invocation_id] = job.job_id

    def claim_next_job(
        self,
        *,
        worker_id: str,
        lease_expires_at: datetime,
        organization_id: str | None = None,
        now: datetime | None = None,
    ) -> ToolIntegrationJob | None:
        effective_now = now or _utcnow()
        eligible = [
            job
            for job in self._jobs.values()
            if (organization_id is None or job.organization_id == organization_id)
            and _is_claimable(job, now=effective_now)
        ]
        if not eligible:
            return None
        chosen = min(eligible, key=lambda job: (job.submitted_at, job.job_id))
        updated_metadata = dict(chosen.metadata)
        updated_metadata["claimed_from_status"] = chosen.status
        claimed = chosen.model_copy(
            update={
                "status": "running",
                "worker_id": worker_id,
                "lease_expires_at": lease_expires_at,
                "attempt_count": chosen.attempt_count + 1,
                "started_at": chosen.started_at or effective_now,
                "last_progress_at": effective_now,
                "metadata": updated_metadata,
            }
        )
        self.save(claimed)
        return claimed.model_copy(deep=True)

    def heartbeat_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> ToolIntegrationJob | None:
        job = self._jobs.get(job_id)
        if job is None or job.worker_id != worker_id or job.status != "running":
            return None
        updated = job.model_copy(
            update={
                "lease_expires_at": lease_expires_at,
                "last_progress_at": _utcnow(),
            }
        )
        self.save(updated)
        return updated.model_copy(deep=True)

    def count_jobs_by_status(self, *, organization_id: str | None = None) -> dict[str, int]:
        counts: dict[str, int] = {}
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
    ) -> list[ToolIntegrationJob]:
        items = [
            job
            for job in self._jobs.values()
            if organization_id is None or job.organization_id == organization_id
        ]
        items.sort(key=lambda job: (job.submitted_at, job.job_id), reverse=True)
        return [job.model_copy(deep=True) for job in items[: max(1, limit)]]

    def list_jobs(
        self,
        *,
        organization_id: str | None = None,
        status: str | None = None,
        conversation_id: str | None = None,
        limit: int = 50,
        include_terminal: bool = True,
    ) -> list[ToolIntegrationJob]:
        terminal_statuses = {"completed", "failed", "cancelled", "dead_lettered"}
        items = []
        for job in self._jobs.values():
            if organization_id is not None and job.organization_id != organization_id:
                continue
            if status is not None and job.status != status:
                continue
            if not include_terminal and job.status in terminal_statuses:
                continue
            payload_call = dict(job.payload.get("tool_call") or {})
            caller = dict(payload_call.get("caller") or {})
            if conversation_id is not None and caller.get("conversation_id") != conversation_id:
                continue
            items.append(job)
        items.sort(key=lambda job: (job.submitted_at, job.job_id), reverse=True)
        return [job.model_copy(deep=True) for job in items[: max(1, limit)]]

    def list_stuck_jobs(
        self,
        *,
        stale_before: datetime,
        organization_id: str | None = None,
        limit: int = 50,
    ) -> list[ToolIntegrationJob]:
        candidates: list[ToolIntegrationJob] = []
        for job in self._jobs.values():
            if organization_id is not None and job.organization_id != organization_id:
                continue
            if job.status in {"completed", "failed", "cancelled", "dead_lettered"}:
                continue
            last_progress = job.last_progress_at or job.submitted_at
            if last_progress <= stale_before:
                candidates.append(job)
        candidates.sort(key=lambda job: (job.last_progress_at or job.submitted_at, job.job_id))
        return [job.model_copy(deep=True) for job in candidates[: max(1, limit)]]


class SQLAlchemyToolIntegrationJobStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_or_get_for_invocation(self, job: ToolIntegrationJob) -> ToolIntegrationJob:
        with self._session_factory() as session:
            existing = (
                session.execute(
                    select(ToolIntegrationJobRecord).where(
                        ToolIntegrationJobRecord.invocation_id == job.invocation_id
                    )
                )
                .scalars()
                .first()
            )
            if existing is not None:
                return _record_to_job(existing)
            record = _job_to_record(job)
            session.add(record)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = (
                    session.execute(
                        select(ToolIntegrationJobRecord).where(
                            ToolIntegrationJobRecord.invocation_id == job.invocation_id
                        )
                    )
                    .scalars()
                    .first()
                )
                if existing is None:
                    raise
                return _record_to_job(existing)
            session.refresh(record)
            return _record_to_job(record)

    def load(self, job_id: str, *, organization_id: str | None = None) -> ToolIntegrationJob | None:
        with self._session_factory() as session:
            record = session.get(ToolIntegrationJobRecord, job_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            return _record_to_job(record)

    def load_by_invocation(
        self,
        invocation_id: str,
        *,
        organization_id: str | None = None,
    ) -> ToolIntegrationJob | None:
        statement = select(ToolIntegrationJobRecord).where(
            ToolIntegrationJobRecord.invocation_id == invocation_id
        )
        if organization_id is not None:
            statement = statement.where(ToolIntegrationJobRecord.organization_id == organization_id)
        with self._session_factory() as session:
            record = session.execute(statement).scalars().first()
            return _record_to_job(record) if record is not None else None

    def load_by_callback_correlation_id(
        self,
        callback_correlation_id: str,
        *,
        organization_id: str | None = None,
    ) -> ToolIntegrationJob | None:
        statement = select(ToolIntegrationJobRecord).where(
            ToolIntegrationJobRecord.callback_correlation_id == callback_correlation_id
        )
        if organization_id is not None:
            statement = statement.where(ToolIntegrationJobRecord.organization_id == organization_id)
        with self._session_factory() as session:
            record = session.execute(statement).scalars().first()
            return _record_to_job(record) if record is not None else None

    def save(self, job: ToolIntegrationJob) -> None:
        with self._session_factory() as session:
            record = session.get(ToolIntegrationJobRecord, job.job_id)
            if record is None:
                session.add(_job_to_record(job))
            else:
                _update_job_record(record, job)
            session.commit()

    def claim_next_job(
        self,
        *,
        worker_id: str,
        lease_expires_at: datetime,
        organization_id: str | None = None,
        now: datetime | None = None,
    ) -> ToolIntegrationJob | None:
        effective_now = now or _utcnow()
        eligible = _claimable_record_predicate(effective_now)
        statement = (
            select(ToolIntegrationJobRecord)
            .where(eligible)
            .order_by(ToolIntegrationJobRecord.submitted_at.asc(), ToolIntegrationJobRecord.job_id.asc())
        )
        if organization_id is not None:
            statement = statement.where(ToolIntegrationJobRecord.organization_id == organization_id)
        with self._session_factory() as session:
            record = session.execute(statement).scalars().first()
            if record is None:
                return None
            previous_status = record.status
            updated_metadata = dict(record.metadata_json or {})
            updated_metadata["claimed_from_status"] = previous_status
            record.status = "running"
            record.worker_id = worker_id
            record.lease_expires_at = lease_expires_at
            record.attempt_count = int(record.attempt_count or 0) + 1
            if record.started_at is None:
                record.started_at = effective_now
            record.last_progress_at = effective_now
            record.metadata_json = updated_metadata
            session.commit()
            session.refresh(record)
            return _record_to_job(record)

    def heartbeat_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_expires_at: datetime,
    ) -> ToolIntegrationJob | None:
        with self._session_factory() as session:
            record = session.get(ToolIntegrationJobRecord, job_id)
            if record is None or record.worker_id != worker_id or record.status != "running":
                return None
            record.lease_expires_at = lease_expires_at
            record.last_progress_at = _utcnow()
            session.commit()
            session.refresh(record)
            return _record_to_job(record)

    def count_jobs_by_status(self, *, organization_id: str | None = None) -> dict[str, int]:
        statement = select(ToolIntegrationJobRecord.status)
        if organization_id is not None:
            statement = statement.where(ToolIntegrationJobRecord.organization_id == organization_id)
        with self._session_factory() as session:
            rows = session.execute(statement).all()
        counts: dict[str, int] = {}
        for (status,) in rows:
            counts[str(status)] = counts.get(str(status), 0) + 1
        return counts

    def list_recent_jobs(
        self,
        *,
        organization_id: str | None = None,
        limit: int = 8,
    ) -> list[ToolIntegrationJob]:
        statement = (
            select(ToolIntegrationJobRecord)
            .order_by(ToolIntegrationJobRecord.submitted_at.desc(), ToolIntegrationJobRecord.job_id.desc())
            .limit(max(1, limit))
        )
        if organization_id is not None:
            statement = statement.where(ToolIntegrationJobRecord.organization_id == organization_id)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_job(record) for record in records]

    def list_jobs(
        self,
        *,
        organization_id: str | None = None,
        status: str | None = None,
        conversation_id: str | None = None,
        limit: int = 50,
        include_terminal: bool = True,
    ) -> list[ToolIntegrationJob]:
        statement = select(ToolIntegrationJobRecord)
        if organization_id is not None:
            statement = statement.where(ToolIntegrationJobRecord.organization_id == organization_id)
        if status is not None:
            statement = statement.where(ToolIntegrationJobRecord.status == status)
        if not include_terminal:
            statement = statement.where(
                ToolIntegrationJobRecord.status.not_in(("completed", "failed", "cancelled", "dead_lettered"))
            )
        if conversation_id is not None:
            statement = statement.where(ToolIntegrationJobRecord.payload_json["tool_call"]["caller"]["conversation_id"].astext == conversation_id)
        statement = statement.order_by(
            ToolIntegrationJobRecord.submitted_at.desc(),
            ToolIntegrationJobRecord.job_id.desc(),
        ).limit(max(1, limit))
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_job(record) for record in records]

    def list_stuck_jobs(
        self,
        *,
        stale_before: datetime,
        organization_id: str | None = None,
        limit: int = 50,
    ) -> list[ToolIntegrationJob]:
        statement = select(ToolIntegrationJobRecord).where(
            ToolIntegrationJobRecord.status.not_in(("completed", "failed", "cancelled", "dead_lettered")),
            or_(
                ToolIntegrationJobRecord.last_progress_at <= stale_before,
                and_(
                    ToolIntegrationJobRecord.last_progress_at.is_(None),
                    ToolIntegrationJobRecord.submitted_at <= stale_before,
                ),
            ),
        )
        if organization_id is not None:
            statement = statement.where(ToolIntegrationJobRecord.organization_id == organization_id)
        statement = statement.order_by(
            ToolIntegrationJobRecord.last_progress_at.asc().nullsfirst(),
            ToolIntegrationJobRecord.submitted_at.asc(),
            ToolIntegrationJobRecord.job_id.asc(),
        ).limit(max(1, limit))
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_job(record) for record in records]


def _is_claimable(job: ToolIntegrationJob, *, now: datetime) -> bool:
    if job.status == "queued":
        return True
    if job.status == "retry_scheduled":
        return job.next_retry_at is None or job.next_retry_at <= now
    if job.status == "waiting_poll":
        return job.next_poll_at is not None and job.next_poll_at <= now
    if job.status == "running":
        return job.lease_expires_at is not None and job.lease_expires_at <= now
    return False


def _claimable_record_predicate(now: datetime):
    return or_(
        ToolIntegrationJobRecord.status == "queued",
        and_(
            ToolIntegrationJobRecord.status == "retry_scheduled",
            or_(
                ToolIntegrationJobRecord.next_retry_at.is_(None),
                ToolIntegrationJobRecord.next_retry_at <= now,
            ),
        ),
        and_(
            ToolIntegrationJobRecord.status == "waiting_poll",
            ToolIntegrationJobRecord.next_poll_at.is_not(None),
            ToolIntegrationJobRecord.next_poll_at <= now,
        ),
        and_(
            ToolIntegrationJobRecord.status == "running",
            ToolIntegrationJobRecord.lease_expires_at.is_not(None),
            ToolIntegrationJobRecord.lease_expires_at <= now,
        ),
    )


def _job_to_record(job: ToolIntegrationJob) -> ToolIntegrationJobRecord:
    return ToolIntegrationJobRecord(
        job_id=job.job_id,
        organization_id=job.organization_id,
        invocation_id=job.invocation_id,
        tool_ref=job.tool_ref,
        executor_kind=job.executor_kind,
        resolution_mode=job.resolution_mode,
        status=job.status,
        queue_name=job.queue_name,
        worker_id=job.worker_id,
        lease_expires_at=job.lease_expires_at,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        dedupe_key=job.dedupe_key,
        external_job_id=job.external_job_id,
        callback_correlation_id=job.callback_correlation_id,
        payload_json=deepcopy(job.payload),
        result_json=deepcopy(job.result),
        error=job.error,
        submitted_at=job.submitted_at,
        started_at=job.started_at,
        last_progress_at=job.last_progress_at,
        next_poll_at=job.next_poll_at,
        next_retry_at=job.next_retry_at,
        finished_at=job.finished_at,
        metadata_json=deepcopy(job.metadata),
    )


def _update_job_record(record: ToolIntegrationJobRecord, job: ToolIntegrationJob) -> None:
    record.organization_id = job.organization_id
    record.invocation_id = job.invocation_id
    record.tool_ref = job.tool_ref
    record.executor_kind = job.executor_kind
    record.resolution_mode = job.resolution_mode
    record.status = job.status
    record.queue_name = job.queue_name
    record.worker_id = job.worker_id
    record.lease_expires_at = job.lease_expires_at
    record.attempt_count = job.attempt_count
    record.max_attempts = job.max_attempts
    record.dedupe_key = job.dedupe_key
    record.external_job_id = job.external_job_id
    record.callback_correlation_id = job.callback_correlation_id
    record.payload_json = deepcopy(job.payload)
    record.result_json = deepcopy(job.result)
    record.error = job.error
    record.submitted_at = job.submitted_at
    record.started_at = job.started_at
    record.last_progress_at = job.last_progress_at
    record.next_poll_at = job.next_poll_at
    record.next_retry_at = job.next_retry_at
    record.finished_at = job.finished_at
    record.metadata_json = deepcopy(job.metadata)


def _record_to_job(record: ToolIntegrationJobRecord) -> ToolIntegrationJob:
    return ToolIntegrationJob.model_validate(
        {
            "job_id": record.job_id,
            "organization_id": record.organization_id,
            "invocation_id": record.invocation_id,
            "tool_ref": record.tool_ref,
            "executor_kind": record.executor_kind,
            "resolution_mode": record.resolution_mode,
            "status": record.status,
            "queue_name": record.queue_name,
            "worker_id": record.worker_id,
            "lease_expires_at": record.lease_expires_at,
            "attempt_count": record.attempt_count,
            "max_attempts": record.max_attempts,
            "dedupe_key": record.dedupe_key,
            "external_job_id": record.external_job_id,
            "callback_correlation_id": record.callback_correlation_id,
            "payload": deepcopy(record.payload_json or {}),
            "result": deepcopy(record.result_json) if record.result_json is not None else None,
            "error": record.error,
            "submitted_at": record.submitted_at,
            "started_at": record.started_at,
            "last_progress_at": record.last_progress_at,
            "next_poll_at": record.next_poll_at,
            "next_retry_at": record.next_retry_at,
            "finished_at": record.finished_at,
            "metadata": deepcopy(record.metadata_json or {}),
        }
    )
