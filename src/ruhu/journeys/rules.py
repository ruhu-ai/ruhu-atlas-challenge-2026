from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
import json

from ruhu.agent_document import AgentDocument

from .models import (
    JourneyDefinition,
    JourneyDefinitionRules,
    JourneyDefinitionVersion,
    JourneyMilestoneRule,
    JourneyRulePredicate,
    JourneyReviewItem,
)

ALLOWED_OUTCOME_RULE_KEYS = {"completed", "abandoned", "transferred", "failed"}


def compile_definition_rules(rules: JourneyDefinitionRules) -> dict[str, object]:
    ordered_milestones = sorted(rules.milestones, key=lambda item: (item.order_index, item.milestone_id))
    predicate_kinds: set[str] = set()

    def _collect_predicates(milestone: JourneyMilestoneRule) -> None:
        for predicate in milestone.enter_when:
            predicate_kinds.add(predicate.kind)
        for predicate in milestone.complete_when:
            predicate_kinds.add(predicate.kind)

    for predicate in rules.entry_rules:
        predicate_kinds.add(predicate.kind)
    for predicate in rules.touchpoint_rules:
        predicate_kinds.add(predicate.kind)
    for predicates in rules.outcome_rules.values():
        for predicate in predicates:
            predicate_kinds.add(predicate.kind)
    for milestone in ordered_milestones:
        _collect_predicates(milestone)

    checkpoint_ids = [item.milestone_id for item in ordered_milestones if item.is_checkpoint]
    dwell_ids = [item.milestone_id for item in ordered_milestones if not item.is_checkpoint]
    return {
        "entry_rule_count": len(rules.entry_rules),
        "touchpoint_rule_count": len(rules.touchpoint_rules),
        "milestone_count": len(ordered_milestones),
        "milestone_ids_in_order": [item.milestone_id for item in ordered_milestones],
        "checkpoint_milestone_ids": checkpoint_ids,
        "dwell_milestone_ids": dwell_ids,
        "outcome_rule_keys": sorted(rules.outcome_rules),
        "predicate_kinds": sorted(predicate_kinds),
    }


def validate_definition_rules(rules: JourneyDefinitionRules) -> list[JourneyReviewItem]:
    issues: list[JourneyReviewItem] = []
    if not rules.entry_rules:
        issues.append(
            JourneyReviewItem(
                severity="error",
                code="journey.entry_rules.missing",
                message="Journey definitions must declare at least one entry rule.",
            )
        )
    if not rules.milestones:
        issues.append(
            JourneyReviewItem(
                severity="error",
                code="journey.milestones.missing",
                message="Journey definitions must declare at least one milestone.",
            )
        )
        return issues

    milestone_ids: set[str] = set()
    order_indexes: list[int] = []
    for milestone in rules.milestones:
        if milestone.milestone_id in milestone_ids:
            issues.append(
                JourneyReviewItem(
                    severity="error",
                    code="journey.milestones.duplicate_id",
                    message=f"Milestone id '{milestone.milestone_id}' is duplicated.",
                )
            )
        milestone_ids.add(milestone.milestone_id)
        order_indexes.append(milestone.order_index)

    duplicate_orders = sorted({value for value in order_indexes if order_indexes.count(value) > 1})
    for order_index in duplicate_orders:
        issues.append(
            JourneyReviewItem(
                severity="error",
                code="journey.milestones.duplicate_order",
                message=f"Milestone order_index '{order_index}' is duplicated.",
            )
        )

    expected_orders = list(range(1, len(order_indexes) + 1))
    if sorted(order_indexes) != expected_orders:
        issues.append(
            JourneyReviewItem(
                severity="warning",
                code="journey.milestones.non_contiguous_order",
                message="Milestone order indexes are not contiguous from 1.",
            )
        )

    invalid_outcomes = sorted(set(rules.outcome_rules) - ALLOWED_OUTCOME_RULE_KEYS)
    for outcome in invalid_outcomes:
        issues.append(
            JourneyReviewItem(
                severity="error",
                code="journey.outcome_rules.invalid_key",
                message=f"Outcome rule key '{outcome}' is not supported in v1.",
            )
        )

    for outcome, predicates in rules.outcome_rules.items():
        if outcome in ALLOWED_OUTCOME_RULE_KEYS and not predicates:
            issues.append(
                JourneyReviewItem(
                    severity="error",
                    code="journey.outcome_rules.empty",
                    message=f"Outcome rule '{outcome}' must declare at least one predicate.",
                )
            )

    if not rules.outcome_rules:
        issues.append(
            JourneyReviewItem(
                severity="warning",
                code="journey.outcome_rules.missing",
                message="Journey definition has no explicit outcome rules yet.",
            )
        )

    return issues


def validate_definition_version(
    definition: JourneyDefinition,
    version: JourneyDefinitionVersion,
    *,
    scoped_agent_documents: Sequence[AgentDocument] | None = None,
    missing_agent_ids: Sequence[str] | None = None,
    available_tool_refs: Iterable[str] | None = None,
) -> list[JourneyReviewItem]:
    issues = validate_definition_rules(version.rules)
    if version.definition_id != definition.definition_id:
        issues.append(
            JourneyReviewItem(
                severity="error",
                code="journey.definition_version.mismatched_definition",
                message="Journey definition version does not belong to the supplied definition.",
            )
        )
    issues.extend(_validate_scope_agents(missing_agent_ids or ()))
    issues.extend(
        _validate_reference_targets(
            version.rules,
            scoped_agent_documents=scoped_agent_documents or (),
            available_tool_refs=available_tool_refs,
        )
    )
    issues.extend(_validate_outcome_conflicts(version.rules))
    return issues


def _validate_scope_agents(missing_agent_ids: Sequence[str]) -> list[JourneyReviewItem]:
    issues: list[JourneyReviewItem] = []
    for agent_id in sorted(set(missing_agent_ids)):
        issues.append(
            JourneyReviewItem(
                severity="error",
                code="journey.scope.agent_missing",
                message=f"Journey scope references unknown or unavailable agent '{agent_id}'.",
            )
        )
    return issues


def _validate_reference_targets(
    rules: JourneyDefinitionRules,
    *,
    scoped_agent_documents: Sequence[AgentDocument],
    available_tool_refs: Iterable[str] | None,
) -> list[JourneyReviewItem]:
    issues: list[JourneyReviewItem] = []
    state_refs, fact_refs, tool_refs, milestone_enter_refs, milestone_complete_refs = _collect_reference_usage(rules)
    step_ids_by_agent = {
        str(agent_document.metadata.get("agent_id") or "<unknown>"): {step.id for step in agent_document.steps}
        for agent_document in scoped_agent_documents
    }
    fact_ids_by_agent = {
        str(agent_document.metadata.get("agent_id") or "<unknown>"): _known_agent_facts(agent_document)
        for agent_document in scoped_agent_documents
    }
    tool_ids_by_agent = {
        str(agent_document.metadata.get("agent_id") or "<unknown>"): _known_agent_tools(agent_document)
        for agent_document in scoped_agent_documents
    }

    missing_state_locations: dict[str, set[str]] = defaultdict(set)
    for state_id, locations in sorted(state_refs.items()):
        missing_agents = sorted(
            agent_id
            for agent_id, step_ids in step_ids_by_agent.items()
            if state_id not in step_ids
        )
        if not missing_agents:
            continue
        issues.append(
            JourneyReviewItem(
                severity="error",
                code="journey.references.state_missing_in_scope",
                message=(
                    f"Step '{state_id}' referenced by {_format_locations(locations)} "
                    f"is missing from scoped agents: {', '.join(missing_agents)}."
                ),
            )
        )
        for location in locations:
            missing_state_locations[location].add(state_id)

    for fact_name, locations in sorted(fact_refs.items()):
        missing_agents = sorted(
            agent_id
            for agent_id, fact_names in fact_ids_by_agent.items()
            if fact_name not in fact_names
        )
        if not missing_agents:
            continue
        issues.append(
            JourneyReviewItem(
                severity="warning",
                code="journey.references.fact_undeclared_in_scope",
                message=(
                    f"Fact '{fact_name}' referenced by {_format_locations(locations)} "
                    f"is not declared or required in scoped agents: {', '.join(missing_agents)}."
                ),
            )
        )

    available_tool_ref_set = None if available_tool_refs is None else set(available_tool_refs)
    invalid_tool_locations: dict[str, set[str]] = defaultdict(set)
    for tool_ref, locations in sorted(tool_refs.items()):
        if available_tool_ref_set is not None and tool_ref not in available_tool_ref_set:
            issues.append(
                JourneyReviewItem(
                    severity="error",
                    code="journey.references.tool_missing_runtime",
                    message=(
                        f"Tool '{tool_ref}' referenced by {_format_locations(locations)} "
                        "is not registered in the runtime."
                    ),
                )
            )
            for location in locations:
                invalid_tool_locations[location].add(tool_ref)
            continue
        missing_agents = sorted(
            agent_id
            for agent_id, tool_ids in tool_ids_by_agent.items()
            if tool_ref not in tool_ids
        )
        if not missing_agents:
            continue
        issues.append(
            JourneyReviewItem(
                severity="error",
                code="journey.references.tool_not_used_in_scope",
                message=(
                    f"Tool '{tool_ref}' referenced by {_format_locations(locations)} "
                    f"is not used by scoped agents: {', '.join(missing_agents)}."
                ),
            )
        )
        for location in locations:
            invalid_tool_locations[location].add(tool_ref)

    for milestone_id, references in sorted(milestone_enter_refs.items()):
        location = f"milestone '{milestone_id}'"
        unreachable_reasons = sorted(
            references.intersection(missing_state_locations.get(location, set()))
            | references.intersection(invalid_tool_locations.get(location, set()))
        )
        if not unreachable_reasons:
            continue
        issues.append(
            JourneyReviewItem(
                severity="error",
                code="journey.milestone.unreachable",
                message=(
                    f"Milestone '{milestone_id}' has entry predicates that cannot match in scope: "
                    f"{', '.join(unreachable_reasons)}."
                ),
            )
        )

    for milestone_id, references in sorted(milestone_complete_refs.items()):
        location = f"milestone '{milestone_id}' completion"
        unreachable_reasons = sorted(
            references.intersection(missing_state_locations.get(location, set()))
            | references.intersection(invalid_tool_locations.get(location, set()))
        )
        if not unreachable_reasons:
            continue
        issues.append(
            JourneyReviewItem(
                severity="warning",
                code="journey.milestone.completion_unreachable",
                message=(
                    f"Milestone '{milestone_id}' has completion predicates that cannot match in scope: "
                    f"{', '.join(unreachable_reasons)}."
                ),
            )
        )

    return issues


def _collect_reference_usage(
    rules: JourneyDefinitionRules,
) -> tuple[
    dict[str, set[str]],
    dict[str, set[str]],
    dict[str, set[str]],
    dict[str, set[str]],
    dict[str, set[str]],
]:
    state_refs: dict[str, set[str]] = defaultdict(set)
    fact_refs: dict[str, set[str]] = defaultdict(set)
    tool_refs: dict[str, set[str]] = defaultdict(set)
    milestone_enter_refs: dict[str, set[str]] = defaultdict(set)
    milestone_complete_refs: dict[str, set[str]] = defaultdict(set)

    def _record(predicate: JourneyRulePredicate, location: str, milestone_refs: set[str] | None = None) -> None:
        if predicate.value is None:
            return
        if predicate.kind == "step_entered":
            state_refs[predicate.value].add(location)
            if milestone_refs is not None:
                milestone_refs.add(predicate.value)
            return
        if predicate.kind in {"fact_present", "fact_equals"}:
            fact_refs[predicate.value].add(location)
            return
        if predicate.kind in {"tool_succeeded", "tool_failed"}:
            tool_refs[predicate.value].add(location)
            if milestone_refs is not None:
                milestone_refs.add(predicate.value)

    for predicate in rules.entry_rules:
        _record(predicate, "entry rules")
    for predicate in rules.touchpoint_rules:
        _record(predicate, "touchpoint rules")
    for milestone in rules.milestones:
        enter_location = f"milestone '{milestone.milestone_id}'"
        for predicate in milestone.enter_when:
            _record(predicate, enter_location, milestone_enter_refs[milestone.milestone_id])
        complete_location = f"milestone '{milestone.milestone_id}' completion"
        for predicate in milestone.complete_when:
            _record(predicate, complete_location, milestone_complete_refs[milestone.milestone_id])
    for outcome, predicates in rules.outcome_rules.items():
        location = f"outcome '{outcome}'"
        for predicate in predicates:
            _record(predicate, location)
    return state_refs, fact_refs, tool_refs, milestone_enter_refs, milestone_complete_refs


def _validate_outcome_conflicts(rules: JourneyDefinitionRules) -> list[JourneyReviewItem]:
    issues: list[JourneyReviewItem] = []
    outcomes_by_signature: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for outcome, predicates in rules.outcome_rules.items():
        if not predicates:
            continue
        signature = tuple(sorted(_predicate_signature(predicate) for predicate in predicates))
        outcomes_by_signature[signature].append(outcome)
    for outcomes in outcomes_by_signature.values():
        if len(outcomes) < 2:
            continue
        issues.append(
            JourneyReviewItem(
                severity="error",
                code="journey.outcome_rules.conflicting_predicates",
                message=(
                    "Multiple outcome rules share the same predicate set: "
                    + ", ".join(sorted(outcomes))
                    + "."
                ),
            )
        )
    return issues


def _predicate_signature(predicate: JourneyRulePredicate) -> str:
    return json.dumps(
        {
            "kind": predicate.kind,
            "value": predicate.value,
            "metadata": predicate.metadata,
        },
        sort_keys=True,
        default=str,
    )


def _known_agent_facts(agent_document: AgentDocument) -> set[str]:
    fact_names = {fact.name for fact in agent_document.fact_schema}
    for step in agent_document.steps:
        fact_names.update(requirement.name for requirement in step.fact_requirements)
        for guard in step.guards:
            if guard.kind == "fact_required":
                fact_names.add(guard.value)
        for transition in step.transitions:
            if transition.when.kind in {"fact_present", "fact_missing"} and transition.when.value:
                fact_names.add(transition.when.value)
    return fact_names


def _known_agent_tools(agent_document: AgentDocument) -> set[str]:
    return {
        binding.ref
        for step in agent_document.steps
        for binding in step.tool_policy
        if binding.ref
    }


def _format_locations(locations: set[str]) -> str:
    return ", ".join(sorted(locations))
