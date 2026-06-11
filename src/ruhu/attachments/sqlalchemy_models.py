from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db_models import Base, OptionalTenantScopeMixin


class AttachmentRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "attachments"

    attachment_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Admission gate. Values: pending | scanning | passed | failed | skipped.
    scan_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Coarse extraction summary for list and detail projections. Per-view
    # readiness lives in AttachmentViewRecord.
    extraction_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True, server_default="pending")
    # Governance fields introduced by the first-principles rebuild.
    trust_tier: Mapped[str] = mapped_column(
        String(32), nullable=False, default="anonymous", index=True
    )
    uploaded_by_actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    retention_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # Storage URI for the attachment bytes when offloaded to a BlobStore
    # (S3 / GCS / local / in-memory). Format: ``<backend>://<bucket>/<key>``.
    # When NULL, the bytes live in ``AttachmentBlobRecord.content_bytes``
    # (the legacy DB-bytes path) — readers MUST tolerate both for the life
    # of any rows uploaded before the BlobStore was wired in.
    blob_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class AttachmentViewRecord(OptionalTenantScopeMixin, Base):
    """One derived representation of an attachment.

    Each attachment can have multiple views (text, vision, transcript,
    summary, native_file_uri, future: retrieval).  Each view has its own
    readiness state so the materializer can compute capability-oriented
    AttachmentRefs without a single coarse extraction_status.

    Uniqueness: at most one view per (attachment_id, kind).  Multi-provider
    views for the same kind are out of scope for V1.
    """

    __tablename__ = "attachment_views"
    __table_args__ = (
        UniqueConstraint(
            "attachment_id",
            "kind",
            name="uq_attachment_views_attachment_kind",
        ),
    )

    view_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    attachment_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("attachments.attachment_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Canonical V1 kinds: text | vision | transcript | summary |
    # native_file_uri.  Forward-compat: retrieval.
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # pending | processing | ready | failed | skipped
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_json: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    provider: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AttachmentViewDeliveryRecord(OptionalTenantScopeMixin, Base):
    """Dedup-gate for the view-ready worker.

    The unique constraint on (conversation_id, attachment_id, view_kind)
    guarantees that each view event is dispatched to the kernel exactly once,
    even when multiple worker instances compete.  The first INSERT wins; all
    others receive IntegrityError and exit early (see view_ready_worker.py §3).

    ``result`` values:
      dispatched         — kernel.process_turn() completed successfully
      failed             — kernel raised an exception (see error_detail)
      skipped_no_match   — current state has no matching view_ready transition
      skipped_stale      — conversation advanced since the candidate was found
      skipped_attachment_gone — attachment was soft-deleted before dispatch
      skipped_agent_version_missing — agent version could not be loaded
    """

    __tablename__ = "attachment_view_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "attachment_id",
            "view_kind",
            name="uq_attachment_view_deliveries_conv_att_kind",
        ),
    )

    delivery_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    attachment_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("attachments.attachment_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    view_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    result: Mapped[str] = mapped_column(String(64), nullable=False)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AttachmentBlobRecord(Base):
    __tablename__ = "attachment_blobs"

    attachment_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("attachments.attachment_id", ondelete="CASCADE"),
        primary_key=True,
    )
    content_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class AttachmentExtractionRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "attachment_extractions"

    extraction_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    attachment_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("attachments.attachment_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_data_json: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ArtifactRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "artifacts"

    artifact_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_attachment_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("attachments.attachment_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    task_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ArtifactBlobRecord(Base):
    __tablename__ = "artifact_blobs"

    artifact_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("artifacts.artifact_id", ondelete="CASCADE"),
        primary_key=True,
    )
    content_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
