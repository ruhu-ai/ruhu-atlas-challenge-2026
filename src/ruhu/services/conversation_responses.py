"""Projection helpers from runtime/domain objects to conversation API responses."""

from __future__ import annotations

from ..agent_document import (
    AgentDocument,
    build_step_runtime_entry,
    compile_agent_document,
    step_capability_flags,
)
from ..api_models import (
    ConversationRuntimeResponse,
    ConversationTraceResponse,
    RealtimeConversationEventResponse,
    TurnExecutionResponse,
    TurnInteractionDebugSnapshotResponse,
    TurnInteractionDebugVoicePolicyResponse,
)
from ..realtime import RealtimeEvent
from ..schemas import (
    ConversationState,
    RuntimeTurnResult,
    StepCapabilities,
    TurnTrace,
)


def conversation_runtime_response(
    conversation: ConversationState,
    *,
    agent_document: AgentDocument | None = None,
) -> ConversationRuntimeResponse:
    runtime_entry = None
    step_capabilities = StepCapabilities()
    if agent_document is not None:
        try:
            compiled_agent_document = compile_agent_document(agent_document)
            runtime_entry = build_step_runtime_entry(
                compiled_agent_document,
                current_step_id=conversation.step_id,
                facts=conversation.facts,
                pending_action=conversation.control_state.pending_action is not None,
                pending_permission=conversation.control_state.pending_permission is not None,
                active_repair=conversation.control_state.active_repair is not None,
            )
            current_step = compiled_agent_document.step_by_id(conversation.step_id)
            step_capabilities = StepCapabilities(**step_capability_flags(current_step))
        except KeyError:
            runtime_entry = None
    return ConversationRuntimeResponse(
        conversation_id=conversation.conversation_id,
        organization_id=conversation.organization_id,
        agent_id=conversation.agent_id,
        agent_version_id=conversation.agent_version_id,
        mode=conversation.mode,
        channel=conversation.channel,
        status=conversation.status,
        outcome=conversation.outcome,
        step_id=conversation.step_id,
        scenario_id=runtime_entry.current_scenario_id if runtime_entry is not None else None,
        step_capabilities=step_capabilities,
        missing_facts=list(runtime_entry.missing_facts) if runtime_entry is not None else [],
        available_tool_refs=list(runtime_entry.available_tool_refs) if runtime_entry is not None else [],
        transition_target_ids=list(runtime_entry.transition_target_ids) if runtime_entry is not None else [],
        scripted_say=runtime_entry.scripted_say if runtime_entry is not None else None,
        facts=conversation.facts,
        metadata=conversation.metadata,
        started_at=conversation.started_at,
        ended_at=conversation.ended_at,
        updated_at=conversation.updated_at,
        control_state=conversation.control_state,
    )


def turn_interaction_debug_snapshot_response(
    snapshot: object | None,
) -> TurnInteractionDebugSnapshotResponse | None:
    if snapshot is None:
        return None
    voice_policy = getattr(snapshot, "voice_interaction_policy", None)
    if voice_policy is None:
        return None
    return TurnInteractionDebugSnapshotResponse(
        step_id=getattr(snapshot, "step_id"),
        channel=getattr(snapshot, "channel"),
        voice_interaction_policy=TurnInteractionDebugVoicePolicyResponse(
            step_id=getattr(voice_policy, "step_id"),
            channel=getattr(snapshot, "channel"),
            endpointing_ms=getattr(voice_policy, "endpointing_ms"),
            soft_timeout_ms=getattr(voice_policy, "soft_timeout_ms"),
            turn_eagerness=getattr(voice_policy, "turn_eagerness"),
            interruptibility_policy=getattr(voice_policy, "interruptibility_policy"),
        ),
        pending_action=getattr(snapshot, "pending_action", None),
        pending_permission=getattr(snapshot, "pending_permission", None),
        active_repair=getattr(snapshot, "active_repair", None),
    )


def turn_execution_response(
    result: RuntimeTurnResult,
    *,
    agent_document: AgentDocument | None = None,
) -> TurnExecutionResponse:
    scenario_before = None
    scenario_after = None
    if agent_document is not None:
        try:
            scenario_before = agent_document.scenario_for_step_id(result.step_before).id
        except KeyError:
            scenario_before = None
        try:
            scenario_after = agent_document.scenario_for_step_id(result.step_after).id
        except KeyError:
            scenario_after = None
    return TurnExecutionResponse(
        turn_id=result.turn_id,
        conversation_id=result.conversation_id,
        step_before=result.step_before,
        step_after=result.step_after,
        scenario_before=scenario_before,
        scenario_after=scenario_after,
        semantic_events=result.semantic_events,
        fact_updates=result.fact_updates,
        chosen_action=result.chosen_action,
        emitted_messages=result.emitted_messages,
        tool_calls=result.tool_calls,
        rules=result.rules,
        trace_id=result.trace_id,
        latency_breakdown_ms=result.latency_breakdown_ms,
        interaction_debug_snapshot=turn_interaction_debug_snapshot_response(
            result.interaction_debug_snapshot
        ),
    )


def conversation_trace_response(trace: TurnTrace) -> ConversationTraceResponse:
    return ConversationTraceResponse(
        trace_id=trace.trace_id,
        conversation_id=trace.conversation_id,
        turn_id=trace.turn_id,
        step_before=trace.step_before,
        step_after=trace.step_after,
        event_type=trace.event_type,
        emitted_messages=trace.emitted_messages,
        chosen_action=trace.chosen_action,
        guard_results=trace.guard_results,
        tool_calls=trace.tool_calls,
        latency_breakdown_ms=trace.latency_breakdown_ms,
        recorded_at=trace.recorded_at,
    )


def normalize_realtime_payload(payload: object) -> object:
    if isinstance(payload, dict):
        normalized: dict[str, object] = {}
        for key, value in payload.items():
            normalized[str(key)] = normalize_realtime_payload(value)
        return normalized
    if isinstance(payload, list):
        return [normalize_realtime_payload(item) for item in payload]
    return payload


def realtime_conversation_event_response(event: RealtimeEvent) -> RealtimeConversationEventResponse:
    normalized_payload = normalize_realtime_payload(event.payload)
    if not isinstance(normalized_payload, dict):
        normalized_payload = {}
    return RealtimeConversationEventResponse(
        event_id=event.event_id,
        conversation_id=event.conversation_id,
        realtime_session_id=event.realtime_session_id,
        family=event.family,
        name=event.name,
        conversation_sequence=event.conversation_sequence,
        actor_type=event.actor_type,
        actor_id=event.actor_id,
        payload=normalized_payload,
        created_at=event.created_at,
    )
