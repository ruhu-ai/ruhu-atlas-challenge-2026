"""KPI API response schemas. Define what the server returns to clients.

Response schemas can be richer than domain models:
- Include computed fields (progress_pct, trend)
- Include related data (organization_name)
- Omit internal fields (primary keys if unneeded by client)
"""

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, computed_field


class GoalResponse(BaseModel):
    """A KPI goal as returned by the API. Includes computed fields."""

    definition_id: str = Field(description="Unique goal ID.")
    organization_id: str = Field(description="Organization that owns this goal.")
    kind: str = Field(description="Goal scope.")
    name: str = Field(description="Goal name.")
    description: Optional[str] = Field(default=None, description="Goal description.")
    metric_key: str = Field(description="Metric path.")
    metric_direction: str = Field(description="higher_is_better or lower_is_better.")
    metric_unit: str = Field(description="Unit: percent, score_100, seconds, usd.")
    target_value: float = Field(description="Target value.")
    baseline_value: Optional[float] = Field(default=None, description="Baseline value.")
    status: str = Field(description="draft, active, paused, completed, archived.")
    tags: list[str] = Field(default_factory=list, description="Tags.")
    created_at: datetime = Field(description="Creation timestamp.")
    updated_at: datetime = Field(description="Last update timestamp.")
    created_by: Optional[str] = Field(default=None, description="Creator user ID.")
    updated_by: Optional[str] = Field(default=None, description="Last updater user ID.")

    # Computed fields from read model (optional; populated from projection)
    current_value: Optional[float] = Field(
        default=None,
        description="Latest observed value.",
    )
    progress_pct: Optional[float] = Field(
        default=None,
        description="Progress toward target as percentage.",
    )
    trend: Optional[str] = Field(
        default=None,
        description="Trend direction: up, down, flat, unknown.",
    )
    confidence: Optional[float] = Field(
        default=None,
        description="Confidence in latest observation (0-1).",
    )
    last_observed_at: Optional[datetime] = Field(
        default=None,
        description="When we last measured this goal.",
    )

    @computed_field
    @property
    def is_on_track(self) -> bool:
        """Is this goal on track?"""
        if self.current_value is None or self.progress_pct is None:
            return False

        if self.metric_direction == "higher_is_better":
            return self.current_value >= self.target_value * 0.9
        else:
            return self.current_value <= self.target_value * 1.1

    @computed_field
    @property
    def days_active(self) -> Optional[int]:
        """How many days has this goal been active?"""
        if self.status == "draft":
            return None
        return (datetime.now(timezone.utc) - self.created_at).days


class GoalListResponse(BaseModel):
    """Paginated list of goals."""

    goals: list[GoalResponse] = Field(description="List of goals.")
    total: int = Field(description="Total number of goals (all pages).")
    page: int = Field(description="Current page number (1-indexed).")
    per_page: int = Field(description="Items per page.")
    has_more: bool = Field(default=False, description="Are there more pages?")


class GoalExecutionResponse(BaseModel):
    """A recorded observation for a goal."""

    execution_id: str = Field(description="Unique execution ID.")
    definition_id: str = Field(description="Goal ID.")
    organization_id: str = Field(description="Organization ID.")
    observation_kind: str = Field(description="Type of observation.")
    observed_value: float = Field(description="Measured value.")
    confidence: float = Field(description="Measurement confidence (0-1).")
    observed_at: datetime = Field(description="When observed.")
    created_at: datetime = Field(description="When recorded.")
    metadata_json: dict = Field(default_factory=dict, description="Additional context.")


class ObservationListResponse(BaseModel):
    """Paginated list of observations for a goal."""

    observations: list[GoalExecutionResponse] = Field(description="List of observations.")
    total: int = Field(description="Total observations.")
    page: int = Field(description="Current page.")
    per_page: int = Field(description="Items per page.")
    has_more: bool = Field(default=False, description="More pages?")
