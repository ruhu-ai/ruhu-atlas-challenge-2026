from __future__ import annotations

from collections import Counter
from datetime import datetime
from math import ceil
from typing import Any, Iterable

from pydantic import BaseModel, Field

from .agent_document import (
    AgentDocument,
    AgentValidationReport,
    Step,
    StepTransition,
)
from .registry import AgentRegistration, AgentVersionSnapshot
from .schemas import AgentDefinitionValidationReport, ConversationState, FactDef, ToolBinding, TurnTrace


class AgentMetadataChange(BaseModel):
    field: str
    before: Any = None
    after: Any = None


class AgentFactChange(BaseModel):
    name: str
    status: str
    before: FactDef | None = None
    after: FactDef | None = None


class AgentStepTransitionChange(BaseModel):
    transition_id: str
    status: str
    before: StepTransition | None = None
    after: StepTransition | None = None


class AgentToolBindingChange(BaseModel):
    ref: str
    status: str
    before: ToolBinding | None = None
    after: ToolBinding | None = None


class AgentStepChange(BaseModel):
    step_id: str
    status: str
    before: Step | None = None
    after: Step | None = None
    changed_fields: list[str] = Field(default_factory=list)
    transition_changes: list[AgentStepTransitionChange] = Field(default_factory=list)
    tool_policy_changes: list[AgentToolBindingChange] = Field(default_factory=list)


class AgentDiffSummary(BaseModel):
    added_steps: int = 0
    removed_steps: int = 0
    changed_steps: int = 0
    added_facts: int = 0
    removed_facts: int = 0
    changed_facts: int = 0
    added_transitions: int = 0
    removed_transitions: int = 0
    changed_transitions: int = 0
    added_tool_bindings: int = 0
    removed_tool_bindings: int = 0
    changed_tool_bindings: int = 0


class AgentVersionDiff(BaseModel):
    agent_id: str
    source_version_id: str
    against_version_id: str
    metadata_changes: list[AgentMetadataChange] = Field(default_factory=list)
    fact_changes: list[AgentFactChange] = Field(default_factory=list)
    step_changes: list[AgentStepChange] = Field(default_factory=list)
    summary: AgentDiffSummary = Field(default_factory=AgentDiffSummary)


class PublishReviewRemediation(BaseModel):
    """Actionable remediation hint attached to a publish-review blocker.

    Per Template-Required-Tools-Onboarding-Spec §5.5 — additive payload
    that lets the frontend render a "Set up X" CTA instead of a raw
    error string.  Existing API clients ignoring the field continue
    to work.
    """

    kind: str  # e.g. "configure_tool"
    tool_ref: str | None = None
    url: str
    label: str
    documentation_url: str | None = None


class PublishReviewItem(BaseModel):
    severity: str
    code: str
    message: str
    remediation: PublishReviewRemediation | None = None


class PublishQualificationSummary(BaseModel):
    policy_version: str = "v1"
    minimum_pass_rate_ratio: float = 1.0
    allow_warning_failures: bool = True
    max_qualified_run_age_hours: int | None = None
    latest_run_id: str | None = None
    latest_run_status: str | None = None
    latest_run_pass_rate_ratio: float | None = None
    latest_qualified_run_id: str | None = None
    latest_qualified_at: datetime | None = None
    required_fixture_count: int = 0
    required_fixture_covered_count: int = 0
    blocker_failure_count: int = 0
    warning_failure_count: int = 0
    evaluation_blockers: list[PublishReviewItem] = Field(default_factory=list)
    fixture_reference_warnings: list[PublishReviewItem] = Field(default_factory=list)


class AgentPublishReadiness(BaseModel):
    agent_id: str
    draft_version_id: str
    published_version_id: str | None = None
    can_publish: bool
    blockers: list[PublishReviewItem] = Field(default_factory=list)
    warnings: list[PublishReviewItem] = Field(default_factory=list)
    validation: AgentDefinitionValidationReport | AgentValidationReport
    diff: AgentVersionDiff | None = None
    available_tools: list[str] = Field(default_factory=list)
    missing_tools: list[str] = Field(default_factory=list)
    qualification: PublishQualificationSummary = Field(default_factory=PublishQualificationSummary)


class AgentAuditEvent(BaseModel):
    kind: str
    version_id: str
    version_number: int
    status: str
    summary: str
    created_at: str
    published_at: str | None = None
    based_on_version_id: str | None = None
    is_current_draft: bool = False
    is_current_published: bool = False


class AgentAuditTrail(BaseModel):
    agent_id: str
    current_draft_version_id: str | None = None
    current_published_version_id: str | None = None
    events: list[AgentAuditEvent] = Field(default_factory=list)


class AgentLatencyStats(BaseModel):
    count: int = 0
    average_ms: int = 0
    p95_ms: int = 0
    max_ms: int = 0


class AgentOperationalMetrics(BaseModel):
    agent_id: str
    agent_version_id: str | None = None
    conversation_count: int = 0
    trace_count: int = 0
    avg_turns_per_conversation: float = 0.0
    total_latency: AgentLatencyStats = Field(default_factory=AgentLatencyStats)
    state_entries: dict[str, int] = Field(default_factory=dict)
    transition_counts: dict[str, int] = Field(default_factory=dict)
    action_counts: dict[str, int] = Field(default_factory=dict)
    tool_status_counts: dict[str, int] = Field(default_factory=dict)


def build_agent_diff(
    source_snapshot: AgentVersionSnapshot,
    against_snapshot: AgentVersionSnapshot,
) -> AgentVersionDiff:
    source_document = _require_agent_document(source_snapshot)
    against_document = _require_agent_document(against_snapshot)
    metadata_changes = _metadata_changes(source_snapshot, source_document, against_snapshot, against_document)
    fact_changes = _fact_changes(source_document.fact_schema, against_document.fact_schema)
    step_changes = _step_changes(source_document.steps, against_document.steps)
    summary = AgentDiffSummary(
        added_steps=sum(1 for item in step_changes if item.status == "added"),
        removed_steps=sum(1 for item in step_changes if item.status == "removed"),
        changed_steps=sum(1 for item in step_changes if item.status == "changed"),
        added_facts=sum(1 for item in fact_changes if item.status == "added"),
        removed_facts=sum(1 for item in fact_changes if item.status == "removed"),
        changed_facts=sum(1 for item in fact_changes if item.status == "changed"),
        added_transitions=sum(
            1 for step_change in step_changes for item in step_change.transition_changes if item.status == "added"
        ),
        removed_transitions=sum(
            1 for step_change in step_changes for item in step_change.transition_changes if item.status == "removed"
        ),
        changed_transitions=sum(
            1 for step_change in step_changes for item in step_change.transition_changes if item.status == "changed"
        ),
        added_tool_bindings=sum(
            1 for step_change in step_changes for item in step_change.tool_policy_changes if item.status == "added"
        ),
        removed_tool_bindings=sum(
            1 for step_change in step_changes for item in step_change.tool_policy_changes if item.status == "removed"
        ),
        changed_tool_bindings=sum(
            1 for step_change in step_changes for item in step_change.tool_policy_changes if item.status == "changed"
        ),
    )
    return AgentVersionDiff(
        agent_id=source_snapshot.agent_id,
        source_version_id=source_snapshot.version_id,
        against_version_id=against_snapshot.version_id,
        metadata_changes=metadata_changes,
        fact_changes=fact_changes,
        step_changes=step_changes,
        summary=summary,
    )


def build_publish_readiness(
    *,
    draft_snapshot: AgentVersionSnapshot,
    validation: AgentDefinitionValidationReport,
    published_snapshot: AgentVersionSnapshot | None,
    available_tool_refs: Iterable[str] | None = None,
) -> AgentPublishReadiness:
    available_tool_set = set(available_tool_refs or [])
    missing_tool_refs: list[str] = []
    draft_document = _require_agent_document(draft_snapshot)
    for step in draft_document.steps:
        for binding in step.tool_policy:
            if binding.mode == "blocked":
                continue
            if binding.ref and available_tool_set and binding.ref not in available_tool_set:
                missing_tool_refs.append(binding.ref)

    blockers = [
        PublishReviewItem(severity="error", code=issue.code, message=issue.message)
        for issue in validation.issues
        if issue.severity == "error"
    ]
    warnings = [
        PublishReviewItem(severity="warning", code=issue.code, message=issue.message)
        for issue in validation.issues
        if issue.severity == "warning"
    ]
    if published_snapshot is None:
        warnings.append(
            PublishReviewItem(
                severity="warning",
                code="publish.first_release",
                message="This agent has no published version yet. Publishing will create the first live release.",
            )
        )

    if missing_tool_refs:
        # Per spec §5.5: one blocker per missing ref so each carries
        # its own remediation.
        for ref in sorted(set(missing_tool_refs)):
            blockers.append(
                PublishReviewItem(
                    severity="error",
                    code="tool.missing_runtime_spec",
                    message=f"Draft references a tool that is not registered in the runtime: {ref}",
                )
            )

    diff = None
    if published_snapshot is not None:
        diff = build_agent_diff(draft_snapshot, published_snapshot)

    return AgentPublishReadiness(
        agent_id=draft_snapshot.agent_id,
        draft_version_id=draft_snapshot.version_id,
        published_version_id=None if published_snapshot is None else published_snapshot.version_id,
        can_publish=not blockers,
        blockers=blockers,
        warnings=warnings,
        validation=validation,
        diff=diff,
        available_tools=sorted(available_tool_set),
        missing_tools=sorted(set(missing_tool_refs)),
    )


def apply_publish_qualification(
    readiness: AgentPublishReadiness,
    qualification: PublishQualificationSummary,
) -> AgentPublishReadiness:
    blockers = [*readiness.blockers, *qualification.evaluation_blockers]
    warnings = [*readiness.warnings, *qualification.fixture_reference_warnings]
    return readiness.model_copy(
        update={
            "can_publish": readiness.can_publish and not qualification.evaluation_blockers,
            "blockers": blockers,
            "warnings": warnings,
            "qualification": qualification,
        }
    )


def build_agent_audit_trail(
    *,
    registration: AgentRegistration,
    versions: list[AgentVersionSnapshot],
) -> AgentAuditTrail:
    events: list[AgentAuditEvent] = []
    for version in sorted(versions, key=lambda item: item.version_number, reverse=True):
        kind = "published" if version.status == "published" else "draft_created"
        if version.is_current_published:
            kind = "current_published"
        elif version.is_current_draft:
            kind = "current_draft"
        summary = f"v{version.version_number} {version.status}"
        if version.based_on_version_id:
            summary += f" from {version.based_on_version_id}"
        events.append(
            AgentAuditEvent(
                kind=kind,
                version_id=version.version_id,
                version_number=version.version_number,
                status=version.status,
                summary=summary,
                created_at=version.created_at.isoformat(),
                published_at=None if version.published_at is None else version.published_at.isoformat(),
                based_on_version_id=version.based_on_version_id,
                is_current_draft=version.is_current_draft,
                is_current_published=version.is_current_published,
            )
        )
    return AgentAuditTrail(
        agent_id=registration.agent_id,
        current_draft_version_id=registration.current_draft_version_id,
        current_published_version_id=registration.current_published_version_id,
        events=events,
    )


def build_agent_metrics(
    *,
    agent_id: str,
    agent_version_id: str | None,
    conversations: list[ConversationState],
    traces: list[TurnTrace],
) -> AgentOperationalMetrics:
    conversation_count = len(conversations)
    trace_count = len(traces)
    latency_values = [
        int(trace.latency_breakdown_ms.get("total", 0))
        for trace in traces
        if isinstance(trace.latency_breakdown_ms.get("total", 0), int)
    ]
    state_entries = Counter(trace.step_after for trace in traces)
    transition_counts = Counter(f"{trace.step_before}->{trace.step_after}" for trace in traces)
    action_counts = Counter(trace.chosen_action.type for trace in traces)
    tool_status_counts = Counter(
        tool_call.status
        for trace in traces
        for tool_call in trace.tool_calls
    )
    avg_turns = 0.0 if conversation_count == 0 else round(trace_count / conversation_count, 2)
    return AgentOperationalMetrics(
        agent_id=agent_id,
        agent_version_id=agent_version_id,
        conversation_count=conversation_count,
        trace_count=trace_count,
        avg_turns_per_conversation=avg_turns,
        total_latency=_latency_stats(latency_values),
        state_entries=dict(sorted(state_entries.items())),
        transition_counts=dict(sorted(transition_counts.items())),
        action_counts=dict(sorted(action_counts.items())),
        tool_status_counts=dict(sorted(tool_status_counts.items())),
    )


def _latency_stats(values: list[int]) -> AgentLatencyStats:
    if not values:
        return AgentLatencyStats()
    ordered = sorted(values)
    p95_index = max(0, ceil(len(ordered) * 0.95) - 1)
    return AgentLatencyStats(
        count=len(ordered),
        average_ms=int(sum(ordered) / len(ordered)),
        p95_ms=ordered[p95_index],
        max_ms=ordered[-1],
    )


def _require_agent_document(snapshot: AgentVersionSnapshot) -> AgentDocument:
    if snapshot.agent_document is None:
        raise ValueError(f"agent version {snapshot.version_id!r} is missing agent_document")
    return snapshot.agent_document


def _metadata_changes(
    source_snapshot: AgentVersionSnapshot,
    source_document: AgentDocument,
    against_snapshot: AgentVersionSnapshot,
    against_document: AgentDocument,
) -> list[AgentMetadataChange]:
    changes: list[AgentMetadataChange] = []
    fields = {
        "name": (against_snapshot.name, source_snapshot.name),
        "version": (against_document.version, source_document.version),
        "start_scenario_id": (against_document.start_scenario_id, source_document.start_scenario_id),
        "start_step_id": (against_document.start_step_id, source_document.start_step_id),
    }
    for field_name, (before, after) in fields.items():
        if before != after:
            changes.append(AgentMetadataChange(field=field_name, before=before, after=after))
    return changes


def _fact_changes(source_facts: list[FactDef], against_facts: list[FactDef]) -> list[AgentFactChange]:
    source_by_name = {fact.name: fact for fact in source_facts}
    against_by_name = {fact.name: fact for fact in against_facts}
    changes: list[AgentFactChange] = []
    for name in sorted(set(source_by_name) | set(against_by_name)):
        source = source_by_name.get(name)
        target = against_by_name.get(name)
        if source is None:
            changes.append(AgentFactChange(name=name, status="removed", before=target, after=None))
            continue
        if target is None:
            changes.append(AgentFactChange(name=name, status="added", before=None, after=source))
            continue
        if source.model_dump(mode="json") != target.model_dump(mode="json"):
            changes.append(AgentFactChange(name=name, status="changed", before=target, after=source))
    return changes


def _step_changes(source_steps: list[Step], against_steps: list[Step]) -> list[AgentStepChange]:
    source_by_id = {step.id: step for step in source_steps}
    against_by_id = {step.id: step for step in against_steps}
    changes: list[AgentStepChange] = []
    for step_id in sorted(set(source_by_id) | set(against_by_id)):
        source = source_by_id.get(step_id)
        target = against_by_id.get(step_id)
        if source is None:
            changes.append(AgentStepChange(step_id=step_id, status="removed", before=target, after=None))
            continue
        if target is None:
            changes.append(AgentStepChange(step_id=step_id, status="added", before=None, after=source))
            continue
        changed_fields = _changed_step_fields(source, target)
        transition_changes = _transition_changes(source.transitions, target.transitions)
        tool_policy_changes = _tool_policy_changes(source.tool_policy, target.tool_policy)
        if changed_fields or transition_changes or tool_policy_changes:
            changes.append(
                AgentStepChange(
                    step_id=step_id,
                    status="changed",
                    before=target,
                    after=source,
                    changed_fields=changed_fields,
                    transition_changes=transition_changes,
                    tool_policy_changes=tool_policy_changes,
                )
            )
    return changes


def _changed_step_fields(source: Step, target: Step) -> list[str]:
    comparable_fields = [
        "name",
        "description",
        "say",
        "event_hints",
        "fact_requirements",
        "response_policy",
        "guards",
        "action_config",
        "workload_class",
        "execution_isolation",
        "handoff",
        "completion",
    ]
    changed: list[str] = []
    source_data = source.model_dump(mode="json")
    target_data = target.model_dump(mode="json")
    for field_name in comparable_fields:
        if source_data.get(field_name) != target_data.get(field_name):
            changed.append(field_name)
    return changed


def _transition_changes(
    source_transitions: list[StepTransition],
    against_transitions: list[StepTransition],
) -> list[AgentStepTransitionChange]:
    source_by_id = {transition.id: transition for transition in source_transitions}
    against_by_id = {transition.id: transition for transition in against_transitions}
    changes: list[AgentStepTransitionChange] = []
    for transition_id in sorted(set(source_by_id) | set(against_by_id)):
        source = source_by_id.get(transition_id)
        target = against_by_id.get(transition_id)
        if source is None:
            changes.append(AgentStepTransitionChange(transition_id=transition_id, status="removed", before=target))
            continue
        if target is None:
            changes.append(AgentStepTransitionChange(transition_id=transition_id, status="added", after=source))
            continue
        if source.model_dump(mode="json") != target.model_dump(mode="json"):
            changes.append(
                AgentStepTransitionChange(
                    transition_id=transition_id,
                    status="changed",
                    before=target,
                    after=source,
                )
            )
    return changes


def _tool_policy_changes(source_bindings: list[ToolBinding], against_bindings: list[ToolBinding]) -> list[AgentToolBindingChange]:
    source_by_ref = {binding.ref: binding for binding in source_bindings}
    against_by_ref = {binding.ref: binding for binding in against_bindings}
    changes: list[AgentToolBindingChange] = []
    for ref in sorted(set(source_by_ref) | set(against_by_ref)):
        source = source_by_ref.get(ref)
        target = against_by_ref.get(ref)
        if source is None:
            changes.append(AgentToolBindingChange(ref=ref, status="removed", before=target))
            continue
        if target is None:
            changes.append(AgentToolBindingChange(ref=ref, status="added", after=source))
            continue
        if source.model_dump(mode="json") != target.model_dump(mode="json"):
            changes.append(
                AgentToolBindingChange(
                    ref=ref,
                    status="changed",
                    before=target,
                    after=source,
                )
            )
    return changes
