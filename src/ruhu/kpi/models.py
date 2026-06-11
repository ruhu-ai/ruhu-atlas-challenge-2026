from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


MetricDirection = Literal["higher_is_better", "lower_is_better"]
MetricUnit = Literal["percent", "score_100", "seconds", "usd"]
ScopeKind = Literal["organization", "agent", "workflow", "channel", "segment", "campaign", "custom"]
RuntimeChannel = Literal["phone", "whatsapp", "web_chat", "web_widget", "browser"]
ExecutionMode = Literal["preview", "apply", "rollback"]
ExecutionStatus = Literal[
    "preview_requested",
    "preview_succeeded",
    "preview_failed",
    "apply_requested",
    "apply_succeeded",
    "apply_failed",
    "rollback_requested",
    "rollback_succeeded",
    "rollback_failed",
]
ExecutionSafetyLevel = Literal["low", "medium", "high"]
ExecutionReversibility = Literal["reversible", "irreversible", "unknown"]
ObservationKind = Literal[
    "baseline",
    "scheduled_refresh",
    "manual_refresh",
    "manual_entry",
    "experiment_readout",
    "post_intervention_readout",
]
GoalStatus = Literal["draft", "active", "on_track", "at_risk", "stalled", "completed", "paused", "abandoned"]
InsightStatus = Literal["open", "accepted", "dismissed", "superseded"]
RecommendationStatus = Literal[
    "draft",
    "ready_for_review",
    "approved",
    "rejected",
    "execution_requested",
    "executed",
    "execution_failed",
    "superseded",
]
AttributionMode = Literal[
    "uncontrolled_observation",
    "sequential_rollout",
    "canary_rollout",
    "ab_experiment",
    "manual_judgment",
]
AttributionConfidence = Literal["none", "weak", "moderate", "strong", "experiment_validated"]
ExperimentStatus = Literal["draft", "running", "completed", "aborted"]

_RUNTIME_CHANNELS = {"phone", "whatsapp", "web_chat", "web_widget", "browser"}
_OUTCOME_LABELS = {
    "resolved",
    "transferred",
    "abandoned",
    "failed",
    "voicemail",
    "callback_scheduled",
    "follow_up_required",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


def normalize_channel(value: str | None) -> RuntimeChannel | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized not in _RUNTIME_CHANNELS:
        raise ValueError(f"unsupported runtime channel: {value}")
    return normalized  # type: ignore[return-value]


class MetricDefinition(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    metric_key: str
    version: int = 1
    label: str
    description: str
    canonical_unit: MetricUnit
    display_unit: str
    value_kind: str
    direction: MetricDirection
    min_value: float | None = None
    max_value: float | None = None
    default_lookback_days: int = 30
    minimum_sample_size: int = 10
    baseline_strategy: str = "measured_default_manual_override"
    eligibility_rule: str | None = None
    calculation_notes: str | None = None
    calculation_variant: str | None = None
    requires_outcome_taxonomy: bool = False
    auto_measurable: bool = True
    measurement_sources: list[str] = Field(default_factory=list)
    contained_outcomes: list[str] = Field(default_factory=list)
    active: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("minimum_sample_size")
    @classmethod
    def _validate_sample_size(cls, value: int) -> int:
        if value < 1:
            raise ValueError("minimum_sample_size must be positive")
        return value

    @field_validator("contained_outcomes")
    @classmethod
    def _validate_contained_outcomes(cls, value: list[str]) -> list[str]:
        invalid = sorted({item for item in value if item not in _OUTCOME_LABELS})
        if invalid:
            raise ValueError(f"unsupported contained outcomes: {', '.join(invalid)}")
        return list(value)


class MetricScope(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    scope_id: str = Field(default_factory=new_id)
    organization_id: str
    scope_kind: ScopeKind
    agent_id: str | None = None
    workflow_id: str | None = None
    channel: RuntimeChannel | None = None
    segment_key: str | None = None
    campaign_key: str | None = None
    custom_scope: dict[str, object] = Field(default_factory=dict)
    display_name: str | None = None
    fingerprint: str
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("channel", mode="before")
    @classmethod
    def _normalize_channel(cls, value: str | None) -> RuntimeChannel | None:
        return normalize_channel(value)


class MetricObservation(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    observation_id: str = Field(default_factory=new_id)
    organization_id: str
    metric_key: str
    metric_definition_version: int = 1
    scope_id: str
    observation_kind: ObservationKind = "scheduled_refresh"
    value: float
    sample_size: int
    confidence: float
    eligibility_count: int | None = None
    excluded_count: int | None = None
    period_start: datetime
    period_end: datetime
    lookback_days: int | None = None
    quality_flags: list[str] = Field(default_factory=list)
    source_summary: dict[str, object] = Field(default_factory=dict)
    calculation_version: str = "v1"
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("sample_size")
    @classmethod
    def _validate_sample_size(cls, value: int) -> int:
        if value < 0:
            raise ValueError("sample_size cannot be negative")
        return value

    @field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("confidence must be between 0 and 1")
        return value

    @field_validator("period_end")
    @classmethod
    def _validate_period_end(cls, value: datetime, info) -> datetime:
        period_start = info.data.get("period_start")
        if isinstance(period_start, datetime) and value < period_start:
            raise ValueError("period_end must be greater than or equal to period_start")
        return value


class BaselineSnapshot(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    baseline_snapshot_id: str = Field(default_factory=new_id)
    organization_id: str
    goal_id: str | None = None
    metric_key: str
    scope_id: str
    source_observation_id: str | None = None
    value: float
    sample_size: int
    confidence: float
    period_start: datetime
    period_end: datetime
    baseline_source: Literal["measured", "manual_override"]
    baseline_reason: str | None = None
    provenance: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class Goal(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    goal_id: str = Field(default_factory=new_id)
    organization_id: str
    metric_key: str
    scope_id: str
    name: str
    description: str | None = None
    baseline_snapshot_id: str
    target_value: float
    status: GoalStatus = "active"
    start_at: datetime = Field(default_factory=utc_now)
    target_at: datetime
    owner_user_id: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    latest_evaluation_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class GoalEvaluation(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    evaluation_id: str = Field(default_factory=new_id)
    organization_id: str
    goal_id: str
    observation_id: str
    status: GoalStatus
    progress_ratio: float
    distance_to_target: float
    delta_from_baseline: float
    sample_size_sufficient: bool
    freshness_seconds: int | None = None
    notes: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class InsightSignal(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    blocker_kind: str
    title: str
    summary: str
    severity: float = 1.0
    occurrence_count: int = 1
    metric_relevance: float = 1.0
    freshness_score: float = 1.0
    evidence_bundle: dict[str, object] = Field(default_factory=dict)
    examples: list[str] = Field(default_factory=list)


class InsightItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    insight_id: str = Field(default_factory=new_id)
    organization_id: str
    goal_id: str | None = None
    scope_id: str
    metric_key: str
    blocker_kind: str
    title: str
    summary: str
    severity: float
    occurrence_count: int
    rank_score: float
    evidence_bundle: dict[str, object] = Field(default_factory=dict)
    status: InsightStatus = "open"
    stale_after: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RecommendationCandidate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    recommendation_id: str = Field(default_factory=new_id)
    organization_id: str
    goal_id: str | None = None
    scope_id: str
    metric_key: str
    insight_id: str | None = None
    category: str
    title: str
    summary: str
    rationale: str
    projected_impact_min: float
    projected_impact_max: float
    projected_confidence: float
    evidence_bundle: dict[str, object] = Field(default_factory=dict)
    dependency_ids: list[str] = Field(default_factory=list)
    execution_template: dict[str, object] | None = None
    status: RecommendationStatus = "draft"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ExecutionIntent(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    execution_intent_id: str = Field(default_factory=new_id)
    organization_id: str
    recommendation_id: str
    goal_id: str | None = None
    adapter_kind: str
    action_type: str
    execution_mode: ExecutionMode
    requested_by: str | None = None
    requested_via: str
    approved_payload: dict[str, object] = Field(default_factory=dict)
    validation_snapshot: dict[str, object] = Field(default_factory=dict)
    safety_level: ExecutionSafetyLevel = "medium"
    reversibility: ExecutionReversibility = "unknown"
    created_at: datetime = Field(default_factory=utc_now)


class ExecutionResult(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    execution_result_id: str = Field(default_factory=new_id)
    organization_id: str
    execution_intent_id: str
    status: ExecutionStatus
    changed_object_refs: list[dict[str, object]] = Field(default_factory=list)
    before_state_summary: dict[str, object] = Field(default_factory=dict)
    after_state_summary: dict[str, object] = Field(default_factory=dict)
    diff_artifact_ref: str | None = None
    adapter_diagnostics: dict[str, object] = Field(default_factory=dict)
    rollback_handle: dict[str, object] | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ImpactAssessment(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    assessment_id: str = Field(default_factory=new_id)
    organization_id: str
    goal_id: str | None = None
    recommendation_id: str | None = None
    execution_intent_id: str | None = None
    experiment_id: str | None = None
    metric_key: str
    scope_id: str
    baseline_observation_id: str
    comparison_observation_id: str
    attribution_mode: AttributionMode = "uncontrolled_observation"
    attribution_confidence: AttributionConfidence = "weak"
    observed_change: float
    attributed_change: float | None = None
    projected_impact_min: float | None = None
    projected_impact_max: float | None = None
    attainment_fraction: float | None = None
    competing_changes: list[str] = Field(default_factory=list)
    notes: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class KPIExperiment(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    experiment_id: str = Field(default_factory=new_id)
    organization_id: str
    goal_id: str | None = None
    recommendation_id: str | None = None
    name: str
    hypothesis: str
    status: ExperimentStatus = "draft"
    primary_metric_key: str
    scope_id: str
    notes: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
