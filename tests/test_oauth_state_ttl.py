"""Tests for OAuth state TTL enforcement.

The Fernet-encrypted ``state`` cookie that ``OAuthFlowManager`` round-trips
through the provider needs an explicit time-to-live so a stale or replayed
state parameter is rejected after the consent window closes. This module
exercises the two layers of that contract:

1. ``CredentialCipher.decrypt(ciphertext, ttl=...)`` — the cipher accepts an
   optional TTL and propagates it to Fernet. Without ``ttl`` the existing
   long-lived behaviour for credential blobs is preserved.
2. ``OAuthFlowManager.decode_state(...)`` — wires
   ``_STATE_TTL_SECONDS = 600`` into every decode, so any path that goes
   through the OAuth flow gets TTL for free.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet, InvalidToken

from ruhu.tools.management import CredentialCipher
from ruhu.tools.oauth import (
    _STATE_TTL_SECONDS,
    OAuthFlowManager,
)


# ── CredentialCipher.decrypt ttl extension ───────────────────────────


def test_decrypt_without_ttl_preserves_legacy_behavior() -> None:
    """Existing callers (credential storage) pass no ``ttl`` and should be
    unaffected: tokens decrypt successfully regardless of their age."""
    cipher = CredentialCipher(Fernet.generate_key())
    token = cipher.encrypt({"foo": "bar"})

    # No ttl → succeeds even for an artificially old token.
    assert cipher.decrypt(token) == {"foo": "bar"}


def test_decrypt_with_ttl_succeeds_for_fresh_token() -> None:
    cipher = CredentialCipher(Fernet.generate_key())
    token = cipher.encrypt({"connection_id": "conn_1"})

    assert cipher.decrypt(token, ttl=600) == {"connection_id": "conn_1"}


def test_decrypt_with_ttl_rejects_token_older_than_window(monkeypatch) -> None:
    """Simulate a state token that was issued 11 minutes ago: with the OAuth
    state TTL of 10 minutes it must be rejected."""
    cipher = CredentialCipher(Fernet.generate_key())

    # Patch Fernet's clock so the token appears 11 minutes old.
    import cryptography.fernet as fernet_module

    real_time = fernet_module.time.time
    issuance_time = real_time()
    monkeypatch.setattr(fernet_module.time, "time", lambda: issuance_time)
    token = cipher.encrypt({"connection_id": "conn_old"})

    monkeypatch.setattr(
        fernet_module.time, "time", lambda: issuance_time + 11 * 60
    )
    with pytest.raises(InvalidToken):
        cipher.decrypt(token, ttl=_STATE_TTL_SECONDS)


def test_decrypt_with_ttl_passes_for_token_inside_window(monkeypatch) -> None:
    cipher = CredentialCipher(Fernet.generate_key())

    import cryptography.fernet as fernet_module

    real_time = fernet_module.time.time
    issuance_time = real_time()
    monkeypatch.setattr(fernet_module.time, "time", lambda: issuance_time)
    token = cipher.encrypt({"connection_id": "conn_fresh"})

    # 5 minutes later, well inside the 10-minute OAuth window.
    monkeypatch.setattr(
        fernet_module.time, "time", lambda: issuance_time + 5 * 60
    )
    assert cipher.decrypt(token, ttl=_STATE_TTL_SECONDS) == {
        "connection_id": "conn_fresh"
    }


# ── OAuthFlowManager.decode_state TTL wiring ─────────────────────────


def _make_flow_manager() -> OAuthFlowManager:
    """Build a manager with a Fernet cipher; session_factory is unused by
    the encode/decode-state code path so a MagicMock is fine."""
    cipher = CredentialCipher(Fernet.generate_key())
    return OAuthFlowManager(
        session_factory=MagicMock(),
        cipher=cipher,
        redirect_base_url="https://app.example.com",
    )


def test_decode_state_round_trips_a_fresh_token() -> None:
    manager = _make_flow_manager()
    state_token = manager._encode_state(
        connection_id="conn_1",
        organization_id="org_1",
        provider="hubspot",
    )
    decoded = manager.decode_state(state_token)
    assert decoded["connection_id"] == "conn_1"
    assert decoded["organization_id"] == "org_1"
    assert decoded["provider"] == "hubspot"


def test_decode_state_rejects_expired_token(monkeypatch) -> None:
    """An OAuth state token that survives past the 10-minute consent window
    must be rejected. Without this, an attacker who steals the state in
    transit (e.g. via a malicious browser extension during consent) could
    wait days and replay the callback."""
    manager = _make_flow_manager()

    import cryptography.fernet as fernet_module

    real_time = fernet_module.time.time
    issuance_time = real_time()
    monkeypatch.setattr(fernet_module.time, "time", lambda: issuance_time)
    state_token = manager._encode_state(
        connection_id="conn_1",
        organization_id="org_1",
        provider="hubspot",
    )

    monkeypatch.setattr(
        fernet_module.time, "time", lambda: issuance_time + _STATE_TTL_SECONDS + 1
    )
    with pytest.raises(ValueError, match="invalid or expired OAuth state token"):
        manager.decode_state(state_token)


def test_decode_state_rejects_tampered_token() -> None:
    """A flipped byte in the state token must fail decrypt with the same
    error shape as expiry — the caller doesn't need to distinguish."""
    manager = _make_flow_manager()
    state_token = manager._encode_state(
        connection_id="conn_1",
        organization_id="org_1",
        provider="hubspot",
    )
    # Flip a byte in the middle of the ciphertext (Fernet tokens are URL-safe
    # base64; mutating an alphanumeric byte produces an invalid token).
    middle = len(state_token) // 2
    tampered = state_token[:middle] + ("X" if state_token[middle] != "X" else "Y") + state_token[middle + 1 :]

    with pytest.raises(ValueError, match="invalid or expired OAuth state token"):
        manager.decode_state(tampered)


def test_decode_state_rejects_token_signed_with_different_key() -> None:
    """A state token signed by a different key (or a forged unsigned blob)
    must fail decrypt — confirms the cipher is doing more than base64-encode."""
    forger = CredentialCipher(Fernet.generate_key())
    forged_token = forger.encrypt(
        {"connection_id": "conn_attacker", "organization_id": "org_attacker", "provider": "hubspot"}
    )

    legitimate_manager = _make_flow_manager()
    with pytest.raises(ValueError):
        legitimate_manager.decode_state(forged_token)
