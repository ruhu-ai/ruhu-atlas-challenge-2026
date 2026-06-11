from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import Select, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

from ..db_models import (
    ConversationRecord,
    RealtimeEventRecord,
    RealtimeIdempotencyKeyRecord,
    RealtimeOutboxRecord,
    RealtimeSessionRecord,
)
from .models import RealtimeEvent, RealtimeIdempotencyKey, RealtimeOutboxEntry, RealtimeSession


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _idempotency_org_filter(column, organization_id: str | None):  # type: ignore[no-untyped-def]
    """SQL filter that treats None as the untenanted partition.

    Migration 0043 made ``RealtimeIdempotencyKeyRecord.organization_id``
    nullable, so callers pass ``None`` directly for the untenanted
    bucket.  A functional unique index on
    ``coalesce(organization_id, '')`` preserves the prior per-(scope,
    idempotency_key) uniqueness semantics that used to rely on the
    ``"public"`` sentinel.
    """
    if organization_id is None:
        return column.is_(None)
    return column == organization_id


def public_organization_scope(organization_id: str | None) -> str:
    """Deprecated alias retained while callers migrate to the nullable
    organization_id pattern (see ``_idempotency_org_filter``).

    Returns the ``"public"`` sentinel for untenanted callers so older
    call sites keep compiling.  New code should pass ``None`` directly
    and rely on the nullable-column filter helper.
    """
    return organization_id or "public"


def _new_idempotency_key_id() -> str:
    from uuid import uuid4

    return uuid4().hex


_IDEMPOTENCY_STATUS_PROCESSING = "processing"
_IDEMPOTENCY_STATUS_COMPLETED = "completed"
_IDEMPOTENCY_STATUS_FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RealtimeIdempotencyLease:
    key_record: RealtimeIdempotencyKey
    owned: bool


def _parse_idempotency_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _idempotency_status(record: RealtimeIdempotencyKeyRecord) -> str:
    result_ref = dict(record.result_ref_json or {})
    status = result_ref.get("_status")
    if isinstance(status, str) and status in {
        _IDEMPOTENCY_STATUS_PROCESSING,
        _IDEMPOTENCY_STATUS_COMPLETED,
        _IDEMPOTENCY_STATUS_FAILED,
    }:
        return status
    if record.result_event_id is not None:
        return _IDEMPOTENCY_STATUS_COMPLETED
    return _IDEMPOTENCY_STATUS_PROCESSING


def _idempotency_owned_by(record: RealtimeIdempotencyKeyRecord, processing_token: str) -> bool:
    result_ref = dict(record.result_ref_json or {})
    return result_ref.get("_processing_token") == processing_token


def _prepare_processing_result_ref(
    existing: dict[str, object] | None,
    *,
    processing_token: str,
    processing_started_at: datetime,
) -> dict[str, object]:
    result_ref = dict(existing or {})
    result_ref["_status"] = _IDEMPOTENCY_STATUS_PROCESSING
    result_ref["_processing_token"] = processing_token
    result_ref["_processing_started_at"] = processing_started_at.isoformat()
    result_ref.pop("_completed_at", None)
    result_ref.pop("_failed_at", None)
    result_ref.pop("_failure", None)
    return result_ref


class SQLAlchemyRealtimeSessionStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def load(self, realtime_session_id: str) -> RealtimeSession | None:
        with self._session_factory() as session:
            record = session.get(RealtimeSessionRecord, realtime_session_id)
            return None if record is None else _record_to_session(record)

    def load_by_external_key(
        self,
        *,
        conversation_id: str,
        provider: str,
        external_session_key: str,
    ) -> RealtimeSession | None:
        statement: Select[tuple[RealtimeSessionRecord]] = select(RealtimeSessionRecord).where(
            RealtimeSessionRecord.conversation_id == conversation_id,
            RealtimeSessionRecord.provider == provider,
            RealtimeSessionRecord.external_session_key == external_session_key,
        ).order_by(RealtimeSessionRecord.created_at.desc())
        with self._session_factory() as session:
            record = session.execute(statement).scalars().first()
            return None if record is None else _record_to_session(record)

    def save(self, realtime_session: RealtimeSession) -> None:
        with self._session_factory.begin() as session:
            record = session.get(RealtimeSessionRecord, realtime_session.realtime_session_id)
            if record is None:
                session.add(_session_to_record(realtime_session))
                return
            record.organization_id = realtime_session.organization_id
            record.conversation_id = realtime_session.conversation_id
            record.parent_realtime_session_id = realtime_session.parent_realtime_session_id
            record.surface = realtime_session.surface
            record.channel = realtime_session.channel
            record.modality = realtime_session.modality
            record.status = realtime_session.status
            record.provider = realtime_session.provider
            record.external_session_key = realtime_session.external_session_key
            record.provider_session_id = realtime_session.provider_session_id
            record.participant_identity = realtime_session.participant_identity
            record.transport_metadata_json = dict(realtime_session.transport_metadata)
            record.started_at = realtime_session.started_at
            record.last_seen_at = realtime_session.last_seen_at
            record.ended_at = realtime_session.ended_at
            record.created_at = realtime_session.created_at
            record.updated_at = realtime_session.updated_at

    def list_by_conversation(self, conversation_id: str) -> list[RealtimeSession]:
        statement: Select[tuple[RealtimeSessionRecord]] = select(RealtimeSessionRecord).where(
            RealtimeSessionRecord.conversation_id == conversation_id
        ).order_by(RealtimeSessionRecord.created_at.asc())
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_session(record) for record in records]

    def list_for_org(
        self,
        *,
        organization_id: str | None,
        status: str | None = None,
        channel: str | None = None,
        surface: str | None = None,
        provider: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RealtimeSession]:
        statement: Select[tuple[RealtimeSessionRecord]] = select(RealtimeSessionRecord).where(
            RealtimeSessionRecord.organization_id == organization_id
        )
        if status is not None:
            statement = statement.where(RealtimeSessionRecord.status == status)
        if channel is not None:
            statement = statement.where(RealtimeSessionRecord.channel == channel)
        if surface is not None:
            statement = statement.where(RealtimeSessionRecord.surface == surface)
        if provider is not None:
            statement = statement.where(RealtimeSessionRecord.provider == provider)
        statement = statement.order_by(RealtimeSessionRecord.started_at.desc()).offset(max(0, offset)).limit(max(1, limit))
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_session(record) for record in records]

    def count_active(
        self,
        *,
        organization_id: str | None,
        channel: str | None = None,
        surface: str | None = None,
        provider: str | None = None,
    ) -> int:
        statement = select(func.count()).select_from(RealtimeSessionRecord).where(
            RealtimeSessionRecord.organization_id == organization_id,
            RealtimeSessionRecord.status == "active",
        )
        if channel is not None:
            statement = statement.where(RealtimeSessionRecord.channel == channel)
        if surface is not None:
            statement = statement.where(RealtimeSessionRecord.surface == surface)
        if provider is not None:
            statement = statement.where(RealtimeSessionRecord.provider == provider)
        with self._session_factory() as session:
            return int(session.execute(statement).scalar_one() or 0)

    def load_by_room_name(
        self,
        *,
        room_name: str,
        provider: str | None = "livekit",
        surface: str | None = "voice",
    ) -> RealtimeSession | None:
        statement: Select[tuple[RealtimeSessionRecord]] = select(RealtimeSessionRecord).where(
            RealtimeSessionRecord.provider_session_id == room_name
        )
        if provider is not None:
            statement = statement.where(RealtimeSessionRecord.provider == provider)
        if surface is not None:
            statement = statement.where(RealtimeSessionRecord.surface == surface)
        statement = statement.order_by(RealtimeSessionRecord.created_at.desc())
        with self._session_factory() as session:
            direct = session.execute(statement).scalars().first()
            if direct is not None:
                return _record_to_session(direct)

            # Cross-database JSON extraction support differs; fallback to Python filtering.
            fallback_statement: Select[tuple[RealtimeSessionRecord]] = select(RealtimeSessionRecord)
            if provider is not None:
                fallback_statement = fallback_statement.where(RealtimeSessionRecord.provider == provider)
            if surface is not None:
                fallback_statement = fallback_statement.where(RealtimeSessionRecord.surface == surface)
            fallback_statement = fallback_statement.order_by(RealtimeSessionRecord.created_at.desc()).limit(500)
            candidates = session.execute(fallback_statement).scalars().all()

        for record in candidates:
            metadata = dict(record.transport_metadata_json or {})
            if str(metadata.get("room_name") or "").strip() == room_name:
                return _record_to_session(record)
        return None

    def list_stale_active(
        self,
        *,
        channel: str | None = None,
        provider: str | None = None,
        surface: str | None = None,
        last_seen_before: datetime,
        limit: int = 100,
    ) -> list[RealtimeSession]:
        statement: Select[tuple[RealtimeSessionRecord]] = select(RealtimeSessionRecord).where(
            RealtimeSessionRecord.status == "active",
            RealtimeSessionRecord.last_seen_at.is_not(None),
            RealtimeSessionRecord.last_seen_at < last_seen_before,
        )
        if channel is not None:
            statement = statement.where(RealtimeSessionRecord.channel == channel)
        if provider is not None:
            statement = statement.where(RealtimeSessionRecord.provider == provider)
        if surface is not None:
            statement = statement.where(RealtimeSessionRecord.surface == surface)
        statement = statement.order_by(RealtimeSessionRecord.last_seen_at.asc()).limit(limit)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_session(record) for record in records]


_NOTIFY_CHANNEL = "ruhu_realtime_events"


class SQLAlchemyRealtimeEventStore:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        enable_pg_notify: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._enable_pg_notify = enable_pg_notify

    def append(
        self,
        *,
        conversation_id: str,
        organization_id: str | None,
        family: str,
        name: str,
        payload: dict[str, object] | None = None,
        audiences: list[str] | None = None,
        projection_policy: dict[str, object] | None = None,
        realtime_session_id: str | None = None,
        causation_id: str | None = None,
        correlation_id: str | None = None,
        actor_type: str | None = None,
        actor_id: str | None = None,
        visibility: str = "surface",
        event_id: str | None = None,
        created_at: datetime | None = None,
        outbox_topic: str | None = None,
        outbox_dedupe_key: str | None = None,
    ) -> RealtimeEvent:
        event_id = event_id or f"evt_{uuid4().hex}"
        created_at = created_at or _utcnow()
        with self._session_factory.begin() as session:
            conversation = session.execute(
                select(ConversationRecord)
                .where(ConversationRecord.conversation_id == conversation_id)
                .with_for_update()
            ).scalar_one()
            next_sequence = int(conversation.last_event_sequence or 0) + 1
            conversation.last_event_sequence = next_sequence
            conversation.updated_at = created_at
            record = RealtimeEventRecord(
                event_id=event_id,
                conversation_id=conversation_id,
                realtime_session_id=realtime_session_id,
                organization_id=organization_id,
                family=family,
                name=name,
                conversation_sequence=next_sequence,
                causation_id=causation_id,
                correlation_id=correlation_id,
                actor_type=actor_type,
                actor_id=actor_id,
                visibility=visibility,
                audiences_json=list(audiences or []),
                projection_policy_json=dict(projection_policy or {}),
                payload_json=dict(payload or {}),
                created_at=created_at,
            )
            session.add(record)
            if outbox_topic:
                now = created_at
                session.add(
                    RealtimeOutboxRecord(
                        outbox_id=f"out_{uuid4().hex}",
                        organization_id=organization_id,
                        conversation_id=conversation_id,
                        event_id=event_id,
                        topic=outbox_topic,
                        dedupe_key=outbox_dedupe_key or event_id,
                        status="pending",
                        payload_json={
                            "event_id": event_id,
                            "conversation_id": conversation_id,
                            "family": family,
                            "name": name,
                        },
                        available_at=now,
                        claimed_at=None,
                        delivered_at=None,
                        last_error=None,
                        attempt_count=0,
                        created_at=now,
                        updated_at=now,
                    )
                )
            # Issue Postgres NOTIFY inside the same transaction so subscribers
            # are notified atomically with the event insert.
            if self._enable_pg_notify:
                try:
                    session.execute(
                        text("SELECT pg_notify(:channel, :payload)"),
                        {"channel": _NOTIFY_CHANNEL, "payload": f"{conversation_id}:{next_sequence}"},
                    )
                except Exception:
                    logger.warning("pg_notify failed for conversation %s", conversation_id, exc_info=True)
        return RealtimeEvent(
            event_id=event_id,
            conversation_id=conversation_id,
            realtime_session_id=realtime_session_id,
            organization_id=organization_id,
            family=family,
            name=name,
            conversation_sequence=next_sequence,
            causation_id=causation_id,
            correlation_id=correlation_id,
            actor_type=actor_type,
            actor_id=actor_id,
            visibility=visibility,
            audiences=list(audiences or []),
            projection_policy=dict(projection_policy or {}),
            payload=dict(payload or {}),
            created_at=created_at,
        )

    def load(self, event_id: str) -> RealtimeEvent | None:
        with self._session_factory() as session:
            record = session.get(RealtimeEventRecord, event_id)
            return None if record is None else _record_to_event(record)

    def replay(
        self,
        *,
        conversation_id: str,
        after_sequence: int | None = None,
        after_event_id: str | None = None,
    ) -> list[RealtimeEvent]:
        try:
            from ..observability.metrics import sse_poll_queries_total
            sse_poll_queries_total.inc()
        except Exception:
            pass
        statement: Select[tuple[RealtimeEventRecord]] = select(RealtimeEventRecord).where(
            RealtimeEventRecord.conversation_id == conversation_id
        )
        if after_sequence is not None:
            statement = statement.where(RealtimeEventRecord.conversation_sequence > after_sequence)
        elif after_event_id is not None:
            with self._session_factory() as session:
                anchor = session.get(RealtimeEventRecord, after_event_id)
                if anchor is None:
                    return []
                anchor_sequence = anchor.conversation_sequence
            statement = statement.where(RealtimeEventRecord.conversation_sequence > anchor_sequence)
        statement = statement.order_by(RealtimeEventRecord.conversation_sequence.asc())
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_event(record) for record in records]


class SQLAlchemyRealtimeIdempotencyStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def load(self, *, organization_id: str | None, scope: str, idempotency_key: str) -> RealtimeIdempotencyKey | None:
        statement: Select[tuple[RealtimeIdempotencyKeyRecord]] = select(RealtimeIdempotencyKeyRecord).where(
            _idempotency_org_filter(RealtimeIdempotencyKeyRecord.organization_id, organization_id),
            RealtimeIdempotencyKeyRecord.scope == scope,
            RealtimeIdempotencyKeyRecord.idempotency_key == idempotency_key,
        )
        with self._session_factory() as session:
            record = session.execute(statement).scalars().first()
            return None if record is None else _record_to_idempotency(record)

    def acquire_processing(
        self,
        *,
        organization_id: str | None,
        scope: str,
        idempotency_key: str,
        conversation_id: str | None,
        processing_token: str,
        processing_started_at: datetime,
        stale_after_seconds: int = 300,
    ) -> RealtimeIdempotencyLease:
        stale_before = processing_started_at - timedelta(seconds=max(stale_after_seconds, 1))

        for _ in range(2):
            try:
                with self._session_factory.begin() as session:
                    record = session.execute(
                        select(RealtimeIdempotencyKeyRecord)
                        .where(
                            _idempotency_org_filter(RealtimeIdempotencyKeyRecord.organization_id, organization_id),
                            RealtimeIdempotencyKeyRecord.scope == scope,
                            RealtimeIdempotencyKeyRecord.idempotency_key == idempotency_key,
                        )
                        .with_for_update()
                    ).scalars().first()
                    if record is None:
                        created = RealtimeIdempotencyKeyRecord(
                            key_id=_new_idempotency_key_id(),
                            organization_id=organization_id,
                            scope=scope,
                            idempotency_key=idempotency_key,
                            conversation_id=conversation_id,
                            result_event_id=None,
                            result_ref_json=_prepare_processing_result_ref(
                                None,
                                processing_token=processing_token,
                                processing_started_at=processing_started_at,
                            ),
                            created_at=processing_started_at,
                            expires_at=None,
                        )
                        session.add(created)
                        session.flush()
                        return RealtimeIdempotencyLease(
                            key_record=_record_to_idempotency(created),
                            owned=True,
                        )

                    owned = False
                    status = _idempotency_status(record)
                    result_ref = dict(record.result_ref_json or {})
                    processing_started = _parse_idempotency_timestamp(result_ref.get("_processing_started_at"))
                    is_stale = status == _IDEMPOTENCY_STATUS_PROCESSING and (
                        processing_started is None or processing_started < stale_before
                    )
                    if status == _IDEMPOTENCY_STATUS_COMPLETED and record.result_event_id is not None:
                        owned = False
                    elif status == _IDEMPOTENCY_STATUS_FAILED or is_stale:
                        owned = True

                    if owned:
                        record.conversation_id = conversation_id
                        record.result_ref_json = _prepare_processing_result_ref(
                            record.result_ref_json,
                            processing_token=processing_token,
                            processing_started_at=processing_started_at,
                        )
                        session.flush()
                    return RealtimeIdempotencyLease(
                        key_record=_record_to_idempotency(record),
                        owned=owned,
                    )
            except IntegrityError:
                continue
        raise RuntimeError("failed to acquire realtime idempotency lease")

    def save_result_event(
        self,
        *,
        organization_id: str | None,
        scope: str,
        idempotency_key: str,
        processing_token: str,
        conversation_id: str | None,
        result_event_id: str,
    ) -> RealtimeIdempotencyKey | None:
        with self._session_factory.begin() as session:
            record = session.execute(
                select(RealtimeIdempotencyKeyRecord)
                .where(
                    _idempotency_org_filter(RealtimeIdempotencyKeyRecord.organization_id, organization_id),
                    RealtimeIdempotencyKeyRecord.scope == scope,
                    RealtimeIdempotencyKeyRecord.idempotency_key == idempotency_key,
                )
                .with_for_update()
            ).scalars().first()
            if record is None or not _idempotency_owned_by(record, processing_token):
                return None
            record.conversation_id = conversation_id
            record.result_event_id = result_event_id
            session.flush()
            return _record_to_idempotency(record)

    def complete_processing(
        self,
        *,
        organization_id: str | None,
        scope: str,
        idempotency_key: str,
        processing_token: str,
        conversation_id: str | None,
        result_event_id: str,
        result_ref: dict[str, object],
        completed_at: datetime,
    ) -> RealtimeIdempotencyKey | None:
        with self._session_factory.begin() as session:
            record = session.execute(
                select(RealtimeIdempotencyKeyRecord)
                .where(
                    _idempotency_org_filter(RealtimeIdempotencyKeyRecord.organization_id, organization_id),
                    RealtimeIdempotencyKeyRecord.scope == scope,
                    RealtimeIdempotencyKeyRecord.idempotency_key == idempotency_key,
                )
                .with_for_update()
            ).scalars().first()
            if record is None or not _idempotency_owned_by(record, processing_token):
                return None
            completed_ref = dict(result_ref)
            completed_ref["_status"] = _IDEMPOTENCY_STATUS_COMPLETED
            completed_ref["_completed_at"] = completed_at.isoformat()
            record.conversation_id = conversation_id
            record.result_event_id = result_event_id
            record.result_ref_json = completed_ref
            session.flush()
            return _record_to_idempotency(record)

    def fail_processing(
        self,
        *,
        organization_id: str | None,
        scope: str,
        idempotency_key: str,
        processing_token: str,
        conversation_id: str | None,
        result_event_id: str | None,
        error_message: str,
        error_type: str | None = None,
        retryable: bool | None = None,
        failed_at: datetime,
    ) -> RealtimeIdempotencyKey | None:
        with self._session_factory.begin() as session:
            record = session.execute(
                select(RealtimeIdempotencyKeyRecord)
                .where(
                    _idempotency_org_filter(RealtimeIdempotencyKeyRecord.organization_id, organization_id),
                    RealtimeIdempotencyKeyRecord.scope == scope,
                    RealtimeIdempotencyKeyRecord.idempotency_key == idempotency_key,
                )
                .with_for_update()
            ).scalars().first()
            if record is None or not _idempotency_owned_by(record, processing_token):
                return None
            failed_ref = dict(record.result_ref_json or {})
            failed_ref["_status"] = _IDEMPOTENCY_STATUS_FAILED
            failed_ref["_failed_at"] = failed_at.isoformat()
            failure: dict[str, object] = {"message": error_message}
            if error_type:
                failure["type"] = error_type
            if retryable is not None:
                failure["retryable"] = retryable
            failed_ref["_failure"] = failure
            failed_ref.pop("_processing_token", None)
            failed_ref.pop("_processing_started_at", None)
            record.conversation_id = conversation_id
            if result_event_id is not None:
                record.result_event_id = result_event_id
            record.result_ref_json = failed_ref
            session.flush()
            return _record_to_idempotency(record)

    def save(self, key_record: RealtimeIdempotencyKey) -> None:
        with self._session_factory.begin() as session:
            record = session.execute(
                select(RealtimeIdempotencyKeyRecord).where(
                    RealtimeIdempotencyKeyRecord.organization_id == key_record.organization_id,
                    RealtimeIdempotencyKeyRecord.scope == key_record.scope,
                    RealtimeIdempotencyKeyRecord.idempotency_key == key_record.idempotency_key,
                )
            ).scalars().first()
            if record is None:
                session.add(_idempotency_to_record(key_record))
                return
            record.conversation_id = key_record.conversation_id
            record.result_event_id = key_record.result_event_id
            record.result_ref_json = dict(key_record.result_ref)
            record.created_at = key_record.created_at
            record.expires_at = key_record.expires_at


class SQLAlchemyRealtimeOutboxStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_pending(
        self,
        *,
        topic: str | None = None,
        limit: int = 100,
        reclaim_after_seconds: int = 60,
    ) -> list[RealtimeOutboxEntry]:
        now = _utcnow()
        reclaim_before = now - timedelta(seconds=max(reclaim_after_seconds, 1))
        statement: Select[tuple[RealtimeOutboxRecord]] = select(RealtimeOutboxRecord).where(
            RealtimeOutboxRecord.available_at <= now,
            (
                (RealtimeOutboxRecord.status == "pending")
                | (
                    (RealtimeOutboxRecord.status == "claimed")
                    & (RealtimeOutboxRecord.claimed_at.is_not(None))
                    & (RealtimeOutboxRecord.claimed_at < reclaim_before)
                )
            ),
        )
        if topic is not None:
            statement = statement.where(RealtimeOutboxRecord.topic == topic)
        statement = statement.order_by(RealtimeOutboxRecord.available_at.asc()).limit(limit)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_outbox(record) for record in records]

    def claim(
        self,
        outbox_id: str,
        *,
        claimed_at: datetime | None = None,
        reclaim_after_seconds: int = 60,
    ) -> RealtimeOutboxEntry | None:
        claimed_at = claimed_at or _utcnow()
        reclaim_before = claimed_at - timedelta(seconds=max(reclaim_after_seconds, 1))
        with self._session_factory.begin() as session:
            record = session.execute(
                select(RealtimeOutboxRecord)
                .where(RealtimeOutboxRecord.outbox_id == outbox_id)
                .with_for_update()
            ).scalars().first()
            if record is None:
                return None
            if record.status == "claimed":
                if record.claimed_at is None or record.claimed_at >= reclaim_before:
                    return None
            elif record.status != "pending":
                return None
            if record.available_at > claimed_at:
                return None
            record.status = "claimed"
            record.claimed_at = claimed_at
            record.updated_at = claimed_at
            session.flush()
            session.refresh(record)
            return _record_to_outbox(record)

    def enqueue(
        self,
        *,
        event_id: str,
        topic: str,
        conversation_id: str | None = None,
        organization_id: str | None = None,
        payload: dict[str, object] | None = None,
        available_at: datetime | None = None,
        dedupe_key: str | None = None,
    ) -> RealtimeOutboxEntry:
        now = available_at or _utcnow()
        entry = RealtimeOutboxEntry(
            outbox_id=f"out_{uuid4().hex}",
            organization_id=organization_id,
            conversation_id=conversation_id,
            event_id=event_id,
            topic=topic,
            dedupe_key=None if dedupe_key is None else dedupe_key.strip() or None,
            status="pending",
            payload=dict(payload or {}),
            available_at=now,
            claimed_at=None,
            delivered_at=None,
            last_error=None,
            attempt_count=0,
            created_at=now,
            updated_at=now,
        )
        with self._session_factory.begin() as session:
            if isinstance(dedupe_key, str) and dedupe_key.strip():
                existing = session.execute(
                    select(RealtimeOutboxRecord)
                    .where(RealtimeOutboxRecord.topic == topic)
                    .where(RealtimeOutboxRecord.dedupe_key == dedupe_key.strip())
                    .with_for_update()
                ).scalars().first()
                if existing is not None:
                    session.flush()
                    session.refresh(existing)
                    return _record_to_outbox(existing)
            session.add(
                RealtimeOutboxRecord(
                    outbox_id=entry.outbox_id,
                    organization_id=entry.organization_id,
                    conversation_id=entry.conversation_id,
                    event_id=entry.event_id,
                    topic=entry.topic,
                    dedupe_key=entry.dedupe_key,
                    status=entry.status,
                    payload_json=dict(entry.payload),
                    available_at=entry.available_at,
                    claimed_at=entry.claimed_at,
                    delivered_at=entry.delivered_at,
                    last_error=entry.last_error,
                    attempt_count=entry.attempt_count,
                    created_at=entry.created_at,
                    updated_at=entry.updated_at,
                )
            )
        return entry

    def mark_delivered(self, outbox_id: str, *, delivered_at: datetime | None = None) -> None:
        delivered_at = delivered_at or _utcnow()
        with self._session_factory.begin() as session:
            record = session.get(RealtimeOutboxRecord, outbox_id)
            if record is None:
                return
            record.status = "delivered"
            record.claimed_at = None
            record.delivered_at = delivered_at
            record.updated_at = delivered_at
            record.last_error = None

    def mark_failed(
        self,
        outbox_id: str,
        *,
        error: str,
        failed_at: datetime | None = None,
    ) -> None:
        failed_at = failed_at or _utcnow()
        with self._session_factory.begin() as session:
            record = session.get(RealtimeOutboxRecord, outbox_id)
            if record is None:
                return
            record.status = "failed"
            record.claimed_at = None
            record.last_error = error[:1000]
            record.attempt_count = int(record.attempt_count or 0) + 1
            record.updated_at = failed_at

    def mark_retry(
        self,
        outbox_id: str,
        *,
        error: str,
        available_at: datetime,
        retry_at: datetime | None = None,
    ) -> RealtimeOutboxEntry | None:
        retry_at = retry_at or _utcnow()
        with self._session_factory.begin() as session:
            record = session.get(RealtimeOutboxRecord, outbox_id)
            if record is None:
                return None
            record.status = "pending"
            record.claimed_at = None
            record.last_error = error[:1000]
            record.available_at = available_at
            record.attempt_count = int(record.attempt_count or 0) + 1
            record.updated_at = retry_at
            session.flush()
            session.refresh(record)
            return _record_to_outbox(record)

    def purge_delivered(
        self,
        *,
        older_than: datetime | None = None,
        batch_size: int = 500,
    ) -> int:
        """Delete delivered outbox rows older than the given threshold.

        Returns the number of rows purged.  Intended to be called by a periodic
        cleanup worker to prevent unbounded table growth (doc 01 finding #4).
        """
        threshold = older_than or (_utcnow() - timedelta(hours=24))
        with self._session_factory.begin() as session:
            subquery = (
                select(RealtimeOutboxRecord.outbox_id)
                .where(RealtimeOutboxRecord.status == "delivered")
                .where(RealtimeOutboxRecord.delivered_at <= threshold)
                .limit(batch_size)
            )
            result = session.execute(
                RealtimeOutboxRecord.__table__.delete().where(
                    RealtimeOutboxRecord.outbox_id.in_(subquery)
                )
            )
            purged = result.rowcount
        if purged > 0:
            logger.info(
                "purged %d delivered outbox rows older than %s",
                purged,
                threshold.isoformat(),
            )
            try:
                from ..observability.metrics import registry
                from prometheus_client import Counter
                if not hasattr(self, "_purge_counter"):
                    self._purge_counter = Counter(
                        "ruhu_realtime_outbox_delivered_purged_total",
                        "Delivered outbox rows purged by cleanup worker",
                        [],
                        registry=registry,
                    )
                self._purge_counter.inc(purged)
            except Exception:
                pass
        return purged


def _session_to_record(model: RealtimeSession) -> RealtimeSessionRecord:
    return RealtimeSessionRecord(
        realtime_session_id=model.realtime_session_id,
        conversation_id=model.conversation_id,
        organization_id=model.organization_id,
        parent_realtime_session_id=model.parent_realtime_session_id,
        surface=model.surface,
        channel=model.channel,
        modality=model.modality,
        status=model.status,
        provider=model.provider,
        external_session_key=model.external_session_key,
        provider_session_id=model.provider_session_id,
        participant_identity=model.participant_identity,
        transport_metadata_json=dict(model.transport_metadata),
        started_at=model.started_at,
        last_seen_at=model.last_seen_at,
        ended_at=model.ended_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _record_to_session(record: RealtimeSessionRecord) -> RealtimeSession:
    return RealtimeSession(
        realtime_session_id=record.realtime_session_id,
        conversation_id=record.conversation_id,
        organization_id=record.organization_id,
        parent_realtime_session_id=record.parent_realtime_session_id,
        surface=record.surface,
        channel=record.channel,
        modality=record.modality,
        status=record.status,
        provider=record.provider,
        external_session_key=record.external_session_key,
        provider_session_id=record.provider_session_id,
        participant_identity=record.participant_identity,
        transport_metadata=dict(record.transport_metadata_json or {}),
        started_at=record.started_at,
        last_seen_at=record.last_seen_at,
        ended_at=record.ended_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _record_to_event(record: RealtimeEventRecord) -> RealtimeEvent:
    return RealtimeEvent(
        event_id=record.event_id,
        conversation_id=record.conversation_id,
        realtime_session_id=record.realtime_session_id,
        organization_id=record.organization_id,
        family=record.family,
        name=record.name,
        conversation_sequence=record.conversation_sequence,
        causation_id=record.causation_id,
        correlation_id=record.correlation_id,
        actor_type=record.actor_type,
        actor_id=record.actor_id,
        visibility=record.visibility,
        audiences=list(record.audiences_json or []),
        projection_policy=dict(record.projection_policy_json or {}),
        payload=dict(record.payload_json or {}),
        created_at=record.created_at,
    )


def _idempotency_to_record(model: RealtimeIdempotencyKey) -> RealtimeIdempotencyKeyRecord:
    return RealtimeIdempotencyKeyRecord(
        organization_id=model.organization_id,
        scope=model.scope,
        idempotency_key=model.idempotency_key,
        conversation_id=model.conversation_id,
        result_event_id=model.result_event_id,
        result_ref_json=dict(model.result_ref),
        created_at=model.created_at,
        expires_at=model.expires_at,
    )


def _record_to_idempotency(record: RealtimeIdempotencyKeyRecord) -> RealtimeIdempotencyKey:
    return RealtimeIdempotencyKey(
        organization_id=record.organization_id,
        scope=record.scope,
        idempotency_key=record.idempotency_key,
        conversation_id=record.conversation_id,
        result_event_id=record.result_event_id,
        result_ref=dict(record.result_ref_json or {}),
        created_at=record.created_at,
        expires_at=record.expires_at,
    )


def _record_to_outbox(record: RealtimeOutboxRecord) -> RealtimeOutboxEntry:
    return RealtimeOutboxEntry(
        outbox_id=record.outbox_id,
        organization_id=record.organization_id,
        conversation_id=record.conversation_id,
        event_id=record.event_id,
        topic=record.topic,
        dedupe_key=record.dedupe_key,
        status=record.status,
        payload=dict(record.payload_json or {}),
        available_at=record.available_at,
        claimed_at=record.claimed_at,
        delivered_at=record.delivered_at,
        last_error=record.last_error,
        attempt_count=record.attempt_count,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
