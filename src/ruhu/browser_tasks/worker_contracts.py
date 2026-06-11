from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import utc_now
from .task_packs import (
    BrowserArtifactKind,
    BrowserCredentialKind,
    BrowserTaskPackBrowserPlan,
    BrowserTaskPack,
    is_url_allowed,
    normalize_allowed_domains,
)


BrowserWorkerPhase = Literal[
    "queued",
    "starting",
    "navigating",
    "authenticating",
    "acting",
    "awaiting_approval",
    "capturing_artifact",
    "completed",
    "failed",
    "cancelled",
]
BrowserWorkerErrorKind = Literal[
    "validation",
    "navigation",
    "authentication",
    "approval_required",
    "policy_violation",
    "network",
    "timeout",
    "rate_limited",
    "worker_unavailable",
    "unknown",
]


class BrowserCredentialRef(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str
    kind: BrowserCredentialKind
    secret_ref: str


class BrowserAttachmentRef(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    attachment_id: str
    filename: str | None = None
    mime_type: str | None = None


class BrowserResolvedUpload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    attachment_id: str
    filename: str
    content_type: str
    content_bytes: bytes


class BrowserWorkerPolicy(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    allowed_domains: list[str]
    max_execution_seconds: int = Field(ge=5, le=1800)
    max_steps: int = Field(ge=1, le=500)
    allow_downloads: bool = False
    allow_uploads: bool = False
    capture_screenshots: bool = True
    screenshot_redaction_selectors: list[str] = Field(default_factory=list)

    @field_validator("allowed_domains")
    @classmethod
    def validate_allowed_domains(cls, value: list[str]) -> list[str]:
        return normalize_allowed_domains(value)


class BrowserWorkerRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    request_id: str
    task_id: str
    organization_id: str | None = None
    agent_id: str | None = None
    conversation_id: str
    pack_id: str
    pack_version: str
    title: str
    start_url: str
    input: dict[str, Any] = Field(default_factory=dict)
    policy: BrowserWorkerPolicy
    browser_plan: BrowserTaskPackBrowserPlan | None = None
    credentials: list[BrowserCredentialRef] = Field(default_factory=list)
    attachments: list[BrowserAttachmentRef] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("start_url")
    @classmethod
    def require_start_url(cls, value: str) -> str:
        if not value:
            raise ValueError("start_url is required")
        return value

    @model_validator(mode="after")
    def assert_start_url_allowed(self) -> BrowserWorkerRequest:
        if not is_url_allowed(self.start_url, self.policy.allowed_domains):
            raise ValueError("start_url must match an allowed domain")
        return self

    @classmethod
    def from_task_pack(
        cls,
        *,
        request_id: str,
        task_id: str,
        conversation_id: str,
        pack: BrowserTaskPack,
        title: str,
        start_url: str | None = None,
        organization_id: str | None = None,
        agent_id: str | None = None,
        input: dict[str, Any] | None = None,
        credentials: list[BrowserCredentialRef] | None = None,
        attachments: list[BrowserAttachmentRef] | None = None,
    ) -> BrowserWorkerRequest:
        selected_start_url = start_url or pack.start_url
        if not selected_start_url:
            raise ValueError("start_url is required when the task pack has no default start_url")
        return cls(
            request_id=request_id,
            task_id=task_id,
            organization_id=organization_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            pack_id=pack.pack_id,
            pack_version=pack.version,
            title=title,
            start_url=selected_start_url,
            input=input or {},
            policy=BrowserWorkerPolicy(
                allowed_domains=pack.allowed_domains,
                max_execution_seconds=pack.execution_policy.max_execution_seconds,
                max_steps=pack.execution_policy.max_steps,
                allow_downloads=pack.execution_policy.allow_downloads,
                allow_uploads=pack.execution_policy.allow_uploads,
                capture_screenshots=pack.execution_policy.capture_screenshots,
                screenshot_redaction_selectors=pack.artifact_policy.screenshot_redaction_selectors,
            ),
            browser_plan=pack.browser_plan,
            credentials=credentials or [],
            attachments=attachments or [],
        )


class BrowserWorkerProgress(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    task_id: str
    event_sequence: int = Field(ge=1)
    phase: BrowserWorkerPhase
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class BrowserWorkerError(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    kind: BrowserWorkerErrorKind
    message: str
    retryable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class BrowserArtifactRef(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    artifact_id: str
    kind: BrowserArtifactKind
    uri: str | None = None
    label: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BrowserGeneratedArtifact(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, arbitrary_types_allowed=True)

    kind: BrowserArtifactKind
    filename: str
    content_type: str
    content_bytes: bytes
    label: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BrowserWorkerResult(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    task_id: str
    success: bool
    summary: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[BrowserArtifactRef] = Field(default_factory=list)
    generated_artifacts: list[BrowserGeneratedArtifact] = Field(default_factory=list)
    error: BrowserWorkerError | None = None
    completed_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def require_error_for_failure(self) -> BrowserWorkerResult:
        if not self.success and self.error is None:
            raise ValueError("failed worker results must include an error")
        return self
