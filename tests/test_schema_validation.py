"""Test schema validation layer.

Tests for:
- JSON schema validation (strict, no coercion)
- ToolSpec input/output validation
- Integration with tool runtime
"""

import pytest
from ruhu.validation.schema import JsonSchemaValidator, ValidationError
from ruhu.tools.specs import ToolSpec, ToolAnnotations


class TestJsonSchemaValidator:
    """Test the JSON schema validator."""

    def test_valid_data(self):
        """Valid data passes validation."""
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        }
        validator = JsonSchemaValidator(schema)
        result = validator.validate({"name": "Alice"})
        assert result == {"name": "Alice"}

    def test_missing_required_field(self):
        """Missing required field raises ValidationError."""
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        validator = JsonSchemaValidator(schema)
        with pytest.raises(ValidationError) as exc_info:
            validator.validate({})

        assert "name" in str(exc_info.value).lower()

    def test_wrong_type(self):
        """Wrong type raises ValidationError."""
        schema = {
            "type": "object",
            "properties": {"age": {"type": "integer"}},
            "required": ["age"],
        }
        validator = JsonSchemaValidator(schema)
        with pytest.raises(ValidationError) as exc_info:
            validator.validate({"age": "not a number"})

        assert "integer" in str(exc_info.value).lower() or "type" in str(exc_info.value).lower()

    def test_additional_properties_not_allowed(self):
        """Extra properties cause error when additionalProperties is false."""
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        }
        validator = JsonSchemaValidator(schema)
        with pytest.raises(ValidationError):
            validator.validate({"name": "Alice", "extra": "field"})

    def test_additional_properties_allowed(self):
        """Extra properties pass when additionalProperties is true."""
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": True,
        }
        validator = JsonSchemaValidator(schema)
        result = validator.validate({"name": "Alice", "extra": "field"})
        assert result == {"name": "Alice", "extra": "field"}

    def test_no_type_coercion(self):
        """Validator does NOT coerce types."""
        schema = {
            "type": "object",
            "properties": {"age": {"type": "integer"}},
            "required": ["age"],
        }
        validator = JsonSchemaValidator(schema)

        # String "42" should NOT be coerced to integer
        with pytest.raises(ValidationError):
            validator.validate({"age": "42"})

    def test_error_context(self):
        """ValidationError includes helpful context."""
        schema = {
            "type": "object",
            "properties": {"user": {"type": "object", "properties": {"name": {"type": "string"}}}},
            "required": ["user"],
        }
        validator = JsonSchemaValidator(schema)

        with pytest.raises(ValidationError) as exc_info:
            validator.validate({"user": {"name": 123}})

        error = exc_info.value
        assert error.message  # Has error message
        assert error.value == 123  # Shows what value failed


class TestToolSpecValidation:
    """Test ToolSpec validation methods."""

    @pytest.fixture
    def tool_spec(self):
        """A sample tool spec."""
        return ToolSpec(
            ref="test.echo",
            kind="builtin",
            display_name="Echo",
            description="Echo back the input message.",
            annotations=ToolAnnotations(read_only=True),
            input_schema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1000,
                    },
                },
                "required": ["message"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "echoed": {
                        "type": "string",
                    },
                },
                "required": ["echoed"],
                "additionalProperties": False,
            },
        )

    def test_valid_input(self, tool_spec):
        """Valid input passes validation."""
        result = tool_spec.validate_input({"message": "hello"})
        assert result == {"message": "hello"}

    def test_invalid_input_type(self, tool_spec):
        """Invalid input type raises error."""
        with pytest.raises(Exception):  # ValidationError or subclass
            tool_spec.validate_input({"message": 123})

    def test_invalid_input_missing_required(self, tool_spec):
        """Missing required field raises error."""
        with pytest.raises(Exception):
            tool_spec.validate_input({})

    def test_valid_output(self, tool_spec):
        """Valid output passes validation."""
        result = tool_spec.validate_output({"echoed": "hello"})
        assert result == {"echoed": "hello"}

    def test_invalid_output_type(self, tool_spec, caplog):
        """Invalid output type logs warning but doesn't raise."""
        # Output validation is lenient; it logs but doesn't fail
        result = tool_spec.validate_output({"echoed": 123})
        assert result == {"echoed": 123}  # Returns unchanged
        # Should have logged a warning
        assert "output validation warning" in caplog.text.lower()

    def test_missing_output_field(self, tool_spec, caplog):
        """Missing output field logs warning but doesn't raise."""
        result = tool_spec.validate_output({})
        assert result == {}  # Returns unchanged
        # Should have logged a warning
        assert "output validation warning" in caplog.text.lower()

    def test_strict_output_validation_raises(self, tool_spec):
        strict_spec = tool_spec.model_copy(update={"output_validation_mode": "strict"})

        with pytest.raises(ValidationError):
            strict_spec.validate_output({})

    def test_validator_caching(self, tool_spec):
        """Validators are created once and cached."""
        # First call creates validator
        result1 = tool_spec.validate_input({"message": "test1"})
        validator1 = tool_spec._input_validator

        # Second call reuses cached validator
        result2 = tool_spec.validate_input({"message": "test2"})
        validator2 = tool_spec._input_validator

        assert validator1 is validator2  # Same object (cached)

    def test_input_examples_must_match_schema(self):
        with pytest.raises(ValueError, match="does not match input_schema"):
            ToolSpec(
                ref="test.echo_examples",
                kind="builtin",
                display_name="Echo",
                description="Echo back the input message with example validation.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                        },
                    },
                    "required": ["message"],
                    "additionalProperties": False,
                },
                input_examples=[
                    {
                        "name": "invalid_example",
                        "description": "This example is intentionally invalid.",
                        "args": {"message": 123},
                    }
                ],
            )


class TestToolSpecIntegration:
    """Integration tests with multiple tool specs."""

    def test_multiple_specs(self):
        """Multiple tool specs can coexist."""
        spec1 = ToolSpec(
            ref="tool.first",
            kind="builtin",
            display_name="First Tool",
            description="This is the first tool.",
            input_schema={
                "type": "object",
                "properties": {"input": {"type": "string"}},
                "required": ["input"],
                "additionalProperties": False,
            },
        )

        spec2 = ToolSpec(
            ref="tool.second",
            kind="builtin",
            display_name="Second Tool",
            description="This is the second tool.",
            input_schema={
                "type": "object",
                "properties": {"count": {"type": "integer"}},
                "required": ["count"],
                "additionalProperties": False,
            },
        )

        # Both should validate independently
        result1 = spec1.validate_input({"input": "hello"})
        assert result1 == {"input": "hello"}

        result2 = spec2.validate_input({"count": 42})
        assert result2 == {"count": 42}

        # Cross-validation should fail
        with pytest.raises(Exception):
            spec1.validate_input({"count": 42})

        with pytest.raises(Exception):
            spec2.validate_input({"input": "hello"})


class TestSchemaEdgeCases:
    """Edge cases and corner cases."""

    def test_nested_objects(self):
        """Validates nested object structures."""
        schema = {
            "type": "object",
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "age": {"type": "integer"},
                    },
                    "required": ["name"],
                }
            },
            "required": ["user"],
        }
        validator = JsonSchemaValidator(schema)

        # Valid nested object
        result = validator.validate({"user": {"name": "Alice", "age": 30}})
        assert result["user"]["name"] == "Alice"

        # Invalid nested object
        with pytest.raises(ValidationError):
            validator.validate({"user": {"age": 30}})  # Missing 'name'

    def test_arrays(self):
        """Validates array schemas."""
        schema = {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                }
            },
            "required": ["tags"],
        }
        validator = JsonSchemaValidator(schema)

        # Valid array
        result = validator.validate({"tags": ["python", "testing"]})
        assert result == {"tags": ["python", "testing"]}

        # Empty array (too small)
        with pytest.raises(ValidationError):
            validator.validate({"tags": []})

        # Wrong item type
        with pytest.raises(ValidationError):
            validator.validate({"tags": ["python", 42]})

    def test_enum_validation(self):
        """Validates enum-like constraints."""
        schema = {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["active", "inactive", "pending"],
                }
            },
            "required": ["status"],
        }
        validator = JsonSchemaValidator(schema)

        # Valid enum value
        result = validator.validate({"status": "active"})
        assert result == {"status": "active"}

        # Invalid enum value
        with pytest.raises(ValidationError):
            validator.validate({"status": "unknown"})

    def test_numeric_constraints(self):
        """Validates min/max constraints."""
        schema = {
            "type": "object",
            "properties": {
                "age": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 150,
                }
            },
            "required": ["age"],
        }
        validator = JsonSchemaValidator(schema)

        # Valid values
        for age in [0, 25, 150]:
            result = validator.validate({"age": age})
            assert result == {"age": age}

        # Invalid values
        with pytest.raises(ValidationError):
            validator.validate({"age": -1})

        with pytest.raises(ValidationError):
            validator.validate({"age": 200})
