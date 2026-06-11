from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


BrowserTaskState = Literal["queued", "awaiting_approval", "running", "completed", "failed", "cancelled"]
BrowserApprovalState = Literal["not_required", "pending", "approved", "denied", "expired", "cancelled"]
BrowserApprovalKind = Literal["generic_access", "change_confirmation"]
BrowserTaskChannel = Literal["phone", "whatsapp", "web_chat", "web_widget", "browser"]
BrowserOperatorCommandType = Literal[
    "click",
    "type_text",
    "press_key",
    "scroll",
    "navigate_back",
    "navigate_forward",
    "wait",
]
BrowserOperatorCommandState = Literal["queued", "delivered", "failed"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex}"


class BrowserTask(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    task_id: str = Field(default_factory=lambda: new_id("btask_"))
    organization_id: str | None = None
    agent_id: str | None = None
    conversation_id: str
    title: str
    summary: str | None = None
    requested_channel: BrowserTaskChannel = "browser"
    task_pack_id: str | None = None
    task_pack_version: str | None = None
    start_url: str | None = None
    input_payload: dict[str, object] = Field(default_factory=dict)
    credential_refs: dict[str, str] = Field(default_factory=dict)
    state: BrowserTaskState = "queued"
    approval_state: BrowserApprovalState = "not_required"
    current_approval_id: str | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    operator_takeover_owner_id: str | None = None
    operator_takeover_expires_at: datetime | None = None
    attempt_count: int = 0
    metadata: dict[str, object] = Field(default_factory=dict)
    result: dict[str, object] = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class BrowserApproval(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    approval_id: str = Field(default_factory=lambda: new_id("appr_"))
    task_id: str
    organization_id: str | None = None
    conversation_id: str
    kind: BrowserApprovalKind = "generic_access"
    state: BrowserApprovalState = "pending"
    prompt: str
    context: dict[str, object] = Field(default_factory=dict)
    decision_reason: str | None = None
    requested_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime | None = None
    decided_at: datetime | None = None


class BrowserTaskEvent(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    event_id: str = Field(default_factory=lambda: new_id("btev_"))
    task_id: str
    organization_id: str | None = None
    conversation_id: str
    event_sequence: int = 0
    event_type: str
    message: str
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class BrowserOperatorCommand(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    command_id: str = Field(default_factory=lambda: new_id("bopc_"))
    task_id: str
    organization_id: str | None = None
    conversation_id: str
    operator_id: str
    command_type: BrowserOperatorCommandType
    payload: dict[str, object] = Field(default_factory=dict)
    state: BrowserOperatorCommandState = "queued"
    created_at: datetime = Field(default_factory=utc_now)
    delivered_at: datetime | None = None
    error: str | None = None


class BrowserTaskSnapshot(BaseModel):
    task: BrowserTask
    approval: BrowserApproval | None = None
    recent_events: list[BrowserTaskEvent] = Field(default_factory=list)
