from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .models import BaselineSnapshot, ExecutionIntent, ExecutionResult, Goal, GoalEvaluation, ImpactAssessment, InsightItem, KPIExperiment, MetricObservation, MetricScope, RecommendationCandidate


class GoalSummaryReadModel(BaseModel):
    goal_id: str
    name: str
    metric_key: str
    scope_id: str
    status: str
    target_value: float
    baseline_value: float
    current_value: float | None = None
    progress_ratio: float | None = None
    latest_observation_at: datetime | None = None
    latest_evaluation_at: datetime | None = None
    open_insight_count: int = 0
    pending_recommendation_count: int = 0


class GoalDetailReadModel(BaseModel):
    goal: Goal
    scope: MetricScope
    baseline_snapshot: BaselineSnapshot
    latest_observation: MetricObservation | None = None
    latest_evaluation: GoalEvaluation | None = None
    insights: list[InsightItem] = Field(default_factory=list)
    recommendations: list[RecommendationCandidate] = Field(default_factory=list)
    execution_intents: list[ExecutionIntent] = Field(default_factory=list)
    execution_results: list[ExecutionResult] = Field(default_factory=list)
    experiments: list[KPIExperiment] = Field(default_factory=list)
    impact_assessments: list[ImpactAssessment] = Field(default_factory=list)
