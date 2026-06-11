"""Tier 2: ``requires_reauth`` status branch in the refresh worker.

When the provider rejects the refresh token (RFC 6749 §5.2 ``invalid_grant``),
the refresh token is dead — the user must reconsent. Marking those
connections ``"error"`` muddles transient failures with permanent ones and
keeps the refresh loop hammering an endpoint that will never recover.

These tests pin three guarantees:
1. ``_is_invalid_grant_error`` matches the canonical error string.
2. ``_refresh_one`` routes ``invalid_grant`` failures to
   ``_mark_connection_requires_reauth`` (status=``requires_reauth``)
   and routes other failures to ``_mark_connection_error`` (status=``error``).
3. ``refresh_expiring_once`` skips ``requires_reauth`` connections — they need
   user action, not another retry.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from ruhu.db import build_session_factory
from ruhu.db_models import APIConnectionRecord
from ruhu.tools import oauth as oauth_module
from ruhu.tools.management import APIConnectionStore
from ruhu.tools.oauth import (
    OAuthTokenRefresher,
    _is_invalid_grant_error,
    _mark_connection_requires_reauth,
)


# ── _is_invalid_grant_error: pure function ──────────────────────────────


def test_is_invalid_grant_error_matches_canonical_oauth_error() -> None:
    """Token endpoints return ``{"error": "invalid_grant", ...}`` per
    RFC 6749 §5.2; ``_fetch_token`` wraps the body text in a ValueError."""
    exc = ValueError(
        'token endpoint returned 400: {"error":"invalid_grant",'
        '"error_description":"refresh token rotated"}'
    )
    assert _is_invalid_grant_error(exc) is True


def test_is_invalid_grant_error_matches_form_encoded_body() -> None:
    """Some legacy providers return form-encoded error responses."""
    exc = ValueError("token endpoint returned 400: error=invalid_grant&error_description=…")
    assert _is_invalid_grant_error(exc) is True


def test_is_invalid_grant_error_skips_unrelated_failures() -> None:
    """Network errors, 5xxs, and other OAuth error codes must NOT be
    classified as needing reauth — those are transient or operator-fixable."""
    assert _is_invalid_grant_error(ValueError("connection refused")) is False
    assert _is_invalid_grant_error(ValueError("token endpoint returned 503: …")) is False
    assert (
        _is_invalid_grant_error(
            ValueError('token endpoint returned 400: {"error":"invalid_client"}')
        )
        is False
    )
    assert (
        _is_invalid_grant_error(
            ValueError('token endpoint returned 400: {"error":"invalid_request"}')
        )
        is False
    )


# ── _mark_connection_requires_reauth: DB write ──────────────────────────


@pytest.fixture
def seed_oauth_connection(postgres_database_url_factory, credential_cipher):
    """Seed a connection with an existing refresh_token, expiring soon."""

    def _seed(*, status: str = "active", expires_in_minutes: int = 1):
        url = postgres_database_url_factory()
        sf = build_session_factory(url)
        store = APIConnectionStore(sf, blob_cipher=credential_cipher)
        record = store.create(
            organization_id="org-A",
            display_name="hubspot",
            provider="hubspot",
            auth_type="oauth2",
            oauth_token={
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "expires_in": 3600,
            },
        )
        # Pin the expiry inside the refresh window and override status.
        with sf.begin() as session:
            row = session.get(APIConnectionRecord, record.connection_id)
            assert row is not None
            row.token_expires_at = datetime.now(timezone.utc) + timedelta(
                minutes=expires_in_minutes
            )
            row.status = status
        return sf, record

    return _seed


def test_mark_connection_requires_reauth_sets_status_and_error(
    seed_oauth_connection,
) -> None:
    sf, record = seed_oauth_connection()

    _mark_connection_requires_reauth(
        sf,
        connection_id=record.connection_id,
        organization_id=record.organization_id,
        error="refresh token rejected by provider: invalid_grant",
    )

    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.status == "requires_reauth"
        assert "invalid_grant" in (after.error_message or "")


# ── _refresh_one: branch on invalid_grant ───────────────────────────────


def _make_refresher(sf, *, fetch_token_raises: Exception) -> OAuthTokenRefresher:
    """Build a refresher whose token endpoint always raises *fetch_token_raises*."""

    async def _mock_fetch_token(**_kwargs):
        raise fetch_token_raises

    # Patch the module-level _fetch_token used by _refresh_one.
    oauth_module._fetch_token = _mock_fetch_token  # type: ignore[assignment]

    return OAuthTokenRefresher(
        sf,
        get_credentials=lambda _provider: ("cid", "csecret"),
    )


def test_refresh_one_marks_requires_reauth_on_invalid_grant(
    seed_oauth_connection, monkeypatch
) -> None:
    """The whole point of this feature: when the provider rejects the
    refresh token, the connection transitions to ``requires_reauth`` so
    the UI can surface a Reconnect button and the loop stops retrying."""
    sf, record = seed_oauth_connection()

    invalid_grant_exc = ValueError(
        'token endpoint returned 400: {"error":"invalid_grant",'
        '"error_description":"refresh_token has expired"}'
    )

    async def _mock_fetch_token(**_kwargs):
        raise invalid_grant_exc

    monkeypatch.setattr(oauth_module, "_fetch_token", _mock_fetch_token)

    refresher = OAuthTokenRefresher(
        sf,
        get_credentials=lambda _provider: ("cid", "csecret"),
    )

    with sf() as session:
        conn = session.get(APIConnectionRecord, record.connection_id)
        assert conn is not None
        # Detach so we can pass it into _refresh_one without session mismatch
        session.expunge(conn)

    asyncio.run(refresher._refresh_one(conn))

    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.status == "requires_reauth"
        assert "invalid_grant" in (after.error_message or "")


def test_refresh_one_marks_error_on_other_failures(
    seed_oauth_connection, monkeypatch
) -> None:
    """Transient failures (network errors, 5xx, invalid_client) stay as
    ``"error"`` so the refresh loop will retry on the next scan."""
    sf, record = seed_oauth_connection()

    async def _mock_fetch_token(**_kwargs):
        raise ValueError("connection reset by peer")

    monkeypatch.setattr(oauth_module, "_fetch_token", _mock_fetch_token)

    refresher = OAuthTokenRefresher(
        sf,
        get_credentials=lambda _provider: ("cid", "csecret"),
    )

    with sf() as session:
        conn = session.get(APIConnectionRecord, record.connection_id)
        assert conn is not None
        session.expunge(conn)

    asyncio.run(refresher._refresh_one(conn))

    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.status == "error"
        assert "connection reset" in (after.error_message or "")


# ── refresh_expiring_once: skip requires_reauth in scan ─────────────────────


def testrefresh_expiring_once_excludes_requires_reauth_connections(
    seed_oauth_connection, monkeypatch
) -> None:
    """A ``requires_reauth`` connection sitting in the table must NOT be
    picked up by the next scan — the refresh worker shouldn't keep
    pounding a known-dead refresh token."""
    sf, record = seed_oauth_connection(status="requires_reauth")

    refresh_attempts: list[str] = []

    async def _mock_fetch_token(**kwargs):
        refresh_attempts.append("called")
        return {"access_token": "new", "expires_in": 3600}

    monkeypatch.setattr(oauth_module, "_fetch_token", _mock_fetch_token)

    refresher = OAuthTokenRefresher(
        sf,
        get_credentials=lambda _provider: ("cid", "csecret"),
    )

    asyncio.run(refresher.refresh_expiring_once())

    assert refresh_attempts == [], (
        "requires_reauth connection should not be re-fetched"
    )

    # And the row itself was untouched.
    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.status == "requires_reauth"


def testrefresh_expiring_once_still_picks_up_active_and_error_connections(
    seed_oauth_connection, monkeypatch
) -> None:
    """Sanity: the exclusion of ``requires_reauth`` doesn't accidentally
    drop the regular ``active`` and ``error`` paths."""
    sf, record = seed_oauth_connection(status="error")

    refresh_attempts: list[str] = []

    async def _mock_fetch_token(**kwargs):
        refresh_attempts.append(kwargs.get("grant_type", ""))
        return {"access_token": "new-access", "expires_in": 3600}

    monkeypatch.setattr(oauth_module, "_fetch_token", _mock_fetch_token)

    refresher = OAuthTokenRefresher(
        sf,
        get_credentials=lambda _provider: ("cid", "csecret"),
    )

    asyncio.run(refresher.refresh_expiring_once())

    assert refresh_attempts == ["refresh_token"]
    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.status == "active"  # _persist_tokens flips it back
