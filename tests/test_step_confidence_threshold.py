"""Tests for ``Step.confidence_threshold`` — WI-1.6.

Schema-only coverage. The runtime enforcement (suppress
``intent_detected`` events below threshold) lands with WI-5.1 (cascade
collapse); this file pins the field shape so authors can populate it
in templates today and the kernel can read it later without schema
changes.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from ruhu.agent_document import (
    AgentDocument,
    Scenario,
    Step,
    StepCompletion,
)


def _terminal_step(**overrides) -> Step:
    base = dict(
        id="entry",
        name="Entry",
        completion=StepCompletion(disposition="resolved"),
    )
    base.update(overrides)
    return Step(**base)


def _doc_with(step: Step) -> AgentDocument:
    return AgentDocument(
        version="v1",
        start_scenario_id="main",
        scenarios=[
            Scenario(id="main", name="Main", start_step_id=step.id, steps=[step]),
        ],
    )


def test_step_confidence_threshold_defaults_to_none() -> None:
    step = _terminal_step()
    assert step.confidence_threshold is None


def test_step_confidence_threshold_accepts_zero() -> None:
    step = _terminal_step(confidence_threshold=0.0)
    assert step.confidence_threshold == 0.0


def test_step_confidence_threshold_accepts_one() -> None:
    step = _terminal_step(confidence_threshold=1.0)
    assert step.confidence_threshold == 1.0


def test_step_confidence_threshold_accepts_typical_values() -> None:
    for value in (0.5, 0.75, 0.85, 0.9, 0.99):
        step = _terminal_step(confidence_threshold=value)
        assert step.confidence_threshold == value


def test_step_confidence_threshold_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        _terminal_step(confidence_threshold=-0.1)


def test_step_confidence_threshold_rejects_above_one() -> None:
    with pytest.raises(ValidationError):
        _terminal_step(confidence_threshold=1.1)


def test_step_confidence_threshold_round_trips_through_model_dump() -> None:
    step = _terminal_step(confidence_threshold=0.85)
    payload = step.model_dump(mode="json")
    assert payload["confidence_threshold"] == 0.85
    rebuilt = Step.model_validate(payload)
    assert rebuilt.confidence_threshold == 0.85


def test_step_confidence_threshold_omitted_round_trips_as_none() -> None:
    step = _terminal_step()
    payload = step.model_dump(mode="json")
    assert payload["confidence_threshold"] is None
    rebuilt = Step.model_validate(payload)
    assert rebuilt.confidence_threshold is None


def test_agent_document_serialises_confidence_threshold() -> None:
    """Full-document round trip preserves the field on each step."""
    doc = _doc_with(_terminal_step(confidence_threshold=0.9))
    payload = doc.model_dump(mode="json")
    assert payload["scenarios"][0]["steps"][0]["confidence_threshold"] == 0.9
    rebuilt = AgentDocument.model_validate(payload)
    assert rebuilt.steps[0].confidence_threshold == 0.9


def test_step_validate_shape_invariants_unaffected_by_threshold() -> None:
    """confidence_threshold doesn't interfere with the existing completion+handoff
    mutual-exclusion rules."""
    from ruhu.agent_document import StepHandoff

    with pytest.raises(ValidationError):
        Step(
            id="x",
            name="x",
            completion=StepCompletion(disposition="resolved"),
            handoff=StepHandoff(target_type="queue", target="sales"),
            confidence_threshold=0.85,
        )
