from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from ruhu.agent_document import AgentDocument
from ruhu.agent_review import PublishQualificationSummary
from ruhu.kernel import ConversationKernel
from ruhu.registry import AgentVersionSnapshot
from ruhu.schemas import Channel, ConversationState, RuntimeTurn, RuntimeTurnResult
from ruhu.tools.types import ToolInvocation

from .assertions import AssertionEngine, validate_fixture
from .models import (
    EvaluationCaseResult,
    EvaluationCaseReview,
    EvaluationRun,
    FixtureValidationIssue,
    SimulationFixture,
    SimulationReplay,
    SimulationSource,
    SimulationTurnInput,
)
from .qualification import (
    build_publish_qualification_summary as build_qualification_summary,
    qualification_policy,
    run_qualifies,
    summarize_fixture_issues,
)
from .store import EvaluationRunStore


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _build_simulation_metadata(
    *,
    source: SimulationSource,
    starting_step_id: str | None,
    starting_scenario_id: str | None,
    seed_facts: dict[str, object],
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    merged_metadata = dict(metadata or {})
    simulation_metadata = dict(merged_metadata.get("simulation", {}))
    simulation_metadata.update(
        {
            "source": source,
            "starting_step_id": starting_step_id,
            "starting_scenario_id": starting_scenario_id,
            "seed_facts": dict(seed_facts),
        }
    )
    merged_metadata["simulation"] = simulation_metadata
    return merged_metadata


def _runtime_turn_from_input(
    turn: SimulationTurnInput,
    *,
    channel: Channel,
    default_id: str,
    received_at: datetime | None = None,
) -> RuntimeTurn:
    turn_id = turn.turn_id or default_id
    dedupe_key = turn.dedupe_key or turn_id
    return RuntimeTurn(
        turn_id=turn_id,
        dedupe_key=dedupe_key,
        channel=channel,
        modality=turn.modality,
        event_type=turn.event_type,
        text=turn.text,
        metadata=dict(turn.metadata),
        received_at=received_at or _utcnow(),
    )


def _snapshot_agent_document(snapshot: AgentVersionSnapshot) -> AgentDocument:
    if snapshot.agent_document is None:
        raise RuntimeError(f"missing agent document on version snapshot: {snapshot.version_id}")
    return snapshot.agent_document


class SimulationReplayService:
    def __init__(self, kernel: ConversationKernel) -> None:
        self._kernel = kernel

    def replay(
        self,
        snapshot: AgentVersionSnapshot,
        turns: list[SimulationTurnInput],
        *,
        conversation_id: str | None = None,
        channel: str = "web_chat",
        source: SimulationSource = "replay",
        organization_id: str | None = None,
        starting_step_id: str | None = None,
        starting_scenario_id: str | None = None,
        seed_facts: dict[str, object] | None = None,
    ) -> SimulationReplay:
        agent_document = _snapshot_agent_document(snapshot)
        conversation_key = conversation_id or str(uuid4())
        simulation_metadata = _build_simulation_metadata(
            source=source,
            starting_step_id=starting_step_id,
            starting_scenario_id=starting_scenario_id,
            seed_facts=seed_facts or {},
        )
        start = self._kernel.start_conversation(
            conversation_key,
            agent_document=agent_document,
            agent_id=snapshot.agent_id,
            agent_name=snapshot.name,
            agent_version_id=snapshot.version_id,
            mode="simulation",
            channel=channel,  # type: ignore[arg-type]
            organization_id=organization_id,
            starting_step_id=starting_step_id,
            starting_scenario_id=starting_scenario_id,
            seed_facts=seed_facts,
            metadata=simulation_metadata,
        )

        results: list[RuntimeTurnResult] = []
        for index, turn in enumerate(turns, start=1):
            runtime_turn = _runtime_turn_from_input(
                turn,
                channel=channel,  # type: ignore[arg-type]
                default_id=f"{conversation_key}:turn:{index}",
            )
            result = self._kernel.process_turn(
                conversation_key,
                runtime_turn,
                agent_document=agent_document,
                agent_id=snapshot.agent_id,
                agent_name=snapshot.name,
                organization_id=organization_id,
            )
            results.append(result)

        final_conversation = self._kernel.load_conversation(conversation_key)
        if final_conversation is None:
            raise RuntimeError(f"missing conversation after replay: {conversation_key}")
        traces = self._kernel.trace_store.by_conversation(conversation_key, organization_id=organization_id)
        tool_invocations = self._tool_invocations(conversation_key, organization_id=organization_id)
        return SimulationReplay(
            conversation=final_conversation,
            start=start,
            turns=results,
            traces=traces,
            tool_invocations=tool_invocations,
            final_step_id=final_conversation.step_id,
            final_facts=dict(final_conversation.facts),
            source=source,
            starting_step_id=starting_step_id,
            starting_scenario_id=starting_scenario_id,
            seed_facts=dict(seed_facts or {}),
        )

    def ensure_conversation(
        self,
        snapshot: AgentVersionSnapshot,
        *,
        conversation_id: str,
        organization_id: str | None,
        source: SimulationSource = "evaluation",
        starting_step_id: str | None = None,
        starting_scenario_id: str | None = None,
        seed_facts: dict[str, object] | None = None,
    ) -> ConversationState:
        agent_document = _snapshot_agent_document(snapshot)
        existing = self._kernel.load_conversation(conversation_id)
        if existing is not None:
            return existing
        return self._kernel.initialize_conversation(
            conversation_id,
            agent_document=agent_document,
            agent_id=snapshot.agent_id,
            agent_name=snapshot.name,
            agent_version_id=snapshot.version_id,
            mode="simulation",
            organization_id=organization_id,
            starting_step_id=starting_step_id,
            starting_scenario_id=starting_scenario_id,
            seed_facts=seed_facts,
            metadata=_build_simulation_metadata(
                source=source,
                starting_step_id=starting_step_id,
                starting_scenario_id=starting_scenario_id,
                seed_facts=seed_facts or {},
            ),
        )

    def _tool_invocations(self, conversation_id: str, *, organization_id: str | None) -> list[ToolInvocation]:
        if self._kernel.tool_runtime is None:
            return []
        return self._kernel.tool_runtime.store.by_conversation(
            conversation_id,
            organization_id=organization_id,
        )


class EvaluationService:
    def __init__(
        self,
        kernel: ConversationKernel,
        run_store: EvaluationRunStore,
        *,
        assertion_engine: AssertionEngine | None = None,
    ) -> None:
        self._kernel = kernel
        self._run_store = run_store
        self._assertion_engine = assertion_engine or AssertionEngine()
        self._replay = SimulationReplayService(kernel)

    def run(
        self,
        snapshot: AgentVersionSnapshot,
        fixtures: list[SimulationFixture],
        *,
        mode: str = "manual_batch",
        source: str = "worker",
        organization_id: str | None = None,
        gate_eligible: bool = False,
        triggered_by_user_id: str | None = None,
        minimum_pass_rate_ratio: float = 1.0,
        allow_warning_failures: bool = True,
    ) -> EvaluationRun:
        policy = qualification_policy(
            minimum_pass_rate_ratio=minimum_pass_rate_ratio,
            allow_warning_failures=allow_warning_failures,
        )
        run = self.create_run(
            snapshot,
            fixtures,
            mode=mode,
            source=source,
            organization_id=organization_id,
            gate_eligible=gate_eligible,
            triggered_by_user_id=triggered_by_user_id,
        )
        return self.execute_run(
            snapshot,
            fixtures,
            evaluation_run_id=run.evaluation_run_id,
            organization_id=organization_id,
            policy=policy,
        )

    def create_run(
        self,
        snapshot: AgentVersionSnapshot,
        fixtures: list[SimulationFixture],
        *,
        mode: str = "manual_batch",
        source: str = "worker",
        organization_id: str | None = None,
        gate_eligible: bool = False,
        triggered_by_user_id: str | None = None,
    ) -> EvaluationRun:
        run = EvaluationRun(
            organization_id=organization_id,
            agent_id=snapshot.agent_id,
            agent_version_id=snapshot.version_id,
            mode=mode,  # type: ignore[arg-type]
            source=source,  # type: ignore[arg-type]
            status="queued",
            gate_eligible=gate_eligible,
            fixture_count=len(fixtures),
            triggered_by_user_id=triggered_by_user_id,
        )
        self._run_store.save(run)
        return run

    def execute_run(
        self,
        snapshot: AgentVersionSnapshot,
        fixtures: list[SimulationFixture],
        *,
        evaluation_run_id: str,
        organization_id: str | None = None,
        policy,
    ) -> EvaluationRun:
        run = self._run_store.load(evaluation_run_id, organization_id=organization_id)
        if run is None:
            raise KeyError(f"unknown evaluation run: {evaluation_run_id}")
        if run.status == "cancelled":
            self._run_store.save(run)
            return run
        run.status = "running"
        run.started_at = run.started_at or _utcnow()
        self._run_store.save(run)

        for fixture in fixtures:
            run = self._run_store.load(run.evaluation_run_id, organization_id=organization_id) or run
            if run.status == "cancelled":
                break
            if run.status == "stopping":
                break

            case_started_at = _utcnow()
            issues = validate_fixture(snapshot, fixture)
            blocker_issues = [issue for issue in issues if issue.severity == "blocker"]
            conversation_id = _conversation_id(run, fixture)

            if blocker_issues:
                case_result = self._validation_error_case(
                    snapshot,
                    run,
                    fixture,
                    issues,
                    conversation_id=conversation_id,
                    organization_id=organization_id,
                    started_at=case_started_at,
                    completed_at=_utcnow(),
                )
            else:
                try:
                    replay = self._replay.replay(
                        snapshot,
                        fixture.turns,
                        conversation_id=conversation_id,
                        channel=fixture.default_channel,
                        source="evaluation",
                        organization_id=organization_id,
                        starting_step_id=fixture.starting_step_id,
                        starting_scenario_id=fixture.starting_scenario_id,
                        seed_facts=fixture.seed_facts,
                    )
                    case_result = self._evaluate_fixture(
                        run,
                        fixture,
                        replay,
                        started_at=case_started_at,
                        completed_at=_utcnow(),
                    )
                except Exception as exc:
                    case_result = self._runtime_error_case(
                        snapshot,
                        run,
                        fixture,
                        conversation_id=conversation_id,
                        error_message=str(exc),
                        organization_id=organization_id,
                        started_at=case_started_at,
                        completed_at=_utcnow(),
                    )

            run.results.append(case_result)
            self._refresh_run_counts(run)
            self._run_store.save(run)

        latest = self._run_store.load(run.evaluation_run_id, organization_id=organization_id)
        if latest is not None:
            run = latest
        self._finalize_run(run, policy=policy)
        self._run_store.save(run)
        return run

    def request_stop(self, evaluation_run_id: str, *, organization_id: str | None = None) -> EvaluationRun | None:
        return self._run_store.request_stop(evaluation_run_id, organization_id=organization_id)

    def load_run(self, evaluation_run_id: str, *, organization_id: str | None = None) -> EvaluationRun | None:
        return self._run_store.load(evaluation_run_id, organization_id=organization_id)

    def cancel(
        self,
        evaluation_run_id: str,
        *,
        organization_id: str | None = None,
        reason: str = "cancelled",
    ) -> EvaluationRun | None:
        return self._run_store.cancel(evaluation_run_id, organization_id=organization_id, reason=reason)

    def fail(
        self,
        evaluation_run_id: str,
        *,
        organization_id: str | None = None,
        reason: str,
    ) -> EvaluationRun | None:
        run = self._run_store.load(evaluation_run_id, organization_id=organization_id)
        if run is None:
            return None
        if run.status not in {"completed", "failed", "cancelled", "stopped"}:
            run.status = "failed"
            run.error_message = reason
            run.completed_at = _utcnow()
            run.duration_ms = _duration_ms(run.started_at, run.completed_at)
            self._refresh_run_counts(run)
            run.qualified_at = None
            self._run_store.save(run)
        return run

    def build_publish_qualification_summary(
        self,
        snapshot: AgentVersionSnapshot,
        fixtures: list[SimulationFixture],
        *,
        organization_id: str | None = None,
        minimum_pass_rate_ratio: float = 1.0,
        allow_warning_failures: bool = True,
        max_qualified_run_age_hours: int | None = None,
    ) -> PublishQualificationSummary:
        policy = qualification_policy(
            minimum_pass_rate_ratio=minimum_pass_rate_ratio,
            allow_warning_failures=allow_warning_failures,
            max_qualified_run_age_hours=max_qualified_run_age_hours,
        )
        all_runs = self._run_store.list_for_agent(
            snapshot.agent_id,
            organization_id=organization_id,
            agent_version_id=snapshot.version_id,
            gate_eligible=True,
        )
        latest_run = all_runs[0] if all_runs else None
        latest_qualified = self._run_store.latest_qualified(
            snapshot.agent_id,
            snapshot.version_id,
            organization_id=organization_id,
        )
        return build_qualification_summary(
            snapshot=snapshot,
            fixtures=fixtures,
            latest_run=latest_run,
            latest_qualified=latest_qualified,
            policy=policy,
        )

    def build_case_review(
        self,
        evaluation_run_id: str,
        case_result_id: str,
        *,
        organization_id: str | None = None,
    ) -> EvaluationCaseReview | None:
        run = self._run_store.load(evaluation_run_id, organization_id=organization_id)
        if run is None:
            return None
        case_result = next((item for item in run.results if item.case_result_id == case_result_id), None)
        if case_result is None:
            return None
        conversation = self._kernel.conversation_store.load(case_result.conversation_id)
        if conversation is None:
            return None
        if organization_id is not None and conversation.organization_id != organization_id:
            return None
        return EvaluationCaseReview(
            run=run,
            case_result=case_result,
            conversation=conversation,
            traces=self._kernel.trace_store.by_conversation(
                case_result.conversation_id,
                organization_id=organization_id,
            ),
            tool_invocations=self._replay._tool_invocations(
                case_result.conversation_id,
                organization_id=organization_id,
            ),
        )

    def _evaluate_fixture(
        self,
        run: EvaluationRun,
        fixture: SimulationFixture,
        replay: SimulationReplay,
        *,
        started_at: datetime,
        completed_at: datetime,
    ) -> EvaluationCaseResult:
        assertion_results = self._assertion_engine.evaluate(
            fixture.assertions,
            conversation=replay.conversation,
            traces=replay.traces,
            tool_invocations=replay.tool_invocations,
            turn_count=len(replay.turns),
        )
        blocker_failures = sum(1 for result in assertion_results if result.severity == "blocker" and not result.passed)
        warning_failures = sum(1 for result in assertion_results if result.severity == "warning" and not result.passed)
        failures = [result.message for result in assertion_results if not result.passed and result.message]
        return EvaluationCaseResult(
            evaluation_run_id=run.evaluation_run_id,
            fixture_id=fixture.fixture_id,
            fixture_name=fixture.name,
            conversation_id=replay.conversation.conversation_id,
            status="failed" if blocker_failures else "passed",
            final_step_id=replay.final_step_id,
            turn_count=len(replay.turns),
            assertions_passed=sum(1 for result in assertion_results if result.passed),
            assertions_failed=sum(1 for result in assertion_results if not result.passed),
            blocker_failures=blocker_failures,
            warning_failures=warning_failures,
            duration_ms=_duration_ms(started_at, completed_at),
            failure_summary="; ".join(failures) if failures else None,
            actual_facts=dict(replay.final_facts),
            assertion_results=assertion_results,
            started_at=started_at,
            completed_at=completed_at,
        )

    def _validation_error_case(
        self,
        snapshot: AgentVersionSnapshot,
        run: EvaluationRun,
        fixture: SimulationFixture,
        issues: list[FixtureValidationIssue],
        *,
        conversation_id: str,
        organization_id: str | None,
        started_at: datetime,
        completed_at: datetime,
    ) -> EvaluationCaseResult:
        conversation = self._replay.ensure_conversation(
            snapshot,
            conversation_id=conversation_id,
            organization_id=organization_id,
            source="evaluation",
            starting_step_id=None,
            starting_scenario_id=None,
            seed_facts=fixture.seed_facts,
        )
        return EvaluationCaseResult(
            evaluation_run_id=run.evaluation_run_id,
            fixture_id=fixture.fixture_id,
            fixture_name=fixture.name,
            conversation_id=conversation.conversation_id,
            status="error",
            final_step_id=conversation.step_id,
            turn_count=0,
            assertions_passed=0,
            assertions_failed=len(issues),
            blocker_failures=sum(1 for issue in issues if issue.severity == "blocker"),
            warning_failures=sum(1 for issue in issues if issue.severity == "warning"),
            duration_ms=_duration_ms(started_at, completed_at),
            failure_summary=summarize_fixture_issues(issues),
            actual_facts=dict(conversation.facts),
            assertion_results=[],
            started_at=started_at,
            completed_at=completed_at,
        )

    def _runtime_error_case(
        self,
        snapshot: AgentVersionSnapshot,
        run: EvaluationRun,
        fixture: SimulationFixture,
        *,
        conversation_id: str,
        error_message: str,
        organization_id: str | None,
        started_at: datetime,
        completed_at: datetime,
    ) -> EvaluationCaseResult:
        conversation = self._replay.ensure_conversation(
            snapshot,
            conversation_id=conversation_id,
            organization_id=organization_id,
            source="evaluation",
            starting_step_id=fixture.starting_step_id,
            starting_scenario_id=fixture.starting_scenario_id,
            seed_facts=fixture.seed_facts,
        )
        return EvaluationCaseResult(
            evaluation_run_id=run.evaluation_run_id,
            fixture_id=fixture.fixture_id,
            fixture_name=fixture.name,
            conversation_id=conversation.conversation_id,
            status="error",
            final_step_id=conversation.step_id,
            turn_count=0,
            assertions_passed=0,
            assertions_failed=1,
            blocker_failures=1,
            warning_failures=0,
            duration_ms=_duration_ms(started_at, completed_at),
            failure_summary=error_message,
            actual_facts=dict(conversation.facts),
            assertion_results=[],
            started_at=started_at,
            completed_at=completed_at,
        )

    def _finalize_run(self, run: EvaluationRun, *, policy) -> None:
        if run.status == "stopping":
            run.status = "stopped"
        elif run.status not in {"cancelled", "failed", "stopped"}:
            run.status = "completed"
        run.completed_at = run.completed_at or _utcnow()
        run.duration_ms = _duration_ms(run.started_at, run.completed_at)
        self._refresh_run_counts(run)
        if run.gate_eligible and run.status == "completed" and run_qualifies(run, policy=policy):
            run.qualified_at = run.completed_at
        else:
            run.qualified_at = None

    def _refresh_run_counts(self, run: EvaluationRun) -> None:
        run.passed_count = sum(1 for result in run.results if result.status == "passed")
        run.failed_count = sum(1 for result in run.results if result.status in {"failed", "error"})
        skipped_results = sum(1 for result in run.results if result.status == "skipped")
        remaining = max(run.fixture_count - len(run.results), 0)
        if run.status in {"stopped", "cancelled"}:
            run.skipped_count = skipped_results + remaining
        else:
            run.skipped_count = skipped_results
        run.pass_rate_ratio = (run.passed_count / run.fixture_count) if run.fixture_count else 1.0


def _duration_ms(started_at: datetime | None, completed_at: datetime | None) -> int | None:
    if started_at is None or completed_at is None:
        return None
    return int((completed_at - started_at).total_seconds() * 1000)


def _conversation_id(run: EvaluationRun, fixture: SimulationFixture) -> str:
    return f"{run.evaluation_run_id}:{fixture.fixture_id}"
