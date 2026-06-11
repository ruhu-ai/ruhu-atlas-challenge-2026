"""Tier 2: connection revoke flow.

Two-layer coverage:

1. **Helpers** (``_mark_connection_revoked``, ``_post_revoke_request``,
   ``OAuthFlowManager.revoke_connection``) — assert that local cleanup
   always succeeds, the provider is notified when a revoke URL is
   configured, and provider failures don't block local cleanup.
2. **Route** (``POST /api/tools/connections/{id}/revoke``) — assert
   tenant isolation, non-OAuth rejection, 404 on missing connection,
   503 when oauth manager not configured, and the happy path.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI

from ruhu.db import build_session_factory
from ruhu.db_models import APIConnectionRecord
from ruhu.runtime_config import RuntimeSettings
from ruhu.tools import oauth as oauth_module
from ruhu.tools.management import (
    APIConnectionStore,
    CredentialCipher,
    ToolAgentAssignmentStore,
    ToolDefinitionStore,
)
from ruhu.tools.oauth import (
    OAuthFlowManager,
    _mark_connection_revoked,
    _post_revoke_request,
)
from ruhu.tools.oauth_providers import OAUTH_PROVIDERS, OAuthProviderConfig
from ruhu.tools_api import install_tools_router


# ── Helper-level: _mark_connection_revoked ──────────────────────────────


@pytest.fixture
def seed_oauth_connection(postgres_database_url_factory, credential_cipher):
    """Seed an OAuth connection with tokens and a future expiry."""

    def _seed(*, provider: str = "google_calendar", with_tokens: bool = True):
        url = postgres_database_url_factory()
        sf = build_session_factory(url)
        store = APIConnectionStore(sf, blob_cipher=credential_cipher)
        oauth_token = (
            {
                "access_token": "atk-123",
                "refresh_token": "rtk-456",
                "expires_in": 3600,
            }
            if with_tokens
            else None
        )
        record = store.create(
            organization_id="org-A",
            display_name=provider,
            provider=provider,
            auth_type="oauth2",
            oauth_token=oauth_token,
        )
        with sf.begin() as session:
            row = session.get(APIConnectionRecord, record.connection_id)
            assert row is not None
            row.token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        return sf, record

    return _seed


def test_mark_connection_revoked_clears_tokens_and_sets_status(
    seed_oauth_connection,
) -> None:
    sf, record = seed_oauth_connection()

    _mark_connection_revoked(
        sf,
        connection_id=record.connection_id,
        organization_id=record.organization_id,
    )

    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.status == "revoked"
        assert after.oauth_token_json is None
        assert after.oauth_token_ct is None
        assert after.token_expires_at is None
        assert after.error_message is None


def test_mark_connection_revoked_is_tenant_scoped(seed_oauth_connection) -> None:
    """Calling with the wrong organization_id is a silent no-op (caller
    handles 404). The local row must remain untouched so a foreign-org
    request can't clobber another tenant's tokens."""
    sf, record = seed_oauth_connection()

    _mark_connection_revoked(
        sf,
        connection_id=record.connection_id,
        organization_id="org-WRONG",
    )

    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.status != "revoked"
        assert after.oauth_token_json is not None


# ── _post_revoke_request: HTTP behaviour ────────────────────────────────


def test_post_revoke_request_posts_token_to_revoke_url(monkeypatch) -> None:
    """RFC 7009 §2.1 — token in form body, POST."""
    captured: dict = {}

    async def _mock_post(self, url, *, data, headers):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        request = httpx.Request("POST", url)
        return httpx.Response(200, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", _mock_post)

    asyncio.run(
        _post_revoke_request(
            revoke_url="https://oauth2.googleapis.com/revoke",
            token="atk-123",
        )
    )

    assert captured["url"] == "https://oauth2.googleapis.com/revoke"
    assert captured["data"] == {"token": "atk-123"}


def test_post_revoke_request_raises_on_non_2xx(monkeypatch) -> None:
    """Non-2xx must propagate so the caller can log it. RFC 7009 says
    servers SHOULD return 200 even for unrecognised tokens, so a non-2xx
    is a real failure (e.g., 503 Service Unavailable)."""

    async def _mock_post(self, url, *, data, headers):
        request = httpx.Request("POST", url)
        return httpx.Response(503, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", _mock_post)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(
            _post_revoke_request(
                revoke_url="https://oauth2.googleapis.com/revoke",
                token="atk-123",
            )
        )


# ── OAuthFlowManager.revoke_connection: orchestration ──────────────────


def _make_flow_manager(sf) -> OAuthFlowManager:
    return OAuthFlowManager(
        session_factory=sf,
        cipher=CredentialCipher(Fernet.generate_key()),
        redirect_base_url="https://app.example.com",
    )


def test_revoke_connection_calls_provider_revoke_for_supported_providers(
    seed_oauth_connection, monkeypatch
) -> None:
    """Google has a stable RFC 7009 revoke URL — verify that the manager
    POSTs both access and refresh tokens to it."""
    sf, record = seed_oauth_connection(provider="google_calendar")
    manager = _make_flow_manager(sf)

    posted_tokens: list[str] = []

    async def _mock_post_revoke(*, revoke_url, token):
        assert revoke_url == "https://oauth2.googleapis.com/revoke"
        posted_tokens.append(token)

    monkeypatch.setattr(oauth_module, "_post_revoke_request", _mock_post_revoke)

    result = asyncio.run(
        manager.revoke_connection(
            connection_id=record.connection_id,
            organization_id=record.organization_id,
        )
    )

    assert result == {"provider_revoke_attempted": True, "provider_revoke_ok": True}
    # Both access_token AND refresh_token are revoked separately.
    assert sorted(posted_tokens) == ["atk-123", "rtk-456"]

    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.status == "revoked"
        assert after.oauth_token_json is None


def test_revoke_connection_skips_provider_call_when_no_revoke_url(
    seed_oauth_connection, monkeypatch
) -> None:
    """HubSpot has no public RFC 7009 endpoint — the local cleanup must
    still happen, but no provider POST is attempted."""
    sf, record = seed_oauth_connection(provider="hubspot")
    manager = _make_flow_manager(sf)

    revoke_calls: list[str] = []

    async def _mock_post_revoke(*, revoke_url, token):
        revoke_calls.append(token)

    monkeypatch.setattr(oauth_module, "_post_revoke_request", _mock_post_revoke)

    result = asyncio.run(
        manager.revoke_connection(
            connection_id=record.connection_id,
            organization_id=record.organization_id,
        )
    )

    assert result == {"provider_revoke_attempted": False, "provider_revoke_ok": False}
    assert revoke_calls == []
    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.status == "revoked"  # local cleanup still happened


def test_revoke_connection_local_cleanup_proceeds_when_provider_fails(
    seed_oauth_connection, monkeypatch
) -> None:
    """An attacker who can poison the provider revoke endpoint must not
    be able to leave us with a still-valid local token. Provider failure
    is logged but local state is always cleared."""
    sf, record = seed_oauth_connection(provider="google_calendar")
    manager = _make_flow_manager(sf)

    async def _failing_post(*, revoke_url, token):
        raise httpx.ConnectError("provider unreachable")

    monkeypatch.setattr(oauth_module, "_post_revoke_request", _failing_post)

    result = asyncio.run(
        manager.revoke_connection(
            connection_id=record.connection_id,
            organization_id=record.organization_id,
        )
    )

    assert result == {"provider_revoke_attempted": True, "provider_revoke_ok": False}
    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.status == "revoked"
        assert after.oauth_token_json is None  # tokens cleared


def test_revoke_connection_raises_on_unknown_connection(
    postgres_database_url_factory,
) -> None:
    """A non-existent connection_id surfaces as ValueError — the route
    handler converts that into a 404."""
    sf = build_session_factory(postgres_database_url_factory())
    manager = _make_flow_manager(sf)

    with pytest.raises(ValueError, match="not found"):
        asyncio.run(
            manager.revoke_connection(
                connection_id="conn-nope",
                organization_id="org-A",
            )
        )


def test_revoke_connection_no_tokens_skips_provider_call(
    seed_oauth_connection, monkeypatch
) -> None:
    """A half-set-up connection (OAuth started but exchange never
    completed) has no tokens — there's nothing to send to the provider,
    but we still flip the local status."""
    sf, record = seed_oauth_connection(provider="google_calendar", with_tokens=False)
    manager = _make_flow_manager(sf)

    revoke_calls: list[str] = []

    async def _mock_post_revoke(*, revoke_url, token):
        revoke_calls.append(token)

    monkeypatch.setattr(oauth_module, "_post_revoke_request", _mock_post_revoke)

    result = asyncio.run(
        manager.revoke_connection(
            connection_id=record.connection_id,
            organization_id=record.organization_id,
        )
    )

    assert result == {"provider_revoke_attempted": False, "provider_revoke_ok": False}
    assert revoke_calls == []
    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.status == "revoked"


# ── Route: POST /api/tools/connections/{id}/revoke ──────────────────────


def _build_revoke_app(
    *, postgres_database_url_factory, credential_cipher, monkeypatch,
    organization_id: str, install_oauth: bool = True,
):
    session_factory = build_session_factory(postgres_database_url_factory())

    fake_context = SimpleNamespace(
        principal=SimpleNamespace(
            organization=SimpleNamespace(organization_id=organization_id),
        )
    )
    monkeypatch.setattr(
        "ruhu.tools_api.require_authenticated_context", lambda _request: fake_context
    )

    legacy_cipher = CredentialCipher(Fernet.generate_key())
    connection_store = APIConnectionStore(
        session_factory,
        blob_cipher=credential_cipher,
        legacy_cipher=legacy_cipher,
    )
    definition_store = ToolDefinitionStore(session_factory)
    assignment_store = ToolAgentAssignmentStore(session_factory)
    oauth_manager = (
        OAuthFlowManager(
            session_factory=session_factory,
            cipher=legacy_cipher,
            redirect_base_url="https://app.example.com",
            blob_cipher=credential_cipher,
        )
        if install_oauth
        else None
    )

    app = FastAPI()
    install_tools_router(
        app,
        connection_store=connection_store,
        definition_store=definition_store,
        assignment_store=assignment_store,
        oauth_manager=oauth_manager,
        settings=RuntimeSettings(frontend_url="https://app.example.com"),
    )
    return app, connection_store


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def test_revoke_route_happy_path(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    async def run() -> None:
        app, store = _build_revoke_app(
            postgres_database_url_factory=postgres_database_url_factory,
            credential_cipher=credential_cipher,
            monkeypatch=monkeypatch,
            organization_id="org_a",
        )
        record = store.create(
            organization_id="org_a",
            display_name="GoogleCal",
            provider="google_calendar",
            auth_type="oauth2",
            oauth_token={"access_token": "atk", "refresh_token": "rtk"},
        )

        async def _mock_post_revoke(*, revoke_url, token):
            return None

        monkeypatch.setattr(oauth_module, "_post_revoke_request", _mock_post_revoke)

        async with _client(app) as client:
            response = await client.post(
                f"/api/tools/connections/{record.connection_id}/revoke"
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["connection_id"] == record.connection_id
        assert body["status"] == "revoked"
        assert body["provider_revoke_attempted"] is True
        assert body["provider_revoke_ok"] is True

    asyncio.run(run())


def test_revoke_route_returns_404_on_cross_tenant_connection(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    """A connection belonging to a different org must surface as 404,
    not 200 — preventing tenant-A from revoking tenant-B's tokens."""

    async def run() -> None:
        app, store = _build_revoke_app(
            postgres_database_url_factory=postgres_database_url_factory,
            credential_cipher=credential_cipher,
            monkeypatch=monkeypatch,
            organization_id="org_a",
        )
        # Connection belongs to org_b, not the authed org_a.
        record = store.create(
            organization_id="org_b",
            display_name="GoogleCal",
            provider="google_calendar",
            auth_type="oauth2",
            oauth_token={"access_token": "atk"},
        )
        async with _client(app) as client:
            response = await client.post(
                f"/api/tools/connections/{record.connection_id}/revoke"
            )
        assert response.status_code == 404

    asyncio.run(run())


def test_revoke_route_returns_400_on_non_oauth_connection(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    """An API-key connection can't be revoked via OAuth flow — the route
    rejects with 400 rather than mis-applying the OAuth path."""

    async def run() -> None:
        app, store = _build_revoke_app(
            postgres_database_url_factory=postgres_database_url_factory,
            credential_cipher=credential_cipher,
            monkeypatch=monkeypatch,
            organization_id="org_a",
        )
        record = store.create(
            organization_id="org_a",
            display_name="API key conn",
            provider="custom",
            auth_type="api_key",
            credentials_plain={"api_key": "secret"},
        )
        async with _client(app) as client:
            response = await client.post(
                f"/api/tools/connections/{record.connection_id}/revoke"
            )
        assert response.status_code == 400
        assert "OAuth" in response.json()["detail"]

    asyncio.run(run())


def test_revoke_route_returns_503_when_oauth_manager_unconfigured(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    async def run() -> None:
        app, store = _build_revoke_app(
            postgres_database_url_factory=postgres_database_url_factory,
            credential_cipher=credential_cipher,
            monkeypatch=monkeypatch,
            organization_id="org_a",
            install_oauth=False,
        )
        record = store.create(
            organization_id="org_a",
            display_name="GoogleCal",
            provider="google_calendar",
            auth_type="oauth2",
            oauth_token={"access_token": "atk"},
        )
        async with _client(app) as client:
            response = await client.post(
                f"/api/tools/connections/{record.connection_id}/revoke"
            )
        assert response.status_code == 503

    asyncio.run(run())


def test_revoke_route_returns_404_for_unknown_connection(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    async def run() -> None:
        app, _store = _build_revoke_app(
            postgres_database_url_factory=postgres_database_url_factory,
            credential_cipher=credential_cipher,
            monkeypatch=monkeypatch,
            organization_id="org_a",
        )
        async with _client(app) as client:
            response = await client.post("/api/tools/connections/conn-nope/revoke")
        assert response.status_code == 404

    asyncio.run(run())
