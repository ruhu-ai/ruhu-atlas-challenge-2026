"""JSON schema validation with explicit error handling.

Does NOT coerce types. Request coercion happens at the Pydantic boundary.
This layer is for runtime validation of tool I/O and other contracts.
"""

from typing import Any
from jsonschema import Draft7Validator, ValidationError as JsonSchemaError


class ValidationError(Exception):
    """Unified validation error with context."""

    def __init__(
        self,
        message: str,
        path: str | None = None,
        value: Any = None,
        schema_path: str | None = None,
    ):
        self.message = message
        self.path = path  # JSONPath to failing field, e.g., "root.goal.target_value"
        self.value = value
        self.schema_path = schema_path  # Path in schema that failed

        error_msg = message
        if path:
            error_msg = f"{message} at {path}"
        if value is not None:
            error_msg = f"{error_msg} (got {repr(value)[:50]})"

        super().__init__(error_msg)


class JsonSchemaValidator:
    """Validates dicts against JSON schema.

    Does NOT coerce types. If a tool expects {"age": int} and gets {"age": "42"},
    this raises ValidationError. The client must retry with the correct type.

    This is intentional: we catch type mismatches early, before they cascade
    through the system.
    """

    def __init__(self, schema: dict):
        """Initialize validator.

        Args:
            schema: JSON schema dict. Must be Draft 7 compatible.
        """
        if not isinstance(schema, dict):
            raise TypeError(f"schema must be dict, got {type(schema)}")

        self.schema = schema
        try:
            self.validator = Draft7Validator(schema)
        except Exception as e:
            raise ValueError(f"Invalid JSON schema: {e}")

    def validate(self, data: Any) -> Any:
        """Validate data against schema.

        Args:
            data: Dict-like data to validate.

        Returns:
            The same data (unchanged).

        Raises:
            ValidationError: If validation fails.
        """
        errors = list(self.validator.iter_errors(data))

        if not errors:
            return data

        # Construct a helpful error message from the first error
        first_error = errors[0]
        path = ".".join(str(p) for p in first_error.path) if first_error.path else "root"
        schema_path = ".".join(
            str(p) for p in first_error.schema_path if p != "properties"
        )

        raise ValidationError(
            message=first_error.message,
            path=f"root.{path}" if path != "root" else "root",
            value=first_error.instance,
            schema_path=schema_path,
        )


def validate_json_schema(data: Any, schema: dict) -> Any:
    """Convenience function. Validates data and returns it unchanged."""
    validator = JsonSchemaValidator(schema)
    return validator.validate(data)
