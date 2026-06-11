"""Tier 3: single-step create-connection-and-ingest-spec.

The ``/api/tools/import/openapi`` route already accepts an optional
``connection_id``; when omitted it creates a new connection from the
spec's metadata. This test file pins the four production-grade
guarantees added by the Tier 3 work:

1. ``auth_type="auto"`` → first detected scheme (oauth2 > bearer >
   api_key > basic > none) drives the new connection's auth_type.
2. Transactional rollback: if tool-definition writes fail AFTER the
   connection was newly created in this call, the connection is
   deleted so callers don't see a half-set-up integration.
3. Existing-connection ingest is NOT rolled back on failure (the
   caller-owned connection stays alive).
4. The route surfaces ``detected_auth_schemes`` so the UI can confirm
   what was detected vs what was saved.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI

from ruhu.db import build_session_factory
from ruhu.db_models import APIConnectionRecord, ToolDefinitionRecord
from ruhu.runtime_config import RuntimeSettings
from ruhu.tools.ingestion import (
    OpenAPIToolIngestionService,
    auto_select_auth_type,
    DetectedAuthScheme,
)
from ruhu.tools.management import (
    APIConnectionStore,
    CredentialCipher,
    ToolAgentAssignmentStore,
    ToolDefinitionStore,
)
from ruhu.tools_api import install_tools_router


_OAUTH_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Demo OAuth API", "version": "1.0"},
    "servers": [{"url": "https://demo.example.com"}],
    "components": {
        "securitySchemes": {
            "OAuth2": {
                "type": "oauth2",
                "flows": {
                    "authorizationCode": {
                        "authorizationUrl": "https://demo.example.com/auth",
                        "tokenUrl": "https://demo.example.com/token",
                        "scopes": {"read": "Read access"},
                    }
                },
            }
        }
    },
    "paths": {
        "/widgets": {
            "get": {
                "operationId": "listWidgets",
                "summary": "List widgets",
                "responses": {"200": {"description": "ok"}},
            }
        }
    },
}


_BEARER_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Demo Bearer API", "version": "1.0"},
    "servers": [{"url": "https://bearer.example.com"}],
    "components": {
        "securitySchemes": {
            "Bearer": {"type": "http", "scheme": "bearer"},
        }
    },
    "paths": {
        "/things": {
            "get": {
                "operationId": "listThings",
                "summary": "List things",
                "responses": {"200": {"description": "ok"}},
            }
        }
    },
}


# ── auto_select_auth_type: pure function ────────────────────────────────


def test_auto_select_prefers_oauth2_over_bearer() -> None:
    """OAuth2 wins because it's the only flow with automatic refresh —
    everything else makes the user paste a static secret."""
    detected = [
        DetectedAuthScheme(name="A", auth_type="bearer_token"),
        DetectedAuthScheme(name="B", auth_type="oauth2"),
    ]
    assert auto_select_auth_type(detected) == "oauth2"


def test_auto_select_falls_back_through_preference_chain() -> None:
    assert auto_select_auth_type([
        DetectedAuthScheme(name="A", auth_type="basic"),
        DetectedAuthScheme(name="B", auth_type="api_key"),
    ]) == "api_key"
    assert auto_select_auth_type([
        DetectedAuthScheme(name="A", auth_type="basic"),
    ]) == "basic"


def test_auto_select_returns_none_on_empty_or_unsupported() -> None:
    assert auto_select_auth_type([]) == "none"
    assert auto_select_auth_type([
        DetectedAuthScheme(name="A", auth_type="openid_connect"),
    ]) == "none"


# ── ingest(auth_type="auto"): connection takes detected type ────────────


def test_ingest_auto_creates_oauth2_connection_from_spec(
    postgres_database_url_factory, credential_cipher
) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    service = OpenAPIToolIngestionService(
        connection_store=APIConnectionStore(sf, blob_cipher=credential_cipher),
        definition_store=ToolDefinitionStore(sf),
    )

    result = service.ingest(
        organization_id="org-A",
        spec=_OAUTH_SPEC,
        display_name="Demo OAuth",
        provider="demo_oauth",
        auth_type="auto",
    )

    with sf() as session:
        conn = session.get(APIConnectionRecord, result.connection_id)
        assert conn is not None
        assert conn.auth_type == "oauth2"  # detected, not the default "none"
        # base_url defaulted to the spec's first server URL.
        assert conn.base_url == "https://demo.example.com"


def test_ingest_auto_falls_back_to_none_for_public_spec(
    postgres_database_url_factory, credential_cipher
) -> None:
    """A spec without securitySchemes → connection created with auth_type="none".
    No error, no exception — public APIs are a legitimate case."""
    sf = build_session_factory(postgres_database_url_factory())
    service = OpenAPIToolIngestionService(
        connection_store=APIConnectionStore(sf, blob_cipher=credential_cipher),
        definition_store=ToolDefinitionStore(sf),
    )
    public_spec = {
        "openapi": "3.0.0",
        "info": {"title": "Public", "version": "1.0"},
        "servers": [{"url": "https://public.example.com"}],
        "paths": {
            "/foo": {
                "get": {
                    "operationId": "getFoo",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }

    result = service.ingest(
        organization_id="org-A",
        spec=public_spec,
        display_name="Public API",
        provider="public",
        auth_type="auto",
    )

    with sf() as session:
        conn = session.get(APIConnectionRecord, result.connection_id)
        assert conn is not None
        assert conn.auth_type == "none"
    assert result.detected_auth_schemes == []


def test_ingest_auto_picks_bearer_when_no_oauth(
    postgres_database_url_factory, credential_cipher
) -> None:
    sf = build_session_factory(postgres_database_url_factory())
    service = OpenAPIToolIngestionService(
        connection_store=APIConnectionStore(sf, blob_cipher=credential_cipher),
        definition_store=ToolDefinitionStore(sf),
    )
    result = service.ingest(
        organization_id="org-A",
        spec=_BEARER_SPEC,
        display_name="Bearer API",
        provider="bearer",
        auth_type="auto",
    )
    with sf() as session:
        conn = session.get(APIConnectionRecord, result.connection_id)
        assert conn is not None
        assert conn.auth_type == "bearer_token"


def test_ingest_explicit_auth_type_overrides_auto(
    postgres_database_url_factory, credential_cipher
) -> None:
    """An explicit auth_type wins over what the spec claims — sometimes
    customers know the spec is wrong / out of date."""
    sf = build_session_factory(postgres_database_url_factory())
    service = OpenAPIToolIngestionService(
        connection_store=APIConnectionStore(sf, blob_cipher=credential_cipher),
        definition_store=ToolDefinitionStore(sf),
    )
    # Spec says OAuth2, but caller forces api_key.
    result = service.ingest(
        organization_id="org-A",
        spec=_OAUTH_SPEC,
        display_name="Override Test",
        provider="x",
        auth_type="api_key",
    )
    with sf() as session:
        conn = session.get(APIConnectionRecord, result.connection_id)
        assert conn is not None
        assert conn.auth_type == "api_key"
    # Detection still surfaces what the spec said, so the UI can warn.
    assert len(result.detected_auth_schemes) == 1
    assert result.detected_auth_schemes[0].auth_type == "oauth2"


# ── Transactional rollback ──────────────────────────────────────────────


def test_ingest_rolls_back_new_connection_on_tool_write_failure(
    postgres_database_url_factory, credential_cipher
) -> None:
    """If the connection was created in THIS call and any tool write
    fails, the connection is deleted — half-set-up integrations are
    worse than no integration."""
    sf = build_session_factory(postgres_database_url_factory())
    connection_store = APIConnectionStore(sf, blob_cipher=credential_cipher)
    definition_store = ToolDefinitionStore(sf)
    service = OpenAPIToolIngestionService(
        connection_store=connection_store,
        definition_store=definition_store,
    )

    # Force tool creation to fail after the connection is committed.
    original_create = definition_store.create

    def _failing_create(*args, **kwargs):
        raise RuntimeError("simulated DB outage during tool write")

    definition_store.create = _failing_create  # type: ignore[method-assign]

    try:
        try:
            service.ingest(
                organization_id="org-A",
                spec=_OAUTH_SPEC,
                display_name="Rollback Test",
                provider="x",
                auth_type="auto",
            )
        except RuntimeError:
            pass  # expected — we want to inspect post-rollback state
    finally:
        definition_store.create = original_create  # type: ignore[method-assign]

    # The connection should NOT exist after rollback.
    with sf() as session:
        rows = session.scalars(
            APIConnectionRecord.__table__.select().where(
                APIConnectionRecord.organization_id == "org-A"
            )
        ).all()
        assert rows == [], "rollback failed: orphan connection survived"


def test_ingest_does_not_roll_back_caller_provided_connection_on_failure(
    postgres_database_url_factory, credential_cipher
) -> None:
    """When the caller passed an existing connection_id, that connection
    is theirs — deleting it on a tool-write failure would destroy
    unrelated state."""
    sf = build_session_factory(postgres_database_url_factory())
    connection_store = APIConnectionStore(sf, blob_cipher=credential_cipher)
    definition_store = ToolDefinitionStore(sf)
    service = OpenAPIToolIngestionService(
        connection_store=connection_store,
        definition_store=definition_store,
    )

    # Pre-create the connection.
    pre_existing = connection_store.create(
        organization_id="org-A",
        display_name="Pre-existing",
        provider="x",
        auth_type="oauth2",
    )

    original_create = definition_store.create

    def _failing_create(*args, **kwargs):
        raise RuntimeError("simulated DB outage")

    definition_store.create = _failing_create  # type: ignore[method-assign]

    try:
        try:
            service.ingest(
                organization_id="org-A",
                spec=_OAUTH_SPEC,
                connection_id=pre_existing.connection_id,
            )
        except RuntimeError:
            pass
    finally:
        definition_store.create = original_create  # type: ignore[method-assign]

    # Pre-existing connection must survive.
    with sf() as session:
        survived = session.get(APIConnectionRecord, pre_existing.connection_id)
        assert survived is not None
        assert survived.display_name == "Pre-existing"


# ── Route: detected_auth_schemes surfaces in response ───────────────────


def _build_app(*, postgres_database_url_factory, credential_cipher, monkeypatch):
    sf = build_session_factory(postgres_database_url_factory())
    monkeypatch.setattr(
        "ruhu.tools_api.require_authenticated_context",
        lambda _request: SimpleNamespace(
            principal=SimpleNamespace(
                organization=SimpleNamespace(organization_id="org_a"),
            )
        ),
    )
    legacy = CredentialCipher(Fernet.generate_key())
    connection_store = APIConnectionStore(sf, blob_cipher=credential_cipher, legacy_cipher=legacy)
    definition_store = ToolDefinitionStore(sf)
    assignment_store = ToolAgentAssignmentStore(sf)

    app = FastAPI()
    install_tools_router(
        app,
        connection_store=connection_store,
        definition_store=definition_store,
        assignment_store=assignment_store,
        oauth_manager=None,
        settings=RuntimeSettings(frontend_url="https://app.example.com"),
    )
    return app, connection_store


def test_route_surfaces_detected_auth_schemes_with_auto(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    async def run() -> None:
        app, _ = _build_app(
            postgres_database_url_factory=postgres_database_url_factory,
            credential_cipher=credential_cipher,
            monkeypatch=monkeypatch,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/api/tools/import/openapi",
                json={
                    "openapi_spec": _OAUTH_SPEC,
                    "display_name": "Demo OAuth",
                    "provider": "demo",
                    "auth_type": "auto",
                },
            )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["connection_id"]
        assert len(body["created_tool_ids"]) == 1
        assert len(body["detected_auth_schemes"]) == 1
        scheme = body["detected_auth_schemes"][0]
        assert scheme["auth_type"] == "oauth2"
        assert scheme["authorization_url"] == "https://demo.example.com/auth"
        assert scheme["token_url"] == "https://demo.example.com/token"
        assert scheme["scopes"] == ["read"]

    asyncio.run(run())


def test_route_combined_create_uses_auto_auth_type(
    postgres_database_url_factory, credential_cipher, monkeypatch
) -> None:
    """End-to-end: posting a single body with auth_type=auto creates a
    connection whose stored auth_type matches the detected scheme."""

    async def run() -> None:
        app, store = _build_app(
            postgres_database_url_factory=postgres_database_url_factory,
            credential_cipher=credential_cipher,
            monkeypatch=monkeypatch,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/api/tools/import/openapi",
                json={
                    "openapi_spec": _BEARER_SPEC,
                    "display_name": "Bearer demo",
                    "provider": "bearer-demo",
                    "auth_type": "auto",
                },
            )
        assert response.status_code == 201
        body = response.json()
        connection_id = body["connection_id"]

        record = store.get(connection_id)
        assert record is not None
        assert record.auth_type == "bearer_token"

    asyncio.run(run())
