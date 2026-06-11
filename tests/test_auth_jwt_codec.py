"""JWTCodec expiry semantics — regression tests for verify_exp + clock-skew.

Before: JWTCodec.decode used verify_exp=False, so an expired access token
could pass auth if a downstream caller forgot to also call a manual exp check.
After: verify_exp=True with 30-second leeway. ExpiredSignatureError is
translated to the domain-typed TokenExpiredError so callers don't need a
second check.
"""
from __future__ import annotations

import time

import pytest

from ruhu.auth import (
    AccessTokenClaims,
    AuthenticationError,
    JWTCodec,
    TokenExpiredError,
)


SECRET = "0123456789abcdef0123456789abcdef"
ISSUER = "ruhu"


def _make_claims(*, exp_offset_seconds: int) -> AccessTokenClaims:
    now = int(time.time())
    return AccessTokenClaims(
        iss=ISSUER,
        sub="user-1",
        sid="session-1",
        org="org-1",
        iat=now,
        exp=now + exp_offset_seconds,
    )


def _codec() -> JWTCodec:
    return JWTCodec(secret=SECRET, issuer=ISSUER)


def test_valid_token_decodes_when_within_exp() -> None:
    codec = _codec()
    token = codec.encode(_make_claims(exp_offset_seconds=60))

    claims = codec.decode(token)
    assert claims.sub == "user-1"


def test_expired_token_raises_token_expired_error() -> None:
    codec = _codec()
    # 60 seconds past exp — well outside the 30s leeway.
    token = codec.encode(_make_claims(exp_offset_seconds=-60))

    with pytest.raises(TokenExpiredError):
        codec.decode(token)


def test_expired_within_leeway_is_accepted() -> None:
    """30s of clock skew tolerance — within window, the token still validates.

    Distributed systems with mildly drifted clocks would otherwise reject
    valid tokens. Industry-standard leeway is 30-60 seconds.
    """
    codec = _codec()
    # 10 seconds past exp — inside the 30s leeway.
    token = codec.encode(_make_claims(exp_offset_seconds=-10))

    claims = codec.decode(token)
    assert claims.sub == "user-1"


def test_just_outside_leeway_is_rejected() -> None:
    codec = _codec()
    # 31 seconds past exp — just outside the 30s leeway.
    token = codec.encode(_make_claims(exp_offset_seconds=-31))

    with pytest.raises(TokenExpiredError):
        codec.decode(token)


def test_token_expired_is_subclass_of_authentication_error() -> None:
    """Callers catching AuthenticationError still handle expired tokens
    without needing to import TokenExpiredError explicitly."""
    codec = _codec()
    token = codec.encode(_make_claims(exp_offset_seconds=-60))

    with pytest.raises(AuthenticationError):
        codec.decode(token)


def test_malformed_token_still_raises_authentication_error() -> None:
    """Non-expiry decode failures (bad signature, malformed) keep their
    existing error type — not converted to TokenExpiredError."""
    codec = _codec()
    other_codec = JWTCodec(secret="differentsecret_differentsecret_", issuer=ISSUER)
    token = other_codec.encode(_make_claims(exp_offset_seconds=60))

    with pytest.raises(AuthenticationError) as excinfo:
        codec.decode(token)
    # The signature failure must NOT be reported as expired.
    assert not isinstance(excinfo.value, TokenExpiredError)
