"""Tests for the outbound-webhook dispatcher's generic event matching.

The dispatcher used to hardcode ``event.family == "semantic_summary"`` in
both fanout and delivery paths. Those checks were lifted so the dispatcher
delivers any event whose ``family.name`` matches a target's ``event_name``.
These tests exercise that generic surface end-to-end with fake collaborators.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx
import pytest

from ruhu.analytics_tagging.models import SemanticSummaryWebhookTarget
from ruhu.analytics_tagging.webhooks import SemanticSummaryWebhookDispatcher
from ruhu.realtime import RealtimeEvent, RealtimeOutboxEntry


# ── Fake collaborators ────────────────────────────────────────────────


@dataclass
class FakeOutbox:
    pending_by_topic: dict[str, list[RealtimeOutboxEntry]] = field(default_factory=dict)
    enqueued: list[dict[str, Any]] = field(default_factory=list)
    delivered: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    retried: list[tuple[str, str, datetime]] = field(default_factory=list)

    def list_pending(self, *, topic: str, limit: int = 100) -> list[RealtimeOutboxEntry]:
        return list(self.pending_by_topic.get(topic, []))[:limit]

    def claim(self, outbox_id: str) -> RealtimeOutboxEntry | None:
        for entries in self.pending_by_topic.values():
            for entry in entries:
                if entry.outbox_id == outbox_id:
                    return entry
        return None

    def enqueue(self, **kwargs: Any) -> None:
        self.enqueued.append(kwargs)

    def mark_delivered(self, outbox_id: str) -> None:
        self.delivered.append(outbox_id)

    def mark_failed(self, outbox_id: str, *, error: str) -> None:
        self.failed.append((outbox_id, error))

    def mark_retry(self, outbox_id: str, *, error: str, available_at: datetime, retry_at: datetime) -> None:
        self.retried.append((outbox_id, error, available_at))


@dataclass
class FakeEventStore:
    events_by_id: dict[str, RealtimeEvent] = field(default_factory=dict)
    appended: list[dict[str, Any]] = field(default_factory=list)

    def load(self, event_id: str) -> RealtimeEvent | None:
        return self.events_by_id.get(event_id)

    def append(self, **kwargs: Any) -> None:
        self.appended.append(kwargs)


@dataclass
class FakeControlPlane:
    outbox: FakeOutbox
    events: FakeEventStore


@dataclass
class FakeWebhookService:
    targets: list[SemanticSummaryWebhookTarget] = field(default_factory=list)
    list_calls: list[dict[str, Any]] = field(default_factory=list)
    success_marks: list[str] = field(default_factory=list)
    failure_marks: list[tuple[str, str]] = field(default_factory=list)
    attempt_marks: list[str] = field(default_factory=list)

    def list_matching_targets(
        self,
        organization_id: str,
        *,
        event_name: str,
        agent_id: str | None = None,
        channel: str | None = None,
    ) -> list[SemanticSummaryWebhookTarget]:
        self.list_calls.append(
            {
                "organization_id": organization_id,
                "event_name": event_name,
                "agent_id": agent_id,
                "channel": channel,
            }
        )
        return [
            target
            for target in self.targets
            if target.organization_id == organization_id
            and target.event_name == event_name
            and (not target.agent_ids or agent_id in set(target.agent_ids))
            and (not target.channels or channel in set(target.channels))
        ]

    def get_target(self, target_id: str) -> SemanticSummaryWebhookTarget | None:
        for target in self.targets:
            if target.webhook_target_id == target_id:
                return target
        return None

    def mark_attempt(self, target: SemanticSummaryWebhookTarget, *, attempted_at: datetime | None = None):
        self.attempt_marks.append(target.webhook_target_id)
        return target

    def mark_success(self, target: SemanticSummaryWebhookTarget, *, delivered_at: datetime | None = None):
        self.success_marks.append(target.webhook_target_id)
        return target

    def mark_failure(self, target: SemanticSummaryWebhookTarget, *, failure, failed_at: datetime | None = None):
        self.failure_marks.append((target.webhook_target_id, failure.message))
        return target


def _make_event(*, family: str, name: str, payload: dict[str, Any] | None = None) -> RealtimeEvent:
    return RealtimeEvent(
        event_id="evt_1",
        conversation_id="conv_1",
        organization_id="org_1",
        family=family,
        name=name,
        conversation_sequence=1,
        payload=payload or {},
        created_at=datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc),
    )


def _make_outbox_entry(*, topic: str, payload: dict[str, Any] | None = None) -> RealtimeOutboxEntry:
    return RealtimeOutboxEntry(
        outbox_id="outbox_1",
        organization_id="org_1",
        conversation_id="conv_1",
        event_id="evt_1",
        topic=topic,
        payload=payload or {},
        available_at=datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc),
    )


def _make_target(
    *,
    event_name: str,
    secret: str | None = None,
) -> SemanticSummaryWebhookTarget:
    return SemanticSummaryWebhookTarget(
        webhook_target_id="tgt_1",
        organization_id="org_1",
        name="test target",
        url="https://example.com/webhooks",
        event_name=event_name,
        signing_secret_ref=secret,
    )


# ── Fanout: generic event matching ────────────────────────────────────


def test_fanout_matches_non_semantic_summary_event_by_family_dot_name() -> None:
    """A target subscribed to ``conversation.completed`` must receive a fanout
    when an event with family=conversation, name=completed is published."""
    event = _make_event(family="conversation", name="completed")
    entry = _make_outbox_entry(topic="outbound_webhooks.publication")

    outbox = FakeOutbox(pending_by_topic={"outbound_webhooks.publication": [entry]})
    events = FakeEventStore(events_by_id={event.event_id: event})
    control_plane = FakeControlPlane(outbox=outbox, events=events)
    service = FakeWebhookService(targets=[_make_target(event_name="conversation.completed")])

    dispatcher = SemanticSummaryWebhookDispatcher(
        control_plane=control_plane,
        webhook_service=service,
    )
    result = dispatcher.fanout_pending(limit=10)

    assert result.publication_attempted == 1
    assert result.publication_fanned_out == 1
    assert result.publication_skipped == 0
    assert result.publication_failed == 0

    # The service was queried with the canonical event_name derived from
    # the event itself, NOT a hardcoded "semantic_summary.finalized".
    assert service.list_calls == [
        {
            "organization_id": "org_1",
            "event_name": "conversation.completed",
            "agent_id": None,
            "channel": None,
        }
    ]
    # One delivery enqueued for the matched target.
    assert len(outbox.enqueued) == 1
    assert outbox.enqueued[0]["topic"] == "outbound_webhooks.delivery"
    assert outbox.enqueued[0]["payload"]["event_name"] == "conversation.completed"
    # The publication outbox entry was marked delivered.
    assert outbox.delivered == ["outbox_1"]


def test_fanout_skips_when_no_target_matches() -> None:
    """Publication marked delivered+skipped when no target subscribes to
    the event's family.name. No fanout, no failure."""
    event = _make_event(family="ticket", name="created")
    entry = _make_outbox_entry(topic="outbound_webhooks.publication")

    outbox = FakeOutbox(pending_by_topic={"outbound_webhooks.publication": [entry]})
    events = FakeEventStore(events_by_id={event.event_id: event})
    control_plane = FakeControlPlane(outbox=outbox, events=events)
    # Target subscribes to a DIFFERENT event name.
    service = FakeWebhookService(targets=[_make_target(event_name="conversation.completed")])

    dispatcher = SemanticSummaryWebhookDispatcher(
        control_plane=control_plane,
        webhook_service=service,
    )
    result = dispatcher.fanout_pending(limit=10)

    assert result.publication_attempted == 1
    assert result.publication_fanned_out == 0
    assert result.publication_skipped == 1
    assert result.publication_failed == 0
    assert outbox.enqueued == []
    assert outbox.delivered == ["outbox_1"]


def test_fanout_still_matches_legacy_semantic_summary_finalized_event() -> None:
    """Backward-compatibility: targets with event_name=semantic_summary.finalized
    must still match when a semantic-summary event is published."""
    event = _make_event(family="semantic_summary", name="finalized")
    entry = _make_outbox_entry(topic="outbound_webhooks.publication")

    outbox = FakeOutbox(pending_by_topic={"outbound_webhooks.publication": [entry]})
    events = FakeEventStore(events_by_id={event.event_id: event})
    control_plane = FakeControlPlane(outbox=outbox, events=events)
    service = FakeWebhookService(
        targets=[_make_target(event_name="semantic_summary.finalized")]
    )

    dispatcher = SemanticSummaryWebhookDispatcher(
        control_plane=control_plane,
        webhook_service=service,
    )
    result = dispatcher.fanout_pending(limit=10)

    assert result.publication_fanned_out == 1
    assert outbox.enqueued[0]["payload"]["event_name"] == "semantic_summary.finalized"


# ── Delivery: payload + headers reflect the actual event name ─────────


def test_delivered_request_carries_dynamic_event_name_in_headers_and_body() -> None:
    """When delivering, X-Ruhu-Event header and body.delivery.event_name
    must reflect the actual event family.name, not a hardcoded constant."""
    event = _make_event(family="ticket", name="created", payload={"ticket_id": "t_1"})
    entry = _make_outbox_entry(
        topic="outbound_webhooks.delivery",
        payload={"webhook_target_id": "tgt_1", "event_name": "ticket.created"},
    )

    outbox = FakeOutbox(pending_by_topic={"outbound_webhooks.delivery": [entry]})
    events = FakeEventStore(events_by_id={event.event_id: event})
    control_plane = FakeControlPlane(outbox=outbox, events=events)
    service = FakeWebhookService(targets=[_make_target(event_name="ticket.created")])

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200)

    def client_factory(timeout: float) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)

    dispatcher = SemanticSummaryWebhookDispatcher(
        control_plane=control_plane,
        webhook_service=service,
        client_factory=client_factory,
    )
    result = dispatcher.deliver_pending(limit=10)

    assert result.delivery_delivered == 1
    assert result.delivery_failed == 0
    assert captured["headers"]["x-ruhu-event"] == "ticket.created"
    assert captured["body"]["delivery"]["event_name"] == "ticket.created"
    # Original event payload is preserved (under the legacy "summary" key).
    assert captured["body"]["summary"] == {"ticket_id": "t_1"}


def test_delivered_request_signs_body_with_hmac_when_secret_configured() -> None:
    """HMAC-SHA256 signature must cover ``timestamp + "." + body`` so a
    receiver can validate authenticity without trusting the network."""
    event = _make_event(family="conversation", name="completed")
    entry = _make_outbox_entry(
        topic="outbound_webhooks.delivery",
        payload={"webhook_target_id": "tgt_1", "event_name": "conversation.completed"},
    )

    outbox = FakeOutbox(pending_by_topic={"outbound_webhooks.delivery": [entry]})
    events = FakeEventStore(events_by_id={event.event_id: event})
    control_plane = FakeControlPlane(outbox=outbox, events=events)
    service = FakeWebhookService(
        targets=[_make_target(event_name="conversation.completed", secret="shhh-1234")]
    )

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content
        return httpx.Response(200)

    def client_factory(timeout: float) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)

    dispatcher = SemanticSummaryWebhookDispatcher(
        control_plane=control_plane,
        webhook_service=service,
        client_factory=client_factory,
    )
    dispatcher.deliver_pending(limit=10)

    timestamp = captured["headers"]["x-ruhu-timestamp"]
    signature_header = captured["headers"]["x-ruhu-signature"]
    assert captured["headers"]["x-ruhu-signing-version"] == "v1"
    assert signature_header.startswith("sha256=")

    expected_digest = hmac.new(
        b"shhh-1234",
        timestamp.encode("utf-8") + b"." + captured["body"],
        hashlib.sha256,
    ).hexdigest()
    assert signature_header == f"sha256={expected_digest}"


def test_delivered_request_omits_signature_when_no_secret_configured() -> None:
    """No signing secret → no signature headers; receiver should treat the
    payload as unsigned and fall back to other auth (e.g. allowlisted IP)."""
    event = _make_event(family="conversation", name="completed")
    entry = _make_outbox_entry(
        topic="outbound_webhooks.delivery",
        payload={"webhook_target_id": "tgt_1", "event_name": "conversation.completed"},
    )

    outbox = FakeOutbox(pending_by_topic={"outbound_webhooks.delivery": [entry]})
    events = FakeEventStore(events_by_id={event.event_id: event})
    control_plane = FakeControlPlane(outbox=outbox, events=events)
    service = FakeWebhookService(
        targets=[_make_target(event_name="conversation.completed", secret=None)]
    )

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(200)

    def client_factory(timeout: float) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)

    dispatcher = SemanticSummaryWebhookDispatcher(
        control_plane=control_plane,
        webhook_service=service,
        client_factory=client_factory,
    )
    dispatcher.deliver_pending(limit=10)

    assert "x-ruhu-signature" not in captured["headers"]
    assert "x-ruhu-signing-version" not in captured["headers"]


def test_delivered_request_includes_idempotency_key_for_consumer_dedupe() -> None:
    """The Idempotency-Key + X-Ruhu-Delivery-Id headers let the receiver
    dedupe in their own system if our retry logic redelivers."""
    event = _make_event(family="conversation", name="completed")
    entry = _make_outbox_entry(
        topic="outbound_webhooks.delivery",
        payload={"webhook_target_id": "tgt_1", "event_name": "conversation.completed"},
    )
    # Realistic dedupe_key from the fanout step:
    entry = entry.model_copy(update={"dedupe_key": "evt_1:tgt_1"})

    outbox = FakeOutbox(pending_by_topic={"outbound_webhooks.delivery": [entry]})
    events = FakeEventStore(events_by_id={event.event_id: event})
    control_plane = FakeControlPlane(outbox=outbox, events=events)
    service = FakeWebhookService(targets=[_make_target(event_name="conversation.completed")])

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(200)

    def client_factory(timeout: float) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)

    dispatcher = SemanticSummaryWebhookDispatcher(
        control_plane=control_plane,
        webhook_service=service,
        client_factory=client_factory,
    )
    dispatcher.deliver_pending(limit=10)

    assert captured["headers"]["idempotency-key"] == "evt_1:tgt_1"
    assert captured["headers"]["x-ruhu-delivery-id"] == "evt_1:tgt_1"


def test_delivered_request_5xx_marks_failure_and_schedules_retry() -> None:
    """Transient 5xx must trigger a retry, not a permanent failure."""
    event = _make_event(family="conversation", name="completed")
    entry = _make_outbox_entry(
        topic="outbound_webhooks.delivery",
        payload={"webhook_target_id": "tgt_1", "event_name": "conversation.completed"},
    )

    outbox = FakeOutbox(pending_by_topic={"outbound_webhooks.delivery": [entry]})
    events = FakeEventStore(events_by_id={event.event_id: event})
    control_plane = FakeControlPlane(outbox=outbox, events=events)
    service = FakeWebhookService(targets=[_make_target(event_name="conversation.completed")])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream unavailable")

    def client_factory(timeout: float) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)

    dispatcher = SemanticSummaryWebhookDispatcher(
        control_plane=control_plane,
        webhook_service=service,
        client_factory=client_factory,
    )
    result = dispatcher.deliver_pending(limit=10)

    assert result.delivery_delivered == 0
    assert result.delivery_retried == 1
    assert result.delivery_failed == 0
    assert outbox.retried  # retry scheduled
    assert service.failure_marks  # failure recorded on the target
