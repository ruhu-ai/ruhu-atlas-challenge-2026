"""Kernel observability regressions.

Two related symptoms surfaced in production sales-agent testing:

1. Knowledge responses leaked diagnostic fields (``"based on our standard
   knowledge lookup"``, ``"passed with a high grade"``, ``"based on our
   ruhu-sales-knowledge document"``). The dialogue generator was given the
   full knowledge tool result — including ``retrieval_mode``, evaluation
   ``grade``, and source ``title`` — via ``user_visible_fields``, then told
   to "Must mention" each key. The LLM dutifully recited the values.

2. When the kernel's interpreter returned ``intent_tags:classifier_unavailable``
   (e.g. Vertex Gemini auth failed), that signal disappeared. The kernel
   silently degraded to "no intent detected" and the agent stayed put,
   making it impossible to distinguish a routing miss from a classifier
   outage during incident response.

These tests exist to keep both fixes honest.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ruhu import ConversationKernel
from ruhu.agent_document import AgentDocument, Scenario, Step, StepTransition
from ruhu.interpreter import SemanticInterpreter
from ruhu.kernel import ConversationKernel as KernelClass
from ruhu.schemas import (
    OtherwiseCondition,
    OutcomeCondition,
    RuntimeTurn,
    SemanticEventRecord,
)


# ── _knowledge_outcome_user_visible_fields scope ────────────────────────────


def test_user_visible_fields_only_exposes_knowledge_context() -> None:
    """The grounding payload sent to the LLM must contain ``knowledge_context``
    and nothing else. Diagnostic fields stay on the trace, not on the LLM
    contract."""
    payload = {
        "status": "success",
        "context_block": "Ruhu helps sales teams qualify leads.",
        "retrieval_mode": "standard",
        "evaluation": {"grade": "A", "score": 0.91},
        "top_hit": {"title": "ruhu-sales-knowledge", "document_id": "doc-1"},
        "hits": [{"title": "ruhu-sales-knowledge"}],
    }
    visible = KernelClass._knowledge_outcome_user_visible_fields(payload)
    assert visible == {"knowledge_context": "Ruhu helps sales teams qualify leads."}


def test_user_visible_fields_drops_internal_diagnostic_keys() -> None:
    """Regression: every internal diagnostic key must be absent so the LLM
    can't recite it as ``Must mention: knowledge_lookup_grade``."""
    payload = {
        "context_block": "Pricing is per-seat.",
        "retrieval_mode": "deep",
        "evaluation": {"grade": "B+"},
        "top_hit": {"title": "pricing-doc"},
    }
    visible = KernelClass._knowledge_outcome_user_visible_fields(payload)
    assert "knowledge_lookup_mode" not in visible
    assert "knowledge_lookup_grade" not in visible
    assert "knowledge_top_title" not in visible
    assert "evaluation" not in visible
    assert "retrieval_mode" not in visible


def test_user_visible_fields_falls_back_to_message_when_no_context_block() -> None:
    payload = {"status": "success", "message": "No relevant chunks found."}
    visible = KernelClass._knowledge_outcome_user_visible_fields(payload)
    assert visible == {"knowledge_context": "No relevant chunks found."}


def test_user_visible_fields_returns_empty_for_empty_payload() -> None:
    assert KernelClass._knowledge_outcome_user_visible_fields(None) == {}
    assert KernelClass._knowledge_outcome_user_visible_fields({}) == {}
    assert KernelClass._knowledge_outcome_user_visible_fields(
        {"context_block": "   "}
    ) == {}


# ── classifier_unavailable observability ────────────────────────────────────


class _UnavailableInterpreter(SemanticInterpreter):
    """Interpreter that emits the workflow-routing classifier-unavailable
    signal exactly as ``StrategyAwareInterpreter`` would when Vertex
    Gemini misbehaves (timeout, auth failure, out-of-catalog label, …)."""

    def interpret(self, **_: object) -> list[SemanticEventRecord]:
        return [
            SemanticEventRecord(
                family="routing",
                name="classifier_unavailable",
                source="system",
                confidence=1.0,
                payload={
                    "strategy": "main_llm",
                    "reason": "main_llm_backend_error:auth_failed",
                },
            )
        ]


class _FailingRealtimeBridge:
    def record_conversation_started(self, *_: object, **__: object) -> None:
        return None

    def record_turn(self, **_: object) -> None:
        raise BrokenPipeError("projection stream closed")


def _single_step_document() -> AgentDocument:
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
                        transitions=[
                            StepTransition(
                                id="t_pricing",
                                when=OutcomeCondition(
                                    event="pricing_question",
                                    description="The user is asking about pricing or plans.",
                                ),
                                to_step_id="qa",
                            ),
                            StepTransition(
                                id="t_stay",
                                when=OtherwiseCondition(),
                                to_step_id="qa",
                            ),
                        ],
                    )
                ],
            )
        ],
    )


def test_classifier_unavailable_preserved_in_trace_and_observability(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the interpreter emits ``routing.classifier_unavailable`` the
    kernel must:
      1. Keep that semantic event on the result so traces show why
         classification didn't run.
      2. Set ``decision_observability.degraded_mode = "classifier_unavailable"``
         and a fallback_reason that includes the upstream reason.
      3. Emit a ``logger.warning`` with agent_id, step_id, strategy, reason
         (operators get the signal without it ever reaching user text).
    """
    document = _single_step_document()
    kernel = ConversationKernel(interpreter=_UnavailableInterpreter())
    kernel.start_conversation(
        "conv_unavail", agent_document=document, agent_id="agent_x"
    )

    with caplog.at_level("WARNING", logger="ruhu.kernel"):
        result = kernel.process_turn(
            "conv_unavail",
            RuntimeTurn(
                turn_id="t1",
                dedupe_key="t1",
                channel="web_chat",
                modality="text",
                event_type="user_message",
                text="explain ruhu's pricing",
                received_at=datetime.now(timezone.utc),
            ),
            agent_document=document,
        )

    # 1. The classifier_unavailable event survives onto the result.
    unavailable = [
        event
        for event in result.semantic_events
        if event.family == "routing" and event.name == "classifier_unavailable"
    ]
    assert len(unavailable) == 1
    assert unavailable[0].payload["reason"] == "main_llm_backend_error:auth_failed"
    assert unavailable[0].payload["strategy"] == "main_llm"

    # 2. The trace's observability captures the degraded mode + reason.
    traces = kernel.trace_store.by_conversation("conv_unavail")
    matching_traces = [t for t in traces if t.turn_id == "t1"]
    assert matching_traces, "expected a trace for the turn"
    trace = matching_traces[-1]
    assert trace.decision_observability.degraded_mode == "classifier_unavailable"
    assert trace.decision_observability.fallback_reason == (
        "classifier_unavailable:main_llm_backend_error:auth_failed"
    )
    assert trace.decision_observability.fallback_used is True

    # 3. A structured warning was emitted for operators.
    matching = [
        record
        for record in caplog.records
        if record.levelname == "WARNING" and "classifier unavailable" in record.message
    ]
    assert matching, "expected a classifier-unavailable warning in the log"
    rendered = matching[0].getMessage()
    assert "agent_x" in rendered
    assert "qa" in rendered
    assert "main_llm" in rendered
    assert "main_llm_backend_error:auth_failed" in rendered

    # 4. The unavailable signal must NOT leak into rendered user-facing
    # messages (the kernel only emits intent_detected events to user text).
    for message in result.emitted_messages:
        assert "classifier_unavailable" not in (message.text or "")
        assert "main_llm_backend_error" not in (message.text or "")


def test_realtime_projection_failure_does_not_fail_committed_turn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    document = _single_step_document()
    kernel = ConversationKernel(realtime_bridge=_FailingRealtimeBridge())
    kernel.start_conversation(
        "conv_projection_failure", agent_document=document, agent_id="agent_x"
    )

    with caplog.at_level("ERROR", logger="ruhu.kernel"):
        result = kernel.process_turn(
            "conv_projection_failure",
            RuntimeTurn(
                turn_id="t1",
                dedupe_key="t1",
                channel="web_widget",
                modality="audio",
                event_type="user_final_transcript",
                text="I want pricing",
                received_at=datetime.now(timezone.utc),
            ),
            agent_document=document,
        )

    assert result.trace_id
    assert kernel.load_conversation("conv_projection_failure") is not None
    assert kernel.trace_store.by_conversation("conv_projection_failure")
    assert any(
        "realtime turn projection failed after turn commit" in record.getMessage()
        for record in caplog.records
    )
