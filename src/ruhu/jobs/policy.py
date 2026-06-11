"""The single retry policy for all background work.

Replaces the per-module backoff implementations that used to live in
ticketing, notifications, and browser_tasks. Tune via ``RetryPolicy`` values
per job type — never by reimplementing the schedule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay_seconds: float = 30.0
    max_delay_seconds: float = 900.0


def next_retry_at(
    attempt_count: int,
    *,
    now: datetime,
    policy: RetryPolicy = RetryPolicy(),
) -> datetime | None:
    """When the next attempt should run, or ``None`` when attempts are exhausted.

    Exponential backoff: ``base * 2^(attempt-1)`` capped at ``max_delay``.
    ``attempt_count`` is the number of attempts already made.
    """
    if attempt_count >= policy.max_attempts:
        return None
    delay = min(
        policy.base_delay_seconds * (2 ** max(attempt_count - 1, 0)),
        policy.max_delay_seconds,
    )
    return now + timedelta(seconds=delay)
