"""Tests for Phase 4: Tool Runtime Hardening.

Covers:
- CircuitBreaker: initial state CLOSED; consecutive failures trip to OPEN;
  OPEN blocks all calls; timeout allows HALF_OPEN probe; HALF_OPEN successes
  close circuit; any HALF_OPEN failure re-opens; half_open_max_calls cap
- CircuitBreakerRegistry: lazy creation, identity (same breaker returned)
- ToolRuntime.execute_async(): validation failure; authorization deny;
  confirmation required; circuit open; missing executor; success path;
  asyncio.TimeoutError; executor exception; metrics recorded;
  circuit breaker record_success/record_failure called appropriately

All tests use anyio.run() — no pytest-asyncio marks.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import MagicMock, patch

import anyio
import pytest

from ruhu.tools.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    CircuitState,
)
from ruhu.tools.registry import ToolRegistry
from ruhu.tools.runtime import ToolRuntime
from ruhu.tools.specs import ToolSpec
from ruhu.tools.types import ToolCall, ToolCaller, ToolResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_spec(
    ref: str = "test.lookup",
    kind: str = "builtin",
    timeout_ms: int = 3000,
) -> ToolSpec:
    return ToolSpec(
        ref=ref,
        kind=kind,
        display_name="Test Tool",
        description="A test tool used only in unit tests for hardening.",
        timeout_ms=timeout_ms,
    )


def _make_call(tool_ref: str = "test.lookup") -> ToolCall:
    return ToolCall(
        tool_ref=tool_ref,
        args={},
        caller=ToolCaller(channel="web_chat"),
    )


def _make_runtime(spec: ToolSpec | None = None) -> ToolRuntime:
    s = spec or _make_spec()
    registry = ToolRegistry([s])
    return ToolRuntime(registry)


def _success_result(call: ToolCall) -> ToolResult:
    return ToolResult(
        invocation_id=call.invocation_id,
        tool_ref=call.tool_ref,
        status="success",
        output={"answer": 42},
    )


class _FakeExecutor:
    kind = "builtin"

    def __init__(self, result: ToolResult | None = None, *, raises=None):
        self._result = result
        self._raises = raises
        self.called = False

    def execute(self, spec, call) -> ToolResult:
        self.called = True
        if self._raises:
            raise self._raises
        return self._result or _success_result(call)


# ── CircuitBreaker ────────────────────────────────────────────────────────────

class TestCircuitBreakerInitialState:
    def test_starts_closed(self):
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED

    def test_can_execute_when_closed(self):
        cb = CircuitBreaker()

        async def _inner():
            return await cb.can_execute()

        result = anyio.run(_inner)
        assert result is True


class TestCircuitBreakerFailureTripping:
    def test_trips_to_open_after_threshold(self):
        cfg = CircuitBreakerConfig(failure_threshold=3)
        cb = CircuitBreaker(cfg)

        async def _inner():
            for _ in range(3):
                await cb.record_failure()

        anyio.run(_inner)
        assert cb.state == CircuitState.OPEN

    def test_does_not_trip_before_threshold(self):
        cfg = CircuitBreakerConfig(failure_threshold=3)
        cb = CircuitBreaker(cfg)

        async def _inner():
            for _ in range(2):
                await cb.record_failure()

        anyio.run(_inner)
        assert cb.state == CircuitState.CLOSED

    def test_success_resets_failure_counter(self):
        cfg = CircuitBreakerConfig(failure_threshold=3)
        cb = CircuitBreaker(cfg)

        async def _inner():
            await cb.record_failure()
            await cb.record_failure()
            await cb.record_success()  # reset
            await cb.record_failure()  # only 1 failure again

        anyio.run(_inner)
        assert cb.state == CircuitState.CLOSED  # threshold not reached


class TestCircuitBreakerOpenState:
    def _open_breaker(self, threshold: int = 1) -> CircuitBreaker:
        cfg = CircuitBreakerConfig(failure_threshold=threshold, timeout_seconds=999)
        cb = CircuitBreaker(cfg)

        async def _trip():
            for _ in range(threshold):
                await cb.record_failure()

        anyio.run(_trip)
        return cb

    def test_blocks_execution_when_open(self):
        cb = self._open_breaker()
        assert cb.state == CircuitState.OPEN

        async def _inner():
            return await cb.can_execute()

        result = anyio.run(_inner)
        assert result is False

    def test_transitions_to_half_open_after_timeout(self):
        cfg = CircuitBreakerConfig(failure_threshold=1, timeout_seconds=0.01)
        cb = CircuitBreaker(cfg)

        async def _inner():
            await cb.record_failure()
            assert cb.state == CircuitState.OPEN
            # wait for timeout
            await asyncio.sleep(0.02)
            allowed = await cb.can_execute()
            return allowed

        result = anyio.run(_inner)
        assert result is True
        assert cb.state == CircuitState.HALF_OPEN


class TestCircuitBreakerHalfOpenState:
    def _half_open_breaker(self) -> CircuitBreaker:
        cfg = CircuitBreakerConfig(
            failure_threshold=1,
            success_threshold=2,
            timeout_seconds=0.01,
            half_open_max_calls=2,
        )
        cb = CircuitBreaker(cfg)

        async def _setup():
            await cb.record_failure()
            await asyncio.sleep(0.02)
            await cb.can_execute()  # triggers OPEN → HALF_OPEN

        anyio.run(_setup)
        return cb

    def test_allows_calls_up_to_max(self):
        cfg = CircuitBreakerConfig(
            failure_threshold=1, timeout_seconds=0.01, half_open_max_calls=2
        )
        cb = CircuitBreaker(cfg)

        async def _inner():
            await cb.record_failure()
            await asyncio.sleep(0.02)
            r1 = await cb.can_execute()  # trips to HALF_OPEN, call 1
            r2 = await cb.can_execute()  # call 2
            r3 = await cb.can_execute()  # beyond max → False
            return r1, r2, r3

        r1, r2, r3 = anyio.run(_inner)
        assert r1 is True
        assert r2 is True
        assert r3 is False

    def test_closes_after_success_threshold(self):
        cfg = CircuitBreakerConfig(
            failure_threshold=1,
            success_threshold=2,
            timeout_seconds=0.01,
            half_open_max_calls=5,
        )
        cb = CircuitBreaker(cfg)

        async def _inner():
            await cb.record_failure()
            await asyncio.sleep(0.02)
            await cb.can_execute()  # move to HALF_OPEN
            await cb.record_success()
            await cb.record_success()  # hits threshold

        anyio.run(_inner)
        assert cb.state == CircuitState.CLOSED

    def test_reopens_on_failure_in_half_open(self):
        cfg = CircuitBreakerConfig(
            failure_threshold=1,
            timeout_seconds=0.01,
            half_open_max_calls=3,
        )
        cb = CircuitBreaker(cfg)

        async def _inner():
            await cb.record_failure()
            await asyncio.sleep(0.02)
            await cb.can_execute()  # → HALF_OPEN
            await cb.record_failure()  # any failure re-opens

        anyio.run(_inner)
        assert cb.state == CircuitState.OPEN


# ── CircuitBreakerRegistry ────────────────────────────────────────────────────

class TestCircuitBreakerRegistry:
    def test_creates_breaker_on_first_access(self):
        registry = CircuitBreakerRegistry()

        async def _inner():
            return await registry.get("tool.a")

        breaker = anyio.run(_inner)
        assert isinstance(breaker, CircuitBreaker)

    def test_returns_same_breaker_on_repeated_access(self):
        registry = CircuitBreakerRegistry()

        async def _inner():
            b1 = await registry.get("tool.a")
            b2 = await registry.get("tool.a")
            return b1, b2

        b1, b2 = anyio.run(_inner)
        assert b1 is b2

    def test_different_refs_get_different_breakers(self):
        registry = CircuitBreakerRegistry()

        async def _inner():
            b1 = await registry.get("tool.a")
            b2 = await registry.get("tool.b")
            return b1, b2

        b1, b2 = anyio.run(_inner)
        assert b1 is not b2


# ── ToolRuntime.execute_async ─────────────────────────────────────────────────

class TestExecuteAsyncValidation:
    def test_validation_failure_returns_error(self):
        spec = ToolSpec(
            ref="test.lookup",
            kind="builtin",
            display_name="Test",
            description="A test tool used only in unit tests for hardening.",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
        )
        registry = ToolRegistry([spec])
        runtime = ToolRuntime(registry)
        call = ToolCall(
            tool_ref="test.lookup",
            args={},  # missing required "name"
            caller=ToolCaller(channel="web_chat"),
        )

        async def _inner():
            return await runtime.execute_async(call)

        result = anyio.run(_inner)
        assert result.status == "error"
        assert result.metadata.get("failure_kind") == "validation_error"
        assert "validation" in result.metadata.get("error_type", "")


class TestExecuteAsyncAuthorization:
    def test_deny_returns_blocked(self):
        spec = _make_spec()
        registry = ToolRegistry([spec])
        from ruhu.tools.types import ToolAuthorizationResult
        authorizer = MagicMock()
        authorizer.authorize = MagicMock(
            return_value=ToolAuthorizationResult(decision="deny", reason="not allowed")
        )
        runtime = ToolRuntime(registry, authorizer=authorizer)
        call = _make_call()

        async def _inner():
            return await runtime.execute_async(call)

        result = anyio.run(_inner)
        assert result.status == "blocked"
        assert result.metadata.get("failure_kind") == "authorization_denied"

    def test_confirm_returns_confirmation_required(self):
        spec = _make_spec()
        registry = ToolRegistry([spec])
        from ruhu.tools.types import ToolAuthorizationResult
        authorizer = MagicMock()
        authorizer.authorize = MagicMock(
            return_value=ToolAuthorizationResult(
                decision="confirm", reason="needs approval"
            )
        )
        runtime = ToolRuntime(registry, authorizer=authorizer)
        call = _make_call()

        async def _inner():
            return await runtime.execute_async(call)

        result = anyio.run(_inner)
        assert result.status == "confirmation_required"
        assert result.metadata.get("failure_kind") == "confirmation_required"


class TestExecuteAsyncCircuitBreaker:
    def test_circuit_open_blocks_execution(self):
        spec = _make_spec()
        runtime = _make_runtime(spec)
        call = _make_call()

        async def _inner():
            breaker = await runtime._circuit_registry.get(call.tool_ref)
            # Trip the circuit open
            cfg = CircuitBreakerConfig(failure_threshold=1)
            breaker._cfg = cfg
            await breaker.record_failure()
            assert breaker.state == CircuitState.OPEN
            return await runtime.execute_async(call)

        result = anyio.run(_inner)
        assert result.status == "error"
        assert "circuit breaker" in result.error
        assert result.metadata.get("failure_kind") == "transient_upstream_error"
        assert result.metadata.get("circuit_state") == "open"

    def test_circuit_open_increments_metric(self):
        from ruhu.observability.metrics import tool_invocations_total

        spec = _make_spec()
        runtime = _make_runtime(spec)
        call = _make_call()

        # Read current count for circuit_open
        before = sum(
            sample.value
            for metric in tool_invocations_total.collect()
            for sample in metric.samples
            if sample.labels.get("status") == "circuit_open"
        )

        async def _inner():
            breaker = await runtime._circuit_registry.get(call.tool_ref)
            breaker._cfg = CircuitBreakerConfig(failure_threshold=1)
            await breaker.record_failure()
            return await runtime.execute_async(call)

        anyio.run(_inner)

        after = sum(
            sample.value
            for metric in tool_invocations_total.collect()
            for sample in metric.samples
            if sample.labels.get("status") == "circuit_open"
        )
        assert after > before


class TestExecuteAsyncMissingExecutor:
    def test_returns_error_when_no_executor(self):
        spec = _make_spec()
        runtime = _make_runtime(spec)  # no executors registered
        call = _make_call()

        async def _inner():
            return await runtime.execute_async(call)

        result = anyio.run(_inner)
        assert result.status == "error"
        assert "no executor" in result.error


class TestExecuteAsyncSuccess:
    def test_returns_success_result(self):
        spec = _make_spec()
        runtime = _make_runtime(spec)
        executor = _FakeExecutor()
        runtime.register_executor(executor)
        call = _make_call()

        async def _inner():
            return await runtime.execute_async(call)

        result = anyio.run(_inner)
        assert result.status == "success"
        assert executor.called

    def test_records_success_to_circuit_breaker(self):
        spec = _make_spec()
        runtime = _make_runtime(spec)
        executor = _FakeExecutor()
        runtime.register_executor(executor)
        call = _make_call()

        # Seed one failure so we can check it gets reset
        async def _inner():
            breaker = await runtime._circuit_registry.get(call.tool_ref)
            await breaker.record_failure()
            assert breaker._failures == 1
            await runtime.execute_async(call)
            return breaker._failures

        failures_after = anyio.run(_inner)
        assert failures_after == 0

    def test_invocation_stored_as_completed(self):
        spec = _make_spec()
        runtime = _make_runtime(spec)
        executor = _FakeExecutor()
        runtime.register_executor(executor)
        call = _make_call()

        async def _inner():
            await runtime.execute_async(call)
            return runtime.store.load(call.invocation_id)

        invocation = anyio.run(_inner)
        assert invocation is not None
        assert invocation.status == "completed"

    def test_metrics_recorded_on_success(self):
        from ruhu.observability.metrics import tool_invocations_total

        spec = _make_spec()
        runtime = _make_runtime(spec)
        executor = _FakeExecutor()
        runtime.register_executor(executor)
        call = _make_call()

        before = sum(
            sample.value
            for metric in tool_invocations_total.collect()
            for sample in metric.samples
            if sample.labels.get("status") == "success"
        )

        async def _inner():
            return await runtime.execute_async(call)

        anyio.run(_inner)

        after = sum(
            sample.value
            for metric in tool_invocations_total.collect()
            for sample in metric.samples
            if sample.labels.get("status") == "success"
        )
        assert after > before


class TestExecuteAsyncTimeout:
    def test_timeout_returns_timeout_result(self):
        spec = _make_spec(timeout_ms=50)
        runtime = _make_runtime(spec)

        class SlowExecutor:
            kind = "builtin"

            def execute(self, s, c):
                time.sleep(0.5)  # longer than the 50 ms timeout; thread exits quickly after
                return _success_result(c)

        runtime.register_executor(SlowExecutor())
        call = _make_call()

        async def _inner():
            return await runtime.execute_async(call)

        result = anyio.run(_inner)
        assert result.status == "timeout"
        assert "timeout" in result.error

    def test_timeout_records_failure_to_circuit_breaker(self):
        spec = _make_spec(timeout_ms=50)
        runtime = _make_runtime(spec)

        class SlowExecutor:
            kind = "builtin"

            def execute(self, s, c):
                time.sleep(0.5)  # longer than 50 ms timeout; thread exits shortly after
                return _success_result(c)

        runtime.register_executor(SlowExecutor())
        call = _make_call()

        async def _inner():
            await runtime.execute_async(call)
            return await runtime._circuit_registry.get(call.tool_ref)

        breaker = anyio.run(_inner)
        assert breaker._failures >= 1


class TestExecuteAsyncExecutorException:
    def test_exception_returns_error_result(self):
        spec = _make_spec()
        runtime = _make_runtime(spec)
        executor = _FakeExecutor(raises=ValueError("database offline"))
        runtime.register_executor(executor)
        call = _make_call()

        async def _inner():
            return await runtime.execute_async(call)

        result = anyio.run(_inner)
        assert result.status == "error"
        assert "database offline" in result.error

    def test_exception_records_failure_to_circuit_breaker(self):
        spec = _make_spec()
        runtime = _make_runtime(spec)
        executor = _FakeExecutor(raises=RuntimeError("boom"))
        runtime.register_executor(executor)
        call = _make_call()

        async def _inner():
            await runtime.execute_async(call)
            return await runtime._circuit_registry.get(call.tool_ref)

        breaker = anyio.run(_inner)
        assert breaker._failures >= 1

    def test_exception_invocation_stored_as_failed(self):
        spec = _make_spec()
        runtime = _make_runtime(spec)
        executor = _FakeExecutor(raises=RuntimeError("boom"))
        runtime.register_executor(executor)
        call = _make_call()

        async def _inner():
            await runtime.execute_async(call)
            return runtime.store.load(call.invocation_id)

        invocation = anyio.run(_inner)
        assert invocation is not None
        assert invocation.status == "failed"
