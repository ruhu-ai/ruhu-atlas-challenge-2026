"""Schema Registry: Runtime management of tool specifications.

The registry stores and retrieves tool specifications (JSON schemas for I/O).
Enables:
  - Tool discovery
  - Dynamic tool loading
  - Schema versioning
  - Runtime validation against schemas

Models:
  - ToolSpecRegistry: Persistent storage of tool specs
  - SchemaRegistry: In-memory cache with versioning
"""

from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field, Column, JSON


def utcnow() -> datetime:
    """Current UTC time."""
    return datetime.now(timezone.utc)


class ToolSpecRegistry(SQLModel, table=True):
    """Persistent tool specification registry.

    Stores tool specifications (input/output schemas) for runtime lookup.
    Enables dynamic tool discovery without hardcoding.
    """

    __tablename__ = "tool_spec_registry"

    # Primary key
    tool_spec_id: str = Field(primary_key=True)

    # Tool identity
    tool_name: str = Field(index=True)  # e.g., "knowledge.lookup"
    tool_category: str = Field(index=True)  # e.g., "knowledge", "http", "mcp"
    version: int = Field(default=1)  # Schema version

    # Organization scoping (optional)
    organization_id: Optional[str] = Field(default=None, index=True)  # None = global

    # Specification (JSON schemas)
    input_schema: dict = Field(sa_column=Column(JSON), description="JSON schema for input validation")
    output_schema: dict = Field(sa_column=Column(JSON), description="JSON schema for output validation")

    # Metadata
    description: Optional[str] = None
    usage_examples: list[dict] = Field(default_factory=list, sa_column=Column(JSON))

    # Validation mode
    input_validation_mode: str = Field(
        default="strict",
        description="'strict' (fail on mismatch) or 'lenient' (warn on mismatch)",
    )
    output_validation_mode: str = Field(
        default="lenient",
        description="'strict' (fail on mismatch) or 'lenient' (warn on mismatch)",
    )

    # Status
    is_active: bool = Field(default=True, index=True)
    is_deprecated: bool = Field(default=False)
    deprecated_at: Optional[datetime] = None
    replacement_tool_name: Optional[str] = None

    # Audit
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class SchemaRegistry:
    """In-memory schema registry with caching and versioning.

    Provides fast lookup of tool specifications without database round-trips.
    Thread-safe for concurrent access.
    """

    def __init__(self):
        self._schemas: dict[str, ToolSpecRegistry] = {}
        self._by_name: dict[str, list[ToolSpecRegistry]] = {}

    def register(self, spec: ToolSpecRegistry) -> None:
        """Register a tool specification."""
        key = f"{spec.tool_name}:v{spec.version}"

        # Store by ID
        self._schemas[spec.tool_spec_id] = spec

        # Index by name for fast lookup
        if spec.tool_name not in self._by_name:
            self._by_name[spec.tool_name] = []
        self._by_name[spec.tool_name].append(spec)

        # Sort by version (descending)
        self._by_name[spec.tool_name].sort(key=lambda s: s.version, reverse=True)

    def get_latest(self, tool_name: str, organization_id: Optional[str] = None) -> Optional[ToolSpecRegistry]:
        """Get latest version of a tool spec.

        Tries organization-specific first, falls back to global.
        """
        specs = self._by_name.get(tool_name, [])

        if not specs:
            return None

        # Prefer org-specific
        if organization_id:
            for spec in specs:
                if spec.organization_id == organization_id and spec.is_active:
                    return spec

        # Fall back to global
        for spec in specs:
            if spec.organization_id is None and spec.is_active:
                return spec

        return None

    def get_version(self, tool_name: str, version: int) -> Optional[ToolSpecRegistry]:
        """Get specific version of a tool spec."""
        specs = self._by_name.get(tool_name, [])
        for spec in specs:
            if spec.version == version and spec.is_active:
                return spec
        return None

    def list_specs(self, category: Optional[str] = None) -> list[ToolSpecRegistry]:
        """List all registered specs, optionally filtered by category."""
        specs = list(self._schemas.values())

        if category:
            specs = [s for s in specs if s.tool_category == category]

        # Sort by name and version
        specs.sort(key=lambda s: (s.tool_name, -s.version))
        return specs

    def invalidate(self) -> None:
        """Clear the cache (e.g., after database refresh)."""
        self._schemas.clear()
        self._by_name.clear()


# Singleton instance
_registry: Optional[SchemaRegistry] = None


def get_schema_registry() -> SchemaRegistry:
    """Get the global schema registry instance."""
    global _registry
    if _registry is None:
        _registry = SchemaRegistry()
    return _registry


def set_schema_registry(registry: SchemaRegistry) -> None:
    """Set the global schema registry instance (for testing)."""
    global _registry
    _registry = registry
