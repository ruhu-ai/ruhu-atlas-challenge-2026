from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

ToolKind = Literal["builtin", "http", "mcp", "code", "composite"]
ToolFailureKind = Literal[
    "validation_error",
    "authorization_denied",
    "confirmation_required",
    "timeout",
    "transient_upstream_error",
    "permanent_upstream_error",
    # Axis 2 of the publish-gate gradient: the tool ref does not
    # resolve to a configured tool definition for this org.  The
    # kernel converts this into a tool_outcome event so author-
    # defined error transitions and the LLM-rendered fallback
    # message both fire instead of crashing the conversation.
    "tool_unavailable",
]
ToolOutputValidationMode = Literal["warn", "strict"]
ToolDecision = Literal["allow", "deny", "confirm"]
ToolInvocationStatus = Literal[
    "pending",
    "waiting_confirmation",
    "queued",
    "running",
    "waiting_poll",
    "waiting_webhook",
    "retry_scheduled",
    "completed",
    "failed",
    "blocked",
    "cancelled",
    "timed_out",
    "dead_lettered",
]
ToolResultStatus = Literal[
    "success",
    "confirmation_required",
    "blocked",
    "timeout",
    "error",
    "cancelled",
]
ToolChannel = Literal["phone", "whatsapp", "web_chat", "web_widget", "browser"]
ToolIntegrationResolutionMode = Literal["manual", "polling", "webhook"]
ToolIntegrationJobStatus = Literal[
    "queued",
    "running",
    "waiting_poll",
    "waiting_webhook",
    "retry_scheduled",
    "completed",
    "failed",
    "cancelled",
    "dead_lettered",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ToolCaller(BaseModel):
    channel: ToolChannel
    conversation_id: str | None = None
    step_id: str | None = None
    agent_id: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None


class ToolCall(BaseModel):
    invocation_id: str = Field(default_factory=lambda: str(uuid4()))
    tool_ref: str
    args: dict[str, Any] = Field(default_factory=dict)
    caller: ToolCaller
    dedupe_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    requested_at: datetime = Field(default_factory=_utcnow)


class ToolAuthorizationResult(BaseModel):
    decision: ToolDecision
    reason: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    invocation_id: str
    tool_ref: str
    status: ToolResultStatus
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    latency_ms: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolInvocation(BaseModel):
    invocation_id: str
    tool_ref: str
    executor_kind: ToolKind
    status: ToolInvocationStatus
    caller: ToolCaller
    args: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str | None = None
    decision: ToolDecision | None = None
    decision_reason: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    latency_ms: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime | None = None


class ToolIntegrationJob(BaseModel):
    job_id: str = Field(default_factory=lambda: str(uuid4()))
    organization_id: str | None = None
    invocation_id: str
    tool_ref: str
    executor_kind: ToolKind
    resolution_mode: ToolIntegrationResolutionMode = "manual"
    status: ToolIntegrationJobStatus = "queued"
    queue_name: str = "default"
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    attempt_count: int = 0
    max_attempts: int = Field(default=1, ge=1)
    dedupe_key: str | None = None
    external_job_id: str | None = None
    callback_correlation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    submitted_at: datetime = Field(default_factory=_utcnow)
    started_at: datetime | None = None
    last_progress_at: datetime | None = None
    next_poll_at: datetime | None = None
    next_retry_at: datetime | None = None
    finished_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
