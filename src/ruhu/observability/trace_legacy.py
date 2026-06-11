"""Read-time leniency for historical workflow events on ``turn_traces``.

Pre-edge-outcomes traces emit workflow signals as
``family="intent_detected", source="classifier"`` events. Post-cutover,
the kernel emits ``family="routing", name="outcome_resolved"`` instead
(see ``classifier_strategy.result_to_routing_events``). Historical
``turn_traces`` rows are *not* rewritten by the migration — only
authored documents and ``classifier_json`` are touched — so any reader
that wants to feed legacy turns through the new code path needs to
project the legacy shape to the new shape on the fly.

This module is a **read-only** projection. It must never be invoked
from kernel writers, validators, or DB migrations: the canonical wire
shape is the new ``routing.outcome_resolved`` event, and we don't keep
two writers in lockstep. Callers (training trace exporter, trace viewer,
analytics tooling) opt in explicitly.

Note that the ``analytics_tagging/`` analytics subsystem *also* emits
``family="intent_detected", source="classifier"`` events post-cutover.
Those are not workflow signals — they are an orthogonal analytics
output, and projecting them to ``routing.outcome_resolved`` would
double-count. Callers that risk seeing both shapes should gate the
projection on a recorded-at timestamp earlier than the migration date,
or drop the projection entirely for traces that already carry a
canonical ``routing.outcome_resolved`` event.
"""
from __future__ import annotations

from typing import Iterable


def legacy_routing_event_or_none(event: dict) -> dict | None:
    """Project a legacy classifier-source ``intent_detected`` event to its
    synthetic ``routing.outcome_resolved`` equivalent.

    Returns ``None`` when ``event`` is not a legacy workflow signal — the
    caller should leave the event untouched in that case.
    """
    if not isinstance(event, dict):
        return None
    if event.get("family") != "intent_detected":
        return None
    if event.get("source") != "classifier":
        return None
    name = event.get("name")
    if not name:
        return None
    return {
        "family": "routing",
        "name": "outcome_resolved",
        "source": "classifier",
        "confidence": event.get("confidence"),
        "payload": {"event": str(name)},
    }


def coerce_routing_events(events: Iterable[dict]) -> list[dict]:
    """Return a list with legacy classifier ``intent_detected`` events
    rewritten to ``routing.outcome_resolved`` and everything else passed
    through unchanged.

    If a trace already carries a ``routing.outcome_resolved`` event the
    legacy projection is *suppressed* — the canonical event wins, so
    repeated reads stay idempotent and double-counting is avoided when a
    trace happens to carry both shapes (the kernel never writes both, but
    a trace re-encoded by tooling could).
    """
    materialised = list(events)
    has_canonical = any(
        isinstance(e, dict)
        and e.get("family") == "routing"
        and e.get("name") == "outcome_resolved"
        for e in materialised
    )
    out: list[dict] = []
    for event in materialised:
        if has_canonical:
            out.append(event)
            continue
        projected = legacy_routing_event_or_none(event)
        out.append(projected if projected is not None else event)
    return out
