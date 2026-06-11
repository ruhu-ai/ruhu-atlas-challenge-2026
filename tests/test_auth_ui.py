from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from ruhu.api import create_app
from ruhu.api_auth import AuthContextResolver
from ruhu.composition import build_minimal_runtime
from ruhu.services.api_services import ApiServices
from ruhu.auth import AuthService, JWTCodec
from ruhu.identity import ExternalIdentity, InMemoryIdentityStore, Organization, OrganizationMembership, User
from ruhu.kernel import ConversationKernel
from ruhu.registry import FileAgentRegistry
from ruhu.runtime_config import RuntimeSettings

TEST_HS256_SECRET = "0123456789abcdef0123456789abcdef"


def _build_auth_app() -> tuple[object, AuthService]:
    agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
    identity_store = InMemoryIdentityStore()
    auth_service = AuthService(
        identity_store=identity_store,
        jwt_codec=JWTCodec(secret=TEST_HS256_SECRET),
    )
    app = create_app(
        build_minimal_runtime(
            kernel=ConversationKernel(),
            agent_registry=FileAgentRegistry(agent_root_path),
        ),
        ApiServices(
            auth_resolver=AuthContextResolver(auth_service=auth_service),
            identity_store=identity_store,
            auth_service=auth_service,
        ),
        settings=RuntimeSettings(auth_allowed_redirect_origins=["http://testserver"]),
    )
    return app, auth_service


def _authorize_client(client: httpx.AsyncClient, *, auth_service: AuthService, user_id: str, organization_id: str) -> None:
    issued = auth_service.issue_browser_session(
        user_id=user_id,
        organization_id=organization_id,
    )
    client.headers["Authorization"] = f"Bearer {issued.access_token}"


def _extract_token_from_dev_outbox(app, *, path: str, query_key: str = "token", entry_index: int = -1) -> str:
    entries = getattr(app.state, "email_outbox", None)
    assert entries is not None
    assert len(entries) > 0
    entry = entries[entry_index]
    for candidate in filter(None, [entry.html_content, entry.text_content]):
        for part in str(candidate).split():
            parsed = urlparse(part.strip('"\'>)'))
            values = parse_qs(parsed.query).get(query_key)
            if parsed.path == path and values:
                return values[0]
    raise AssertionError(f"no {query_key} found for path {path}")


def test_auth_html_routes_render_old_layout_with_shared_theme() -> None:
    async def run() -> None:
        app, _auth_service = _build_auth_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            login = await client.get("/login")
            assert login.status_code == 200
            assert "Log in to Ruhu AI" in login.text
            assert "Continue with Google" in login.text
            assert "Continue with Magic Link" in login.text
            assert "Continue with SSO" in login.text
            assert '--sans: "Inter", system-ui' in login.text
            assert "--primary: 14 82% 45%;" in login.text

            signup = await client.get("/signup")
            assert signup.status_code == 200
            assert "Invitation-only signup" in signup.text
            assert "Verifying invite token..." in signup.text
            assert "/auth/invite/validate" in signup.text

            accept = await client.get("/accept-invitation")
            assert accept.status_code == 200
            assert "Loading invitation..." in accept.text
            assert "Continue with Google" in accept.text
            assert "Send me a sign-in link" in accept.text

            magic_callback = await client.get("/auth/magic-link")
            assert magic_callback.status_code == 200
            assert "Signing you in" in magic_callback.text

            oauth_callback = await client.get("/auth/callback")
            assert oauth_callback.status_code == 200
            assert "Completing Sign-in" in oauth_callback.text

            tickets = await client.get("/tickets")
            assert tickets.status_code == 307
            assert tickets.headers["location"] == "/login"

    asyncio.run(run())


def test_invitation_validate_response_exposes_old_accept_page_metadata() -> None:
    async def run() -> None:
        app, auth_service = _build_auth_app()
        store = auth_service.identity_store

        inviter = store.save_user(
            User(
                user_id="user-inviter",
                email="owner@example.com",
                display_name="Ijidai",
            )
        )
        store.save_organization(
            Organization(
                organization_id="org-acme",
                slug="acme",
                name="Acme Voice",
            )
        )
        store.add_organization_membership(
            OrganizationMembership(
                user_id=inviter.user_id,
                organization_id="org-acme",
                role="admin",
                is_account_owner=True,
            )
        )
        issued = auth_service.create_organization_invitation(
            organization_id="org-acme",
            email="invitee@example.com",
            role="developer",
            invited_by_user_id=inviter.user_id,
            is_account_owner=False,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(
                "/auth/invite/validate",
                params={"token": issued.invitation_token},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["valid"] is True
            assert payload["email"] == "invitee@example.com"
            assert payload["organization_name"] == "Acme Voice"
            assert payload["invited_by_name"] == "Ijidai"
            assert payload["role"] == "developer"

    asyncio.run(run())


def test_google_start_accepts_explicitly_allowed_auth_callback_without_frontend_url(monkeypatch) -> None:
    async def run() -> None:
        async def fake_fetch_discovery(_issuer: str) -> dict[str, str]:
            return {"authorization_endpoint": "https://accounts.example.com/o/oauth2/v2/auth"}

        monkeypatch.setattr("ruhu.routes.auth_sessions.resolve_google_credentials", lambda _settings: ("client-id", "client-secret"))
        monkeypatch.setattr("ruhu.routes.auth_sessions.fetch_discovery", fake_fetch_discovery)
        monkeypatch.setattr("ruhu.routes.organization.fetch_discovery", fake_fetch_discovery)

        app, _auth_service = _build_auth_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/auth/oauth/google/start",
                json={"redirect_uri": "http://testserver/auth/callback"},
            )
            assert response.status_code == 200
            assert "http%3A%2F%2Ftestserver%2Fauth%2Fcallback" in response.json()["authorization_url"]

    asyncio.run(run())


def test_google_start_rejects_redirect_uri_origin_derived_only_from_request_host(monkeypatch) -> None:
    async def run() -> None:
        async def fake_fetch_discovery(_issuer: str) -> dict[str, str]:
            return {"authorization_endpoint": "https://accounts.example.com/o/oauth2/v2/auth"}

        monkeypatch.setattr("ruhu.routes.auth_sessions.resolve_google_credentials", lambda _settings: ("client-id", "client-secret"))
        monkeypatch.setattr("ruhu.routes.auth_sessions.fetch_discovery", fake_fetch_discovery)
        monkeypatch.setattr("ruhu.routes.organization.fetch_discovery", fake_fetch_discovery)

        app, _auth_service = _build_auth_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://attacker.example") as client:
            response = await client.post(
                "/auth/oauth/google/start",
                json={"redirect_uri": "http://attacker.example/auth/callback"},
            )
            assert response.status_code == 400
            assert response.json()["detail"] == "redirect URI origin is not allowed"

    asyncio.run(run())


def test_create_invitation_route_delivers_to_dev_outbox_without_exposing_token() -> None:
    async def run() -> None:
        app, auth_service = _build_auth_app()
        store = auth_service.identity_store
        admin = store.save_user(
            User(
                user_id="user-admin",
                email="admin@example.com",
                display_name="Admin",
            )
        )
        store.save_organization(
            Organization(
                organization_id="org-1",
                slug="acme",
                name="Acme Voice",
            )
        )
        store.add_organization_membership(
            OrganizationMembership(
                user_id=admin.user_id,
                organization_id="org-1",
                role="admin",
                is_account_owner=True,
            )
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(client, auth_service=auth_service, user_id="user-admin", organization_id="org-1")
            response = await client.post(
                "/organization/invitations",
                json={"email": "invitee@example.com", "role": "developer"},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["delivery"]["transport"] == "dev_outbox"
            assert "invitation_token" not in payload

            invitation_token = _extract_token_from_dev_outbox(app, path="/accept-invitation")
            validate = await client.get("/auth/invite/validate", params={"token": invitation_token})
            assert validate.status_code == 200
            assert validate.json()["email"] == "invitee@example.com"

    asyncio.run(run())


def test_magic_link_request_delivers_to_dev_outbox_without_exposing_token() -> None:
    async def run() -> None:
        app, auth_service = _build_auth_app()
        store = auth_service.identity_store
        user = store.save_user(
            User(
                user_id="user-1",
                email="owner@example.com",
                display_name="Owner",
            )
        )
        store.save_organization(
            Organization(
                organization_id="org-1",
                slug="acme",
                name="Acme Voice",
            )
        )
        store.add_organization_membership(
            OrganizationMembership(
                user_id=user.user_id,
                organization_id="org-1",
                role="admin",
                is_account_owner=True,
            )
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/auth/magic-link/request",
                json={"email": "owner@example.com", "organization_id": "org-1"},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["delivery"]["transport"] == "dev_outbox"
            assert "magic_link_token" not in payload

            token = _extract_token_from_dev_outbox(app, path="/auth/magic-link")
            verified = await client.post("/auth/magic-link/verify", json={"token": token})
            assert verified.status_code == 200
            assert verified.json()["user"]["email"] == "owner@example.com"

    asyncio.run(run())


def test_tickets_route_renders_for_authenticated_workspace() -> None:
    async def run() -> None:
        app, auth_service = _build_auth_app()
        store = auth_service.identity_store
        user = store.save_user(
            User(
                user_id="user-admin",
                email="admin@example.com",
                display_name="Admin",
            )
        )
        store.save_organization(
            Organization(
                organization_id="org-1",
                slug="acme",
                name="Acme Voice",
            )
        )
        store.add_organization_membership(
            OrganizationMembership(
                user_id=user.user_id,
                organization_id="org-1",
                role="admin",
                is_account_owner=True,
            )
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(client, auth_service=auth_service, user_id=user.user_id, organization_id="org-1")
            tickets = await client.get("/tickets")
            assert tickets.status_code == 200
            assert "Tickets" in tickets.text
            assert "Recent conversations handled by your agents." in tickets.text

    asyncio.run(run())


def test_patch_auth_me_updates_profile_settings() -> None:
    async def run() -> None:
        app, auth_service = _build_auth_app()
        store = auth_service.identity_store
        user = store.save_user(
            User(
                user_id="user-1",
                email="owner@example.com",
                display_name="Owner",
                timezone="UTC",
                language="en",
            )
        )
        store.save_organization(
            Organization(
                organization_id="org-1",
                slug="acme",
                name="Acme Voice",
            )
        )
        store.add_organization_membership(
            OrganizationMembership(
                user_id=user.user_id,
                organization_id="org-1",
                role="admin",
                is_account_owner=True,
            )
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(client, auth_service=auth_service, user_id="user-1", organization_id="org-1")
            response = await client.patch(
                "/auth/me",
                json={
                    "display_name": "Ijidai",
                    "timezone": "Africa/Lagos",
                    "language": "en-NG",
                    "preferences": {"theme": "warm"},
                },
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["user"]["display_name"] == "Ijidai"
            assert payload["user"]["timezone"] == "Africa/Lagos"
            assert payload["user"]["language"] == "en-NG"

            stored_user = store.get_user("user-1")
            assert stored_user is not None
            assert stored_user.preferences == {"theme": "warm"}

    asyncio.run(run())


def test_auth_external_identities_lists_linked_providers() -> None:
    async def run() -> None:
        app, auth_service = _build_auth_app()
        store = auth_service.identity_store
        user = store.save_user(
            User(
                user_id="user-1",
                email="owner@example.com",
                display_name="Owner",
            )
        )
        store.save_organization(
            Organization(
                organization_id="org-1",
                slug="acme",
                name="Acme Voice",
            )
        )
        store.add_organization_membership(
            OrganizationMembership(
                user_id=user.user_id,
                organization_id="org-1",
                role="admin",
                is_account_owner=True,
            )
        )
        auth_service.link_external_identity(
            ExternalIdentity(
                user_id="user-1",
                organization_id="org-1",
                provider_type="google",
                provider_key="google",
                subject="google-subject",
                email="owner@example.com",
            )
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(client, auth_service=auth_service, user_id="user-1", organization_id="org-1")
            response = await client.get("/auth/external-identities")
            assert response.status_code == 200
            payload = response.json()
            assert len(payload) == 1
            assert payload[0]["provider_type"] == "google"
            assert payload[0]["email"] == "owner@example.com"

    asyncio.run(run())


def test_internal_routes_require_superuser_and_list_platform_state() -> None:
    async def run() -> None:
        app, auth_service = _build_auth_app()
        store = auth_service.identity_store
        superuser = store.save_user(
            User(
                user_id="user-super",
                email="staff@ruhu.ai",
                display_name="Staff",
                is_superuser=True,
            )
        )
        member = store.save_user(
            User(
                user_id="user-member",
                email="member@example.com",
                display_name="Member",
            )
        )
        other_superuser = store.save_user(
            User(
                user_id="user-super-2",
                email="staff-two@ruhu.ai",
                display_name="Staff Two",
                is_superuser=True,
            )
        )
        store.save_organization(Organization(organization_id="org-1", slug="acme", name="Acme Voice"))
        store.add_organization_membership(
            OrganizationMembership(
                user_id=superuser.user_id,
                organization_id="org-1",
                role="admin",
                is_account_owner=True,
            )
        )
        store.add_organization_membership(
            OrganizationMembership(
                user_id=member.user_id,
                organization_id="org-1",
                role="analyst",
            )
        )
        store.add_organization_membership(
            OrganizationMembership(
                user_id=other_superuser.user_id,
                organization_id="org-1",
                role="admin",
            )
        )
        auth_service.link_external_identity(
            ExternalIdentity(
                user_id=member.user_id,
                organization_id="org-1",
                provider_type="google",
                provider_key="google",
                subject="member-google",
                email="member@example.com",
            )
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(client, auth_service=auth_service, user_id="user-member", organization_id="org-1")
            forbidden = await client.get("/internal/platform/health")
            assert forbidden.status_code == 403

            _authorize_client(client, auth_service=auth_service, user_id="user-super", organization_id="org-1")
            health = await client.get("/internal/platform/health")
            assert health.status_code == 200
            assert health.json()["user_count"] == 3
            assert health.json()["organization_count"] == 1

            organizations = await client.get("/internal/organizations")
            assert organizations.status_code == 200
            assert organizations.json()[0]["member_count"] == 3

            users = await client.get("/internal/users")
            assert users.status_code == 200
            assert any(item["is_superuser"] is True for item in users.json())

            identities = await client.get("/internal/users/user-member/external-identities")
            assert identities.status_code == 200
            assert identities.json()[0]["provider_type"] == "google"

            promoted = await client.post("/internal/users/user-member/promote-superuser")
            assert promoted.status_code == 200
            assert promoted.json()["is_superuser"] is True

            revoke_self = await client.post("/internal/users/user-super/revoke-superuser")
            assert revoke_self.status_code == 409

            revoked = await client.post("/internal/users/user-member/revoke-superuser")
            assert revoked.status_code == 200
            assert revoked.json()["is_superuser"] is False

    asyncio.run(run())


def test_authenticated_console_routes_render_and_redirect_cleanly() -> None:
    async def run() -> None:
        app, auth_service = _build_auth_app()
        store = auth_service.identity_store
        admin = store.save_user(
            User(
                user_id="user-admin",
                email="admin@example.com",
                display_name="Admin",
            )
        )
        superuser = store.save_user(
            User(
                user_id="user-super",
                email="staff@ruhu.ai",
                display_name="Staff",
                is_superuser=True,
            )
        )
        store.save_organization(
            Organization(
                organization_id="org-1",
                slug="acme",
                name="Acme Voice",
            )
        )
        store.add_organization_membership(
            OrganizationMembership(
                user_id=admin.user_id,
                organization_id="org-1",
                role="admin",
                is_account_owner=True,
            )
        )
        store.add_organization_membership(
            OrganizationMembership(
                user_id=superuser.user_id,
                organization_id="org-1",
                role="admin",
            )
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            unauthenticated = await client.get("/app")
            assert unauthenticated.status_code == 307
            assert unauthenticated.headers["location"] == "/login"

            account_redirect = await client.get("/account")
            assert account_redirect.status_code == 307
            assert account_redirect.headers["location"] == "/login"

            _authorize_client(client, auth_service=auth_service, user_id=admin.user_id, organization_id="org-1")

            workspace = await client.get("/app")
            assert workspace.status_code == 200
            assert "Account, sessions, organization settings, members, and invitations." in workspace.text
            assert "Profile" in workspace.text
            assert "Email Address" in workspace.text
            assert 'id="profile-email"' in workspace.text
            assert 'id="profile-preferences"' in workspace.text
            assert 'id="org-settings"' in workspace.text
            assert 'id="org-metadata"' in workspace.text
            assert "Only organization admins can create invitations." in workspace.text
            assert "Sessions" in workspace.text
            assert "Organization Settings" in workspace.text
            assert "Invitations" in workspace.text

            account = await client.get("/account")
            assert account.status_code == 307
            assert account.headers["location"] == "/app"

            forbidden_admin = await client.get("/internal/admin")
            assert forbidden_admin.status_code == 307
            assert forbidden_admin.headers["location"] == "/app"

            _authorize_client(client, auth_service=auth_service, user_id=superuser.user_id, organization_id="org-1")

            internal_admin = await client.get("/internal/admin")
            assert internal_admin.status_code == 200
            assert "Ruhu Internal Admin" in internal_admin.text
            assert "Platform diagnostics, tenant inspection, and superuser controls." in internal_admin.text
            assert "data-user-identities" in internal_admin.text
            assert "data-promote-superuser" in internal_admin.text

    asyncio.run(run())


def test_callback_pages_now_redirect_to_app_shell() -> None:
    async def run() -> None:
        app, _auth_service = _build_auth_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            magic_callback = await client.get("/auth/magic-link")
            assert magic_callback.status_code == 200
            assert 'const successRedirectPath = "/app";' in magic_callback.text
            assert "/playground" not in magic_callback.text

            oauth_callback = await client.get("/auth/callback")
            assert oauth_callback.status_code == 200
            assert 'const successRedirectPath = "/app";' in oauth_callback.text
            assert "/playground" not in oauth_callback.text

    asyncio.run(run())
