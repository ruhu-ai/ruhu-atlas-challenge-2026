from __future__ import annotations

from datetime import datetime, timezone

from ruhu.agent_document import (
    AgentDocument,
    Scenario,
    Step,
    StepCompletion,
    StepTransition,
)
from ruhu.registry import AgentVersionSnapshot
from ruhu.schemas import (
    ActionRecord,
    Condition,
    ConversationState,
    FactDef,
    OtherwiseCondition,
    RenderedMessage,
    ToolBinding,
    ToolCallRecord,
    TurnTrace,
)
from ruhu.simulation_eval import (
    AssertionEngine,
    SimulationAssertion,
    SimulationFixture,
    SimulationTurnInput,
    validate_fixture,
)
from ruhu.tools.types import ToolCaller, ToolInvocation


def test_assertion_engine_evaluates_extended_assertions_and_uses_explicit_turn_count() -> None:
    now = datetime.now(timezone.utc)
    conversation = ConversationState(
        conversation_id="conv-1",
        organization_id="org-1",
        agent_id="sales_agent",
        agent_version_id="version-1",
        step_id="done",
        facts={"email": "user@example.com", "intent": "pricing"},
        updated_at=now,
    )
    traces = [
        TurnTrace(
            trace_id="trace-1",
            conversation_id="conv-1",
            organization_id="org-1",
            turn_id="turn-start",
            agent_id="sales_agent",
            agent_version_id="version-1",
            step_before="entry",
            step_after="discover",
            chosen_action=ActionRecord(type="transition", reason="start"),
            emitted_messages=[RenderedMessage(text="Hi there")],
            latency_breakdown_ms={"total": 5},
        ),
        TurnTrace(
            trace_id="trace-2",
            conversation_id="conv-1",
            organization_id="org-1",
            turn_id="turn-1",
            agent_id="sales_agent",
            agent_version_id="version-1",
            step_before="discover",
            step_after="done",
            chosen_action=ActionRecord(type="reply", reason="pricing_answered"),
            emitted_messages=[RenderedMessage(text="Please confirm pricing for user@example.com")],
            tool_calls=[
                ToolCallRecord(tool_ref="knowledge.lookup", status="success", reason="completed"),
                ToolCallRecord(tool_ref="knowledge.lookup", status="success", reason="completed"),
            ],
            latency_breakdown_ms={"total": 12},
        ),
    ]
    tool_invocations = [
        ToolInvocation(
            invocation_id="tool-1",
            tool_ref="knowledge.lookup",
            executor_kind="builtin",
            status="completed",
            caller=ToolCaller(channel="web_chat", conversation_id="conv-1", tenant_id="org-1"),
            created_at=now,
            updated_at=now,
        ),
        ToolInvocation(
            invocation_id="tool-2",
            tool_ref="knowledge.lookup",
            executor_kind="builtin",
            status="completed",
            caller=ToolCaller(channel="web_chat", conversation_id="conv-1", tenant_id="org-1"),
            created_at=now,
            updated_at=now,
        ),
    ]
    assertions = [
        SimulationAssertion(assertion_id="a1", kind="final_step_one_of", config={"step_ids": ["done", "handoff"]}),
        SimulationAssertion(assertion_id="a2", kind="fact_in", config={"fact_name": "intent", "values": ["pricing", "support"]}),
        SimulationAssertion(assertion_id="a3", kind="fact_matches_regex", config={"fact_name": "email", "pattern": ".+@example\\.com$"}),
        SimulationAssertion(assertion_id="a4", kind="tool_called_count_at_least", config={"tool_ref": "knowledge.lookup", "count": 2}),
        SimulationAssertion(assertion_id="a5", kind="tool_called_count_equals", config={"tool_ref": "knowledge.lookup", "count": 2}),
        SimulationAssertion(assertion_id="a6", kind="message_any_of", config={"texts": ["confirm pricing", "handoff"]}),
        SimulationAssertion(assertion_id="a7", kind="turn_count_equals", config={"count": 1}),
    ]

    results = AssertionEngine().evaluate(
        assertions,
        conversation=conversation,
        traces=traces,
        tool_invocations=tool_invocations,
        turn_count=1,
    )

    assert len(results) == 7
    assert all(result.passed for result in results)


def test_assertion_engine_reports_invalid_regex_and_message_failure() -> None:
    now = datetime.now(timezone.utc)
    conversation = ConversationState(
        conversation_id="conv-2",
        agent_id="sales_agent",
        agent_version_id="version-1",
        step_id="discover",
        facts={"email": "user@example.com"},
        updated_at=now,
    )

    results = AssertionEngine().evaluate(
        [
            SimulationAssertion(assertion_id="a1", kind="fact_matches_regex", config={"fact_name": "email", "pattern": "("}),
            SimulationAssertion(assertion_id="a2", kind="message_any_of", config={"texts": ["missing text"]}),
        ],
        conversation=conversation,
        traces=[],
        tool_invocations=[],
        turn_count=0,
    )

    assert len(results) == 2
    assert results[0].passed is False
    assert "invalid regex pattern" in (results[0].message or "")
    assert results[1].passed is False
    assert "expected a message containing any of" in (results[1].message or "")


def test_validate_fixture_reports_structural_and_reference_issues() -> None:
    fixture = SimulationFixture(
        fixture_id="fixture-1",
        agent_id="agent-1",
        name="broken fixture",
        gate_required=True,
        starting_step_id="missing_start",
        turns=[
            SimulationTurnInput(turn_id="turn-1", dedupe_key="dup", text=""),
            SimulationTurnInput(turn_id="turn-1", dedupe_key="dup", text="hello"),
        ],
        assertions=[
            SimulationAssertion(assertion_id="assert-1", kind="final_step_equals", config={"step_id": "missing_step"}),
            SimulationAssertion(assertion_id="assert-1", kind="fact_matches_regex", config={"fact_name": "email", "pattern": "("}),
        ],
    )

    issues = validate_fixture(_snapshot(), fixture)
    codes = {issue.code for issue in issues}
    severities = {issue.code: issue.severity for issue in issues}

    assert "fixture.turn_id_duplicate" in codes
    assert "fixture.dedupe_key_duplicate" in codes
    assert "fixture.assertion_id_duplicate" in codes
    assert "fixture.turn_text_empty" in codes
    assert "fixture.starting_step_missing" in codes
    assert "fixture.assertion_step_missing" in codes
    assert "fixture.assertion_regex_invalid" in codes
    assert severities["fixture.starting_step_missing"] == "blocker"
    assert severities["fixture.assertion_step_missing"] == "warning"


def _snapshot() -> AgentVersionSnapshot:
    now = datetime.now(timezone.utc)
    document = AgentDocument(
        start_scenario_id="main",
        fact_schema=[FactDef(name="email", type="string")],
        scenarios=[
            Scenario(
                id="main",
                name="Main",
                start_step_id="entry",
                steps=[
                    Step(
                        id="entry",
                        name="Entry",
                        tool_policy=[ToolBinding(ref="knowledge.lookup")],
                        transitions=[
                            StepTransition(
                                id="t1",
                                when=OtherwiseCondition(),
                                to_step_id="done",
                            )
                        ],
                    ),
                    Step(
                        id="done",
                        name="Done",
                        completion=StepCompletion(disposition="resolved"),
                    ),
                ],
            )
        ],
    )
    return AgentVersionSnapshot(
        agent_id="agent-1",
        name="Fixture Agent",
        version_id="version-1",
        version_number=1,
        status="draft",
        agent_document=document,
        created_at=now,
        updated_at=now,
        published_at=None,
        based_on_version_id=None,
        is_current_draft=True,
        is_current_published=False,
        organization_id="org-1",
    )
