from __future__ import annotations

import importlib

from sqlalchemy import text

from ruhu.db import (
    RLSPolicyAuditFailure,
    RUNTIME_TENANT_RLS_EXEMPT_TABLES,
    RUNTIME_TENANT_RLS_TABLES,
    _compute_runtime_tenant_rls_tables,
    assert_rls_policies_healthy,
    build_session_factory,
    tenant_db_context,
)
from ruhu.db_models import Base
from ruhu.tenant import TenantScope, apply_tenant_session_context

_SQLALCHEMY_MODEL_MODULES = (
    "ruhu.db_models",
    "ruhu.attachments.sqlalchemy_models",
    "ruhu.billing.sqlalchemy_models",
    "ruhu.browser_tasks.sqlalchemy_models",
    "ruhu.analytics_tagging.sqlalchemy_models",
    "ruhu.knowledge.sqlalchemy_models",
    "ruhu.kpi.sqlalchemy_models",
    "ruhu.notifications.sqlalchemy_models",
    "ruhu.rules_sqlalchemy_models",
)


def test_apply_tenant_session_context_sets_postgres_session_config(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())

    with session_factory.begin() as session:
        apply_tenant_session_context(
            session,
            scope=TenantScope(organization_id="org-1"),
            user_id="user-1",
        )

        assert session.scalar(text("SELECT current_setting('app.current_organization_id', true)")) == "org-1"
        assert session.scalar(text("SELECT current_setting('app.current_user_id', true)")) == "user-1"


def test_tenant_db_context_applies_session_context_automatically(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())

    with tenant_db_context(organization_id="org-2", user_id="user-2", is_superuser=True):
        with session_factory.begin() as session:
            assert session.scalar(text("SELECT current_setting('app.current_organization_id', true)")) == "org-2"
            assert session.scalar(text("SELECT current_setting('app.current_user_id', true)")) == "user-2"
            assert session.scalar(text("SELECT current_setting('app.current_is_superuser', true)")) == "true"


def test_build_session_factory_installs_runtime_tenant_policies(postgres_database_url_factory) -> None:
    session_factory = build_session_factory(postgres_database_url_factory())

    with session_factory.begin() as session:
        rows = session.execute(
            text(
                """
                SELECT tablename, policyname
                FROM pg_policies
                WHERE schemaname = current_schema()
                ORDER BY tablename, policyname
                """
            )
        ).all()

    installed = {(row.tablename, row.policyname) for row in rows}
    expected = {
        (table_name, f"tenant_scope_{table_name}")
        for table_name in RUNTIME_TENANT_RLS_TABLES
    }
    assert expected.issubset(installed)


def test_runtime_tenant_rls_table_list_covers_non_exempt_org_scoped_tables() -> None:
    for module_name in _SQLALCHEMY_MODEL_MODULES:
        importlib.import_module(module_name)

    org_scoped_tables = {
        table_name
        for table_name, table in Base.metadata.tables.items()
        if "organization_id" in table.c
    }
    missing = sorted(
        table_name
        for table_name in org_scoped_tables
        if table_name not in RUNTIME_TENANT_RLS_TABLES
        and table_name not in RUNTIME_TENANT_RLS_EXEMPT_TABLES
    )

    assert missing == []


def _ensure_rls_app_role(session_factory, schema_name: str) -> str:
    """Create a NOSUPERUSER NOBYPASSRLS role for RLS tests + grant access.

    PostgreSQL superusers bypass RLS even under FORCE ROW LEVEL SECURITY.
    In production, Ruhu connects as a non-privileged role — in tests we
    usually connect as `postgres` (true superuser), so RLS never engages.
    These helpers replicate the production identity model for the duration
    of a single transaction via SET LOCAL ROLE.
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


def _schema_name_from_url(url: str) -> str:
    # URL has ?options=-csearch_path%3D<schema>
    marker = "search_path%3D"
    idx = url.find(marker)
    assert idx != -1, f"cannot extract schema from {url!r}"
    return url[idx + len(marker):].split("&")[0]


def test_rls_rejects_cross_tenant_reads(postgres_database_url_factory) -> None:
    """End-to-end: org_a cannot see org_b's conversations via RLS.

    This is the defining multi-tenancy guarantee. Without it, any query bug
    that omits an explicit WHERE organization_id filter becomes a data leak.
    RLS catches it at the database layer.
    """
    from datetime import datetime, timezone
    from ruhu.db_models import ConversationRecord

    url = postgres_database_url_factory()
    session_factory = build_session_factory(url)
    role_name = _ensure_rls_app_role(session_factory, _schema_name_from_url(url))

    # Seed both orgs (use superuser context to bypass RLS on insert)
    now = datetime.now(timezone.utc)
    with tenant_db_context(organization_id=None, user_id=None, is_superuser=True):
        with session_factory.begin() as session:
            for org_id in ("org-a", "org-b"):
                session.add(
                    ConversationRecord(
                        conversation_id=f"conv-{org_id}",
                        organization_id=org_id,
                        agent_id="g",
                        agent_version_id="v",
                        step_id="s",
                        started_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )

    # Query as org-a (via non-superuser role) — must see only org-a's row
    with tenant_db_context(organization_id="org-a", user_id="u-a", is_superuser=False):
        with session_factory.begin() as session:
            session.execute(text(f"SET LOCAL ROLE {role_name}"))
            rows = session.execute(
                text("SELECT conversation_id, organization_id FROM conversations")
            ).all()
    org_ids_visible = {r.organization_id for r in rows}
    assert org_ids_visible == {"org-a"}, (
        f"RLS leak: org-a saw conversations from {org_ids_visible}"
    )

    # Query as org-b — must see only org-b's row
    with tenant_db_context(organization_id="org-b", user_id="u-b", is_superuser=False):
        with session_factory.begin() as session:
            session.execute(text(f"SET LOCAL ROLE {role_name}"))
            rows = session.execute(
                text("SELECT conversation_id, organization_id FROM conversations")
            ).all()
    org_ids_visible = {r.organization_id for r in rows}
    assert org_ids_visible == {"org-b"}, (
        f"RLS leak: org-b saw conversations from {org_ids_visible}"
    )

    # Explicit attempt to read the other org's row via its ID — RLS must hide it
    with tenant_db_context(organization_id="org-a", user_id="u-a", is_superuser=False):
        with session_factory.begin() as session:
            session.execute(text(f"SET LOCAL ROLE {role_name}"))
            rows = session.execute(
                text(
                    "SELECT conversation_id FROM conversations "
                    "WHERE conversation_id = :cid"
                ),
                {"cid": "conv-org-b"},
            ).all()
    assert rows == [], "RLS leak: targeted cross-tenant read returned a row"


def test_rls_rejects_cross_tenant_writes(postgres_database_url_factory) -> None:
    """org-a cannot UPDATE or DELETE org-b's rows through RLS."""
    from datetime import datetime, timezone
    from ruhu.db_models import ConversationRecord

    url = postgres_database_url_factory()
    session_factory = build_session_factory(url)
    role_name = _ensure_rls_app_role(session_factory, _schema_name_from_url(url))

    now = datetime.now(timezone.utc)
    with tenant_db_context(organization_id=None, user_id=None, is_superuser=True):
        with session_factory.begin() as session:
            session.add(
                ConversationRecord(
                    conversation_id="conv-victim",
                    organization_id="org-b",
                    agent_id="g",
                    agent_version_id="v",
                    step_id="s",
                    started_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )

    # As org-a (non-superuser), attempt to UPDATE a row owned by org-b
    with tenant_db_context(organization_id="org-a", user_id="u-a", is_superuser=False):
        with session_factory.begin() as session:
            session.execute(text(f"SET LOCAL ROLE {role_name}"))
            result = session.execute(
                text(
                    "UPDATE conversations SET step_id = 'hijacked' "
                    "WHERE conversation_id = :cid"
                ),
                {"cid": "conv-victim"},
            )
            assert result.rowcount == 0, "RLS leak: cross-tenant UPDATE matched a row"

    # DELETE — same story, zero rows affected
    with tenant_db_context(organization_id="org-a", user_id="u-a", is_superuser=False):
        with session_factory.begin() as session:
            session.execute(text(f"SET LOCAL ROLE {role_name}"))
            result = session.execute(
                text("DELETE FROM conversations WHERE conversation_id = :cid"),
                {"cid": "conv-victim"},
            )
            assert result.rowcount == 0, "RLS leak: cross-tenant DELETE matched a row"

    # Verify the row is still intact under org-b's scope
    with tenant_db_context(organization_id="org-b", user_id="u-b", is_superuser=False):
        with session_factory.begin() as session:
            session.execute(text(f"SET LOCAL ROLE {role_name}"))
            rows = session.execute(
                text(
                    "SELECT step_id FROM conversations "
                    "WHERE conversation_id = :cid"
                ),
                {"cid": "conv-victim"},
            ).all()
    assert len(rows) == 1 and rows[0].step_id == "s", (
        "Victim row was modified despite RLS supposedly blocking cross-tenant writes"
    )


# ── RLS policy auto-derivation (single source of truth) ────────────────────────

def test_rls_tables_are_derived_from_base_metadata() -> None:
    """RUNTIME_TENANT_RLS_TABLES is computed from Base.metadata, not hardcoded.

    Implication: adding an ``organization_id`` column to any SQLAlchemy model
    (and ensuring the module gets imported by the sidecar loader) automatically
    enrols the table in RLS on the next startup.
    """
    derived = set(_compute_runtime_tenant_rls_tables())
    exposed = set(RUNTIME_TENANT_RLS_TABLES)
    assert derived == exposed, "Module-level RUNTIME_TENANT_RLS_TABLES must match derivation"

    # Every derived table has an organization_id column and is not exempt
    for table_name in derived:
        table = Base.metadata.tables[table_name]
        assert "organization_id" in table.c
        assert table_name not in RUNTIME_TENANT_RLS_EXEMPT_TABLES


def test_rls_tables_derivation_excludes_exempt_tables() -> None:
    """Identity/auth tables carved out of RLS must not appear in the derived list."""
    derived = set(_compute_runtime_tenant_rls_tables())
    for exempt in RUNTIME_TENANT_RLS_EXEMPT_TABLES:
        assert exempt not in derived, (
            f"Exempt table {exempt!r} leaked into RLS-required list"
        )


# ── Startup healthcheck: assert_rls_policies_healthy ──────────────────────────

def test_assert_rls_policies_healthy_passes_on_fresh_schema(
    postgres_database_url_factory,
) -> None:
    """A freshly built session factory installs policies — the healthcheck passes."""
    url = postgres_database_url_factory()
    # build_session_factory already calls assert_rls_policies_healthy internally;
    # if it raised, this line would never return.
    session_factory = build_session_factory(url)
    # Re-run explicitly for good measure
    engine = session_factory.kw["bind"]
    assert_rls_policies_healthy(engine)  # must not raise


def test_assert_rls_policies_healthy_detects_missing_policy(
    postgres_database_url_factory,
) -> None:
    """If a policy is dropped, the healthcheck raises RLSPolicyAuditFailure.

    Simulates a migration drift where someone altered the schema manually
    or a DDL got rolled back without the corresponding policy reinstall.
    """
    url = postgres_database_url_factory()
    session_factory = build_session_factory(url)
    engine = session_factory.kw["bind"]

    # Pick a known org-scoped table and drop its policy to simulate drift
    victim_table = "conversations"
    with engine.begin() as conn:
        conn.execute(text(
            f'DROP POLICY "tenant_scope_{victim_table}" ON "{victim_table}"'
        ))

    import pytest
    with pytest.raises(RLSPolicyAuditFailure) as excinfo:
        assert_rls_policies_healthy(engine)

    assert victim_table in str(excinfo.value), (
        f"Error message must name the offending table: {excinfo.value}"
    )
    assert "cross-tenant" in str(excinfo.value).lower(), (
        "Error message must signal security severity"
    )


def test_assert_rls_policies_healthy_ignores_exempt_tables(
    postgres_database_url_factory,
) -> None:
    """Identity/auth tables without policies don't cause the healthcheck to fail."""
    url = postgres_database_url_factory()
    session_factory = build_session_factory(url)
    engine = session_factory.kw["bind"]

    # Identity tables are intentionally NOT protected by tenant RLS policies.
    # The healthcheck must not flag them. (Verified by successful build above;
    # if it flagged them, build_session_factory would have raised.)
    with engine.begin() as conn:
        # Sanity: at least one identity table exists in the schema
        rows = conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
            " AND tablename = ANY(:names)"
        ), {"names": list(RUNTIME_TENANT_RLS_EXEMPT_TABLES)}).all()
        identity_tables_present = {r.tablename for r in rows}

    # If any identity table is present, confirm it has no tenant_scope policy
    # AND the healthcheck still passes (no raise above).
    if identity_tables_present:
        with engine.begin() as conn:
            policy_rows = conn.execute(text(
                "SELECT tablename FROM pg_policies WHERE schemaname = current_schema()"
                " AND policyname LIKE 'tenant_scope_%'"
            )).all()
            tables_with_policy = {r.tablename for r in policy_rows}
        assert not (identity_tables_present & tables_with_policy), (
            "Exempt identity tables should NOT have tenant_scope policies"
        )
