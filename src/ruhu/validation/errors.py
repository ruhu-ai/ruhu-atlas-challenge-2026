"""Structured validation error responses for APIs."""

from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field


class FieldError(BaseModel):
    """Error on a single field."""

    field: str  # JSONPath, e.g., "root.goal.target_value"
    message: str
    type: str  # "type_error" | "value_error" | "constraint_error"
    value: Any | None = None  # What value caused the error


class ValidationErrorResponse(BaseModel):
    """Structured response for validation errors."""

    error: str = "validation_error"
    message: str | None = None
    fields: list[FieldError] = Field(default_factory=list)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    request_id: str | None = None  # Correlation ID for debugging


class SchemaValidationError(BaseModel):
    """Error when a tool schema validation fails."""

    tool_ref: str
    invocation_id: str
    error_type: str  # "input_validation" | "output_validation"
    message: str
    path: str | None = None  # Where in the schema did it fail
