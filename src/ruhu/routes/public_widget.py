"""Public widget routes — extracted from api.py (RP-3.1 step 13, blueprint
group 17, the fattest group).

Two builders, mounted at the two positions the inline blocks occupied
(hazard H2: registration order is contract):

- ``build_public_widget_config_router`` — ``GET /public/widget/config``
  (registered early, right after the health/console routers);
- ``build_public_widget_router`` — the ``/public/widget/sessions`` surface:
  create/resume, session read, heartbeat/end/token-refresh, the
  conversation-events SSE stream + replay, tool-invocation list/confirm/
  cancel, voice start/disconnect, message POST + SSE stream, and the
  analytics-events ingest (deferred from step 9).

THE POINT OF THE ARC (step 11 finale): the message POST route calls
``turn_service.process_turn`` and the SSE stream route calls
``turn_service.aprocess_turn`` DIRECTLY — the identical call
``/conversations/{id}/turns`` makes. No turn logic remains in any route.

Session-token verification lives in
``ruhu.services.widget_sessions.WidgetSessionAccessService``; voice-policy
helpers are shared with ``routes.voice_sessions``. SYNC-KERNEL group: the
turn paths keep the explicit kernel executor via ``aprocess_turn``; LiveKit
runtime clients and the pg-notify dispatcher are resolved per-request from
``app.state`` via zero-arg callables threaded from ``create_app()``.

The widget DTOs still live in ``ruhu.api``, so this module is imported by
``create_app()`` AT THE MOUNT SITE rather than at api.py's module top
(hazard H7: DTO imports sit at module top here so PEP 563 annotations
resolve). No ``tags=`` / ``prefix=`` and unchanged handler names (H1).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

# DTOs at module top (hazard H7: PEP 563 handler annotations resolve against
# this module's globals).
from ..api import (
    WidgetConfigResponse,
    WidgetEventBatchRequest,
    WidgetMessageRequest,
    WidgetMessageResponse,
    WidgetSessionCreateRequest,
    WidgetSessionResponse,
    WidgetVoiceDisconnectRequest,
    WidgetVoiceDisconnectResponse,
    WidgetVoiceSessionRequest,
    WidgetVoiceSessionResponse,
    _livekit_transport_payload,
)
from ..auth_deps import make_reviewer_context_dep
from ..livekit_adapter import LiveKitAgentsUnavailableError
from ..realtime import RealtimeEvent
from ..schemas import RuntimeTurn
from ..services.channel_ingress import LiveKitTransportResponse
from ..services.org_scope import organization_id_for_context
from ..services.widget_sessions import (
    WIDGET_SESSION_TOKEN_METADATA_KEY,
    extract_request_origin,
    hash_widget_session_token,
    issue_widget_session_token,
    validate_widget_origin,
)
from ..tools.types import ToolInvocation
from .voice_sessions import (
    normalize_livekit_dispatch_result,
    voice_interaction_policy_metadata,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker

    from ..kernel import ConversationKernel
    from ..realtime import RealtimeControlPlane, RealtimeSession
    from ..registry import SQLAlchemyAgentRegistry
    from ..services.conversation_turns import ConversationTurnService
    from ..services.widget_sessions import WidgetSessionAccessService

logger = logging.getLogger(__name__)

_PUBLIC_WIDGET_MESSAGE_STREAM_ERROR = {
    "error": "message_processing_failed",
    "detail": "We couldn't process that message right now. Please try again.",
}


def _resolve_widget_dedupe_key(
    payload: WidgetMessageRequest,
    *,
    conversation_id: str,
    attachment_ids: list[str],
) -> str:
    """Return the dedupe key for a widget message turn.

    Prefer the client-supplied ``payload.dedupe_key`` — that is the
    only signal that distinguishes a *retry* (same key, must dedupe)
    from a *legitimate identical message* (different key, must process).
    When the client omits it, synthesise a deterministic fallback from
    the message content + attachment ids. Identical-content retries
    within the same conversation dedupe, but a customer who deliberately
    sends the same text twice will see only one turn. Clients should send
    an explicit per-send nonce.
    """
    if payload.dedupe_key:
        return payload.dedupe_key.strip()
    digest = hashlib.sha256()
    digest.update(conversation_id.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(payload.text.encode("utf-8"))
    for att in sorted(attachment_ids):
        digest.update(b"\x00")
        digest.update(att.encode("utf-8"))
    return f"widget-fallback:{digest.hexdigest()[:32]}"


def build_public_widget_config_router(
    *,
    widget_config: Callable[[str], WidgetConfigResponse],
) -> APIRouter:
    """Build the unauthenticated ``GET /public/widget/config`` router.

    ``widget_config`` is the single H6 implementation
    (``ruhu.services.widget_config.make_widget_config`` product) — the same
    callable the ConversationTurnService's ``company_name_lookup`` wraps.
    """
    router = APIRouter()

    @router.get("/public/widget/config", response_model=WidgetConfigResponse)
    def get_public_widget_config(
        response: Response,
        agent_id: str = Query(...),
    ) -> WidgetConfigResponse:
        # Short cache so a persona/widget edit reaches end customers within a
        # minute. This endpoint is hot (every widget load), so cache pressure
        # matters; ``max-age=60`` is the smallest value that meaningfully
        # reduces load while keeping iteration fast.
        response.headers["Cache-Control"] = "public, max-age=60"
        return widget_config(agent_id)

    return router


def build_public_widget_router(
    *,
    kernel: "ConversationKernel",
    agent_registry: "SQLAlchemyAgentRegistry",
    realtime_control_plane: "RealtimeControlPlane | None",
    turn_service: "ConversationTurnService",
    widget_session_access: "WidgetSessionAccessService",
    auth_session_factory: "sessionmaker",
    auth_enabled: bool,
    widget_transcript_history: Callable,
    widget_messages_from_rendered: Callable,
    pending_tool_invocations: Callable,
    resolve_conversation_attachment_refs: Callable,
    load_livekit_voice_session: Callable,
    latest_livekit_voice_session: Callable,
    disconnect_superseded_widget_voice_sessions: Callable,
    voice_transport_metadata: Callable,
    build_session_lifecycle_response: Callable,
    livekit_token_issuer_state: Callable[[], object | None],
    livekit_dispatch_client_state: Callable[[], object | None],
    pg_notify_dispatcher_state: Callable[[], object | None],
) -> APIRouter:
    """Build the ``/public/widget/sessions`` router.

    The transcript/pending-invocation projections, the attachment-ref
    resolver, and the LiveKit voice-session helpers are create_app()
    closures shared with groups 18/20/21 — they stay in api.py and thread
    in as explicit kwargs (the voice-sessions/phone-numbers precedent).
    The ``*_state`` callables read ``app.state`` per request.
    """
    router = APIRouter()

    _require_public_widget_session_access = (
        widget_session_access.require_public_widget_session_access
    )
    _require_runtime_reviewer_context = make_reviewer_context_dep(auth_enabled)
    _organization_id_for_context = organization_id_for_context

    @router.post("/public/widget/sessions", response_model=WidgetSessionResponse)
    def create_public_widget_session(payload: WidgetSessionCreateRequest, request: Request) -> WidgetSessionResponse:
        from sqlalchemy import select as _sa_select

        from ..db_models import ApiKeyRecord, WidgetSessionRecord

        if payload.channel != "web_widget":
            raise HTTPException(status_code=400, detail="public widget sessions must use the web_widget channel")

        # ── 1. Require + resolve publishable key ──────────────────────────────
        # Every widget session MUST be anchored to a publishable key.  The key's
        # organization_id is the authoritative tenant for the session.
        raw_key = payload.publishable_key.strip()
        if not raw_key:
            raise HTTPException(status_code=400, detail="publishable_key required")
        pk_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        with auth_session_factory() as _pks:
            publishable_key_record = _pks.scalar(
                _sa_select(ApiKeyRecord).where(
                    ApiKeyRecord.key_hash == pk_hash,
                    ApiKeyRecord.key_type == "publishable",
                    ApiKeyRecord.is_active.is_(True),
                )
            )
        if publishable_key_record is None:
            raise HTTPException(status_code=401, detail="invalid or revoked publishable key")

        tenant_organization_id: str = publishable_key_record.organization_id
        key_agent_id: str | None = publishable_key_record.agent_id

        # ── 2. Enforce key ↔ agent binding ────────────────────────────────────
        if key_agent_id is not None and key_agent_id != payload.agent_id:
            raise HTTPException(
                status_code=403,
                detail="publishable key is bound to a different agent",
            )

        # ── 3. Validate origin ────────────────────────────────────────────────
        validate_widget_origin(
            request,
            list(getattr(publishable_key_record, "allowed_origins", None) or []),
        )
        request_origin = extract_request_origin(request)

        # ── 4. Draft access requires a reviewer session (pk alone is insufficient)
        if payload.target == "draft":
            draft_context = _require_runtime_reviewer_context(request)
            draft_context_org = _organization_id_for_context(draft_context)
            if draft_context_org != tenant_organization_id:
                raise HTTPException(
                    status_code=403,
                    detail="draft widget access must be authenticated in the same organization as the publishable key",
                )

        # ── 5. Resolve agent snapshot scoped to the key's tenant ──────────────
        # Bypass _resolve_agent_snapshot (which reads request context) — the
        # publishable key is the tenant anchor here, not the request session.
        try:
            if payload.target == "draft":
                version_id = agent_registry.resolve_version_id(
                    payload.agent_id,
                    target="draft",
                    organization_id=tenant_organization_id,
                )
            else:
                version_id = agent_registry.resolve_version_id(
                    payload.agent_id,
                    target="published",
                    organization_id=tenant_organization_id,
                )
            snapshot = agent_registry.get_version_snapshot(
                version_id,
                organization_id=tenant_organization_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        # ── 6. Cross-check agent org matches key org ──────────────────────────
        # System/template agents with organization_id=None are not addressable
        # via widget flow — clone into a tenant first.
        if snapshot.organization_id is None:
            raise HTTPException(
                status_code=403,
                detail="agent is not tenant-scoped and cannot be served via widget",
            )
        if snapshot.organization_id != tenant_organization_id:
            raise HTTPException(
                status_code=403,
                detail="agent belongs to a different organization",
            )

        # ── 7. Conversation-resume branch (verified cross-tenant safe) ───────
        if payload.conversation_id:
            existing = kernel.load_conversation(payload.conversation_id)
            if existing is not None:
                if existing.organization_id != tenant_organization_id:
                    raise HTTPException(
                        status_code=403,
                        detail="conversation belongs to a different organization",
                    )
                presented_token = _require_public_widget_session_access(
                    request,
                    existing,
                    explicit_token=payload.session_token,
                )
                if existing.agent_id != payload.agent_id:
                    raise HTTPException(status_code=409, detail="conversation belongs to a different agent")
                return WidgetSessionResponse(
                    conversation_id=existing.conversation_id,
                    agent_id=existing.agent_id,
                    step_id=existing.step_id,
                    resumed=True,
                    session_token=presented_token,
                    messages=widget_transcript_history(
                        existing.conversation_id,
                        organization_id=existing.organization_id,
                    ),
                    pending_tool_invocations=pending_tool_invocations(
                        existing.conversation_id,
                        organization_id=existing.organization_id,
                    ),
                )

        conversation_id = payload.conversation_id or str(uuid4())
        session_token = issue_widget_session_token()
        try:
            start = kernel.start_conversation(
                conversation_id,
                agent_document=snapshot.agent_document,
                agent_id=snapshot.agent_id,
                agent_name=snapshot.name,
                agent_version_id=snapshot.version_id,
                mode="live",
                channel=payload.channel,
                organization_id=tenant_organization_id,
                metadata={
                    WIDGET_SESSION_TOKEN_METADATA_KEY: hash_widget_session_token(session_token),
                    **(
                        {"anonymous_id": payload.anonymous_id.strip()}
                        if isinstance(payload.anonymous_id, str) and payload.anonymous_id.strip()
                        else {}
                    ),
                },
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        # ── 8. Dual-write: persist WidgetSessionRecord ────────────────────────
        # Best-effort — a failure here must not break the session response.
        # The conversation is already committed; the session row is supplementary.
        _now = datetime.now(timezone.utc)
        _token_expires_at = _now + timedelta(hours=24)
        _widget_session_id = str(uuid4())
        try:
            _widget_session = WidgetSessionRecord(
                session_id=_widget_session_id,
                organization_id=tenant_organization_id,
                conversation_id=conversation_id,
                publishable_key_id=publishable_key_record.key_id,
                anonymous_id=(
                    payload.anonymous_id.strip()
                    if isinstance(payload.anonymous_id, str) and payload.anonymous_id.strip()
                    else None
                ),
                origin=request_origin,
                ip_address=(
                    request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                    or (request.client.host if request.client else None)
                ),
                user_agent=request.headers.get("user-agent"),
                page_url=None,  # not sent by client in this call
                channel="web_widget",
                status="active",
                session_token_hash=hash_widget_session_token(session_token),
                token_expires_at=_token_expires_at,
                message_count=0,
                voice_duration_seconds=0,
                started_at=_now,
                ended_at=None,
                last_activity_at=_now,
                created_at=_now,
                updated_at=_now,
            )
            with auth_session_factory.begin() as _ws:
                _ws.add(_widget_session)
        except Exception:  # noqa: BLE001
            pass  # never break session creation over analytics persistence

        return WidgetSessionResponse(
            conversation_id=conversation_id,
            agent_id=snapshot.agent_id,
            step_id=start.step_after,
            resumed=False,
            session_token=session_token,
            messages=widget_messages_from_rendered(start.emitted_messages),
            pending_tool_invocations=pending_tool_invocations(
                conversation_id,
                organization_id=tenant_organization_id,
            ),
        )

    @router.get("/public/widget/sessions/{conversation_id}", response_model=WidgetSessionResponse)
    def get_public_widget_session(conversation_id: str, request: Request) -> WidgetSessionResponse:
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="unknown conversation id")
        presented_token = _require_public_widget_session_access(request, conversation)
        return WidgetSessionResponse(
            conversation_id=conversation.conversation_id,
            agent_id=conversation.agent_id,
            step_id=conversation.step_id,
            resumed=True,
            session_token=presented_token,
            messages=widget_transcript_history(
                conversation.conversation_id,
                organization_id=conversation.organization_id,
            ),
            pending_tool_invocations=pending_tool_invocations(
                conversation.conversation_id,
                organization_id=conversation.organization_id,
            ),
        )

    @router.post("/public/widget/sessions/{conversation_id}/heartbeat", status_code=204)
    def widget_session_heartbeat(conversation_id: str, request: Request) -> Response:
        """Update last_activity_at on the WidgetSessionRecord.

        Called by the client every ~30 s to prevent expiry sweeps from closing
        the session prematurely.  Best-effort — a DB failure returns 204 anyway
        so that a transient write error never kills an active chat session.
        """
        from sqlalchemy import select as sa_select

        from ..db_models import WidgetSessionRecord
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="unknown conversation id")
        _require_public_widget_session_access(request, conversation)
        _now = datetime.now(timezone.utc)
        try:
            with auth_session_factory.begin() as _s:
                ws = _s.scalar(
                    sa_select(WidgetSessionRecord).where(
                        WidgetSessionRecord.conversation_id == conversation_id
                    )
                )
                if ws is not None and ws.status == "active":
                    ws.last_activity_at = _now
                    ws.updated_at = _now
        except Exception:  # noqa: BLE001
            pass
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/public/widget/sessions/{conversation_id}/end", status_code=204)
    def end_widget_session(conversation_id: str, request: Request) -> Response:
        """Mark the WidgetSessionRecord as ended.

        Called when the user explicitly closes the widget.  Does not terminate
        any in-progress voice session (the client should call /voice/disconnect
        first).
        """
        from sqlalchemy import select as sa_select

        from ..db_models import WidgetSessionRecord
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="unknown conversation id")
        _require_public_widget_session_access(request, conversation)
        _now = datetime.now(timezone.utc)
        try:
            with auth_session_factory.begin() as _s:
                ws = _s.scalar(
                    sa_select(WidgetSessionRecord).where(
                        WidgetSessionRecord.conversation_id == conversation_id
                    )
                )
                if ws is not None and ws.status == "active":
                    ws.status = "ended"
                    ws.ended_at = _now
                    ws.updated_at = _now
        except Exception:  # noqa: BLE001
            pass
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/public/widget/sessions/{conversation_id}/token/refresh")
    def refresh_widget_session_token(conversation_id: str, request: Request) -> dict:
        """Issue a fresh session token and update token_expires_at.

        Called by the client when token_expires_at is within 60 s of expiring
        (or has already expired).  The new token replaces the old one; the old
        token is immediately invalid.

        ``allow_expired=True`` is passed so a client whose token already
        crossed the 24h TTL can still rotate — the token hash is still
        verified, so only a legitimate holder of the previous token can
        request a refresh.
        """
        from sqlalchemy import select as sa_select

        from ..db_models import WidgetSessionRecord
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="unknown conversation id")
        _require_public_widget_session_access(request, conversation, allow_expired=True)
        new_token = issue_widget_session_token()
        new_hash = hash_widget_session_token(new_token)
        _now = datetime.now(timezone.utc)
        new_expires = _now + timedelta(hours=24)
        # Update metadata hash (legacy auth path — mutate + save via conversation_store)
        conversation.metadata[WIDGET_SESSION_TOKEN_METADATA_KEY] = new_hash
        kernel.conversation_store.save(conversation)
        # Update WidgetSessionRecord (new auth path)
        try:
            with auth_session_factory.begin() as _s:
                ws = _s.scalar(
                    sa_select(WidgetSessionRecord).where(
                        WidgetSessionRecord.conversation_id == conversation_id
                    )
                )
                if ws is not None:
                    ws.session_token_hash = new_hash
                    ws.token_expires_at = new_expires
                    ws.updated_at = _now
        except Exception:  # noqa: BLE001
            pass
        return {
            "session_token": new_token,
            "token_expires_at": new_expires.isoformat(),
        }

    @router.get("/public/widget/sessions/{conversation_id}/conversation-events")
    async def stream_public_widget_conversation_events(
        conversation_id: str,
        request: Request,
        after_sequence: int = Query(default=0, ge=0),
        poll_interval_seconds: float = Query(default=2.0, ge=0.5, le=10.0),
    ) -> StreamingResponse:
        if realtime_control_plane is None:
            raise HTTPException(status_code=503, detail="realtime control plane is not configured")
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="unknown conversation id")
        _require_public_widget_session_access(request, conversation)

        try:
            from ..observability.metrics import sse_active_subscribers
            sse_active_subscribers.inc()
        except Exception:
            pass

        async def _stream() -> Any:
            try:
                cursor = after_sequence
                # Initial replay
                events = realtime_control_plane.events.replay(
                    conversation_id=conversation_id,
                    after_sequence=cursor,
                )
                for event in events:
                    cursor = max(cursor, event.conversation_sequence)
                    if event.visibility != "surface":
                        continue
                    yield b"event: conversation.event\n"
                    yield f"data: {event.model_dump_json()}\n\n".encode("utf-8")

                # Push mode via PgNotifyDispatcher if available, else poll fallback
                dispatcher = pg_notify_dispatcher_state()
                if dispatcher is not None and dispatcher.is_running:
                    async for sequence in dispatcher.subscribe(conversation_id):
                        if await request.is_disconnected():
                            break
                        if sequence <= cursor:
                            continue
                        events = realtime_control_plane.events.replay(
                            conversation_id=conversation_id,
                            after_sequence=cursor,
                        )
                        for event in events:
                            cursor = max(cursor, event.conversation_sequence)
                            if event.visibility != "surface":
                                continue
                            yield b"event: conversation.event\n"
                            yield f"data: {event.model_dump_json()}\n\n".encode("utf-8")
                else:
                    # Fallback: polling with longer interval (default 2s, not 0.25s)
                    while True:
                        if await request.is_disconnected():
                            break
                        events = realtime_control_plane.events.replay(
                            conversation_id=conversation_id,
                            after_sequence=cursor,
                        )
                        emitted = False
                        for event in events:
                            cursor = max(cursor, event.conversation_sequence)
                            if event.visibility != "surface":
                                continue
                            emitted = True
                            yield b"event: conversation.event\n"
                            yield f"data: {event.model_dump_json()}\n\n".encode("utf-8")
                        if not emitted:
                            yield b"event: heartbeat\n"
                            yield b"data: {}\n\n"
                        await asyncio.sleep(poll_interval_seconds)
            finally:
                try:
                    from ..observability.metrics import sse_active_subscribers
                    sse_active_subscribers.dec()
                except Exception:
                    pass

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/public/widget/sessions/{conversation_id}/conversation-events/replay", response_model=list[RealtimeEvent])
    def replay_public_widget_conversation_events(
        conversation_id: str,
        request: Request,
        after_sequence: int = Query(default=0, ge=0),
    ) -> list[RealtimeEvent]:
        if realtime_control_plane is None:
            raise HTTPException(status_code=503, detail="realtime control plane is not configured")
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="unknown conversation id")
        _require_public_widget_session_access(request, conversation)
        events = realtime_control_plane.events.replay(
            conversation_id=conversation_id,
            after_sequence=after_sequence,
        )
        return [event for event in events if event.visibility == "surface"]

    @router.get("/public/widget/sessions/{conversation_id}/tool-invocations", response_model=list[ToolInvocation])
    def list_public_widget_tool_invocations(conversation_id: str, request: Request) -> list[ToolInvocation]:
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="unknown conversation id")
        _require_public_widget_session_access(request, conversation)
        return pending_tool_invocations(
            conversation_id,
            organization_id=conversation.organization_id,
        )

    @router.post("/public/widget/sessions/{conversation_id}/voice", response_model=WidgetVoiceSessionResponse)
    async def start_public_widget_voice_session(
        conversation_id: str,
        payload: WidgetVoiceSessionRequest,
        request: Request,
    ) -> WidgetVoiceSessionResponse:
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="unknown conversation id")
        _require_public_widget_session_access(request, conversation)
        if realtime_control_plane is None:
            raise HTTPException(status_code=503, detail="realtime control plane is not configured")
        issuer = livekit_token_issuer_state()
        if issuer is None:
            raise HTTPException(status_code=503, detail="livekit voice transport is not configured")
        dispatch_client = livekit_dispatch_client_state()
        requested_session = load_livekit_voice_session(
            conversation_id,
            channel="web_widget",
            realtime_session_id=payload.realtime_session_id,
        )
        active_session = latest_livekit_voice_session(
            conversation_id,
            channel="web_widget",
            active_only=True,
        )
        latest_prior_session = latest_livekit_voice_session(
            conversation_id,
            channel="web_widget",
            active_only=False,
        )
        session: "RealtimeSession"
        prior_session = requested_session or active_session or latest_prior_session
        resumed = prior_session is not None
        resume_reason = (
            payload.resume_reason.strip()
            if isinstance(payload.resume_reason, str) and payload.resume_reason.strip()
            else "widget_voice_resume"
        )

        if requested_session is not None and requested_session.status == "active":
            disconnect_superseded_widget_voice_sessions(
                conversation_id=conversation_id,
                keep_session_id=requested_session.realtime_session_id,
                replacement_session_id=requested_session.realtime_session_id,
                requested_session_id=requested_session.realtime_session_id,
                replacement_reason=resume_reason,
            )
            touched_session = realtime_control_plane.touch_session(
                requested_session.realtime_session_id,
                participant_identity=requested_session.participant_identity or payload.participant_identity,
                metadata=voice_transport_metadata(
                    base_session=requested_session,
                    request_metadata=payload.metadata,
                ),
            )
            session = touched_session or requested_session
            realtime_control_plane.record_voice_lifecycle_event(
                session.realtime_session_id,
                name="resumed",
                payload={
                    "reason": resume_reason,
                    "requested_realtime_session_id": requested_session.realtime_session_id,
                    "prior_realtime_session_id": requested_session.realtime_session_id,
                },
            )
        elif active_session is not None:
            replacement_session_id = f"rs_{uuid4().hex}"
            disconnect_superseded_widget_voice_sessions(
                conversation_id=conversation_id,
                keep_session_id=None,
                replacement_session_id=replacement_session_id,
                requested_session_id=None if requested_session is None else requested_session.realtime_session_id,
                replacement_reason=resume_reason,
            )
            session = realtime_control_plane.create_session(
                conversation_id=conversation_id,
                organization_id=conversation.organization_id,
                surface="voice",
                channel="web_widget",
                modality="audio",
                provider="livekit",
                external_session_key=str(uuid4()),
                participant_identity=active_session.participant_identity or payload.participant_identity,
                transport_metadata=voice_transport_metadata(
                    base_session=active_session,
                    request_metadata=payload.metadata,
                ),
                parent_realtime_session_id=active_session.realtime_session_id,
                realtime_session_id=replacement_session_id,
            )
            realtime_control_plane.record_voice_lifecycle_event(
                session.realtime_session_id,
                name="resumed",
                payload={
                    "reason": resume_reason,
                    "requested_realtime_session_id": None if requested_session is None else requested_session.realtime_session_id,
                    "prior_realtime_session_id": active_session.realtime_session_id,
                },
            )
        elif prior_session is not None:
            session = realtime_control_plane.create_session(
                conversation_id=conversation_id,
                organization_id=conversation.organization_id,
                surface="voice",
                channel="web_widget",
                modality="audio",
                provider="livekit",
                external_session_key=str(uuid4()),
                participant_identity=prior_session.participant_identity or payload.participant_identity,
                transport_metadata=voice_transport_metadata(
                    base_session=prior_session,
                    request_metadata=payload.metadata,
                ),
                parent_realtime_session_id=prior_session.realtime_session_id,
            )
            realtime_control_plane.record_voice_lifecycle_event(
                session.realtime_session_id,
                name="resumed",
                payload={
                    "reason": resume_reason,
                    "requested_realtime_session_id": None if requested_session is None else requested_session.realtime_session_id,
                    "prior_realtime_session_id": prior_session.realtime_session_id,
                },
            )
        else:
            session = realtime_control_plane.create_session(
                conversation_id=conversation_id,
                organization_id=conversation.organization_id,
                surface="voice",
                channel="web_widget",
                modality="audio",
                provider="livekit",
                external_session_key=str(uuid4()),
                participant_identity=payload.participant_identity,
                transport_metadata=voice_transport_metadata(
                    base_session=None,
                    request_metadata=payload.metadata,
                ),
            )
        voice_policy_snapshot = agent_registry.get_version_snapshot(
            conversation.agent_version_id,
            organization_id=conversation.organization_id,
        )
        voice_policy_metadata = voice_interaction_policy_metadata(
            agent_document=voice_policy_snapshot.agent_document,
            step_id=conversation.step_id,
            channel="web_widget",
        )
        try:
            transport = issuer.issue_voice_transport(
                channel="web_widget",
                conversation_id=conversation_id,
                realtime_session_id=session.realtime_session_id,
                participant_identity=session.participant_identity or payload.participant_identity,
                participant_name=payload.participant_name,
                metadata={
                    **dict(session.transport_metadata),
                    **dict(payload.metadata),
                    "agent_id": conversation.agent_id,
                    "agent_version_id": conversation.agent_version_id,
                    "provider_session_id": session.provider_session_id or session.transport_metadata.get("room_name"),
                    **voice_policy_metadata,
                },
            )
        except LiveKitAgentsUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        transport_payload = _livekit_transport_payload(transport)
        if not transport_payload:
            raise HTTPException(status_code=503, detail="livekit voice transport did not return a usable grant")
        if dispatch_client is not None:
            dispatch_result = await dispatch_client.create_dispatch(
                room_name=str(transport_payload.get("room_name") or ""),
                metadata={
                    "conversation_id": conversation_id,
                    "realtime_session_id": session.realtime_session_id,
                    "agent_id": conversation.agent_id,
                    "agent_version_id": conversation.agent_version_id,
                    "channel": "web_widget",
                    "room_name": transport_payload.get("room_name"),
                    "provider_session_id": transport_payload.get("room_name"),
                    "participant_identity": transport_payload.get("participant_identity"),
                    "voice_mode": transport_payload.get("voice_mode"),
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
        transport_response = LiveKitTransportResponse(**transport_payload)
        realtime_control_plane.touch_session(
            session.realtime_session_id,
            provider_session_id=transport_response.room_name,
            participant_identity=transport_response.participant_identity,
            metadata={
                **dict(session.transport_metadata),
                **dict(payload.metadata),
                "room_name": transport_response.room_name,
                "agent_name": transport_response.agent_name,
                "sdk_version_target": transport_response.sdk_version_target,
                "voice_mode": transport_response.voice_mode,
                "dispatch_strategy": transport_response.dispatch_strategy,
                "dispatch": transport_response.dispatch,
                **voice_policy_metadata,
            },
        )
        return WidgetVoiceSessionResponse(
            conversation_id=conversation_id,
            realtime_session_id=session.realtime_session_id,
            resumed=resumed,
            step_after=conversation.step_id,
            transport=transport_response,
            pending_tool_invocations=pending_tool_invocations(
                conversation_id,
                organization_id=conversation.organization_id,
            ),
            )

    @router.post(
        "/public/widget/sessions/{conversation_id}/voice/disconnect",
        response_model=WidgetVoiceDisconnectResponse,
    )
    def disconnect_public_widget_voice_session(
        conversation_id: str,
        payload: WidgetVoiceDisconnectRequest,
        request: Request,
    ) -> WidgetVoiceDisconnectResponse:
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="unknown conversation id")
        _require_public_widget_session_access(request, conversation)
        if realtime_control_plane is None:
            raise HTTPException(status_code=503, detail="realtime control plane is not configured")
        session: "RealtimeSession | None" = None
        if payload.realtime_session_id:
            candidate = realtime_control_plane.sessions.load(payload.realtime_session_id)
            if (
                candidate is not None
                and candidate.conversation_id == conversation_id
                and candidate.provider == "livekit"
                and candidate.channel == "web_widget"
                and candidate.surface == "voice"
                and candidate.modality == "audio"
            ):
                session = candidate
        if session is None:
            session = latest_livekit_voice_session(
                conversation_id,
                channel="web_widget",
                active_only=True,
            )
        if session is None:
            return WidgetVoiceDisconnectResponse(disconnected=False, session=None)
        if session.status != "active":
            return WidgetVoiceDisconnectResponse(
                disconnected=False,
                session=build_session_lifecycle_response(session),
            )
        disconnected = realtime_control_plane.disconnect_session(
            session.realtime_session_id,
            reason=payload.reason or "widget_client_disconnected",
            metadata=dict(payload.metadata),
        )
        if disconnected is None:
            return WidgetVoiceDisconnectResponse(disconnected=False, session=None)

        # ── Phase 7: roll up voice duration to WidgetSessionRecord ───────────
        _voice_secs = 0
        if disconnected.ended_at is not None and disconnected.started_at is not None:
            _delta = disconnected.ended_at - disconnected.started_at
            _voice_secs = max(0, int(_delta.total_seconds()))
        if _voice_secs > 0:
            from sqlalchemy import select as sa_select

            from ..db_models import WidgetSessionRecord
            try:
                with auth_session_factory.begin() as _vs:
                    _wsr = _vs.scalar(
                        sa_select(WidgetSessionRecord).where(
                            WidgetSessionRecord.conversation_id == conversation_id
                        )
                    )
                    if _wsr is not None:
                        _wsr.voice_duration_seconds = (_wsr.voice_duration_seconds or 0) + _voice_secs
                        _wsr.last_activity_at = datetime.now(timezone.utc)
                        _wsr.updated_at = _wsr.last_activity_at
            except Exception:  # noqa: BLE001
                pass

        return WidgetVoiceDisconnectResponse(
            disconnected=True,
            session=build_session_lifecycle_response(disconnected),
        )

    @router.post("/public/widget/sessions/{conversation_id}/messages", response_model=WidgetMessageResponse)
    def send_public_widget_message(
        conversation_id: str,
        payload: WidgetMessageRequest,
        request: Request,
    ) -> WidgetMessageResponse:
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="unknown conversation id")
        _require_public_widget_session_access(request, conversation)
        try:
            snapshot = agent_registry.get_version_snapshot(
                conversation.agent_version_id,
                organization_id=conversation.organization_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        attachment_ids, attachment_refs = resolve_conversation_attachment_refs(
            conversation_id=conversation_id,
            organization_id=conversation.organization_id,
            attachment_ids=payload.attachment_ids,
        )

        try:
            result = turn_service.process_turn(
                conversation_id,
                RuntimeTurn(
                    turn_id=str(uuid4()),
                    dedupe_key=_resolve_widget_dedupe_key(
                        payload,
                        conversation_id=conversation_id,
                        attachment_ids=attachment_ids,
                    ),
                    channel="web_widget",
                    modality="text",
                    event_type="user_message",
                    text=payload.text,
                    attachments=attachment_refs,
                    metadata={
                        # Debug/trace hint only — first-class refs live on
                        # RuntimeTurn.attachments (spec §3).
                        **({"attachment_ids": attachment_ids} if attachment_ids else {}),
                        **(payload.metadata if isinstance(payload.metadata, dict) else {}),
                    },
                    received_at=datetime.now(timezone.utc),
                ),
                agent_document=snapshot.agent_document,
                agent_id=snapshot.agent_id,
                agent_name=snapshot.name,
                organization_id=conversation.organization_id,
            )
        except HTTPException:
            # Routes inside the kernel may raise HTTPException directly
            # (e.g. tool-policy violations) — let those bubble to FastAPI.
            raise
        except Exception:
            # Catch-all: LLM timeouts, circuit-breaker open, integration
            # job failures, DB transient errors. Log loudly; convert to a
            # 503 so the client knows to retry rather than re-rendering
            # an empty 500. The streaming sibling endpoint emits an SSE
            # error event — this non-stream path returns the same shape
            # via JSON for consistency.
            logger.exception(
                "public widget message processing failed",
                extra={
                    "conversation_id": conversation_id,
                    "agent_id": conversation.agent_id,
                },
            )
            raise HTTPException(
                status_code=503,
                detail=_PUBLIC_WIDGET_MESSAGE_STREAM_ERROR,
            )
        return WidgetMessageResponse(
            conversation_id=conversation_id,
            step_after=result.step_after,
            messages=result.emitted_messages,
            trace_id=result.trace_id,
            pending_tool_invocations=pending_tool_invocations(
                conversation_id,
                organization_id=conversation.organization_id,
            ),
        )

    @router.post("/public/widget/sessions/{conversation_id}/messages/stream")
    async def stream_public_widget_message(
        conversation_id: str,
        payload: WidgetMessageRequest,
        request: Request,
    ) -> StreamingResponse:
        """
        Process a widget message and stream the result via Server-Sent Events.

        Emits the following event types in order:
          - ``typing``  — immediately, so the client shows the typing indicator
          - ``message`` — once per emitted assistant/system message in the response
                          - ``done``    — final event carrying step_after, trace_id, and
                          pending_tool_invocations; signals the stream is complete
          - ``error``   — emitted instead of ``done`` if processing fails
        """
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="unknown conversation id")
        _require_public_widget_session_access(request, conversation)
        try:
            snapshot = agent_registry.get_version_snapshot(
                conversation.agent_version_id,
                organization_id=conversation.organization_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        attachment_ids, attachment_refs = resolve_conversation_attachment_refs(
            conversation_id=conversation_id,
            organization_id=conversation.organization_id,
            attachment_ids=payload.attachment_ids,
        )

        organization_id = conversation.organization_id
        turn = RuntimeTurn(
            turn_id=str(uuid4()),
            dedupe_key=_resolve_widget_dedupe_key(
                payload,
                conversation_id=conversation_id,
                attachment_ids=attachment_ids,
            ),
            channel="web_widget",
            modality="text",
            event_type="user_message",
            text=payload.text,
            attachments=attachment_refs,
            metadata={
                **({"attachment_ids": attachment_ids} if attachment_ids else {}),
                **(payload.metadata if isinstance(payload.metadata, dict) else {}),
            },
            received_at=datetime.now(timezone.utc),
        )

        async def _stream():
            # 1. Immediately signal that the agent is processing
            if await request.is_disconnected():
                return
            yield b"event: typing\ndata: " + json.dumps({"is_typing": True}).encode() + b"\n\n"

            try:
                if await request.is_disconnected():
                    return
                result = await turn_service.aprocess_turn(
                    request.app,
                    conversation_id,
                    turn,
                    agent_document=snapshot.agent_document,
                    agent_id=snapshot.agent_id,
                    agent_name=snapshot.name,
                    organization_id=organization_id,
                )
            except Exception:
                logger.exception(
                    "public widget message stream failed",
                    extra={
                        "conversation_id": conversation_id,
                        "agent_id": conversation.agent_id,
                    },
                )
                yield b"event: typing\ndata: " + json.dumps({"is_typing": False}).encode() + b"\n\n"
                yield (
                    b"event: error\ndata: "
                    + json.dumps(_PUBLIC_WIDGET_MESSAGE_STREAM_ERROR).encode()
                    + b"\n\n"
                )
                return

            # 2. Stop typing indicator
            if await request.is_disconnected():
                return
            yield b"event: typing\ndata: " + json.dumps({"is_typing": False}).encode() + b"\n\n"

            # 3. Emit each assistant/system message individually
            for msg in result.emitted_messages:
                if await request.is_disconnected():
                    return
                yield (
                    b"event: message\ndata: "
                    + json.dumps(
                        {
                            "role": msg.role,
                            "text": msg.text,
                            **({"message_type": msg.message_type} if msg.message_type else {}),
                            **({"payload": msg.payload} if msg.payload else {}),
                        }
                    ).encode()
                    + b"\n\n"
                )

            # 4. Done — carries state metadata the client needs
            pending = pending_tool_invocations(
                conversation_id,
                organization_id=organization_id,
            )
            done_payload = {
                "conversation_id": conversation_id,
                "step_after": result.step_after,
                "trace_id": result.trace_id,
                "pending_tool_invocations": [p.model_dump(mode="json") for p in pending],
            }
            if await request.is_disconnected():
                return
            yield b"event: done\ndata: " + json.dumps(done_payload).encode() + b"\n\n"

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post(
        "/public/widget/sessions/{conversation_id}/tool-invocations/{invocation_id}/confirm",
        response_model=WidgetMessageResponse,
    )
    def confirm_public_widget_tool_invocation(
        conversation_id: str,
        invocation_id: str,
        request: Request,
    ) -> WidgetMessageResponse:
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="unknown conversation id")
        _require_public_widget_session_access(request, conversation)
        try:
            result = turn_service.confirm_tool_invocation(
                conversation_id,
                invocation_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return WidgetMessageResponse(
            conversation_id=conversation_id,
            step_after=result.step_after,
            messages=result.emitted_messages,
            trace_id=result.trace_id,
            pending_tool_invocations=pending_tool_invocations(
                conversation_id,
                organization_id=conversation.organization_id,
            ),
        )

    @router.post(
        "/public/widget/sessions/{conversation_id}/tool-invocations/{invocation_id}/cancel",
        response_model=WidgetMessageResponse,
    )
    def cancel_public_widget_tool_invocation(
        conversation_id: str,
        invocation_id: str,
        request: Request,
    ) -> WidgetMessageResponse:
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="unknown conversation id")
        _require_public_widget_session_access(request, conversation)
        try:
            result = turn_service.cancel_tool_invocation(
                conversation_id,
                invocation_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return WidgetMessageResponse(
            conversation_id=conversation_id,
            step_after=result.step_after,
            messages=result.emitted_messages,
            trace_id=result.trace_id,
            pending_tool_invocations=pending_tool_invocations(
                conversation_id,
                organization_id=conversation.organization_id,
            ),
        )

    # ── Widget analytics ──────────────────────────────────────────────────
    # Schema classes (WidgetEventBatchRequest, WidgetAnalyticsSummary, etc.) are module-level.

    @router.post("/public/widget/sessions/{conversation_id}/events", status_code=202)
    def ingest_widget_events(
        conversation_id: str,
        payload: WidgetEventBatchRequest,
        request: Request,
    ) -> Response:
        """Receive client-emitted widget analytics events.

        Accepts a batch of up to 50 events per call.  Validation is minimal:
        unknown event types are accepted so the client can iterate freely
        without backend deploys.  Best-effort persistence — a write failure
        returns 202 anyway so analytics never blocks the chat UX.
        """
        from sqlalchemy import select as sa_select

        from ..db_models import WidgetEventRecord, WidgetSessionRecord
        conversation = kernel.load_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="unknown conversation id")
        _require_public_widget_session_access(request, conversation)
        _now = datetime.now(timezone.utc)
        try:
            with auth_session_factory() as _qs:
                ws = _qs.scalar(
                    sa_select(WidgetSessionRecord).where(
                        WidgetSessionRecord.conversation_id == conversation_id
                    )
                )
                _session_id = ws.session_id if ws is not None else None
                _org_id = conversation.organization_id or ""
                _agent_id = conversation.agent_id
        except Exception:  # noqa: BLE001
            return Response(status_code=202)
        try:
            with auth_session_factory.begin() as _es:
                for ev in payload.events:
                    if _session_id is None:
                        continue
                    _es.add(
                        WidgetEventRecord(
                            event_id=str(uuid4()),
                            organization_id=_org_id,
                            session_id=_session_id,
                            conversation_id=conversation_id,
                            agent_id=_agent_id,
                            event_type=ev.event_type,
                            event_data=ev.event_data,
                            occurred_at=ev.occurred_at or _now,
                            created_at=_now,
                        )
                    )
        except Exception:  # noqa: BLE001
            pass
        return Response(status_code=202)

    return router
