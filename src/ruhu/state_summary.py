from __future__ import annotations

from .agent_document import Step


def derive_step_summary(step: Step) -> str:
    if step.description and step.description.strip():
        return step.description.strip()

    label = (step.name or "step").strip() or "step"
    lowered = label.lower()
    if step.completion is not None:
        return f"Finish in {lowered}"
    if step.handoff is not None:
        return f"Hand off from {lowered}"
    if step.action_config is not None:
        return f"Complete the configured action in {lowered}"
    if step.fact_requirements:
        return f"Collect the needed information in {lowered}"
    if step.say and step.say.strip():
        return step.say.strip()
    return f"Handle {lowered}"


def derive_workflow_step_summary(step_name: str) -> str:
    label = (step_name or "step").strip() or "step"
    return f"Handle {label.lower()}"


def summarize_step(step: Step) -> str:
    return derive_step_summary(step)


def summarize_state(state: object) -> str:
    description = getattr(state, "description", None) or getattr(state, "purpose", None)
    if isinstance(description, str) and description.strip():
        return description.strip()
    goal = getattr(state, "goal", None)
    if isinstance(goal, str) and goal.strip():
        return goal.strip()
    name = getattr(state, "name", None) or getattr(state, "id", None)
    label = str(name or "state").strip() or "state"
    return f"Handle {label.lower()}"
