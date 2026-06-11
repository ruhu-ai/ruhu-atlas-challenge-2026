"""Tests for the trace-reader leniency adapter.

Read-only projection of legacy ``family="intent_detected"`` workflow
events to the canonical ``routing.outcome_resolved`` shape. Used by
trace-reading tooling so historical turns can flow through the new code
path without rewriting the database.
"""
from __future__ import annotations

from ruhu.observability.trace_legacy import (
    coerce_routing_events,
    legacy_routing_event_or_none,
)


# ── legacy_routing_event_or_none ────────────────────────────────────────────


def test_legacy_classifier_intent_detected_projects_to_routing_outcome() -> None:
    legacy = {
        "family": "intent_detected",
        "name": "transfer_status",
        "source": "classifier",
        "confidence": 0.91,
        "payload": {"language": "en"},
    }
    projected = legacy_routing_event_or_none(legacy)
    assert projected == {
        "family": "routing",
        "name": "outcome_resolved",
        "source": "classifier",
        "confidence": 0.91,
        "payload": {"event": "transfer_status"},
    }


def test_legacy_event_without_classifier_source_is_not_projected() -> None:
    """Analytics-emitted intent_detected events (non-classifier source) are
    not workflow signals — leave them alone."""
    analytics = {
        "family": "intent_detected",
        "name": "transfer_status",
        "source": "analytics",
        "confidence": 0.7,
    }
    assert legacy_routing_event_or_none(analytics) is None


def test_legacy_event_without_name_returns_none() -> None:
    assert (
        legacy_routing_event_or_none(
            {"family": "intent_detected", "source": "classifier"}
        )
        is None
    )
    assert (
        legacy_routing_event_or_none(
            {"family": "intent_detected", "source": "classifier", "name": ""}
        )
        is None
    )


def test_non_intent_detected_family_returns_none() -> None:
    assert (
        legacy_routing_event_or_none(
            {"family": "fact_extracted", "name": "email", "source": "classifier"}
        )
        is None
    )
    assert (
        legacy_routing_event_or_none(
            {
                "family": "routing",
                "name": "outcome_resolved",
                "source": "classifier",
                "payload": {"event": "x"},
            }
        )
        is None
    )


def test_malformed_event_returns_none() -> None:
    assert legacy_routing_event_or_none("string") is None  # type: ignore[arg-type]
    assert legacy_routing_event_or_none(None) is None  # type: ignore[arg-type]
    assert legacy_routing_event_or_none(42) is None  # type: ignore[arg-type]


# ── coerce_routing_events ───────────────────────────────────────────────────


def test_coerce_rewrites_legacy_events_in_a_trace_without_canonical() -> None:
    events = [
        {"family": "fact_extracted", "name": "email", "source": "deterministic"},
        {
            "family": "intent_detected",
            "name": "transfer_status",
            "source": "classifier",
            "confidence": 0.9,
        },
    ]
    out = coerce_routing_events(events)
    assert out[0] == events[0]  # untouched
    assert out[1]["family"] == "routing"
    assert out[1]["name"] == "outcome_resolved"
    assert out[1]["payload"] == {"event": "transfer_status"}


def test_coerce_preserves_canonical_event_and_suppresses_legacy_projection() -> None:
    """When a canonical routing.outcome_resolved is already in the trace,
    the legacy projection is suppressed to avoid double-counting."""
    events = [
        {
            "family": "routing",
            "name": "outcome_resolved",
            "source": "classifier",
            "payload": {"event": "transfer_status"},
        },
        {
            "family": "intent_detected",
            "name": "transfer_status",
            "source": "classifier",
            "confidence": 0.9,
        },
    ]
    out = coerce_routing_events(events)
    # Canonical preserved as-is.
    assert out[0]["family"] == "routing"
    # Legacy passed through *unchanged* — not projected.
    assert out[1] == events[1]
    # Confirms exactly one routing.outcome_resolved survives.
    assert sum(1 for e in out if e.get("name") == "outcome_resolved") == 1


def test_coerce_passes_through_empty_input() -> None:
    assert coerce_routing_events([]) == []


def test_coerce_returns_list_of_originals_when_no_legacy_present() -> None:
    events = [
        {"family": "fact_updated", "name": "x", "source": "deterministic"},
        {"family": "tool_outcome", "name": "ok", "source": "tool"},
    ]
    assert coerce_routing_events(events) == events


def test_coerce_leaves_analytics_intent_detected_alone() -> None:
    events = [
        {
            "family": "intent_detected",
            "name": "transfer_status",
            "source": "analytics",
            "confidence": 0.7,
        }
    ]
    out = coerce_routing_events(events)
    assert out == events
