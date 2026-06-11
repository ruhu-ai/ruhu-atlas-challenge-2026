"""Readiness probes (RP-2.5): can this process serve traffic?

Used by ``GET /ready``. Each probe is bounded so a slow dependency degrades
the report instead of cascading into request timeouts. Liveness stays
deliberately shallow at ``/live`` — these checks must never trigger restart
storms.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from ..runtime_config import RuntimeSettings

_PROBE_TIMEOUT_SECONDS = 0.5


async def run_readiness_probes(
    *,
    session_factory: sessionmaker[Session],
    settings: RuntimeSettings,
) -> tuple[bool, dict[str, str]]:
    """Returns (overall_ok, per-probe status map)."""
    from ..db_async import _ASYNC_ENGINE as async_engine  # noqa: PLC0415

    probes: dict[str, str] = {}
    overall_ok = True

    async def _probe_sync() -> None:
        def _run() -> None:
            with session_factory() as session:
                session.execute(text("SELECT 1"))

        await asyncio.to_thread(_run)

    async def _probe_async() -> None:
        if async_engine is None:
            raise RuntimeError("async engine not initialised")
        async with async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def _probe_redis() -> None:
        import redis.asyncio as aioredis  # noqa: PLC0415

        client = aioredis.from_url(settings.redis_url)
        try:
            await client.ping()
        finally:
            await client.aclose()

    for label, probe in (("sync_db", _probe_sync), ("async_db", _probe_async)):
        try:
            await asyncio.wait_for(probe(), timeout=_PROBE_TIMEOUT_SECONDS)
            probes[label] = "ok"
        except Exception as exc:  # noqa: BLE001 — readiness intentionally broad
            probes[label] = f"error:{type(exc).__name__}"
            overall_ok = False

    if settings.redis_url:
        try:
            await asyncio.wait_for(_probe_redis(), timeout=_PROBE_TIMEOUT_SECONDS)
            probes["redis"] = "ok"
        except Exception as exc:  # noqa: BLE001
            probes["redis"] = f"error:{type(exc).__name__}"
            overall_ok = False

    # Credential cipher: required outside development — a deploy with a
    # missing/garbled key ring must not take traffic and then fail every
    # connection decrypt.
    try:
        from ..tools.cipher import FernetCipher  # noqa: PLC0415

        FernetCipher.from_env()
        probes["credential_cipher"] = "ok"
    except ValueError:
        if settings.environment.strip().lower() in {"production", "staging"}:
            probes["credential_cipher"] = "error:missing"
            overall_ok = False
        else:
            probes["credential_cipher"] = "absent"

    return overall_ok, probes
