from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select

from ruhu.api import build_default_app
from ruhu.db import build_session_factory
from ruhu.db_models import RealtimeOutboxRecord
from ruhu.provider_projection import MetaWhatsAppProjectionDispatcher
from ruhu.runtime_config import RuntimeSettings


class _FailingProviderClient:
    async def post(self, url: str, *, json=None, headers=None):
        request = httpx.Request("POST", url, json=json, headers=headers)
        raise httpx.ConnectError("provider network unavailable", request=request)


class _HTTPStatusFailingProviderClient:
    def __init__(self, status_code: int, *, message: str, code: int | None = None) -> None:
        self.status_code = status_code
        self.message = message
        self.code = code

    async def post(self, url: str, *, json=None, headers=None):
        request = httpx.Request("POST", url, json=json, headers=headers)
        payload = {
            "error": {
                "message": self.message,
                **({"code": self.code} if self.code is not None else {}),
            }
        }
        response = httpx.Response(self.status_code, request=request, json=payload)
        raise httpx.HTTPStatusError(self.message, request=request, response=response)


def test_whatsapp_projection_dispatch_schedules_retry_on_transient_failure(postgres_database_url_factory) -> None:
    async def run() -> None:
        database_url = postgres_database_url_factory()
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        runtime_settings = RuntimeSettings(
            database_url=database_url,
            interpreter_name="sales",
            whatsapp_meta_channels={
                "phone-number-id-1": {
                    "phone_number_id": "phone-number-id-1",
                    "agent_id": "sales",
                    "verify_token": "verify-token",
                    "access_token": "meta-access-token",
                    "app_secret": "meta-app-secret",
                }
            },
        )
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=database_url,
            runtime_settings=runtime_settings,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/channels/whatsapp/messages",
                json={
                    "agent_id": "sales",
                    "external_session_id": "wa-retry-1",
                    "text": "Tell me about pricing.",
                    "provider": "meta_whatsapp",
                    "provider_session_id": "phone-number-id-1",
                    "participant_identity": "15551230000",
                },
            )
            assert response.status_code == 200
            conversation_id = response.json()["conversation_id"]

        dispatcher = MetaWhatsAppProjectionDispatcher(
            control_plane=app.state.realtime_control_plane,
            configs=app.state.whatsapp_meta_channels,
            provider_cost_store=app.state.provider_cost_store,
            client=_FailingProviderClient(),
        )
        outcome = await dispatcher.dispatch_pending(conversation_id=conversation_id, limit=10)
        assert outcome.attempted >= 1
        assert outcome.retried >= 1
        assert outcome.delivered == 0
        assert outcome.failed == 0

        session_factory = build_session_factory(database_url)
        with session_factory() as session:
            records = session.execute(
                select(RealtimeOutboxRecord).where(
                    RealtimeOutboxRecord.conversation_id == conversation_id,
                    RealtimeOutboxRecord.topic == "provider_projection.meta_whatsapp",
                )
            ).scalars().all()
            assert records
            assert all(record.status == "pending" for record in records)
            assert all(record.attempt_count == 1 for record in records)
            assert all(record.last_error == "provider network unavailable" for record in records)
            assert all(record.available_at > datetime.now(timezone.utc) for record in records)

        events = app.state.realtime_control_plane.events.replay(conversation_id=conversation_id)
        retry_events = [
            event
            for event in events
            if event.family == "provider" and event.name == "whatsapp_projection_retry_scheduled"
        ]
        assert retry_events
        assert all(event.payload["attempt_number"] == 1 for event in retry_events)
        assert retry_events[-1].payload["failure"]["retryable"] is True
        assert retry_events[-1].payload["failure"]["category"] == "provider_network_error"

    asyncio.run(run())


def test_whatsapp_projection_dispatch_marks_non_retryable_http_rejections_failed(postgres_database_url_factory) -> None:
    async def run() -> None:
        database_url = postgres_database_url_factory()
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        runtime_settings = RuntimeSettings(
            database_url=database_url,
            interpreter_name="sales",
            whatsapp_meta_channels={
                "phone-number-id-1": {
                    "phone_number_id": "phone-number-id-1",
                    "agent_id": "sales",
                    "verify_token": "verify-token",
                    "access_token": "meta-access-token",
                    "app_secret": "meta-app-secret",
                }
            },
        )
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=database_url,
            runtime_settings=runtime_settings,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/channels/whatsapp/messages",
                json={
                    "agent_id": "sales",
                    "external_session_id": "wa-fail-1",
                    "text": "Tell me about pricing.",
                    "provider": "meta_whatsapp",
                    "provider_session_id": "phone-number-id-1",
                    "participant_identity": "15551230001",
                },
            )
            assert response.status_code == 200
            conversation_id = response.json()["conversation_id"]

        dispatcher = MetaWhatsAppProjectionDispatcher(
            control_plane=app.state.realtime_control_plane,
            configs=app.state.whatsapp_meta_channels,
            provider_cost_store=app.state.provider_cost_store,
            client=_HTTPStatusFailingProviderClient(
                401,
                message="invalid access token",
                code=190,
            ),
        )
        outcome = await dispatcher.dispatch_pending(conversation_id=conversation_id, limit=10)
        assert outcome.attempted >= 1
        assert outcome.retried == 0
        assert outcome.delivered == 0
        assert outcome.failed >= 1

        session_factory = build_session_factory(database_url)
        with session_factory() as session:
            records = session.execute(
                select(RealtimeOutboxRecord).where(
                    RealtimeOutboxRecord.conversation_id == conversation_id,
                    RealtimeOutboxRecord.topic == "provider_projection.meta_whatsapp",
                )
            ).scalars().all()
            assert records
            assert all(record.status == "failed" for record in records)
            assert all(record.attempt_count == 1 for record in records)
            assert all(record.last_error == "invalid access token" for record in records)

        events = app.state.realtime_control_plane.events.replay(conversation_id=conversation_id)
        failure_events = [
            event
            for event in events
            if event.family == "provider" and event.name == "whatsapp_projection_failed"
        ]
        assert failure_events
        assert failure_events[-1].payload["failure"]["retryable"] is False
        assert failure_events[-1].payload["failure"]["category"] == "provider_http_rejected"
        assert failure_events[-1].payload["failure"]["status_code"] == 401
        assert failure_events[-1].payload["failure"]["provider_code"] == "190"

    asyncio.run(run())
