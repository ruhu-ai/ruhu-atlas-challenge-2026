"""
Tool hardening phase 2 tests: retry jitter + sync-path circuit breaker.

Validates:
- compute_next_retry_at() applies +/-20% jitter by default
- retry_jitter=False in metadata disables jitter (deterministic path)
- CircuitBreaker.can_execute_sync() / record_failure_sync() mirror async behavior
- Sync invoke() path is now circuit-breaker protected (parity with execute_async)
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import anyio
import pytest

from ruhu.tools.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    CircuitState,
)
from ruhu.tools.integration_runtime import ToolIntegrationRuntime
from ruhu.tools.integration_store import InMemoryToolIntegrationJobStore
from ruhu.tools.types import ToolIntegrationJob


# ----- Retry jitter tests ----------------------------------------------------

class TestRetryJitter:
    def _build_runtime(self) -> ToolIntegrationRuntime:
        return ToolIntegrationRuntime(
            invocation_store=MagicMock(),
            job_store=InMemoryToolIntegrationJobStore(),
        )

    def _build_job(self, attempt_count: int = 1, jitter: bool | None = None) -> ToolIntegrationJob:
        now = datetime.now(timezone.utc)
        metadata = {
            "retry_backoff_base_seconds": 5,
            "retry_backoff_max_seconds": 300,
        }
        if jitter is not None:
            metadata["retry_jitter"] = jitter
        return ToolIntegrationJob(
            job_id="job-test",
            organization_id="org-test",
            tool_ref="crm.create_lead",
            invocation_id="inv-1",
            executor_kind="http",
            payload={},
            status="failed",
            attempt_count=attempt_count,
            submitted_at=now,
            last_progress_at=now,
            metadata=metadata,
        )

    def test_jitter_produces_varied_delays(self) -> None:
        runtime = self._build_runtime()
        job = self._build_job(attempt_count=1)

        base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        delays_seconds: list[float] = []
        for _ in range(100):
            next_at = runtime.compute_next_retry_at(job, now=base_time)
            delays_seconds.append((next_at - base_time).total_seconds())

        min_delay = min(delays_seconds)
        max_delay = max(delays_seconds)
        assert 3.5 < min_delay < 5.0, f"min delay {min_delay} outside expected band"
        assert 5.0 < max_delay < 6.5, f"max delay {max_delay} outside expected band"
        assert max_delay - min_delay > 0.5, "jitter produced too little variance"

    def test_jitter_disabled_produces_deterministic_delay(self) -> None:
        runtime = self._build_runtime()
        job = self._build_job(attempt_count=2, jitter=False)

        base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        delays = {
            (runtime.compute_next_retry_at(job, now=base_time) - base_time).total_seconds()
            for _ in range(10)
        }
        assert delays == {10.0}, f"jitter=False should be deterministic, got {delays}"

    def test_jitter_respects_min_floor(self) -> None:
        runtime = self._build_runtime()
        job = self._build_job(attempt_count=1)
        job.metadata["retry_backoff_base_seconds"] = 1

        base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for _ in range(50):
            next_at = runtime.compute_next_retry_at(job, now=base_time)
            delay = (next_at - base_time).total_seconds()
            assert delay >= 1.0, f"delay {delay} fell below 1s floor"


# ----- Sync circuit breaker tests --------------------------------------------

class TestSyncCircuitBreaker:
    def test_closed_state_allows_execution(self) -> None:
        breaker = CircuitBreaker()
        assert breaker.can_execute_sync() is True
        assert breaker.state == CircuitState.CLOSED

    def test_opens_after_failure_threshold(self) -> None:
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3))
        for _ in range(3):
            breaker.record_failure_sync()
        assert breaker.state == CircuitState.OPEN
        assert breaker.can_execute_sync() is False

    def test_successes_in_closed_reset_failure_counter(self) -> None:
        breaker = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3))
        breaker.record_failure_sync()
        breaker.record_failure_sync()
        breaker.record_success_sync()
        breaker.record_failure_sync()
        breaker.record_failure_sync()
        assert breaker.state == CircuitState.CLOSED

    def test_open_to_half_open_after_timeout(self) -> None:
        breaker = CircuitBreaker(CircuitBreakerConfig(
            failure_threshold=2, timeout_seconds=0.01
        ))
        breaker.record_failure_sync()
        breaker.record_failure_sync()
        assert breaker.state == CircuitState.OPEN

        time.sleep(0.02)
        allowed = breaker.can_execute_sync()
        assert allowed is True
        assert breaker.state == CircuitState.HALF_OPEN

    def test_half_open_closes_on_success_threshold(self) -> None:
        breaker = CircuitBreaker(CircuitBreakerConfig(
            failure_threshold=2, timeout_seconds=0.01, success_threshold=2
        ))
        breaker.record_failure_sync()
        breaker.record_failure_sync()
        time.sleep(0.02)
        breaker.can_execute_sync()
        breaker.record_success_sync()
        breaker.record_success_sync()
        assert breaker.state == CircuitState.CLOSED

    def test_half_open_trips_back_to_open_on_any_failure(self) -> None:
        breaker = CircuitBreaker(CircuitBreakerConfig(
            failure_threshold=2, timeout_seconds=0.01
        ))
        breaker.record_failure_sync()
        breaker.record_failure_sync()
        time.sleep(0.02)
        breaker.can_execute_sync()
        breaker.record_failure_sync()
        assert breaker.state == CircuitState.OPEN


class TestRegistrySync:
    def test_get_sync_creates_breaker_per_tool(self) -> None:
        registry = CircuitBreakerRegistry()
        b1 = registry.get_sync("tool_a")
        b2 = registry.get_sync("tool_b")
        b1_again = registry.get_sync("tool_a")
        assert b1 is b1_again
        assert b1 is not b2

    def test_sync_and_async_share_breakers(self) -> None:
        registry = CircuitBreakerRegistry()
        b_sync = registry.get_sync("shared_tool")

        async def fetch_async():
            return await registry.get("shared_tool")

        b_async = anyio.run(fetch_async)
        assert b_sync is b_async

    def test_state_change_visible_across_sync_and_async(self) -> None:
        registry = CircuitBreakerRegistry(
            CircuitBreakerConfig(failure_threshold=1)
        )
        breaker = registry.get_sync("tool")
        breaker.record_failure_sync()

        async def check():
            b = await registry.get("tool")
            return await b.can_execute()

        allowed = anyio.run(check)
        assert allowed is False, "Async path should see OPEN state set by sync recorder"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
