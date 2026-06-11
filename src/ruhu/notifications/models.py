from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_notification_id() -> str:
    return f"notif_{uuid4().hex}"


# ---------------------------------------------------------------------------
# Domain model (immutable)
# ---------------------------------------------------------------------------

class NotificationRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    notification_id: str
    organization_id: str
    user_id: str | None
    category: str
    level: str
    urgency: str
    title: str
    message: str | None
    url: str | None
    url_label: str | None
    source_type: str | None
    source_id: str | None
    payload: dict[str, object]
    read_at: datetime | None
    dismissed_at: datetime | None
    expires_at: datetime | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Emission input
# ---------------------------------------------------------------------------

class NotificationCreate(BaseModel):
    organization_id: str
    user_id: str | None = None
    category: str
    level: str = "info"
    urgency: str = "fyi"
    title: str
    message: str | None = None
    url: str | None = None
    url_label: str | None = None
    source_type: str | None = None
    source_id: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)
    expires_after_hours: int | None = None


# ---------------------------------------------------------------------------
# API response (id mapped from notification_id for frontend compatibility)
# ---------------------------------------------------------------------------

class NotificationResponse(BaseModel):
    id: str
    organization_id: str
    user_id: str | None
    title: str
    message: str | None
    level: str
    url: str | None
    url_label: str | None
    payload: dict[str, object]
    read_at: str | None
    created_at: str
    # Extended fields
    category: str
    urgency: str
    source_type: str | None
    source_id: str | None
    dismissed_at: str | None
    expires_at: str | None

    @classmethod
    def from_record(cls, record: NotificationRecord) -> "NotificationResponse":
        def _fmt(dt: datetime | None) -> str | None:
            if dt is None:
                return None
            return dt.isoformat()

        return cls(
            id=record.notification_id,
            organization_id=record.organization_id,
            user_id=record.user_id,
            title=record.title,
            message=record.message,
            level=record.level,
            url=record.url,
            url_label=record.url_label,
            payload=record.payload,
            read_at=_fmt(record.read_at),
            created_at=_fmt(record.created_at) or "",
            category=record.category,
            urgency=record.urgency,
            source_type=record.source_type,
            source_id=record.source_id,
            dismissed_at=_fmt(record.dismissed_at),
            expires_at=_fmt(record.expires_at),
        )


class UnreadCountResponse(BaseModel):
    unread_count: int


class MarkReadRequest(BaseModel):
    notification_id: str


class MarkedResponse(BaseModel):
    marked: int


class DismissResponse(BaseModel):
    dismissed: bool
