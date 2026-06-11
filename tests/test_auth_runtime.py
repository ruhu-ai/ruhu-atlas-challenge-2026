from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import types

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ruhu.api import build_default_app
from ruhu.db import build_session_factory
from ruhu.identity import IdentityStore, Organization, OrganizationMembership, User
from ruhu.identity_sqlalchemy import SQLAlchemyIdentityStore
from ruhu.runtime_config import RuntimeSettings
from ruhu.session_http import ACCESS_TOKEN_COOKIE_NAME, REFRESH_TOKEN_COOKIE_NAME

TEST_HS256_SECRET = "0123456789abcdef0123456789abcdef"


def _seed_identity_store(store: IdentityStore) -> None:
    user = store.save_user(
        User(
            user_id="user-1",
            email="owner@example.com",
            display_name="Owner",
        )
    )
    store.save_organization(Organization(organization_id="org-1", slug="acme", name="Acme"))
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
        OrganizationMembership(
            user_id=user.user_id,
            organization_id="org-2",
            role="analyst",
        )
    )


def _private_key_pem() -> str:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def _install_fake_secret_manager(monkeypatch, *, payload_by_name: dict[str, str]) -> None:
    google_module = types.ModuleType("google")
    cloud_module = types.ModuleType("google.cloud")
    secretmanager_module = types.ModuleType("google.cloud.secretmanager_v1")

    class FakeSecretManagerServiceClient:
        def access_secret_version(self, request: dict[str, str]):
            name = request["name"]
            payload = payload_by_name[name]
            return types.SimpleNamespace(
                payload=types.SimpleNamespace(data=payload.encode("utf-8"))
            )

    secretmanager_module.SecretManagerServiceClient = FakeSecretManagerServiceClient
    cloud_module.secretmanager_v1 = secretmanager_module
    google_module.cloud = cloud_module
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud_module)
    monkeypatch.setitem(sys.modules, "google.cloud.secretmanager_v1", secretmanager_module)


def test_build_default_app_uses_persistent_sqlalchemy_auth_runtime(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        runtime_database_url = postgres_database_url_factory()
        secret = TEST_HS256_SECRET
        _seed_identity_store(SQLAlchemyIdentityStore(build_session_factory(auth_database_url)))

        app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_database_url,
            auth_database_url=auth_database_url,
            auth_jwt_secret=secret,
            interpreter_name="sales",
        )
        assert app.state.identity_store is not None
        assert app.state.auth_service is not None
        assert app.state.tenant_identity_repositories is not None

        issued = app.state.auth_service.issue_browser_session(
            user_id="user-1",
            organization_id="org-1",
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            client.cookies.set(ACCESS_TOKEN_COOKIE_NAME, issued.access_token)
            client.cookies.set(REFRESH_TOKEN_COOKIE_NAME, issued.refresh_token)
            me_response = await client.get("/auth/me")
            assert me_response.status_code == 200
            session_id = me_response.json()["session_id"]
            persisted_cookies = dict(client.cookies)

        restarted_app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_database_url,
            auth_database_url=auth_database_url,
            auth_jwt_secret=secret,
            interpreter_name="sales",
        )
        restarted_transport = httpx.ASGITransport(app=restarted_app)
        async with httpx.AsyncClient(
            transport=restarted_transport,
            base_url="http://testserver",
            cookies=persisted_cookies,
        ) as restarted_client:
            me_response = await restarted_client.get("/auth/me")
            assert me_response.status_code == 200
            payload = me_response.json()
            assert payload["session_id"] == session_id
            assert payload["organization"]["organization_id"] == "org-1"

    asyncio.run(run())


def test_persistent_tenant_identity_repository_factory_scopes_sqlalchemy_data(
    postgres_database_url_factory,
) -> None:
    auth_database_url = postgres_database_url_factory()
    store = SQLAlchemyIdentityStore(build_session_factory(auth_database_url))
    _seed_identity_store(store)

    app = build_default_app(
        agent_root=Path(__file__).resolve().parent / "_fixtures" / "data" / "agents",
        database_url=postgres_database_url_factory(),
        auth_database_url=auth_database_url,
        auth_jwt_secret=TEST_HS256_SECRET,
        interpreter_name="sales",
    )

    factory = app.state.tenant_identity_repositories
    org_one_repo = factory.for_scope(organization_id="org-1")
    org_two_repo = factory.for_scope(organization_id="org-2")

    assert org_one_repo.get_organization() is not None
    assert org_one_repo.get_organization().organization_id == "org-1"
    assert org_one_repo.get_organization_membership("user-1").role == "admin"

    assert org_two_repo.get_organization() is not None
    assert org_two_repo.get_organization().organization_id == "org-2"
    assert org_two_repo.get_organization_membership("user-1").role == "analyst"


def test_build_default_app_supports_rs256_runtime_and_jwks(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        runtime_database_url = postgres_database_url_factory()
        _seed_identity_store(SQLAlchemyIdentityStore(build_session_factory(auth_database_url)))

        app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_database_url,
            runtime_settings=RuntimeSettings(
                auth_database_url=auth_database_url,
                auth_jwt_private_key_pem=_private_key_pem(),
                auth_jwt_active_kid="kid-rs256",
            ),
            interpreter_name="sales",
        )

        issued = app.state.auth_service.issue_browser_session(
            user_id="user-1",
            organization_id="org-1",
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            client.cookies.set(ACCESS_TOKEN_COOKIE_NAME, issued.access_token)
            client.cookies.set(REFRESH_TOKEN_COOKIE_NAME, issued.refresh_token)
            me_response = await client.get("/auth/me")
            assert me_response.status_code == 200
            assert me_response.json()["organization"]["organization_id"] == "org-1"

            jwks = await client.get("/.well-known/jwks.json")
            assert jwks.status_code == 200
            assert jwks.headers["cache-control"] == "public, max-age=300"
            keys = jwks.json()["keys"]
            assert len(keys) == 1
            assert keys[0]["kid"] == "kid-rs256"
            assert keys[0]["alg"] == "RS256"

    asyncio.run(run())


def test_build_default_app_supports_rs256_private_key_from_secret_manager(
    postgres_database_url_factory,
    monkeypatch,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        runtime_database_url = postgres_database_url_factory()
        _seed_identity_store(SQLAlchemyIdentityStore(build_session_factory(auth_database_url)))

        secret_version_name = "projects/ruhu-dev/secrets/jwt-private-key/versions/12"
        _install_fake_secret_manager(
            monkeypatch,
            payload_by_name={secret_version_name: _private_key_pem()},
        )

        app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_database_url,
            runtime_settings=RuntimeSettings(
                auth_database_url=auth_database_url,
                auth_jwt_private_key_secret_version=secret_version_name,
                auth_jwt_active_kid="kid-secret-manager",
            ),
            interpreter_name="sales",
        )

        issued = app.state.auth_service.issue_browser_session(
            user_id="user-1",
            organization_id="org-1",
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            client.cookies.set(ACCESS_TOKEN_COOKIE_NAME, issued.access_token)
            client.cookies.set(REFRESH_TOKEN_COOKIE_NAME, issued.refresh_token)
            me_response = await client.get("/auth/me")
            assert me_response.status_code == 200

            jwks = await client.get("/.well-known/jwks.json")
            assert jwks.status_code == 200
            assert jwks.json()["keys"][0]["kid"] == "kid-secret-manager"

    asyncio.run(run())


def test_build_default_app_rejects_hs256_when_asymmetric_tokens_are_required(
    postgres_database_url_factory,
) -> None:
    agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"

    try:
        build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            runtime_settings=RuntimeSettings(
                auth_database_url=postgres_database_url_factory(),
                environment="production",
                auth_require_asymmetric_tokens=True,
                auth_jwt_secret="legacy-hs256-secret-0123456789abcdef",
            ),
            interpreter_name="sales",
        )
    except ValueError as exc:
        assert "HS256 secret cannot be configured" in str(exc)
    else:
        raise AssertionError("expected build_default_app() to reject HS256 fallback in asymmetric mode")
