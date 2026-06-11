from __future__ import annotations

from typing import Any

from .agent_document import AgentDocument, Step, StepTransition, step_capability_flags
from .schemas import (
    AgentCapabilityManifest,
    AuthoredStepGuidance,
    ConversationState,
    FactRequirement,
    JourneyContext,
    PendingFactContext,
    RouteBranch,
    RuntimeTurn,
    SemanticEventRecord,
    StepCapabilities,
    ToolOutcomeRecord,
    Transition,
    TransitionNarrative,
)
from .state_summary import summarize_step


def build_authored_step_guidance(step: object) -> AuthoredStepGuidance | None:
    guidance = AuthoredStepGuidance(
        say_on_entry=getattr(step, "say_on_entry", None),
        say_on_transition=getattr(step, "say_on_transition", None),
        ask_for_fact=getattr(step, "ask_for_fact", None),
        repair_response=getattr(step, "repair_response", None),
    )
    if not guidance.model_dump(exclude_defaults=True, exclude_none=True):
        return None
    return guidance


def build_authored_state_guidance(state: object) -> AuthoredStepGuidance | None:
    return build_authored_step_guidance(state)


def normalized_fact_requirements(step_like: object) -> list[FactRequirement]:
    raw_requirements = getattr(step_like, "fact_requirements", None)
    if not isinstance(raw_requirements, list):
        return []
    return [
        requirement
        if isinstance(requirement, FactRequirement)
        else FactRequirement.model_validate(requirement)
        for requirement in raw_requirements
    ]


def fact_requirement_names(step_like: object) -> list[str]:
    return [requirement.name for requirement in normalized_fact_requirements(step_like)]


def build_pending_fact_contexts(
    step: Step,
    accepted_facts: dict[str, Any],
    *,
    triggered_by: str | None = None,
    triggered_in_step: str | None = None,
) -> dict[str, PendingFactContext]:
    pending: dict[str, PendingFactContext] = {}

    requirements = normalized_fact_requirements(step)
    requirement_by_name = {req.name: req for req in requirements}
    for fact_name in [requirement.name for requirement in requirements]:
        if fact_name in accepted_facts:
            continue
        requirement = requirement_by_name.get(fact_name)
        pending[fact_name] = PendingFactContext(
            purpose=(requirement.purpose if requirement else "") or "",
            triggered_by=triggered_by,
            triggered_in_step=triggered_in_step,
            ask_for_fact=step.say if requirement is not None else None,
        )
    return pending


def build_route_horizon(
    agent_document: AgentDocument | None,
    step: Step,
    *,
    accepted_facts: dict[str, Any] | None = None,
) -> list[RouteBranch]:
    branches: list[RouteBranch] = []
    if agent_document is None:
        return branches
    for transition in step.transitions:
        target_step_id = _transition_target_step_id(transition)
        if target_step_id is None:
            continue
        if (
            target_step_id == step.id
            and getattr(getattr(transition, "when", None), "kind", None) == "otherwise"
        ):
            continue
        try:
            target = agent_document.step_by_id(target_step_id)
        except KeyError:
            continue
        branches.append(
            RouteBranch(
                target_step_id=target.id,
                target_step_capabilities=_step_capabilities(target),
                target_step_name=target.name,
                target_step_summary=summarize_step(target),
                branch_reason_code=getattr(transition, "reason_code", None) or None,
                branch_natural_reason=getattr(transition, "natural_reason", None)
                or getattr(transition, "label", None),
                branch_when_to_use=getattr(transition, "when_to_use", None)
                or getattr(transition, "label", None),
                required_fact_names=fact_requirement_names(target),
                required_tools=[binding.ref for binding in target.tool_policy if binding.ref],
            )
        )
    return branches


def build_transition_narrative(
    *,
    from_step: Step | None,
    to_step: Step,
    reason_code: str | None = None,
    transition_intent: str | None = None,
    natural_reason: str | None = None,
    bridge_required: bool = True,
) -> TransitionNarrative:
    return TransitionNarrative(
        from_step_id=from_step.id if from_step else None,
        to_step_id=to_step.id,
        reason_code=reason_code,
        transition_intent=transition_intent,
        natural_reason=natural_reason,
        bridge_required=bridge_required,
    )


def build_journey_context(
    *,
    conversation: ConversationState,
    agent_document: AgentDocument | None,
    step: Step,
    turn: RuntimeTurn,
    semantic_events: list[SemanticEventRecord] | None = None,
    previous_step: Step | None = None,
    transition_reason_code: str | None = None,
    transition_intent: str | None = None,
    transition_natural_reason: str | None = None,
    journey_summary: str | None = None,
    topic_freshness: str = "unknown",
    recent_tool_outcomes: list[ToolOutcomeRecord] | None = None,
) -> JourneyContext:
    del semantic_events
    pending_action = conversation.control_state.pending_action
    pending_action_summary = None
    if pending_action is not None:
        label = pending_action.action_label or pending_action.tool_ref or pending_action.action_type
        pending_action_summary = f"{label}: {pending_action.status}"

    triggered_by = transition_intent or transition_reason_code
    triggered_in_step = previous_step.id if previous_step else None

    return JourneyContext(
        conversation_phase=conversation.metadata.get("conversation_phase"),
        current_step_id=step.id,
        current_step_capabilities=_step_capabilities(step),
        current_step_name=step.name,
        current_step_purpose=summarize_step(step),
        previous_step_id=previous_step.id if previous_step else None,
        previous_step_name=previous_step.name if previous_step else None,
        transition_reason_code=transition_reason_code,
        transition_intent=transition_intent,
        transition_natural_reason=transition_natural_reason,
        journey_summary=journey_summary,
        current_user_text=turn.text,
        topic_freshness=topic_freshness,  # type: ignore[arg-type]
        pending_facts=build_pending_fact_contexts(
            step,
            conversation.facts,
            triggered_by=triggered_by,
            triggered_in_step=triggered_in_step,
        ),
        pending_action_summary=pending_action_summary,
        recent_tool_outcomes=list(recent_tool_outcomes or []),
        route_horizon=build_route_horizon(
            agent_document,
            step,
            accepted_facts=conversation.facts,
        ),
        authored_guidance=build_authored_step_guidance(step),
        agent_capability_manifest=(
            agent_document.agent_capability_manifest.model_copy(deep=True)
            if agent_document is not None
            and isinstance(agent_document.agent_capability_manifest, AgentCapabilityManifest)
            else (
                AgentCapabilityManifest.model_validate(agent_document.agent_capability_manifest)
                if agent_document is not None and agent_document.agent_capability_manifest is not None
                else None
            )
        ),
    )


def _transition_target_step_id(transition: object) -> str | None:
    if isinstance(transition, StepTransition):
        return transition.to_step_id
    if isinstance(transition, Transition):
        return transition.to
    target = getattr(transition, "to_step_id", None)
    if isinstance(target, str) and target.strip():
        return target
    target = getattr(transition, "to", None)
    return target if isinstance(target, str) and target.strip() else None


def _step_capabilities(
    step: Step,
) -> StepCapabilities:
    return StepCapabilities(**step_capability_flags(step))
