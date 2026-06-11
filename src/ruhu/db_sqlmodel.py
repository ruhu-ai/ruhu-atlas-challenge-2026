"""SQLModel unified models (Pydantic + SQLAlchemy combined).

SQLModel allows a single model definition to work as both a Pydantic model
(for validation) and a SQLAlchemy ORM model (for database mapping).

This file contains the new SQLModel-based models. During migration, models
will move from db_models.py here.

Eventual plan: db_models.py → db_sqlmodel.py consolidation after pilot.
"""

from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field, Column, JSON
from uuid import uuid4


def utcnow() -> datetime:
    """Current UTC time."""
    return datetime.now(timezone.utc)


def _uuid4_str() -> str:
    """Generate a UUID4 string."""
    return str(uuid4())


# ─────────────────────────────────────────────────────────────────────
# Tenant/Organization Mixins
# ─────────────────────────────────────────────────────────────────────


class OptionalTenantMixin:
    """Mixin for models that optionally belong to an organization."""

    organization_id: Optional[str] = Field(default=None, index=True)


class RequiredTenantMixin:
    """Mixin for models that require an organization."""

    organization_id: str = Field(index=True)


# ─────────────────────────────────────────────────────────────────────
# Domain Events (for event sourcing)
# ─────────────────────────────────────────────────────────────────────


class DomainEventBase(SQLModel):
    """Shared fields for DomainEvent."""

    event_type: str  # "GoalDefinitionCreated" | "ConversationEnded" | ...
    aggregate_type: str  # "GoalDefinition" | "Conversation" | ...
    aggregate_id: str  # e.g., definition_id, conversation_id
    causation_id: Optional[str] = Field(default=None, index=True)
    correlation_id: Optional[str] = Field(default=None, index=True)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    timestamp: datetime = Field(index=True)
    version: int  # Schema version of the event


class DomainEvent(DomainEventBase, OptionalTenantMixin, table=True):
    """Domain event. Append-only log for event sourcing."""

    __tablename__ = "domain_events"

    event_id: str = Field(primary_key=True, default_factory=_uuid4_str)
    created_at: datetime = Field(default_factory=utcnow)


# ─────────────────────────────────────────────────────────────────────
# KPI Models (SQLModel)
# ─────────────────────────────────────────────────────────────────────


class GoalDefinitionBase(SQLModel):
    """Shared fields for KPI goal definitions."""

    organization_id: str = Field(index=True)
    kind: str  # "organization" | "agent" | "workflow" | "channel" | "segment" | "campaign" | "custom"
    name: str
    description: Optional[str] = None
    metric_key: str
    metric_direction: str  # "higher_is_better" | "lower_is_better"
    metric_unit: str  # "percent" | "score_100" | "seconds" | "usd"
    target_value: float
    baseline_value: Optional[float] = None
    status: str = Field(default="draft", index=True)  # "draft" | "active" | "paused" | "completed"
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))


class GoalDefinition(GoalDefinitionBase, RequiredTenantMixin, table=True):
    """KPI goal definition. Storage model."""

    __tablename__ = "kpi_goal_definitions"

    definition_id: str = Field(primary_key=True, default_factory=_uuid4_str)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class GoalExecutionBase(SQLModel):
    """Shared fields for goal executions (observations)."""

    definition_id: str = Field(index=True)
    observation_kind: str  # "baseline" | "scheduled_refresh" | "manual_entry" | "experiment_readout"
    observed_value: float
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    observed_at: datetime = Field(index=True)
    metadata_json: dict = Field(default_factory=dict, sa_column=Column(JSON))


class GoalExecution(GoalExecutionBase, RequiredTenantMixin, table=True):
    """Goal execution (observation). When did we measure this goal?"""

    __tablename__ = "kpi_goal_executions"

    execution_id: str = Field(primary_key=True, default_factory=_uuid4_str)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# ─────────────────────────────────────────────────────────────────────
# Intent Tags Models (SQLModel)
# ─────────────────────────────────────────────────────────────────────


class TaxonomyVersionBase(SQLModel):
    """Shared fields for taxonomy versions."""

    name: str
    status: str = Field(default="draft", index=True)  # "draft" | "published" | "archived"
    notes: Optional[str] = None
    published_at: Optional[datetime] = None


class TaxonomyVersion(TaxonomyVersionBase, RequiredTenantMixin, table=True):
    """Intent taxonomy version. Immutable once published."""

    __tablename__ = "intent_tag_taxonomy_versions"

    taxonomy_version_id: str = Field(primary_key=True, default_factory=_uuid4_str)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class IntentDefinitionBase(SQLModel):
    """Shared fields for intent definitions."""

    name: str = Field(index=True)
    display_name: str
    description: Optional[str] = None
    category: Optional[str] = Field(default=None, index=True)
    example_phrases: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    priority: int = Field(default=0)
    status: str = Field(default="draft", index=True)  # "draft" | "active" | "deprecated" | "archived"
    is_deprecated: bool = Field(default=False, index=True)
    color: Optional[str] = None
    icon: Optional[str] = None
    metadata_json: dict = Field(default_factory=dict, sa_column=Column(JSON))


class IntentDefinition(IntentDefinitionBase, RequiredTenantMixin, table=True):
    """Intent definition within a taxonomy."""

    __tablename__ = "intent_definitions"

    intent_definition_id: str = Field(primary_key=True, default_factory=_uuid4_str)
    taxonomy_version_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


# ─────────────────────────────────────────────────────────────────────
# Attachments Models (SQLModel)
# ─────────────────────────────────────────────────────────────────────


class AttachmentBase(SQLModel):
    """Shared fields for attachments."""

    conversation_id: str = Field(index=True)
    filename: str
    file_size_bytes: int
    mime_type: str
    attachment_type: str  # "document" | "image" | "audio" | "video" | "artifact" | "other"
    storage_key: str
    processing_status: str = Field(default="pending", index=True)  # "pending" | "processing" | "completed" | "failed" | "skipped"
    content_type: str = Field(default="binary")  # "text" | "binary" | "structured"
    extracted_text: Optional[str] = None
    extracted_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON))
    processing_error: Optional[str] = None
    uploaded_by: Optional[str] = None
    uploaded_at: datetime = Field(default_factory=utcnow)
    processing_started_at: Optional[datetime] = None
    processing_completed_at: Optional[datetime] = None


class Attachment(AttachmentBase, RequiredTenantMixin, table=True):
    """Attachment (file, image, etc.) associated with a conversation."""

    __tablename__ = "attachments"

    attachment_id: str = Field(primary_key=True, default_factory=_uuid4_str)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


# ─────────────────────────────────────────────────────────────────────
# Note: New SQLModel tables should be added here as they're created
# during development. Existing tables in db_models.py will migrate
# here as part of schema consolidation.
# ─────────────────────────────────────────────────────────────────────
