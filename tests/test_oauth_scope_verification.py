"""Tier 4: OAuth scope verification at exchange + refresh time.

When the user goes through a provider's consent screen they CAN
deselect individual scopes (Slack, Google, GitHub all support this).
The token endpoint then returns a narrower ``scope`` than we asked for.
Without comparing the two sets, the platform doesn't notice the gap
until tools start failing at runtime with cryptic 403s — by which time
the conversation context is already broken.

These tests pin five guarantees:

1. ``compute_scope_status`` returns ``("partial", {missing})`` when
   the granted set is a strict subset of the requested set, ``"complete"``
   when they match, and ``"unknown"`` when the provider omitted the
   ``scope`` field entirely.
2. ``_parse_granted_scopes`` accepts both the RFC 6749 string form
   and the list form some providers return.
3. The state payload from ``build_authorization_url`` carries the
   resolved ``requested_scopes`` so the callback can compare without a
   server-side session lookup.
4. ``exchange_code`` writes ``_requested_scopes`` and ``_scope_status``
   into ``oauth_token_json`` — partial-consent is observable via
   inspecting the connection record.
5. The refresh path preserves ``_requested_scopes`` even when the
   refresh response omits ``scope`` (most providers do); status
   updates only when granted info is available.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet

from ruhu.db import build_session_factory
from ruhu.db_models import APIConnectionRecord
from ruhu.tools import oauth as oauth_module
from ruhu.tools.management import APIConnectionStore, CredentialCipher
from ruhu.tools.oauth import (
    OAuthFlowManager,
    OAuthTokenRefresher,
    _parse_granted_scopes,
    compute_scope_status,
)


def _make_flow_manager(sf=None) -> OAuthFlowManager:
    return OAuthFlowManager(
        session_factory=sf or _MagicSf(),
        cipher=CredentialCipher(Fernet.generate_key()),
        redirect_base_url="https://app.example.com",
    )


class _MagicSf:
    """Minimal stand-in when build_authorization_url is exercised
    without a DB (no auth_url_override lookup needed)."""

    def __call__(self):
        raise RuntimeError("session_factory should not be opened in this test")


# ── compute_scope_status (pure) ─────────────────────────────────────────


def test_scope_complete_when_granted_matches_requested() -> None:
    status, missing = compute_scope_status(
        requested=["read", "write"], granted={"read", "write"}
    )
    assert status == "complete"
    assert missing == set()


def test_scope_complete_when_granted_is_superset() -> None:
    """Provider granting MORE than asked is fine — common with Google
    where scopes have implicit dependencies (e.g., calendar.events
    grants calendar.readonly too)."""
    status, _ = compute_scope_status(
        requested=["read"], granted={"read", "write", "admin"}
    )
    assert status == "complete"


def test_scope_partial_lists_missing_scopes() -> None:
    status, missing = compute_scope_status(
        requested=["read", "write", "admin"], granted={"read"}
    )
    assert status == "partial"
    assert missing == {"write", "admin"}


def test_scope_unknown_when_provider_omits_scope_field() -> None:
    """Salesforce and others sometimes omit the ``scope`` response
    field. Don't flip to 'partial' on empty grant set — we have no
    signal to infer anything."""
    status, missing = compute_scope_status(
        requested=["api", "refresh_token"], granted=set()
    )
    assert status == "unknown"
    assert missing == set()


def test_scope_complete_when_no_scopes_requested() -> None:
    """A request with no scopes (rare; usually means provider chooses
    a default set) is trivially complete."""
    status, _ = compute_scope_status(requested=[], granted={"x"})
    assert status == "complete"


# ── _parse_granted_scopes (pure) ────────────────────────────────────────


def test_parse_granted_scopes_handles_rfc_string_form() -> None:
    assert _parse_granted_scopes({"scope": "read write admin"}) == {
        "read",
        "write",
        "admin",
    }


def test_parse_granted_scopes_handles_list_form() -> None:
    """Microsoft Graph returns ``scp`` lists in some flows; some token
    endpoints follow suit and return ``scope`` as a list."""
    assert _parse_granted_scopes({"scope": ["read", "write"]}) == {"read", "write"}


def test_parse_granted_scopes_returns_empty_when_absent() -> None:
    assert _parse_granted_scopes({}) == set()
    assert _parse_granted_scopes({"scope": None}) == set()


def test_parse_granted_scopes_strips_empty_tokens() -> None:
    """Whitespace splitting must not produce empty strings."""
    assert _parse_granted_scopes({"scope": "  read   write  "}) == {"read", "write"}


# ── State payload carries requested_scopes ──────────────────────────────


def test_authorization_url_state_carries_requested_scopes_for_known_provider(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        oauth_module, "_load_connection_auth_url_override", lambda *a, **kw: None
    )
    manager = _make_flow_manager()
    url = manager.build_authorization_url(
        connection_id="conn_1",
        organization_id="org_a",
        provider="hubspot",
        client_id="cid",
        scopes=["read.contacts", "write.contacts"],
    )
    state_token = parse_qs(urlparse(url).query)["state"][0]
    payload = manager.decode_state(state_token)
    assert payload["requested_scopes"] == ["read.contacts", "write.contacts"]


def test_authorization_url_state_falls_back_to_provider_default_scopes(
    monkeypatch,
) -> None:
    """When the caller doesn't pass ``scopes``, the URL should use the
    provider's default_scopes AND the state should reflect them — so
    the callback compares against the same set we actually asked for."""
    monkeypatch.setattr(
        oauth_module, "_load_connection_auth_url_override", lambda *a, **kw: None
    )
    manager = _make_flow_manager()
    url = manager.build_authorization_url(
        connection_id="conn_1",
        organization_id="org_a",
        provider="hubspot",
        client_id="cid",
    )
    state_token = parse_qs(urlparse(url).query)["state"][0]
    payload = manager.decode_state(state_token)
    assert payload["requested_scopes"]  # non-empty
    # The URL's scope param must list the same scopes we sealed in state.
    qs_scope = parse_qs(urlparse(url).query)["scope"][0].split()
    assert sorted(qs_scope) == sorted(payload["requested_scopes"])


# ── exchange_code persists scope metadata ──────────────────────────────


@pytest.fixture
def seed_oauth_connection(postgres_database_url_factory, credential_cipher):
    def _seed(*, provider: str = "hubspot"):
        url = postgres_database_url_factory()
        sf = build_session_factory(url)
        store = APIConnectionStore(sf, blob_cipher=credential_cipher)
        record = store.create(
            organization_id="org-A",
            display_name=provider,
            provider=provider,
            auth_type="oauth2",
        )
        return sf, record

    return _seed


def test_exchange_code_records_complete_status_when_full_scope_granted(
    seed_oauth_connection, monkeypatch
) -> None:
    sf, record = seed_oauth_connection()
    manager = OAuthFlowManager(
        session_factory=sf,
        cipher=CredentialCipher(Fernet.generate_key()),
        redirect_base_url="https://app.example.com",
    )

    async def _mock_fetch_token(**_kwargs):
        return {
            "access_token": "atk",
            "refresh_token": "rtk",
            "expires_in": 3600,
            "scope": "read write admin",
        }

    monkeypatch.setattr(oauth_module, "_fetch_token", _mock_fetch_token)

    asyncio.run(
        manager.exchange_code(
            connection_id=record.connection_id,
            organization_id=record.organization_id,
            provider=record.provider,
            code="auth-code",
            client_id="cid",
            client_secret="csecret",
            requested_scopes=["read", "write", "admin"],
        )
    )

    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.oauth_token_json["_requested_scopes"] == ["read", "write", "admin"]
        assert after.oauth_token_json["_scope_status"] == "complete"


def test_exchange_code_records_partial_status_when_user_deselects_scope(
    seed_oauth_connection, monkeypatch
) -> None:
    """The whole point of this feature: user deselected ``admin`` on
    the consent screen. The token endpoint returns ``scope=read write``,
    we record ``_scope_status="partial"`` so downstream tools / UI can
    react before runtime."""
    sf, record = seed_oauth_connection()
    manager = OAuthFlowManager(
        session_factory=sf,
        cipher=CredentialCipher(Fernet.generate_key()),
        redirect_base_url="https://app.example.com",
    )

    async def _mock_fetch_token(**_kwargs):
        return {
            "access_token": "atk",
            "refresh_token": "rtk",
            "expires_in": 3600,
            "scope": "read write",  # admin deselected
        }

    monkeypatch.setattr(oauth_module, "_fetch_token", _mock_fetch_token)

    asyncio.run(
        manager.exchange_code(
            connection_id=record.connection_id,
            organization_id=record.organization_id,
            provider=record.provider,
            code="auth-code",
            client_id="cid",
            client_secret="csecret",
            requested_scopes=["read", "write", "admin"],
        )
    )

    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.oauth_token_json["_requested_scopes"] == ["read", "write", "admin"]
        assert after.oauth_token_json["_scope_status"] == "partial"


def test_exchange_code_records_unknown_when_provider_omits_scope(
    seed_oauth_connection, monkeypatch
) -> None:
    """Salesforce-style omission: provider doesn't echo ``scope`` in
    the token response. We persist requested_scopes but mark status
    as ``"unknown"`` so the UI can prompt for re-consent if it wants
    certainty, but we don't falsely flag the connection partial."""
    sf, record = seed_oauth_connection()
    manager = OAuthFlowManager(
        session_factory=sf,
        cipher=CredentialCipher(Fernet.generate_key()),
        redirect_base_url="https://app.example.com",
    )

    async def _mock_fetch_token(**_kwargs):
        return {"access_token": "atk", "refresh_token": "rtk", "expires_in": 3600}

    monkeypatch.setattr(oauth_module, "_fetch_token", _mock_fetch_token)

    asyncio.run(
        manager.exchange_code(
            connection_id=record.connection_id,
            organization_id=record.organization_id,
            provider=record.provider,
            code="auth-code",
            client_id="cid",
            client_secret="csecret",
            requested_scopes=["api", "refresh_token"],
        )
    )

    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.oauth_token_json["_requested_scopes"] == ["api", "refresh_token"]
        assert after.oauth_token_json["_scope_status"] == "unknown"


def test_exchange_code_skips_scope_metadata_when_no_requested_passed(
    seed_oauth_connection, monkeypatch
) -> None:
    """Backwards compat: if the caller doesn't pass requested_scopes
    (e.g., a custom integration that builds its own auth URL), don't
    add scope metadata to the token JSON."""
    sf, record = seed_oauth_connection()
    manager = OAuthFlowManager(
        session_factory=sf,
        cipher=CredentialCipher(Fernet.generate_key()),
        redirect_base_url="https://app.example.com",
    )

    async def _mock_fetch_token(**_kwargs):
        return {
            "access_token": "atk",
            "refresh_token": "rtk",
            "expires_in": 3600,
            "scope": "read",
        }

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

    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert "_requested_scopes" not in after.oauth_token_json
        assert "_scope_status" not in after.oauth_token_json


# ── Refresh path preserves scope metadata ──────────────────────────────


def test_refresh_preserves_requested_scopes_when_response_omits_scope(
    seed_oauth_connection, monkeypatch
) -> None:
    """Most providers don't return ``scope`` on refresh — they assume
    'same as before'. The platform must REMEMBER what was originally
    consented to, not silently lose it on the next refresh tick."""
    sf, record = seed_oauth_connection()
    manager = OAuthFlowManager(
        session_factory=sf,
        cipher=CredentialCipher(Fernet.generate_key()),
        redirect_base_url="https://app.example.com",
    )

    # Step 1: initial exchange records partial status (admin missing).
    async def _initial_exchange(**_kwargs):
        return {
            "access_token": "atk1",
            "refresh_token": "rtk",
            "expires_in": 60,
            "scope": "read write",
        }

    monkeypatch.setattr(oauth_module, "_fetch_token", _initial_exchange)

    asyncio.run(
        manager.exchange_code(
            connection_id=record.connection_id,
            organization_id=record.organization_id,
            provider=record.provider,
            code="auth-code",
            client_id="cid",
            client_secret="csecret",
            requested_scopes=["read", "write", "admin"],
        )
    )

    # Step 2: background refresh — provider omits ``scope``.
    async def _refresh_no_scope(**_kwargs):
        return {"access_token": "atk2", "expires_in": 3600}

    monkeypatch.setattr(oauth_module, "_fetch_token", _refresh_no_scope)

    refresher = OAuthTokenRefresher(
        sf, get_credentials=lambda _provider: ("cid", "csecret")
    )
    # Move expiry into the refresh window.
    with sf.begin() as session:
        row = session.get(APIConnectionRecord, record.connection_id)
        row.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=10)

    asyncio.run(refresher.refresh_expiring_once())

    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        # New access token rotated in.
        assert after.oauth_token_json["access_token"] == "atk2"
        # Originally-requested scopes preserved.
        assert after.oauth_token_json["_requested_scopes"] == ["read", "write", "admin"]
        # Status becomes "unknown" (no scope info on this refresh response)
        # rather than spuriously flipping to "complete" or "partial".
        assert after.oauth_token_json["_scope_status"] == "unknown"


def test_refresh_recomputes_status_when_provider_returns_scope(
    seed_oauth_connection, monkeypatch
) -> None:
    """Some providers (HubSpot) DO echo ``scope`` on refresh. If they
    do, recompute status — admin can be revoked / re-granted between
    refresh cycles via the provider's settings panel."""
    sf, record = seed_oauth_connection()
    manager = OAuthFlowManager(
        session_factory=sf,
        cipher=CredentialCipher(Fernet.generate_key()),
        redirect_base_url="https://app.example.com",
    )

    # Initial: partial.
    async def _initial(**_kwargs):
        return {
            "access_token": "atk1",
            "refresh_token": "rtk",
            "expires_in": 60,
            "scope": "read",
        }

    monkeypatch.setattr(oauth_module, "_fetch_token", _initial)

    asyncio.run(
        manager.exchange_code(
            connection_id=record.connection_id,
            organization_id=record.organization_id,
            provider=record.provider,
            code="auth-code",
            client_id="cid",
            client_secret="csecret",
            requested_scopes=["read", "write"],
        )
    )

    # Refresh: provider granted everything now.
    async def _refresh_complete(**_kwargs):
        return {
            "access_token": "atk2",
            "expires_in": 3600,
            "scope": "read write",
        }

    monkeypatch.setattr(oauth_module, "_fetch_token", _refresh_complete)
    with sf.begin() as session:
        row = session.get(APIConnectionRecord, record.connection_id)
        row.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=10)

    refresher = OAuthTokenRefresher(
        sf, get_credentials=lambda _provider: ("cid", "csecret")
    )
    asyncio.run(refresher.refresh_expiring_once())

    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.oauth_token_json["_scope_status"] == "complete"
