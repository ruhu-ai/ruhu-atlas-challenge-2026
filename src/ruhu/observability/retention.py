"""TurnTrace retention worker — Phase S6.

Hot-window: rows older than ``hot_window_days`` (default 90) are eligible for
deletion.  Rows referenced by ``retention_holds`` are exempt.

Archive strategy: DELETE with a ``retention_events`` audit row per sweep batch.
The deletion uses the application DB session; a separate privileged role for
actual DELETE against ``turn_traces`` (which is append-only for the app writer
role) should be configured at deploy time.  In dev/test the same engine is
used for both table access and deletion.

Metrics emitted after each sweep:
  - ``ruhu_retention_sweep_rows_total{table="turn_traces"}``
  - ``ruhu_retention_sweep_duration_seconds{table="turn_traces"}``
  - ``ruhu_retention_archival_pressure{table="turn_traces"}``
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String, delete, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ruhu.db_models import Base, TurnTraceRecord

if TYPE_CHECKING:
    from sqlalchemy import Engine


# ── Retention hold table ──────────────────────────────────────────────────────

class RetentionHoldRecord(Base):
    """Active legal or compliance hold exempting a row from retention deletion.

    Composite PK: (resource_table, resource_id) so holds can be placed on any
    table without schema changes.
    """

    __tablename__ = "retention_holds"

    resource_table: Mapped[str] = mapped_column(String(64), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    hold_reason: Mapped[str] = mapped_column(String(255), nullable=False)
    held_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    held_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ── Retention event log ───────────────────────────────────────────────────────

class RetentionEventRecord(Base):
    """Append-only audit log entry written after every retention sweep batch.

    Presence of this row proves that a deletion happened and when.
    """

    __tablename__ = "retention_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource_table: Mapped[str] = mapped_column(String(64), nullable=False)
    rows_deleted: Mapped[int] = mapped_column(Integer, nullable=False)
    cutoff_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    swept_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class SweepResult:
    """Summary of one retention sweep batch."""

    table: str
    rows_deleted: int
    rows_skipped_hold: int
    archival_pressure: int  # rows beyond window remaining after sweep
    duration_seconds: float
    errors: list[str] = field(default_factory=list)


# ── Core sweep function ───────────────────────────────────────────────────────

def sweep_turn_traces(
    engine: "Engine",
    *,
    hot_window_days: int = 90,
    batch_size: int = 500,
    worker_id: str | None = None,
    _now: datetime | None = None,
) -> SweepResult:
    """Delete TurnTrace rows older than ``hot_window_days``, skipping legal holds.

    Writes one ``RetentionEventRecord`` row per sweep (even zero-deletion runs
    are recorded so the worker's activity is auditable).  Emits Prometheus
    metrics after the DB transaction commits.

    ``_now`` is injectable for testing.
    """
    start = time.monotonic()
    table = "turn_traces"
    now = _now or datetime.now(UTC)
    cutoff = now - timedelta(days=hot_window_days)
    rows_deleted = 0
    rows_skipped_hold = 0
    archival_pressure_after = 0
    errors: list[str] = []

    try:
        with Session(engine) as session:
            held_ids_subq = select(RetentionHoldRecord.resource_id).where(
                RetentionHoldRecord.resource_table == table
            )

            # Total rows beyond window (including held) — gives raw pressure.
            total_beyond_window: int = (
                session.scalar(
                    select(func.count())
                    .select_from(TurnTraceRecord)
                    .where(TurnTraceRecord.recorded_at < cutoff)
                )
                or 0
            )

            # Rows on legal hold within the expired window.
            held_in_window: int = (
                session.scalar(
                    select(func.count())
                    .select_from(TurnTraceRecord)
                    .where(
                        TurnTraceRecord.recorded_at < cutoff,
                        TurnTraceRecord.trace_id.in_(held_ids_subq),
                    )
                )
                or 0
            )
            rows_skipped_hold = held_in_window

            # IDs eligible for deletion this batch.
            eligible_ids = list(
                session.scalars(
                    select(TurnTraceRecord.trace_id)
                    .where(
                        TurnTraceRecord.recorded_at < cutoff,
                        TurnTraceRecord.trace_id.not_in(held_ids_subq),
                    )
                    .limit(batch_size)
                ).all()
            )

            if eligible_ids:
                result = session.execute(
                    delete(TurnTraceRecord).where(
                        TurnTraceRecord.trace_id.in_(eligible_ids)
                    )
                )
                rows_deleted = result.rowcount or len(eligible_ids)

            # Audit log — write even for zero-row sweeps.
            session.add(
                RetentionEventRecord(
                    resource_table=table,
                    rows_deleted=rows_deleted,
                    cutoff_date=cutoff,
                    swept_at=datetime.now(UTC),
                    worker_id=worker_id,
                )
            )
            session.commit()

        archival_pressure_after = max(0, total_beyond_window - rows_deleted)

    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))

    duration = time.monotonic() - start

    # Metrics — best-effort; never raise.
    try:
        from .metrics import (
            retention_archival_pressure,
            retention_sweep_duration_seconds,
            retention_sweep_rows_total,
        )

        retention_sweep_rows_total.labels(table=table).inc(rows_deleted)
        retention_sweep_duration_seconds.labels(table=table).observe(duration)
        retention_archival_pressure.labels(table=table).set(archival_pressure_after)
    except Exception:  # noqa: BLE001
        pass

    return SweepResult(
        table=table,
        rows_deleted=rows_deleted,
        rows_skipped_hold=rows_skipped_hold,
        archival_pressure=archival_pressure_after,
        duration_seconds=duration,
        errors=errors,
    )
