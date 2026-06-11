from __future__ import annotations

import pytest

from ruhu.tools.specs import ToolSpec
from ruhu.tools.validators import ToolSpecValidator, ToolValidationError, validate_tool_args


def _spec(**overrides: object) -> ToolSpec:
    data = {
        "ref": "knowledge.lookup",
        "kind": "builtin",
        "display_name": "Knowledge Lookup",
        "description": "Search the configured knowledge source for relevant product facts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query."},
                "limit": {"type": "integer", "description": "Maximum results to return.", "enum": [1, 3, 5]},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    }
    data.update(overrides)
    return ToolSpec.model_validate(data)


def test_spec_validator_flags_short_property_description() -> None:
    spec = _spec(
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "short"},
            },
            "required": ["query"],
            "additionalProperties": False,
        }
    )

    report = ToolSpecValidator().validate_spec(spec)

    assert not report.is_valid
    assert report.issues[0].field == "input_schema.properties.query.description"


def test_validate_tool_args_rejects_missing_and_unknown_fields() -> None:
    spec = _spec()

    with pytest.raises(ToolValidationError) as exc:
        validate_tool_args(spec, {"extra": True})

    messages = {f"{issue.field}: {issue.message}" for issue in exc.value.issues}
    assert "query: missing required field" in messages
    assert "extra: field is not allowed by schema" in messages


def test_validate_tool_args_checks_types_and_enum() -> None:
    spec = _spec()

    with pytest.raises(ToolValidationError) as exc:
        validate_tool_args(spec, {"query": 5, "limit": 2})

    messages = {f"{issue.field}: {issue.message}" for issue in exc.value.issues}
    assert "query: expected string" in messages
    assert "limit: value must be one of [1, 3, 5]" in messages


def test_spec_validator_flags_invalid_nested_schema() -> None:
    spec = _spec(
        input_schema={
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "description": "Filtering options for the query payload.",
                    "properties": {
                        "tags": {
                            "type": "array",
                            "description": "Tags used to narrow the search result set.",
                        }
                    },
                    "required": ["tags"],
                    "additionalProperties": False,
                },
            },
            "required": ["filters"],
            "additionalProperties": False,
        }
    )

    report = ToolSpecValidator().validate_spec(spec)

    assert not report.is_valid
    assert any(issue.field == "input_schema.properties.filters.properties.tags.items" for issue in report.issues)


def test_spec_validator_rejects_invalid_json_schema() -> None:
    spec = _spec(
        input_schema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of records to return.",
                    "minimum": "bad",
                }
            },
            "required": ["limit"],
            "additionalProperties": False,
        }
    )

    report = ToolSpecValidator().validate_spec(spec)

    assert not report.is_valid
    assert report.issues[0].field == "input_schema"
    assert "invalid JSON schema" in report.issues[0].message


def test_spec_validator_can_require_aci_fields() -> None:
    spec = _spec()

    report = ToolSpecValidator().validate_spec(spec, require_aci_fields=True)

    fields = {issue.field for issue in report.issues}
    assert {"purpose", "when_to_use", "when_not_to_use", "input_examples", "failure_modes"} <= fields


def test_spec_validator_accepts_complete_aci_fields() -> None:
    spec = _spec(
        purpose="Retrieve grounded product facts before answering the user.",
        when_to_use=["Use when the user needs grounded product facts from knowledge."],
        when_not_to_use=["Do not use for account mutations or live external system checks."],
        input_examples=[
            {
                "name": "basic_lookup",
                "description": "Looks up a pricing answer from curated knowledge.",
                "args": {"query": "What does pricing look like?"},
            }
        ],
        failure_modes=[
            {
                "kind": "transient_upstream_error",
                "description": "Knowledge backend timed out while processing the lookup.",
                "retryable": True,
            }
        ],
        output_validation_mode="strict",
    )

    report = ToolSpecValidator().validate_spec(spec, require_aci_fields=True)

    assert report.is_valid
