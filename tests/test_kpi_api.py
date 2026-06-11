from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from ruhu.api import build_default_app
from ruhu.db import build_session_factory
from ruhu.db_models import ConversationRecord, ProviderCostRecord, RealtimeEventRecord, ToolInvocationRecord, TurnTraceRecord
from ruhu.registry import SQLAlchemyAgentRegistry


def _seed_kpi_measurement_data(database_url: str) -> None:
    session_factory = build_session_factory(database_url)
    registry = SQLAlchemyAgentRegistry(session_factory)
    version_id = registry.resolve_version_id("sales", target="published")
    organization_id = "org-kpi-api"
    now = datetime.now(timezone.utc)

    resolved_conversations = [
        ("web_widget:resolved-1", now - timedelta(days=3, hours=2), now - timedelta(days=3, hours=1, minutes=40)),
        ("web_widget:resolved-2", now - timedelta(days=2, hours=4), now - timedelta(days=2, hours=3, minutes=20)),
    ]
    transferred_conversations = [
        ("web_widget:transfer-1", now - timedelta(days=1, hours=3), now - timedelta(days=1, hours=2, minutes=45)),
        ("web_widget:transfer-2", now - timedelta(hours=20), now - timedelta(hours=19, minutes=40)),
    ]

    with session_factory.begin() as session:
        for conversation_id, started_at, ended_at in resolved_conversations:
            session.add(
                ConversationRecord(
                    conversation_id=conversation_id,
                    organization_id=organization_id,
                    agent_id="sales",
                    agent_version_id=version_id,
                    mode="live",
                    status="ended",
                    step_id="demo_requested_done",
                    facts_json={},
                    metadata_json={},
                    processed_dedupe_keys_json=[],
                    last_event_sequence=1,
                    started_at=started_at,
                    ended_at=ended_at,
                    created_at=started_at,
                    updated_at=ended_at,
                )
            )
            session.add(
                RealtimeEventRecord(
                    event_id=str(uuid4()),
                    conversation_id=conversation_id,
                    realtime_session_id=None,
                    organization_id=organization_id,
                    conversation_sequence=1,
                    family="session",
                    name="started",
                    causation_id=None,
                    correlation_id=None,
                    actor_type="system",
                    actor_id=None,
                    visibility="surface",
                    audiences_json=["public_widget"],
                    projection_policy_json={},
                    payload_json={"channel": "web_widget"},
                    created_at=started_at,
                )
            )
            session.add(
                ProviderCostRecord(
                    cost_record_id=str(uuid4()),
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                    realtime_session_id=None,
                    turn_trace_id=None,
                    tool_invocation_id=None,
                    provider="meta",
                    cost_type="message_delivery",
                    amount_usd=0.12,
                    reference_key=f"cost-{conversation_id}",
                    metadata_json={},
                    occurred_at=ended_at,
                    created_at=ended_at,
                )
            )

        for conversation_id, started_at, handoff_at in transferred_conversations:
            session.add(
                ConversationRecord(
                    conversation_id=conversation_id,
                    organization_id=organization_id,
                    agent_id="sales",
                    agent_version_id=version_id,
                    mode="live",
                    status="active",
                    step_id="discover",
                    facts_json={},
                    metadata_json={},
                    processed_dedupe_keys_json=[],
                    last_event_sequence=2,
                    started_at=started_at,
                    ended_at=None,
                    created_at=started_at,
                    updated_at=handoff_at,
                )
            )
            session.add(
                RealtimeEventRecord(
                    event_id=str(uuid4()),
                    conversation_id=conversation_id,
                    realtime_session_id=None,
                    organization_id=organization_id,
                    conversation_sequence=1,
                    family="session",
                    name="started",
                    causation_id=None,
                    correlation_id=None,
                    actor_type="system",
                    actor_id=None,
                    visibility="surface",
                    audiences_json=["public_widget"],
                    projection_policy_json={},
                    payload_json={"channel": "web_widget"},
                    created_at=started_at,
                )
            )
            session.add(
                TurnTraceRecord(
                    trace_id=str(uuid4()),
                    conversation_id=conversation_id,
                    organization_id=organization_id,
                    turn_id=f"turn-{conversation_id}",
                    agent_id="sales",
                    agent_version_id=version_id,
                    step_before="discover",
                    step_after="discover",
                    semantic_events_json=[],
                    fact_updates_json=[],
                    chosen_action_json={"type": "handoff", "reason": "billing escalation"},
                    emitted_messages_json=[],
                    tool_calls_json=[],
                    latency_breakdown_ms_json={},
                    recorded_at=handoff_at,
                )
            )
            session.add(
                ToolInvocationRecord(
                    invocation_id=str(uuid4()),
                    organization_id=organization_id,
                    tool_ref="knowledge.lookup",
                    executor_kind="builtin",
                    status="failed",
                    caller_json={"conversation_id": conversation_id, "channel": "web_widget"},
                    args_json={"query": "billing refund"},
                    dedupe_key=None,
                    decision="allow",
                    decision_reason=None,
                    output_json={},
                    error="upstream lookup timeout",
                    latency_ms=1200,
                    metadata_json={},
                    created_at=handoff_at - timedelta(seconds=10),
                    updated_at=handoff_at - timedelta(seconds=5),
                )
            )
            session.add(
                ProviderCostRecord(
                    cost_record_id=str(uuid4()),
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                    realtime_session_id=None,
                    turn_trace_id=None,
                    tool_invocation_id=None,
                    provider="meta",
                    cost_type="message_delivery",
                    amount_usd=0.2,
                    reference_key=f"cost-{conversation_id}",
                    metadata_json={},
                    occurred_at=handoff_at,
                    created_at=handoff_at,
                )
            )


def test_kpi_api_exposes_measurement_refresh_and_review_flow(postgres_database_url_factory) -> None:
    async def run() -> None:
        database_url = postgres_database_url_factory()
        agent_root = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        app = build_default_app(
            agent_root=agent_root,
            database_url=database_url,
            interpreter_name="sales",
        )
        _seed_kpi_measurement_data(database_url)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            page = await client.get("/kpi")
            assert page.status_code == 200
            assert "KPI Goals Console" in page.text

            scope_response = await client.post(
                "/kpi/scopes",
                json={
                    "organization_id": "org-kpi-api",
                    "scope_kind": "channel",
                    "channel": "web_widget",
                    "display_name": "Website widget",
                },
            )
            assert scope_response.status_code == 200
            scope_id = scope_response.json()["scope_id"]

            support = await client.get(
                f"/kpi/scopes/{scope_id}/measurement-support",
                params={"organization_id": "org-kpi-api"},
            )
            assert support.status_code == 200
            support_payload = {item["metric_key"]: item for item in support.json()}
            assert support_payload["transfer_rate"]["supported"] is True
            assert support_payload["cost_per_conversation"]["supported"] is True

            baseline_observation = await client.post(
                "/kpi/observations",
                json={
                    "organization_id": "org-kpi-api",
                    "metric_key": "transfer_rate",
                    "scope_id": scope_id,
                    "value": 65.0,
                    "sample_size": 24,
                    "confidence": 0.8,
                    "period_start": (datetime.now(timezone.utc) - timedelta(days=45)).isoformat(),
                    "period_end": (datetime.now(timezone.utc) - timedelta(days=15)).isoformat(),
                    "observation_kind": "manual_entry",
                    "lookback_days": 30,
                    "source_summary": {"sources": ["manual_entry"]},
                },
            )
            assert baseline_observation.status_code == 200
            baseline_observation_id = baseline_observation.json()["observation_id"]

            goal_response = await client.post(
                "/kpi/goals",
                json={
                    "organization_id": "org-kpi-api",
                    "metric_key": "transfer_rate",
                    "scope_id": scope_id,
                    "name": "Reduce website escalations",
                    "target_value": 25.0,
                    "target_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
                },
            )
            assert goal_response.status_code == 200
            goal_id = goal_response.json()["goal_id"]

            refreshed_observation = await client.post(
                f"/kpi/scopes/{scope_id}/measurements/transfer_rate/refresh",
                json={"organization_id": "org-kpi-api"},
            )
            assert refreshed_observation.status_code == 200
            refreshed_payload = refreshed_observation.json()
            assert refreshed_payload["value"] == 100.0
            assert refreshed_payload["sample_size"] == 2
            refreshed_observation_id = refreshed_payload["observation_id"]

            evaluation = await client.post(
                f"/kpi/goals/{goal_id}/evaluate",
                json={"organization_id": "org-kpi-api"},
            )
            assert evaluation.status_code == 200
            assert evaluation.json()["status"] == "at_risk"

            insights = await client.post(
                f"/kpi/goals/{goal_id}/insights/generate",
                params={"organization_id": "org-kpi-api"},
            )
            assert insights.status_code == 200
            insight_payload = insights.json()
            assert any(item["blocker_kind"] == "transfer_escalation" for item in insight_payload)
            insight_id = insight_payload[0]["insight_id"]

            insight_update = await client.post(
                f"/kpi/insights/{insight_id}/status",
                json={"organization_id": "org-kpi-api", "status": "accepted"},
            )
            assert insight_update.status_code == 200
            assert insight_update.json()["status"] == "accepted"

            recommendations = await client.post(
                f"/kpi/goals/{goal_id}/recommendations/generate",
                json={"organization_id": "org-kpi-api"},
            )
            assert recommendations.status_code == 200
            recommendation_payload = recommendations.json()
            assert recommendation_payload
            assert all(item["category"] for item in recommendation_payload)
            recommendation_id = recommendation_payload[0]["recommendation_id"]

            recommendation_update = await client.post(
                f"/kpi/recommendations/{recommendation_id}/status",
                json={"organization_id": "org-kpi-api", "status": "approved"},
            )
            assert recommendation_update.status_code == 200
            assert recommendation_update.json()["status"] == "approved"

            preview_intent = await client.post(
                f"/kpi/recommendations/{recommendation_id}/execution-intents",
                json={"organization_id": "org-kpi-api", "execution_mode": "preview", "requested_via": "test"},
            )
            assert preview_intent.status_code == 200
            preview_intent_id = preview_intent.json()["execution_intent_id"]

            preview_result = await client.post(
                f"/kpi/execution-intents/{preview_intent_id}/preview",
                params={"organization_id": "org-kpi-api"},
            )
            assert preview_result.status_code == 200
            assert preview_result.json()["status"] in {"preview_succeeded", "preview_failed"}

            apply_intent = await client.post(
                f"/kpi/recommendations/{recommendation_id}/execution-intents",
                json={"organization_id": "org-kpi-api", "execution_mode": "apply", "requested_via": "test"},
            )
            assert apply_intent.status_code == 200
            apply_intent_id = apply_intent.json()["execution_intent_id"]

            apply_result = await client.post(
                f"/kpi/execution-intents/{apply_intent_id}/apply",
                params={"organization_id": "org-kpi-api"},
            )
            assert apply_result.status_code == 200
            assert apply_result.json()["status"] in {"apply_succeeded", "apply_failed"}

            experiment = await client.post(
                "/kpi/experiments",
                json={
                    "organization_id": "org-kpi-api",
                    "goal_id": goal_id,
                    "recommendation_id": recommendation_id,
                    "name": "Escalation canary",
                    "hypothesis": "A bounded workflow change reduces escalations.",
                    "primary_metric_key": "transfer_rate",
                    "scope_id": scope_id,
                },
            )
            assert experiment.status_code == 200
            experiment_id = experiment.json()["experiment_id"]

            experiment_status = await client.post(
                f"/kpi/experiments/{experiment_id}/status",
                json={"organization_id": "org-kpi-api", "status": "running"},
            )
            assert experiment_status.status_code == 200
            assert experiment_status.json()["status"] == "running"

            impact = await client.post(
                "/kpi/impact-assessments",
                json={
                    "organization_id": "org-kpi-api",
                    "metric_key": "transfer_rate",
                    "scope_id": scope_id,
                    "goal_id": goal_id,
                    "recommendation_id": recommendation_id,
                    "execution_intent_id": apply_intent_id,
                    "experiment_id": experiment_id,
                    "baseline_observation_id": baseline_observation_id,
                    "comparison_observation_id": refreshed_observation_id,
                    "attribution_mode": "sequential_rollout",
                    "attribution_confidence": "strong",
                    "notes": "Measured after the reviewed workflow changes were staged.",
                },
            )
            assert impact.status_code == 200
            assert impact.json()["observed_change"] == 35.0
            assert impact.json()["attributed_change"] == 35.0

            detail = await client.get(
                f"/kpi/goals/{goal_id}",
                params={"organization_id": "org-kpi-api"},
            )
            assert detail.status_code == 200
            detail_payload = detail.json()
            assert detail_payload["goal"]["goal_id"] == goal_id
            assert detail_payload["latest_observation"]["observation_id"] == refreshed_observation_id
            assert detail_payload["latest_evaluation"]["status"] == "at_risk"
            assert any(item["status"] == "accepted" for item in detail_payload["insights"])
            assert any(item["status"] in {"approved", "execution_failed", "executed"} for item in detail_payload["recommendations"])
            assert len(detail_payload["execution_intents"]) == 2
            assert len(detail_payload["execution_results"]) >= 1
            assert len(detail_payload["experiments"]) == 1
            assert len(detail_payload["impact_assessments"]) == 1

            summaries = await client.get("/kpi/goals", params={"organization_id": "org-kpi-api"})
            assert summaries.status_code == 200
            assert summaries.json()[0]["pending_recommendation_count"] >= 1

    asyncio.run(run())
