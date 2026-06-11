"""Tests for the widget-analytics endpoints — first coverage for both routes.

GET /agents/{agent_id}/widget-analytics lives in ruhu.routes.widget_analytics
(RP-3.1 step 9) and is the codebase's first async-converted router: it
aggregates WidgetEventRecord rows on an AsyncSession from ruhu.db_async, so
these tests run against real Postgres (per-test throwaway schema, same
pattern as tests/test_async_db_postgres.py).

POST /public/widget/sessions/{conversation_id}/events is the best-effort
ingest route still inline in api.py: it must 202 on success AND on write
failure (analytics never blocks the chat UX), and 404 only for an unknown
conversation.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import select, text

from ruhu.api import build_default_app
from ruhu.api_auth import AuthContextResolver
from ruhu.auth import AuthService, JWTCodec
from ruhu.db import build_session_factory
from ruhu.db_async import close_async_engine
from ruhu.db_models import ConversationRecord, WidgetEventRecord, WidgetSessionRecord
from ruhu.identity import InMemoryIdentityStore, Organization, OrganizationMembership, User
from ruhu.runtime_config import RuntimeSettings
from tests.conftest import make_widget_publishable_key

TEST_HS256_SECRET = "0123456789abcdef0123456789abcdef"
AGENT_ID = "sales"
ORG_ID = "test-org"
OTHER_ORG_ID = "other-org"
AGENT_ROOT = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"


def _build_auth_service() -> AuthService:
    """Analyst in ORG_ID (owns the seeded agents) + analyst in OTHER_ORG_ID."""
    store = InMemoryIdentityStore()
    store.save_organization(Organization(organization_id=ORG_ID, slug="test-org", name="Test Org"))
    store.save_organization(
        Organization(organization_id=OTHER_ORG_ID, slug="other-org", name="Other Org")
    )
    store.save_user(User(user_id="analyst-1", email="analyst@example.com"))
    store.save_user(User(user_id="outsider-1", email="outsider@example.com"))
    store.add_organization_membership(
        OrganizationMembership(user_id="analyst-1", organization_id=ORG_ID, role="analyst")
    )
    store.add_organization_membership(
        OrganizationMembership(user_id="outsider-1", organization_id=OTHER_ORG_ID, role="analyst")
    )
    return AuthService(identity_store=store, jwt_codec=JWTCodec(secret=TEST_HS256_SECRET))


def _build_app(database_url: str, service: AuthService | None = None):
    kwargs: dict = {}
    if service is not None:
        kwargs["auth_resolver"] = AuthContextResolver(auth_service=service)
        kwargs["runtime_settings"] = RuntimeSettings(
            auth_allowed_redirect_origins=["http://testserver"]
        )
    return build_default_app(
        agent_root=AGENT_ROOT,
        database_url=database_url,
        interpreter_name="sales",
        bootstrap_organization_id=ORG_ID,
        **kwargs,
    )


def _bearer(service: AuthService, *, user_id: str, organization_id: str) -> dict[str, str]:
    issued = service.issue_session(user_id=user_id, organization_id=organization_id)
    return {"Authorization": f"Bearer {issued.access_token}"}


def _seed_widget_events(
    database_url: str,
    events_by_session: dict[str, list[tuple[str, datetime]]],
) -> None:
    """Insert conversation + widget-session + event rows directly.

    Direct inserts (rather than the ingest route) give the aggregation tests
    full control over occurred_at, which drives the time-window filters.
    """
    now = datetime.now(timezone.utc)
    session_factory = build_session_factory(database_url)
    with session_factory.begin() as db:
        for session_id, events in events_by_session.items():
            conversation_id = f"conv-{session_id}"
            db.add(
                ConversationRecord(
                    conversation_id=conversation_id,
                    organization_id=ORG_ID,
                    agent_id=AGENT_ID,
                    agent_version_id="v1",
                    step_id="s1",
                    started_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add(
                WidgetSessionRecord(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    organization_id=ORG_ID,
                    session_token_hash=uuid4().hex + uuid4().hex,
                    started_at=now,
                    last_activity_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            # No ORM relationship links events to sessions, so flush the
            # parent rows first or the FK on widget_events fails.
            db.flush()
            for index, (event_type, occurred_at) in enumerate(events):
                db.add(
                    WidgetEventRecord(
                        event_id=f"{session_id}-ev-{index}",
                        organization_id=ORG_ID,
                        session_id=session_id,
                        conversation_id=conversation_id,
                        agent_id=AGENT_ID,
                        event_type=event_type,
                        event_data={},
                        occurred_at=occurred_at,
                        created_at=now,
                    )
                )


def _widget_headers(session_token: str) -> dict[str, str]:
    return {"X-Ruhu-Widget-Session-Token": session_token}


async def _create_widget_session(client: httpx.AsyncClient, publishable_key: str) -> dict:
    response = await client.post(
        "/public/widget/sessions",
        json={
            "agent_id": AGENT_ID,
            "channel": "web_widget",
            "publishable_key": publishable_key,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


# ── GET /agents/{agent_id}/widget-analytics ──────────────────────────────────


def test_widget_analytics_default_window_aggregates_seeded_events(
    postgres_database_url_factory,
) -> None:
    """Distinct-session count + per-type counts over the default 7-day window."""

    async def run() -> None:
        database_url = postgres_database_url_factory()
        service = _build_auth_service()
        app = _build_app(database_url, service)
        now = datetime.now(timezone.utc)
        _seed_widget_events(
            database_url,
            {
                "ws-a": [
                    ("widget_opened", now - timedelta(days=1)),
                    ("widget_opened", now - timedelta(days=1)),
                    ("message_sent", now - timedelta(days=1)),
                ],
                "ws-b": [("widget_opened", now - timedelta(days=2))],
                # Outside the default 7-day window — must not be counted.
                "ws-old": [("widget_opened", now - timedelta(days=30))],
            },
        )
        headers = _bearer(service, user_id="analyst-1", organization_id=ORG_ID)
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await client.get(
                    f"/agents/{AGENT_ID}/widget-analytics", headers=headers
                )
                assert response.status_code == 200, response.text
                payload = response.json()
                assert payload["agent_id"] == AGENT_ID
                assert payload["total_sessions"] == 2
                assert payload["total_events"] == 4
                assert payload["event_counts"] == {"widget_opened": 3, "message_sent": 1}
                # Default window is exactly [now - 7d, now).
                start = datetime.fromisoformat(payload["period_start"].replace("Z", "+00:00"))
                end = datetime.fromisoformat(payload["period_end"].replace("Z", "+00:00"))
                assert end - start == timedelta(days=7)
        finally:
            await close_async_engine()

    asyncio.run(run())


def test_widget_analytics_explicit_window_filters_by_occurred_at(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        database_url = postgres_database_url_factory()
        service = _build_auth_service()
        app = _build_app(database_url, service)
        now = datetime.now(timezone.utc)
        _seed_widget_events(
            database_url,
            {
                "ws-recent": [("widget_opened", now - timedelta(days=1))],
                "ws-old": [
                    ("widget_opened", now - timedelta(days=30)),
                    ("message_sent", now - timedelta(days=30)),
                ],
            },
        )
        headers = _bearer(service, user_id="analyst-1", organization_id=ORG_ID)
        period_start = now - timedelta(days=45)
        period_end = now - timedelta(days=8)
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await client.get(
                    f"/agents/{AGENT_ID}/widget-analytics",
                    params={
                        "period_start": period_start.isoformat(),
                        "period_end": period_end.isoformat(),
                    },
                    headers=headers,
                )
                assert response.status_code == 200, response.text
                payload = response.json()
                assert payload["total_sessions"] == 1
                assert payload["total_events"] == 2
                assert payload["event_counts"] == {"widget_opened": 1, "message_sent": 1}
                start = datetime.fromisoformat(payload["period_start"].replace("Z", "+00:00"))
                end = datetime.fromisoformat(payload["period_end"].replace("Z", "+00:00"))
                assert start == period_start
                assert end == period_end
        finally:
            await close_async_engine()

    asyncio.run(run())


def test_widget_analytics_cross_org_agent_is_404(postgres_database_url_factory) -> None:
    """Agents are seeded under ORG_ID — an OTHER_ORG_ID analyst must get 404."""

    async def run() -> None:
        database_url = postgres_database_url_factory()
        service = _build_auth_service()
        app = _build_app(database_url, service)
        headers = _bearer(service, user_id="outsider-1", organization_id=OTHER_ORG_ID)
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await client.get(
                    f"/agents/{AGENT_ID}/widget-analytics", headers=headers
                )
                assert response.status_code == 404
        finally:
            await close_async_engine()

    asyncio.run(run())


def test_widget_analytics_rejects_unauthenticated_request(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        database_url = postgres_database_url_factory()
        app = _build_app(database_url, _build_auth_service())
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await client.get(f"/agents/{AGENT_ID}/widget-analytics")
                assert response.status_code == 401
        finally:
            await close_async_engine()

    asyncio.run(run())


# ── POST /public/widget/sessions/{conversation_id}/events ────────────────────


def test_ingest_widget_events_persists_batch_and_returns_202(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        database_url = postgres_database_url_factory()
        app = _build_app(database_url)
        pk = make_widget_publishable_key(
            database_url, agent_id=AGENT_ID, organization_id=ORG_ID
        )
        occurred_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            session_payload = await _create_widget_session(client, pk)
            conversation_id = session_payload["conversation_id"]
            response = await client.post(
                f"/public/widget/sessions/{conversation_id}/events",
                json={
                    "events": [
                        {"event_type": "widget_opened"},
                        {
                            "event_type": "cta_clicked",
                            "event_data": {"button": "buy"},
                            "occurred_at": occurred_at.isoformat(),
                        },
                    ]
                },
                headers=_widget_headers(session_payload["session_token"]),
            )
            assert response.status_code == 202

        session_factory = build_session_factory(database_url)
        with session_factory() as db:
            records = (
                db.execute(
                    select(WidgetEventRecord).where(
                        WidgetEventRecord.conversation_id == conversation_id
                    )
                )
                .scalars()
                .all()
            )
        assert {record.event_type for record in records} == {"widget_opened", "cta_clicked"}
        for record in records:
            assert record.organization_id == ORG_ID
            assert record.agent_id == AGENT_ID
            assert record.session_id  # linked to the widget session row
        cta = next(record for record in records if record.event_type == "cta_clicked")
        assert cta.event_data == {"button": "buy"}
        assert cta.occurred_at == occurred_at

    asyncio.run(run())


def test_ingest_widget_events_unknown_conversation_is_404(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        app = _build_app(postgres_database_url_factory())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/public/widget/sessions/no-such-conversation/events",
                json={"events": [{"event_type": "widget_opened"}]},
            )
            assert response.status_code == 404

    asyncio.run(run())


def test_ingest_widget_events_requires_session_token(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        database_url = postgres_database_url_factory()
        app = _build_app(database_url)
        pk = make_widget_publishable_key(
            database_url, agent_id=AGENT_ID, organization_id=ORG_ID
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            session_payload = await _create_widget_session(client, pk)
            response = await client.post(
                f"/public/widget/sessions/{session_payload['conversation_id']}/events",
                json={"events": [{"event_type": "widget_opened"}]},
            )
            assert response.status_code == 401

    asyncio.run(run())


def test_ingest_widget_events_swallows_write_failure_with_202(
    postgres_database_url_factory,
) -> None:
    """Best-effort contract: a broken events table must not break the widget."""

    async def run() -> None:
        database_url = postgres_database_url_factory()
        app = _build_app(database_url)
        pk = make_widget_publishable_key(
            database_url, agent_id=AGENT_ID, organization_id=ORG_ID
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            session_payload = await _create_widget_session(client, pk)
            # Break ONLY the event write path; the widget-session lookup that
            # precedes it must keep working.
            session_factory = build_session_factory(database_url)
            with session_factory.begin() as db:
                db.execute(text("DROP TABLE widget_events CASCADE"))
            response = await client.post(
                f"/public/widget/sessions/{session_payload['conversation_id']}/events",
                json={"events": [{"event_type": "widget_opened"}]},
                headers=_widget_headers(session_payload["session_token"]),
            )
            assert response.status_code == 202

    asyncio.run(run())


# ── Ingest → analytics integration ───────────────────────────────────────────


def test_ingested_events_surface_in_widget_analytics_summary(
    postgres_database_url_factory,
) -> None:
    """End-to-end: rows written by the public ingest route carry the denormalised
    org/agent columns the async aggregation route filters on."""

    async def run() -> None:
        database_url = postgres_database_url_factory()
        service = _build_auth_service()
        app = _build_app(database_url, service)
        pk = make_widget_publishable_key(
            database_url, agent_id=AGENT_ID, organization_id=ORG_ID
        )
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                session_payload = await _create_widget_session(client, pk)
                ingest = await client.post(
                    f"/public/widget/sessions/{session_payload['conversation_id']}/events",
                    json={
                        "events": [
                            {"event_type": "widget_opened"},
                            {"event_type": "message_sent"},
                        ]
                    },
                    headers=_widget_headers(session_payload["session_token"]),
                )
                assert ingest.status_code == 202

                headers = _bearer(service, user_id="analyst-1", organization_id=ORG_ID)
                summary = await client.get(
                    f"/agents/{AGENT_ID}/widget-analytics", headers=headers
                )
                assert summary.status_code == 200, summary.text
                payload = summary.json()
                assert payload["total_sessions"] == 1
                assert payload["total_events"] == 2
                assert payload["event_counts"] == {"widget_opened": 1, "message_sent": 1}
        finally:
            await close_async_engine()

    asyncio.run(run())
