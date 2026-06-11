from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import ProviderCostRecord as ProviderCostRecordModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProviderCostRecord(BaseModel):
    cost_record_id: str = Field(default_factory=lambda: str(uuid4()))
    organization_id: str | None = None
    conversation_id: str | None = None
    realtime_session_id: str | None = None
    turn_trace_id: str | None = None
    tool_invocation_id: str | None = None
    provider: str
    cost_type: str
    amount_usd: float
    reference_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=_utcnow)
    created_at: datetime = Field(default_factory=_utcnow)


class SQLAlchemyProviderCostStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save(self, record: ProviderCostRecord) -> ProviderCostRecord:
        with self._session_factory() as session:
            existing = session.get(ProviderCostRecordModel, record.cost_record_id)
            if existing is None:
                session.add(_to_model(record))
            else:
                existing.organization_id = record.organization_id
                existing.conversation_id = record.conversation_id
                existing.realtime_session_id = record.realtime_session_id
                existing.turn_trace_id = record.turn_trace_id
                existing.tool_invocation_id = record.tool_invocation_id
                existing.provider = record.provider
                existing.cost_type = record.cost_type
                existing.amount_usd = record.amount_usd
                existing.reference_key = record.reference_key
                existing.metadata_json = dict(record.metadata)
                existing.occurred_at = record.occurred_at
                existing.created_at = record.created_at
            session.commit()
        return record

    def save_all(self, records: Iterable[ProviderCostRecord]) -> list[ProviderCostRecord]:
        saved: list[ProviderCostRecord] = []
        for record in records:
            saved.append(self.save(record))
        return saved

    def by_conversation(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[ProviderCostRecord]:
        statement = (
            select(ProviderCostRecordModel)
            .where(ProviderCostRecordModel.conversation_id == conversation_id)
            .order_by(ProviderCostRecordModel.occurred_at.asc())
        )
        if organization_id is not None:
            statement = statement.where(ProviderCostRecordModel.organization_id == organization_id)
        with self._session_factory() as session:
            rows = session.execute(statement).scalars().all()
        return [_from_model(row) for row in rows]

    def by_session(
        self,
        realtime_session_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[ProviderCostRecord]:
        statement = (
            select(ProviderCostRecordModel)
            .where(ProviderCostRecordModel.realtime_session_id == realtime_session_id)
            .order_by(ProviderCostRecordModel.occurred_at.asc())
        )
        if organization_id is not None:
            statement = statement.where(ProviderCostRecordModel.organization_id == organization_id)
        with self._session_factory() as session:
            rows = session.execute(statement).scalars().all()
        return [_from_model(row) for row in rows]

    def list_records(
        self,
        *,
        organization_id: str | None = None,
        provider: str | None = None,
        conversation_id: str | None = None,
        limit: int = 100,
    ) -> list[ProviderCostRecord]:
        statement = select(ProviderCostRecordModel).order_by(ProviderCostRecordModel.occurred_at.desc()).limit(limit)
        if organization_id is not None:
            statement = statement.where(ProviderCostRecordModel.organization_id == organization_id)
        if provider is not None:
            statement = statement.where(ProviderCostRecordModel.provider == provider)
        if conversation_id is not None:
            statement = statement.where(ProviderCostRecordModel.conversation_id == conversation_id)
        with self._session_factory() as session:
            rows = session.execute(statement).scalars().all()
        return [_from_model(row) for row in rows]


def build_provider_cost_records(
    *,
    provider: str,
    payload: dict[str, Any] | None,
    organization_id: str | None,
    conversation_id: str | None,
    realtime_session_id: str | None = None,
    turn_trace_id: str | None = None,
    tool_invocation_id: str | None = None,
    default_cost_type: str = "provider_event",
    occurred_at: datetime | None = None,
) -> list[ProviderCostRecord]:
    if not payload:
        return []
    normalized_payload = dict(payload)
    candidates: list[dict[str, Any]] = []
    explicit_many = normalized_payload.get("provider_cost_records")
    explicit_one = normalized_payload.get("provider_cost_record")
    if isinstance(explicit_many, list):
        candidates.extend(item for item in explicit_many if isinstance(item, dict))
    elif isinstance(explicit_one, dict):
        candidates.append(explicit_one)
    elif any(key in normalized_payload for key in ("provider_cost_usd", "cost_usd", "amount_usd")):
        candidates.append(normalized_payload)

    records: list[ProviderCostRecord] = []
    for item in candidates:
        amount = _coerce_amount(
            item.get("amount_usd", item.get("cost_usd", item.get("provider_cost_usd")))
        )
        if amount is None:
            continue
        record_occurred_at = _coerce_datetime(item.get("occurred_at")) or occurred_at or _utcnow()
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        reference_key = _coerce_reference_key(item)
        cost_type = str(item.get("cost_type") or item.get("type") or default_cost_type).strip() or default_cost_type
        record = ProviderCostRecord(
            organization_id=organization_id,
            conversation_id=conversation_id,
            realtime_session_id=realtime_session_id,
            turn_trace_id=turn_trace_id,
            tool_invocation_id=tool_invocation_id,
            provider=str(item.get("provider") or provider).strip() or provider,
            cost_type=cost_type,
            amount_usd=amount,
            reference_key=reference_key,
            metadata=metadata,
            occurred_at=record_occurred_at,
            created_at=_utcnow(),
        )
        records.append(record)
    return records


def _coerce_amount(value: Any) -> float | None:
    if value is None:
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return amount


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return None


def _coerce_reference_key(payload: dict[str, Any]) -> str | None:
    for key in ("reference_key", "message_id", "event_id", "provider_message_id", "session_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _to_model(record: ProviderCostRecord) -> ProviderCostRecordModel:
    return ProviderCostRecordModel(
        cost_record_id=record.cost_record_id,
        organization_id=record.organization_id,
        conversation_id=record.conversation_id,
        realtime_session_id=record.realtime_session_id,
        turn_trace_id=record.turn_trace_id,
        tool_invocation_id=record.tool_invocation_id,
        provider=record.provider,
        cost_type=record.cost_type,
        amount_usd=record.amount_usd,
        reference_key=record.reference_key,
        metadata_json=dict(record.metadata),
        occurred_at=record.occurred_at,
        created_at=record.created_at,
    )


def _from_model(record: ProviderCostRecordModel) -> ProviderCostRecord:
    return ProviderCostRecord(
        cost_record_id=record.cost_record_id,
        organization_id=record.organization_id,
        conversation_id=record.conversation_id,
        realtime_session_id=record.realtime_session_id,
        turn_trace_id=record.turn_trace_id,
        tool_invocation_id=record.tool_invocation_id,
        provider=record.provider,
        cost_type=record.cost_type,
        amount_usd=record.amount_usd,
        reference_key=record.reference_key,
        metadata=dict(record.metadata_json or {}),
        occurred_at=record.occurred_at,
        created_at=record.created_at,
    )
