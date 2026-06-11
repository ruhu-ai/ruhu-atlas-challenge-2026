"""Intent Tags domain models. Business logic independent of persistence.

These models represent intent classification concepts as they exist in the kernel.
An intent is a semantic bucket for user messages (e.g., "billing_inquiry", "password_reset").
Intents are organized into taxonomies and tagged on conversations.
"""

from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    """Current UTC time."""
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────
# Intent-specific enums
# ─────────────────────────────────────────────────────────────────────

IntentStatus = Literal["draft", "active", "deprecated", "archived"]
TaxonomyStatus = Literal["draft", "published", "archived"]


# ─────────────────────────────────────────────────────────────────────
# Taxonomy Definition
# ─────────────────────────────────────────────────────────────────────


class TaxonomyVersion(BaseModel):
    """A taxonomy version that groups related intents.

    Taxonomies evolve over time. Each version is immutable once published.
    """

    taxonomy_version_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique identifier for this taxonomy version.",
    )
    organization_id: str = Field(
        description="Organization that owns this taxonomy.",
    )
    name: str = Field(
        min_length=1,
        max_length=255,
        description="Human-readable taxonomy name.",
    )
    status: TaxonomyStatus = Field(
        default="draft",
        description="draft, published, or archived.",
    )
    notes: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Version notes or changelog.",
    )
    published_at: Optional[datetime] = Field(
        default=None,
        description="When this taxonomy was published.",
    )
    created_at: datetime = Field(
        default_factory=utcnow,
        description="When created.",
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        description="When last updated.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "taxonomy_version_id": "tax_v1_2024",
                    "organization_id": "org_acme",
                    "name": "Customer Service Intent Taxonomy",
                    "status": "published",
                    "published_at": "2024-01-15T10:30:00Z",
                }
            ]
        }
    )


# ─────────────────────────────────────────────────────────────────────
# Intent Definition
# ─────────────────────────────────────────────────────────────────────


class IntentDefinition(BaseModel):
    """A semantic intent category within a taxonomy.

    Intents are labels for conversation purposes (billing_inquiry, password_reset).
    The LLM classifier predicts which intent applies to user messages.
    """

    intent_definition_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique identifier for this intent.",
    )
    organization_id: str = Field(
        description="Organization that owns this intent.",
    )
    taxonomy_version_id: Optional[str] = Field(
        default=None,
        description="Taxonomy version this intent belongs to (optional: intent can exist standalone).",
    )
    name: str = Field(
        min_length=1,
        max_length=100,
        description="Machine-readable intent name (e.g., 'billing_inquiry').",
    )
    display_name: str = Field(
        min_length=1,
        max_length=255,
        description="Human-readable display name.",
    )
    description: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="What does this intent represent?",
    )
    category: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Logical grouping (e.g., 'billing', 'support').",
    )
    example_phrases: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Example user phrases that should trigger this intent.",
    )
    confidence_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Minimum confidence (0-1) to apply this intent.",
    )
    priority: int = Field(
        default=0,
        description="Higher priority intents take precedence when multiple match.",
    )
    status: IntentStatus = Field(
        default="draft",
        description="draft, active, deprecated, or archived.",
    )
    is_deprecated: bool = Field(
        default=False,
        description="If true, this intent is no longer used.",
    )
    color: Optional[str] = Field(
        default=None,
        max_length=16,
        description="UI display color (hex code).",
    )
    icon: Optional[str] = Field(
        default=None,
        max_length=64,
        description="UI icon identifier.",
    )
    metadata_json: dict = Field(
        default_factory=dict,
        description="Custom metadata.",
    )
    created_at: datetime = Field(
        default_factory=utcnow,
        description="When created.",
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        description="When last updated.",
    )

    @field_validator("name")
    @classmethod
    def validate_name_format(cls, v: str) -> str:
        """Intent name must be alphanumeric with underscores."""
        if not v or not all(c.isalnum() or c == "_" for c in v):
            raise ValueError("name must be alphanumeric with underscores (e.g., 'billing_inquiry')")
        return v

    @field_validator("confidence_threshold")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        """Confidence must be between 0 and 1."""
        if not (0.0 <= v <= 1.0):
            raise ValueError("confidence_threshold must be between 0.0 and 1.0")
        return v

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "intent_definition_id": "intent_billing",
                    "organization_id": "org_acme",
                    "taxonomy_version_id": "tax_v1_2024",
                    "name": "billing_inquiry",
                    "display_name": "Billing Inquiry",
                    "description": "User asking about invoices, charges, or billing.",
                    "category": "billing",
                    "example_phrases": [
                        "Why was I charged?",
                        "I don't recognize this charge.",
                        "Can I get an invoice?",
                    ],
                    "confidence_threshold": 0.75,
                    "priority": 1,
                    "status": "active",
                }
            ]
        }
    )


# ─────────────────────────────────────────────────────────────────────
# Domain Events
# ─────────────────────────────────────────────────────────────────────


class TaxonomyVersionCreated(BaseModel):
    """Event: taxonomy version was created."""

    taxonomy_version_id: str
    organization_id: str
    name: str
    timestamp: datetime = Field(default_factory=utcnow)


class IntentDefinitionCreated(BaseModel):
    """Event: intent definition was created."""

    intent_definition_id: str
    organization_id: str
    taxonomy_version_id: Optional[str]
    name: str
    display_name: str
    timestamp: datetime = Field(default_factory=utcnow)


class IntentDefinitionUpdated(BaseModel):
    """Event: intent definition was updated."""

    intent_definition_id: str
    organization_id: str
    changes: dict  # What changed
    timestamp: datetime = Field(default_factory=utcnow)
