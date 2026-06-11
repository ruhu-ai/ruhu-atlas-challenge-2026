"""Notification fan-out — beyond the in-app store.

A notification is, by design, a best-effort side-effect (see the docstring on
``emit_notification``). The in-app store is the authoritative record. This
module adds two optional fan-out channels on top:

* **Email** — for notifications whose urgency is ``critical`` or
  ``action_required``, when the notification has a ``user_id`` and we can
  resolve that user's email. Uses the project's ``EmailSender`` interface
  (Resend / SMTP / dev outbox).
* **Webhook** — synchronously POSTs to any matching ``OutboundWebhookTarget``
  registered with ``event_name="notification.<category>"`` or the catch-all
  ``event_name="notification.created"``. Signed with HMAC-SHA256 when the
  target has a signing secret. Best-effort: a failure is logged but does
  not raise.

Both channels are opt-in by wiring the relevant collaborator. If the
collaborator is absent, that channel is silently skipped — the in-app
notification record is still created.

Why webhook fan-out is synchronous here (not via the realtime outbox the
existing OutboundWebhookDispatcher uses): the realtime event store is
conversation-scoped, and many notifications (billing, account-level) have
no conversation. Going directly through HTTP keeps the surface simple at
the cost of no durable retry. Notifications are explicitly best-effort, so
that trade is acceptable.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable, Protocol
from uuid import uuid4

import httpx

from ..email_templates import render_notification_email
from ..email_transport import EmailMessage, EmailSender, EmailTransportError
from .models import NotificationRecord

logger = logging.getLogger(__name__)


# ── Routing rules ────────────────────────────────────────────────────


_EMAIL_ELIGIBLE_URGENCIES = frozenset({"critical", "action_required"})


def should_email(record: NotificationRecord) -> bool:
    """Urgency-based routing: only critical / action_required notifications
    emit email by default. Avoids accidentally spamming users with
    fyi-grade events.
    """
    return record.urgency in _EMAIL_ELIGIBLE_URGENCIES and record.user_id is not None


# ── Collaborator types ───────────────────────────────────────────────


EmailAddressResolver = Callable[[str], str | None]
"""Given a user_id, return the user's email address or None if unknown.

Plug this in by wrapping your identity store, e.g.::

    def resolve_email(user_id: str) -> str | None:
        user = identity_store.get_user(user_id)
        return user.email if user else None
"""


class WebhookTargetMatcher(Protocol):
    """Subset of OutboundWebhookService used by notification fan-out.

    Defined as a Protocol so the notifications package doesn't take a
    direct dependency on analytics_tagging. Pass any object that implements
    ``list_matching_targets``.
    """

    def list_matching_targets(
        self,
        organization_id: str,
        *,
        event_name: str,
        agent_id: str | None = None,
        channel: str | None = None,
    ) -> Iterable[object]: ...


# ── Result ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class NotificationFanoutResult:
    emailed: bool = False
    email_skipped_reason: str | None = None
    webhooks_attempted: int = 0
    webhooks_delivered: int = 0
    webhooks_failed: int = 0


# ── Dispatcher ───────────────────────────────────────────────────────


class NotificationFanoutDispatcher:
    """Fans a notification out to email + webhook channels.

    All collaborators are optional. Missing collaborator → that channel
    is skipped silently. The in-app store side of notifications is
    handled separately by ``emit_notification``.
    """

    def __init__(
        self,
        *,
        email_sender: EmailSender | None = None,
        email_resolver: EmailAddressResolver | None = None,
        webhook_matcher: WebhookTargetMatcher | None = None,
        frontend_url: str | None = None,
        webhook_user_agent: str = "RuhuNotifications/1.0",
        webhook_timeout_seconds: float = 5.0,
        webhook_client: httpx.Client | None = None,
    ) -> None:
        self._email_sender = email_sender
        self._email_resolver = email_resolver
        self._webhook_matcher = webhook_matcher
        self._frontend_url = frontend_url
        self._webhook_user_agent = webhook_user_agent
        self._webhook_timeout_seconds = webhook_timeout_seconds
        self._webhook_client = webhook_client

    # ── Email ────────────────────────────────────────────────────────

    def _dispatch_email(self, record: NotificationRecord) -> tuple[bool, str | None]:
        """Returns (emailed, skip_reason). Never raises."""
        if self._email_sender is None or self._email_resolver is None:
            return False, "email_not_configured"
        if not should_email(record):
            return False, "urgency_below_threshold"
        if record.user_id is None:
            return False, "no_user_id"
        try:
            recipient = self._email_resolver(record.user_id)
        except Exception:
            logger.exception("notification_email_resolver_failed user_id=%s", record.user_id)
            return False, "email_resolver_error"
        if not recipient:
            return False, "user_email_unknown"
        rendered = render_notification_email(
            to_email=recipient,
            title=record.title,
            message=record.message or "",
            url=_resolve_email_url(record.url, self._frontend_url),
            url_label=record.url_label or "Open in Ruhu",
            level=record.level,
            urgency=record.urgency,
        )
        try:
            self._email_sender.send(
                EmailMessage(
                    to_email=recipient,
                    subject=rendered.subject,
                    html_content=rendered.html,
                    text_content=rendered.text,
                    metadata={
                        "kind": "notification",
                        "notification_id": record.notification_id,
                        "category": record.category,
                        "urgency": record.urgency,
                    },
                )
            )
        except EmailTransportError:
            logger.warning(
                "notification_email_send_failed notification_id=%s recipient=%s",
                record.notification_id,
                recipient,
            )
            return False, "email_transport_error"
        return True, None

    # ── Webhook ──────────────────────────────────────────────────────

    def _dispatch_webhooks(self, record: NotificationRecord) -> tuple[int, int, int]:
        """Returns (attempted, delivered, failed). Never raises."""
        if self._webhook_matcher is None:
            return 0, 0, 0

        targets: list[object] = []
        try:
            # Match both the catch-all and the category-specific event names.
            generic = list(
                self._webhook_matcher.list_matching_targets(
                    record.organization_id,
                    event_name="notification.created",
                )
            )
            specific = list(
                self._webhook_matcher.list_matching_targets(
                    record.organization_id,
                    event_name=f"notification.{record.category}",
                )
            )
        except Exception:
            logger.exception(
                "notification_webhook_match_failed notification_id=%s",
                record.notification_id,
            )
            return 0, 0, 0

        # De-dup by webhook_target_id (a target can match both the generic
        # and the category-specific name; only deliver once).
        seen: set[str] = set()
        for target in generic + specific:
            target_id = getattr(target, "webhook_target_id", None)
            if target_id is None or target_id in seen:
                continue
            if not getattr(target, "is_active", True):
                continue
            seen.add(target_id)
            targets.append(target)

        if not targets:
            return 0, 0, 0

        delivered = 0
        failed = 0
        body = self._encode_webhook_body(record)
        for target in targets:
            try:
                self._post_to_target(target, body, record)
                delivered += 1
            except Exception as exc:
                logger.warning(
                    "notification_webhook_post_failed target_id=%s notification_id=%s error=%s",
                    getattr(target, "webhook_target_id", "?"),
                    record.notification_id,
                    exc,
                )
                failed += 1
        return len(targets), delivered, failed

    def _encode_webhook_body(self, record: NotificationRecord) -> bytes:
        delivery_id = f"delivery_{uuid4().hex}"
        timestamp = datetime.now(timezone.utc).isoformat()
        payload = {
            "delivery": {
                "delivery_id": delivery_id,
                "delivery_key": f"{record.notification_id}:{record.organization_id}",
                "event_name": f"notification.{record.category}",
                "event_id": record.notification_id,
                "organization_id": record.organization_id,
                "occurred_at": record.created_at.isoformat(),
                "sent_at": timestamp,
                "attempt_number": 1,
            },
            "notification": {
                "notification_id": record.notification_id,
                "user_id": record.user_id,
                "category": record.category,
                "level": record.level,
                "urgency": record.urgency,
                "title": record.title,
                "message": record.message,
                "url": record.url,
                "url_label": record.url_label,
                "source_type": record.source_type,
                "source_id": record.source_id,
                "payload": dict(record.payload),
            },
        }
        return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    def _post_to_target(
        self,
        target: object,
        body: bytes,
        record: NotificationRecord,
    ) -> None:
        url = getattr(target, "url", None)
        if not url:
            raise ValueError("webhook target has no url")
        timestamp = datetime.now(timezone.utc).isoformat()
        delivery_key = f"{record.notification_id}:{getattr(target, 'webhook_target_id', '')}"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": self._webhook_user_agent,
            "Idempotency-Key": delivery_key,
            "X-Ruhu-Delivery-Id": delivery_key,
            "X-Ruhu-Event": f"notification.{record.category}",
            "X-Ruhu-Event-Id": record.notification_id,
            "X-Ruhu-Timestamp": timestamp,
        }
        # Skip extra_headers that collide with reserved headers; just use as-is
        # for the simple path. The OutboundWebhookService's CRUD already
        # rejects reserved headers at registration time.
        for key, value in (getattr(target, "extra_headers", {}) or {}).items():
            if key.lower() in {h.lower() for h in headers}:
                continue
            headers[key] = value
        signing_secret = self._resolve_signing_secret(target)
        if signing_secret:
            digest = hmac.new(
                signing_secret.encode("utf-8"),
                timestamp.encode("utf-8") + b"." + body,
                hashlib.sha256,
            ).hexdigest()
            headers["X-Ruhu-Signing-Version"] = "v1"
            headers["X-Ruhu-Signature"] = f"sha256={digest}"
        if self._webhook_client is not None:
            response = self._webhook_client.post(
                url, content=body, headers=headers, timeout=self._webhook_timeout_seconds
            )
        else:
            with httpx.Client(timeout=self._webhook_timeout_seconds, follow_redirects=False) as client:
                response = client.post(url, content=body, headers=headers)
        response.raise_for_status()

    @staticmethod
    def _resolve_signing_secret(target: object) -> str | None:
        # Inline import to keep notifications package independent of analytics_tagging
        # at import time. The function is small + stable.
        try:
            from ..analytics_tagging.webhooks import resolve_webhook_secret
        except ImportError:
            return None
        ref = getattr(target, "signing_secret_ref", None)
        if not ref:
            return None
        try:
            return resolve_webhook_secret(ref) or None
        except Exception:
            logger.exception(
                "notification_webhook_secret_resolve_failed target_id=%s",
                getattr(target, "webhook_target_id", "?"),
            )
            return None

    # ── Public entrypoint ────────────────────────────────────────────

    def dispatch(self, record: NotificationRecord) -> NotificationFanoutResult:
        emailed, email_skip = self._dispatch_email(record)
        attempted, delivered, failed = self._dispatch_webhooks(record)
        return NotificationFanoutResult(
            emailed=emailed,
            email_skipped_reason=email_skip,
            webhooks_attempted=attempted,
            webhooks_delivered=delivered,
            webhooks_failed=failed,
        )


def _resolve_email_url(notification_url: str | None, frontend_url: str | None) -> str | None:
    """If notification.url is relative (starts with ``/``), prepend frontend_url."""
    if not notification_url:
        return None
    if notification_url.startswith(("http://", "https://")):
        return notification_url
    if not frontend_url:
        return notification_url
    return f"{frontend_url.rstrip('/')}{notification_url}"
