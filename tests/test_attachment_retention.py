"""Tests for the attachment retention system.

Covers:
  - default retention applied at upload time
  - soft-delete filtering in projection/list/get/materialize reads
  - retention worker: soft-delete pass finds expired attachments
  - retention worker: hard-delete pass removes rows past grace
  - worker idempotency: re-running on the same state is a no-op
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ruhu.attachments.models import AttachmentView
from ruhu.attachments.retention_worker import AttachmentRetention
from ruhu.attachments.service import AttachmentService
from ruhu.attachments.store import InMemoryAttachmentStore, SQLAlchemyAttachmentStore
from ruhu.attachments.sqlalchemy_models import (
    AttachmentBlobRecord,
    AttachmentRecord,
    AttachmentViewRecord,
)
from ruhu.db import build_session_factory
from ruhu.db_models import Base


# ══════════════════════════════════════════════════════════════════════════════
# Service-level default retention + soft-delete filtering (in-memory)
# ══════════════════════════════════════════════════════════════════════════════


def test_service_applies_default_retention_days_when_set() -> None:
    service = AttachmentService(
        InMemoryAttachmentStore(),
        max_file_bytes=1024 * 1024,
        default_retention_days=30,
    )
    before = datetime.now(timezone.utc)
    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="web_widget",
        filename="notes.txt",
        content_type="text/plain",
        content_bytes=b"hello",
    )
    after = datetime.now(timezone.utc)
    assert attachment.retention_expires_at is not None
    # Expires 30 days in the future, +/- the time the test took.
    expected_min = before + timedelta(days=30)
    expected_max = after + timedelta(days=30)
    assert expected_min <= attachment.retention_expires_at <= expected_max


def test_service_leaves_retention_null_when_default_not_set() -> None:
    service = AttachmentService(InMemoryAttachmentStore())
    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="web_widget",
        filename="notes.txt",
        content_type="text/plain",
        content_bytes=b"hello",
    )
    assert attachment.retention_expires_at is None


def test_service_respects_explicit_retention_over_default() -> None:
    service = AttachmentService(
        InMemoryAttachmentStore(),
        default_retention_days=30,
    )
    custom = datetime.now(timezone.utc) + timedelta(days=7)
    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="web_widget",
        filename="notes.txt",
        content_type="text/plain",
        content_bytes=b"hello",
        retention_expires_at=custom,
    )
    # The explicit value wins over the 30-day default.
    assert attachment.retention_expires_at == custom


def test_soft_deleted_attachments_are_filtered_from_reads() -> None:
    store = InMemoryAttachmentStore()
    service = AttachmentService(store)
    attachment = service.upload_attachment(
        conversation_id="conv_1",
        organization_id="org_1",
        channel="web_widget",
        filename="notes.txt",
        content_type="text/plain",
        content_bytes=b"hello",
    )
    service.process_attachment(
        attachment_id=attachment.attachment_id,
        organization_id="org_1",
    )

    # Pre-delete: all reads work.
    assert service.get_projection(
        attachment_id=attachment.attachment_id, organization_id="org_1"
    ) is not None
    listed = service.list_conversation_attachments(
        conversation_id="conv_1", organization_id="org_1"
    )
    assert len(listed) == 1
    assert service.get_attachment_bytes(
        attachment_id=attachment.attachment_id, organization_id="org_1"
    ) is not None
    assert service.materialize_ref(
        attachment_id=attachment.attachment_id, organization_id="org_1"
    ) is not None

    # Mark soft-deleted via store.
    deleted_copy = attachment.model_copy(
        update={"deleted_at": datetime.now(timezone.utc)}
    )
    store.save_attachment(deleted_copy)

    # Post-delete: reads skip the row.
    assert service.get_projection(
        attachment_id=attachment.attachment_id, organization_id="org_1"
    ) is None
    listed = service.list_conversation_attachments(
        conversation_id="conv_1", organization_id="org_1"
    )
    assert listed == []
    assert service.get_attachment_bytes(
        attachment_id=attachment.attachment_id, organization_id="org_1"
    ) is None
    assert service.materialize_ref(
        attachment_id=attachment.attachment_id, organization_id="org_1"
    ) is None


# ══════════════════════════════════════════════════════════════════════════════
# Retention worker (real DB)
# ══════════════════════════════════════════════════════════════════════════════


def _setup_schema(database_url: str) -> None:
    from sqlalchemy import create_engine

    engine = create_engine(database_url, future=True)
    # Attachment tables + their deps
    AttachmentRecord.__table__.create(engine, checkfirst=True)
    AttachmentBlobRecord.__table__.create(engine, checkfirst=True)
    AttachmentViewRecord.__table__.create(engine, checkfirst=True)
    engine.dispose()


def _insert_attachment(
    store: SQLAlchemyAttachmentStore,
    *,
    attachment_id: str,
    retention_expires_at: datetime | None = None,
    deleted_at: datetime | None = None,
) -> None:
    from ruhu.attachments.models import AttachmentUpload

    now = datetime.now(timezone.utc)
    store.save_attachment(
        AttachmentUpload(
            attachment_id=attachment_id,
            organization_id="org_retention",
            conversation_id="conv_retention",
            channel="web_widget",
            source="public_widget",
            filename="x.txt",
            content_type="text/plain",
            size_bytes=5,
            sha256="a" * 64,
            kind="text",
            scan_status="passed",
            trust_tier="anonymous",
            retention_expires_at=retention_expires_at,
            deleted_at=deleted_at,
            created_at=now - timedelta(days=60),
            updated_at=now - timedelta(days=60),
        )
    )
    store.save_blob(attachment_id, b"hello")


def test_retention_worker_soft_deletes_expired_attachments(postgres_database_url_factory) -> None:
    db_url = postgres_database_url_factory()
    _setup_schema(db_url)
    sf = build_session_factory(db_url)
    store = SQLAlchemyAttachmentStore(sf)
    now = datetime.now(timezone.utc)

    # Three rows:
    #   A) expired retention, not yet soft-deleted → should be soft-deleted
    #   B) future retention, not soft-deleted → should stay alone
    #   C) no retention set → should stay alone (indefinite retention)
    _insert_attachment(store, attachment_id="A", retention_expires_at=now - timedelta(days=1))
    _insert_attachment(store, attachment_id="B", retention_expires_at=now + timedelta(days=30))
    _insert_attachment(store, attachment_id="C", retention_expires_at=None)

    worker = AttachmentRetention(
        session_factory=sf,
        batch_size=100,
        hard_delete_grace_seconds=30 * 24 * 3600,
    )
    summary = worker.process_once()

    assert summary.soft_deleted_count == 1
    assert summary.hard_deleted_count == 0
    assert summary.error is None

    # Verify state
    a = store.get_attachment("A")
    b = store.get_attachment("B")
    c = store.get_attachment("C")
    assert a is not None and a.deleted_at is not None
    assert b is not None and b.deleted_at is None
    assert c is not None and c.deleted_at is None


def test_retention_worker_hard_deletes_past_grace(postgres_database_url_factory) -> None:
    db_url = postgres_database_url_factory()
    _setup_schema(db_url)
    sf = build_session_factory(db_url)
    store = SQLAlchemyAttachmentStore(sf)
    now = datetime.now(timezone.utc)

    # Rows:
    #   A) soft-deleted 31 days ago (past 30-day grace) → hard-deleted
    #   B) soft-deleted 5 days ago (within grace) → kept
    #   C) never soft-deleted → kept
    _insert_attachment(
        store,
        attachment_id="A",
        retention_expires_at=now - timedelta(days=32),
        deleted_at=now - timedelta(days=31),
    )
    _insert_attachment(
        store,
        attachment_id="B",
        retention_expires_at=now - timedelta(days=6),
        deleted_at=now - timedelta(days=5),
    )
    _insert_attachment(store, attachment_id="C", retention_expires_at=None)

    worker = AttachmentRetention(
        session_factory=sf,
        batch_size=100,
        hard_delete_grace_seconds=30 * 24 * 3600,
    )
    summary = worker.process_once()

    assert summary.hard_deleted_count == 1
    assert summary.error is None

    # A is gone; its blob is gone too (ON DELETE CASCADE).
    assert store.get_attachment("A") is None
    assert store.get_blob("A") is None
    # B and C remain
    assert store.get_attachment("B") is not None
    assert store.get_attachment("C") is not None


def test_retention_worker_is_idempotent(postgres_database_url_factory) -> None:
    db_url = postgres_database_url_factory()
    _setup_schema(db_url)
    sf = build_session_factory(db_url)
    store = SQLAlchemyAttachmentStore(sf)
    now = datetime.now(timezone.utc)

    _insert_attachment(store, attachment_id="A", retention_expires_at=now - timedelta(days=1))

    worker = AttachmentRetention(session_factory=sf)
    first = worker.process_once()
    second = worker.process_once()

    assert first.soft_deleted_count == 1
    # Second pass: already soft-deleted, nothing new.
    assert second.soft_deleted_count == 0
    assert second.hard_deleted_count == 0


def test_retention_worker_respects_batch_size(postgres_database_url_factory) -> None:
    db_url = postgres_database_url_factory()
    _setup_schema(db_url)
    sf = build_session_factory(db_url)
    store = SQLAlchemyAttachmentStore(sf)
    now = datetime.now(timezone.utc)

    # 5 expired attachments; batch_size=2 should only soft-delete 2 per pass.
    for i in range(5):
        _insert_attachment(
            store,
            attachment_id=f"att_{i}",
            retention_expires_at=now - timedelta(days=1),
        )

    worker = AttachmentRetention(session_factory=sf, batch_size=2)
    s1 = worker.process_once()
    s2 = worker.process_once()
    s3 = worker.process_once()

    assert s1.soft_deleted_count == 2
    assert s2.soft_deleted_count == 2
    assert s3.soft_deleted_count == 1  # the leftover


# ── jobs-runtime integration (RP-2.2) ─────────────────────────────────────────

def test_retention_runs_as_recurring_tick_on_job_runtime(postgres_database_url_factory) -> None:
    """End-to-end: schedule -> tick job -> retention handler -> soft delete."""
    from datetime import timedelta

    from ruhu.jobs import JobRuntime, SQLAlchemyJobStore
    from ruhu.runtime_config import RuntimeSettings
    from ruhu.worker import build_handler_registry

    sf = build_session_factory(postgres_database_url_factory())
    store = SQLAlchemyAttachmentStore(sf)
    now = datetime.now(timezone.utc)
    _insert_attachment(store, attachment_id="EXPIRED", retention_expires_at=now - timedelta(days=1))

    settings = RuntimeSettings(
        attachments_retention_sweep_enabled=True,
        attachments_retention_sweep_interval_seconds=600.0,
        attachments_retention_sweep_batch_size=10,
        attachments_retention_hard_delete_grace_seconds=30 * 24 * 3600.0,
    )
    registry, schedules = build_handler_registry(session_factory=sf, settings=settings)
    assert [s.job_type for s in schedules] == ["attachment_retention.tick"]

    runtime = JobRuntime(
        SQLAlchemyJobStore(sf), registry, worker_id="w-test", schedules=schedules
    )
    assert runtime.run_once() == 1

    refreshed = store.get_attachment("EXPIRED")
    assert refreshed is not None
    assert refreshed.deleted_at is not None
