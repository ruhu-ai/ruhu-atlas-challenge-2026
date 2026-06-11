from __future__ import annotations

from ruhu.simulation_eval import SimulationAssertion, SimulationFixture, SimulationTurnInput, export_fixture, export_fixtures, import_fixture, import_fixtures


def test_fixture_import_export_roundtrip() -> None:
    fixture = SimulationFixture(
        fixture_id="fixture-1",
        agent_id="agent-1",
        name="fixture one",
        turns=[SimulationTurnInput(turn_id="turn-1", text="hello")],
        assertions=[SimulationAssertion(assertion_id="assert-1", kind="message_contains", config={"text": "hello"})],
    )

    encoded = export_fixture(fixture)
    decoded = import_fixture(encoded)

    assert decoded.fixture_id == fixture.fixture_id
    assert decoded.turns[0].text == "hello"
    assert decoded.assertions[0].kind == "message_contains"


def test_fixture_bundle_import_export_roundtrip() -> None:
    fixtures = [
        SimulationFixture(
            fixture_id="fixture-1",
            agent_id="agent-1",
            name="fixture one",
            turns=[SimulationTurnInput(turn_id="turn-1", text="hello")],
        ),
        SimulationFixture(
            fixture_id="fixture-2",
            agent_id="agent-1",
            name="fixture two",
            turns=[SimulationTurnInput(turn_id="turn-2", text="pricing")],
        ),
    ]

    encoded = export_fixtures(fixtures)
    decoded = import_fixtures(encoded)

    assert [fixture.fixture_id for fixture in decoded] == ["fixture-1", "fixture-2"]
