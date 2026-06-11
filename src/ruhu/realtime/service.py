from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import logging
from time import monotonic, sleep
from uuid import uuid4

logger = logging.getLogger(__name__)

from ..schemas import RuntimeTurnResult
from .models import RealtimeEvent, RealtimeIdempotencyKey, RealtimeSession, TranscriptCommitResult
from .store import (
    SQLAlchemyRealtimeEventStore,
    SQLAlchemyRealtimeIdempotencyStore,
    SQLAlchemyRealtimeOutboxStore,
    SQLAlchemyRealtimeSessionStore,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_FINAL_TRANSCRIPT_SCOPE = "voice.final_transcript"
_FINAL_TRANSCRIPT_PROCESSING_STALE_AFTER_SECONDS = 300
_FINAL_TRANSCRIPT_WAIT_TIMEOUT_SECONDS = 10.0
_FINAL_TRANSCRIPT_WAIT_INTERVAL_SECONDS = 0.05

_SURFACE_VISIBLE_VOICE_EVENTS = {
    "assistant_speaking_started",
    "assistant_speaking_stopped",
    "assistant_interrupted",
    "assistant_resumed",
    "user_barged_in",
    "interruption_detected",
}


def _idempotency_result_status(record: RealtimeIdempotencyKey) -> str:
    status = record.result_ref.get("_status")
    if isinstance(status, str):
        return status
    if record.result_event_id is not None:
        return "completed"
    return "processing"


def _completed_turn_result_ref(turn_result: RuntimeTurnResult | None) -> dict[str, object]:
    if turn_result is None:
        return {}
    return {
        "trace_id": turn_result.trace_id,
        "turn_id": turn_result.turn_id,
        "step_after": turn_result.step_after,
        "messages": [message.model_dump() for message in turn_result.emitted_messages],
    }


class RealtimeControlPlane:
    def __init__(
        self,
        *,
        sessions: SQLAlchemyRealtimeSessionStore,
        events: SQLAlchemyRealtimeEventStore,
        idempotency: SQLAlchemyRealtimeIdempotencyStore,
        outbox: SQLAlchemyRealtimeOutboxStore,
    ) -> None:
        self.sessions = sessions
        self.events = events
        self.idempotency = idempotency
        self.outbox = outbox

    def start_session(self, session: RealtimeSession) -> RealtimeSession:
        self.sessions.save(session)
        try:
            from ..observability.metrics import voice_sessions_active
            voice_sessions_active.inc()
        except Exception:
            pass
        self.events.append(
            conversation_id=session.conversation_id,
            organization_id=session.organization_id,
            realtime_session_id=session.realtime_session_id,
            family="session",
            name="started",
            payload={
                "surface": session.surface,
                "channel": session.channel,
                "modality": session.modality,
                "provider": session.provider,
                "external_session_key": session.external_session_key,
                "provider_session_id": session.provider_session_id,
                "participant_identity": session.participant_identity,
            },
            actor_type="system",
            visibility="internal",
            outbox_topic="conversation_projection",
        )
        return session

    def record_voice_lifecycle_event(
        self,
        realtime_session_id: str,
        *,
        name: str,
        payload: dict[str, object] | None = None,
    ) -> RealtimeEvent | None:
        session = self.sessions.load(realtime_session_id)
        if session is None or session.surface != "voice":
            return None
        event_payload = {
            "surface": session.surface,
            "channel": session.channel,
            "provider": session.provider,
            "provider_session_id": session.provider_session_id,
            "participant_identity": session.participant_identity,
            "status": session.status,
        }
        if payload:
            event_payload.update(payload)
        return self.events.append(
            conversation_id=session.conversation_id,
            organization_id=session.organization_id,
            realtime_session_id=session.realtime_session_id,
            family="voice",
            name=name,
            payload=event_payload,
            actor_type="system",
            visibility="surface" if name in _SURFACE_VISIBLE_VOICE_EVENTS else "internal",
            outbox_topic="conversation_projection",
        )

    def end_session(
        self,
        realtime_session_id: str,
        *,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> RealtimeSession | None:
        return self._transition_session(
            realtime_session_id,
            status="ended",
            event_name="ended",
            reason=reason,
            metadata=metadata,
        )

    def disconnect_session(
        self,
        realtime_session_id: str,
        *,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> RealtimeSession | None:
        return self._transition_session(
            realtime_session_id,
            status="disconnected",
            event_name="disconnected",
            reason=reason,
            metadata=metadata,
        )

    def error_session(
        self,
        realtime_session_id: str,
        *,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> RealtimeSession | None:
        return self._transition_session(
            realtime_session_id,
            status="errored",
            event_name="errored",
            reason=reason,
            metadata=metadata,
        )

    def touch_session(
        self,
        realtime_session_id: str,
        *,
        provider_session_id: str | None = None,
        participant_identity: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> RealtimeSession | None:
        session = self.sessions.load(realtime_session_id)
        if session is None:
            return None
        now = _utcnow()
        session.last_seen_at = now
        session.updated_at = now
        if provider_session_id and not session.provider_session_id:
            session.provider_session_id = provider_session_id
        if participant_identity and not session.participant_identity:
            session.participant_identity = participant_identity
        if metadata:
            merged = dict(session.transport_metadata)
            merged.update(metadata)
            session.transport_metadata = merged
        self.sessions.save(session)
        return session

    def reconcile_stale_sessions(
        self,
        *,
        channel: str | None = None,
        provider: str | None = None,
        surface: str | None = None,
        last_seen_before: datetime,
        reason: str = "stale_session",
        limit: int = 100,
    ) -> list[RealtimeSession]:
        reconciled: list[RealtimeSession] = []
        stale_sessions = self.sessions.list_stale_active(
            channel=channel,
            provider=provider,
            surface=surface,
            last_seen_before=last_seen_before,
            limit=limit,
        )
        for session in stale_sessions:
            updated = self.disconnect_session(
                session.realtime_session_id,
                reason=reason,
                metadata={"last_seen_before": last_seen_before.isoformat()},
            )
            if updated is not None:
                reconciled.append(updated)
        return reconciled

    def _load_completed_transcript_result(
        self,
        *,
        idempotency: RealtimeIdempotencyKey,
    ) -> TranscriptCommitResult:
        if idempotency.result_event_id is None:
            raise RuntimeError("completed realtime idempotency record is missing an accepted event")
        accepted_event = self.events.load(idempotency.result_event_id)
        if accepted_event is None:
            raise RuntimeError("idempotency record points to missing event")
        return TranscriptCommitResult(
            duplicate=True,
            accepted_event=accepted_event,
            turn_result=None,
            idempotency=idempotency,
        )

    def _acquire_transcript_processing_lease(
        self,
        *,
        organization_id: str | None,
        conversation_id: str,
        idempotency_key: str,
        deadline: float,
    ) -> tuple[RealtimeIdempotencyKey, str]:
        while True:
            processing_token = f"rtproc_{uuid4().hex}"
            lease = self.idempotency.acquire_processing(
                organization_id=organization_id,
                scope=_FINAL_TRANSCRIPT_SCOPE,
                idempotency_key=idempotency_key,
                conversation_id=conversation_id,
                processing_token=processing_token,
                processing_started_at=_utcnow(),
                stale_after_seconds=_FINAL_TRANSCRIPT_PROCESSING_STALE_AFTER_SECONDS,
            )
            status = _idempotency_result_status(lease.key_record)
            if status == "completed" and lease.key_record.result_event_id is not None:
                return lease.key_record, processing_token
            if lease.owned:
                return lease.key_record, processing_token
            if monotonic() >= deadline:
                raise RuntimeError("final transcript processing is already in progress")
            sleep(_FINAL_TRANSCRIPT_WAIT_INTERVAL_SECONDS)

    def commit_final_transcript(
        self,
        *,
        conversation_id: str,
        organization_id: str | None,
        realtime_session_id: str,
        text: str,
        idempotency_key: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        metadata: dict[str, object] | None = None,
        process_turn: Callable[[], RuntimeTurnResult] | None = None,
    ) -> TranscriptCommitResult:
        scope_organization_id = organization_id
        existing = self.idempotency.load(
            organization_id=scope_organization_id,
            scope=_FINAL_TRANSCRIPT_SCOPE,
            idempotency_key=idempotency_key,
        )
        if existing is not None and _idempotency_result_status(existing) == "completed" and existing.result_event_id is not None:
            return self._load_completed_transcript_result(idempotency=existing)

        lease_deadline = monotonic() + _FINAL_TRANSCRIPT_WAIT_TIMEOUT_SECONDS
        idempotency, processing_token = self._acquire_transcript_processing_lease(
            organization_id=scope_organization_id,
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
            deadline=lease_deadline,
        )
        if _idempotency_result_status(idempotency) == "completed" and idempotency.result_event_id is not None:
            return self._load_completed_transcript_result(idempotency=idempotency)

        accepted_event: RealtimeEvent | None = None
        if idempotency.result_event_id is not None:
            accepted_event = self.events.load(idempotency.result_event_id)
            if accepted_event is None:
                raise RuntimeError("idempotency record points to missing event")
        else:
            accepted_event = self.events.append(
                conversation_id=conversation_id,
                organization_id=organization_id,
                realtime_session_id=realtime_session_id,
                family="voice",
                name="final_transcript_observed",
                payload={"text": text, "metadata": dict(metadata or {})},
                actor_type="user",
                correlation_id=correlation_id,
                causation_id=causation_id,
                visibility="internal",
                outbox_topic="conversation_projection",
            )
            updated_idempotency = self.idempotency.save_result_event(
                organization_id=scope_organization_id,
                scope=_FINAL_TRANSCRIPT_SCOPE,
                idempotency_key=idempotency_key,
                processing_token=processing_token,
                conversation_id=conversation_id,
                result_event_id=accepted_event.event_id,
            )
            if updated_idempotency is None:
                latest = self.idempotency.load(
                    organization_id=scope_organization_id,
                    scope=_FINAL_TRANSCRIPT_SCOPE,
                    idempotency_key=idempotency_key,
                )
                if latest is not None and _idempotency_result_status(latest) == "completed" and latest.result_event_id is not None:
                    return self._load_completed_transcript_result(idempotency=latest)
                raise RuntimeError("lost transcript idempotency lease before saving accepted event")
            idempotency = updated_idempotency

        turn_result: RuntimeTurnResult | None = None
        try:
            if process_turn is not None:
                turn_result = process_turn()
        except Exception as exc:
            self.idempotency.fail_processing(
                organization_id=scope_organization_id,
                scope=_FINAL_TRANSCRIPT_SCOPE,
                idempotency_key=idempotency_key,
                processing_token=processing_token,
                conversation_id=conversation_id,
                result_event_id=None if accepted_event is None else accepted_event.event_id,
                error_message=str(exc),
                error_type=type(exc).__name__,
                retryable=True,
                failed_at=_utcnow(),
            )
            raise

        completed_idempotency = self.idempotency.complete_processing(
            organization_id=scope_organization_id,
            scope=_FINAL_TRANSCRIPT_SCOPE,
            idempotency_key=idempotency_key,
            processing_token=processing_token,
            conversation_id=conversation_id,
            result_event_id=accepted_event.event_id,
            result_ref=_completed_turn_result_ref(turn_result),
            completed_at=_utcnow(),
        )
        if completed_idempotency is None:
            latest = self.idempotency.load(
                organization_id=scope_organization_id,
                scope=_FINAL_TRANSCRIPT_SCOPE,
                idempotency_key=idempotency_key,
            )
            if latest is not None and _idempotency_result_status(latest) == "completed" and latest.result_event_id is not None:
                return self._load_completed_transcript_result(idempotency=latest)
            raise RuntimeError("lost transcript idempotency lease before completion")

        return TranscriptCommitResult(
            duplicate=False,
            accepted_event=accepted_event,
            turn_result=turn_result,
            idempotency=completed_idempotency,
        )

    def provisional_transcript_observation(
        self,
        *,
        conversation_id: str,
        organization_id: str | None,
        realtime_session_id: str,
        text: str,
        metadata: dict[str, object] | None = None,
    ) -> RealtimeEvent:
        return self.events.append(
            conversation_id=conversation_id,
            organization_id=organization_id,
            realtime_session_id=realtime_session_id,
            family="voice",
            name="partial_transcript_observed",
            payload={"text": text, "metadata": dict(metadata or {})},
            actor_type="user",
            visibility="internal",
            outbox_topic="conversation_projection",
        )

    def create_session(
        self,
        *,
        conversation_id: str,
        organization_id: str | None,
        surface: str,
        channel: str,
        modality: str,
        provider: str | None = None,
        external_session_key: str | None = None,
        provider_session_id: str | None = None,
        participant_identity: str | None = None,
        transport_metadata: dict[str, object] | None = None,
        parent_realtime_session_id: str | None = None,
        realtime_session_id: str | None = None,
    ) -> RealtimeSession:
        now = _utcnow()
        session = RealtimeSession(
            realtime_session_id=realtime_session_id or f"rs_{uuid4().hex}",
            conversation_id=conversation_id,
            organization_id=organization_id,
            parent_realtime_session_id=parent_realtime_session_id,
            surface=surface,
            channel=channel,
            modality=modality,
            status="active",
            provider=provider,
            external_session_key=external_session_key,
            provider_session_id=provider_session_id,
            participant_identity=participant_identity,
            transport_metadata=dict(transport_metadata or {}),
            started_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        return self.start_session(session)

    def _transition_session(
        self,
        realtime_session_id: str,
        *,
        status: str,
        event_name: str,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> RealtimeSession | None:
        session = self.sessions.load(realtime_session_id)
        if session is None:
            return None
        if session.status == status:
            if metadata:
                merged_metadata = dict(session.transport_metadata)
                merged_metadata.update(metadata)
                session.transport_metadata = merged_metadata
                session.updated_at = _utcnow()
                self.sessions.save(session)
            return session
        now = _utcnow()
        session.status = status
        session.last_seen_at = now
        session.updated_at = now
        if status in {"ended", "disconnected", "errored"}:
            session.ended_at = now
            try:
                from ..observability.metrics import voice_sessions_active
                voice_sessions_active.dec()
            except Exception:
                pass
        if metadata:
            merged_metadata = dict(session.transport_metadata)
            merged_metadata.update(metadata)
            session.transport_metadata = merged_metadata
        self.sessions.save(session)
        payload = {
            "surface": session.surface,
            "channel": session.channel,
            "provider": session.provider,
            "provider_session_id": session.provider_session_id,
            "participant_identity": session.participant_identity,
        }
        if reason is not None:
            payload["reason"] = reason
        if metadata:
            payload["metadata"] = dict(metadata)
        self.events.append(
            conversation_id=session.conversation_id,
            organization_id=session.organization_id,
            realtime_session_id=session.realtime_session_id,
            family="session",
            name=event_name,
            payload=payload,
            actor_type="system",
            visibility="internal",
            outbox_topic="conversation_projection",
        )
        if session.surface == "voice" and event_name == "disconnected":
            self.record_voice_lifecycle_event(
                session.realtime_session_id,
                name="disconnected",
                payload=payload,
            )
        return session


# ── Background cleanup tasks ─────────────────────────────────────────────────

import asyncio
from datetime import timedelta


async def run_stale_session_reconciler(
    control_plane: RealtimeControlPlane,
    *,
    stop_event: asyncio.Event,
    interval_seconds: float = 60.0,
    stale_threshold_seconds: float = 300.0,
) -> None:
    """Periodic background task that reconciles stale active sessions.

    Should be started via ``asyncio.create_task()`` in the app lifespan.
    """
    while not stop_event.is_set():
        try:
            threshold = _utcnow() - timedelta(seconds=stale_threshold_seconds)
            reconciled = control_plane.reconcile_stale_sessions(
                last_seen_before=threshold,
                limit=50,
            )
            if reconciled:
                logger.info(
                    "reconciled %d stale sessions",
                    len(reconciled),
                )
                try:
                    from ..observability.metrics import registry
                    from prometheus_client import Counter
                    if not hasattr(run_stale_session_reconciler, "_counter"):
                        run_stale_session_reconciler._counter = Counter(
                            "ruhu_stale_sessions_reconciled_total",
                            "Stale sessions automatically reconciled",
                            [],
                            registry=registry,
                        )
                    run_stale_session_reconciler._counter.inc(len(reconciled))
                except Exception:
                    pass
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("stale session reconciler error")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            break
        except asyncio.TimeoutError:
            pass


async def run_outbox_cleanup(
    outbox_store,
    *,
    stop_event: asyncio.Event,
    interval_seconds: float = 300.0,
    retention_hours: int = 24,
    batch_size: int = 500,
) -> None:
    """Periodic background task that purges delivered outbox rows.

    Should be started via ``asyncio.create_task()`` in the app lifespan.
    """
    while not stop_event.is_set():
        try:
            threshold = _utcnow() - timedelta(hours=retention_hours)
            purged = outbox_store.purge_delivered(
                older_than=threshold,
                batch_size=batch_size,
            )
            if purged > 0:
                logger.info("outbox cleanup purged %d delivered rows", purged)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("outbox cleanup error")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            break
        except asyncio.TimeoutError:
            pass
