"""Explicit kernel thread pool (RP-2.4 step 3).

The kernel turn path is sync end-to-end, so async API routes hop onto a
bounded ``ThreadPoolExecutor`` for turn processing. Before RP-2.4 this pool
was also installed as the event loop's DEFAULT executor, silently hosting
every ``run_in_executor(None, …)`` in the process (tools/runtime, rate-limit
fallbacks). Now the kernel pool is passed explicitly; everything else falls
back to the loop's stock default executor — bounded, acceptable (H4).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import functools
import logging
import os
from typing import Any, Callable, TypeVar

from ..runtime_config import RuntimeSettings

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


def build_kernel_executor(
    settings: RuntimeSettings,
) -> concurrent.futures.ThreadPoolExecutor:
    """Create the kernel thread pool; warn when it oversubscribes the DB pool.

    Each in-flight kernel turn holds a sync DB session, so an executor larger
    than ``sync_db_pool_size + sync_db_max_overflow`` means turns beyond DB
    capacity queue on ``pool_timeout`` instead of in the executor.
    """
    max_workers = int(os.getenv("RUHU_KERNEL_THREAD_POOL_SIZE", "64"))
    db_capacity = settings.sync_db_pool_size + settings.sync_db_max_overflow
    if max_workers > db_capacity:
        logger.warning(
            "kernel executor size %d exceeds sync DB pool capacity %d "
            "(pool_size=%d + max_overflow=%d); excess turns will wait on "
            "the DB pool_timeout",
            max_workers,
            db_capacity,
            settings.sync_db_pool_size,
            settings.sync_db_max_overflow,
        )
    return concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)


async def run_in_kernel_executor(
    app: Any,
    func: Callable[..., _T],
    /,
    *args: Any,
    **kwargs: Any,
) -> _T:
    """Run ``func(*args, **kwargs)`` on the app's explicit kernel executor.

    The executor is read from ``app.state.kernel_executor`` (set during
    lifespan startup). When absent (lifespan not started, e.g. in-process
    test clients), the loop's stock default executor is used.

    Matches ``asyncio.to_thread`` semantics: contextvars — notably the tenant
    RLS scope populated by ``AuthContextMiddleware`` — propagate into the
    worker thread via ``Context.run``.
    """
    executor: concurrent.futures.ThreadPoolExecutor | None = getattr(
        app.state, "kernel_executor", None
    )
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    call = functools.partial(func, *args, **kwargs)
    return await loop.run_in_executor(executor, ctx.run, call)
