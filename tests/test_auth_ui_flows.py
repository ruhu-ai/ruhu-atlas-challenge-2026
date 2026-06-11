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


def _seed_org(auth_service: AuthService) -> InMemoryIdentityStore:
    store = auth_service.identity_store
    store.save_organization(
        Organization(
            organization_id="org-1",
            slug="acme",
            name="Acme Voice",
            domain="acme.com",
        )
    )
    return store


def test_browser_style_invite_magic_link_flow_reaches_workspace_and_logout() -> None:
    async def run() -> None:
        app, auth_service = _build_auth_app()
        store = _seed_org(auth_service)
        admin = store.save_user(
            User(
                user_id="user-admin",
                email="admin@example.com",
                display_name="Admin",
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
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as admin_client:
            _authorize_client(admin_client, auth_service=auth_service, user_id=admin.user_id, organization_id="org-1")
            created_invitation = await admin_client.post(
                "/organization/invitations",
                json={"email": "invitee@example.com", "role": "developer"},
            )
            assert created_invitation.status_code == 200
            invitation_token = _extract_token_from_dev_outbox(app, path="/accept-invitation")

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as browser:
            invite_page = await browser.get("/accept-invitation", params={"token": invitation_token})
            assert invite_page.status_code == 200
            assert "Loading invitation..." in invite_page.text

            requested_magic_link = await browser.post(
                "/auth/magic-link/request",
                json={
                    "email": "invitee@example.com",
                    "invitation_token": invitation_token,
                },
            )
            assert requested_magic_link.status_code == 200
            magic_link_token = _extract_token_from_dev_outbox(app, path="/auth/magic-link")

            callback_page = await browser.get("/auth/magic-link", params={"token": magic_link_token})
            assert callback_page.status_code == 200
            assert 'const successRedirectPath = "/app";' in callback_page.text

            verified = await browser.post("/auth/magic-link/verify", json={"token": magic_link_token})
            assert verified.status_code == 200
            assert verified.json()["user"]["email"] == "invitee@example.com"

            workspace = await browser.get("/app")
            assert workspace.status_code == 200
            assert "Account, sessions, organization settings, members, and invitations." in workspace.text

            sessions = await browser.get("/auth/sessions")
            assert sessions.status_code == 200
            assert len(sessions.json()) == 1

            logout = await browser.delete("/auth/sessions/current")
            assert logout.status_code == 204

            logged_out = await browser.get("/app")
            assert logged_out.status_code == 307
            assert logged_out.headers["location"] == "/login"

    asyncio.run(run())


def test_browser_style_google_and_sso_flows_reach_workspace(monkeypatch) -> None:
    async def run() -> None:
        app, auth_service = _build_auth_app()
        store = _seed_org(auth_service)
        admin = store.save_user(
            User(
                user_id="user-admin",
                email="admin@example.com",
                display_name="Admin",
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

        oauth_profile = {"email": "googleinvite@example.com"}

        async def fake_fetch_discovery(_issuer_url: str) -> dict[str, str]:
            return {
                "authorization_endpoint": "https://idp.example.com/authorize",
                "token_endpoint": "https://idp.example.com/token",
                "userinfo_endpoint": "https://idp.example.com/userinfo",
            }

        async def fake_exchange_code_for_tokens(**_kwargs) -> dict[str, str]:
            return {"access_token": "provider-access-token"}

        async def fake_fetch_userinfo(*, access_token: str, userinfo_endpoint: str) -> dict[str, object]:
            assert access_token == "provider-access-token"
            assert userinfo_endpoint == "https://idp.example.com/userinfo"
            current_email = oauth_profile["email"]
            return {
                "sub": f"subject:{current_email}",
                "email": current_email,
                "email_verified": True,
                "name": current_email.split("@", 1)[0].title(),
                "picture": "https://cdn.example.com/avatar.png",
            }

        monkeypatch.setattr("ruhu.routes.auth_sessions.resolve_google_credentials", lambda _settings: ("google-client-id", "google-client-secret"))
        monkeypatch.setattr("ruhu.routes.auth_sessions.resolve_enterprise_sso_client_secret", lambda _ref: "enterprise-secret")
        monkeypatch.setattr("ruhu.routes.auth_sessions.fetch_discovery", fake_fetch_discovery)
        monkeypatch.setattr("ruhu.routes.organization.fetch_discovery", fake_fetch_discovery)
        monkeypatch.setattr("ruhu.routes.auth_sessions.exchange_code_for_tokens", fake_exchange_code_for_tokens)
        monkeypatch.setattr("ruhu.routes.auth_sessions.fetch_userinfo", fake_fetch_userinfo)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as admin_client:
            _authorize_client(admin_client, auth_service=auth_service, user_id=admin.user_id, organization_id="org-1")
            created_invitation = await admin_client.post(
                "/organization/invitations",
                json={"email": "googleinvite@example.com", "role": "developer"},
            )
            assert created_invitation.status_code == 200
            invitation_token = _extract_token_from_dev_outbox(app, path="/accept-invitation")

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as google_browser:
            google_callback_page = await google_browser.get(
                "/auth/callback",
                params={"code": "google-code", "state": "placeholder"},
            )
            assert google_callback_page.status_code == 200
            assert 'const successRedirectPath = "/app";' in google_callback_page.text

            google_start = await google_browser.post(
                "/auth/oauth/google/start",
                json={
                    "invitation_token": invitation_token,
                    "redirect_uri": "http://testserver/auth/callback",
                },
            )
            assert google_start.status_code == 200
            google_state = parse_qs(urlparse(google_start.json()["authorization_url"]).query)["state"][0]

            google_callback = await google_browser.post(
                "/auth/oauth/callback",
                json={
                    "code": "google-code",
                    "state": google_state,
                    "redirect_uri": "http://testserver/auth/callback",
                },
            )
            assert google_callback.status_code == 200
            assert google_callback.json()["user"]["email"] == "googleinvite@example.com"

            google_workspace = await google_browser.get("/app")
            assert google_workspace.status_code == 200
            assert "Invitations" in google_workspace.text

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as admin_client:
            _authorize_client(admin_client, auth_service=auth_service, user_id=admin.user_id, organization_id="org-1")
            created_sso_config = await admin_client.put(
                "/auth/sso/config",
                json={
                    "issuer_url": "https://sso.example.com",
                    "client_id": "enterprise-client-id",
                    "client_secret_ref": "env:RUHU_SSO_CLIENT_SECRET__ACME_OIDC",
                    "allowed_domains": ["acme.com"],
                    "scopes": ["openid", "profile", "email"],
                    "is_active": True,
                    "enforce_sso": True,
                    "jit_provisioning_enabled": True,
                },
            )
            assert created_sso_config.status_code == 200

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as sso_browser:
            oauth_profile["email"] = "jit-user@acme.com"

            sso_start = await sso_browser.post(
                "/auth/oauth/sso/start",
                json={
                    "email": "jit-user@acme.com",
                    "redirect_uri": "http://testserver/auth/callback",
                },
            )
            assert sso_start.status_code == 200
            sso_state = parse_qs(urlparse(sso_start.json()["authorization_url"]).query)["state"][0]

            sso_callback_page = await sso_browser.get(
                "/auth/callback",
                params={"code": "sso-code", "state": sso_state},
            )
            assert sso_callback_page.status_code == 200
            assert 'const successRedirectPath = "/app";' in sso_callback_page.text

            sso_callback = await sso_browser.post(
                "/auth/oauth/callback",
                json={
                    "code": "sso-code",
                    "state": sso_state,
                    "redirect_uri": "http://testserver/auth/callback",
                },
            )
            assert sso_callback.status_code == 200
            assert sso_callback.json()["user"]["email"] == "jit-user@acme.com"

            sso_workspace = await sso_browser.get("/app")
            assert sso_workspace.status_code == 200
            assert "Organization Settings" in sso_workspace.text

    asyncio.run(run())


def test_workspace_management_endpoints_backing_app_forms(monkeypatch) -> None:
    async def run() -> None:
        async def fake_fetch_discovery(_issuer_url: str) -> dict[str, str]:
            return {
                "authorization_endpoint": "https://idp.example.com/authorize",
                "token_endpoint": "https://idp.example.com/token",
                "userinfo_endpoint": "https://idp.example.com/userinfo",
            }

        monkeypatch.setattr("ruhu.routes.auth_sessions.fetch_discovery", fake_fetch_discovery)
        monkeypatch.setattr("ruhu.routes.organization.fetch_discovery", fake_fetch_discovery)

        app, auth_service = _build_auth_app()
        store = _seed_org(auth_service)
        admin = store.save_user(
            User(
                user_id="user-admin",
                email="admin@example.com",
                display_name="Admin",
                timezone="UTC",
                language="en",
            )
        )
        member = store.save_user(
            User(
                user_id="user-member",
                email="member@example.com",
                display_name="Member",
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
                user_id=member.user_id,
                organization_id="org-1",
                role="analyst",
            )
        )
        auth_service.link_external_identity(
            ExternalIdentity(
                user_id=admin.user_id,
                organization_id="org-1",
                provider_type="google",
                provider_key="google",
                subject="google-admin",
                email="admin@example.com",
            )
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(client, auth_service=auth_service, user_id=admin.user_id, organization_id="org-1")

            workspace = await client.get("/app")
            assert workspace.status_code == 200

            updated_profile = await client.patch(
                "/auth/me",
                json={
                    "display_name": "Ijidai",
                    "avatar_url": "https://cdn.example.com/admin.png",
                    "timezone": "Africa/Lagos",
                    "language": "en-NG",
                    "preferences": {"theme": "warm", "default_view": "workspace"},
                },
            )
            assert updated_profile.status_code == 200
            assert updated_profile.json()["user"]["display_name"] == "Ijidai"
            assert updated_profile.json()["user"]["preferences"] == {
                "theme": "warm",
                "default_view": "workspace",
            }

            identities = await client.get("/auth/external-identities")
            assert identities.status_code == 200
            assert identities.json()[0]["provider_type"] == "google"

            updated_org = await client.patch(
                "/organization",
                json={
                    "name": "Acme AI",
                    "domain": "acme.ai",
                    "email": "hello@acme.ai",
                    "phone": "+2348000000000",
                    "icon_url": "https://cdn.example.com/acme.png",
                    "description": "Voice and browser agents",
                    "brand_color": "#d97706",
                    "settings": {"support_email": "ops@acme.ai"},
                    "metadata": {"industry": "healthcare"},
                },
            )
            assert updated_org.status_code == 200
            assert updated_org.json()["name"] == "Acme AI"
            assert updated_org.json()["settings"] == {"support_email": "ops@acme.ai"}
            assert updated_org.json()["metadata"] == {"industry": "healthcare"}

            sso_saved = await client.put(
                "/auth/sso/config",
                json={
                    "issuer_url": "https://sso.example.com",
                    "client_id": "enterprise-client-id",
                    "client_secret_ref": "env:RUHU_SSO_CLIENT_SECRET__ORG",
                    "allowed_domains": ["acme.ai"],
                    "scopes": ["openid", "profile", "email"],
                    "is_active": True,
                    "enforce_sso": False,
                    "jit_provisioning_enabled": True,
                },
            )
            assert sso_saved.status_code == 200
            assert sso_saved.json()["allowed_domains"] == ["acme.ai"]

            members = await client.get("/organization/members")
            assert members.status_code == 200
            assert len(members.json()) == 2

            updated_member = await client.patch(
                "/organization/members/user-member",
                json={"role": "developer"},
            )
            assert updated_member.status_code == 200
            assert updated_member.json()["role"] == "developer"

            member_sessions = await client.get("/organization/members/user-member/sessions")
            assert member_sessions.status_code == 200
            assert member_sessions.json() == []

            created_invitation = await client.post(
                "/organization/invitations",
                json={"email": "newhire@example.com", "role": "analyst"},
            )
            assert created_invitation.status_code == 200

            invitations = await client.get("/organization/invitations")
            assert invitations.status_code == 200
            assert len(invitations.json()) == 1

            revoked_invitation = await client.delete(
                f"/organization/invitations/{invitations.json()[0]['invitation_id']}"
            )
            assert revoked_invitation.status_code == 204

            sso_disabled = await client.delete("/auth/sso/config")
            assert sso_disabled.status_code == 204

            org_sessions_revoked = await client.post("/organization/auth/revoke-sessions")
            assert org_sessions_revoked.status_code == 200
            assert org_sessions_revoked.json()["organization_id"] == "org-1"

    asyncio.run(run())


def test_internal_admin_management_endpoints_backing_console() -> None:
    async def run() -> None:
        app, auth_service = _build_auth_app()
        store = _seed_org(auth_service)
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
                role="developer",
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
            _authorize_client(client, auth_service=auth_service, user_id=superuser.user_id, organization_id="org-1")

            admin_page = await client.get("/internal/admin")
            assert admin_page.status_code == 200
            assert "Ruhu Internal Admin" in admin_page.text
            assert "Classifier Diagnostics" in admin_page.text

            health = await client.get("/internal/platform/health")
            assert health.status_code == 200
            assert health.json()["user_count"] == 2

            diagnostics = await client.get("/internal/auth/diagnostics")
            assert diagnostics.status_code == 200
            assert diagnostics.json()["issuer"] == "ruhu"

            organizations = await client.get("/internal/organizations")
            assert organizations.status_code == 200
            assert organizations.json()[0]["organization_id"] == "org-1"

            users = await client.get("/internal/users")
            assert users.status_code == 200
            assert len(users.json()) == 2

            identities = await client.get("/internal/users/user-member/external-identities")
            assert identities.status_code == 200
            assert identities.json()[0]["provider_type"] == "google"

            promoted = await client.post("/internal/users/user-member/promote-superuser")
            assert promoted.status_code == 200
            assert promoted.json()["is_superuser"] is True

            revoked = await client.post("/internal/users/user-member/revoke-superuser")
            assert revoked.status_code == 200
            assert revoked.json()["is_superuser"] is False

    asyncio.run(run())
