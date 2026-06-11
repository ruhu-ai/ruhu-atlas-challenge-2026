from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from ruhu.auth import AccessTokenClaims, JWTCodec
from ruhu.jwt_keys import JWTKeyConfigurationError, JWTKeyManager


def _rsa_keypair() -> tuple[object, str, dict[str, object]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"use": "sig", "alg": "RS256"})
    return private_key, private_pem, public_jwk


def _claims() -> AccessTokenClaims:
    now = datetime.now(timezone.utc)
    return AccessTokenClaims(
        iss="ruhu",
        sub="user-1",
        sid="session-1",
        org="org-1",
        iat=int(now.timestamp()),
        exp=int((now + timedelta(minutes=10)).timestamp()),
    )


def test_rs256_jwt_codec_signs_and_publishes_jwks() -> None:
    _, private_pem, _ = _rsa_keypair()
    codec = JWTCodec(
        issuer="ruhu",
        key_manager=JWTKeyManager.from_sources(
            private_key_pem=private_pem,
            active_kid="kid-active",
        ),
    )

    token = codec.encode(_claims())
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "RS256"
    assert header["kid"] == "kid-active"

    claims = codec.decode(token)
    assert claims.sub == "user-1"

    jwks = codec.public_jwks()
    assert jwks["keys"][0]["kid"] == "kid-active"
    assert jwks["keys"][0]["alg"] == "RS256"


def test_rs256_jwt_codec_verifies_rotated_public_jwks_keys() -> None:
    old_private_key, _, old_public_jwk = _rsa_keypair()
    _, new_private_pem, _ = _rsa_keypair()
    old_public_jwk["kid"] = "kid-old"

    codec = JWTCodec(
        issuer="ruhu",
        key_manager=JWTKeyManager.from_sources(
            private_key_pem=new_private_pem,
            active_kid="kid-new",
            verification_jwks=json.dumps({"keys": [old_public_jwk]}),
        ),
    )

    token = jwt.encode(
        _claims().model_dump(mode="json"),
        old_private_key,
        algorithm="RS256",
        headers={"kid": "kid-old", "typ": "JWT"},
    )

    claims = codec.decode(token)
    assert claims.org == "org-1"


def test_hs256_key_manager_rejects_short_secrets() -> None:
    with pytest.raises(JWTKeyConfigurationError, match="HS256 secret must be at least 32 bytes"):
        JWTKeyManager.from_sources(hs256_secret="too-short")
