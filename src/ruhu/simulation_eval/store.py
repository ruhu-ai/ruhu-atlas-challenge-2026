from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from ruhu.db_models import (
    EvaluationAssertionResultRecord,
    EvaluationCaseResultRecord,
    EvaluationRunRecord,
    SimulationFixtureAssertionRecord,
    SimulationFixtureRecord,
    SimulationFixtureTurnRecord,
)

from .models import AssertionResult, EvaluationCaseResult, EvaluationRun, SimulationAssertion, SimulationFixture, SimulationTurnInput


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SimulationFixtureStore(Protocol):
    def load(self, fixture_id: str, *, organization_id: str | None = None) -> SimulationFixture | None: ...

    def save(self, fixture: SimulationFixture) -> None: ...

    def list_for_agent(
        self,
        agent_id: str,
        *,
        organization_id: str | None = None,
        is_active: bool | None = None,
        gate_required: bool | None = None,
        folder_path: str | None = None,
    ) -> list[SimulationFixture]: ...

    def deactivate(self, fixture_id: str, *, organization_id: str | None = None) -> bool: ...


class EvaluationRunStore(Protocol):
    def load(self, evaluation_run_id: str, *, organization_id: str | None = None) -> EvaluationRun | None: ...

    def save(self, run: EvaluationRun) -> None: ...

    def list_for_agent(
        self,
        agent_id: str,
        *,
        organization_id: str | None = None,
        agent_version_id: str | None = None,
        gate_eligible: bool | None = None,
    ) -> list[EvaluationRun]: ...

    def latest_qualified(
        self,
        agent_id: str,
        agent_version_id: str,
        *,
        organization_id: str | None = None,
    ) -> EvaluationRun | None: ...

    def request_stop(self, evaluation_run_id: str, *, organization_id: str | None = None) -> EvaluationRun | None: ...

    def cancel(
        self,
        evaluation_run_id: str,
        *,
        organization_id: str | None = None,
        reason: str = "cancelled",
    ) -> EvaluationRun | None: ...


class InMemorySimulationFixtureStore:
    def __init__(self) -> None:
        self._items: dict[str, SimulationFixture] = {}

    def load(self, fixture_id: str, *, organization_id: str | None = None) -> SimulationFixture | None:
        item = self._items.get(fixture_id)
        if item is None:
            return None
        if organization_id is not None and item.organization_id != organization_id:
            return None
        return item.model_copy(deep=True)

    def save(self, fixture: SimulationFixture) -> None:
        self._items[fixture.fixture_id] = fixture.model_copy(deep=True)

    def list_for_agent(
        self,
        agent_id: str,
        *,
        organization_id: str | None = None,
        is_active: bool | None = None,
        gate_required: bool | None = None,
    ) -> list[SimulationFixture]:
        items = [
            item
            for item in self._items.values()
            if item.agent_id == agent_id
            and (organization_id is None or item.organization_id == organization_id)
            and (is_active is None or item.is_active == is_active)
            and (gate_required is None or item.gate_required == gate_required)
        ]
        return [item.model_copy(deep=True) for item in sorted(items, key=lambda item: item.updated_at)]

    def deactivate(self, fixture_id: str, *, organization_id: str | None = None) -> bool:
        fixture = self._items.get(fixture_id)
        if fixture is None:
            return False
        if organization_id is not None and fixture.organization_id != organization_id:
            return False
        updated = fixture.model_copy(deep=True)
        updated.is_active = False
        self._items[fixture_id] = updated
        return True


class InMemoryEvaluationRunStore:
    def __init__(self) -> None:
        self._items: dict[str, EvaluationRun] = {}

    def load(self, evaluation_run_id: str, *, organization_id: str | None = None) -> EvaluationRun | None:
        item = self._items.get(evaluation_run_id)
        if item is None:
            return None
        if organization_id is not None and item.organization_id != organization_id:
            return None
        return item.model_copy(deep=True)

    def save(self, run: EvaluationRun) -> None:
        self._items[run.evaluation_run_id] = run.model_copy(deep=True)

    def list_for_agent(
        self,
        agent_id: str,
        *,
        organization_id: str | None = None,
        agent_version_id: str | None = None,
        gate_eligible: bool | None = None,
    ) -> list[EvaluationRun]:
        items = [
            item
            for item in self._items.values()
            if item.agent_id == agent_id
            and (organization_id is None or item.organization_id == organization_id)
            and (agent_version_id is None or item.agent_version_id == agent_version_id)
            and (gate_eligible is None or item.gate_eligible == gate_eligible)
        ]
        return [item.model_copy(deep=True) for item in sorted(items, key=_run_sort_key, reverse=True)]

    def latest_qualified(
        self,
        agent_id: str,
        agent_version_id: str,
        *,
        organization_id: str | None = None,
    ) -> EvaluationRun | None:
        candidates = [
            item
            for item in self._items.values()
            if item.agent_id == agent_id
            and item.agent_version_id == agent_version_id
            and item.qualified_at is not None
            and (organization_id is None or item.organization_id == organization_id)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.qualified_at or item.completed_at).model_copy(deep=True)

    def request_stop(self, evaluation_run_id: str, *, organization_id: str | None = None) -> EvaluationRun | None:
        run = self._items.get(evaluation_run_id)
        if run is None:
            return None
        if organization_id is not None and run.organization_id != organization_id:
            return None
        updated = run.model_copy(deep=True)
        if updated.status == "queued":
            updated.status = "cancelled"
            updated.completed_at = updated.completed_at or _utcnow()
            updated.duration_ms = _duration_ms(updated.started_at, updated.completed_at)
        elif updated.status == "running":
            updated.status = "stopping"
        self._items[evaluation_run_id] = updated
        return updated.model_copy(deep=True)

    def cancel(
        self,
        evaluation_run_id: str,
        *,
        organization_id: str | None = None,
        reason: str = "cancelled",
    ) -> EvaluationRun | None:
        run = self._items.get(evaluation_run_id)
        if run is None:
            return None
        if organization_id is not None and run.organization_id != organization_id:
            return None
        updated = run.model_copy(deep=True)
        if updated.status not in {"completed", "failed", "cancelled", "stopped"}:
            updated.status = "cancelled"
            updated.error_message = reason
            updated.completed_at = updated.completed_at or _utcnow()
            updated.duration_ms = _duration_ms(updated.started_at, updated.completed_at)
        self._items[evaluation_run_id] = updated
        return updated.model_copy(deep=True)


class SQLAlchemySimulationFixtureStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def load(self, fixture_id: str, *, organization_id: str | None = None) -> SimulationFixture | None:
        with self._session_factory() as session:
            record = session.get(SimulationFixtureRecord, fixture_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            return _record_to_fixture(session, record)

    def save(self, fixture: SimulationFixture) -> None:
        with self._session_factory() as session:
            record = session.get(SimulationFixtureRecord, fixture.fixture_id)
            if record is None:
                session.add(_fixture_to_record(fixture))
            else:
                _update_fixture_record(record, fixture)
            session.execute(
                delete(SimulationFixtureTurnRecord).where(
                    SimulationFixtureTurnRecord.fixture_id == fixture.fixture_id,
                )
            )
            session.execute(
                delete(SimulationFixtureAssertionRecord).where(
                    SimulationFixtureAssertionRecord.fixture_id == fixture.fixture_id,
                )
            )
            for index, turn in enumerate(fixture.turns):
                session.add(_fixture_turn_to_record(fixture, turn, order_index=index))
            for index, assertion in enumerate(fixture.assertions):
                session.add(_fixture_assertion_to_record(fixture, assertion, order_index=index))
            session.commit()

    def list_for_agent(
        self,
        agent_id: str,
        *,
        organization_id: str | None = None,
        is_active: bool | None = None,
        gate_required: bool | None = None,
        folder_path: str | None = None,
    ) -> list[SimulationFixture]:
        statement = (
            select(SimulationFixtureRecord)
            .where(SimulationFixtureRecord.agent_id == agent_id)
            .order_by(SimulationFixtureRecord.updated_at.asc())
        )
        if organization_id is not None:
            statement = statement.where(SimulationFixtureRecord.organization_id == organization_id)
        if is_active is not None:
            statement = statement.where(SimulationFixtureRecord.is_active == is_active)
        if gate_required is not None:
            statement = statement.where(SimulationFixtureRecord.gate_required == gate_required)
        if folder_path is not None:
            statement = statement.where(SimulationFixtureRecord.folder_path == folder_path)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
            return [_record_to_fixture(session, record) for record in records]

    def deactivate(self, fixture_id: str, *, organization_id: str | None = None) -> bool:
        with self._session_factory() as session:
            record = session.get(SimulationFixtureRecord, fixture_id)
            if record is None:
                return False
            if organization_id is not None and record.organization_id != organization_id:
                return False
            record.is_active = False
            session.commit()
            return True


class SQLAlchemyEvaluationRunStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def load(self, evaluation_run_id: str, *, organization_id: str | None = None) -> EvaluationRun | None:
        with self._session_factory() as session:
            record = session.get(EvaluationRunRecord, evaluation_run_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            return _record_to_run(session, record)

    def save(self, run: EvaluationRun) -> None:
        with self._session_factory() as session:
            record = session.get(EvaluationRunRecord, run.evaluation_run_id)
            if record is None:
                session.add(_run_to_record(run))
            else:
                _update_run_record(record, run)

            case_ids = session.execute(
                select(EvaluationCaseResultRecord.case_result_id).where(
                    EvaluationCaseResultRecord.evaluation_run_id == run.evaluation_run_id,
                )
            ).scalars().all()
            if case_ids:
                session.execute(
                    delete(EvaluationAssertionResultRecord).where(
                        EvaluationAssertionResultRecord.case_result_id.in_(case_ids),
                    )
                )
            session.execute(
                delete(EvaluationCaseResultRecord).where(
                    EvaluationCaseResultRecord.evaluation_run_id == run.evaluation_run_id,
                )
            )

            for case_result in run.results:
                session.add(_case_result_to_record(run, case_result))
                session.flush()
                for assertion_result in case_result.assertion_results:
                    session.add(_assertion_result_to_record(run, case_result, assertion_result))
            session.commit()

    def list_for_agent(
        self,
        agent_id: str,
        *,
        organization_id: str | None = None,
        agent_version_id: str | None = None,
        gate_eligible: bool | None = None,
    ) -> list[EvaluationRun]:
        statement = (
            select(EvaluationRunRecord)
            .where(EvaluationRunRecord.agent_id == agent_id)
            .order_by(EvaluationRunRecord.started_at.desc(), EvaluationRunRecord.completed_at.desc())
        )
        if organization_id is not None:
            statement = statement.where(EvaluationRunRecord.organization_id == organization_id)
        if agent_version_id is not None:
            statement = statement.where(EvaluationRunRecord.agent_version_id == agent_version_id)
        if gate_eligible is not None:
            statement = statement.where(EvaluationRunRecord.gate_eligible == gate_eligible)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
            return [_record_to_run(session, record) for record in records]

    def latest_qualified(
        self,
        agent_id: str,
        agent_version_id: str,
        *,
        organization_id: str | None = None,
    ) -> EvaluationRun | None:
        statement = (
            select(EvaluationRunRecord)
            .where(EvaluationRunRecord.agent_id == agent_id)
            .where(EvaluationRunRecord.agent_version_id == agent_version_id)
            .where(EvaluationRunRecord.qualified_at.is_not(None))
            .order_by(EvaluationRunRecord.qualified_at.desc(), EvaluationRunRecord.completed_at.desc())
        )
        if organization_id is not None:
            statement = statement.where(EvaluationRunRecord.organization_id == organization_id)
        with self._session_factory() as session:
            record = session.execute(statement).scalars().first()
            if record is None:
                return None
            return _record_to_run(session, record)

    def request_stop(self, evaluation_run_id: str, *, organization_id: str | None = None) -> EvaluationRun | None:
        with self._session_factory() as session:
            record = session.get(EvaluationRunRecord, evaluation_run_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            if record.status == "queued":
                record.status = "cancelled"
                record.completed_at = record.completed_at or _utcnow()
                record.duration_ms = _duration_ms(record.started_at, record.completed_at)
            elif record.status == "running":
                record.status = "stopping"
            session.commit()
            return _record_to_run(session, record)

    def cancel(
        self,
        evaluation_run_id: str,
        *,
        organization_id: str | None = None,
        reason: str = "cancelled",
    ) -> EvaluationRun | None:
        with self._session_factory() as session:
            record = session.get(EvaluationRunRecord, evaluation_run_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            if record.status not in {"completed", "failed", "cancelled", "stopped"}:
                record.status = "cancelled"
                record.error_message = reason
                record.completed_at = record.completed_at or _utcnow()
                record.duration_ms = _duration_ms(record.started_at, record.completed_at)
            session.commit()
            return _record_to_run(session, record)


def _run_sort_key(run: EvaluationRun) -> tuple[object, object]:
    return (run.started_at or run.completed_at or run.qualified_at or 0, run.evaluation_run_id)


def _fixture_to_record(fixture: SimulationFixture) -> SimulationFixtureRecord:
    return SimulationFixtureRecord(
        fixture_id=fixture.fixture_id,
        organization_id=fixture.organization_id,
        agent_id=fixture.agent_id,
        name=fixture.name,
        description=fixture.description,
        tags_json=list(fixture.tags),
        default_channel=fixture.default_channel,
        default_modality=fixture.default_modality,
        starting_step_id=fixture.starting_step_id,
        starting_scenario_id=fixture.starting_scenario_id,
        seed_facts_json=dict(fixture.seed_facts),
        is_active=fixture.is_active,
        gate_required=fixture.gate_required,
        created_by_user_id=fixture.created_by_user_id,
        created_at=fixture.created_at,
        updated_at=fixture.updated_at,
    )


def _update_fixture_record(record: SimulationFixtureRecord, fixture: SimulationFixture) -> None:
    record.organization_id = fixture.organization_id
    record.agent_id = fixture.agent_id
    record.name = fixture.name
    record.description = fixture.description
    record.tags_json = list(fixture.tags)
    record.default_channel = fixture.default_channel
    record.default_modality = fixture.default_modality
    record.starting_step_id = fixture.starting_step_id
    record.starting_scenario_id = fixture.starting_scenario_id
    record.seed_facts_json = dict(fixture.seed_facts)
    record.is_active = fixture.is_active
    record.gate_required = fixture.gate_required
    record.created_by_user_id = fixture.created_by_user_id
    record.created_at = fixture.created_at
    record.updated_at = fixture.updated_at


def _fixture_turn_to_record(
    fixture: SimulationFixture,
    turn: SimulationTurnInput,
    *,
    order_index: int,
) -> SimulationFixtureTurnRecord:
    return SimulationFixtureTurnRecord(
        fixture_turn_id=turn.turn_id or f"{fixture.fixture_id}:turn:{order_index}",
        organization_id=fixture.organization_id,
        fixture_id=fixture.fixture_id,
        order_index=order_index,
        event_type=turn.event_type,
        modality=turn.modality,
        text=turn.text,
        metadata_json=dict(turn.metadata),
    )


def _fixture_assertion_to_record(
    fixture: SimulationFixture,
    assertion: SimulationAssertion,
    *,
    order_index: int,
) -> SimulationFixtureAssertionRecord:
    return SimulationFixtureAssertionRecord(
        fixture_assertion_id=assertion.assertion_id,
        organization_id=fixture.organization_id,
        fixture_id=fixture.fixture_id,
        order_index=order_index,
        assertion_kind=assertion.kind,
        severity=assertion.severity,
        config_json=dict(assertion.config),
    )


def _record_to_fixture(session: Session, record: SimulationFixtureRecord) -> SimulationFixture:
    turns = session.execute(
        select(SimulationFixtureTurnRecord)
        .where(SimulationFixtureTurnRecord.fixture_id == record.fixture_id)
        .order_by(SimulationFixtureTurnRecord.order_index.asc())
    ).scalars().all()
    assertions = session.execute(
        select(SimulationFixtureAssertionRecord)
        .where(SimulationFixtureAssertionRecord.fixture_id == record.fixture_id)
        .order_by(SimulationFixtureAssertionRecord.order_index.asc())
    ).scalars().all()
    return SimulationFixture(
        fixture_id=record.fixture_id,
        organization_id=record.organization_id,
        agent_id=record.agent_id,
        name=record.name,
        description=record.description,
        tags=list(record.tags_json or []),
        default_channel=record.default_channel,
        default_modality=record.default_modality,
        starting_step_id=record.starting_step_id,
        starting_scenario_id=record.starting_scenario_id,
        seed_facts=dict(record.seed_facts_json or {}),
        turns=[
            SimulationTurnInput(
                turn_id=item.fixture_turn_id,
                dedupe_key=item.fixture_turn_id,
                event_type=item.event_type,
                modality=item.modality,
                text=item.text,
                metadata=dict(item.metadata_json or {}),
            )
            for item in turns
        ],
        assertions=[
            SimulationAssertion(
                assertion_id=item.fixture_assertion_id,
                kind=item.assertion_kind,
                severity=item.severity,
                config=dict(item.config_json or {}),
            )
            for item in assertions
        ],
        is_active=record.is_active,
        gate_required=record.gate_required,
        created_by_user_id=record.created_by_user_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _run_to_record(run: EvaluationRun) -> EvaluationRunRecord:
    return EvaluationRunRecord(
        evaluation_run_id=run.evaluation_run_id,
        organization_id=run.organization_id,
        agent_id=run.agent_id,
        agent_version_id=run.agent_version_id,
        mode=run.mode,
        source=run.source,
        status=run.status,
        gate_eligible=run.gate_eligible,
        fixture_count=run.fixture_count,
        passed_count=run.passed_count,
        failed_count=run.failed_count,
        skipped_count=run.skipped_count,
        pass_rate_ratio=run.pass_rate_ratio,
        triggered_by_user_id=run.triggered_by_user_id,
        started_at=run.started_at,
        completed_at=run.completed_at,
        duration_ms=run.duration_ms,
        error_message=run.error_message,
        qualified_at=run.qualified_at,
        summary_json={},
    )


def _update_run_record(record: EvaluationRunRecord, run: EvaluationRun) -> None:
    record.organization_id = run.organization_id
    record.agent_id = run.agent_id
    record.agent_version_id = run.agent_version_id
    record.mode = run.mode
    record.source = run.source
    record.status = run.status
    record.gate_eligible = run.gate_eligible
    record.fixture_count = run.fixture_count
    record.passed_count = run.passed_count
    record.failed_count = run.failed_count
    record.skipped_count = run.skipped_count
    record.pass_rate_ratio = run.pass_rate_ratio
    record.triggered_by_user_id = run.triggered_by_user_id
    record.started_at = run.started_at
    record.completed_at = run.completed_at
    record.duration_ms = run.duration_ms
    record.error_message = run.error_message
    record.qualified_at = run.qualified_at
    record.summary_json = {}


def _case_result_to_record(run: EvaluationRun, result: EvaluationCaseResult) -> EvaluationCaseResultRecord:
    return EvaluationCaseResultRecord(
        case_result_id=result.case_result_id,
        organization_id=run.organization_id,
        evaluation_run_id=run.evaluation_run_id,
        fixture_id=result.fixture_id,
        fixture_name=result.fixture_name,
        conversation_id=result.conversation_id,
        status=result.status,
        final_state=result.final_step_id,
        turn_count=result.turn_count,
        assertions_passed=result.assertions_passed,
        assertions_failed=result.assertions_failed,
        blocker_failures=result.blocker_failures,
        warning_failures=result.warning_failures,
        duration_ms=result.duration_ms,
        failure_summary=result.failure_summary,
        actual_facts_json=dict(result.actual_facts),
        started_at=result.started_at,
        completed_at=result.completed_at,
    )


def _assertion_result_to_record(
    run: EvaluationRun,
    case_result: EvaluationCaseResult,
    result: AssertionResult,
) -> EvaluationAssertionResultRecord:
    return EvaluationAssertionResultRecord(
        assertion_result_id=result.assertion_result_id,
        organization_id=run.organization_id,
        case_result_id=case_result.case_result_id,
        fixture_assertion_id=result.fixture_assertion_id,
        assertion_kind=result.kind,
        severity=result.severity,
        passed=result.passed,
        expected_json=dict(result.expected),
        actual_json=dict(result.actual),
        message=result.message,
        created_at=result.created_at,
    )


def _record_to_run(session: Session, record: EvaluationRunRecord) -> EvaluationRun:
    case_records = session.execute(
        select(EvaluationCaseResultRecord)
        .where(EvaluationCaseResultRecord.evaluation_run_id == record.evaluation_run_id)
        .order_by(EvaluationCaseResultRecord.started_at.asc(), EvaluationCaseResultRecord.case_result_id.asc())
    ).scalars().all()
    results = [_record_to_case_result(session, case_record) for case_record in case_records]
    return EvaluationRun(
        evaluation_run_id=record.evaluation_run_id,
        organization_id=record.organization_id,
        agent_id=record.agent_id,
        agent_version_id=record.agent_version_id,
        mode=record.mode,
        source=record.source,
        status=record.status,
        gate_eligible=record.gate_eligible,
        fixture_count=record.fixture_count,
        passed_count=record.passed_count,
        failed_count=record.failed_count,
        skipped_count=record.skipped_count,
        pass_rate_ratio=record.pass_rate_ratio,
        triggered_by_user_id=record.triggered_by_user_id,
        started_at=record.started_at,
        completed_at=record.completed_at,
        duration_ms=record.duration_ms,
        error_message=record.error_message,
        qualified_at=record.qualified_at,
        results=results,
    )


def _record_to_case_result(session: Session, record: EvaluationCaseResultRecord) -> EvaluationCaseResult:
    assertion_records = session.execute(
        select(EvaluationAssertionResultRecord)
        .where(EvaluationAssertionResultRecord.case_result_id == record.case_result_id)
        .order_by(EvaluationAssertionResultRecord.created_at.asc(), EvaluationAssertionResultRecord.assertion_result_id.asc())
    ).scalars().all()
    return EvaluationCaseResult(
        case_result_id=record.case_result_id,
        evaluation_run_id=record.evaluation_run_id,
        fixture_id=record.fixture_id,
        fixture_name=record.fixture_name,
        conversation_id=record.conversation_id,
        status=record.status,
        final_step_id=record.final_state,
        turn_count=record.turn_count,
        assertions_passed=record.assertions_passed,
        assertions_failed=record.assertions_failed,
        blocker_failures=record.blocker_failures,
        warning_failures=record.warning_failures,
        duration_ms=record.duration_ms,
        failure_summary=record.failure_summary,
        actual_facts=dict(record.actual_facts_json or {}),
        assertion_results=[
            AssertionResult(
                assertion_result_id=item.assertion_result_id,
                fixture_assertion_id=item.fixture_assertion_id,
                kind=item.assertion_kind,
                severity=item.severity,
                passed=item.passed,
                expected=dict(item.expected_json or {}),
                actual=dict(item.actual_json or {}),
                message=item.message,
                created_at=item.created_at,
            )
            for item in assertion_records
        ],
        started_at=record.started_at,
        completed_at=record.completed_at,
    )


def _duration_ms(started_at: datetime | None, completed_at: datetime | None) -> int | None:
    if started_at is None or completed_at is None:
        return None
    return int((completed_at - started_at).total_seconds() * 1000)
