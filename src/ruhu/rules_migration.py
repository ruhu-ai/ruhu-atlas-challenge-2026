from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .rules_store import (
    RuleBindingCreate,
    RuleRevisionBody,
    RulesRuntime,
)

_STRING_LITERAL_PATTERN = r"""(?P<quote>["'])(?P<value>.*?)(?P=quote)"""
_RE_IN_PATTERN = re.compile(rf"^\s*{_STRING_LITERAL_PATTERN}\s+in\s+(?P<var>[A-Za-z_][A-Za-z0-9_\.()]*)\s*$")
_RE_COMPARE_PATTERN = re.compile(
    rf"^\s*(?P<var>[A-Za-z_][A-Za-z0-9_\.()]*)\s*(?P<op>==|!=|>=|<=|>|<)\s*{_STRING_LITERAL_PATTERN}\s*$"
)
_RE_NUMERIC_COMPARE_PATTERN = re.compile(
    r"^\s*(?P<var>[A-Za-z_][A-Za-z0-9_\.()]*)\s*(?P<op>>=|<=|>|<)\s*(?P<value>-?\d+(?:\.\d+)?)\s*$"
)
_RE_BETWEEN_PATTERN = re.compile(
    r"^\s*(?P<lower>-?\d+(?:\.\d+)?)\s*<=\s*(?P<var>[A-Za-z_][A-Za-z0-9_\.()]*)\s*<=\s*(?P<upper>-?\d+(?:\.\d+)?)\s*$"
)
_RE_REGEX_PATTERN = re.compile(
    r"""^\s*re\.search\(\s*r?(?P<quote>["'])(?P<pattern>.*?)(?P=quote)\s*,\s*(?P<var>[A-Za-z_][A-Za-z0-9_\.()]*)\s*\)\s*$"""
)

_VARIABLE_PATHS: dict[str, str] = {
    "user_utterance": "turn.text",
    "agent_response": "metadata.agent_response",
    "intent": "metadata.intent",
    "entities": "metadata.entities",
    "tool_name": "tool.ref",
    "tool_parameters": "tool.args",
    "current_hour": "time.current_hour",
    "current_day": "time.current_day",
    "session_data": "metadata.session_data",
    "user_id": "metadata.user_id",
    "call_sid": "metadata.call_sid",
    "current_node": "conversation.step_id",
}


@dataclass(slots=True)
class LegacyPolicyMigrationItem:
    policy_id: str
    status: str
    detail: str
    rule_id: str | None = None
    revision: int | None = None
    binding_id: str | None = None


@dataclass(slots=True)
class LegacyPolicyMigrationReport:
    total: int
    migrated: int
    skipped: int
    failed: int
    items: list[LegacyPolicyMigrationItem]

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "migrated": self.migrated,
            "skipped": self.skipped,
            "failed": self.failed,
            "items": [item.__dict__ for item in self.items],
        }


def extract_legacy_policies(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        if isinstance(payload.get("items"), list):
            return [dict(item) for item in payload["items"] if isinstance(item, Mapping)]
        if isinstance(payload.get("policies"), list):
            return [dict(item) for item in payload["policies"] if isinstance(item, Mapping)]
        return [dict(payload)]
    return []


def migrate_legacy_policies(
    *,
    runtime: RulesRuntime | None,
    organization_id: str,
    actor_user_id: str | None,
    policies: Sequence[Mapping[str, Any]],
    create_bindings: bool = True,
    dry_run: bool = False,
) -> LegacyPolicyMigrationReport:
    items: list[LegacyPolicyMigrationItem] = []
    migrated = 0
    skipped = 0
    failed = 0

    for raw in policies:
        policy = dict(raw)
        policy_id = str(policy.get("id") or policy.get("policy_id") or policy.get("name") or "unknown")
        try:
            converted = convert_legacy_policy(policy)
            if converted is None:
                skipped += 1
                items.append(
                    LegacyPolicyMigrationItem(
                        policy_id=policy_id,
                        status="skipped",
                        detail="unsupported legacy condition format",
                    )
                )
                continue

            rule_id, body = converted
            scope = "organization"
            if dry_run:
                migrated += 1
                items.append(
                    LegacyPolicyMigrationItem(
                        policy_id=policy_id,
                        status="migrated",
                        detail="dry-run conversion successful",
                        rule_id=rule_id,
                        revision=1,
                    )
                )
                continue

            if runtime is None:
                raise ValueError("runtime is required when dry_run is False")

            try:
                revision = runtime.store.create_definition(
                    organization_id=organization_id,
                    actor_user_id=actor_user_id,
                    body=body,
                    rule_id=rule_id,
                    organization_scope=scope,
                    allow_system_scope=False,
                )
            except Exception:
                revision = runtime.store.create_next_revision(
                    organization_id=organization_id,
                    actor_user_id=actor_user_id,
                    rule_id=rule_id,
                    body=body,
                    allow_system_scope=False,
                )

            if revision.status == "draft":
                revision = runtime.store.publish_revision(
                    organization_id=organization_id,
                    rule_id=rule_id,
                    revision=revision.revision,
                    allow_system_scope=False,
                )

            binding_id: str | None = None
            if create_bindings:
                binding = _build_binding(policy=policy, rule_id=rule_id, revision=revision.revision)
                binding_doc = runtime.store.create_binding(
                    organization_id=organization_id,
                    actor_user_id=actor_user_id,
                    payload=binding,
                    allow_system_scope=False,
                )
                binding_id = binding_doc.binding_id

            migrated += 1
            items.append(
                LegacyPolicyMigrationItem(
                    policy_id=policy_id,
                    status="migrated",
                    detail="created published rule revision",
                    rule_id=rule_id,
                    revision=revision.revision,
                    binding_id=binding_id,
                )
            )
        except Exception as exc:
            failed += 1
            items.append(
                LegacyPolicyMigrationItem(
                    policy_id=policy_id,
                    status="failed",
                    detail=str(exc),
                )
            )

    return LegacyPolicyMigrationReport(
        total=len(policies),
        migrated=migrated,
        skipped=skipped,
        failed=failed,
        items=items,
    )


def convert_legacy_policy(policy: Mapping[str, Any]) -> tuple[str, RuleRevisionBody] | None:
    condition_builder = _read_condition_builder(policy)
    if condition_builder is not None:
        predicate, stage = _convert_condition_group(condition_builder)
    else:
        expression = _read_condition_expression(policy)
        if not expression:
            return None
        converted = _convert_expression(expression)
        if converted is None:
            return None
        predicate, stage = converted

    action = _read_action(policy)
    reason = _read_reason(policy)
    effect = _effect_from_action(action=action, reason=reason, rule_name=str(policy.get("name") or "legacy"))
    rule_id = _rule_id_for_legacy_policy(policy)
    tags = _build_tags(policy)
    metadata = {
        "source": "legacy_policy_migration",
        "legacy_policy_id": str(policy.get("id") or ""),
        "legacy_policy_type": str(policy.get("policy_type") or ""),
        "legacy_agent_id": policy.get("agent_id"),
    }
    return (
        rule_id,
        RuleRevisionBody(
            name=str(policy.get("name") or rule_id),
            summary=str(policy.get("description") or "Migrated from legacy policy record."),
            stage=stage,
            predicate=predicate,
            effect=effect,
            tags=tags,
            metadata=metadata,
        ),
    )


def _convert_condition_group(group: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    logic = str(group.get("logic", "AND")).upper()
    predicates: list[dict[str, Any]] = []
    stage = "turn_ingress"

    for rule in list(group.get("rules") or []):
        if not isinstance(rule, Mapping):
            continue
        converted = _convert_condition_rule(rule)
        if converted is None:
            continue
        rule_predicate, rule_stage = converted
        predicates.append(rule_predicate)
        stage = _stage_priority(stage, rule_stage)

    for nested in list(group.get("groups") or []):
        if not isinstance(nested, Mapping):
            continue
        nested_predicate, nested_stage = _convert_condition_group(nested)
        predicates.append(nested_predicate)
        stage = _stage_priority(stage, nested_stage)

    if not predicates:
        raise ValueError("legacy condition builder had no supported rules")

    if len(predicates) == 1:
        return predicates[0], stage
    if logic == "OR":
        return {"kind": "any", "predicates": predicates}, stage
    return {"kind": "all", "predicates": predicates}, stage


def _convert_condition_rule(rule: Mapping[str, Any]) -> tuple[dict[str, Any], str] | None:
    variable = str(rule.get("variable") or "").strip()
    operator = str(rule.get("operator") or "").strip()
    raw_value = rule.get("value")
    if not variable or not operator:
        return None

    path = _variable_to_path(variable)
    if path is None:
        return None
    stage = _stage_from_path(path)
    case_sensitive = bool(rule.get("case_sensitive", False))

    if operator == "equals":
        return {"kind": "match", "path": path, "operator": "eq", "value": raw_value}, stage
    if operator == "not_equals":
        return {"kind": "match", "path": path, "operator": "neq", "value": raw_value}, stage
    if operator == "contains":
        return {
            "kind": "match",
            "path": path,
            "operator": "contains",
            "value": str(raw_value or ""),
            "case_sensitive": case_sensitive,
        }, stage
    if operator == "not_contains":
        return {
            "kind": "not",
            "predicate": {
                "kind": "match",
                "path": path,
                "operator": "contains",
                "value": str(raw_value or ""),
                "case_sensitive": case_sensitive,
            },
        }, stage
    if operator == "starts_with":
        value = re.escape(str(raw_value or ""))
        return {"kind": "match", "path": path, "operator": "regex", "value": f"^{value}"}, stage
    if operator == "ends_with":
        value = re.escape(str(raw_value or ""))
        return {"kind": "match", "path": path, "operator": "regex", "value": f"{value}$"}, stage
    if operator == "matches_regex":
        return {"kind": "match", "path": path, "operator": "regex", "value": str(raw_value or "")}, stage
    if operator == "greater_than":
        return {"kind": "match", "path": path, "operator": "gt", "value": raw_value}, stage
    if operator == "less_than":
        return {"kind": "match", "path": path, "operator": "lt", "value": raw_value}, stage
    if operator == "greater_than_or_equal":
        return {"kind": "match", "path": path, "operator": "gte", "value": raw_value}, stage
    if operator == "less_than_or_equal":
        return {"kind": "match", "path": path, "operator": "lte", "value": raw_value}, stage
    if operator == "in_list":
        values = list(raw_value) if isinstance(raw_value, Sequence) and not isinstance(raw_value, (str, bytes)) else [raw_value]
        return {"kind": "match", "path": path, "operator": "in", "values": values}, stage
    if operator == "not_in_list":
        values = list(raw_value) if isinstance(raw_value, Sequence) and not isinstance(raw_value, (str, bytes)) else [raw_value]
        return {"kind": "match", "path": path, "operator": "not_in", "values": values}, stage
    if operator == "between":
        values = list(raw_value) if isinstance(raw_value, Sequence) and not isinstance(raw_value, (str, bytes)) else []
        if len(values) < 2:
            return None
        return {"kind": "match", "path": path, "operator": "between", "lower": values[0], "upper": values[1]}, stage
    if operator == "time_between":
        values = list(raw_value) if isinstance(raw_value, Sequence) and not isinstance(raw_value, (str, bytes)) else []
        if len(values) < 2:
            return None
        return {
            "kind": "match",
            "path": "time.current_hour",
            "operator": "between",
            "lower": values[0],
            "upper": values[1],
        }, "turn_ingress"
    return None


def _convert_expression(expression: str) -> tuple[dict[str, Any], str] | None:
    normalized = expression.strip()
    if not normalized or normalized in {"True", "False"}:
        return None

    in_match = _RE_IN_PATTERN.match(normalized)
    if in_match:
        value = in_match.group("value")
        path = _variable_to_path(in_match.group("var"))
        if path is None:
            return None
        return {"kind": "match", "path": path, "operator": "contains", "value": value}, _stage_from_path(path)

    compare_match = _RE_COMPARE_PATTERN.match(normalized)
    if compare_match:
        op_map = {"==": "eq", "!=": "neq", ">": "gt", "<": "lt", ">=": "gte", "<=": "lte"}
        path = _variable_to_path(compare_match.group("var"))
        if path is None:
            return None
        return {
            "kind": "match",
            "path": path,
            "operator": op_map[compare_match.group("op")],
            "value": compare_match.group("value"),
        }, _stage_from_path(path)

    numeric_compare = _RE_NUMERIC_COMPARE_PATTERN.match(normalized)
    if numeric_compare:
        op_map = {">": "gt", "<": "lt", ">=": "gte", "<=": "lte"}
        path = _variable_to_path(numeric_compare.group("var"))
        if path is None:
            return None
        value = float(numeric_compare.group("value"))
        if value.is_integer():
            value = int(value)
        return {"kind": "match", "path": path, "operator": op_map[numeric_compare.group("op")], "value": value}, _stage_from_path(path)

    between_match = _RE_BETWEEN_PATTERN.match(normalized)
    if between_match:
        path = _variable_to_path(between_match.group("var"))
        if path is None:
            return None
        lower = float(between_match.group("lower"))
        upper = float(between_match.group("upper"))
        if lower.is_integer():
            lower = int(lower)
        if upper.is_integer():
            upper = int(upper)
        return {"kind": "match", "path": path, "operator": "between", "lower": lower, "upper": upper}, _stage_from_path(path)

    regex_match = _RE_REGEX_PATTERN.match(normalized)
    if regex_match:
        path = _variable_to_path(regex_match.group("var"))
        if path is None:
            return None
        return {
            "kind": "match",
            "path": path,
            "operator": "regex",
            "value": regex_match.group("pattern"),
        }, _stage_from_path(path)

    return None


def _build_binding(*, policy: Mapping[str, Any], rule_id: str, revision: int) -> RuleBindingCreate:
    legacy_policy_id = str(policy.get("id") or policy.get("name") or "legacy")
    priority = int(policy.get("priority") or 5)
    agent_id = policy.get("agent_id")
    is_active = bool(policy.get("is_active", True))
    order = max(1, min(1000, priority * 10))
    binding_scope = {
        "channels": [],
        "agent_ids": [str(agent_id)] if agent_id else [],
        "step_ids": [],
        "tool_refs": [],
        "event_types": [],
    }
    return RuleBindingCreate(
        organization_scope="organization",
        binding_id=f"legacy.policy.{_slugify(legacy_policy_id)}",
        rule_id=rule_id,
        revision=revision,
        mode="enforce" if is_active else "disabled",
        order=order,
        scope=binding_scope,
        metadata={
            "source": "legacy_policy_migration",
            "legacy_policy_id": policy.get("id"),
            "legacy_policy_name": policy.get("name"),
        },
        confirm_broad_scope=True,
    )


def _build_tags(policy: Mapping[str, Any]) -> list[str]:
    tags = ["legacy-migrated"]
    policy_type = str(policy.get("policy_type") or "").strip()
    if policy_type:
        tags.append(f"policy-type:{policy_type}")
    return tags


def _read_condition_builder(policy: Mapping[str, Any]) -> Mapping[str, Any] | None:
    rules = policy.get("rules")
    if isinstance(rules, Mapping) and isinstance(rules.get("condition_builder"), Mapping):
        return dict(rules["condition_builder"])
    direct = policy.get("condition_builder")
    if isinstance(direct, Mapping):
        return dict(direct)
    return None


def _read_condition_expression(policy: Mapping[str, Any]) -> str | None:
    conditions = policy.get("conditions")
    if isinstance(conditions, Mapping) and isinstance(conditions.get("expression"), str):
        return conditions["expression"]
    rules = policy.get("rules")
    if isinstance(rules, Mapping) and isinstance(rules.get("condition"), str):
        return rules["condition"]
    direct = policy.get("condition")
    if isinstance(direct, str):
        return direct
    return None


def _read_action(policy: Mapping[str, Any]) -> str:
    actions = policy.get("actions")
    if isinstance(actions, Mapping) and isinstance(actions.get("action"), str):
        return actions["action"].strip().lower()
    action = policy.get("action")
    if isinstance(action, str):
        return action.strip().lower()
    return "warn"


def _read_reason(policy: Mapping[str, Any]) -> str:
    actions = policy.get("actions")
    if isinstance(actions, Mapping) and isinstance(actions.get("reason"), str):
        return actions["reason"].strip()
    reason = policy.get("reason")
    if isinstance(reason, str):
        return reason.strip()
    return "Migrated legacy policy rule triggered."


def _effect_from_action(*, action: str, reason: str, rule_name: str) -> dict[str, Any]:
    code = f"legacy_{_slugify(rule_name) or 'rule'}_{action or 'warn'}"
    if action == "block":
        return {"kind": "block", "code": code, "message": reason}
    if action == "warn":
        return {"kind": "warn", "code": code, "message": reason}
    if action == "escalate":
        return {"kind": "require_confirmation", "code": code, "message": reason}
    return {"kind": "trace", "code": code, "message": reason}


def _rule_id_for_legacy_policy(policy: Mapping[str, Any]) -> str:
    policy_type = _slugify(str(policy.get("policy_type") or "legacy"))
    identifier = _slugify(str(policy.get("id") or policy.get("name") or "policy"))
    return f"legacy.{policy_type}.{identifier}"


def _variable_to_path(variable: str) -> str | None:
    normalized = variable.strip().lower().replace(".lower()", "")
    if normalized in _VARIABLE_PATHS:
        return _VARIABLE_PATHS[normalized]
    return _VARIABLE_PATHS.get(normalized.split(".")[-1])


def _stage_from_path(path: str) -> str:
    if path.startswith("tool."):
        return "before_tool"
    if path.startswith("metadata.agent_response"):
        return "before_emit"
    return "turn_ingress"


def _stage_priority(current: str, candidate: str) -> str:
    order = {
        "turn_ingress": 0,
        "before_tool": 1,
        "after_tool": 2,
        "before_response": 3,
        "before_emit": 4,
    }
    return candidate if order.get(candidate, 0) > order.get(current, 0) else current


def _slugify(value: str) -> str:
    lowered = value.strip().lower()
    if not lowered:
        return ""
    sanitized = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    return sanitized[:100]
