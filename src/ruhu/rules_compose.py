"""Natural-language policy authoring compiler (Doc 04).

Translates plain-language policy descriptions into the existing rules DSL
without replacing it. The pipeline is:

    plain language -> structured intent -> RuleRevisionBody + binding scope

The compiler is intentionally deterministic: when a phrase is underspecified
it raises an ambiguity instead of guessing silently.

Step-native discipline: the compose surface uses ``agent_ids`` and
``step_ids`` vocabulary. ``scenario_ids`` is accepted as advisory metadata
only until runtime enforcement grows that dimension.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from .rules import (
    BlockEffect,
    Channel,
    RequireConfirmationEffect,
    RuleBindingScope,
    RuleEffect,
    RuleStage,
    SuppressToolEffect,
    TraceEffect,
    WarnEffect,
)
from .rules_dsl import compile_expression
from .rules_store import RuleRevisionBody


_EFFECT_VERBS: dict[str, str] = {
    "block": "block",
    "deny": "block",
    "prohibit": "block",
    "disallow": "block",
    "reject": "block",
    "stop": "block",
    "prevent": "block",
    "forbid": "block",
    "warn": "warn",
    "flag": "warn",
    "alert": "warn",
    "caution": "warn",
    "require approval": "require_confirmation",
    "require confirmation": "require_confirmation",
    "needs approval": "require_confirmation",
    "needs confirmation": "require_confirmation",
    "ask for approval": "require_confirmation",
    "ask for confirmation": "require_confirmation",
    "explicit ok": "require_confirmation",
    "suppress": "suppress_tool",
    "skip": "suppress_tool",
    "disable": "suppress_tool",
    "log": "trace",
    "audit": "trace",
    "trace": "trace",
}

_CHANNEL_TOKENS: dict[str, Channel] = {
    "phone": "phone",
    "voice": "phone",
    "call": "phone",
    "whatsapp": "whatsapp",
    "wa": "whatsapp",
    "web chat": "web_chat",
    "web_chat": "web_chat",
    "webchat": "web_chat",
    "chat": "web_chat",
    "widget": "web_widget",
    "web widget": "web_widget",
    "browser": "browser",
}

_BUSINESS_HOURS_DEFAULT = (9, 16)
_DEFAULT_RULE_NAME = "Composed policy"

_NUMBER_PATTERN = re.compile(r"\$?\s*([\d,]+(?:\.\d+)?)")
_QUOTED_PATTERN = re.compile(r"[\"\u201c\u201d]([^\"\u201c\u201d]+)[\"\u201c\u201d]")
_NAME_BEFORE_TOOL_PATTERN = re.compile(
    r"`?([a-zA-Z][\w\.\-]*)`?\s+(?:tool|action)\b",
    re.IGNORECASE,
)
_USING_TOOL_PATTERN = re.compile(
    r"(?:using|calling|invoking|when calling|when using|when invoking|for the)\s+`?([a-zA-Z][\w\.\-]*)`?\s*(?:tool|action)?",
    re.IGNORECASE,
)
_TOOL_LABELED_PATTERN = re.compile(
    r"(?:tool|action)[\s:]+`?([a-zA-Z][\w\.\-]+)`?",
    re.IGNORECASE,
)
_GENERIC_TOOL_WORDS = {"the", "a", "an", "this", "that", "any", "outside", "inside", "tool", "action"}
_STEP_PATTERN = re.compile(
    r"(?:in|during|inside|on)\s+(?:the\s+)?step\s+`?([a-zA-Z][\w\.\-]*)`?",
    re.IGNORECASE,
)
_AGENT_PATTERN = re.compile(
    r"(?:in|for|on)\s+(?:the\s+)?agent\s+`?([a-zA-Z][\w\.\-]*)`?",
    re.IGNORECASE,
)
_SCENARIO_PATTERN = re.compile(
    r"(?:in|for|on)\s+(?:the\s+)?scenario\s+`?([a-zA-Z][\w\.\-]*)`?",
    re.IGNORECASE,
)
_LONGER_THAN_PATTERN = re.compile(
    r"(?:longer than|more than|over|exceeds?|exceeding|above)\s+(\d+)\s+(characters?|chars?|messages?|turns?)",
    re.IGNORECASE,
)


class ComposeBindingScope(BaseModel):
    """Step-native binding scope used by the compose surface.

    ``scenario_ids`` remains an advisory field until scenario-scoped
    enforcement lands.
    """

    channels: list[Channel] = Field(default_factory=list)
    agent_ids: list[str] = Field(default_factory=list)
    scenario_ids: list[str] = Field(default_factory=list)
    step_ids: list[str] = Field(default_factory=list)
    tool_refs: list[str] = Field(default_factory=list)
    event_types: list[str] = Field(default_factory=list)

    def to_persisted_scope(self) -> RuleBindingScope:
        return RuleBindingScope(
            channels=list(self.channels),
            agent_ids=list(self.agent_ids),
            step_ids=list(self.step_ids),
            tool_refs=list(self.tool_refs),
            event_types=list(self.event_types),
        )

    @classmethod
    def from_persisted_scope(cls, scope: RuleBindingScope) -> "ComposeBindingScope":
        return cls(
            channels=list(scope.channels),
            agent_ids=list(scope.agent_ids),
            scenario_ids=[],
            step_ids=list(scope.step_ids),
            tool_refs=list(scope.tool_refs),
            event_types=list(scope.event_types),
        )


class ComposeAmbiguity(BaseModel):
    """A specific underspecification the author should resolve."""

    code: str
    message: str
    hint: str | None = None


ComposeOutcome = Literal["ready", "needs_clarification", "unsupported"]


class ComposePolicyRequest(BaseModel):
    """User-supplied natural-language policy description."""

    text: str
    rule_id_hint: str | None = None
    suggested_tags: list[str] = Field(default_factory=list)


class ComposePolicyProposal(BaseModel):
    """Compiler output: structured proposal plus generated DSL artifacts."""

    outcome: ComposeOutcome
    summary: str
    rule_body: RuleRevisionBody | None = None
    expression: str | None = None
    binding_scope: ComposeBindingScope = Field(default_factory=ComposeBindingScope)
    affected_tags: list[str] = Field(default_factory=list)
    ambiguities: list[ComposeAmbiguity] = Field(default_factory=list)
    example_match: str | None = None
    example_no_match: str | None = None


class ComposeExplainRequest(BaseModel):
    """Render an existing RuleRevisionBody back into plain English."""

    rule_body: RuleRevisionBody
    binding_scope: ComposeBindingScope | None = None


class ComposeExplainResponse(BaseModel):
    explanation: str
    expression: str | None = None


class _Intent(BaseModel):
    effect_kind: Literal["block", "warn", "require_confirmation", "suppress_tool", "trace"] | None = None
    stage: RuleStage | None = None
    expression_parts: list[str] = Field(default_factory=list)
    tool_ref: str | None = None
    step_id: str | None = None
    agent_id: str | None = None
    scenario_id: str | None = None
    channel: Channel | None = None
    quoted_phrases: list[str] = Field(default_factory=list)
    has_amount: bool = False
    amount_value: float | int | None = None
    has_business_hours: bool = False
    after_hours: bool = False
    has_text_length: bool = False
    text_length_threshold: int | None = None
    ambiguities: list[ComposeAmbiguity] = Field(default_factory=list)


def compile_policy(request: ComposePolicyRequest) -> ComposePolicyProposal:
    """Compile a natural-language policy into a structured proposal."""
    text = (request.text or "").strip()
    if not text:
        return ComposePolicyProposal(
            outcome="unsupported",
            summary="Empty policy text.",
            ambiguities=[
                ComposeAmbiguity(
                    code="empty_text",
                    message="Policy text was empty.",
                    hint="Describe the rule in plain language, for example: 'block credit card numbers in chat'.",
                )
            ],
        )

    intent = _interpret(text)
    if intent.effect_kind is None:
        intent.ambiguities.append(
            ComposeAmbiguity(
                code="effect_unclear",
                message="Could not infer the policy effect (block, warn, require approval, suppress, trace).",
                hint="Use a verb like 'block', 'warn', 'require approval', or 'log'.",
            )
        )

    if not intent.expression_parts:
        intent.ambiguities.append(
            ComposeAmbiguity(
                code="condition_unclear",
                message="Could not infer the policy condition.",
                hint="Specify what should match: a phrase in quotes, an amount threshold, a tool, or a time window.",
            )
        )

    if intent.effect_kind in {"block", "warn", "trace"} and intent.stage is None:
        intent.stage = "turn_ingress"
    if intent.effect_kind in {"require_confirmation", "suppress_tool"} and intent.stage is None:
        intent.stage = "before_tool"
        if intent.tool_ref is None:
            intent.ambiguities.append(
                ComposeAmbiguity(
                    code="tool_ref_missing",
                    message="This policy targets tool execution but no tool was named.",
                    hint="Include the tool, for example: 'when calling refund tool' or 'for tool process_transaction'.",
                )
            )

    expression = " and ".join(intent.expression_parts) if intent.expression_parts else None
    if expression is not None:
        try:
            compile_expression(expression)
        except Exception as exc:  # noqa: BLE001
            intent.ambiguities.append(
                ComposeAmbiguity(
                    code="expression_invalid",
                    message=f"Generated DSL did not compile: {exc}",
                    hint="Edit the generated DSL directly or rephrase the policy.",
                )
            )

    if intent.effect_kind is None or expression is None or intent.stage is None:
        return ComposePolicyProposal(
            outcome="needs_clarification",
            summary=_summary_from_intent(text, intent),
            expression=expression,
            binding_scope=_scope_from_intent(intent),
            affected_tags=list(request.suggested_tags),
            ambiguities=intent.ambiguities,
        )

    rule_id = request.rule_id_hint or _suggest_rule_id(intent, expression)
    name = _suggest_name(intent)
    summary = _summary_from_intent(text, intent)
    effect = _effect_from_intent(intent, summary=summary)
    tags = sorted(set(["composed", *request.suggested_tags, intent.effect_kind]))

    metadata = {
        "compose_source": "natural_language",
        "compose_text": text,
        "compose_rule_id_hint": rule_id,
    }
    if intent.scenario_id is not None:
        metadata["compose_scope_advisory"] = {"scenario_ids": [intent.scenario_id]}

    body = RuleRevisionBody(
        name=name,
        summary=summary,
        stage=intent.stage,
        expression=expression,
        effect=effect,
        tags=tags,
        metadata=metadata,
    )

    proposal = ComposePolicyProposal(
        outcome="ready" if not intent.ambiguities else "needs_clarification",
        summary=summary,
        rule_body=body,
        expression=expression,
        binding_scope=_scope_from_intent(intent),
        affected_tags=tags,
        ambiguities=intent.ambiguities,
    )
    proposal.example_match = _build_example_match(intent)
    proposal.example_no_match = _build_example_no_match(intent)
    return proposal


def explain_policy(request: ComposeExplainRequest) -> ComposeExplainResponse:
    """Render a RuleRevisionBody back into a plain-English explanation."""
    body = request.rule_body
    parts: list[str] = []
    effect_phrase = _effect_phrase(body.effect)
    parts.append(effect_phrase)
    if body.expression:
        parts.append(f"when {body.expression.strip()}")
    parts.append(f"at the {body.stage.replace('_', ' ')} stage")

    scope = request.binding_scope
    if scope is not None:
        scope_chunks: list[str] = []
        if scope.channels:
            scope_chunks.append(f"on {', '.join(scope.channels)}")
        if scope.tool_refs:
            scope_chunks.append(f"for tool {', '.join(scope.tool_refs)}")
        if scope.step_ids:
            scope_chunks.append(f"in step {', '.join(scope.step_ids)}")
        if scope.scenario_ids:
            scope_chunks.append(f"in scenario {', '.join(scope.scenario_ids)} (advisory)")
        if scope.agent_ids:
            scope_chunks.append(f"on agent {', '.join(scope.agent_ids)}")
        if scope_chunks:
            parts.append("(" + "; ".join(scope_chunks) + ")")

    return ComposeExplainResponse(
        explanation=" ".join(parts).strip().rstrip(".") + ".",
        expression=body.expression,
    )


def _interpret(text: str) -> _Intent:
    intent = _Intent()
    lowered = text.lower()

    intent.effect_kind = _detect_effect(lowered)  # type: ignore[assignment]
    intent.tool_ref = _detect_tool(text)
    intent.step_id = _detect_step(text)
    intent.agent_id = _detect_agent(text)
    intent.scenario_id = _detect_scenario(text)
    intent.channel = _detect_channel(lowered)
    intent.quoted_phrases = _QUOTED_PATTERN.findall(text)

    for phrase in intent.quoted_phrases:
        sanitized = phrase.replace('"', '\\"').strip()
        if sanitized:
            intent.expression_parts.append(f'turn.text contains "{sanitized}"')

    keyword_clauses = _detect_keyword_clauses(lowered, intent.quoted_phrases)
    intent.expression_parts.extend(keyword_clauses)

    pattern_clauses = _detect_pattern_clauses(lowered)
    intent.expression_parts.extend(pattern_clauses)

    length_match = _LONGER_THAN_PATTERN.search(lowered)
    if length_match and "char" in length_match.group(2):
        threshold = int(length_match.group(1))
        intent.has_text_length = True
        intent.text_length_threshold = threshold
        intent.expression_parts.append(f"turn.text_length > {threshold}")

    amount_clause, amount_value = _detect_amount_clause(lowered)
    if amount_clause is not None:
        intent.has_amount = True
        intent.amount_value = amount_value
        intent.expression_parts.append(amount_clause)
        if intent.tool_ref is None and "transaction" in lowered:
            intent.tool_ref = "process_transaction"
        if intent.stage is None:
            intent.stage = "before_tool"

    if (
        "after hours" in lowered
        or "outside business hours" in lowered
        or "outside of business hours" in lowered
        or "off hours" in lowered
    ):
        intent.has_business_hours = True
        intent.after_hours = True
        intent.expression_parts.append(
            f"not (time.current_hour between [{_BUSINESS_HOURS_DEFAULT[0]}, {_BUSINESS_HOURS_DEFAULT[1]}])"
        )
    elif "business hours" in lowered or "during business" in lowered:
        intent.has_business_hours = True
        intent.expression_parts.append(
            f"time.current_hour between [{_BUSINESS_HOURS_DEFAULT[0]}, {_BUSINESS_HOURS_DEFAULT[1]}]"
        )

    if intent.tool_ref is not None and intent.stage is None:
        intent.stage = "before_tool"

    return intent


def _detect_effect(lowered: str) -> str | None:
    multi_word = sorted(
        (verb for verb in _EFFECT_VERBS if " " in verb),
        key=len,
        reverse=True,
    )
    for verb in multi_word:
        if verb in lowered:
            return _EFFECT_VERBS[verb]
    for verb, kind in _EFFECT_VERBS.items():
        if " " in verb:
            continue
        if re.search(rf"\b{re.escape(verb)}\b", lowered):
            return kind
    return None


def _detect_tool(text: str) -> str | None:
    for pattern in (_USING_TOOL_PATTERN, _NAME_BEFORE_TOOL_PATTERN, _TOOL_LABELED_PATTERN):
        for match in pattern.finditer(text):
            candidate = match.group(1)
            if candidate.lower() in _GENERIC_TOOL_WORDS:
                continue
            return candidate
    return None


def _detect_step(text: str) -> str | None:
    match = _STEP_PATTERN.search(text)
    if match:
        return match.group(1)
    return None


def _detect_agent(text: str) -> str | None:
    match = _AGENT_PATTERN.search(text)
    if match:
        return match.group(1)
    return None


def _detect_scenario(text: str) -> str | None:
    match = _SCENARIO_PATTERN.search(text)
    if match:
        return match.group(1)
    return None


def _detect_channel(lowered: str) -> Channel | None:
    multi_word = [token for token in _CHANNEL_TOKENS if " " in token]
    for token in sorted(multi_word, key=len, reverse=True):
        if token in lowered:
            return _CHANNEL_TOKENS[token]
    for token, channel in _CHANNEL_TOKENS.items():
        if " " in token:
            continue
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            return channel
    return None


def _detect_keyword_clauses(lowered: str, already_quoted: list[str]) -> list[str]:
    clauses: list[str] = []
    quoted_lower = {phrase.lower() for phrase in already_quoted}
    keyword_phrases: list[tuple[str, str]] = [
        ("credit card", r"\bcredit card\b"),
        ("card number", r"\bcard number\b"),
        ("cvv", r"\bcvv\b"),
        ("ssn", r"\bssn\b"),
        ("social security", r"\bsocial security\b"),
        ("refund", r"\brefund\b"),
        ("password", r"\bpassword\b"),
    ]
    for keyword, pattern in keyword_phrases:
        if keyword in quoted_lower:
            continue
        if re.search(pattern, lowered):
            clauses.append(f'turn.text contains "{keyword}"')
    return clauses


def _detect_pattern_clauses(lowered: str) -> list[str]:
    clauses: list[str] = []
    if "credit card number" in lowered or re.search(r"\b16[\s-]?digit", lowered):
        clauses.append('turn.text matches "\\\\b\\\\d{4}[\\\\s-]?\\\\d{4}[\\\\s-]?\\\\d{4}[\\\\s-]?\\\\d{4}\\\\b"')
    if re.search(r"\b9[\s-]?digit\b", lowered) or "social security number" in lowered:
        clauses.append('turn.text matches "\\\\b\\\\d{3}[\\\\s-]?\\\\d{2}[\\\\s-]?\\\\d{4}\\\\b"')
    return clauses


def _detect_amount_clause(lowered: str) -> tuple[str | None, float | int | None]:
    amount_match = re.search(
        r"(?:more than|over|above|exceeds?|exceeding|greater than|>\s*)\s*\$?\s*([\d,]+(?:\.\d+)?)",
        lowered,
    )
    if amount_match:
        value = _parse_number(amount_match.group(1))
        if value is not None:
            return f"tool.args.amount > {value}", value

    at_least_match = re.search(
        r"(?:at least|minimum of|>=\s*)\s*\$?\s*([\d,]+(?:\.\d+)?)",
        lowered,
    )
    if at_least_match:
        value = _parse_number(at_least_match.group(1))
        if value is not None:
            return f"tool.args.amount >= {value}", value

    less_than_match = re.search(
        r"(?:less than|under|below|<\s*)\s*\$?\s*([\d,]+(?:\.\d+)?)",
        lowered,
    )
    if less_than_match:
        value = _parse_number(less_than_match.group(1))
        if value is not None:
            return f"tool.args.amount < {value}", value
    return None, None


def _parse_number(raw: str) -> float | int | None:
    cleaned = raw.replace(",", "").strip()
    if not cleaned:
        return None
    try:
        if "." in cleaned:
            return float(cleaned)
        return int(cleaned)
    except ValueError:
        return None


def _scope_from_intent(intent: _Intent) -> ComposeBindingScope:
    return ComposeBindingScope(
        channels=[intent.channel] if intent.channel else [],
        step_ids=[intent.step_id] if intent.step_id else [],
        agent_ids=[intent.agent_id] if intent.agent_id else [],
        scenario_ids=[intent.scenario_id] if intent.scenario_id else [],
        tool_refs=[intent.tool_ref] if intent.tool_ref else [],
    )


def _effect_from_intent(intent: _Intent, *, summary: str) -> RuleEffect:
    code = _suggest_effect_code(intent)
    message = summary
    kind = intent.effect_kind
    if kind == "block":
        return BlockEffect(code=code, message=message)
    if kind == "warn":
        return WarnEffect(code=code, message=message)
    if kind == "require_confirmation":
        return RequireConfirmationEffect(code=code, message=message)
    if kind == "suppress_tool":
        return SuppressToolEffect(code=code, message=message, tool_ref=intent.tool_ref)
    if kind == "trace":
        return TraceEffect(code=code, message=message)
    raise ValueError(f"unsupported effect kind: {kind}")


def _suggest_effect_code(intent: _Intent) -> str:
    parts: list[str] = []
    if intent.effect_kind:
        parts.append(intent.effect_kind)
    if intent.tool_ref:
        parts.append(intent.tool_ref)
    if intent.has_amount:
        parts.append("amount_threshold")
    if intent.has_business_hours:
        parts.append("after_hours" if intent.after_hours else "business_hours")
    if intent.quoted_phrases:
        parts.append(_slugify(intent.quoted_phrases[0]))
    if intent.has_text_length:
        parts.append("text_length")
    if not parts:
        parts = ["composed_policy"]
    return ".".join(parts)[:120]


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return cleaned or "phrase"


def _suggest_rule_id(intent: _Intent, expression: str) -> str:
    base = "rule.compose"
    fragments: list[str] = []
    if intent.stage:
        fragments.append(intent.stage.split("_")[0])
    if intent.effect_kind:
        fragments.append(intent.effect_kind)
    if intent.tool_ref:
        fragments.append(intent.tool_ref)
    elif intent.quoted_phrases:
        fragments.append(_slugify(intent.quoted_phrases[0]))
    elif intent.has_amount:
        fragments.append("amount")
    elif intent.has_text_length:
        fragments.append("length")
    else:
        fragments.append(_slugify(expression)[:24])
    return ".".join([base, *fragments])[:160]


def _suggest_name(intent: _Intent) -> str:
    if intent.effect_kind == "block":
        verb = "Block"
    elif intent.effect_kind == "warn":
        verb = "Warn on"
    elif intent.effect_kind == "require_confirmation":
        verb = "Require approval for"
    elif intent.effect_kind == "suppress_tool":
        verb = "Suppress"
    elif intent.effect_kind == "trace":
        verb = "Trace"
    else:
        verb = "Policy for"

    target_parts: list[str] = []
    if intent.tool_ref:
        target_parts.append(f"{intent.tool_ref} tool")
    if intent.has_amount and intent.amount_value is not None:
        target_parts.append(f"amounts over {intent.amount_value}")
    if intent.has_text_length and intent.text_length_threshold is not None:
        target_parts.append(f"messages longer than {intent.text_length_threshold} characters")
    if intent.has_business_hours:
        target_parts.append("after hours" if intent.after_hours else "business hours")
    if intent.quoted_phrases:
        target_parts.append(f'"{intent.quoted_phrases[0]}"')
    if not target_parts:
        target_parts.append("matching turns")

    return f"{verb} {', '.join(target_parts)}"[:200] or _DEFAULT_RULE_NAME


def _summary_from_intent(text: str, intent: _Intent) -> str:
    summary = text.strip().rstrip(".")
    return (summary[:280] + "...") if len(summary) > 280 else summary


def _effect_phrase(effect: RuleEffect) -> str:
    if effect.kind == "block":
        return f"Block ({effect.code})"
    if effect.kind == "warn":
        return f"Warn ({effect.code})"
    if effect.kind == "require_confirmation":
        return f"Require confirmation ({effect.code})"
    if effect.kind == "suppress_tool":
        target = f" for tool {effect.tool_ref}" if effect.tool_ref else ""
        return f"Suppress tool execution{target} ({effect.code})"
    if effect.kind == "trace":
        return f"Log a trace ({effect.code})"
    return f"Apply {effect.kind}"


def _build_example_match(intent: _Intent) -> str | None:
    pieces: list[str] = []
    if intent.quoted_phrases:
        pieces.append(f'A turn containing "{intent.quoted_phrases[0]}"')
    if intent.has_amount and intent.amount_value is not None:
        threshold = intent.amount_value
        pieces.append(
            f"a tool call with amount {int(threshold) + 1 if isinstance(threshold, int) else threshold + 1.0}"
        )
    if intent.has_text_length and intent.text_length_threshold is not None:
        pieces.append(f"a turn with more than {intent.text_length_threshold} characters")
    if intent.has_business_hours:
        pieces.append("a turn during off-hours" if intent.after_hours else "a turn during business hours")
    if not pieces:
        return None
    return "; ".join(pieces)


def _build_example_no_match(intent: _Intent) -> str | None:
    pieces: list[str] = []
    if intent.quoted_phrases:
        pieces.append(f'A turn that does not mention "{intent.quoted_phrases[0]}"')
    if intent.has_amount and intent.amount_value is not None:
        threshold = intent.amount_value
        pieces.append(f"a tool call with amount {threshold}")
    if intent.has_text_length and intent.text_length_threshold is not None:
        pieces.append(f"a turn with at most {intent.text_length_threshold} characters")
    if not pieces:
        return None
    return "; ".join(pieces)


__all__ = [
    "ComposeAmbiguity",
    "ComposeBindingScope",
    "ComposeExplainRequest",
    "ComposeExplainResponse",
    "ComposePolicyProposal",
    "ComposePolicyRequest",
    "compile_policy",
    "explain_policy",
]
