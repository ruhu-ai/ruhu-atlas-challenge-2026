"""Semantic-summary webhook dispatch on the jobs runtime (RP-2.2).

The thread-based dispatch worker is gone; the dispatcher runs as the
``semantic_summary_webhooks.tick`` recurring job in ``ruhu.worker``.
Dispatcher delivery/fanout/retry behavior is pinned by the wider intent-tags
suites; this module pins the worker-process wiring.
"""

from __future__ import annotations

from ruhu.db import build_session_factory
from ruhu.analytics_tagging.webhooks import WEBHOOK_DISPATCH_JOB_TYPE
from ruhu.runtime_config import RuntimeSettings
from ruhu.worker import build_handler_registry


def test_webhook_dispatch_registers_handler_and_schedule(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    settings = RuntimeSettings(
        semantic_summary_webhook_worker_enabled=True,
        semantic_summary_webhook_interval_seconds=30.0,
        semantic_summary_webhook_batch_size=25,
    )
    registry, schedules = build_handler_registry(
        session_factory=session_factory, settings=settings
    )
    assert WEBHOOK_DISPATCH_JOB_TYPE in registry.job_types
    by_type = {schedule.job_type: schedule for schedule in schedules}
    assert by_type[WEBHOOK_DISPATCH_JOB_TYPE].interval_seconds == 30.0


def test_webhook_dispatch_tick_runs_against_empty_outbox(postgres_database_url_factory) -> None:
    """The registered handler executes a full dispatcher cycle end-to-end."""
    from ruhu.jobs import JobRuntime, SQLAlchemyJobStore, recurring_tick_status

    session_factory = build_session_factory(postgres_database_url_factory())
    settings = RuntimeSettings(semantic_summary_webhook_worker_enabled=True)
    registry, schedules = build_handler_registry(
        session_factory=session_factory, settings=settings
    )
    store = SQLAlchemyJobStore(session_factory)
    runtime = JobRuntime(store, registry, worker_id="w-test", schedules=schedules)

    assert runtime.run_once() == 1

    status = recurring_tick_status(store, WEBHOOK_DISPATCH_JOB_TYPE)
    assert status.last_tick_status == "succeeded"
    assert status.last_error is None
