"""
Async SQLAlchemy engine for FastAPI route handlers.

Reuses the existing context vars (_CURRENT_DB_ORGANIZATION_ID, _CURRENT_DB_USER_ID,
_CURRENT_DB_IS_SUPERUSER) from db.py — these are already populated by
AuthContextMiddleware → tenant_db_context() for every authenticated request.
No separate context management needed; async sessions get the same tenant scope.

Usage in route handlers::

    from ruhu.db_async import get_db_session

    @router.post("/conversations")
    async def create_conversation(
        db: AsyncSession = Depends(get_db_session),
    ):
        ...

Initialization (in app lifespan)::

    from ruhu.db_async import init_async_engine, close_async_engine

    init_async_engine(settings.database_url)
    ...
    await close_async_engine()
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import time
from typing import AsyncIterator, Optional

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Reuse the sync-engine infrastructure: the class-level ``after_begin`` listener
# installed by ``_install_session_context_hooks`` applies the tenant RLS config
# to every transaction begin on every Session, including the sync Session that
# AsyncSession wraps. Calling it here is idempotent and guards the async-only
# startup path (if the sync session factory isn't built first).
from ruhu.db import (
    _install_session_context_hooks,
    resolve_database_url,
)

_ASYNC_ENGINE: Optional[AsyncEngine] = None
_ASYNC_SESSION_FACTORY: Optional[async_sessionmaker[AsyncSession]] = None


def _install_async_pool_metrics(engine: AsyncEngine) -> None:
    """Attach pool event listeners to the async engine's sync pool."""
    try:
        from .observability.metrics import db_pool_checked_out, db_pool_overflow
    except Exception:
        return
    sync_engine = engine.sync_engine

    @event.listens_for(sync_engine.pool, "checkout")
    def _on_checkout(dbapi_conn: object, connection_record: object, connection_proxy: object) -> None:
        pool = sync_engine.pool
        db_pool_checked_out.labels(pool="async").set(pool.checkedout())
        db_pool_overflow.labels(pool="async").set(pool.overflow())

    @event.listens_for(sync_engine.pool, "connect")
    def _on_connect(dbapi_conn: object, connection_record: object) -> None:
        pool = sync_engine.pool
        db_pool_checked_out.labels(pool="async").set(pool.checkedout())
        db_pool_overflow.labels(pool="async").set(pool.overflow())

    @event.listens_for(sync_engine.pool, "checkin")
    def _on_checkin(dbapi_conn: object, connection_record: object) -> None:
        pool = sync_engine.pool
        db_pool_checked_out.labels(pool="async").set(pool.checkedout())
        db_pool_overflow.labels(pool="async").set(pool.overflow())


def _install_async_query_metrics(engine: AsyncEngine) -> None:
    """Attach cursor execute events on the underlying sync engine to time queries."""
    try:
        from .observability.metrics import db_query_duration_seconds
    except Exception:
        return
    import time
    sync_engine = engine.sync_engine
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

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _before(conn: object, cursor: object, statement: object, parameters: object, context: object, executemany: object) -> None:
        start_times[id(cursor)] = time.monotonic()
        operations[id(cursor)] = _classify_operation(statement)

    @event.listens_for(sync_engine, "after_cursor_execute")
    def _after(conn: object, cursor: object, statement: object, parameters: object, context: object, executemany: object) -> None:
        start = start_times.pop(id(cursor), None)
        operation = operations.pop(id(cursor), "other")
        if start is not None:
            db_query_duration_seconds.labels(pool="async", operation=operation).observe(
                time.monotonic() - start
            )


def init_async_engine(
    database_url: str,
    *,
    pool_size: int = 20,
    max_overflow: int = 40,
    pool_recycle: int = 1800,
    pool_timeout: float = 30.0,
    statement_timeout_ms: int = 30_000,
) -> None:
    """Initialise the module-level async engine.  Call once at application startup.

    Re-initialisation policy (H6): if an engine already exists (e.g. the eager
    ``build_default_app`` init followed by the lifespan init), the existing
    engine is DISPOSED before the new one is created — re-init replaces rather
    than leaks. ``engine.sync_engine.dispose()`` is the sync-safe disposal path
    (no event loop required); in the double-init case the first engine's pool
    has never connected, so there is nothing async to close.
    """
    import logging
    _logger = logging.getLogger(__name__)
    global _ASYNC_ENGINE, _ASYNC_SESSION_FACTORY
    if _ASYNC_ENGINE is not None:
        _logger.info("init_async_engine: disposing existing async engine before re-init")
        try:
            _ASYNC_ENGINE.sync_engine.dispose()
        except Exception:
            _logger.warning("failed to dispose previous async engine", exc_info=True)
        _ASYNC_ENGINE = None
        _ASYNC_SESSION_FACTORY = None
    # resolve_database_url normalises to postgresql+psycopg://… which is
    # already a valid async URL — SQLAlchemy selects the psycopg async DBAPI
    # when the engine is created via create_async_engine. Unlike asyncpg,
    # psycopg3 accepts libpq query parameters such as
    # ``?options=-csearch_path%3D<schema>`` (used by per-test schema URLs).
    url = resolve_database_url(database_url=database_url)
    _ASYNC_ENGINE = create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_recycle=pool_recycle,
        pool_timeout=pool_timeout,
        pool_pre_ping=True,
    )
    # Apply statement_timeout per connection via a connect-event listener
    # rather than connect_args — mirrors the sync engine in db.py, and avoids
    # clobbering the URL's ``options=`` parameter. The DBAPI connection here
    # is SQLAlchemy's AdaptedConnection wrapping a psycopg AsyncConnection;
    # ``run_async`` executes the coroutine on the driver's loop.
    if statement_timeout_ms > 0 and url.startswith("postgresql"):

        @event.listens_for(_ASYNC_ENGINE.sync_engine, "connect")
        def _set_statement_timeout(dbapi_connection, _):  # type: ignore[no-untyped-def]
            dbapi_connection.run_async(
                lambda connection: connection.execute(
                    f"SET statement_timeout = {int(statement_timeout_ms)}"
                )
            )
    _logger.info(
        "async DB engine created",
        extra={
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            "pool_recycle": pool_recycle,
            "pool_timeout": pool_timeout,
            "statement_timeout_ms": statement_timeout_ms,
        },
    )
    _install_async_pool_metrics(_ASYNC_ENGINE)
    _install_async_query_metrics(_ASYNC_ENGINE)
    # Ensure the class-level ``after_begin`` RLS hook exists. Idempotent —
    # if ``build_session_factory`` already ran, this is a no-op.
    _install_session_context_hooks()
    _ASYNC_SESSION_FACTORY = async_sessionmaker(
        bind=_ASYNC_ENGINE,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
        class_=AsyncSession,
    )


async def close_async_engine() -> None:
    """Dispose the async engine pool.  Call in application shutdown."""
    global _ASYNC_ENGINE
    if _ASYNC_ENGINE is not None:
        await _ASYNC_ENGINE.dispose()
        _ASYNC_ENGINE = None


@asynccontextmanager
async def get_async_session() -> AsyncIterator[AsyncSession]:
    """
    Yield an ``AsyncSession`` running inside a single request-scoped transaction.

    The class-level ``after_begin`` event listener installed by
    ``_install_session_context_hooks()`` (in ``ruhu.db``) re-applies the tenant
    RLS config on every transaction begin — including the implicit begin that
    fires when this context manager opens ``session.begin()`` — using the
    ``_CURRENT_DB_ORGANIZATION_ID`` / ``_CURRENT_DB_USER_ID`` /
    ``_CURRENT_DB_IS_SUPERUSER`` context vars that
    ``AuthContextMiddleware`` populates for every authenticated request.

    This mirrors the sync session's behaviour: RLS is re-applied on each
    ``after_begin``, so code paths that commit mid-handler and start a new
    transaction still get the right tenant scope on the next statement.

    Commits on clean exit; rolls back on exception. Callers should NOT call
    ``await session.commit()`` themselves — the context manager handles it.
    For nested units of work within a single request, use
    ``await session.begin_nested()`` (savepoints).

    Unauthenticated requests have empty-string context vars; the RLS policy
    then only permits rows with ``organization_id IS NULL``.
    """
    if _ASYNC_SESSION_FACTORY is None:
        raise RuntimeError(
            "Async engine not initialised. Call init_async_engine() at application startup."
        )

    async with _ASYNC_SESSION_FACTORY() as session:
        async with session.begin():
            yield session


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI ``Depends()`` injection for async route handlers."""
    async with get_async_session() as session:
        yield session
