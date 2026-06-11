from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import PhoneNumberAuditRecord


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_optional_string(value: object | None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate or None


def _normalize_payload(value: object | None) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("payload must be an object")
    return {str(key): item for key, item in value.items()}


class PhoneNumberAuditEvent(BaseModel):
    audit_event_id: str
    organization_id: str
    phone_number_id: str | None = None
    actor_type: str
    actor_user_id: str | None = None
    action: str
    resource_type: str
    resource_id: str | None = None
    summary: str
    payload: dict[str, object] = Field(default_factory=dict)
    ip_address: str | None = None
    user_agent: str | None = None
    created_at: datetime


class PhoneNumberAuditService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def record_event(
        self,
        *,
        organization_id: str,
        action: str,
        resource_type: str,
        summary: str,
        phone_number_id: str | None = None,
        resource_id: str | None = None,
        actor_type: str = "user",
        actor_user_id: str | None = None,
        payload: dict[str, object] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> PhoneNumberAuditEvent:
        normalized_action = _normalize_optional_string(action)
        normalized_resource_type = _normalize_optional_string(resource_type)
        normalized_summary = _normalize_optional_string(summary)
        if normalized_action is None:
            raise ValueError("action is required")
        if normalized_resource_type is None:
            raise ValueError("resource_type is required")
        if normalized_summary is None:
            raise ValueError("summary is required")
        now = _utcnow()
        record = PhoneNumberAuditRecord(
            audit_event_id=f"pna_{uuid4().hex}",
            organization_id=organization_id,
            phone_number_id=_normalize_optional_string(phone_number_id),
            actor_type=_normalize_optional_string(actor_type) or "user",
            actor_user_id=_normalize_optional_string(actor_user_id),
            action=normalized_action,
            resource_type=normalized_resource_type,
            resource_id=_normalize_optional_string(resource_id),
            summary=normalized_summary,
            payload_json=_normalize_payload(payload),
            ip_address=_normalize_optional_string(ip_address),
            user_agent=_normalize_optional_string(user_agent),
            created_at=now,
        )
        with self._session_factory.begin() as session:
            session.add(record)
        return _event_from_record(record)

    def list_events(
        self,
        *,
        organization_id: str,
        phone_number_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        limit: int = 50,
    ) -> list[PhoneNumberAuditEvent]:
        requested_limit = max(1, min(int(limit), 200))
        with self._session_factory() as session:
            statement = (
                select(PhoneNumberAuditRecord)
                .where(PhoneNumberAuditRecord.organization_id == organization_id)
                .order_by(PhoneNumberAuditRecord.created_at.desc(), PhoneNumberAuditRecord.audit_event_id.desc())
                .limit(requested_limit)
            )
            normalized_phone_number_id = _normalize_optional_string(phone_number_id)
            if normalized_phone_number_id is not None:
                statement = statement.where(PhoneNumberAuditRecord.phone_number_id == normalized_phone_number_id)
            normalized_resource_type = _normalize_optional_string(resource_type)
            if normalized_resource_type is not None:
                statement = statement.where(PhoneNumberAuditRecord.resource_type == normalized_resource_type)
            normalized_resource_id = _normalize_optional_string(resource_id)
            if normalized_resource_id is not None:
                statement = statement.where(PhoneNumberAuditRecord.resource_id == normalized_resource_id)
            records = session.scalars(statement).all()
        return [_event_from_record(record) for record in records]


def _event_from_record(record: PhoneNumberAuditRecord) -> PhoneNumberAuditEvent:
    return PhoneNumberAuditEvent(
        audit_event_id=record.audit_event_id,
        organization_id=record.organization_id,
        phone_number_id=record.phone_number_id,
        actor_type=record.actor_type,
        actor_user_id=record.actor_user_id,
        action=record.action,
        resource_type=record.resource_type,
        resource_id=record.resource_id,
        summary=record.summary,
        payload=dict(record.payload_json or {}),
        ip_address=record.ip_address,
        user_agent=record.user_agent,
        created_at=record.created_at,
    )
