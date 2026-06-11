"""Channel ingress routes — extracted from api.py (RP-3.1 step 14, blueprint
group 19). SYNC-KERNEL: the synthetic ingress routes and the Meta WhatsApp
webhook call ``ChannelIngressService`` methods directly
(``start_channel_session`` / ``process_live_channel_message`` — sites 5/6 of
the eight kernel call sites), and the tool-integration webhook drives the
kernel through ``turn_service.reconcile_tool_invocation_result``.

Covers the synthetic-channel access dependency, the synthetic injection
endpoints (`/channels/whatsapp/messages`, `/channels/phone/calls/start`,
`/channels/phone/calls/{call_id}/transcripts`), the Meta WhatsApp webhook
GET/POST + projection dispatch, the intent-tags webhook dispatch trigger, and
the tool-integration webhook handler. The WhatsApp-only helpers (projection
source-event resolution, status observation/reconciliation recording, the
projection dispatcher runner) moved here with the webhook — nothing else in
api.py used them. The conversation/session/observation/cost helpers shared
with the still-inline provider group thread in as explicit kwargs.

The channel DTOs still live in ``ruhu.api``, so this module is imported by
``create_app()`` AT THE MOUNT SITE rather than at api.py's module top
(hazard H7: DTO imports sit at module top here). The provider HTTP client
and the tool-integration worker resolve per-request from ``app.state``
(zero-arg state callable / ``request.app.state``) so tests can keep
overriding ``app.state`` after construction.

No ``tags=`` / ``prefix=`` and unchanged handler names (hazard H1).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

# DTOs at module top (hazard H7: PEP 563 handler annotations resolve against
# this module's globals).
from ..api import (
    ChannelMessageIngressRequest,
    ChannelSessionStartRequest,
    PhoneTranscriptIngressRequest,
    ProviderDispatchResponse,
    ProviderWebhookAck,
    ToolIntegrationWebhookRequest,
    ToolIntegrationWebhookResponse,
)
from ..intent_tags_api import SemanticWebhookDispatchResponse
from ..provider_integrations import (
    extract_whatsapp_meta_messages,
    extract_whatsapp_meta_phone_number_id,
    extract_whatsapp_meta_statuses,
    fetch_whatsapp_meta_media,
    match_whatsapp_meta_verify_token,
    provider_secret_is_valid,
    verify_whatsapp_meta_signature,
)
from ..provider_projection import MetaWhatsAppProjectionDispatcher
from ..services.channel_ingress import ChannelTurnResponse

if TYPE_CHECKING:
    from ..attachments import AttachmentRuntime
    from ..analytics_tagging.webhooks import SemanticSummaryWebhookDispatcher
    from ..kernel import ConversationKernel
    from ..provider_costs import ProviderCostRecord, SQLAlchemyProviderCostStore
    from ..provider_integrations import WhatsAppMetaChannelConfig
    from ..realtime import RealtimeControlPlane, RealtimeEvent, RealtimeSession
    from ..registry import SQLAlchemyAgentRegistry
    from ..runtime_config import RuntimeSettings
    from ..schemas import ConversationState
    from ..services.channel_ingress import ChannelIngressService
    from ..services.conversation_turns import ConversationTurnService


def _normalize_provider_status_name(status_name: str) -> str:
    return "".join(
        character.lower() if character.isalnum() else "_"
        for character in status_name.strip()
    ).strip("_") or "unknown"


def _classify_whatsapp_delivery_status(status_name: str) -> tuple[str, bool]:
    normalized_status = _normalize_provider_status_name(status_name)
    if normalized_status in {"sent", "accepted", "queued"}:
        return "provider_accepted", False
    if normalized_status in {"delivered", "read"}:
        return "delivery_confirmed", True
    if normalized_status in {"failed", "undeliverable", "deleted"}:
        return "delivery_failed", True
    return "delivery_observed", False


def build_channels_router(
    *,
    kernel: "ConversationKernel",
    agent_registry: "SQLAlchemyAgentRegistry",
    turn_service: "ConversationTurnService",
    channel_ingress: "ChannelIngressService",
    realtime_control_plane: "RealtimeControlPlane | None",
    provider_cost_store: "SQLAlchemyProviderCostStore | None",
    whatsapp_meta_channels: "dict[str, WhatsAppMetaChannelConfig]",
    attachment_runtime: "AttachmentRuntime | None",
    semantic_summary_webhook_dispatcher: "SemanticSummaryWebhookDispatcher | None",
    effective_runtime_settings: "RuntimeSettings",
    auth_enabled: bool,
    organization_id_for_request: Callable[[Request], str | None],
    require_provider_secret: Callable[[str | None], None],
    require_internal_api_access: Callable[[Request], None],
    configured_internal_api_secret: Callable[[], str | None],
    ensure_live_channel_conversation: Callable[..., "ConversationState"],
    ensure_realtime_session: Callable[..., "RealtimeSession | None"],
    record_inbound_observation: Callable[..., None],
    record_provider_costs: Callable[..., "list[ProviderCostRecord]"],
    provider_http_client_state: Callable[[], object | None],
) -> APIRouter:
    router = APIRouter()

    # /channels/* are SYNTHETIC injection endpoints — they bypass the HMAC
    # signature verification that real provider webhooks (e.g.
    # /providers/meta/whatsapp/webhook) enforce. They exist for internal
    # testing/admin tooling and must NOT be reachable by anonymous traffic.
    #
    # In production (auth_enabled=True) we require either a superuser
    # principal or a valid X-Ruhu-Internal-Secret header. In bootstrap dev/test
    # mode, require a configured internal/provider shared secret when present;
    # only fully secretless local harnesses keep the historical open path.
    def _require_synthetic_channel_access(request: Request) -> None:
        if auth_enabled:
            require_internal_api_access(request)
            return
        internal_secret = configured_internal_api_secret()
        if internal_secret is not None:
            provided_secret = request.headers.get("X-Ruhu-Internal-Secret")
            if not provider_secret_is_valid(internal_secret, provided_secret):
                raise HTTPException(status_code=403, detail="invalid internal API secret")
            return
        provider_secret = effective_runtime_settings.provider_shared_secret
        if provider_secret is not None and provider_secret.strip():
            provided_secret = request.headers.get("X-Ruhu-Provider-Secret")
            if not provider_secret_is_valid(provider_secret, provided_secret):
                raise HTTPException(status_code=403, detail="invalid provider secret")

    _synthetic_channel_deps = [Depends(_require_synthetic_channel_access)]

    def _resolve_whatsapp_projection_source_event(
        *,
        conversation_id: str,
        provider_message_id: str | None,
    ) -> "RealtimeEvent | None":
        if realtime_control_plane is None or provider_message_id is None or not provider_message_id.strip():
            return None
        events = realtime_control_plane.events.replay(conversation_id=conversation_id)
        normalized_message_id = provider_message_id.strip()
        for event in reversed(events):
            if event.family != "provider" or event.name != "whatsapp_projection_delivered":
                continue
            candidate_message_id = str(event.payload.get("provider_message_id") or "").strip()
            if candidate_message_id != normalized_message_id:
                continue
            return event
        return None

    def _record_whatsapp_status_observation(
        *,
        conversation_id: str | None,
        organization_id: str | None,
        realtime_session_id: str | None,
        status_name: str,
        payload: dict[str, object],
    ) -> None:
        if realtime_control_plane is None or conversation_id is None:
            return
        normalized_status = _normalize_provider_status_name(status_name)
        realtime_control_plane.events.append(
            conversation_id=conversation_id,
            organization_id=organization_id,
            realtime_session_id=realtime_session_id,
            family="provider",
            name=f"whatsapp_status_{normalized_status}",
            payload=payload,
            actor_type="system",
            visibility="internal",
            outbox_topic="conversation_projection",
        )

    def _record_whatsapp_projection_reconciliation(
        *,
        conversation_id: str | None,
        organization_id: str | None,
        realtime_session_id: str | None,
        projection_event: "RealtimeEvent | None",
        status_name: str,
        payload: dict[str, object],
    ) -> None:
        if realtime_control_plane is None or conversation_id is None or projection_event is None:
            return
        delivery_state, terminal = _classify_whatsapp_delivery_status(status_name)
        source_event_id = projection_event.payload.get("source_event_id")
        reconciliation_payload: dict[str, object] = {
            "provider": "meta_whatsapp",
            "projection_event_id": projection_event.event_id,
            "status": status_name,
            "delivery_state": delivery_state,
            "terminal": terminal,
            "status_payload": dict(payload),
        }
        if isinstance(source_event_id, str) and source_event_id.strip():
            reconciliation_payload["source_event_id"] = source_event_id.strip()
        provider_message_id = payload.get("provider_message_id")
        if isinstance(provider_message_id, str) and provider_message_id.strip():
            reconciliation_payload["provider_message_id"] = provider_message_id.strip()
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            reconciliation_payload["errors"] = errors
        realtime_control_plane.events.append(
            conversation_id=conversation_id,
            organization_id=organization_id,
            realtime_session_id=realtime_session_id,
            family="provider",
            name="whatsapp_projection_reconciled",
            payload=reconciliation_payload,
            actor_type="system",
            visibility="internal",
            outbox_topic="conversation_projection",
        )

    async def _dispatch_whatsapp_projections(
        *,
        conversation_id: str | None = None,
        limit: int = 100,
    ) -> ProviderDispatchResponse:
        if realtime_control_plane is None:
            return ProviderDispatchResponse()
        provider_http_client = provider_http_client_state()
        dispatch_runner = MetaWhatsAppProjectionDispatcher(
            control_plane=realtime_control_plane,
            configs=whatsapp_meta_channels,
            provider_cost_store=provider_cost_store,
            client=provider_http_client,
        )
        outcome = await dispatch_runner.dispatch_pending(
            conversation_id=conversation_id,
            limit=limit,
        )
        return ProviderDispatchResponse(
            attempted=outcome.attempted,
            delivered=outcome.delivered,
            failed=outcome.failed,
            retried=outcome.retried,
            skipped=outcome.skipped,
        )

    @router.post(
        "/channels/whatsapp/messages",
        response_model=ChannelTurnResponse,
        dependencies=_synthetic_channel_deps,
    )
    def ingest_whatsapp_message(payload: ChannelMessageIngressRequest, request: Request) -> ChannelTurnResponse:
        organization_id = organization_id_for_request(request)
        return channel_ingress.process_live_channel_message(
            channel="whatsapp",
            external_session_id=payload.external_session_id,
            agent_id=payload.agent_id,
            text=payload.text,
            metadata=payload.metadata,
            modality="text",
            event_type="user_message",
            organization_id=organization_id,
            emit_entry_prelude_on_autostart=False,
            provider=payload.provider,
            provider_session_id=payload.provider_session_id,
            participant_identity=payload.participant_identity,
            idempotency_key=payload.idempotency_key,
        )

    @router.post(
        "/channels/phone/calls/start",
        response_model=ChannelTurnResponse,
        dependencies=_synthetic_channel_deps,
    )
    def start_phone_call(payload: ChannelSessionStartRequest, request: Request) -> ChannelTurnResponse:
        organization_id = organization_id_for_request(request)
        return channel_ingress.start_channel_session(
            channel="phone",
            agent_id=payload.agent_id,
            external_session_id=payload.external_session_id,
            organization_id=organization_id,
            provider=payload.provider,
            provider_session_id=payload.provider_session_id,
            participant_identity=payload.participant_identity,
            metadata=payload.metadata,
        )

    @router.post(
        "/channels/phone/calls/{call_id}/transcripts",
        response_model=ChannelTurnResponse,
        dependencies=_synthetic_channel_deps,
    )
    def ingest_phone_transcript(
        call_id: str,
        payload: PhoneTranscriptIngressRequest,
        request: Request,
    ) -> ChannelTurnResponse:
        organization_id = organization_id_for_request(request)
        return channel_ingress.process_live_channel_message(
            channel="phone",
            external_session_id=call_id,
            agent_id=payload.agent_id,
            text=payload.text,
            metadata=payload.metadata,
            modality="audio",
            event_type="user_final_transcript" if payload.is_final else "user_partial_transcript",
            organization_id=organization_id,
            emit_entry_prelude_on_autostart=True,
            provider=payload.provider,
            provider_session_id=payload.provider_session_id,
            participant_identity=payload.participant_identity,
            idempotency_key=payload.idempotency_key,
        )

    @router.get("/providers/meta/whatsapp/webhook")
    def verify_meta_whatsapp_webhook(
        hub_mode: str | None = Query(default=None, alias="hub.mode"),
        hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
        hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    ) -> Response:
        if hub_mode != "subscribe":
            raise HTTPException(status_code=403, detail="webhook verification failed")
        matched = match_whatsapp_meta_verify_token(whatsapp_meta_channels.values(), hub_verify_token)
        if matched is None or hub_challenge is None:
            raise HTTPException(status_code=403, detail="webhook verification failed")
        return Response(content=str(hub_challenge), media_type="text/plain")

    @router.post("/providers/meta/whatsapp/webhook", response_model=ProviderWebhookAck)
    async def handle_meta_whatsapp_webhook(
        request: Request,
        x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    ) -> ProviderWebhookAck:
        raw_body = await request.body()
        payload = await request.json() if raw_body else {}
        phone_number_id = extract_whatsapp_meta_phone_number_id(payload)
        if phone_number_id is None:
            return ProviderWebhookAck(status="ignored")
        config = whatsapp_meta_channels.get(phone_number_id)
        if config is None:
            return ProviderWebhookAck(status="ignored")
        if not verify_whatsapp_meta_signature(config, raw_body, x_hub_signature_256):
            raise HTTPException(status_code=403, detail="invalid whatsapp webhook signature")
        provider_http_client = getattr(request.app.state, "provider_http_client", None)

        processed_messages = 0
        processed_media_messages = 0
        processed_statuses = 0
        for status_event in extract_whatsapp_meta_statuses(payload):
            processed_statuses += 1
            conversation_id = (
                f"whatsapp:{status_event.recipient_id}"
                if status_event.recipient_id
                else None
            )
            realtime_session_id = None
            source_projection: "RealtimeEvent | None" = None
            if conversation_id is not None and realtime_control_plane is not None:
                sessions = realtime_control_plane.sessions.list_by_conversation(conversation_id)
                eligible = [
                    session
                    for session in sessions
                    if session.channel == "whatsapp" and session.provider == "meta_whatsapp"
                ]
                if eligible:
                    eligible.sort(
                        key=lambda session: (
                            session.last_seen_at or session.updated_at,
                            session.created_at,
                        ),
                        reverse=True,
                    )
                    realtime_session_id = eligible[0].realtime_session_id
            status_payload: dict[str, object] = {
                "provider": "meta_whatsapp",
                "phone_number_id": status_event.phone_number_id,
                "status": status_event.status,
            }
            if status_event.recipient_id is not None:
                status_payload["recipient_id"] = status_event.recipient_id
            if status_event.provider_message_id is not None:
                status_payload["provider_message_id"] = status_event.provider_message_id
                source_projection = _resolve_whatsapp_projection_source_event(
                    conversation_id=conversation_id or "",
                    provider_message_id=status_event.provider_message_id,
                )
                if source_projection is not None:
                    status_payload["projection_event_id"] = source_projection.event_id
                    source_event_id = source_projection.payload.get("source_event_id")
                    if isinstance(source_event_id, str) and source_event_id.strip():
                        status_payload["source_event_id"] = source_event_id.strip()
            if status_event.occurred_at is not None:
                status_payload["occurred_at"] = status_event.occurred_at
            if status_event.errors:
                status_payload["errors"] = status_event.errors
            if status_event.pricing:
                status_payload["pricing"] = status_event.pricing
            if status_event.metadata:
                status_payload["metadata"] = status_event.metadata
            _record_whatsapp_status_observation(
                conversation_id=conversation_id,
                organization_id=config.organization_id,
                realtime_session_id=realtime_session_id,
                status_name=status_event.status,
                payload=status_payload,
            )
            _record_whatsapp_projection_reconciliation(
                conversation_id=conversation_id,
                organization_id=config.organization_id,
                realtime_session_id=realtime_session_id,
                projection_event=source_projection,
                status_name=status_event.status,
                payload=status_payload,
            )
            record_provider_costs(
                conversation_id=conversation_id,
                organization_id=config.organization_id,
                realtime_session_id=realtime_session_id,
                provider="meta_whatsapp",
                payload={
                    **status_payload,
                    **({"provider_cost_record": status_event.pricing} if status_event.pricing else {}),
                },
                default_cost_type=f"provider_message_status_{status_event.status}",
            )
        for inbound in extract_whatsapp_meta_messages(payload):
            conversation_id = f"whatsapp:{inbound.sender_id}"
            if not inbound.text:
                processed_media_messages += 1
                conversation = ensure_live_channel_conversation(
                    channel="whatsapp",
                    external_session_id=inbound.sender_id,
                    agent_id=config.agent_id,
                    organization_id=config.organization_id,
                    metadata={"provider": "meta_whatsapp"},
                )
                realtime_session = ensure_realtime_session(
                    conversation_id=conversation_id,
                    organization_id=conversation.organization_id,
                    channel="whatsapp",
                    external_session_id=inbound.sender_id,
                    provider="meta_whatsapp",
                    provider_session_id=phone_number_id,
                    participant_identity=inbound.sender_id,
                    metadata={**inbound.metadata, "message_id": inbound.message_id},
                    allow_new_on_inactive=True,
                )
                realtime_session_id = None
                if realtime_session is not None:
                    realtime_session_id = realtime_session.realtime_session_id
                media = inbound.metadata.get("media")
                attachment_id = None
                if (
                    attachment_runtime is not None
                    and isinstance(media, dict)
                    and isinstance(media.get("id"), str)
                    and media.get("id")
                ):
                    downloaded_media = await fetch_whatsapp_meta_media(
                        config,
                        media_id=str(media["id"]),
                        message_id=inbound.message_id,
                        message_type=inbound.message_type,
                        client=provider_http_client,
                    )
                    attachment = attachment_runtime.service.upload_attachment(
                        conversation_id=conversation_id,
                        organization_id=conversation.organization_id,
                        channel="whatsapp",
                        filename=downloaded_media.filename,
                        content_type=downloaded_media.content_type,
                        content_bytes=downloaded_media.content_bytes,
                        source="meta_whatsapp",
                        metadata={
                            **inbound.metadata,
                            **downloaded_media.metadata,
                            "message_id": inbound.message_id,
                            "sender_id": inbound.sender_id,
                        },
                    )
                    attachment_runtime.schedule_processing(
                        attachment_id=attachment.attachment_id,
                        organization_id=conversation.organization_id,
                    )
                    attachment_id = attachment.attachment_id
                record_inbound_observation(
                    conversation_id=conversation_id,
                    organization_id=config.organization_id,
                    realtime_session_id=realtime_session_id,
                    channel="whatsapp",
                    modality=inbound.modality,
                    text=None,
                    metadata={
                        **inbound.metadata,
                        "message_id": inbound.message_id,
                        **({"attachment_id": attachment_id} if attachment_id is not None else {}),
                    },
                    idempotency_key=inbound.message_id or None,
                )
                continue
            processed_messages += 1
            channel_ingress.process_live_channel_message(
                channel="whatsapp",
                external_session_id=inbound.sender_id,
                agent_id=config.agent_id,
                text=inbound.text,
                metadata={**inbound.metadata, "message_id": inbound.message_id},
                modality=inbound.modality,
                event_type="user_message",
                organization_id=config.organization_id,
                emit_entry_prelude_on_autostart=False,
                provider="meta_whatsapp",
                provider_session_id=phone_number_id,
                participant_identity=inbound.sender_id,
                idempotency_key=inbound.message_id or None,
            )
        dispatch = await _dispatch_whatsapp_projections(limit=max(processed_messages, 1) * 10)
        return ProviderWebhookAck(
            status="ok",
            processed_messages=processed_messages,
            processed_media_messages=processed_media_messages,
            processed_statuses=processed_statuses,
            delivered_messages=dispatch.delivered,
        )

    @router.post("/providers/meta/whatsapp/dispatch", response_model=ProviderDispatchResponse)
    async def dispatch_meta_whatsapp_projection(
        conversation_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        x_ruhu_provider_secret: str | None = Header(default=None, alias="X-Ruhu-Provider-Secret"),
    ) -> ProviderDispatchResponse:
        require_provider_secret(x_ruhu_provider_secret)
        return await _dispatch_whatsapp_projections(conversation_id=conversation_id, limit=limit)

    @router.post("/providers/intent-tags/webhooks/dispatch", response_model=SemanticWebhookDispatchResponse)
    def dispatch_semantic_summary_webhooks_provider(
        organization_id: str | None = Query(default=None),
        conversation_id: str | None = Query(default=None),
        mode: str = Query(default="both", pattern="^(fanout|deliver|both)$"),
        limit: int = Query(default=100, ge=1, le=1000),
        x_ruhu_provider_secret: str | None = Header(default=None, alias="X-Ruhu-Provider-Secret"),
    ) -> SemanticWebhookDispatchResponse:
        require_provider_secret(x_ruhu_provider_secret)
        if semantic_summary_webhook_dispatcher is None:
            raise HTTPException(status_code=503, detail="semantic summary webhook dispatcher is not configured")
        result = semantic_summary_webhook_dispatcher.run_pending(
            organization_id=organization_id,
            conversation_id=conversation_id,
            limit=limit,
            mode=mode,
        )
        return SemanticWebhookDispatchResponse(
            publication_attempted=result.publication_attempted,
            publication_fanned_out=result.publication_fanned_out,
            publication_skipped=result.publication_skipped,
            publication_failed=result.publication_failed,
            delivery_attempted=result.delivery_attempted,
            delivery_delivered=result.delivery_delivered,
            delivery_failed=result.delivery_failed,
            delivery_retried=result.delivery_retried,
            delivery_skipped=result.delivery_skipped,
        )

    @router.post(
        "/providers/tools/integration-webhooks/{callback_correlation_id}",
        response_model=ToolIntegrationWebhookResponse,
    )
    async def handle_tool_integration_webhook(
        callback_correlation_id: str,
        body: ToolIntegrationWebhookRequest,
        request: Request,
        x_ruhu_provider_secret: str | None = Header(default=None, alias="X-Ruhu-Provider-Secret"),
    ) -> ToolIntegrationWebhookResponse:
        require_provider_secret(x_ruhu_provider_secret)
        worker = getattr(request.app.state, "tool_integration_worker", None)
        if worker is None:
            raise HTTPException(status_code=503, detail="tool integration worker is not configured")
        raw_body = await request.body()
        try:
            process_result = worker.process_webhook_callback(
                callback_correlation_id,
                payload=dict(body.payload),
                headers={str(k): str(v) for k, v in request.headers.items()},
                raw_body=raw_body,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        invocation = kernel.tool_runtime.store.load(process_result.job.invocation_id) if kernel.tool_runtime is not None else None
        conversation_id = None if invocation is None else invocation.caller.conversation_id
        kernel_turn_applied = False
        step_after = None
        if not process_result.replayed and invocation is not None and invocation.caller.conversation_id:
            conversation = kernel.load_conversation(invocation.caller.conversation_id)
            if conversation is not None:
                try:
                    snapshot = agent_registry.get_version_snapshot(
                        conversation.agent_version_id,
                        organization_id=conversation.organization_id,
                    )
                except KeyError:
                    snapshot = None
                if snapshot is not None:
                    try:
                        result = turn_service.reconcile_tool_invocation_result(
                            invocation.caller.conversation_id,
                            process_result.job.invocation_id,
                        )
                    except ValueError as exc:
                        raise HTTPException(status_code=409, detail=str(exc)) from exc
                    kernel_turn_applied = True
                    step_after = result.step_after

        return ToolIntegrationWebhookResponse(
            job_id=process_result.job.job_id,
            invocation_id=process_result.job.invocation_id,
            job_status=process_result.job.status,
            conversation_id=conversation_id,
            kernel_turn_applied=kernel_turn_applied,
            step_after=step_after,
            replayed=process_result.replayed,
        )

    return router
