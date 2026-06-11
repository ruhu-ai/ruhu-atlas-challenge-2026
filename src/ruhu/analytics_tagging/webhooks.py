"""Semantic-summary webhook fanout + delivery.

Scheduling: runs as a recurring tick on the unified jobs runtime
(``semantic_summary_webhooks.tick``, registered in ``ruhu.worker``); opt-in
via ``RuntimeSettings.semantic_summary_webhook_worker_enabled``. The
dispatcher manages its own per-delivery retry via the realtime outbox.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
import os
from typing import Any, Callable

import httpx

from ..realtime import RealtimeControlPlane, RealtimeEvent, RealtimeOutboxEntry
from ..secret_sources import load_text_secret, normalize_gcp_secret_version
from .models import SemanticSummaryWebhookTarget, utc_now
from .store import IntentTagsStore

logger = logging.getLogger(__name__)

WEBHOOK_DISPATCH_JOB_TYPE = "semantic_summary_webhooks.tick"

_RETRYABLE_HTTP_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
_RETRYABLE_MESSAGE_HINTS = (
    "timed out",
    "temporar",
    "unavailable",
    "connection reset",
    "connection refused",
    "connection aborted",
    "connection dropped",
    "network",
)
_RESERVED_HEADERS = {
    "content-type",
    "user-agent",
    "idempotency-key",
    "x-ruhu-delivery-id",
    "x-ruhu-event",
    "x-ruhu-event-id",
    "x-ruhu-signature",
    "x-ruhu-signing-version",
    "x-ruhu-timestamp",
}


@dataclass(slots=True)
class SemanticSummaryWebhookDispatchResult:
    publication_attempted: int = 0
    publication_fanned_out: int = 0
    publication_skipped: int = 0
    publication_failed: int = 0
    delivery_attempted: int = 0
    delivery_delivered: int = 0
    delivery_failed: int = 0
    delivery_retried: int = 0
    delivery_skipped: int = 0

    def merge(self, other: "SemanticSummaryWebhookDispatchResult") -> "SemanticSummaryWebhookDispatchResult":
        self.publication_attempted += other.publication_attempted
        self.publication_fanned_out += other.publication_fanned_out
        self.publication_skipped += other.publication_skipped
        self.publication_failed += other.publication_failed
        self.delivery_attempted += other.delivery_attempted
        self.delivery_delivered += other.delivery_delivered
        self.delivery_failed += other.delivery_failed
        self.delivery_retried += other.delivery_retried
        self.delivery_skipped += other.delivery_skipped
        return self

    def as_dict(self) -> dict[str, int]:
        return {
            "publication_attempted": self.publication_attempted,
            "publication_fanned_out": self.publication_fanned_out,
            "publication_skipped": self.publication_skipped,
            "publication_failed": self.publication_failed,
            "delivery_attempted": self.delivery_attempted,
            "delivery_delivered": self.delivery_delivered,
            "delivery_failed": self.delivery_failed,
            "delivery_retried": self.delivery_retried,
            "delivery_skipped": self.delivery_skipped,
        }

    def has_activity(self) -> bool:
        return any(self.as_dict().values())


@dataclass(frozen=True, slots=True)
class SemanticSummaryWebhookFailure:
    message: str
    retryable: bool
    category: str
    status_code: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def as_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "message": self.message,
            "retryable": self.retryable,
            "category": self.category,
        }
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


def classify_semantic_webhook_error(exc: Exception) -> SemanticSummaryWebhookFailure:
    if isinstance(exc, httpx.TimeoutException):
        return SemanticSummaryWebhookFailure(
            message=str(exc) or "semantic summary webhook request timed out",
            retryable=True,
            category="webhook_timeout",
        )
    if isinstance(exc, httpx.NetworkError):
        return SemanticSummaryWebhookFailure(
            message=str(exc) or "semantic summary webhook network error",
            retryable=True,
            category="webhook_network_error",
        )
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        retryable = status_code in _RETRYABLE_HTTP_STATUS_CODES
        try:
            payload = exc.response.json()
        except Exception:
            payload = None
        message = exc.response.text.strip() or f"semantic summary webhook rejected with HTTP {status_code}"
        if isinstance(payload, dict):
            candidate = payload.get("message") or payload.get("detail") or payload.get("error")
            if isinstance(candidate, str) and candidate.strip():
                message = candidate.strip()
        return SemanticSummaryWebhookFailure(
            message=message[:500],
            retryable=retryable,
            category="webhook_http_retryable" if retryable else "webhook_http_rejected",
            status_code=status_code,
        )
    message = str(exc) or exc.__class__.__name__
    lowered = message.lower()
    retryable = any(hint in lowered for hint in _RETRYABLE_MESSAGE_HINTS)
    return SemanticSummaryWebhookFailure(
        message=message[:500],
        retryable=retryable,
        category="webhook_error_retryable" if retryable else "webhook_error_rejected",
    )


def resolve_webhook_secret(secret_ref: str | None) -> str:
    ref = str(secret_ref or "").strip()
    if not ref:
        return ""
    if ref.startswith("env:"):
        env_name = ref.split(":", 1)[1].strip()
        value = os.getenv(env_name)
        if not value:
            raise ValueError(f"semantic summary webhook env secret is not configured for {ref}")
        return value
    if ref.startswith("projects/"):
        normalize_gcp_secret_version(ref)
        return load_text_secret(ref)
    return ref


class SemanticSummaryWebhookService:
    def __init__(self, store: IntentTagsStore) -> None:
        self.store = store

    def save_target(self, target: SemanticSummaryWebhookTarget) -> SemanticSummaryWebhookTarget:
        self._validate_headers(target.extra_headers)
        return self.store.save_semantic_webhook_target(target)

    def get_target(self, webhook_target_id: str) -> SemanticSummaryWebhookTarget | None:
        return self.store.get_semantic_webhook_target(webhook_target_id)

    def list_targets(
        self,
        organization_id: str,
        *,
        is_active: bool | None = None,
    ) -> list[SemanticSummaryWebhookTarget]:
        return self.store.list_semantic_webhook_targets(organization_id, is_active=is_active)

    def delete_target(self, webhook_target_id: str) -> bool:
        return self.store.delete_semantic_webhook_target(webhook_target_id)

    def list_matching_targets(
        self,
        organization_id: str,
        *,
        event_name: str,
        agent_id: str | None = None,
        channel: str | None = None,
    ) -> list[SemanticSummaryWebhookTarget]:
        matches: list[SemanticSummaryWebhookTarget] = []
        for target in self.store.list_semantic_webhook_targets(organization_id, is_active=True):
            if target.event_name != event_name:
                continue
            if target.agent_ids and agent_id not in set(target.agent_ids):
                continue
            if target.channels and channel not in set(target.channels):
                continue
            matches.append(target)
        return matches

    def mark_attempt(
        self,
        target: SemanticSummaryWebhookTarget,
        *,
        attempted_at: datetime | None = None,
    ) -> SemanticSummaryWebhookTarget:
        now = attempted_at or utc_now()
        return self.save_target(
            target.model_copy(
                update={
                    "last_attempt_at": now,
                    "updated_at": now,
                }
            )
        )

    def mark_success(
        self,
        target: SemanticSummaryWebhookTarget,
        *,
        delivered_at: datetime | None = None,
    ) -> SemanticSummaryWebhookTarget:
        now = delivered_at or utc_now()
        return self.save_target(
            target.model_copy(
                update={
                    "last_attempt_at": now,
                    "last_success_at": now,
                    "consecutive_failure_count": 0,
                    "last_error": None,
                    "updated_at": now,
                }
            )
        )

    def mark_failure(
        self,
        target: SemanticSummaryWebhookTarget,
        *,
        failure: SemanticSummaryWebhookFailure,
        failed_at: datetime | None = None,
    ) -> SemanticSummaryWebhookTarget:
        now = failed_at or utc_now()
        return self.save_target(
            target.model_copy(
                update={
                    "last_attempt_at": now,
                    "last_failure_at": now,
                    "consecutive_failure_count": int(target.consecutive_failure_count or 0) + 1,
                    "last_error": failure.message[:1000],
                    "updated_at": now,
                }
            )
        )

    def _validate_headers(self, headers: dict[str, str]) -> None:
        forbidden = sorted(
            header
            for header in headers
            if str(header).strip().lower() in _RESERVED_HEADERS
        )
        if forbidden:
            raise ValueError(f"reserved webhook headers cannot be overridden: {', '.join(forbidden)}")


class SemanticSummaryWebhookDispatcher:
    def __init__(
        self,
        *,
        control_plane: RealtimeControlPlane,
        webhook_service: SemanticSummaryWebhookService,
        client_factory: Callable[[float], Any] | None = None,
        user_agent: str = "RuhuSemanticSummaryWebhook/1.0",
    ) -> None:
        self._control_plane = control_plane
        self._webhook_service = webhook_service
        self._client_factory = client_factory
        self._user_agent = user_agent

    def run_pending(
        self,
        *,
        organization_id: str | None = None,
        conversation_id: str | None = None,
        limit: int = 100,
        mode: str = "both",
    ) -> SemanticSummaryWebhookDispatchResult:
        result = SemanticSummaryWebhookDispatchResult()
        if mode in {"fanout", "both"}:
            result.merge(
                self.fanout_pending(
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                    limit=limit,
                )
            )
        if mode in {"deliver", "both"}:
            result.merge(
                self.deliver_pending(
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                    limit=limit,
                )
            )
        return result

    def fanout_pending(
        self,
        *,
        organization_id: str | None = None,
        conversation_id: str | None = None,
        limit: int = 100,
    ) -> SemanticSummaryWebhookDispatchResult:
        result = SemanticSummaryWebhookDispatchResult()
        pending = self._control_plane.outbox.list_pending(
            topic="outbound_webhooks.publication",
            limit=limit,
        )
        for entry in pending:
            if organization_id is not None and entry.organization_id != organization_id:
                continue
            if conversation_id is not None and entry.conversation_id != conversation_id:
                continue
            claimed = self._control_plane.outbox.claim(entry.outbox_id)
            if claimed is None:
                continue
            entry = claimed
            result.publication_attempted += 1
            event = self._control_plane.events.load(entry.event_id)
            if event is None:
                self._control_plane.outbox.mark_failed(entry.outbox_id, error="missing source event")
                result.publication_failed += 1
                continue
            if event.organization_id is None:
                # Enterprise posture: only tenant-scoped events can match a webhook target.
                self._control_plane.outbox.mark_delivered(entry.outbox_id)
                result.publication_skipped += 1
                continue
            event_name = f"{event.family}.{event.name}"
            targets = self._webhook_service.list_matching_targets(
                event.organization_id,
                event_name=event_name,
                agent_id=_string_value(event.payload.get("agent_id")),
                channel=_string_value(event.payload.get("channel")),
            )
            if not targets:
                self._control_plane.outbox.mark_delivered(entry.outbox_id)
                result.publication_skipped += 1
                continue
            for target in targets:
                self._control_plane.outbox.enqueue(
                    event_id=event.event_id,
                    topic="outbound_webhooks.delivery",
                    conversation_id=event.conversation_id,
                    organization_id=event.organization_id,
                    payload={
                        "webhook_target_id": target.webhook_target_id,
                        "event_name": event_name,
                    },
                    dedupe_key=f"{event.event_id}:{target.webhook_target_id}",
                )
                result.publication_fanned_out += 1
            self._control_plane.outbox.mark_delivered(entry.outbox_id)
            self._append_delivery_event(
                event=event,
                name="webhook_fanout_scheduled",
                payload={
                    "source_outbox_id": entry.outbox_id,
                    "target_count": len(targets),
                    "target_ids": [target.webhook_target_id for target in targets],
                },
            )
        return result

    def deliver_pending(
        self,
        *,
        organization_id: str | None = None,
        conversation_id: str | None = None,
        limit: int = 100,
    ) -> SemanticSummaryWebhookDispatchResult:
        result = SemanticSummaryWebhookDispatchResult()
        pending = self._control_plane.outbox.list_pending(
            topic="outbound_webhooks.delivery",
            limit=limit,
        )
        for entry in pending:
            if organization_id is not None and entry.organization_id != organization_id:
                continue
            if conversation_id is not None and entry.conversation_id != conversation_id:
                continue
            claimed = self._control_plane.outbox.claim(entry.outbox_id)
            if claimed is None:
                continue
            entry = claimed
            result.delivery_attempted += 1
            outcome = self._deliver_entry(entry)
            if outcome == "delivered":
                result.delivery_delivered += 1
            elif outcome == "retried":
                result.delivery_retried += 1
            elif outcome == "failed":
                result.delivery_failed += 1
            else:
                result.delivery_skipped += 1
        return result

    def _deliver_entry(self, entry: RealtimeOutboxEntry) -> str:
        event = self._control_plane.events.load(entry.event_id)
        if event is None:
            self._control_plane.outbox.mark_failed(entry.outbox_id, error="missing source event")
            return "failed"
        webhook_target_id = _string_value(entry.payload.get("webhook_target_id"))
        if webhook_target_id is None:
            self._control_plane.outbox.mark_failed(entry.outbox_id, error="missing webhook target id")
            return "failed"
        target = self._webhook_service.get_target(webhook_target_id)
        if target is None or not target.is_active:
            self._control_plane.outbox.mark_delivered(entry.outbox_id)
            return "skipped"
        if target.organization_id != event.organization_id:
            self._control_plane.outbox.mark_delivered(entry.outbox_id)
            return "skipped"
        if target.agent_ids and _string_value(event.payload.get("agent_id")) not in set(target.agent_ids):
            self._control_plane.outbox.mark_delivered(entry.outbox_id)
            return "skipped"
        if target.channels and _string_value(event.payload.get("channel")) not in set(target.channels):
            self._control_plane.outbox.mark_delivered(entry.outbox_id)
            return "skipped"
        target = self._webhook_service.mark_attempt(target, attempted_at=utc_now())
        try:
            self._send_to_target(entry, event, target)
        except Exception as exc:
            failure = classify_semantic_webhook_error(exc)
            target = self._webhook_service.mark_failure(target, failure=failure, failed_at=utc_now())
            if self._schedule_retry(entry, event=event, target=target, failure=failure):
                return "retried"
            return "failed"
        self._control_plane.outbox.mark_delivered(entry.outbox_id)
        self._webhook_service.mark_success(target, delivered_at=utc_now())
        self._append_delivery_event(
            event=event,
            name="webhook_delivered",
            payload={
                "outbox_id": entry.outbox_id,
                "webhook_target_id": target.webhook_target_id,
                "target_name": target.name,
                "target_url": target.url,
            },
        )
        return "delivered"

    def _send_to_target(
        self,
        entry: RealtimeOutboxEntry,
        event: RealtimeEvent,
        target: SemanticSummaryWebhookTarget,
    ) -> None:
        delivery_key = entry.dedupe_key or f"{event.event_id}:{target.webhook_target_id}"
        timestamp = utc_now().isoformat()
        body = self._encode_payload(entry, event, target, delivery_key=delivery_key, timestamp=timestamp)
        headers = self._headers_for_target(target, delivery_key=delivery_key, timestamp=timestamp, body=body, event=event)
        with self._build_client(target.timeout_seconds) as client:
            response = client.post(
                target.url,
                content=body,
                headers=headers,
            )
        response.raise_for_status()

    def _encode_payload(
        self,
        entry: RealtimeOutboxEntry,
        event: RealtimeEvent,
        target: SemanticSummaryWebhookTarget,
        *,
        delivery_key: str,
        timestamp: str,
    ) -> bytes:
        event_name = f"{event.family}.{event.name}"
        payload = {
            "delivery": {
                "delivery_id": entry.outbox_id,
                "delivery_key": delivery_key,
                "event_name": event_name,
                "event_id": event.event_id,
                "conversation_id": event.conversation_id,
                "organization_id": event.organization_id,
                "target_id": target.webhook_target_id,
                "target_name": target.name,
                "occurred_at": event.created_at.isoformat(),
                "sent_at": timestamp,
                "attempt_number": int(entry.attempt_count or 0) + 1,
            },
            "summary": dict(event.payload),
        }
        return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    def _headers_for_target(
        self,
        target: SemanticSummaryWebhookTarget,
        *,
        delivery_key: str,
        timestamp: str,
        body: bytes,
        event: RealtimeEvent,
    ) -> dict[str, str]:
        event_name = f"{event.family}.{event.name}"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": self._user_agent,
            "Idempotency-Key": delivery_key,
            "X-Ruhu-Delivery-Id": delivery_key,
            "X-Ruhu-Event": event_name,
            "X-Ruhu-Event-Id": event.event_id,
            "X-Ruhu-Timestamp": timestamp,
        }
        for key, value in target.extra_headers.items():
            if key.strip().lower() in _RESERVED_HEADERS:
                continue
            headers[key] = value
        signing_secret = resolve_webhook_secret(target.signing_secret_ref)
        if signing_secret:
            digest = hmac.new(
                signing_secret.encode("utf-8"),
                timestamp.encode("utf-8") + b"." + body,
                hashlib.sha256,
            ).hexdigest()
            headers["X-Ruhu-Signing-Version"] = "v1"
            headers["X-Ruhu-Signature"] = f"sha256={digest}"
        return headers

    def _schedule_retry(
        self,
        entry: RealtimeOutboxEntry,
        *,
        event: RealtimeEvent,
        target: SemanticSummaryWebhookTarget,
        failure: SemanticSummaryWebhookFailure,
    ) -> bool:
        attempt_number = int(entry.attempt_count or 0) + 1
        if not failure.retryable or attempt_number > max(int(target.max_retries or 0), 0):
            self._control_plane.outbox.mark_failed(entry.outbox_id, error=failure.message)
            self._append_delivery_event(
                event=event,
                name="webhook_failed",
                payload={
                    "outbox_id": entry.outbox_id,
                    "webhook_target_id": target.webhook_target_id,
                    "target_name": target.name,
                    "error": failure.message,
                    "failure": failure.as_payload(),
                },
            )
            return False
        retry_at = datetime.now(timezone.utc) + timedelta(
            seconds=self._retry_delay_for_attempt(
                retry_backoff_seconds=target.retry_backoff_seconds,
                attempt_number=attempt_number,
            )
        )
        self._control_plane.outbox.mark_retry(
            entry.outbox_id,
            error=failure.message,
            available_at=retry_at,
            retry_at=datetime.now(timezone.utc),
        )
        self._append_delivery_event(
            event=event,
            name="webhook_retry_scheduled",
            payload={
                "outbox_id": entry.outbox_id,
                "webhook_target_id": target.webhook_target_id,
                "target_name": target.name,
                "attempt_number": attempt_number,
                "retry_at": retry_at.isoformat(),
                "error": failure.message,
                "failure": failure.as_payload(),
            },
        )
        return True

    def _retry_delay_for_attempt(self, *, retry_backoff_seconds: float, attempt_number: int) -> int:
        base = max(float(retry_backoff_seconds or 0), 0.1)
        return min(3600, max(1, int(round(base * (2 ** max(attempt_number - 1, 0))))))

    def _append_delivery_event(
        self,
        *,
        event: RealtimeEvent,
        name: str,
        payload: dict[str, object],
    ) -> None:
        self._control_plane.events.append(
            conversation_id=event.conversation_id,
            organization_id=event.organization_id,
            family="semantic_summary_webhook",
            name=name,
            payload=dict(payload),
            actor_type="system",
            visibility="internal",
            causation_id=event.event_id,
            correlation_id=event.correlation_id or event.event_id,
        )

    def _build_client(self, timeout_seconds: float) -> Any:
        if self._client_factory is not None:
            return self._client_factory(timeout_seconds)
        return httpx.Client(timeout=timeout_seconds, follow_redirects=False)


def _string_value(value: object | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
