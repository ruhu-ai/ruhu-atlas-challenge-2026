"""Attachments Read Models (Projections)."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Index
from sqlmodel import SQLModel, Field, Column, JSON


def utcnow() -> datetime:
    """Current UTC time."""
    return datetime.now(timezone.utc)


class AttachmentProcessingProjection(SQLModel, table=True):
    """Read model: Attachment processing queue and status."""

    __tablename__ = "attachment_processing"
    __table_args__ = (
        Index("ix_attachment_processing_status", "organization_id", "processing_status"),
        Index("ix_attachment_processing_conversation", "conversation_id", "processing_status"),
    )

    attachment_id: str = Field(primary_key=True)
    organization_id: str = Field(index=True)
    conversation_id: str = Field(index=True)

    # Processing state
    processing_status: str  # "pending" | "processing" | "completed" | "failed" | "skipped"
    attachment_type: str
    file_size_bytes: int

    # Results
    extracted_text_length: int = 0  # Length of extracted text
    extraction_error: Optional[str] = None
    processing_duration_ms: Optional[int] = None  # How long it took

    # Custom metadata
    custom_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON))

    uploaded_at: datetime
    processing_completed_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utcnow)


class ConversationAttachmentSummaryProjection(SQLModel, table=True):
    """Read model: Summary of attachments per conversation."""

    __tablename__ = "conversation_attachment_summary"

    conversation_id: str = Field(primary_key=True)
    organization_id: str = Field(index=True)

    total_attachments: int = 0
    total_size_bytes: int = 0

    # Processing status breakdown
    pending_count: int = 0
    processing_count: int = 0
    completed_count: int = 0
    failed_count: int = 0

    # Content summary
    total_extracted_text_chars: int = 0

    custom_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON))

    updated_at: datetime = Field(default_factory=utcnow)
