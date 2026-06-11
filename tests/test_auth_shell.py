from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from ruhu.api import build_default_app
from ruhu.api_auth import AuthContextResolver, extract_bearer_token
from ruhu.auth import AuthService, AuthenticationError, JWTCodec, TokenExpiredError
from ruhu.identity import InMemoryIdentityStore, Organization, OrganizationMembership, SessionAuditContext, User
from ruhu.runtime_config import RuntimeSettings
from ruhu.session_http import ACCESS_TOKEN_COOKIE_NAME, REFRESH_TOKEN_COOKIE_NAME

TEST_HS256_SECRET = "0123456789abcdef0123456789abcdef"


def build_auth_service() -> tuple[AuthService, InMemoryIdentityStore]:
    store = InMemoryIdentityStore()
    user = store.save_user(
        User(
            user_id="user-1",
            email="Owner@Example.com ",
            display_name="Owner",
            avatar_url="https://cdn.example.com/owner.png",
            timezone="Africa/Lagos",
            language="en-NG",
        )
    )
    store.save_organization(
        Organization(
            organization_id="org-1",
            slug="acme",
            name="Acme",
            domain="acme.example.com",
            email="team@acme.example.com",
            phone="+2348000000000",
            icon_url="https://cdn.example.com/acme-icon.png",
            description="Acme customer workspace",
            brand_color="#1254ff",
        )
    )
    store.save_organization(Organization(organization_id="org-2", slug="other", name="Other"))
    store.add_organization_membership(
        OrganizationMembership(
            user_id=user.user_id,
            organization_id="org-1",
            role="admin",
            is_account_owner=True,
        )
    )
    store.add_organization_membership(
        OrganizationMembership(user_id=user.user_id, organization_id="org-2", role="analyst")
    )
    service = AuthService(identity_store=store, jwt_codec=JWTCodec(secret=TEST_HS256_SECRET))
    return service, store


def build_test_app(database_url: str, service: AuthService):
    agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
    return build_default_app(
        agent_root=agent_root_path,
        database_url=database_url,
        interpreter_name="sales",
        auth_resolver=AuthContextResolver(auth_service=service),
        runtime_settings=RuntimeSettings(auth_allowed_redirect_origins=["http://testserver"]),
    )


def _extract_token_from_dev_outbox(app, *, path: str, query_key: str = "token") -> str:
    entries = getattr(app.state, "email_outbox", None)
    assert entries is not None
    assert len(entries) > 0
    entry = entries[-1]
    for candidate in filter(None, [entry.html_content, entry.text_content]):
        for part in str(candidate).split():
            parsed = urlparse(part.strip('"\'>)'))
            values = parse_qs(parsed.query).get(query_key)
            if parsed.path == path and values:
                return values[0]
    raise AssertionError(f"no {query_key} found for path {path}")


def _set_browser_session(client: httpx.AsyncClient, issued) -> None:
    client.cookies.set(ACCESS_TOKEN_COOKIE_NAME, issued.access_token)
    client.cookies.set(REFRESH_TOKEN_COOKIE_NAME, issued.refresh_token)


def test_extract_bearer_token() -> None:
    assert extract_bearer_token(None) is None
    assert extract_bearer_token("Basic abc123") is None
    assert extract_bearer_token("Bearer token-123") == "token-123"


def test_issue_session_and_authenticate_access_token() -> None:
    service, _ = build_auth_service()
    issued = service.issue_session(user_id="user-1", organization_id="org-1")
    principal = service.authenticate_access_token(issued.access_token)
    assert principal.user.user_id == "user-1"
    assert principal.organization.organization_id == "org-1"
    assert principal.organization_role == "admin"
    assert principal.is_account_owner is True
    assert principal.user.email == "owner@example.com"


def test_expired_access_token_is_rejected() -> None:
    service, _ = build_auth_service()
    issued = service.issue_session(
        user_id="user-1",
        organization_id="org-1",
        ttl=timedelta(seconds=-1),
    )
    with pytest.raises(TokenExpiredError):
        service.authenticate_access_token(issued.access_token)


def test_mixed_case_email_is_normalized_for_magic_link_lookup() -> None:
    service, _ = build_auth_service()
    issued = service.request_magic_link(
        email=" OWNER@EXAMPLE.COM ",
        organization_id="org-1",
    )
    assert issued.challenge.email == "owner@example.com"


def test_deactivated_user_cannot_request_magic_link() -> None:
    service, store = build_auth_service()
    user = store.get_user("user-1")
    assert user is not None
    store.save_user(user.model_copy(update={"is_active": False}))
    with pytest.raises(AuthenticationError):
        service.request_magic_link(email="owner@example.com", organization_id="org-1")


def test_org_auth_revocation_expires_existing_tokens() -> None:
    service, store = build_auth_service()
    issued = service.issue_session(user_id="user-1", organization_id="org-1")
    organization = store.get_organization("org-1")
    assert organization is not None
    revoked_after_epoch = int(issued.session.issued_at.timestamp()) + 1
    store.save_organization(
        organization.model_copy(
            update={
                "settings": {
                    **organization.settings,
                    "auth_revoked_after_epoch": revoked_after_epoch,
                }
            }
        )
    )
    with pytest.raises(AuthenticationError):
        service.authenticate_access_token(issued.access_token)


def test_magic_link_request_requires_organization_for_multi_org_user(postgres_database_url_factory) -> None:
    async def run() -> None:
        service, _ = build_auth_service()
        app = build_test_app(postgres_database_url_factory(), service)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/auth/magic-link/request",
                json={
                    "email": "owner@example.com",
                },
            )
            assert response.status_code == 200
            assert response.json() == {
                "message": "If the sign-in request is valid, a sign-in link has been issued.",
                "delivery": {
                    "transport": "dev_outbox",
                    "delivery_id": None,
                    "status": "queued",
                    "dev_outbox_entry_id": None,
                },
            }
            assert getattr(app.state, "email_outbox", []) == []

    asyncio.run(run())


def test_magic_link_verify_sets_browser_cookies_and_supports_cookie_auth(postgres_database_url_factory) -> None:
    async def run() -> None:
        service, _ = build_auth_service()
        app = build_test_app(postgres_database_url_factory(), service)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            requested = await client.post(
                "/auth/magic-link/request",
                json={
                    "email": "owner@example.com",
                    "organization_id": "org-1",
                },
            )
            assert requested.status_code == 200
            assert requested.json()["delivery"]["transport"] == "dev_outbox"
            response = await client.post(
                "/auth/magic-link/verify",
                json={"token": _extract_token_from_dev_outbox(app, path="/auth/magic-link")},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["organization"]["organization_id"] == "org-1"

            set_cookies = response.headers.get_list("set-cookie")
            assert any(
                cookie.startswith(f"{ACCESS_TOKEN_COOKIE_NAME}=")
                and "HttpOnly" in cookie
                and "Path=/" in cookie
                for cookie in set_cookies
            )
            assert any(
                cookie.startswith(f"{REFRESH_TOKEN_COOKIE_NAME}=")
                and "HttpOnly" in cookie
                and "Path=/" in cookie
                for cookie in set_cookies
            )

            me_response = await client.get("/auth/me")
            assert me_response.status_code == 200
            me_payload = me_response.json()
            assert me_payload["session_id"] == payload["session_id"]
            assert "project" not in me_payload

    asyncio.run(run())


def test_auth_refresh_accepts_refresh_cookie_even_with_invalid_access_cookie(postgres_database_url_factory) -> None:
    async def run() -> None:
        service, _ = build_auth_service()
        issued = service.issue_browser_session(
            user_id="user-1",
            organization_id="org-1",
        )
        app = build_test_app(postgres_database_url_factory(), service)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/auth/refresh",
                headers={
                    "Cookie": (
                        f"{ACCESS_TOKEN_COOKIE_NAME}=not-a-jwt; "
                        f"{REFRESH_TOKEN_COOKIE_NAME}={issued.refresh_token}"
                    )
                },
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["organization"]["organization_id"] == "org-1"
            assert "project" not in payload

    asyncio.run(run())


def test_auth_refresh_reuse_revokes_browser_session(postgres_database_url_factory) -> None:
    async def run() -> None:
        service, _ = build_auth_service()
        app = build_test_app(postgres_database_url_factory(), service)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            issued = service.issue_browser_session(user_id="user-1", organization_id="org-1")
            _set_browser_session(client, issued)
            original_refresh_token = issued.refresh_token
            assert original_refresh_token is not None

            refresh_response = await client.post("/auth/refresh")
            assert refresh_response.status_code == 200

            reuse_response = await client.post(
                "/auth/refresh",
                json={"refresh_token": original_refresh_token},
            )
            assert reuse_response.status_code == 401
            assert reuse_response.json()["detail"] == "refresh token reuse detected"

            me_response = await client.get("/auth/me")
            assert me_response.status_code == 401

    asyncio.run(run())


def test_auth_logout_clears_browser_session(postgres_database_url_factory) -> None:
    async def run() -> None:
        service, _ = build_auth_service()
        app = build_test_app(postgres_database_url_factory(), service)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            issued = service.issue_browser_session(user_id="user-1", organization_id="org-1")
            _set_browser_session(client, issued)

            logout_response = await client.post("/auth/logout")
            assert logout_response.status_code == 204

            me_response = await client.get("/auth/me")
            assert me_response.status_code == 401

    asyncio.run(run())


def test_admin_can_revoke_organization_sessions_from_security_endpoint(postgres_database_url_factory) -> None:
    async def run() -> None:
        service, _ = build_auth_service()
        # Anchor to real time: the JWT codec verifies exp against the wall
        # clock (auth.py jwt.decode verify_exp), so an absolute frozen date
        # rots once it falls outside the token TTL.
        issued_at = datetime.now(timezone.utc).replace(microsecond=0)
        service.now_provider = lambda: issued_at
        app = build_test_app(postgres_database_url_factory(), service)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            issued = service.issue_browser_session(
                user_id="user-1",
                organization_id="org-1",
                audit=SessionAuditContext(occurred_at=issued_at),
            )
            _set_browser_session(client, issued)

            revoked_at = issued_at + timedelta(seconds=10)
            service.now_provider = lambda: revoked_at

            revoke_response = await client.post("/organization/auth/revoke-sessions")
            assert revoke_response.status_code == 200
            revoke_payload = revoke_response.json()
            assert revoke_payload["organization_id"] == "org-1"
            assert revoke_payload["auth_revoked_after_epoch"] == int(revoked_at.timestamp())

            me_response = await client.get("/auth/me")
            assert me_response.status_code == 401
            assert me_response.json()["detail"] == "session expired. please sign in again"

    asyncio.run(run())


def test_auth_me_requires_token(postgres_database_url_factory) -> None:
    async def run() -> None:
        service, _ = build_auth_service()
        app = build_test_app(postgres_database_url_factory(), service)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/auth/me")
            assert response.status_code == 401
            assert response.json()["detail"] == "authentication required"

    asyncio.run(run())


def test_cross_org_header_is_rejected(postgres_database_url_factory) -> None:
    async def run() -> None:
        service, _ = build_auth_service()
        issued = service.issue_session(user_id="user-1", organization_id="org-1")
        app = build_test_app(postgres_database_url_factory(), service)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(
                "/auth/me",
                headers={
                    "Authorization": f"Bearer {issued.access_token}",
                    "X-Ruhu-Organization-Id": "org-2",
                },
            )
            assert response.status_code == 403
            assert "requested organization" in response.json()["detail"]

    asyncio.run(run())
