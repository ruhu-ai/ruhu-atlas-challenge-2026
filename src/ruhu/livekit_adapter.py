from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
import hashlib
import hmac
import json
import logging
import re
import time
from types import SimpleNamespace
from typing import Any

import httpx

from .runtime_config import RuntimeSettings

logger = logging.getLogger(__name__)

_FINAL_TRANSCRIPT_POST_ATTEMPTS = 4
_FINAL_TRANSCRIPT_POST_TIMEOUT_SECONDS = 45.0
_TRANSCRIPT_POST_RETRY_DELAYS_SECONDS = (0.25, 0.75, 1.5)
_TRANSIENT_TRANSCRIPT_STATUSES = {408, 429, 500, 502, 503, 504}


# ── HMAC room metadata signing ────────────────────────────────────────────────

def sign_room_metadata(payload: dict, secret: str, *, exp_seconds: int = 300) -> str:
    """Sign room metadata so the worker can verify it was issued by this server.

    Returns a compact JSON envelope ``{"p": payload_with_timestamps, "s": hmac_hex}``.
    The payload is extended with ``iat`` (issued-at) and ``exp`` (expiry) claims.
    """
    ts = int(time.time())
    signed_payload = {**payload, "iat": ts, "exp": ts + exp_seconds}
    body = json.dumps(signed_payload, separators=(",", ":"), sort_keys=True)
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return json.dumps({"p": signed_payload, "s": sig}, separators=(",", ":"))


def verify_room_metadata(signed: str, secret: str) -> dict:
    """Verify a signed room metadata envelope and return the payload.

    Raises ``ValueError`` on signature mismatch or expiry.
    Raises ``json.JSONDecodeError`` / ``KeyError`` on malformed input.
    """
    envelope = json.loads(signed)
    body = json.dumps(envelope["p"], separators=(",", ":"), sort_keys=True)
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(envelope["s"], expected):
        raise ValueError("room metadata: signature mismatch")
    if int(time.time()) > envelope["p"].get("exp", 0):
        raise ValueError("room metadata: expired")
    return envelope["p"]


class LiveKitAgentsUnavailableError(RuntimeError):
    """Raised when the optional LiveKit Agents SDK is not installed."""


def _json_object(value: object | None) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


@dataclass(slots=True, frozen=True)
class LiveKitAdapterConfig:
    server_url: str
    api_key: str
    api_secret: str
    agent_name: str = "ruhu-voice"
    room_prefix: str = "ruhu"
    sdk_version_target: str = "1.5.2"
    voice_mode: str = "pipeline"
    dispatch_strategy: str = "hybrid"
    phone_provider: str = "livekit"
    metadata: dict[str, object] = field(default_factory=dict)
    control_plane_base_url: str | None = None

    @classmethod
    def from_settings(cls, settings: RuntimeSettings) -> LiveKitAdapterConfig | None:
        if not settings.livekit_server_url or not settings.livekit_api_key or not settings.livekit_api_secret:
            return None
        return cls(
            server_url=settings.livekit_server_url,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
            agent_name=settings.livekit_agent_name,
            room_prefix=settings.livekit_room_prefix,
            sdk_version_target=settings.livekit_agents_sdk_version_target,
            voice_mode=settings.livekit_voice_mode,
            dispatch_strategy=settings.livekit_dispatch_strategy,
            phone_provider=settings.livekit_phone_provider,
            metadata=dict(settings.livekit_metadata),
            control_plane_base_url=settings.livekit_control_plane_base_url,
        )


def load_livekit_agents_sdk() -> Any:
    try:
        import livekit.agents as agents_sdk  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - dependency optional in most test runs
        raise LiveKitAgentsUnavailableError(
            "LiveKit Agents SDK is not installed. Install the pinned SDK for the adapter service."
        ) from exc
    return agents_sdk


def load_livekit_api_sdk() -> Any:
    try:
        from livekit import api as livekit_api  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - dependency optional in most test runs
        raise LiveKitAgentsUnavailableError(
            "LiveKit API SDK is not installed. Install the pinned livekit extra for token issuance."
        ) from exc
    return livekit_api


def build_livekit_room_name(*, room_prefix: str, conversation_id: str, realtime_session_id: str) -> str:
    raw_name = f"{room_prefix}-{conversation_id}-{realtime_session_id}"
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw_name).strip("-")
    if not normalized:
        normalized = f"{room_prefix}-{realtime_session_id}"
    if len(normalized) > 128:
        normalized = normalized[-128:]
    return normalized


def build_livekit_participant_identity(
    *,
    channel: str,
    conversation_id: str,
    realtime_session_id: str,
    requested_identity: str | None = None,
) -> str:
    if requested_identity and requested_identity.strip():
        return requested_identity.strip()
    return f"{channel}:{conversation_id}:{realtime_session_id}"


@dataclass(slots=True, frozen=True)
class LiveKitVoiceTransportGrant:
    provider: str
    url: str
    room_name: str
    token: str
    participant_identity: str
    agent_name: str
    sdk_version_target: str
    voice_mode: str
    dispatch_strategy: str
    dispatch: dict[str, object] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "url": self.url,
            "room_name": self.room_name,
            "token": self.token,
            "participant_identity": self.participant_identity,
            "agent_name": self.agent_name,
            "sdk_version_target": self.sdk_version_target,
            "voice_mode": self.voice_mode,
            "dispatch_strategy": self.dispatch_strategy,
            "dispatch": dict(self.dispatch),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True, frozen=True)
class LiveKitDispatchResult:
    strategy: str
    attempted: bool = False
    applied: bool = False
    room_name: str | None = None
    agent_name: str | None = None
    dispatch_id: str | None = None
    error: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "strategy": self.strategy,
            "attempted": self.attempted,
            "applied": self.applied,
        }
        if self.room_name is not None:
            payload["room_name"] = self.room_name
        if self.agent_name is not None:
            payload["agent_name"] = self.agent_name
        if self.dispatch_id is not None:
            payload["dispatch_id"] = self.dispatch_id
        if self.error is not None:
            payload["error"] = self.error
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(slots=True)
class LiveKitTokenIssuer:
    config: LiveKitAdapterConfig
    api_loader: Callable[[], Any] = load_livekit_api_sdk

    def issue_voice_transport(
        self,
        *,
        channel: str,
        conversation_id: str,
        realtime_session_id: str,
        participant_identity: str | None = None,
        participant_name: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> LiveKitVoiceTransportGrant:
        room_name = build_livekit_room_name(
            room_prefix=self.config.room_prefix,
            conversation_id=conversation_id,
            realtime_session_id=realtime_session_id,
        )
        resolved_identity = build_livekit_participant_identity(
            channel=channel,
            conversation_id=conversation_id,
            realtime_session_id=realtime_session_id,
            requested_identity=participant_identity,
        )
        dispatch = {
            "agent_name": self.config.agent_name,
            "conversation_id": conversation_id,
            "realtime_session_id": realtime_session_id,
            "channel": channel,
            "voice_mode": self.config.voice_mode,
        }
        combined_metadata = dict(self.config.metadata)
        combined_metadata.update(dict(metadata or {}))
        combined_metadata.update(dispatch)
        token, dispatch_result = self._issue_token(
            room_name=room_name,
            participant_identity=resolved_identity,
            participant_name=participant_name,
            metadata=combined_metadata,
        )
        return LiveKitVoiceTransportGrant(
            provider="livekit",
            url=self.config.server_url,
            room_name=room_name,
            token=token,
            participant_identity=resolved_identity,
            agent_name=self.config.agent_name,
            sdk_version_target=self.config.sdk_version_target,
            voice_mode=self.config.voice_mode,
            dispatch_strategy=self.config.dispatch_strategy,
            dispatch={
                **dispatch,
                **dispatch_result.as_dict(),
            },
            metadata=combined_metadata,
        )

    def _issue_token(
        self,
        *,
        room_name: str,
        participant_identity: str,
        participant_name: str | None,
        metadata: dict[str, object],
    ) -> tuple[str, LiveKitDispatchResult]:
        livekit_api = self.api_loader()
        access_token = livekit_api.AccessToken(self.config.api_key, self.config.api_secret)
        if hasattr(access_token, "with_identity"):
            access_token = access_token.with_identity(participant_identity)
        if participant_name and hasattr(access_token, "with_name"):
            access_token = access_token.with_name(participant_name)
        if hasattr(access_token, "with_metadata"):
            access_token = access_token.with_metadata(json.dumps(metadata, sort_keys=True))
        # Voice session tokens should be short-lived. Default LiveKit TTL is 6 hours
        # which is far too long; cap at 15 minutes.
        if hasattr(access_token, "with_ttl"):
            access_token = access_token.with_ttl(timedelta(minutes=15))
        grants_cls = getattr(livekit_api, "VideoGrants", None) or getattr(livekit_api, "VideoGrant", None)
        if grants_cls is None:
            raise LiveKitAgentsUnavailableError("LiveKit API SDK does not expose VideoGrants")
        grants = grants_cls(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
        )
        if hasattr(access_token, "with_grants"):
            access_token = access_token.with_grants(grants)
        elif hasattr(access_token, "with_video_grants"):
            access_token = access_token.with_video_grants(grants)
        else:  # pragma: no cover - defensive against unexpected SDK surface
            raise LiveKitAgentsUnavailableError("Unsupported LiveKit API AccessToken grant surface")
        dispatch_result = self._apply_room_config_dispatch(
            livekit_api=livekit_api,
            access_token=access_token,
            room_name=room_name,
            metadata=metadata,
        )
        access_token = dispatch_result[0]
        dispatch_state = dispatch_result[1]
        if hasattr(access_token, "to_jwt"):
            return access_token.to_jwt(), dispatch_state
        if hasattr(access_token, "to_token"):
            return access_token.to_token(), dispatch_state
        raise LiveKitAgentsUnavailableError("Unsupported LiveKit API AccessToken output surface")

    def _apply_room_config_dispatch(
        self,
        *,
        livekit_api: Any,
        access_token: Any,
        room_name: str,
        metadata: dict[str, object],
    ) -> tuple[Any, LiveKitDispatchResult]:
        strategy = self.config.dispatch_strategy
        result = LiveKitDispatchResult(
            strategy=strategy,
            attempted=strategy in {"room_config", "hybrid"},
            applied=False,
            room_name=room_name,
            agent_name=self.config.agent_name,
            metadata={"voice_mode": self.config.voice_mode},
        )
        if strategy not in {"room_config", "hybrid"}:
            return access_token, result
        room_dispatch_cls = getattr(livekit_api, "RoomAgentDispatch", None)
        room_config_cls = (
            getattr(livekit_api, "RoomConfiguration", None)
            or getattr(livekit_api, "RoomConfig", None)
        )
        with_room_config = getattr(access_token, "with_room_config", None)
        if room_dispatch_cls is None or room_config_cls is None or not callable(with_room_config):
            return access_token, LiveKitDispatchResult(
                strategy=strategy,
                attempted=True,
                applied=False,
                room_name=room_name,
                agent_name=self.config.agent_name,
                error="room_config_dispatch_unsupported",
                metadata={"voice_mode": self.config.voice_mode},
            )
        dispatch_metadata = json.dumps(_json_object(metadata), sort_keys=True)
        dispatch_obj = room_dispatch_cls(
            agent_name=self.config.agent_name,
            metadata=dispatch_metadata,
        )
        room_config = None
        for candidate_kwargs in (
            {"agents": [dispatch_obj]},
            {"agent_dispatches": [dispatch_obj]},
        ):
            try:
                room_config = room_config_cls(**candidate_kwargs)
                break
            except TypeError:
                continue
        if room_config is None:
            return access_token, LiveKitDispatchResult(
                strategy=strategy,
                attempted=True,
                applied=False,
                room_name=room_name,
                agent_name=self.config.agent_name,
                error="room_config_constructor_unsupported",
                metadata={"voice_mode": self.config.voice_mode},
            )
        access_token = with_room_config(room_config)
        return access_token, LiveKitDispatchResult(
            strategy=strategy,
            attempted=True,
            applied=True,
            room_name=room_name,
            agent_name=self.config.agent_name,
            metadata={"mechanism": "room_config", "voice_mode": self.config.voice_mode},
        )


@dataclass(slots=True)
class LiveKitDispatchClient:
    config: LiveKitAdapterConfig
    api_loader: Callable[[], Any] = load_livekit_api_sdk
    api_client_factory: Callable[[Any, LiveKitAdapterConfig], Any] | None = None
    _shared_api_client: Any = field(default=None, init=False, repr=False)

    def _get_or_create_api_client(self, livekit_api: Any) -> Any:
        """Return a cached LiveKit API client, creating one on first call."""
        if self.api_client_factory is not None:
            return self.api_client_factory(livekit_api, self.config)
        if self._shared_api_client is not None:
            return self._shared_api_client
        client_cls = getattr(livekit_api, "LiveKitAPI", None)
        if client_cls is None:
            return None
        try:
            client = client_cls(
                self.config.server_url,
                self.config.api_key,
                self.config.api_secret,
            )
        except TypeError:
            client = client_cls(
                url=self.config.server_url,
                api_key=self.config.api_key,
                api_secret=self.config.api_secret,
            )
        self._shared_api_client = client
        return client

    async def close(self) -> None:
        """Close the shared LiveKit API client if one was created."""
        client = self._shared_api_client
        self._shared_api_client = None
        if client is not None:
            if hasattr(client, "aclose"):
                await client.aclose()
            elif hasattr(client, "close"):
                client.close()

    async def create_dispatch(
        self,
        *,
        room_name: str,
        metadata: dict[str, object] | None = None,
        agent_name: str | None = None,
    ) -> LiveKitDispatchResult:
        strategy = self.config.dispatch_strategy
        result = LiveKitDispatchResult(
            strategy=strategy,
            attempted=strategy in {"api_dispatch", "hybrid"},
            applied=False,
            room_name=room_name,
            agent_name=agent_name or self.config.agent_name,
            metadata={"voice_mode": self.config.voice_mode},
        )
        if strategy not in {"api_dispatch", "hybrid"}:
            return result
        livekit_api = self.api_loader()
        request_cls = getattr(livekit_api, "CreateAgentDispatchRequest", None)
        if request_cls is None:
            return LiveKitDispatchResult(
                strategy=strategy,
                attempted=True,
                applied=False,
                room_name=room_name,
                agent_name=agent_name or self.config.agent_name,
                error="create_dispatch_request_unsupported",
                metadata={"voice_mode": self.config.voice_mode},
            )
        client = self._get_or_create_api_client(livekit_api)
        if client is None:
            return LiveKitDispatchResult(
                strategy=strategy,
                attempted=True,
                applied=False,
                room_name=room_name,
                agent_name=agent_name or self.config.agent_name,
                error="livekit_api_client_unavailable",
                metadata={"voice_mode": self.config.voice_mode},
            )
        try:
            request = request_cls(
                room=room_name,
                agent_name=agent_name or self.config.agent_name,
                metadata=json.dumps(_json_object(metadata), sort_keys=True),
            )
            dispatch_api = self._resolve_dispatch_api(client=client, livekit_api=livekit_api)
            if dispatch_api is None or not hasattr(dispatch_api, "create_dispatch"):
                return LiveKitDispatchResult(
                    strategy=strategy,
                    attempted=True,
                    applied=False,
                    room_name=room_name,
                    agent_name=agent_name or self.config.agent_name,
                    error="agent_dispatch_api_unavailable",
                    metadata={"voice_mode": self.config.voice_mode},
                )
            dispatch_result = await self._dispatch_with_room_retry(
                dispatch_api=dispatch_api,
                request=request,
                room_name=room_name,
                livekit_api=livekit_api,
                client=client,
            )
            dispatch_id = getattr(dispatch_result, "dispatch_id", None) or getattr(dispatch_result, "id", None)
            return LiveKitDispatchResult(
                strategy=strategy,
                attempted=True,
                applied=True,
                room_name=room_name,
                agent_name=agent_name or self.config.agent_name,
                dispatch_id=None if dispatch_id is None else str(dispatch_id),
                metadata={"mechanism": "api_dispatch", "voice_mode": self.config.voice_mode},
            )
        except Exception as exc:
            return LiveKitDispatchResult(
                strategy=strategy,
                attempted=True,
                applied=False,
                room_name=room_name,
                agent_name=agent_name or self.config.agent_name,
                error=str(exc),
                metadata={"voice_mode": self.config.voice_mode},
            )
        finally:
            pass  # Client is cached in _shared_api_client; closed via close()

    def _resolve_dispatch_api(self, *, client: Any, livekit_api: Any) -> Any:
        if hasattr(client, "agent_dispatch"):
            return getattr(client, "agent_dispatch")
        if hasattr(livekit_api, "AgentDispatchService"):
            return livekit_api.AgentDispatchService(client)
        return None

    async def _dispatch_with_room_retry(
        self,
        *,
        dispatch_api: Any,
        request: Any,
        room_name: str,
        livekit_api: Any,
        client: Any,
    ) -> Any:
        try:
            return await self._await_if_needed(dispatch_api.create_dispatch(request))
        except Exception as exc:
            if not self._should_retry_after_room_create(exc):
                raise
            created = await self._ensure_room_exists(
                room_name=room_name,
                livekit_api=livekit_api,
                client=client,
            )
            if not created:
                raise
            return await self._await_if_needed(dispatch_api.create_dispatch(request))

    async def _ensure_room_exists(
        self,
        *,
        room_name: str,
        livekit_api: Any,
        client: Any,
    ) -> bool:
        create_room_request_cls = getattr(livekit_api, "CreateRoomRequest", None)
        if create_room_request_cls is None:
            return False
        room_api = self._resolve_room_api(client=client, livekit_api=livekit_api)
        create_room = None if room_api is None else getattr(room_api, "create_room", None)
        if not callable(create_room):
            return False
        try:
            request = create_room_request_cls(name=room_name)
        except TypeError:
            request = create_room_request_cls(room=room_name)
        try:
            await self._await_if_needed(create_room(request))
            return True
        except Exception as exc:
            if self._room_already_exists(exc):
                return True
            return False

    def _resolve_room_api(self, *, client: Any, livekit_api: Any) -> Any:
        if hasattr(client, "room"):
            return getattr(client, "room")
        service_cls = getattr(livekit_api, "RoomService", None)
        if service_cls is not None:
            return service_cls(client)
        return None

    def _should_retry_after_room_create(self, exc: Exception) -> bool:
        # Prefer gRPC status code check; fall back to message string for non-gRPC paths.
        code_attr = getattr(exc, "code", None)
        if callable(code_attr):
            code_str = str(code_attr())
            if "NOT_FOUND" in code_str:
                return True
        elif code_attr is not None:
            if "NOT_FOUND" in str(code_attr):
                return True
        message = str(exc).lower()
        return "not_found" in message or "could not find object" in message or "room not found" in message

    def _room_already_exists(self, exc: Exception) -> bool:
        # Prefer gRPC status code check; fall back to message string for non-gRPC paths.
        code_attr = getattr(exc, "code", None)
        if callable(code_attr):
            code_str = str(code_attr())
            if "ALREADY_EXISTS" in code_str:
                return True
        elif code_attr is not None:
            if "ALREADY_EXISTS" in str(code_attr):
                return True
        message = str(exc).lower()
        return "already exists" in message or "already_exists" in message or "duplicate" in message

    async def _await_if_needed(self, value: Any) -> Any:
        if hasattr(value, "__await__"):
            return await value
        return value


@dataclass(slots=True)
class LiveKitRoomRuntimeClient:
    config: LiveKitAdapterConfig
    api_loader: Callable[[], Any] = load_livekit_api_sdk
    api_client_factory: Callable[[Any, LiveKitAdapterConfig], Any] | None = None

    async def ping(self) -> bool:
        try:
            await self.list_rooms(limit=1)
            return True
        except Exception:
            return False

    async def list_rooms(self, *, limit: int | None = None) -> list[dict[str, object]]:
        request_kwargs: dict[str, object] = {}
        if limit is not None:
            request_kwargs["limit"] = limit
        response = await self._call_room_api(
            method_name="list_rooms",
            request_class_candidates=("ListRoomsRequest",),
            request_kwargs=request_kwargs,
        )
        if response is None:
            return []
        rooms = getattr(response, "rooms", None)
        if isinstance(rooms, list):
            return [self._room_to_dict(item) for item in rooms]
        if isinstance(response, list):
            return [self._room_to_dict(item) for item in response]
        return []

    async def get_room(self, *, room_name: str) -> dict[str, object] | None:
        # Use the server-side names filter to avoid fetching all rooms.
        response = await self._call_room_api(
            method_name="list_rooms",
            request_class_candidates=("ListRoomsRequest",),
            request_kwargs={"names": [room_name]},
        )
        if response is None:
            return None
        rooms_raw = getattr(response, "rooms", None)
        if isinstance(rooms_raw, list):
            rooms = [self._room_to_dict(item) for item in rooms_raw]
        elif isinstance(response, list):
            rooms = [self._room_to_dict(item) for item in response]
        else:
            return None
        for room in rooms:
            if str(room.get("name") or "").strip() == room_name:
                return room
        return None

    async def get_room_participants(self, *, room_name: str) -> list[dict[str, object]]:
        response = await self._call_room_api(
            method_name="list_participants",
            request_class_candidates=("ListParticipantsRequest",),
            request_kwargs={"room": room_name},
        )
        if response is None:
            return []
        participants = getattr(response, "participants", None)
        if isinstance(participants, list):
            return [self._participant_to_dict(item) for item in participants]
        if isinstance(response, list):
            return [self._participant_to_dict(item) for item in response]
        return []

    async def delete_room(self, *, room_name: str) -> bool:
        try:
            await self._call_room_api(
                method_name="delete_room",
                request_class_candidates=("DeleteRoomRequest",),
                request_kwargs={"room": room_name},
            )
            return True
        except Exception:
            return False

    async def _call_room_api(
        self,
        *,
        method_name: str,
        request_class_candidates: tuple[str, ...],
        request_kwargs: dict[str, object],
    ) -> Any:
        livekit_api = self.api_loader()
        client = self._get_or_create_api_client(livekit_api)
        if client is None:
            raise LiveKitAgentsUnavailableError("LiveKit API client is not available")
        room_api = self._resolve_room_api(client=client, livekit_api=livekit_api)
        if room_api is None:
            raise LiveKitAgentsUnavailableError("LiveKit room API is not available")
        operation = getattr(room_api, method_name, None)
        if not callable(operation):
            raise LiveKitAgentsUnavailableError(f"LiveKit room API does not support {method_name}")
        request = self._build_request(livekit_api=livekit_api, candidates=request_class_candidates, kwargs=request_kwargs)
        if request is None:
            result = operation(**request_kwargs)
        else:
            result = operation(request)
        if hasattr(result, "__await__"):
            return await result
        return result

    def _resolve_room_api(self, *, client: Any, livekit_api: Any) -> Any:
        if hasattr(client, "room"):
            return getattr(client, "room")
        service_cls = getattr(livekit_api, "RoomService", None)
        if service_cls is not None:
            return service_cls(client)
        return None

    def _build_request(self, *, livekit_api: Any, candidates: tuple[str, ...], kwargs: dict[str, object]) -> Any:
        for request_class_name in candidates:
            request_cls = getattr(livekit_api, request_class_name, None)
            if request_cls is None:
                continue
            try:
                return request_cls(**kwargs)
            except TypeError:
                continue
        return None

    def _room_to_dict(self, room: Any) -> dict[str, object]:
        if isinstance(room, dict):
            return {str(key): value for key, value in room.items()}
        payload: dict[str, object] = {}
        for key in ("sid", "name", "empty_timeout", "departure_timeout", "num_participants", "creation_time"):
            value = getattr(room, key, None)
            if value is not None:
                payload[key] = value
        return payload

    def _participant_to_dict(self, participant: Any) -> dict[str, object]:
        if isinstance(participant, dict):
            return {str(key): value for key, value in participant.items()}
        payload: dict[str, object] = {}
        for key in ("sid", "identity", "name", "state", "joined_at", "joinedAt"):
            value = getattr(participant, key, None)
            if value is not None:
                normalized_key = "joined_at" if key == "joinedAt" else key
                payload[normalized_key] = value
        return payload


@dataclass(slots=True, frozen=True)
class LiveKitWorkerDispatchContext:
    conversation_id: str
    realtime_session_id: str
    agent_id: str
    agent_version_id: str
    channel: str
    room_name: str
    voice_mode: str = "pipeline"
    participant_identity: str | None = None
    provider_session_id: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: object) -> LiveKitWorkerDispatchContext:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {}
        if not isinstance(raw, dict):
            raise ValueError("LiveKit worker dispatch metadata must be a JSON object")

        def _coerce_str(value: object | None, *, default: str = "") -> str:
            if value is None:
                return default
            if isinstance(value, str):
                text = value.strip()
                return text if text else default
            if isinstance(value, (int, float, bool)):
                text = str(value).strip()
                return text if text else default
            return default

        payload = {str(key): value for key, value in raw.items()}
        metadata = _json_object(payload.get("metadata"))
        conversation_id = _coerce_str(payload.get("conversation_id"))
        realtime_session_id = _coerce_str(payload.get("realtime_session_id"))
        room_name = _coerce_str(payload.get("room_name"), default=_coerce_str(payload.get("provider_session_id")))
        return cls(
            conversation_id=conversation_id,
            realtime_session_id=realtime_session_id,
            agent_id=str(payload.get("agent_id") or ""),
            agent_version_id=str(payload.get("agent_version_id") or ""),
            channel=str(payload.get("channel") or "web_widget"),
            room_name=room_name,
            voice_mode=str(payload.get("voice_mode") or "pipeline"),
            participant_identity=None
            if payload.get("participant_identity") in {None, ""}
            else _coerce_str(payload.get("participant_identity")),
            provider_session_id=None
            if payload.get("provider_session_id") in {None, ""}
            else _coerce_str(payload.get("provider_session_id")),
            metadata=metadata,
        )


@dataclass(slots=True)
class LiveKitControlPlaneClient:
    base_url: str
    provider_secret: str
    client: Any | None = None
    client_factory: Callable[[], Any] | None = None
    _owns_client: bool = field(default=False, init=False, repr=False, compare=False)

    def __getstate__(self) -> dict[str, object]:
        # LiveKit worker jobs can be spawned in child processes; never pickle a live HTTP client.
        return {
            "base_url": self.base_url,
            "provider_secret": self.provider_secret,
            "client": None,
            "client_factory": None,
        }

    def __setstate__(self, state: dict[str, object]) -> None:
        self.base_url = str(state.get("base_url") or "")
        self.provider_secret = str(state.get("provider_secret") or "")
        self.client = None
        self.client_factory = None
        self._owns_client = False

    def _ensure_client(self) -> Any:
        client = self.client
        if client is not None:
            return client
        factory = self.client_factory
        if callable(factory):
            client = factory()
        else:
            client = httpx.AsyncClient(timeout=15.0)
        self.client = client
        self._owns_client = True
        return client

    async def aclose(self) -> None:
        client = self.client
        if client is None or not self._owns_client:
            return
        if hasattr(client, "aclose"):
            await client.aclose()
        elif hasattr(client, "close"):
            client.close()
        self.client = None
        self._owns_client = False

    async def commit_partial_transcript(
        self,
        *,
        realtime_session_id: str,
        text: str,
        participant_identity: str | None = None,
        provider_session_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return await self._post_transcript(
            realtime_session_id=realtime_session_id,
            text=text,
            is_final=False,
            idempotency_key=None,
            participant_identity=participant_identity,
            provider_session_id=provider_session_id,
            metadata=metadata,
        )

    async def replay_events(
        self,
        *,
        conversation_id: str,
        after_sequence: int = 0,
        family: str | None = None,
        name: str | None = None,
    ) -> list[dict[str, object]]:
        params: dict[str, object] = {"after_sequence": after_sequence}
        if family is not None:
            params["family"] = family
        if name is not None:
            params["name"] = name
        client = self._ensure_client()
        response = await client.get(
            f"{self.base_url.rstrip('/')}/providers/livekit/conversations/{conversation_id}/events",
            params=params,
            headers={"X-Ruhu-Provider-Secret": self.provider_secret},
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    async def replay_assistant_outputs(
        self,
        *,
        conversation_id: str,
        after_sequence: int = 0,
    ) -> list[dict[str, object]]:
        client = self._ensure_client()
        response = await client.get(
            f"{self.base_url.rstrip('/')}/providers/livekit/conversations/{conversation_id}/assistant-outputs",
            params={"after_sequence": after_sequence},
            headers={"X-Ruhu-Provider-Secret": self.provider_secret},
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    async def commit_final_transcript(
        self,
        *,
        realtime_session_id: str,
        text: str,
        idempotency_key: str,
        participant_identity: str | None = None,
        provider_session_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return await self._post_transcript(
            realtime_session_id=realtime_session_id,
            text=text,
            is_final=True,
            idempotency_key=idempotency_key,
            participant_identity=participant_identity,
            provider_session_id=provider_session_id,
            metadata=metadata,
        )

    async def commit_text_message(
        self,
        *,
        realtime_session_id: str,
        text: str,
        participant_identity: str | None = None,
        provider_session_id: str | None = None,
        attachment_ids: list[str] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        client = self._ensure_client()
        response = await client.post(
            f"{self.base_url.rstrip('/')}/providers/livekit/voice/sessions/{realtime_session_id}/messages",
            json={
                "text": text,
                "participant_identity": participant_identity,
                "provider_session_id": provider_session_id,
                "attachment_ids": list(attachment_ids or []),
                "metadata": dict(metadata or {}),
            },
            headers={"X-Ruhu-Provider-Secret": self.provider_secret},
        )
        response.raise_for_status()
        return response.json()

    async def transition_session(
        self,
        *,
        realtime_session_id: str,
        target: str,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        client = self._ensure_client()
        response = await client.post(
            f"{self.base_url.rstrip('/')}/providers/livekit/voice/sessions/{realtime_session_id}/{target}",
            json={"reason": reason, "metadata": dict(metadata or {})},
            headers={"X-Ruhu-Provider-Secret": self.provider_secret},
        )
        response.raise_for_status()
        return response.json()

    async def emit_voice_signal(
        self,
        *,
        realtime_session_id: str,
        signal: str,
        reason: str | None = None,
        participant_identity: str | None = None,
        provider_session_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        client = self._ensure_client()
        response = await client.post(
            f"{self.base_url.rstrip('/')}/providers/livekit/voice/sessions/{realtime_session_id}/signals",
            json={
                "signal": signal,
                "reason": reason,
                "participant_identity": participant_identity,
                "provider_session_id": provider_session_id,
                "metadata": dict(metadata or {}),
            },
            headers={"X-Ruhu-Provider-Secret": self.provider_secret},
        )
        response.raise_for_status()
        return response.json()

    async def acknowledge_assistant_output(
        self,
        *,
        realtime_session_id: str,
        delivery_id: str,
        stage: str,
        reason: str | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        client = self._ensure_client()
        response = await client.post(
            f"{self.base_url.rstrip('/')}/providers/livekit/voice/sessions/{realtime_session_id}/assistant-outputs/{delivery_id}/ack",
            json={
                "stage": stage,
                "reason": reason,
                "idempotency_key": idempotency_key,
                "metadata": dict(metadata or {}),
            },
            headers={"X-Ruhu-Provider-Secret": self.provider_secret},
        )
        response.raise_for_status()
        return response.json()

    async def _post_transcript(
        self,
        *,
        realtime_session_id: str,
        text: str,
        is_final: bool,
        idempotency_key: str | None,
        participant_identity: str | None,
        provider_session_id: str | None,
        metadata: dict[str, object] | None,
    ) -> dict[str, object]:
        if is_final and idempotency_key:
            return await self._post_final_transcript_with_retry(
                realtime_session_id=realtime_session_id,
                text=text,
                idempotency_key=idempotency_key,
                participant_identity=participant_identity,
                provider_session_id=provider_session_id,
                metadata=metadata,
            )
        return await self._post_transcript_once(
            realtime_session_id=realtime_session_id,
            text=text,
            is_final=is_final,
            idempotency_key=idempotency_key,
            participant_identity=participant_identity,
            provider_session_id=provider_session_id,
            metadata=metadata,
            timeout=None,
        )

    async def _post_final_transcript_with_retry(
        self,
        *,
        realtime_session_id: str,
        text: str,
        idempotency_key: str,
        participant_identity: str | None,
        provider_session_id: str | None,
        metadata: dict[str, object] | None,
    ) -> dict[str, object]:
        last_exc: Exception | None = None
        for attempt in range(1, _FINAL_TRANSCRIPT_POST_ATTEMPTS + 1):
            try:
                return await self._post_transcript_once(
                    realtime_session_id=realtime_session_id,
                    text=text,
                    is_final=True,
                    idempotency_key=idempotency_key,
                    participant_identity=participant_identity,
                    provider_session_id=provider_session_id,
                    metadata=metadata,
                    timeout=_FINAL_TRANSCRIPT_POST_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                last_exc = exc
                if attempt >= _FINAL_TRANSCRIPT_POST_ATTEMPTS or not _is_retryable_transcript_error(exc):
                    raise
                delay = _retry_delay_seconds(exc, attempt=attempt)
                logger.warning(
                    "livekit final transcript commit retrying",
                    extra={
                        "realtime_session_id": realtime_session_id,
                        "idempotency_key": idempotency_key,
                        "attempt": attempt,
                        "next_attempt": attempt + 1,
                        "delay_seconds": delay,
                        "error_type": type(exc).__name__,
                    },
                )
                await asyncio.sleep(delay)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("final transcript commit retry loop exhausted without an exception")

    async def _post_transcript_once(
        self,
        *,
        realtime_session_id: str,
        text: str,
        is_final: bool,
        idempotency_key: str | None,
        participant_identity: str | None,
        provider_session_id: str | None,
        metadata: dict[str, object] | None,
        timeout: float | None,
    ) -> dict[str, object]:
        client = self._ensure_client()
        kwargs: dict[str, object] = {
            "json": {
                "text": text,
                "is_final": is_final,
                "idempotency_key": idempotency_key,
                "participant_identity": participant_identity,
                "provider_session_id": provider_session_id,
                "metadata": dict(metadata or {}),
            },
            "headers": {"X-Ruhu-Provider-Secret": self.provider_secret},
        }
        if timeout is not None:
            kwargs["timeout"] = timeout
        response = await client.post(
            f"{self.base_url.rstrip('/')}/providers/livekit/voice/sessions/{realtime_session_id}/transcripts",
            **kwargs,
        )
        response.raise_for_status()
        return response.json()


def _is_retryable_transcript_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _TRANSIENT_TRANSCRIPT_STATUSES
    return isinstance(exc, httpx.RequestError)


def _retry_delay_seconds(exc: Exception, *, attempt: int) -> float:
    if isinstance(exc, httpx.HTTPStatusError):
        retry_after = exc.response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, min(float(retry_after), 5.0))
            except ValueError:
                pass
    index = max(0, min(attempt - 1, len(_TRANSCRIPT_POST_RETRY_DELAYS_SECONDS) - 1))
    return _TRANSCRIPT_POST_RETRY_DELAYS_SECONDS[index]


@dataclass(slots=True)
class RuhuLiveKitAgentWorker:
    config: LiveKitAdapterConfig
    control_plane_client: LiveKitControlPlaneClient
    sdk_loader: Callable[[], Any] = load_livekit_agents_sdk

    def resolve_voice_mode(self, raw_value: str | None = None) -> str:
        voice_mode = (raw_value or self.config.voice_mode or "pipeline").strip().lower()
        if voice_mode not in {"pipeline", "realtime_assisted"}:
            return "pipeline"
        return voice_mode

    def create_agent_session(self, **kwargs: Any) -> Any:
        agents_sdk = self.sdk_loader()
        session_cls = getattr(agents_sdk, "AgentSession", None)
        if session_cls is None:
            raise LiveKitAgentsUnavailableError("LiveKit Agents SDK does not expose AgentSession")
        return session_cls(**kwargs)

    def create_managed_agent_session(
        self,
        *,
        voice_mode: str | None = None,
        **kwargs: Any,
    ) -> Any:
        resolved_voice_mode = self.resolve_voice_mode(voice_mode)
        managed_kwargs = dict(kwargs)
        metadata = _json_object(managed_kwargs.pop("metadata", None))
        metadata.setdefault("voice_mode", resolved_voice_mode)
        session = self.create_agent_session(**managed_kwargs)
        if hasattr(session, "metadata"):
            try:
                existing = _json_object(getattr(session, "metadata", None))
                existing.update(metadata)
                setattr(session, "metadata", existing)
            except Exception:
                pass
        elif hasattr(session, "__dict__"):
            try:
                setattr(session, "ruhu_metadata", metadata)
            except Exception:
                pass
        return session

    async def replay_events(
        self,
        *,
        conversation_id: str,
        after_sequence: int = 0,
        family: str | None = None,
        name: str | None = None,
    ) -> list[dict[str, object]]:
        return await self.control_plane_client.replay_events(
            conversation_id=conversation_id,
            after_sequence=after_sequence,
            family=family,
            name=name,
        )

    async def replay_assistant_voice_outputs(
        self,
        *,
        conversation_id: str,
        after_sequence: int = 0,
    ) -> list[dict[str, object]]:
        outputs = await self.control_plane_client.replay_assistant_outputs(
            conversation_id=conversation_id,
            after_sequence=after_sequence,
        )
        normalized: list[dict[str, object]] = []
        for output in outputs:
            try:
                conversation_sequence = int(output.get("conversation_sequence") or 0)
            except (TypeError, ValueError):
                continue
            text = str(output.get("text") or "").strip()
            if not text:
                continue
            normalized.append(
                {
                    "delivery_id": output.get("delivery_id"),
                    "event_id": output.get("source_event_id") or output.get("delivery_id"),
                    "conversation_sequence": conversation_sequence,
                    "text": text,
                    "trace_id": output.get("trace_id"),
                    "turn_id": output.get("turn_id"),
                }
            )
        return normalized

    async def emit_partial_transcript(
        self,
        *,
        realtime_session_id: str,
        text: str,
        participant_identity: str | None = None,
        provider_session_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return await self.control_plane_client.commit_partial_transcript(
            realtime_session_id=realtime_session_id,
            text=text,
            participant_identity=participant_identity,
            provider_session_id=provider_session_id,
            metadata=metadata,
        )

    async def emit_final_transcript(
        self,
        *,
        realtime_session_id: str,
        text: str,
        idempotency_key: str,
        participant_identity: str | None = None,
        provider_session_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return await self.control_plane_client.commit_final_transcript(
            realtime_session_id=realtime_session_id,
            text=text,
            idempotency_key=idempotency_key,
            participant_identity=participant_identity,
            provider_session_id=provider_session_id,
            metadata=metadata,
        )

    async def emit_text_message(
        self,
        *,
        realtime_session_id: str,
        text: str,
        participant_identity: str | None = None,
        provider_session_id: str | None = None,
        attachment_ids: list[str] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return await self.control_plane_client.commit_text_message(
            realtime_session_id=realtime_session_id,
            text=text,
            participant_identity=participant_identity,
            provider_session_id=provider_session_id,
            attachment_ids=attachment_ids,
            metadata=metadata,
        )

    async def emit_voice_signal(
        self,
        *,
        realtime_session_id: str,
        signal: str,
        reason: str | None = None,
        participant_identity: str | None = None,
        provider_session_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return await self.control_plane_client.emit_voice_signal(
            realtime_session_id=realtime_session_id,
            signal=signal,
            reason=reason,
            participant_identity=participant_identity,
            provider_session_id=provider_session_id,
            metadata=metadata,
        )

    async def acknowledge_assistant_output(
        self,
        *,
        realtime_session_id: str,
        delivery_id: str,
        stage: str,
        reason: str | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return await self.control_plane_client.acknowledge_assistant_output(
            realtime_session_id=realtime_session_id,
            delivery_id=delivery_id,
            stage=stage,
            reason=reason,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )

    async def mark_session_disconnected(
        self,
        *,
        realtime_session_id: str,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return await self.control_plane_client.transition_session(
            realtime_session_id=realtime_session_id,
            target="disconnect",
            reason=reason,
            metadata=metadata,
        )

    async def mark_session_ended(
        self,
        *,
        realtime_session_id: str,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return await self.control_plane_client.transition_session(
            realtime_session_id=realtime_session_id,
            target="end",
            reason=reason,
            metadata=metadata,
        )

    async def mark_session_errored(
        self,
        *,
        realtime_session_id: str,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return await self.control_plane_client.transition_session(
            realtime_session_id=realtime_session_id,
            target="error",
            reason=reason,
            metadata=metadata,
        )


@dataclass(slots=True)
class LiveKitPhoneAdapter:
    config: LiveKitAdapterConfig | None
    require_provider_secret: Callable[[str | None], None]
    start_live_channel_session: Callable[..., Any]
    process_live_channel_message: Callable[..., Any]
    transition_provider_session: Callable[..., Any]
    assistant_texts: Callable[[list[Any]], list[str]]
    token_issuer: LiveKitTokenIssuer | None = None

    def _phone_transport_metadata(self, payload: Any) -> dict[str, object]:
        metadata = dict(self.config.metadata) if self.config is not None else {}
        metadata.update(dict(getattr(payload, "metadata", {}) or {}))
        metadata["provider"] = "livekit"
        metadata["transport_provider"] = "livekit"
        telephony_provider = getattr(payload, "provider", None)
        if isinstance(telephony_provider, str) and telephony_provider.strip():
            metadata["telephony_provider"] = telephony_provider.strip()
        if self.config is not None:
            metadata.setdefault("agent_name", self.config.agent_name)
            metadata.setdefault("sdk_version_target", self.config.sdk_version_target)
        return metadata

    def sdk_available(self) -> bool:
        try:
            load_livekit_agents_sdk()
        except LiveKitAgentsUnavailableError:
            return False
        return True

    def start_phone_call(
        self,
        payload: Any,
        provider_secret: str | None,
    ) -> dict[str, Any]:
        self.require_provider_secret(provider_secret)
        metadata = self._phone_transport_metadata(payload)
        response = self.start_live_channel_session(
            channel="phone",
            agent_id=payload.agent_id,
            external_session_id=payload.external_session_id,
            organization_id=getattr(payload, "organization_id", None),
            provider="livekit",
            provider_session_id=payload.provider_session_id,
            participant_identity=payload.participant_identity,
            metadata=metadata,
        )
        transport = None
        if self.token_issuer is not None and response.realtime_session_id:
            transport = self.token_issuer.issue_voice_transport(
                channel="phone",
                conversation_id=response.conversation_id,
                realtime_session_id=response.realtime_session_id,
                participant_identity=payload.participant_identity,
                metadata={
                    "provider_session_id": payload.provider_session_id,
                    "external_session_id": payload.external_session_id,
                },
            ).as_dict()
        return {
            "conversation_id": response.conversation_id,
            "realtime_session_id": response.realtime_session_id,
            "step_after": response.step_after,
            "transport": transport,
            "speak_texts": self.assistant_texts(response.messages),
            "messages": response.messages,
            "trace_id": response.trace_id,
            "pending_tool_invocations": response.pending_tool_invocations,
        }

    def ingest_phone_transcript(
        self,
        *,
        call_id: str,
        payload: Any,
        provider_secret: str | None,
    ) -> dict[str, Any]:
        self.require_provider_secret(provider_secret)
        metadata = self._phone_transport_metadata(payload)
        response = self.process_live_channel_message(
            channel="phone",
            external_session_id=call_id,
            agent_id=payload.agent_id,
            text=payload.text,
            metadata=metadata,
            modality="audio",
            event_type="user_final_transcript" if payload.is_final else "user_partial_transcript",
            organization_id=None,
            emit_entry_prelude_on_autostart=True,
            provider="livekit",
            provider_session_id=payload.provider_session_id,
            participant_identity=payload.participant_identity,
            idempotency_key=payload.idempotency_key,
        )
        return {
            "conversation_id": response.conversation_id,
            "realtime_session_id": response.realtime_session_id,
            "step_after": response.step_after,
            "speak_texts": self.assistant_texts(response.messages),
            "messages": response.messages,
            "trace_id": response.trace_id,
            "pending_tool_invocations": response.pending_tool_invocations,
        }

    def transition_phone_call(
        self,
        *,
        call_id: str,
        payload: Any | None,
        provider_secret: str | None,
        target: str,
    ) -> Any:
        self.require_provider_secret(provider_secret)
        request_payload = payload or SimpleNamespace(reason=None, metadata={})
        metadata = dict(self.config.metadata) if self.config is not None else {}
        metadata.update(dict(getattr(request_payload, "metadata", {}) or {}))
        if self.config is not None:
            metadata.setdefault("agent_name", self.config.agent_name)
            metadata.setdefault("sdk_version_target", self.config.sdk_version_target)
        return self.transition_provider_session(
            channel="phone",
            external_session_id=call_id,
            provider="livekit",
            target=target,
            reason=request_payload.reason,
            metadata=metadata,
        )
