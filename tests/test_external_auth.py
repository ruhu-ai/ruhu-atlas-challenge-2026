from __future__ import annotations

import asyncio

import httpx
import pytest

from ruhu.external_auth import (
    ExternalAuthError,
    exchange_code_for_tokens,
    fetch_discovery,
    fetch_userinfo,
    validate_oidc_issuer_url,
)


def test_validate_oidc_issuer_url_rejects_local_or_insecure_hosts() -> None:
    for candidate in (
        "http://sso.example.com",
        "https://localhost",
        "https://sso.internal",
        "https://10.0.0.15",
        "https://169.254.169.254",
        "https://user:pass@sso.example.com",
        "https://sso.example.com/callback?next=/admin",
    ):
        with pytest.raises(ExternalAuthError):
            validate_oidc_issuer_url(candidate)


def test_fetch_discovery_rejects_private_endpoints_and_redirects(monkeypatch) -> None:
    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {
                "issuer": "https://sso.example.com",
                "authorization_endpoint": "https://sso.example.com/oauth/authorize",
                "token_endpoint": "https://169.254.169.254/token",
                "userinfo_endpoint": "https://sso.example.com/oauth/userinfo",
            }

    class _FakeAsyncClient:
        def __init__(self, *, timeout: float, follow_redirects: bool) -> None:
            assert timeout == 10.0
            assert follow_redirects is False

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str) -> _FakeResponse:
            assert url == "https://sso.example.com/.well-known/openid-configuration"
            return _FakeResponse()

    monkeypatch.setattr("ruhu.external_auth.httpx.AsyncClient", _FakeAsyncClient)

    with pytest.raises(ExternalAuthError, match="OIDC token endpoint"):
        asyncio.run(fetch_discovery("https://sso.example.com"))


def test_exchange_code_for_tokens_disables_redirects_and_retries_with_client_secret_post(monkeypatch) -> None:
    post_calls: list[dict[str, object]] = []

    class _FakeResponse:
        def __init__(self, status_code: int, payload: dict[str, object]) -> None:
            self.status_code = status_code
            self._payload = payload

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    "request failed",
                    request=httpx.Request("POST", "https://sso.example.com/token"),
                    response=httpx.Response(self.status_code),
                )

        def json(self) -> dict[str, object]:
            return dict(self._payload)

    class _FakeAsyncClient:
        def __init__(self, *, timeout: float, follow_redirects: bool) -> None:
            assert timeout == 10.0
            assert follow_redirects is False

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, *, data=None, auth=None, headers=None) -> _FakeResponse:
            post_calls.append({"url": url, "data": data, "auth": auth, "headers": headers})
            if len(post_calls) == 1:
                return _FakeResponse(401, {"error": "invalid_client"})
            return _FakeResponse(200, {"access_token": "token-123", "token_type": "Bearer"})

    monkeypatch.setattr("ruhu.external_auth.httpx.AsyncClient", _FakeAsyncClient)

    payload = asyncio.run(
        exchange_code_for_tokens(
            token_endpoint="https://sso.example.com/token",
            code="code-123",
            redirect_uri="https://app.example.com/auth/callback",
            client_id="client-123",
            client_secret="secret-456",
        )
    )

    assert payload["access_token"] == "token-123"
    assert len(post_calls) == 2
    assert post_calls[0]["auth"] == ("client-123", "secret-456")
    assert post_calls[1]["auth"] is None
    assert post_calls[1]["data"] == {
        "grant_type": "authorization_code",
        "code": "code-123",
        "redirect_uri": "https://app.example.com/auth/callback",
        "client_id": "client-123",
        "client_secret": "secret-456",
    }


def test_fetch_userinfo_wraps_http_errors(monkeypatch) -> None:
    class _FakeAsyncClient:
        def __init__(self, *, timeout: float, follow_redirects: bool) -> None:
            assert timeout == 10.0
            assert follow_redirects is False

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url: str, *, headers=None):
            raise httpx.ConnectError(
                "network down",
                request=httpx.Request("GET", url, headers=headers),
            )

    monkeypatch.setattr("ruhu.external_auth.httpx.AsyncClient", _FakeAsyncClient)

    with pytest.raises(ExternalAuthError, match="OIDC userinfo request failed"):
        asyncio.run(
            fetch_userinfo(
                userinfo_endpoint="https://sso.example.com/userinfo",
                access_token="token-123",
            )
        )
