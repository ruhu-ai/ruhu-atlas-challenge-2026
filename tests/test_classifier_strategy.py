"""Tests for ``ruhu.classifier_strategy.StrategyAwareInterpreter``.

Covers each strategy branch + failure surfaces in the edge-owned-outcomes
contract:

- ``off``                          → no events emitted
- ``main_llm`` (success)           → ``routing.outcome_resolved`` event
- ``main_llm`` (backend error)     → ``routing.classifier_unavailable``
- ``main_llm`` (no backend wired)  → falls through to prefill_interpreter
                                     when present, else
                                     ``routing.classifier_unavailable``
- ``prefill`` (eligible)           → delegates to inner prefill interpreter
- ``prefill`` (no LoRA)            → ``routing.classifier_unavailable``
- ``prefill`` (no resolver)        → ``routing.classifier_unavailable``
- default (no resolver)            → main_llm path used
- out-of-catalog return            → ``routing.classifier_unavailable``
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ruhu.agent_document import (
    AgentDocument,
    Scenario,
    Step,
    StepTransition,
)
from ruhu.api_models import (
    AgentClassifierConfig,
    AgentLLMConfig,
    AgentSettings,
)
from ruhu.classifier.protocol import ClassificationRequest, ClassificationResult
from ruhu.classifier_strategy import (
    LoRAEligibility,
    StrategyAwareInterpreter,
)
from ruhu.interpreter import SemanticInterpreter
from ruhu.schemas import OtherwiseCondition, OutcomeCondition, RuntimeTurn, SemanticEventRecord


def _build_step() -> Step:
    return Step(
        id="discover",
        name="Discover",
        transitions=[
            StepTransition(
                id="t_product",
                when=OutcomeCondition(
                    event="product_question",
                    description="The user asks about the product or its features.",
                ),
                to_step_id="answer_product",
            ),
            StepTransition(
                id="t_otherwise",
                when=OtherwiseCondition(),
                to_step_id="discover",
            ),
        ],
    )


def _build_document() -> AgentDocument:
    return AgentDocument(
        start_scenario_id="main",
        scenarios=[
            Scenario(
                id="main",
                name="Main",
                start_step_id="discover",
                steps=[
                    _build_step(),
                    Step(id="answer_product", name="Answer Product"),
                ],
            )
        ],
    )


def _build_turn(text: str = "describe ruhu") -> RuntimeTurn:
    return RuntimeTurn(
        turn_id="t1",
        dedupe_key="t1",
        channel="web_chat",
        modality="text",
        event_type="user_message",
        text=text,
        received_at=datetime.now(timezone.utc),
    )


class _FakeBackend:
    """Stand-in ``PrefillClassifier`` that returns a pre-canned result."""

    def __init__(self, result: ClassificationResult | Exception):
        self._result = result
        self.calls: list[ClassificationRequest] = []

    def classify(self, request: ClassificationRequest) -> ClassificationResult:
        self.calls.append(request)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _RecordingInterpreter(SemanticInterpreter):
    """Captures whether ``interpret`` was called and returns a fixed event."""

    def __init__(self) -> None:
        self.called = False

    def interpret(self, **_: Any) -> list[SemanticEventRecord]:
        self.called = True
        return [
            SemanticEventRecord(
                family="routing",
                name="outcome_resolved",
                source="classifier",
                confidence=0.95,
                payload={
                    "event": "product_question",
                    "transition_id": "t_product",
                    "classifier_trace": {"backend": "fake_prefill"},
                },
            )
        ]


def _settings_for(strategy: str) -> AgentSettings:
    return AgentSettings(
        llm_config=AgentLLMConfig(
            classifier=AgentClassifierConfig(strategy=strategy),  # type: ignore[arg-type]
        )
    )


# ─── strategy = off ──────────────────────────────────────────────────────


def test_off_strategy_emits_no_events() -> None:
    interp = StrategyAwareInterpreter(
        settings_resolver=lambda _aid: _settings_for("off"),
        main_llm_classifier=_FakeBackend(
            ClassificationResult(chosen_label="product_question", confidence=0.9)
        ),
    )
    events = interp.interpret(
        agent_document=_build_document(),
        step=_build_step(),
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_build_turn(),
    )
    assert events == []


# ─── strategy = main_llm ─────────────────────────────────────────────────


def test_main_llm_success_emits_routing_outcome_resolved() -> None:
    backend = _FakeBackend(
        ClassificationResult(
            chosen_label="product_question",
            confidence=0.9,
            backend="vertex_gemini",
        )
    )
    interp = StrategyAwareInterpreter(
        settings_resolver=lambda _aid: _settings_for("main_llm"),
        main_llm_classifier=backend,
        main_llm_model_name="gemini-3-flash-preview",
    )
    events = interp.interpret(
        agent_document=_build_document(),
        step=_build_step(),
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_build_turn(),
    )
    assert len(events) == 1
    event = events[0]
    assert event.family == "routing"
    assert event.name == "outcome_resolved"
    assert event.source == "classifier"
    assert event.payload["event"] == "product_question"
    # The transition id is resolved from the step's outcome edges.
    assert event.payload["transition_id"] == "t_product"
    assert event.payload["classifier_trace"]["chosen_label"] == "product_question"
    assert event.payload["classifier_trace"]["strategy"] == "main_llm"


def test_main_llm_universal_outcome_resolves_with_no_transition_id() -> None:
    """A universal outcome (e.g. ``audio_check``) is in the catalog but has
    no authored transition on this step; the strategy interpreter still
    emits the routing event so the kernel can fire its framework-side
    handler. ``transition_id`` is None to signal "no authored route"."""
    backend = _FakeBackend(
        ClassificationResult(chosen_label="audio_check", confidence=0.85)
    )
    interp = StrategyAwareInterpreter(
        settings_resolver=lambda _aid: _settings_for("main_llm"),
        main_llm_classifier=backend,
    )
    events = interp.interpret(
        agent_document=_build_document(),
        step=_build_step(),
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_build_turn(),
    )
    assert len(events) == 1
    assert events[0].payload["event"] == "audio_check"
    assert events[0].payload["transition_id"] is None


def test_main_llm_backend_error_emits_classifier_unavailable() -> None:
    backend = _FakeBackend(
        ClassificationResult(
            chosen_label=None,
            confidence=0.0,
            error="timeout",
        )
    )
    interp = StrategyAwareInterpreter(
        settings_resolver=lambda _aid: _settings_for("main_llm"),
        main_llm_classifier=backend,
    )
    events = interp.interpret(
        agent_document=_build_document(),
        step=_build_step(),
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_build_turn(),
    )
    assert len(events) == 1
    assert events[0].family == "routing"
    assert events[0].name == "classifier_unavailable"
    assert "main_llm_backend_error" in events[0].payload["reason"]
    assert "timeout" in events[0].payload["reason"]


def test_main_llm_unknown_label_emits_classifier_unavailable() -> None:
    backend = _FakeBackend(
        ClassificationResult(chosen_label=None, confidence=0.5)
    )
    interp = StrategyAwareInterpreter(
        settings_resolver=lambda _aid: _settings_for("main_llm"),
        main_llm_classifier=backend,
    )
    events = interp.interpret(
        agent_document=_build_document(),
        step=_build_step(),
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_build_turn(),
    )
    assert len(events) == 1
    assert events[0].name == "classifier_unavailable"
    assert "main_llm_unknown" in events[0].payload["reason"]


def test_main_llm_out_of_catalog_emits_classifier_unavailable() -> None:
    """Backend returned a label not in the step's catalog. Today's silent
    miss is now visible — the kernel sees a classifier_unavailable signal
    with the offending label in the reason."""
    backend = _FakeBackend(
        ClassificationResult(chosen_label="not_in_catalog", confidence=0.7)
    )
    interp = StrategyAwareInterpreter(
        settings_resolver=lambda _aid: _settings_for("main_llm"),
        main_llm_classifier=backend,
    )
    events = interp.interpret(
        agent_document=_build_document(),
        step=_build_step(),
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_build_turn(),
    )
    assert len(events) == 1
    assert events[0].name == "classifier_unavailable"
    assert "main_llm_label_out_of_catalog" in events[0].payload["reason"]
    assert "not_in_catalog" in events[0].payload["reason"]


def test_main_llm_no_backend_configured_falls_through_to_prefill_interpreter() -> None:
    """Dev/test deploys without Vertex creds reuse the configured kernel
    interpreter (keyword/Gemma/etc.) as the main_llm fallback so legacy
    behaviour keeps working."""
    inner = _RecordingInterpreter()
    interp = StrategyAwareInterpreter(
        settings_resolver=lambda _aid: _settings_for("main_llm"),
        main_llm_classifier=None,
        prefill_interpreter=inner,
    )
    events = interp.interpret(
        agent_document=_build_document(),
        step=_build_step(),
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_build_turn(),
    )
    assert inner.called is True
    assert all(event.name != "classifier_unavailable" for event in events)
    assert events[0].family == "routing"
    assert events[0].name == "outcome_resolved"


def test_main_llm_no_backend_no_fallback_emits_unavailable() -> None:
    interp = StrategyAwareInterpreter(
        settings_resolver=lambda _aid: _settings_for("main_llm"),
        main_llm_classifier=None,
        prefill_interpreter=None,
    )
    events = interp.interpret(
        agent_document=_build_document(),
        step=_build_step(),
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_build_turn(),
    )
    assert len(events) == 1
    assert events[0].name == "classifier_unavailable"
    assert events[0].payload["reason"] == "main_llm_not_configured"


def test_main_llm_classifier_exception_emits_unavailable() -> None:
    backend = _FakeBackend(RuntimeError("network hiccup"))
    interp = StrategyAwareInterpreter(
        settings_resolver=lambda _aid: _settings_for("main_llm"),
        main_llm_classifier=backend,
    )
    events = interp.interpret(
        agent_document=_build_document(),
        step=_build_step(),
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_build_turn(),
    )
    assert len(events) == 1
    assert events[0].name == "classifier_unavailable"
    assert "main_llm_exception" in events[0].payload["reason"]


# ─── strategy = prefill ──────────────────────────────────────────────────


def test_prefill_eligible_delegates_to_prefill_interpreter() -> None:
    inner = _RecordingInterpreter()
    interp = StrategyAwareInterpreter(
        settings_resolver=lambda _aid: _settings_for("prefill"),
        prefill_interpreter=inner,
        lora_eligibility_resolver=lambda _aid, _sid: LoRAEligibility(
            available=True, lora_name="ruhu-sales-v1"
        ),
    )
    events = interp.interpret(
        agent_document=_build_document(),
        step=_build_step(),
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_build_turn(),
    )
    assert inner.called is True
    assert len(events) == 1
    assert events[0].name == "outcome_resolved"


def test_prefill_no_lora_emits_unavailable_and_skips_prefill() -> None:
    inner = _RecordingInterpreter()
    interp = StrategyAwareInterpreter(
        settings_resolver=lambda _aid: _settings_for("prefill"),
        prefill_interpreter=inner,
        lora_eligibility_resolver=lambda _aid, _sid: LoRAEligibility(
            available=False, reason="no_production_lora"
        ),
    )
    events = interp.interpret(
        agent_document=_build_document(),
        step=_build_step(),
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_build_turn(),
    )
    assert inner.called is False
    assert len(events) == 1
    assert events[0].name == "classifier_unavailable"
    assert events[0].payload["reason"] == "no_production_lora"


def test_prefill_without_resolver_rejects_conservatively() -> None:
    inner = _RecordingInterpreter()
    interp = StrategyAwareInterpreter(
        settings_resolver=lambda _aid: _settings_for("prefill"),
        prefill_interpreter=inner,
        lora_eligibility_resolver=None,
    )
    events = interp.interpret(
        agent_document=_build_document(),
        step=_build_step(),
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_build_turn(),
    )
    assert inner.called is False
    assert len(events) == 1
    assert events[0].name == "classifier_unavailable"
    assert events[0].payload["reason"] == "lora_eligibility_resolver_missing"


# ─── default behaviour ───────────────────────────────────────────────────


def test_no_settings_resolver_uses_default_strategy() -> None:
    """No resolver → falls back to ``default_strategy`` (main_llm)."""
    backend = _FakeBackend(
        ClassificationResult(chosen_label="product_question", confidence=0.9)
    )
    interp = StrategyAwareInterpreter(
        settings_resolver=None,
        main_llm_classifier=backend,
        default_strategy="main_llm",
    )
    events = interp.interpret(
        agent_document=_build_document(),
        step=_build_step(),
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_build_turn(),
    )
    assert len(backend.calls) == 1
    assert len(events) == 1
    assert events[0].name == "outcome_resolved"


def test_settings_resolver_exception_falls_back_to_default() -> None:
    def exploding_resolver(_aid: str) -> AgentSettings | None:
        raise RuntimeError("boom")

    backend = _FakeBackend(
        ClassificationResult(chosen_label="product_question", confidence=0.9)
    )
    interp = StrategyAwareInterpreter(
        settings_resolver=exploding_resolver,
        main_llm_classifier=backend,
    )
    events = interp.interpret(
        agent_document=_build_document(),
        step=_build_step(),
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_build_turn(),
    )
    assert len(events) == 1
    assert events[0].name == "outcome_resolved"


def test_empty_user_text_returns_no_events_regardless_of_strategy() -> None:
    backend = _FakeBackend(
        ClassificationResult(chosen_label="product_question", confidence=0.9)
    )
    interp = StrategyAwareInterpreter(
        settings_resolver=lambda _aid: _settings_for("main_llm"),
        main_llm_classifier=backend,
    )
    events = interp.interpret(
        agent_document=_build_document(),
        step=_build_step(),
        agent_id="agent_x",
        agent_name="Agent X",
        conversation_facts={},
        turn=_build_turn(text=""),
    )
    assert events == []
    assert backend.calls == []
