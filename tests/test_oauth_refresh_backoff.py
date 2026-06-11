"""Tier 3: refresh backoff for transient failures.

Without backoff, a refresh worker polling every 60s will hammer a
flapping provider 60 times an hour. With backoff, consecutive transient
failures double the cooldown (60s → 120s → 240s → ...) up to a 30-min
cap, while a successful refresh resets the counter.

These tests pin the four contract guarantees:

1. The deterministic curve doubles per failure, capped at 30 min.
2. ``_record_refresh_failure`` increments the counter and stamps the
   attempt time, so subsequent scans can decide cooldown.
3. ``_persist_tokens`` (called on every successful refresh AND initial
   exchange) resets the counter — proves "tokens written = healthy".
4. The refresher's ``_refresh_one`` skips connections inside their
   cooldown window without calling the provider.

``invalid_grant`` failures don't traverse this curve — the connection
is flipped to ``requires_reauth`` and excluded from the scan entirely.
That guarantee lives in test_oauth_requires_reauth.py.
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
    _is_within_refresh_backoff_window,
    _record_refresh_failure,
    _refresh_backoff_seconds,
    _REFRESH_BACKOFF_BASE_SECONDS,
    _REFRESH_BACKOFF_MAX_SECONDS,
)


# ── _refresh_backoff_seconds: pure function ─────────────────────────────


def test_backoff_zero_for_zero_failures() -> None:
    """A fresh connection (no prior failures) retries immediately."""
    assert _refresh_backoff_seconds(0) == 0.0
    assert _refresh_backoff_seconds(-1) == 0.0  # defensive: negative same as 0


def test_backoff_doubles_per_failure_until_cap() -> None:
    """Curve: 60, 120, 240, 480, 960, 1800, 1800, 1800, ..."""
    assert _refresh_backoff_seconds(1) == _REFRESH_BACKOFF_BASE_SECONDS
    assert _refresh_backoff_seconds(2) == _REFRESH_BACKOFF_BASE_SECONDS * 2
    assert _refresh_backoff_seconds(3) == _REFRESH_BACKOFF_BASE_SECONDS * 4
    assert _refresh_backoff_seconds(4) == _REFRESH_BACKOFF_BASE_SECONDS * 8
    assert _refresh_backoff_seconds(5) == _REFRESH_BACKOFF_BASE_SECONDS * 16
    # Capped from this point on.
    assert _refresh_backoff_seconds(6) == _REFRESH_BACKOFF_MAX_SECONDS
    assert _refresh_backoff_seconds(20) == _REFRESH_BACKOFF_MAX_SECONDS


# ── _is_within_refresh_backoff_window: edge cases ───────────────────────


class _Stub:
    """Minimal duck-typed stand-in for APIConnectionRecord."""

    def __init__(self, *, refresh_failure_count: int, last_refresh_attempt_at):
        self.refresh_failure_count = refresh_failure_count
        self.last_refresh_attempt_at = last_refresh_attempt_at


def test_within_backoff_returns_false_when_no_failures() -> None:
    conn = _Stub(refresh_failure_count=0, last_refresh_attempt_at=None)
    assert _is_within_refresh_backoff_window(conn, now=datetime.now(timezone.utc)) is False


def test_within_backoff_returns_false_when_attempt_is_unset() -> None:
    """Defensive: if the failure_count was bumped without setting
    last_refresh_attempt_at, treat it as eligible (don't pin a connection
    in cooldown forever)."""
    conn = _Stub(refresh_failure_count=3, last_refresh_attempt_at=None)
    assert _is_within_refresh_backoff_window(conn, now=datetime.now(timezone.utc)) is False


def test_within_backoff_returns_true_immediately_after_failure() -> None:
    now = datetime.now(timezone.utc)
    conn = _Stub(refresh_failure_count=1, last_refresh_attempt_at=now)
    # 1 failure → 60s cooldown; just one second after the attempt is well within
    assert _is_within_refresh_backoff_window(conn, now=now + timedelta(seconds=1)) is True


def test_within_backoff_returns_false_after_window_elapses() -> None:
    last = datetime.now(timezone.utc)
    conn = _Stub(refresh_failure_count=1, last_refresh_attempt_at=last)
    # 1 failure → 60s cooldown; 61s later → eligible
    assert _is_within_refresh_backoff_window(conn, now=last + timedelta(seconds=61)) is False


def test_within_backoff_respects_doubling_curve() -> None:
    """At failure_count=3 the window is 240s; verify not-yet-eligible at 200s
    and eligible at 250s."""
    last = datetime.now(timezone.utc)
    conn = _Stub(refresh_failure_count=3, last_refresh_attempt_at=last)
    assert _is_within_refresh_backoff_window(conn, now=last + timedelta(seconds=200)) is True
    assert _is_within_refresh_backoff_window(conn, now=last + timedelta(seconds=250)) is False


# ── _record_refresh_failure: persists state ─────────────────────────────


@pytest.fixture
def seed_oauth_connection(postgres_database_url_factory, credential_cipher):
    def _seed():
        url = postgres_database_url_factory()
        sf = build_session_factory(url)
        store = APIConnectionStore(sf, blob_cipher=credential_cipher)
        record = store.create(
            organization_id="org-A",
            display_name="hubspot",
            provider="hubspot",
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
        return sf, record

    return _seed


def test_record_refresh_failure_increments_counter(seed_oauth_connection) -> None:
    sf, record = seed_oauth_connection()

    _record_refresh_failure(
        sf,
        connection_id=record.connection_id,
        organization_id=record.organization_id,
        error="transient timeout",
    )

    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.refresh_failure_count == 1
        assert after.last_refresh_attempt_at is not None
        assert after.status == "error"
        assert "transient timeout" in (after.error_message or "")


def test_record_refresh_failure_increments_monotonically(seed_oauth_connection) -> None:
    """Each new failure adds one. Three consecutive failures → count=3."""
    sf, record = seed_oauth_connection()

    for _ in range(3):
        _record_refresh_failure(
            sf,
            connection_id=record.connection_id,
            organization_id=record.organization_id,
            error="still failing",
        )

    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.refresh_failure_count == 3


# ── _persist_tokens: resets the counter on success ──────────────────────


def test_successful_refresh_resets_failure_counter(
    seed_oauth_connection, monkeypatch
) -> None:
    """A successful refresh after an error streak must drop the
    counter to zero — proves "tokens written = healthy" is the single
    signal driving the curve."""
    sf, record = seed_oauth_connection()

    # Simulate two prior failures.
    _record_refresh_failure(
        sf, connection_id=record.connection_id,
        organization_id=record.organization_id, error="fail 1",
    )
    _record_refresh_failure(
        sf, connection_id=record.connection_id,
        organization_id=record.organization_id, error="fail 2",
    )

    # Pin the connection's last_refresh_attempt_at into the past so the
    # backoff window has elapsed and the next refresh attempt happens.
    with sf.begin() as session:
        row = session.get(APIConnectionRecord, record.connection_id)
        assert row is not None
        row.last_refresh_attempt_at = datetime.now(timezone.utc) - timedelta(minutes=10)

    async def _ok_refresh(**_kwargs):
        return {"access_token": "fresh-atk", "expires_in": 3600}

    monkeypatch.setattr(oauth_module, "_fetch_token", _ok_refresh)

    refresher = OAuthTokenRefresher(
        sf,
        get_credentials=lambda _provider: ("cid", "csecret"),
    )

    asyncio.run(refresher.refresh_expiring_once())

    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.status == "active"
        assert after.refresh_failure_count == 0
        assert after.error_message is None


# ── _refresh_one: skips when within cooldown ────────────────────────────


def test_refresh_one_skips_provider_call_when_in_backoff(
    seed_oauth_connection, monkeypatch
) -> None:
    """The whole point of backoff: the refresher must not call the
    provider while the connection is in cooldown."""
    sf, record = seed_oauth_connection()

    # Mark a recent failure → 60s cooldown active.
    _record_refresh_failure(
        sf, connection_id=record.connection_id,
        organization_id=record.organization_id, error="recent fail",
    )

    refresh_attempts: list[str] = []

    async def _spy_fetch(**_kwargs):
        refresh_attempts.append("called")
        return {"access_token": "x", "expires_in": 3600}

    monkeypatch.setattr(oauth_module, "_fetch_token", _spy_fetch)

    refresher = OAuthTokenRefresher(
        sf,
        get_credentials=lambda _provider: ("cid", "csecret"),
    )

    asyncio.run(refresher.refresh_expiring_once())

    assert refresh_attempts == [], (
        "connection in backoff window must not be called"
    )

    # And the row's failure_count is preserved (skip is not a new attempt).
    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.refresh_failure_count == 1


def test_refresh_one_proceeds_when_cooldown_elapsed(
    seed_oauth_connection, monkeypatch
) -> None:
    """The complementary contract: once the backoff window passes, the
    next scan retries normally."""
    sf, record = seed_oauth_connection()

    _record_refresh_failure(
        sf, connection_id=record.connection_id,
        organization_id=record.organization_id, error="old fail",
    )
    # Push the attempt time past the 60s cooldown (use 5 min for clarity).
    with sf.begin() as session:
        row = session.get(APIConnectionRecord, record.connection_id)
        assert row is not None
        row.last_refresh_attempt_at = datetime.now(timezone.utc) - timedelta(minutes=5)

    refresh_attempts: list[str] = []

    async def _spy_fetch(**_kwargs):
        refresh_attempts.append("called")
        return {"access_token": "post-cooldown-atk", "expires_in": 3600}

    monkeypatch.setattr(oauth_module, "_fetch_token", _spy_fetch)

    refresher = OAuthTokenRefresher(
        sf,
        get_credentials=lambda _provider: ("cid", "csecret"),
    )

    asyncio.run(refresher.refresh_expiring_once())

    assert refresh_attempts == ["called"]
    with sf() as session:
        after = session.get(APIConnectionRecord, record.connection_id)
        assert after is not None
        assert after.status == "active"
        assert after.refresh_failure_count == 0  # reset by _persist_tokens


def test_refresh_one_advances_counter_on_repeated_failure(
    seed_oauth_connection, monkeypatch
) -> None:
    """Two attempts past the cooldown window, both failing, should push
    the counter to 2 — proves the curve actually advances over time."""
    sf, record = seed_oauth_connection()

    async def _failing_fetch(**_kwargs):
        raise ValueError("connection refused")

    monkeypatch.setattr(oauth_module, "_fetch_token", _failing_fetch)

    refresher = OAuthTokenRefresher(
        sf,
        get_credentials=lambda _provider: ("cid", "csecret"),
    )

    # First attempt: count 0 → 1
    asyncio.run(refresher._refresh_one(_load_for_refresh(sf, record.connection_id)))
    with sf() as session:
        assert session.get(APIConnectionRecord, record.connection_id).refresh_failure_count == 1

    # Push past 60s cooldown so the second attempt fires.
    with sf.begin() as session:
        row = session.get(APIConnectionRecord, record.connection_id)
        row.last_refresh_attempt_at = datetime.now(timezone.utc) - timedelta(minutes=10)

    # Second attempt: count 1 → 2
    asyncio.run(refresher._refresh_one(_load_for_refresh(sf, record.connection_id)))
    with sf() as session:
        assert session.get(APIConnectionRecord, record.connection_id).refresh_failure_count == 2


def _load_for_refresh(sf, connection_id: str) -> APIConnectionRecord:
    with sf() as session:
        conn = session.get(APIConnectionRecord, connection_id)
        assert conn is not None
        session.expunge(conn)
        return conn
