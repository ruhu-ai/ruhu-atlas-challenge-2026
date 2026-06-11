"""Intent Tags Read Models (Projections).

Denormalized views optimized for analytics and fast queries.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Index
from sqlmodel import SQLModel, Field, Column, JSON


def utcnow() -> datetime:
    """Current UTC time."""
    return datetime.now(timezone.utc)


class IntentAnalyticsProjection(SQLModel, table=True):
    """Read model: Intent usage and performance analytics."""

    __tablename__ = "intent_tag_analytics"
    __table_args__ = (
        Index("ix_intent_tag_analytics_org_updated", "organization_id", "updated_at"),
        Index("ix_intent_tag_analytics_active", "organization_id", "is_active"),
    )

    intent_definition_id: str = Field(primary_key=True)
    organization_id: str = Field(index=True)
    taxonomy_version_id: Optional[str] = None

    # Identity
    name: str
    display_name: str

    # Usage metrics
    usage_count: int = 0  # Total conversations tagged
    last_used_at: Optional[datetime] = None

    # Performance
    avg_confidence: Optional[float] = None  # Average confidence when predicted
    prediction_count: int = 0  # Times this intent was predicted

    # Status
    is_active: bool = True
    is_deprecated: bool = False

    custom_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON))

    event_version: int = 0
    updated_at: datetime = Field(default_factory=utcnow)


class TaxonomyAnalyticsProjection(SQLModel, table=True):
    """Read model: Taxonomy performance and coverage."""

    __tablename__ = "taxonomy_analytics"

    taxonomy_version_id: str = Field(primary_key=True)
    organization_id: str = Field(index=True)

    # Identity
    name: str
    status: str

    # Coverage metrics
    total_intents: int = 0
    active_intents: int = 0
    deprecated_intents: int = 0

    # Usage
    total_conversations_tagged: int = 0
    coverage_pct: Optional[float] = None  # % of conversations tagged

    custom_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON))

    updated_at: datetime = Field(default_factory=utcnow)
