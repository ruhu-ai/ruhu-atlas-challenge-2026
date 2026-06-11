from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


AtlasScope = Literal[
    "agent_authoring",
    "provisioning",
    "validation",
    "operations",
]

AtlasSessionStatus = Literal[
    "active",
    "completed",
    "blocked",
    "archived",
]

AtlasMessageRole = Literal["user", "assistant", "system", "tool"]

AtlasEventType = Literal[
    "start",
    "token",
    "tool_start",
    "tool_done",
    "permission_request",
    "progress",
    "complete",
    "error",
]

AtlasPermissionKind = Literal[
    "apply_deltas",
    "provision_resource",
    "execute_side_effecting_tool",
    "execute_code",
    "destructive_change",
]

AtlasPermissionStatus = Literal["pending", "approved", "denied", "expired"]


class AtlasAgentPolicy(BaseModel):
    agent_id: str
    organization_id: str | None = None
    atlas_enabled: bool = True
    updated_by_user_id: str | None = None
    created_at: datetime
    updated_at: datetime


class AtlasSession(BaseModel):
    session_id: str
    organization_id: str | None = None
    scope: AtlasScope
    status: AtlasSessionStatus
    agent_id: str
    agent_version_id: str | None = None
    title: str | None = None
    summary: str | None = None
    created_by: str | None = None
    scenario_id: str | None = None
    step_id: str | None = None
    conversation_id: str | None = None
    trace_id: str | None = None
    atlas_enabled_snapshot: bool = True
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AtlasMessage(BaseModel):
    message_id: str
    session_id: str
    organization_id: str | None = None
    sequence_number: int
    role: AtlasMessageRole
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AtlasEvent(BaseModel):
    event_id: str
    session_id: str
    organization_id: str | None = None
    sequence_number: int
    type: AtlasEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AtlasReviewDecisionRecord(BaseModel):
    review_decision_id: str
    session_id: str
    organization_id: str | None = None
    delta_id: str
    decision: Literal["approved", "rejected"]
    # Approval is content-addressed: it only authorizes the exact delta
    # payload that was reviewed, so a later delta reusing the same delta_id
    # with different content cannot inherit it.
    delta_payload_hash: str | None = None
    note: str | None = None
    decided_by_user_id: str | None = None
    created_at: datetime


class AtlasApplyRequestRecordModel(BaseModel):
    apply_request_id: str
    session_id: str
    organization_id: str | None = None
    status: Literal["pending", "rejected", "failed", "applied"]
    delta_ids: list[str] = Field(default_factory=list)
    apply_note: str | None = None
    confirmed_by_user_id: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class AtlasPermissionRequest(BaseModel):
    request_id: str
    session_id: str
    organization_id: str | None = None
    kind: AtlasPermissionKind
    status: AtlasPermissionStatus
    reason: str
    risk_summary: str | None = None
    scope_ref: dict[str, Any] = Field(default_factory=dict)
    delta_ids: list[str] = Field(default_factory=list)
    requested_actions: list[str] = Field(default_factory=list)
    decision_reason: str | None = None
    decided_by_user_id: str | None = None
    created_at: datetime
    expires_at: datetime | None = None
    decided_at: datetime | None = None
