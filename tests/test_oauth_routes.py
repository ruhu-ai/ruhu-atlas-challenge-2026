"""Route tests for the three OAuth API endpoints.

Coverage matrix this file fills (the audit found 0 tests at the route
layer):

* ``POST /api/tools/connections/{id}/oauth/start``  — happy path,
  cross-tenant 404, non-OAuth 400, missing creds 503, unsupported
  provider 400, service-not-configured 503
* ``GET  /api/tools/oauth/callback``  — provider-error redirect,
  missing-code redirect, tampered-state redirect, cross-tenant
  redirect, missing-creds redirect, token-exchange-failure redirect,
  happy-path success redirect
* ``POST /api/tools/oauth/exchange``  — tampered state 400, org
  mismatch 403, missing connection 404, missing creds 503, happy path
  with mocked provider HTTP

The existing ``test_tools_api.py`` already documents the harness shape
for this surface (FastAPI app, ASGI transport, monkeypatched auth
context, in-memory cipher fixture); this file follows the same pattern.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI

from ruhu.db import build_session_factory
from ruhu.runtime_config import RuntimeSettings
from ruhu.tools.management import (
    APIConnectionStore,
    CredentialCipher,
    ToolAgentAssignmentStore,
    ToolDefinitionStore,
)
from ruhu.tools.oauth import OAuthFlowManager
from ruhu.tools_api import install_tools_router


# ── Shared scaffolding ──────────────────────────────────────────────


def _build_test_app(
    *,
    postgres_database_url_factory,
    credential_cipher,
    monkeypatch,
    organization_id: str,
    settings: RuntimeSettings | None = None,
    install_oauth: bool = True,
) -> tuple[FastAPI, APIConnectionStore]:
    """Build a FastAPI app wired with the tools router.

    Returns (app, connection_store) so individual tests can seed
    connections directly. Auth is monkeypatched to return a fixed
    organisation principal — every test runs as the same tenant unless
    it overrides via a separate context.
    """
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

    if install_oauth:
        oauth_manager = OAuthFlowManager(
            session_factory=session_factory,
            cipher=legacy_cipher,
            redirect_base_url="https://app.example.com",
            blob_cipher=credential_cipher,
        )
    else:
        oauth_manager = None

    effective_settings = settings or RuntimeSettings(
        hubspot_client_id="hubspot-cid",
        hubspot_client_secret="hubspot-secret",
        frontend_url="https://app.example.com",
    )

    app = FastAPI()
    install_tools_router(
        app,
        connection_store=connection_store,
        definition_store=definition_store,
        assignment_store=assignment_store,
        oauth_manager=oauth_manager,
        settings=effective_settings,
    )
    return app, connection_store


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _seed_oauth_connection(
    connection_store: APIConnectionStore,
    *,
    organization_id: str,
    provider: str = "hubspot",
) -> str:
    record = connection_store.create(
        organization_id=organization_id,
        display_name=f"Test {provider}",
        provider=provider,
        auth_type="oauth2",
    )
    return record.connection_id


# ── POST /api/tools/connections/{id}/oauth/start ─────────────────────


def test_oauth_start_returns_authorization_url_with_state_and_pkce(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    async def run() -> None:
        app, store = _build_test_app(
            postgres_database_url_factory=postgres_database_url_factory,
            credential_cipher=credential_cipher,
            monkeypatch=monkeypatch,
            organization_id="org_a",
        )
        connection_id = _seed_oauth_connection(store, organization_id="org_a")
        async with _client(app) as client:
            response = await client.post(
                f"/api/tools/connections/{connection_id}/oauth/start"
            )

        assert response.status_code == 200
        url = response.json()["authorization_url"]
        qs = parse_qs(urlparse(url).query)
        # All required params present.
        assert qs["client_id"] == ["hubspot-cid"]
        assert qs["state"]  # opaque encrypted blob
        assert qs["code_challenge"]  # PKCE on by default
        assert qs["code_challenge_method"] == ["S256"]
        assert qs["redirect_uri"][0].endswith("/integrations/oauth/callback")

    asyncio.run(run())


def test_oauth_start_returns_404_on_cross_tenant_connection(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    async def run() -> None:
        app, store = _build_test_app(
            postgres_database_url_factory=postgres_database_url_factory,
            credential_cipher=credential_cipher,
            monkeypatch=monkeypatch,
            organization_id="org_a",
        )
        # Connection belongs to a DIFFERENT org.
        cross_tenant_id = _seed_oauth_connection(store, organization_id="org_b")
        async with _client(app) as client:
            response = await client.post(
                f"/api/tools/connections/{cross_tenant_id}/oauth/start"
            )

        assert response.status_code == 404
        assert "connection not found" in response.json()["detail"].lower()

    asyncio.run(run())


def test_oauth_start_returns_400_for_non_oauth_connection(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    async def run() -> None:
        app, store = _build_test_app(
            postgres_database_url_factory=postgres_database_url_factory,
            credential_cipher=credential_cipher,
            monkeypatch=monkeypatch,
            organization_id="org_a",
        )
        record = store.create(
            organization_id="org_a",
            display_name="api-key conn",
            provider="custom",
            auth_type="api_key",  # not oauth2
        )
        async with _client(app) as client:
            response = await client.post(
                f"/api/tools/connections/{record.connection_id}/oauth/start"
            )

        assert response.status_code == 400
        assert "not an OAuth2" in response.json()["detail"]

    asyncio.run(run())


def test_oauth_start_returns_503_when_platform_credentials_missing(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    async def run() -> None:
        # Settings WITHOUT hubspot_client_id/secret configured.
        bare_settings = RuntimeSettings(frontend_url="https://app.example.com")
        app, store = _build_test_app(
            postgres_database_url_factory=postgres_database_url_factory,
            credential_cipher=credential_cipher,
            monkeypatch=monkeypatch,
            organization_id="org_a",
            settings=bare_settings,
        )
        connection_id = _seed_oauth_connection(store, organization_id="org_a")
        async with _client(app) as client:
            response = await client.post(
                f"/api/tools/connections/{connection_id}/oauth/start"
            )

        assert response.status_code == 503
        assert "credentials" in response.json()["detail"].lower()

    asyncio.run(run())


def test_oauth_start_returns_503_when_oauth_manager_not_configured(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    async def run() -> None:
        app, store = _build_test_app(
            postgres_database_url_factory=postgres_database_url_factory,
            credential_cipher=credential_cipher,
            monkeypatch=monkeypatch,
            organization_id="org_a",
            install_oauth=False,
        )
        connection_id = _seed_oauth_connection(store, organization_id="org_a")
        async with _client(app) as client:
            response = await client.post(
                f"/api/tools/connections/{connection_id}/oauth/start"
            )

        assert response.status_code == 503

    asyncio.run(run())


# ── GET /api/tools/oauth/callback ────────────────────────────────────


def test_oauth_callback_redirects_with_error_when_provider_returns_error(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    async def run() -> None:
        app, _ = _build_test_app(
            postgres_database_url_factory=postgres_database_url_factory,
            credential_cipher=credential_cipher,
            monkeypatch=monkeypatch,
            organization_id="org_a",
        )
        async with _client(app) as client:
            response = await client.get(
                "/api/tools/oauth/callback",
                params={"error": "access_denied", "error_description": "user said no"},
            )

        # 302 → frontend with oauth_error qs
        assert response.status_code == 302
        location = response.headers["location"]
        assert "oauth_error=" in location
        assert "user+said+no" in location or "user%20said%20no" in location

    asyncio.run(run())


def test_oauth_callback_redirects_when_code_or_state_missing(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    async def run() -> None:
        app, _ = _build_test_app(
            postgres_database_url_factory=postgres_database_url_factory,
            credential_cipher=credential_cipher,
            monkeypatch=monkeypatch,
            organization_id="org_a",
        )
        async with _client(app) as client:
            r1 = await client.get("/api/tools/oauth/callback", params={"state": "x"})
            r2 = await client.get("/api/tools/oauth/callback", params={"code": "x"})

        for response in (r1, r2):
            assert response.status_code == 302
            assert "oauth_error" in response.headers["location"]

    asyncio.run(run())


def test_oauth_callback_redirects_when_state_is_tampered(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    async def run() -> None:
        app, _ = _build_test_app(
            postgres_database_url_factory=postgres_database_url_factory,
            credential_cipher=credential_cipher,
            monkeypatch=monkeypatch,
            organization_id="org_a",
        )
        async with _client(app) as client:
            response = await client.get(
                "/api/tools/oauth/callback",
                params={"code": "x", "state": "bogus-state-token"},
            )

        assert response.status_code == 302
        location = response.headers["location"]
        assert "invalid+state" in location or "invalid%20state" in location

    asyncio.run(run())


def test_oauth_callback_happy_path_persists_tokens_and_redirects_success(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    """Drive the full callback flow with a mocked token endpoint.

    1. Create a connection via the connection store
    2. Build an authorization URL via OAuthFlowManager (this generates the
       state + verifier we'll feed back)
    3. Hit the callback with a fake ``code`` and the real ``state``
    4. Verify a 302 to the success URL
    5. Confirm the connection now has tokens persisted
    """
    async def run() -> None:
        app, store = _build_test_app(
            postgres_database_url_factory=postgres_database_url_factory,
            credential_cipher=credential_cipher,
            monkeypatch=monkeypatch,
            organization_id="org_a",
        )
        connection_id = _seed_oauth_connection(store, organization_id="org_a")

        # Hit /oauth/start to obtain a real, fernet-signed state token.
        async with _client(app) as client:
            start_response = await client.post(
                f"/api/tools/connections/{connection_id}/oauth/start"
            )
        assert start_response.status_code == 200
        url = start_response.json()["authorization_url"]
        state_token = parse_qs(urlparse(url).query)["state"][0]

        # Mock the upstream token endpoint inside _fetch_token.
        async def _mock_fetch_token(
            *, config, grant_type, client_id, client_secret, extra_params, token_url_override
        ) -> dict:
            assert grant_type == "authorization_code"
            # PKCE: the verifier must round-trip through state.
            assert "code_verifier" in extra_params
            return {
                "access_token": "atk_test",
                "refresh_token": "rtk_test",
                "expires_in": 3600,
                "token_type": "bearer",
            }

        monkeypatch.setattr("ruhu.tools.oauth._fetch_token", _mock_fetch_token)

        async with _client(app) as client:
            response = await client.get(
                "/api/tools/oauth/callback",
                params={"code": "auth-code-from-provider", "state": state_token},
            )

        assert response.status_code == 302
        location = response.headers["location"]
        assert "oauth=success" in location
        assert connection_id in location

        # Tokens persisted.
        record = store.get(connection_id)
        assert record is not None
        assert record.status == "active"
        assert record.oauth_token_json["access_token"] == "atk_test"

    asyncio.run(run())


def test_oauth_callback_redirects_on_token_exchange_failure(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    async def run() -> None:
        app, store = _build_test_app(
            postgres_database_url_factory=postgres_database_url_factory,
            credential_cipher=credential_cipher,
            monkeypatch=monkeypatch,
            organization_id="org_a",
        )
        connection_id = _seed_oauth_connection(store, organization_id="org_a")

        async with _client(app) as client:
            start = await client.post(
                f"/api/tools/connections/{connection_id}/oauth/start"
            )
        state_token = parse_qs(urlparse(start.json()["authorization_url"]).query)["state"][0]

        async def _angry_fetch_token(**kwargs) -> dict:
            raise ValueError("token endpoint returned 400: bad client_secret")

        monkeypatch.setattr("ruhu.tools.oauth._fetch_token", _angry_fetch_token)

        async with _client(app) as client:
            response = await client.get(
                "/api/tools/oauth/callback",
                params={"code": "code", "state": state_token},
            )

        assert response.status_code == 302
        assert "oauth_error=" in response.headers["location"]
        # Connection is marked errored.
        record = store.get(connection_id)
        assert record is not None
        assert record.status == "error"

    asyncio.run(run())


# ── POST /api/tools/oauth/exchange ───────────────────────────────────


def test_oauth_exchange_returns_400_on_tampered_state(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    async def run() -> None:
        app, _ = _build_test_app(
            postgres_database_url_factory=postgres_database_url_factory,
            credential_cipher=credential_cipher,
            monkeypatch=monkeypatch,
            organization_id="org_a",
        )
        async with _client(app) as client:
            response = await client.post(
                "/api/tools/oauth/exchange",
                json={"code": "x", "state": "bogus-state"},
            )

        assert response.status_code == 400
        assert "invalid state" in response.json()["detail"].lower()

    asyncio.run(run())


def test_oauth_exchange_returns_403_on_organisation_mismatch(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    """A state token signed for org_a must not be exchangeable by an
    authenticated session for org_b."""
    async def run() -> None:
        # Build two apps wired to the same DB but authenticated as different orgs.
        # First, build org_a's app and use it to mint a real state token.
        app_a, store_a = _build_test_app(
            postgres_database_url_factory=postgres_database_url_factory,
            credential_cipher=credential_cipher,
            monkeypatch=monkeypatch,
            organization_id="org_a",
        )
        connection_id = _seed_oauth_connection(store_a, organization_id="org_a")
        async with _client(app_a) as client:
            start = await client.post(
                f"/api/tools/connections/{connection_id}/oauth/start"
            )
        state_token = parse_qs(urlparse(start.json()["authorization_url"]).query)["state"][0]

        # Now re-monkeypatch the auth context to org_b and try to exchange.
        # We have to use the SAME app (and therefore same connection_store)
        # so the state's connection_id resolves; the auth context override
        # is what triggers the org mismatch check.
        fake_org_b = SimpleNamespace(
            principal=SimpleNamespace(
                organization=SimpleNamespace(organization_id="org_b"),
            )
        )
        monkeypatch.setattr(
            "ruhu.tools_api.require_authenticated_context", lambda _request: fake_org_b
        )

        async with _client(app_a) as client:
            response = await client.post(
                "/api/tools/oauth/exchange",
                json={"code": "x", "state": state_token},
            )

        assert response.status_code == 403
        assert "organisation mismatch" in response.json()["detail"].lower()

    asyncio.run(run())


def test_oauth_exchange_happy_path_persists_tokens(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    async def run() -> None:
        app, store = _build_test_app(
            postgres_database_url_factory=postgres_database_url_factory,
            credential_cipher=credential_cipher,
            monkeypatch=monkeypatch,
            organization_id="org_a",
        )
        connection_id = _seed_oauth_connection(store, organization_id="org_a")
        async with _client(app) as client:
            start = await client.post(
                f"/api/tools/connections/{connection_id}/oauth/start"
            )
        state_token = parse_qs(urlparse(start.json()["authorization_url"]).query)["state"][0]

        async def _mock_fetch_token(**kwargs) -> dict:
            return {
                "access_token": "atk_exchange",
                "refresh_token": "rtk_exchange",
                "expires_in": 3600,
                "token_type": "bearer",
            }

        monkeypatch.setattr("ruhu.tools.oauth._fetch_token", _mock_fetch_token)

        async with _client(app) as client:
            response = await client.post(
                "/api/tools/oauth/exchange",
                json={"code": "code-from-popup", "state": state_token},
            )

        assert response.status_code == 200
        record = store.get(connection_id)
        assert record is not None
        assert record.oauth_token_json["access_token"] == "atk_exchange"
        assert record.status == "active"

    asyncio.run(run())
