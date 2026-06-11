from __future__ import annotations

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db_models import Base, OptionalTenantScopeMixin


class BrowserTaskRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "browser_tasks"

    task_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_channel: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    task_pack_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    task_pack_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    start_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_json: Mapped[dict] = mapped_column(JSON, default=dict)
    credential_refs_json: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    approval_state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    current_approval_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    lease_expires_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    operator_takeover_owner_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    operator_takeover_expires_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BrowserApprovalRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "browser_task_approvals"

    approval_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("browser_tasks.task_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    context_json: Mapped[dict] = mapped_column(JSON, default=dict)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    decided_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BrowserTaskEventRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "browser_task_events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("browser_tasks.task_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class BrowserOperatorCommandRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "browser_operator_commands"

    command_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("browser_tasks.task_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    operator_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    command_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    delivered_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class BrowserTaskPackAccessRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "browser_task_pack_access"

    access_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    pack_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
