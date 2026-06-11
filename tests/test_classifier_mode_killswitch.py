"""Tests for WI-5.5 — RUHU_CLASSIFIER_MODE runtime kill-switch."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ruhu.agent_document import AgentDocument, Scenario, Step, StepCompletion
from ruhu.heuristics import (
    KeywordInterpreter,
    NullInterpreter,
    interpreter_for_classifier_mode,
)
from ruhu.runtime_config import RuntimeSettings
from ruhu.schemas import RuntimeTurn


def _doc() -> AgentDocument:
    step = Step(
        id="entry",
        name="Entry",
        completion=StepCompletion(disposition="resolved"),
    )
    return AgentDocument(
        version="v1",
        start_scenario_id="main",
        scenarios=[Scenario(id="main", name="Main", start_step_id="entry", steps=[step])],
    )


def _turn(text: str = "hi") -> RuntimeTurn:
    return RuntimeTurn(
        turn_id="t",
        dedupe_key="d",
        channel="web_chat",
        modality="text",
        event_type="user_message",
        text=text,
        received_at=datetime.now(timezone.utc),
    )


# ── RuntimeSettings env loading ─────────────────────────────────────────────


def test_classifier_mode_defaults_to_single(monkeypatch) -> None:
    monkeypatch.delenv("RUHU_CLASSIFIER_MODE", raising=False)
    settings = RuntimeSettings.from_env()
    assert settings.classifier_mode == "single"


def test_classifier_mode_accepts_off(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_CLASSIFIER_MODE", "off")
    settings = RuntimeSettings.from_env()
    assert settings.classifier_mode == "off"


def test_classifier_mode_lowercases_and_trims(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_CLASSIFIER_MODE", "  OFF  ")
    settings = RuntimeSettings.from_env()
    assert settings.classifier_mode == "off"


def test_classifier_mode_rejects_unknown_value(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_CLASSIFIER_MODE", "fictional")
    with pytest.raises(ValueError, match="single/off"):
        RuntimeSettings.from_env()


# ── NullInterpreter behaviour ──────────────────────────────────────────────


def test_null_interpreter_returns_empty_events_regardless_of_input() -> None:
    interpreter = NullInterpreter()
    events = interpreter.interpret(
        agent_document=_doc(),
        step=_doc().steps[0],
        agent_id="a",
        agent_name="A",
        conversation_facts={},
        turn=_turn("anything"),
    )
    assert events == []


def test_null_interpreter_returns_empty_for_empty_text() -> None:
    interpreter = NullInterpreter()
    events = interpreter.interpret(
        agent_document=_doc(),
        step=_doc().steps[0],
        agent_id="a",
        agent_name="A",
        conversation_facts={},
        turn=_turn(""),
    )
    assert events == []


# ── interpreter_for_classifier_mode wrapper ────────────────────────────────


def test_classifier_mode_off_replaces_interpreter_with_null() -> None:
    keyword = KeywordInterpreter(rules={"hello": ("hi",)})
    wrapped = interpreter_for_classifier_mode(keyword, classifier_mode="off")
    assert isinstance(wrapped, NullInterpreter)


def test_classifier_mode_single_passes_interpreter_through() -> None:
    keyword = KeywordInterpreter(rules={"hello": ("hi",)})
    wrapped = interpreter_for_classifier_mode(keyword, classifier_mode="single")
    assert wrapped is keyword


def test_classifier_mode_off_replaces_none_with_null() -> None:
    """Even when no interpreter is configured, mode='off' returns NullInterpreter
    so callers don't need to special-case that combination."""
    wrapped = interpreter_for_classifier_mode(None, classifier_mode="off")
    assert isinstance(wrapped, NullInterpreter)


def test_classifier_mode_single_with_none_returns_none() -> None:
    """Mode='single' is a no-op wrapper; absence of an interpreter passes through."""
    wrapped = interpreter_for_classifier_mode(None, classifier_mode="single")
    assert wrapped is None


def test_classifier_mode_unknown_value_treats_as_single() -> None:
    """Defensive: invalid mode strings shouldn't accidentally disable classification.
    The env-loader rejects bad values upstream; this is belt-and-braces."""
    keyword = KeywordInterpreter(rules={})
    wrapped = interpreter_for_classifier_mode(keyword, classifier_mode="unknown_mode")
    assert wrapped is keyword


# ── end-to-end: classifier_mode=off with interpreter wired through wrapper ─


def test_off_mode_through_wrapper_yields_empty_events() -> None:
    """Wiring NullInterpreter via the wrapper produces an empty event stream
    even though the underlying interpreter would have matched."""
    keyword = KeywordInterpreter(rules={"hello": ("hi",)})
    interpreter = interpreter_for_classifier_mode(keyword, classifier_mode="off")
    events = interpreter.interpret(
        agent_document=_doc(),
        step=_doc().steps[0],
        agent_id="a",
        agent_name="A",
        conversation_facts={},
        turn=_turn("hi there"),
    )
    assert events == []


def test_single_mode_through_wrapper_uses_underlying_interpreter() -> None:
    keyword = KeywordInterpreter(rules={"hello": ("hi",)})
    interpreter = interpreter_for_classifier_mode(keyword, classifier_mode="single")
    events = interpreter.interpret(
        agent_document=_doc(),
        step=_doc().steps[0],
        agent_id="a",
        agent_name="A",
        conversation_facts={},
        turn=_turn("hi there"),
    )
    # KeywordInterpreter matches "hi" → emits intent_detected:hello
    intents = [e.name for e in events if e.family == "intent_detected"]
    assert "hello" in intents
