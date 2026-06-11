from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from ruhu.capture.audit import AuditWriter
from ruhu.capture.sqlalchemy_models import CaptureAuditOutboxRecord
from ruhu.capture.types import CaptureAuditRow


class SqlOutboxAuditWriter:
    """Durably enqueue capture audit rows for asynchronous delivery."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def write(self, rows: list[CaptureAuditRow]) -> None:
        if not rows:
            return
        now = datetime.now(timezone.utc)
        with self._session_factory.begin() as session:
            for row in rows:
                session.add(
                    CaptureAuditOutboxRecord(
                        id=str(uuid4()),
                        conversation_id=row.conversation_id,
                        turn_id=row.turn_id,
                        step_id=row.step_id,
                        payload=_row_payload(row),
                        status="pending",
                        attempt_count=0,
                        next_attempt_at=now,
                        last_error=None,
                        organization_id=row.organization_id,
                        created_at=now,
                        updated_at=now,
                    )
                )


def drain_capture_audit_outbox(
    session_factory,
    *,
    audit_writer: AuditWriter,
    batch_size: int = 100,
    max_attempts: int = 5,
    _now: datetime | None = None,
) -> int:
    """Deliver pending outbox rows to an AuditWriter with bounded retries."""
    now = _now or datetime.now(timezone.utc)
    delivered = 0
    with session_factory.begin() as session:
        records = list(
            session.scalars(
                select(CaptureAuditOutboxRecord)
                .where(
                    CaptureAuditOutboxRecord.status == "pending",
                    CaptureAuditOutboxRecord.next_attempt_at <= now,
                )
                .order_by(CaptureAuditOutboxRecord.created_at.asc())
                .limit(batch_size)
            ).all()
        )
        for record in records:
            try:
                audit_writer.write([_row_from_payload(record.payload)])
            except Exception as exc:  # noqa: BLE001
                record.attempt_count += 1
                record.last_error = str(exc)[:1000]
                record.updated_at = now
                if record.attempt_count >= max_attempts:
                    record.status = "failed"
                else:
                    delay_seconds = min(300, 2 ** max(0, record.attempt_count))
                    record.next_attempt_at = now + timedelta(seconds=delay_seconds)
                continue
            record.status = "delivered"
            record.updated_at = now
            delivered += 1
    return delivered


def _row_payload(row: CaptureAuditRow) -> dict[str, Any]:
    return asdict(row)


def _row_from_payload(payload: dict[str, Any]) -> CaptureAuditRow:
    return CaptureAuditRow(**payload)
