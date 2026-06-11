"""Tests for PKCE (RFC 7636) on the OAuth authorization flow.

The platform sends ``code_challenge`` + ``code_challenge_method=S256`` on
the authorization URL, seals the ``code_verifier`` inside the encrypted
``state`` parameter, and replays the verifier during token exchange.

Per-provider toggle (``OAuthProviderConfig.pkce_supported``) lets a
custom OAuth server that doesn't accept the challenge opt out without
losing the rest of the flow.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet

from ruhu.tools.management import CredentialCipher
from ruhu.tools.oauth import (
    OAuthFlowManager,
    _generate_pkce_verifier,
    _pkce_challenge_from_verifier,
)
from ruhu.tools.oauth_providers import OAUTH_PROVIDERS, OAuthProviderConfig


def _make_flow_manager() -> OAuthFlowManager:
    return OAuthFlowManager(
        session_factory=MagicMock(),
        cipher=CredentialCipher(Fernet.generate_key()),
        redirect_base_url="https://app.example.com",
    )


# ── PKCE helpers ─────────────────────────────────────────────────────


def test_pkce_verifier_is_url_safe_and_long_enough() -> None:
    """RFC 7636 §4.1: 43–128 chars from [A-Z][a-z][0-9]-._~. ``token_urlsafe``
    emits ``[A-Za-z0-9_-]+`` which is a strict subset of that set."""
    verifier = _generate_pkce_verifier()
    assert 43 <= len(verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9_\-]+", verifier) is not None


def test_pkce_verifier_is_unique_per_call() -> None:
    """A leaked or replayed verifier compromises the OAuth flow. Make
    sure two consecutive calls produce different values."""
    samples = {_generate_pkce_verifier() for _ in range(20)}
    assert len(samples) == 20  # no collisions


def test_pkce_challenge_is_base64url_sha256_no_padding() -> None:
    """The challenge format is rigid — RFC 7636 §4.2: base64url(sha256)
    with the trailing ``=`` padding stripped. This is the exact value the
    provider hashes the verifier into and compares against, so any
    deviation fails the consent step on the provider's side."""
    verifier = "test-verifier-123"
    challenge = _pkce_challenge_from_verifier(verifier)

    expected_digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected_b64 = base64.urlsafe_b64encode(expected_digest).rstrip(b"=").decode("ascii")
    assert challenge == expected_b64
    assert "=" not in challenge  # no padding
    assert re.fullmatch(r"[A-Za-z0-9_\-]+", challenge) is not None


def test_pkce_challenge_is_deterministic_for_a_given_verifier() -> None:
    verifier = _generate_pkce_verifier()
    assert _pkce_challenge_from_verifier(verifier) == _pkce_challenge_from_verifier(verifier)


# ── Auth URL building with PKCE ──────────────────────────────────────


def test_build_authorization_url_emits_pkce_for_hubspot() -> None:
    manager = _make_flow_manager()
    url = manager.build_authorization_url(
        connection_id="conn_1",
        organization_id="org_1",
        provider="hubspot",
        client_id="hubspot-client-id",
    )
    qs = parse_qs(urlparse(url).query)
    assert "code_challenge" in qs
    assert qs["code_challenge_method"] == ["S256"]
    assert len(qs["code_challenge"][0]) == 43  # base64url(sha256) = 43 chars unpadded


def test_build_authorization_url_emits_pkce_for_all_known_providers() -> None:
    """Every provider in OAUTH_PROVIDERS defaults to ``pkce_supported=True``.
    Verify the auth URL carries the challenge for each."""
    manager = _make_flow_manager()
    for slug in OAUTH_PROVIDERS.keys():
        url = manager.build_authorization_url(
            connection_id="conn_1",
            organization_id="org_1",
            provider=slug,
            client_id="cid",
        )
        qs = parse_qs(urlparse(url).query)
        assert "code_challenge" in qs, f"{slug} should send PKCE by default"
        assert qs["code_challenge_method"] == ["S256"], slug


def test_build_authorization_url_omits_pkce_when_provider_disabled(
    monkeypatch,
) -> None:
    """A provider with ``pkce_supported=False`` should produce a plain
    OAuth 2.0 auth URL (no challenge, no method)."""
    legacy_config = OAuthProviderConfig(
        authorization_url="https://legacy.example.com/oauth/authorize",
        token_url="https://legacy.example.com/oauth/token",
        default_scopes=["read"],
        pkce_supported=False,
    )
    monkeypatch.setitem(OAUTH_PROVIDERS, "legacy_provider", legacy_config)

    manager = _make_flow_manager()
    url = manager.build_authorization_url(
        connection_id="conn_1",
        organization_id="org_1",
        provider="legacy_provider",
        client_id="cid",
    )
    qs = parse_qs(urlparse(url).query)
    assert "code_challenge" not in qs
    assert "code_challenge_method" not in qs


def test_build_authorization_url_caller_can_force_pkce_off() -> None:
    """Some legacy OAuth servers refuse requests with unknown query
    params. The caller can override the provider default by passing
    ``pkce_supported=False`` explicitly."""
    manager = _make_flow_manager()
    url = manager.build_authorization_url(
        connection_id="conn_1",
        organization_id="org_1",
        provider="hubspot",
        client_id="cid",
        pkce_supported=False,
    )
    qs = parse_qs(urlparse(url).query)
    assert "code_challenge" not in qs


def test_build_authorization_url_caller_can_force_pkce_on() -> None:
    """And vice-versa: a custom provider default can be overridden by
    passing ``pkce_supported=True`` explicitly."""
    legacy_config = OAuthProviderConfig(
        authorization_url="https://legacy.example.com/oauth/authorize",
        token_url="https://legacy.example.com/oauth/token",
        default_scopes=["read"],
        pkce_supported=False,
    )
    import ruhu.tools.oauth_providers as op_mod

    op_mod.OAUTH_PROVIDERS["legacy_force_on"] = legacy_config
    try:
        manager = _make_flow_manager()
        url = manager.build_authorization_url(
            connection_id="conn_1",
            organization_id="org_1",
            provider="legacy_force_on",
            client_id="cid",
            pkce_supported=True,
        )
        qs = parse_qs(urlparse(url).query)
        assert "code_challenge" in qs
        assert qs["code_challenge_method"] == ["S256"]
    finally:
        op_mod.OAUTH_PROVIDERS.pop("legacy_force_on", None)


def test_build_authorization_url_emits_fresh_verifier_per_call() -> None:
    """Replay protection: every call to the same connection must yield a
    different ``code_challenge`` because the verifier is generated fresh."""
    manager = _make_flow_manager()
    challenges: set[str] = set()
    for _ in range(5):
        url = manager.build_authorization_url(
            connection_id="conn_1",
            organization_id="org_1",
            provider="hubspot",
            client_id="cid",
        )
        qs = parse_qs(urlparse(url).query)
        challenges.add(qs["code_challenge"][0])
    assert len(challenges) == 5


# ── State payload round-trip ─────────────────────────────────────────


def test_state_payload_carries_code_verifier_when_pkce_used() -> None:
    """The verifier must reach the callback. We seal it inside the
    Fernet-encrypted state so the browser never sees it."""
    manager = _make_flow_manager()
    url = manager.build_authorization_url(
        connection_id="conn_1",
        organization_id="org_1",
        provider="hubspot",
        client_id="cid",
    )
    state_token = parse_qs(urlparse(url).query)["state"][0]
    payload = manager.decode_state(state_token)

    assert "code_verifier" in payload
    verifier = payload["code_verifier"]
    challenge_in_url = parse_qs(urlparse(url).query)["code_challenge"][0]
    # The challenge in the URL must match SHA256(verifier-in-state).
    assert _pkce_challenge_from_verifier(verifier) == challenge_in_url


def test_state_payload_omits_verifier_when_pkce_disabled() -> None:
    manager = _make_flow_manager()
    url = manager.build_authorization_url(
        connection_id="conn_1",
        organization_id="org_1",
        provider="hubspot",
        client_id="cid",
        pkce_supported=False,
    )
    state_token = parse_qs(urlparse(url).query)["state"][0]
    payload = manager.decode_state(state_token)
    assert "code_verifier" not in payload


# ── exchange_code propagates verifier into the token request ─────────


def test_exchange_code_sends_code_verifier_to_token_endpoint(monkeypatch) -> None:
    """When ``code_verifier`` is supplied, the token-exchange POST body
    must include it so the provider can recompute the challenge and
    accept the request."""
    manager = _make_flow_manager()

    captured_extra_params: dict = {}

    async def _mock_fetch_token(
        *, config, grant_type, client_id, client_secret, extra_params, token_url_override
    ):
        captured_extra_params.update(extra_params)
        return {"access_token": "access_xyz", "expires_in": 3600}

    monkeypatch.setattr("ruhu.tools.oauth._fetch_token", _mock_fetch_token)
    monkeypatch.setattr(
        "ruhu.tools.oauth._load_connection_token_url_override", lambda *a, **kw: None
    )
    monkeypatch.setattr("ruhu.tools.oauth._persist_tokens", lambda *a, **kw: None)

    async def run() -> None:
        await manager.exchange_code(
            connection_id="conn_1",
            organization_id="org_1",
            provider="hubspot",
            code="code-from-callback",
            client_id="cid",
            client_secret="csecret",
            code_verifier="my-verifier",
        )

    asyncio.run(run())

    assert captured_extra_params["code"] == "code-from-callback"
    assert captured_extra_params["code_verifier"] == "my-verifier"


def test_exchange_code_omits_verifier_when_not_provided(monkeypatch) -> None:
    """For providers without PKCE, no ``code_verifier`` should appear in
    the token-exchange POST body — some legacy servers reject unknown
    parameters."""
    manager = _make_flow_manager()

    captured_extra_params: dict = {}

    async def _mock_fetch_token(
        *, config, grant_type, client_id, client_secret, extra_params, token_url_override
    ):
        captured_extra_params.update(extra_params)
        return {"access_token": "access_xyz", "expires_in": 3600}

    monkeypatch.setattr("ruhu.tools.oauth._fetch_token", _mock_fetch_token)
    monkeypatch.setattr(
        "ruhu.tools.oauth._load_connection_token_url_override", lambda *a, **kw: None
    )
    monkeypatch.setattr("ruhu.tools.oauth._persist_tokens", lambda *a, **kw: None)

    async def run() -> None:
        await manager.exchange_code(
            connection_id="conn_1",
            organization_id="org_1",
            provider="hubspot",
            code="code-from-callback",
            client_id="cid",
            client_secret="csecret",
            code_verifier=None,
        )

    asyncio.run(run())

    assert "code_verifier" not in captured_extra_params
    assert captured_extra_params["code"] == "code-from-callback"
