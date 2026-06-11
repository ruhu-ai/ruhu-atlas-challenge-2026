from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4
from dataclasses import replace
import os

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from starlette.datastructures import URL

from ruhu.api import build_default_app, create_app
from ruhu.api_auth import AuthContextResolver
from ruhu.auth import AuthService, JWTCodec
from ruhu.identity import InMemoryIdentityStore, Organization, OrganizationMembership, User
from ruhu.identity_sqlalchemy import SQLAlchemyIdentityStore
from ruhu.kernel import ConversationKernel
from ruhu.registry import FileGraphRegistry
from ruhu.runtime_config import RuntimeSettings
from ruhu.session_http import ACCESS_TOKEN_COOKIE_NAME, REFRESH_COOKIE_PATH, REFRESH_TOKEN_COOKIE_NAME
from ruhu.db import build_session_factory
from ruhu.ticket_system import TicketSystemService
from ruhu.ticketing_providers import ProviderConnectionConfig, RemoteCase, TicketingProviderError, WebhookSyncResult

TEST_HS256_SECRET = "0123456789abcdef0123456789abcdef"
DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/ruhu_runtime_dev"


def _unused_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _base_test_database_url() -> str:
    return os.getenv("RUHU_TEST_DATABASE_URL") or os.getenv("RUHU_DATABASE_URL") or DEFAULT_TEST_DATABASE_URL


def _schema_database_url(base_url: str, schema_name: str) -> str:
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}options=-csearch_path%3D{schema_name}"


@dataclass
class AuthBrowserHarness:
    base_url: str
    app: object
    auth_service: AuthService
    identity_store: InMemoryIdentityStore

    def seed_organization(
        self,
        *,
        organization_id: str = "org-1",
        slug: str = "acme",
        name: str = "Acme Voice",
        domain: str | None = "acme.com",
    ) -> Organization:
        return self.identity_store.save_organization(
            Organization(
                organization_id=organization_id,
                slug=slug,
                name=name,
                domain=domain,
            )
        )

    def save_user(
        self,
        *,
        user_id: str,
        email: str,
        display_name: str | None = None,
        is_superuser: bool = False,
    ) -> User:
        return self.identity_store.save_user(
            User(
                user_id=user_id,
                email=email,
                display_name=display_name,
                is_superuser=is_superuser,
            )
        )

    def add_membership(
        self,
        *,
        user_id: str,
        organization_id: str = "org-1",
        role: str = "developer",
        is_account_owner: bool = False,
    ) -> OrganizationMembership:
        return self.identity_store.add_organization_membership(
            OrganizationMembership(
                user_id=user_id,
                organization_id=organization_id,
                role=role,
                is_account_owner=is_account_owner,
            )
        )

    def add_browser_session(self, page, *, user_id: str, organization_id: str = "org-1") -> None:
        issued = self.auth_service.issue_browser_session(
            user_id=user_id,
            organization_id=organization_id,
        )
        page.context.add_cookies(
            [
                {
                    "name": ACCESS_TOKEN_COOKIE_NAME,
                    "value": issued.access_token,
                    "url": self.base_url,
                    "httpOnly": True,
                    "sameSite": "Lax",
                },
                {
                    "name": REFRESH_TOKEN_COOKIE_NAME,
                    "value": issued.refresh_token,
                    "url": f"{self.base_url}/auth",
                    "httpOnly": True,
                    "sameSite": "Lax",
                },
            ]
        )

    def authorized_client(self, *, user_id: str, organization_id: str = "org-1") -> httpx.Client:
        issued = self.auth_service.issue_browser_session(
            user_id=user_id,
            organization_id=organization_id,
        )
        client = httpx.Client(base_url=self.base_url, follow_redirects=False)
        client.headers["Authorization"] = f"Bearer {issued.access_token}"
        return client

    def extract_dev_outbox_token(
        self,
        *,
        path: str,
        query_key: str = "token",
        entry_index: int = -1,
    ) -> str:
        entries = getattr(self.app.state, "email_outbox", None)
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


@dataclass
class TicketBrowserHarness:
    base_url: str
    app: object
    auth_service: AuthService
    identity_store: SQLAlchemyIdentityStore
    runtime_session_factory: object
    remote_cases: dict[str, RemoteCase]
    failures: dict[str, int]

    def seed_organization(
        self,
        *,
        organization_id: str = "org-1",
        slug: str = "acme",
        name: str = "Acme Voice",
        domain: str | None = "acme.com",
    ) -> Organization:
        return self.identity_store.save_organization(
            Organization(
                organization_id=organization_id,
                slug=slug,
                name=name,
                domain=domain,
            )
        )

    def save_user(
        self,
        *,
        user_id: str,
        email: str,
        display_name: str | None = None,
        is_superuser: bool = False,
    ) -> User:
        return self.identity_store.save_user(
            User(
                user_id=user_id,
                email=email,
                display_name=display_name,
                is_superuser=is_superuser,
            )
        )

    def add_membership(
        self,
        *,
        user_id: str,
        organization_id: str = "org-1",
        role: str = "developer",
        is_account_owner: bool = False,
    ) -> OrganizationMembership:
        return self.identity_store.add_organization_membership(
            OrganizationMembership(
                user_id=user_id,
                organization_id=organization_id,
                role=role,
                is_account_owner=is_account_owner,
            )
        )

    def add_browser_session(self, page, *, user_id: str, organization_id: str = "org-1") -> None:
        issued = self.auth_service.issue_browser_session(
            user_id=user_id,
            organization_id=organization_id,
        )
        page.context.add_cookies(
            [
                {
                    "name": ACCESS_TOKEN_COOKIE_NAME,
                    "value": issued.access_token,
                    "url": self.base_url,
                    "httpOnly": True,
                    "sameSite": "Lax",
                },
                {
                    "name": REFRESH_TOKEN_COOKIE_NAME,
                    "value": issued.refresh_token,
                    "url": f"{self.base_url}/auth",
                    "httpOnly": True,
                    "sameSite": "Lax",
                },
            ]
        )

    def authorized_client(self, *, user_id: str, organization_id: str = "org-1") -> httpx.Client:
        issued = self.auth_service.issue_browser_session(
            user_id=user_id,
            organization_id=organization_id,
        )
        client = httpx.Client(base_url=self.base_url, follow_redirects=False)
        client.headers["Authorization"] = f"Bearer {issued.access_token}"
        return client


@dataclass
class WidgetBrowserHarness:
    base_url: str
    app: object
    runtime_session_factory: object
    provider_secret: str

    def provider_client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            follow_redirects=False,
            headers={"X-Ruhu-Provider-Secret": self.provider_secret},
        )


@pytest.fixture
def auth_browser_harness(monkeypatch: pytest.MonkeyPatch) -> AuthBrowserHarness:
    graph_root = Path(__file__).resolve().parent / "_fixtures" / "data" / "graphs"
    identity_store = InMemoryIdentityStore()
    auth_service = AuthService(
        identity_store=identity_store,
        jwt_codec=JWTCodec(secret=TEST_HS256_SECRET),
    )
    port = _unused_tcp_port()
    base_url = f"http://127.0.0.1:{port}"

    async def fake_fetch_discovery(issuer_url: str) -> dict[str, str]:
        if issuer_url.rstrip("/") == "https://accounts.google.com":
            authorization_endpoint = f"{base_url}/__test__/authorize/google/google-invite"
        else:
            authorization_endpoint = f"{base_url}/__test__/authorize/oidc/sso-login"
        return {
            "authorization_endpoint": authorization_endpoint,
            "token_endpoint": f"{base_url}/__test__/token",
            "userinfo_endpoint": f"{base_url}/__test__/userinfo",
        }

    async def fake_exchange_code_for_tokens(**kwargs) -> dict[str, str]:
        code = str(kwargs.get("code") or "").strip()
        if not code:
            raise AssertionError("missing code for fake token exchange")
        return {"access_token": code}

    async def fake_fetch_userinfo(*, access_token: str, userinfo_endpoint: str) -> dict[str, object]:
        assert userinfo_endpoint == f"{base_url}/__test__/userinfo"
        profiles: dict[str, dict[str, object]] = {
            "google-invite": {
                "sub": "subject:googleinvite@example.com",
                "email": "googleinvite@example.com",
                "email_verified": True,
                "name": "Google Invite",
                "picture": "https://cdn.example.com/googleinvite.png",
            },
            "sso-login": {
                "sub": "subject:analyst@acme.com",
                "email": "analyst@acme.com",
                "email_verified": True,
                "name": "Analyst User",
                "picture": "https://cdn.example.com/analyst.png",
            },
        }
        profile = profiles.get(access_token)
        if profile is None:
            raise AssertionError(f"unexpected fake provider access token {access_token!r}")
        return profile

    monkeypatch.setattr("ruhu.api.resolve_google_credentials", lambda _settings: ("google-client-id", "google-client-secret"))
    monkeypatch.setattr("ruhu.api.resolve_enterprise_sso_client_secret", lambda _ref: "enterprise-secret")
    monkeypatch.setattr("ruhu.api.fetch_discovery", fake_fetch_discovery)
    monkeypatch.setattr("ruhu.api.exchange_code_for_tokens", fake_exchange_code_for_tokens)
    monkeypatch.setattr("ruhu.api.fetch_userinfo", fake_fetch_userinfo)

    inner_app = create_app(
        kernel=ConversationKernel(),
        graph_registry=FileGraphRegistry(graph_root),
        auth_resolver=AuthContextResolver(auth_service=auth_service),
        identity_store=identity_store,
        auth_service=auth_service,
        runtime_settings=RuntimeSettings(
            frontend_url=base_url,
            auth_allowed_redirect_origins=[base_url],
        ),
    )
    wrapper_app = FastAPI()

    @wrapper_app.get("/__test__/authorize/{provider}/{code}")
    def fake_authorize(provider: str, code: str, redirect_uri: str, state: str) -> RedirectResponse:
        del provider
        target = URL(redirect_uri).include_query_params(code=code, state=state)
        return RedirectResponse(str(target), status_code=307)

    wrapper_app.mount("/", inner_app)

    server = uvicorn.Server(
        uvicorn.Config(
            wrapper_app,
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url}/ready", timeout=0.25)
            if response.status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("timed out waiting for Playwright auth test server to start")

    try:
        yield AuthBrowserHarness(
            base_url=base_url,
            app=inner_app,
            auth_service=auth_service,
            identity_store=identity_store,
        )
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def ticket_browser_harness(monkeypatch: pytest.MonkeyPatch) -> TicketBrowserHarness:
    graph_root = Path(__file__).resolve().parent / "_fixtures" / "data" / "graphs"
    base_database_url = _base_test_database_url()
    admin_engine = create_engine(base_database_url, future=True)
    auth_schema = f"test_{uuid4().hex}"
    runtime_schema = f"test_{uuid4().hex}"
    for schema_name in (auth_schema, runtime_schema):
        with admin_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema_name}"'))

    auth_database_url = _schema_database_url(base_database_url, auth_schema)
    runtime_database_url = _schema_database_url(base_database_url, runtime_schema)
    runtime_session_factory = build_session_factory(runtime_database_url)
    identity_store = SQLAlchemyIdentityStore(build_session_factory(auth_database_url))
    remote_cases: dict[str, RemoteCase] = {}
    failures: dict[str, int] = {}

    class FakeTicketingAdapter:
        def __init__(self, config: ProviderConnectionConfig) -> None:
            self._config = config

        def _maybe_fail(self, action: str) -> None:
            remaining = int(failures.get(action, 0) or 0)
            if remaining <= 0:
                return
            failures[action] = remaining - 1
            raise TicketingProviderError(
                f"temporary {action} failure",
                provider=self._config.provider,
                status_code=503,
                retryable=True,
            )

        def health_check(self) -> dict[str, object]:
            self._maybe_fail("health_check")
            if not self._config.credentials_ref:
                raise TicketingProviderError("missing credentials_ref", provider=self._config.provider, status_code=400)
            return {"provider": self._config.provider, "status": "ok"}

        def create_case(
            self,
            *,
            title: str,
            description: str,
            priority: str | None = None,
            status: str | None = None,
            participant_email: str | None = None,
            participant_display: str | None = None,
            tags: list[str] | None = None,
            metadata: dict[str, object] | None = None,
        ) -> RemoteCase:
            del participant_email, participant_display
            self._maybe_fail("create_case")
            case_id = f"{self._config.provider}-remote-{len(remote_cases) + 1}"
            remote = RemoteCase(
                external_case_id=case_id,
                external_case_key=case_id.upper(),
                external_case_url=f"https://tickets.example.com/{case_id}",
                external_case_status=status or "open",
                external_case_priority=priority or "medium",
                payload={
                    "title": title,
                    "description": description,
                    "tags": list(tags or []),
                    "metadata": dict(metadata or {}),
                },
            )
            remote_cases[case_id] = remote
            return remote

        def fetch_case(self, external_case_id: str) -> RemoteCase | None:
            self._maybe_fail("fetch_case")
            return remote_cases.get(external_case_id)

        def search_cases(self, *, query: str, limit: int = 20) -> list[RemoteCase]:
            matches = [
                case
                for case in remote_cases.values()
                if query.lower() in case.external_case_id.lower()
                or query.lower() in str(case.payload.get("title") or "").lower()
            ]
            return matches[:limit]

        def add_comment(self, *, external_case_id: str, body: str, visibility: str) -> dict[str, object]:
            self._maybe_fail("add_comment")
            if external_case_id not in remote_cases:
                raise TicketingProviderError("unknown remote case", provider=self._config.provider, status_code=404)
            return {"commented": True, "body": body, "visibility": visibility}

        def transition_case(self, *, external_case_id: str, status_value: str) -> RemoteCase:
            self._maybe_fail("transition_case")
            remote = remote_cases.get(external_case_id)
            if remote is None:
                raise TicketingProviderError("unknown remote case", provider=self._config.provider, status_code=404)
            updated = replace(remote, external_case_status=status_value, payload={**remote.payload, "status": status_value})
            remote_cases[external_case_id] = updated
            return updated

        def parse_webhook(self, *, payload: dict[str, object], headers: dict[str, str] | None = None) -> WebhookSyncResult:
            del headers
            case_id = str(payload.get("external_case_id") or "").strip()
            if not case_id:
                raise TicketingProviderError("missing external_case_id", provider=self._config.provider, status_code=400)
            remote = remote_cases.get(case_id)
            status_value = str(payload.get("status") or (None if remote is None else remote.external_case_status) or "updated")
            if remote is not None:
                remote = replace(remote, external_case_status=status_value, payload={**remote.payload, **payload})
                remote_cases[case_id] = remote
            return WebhookSyncResult(
                event_type=str(payload.get("event_type") or "case_updated"),
                external_case_id=case_id,
                external_case_key=None if remote is None else remote.external_case_key,
                external_case_url=None if remote is None else remote.external_case_url,
                external_case_status=status_value,
                external_case_priority=None if remote is None else remote.external_case_priority,
                payload_snapshot=dict(payload),
            )

    monkeypatch.setattr(
        "ruhu.api.TicketSystemService",
        lambda session_factory: TicketSystemService(
            session_factory,
            adapter_builder=lambda config: FakeTicketingAdapter(config),
        ),
    )

    app = build_default_app(
        graph_root=graph_root,
        database_url=runtime_database_url,
        auth_database_url=auth_database_url,
        auth_jwt_secret=TEST_HS256_SECRET,
        interpreter_name="sales",
        runtime_settings=RuntimeSettings(
            database_url=runtime_database_url,
            auth_database_url=auth_database_url,
            auth_jwt_secret=TEST_HS256_SECRET,
            provider_shared_secret="provider-secret",
        ),
    )

    port = _unused_tcp_port()
    base_url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url}/ready", timeout=0.25)
            if response.status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("timed out waiting for Playwright ticket test server to start")

    try:
        yield TicketBrowserHarness(
            base_url=base_url,
            app=app,
            auth_service=app.state.auth_service,
            identity_store=identity_store,
            runtime_session_factory=runtime_session_factory,
            remote_cases=remote_cases,
            failures=failures,
        )
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        for schema_name in (runtime_schema, auth_schema):
            for attempt in range(3):
                try:
                    with admin_engine.begin() as conn:
                        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
                    break
                except OperationalError as exc:
                    if "deadlock detected" not in str(exc).lower() or attempt == 2:
                        raise
                    time.sleep(0.2 * (attempt + 1))
        admin_engine.dispose()


@pytest.fixture
def widget_browser_harness() -> WidgetBrowserHarness:
    graph_root = Path(__file__).resolve().parent / "_fixtures" / "data" / "graphs"
    base_database_url = _base_test_database_url()
    admin_engine = create_engine(base_database_url, future=True)
    auth_schema = f"test_{uuid4().hex}"
    runtime_schema = f"test_{uuid4().hex}"
    for schema_name in (auth_schema, runtime_schema):
        with admin_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema_name}"'))

    auth_database_url = _schema_database_url(base_database_url, auth_schema)
    runtime_database_url = _schema_database_url(base_database_url, runtime_schema)
    runtime_session_factory = build_session_factory(runtime_database_url)
    provider_secret = "widget-provider-secret"

    app = build_default_app(
        graph_root=graph_root,
        database_url=runtime_database_url,
        auth_database_url=auth_database_url,
        auth_jwt_secret=TEST_HS256_SECRET,
        interpreter_name="sales",
        runtime_settings=RuntimeSettings(
            database_url=runtime_database_url,
            auth_database_url=auth_database_url,
            auth_jwt_secret=TEST_HS256_SECRET,
            provider_shared_secret=provider_secret,
            livekit_server_url="ws://127.0.0.1:7880",
            livekit_api_key="devkey",
            livekit_api_secret="0123456789abcdef0123456789abcdef",
            livekit_agent_name="ruhu-voice",
            livekit_room_prefix="widget",
            livekit_dispatch_strategy="room_config",
        ),
    )

    port = _unused_tcp_port()
    base_url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url}/ready", timeout=0.25)
            if response.status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("timed out waiting for Playwright widget test server to start")

    try:
        yield WidgetBrowserHarness(
            base_url=base_url,
            app=app,
            runtime_session_factory=runtime_session_factory,
            provider_secret=provider_secret,
        )
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        for schema_name in (runtime_schema, auth_schema):
            for attempt in range(3):
                try:
                    with admin_engine.begin() as conn:
                        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
                    break
                except OperationalError as exc:
                    if "deadlock detected" not in str(exc).lower() or attempt == 2:
                        raise
                    time.sleep(0.2 * (attempt + 1))
        admin_engine.dispose()
