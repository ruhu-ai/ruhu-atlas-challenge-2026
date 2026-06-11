"""Tests for Phase 2: async DB foundation (db_async.py).

Covers:
- get_async_session/get_db_session contracts (engine lifecycle pins live in test_async_db.py)
- init_async_engine / close_async_engine: engine lifecycle
- init_async_engine: installs the tenant RLS session hook
- get_async_session: raises when engine not initialised
- get_async_session: opens a transaction so the after_begin RLS hook fires
- get_async_session: commits on clean exit / rolls back on exception
- get_db_session: is an async generator suitable for FastAPI Depends()

Tenant RLS application itself is covered by db.py's session_context_hooks
tests — we only verify here that db_async wires in the same hook and opens a
transaction so the hook actually fires.

These tests do NOT require a live database.  Session / engine interactions are
mocked or tested at the URL-conversion / guard-clause level only.

All async calls use anyio.run() (no pytest-asyncio mark) to avoid the pytest-asyncio
Package.__init__ collection error that occurs when tests/ is a package.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from ruhu.db_async import (
    get_async_session,
    get_db_session,
    init_async_engine,
)
import ruhu.db_async as db_async_mod


# ── get_async_session: guard clause ──────────────────────────────────────────

class TestGetAsyncSessionGuard:
    def setup_method(self):
        db_async_mod._ASYNC_ENGINE = None
        db_async_mod._ASYNC_SESSION_FACTORY = None

    def test_raises_runtime_error_when_not_initialised(self):
        async def _inner():
            with pytest.raises(RuntimeError, match="not initialised"):
                async with get_async_session():
                    pass  # pragma: no cover

        anyio.run(_inner)


# ── get_async_session: transaction boundary + tenant RLS wiring ──────────────

class TestGetAsyncSessionTransaction:
    """
    Verify the contract that makes tenant RLS safe:

    - ``get_async_session`` MUST open ``session.begin()`` so the class-level
      ``after_begin`` event listener (installed by
      ``_install_session_context_hooks`` in db.py) fires and applies the tenant
      config via ``set_config('app.current_*', ..., true)`` on the correct
      connection. Without this transaction boundary, the listener never fires
      and RLS defaults to the unauthenticated (``organization_id IS NULL``)
      scope — a tenant isolation bug.

    - Clean exit commits (handled by ``session.begin()``'s __aexit__); exception
      rolls back.
    """

    def setup_method(self):
        db_async_mod._ASYNC_ENGINE = None
        db_async_mod._ASYNC_SESSION_FACTORY = None

    def teardown_method(self):
        db_async_mod._ASYNC_SESSION_FACTORY = None

    def _make_mock_factory(self):
        """Return a mock session factory context manager and the session mock."""
        mock_session = AsyncMock()
        begin_cm = AsyncMock()
        begin_cm.__aenter__ = AsyncMock(return_value=begin_cm)
        begin_cm.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(return_value=begin_cm)

        @asynccontextmanager
        async def _ctx():
            yield mock_session

        mock_factory = MagicMock()
        mock_factory.return_value = _ctx()
        return mock_factory, mock_session, begin_cm

    def test_opens_transaction_so_after_begin_hook_fires(self):
        """The request-scoped ``session.begin()`` is what triggers the RLS hook."""
        mock_factory, mock_session, begin_cm = self._make_mock_factory()
        db_async_mod._ASYNC_SESSION_FACTORY = mock_factory

        async def _inner():
            async with get_async_session() as session:
                assert session is mock_session

        anyio.run(_inner)
        mock_session.begin.assert_called_once()
        begin_cm.__aenter__.assert_awaited_once()
        # Clean exit: __aexit__ receives (None, None, None) → session.begin()
        # commits the transaction. We only assert the context manager exits
        # normally; the commit semantics belong to SQLAlchemy itself.
        begin_cm.__aexit__.assert_awaited_once()

    def test_rolls_back_via_transaction_context_on_exception(self):
        """An exception inside the ``async with`` bubbles to ``session.begin().__aexit__``."""
        mock_factory, mock_session, begin_cm = self._make_mock_factory()
        db_async_mod._ASYNC_SESSION_FACTORY = mock_factory

        async def _inner():
            with pytest.raises(ValueError, match="boom"):
                async with get_async_session():
                    raise ValueError("boom")

        anyio.run(_inner)
        # __aexit__ is awaited with the exception info — SQLAlchemy's
        # session.begin() context manager is what actually rolls back.
        begin_cm.__aexit__.assert_awaited_once()
        exit_args = begin_cm.__aexit__.call_args.args
        assert exit_args[0] is ValueError
        assert isinstance(exit_args[1], ValueError)


class TestInitAsyncEngineInstallsSessionHook:
    """``init_async_engine`` must install the class-level after_begin RLS hook,
    so the async engine is safe even if ``build_session_factory`` never runs
    (async-only deployment)."""

    def setup_method(self):
        db_async_mod._ASYNC_ENGINE = None
        db_async_mod._ASYNC_SESSION_FACTORY = None

    def teardown_method(self):
        db_async_mod._ASYNC_ENGINE = None
        db_async_mod._ASYNC_SESSION_FACTORY = None

    def test_calls_install_session_context_hooks(self):
        with (
            patch("ruhu.db_async._install_session_context_hooks") as mock_install,
            patch("ruhu.db_async.create_async_engine", return_value=MagicMock()),
            patch("ruhu.db_async.async_sessionmaker", return_value=MagicMock()),
            patch("ruhu.db_async._install_async_pool_metrics"),
            patch("ruhu.db_async._install_async_query_metrics"),
        ):
            # statement_timeout_ms=0 skips the connect-listener registration,
            # which cannot target a MagicMock engine.
            init_async_engine("postgresql+psycopg://u:p@localhost/db", statement_timeout_ms=0)
            mock_install.assert_called_once()


# ── get_db_session: FastAPI Depends() compatibility ───────────────────────────

class TestGetDbSession:
    def test_is_async_generator_function(self):
        """get_db_session must be an async generator function for FastAPI Depends()."""
        import inspect
        assert inspect.isasyncgenfunction(get_db_session)

    def test_raises_when_engine_not_initialised(self):
        db_async_mod._ASYNC_ENGINE = None
        db_async_mod._ASYNC_SESSION_FACTORY = None

        async def _inner():
            with pytest.raises(RuntimeError):
                async for _ in get_db_session():
                    pass  # pragma: no cover

        anyio.run(_inner)
