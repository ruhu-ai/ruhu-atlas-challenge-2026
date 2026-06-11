"""Tests for ``Step.classifier_uses_facts`` — WI-6.12.

Schema + suffix-injection coverage. The named facts must land in the
classifier prompt suffix only — never in the cached prefix — so
prefix-cache hits survive when per-step fact opt-in is enabled.
"""
from __future__ import annotations

import pytest

from ruhu.agent_document import AgentDocument, Scenario, Step, StepCompletion
from ruhu.classifier.prompt import (
    build_classifier_prefix,
    build_classifier_prompt,
    build_classifier_suffix,
    reset_prefix_cache,
)


@pytest.fixture(autouse=True)
def _clear_prefix_cache() -> None:
    reset_prefix_cache()
    yield
    reset_prefix_cache()


def _step(**overrides) -> Step:
    base = dict(
        id="entry",
        name="Entry",
        completion=StepCompletion(disposition="resolved"),
    )
    base.update(overrides)
    return Step(**base)


def _doc(step: Step, version: str = "v1") -> AgentDocument:
    return AgentDocument(
        version=version,
        start_scenario_id="main",
        scenarios=[Scenario(id="main", name="Main", start_step_id=step.id, steps=[step])],
    )


# ── schema ─────────────────────────────────────────────────────────────────


def test_classifier_uses_facts_defaults_to_none() -> None:
    step = _step()
    assert step.classifier_uses_facts is None


def test_classifier_uses_facts_accepts_empty_list() -> None:
    step = _step(classifier_uses_facts=[])
    assert step.classifier_uses_facts == []


def test_classifier_uses_facts_accepts_typical_list() -> None:
    step = _step(classifier_uses_facts=["account_status", "kyc_state"])
    assert step.classifier_uses_facts == ["account_status", "kyc_state"]


def test_classifier_uses_facts_round_trips_through_model_dump() -> None:
    step = _step(classifier_uses_facts=["account_status"])
    payload = step.model_dump(mode="json")
    assert payload["classifier_uses_facts"] == ["account_status"]
    rebuilt = Step.model_validate(payload)
    assert rebuilt.classifier_uses_facts == ["account_status"]


def test_classifier_uses_facts_omitted_round_trips_as_none() -> None:
    step = _step()
    payload = step.model_dump(mode="json")
    assert payload["classifier_uses_facts"] is None
    rebuilt = Step.model_validate(payload)
    assert rebuilt.classifier_uses_facts is None


# ── suffix injection ───────────────────────────────────────────────────────


def test_build_classifier_suffix_without_fact_names_is_unchanged() -> None:
    """No fact_names → suffix matches the original WI-4.1 contract verbatim."""
    assert build_classifier_suffix("hi") == "User message: hi\nOutcome:"


def test_build_classifier_suffix_injects_named_facts_before_user_message() -> None:
    suffix = build_classifier_suffix(
        "where is my money",
        facts={"account_status": "active", "kyc_state": "verified"},
        fact_names=["account_status", "kyc_state"],
    )
    assert suffix == (
        "Known facts: account_status=active, kyc_state=verified\n"
        "User message: where is my money\n"
        "Outcome:"
    )


def test_build_classifier_suffix_preserves_fact_names_order() -> None:
    suffix = build_classifier_suffix(
        "x",
        facts={"b": "B", "a": "A"},
        fact_names=["a", "b"],
    )
    assert "a=A, b=B" in suffix


def test_build_classifier_suffix_skips_missing_and_none_facts_silently() -> None:
    suffix = build_classifier_suffix(
        "x",
        facts={"a": "A", "b": None},
        fact_names=["a", "b", "c"],
    )
    assert "a=A" in suffix
    assert "b=" not in suffix
    assert "c=" not in suffix


def test_build_classifier_suffix_no_known_facts_block_when_all_skipped() -> None:
    suffix = build_classifier_suffix(
        "x",
        facts={"a": None},
        fact_names=["a", "b"],
    )
    assert "Known facts:" not in suffix
    assert suffix == "User message: x\nOutcome:"


def test_build_classifier_suffix_empty_fact_names_means_no_block() -> None:
    suffix = build_classifier_suffix("x", facts={"a": "A"}, fact_names=[])
    assert "Known facts:" not in suffix


# ── prompt assembly contract ──────────────────────────────────────────────


def test_build_classifier_prompt_facts_land_in_suffix_only() -> None:
    """The load-bearing claim: facts NEVER enter the cached prefix."""
    step = _step(
        completion=None,
        classifier_uses_facts=["account_status"],
        event_hints={"transfer_status": "User asks about a transfer."},
        transitions=[],
    )
    document = _doc(step)
    prefix, suffix = build_classifier_prompt(
        document,
        step,
        user_text="hi",
        facts={"account_status": "frozen"},
    )
    assert "frozen" not in prefix
    assert "account_status" not in prefix
    assert "frozen" in suffix
    assert "account_status=frozen" in suffix


def test_build_classifier_prompt_prefix_unchanged_by_fact_values() -> None:
    """Two turns at the same step with different fact values produce the
    same byte-identical prefix — that's the whole point of the WI-6.12
    suffix-only injection."""
    step = _step(
        completion=None,
        classifier_uses_facts=["account_status"],
        event_hints={"transfer_status": "x"},
        transitions=[],
    )
    document = _doc(step)
    prefix_a, _ = build_classifier_prompt(
        document, step, user_text="hi", facts={"account_status": "active"}
    )
    prefix_b, _ = build_classifier_prompt(
        document, step, user_text="hi", facts={"account_status": "frozen"}
    )
    assert prefix_a == prefix_b


def test_build_classifier_prompt_no_fact_opt_in_keeps_original_suffix() -> None:
    step = _step(
        completion=None,
        event_hints={"transfer_status": "x"},
        transitions=[],
    )  # classifier_uses_facts is None
    document = _doc(step)
    _, suffix = build_classifier_prompt(
        document, step, user_text="hi", facts={"account_status": "active"}
    )
    # No opt-in → facts ignored even when present in the conversation
    assert "Known facts:" not in suffix
    assert suffix == "User message: hi\nOutcome:"


def test_build_classifier_prompt_prefix_byte_identical_across_runs() -> None:
    """Original WI-4.1 invariant survives the WI-6.12 addition."""
    step = _step(
        completion=None,
        classifier_uses_facts=["foo"],
        event_hints={"transfer_status": "x"},
        transitions=[],
    )
    document = _doc(step)
    a = build_classifier_prefix(document, step)
    b = build_classifier_prefix(document, step)
    assert a is b
