"""Voice-session lifecycle + LiveKit webhook routes — extracted from api.py
(RP-3.1 step 12, blueprint group 12).

Covers the browser voice-testing surface (`/voice-sessions/*`: health,
create, list, active count, status, delete — SYNC-KERNEL: ``create`` calls
``kernel.start_conversation`` directly) and the LiveKit server webhook
(`POST /providers/livekit/webhooks`). Registration order inside this router
preserves the original inline order (hazard H2: ``/voice-sessions/health``
and ``/voice-sessions/active/count`` register before
``/voice-sessions/{session_id}``).

The voice DTOs still live in ``ruhu.api``, so this module is imported by
``create_app()`` AT THE MOUNT SITE rather than at api.py's module top.
LiveKit runtime clients (token issuer, room runtime, dispatch) are resolved
per-request from ``app.state`` via zero-arg callables threaded from
``create_app()`` — the same seam the phone-numbers router uses, so tests can
keep overriding ``app.state`` after construction.

``voice_interaction_policy_metadata`` and
``normalize_livekit_dispatch_result`` are module-level pure helpers here
because the public-widget voice routes (blueprint group 17, step 13) share
them; api.py rebinds the old closure names until that step lands.

No ``tags=`` / ``prefix=`` and unchanged handler names (hazard H1).
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status

# DTOs at module top (hazard H7: PEP 563 handler annotations resolve against
# this module's globals).
from ..api import (
    VoiceHealthResponse,
    VoiceSessionCreateRequest,
    VoiceSessionEndRequest,
    VoiceSessionParticipant,
    VoiceSessionResponse,
    VoiceSessionStatusResponse,
    VoiceSessionSummaryResponse,
    _livekit_transport_payload,
)
from ..api_auth import RequestAuthContext
from ..auth_deps import make_author_context_dep, make_org_context_dep
from ..interaction_pacing import pacing_policy_for_channel
from ..livekit_adapter import LiveKitAgentsUnavailableError, load_livekit_api_sdk
from ..services.org_scope import (
    make_required_author_organization_id,
    user_id_for_context,
)

if TYPE_CHECKING:
    from ..agent_document import AgentDocument, Step
    from ..kernel import ConversationKernel
    from ..realtime import RealtimeControlPlane, RealtimeSession
    from ..registry import SQLAlchemyAgentRegistry
    from ..schemas import ConversationState


def _voice_session_duration_seconds(session: "RealtimeSession") -> int | None:
    end_time = session.ended_at or datetime.now(timezone.utc)
    elapsed = int((end_time - session.started_at).total_seconds())
    if elapsed < 0:
        return 0
    return elapsed


def _voice_room_name(session: "RealtimeSession") -> str | None:
    provider_session = (session.provider_session_id or "").strip()
    if provider_session:
        return provider_session
    metadata_room = str(session.transport_metadata.get("room_name") or "").strip()
    if metadata_room:
        return metadata_room
    return None


def normalize_livekit_dispatch_result(dispatch_result: object) -> dict[str, object]:
    if isinstance(dispatch_result, Mapping):
        return {str(key): value for key, value in dispatch_result.items()}
    as_dict = getattr(dispatch_result, "as_dict", None)
    if callable(as_dict):
        payload = as_dict()
        if isinstance(payload, Mapping):
            return {str(key): value for key, value in payload.items()}
    payload: dict[str, object] = {}
    for key in ("strategy", "attempted", "applied", "room_name", "agent_name", "dispatch_id", "error", "metadata"):
        if hasattr(dispatch_result, key):
            payload[key] = getattr(dispatch_result, key)
    return payload


def _interaction_pacing_overrides_for_step(step: "Step") -> dict[str, object]:
    overrides: dict[str, object] = {}
    slow_threshold_ms = getattr(step, "slow_threshold_ms", None)
    if slow_threshold_ms is not None:
        overrides["slow_threshold_ms"] = slow_threshold_ms
    soft_timeout_ms = getattr(step, "soft_timeout_ms", None)
    if soft_timeout_ms is not None:
        overrides["soft_timeout_ms"] = soft_timeout_ms
    endpointing_ms = getattr(step, "endpointing_ms", None)
    if endpointing_ms is not None:
        overrides["endpointing_ms"] = endpointing_ms
    turn_eagerness = getattr(step, "turn_eagerness", None)
    if turn_eagerness is not None:
        overrides["turn_eagerness"] = turn_eagerness
    interruptibility_policy = getattr(step, "interruptibility_policy", None)
    if interruptibility_policy is not None:
        overrides["interruptibility_policy"] = interruptibility_policy
    return overrides


def _resolved_voice_interaction_policy(
    *,
    agent_document: "AgentDocument",
    step_id: str | None,
    channel: str,
) -> dict[str, object] | None:
    if not step_id:
        return None
    try:
        step = agent_document.step_by_id(step_id)
    except KeyError:
        return None
    pacing = pacing_policy_for_channel(
        channel,
        overrides=_interaction_pacing_overrides_for_step(step),
    )
    return {
        "step_id": step.id,
        "endpointing_ms": pacing.endpointing_ms,
        "soft_timeout_ms": pacing.soft_timeout_ms,
        "turn_eagerness": pacing.turn_eagerness,
        "interruptibility_policy": pacing.interruptibility_policy,
    }


def voice_interaction_policy_metadata(
    *,
    agent_document: "AgentDocument",
    step_id: str | None,
    channel: str,
) -> dict[str, object]:
    policy = _resolved_voice_interaction_policy(
        agent_document=agent_document,
        step_id=step_id,
        channel=channel,
    )
    if policy is None:
        return {}
    return {"voice_interaction_policy": policy}


def build_voice_sessions_router(
    *,
    kernel: "ConversationKernel",
    agent_registry: "SQLAlchemyAgentRegistry",
    realtime_control_plane: "RealtimeControlPlane | None",
    auth_enabled: bool,
    bootstrap_organization_id: str | None,
    resolve_live_agent_snapshot: Callable,
    livekit_phone_adapter_config: object | None,
    livekit_phone_adapter_config_state: Callable[[], object | None],
    livekit_room_runtime_client_state: Callable[[], object | None],
    livekit_token_issuer_state: Callable[[], object | None],
    livekit_dispatch_client_state: Callable[[], object | None],
) -> APIRouter:
    """Build the voice-sessions + LiveKit-webhook router.

    ``resolve_live_agent_snapshot`` is create_app()'s
    ``_resolve_live_agent_snapshot`` closure (shared with the channel-ingress
    service — it stays in api.py until the channels extraction at blueprint
    step 14). The ``*_state`` callables read ``app.state`` per request.
    ``livekit_phone_adapter_config`` is threaded directly (construction-time
    value) for webhook signature verification, mirroring the closure the
    inline route captured.
    """
    router = APIRouter()

    _require_org_context = make_org_context_dep(auth_enabled)
    _require_runtime_author_context = make_author_context_dep(auth_enabled)
    _required_author_organization_id = make_required_author_organization_id(
        bootstrap_organization_id=bootstrap_organization_id,
    )
    _user_id_for_context = user_id_for_context

    def _voice_session_agent_name(*, conversation_id: str, organization_id: str | None) -> str:
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            return "Unknown agent"
        try:
            registration = agent_registry.get_agent_registration(
                conversation.agent_id,
                organization_id=organization_id,
            )
            return registration.name
        except KeyError:
            return conversation.agent_id

    def _voice_session_agent_id(*, conversation_id: str) -> str:
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            return "unknown"
        return conversation.agent_id

    def _voice_session_summary(session: "RealtimeSession") -> VoiceSessionSummaryResponse:
        return VoiceSessionSummaryResponse(
            id=session.realtime_session_id,
            agent_id=_voice_session_agent_id(conversation_id=session.conversation_id),
            agent_name=_voice_session_agent_name(
                conversation_id=session.conversation_id,
                organization_id=session.organization_id,
            ),
            conversation_id=session.conversation_id,
            room_name=_voice_room_name(session),
            status=session.status,
            started_at=session.started_at,
            ended_at=session.ended_at,
            duration_seconds=_voice_session_duration_seconds(session),
        )

    def _verify_livekit_webhook_signature(*, raw_body: bytes, authorization: str | None) -> None:
        if livekit_phone_adapter_config is None:
            raise HTTPException(status_code=503, detail="livekit webhook is not configured")
        raw_authorization = (authorization or "").strip()
        if not raw_authorization:
            raise HTTPException(status_code=403, detail="missing livekit webhook authorization")
        token = raw_authorization
        if raw_authorization.lower().startswith("bearer "):
            token = raw_authorization[7:].strip()
        if not token:
            raise HTTPException(status_code=403, detail="missing livekit webhook authorization")
        try:
            livekit_api = load_livekit_api_sdk()
            token_verifier_cls = getattr(livekit_api, "TokenVerifier", None)
            webhook_receiver_cls = getattr(livekit_api, "WebhookReceiver", None)
            if token_verifier_cls is None or webhook_receiver_cls is None:
                raise HTTPException(status_code=503, detail="livekit webhook verification is unavailable")
            verifier = token_verifier_cls(
                livekit_phone_adapter_config.api_key,
                livekit_phone_adapter_config.api_secret,
            )
            receiver = webhook_receiver_cls(verifier)
            receiver.receive(raw_body.decode("utf-8"), token)
        except LiveKitAgentsUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=403, detail="invalid livekit webhook signature") from exc

    @router.get("/voice-sessions/health", response_model=VoiceHealthResponse)
    async def get_voice_sessions_health(
        context: RequestAuthContext | None = Depends(_require_org_context),
    ) -> VoiceHealthResponse:
        if context is not None and context.principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        configured_livekit = livekit_phone_adapter_config_state()
        room_runtime_client = livekit_room_runtime_client_state()
        issuer = livekit_token_issuer_state()
        if room_runtime_client is None:
            return VoiceHealthResponse(
                voice_available=True,
                livekit_reachable=False,
                mock=True,
            )
        livekit_reachable = await room_runtime_client.ping()
        transport_available = realtime_control_plane is not None and issuer is not None
        voice_available = livekit_reachable and transport_available
        return VoiceHealthResponse(
            voice_available=voice_available,
            livekit_reachable=livekit_reachable,
            mock=configured_livekit is None,
        )

    @router.post("/voice-sessions", response_model=VoiceSessionResponse)
    async def create_voice_session(
        payload: VoiceSessionCreateRequest,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> VoiceSessionResponse:
        if realtime_control_plane is None:
            raise HTTPException(status_code=503, detail="realtime control plane is not configured")
        issuer = livekit_token_issuer_state()
        if issuer is None:
            raise HTTPException(status_code=503, detail="livekit voice transport is not configured")
        organization_id = _required_author_organization_id(context)
        user_id = _user_id_for_context(context)

        snapshot = resolve_live_agent_snapshot(payload.agent_id, organization_id=organization_id)
        if payload.canvas_version_id:
            try:
                canvas_version = agent_registry.get_version_snapshot(
                    payload.canvas_version_id,
                    organization_id=organization_id,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            if canvas_version.agent_id != payload.agent_id:
                raise HTTPException(status_code=409, detail="canvas_version_id belongs to a different agent")

        conversation: ConversationState | None = None
        if payload.conversation_id:
            conversation = kernel.load_conversation(payload.conversation_id)
            if conversation is None:
                raise HTTPException(status_code=404, detail="unknown conversation id")
            if conversation.organization_id != organization_id:
                raise HTTPException(status_code=404, detail="unknown conversation id")
            if conversation.agent_id != payload.agent_id:
                raise HTTPException(status_code=409, detail="conversation belongs to a different agent")
        else:
            conversation_id = str(uuid4())
            try:
                kernel.start_conversation(
                    conversation_id,
                    agent_document=snapshot.agent_document,
                    agent_id=snapshot.agent_id,
                    agent_name=snapshot.name,
                    agent_version_id=snapshot.version_id,
                    mode="live",
                    channel="browser",
                    organization_id=organization_id,
                    metadata=dict(payload.metadata),
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            conversation = kernel.load_conversation(conversation_id)
            if conversation is None:
                raise HTTPException(status_code=500, detail="conversation was not created")

        session = realtime_control_plane.create_session(
            conversation_id=conversation.conversation_id,
            organization_id=organization_id,
            surface="voice",
            channel="browser",
            modality="audio",
            provider="livekit",
            external_session_key=str(uuid4()),
            participant_identity=None if user_id is None else f"user:{user_id}",
            transport_metadata=dict(payload.metadata),
        )
        voice_policy_metadata = voice_interaction_policy_metadata(
            agent_document=snapshot.agent_document,
            step_id=conversation.step_id,
            channel="browser",
        )
        try:
            transport = issuer.issue_voice_transport(
                channel="browser",
                conversation_id=conversation.conversation_id,
                realtime_session_id=session.realtime_session_id,
                participant_identity=session.participant_identity,
                participant_name=None,
                metadata={
                    **dict(payload.metadata),
                    "agent_id": payload.agent_id,
                    "agent_version_id": snapshot.version_id,
                    "conversation_id": conversation.conversation_id,
                    **voice_policy_metadata,
                },
            )
        except LiveKitAgentsUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        transport_payload = _livekit_transport_payload(transport)
        room_name = str(transport_payload.get("room_name") or "").strip()
        if not room_name:
            raise HTTPException(status_code=503, detail="livekit transport did not return room metadata")

        dispatch_client = livekit_dispatch_client_state()
        if dispatch_client is not None:
            dispatch_result = await dispatch_client.create_dispatch(
                room_name=room_name,
                metadata={
                    "conversation_id": conversation.conversation_id,
                    "realtime_session_id": session.realtime_session_id,
                    "agent_id": payload.agent_id,
                    "agent_version_id": snapshot.version_id,
                    "channel": "browser",
                    "room_name": room_name,
                    "metadata": {
                        **dict(payload.metadata),
                        **voice_policy_metadata,
                    },
                },
                agent_name=str(transport_payload.get("agent_name") or issuer.config.agent_name),
            )
            normalized_dispatch_result = normalize_livekit_dispatch_result(dispatch_result)
            dispatch_payload = dict(transport_payload.get("dispatch", {}))
            dispatch_payload.update(normalized_dispatch_result)
            transport_payload["dispatch"] = dispatch_payload
            if bool(normalized_dispatch_result.get("attempted")) and not bool(normalized_dispatch_result.get("applied")):
                raise HTTPException(
                    status_code=503,
                    detail=str(normalized_dispatch_result.get("error") or "livekit agent dispatch failed"),
                )

        realtime_control_plane.touch_session(
            session.realtime_session_id,
            provider_session_id=room_name,
            participant_identity=str(transport_payload.get("participant_identity") or session.participant_identity or ""),
            metadata={
                **dict(session.transport_metadata),
                **dict(payload.metadata),
                "agent_id": payload.agent_id,
                "agent_version_id": snapshot.version_id,
                "room_name": room_name,
                "agent_name": transport_payload.get("agent_name"),
                "sdk_version_target": transport_payload.get("sdk_version_target"),
                "voice_mode": transport_payload.get("voice_mode"),
                "dispatch_strategy": transport_payload.get("dispatch_strategy"),
                "dispatch": transport_payload.get("dispatch"),
                **voice_policy_metadata,
            },
        )

        return VoiceSessionResponse(
            id=session.realtime_session_id,
            organization_id=organization_id,
            agent_id=payload.agent_id,
            agent_name=snapshot.name,
            conversation_id=conversation.conversation_id,
            canvas_version_id=payload.canvas_version_id,
            room_name=room_name,
            status=session.status,
            started_at=session.started_at,
            ended_at=session.ended_at,
            duration_seconds=_voice_session_duration_seconds(session),
            access_token=str(transport_payload.get("token") or ""),
            connection_url=str(transport_payload.get("url") or ""),
            metadata=dict(payload.metadata),
        )

    @router.get("/voice-sessions", response_model=list[VoiceSessionSummaryResponse])
    def list_voice_sessions(
        request: Request,
        status_filter: Literal["active", "ended", "all"] = Query(default="all"),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> list[VoiceSessionSummaryResponse]:
        if realtime_control_plane is None:
            raise HTTPException(status_code=503, detail="realtime control plane is not configured")
        organization_id = _required_author_organization_id(context)
        sessions = realtime_control_plane.sessions.list_for_org(
            organization_id=organization_id,
            channel="browser",
            surface="voice",
            provider="livekit",
            limit=max(limit * 2, limit),
            offset=offset,
        )
        if status_filter == "active":
            sessions = [session for session in sessions if session.status == "active"]
        elif status_filter == "ended":
            sessions = [session for session in sessions if session.status != "active"]
        return [_voice_session_summary(session) for session in sessions[:limit]]

    @router.get("/voice-sessions/active/count")
    def get_active_voice_session_count(
        request: Request,
        context: RequestAuthContext | None = Depends(_require_org_context),
    ) -> dict[str, int]:
        if realtime_control_plane is None:
            raise HTTPException(status_code=503, detail="realtime control plane is not configured")
        organization_id = _required_author_organization_id(context)
        active_count = realtime_control_plane.sessions.count_active(
            organization_id=organization_id,
            channel="browser",
            surface="voice",
            provider="livekit",
        )
        return {"active_sessions": active_count}

    @router.get("/voice-sessions/{session_id}", response_model=VoiceSessionStatusResponse)
    async def get_voice_session_status(
        session_id: str,
        request: Request,
        context: RequestAuthContext | None = Depends(_require_org_context),
    ) -> VoiceSessionStatusResponse:
        if realtime_control_plane is None:
            raise HTTPException(status_code=503, detail="realtime control plane is not configured")
        organization_id = _required_author_organization_id(context)
        session = realtime_control_plane.sessions.load(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        if (
            session.organization_id != organization_id
            or session.surface != "voice"
            or session.channel != "browser"
            or session.provider != "livekit"
        ):
            raise HTTPException(status_code=404, detail="session not found")

        participants: list[VoiceSessionParticipant] = []
        room_name = _voice_room_name(session)
        room_client = livekit_room_runtime_client_state()
        if session.status == "active" and room_name and room_client is not None:
            try:
                raw_participants = await room_client.get_room_participants(room_name=room_name)
            except Exception:
                raw_participants = []
            for item in raw_participants:
                participants.append(
                    VoiceSessionParticipant(
                        identity=None if item.get("identity") is None else str(item.get("identity")),
                        name=None if item.get("name") is None else str(item.get("name")),
                        joined_at=None if item.get("joined_at") is None else str(item.get("joined_at")),
                    )
                )

        return VoiceSessionStatusResponse(
            id=session.realtime_session_id,
            room_name=room_name,
            status=session.status,
            num_participants=len(participants),
            participants=participants,
            started_at=session.started_at,
            duration_seconds=_voice_session_duration_seconds(session),
        )

    @router.delete("/voice-sessions/{session_id}", status_code=204)
    async def end_voice_session(
        session_id: str,
        request: Request,
        payload: VoiceSessionEndRequest | None = None,
        context: RequestAuthContext | None = Depends(_require_runtime_author_context),
    ) -> Response:
        if realtime_control_plane is None:
            raise HTTPException(status_code=503, detail="realtime control plane is not configured")
        organization_id = _required_author_organization_id(context)
        session = realtime_control_plane.sessions.load(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        if (
            session.organization_id != organization_id
            or session.surface != "voice"
            or session.channel != "browser"
            or session.provider != "livekit"
        ):
            raise HTTPException(status_code=404, detail="session not found")

        room_name = _voice_room_name(session)
        room_client = livekit_room_runtime_client_state()
        if room_name and room_client is not None:
            try:
                await room_client.delete_room(room_name=room_name)
            except Exception:
                pass

        realtime_control_plane.end_session(
            session.realtime_session_id,
            reason="api_delete" if payload is None or payload.reason is None else payload.reason,
            metadata={"source": "voice_sessions_api"},
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/providers/livekit/webhooks")
    async def ingest_livekit_webhook(
        request: Request,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> dict[str, str]:
        raw_body = await request.body()
        _verify_livekit_webhook_signature(raw_body=raw_body, authorization=authorization)
        if realtime_control_plane is None:
            return {"status": "ignored"}
        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except ValueError:
            return {"status": "ignored"}
        if not isinstance(payload, dict):
            return {"status": "ignored"}
        event_name = str(payload.get("event") or "").strip()
        room_payload = payload.get("room")
        room_name = ""
        if isinstance(room_payload, dict):
            room_name = str(room_payload.get("name") or "").strip()
        if not room_name:
            return {"status": "ignored"}

        session = realtime_control_plane.sessions.load_by_room_name(
            room_name=room_name,
            provider="livekit",
            surface="voice",
        )
        if session is None:
            return {"status": "ignored"}

        metadata = {
            "source": "livekit_webhook",
            "event": event_name,
            "room_name": room_name,
        }
        if event_name == "room_finished":
            realtime_control_plane.end_session(
                session.realtime_session_id,
                reason="room_finished",
                metadata=metadata,
            )
            return {"status": "ok"}
        if event_name == "participant_disconnected":
            participant_payload = payload.get("participant")
            participant_identity = ""
            if isinstance(participant_payload, dict):
                participant_identity = str(participant_payload.get("identity") or "").strip()
            if participant_identity and participant_identity.lower().startswith("agent"):
                return {"status": "ignored"}
            realtime_control_plane.disconnect_session(
                session.realtime_session_id,
                reason="participant_disconnected",
                metadata={**metadata, "participant_identity": participant_identity},
            )
            return {"status": "ok"}
        return {"status": "ignored"}

    return router
