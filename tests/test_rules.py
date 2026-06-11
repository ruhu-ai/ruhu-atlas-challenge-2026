from __future__ import annotations

from ruhu.rules import (
    BlockEffect,
    MatchPredicate,
    RequireConfirmationEffect,
    RuleDecision,
    RuleBinding,
    RuleBindingScope,
    RuleDefinition,
    RuleEngine,
    RuleEvaluationContext,
    RuleLibrary,
    RuleProgram,
    RuntimeRulesTrace,
    TraceEffect,
    WarnEffect,
    starter_rule_program,
)


def test_starter_rules_block_payment_card_data_on_turn_ingress() -> None:
    engine = RuleEngine()
    decision = engine.evaluate(
        starter_rule_program(),
        RuleEvaluationContext(
            stage="turn_ingress",
            conversation={"channel": "web_widget"},
            turn={"text": "My credit card number is 4111 1111 1111 1111"},
        ),
    )

    assert decision.terminal_effect is not None
    assert decision.terminal_effect.kind == "block"
    assert decision.terminal_effect.code == "payment_card_data_detected"
    assert decision.matched_rules[0].rule_id == "rule.turn.payment_card_data_block"


def test_starter_rules_warn_on_abusive_language_without_blocking() -> None:
    engine = RuleEngine()
    decision = engine.evaluate(
        starter_rule_program(),
        RuleEvaluationContext(
            stage="turn_ingress",
            conversation={"channel": "web_chat"},
            turn={"text": "This is bullshit and your system is useless."},
        ),
    )

    assert decision.terminal_effect is None
    assert len(decision.matched_rules) == 1
    assert decision.matched_rules[0].effect.kind == "warn"
    assert decision.matched_rules[0].rule_id == "rule.turn.unsafe_language_warning"


def test_starter_rules_suppress_after_hours_handoff_tool() -> None:
    engine = RuleEngine()
    decision = engine.evaluate(
        starter_rule_program(),
        RuleEvaluationContext(
            stage="before_tool",
            conversation={"channel": "phone"},
            tool={"ref": "human_handoff"},
            time={"current_hour": 21},
        ),
    )

    assert decision.terminal_effect is not None
    assert decision.terminal_effect.kind == "suppress_tool"
    assert decision.terminal_effect.tool_ref == "human_handoff"


def test_starter_rules_require_confirmation_for_high_value_transaction() -> None:
    engine = RuleEngine()
    decision = engine.evaluate(
        starter_rule_program(),
        RuleEvaluationContext(
            stage="before_tool",
            tool={"ref": "process_transaction", "args": {"amount": 25000}},
        ),
    )

    assert decision.terminal_effect is not None
    assert decision.terminal_effect.kind == "require_confirmation"
    assert decision.terminal_effect.code == "high_value_transaction_requires_confirmation"


def test_rule_program_rejects_bindings_that_reference_unknown_rules() -> None:
    try:
        RuleProgram(
            library=RuleLibrary(
                library_id="demo",
                version="1",
                rules=[],
            ),
            bindings=[
                RuleBinding(
                    binding_id="bind.unknown",
                    rule_id="rule.missing",
                )
            ],
        )
    except ValueError as exc:
        assert "unknown rule" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected validation error")


def test_rule_predicate_paths_must_use_known_runtime_roots() -> None:
    try:
        MatchPredicate(path="user_utterance", operator="contains", value="refund")
    except ValueError as exc:
        assert "must start with one of" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected validation error")


def test_shadow_rules_record_matches_without_producing_terminal_effects() -> None:
    program = RuleProgram(
        library=RuleLibrary(
            library_id="demo",
            version="1",
            rules=[
                RuleDefinition(
                    rule_id="rule.shadow.warn",
                    name="Shadow warning",
                    summary="Track a match without enforcing it.",
                    stage="turn_ingress",
                    predicate=MatchPredicate(path="turn.text", operator="contains", value="refund"),
                    effect=WarnEffect(code="refund_detected", message="Refund discussion detected."),
                ),
                RuleDefinition(
                    rule_id="rule.enforce.trace",
                    name="Trace follow-up",
                    summary="Record a non-terminal trace after shadow evaluation.",
                    stage="turn_ingress",
                    predicate=MatchPredicate(path="turn.text", operator="contains", value="refund"),
                    effect=TraceEffect(code="refund_trace", message="Refund flow trace."),
                ),
                RuleDefinition(
                    rule_id="rule.enforce.block",
                    name="Block destructive refund phrase",
                    summary="Stop the turn when a destructive phrase appears.",
                    stage="turn_ingress",
                    predicate=MatchPredicate(path="turn.text", operator="contains", value="refund everything"),
                    effect=BlockEffect(code="destructive_refund_request", message="Manual review required."),
                ),
            ],
        ),
        bindings=[
            RuleBinding(binding_id="bind.shadow.warn", rule_id="rule.shadow.warn", mode="shadow", order=10),
            RuleBinding(binding_id="bind.enforce.trace", rule_id="rule.enforce.trace", order=20),
            RuleBinding(binding_id="bind.enforce.block", rule_id="rule.enforce.block", order=30),
        ],
    )

    engine = RuleEngine()
    decision = engine.evaluate(
        program,
        RuleEvaluationContext(
            stage="turn_ingress",
            turn={"text": "Please refund everything immediately."},
        ),
    )

    assert decision.terminal_effect is not None
    assert decision.terminal_effect.kind == "block"
    assert [trace.outcome for trace in decision.traces] == ["shadow_match", "matched", "matched"]
    assert [match.rule_id for match in decision.matched_rules] == [
        "rule.enforce.trace",
        "rule.enforce.block",
    ]


def test_rule_binding_scope_filters_by_agent_step_channel_and_tool() -> None:
    program = RuleProgram(
        library=RuleLibrary(
            library_id="demo",
            version="1",
            rules=[
                RuleDefinition(
                    rule_id="rule.agent.specific",
                    name="Agent-specific block",
                    summary="Block only in a specific agent/step/tool combination.",
                    stage="before_tool",
                    predicate=MatchPredicate(path="metadata.execution_count", operator="gte", value=1),
                    effect=BlockEffect(code="agent_specific_block", message="Blocked."),
                )
            ],
        ),
        bindings=[
            RuleBinding(
                binding_id="bind.agent.specific",
                rule_id="rule.agent.specific",
                scope=RuleBindingScope(
                    channels=["web_chat"],
                    agent_ids=["support_triage_agent"],
                    step_ids=["collect_details"],
                    tool_refs=["knowledge.lookup"],
                ),
            )
        ],
    )

    engine = RuleEngine()
    matched = engine.evaluate(
        program,
        RuleEvaluationContext(
            stage="before_tool",
            conversation={
                "channel": "web_chat",
                "agent_id": "support_triage_agent",
                "step_id": "collect_details",
            },
            tool={"ref": "knowledge.lookup"},
            metadata={"execution_count": 1},
        ),
    )
    skipped = engine.evaluate(
        program,
        RuleEvaluationContext(
            stage="before_tool",
            conversation={
                "channel": "phone",
                "agent_id": "support_triage_agent",
                "step_id": "collect_details",
            },
            tool={"ref": "knowledge.lookup"},
            metadata={"execution_count": 1},
        ),
    )

    assert matched.terminal_effect is not None
    assert matched.terminal_effect.kind == "block"
    assert skipped.terminal_effect is None
    assert skipped.traces[0].outcome == "skipped"


def test_runtime_rules_trace_wraps_single_stage_decisions_without_losing_effects() -> None:
    trace = RuntimeRulesTrace()
    trace.append_decision(
        stage="turn_ingress",
        decision=RuleDecision(
            traces=[],
            matched_rules=[],
            terminal_effect=WarnEffect(
                code="language_warning",
                message="Calm the conversation.",
            ),
        ),
    )

    assert len(trace.evaluations) == 1
    assert trace.evaluations[0].stage == "turn_ingress"
    assert trace.evaluations[0].terminal_effect is not None
    assert trace.evaluations[0].terminal_effect.kind == "warn"
    assert trace.evaluations[0].terminal_effect.code == "language_warning"


def test_rule_engine_skips_exact_confirmed_binding_for_require_confirmation() -> None:
    program = RuleProgram(
        library=RuleLibrary(
            library_id="demo",
            version="1",
            rules=[
                RuleDefinition(
                    rule_id="rule.tool.confirm",
                    name="Confirm before tool",
                    summary="Require confirmation before tool execution.",
                    stage="before_tool",
                    predicate=MatchPredicate(path="tool.ref", operator="eq", value="process_transaction"),
                    effect=RequireConfirmationEffect(
                        code="confirm_transaction",
                        message="Confirm transaction.",
                    ),
                )
            ],
        ),
        bindings=[
            RuleBinding(
                binding_id="bind.tool.confirm",
                rule_id="rule.tool.confirm",
                scope=RuleBindingScope(tool_refs=["process_transaction"]),
            )
        ],
    )

    engine = RuleEngine()
    decision = engine.evaluate(
        program,
        RuleEvaluationContext(
            stage="before_tool",
            tool={"ref": "process_transaction"},
            metadata={"confirmed_rule_binding_ids": ["bind.tool.confirm"]},
        ),
    )

    assert decision.terminal_effect is None
    assert decision.matched_rules == []
    assert decision.traces[0].outcome == "skipped"
    assert decision.traces[0].detail == "binding already confirmed"
