from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path

import httpx
from livekit import api as livekit_api

from ruhu.api import build_default_app
from ruhu.runtime_config import RuntimeSettings


class _FakeLiveKitIssuer:
    def issue_voice_transport(
        self,
        *,
        channel: str,
        conversation_id: str,
        realtime_session_id: str,
        participant_identity: str | None = None,
        participant_name: str | None = None,
        metadata: dict[str, object] | None = None,
    ):
        class _Grant:
            def as_dict(self_nonlocal) -> dict[str, object]:
                return {
                    "provider": "livekit",
                    "url": "wss://livekit.example.test",
                    "room_name": f"room-{realtime_session_id}",
                    "token": "test-token",
                    "participant_identity": participant_identity or f"{channel}:{conversation_id}:{realtime_session_id}",
                    "agent_name": "ruhu-voice",
                    "sdk_version_target": "1.5.2",
                    "voice_mode": "pipeline",
                    "dispatch_strategy": "room_config",
                    "dispatch": {
                        "agent_name": "ruhu-voice",
                        "conversation_id": conversation_id,
                        "realtime_session_id": realtime_session_id,
                        "channel": channel,
                    },
                    "metadata": dict(metadata or {}),
                }

        return _Grant()


class _FakeDispatchResult:
    def as_dict(self) -> dict[str, object]:
        return {
            "strategy": "api_dispatch",
            "attempted": True,
            "applied": True,
            "dispatch_id": "dispatch-1",
        }


class _FakeDispatchClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object | None]] = []

    async def create_dispatch(
        self,
        *,
        room_name: str,
        metadata: dict[str, object] | None = None,
        agent_name: str | None = None,
    ) -> _FakeDispatchResult:
        self.calls.append(
            {
                "room_name": room_name,
                "metadata": metadata,
                "agent_name": agent_name,
            }
        )
        return _FakeDispatchResult()


class _FailingDispatchClient:
    async def create_dispatch(
        self,
        *,
        room_name: str,
        metadata: dict[str, object] | None = None,
        agent_name: str | None = None,
    ) -> dict[str, object]:
        del room_name, metadata, agent_name
        return {
            "strategy": "api_dispatch",
            "attempted": True,
            "applied": False,
            "error": "dispatch backend unavailable",
        }


class _FakeRoomRuntimeClient:
    async def ping(self) -> bool:
        return True

    async def get_room_participants(self, *, room_name: str) -> list[dict[str, object]]:
        return [
            {"identity": "user:test", "name": "User", "joined_at": "2026-04-11T10:00:00Z"},
            {"identity": "agent:ruhu", "name": "Agent", "joined_at": "2026-04-11T10:00:01Z"},
        ]

    async def delete_room(self, *, room_name: str) -> bool:
        return True


def _livekit_webhook_authorization_header(
    *,
    api_key: str,
    api_secret: str,
    raw_body: str,
) -> str:
    body_sha256 = base64.b64encode(hashlib.sha256(raw_body.encode("utf-8")).digest()).decode("utf-8")
    token = livekit_api.AccessToken(api_key, api_secret).with_sha256(body_sha256).to_jwt()
    return f"Bearer {token}"


def test_live_call_routes_create_list_status_delete_and_webhook(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        runtime_settings = RuntimeSettings(
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            provider_shared_secret="livekit-provider-secret",
            internal_api_secret="internal-ops-secret",
            livekit_server_url="wss://livekit.example.test",
            livekit_api_key="key",
            livekit_api_secret="secret-key-at-least-32-characters",
        )
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_settings.database_url,
            runtime_settings=runtime_settings,
            bootstrap_organization_id="public",
        )
        app.state.livekit_token_issuer = _FakeLiveKitIssuer()
        dispatch_client = _FakeDispatchClient()
        app.state.livekit_dispatch_client = dispatch_client
        app.state.livekit_room_runtime_client = _FakeRoomRuntimeClient()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            health = await client.get("/voice-sessions/health")
            assert health.status_code == 200
            health_payload = health.json()
            assert health_payload["voice_available"] is True
            assert health_payload["livekit_reachable"] is True
            assert health_payload["mock"] is False

            created = await client.post(
                "/voice-sessions",
                json={"agent_id": "sales"},
            )
            assert created.status_code == 200
            created_payload = created.json()
            session_id = created_payload["id"]
            room_name = created_payload["room_name"]
            assert created_payload["agent_id"] == "sales"
            assert created_payload["status"] == "active"
            assert created_payload["connection_url"] == "wss://livekit.example.test"
            assert created_payload["access_token"] == "test-token"
            assert dispatch_client.calls
            dispatch_metadata = dispatch_client.calls[-1]["metadata"]
            assert isinstance(dispatch_metadata, dict)
            assert dispatch_metadata["conversation_id"] == created_payload["conversation_id"]
            assert isinstance(dispatch_metadata.get("metadata"), dict)
            assert dispatch_metadata["metadata"]["voice_interaction_policy"]["endpointing_ms"] == 650
            assert "interruptibility_policy" in dispatch_metadata["metadata"]["voice_interaction_policy"]

            listed = await client.get("/voice-sessions")
            assert listed.status_code == 200
            listed_payload = listed.json()
            assert len(listed_payload) == 1
            assert listed_payload[0]["id"] == session_id
            assert listed_payload[0]["status"] == "active"

            active_count = await client.get("/voice-sessions/active/count")
            assert active_count.status_code == 200
            assert active_count.json()["active_sessions"] == 1

            status_response = await client.get(f"/voice-sessions/{session_id}")
            assert status_response.status_code == 200
            status_payload = status_response.json()
            assert status_payload["status"] == "active"
            assert status_payload["room_name"] == room_name
            assert status_payload["num_participants"] == 2

            webhook_payload = {
                "event": "participant_disconnected",
                "room": {"name": room_name},
                "participant": {"identity": "user:test"},
            }
            webhook_raw_body = json.dumps(webhook_payload, separators=(",", ":"))
            webhook_auth = _livekit_webhook_authorization_header(
                api_key=runtime_settings.livekit_api_key,
                api_secret=runtime_settings.livekit_api_secret,
                raw_body=webhook_raw_body,
            )
            webhook = await client.post(
                "/providers/livekit/webhooks",
                json=webhook_payload,
                headers={"Authorization": webhook_auth},
            )
            assert webhook.status_code == 200
            assert webhook.json()["status"] == "ok"

            unauthorized_webhook = await client.post(
                "/providers/livekit/webhooks",
                json=webhook_payload,
                headers={"Authorization": "Bearer invalid-token"},
            )
            assert unauthorized_webhook.status_code == 403

            status_after_webhook = await client.get(f"/voice-sessions/{session_id}")
            assert status_after_webhook.status_code == 200
            assert status_after_webhook.json()["status"] == "disconnected"

            ended = await client.request(
                "DELETE",
                f"/voice-sessions/{session_id}",
                json={"reason": "user_ended"},
            )
            assert ended.status_code == 204

            ended_status = await client.get(f"/voice-sessions/{session_id}")
            assert ended_status.status_code == 200
            assert ended_status.json()["status"] == "ended"

    asyncio.run(run())


def test_live_call_route_returns_503_when_dispatch_attempt_fails(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        runtime_settings = RuntimeSettings(
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            provider_shared_secret="livekit-provider-secret",
            internal_api_secret="internal-ops-secret",
            livekit_server_url="wss://livekit.example.test",
            livekit_api_key="key",
            livekit_api_secret="secret-key-at-least-32-characters",
        )
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_settings.database_url,
            runtime_settings=runtime_settings,
            bootstrap_organization_id="public",
        )
        app.state.livekit_token_issuer = _FakeLiveKitIssuer()
        app.state.livekit_dispatch_client = _FailingDispatchClient()
        app.state.livekit_room_runtime_client = _FakeRoomRuntimeClient()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            created = await client.post(
                "/voice-sessions",
                json={"agent_id": "sales"},
            )

        assert created.status_code == 503
        assert created.json()["detail"] == "dispatch backend unavailable"

    asyncio.run(run())
