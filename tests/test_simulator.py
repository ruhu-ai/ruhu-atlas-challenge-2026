from ruhu.simulator import simulate_transcript
from tests._fixtures.templates import load_template_agent_document
from tests._fixtures.interpreters import sales_interpreter
from tests.tooling import build_demo_tool_runtime


def test_simulator_runs_demo_transcript() -> None:
    run = simulate_transcript(
        load_template_agent_document("sales-agent.json"),
        ["Can you explain what the product does?", "I also want a demo"],
        interpreter=sales_interpreter(),
        tool_runtime=build_demo_tool_runtime(),
    )

    assert run.start.step_after == "discover"
    assert len(run.turns) == 2
    assert run.final_step_id == "collect_booking_details"


def test_sales_agent_routes_ruhu_overview_phrases_to_knowledge_lookup() -> None:
    run = simulate_transcript(
        load_template_agent_document("sales-agent.json"),
        ["describe ruhu", "help explain ruhu"],
        interpreter=sales_interpreter(),
        tool_runtime=build_demo_tool_runtime(),
    )

    for turn in run.turns:
        assert turn.step_after == "product_qa"
        assert [message.text for message in turn.emitted_messages] == [
            "Ruhu helps businesses build phone, WhatsApp, and web chat agents with shared workflows."
        ]
        assert turn.semantic_events[-1].payload["facts"]["last_knowledge_query"] == "Ruhu product overview"


def test_simulator_keeps_requested_conversation_id() -> None:
    run = simulate_transcript(
        load_template_agent_document("sales-agent.json"),
        ["Can you explain what the product does?"],
        conversation_id="conv_demo_1",
        interpreter=sales_interpreter(),
        tool_runtime=build_demo_tool_runtime(),
    )

    assert run.start.conversation_id == "conv_demo_1"
    assert run.turns[0].conversation_id == "conv_demo_1"
