from __future__ import annotations

from pathlib import Path

from ruhu.interpreters import AgentInterpreterRouter
from ruhu.loader import load_agent_document_source
from ruhu.schemas import RuntimeTurn
from tests._fixtures.interpreters import (
    sales_interpreter,
    support_triage_interpreter,
)

_FIXTURE_AGENTS = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"


def test_interpreter_router_routes_by_agent_id() -> None:
    sales_document, sales_agent_id, sales_agent_name = load_agent_document_source(
        _FIXTURE_AGENTS / "sales.json"
    )
    support_document, support_agent_id, support_agent_name = load_agent_document_source(
        _FIXTURE_AGENTS / "support_triage.json"
    )

    router = AgentInterpreterRouter(
        agent_interpreters={
            sales_agent_id: sales_interpreter(),
            support_agent_id: support_triage_interpreter(),
        }
    )

    sales_step = sales_document.step_by_id("discover")
    support_step = support_document.step_by_id("triage")
    product_turn = RuntimeTurn.model_validate(
        {
            "turn_id": "turn_1",
            "dedupe_key": "turn_1",
            "channel": "web_chat",
            "modality": "text",
            "event_type": "user_message",
            "text": "Tell me what the product does.",
            "received_at": "2026-04-10T00:00:00Z",
        }
    )
    support_turn = RuntimeTurn.model_validate(
        {
            "turn_id": "turn_2",
            "dedupe_key": "turn_2",
            "channel": "web_chat",
            "modality": "text",
            "event_type": "user_message",
            "text": "I need support with billing.",
            "received_at": "2026-04-10T00:00:00Z",
        }
    )

    sales_events = router.interpret(
        agent_document=sales_document,
        step=sales_step,
        agent_id=sales_agent_id,
        agent_name=sales_agent_name,
        conversation_facts={},
        turn=product_turn,
    )
    support_events = router.interpret(
        agent_document=support_document,
        step=support_step,
        agent_id=support_agent_id,
        agent_name=support_agent_name,
        conversation_facts={},
        turn=support_turn,
    )

    assert any(
        event.family == "routing"
        and event.name == "outcome_resolved"
        and event.payload.get("event") == "product_question"
        for event in sales_events
    )
    assert any(
        event.family == "routing"
        and event.name == "outcome_resolved"
        and event.payload.get("event") == "support_request"
        for event in support_events
    )
