from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft7Validator, SchemaError

from .specs import ToolSpec

VALID_PROPERTY_TYPES = {"string", "integer", "number", "boolean", "array", "object", "null"}
_SCHEMA_COMBINATORS = ("allOf", "anyOf", "oneOf")


@dataclass
class ValidationIssue:
    field: str
    message: str


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.issues


class ToolValidationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        super().__init__("tool validation failed")


class ToolSpecValidator:
    def validate_spec(
        self,
        spec: ToolSpec,
        *,
        require_aci_fields: bool = False,
    ) -> ValidationReport:
        issues: list[ValidationIssue] = []
        issues.extend(
            self.validate_schema(
                spec.input_schema,
                field_root="input_schema",
                require_parameter_descriptions=True,
            ).issues
        )
        issues.extend(
            self.validate_schema(
                spec.output_schema,
                field_root="output_schema",
                require_parameter_descriptions=False,
            ).issues
        )
        if require_aci_fields:
            if not spec.purpose:
                issues.append(
                    ValidationIssue(
                        field="purpose",
                        message="is required for Anthropic-aligned ACI quality",
                    )
                )
            if not spec.when_to_use:
                issues.append(
                    ValidationIssue(
                        field="when_to_use",
                        message="must include at least one usage guideline",
                    )
                )
            if not spec.when_not_to_use:
                issues.append(
                    ValidationIssue(
                        field="when_not_to_use",
                        message="must include at least one misuse boundary",
                    )
                )
            if not spec.input_examples:
                issues.append(
                    ValidationIssue(
                        field="input_examples",
                        message="must include at least one concrete example",
                    )
                )
            if not spec.failure_modes:
                issues.append(
                    ValidationIssue(
                        field="failure_modes",
                        message="must declare at least one expected failure mode",
                    )
                )
        return ValidationReport(issues)

    def validate_schema(
        self,
        schema: dict[str, Any],
        *,
        field_root: str,
        require_parameter_descriptions: bool,
    ) -> ValidationReport:
        issues: list[ValidationIssue] = []

        if not isinstance(schema, dict):
            return ValidationReport(
                [ValidationIssue(field=field_root, message="must be an object")]
            )

        try:
            Draft7Validator.check_schema(schema)
        except SchemaError as exc:
            issues.append(
                ValidationIssue(
                    field=field_root,
                    message=f"invalid JSON schema: {exc.message}",
                )
            )
            return ValidationReport(issues)

        if schema.get("type") != "object":
            issues.append(
                ValidationIssue(
                    field=f"{field_root}.type",
                    message="must be 'object'",
                )
            )

        self._validate_object_schema(
            schema,
            field_root=field_root,
            issues=issues,
            require_parameter_descriptions=require_parameter_descriptions,
        )
        return ValidationReport(issues)

    def _validate_object_schema(
        self,
        schema: dict[str, Any],
        *,
        field_root: str,
        issues: list[ValidationIssue],
        require_parameter_descriptions: bool,
    ) -> None:
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        if properties is not None and not isinstance(properties, dict):
            issues.append(
                ValidationIssue(
                    field=f"{field_root}.properties",
                    message="must be an object",
                )
            )
            return

        if required is not None and not isinstance(required, list):
            issues.append(
                ValidationIssue(
                    field=f"{field_root}.required",
                    message="must be an array",
                )
            )
            required = []

        if isinstance(required, list):
            for name in required:
                if not isinstance(name, str):
                    issues.append(
                        ValidationIssue(
                            field=f"{field_root}.required",
                            message="must contain only strings",
                        )
                    )
                    continue
                if isinstance(properties, dict) and name not in properties:
                    issues.append(
                        ValidationIssue(
                            field=f"{field_root}.required",
                            message=f"required field {name!r} not present in properties",
                        )
                    )

        if not isinstance(properties, dict):
            return

        for name, prop in properties.items():
            prop_field = f"{field_root}.properties.{name}"
            if not isinstance(prop, dict):
                issues.append(
                    ValidationIssue(field=prop_field, message="must be an object")
                )
                continue
            self._validate_property_schema(
                prop,
                field_root=prop_field,
                issues=issues,
                require_description=require_parameter_descriptions,
            )

    def _validate_property_schema(
        self,
        schema: dict[str, Any],
        *,
        field_root: str,
        issues: list[ValidationIssue],
        require_description: bool,
    ) -> None:
        prop_type = schema.get("type")
        has_combinator = any(key in schema for key in _SCHEMA_COMBINATORS)

        if isinstance(prop_type, list):
            unsupported = [
                value for value in prop_type if not isinstance(value, str) or value not in VALID_PROPERTY_TYPES
            ]
            if unsupported:
                issues.append(
                    ValidationIssue(
                        field=f"{field_root}.type",
                        message=f"unsupported types: {unsupported!r}",
                    )
                )
        elif prop_type is not None and prop_type not in VALID_PROPERTY_TYPES:
            issues.append(
                ValidationIssue(
                    field=f"{field_root}.type",
                    message=f"unsupported type: {prop_type}",
                )
            )
        elif prop_type is None and not has_combinator:
            issues.append(
                ValidationIssue(
                    field=f"{field_root}.type",
                    message="must declare a type or use allOf/anyOf/oneOf",
                )
            )

        if require_description:
            description = str(schema.get("description") or "").strip()
            if len(description) < 10:
                issues.append(
                    ValidationIssue(
                        field=f"{field_root}.description",
                        message="must be at least 10 characters",
                    )
                )

        normalized_types = (
            set(prop_type)
            if isinstance(prop_type, list)
            else ({prop_type} if isinstance(prop_type, str) else set())
        )

        if "object" in normalized_types or "properties" in schema:
            self._validate_object_schema(
                schema,
                field_root=field_root,
                issues=issues,
                require_parameter_descriptions=require_description,
            )

        if "array" in normalized_types or "items" in schema:
            items = schema.get("items")
            if items is None:
                issues.append(
                    ValidationIssue(
                        field=f"{field_root}.items",
                        message="array schemas must declare items",
                    )
                )
            elif isinstance(items, dict):
                self._validate_property_schema(
                    items,
                    field_root=f"{field_root}.items",
                    issues=issues,
                    require_description=False,
                )
            elif isinstance(items, list):
                for index, child in enumerate(items):
                    if not isinstance(child, dict):
                        issues.append(
                            ValidationIssue(
                                field=f"{field_root}.items[{index}]",
                                message="must be an object",
                            )
                        )
                        continue
                    self._validate_property_schema(
                        child,
                        field_root=f"{field_root}.items[{index}]",
                        issues=issues,
                        require_description=False,
                    )
            else:
                issues.append(
                    ValidationIssue(
                        field=f"{field_root}.items",
                        message="must be an object or array",
                    )
                )

        for combinator in _SCHEMA_COMBINATORS:
            children = schema.get(combinator)
            if children is None:
                continue
            if not isinstance(children, list) or not children:
                issues.append(
                    ValidationIssue(
                        field=f"{field_root}.{combinator}",
                        message="must be a non-empty array",
                    )
                )
                continue
            for index, child in enumerate(children):
                if not isinstance(child, dict):
                    issues.append(
                        ValidationIssue(
                            field=f"{field_root}.{combinator}[{index}]",
                            message="must be an object",
                        )
                    )
                    continue
                self._validate_property_schema(
                    child,
                    field_root=f"{field_root}.{combinator}[{index}]",
                    issues=issues,
                    require_description=False,
                )


def validate_tool_args(spec: ToolSpec, args: dict[str, Any]) -> None:
    issues: list[ValidationIssue] = []
    schema = spec.input_schema
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    additional_allowed = bool(schema.get("additionalProperties", False))

    for name in required:
        if name not in args:
            issues.append(ValidationIssue(field=name, message="missing required field"))

    if not additional_allowed:
        unknown = sorted(set(args) - set(properties))
        for name in unknown:
            issues.append(ValidationIssue(field=name, message="field is not allowed by schema"))

    for name, value in args.items():
        prop = properties.get(name)
        if not isinstance(prop, dict):
            continue
        prop_type = prop.get("type")
        if prop_type == "string" and not isinstance(value, str):
            issues.append(ValidationIssue(field=name, message="expected string"))
        elif prop_type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            issues.append(ValidationIssue(field=name, message="expected integer"))
        elif prop_type == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            issues.append(ValidationIssue(field=name, message="expected number"))
        elif prop_type == "boolean" and not isinstance(value, bool):
            issues.append(ValidationIssue(field=name, message="expected boolean"))
        elif prop_type == "array" and not isinstance(value, list):
            issues.append(ValidationIssue(field=name, message="expected array"))
        elif prop_type == "object" and not isinstance(value, dict):
            issues.append(ValidationIssue(field=name, message="expected object"))

        enum = prop.get("enum")
        if enum is not None and value not in enum:
            issues.append(ValidationIssue(field=name, message=f"value must be one of {enum!r}"))

    if issues:
        raise ToolValidationError(issues)
