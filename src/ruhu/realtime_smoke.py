from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import asyncio
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any, AsyncIterator, Callable, Iterable

import httpx

from .env_files import load_env_file
from .livekit_adapter import (
    LiveKitAdapterConfig,
    LiveKitAgentsUnavailableError,
    LiveKitDispatchClient,
    LiveKitTokenIssuer,
)
from .provider_integrations import parse_whatsapp_meta_channels, send_whatsapp_meta_texts
from .runtime_config import RuntimeSettings


@dataclass(frozen=True, slots=True)
class LiveKitSmokeResult:
    ok: bool
    provider: str
    server_url: str | None
    channel: str
    conversation_id: str
    realtime_session_id: str
    room_name: str | None = None
    participant_identity: str | None = None
    agent_name: str | None = None
    voice_mode: str | None = None
    sdk_version_target: str | None = None
    configured_dispatch_strategy: str | None = None
    token_issued: bool = False
    token_length: int = 0
    dispatch: dict[str, object] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WhatsAppSmokeResult:
    ok: bool
    provider: str
    phone_number_id: str
    recipient_id: str
    agent_id: str | None = None
    organization_id: str | None = None
    messages_url: str | None = None
    text_count: int = 0
    deliveries: list[dict[str, object]] = field(default_factory=list)
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WidgetChatSmokeResult:
    ok: bool
    provider: str
    base_url: str
    agent_id: str
    conversation_id: str | None = None
    resumed: bool = False
    step_after: str | None = None
    turns_sent: int = 0
    trace_ids: list[str] = field(default_factory=list)
    assistant_messages: list[str] = field(default_factory=list)
    pending_tool_invocations: int = 0
    session_token_issued: bool = False
    session_token_length: int = 0
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WidgetVoiceSmokeResult:
    ok: bool
    provider: str
    base_url: str
    agent_id: str
    conversation_id: str | None = None
    realtime_session_id: str | None = None
    resumed: bool = False
    room_name: str | None = None
    participant_identity: str | None = None
    agent_name: str | None = None
    voice_mode: str | None = None
    configured_dispatch_strategy: str | None = None
    token_issued: bool = False
    token_length: int = 0
    transcript_count: int = 0
    trace_ids: list[str] = field(default_factory=list)
    assistant_messages: list[str] = field(default_factory=list)
    speak_texts: list[str] = field(default_factory=list)
    disconnected: bool = False
    session_token_issued: bool = False
    session_token_length: int = 0
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _print(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
        return
    for key, value in payload.items():
        print(f"{key}={value}")


def _parse_json_object(raw_value: str | None, *, argument_name: str) -> dict[str, object]:
    if raw_value in {None, ""}:
        return {}
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{argument_name} must be valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{argument_name} must decode to a JSON object")
    return {str(key): value for key, value in payload.items()}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _default_livekit_ids(now: datetime) -> tuple[str, str]:
    stamp = now.strftime("%Y%m%d%H%M%S")
    return f"smoke-livekit-{stamp}", f"rs-smoke-{stamp}"


def _default_whatsapp_text(now: datetime) -> str:
    return f"Ruhu WhatsApp smoke check {now.isoformat(timespec='seconds')}"


def _default_widget_chat_text(now: datetime) -> str:
    return f"Ruhu widget chat smoke check {now.isoformat(timespec='seconds')}"


def _default_widget_voice_text(now: datetime) -> str:
    return "Tell me about pricing."


def _normalize_base_url(base_url: str) -> str:
    normalized = base_url.strip()
    return normalized.rstrip("/") if normalized else "http://127.0.0.1:8010"


@asynccontextmanager
async def _client_scope(
    *,
    base_url: str,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    if client is not None:
        yield client
        return
    async with httpx.AsyncClient(base_url=_normalize_base_url(base_url), timeout=30.0) as managed_client:
        yield managed_client


def _response_reason(response: httpx.Response) -> str:
    detail: str | None = None
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        for key in ("detail", "message", "reason", "error"):
            value = payload.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                detail = value.strip() or None
            else:
                detail = json.dumps(value, default=str)
            if detail:
                break
    if detail is None:
        text = response.text.strip()
        detail = text[:500] if text else response.reason_phrase
    return f"HTTP {response.status_code}: {detail}"


def _extract_rendered_message_texts(messages_payload: object) -> list[str]:
    if not isinstance(messages_payload, list):
        return []
    texts: list[str] = []
    for item in messages_payload:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    return texts


def _extract_trace_id(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    trace_id = payload.get("trace_id")
    if isinstance(trace_id, str) and trace_id.strip():
        return trace_id.strip()
    return None


def _count_pending_tool_invocations(payload: object) -> int:
    if not isinstance(payload, dict):
        return 0
    pending = payload.get("pending_tool_invocations")
    return len(pending) if isinstance(pending, list) else 0


def _widget_session_headers(session_token: str) -> dict[str, str]:
    return {"X-Ruhu-Widget-Session-Token": session_token}


async def _create_widget_session(
    *,
    base_url: str,
    agent_id: str,
    conversation_id: str | None = None,
    session_token: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    request_payload: dict[str, object] = {"agent_id": agent_id}
    if conversation_id:
        request_payload["conversation_id"] = conversation_id
    if session_token:
        request_payload["session_token"] = session_token
    async with _client_scope(base_url=base_url, client=client) as active_client:
        response = await active_client.post("/public/widget/sessions", json=request_payload)
    if not response.is_success:
        return None, None, _response_reason(response)
    payload = response.json()
    if not isinstance(payload, dict):
        return None, None, "widget session response was not a JSON object"
    issued_session_token = payload.get("session_token")
    if not isinstance(issued_session_token, str) or not issued_session_token.strip():
        return None, None, "widget session did not return a usable session_token"
    return payload, issued_session_token.strip(), None


async def run_livekit_smoke(
    *,
    settings: RuntimeSettings | None = None,
    conversation_id: str,
    realtime_session_id: str,
    channel: str = "web_widget",
    participant_identity: str | None = None,
    participant_name: str | None = None,
    metadata: dict[str, object] | None = None,
    skip_dispatch: bool = False,
    dispatch_strategy: str = "api_dispatch",
    token_issuer: Any | None = None,
    dispatch_client: Any | None = None,
) -> LiveKitSmokeResult:
    effective_settings = settings or RuntimeSettings.from_env()
    config = LiveKitAdapterConfig.from_settings(effective_settings)
    if config is None:
        return LiveKitSmokeResult(
            ok=False,
            provider="livekit",
            server_url=effective_settings.livekit_server_url,
            channel=channel,
            conversation_id=conversation_id,
            realtime_session_id=realtime_session_id,
            reason="LiveKit adapter config is incomplete",
        )

    smoke_metadata = {
        "smoke": True,
        "smoke_kind": "livekit",
        "smoke_at": _utc_now().isoformat(timespec="seconds"),
    }
    smoke_metadata.update(metadata or {})
    active_token_issuer = token_issuer or LiveKitTokenIssuer(config)
    try:
        grant = active_token_issuer.issue_voice_transport(
            channel=channel,
            conversation_id=conversation_id,
            realtime_session_id=realtime_session_id,
            participant_identity=participant_identity,
            participant_name=participant_name,
            metadata=smoke_metadata,
        )
    except LiveKitAgentsUnavailableError as exc:
        return LiveKitSmokeResult(
            ok=False,
            provider="livekit",
            server_url=config.server_url,
            channel=channel,
            conversation_id=conversation_id,
            realtime_session_id=realtime_session_id,
            configured_dispatch_strategy=config.dispatch_strategy,
            voice_mode=config.voice_mode,
            sdk_version_target=config.sdk_version_target,
            reason=str(exc),
        )
    except Exception as exc:
        return LiveKitSmokeResult(
            ok=False,
            provider="livekit",
            server_url=config.server_url,
            channel=channel,
            conversation_id=conversation_id,
            realtime_session_id=realtime_session_id,
            configured_dispatch_strategy=config.dispatch_strategy,
            voice_mode=config.voice_mode,
            sdk_version_target=config.sdk_version_target,
            reason=str(exc),
        )

    dispatch_payload: dict[str, object]
    dispatch_ok = True
    reason = None
    if skip_dispatch:
        dispatch_payload = {
            "strategy": dispatch_strategy,
            "attempted": False,
            "applied": False,
            "skipped": True,
        }
    else:
        smoke_config = replace(config, dispatch_strategy=dispatch_strategy)
        active_dispatch_client = dispatch_client or LiveKitDispatchClient(smoke_config)
        dispatch_result = await active_dispatch_client.create_dispatch(
            room_name=grant.room_name,
            metadata=grant.metadata,
            agent_name=grant.agent_name,
        )
        dispatch_payload = dispatch_result.as_dict()
        dispatch_ok = bool(dispatch_result.applied)
        if not dispatch_ok:
            reason = dispatch_result.error or "LiveKit dispatch smoke failed"

    return LiveKitSmokeResult(
        ok=dispatch_ok,
        provider="livekit",
        server_url=config.server_url,
        channel=channel,
        conversation_id=conversation_id,
        realtime_session_id=realtime_session_id,
        room_name=grant.room_name,
        participant_identity=grant.participant_identity,
        agent_name=grant.agent_name,
        voice_mode=grant.voice_mode,
        sdk_version_target=grant.sdk_version_target,
        configured_dispatch_strategy=config.dispatch_strategy,
        token_issued=True,
        token_length=len(grant.token),
        dispatch=dispatch_payload,
        metadata=dict(grant.metadata),
        reason=reason,
    )


async def run_whatsapp_smoke(
    *,
    settings: RuntimeSettings | None = None,
    phone_number_id: str,
    recipient_id: str,
    texts: Iterable[str] | None = None,
    send_texts: Callable[..., Any] = send_whatsapp_meta_texts,
    client: Any | None = None,
) -> WhatsAppSmokeResult:
    effective_settings = settings or RuntimeSettings.from_env()
    try:
        configs = parse_whatsapp_meta_channels(effective_settings.whatsapp_meta_channels)
    except ValueError as exc:
        return WhatsAppSmokeResult(
            ok=False,
            provider="meta_whatsapp",
            phone_number_id=phone_number_id,
            recipient_id=recipient_id,
            reason=str(exc),
        )
    config = configs.get(phone_number_id)
    if config is None:
        return WhatsAppSmokeResult(
            ok=False,
            provider="meta_whatsapp",
            phone_number_id=phone_number_id,
            recipient_id=recipient_id,
            reason=f"Meta WhatsApp channel {phone_number_id!r} is not configured",
        )

    outbound_texts = [text.strip() for text in texts or [] if text and text.strip()]
    if not outbound_texts:
        outbound_texts = [_default_whatsapp_text(_utc_now())]

    try:
        deliveries = await send_texts(
            config,
            recipient_id=recipient_id,
            texts=outbound_texts,
            client=client,
        )
    except httpx.HTTPStatusError as exc:
        response_text = exc.response.text.strip() if exc.response is not None else ""
        response_excerpt = response_text[:500] if response_text else ""
        reason = (
            f"Meta WhatsApp send failed with HTTP {exc.response.status_code}: {response_excerpt}"
            if exc.response is not None
            else f"Meta WhatsApp send failed: {exc}"
        )
        return WhatsAppSmokeResult(
            ok=False,
            provider="meta_whatsapp",
            phone_number_id=phone_number_id,
            recipient_id=recipient_id,
            agent_id=config.agent_id,
            organization_id=config.organization_id,
            messages_url=config.messages_url,
            text_count=len(outbound_texts),
            reason=reason,
        )
    except Exception as exc:
        return WhatsAppSmokeResult(
            ok=False,
            provider="meta_whatsapp",
            phone_number_id=phone_number_id,
            recipient_id=recipient_id,
            agent_id=config.agent_id,
            organization_id=config.organization_id,
            messages_url=config.messages_url,
            text_count=len(outbound_texts),
            reason=str(exc),
        )

    return WhatsAppSmokeResult(
        ok=len(deliveries) == len(outbound_texts),
        provider="meta_whatsapp",
        phone_number_id=phone_number_id,
        recipient_id=recipient_id,
        agent_id=config.agent_id,
        organization_id=config.organization_id,
        messages_url=config.messages_url,
        text_count=len(outbound_texts),
        deliveries=[dict(item) for item in deliveries],
    )


async def run_widget_chat_smoke(
    *,
    settings: RuntimeSettings | None = None,
    base_url: str = "http://127.0.0.1:8010",
    agent_id: str,
    conversation_id: str | None = None,
    session_token: str | None = None,
    texts: Iterable[str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> WidgetChatSmokeResult:
    del settings
    effective_base_url = _normalize_base_url(base_url)
    outbound_texts = [text.strip() for text in texts or [] if text and text.strip()]
    if not outbound_texts:
        outbound_texts = [_default_widget_chat_text(_utc_now())]

    session_payload, issued_session_token, session_reason = await _create_widget_session(
        base_url=effective_base_url,
        agent_id=agent_id,
        conversation_id=conversation_id,
        session_token=session_token,
        client=client,
    )
    if session_payload is None or issued_session_token is None:
        return WidgetChatSmokeResult(
            ok=False,
            provider="widget_chat",
            base_url=effective_base_url,
            agent_id=agent_id,
            conversation_id=conversation_id,
            reason=session_reason,
        )

    resolved_conversation_id = str(session_payload.get("conversation_id") or "").strip() or conversation_id
    assistant_messages = _extract_rendered_message_texts(session_payload.get("messages"))
    pending_tool_invocations = _count_pending_tool_invocations(session_payload)
    step_after = str(session_payload.get("step_id") or "").strip() or None
    trace_ids: list[str] = []
    turns_sent = 0

    async with _client_scope(base_url=effective_base_url, client=client) as active_client:
        for text in outbound_texts:
            response = await active_client.post(
                f"/public/widget/sessions/{resolved_conversation_id}/messages",
                headers=_widget_session_headers(issued_session_token),
                json={"text": text},
            )
            if not response.is_success:
                return WidgetChatSmokeResult(
                    ok=False,
                    provider="widget_chat",
                    base_url=effective_base_url,
                    agent_id=agent_id,
                    conversation_id=resolved_conversation_id,
                    resumed=bool(session_payload.get("resumed")),
                    step_after=step_after,
                    turns_sent=turns_sent,
                    trace_ids=trace_ids,
                    assistant_messages=assistant_messages,
                    pending_tool_invocations=pending_tool_invocations,
                    session_token_issued=True,
                    session_token_length=len(issued_session_token),
                    reason=_response_reason(response),
                )
            payload = response.json()
            turns_sent += 1
            assistant_messages.extend(_extract_rendered_message_texts(payload.get("messages")))
            pending_tool_invocations = _count_pending_tool_invocations(payload)
            step_after = str(payload.get("step_after") or "").strip() or step_after
            trace_id = _extract_trace_id(payload)
            if trace_id is not None:
                trace_ids.append(trace_id)

    return WidgetChatSmokeResult(
        ok=True,
        provider="widget_chat",
        base_url=effective_base_url,
        agent_id=agent_id,
        conversation_id=resolved_conversation_id,
        resumed=bool(session_payload.get("resumed")),
        step_after=step_after,
        turns_sent=turns_sent,
        trace_ids=trace_ids,
        assistant_messages=assistant_messages,
        pending_tool_invocations=pending_tool_invocations,
        session_token_issued=True,
        session_token_length=len(issued_session_token),
    )


async def run_widget_voice_smoke(
    *,
    settings: RuntimeSettings | None = None,
    base_url: str = "http://127.0.0.1:8010",
    agent_id: str,
    conversation_id: str | None = None,
    session_token: str | None = None,
    participant_identity: str | None = None,
    participant_name: str | None = None,
    metadata: dict[str, object] | None = None,
    texts: Iterable[str] | None = None,
    provider_secret: str | None = None,
    disconnect: bool = True,
    client: httpx.AsyncClient | None = None,
) -> WidgetVoiceSmokeResult:
    effective_settings = settings or RuntimeSettings.from_env()
    effective_base_url = _normalize_base_url(base_url)
    transcript_texts = [text.strip() for text in texts or [] if text and text.strip()]
    voice_metadata = {
        "smoke": True,
        "smoke_kind": "widget_voice",
        "smoke_at": _utc_now().isoformat(timespec="seconds"),
    }
    voice_metadata.update(metadata or {})

    session_payload, issued_session_token, session_reason = await _create_widget_session(
        base_url=effective_base_url,
        agent_id=agent_id,
        conversation_id=conversation_id,
        session_token=session_token,
        client=client,
    )
    if session_payload is None or issued_session_token is None:
        return WidgetVoiceSmokeResult(
            ok=False,
            provider="widget_voice_livekit",
            base_url=effective_base_url,
            agent_id=agent_id,
            conversation_id=conversation_id,
            reason=session_reason,
        )

    resolved_conversation_id = str(session_payload.get("conversation_id") or "").strip() or conversation_id
    assistant_messages = _extract_rendered_message_texts(session_payload.get("messages"))
    trace_ids: list[str] = []
    speak_texts: list[str] = []
    disconnected = False

    async with _client_scope(base_url=effective_base_url, client=client) as active_client:
        voice_response = await active_client.post(
            f"/public/widget/sessions/{resolved_conversation_id}/voice",
            headers=_widget_session_headers(issued_session_token),
            json={
                "participant_identity": participant_identity,
                "participant_name": participant_name,
                "metadata": voice_metadata,
            },
        )
        if not voice_response.is_success:
            return WidgetVoiceSmokeResult(
                ok=False,
                provider="widget_voice_livekit",
                base_url=effective_base_url,
                agent_id=agent_id,
                conversation_id=resolved_conversation_id,
                resumed=bool(session_payload.get("resumed")),
                session_token_issued=True,
                session_token_length=len(issued_session_token),
                reason=_response_reason(voice_response),
            )
        voice_payload = voice_response.json()
        transport_payload = voice_payload.get("transport") if isinstance(voice_payload, dict) else {}
        transport = transport_payload if isinstance(transport_payload, dict) else {}
        realtime_session_id = str(voice_payload.get("realtime_session_id") or "").strip() or None
        if realtime_session_id is None:
            return WidgetVoiceSmokeResult(
                ok=False,
                provider="widget_voice_livekit",
                base_url=effective_base_url,
                agent_id=agent_id,
                conversation_id=resolved_conversation_id,
                resumed=bool(voice_payload.get("resumed")),
                room_name=str(transport.get("room_name") or "").strip() or None,
                participant_identity=str(transport.get("participant_identity") or "").strip() or None,
                agent_name=str(transport.get("agent_name") or "").strip() or None,
                voice_mode=str(transport.get("voice_mode") or "").strip() or None,
                configured_dispatch_strategy=str(transport.get("dispatch_strategy") or "").strip() or None,
                token_issued=isinstance(transport.get("token"), str) and bool(str(transport.get("token")).strip()),
                token_length=len(str(transport.get("token") or "")),
                session_token_issued=True,
                session_token_length=len(issued_session_token),
                reason="widget voice session did not return realtime_session_id",
            )

        effective_provider_secret = provider_secret or effective_settings.provider_shared_secret
        transcript_count = 0
        if transcript_texts and not effective_provider_secret:
            return WidgetVoiceSmokeResult(
                ok=False,
                provider="widget_voice_livekit",
                base_url=effective_base_url,
                agent_id=agent_id,
                conversation_id=resolved_conversation_id,
                realtime_session_id=realtime_session_id,
                resumed=bool(voice_payload.get("resumed")),
                room_name=str(transport.get("room_name") or "").strip() or None,
                participant_identity=str(transport.get("participant_identity") or "").strip() or None,
                agent_name=str(transport.get("agent_name") or "").strip() or None,
                voice_mode=str(transport.get("voice_mode") or "").strip() or None,
                configured_dispatch_strategy=str(transport.get("dispatch_strategy") or "").strip() or None,
                token_issued=isinstance(transport.get("token"), str) and bool(str(transport.get("token")).strip()),
                token_length=len(str(transport.get("token") or "")),
                session_token_issued=True,
                session_token_length=len(issued_session_token),
                reason="provider secret required to bridge widget voice transcripts",
            )

        for transcript_index, text in enumerate(transcript_texts, start=1):
            transcript_response = await active_client.post(
                f"/providers/livekit/voice/sessions/{realtime_session_id}/transcripts",
                headers={"X-Ruhu-Provider-Secret": str(effective_provider_secret)},
                json={
                    "text": text,
                    "is_final": True,
                    "idempotency_key": f"{realtime_session_id}:smoke-{transcript_index}",
                },
            )
            if not transcript_response.is_success:
                return WidgetVoiceSmokeResult(
                    ok=False,
                    provider="widget_voice_livekit",
                    base_url=effective_base_url,
                    agent_id=agent_id,
                    conversation_id=resolved_conversation_id,
                    realtime_session_id=realtime_session_id,
                    resumed=bool(voice_payload.get("resumed")),
                    room_name=str(transport.get("room_name") or "").strip() or None,
                    participant_identity=str(transport.get("participant_identity") or "").strip() or None,
                    agent_name=str(transport.get("agent_name") or "").strip() or None,
                    voice_mode=str(transport.get("voice_mode") or "").strip() or None,
                    configured_dispatch_strategy=str(transport.get("dispatch_strategy") or "").strip() or None,
                    token_issued=isinstance(transport.get("token"), str) and bool(str(transport.get("token")).strip()),
                    token_length=len(str(transport.get("token") or "")),
                    transcript_count=transcript_count,
                    trace_ids=trace_ids,
                    assistant_messages=assistant_messages,
                    speak_texts=speak_texts,
                    session_token_issued=True,
                    session_token_length=len(issued_session_token),
                    reason=_response_reason(transcript_response),
                )
            transcript_payload = transcript_response.json()
            transcript_count += 1
            assistant_messages.extend(_extract_rendered_message_texts(transcript_payload.get("messages")))
            trace_id = _extract_trace_id(transcript_payload)
            if trace_id is not None:
                trace_ids.append(trace_id)
            transcript_speak_texts = transcript_payload.get("speak_texts")
            if isinstance(transcript_speak_texts, list):
                speak_texts.extend(str(item).strip() for item in transcript_speak_texts if str(item).strip())

        if disconnect:
            disconnect_response = await active_client.post(
                f"/public/widget/sessions/{resolved_conversation_id}/voice/disconnect",
                headers=_widget_session_headers(issued_session_token),
                json={
                    "realtime_session_id": realtime_session_id,
                    "reason": "smoke_complete",
                    "metadata": {"smoke": True},
                },
            )
            if not disconnect_response.is_success:
                return WidgetVoiceSmokeResult(
                    ok=False,
                    provider="widget_voice_livekit",
                    base_url=effective_base_url,
                    agent_id=agent_id,
                    conversation_id=resolved_conversation_id,
                    realtime_session_id=realtime_session_id,
                    resumed=bool(voice_payload.get("resumed")),
                    room_name=str(transport.get("room_name") or "").strip() or None,
                    participant_identity=str(transport.get("participant_identity") or "").strip() or None,
                    agent_name=str(transport.get("agent_name") or "").strip() or None,
                    voice_mode=str(transport.get("voice_mode") or "").strip() or None,
                    configured_dispatch_strategy=str(transport.get("dispatch_strategy") or "").strip() or None,
                    token_issued=isinstance(transport.get("token"), str) and bool(str(transport.get("token")).strip()),
                    token_length=len(str(transport.get("token") or "")),
                    transcript_count=transcript_count,
                    trace_ids=trace_ids,
                    assistant_messages=assistant_messages,
                    speak_texts=speak_texts,
                    session_token_issued=True,
                    session_token_length=len(issued_session_token),
                    reason=_response_reason(disconnect_response),
                )
            disconnect_payload = disconnect_response.json()
            disconnected = bool(disconnect_payload.get("disconnected"))
            if not disconnected:
                return WidgetVoiceSmokeResult(
                    ok=False,
                    provider="widget_voice_livekit",
                    base_url=effective_base_url,
                    agent_id=agent_id,
                    conversation_id=resolved_conversation_id,
                    realtime_session_id=realtime_session_id,
                    resumed=bool(voice_payload.get("resumed")),
                    room_name=str(transport.get("room_name") or "").strip() or None,
                    participant_identity=str(transport.get("participant_identity") or "").strip() or None,
                    agent_name=str(transport.get("agent_name") or "").strip() or None,
                    voice_mode=str(transport.get("voice_mode") or "").strip() or None,
                    configured_dispatch_strategy=str(transport.get("dispatch_strategy") or "").strip() or None,
                    token_issued=isinstance(transport.get("token"), str) and bool(str(transport.get("token")).strip()),
                    token_length=len(str(transport.get("token") or "")),
                    transcript_count=transcript_count,
                    trace_ids=trace_ids,
                    assistant_messages=assistant_messages,
                    speak_texts=speak_texts,
                    session_token_issued=True,
                    session_token_length=len(issued_session_token),
                    reason="widget voice disconnect was not acknowledged",
                )

    return WidgetVoiceSmokeResult(
        ok=True,
        provider="widget_voice_livekit",
        base_url=effective_base_url,
        agent_id=agent_id,
        conversation_id=resolved_conversation_id,
        realtime_session_id=realtime_session_id,
        resumed=bool(voice_payload.get("resumed")),
        room_name=str(transport.get("room_name") or "").strip() or None,
        participant_identity=str(transport.get("participant_identity") or "").strip() or None,
        agent_name=str(transport.get("agent_name") or "").strip() or None,
        voice_mode=str(transport.get("voice_mode") or "").strip() or None,
        configured_dispatch_strategy=str(transport.get("dispatch_strategy") or "").strip() or None,
        token_issued=isinstance(transport.get("token"), str) and bool(str(transport.get("token")).strip()),
        token_length=len(str(transport.get("token") or "")),
        transcript_count=transcript_count,
        trace_ids=trace_ids,
        assistant_messages=assistant_messages,
        speak_texts=speak_texts,
        disconnected=disconnected,
        session_token_issued=True,
        session_token_length=len(issued_session_token),
    )


def _load_settings(args: argparse.Namespace) -> RuntimeSettings:
    env_file = getattr(args, "env_file", None)
    if env_file is not None:
        load_env_file(env_file, override=getattr(args, "override_env", False))
    return RuntimeSettings.from_env()


async def _run_livekit(args: argparse.Namespace) -> int:
    try:
        metadata = _parse_json_object(args.metadata_json, argument_name="--metadata-json")
    except ValueError as exc:
        payload = {"ok": False, "reason": str(exc)}
        _print(payload, as_json=args.json)
        return 1
    now = _utc_now()
    default_conversation_id, default_realtime_session_id = _default_livekit_ids(now)
    result = await run_livekit_smoke(
        settings=_load_settings(args),
        conversation_id=args.conversation_id or default_conversation_id,
        realtime_session_id=args.realtime_session_id or default_realtime_session_id,
        channel=args.channel,
        participant_identity=args.participant_identity,
        participant_name=args.participant_name,
        metadata=metadata,
        skip_dispatch=args.skip_dispatch,
        dispatch_strategy=args.dispatch_strategy,
    )
    _print(result.as_dict(), as_json=args.json)
    return 0 if result.ok else 1


async def _run_whatsapp(args: argparse.Namespace) -> int:
    result = await run_whatsapp_smoke(
        settings=_load_settings(args),
        phone_number_id=args.phone_number_id,
        recipient_id=args.recipient_id,
        texts=args.text,
    )
    _print(result.as_dict(), as_json=args.json)
    return 0 if result.ok else 1


async def _run_widget_chat(args: argparse.Namespace) -> int:
    result = await run_widget_chat_smoke(
        settings=_load_settings(args),
        base_url=args.base_url,
        agent_id=args.agent_id,
        conversation_id=args.conversation_id,
        session_token=args.session_token,
        texts=args.text,
    )
    _print(result.as_dict(), as_json=args.json)
    return 0 if result.ok else 1


async def _run_widget_voice(args: argparse.Namespace) -> int:
    try:
        metadata = _parse_json_object(args.metadata_json, argument_name="--metadata-json")
    except ValueError as exc:
        payload = {"ok": False, "reason": str(exc)}
        _print(payload, as_json=args.json)
        return 1
    default_texts = None if args.text else ([_default_widget_voice_text(_utc_now())] if args.bridge_transcript else None)
    result = await run_widget_voice_smoke(
        settings=_load_settings(args),
        base_url=args.base_url,
        agent_id=args.agent_id,
        conversation_id=args.conversation_id,
        session_token=args.session_token,
        participant_identity=args.participant_identity,
        participant_name=args.participant_name,
        metadata=metadata,
        texts=args.text if args.text else default_texts,
        provider_secret=args.provider_secret,
        disconnect=not args.skip_disconnect,
    )
    _print(result.as_dict(), as_json=args.json)
    return 0 if result.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run provider and surface smoke checks for the Ruhu realtime stack.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--env-file", type=Path)
        subparser.add_argument("--override-env", action="store_true")
        subparser.add_argument("--json", action="store_true")

    livekit = subparsers.add_parser("livekit", help="Issue a LiveKit transport grant and optionally attempt a real dispatch.")
    add_common(livekit)
    livekit.add_argument("--conversation-id")
    livekit.add_argument("--realtime-session-id")
    livekit.add_argument("--channel", default="web_widget")
    livekit.add_argument("--participant-identity")
    livekit.add_argument("--participant-name")
    livekit.add_argument("--metadata-json")
    livekit.add_argument("--skip-dispatch", action="store_true")
    livekit.add_argument(
        "--dispatch-strategy",
        choices=["room_config", "api_dispatch", "hybrid"],
        default="api_dispatch",
        help="Dispatch strategy used for the smoke probe. Defaults to api_dispatch to force a live provider check.",
    )
    livekit.set_defaults(handler=_run_livekit)

    whatsapp = subparsers.add_parser("whatsapp", help="Send one or more real Meta WhatsApp smoke texts.")
    add_common(whatsapp)
    whatsapp.add_argument("--phone-number-id", required=True)
    whatsapp.add_argument("--recipient-id", required=True)
    whatsapp.add_argument("--text", action="append")
    whatsapp.set_defaults(handler=_run_whatsapp)

    widget_chat = subparsers.add_parser(
        "widget-chat",
        help="Create a public widget session and send one or more real chat turns against a running app.",
    )
    add_common(widget_chat)
    widget_chat.add_argument("--base-url", default="http://127.0.0.1:8010")
    widget_chat.add_argument("--agent-id", required=True)
    widget_chat.add_argument("--conversation-id")
    widget_chat.add_argument("--session-token")
    widget_chat.add_argument("--text", action="append")
    widget_chat.set_defaults(handler=_run_widget_chat)

    widget_voice = subparsers.add_parser(
        "widget-voice",
        help="Start a public widget voice session and optionally bridge a final transcript against a running app.",
    )
    add_common(widget_voice)
    widget_voice.add_argument("--base-url", default="http://127.0.0.1:8010")
    widget_voice.add_argument("--agent-id", required=True)
    widget_voice.add_argument("--conversation-id")
    widget_voice.add_argument("--session-token")
    widget_voice.add_argument("--participant-identity")
    widget_voice.add_argument("--participant-name")
    widget_voice.add_argument("--metadata-json")
    widget_voice.add_argument("--provider-secret")
    widget_voice.add_argument("--text", action="append")
    widget_voice.add_argument(
        "--bridge-transcript",
        action="store_true",
        help="Bridge a final transcript after transport start. If --text is omitted, a default pricing prompt is used.",
    )
    widget_voice.add_argument("--skip-disconnect", action="store_true")
    widget_voice.set_defaults(handler=_run_widget_voice)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(asyncio.run(args.handler(args)))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
