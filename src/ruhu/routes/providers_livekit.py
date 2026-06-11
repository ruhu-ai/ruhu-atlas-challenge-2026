"""LiveKit provider routes — extracted from api.py (RP-3.1 step 15, blueprint
group 20 — the LAST route extraction). SYNC-KERNEL: the voice transcript and
message ingestion routes call ``ChannelIngressService.process_session_message``
directly (site 7 of the eight kernel call sites) — no turn logic remains in
any route.

Covers the LiveKit phone-call bridge (`/providers/livekit/phone/calls/start`
+ per-call disconnect/end/error/transcripts), the realtime voice-session
ingress (transcripts/messages/signals), the conversation event +
assistant-output replay reads, the assistant-output delivery ack, the
voice-session lifecycle transitions (disconnect/end/error), and the internal
stale voice-session reconciler. The voice-signal helpers (signal metadata,
signal/stage event-name mapping, lifecycle-event recording, the idempotent
reply-delivery ack) and the reply-cutoff/delivery-scope constants moved here
with the routes — nothing else in api.py used them. The phone-route resolver,
phone-session loader, phone-metadata builder, session-lifecycle helpers, the
attachment-ref resolver, and the provider/internal access checks are shared
with the still-inline composition (and the LiveKitPhoneAdapter, whose
construction STAYS in api.py) and thread in as explicit kwargs.

The provider/voice DTOs still live in ``ruhu.api``, so this module is
imported by ``create_app()`` AT THE MOUNT SITE rather than at api.py's
module top (hazard H7: DTO imports sit at module top here).

No ``tags=`` / ``prefix=`` and unchanged handler names (hazard H1).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Query, Request

# DTOs at module top (hazard H7: PEP 563 handler annotations resolve against
# this module's globals).
from ..api import (
    LiveKitVoiceAssistantAckRequest,
    LiveKitVoiceAssistantAckResponse,
    LiveKitVoiceAssistantOutput,
    LiveKitVoiceMessageIngressRequest,
    LiveKitVoiceSignalRequest,
    LiveKitVoiceSignalResponse,
    LiveKitVoiceTranscriptIngressRequest,
    PhoneTranscriptIngressRequest,
    ProviderPhoneCallStartRequest,
    ProviderSessionLifecycleRequest,
    ProviderSessionLifecycleResponse,
    VoiceSessionReconcileRequest,
    VoiceSessionReconcileResponse,
)
from ..livekit_adapter import LiveKitAgentsUnavailableError
from ..realtime import RealtimeEvent
from ..services.channel_ingress import ProviderPhoneBridgeResponse

if TYPE_CHECKING:
    from ..attachments import AttachmentRef
    from ..kernel import ConversationKernel
    from ..livekit_adapter import LiveKitAdapterConfig, LiveKitPhoneAdapter
    from ..phone_numbers import PhoneNumberRouteConfig
    from ..realtime import RealtimeControlPlane, RealtimeSession
    from ..services.channel_ingress import ChannelIngressService

_LIVEKIT_VOICE_REPLY_CUTOFF_EVENTS = {
    ("voice", "user_barged_in"),
    ("voice", "assistant_interrupted"),
    ("voice", "interruption_detected"),
}

_LIVEKIT_VOICE_REPLY_DELIVERY_SCOPE = "voice.reply_delivery"


def _voice_signal_session_metadata(
    *,
    signal: str,
    reason: str | None,
    metadata: dict[str, object],
) -> dict[str, object]:
    merged = dict(metadata)
    merged["last_voice_signal"] = signal
    if reason is not None:
        merged["last_voice_signal_reason"] = reason
    if signal == "assistant_speaking_started":
        merged["assistant_speech_state"] = "speaking"
    elif signal == "assistant_resumed":
        merged["assistant_speech_state"] = "speaking"
    elif signal == "assistant_speaking_stopped":
        merged["assistant_speech_state"] = "idle"
    else:
        merged["assistant_speech_state"] = "interrupted"
    return merged


def _voice_signal_event_names(signal: str) -> list[str]:
    if signal == "user_barged_in":
        return ["user_barged_in", "interruption_detected", "assistant_interrupted"]
    return [signal]


def _voice_reply_stage_event_name(stage: str) -> str:
    if stage == "resolved":
        return "assistant_delivery_resolved"
    if stage == "started":
        return "assistant_playback_started"
    if stage == "completed":
        return "assistant_playback_completed"
    if stage == "interrupted":
        return "assistant_playback_interrupted"
    raise ValueError(f"unsupported assistant reply stage: {stage}")


def build_providers_livekit_router(
    *,
    kernel: "ConversationKernel",
    realtime_control_plane: "RealtimeControlPlane | None",
    channel_ingress: "ChannelIngressService",
    livekit_phone_adapter: "LiveKitPhoneAdapter",
    livekit_phone_adapter_config: "LiveKitAdapterConfig | None",
    require_provider_secret: Callable[[str | None], None],
    require_internal_api_access: Callable[[Request], None],
    resolve_provider_phone_route: Callable[
        [ProviderPhoneCallStartRequest], "PhoneNumberRouteConfig | None"
    ],
    load_livekit_phone_session: Callable[[str], "RealtimeSession | None"],
    build_provider_phone_metadata: Callable[..., dict[str, object]],
    transition_realtime_session_by_id: Callable[..., ProviderSessionLifecycleResponse],
    build_session_lifecycle_response: Callable[
        ["RealtimeSession"], ProviderSessionLifecycleResponse
    ],
    resolve_conversation_attachment_refs: Callable[
        ..., "tuple[list[str], list[AttachmentRef]]"
    ],
    resolve_ingress_idempotency_key: Callable[
        [str | None, dict[str, object]], str | None
    ],
) -> APIRouter:
    router = APIRouter()

    def _record_livekit_voice_signal(
        *,
        session: "RealtimeSession",
        signal: str,
        reason: str | None,
        metadata: dict[str, object],
    ) -> list[RealtimeEvent]:
        if realtime_control_plane is None:
            raise HTTPException(status_code=503, detail="realtime control plane is not configured")
        delivery_id = metadata.get("delivery_id")
        conversation_sequence = metadata.get("conversation_sequence")
        trace_id = metadata.get("trace_id")
        turn_id = metadata.get("turn_id")
        payload = {
            "signal": signal,
            "provider": "livekit_voice",
            "reason": reason,
            "delivery_id": delivery_id if isinstance(delivery_id, str) and delivery_id.strip() else None,
            "conversation_sequence": (
                conversation_sequence
                if isinstance(conversation_sequence, int)
                else None
            ),
            "trace_id": trace_id if isinstance(trace_id, str) and trace_id.strip() else None,
            "turn_id": turn_id if isinstance(turn_id, str) and turn_id.strip() else None,
            "metadata": dict(metadata),
        }
        recorded: list[RealtimeEvent] = []
        for event_name in _voice_signal_event_names(signal):
            event = realtime_control_plane.record_voice_lifecycle_event(
                session.realtime_session_id,
                name=event_name,
                payload=payload,
            )
            if event is not None:
                recorded.append(event)
        return recorded

    def _record_livekit_voice_reply_ack(
        *,
        session: "RealtimeSession",
        delivery_id: str,
        stage: str,
        reason: str | None,
        metadata: dict[str, object],
        idempotency_key: str | None,
    ) -> tuple[RealtimeEvent, bool]:
        if realtime_control_plane is None:
            raise HTTPException(status_code=503, detail="realtime control plane is not configured")
        normalized_delivery_id = delivery_id.strip()
        if not normalized_delivery_id:
            raise HTTPException(status_code=400, detail="delivery_id is required")
        event_name = _voice_reply_stage_event_name(stage)
        resolved_idempotency_key = (
            resolve_ingress_idempotency_key(
                idempotency_key,
                {
                    **metadata,
                    "delivery_id": normalized_delivery_id,
                    "stage": stage,
                },
            )
            or f"{normalized_delivery_id}:{stage}"
        )
        existing = realtime_control_plane.idempotency.load(
            organization_id=session.organization_id,
            scope=_LIVEKIT_VOICE_REPLY_DELIVERY_SCOPE,
            idempotency_key=resolved_idempotency_key,
        )
        if existing is not None and existing.result_event_id is not None:
            existing_event = realtime_control_plane.events.load(existing.result_event_id)
            if existing_event is not None:
                return existing_event, True
        lease = realtime_control_plane.idempotency.acquire_processing(
            organization_id=session.organization_id,
            scope=_LIVEKIT_VOICE_REPLY_DELIVERY_SCOPE,
            idempotency_key=resolved_idempotency_key,
            conversation_id=session.conversation_id,
            processing_token=f"voiceack_{uuid4().hex}",
            processing_started_at=datetime.now(timezone.utc),
            stale_after_seconds=300,
        )
        if not lease.owned:
            latest = realtime_control_plane.idempotency.load(
                organization_id=session.organization_id,
                scope=_LIVEKIT_VOICE_REPLY_DELIVERY_SCOPE,
                idempotency_key=resolved_idempotency_key,
            )
            if latest is not None and latest.result_event_id is not None:
                latest_event = realtime_control_plane.events.load(latest.result_event_id)
                if latest_event is not None:
                    return latest_event, True
            raise HTTPException(status_code=409, detail="assistant reply acknowledgement is already in progress")
        event_payload = {
            "delivery_id": normalized_delivery_id,
            "stage": stage,
            "reason": reason,
            "metadata": dict(metadata),
            "provider": "livekit_voice",
        }
        event = realtime_control_plane.events.append(
            conversation_id=session.conversation_id,
            organization_id=session.organization_id,
            realtime_session_id=session.realtime_session_id,
            family="voice",
            name=event_name,
            payload=event_payload,
            actor_type="system",
            visibility="internal",
            outbox_topic="conversation_projection",
            outbox_dedupe_key=f"{normalized_delivery_id}:{stage}",
        )
        completed = realtime_control_plane.idempotency.complete_processing(
            organization_id=session.organization_id,
            scope=_LIVEKIT_VOICE_REPLY_DELIVERY_SCOPE,
            idempotency_key=resolved_idempotency_key,
            processing_token=str(lease.key_record.result_ref.get("_processing_token") or ""),
            conversation_id=session.conversation_id,
            result_event_id=event.event_id,
            result_ref={
                "delivery_id": normalized_delivery_id,
                "stage": stage,
                "event_id": event.event_id,
                "conversation_sequence": event.conversation_sequence,
            },
            completed_at=datetime.now(timezone.utc),
        )
        if completed is None:
            latest = realtime_control_plane.idempotency.load(
                organization_id=session.organization_id,
                scope=_LIVEKIT_VOICE_REPLY_DELIVERY_SCOPE,
                idempotency_key=resolved_idempotency_key,
            )
            if latest is not None and latest.result_event_id is not None:
                latest_event = realtime_control_plane.events.load(latest.result_event_id)
                if latest_event is not None:
                    return latest_event, True
            raise HTTPException(status_code=409, detail="assistant reply acknowledgement completion was lost")
        return event, False

    @router.post("/providers/livekit/phone/calls/start", response_model=ProviderPhoneBridgeResponse)
    def start_livekit_phone_call(
        payload: ProviderPhoneCallStartRequest,
        x_ruhu_provider_secret: str | None = Header(default=None, alias="X-Ruhu-Provider-Secret"),
    ) -> ProviderPhoneBridgeResponse:
        resolved_route = resolve_provider_phone_route(payload)
        if payload.agent_id and resolved_route is not None and payload.agent_id != resolved_route.agent_id:
            raise HTTPException(
                status_code=409,
                detail="configured phone number route does not match provided agent_id",
            )
        agent_id = payload.agent_id or (None if resolved_route is None else resolved_route.agent_id)
        if not agent_id:
            raise HTTPException(
                status_code=400,
                detail="agent_id is required when no phone number route matches the called number",
            )
        organization_id = payload.organization_id or (None if resolved_route is None else resolved_route.organization_id)
        metadata = build_provider_phone_metadata(
            incoming_metadata=payload.metadata,
            telephony_provider=payload.provider,
            resolved_route=resolved_route,
        )
        effective_telephony_provider = payload.provider
        if effective_telephony_provider is None and resolved_route is not None:
            effective_telephony_provider = resolved_route.provider
        start_payload = payload.model_copy(
            update={
                "agent_id": agent_id,
                "organization_id": organization_id,
                "provider": effective_telephony_provider,
                "metadata": metadata,
            }
        )
        try:
            response = livekit_phone_adapter.start_phone_call(
                payload=start_payload,
                provider_secret=x_ruhu_provider_secret,
            )
        except LiveKitAgentsUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return ProviderPhoneBridgeResponse(**response)

    @router.post("/providers/livekit/phone/calls/{call_id}/disconnect", response_model=ProviderSessionLifecycleResponse)
    def disconnect_livekit_phone_call(
        call_id: str,
        payload: ProviderSessionLifecycleRequest | None = None,
        x_ruhu_provider_secret: str | None = Header(default=None, alias="X-Ruhu-Provider-Secret"),
    ) -> ProviderSessionLifecycleResponse:
        session = load_livekit_phone_session(call_id)
        request_payload = (payload or ProviderSessionLifecycleRequest()).model_copy(
            update={
                "metadata": build_provider_phone_metadata(
                    incoming_metadata=None if payload is None else payload.metadata,
                    session=session,
                )
            }
        )
        return livekit_phone_adapter.transition_phone_call(
            call_id=call_id,
            payload=request_payload,
            provider_secret=x_ruhu_provider_secret,
            target="disconnected",
        )

    @router.post("/providers/livekit/phone/calls/{call_id}/end", response_model=ProviderSessionLifecycleResponse)
    def end_livekit_phone_call(
        call_id: str,
        payload: ProviderSessionLifecycleRequest | None = None,
        x_ruhu_provider_secret: str | None = Header(default=None, alias="X-Ruhu-Provider-Secret"),
    ) -> ProviderSessionLifecycleResponse:
        session = load_livekit_phone_session(call_id)
        request_payload = (payload or ProviderSessionLifecycleRequest()).model_copy(
            update={
                "metadata": build_provider_phone_metadata(
                    incoming_metadata=None if payload is None else payload.metadata,
                    session=session,
                )
            }
        )
        return livekit_phone_adapter.transition_phone_call(
            call_id=call_id,
            payload=request_payload,
            provider_secret=x_ruhu_provider_secret,
            target="ended",
        )

    @router.post("/providers/livekit/phone/calls/{call_id}/error", response_model=ProviderSessionLifecycleResponse)
    def error_livekit_phone_call(
        call_id: str,
        payload: ProviderSessionLifecycleRequest | None = None,
        x_ruhu_provider_secret: str | None = Header(default=None, alias="X-Ruhu-Provider-Secret"),
    ) -> ProviderSessionLifecycleResponse:
        session = load_livekit_phone_session(call_id)
        request_payload = (payload or ProviderSessionLifecycleRequest()).model_copy(
            update={
                "metadata": build_provider_phone_metadata(
                    incoming_metadata=None if payload is None else payload.metadata,
                    session=session,
                )
            }
        )
        return livekit_phone_adapter.transition_phone_call(
            call_id=call_id,
            payload=request_payload,
            provider_secret=x_ruhu_provider_secret,
            target="errored",
        )

    @router.post("/providers/livekit/phone/calls/{call_id}/transcripts", response_model=ProviderPhoneBridgeResponse)
    def ingest_livekit_phone_transcript(
        call_id: str,
        payload: PhoneTranscriptIngressRequest,
        x_ruhu_provider_secret: str | None = Header(default=None, alias="X-Ruhu-Provider-Secret"),
    ) -> ProviderPhoneBridgeResponse:
        session = load_livekit_phone_session(call_id)
        transcript_payload = payload.model_copy(
            update={
                "provider": payload.provider
                or (
                    str(session.transport_metadata.get("telephony_provider")).strip()
                    if session is not None
                    and isinstance(session.transport_metadata.get("telephony_provider"), str)
                    and str(session.transport_metadata.get("telephony_provider")).strip()
                    else None
                ),
                "metadata": build_provider_phone_metadata(
                    incoming_metadata=payload.metadata,
                    telephony_provider=payload.provider,
                    session=session,
                ),
            }
        )
        response = livekit_phone_adapter.ingest_phone_transcript(
            call_id=call_id,
            payload=transcript_payload,
            provider_secret=x_ruhu_provider_secret,
        )
        return ProviderPhoneBridgeResponse(**response)

    @router.post("/providers/livekit/voice/sessions/{realtime_session_id}/transcripts", response_model=ProviderPhoneBridgeResponse)
    def ingest_livekit_voice_transcript(
        realtime_session_id: str,
        payload: LiveKitVoiceTranscriptIngressRequest,
        x_ruhu_provider_secret: str | None = Header(default=None, alias="X-Ruhu-Provider-Secret"),
    ) -> ProviderPhoneBridgeResponse:
        require_provider_secret(x_ruhu_provider_secret)
        if realtime_control_plane is None:
            raise HTTPException(status_code=503, detail="realtime control plane is not configured")
        session = realtime_control_plane.sessions.load(realtime_session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="unknown provider session")
        metadata = {
            **payload.metadata,
            "provider": "livekit_voice",
            "agent_name": None if livekit_phone_adapter_config is None else livekit_phone_adapter_config.agent_name,
            "sdk_version_target": None
            if livekit_phone_adapter_config is None
            else livekit_phone_adapter_config.sdk_version_target,
        }
        if payload.provider_session_id or payload.participant_identity:
            realtime_control_plane.touch_session(
                realtime_session_id,
                provider_session_id=payload.provider_session_id,
                participant_identity=payload.participant_identity,
                metadata=metadata,
            )
            session = realtime_control_plane.sessions.load(realtime_session_id) or session
        return channel_ingress.process_session_message(
            session=session,
            text=payload.text,
            metadata=metadata,
            modality="audio",
            event_type="user_final_transcript" if payload.is_final else "user_partial_transcript",
            organization_id=session.organization_id,
            idempotency_key=payload.idempotency_key,
        )

    @router.post("/providers/livekit/voice/sessions/{realtime_session_id}/messages", response_model=ProviderPhoneBridgeResponse)
    def ingest_livekit_voice_message(
        realtime_session_id: str,
        payload: LiveKitVoiceMessageIngressRequest,
        x_ruhu_provider_secret: str | None = Header(default=None, alias="X-Ruhu-Provider-Secret"),
    ) -> ProviderPhoneBridgeResponse:
        require_provider_secret(x_ruhu_provider_secret)
        if realtime_control_plane is None:
            raise HTTPException(status_code=503, detail="realtime control plane is not configured")
        session = realtime_control_plane.sessions.load(realtime_session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="unknown provider session")
        attachment_ids, attachment_refs = resolve_conversation_attachment_refs(
            conversation_id=session.conversation_id,
            organization_id=session.organization_id,
            attachment_ids=payload.attachment_ids,
        )
        # Refs travel as a first-class list in metadata ``attachment_refs``
        # (serialized dicts) so that ``_build_runtime_turn_from_metadata``
        # downstream can migrate them to ``RuntimeTurn.attachments``.  IDs
        # are preserved as debug hints per spec §3.
        metadata = {
            **payload.metadata,
            "provider": "livekit_voice",
            "agent_name": None if livekit_phone_adapter_config is None else livekit_phone_adapter_config.agent_name,
            "sdk_version_target": None
            if livekit_phone_adapter_config is None
            else livekit_phone_adapter_config.sdk_version_target,
            **({"attachment_ids": attachment_ids} if attachment_ids else {}),
            **(
                {"attachment_refs": [ref.model_dump(mode="json") for ref in attachment_refs]}
                if attachment_refs
                else {}
            ),
        }
        if payload.provider_session_id or payload.participant_identity:
            realtime_control_plane.touch_session(
                realtime_session_id,
                provider_session_id=payload.provider_session_id,
                participant_identity=payload.participant_identity,
                metadata=metadata,
            )
            session = realtime_control_plane.sessions.load(realtime_session_id) or session
        return channel_ingress.process_session_message(
            session=session,
            text=payload.text,
            metadata=metadata,
            modality="text",
            event_type="user_message",
            organization_id=session.organization_id,
            idempotency_key=payload.idempotency_key,
        )

    @router.post("/providers/livekit/voice/sessions/{realtime_session_id}/signals", response_model=LiveKitVoiceSignalResponse)
    def ingest_livekit_voice_signal(
        realtime_session_id: str,
        payload: LiveKitVoiceSignalRequest,
        x_ruhu_provider_secret: str | None = Header(default=None, alias="X-Ruhu-Provider-Secret"),
    ) -> LiveKitVoiceSignalResponse:
        require_provider_secret(x_ruhu_provider_secret)
        if realtime_control_plane is None:
            raise HTTPException(status_code=503, detail="realtime control plane is not configured")
        session = realtime_control_plane.sessions.load(realtime_session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="unknown provider session")
        if session.status != "active":
            raise HTTPException(status_code=409, detail=f"{session.channel} session is no longer active")
        metadata = _voice_signal_session_metadata(
            signal=payload.signal,
            reason=payload.reason,
            metadata={
                **payload.metadata,
                "provider": "livekit_voice",
                "provider_session_id": payload.provider_session_id,
                "participant_identity": payload.participant_identity,
            },
        )
        updated_session = realtime_control_plane.touch_session(
            realtime_session_id,
            provider_session_id=payload.provider_session_id,
            participant_identity=payload.participant_identity,
            metadata=metadata,
        ) or session
        recorded_events = _record_livekit_voice_signal(
            session=updated_session,
            signal=payload.signal,
            reason=payload.reason,
            metadata=metadata,
        )
        return LiveKitVoiceSignalResponse(
            conversation_id=updated_session.conversation_id,
            realtime_session_id=updated_session.realtime_session_id,
            signal=payload.signal,
            status=updated_session.status,
            recorded_names=[event.name for event in recorded_events],
            conversation_sequence=None if not recorded_events else recorded_events[-1].conversation_sequence,
            updated_at=updated_session.updated_at,
        )

    @router.get("/providers/livekit/conversations/{conversation_id}/events", response_model=list[RealtimeEvent])
    def replay_livekit_conversation_events(
        conversation_id: str,
        after_sequence: int = Query(default=0, ge=0),
        family: str | None = Query(default=None),
        name: str | None = Query(default=None),
        x_ruhu_provider_secret: str | None = Header(default=None, alias="X-Ruhu-Provider-Secret"),
    ) -> list[RealtimeEvent]:
        require_provider_secret(x_ruhu_provider_secret)
        if realtime_control_plane is None:
            raise HTTPException(status_code=503, detail="realtime control plane is not configured")
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="unknown conversation id")
        events = realtime_control_plane.events.replay(
            conversation_id=conversation_id,
            after_sequence=after_sequence,
        )
        if family is not None:
            events = [event for event in events if event.family == family]
        if name is not None:
            events = [event for event in events if event.name == name]
        return events

    @router.get(
        "/providers/livekit/conversations/{conversation_id}/assistant-outputs",
        response_model=list[LiveKitVoiceAssistantOutput],
    )
    def replay_livekit_assistant_outputs(
        conversation_id: str,
        after_sequence: int = Query(default=0, ge=0),
        x_ruhu_provider_secret: str | None = Header(default=None, alias="X-Ruhu-Provider-Secret"),
    ) -> list[LiveKitVoiceAssistantOutput]:
        require_provider_secret(x_ruhu_provider_secret)
        if realtime_control_plane is None:
            raise HTTPException(status_code=503, detail="realtime control plane is not configured")
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="unknown conversation id")
        events = realtime_control_plane.events.replay(
            conversation_id=conversation_id,
            after_sequence=after_sequence,
        )
        cutoff_sequence = after_sequence
        for event in events:
            if (event.family, event.name) in _LIVEKIT_VOICE_REPLY_CUTOFF_EVENTS:
                cutoff_sequence = max(cutoff_sequence, event.conversation_sequence)
        outputs: list[LiveKitVoiceAssistantOutput] = []
        for event in events:
            if event.family != "message" or event.name != "assistant_emitted":
                continue
            if event.conversation_sequence <= cutoff_sequence:
                continue
            text = str(event.payload.get("text") or "").strip()
            if not text:
                continue
            outputs.append(
                LiveKitVoiceAssistantOutput(
                    delivery_id=event.event_id,
                    conversation_id=conversation_id,
                    conversation_sequence=event.conversation_sequence,
                    text=text,
                    trace_id=(
                        str(event.payload.get("trace_id"))
                        if isinstance(event.payload.get("trace_id"), str)
                        else None
                    ),
                    turn_id=(
                        str(event.payload.get("turn_id"))
                        if isinstance(event.payload.get("turn_id"), str)
                        else None
                    ),
                    source_event_id=event.event_id,
                )
            )
        return outputs

    @router.post(
        "/providers/livekit/voice/sessions/{realtime_session_id}/assistant-outputs/{delivery_id}/ack",
        response_model=LiveKitVoiceAssistantAckResponse,
    )
    def acknowledge_livekit_assistant_output(
        realtime_session_id: str,
        delivery_id: str,
        payload: LiveKitVoiceAssistantAckRequest,
        x_ruhu_provider_secret: str | None = Header(default=None, alias="X-Ruhu-Provider-Secret"),
    ) -> LiveKitVoiceAssistantAckResponse:
        require_provider_secret(x_ruhu_provider_secret)
        if realtime_control_plane is None:
            raise HTTPException(status_code=503, detail="realtime control plane is not configured")
        session = realtime_control_plane.sessions.load(realtime_session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="unknown provider session")
        event, duplicate = _record_livekit_voice_reply_ack(
            session=session,
            delivery_id=delivery_id,
            stage=payload.stage,
            reason=payload.reason,
            metadata={
                **payload.metadata,
                "provider": "livekit_voice",
            },
            idempotency_key=payload.idempotency_key,
        )
        return LiveKitVoiceAssistantAckResponse(
            conversation_id=session.conversation_id,
            realtime_session_id=session.realtime_session_id,
            delivery_id=delivery_id,
            stage=payload.stage,
            recorded_name=event.name,
            status=session.status,
            duplicate=duplicate,
            conversation_sequence=event.conversation_sequence,
            updated_at=session.updated_at,
        )

    @router.post("/providers/livekit/voice/sessions/{realtime_session_id}/disconnect", response_model=ProviderSessionLifecycleResponse)
    def disconnect_livekit_voice_session(
        realtime_session_id: str,
        payload: ProviderSessionLifecycleRequest | None = None,
        x_ruhu_provider_secret: str | None = Header(default=None, alias="X-Ruhu-Provider-Secret"),
    ) -> ProviderSessionLifecycleResponse:
        require_provider_secret(x_ruhu_provider_secret)
        request_payload = payload or ProviderSessionLifecycleRequest()
        return transition_realtime_session_by_id(
            realtime_session_id=realtime_session_id,
            target="disconnected",
            reason=request_payload.reason,
            metadata=request_payload.metadata,
        )

    @router.post("/providers/livekit/voice/sessions/{realtime_session_id}/end", response_model=ProviderSessionLifecycleResponse)
    def end_livekit_voice_session(
        realtime_session_id: str,
        payload: ProviderSessionLifecycleRequest | None = None,
        x_ruhu_provider_secret: str | None = Header(default=None, alias="X-Ruhu-Provider-Secret"),
    ) -> ProviderSessionLifecycleResponse:
        require_provider_secret(x_ruhu_provider_secret)
        request_payload = payload or ProviderSessionLifecycleRequest()
        return transition_realtime_session_by_id(
            realtime_session_id=realtime_session_id,
            target="ended",
            reason=request_payload.reason,
            metadata=request_payload.metadata,
        )

    @router.post("/providers/livekit/voice/sessions/{realtime_session_id}/error", response_model=ProviderSessionLifecycleResponse)
    def error_livekit_voice_session(
        realtime_session_id: str,
        payload: ProviderSessionLifecycleRequest | None = None,
        x_ruhu_provider_secret: str | None = Header(default=None, alias="X-Ruhu-Provider-Secret"),
    ) -> ProviderSessionLifecycleResponse:
        require_provider_secret(x_ruhu_provider_secret)
        request_payload = payload or ProviderSessionLifecycleRequest()
        return transition_realtime_session_by_id(
            realtime_session_id=realtime_session_id,
            target="errored",
            reason=request_payload.reason,
            metadata=request_payload.metadata,
        )

    @router.post("/internal/realtime/voice-sessions/reconcile", response_model=VoiceSessionReconcileResponse)
    def reconcile_voice_sessions(payload: VoiceSessionReconcileRequest, request: Request) -> VoiceSessionReconcileResponse:
        require_internal_api_access(request)
        if realtime_control_plane is None:
            raise HTTPException(status_code=503, detail="realtime control plane is not configured")
        cutoff = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() - payload.stale_seconds,
            tz=timezone.utc,
        )
        reconciled = realtime_control_plane.reconcile_stale_sessions(
            channel="phone",
            provider=payload.provider,
            surface="voice",
            last_seen_before=cutoff,
            reason="reconciled_stale_voice_session",
            limit=payload.limit,
        )
        return VoiceSessionReconcileResponse(
            reconciled=len(reconciled),
            sessions=[build_session_lifecycle_response(session) for session in reconciled],
        )

    return router
