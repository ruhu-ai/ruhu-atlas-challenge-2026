"""Attachments API response schemas. Define what the server returns to clients."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, computed_field


class AttachmentResponse(BaseModel):
    """An attachment as returned by the API."""

    attachment_id: str = Field(description="Unique attachment ID.")
    organization_id: str = Field(description="Organization ID.")
    conversation_id: str = Field(description="Conversation ID.")
    filename: str = Field(description="Original filename.")
    file_size_bytes: int = Field(description="File size in bytes.")
    mime_type: str = Field(description="MIME type.")
    attachment_type: str = Field(description="document, image, audio, video, artifact, other.")
    processing_status: str = Field(description="pending, processing, completed, failed, skipped.")
    content_type: str = Field(description="text, binary, or structured.")
    extracted_text: Optional[str] = Field(default=None, description="Extracted text.")
    extracted_metadata: dict = Field(default_factory=dict, description="Extracted metadata.")
    processing_error: Optional[str] = Field(default=None, description="Error message.")
    uploaded_by: Optional[str] = Field(default=None, description="User ID who uploaded.")
    uploaded_at: datetime = Field(description="Upload timestamp.")
    processing_started_at: Optional[datetime] = Field(default=None, description="Processing start.")
    processing_completed_at: Optional[datetime] = Field(default=None, description="Processing complete.")
    created_at: datetime = Field(description="Creation timestamp.")
    updated_at: datetime = Field(description="Last update timestamp.")

    # Computed fields
    download_url: Optional[str] = Field(
        default=None,
        description="Presigned download URL (if available).",
    )

    @computed_field
    @property
    def is_processing_complete(self) -> bool:
        """Is async processing done?"""
        return self.processing_status in ("completed", "failed", "skipped")

    @computed_field
    @property
    def is_text_available(self) -> bool:
        """Is extracted text available?"""
        return self.extracted_text is not None and len(self.extracted_text) > 0


class AttachmentListResponse(BaseModel):
    """Paginated list of attachments for a conversation."""

    attachments: list[AttachmentResponse] = Field(description="List of attachments.")
    total: int = Field(description="Total count.")
    page: int = Field(description="Current page (1-indexed).")
    per_page: int = Field(description="Items per page.")
    has_more: bool = Field(default=False, description="More pages?")


class AttachmentUploadResponse(BaseModel):
    """Response to attachment upload (may return signing URL for client-side upload)."""

    attachment_id: str = Field(description="Newly created attachment ID.")
    storage_key: str = Field(description="Storage key in S3/blob storage.")
    upload_url: Optional[str] = Field(
        default=None,
        description="Presigned POST URL for client-side upload.",
    )
    expires_in_seconds: Optional[int] = Field(
        default=None,
        description="How long the upload URL is valid.",
    )
