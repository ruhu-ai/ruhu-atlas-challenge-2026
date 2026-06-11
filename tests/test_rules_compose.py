"""NL\u2192DSL compiler test corpus (Doc 04).

Pinned translations cover:
- canonical good translations (block / warn / require approval / suppress)
- step-native scope vocabulary (step_id, never state_id at the API edge)
- ambiguity surface (effect missing, condition missing, tool missing)
- regression coverage (PCI/SSN keyword detection, business hours, length)

The compose surface now persists through the same agent/step-native rule scope
model used elsewhere.
"""
from __future__ import annotations

import pytest

from ruhu.rules import (
    BlockEffect,
    RequireConfirmationEffect,
    RuleBinding,
    RuleEvaluationContext,
    RuleEngine,
    RuleLibrary,
    RuleProgram,
    SuppressToolEffect,
    WarnEffect,
)
from ruhu.rules_compose import (
    ComposeBindingScope,
    ComposeExplainRequest,
    ComposePolicyRequest,
    compile_policy,
    explain_policy,
)


def _evaluate(proposal, context: RuleEvaluationContext):
    assert proposal.rule_body is not None
    rule = proposal.rule_body.to_definition(rule_id="rule.test", revision=1)
    library = RuleLibrary(library_id="lib.test", version="t", rules=[rule])
    binding = RuleBinding(binding_id="bind.test", rule_id=rule.rule_id, revision=rule.revision)
    program = RuleProgram(library=library, bindings=[binding])
    return RuleEngine().evaluate(program, context)


def test_block_credit_card_keyword_compiles_to_block_at_turn_ingress() -> None:
    proposal = compile_policy(
        ComposePolicyRequest(text="Block any message containing credit card numbers")
    )

    assert proposal.outcome == "ready"
    assert proposal.rule_body is not None
    assert proposal.rule_body.stage == "turn_ingress"
    assert isinstance(proposal.rule_body.effect, BlockEffect)
    assert "credit card" in (proposal.expression or "")
    assert proposal.ambiguities == []


def test_warn_on_abusive_quoted_phrase_compiles_to_warn() -> None:
    proposal = compile_policy(
        ComposePolicyRequest(text='Warn when the user says "this is a scam"')
    )

    assert proposal.outcome == "ready"
    assert proposal.rule_body is not None
    assert isinstance(proposal.rule_body.effect, WarnEffect)
    assert 'turn.text contains "this is a scam"' in (proposal.expression or "")


def test_require_approval_for_large_transaction_targets_before_tool_stage() -> None:
    proposal = compile_policy(
        ComposePolicyRequest(
            text="Require approval for transactions over $10000 when calling process_transaction tool"
        )
    )

    assert proposal.outcome == "ready"
    assert proposal.rule_body is not None
    assert proposal.rule_body.stage == "before_tool"
    assert isinstance(proposal.rule_body.effect, RequireConfirmationEffect)
    assert "tool.args.amount > 10000" in (proposal.expression or "")
    assert proposal.binding_scope.tool_refs == ["process_transaction"]


def test_suppress_handoff_after_business_hours_targets_tool_stage() -> None:
    proposal = compile_policy(
        ComposePolicyRequest(
            text="Suppress the human_handoff tool outside business hours"
        )
    )

    assert proposal.outcome == "ready"
    assert proposal.rule_body is not None
    assert proposal.rule_body.stage == "before_tool"
    assert isinstance(proposal.rule_body.effect, SuppressToolEffect)
    assert proposal.rule_body.effect.tool_ref == "human_handoff"
    assert "not (time.current_hour between" in (proposal.expression or "")


def test_block_messages_longer_than_threshold_compiles_to_text_length_predicate() -> None:
    proposal = compile_policy(
        ComposePolicyRequest(text="Block messages longer than 500 characters")
    )

    assert proposal.outcome == "ready"
    assert proposal.expression and "turn.text_length > 500" in proposal.expression
    decision = _evaluate(
        proposal,
        RuleEvaluationContext(
            stage="turn_ingress",
            turn={"text": "x" * 600},
        ),
    )
    assert decision.terminal_effect is not None
    assert decision.terminal_effect.kind == "block"


def test_step_scope_uses_step_native_vocabulary_at_compose_edge() -> None:
    proposal = compile_policy(
        ComposePolicyRequest(
            text='Block "credit card" in step collect_payment_info'
        )
    )

    assert proposal.binding_scope.step_ids == ["collect_payment_info"]
    persisted = proposal.binding_scope.to_persisted_scope()
    assert persisted.step_ids == ["collect_payment_info"]


def test_agent_scope_uses_agent_native_vocabulary_at_compose_edge() -> None:
    proposal = compile_policy(
        ComposePolicyRequest(
            text='Block "credit card" in agent billing_support'
        )
    )

    assert proposal.binding_scope.agent_ids == ["billing_support"]
    persisted = proposal.binding_scope.to_persisted_scope()
    assert persisted.agent_ids == ["billing_support"]


def test_scenario_scope_is_carried_as_advisory_metadata() -> None:
    proposal = compile_policy(
        ComposePolicyRequest(
            text='Block "credit card" in scenario refunds'
        )
    )

    assert proposal.binding_scope.scenario_ids == ["refunds"]
    assert proposal.rule_body is not None
    advisory = proposal.rule_body.metadata.get("compose_scope_advisory")
    assert advisory == {"scenario_ids": ["refunds"]}


def test_compose_scope_round_trip_from_persisted_step_ids() -> None:
    from ruhu.rules import RuleBindingScope

    persisted = RuleBindingScope(step_ids=["welcome_step"], channels=["web_chat"])
    compose_scope = ComposeBindingScope.from_persisted_scope(persisted)
    assert compose_scope.step_ids == ["welcome_step"]
    assert compose_scope.agent_ids == []
    assert compose_scope.scenario_ids == []
    assert compose_scope.channels == ["web_chat"]


def test_underspecified_policy_with_no_effect_verb_returns_clarification() -> None:
    proposal = compile_policy(ComposePolicyRequest(text="be careful with refunds"))

    assert proposal.outcome == "needs_clarification"
    assert any(item.code == "effect_unclear" for item in proposal.ambiguities)


def test_effect_without_condition_returns_clarification() -> None:
    proposal = compile_policy(ComposePolicyRequest(text="block everything"))

    assert proposal.outcome == "needs_clarification"
    assert any(item.code == "condition_unclear" for item in proposal.ambiguities)


def test_tool_targeting_policy_without_tool_named_flags_ambiguity() -> None:
    proposal = compile_policy(
        ComposePolicyRequest(text="Require approval before sending the message")
    )

    assert any(item.code == "tool_ref_missing" for item in proposal.ambiguities)


def test_empty_text_returns_unsupported_outcome() -> None:
    proposal = compile_policy(ComposePolicyRequest(text="   "))

    assert proposal.outcome == "unsupported"
    assert any(item.code == "empty_text" for item in proposal.ambiguities)


def test_compose_proposal_compiles_executable_rule_for_credit_card() -> None:
    proposal = compile_policy(
        ComposePolicyRequest(
            text="Block any message that mentions credit card numbers"
        )
    )

    decision = _evaluate(
        proposal,
        RuleEvaluationContext(
            stage="turn_ingress",
            turn={"text": "Here is my credit card 4111 1111 1111 1111"},
        ),
    )

    assert decision.terminal_effect is not None
    assert decision.terminal_effect.kind == "block"


def test_compose_proposal_compiles_executable_rule_for_amount_threshold() -> None:
    proposal = compile_policy(
        ComposePolicyRequest(
            text="Require approval for transactions over $5000 when calling process_transaction tool"
        )
    )

    decision = _evaluate(
        proposal,
        RuleEvaluationContext(
            stage="before_tool",
            tool={"ref": "process_transaction", "args": {"amount": 12000}},
        ),
    )

    assert decision.terminal_effect is not None
    assert decision.terminal_effect.kind == "require_confirmation"


def test_explain_round_trips_block_rule_into_plain_english() -> None:
    proposal = compile_policy(
        ComposePolicyRequest(text="Block any message containing credit card numbers")
    )
    assert proposal.rule_body is not None

    explanation = explain_policy(
        ComposeExplainRequest(rule_body=proposal.rule_body, binding_scope=proposal.binding_scope)
    )

    assert "Block" in explanation.explanation
    assert "turn ingress" in explanation.explanation
    assert "credit card" in explanation.explanation


def test_channel_phrase_populates_binding_scope() -> None:
    proposal = compile_policy(
        ComposePolicyRequest(text='Warn on "competitor" mentions in whatsapp')
    )

    assert proposal.binding_scope.channels == ["whatsapp"]


@pytest.mark.parametrize(
    "text,expected_kind",
    [
        ("Block messages with cvv", "block"),
        ('Warn on "frustrated" mentions', "warn"),
        ('Log every turn that mentions "refund"', "trace"),
    ],
)
def test_effect_verb_inference_handles_block_warn_trace(text: str, expected_kind: str) -> None:
    proposal = compile_policy(ComposePolicyRequest(text=text))
    assert proposal.rule_body is not None
    assert proposal.rule_body.effect.kind == expected_kind
