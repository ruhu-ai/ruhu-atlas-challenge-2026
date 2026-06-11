"""KPI Read Models (Projections).

Read models are denormalized views of KPI data optimized for queries and analytics.
They're updated via event handlers in response to domain events.

Models:
  - GoalAnalyticsProjection: Current goal status with computed metrics
  - GoalTrendProjection: Historical trend data for charting
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Index
from sqlmodel import SQLModel, Field, Column, JSON
from uuid import uuid4


def utcnow() -> datetime:
    """Current UTC time."""
    return datetime.now(timezone.utc)


def _uuid4_str() -> str:
    """Generate a UUID4 string."""
    return str(uuid4())


class GoalAnalyticsProjection(SQLModel, table=True):
    """Read model: Current goal analytics snapshot.

    Denormalized for fast queries. Updated via event handlers.
    Used to populate GoalResponse.current_value, progress_pct, trend, confidence.
    """

    __tablename__ = "kpi_goal_analytics"
    __table_args__ = (
        Index("ix_kpi_goal_analytics_org_created", "organization_id", "created_at"),
        Index("ix_kpi_goal_analytics_status", "organization_id", "goal_status"),
    )

    # Primary key (same as goal definition ID for 1:1 mapping)
    definition_id: str = Field(primary_key=True, index=True)
    organization_id: str = Field(index=True)

    # Latest observation
    current_value: Optional[float] = None
    observed_at: Optional[datetime] = None
    confidence: Optional[float] = None

    # Computed metrics
    progress_pct: Optional[float] = None  # (current / target) * 100
    trend: Optional[str] = None  # "up" | "down" | "flat" | "unknown"
    trend_magnitude: Optional[float] = None  # Percentage change over period

    # Goal status snapshot
    goal_status: str  # "draft" | "active" | "paused" | "completed" | "archived"
    target_value: float
    baseline_value: Optional[float] = None

    # Historical summary
    observation_count: int = 0  # Total observations recorded
    days_active: Optional[int] = None  # Days since activation
    on_track_days: int = 0  # Days goal was on track
    off_track_days: int = 0  # Days goal was off track

    # Custom metadata for analytics
    custom_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON))

    # Versioning for event sourcing
    event_version: int = 0  # Version of last event that updated this projection
    projection_updated_at: datetime = Field(default_factory=utcnow)

    # Audit
    created_at: datetime = Field(index=True)
    updated_at: datetime


class GoalTrendProjection(SQLModel, table=True):
    """Read model: Goal trend history for charting.

    Time-series data for trend analysis and visualization.
    One record per observation; indexed for fast range queries.
    """

    __tablename__ = "kpi_goal_trends"
    __table_args__ = (
        Index("ix_kpi_goal_trends_definition_date", "definition_id", "observed_at"),
        Index("ix_kpi_goal_trends_org_date", "organization_id", "observed_at"),
    )

    trend_id: str = Field(primary_key=True, default_factory=_uuid4_str)
    organization_id: str = Field(index=True)
    definition_id: str = Field(index=True)

    # Observation details
    observed_value: float
    observed_at: datetime = Field(index=True)
    confidence: float = Field(ge=0.0, le=1.0)

    # Context
    observation_kind: str  # "baseline" | "scheduled_refresh" | "manual_entry" | "experiment_readout"
    source_system: Optional[str] = None

    # Analysis
    percent_of_target: float  # (observed_value / target_value) * 100
    is_on_track: bool  # Computed: within 90% for higher_is_better
    variance_from_baseline: Optional[float] = None  # observed - baseline

    custom_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)


class GoalComparisonProjection(SQLModel, table=True):
    """Read model: Goal comparison matrix for dashboards.

    Aggregated view for comparing goals across organization/agent/workflow.
    Updated periodically (hourly batch or event-driven).
    """

    __tablename__ = "kpi_goal_comparisons"

    comparison_id: str = Field(primary_key=True, default_factory=_uuid4_str)
    organization_id: str = Field(index=True)

    # Grouping dimensions
    group_kind: str  # "organization" | "agent" | "workflow" | "channel"
    group_id: Optional[str] = None  # agent_id, workflow_id, etc. (None = org-level)

    # Summary metrics
    total_goals: int = 0
    active_goals: int = 0
    on_track_goals: int = 0
    off_track_goals: int = 0
    avg_progress_pct: Optional[float] = None

    # Risk indicators
    at_risk_count: int = 0  # Goals < 75% of target
    critical_count: int = 0  # Goals < 50% of target

    # Performance
    trending_up_count: int = 0
    trending_down_count: int = 0
    trending_flat_count: int = 0

    custom_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=utcnow)
