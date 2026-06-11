from __future__ import annotations

from copy import deepcopy
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..db_models import Base
from .models import (
    BaselineSnapshot,
    ExecutionIntent,
    ExecutionResult,
    Goal,
    GoalEvaluation,
    ImpactAssessment,
    InsightItem,
    KPIExperiment,
    MetricObservation,
    MetricScope,
    RecommendationCandidate,
)
from .sqlalchemy_models import (
    KPIBaselineSnapshotRecord,
    KPIExecutionIntentRecord,
    KPIExecutionResultRecord,
    KPIExperimentRecord,
    KPIGoalEvaluationRecord,
    KPIGoalRecord,
    KPIImpactAssessmentRecord,
    KPIInsightRecord,
    KPIMetricObservationRecord,
    KPIMetricScopeRecord,
    KPIRecommendationRecord,
)


class KPIStore(Protocol):
    def save_scope(self, scope: MetricScope) -> MetricScope: ...

    def get_scope(self, scope_id: str) -> MetricScope | None: ...

    def get_scope_by_fingerprint(self, organization_id: str, fingerprint: str) -> MetricScope | None: ...

    def list_scopes(self, organization_id: str, *, scope_kind: str | None = None) -> list[MetricScope]: ...

    def save_observation(self, observation: MetricObservation) -> MetricObservation: ...

    def get_observation(self, observation_id: str) -> MetricObservation | None: ...

    def list_observations(
        self,
        organization_id: str,
        *,
        metric_key: str | None = None,
        scope_id: str | None = None,
        limit: int = 100,
    ) -> list[MetricObservation]: ...

    def get_latest_observation(self, organization_id: str, metric_key: str, scope_id: str) -> MetricObservation | None: ...

    def save_baseline_snapshot(self, snapshot: BaselineSnapshot) -> BaselineSnapshot: ...

    def get_baseline_snapshot(self, baseline_snapshot_id: str) -> BaselineSnapshot | None: ...

    def list_baseline_snapshots(self, organization_id: str, *, goal_id: str | None = None) -> list[BaselineSnapshot]: ...

    def save_goal(self, goal: Goal) -> Goal: ...

    def get_goal(self, goal_id: str) -> Goal | None: ...

    def list_goals(
        self,
        organization_id: str,
        *,
        scope_id: str | None = None,
        metric_key: str | None = None,
        status: str | None = None,
    ) -> list[Goal]: ...

    def save_goal_evaluation(self, evaluation: GoalEvaluation) -> GoalEvaluation: ...

    def list_goal_evaluations(self, goal_id: str, *, limit: int = 100) -> list[GoalEvaluation]: ...

    def get_latest_goal_evaluation(self, goal_id: str) -> GoalEvaluation | None: ...

    def save_insight(self, insight: InsightItem) -> InsightItem: ...

    def get_insight(self, insight_id: str) -> InsightItem | None: ...

    def list_insights(
        self,
        organization_id: str,
        *,
        goal_id: str | None = None,
        scope_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[InsightItem]: ...

    def save_recommendation(self, recommendation: RecommendationCandidate) -> RecommendationCandidate: ...

    def get_recommendation(self, recommendation_id: str) -> RecommendationCandidate | None: ...

    def list_recommendations(
        self,
        organization_id: str,
        *,
        goal_id: str | None = None,
        scope_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[RecommendationCandidate]: ...

    def save_impact_assessment(self, assessment: ImpactAssessment) -> ImpactAssessment: ...

    def list_impact_assessments(
        self,
        organization_id: str,
        *,
        goal_id: str | None = None,
        recommendation_id: str | None = None,
        limit: int = 100,
    ) -> list[ImpactAssessment]: ...

    def save_execution_intent(self, intent: ExecutionIntent) -> ExecutionIntent: ...

    def get_execution_intent(self, execution_intent_id: str) -> ExecutionIntent | None: ...

    def list_execution_intents(
        self,
        organization_id: str,
        *,
        goal_id: str | None = None,
        recommendation_id: str | None = None,
        execution_mode: str | None = None,
        limit: int = 100,
    ) -> list[ExecutionIntent]: ...

    def save_execution_result(self, result: ExecutionResult) -> ExecutionResult: ...

    def list_execution_results(
        self,
        organization_id: str,
        *,
        execution_intent_id: str | None = None,
        limit: int = 100,
    ) -> list[ExecutionResult]: ...

    def get_latest_execution_result(self, execution_intent_id: str) -> ExecutionResult | None: ...

    def save_experiment(self, experiment: KPIExperiment) -> KPIExperiment: ...

    def get_experiment(self, experiment_id: str) -> KPIExperiment | None: ...

    def list_experiments(
        self,
        organization_id: str,
        *,
        goal_id: str | None = None,
        recommendation_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[KPIExperiment]: ...


class InMemoryKPIStore:
    def __init__(self) -> None:
        self._scopes: dict[str, MetricScope] = {}
        self._scope_ids_by_fingerprint: dict[tuple[str, str], str] = {}
        self._observations: dict[str, MetricObservation] = {}
        self._baseline_snapshots: dict[str, BaselineSnapshot] = {}
        self._goals: dict[str, Goal] = {}
        self._goal_evaluations: dict[str, GoalEvaluation] = {}
        self._insights: dict[str, InsightItem] = {}
        self._recommendations: dict[str, RecommendationCandidate] = {}
        self._execution_intents: dict[str, ExecutionIntent] = {}
        self._execution_results: dict[str, ExecutionResult] = {}
        self._impact_assessments: dict[str, ImpactAssessment] = {}
        self._experiments: dict[str, KPIExperiment] = {}

    def save_scope(self, scope: MetricScope) -> MetricScope:
        existing_id = self._scope_ids_by_fingerprint.get((scope.organization_id, scope.fingerprint))
        stored = scope.model_copy(deep=True)
        if existing_id and existing_id != stored.scope_id:
            stored.scope_id = existing_id
        self._scopes[stored.scope_id] = stored
        self._scope_ids_by_fingerprint[(stored.organization_id, stored.fingerprint)] = stored.scope_id
        return stored.model_copy(deep=True)

    def get_scope(self, scope_id: str) -> MetricScope | None:
        item = self._scopes.get(scope_id)
        return None if item is None else item.model_copy(deep=True)

    def get_scope_by_fingerprint(self, organization_id: str, fingerprint: str) -> MetricScope | None:
        scope_id = self._scope_ids_by_fingerprint.get((organization_id, fingerprint))
        return None if scope_id is None else self.get_scope(scope_id)

    def list_scopes(self, organization_id: str, *, scope_kind: str | None = None) -> list[MetricScope]:
        items = [item for item in self._scopes.values() if item.organization_id == organization_id]
        if scope_kind is not None:
            items = [item for item in items if item.scope_kind == scope_kind]
        items.sort(key=lambda item: (item.created_at, item.scope_id))
        return [item.model_copy(deep=True) for item in items]

    def save_observation(self, observation: MetricObservation) -> MetricObservation:
        stored = observation.model_copy(deep=True)
        self._observations[stored.observation_id] = stored
        return stored.model_copy(deep=True)

    def get_observation(self, observation_id: str) -> MetricObservation | None:
        item = self._observations.get(observation_id)
        return None if item is None else item.model_copy(deep=True)

    def list_observations(
        self,
        organization_id: str,
        *,
        metric_key: str | None = None,
        scope_id: str | None = None,
        limit: int = 100,
    ) -> list[MetricObservation]:
        items = [item for item in self._observations.values() if item.organization_id == organization_id]
        if metric_key is not None:
            items = [item for item in items if item.metric_key == metric_key]
        if scope_id is not None:
            items = [item for item in items if item.scope_id == scope_id]
        items.sort(key=lambda item: (item.period_end, item.observation_id), reverse=True)
        return [item.model_copy(deep=True) for item in items[:limit]]

    def get_latest_observation(self, organization_id: str, metric_key: str, scope_id: str) -> MetricObservation | None:
        items = self.list_observations(organization_id, metric_key=metric_key, scope_id=scope_id, limit=1)
        return None if not items else items[0]

    def save_baseline_snapshot(self, snapshot: BaselineSnapshot) -> BaselineSnapshot:
        stored = snapshot.model_copy(deep=True)
        self._baseline_snapshots[stored.baseline_snapshot_id] = stored
        return stored.model_copy(deep=True)

    def get_baseline_snapshot(self, baseline_snapshot_id: str) -> BaselineSnapshot | None:
        item = self._baseline_snapshots.get(baseline_snapshot_id)
        return None if item is None else item.model_copy(deep=True)

    def list_baseline_snapshots(self, organization_id: str, *, goal_id: str | None = None) -> list[BaselineSnapshot]:
        items = [item for item in self._baseline_snapshots.values() if item.organization_id == organization_id]
        if goal_id is not None:
            items = [item for item in items if item.goal_id == goal_id]
        items.sort(key=lambda item: (item.created_at, item.baseline_snapshot_id), reverse=True)
        return [item.model_copy(deep=True) for item in items]

    def save_goal(self, goal: Goal) -> Goal:
        stored = goal.model_copy(deep=True)
        self._goals[stored.goal_id] = stored
        return stored.model_copy(deep=True)

    def get_goal(self, goal_id: str) -> Goal | None:
        item = self._goals.get(goal_id)
        return None if item is None else item.model_copy(deep=True)

    def list_goals(
        self,
        organization_id: str,
        *,
        scope_id: str | None = None,
        metric_key: str | None = None,
        status: str | None = None,
    ) -> list[Goal]:
        items = [item for item in self._goals.values() if item.organization_id == organization_id]
        if scope_id is not None:
            items = [item for item in items if item.scope_id == scope_id]
        if metric_key is not None:
            items = [item for item in items if item.metric_key == metric_key]
        if status is not None:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: (item.created_at, item.goal_id), reverse=True)
        return [item.model_copy(deep=True) for item in items]

    def save_goal_evaluation(self, evaluation: GoalEvaluation) -> GoalEvaluation:
        stored = evaluation.model_copy(deep=True)
        self._goal_evaluations[stored.evaluation_id] = stored
        return stored.model_copy(deep=True)

    def list_goal_evaluations(self, goal_id: str, *, limit: int = 100) -> list[GoalEvaluation]:
        items = [item for item in self._goal_evaluations.values() if item.goal_id == goal_id]
        items.sort(key=lambda item: (item.created_at, item.evaluation_id), reverse=True)
        return [item.model_copy(deep=True) for item in items[:limit]]

    def get_latest_goal_evaluation(self, goal_id: str) -> GoalEvaluation | None:
        items = self.list_goal_evaluations(goal_id, limit=1)
        return None if not items else items[0]

    def save_insight(self, insight: InsightItem) -> InsightItem:
        stored = insight.model_copy(deep=True)
        self._insights[stored.insight_id] = stored
        return stored.model_copy(deep=True)

    def get_insight(self, insight_id: str) -> InsightItem | None:
        item = self._insights.get(insight_id)
        return None if item is None else item.model_copy(deep=True)

    def list_insights(
        self,
        organization_id: str,
        *,
        goal_id: str | None = None,
        scope_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[InsightItem]:
        items = [item for item in self._insights.values() if item.organization_id == organization_id]
        if goal_id is not None:
            items = [item for item in items if item.goal_id == goal_id]
        if scope_id is not None:
            items = [item for item in items if item.scope_id == scope_id]
        if status is not None:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: (item.rank_score, item.updated_at, item.insight_id), reverse=True)
        return [item.model_copy(deep=True) for item in items[:limit]]

    def save_recommendation(self, recommendation: RecommendationCandidate) -> RecommendationCandidate:
        stored = recommendation.model_copy(deep=True)
        self._recommendations[stored.recommendation_id] = stored
        return stored.model_copy(deep=True)

    def get_recommendation(self, recommendation_id: str) -> RecommendationCandidate | None:
        item = self._recommendations.get(recommendation_id)
        return None if item is None else item.model_copy(deep=True)

    def list_recommendations(
        self,
        organization_id: str,
        *,
        goal_id: str | None = None,
        scope_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[RecommendationCandidate]:
        items = [item for item in self._recommendations.values() if item.organization_id == organization_id]
        if goal_id is not None:
            items = [item for item in items if item.goal_id == goal_id]
        if scope_id is not None:
            items = [item for item in items if item.scope_id == scope_id]
        if status is not None:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: (item.updated_at, item.recommendation_id), reverse=True)
        return [item.model_copy(deep=True) for item in items[:limit]]

    def save_impact_assessment(self, assessment: ImpactAssessment) -> ImpactAssessment:
        stored = assessment.model_copy(deep=True)
        self._impact_assessments[stored.assessment_id] = stored
        return stored.model_copy(deep=True)

    def list_impact_assessments(
        self,
        organization_id: str,
        *,
        goal_id: str | None = None,
        recommendation_id: str | None = None,
        limit: int = 100,
    ) -> list[ImpactAssessment]:
        items = [item for item in self._impact_assessments.values() if item.organization_id == organization_id]
        if goal_id is not None:
            items = [item for item in items if item.goal_id == goal_id]
        if recommendation_id is not None:
            items = [item for item in items if item.recommendation_id == recommendation_id]
        items.sort(key=lambda item: (item.created_at, item.assessment_id), reverse=True)
        return [item.model_copy(deep=True) for item in items[:limit]]

    def save_execution_intent(self, intent: ExecutionIntent) -> ExecutionIntent:
        stored = intent.model_copy(deep=True)
        self._execution_intents[stored.execution_intent_id] = stored
        return stored.model_copy(deep=True)

    def get_execution_intent(self, execution_intent_id: str) -> ExecutionIntent | None:
        item = self._execution_intents.get(execution_intent_id)
        return None if item is None else item.model_copy(deep=True)

    def list_execution_intents(
        self,
        organization_id: str,
        *,
        goal_id: str | None = None,
        recommendation_id: str | None = None,
        execution_mode: str | None = None,
        limit: int = 100,
    ) -> list[ExecutionIntent]:
        items = [item for item in self._execution_intents.values() if item.organization_id == organization_id]
        if goal_id is not None:
            items = [item for item in items if item.goal_id == goal_id]
        if recommendation_id is not None:
            items = [item for item in items if item.recommendation_id == recommendation_id]
        if execution_mode is not None:
            items = [item for item in items if item.execution_mode == execution_mode]
        items.sort(key=lambda item: (item.created_at, item.execution_intent_id), reverse=True)
        return [item.model_copy(deep=True) for item in items[:limit]]

    def save_execution_result(self, result: ExecutionResult) -> ExecutionResult:
        stored = result.model_copy(deep=True)
        self._execution_results[stored.execution_result_id] = stored
        return stored.model_copy(deep=True)

    def list_execution_results(
        self,
        organization_id: str,
        *,
        execution_intent_id: str | None = None,
        limit: int = 100,
    ) -> list[ExecutionResult]:
        items = [item for item in self._execution_results.values() if item.organization_id == organization_id]
        if execution_intent_id is not None:
            items = [item for item in items if item.execution_intent_id == execution_intent_id]
        items.sort(key=lambda item: (item.created_at, item.execution_result_id), reverse=True)
        return [item.model_copy(deep=True) for item in items[:limit]]

    def get_latest_execution_result(self, execution_intent_id: str) -> ExecutionResult | None:
        items = [item.model_copy(deep=True) for item in self._execution_results.values() if item.execution_intent_id == execution_intent_id]
        items.sort(key=lambda item: (item.created_at, item.execution_result_id), reverse=True)
        return None if not items else items[0]

    def save_experiment(self, experiment: KPIExperiment) -> KPIExperiment:
        stored = experiment.model_copy(deep=True)
        self._experiments[stored.experiment_id] = stored
        return stored.model_copy(deep=True)

    def get_experiment(self, experiment_id: str) -> KPIExperiment | None:
        item = self._experiments.get(experiment_id)
        return None if item is None else item.model_copy(deep=True)

    def list_experiments(
        self,
        organization_id: str,
        *,
        goal_id: str | None = None,
        recommendation_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[KPIExperiment]:
        items = [item for item in self._experiments.values() if item.organization_id == organization_id]
        if goal_id is not None:
            items = [item for item in items if item.goal_id == goal_id]
        if recommendation_id is not None:
            items = [item for item in items if item.recommendation_id == recommendation_id]
        if status is not None:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: (item.updated_at, item.experiment_id), reverse=True)
        return [item.model_copy(deep=True) for item in items[:limit]]


class SQLAlchemyKPIStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        bind = self._session_factory.kw.get("bind")
        if bind is None:
            with self._session_factory() as session:
                bind = session.get_bind()
        Base.metadata.create_all(
            bind=bind,
            tables=[
                KPIMetricScopeRecord.__table__,
                KPIMetricObservationRecord.__table__,
                KPIBaselineSnapshotRecord.__table__,
                KPIGoalRecord.__table__,
                KPIGoalEvaluationRecord.__table__,
                KPIInsightRecord.__table__,
                KPIRecommendationRecord.__table__,
                KPIExecutionIntentRecord.__table__,
                KPIExecutionResultRecord.__table__,
                KPIImpactAssessmentRecord.__table__,
                KPIExperimentRecord.__table__,
            ],
        )

    def save_scope(self, scope: MetricScope) -> MetricScope:
        with self._session_factory() as session:
            record = session.get(KPIMetricScopeRecord, scope.scope_id)
            if record is None:
                record = session.execute(
                    select(KPIMetricScopeRecord).where(
                        KPIMetricScopeRecord.organization_id == scope.organization_id,
                        KPIMetricScopeRecord.fingerprint == scope.fingerprint,
                    )
                ).scalar_one_or_none()
            if record is None:
                record = KPIMetricScopeRecord(scope_id=scope.scope_id)
                session.add(record)
            _apply_scope(record, scope)
            session.commit()
            return _record_to_scope(record)

    def get_scope(self, scope_id: str) -> MetricScope | None:
        with self._session_factory() as session:
            record = session.get(KPIMetricScopeRecord, scope_id)
            return None if record is None else _record_to_scope(record)

    def get_scope_by_fingerprint(self, organization_id: str, fingerprint: str) -> MetricScope | None:
        statement = select(KPIMetricScopeRecord).where(
            KPIMetricScopeRecord.organization_id == organization_id,
            KPIMetricScopeRecord.fingerprint == fingerprint,
        )
        with self._session_factory() as session:
            record = session.execute(statement).scalar_one_or_none()
            return None if record is None else _record_to_scope(record)

    def list_scopes(self, organization_id: str, *, scope_kind: str | None = None) -> list[MetricScope]:
        statement = select(KPIMetricScopeRecord).where(KPIMetricScopeRecord.organization_id == organization_id)
        if scope_kind is not None:
            statement = statement.where(KPIMetricScopeRecord.scope_kind == scope_kind)
        statement = statement.order_by(KPIMetricScopeRecord.created_at.asc(), KPIMetricScopeRecord.scope_id.asc())
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_scope(record) for record in records]

    def save_observation(self, observation: MetricObservation) -> MetricObservation:
        with self._session_factory() as session:
            record = session.get(KPIMetricObservationRecord, observation.observation_id)
            if record is None:
                record = KPIMetricObservationRecord(observation_id=observation.observation_id)
                session.add(record)
            _apply_observation(record, observation)
            session.commit()
            return _record_to_observation(record)

    def get_observation(self, observation_id: str) -> MetricObservation | None:
        with self._session_factory() as session:
            record = session.get(KPIMetricObservationRecord, observation_id)
            return None if record is None else _record_to_observation(record)

    def list_observations(
        self,
        organization_id: str,
        *,
        metric_key: str | None = None,
        scope_id: str | None = None,
        limit: int = 100,
    ) -> list[MetricObservation]:
        statement = select(KPIMetricObservationRecord).where(KPIMetricObservationRecord.organization_id == organization_id)
        if metric_key is not None:
            statement = statement.where(KPIMetricObservationRecord.metric_key == metric_key)
        if scope_id is not None:
            statement = statement.where(KPIMetricObservationRecord.scope_id == scope_id)
        statement = statement.order_by(KPIMetricObservationRecord.period_end.desc(), KPIMetricObservationRecord.observation_id.desc()).limit(limit)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_observation(record) for record in records]

    def get_latest_observation(self, organization_id: str, metric_key: str, scope_id: str) -> MetricObservation | None:
        items = self.list_observations(organization_id, metric_key=metric_key, scope_id=scope_id, limit=1)
        return None if not items else items[0]

    def save_baseline_snapshot(self, snapshot: BaselineSnapshot) -> BaselineSnapshot:
        with self._session_factory() as session:
            record = session.get(KPIBaselineSnapshotRecord, snapshot.baseline_snapshot_id)
            if record is None:
                record = KPIBaselineSnapshotRecord(baseline_snapshot_id=snapshot.baseline_snapshot_id)
                session.add(record)
            _apply_baseline_snapshot(record, snapshot)
            session.commit()
            return _record_to_baseline_snapshot(record)

    def get_baseline_snapshot(self, baseline_snapshot_id: str) -> BaselineSnapshot | None:
        with self._session_factory() as session:
            record = session.get(KPIBaselineSnapshotRecord, baseline_snapshot_id)
            return None if record is None else _record_to_baseline_snapshot(record)

    def list_baseline_snapshots(self, organization_id: str, *, goal_id: str | None = None) -> list[BaselineSnapshot]:
        statement = select(KPIBaselineSnapshotRecord).where(KPIBaselineSnapshotRecord.organization_id == organization_id)
        if goal_id is not None:
            statement = statement.where(KPIBaselineSnapshotRecord.goal_id == goal_id)
        statement = statement.order_by(KPIBaselineSnapshotRecord.created_at.desc(), KPIBaselineSnapshotRecord.baseline_snapshot_id.desc())
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_baseline_snapshot(record) for record in records]

    def save_goal(self, goal: Goal) -> Goal:
        with self._session_factory() as session:
            record = session.get(KPIGoalRecord, goal.goal_id)
            if record is None:
                record = KPIGoalRecord(goal_id=goal.goal_id)
                session.add(record)
            _apply_goal(record, goal)
            session.commit()
            return _record_to_goal(record)

    def get_goal(self, goal_id: str) -> Goal | None:
        with self._session_factory() as session:
            record = session.get(KPIGoalRecord, goal_id)
            return None if record is None else _record_to_goal(record)

    def list_goals(
        self,
        organization_id: str,
        *,
        scope_id: str | None = None,
        metric_key: str | None = None,
        status: str | None = None,
    ) -> list[Goal]:
        statement = select(KPIGoalRecord).where(KPIGoalRecord.organization_id == organization_id)
        if scope_id is not None:
            statement = statement.where(KPIGoalRecord.scope_id == scope_id)
        if metric_key is not None:
            statement = statement.where(KPIGoalRecord.metric_key == metric_key)
        if status is not None:
            statement = statement.where(KPIGoalRecord.status == status)
        statement = statement.order_by(KPIGoalRecord.created_at.desc(), KPIGoalRecord.goal_id.desc())
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_goal(record) for record in records]

    def save_goal_evaluation(self, evaluation: GoalEvaluation) -> GoalEvaluation:
        with self._session_factory() as session:
            record = session.get(KPIGoalEvaluationRecord, evaluation.evaluation_id)
            if record is None:
                record = KPIGoalEvaluationRecord(evaluation_id=evaluation.evaluation_id)
                session.add(record)
            _apply_goal_evaluation(record, evaluation)
            session.commit()
            return _record_to_goal_evaluation(record)

    def list_goal_evaluations(self, goal_id: str, *, limit: int = 100) -> list[GoalEvaluation]:
        statement = (
            select(KPIGoalEvaluationRecord)
            .where(KPIGoalEvaluationRecord.goal_id == goal_id)
            .order_by(KPIGoalEvaluationRecord.created_at.desc(), KPIGoalEvaluationRecord.evaluation_id.desc())
            .limit(limit)
        )
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_goal_evaluation(record) for record in records]

    def get_latest_goal_evaluation(self, goal_id: str) -> GoalEvaluation | None:
        items = self.list_goal_evaluations(goal_id, limit=1)
        return None if not items else items[0]

    def save_insight(self, insight: InsightItem) -> InsightItem:
        with self._session_factory() as session:
            record = session.get(KPIInsightRecord, insight.insight_id)
            if record is None:
                record = KPIInsightRecord(insight_id=insight.insight_id)
                session.add(record)
            _apply_insight(record, insight)
            session.commit()
            return _record_to_insight(record)

    def get_insight(self, insight_id: str) -> InsightItem | None:
        with self._session_factory() as session:
            record = session.get(KPIInsightRecord, insight_id)
            return None if record is None else _record_to_insight(record)

    def list_insights(
        self,
        organization_id: str,
        *,
        goal_id: str | None = None,
        scope_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[InsightItem]:
        statement = select(KPIInsightRecord).where(KPIInsightRecord.organization_id == organization_id)
        if goal_id is not None:
            statement = statement.where(KPIInsightRecord.goal_id == goal_id)
        if scope_id is not None:
            statement = statement.where(KPIInsightRecord.scope_id == scope_id)
        if status is not None:
            statement = statement.where(KPIInsightRecord.status == status)
        statement = statement.order_by(KPIInsightRecord.rank_score.desc(), KPIInsightRecord.updated_at.desc()).limit(limit)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_insight(record) for record in records]

    def save_recommendation(self, recommendation: RecommendationCandidate) -> RecommendationCandidate:
        with self._session_factory() as session:
            record = session.get(KPIRecommendationRecord, recommendation.recommendation_id)
            if record is None:
                record = KPIRecommendationRecord(recommendation_id=recommendation.recommendation_id)
                session.add(record)
            _apply_recommendation(record, recommendation)
            session.commit()
            return _record_to_recommendation(record)

    def get_recommendation(self, recommendation_id: str) -> RecommendationCandidate | None:
        with self._session_factory() as session:
            record = session.get(KPIRecommendationRecord, recommendation_id)
            return None if record is None else _record_to_recommendation(record)

    def list_recommendations(
        self,
        organization_id: str,
        *,
        goal_id: str | None = None,
        scope_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[RecommendationCandidate]:
        statement = select(KPIRecommendationRecord).where(KPIRecommendationRecord.organization_id == organization_id)
        if goal_id is not None:
            statement = statement.where(KPIRecommendationRecord.goal_id == goal_id)
        if scope_id is not None:
            statement = statement.where(KPIRecommendationRecord.scope_id == scope_id)
        if status is not None:
            statement = statement.where(KPIRecommendationRecord.status == status)
        statement = statement.order_by(KPIRecommendationRecord.updated_at.desc(), KPIRecommendationRecord.recommendation_id.desc()).limit(limit)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_recommendation(record) for record in records]

    def save_impact_assessment(self, assessment: ImpactAssessment) -> ImpactAssessment:
        with self._session_factory() as session:
            record = session.get(KPIImpactAssessmentRecord, assessment.assessment_id)
            if record is None:
                record = KPIImpactAssessmentRecord(assessment_id=assessment.assessment_id)
                session.add(record)
            _apply_impact_assessment(record, assessment)
            session.commit()
            return _record_to_impact_assessment(record)

    def list_impact_assessments(
        self,
        organization_id: str,
        *,
        goal_id: str | None = None,
        recommendation_id: str | None = None,
        limit: int = 100,
    ) -> list[ImpactAssessment]:
        statement = select(KPIImpactAssessmentRecord).where(KPIImpactAssessmentRecord.organization_id == organization_id)
        if goal_id is not None:
            statement = statement.where(KPIImpactAssessmentRecord.goal_id == goal_id)
        if recommendation_id is not None:
            statement = statement.where(KPIImpactAssessmentRecord.recommendation_id == recommendation_id)
        statement = statement.order_by(KPIImpactAssessmentRecord.created_at.desc(), KPIImpactAssessmentRecord.assessment_id.desc()).limit(limit)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_impact_assessment(record) for record in records]

    def save_execution_intent(self, intent: ExecutionIntent) -> ExecutionIntent:
        with self._session_factory() as session:
            record = session.get(KPIExecutionIntentRecord, intent.execution_intent_id)
            if record is None:
                record = KPIExecutionIntentRecord(execution_intent_id=intent.execution_intent_id)
                session.add(record)
            _apply_execution_intent(record, intent)
            session.commit()
            return _record_to_execution_intent(record)

    def get_execution_intent(self, execution_intent_id: str) -> ExecutionIntent | None:
        with self._session_factory() as session:
            record = session.get(KPIExecutionIntentRecord, execution_intent_id)
            return None if record is None else _record_to_execution_intent(record)

    def list_execution_intents(
        self,
        organization_id: str,
        *,
        goal_id: str | None = None,
        recommendation_id: str | None = None,
        execution_mode: str | None = None,
        limit: int = 100,
    ) -> list[ExecutionIntent]:
        statement = select(KPIExecutionIntentRecord).where(KPIExecutionIntentRecord.organization_id == organization_id)
        if goal_id is not None:
            statement = statement.where(KPIExecutionIntentRecord.goal_id == goal_id)
        if recommendation_id is not None:
            statement = statement.where(KPIExecutionIntentRecord.recommendation_id == recommendation_id)
        if execution_mode is not None:
            statement = statement.where(KPIExecutionIntentRecord.execution_mode == execution_mode)
        statement = statement.order_by(KPIExecutionIntentRecord.created_at.desc(), KPIExecutionIntentRecord.execution_intent_id.desc()).limit(limit)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_execution_intent(record) for record in records]

    def save_execution_result(self, result: ExecutionResult) -> ExecutionResult:
        with self._session_factory() as session:
            record = session.get(KPIExecutionResultRecord, result.execution_result_id)
            if record is None:
                record = KPIExecutionResultRecord(execution_result_id=result.execution_result_id)
                session.add(record)
            _apply_execution_result(record, result)
            session.commit()
            return _record_to_execution_result(record)

    def list_execution_results(
        self,
        organization_id: str,
        *,
        execution_intent_id: str | None = None,
        limit: int = 100,
    ) -> list[ExecutionResult]:
        statement = select(KPIExecutionResultRecord).where(KPIExecutionResultRecord.organization_id == organization_id)
        if execution_intent_id is not None:
            statement = statement.where(KPIExecutionResultRecord.execution_intent_id == execution_intent_id)
        statement = statement.order_by(KPIExecutionResultRecord.created_at.desc(), KPIExecutionResultRecord.execution_result_id.desc()).limit(limit)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_execution_result(record) for record in records]

    def get_latest_execution_result(self, execution_intent_id: str) -> ExecutionResult | None:
        statement = (
            select(KPIExecutionResultRecord)
            .where(KPIExecutionResultRecord.execution_intent_id == execution_intent_id)
            .order_by(KPIExecutionResultRecord.created_at.desc(), KPIExecutionResultRecord.execution_result_id.desc())
            .limit(1)
        )
        with self._session_factory() as session:
            record = session.execute(statement).scalar_one_or_none()
        return None if record is None else _record_to_execution_result(record)

    def save_experiment(self, experiment: KPIExperiment) -> KPIExperiment:
        with self._session_factory() as session:
            record = session.get(KPIExperimentRecord, experiment.experiment_id)
            if record is None:
                record = KPIExperimentRecord(experiment_id=experiment.experiment_id)
                session.add(record)
            _apply_experiment(record, experiment)
            session.commit()
            return _record_to_experiment(record)

    def get_experiment(self, experiment_id: str) -> KPIExperiment | None:
        with self._session_factory() as session:
            record = session.get(KPIExperimentRecord, experiment_id)
            return None if record is None else _record_to_experiment(record)

    def list_experiments(
        self,
        organization_id: str,
        *,
        goal_id: str | None = None,
        recommendation_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[KPIExperiment]:
        statement = select(KPIExperimentRecord).where(KPIExperimentRecord.organization_id == organization_id)
        if goal_id is not None:
            statement = statement.where(KPIExperimentRecord.goal_id == goal_id)
        if recommendation_id is not None:
            statement = statement.where(KPIExperimentRecord.recommendation_id == recommendation_id)
        if status is not None:
            statement = statement.where(KPIExperimentRecord.status == status)
        statement = statement.order_by(KPIExperimentRecord.updated_at.desc(), KPIExperimentRecord.experiment_id.desc()).limit(limit)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_experiment(record) for record in records]


def _copy_json(value: object) -> object:
    return deepcopy(value)


def _apply_scope(record: KPIMetricScopeRecord, scope: MetricScope) -> None:
    record.organization_id = scope.organization_id
    record.scope_kind = scope.scope_kind
    record.agent_id = scope.agent_id
    record.workflow_id = scope.workflow_id
    record.channel = scope.channel
    record.segment_key = scope.segment_key
    record.campaign_key = scope.campaign_key
    record.custom_scope_json = _copy_json(scope.custom_scope)
    record.display_name = scope.display_name
    record.fingerprint = scope.fingerprint
    record.created_at = scope.created_at


def _record_to_scope(record: KPIMetricScopeRecord) -> MetricScope:
    return MetricScope(
        scope_id=record.scope_id,
        organization_id=record.organization_id,
        scope_kind=record.scope_kind,
        agent_id=record.agent_id,
        workflow_id=record.workflow_id,
        channel=record.channel,
        segment_key=record.segment_key,
        campaign_key=record.campaign_key,
        custom_scope=dict(record.custom_scope_json or {}),
        display_name=record.display_name,
        fingerprint=record.fingerprint,
        created_at=record.created_at,
    )


def _apply_observation(record: KPIMetricObservationRecord, observation: MetricObservation) -> None:
    record.organization_id = observation.organization_id
    record.metric_key = observation.metric_key
    record.metric_definition_version = observation.metric_definition_version
    record.scope_id = observation.scope_id
    record.observation_kind = observation.observation_kind
    record.value = observation.value
    record.sample_size = observation.sample_size
    record.confidence = observation.confidence
    record.eligibility_count = observation.eligibility_count
    record.excluded_count = observation.excluded_count
    record.period_start = observation.period_start
    record.period_end = observation.period_end
    record.lookback_days = observation.lookback_days
    record.quality_flags_json = list(observation.quality_flags)
    record.source_summary_json = _copy_json(observation.source_summary)
    record.calculation_version = observation.calculation_version
    record.created_at = observation.created_at


def _record_to_observation(record: KPIMetricObservationRecord) -> MetricObservation:
    return MetricObservation(
        observation_id=record.observation_id,
        organization_id=record.organization_id,
        metric_key=record.metric_key,
        metric_definition_version=record.metric_definition_version,
        scope_id=record.scope_id,
        observation_kind=record.observation_kind,
        value=record.value,
        sample_size=record.sample_size,
        confidence=record.confidence,
        eligibility_count=record.eligibility_count,
        excluded_count=record.excluded_count,
        period_start=record.period_start,
        period_end=record.period_end,
        lookback_days=record.lookback_days,
        quality_flags=list(record.quality_flags_json or []),
        source_summary=dict(record.source_summary_json or {}),
        calculation_version=record.calculation_version,
        created_at=record.created_at,
    )


def _apply_baseline_snapshot(record: KPIBaselineSnapshotRecord, snapshot: BaselineSnapshot) -> None:
    record.organization_id = snapshot.organization_id
    record.goal_id = snapshot.goal_id
    record.metric_key = snapshot.metric_key
    record.scope_id = snapshot.scope_id
    record.source_observation_id = snapshot.source_observation_id
    record.value = snapshot.value
    record.sample_size = snapshot.sample_size
    record.confidence = snapshot.confidence
    record.period_start = snapshot.period_start
    record.period_end = snapshot.period_end
    record.baseline_source = snapshot.baseline_source
    record.baseline_reason = snapshot.baseline_reason
    record.provenance_json = _copy_json(snapshot.provenance)
    record.created_at = snapshot.created_at


def _record_to_baseline_snapshot(record: KPIBaselineSnapshotRecord) -> BaselineSnapshot:
    return BaselineSnapshot(
        baseline_snapshot_id=record.baseline_snapshot_id,
        organization_id=record.organization_id,
        goal_id=record.goal_id,
        metric_key=record.metric_key,
        scope_id=record.scope_id,
        source_observation_id=record.source_observation_id,
        value=record.value,
        sample_size=record.sample_size,
        confidence=record.confidence,
        period_start=record.period_start,
        period_end=record.period_end,
        baseline_source=record.baseline_source,  # type: ignore[arg-type]
        baseline_reason=record.baseline_reason,
        provenance=dict(record.provenance_json or {}),
        created_at=record.created_at,
    )


def _apply_goal(record: KPIGoalRecord, goal: Goal) -> None:
    record.organization_id = goal.organization_id
    record.metric_key = goal.metric_key
    record.scope_id = goal.scope_id
    record.name = goal.name
    record.description = goal.description
    record.baseline_snapshot_id = goal.baseline_snapshot_id
    record.target_value = goal.target_value
    record.status = goal.status
    record.start_at = goal.start_at
    record.target_at = goal.target_at
    record.owner_user_id = goal.owner_user_id
    record.metadata_json = _copy_json(goal.metadata)
    record.latest_evaluation_id = goal.latest_evaluation_id
    record.created_at = goal.created_at
    record.updated_at = goal.updated_at


def _record_to_goal(record: KPIGoalRecord) -> Goal:
    return Goal(
        goal_id=record.goal_id,
        organization_id=record.organization_id,
        metric_key=record.metric_key,
        scope_id=record.scope_id,
        name=record.name,
        description=record.description,
        baseline_snapshot_id=record.baseline_snapshot_id,
        target_value=record.target_value,
        status=record.status,  # type: ignore[arg-type]
        start_at=record.start_at,
        target_at=record.target_at,
        owner_user_id=record.owner_user_id,
        metadata=dict(record.metadata_json or {}),
        latest_evaluation_id=record.latest_evaluation_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _apply_goal_evaluation(record: KPIGoalEvaluationRecord, evaluation: GoalEvaluation) -> None:
    record.organization_id = evaluation.organization_id
    record.goal_id = evaluation.goal_id
    record.observation_id = evaluation.observation_id
    record.status = evaluation.status
    record.progress_ratio = evaluation.progress_ratio
    record.distance_to_target = evaluation.distance_to_target
    record.delta_from_baseline = evaluation.delta_from_baseline
    record.sample_size_sufficient = 1 if evaluation.sample_size_sufficient else 0
    record.freshness_seconds = evaluation.freshness_seconds
    record.notes = evaluation.notes
    record.created_at = evaluation.created_at


def _record_to_goal_evaluation(record: KPIGoalEvaluationRecord) -> GoalEvaluation:
    return GoalEvaluation(
        evaluation_id=record.evaluation_id,
        organization_id=record.organization_id,
        goal_id=record.goal_id,
        observation_id=record.observation_id,
        status=record.status,  # type: ignore[arg-type]
        progress_ratio=record.progress_ratio,
        distance_to_target=record.distance_to_target,
        delta_from_baseline=record.delta_from_baseline,
        sample_size_sufficient=bool(record.sample_size_sufficient),
        freshness_seconds=record.freshness_seconds,
        notes=record.notes,
        created_at=record.created_at,
    )


def _apply_insight(record: KPIInsightRecord, insight: InsightItem) -> None:
    record.organization_id = insight.organization_id
    record.goal_id = insight.goal_id
    record.scope_id = insight.scope_id
    record.metric_key = insight.metric_key
    record.blocker_kind = insight.blocker_kind
    record.title = insight.title
    record.summary = insight.summary
    record.severity = insight.severity
    record.occurrence_count = insight.occurrence_count
    record.rank_score = insight.rank_score
    record.evidence_bundle_json = _copy_json(insight.evidence_bundle)
    record.status = insight.status
    record.stale_after = insight.stale_after
    record.created_at = insight.created_at
    record.updated_at = insight.updated_at


def _record_to_insight(record: KPIInsightRecord) -> InsightItem:
    return InsightItem(
        insight_id=record.insight_id,
        organization_id=record.organization_id,
        goal_id=record.goal_id,
        scope_id=record.scope_id,
        metric_key=record.metric_key,
        blocker_kind=record.blocker_kind,
        title=record.title,
        summary=record.summary,
        severity=record.severity,
        occurrence_count=record.occurrence_count,
        rank_score=record.rank_score,
        evidence_bundle=dict(record.evidence_bundle_json or {}),
        status=record.status,  # type: ignore[arg-type]
        stale_after=record.stale_after,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _apply_recommendation(record: KPIRecommendationRecord, recommendation: RecommendationCandidate) -> None:
    record.organization_id = recommendation.organization_id
    record.goal_id = recommendation.goal_id
    record.scope_id = recommendation.scope_id
    record.metric_key = recommendation.metric_key
    record.insight_id = recommendation.insight_id
    record.category = recommendation.category
    record.title = recommendation.title
    record.summary = recommendation.summary
    record.rationale = recommendation.rationale
    record.projected_impact_min = recommendation.projected_impact_min
    record.projected_impact_max = recommendation.projected_impact_max
    record.projected_confidence = recommendation.projected_confidence
    record.evidence_bundle_json = _copy_json(recommendation.evidence_bundle)
    record.dependency_ids_json = list(recommendation.dependency_ids)
    record.execution_template_json = _copy_json(recommendation.execution_template)
    record.status = recommendation.status
    record.created_at = recommendation.created_at
    record.updated_at = recommendation.updated_at


def _record_to_recommendation(record: KPIRecommendationRecord) -> RecommendationCandidate:
    return RecommendationCandidate(
        recommendation_id=record.recommendation_id,
        organization_id=record.organization_id,
        goal_id=record.goal_id,
        scope_id=record.scope_id,
        metric_key=record.metric_key,
        insight_id=record.insight_id,
        category=record.category,
        title=record.title,
        summary=record.summary,
        rationale=record.rationale,
        projected_impact_min=record.projected_impact_min,
        projected_impact_max=record.projected_impact_max,
        projected_confidence=record.projected_confidence,
        evidence_bundle=dict(record.evidence_bundle_json or {}),
        dependency_ids=list(record.dependency_ids_json or []),
        execution_template=deepcopy(record.execution_template_json),
        status=record.status,  # type: ignore[arg-type]
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _apply_execution_intent(record: KPIExecutionIntentRecord, intent: ExecutionIntent) -> None:
    record.organization_id = intent.organization_id
    record.recommendation_id = intent.recommendation_id
    record.goal_id = intent.goal_id
    record.adapter_kind = intent.adapter_kind
    record.action_type = intent.action_type
    record.execution_mode = intent.execution_mode
    record.requested_by = intent.requested_by
    record.requested_via = intent.requested_via
    record.approved_payload_json = _copy_json(intent.approved_payload)
    record.validation_snapshot_json = _copy_json(intent.validation_snapshot)
    record.safety_level = intent.safety_level
    record.reversibility = intent.reversibility
    record.created_at = intent.created_at


def _record_to_execution_intent(record: KPIExecutionIntentRecord) -> ExecutionIntent:
    return ExecutionIntent(
        execution_intent_id=record.execution_intent_id,
        organization_id=record.organization_id,
        recommendation_id=record.recommendation_id,
        goal_id=record.goal_id,
        adapter_kind=record.adapter_kind,
        action_type=record.action_type,
        execution_mode=record.execution_mode,  # type: ignore[arg-type]
        requested_by=record.requested_by,
        requested_via=record.requested_via,
        approved_payload=dict(record.approved_payload_json or {}),
        validation_snapshot=dict(record.validation_snapshot_json or {}),
        safety_level=record.safety_level,  # type: ignore[arg-type]
        reversibility=record.reversibility,  # type: ignore[arg-type]
        created_at=record.created_at,
    )


def _apply_execution_result(record: KPIExecutionResultRecord, result: ExecutionResult) -> None:
    record.organization_id = result.organization_id
    record.execution_intent_id = result.execution_intent_id
    record.status = result.status
    record.changed_object_refs_json = _copy_json(result.changed_object_refs)
    record.before_state_summary_json = _copy_json(result.before_state_summary)
    record.after_state_summary_json = _copy_json(result.after_state_summary)
    record.diff_artifact_ref = result.diff_artifact_ref
    record.adapter_diagnostics_json = _copy_json(result.adapter_diagnostics)
    record.rollback_handle_json = None if result.rollback_handle is None else _copy_json(result.rollback_handle)
    record.error_code = result.error_code
    record.error_message = result.error_message
    record.created_at = result.created_at


def _record_to_execution_result(record: KPIExecutionResultRecord) -> ExecutionResult:
    return ExecutionResult(
        execution_result_id=record.execution_result_id,
        organization_id=record.organization_id,
        execution_intent_id=record.execution_intent_id,
        status=record.status,  # type: ignore[arg-type]
        changed_object_refs=list(record.changed_object_refs_json or []),
        before_state_summary=dict(record.before_state_summary_json or {}),
        after_state_summary=dict(record.after_state_summary_json or {}),
        diff_artifact_ref=record.diff_artifact_ref,
        adapter_diagnostics=dict(record.adapter_diagnostics_json or {}),
        rollback_handle=None if record.rollback_handle_json is None else dict(record.rollback_handle_json),
        error_code=record.error_code,
        error_message=record.error_message,
        created_at=record.created_at,
    )


def _apply_impact_assessment(record: KPIImpactAssessmentRecord, assessment: ImpactAssessment) -> None:
    record.organization_id = assessment.organization_id
    record.goal_id = assessment.goal_id
    record.recommendation_id = assessment.recommendation_id
    record.execution_intent_id = assessment.execution_intent_id
    record.experiment_id = assessment.experiment_id
    record.metric_key = assessment.metric_key
    record.scope_id = assessment.scope_id
    record.baseline_observation_id = assessment.baseline_observation_id
    record.comparison_observation_id = assessment.comparison_observation_id
    record.attribution_mode = assessment.attribution_mode
    record.attribution_confidence = assessment.attribution_confidence
    record.observed_change = assessment.observed_change
    record.attributed_change = assessment.attributed_change
    record.projected_impact_min = assessment.projected_impact_min
    record.projected_impact_max = assessment.projected_impact_max
    record.attainment_fraction = assessment.attainment_fraction
    record.competing_changes_json = list(assessment.competing_changes)
    record.notes = assessment.notes
    record.created_at = assessment.created_at


def _record_to_impact_assessment(record: KPIImpactAssessmentRecord) -> ImpactAssessment:
    return ImpactAssessment(
        assessment_id=record.assessment_id,
        organization_id=record.organization_id,
        goal_id=record.goal_id,
        recommendation_id=record.recommendation_id,
        execution_intent_id=record.execution_intent_id,
        experiment_id=record.experiment_id,
        metric_key=record.metric_key,
        scope_id=record.scope_id,
        baseline_observation_id=record.baseline_observation_id,
        comparison_observation_id=record.comparison_observation_id,
        attribution_mode=record.attribution_mode,  # type: ignore[arg-type]
        attribution_confidence=record.attribution_confidence,  # type: ignore[arg-type]
        observed_change=record.observed_change,
        attributed_change=record.attributed_change,
        projected_impact_min=record.projected_impact_min,
        projected_impact_max=record.projected_impact_max,
        attainment_fraction=record.attainment_fraction,
        competing_changes=list(record.competing_changes_json or []),
        notes=record.notes,
        created_at=record.created_at,
    )


def _apply_experiment(record: KPIExperimentRecord, experiment: KPIExperiment) -> None:
    record.organization_id = experiment.organization_id
    record.goal_id = experiment.goal_id
    record.recommendation_id = experiment.recommendation_id
    record.name = experiment.name
    record.hypothesis = experiment.hypothesis
    record.status = experiment.status
    record.primary_metric_key = experiment.primary_metric_key
    record.scope_id = experiment.scope_id
    record.notes = experiment.notes
    record.started_at = experiment.started_at
    record.ended_at = experiment.ended_at
    record.created_at = experiment.created_at
    record.updated_at = experiment.updated_at


def _record_to_experiment(record: KPIExperimentRecord) -> KPIExperiment:
    return KPIExperiment(
        experiment_id=record.experiment_id,
        organization_id=record.organization_id,
        goal_id=record.goal_id,
        recommendation_id=record.recommendation_id,
        name=record.name,
        hypothesis=record.hypothesis,
        status=record.status,  # type: ignore[arg-type]
        primary_metric_key=record.primary_metric_key,
        scope_id=record.scope_id,
        notes=record.notes,
        started_at=record.started_at,
        ended_at=record.ended_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
