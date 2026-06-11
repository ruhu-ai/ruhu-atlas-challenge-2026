"""RP-2.2: ruhu.worker handler-registry composition.

Each migrated background worker must register its handler + recurring
schedule when enabled, and register nothing when disabled. End-to-end tick
behavior is pinned in each worker's own test module; this file pins the
composition so a migration can't silently drop out of the worker process.
"""

from __future__ import annotations

from ruhu.db import build_session_factory
from ruhu.runtime_config import RuntimeSettings
from ruhu.worker import build_handler_registry


def _registry(postgres_database_url_factory, **settings_overrides):
    database_url = postgres_database_url_factory()
    session_factory = build_session_factory(database_url)
    return build_handler_registry(
        session_factory=session_factory,
        settings=RuntimeSettings(
            **{
                "journey_runtime_worker_enabled": False,
                "tool_integration_worker_enabled": False,
                **settings_overrides,
            }
        ),
        database_url=database_url,
    )


def test_all_disabled_registers_nothing(postgres_database_url_factory) -> None:
    registry, schedules = _registry(postgres_database_url_factory)
    assert registry.job_types == []
    assert schedules == []


def test_enabled_workers_register_handler_and_schedule(postgres_database_url_factory) -> None:
    registry, schedules = _registry(
        postgres_database_url_factory,
        conversation_sweep_worker_enabled=True,
        attachments_retention_sweep_enabled=True,
        capture_audit_worker_enabled=True,
        semantic_summary_webhook_worker_enabled=True,
        tool_oauth_redirect_base_url="https://app.example.com/oauth",
        journey_runtime_worker_enabled=True,
        browser_task_worker_enabled=True,
        browser_task_worker_adapter="playwright",
    )
    expected = {
        "attachment_retention.tick",
        "browser_tasks.tick",
        "capture_audit.tick",
        "conversation_sweep.tick",
        "journey_runtime.tick",
        "oauth_token_refresh.tick",
        "semantic_summary_webhooks.tick",
    }
    assert set(registry.job_types) == expected
    assert {schedule.job_type for schedule in schedules} == expected
    # Every schedule pulls its interval from settings, not a hardcoded default.
    by_type = {schedule.job_type: schedule for schedule in schedules}
    defaults = RuntimeSettings()
    assert by_type["conversation_sweep.tick"].interval_seconds == defaults.conversation_sweep_interval_seconds
    assert (
        by_type["attachment_retention.tick"].interval_seconds
        == defaults.attachments_retention_sweep_interval_seconds
    )
    assert by_type["capture_audit.tick"].interval_seconds == defaults.capture_audit_worker_interval_seconds
    assert (
        by_type["semantic_summary_webhooks.tick"].interval_seconds
        == defaults.semantic_summary_webhook_interval_seconds
    )
    assert by_type["oauth_token_refresh.tick"].interval_seconds == 60.0
    assert (
        by_type["journey_runtime.tick"].interval_seconds
        == defaults.journey_runtime_poll_interval_seconds
    )
    assert (
        by_type["browser_tasks.tick"].interval_seconds
        == defaults.browser_task_worker_poll_interval_seconds
    )


def test_kernel_dependent_workers_register_handler_and_schedule(
    postgres_database_url_factory,
) -> None:
    """RP-3.2 step 4: view-ready, sentiment, and tool-integration compose one
    shared ``build_runtime`` and register their ticks."""
    registry, schedules = _registry(
        postgres_database_url_factory,
        attachments_view_ready_worker_enabled=True,
        sentiment_worker_enabled=True,
        sentiment_worker_llm_base_url="https://llm.example.test/v1",
        sentiment_worker_llm_api_key="test-key",
        tool_integration_worker_enabled=True,
    )
    expected = {"view_ready.tick", "sentiment.tick", "tool_integration.tick"}
    assert set(registry.job_types) == expected
    by_type = {schedule.job_type: schedule for schedule in schedules}
    assert set(by_type) == expected
    defaults = RuntimeSettings()
    assert (
        by_type["view_ready.tick"].interval_seconds
        == defaults.attachments_view_ready_worker_interval_seconds
    )
    assert (
        by_type["sentiment.tick"].interval_seconds
        == defaults.sentiment_worker_interval_seconds
    )
    assert (
        by_type["tool_integration.tick"].interval_seconds
        == defaults.tool_integration_worker_poll_interval_seconds
    )


def test_sentiment_tick_requires_llm_settings(postgres_database_url_factory) -> None:
    """Enabling sentiment without its LLM credentials registers nothing —
    mirrors api.py's construction guard."""
    registry, schedules = _registry(
        postgres_database_url_factory,
        sentiment_worker_enabled=True,
    )
    assert registry.job_types == []
    assert schedules == []
