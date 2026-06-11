from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ruhu.capture.sqlalchemy_models import CaptureAuditRecord
from ruhu.observability.retention import RetentionEventRecord, RetentionHoldRecord, SweepResult

if TYPE_CHECKING:
    from sqlalchemy import Engine


def sweep_capture_audit(
    engine: "Engine",
    *,
    audit_window_days: int = 90,
    batch_size: int = 500,
    worker_id: str | None = None,
    _now: datetime | None = None,
) -> SweepResult:
    """Delete expired capture audit rows, skipping legal/compliance holds."""
    start = time.monotonic()
    table = "capture_audit"
    now = _now or datetime.now(UTC)
    cutoff = now - timedelta(days=audit_window_days)
    rows_deleted = 0
    rows_skipped_hold = 0
    archival_pressure_after = 0
    errors: list[str] = []

    try:
        with Session(engine) as session:
            held_ids_subq = select(RetentionHoldRecord.resource_id).where(
                RetentionHoldRecord.resource_table == table
            )
            expired_filter = (
                CaptureAuditRecord.retention_policy == "audit_90d",
                CaptureAuditRecord.created_at < cutoff,
            )

            total_beyond_window = (
                session.scalar(select(func.count()).select_from(CaptureAuditRecord).where(*expired_filter))
                or 0
            )
            rows_skipped_hold = (
                session.scalar(
                    select(func.count())
                    .select_from(CaptureAuditRecord)
                    .where(*expired_filter, CaptureAuditRecord.id.in_(held_ids_subq))
                )
                or 0
            )
            eligible_ids = list(
                session.scalars(
                    select(CaptureAuditRecord.id)
                    .where(*expired_filter, CaptureAuditRecord.id.not_in(held_ids_subq))
                    .order_by(CaptureAuditRecord.created_at.asc())
                    .limit(batch_size)
                ).all()
            )
            if eligible_ids:
                result = session.execute(delete(CaptureAuditRecord).where(CaptureAuditRecord.id.in_(eligible_ids)))
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
