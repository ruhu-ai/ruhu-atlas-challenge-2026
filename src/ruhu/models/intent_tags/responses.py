"""Intent Tags API response schemas. Define what the server returns to clients."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, computed_field


class TaxonomyResponse(BaseModel):
    """A taxonomy version as returned by the API."""

    taxonomy_version_id: str = Field(description="Unique taxonomy ID.")
    organization_id: str = Field(description="Organization ID.")
    name: str = Field(description="Taxonomy name.")
    status: str = Field(description="draft, published, or archived.")
    notes: Optional[str] = Field(default=None, description="Version notes.")
    published_at: Optional[datetime] = Field(default=None, description="Publish timestamp.")
    created_at: datetime = Field(description="Creation timestamp.")
    updated_at: datetime = Field(description="Last update timestamp.")

    # Computed fields from read model (optional)
    intent_count: Optional[int] = Field(
        default=None,
        description="Number of intents in this taxonomy.",
    )
    active_intent_count: Optional[int] = Field(
        default=None,
        description="Number of active intents.",
    )

    @computed_field
    @property
    def is_published(self) -> bool:
        """Is this taxonomy published?"""
        return self.status == "published"


class IntentResponse(BaseModel):
    """An intent definition as returned by the API."""

    intent_definition_id: str = Field(description="Unique intent ID.")
    organization_id: str = Field(description="Organization ID.")
    taxonomy_version_id: Optional[str] = Field(default=None, description="Taxonomy ID.")
    name: str = Field(description="Intent name.")
    display_name: str = Field(description="Display name.")
    description: Optional[str] = Field(default=None, description="Intent description.")
    category: Optional[str] = Field(default=None, description="Logical category.")
    example_phrases: list[str] = Field(default_factory=list, description="Example phrases.")
    confidence_threshold: float = Field(description="Confidence threshold (0-1).")
    priority: int = Field(description="Intent priority.")
    status: str = Field(description="draft, active, deprecated, or archived.")
    is_deprecated: bool = Field(description="Is this intent deprecated?")
    color: Optional[str] = Field(default=None, description="UI color (hex).")
    icon: Optional[str] = Field(default=None, description="UI icon.")
    created_at: datetime = Field(description="Creation timestamp.")
    updated_at: datetime = Field(description="Last update timestamp.")

    # Computed fields from read model (optional)
    usage_count: Optional[int] = Field(
        default=None,
        description="How many conversations tagged with this intent.",
    )
    last_used_at: Optional[datetime] = Field(
        default=None,
        description="Last time a conversation was tagged with this intent.",
    )

    @computed_field
    @property
    def is_active(self) -> bool:
        """Is this intent active and not deprecated?"""
        return self.status == "active" and not self.is_deprecated


class TaxonomyListResponse(BaseModel):
    """Paginated list of taxonomies."""

    taxonomies: list[TaxonomyResponse] = Field(description="List of taxonomies.")
    total: int = Field(description="Total count.")
    page: int = Field(description="Current page (1-indexed).")
    per_page: int = Field(description="Items per page.")
    has_more: bool = Field(default=False, description="More pages?")


class IntentListResponse(BaseModel):
    """Paginated list of intents."""

    intents: list[IntentResponse] = Field(description="List of intents.")
    total: int = Field(description="Total count.")
    page: int = Field(description="Current page.")
    per_page: int = Field(description="Items per page.")
    has_more: bool = Field(default=False, description="More pages?")
