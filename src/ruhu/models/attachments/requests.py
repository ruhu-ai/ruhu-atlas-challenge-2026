"""Attachments API request schemas. Define what clients send to the API."""

from typing import Optional
from pydantic import BaseModel, Field, field_validator


class UploadAttachmentRequest(BaseModel):
    """Request to upload an attachment to a conversation."""

    conversation_id: str = Field(
        description="Conversation ID.",
    )
    filename: str = Field(
        min_length=1,
        max_length=255,
        description="Original filename.",
    )
    mime_type: str = Field(
        max_length=100,
        description="MIME type (e.g., 'application/pdf').",
    )
    file_size_bytes: int = Field(
        ge=1,
        le=100 * 1024 * 1024,  # 100 MB max
        description="File size in bytes.",
    )
    attachment_type: Optional[str] = Field(
        default=None,
        pattern="^(document|image|audio|video|artifact|other)$",
        description="Classification hint (auto-detected if not provided).",
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
            raise ValueError("mime_type must be in format 'type/subtype'")
        return v


class UpdateAttachmentRequest(BaseModel):
    """Update attachment metadata (patch semantics)."""

    extracted_text: Optional[str] = Field(
        default=None,
        max_length=100000,
        description="Extracted text from processing.",
    )
    extracted_metadata: Optional[dict] = Field(
        default=None,
        description="Extracted metadata.",
    )
    processing_status: Optional[str] = Field(
        default=None,
        pattern="^(pending|processing|completed|failed|skipped)$",
        description="New processing status.",
    )
    processing_error: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Error message if processing failed.",
    )
