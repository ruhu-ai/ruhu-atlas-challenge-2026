from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from .runtime_config import RuntimeSettings


class ExternalAuthError(Exception):
    pass


@dataclass(slots=True)
class ExternalIdentityClaims:
    subject: str
    email: str | None
    email_verified: bool | None
    display_name: str | None
    avatar_url: str | None
    claims: dict[str, Any]


_DEFAULT_TIMEOUT_SECONDS = 10.0
_DISALLOWED_SSO_HOST_SUFFIXES = (".internal", ".local", ".localdomain", ".localhost", ".home.arpa")


def _validate_public_hostname(hostname: str, *, field_name: str) -> str:
    candidate = hostname.strip().rstrip(".").lower()
    if not candidate:
        raise ExternalAuthError(f"{field_name} is invalid")
    if "." not in candidate:
        raise ExternalAuthError(f"{field_name} must use a public hostname")
    if candidate == "localhost" or any(candidate.endswith(suffix) for suffix in _DISALLOWED_SSO_HOST_SUFFIXES):
        raise ExternalAuthError(f"{field_name} must use a public hostname")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return candidate
    raise ExternalAuthError(f"{field_name} must not use an IP address")


def _validate_public_https_url(value: str, *, field_name: str) -> str:
    candidate = value.strip()
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or not parsed.netloc or parsed.hostname is None:
        raise ExternalAuthError(f"{field_name} must be a valid https URL")
    if parsed.username is not None or parsed.password is not None:
        raise ExternalAuthError(f"{field_name} must not include credentials")
    if parsed.query or parsed.fragment:
        raise ExternalAuthError(f"{field_name} must not include query or fragment")
    _validate_public_hostname(parsed.hostname, field_name=field_name)
    return candidate


def validate_oidc_issuer_url(issuer_url: str) -> str:
    return _validate_public_https_url(issuer_url, field_name="OIDC issuer URL")


def validate_oidc_endpoint_url(endpoint_url: str, *, field_name: str) -> str:
    return _validate_public_https_url(endpoint_url, field_name=field_name)


def validate_redirect_uri(
    redirect_uri: str | None,
    *,
    frontend_url: str | None,
    allowed_origins: list[str],
    default_path: str = "/auth/callback",
) -> str:
    if redirect_uri and redirect_uri.strip():
        candidate = redirect_uri.strip()
    elif frontend_url:
        candidate = f"{frontend_url.rstrip('/')}{default_path}"
    else:
        raise ExternalAuthError("redirect URI is required")

    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ExternalAuthError("invalid redirect URI")

    origin = f"{parsed.scheme}://{parsed.netloc}"
    normalized_allowed_origins = {item.rstrip("/") for item in allowed_origins if item}
    if origin not in normalized_allowed_origins:
        raise ExternalAuthError("redirect URI origin is not allowed")
    if parsed.path != default_path:
        raise ExternalAuthError(f"redirect URI path must be {default_path}")
    return f"{origin}{parsed.path}"


def build_authorization_url(
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    scopes: list[str],
    nonce: str | None = None,
    login_hint: str | None = None,
    prompt: str | None = None,
) -> str:
    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
    }
    if nonce:
        params["nonce"] = nonce
    if login_hint:
        params["login_hint"] = login_hint
    if prompt:
        params["prompt"] = prompt
    return f"{authorization_endpoint}?{urlencode(params)}"


async def fetch_discovery(issuer_url: str) -> dict[str, Any]:
    issuer = validate_oidc_issuer_url(issuer_url).rstrip("/")
    url = f"{issuer}/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS, follow_redirects=False) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise ExternalAuthError(f"OIDC discovery request failed: {exc}") from exc
        except ValueError as exc:
            raise ExternalAuthError("invalid OIDC discovery response") from exc
    if not isinstance(payload, dict):
        raise ExternalAuthError("invalid OIDC discovery response")
    required_keys = {"authorization_endpoint", "token_endpoint", "userinfo_endpoint"}
    missing = sorted(key for key in required_keys if key not in payload)
    if missing:
        raise ExternalAuthError(f"OIDC discovery is missing keys: {', '.join(missing)}")
    normalized = dict(payload)
    normalized["authorization_endpoint"] = validate_oidc_endpoint_url(
        str(payload["authorization_endpoint"]),
        field_name="OIDC authorization endpoint",
    )
    normalized["token_endpoint"] = validate_oidc_endpoint_url(
        str(payload["token_endpoint"]),
        field_name="OIDC token endpoint",
    )
    normalized["userinfo_endpoint"] = validate_oidc_endpoint_url(
        str(payload["userinfo_endpoint"]),
        field_name="OIDC userinfo endpoint",
    )
    if isinstance(payload.get("issuer"), str) and payload["issuer"].strip():
        discovered_issuer = validate_oidc_issuer_url(payload["issuer"]).rstrip("/")
        if discovered_issuer != issuer:
            raise ExternalAuthError("OIDC discovery issuer does not match configured issuer")
        normalized["issuer"] = discovered_issuer
    else:
        normalized["issuer"] = issuer
    return normalized


async def exchange_code_for_tokens(
    *,
    token_endpoint: str,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS, follow_redirects=False) as client:
        try:
            response = await client.post(
                token_endpoint,
                data=data,
                auth=(client_id, client_secret),
                headers={"Accept": "application/json"},
            )
            if response.status_code >= 400:
                response = await client.post(
                    token_endpoint,
                    data={
                        **data,
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                    headers={"Accept": "application/json"},
                )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise ExternalAuthError(f"OIDC token exchange failed: {exc}") from exc
        except ValueError as exc:
            raise ExternalAuthError("invalid token response") from exc
    if not isinstance(payload, dict):
        raise ExternalAuthError("invalid token response")
    if "access_token" not in payload:
        raise ExternalAuthError("OIDC provider did not return access_token")
    return payload


async def fetch_userinfo(*, userinfo_endpoint: str, access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS, follow_redirects=False) as client:
        try:
            response = await client.get(
                userinfo_endpoint,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise ExternalAuthError(f"OIDC userinfo request failed: {exc}") from exc
        except ValueError as exc:
            raise ExternalAuthError("invalid userinfo response") from exc
    if not isinstance(payload, dict):
        raise ExternalAuthError("invalid userinfo response")
    return payload


def identity_from_claims(claims: dict[str, Any]) -> ExternalIdentityClaims:
    subject = str(claims.get("sub") or "").strip()
    if not subject:
        raise ExternalAuthError("identity provider did not return subject")
    email = claims.get("email")
    if email is not None:
        email = str(email).strip()
    return ExternalIdentityClaims(
        subject=subject,
        email=email,
        email_verified=claims.get("email_verified"),
        display_name=claims.get("name"),
        avatar_url=claims.get("picture"),
        claims=claims,
    )


def resolve_google_credentials(settings: RuntimeSettings) -> tuple[str, str]:
    client_id = settings.google_client_id
    client_secret = settings.google_client_secret
    if not client_id or not client_secret:
        raise ExternalAuthError("Google sign-in is not configured")
    return client_id, client_secret


def resolve_enterprise_sso_client_secret(secret_ref: str) -> str:
    ref = secret_ref.strip()
    if not ref:
        raise ExternalAuthError("enterprise SSO client secret reference is required")
    if ref.startswith("env:"):
        env_name = ref[4:].strip()
    else:
        normalized = re.sub(r"[^A-Za-z0-9]+", "_", ref).strip("_").upper()
        env_name = f"RUHU_SSO_CLIENT_SECRET__{normalized}"
    value = os.getenv(env_name)
    if not value:
        raise ExternalAuthError(f"enterprise SSO client secret is not configured for {secret_ref}")
    return value
