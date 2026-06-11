"""Cross-scenario step transitions: kernel keeps current_scenario_id in sync.

Authors can target steps in a different scenario via a step transition's
`to_step_id`. The kernel's multi-hop loop updates current_scenario_id when
this happens so trace fields and scenario-route gates downstream remain
consistent. See ConversationKernel._process_step_turn.
"""
from datetime import datetime, timezone

from ruhu import ConversationKernel
from ruhu.agent_document import AgentDocument, Scenario, Step, StepTransition
from ruhu.heuristics import KeywordInterpreter
from ruhu.schemas import OtherwiseCondition, RuntimeTurn


def _build_two_scenario_document() -> AgentDocument:
    return AgentDocument(
        start_scenario_id="sales",
        scenarios=[
            Scenario(
                id="sales",
                name="Sales",
                start_step_id="sales_start",
                steps=[
                    Step(
                        id="sales_start",
                        name="Sales Start",
                        transitions=[
                            StepTransition(
                                id="t_to_pricing",
                                when=OtherwiseCondition(),
                                to_step_id="pricing_quote",
                            )
                        ],
                    )
                ],
            ),
            Scenario(
                id="pricing",
                name="Pricing",
                start_step_id="pricing_quote",
                steps=[
                    Step(
                        id="pricing_quote",
                        name="Pricing Quote",
                        say="Here are our plans.",
                        # Self-loop so the multi-hop terminates without
                        # converting chosen_action to "end".
                        transitions=[
                            StepTransition(
                                id="t_pricing_stay",
                                when=OtherwiseCondition(),
                                to_step_id="pricing_quote",
                            )
                        ],
                    )
                ],
            ),
        ],
    )


def test_cross_scenario_step_transition_updates_current_scenario_id() -> None:
    document = _build_two_scenario_document()
    kernel = ConversationKernel(interpreter=KeywordInterpreter(rules={}))
    kernel.start_conversation(
        "conv_cross", agent_document=document, agent_id="test_agent"
    )

    result = kernel.process_turn(
        "conv_cross",
        RuntimeTurn(
            turn_id="t1",
            dedupe_key="t1",
            channel="web_chat",
            modality="text",
            event_type="user_message",
            text="anything",
            received_at=datetime.now(timezone.utc),
        ),
        agent_document=document,
    )

    # The cross-scenario transition fires (otherwise) and lands on
    # pricing_quote in the "pricing" scenario.
    assert result.step_after == "pricing_quote"
    assert document.scenario_for_step_id(result.step_after).id == "pricing"

    # The persisted conversation's step is in the new scenario.
    conversation = kernel.conversation_store.load("conv_cross")
    assert conversation is not None
    assert conversation.step_id == "pricing_quote"
    assert document.scenario_for_step_id(conversation.step_id).id == "pricing"
