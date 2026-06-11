from __future__ import annotations

import asyncio
import pickle
from types import SimpleNamespace

import httpx
import pytest

import ruhu.livekit_adapter as livekit_adapter
from ruhu.livekit_adapter import (
    LiveKitAdapterConfig,
    LiveKitControlPlaneClient,
    LiveKitDispatchClient,
    LiveKitPhoneAdapter,
    LiveKitTokenIssuer,
    RuhuLiveKitAgentWorker,
)
from ruhu.livekit_worker import build_livekit_agent_server_app
from ruhu.runtime_config import RuntimeSettings
from ruhu.schemas import RenderedMessage


def test_livekit_adapter_config_from_settings() -> None:
    settings = RuntimeSettings(
        livekit_server_url="wss://livekit.example.com",
        livekit_api_key="key",
        livekit_api_secret="secret",
        livekit_agent_name="voice-prod",
        livekit_room_prefix="calls",
        livekit_agents_sdk_version_target="1.5.2",
        livekit_voice_mode="realtime_assisted",
        livekit_dispatch_strategy="hybrid",
        livekit_metadata={"region": "eu"},
    )

    config = LiveKitAdapterConfig.from_settings(settings)

    assert config is not None
    assert config.server_url == "wss://livekit.example.com"
    assert config.api_key == "key"
    assert config.api_secret == "secret"
    assert config.agent_name == "voice-prod"
    assert config.room_prefix == "calls"
    assert config.sdk_version_target == "1.5.2"
    assert config.voice_mode == "realtime_assisted"
    assert config.dispatch_strategy == "hybrid"
    assert config.metadata == {"region": "eu"}


def test_livekit_phone_adapter_delegates_control_plane_calls() -> None:
    calls: dict[str, object] = {}
    config = LiveKitAdapterConfig(
        server_url="wss://livekit.example.com",
        api_key="key",
        api_secret="secret",
        agent_name="voice-prod",
        sdk_version_target="1.5.2",
        metadata={"region": "eu"},
    )

    def require_provider_secret(secret: str | None) -> None:
        calls["secret"] = secret

    def start_live_channel_session(**kwargs):
        calls["start"] = kwargs
        return SimpleNamespace(
            conversation_id="phone:call-1",
            realtime_session_id="rs-1",
            step_after="discover",
            messages=[RenderedMessage(text="Hello")],
            trace_id="trace-1",
            pending_tool_invocations=[],
        )

    def process_live_channel_message(**kwargs):
        calls["transcript"] = kwargs
        return SimpleNamespace(
            conversation_id="phone:call-1",
            realtime_session_id="rs-1",
            step_after="discover",
            messages=[RenderedMessage(text="How can I help?")],
            trace_id="trace-2",
            pending_tool_invocations=[],
        )

    def transition_provider_session(**kwargs):
        calls["transition"] = kwargs
        return SimpleNamespace(status=kwargs["target"])

    adapter = LiveKitPhoneAdapter(
        config=config,
        require_provider_secret=require_provider_secret,
        start_live_channel_session=start_live_channel_session,
        process_live_channel_message=process_live_channel_message,
        transition_provider_session=transition_provider_session,
        assistant_texts=lambda messages: [message.text for message in messages],
    )

    start_result = adapter.start_phone_call(
        payload=SimpleNamespace(
            agent_id="sales_agent",
            organization_id="org-demo",
            external_session_id="call-1",
            provider="telnyx",
            provider_session_id="RM_123",
            participant_identity="+2348000000000",
            metadata={"from_number": "+2348000000000"},
        ),
        provider_secret="shared-secret",
    )
    transcript_result = adapter.ingest_phone_transcript(
        call_id="call-1",
        payload=SimpleNamespace(
            agent_id="sales_agent",
            text="I want a demo",
            is_final=True,
            provider="telnyx",
            provider_session_id="RM_123",
            participant_identity="+2348000000000",
            idempotency_key="seg-1",
            metadata={"segment": 1},
        ),
        provider_secret="shared-secret",
    )
    transition_result = adapter.transition_phone_call(
        call_id="call-1",
        payload=SimpleNamespace(reason="hangup", metadata={"cause": "remote_end"}),
        provider_secret="shared-secret",
        target="ended",
    )

    assert calls["secret"] == "shared-secret"
    assert start_result["conversation_id"] == "phone:call-1"
    assert start_result["speak_texts"] == ["Hello"]
    start_call = calls["start"]
    assert isinstance(start_call, dict)
    assert start_call["provider"] == "livekit"
    assert start_call["organization_id"] == "org-demo"
    assert start_call["metadata"]["provider"] == "livekit"
    assert start_call["metadata"]["transport_provider"] == "livekit"
    assert start_call["metadata"]["telephony_provider"] == "telnyx"
    assert start_call["metadata"]["agent_name"] == "voice-prod"
    assert start_call["metadata"]["sdk_version_target"] == "1.5.2"
    assert transcript_result["trace_id"] == "trace-2"
    transcript_call = calls["transcript"]
    assert isinstance(transcript_call, dict)
    assert transcript_call["event_type"] == "user_final_transcript"
    assert transcript_call["provider"] == "livekit"
    assert transcript_call["metadata"]["provider"] == "livekit"
    assert transcript_call["metadata"]["transport_provider"] == "livekit"
    assert transcript_call["metadata"]["telephony_provider"] == "telnyx"
    assert transcript_call["metadata"]["agent_name"] == "voice-prod"
    transition_call = calls["transition"]
    assert isinstance(transition_call, dict)
    assert transition_call["target"] == "ended"
    assert transition_call["metadata"]["cause"] == "remote_end"
    assert transition_call["metadata"]["agent_name"] == "voice-prod"
    assert transition_result.status == "ended"


def test_livekit_token_issuer_builds_transport_with_fake_sdk() -> None:
    class FakeVideoGrants:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeAccessToken:
        def __init__(self, api_key: str, api_secret: str) -> None:
            self.api_key = api_key
            self.api_secret = api_secret
            self.identity = None
            self.name = None
            self.metadata = None
            self.grants = None
            self.room_config = None

        def with_identity(self, identity: str):
            self.identity = identity
            return self

        def with_name(self, name: str):
            self.name = name
            return self

        def with_metadata(self, metadata: str):
            self.metadata = metadata
            return self

        def with_grants(self, grants):
            self.grants = grants
            return self

        def with_room_config(self, room_config):
            self.room_config = room_config
            return self

        def to_jwt(self) -> str:
            return f"jwt::{self.identity}"

    class FakeRoomAgentDispatch:
        def __init__(self, *, agent_name: str, metadata: str) -> None:
            self.agent_name = agent_name
            self.metadata = metadata

    class FakeRoomConfiguration:
        def __init__(self, *, agents):
            self.agents = agents

    fake_sdk = SimpleNamespace(
        AccessToken=FakeAccessToken,
        VideoGrants=FakeVideoGrants,
        RoomAgentDispatch=FakeRoomAgentDispatch,
        RoomConfiguration=FakeRoomConfiguration,
    )
    issuer = LiveKitTokenIssuer(
        LiveKitAdapterConfig(
            server_url="wss://livekit.example.com",
            api_key="key",
            api_secret="secret",
            agent_name="voice-prod",
            room_prefix="ruhu",
            sdk_version_target="1.5.2",
            metadata={"region": "eu"},
        ),
        api_loader=lambda: fake_sdk,
    )

    grant = issuer.issue_voice_transport(
        channel="web_widget",
        conversation_id="conv_1",
        realtime_session_id="rs_1",
        participant_name="Visitor",
        metadata={
            "locale": "en-NG",
            "voice_interaction_policy": {
                "interruptibility_policy": "non_interruptible",
                "endpointing_ms": 900,
                "soft_timeout_ms": 750,
                "turn_eagerness": "high",
            },
        },
    )

    assert grant.provider == "livekit"
    assert grant.url == "wss://livekit.example.com"
    assert grant.token.startswith("jwt::web_widget:conv_1:rs_1")
    assert grant.dispatch["agent_name"] == "voice-prod"
    assert grant.dispatch["applied"] is True
    assert grant.dispatch["strategy"] == "hybrid"
    assert grant.voice_mode == "pipeline"
    assert grant.metadata["conversation_id"] == "conv_1"
    assert grant.metadata["realtime_session_id"] == "rs_1"
    assert grant.metadata["locale"] == "en-NG"
    assert grant.metadata["voice_interaction_policy"]["interruptibility_policy"] == "non_interruptible"
    assert grant.metadata["voice_interaction_policy"]["endpointing_ms"] == 900
    assert grant.metadata["voice_interaction_policy"]["soft_timeout_ms"] == 750
    assert grant.metadata["voice_interaction_policy"]["turn_eagerness"] == "high"


def test_livekit_dispatch_client_uses_api_dispatch_surface() -> None:
    class FakeDispatchAPI:
        async def create_dispatch(self, request):
            return SimpleNamespace(dispatch_id="dispatch-1", room=request.room, metadata=request.metadata)

    class FakeLiveKitAPI:
        def __init__(self, *args, **kwargs) -> None:
            self.agent_dispatch = FakeDispatchAPI()

        async def aclose(self) -> None:
            return None

    class FakeCreateAgentDispatchRequest:
        def __init__(self, *, room: str, agent_name: str, metadata: str) -> None:
            self.room = room
            self.agent_name = agent_name
            self.metadata = metadata

    fake_sdk = SimpleNamespace(
        LiveKitAPI=FakeLiveKitAPI,
        CreateAgentDispatchRequest=FakeCreateAgentDispatchRequest,
    )
    client = LiveKitDispatchClient(
        LiveKitAdapterConfig(
            server_url="wss://livekit.example.com",
            api_key="key",
            api_secret="secret",
            dispatch_strategy="api_dispatch",
        ),
        api_loader=lambda: fake_sdk,
    )

    import asyncio

    async def run() -> None:
        result = await client.create_dispatch(
            room_name="room-1",
            metadata={"conversation_id": "conv-1"},
            agent_name="ruhu-voice",
        )
        assert result.applied is True
        assert result.dispatch_id == "dispatch-1"
        assert result.strategy == "api_dispatch"

    asyncio.run(run())


def test_livekit_dispatch_client_creates_room_and_retries_when_room_is_missing() -> None:
    class FakeTwirpError(RuntimeError):
        pass

    class FakeDispatchAPI:
        def __init__(self) -> None:
            self.calls = 0

        async def create_dispatch(self, request):
            self.calls += 1
            if self.calls == 1:
                raise FakeTwirpError("TwirpError(code=internal, message=could not find object, status=500)")
            return SimpleNamespace(dispatch_id="dispatch-2", room=request.room, metadata=request.metadata)

    class FakeRoomAPI:
        def __init__(self) -> None:
            self.created: list[str] = []

        async def create_room(self, request):
            self.created.append(request.name)
            return SimpleNamespace(name=request.name, sid="RM_123")

    class FakeLiveKitAPI:
        def __init__(self, *args, **kwargs) -> None:
            self.agent_dispatch = FakeDispatchAPI()
            self.room = FakeRoomAPI()

        async def aclose(self) -> None:
            return None

    class FakeCreateAgentDispatchRequest:
        def __init__(self, *, room: str, agent_name: str, metadata: str) -> None:
            self.room = room
            self.agent_name = agent_name
            self.metadata = metadata

    class FakeCreateRoomRequest:
        def __init__(self, *, name: str) -> None:
            self.name = name

    fake_sdk = SimpleNamespace(
        LiveKitAPI=FakeLiveKitAPI,
        CreateAgentDispatchRequest=FakeCreateAgentDispatchRequest,
        CreateRoomRequest=FakeCreateRoomRequest,
    )
    client = LiveKitDispatchClient(
        LiveKitAdapterConfig(
            server_url="ws://localhost:7880",
            api_key="key",
            api_secret="secret",
            dispatch_strategy="api_dispatch",
        ),
        api_loader=lambda: fake_sdk,
    )

    import asyncio

    async def run() -> None:
        result = await client.create_dispatch(
            room_name="room-2",
            metadata={"conversation_id": "conv-2"},
            agent_name="ruhu-voice",
        )
        assert result.applied is True
        assert result.dispatch_id == "dispatch-2"
        assert result.strategy == "api_dispatch"

    asyncio.run(run())


def test_livekit_worker_bridge_posts_to_control_plane() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = {}
        if request.content:
            payload = httpx.Request(
                request.method,
                str(request.url),
                content=request.content,
                headers=request.headers,
            ).read()
        calls.append((request.url.path, {"method": request.method, "body": request.content.decode() if request.content else ""}))
        return httpx.Response(200, json={"ok": True, "path": request.url.path})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://control-plane")
    bridge = LiveKitControlPlaneClient(
        base_url="http://control-plane",
        provider_secret="shared-secret",
        client=client,
    )
    worker = RuhuLiveKitAgentWorker(
        config=LiveKitAdapterConfig(
            server_url="wss://livekit.example.com",
            api_key="key",
            api_secret="secret",
        ),
        control_plane_client=bridge,
        sdk_loader=lambda: SimpleNamespace(AgentSession=dict),
    )

    import asyncio

    async def run() -> None:
        partial = await worker.emit_partial_transcript(
            realtime_session_id="rs-1",
            text="hello",
            provider_session_id="RM_1",
        )
        final = await worker.emit_final_transcript(
            realtime_session_id="rs-1",
            text="hello there",
            idempotency_key="seg-1",
        )
        message = await worker.emit_text_message(
            realtime_session_id="rs-1",
            text="hello via chat",
            attachment_ids=["att-1"],
        )
        signaled = await worker.emit_voice_signal(
            realtime_session_id="rs-1",
            signal="user_barged_in",
            reason="speech_detected",
        )
        acknowledged = await worker.acknowledge_assistant_output(
            realtime_session_id="rs-1",
            delivery_id="delivery-1",
            stage="resolved",
            idempotency_key="delivery-1:resolved",
        )
        ended = await worker.mark_session_ended(realtime_session_id="rs-1", reason="hangup")
        session = worker.create_agent_session(agent="assistant")

        assert partial["ok"] is True
        assert final["ok"] is True
        assert message["ok"] is True
        assert signaled["ok"] is True
        assert acknowledged["ok"] is True
        assert ended["ok"] is True
        assert session == {"agent": "assistant"}

    asyncio.run(run())
    assert calls[0][0] == "/providers/livekit/voice/sessions/rs-1/transcripts"
    assert '"is_final":false' in calls[0][1]["body"]
    assert calls[1][0] == "/providers/livekit/voice/sessions/rs-1/transcripts"
    assert '"is_final":true' in calls[1][1]["body"]
    assert calls[2][0] == "/providers/livekit/voice/sessions/rs-1/messages"
    assert '"attachment_ids":["att-1"]' in calls[2][1]["body"]
    assert calls[3][0] == "/providers/livekit/voice/sessions/rs-1/signals"
    assert '"signal":"user_barged_in"' in calls[3][1]["body"]
    assert calls[4][0] == "/providers/livekit/voice/sessions/rs-1/assistant-outputs/delivery-1/ack"
    assert '"stage":"resolved"' in calls[4][1]["body"]
    assert calls[5][0] == "/providers/livekit/voice/sessions/rs-1/end"


def test_final_transcript_commit_retries_transient_control_plane_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(livekit_adapter, "_TRANSCRIPT_POST_RETRY_DELAYS_SECONDS", (0.0, 0.0, 0.0))
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.content.decode())
        if len(calls) == 1:
            return httpx.Response(500, json={"detail": "temporary failure"})
        return httpx.Response(
            200,
            json={
                "conversation_id": "conv-1",
                "realtime_session_id": "rs-1",
                "step_after": "entry",
                "speak_texts": ["Recovered"],
                "messages": [],
                "trace_id": "trace-recovered",
                "pending_tool_invocations": [],
            },
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://control-plane",
    )
    bridge = LiveKitControlPlaneClient(
        base_url="http://control-plane",
        provider_secret="shared-secret",
        client=client,
    )

    async def run() -> None:
        result = await bridge.commit_final_transcript(
            realtime_session_id="rs-1",
            text="I want to apply for a loan.",
            idempotency_key="rs-1:final:abc",
        )
        assert result["trace_id"] == "trace-recovered"

    asyncio.run(run())
    assert len(calls) == 2
    assert all('"idempotency_key":"rs-1:final:abc"' in body for body in calls)
    asyncio.run(client.aclose())


def test_livekit_control_plane_client_pickles_without_live_http_client_state() -> None:
    client = httpx.AsyncClient()
    bridge = LiveKitControlPlaneClient(
        base_url="http://control-plane",
        provider_secret="shared-secret",
        client=client,
    )
    restored = pickle.loads(pickle.dumps(bridge))
    assert isinstance(restored, LiveKitControlPlaneClient)
    assert restored.base_url == "http://control-plane"
    assert restored.provider_secret == "shared-secret"
    assert restored.client is None
    asyncio.run(client.aclose())


def test_livekit_worker_replay_suppresses_outputs_cut_off_by_interruption() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/providers/livekit/conversations/conv-1/assistant-outputs"
        return httpx.Response(
            200,
            json=[
                {
                    "delivery_id": "evt-3",
                    "conversation_id": "conv-1",
                    "source_event_id": "evt-3",
                    "conversation_sequence": 3,
                    "text": "Replacement answer",
                    "trace_id": "trace-2",
                    "turn_id": "turn-2",
                },
            ],
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://control-plane")
    bridge = LiveKitControlPlaneClient(
        base_url="http://control-plane",
        provider_secret="shared-secret",
        client=client,
    )
    worker = RuhuLiveKitAgentWorker(
        config=LiveKitAdapterConfig(
            server_url="wss://livekit.example.com",
            api_key="key",
            api_secret="secret",
        ),
        control_plane_client=bridge,
        sdk_loader=lambda: SimpleNamespace(AgentSession=dict),
    )

    async def run() -> None:
        outputs = await worker.replay_assistant_voice_outputs(conversation_id="conv-1")
        assert outputs == [
            {
                "delivery_id": "evt-3",
                "event_id": "evt-3",
                "conversation_sequence": 3,
                "text": "Replacement answer",
                "trace_id": "trace-2",
                "turn_id": "turn-2",
            }
        ]

    asyncio.run(run())


def test_livekit_worker_replay_suppresses_outputs_cut_off_by_session_staleness() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/providers/livekit/conversations/conv-1/assistant-outputs"
        return httpx.Response(
            200,
            json=[
                {
                    "delivery_id": "evt-5",
                    "conversation_id": "conv-1",
                    "source_event_id": "evt-5",
                    "conversation_sequence": 5,
                    "text": "Output after disconnect",
                    "trace_id": "trace-2",
                    "turn_id": "turn-2",
                },
            ],
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://control-plane")
    bridge = LiveKitControlPlaneClient(
        base_url="http://control-plane",
        provider_secret="shared-secret",
        client=client,
    )
    worker = RuhuLiveKitAgentWorker(
        config=LiveKitAdapterConfig(
            server_url="wss://livekit.example.com",
            api_key="key",
            api_secret="secret",
        ),
        control_plane_client=bridge,
        sdk_loader=lambda: SimpleNamespace(AgentSession=dict),
    )

    async def run() -> None:
        outputs = await worker.replay_assistant_voice_outputs(conversation_id="conv-1")
        assert outputs == [
            {
                "delivery_id": "evt-5",
                "event_id": "evt-5",
                "conversation_sequence": 5,
                "text": "Output after disconnect",
                "trace_id": "trace-2",
                "turn_id": "turn-2",
            }
        ]

    asyncio.run(run())


def test_livekit_agent_server_app_registers_rtc_session() -> None:
    registered: dict[str, object] = {}

    class FakeServer:
        def rtc_session(self, *, agent_name: str):
            def decorator(fn):
                registered["agent_name"] = agent_name
                registered["fn"] = fn
                return fn

            return decorator

    class FakeSDK:
        AgentServer = FakeServer

    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    client = httpx.AsyncClient(transport=transport, base_url="http://control-plane")
    app = build_livekit_agent_server_app(
        control_plane_base_url="http://control-plane",
        provider_secret="shared-secret",
        runtime_settings=RuntimeSettings(
            livekit_server_url="wss://livekit.example.com",
            livekit_api_key="key",
            livekit_api_secret="secret",
            livekit_agent_name="ruhu-voice",
            livekit_control_plane_base_url="http://control-plane",
            provider_shared_secret="shared-secret",
        ),
        http_client=client,
    )
    app._sdk_loader = lambda: FakeSDK()  # type: ignore[assignment]

    server = app.build_server()

    assert isinstance(server, FakeServer)
    assert registered["agent_name"] == "ruhu-voice"
