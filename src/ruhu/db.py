from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
import importlib
import logging
from pathlib import Path
import time
from typing import AsyncIterator, Iterable, Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import Pool

from .db_models import Base

logger = logging.getLogger(__name__)

_SYNC_ENGINE: "Engine | None" = None

_SIDECAR_SQLALCHEMY_MODULES = (
    ".rules_sqlalchemy_models",
    ".attachments.sqlalchemy_models",
    ".audit.store",
    ".billing.sqlalchemy_models",
    ".browser_tasks.sqlalchemy_models",
    ".capture.sqlalchemy_models",
    ".analytics_tagging.sqlalchemy_models",
    ".jobs.sqlalchemy_models",
    ".kpi.sqlalchemy_models",
    ".live_eval_sqlalchemy_models",
    ".notifications.sqlalchemy_models",
)
_SIDECAR_SQLALCHEMY_MODELS_LOADED = False

# Authentication and identity data is the only storage boundary that must
# survive a non-auth schema reset. Everything else is application-domain state
# that can be rebuilt from seeds, templates, or runtime activity.
AUTH_PRESERVED_TABLES = (
    "auth_refresh_families",
    "auth_sessions",
    "identity_api_keys",
    "identity_auth_challenges",
    "identity_enterprise_sso_configurations",
    "identity_external_identities",
    "identity_org_invitations",
    "identity_org_memberships",
    "identity_organizations",
    "identity_user_avatars",
    "identity_users",
)

NON_AUTH_RESET_IGNORED_TABLES = ("alembic_version",)

_CURRENT_DB_ORGANIZATION_ID: ContextVar[str | None] = ContextVar(
    "ruhu_current_db_organization_id",
    default=None,
)
_CURRENT_DB_USER_ID: ContextVar[str | None] = ContextVar(
    "ruhu_current_db_user_id",
    default=None,
)
_CURRENT_DB_IS_SUPERUSER: ContextVar[bool] = ContextVar(
    "ruhu_current_db_is_superuser",
    default=False,
)
_SESSION_CONTEXT_HOOKS_INSTALLED = False

# These tables carry organization identifiers but participate in identity or
# auth flows before a request tenant context exists, so runtime-scoped RLS would
# block bootstrap and login operations.
#
# This is a deliberate allowlist — a human must add a table here to carve it out
# of RLS. Everything else with an ``organization_id`` column is covered
# automatically by the derivation below.
RUNTIME_TENANT_RLS_EXEMPT_TABLES = (
    "auth_refresh_families",
    "auth_sessions",
    "identity_api_keys",
    "identity_auth_challenges",
    "identity_enterprise_sso_configurations",
    "identity_external_identities",
    "identity_org_invitations",
    "identity_org_memberships",
    "identity_organizations",
)


def _compute_runtime_tenant_rls_tables() -> tuple[str, ...]:
    """Return the authoritative list of org-scoped tables that require RLS.

    Derived from ``Base.metadata`` after all sidecar model modules are loaded,
    so adding a new SQLAlchemy model with an ``organization_id`` column
    automatically enrols the table in RLS on the next startup — no separate
    registration step, no silent drift.

    Carve-outs (identity/auth tables that RLS would block during bootstrap)
    live in ``RUNTIME_TENANT_RLS_EXEMPT_TABLES`` and are explicit by design.
    """
    _ensure_sidecar_sqlalchemy_models_loaded()
    return tuple(
        sorted(
            name
            for name, table in Base.metadata.tables.items()
            if "organization_id" in table.c
            and name not in RUNTIME_TENANT_RLS_EXEMPT_TABLES
        )
    )


def _compute_non_auth_schema_tables() -> tuple[str, ...]:
    """Return the rebuildable, non-auth application tables.

    The clean storage contract is:
    - preserve only authentication/identity tables in ``AUTH_PRESERVED_TABLES``
    - treat every other SQLAlchemy table as disposable application state
    """
    _ensure_sidecar_sqlalchemy_models_loaded()
    return tuple(
        sorted(
            name
            for name in Base.metadata.tables
            if name not in AUTH_PRESERVED_TABLES
        )
    )


def compute_non_auth_reset_drop_tables(live_table_names: Iterable[str]) -> tuple[str, ...]:
    """Return live tables that should be dropped during a non-auth reset.

    Preserve only auth/identity tables plus migration bookkeeping. Everything
    else is treated as rebuildable application state.
    """
    return tuple(
        sorted(
            name
            for name in set(live_table_names)
            if name not in AUTH_PRESERVED_TABLES and name not in NON_AUTH_RESET_IGNORED_TABLES
        )
    )


def __getattr__(name: str):
    # Module-level __getattr__: preserves the classic import style
    # ``from ruhu.db import RUNTIME_TENANT_RLS_TABLES`` while recomputing
    # from ``Base.metadata`` on each access (cheap — hot-path callers cache
    # the result themselves). Lazy access ensures tables registered after
    # this module loads (late imports) still participate.
    if name == "RUNTIME_TENANT_RLS_TABLES":
        return _compute_runtime_tenant_rls_tables()
    if name == "NON_AUTH_SCHEMA_TABLES":
        return _compute_non_auth_schema_tables()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def resolve_database_url(*, database_url: str | None = None) -> str:
    if database_url is None or not database_url.strip():
        raise ValueError("database_url is required")
    candidate = database_url.strip()
    lower = candidate.lower()
    if lower.startswith("postgres://"):
        return f"postgresql+psycopg://{candidate[len('postgres://'):]}"
    if lower.startswith("postgresql://"):
        return f"postgresql+psycopg://{candidate[len('postgresql://'):]}"
    if lower.startswith("postgresql+psycopg2://"):
        return f"postgresql+psycopg://{candidate[len('postgresql+psycopg2://'):]}"
    return candidate


def build_engine(
    database_url: str,
    *,
    pool_size: int = 20,
    max_overflow: int = 40,
    pool_recycle: int = 1800,
    pool_timeout: float = 30.0,
    statement_timeout_ms: int = 30_000,
) -> Engine:
    resolved_url = resolve_database_url(database_url=database_url)
    engine = create_engine(
        resolved_url,
        future=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_recycle=pool_recycle,
        pool_timeout=pool_timeout,
        pool_pre_ping=True,
    )
    # Apply statement_timeout per connection via an event listener rather than
    # connect_args["options"] — the latter clobbers the URL's ``options=``
    # parameter (e.g. ``options=-csearch_path=...`` used by test fixtures).
    if statement_timeout_ms > 0 and resolved_url.startswith("postgresql"):
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _set_statement_timeout(dbapi_connection, _):  # type: ignore[no-untyped-def]
            with dbapi_connection.cursor() as cursor:
                cursor.execute(f"SET statement_timeout = {int(statement_timeout_ms)}")
    logger.info(
        "sync DB engine created",
        extra={
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            "pool_recycle": pool_recycle,
            "pool_timeout": pool_timeout,
            "statement_timeout_ms": statement_timeout_ms,
        },
    )
    _install_pool_metrics(engine, pool_label="sync")
    _install_query_metrics(engine, pool_label="sync")
    return engine


def close_sync_engine() -> None:
    """Dispose the sync engine pool. Call in application shutdown."""
    global _SYNC_ENGINE
    if _SYNC_ENGINE is not None:
        _SYNC_ENGINE.dispose()
        _SYNC_ENGINE = None


def _install_pool_metrics(engine: Engine, *, pool_label: str) -> None:
    """Attach SQLAlchemy pool event listeners that update pool gauge metrics.

    Connection wait time is intentionally not exported here. SQLAlchemy's pool
    hooks fire after checkout, so a precise wait metric would require pool
    subclassing or eager checkout semantics that would distort normal request
    behaviour. We keep only gauges that reflect current pool pressure.
    """
    try:
        from .observability.metrics import db_pool_checked_out, db_pool_overflow
    except Exception:
        return

    @event.listens_for(engine.pool, "checkout")
    def _on_checkout(dbapi_conn: object, connection_record: object, connection_proxy: object) -> None:
        pool = engine.pool
        db_pool_checked_out.labels(pool=pool_label).set(pool.checkedout())
        db_pool_overflow.labels(pool=pool_label).set(pool.overflow())

    @event.listens_for(engine.pool, "connect")
    def _on_connect(dbapi_conn: object, connection_record: object) -> None:
        pool = engine.pool
        db_pool_checked_out.labels(pool=pool_label).set(pool.checkedout())
        db_pool_overflow.labels(pool=pool_label).set(pool.overflow())

    @event.listens_for(engine.pool, "checkin")
    def _on_checkin(dbapi_conn: object, connection_record: object) -> None:
        pool = engine.pool
        db_pool_checked_out.labels(pool=pool_label).set(pool.checkedout())
        db_pool_overflow.labels(pool=pool_label).set(pool.overflow())


def _install_query_metrics(engine: Engine, *, pool_label: str = "sync") -> None:
    """Attach cursor execute events to time individual queries."""
    try:
        from .observability.metrics import db_query_duration_seconds
    except Exception:
        return

    start_times: dict[int, float] = {}
    operations: dict[int, str] = {}

    def _classify_operation(statement: object) -> str:
        if not isinstance(statement, str):
            return "other"
        head = statement.lstrip().split(None, 1)
        if not head:
            return "other"
        op = head[0].lower()
        if op in {"select", "insert", "update", "delete"}:
            return op
        return "other"

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn: object, cursor: object, statement: object, parameters: object, context: object, executemany: object) -> None:
        start_times[id(cursor)] = time.monotonic()
        operations[id(cursor)] = _classify_operation(statement)

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn: object, cursor: object, statement: object, parameters: object, context: object, executemany: object) -> None:
        start = start_times.pop(id(cursor), None)
        operation = operations.pop(id(cursor), "other")
        if start is not None:
            db_query_duration_seconds.labels(pool=pool_label, operation=operation).observe(
                time.monotonic() - start
            )


def build_session_factory(
    database_url: str,
    *,
    pool_size: int = 20,
    max_overflow: int = 40,
    pool_recycle: int = 1800,
    pool_timeout: float = 30.0,
    statement_timeout_ms: int = 30_000,
) -> sessionmaker[Session]:
    global _SYNC_ENGINE
    _ensure_sidecar_sqlalchemy_models_loaded()
    engine = build_engine(
        resolve_database_url(database_url=database_url),
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_recycle=pool_recycle,
        pool_timeout=pool_timeout,
        statement_timeout_ms=statement_timeout_ms,
    )
    _SYNC_ENGINE = engine
    Base.metadata.create_all(engine)
    ensure_postgres_runtime_tenant_policies(engine)
    # Backstop: if any org-scoped table slipped through (model not registered,
    # migration added a column but Python side missed it, etc.) fail loudly
    # before accepting traffic rather than serve cross-tenant leaks.
    assert_rls_policies_healthy(engine)
    _install_session_context_hooks()
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


async def get_async_session() -> AsyncIterator["AsyncSession"]:
    """Compatibility wrapper re-exporting the async session dependency.

    Several route modules import ``get_async_session`` from ``ruhu.db``.
    The actual async session factory lives in ``ruhu.db_async``, which imports
    this module for shared context/RLS helpers, so the import stays lazy here
    to avoid a module cycle.
    """
    from .db_async import get_async_session as _get_async_session

    async with _get_async_session() as session:
        yield session


def run_migrations(database_url: str) -> None:
    _ensure_sidecar_sqlalchemy_models_loaded()
    resolved_database_url = resolve_database_url(database_url=database_url)
    engine = build_engine(resolved_database_url)
    if engine.dialect.name == "postgresql":
        # Fresh-start vs upgrade.  If the target DB/schema has no tables
        # yet, create the full schema from the current ORM definitions and
        # stamp the alembic history to head.  For existing databases, run
        # migrations normally.  This avoids the chicken-and-egg where
        # ORM-only tables (e.g. ``attachments``) aren't in the migration
        # chain but are altered by later migrations.
        if _is_postgres_schema_empty(engine):
            Base.metadata.create_all(engine)
            _stamp_postgres_schema_to_head(resolved_database_url)
        else:
            _upgrade_postgres_schema(resolved_database_url)
    else:
        Base.metadata.create_all(engine)
    ensure_postgres_runtime_tenant_policies(engine)
    assert_rls_policies_healthy(engine)
    engine.dispose()


def reset_non_auth_schema(database_url: str) -> dict[str, object]:
    """Drop and recreate all non-auth tables while preserving auth data."""
    _ensure_sidecar_sqlalchemy_models_loaded()
    resolved_database_url = resolve_database_url(database_url=database_url)
    engine = build_engine(resolved_database_url)
    try:
        if engine.dialect.name == "postgresql":
            dropped_tables = _reset_postgres_non_auth_schema(engine)
            Base.metadata.create_all(engine)
            _stamp_postgres_schema_to_head(resolved_database_url)
        else:
            dropped_tables = _reset_generic_non_auth_schema(engine)
            Base.metadata.create_all(engine)
        ensure_postgres_runtime_tenant_policies(engine)
        assert_rls_policies_healthy(engine)
        return {
            "database_url": resolved_database_url,
            "preserved_tables": AUTH_PRESERVED_TABLES,
            "dropped_tables": dropped_tables,
            "non_auth_schema_tables": _compute_non_auth_schema_tables(),
        }
    finally:
        engine.dispose()


def _is_postgres_schema_empty(engine: Engine) -> bool:
    """Return True if the configured schema has no user tables yet."""
    from sqlalchemy import text

    with engine.connect() as connection:
        count = connection.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = current_schema()"
            )
        ).scalar_one()
    return count == 0


def _reset_postgres_non_auth_schema(engine: Engine) -> tuple[str, ...]:
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = current_schema()
                """
            )
        ).all()
        live_tables = [str(row.tablename) for row in rows]
        to_drop = compute_non_auth_reset_drop_tables(live_tables)
        for table_name in to_drop:
            connection.execute(text(f'DROP TABLE IF EXISTS "{table_name}" CASCADE'))
    return to_drop


def _reset_generic_non_auth_schema(engine: Engine) -> tuple[str, ...]:
    _ensure_sidecar_sqlalchemy_models_loaded()
    table_map = Base.metadata.tables
    to_drop = tuple(
        table.name
        for table in reversed(Base.metadata.sorted_tables)
        if table.name in NON_AUTH_SCHEMA_TABLES
    )
    Base.metadata.drop_all(
        engine,
        tables=[table_map[name] for name in to_drop if name in table_map],
    )
    return to_drop


def _upgrade_postgres_schema(database_url: str, *, target_revision: str = "heads") -> None:
    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[2]
    alembic_config = Config(str(repo_root / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(alembic_config, target_revision)


def _stamp_postgres_schema_to_head(database_url: str) -> None:
    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[2]
    alembic_config = Config(str(repo_root / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.stamp(alembic_config, "heads")


def _ensure_sidecar_sqlalchemy_models_loaded() -> None:
    global _SIDECAR_SQLALCHEMY_MODELS_LOADED
    if _SIDECAR_SQLALCHEMY_MODELS_LOADED:
        return
    for module_name in _SIDECAR_SQLALCHEMY_MODULES:
        importlib.import_module(module_name, __package__)
    _SIDECAR_SQLALCHEMY_MODELS_LOADED = True


@contextmanager
def tenant_db_context(
    *,
    organization_id: str | None,
    user_id: str | None = None,
    is_superuser: bool = False,
) -> Iterator[None]:
    org_token: Token[str | None] = _CURRENT_DB_ORGANIZATION_ID.set(organization_id)
    user_token: Token[str | None] = _CURRENT_DB_USER_ID.set(user_id)
    superuser_token: Token[bool] = _CURRENT_DB_IS_SUPERUSER.set(is_superuser)
    try:
        yield
    finally:
        _CURRENT_DB_IS_SUPERUSER.reset(superuser_token)
        _CURRENT_DB_USER_ID.reset(user_token)
        _CURRENT_DB_ORGANIZATION_ID.reset(org_token)


def ensure_postgres_runtime_tenant_policies(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        _ensure_runtime_tenant_policies(connection)


def _install_session_context_hooks() -> None:
    global _SESSION_CONTEXT_HOOKS_INSTALLED
    if _SESSION_CONTEXT_HOOKS_INSTALLED:
        return

    @event.listens_for(Session, "after_begin")
    def _apply_request_tenant_context(session: Session, transaction, connection: Connection) -> None:  # type: ignore[no-untyped-def]
        if connection.dialect.name != "postgresql":
            return
        _set_connection_tenant_context(
            connection,
            organization_id=_CURRENT_DB_ORGANIZATION_ID.get(),
            user_id=_CURRENT_DB_USER_ID.get(),
            is_superuser=_CURRENT_DB_IS_SUPERUSER.get(),
        )

    _SESSION_CONTEXT_HOOKS_INSTALLED = True


def _ensure_runtime_tenant_policies(connection: Connection) -> None:
    tables = _compute_runtime_tenant_rls_tables()
    skipped: list[str] = []
    for table_name in tables:
        exists = connection.execute(
            text("SELECT to_regclass(:table_name)"),
            {"table_name": table_name},
        ).scalar()
        if exists is None:
            # Table exists on the model but not in this schema (e.g. migration
            # not yet applied, or a fresh schema that only ran a subset of
            # create_all). Record for observability but don't block startup.
            skipped.append(table_name)
            continue
        policy_name = f"tenant_scope_{table_name}"
        connection.execute(text(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY'))
        connection.execute(text(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY'))
        connection.execute(text(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"'))
        connection.execute(
            text(
                f'''
                CREATE POLICY "{policy_name}" ON "{table_name}"
                USING (
                    current_setting('app.current_is_superuser', true) = 'true'
                    OR organization_id IS NULL
                    OR organization_id = nullif(current_setting('app.current_organization_id', true), '')
                )
                WITH CHECK (
                    current_setting('app.current_is_superuser', true) = 'true'
                    OR organization_id IS NULL
                    OR organization_id = nullif(current_setting('app.current_organization_id', true), '')
                )
                '''
            )
        )
    if skipped:
        # Emit once per policy-install run. Expected for fresh schemas (create_all
        # didn't make every table yet). Unexpected + persistent = migration drift.
        logger.info(
            "runtime_tenant_rls_policies_installed",
            extra={
                "installed": len(tables) - len(skipped),
                "skipped_missing_from_schema": sorted(skipped),
            },
        )


class RLSPolicyAuditFailure(RuntimeError):
    """Raised when a required RLS policy is missing from the database.

    Treat this as a SEV-1 — it means at least one org-scoped table in the
    live schema has no tenant policy installed, which is a silent
    cross-tenant data-exposure risk.
    """


def assert_rls_policies_healthy(engine: Engine) -> None:
    """Verify that every org-scoped table in the live schema has a policy.

    Call at application startup after migrations and policy installation
    complete. Raises ``RLSPolicyAuditFailure`` listing the offending tables
    if any org-scoped table lacks a ``tenant_scope_<table>`` policy.

    This is the backstop for the "table exists in DB but nobody installed a
    policy for it" failure mode — for example, a new migration added a table
    with ``organization_id`` but the RLS derivation hasn't caught up yet
    because the Python model wasn't updated to match.
    """
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as connection:
        rows = connection.execute(
            text(
                """
                SELECT c.relname AS table_name
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_attribute a ON a.attrelid = c.oid
                WHERE n.nspname = current_schema()
                  AND c.relkind = 'r'
                  AND a.attname = 'organization_id'
                  AND NOT a.attisdropped
                """
            )
        ).all()
        live_org_scoped = {
            row.table_name
            for row in rows
            if row.table_name not in RUNTIME_TENANT_RLS_EXEMPT_TABLES
        }
        if not live_org_scoped:
            return

        policy_rows = connection.execute(
            text(
                """
                SELECT tablename, policyname
                FROM pg_policies
                WHERE schemaname = current_schema()
                """
            )
        ).all()
        policies_by_table: dict[str, set[str]] = {}
        for row in policy_rows:
            policies_by_table.setdefault(row.tablename, set()).add(row.policyname)

        missing: list[str] = []
        for table_name in sorted(live_org_scoped):
            expected = f"tenant_scope_{table_name}"
            if expected not in policies_by_table.get(table_name, set()):
                missing.append(table_name)

    if missing:
        logger.error(
            "runtime_tenant_rls_policies_missing",
            extra={"tables": missing},
        )
        raise RLSPolicyAuditFailure(
            "Org-scoped tables without tenant_scope RLS policy: "
            + ", ".join(missing)
            + ". This is a cross-tenant data-exposure risk. Ensure the model is "
            "imported by the sidecar loader, then redeploy to re-run policy install."
        )


def _set_connection_tenant_context(
    connection: Connection,
    *,
    organization_id: str | None,
    user_id: str | None,
    is_superuser: bool,
) -> None:
    connection.execute(
        text("SELECT set_config('app.current_is_superuser', :is_superuser, true)"),
        {"is_superuser": "true" if is_superuser else "false"},
    )
    connection.execute(
        text("SELECT set_config('app.current_organization_id', :org_id, true)"),
        {"org_id": organization_id or ""},
    )
    connection.execute(
        text("SELECT set_config('app.current_user_id', :user_id, true)"),
        {"user_id": user_id or ""},
    )
