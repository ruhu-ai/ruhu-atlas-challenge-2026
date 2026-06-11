from __future__ import annotations

from datetime import datetime, timezone

from ruhu.agent_document import (
    AgentDocument,
    Scenario,
    Step,
    StepCompletion,
    StepTransition,
)
from ruhu.kernel import ConversationKernel
from ruhu.registry import AgentVersionSnapshot
from ruhu.schemas import Condition, OtherwiseCondition
from ruhu.simulation_eval import (
    EvaluationRuntime,
    EvaluationService,
    InMemoryEvaluationRunStore,
    SimulationAssertion,
    SimulationFixture,
    SimulationTurnInput,
)


def _agent_document() -> AgentDocument:
    return AgentDocument(
        start_scenario_id="main",
        scenarios=[
            Scenario(
                id="main",
                name="Main",
                start_step_id="entry",
                steps=[
                    Step(
                        id="entry",
                        name="Entry",
                        transitions=[
                            StepTransition(
                                id="t0",
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


def _snapshot(document: AgentDocument, *, agent_id: str = "runtime_agent") -> AgentVersionSnapshot:
    now = datetime.now(timezone.utc)
    return AgentVersionSnapshot(
        agent_id=agent_id,
        name="Runtime Agent",
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


def test_evaluation_runtime_schedules_and_completes_background_run() -> None:
    snapshot = _snapshot(_agent_document())
    service = EvaluationService(ConversationKernel(), InMemoryEvaluationRunStore())
    runtime = EvaluationRuntime(service=service, max_workers=1)
    fixture = SimulationFixture(
        fixture_id="fixture-1",
        organization_id="org-1",
        agent_id="runtime_agent",
        name="runtime fixture",
        turns=[SimulationTurnInput(turn_id="turn-1", text="hello")],
        assertions=[
            SimulationAssertion(
                assertion_id="assertion-1",
                kind="final_step_equals",
                config={"step_id": "done"},
            )
        ],
    )

    queued = runtime.schedule_run(
        snapshot,
        [fixture],
        organization_id="org-1",
        gate_eligible=True,
        source="worker",
    )

    assert queued.status == "queued"

    completed = runtime.wait_for_run(queued.evaluation_run_id, timeout_seconds=2.0)
    status = runtime.status()
    runtime.shutdown()

    assert completed.status == "completed"
    assert completed.qualified_at is not None
    assert status.completed_runs == 1
    assert status.running_runs == 0
