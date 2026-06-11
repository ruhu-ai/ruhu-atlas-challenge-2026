from __future__ import annotations

import pytest

from ruhu.tools.authorizer import DefaultToolAuthorizer
from ruhu.tools.registry import ToolRegistry
from ruhu.tools.specs import ToolAnnotations, ToolSpec
from ruhu.tools.types import ToolCall, ToolCaller


def _spec(**overrides: object) -> ToolSpec:
    data = {
        "ref": "crm.lookup",
        "kind": "builtin",
        "display_name": "CRM Lookup",
        "description": "Look up customer records in the CRM system.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "Unique customer identifier."},
            },
            "required": ["customer_id"],
            "additionalProperties": False,
        },
    }
    data.update(overrides)
    return ToolSpec.model_validate(data)


def test_registry_rejects_duplicate_refs() -> None:
    registry = ToolRegistry()
    registry.register(_spec())
    with pytest.raises(ValueError, match="duplicate tool ref"):
        registry.register(_spec())


def test_authorizer_denies_channel_mismatch() -> None:
    spec = _spec(allowed_channels=["web_chat"])
    call = ToolCall(tool_ref=spec.ref, args={"customer_id": "abc"}, caller=ToolCaller(channel="phone"))

    result = DefaultToolAuthorizer().authorize(spec, call)

    assert result.decision == "deny"
    assert result.reason == "channel_not_allowed:phone"


def test_authorizer_confirms_destructive_tool() -> None:
    spec = _spec(
        ref="crm.delete_contact",
        annotations=ToolAnnotations(destructive=True),
        confirmation="destructive_only",
    )
    call = ToolCall(tool_ref=spec.ref, args={"customer_id": "abc"}, caller=ToolCaller(channel="web_chat"))

    result = DefaultToolAuthorizer().authorize(spec, call)

    assert result.decision == "confirm"
    assert result.reason == "destructive_tool_requires_confirmation"
