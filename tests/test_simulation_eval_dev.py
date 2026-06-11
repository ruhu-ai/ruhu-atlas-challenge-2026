from __future__ import annotations

import json
from pathlib import Path

from ruhu.agent_document import (
    AgentDocument,
    Scenario,
    Step,
    StepCompletion,
    StepTransition,
)
from ruhu.schemas import Condition, FactPresentCondition, OtherwiseCondition
from ruhu.simulation_eval import SimulationAssertion, SimulationFixture, SimulationTurnInput
from ruhu.simulation_eval.dev import (
    build_local_snapshot,
    export_fixture_file,
    import_fixture_file,
    load_fixture_file,
    run_local_evaluation,
)


def test_load_fixture_file_supports_single_and_bundle(tmp_path: Path) -> None:
    fixture = _fixture("fixture-1")
    single_path = tmp_path / "fixture.json"
    bundle_path = tmp_path / "fixtures.json"
    single_path.write_text(fixture.model_dump_json(indent=2), encoding="utf-8")
    bundle_path.write_text(
        '{"schema_version":"simulation_fixture_bundle.v1","fixtures":[' + fixture.model_dump_json() + ']}',
        encoding="utf-8",
    )

    single_loaded = load_fixture_file(single_path)
    bundle_loaded = load_fixture_file(bundle_path)

    assert [item.fixture_id for item in single_loaded] == ["fixture-1"]
    assert [item.fixture_id for item in bundle_loaded] == ["fixture-1"]


def test_import_fixture_file_merges_into_bundle(tmp_path: Path) -> None:
    first = _fixture("fixture-1")
    second = _fixture("fixture-2")
    input_path = tmp_path / "fixture.json"
    output_path = tmp_path / "fixtures.json"
    input_path.write_text(second.model_dump_json(indent=2), encoding="utf-8")
    output_path.write_text(
        '{"schema_version":"simulation_fixture_bundle.v1","fixtures":[' + first.model_dump_json() + ']}',
        encoding="utf-8",
    )

    fixtures = import_fixture_file(input_path, output_path, append=True)
    reloaded = load_fixture_file(output_path)

    assert [item.fixture_id for item in fixtures] == ["fixture-1", "fixture-2"]
    assert [item.fixture_id for item in reloaded] == ["fixture-1", "fixture-2"]


def test_export_fixture_file_can_export_single_fixture(tmp_path: Path) -> None:
    first = _fixture("fixture-1")
    second = _fixture("fixture-2")
    bundle_path = tmp_path / "fixtures.json"
    output_path = tmp_path / "fixture-2.json"
    bundle_path.write_text(
        '{"schema_version":"simulation_fixture_bundle.v1","fixtures":['
        + first.model_dump_json()
        + ","
        + second.model_dump_json()
        + "]}",
        encoding="utf-8",
    )

    payload = export_fixture_file(bundle_path, output_path=output_path, fixture_ids=["fixture-2"], single=True)
    loaded = load_fixture_file(output_path)

    assert '"fixture_id": "fixture-2"' in payload
    assert [item.fixture_id for item in loaded] == ["fixture-2"]


def test_run_local_evaluation_executes_fixture_bundle(tmp_path: Path) -> None:
    agent_path = tmp_path / "agent.json"
    fixtures_path = tmp_path / "fixtures.json"
    agent_path.write_text(json.dumps(_agent_payload()), encoding="utf-8")
    fixtures_path.write_text(
        '{"schema_version":"simulation_fixture_bundle.v1","fixtures":[' + _fixture("fixture-1").model_dump_json() + ']}',
        encoding="utf-8",
    )

    snapshot = build_local_snapshot(agent_path, organization_id="org-1")
    run = run_local_evaluation(
        agent_path,
        fixtures_path,
        organization_id="org-1",
        gate_eligible=True,
    )

    assert snapshot.agent_id == "email_agent"
    assert run.status == "completed"
    assert run.pass_rate_ratio == 1.0
    assert run.results[0].status == "passed"


def _agent_document() -> AgentDocument:
    return AgentDocument(
        start_scenario_id="main",
        scenarios=[
            Scenario(
                id="main",
                name="Email",
                start_step_id="entry",
                steps=[
                    Step(
                        id="entry",
                        name="Entry",
                        transitions=[
                            StepTransition(
                                id="t0",
                                when=OtherwiseCondition(),
                                to_step_id="collect_email",
                            )
                        ],
                    ),
                    Step(
                        id="collect_email",
                        name="Collect Email",
                        fact_requirements=[{"name": "email"}],
                        transitions=[
                            StepTransition(
                                id="t1",
                                when=FactPresentCondition(fact_name="email"),
                                to_step_id="done",
                            ),
                            StepTransition(
                                id="t2",
                                when=OtherwiseCondition(),
                                to_step_id="collect_email",
                            ),
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


def _agent_payload() -> dict:
    return {
        "agent_id": "email_agent",
        "name": "Email Agent",
        "agent_document": _agent_document().model_dump(),
    }


def _fixture(fixture_id: str) -> SimulationFixture:
    return SimulationFixture(
        fixture_id=fixture_id,
        organization_id="org-1",
        agent_id="email_agent",
        name=f"Fixture {fixture_id}",
        turns=[SimulationTurnInput(turn_id="turn-1", text="jane@example.com")],
        assertions=[
            SimulationAssertion(assertion_id=f"{fixture_id}-a1", kind="final_step_equals", config={"step_id": "done"}),
            SimulationAssertion(
                assertion_id=f"{fixture_id}-a2",
                kind="fact_equals",
                config={"fact_name": "email", "value": "jane@example.com"},
            ),
        ],
    )
