from datetime import datetime, timezone

from ruhu import ConversationKernel
from ruhu.schemas import RuntimeTurn
from tests._fixtures.templates import load_template_agent_document
from tests._fixtures.interpreters import sales_interpreter
from tests.tooling import build_demo_tool_runtime


def test_kernel_appends_trace_records() -> None:
    agent_doc = load_template_agent_document("sales-agent.json")
    kernel = ConversationKernel(interpreter=sales_interpreter(), tool_runtime=build_demo_tool_runtime())
    kernel.start_conversation("conv_1", agent_document=agent_doc, agent_id="test_agent")

    kernel.process_turn(
        "conv_1",
        RuntimeTurn(
            turn_id="turn_1",
            dedupe_key="turn_1",
            channel="web_chat",
            modality="text",
            event_type="user_message",
            text="Tell me about pricing",
            received_at=datetime.now(timezone.utc),
        ),
        agent_document=agent_doc,
    )

    traces = kernel.trace_store.all()
    assert len(traces) == 2
    assert traces[-1].conversation_id == "conv_1"
    assert traces[-1].step_before == "discover"


def test_duplicate_dedupe_key_is_ignored() -> None:
    agent_doc = load_template_agent_document("sales-agent.json")
    kernel = ConversationKernel(interpreter=sales_interpreter(), tool_runtime=build_demo_tool_runtime())
    kernel.start_conversation("conv_1", agent_document=agent_doc, agent_id="test_agent")

    turn = RuntimeTurn(
        turn_id="turn_1",
        dedupe_key="turn_1",
        channel="web_chat",
        modality="text",
        event_type="user_message",
        text="Tell me about pricing",
        received_at=datetime.now(timezone.utc),
    )

    first = kernel.process_turn("conv_1", turn, agent_document=agent_doc)
    second = kernel.process_turn("conv_1", turn, agent_document=agent_doc)

    assert first.turn_id == "turn_1"
    assert second.chosen_action.reason == "duplicate_dedupe_key"
