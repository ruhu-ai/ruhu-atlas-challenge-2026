from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


AttachmentViewKind = Literal[
    "text",
    "vision",
    "transcript",
    "summary",
    "native_file_uri",
]
AttachmentViewStatus = Literal["pending", "processing", "ready", "failed", "skipped"]

AttachmentKind = Literal[
    "text",
    "markdown",
    "json",
    "yaml",
    "csv",
    "html",
    "xml",
    "docx",
    "pdf",
    "image",
    "audio",
    "binary",
]
AttachmentScanStatus = Literal["pending", "scanning", "passed", "failed", "skipped"]
AttachmentExtractionStatus = Literal["pending", "ready", "failed", "skipped"]
ArtifactKind = Literal["screenshot", "download", "transcript", "result_bundle", "log", "other"]
AttachmentChannel = Literal["phone", "whatsapp", "web_chat", "web_widget", "browser"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex}"


class AttachmentView(BaseModel):
    """One derived representation of an attachment (text, vision, transcript, etc.).

    Each attachment may have at most one view per kind (enforced by a unique
    constraint in the DB).  The service writes views as a side-effect of
    processing; the view-ready worker dispatches synthetic turns to the kernel
    when a view transitions to ``status='ready'``.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    view_id: str = Field(default_factory=lambda: new_id("avw_"))
    attachment_id: str
    conversation_id: str
    organization_id: str | None = None
    kind: AttachmentViewKind
    status: AttachmentViewStatus = "pending"
    content_text: str | None = None
    content_json: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)
    provider: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AttachmentUpload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    attachment_id: str = Field(default_factory=lambda: new_id("att_"))
    organization_id: str | None = None
    conversation_id: str
    channel: AttachmentChannel = "web_widget"
    source: str = "public_widget"
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    kind: AttachmentKind = "binary"
    scan_status: AttachmentScanStatus = "pending"
    extraction_status: AttachmentExtractionStatus = "pending"
    trust_tier: str = "anonymous"
    retention_expires_at: datetime | None = None
    deleted_at: datetime | None = None
    message: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    blob_uri: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AttachmentExtraction(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    extraction_id: str = Field(default_factory=lambda: new_id("atex_"))
    attachment_id: str
    organization_id: str | None = None
    conversation_id: str
    text_content: str | None = None
    summary: str | None = None
    structured_data: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Artifact(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    artifact_id: str = Field(default_factory=lambda: new_id("art_"))
    organization_id: str | None = None
    conversation_id: str
    source_attachment_id: str | None = None
    task_id: str | None = None
    kind: ArtifactKind = "other"
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class AttachmentRef(BaseModel):
    attachment_id: str
    kind: AttachmentKind
    source: str
    filename: str
    content_type: str
    scan_status: AttachmentScanStatus = "pending"
    extraction_status: AttachmentExtractionStatus = "pending"
    # View-model fields (populated by the view-ready worker when carrying an
    # inline view payload to the kernel).
    trust_tier: str = "anonymous"
    available_views: list[str] = Field(default_factory=list)
    inline_text: str | None = None
    size_bytes: int = 0
    extracted_text: str | None = None
    structured_data: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)
    policy: dict[str, object] = Field(default_factory=dict)


class AttachmentProjection(BaseModel):
    attachment: AttachmentUpload
    extraction: AttachmentExtraction | None = None


class ArtifactProjection(BaseModel):
    artifact: Artifact
    download_filename: str


class AttachmentRuntimeStatus(BaseModel):
    queued_jobs: int = 0
    running_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    last_error: str | None = None
