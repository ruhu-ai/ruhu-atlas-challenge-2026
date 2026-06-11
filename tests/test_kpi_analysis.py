from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from ruhu.db import build_session_factory
from ruhu.db_models import ConversationRecord, ProviderCostRecord, ToolInvocationRecord, TurnTraceRecord
from ruhu.analytics_tagging.sqlalchemy_models import (
    IntentTagAssignmentRecord,
    IntentTagConversationSummaryRecord,
    TagDefinitionRecord,
)
from ruhu.kpi.models import utc_now
from ruhu.kpi.runtime import build_kpi_runtime
from ruhu.registry import SQLAlchemyAgentRegistry


def test_kpi_insight_analyzer_surfaces_workflow_hotspots_and_tool_failures(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    registry = SQLAlchemyAgentRegistry(session_factory)
    registry.bootstrap_from_directory(Path(__file__).resolve().parent / "_fixtures" / "data" / "agents")
    runtime = build_kpi_runtime(session_factory=session_factory, agent_registry=registry)

    demo_version_id = registry.resolve_version_id("sales", target="published")
    support_version_id = registry.resolve_version_id("support_triage", target="published")
    organization_id = "org-kpi-analysis"
    now = datetime.now(timezone.utc)

    with session_factory.begin() as session:
        for index in range(3):
            conversation_id = f"transfer-{index}"
            started_at = now - timedelta(days=5 - index, hours=2)
            ended_at = started_at + timedelta(minutes=18 + index)
            session.add(
                ConversationRecord(
                    conversation_id=conversation_id,
                    organization_id=organization_id,
                    agent_id="sales",
                    agent_version_id=demo_version_id,
                    mode="live",
                    channel="web_widget",
                    status="ended",
                    outcome="transferred",
                    step_id="discover",
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
                TurnTraceRecord(
                    trace_id=str(uuid4()),
                    conversation_id=conversation_id,
                    organization_id=organization_id,
                    turn_id=f"turn-{conversation_id}",
                    agent_id="sales",
                    agent_version_id=demo_version_id,
                    step_before="discover",
                    step_after="discover",
                    semantic_events_json=[],
                    fact_updates_json=[],
                    chosen_action_json={"type": "handoff", "reason": "escalation"},
                    emitted_messages_json=[],
                    tool_calls_json=[],
                    latency_breakdown_ms_json={},
                    recorded_at=ended_at,
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
                    error="timeout",
                    latency_ms=1100,
                    metadata_json={},
                    created_at=ended_at - timedelta(minutes=1),
                    updated_at=ended_at - timedelta(seconds=20),
                )
            )

        resolved_started = now - timedelta(days=2, hours=1)
        resolved_ended = resolved_started + timedelta(minutes=7)
        session.add(
            ConversationRecord(
                conversation_id="resolved-reference",
                organization_id=organization_id,
                agent_id="support_triage",
                agent_version_id=support_version_id,
                mode="live",
                channel="web_widget",
                status="ended",
                outcome="resolved",
                step_id="handoff_support",
                facts_json={},
                metadata_json={},
                processed_dedupe_keys_json=[],
                last_event_sequence=1,
                started_at=resolved_started,
                ended_at=resolved_ended,
                created_at=resolved_started,
                updated_at=resolved_ended,
            )
        )

    scope = runtime.service.ensure_scope(
        organization_id=organization_id,
        scope_kind="organization",
        display_name="Org-wide transfer KPI",
    )
    runtime.service.record_observation(
        organization_id=organization_id,
        metric_key="transfer_rate",
        scope_id=scope.scope_id,
        value=60.0,
        sample_size=4,
        confidence=0.8,
        period_start=utc_now() - timedelta(days=30),
        period_end=utc_now(),
        source_summary={"sources": ["conversations", "turn_traces", "tool_invocations"]},
    )
    goal = runtime.service.create_goal(
        organization_id=organization_id,
        metric_key="transfer_rate",
        scope_id=scope.scope_id,
        name="Reduce organization transfers",
        target_value=25.0,
        target_at=utc_now() + timedelta(days=30),
    )

    signals = runtime.insight_analyzer.build_signals_for_goal(goal.goal_id)
    signal_by_kind = {signal.blocker_kind: signal for signal in signals}

    assert "transfer_escalation" in signal_by_kind
    assert "workflow_hotspot" in signal_by_kind
    assert "tool_failures" in signal_by_kind
    assert signal_by_kind["workflow_hotspot"].evidence_bundle["agent_id"] == "sales"


def test_kpi_insight_analyzer_surfaces_cost_hotspots_and_tool_overhead(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    registry = SQLAlchemyAgentRegistry(session_factory)
    registry.bootstrap_from_directory(Path(__file__).resolve().parent / "_fixtures" / "data" / "agents")
    runtime = build_kpi_runtime(session_factory=session_factory, agent_registry=registry)

    demo_version_id = registry.resolve_version_id("sales", target="published")
    support_version_id = registry.resolve_version_id("support_triage", target="published")
    organization_id = "org-kpi-cost-analysis"
    now = datetime.now(timezone.utc)

    with session_factory.begin() as session:
        high_cost_conversations = []
        for index in range(3):
            conversation_id = f"costly-{index}"
            high_cost_conversations.append(conversation_id)
            started_at = now - timedelta(days=4 - index, hours=3)
            ended_at = started_at + timedelta(minutes=20)
            session.add(
                ConversationRecord(
                    conversation_id=conversation_id,
                    organization_id=organization_id,
                    agent_id="sales",
                    agent_version_id=demo_version_id,
                    mode="live",
                    channel="web_chat",
                    status="ended",
                    outcome="resolved",
                    step_id="closed",
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
            for invocation_index in range(3):
                session.add(
                    ToolInvocationRecord(
                        invocation_id=str(uuid4()),
                        organization_id=organization_id,
                        tool_ref="knowledge.lookup",
                        executor_kind="builtin",
                        status="succeeded" if invocation_index < 2 else "failed",
                        caller_json={"conversation_id": conversation_id, "channel": "web_chat"},
                        args_json={"query": f"refund flow {invocation_index}"},
                        dedupe_key=None,
                        decision="allow",
                        decision_reason=None,
                        output_json={},
                        error=None if invocation_index < 2 else "upstream timeout",
                        latency_ms=900 + invocation_index * 50,
                        metadata_json={},
                        created_at=ended_at - timedelta(minutes=4 - invocation_index),
                        updated_at=ended_at - timedelta(minutes=4 - invocation_index),
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
                    amount_usd=0.35,
                    reference_key=f"meta-{conversation_id}",
                    metadata_json={},
                    occurred_at=ended_at,
                    created_at=ended_at,
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
                    provider="openai",
                    cost_type="llm_tokens",
                    amount_usd=0.55,
                    reference_key=f"llm-{conversation_id}",
                    metadata_json={},
                    occurred_at=ended_at,
                    created_at=ended_at,
                )
            )

        reference_started = now - timedelta(days=1, hours=1)
        reference_ended = reference_started + timedelta(minutes=6)
        session.add(
            ConversationRecord(
                conversation_id="cheap-reference",
                organization_id=organization_id,
                agent_id="support_triage",
                agent_version_id=support_version_id,
                mode="live",
                channel="web_chat",
                status="ended",
                outcome="resolved",
                step_id="handoff_support",
                facts_json={},
                metadata_json={},
                processed_dedupe_keys_json=[],
                last_event_sequence=1,
                started_at=reference_started,
                ended_at=reference_ended,
                created_at=reference_started,
                updated_at=reference_ended,
            )
        )
        session.add(
            ProviderCostRecord(
                cost_record_id=str(uuid4()),
                organization_id=organization_id,
                conversation_id="cheap-reference",
                realtime_session_id=None,
                turn_trace_id=None,
                tool_invocation_id=None,
                provider="meta",
                cost_type="message_delivery",
                amount_usd=0.05,
                reference_key="cheap-reference",
                metadata_json={},
                occurred_at=reference_ended,
                created_at=reference_ended,
            )
        )

    scope = runtime.service.ensure_scope(
        organization_id=organization_id,
        scope_kind="organization",
        display_name="Org-wide cost KPI",
    )
    runtime.service.record_observation(
        organization_id=organization_id,
        metric_key="cost_per_conversation",
        scope_id=scope.scope_id,
        value=0.72,
        sample_size=4,
        confidence=0.85,
        period_start=utc_now() - timedelta(days=30),
        period_end=utc_now(),
        source_summary={"sources": ["provider_cost_records", "tool_invocations", "conversations"]},
    )
    goal = runtime.service.create_goal(
        organization_id=organization_id,
        metric_key="cost_per_conversation",
        scope_id=scope.scope_id,
        name="Lower organization cost per conversation",
        target_value=0.4,
        target_at=utc_now() + timedelta(days=30),
    )

    signals = runtime.insight_analyzer.build_signals_for_goal(goal.goal_id)
    signal_by_kind = {signal.blocker_kind: signal for signal in signals}

    assert "cost_inflation" in signal_by_kind
    assert "workflow_cost_hotspot" in signal_by_kind
    assert "tool_overhead" in signal_by_kind


def test_kpi_insight_analyzer_consumes_semantic_summary_tags(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    registry = SQLAlchemyAgentRegistry(session_factory)
    registry.bootstrap_from_directory(Path(__file__).resolve().parent / "_fixtures" / "data" / "agents")
    runtime = build_kpi_runtime(session_factory=session_factory, agent_registry=registry)

    demo_version_id = registry.resolve_version_id("sales", target="published")
    organization_id = "org-kpi-semantic-analysis"
    now = datetime.now(timezone.utc)
    tag_definition_id = str(uuid4())
    tagged_summaries: list[tuple[str, datetime]] = []

    with session_factory.begin() as session:
        for index in range(3):
            conversation_id = f"semantic-{index}"
            started_at = now - timedelta(days=4 - index, hours=2)
            ended_at = started_at + timedelta(minutes=10 + index)
            session.add(
                ConversationRecord(
                    conversation_id=conversation_id,
                    organization_id=organization_id,
                    agent_id="sales",
                    agent_version_id=demo_version_id,
                    mode="live",
                    channel="web_chat",
                    status="ended",
                    outcome="failed" if index < 2 else "resolved",
                    step_id="closed",
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
            # Flush the conversation row before its dependent summary row:
            # the two mappers share no relationship(), so SQLAlchemy does not
            # infer cross-mapper insert ordering from the raw FK alone.
            session.flush()
            summary_id = str(uuid4())
            session.add(
                IntentTagConversationSummaryRecord(
                    conversation_summary_id=summary_id,
                    organization_id=organization_id,
                    agent_id="sales",
                    agent_version_id=demo_version_id,
                    conversation_id=conversation_id,
                    summary_version=1,
                    status="final",
                    primary_intent_name="demo_request",
                    secondary_intents_json=[],
                    resolution_status="failed" if index < 2 else "resolved",
                    outcome="failed" if index < 2 else "resolved",
                    final_language="en",
                    response_language="en",
                    channel="web_chat",
                    requires_human_followup=False,
                    requires_review=False,
                    summary_payload_json={},
                    evidence_payload_json={},
                    generated_from_event_count=1,
                    last_event_created_at=ended_at,
                    created_at=ended_at,
                    updated_at=ended_at,
                )
            )
            if index < 2:
                tagged_summaries.append((summary_id, ended_at))

        session.add(
            TagDefinitionRecord(
                tag_definition_id=tag_definition_id,
                organization_id=organization_id,
                agent_id="sales",
                taxonomy_version_id=None,
                name="payment_declined",
                display_name="Payment Declined",
                description="Payment provider declined the action",
                tag_kind="blocker",
                category="payments",
                confidence_threshold=0.6,
                apply_scope="conversation",
                related_intent_id=None,
                is_active=True,
                is_deprecated=False,
                color=None,
                icon=None,
                rule_config_json={},
                metadata_json={},
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        for index, (summary_id, ended_at) in enumerate(tagged_summaries):
            session.add(
                IntentTagAssignmentRecord(
                    tag_assignment_id=str(uuid4()),
                    organization_id=organization_id,
                    conversation_id=f"semantic-{index}",
                    classification_event_id=None,
                    conversation_summary_id=summary_id,
                    tag_definition_id=tag_definition_id,
                    assignment_scope="conversation",
                    assignment_source="summary_rollup",
                    confidence=0.9,
                    reason_text="Matched summary rule for payment_declined",
                    evidence_payload_json={},
                    is_validated=False,
                    validated_by_user_id=None,
                    validated_at=None,
                    created_at=ended_at,
                )
            )

    scope = runtime.service.ensure_scope(
        organization_id=organization_id,
        scope_kind="organization",
        display_name="Org-wide resolution KPI",
    )
    runtime.service.record_observation(
        organization_id=organization_id,
        metric_key="resolution_rate",
        scope_id=scope.scope_id,
        value=33.3,
        sample_size=3,
        confidence=0.8,
        period_start=utc_now() - timedelta(days=30),
        period_end=utc_now(),
        source_summary={"sources": ["conversations", "intent_tag_conversation_summaries", "intent_tag_assignments"]},
    )
    goal = runtime.service.create_goal(
        organization_id=organization_id,
        metric_key="resolution_rate",
        scope_id=scope.scope_id,
        name="Improve resolution quality",
        target_value=70.0,
        target_at=utc_now() + timedelta(days=30),
    )

    signals = runtime.insight_analyzer.build_signals_for_goal(goal.goal_id)
    summary_signals = [
        signal
        for signal in signals
        if signal.blocker_kind == "summary_tag_pattern"
        and signal.evidence_bundle.get("tag_name") == "payment_declined"
    ]

    assert summary_signals
    assert summary_signals[0].occurrence_count == 2
    assert summary_signals[0].evidence_bundle["conversation_ids"] == ["semantic-0", "semantic-1"]
