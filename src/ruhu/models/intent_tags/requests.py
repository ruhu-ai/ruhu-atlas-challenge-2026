"""Intent Tags API request schemas. Define what clients send to the API."""

from typing import Optional
from pydantic import BaseModel, Field, field_validator


class CreateTaxonomyRequest(BaseModel):
    """Create a new intent taxonomy version."""

    name: str = Field(
        min_length=1,
        max_length=255,
        description="Taxonomy name.",
    )
    notes: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Version notes.",
    )


class UpdateTaxonomyRequest(BaseModel):
    """Update a taxonomy version (patch semantics)."""

    name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="New taxonomy name.",
    )
    status: Optional[str] = Field(
        default=None,
        pattern="^(draft|published|archived)$",
        description="New status.",
    )
    notes: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Updated notes.",
    )


class CreateIntentRequest(BaseModel):
    """Create a new intent definition."""

    name: str = Field(
        min_length=1,
        max_length=100,
        description="Machine-readable intent name.",
    )
    display_name: str = Field(
        min_length=1,
        max_length=255,
        description="Human-readable name.",
    )
    description: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Intent description.",
    )
    category: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Logical category.",
    )
    example_phrases: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Example phrases.",
    )
    confidence_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Confidence threshold (0-1).",
    )
    priority: int = Field(
        default=0,
        description="Intent priority.",
    )
    taxonomy_version_id: Optional[str] = Field(
        default=None,
        description="Taxonomy version ID (optional).",
    )
    color: Optional[str] = Field(
        default=None,
        max_length=16,
        description="UI color (hex).",
    )
    icon: Optional[str] = Field(
        default=None,
        max_length=64,
        description="UI icon.",
    )

    @field_validator("name")
    @classmethod
    def validate_name_format(cls, v: str) -> str:
        """Intent name must be alphanumeric with underscores."""
        if not v or not all(c.isalnum() or c == "_" for c in v):
            raise ValueError("name must be alphanumeric with underscores")
        return v


class UpdateIntentRequest(BaseModel):
    """Update an intent definition (patch semantics)."""

    name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="New name.",
    )
    display_name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="New display name.",
    )
    description: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="New description.",
    )
    category: Optional[str] = Field(
        default=None,
        max_length=100,
        description="New category.",
    )
    example_phrases: Optional[list[str]] = Field(
        default=None,
        max_length=20,
        description="New example phrases.",
    )
    confidence_threshold: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="New threshold.",
    )
    priority: Optional[int] = Field(
        default=None,
        description="New priority.",
    )
    status: Optional[str] = Field(
        default=None,
        pattern="^(draft|active|deprecated|archived)$",
        description="New status.",
    )
    color: Optional[str] = Field(
        default=None,
        max_length=16,
        description="New color.",
    )
    icon: Optional[str] = Field(
        default=None,
        max_length=64,
        description="New icon.",
    )
