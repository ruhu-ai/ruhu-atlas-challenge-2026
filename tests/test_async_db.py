"""Tests for the async DB layer (db_async.py) — Phase: async DB hardening.

Tests are unit-level and do not require a live PostgreSQL connection.
Integration tests that need a real DB are skipped when RUHU_DATABASE_URL is unset.

Coverage:
  - URL resolution (resolve_database_url output passed straight to create_async_engine)
  - RuntimeSettings DB pool defaults (correctness, env var override)
  - init_async_engine + close_async_engine lifecycle (incl. H6 re-init disposal)
  - get_async_session raises when engine is not initialised
  - Statement timeout applied via connect-event listener (not connect_args)
  - pool_timeout propagated to engine
  - Async query metrics installed on sync_engine
  - Async pool metrics installed on sync_engine pool
  - db.py: build_engine honours pool_timeout and statement_timeout_ms
  - db.py: close_sync_engine clears module global
  - db.py: build_session_factory sets _SYNC_ENGINE
  - RuntimeSettings: DB fields parsed from env vars
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# URL resolution — the resolved postgresql+psycopg URL goes straight to
# create_async_engine (psycopg3 serves both sync and async engines).
# ═══════════════════════════════════════════════════════════════════════════════

def _init_with_fake_engine(url: str, **init_kwargs):
    """Run init_async_engine with create_async_engine faked; return (url, kwargs)."""
    import ruhu.db_async as _mod
    captured: dict = {"url": None, "kwargs": {}}

    def _fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        m = MagicMock()
        m.sync_engine = MagicMock()
        m.sync_engine.pool = MagicMock()
        return m

    with (
        patch("ruhu.db_async.create_async_engine", side_effect=_fake_create_engine),
        patch("ruhu.db_async.async_sessionmaker", return_value=MagicMock()),
        patch("ruhu.db_async.event.listens_for") as listens_for,
        # ``ruhu.db_async.event`` IS the shared ``sqlalchemy.event`` module —
        # patching listens_for above would turn the REAL RLS after_begin hook
        # registration inside _install_session_context_hooks into a mock
        # no-op while still flipping its installed-flag, silently disabling
        # tenant scoping for the rest of the process. Patch the installer
        # itself so the flag is untouched.
        patch("ruhu.db_async._install_session_context_hooks"),
        patch("ruhu.db_async._install_async_pool_metrics"),
        patch("ruhu.db_async._install_async_query_metrics"),
    ):
        _mod.init_async_engine(url, **init_kwargs)
    captured["listens_for"] = listens_for
    _mod._ASYNC_ENGINE = None
    _mod._ASYNC_SESSION_FACTORY = None
    return captured


class TestAsyncEngineUrlResolution:
    def test_postgres_shorthand_becomes_psycopg(self):
        captured = _init_with_fake_engine("postgres://user:pass@host:5432/db")
        assert captured["url"].startswith("postgresql+psycopg://")

    def test_postgresql_becomes_psycopg(self):
        captured = _init_with_fake_engine("postgresql://user:pass@host:5432/db")
        assert captured["url"].startswith("postgresql+psycopg://")

    def test_psycopg2_url_becomes_psycopg(self):
        captured = _init_with_fake_engine("postgresql+psycopg2://user:pass@host:5432/db")
        assert captured["url"].startswith("postgresql+psycopg://")

    def test_already_psycopg_url_unchanged(self):
        captured = _init_with_fake_engine("postgresql+psycopg://user:pass@host:5432/db")
        assert captured["url"] == "postgresql+psycopg://user:pass@host:5432/db"

    def test_host_path_and_options_preserved(self):
        # Per-test schema URLs carry libpq ``?options=`` — psycopg3 accepts
        # them; the engine must receive them untouched (asyncpg could not).
        captured = _init_with_fake_engine(
            "postgres://alice:secret@db.example.com:5432/mydb?options=-csearch_path%3Dtest_x"
        )
        assert "alice:secret@db.example.com:5432/mydb" in captured["url"]
        assert "options=-csearch_path%3Dtest_x" in captured["url"]


# ═══════════════════════════════════════════════════════════════════════════════
# RuntimeSettings DB pool defaults and env var parsing
# ═══════════════════════════════════════════════════════════════════════════════

class TestRuntimeSettingsDbDefaults:
    def test_sync_pool_defaults(self):
        from ruhu.runtime_config import RuntimeSettings
        s = RuntimeSettings()
        assert s.sync_db_pool_size == 20
        assert s.sync_db_max_overflow == 40
        assert s.sync_db_pool_recycle == 1800
        assert s.sync_db_pool_timeout == 30.0
        assert s.sync_db_statement_timeout_ms == 30_000

    def test_async_pool_defaults(self):
        from ruhu.runtime_config import RuntimeSettings
        s = RuntimeSettings()
        assert s.async_db_pool_size == 20
        assert s.async_db_max_overflow == 40
        assert s.async_db_pool_recycle == 1800
        assert s.async_db_pool_timeout == 30.0
        assert s.async_db_statement_timeout_ms == 30_000

    def test_sync_pool_from_env(self, monkeypatch):
        monkeypatch.setenv("RUHU_SYNC_DB_POOL_SIZE", "5")
        monkeypatch.setenv("RUHU_SYNC_DB_MAX_OVERFLOW", "10")
        monkeypatch.setenv("RUHU_SYNC_DB_POOL_TIMEOUT", "15.5")
        monkeypatch.setenv("RUHU_SYNC_DB_STATEMENT_TIMEOUT_MS", "5000")
        from ruhu.runtime_config import RuntimeSettings
        s = RuntimeSettings.from_env()
        assert s.sync_db_pool_size == 5
        assert s.sync_db_max_overflow == 10
        assert s.sync_db_pool_timeout == 15.5
        assert s.sync_db_statement_timeout_ms == 5000

    def test_async_pool_from_env(self, monkeypatch):
        monkeypatch.setenv("RUHU_ASYNC_DB_POOL_SIZE", "8")
        monkeypatch.setenv("RUHU_ASYNC_DB_MAX_OVERFLOW", "16")
        monkeypatch.setenv("RUHU_ASYNC_DB_POOL_TIMEOUT", "45.0")
        monkeypatch.setenv("RUHU_ASYNC_DB_STATEMENT_TIMEOUT_MS", "10000")
        from ruhu.runtime_config import RuntimeSettings
        s = RuntimeSettings.from_env()
        assert s.async_db_pool_size == 8
        assert s.async_db_max_overflow == 16
        assert s.async_db_pool_timeout == 45.0
        assert s.async_db_statement_timeout_ms == 10000


# ═══════════════════════════════════════════════════════════════════════════════
# init_async_engine / close_async_engine lifecycle
# ═══════════════════════════════════════════════════════════════════════════════

class TestAsyncEngineLifecycle:
    def setup_method(self):
        """Reset module globals before each test."""
        import ruhu.db_async as _mod
        _mod._ASYNC_ENGINE = None
        _mod._ASYNC_SESSION_FACTORY = None

    def teardown_method(self):
        """Ensure globals are cleaned up."""
        import ruhu.db_async as _mod
        _mod._ASYNC_ENGINE = None
        _mod._ASYNC_SESSION_FACTORY = None

    def test_get_async_session_raises_before_init(self):
        from ruhu.db_async import get_async_session
        import asyncio

        async def _run():
            async with get_async_session() as _:
                pass

        with pytest.raises(RuntimeError, match="not initialised"):
            asyncio.run(_run())

    def test_init_sets_engine_and_factory(self):
        import ruhu.db_async as _mod
        mock_engine = MagicMock()
        mock_engine.sync_engine = MagicMock()
        mock_engine.sync_engine.pool = MagicMock()
        mock_factory = MagicMock()

        with (
            patch("ruhu.db_async.create_async_engine", return_value=mock_engine),
            patch("ruhu.db_async.async_sessionmaker", return_value=mock_factory),
            patch("ruhu.db_async.event.listens_for"),
            patch("ruhu.db_async._install_session_context_hooks"),
            patch("ruhu.db_async._install_async_pool_metrics"),
            patch("ruhu.db_async._install_async_query_metrics"),
        ):
            _mod.init_async_engine("postgresql+psycopg://u:p@localhost/db")

        assert _mod._ASYNC_ENGINE is mock_engine
        assert _mod._ASYNC_SESSION_FACTORY is mock_factory

    def test_close_async_engine_disposes_and_clears(self):
        import asyncio
        import ruhu.db_async as _mod

        mock_engine = AsyncMock()
        _mod._ASYNC_ENGINE = mock_engine

        asyncio.run(_mod.close_async_engine())

        mock_engine.dispose.assert_awaited_once()
        assert _mod._ASYNC_ENGINE is None

    def test_close_async_engine_noop_when_not_initialised(self):
        import asyncio
        from ruhu.db_async import close_async_engine
        # Should not raise
        asyncio.run(close_async_engine())

    def test_reinit_disposes_existing_engine(self):
        """H6: a second init_async_engine must dispose the first engine's pool."""
        import ruhu.db_async as _mod
        first_engine = MagicMock()
        first_engine.sync_engine = MagicMock()
        _mod._ASYNC_ENGINE = first_engine
        _mod._ASYNC_SESSION_FACTORY = MagicMock()

        _init_with_fake_engine("postgresql+psycopg://u:p@localhost/db")

        first_engine.sync_engine.dispose.assert_called_once()

    def test_statement_timeout_registered_as_connect_listener(self):
        # Timeout moves from asyncpg connect_args["server_settings"] to a
        # connect-event listener on engine.sync_engine (psycopg3 pattern,
        # mirroring db.build_engine). No connect_args on the engine at all.
        captured = _init_with_fake_engine(
            "postgresql+psycopg://u:p@localhost/db",
            statement_timeout_ms=5000,
        )
        assert "connect_args" not in captured["kwargs"]
        captured["listens_for"].assert_called_once()
        args, _kwargs = captured["listens_for"].call_args
        assert args[1] == "connect"

    def test_zero_statement_timeout_registers_no_listener(self):
        captured = _init_with_fake_engine(
            "postgresql+psycopg://u:p@localhost/db",
            statement_timeout_ms=0,
        )
        assert "connect_args" not in captured["kwargs"]
        captured["listens_for"].assert_not_called()

    def test_statement_timeout_listener_runs_set_statement_timeout(self):
        # Drive the registered listener with a fake AdaptedConnection and
        # assert the AdaptedConnection.run_async pattern issues the SET.
        import ruhu.db_async as _mod

        registered: dict = {}

        def _fake_listens_for(target, identifier):
            def _decorator(fn):
                registered["fn"] = fn
                return fn
            return _decorator

        def _fake_create_engine(url, **kwargs):
            m = MagicMock()
            m.sync_engine = MagicMock()
            m.sync_engine.pool = MagicMock()
            return m

        with (
            patch("ruhu.db_async.create_async_engine", side_effect=_fake_create_engine),
            patch("ruhu.db_async.async_sessionmaker", return_value=MagicMock()),
            patch("ruhu.db_async.event.listens_for", side_effect=_fake_listens_for),
            patch("ruhu.db_async._install_session_context_hooks"),
            patch("ruhu.db_async._install_async_pool_metrics"),
            patch("ruhu.db_async._install_async_query_metrics"),
        ):
            _mod.init_async_engine(
                "postgresql+psycopg://u:p@localhost/db",
                statement_timeout_ms=7500,
            )
        _mod._ASYNC_ENGINE = None
        _mod._ASYNC_SESSION_FACTORY = None

        executed: list[str] = []
        adapted = MagicMock()

        def _run_async(fn):
            driver_connection = MagicMock()
            driver_connection.execute = lambda sql: executed.append(sql)
            fn(driver_connection)

        adapted.run_async = _run_async
        registered["fn"](adapted, MagicMock())
        assert executed == ["SET statement_timeout = 7500"]

    def test_pool_timeout_propagated(self):
        captured = _init_with_fake_engine(
            "postgresql+psycopg://u:p@localhost/db",
            pool_timeout=45.0,
        )
        assert captured["kwargs"]["pool_timeout"] == 45.0

    def test_pool_pre_ping_always_enabled(self):
        captured = _init_with_fake_engine("postgresql+psycopg://u:p@localhost/db")
        assert captured["kwargs"]["pool_pre_ping"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# db.py sync engine hardening
# ═══════════════════════════════════════════════════════════════════════════════

class TestSyncEngineHardening:
    def test_build_engine_passes_pool_timeout(self):
        from ruhu.db import build_engine
        captured: dict = {}

        def _fake_create_engine(url, **kwargs):
            captured.update(kwargs)
            return MagicMock()

        with (
            patch("ruhu.db.create_engine", side_effect=_fake_create_engine),
            patch("ruhu.db._install_pool_metrics"),
            patch("ruhu.db._install_query_metrics"),
        ):
            build_engine("sqlite:///test.db", pool_timeout=99.0)

        assert captured["pool_timeout"] == 99.0

    def test_build_engine_registers_statement_timeout_hook_for_postgres(self):
        from ruhu.db import build_engine
        captured: dict = {}

        def _fake_create_engine(url, **kwargs):
            captured.update(kwargs)
            return MagicMock()

        with (
            patch("ruhu.db.create_engine", side_effect=_fake_create_engine),
            patch("ruhu.db.event.listens_for") as mocked_listens_for,
            patch("ruhu.db._install_pool_metrics"),
            patch("ruhu.db._install_query_metrics"),
        ):
            build_engine(
                "postgresql+psycopg://u:p@localhost/db",
                statement_timeout_ms=10_000,
            )

        assert captured.get("connect_args", {}) == {}
        mocked_listens_for.assert_called()

    def test_build_engine_no_connect_args_for_sqlite(self):
        from ruhu.db import build_engine
        captured: dict = {}

        def _fake_create_engine(url, **kwargs):
            captured.update(kwargs)
            return MagicMock()

        with (
            patch("ruhu.db.create_engine", side_effect=_fake_create_engine),
            patch("ruhu.db._install_pool_metrics"),
            patch("ruhu.db._install_query_metrics"),
        ):
            build_engine("sqlite:///test.db", statement_timeout_ms=5000)

        # SQLite does not support statement_timeout — connect_args should be empty
        assert captured.get("connect_args", {}) == {}

    def test_zero_statement_timeout_omits_connect_args(self):
        from ruhu.db import build_engine
        captured: dict = {}

        def _fake_create_engine(url, **kwargs):
            captured.update(kwargs)
            return MagicMock()

        with (
            patch("ruhu.db.create_engine", side_effect=_fake_create_engine),
            patch("ruhu.db._install_pool_metrics"),
            patch("ruhu.db._install_query_metrics"),
        ):
            build_engine("postgresql+psycopg://u:p@localhost/db", statement_timeout_ms=0)

        assert captured.get("connect_args", {}) == {}

    def test_close_sync_engine_disposes_and_clears(self):
        import ruhu.db as _mod
        mock_engine = MagicMock()
        _mod._SYNC_ENGINE = mock_engine

        _mod.close_sync_engine()

        mock_engine.dispose.assert_called_once()
        assert _mod._SYNC_ENGINE is None

    def test_close_sync_engine_noop_when_none(self):
        import ruhu.db as _mod
        _mod._SYNC_ENGINE = None
        # Should not raise
        _mod.close_sync_engine()

    def test_build_session_factory_sets_sync_engine(self):
        import ruhu.db as _mod
        mock_engine = MagicMock()
        mock_engine.dialect.name = "sqlite"

        with (
            patch("ruhu.db.build_engine", return_value=mock_engine),
            patch("ruhu.db._ensure_sidecar_sqlalchemy_models_loaded"),
            patch("ruhu.db.Base.metadata.create_all"),
            patch("ruhu.db.ensure_postgres_runtime_tenant_policies"),
            patch("ruhu.db._install_session_context_hooks"),
            patch("ruhu.db.sessionmaker", return_value=MagicMock()),
        ):
            _mod.build_session_factory("sqlite:///test.db")

        assert _mod._SYNC_ENGINE is mock_engine


# ═══════════════════════════════════════════════════════════════════════════════
# Metrics installation
# ═══════════════════════════════════════════════════════════════════════════════

class TestMetricsRegistered:
    def test_retention_metrics_in_registry(self):
        from ruhu.observability import metrics as _m
        names = {m.name for m in _m.registry.collect()}
        assert "ruhu_retention_sweep_rows" in names
        assert "ruhu_retention_sweep_duration_seconds" in names
        assert "ruhu_retention_archival_pressure" in names

    def test_db_pool_metrics_in_registry(self):
        from ruhu.observability import metrics as _m
        names = {m.name for m in _m.registry.collect()}
        assert "ruhu_db_pool_checked_out" in names
        assert "ruhu_db_pool_overflow" in names

    def test_db_query_metrics_in_registry(self):
        from ruhu.observability import metrics as _m
        names = {m.name for m in _m.registry.collect()}
        assert "ruhu_db_query_duration_seconds" in names
