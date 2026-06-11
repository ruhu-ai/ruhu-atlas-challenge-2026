from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

Channel = Literal["phone", "whatsapp", "web_chat", "web_widget", "browser"]

RuleStage = Literal[
    "turn_ingress",
    "before_response",
    "before_tool",
    "after_tool",
    "before_emit",
]
RuleBindingMode = Literal["enforce", "shadow", "disabled"]
PredicateOperator = Literal[
    "eq",
    "neq",
    "contains",
    "regex",
    "in",
    "not_in",
    "gt",
    "gte",
    "lt",
    "lte",
    "exists",
    "between",
]

_ALLOWED_PATH_ROOTS = frozenset({"conversation", "turn", "tool", "facts", "metadata", "time"})
_PATH_SEGMENT_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_path(path: str) -> str:
    normalized = path.strip()
    if not normalized:
        raise ValueError("rule predicate path must not be empty")
    parts = normalized.split(".")
    if parts[0] not in _ALLOWED_PATH_ROOTS:
        raise ValueError(
            "rule predicate path must start with one of: "
            + ", ".join(sorted(_ALLOWED_PATH_ROOTS))
        )
    for part in parts:
        if not _PATH_SEGMENT_PATTERN.fullmatch(part):
            raise ValueError(f"invalid path segment: {part}")
    return normalized


class RuleConversationContext(BaseModel):
    organization_id: str | None = None
    conversation_id: str | None = None
    agent_id: str | None = None
    step_id: str | None = None
    channel: Channel | None = None
    turn_count: int = 0


class RuleTurnContext(BaseModel):
    event_type: str | None = None
    text: str | None = None
    text_length: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def populate_text_length(self) -> "RuleTurnContext":
        if self.text_length is None and self.text is not None:
            self.text_length = len(self.text)
        return self


class RuleToolContext(BaseModel):
    ref: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    outcome: str | None = None


class RuleTimeContext(BaseModel):
    current_hour: int | None = None
    current_day: str | None = None

    @model_validator(mode="after")
    def populate_defaults(self) -> "RuleTimeContext":
        now = datetime.now(timezone.utc)
        if self.current_hour is None:
            self.current_hour = now.hour
        if self.current_day is None:
            self.current_day = now.strftime("%A")
        return self


class RuleEvaluationContext(BaseModel):
    stage: RuleStage
    conversation: RuleConversationContext = Field(default_factory=RuleConversationContext)
    turn: RuleTurnContext = Field(default_factory=RuleTurnContext)
    tool: RuleToolContext = Field(default_factory=RuleToolContext)
    facts: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    time: RuleTimeContext = Field(default_factory=RuleTimeContext)


class MatchPredicate(BaseModel):
    kind: Literal["match"] = "match"
    path: str
    operator: PredicateOperator
    value: Any | None = None
    values: list[Any] = Field(default_factory=list)
    lower: Any | None = None
    upper: Any | None = None
    case_sensitive: bool = False

    @model_validator(mode="after")
    def validate_predicate(self) -> "MatchPredicate":
        self.path = _validate_path(self.path)
        if self.operator in {"in", "not_in"} and not self.values:
            raise ValueError(f"{self.operator} predicate requires values")
        if self.operator == "between":
            if self.lower is None or self.upper is None:
                raise ValueError("between predicate requires lower and upper")
        elif self.operator != "exists" and self.operator not in {"in", "not_in"} and self.value is None:
            raise ValueError(f"{self.operator} predicate requires value")
        return self


class AllPredicate(BaseModel):
    kind: Literal["all"] = "all"
    predicates: list["RulePredicate"] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_predicates(self) -> "AllPredicate":
        if not self.predicates:
            raise ValueError("all predicate requires at least one child predicate")
        return self


class AnyPredicate(BaseModel):
    kind: Literal["any"] = "any"
    predicates: list["RulePredicate"] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_predicates(self) -> "AnyPredicate":
        if not self.predicates:
            raise ValueError("any predicate requires at least one child predicate")
        return self


class NotPredicate(BaseModel):
    kind: Literal["not"] = "not"
    predicate: "RulePredicate"


RulePredicate = Annotated[
    MatchPredicate | AllPredicate | AnyPredicate | NotPredicate,
    Field(discriminator="kind"),
]
AllPredicate.model_rebuild()
AnyPredicate.model_rebuild()
NotPredicate.model_rebuild()


class BlockEffect(BaseModel):
    kind: Literal["block"] = "block"
    code: str
    message: str


class WarnEffect(BaseModel):
    kind: Literal["warn"] = "warn"
    code: str
    message: str


class RequireConfirmationEffect(BaseModel):
    kind: Literal["require_confirmation"] = "require_confirmation"
    code: str
    message: str


class SuppressToolEffect(BaseModel):
    kind: Literal["suppress_tool"] = "suppress_tool"
    code: str
    message: str
    tool_ref: str | None = None


class TraceEffect(BaseModel):
    kind: Literal["trace"] = "trace"
    code: str
    message: str | None = None


RuleEffect = Annotated[
    BlockEffect | WarnEffect | RequireConfirmationEffect | SuppressToolEffect | TraceEffect,
    Field(discriminator="kind"),
]


class RuleDefinition(BaseModel):
    rule_id: str
    revision: int = Field(default=1, ge=1)
    name: str
    summary: str
    stage: RuleStage
    predicate: RulePredicate | None = None
    expression: str | None = None
    effect: RuleEffect
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _compile_expression(self) -> "RuleDefinition":
        if self.expression and self.predicate is None:
            from .rules_dsl import compile_expression
            try:
                compiled = compile_expression(self.expression)
                object.__setattr__(self, "predicate", compiled)
            except Exception as exc:
                raise ValueError(f"failed to compile expression {self.expression!r}: {exc}") from exc
        elif self.predicate is None and self.expression is None:
            raise ValueError("One of predicate or expression must be provided")
        return self


class RuleLibrary(BaseModel):
    library_id: str
    version: str
    rules: list[RuleDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_rules(self) -> "RuleLibrary":
        seen: set[tuple[str, int]] = set()
        for rule in self.rules:
            key = (rule.rule_id, rule.revision)
            if key in seen:
                raise ValueError(f"duplicate rule revision: {rule.rule_id}@{rule.revision}")
            seen.add(key)
        return self

    def rule_index(self) -> dict[tuple[str, int], RuleDefinition]:
        return {(rule.rule_id, rule.revision): rule for rule in self.rules}


class RuleBindingScope(BaseModel):
    channels: list[Channel] = Field(default_factory=list)
    agent_ids: list[str] = Field(default_factory=list)
    step_ids: list[str] = Field(default_factory=list)
    tool_refs: list[str] = Field(default_factory=list)
    event_types: list[str] = Field(default_factory=list)

    def matches(self, context: RuleEvaluationContext) -> bool:
        if self.channels:
            if context.conversation.channel not in self.channels:
                return False
        if self.agent_ids:
            if context.conversation.agent_id not in self.agent_ids:
                return False
        if self.step_ids:
            if context.conversation.step_id not in self.step_ids:
                return False
        if self.tool_refs:
            if context.tool.ref not in self.tool_refs:
                return False
        if self.event_types:
            if context.turn.event_type not in self.event_types:
                return False
        return True


class RuleBinding(BaseModel):
    binding_id: str
    rule_id: str
    revision: int = Field(default=1, ge=1)
    mode: RuleBindingMode = "enforce"
    order: int = Field(default=100, ge=1)
    scope: RuleBindingScope = Field(default_factory=RuleBindingScope)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuleProgram(BaseModel):
    library: RuleLibrary
    bindings: list[RuleBinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_bindings(self) -> "RuleProgram":
        rule_index = self.library.rule_index()
        for binding in self.bindings:
            if (binding.rule_id, binding.revision) not in rule_index:
                raise ValueError(
                    f"binding {binding.binding_id} references unknown rule "
                    f"{binding.rule_id}@{binding.revision}"
                )
        return self


class RuleMatch(BaseModel):
    binding_id: str
    rule_id: str
    revision: int
    rule_name: str
    mode: RuleBindingMode
    effect: RuleEffect


class RuleTrace(BaseModel):
    binding_id: str
    rule_id: str
    revision: int
    outcome: Literal["skipped", "no_match", "matched", "shadow_match", "error"]
    mode: RuleBindingMode
    effect_kind: str | None = None
    detail: str | None = None


class RuleDecision(BaseModel):
    traces: list[RuleTrace] = Field(default_factory=list)
    matched_rules: list[RuleMatch] = Field(default_factory=list)
    terminal_effect: RuleEffect | None = None


class RuleStageDecision(BaseModel):
    stage: RuleStage
    traces: list[RuleTrace] = Field(default_factory=list)
    matched_rules: list[RuleMatch] = Field(default_factory=list)
    terminal_effect: RuleEffect | None = None

    @classmethod
    def from_decision(cls, *, stage: RuleStage, decision: RuleDecision) -> "RuleStageDecision":
        return cls(
            stage=stage,
            traces=[trace.model_copy(deep=True) for trace in decision.traces],
            matched_rules=[match.model_copy(deep=True) for match in decision.matched_rules],
            terminal_effect=None if decision.terminal_effect is None else decision.terminal_effect.model_copy(deep=True),
        )


class RuntimeRulesTrace(BaseModel):
    evaluations: list[RuleStageDecision] = Field(default_factory=list)

    def append_decision(self, *, stage: RuleStage, decision: RuleDecision) -> None:
        self.evaluations.append(RuleStageDecision.from_decision(stage=stage, decision=decision))


class PendingRuleConfirmation(BaseModel):
    confirmation_token: str
    conversation_id: str
    step_id: str
    tool_ref: str
    resolved_args_json: dict[str, Any] = Field(default_factory=dict)
    binding_id: str
    rule_id: str
    rule_revision: int = Field(ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    channel: Channel | None = None
    event_type: str | None = None


def load_rule_program(path: str | Path) -> RuleProgram:
    content = Path(path).read_text(encoding="utf-8")
    return RuleProgram.model_validate(json.loads(content))


def dump_rule_program(program: RuleProgram) -> str:
    return json.dumps(program.model_dump(mode="json"), indent=2, sort_keys=True)


def starter_rule_program() -> RuleProgram:
    return RuleProgram(
        library=RuleLibrary(
            library_id="ruhu.starter.rules",
            version="2026-04-10",
            rules=[
                RuleDefinition(
                    rule_id="rule.turn.payment_card_data_block",
                    name="Block payment card data collection",
                    summary="Block payment card details in user turns on public conversation surfaces.",
                    stage="turn_ingress",
                    predicate=AnyPredicate(
                        predicates=[
                            MatchPredicate(path="turn.text", operator="contains", value="credit card"),
                            MatchPredicate(path="turn.text", operator="contains", value="card number"),
                            MatchPredicate(path="turn.text", operator="contains", value="cvv"),
                            MatchPredicate(
                                path="turn.text",
                                operator="regex",
                                value=r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",
                            ),
                        ]
                    ),
                    effect=BlockEffect(
                        code="payment_card_data_detected",
                        message="Payment card data cannot be collected in this channel.",
                    ),
                    tags=["compliance", "pci", "starter", "legacy-carried-forward"],
                ),
                RuleDefinition(
                    rule_id="rule.turn.ssn_data_block",
                    name="Block social security number collection",
                    summary="Block SSN collection when a turn contains SSN-shaped data and social security intent.",
                    stage="turn_ingress",
                    predicate=AllPredicate(
                        predicates=[
                            MatchPredicate(
                                path="turn.text",
                                operator="regex",
                                value=r"\b\d{3}[\s-]?\d{2}[\s-]?\d{4}\b",
                            ),
                            AnyPredicate(
                                predicates=[
                                    MatchPredicate(path="turn.text", operator="contains", value="social security"),
                                    MatchPredicate(path="turn.text", operator="contains", value="ssn"),
                                ]
                            ),
                        ]
                    ),
                    effect=BlockEffect(
                        code="ssn_detected",
                        message="Social security numbers cannot be collected in this channel.",
                    ),
                    tags=["compliance", "privacy", "starter", "legacy-carried-forward"],
                ),
                RuleDefinition(
                    rule_id="rule.turn.max_user_message_length",
                    name="Limit excessively long user turns",
                    summary="Block user turns that exceed the supported message length budget.",
                    stage="turn_ingress",
                    predicate=MatchPredicate(path="turn.text_length", operator="gt", value=500),
                    effect=BlockEffect(
                        code="turn_too_long",
                        message="Message too long. Please keep your message under 500 characters.",
                    ),
                    tags=["safety", "runtime-budget", "starter", "legacy-carried-forward"],
                ),
                RuleDefinition(
                    rule_id="rule.turn.unsafe_language_warning",
                    name="Warn on abusive language",
                    summary="Warn on abusive language instead of hard blocking the conversation.",
                    stage="turn_ingress",
                    predicate=MatchPredicate(
                        path="turn.text",
                        operator="regex",
                        value=r"\b(bullshit|fuck|shit|damn|bitch|asshole|bastard)\b",
                    ),
                    effect=WarnEffect(
                        code="abusive_language_detected",
                        message="De-escalate the conversation and prefer a calm clarification or human handoff.",
                    ),
                    tags=["safety", "de-escalation", "starter", "legacy-updated"],
                ),
                RuleDefinition(
                    rule_id="rule.tool.after_hours_handoff_suppression",
                    name="Suppress handoff outside business hours",
                    summary="Suppress human handoff attempts when staffed support is unavailable.",
                    stage="before_tool",
                    predicate=NotPredicate(
                        predicate=MatchPredicate(path="time.current_hour", operator="between", lower=9, upper=16)
                    ),
                    effect=SuppressToolEffect(
                        code="handoff_outside_business_hours",
                        message="Human handoff is unavailable outside business hours.",
                        tool_ref="human_handoff",
                    ),
                    tags=["operations", "tool-guardrail", "starter", "legacy-updated"],
                ),
                RuleDefinition(
                    rule_id="rule.tool.high_value_transaction_confirmation",
                    name="Require confirmation for high-value transactions",
                    summary="Require explicit confirmation before processing unusually large transactions.",
                    stage="before_tool",
                    predicate=MatchPredicate(path="tool.args.amount", operator="gt", value=10000),
                    effect=RequireConfirmationEffect(
                        code="high_value_transaction_requires_confirmation",
                        message="This transaction requires explicit approval before execution.",
                    ),
                    tags=["business", "approval", "starter", "legacy-updated"],
                ),
                RuleDefinition(
                    rule_id="rule.tool.execution_rate_limit",
                    name="Rate limit repeated tool execution",
                    summary="Suppress tool execution after repeated attempts in the same conversation.",
                    stage="before_tool",
                    predicate=MatchPredicate(path="metadata.tool_execution_count", operator="gte", value=10),
                    effect=SuppressToolEffect(
                        code="tool_execution_rate_limited",
                        message="Too many tool executions in this conversation. Escalate or stop retrying.",
                    ),
                    tags=["safety", "tooling", "starter", "legacy-carried-forward"],
                ),
            ],
        ),
        bindings=[
            RuleBinding(
                binding_id="bind.turn.payment_card_data_block.public_surfaces",
                rule_id="rule.turn.payment_card_data_block",
                order=10,
                scope=RuleBindingScope(channels=["phone", "whatsapp", "web_chat", "web_widget"]),
            ),
            RuleBinding(
                binding_id="bind.turn.ssn_data_block.public_surfaces",
                rule_id="rule.turn.ssn_data_block",
                order=11,
                scope=RuleBindingScope(channels=["phone", "whatsapp", "web_chat", "web_widget"]),
            ),
            RuleBinding(
                binding_id="bind.turn.max_user_message_length.default",
                rule_id="rule.turn.max_user_message_length",
                order=20,
            ),
            RuleBinding(
                binding_id="bind.turn.unsafe_language_warning.default",
                rule_id="rule.turn.unsafe_language_warning",
                order=30,
            ),
            RuleBinding(
                binding_id="bind.tool.after_hours_handoff_suppression.default",
                rule_id="rule.tool.after_hours_handoff_suppression",
                order=40,
                scope=RuleBindingScope(tool_refs=["human_handoff"]),
            ),
            RuleBinding(
                binding_id="bind.tool.high_value_transaction_confirmation.default",
                rule_id="rule.tool.high_value_transaction_confirmation",
                order=50,
                scope=RuleBindingScope(tool_refs=["process_transaction"]),
            ),
            RuleBinding(
                binding_id="bind.tool.execution_rate_limit.default",
                rule_id="rule.tool.execution_rate_limit",
                order=60,
            ),
        ],
    )


class RuleEngine:
    def evaluate(self, program: RuleProgram, context: RuleEvaluationContext) -> RuleDecision:
        rule_index = program.library.rule_index()
        traces: list[RuleTrace] = []
        matched_rules: list[RuleMatch] = []
        terminal_effect: RuleEffect | None = None
        confirmed_rule_binding_ids = {
            str(item)
            for item in list(context.metadata.get("confirmed_rule_binding_ids") or [])
            if str(item).strip()
        }

        ordered_bindings = sorted(program.bindings, key=lambda item: (item.order, item.binding_id))
        for binding in ordered_bindings:
            if binding.mode == "disabled":
                traces.append(
                    RuleTrace(
                        binding_id=binding.binding_id,
                        rule_id=binding.rule_id,
                        revision=binding.revision,
                        outcome="skipped",
                        mode=binding.mode,
                        detail="binding disabled",
                    )
                )
                continue

            rule = rule_index[(binding.rule_id, binding.revision)]
            if rule.stage != context.stage or not binding.scope.matches(context):
                traces.append(
                    RuleTrace(
                        binding_id=binding.binding_id,
                        rule_id=rule.rule_id,
                        revision=rule.revision,
                        outcome="skipped",
                        mode=binding.mode,
                    )
                )
                continue
            if rule.effect.kind == "require_confirmation" and binding.binding_id in confirmed_rule_binding_ids:
                traces.append(
                    RuleTrace(
                        binding_id=binding.binding_id,
                        rule_id=rule.rule_id,
                        revision=rule.revision,
                        outcome="skipped",
                        mode=binding.mode,
                        detail="binding already confirmed",
                    )
                )
                continue

            try:
                matched = _evaluate_predicate(rule.predicate, context)
            except Exception as exc:  # pragma: no cover - defensive path
                traces.append(
                    RuleTrace(
                        binding_id=binding.binding_id,
                        rule_id=rule.rule_id,
                        revision=rule.revision,
                        outcome="error",
                        mode=binding.mode,
                        detail=str(exc),
                    )
                )
                continue

            if not matched:
                traces.append(
                    RuleTrace(
                        binding_id=binding.binding_id,
                        rule_id=rule.rule_id,
                        revision=rule.revision,
                        outcome="no_match",
                        mode=binding.mode,
                    )
                )
                continue

            outcome = "shadow_match" if binding.mode == "shadow" else "matched"
            traces.append(
                RuleTrace(
                    binding_id=binding.binding_id,
                    rule_id=rule.rule_id,
                    revision=rule.revision,
                    outcome=outcome,
                    mode=binding.mode,
                    effect_kind=rule.effect.kind,
                )
            )
            if binding.mode == "shadow":
                continue

            matched_rules.append(
                RuleMatch(
                    binding_id=binding.binding_id,
                    rule_id=rule.rule_id,
                    revision=rule.revision,
                    rule_name=rule.name,
                    mode=binding.mode,
                    effect=rule.effect,
                )
            )
            if _effect_is_terminal(rule.effect):
                terminal_effect = rule.effect
                break

        return RuleDecision(
            traces=traces,
            matched_rules=matched_rules,
            terminal_effect=terminal_effect,
        )


def _resolve_path(context: RuleEvaluationContext, path: str) -> Any:
    current: Any = context
    for segment in path.split("."):
        if isinstance(current, BaseModel):
            current = getattr(current, segment, None)
        elif isinstance(current, dict):
            current = current.get(segment)
        elif isinstance(current, (list, tuple)) and segment.isdigit():
            index = int(segment)
            current = current[index] if 0 <= index < len(current) else None
        else:
            current = getattr(current, segment, None)
        if current is None:
            return None
    return current


def _normalize_for_compare(value: Any, *, case_sensitive: bool) -> Any:
    if isinstance(value, str) and not case_sensitive:
        return value.lower()
    return value


def _evaluate_predicate(predicate: RulePredicate, context: RuleEvaluationContext) -> bool:
    if isinstance(predicate, AllPredicate):
        return all(_evaluate_predicate(item, context) for item in predicate.predicates)
    if isinstance(predicate, AnyPredicate):
        return any(_evaluate_predicate(item, context) for item in predicate.predicates)
    if isinstance(predicate, NotPredicate):
        return not _evaluate_predicate(predicate.predicate, context)

    actual = _resolve_path(context, predicate.path)
    operator = predicate.operator
    if operator == "exists":
        return actual is not None

    if operator in {"in", "not_in"}:
        normalized_actual = _normalize_for_compare(actual, case_sensitive=predicate.case_sensitive)
        normalized_values = [
            _normalize_for_compare(item, case_sensitive=predicate.case_sensitive) for item in predicate.values
        ]
        present = normalized_actual in normalized_values
        return present if operator == "in" else not present

    if operator == "between":
        return actual is not None and predicate.lower <= actual <= predicate.upper

    expected = predicate.value
    if operator == "contains":
        if isinstance(actual, str) and isinstance(expected, str):
            return _normalize_for_compare(expected, case_sensitive=predicate.case_sensitive) in _normalize_for_compare(
                actual,
                case_sensitive=predicate.case_sensitive,
            )
        if isinstance(actual, (list, tuple, set)):
            normalized_expected = _normalize_for_compare(expected, case_sensitive=predicate.case_sensitive)
            normalized_actual = [
                _normalize_for_compare(item, case_sensitive=predicate.case_sensitive) for item in actual
            ]
            return normalized_expected in normalized_actual
        return False

    if operator == "regex":
        if not isinstance(actual, str):
            return False
        flags = 0 if predicate.case_sensitive else re.IGNORECASE
        return re.search(str(expected), actual, flags) is not None

    normalized_actual = _normalize_for_compare(actual, case_sensitive=predicate.case_sensitive)
    normalized_expected = _normalize_for_compare(expected, case_sensitive=predicate.case_sensitive)
    if operator == "eq":
        return normalized_actual == normalized_expected
    if operator == "neq":
        return normalized_actual != normalized_expected
    if operator == "gt":
        return actual is not None and actual > expected
    if operator == "gte":
        return actual is not None and actual >= expected
    if operator == "lt":
        return actual is not None and actual < expected
    if operator == "lte":
        return actual is not None and actual <= expected
    raise ValueError(f"unsupported predicate operator: {operator}")


def _effect_is_terminal(effect: RuleEffect) -> bool:
    return effect.kind in {"block", "require_confirmation", "suppress_tool"}
