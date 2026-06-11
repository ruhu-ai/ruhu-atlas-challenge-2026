from __future__ import annotations

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db_models import Base, RequiredTenantScopeMixin


class KPIMetricScopeRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "kpi_metric_scopes"
    __table_args__ = (
        UniqueConstraint("organization_id", "fingerprint", name="uq_kpi_metric_scopes_org_fingerprint"),
    )

    scope_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    scope_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    workflow_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    channel: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    segment_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    campaign_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    custom_scope_json: Mapped[dict] = mapped_column(JSON, default=dict)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class KPIMetricObservationRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "kpi_metric_observations"

    observation_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    metric_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    metric_definition_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    scope_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("kpi_metric_scopes.scope_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    observation_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    eligibility_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    excluded_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    period_start: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    lookback_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality_flags_json: Mapped[list] = mapped_column(JSON, default=list)
    source_summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
    calculation_version: Mapped[str] = mapped_column(String(64), nullable=False, default="v1")
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class KPIBaselineSnapshotRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "kpi_baseline_snapshots"

    baseline_snapshot_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    goal_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    metric_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    scope_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("kpi_metric_scopes.scope_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_observation_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("kpi_metric_observations.observation_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    value: Mapped[float] = mapped_column(Float, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    period_start: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    baseline_source: Mapped[str] = mapped_column(String(64), nullable=False)
    baseline_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class KPIGoalRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "kpi_goals_v2"

    goal_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    metric_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    scope_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("kpi_metric_scopes.scope_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    baseline_snapshot_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("kpi_baseline_snapshots.baseline_snapshot_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    target_value: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    start_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    target_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    latest_evaluation_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class KPIGoalEvaluationRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "kpi_goal_evaluations"

    evaluation_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    goal_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("kpi_goals_v2.goal_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    observation_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("kpi_metric_observations.observation_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    progress_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    distance_to_target: Mapped[float] = mapped_column(Float, nullable=False)
    delta_from_baseline: Mapped[float] = mapped_column(Float, nullable=False)
    sample_size_sufficient: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    freshness_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class KPIInsightRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "kpi_insights"

    insight_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    goal_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("kpi_goals_v2.goal_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    scope_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("kpi_metric_scopes.scope_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    metric_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    blocker_kind: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[float] = mapped_column(Float, nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    rank_score: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    evidence_bundle_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    stale_after: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class KPIRecommendationRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "kpi_recommendations"

    recommendation_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    goal_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("kpi_goals_v2.goal_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    scope_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("kpi_metric_scopes.scope_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    metric_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    insight_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("kpi_insights.insight_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    category: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    projected_impact_min: Mapped[float] = mapped_column(Float, nullable=False)
    projected_impact_max: Mapped[float] = mapped_column(Float, nullable=False)
    projected_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_bundle_json: Mapped[dict] = mapped_column(JSON, default=dict)
    dependency_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    execution_template_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class KPIExecutionIntentRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "kpi_execution_intents"

    execution_intent_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    recommendation_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("kpi_recommendations.recommendation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    goal_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("kpi_goals_v2.goal_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    adapter_kind: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    execution_mode: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    requested_via: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    validation_snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict)
    safety_level: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reversibility: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class KPIExecutionResultRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "kpi_execution_results"

    execution_result_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    execution_intent_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("kpi_execution_intents.execution_intent_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    changed_object_refs_json: Mapped[list] = mapped_column(JSON, default=list)
    before_state_summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
    after_state_summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
    diff_artifact_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    adapter_diagnostics_json: Mapped[dict] = mapped_column(JSON, default=dict)
    rollback_handle_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class KPIImpactAssessmentRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "kpi_impact_assessments"

    assessment_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    goal_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("kpi_goals_v2.goal_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    recommendation_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("kpi_recommendations.recommendation_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    execution_intent_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("kpi_execution_intents.execution_intent_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    experiment_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("kpi_experiments.experiment_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    metric_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    scope_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("kpi_metric_scopes.scope_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    baseline_observation_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("kpi_metric_observations.observation_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    comparison_observation_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("kpi_metric_observations.observation_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    attribution_mode: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    attribution_confidence: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    observed_change: Mapped[float] = mapped_column(Float, nullable=False)
    attributed_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    projected_impact_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    projected_impact_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    attainment_fraction: Mapped[float | None] = mapped_column(Float, nullable=True)
    competing_changes_json: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class KPIExperimentRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "kpi_experiments"

    experiment_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    goal_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("kpi_goals_v2.goal_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    recommendation_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("kpi_recommendations.recommendation_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    primary_metric_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    scope_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("kpi_metric_scopes.scope_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
