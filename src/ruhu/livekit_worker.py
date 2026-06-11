from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import inspect
import json
import logging
import re
import os
import importlib.metadata
import sys
from pathlib import Path
from types import SimpleNamespace
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .livekit_adapter import (
    LiveKitAdapterConfig,
    LiveKitAgentsUnavailableError,
    LiveKitControlPlaneClient,
    LiveKitWorkerDispatchContext,
    RuhuLiveKitAgentWorker,
    load_livekit_agents_sdk,
)
from .runtime_config import RuntimeSettings
from .env_files import load_env_file


logger = logging.getLogger(__name__)


def _load_runtime_env_files() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    candidate_paths = [
        repo_root / ".env.development.local",
        repo_root / ".env.local",
        repo_root / ".env.development",
        repo_root / ".env",
    ]
    env_file_override = os.getenv("RUHU_DEV_ENV_FILE")
    if env_file_override:
        candidate_paths.append(Path(env_file_override).expanduser())
    for env_path in candidate_paths:
        if env_path.exists():
            load_env_file(env_path, override=False)


def _probe_google_credentials() -> None:
    """Emit an early warning if Google Application Default Credentials are missing.

    STT/TTS failures due to missing ADC surface mid-call as confusing auth errors.
    Probing at startup surfaces the problem before the first voice session starts.
    """
    try:
        import google.auth  # type: ignore[import-not-found]
        import google.auth.exceptions  # type: ignore[import-not-found]
    except ImportError:
        # google-auth not installed; Google plugins won't work but that's caught
        # later when the STT/TTS instances are built.
        return
    try:
        credentials, project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        logger.info(
            "livekit worker google adc probe succeeded",
            extra={"project": project or "unknown", "credentials_type": type(credentials).__name__},
        )
    except google.auth.exceptions.DefaultCredentialsError as exc:
        logger.error(
            "livekit worker google adc probe failed — STT/TTS will not work",
            extra={"error": str(exc)},
        )
    except Exception as exc:
        logger.warning(
            "livekit worker google adc probe raised unexpected error",
            extra={"error": str(exc)},
        )


def _log_runtime_versions() -> None:
    package_versions: dict[str, str] = {}
    for package_name in [
        "livekit",
        "livekit-agents",
        "livekit-api",
        "livekit-plugins-google",
        "livekit-plugins-silero",
    ]:
        try:
            package_versions[package_name] = importlib.metadata.version(package_name)
        except Exception:
            package_versions[package_name] = "unavailable"
    logger.info(
        "livekit worker runtime versions",
        extra={
            "python_executable": sys.executable,
            "python_version": sys.version,
            **{f"package.{name}": version for name, version in package_versions.items()},
        },
    )

_VOICE_LISTENER_INSTRUCTIONS = (
    "You are in backend-driven voice mode. Do not generate greetings, fillers, "
    "acknowledgements, confirmations, or autonomous replies. Remain silent and "
    "only support transcription, interruption handling, and room lifecycle."
)

_CHIRP_MODELS = {"chirp_2", "chirp_3", "chirp"}
_STT_MODEL_UPGRADES = {"chirp_2": "chirp_3"}
_CHIRP3_EU_UNSUPPORTED = frozenset(
    {
        "en-NG",
        "en-GH",
        "en-TZ",
        "en-KE",
        "en-ZA",
        "en-ET",
        "sw-KE",
        "sw-TZ",
        "yo-NG",
        "ha-NG",
        "ig-NG",
        "af-ZA",
    }
)


def _chunk_assistant_text(text: str, *, max_chars: int = 900) -> list[str]:
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]

    segments = re.split(r"(?<=[\.\!\?;:])\s+", normalized)
    if len(segments) == 1:
        segments = [normalized]

    chunks: list[str] = []
    current = ""

    def flush_current() -> None:
        nonlocal current
        if not current:
            return
        chunks.append(current)
        current = ""

    def append_piece(piece: str) -> None:
        nonlocal current
        piece = piece.strip()
        if not piece:
            return
        if len(piece) > max_chars:
            words = piece.split()
            if not words:
                chunks.extend(
                    [piece[i : i + max_chars] for i in range(0, len(piece), max_chars)]
                )
                return
            word_buffer: list[str] = []
            line_length = 0
            for word in words:
                add_len = len(word) if not word_buffer else len(word) + 1
                if not word_buffer:
                    if len(word) <= max_chars:
                        word_buffer.append(word)
                        line_length = len(word)
                    else:
                        chunks.extend(
                            [word[i : i + max_chars] for i in range(0, len(word), max_chars)]
                        )
                elif line_length + add_len <= max_chars:
                    word_buffer.append(word)
                    line_length += add_len
                else:
                    flush_current()
                    chunks.append(" ".join(word_buffer))
                    word_buffer = [word]
                    line_length = len(word)
                    if line_length > max_chars:
                        chunks.extend(
                            [word[i : i + max_chars] for i in range(0, len(word), max_chars)]
                        )
                        word_buffer = []
                        line_length = 0
            if word_buffer:
                append_candidate = " ".join(word_buffer)
                if len(append_candidate) > max_chars:
                    chunks.extend(
                        [append_candidate[i : i + max_chars] for i in range(0, len(append_candidate), max_chars)]
                    )
                else:
                    flush_current()
                    current = append_candidate
            return

        if not current:
            current = piece
            return
        if len(current) + 1 + len(piece) <= max_chars:
            current = f"{current} {piece}"
            return
        flush_current()
        current = piece

    for segment in segments:
        if len(segment.strip()) <= max_chars:
            append_piece(segment.strip())
            continue
        append_piece(segment)

    if current:
        chunks.append(current)

    if not chunks:
        return [normalized[:max_chars], *(normalized[i : i + max_chars] for i in range(max_chars, len(normalized), max_chars))]

    return chunks


def _resolve_vertex_llm_location(*, model: str, location: str) -> str:
    normalized_location = str(location or "").strip() or "global"
    lowered_model = str(model or "").strip().lower()
    if lowered_model.startswith("gemini-3") or lowered_model.startswith("gemini-3."):
        return "global"
    return normalized_location


def _print(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
        return
    for key, value in payload.items():
        print(f"{key}={value}")


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _http_error_payload(exc: httpx.HTTPError) -> dict[str, object]:
    payload: dict[str, object] = {"ok": False, "reason": str(exc) or exc.__class__.__name__}
    request = getattr(exc, "request", None)
    if request is not None:
        payload["request_url"] = str(request.url)
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        payload["status_code"] = response.status_code
        payload["reason"] = f"HTTP {response.status_code}: {response.text.strip() or str(exc)}"
        body = response.text.strip()
        if body:
            payload["response_text"] = body
    return payload


def _call_supported(target: Any, /, **kwargs: Any) -> Any:
    if not callable(target):
        raise TypeError("target is not callable")
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        return target(**kwargs)
    accepts_var_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    if accepts_var_kwargs:
        return target(**kwargs)
    supported = {
        name: value
        for name, value in kwargs.items()
        if name in signature.parameters
    }
    return target(**supported)


_SUPPORTED_RUNTIME_MODES = {"auto", "worker_options", "agent_server"}


def _resolve_runtime_mode(raw_value: str | None) -> str:
    normalized = str(raw_value or "auto").strip().lower()
    if normalized in _SUPPORTED_RUNTIME_MODES:
        return normalized
    return "auto"


def _env_truthy(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_positive_int(name: str, *, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    if value < minimum:
        return default
    return value


def _parse_positive_float(name: str, *, default: float, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        return default
    if value < minimum:
        return default
    return value


def _load_livekit_plugin_attr(module_name: str, attr_name: str) -> Any | None:
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return None
    return getattr(module, attr_name, None)


def _load_livekit_plugin_module(module_name: str) -> Any | None:
    try:
        return importlib.import_module(module_name)
    except Exception:
        return None


def _load_livekit_room_io() -> Any | None:
    try:
        from livekit.agents import room_io  # type: ignore[import-not-found]
    except Exception:
        return None
    return room_io


def _normalize_google_chirp_voice_name(*, voice_name: str, language: str) -> str:
    raw_voice_name = str(voice_name or "").strip() or "Kore"
    if raw_voice_name.lower() in {"alloy", "echo", "fable", "onyx", "nova", "shimmer", "ash", "sage"}:
        raw_voice_name = "Kore"
    if "chirp3" in raw_voice_name.lower():
        return raw_voice_name
    return f"{language}-Chirp3-HD-{raw_voice_name}"


def _default_google_stt_location_for_chirp() -> str:
    explicit = str(
        os.getenv("VOICE_STT_LOCATION")
        or os.getenv("STT_LOCATION")
        or ""
    ).strip().lower()
    if explicit:
        return explicit
    vertex_location = str(os.getenv("VERTEX_AI_LOCATION") or "").strip().lower()
    if not vertex_location or vertex_location == "global":
        return "us"
    if vertex_location.startswith("europe-") or vertex_location.startswith("eu-"):
        return "eu"
    if vertex_location.startswith("us-") or vertex_location.startswith("northamerica-"):
        return "us"
    return "us"


def _warn_stt_language_region_compat(*, model: str, location: str, language: str) -> None:
    if model not in {"chirp_3", "chirp_2"}:
        return
    normalized_language = str(language or "").strip().lower()
    if location == "eu" and normalized_language in {item.lower() for item in _CHIRP3_EU_UNSUPPORTED}:
        logger.warning(
            "livekit_worker_stt_language_region_incompatible",
            extra={
                "model": model,
                "location": location,
                "language": language,
            },
        )


def _build_google_stt() -> Any | None:
    google = _load_livekit_plugin_module("livekit.plugins.google")
    if google is None or not hasattr(google, "STT"):
        logger.warning(
            "livekit_worker_google_stt_plugin_missing",
            extra={"plugin_module": "livekit.plugins.google", "attribute": "STT"},
        )
        return None
    raw_model = str(os.getenv("VOICE_STT_MODEL", "chirp_3") or "chirp_3").strip()
    stt_model_name = _STT_MODEL_UPGRADES.get(raw_model, raw_model)
    stt_language = str(os.getenv("VOICE_STT_LANGUAGE", "en-US") or "en-US").strip()
    stt_location = str(
        os.getenv("VOICE_STT_LOCATION")
        or os.getenv("STT_LOCATION")
        or "global"
    ).strip().lower()
    if stt_model_name in _CHIRP_MODELS and stt_location == "global":
        stt_location = _default_google_stt_location_for_chirp()
    elif stt_model_name.startswith("latest_") and stt_location != "global":
        stt_location = "global"
    _warn_stt_language_region_compat(
        model=stt_model_name,
        location=stt_location,
        language=stt_language,
    )
    # detect_language and languages are mutually exclusive in the Google Speech API.
    # The plugin constructor param is 'languages' (plural), accepts str or list.
    detect_language = _env_truthy("VOICE_STT_DETECT_LANGUAGE", default=True)
    stt_kwargs: dict[str, object] = {
        "model": stt_model_name,
        "location": stt_location,
        "interim_results": _env_truthy("VOICE_STT_INTERIM_RESULTS", default=True),
        "punctuate": _env_truthy("VOICE_STT_PUNCTUATE", default=True),
    }
    if detect_language:
        stt_kwargs["detect_language"] = True
    else:
        # 'languages' (plural) is the correct kwarg — plugin converts str → list internally.
        stt_kwargs["languages"] = stt_language
    try:
        stt_instance = _call_supported(google.STT, **stt_kwargs)
        logger.info(
            "livekit_worker_google_stt_initialized",
            extra={
                "model": stt_model_name,
                "detect_language": detect_language,
                "language": stt_language if not detect_language else None,
            },
        )
        return stt_instance
    except Exception:
        logger.exception("livekit_worker_google_stt_init_failed")
        return None


def _build_silero_vad() -> Any | None:
    return _build_silero_vad_with_overrides()


def _voice_policy_min_silence_duration_seconds(
    *,
    endpointing_ms: int | None,
    turn_eagerness: str | None,
) -> float | None:
    if endpointing_ms is None:
        return None
    base_seconds = max(0.2, min(float(endpointing_ms) / 1000.0, 2.0))
    eagerness = str(turn_eagerness or "normal").strip().lower()
    factor = 1.0
    if eagerness == "high":
        factor = 0.85
    elif eagerness == "low":
        factor = 1.2
    return max(0.2, min(base_seconds * factor, 2.0))


def _build_silero_vad_with_overrides(
    *,
    min_silence_duration_override: float | None = None,
) -> Any | None:
    silero = _load_livekit_plugin_attr("livekit.plugins.silero", "VAD")
    if silero is None or not hasattr(silero, "load"):
        logger.warning(
            "livekit_worker_silero_vad_plugin_missing",
            extra={"plugin_module": "livekit.plugins.silero", "attribute": "VAD"},
        )
        return None
    try:
        min_silence_duration = (
            min_silence_duration_override
            if min_silence_duration_override is not None
            else _parse_positive_float(
                "VOICE_VAD_MIN_SILENCE_SECONDS",
                default=0.45,
                minimum=0.05,
            )
        )
        vad_instance = silero.load(
            min_silence_duration=min_silence_duration,
            prefix_padding_duration=_parse_positive_float(
                "VOICE_VAD_PREFIX_PADDING_SECONDS",
                default=0.25,
                minimum=0.0,
            ),
            activation_threshold=_parse_positive_float(
                "VOICE_VAD_ACTIVATION_THRESHOLD",
                default=0.5,
                minimum=0.0,
            ),
        )
        logger.info(
            "livekit_worker_silero_vad_initialized",
            extra={
                "min_silence_duration": min_silence_duration,
                "prefix_padding_duration": _parse_positive_float(
                    "VOICE_VAD_PREFIX_PADDING_SECONDS",
                    default=0.25,
                    minimum=0.0,
                ),
                "activation_threshold": _parse_positive_float(
                    "VOICE_VAD_ACTIVATION_THRESHOLD",
                    default=0.5,
                    minimum=0.0,
                ),
                "policy_override_applied": min_silence_duration_override is not None,
            },
        )
        return vad_instance
    except Exception:
        logger.exception("livekit_worker_silero_vad_init_failed")
        return None


def _pipeline_assistant_tts_enabled(*, voice_mode: str) -> bool:
    return voice_mode != "pipeline" or _env_truthy(
        "RUHU_LIVEKIT_PIPELINE_ENABLE_ASSISTANT_TTS",
        default=False,
    )


def _prewarm_worker_process(proc: Any) -> None:
    userdata = getattr(proc, "userdata", None)
    if not isinstance(userdata, dict):
        userdata = {}
        try:
            setattr(proc, "userdata", userdata)
        except Exception:
            return
    if "vad" not in userdata:
        vad = _build_silero_vad()
        if vad is not None:
            userdata["vad"] = vad


def _prewarmed_resource(ctx: Any, key: str) -> Any | None:
    proc = getattr(ctx, "proc", None)
    userdata = getattr(proc, "userdata", None)
    if not isinstance(userdata, dict):
        return None
    return userdata.get(key)


def build_livekit_worker(
    *,
    control_plane_base_url: str | None = None,
    provider_secret: str | None = None,
    runtime_settings: RuntimeSettings | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> RuhuLiveKitAgentWorker:
    settings = runtime_settings or RuntimeSettings.from_env()
    config = LiveKitAdapterConfig.from_settings(settings)
    if config is None:
        raise LiveKitAgentsUnavailableError("LiveKit adapter config is incomplete")
    resolved_base_url = control_plane_base_url or settings.livekit_control_plane_base_url
    if resolved_base_url is None or not resolved_base_url.strip():
        raise LiveKitAgentsUnavailableError("LiveKit control plane base URL is not configured")
    resolved_provider_secret = provider_secret or settings.provider_shared_secret
    if resolved_provider_secret is None or not resolved_provider_secret.strip():
        raise LiveKitAgentsUnavailableError("LiveKit provider shared secret is not configured")
    bridge = LiveKitControlPlaneClient(
        base_url=resolved_base_url,
        provider_secret=resolved_provider_secret,
        client=http_client,
    )
    return RuhuLiveKitAgentWorker(config=config, control_plane_client=bridge)


class RuhuLiveKitAgentServerApp:
    def __init__(
        self,
        *,
        worker: RuhuLiveKitAgentWorker,
        sdk_loader: Any = load_livekit_agents_sdk,
    ) -> None:
        self.worker = worker
        self.config = worker.config
        self._sdk_loader = sdk_loader
        self._server = None
        self._registered = False

    def __getstate__(self) -> dict[str, object]:
        # LiveKit may spawn worker processes by pickling the rtc-session entrypoint method.
        # Keep only the minimum state needed by the entrypoint to avoid serializing server internals.
        return {"worker": self.worker}

    def __setstate__(self, state: dict[str, object]) -> None:
        worker = state.get("worker")
        if worker is None:
            raise TypeError("invalid worker state for RuhuLiveKitAgentServerApp")
        self.worker = worker
        self.config = worker.config
        self._sdk_loader = load_livekit_agents_sdk
        self._server = None
        self._registered = False

    def build_server(self) -> Any:
        agents_sdk = self._sdk_loader()
        server_cls = getattr(agents_sdk, "AgentServer", None)
        if server_cls is None:
            raise LiveKitAgentsUnavailableError("LiveKit Agents SDK does not expose AgentServer")
        server = server_cls()
        self.register_rtc_session(server)
        self._server = server
        return server

    def register_rtc_session(self, server: Any) -> Any:
        if self._registered:
            return server
        decorator = getattr(server, "rtc_session", None)
        if not callable(decorator):
            raise LiveKitAgentsUnavailableError("LiveKit AgentServer does not expose rtc_session")
        wrapped = decorator(agent_name=self.config.agent_name)(self._rtc_session_entrypoint)
        self._registered = True
        return wrapped

    async def _rtc_session_entrypoint(self, ctx: Any) -> Any:
        dispatch_context = self._dispatch_context_from_ctx(ctx)
        logger.info(
            "livekit worker entrypoint starting",
            extra={
                "conversation_id": dispatch_context.conversation_id,
                "realtime_session_id": dispatch_context.realtime_session_id,
                "room_name": dispatch_context.room_name,
                "voice_mode": dispatch_context.voice_mode,
            },
        )
        logger.info("livekit worker building session kwargs")
        session_kwargs = self._build_streaming_session_kwargs(ctx, dispatch_context=dispatch_context)
        logger.info("livekit worker session kwargs ready", extra={"keys": sorted(session_kwargs.keys())})
        logger.info("livekit worker creating managed agent session")
        session = self.worker.create_managed_agent_session(
            voice_mode=dispatch_context.voice_mode,
            **session_kwargs,
        )
        logger.info("livekit worker session created")
        logger.info("livekit worker building session agent")
        agent = self._build_session_agent(dispatch_context=dispatch_context)
        logger.info("livekit worker agent ready")
        logger.info("livekit worker binding session events")
        refresh_audio_output_bindings = self._bind_session_events(
            session,
            room=getattr(ctx, "room", None),
            dispatch_context=dispatch_context,
        )
        logger.info("livekit worker session events bound")
        # Connect to the room before starting the session.
        # In WorkerOptions mode the room is not joined until ctx.connect() is called.
        connect_fn = getattr(ctx, "connect", None)
        if callable(connect_fn):
            logger.info("livekit worker connecting to room")
            await _maybe_await(connect_fn())
            logger.info("livekit worker connected to room")
        start = getattr(session, "start", None)
        if callable(start):
            logger.info("livekit worker starting agent session")
            audio_output_enabled = "tts" in session_kwargs
            room_options = self._build_room_options(
                audio_output_enabled=audio_output_enabled,
            )
            startup_timeout_seconds = _parse_positive_float(
                "RUHU_LIVEKIT_SESSION_START_TIMEOUT_SECONDS",
                default=20.0,
                minimum=1.0,
            )
            startup_started = asyncio.get_running_loop().time()
            try:
                await asyncio.wait_for(
                    _maybe_await(
                        _call_supported(
                            start,
                            agent=agent,
                            room=getattr(ctx, "room", None),
                            room_options=room_options,
                        )
                    ),
                    timeout=startup_timeout_seconds,
                )
            except TimeoutError as exc:
                logger.error(
                    "livekit worker agent session start timed out",
                    extra={
                        "conversation_id": dispatch_context.conversation_id,
                        "realtime_session_id": dispatch_context.realtime_session_id,
                        "room_name": dispatch_context.room_name,
                        "voice_mode": dispatch_context.voice_mode,
                        "timeout_seconds": startup_timeout_seconds,
                    },
                )
                await self.worker.mark_session_errored(
                    realtime_session_id=dispatch_context.realtime_session_id,
                    reason="agent_session_start_timeout",
                    metadata={
                        "voice_mode": dispatch_context.voice_mode,
                        "room_name": dispatch_context.room_name,
                        "timeout_seconds": startup_timeout_seconds,
                    },
                )
                raise exc
            logger.info("livekit worker agent session started")
            logger.info(
                "livekit worker agent session startup complete",
                extra={
                    "conversation_id": dispatch_context.conversation_id,
                    "realtime_session_id": dispatch_context.realtime_session_id,
                    "room_name": dispatch_context.room_name,
                    "voice_mode": dispatch_context.voice_mode,
                    "startup_duration_ms": int((asyncio.get_running_loop().time() - startup_started) * 1000),
                },
            )
            refresh_audio_output_bindings()
            logger.info("livekit worker audio output bindings refreshed")
            output_controller = getattr(session, "output", None)
            set_audio_enabled = None if output_controller is None else getattr(output_controller, "set_audio_enabled", None)
            if not audio_output_enabled and callable(set_audio_enabled):
                try:
                    await _maybe_await(set_audio_enabled(False))
                    logger.info("livekit worker disabled session audio output without tts")
                except Exception:
                    logger.exception("livekit worker failed to disable audio output without tts")
        return session

    def _build_room_options(self, *, audio_output_enabled: bool = True) -> Any | None:
        room_io = _load_livekit_room_io()
        if room_io is None:
            return None
        audio_input_options = None

        room_options_kwargs: dict[str, Any] = {
            "audio_input": True,
            "audio_output": audio_output_enabled,
            "text_output": True,
            "text_input": True,
            "close_on_disconnect": True,
            "delete_room_on_close": False,
        }

        # Keep SDK text input enabled by default. We now explicitly replace
        # handlers in _bind_session_events, which is safer than disabling this
        # feature at room-option level.
        room_options_cls = getattr(room_io, "RoomOptions", None)
        if room_options_cls is not None:
            try:
                if "text_input" in inspect.signature(room_options_cls).parameters:
                    room_options_kwargs["text_input"] = not _env_truthy(
                        "RUHU_LIVEKIT_DISABLE_DEFAULT_TEXT_INPUT",
                        default=False,
                    )
            except (TypeError, ValueError):
                pass

        # Load the noise cancellation plugin if available. Controlled via
        # RUHU_LIVEKIT_NOISE_CANCELLATION (default: enabled).
        noise_cancellation_instance = None
        if _env_truthy("RUHU_LIVEKIT_NOISE_CANCELLATION", default=True):
            nc_cls = _load_livekit_plugin_attr("livekit.plugins.noise_cancellation", "BVC")
            if nc_cls is not None and callable(nc_cls):
                try:
                    noise_cancellation_instance = nc_cls()
                    logger.info("livekit worker noise cancellation enabled")
                except Exception:
                    logger.warning("livekit worker noise cancellation init failed, skipping")

        audio_input_cls = getattr(room_io, "AudioInputOptions", None)
        if callable(audio_input_cls):
            try:
                audio_input_kwargs: dict[str, Any] = {
                    "sample_rate": _parse_positive_int("VOICE_AUDIO_INPUT_SAMPLE_RATE", default=24000),
                    "num_channels": _parse_positive_int("VOICE_AUDIO_INPUT_CHANNELS", default=1),
                    "frame_size_ms": _parse_positive_int("VOICE_AUDIO_INPUT_FRAME_SIZE_MS", default=50),
                    "pre_connect_audio": _env_truthy("VOICE_AUDIO_PRECONNECT", default=True),
                    "pre_connect_audio_timeout": _parse_positive_float(
                        "VOICE_AUDIO_PRECONNECT_TIMEOUT_SECONDS",
                        default=3.0,
                        minimum=0.0,
                    ),
                }
                if noise_cancellation_instance is not None:
                    audio_input_kwargs["noise_cancellation"] = noise_cancellation_instance
                audio_input_options = _call_supported(audio_input_cls, **audio_input_kwargs)
            except Exception:
                audio_input_options = True

        room_options_cls = getattr(room_io, "RoomOptions", None)
        if not callable(room_options_cls):
            return None
        room_options_kwargs["audio_input"] = (
            audio_input_options if audio_input_options is not None else True
        )
        options = _call_supported(
            room_options_cls,
            **room_options_kwargs,
        )
        logger.info(
            "livekit worker room options built",
            extra={
                "audio_input_enabled": bool(audio_input_options if audio_input_options is not None else True),
                "audio_output_enabled": audio_output_enabled,
                "text_input_enabled": room_options_kwargs.get("text_input", None),
                "text_output_enabled": True,
                "noise_cancellation_enabled": noise_cancellation_instance is not None,
            },
        )
        return options

    def _build_streaming_session_kwargs(
        self,
        ctx: Any,
        *,
        dispatch_context: LiveKitWorkerDispatchContext,
    ) -> dict[str, object]:
        # Voice interaction policy (endpointing_ms, turn_eagerness,
        # interruptibility_policy) is resolved exactly once per dispatch from
        # dispatch_context.metadata and materialised into the VAD + session
        # kwargs below. Policy edits published mid-call do NOT take effect
        # until the next dispatch (i.e. the next LiveKit room join). There is
        # no runtime hook that rebuilds the VAD or re-reads the policy while a
        # session is live — documented limitation, not a bug to retry against.
        kwargs: dict[str, object] = {}
        voice_policy = (
            dispatch_context.metadata.get("voice_interaction_policy")
            if isinstance(dispatch_context.metadata.get("voice_interaction_policy"), dict)
            else {}
        )
        endpointing_ms: int | None = None
        if isinstance(voice_policy, dict):
            raw_endpointing_ms = voice_policy.get("endpointing_ms")
            if isinstance(raw_endpointing_ms, int):
                endpointing_ms = raw_endpointing_ms
            elif isinstance(raw_endpointing_ms, float):
                endpointing_ms = int(raw_endpointing_ms)
        turn_eagerness = (
            str(voice_policy.get("turn_eagerness")).strip().lower()
            if isinstance(voice_policy, dict) and voice_policy.get("turn_eagerness") is not None
            else None
        )
        policy_min_silence_duration = _voice_policy_min_silence_duration_seconds(
            endpointing_ms=endpointing_ms,
            turn_eagerness=turn_eagerness,
        )

        prewarmed_vad = _prewarmed_resource(ctx, "vad")
        if prewarmed_vad is not None and policy_min_silence_duration is None:
            kwargs["vad"] = prewarmed_vad
            logger.info("livekit worker vad prewarmed and enabled")
        elif _env_truthy("RUHU_LIVEKIT_ENABLE_SILERO_VAD", default=True):
            if prewarmed_vad is not None and policy_min_silence_duration is not None:
                logger.info(
                    "livekit worker rebuilding vad for voice interaction policy",
                    extra={
                        "endpointing_ms": endpointing_ms,
                        "turn_eagerness": turn_eagerness,
                        "min_silence_duration": policy_min_silence_duration,
                    },
                )
            vad = _build_silero_vad_with_overrides(
                min_silence_duration_override=policy_min_silence_duration,
            )
            if vad is not None:
                kwargs["vad"] = vad
                logger.info("livekit worker vad enabled at startup")
            else:
                logger.warning("livekit worker vad unavailable")

        pipeline_assistant_tts_enabled = _pipeline_assistant_tts_enabled(
            voice_mode=dispatch_context.voice_mode,
        )
        stt = _build_google_stt()
        if stt is not None:
            kwargs["stt"] = stt
            logger.info(
                "livekit worker stt registered",
                extra={"conversation_id": dispatch_context.conversation_id},
            )
        else:
            logger.warning(
                "livekit worker stt unavailable",
                extra={"conversation_id": dispatch_context.conversation_id},
            )

        google = _load_livekit_plugin_module("livekit.plugins.google")
        if google is None:
            return kwargs

        stt_language = str(os.getenv("VOICE_STT_LANGUAGE", "en-US") or "en-US").strip()

        # Pipeline mode keeps the backend as the reasoning authority. Do not
        # construct a provider-side LLM here; that duplicates backend inference,
        # burns quota, and can degrade turns even when the canonical assistant
        # output has already been resolved by the control plane.
        if dispatch_context.voice_mode == "pipeline":
            logger.info(
                "livekit worker skipping provider llm in pipeline mode",
                extra={
                    "conversation_id": dispatch_context.conversation_id,
                    "realtime_session_id": dispatch_context.realtime_session_id,
                },
            )
        else:
            llm_model = str(
                os.getenv("VOICE_LLM_MODEL")
                or os.getenv("GEMINI_MODEL")
                or os.getenv("RUHU_GEMINI_MODEL")
                or "gemini-3-flash-preview"
            ).strip()
            llm_temperature = _parse_positive_float("VOICE_LLM_TEMPERATURE", default=0.7, minimum=0.0)
            llm_kwargs: dict[str, object] = {
                "model": llm_model,
                "temperature": llm_temperature,
            }
            llm_api_key = (
                os.getenv("RUHU_GEMINI_API_KEY")
                or os.getenv("GOOGLE_API_KEY")
                or os.getenv("GEMINI_API_KEY")
            )
            if llm_api_key:
                llm_kwargs["api_key"] = llm_api_key
            else:
                vertex_project = os.getenv("VERTEX_AI_PROJECT")
                vertex_location = (
                    os.getenv("RUHU_VERTEX_AI_LOCATION")
                    or os.getenv("VERTEX_AI_LOCATION")
                    or "global"
                )
                if vertex_project:
                    llm_kwargs["vertexai"] = True
                    llm_kwargs["project"] = vertex_project
                    llm_kwargs["location"] = _resolve_vertex_llm_location(
                        model=llm_model,
                        location=vertex_location,
                    )
            try:
                kwargs["llm"] = _call_supported(google.LLM, **llm_kwargs)
            except Exception:
                pass

        if pipeline_assistant_tts_enabled:
            raw_voice_id = str(
                dispatch_context.metadata.get("voice_id")
                or os.getenv("VOICE_TTS_VOICE_NAME")
                or os.getenv("VOICE_TTS_VOICE_ID")
                or os.getenv("RUHU_LIVEKIT_VOICE_ID")
                or "Kore"
            ).strip()
            tts_language = str(os.getenv("VOICE_TTS_LANGUAGE", stt_language) or stt_language).strip()
            try:
                # Default to chirp_3 (Google Cloud TTS, works with standard ADC credentials).
                # Set VOICE_TTS_MODEL_NAME=gemini-2.5-flash-tts to use Gemini TTS
                # (requires Gemini API key, not just Google Cloud credentials).
                tts_model_name = str(
                    os.getenv("VOICE_TTS_MODEL_NAME", "chirp_3") or "chirp_3"
                ).strip()
                # Voice name format differs by model family:
                # - Gemini TTS: simple names like "Kore", "Puck", "Charon" (no language prefix)
                # - Chirp3-HD: full format "en-US-Chirp3-HD-Kore"
                if tts_model_name == "chirp_3":
                    tts_voice_name = _normalize_google_chirp_voice_name(
                        voice_name=raw_voice_id,
                        language=tts_language,
                    )
                else:
                    # For Gemini TTS, use the raw voice name directly (just "Kore" etc.).
                    tts_voice_name = str(raw_voice_id or "").strip() or "Kore"
                tts_use_streaming = _env_truthy("VOICE_TTS_USE_STREAMING", default=True)
                tts_kwargs: dict[str, object] = {
                    "model_name": tts_model_name,
                    "voice_name": tts_voice_name,
                    "language": tts_language,
                    "speaking_rate": _parse_positive_float("VOICE_TTS_SPEED", default=1.0, minimum=0.25),
                    "use_streaming": tts_use_streaming,
                }
                # Chirp3-HD voices only support LINEAR16, MP3, OGG_OPUS, MULAW, ALAW.
                # The livekit-plugins-google default (PCM / value 7) is NOT in that list
                # and causes a 400 error. Use OGG_OPUS: it works for both streaming and
                # non-streaming paths and is the most bandwidth-efficient option.
                if tts_model_name in _CHIRP_MODELS:
                    try:
                        from google.cloud import texttospeech as _gcp_tts
                        tts_kwargs["audio_encoding"] = _gcp_tts.AudioEncoding.OGG_OPUS
                    except ImportError:
                        pass
                tts_location = str(
                    os.getenv("VOICE_TTS_LOCATION")
                    or os.getenv("TTS_LOCATION")
                    or "global"
                ).strip()
                if tts_location:
                    tts_kwargs["location"] = tts_location
                kwargs["tts"] = _call_supported(google.TTS, **tts_kwargs)
                logger.info(
                    "livekit worker tts initialized",
                    extra={"model": tts_kwargs.get("model_name"), "language": tts_kwargs.get("language")},
                )
            except Exception:
                logger.exception("livekit_worker_google_tts_init_failed")
        else:
            logger.warning(
                "livekit worker assistant tts disabled",
                extra={"voice_mode": dispatch_context.voice_mode},
            )
        return kwargs

    def _build_session_agent(self, *, dispatch_context: LiveKitWorkerDispatchContext) -> Any:
        agents_sdk = self._sdk_loader()
        agent_cls = getattr(agents_sdk, "Agent", None)
        if dispatch_context.voice_mode == "pipeline":
            instructions = _VOICE_LISTENER_INSTRUCTIONS
        else:
            instructions = str(
                os.getenv("RUHU_LIVEKIT_AGENT_INSTRUCTIONS")
                or os.getenv("VOICE_AGENT_SYSTEM_PROMPT")
                or "You are a concise, helpful voice assistant."
            ).strip()
        if dispatch_context.agent_id:
            instructions = f"{instructions}\nCurrent agent: {dispatch_context.agent_id}."
        if agent_cls is None:
            return SimpleNamespace(instructions=instructions)
        return _call_supported(agent_cls, instructions=instructions)

    def _dispatch_context_from_ctx(self, ctx: Any) -> LiveKitWorkerDispatchContext:
        job = getattr(ctx, "job", None)
        raw_metadata = getattr(job, "metadata", None)
        if raw_metadata in {None, ""}:
            room = getattr(ctx, "room", None)
            room_name = getattr(room, "name", "")
            return LiveKitWorkerDispatchContext(
                conversation_id="",
                realtime_session_id="",
                agent_id="",
                agent_version_id="",
                channel="web_widget",
                room_name=room_name,
                voice_mode=self.config.voice_mode,
                metadata={},
            )
        room = getattr(ctx, "room", None)
        room_name = getattr(room, "name", "")
        try:
            return LiveKitWorkerDispatchContext.from_raw(raw_metadata)
        except Exception as exc:
            logger.warning(
                "livekit worker failed to parse dispatch metadata",
                extra={
                    "room_name": room_name,
                    "error": str(exc),
                    "metadata_type": type(raw_metadata).__name__,
                },
            )
            return LiveKitWorkerDispatchContext(
                conversation_id="",
                realtime_session_id="",
                agent_id="",
                agent_version_id="",
                channel="web_widget",
                room_name=room_name,
                voice_mode=self.config.voice_mode,
                metadata={},
            )

    def _bind_session_events(
        self,
        session: Any,
        *,
        room: Any = None,
        dispatch_context: LiveKitWorkerDispatchContext,
    ) -> Any:
        on = getattr(session, "on", None)
        if not callable(on):
            return lambda: None
        assistant_speaking = False
        assistant_interrupted = False
        agent_state = str(getattr(session, "agent_state", "initializing") or "initializing")
        user_state = str(getattr(session, "user_state", "listening") or "listening")
        conversation_id = dispatch_context.conversation_id
        pipeline_assistant_tts_enabled = _pipeline_assistant_tts_enabled(
            voice_mode=dispatch_context.voice_mode,
        )
        prefer_explicit_delivery_lifecycle = (
            dispatch_context.voice_mode == "pipeline" and pipeline_assistant_tts_enabled
        )
        last_replayed_sequence = 0
        _replay_lock = asyncio.Lock()
        active_delivery_context: dict[str, object] | None = None
        bound_audio_output: Any = None
        bound_audio_callbacks: list[tuple[str, Any]] = []
        pending_tasks: set[asyncio.Task[Any]] = set()
        pending_attachment_ids: list[str] = []

        def schedule(handler: Any, *args: Any) -> None:
            try:
                result = handler(*args)
            except Exception:
                return
            if not inspect.isawaitable(result):
                return
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No running event loop — this should not happen inside the LiveKit
                # agents SDK, which always runs within an asyncio loop.  Rather than
                # creating a new loop (which breaks on Python 3.12+ when called from
                # a thread that already has one), log and drop the event.
                logger.warning(
                    "livekit worker schedule() called outside event loop, dropping coroutine",
                    extra={"handler": getattr(handler, "__name__", repr(handler))},
                )
                return
            task = loop.create_task(result)
            pending_tasks.add(task)

            def _finalize(done: asyncio.Task[Any]) -> None:
                pending_tasks.discard(done)
                try:
                    done.result()
                except Exception as exc:
                    loop.call_exception_handler(
                        {
                            "message": "Unhandled LiveKit worker event callback failed",
                            "exception": exc,
                            "task": done,
                        }
                    )

            task.add_done_callback(_finalize)

        def register_event(emitter: Any, name: str, handler: Any) -> Any | None:
            event_on = getattr(emitter, "on", None)
            if not callable(event_on):
                return None

            def callback(*args: Any) -> None:
                if not args:
                    schedule(handler, None)
                    return
                schedule(handler, *args)

            try:
                event_on(name, callback)
                return callback
            except TypeError:
                try:
                    event_on(name)(callback)
                    return callback
                except Exception:
                    return None
            except Exception:
                return None

        def register_text_stream_handler(
            topic: str,
            handler: Any,
            *,
            fallback_to_data: bool = False,
        ) -> bool:
            room_text_stream_handler = None if room is None else getattr(room, "register_text_stream_handler", None)
            if not callable(room_text_stream_handler):
                if not fallback_to_data:
                    return False
                return _register_room_text_data_fallback(topic, handler)
            room_unregister_text_stream_handler = getattr(
                room,
                "unregister_text_stream_handler",
                None,
            )
            if callable(room_unregister_text_stream_handler):
                try:
                    room_unregister_text_stream_handler(topic)
                except Exception:
                    logger.debug(
                        "livekit worker failed to pre-unregister text stream handler",
                        extra={
                            "topic": topic,
                            "room_name": dispatch_context.room_name,
                        },
                    )
            try:
                room_text_stream_handler(topic, lambda reader, participant_identity: schedule(handler, reader, participant_identity))
                return True
            except ValueError:
                logger.warning(
                    "livekit worker text stream handler already registered",
                    extra={
                        "topic": topic,
                        "room_name": dispatch_context.room_name,
                    },
                )
                if not fallback_to_data:
                    return False
                return _register_room_text_data_fallback(topic, handler)
            except Exception:
                logger.exception(
                    "livekit worker failed to register text stream handler",
                    extra={
                        "topic": topic,
                        "room_name": dispatch_context.room_name,
                    },
                )
                return False

        def register_byte_stream_handler(topic: str, handler: Any) -> bool:
            room_byte_stream_handler = None if room is None else getattr(room, "register_byte_stream_handler", None)
            if not callable(room_byte_stream_handler):
                return False
            room_unregister_byte_stream_handler = getattr(
                room,
                "unregister_byte_stream_handler",
                None,
            )
            if callable(room_unregister_byte_stream_handler):
                try:
                    room_unregister_byte_stream_handler(topic)
                except Exception:
                    logger.debug(
                        "livekit worker failed to pre-unregister byte stream handler",
                        extra={
                            "topic": topic,
                            "room_name": dispatch_context.room_name,
                        },
                    )
            try:
                room_byte_stream_handler(
                    topic,
                    lambda reader, participant_identity: schedule(handler, reader, participant_identity),
                )
                return True
            except ValueError:
                logger.warning(
                    "livekit worker byte stream handler already registered",
                    extra={
                        "topic": topic,
                        "room_name": dispatch_context.room_name,
                    },
                )
                return False
            except Exception:
                logger.exception(
                    "livekit worker failed to register byte stream handler",
                    extra={
                        "topic": topic,
                        "room_name": dispatch_context.room_name,
                    },
                )
                return False

        def _register_room_text_data_fallback(topic: str, handler: Any) -> bool:
            room_data_handler = None if room is None else getattr(room, "on", None)
            if not callable(room_data_handler):
                return False

            def _fallback(room_event: Any) -> None:
                packet_topic = getattr(room_event, "topic", None)
                if packet_topic != topic:
                    return
                data = getattr(room_event, "data", None)
                if data in (None, b""):
                    return
                if not isinstance(data, (bytes, bytearray)):
                    return
                try:
                    text_payload = bytes(data).decode("utf-8")
                except Exception:
                    logger.warning(
                        "livekit worker failed to decode room data packet for topic",
                        extra={"topic": topic, "room_name": dispatch_context.room_name},
                    )
                    return

                async def _read_all() -> str:
                    return text_payload

                fake_reader = SimpleNamespace(
                    read_all=_read_all,
                )
                try:
                    schedule(handler, fake_reader, dispatch_context.participant_identity)
                except Exception:
                    logger.exception(
                        "livekit worker failed to schedule room data fallback handler",
                        extra={"topic": topic, "room_name": dispatch_context.room_name},
                    )

            try:
                room_data_handler("data_received", _fallback)
                return True
            except TypeError:
                try:
                    room_data_handler("data_received")(_fallback)
                    return True
                except Exception:
                    logger.exception(
                        "livekit worker failed to bind room data fallback for text channel",
                        extra={"topic": topic, "room_name": dispatch_context.room_name},
                    )
                    return False
            except Exception:
                logger.exception(
                    "livekit worker failed to bind room data fallback for text channel",
                    extra={"topic": topic, "room_name": dispatch_context.room_name},
                )
                return False

        def clear_audio_output_bindings() -> None:
            nonlocal bound_audio_output, bound_audio_callbacks
            event_off = None if bound_audio_output is None else getattr(bound_audio_output, "off", None)
            if callable(event_off):
                for event_name, callback in bound_audio_callbacks:
                    try:
                        event_off(event_name, callback)
                    except Exception:
                        continue
            bound_audio_output = None
            bound_audio_callbacks = []

        async def emit_voice_signal(
            signal: str,
            *,
            reason: str | None = None,
            event: Any = None,
        ) -> None:
            delivery_metadata = dict(active_delivery_context or {})
            await self.worker.emit_voice_signal(
                realtime_session_id=dispatch_context.realtime_session_id,
                signal=signal,
                reason=reason,
                participant_identity=dispatch_context.participant_identity,
                provider_session_id=dispatch_context.provider_session_id or dispatch_context.room_name or None,
                metadata={
                    "voice_mode": dispatch_context.voice_mode,
                    **delivery_metadata,
                    **dispatch_context.metadata,
                    **self._event_metadata(event),
                },
            )

        async def mark_assistant_speaking(
            event: Any = None,
            *,
            reason: str | None = None,
        ) -> None:
            nonlocal assistant_speaking, assistant_interrupted
            if assistant_speaking:
                return
            assistant_speaking = True
            assistant_interrupted = False
            await emit_voice_signal(
                "assistant_speaking_started",
                reason=reason,
                event=event,
            )

        async def mark_assistant_idle(
            event: Any = None,
            *,
            reason: str | None = None,
        ) -> None:
            nonlocal assistant_speaking, assistant_interrupted
            if not assistant_speaking:
                return
            assistant_speaking = False
            assistant_interrupted = False
            await emit_voice_signal(
                "assistant_speaking_stopped",
                reason=reason,
                event=event,
            )

        async def mark_assistant_interrupted(
            event: Any = None,
            *,
            reason: str | None = None,
        ) -> None:
            nonlocal assistant_speaking, assistant_interrupted
            if assistant_interrupted or (not assistant_speaking and agent_state != "speaking"):
                return
            assistant_speaking = False
            assistant_interrupted = True
            await emit_voice_signal("assistant_interrupted", reason=reason, event=event)

        async def mark_user_barge_in(
            event: Any = None,
            *,
            reason: str | None = None,
        ) -> None:
            nonlocal assistant_speaking, assistant_interrupted
            voice_policy = dispatch_context.metadata.get("voice_interaction_policy")
            if isinstance(voice_policy, dict):
                interruptibility_policy = str(
                    voice_policy.get("interruptibility_policy") or "interruptible_except_policy"
                ).strip()
                if interruptibility_policy == "non_interruptible":
                    return
            if assistant_interrupted or (not assistant_speaking and agent_state != "speaking"):
                return
            assistant_speaking = False
            assistant_interrupted = True
            await emit_voice_signal("user_barged_in", reason=reason, event=event)

        async def replay_and_speak_assistant_outputs(response_payload: Any = None) -> None:
            nonlocal active_delivery_context, last_replayed_sequence
            if not conversation_id:
                return
            async with _replay_lock:
                await _replay_and_speak_assistant_outputs_locked(response_payload)

        async def _replay_and_speak_assistant_outputs_locked(response_payload: Any = None) -> None:
            nonlocal active_delivery_context, last_replayed_sequence
            try:
                outputs = await self.worker.replay_assistant_voice_outputs(
                    conversation_id=conversation_id,
                    after_sequence=last_replayed_sequence,
                )
            except Exception:
                outputs = []
            for output in outputs:
                if not isinstance(output, dict):
                    continue
                text = str(output.get("text") or "").strip()
                if not text:
                    continue
                try:
                    conversation_sequence = int(output.get("conversation_sequence") or 0)
                except (TypeError, ValueError):
                    conversation_sequence = 0
                if conversation_sequence > 0:
                    last_replayed_sequence = max(last_replayed_sequence, conversation_sequence)
                delivery_id = str(output.get("delivery_id") or output.get("event_id") or "").strip()
                active_delivery_context = {
                    "delivery_id": delivery_id or None,
                    "conversation_sequence": conversation_sequence if conversation_sequence > 0 else None,
                    "trace_id": output.get("trace_id") if isinstance(output.get("trace_id"), str) else None,
                    "turn_id": output.get("turn_id") if isinstance(output.get("turn_id"), str) else None,
                }
                try:
                    if delivery_id:
                        logger.info(
                            "livekit worker resolved assistant output",
                            extra={
                                "conversation_id": conversation_id,
                                "realtime_session_id": dispatch_context.realtime_session_id,
                                "delivery_id": delivery_id,
                                "conversation_sequence": conversation_sequence,
                                "trace_id": output.get("trace_id"),
                                "turn_id": output.get("turn_id"),
                            },
                        )
                        await self.worker.acknowledge_assistant_output(
                            realtime_session_id=dispatch_context.realtime_session_id,
                            delivery_id=delivery_id,
                            stage="resolved",
                            idempotency_key=f"{delivery_id}:resolved",
                            metadata={
                                "voice_mode": dispatch_context.voice_mode,
                                "conversation_sequence": conversation_sequence,
                                "trace_id": output.get("trace_id"),
                                "turn_id": output.get("turn_id"),
                            },
                        )
                    if pipeline_assistant_tts_enabled:
                        await self._speak_text_chunks(
                            session,
                            text,
                            realtime_session_id=dispatch_context.realtime_session_id,
                            delivery_id=delivery_id or None,
                            conversation_sequence=conversation_sequence if conversation_sequence > 0 else None,
                            trace_id=None if not isinstance(output.get("trace_id"), str) else str(output.get("trace_id")),
                            turn_id=None if not isinstance(output.get("turn_id"), str) else str(output.get("turn_id")),
                            on_started=(
                                (lambda: emit_voice_signal("assistant_speaking_started"))
                                if prefer_explicit_delivery_lifecycle
                                else None
                            ),
                            on_completed=(
                                (lambda: emit_voice_signal("assistant_speaking_stopped", reason="delivery_completed"))
                                if prefer_explicit_delivery_lifecycle
                                else None
                            ),
                            on_interrupted=(
                                (
                                    lambda reason: emit_voice_signal(
                                        "assistant_interrupted",
                                        reason=reason,
                                    )
                                )
                                if prefer_explicit_delivery_lifecycle
                                else None
                            ),
                        )
                finally:
                    active_delivery_context = None
            if outputs:
                return
            if not isinstance(response_payload, dict):
                return
            for candidate in response_payload.get("speak_texts") or []:
                text = str(candidate or "").strip()
                if text and pipeline_assistant_tts_enabled:
                    active_delivery_context = None
                    await self._speak_text_chunks(
                        session,
                        text,
                        realtime_session_id=dispatch_context.realtime_session_id,
                        delivery_id=None,
                        conversation_sequence=None,
                        trace_id=None,
                        turn_id=None,
                        on_started=None,
                        on_completed=None,
                        on_interrupted=None,
                    )
                    active_delivery_context = None

        def collect_attachment_ids(*, attributes: Any = None, attachments: Any = None) -> list[str]:
            attachment_ids: list[str] = []
            if isinstance(attributes, dict):
                raw_attachment_id = attributes.get("attachment_id")
                if isinstance(raw_attachment_id, str) and raw_attachment_id.strip():
                    attachment_ids.append(raw_attachment_id.strip())
                raw_attachment_ids = attributes.get("attachment_ids")
                if isinstance(raw_attachment_ids, str) and raw_attachment_ids.strip():
                    try:
                        parsed = json.loads(raw_attachment_ids)
                    except json.JSONDecodeError:
                        parsed = [raw_attachment_ids]
                    if isinstance(parsed, list):
                        for item in parsed:
                            if isinstance(item, str) and item.strip():
                                attachment_ids.append(item.strip())
            if isinstance(attachments, list):
                for item in attachments:
                    if isinstance(item, str) and item.strip():
                        attachment_ids.append(item.strip())
            deduped: list[str] = []
            seen: set[str] = set()
            for item in attachment_ids:
                if item in seen:
                    continue
                seen.add(item)
                deduped.append(item)
            return deduped

        _last_partial_text: str = ""
        _last_partial_segment_id: str | None = None

        async def handle_transcript(event: Any) -> None:
            nonlocal conversation_id, _last_partial_text, _last_partial_segment_id
            if (assistant_speaking or agent_state == "speaking") and user_state != "speaking":
                await mark_user_barge_in(
                    event,
                    reason="user_input_transcribed_during_assistant_speech",
                )
            text = self._event_text(event)
            if not text:
                return
            is_final = self._event_is_final(event)
            logger.info(
                "livekit worker transcript observed",
                extra={
                    "conversation_id": conversation_id,
                    "realtime_session_id": dispatch_context.realtime_session_id,
                    "is_final": is_final,
                    "segment_id": getattr(event, "segment_id", None),
                    "event_id": getattr(event, "event_id", None),
                    "text_length": len(text),
                },
            )
            provider_session_id = dispatch_context.provider_session_id or dispatch_context.room_name or None
            segment_id = getattr(event, "segment_id", None)
            if is_final:
                # Reset partial tracking on final transcript.
                _last_partial_text = ""
                _last_partial_segment_id = None
                idempotency_key = self._event_idempotency_key(event, dispatch_context=dispatch_context, text=text)
                response = await self.worker.emit_final_transcript(
                    realtime_session_id=dispatch_context.realtime_session_id,
                    text=text,
                    idempotency_key=idempotency_key,
                    participant_identity=dispatch_context.participant_identity,
                    provider_session_id=provider_session_id,
                    metadata={"voice_mode": dispatch_context.voice_mode, **dispatch_context.metadata},
                )
                if isinstance(response, dict):
                    candidate = response.get("conversation_id")
                    if isinstance(candidate, str) and candidate.strip():
                        conversation_id = candidate.strip()
                await replay_and_speak_assistant_outputs(response)
                return
            # Deduplicate partials: Google STT interims are cumulative, so skip
            # if the new text is identical to or a prefix of the previous partial
            # for the same segment.  This prevents "I'm I'm" stuttering.
            if (
                segment_id == _last_partial_segment_id
                and _last_partial_text
                and (text == _last_partial_text or _last_partial_text.startswith(text))
            ):
                return
            _last_partial_text = text
            _last_partial_segment_id = segment_id if isinstance(segment_id, str) else None
            await self.worker.emit_partial_transcript(
                realtime_session_id=dispatch_context.realtime_session_id,
                text=text,
                participant_identity=dispatch_context.participant_identity,
                provider_session_id=provider_session_id,
                metadata={
                    "voice_mode": dispatch_context.voice_mode,
                    "segment_id": segment_id,
                    **dispatch_context.metadata,
                },
            )

        async def handle_text_stream(reader: Any, participant_identity: str) -> None:
            nonlocal conversation_id, pending_attachment_ids
            try:
                text = str(await _maybe_await(reader.read_all()) or "").strip()
            except Exception:
                logger.exception("livekit worker failed to read lk.chat text stream")
                return
            if not text:
                return
            info = getattr(reader, "info", None)
            text_attachment_ids = collect_attachment_ids(
                attributes=getattr(info, "attributes", None),
                attachments=getattr(info, "attachments", None),
            )
            attachment_ids = pending_attachment_ids + text_attachment_ids
            if attachment_ids:
                deduped: list[str] = []
                seen: set[str] = set()
                for item in attachment_ids:
                    if item in seen:
                        continue
                    seen.add(item)
                    deduped.append(item)
                attachment_ids = deduped
            pending_attachment_ids = []
            provider_session_id = dispatch_context.provider_session_id or dispatch_context.room_name or None
            try:
                response = await self.worker.emit_text_message(
                    realtime_session_id=dispatch_context.realtime_session_id,
                    text=text,
                    participant_identity=participant_identity or dispatch_context.participant_identity,
                    provider_session_id=provider_session_id,
                    attachment_ids=attachment_ids or None,
                    metadata={"voice_mode": dispatch_context.voice_mode, **dispatch_context.metadata},
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 409:
                    logger.warning(
                        "livekit worker dropped lk.chat message after session closed",
                        extra={
                            "conversation_id": conversation_id,
                            "realtime_session_id": dispatch_context.realtime_session_id,
                            "room_name": dispatch_context.room_name,
                        },
                    )
                    return
                raise
            if isinstance(response, dict):
                candidate = response.get("conversation_id")
                if isinstance(candidate, str) and candidate.strip():
                    conversation_id = candidate.strip()
            await replay_and_speak_assistant_outputs(response)

        async def handle_attachment_stream(reader: Any, participant_identity: str) -> None:
            del participant_identity
            nonlocal pending_attachment_ids
            info = getattr(reader, "info", None)
            attachment_ids = collect_attachment_ids(
                attributes=getattr(info, "attributes", None),
                attachments=getattr(info, "attachments", None),
            )
            if attachment_ids:
                pending_attachment_ids = pending_attachment_ids + attachment_ids
            try:
                async for _ in reader:
                    pass
            except Exception:
                logger.exception("livekit worker failed to consume lk.attachment byte stream")
                return
            await emit_voice_signal(
                "attachment_received",
                reason="lk_attachment_ingested",
                event=None,
            )

        async def handle_agent_state_changed(event: Any) -> None:
            nonlocal agent_state
            old_state = str(getattr(event, "old_state", agent_state) or agent_state)
            new_state = str(getattr(event, "new_state", agent_state) or agent_state)
            agent_state = new_state
            refresh_audio_output_bindings()
            if prefer_explicit_delivery_lifecycle:
                return
            if new_state == "speaking":
                if bound_audio_output is None:
                    await mark_assistant_speaking(event, reason="agent_state_speaking")
                return
            if old_state == "speaking" and assistant_speaking:
                await mark_assistant_idle(
                    event,
                    reason=f"agent_state_changed:{old_state}->{new_state}",
                )

        async def handle_user_state_changed(event: Any) -> None:
            nonlocal user_state
            old_state = str(getattr(event, "old_state", user_state) or user_state)
            new_state = str(getattr(event, "new_state", user_state) or user_state)
            user_state = new_state
            if new_state == "speaking" and old_state != "speaking":
                await mark_user_barge_in(event, reason="user_state_changed_to_speaking")

        async def handle_false_interruption(event: Any) -> None:
            nonlocal assistant_interrupted
            if not bool(getattr(event, "resumed", False)):
                return
            assistant_interrupted = False
            await emit_voice_signal(
                "assistant_resumed",
                reason="agent_false_interruption_resumed",
                event=event,
            )

        async def handle_overlapping_speech(event: Any) -> None:
            if not bool(getattr(event, "is_interruption", False)):
                return
            await mark_user_barge_in(event, reason="overlapping_speech_detected")

        async def handle_playback_started(event: Any = None) -> None:
            if prefer_explicit_delivery_lifecycle:
                return
            await mark_assistant_speaking(event, reason="audio_playback_started")

        async def handle_playback_finished(event: Any = None) -> None:
            if prefer_explicit_delivery_lifecycle:
                return
            if bool(getattr(event, "interrupted", False)):
                await mark_assistant_interrupted(event, reason="audio_playback_interrupted")
                return
            await mark_assistant_idle(event, reason="audio_playback_finished")

        async def handle_speech_created(event: Any = None) -> None:
            del event
            refresh_audio_output_bindings()

        async def handle_close(event: Any = None) -> None:
            clear_audio_output_bindings()
            await self.worker.mark_session_ended(
                realtime_session_id=dispatch_context.realtime_session_id,
                reason="livekit_session_closed",
                metadata={
                    "voice_mode": dispatch_context.voice_mode,
                    "provider_session_id": dispatch_context.provider_session_id or dispatch_context.room_name,
                },
            )

        async def handle_error(event: Any = None) -> None:
            reason = self._event_error_reason(event)
            normalized_reason = reason.lower()
            if dispatch_context.voice_mode == "pipeline":
                await emit_voice_signal(
                    "assistant_interrupted",
                    reason="tts_generation_failed",
                    event=event,
                )
                return
            if "no audio frames were pushed" in normalized_reason:
                await emit_voice_signal(
                    "assistant_interrupted",
                    reason="tts_generation_failed",
                    event=event,
                )
                return
            await self.worker.mark_session_errored(
                realtime_session_id=dispatch_context.realtime_session_id,
                reason=reason,
                metadata={
                    "voice_mode": dispatch_context.voice_mode,
                    "provider_session_id": dispatch_context.provider_session_id or dispatch_context.room_name,
                },
            )

        def refresh_audio_output_bindings() -> None:
            nonlocal bound_audio_output, bound_audio_callbacks
            audio_output = getattr(getattr(session, "output", None), "audio", None)
            if audio_output is None:
                clear_audio_output_bindings()
                return
            if audio_output is bound_audio_output:
                return
            clear_audio_output_bindings()
            bound_audio_output = audio_output
            playback_started = register_event(audio_output, "playback_started", handle_playback_started)
            playback_finished = register_event(audio_output, "playback_finished", handle_playback_finished)
            bound_audio_callbacks = [
                (event_name, callback)
                for event_name, callback in (
                    ("playback_started", playback_started),
                    ("playback_finished", playback_finished),
                )
                if callback is not None
            ]

        register_event(session, "agent_state_changed", handle_agent_state_changed)
        register_event(session, "user_state_changed", handle_user_state_changed)
        register_event(session, "user_input_transcribed", handle_transcript)
        register_event(session, "agent_false_interruption", handle_false_interruption)
        register_event(session, "overlapping_speech", handle_overlapping_speech)
        register_event(session, "speech_created", handle_speech_created)
        register_event(session, "close", handle_close)
        register_event(session, "error", handle_error)
        register_text_stream_handler("lk.chat", handle_text_stream, fallback_to_data=True)
        attachment_handler = lambda reader, participant_identity: schedule(handle_attachment_stream, reader, participant_identity)
        register_byte_stream_handler("lk.attachment", attachment_handler)
        register_byte_stream_handler("widget-images", attachment_handler)
        refresh_audio_output_bindings()
        if conversation_id:
            schedule(replay_and_speak_assistant_outputs)
        return refresh_audio_output_bindings

    def _event_text(self, event: Any) -> str:
        for key in ("text", "transcript", "content"):
            value = getattr(event, key, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _event_is_final(self, event: Any) -> bool:
        for key in ("is_final", "final", "committed"):
            value = getattr(event, key, None)
            if isinstance(value, bool):
                return value
        return True

    def _event_error_reason(self, event: Any) -> str:
        if event is None:
            return "livekit_session_error"
        for key in ("message", "error", "reason"):
            value = getattr(event, key, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "livekit_session_error"

    def _event_metadata(self, event: Any) -> dict[str, object]:
        if event is None:
            return {}
        metadata: dict[str, object] = {}
        for key in (
            "state",
            "old_state",
            "new_state",
            "reason",
            "event_id",
            "segment_id",
            "speaker_id",
            "type",
        ):
            value = getattr(event, key, None)
            if isinstance(value, str) and value.strip():
                metadata[key] = value.strip()
        for key in (
            "is_final",
            "interrupted",
            "is_interruption",
            "resumed",
        ):
            value = getattr(event, key, None)
            if isinstance(value, bool):
                metadata[key] = value
        for key in (
            "created_at",
            "detected_at",
            "playback_position",
        ):
            value = getattr(event, key, None)
            if isinstance(value, int | float):
                metadata[key] = value
        return metadata

    def _event_idempotency_key(
        self,
        event: Any,
        *,
        dispatch_context: LiveKitWorkerDispatchContext,
        text: str,
    ) -> str:
        for key in ("idempotency_key", "segment_id", "event_id"):
            value = getattr(event, key, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        normalized_text = " ".join(text.split())
        digest = hashlib.sha256(
            f"{dispatch_context.realtime_session_id}:{normalized_text}".encode("utf-8")
        ).hexdigest()[:16]
        return f"{dispatch_context.realtime_session_id}:final:{digest}"

    async def _speak_text_chunks(
        self,
        session: Any,
        text: str,
        *,
        realtime_session_id: str,
        delivery_id: str | None,
        conversation_sequence: int | None,
        trace_id: str | None,
        turn_id: str | None,
        on_started: Any = None,
        on_completed: Any = None,
        on_interrupted: Any = None,
    ) -> None:
        normalized = " ".join(str(text or "").split())
        if not normalized:
            return

        # Use a single say() call with an async iterable so the SDK creates ONE
        # SpeechHandle.  This avoids duplicate transcription events (each say()
        # generates its own TranscriptionReceived) and prevents truncation caused
        # by barge-in between sequential say() calls dropping remaining chunks.
        # The SDK's internal SentenceTokenizer handles audio-level chunking.
        async def _stream_sentences() -> AsyncIterator[str]:
            segments = re.split(r"(?<=[\.\!\?;:])\s+", normalized)
            for segment in segments:
                stripped = segment.strip()
                if stripped:
                    yield stripped + " "

        await self._speak_text(
            session,
            _stream_sentences(),
            realtime_session_id=realtime_session_id,
            delivery_id=delivery_id,
            conversation_sequence=conversation_sequence,
            trace_id=trace_id,
            turn_id=turn_id,
            on_started=on_started,
            on_completed=on_completed,
            on_interrupted=on_interrupted,
        )

    async def _speak_text(
        self,
        session: Any,
        text: Any,  # str | AsyncIterable[str]
        *,
        realtime_session_id: str,
        delivery_id: str | None,
        conversation_sequence: int | None,
        trace_id: str | None,
        turn_id: str | None,
        on_started: Any = None,
        on_completed: Any = None,
        on_interrupted: Any = None,
    ) -> None:
        say = getattr(session, "say", None)
        if not callable(say):
            return
        try:
            if delivery_id:
                await self.worker.acknowledge_assistant_output(
                    realtime_session_id=realtime_session_id,
                    delivery_id=delivery_id,
                    stage="started",
                    idempotency_key=f"{delivery_id}:started",
                    metadata={
                        "conversation_sequence": conversation_sequence,
                        "trace_id": trace_id,
                        "turn_id": turn_id,
                    },
                )
            if callable(on_started):
                await _maybe_await(on_started())
            speech_handle = _call_supported(
                say,
                text=text,
                allow_interruptions=True,
                add_to_chat_ctx=False,
            )
        except Exception:
            if delivery_id:
                await self.worker.acknowledge_assistant_output(
                    realtime_session_id=realtime_session_id,
                    delivery_id=delivery_id,
                    stage="interrupted",
                    reason="speech_start_failed",
                    idempotency_key=f"{delivery_id}:interrupted",
                    metadata={
                        "conversation_sequence": conversation_sequence,
                        "trace_id": trace_id,
                        "turn_id": turn_id,
                    },
                )
            if callable(on_interrupted):
                await _maybe_await(on_interrupted("speech_start_failed"))
            return
        speech_handle = await _maybe_await(speech_handle)
        wait_for_playout = getattr(speech_handle, "wait_for_playout", None)
        if callable(wait_for_playout):
            try:
                await _maybe_await(wait_for_playout())
            except Exception:
                if delivery_id:
                    await self.worker.acknowledge_assistant_output(
                        realtime_session_id=realtime_session_id,
                        delivery_id=delivery_id,
                        stage="interrupted",
                        reason="speech_playout_interrupted",
                        idempotency_key=f"{delivery_id}:interrupted",
                        metadata={
                            "conversation_sequence": conversation_sequence,
                            "trace_id": trace_id,
                            "turn_id": turn_id,
                        },
                    )
                if callable(on_interrupted):
                    await _maybe_await(on_interrupted("speech_playout_interrupted"))
                return
        if delivery_id:
            await self.worker.acknowledge_assistant_output(
                realtime_session_id=realtime_session_id,
                delivery_id=delivery_id,
                stage="completed",
                idempotency_key=f"{delivery_id}:completed",
                metadata={
                    "conversation_sequence": conversation_sequence,
                    "trace_id": trace_id,
                    "turn_id": turn_id,
                },
            )
        if callable(on_completed):
            await _maybe_await(on_completed())

    def _run_worker_options_runtime(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        log_level: str | None = None,
    ) -> Any | None:
        agents_sdk = self._sdk_loader()
        worker_options_cls = getattr(agents_sdk, "WorkerOptions", None)
        cli = getattr(agents_sdk, "cli", None)
        run_app = None if cli is None else getattr(cli, "run_app", None)
        if worker_options_cls is None or not callable(run_app):
            return None
        worker_options = _call_supported(
            worker_options_cls,
            entrypoint_fnc=self._rtc_session_entrypoint,
            prewarm_fnc=_prewarm_worker_process,
            agent_name=self.config.agent_name,
            num_idle_processes=_parse_positive_int("AGENT_NUM_IDLE_PROCESSES", default=1, minimum=1),
            job_memory_warn_mb=_parse_positive_int("AGENT_JOB_MEMORY_WARN_MB", default=500, minimum=1),
            job_memory_limit_mb=_parse_positive_int("AGENT_JOB_MEMORY_LIMIT_MB", default=1024, minimum=1),
            drain_timeout=_parse_positive_int("AGENT_DRAIN_TIMEOUT_SECONDS", default=1800, minimum=1),
            initialize_process_timeout=_parse_positive_float(
                "AGENT_INITIALIZE_PROCESS_TIMEOUT_SECONDS",
                default=30.0,
                minimum=1.0,
            ),
            shutdown_process_timeout=_parse_positive_float(
                "AGENT_SHUTDOWN_PROCESS_TIMEOUT_SECONDS",
                default=15.0,
                minimum=1.0,
            ),
            ws_url=self.config.server_url,
            api_key=self.config.api_key,
            api_secret=self.config.api_secret,
            host=host or "",
            port=port if port is not None else _parse_positive_int("AGENT_PORT", default=0, minimum=0),
            log_level=log_level or os.getenv("AGENT_LOG_LEVEL") or "INFO",
        )
        # livekit.agents.cli.run_app() launches a Typer app and parses sys.argv.
        # Our wrapper already consumed argparse args (e.g. "serve"), so force a
        # compatible LiveKit subcommand here.
        command = str(os.getenv("RUHU_LIVEKIT_WORKER_CLI_COMMAND", "start") or "start").strip()
        argv_before = list(sys.argv)
        try:
            sys.argv = [argv_before[0], command]
            return _call_supported(run_app, server=worker_options)
        finally:
            sys.argv = argv_before

    def has_worker_options_runtime(self) -> bool:
        agents_sdk = self._sdk_loader()
        if not hasattr(agents_sdk, "WorkerOptions"):
            return False
        cli = getattr(agents_sdk, "cli", None)
        return callable(None if cli is None else getattr(cli, "run_app", None))

    def _run_agent_server_runtime(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        log_level: str | None = None,
    ) -> Any:
        server = self._server or self.build_server()
        run = getattr(server, "run", None)
        if callable(run):
            return _call_supported(run, host=host, port=port, log_level=log_level)
        agents_sdk = self._sdk_loader()
        cli = getattr(agents_sdk, "cli", None)
        if cli is not None and hasattr(cli, "run_app"):
            return _call_supported(cli.run_app, server=server, host=host, port=port, log_level=log_level)
        raise LiveKitAgentsUnavailableError("LiveKit SDK does not expose a supported server runtime entrypoint")

    def run(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        log_level: str | None = None,
        runtime_mode: str | None = None,
    ) -> Any:
        mode = _resolve_runtime_mode(
            runtime_mode
            or os.getenv("RUHU_LIVEKIT_RUNTIME_MODE")
            or os.getenv("RUHU_LIVEKIT_WORKER_RUNTIME_MODE")
        )
        if mode in {"auto", "worker_options"}:
            worker_runtime = self._run_worker_options_runtime(host=host, port=port, log_level=log_level)
            if worker_runtime is not None:
                return worker_runtime
            if mode == "worker_options":
                raise LiveKitAgentsUnavailableError("LiveKit WorkerOptions runtime is not available")
        return self._run_agent_server_runtime(host=host, port=port, log_level=log_level)


def build_livekit_agent_server_app(
    *,
    control_plane_base_url: str | None = None,
    provider_secret: str | None = None,
    runtime_settings: RuntimeSettings | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> RuhuLiveKitAgentServerApp:
    worker = build_livekit_worker(
        control_plane_base_url=control_plane_base_url,
        provider_secret=provider_secret,
        runtime_settings=runtime_settings,
        http_client=http_client,
    )
    return RuhuLiveKitAgentServerApp(worker=worker)


def _run_sdk_status(args: argparse.Namespace) -> int:
    settings = RuntimeSettings.from_env()
    config = LiveKitAdapterConfig.from_settings(settings)
    payload = {
        "configured": config is not None,
        "sdk_version_target": None if config is None else config.sdk_version_target,
        "server_url": None if config is None else config.server_url,
        "agent_name": None if config is None else config.agent_name,
        "voice_mode": None if config is None else config.voice_mode,
        "dispatch_strategy": None if config is None else config.dispatch_strategy,
    }
    try:
        sdk = load_livekit_agents_sdk()
        payload["sdk_available"] = True
        payload["sdk_module"] = getattr(sdk, "__name__", "livekit.agents")
        payload["agent_server_available"] = hasattr(sdk, "AgentServer")
        payload["worker_options_available"] = hasattr(sdk, "WorkerOptions")
    except LiveKitAgentsUnavailableError as exc:
        payload["sdk_available"] = False
        payload["error"] = str(exc)
        _print(payload, as_json=args.json)
        return 1
    _print(payload, as_json=args.json)
    return 0


def _run_server_status(args: argparse.Namespace) -> int:
    settings = RuntimeSettings.from_env()
    runtime_mode = _resolve_runtime_mode(
        getattr(args, "runtime_mode", None)
        or os.getenv("RUHU_LIVEKIT_RUNTIME_MODE")
        or os.getenv("RUHU_LIVEKIT_WORKER_RUNTIME_MODE")
    )
    try:
        app = build_livekit_agent_server_app(
            control_plane_base_url=args.control_plane_base_url,
            provider_secret=args.provider_secret,
            runtime_settings=settings,
        )
        server = None
        if runtime_mode != "worker_options":
            server = app.build_server()
        elif not app.has_worker_options_runtime():
            raise LiveKitAgentsUnavailableError("LiveKit WorkerOptions runtime is not available")
    except LiveKitAgentsUnavailableError as exc:
        payload = {"ready": False, "error": str(exc)}
        _print(payload, as_json=args.json)
        return 1
    payload = {
        "ready": True,
        "agent_name": app.config.agent_name,
        "voice_mode": app.config.voice_mode,
        "dispatch_strategy": app.config.dispatch_strategy,
        "server_class": None if server is None else type(server).__name__,
        "runtime_mode": runtime_mode,
    }
    _print(payload, as_json=args.json)
    return 0


def _configure_livekit_sdk_env(config: LiveKitAdapterConfig) -> None:
    os.environ["LIVEKIT_URL"] = config.server_url
    os.environ["LIVEKIT_API_KEY"] = config.api_key
    os.environ["LIVEKIT_API_SECRET"] = config.api_secret


async def _run_bridge_final_async(args: argparse.Namespace) -> int:
    worker = build_livekit_worker(
        control_plane_base_url=args.control_plane_base_url,
        provider_secret=args.provider_secret,
    )
    try:
        try:
            payload = await worker.emit_final_transcript(
                realtime_session_id=args.realtime_session_id,
                text=args.text,
                idempotency_key=args.idempotency_key,
                participant_identity=args.participant_identity,
                provider_session_id=args.provider_session_id,
            )
        except httpx.HTTPError as exc:
            _print(_http_error_payload(exc), as_json=args.json)
            return 1
        _print(payload, as_json=args.json)
        return 0
    finally:
        await worker.control_plane_client.aclose()


def _run_bridge_final(args: argparse.Namespace) -> int:
    return int(asyncio.run(_run_bridge_final_async(args)))


def _run_serve(args: argparse.Namespace) -> int:
    _log_runtime_versions()
    _probe_google_credentials()
    app = build_livekit_agent_server_app(
        control_plane_base_url=args.control_plane_base_url,
        provider_secret=args.provider_secret,
    )
    _configure_livekit_sdk_env(app.config)
    run_result = app.run(
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        runtime_mode=args.runtime_mode,
    )
    if inspect.isawaitable(run_result):
        asyncio.run(_maybe_await(run_result))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate the Ruhu LiveKit worker bridge.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sdk_status = subparsers.add_parser("sdk-status", help="Validate LiveKit SDK availability and config.")
    sdk_status.add_argument("--json", action="store_true")
    sdk_status.set_defaults(handler=_run_sdk_status)

    server_status = subparsers.add_parser("server-status", help="Validate AgentServer availability and wiring.")
    server_status.add_argument("--control-plane-base-url")
    server_status.add_argument("--provider-secret")
    server_status.add_argument("--runtime-mode", choices=sorted(_SUPPORTED_RUNTIME_MODES))
    server_status.add_argument("--json", action="store_true")
    server_status.set_defaults(handler=_run_server_status)

    serve = subparsers.add_parser("serve", help="Start the LiveKit worker runtime.")
    serve.add_argument("--control-plane-base-url")
    serve.add_argument("--provider-secret")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--log-level")
    serve.add_argument("--runtime-mode", choices=sorted(_SUPPORTED_RUNTIME_MODES))
    serve.set_defaults(handler=_run_serve)

    bridge_final = subparsers.add_parser(
        "bridge-final-transcript",
        help="Post one final transcript to the control plane through the LiveKit worker bridge.",
    )
    bridge_final.add_argument("--control-plane-base-url", required=True)
    bridge_final.add_argument("--provider-secret", required=True)
    bridge_final.add_argument("--realtime-session-id", required=True)
    bridge_final.add_argument("--idempotency-key", required=True)
    bridge_final.add_argument("--text", required=True)
    bridge_final.add_argument("--participant-identity")
    bridge_final.add_argument("--provider-session-id")
    bridge_final.add_argument("--json", action="store_true")
    bridge_final.set_defaults(handler=_run_bridge_final)

    return parser


def main(argv: list[str] | None = None) -> int:
    _load_runtime_env_files()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
