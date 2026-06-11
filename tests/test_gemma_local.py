"""Tests for the constrained-decode local-Gemma interpreter.

Edge-owned-outcomes contract: ``GemmaLocalInterpreter`` builds the
candidate-label catalog from the step's ``OutcomeCondition`` transitions
plus the kernel-injected universal outcomes (``classifier.prompt
.UNIVERSAL_OUTCOMES``). It delegates to a ``PrefillClassifier`` and
projects the chosen label onto the ``routing.outcome_resolved`` /
``routing.classifier_unavailable`` events the kernel routes on.

Tests use a deterministic fake classifier so they don't need
transformers/torch.
"""
from __future__ import annotations

import datetime
import hashlib
from pathlib import Path

import pytest

from ruhu.agent_document import AgentDocument, Scenario, Step, StepTransition
from ruhu.classifier.protocol import (
    ClassificationRequest,
    ClassificationResult,
)
from ruhu.gemma_local import (
    GemmaLocalInterpreter,
    _expected_sha256_for_model_path,
    _sha256_file,
)
from ruhu.schemas import (
    OtherwiseCondition,
    OutcomeCondition,
    RuntimeTurn,
)


class FakeClassifier:
    """Returns a fixed ``ClassificationResult`` and records the request seen."""

    def __init__(self, result: ClassificationResult) -> None:
        self.result = result
        self.last_request: ClassificationRequest | None = None

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        self.last_request = request
        return self.result


def _outcome(event: str, description: str | None = None) -> OutcomeCondition:
    return OutcomeCondition(
        event=event,
        description=description or f"User triggers the {event} outcome.",
    )


def _document_with_outcomes(*outcome_events: str) -> AgentDocument:
    transitions = [
        StepTransition(
            id=f"t_{event}",
            when=_outcome(event),
            to_step_id="qa",
        )
        for event in outcome_events
    ]
    transitions.append(
        StepTransition(
            id="t_otherwise",
            when=OtherwiseCondition(),
            to_step_id="qa",
        )
    )
    return AgentDocument(
        start_scenario_id="main",
        scenarios=[
            Scenario(
                id="main",
                name="Main",
                start_step_id="qa",
                steps=[
                    Step(
                        id="qa",
                        name="Q&A",
                        description="Answer the user's question.",
                        transitions=transitions,
                    )
                ],
            )
        ],
    )


def _make_turn(text: str) -> RuntimeTurn:
    return RuntimeTurn(
        turn_id="turn_1",
        dedupe_key="turn_1",
        channel="web_chat",
        modality="text",
        event_type="user_message",
        text=text,
        received_at=datetime.datetime.now(datetime.timezone.utc),
    )


# ── successful classification ──────────────────────────────────────────────


def test_interpreter_emits_routing_outcome_resolved_for_authored_event() -> None:
    classifier = FakeClassifier(
        ClassificationResult(
            chosen_label="product_question",
            confidence=0.93,
            backend="transformers",
            elapsed_ms=42,
        )
    )
    interpreter = GemmaLocalInterpreter(classifier=classifier, model_name="gemma-test")
    document = _document_with_outcomes("product_question")
    step = document.steps[0]

    events = interpreter.interpret(
        agent_document=document,
        step=step,
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_make_turn("describe the product"),
    )

    assert len(events) == 1
    event = events[0]
    assert event.family == "routing"
    assert event.name == "outcome_resolved"
    assert event.payload["event"] == "product_question"
    # Authored transition resolves to its id.
    assert event.payload["transition_id"] == "t_product_question"
    trace = event.payload["classifier_trace"]
    assert trace["chosen_label"] == "product_question"
    assert trace["model"] == "gemma-test"
    assert trace["backend"] == "transformers"


def test_interpreter_passes_agent_version_through_to_classifier() -> None:
    classifier = FakeClassifier(
        ClassificationResult(chosen_label="product_question", confidence=0.9)
    )
    interpreter = GemmaLocalInterpreter(classifier=classifier)
    document = _document_with_outcomes("product_question")
    step = document.steps[0]

    interpreter.interpret(
        agent_document=document,
        step=step,
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_make_turn("hi"),
    )

    request = classifier.last_request
    assert request is not None
    assert request.agent_version_id == document.version
    assert request.step_id == "qa"
    assert "product_question" in request.candidate_labels


def test_interpreter_populates_prefix_and_suffix_via_prompt_assembler() -> None:
    classifier = FakeClassifier(
        ClassificationResult(chosen_label="product_question", confidence=0.9)
    )
    interpreter = GemmaLocalInterpreter(classifier=classifier)
    document = _document_with_outcomes("product_question")
    step = document.steps[0]

    from ruhu.classifier.prompt import (
        build_classifier_prefix,
        build_classifier_suffix,
        reset_prefix_cache,
    )

    reset_prefix_cache()

    interpreter.interpret(
        agent_document=document,
        step=step,
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_make_turn("describe the product"),
    )

    request = classifier.last_request
    assert request is not None
    assert request.prefix == build_classifier_prefix(document, step)
    assert request.suffix == build_classifier_suffix("describe the product")


# ── universal outcomes (kernel-injected) ───────────────────────────────────


def test_interpreter_emits_outcome_resolved_for_universal_with_no_transition_id() -> None:
    """The classifier picks a universal outcome (e.g. ``audio_check``)
    that's in the catalog by virtue of ``UNIVERSAL_OUTCOMES`` but has
    no authored transition. The interpreter must still emit the routing
    event so the kernel's framework-side handler can fire; the
    ``transition_id`` payload is ``None`` to signal "no authored route"."""
    classifier = FakeClassifier(
        ClassificationResult(chosen_label="audio_check", confidence=0.85)
    )
    interpreter = GemmaLocalInterpreter(classifier=classifier)
    document = _document_with_outcomes("product_question")  # no audio_check edge
    step = document.steps[0]

    events = interpreter.interpret(
        agent_document=document,
        step=step,
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_make_turn("are you there?"),
    )

    assert len(events) == 1
    assert events[0].payload["event"] == "audio_check"
    assert events[0].payload["transition_id"] is None


# ── degraded paths ─────────────────────────────────────────────────────────


def test_interpreter_emits_classifier_unavailable_for_unknown_label() -> None:
    classifier = FakeClassifier(
        ClassificationResult(chosen_label=None, confidence=0.0)
    )
    interpreter = GemmaLocalInterpreter(classifier=classifier)
    document = _document_with_outcomes("product_question")
    step = document.steps[0]

    events = interpreter.interpret(
        agent_document=document,
        step=step,
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_make_turn("blah"),
    )

    assert len(events) == 1
    assert events[0].family == "routing"
    assert events[0].name == "classifier_unavailable"
    assert "prefill_unknown" in events[0].payload["reason"]


def test_interpreter_emits_classifier_unavailable_for_label_outside_catalog() -> None:
    classifier = FakeClassifier(
        ClassificationResult(chosen_label="not_in_catalog", confidence=0.7)
    )
    interpreter = GemmaLocalInterpreter(classifier=classifier)
    document = _document_with_outcomes("product_question")
    step = document.steps[0]

    events = interpreter.interpret(
        agent_document=document,
        step=step,
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_make_turn("blah"),
    )

    assert len(events) == 1
    assert events[0].name == "classifier_unavailable"
    assert "prefill_label_out_of_catalog" in events[0].payload["reason"]
    assert "not_in_catalog" in events[0].payload["reason"]


def test_interpreter_emits_classifier_unavailable_when_backend_errors() -> None:
    classifier = FakeClassifier(
        ClassificationResult(chosen_label=None, confidence=0.0, error="timeout")
    )
    interpreter = GemmaLocalInterpreter(classifier=classifier)
    document = _document_with_outcomes("product_question")
    step = document.steps[0]

    events = interpreter.interpret(
        agent_document=document,
        step=step,
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_make_turn("anything"),
    )

    assert len(events) == 1
    assert events[0].name == "classifier_unavailable"
    assert "prefill_backend_error" in events[0].payload["reason"]


# ── empty / degenerate inputs ──────────────────────────────────────────────


def test_interpreter_returns_empty_for_empty_user_text() -> None:
    classifier = FakeClassifier(
        ClassificationResult(chosen_label="product_question", confidence=0.9)
    )
    interpreter = GemmaLocalInterpreter(classifier=classifier)
    document = _document_with_outcomes("product_question")
    step = document.steps[0]

    events = interpreter.interpret(
        agent_document=document,
        step=step,
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_make_turn(""),
    )
    assert events == []
    # And the classifier was never called.
    assert classifier.last_request is None


# ── weight loader / SHA helpers ────────────────────────────────────────────


def test_sha256_file_matches_known_content(tmp_path: Path) -> None:
    sample = tmp_path / "sample.bin"
    payload = b"prefill-first-classifier-test-bytes"
    sample.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    assert _sha256_file(sample) == expected


def test_expected_sha256_for_model_path_uses_known_manifest(monkeypatch) -> None:
    """The ``KNOWN_GEMMA_MODEL_SHA256`` manifest is read by name; an
    explicit env override wins. Today the manifest is empty (no pinned
    snapshots) — we just verify the env-override hook is wired."""
    monkeypatch.setenv("RUHU_GEMMA_MODEL_SHA256", "deadbeef")
    assert _expected_sha256_for_model_path("/tmp/anything") == "deadbeef"
