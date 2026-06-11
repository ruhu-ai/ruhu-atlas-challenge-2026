"""
Three-state circuit breaker for tool execution.

State transitions:
  CLOSED  →  OPEN      : failure_threshold consecutive failures
  OPEN    →  HALF_OPEN : timeout_seconds elapsed since last failure
  HALF_OPEN → CLOSED   : success_threshold consecutive successes
  HALF_OPEN → OPEN     : any single failure

Design notes:
- Each state transition is protected by an asyncio.Lock so concurrent callers
  can never race their way into an inconsistent state.
- half_open_max_calls limits the number of probe calls admitted concurrently
  while the circuit is in HALF_OPEN state.  Calls beyond that cap are rejected
  the same as OPEN — they don't count as failures.
"""
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class CircuitState(str, Enum):
    CLOSED    = "closed"     # Normal: execute requests
    OPEN      = "open"       # Failing: reject requests immediately
    HALF_OPEN = "half_open"  # Testing recovery: allow limited calls


@dataclass
class CircuitBreakerConfig:
    failure_threshold:   int   = 5    # consecutive failures to trip to OPEN
    success_threshold:   int   = 2    # successes in HALF_OPEN to return to CLOSED
    timeout_seconds:     float = 60.0  # wait in OPEN before trying HALF_OPEN
    half_open_max_calls: int   = 3    # concurrent probes admitted in HALF_OPEN


class CircuitBreaker:
    """Thread-safe three-state circuit breaker.

    State transitions are protected by a threading.Lock so both sync and async
    callers see a consistent view. The critical sections are microseconds long
    (just state reads/writes) — holding a sync lock inside an async handler is
    safe at this granularity.
    """

    def __init__(self, config: Optional[CircuitBreakerConfig] = None) -> None:
        self._cfg = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._successes = 0
        self._half_open_calls = 0
        self._last_failure: Optional[float] = None
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    def can_execute_sync(self) -> bool:
        """Sync variant of can_execute() for use from sync tool invocation paths."""
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                if (
                    self._last_failure is not None
                    and time.monotonic() - self._last_failure >= self._cfg.timeout_seconds
                ):
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                    self._successes = 0
                    # fall through to HALF_OPEN logic below
                else:
                    return False

            # HALF_OPEN
            if self._half_open_calls < self._cfg.half_open_max_calls:
                self._half_open_calls += 1
                return True
            return False

    async def can_execute(self) -> bool:
        """Return True if a call is permitted; False if the circuit blocks it."""
        return self.can_execute_sync()

    def record_success_sync(self) -> None:
        """Sync variant of record_success()."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._successes += 1
                if self._successes >= self._cfg.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failures = 0
            elif self._state == CircuitState.CLOSED:
                self._failures = 0

    async def record_success(self) -> None:
        """Record a successful execution.

        In HALF_OPEN: accumulate successes toward closing the circuit.
        In CLOSED:   reset consecutive failure counter.
        """
        self.record_success_sync()

    def record_failure_sync(self) -> None:
        """Sync variant of record_failure()."""
        with self._lock:
            self._last_failure = time.monotonic()
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
            elif self._state == CircuitState.CLOSED:
                self._failures += 1
                if self._failures >= self._cfg.failure_threshold:
                    self._state = CircuitState.OPEN

    async def record_failure(self) -> None:
        """Record a failed execution (timeout or exception).

        In HALF_OPEN: immediately trips back to OPEN.
        In CLOSED:    increments failure counter; trips to OPEN at threshold.
        """
        self.record_failure_sync()


class CircuitBreakerRegistry:
    """One breaker per tool_ref, shared for the lifetime of the process.

    Thread-safe: the registry uses a threading.Lock so both sync and async
    callers share the same breaker instance per tool_ref.
    """

    def __init__(self, default_config: Optional[CircuitBreakerConfig] = None) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self._default = default_config or CircuitBreakerConfig()
        self._lock = threading.Lock()

    def get_sync(self, tool_ref: str) -> CircuitBreaker:
        """Sync variant of get() for use from sync tool invocation paths."""
        with self._lock:
            if tool_ref not in self._breakers:
                self._breakers[tool_ref] = CircuitBreaker(self._default)
            return self._breakers[tool_ref]

    async def get(self, tool_ref: str) -> CircuitBreaker:
        """Return the breaker for *tool_ref*, creating one on first access."""
        return self.get_sync(tool_ref)
