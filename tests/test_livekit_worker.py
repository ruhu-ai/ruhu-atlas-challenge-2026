from __future__ import annotations

import asyncio
import inspect
import json
import pickle
import os
from types import SimpleNamespace
from typing import Any

import ruhu.livekit_worker as livekit_worker_module
from ruhu.livekit_adapter import LiveKitAdapterConfig, LiveKitWorkerDispatchContext
from ruhu.livekit_worker import RuhuLiveKitAgentServerApp, _resolve_vertex_llm_location


class _FakeEmitter:
    def __init__(self) -> None:
        self._events: dict[str, list[Any]] = {}

    def on(self, event: str, callback: Any | None = None) -> Any:
        if callback is None:
            def decorator(fn: Any) -> Any:
                self.on(event, fn)
                return fn

            return decorator
        assert not inspect.iscoroutinefunction(callback)
        self._events.setdefault(event, []).append(callback)
        return callback

    def off(self, event: str, callback: Any) -> None:
        callbacks = self._events.get(event)
        if not callbacks:
            return
        self._events[event] = [item for item in callbacks if item is not callback]

    def emit(self, event: str, *args: Any) -> None:
        for callback in list(self._events.get(event, [])):
            callback(*args)


class _FakeAudioOutput(_FakeEmitter):
    pass


class _FakeRoom:
    def __init__(
        self,
        name: str,
        *,
        fail_on_duplicate_text_handler: bool = False,
        fail_on_duplicate_byte_handler: bool = False,
        fail_on_unregister_text_handler: bool = False,
        fail_on_unregister_byte_handler: bool = False,
    ) -> None:
        self.name = name
        self._text_handlers: dict[str, Any] = {}
        self._byte_handlers: dict[str, Any] = {}
        self.unregister_calls: dict[str, list[str]] = {"text": [], "byte": []}
        self.fail_on_duplicate_text_handler = fail_on_duplicate_text_handler
        self.fail_on_duplicate_byte_handler = fail_on_duplicate_byte_handler
        self.fail_on_unregister_text_handler = fail_on_unregister_text_handler
        self.fail_on_unregister_byte_handler = fail_on_unregister_byte_handler
        self.data_handlers: list[Any] = []

    def register_text_stream_handler(self, topic: str, handler: Any) -> None:
        if topic in self._text_handlers and self.fail_on_duplicate_text_handler:
            raise ValueError("text stream handler for topic '%s' already set" % topic)
        self._text_handlers[topic] = handler

    def register_byte_stream_handler(self, topic: str, handler: Any) -> None:
        if topic in self._byte_handlers and self.fail_on_duplicate_byte_handler:
            raise ValueError("byte stream handler for topic '%s' already set" % topic)
        self._byte_handlers[topic] = handler

    def unregister_text_stream_handler(self, topic: str) -> None:
        self.unregister_calls["text"].append(topic)
        if self.fail_on_unregister_text_handler:
            raise RuntimeError("failed to unregister text stream handler")
        self._text_handlers.pop(topic, None)

    def unregister_byte_stream_handler(self, topic: str) -> None:
        self.unregister_calls["byte"].append(topic)
        if self.fail_on_unregister_byte_handler:
            raise RuntimeError("failed to unregister byte stream handler")
        self._byte_handlers.pop(topic, None)

    def emit_text_stream(self, topic: str, reader: Any, participant_identity: str) -> None:
        self._text_handlers[topic](reader, participant_identity)

    def emit_byte_stream(self, topic: str, reader: Any, participant_identity: str) -> None:
        self._byte_handlers[topic](reader, participant_identity)

    def on(self, event: str, callback: Any | None = None) -> Any:
        if callback is None:
            def decorator(fn: Any) -> Any:
                self.on(event, fn)
                return fn

            return decorator
        if event != "data_received":
            return callback
        self.data_handlers.append(callback)
        return callback

    def emit_data_packet(self, data: bytes, topic: str) -> None:
        event = SimpleNamespace(topic=topic, data=data, participant=None)
        for callback in list(self.data_handlers):
            callback(event)


class _FakeTextStreamReader:
    def __init__(self, text: str, *, attributes: dict[str, object] | None = None, attachments: list[str] | None = None) -> None:
        self._text = text
        self.info = SimpleNamespace(attributes=attributes or {}, attachments=attachments or [])

    async def read_all(self) -> str:
        return self._text


class _FakeByteStreamReader:
    def __init__(self, chunks: list[bytes], *, attributes: dict[str, object] | None = None, attachments: list[str] | None = None) -> None:
        self._chunks = list(chunks)
        self.info = SimpleNamespace(attributes=attributes or {}, attachments=attachments or [])

    def __aiter__(self) -> "_FakeByteStreamReader":
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeSpeechHandle:
    def __init__(self, text: str) -> None:
        self.text = text

    async def wait_for_playout(self) -> None:
        return None


class _FakeSession(_FakeEmitter):
    def __init__(self) -> None:
        super().__init__()
        self.agent_state = "initializing"
        self.user_state = "listening"
        self.output = SimpleNamespace(audio=None)
        self.started_kwargs: dict[str, object] | None = None
        self.said_texts: list[str] = []

    async def start(self, **kwargs: Any) -> None:
        self.started_kwargs = kwargs
        self.output.audio = _FakeAudioOutput()

    def say(self, text: Any, **_kwargs: Any) -> _FakeSpeechHandle:
        # text may be str or AsyncIterable[str] after the streaming refactor.
        # Store raw value; use collect_said_texts() for assertions.
        self.said_texts.append(text)
        return _FakeSpeechHandle(text if isinstance(text, str) else "<stream>")

    async def collect_said_texts(self) -> list[str]:
        """Resolve any async iterables in said_texts to plain strings."""
        result: list[str] = []
        for item in self.said_texts:
            if hasattr(item, "__aiter__"):
                parts: list[str] = []
                async for chunk in item:
                    parts.append(chunk)
                result.append("".join(parts).strip())
            else:
                result.append(item)
        return result


class _FakeHangingSession(_FakeSession):
    async def start(self, **kwargs: Any) -> None:
        self.started_kwargs = kwargs
        await asyncio.sleep(60)


class _RecordingWorker:
    def __init__(self, session: _FakeSession) -> None:
        self.config = LiveKitAdapterConfig(
            server_url="ws://localhost:7880",
            api_key="key",
            api_secret="secret",
            agent_name="ruhu-voice",
        )
        self._session = session
        self.signal_calls: list[dict[str, object]] = []
        self.final_transcript_calls: list[dict[str, object]] = []
        self.partial_transcript_calls: list[dict[str, object]] = []
        self.text_message_calls: list[dict[str, object]] = []
        self.ended_calls: list[dict[str, object]] = []
        self.errored_calls: list[dict[str, object]] = []
        self.assistant_output_ack_calls: list[dict[str, object]] = []
        self.replay_calls: list[dict[str, object]] = []
        self.replay_outputs: list[dict[str, object]] = []
        self.replay_outputs_by_after_sequence: dict[int, list[dict[str, object]]] = {}

    def create_managed_agent_session(self, *, voice_mode: str | None = None, **_kwargs: Any) -> _FakeSession:
        assert voice_mode == "pipeline"
        return self._session

    async def emit_voice_signal(self, **kwargs: Any) -> dict[str, object]:
        self.signal_calls.append(kwargs)
        return {"ok": True}

    async def emit_final_transcript(self, **kwargs: Any) -> dict[str, object]:
        self.final_transcript_calls.append(kwargs)
        return {"ok": True}

    async def emit_partial_transcript(self, **kwargs: Any) -> dict[str, object]:
        self.partial_transcript_calls.append(kwargs)
        return {"ok": True}

    async def emit_text_message(self, **kwargs: Any) -> dict[str, object]:
        self.text_message_calls.append(kwargs)
        return {"ok": True}

    async def mark_session_ended(self, **kwargs: Any) -> dict[str, object]:
        self.ended_calls.append(kwargs)
        return {"ok": True}

    async def mark_session_errored(self, **kwargs: Any) -> dict[str, object]:
        self.errored_calls.append(kwargs)
        return {"ok": True}

    async def acknowledge_assistant_output(self, **kwargs: Any) -> dict[str, object]:
        self.assistant_output_ack_calls.append(kwargs)
        return {"ok": True}

    async def replay_assistant_voice_outputs(
        self,
        *,
        conversation_id: str,
        after_sequence: int = 0,
    ) -> list[dict[str, object]]:
        self.replay_calls.append(
            {
                "conversation_id": conversation_id,
                "after_sequence": after_sequence,
            }
        )
        if after_sequence in self.replay_outputs_by_after_sequence:
            return list(self.replay_outputs_by_after_sequence[after_sequence])
        return list(self.replay_outputs)


def _dispatch_context() -> LiveKitWorkerDispatchContext:
    return LiveKitWorkerDispatchContext(
        conversation_id="conv-widget-1",
        realtime_session_id="rs-widget-1",
        agent_id="sales_agent",
        agent_version_id="",
        channel="web_widget",
        room_name="room-rs-widget-1",
        voice_mode="pipeline",
        participant_identity="visitor-1",
        provider_session_id="room-rs-widget-1",
        metadata={"tenant": "smoke"},
    )


async def _drain() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


def test_livekit_worker_loads_dev_env_file_override(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".dev.override.env"
    marker_key = "RUHU_LIVEKIT_TEST_MARKER"
    env_file.write_text(
        "RUHU_LIVEKIT_SERVER_URL=ws://override-host:7777\n"
        "RUHU_LIVEKIT_API_KEY=override-key\n"
        f"{marker_key}=override-marker\n"
    )
    monkeypatch.setenv("RUHU_DEV_ENV_FILE", str(env_file))
    monkeypatch.delenv("RUHU_LIVEKIT_TEST_LEGACY_MARKER", raising=False)
    # Keep RUHU_LIVEKIT_SERVER_URL/API_KEY explicit in test environments where
    # the repository's development env is loaded by default.
    monkeypatch.delenv("RUHU_LIVEKIT_SERVER_URL", raising=False)
    monkeypatch.delenv("RUHU_LIVEKIT_API_KEY", raising=False)

    livekit_worker_module._load_runtime_env_files()

    assert os.getenv(marker_key) == "override-marker"


def test_livekit_worker_dispatch_context_from_raw_handles_invalid_json() -> None:
    dispatch_context = LiveKitWorkerDispatchContext.from_raw("{not-json}")
    assert dispatch_context.conversation_id == ""
    assert dispatch_context.realtime_session_id == ""
    assert dispatch_context.room_name == ""
    assert dispatch_context.voice_mode == "pipeline"


def test_livekit_worker_entrypoint_uses_fallback_context_when_metadata_is_invalid() -> None:
    session = _FakeSession()
    worker = _RecordingWorker(session)
    app = RuhuLiveKitAgentServerApp(worker=worker, sdk_loader=lambda: None)
    ctx = SimpleNamespace(
        room=SimpleNamespace(name="room-rs-invalid"),
        participant=None,
        job=SimpleNamespace(metadata=7),
    )
    dispatch_context = app._dispatch_context_from_ctx(ctx)
    assert dispatch_context.conversation_id == ""
    assert dispatch_context.realtime_session_id == ""
    assert dispatch_context.room_name == "room-rs-invalid"
    assert dispatch_context.voice_mode == "pipeline"


def test_livekit_worker_entrypoint_refreshes_audio_output_bindings_after_start(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_LIVEKIT_PIPELINE_ENABLE_ASSISTANT_TTS", "false")
    async def run() -> None:
        session = _FakeSession()
        worker = _RecordingWorker(session)
        app = RuhuLiveKitAgentServerApp(worker=worker, sdk_loader=lambda: None)
        ctx = SimpleNamespace(
            room=SimpleNamespace(name="room-rs-widget-1"),
            participant=None,
            job=SimpleNamespace(
                metadata=json.dumps(
                    {
                        "conversation_id": "conv-widget-1",
                        "realtime_session_id": "rs-widget-1",
                        "agent_id": "sales_agent",
                        "channel": "web_widget",
                        "room_name": "room-rs-widget-1",
                        "voice_mode": "pipeline",
                        "participant_identity": "visitor-1",
                        "provider_session_id": "room-rs-widget-1",
                        "metadata": {"tenant": "smoke"},
                    }
                )
            ),
        )

        returned = await app._rtc_session_entrypoint(ctx)
        assert returned is session
        assert session.started_kwargs is not None
        assert session.started_kwargs["room"] == ctx.room
        assert isinstance(session.output.audio, _FakeAudioOutput)

        session.emit("agent_state_changed", SimpleNamespace(old_state="initializing", new_state="speaking"))
        await _drain()
        session.output.audio.emit("playback_started", SimpleNamespace(created_at=1.0))
        await _drain()
        session.output.audio.emit(
            "playback_finished",
            SimpleNamespace(playback_position=1.25, interrupted=False),
        )
        await _drain()

        assert [call["signal"] for call in worker.signal_calls] == [
            "assistant_speaking_started",
            "assistant_speaking_stopped",
        ]

    asyncio.run(run())


def test_livekit_worker_routes_gemini3_vertex_requests_to_global() -> None:
    assert _resolve_vertex_llm_location(
        model="gemini-3-flash-preview",
        location="us-central1",
    ) == "global"
    assert _resolve_vertex_llm_location(
        model="gemini-3.0-pro-preview",
        location="europe-west2",
    ) == "global"
    assert _resolve_vertex_llm_location(
        model="gemini-2.5-flash",
        location="us-central1",
    ) == "us-central1"


def test_livekit_worker_pipeline_mode_skips_provider_llm(monkeypatch) -> None:
    llm_call_count = 0
    monkeypatch.setenv("RUHU_LIVEKIT_PIPELINE_ENABLE_ASSISTANT_TTS", "true")

    class _FakeGooglePlugin:
        class tts:
            class texttospeech:
                class AudioEncoding:
                    OGG_OPUS = "OGG_OPUS"

        @staticmethod
        def LLM(**_kwargs: Any) -> object:
            nonlocal llm_call_count
            llm_call_count += 1
            return object()

        @staticmethod
        def TTS(**kwargs: Any) -> dict[str, Any]:
            return {"kind": "tts", "kwargs": kwargs}

    monkeypatch.setattr(livekit_worker_module, "_build_google_stt", lambda: {"kind": "stt"})
    monkeypatch.setattr(
        livekit_worker_module,
        "_load_livekit_plugin_module",
        lambda module_name: _FakeGooglePlugin if module_name == "livekit.plugins.google" else None,
    )

    worker = _RecordingWorker(_FakeSession())
    app = RuhuLiveKitAgentServerApp(worker=worker, sdk_loader=lambda: None)

    kwargs = app._build_streaming_session_kwargs(
        SimpleNamespace(proc=SimpleNamespace(userdata={})),
        dispatch_context=_dispatch_context(),
    )

    assert kwargs["stt"] == {"kind": "stt"}
    assert "tts" in kwargs
    assert "llm" not in kwargs
    assert llm_call_count == 0


def test_livekit_worker_builds_policy_aware_vad_from_voice_interaction_policy(monkeypatch) -> None:
    captured: dict[str, float] = {}

    class _FakeSilero:
        @staticmethod
        def load(**kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {"kind": "vad", "kwargs": kwargs}

    monkeypatch.setattr(
        livekit_worker_module,
        "_load_livekit_plugin_attr",
        lambda module_name, attr_name: _FakeSilero
        if module_name == "livekit.plugins.silero" and attr_name == "VAD"
        else None,
    )
    monkeypatch.setattr(livekit_worker_module, "_build_google_stt", lambda: None)
    monkeypatch.setattr(
        livekit_worker_module,
        "_load_livekit_plugin_module",
        lambda _module_name: None,
    )

    worker = _RecordingWorker(_FakeSession())
    app = RuhuLiveKitAgentServerApp(worker=worker, sdk_loader=lambda: None)
    dispatch_context = _dispatch_context()
    dispatch_context = LiveKitWorkerDispatchContext(
        conversation_id=dispatch_context.conversation_id,
        realtime_session_id=dispatch_context.realtime_session_id,
        agent_id=dispatch_context.agent_id,
        agent_version_id=dispatch_context.agent_version_id,
        channel=dispatch_context.channel,
        room_name=dispatch_context.room_name,
        voice_mode=dispatch_context.voice_mode,
        participant_identity=dispatch_context.participant_identity,
        provider_session_id=dispatch_context.provider_session_id,
        metadata={
            **dispatch_context.metadata,
            "voice_interaction_policy": {
                "endpointing_ms": 900,
                "soft_timeout_ms": 750,
                "turn_eagerness": "low",
                "interruptibility_policy": "interruptible_except_policy",
            },
        },
    )

    kwargs = app._build_streaming_session_kwargs(
        SimpleNamespace(proc=SimpleNamespace(userdata={"vad": "prewarmed-vad"})),
        dispatch_context=dispatch_context,
    )

    assert kwargs["vad"]["kind"] == "vad"
    assert kwargs["vad"] != "prewarmed-vad"
    assert round(captured["min_silence_duration"], 2) == 1.08


def test_livekit_worker_entrypoint_marks_session_errored_on_start_timeout(monkeypatch) -> None:
    async def run() -> None:
        monkeypatch.setenv("RUHU_LIVEKIT_SESSION_START_TIMEOUT_SECONDS", "1")
        session = _FakeHangingSession()
        worker = _RecordingWorker(session)
        app = RuhuLiveKitAgentServerApp(worker=worker, sdk_loader=lambda: None)
        ctx = SimpleNamespace(
            room=SimpleNamespace(name="room-rs-widget-1"),
            participant=None,
            job=SimpleNamespace(
                metadata=json.dumps(
                    {
                        "conversation_id": "conv-widget-1",
                        "realtime_session_id": "rs-widget-1",
                        "agent_id": "sales_agent",
                        "channel": "web_widget",
                        "room_name": "room-rs-widget-1",
                        "voice_mode": "pipeline",
                        "participant_identity": "visitor-1",
                        "provider_session_id": "room-rs-widget-1",
                        "metadata": {"tenant": "smoke"},
                    }
                )
            ),
        )

        try:
            await app._rtc_session_entrypoint(ctx)
        except TimeoutError:
            pass
        else:
            raise AssertionError("expected session start timeout")

        assert worker.errored_calls == [
            {
                "realtime_session_id": "rs-widget-1",
                "reason": "agent_session_start_timeout",
                "metadata": {
                    "voice_mode": "pipeline",
                    "room_name": "room-rs-widget-1",
                    "timeout_seconds": 1.0,
                },
            }
        ]

    asyncio.run(run())


def test_livekit_worker_uses_concrete_session_events_and_sync_callbacks(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_LIVEKIT_PIPELINE_ENABLE_ASSISTANT_TTS", "false")
    async def run() -> None:
        session = _FakeSession()
        session.output.audio = _FakeAudioOutput()
        worker = _RecordingWorker(session)
        app = RuhuLiveKitAgentServerApp(worker=worker, sdk_loader=lambda: None)

        refresh_audio_output_bindings = app._bind_session_events(
            session,
            dispatch_context=_dispatch_context(),
        )
        refresh_audio_output_bindings()

        session.emit("agent_state_changed", SimpleNamespace(old_state="initializing", new_state="speaking"))
        await _drain()
        session.output.audio.emit("playback_started", SimpleNamespace(created_at=1.0))
        await _drain()
        session.emit(
            "overlapping_speech",
            SimpleNamespace(
                is_interruption=True,
                probability=0.91,
                detected_at=1.1,
            ),
        )
        await _drain()
        session.emit("agent_false_interruption", SimpleNamespace(resumed=True))
        await _drain()
        session.emit("agent_state_changed", SimpleNamespace(old_state="speaking", new_state="listening"))
        await _drain()
        session.emit(
            "user_input_transcribed",
            SimpleNamespace(
                transcript="Tell me about pricing.",
                is_final=True,
                segment_id="seg-1",
            ),
        )
        await _drain()
        session.emit("error", SimpleNamespace(message="tts failed"))
        await _drain()
        session.emit("close", SimpleNamespace(reason="participant_disconnected"))
        await _drain()

        assert [call["signal"] for call in worker.signal_calls] == [
            "assistant_speaking_started",
            "user_barged_in",
            "assistant_resumed",
            "assistant_interrupted",
        ]
        assert worker.signal_calls[0]["metadata"]["created_at"] == 1.0
        assert worker.signal_calls[1]["metadata"]["is_interruption"] is True
        assert worker.signal_calls[2]["reason"] == "agent_false_interruption_resumed"
        assert worker.final_transcript_calls == [
            {
                "realtime_session_id": "rs-widget-1",
                "text": "Tell me about pricing.",
                "idempotency_key": "seg-1",
                "participant_identity": "visitor-1",
                "provider_session_id": "room-rs-widget-1",
                "metadata": {"voice_mode": "pipeline", "tenant": "smoke"},
            }
        ]
        assert worker.errored_calls == []
        assert worker.ended_calls[0]["reason"] == "livekit_session_closed"

    asyncio.run(run())


def test_livekit_worker_does_not_emit_user_barge_in_when_voice_policy_is_non_interruptible(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_LIVEKIT_PIPELINE_ENABLE_ASSISTANT_TTS", "false")

    async def run() -> None:
        session = _FakeSession()
        session.output.audio = _FakeAudioOutput()
        worker = _RecordingWorker(session)
        app = RuhuLiveKitAgentServerApp(worker=worker, sdk_loader=lambda: None)
        dispatch_context = _dispatch_context()
        dispatch_context = LiveKitWorkerDispatchContext(
            conversation_id=dispatch_context.conversation_id,
            realtime_session_id=dispatch_context.realtime_session_id,
            agent_id=dispatch_context.agent_id,
            agent_version_id=dispatch_context.agent_version_id,
            channel=dispatch_context.channel,
            room_name=dispatch_context.room_name,
            voice_mode=dispatch_context.voice_mode,
            participant_identity=dispatch_context.participant_identity,
            provider_session_id=dispatch_context.provider_session_id,
            metadata={
                **dispatch_context.metadata,
                "voice_interaction_policy": {
                    "interruptibility_policy": "non_interruptible",
                    "endpointing_ms": 650,
                    "turn_eagerness": "normal",
                },
            },
        )

        refresh_audio_output_bindings = app._bind_session_events(
            session,
            dispatch_context=dispatch_context,
        )
        refresh_audio_output_bindings()

        session.emit("agent_state_changed", SimpleNamespace(old_state="initializing", new_state="speaking"))
        await _drain()
        session.output.audio.emit("playback_started", SimpleNamespace(created_at=1.0))
        await _drain()
        session.emit(
            "overlapping_speech",
            SimpleNamespace(
                is_interruption=True,
                probability=0.91,
                detected_at=1.1,
            ),
        )
        await _drain()

        assert [call["signal"] for call in worker.signal_calls] == [
            "assistant_speaking_started",
        ]

    asyncio.run(run())


def test_livekit_worker_final_transcript_fallback_idempotency_key_is_stable() -> None:
    worker = _RecordingWorker(_FakeSession())
    app = RuhuLiveKitAgentServerApp(worker=worker, sdk_loader=lambda: None)
    event = SimpleNamespace(idempotency_key=None, segment_id=None, event_id=None)

    first = app._event_idempotency_key(
        event,
        dispatch_context=_dispatch_context(),
        text="Tell   me  about pricing.",
    )
    second = app._event_idempotency_key(
        event,
        dispatch_context=_dispatch_context(),
        text="Tell me about pricing.",
    )

    assert first == second
    assert first.startswith("rs-widget-1:final:")


def test_livekit_worker_entrypoint_pickles_without_server_state() -> None:
    session = _FakeSession()
    worker = _RecordingWorker(session)
    app = RuhuLiveKitAgentServerApp(worker=worker, sdk_loader=lambda: None)
    app._server = SimpleNamespace(non_picklable=lambda value: value)
    serialized = pickle.dumps(app._rtc_session_entrypoint)
    restored_entrypoint = pickle.loads(serialized)
    assert callable(restored_entrypoint)


def test_livekit_worker_replays_incremental_assistant_outputs_after_final_transcript(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_LIVEKIT_PIPELINE_ENABLE_ASSISTANT_TTS", "false")
    async def run() -> None:
        session = _FakeSession()
        worker = _RecordingWorker(session)
        worker.replay_outputs_by_after_sequence = {
            0: [
                {
                    "delivery_id": "evt-2",
                    "conversation_sequence": 2,
                    "text": "First response",
                }
            ],
            2: [
                {
                    "delivery_id": "evt-5",
                    "conversation_sequence": 5,
                    "text": "Replacement response",
                }
            ],
        }
        app = RuhuLiveKitAgentServerApp(worker=worker, sdk_loader=lambda: None)
        refresh_audio_output_bindings = app._bind_session_events(
            session,
            dispatch_context=_dispatch_context(),
        )
        refresh_audio_output_bindings()
        await _drain()

        session.emit(
            "user_input_transcribed",
            SimpleNamespace(
                transcript="Tell me about pricing.",
                is_final=True,
                segment_id="seg-1",
            ),
        )
        await _drain()

        session.emit(
            "user_input_transcribed",
            SimpleNamespace(
                transcript="What about enterprise?",
                is_final=True,
                segment_id="seg-2",
            ),
        )
        await _drain()

        assert [call["after_sequence"] for call in worker.replay_calls] == [0, 2, 5]
        assert [call["stage"] for call in worker.assistant_output_ack_calls] == ["resolved", "resolved"]
        assert session.said_texts == []

    asyncio.run(run())


def test_livekit_worker_enables_pipeline_assistant_tts_when_explicitly_requested(monkeypatch) -> None:
    async def run() -> None:
        monkeypatch.setenv("RUHU_LIVEKIT_PIPELINE_ENABLE_ASSISTANT_TTS", "true")
        session = _FakeSession()
        worker = _RecordingWorker(session)
        worker.replay_outputs_by_after_sequence = {
            0: [
                {
                    "delivery_id": "evt-2",
                    "conversation_sequence": 2,
                    "text": "First response",
                }
            ],
        }
        app = RuhuLiveKitAgentServerApp(worker=worker, sdk_loader=lambda: None)
        refresh_audio_output_bindings = app._bind_session_events(
            session,
            dispatch_context=_dispatch_context(),
        )
        refresh_audio_output_bindings()
        await _drain()

        session.emit(
            "user_input_transcribed",
            SimpleNamespace(
                transcript="Tell me about pricing.",
                is_final=True,
                segment_id="seg-1",
            ),
        )
        await _drain()

        collected = await session.collect_said_texts()
        assert collected == ["First response"]
        assert [call["stage"] for call in worker.assistant_output_ack_calls] == [
            "resolved",
            "started",
            "completed",
        ]
        assert [call["signal"] for call in worker.signal_calls] == [
            "assistant_speaking_started",
            "assistant_speaking_stopped",
        ]
        assert worker.signal_calls[0]["metadata"]["delivery_id"] == "evt-2"
        assert worker.signal_calls[0]["metadata"]["conversation_sequence"] == 2
        assert worker.signal_calls[1]["metadata"]["delivery_id"] == "evt-2"
        assert worker.signal_calls[1]["metadata"]["conversation_sequence"] == 2

    asyncio.run(run())


def test_livekit_worker_chunk_assistant_text_function() -> None:
    text = "Hello. This is a long response designed to exceed the configured TTS chunk size so we can test splitting."
    chunks = livekit_worker_module._chunk_assistant_text(text, max_chars=35)
    assert chunks[0].startswith("Hello.")
    assert chunks[-1].endswith("splitting.")
    assert all(len(chunk) <= 35 for chunk in chunks)
    assert len(chunks) >= 2


def test_livekit_worker_speak_text_chunks_preserves_single_delivery_lifecycle(monkeypatch) -> None:
    async def run() -> None:
        monkeypatch.setenv("VOICE_TTS_MAX_CHUNK_CHARS", "20")
        session = _FakeSession()
        worker = _RecordingWorker(session)
        app = RuhuLiveKitAgentServerApp(worker=worker, sdk_loader=lambda: None)

        await app._speak_text_chunks(
            session,
            "A very long assistant response that should be split and spoken in pieces.",
            realtime_session_id="rs-widget-1",
            delivery_id="evt-voice-chunks",
            conversation_sequence=7,
            trace_id="trace-1",
            turn_id="turn-1",
        )

        # With streaming refactor, a single say() call is made with an async iterable.
        collected = await session.collect_said_texts()
        assert len(collected) == 1
        assert "very long assistant response" in collected[0]
        assert [call["stage"] for call in worker.assistant_output_ack_calls] == [
            "started",
            "completed",
        ]
        assert worker.assistant_output_ack_calls[0]["delivery_id"] == "evt-voice-chunks"
        assert worker.assistant_output_ack_calls[1]["delivery_id"] == "evt-voice-chunks"

    asyncio.run(run())


def test_livekit_worker_bridges_lk_chat_and_lk_attachment_streams() -> None:
    async def run() -> None:
        session = _FakeSession()
        room = _FakeRoom("room-rs-widget-1")
        worker = _RecordingWorker(session)
        app = RuhuLiveKitAgentServerApp(worker=worker, sdk_loader=lambda: None)
        refresh_audio_output_bindings = app._bind_session_events(
            session,
            room=room,
            dispatch_context=_dispatch_context(),
        )
        refresh_audio_output_bindings()
        await _drain()

        room.emit_byte_stream(
            "lk.attachment",
            _FakeByteStreamReader(
                [b"abc"],
                attributes={"attachment_id": "att-1"},
            ),
            "visitor-1",
        )
        await _drain()

        room.emit_text_stream(
            "lk.chat",
            _FakeTextStreamReader(
                "hello from in-call chat",
                attributes={"attachment_ids": json.dumps(["att-2", "att-1"])},
            ),
            "visitor-1",
        )
        await _drain()

        assert worker.text_message_calls == [
            {
                "realtime_session_id": "rs-widget-1",
                "text": "hello from in-call chat",
                "participant_identity": "visitor-1",
                "provider_session_id": "room-rs-widget-1",
                "attachment_ids": ["att-1", "att-2"],
                "metadata": {"voice_mode": "pipeline", "tenant": "smoke"},
            }
        ]
        assert worker.signal_calls[-1]["signal"] == "attachment_received"

    asyncio.run(run())


def test_livekit_worker_build_room_options_enables_text_input_when_supported() -> None:
    captured: dict[str, Any] = {}

    class FakeRoomOptions:
        def __init__(
            self,
            *,
            text_input: Any = None,
            audio_input: Any = True,
            audio_output: bool = True,
            text_output: bool = True,
            close_on_disconnect: bool = True,
            delete_room_on_close: bool = False,
        ) -> None:
            captured.update(
                {
                    "text_input": text_input,
                    "audio_input": audio_input,
                    "audio_output": audio_output,
                    "text_output": text_output,
                    "close_on_disconnect": close_on_disconnect,
                    "delete_room_on_close": delete_room_on_close,
                }
            )

    class FakeAudioInputOptions:
        def __init__(self, **kwargs: Any) -> None:
            captured["audio_input_options_kwargs"] = kwargs

    class FakeRoomIO:
        RoomOptions = FakeRoomOptions
        AudioInputOptions = FakeAudioInputOptions

    worker = _RecordingWorker(_FakeSession())
    app = RuhuLiveKitAgentServerApp(worker=worker, sdk_loader=lambda: None)
    previous_loader = livekit_worker_module._load_livekit_room_io
    try:
        livekit_worker_module._load_livekit_room_io = lambda: FakeRoomIO
        app._build_room_options()
    finally:
        livekit_worker_module._load_livekit_room_io = previous_loader

    assert captured["audio_output"] is True
    assert captured["text_input"] is True
    assert captured["close_on_disconnect"] is True
    assert captured["delete_room_on_close"] is False


def test_livekit_worker_bind_session_events_replaces_existing_lk_chat_handler() -> None:
    async def run() -> None:
        session = _FakeSession()
        room = _FakeRoom("room-rs-widget-1", fail_on_duplicate_text_handler=True)

        room.register_text_stream_handler(
            "lk.chat",
            lambda _reader, _participant_identity: None,
        )

        worker = _RecordingWorker(session)
        app = RuhuLiveKitAgentServerApp(worker=worker, sdk_loader=lambda: None)
        app._bind_session_events(
            session,
            room=room,
            dispatch_context=_dispatch_context(),
        )
        await _drain()

        room.emit_text_stream(
            "lk.chat",
            _FakeTextStreamReader("hello from already-bound chat"),
            "visitor-1",
        )
        await _drain()

        assert room.unregister_calls["text"] == ["lk.chat"]
        assert worker.text_message_calls == [
            {
                "realtime_session_id": "rs-widget-1",
                "text": "hello from already-bound chat",
                "participant_identity": "visitor-1",
                "provider_session_id": "room-rs-widget-1",
                "attachment_ids": None,
                "metadata": {"voice_mode": "pipeline", "tenant": "smoke"},
            }
        ]
        assert session.said_texts == []

    asyncio.run(run())


def test_livekit_worker_bind_session_events_replaces_existing_lk_attachment_handler() -> None:
    async def run() -> None:
        session = _FakeSession()
        room = _FakeRoom("room-rs-widget-1")
        room._byte_handlers["lk.attachment"] = lambda *_args, **_kwargs: None
        worker = _RecordingWorker(session)
        app = RuhuLiveKitAgentServerApp(worker=worker, sdk_loader=lambda: None)
        app._bind_session_events(
            session,
            room=room,
            dispatch_context=_dispatch_context(),
        )
        await _drain()

        room.emit_byte_stream(
            "lk.attachment",
            _FakeByteStreamReader([b"abc"]),
            "visitor-1",
        )
        await _drain()

        assert room.unregister_calls["byte"] == ["lk.attachment", "widget-images"]
        assert worker.signal_calls[-1]["signal"] == "attachment_received"

    asyncio.run(run())


def test_livekit_worker_run_prefers_worker_options_runtime_when_available() -> None:
    session = _FakeSession()
    worker = _RecordingWorker(session)
    captured: dict[str, object] = {}

    class FakeWorkerOptions:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class FakeCLI:
        @staticmethod
        def run_app(server: object) -> str:
            captured["server"] = server
            return "worker-options-ok"

    fake_sdk = SimpleNamespace(WorkerOptions=FakeWorkerOptions, cli=FakeCLI())
    app = RuhuLiveKitAgentServerApp(worker=worker, sdk_loader=lambda: fake_sdk)

    result = app.run(
        runtime_mode="worker_options",
        host="127.0.0.1",
        port=7788,
        log_level="error",
    )

    assert result == "worker-options-ok"
    options = captured["server"]
    assert isinstance(options, FakeWorkerOptions)
    assert options.kwargs["entrypoint_fnc"] == app._rtc_session_entrypoint
    assert callable(options.kwargs["prewarm_fnc"])
    assert options.kwargs["agent_name"] == "ruhu-voice"
    assert options.kwargs["ws_url"] == "ws://localhost:7880"
    assert options.kwargs["host"] == "127.0.0.1"
    assert options.kwargs["port"] == 7788


def test_livekit_worker_run_falls_back_to_agent_server_runtime_in_auto_mode() -> None:
    session = _FakeSession()
    worker = _RecordingWorker(session)
    registered: dict[str, object] = {}

    class FakeServer:
        def rtc_session(self, *, agent_name: str):
            def decorator(fn):
                registered["agent_name"] = agent_name
                registered["entrypoint"] = fn
                return fn

            return decorator

        def run(self, **_kwargs: Any) -> str:
            return "agent-server-ok"

    fake_sdk = SimpleNamespace(AgentServer=FakeServer)
    app = RuhuLiveKitAgentServerApp(worker=worker, sdk_loader=lambda: fake_sdk)

    result = app.run(runtime_mode="auto")

    assert result == "agent-server-ok"
    assert registered["agent_name"] == "ruhu-voice"
