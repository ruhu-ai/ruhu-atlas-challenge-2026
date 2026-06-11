from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ruhu.agent_document import (
    AgentDocument,
    Scenario,
    Step,
    StepCompletion,
    StepTransition,
)
from ruhu.agent_review import apply_publish_qualification, build_publish_readiness
from ruhu.kernel import ConversationKernel
from ruhu.registry import AgentVersionSnapshot
from ruhu.schemas import AgentDefinitionValidationReport, Condition, FactPresentCondition, OtherwiseCondition
from ruhu.simulation_eval import (
    EvaluationService,
    InMemoryEvaluationRunStore,
    SimulationAssertion,
    SimulationFixture,
    SimulationTurnInput,
)


def _snapshot(document: AgentDocument, *, agent_id: str = "email_agent") -> AgentVersionSnapshot:
    now = datetime.now(timezone.utc)
    return AgentVersionSnapshot(
        agent_id=agent_id,
        name="Email Agent",
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


def _email_agent_document() -> AgentDocument:
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


def test_evaluation_service_runs_fixture_and_marks_run_qualified() -> None:
    document = _email_agent_document()
    snapshot = _snapshot(document)
    service = EvaluationService(ConversationKernel(), InMemoryEvaluationRunStore())
    fixture = _email_fixture("fixture-1", "collect email")

    run = service.run(
        snapshot,
        [fixture],
        gate_eligible=True,
        organization_id="org-1",
        source="worker",
    )

    assert run.status == "completed"
    assert run.pass_rate_ratio == 1.0
    assert run.qualified_at is not None
    assert run.results[0].status == "passed"
    assert run.results[0].final_step_id == "done"


def test_evaluation_service_marks_invalid_fixture_case_error_and_does_not_qualify() -> None:
    document = _email_agent_document()
    snapshot = _snapshot(document)
    service = EvaluationService(ConversationKernel(), InMemoryEvaluationRunStore())
    fixture = _email_fixture("fixture-invalid", "invalid fixture")
    fixture.starting_step_id = "missing_step"

    run = service.run(snapshot, [fixture], gate_eligible=True, organization_id="org-1")

    assert run.status == "completed"
    assert run.pass_rate_ratio == 0.0
    assert run.qualified_at is None
    assert run.failed_count == 1
    assert run.results[0].status == "error"
    assert "missing starting step" in (run.results[0].failure_summary or "")


def test_evaluation_service_honors_stop_request_and_marks_remaining_cases_skipped() -> None:
    document = _email_agent_document()
    snapshot = _snapshot(document)
    store = _AutoStopRunStore()
    service = EvaluationService(ConversationKernel(), store)
    fixtures = [
        _email_fixture("fixture-1", "fixture one"),
        _email_fixture("fixture-2", "fixture two"),
    ]

    run = service.run(snapshot, fixtures, organization_id="org-1")

    assert run.status == "stopped"
    assert len(run.results) == 1
    assert run.skipped_count == 1
    assert run.qualified_at is None


def test_publish_qualification_summary_surfaces_fixture_warnings_and_merges_into_readiness() -> None:
    document = _email_agent_document()
    snapshot = _snapshot(document)
    store = InMemoryEvaluationRunStore()
    service = EvaluationService(ConversationKernel(), store)

    valid_fixture = _email_fixture("fixture-valid", "valid fixture")
    stale_fixture = SimulationFixture(
        fixture_id="fixture-stale",
        organization_id="org-1",
        agent_id=snapshot.agent_id,
        name="stale fixture",
        gate_required=False,
        turns=[SimulationTurnInput(turn_id="turn-2", text="hello")],
        assertions=[SimulationAssertion(assertion_id="a2", kind="final_step_equals", config={"step_id": "missing_step"})],
    )

    service.run(snapshot, [valid_fixture], gate_eligible=True, organization_id="org-1")
    qualification = service.build_publish_qualification_summary(
        snapshot,
        [valid_fixture, stale_fixture],
        organization_id="org-1",
    )

    assert qualification.latest_qualified_run_id is not None
    assert not qualification.evaluation_blockers
    assert qualification.fixture_reference_warnings

    readiness = build_publish_readiness(
        draft_snapshot=snapshot,
        validation=AgentDefinitionValidationReport(
            agent_id=snapshot.agent_id,
            agent_name=snapshot.name,
            valid=True,
            error_count=0,
            warning_count=0,
        ),
        published_snapshot=None,
        available_tool_refs=[],
    )
    enriched = apply_publish_qualification(readiness, qualification)

    assert enriched.can_publish is True
    assert enriched.qualification.latest_qualified_run_id == qualification.latest_qualified_run_id
    assert any(item.code == "fixture.assertion_step_missing" for item in enriched.warnings)


def test_publish_qualification_summary_blocks_on_required_coverage_and_stale_run() -> None:
    document = _email_agent_document()
    snapshot = _snapshot(document)
    store = InMemoryEvaluationRunStore()
    service = EvaluationService(ConversationKernel(), store)
    first = _email_fixture("fixture-1", "first")
    second = _email_fixture("fixture-2", "second")

    run = service.run(snapshot, [first], gate_eligible=True, organization_id="org-1")
    run.qualified_at = datetime.now(timezone.utc) - timedelta(hours=3)
    store.save(run)

    qualification = service.build_publish_qualification_summary(
        snapshot,
        [first, second],
        organization_id="org-1",
        max_qualified_run_age_hours=1,
    )
    codes = {item.code for item in qualification.evaluation_blockers}

    assert "evaluation.required_fixture_coverage_missing" in codes
    assert "evaluation.qualified_run_stale" in codes


def test_publish_qualification_summary_respects_warning_failure_policy() -> None:
    document = _email_agent_document()
    snapshot = _snapshot(document)
    store = InMemoryEvaluationRunStore()
    service = EvaluationService(ConversationKernel(), store)
    fixture = _email_fixture("fixture-warning", "warning fixture")
    fixture.assertions.append(
        SimulationAssertion(
            assertion_id="warn-1",
            kind="message_contains",
            severity="warning",
            config={"text": "missing message"},
        )
    )

    run = service.run(snapshot, [fixture], gate_eligible=True, organization_id="org-1")
    assert run.qualified_at is not None

    qualification = service.build_publish_qualification_summary(
        snapshot,
        [fixture],
        organization_id="org-1",
        allow_warning_failures=False,
    )

    assert any(item.code == "evaluation.warning_failures_present" for item in qualification.evaluation_blockers)


def test_evaluation_service_builds_case_review_from_runtime_evidence() -> None:
    document = _email_agent_document()
    snapshot = _snapshot(document)
    service = EvaluationService(ConversationKernel(), InMemoryEvaluationRunStore())
    fixture = _email_fixture("fixture-review", "review fixture")

    run = service.run(snapshot, [fixture], organization_id="org-1", gate_eligible=True)
    case_result = run.results[0]

    review = service.build_case_review(
        run.evaluation_run_id,
        case_result.case_result_id,
        organization_id="org-1",
    )

    assert review is not None
    assert review.case_result.case_result_id == case_result.case_result_id
    assert review.conversation.metadata["simulation"]["source"] == "evaluation"
    assert review.traces


def _email_fixture(fixture_id: str, name: str) -> SimulationFixture:
    return SimulationFixture(
        fixture_id=fixture_id,
        organization_id="org-1",
        agent_id="email_agent",
        name=name,
        default_channel="web_chat",
        turns=[SimulationTurnInput(turn_id="turn-1", text="jane@example.com")],
        assertions=[
            SimulationAssertion(assertion_id="a1", kind="final_step_equals", config={"step_id": "done"}),
            SimulationAssertion(assertion_id="a2", kind="fact_equals", config={"fact_name": "email", "value": "jane@example.com"}),
        ],
    )


class _AutoStopRunStore(InMemoryEvaluationRunStore):
    def __init__(self) -> None:
        super().__init__()
        self._triggered = False

    def save(self, run) -> None:  # type: ignore[override]
        super().save(run)
        if not self._triggered and run.status == "running" and len(run.results) == 1:
            self._triggered = True
            self._items[run.evaluation_run_id].status = "stopping"
