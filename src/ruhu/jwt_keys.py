from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from jwt.algorithms import RSAAlgorithm


class JWTKeyConfigurationError(ValueError):
    pass


_MIN_HS256_SECRET_BYTES = 32


@dataclass(frozen=True, slots=True)
class JWTVerificationKey:
    kid: str | None
    algorithm: str
    key: Any
    public_jwk: dict[str, object] | None = None


@dataclass(slots=True)
class JWTKeyManager:
    hs256_secret: str | None = None
    signing_algorithm: str = "HS256"
    signing_key: Any | None = None
    signing_kid: str | None = None
    verification_keys: tuple[JWTVerificationKey, ...] = ()

    @classmethod
    def from_sources(
        cls,
        *,
        hs256_secret: str | None = None,
        private_key_pem: str | None = None,
        private_key_path: str | Path | None = None,
        active_kid: str | None = None,
        verification_jwks: str | None = None,
        verification_jwks_path: str | Path | None = None,
    ) -> "JWTKeyManager":
        if private_key_pem and private_key_path:
            raise JWTKeyConfigurationError(
                "configure only one of private_key_pem or private_key_path"
            )

        sources: list[dict[str, object]] = []
        if verification_jwks:
            sources.append(_parse_jwks_payload(verification_jwks))
        if verification_jwks_path is not None:
            sources.append(_parse_jwks_payload(Path(verification_jwks_path).read_text(encoding="utf-8")))

        verification: dict[tuple[str, str | None], JWTVerificationKey] = {}
        signing_key: Any | None = None
        signing_algorithm = "HS256"
        signing_kid: str | None = None

        if private_key_pem or private_key_path:
            if not active_kid:
                raise JWTKeyConfigurationError("active_kid is required when RS256 signing is configured")
            pem = private_key_pem or Path(private_key_path).read_text(encoding="utf-8")
            signing_key = serialization.load_pem_private_key(
                pem.encode("utf-8"),
                password=None,
            )
            signing_algorithm = "RS256"
            signing_kid = active_kid
            active_jwk = json.loads(RSAAlgorithm.to_jwk(signing_key.public_key()))
            active_jwk.update({"kid": active_kid, "use": "sig", "alg": "RS256"})
            verification[("RS256", active_kid)] = JWTVerificationKey(
                kid=active_kid,
                algorithm="RS256",
                key=signing_key.public_key(),
                public_jwk=active_jwk,
            )

        for payload in sources:
            keys = payload.get("keys", [])
            if not isinstance(keys, list):
                raise JWTKeyConfigurationError("JWKS payload must contain a keys array")
            for item in keys:
                if not isinstance(item, dict):
                    raise JWTKeyConfigurationError("each JWKS entry must be an object")
                algorithm = str(item.get("alg") or "RS256")
                if algorithm != "RS256":
                    continue
                jwk = dict(item)
                jwk.setdefault("use", "sig")
                jwk.setdefault("alg", "RS256")
                kid = jwk.get("kid")
                key_obj = RSAAlgorithm.from_jwk(json.dumps(jwk))
                verification.setdefault(
                    ("RS256", kid if isinstance(kid, str) else None),
                    JWTVerificationKey(
                        kid=kid if isinstance(kid, str) else None,
                        algorithm="RS256",
                        key=key_obj,
                        public_jwk=jwk,
                    ),
                )

        if signing_key is None and not hs256_secret:
            raise JWTKeyConfigurationError(
                "configure either an HS256 secret or RS256 private key signing material"
            )
        if signing_key is None and hs256_secret is not None:
            if len(hs256_secret.encode("utf-8")) < _MIN_HS256_SECRET_BYTES:
                raise JWTKeyConfigurationError(
                    f"HS256 secret must be at least {_MIN_HS256_SECRET_BYTES} bytes"
                )

        return cls(
            hs256_secret=hs256_secret,
            signing_algorithm=signing_algorithm,
            signing_key=signing_key,
            signing_kid=signing_kid,
            verification_keys=tuple(verification.values()),
        )

    @property
    def rs256_enabled(self) -> bool:
        return self.signing_algorithm == "RS256" and self.signing_key is not None

    def signing_params(self) -> tuple[Any, str, dict[str, str]]:
        if self.rs256_enabled:
            return self.signing_key, "RS256", {"kid": self.signing_kid or ""}
        if not self.hs256_secret:
            raise JWTKeyConfigurationError("HS256 secret is not configured")
        return self.hs256_secret, "HS256", {}

    def verification_candidates(self, *, algorithm: str, kid: str | None) -> list[Any]:
        if algorithm == "RS256":
            if kid is not None:
                keyed = [
                    item.key
                    for item in self.verification_keys
                    if item.algorithm == "RS256" and item.kid == kid
                ]
                if keyed:
                    return keyed
            return [
                item.key
                for item in self.verification_keys
                if item.algorithm == "RS256"
            ]
        if algorithm == "HS256" and self.hs256_secret:
            return [self.hs256_secret]
        return []

    def public_jwks(self) -> dict[str, list[dict[str, object]]]:
        keys: list[dict[str, object]] = []
        seen: set[str] = set()
        for item in self.verification_keys:
            if item.algorithm != "RS256" or item.public_jwk is None:
                continue
            serialized = json.dumps(item.public_jwk, sort_keys=True)
            if serialized in seen:
                continue
            seen.add(serialized)
            keys.append(dict(item.public_jwk))
        return {"keys": keys}


def _parse_jwks_payload(value: str) -> dict[str, object]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise JWTKeyConfigurationError("invalid JWKS JSON payload") from exc
    if isinstance(payload, dict) and "keys" in payload:
        return payload
    raise JWTKeyConfigurationError("JWKS payload must be a JSON object containing keys")
