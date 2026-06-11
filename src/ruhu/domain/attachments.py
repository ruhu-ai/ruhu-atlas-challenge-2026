"""Attachments domain models. Business logic independent of persistence.

Attachments represent files, images, artifacts, or other media associated with
conversations. They support async processing, content extraction, and analysis.
"""

from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    """Current UTC time."""
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────
# Attachment-specific enums
# ─────────────────────────────────────────────────────────────────────

AttachmentType = Literal["document", "image", "audio", "video", "artifact", "other"]
ProcessingStatus = Literal["pending", "processing", "completed", "failed", "skipped"]
ContentType = Literal["text", "binary", "structured"]


# ─────────────────────────────────────────────────────────────────────
# Attachment Definition
# ─────────────────────────────────────────────────────────────────────


class Attachment(BaseModel):
    """A file or artifact associated with a conversation.

    Attachments support async processing: upload → pending → processing → completed.
    Content can be extracted and analyzed (OCR, transcription, entity extraction).
    """

    attachment_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique attachment identifier.",
    )
    organization_id: str = Field(
        description="Organization that owns this attachment.",
    )
    conversation_id: str = Field(
        description="Conversation this attachment belongs to.",
    )
    filename: str = Field(
        min_length=1,
        max_length=255,
        description="Original filename.",
    )
    file_size_bytes: int = Field(
        ge=0,
        le=100 * 1024 * 1024,  # 100 MB max
        description="File size in bytes.",
    )
    mime_type: str = Field(
        max_length=100,
        description="MIME type (e.g., 'application/pdf').",
    )
    attachment_type: AttachmentType = Field(
        description="Classification: document, image, audio, video, artifact, other.",
    )
    storage_key: str = Field(
        description="S3/blob storage key for retrieval.",
    )
    processing_status: ProcessingStatus = Field(
        default="pending",
        description="pending, processing, completed, failed, skipped.",
    )
    content_type: ContentType = Field(
        default="binary",
        description="text, binary, or structured (JSON).",
    )
    extracted_text: Optional[str] = Field(
        default=None,
        max_length=100000,
        description="Extracted text (OCR, transcription, etc.).",
    )
    extracted_metadata: dict = Field(
        default_factory=dict,
        description="Extracted metadata (dimensions, duration, entities, etc.).",
    )
    processing_error: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Error message if processing failed.",
    )
    uploaded_by: Optional[str] = Field(
        default=None,
        description="User ID who uploaded.",
    )
    uploaded_at: datetime = Field(
        default_factory=utcnow,
        description="When uploaded.",
    )
    processing_started_at: Optional[datetime] = Field(
        default=None,
        description="When async processing started.",
    )
    processing_completed_at: Optional[datetime] = Field(
        default=None,
        description="When async processing completed.",
    )
    created_at: datetime = Field(
        default_factory=utcnow,
        description="When created.",
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        description="When last updated.",
    )

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, v: str) -> str:
        """Filename must not contain path separators."""
        if "/" in v or "\\" in v:
            raise ValueError("filename must not contain path separators")
        return v

    @field_validator("mime_type")
    @classmethod
    def validate_mime_type(cls, v: str) -> str:
        """MIME type should follow standard format."""
        if "/" not in v:
            raise ValueError("mime_type must be in format 'type/subtype' (e.g., 'image/png')")
        return v

    def is_processing_complete(self) -> bool:
        """Check if async processing is done."""
        return self.processing_status in ("completed", "failed", "skipped")

    def is_text_available(self) -> bool:
        """Check if extracted text is available."""
        return self.extracted_text is not None and len(self.extracted_text) > 0

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "attachment_id": "att_invoice_2024",
                    "organization_id": "org_acme",
                    "conversation_id": "conv_789",
                    "filename": "invoice_jan_2024.pdf",
                    "file_size_bytes": 524288,
                    "mime_type": "application/pdf",
                    "attachment_type": "document",
                    "storage_key": "s3://bucket/org_acme/conv_789/invoice_jan_2024.pdf",
                    "processing_status": "completed",
                    "content_type": "text",
                    "extracted_text": "Invoice #INV-2024-001...",
                    "uploaded_by": "user_123",
                }
            ]
        }
    )


# ─────────────────────────────────────────────────────────────────────
# Domain Events
# ─────────────────────────────────────────────────────────────────────


class AttachmentUploaded(BaseModel):
    """Event: attachment was uploaded."""

    attachment_id: str
    organization_id: str
    conversation_id: str
    filename: str
    mime_type: str
    file_size_bytes: int
    timestamp: datetime = Field(default_factory=utcnow)


class AttachmentProcessingStarted(BaseModel):
    """Event: async processing started."""

    attachment_id: str
    organization_id: str
    processing_type: str  # "ocr", "transcription", "extraction", etc.
    timestamp: datetime = Field(default_factory=utcnow)


class AttachmentProcessingCompleted(BaseModel):
    """Event: async processing completed."""

    attachment_id: str
    organization_id: str
    processing_status: ProcessingStatus
    extracted_text: Optional[str] = None
    extracted_metadata: dict = Field(default_factory=dict)
    processing_error: Optional[str] = None
    timestamp: datetime = Field(default_factory=utcnow)
