from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..schemas import Channel, Modality, RuntimeTurnResult


RealtimeSurface = Literal[
    "public_widget",
    "internal_chat",
    "voice",
    "operator",
    "browser_projection",
    "external_channel",
]
RealtimeSessionStatus = Literal["active", "disconnected", "ended", "errored"]
RealtimeEventVisibility = Literal["surface", "internal", "audit_only"]
RealtimeAudience = Literal[
    "public_widget",
    "internal_chat",
    "voice_summary",
    "operator",
    "audit",
    "external_channel",
]
RealtimeOutboxStatus = Literal["pending", "claimed", "delivered", "failed"]


class RealtimeSession(BaseModel):
    realtime_session_id: str
    conversation_id: str
    organization_id: str | None = None
    parent_realtime_session_id: str | None = None
    surface: RealtimeSurface
    channel: Channel
    modality: Modality
    status: RealtimeSessionStatus = "active"
    provider: str | None = None
    external_session_key: str | None = None
    provider_session_id: str | None = None
    participant_identity: str | None = None
    transport_metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime
    last_seen_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class RealtimeEvent(BaseModel):
    event_id: str
    conversation_id: str
    realtime_session_id: str | None = None
    organization_id: str | None = None
    family: str
    name: str
    conversation_sequence: int
    causation_id: str | None = None
    correlation_id: str | None = None
    actor_type: str | None = None
    actor_id: str | None = None
    visibility: RealtimeEventVisibility = "surface"
    audiences: list[RealtimeAudience] = Field(default_factory=list)
    projection_policy: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RealtimeIdempotencyKey(BaseModel):
    organization_id: str | None = None
    scope: str
    idempotency_key: str
    conversation_id: str | None = None
    result_event_id: str | None = None
    result_ref: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    expires_at: datetime | None = None


class RealtimeOutboxEntry(BaseModel):
    outbox_id: str
    organization_id: str | None = None
    conversation_id: str | None = None
    event_id: str
    topic: str
    dedupe_key: str | None = None
    status: RealtimeOutboxStatus = "pending"
    payload: dict[str, Any] = Field(default_factory=dict)
    available_at: datetime
    claimed_at: datetime | None = None
    delivered_at: datetime | None = None
    last_error: str | None = None
    attempt_count: int = 0
    created_at: datetime
    updated_at: datetime


class TranscriptCommitResult(BaseModel):
    duplicate: bool = False
    accepted_event: RealtimeEvent
    turn_result: RuntimeTurnResult | None = None
    idempotency: RealtimeIdempotencyKey
