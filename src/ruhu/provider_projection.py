from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
import json
from typing import Any

import httpx

from .provider_costs import SQLAlchemyProviderCostStore, build_provider_cost_records
from .provider_integrations import WhatsAppMetaChannelConfig, send_whatsapp_meta_texts
from .realtime import RealtimeControlPlane, RealtimeEvent, RealtimeOutboxEntry, RealtimeSession


@dataclass(slots=True)
class ProviderDispatchResult:
    attempted: int = 0
    delivered: int = 0
    failed: int = 0
    retried: int = 0
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class ProviderDispatchFailure:
    message: str
    retryable: bool
    category: str
    status_code: int | None = None
    provider_code: str | None = None
    provider_subcode: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def as_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "message": self.message,
            "retryable": self.retryable,
            "category": self.category,
        }
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        if self.provider_code is not None:
            payload["provider_code"] = self.provider_code
        if self.provider_subcode is not None:
            payload["provider_subcode"] = self.provider_subcode
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


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


def classify_whatsapp_dispatch_error(exc: Exception) -> ProviderDispatchFailure:
    if isinstance(exc, httpx.TimeoutException):
        return ProviderDispatchFailure(
            message=str(exc) or "provider request timed out",
            retryable=True,
            category="provider_timeout",
        )
    if isinstance(exc, httpx.NetworkError):
        return ProviderDispatchFailure(
            message=str(exc) or "provider network error",
            retryable=True,
            category="provider_network_error",
        )
    if isinstance(exc, httpx.HTTPStatusError):
        error_message, provider_code, provider_subcode = _extract_meta_error_details(exc.response)
        status_code = exc.response.status_code
        retryable = status_code in _RETRYABLE_HTTP_STATUS_CODES
        category = "provider_http_retryable" if retryable else "provider_http_rejected"
        return ProviderDispatchFailure(
            message=error_message,
            retryable=retryable,
            category=category,
            status_code=status_code,
            provider_code=provider_code,
            provider_subcode=provider_subcode,
        )
    message = str(exc) or exc.__class__.__name__
    lowered = message.lower()
    retryable = any(hint in lowered for hint in _RETRYABLE_MESSAGE_HINTS)
    return ProviderDispatchFailure(
        message=message,
        retryable=retryable,
        category="provider_error_retryable" if retryable else "provider_error_rejected",
    )


def _extract_meta_error_details(response: httpx.Response) -> tuple[str, str | None, str | None]:
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        body = response.text.strip()
        fallback = body or f"Meta WhatsApp request failed with HTTP {response.status_code}"
        return fallback[:500], None, None
    if not isinstance(payload, dict):
        return f"Meta WhatsApp request failed with HTTP {response.status_code}", None, None
    error_payload = payload.get("error")
    if not isinstance(error_payload, dict):
        return f"Meta WhatsApp request failed with HTTP {response.status_code}", None, None
    message = str(error_payload.get("message") or "").strip() or f"Meta WhatsApp request failed with HTTP {response.status_code}"
    provider_code = error_payload.get("code")
    provider_subcode = error_payload.get("error_subcode")
    normalized_code = None if provider_code in {None, ""} else str(provider_code)
    normalized_subcode = None if provider_subcode in {None, ""} else str(provider_subcode)
    return message[:500], normalized_code, normalized_subcode


class MetaWhatsAppProjectionDispatcher:
    def __init__(
        self,
        *,
        control_plane: RealtimeControlPlane,
        configs: dict[str, WhatsAppMetaChannelConfig],
        provider_cost_store: SQLAlchemyProviderCostStore | None = None,
        client: Any | None = None,
        max_attempts: int = 5,
        retry_delays_seconds: tuple[int, ...] = (5, 15, 60, 300, 900),
    ) -> None:
        self._control_plane = control_plane
        self._configs = configs
        self._provider_cost_store = provider_cost_store
        self._client = client
        self._max_attempts = max(1, max_attempts)
        self._retry_delays_seconds = retry_delays_seconds or (5, 15, 60, 300, 900)

    async def dispatch_pending(
        self,
        *,
        conversation_id: str | None = None,
        limit: int = 100,
    ) -> ProviderDispatchResult:
        result = ProviderDispatchResult()
        pending = self._control_plane.outbox.list_pending(
            topic="provider_projection.meta_whatsapp",
            limit=limit,
        )
        for entry in pending:
            if conversation_id is not None and entry.conversation_id != conversation_id:
                continue
            claimed = self._control_plane.outbox.claim(entry.outbox_id)
            if claimed is None:
                continue
            entry = claimed
            result.attempted += 1
            event = self._control_plane.events.load(entry.event_id)
            if event is None:
                self._control_plane.outbox.mark_failed(entry.outbox_id, error="missing source event")
                result.failed += 1
                continue
            delivered = await self._dispatch_entry(entry, event)
            if delivered == "delivered":
                result.delivered += 1
            elif delivered == "retried":
                result.retried += 1
            elif delivered == "failed":
                result.failed += 1
            else:
                result.skipped += 1
        return result

    async def _dispatch_entry(
        self,
        entry: RealtimeOutboxEntry,
        event: RealtimeEvent,
    ) -> str:
        text = str(event.payload.get("text") or "").strip()
        if event.family != "message" or event.name != "assistant_emitted" or not text:
            self._control_plane.outbox.mark_delivered(entry.outbox_id)
            return "skipped"
        session = self._resolve_whatsapp_session(event.conversation_id)
        if session is None:
            if self._schedule_retry(
                entry,
                event=event,
                failure=ProviderDispatchFailure(
                    message="no whatsapp session available",
                    retryable=True,
                    category="projection_session_unavailable",
                ),
            ):
                return "retried"
            self._record_delivery_event(
                conversation_id=event.conversation_id,
                organization_id=event.organization_id,
                realtime_session_id=event.realtime_session_id,
                source_event_id=event.event_id,
                status="failed",
                error="no whatsapp session available",
            )
            return "failed"
        config = self._configs.get(session.provider_session_id or "")
        recipient_id = (session.participant_identity or session.external_session_key or "").strip()
        if config is None or not recipient_id:
            if self._schedule_retry(
                entry,
                event=event,
                failure=ProviderDispatchFailure(
                    message="missing provider routing data",
                    retryable=True,
                    category="projection_routing_unavailable",
                ),
                realtime_session_id=session.realtime_session_id,
            ):
                return "retried"
            self._record_delivery_event(
                conversation_id=event.conversation_id,
                organization_id=event.organization_id,
                realtime_session_id=session.realtime_session_id,
                source_event_id=event.event_id,
                status="failed",
                error="missing provider routing data",
            )
            return "failed"
        try:
            deliveries = await send_whatsapp_meta_texts(
                config,
                recipient_id=recipient_id,
                texts=[text],
                client=self._client,
            )
        except Exception as exc:  # pragma: no cover - exercised via tests with failing client
            failure = classify_whatsapp_dispatch_error(exc)
            if self._schedule_retry(
                entry,
                event=event,
                failure=failure,
                realtime_session_id=session.realtime_session_id,
            ):
                return "retried"
            self._record_delivery_event(
                conversation_id=event.conversation_id,
                organization_id=event.organization_id,
                realtime_session_id=session.realtime_session_id,
                source_event_id=event.event_id,
                status="failed",
                error=failure.message,
                failure=failure,
            )
            return "failed"
        provider_message_id = None
        if deliveries:
            raw_message_id = deliveries[0].get("message_id")
            if isinstance(raw_message_id, str) and raw_message_id.strip():
                provider_message_id = raw_message_id.strip()
        self._control_plane.outbox.mark_delivered(entry.outbox_id)
        self._record_delivery_event(
            conversation_id=event.conversation_id,
            organization_id=event.organization_id,
            realtime_session_id=session.realtime_session_id,
            source_event_id=event.event_id,
            status="delivered",
            provider_message_id=provider_message_id,
            recipient_id=recipient_id,
        )
        self._record_delivery_costs(
            conversation_id=event.conversation_id,
            organization_id=event.organization_id,
            realtime_session_id=session.realtime_session_id,
            deliveries=deliveries,
        )
        return "delivered"

    def _schedule_retry(
        self,
        entry: RealtimeOutboxEntry,
        *,
        event: RealtimeEvent,
        failure: ProviderDispatchFailure,
        realtime_session_id: str | None = None,
    ) -> bool:
        attempt_number = int(entry.attempt_count or 0) + 1
        if not failure.retryable or attempt_number >= self._max_attempts:
            self._control_plane.outbox.mark_failed(entry.outbox_id, error=failure.message)
            self._record_delivery_event(
                conversation_id=event.conversation_id,
                organization_id=event.organization_id,
                realtime_session_id=realtime_session_id or event.realtime_session_id,
                source_event_id=event.event_id,
                status="failed",
                error=failure.message,
                failure=failure,
            )
            return False
        delay_seconds = self._retry_delay_for_attempt(attempt_number)
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        self._control_plane.outbox.mark_retry(
            entry.outbox_id,
            error=failure.message,
            available_at=retry_at,
            retry_at=datetime.now(timezone.utc),
        )
        self._control_plane.events.append(
            conversation_id=event.conversation_id,
            organization_id=event.organization_id,
            realtime_session_id=realtime_session_id or event.realtime_session_id,
            family="provider",
            name="whatsapp_projection_retry_scheduled",
            payload={
                "source_event_id": event.event_id,
                "outbox_id": entry.outbox_id,
                "attempt_number": attempt_number,
                "retry_at": retry_at.isoformat(),
                "error": failure.message,
                "failure": failure.as_payload(),
            },
            actor_type="system",
            visibility="internal",
            outbox_topic="conversation_projection",
        )
        return True

    def _retry_delay_for_attempt(self, attempt_number: int) -> int:
        index = max(attempt_number - 1, 0)
        if index < len(self._retry_delays_seconds):
            return max(1, int(self._retry_delays_seconds[index]))
        return max(1, int(self._retry_delays_seconds[-1]))

    def _resolve_whatsapp_session(self, conversation_id: str) -> RealtimeSession | None:
        sessions = self._control_plane.sessions.list_by_conversation(conversation_id)
        eligible = [
            session
            for session in sessions
            if session.channel == "whatsapp"
            and session.provider == "meta_whatsapp"
            and (session.provider_session_id or "").strip()
            and (session.participant_identity or session.external_session_key or "").strip()
        ]
        if not eligible:
            return None
        active = [session for session in eligible if session.status == "active"]
        candidates = active or eligible
        candidates.sort(
            key=lambda session: (
                session.last_seen_at or session.updated_at,
                session.created_at,
            ),
            reverse=True,
        )
        return candidates[0]

    def _record_delivery_event(
        self,
        *,
        conversation_id: str,
        organization_id: str | None,
        realtime_session_id: str | None,
        source_event_id: str,
        status: str,
        provider_message_id: str | None = None,
        recipient_id: str | None = None,
        error: str | None = None,
        failure: ProviderDispatchFailure | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "provider": "meta_whatsapp",
            "source_event_id": source_event_id,
            "status": status,
        }
        if provider_message_id is not None:
            payload["provider_message_id"] = provider_message_id
        if recipient_id is not None:
            payload["recipient_id"] = recipient_id
        if error is not None:
            payload["error"] = error
        if failure is not None:
            payload["failure"] = failure.as_payload()
        self._control_plane.events.append(
            conversation_id=conversation_id,
            organization_id=organization_id,
            realtime_session_id=realtime_session_id,
            family="provider",
            name="whatsapp_projection_delivered" if status == "delivered" else "whatsapp_projection_failed",
            payload=payload,
            actor_type="system",
            visibility="internal",
            outbox_topic="conversation_projection",
        )

    def _record_delivery_costs(
        self,
        *,
        conversation_id: str,
        organization_id: str | None,
        realtime_session_id: str,
        deliveries: list[dict[str, object]],
    ) -> None:
        if self._provider_cost_store is None or not deliveries:
            return
        for delivery in deliveries:
            records = build_provider_cost_records(
                provider="meta_whatsapp",
                payload=delivery,
                organization_id=organization_id,
                conversation_id=conversation_id,
                realtime_session_id=realtime_session_id,
                default_cost_type="provider_message_delivery",
            )
            if not records:
                continue
            self._provider_cost_store.save_all(records)
            for record in records:
                self._control_plane.events.append(
                    conversation_id=conversation_id,
                    organization_id=organization_id,
                    realtime_session_id=realtime_session_id,
                    family="provider",
                    name="cost_recorded",
                    payload={
                        "provider": record.provider,
                        "cost_type": record.cost_type,
                        "amount_usd": record.amount_usd,
                        "reference_key": record.reference_key,
                    },
                    actor_type="system",
                    visibility="internal",
                    outbox_topic="conversation_projection",
                )
