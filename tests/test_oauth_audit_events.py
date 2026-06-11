"""Tier 3: OAuth lifecycle audit events.

The OAuth flow handles consent for third-party API access on behalf of
customers. Three transitions are security-relevant for compliance and
incident response:

* ``auth.oauth.connection_authorized`` — user consented; tokens stored.
* ``auth.oauth.connection_requires_reauth`` — provider rejected the
  refresh token (consent withdrawn or rotated).
* ``auth.oauth.connection_revoked`` — user revoked the integration.

Routine machine refreshes are intentionally NOT audited — auditing one
event per active connection per ~50 minutes would drown the log in
non-actionable activity. Those continue to appear in stdlib logs.

These tests pin:
1. Each transition emits exactly one event with the right type, org, and
   resource id (``connection_id``).
2. ``audit_router=None`` is a clean no-op (no crash, no spurious calls).
3. An audit-pipeline failure does NOT block the underlying business
   action (revoke must clear local tokens even if audit DB is down).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet

from ruhu.audit.events import (
    AUTH_OAUTH_CONNECTION_AUTHORIZED,
    AUTH_OAUTH_CONNECTION_REQUIRES_REAUTH,
    AUTH_OAUTH_CONNECTION_REVOKED,
)
from ruhu.db import build_session_factory
from ruhu.db_models import APIConnectionRecord
from ruhu.tools import oauth as oauth_module
from ruhu.tools.management import APIConnectionStore, CredentialCipher
from ruhu.tools.oauth import OAuthFlowManager, OAuthTokenRefresher


@pytest.fixture
def seed_oauth_connection(postgres_database_url_factory, credential_cipher):
    def _seed(*, provider: str = "google_calendar", status: str = "active"):
        url = postgres_database_url_factory()
        sf = build_session_factory(url)
        store = APIConnectionStore(sf, blob_cipher=credential_cipher)
        record = store.create(
            organization_id="org-A",
            display_name=provider,
            provider=provider,
            auth_type="oauth2",
            oauth_token={
                "access_token": "atk",
                "refresh_token": "rtk",
                "expires_in": 3600,
            },
        )
        with sf.begin() as session:
            row = session.get(APIConnectionRecord, record.connection_id)
            assert row is not None
            row.token_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
            row.status = status
        return sf, record

    return _seed


def _captured_events(audit_router: MagicMock) -> list:
    """Pull AuditEvent objects out of the mocked router's route() calls."""
    return [call.args[0] for call in audit_router.route.call_args_list]


# ── connection_authorized: emitted on successful exchange_code ──────────


def test_exchange_code_emits_connection_authorized_audit_event(
    seed_oauth_connection, monkeypatch
) -> None:
    sf, record = seed_oauth_connection()
    audit_router = MagicMock()
    manager = OAuthFlowManager(
        session_factory=sf,
        cipher=CredentialCipher(Fernet.generate_key()),
        redirect_base_url="https://app.example.com",
        audit_router=audit_router,
    )

    async def _mock_fetch_token(**_kwargs):
        return {"access_token": "new-atk", "refresh_token": "new-rtk", "expires_in": 3600}

    monkeypatch.setattr(oauth_module, "_fetch_token", _mock_fetch_token)

    asyncio.run(
        manager.exchange_code(
            connection_id=record.connection_id,
            organization_id=record.organization_id,
            provider=record.provider,
            code="auth-code",
            client_id="cid",
            client_secret="csecret",
        )
    )

    events = _captured_events(audit_router)
    assert len(events) == 1
    event = events[0]
    assert event.event_type == AUTH_OAUTH_CONNECTION_AUTHORIZED
    assert event.organization_id == record.organization_id
    assert event.resource_type == "oauth_connection"
    assert event.resource_id == record.connection_id
    assert event.detail["provider"] == record.provider
    assert event.outcome == "success"


def test_exchange_code_no_audit_event_when_router_is_none(
    seed_oauth_connection, monkeypatch
) -> None:
    sf, record = seed_oauth_connection()
    manager = OAuthFlowManager(
        session_factory=sf,
        cipher=CredentialCipher(Fernet.generate_key()),
        redirect_base_url="https://app.example.com",
        audit_router=None,
    )

    async def _mock_fetch_token(**_kwargs):
        return {"access_token": "new-atk", "expires_in": 3600}

    monkeypatch.setattr(oauth_module, "_fetch_token", _mock_fetch_token)

    # Simply verify no exception — the call completes and no audit attempt
    # blows up. (Implicit assertion: _emit_oauth_audit's None-guard is hit.)
    asyncio.run(
        manager.exchange_code(
            connection_id=record.connection_id,
            organization_id=record.organization_id,
            provider=record.provider,
            code="auth-code",
            client_id="cid",
            client_secret="csecret",
        )
    )

    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.status == "active"


def test_exchange_code_failure_does_not_emit_authorized_event(
    seed_oauth_connection, monkeypatch
) -> None:
    """A failed token exchange must NOT emit ``connection_authorized``
    — the user did not actually authorize anything yet, the refresh
    token does not exist, and conflating these would mislead auditors
    investigating which integrations are active."""
    sf, record = seed_oauth_connection()
    audit_router = MagicMock()
    manager = OAuthFlowManager(
        session_factory=sf,
        cipher=CredentialCipher(Fernet.generate_key()),
        redirect_base_url="https://app.example.com",
        audit_router=audit_router,
    )

    async def _failing_fetch(**_kwargs):
        raise ValueError("token endpoint returned 400: invalid_request")

    monkeypatch.setattr(oauth_module, "_fetch_token", _failing_fetch)

    with pytest.raises(ValueError):
        asyncio.run(
            manager.exchange_code(
                connection_id=record.connection_id,
                organization_id=record.organization_id,
                provider=record.provider,
                code="auth-code",
                client_id="cid",
                client_secret="csecret",
            )
        )

    events = _captured_events(audit_router)
    assert events == []  # no audit on failure


# ── requires_reauth: emitted on invalid_grant during refresh ────────────


def test_refresh_invalid_grant_emits_requires_reauth_audit(
    seed_oauth_connection, monkeypatch
) -> None:
    sf, record = seed_oauth_connection()
    audit_router = MagicMock()

    async def _invalid_grant(**_kwargs):
        raise ValueError(
            'token endpoint returned 400: {"error":"invalid_grant"}'
        )

    monkeypatch.setattr(oauth_module, "_fetch_token", _invalid_grant)

    refresher = OAuthTokenRefresher(
        sf,
        get_credentials=lambda _provider: ("cid", "csecret"),
        audit_router=audit_router,
    )

    with sf() as session:
        conn = session.get(APIConnectionRecord, record.connection_id)
        assert conn is not None
        session.expunge(conn)

    asyncio.run(refresher._refresh_one(conn))

    events = _captured_events(audit_router)
    assert len(events) == 1
    event = events[0]
    assert event.event_type == AUTH_OAUTH_CONNECTION_REQUIRES_REAUTH
    assert event.organization_id == record.organization_id
    assert event.resource_id == record.connection_id
    assert event.detail["provider"] == record.provider
    assert event.detail["error_kind"] == "invalid_grant"
    assert event.outcome == "failure"


def test_refresh_transient_error_does_not_emit_requires_reauth(
    seed_oauth_connection, monkeypatch
) -> None:
    """Network/5xx failures put the connection in ``error`` (still
    retried) — they are NOT a security-relevant transition and must
    not pollute the audit stream."""
    sf, record = seed_oauth_connection()
    audit_router = MagicMock()

    async def _network_error(**_kwargs):
        raise ValueError("connection reset by peer")

    monkeypatch.setattr(oauth_module, "_fetch_token", _network_error)

    refresher = OAuthTokenRefresher(
        sf,
        get_credentials=lambda _provider: ("cid", "csecret"),
        audit_router=audit_router,
    )

    with sf() as session:
        conn = session.get(APIConnectionRecord, record.connection_id)
        assert conn is not None
        session.expunge(conn)

    asyncio.run(refresher._refresh_one(conn))

    assert _captured_events(audit_router) == []


def test_refresh_success_does_not_emit_audit_event(
    seed_oauth_connection, monkeypatch
) -> None:
    """Routine machine refresh must NOT audit — design choice to keep
    the security log signal-rich."""
    sf, record = seed_oauth_connection()
    audit_router = MagicMock()

    async def _ok_refresh(**_kwargs):
        return {"access_token": "new-atk", "expires_in": 3600}

    monkeypatch.setattr(oauth_module, "_fetch_token", _ok_refresh)

    refresher = OAuthTokenRefresher(
        sf,
        get_credentials=lambda _provider: ("cid", "csecret"),
        audit_router=audit_router,
    )

    with sf() as session:
        conn = session.get(APIConnectionRecord, record.connection_id)
        assert conn is not None
        session.expunge(conn)

    asyncio.run(refresher._refresh_one(conn))

    assert _captured_events(audit_router) == []


# ── connection_revoked: emitted on user-initiated revoke ────────────────


def test_revoke_connection_emits_revoked_audit_event(
    seed_oauth_connection, monkeypatch
) -> None:
    sf, record = seed_oauth_connection(provider="google_calendar")
    audit_router = MagicMock()
    manager = OAuthFlowManager(
        session_factory=sf,
        cipher=CredentialCipher(Fernet.generate_key()),
        redirect_base_url="https://app.example.com",
        audit_router=audit_router,
    )

    async def _mock_post_revoke(*, revoke_url, token):
        return None

    monkeypatch.setattr(oauth_module, "_post_revoke_request", _mock_post_revoke)

    asyncio.run(
        manager.revoke_connection(
            connection_id=record.connection_id,
            organization_id=record.organization_id,
        )
    )

    events = _captured_events(audit_router)
    assert len(events) == 1
    event = events[0]
    assert event.event_type == AUTH_OAUTH_CONNECTION_REVOKED
    assert event.organization_id == record.organization_id
    assert event.resource_id == record.connection_id
    assert event.detail["provider"] == "google_calendar"
    # Provider-call telemetry is captured in the detail so audit
    # consumers can answer "did the provider get notified?".
    assert event.detail["provider_revoke_attempted"] is True
    assert event.detail["provider_revoke_ok"] is True


def test_revoke_audit_failure_does_not_block_local_cleanup(
    seed_oauth_connection, monkeypatch
) -> None:
    """Critical: an audit pipeline outage must not leave us holding
    valid tokens. Local cleanup runs unconditionally; only the audit
    event is lost (and logged via the warning path)."""
    sf, record = seed_oauth_connection(provider="hubspot")  # no revoke URL

    failing_router = MagicMock()
    failing_router.route.side_effect = RuntimeError("audit DB unreachable")

    manager = OAuthFlowManager(
        session_factory=sf,
        cipher=CredentialCipher(Fernet.generate_key()),
        redirect_base_url="https://app.example.com",
        audit_router=failing_router,
    )

    # Should NOT raise — the audit failure is swallowed inside
    # _emit_oauth_audit so the security action (token clearing) wins.
    asyncio.run(
        manager.revoke_connection(
            connection_id=record.connection_id,
            organization_id=record.organization_id,
        )
    )

    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.status == "revoked"
        assert after.oauth_token_json is None
