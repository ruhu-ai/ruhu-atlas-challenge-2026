from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

JobStatus = Literal["queued", "running", "succeeded", "dead"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(BaseModel):
    """One unit of background work.

    ``dedupe_key`` makes enqueue idempotent per ``job_type``: a second enqueue
    with the same key returns the existing job instead of inserting. Failed
    attempts re-queue with backoff until ``max_attempts``, then move to
    ``dead`` (the dead-letter state — visible, never silently dropped).
    """

    job_id: str = Field(default_factory=lambda: str(uuid4()))
    job_type: str
    organization_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus = "queued"
    priority: int = 0
    run_at: datetime = Field(default_factory=_utcnow)
    attempt_count: int = 0
    max_attempts: int = 4
    lease_expires_at: datetime | None = None
    worker_id: str | None = None
    last_error: str | None = None
    dedupe_key: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None
