"""Audit event retention worker — Phase S6.

Hot-window: audit events older than ``hot_window_days`` (default 730 days /
2 years — per spec §7) are eligible for deletion.  Events referenced by
``retention_holds`` are exempt.

``AuditEventRecord.created_at`` is stored as an ISO 8601 string (``String(30)``).
ISO 8601 strings sort lexicographically, so string comparison against
``cutoff.isoformat()`` is semantically equivalent to a datetime comparison.

Metrics emitted after each sweep:
  - ``ruhu_retention_sweep_rows_total{table="audit_events"}``
  - ``ruhu_retention_sweep_duration_seconds{table="audit_events"}``
  - ``ruhu_retention_archival_pressure{table="audit_events"}``
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ruhu.audit.store import AuditEventRecord
from ruhu.observability.retention import RetentionEventRecord, RetentionHoldRecord, SweepResult

if TYPE_CHECKING:
    from sqlalchemy import Engine


def sweep_audit_events(
    engine: "Engine",
    *,
    hot_window_days: int = 730,
    batch_size: int = 500,
    worker_id: str | None = None,
    _now: datetime | None = None,
) -> SweepResult:
    """Delete AuditEvent rows older than ``hot_window_days``, skipping legal holds.

    Mirrors ``sweep_turn_traces`` in structure.  Uses ISO 8601 string comparison
    for ``created_at`` (the column type is ``String(30)`` and values are always
    UTC ISO 8601).
    """
    start = time.monotonic()
    table = "audit_events"
    now = _now or datetime.now(UTC)
    cutoff = now - timedelta(days=hot_window_days)
    cutoff_str = cutoff.isoformat()
    rows_deleted = 0
    rows_skipped_hold = 0
    archival_pressure_after = 0
    errors: list[str] = []

    try:
        with Session(engine) as session:
            held_ids_subq = select(RetentionHoldRecord.resource_id).where(
                RetentionHoldRecord.resource_table == table
            )

            total_beyond_window: int = (
                session.scalar(
                    select(func.count())
                    .select_from(AuditEventRecord)
                    .where(AuditEventRecord.created_at < cutoff_str)
                )
                or 0
            )

            held_in_window: int = (
                session.scalar(
                    select(func.count())
                    .select_from(AuditEventRecord)
                    .where(
                        AuditEventRecord.created_at < cutoff_str,
                        AuditEventRecord.event_id.in_(held_ids_subq),
                    )
                )
                or 0
            )
            rows_skipped_hold = held_in_window

            eligible_ids = list(
                session.scalars(
                    select(AuditEventRecord.event_id)
                    .where(
                        AuditEventRecord.created_at < cutoff_str,
                        AuditEventRecord.event_id.not_in(held_ids_subq),
                    )
                    .limit(batch_size)
                ).all()
            )

            if eligible_ids:
                result = session.execute(
                    delete(AuditEventRecord).where(
                        AuditEventRecord.event_id.in_(eligible_ids)
                    )
                )
                rows_deleted = result.rowcount or len(eligible_ids)

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

    try:
        from ruhu.observability.metrics import (
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
