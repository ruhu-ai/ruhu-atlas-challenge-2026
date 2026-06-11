"""Tests for notification fan-out to email + webhook channels.

The notification store is the authoritative record. These tests verify
that the optional fan-out dispatcher correctly:
  * routes to email only when urgency is critical / action_required AND
    a user_id is present AND the email collaborator can resolve an address
  * synchronously POSTs to matching webhook targets, signed with HMAC
  * never raises on collaborator failure (notifications are best-effort)
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

import httpx
import pytest

from ruhu.email_transport import (
    EmailDeliveryResult,
    EmailMessage,
    EmailTransportError,
)
from ruhu.notifications.fanout import (
    NotificationFanoutDispatcher,
    NotificationFanoutResult,
    should_email,
)
from ruhu.notifications.models import NotificationRecord


def _make_notification(
    *,
    urgency: str = "fyi",
    user_id: str | None = "user_1",
    organization_id: str = "org_1",
    category: str = "billing.invoice_failed",
    url: str | None = None,
) -> NotificationRecord:
    return NotificationRecord(
        notification_id="notif_1",
        organization_id=organization_id,
        user_id=user_id,
        category=category,
        level="error" if urgency == "critical" else "info",
        urgency=urgency,
        title="Payment failed",
        message="Your card was declined. Update your billing details.",
        url=url,
        url_label=None,
        source_type=None,
        source_id=None,
        payload={},
        read_at=None,
        dismissed_at=None,
        expires_at=None,
        created_at=datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc),
    )


@dataclass
class _RecordingEmailSender:
    sent: list[EmailMessage] = field(default_factory=list)
    raise_on_send: bool = False

    def send(self, message: EmailMessage) -> EmailDeliveryResult:
        if self.raise_on_send:
            raise EmailTransportError("simulated transport failure")
        self.sent.append(message)
        return EmailDeliveryResult(transport="resend", message_id="msg_test")


@dataclass
class _FakeWebhookTarget:
    webhook_target_id: str
    organization_id: str = "org_1"
    name: str = "test target"
    url: str = "https://example.com/hook"
    is_active: bool = True
    signing_secret_ref: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)


@dataclass
class _FakeWebhookMatcher:
    targets_by_event_name: dict[str, list[_FakeWebhookTarget]] = field(default_factory=dict)
    list_calls: list[dict] = field(default_factory=list)

    def list_matching_targets(
        self,
        organization_id: str,
        *,
        event_name: str,
        agent_id: str | None = None,
        channel: str | None = None,
    ) -> Iterable[_FakeWebhookTarget]:
        self.list_calls.append({"organization_id": organization_id, "event_name": event_name})
        return [
            t
            for t in self.targets_by_event_name.get(event_name, [])
            if t.organization_id == organization_id
        ]


# ── should_email() routing rules ──────────────────────────────────────


def test_should_email_true_for_critical_with_user_id() -> None:
    assert should_email(_make_notification(urgency="critical", user_id="u_1")) is True


def test_should_email_true_for_action_required_with_user_id() -> None:
    assert should_email(_make_notification(urgency="action_required", user_id="u_1")) is True


def test_should_email_false_for_fyi_even_with_user_id() -> None:
    assert should_email(_make_notification(urgency="fyi", user_id="u_1")) is False


def test_should_email_false_when_user_id_missing() -> None:
    assert should_email(_make_notification(urgency="critical", user_id=None)) is False


# ── Email dispatch ───────────────────────────────────────────────────


def test_dispatch_emails_critical_notification_when_collaborators_present() -> None:
    sender = _RecordingEmailSender()
    dispatcher = NotificationFanoutDispatcher(
        email_sender=sender,
        email_resolver=lambda user_id: f"{user_id}@example.com",
    )
    record = _make_notification(urgency="critical", url="/billing")

    result = dispatcher.dispatch(record)

    assert result.emailed is True
    assert result.email_skipped_reason is None
    assert len(sender.sent) == 1
    assert sender.sent[0].to_email == "user_1@example.com"
    assert sender.sent[0].subject == "Payment failed"
    assert sender.sent[0].metadata["notification_id"] == "notif_1"
    assert sender.sent[0].metadata["urgency"] == "critical"


def test_dispatch_skips_email_for_fyi_notifications() -> None:
    sender = _RecordingEmailSender()
    dispatcher = NotificationFanoutDispatcher(
        email_sender=sender,
        email_resolver=lambda user_id: f"{user_id}@example.com",
    )
    record = _make_notification(urgency="fyi")

    result = dispatcher.dispatch(record)

    assert result.emailed is False
    assert result.email_skipped_reason == "urgency_below_threshold"
    assert sender.sent == []


def test_dispatch_skips_email_when_resolver_returns_none() -> None:
    sender = _RecordingEmailSender()
    dispatcher = NotificationFanoutDispatcher(
        email_sender=sender,
        email_resolver=lambda user_id: None,  # user has no email
    )
    record = _make_notification(urgency="critical")

    result = dispatcher.dispatch(record)

    assert result.emailed is False
    assert result.email_skipped_reason == "user_email_unknown"
    assert sender.sent == []


def test_dispatch_skips_email_when_no_email_sender_configured() -> None:
    dispatcher = NotificationFanoutDispatcher(
        email_sender=None,
        email_resolver=lambda user_id: "user@example.com",
    )
    record = _make_notification(urgency="critical")

    result = dispatcher.dispatch(record)

    assert result.emailed is False
    assert result.email_skipped_reason == "email_not_configured"


def test_dispatch_swallows_email_transport_failure() -> None:
    """A bad email send must not propagate — notifications are best-effort."""
    sender = _RecordingEmailSender(raise_on_send=True)
    dispatcher = NotificationFanoutDispatcher(
        email_sender=sender,
        email_resolver=lambda user_id: f"{user_id}@example.com",
    )
    record = _make_notification(urgency="critical")

    result = dispatcher.dispatch(record)  # must not raise

    assert result.emailed is False
    assert result.email_skipped_reason == "email_transport_error"


def test_dispatch_swallows_resolver_exception() -> None:
    def angry_resolver(user_id: str) -> str | None:
        raise RuntimeError("identity store unreachable")

    sender = _RecordingEmailSender()
    dispatcher = NotificationFanoutDispatcher(
        email_sender=sender,
        email_resolver=angry_resolver,
    )
    record = _make_notification(urgency="critical")

    result = dispatcher.dispatch(record)

    assert result.emailed is False
    assert result.email_skipped_reason == "email_resolver_error"
    assert sender.sent == []


def test_dispatch_resolves_relative_url_against_frontend_url() -> None:
    sender = _RecordingEmailSender()
    dispatcher = NotificationFanoutDispatcher(
        email_sender=sender,
        email_resolver=lambda user_id: f"{user_id}@example.com",
        frontend_url="https://app.ruhu.ai",
    )
    record = _make_notification(urgency="critical", url="/billing/invoices/inv_1")

    dispatcher.dispatch(record)

    assert "https://app.ruhu.ai/billing/invoices/inv_1" in sender.sent[0].html_content


# ── Webhook dispatch ────────────────────────────────────────────────


def test_dispatch_skips_webhooks_when_matcher_not_configured() -> None:
    dispatcher = NotificationFanoutDispatcher()
    record = _make_notification(urgency="fyi")

    result = dispatcher.dispatch(record)

    assert result.webhooks_attempted == 0
    assert result.webhooks_delivered == 0
    assert result.webhooks_failed == 0


def test_dispatch_posts_to_matching_webhook_target_with_signed_body() -> None:
    target = _FakeWebhookTarget(
        webhook_target_id="tgt_1",
        signing_secret_ref="literal-secret-shhh",
    )
    matcher = _FakeWebhookMatcher(
        targets_by_event_name={
            "notification.billing.invoice_failed": [target],
        }
    )

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    dispatcher = NotificationFanoutDispatcher(
        webhook_matcher=matcher,
        webhook_client=client,
    )
    record = _make_notification(urgency="critical", category="billing.invoice_failed")

    result = dispatcher.dispatch(record)

    assert result.webhooks_attempted == 1
    assert result.webhooks_delivered == 1
    assert result.webhooks_failed == 0
    assert captured["url"] == "https://example.com/hook"
    assert captured["headers"]["x-ruhu-event"] == "notification.billing.invoice_failed"
    body_obj = json.loads(captured["body"].decode("utf-8"))
    assert body_obj["notification"]["category"] == "billing.invoice_failed"
    assert body_obj["notification"]["urgency"] == "critical"
    # Signature must be HMAC-SHA256 over `timestamp + "." + body`.
    timestamp = captured["headers"]["x-ruhu-timestamp"]
    signature = captured["headers"]["x-ruhu-signature"]
    expected = hmac.new(
        b"literal-secret-shhh",
        timestamp.encode("utf-8") + b"." + captured["body"],
        hashlib.sha256,
    ).hexdigest()
    assert signature == f"sha256={expected}"


def test_dispatch_dedupes_target_matched_by_both_specific_and_generic_event() -> None:
    """A target subscribed to ``notification.created`` AND
    ``notification.billing.invoice_failed`` must only receive ONE delivery."""
    target = _FakeWebhookTarget(webhook_target_id="tgt_dup")
    matcher = _FakeWebhookMatcher(
        targets_by_event_name={
            "notification.created": [target],
            "notification.billing.invoice_failed": [target],
        }
    )

    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(str(request.url))
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    dispatcher = NotificationFanoutDispatcher(
        webhook_matcher=matcher,
        webhook_client=client,
    )
    record = _make_notification(urgency="fyi", category="billing.invoice_failed")

    result = dispatcher.dispatch(record)

    assert result.webhooks_attempted == 1
    assert result.webhooks_delivered == 1
    assert len(posts) == 1


def test_dispatch_records_failure_for_5xx_target_without_raising() -> None:
    target = _FakeWebhookTarget(webhook_target_id="tgt_dead")
    matcher = _FakeWebhookMatcher(
        targets_by_event_name={"notification.created": [target]}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream unavailable")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    dispatcher = NotificationFanoutDispatcher(
        webhook_matcher=matcher,
        webhook_client=client,
    )
    record = _make_notification(urgency="critical")

    result = dispatcher.dispatch(record)  # must not raise

    assert result.webhooks_attempted == 1
    assert result.webhooks_delivered == 0
    assert result.webhooks_failed == 1


def test_dispatch_skips_inactive_target() -> None:
    target = _FakeWebhookTarget(webhook_target_id="tgt_inactive", is_active=False)
    matcher = _FakeWebhookMatcher(
        targets_by_event_name={"notification.created": [target]}
    )

    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(str(request.url))
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    dispatcher = NotificationFanoutDispatcher(
        webhook_matcher=matcher,
        webhook_client=client,
    )
    record = _make_notification(urgency="fyi")

    result = dispatcher.dispatch(record)

    assert result.webhooks_attempted == 0
    assert posts == []


def test_dispatch_swallows_matcher_exception() -> None:
    class AngryMatcher:
        def list_matching_targets(self, *args, **kwargs):
            raise RuntimeError("registry down")

    dispatcher = NotificationFanoutDispatcher(webhook_matcher=AngryMatcher())
    record = _make_notification(urgency="critical")

    result = dispatcher.dispatch(record)  # must not raise

    assert result.webhooks_attempted == 0
    assert result.webhooks_delivered == 0


# ── emit_notification integration ───────────────────────────────────


def test_emit_notification_passes_record_to_fanout() -> None:
    """End-to-end: emit_notification → store.create → fanout.dispatch."""
    from ruhu.notifications.service import emit_notification
    from ruhu.notifications.store import InMemoryNotificationStore

    captured: list[NotificationRecord] = []

    class CapturingDispatcher:
        def dispatch(self, record: NotificationRecord) -> NotificationFanoutResult:
            captured.append(record)
            return NotificationFanoutResult()

    store = InMemoryNotificationStore()
    emit_notification(
        store,
        organization_id="org_1",
        category="billing.invoice_failed",
        title="Payment failed",
        urgency="critical",
        user_id="user_1",
        fanout=CapturingDispatcher(),
    )

    assert len(captured) == 1
    assert captured[0].title == "Payment failed"
    assert captured[0].urgency == "critical"


def test_emit_notification_swallows_fanout_exception() -> None:
    """Fan-out must never break the in-app store path. The notification is
    persisted to the store regardless."""
    from ruhu.notifications.service import emit_notification
    from ruhu.notifications.store import InMemoryNotificationStore

    class AngryDispatcher:
        def dispatch(self, record: NotificationRecord) -> NotificationFanoutResult:
            raise RuntimeError("fanout fell over")

    store = InMemoryNotificationStore()
    emit_notification(
        store,
        organization_id="org_1",
        category="auth.failed_login",
        title="Suspicious login",
        urgency="critical",
        user_id="user_1",
        fanout=AngryDispatcher(),
    )

    # Notification is still stored despite the broken fan-out.
    notifications = store.list_for_user(
        organization_id="org_1", user_id="user_1", limit=10
    )
    assert len(notifications) == 1
    assert notifications[0].title == "Suspicious login"
