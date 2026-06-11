from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from ruhu.capture.audit import InMemoryAuditWriter
from ruhu.capture.outbox import SqlOutboxAuditWriter, drain_capture_audit_outbox
from ruhu.capture.sqlalchemy_models import CaptureAuditOutboxRecord, CaptureAuditRecord
from ruhu.capture.types import CaptureAuditRow
from ruhu.capture.worker import CaptureAudit
from ruhu.observability.retention import RetentionEventRecord, RetentionHoldRecord


def _session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    CaptureAuditOutboxRecord.__table__.create(engine)
    CaptureAuditRecord.__table__.create(engine)
    RetentionHoldRecord.__table__.create(engine)
    RetentionEventRecord.__table__.create(engine)
    return sessionmaker(engine, future=True)


def test_sql_outbox_audit_writer_enqueues_and_drains_rows() -> None:
    session_factory = _session_factory()
    row = CaptureAuditRow(
        conversation_id="conversation-1",
        turn_id="turn-1",
        step_id="step-1",
        fact_name="email",
        source="deterministic",
        outcome="accepted",
        raw_value="user@example.com",
        normalized_value="user@example.com",
        confidence=1.0,
        organization_id="org-1",
    )

    SqlOutboxAuditWriter(session_factory).write([row])
    sink = InMemoryAuditWriter()
    delivered = drain_capture_audit_outbox(session_factory, audit_writer=sink)

    assert delivered == 1
    assert sink.rows[0].fact_name == "email"
    with session_factory() as session:
        records = list(session.scalars(select(CaptureAuditOutboxRecord)).all())
    assert records[0].status == "delivered"


def test_capture_audit_outbox_marks_failed_after_bounded_retries() -> None:
    class FailingWriter:
        def write(self, rows):
            raise RuntimeError("sink down")

    session_factory = _session_factory()
    SqlOutboxAuditWriter(session_factory).write(
        [
            CaptureAuditRow(
                conversation_id="conversation-1",
                turn_id="turn-1",
                step_id=None,
                fact_name="email",
                source="deterministic",
                outcome="accepted",
            )
        ]
    )

    drain_capture_audit_outbox(
        session_factory,
        audit_writer=FailingWriter(),
        max_attempts=1,
        _now=datetime.now(timezone.utc),
    )

    with session_factory() as session:
        record = session.scalar(select(CaptureAuditOutboxRecord))
    assert record is not None
    assert record.status == "failed"
    assert record.attempt_count == 1
    assert "sink down" in (record.last_error or "")


def test_capture_audit_worker_process_once_drains_outbox() -> None:
    session_factory = _session_factory()
    SqlOutboxAuditWriter(session_factory).write(
        [
            CaptureAuditRow(
                conversation_id="conversation-1",
                turn_id="turn-1",
                step_id="step-1",
                fact_name="email",
                source="deterministic",
                outcome="accepted",
            )
        ]
    )
    sink = InMemoryAuditWriter()
    worker = CaptureAudit(
        session_factory=session_factory,
        audit_writer=sink,
        outbox_batch_size=10,
    )

    summary = worker.process_once()

    assert summary.outbox_delivered_count == 1
    assert sink.rows[0].fact_name == "email"
    assert summary.model_dump()["outbox_delivered_count"] == 1


def test_capture_audit_worker_process_once_sweeps_expired_audit_rows() -> None:
    session_factory = _session_factory()
    now = datetime.now(timezone.utc)
    with session_factory.begin() as session:
        session.add(
            CaptureAuditRecord(
                id="old-row",
                conversation_id="conversation-1",
                turn_id="turn-old",
                step_id="step-1",
                fact_name="email",
                storage_scope="conversation",
                retention_policy="audit_90d",
                sensitivity="personal",
                audit_raw_policy="hash",
                source="deterministic",
                outcome="accepted",
                created_at=now - timedelta(days=120),
            )
        )
        session.add(
            CaptureAuditRecord(
                id="fresh-row",
                conversation_id="conversation-1",
                turn_id="turn-new",
                step_id="step-1",
                fact_name="email",
                storage_scope="conversation",
                retention_policy="audit_90d",
                sensitivity="personal",
                audit_raw_policy="hash",
                source="deterministic",
                outcome="accepted",
                created_at=now,
            )
        )
    worker = CaptureAudit(
        session_factory=session_factory,
        audit_writer=InMemoryAuditWriter(),
        retention_days=90,
        retention_batch_size=10,
    )

    summary = worker.process_once()

    assert summary.retention_deleted_count == 1
    with session_factory() as session:
        remaining_ids = set(session.scalars(select(CaptureAuditRecord.id)).all())
    assert remaining_ids == {"fresh-row"}
