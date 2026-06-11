from __future__ import annotations

import asyncio
import json

import httpx

from ruhu.livekit_adapter import LiveKitDispatchResult, LiveKitVoiceTransportGrant
from ruhu.realtime_smoke import (
    run_livekit_smoke,
    run_whatsapp_smoke,
    run_widget_chat_smoke,
    run_widget_voice_smoke,
)
from ruhu.runtime_config import RuntimeSettings


def test_run_livekit_smoke_issues_transport_and_dispatches() -> None:
    class FakeTokenIssuer:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def issue_voice_transport(self, **kwargs):
            self.calls.append(kwargs)
            return LiveKitVoiceTransportGrant(
                provider="livekit",
                url="wss://livekit.example.com",
                room_name="smoke-room-1",
                token="jwt::smoke-token",
                participant_identity="visitor-1",
                agent_name="ruhu-voice",
                sdk_version_target="1.5.2",
                voice_mode="pipeline",
                dispatch_strategy="room_config",
                dispatch={"strategy": "room_config", "attempted": True, "applied": True},
                metadata={"conversation_id": "conv-1", "smoke": True},
            )

    class FakeDispatchClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def create_dispatch(self, **kwargs):
            self.calls.append(kwargs)
            return LiveKitDispatchResult(
                strategy="api_dispatch",
                attempted=True,
                applied=True,
                room_name="smoke-room-1",
                agent_name="ruhu-voice",
                dispatch_id="dispatch-1",
                metadata={"mechanism": "api_dispatch"},
            )

    async def run() -> None:
        token_issuer = FakeTokenIssuer()
        dispatch_client = FakeDispatchClient()
        result = await run_livekit_smoke(
            settings=RuntimeSettings(
                livekit_server_url="wss://livekit.example.com",
                livekit_api_key="key",
                livekit_api_secret="secret",
                livekit_dispatch_strategy="room_config",
            ),
            conversation_id="conv-1",
            realtime_session_id="rs-1",
            metadata={"tenant": "smoke"},
            token_issuer=token_issuer,
            dispatch_client=dispatch_client,
        )

        assert result.ok is True
        assert result.room_name == "smoke-room-1"
        assert result.token_issued is True
        assert result.token_length == len("jwt::smoke-token")
        assert result.dispatch["dispatch_id"] == "dispatch-1"
        assert result.configured_dispatch_strategy == "room_config"
        assert "token" not in result.as_dict()
        assert token_issuer.calls[0]["metadata"]["tenant"] == "smoke"
        assert token_issuer.calls[0]["metadata"]["smoke"] is True
        assert dispatch_client.calls[0]["room_name"] == "smoke-room-1"

    asyncio.run(run())


def test_run_livekit_smoke_reports_missing_config() -> None:
    async def run() -> None:
        result = await run_livekit_smoke(
            settings=RuntimeSettings(),
            conversation_id="conv-1",
            realtime_session_id="rs-1",
        )

        assert result.ok is False
        assert result.reason == "LiveKit adapter config is incomplete"

    asyncio.run(run())


def test_run_whatsapp_smoke_sends_configured_messages() -> None:
    calls: list[dict[str, object]] = []

    async def fake_send(config, *, recipient_id: str, texts, client=None):
        calls.append(
            {
                "phone_number_id": config.phone_number_id,
                "recipient_id": recipient_id,
                "texts": list(texts),
            }
        )
        return [{"status": "sent", "text": text, "message_id": f"wamid.{index}"} for index, text in enumerate(calls[-1]["texts"], start=1)]

    async def run() -> None:
        result = await run_whatsapp_smoke(
            settings=RuntimeSettings(
                whatsapp_meta_channels={
                    "12345": {
                        "agent_id": "sales_agent",
                        "phone_number_id": "12345",
                        "verify_token": "verify-token",
                        "access_token": "access-token",
                        "app_secret": "app-secret",
                        "organization_id": "org-1",
                    }
                }
            ),
            phone_number_id="12345",
            recipient_id="2348000000000",
            texts=["hello from smoke"],
            send_texts=fake_send,
        )

        assert result.ok is True
        assert result.agent_id == "sales_agent"
        assert result.organization_id == "org-1"
        assert result.text_count == 1
        assert result.deliveries[0]["message_id"] == "wamid.1"
        assert calls == [
            {
                "phone_number_id": "12345",
                "recipient_id": "2348000000000",
                "texts": ["hello from smoke"],
            }
        ]

    asyncio.run(run())


def test_run_whatsapp_smoke_generates_default_text_when_none_provided() -> None:
    captured: dict[str, object] = {}

    async def fake_send(config, *, recipient_id: str, texts, client=None):
        captured["texts"] = list(texts)
        return [{"status": "sent", "text": captured["texts"][0], "message_id": "wamid.1"}]

    async def run() -> None:
        result = await run_whatsapp_smoke(
            settings=RuntimeSettings(
                whatsapp_meta_channels={
                    "12345": {
                        "agent_id": "sales_agent",
                        "phone_number_id": "12345",
                        "verify_token": "verify-token",
                        "access_token": "access-token",
                        "app_secret": "app-secret",
                    }
                }
            ),
            phone_number_id="12345",
            recipient_id="2348000000000",
            send_texts=fake_send,
        )

        assert result.ok is True
        assert captured["texts"]
        assert str(captured["texts"][0]).startswith("Ruhu WhatsApp smoke check ")

    asyncio.run(run())


def test_run_whatsapp_smoke_reports_http_failures() -> None:
    async def fake_send(config, *, recipient_id: str, texts, client=None):
        request = httpx.Request("POST", config.messages_url)
        response = httpx.Response(401, request=request, text="invalid token")
        raise httpx.HTTPStatusError("unauthorized", request=request, response=response)

    async def run() -> None:
        result = await run_whatsapp_smoke(
            settings=RuntimeSettings(
                whatsapp_meta_channels={
                    "12345": {
                        "agent_id": "sales_agent",
                        "phone_number_id": "12345",
                        "verify_token": "verify-token",
                        "access_token": "access-token",
                        "app_secret": "app-secret",
                    }
                }
            ),
            phone_number_id="12345",
            recipient_id="2348000000000",
            texts=["hello"],
            send_texts=fake_send,
        )

        assert result.ok is False
        assert "HTTP 401" in str(result.reason)
        assert "invalid token" in str(result.reason)

    asyncio.run(run())


def test_run_widget_chat_smoke_creates_session_and_sends_turn() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/public/widget/sessions":
            payload = json.loads(request.content.decode())
            assert payload == {"agent_id": "sales_agent"}
            return httpx.Response(
                200,
                json={
                    "conversation_id": "widget-conv-1",
                    "agent_id": "sales_agent",
                    "step_id": "entry",
                    "resumed": False,
                    "session_token": "widget-token",
                    "messages": [{"text": "Hi! Ask us anything."}],
                    "pending_tool_invocations": [],
                },
            )
        if request.method == "POST" and request.url.path == "/public/widget/sessions/widget-conv-1/messages":
            assert request.headers["X-Ruhu-Widget-Session-Token"] == "widget-token"
            payload = json.loads(request.content.decode())
            assert payload == {"text": "hello from widget smoke"}
            return httpx.Response(
                200,
                json={
                    "conversation_id": "widget-conv-1",
                    "step_after": "faq_followup",
                    "messages": [{"text": "Pricing starts at $99/mo."}],
                    "trace_id": "trace-widget-1",
                    "pending_tool_invocations": [],
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async def run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            result = await run_widget_chat_smoke(
                base_url="http://testserver",
                agent_id="sales_agent",
                texts=["hello from widget smoke"],
                client=client,
            )

        assert result.ok is True
        assert result.conversation_id == "widget-conv-1"
        assert result.turns_sent == 1
        assert result.trace_ids == ["trace-widget-1"]
        assert result.assistant_messages == ["Hi! Ask us anything.", "Pricing starts at $99/mo."]
        assert result.session_token_issued is True
        assert result.session_token_length == len("widget-token")
        assert "session_token" not in result.as_dict()

    asyncio.run(run())


def test_run_widget_voice_smoke_starts_transport_bridges_transcript_and_disconnects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/public/widget/sessions":
            return httpx.Response(
                200,
                json={
                    "conversation_id": "widget-conv-voice-1",
                    "agent_id": "sales_agent",
                    "step_id": "entry",
                    "resumed": False,
                    "session_token": "widget-voice-token",
                    "messages": [{"text": "Hi! Ask us anything."}],
                    "pending_tool_invocations": [],
                },
            )
        if request.method == "POST" and request.url.path == "/public/widget/sessions/widget-conv-voice-1/voice":
            assert request.headers["X-Ruhu-Widget-Session-Token"] == "widget-voice-token"
            payload = json.loads(request.content.decode())
            assert payload["participant_name"] == "Ada"
            assert payload["metadata"]["smoke"] is True
            return httpx.Response(
                200,
                json={
                    "conversation_id": "widget-conv-voice-1",
                    "realtime_session_id": "rs-widget-1",
                    "resumed": False,
                    "step_after": "entry",
                    "transport": {
                        "provider": "livekit",
                        "url": "ws://localhost:7880",
                        "room_name": "room-rs-widget-1",
                        "token": "jwt::widget-voice-token",
                        "participant_identity": "visitor-1",
                        "agent_name": "ruhu-voice",
                        "sdk_version_target": "1.5.2",
                        "voice_mode": "pipeline",
                        "dispatch_strategy": "api_dispatch",
                        "dispatch": {"attempted": True, "applied": True},
                        "metadata": {"agent_id": "sales_agent"},
                    },
                    "pending_tool_invocations": [],
                },
            )
        if request.method == "POST" and request.url.path == "/providers/livekit/voice/sessions/rs-widget-1/transcripts":
            assert request.headers["X-Ruhu-Provider-Secret"] == "shared-secret"
            payload = json.loads(request.content.decode())
            assert payload["text"] == "Tell me about pricing."
            assert payload["is_final"] is True
            assert payload["idempotency_key"] == "rs-widget-1:smoke-1"
            return httpx.Response(
                200,
                json={
                    "conversation_id": "widget-conv-voice-1",
                    "realtime_session_id": "rs-widget-1",
                    "step_after": "faq_followup",
                    "speak_texts": ["Pricing starts at $99/mo."],
                    "messages": [{"text": "Pricing starts at $99/mo."}],
                    "trace_id": "trace-voice-1",
                    "pending_tool_invocations": [],
                },
            )
        if request.method == "POST" and request.url.path == "/public/widget/sessions/widget-conv-voice-1/voice/disconnect":
            assert request.headers["X-Ruhu-Widget-Session-Token"] == "widget-voice-token"
            payload = json.loads(request.content.decode())
            assert payload["realtime_session_id"] == "rs-widget-1"
            return httpx.Response(
                200,
                json={
                    "disconnected": True,
                    "session": {
                        "conversation_id": "widget-conv-voice-1",
                        "realtime_session_id": "rs-widget-1",
                        "channel": "web_widget",
                        "provider": "livekit",
                        "status": "disconnected",
                        "updated_at": "2026-04-11T12:00:00Z",
                    },
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async def run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            result = await run_widget_voice_smoke(
                base_url="http://testserver",
                agent_id="sales_agent",
                participant_name="Ada",
                texts=["Tell me about pricing."],
                provider_secret="shared-secret",
                client=client,
            )

        assert result.ok is True
        assert result.conversation_id == "widget-conv-voice-1"
        assert result.realtime_session_id == "rs-widget-1"
        assert result.room_name == "room-rs-widget-1"
        assert result.token_issued is True
        assert result.token_length == len("jwt::widget-voice-token")
        assert result.transcript_count == 1
        assert result.trace_ids == ["trace-voice-1"]
        assert result.assistant_messages == ["Hi! Ask us anything.", "Pricing starts at $99/mo."]
        assert result.speak_texts == ["Pricing starts at $99/mo."]
        assert result.disconnected is True
        assert "token" not in result.as_dict()

    asyncio.run(run())


def test_run_widget_voice_smoke_requires_provider_secret_for_transcript_bridge() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/public/widget/sessions":
            return httpx.Response(
                200,
                json={
                    "conversation_id": "widget-conv-voice-2",
                    "agent_id": "sales_agent",
                    "step_id": "entry",
                    "resumed": False,
                    "session_token": "widget-voice-token",
                    "messages": [],
                    "pending_tool_invocations": [],
                },
            )
        if request.method == "POST" and request.url.path == "/public/widget/sessions/widget-conv-voice-2/voice":
            return httpx.Response(
                200,
                json={
                    "conversation_id": "widget-conv-voice-2",
                    "realtime_session_id": "rs-widget-2",
                    "resumed": False,
                    "transport": {
                        "provider": "livekit",
                        "url": "ws://localhost:7880",
                        "room_name": "room-rs-widget-2",
                        "token": "jwt::widget-voice-token",
                        "participant_identity": "visitor-2",
                        "agent_name": "ruhu-voice",
                        "sdk_version_target": "1.5.2",
                        "voice_mode": "pipeline",
                        "dispatch_strategy": "api_dispatch",
                    },
                    "pending_tool_invocations": [],
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async def run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            result = await run_widget_voice_smoke(
                settings=RuntimeSettings(),
                base_url="http://testserver",
                agent_id="sales_agent",
                texts=["Tell me about pricing."],
                client=client,
            )

        assert result.ok is False
        assert result.reason == "provider secret required to bridge widget voice transcripts"

    asyncio.run(run())
