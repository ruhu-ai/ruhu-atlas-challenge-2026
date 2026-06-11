from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from typing import Any

from ruhu.capture.comparison import fact_value_equals
from ruhu.registry import AgentVersionSnapshot
from ruhu.schemas import ConversationState, TurnTrace
from ruhu.tools.types import ToolInvocation

from .models import AssertionResult, FixtureValidationIssue, SimulationAssertion, SimulationFixture


class AssertionEngine:
    def evaluate(
        self,
        assertions: Iterable[SimulationAssertion],
        *,
        conversation: ConversationState,
        traces: list[TurnTrace],
        tool_invocations: list[ToolInvocation],
        turn_count: int | None = None,
    ) -> list[AssertionResult]:
        path = _step_path(traces, fallback_step=conversation.step_id)
        messages = [message.text for trace in traces for message in trace.emitted_messages]
        total_latency_ms = sum(trace.latency_breakdown_ms.get("total", 0) for trace in traces)
        first_latency_ms = next((trace.latency_breakdown_ms.get("total", 0) for trace in traces), 0)
        effective_turn_count = max(turn_count if turn_count is not None else len(traces) - 1, 0)
        tool_refs = _observed_tool_refs(traces, tool_invocations)
        tool_statuses = _observed_tool_statuses(traces, tool_invocations)
        tool_call_counts = _observed_tool_call_counts(traces, tool_invocations)

        results: list[AssertionResult] = []
        for assertion in assertions:
            results.append(
                self._evaluate_one(
                    assertion,
                    conversation=conversation,
                    path=path,
                    messages=messages,
                    tool_refs=tool_refs,
                    tool_statuses=tool_statuses,
                    tool_call_counts=tool_call_counts,
                    total_latency_ms=total_latency_ms,
                    first_latency_ms=first_latency_ms,
                    turn_count=effective_turn_count,
                )
            )
        return results

    def _evaluate_one(
        self,
        assertion: SimulationAssertion,
        *,
        conversation: ConversationState,
        path: list[str],
        messages: list[str],
        tool_refs: set[str],
        tool_statuses: dict[str, set[str]],
        tool_call_counts: dict[str, int],
        total_latency_ms: int,
        first_latency_ms: int,
        turn_count: int,
    ) -> AssertionResult:
        kind = assertion.kind
        config = assertion.config
        passed = False
        expected: dict[str, Any] = dict(config)
        actual: dict[str, Any] = {}
        message: str | None = None

        if kind == "final_step_equals":
            expected_step = str(config.get("step_id") or config.get("value") or "")
            actual = {"final_step": conversation.step_id}
            passed = conversation.step_id == expected_step
            message = f"expected final step {expected_step!r}, got {conversation.step_id!r}"
        elif kind == "final_step_one_of":
            step_ids = _str_list(config.get("step_ids") or config.get("values") or [])
            actual = {"final_step": conversation.step_id}
            passed = conversation.step_id in step_ids
            message = f"expected final step in {step_ids!r}, got {conversation.step_id!r}"
        elif kind == "fact_equals":
            fact_name = str(config.get("fact_name") or config.get("name") or "")
            expected_value = config.get("value")
            actual_value = conversation.facts.get(fact_name)
            actual = {"fact_name": fact_name, "value": actual_value}
            path_config = config.get("path")
            passed = fact_value_equals(
                actual_value,
                expected_value,
                path=path_config if isinstance(path_config, str) else None,
            )
            message = f"expected fact {fact_name!r}={expected_value!r}, got {actual_value!r}"
        elif kind == "fact_in":
            fact_name = str(config.get("fact_name") or config.get("name") or "")
            expected_values = list(config.get("values") or [])
            actual_value = conversation.facts.get(fact_name)
            actual = {"fact_name": fact_name, "value": actual_value}
            passed = actual_value in expected_values
            message = f"expected fact {fact_name!r} in {expected_values!r}, got {actual_value!r}"
        elif kind == "fact_matches_regex":
            fact_name = str(config.get("fact_name") or config.get("name") or "")
            pattern = str(config.get("pattern") or "")
            actual_value = conversation.facts.get(fact_name)
            actual = {"fact_name": fact_name, "value": actual_value}
            if not isinstance(actual_value, str):
                passed = False
                message = f"expected fact {fact_name!r} to be a string matching {pattern!r}, got {actual_value!r}"
            else:
                try:
                    passed = re.search(pattern, actual_value) is not None
                    message = f"expected fact {fact_name!r} to match regex {pattern!r}, got {actual_value!r}"
                except re.error as exc:
                    passed = False
                    message = f"invalid regex pattern {pattern!r}: {exc}"
        elif kind == "fact_present":
            fact_name = str(config.get("fact_name") or config.get("name") or "")
            actual = {"fact_name": fact_name, "present": fact_name in conversation.facts}
            passed = fact_name in conversation.facts
            message = f"expected fact {fact_name!r} to be present"
        elif kind == "fact_absent":
            fact_name = str(config.get("fact_name") or config.get("name") or "")
            actual = {"fact_name": fact_name, "present": fact_name in conversation.facts}
            passed = fact_name not in conversation.facts
            message = f"expected fact {fact_name!r} to be absent"
        elif kind == "step_path_contains":
            step_ids = _str_list(config.get("step_ids") or config.get("values") or [])
            actual = {"step_path": path}
            passed = all(step_id in path for step_id in step_ids)
            message = f"expected step path to contain {step_ids!r}"
        elif kind == "step_path_excludes":
            step_ids = _str_list(config.get("step_ids") or config.get("values") or [])
            actual = {"step_path": path}
            passed = all(step_id not in path for step_id in step_ids)
            message = f"expected step path to exclude {step_ids!r}"
        elif kind == "tool_called":
            tool_ref = str(config.get("tool_ref") or "")
            actual = {"tool_refs": sorted(tool_refs)}
            passed = tool_ref in tool_refs
            message = f"expected tool {tool_ref!r} to be called"
        elif kind == "tool_called_count_at_least":
            tool_ref = str(config.get("tool_ref") or "")
            expected_count = int(config.get("count") or config.get("value") or 0)
            actual_count = tool_call_counts.get(tool_ref, 0)
            actual = {"tool_ref": tool_ref, "count": actual_count}
            passed = actual_count >= expected_count
            message = f"expected tool {tool_ref!r} to be called at least {expected_count} times, got {actual_count}"
        elif kind == "tool_called_count_equals":
            tool_ref = str(config.get("tool_ref") or "")
            expected_count = int(config.get("count") or config.get("value") or 0)
            actual_count = tool_call_counts.get(tool_ref, 0)
            actual = {"tool_ref": tool_ref, "count": actual_count}
            passed = actual_count == expected_count
            message = f"expected tool {tool_ref!r} to be called {expected_count} times, got {actual_count}"
        elif kind == "tool_not_called":
            tool_ref = str(config.get("tool_ref") or "")
            actual = {"tool_refs": sorted(tool_refs)}
            passed = tool_ref not in tool_refs
            message = f"expected tool {tool_ref!r} not to be called"
        elif kind == "tool_status":
            tool_ref = str(config.get("tool_ref") or "")
            expected_status = str(config.get("status") or "")
            actual_statuses = sorted(tool_statuses.get(tool_ref, set()))
            actual = {"tool_ref": tool_ref, "statuses": actual_statuses}
            passed = expected_status in actual_statuses
            message = f"expected tool {tool_ref!r} to have status {expected_status!r}, got {actual_statuses!r}"
        elif kind == "message_contains":
            expected_text = str(config.get("text") or config.get("substring") or "")
            actual = {"messages": messages}
            passed = any(expected_text in text for text in messages)
            message = f"expected a message containing {expected_text!r}"
        elif kind == "message_any_of":
            candidates = _str_list(config.get("texts") or config.get("substrings") or [])
            actual = {"messages": messages}
            passed = any(candidate in text for candidate in candidates for text in messages)
            message = f"expected a message containing any of {candidates!r}"
        elif kind == "message_not_contains":
            expected_text = str(config.get("text") or config.get("substring") or "")
            actual = {"messages": messages}
            passed = all(expected_text not in text for text in messages)
            message = f"expected no message containing {expected_text!r}"
        elif kind == "pending_confirmation_required":
            tool_ref = str(config.get("tool_ref") or "")
            waiting = {ref for ref, statuses in tool_statuses.items() if "waiting_confirmation" in statuses}
            actual = {"tool_refs_waiting_confirmation": sorted(waiting)}
            passed = bool(waiting) if not tool_ref else tool_ref in waiting
            message = (
                "expected at least one pending confirmation"
                if not tool_ref
                else f"expected pending confirmation for {tool_ref!r}"
            )
        elif kind == "pending_confirmation_absent":
            tool_ref = str(config.get("tool_ref") or "")
            waiting = {ref for ref, statuses in tool_statuses.items() if "waiting_confirmation" in statuses}
            actual = {"tool_refs_waiting_confirmation": sorted(waiting)}
            passed = not waiting if not tool_ref else tool_ref not in waiting
            message = (
                "expected no pending confirmations"
                if not tool_ref
                else f"expected no pending confirmation for {tool_ref!r}"
            )
        elif kind == "turn_count_equals":
            expected_count = int(config.get("count") or config.get("value") or 0)
            actual = {"turn_count": turn_count}
            passed = turn_count == expected_count
            message = f"expected turn count {expected_count}, got {turn_count}"
        elif kind == "turn_count_at_most":
            expected_count = int(config.get("count") or config.get("value") or 0)
            actual = {"turn_count": turn_count}
            passed = turn_count <= expected_count
            message = f"expected turn count <= {expected_count}, got {turn_count}"
        elif kind == "latency_total_lt_ms":
            threshold = int(config.get("value") or config.get("ms") or 0)
            actual = {"latency_total_ms": total_latency_ms}
            passed = total_latency_ms < threshold
            message = f"expected total latency < {threshold}, got {total_latency_ms}"
        elif kind == "latency_first_response_lt_ms":
            threshold = int(config.get("value") or config.get("ms") or 0)
            actual = {"latency_first_response_ms": first_latency_ms}
            passed = first_latency_ms < threshold
            message = f"expected first response latency < {threshold}, got {first_latency_ms}"
        else:
            actual = {"kind": kind}
            message = f"unsupported assertion kind {kind!r}"

        return AssertionResult(
            fixture_assertion_id=assertion.assertion_id,
            kind=kind,
            severity=assertion.severity,
            passed=passed,
            expected=expected,
            actual=actual,
            message=None if passed else message,
        )


def validate_fixture_structure(fixture: SimulationFixture) -> list[FixtureValidationIssue]:
    issues: list[FixtureValidationIssue] = []
    if not fixture.turns:
        issues.append(
            FixtureValidationIssue(
                severity="blocker" if fixture.gate_required else "warning",
                code="fixture.turns_missing",
                message="Fixture does not declare any turns.",
                fixture_id=fixture.fixture_id,
            )
        )
    if not fixture.assertions:
        issues.append(
            FixtureValidationIssue(
                severity="warning",
                code="fixture.assertions_missing",
                message="Fixture does not declare any assertions.",
                fixture_id=fixture.fixture_id,
            )
        )

    turn_ids = [turn.turn_id for turn in fixture.turns if turn.turn_id]
    dedupe_keys = [turn.dedupe_key for turn in fixture.turns if turn.dedupe_key]
    assertion_ids = [assertion.assertion_id for assertion in fixture.assertions if assertion.assertion_id]

    for duplicate in _duplicates(turn_ids):
        issues.append(
            FixtureValidationIssue(
                severity="blocker",
                code="fixture.turn_id_duplicate",
                message=f"Fixture contains duplicate turn id {duplicate!r}.",
                fixture_id=fixture.fixture_id,
                turn_id=duplicate,
            )
        )
    for duplicate in _duplicates(dedupe_keys):
        issues.append(
            FixtureValidationIssue(
                severity="blocker",
                code="fixture.dedupe_key_duplicate",
                message=f"Fixture contains duplicate dedupe key {duplicate!r}.",
                fixture_id=fixture.fixture_id,
                turn_id=duplicate,
            )
        )
    for duplicate in _duplicates(assertion_ids):
        issues.append(
            FixtureValidationIssue(
                severity="blocker",
                code="fixture.assertion_id_duplicate",
                message=f"Fixture contains duplicate assertion id {duplicate!r}.",
                fixture_id=fixture.fixture_id,
                assertion_id=duplicate,
            )
        )

    for turn in fixture.turns:
        if turn.event_type in {"user_message", "user_final_transcript"} and not (turn.text or "").strip():
            issues.append(
                FixtureValidationIssue(
                    severity="warning",
                    code="fixture.turn_text_empty",
                    message=f"Turn {turn.turn_id or '<generated>'} is a user text event with empty text.",
                    fixture_id=fixture.fixture_id,
                    turn_id=turn.turn_id,
                )
            )
    for assertion in fixture.assertions:
        issues.extend(_validate_assertion_structure(fixture.fixture_id, assertion))
    return issues


def validate_fixture_references(snapshot: AgentVersionSnapshot, fixture: SimulationFixture) -> list[FixtureValidationIssue]:
    if snapshot.agent_document is None:
        raise ValueError(f"agent version {snapshot.version_id!r} is missing canonical agent document")
    step_ids = set(snapshot.agent_document.step_ids)
    entry_ids = step_ids
    fact_names = {fact.name for fact in snapshot.agent_document.fact_schema}
    tool_refs = {
        binding.ref
        for step in snapshot.agent_document.steps
        for binding in step.tool_policy
        if binding.ref
    }

    issues: list[FixtureValidationIssue] = []
    if fixture.starting_step_id and fixture.starting_step_id not in entry_ids:
        issues.append(
            FixtureValidationIssue(
                severity="blocker",
                code="fixture.starting_step_missing",
                message=f"Fixture references missing starting step {fixture.starting_step_id!r}.",
                fixture_id=fixture.fixture_id,
            )
        )

    for assertion in fixture.assertions:
        config = assertion.config
        if assertion.kind in {"final_step_equals"}:
            candidate = str(config.get("step_id") or config.get("value") or "")
            if candidate and candidate not in step_ids:
                issues.append(_step_issue(fixture.fixture_id, assertion.assertion_id, candidate))
        elif assertion.kind in {"final_step_one_of", "step_path_contains", "step_path_excludes"}:
            for candidate in _str_list(config.get("step_ids") or config.get("values") or []):
                if candidate not in step_ids:
                    issues.append(_step_issue(fixture.fixture_id, assertion.assertion_id, candidate))
        elif assertion.kind in {"fact_equals", "fact_in", "fact_matches_regex", "fact_present", "fact_absent"}:
            candidate = str(config.get("fact_name") or config.get("name") or "")
            if candidate and candidate not in fact_names:
                issues.append(
                    FixtureValidationIssue(
                        severity="warning",
                        code="fixture.assertion_fact_missing",
                        message=f"Fixture assertion references unknown fact {candidate!r}.",
                        fixture_id=fixture.fixture_id,
                        assertion_id=assertion.assertion_id,
                    )
                )
        elif assertion.kind in {
            "tool_called",
            "tool_called_count_at_least",
            "tool_called_count_equals",
            "tool_not_called",
            "tool_status",
            "pending_confirmation_required",
            "pending_confirmation_absent",
        }:
            candidate = str(config.get("tool_ref") or "")
            if candidate and candidate not in tool_refs:
                issues.append(
                    FixtureValidationIssue(
                        severity="warning",
                        code="fixture.assertion_tool_missing",
                        message=f"Fixture assertion references unknown tool {candidate!r}.",
                        fixture_id=fixture.fixture_id,
                        assertion_id=assertion.assertion_id,
                    )
                )
    return issues


def validate_fixture(snapshot: AgentVersionSnapshot, fixture: SimulationFixture) -> list[FixtureValidationIssue]:
    return [*validate_fixture_structure(fixture), *validate_fixture_references(snapshot, fixture)]


def collect_fixture_validation_issues(
    snapshot: AgentVersionSnapshot,
    fixtures: Iterable[SimulationFixture],
    *,
    active_only: bool = True,
) -> list[FixtureValidationIssue]:
    issues: list[FixtureValidationIssue] = []
    for fixture in fixtures:
        if active_only and not fixture.is_active:
            continue
        issues.extend(validate_fixture(snapshot, fixture))
    return issues


def _validate_assertion_structure(
    fixture_id: str,
    assertion: SimulationAssertion,
) -> list[FixtureValidationIssue]:
    issues: list[FixtureValidationIssue] = []
    config = assertion.config
    kind = assertion.kind

    if kind == "final_step_equals":
        issues.extend(_require_non_empty_string(fixture_id, assertion, config, "step_id", "fixture.assertion_step_required"))
    elif kind in {"final_step_one_of", "step_path_contains", "step_path_excludes"}:
        issues.extend(_require_non_empty_list(fixture_id, assertion, config, "step_ids", "fixture.assertion_steps_required"))
    elif kind in {"fact_equals", "fact_present", "fact_absent"}:
        issues.extend(_require_non_empty_string(fixture_id, assertion, config, "fact_name", "fixture.assertion_fact_required"))
    elif kind == "fact_in":
        issues.extend(_require_non_empty_string(fixture_id, assertion, config, "fact_name", "fixture.assertion_fact_required"))
        issues.extend(_require_non_empty_list(fixture_id, assertion, config, "values", "fixture.assertion_values_required"))
    elif kind == "fact_matches_regex":
        issues.extend(_require_non_empty_string(fixture_id, assertion, config, "fact_name", "fixture.assertion_fact_required"))
        pattern = str(config.get("pattern") or "")
        if not pattern:
            issues.append(
                _assertion_issue(
                    fixture_id,
                    assertion,
                    "fixture.assertion_pattern_required",
                    "Fixture assertion requires a non-empty regex pattern.",
                )
            )
        else:
            try:
                re.compile(pattern)
            except re.error as exc:
                issues.append(
                    _assertion_issue(
                        fixture_id,
                        assertion,
                        "fixture.assertion_regex_invalid",
                        f"Fixture assertion regex pattern is invalid: {exc}",
                    )
                )
    elif kind in {"tool_called", "tool_called_count_at_least", "tool_called_count_equals", "tool_not_called", "tool_status"}:
        issues.extend(_require_non_empty_string(fixture_id, assertion, config, "tool_ref", "fixture.assertion_tool_required"))
        if kind == "tool_status":
            issues.extend(_require_non_empty_string(fixture_id, assertion, config, "status", "fixture.assertion_status_required"))
    elif kind in {"message_contains", "message_not_contains"}:
        issues.extend(_require_non_empty_string(fixture_id, assertion, config, "text", "fixture.assertion_text_required"))
    elif kind == "message_any_of":
        issues.extend(_require_non_empty_list(fixture_id, assertion, config, "texts", "fixture.assertion_texts_required"))
    elif kind in {"turn_count_equals", "turn_count_at_most"}:
        issues.extend(_require_int_threshold(fixture_id, assertion, config, "count", "fixture.assertion_count_required"))
    elif kind in {"latency_total_lt_ms", "latency_first_response_lt_ms"}:
        issues.extend(_require_int_threshold(fixture_id, assertion, config, "ms", "fixture.assertion_threshold_required"))

    return issues


def _require_non_empty_string(
    fixture_id: str,
    assertion: SimulationAssertion,
    config: dict[str, Any],
    primary_key: str,
    code: str,
) -> list[FixtureValidationIssue]:
    aliases = {
        "step_id": ("step_id", "value"),
        "fact_name": ("fact_name", "name"),
        "tool_ref": ("tool_ref",),
        "status": ("status",),
        "text": ("text", "substring"),
    }
    keys = aliases.get(primary_key, (primary_key,))
    value = next((config.get(key) for key in keys if config.get(key) not in {None, ""}), None)
    if value is not None:
        return []
    messages = {
        "fixture.assertion_step_required": "Fixture assertion requires a target step id.",
        "fixture.assertion_fact_required": "Fixture assertion requires a fact name.",
        "fixture.assertion_tool_required": "Fixture assertion requires a tool ref.",
        "fixture.assertion_status_required": "Fixture assertion requires a tool status.",
        "fixture.assertion_text_required": "Fixture assertion requires non-empty message text.",
    }
    return [_assertion_issue(fixture_id, assertion, code, messages.get(code, "Fixture assertion is missing required text."))]


def _require_non_empty_list(
    fixture_id: str,
    assertion: SimulationAssertion,
    config: dict[str, Any],
    primary_key: str,
    code: str,
) -> list[FixtureValidationIssue]:
    aliases = {
        "step_ids": ("step_ids", "values"),
        "values": ("values",),
        "texts": ("texts", "substrings"),
    }
    keys = aliases.get(primary_key, (primary_key,))
    value = next((config.get(key) for key in keys if config.get(key) is not None), None)
    if isinstance(value, list) and value:
        return []
    messages = {
        "fixture.assertion_steps_required": "Fixture assertion requires at least one step id.",
        "fixture.assertion_values_required": "Fixture assertion requires at least one allowed value.",
        "fixture.assertion_texts_required": "Fixture assertion requires at least one candidate message substring.",
    }
    return [_assertion_issue(fixture_id, assertion, code, messages.get(code, "Fixture assertion is missing required values."))]


def _require_int_threshold(
    fixture_id: str,
    assertion: SimulationAssertion,
    config: dict[str, Any],
    primary_key: str,
    code: str,
) -> list[FixtureValidationIssue]:
    aliases = {
        "count": ("count", "value"),
        "ms": ("ms", "value"),
    }
    keys = aliases.get(primary_key, (primary_key,))
    value = next((config.get(key) for key in keys if config.get(key) is not None), None)
    if value is None:
        return [_assertion_issue(fixture_id, assertion, code, "Fixture assertion is missing a required numeric threshold.")]
    try:
        if int(value) < 0:
            raise ValueError("threshold must be non-negative")
    except (TypeError, ValueError) as exc:
        return [
            _assertion_issue(
                fixture_id,
                assertion,
                "fixture.assertion_threshold_invalid",
                f"Fixture assertion threshold is invalid: {exc}",
            )
        ]
    return []


def _assertion_issue(
    fixture_id: str,
    assertion: SimulationAssertion,
    code: str,
    message: str,
) -> FixtureValidationIssue:
    return FixtureValidationIssue(
        severity="blocker",
        code=code,
        message=message,
        fixture_id=fixture_id,
        assertion_id=assertion.assertion_id,
    )


def _step_issue(fixture_id: str, assertion_id: str, step_id: str) -> FixtureValidationIssue:
    return FixtureValidationIssue(
        severity="warning",
        code="fixture.assertion_step_missing",
        message=f"Fixture assertion references unknown step {step_id!r}.",
        fixture_id=fixture_id,
        assertion_id=assertion_id,
    )


def _step_path(traces: list[TurnTrace], *, fallback_step: str) -> list[str]:
    if not traces:
        return [fallback_step]
    path = [traces[0].step_before]
    path.extend(trace.step_after for trace in traces)
    return path


def _observed_tool_refs(traces: list[TurnTrace], invocations: list[ToolInvocation]) -> set[str]:
    refs = {invocation.tool_ref for invocation in invocations}
    for trace in traces:
        refs.update(call.tool_ref for call in trace.tool_calls)
    return refs


def _observed_tool_statuses(traces: list[TurnTrace], invocations: list[ToolInvocation]) -> dict[str, set[str]]:
    statuses: dict[str, set[str]] = {}
    for invocation in invocations:
        statuses.setdefault(invocation.tool_ref, set()).add(invocation.status)
    for trace in traces:
        for call in trace.tool_calls:
            statuses.setdefault(call.tool_ref, set()).add(_normalize_trace_tool_status(call.status))
    return statuses


def _observed_tool_call_counts(traces: list[TurnTrace], invocations: list[ToolInvocation]) -> dict[str, int]:
    trace_counts = Counter(call.tool_ref for trace in traces for call in trace.tool_calls)
    invocation_counts = Counter(invocation.tool_ref for invocation in invocations)
    refs = set(trace_counts) | set(invocation_counts)
    return {ref: max(trace_counts.get(ref, 0), invocation_counts.get(ref, 0)) for ref in refs}


def _normalize_trace_tool_status(status: str) -> str:
    mapping = {
        "requested": "pending",
        "confirmation_required": "waiting_confirmation",
        "success": "completed",
        "timeout": "timed_out",
        "error": "failed",
    }
    return mapping.get(status, status)


def _str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value is None:
        return []
    return [str(value)]


def _duplicates(values: Iterable[str]) -> list[str]:
    counts = Counter(values)
    return [value for value, count in counts.items() if count > 1]
