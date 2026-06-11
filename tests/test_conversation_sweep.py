"""Tests for ConversationSweep (Issue 1 — stale conversation sweep)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from ruhu.conversation_sweep import ConversationSweep
from ruhu.db import build_session_factory
from ruhu.db_models import ConversationRecord


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _insert_conversation(
    session_factory,
    *,
    status: str = "active",
    outcome: str | None = None,
    updated_at: datetime | None = None,
    ended_at: datetime | None = None,
    conversation_id: str | None = None,
) -> str:
    cid = conversation_id or f"conv-{uuid4().hex[:8]}"
    now = _utcnow()
    with session_factory.begin() as session:
        session.add(
            ConversationRecord(
                conversation_id=cid,
                agent_id="test_agent",
                agent_version_id="v1",
                step_id="start",
                status=status,
                outcome=outcome,
                started_at=now - timedelta(hours=2),
                ended_at=ended_at,
                created_at=now - timedelta(hours=2),
                updated_at=updated_at or (now - timedelta(hours=2)),
                metadata_json={},
                facts_json={},
            )
        )
    return cid


def _load_conversation(session_factory, conversation_id: str) -> ConversationRecord | None:
    with session_factory() as session:
        return session.get(ConversationRecord, conversation_id)


# ── process_once: core sweep behaviour ────────────────────────────────────────

def test_sweep_marks_stale_active_conversation_abandoned(postgres_database_url_factory) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = ConversationSweep(
        session_factory=sf,
        idle_timeout_seconds=600,  # 10-minute timeout
        batch_size=100,
    )

    # Conversation that went idle 2 hours ago → should be swept
    stale_id = _insert_conversation(sf, updated_at=_utcnow() - timedelta(hours=2))

    summary = worker.process_once()

    assert summary.abandoned_count == 1
    assert summary.error is None

    record = _load_conversation(sf, stale_id)
    assert record.status == "ended"
    assert record.outcome == "abandoned"
    assert record.ended_at is not None


def test_sweep_does_not_touch_recent_conversation(postgres_database_url_factory) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = ConversationSweep(
        session_factory=sf,
        idle_timeout_seconds=3600,  # 1-hour timeout
        batch_size=100,
    )

    # Conversation updated 5 minutes ago — well within the 1-hour window
    recent_id = _insert_conversation(sf, updated_at=_utcnow() - timedelta(minutes=5))

    summary = worker.process_once()

    assert summary.abandoned_count == 0
    record = _load_conversation(sf, recent_id)
    assert record.status == "active"
    assert record.outcome is None


def test_sweep_does_not_touch_already_ended_conversation(postgres_database_url_factory) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = ConversationSweep(
        session_factory=sf,
        idle_timeout_seconds=60,
        batch_size=100,
    )

    ended_id = _insert_conversation(
        sf,
        status="ended",
        outcome="resolved",
        ended_at=_utcnow() - timedelta(hours=3),
        updated_at=_utcnow() - timedelta(hours=3),
    )

    summary = worker.process_once()

    assert summary.abandoned_count == 0
    record = _load_conversation(sf, ended_id)
    assert record.outcome == "resolved"  # unchanged


def test_sweep_does_not_touch_active_conversation_with_existing_outcome(
    postgres_database_url_factory,
) -> None:
    """status='active' + outcome already set should not be double-swept."""
    sf = build_session_factory(postgres_database_url_factory())
    worker = ConversationSweep(
        session_factory=sf,
        idle_timeout_seconds=60,
        batch_size=100,
    )

    # Unusual state: active but outcome already populated — sweep must not overwrite
    _insert_conversation(
        sf,
        status="active",
        outcome="resolved",
        updated_at=_utcnow() - timedelta(hours=5),
    )

    summary = worker.process_once()
    assert summary.abandoned_count == 0


def test_sweep_respects_batch_size(postgres_database_url_factory) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = ConversationSweep(
        session_factory=sf,
        idle_timeout_seconds=60,
        batch_size=2,  # only 2 per pass
    )

    stale_ids = [
        _insert_conversation(sf, updated_at=_utcnow() - timedelta(hours=3))
        for _ in range(5)
    ]

    summary = worker.process_once()

    assert summary.abandoned_count == 2  # capped by batch_size

    # Remaining 3 are still active
    still_active = [
        cid for cid in stale_ids
        if _load_conversation(sf, cid).status == "active"
    ]
    assert len(still_active) == 3


def test_sweep_processes_multiple_conversations_in_one_pass(
    postgres_database_url_factory,
) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = ConversationSweep(
        session_factory=sf,
        idle_timeout_seconds=3600,  # 1-hour timeout
        batch_size=100,
    )

    stale_ids = [
        _insert_conversation(sf, updated_at=_utcnow() - timedelta(hours=2))
        for _ in range(4)
    ]
    # One recent conversation (10 seconds ago, well within 1-hour window) — should be untouched
    recent_id = _insert_conversation(sf, updated_at=_utcnow() - timedelta(seconds=10))

    summary = worker.process_once()

    assert summary.abandoned_count == 4
    for cid in stale_ids:
        assert _load_conversation(sf, cid).outcome == "abandoned"
    assert _load_conversation(sf, recent_id).status == "active"


# ── summary model ─────────────────────────────────────────────────────────────

def test_sweep_summary_model_dump(postgres_database_url_factory) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = ConversationSweep(session_factory=sf, idle_timeout_seconds=60)
    _insert_conversation(sf, updated_at=_utcnow() - timedelta(hours=3))

    summary = worker.process_once()
    d = summary.model_dump()

    assert d["abandoned_count"] == 1
    assert d["error"] is None


def test_sweep_summary_returns_zero_when_nothing_stale(postgres_database_url_factory) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    worker = ConversationSweep(session_factory=sf, idle_timeout_seconds=3600)

    summary = worker.process_once()

    assert summary.abandoned_count == 0
    assert summary.error is None



# ── jobs-runtime integration (RP-2.2) ─────────────────────────────────────────

def test_sweep_runs_as_recurring_tick_on_job_runtime(postgres_database_url_factory) -> None:
    """End-to-end: schedule -> tick job -> sweep handler -> conversation abandoned."""
    from ruhu.jobs import JobRuntime, SQLAlchemyJobStore
    from ruhu.runtime_config import RuntimeSettings
    from ruhu.worker import build_handler_registry

    sf = build_session_factory(postgres_database_url_factory())
    stale_id = _insert_conversation(sf, updated_at=_utcnow() - timedelta(hours=3))

    settings = RuntimeSettings(
        conversation_sweep_worker_enabled=True,
        conversation_sweep_interval_seconds=60.0,
        conversation_sweep_idle_timeout_seconds=3600.0,
        conversation_sweep_batch_size=10,
    )
    registry, schedules = build_handler_registry(session_factory=sf, settings=settings)
    assert [s.job_type for s in schedules] == ["conversation_sweep.tick"]

    runtime = JobRuntime(
        SQLAlchemyJobStore(sf), registry, worker_id="w-test", schedules=schedules
    )
    processed = runtime.run_once()

    assert processed == 1
    record = _load_conversation(sf, stale_id)
    assert record.status == "ended"
    assert record.outcome == "abandoned"


def test_sweep_tick_status_visible_from_jobs_table(postgres_database_url_factory) -> None:
    from ruhu.conversation_sweep import SWEEP_JOB_TYPE
    from ruhu.jobs import JobRuntime, SQLAlchemyJobStore, recurring_tick_status
    from ruhu.runtime_config import RuntimeSettings
    from ruhu.worker import build_handler_registry

    sf = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyJobStore(sf)
    settings = RuntimeSettings(
        conversation_sweep_worker_enabled=True,
        conversation_sweep_interval_seconds=60.0,
    )
    registry, schedules = build_handler_registry(session_factory=sf, settings=settings)

    before = recurring_tick_status(store, SWEEP_JOB_TYPE)
    assert before.scheduled is False and before.last_tick_at is None

    JobRuntime(store, registry, worker_id="w-test", schedules=schedules).run_once()

    after = recurring_tick_status(store, SWEEP_JOB_TYPE)
    assert after.last_tick_status == "succeeded"
    assert after.last_tick_at is not None
    assert after.last_error is None


def test_sweep_disabled_registers_nothing(postgres_database_url_factory) -> None:
    from ruhu.runtime_config import RuntimeSettings
    from ruhu.worker import build_handler_registry

    sf = build_session_factory(postgres_database_url_factory())
    registry, schedules = build_handler_registry(
        session_factory=sf,
        settings=RuntimeSettings(conversation_sweep_worker_enabled=False),
    )
    assert registry.job_types == []
    assert schedules == []
