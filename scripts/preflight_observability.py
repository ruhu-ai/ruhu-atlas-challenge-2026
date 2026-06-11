#!/usr/bin/env python3
"""Pre-GA observability gate for Ruhu — Phase S6.

Validates that retention workers are configured before a production deployment.
Exits non-zero if any check fails so CI can gate the deploy job.

Checks
------
1. Trace retention sweep is enabled
   (RUHU_TRACE_RETENTION_SWEEP_ENABLED=true) with a valid hot-window
   (RUHU_TRACE_RETENTION_HOT_WINDOW_DAYS > 0).
2. Audit event retention sweep is enabled
   (RUHU_AUDIT_RETENTION_SWEEP_ENABLED=true) with a valid hot-window
   (RUHU_AUDIT_RETENTION_HOT_WINDOW_DAYS > 0, max 730 days / 2 years
   per spec §7).

Exit codes
----------
0 — all checks pass
1 — one or more checks failed (details written to stderr)

Usage
-----
    python scripts/preflight_observability.py

    # In CI (after sourcing .env):
    RUHU_TRACE_RETENTION_SWEEP_ENABLED=true \\
    RUHU_TRACE_RETENTION_HOT_WINDOW_DAYS=90 \\
    RUHU_AUDIT_RETENTION_SWEEP_ENABLED=true \\
    RUHU_AUDIT_RETENTION_HOT_WINDOW_DAYS=730 \\
    python scripts/preflight_observability.py
"""
from __future__ import annotations

import os
import sys


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes")


def _parse_positive_int(value: str | None, name: str) -> tuple[int | None, str | None]:
    """Return (parsed_value, error_message).  ``None`` means the var is unset."""
    if not value or not value.strip():
        return None, None
    try:
        n = int(value.strip())
    except ValueError:
        return None, f"{name} must be a positive integer, got {value!r}"
    if n <= 0:
        return None, f"{name} must be > 0, got {n}"
    return n, None


# ── Checks ────────────────────────────────────────────────────────────────────

def check_trace_retention() -> list[str]:
    """Return a list of failure strings (empty = pass)."""
    failures: list[str] = []

    enabled = _is_truthy(os.environ.get("RUHU_TRACE_RETENTION_SWEEP_ENABLED"))
    if not enabled:
        failures.append(
            "RUHU_TRACE_RETENTION_SWEEP_ENABLED is not set to 'true'. "
            "Enable the TurnTrace retention worker before going to production. "
            "Set RUHU_TRACE_RETENTION_HOT_WINDOW_DAYS (default 90) as well."
        )
        return failures  # window check only meaningful when enabled

    days_raw = os.environ.get("RUHU_TRACE_RETENTION_HOT_WINDOW_DAYS")
    days, err = _parse_positive_int(days_raw, "RUHU_TRACE_RETENTION_HOT_WINDOW_DAYS")
    if err:
        failures.append(err)

    return failures


def check_audit_retention() -> list[str]:
    """Return a list of failure strings (empty = pass)."""
    failures: list[str] = []

    enabled = _is_truthy(os.environ.get("RUHU_AUDIT_RETENTION_SWEEP_ENABLED"))
    if not enabled:
        failures.append(
            "RUHU_AUDIT_RETENTION_SWEEP_ENABLED is not set to 'true'. "
            "Enable the audit event retention worker before going to production. "
            "Spec §7 requires a maximum 730-day (2-year) hot-window: "
            "set RUHU_AUDIT_RETENTION_HOT_WINDOW_DAYS accordingly."
        )
        return failures

    days_raw = os.environ.get("RUHU_AUDIT_RETENTION_HOT_WINDOW_DAYS")
    days, err = _parse_positive_int(days_raw, "RUHU_AUDIT_RETENTION_HOT_WINDOW_DAYS")
    if err:
        failures.append(err)
    elif days is not None and days > 730:
        failures.append(
            f"RUHU_AUDIT_RETENTION_HOT_WINDOW_DAYS={days} exceeds the 730-day "
            "maximum required by spec §7 (2-year audit retention)."
        )

    return failures


# ── Entry point ───────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:  # noqa: ARG001
    failures: list[str] = []
    failures.extend(check_trace_retention())
    failures.extend(check_audit_retention())

    if failures:
        print("Pre-GA observability gate FAILED:", file=sys.stderr)
        for i, msg in enumerate(failures, 1):
            print(f"  {i}. {msg}", file=sys.stderr)
        return 1

    print("Pre-GA observability gate PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
