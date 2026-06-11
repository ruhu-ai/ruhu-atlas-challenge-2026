from __future__ import annotations

from datetime import datetime, timezone

from ruhu.db import build_session_factory
from tests._fixtures.templates import load_template_agent_document
from ruhu.registry import SQLAlchemyAgentRegistry
from ruhu.schemas import ConversationState
from ruhu.simulation_eval import (
    AssertionResult,
    EvaluationCaseResult,
    EvaluationRun,
    InMemoryEvaluationRunStore,
    SQLAlchemyEvaluationRunStore,
    SQLAlchemySimulationFixtureStore,
    SimulationAssertion,
    SimulationFixture,
    SimulationTurnInput,
)
from ruhu.stores import SQLAlchemyConversationStore


def test_in_memory_evaluation_run_store_request_stop_and_cancel() -> None:
    now = datetime.now(timezone.utc)
    store = InMemoryEvaluationRunStore()
    run = EvaluationRun(
        evaluation_run_id="run-1",
        organization_id="org-1",
        agent_id="agent-1",
        agent_version_id="version-1",
        mode="manual_batch",
        source="worker",
        status="running",
        fixture_count=2,
        started_at=now,
    )
    store.save(run)

    stopping = store.request_stop("run-1", organization_id="org-1")
    assert stopping is not None
    assert stopping.status == "stopping"

    cancelled = store.cancel("run-1", organization_id="org-1", reason="operator_cancelled")
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.error_message == "operator_cancelled"


def test_sqlalchemy_simulation_fixture_store_roundtrip(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    registry = SQLAlchemyAgentRegistry(session_factory)
    snapshot = registry.create_agent_document(
        agent_id="sales",
        agent_name="Sales Agent",
        document=load_template_agent_document("sales-agent.json"),
        organization_id="org-1",
    )
    store = SQLAlchemySimulationFixtureStore(session_factory)
    fixture = SimulationFixture(
        organization_id="org-1",
        agent_id=snapshot.agent_id,
        name="Product question",
        description="Ask a basic product question.",
        tags=["smoke"],
        turns=[
            SimulationTurnInput(
                turn_id="turn-1",
                event_type="user_message",
                text="What does the product do?",
            )
        ],
        assertions=[
            SimulationAssertion(
                assertion_id="assert-1",
                kind="message_contains",
                config={"text": "Ruhu"},
            )
        ],
    )

    store.save(fixture)

    loaded = store.load(fixture.fixture_id, organization_id="org-1")
    assert loaded is not None
    assert loaded.name == "Product question"
    assert loaded.turns[0].text == "What does the product do?"
    assert loaded.assertions[0].kind == "message_contains"
    assert store.list_for_agent(snapshot.agent_id, organization_id="org-1")[0].fixture_id == fixture.fixture_id
    assert store.load(fixture.fixture_id, organization_id="org-2") is None


def test_sqlalchemy_evaluation_run_store_roundtrip_and_stop_cancel(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())
    registry = SQLAlchemyAgentRegistry(session_factory)
    snapshot = registry.create_agent_document(
        agent_id="sales",
        agent_name="Sales Agent",
        document=load_template_agent_document("sales-agent.json"),
        organization_id="org-1",
    )
    fixture_store = SQLAlchemySimulationFixtureStore(session_factory)
    fixture = SimulationFixture(
        fixture_id="fixture-1",
        organization_id="org-1",
        agent_id=snapshot.agent_id,
        name="Fixture One",
        turns=[SimulationTurnInput(turn_id="turn-1", text="pricing?")],
        assertions=[SimulationAssertion(assertion_id="assert-1", kind="fact_present", config={"fact_name": "email"})],
    )
    fixture_store.save(fixture)

    conversation_store = SQLAlchemyConversationStore(session_factory)
    now = datetime.now(timezone.utc)
    conversation_store.save(
        ConversationState(
            conversation_id="conv-1",
            organization_id="org-1",
            agent_id=snapshot.agent_id,
            agent_version_id=snapshot.version_id,
            step_id="discover",
            facts={"email": "user@example.com"},
            updated_at=now,
        )
    )

    store = SQLAlchemyEvaluationRunStore(session_factory)
    run = EvaluationRun(
        evaluation_run_id="run-1",
        organization_id="org-1",
        agent_id=snapshot.agent_id,
        agent_version_id=snapshot.version_id,
        mode="manual_batch",
        source="worker",
        status="completed",
        gate_eligible=True,
        fixture_count=1,
        passed_count=1,
        failed_count=0,
        pass_rate_ratio=1.0,
        started_at=now,
        completed_at=now,
        qualified_at=now,
        results=[
            EvaluationCaseResult(
                case_result_id="case-1",
                evaluation_run_id="run-1",
                fixture_id=fixture.fixture_id,
                fixture_name=fixture.name,
                conversation_id="conv-1",
                status="passed",
                final_step_id="discover",
                turn_count=1,
                assertions_passed=1,
                assertion_results=[
                    AssertionResult(
                        assertion_result_id="result-1",
                        fixture_assertion_id="assert-1",
                        kind="fact_present",
                        severity="blocker",
                        passed=True,
                        expected={"fact_name": "email"},
                        actual={"fact_name": "email", "present": True},
                    )
                ],
                started_at=now,
                completed_at=now,
            )
        ],
    )

    store.save(run)

    loaded = store.load("run-1", organization_id="org-1")
    assert loaded is not None
    assert loaded.results[0].fixture_id == fixture.fixture_id
    assert loaded.results[0].assertion_results[0].kind == "fact_present"
    assert store.latest_qualified(snapshot.agent_id, snapshot.version_id, organization_id="org-1") is not None
    assert store.load("run-1", organization_id="org-2") is None

    running = loaded.model_copy(update={"status": "running", "completed_at": None, "qualified_at": None})
    store.save(running)
    stopping = store.request_stop("run-1", organization_id="org-1")
    assert stopping is not None
    assert stopping.status == "stopping"

    cancelled = store.cancel("run-1", organization_id="org-1", reason="requested")
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.error_message == "requested"
