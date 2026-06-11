from __future__ import annotations

import hashlib
import os
import secrets
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from ruhu.db import build_session_factory, resolve_database_url
from ruhu.heuristics import register_interpreter_factory

# Register keyword interpreters for tests that exercise API/routing
# behavior by interpreter name.  Production code does not ship these.
from tests._fixtures.interpreters import (
    sales_interpreter,
    support_triage_interpreter,
)

register_interpreter_factory("sales", sales_interpreter)
register_interpreter_factory("support_triage", support_triage_interpreter)


# Track every SQLAlchemy engine ``build_session_factory`` creates so we can
# dispose them between tests — without this the suite exhausts the postgres
# connection limit (default 100) after ~3 tests, since each test's
# ``build_session_factory(database_url)`` builds an engine with pool_size=20
# + max_overflow=40 and tests don't dispose it themselves. Errors surface as
# ``FATAL: sorry, too many clients already`` against arbitrary downstream
# tests (phone_number_registry, sentiment_worker, tenant, etc.).
import ruhu.db as _ruhu_db  # noqa: E402
from sqlalchemy.engine import Engine as _Engine  # noqa: E402

_TEST_ENGINES: list[_Engine] = []
_orig_build_engine = _ruhu_db.build_engine


def _tracking_build_engine(*args, **kwargs):
    engine = _orig_build_engine(*args, **kwargs)
    _TEST_ENGINES.append(engine)
    return engine


_ruhu_db.build_engine = _tracking_build_engine


@pytest.fixture(autouse=True)
def _dispose_engines_after_test():
    yield
    while _TEST_ENGINES:
        engine = _TEST_ENGINES.pop()
        try:
            engine.dispose()
        except Exception:
            pass


# ``ruhu.livekit_worker._load_runtime_env_files()`` (called from
# ``livekit_worker.main``) reads ``.env.development.local`` / ``.env.local``
# / ``.env.development`` / ``.env`` into ``os.environ`` with
# ``override=False``. Once the worker-CLI tests exercise that code path,
# vars like ``RUHU_AUTH_JWT_SECRET`` and ``RUHU_PROVIDER_SHARED_SECRET``
# leak into the rest of the suite. Downstream tests that build a default
# app without explicit auth then unexpectedly get auth_enabled=True and
# start rejecting unauthenticated synthetic-channel requests with 503.
#
# Snapshot RUHU_* env vars per test and restore them on teardown so each
# test sees the env it was launched with — same isolation pytest's
# ``monkeypatch.setenv`` provides, but applied to env mutations from
# arbitrary in-process code paths.
@pytest.fixture(autouse=True)
def _isolate_ruhu_env_per_test():
    snapshot = {k: v for k, v in os.environ.items() if k.startswith("RUHU_")}
    try:
        yield
    finally:
        current = {k for k in os.environ if k.startswith("RUHU_")}
        for key in current - set(snapshot):
            os.environ.pop(key, None)
        for key, value in snapshot.items():
            os.environ[key] = value


DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/ruhu_runtime_dev"
# JWT secret shared between test fixtures - must be at least 32 bytes for HS256
TEST_JWT_SECRET = "test_secret_key_for_jwt_signing_needs_32bytes!"


def _base_test_database_url() -> str:
    return (
        os.getenv("RUHU_TEST_DATABASE_URL")
        or os.getenv("RUHU_DATABASE_URL")
        or DEFAULT_TEST_DATABASE_URL
    )


def _schema_database_url(base_url: str, schema_name: str) -> str:
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}options=-csearch_path%3D{schema_name}"


@pytest.fixture
def credential_cipher():
    """Return a deterministic ``FernetCipher`` for tests that need one.

    Uses a fixed key so assertions about ``primary_key_id_hex`` are stable
    across runs — a different test fixture would need a different key.
    Do NOT use this key for anything outside of tests.
    """
    from ruhu.tools.cipher import FernetCipher

    # 32 zero bytes, url-safe base64 encoded.  Deterministic; do not deploy.
    _TEST_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    return FernetCipher(primary=_TEST_KEY)


@pytest.fixture
def postgres_database_url_factory() -> Iterator[Callable[[], str]]:
    base_url = resolve_database_url(database_url=_base_test_database_url())
    admin_engine = create_engine(base_url, future=True)
    created_schemas: list[str] = []

    def factory() -> str:
        schema_name = f"test_{uuid4().hex}"
        with admin_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema_name}"'))
        created_schemas.append(schema_name)
        return _schema_database_url(base_url, schema_name)

    try:
        yield factory
    finally:
        for schema_name in reversed(created_schemas):
            for attempt in range(3):
                try:
                    with admin_engine.begin() as conn:
                        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
                    break
                except OperationalError as exc:
                    if 'deadlock detected' not in str(exc).lower() or attempt == 2:
                        raise
                    time.sleep(0.2 * (attempt + 1))
        admin_engine.dispose()


def make_widget_publishable_key(
    database_url: str,
    *,
    agent_id: str,
    organization_id: str,
    allowed_origins: list[str] | None = None,
    environment: str = "test",
) -> str:
    """Insert a publishable key directly into the DB and return plaintext.

    Widget-session tests use this to bypass admin auth (which would require a
    full authenticated session to hit `POST /api-keys/publishable`).  The key
    binds to a single agent and organization, matching production semantics.

    Returns the plaintext ``pk_test_…`` token that callers pass as the
    ``publishable_key`` field on ``POST /public/widget/sessions``.
    """
    from ruhu.db_models import ApiKeyRecord

    plaintext = f"pk_{environment}_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    session_factory = build_session_factory(database_url)
    with session_factory.begin() as session:
        session.add(
            ApiKeyRecord(
                key_id=str(uuid4()),
                organization_id=organization_id,
                name="test widget key",
                key_hash=key_hash,
                key_prefix=plaintext[:12],
                is_active=True,
                created_at=datetime.now(timezone.utc),
                key_type="publishable",
                agent_id=agent_id,
                allowed_origins=list(allowed_origins or []),
                environment=environment,
            )
        )
    return plaintext


@pytest.fixture
def test_db_urls(postgres_database_url_factory, auth_database_url_factory) -> tuple[str, str]:
    """Create both runtime and auth database URLs and return them together."""
    runtime_url = postgres_database_url_factory()
    auth_url = auth_database_url_factory()
    return runtime_url, auth_url


@pytest.fixture
def auth_database_url_factory() -> Callable[[], str]:
    """Create isolated auth database schemas for tests (same as runtime DB for simplicity)."""
    # In tests, auth DB = runtime DB (separate in production)
    base_url = resolve_database_url(database_url=_base_test_database_url())
    admin_engine = create_engine(base_url, future=True)
    created_schemas: list[str] = []

    def factory() -> str:
        schema_name = f"auth_{uuid4().hex}"
        with admin_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema_name}"'))
        created_schemas.append(schema_name)
        return _schema_database_url(base_url, schema_name)

    try:
        yield factory
    finally:
        for schema_name in reversed(created_schemas):
            try:
                with admin_engine.begin() as conn:
                    conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
            except OperationalError:
                pass
        admin_engine.dispose()


def _test_private_key_pem() -> str:
    """RSA private key for JWT signing in tests (deterministic, do NOT use in production)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    # Generate deterministic key for stable test token assertions
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode("utf-8")


def _make_auth_service(auth_db_url: str) -> "AuthService":
    """Internal helper to create AuthService with a given database URL."""
    from ruhu.auth import AuthService, JWTCodec
    from ruhu.identity_sqlalchemy import SQLAlchemyIdentityStore

    session_factory = build_session_factory(auth_db_url)

    # Ensure identity tables exist
    from ruhu.db_models import Base

    engine = create_engine(auth_db_url, future=True)
    Base.metadata.create_all(engine)
    engine.dispose()

    identity_store = SQLAlchemyIdentityStore(session_factory)
    jwt_codec = JWTCodec(secret=TEST_JWT_SECRET)
    return AuthService(
        identity_store=identity_store,
        jwt_codec=jwt_codec,
    )


@pytest.fixture
def auth_service(test_db_urls) -> "AuthService":
    """Create AuthService configured for tests using shared test databases."""
    _, auth_db_url = test_db_urls
    return _make_auth_service(auth_db_url)


@pytest.fixture
def superuser_auth_headers(auth_service: "AuthService") -> dict[str, str]:
    """Return HTTP headers with valid superuser JWT token."""
    from ruhu.identity import Organization, OrganizationMembership, SessionAuditContext, User

    # Create superuser organization and user
    org_id = f"test_org_{uuid4().hex}"
    org = auth_service.identity_store.save_organization(
        Organization(organization_id=org_id, name="Test Org", slug="test-org")
    )
    user = auth_service.identity_store.save_user(
        User(
            user_id=f"test_superuser_{uuid4().hex}",
            email="superuser@test.local",
            is_superuser=True,
        )
    )
    auth_service.identity_store.add_organization_membership(
        OrganizationMembership(
            organization_id=org.organization_id,
            user_id=user.user_id,
            role="admin",
        )
    )

    # Issue token
    issued = auth_service.issue_browser_session(
        user_id=user.user_id,
        organization_id=org.organization_id,
        audit=SessionAuditContext(),
    )
    return {"Authorization": f"Bearer {issued.access_token}"}


@pytest.fixture
def user_auth_headers(auth_service: "AuthService") -> dict[str, str]:
    """Return HTTP headers with valid regular user JWT token."""
    from ruhu.identity import Organization, OrganizationMembership, SessionAuditContext, User

    # Create regular user organization and user
    org_id = f"user_org_{uuid4().hex}"
    org = auth_service.identity_store.save_organization(
        Organization(organization_id=org_id, name="User Org", slug="user-org")
    )
    user = auth_service.identity_store.save_user(
        User(
            user_id=f"test_user_{uuid4().hex}",
            email="user@test.local",
            is_superuser=False,
        )
    )
    auth_service.identity_store.add_organization_membership(
        OrganizationMembership(
            organization_id=org.organization_id,
            user_id=user.user_id,
            role="admin",
        )
    )

    # Issue token
    issued = auth_service.issue_browser_session(
        user_id=user.user_id,
        organization_id=org.organization_id,
        audit=SessionAuditContext(),
    )
    return {"Authorization": f"Bearer {issued.access_token}"}


# ─────────────────────────────────────────────────────────────────────────────
# WI-6 of doc 36: move-selection replay harness pytest fixtures.
#
# Tests opt into one of three modes:
#   - "deterministic" (default): kernel never invokes LLM move selection
#   - "recorded": replay a recorded fixture instead of calling the LLM
#   - "live":     call the real LLM (used only for opt-in integration tests)
#
# In P1 the kernel's ``_select_move`` is a stub that raises
# ``NotImplementedError``, so applying a ``"recorded"`` fixture against the
# real kernel still raises — that is the expected wired-but-not-active
# state.  P2+ replaces the stub with a real recorder/replayer pipeline.
# ─────────────────────────────────────────────────────────────────────────────


import json as _json_for_replay
import pathlib as _pathlib_for_replay


_REPLAY_FIXTURE_DIR = (
    _pathlib_for_replay.Path(__file__).resolve().parent
    / "fixtures"
    / "move_selection_replay"
)


@pytest.fixture()
def move_selection_replay_mode(request) -> str:
    """Replay mode for move-selection tests.

    Override per test by parametrizing this fixture indirectly.  The default
    is ``"deterministic"`` so the bulk of CI runs with no live LLM and no
    recorded fixtures.
    """
    return getattr(request, "param", "deterministic")


@pytest.fixture()
def move_selection_replay_fixture_dir() -> _pathlib_for_replay.Path:
    """Path to the bundled move-selection replay fixtures."""
    return _REPLAY_FIXTURE_DIR


@pytest.fixture()
def load_move_selection_replay_fixture(
    move_selection_replay_fixture_dir: _pathlib_for_replay.Path,
):
    """Return a loader callable that parses a fixture by filename."""
    from ruhu.schemas import MoveSelectionReplayRecord

    def _loader(filename: str) -> "MoveSelectionReplayRecord":  # type: ignore[name-defined]
        path = move_selection_replay_fixture_dir / filename
        payload = _json_for_replay.loads(path.read_text())
        return MoveSelectionReplayRecord.model_validate(payload)

    return _loader
