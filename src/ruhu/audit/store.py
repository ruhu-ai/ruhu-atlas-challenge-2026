"""Audit event persistence — Postgres store with async session support.

Two write modes:
  - ``save()`` — single event, used for sync security/admin writes.
  - ``save_batch()`` — multiple events, used by the async flusher.

Query methods support the investigation-oriented API: by resource, by actor,
by event type, with time range and pagination.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import DateTime, Index, Integer, SmallInteger, String, Text, text
from sqlalchemy import JSON as SA_JSON
from sqlalchemy import select, func
from sqlalchemy.orm import Mapped, mapped_column, Session

from ruhu.db_models import Base

from .events import AuditEvent


# ── SQLAlchemy model ─────────────────────────────────────────────────────────

class AuditEventRecord(Base):
    __tablename__ = "audit_events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    actor_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[dict] = mapped_column(SA_JSON, nullable=False, server_default=text("'{}'"))
    outcome: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'success'"))
    http_method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    http_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    http_status: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[str] = mapped_column(String(30), nullable=False)

    __table_args__ = (
        Index("ix_audit_org_created", "organization_id", "created_at"),
        Index("ix_audit_org_resource", "organization_id", "resource_type", "resource_id", "created_at"),
        Index("ix_audit_org_actor", "organization_id", "actor_id", "created_at"),
        Index("ix_audit_org_event_type", "organization_id", "event_type", "created_at"),
        Index("ix_audit_request_id", "request_id"),
        Index("ix_audit_trace_id", "trace_id"),
    )


# ── Store protocol ───────────────────────────────────────────────────────────

class AuditStore(Protocol):
    def save(self, event: AuditEvent) -> None: ...
    def save_batch(self, events: list[AuditEvent]) -> None: ...
    def get(self, event_id: str, *, organization_id: str) -> AuditEvent | None: ...
    def list_events(
        self,
        *,
        organization_id: str,
        event_type: str | None = None,
        operation: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        actor_id: str | None = None,
        outcome: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditEvent]: ...
    def get_latest_hash(self, organization_id: str) -> str | None: ...
    def count_events(
        self,
        *,
        organization_id: str,
        event_type: str | None = None,
        outcome: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> int: ...


# ── Record ↔ Event conversion ────────────────────────────────────────────────

def _event_to_record(event: AuditEvent) -> AuditEventRecord:
    return AuditEventRecord(
        event_id=event.event_id,
        organization_id=event.organization_id,
        actor_id=event.actor_id,
        actor_ip=event.actor_ip,
        actor_session_id=event.actor_session_id,
        event_type=event.event_type,
        operation=event.operation,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        detail=event.detail,
        outcome=event.outcome,
        http_method=event.http_method,
        http_path=event.http_path,
        http_status=event.http_status,
        duration_ms=event.duration_ms,
        request_id=event.request_id,
        trace_id=event.trace_id,
        content_hash=event.content_hash,
        prev_hash=event.prev_hash,
        created_at=event.created_at,
    )


def _record_to_event(record: AuditEventRecord) -> AuditEvent:
    event = AuditEvent(
        event_type=record.event_type,
        organization_id=record.organization_id,
        outcome=record.outcome,
        actor_id=record.actor_id,
        actor_ip=record.actor_ip,
        actor_session_id=record.actor_session_id,
        resource_type=record.resource_type,
        resource_id=record.resource_id,
        detail=record.detail or {},
        http_method=record.http_method,
        http_path=record.http_path,
        http_status=record.http_status,
        duration_ms=record.duration_ms,
        request_id=record.request_id,
        trace_id=record.trace_id,
        event_id=record.event_id,
        operation=record.operation,
        content_hash=record.content_hash,
        prev_hash=record.prev_hash,
        created_at=record.created_at,
    )
    return event


# ── In-memory store (tests / dev) ────────────────────────────────────────────

class InMemoryAuditStore:
    """Dict-backed store for tests. No persistence."""

    def __init__(self) -> None:
        self._events: dict[str, AuditEvent] = {}

    def save(self, event: AuditEvent) -> None:
        self._events[event.event_id] = event

    def save_batch(self, events: list[AuditEvent]) -> None:
        for event in events:
            self.save(event)

    def get(self, event_id: str, *, organization_id: str) -> AuditEvent | None:
        event = self._events.get(event_id)
        if event is not None and event.organization_id == organization_id:
            return event
        return None

    def list_events(
        self,
        *,
        organization_id: str,
        event_type: str | None = None,
        operation: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        actor_id: str | None = None,
        outcome: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditEvent]:
        results = [e for e in self._events.values() if e.organization_id == organization_id]
        if event_type:
            results = [e for e in results if e.event_type == event_type]
        if operation:
            results = [e for e in results if e.operation == operation]
        if resource_type:
            results = [e for e in results if e.resource_type == resource_type]
        if resource_id:
            results = [e for e in results if e.resource_id == resource_id]
        if actor_id:
            results = [e for e in results if e.actor_id == actor_id]
        if outcome:
            results = [e for e in results if e.outcome == outcome]
        if start_date:
            results = [e for e in results if e.created_at >= start_date]
        if end_date:
            results = [e for e in results if e.created_at <= end_date]
        results.sort(key=lambda e: e.created_at, reverse=True)
        return results[offset:offset + limit]

    def get_latest_hash(self, organization_id: str) -> str | None:
        org_events = [e for e in self._events.values() if e.organization_id == organization_id]
        if not org_events:
            return None
        org_events.sort(key=lambda e: e.created_at, reverse=True)
        return org_events[0].content_hash

    def count_events(
        self,
        *,
        organization_id: str,
        event_type: str | None = None,
        outcome: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> int:
        results = [e for e in self._events.values() if e.organization_id == organization_id]
        if event_type:
            results = [e for e in results if e.event_type == event_type]
        if outcome:
            results = [e for e in results if e.outcome == outcome]
        if start_date:
            results = [e for e in results if e.created_at >= start_date]
        if end_date:
            results = [e for e in results if e.created_at <= end_date]
        return len(results)


# ── SQLAlchemy store (production) ────────────────────────────────────────────

class SQLAlchemyAuditStore:
    """Sync SQLAlchemy store for audit events."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def save(self, event: AuditEvent) -> None:
        with self._session_factory() as session:
            session.add(_event_to_record(event))
            session.commit()

    def save_batch(self, events: list[AuditEvent]) -> None:
        if not events:
            return
        with self._session_factory() as session:
            for event in events:
                session.add(_event_to_record(event))
            session.commit()

    def get(self, event_id: str, *, organization_id: str) -> AuditEvent | None:
        with self._session_factory() as session:
            record = session.get(AuditEventRecord, event_id)
            if record is None or record.organization_id != organization_id:
                return None
            return _record_to_event(record)

    def list_events(
        self,
        *,
        organization_id: str,
        event_type: str | None = None,
        operation: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        actor_id: str | None = None,
        outcome: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditEvent]:
        with self._session_factory() as session:
            q = select(AuditEventRecord).where(AuditEventRecord.organization_id == organization_id)
            if event_type:
                q = q.where(AuditEventRecord.event_type == event_type)
            if operation:
                q = q.where(AuditEventRecord.operation == operation)
            if resource_type:
                q = q.where(AuditEventRecord.resource_type == resource_type)
            if resource_id:
                q = q.where(AuditEventRecord.resource_id == resource_id)
            if actor_id:
                q = q.where(AuditEventRecord.actor_id == actor_id)
            if outcome:
                q = q.where(AuditEventRecord.outcome == outcome)
            if start_date:
                q = q.where(AuditEventRecord.created_at >= start_date)
            if end_date:
                q = q.where(AuditEventRecord.created_at <= end_date)
            q = q.order_by(AuditEventRecord.created_at.desc()).limit(limit).offset(offset)
            return [_record_to_event(r) for r in session.scalars(q).all()]

    def get_latest_hash(self, organization_id: str) -> str | None:
        with self._session_factory() as session:
            q = (
                select(AuditEventRecord.content_hash)
                .where(AuditEventRecord.organization_id == organization_id)
                .order_by(AuditEventRecord.created_at.desc())
                .limit(1)
            )
            return session.scalar(q)

    def count_events(
        self,
        *,
        organization_id: str,
        event_type: str | None = None,
        outcome: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> int:
        with self._session_factory() as session:
            q = select(func.count()).select_from(AuditEventRecord).where(
                AuditEventRecord.organization_id == organization_id
            )
            if event_type:
                q = q.where(AuditEventRecord.event_type == event_type)
            if outcome:
                q = q.where(AuditEventRecord.outcome == outcome)
            if start_date:
                q = q.where(AuditEventRecord.created_at >= start_date)
            if end_date:
                q = q.where(AuditEventRecord.created_at <= end_date)
            return session.scalar(q) or 0
