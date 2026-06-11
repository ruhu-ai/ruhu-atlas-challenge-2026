from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .kpi import (
    BaselineSnapshot,
    ExecutionIntent,
    ExecutionResult,
    Goal,
    GoalDetailReadModel,
    GoalEvaluation,
    GoalSummaryReadModel,
    ImpactAssessment,
    InsightItem,
    InsightStatus,
    KPIExperiment,
    KPIRuntime,
    MetricDefinition,
    MetricObservation,
    MetricScope,
    ObservationKind,
    RecommendationCandidate,
    RecommendationStatus,
    ScopeKind,
)

OrganizationResolver = Callable[[Request, str | None], str]


class KPIScopeCreateRequest(BaseModel):
    organization_id: str | None = None
    scope_kind: ScopeKind
    agent_id: str | None = None
    workflow_id: str | None = None
    channel: str | None = None
    segment_key: str | None = None
    campaign_key: str | None = None
    custom_scope: dict[str, object] = Field(default_factory=dict)
    display_name: str | None = None


class KPIObservationCreateRequest(BaseModel):
    organization_id: str | None = None
    metric_key: str
    scope_id: str
    value: float
    sample_size: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)
    period_start: datetime
    period_end: datetime
    observation_kind: ObservationKind = "manual_entry"
    eligibility_count: int | None = Field(default=None, ge=0)
    excluded_count: int | None = Field(default=None, ge=0)
    lookback_days: int | None = Field(default=None, ge=1)
    quality_flags: list[str] = Field(default_factory=list)
    source_summary: dict[str, object] = Field(default_factory=dict)
    calculation_version: str = "manual_v1"


class KPIBaselineCreateRequest(BaseModel):
    organization_id: str | None = None
    metric_key: str
    scope_id: str
    goal_id: str | None = None
    observation_id: str | None = None
    manual_value: float | None = None
    manual_sample_size: int | None = Field(default=None, ge=0)
    manual_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    period_start: datetime | None = None
    period_end: datetime | None = None
    reason: str | None = None
    provenance: dict[str, object] = Field(default_factory=dict)


class KPIObservationRefreshRequest(BaseModel):
    organization_id: str | None = None
    lookback_days: int | None = Field(default=None, ge=1)
    period_end: datetime | None = None


class KPIGoalCreateRequest(BaseModel):
    organization_id: str | None = None
    metric_key: str
    scope_id: str
    name: str
    target_value: float
    target_at: datetime
    description: str | None = None
    owner_user_id: str | None = None
    baseline_snapshot_id: str | None = None
    start_at: datetime | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class KPIGoalEvaluateRequest(BaseModel):
    organization_id: str | None = None
    observation_id: str | None = None


class KPIGoalStatusRequest(BaseModel):
    organization_id: str | None = None
    status: Literal["active", "paused", "completed", "abandoned"]


class KPIInsightStatusRequest(BaseModel):
    organization_id: str | None = None
    status: InsightStatus


class KPIRecommendationGenerateRequest(BaseModel):
    organization_id: str | None = None
    insight_ids: list[str] | None = None


class KPIRecommendationStatusRequest(BaseModel):
    organization_id: str | None = None
    status: RecommendationStatus


class KPIExecutionIntentCreateRequest(BaseModel):
    organization_id: str | None = None
    execution_mode: Literal["preview", "apply"]
    requested_by: str | None = None
    requested_via: str = "kpi_api"
    approved_payload: dict[str, object] = Field(default_factory=dict)


class KPIExperimentCreateRequest(BaseModel):
    organization_id: str | None = None
    goal_id: str | None = None
    recommendation_id: str | None = None
    name: str
    hypothesis: str
    primary_metric_key: str
    scope_id: str
    notes: str | None = None


class KPIExperimentStatusRequest(BaseModel):
    organization_id: str | None = None
    status: Literal["draft", "running", "completed", "aborted"]
    notes: str | None = None


class KPIImpactAssessmentCreateRequest(BaseModel):
    organization_id: str | None = None
    metric_key: str
    scope_id: str
    baseline_observation_id: str
    comparison_observation_id: str
    goal_id: str | None = None
    recommendation_id: str | None = None
    execution_intent_id: str | None = None
    experiment_id: str | None = None
    attribution_mode: str = "uncontrolled_observation"
    attribution_confidence: str = "weak"
    attributed_change: float | None = None
    competing_changes: list[str] = Field(default_factory=list)
    notes: str | None = None


def install_kpi_router(
    app: FastAPI,
    *,
    runtime: KPIRuntime | None,
    resolve_organization_id: OrganizationResolver,
    rate_limiter=None,
) -> None:
    router = APIRouter(
        tags=["kpi"],
        dependencies=[rate_limiter] if rate_limiter else [],
    )

    def _require_runtime() -> KPIRuntime:
        if runtime is None:
            raise HTTPException(status_code=503, detail="kpi runtime is not configured")
        return runtime

    def _organization_id(request: Request, requested: str | None) -> str:
        return resolve_organization_id(request, requested)

    def _scope(scope_id: str, *, organization_id: str) -> MetricScope:
        scope = _require_runtime().store.get_scope(scope_id)
        if scope is None or scope.organization_id != organization_id:
            raise HTTPException(status_code=404, detail="scope not found")
        return scope

    def _baseline(baseline_snapshot_id: str, *, organization_id: str) -> BaselineSnapshot:
        baseline = _require_runtime().store.get_baseline_snapshot(baseline_snapshot_id)
        if baseline is None or baseline.organization_id != organization_id:
            raise HTTPException(status_code=404, detail="baseline snapshot not found")
        return baseline

    def _goal(goal_id: str, *, organization_id: str) -> Goal:
        goal = _require_runtime().store.get_goal(goal_id)
        if goal is None or goal.organization_id != organization_id:
            raise HTTPException(status_code=404, detail="goal not found")
        return goal

    def _insight(insight_id: str, *, organization_id: str) -> InsightItem:
        insight = _require_runtime().store.get_insight(insight_id)
        if insight is None or insight.organization_id != organization_id:
            raise HTTPException(status_code=404, detail="insight not found")
        return insight

    def _recommendation(recommendation_id: str, *, organization_id: str) -> RecommendationCandidate:
        recommendation = _require_runtime().store.get_recommendation(recommendation_id)
        if recommendation is None or recommendation.organization_id != organization_id:
            raise HTTPException(status_code=404, detail="recommendation not found")
        return recommendation

    def _execution_intent(execution_intent_id: str, *, organization_id: str) -> ExecutionIntent:
        intent = _require_runtime().store.get_execution_intent(execution_intent_id)
        if intent is None or intent.organization_id != organization_id:
            raise HTTPException(status_code=404, detail="execution intent not found")
        return intent

    def _experiment(experiment_id: str, *, organization_id: str) -> KPIExperiment:
        experiment = _require_runtime().store.get_experiment(experiment_id)
        if experiment is None or experiment.organization_id != organization_id:
            raise HTTPException(status_code=404, detail="experiment not found")
        return experiment

    def _goal_detail(goal_id: str, *, organization_id: str) -> GoalDetailReadModel:
        detail = _require_runtime().read_service.get_goal_detail(goal_id)
        if detail is None or detail.goal.organization_id != organization_id:
            raise HTTPException(status_code=404, detail="goal not found")
        return detail

    def _bad_request(exc: ValueError) -> HTTPException:
        return HTTPException(status_code=400, detail=str(exc))

    @router.get("/kpi/definitions", response_model=list[MetricDefinition])
    def list_metric_definitions() -> list[MetricDefinition]:
        return _require_runtime().service.list_metric_definitions()

    @router.get("/kpi/definitions/{metric_key}", response_model=MetricDefinition)
    def get_metric_definition(metric_key: str) -> MetricDefinition:
        definition = _require_runtime().service.get_metric_definition(metric_key)
        if definition is None:
            raise HTTPException(status_code=404, detail="metric definition not found")
        return definition

    @router.get("/kpi/scopes", response_model=list[MetricScope])
    def list_scopes(
        request: Request,
        organization_id: str | None = None,
        scope_kind: ScopeKind | None = None,
    ) -> list[MetricScope]:
        effective_organization_id = _organization_id(request, organization_id)
        return _require_runtime().store.list_scopes(effective_organization_id, scope_kind=scope_kind)

    @router.post("/kpi/scopes", response_model=MetricScope)
    def create_scope(payload: KPIScopeCreateRequest, request: Request) -> MetricScope:
        effective_organization_id = _organization_id(request, payload.organization_id)
        try:
            return _require_runtime().service.ensure_scope(
                organization_id=effective_organization_id,
                scope_kind=payload.scope_kind,
                agent_id=payload.agent_id,
                workflow_id=payload.workflow_id,
                channel=payload.channel,
                segment_key=payload.segment_key,
                campaign_key=payload.campaign_key,
                custom_scope=payload.custom_scope,
                display_name=payload.display_name,
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.get("/kpi/scopes/{scope_id}", response_model=MetricScope)
    def get_scope(scope_id: str, request: Request, organization_id: str | None = None) -> MetricScope:
        return _scope(scope_id, organization_id=_organization_id(request, organization_id))

    @router.get("/kpi/scopes/{scope_id}/measurement-support")
    def get_scope_measurement_support(
        scope_id: str,
        request: Request,
        organization_id: str | None = None,
    ) -> list[dict[str, object]]:
        scope = _scope(scope_id, organization_id=_organization_id(request, organization_id))
        return _require_runtime().measurement_service.list_measurement_support(scope)

    @router.get("/kpi/observations", response_model=list[MetricObservation])
    def list_observations(
        request: Request,
        organization_id: str | None = None,
        metric_key: str | None = None,
        scope_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> list[MetricObservation]:
        effective_organization_id = _organization_id(request, organization_id)
        if scope_id is not None:
            _scope(scope_id, organization_id=effective_organization_id)
        return _require_runtime().store.list_observations(
            effective_organization_id,
            metric_key=metric_key,
            scope_id=scope_id,
            limit=limit,
        )

    @router.post("/kpi/observations", response_model=MetricObservation)
    def create_observation(payload: KPIObservationCreateRequest, request: Request) -> MetricObservation:
        effective_organization_id = _organization_id(request, payload.organization_id)
        _scope(payload.scope_id, organization_id=effective_organization_id)
        try:
            return _require_runtime().service.record_observation(
                organization_id=effective_organization_id,
                metric_key=payload.metric_key,
                scope_id=payload.scope_id,
                value=payload.value,
                sample_size=payload.sample_size,
                confidence=payload.confidence,
                period_start=payload.period_start,
                period_end=payload.period_end,
                observation_kind=payload.observation_kind,
                eligibility_count=payload.eligibility_count,
                excluded_count=payload.excluded_count,
                lookback_days=payload.lookback_days,
                quality_flags=payload.quality_flags,
                source_summary=payload.source_summary,
                calculation_version=payload.calculation_version,
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.get("/kpi/baselines", response_model=list[BaselineSnapshot])
    def list_baselines(
        request: Request,
        organization_id: str | None = None,
        goal_id: str | None = None,
    ) -> list[BaselineSnapshot]:
        effective_organization_id = _organization_id(request, organization_id)
        if goal_id is not None:
            _goal(goal_id, organization_id=effective_organization_id)
        return _require_runtime().store.list_baseline_snapshots(effective_organization_id, goal_id=goal_id)

    @router.post("/kpi/baselines", response_model=BaselineSnapshot)
    def create_baseline(payload: KPIBaselineCreateRequest, request: Request) -> BaselineSnapshot:
        effective_organization_id = _organization_id(request, payload.organization_id)
        _scope(payload.scope_id, organization_id=effective_organization_id)
        if payload.goal_id is not None:
            _goal(payload.goal_id, organization_id=effective_organization_id)
        try:
            return _require_runtime().service.create_baseline_snapshot(
                organization_id=effective_organization_id,
                metric_key=payload.metric_key,
                scope_id=payload.scope_id,
                goal_id=payload.goal_id,
                observation_id=payload.observation_id,
                manual_value=payload.manual_value,
                manual_sample_size=payload.manual_sample_size,
                manual_confidence=payload.manual_confidence,
                period_start=payload.period_start,
                period_end=payload.period_end,
                reason=payload.reason,
                provenance=payload.provenance,
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.post("/kpi/scopes/{scope_id}/measurements/{metric_key}/refresh", response_model=MetricObservation)
    def refresh_observation(
        scope_id: str,
        metric_key: str,
        payload: KPIObservationRefreshRequest,
        request: Request,
    ) -> MetricObservation:
        effective_organization_id = _organization_id(request, payload.organization_id)
        _scope(scope_id, organization_id=effective_organization_id)
        try:
            return _require_runtime().measurement_service.refresh_observation(
                organization_id=effective_organization_id,
                metric_key=metric_key,
                scope_id=scope_id,
                lookback_days=payload.lookback_days,
                period_end=payload.period_end,
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.get("/kpi/goals", response_model=list[GoalSummaryReadModel])
    def list_goal_summaries(
        request: Request,
        organization_id: str | None = None,
        scope_id: str | None = None,
        status: str | None = None,
    ) -> list[GoalSummaryReadModel]:
        effective_organization_id = _organization_id(request, organization_id)
        if scope_id is not None:
            _scope(scope_id, organization_id=effective_organization_id)
        return _require_runtime().read_service.list_goal_summaries(
            effective_organization_id,
            scope_id=scope_id,
            status=status,
        )

    @router.post("/kpi/goals", response_model=Goal)
    def create_goal(payload: KPIGoalCreateRequest, request: Request) -> Goal:
        effective_organization_id = _organization_id(request, payload.organization_id)
        _scope(payload.scope_id, organization_id=effective_organization_id)
        if payload.baseline_snapshot_id is not None:
            _baseline(payload.baseline_snapshot_id, organization_id=effective_organization_id)
        try:
            return _require_runtime().service.create_goal(
                organization_id=effective_organization_id,
                metric_key=payload.metric_key,
                scope_id=payload.scope_id,
                name=payload.name,
                target_value=payload.target_value,
                target_at=payload.target_at,
                description=payload.description,
                owner_user_id=payload.owner_user_id,
                baseline_snapshot_id=payload.baseline_snapshot_id,
                start_at=payload.start_at,
                metadata=payload.metadata,
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.get("/kpi/goals/{goal_id}", response_model=GoalDetailReadModel)
    def get_goal_detail(
        goal_id: str,
        request: Request,
        organization_id: str | None = None,
    ) -> GoalDetailReadModel:
        return _goal_detail(goal_id, organization_id=_organization_id(request, organization_id))

    @router.get("/kpi/goals/{goal_id}/evaluations", response_model=list[GoalEvaluation])
    def list_goal_evaluations(
        goal_id: str,
        request: Request,
        organization_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> list[GoalEvaluation]:
        effective_organization_id = _organization_id(request, organization_id)
        _goal(goal_id, organization_id=effective_organization_id)
        return _require_runtime().store.list_goal_evaluations(goal_id, limit=limit)

    @router.post("/kpi/goals/{goal_id}/evaluate", response_model=GoalEvaluation)
    def evaluate_goal(
        goal_id: str,
        payload: KPIGoalEvaluateRequest,
        request: Request,
    ) -> GoalEvaluation:
        effective_organization_id = _organization_id(request, payload.organization_id)
        _goal(goal_id, organization_id=effective_organization_id)
        try:
            return _require_runtime().service.evaluate_goal(goal_id, observation_id=payload.observation_id)
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.post("/kpi/goals/{goal_id}/status", response_model=Goal)
    def update_goal_status(
        goal_id: str,
        payload: KPIGoalStatusRequest,
        request: Request,
    ) -> Goal:
        effective_organization_id = _organization_id(request, payload.organization_id)
        _goal(goal_id, organization_id=effective_organization_id)
        service = _require_runtime().service
        if payload.status == "active":
            return service.resume_goal(goal_id)
        if payload.status == "paused":
            return service.pause_goal(goal_id)
        if payload.status == "completed":
            return service.complete_goal(goal_id)
        if payload.status == "abandoned":
            return service.abandon_goal(goal_id)
        raise HTTPException(status_code=400, detail=f"unsupported goal status update: {payload.status}")

    @router.get("/kpi/goals/{goal_id}/insights", response_model=list[InsightItem])
    def list_goal_insights(
        goal_id: str,
        request: Request,
        organization_id: str | None = None,
        status: InsightStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> list[InsightItem]:
        effective_organization_id = _organization_id(request, organization_id)
        goal = _goal(goal_id, organization_id=effective_organization_id)
        return _require_runtime().store.list_insights(
            effective_organization_id,
            goal_id=goal.goal_id,
            scope_id=goal.scope_id,
            status=status,
            limit=limit,
        )

    @router.post("/kpi/goals/{goal_id}/insights/generate", response_model=list[InsightItem])
    def generate_goal_insights(
        goal_id: str,
        request: Request,
        organization_id: str | None = None,
    ) -> list[InsightItem]:
        effective_organization_id = _organization_id(request, organization_id)
        _goal(goal_id, organization_id=effective_organization_id)
        return _require_runtime().insight_analyzer.generate_for_goal(goal_id)

    @router.post("/kpi/insights/{insight_id}/status", response_model=InsightItem)
    def update_insight_status(
        insight_id: str,
        payload: KPIInsightStatusRequest,
        request: Request,
    ) -> InsightItem:
        effective_organization_id = _organization_id(request, payload.organization_id)
        _insight(insight_id, organization_id=effective_organization_id)
        try:
            return _require_runtime().service.update_insight_status(insight_id, status=payload.status)
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.get("/kpi/goals/{goal_id}/recommendations", response_model=list[RecommendationCandidate])
    def list_goal_recommendations(
        goal_id: str,
        request: Request,
        organization_id: str | None = None,
        status: RecommendationStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> list[RecommendationCandidate]:
        effective_organization_id = _organization_id(request, organization_id)
        goal = _goal(goal_id, organization_id=effective_organization_id)
        return _require_runtime().store.list_recommendations(
            effective_organization_id,
            goal_id=goal.goal_id,
            scope_id=goal.scope_id,
            status=status,
            limit=limit,
        )

    @router.post("/kpi/goals/{goal_id}/recommendations/generate", response_model=list[RecommendationCandidate])
    def generate_goal_recommendations(
        goal_id: str,
        payload: KPIRecommendationGenerateRequest,
        request: Request,
    ) -> list[RecommendationCandidate]:
        effective_organization_id = _organization_id(request, payload.organization_id)
        _goal(goal_id, organization_id=effective_organization_id)
        try:
            return _require_runtime().service.generate_recommendations(
                organization_id=effective_organization_id,
                goal_id=goal_id,
                insight_ids=payload.insight_ids,
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.post("/kpi/recommendations/{recommendation_id}/status", response_model=RecommendationCandidate)
    def update_recommendation_status(
        recommendation_id: str,
        payload: KPIRecommendationStatusRequest,
        request: Request,
    ) -> RecommendationCandidate:
        effective_organization_id = _organization_id(request, payload.organization_id)
        _recommendation(recommendation_id, organization_id=effective_organization_id)
        try:
            return _require_runtime().service.update_recommendation_status(
                recommendation_id,
                status=payload.status,
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.get("/kpi/execution-intents", response_model=list[ExecutionIntent])
    def list_execution_intents(
        request: Request,
        organization_id: str | None = None,
        goal_id: str | None = None,
        recommendation_id: str | None = None,
        execution_mode: Literal["preview", "apply", "rollback"] | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> list[ExecutionIntent]:
        effective_organization_id = _organization_id(request, organization_id)
        if goal_id is not None:
            _goal(goal_id, organization_id=effective_organization_id)
        if recommendation_id is not None:
            _recommendation(recommendation_id, organization_id=effective_organization_id)
        return _require_runtime().store.list_execution_intents(
            effective_organization_id,
            goal_id=goal_id,
            recommendation_id=recommendation_id,
            execution_mode=execution_mode,
            limit=limit,
        )

    @router.get("/kpi/execution-results", response_model=list[ExecutionResult])
    def list_execution_results(
        request: Request,
        organization_id: str | None = None,
        execution_intent_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> list[ExecutionResult]:
        effective_organization_id = _organization_id(request, organization_id)
        if execution_intent_id is not None:
            _execution_intent(execution_intent_id, organization_id=effective_organization_id)
        return _require_runtime().store.list_execution_results(
            effective_organization_id,
            execution_intent_id=execution_intent_id,
            limit=limit,
        )

    @router.post("/kpi/recommendations/{recommendation_id}/execution-intents", response_model=ExecutionIntent)
    def create_execution_intent(
        recommendation_id: str,
        payload: KPIExecutionIntentCreateRequest,
        request: Request,
    ) -> ExecutionIntent:
        effective_organization_id = _organization_id(request, payload.organization_id)
        _recommendation(recommendation_id, organization_id=effective_organization_id)
        try:
            return _require_runtime().service.request_execution_intent(
                recommendation_id=recommendation_id,
                execution_mode=payload.execution_mode,
                requested_via=payload.requested_via,
                requested_by=payload.requested_by,
                approved_payload=payload.approved_payload,
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.post("/kpi/execution-intents/{execution_intent_id}/preview", response_model=ExecutionResult)
    def preview_execution_intent(
        execution_intent_id: str,
        request: Request,
        organization_id: str | None = None,
    ) -> ExecutionResult:
        effective_organization_id = _organization_id(request, organization_id)
        _execution_intent(execution_intent_id, organization_id=effective_organization_id)
        try:
            return _require_runtime().service.preview_execution_intent(execution_intent_id)
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.post("/kpi/execution-intents/{execution_intent_id}/apply", response_model=ExecutionResult)
    def apply_execution_intent(
        execution_intent_id: str,
        request: Request,
        organization_id: str | None = None,
    ) -> ExecutionResult:
        effective_organization_id = _organization_id(request, organization_id)
        _execution_intent(execution_intent_id, organization_id=effective_organization_id)
        try:
            return _require_runtime().service.apply_execution_intent(execution_intent_id)
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.get("/kpi/experiments", response_model=list[KPIExperiment])
    def list_experiments(
        request: Request,
        organization_id: str | None = None,
        goal_id: str | None = None,
        recommendation_id: str | None = None,
        status: Literal["draft", "running", "completed", "aborted"] | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> list[KPIExperiment]:
        effective_organization_id = _organization_id(request, organization_id)
        if goal_id is not None:
            _goal(goal_id, organization_id=effective_organization_id)
        if recommendation_id is not None:
            _recommendation(recommendation_id, organization_id=effective_organization_id)
        return _require_runtime().store.list_experiments(
            effective_organization_id,
            goal_id=goal_id,
            recommendation_id=recommendation_id,
            status=status,
            limit=limit,
        )

    @router.post("/kpi/experiments", response_model=KPIExperiment)
    def create_experiment(
        payload: KPIExperimentCreateRequest,
        request: Request,
    ) -> KPIExperiment:
        effective_organization_id = _organization_id(request, payload.organization_id)
        _scope(payload.scope_id, organization_id=effective_organization_id)
        if payload.goal_id is not None:
            _goal(payload.goal_id, organization_id=effective_organization_id)
        if payload.recommendation_id is not None:
            _recommendation(payload.recommendation_id, organization_id=effective_organization_id)
        try:
            return _require_runtime().service.create_experiment(
                organization_id=effective_organization_id,
                scope_id=payload.scope_id,
                primary_metric_key=payload.primary_metric_key,
                name=payload.name,
                hypothesis=payload.hypothesis,
                goal_id=payload.goal_id,
                recommendation_id=payload.recommendation_id,
                notes=payload.notes,
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.post("/kpi/experiments/{experiment_id}/status", response_model=KPIExperiment)
    def update_experiment_status(
        experiment_id: str,
        payload: KPIExperimentStatusRequest,
        request: Request,
    ) -> KPIExperiment:
        effective_organization_id = _organization_id(request, payload.organization_id)
        _experiment(experiment_id, organization_id=effective_organization_id)
        try:
            return _require_runtime().service.update_experiment_status(
                experiment_id,
                status=payload.status,
                notes=payload.notes,
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc

    @router.get("/kpi/impact-assessments", response_model=list[ImpactAssessment])
    def list_impact_assessments(
        request: Request,
        organization_id: str | None = None,
        goal_id: str | None = None,
        recommendation_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> list[ImpactAssessment]:
        effective_organization_id = _organization_id(request, organization_id)
        if goal_id is not None:
            _goal(goal_id, organization_id=effective_organization_id)
        if recommendation_id is not None:
            _recommendation(recommendation_id, organization_id=effective_organization_id)
        return _require_runtime().store.list_impact_assessments(
            effective_organization_id,
            goal_id=goal_id,
            recommendation_id=recommendation_id,
            limit=limit,
        )

    @router.post("/kpi/impact-assessments", response_model=ImpactAssessment)
    def create_impact_assessment(
        payload: KPIImpactAssessmentCreateRequest,
        request: Request,
    ) -> ImpactAssessment:
        effective_organization_id = _organization_id(request, payload.organization_id)
        _scope(payload.scope_id, organization_id=effective_organization_id)
        if payload.goal_id is not None:
            _goal(payload.goal_id, organization_id=effective_organization_id)
        if payload.recommendation_id is not None:
            _recommendation(payload.recommendation_id, organization_id=effective_organization_id)
        try:
            return _require_runtime().service.record_impact_assessment(
                organization_id=effective_organization_id,
                metric_key=payload.metric_key,
                scope_id=payload.scope_id,
                baseline_observation_id=payload.baseline_observation_id,
                comparison_observation_id=payload.comparison_observation_id,
                goal_id=payload.goal_id,
                recommendation_id=payload.recommendation_id,
                execution_intent_id=payload.execution_intent_id,
                experiment_id=payload.experiment_id,
                attribution_mode=payload.attribution_mode,  # type: ignore[arg-type]
                attribution_confidence=payload.attribution_confidence,  # type: ignore[arg-type]
                attributed_change=payload.attributed_change,
                competing_changes=payload.competing_changes,
                notes=payload.notes,
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc

    app.include_router(router)
