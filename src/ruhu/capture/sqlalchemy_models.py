from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, Numeric, String, Text, JSON, Boolean, text
from sqlalchemy.orm import Mapped, mapped_column

from ruhu.db_models import Base, OptionalTenantScopeMixin


class CaptureAuditRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "capture_audit"
    __table_args__ = (
        Index("idx_capture_audit_conversation", "conversation_id", "turn_id"),
        Index("idx_capture_audit_org_fact", "organization_id", "fact_name", "created_at"),
        Index("idx_capture_audit_scope", "organization_id", "storage_scope", "created_at"),
        Index("idx_capture_audit_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(Text, nullable=False)
    turn_id: Mapped[str] = mapped_column(Text, nullable=False)
    step_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    fact_name: Mapped[str] = mapped_column(Text, nullable=False)
    storage_scope: Mapped[str] = mapped_column(Text, nullable=False, server_default="conversation")
    retention_policy: Mapped[str] = mapped_column(Text, nullable=False, server_default="conversation")
    sensitivity: Mapped[str] = mapped_column(Text, nullable=False, server_default="personal")
    audit_raw_policy: Mapped[str] = mapped_column(Text, nullable=False, server_default="hash")
    raw_value_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    replaced_previous: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CaptureAuditOutboxRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "capture_audit_outbox"
    __table_args__ = (
        Index("idx_capture_audit_outbox_status_next", "status", "next_attempt_at"),
        Index("idx_capture_audit_outbox_conversation", "conversation_id", "turn_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(Text, nullable=False)
    turn_id: Mapped[str] = mapped_column(Text, nullable=False)
    step_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
