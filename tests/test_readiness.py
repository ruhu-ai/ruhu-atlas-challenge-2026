"""RP-2.5: readiness probes (services/readiness.py + GET /ready)."""

from __future__ import annotations

import asyncio

from ruhu.db import build_session_factory
from ruhu.db_async import close_async_engine, init_async_engine
from ruhu.runtime_config import RuntimeSettings
from ruhu.services.readiness import run_readiness_probes


def _run_probes(url: str, settings: RuntimeSettings):
    # RP-2.4: the psycopg3 async engine accepts the per-test-schema
    # `?options=` URL directly — readiness now exercises the true URL.
    # The engine is disposed in the same event loop the probes ran in (H9).
    init_async_engine(url)
    session_factory = build_session_factory(url)

    async def _go():
        try:
            return await run_readiness_probes(
                session_factory=session_factory,
                settings=settings,
            )
        finally:
            await close_async_engine()

    return asyncio.run(_go())


def test_ready_ok_in_development_without_cipher(
    postgres_database_url_factory, monkeypatch
) -> None:
    monkeypatch.delenv("RUHU_CREDENTIAL_CIPHER_PRIMARY", raising=False)
    url = postgres_database_url_factory()
    ok, probes = _run_probes(url, RuntimeSettings(environment="development"))
    assert ok is True, probes
    assert probes["sync_db"] == "ok"
    assert probes["async_db"] == "ok"
    assert probes["credential_cipher"] == "absent"
    assert "redis" not in probes  # not configured -> not probed


def test_ready_fails_in_production_without_cipher(
    postgres_database_url_factory, monkeypatch
) -> None:
    monkeypatch.delenv("RUHU_CREDENTIAL_CIPHER_PRIMARY", raising=False)
    url = postgres_database_url_factory()
    ok, probes = _run_probes(url, RuntimeSettings(environment="production"))
    assert ok is False
    assert probes["credential_cipher"] == "error:missing"


def test_ready_ok_in_production_with_cipher(
    postgres_database_url_factory, monkeypatch
) -> None:
    from cryptography.fernet import Fernet

    monkeypatch.setenv("RUHU_CREDENTIAL_CIPHER_PRIMARY", Fernet.generate_key().decode())
    url = postgres_database_url_factory()
    ok, probes = _run_probes(url, RuntimeSettings(environment="production"))
    assert ok is True, probes
    assert probes["credential_cipher"] == "ok"


def test_ready_reports_unreachable_redis(postgres_database_url_factory) -> None:
    url = postgres_database_url_factory()
    ok, probes = _run_probes(
        url,
        RuntimeSettings(
            environment="development",
            redis_url="redis://127.0.0.1:1/0",  # nothing listens here
        ),
    )
    assert ok is False
    assert probes["redis"].startswith("error:")
