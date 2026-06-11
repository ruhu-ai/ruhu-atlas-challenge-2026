"""Phase S6 — Retention worker + pre-GA gate tests.

Coverage:
  - sweep_turn_traces: deletes old rows, skips held rows, writes audit event
  - sweep_audit_events: same for audit_events table
  - Metrics emitted by each sweep
  - Pre-GA gate script (check_trace_retention, check_audit_retention, main)
  - Zero-deletion sweeps still write audit rows
  - SweepResult fields are accurate
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ruhu.audit.store import AuditEventRecord
from ruhu.db_models import TurnTraceRecord
from ruhu.observability.retention import (
    RetentionEventRecord,
    RetentionHoldRecord,
    SweepResult,
    sweep_turn_traces,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def engine():
    """In-memory SQLite engine with only the tables needed for S6 tests."""
    eng = create_engine("sqlite:///:memory:")
    # Create only the tables we use — avoids Postgres ARRAY incompatibility
    # that exists on the rule_bindings table in Base.metadata.
    for tbl in [
        TurnTraceRecord.__table__,
        AuditEventRecord.__table__,
        RetentionHoldRecord.__table__,
        RetentionEventRecord.__table__,
    ]:
        tbl.create(eng, checkfirst=True)
    return eng


# ── Helpers ───────────────────────────────────────────────────────────────────

def _insert_trace(session: Session, trace_id: str, recorded_at: datetime) -> None:
    session.add(
        TurnTraceRecord(
            trace_id=trace_id,
            conversation_id="conv-test",
            turn_id=f"turn-{trace_id}",
            agent_id="agent-test",
            step_before="start",
            step_after="end",
            chosen_action_json={"type": "stay", "reason": "test"},
            recorded_at=recorded_at,
        )
    )


def _insert_audit(session: Session, event_id: str, created_at: datetime) -> None:
    from ruhu.audit.events import AuditEvent

    # Build a minimal AuditEvent and persist it directly via ORM to avoid
    # hash-chain machinery.
    session.add(
        AuditEventRecord(
            event_id=event_id,
            organization_id="org-test",
            event_type="test.event",
            operation="create",
            outcome="success",
            detail={},
            content_hash="aabbcc",
            created_at=created_at.isoformat(),
        )
    )


_NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)
_OLD = _NOW - timedelta(days=100)    # beyond the 90-day window used in tests
_RECENT = _NOW - timedelta(days=10)  # within the 90-day window


# ═══════════════════════════════════════════════════════════════════════════════
# TurnTrace retention sweep
# ═══════════════════════════════════════════════════════════════════════════════

class TestSweepTurnTraces:

    def test_deletes_old_rows_and_keeps_recent(self, engine):
        with Session(engine) as s:
            _insert_trace(s, "old-1", _OLD)
            _insert_trace(s, "old-2", _OLD)
            _insert_trace(s, "recent-1", _RECENT)
            s.commit()

        result = sweep_turn_traces(engine, hot_window_days=90, _now=_NOW)

        assert result.rows_deleted == 2
        assert result.table == "turn_traces"

        with Session(engine) as s:
            remaining = list(s.scalars(select(TurnTraceRecord)))
        assert len(remaining) == 1
        assert remaining[0].trace_id == "recent-1"

    def test_skips_held_rows(self, engine):
        with Session(engine) as s:
            _insert_trace(s, "old-held", _OLD)
            _insert_trace(s, "old-free", _OLD)
            s.add(
                RetentionHoldRecord(
                    resource_table="turn_traces",
                    resource_id="old-held",
                    hold_reason="legal investigation",
                    held_at=_NOW,
                )
            )
            s.commit()

        result = sweep_turn_traces(engine, hot_window_days=90, _now=_NOW)

        assert result.rows_deleted == 1
        assert result.rows_skipped_hold == 1

        with Session(engine) as s:
            remaining = list(s.scalars(select(TurnTraceRecord)))
        assert len(remaining) == 1
        assert remaining[0].trace_id == "old-held"

    def test_writes_retention_event_on_deletion(self, engine):
        with Session(engine) as s:
            _insert_trace(s, "old-1", _OLD)
            s.commit()

        sweep_turn_traces(engine, hot_window_days=90, worker_id="test-worker", _now=_NOW)

        with Session(engine) as s:
            events = list(s.scalars(select(RetentionEventRecord)))
        assert len(events) == 1
        evt = events[0]
        assert evt.resource_table == "turn_traces"
        assert evt.rows_deleted == 1
        assert evt.worker_id == "test-worker"

    def test_writes_retention_event_on_zero_deletion(self, engine):
        """Even when nothing is deleted, an audit event is written."""
        with Session(engine) as s:
            _insert_trace(s, "recent-1", _RECENT)
            s.commit()

        sweep_turn_traces(engine, hot_window_days=90, _now=_NOW)

        with Session(engine) as s:
            events = list(s.scalars(select(RetentionEventRecord)))
        assert len(events) == 1
        assert events[0].rows_deleted == 0

    def test_respects_batch_size(self, engine):
        with Session(engine) as s:
            for i in range(5):
                _insert_trace(s, f"old-{i}", _OLD)
            s.commit()

        result = sweep_turn_traces(engine, hot_window_days=90, batch_size=3, _now=_NOW)

        # Only the batch was deleted, not all 5.
        assert result.rows_deleted == 3
        with Session(engine) as s:
            remaining = list(s.scalars(select(TurnTraceRecord)))
        assert len(remaining) == 2

    def test_archival_pressure_counts_remaining_after_sweep(self, engine):
        with Session(engine) as s:
            for i in range(5):
                _insert_trace(s, f"old-{i}", _OLD)
            s.commit()

        result = sweep_turn_traces(engine, hot_window_days=90, batch_size=3, _now=_NOW)

        # 5 total, 3 deleted → 2 remain in the expired window.
        assert result.archival_pressure == 2

    def test_no_rows_returns_zero_deleted(self, engine):
        result = sweep_turn_traces(engine, hot_window_days=90, _now=_NOW)
        assert result.rows_deleted == 0
        assert result.errors == []

    def test_duration_is_positive(self, engine):
        result = sweep_turn_traces(engine, hot_window_days=90, _now=_NOW)
        assert result.duration_seconds >= 0

    def test_held_rows_do_not_count_toward_archival_pressure_reduction(self, engine):
        """Held rows remain in the DB; pressure is reduced only by actual deletions."""
        with Session(engine) as s:
            _insert_trace(s, "old-held", _OLD)
            _insert_trace(s, "old-free", _OLD)
            s.add(
                RetentionHoldRecord(
                    resource_table="turn_traces",
                    resource_id="old-held",
                    hold_reason="hold",
                    held_at=_NOW,
                )
            )
            s.commit()

        result = sweep_turn_traces(engine, hot_window_days=90, _now=_NOW)
        # 2 beyond window, 1 deleted → 1 still in window (the held one)
        assert result.archival_pressure == 1


# ═══════════════════════════════════════════════════════════════════════════════
# AuditEvent retention sweep
# ═══════════════════════════════════════════════════════════════════════════════

class TestSweepAuditEvents:

    def test_deletes_old_rows_and_keeps_recent(self, engine):
        with Session(engine) as s:
            _insert_audit(s, "evt-old-1", _OLD)
            _insert_audit(s, "evt-old-2", _OLD)
            _insert_audit(s, "evt-recent", _RECENT)
            s.commit()

        from ruhu.audit.retention import sweep_audit_events

        result = sweep_audit_events(engine, hot_window_days=90, _now=_NOW)

        assert result.rows_deleted == 2
        assert result.table == "audit_events"

        with Session(engine) as s:
            remaining = list(s.scalars(select(AuditEventRecord)))
        assert len(remaining) == 1
        assert remaining[0].event_id == "evt-recent"

    def test_skips_held_rows(self, engine):
        with Session(engine) as s:
            _insert_audit(s, "evt-held", _OLD)
            _insert_audit(s, "evt-free", _OLD)
            s.add(
                RetentionHoldRecord(
                    resource_table="audit_events",
                    resource_id="evt-held",
                    hold_reason="compliance hold",
                    held_at=_NOW,
                )
            )
            s.commit()

        from ruhu.audit.retention import sweep_audit_events

        result = sweep_audit_events(engine, hot_window_days=90, _now=_NOW)

        assert result.rows_deleted == 1
        assert result.rows_skipped_hold == 1

        with Session(engine) as s:
            remaining = list(s.scalars(select(AuditEventRecord)))
        assert len(remaining) == 1
        assert remaining[0].event_id == "evt-held"

    def test_writes_retention_event(self, engine):
        with Session(engine) as s:
            _insert_audit(s, "evt-old", _OLD)
            s.commit()

        from ruhu.audit.retention import sweep_audit_events

        sweep_audit_events(engine, hot_window_days=90, worker_id="audit-worker", _now=_NOW)

        with Session(engine) as s:
            events = list(s.scalars(select(RetentionEventRecord)))
        assert len(events) == 1
        assert events[0].resource_table == "audit_events"
        assert events[0].rows_deleted == 1
        assert events[0].worker_id == "audit-worker"

    def test_uses_default_730_day_window(self, engine):
        """The default hot_window_days=730: a 729-day-old record is NOT deleted,
        but a 731-day-old record is."""
        just_inside = _NOW - timedelta(days=729)
        just_outside = _NOW - timedelta(days=731)
        with Session(engine) as s:
            _insert_audit(s, "evt-inside", just_inside)
            _insert_audit(s, "evt-outside", just_outside)
            s.commit()

        from ruhu.audit.retention import sweep_audit_events

        result = sweep_audit_events(engine, _now=_NOW)  # default hot_window_days=730
        assert result.rows_deleted == 1

        with Session(engine) as s:
            remaining = list(s.scalars(select(AuditEventRecord)))
        assert len(remaining) == 1
        assert remaining[0].event_id == "evt-inside"


# ═══════════════════════════════════════════════════════════════════════════════
# Metrics
# ═══════════════════════════════════════════════════════════════════════════════

class TestRetentionMetrics:

    def _get_counter_value(self, registry, name_base: str, table: str) -> float:
        for metric in registry.collect():
            if metric.name == name_base:
                for sample in metric.samples:
                    if sample.labels.get("table") == table:
                        return sample.value
        return 0.0

    def test_sweep_rows_total_increments(self, engine):
        from prometheus_client import CollectorRegistry, Counter, Histogram, Gauge
        from ruhu.observability import metrics as _m

        # Capture current value from the real registry
        before = sum(
            s.value
            for m in _m.registry.collect()
            if m.name == "ruhu_retention_sweep_rows"
            for s in m.samples
            if s.labels.get("table") == "turn_traces"
        )

        with Session(engine) as s:
            _insert_trace(s, "old-for-metrics", _OLD)
            s.commit()

        sweep_turn_traces(engine, hot_window_days=90, _now=_NOW)

        after = sum(
            s.value
            for m in _m.registry.collect()
            if m.name == "ruhu_retention_sweep_rows"
            for s in m.samples
            if s.labels.get("table") == "turn_traces"
        )

        assert after - before == 1.0

    def test_sweep_rows_registered_in_registry(self):
        from ruhu.observability import metrics as _m

        names = {m.name for m in _m.registry.collect()}
        assert "ruhu_retention_sweep_rows" in names
        assert "ruhu_retention_sweep_duration_seconds" in names
        assert "ruhu_retention_archival_pressure" in names


# ═══════════════════════════════════════════════════════════════════════════════
# Pre-GA gate script
# ═══════════════════════════════════════════════════════════════════════════════

class TestPreflightScript:

    def _run(self, env_overrides: dict[str, str]) -> tuple[int, str]:
        """Run the preflight script with a clean env + overrides."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("RUHU_")}
        env.update(env_overrides)
        result = subprocess.run(
            [sys.executable, "scripts/preflight_observability.py"],
            capture_output=True,
            text=True,
            env=env,
            cwd=Path(__file__).resolve().parents[1],
        )
        return result.returncode, result.stderr

    def test_passes_when_both_enabled(self):
        rc, _ = self._run(
            {
                "RUHU_TRACE_RETENTION_SWEEP_ENABLED": "true",
                "RUHU_TRACE_RETENTION_HOT_WINDOW_DAYS": "90",
                "RUHU_AUDIT_RETENTION_SWEEP_ENABLED": "true",
                "RUHU_AUDIT_RETENTION_HOT_WINDOW_DAYS": "730",
            }
        )
        assert rc == 0

    def test_fails_when_trace_retention_disabled(self):
        rc, stderr = self._run(
            {
                "RUHU_AUDIT_RETENTION_SWEEP_ENABLED": "true",
                "RUHU_AUDIT_RETENTION_HOT_WINDOW_DAYS": "730",
            }
        )
        assert rc == 1
        assert "RUHU_TRACE_RETENTION_SWEEP_ENABLED" in stderr

    def test_fails_when_audit_retention_disabled(self):
        rc, stderr = self._run(
            {
                "RUHU_TRACE_RETENTION_SWEEP_ENABLED": "true",
                "RUHU_TRACE_RETENTION_HOT_WINDOW_DAYS": "90",
            }
        )
        assert rc == 1
        assert "RUHU_AUDIT_RETENTION_SWEEP_ENABLED" in stderr

    def test_fails_when_both_disabled(self):
        rc, stderr = self._run({})
        assert rc == 1
        assert "RUHU_TRACE_RETENTION_SWEEP_ENABLED" in stderr
        assert "RUHU_AUDIT_RETENTION_SWEEP_ENABLED" in stderr

    def test_fails_when_audit_window_exceeds_730_days(self):
        rc, stderr = self._run(
            {
                "RUHU_TRACE_RETENTION_SWEEP_ENABLED": "true",
                "RUHU_AUDIT_RETENTION_SWEEP_ENABLED": "true",
                "RUHU_AUDIT_RETENTION_HOT_WINDOW_DAYS": "800",
            }
        )
        assert rc == 1
        assert "730" in stderr

    def test_fails_when_trace_window_is_zero(self):
        rc, stderr = self._run(
            {
                "RUHU_TRACE_RETENTION_SWEEP_ENABLED": "true",
                "RUHU_TRACE_RETENTION_HOT_WINDOW_DAYS": "0",
                "RUHU_AUDIT_RETENTION_SWEEP_ENABLED": "true",
            }
        )
        assert rc == 1

    def test_fails_when_trace_window_is_invalid_string(self):
        rc, stderr = self._run(
            {
                "RUHU_TRACE_RETENTION_SWEEP_ENABLED": "true",
                "RUHU_TRACE_RETENTION_HOT_WINDOW_DAYS": "ninety",
                "RUHU_AUDIT_RETENTION_SWEEP_ENABLED": "true",
            }
        )
        assert rc == 1
        assert "integer" in stderr.lower()

    def test_check_functions_directly(self):
        """Unit-test the check functions without subprocess overhead."""
        from scripts.preflight_observability import check_audit_retention, check_trace_retention

        # Temporarily set env vars for the check functions.
        env_backup = {
            k: os.environ.get(k)
            for k in [
                "RUHU_TRACE_RETENTION_SWEEP_ENABLED",
                "RUHU_TRACE_RETENTION_HOT_WINDOW_DAYS",
                "RUHU_AUDIT_RETENTION_SWEEP_ENABLED",
                "RUHU_AUDIT_RETENTION_HOT_WINDOW_DAYS",
            ]
        }

        try:
            os.environ["RUHU_TRACE_RETENTION_SWEEP_ENABLED"] = "true"
            os.environ["RUHU_TRACE_RETENTION_HOT_WINDOW_DAYS"] = "90"
            assert check_trace_retention() == []

            os.environ["RUHU_AUDIT_RETENTION_SWEEP_ENABLED"] = "true"
            os.environ["RUHU_AUDIT_RETENTION_HOT_WINDOW_DAYS"] = "730"
            assert check_audit_retention() == []

            del os.environ["RUHU_TRACE_RETENTION_SWEEP_ENABLED"]
            assert check_trace_retention() != []

        finally:
            for k, v in env_backup.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


# ═══════════════════════════════════════════════════════════════════════════════
# RetentionHoldRecord composite PK
# ═══════════════════════════════════════════════════════════════════════════════

class TestRetentionHoldRecord:

    def test_hold_applies_per_table(self, engine):
        """A hold on turn_traces does not affect audit_events with the same id."""
        shared_id = "shared-id"
        with Session(engine) as s:
            _insert_trace(s, shared_id, _OLD)
            _insert_audit(s, shared_id, _OLD)
            # Hold only on turn_traces
            s.add(
                RetentionHoldRecord(
                    resource_table="turn_traces",
                    resource_id=shared_id,
                    hold_reason="scoped hold",
                    held_at=_NOW,
                )
            )
            s.commit()

        # Trace sweep: should skip due to hold
        result_traces = sweep_turn_traces(engine, hot_window_days=90, _now=_NOW)
        assert result_traces.rows_deleted == 0
        assert result_traces.rows_skipped_hold == 1

        # Audit sweep: no hold on audit_events, should delete
        from ruhu.audit.retention import sweep_audit_events

        result_audit = sweep_audit_events(engine, hot_window_days=90, _now=_NOW)
        assert result_audit.rows_deleted == 1
        assert result_audit.rows_skipped_hold == 0
