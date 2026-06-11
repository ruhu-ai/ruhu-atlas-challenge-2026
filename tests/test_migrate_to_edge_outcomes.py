"""Pure-function tests for ``scripts/migrate_to_edge_outcomes.py``.

Covers the transform helpers (no DB). End-to-end DB-orchestration paths are
exercised in the dev environment with the actual ``--dry-run`` flag.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add scripts/ to sys.path so the migration script imports cleanly under
# pytest, the same way operators run it.
_SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from migrate_to_edge_outcomes import (  # type: ignore[import-not-found]
    _description_for,
    _migrate_classifier_json,
    _migrate_document,
    _migrate_step,
    _scan_for_legacy_refs,
)


# ── _description_for ────────────────────────────────────────────────────────


def test_description_prefers_authored_event_hint() -> None:
    assert (
        _description_for(
            "transfer_status",
            {"transfer_status": "User asks about a transfer."},
            existing_label="Transfer Q",
        )
        == "User asks about a transfer."
    )


def test_description_falls_back_to_transition_label_when_hint_missing() -> None:
    assert (
        _description_for(
            "kyc_help",
            {},
            existing_label="User has a KYC question.",
        )
        == "User has a KYC question."
    )


def test_description_synthesises_fallback_when_label_too_short() -> None:
    out = _description_for("book_demo", {}, existing_label="demo")
    assert out == "User triggers the book demo workflow outcome."
    # Validator on OutcomeCondition.description requires min_length=8.
    assert len(out) >= 8


def test_description_synthesises_fallback_when_no_inputs() -> None:
    out = _description_for("foo_bar", {}, existing_label=None)
    assert "foo bar" in out
    assert len(out) >= 8


# ── _migrate_step ───────────────────────────────────────────────────────────


def test_migrate_step_drops_event_hints_field() -> None:
    step = {
        "id": "entry",
        "event_hints": {"transfer_status": "User asks about a transfer."},
        "transitions": [],
    }
    changed = _migrate_step(step)
    assert changed is True
    assert "event_hints" not in step


def test_migrate_step_converts_event_transition_to_outcome() -> None:
    step = {
        "id": "entry",
        "event_hints": {"transfer_status": "User asks about a transfer."},
        "transitions": [
            {
                "id": "t1",
                "to_step_id": "next",
                "label": "Transfer Q",
                "when": {"kind": "event", "value": "intent_detected:transfer_status"},
            }
        ],
    }
    _migrate_step(step)
    assert step["transitions"][0]["when"] == {
        "kind": "outcome",
        "event": "transfer_status",
        "description": "User asks about a transfer.",
    }


def test_migrate_step_renames_fact_present_value_to_fact_name() -> None:
    step = {
        "transitions": [
            {"id": "t1", "to_step_id": "x", "when": {"kind": "fact_present", "value": "email"}}
        ]
    }
    _migrate_step(step)
    assert step["transitions"][0]["when"] == {"kind": "fact_present", "fact_name": "email"}


def test_migrate_step_renames_fact_missing_value_to_fact_name() -> None:
    step = {
        "transitions": [
            {"id": "t1", "to_step_id": "x", "when": {"kind": "fact_missing", "value": "email"}}
        ]
    }
    _migrate_step(step)
    assert step["transitions"][0]["when"] == {"kind": "fact_missing", "fact_name": "email"}


def test_migrate_step_renames_guard_failure_value_to_guard_id() -> None:
    step = {
        "transitions": [
            {"id": "t1", "to_step_id": "x", "when": {"kind": "guard_failure", "value": "g_age"}}
        ]
    }
    _migrate_step(step)
    assert step["transitions"][0]["when"] == {"kind": "guard_failure", "guard_id": "g_age"}


def test_migrate_step_renames_tool_outcome_value_to_outcome() -> None:
    step = {
        "transitions": [
            {
                "id": "t1",
                "to_step_id": "x",
                "when": {"kind": "tool_outcome", "value": "action_code_approved"},
            }
        ]
    }
    _migrate_step(step)
    assert step["transitions"][0]["when"] == {
        "kind": "tool_outcome",
        "outcome": "action_code_approved",
    }


def test_migrate_step_leaves_already_migrated_outcome_alone() -> None:
    step = {
        "transitions": [
            {
                "id": "t1",
                "to_step_id": "x",
                "when": {
                    "kind": "outcome",
                    "event": "ready",
                    "description": "User is ready.",
                },
            }
        ]
    }
    assert _migrate_step(step) is False


def test_migrate_step_returns_false_when_nothing_changed() -> None:
    step = {
        "id": "entry",
        "transitions": [
            {"id": "t1", "to_step_id": "exit", "when": {"kind": "otherwise"}}
        ],
    }
    assert _migrate_step(step) is False


def test_migrate_step_raises_for_unrecognised_event_value() -> None:
    step = {
        "transitions": [
            {
                "id": "t_bad",
                "to_step_id": "x",
                "when": {"kind": "event", "value": "weird_namespace:thing:nested"},
            }
        ]
    }
    with pytest.raises(ValueError, match="t_bad"):
        _migrate_step(step)


# ── _migrate_document ───────────────────────────────────────────────────────


def test_migrate_document_walks_scenarios_and_steps() -> None:
    doc = {
        "scenarios": [
            {
                "id": "main",
                "steps": [
                    {
                        "id": "entry",
                        "event_hints": {"foo": "User asks about foo."},
                        "transitions": [
                            {
                                "id": "t",
                                "to_step_id": "next",
                                "when": {"kind": "event", "value": "intent_detected:foo"},
                            }
                        ],
                    }
                ],
            }
        ]
    }
    assert _migrate_document(doc) is True
    step = doc["scenarios"][0]["steps"][0]
    assert "event_hints" not in step
    assert step["transitions"][0]["when"]["kind"] == "outcome"


def test_migrate_document_handles_nested_agent_document_wrapper() -> None:
    """Templates wrap the AgentDocument under ``agent_document``."""
    doc = {
        "name": "Wrapped",
        "agent_document": {
            "scenarios": [
                {
                    "id": "main",
                    "steps": [
                        {
                            "id": "entry",
                            "event_hints": {"foo": "User asks about foo."},
                            "transitions": [],
                        }
                    ],
                }
            ]
        },
    }
    assert _migrate_document(doc) is True
    inner_step = doc["agent_document"]["scenarios"][0]["steps"][0]
    assert "event_hints" not in inner_step


def test_migrate_document_returns_false_for_already_migrated() -> None:
    doc = {
        "scenarios": [
            {
                "id": "main",
                "steps": [
                    {
                        "id": "entry",
                        "transitions": [
                            {
                                "id": "t",
                                "to_step_id": "next",
                                "when": {
                                    "kind": "outcome",
                                    "event": "foo",
                                    "description": "User asks about foo.",
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    }
    assert _migrate_document(doc) is False


# ── _migrate_classifier_json ────────────────────────────────────────────────


def test_migrate_classifier_json_renames_intent_name_to_chosen_label() -> None:
    blob = {"intent_name": "transfer_status", "confidence": 0.95}
    assert _migrate_classifier_json(blob) is True
    assert blob == {"chosen_label": "transfer_status", "confidence": 0.95}


def test_migrate_classifier_json_idempotent_for_already_migrated() -> None:
    blob = {"chosen_label": "kyc_help", "confidence": 0.7}
    assert _migrate_classifier_json(blob) is False
    assert blob == {"chosen_label": "kyc_help", "confidence": 0.7}


def test_migrate_classifier_json_prefers_existing_chosen_label_when_both_present() -> None:
    blob = {
        "intent_name": "stale",
        "chosen_label": "fresh",
        "confidence": 0.8,
    }
    assert _migrate_classifier_json(blob) is True
    assert "intent_name" not in blob
    assert blob["chosen_label"] == "fresh"


def test_migrate_classifier_json_handles_empty_dict() -> None:
    blob: dict = {}
    assert _migrate_classifier_json(blob) is False
    assert blob == {}


# ── _scan_for_legacy_refs ───────────────────────────────────────────────────


def test_scan_detects_event_hints() -> None:
    doc = {"scenarios": [{"steps": [{"event_hints": {"x": "y"}}]}]}
    hits = _scan_for_legacy_refs(doc)
    assert any("event_hints" in pat for pat in hits)


def test_scan_detects_intent_detected_token() -> None:
    doc = {"transitions": [{"when": {"value": "intent_detected:foo"}}]}
    hits = _scan_for_legacy_refs(doc)
    assert any("intent_detected" in pat for pat in hits)


def test_scan_detects_legacy_event_kind() -> None:
    doc = {"transitions": [{"when": {"kind": "event", "value": "x"}}]}
    hits = _scan_for_legacy_refs(doc)
    assert any('"event"' in pat for pat in hits)


def test_scan_returns_empty_for_clean_doc() -> None:
    doc = {
        "scenarios": [
            {
                "id": "main",
                "steps": [
                    {
                        "id": "entry",
                        "transitions": [
                            {
                                "id": "t",
                                "to_step_id": "next",
                                "when": {
                                    "kind": "outcome",
                                    "event": "foo",
                                    "description": "User asks about foo.",
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    }
    assert _scan_for_legacy_refs(doc) == []
