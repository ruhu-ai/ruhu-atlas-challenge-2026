"""Tests for ``IntentClassificationRequest.prefix`` / ``.suffix``.

Schema-only coverage. Runtime use of these fields by the dispatcher
flows through ``classifier.dispatcher.ClassifierDispatcher``. This
file pins the field shape so the dispatcher can populate them and the
backends can read them.
"""
from __future__ import annotations

import pytest

from ruhu.response_generation import (
    IntentClassificationRequest,
    ResponseGenerationContext,
)


def _request(**overrides) -> IntentClassificationRequest:
    base: dict = {
        "conversation_id": "c1",
        "organization_id": None,
        "agent_id": "agent_a",
        "agent_version_id": "v1",
        "step_id": "entry",
        "step_name": "Entry",
        "step_summary": "Triage.",
        "channel": "web_chat",
        "event_type": "user_message",
        "user_text": "where is my money?",
        "valid_intents": {"transfer_status": "User asks about a transfer."},
        "context": ResponseGenerationContext(),
    }
    base.update(overrides)
    return IntentClassificationRequest(**base)


def test_prefix_and_suffix_default_to_none() -> None:
    request = _request()
    assert request.prefix is None
    assert request.suffix is None


def test_prefix_and_suffix_accept_strings() -> None:
    request = _request(prefix="<canonical-prefix>", suffix="User message: x\nIntent:")
    assert request.prefix == "<canonical-prefix>"
    assert request.suffix == "User message: x\nIntent:"


def test_prefix_and_suffix_can_be_set_independently() -> None:
    only_prefix = _request(prefix="<x>")
    assert only_prefix.prefix == "<x>"
    assert only_prefix.suffix is None

    only_suffix = _request(suffix="<y>")
    assert only_suffix.prefix is None
    assert only_suffix.suffix == "<y>"


def test_request_remains_frozen_after_field_addition() -> None:
    """Adding the prefill fields must not break the frozen-dataclass contract."""
    request = _request(prefix="x", suffix="y")
    with pytest.raises(Exception):
        request.prefix = "z"  # type: ignore[misc]
    with pytest.raises(Exception):
        request.user_text = "anything"  # type: ignore[misc]


def test_dataclass_fields_include_prefix_and_suffix() -> None:
    fields = {f.name for f in IntentClassificationRequest.__dataclass_fields__.values()}
    assert "prefix" in fields
    assert "suffix" in fields


def test_existing_required_fields_preserved() -> None:
    fields = {f.name for f in IntentClassificationRequest.__dataclass_fields__.values()}
    for required in (
        "conversation_id",
        "agent_id",
        "agent_version_id",
        "step_id",
        "step_name",
        "step_summary",
        "user_text",
        "valid_intents",
        "context",
    ):
        assert required in fields
