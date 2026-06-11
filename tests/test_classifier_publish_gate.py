"""Tests for src/ruhu/classifier/publish_gate.py — WI-4.5.

The gate walks every authored ``OutcomeCondition`` event (NOT the
universal outcomes — those are framework-controlled) and warns when any
tokenizes past the threshold. Per-event dedup so an event appearing on
multiple steps yields one warning attached to the first sighting.
"""
from __future__ import annotations

import pytest

from ruhu.agent_document import (
    AgentDocument,
    Scenario,
    Step,
    StepCompletion,
    StepTransition,
)
from ruhu.classifier.publish_gate import (
    DEFAULT_MAX_TOKENS,
    heuristic_token_counter,
    tokenizer_pass_warnings,
)
from ruhu.schemas import OtherwiseCondition, OutcomeCondition


def _outcome(event: str) -> OutcomeCondition:
    """Author-supplied outcome with a description that satisfies validators."""
    return OutcomeCondition(
        event=event,
        description=f"User triggers the {event} workflow outcome.",
    )


def _step(
    *,
    id: str = "entry",
    name: str = "Entry",
    outcome_events: list[str] | None = None,
    extra_transitions: list[StepTransition] | None = None,
    completion: StepCompletion | None = None,
) -> Step:
    transitions: list[StepTransition] = []
    for idx, event in enumerate(outcome_events or []):
        transitions.append(
            StepTransition(
                id=f"t_{event}_{idx}",
                when=_outcome(event),
                to_step_id=id,
            )
        )
    if extra_transitions:
        transitions.extend(extra_transitions)
    return Step(
        id=id,
        name=name,
        transitions=transitions,
        completion=completion,
    )


def _doc(*steps: Step, version: str = "v1") -> AgentDocument:
    if not steps:
        steps = (_step(completion=StepCompletion(disposition="resolved")),)
    return AgentDocument(
        version=version,
        start_scenario_id="main",
        scenarios=[
            Scenario(
                id="main",
                name="Main",
                start_step_id=steps[0].id,
                steps=list(steps),
            )
        ],
    )


# ── happy path ────────────────────────────────────────────────────────────


def test_no_warnings_when_all_events_under_threshold() -> None:
    step = _step(
        outcome_events=["transfer", "kyc", "card"],
        completion=StepCompletion(disposition="resolved"),
    )
    issues = tokenizer_pass_warnings(
        _doc(step), token_counter=lambda s: 1, max_tokens=3
    )
    assert issues == []


def test_warns_when_event_exceeds_threshold() -> None:
    step = _step(
        outcome_events=["short", "very_long_compound_outcome"],
        completion=StepCompletion(disposition="resolved"),
    )
    issues = tokenizer_pass_warnings(
        _doc(step),
        token_counter=lambda s: 5 if s == "very_long_compound_outcome" else 1,
        max_tokens=3,
    )
    assert len(issues) == 1
    assert issues[0].code == "classifier.outcome_event_long"
    assert issues[0].severity == "warning"
    assert "very_long_compound_outcome" in issues[0].message
    assert issues[0].step_id == "entry"
    assert issues[0].scenario_id == "main"


def test_warning_message_includes_token_count_and_threshold() -> None:
    step = _step(
        outcome_events=["long_outcome"],
        completion=StepCompletion(disposition="resolved"),
    )
    issues = tokenizer_pass_warnings(
        _doc(step),
        token_counter=lambda _s: 7,
        max_tokens=3,
    )
    assert "7 tokens" in issues[0].message
    assert "> 3" in issues[0].message


def test_dedup_across_steps_only_warns_once_per_event() -> None:
    """Same outcome event in two steps produces one warning at first sighting."""
    step_a = _step(
        id="a",
        outcome_events=["shared_outcome"],
        # Add an otherwise edge so step_a isn't a non-terminal-without-transition.
        extra_transitions=[
            StepTransition(
                id="t_a_to_b",
                when=OtherwiseCondition(),
                to_step_id="b",
            ),
        ],
    )
    step_b = _step(
        id="b",
        outcome_events=["shared_outcome"],
        completion=StepCompletion(disposition="resolved"),
    )
    document = _doc(step_a, step_b)
    issues = tokenizer_pass_warnings(
        document, token_counter=lambda _s: 5, max_tokens=3
    )
    assert len(issues) == 1
    # First sighting wins — that's step_a, not step_b
    assert issues[0].step_id == "a"


def test_threshold_boundary_equal_does_not_warn() -> None:
    step = _step(
        outcome_events=["foo"],
        completion=StepCompletion(disposition="resolved"),
    )
    # Exactly threshold = no warning
    issues = tokenizer_pass_warnings(
        _doc(step), token_counter=lambda _s: 3, max_tokens=3
    )
    assert issues == []


def test_threshold_boundary_one_over_warns() -> None:
    step = _step(
        outcome_events=["foo"],
        completion=StepCompletion(disposition="resolved"),
    )
    issues = tokenizer_pass_warnings(
        _doc(step), token_counter=lambda _s: 4, max_tokens=3
    )
    assert len(issues) == 1


def test_invalid_max_tokens_raises() -> None:
    with pytest.raises(ValueError):
        tokenizer_pass_warnings(_doc(), token_counter=lambda _s: 1, max_tokens=0)


def test_empty_document_no_authored_outcomes_no_warnings() -> None:
    """Step with no authored outcome transitions produces no warnings —
    universal outcomes are framework-controlled and skipped."""
    issues = tokenizer_pass_warnings(_doc(), token_counter=lambda _s: 100)
    assert issues == []


def test_default_max_tokens_is_three() -> None:
    """Per spec — labels >3 tokens warn."""
    assert DEFAULT_MAX_TOKENS == 3


def test_warnings_walk_multiple_scenarios() -> None:
    long_step = _step(
        id="alpha",
        outcome_events=["long_alpha_outcome"],
        completion=StepCompletion(disposition="resolved"),
    )
    other_step = _step(
        id="beta",
        outcome_events=["long_beta_outcome"],
        completion=StepCompletion(disposition="resolved"),
    )
    doc = AgentDocument(
        version="v1",
        start_scenario_id="s_alpha",
        scenarios=[
            Scenario(id="s_alpha", name="A", start_step_id="alpha", steps=[long_step]),
            Scenario(id="s_beta", name="B", start_step_id="beta", steps=[other_step]),
        ],
    )
    issues = tokenizer_pass_warnings(
        doc, token_counter=lambda _s: 5, max_tokens=3
    )
    assert {issue.scenario_id for issue in issues} == {"s_alpha", "s_beta"}


def test_universal_outcomes_are_not_walked() -> None:
    """The gate must not warn on framework-injected universal outcomes
    (audio_check, agent_identity_question, …) — authors can't shorten
    those names. We confirm by giving every event a token count ≥ max
    and asserting a step with NO authored outcomes raises zero
    warnings."""
    step = _step(completion=StepCompletion(disposition="resolved"))
    # Token counter says everything is huge — would warn 4 times if
    # universals were walked.
    issues = tokenizer_pass_warnings(
        _doc(step), token_counter=lambda _s: 100, max_tokens=3
    )
    assert issues == []


# ── heuristic_token_counter ──────────────────────────────────────────────


def test_heuristic_counts_underscore_separated_tokens() -> None:
    assert heuristic_token_counter("transfer_status") == 2
    assert heuristic_token_counter("kyc_verification_required") == 3
    assert heuristic_token_counter("very_long_compound_outcome_id") == 5


def test_heuristic_handles_single_word() -> None:
    assert heuristic_token_counter("close") == 1


def test_heuristic_empty_string_is_zero() -> None:
    assert heuristic_token_counter("") == 0


def test_heuristic_collapses_consecutive_underscores() -> None:
    assert heuristic_token_counter("a__b") == 2
