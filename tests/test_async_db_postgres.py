"""RP-2.4 step 2: first-ever real-Postgres tests for the async DB path.

Until the psycopg3-async migration, the eagerly-built asyncpg engine in app
tests could never connect (per-test schema URLs carry libpq ``?options=``
which asyncpg rejects) — so async RLS enforcement had NEVER been exercised.
These tests close that gap:

  (a) async engine connects with the full per-test-schema ``?options=`` URL
      and the search_path is applied;
  (b) RLS parity — an org-scoped row inserted as org-A is invisible to org-B
      through an AsyncSession under ``tenant_db_context``, visible to org-A,
      and visible to a superuser context;
  (c) statement_timeout is applied per connection (connect-event listener);
  (d) get_async_session commits on clean exit and rolls back on exception.

H9: the conftest engine tracker only sees sync engines — every async engine
created here is explicitly disposed (``close_async_engine`` in a ``finally``,
inside the same event loop that opened the connections).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import text

from ruhu.db import build_session_factory, tenant_db_context
from ruhu.db_async import close_async_engine, get_async_session, init_async_engine
from ruhu.db_models import ConversationRecord


def _schema_name_from_url(url: str) -> str:
    # URL has ?options=-csearch_path%3D<schema>
    marker = "search_path%3D"
    idx = url.find(marker)
    assert idx != -1, f"cannot extract schema from {url!r}"
    return url[idx + len(marker):].split("&")[0]


def _ensure_rls_app_role(session_factory, schema_name: str) -> str:
    """Create a NOSUPERUSER NOBYPASSRLS role for RLS tests + grant access.

    PostgreSQL superusers bypass RLS even under FORCE ROW LEVEL SECURITY.
    In production, Ruhu connects as a non-privileged role — in tests we
    usually connect as ``postgres`` (true superuser), so RLS never engages.
    ``SET LOCAL ROLE`` replicates the production identity model for the
    duration of a single transaction (same pattern as tests/test_tenant.py).
    """
    role_name = "ruhu_test_app_role"
    with session_factory.begin() as session:
        session.execute(text(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role_name}') THEN
                    CREATE ROLE {role_name} NOSUPERUSER NOBYPASSRLS NOINHERIT LOGIN PASSWORD 'test';
                END IF;
            END $$;
            """
        ))
        session.execute(text(f'GRANT USAGE ON SCHEMA "{schema_name}" TO {role_name}'))
        session.execute(text(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema_name}" TO {role_name}'
        ))
        session.execute(text(
            f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{schema_name}" TO {role_name}'
        ))
    return role_name


def _run_with_async_engine(url: str, coro_fn, **init_kwargs):
    """Init the async engine, run ``coro_fn``, and ALWAYS dispose (H9).

    Disposal happens inside the same event loop that opened the pool's
    connections — psycopg AsyncConnections are bound to their loop.
    """
    async def _go():
        init_async_engine(url, **init_kwargs)
        try:
            return await coro_fn()
        finally:
            await close_async_engine()

    return asyncio.run(_go())


def _make_conversation(conversation_id: str, organization_id: str | None) -> ConversationRecord:
    now = datetime.now(timezone.utc)
    return ConversationRecord(
        conversation_id=conversation_id,
        organization_id=organization_id,
        agent_id="g",
        agent_version_id="v",
        step_id="s",
        started_at=now,
        created_at=now,
        updated_at=now,
    )


# ── (a) options-URL connect + search_path ────────────────────────────────────

def test_async_engine_connects_with_options_url_and_applies_search_path(
    postgres_database_url_factory,
) -> None:
    url = postgres_database_url_factory()
    schema = _schema_name_from_url(url)

    async def _check() -> str:
        async with get_async_session() as session:
            return (await session.execute(text("SHOW search_path"))).scalar_one()

    search_path = _run_with_async_engine(url, _check)
    assert schema in search_path, (
        f"search_path {search_path!r} does not include per-test schema {schema!r}"
    )


# ── (b) RLS tenant-isolation parity ──────────────────────────────────────────

def test_async_rls_parity_cross_tenant_isolation(postgres_database_url_factory) -> None:
    """The defining multi-tenancy guarantee, now proven on the async path."""
    url = postgres_database_url_factory()
    # build_session_factory creates the tables AND installs the RLS policies.
    session_factory = build_session_factory(url)
    role_name = _ensure_rls_app_role(session_factory, _schema_name_from_url(url))

    # Seed both orgs via the sync path (superuser context bypasses RLS).
    with tenant_db_context(organization_id=None, user_id=None, is_superuser=True):
        with session_factory.begin() as session:
            session.add(_make_conversation("conv-org-a", "org-a"))
            session.add(_make_conversation("conv-org-b", "org-b"))

    async def _check() -> tuple[set[str], set[str], set[str]]:
        async def _visible_orgs(*, organization_id: str | None, is_superuser: bool) -> set[str]:
            with tenant_db_context(
                organization_id=organization_id,
                user_id="u",
                is_superuser=is_superuser,
            ):
                async with get_async_session() as session:
                    # Drop to the non-superuser role AFTER the after_begin
                    # hook applied set_config — RLS engages for the rest of
                    # the transaction.
                    await session.execute(text(f"SET LOCAL ROLE {role_name}"))
                    rows = (
                        await session.execute(
                            text("SELECT organization_id FROM conversations")
                        )
                    ).all()
            return {row.organization_id for row in rows}

        as_org_b = await _visible_orgs(organization_id="org-b", is_superuser=False)
        as_org_a = await _visible_orgs(organization_id="org-a", is_superuser=False)
        as_superuser = await _visible_orgs(organization_id=None, is_superuser=True)
        return as_org_a, as_org_b, as_superuser

    as_org_a, as_org_b, as_superuser = _run_with_async_engine(url, _check)
    assert as_org_b == {"org-b"}, f"RLS leak: org-b saw {as_org_b} via AsyncSession"
    assert as_org_a == {"org-a"}, f"RLS leak: org-a saw {as_org_a} via AsyncSession"
    assert as_superuser == {"org-a", "org-b"}, (
        f"superuser context must see all orgs, saw {as_superuser}"
    )


# ── (c) statement_timeout applied per connection ─────────────────────────────

def test_async_statement_timeout_applied(postgres_database_url_factory) -> None:
    url = postgres_database_url_factory()

    async def _check() -> str:
        async with get_async_session() as session:
            return (await session.execute(text("SHOW statement_timeout"))).scalar_one()

    value = _run_with_async_engine(url, _check, statement_timeout_ms=4500)
    assert value == "4500ms"


def test_async_zero_statement_timeout_leaves_server_default(
    postgres_database_url_factory,
) -> None:
    url = postgres_database_url_factory()

    async def _check() -> str:
        async with get_async_session() as session:
            return (await session.execute(text("SHOW statement_timeout"))).scalar_one()

    value = _run_with_async_engine(url, _check, statement_timeout_ms=0)
    assert value == "0"  # Postgres default: disabled


# ── (d) get_async_session commit/rollback semantics ──────────────────────────

def test_get_async_session_commits_on_clean_exit(postgres_database_url_factory) -> None:
    url = postgres_database_url_factory()
    build_session_factory(url)  # create tables + policies

    async def _go() -> int:
        async with get_async_session() as session:
            session.add(_make_conversation("conv-commit", None))
        # New session/transaction: the row must have been committed.
        async with get_async_session() as session:
            return (
                await session.execute(
                    text(
                        "SELECT count(*) FROM conversations "
                        "WHERE conversation_id = 'conv-commit'"
                    )
                )
            ).scalar_one()

    assert _run_with_async_engine(url, _go) == 1


def test_get_async_session_rolls_back_on_exception(postgres_database_url_factory) -> None:
    url = postgres_database_url_factory()
    build_session_factory(url)

    async def _go() -> int:
        class _Boom(RuntimeError):
            pass

        try:
            async with get_async_session() as session:
                session.add(_make_conversation("conv-rollback", None))
                await session.flush()
                raise _Boom("rollback me")
        except _Boom:
            pass
        async with get_async_session() as session:
            return (
                await session.execute(
                    text(
                        "SELECT count(*) FROM conversations "
                        "WHERE conversation_id = 'conv-rollback'"
                    )
                )
            ).scalar_one()

    assert _run_with_async_engine(url, _go) == 0
