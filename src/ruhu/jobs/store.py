from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Protocol, Sequence

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .models import Job
from .policy import RetryPolicy, next_retry_at
from .sqlalchemy_models import JobRecord


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobStore(Protocol):
    def enqueue(self, job: Job, *, session: Session | None = None) -> Job: ...

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: float = 60.0,
        job_types: Sequence[str] | None = None,
        now: datetime | None = None,
    ) -> Job | None: ...

    def heartbeat(self, job_id: str, *, worker_id: str, lease_seconds: float = 60.0) -> Job | None: ...

    def complete(self, job_id: str, *, worker_id: str) -> Job | None: ...

    def fail(
        self,
        job_id: str,
        *,
        worker_id: str,
        error: str,
        retryable: bool = True,
        policy: RetryPolicy | None = None,
        now: datetime | None = None,
    ) -> Job | None: ...

    def load(self, job_id: str) -> Job | None: ...

    def has_job(self, job_type: str, dedupe_key: str) -> bool: ...

    def list_jobs(
        self,
        *,
        job_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
        newest_first: bool = False,
    ) -> list[Job]: ...


def _apply_failure(job: Job, *, error: str, retryable: bool, policy: RetryPolicy, now: datetime) -> Job:
    job.last_error = error
    job.worker_id = None
    job.lease_expires_at = None
    job.updated_at = now
    effective_policy = RetryPolicy(
        max_attempts=job.max_attempts,
        base_delay_seconds=policy.base_delay_seconds,
        max_delay_seconds=policy.max_delay_seconds,
    )
    retry_at = next_retry_at(job.attempt_count, now=now, policy=effective_policy) if retryable else None
    if retry_at is None:
        job.status = "dead"
        job.finished_at = now
    else:
        job.status = "queued"
        job.run_at = retry_at
    return job


class InMemoryJobStore:
    """Dev/test implementation with the same claim/lease/retry semantics."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def enqueue(self, job: Job, *, session: Session | None = None) -> Job:
        with self._lock:
            if job.dedupe_key is not None:
                # Dedupe only against active jobs — completed/dead jobs free
                # the key (mirrors the partial unique index in Postgres).
                for existing in self._jobs.values():
                    if (
                        existing.job_type == job.job_type
                        and existing.dedupe_key == job.dedupe_key
                        and existing.status in ("queued", "running")
                    ):
                        return existing.model_copy(deep=True)
            stored = job.model_copy(deep=True)
            self._jobs[stored.job_id] = stored
            return stored.model_copy(deep=True)

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: float = 60.0,
        job_types: Sequence[str] | None = None,
        now: datetime | None = None,
    ) -> Job | None:
        effective_now = now or _utcnow()
        with self._lock:
            candidates = [
                job
                for job in self._jobs.values()
                if (job_types is None or job.job_type in job_types)
                and (
                    (job.status == "queued" and job.run_at <= effective_now)
                    or (
                        job.status == "running"
                        and job.lease_expires_at is not None
                        and job.lease_expires_at <= effective_now
                    )
                )
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda job: (-job.priority, job.run_at, job.job_id))
            job = candidates[0]
            job.status = "running"
            job.worker_id = worker_id
            job.lease_expires_at = effective_now + timedelta(seconds=lease_seconds)
            job.attempt_count += 1
            job.updated_at = effective_now
            return job.model_copy(deep=True)

    def heartbeat(self, job_id: str, *, worker_id: str, lease_seconds: float = 60.0) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "running" or job.worker_id != worker_id:
                return None
            job.lease_expires_at = _utcnow() + timedelta(seconds=lease_seconds)
            return job.model_copy(deep=True)

    def complete(self, job_id: str, *, worker_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "running" or job.worker_id != worker_id:
                return None
            now = _utcnow()
            job.status = "succeeded"
            job.finished_at = now
            job.updated_at = now
            job.lease_expires_at = None
            return job.model_copy(deep=True)

    def fail(
        self,
        job_id: str,
        *,
        worker_id: str,
        error: str,
        retryable: bool = True,
        policy: RetryPolicy | None = None,
        now: datetime | None = None,
    ) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "running" or job.worker_id != worker_id:
                return None
            _apply_failure(
                job,
                error=error,
                retryable=retryable,
                policy=policy or RetryPolicy(),
                now=now or _utcnow(),
            )
            return job.model_copy(deep=True)

    def load(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    def has_job(self, job_type: str, dedupe_key: str) -> bool:
        with self._lock:
            return any(
                job.job_type == job_type and job.dedupe_key == dedupe_key
                for job in self._jobs.values()
            )

    def list_jobs(
        self,
        *,
        job_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
        newest_first: bool = False,
    ) -> list[Job]:
        with self._lock:
            items = [
                job.model_copy(deep=True)
                for job in self._jobs.values()
                if (job_type is None or job.job_type == job_type)
                and (status is None or job.status == status)
            ]
            items.sort(key=lambda job: job.created_at, reverse=newest_first)
            return items[:limit]


class SQLAlchemyJobStore:
    """Postgres-backed job store.

    ``enqueue`` accepts an open ``session`` so producers can insert the job in
    the same transaction as the state change that caused it (transactional
    outbox). ``claim_next`` uses ``FOR UPDATE SKIP LOCKED`` so any number of
    worker processes can poll the same table without contention; expired
    leases are reclaimable, which is how crashed workers' jobs recover.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def enqueue(self, job: Job, *, session: Session | None = None) -> Job:
        managed_session = session
        owns_session = managed_session is None
        if managed_session is None:
            managed_session = self._session_factory()
        try:
            managed_session.add(_job_to_record(job))
            try:
                managed_session.flush()
            except IntegrityError:
                if owns_session:
                    managed_session.rollback()
                else:
                    raise
                existing = self._load_by_dedupe(job.job_type, job.dedupe_key)
                if existing is not None:
                    return existing
                raise
            if owns_session:
                managed_session.commit()
            return job.model_copy(deep=True)
        finally:
            if owns_session:
                managed_session.close()

    def _load_by_dedupe(self, job_type: str, dedupe_key: str | None) -> Job | None:
        if dedupe_key is None:
            return None
        with self._session_factory() as session:
            record = session.execute(
                select(JobRecord)
                .where(
                    JobRecord.job_type == job_type,
                    JobRecord.dedupe_key == dedupe_key,
                    JobRecord.status.in_(["queued", "running"]),
                )
                .limit(1)
            ).scalars().one_or_none()
            return _record_to_job(record) if record is not None else None

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: float = 60.0,
        job_types: Sequence[str] | None = None,
        now: datetime | None = None,
    ) -> Job | None:
        effective_now = now or _utcnow()
        with self._session_factory() as session:
            statement = (
                select(JobRecord)
                .where(
                    or_(
                        and_(JobRecord.status == "queued", JobRecord.run_at <= effective_now),
                        and_(
                            JobRecord.status == "running",
                            JobRecord.lease_expires_at.is_not(None),
                            JobRecord.lease_expires_at <= effective_now,
                        ),
                    )
                )
                .order_by(JobRecord.priority.desc(), JobRecord.run_at.asc(), JobRecord.job_id.asc())
                .limit(1)
            )
            if job_types is not None:
                statement = statement.where(JobRecord.job_type.in_(list(job_types)))
            bind = session.get_bind()
            if bind is not None and bind.dialect.name == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            record = session.execute(statement).scalars().one_or_none()
            if record is None:
                return None
            record.status = "running"
            record.worker_id = worker_id
            record.lease_expires_at = effective_now + timedelta(seconds=lease_seconds)
            record.attempt_count = int(record.attempt_count or 0) + 1
            record.updated_at = effective_now
            session.commit()
            session.refresh(record)
            return _record_to_job(record)

    def heartbeat(self, job_id: str, *, worker_id: str, lease_seconds: float = 60.0) -> Job | None:
        with self._session_factory() as session:
            record = session.get(JobRecord, job_id)
            if record is None or record.status != "running" or record.worker_id != worker_id:
                return None
            record.lease_expires_at = _utcnow() + timedelta(seconds=lease_seconds)
            session.commit()
            session.refresh(record)
            return _record_to_job(record)

    def complete(self, job_id: str, *, worker_id: str) -> Job | None:
        with self._session_factory() as session:
            record = session.get(JobRecord, job_id)
            if record is None or record.status != "running" or record.worker_id != worker_id:
                return None
            now = _utcnow()
            record.status = "succeeded"
            record.finished_at = now
            record.updated_at = now
            record.lease_expires_at = None
            session.commit()
            session.refresh(record)
            return _record_to_job(record)

    def fail(
        self,
        job_id: str,
        *,
        worker_id: str,
        error: str,
        retryable: bool = True,
        policy: RetryPolicy | None = None,
        now: datetime | None = None,
    ) -> Job | None:
        with self._session_factory() as session:
            record = session.get(JobRecord, job_id)
            if record is None or record.status != "running" or record.worker_id != worker_id:
                return None
            job = _record_to_job(record)
            _apply_failure(
                job,
                error=error,
                retryable=retryable,
                policy=policy or RetryPolicy(),
                now=now or _utcnow(),
            )
            record.status = job.status
            record.run_at = job.run_at
            record.last_error = job.last_error
            record.worker_id = job.worker_id
            record.lease_expires_at = job.lease_expires_at
            record.updated_at = job.updated_at
            record.finished_at = job.finished_at
            session.commit()
            session.refresh(record)
            return _record_to_job(record)

    def load(self, job_id: str) -> Job | None:
        with self._session_factory() as session:
            record = session.get(JobRecord, job_id)
            return _record_to_job(record) if record is not None else None

    def has_job(self, job_type: str, dedupe_key: str) -> bool:
        with self._session_factory() as session:
            return (
                session.execute(
                    select(JobRecord.job_id)
                    .where(JobRecord.job_type == job_type, JobRecord.dedupe_key == dedupe_key)
                    .limit(1)
                ).scalar_one_or_none()
                is not None
            )

    def list_jobs(
        self,
        *,
        job_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
        newest_first: bool = False,
    ) -> list[Job]:
        order = JobRecord.created_at.desc() if newest_first else JobRecord.created_at.asc()
        statement = select(JobRecord).order_by(order).limit(limit)
        if job_type is not None:
            statement = statement.where(JobRecord.job_type == job_type)
        if status is not None:
            statement = statement.where(JobRecord.status == status)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
            return [_record_to_job(record) for record in records]


def _job_to_record(job: Job) -> JobRecord:
    return JobRecord(
        job_id=job.job_id,
        job_type=job.job_type,
        organization_id=job.organization_id,
        payload_json=dict(job.payload),
        status=job.status,
        priority=job.priority,
        run_at=job.run_at,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        lease_expires_at=job.lease_expires_at,
        worker_id=job.worker_id,
        last_error=job.last_error,
        dedupe_key=job.dedupe_key,
        created_at=job.created_at,
        updated_at=job.updated_at,
        finished_at=job.finished_at,
    )


def _record_to_job(record: JobRecord) -> Job:
    return Job(
        job_id=record.job_id,
        job_type=record.job_type,
        organization_id=record.organization_id,
        payload=dict(record.payload_json or {}),
        status=record.status,
        priority=record.priority,
        run_at=record.run_at,
        attempt_count=record.attempt_count,
        max_attempts=record.max_attempts,
        lease_expires_at=record.lease_expires_at,
        worker_id=record.worker_id,
        last_error=record.last_error,
        dedupe_key=record.dedupe_key,
        created_at=record.created_at,
        updated_at=record.updated_at,
        finished_at=record.finished_at,
    )
