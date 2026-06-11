"""Tests for src/ruhu/classifier/prompt.py — outcome-catalog edition.

These tests pin two contracts:

1. **Byte-identical prefix.** vLLM's prefix cache hits only when bytes
   match across turns at the same step. Re-ordering transitions or
   editing whitespace must not produce a different prefix string.

2. **Outcome catalog ownership.** The catalog is sourced **only** from
   the step's ``OutcomeCondition`` transitions (plus the kernel-injected
   universal outcomes). ``Step.event_hints`` is gone; constructing the
   catalog from any other place would re-introduce the indirection that
   the edge-owned-outcomes migration removed.
"""
from __future__ import annotations

import pytest

from ruhu.agent_document import (
    AgentDocument,
    Scenario,
    Step,
    StepTransition,
)
from ruhu.classifier.constrained import UNKNOWN_LABEL
from ruhu.classifier.prompt import (
    SYSTEM_MESSAGE,
    UNIVERSAL_OUTCOMES,
    build_classifier_prefix,
    build_classifier_prompt,
    build_classifier_suffix,
    outcome_catalog_for_step,
    reset_prefix_cache,
)
from ruhu.schemas import OtherwiseCondition, OutcomeCondition


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_prefix_cache()
    yield
    reset_prefix_cache()


def _doc(*, version: str = "v1", step: Step) -> AgentDocument:
    return AgentDocument(
        version=version,
        start_scenario_id="main",
        scenarios=[
            Scenario(
                id="main",
                name="Main",
                start_step_id=step.id,
                steps=[step],
            )
        ],
    )


def _outcome(event: str, description: str = "Authored description.") -> OutcomeCondition:
    return OutcomeCondition(event=event, description=description)


def _step(
    *,
    id: str = "entry",
    name: str = "Entry",
    description: str | None = "Triage the user's reason for contacting.",
    transitions: list[StepTransition] | None = None,
) -> Step:
    return Step(
        id=id,
        name=name,
        description=description,
        transitions=transitions if transitions is not None else [],
    )


# ── outcome_catalog_for_step ────────────────────────────────────────────────


class TestOutcomeCatalogForStep:
    def test_authored_outcomes_in_catalog(self) -> None:
        step = _step(
            transitions=[
                StepTransition(
                    id="t1",
                    when=_outcome("transfer_status", "User asks about a transfer."),
                    to_step_id="entry",
                ),
                StepTransition(
                    id="t2",
                    when=_outcome("kyc_help", "User asks about KYC."),
                    to_step_id="entry",
                ),
            ],
        )
        catalog = outcome_catalog_for_step(step)
        assert catalog["transfer_status"] == "User asks about a transfer."
        assert catalog["kyc_help"] == "User asks about KYC."

    def test_universal_outcomes_appended(self) -> None:
        step = _step(
            transitions=[
                StepTransition(
                    id="t1",
                    when=_outcome("transfer_status", "User asks about a transfer."),
                    to_step_id="entry",
                ),
            ],
        )
        catalog = outcome_catalog_for_step(step)
        for universal_event in UNIVERSAL_OUTCOMES:
            assert universal_event in catalog
        # Authored outcome description survives.
        assert catalog["transfer_status"] == "User asks about a transfer."

    def test_authored_outcome_shadows_universal_collision(self) -> None:
        # If an author named an outcome the same as a universal one, the
        # author's description wins.
        step = _step(
            transitions=[
                StepTransition(
                    id="t_audio",
                    when=_outcome("audio_check", "Custom: ask if I can hear them."),
                    to_step_id="entry",
                ),
            ],
        )
        catalog = outcome_catalog_for_step(step)
        assert catalog["audio_check"] == "Custom: ask if I can hear them."

    def test_step_with_only_otherwise_yields_only_universals(self) -> None:
        step = _step(
            transitions=[
                StepTransition(
                    id="t_otherwise",
                    when=OtherwiseCondition(),
                    to_step_id="entry",
                ),
            ],
        )
        catalog = outcome_catalog_for_step(step)
        assert sorted(catalog.keys()) == sorted(UNIVERSAL_OUTCOMES.keys())

    def test_catalog_sorted_by_event_for_cache_stability(self) -> None:
        step = _step(
            transitions=[
                StepTransition(
                    id="t_z",
                    when=_outcome("zzz_last", "Description for Z."),
                    to_step_id="entry",
                ),
                StepTransition(
                    id="t_a",
                    when=_outcome("aaa_first", "Description for A."),
                    to_step_id="entry",
                ),
            ],
        )
        events = list(outcome_catalog_for_step(step).keys())
        # Sorted ascending — so aaa_first comes before zzz_last regardless
        # of authoring order.
        assert events.index("aaa_first") < events.index("zzz_last")


# ── prefix shape ────────────────────────────────────────────────────────────


class TestPrefixShape:
    def test_starts_with_system_message(self) -> None:
        step = _step(
            transitions=[
                StepTransition(
                    id="t1",
                    when=_outcome("foo", "Foo description."),
                    to_step_id="entry",
                ),
            ],
        )
        prefix = build_classifier_prefix(_doc(step=step), step)
        assert prefix.startswith(SYSTEM_MESSAGE)

    def test_contains_step_block(self) -> None:
        step = _step(
            transitions=[
                StepTransition(
                    id="t1",
                    when=_outcome("foo", "Foo description."),
                    to_step_id="entry",
                ),
            ],
        )
        prefix = build_classifier_prefix(_doc(step=step), step)
        assert "Step: Entry" in prefix
        assert "Step summary:" in prefix
        assert "Step capabilities:" in prefix

    def test_contains_workflow_outcomes_header(self) -> None:
        step = _step(
            transitions=[
                StepTransition(
                    id="t1",
                    when=_outcome("foo", "Foo description."),
                    to_step_id="entry",
                ),
            ],
        )
        prefix = build_classifier_prefix(_doc(step=step), step)
        assert "Workflow outcomes (choose exactly one):" in prefix

    def test_contains_unknown_sentinel(self) -> None:
        step = _step(
            transitions=[
                StepTransition(
                    id="t1",
                    when=_outcome("foo", "Foo description."),
                    to_step_id="entry",
                ),
            ],
        )
        prefix = build_classifier_prefix(_doc(step=step), step)
        assert UNKNOWN_LABEL in prefix
        assert f"- {UNKNOWN_LABEL}: " in prefix


class TestPrefixDeterminism:
    def test_same_inputs_produce_same_string(self) -> None:
        step_a = _step(
            transitions=[
                StepTransition(
                    id="t_a",
                    when=_outcome("alpha", "Description for A."),
                    to_step_id="entry",
                ),
                StepTransition(
                    id="t_z",
                    when=_outcome("zeta", "Description for Z."),
                    to_step_id="entry",
                ),
            ],
        )
        # Same authored events but reverse order; should produce the
        # same prefix because catalog is sorted by event.
        step_b = _step(
            transitions=[
                StepTransition(
                    id="t_z",
                    when=_outcome("zeta", "Description for Z."),
                    to_step_id="entry",
                ),
                StepTransition(
                    id="t_a",
                    when=_outcome("alpha", "Description for A."),
                    to_step_id="entry",
                ),
            ],
        )
        # Reset so the cache doesn't cause a hit by version reuse.
        reset_prefix_cache()
        prefix_a = build_classifier_prefix(_doc(version="va", step=step_a), step_a)
        reset_prefix_cache()
        prefix_b = build_classifier_prefix(_doc(version="vb", step=step_b), step_b)
        assert prefix_a == prefix_b

    def test_whitespace_normalised_in_descriptions(self) -> None:
        messy = _step(
            transitions=[
                StepTransition(
                    id="t_foo",
                    when=_outcome("foo", "  multi    space\tdescription\n with newline "),
                    to_step_id="entry",
                ),
            ],
        )
        clean = _step(
            transitions=[
                StepTransition(
                    id="t_foo",
                    when=_outcome("foo", "multi space description with newline"),
                    to_step_id="entry",
                ),
            ],
        )
        reset_prefix_cache()
        messy_prefix = build_classifier_prefix(_doc(version="vx", step=messy), messy)
        reset_prefix_cache()
        clean_prefix = build_classifier_prefix(_doc(version="vy", step=clean), clean)
        assert messy_prefix == clean_prefix


# ── prefix cache ────────────────────────────────────────────────────────────


class TestPrefixCache:
    def test_cache_hit_returns_identity(self) -> None:
        step = _step(
            transitions=[
                StepTransition(
                    id="t",
                    when=_outcome("foo", "Foo desc"),
                    to_step_id="entry",
                ),
            ],
        )
        doc = _doc(step=step)
        first = build_classifier_prefix(doc, step)
        second = build_classifier_prefix(doc, step)
        # Memoisation returns the *same string object* for identical
        # cache keys — caller can compare by identity.
        assert first is second

    def test_different_versions_produce_distinct_strings(self) -> None:
        step = _step(
            transitions=[
                StepTransition(
                    id="t",
                    when=_outcome("foo", "Old desc"),
                    to_step_id="entry",
                ),
            ],
        )
        prefix_v1 = build_classifier_prefix(_doc(version="v1", step=step), step)
        step_v2 = _step(
            transitions=[
                StepTransition(
                    id="t",
                    when=_outcome("foo", "New desc"),
                    to_step_id="entry",
                ),
            ],
        )
        prefix_v2 = build_classifier_prefix(_doc(version="v2", step=step_v2), step_v2)
        assert prefix_v1 != prefix_v2

    def test_reset_clears_cache(self) -> None:
        step = _step(
            transitions=[
                StepTransition(
                    id="t",
                    when=_outcome("foo", "First version description."),
                    to_step_id="entry",
                ),
            ],
        )
        doc = _doc(step=step)
        first = build_classifier_prefix(doc, step)
        reset_prefix_cache()
        # After reset, the rebuilt string is equal but a fresh object.
        again = build_classifier_prefix(doc, step)
        assert first == again


# ── suffix shape ────────────────────────────────────────────────────────────


class TestSuffix:
    def test_terminates_with_outcome_anchor(self) -> None:
        suffix = build_classifier_suffix("hello world")
        assert suffix.endswith("Outcome:")

    def test_includes_user_message(self) -> None:
        suffix = build_classifier_suffix("hello world")
        assert "User message: hello world" in suffix

    def test_optional_known_facts_block_when_named(self) -> None:
        suffix = build_classifier_suffix(
            "hello",
            facts={"email": "a@b.com", "tier": "gold"},
            fact_names=["email"],
        )
        # facts named: only email rendered (tier is in facts but not named)
        assert "Known facts: email=a@b.com" in suffix
        assert "tier" not in suffix

    def test_no_facts_block_when_no_names_given(self) -> None:
        suffix = build_classifier_suffix(
            "hello",
            facts={"email": "a@b.com"},
            fact_names=None,
        )
        assert "Known facts" not in suffix


# ── full prompt convenience ─────────────────────────────────────────────────


class TestBuildClassifierPrompt:
    def test_returns_prefix_and_suffix(self) -> None:
        step = _step(
            transitions=[
                StepTransition(
                    id="t",
                    when=_outcome("foo", "Foo desc"),
                    to_step_id="entry",
                ),
            ],
        )
        prefix, suffix = build_classifier_prompt(
            _doc(step=step), step, user_text="hi"
        )
        assert prefix.endswith("\n\n")
        assert suffix.endswith("Outcome:")
        assert "hi" in suffix
