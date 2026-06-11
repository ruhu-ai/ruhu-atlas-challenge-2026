"""KPI API request schemas. Define what clients send to the API.

At this boundary (HTTP), coercion is acceptable. Pydantic will convert
string "123" to int 123 if the field expects int.
"""

from typing import Optional
from pydantic import BaseModel, Field, field_validator


class CreateGoalRequest(BaseModel):
    """Create a KPI goal. Client provides: what to measure and target."""

    name: str = Field(
        min_length=1,
        max_length=255,
        description="Goal name.",
    )
    description: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="What does this goal measure?",
    )
    kind: str = Field(
        default="custom",
        pattern="^(organization|agent|workflow|channel|segment|campaign|custom)$",
        description="Scope: organization-wide, per-agent, per-workflow, etc.",
    )
    metric_key: str = Field(
        min_length=1,
        max_length=255,
        description="Dotted metric path. E.g., 'conversation.fcr_score'",
    )
    metric_direction: str = Field(
        pattern="^(higher_is_better|lower_is_better)$",
        description="Optimization direction.",
    )
    metric_unit: str = Field(
        pattern="^(percent|score_100|seconds|usd)$",
        description="Unit of measurement.",
    )
    target_value: float = Field(
        gt=0,
        description="Target value. Must be positive.",
    )
    baseline_value: Optional[float] = Field(
        default=None,
        ge=0,
        description="Baseline for comparison.",
    )
    tags: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Searchable tags.",
    )

    @field_validator("metric_key")
    @classmethod
    def validate_metric_key(cls, v: str) -> str:
        """Metric key must be alphanumeric with dots."""
        if not v or not all(
            part.replace("_", "").isalnum() for part in v.split(".")
        ):
            raise ValueError(
                "metric_key must be alphanumeric identifiers separated by dots"
            )
        return v


class UpdateGoalRequest(BaseModel):
    """Update a KPI goal. All fields optional (patch semantics)."""

    name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="New goal name.",
    )
    description: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="New description.",
    )
    target_value: Optional[float] = Field(
        default=None,
        gt=0,
        description="New target value.",
    )
    baseline_value: Optional[float] = Field(
        default=None,
        ge=0,
        description="New baseline.",
    )
    status: Optional[str] = Field(
        default=None,
        pattern="^(draft|active|paused|completed|archived)$",
        description="New status.",
    )
    tags: Optional[list[str]] = Field(
        default=None,
        max_length=10,
        description="New tags.",
    )


class RecordObservationRequest(BaseModel):
    """Record an observation (measurement) for a goal."""

    observation_kind: str = Field(
        pattern="^(baseline|scheduled_refresh|manual_entry|experiment_readout)$",
        description="Type of observation.",
    )
    observed_value: float = Field(
        description="The measured value.",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence in this measurement (0-1).",
    )
    metadata_json: Optional[dict] = Field(
        default=None,
        description="Additional context.",
    )
