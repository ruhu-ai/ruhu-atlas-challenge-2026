from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from .atlas_models import (
    AtlasAgentPolicy,
    AtlasApplyRequestRecordModel,
    AtlasEvent,
    AtlasMessage,
    AtlasPermissionRequest,
    AtlasReviewDecisionRecord,
    AtlasSession,
)
from .atlas_protocol import (
    AgentMetadataDelta,
    AtlasProposedChanges,
    ChannelPolicyDelta,
    IntegrationBindingDelta,
    KnowledgeDelta,
    RuleDelta,
    ScenarioDelta,
    ScenarioRouteDelta,
    StepDelta,
)
from .db_models import (
    AtlasAgentPolicyRecord,
    AtlasApplyRequestRecord,
    AtlasEventRecord,
    AtlasMessageRecord,
    AtlasPermissionRequestRecord,
    AtlasProposedDeltaRecord,
    AtlasReviewDecisionRecord as AtlasReviewDecisionORM,
    AtlasSessionRecord,
)


logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _advisory_lock_key(value: str) -> int:
    digest = hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


class AtlasStore(Protocol):
    def get_agent_policy(self, agent_id: str, *, organization_id: str | None = None) -> AtlasAgentPolicy | None: ...
    def set_agent_policy(
        self,
        agent_id: str,
        *,
        organization_id: str | None,
        atlas_enabled: bool,
        updated_by_user_id: str | None,
    ) -> AtlasAgentPolicy: ...
    def create_session(self, session: AtlasSession) -> AtlasSession: ...
    def get_session(self, session_id: str, *, organization_id: str | None = None) -> AtlasSession | None: ...
    def list_sessions(
        self,
        *,
        organization_id: str | None = None,
        agent_id: str | None = None,
        scope: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AtlasSession], int, bool]: ...
    def update_session(
        self,
        session: AtlasSession,
        *,
        organization_id: str | None = None,
        expected_updated_at: datetime | None = None,
    ) -> AtlasSession: ...
    def update_session_status(
        self,
        session_id: str,
        status: str,
        *,
        organization_id: str | None = None,
        updated_at: datetime | None = None,
    ) -> AtlasSession: ...
    def archive_session(self, session_id: str, *, organization_id: str | None = None) -> AtlasSession | None: ...
    def append_message(self, message: AtlasMessage) -> AtlasMessage: ...
    def list_messages(
        self,
        session_id: str,
        *,
        organization_id: str | None = None,
        before_sequence: int | None = None,
        limit: int = 50,
    ) -> tuple[list[AtlasMessage], int, bool]: ...
    def append_event(self, event: AtlasEvent) -> AtlasEvent: ...
    def list_events(
        self,
        session_id: str,
        *,
        organization_id: str | None = None,
        after_sequence: int | None = None,
        limit: int = 100,
    ) -> tuple[list[AtlasEvent], int, bool]: ...
    def save_review_decisions(self, decisions: list[AtlasReviewDecisionRecord]) -> list[AtlasReviewDecisionRecord]: ...
    def list_review_decisions(
        self,
        session_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[AtlasReviewDecisionRecord]: ...
    def replace_proposed_changes(
        self,
        session_id: str,
        proposed_changes: AtlasProposedChanges,
        *,
        organization_id: str | None = None,
    ) -> AtlasProposedChanges: ...
    def load_proposed_changes(
        self,
        session_id: str,
        *,
        organization_id: str | None = None,
    ) -> AtlasProposedChanges: ...
    def apply_lock(
        self,
        session_id: str,
        *,
        organization_id: str | None = None,
    ) -> Iterator[None]: ...
    def update_proposed_delta_statuses(
        self,
        session_id: str,
        statuses_by_delta_id: dict[str, str],
        *,
        organization_id: str | None = None,
    ) -> AtlasProposedChanges: ...
    def create_apply_request(self, request: AtlasApplyRequestRecordModel) -> AtlasApplyRequestRecordModel: ...
    def latest_apply_request(
        self,
        session_id: str,
        *,
        organization_id: str | None = None,
    ) -> AtlasApplyRequestRecordModel | None: ...
    def create_permission_request(self, request: AtlasPermissionRequest) -> AtlasPermissionRequest: ...
    def list_permission_requests(
        self,
        session_id: str,
        *,
        organization_id: str | None = None,
        status: str | None = None,
    ) -> list[AtlasPermissionRequest]: ...
    def find_approved_apply_permission(
        self,
        session_id: str,
        delta_ids: list[str],
        *,
        organization_id: str | None = None,
    ) -> AtlasPermissionRequest | None: ...
    def apply_permission_decisions(
        self,
        session_id: str,
        decisions: list[dict[str, object]],
        *,
        organization_id: str | None = None,
        decided_by_user_id: str | None = None,
    ) -> list[AtlasPermissionRequest]: ...


def _policy_from_record(record: AtlasAgentPolicyRecord) -> AtlasAgentPolicy:
    return AtlasAgentPolicy(
        agent_id=record.agent_id,
        organization_id=record.organization_id,
        atlas_enabled=record.atlas_enabled,
        updated_by_user_id=record.updated_by_user_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _session_from_record(record: AtlasSessionRecord) -> AtlasSession:
    return AtlasSession(
        session_id=record.session_id,
        organization_id=record.organization_id,
        scope=record.scope,  # type: ignore[arg-type]
        status=record.status,  # type: ignore[arg-type]
        agent_id=record.agent_id,
        agent_version_id=record.agent_version_id,
        title=record.title,
        summary=record.summary,
        created_by=record.created_by,
        scenario_id=record.scenario_id,
        step_id=record.step_id,
        conversation_id=record.conversation_id,
        trace_id=record.trace_id,
        atlas_enabled_snapshot=record.atlas_enabled_snapshot,
        archived_at=record.archived_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _message_from_record(record: AtlasMessageRecord) -> AtlasMessage:
    return AtlasMessage(
        message_id=record.message_id,
        session_id=record.session_id,
        organization_id=record.organization_id,
        sequence_number=record.sequence_number,
        role=record.role,  # type: ignore[arg-type]
        content=record.content,
        metadata=dict(record.metadata_json or {}),
        created_at=record.created_at,
    )


def _event_from_record(record: AtlasEventRecord) -> AtlasEvent:
    return AtlasEvent(
        event_id=record.event_id,
        session_id=record.session_id,
        organization_id=record.organization_id,
        sequence_number=record.sequence_number,
        type=record.event_type,  # type: ignore[arg-type]
        payload=dict(record.payload_json or {}),
        created_at=record.created_at,
    )


def _review_decision_from_record(record: AtlasReviewDecisionORM) -> AtlasReviewDecisionRecord:
    return AtlasReviewDecisionRecord(
        review_decision_id=record.review_decision_id,
        session_id=record.session_id,
        organization_id=record.organization_id,
        delta_id=record.delta_id,
        decision=record.decision,  # type: ignore[arg-type]
        delta_payload_hash=record.delta_payload_hash,
        note=record.note,
        decided_by_user_id=record.decided_by_user_id,
        created_at=record.created_at,
    )


def _apply_request_from_record(record: AtlasApplyRequestRecord) -> AtlasApplyRequestRecordModel:
    return AtlasApplyRequestRecordModel(
        apply_request_id=record.apply_request_id,
        session_id=record.session_id,
        organization_id=record.organization_id,
        status=record.status,  # type: ignore[arg-type]
        delta_ids=list(record.delta_ids_json or []),
        apply_note=record.apply_note,
        confirmed_by_user_id=record.confirmed_by_user_id,
        error=record.error,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _permission_request_from_record(record: AtlasPermissionRequestRecord) -> AtlasPermissionRequest:
    return AtlasPermissionRequest(
        request_id=record.request_id,
        session_id=record.session_id,
        organization_id=record.organization_id,
        kind=record.kind,  # type: ignore[arg-type]
        status=record.status,  # type: ignore[arg-type]
        reason=record.reason,
        risk_summary=record.risk_summary,
        scope_ref=dict(record.scope_ref_json or {}),
        delta_ids=list(record.delta_ids_json or []),
        requested_actions=list(record.requested_actions_json or []),
        decision_reason=record.decision_reason,
        decided_by_user_id=record.decided_by_user_id,
        created_at=record.created_at,
        expires_at=record.expires_at,
        decided_at=record.decided_at,
    )


_DELTA_FAMILY_SPECS: tuple[tuple[str, str, type], ...] = (
    ("agent_metadata_deltas", "agent_metadata", AgentMetadataDelta),
    ("scenario_deltas", "scenario", ScenarioDelta),
    ("step_deltas", "step", StepDelta),
    ("scenario_route_deltas", "scenario_route", ScenarioRouteDelta),
    ("channel_policy_deltas", "channel_policy", ChannelPolicyDelta),
    ("rule_deltas", "rule", RuleDelta),
    ("knowledge_deltas", "knowledge", KnowledgeDelta),
    ("integration_binding_deltas", "integration_binding", IntegrationBindingDelta),
)

_DELTA_FAMILY_TO_ATTR = {family: attr for attr, family, _ in _DELTA_FAMILY_SPECS}
_DELTA_FAMILY_TO_CLASS = {family: cls for _, family, cls in _DELTA_FAMILY_SPECS}


class SQLAlchemyAtlasStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def _next_sequence(self, session: Session, orm_cls, field_name: str, session_id: str) -> int:
        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            lock_key = _advisory_lock_key(f"atlas-sequence:{orm_cls.__tablename__}:{session_id}")
            session.execute(select(func.pg_advisory_xact_lock(lock_key)))
        column = getattr(orm_cls, field_name)
        current = session.execute(
            select(func.coalesce(func.max(column), 0)).where(orm_cls.session_id == session_id)
        ).scalar_one()
        return int(current) + 1

    def _session_organization_for_append(
        self,
        session: Session,
        session_id: str,
        *,
        organization_id: str | None,
    ) -> str | None:
        session_record = session.get(AtlasSessionRecord, session_id)
        if session_record is None:
            raise KeyError(f"unknown atlas session: {session_id}")
        session_organization_id = session_record.organization_id
        if organization_id is not None and organization_id != session_organization_id:
            raise KeyError(f"unknown atlas session: {session_id}")
        return session_organization_id

    def get_agent_policy(self, agent_id: str, *, organization_id: str | None = None) -> AtlasAgentPolicy | None:
        with self._session_factory() as session:
            record = session.get(AtlasAgentPolicyRecord, agent_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            return _policy_from_record(record)

    def set_agent_policy(
        self,
        agent_id: str,
        *,
        organization_id: str | None,
        atlas_enabled: bool,
        updated_by_user_id: str | None,
    ) -> AtlasAgentPolicy:
        now = _utcnow()
        with self._session_factory.begin() as session:
            record = session.get(AtlasAgentPolicyRecord, agent_id)
            if record is None:
                record = AtlasAgentPolicyRecord(
                    agent_id=agent_id,
                    organization_id=organization_id,
                    atlas_enabled=atlas_enabled,
                    updated_by_user_id=updated_by_user_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
            else:
                record.organization_id = organization_id
                record.atlas_enabled = atlas_enabled
                record.updated_by_user_id = updated_by_user_id
                record.updated_at = now
        return self.get_agent_policy(agent_id, organization_id=organization_id)  # type: ignore[return-value]

    def create_session(self, session_model: AtlasSession) -> AtlasSession:
        with self._session_factory.begin() as session:
            session.add(
                AtlasSessionRecord(
                    session_id=session_model.session_id,
                    organization_id=session_model.organization_id,
                    scope=session_model.scope,
                    status=session_model.status,
                    agent_id=session_model.agent_id,
                    agent_version_id=session_model.agent_version_id,
                    title=session_model.title,
                    summary=session_model.summary,
                    created_by=session_model.created_by,
                    scenario_id=session_model.scenario_id,
                    step_id=session_model.step_id,
                    conversation_id=session_model.conversation_id,
                    trace_id=session_model.trace_id,
                    atlas_enabled_snapshot=session_model.atlas_enabled_snapshot,
                    archived_at=session_model.archived_at,
                    created_at=session_model.created_at,
                    updated_at=session_model.updated_at,
                )
            )
        return session_model

    def get_session(self, session_id: str, *, organization_id: str | None = None) -> AtlasSession | None:
        with self._session_factory() as session:
            record = session.get(AtlasSessionRecord, session_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            return _session_from_record(record)

    def list_sessions(
        self,
        *,
        organization_id: str | None = None,
        agent_id: str | None = None,
        scope: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AtlasSession], int, bool]:
        with self._session_factory() as session:
            filters = []
            if organization_id is not None:
                filters.append(AtlasSessionRecord.organization_id == organization_id)
            if agent_id is not None:
                filters.append(AtlasSessionRecord.agent_id == agent_id)
            if scope is not None:
                filters.append(AtlasSessionRecord.scope == scope)
            if status is not None:
                filters.append(AtlasSessionRecord.status == status)

            count_stmt = select(func.count()).select_from(AtlasSessionRecord)
            if filters:
                count_stmt = count_stmt.where(*filters)
            total_count = int(session.execute(count_stmt).scalar_one())

            stmt = select(AtlasSessionRecord)
            if filters:
                stmt = stmt.where(*filters)
            stmt = stmt.order_by(AtlasSessionRecord.updated_at.desc()).offset(offset).limit(limit)
            records = session.execute(stmt).scalars().all()
            items = [_session_from_record(record) for record in records]
            has_more = offset + len(items) < total_count
            return items, total_count, has_more

    def update_session(
        self,
        session_model: AtlasSession,
        *,
        organization_id: str | None = None,
        expected_updated_at: datetime | None = None,
    ) -> AtlasSession:
        with self._session_factory.begin() as session:
            record = session.get(AtlasSessionRecord, session_model.session_id)
            if record is None:
                raise KeyError(f"unknown atlas session: {session_model.session_id}")
            scoped_organization_id = organization_id if organization_id is not None else session_model.organization_id
            if scoped_organization_id is not None and record.organization_id != scoped_organization_id:
                raise KeyError(f"unknown atlas session: {session_model.session_id}")
            if expected_updated_at is not None and record.updated_at != expected_updated_at:
                raise ValueError("atlas session was updated by another request")
            record.status = session_model.status
            record.title = session_model.title
            record.summary = session_model.summary
            record.scenario_id = session_model.scenario_id
            record.step_id = session_model.step_id
            record.conversation_id = session_model.conversation_id
            record.trace_id = session_model.trace_id
            record.archived_at = session_model.archived_at
            record.updated_at = session_model.updated_at
        return session_model

    def update_session_status(
        self,
        session_id: str,
        status: str,
        *,
        organization_id: str | None = None,
        updated_at: datetime | None = None,
    ) -> AtlasSession:
        now = updated_at or _utcnow()
        with self._session_factory.begin() as session:
            record = session.get(AtlasSessionRecord, session_id)
            if record is None:
                raise KeyError(f"unknown atlas session: {session_id}")
            if organization_id is not None and record.organization_id != organization_id:
                raise KeyError(f"unknown atlas session: {session_id}")
            record.status = status
            record.updated_at = now
            session.flush()
            return _session_from_record(record)

    def archive_session(self, session_id: str, *, organization_id: str | None = None) -> AtlasSession | None:
        current = self.get_session(session_id, organization_id=organization_id)
        if current is None:
            return None
        archived = current.model_copy(update={"status": "archived", "archived_at": _utcnow(), "updated_at": _utcnow()})
        return self.update_session(
            archived,
            organization_id=organization_id,
            expected_updated_at=current.updated_at,
        )

    def append_message(self, message: AtlasMessage) -> AtlasMessage:
        with self._session_factory.begin() as session:
            organization_id = self._session_organization_for_append(
                session,
                message.session_id,
                organization_id=message.organization_id,
            )
            sequence_number = message.sequence_number
            if sequence_number <= 0:
                sequence_number = self._next_sequence(session, AtlasMessageRecord, "sequence_number", message.session_id)
            session.add(
                AtlasMessageRecord(
                    message_id=message.message_id,
                    session_id=message.session_id,
                    organization_id=organization_id,
                    sequence_number=sequence_number,
                    role=message.role,
                    content=message.content,
                    metadata_json=message.metadata,
                    created_at=message.created_at,
                )
            )
        return message.model_copy(update={"organization_id": organization_id, "sequence_number": sequence_number})

    def list_messages(
        self,
        session_id: str,
        *,
        organization_id: str | None = None,
        before_sequence: int | None = None,
        limit: int = 50,
    ) -> tuple[list[AtlasMessage], int, bool]:
        with self._session_factory() as session:
            filters = [AtlasMessageRecord.session_id == session_id]
            if organization_id is not None:
                filters.append(AtlasMessageRecord.organization_id == organization_id)
            total_count = session.execute(
                select(func.count(AtlasMessageRecord.message_id)).where(*filters)
            ).scalar_one()
            stmt = (
                select(AtlasMessageRecord)
                .where(*filters)
                .order_by(AtlasMessageRecord.sequence_number.desc())
                .limit(limit + 1)
            )
            if before_sequence is not None:
                stmt = stmt.where(AtlasMessageRecord.sequence_number < before_sequence)
            rows = session.execute(stmt).scalars().all()
            has_more = len(rows) > limit
            page_rows = list(reversed(rows[:limit]))
            return ([_message_from_record(row) for row in page_rows], int(total_count), has_more)

    def append_event(self, event: AtlasEvent) -> AtlasEvent:
        with self._session_factory.begin() as session:
            organization_id = self._session_organization_for_append(
                session,
                event.session_id,
                organization_id=event.organization_id,
            )
            sequence_number = event.sequence_number
            if sequence_number <= 0:
                sequence_number = self._next_sequence(session, AtlasEventRecord, "sequence_number", event.session_id)
            session.add(
                AtlasEventRecord(
                    event_id=event.event_id,
                    session_id=event.session_id,
                    organization_id=organization_id,
                    sequence_number=sequence_number,
                    event_type=event.type,
                    payload_json=event.payload,
                    created_at=event.created_at,
                )
            )
        return event.model_copy(update={"organization_id": organization_id, "sequence_number": sequence_number})

    def list_events(
        self,
        session_id: str,
        *,
        organization_id: str | None = None,
        after_sequence: int | None = None,
        limit: int = 100,
    ) -> tuple[list[AtlasEvent], int, bool]:
        with self._session_factory() as session:
            filters = [AtlasEventRecord.session_id == session_id]
            if organization_id is not None:
                filters.append(AtlasEventRecord.organization_id == organization_id)
            total_count = session.execute(
                select(func.count(AtlasEventRecord.event_id)).where(*filters)
            ).scalar_one()
            stmt = (
                select(AtlasEventRecord)
                .where(*filters)
                .order_by(AtlasEventRecord.sequence_number.asc())
                .limit(limit + 1)
            )
            if after_sequence is not None:
                stmt = stmt.where(AtlasEventRecord.sequence_number > after_sequence)
            rows = session.execute(stmt).scalars().all()
            has_more = len(rows) > limit
            page_rows = rows[:limit]
            return ([_event_from_record(row) for row in page_rows], int(total_count), has_more)

    def save_review_decisions(self, decisions: list[AtlasReviewDecisionRecord]) -> list[AtlasReviewDecisionRecord]:
        if not decisions:
            return []
        with self._session_factory.begin() as session:
            for item in decisions:
                session.add(
                    AtlasReviewDecisionORM(
                        review_decision_id=item.review_decision_id,
                        session_id=item.session_id,
                        organization_id=item.organization_id,
                        delta_id=item.delta_id,
                        decision=item.decision,
                        delta_payload_hash=item.delta_payload_hash,
                        note=item.note,
                        decided_by_user_id=item.decided_by_user_id,
                        created_at=item.created_at,
                    )
                )
        return decisions

    def list_review_decisions(
        self,
        session_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[AtlasReviewDecisionRecord]:
        with self._session_factory() as session:
            stmt = (
                select(AtlasReviewDecisionORM)
                .where(AtlasReviewDecisionORM.session_id == session_id)
                .order_by(AtlasReviewDecisionORM.created_at.asc())
            )
            if organization_id is not None:
                stmt = stmt.where(AtlasReviewDecisionORM.organization_id == organization_id)
            return [_review_decision_from_record(row) for row in session.execute(stmt).scalars().all()]

    def replace_proposed_changes(
        self,
        session_id: str,
        proposed_changes: AtlasProposedChanges,
        *,
        organization_id: str | None = None,
    ) -> AtlasProposedChanges:
        now = _utcnow()
        with self._session_factory.begin() as session:
            delete_stmt = delete(AtlasProposedDeltaRecord).where(AtlasProposedDeltaRecord.session_id == session_id)
            if organization_id is not None:
                delete_stmt = delete_stmt.where(AtlasProposedDeltaRecord.organization_id == organization_id)
            session.execute(delete_stmt)
            for attr, family, _ in _DELTA_FAMILY_SPECS:
                for item in getattr(proposed_changes, attr):
                    session.add(
                        AtlasProposedDeltaRecord(
                            delta_id=item.delta_id,
                            session_id=session_id,
                            organization_id=organization_id,
                            delta_family=family,
                            delta_json=item.model_dump(mode="json"),
                            created_at=now,
                            updated_at=now,
                        )
                    )
        return self.load_proposed_changes(session_id, organization_id=organization_id)

    def load_proposed_changes(
        self,
        session_id: str,
        *,
        organization_id: str | None = None,
    ) -> AtlasProposedChanges:
        payload = AtlasProposedChanges()
        with self._session_factory() as session:
            stmt = (
                select(AtlasProposedDeltaRecord)
                .where(AtlasProposedDeltaRecord.session_id == session_id)
                .order_by(AtlasProposedDeltaRecord.created_at.asc(), AtlasProposedDeltaRecord.delta_id.asc())
            )
            if organization_id is not None:
                stmt = stmt.where(AtlasProposedDeltaRecord.organization_id == organization_id)
            rows = session.execute(stmt).scalars().all()
            for row in rows:
                attr = _DELTA_FAMILY_TO_ATTR.get(row.delta_family)
                cls = _DELTA_FAMILY_TO_CLASS.get(row.delta_family)
                if attr is None or cls is None:
                    continue
                try:
                    parsed = cls.model_validate(dict(row.delta_json or {}))
                except ValidationError:
                    # AR-3.4: a single delta row whose persisted JSON no longer
                    # satisfies the current model (schema drift across a deploy)
                    # must not 500 the whole session — quarantine it and keep
                    # the rest of the review set readable.
                    logger.warning(
                        "atlas skipping unparseable proposed delta row",
                        extra={
                            "session_id": session_id,
                            "delta_id": getattr(row, "delta_id", None),
                            "delta_family": row.delta_family,
                        },
                    )
                    continue
                getattr(payload, attr).append(parsed)
        return payload

    @contextmanager
    def apply_lock(
        self,
        session_id: str,
        *,
        organization_id: str | None = None,
    ) -> Iterator[None]:
        lock_key = _advisory_lock_key(f"atlas-apply:{organization_id or ''}:{session_id}")
        session = self._session_factory()
        locked = False
        try:
            locked = bool(session.execute(select(func.pg_try_advisory_lock(lock_key))).scalar_one())
            if not locked:
                raise ValueError("another atlas apply is already in progress for this session")
            yield
        finally:
            if locked:
                session.execute(select(func.pg_advisory_unlock(lock_key)))
            session.close()

    def update_proposed_delta_statuses(
        self,
        session_id: str,
        statuses_by_delta_id: dict[str, str],
        *,
        organization_id: str | None = None,
    ) -> AtlasProposedChanges:
        if not statuses_by_delta_id:
            return self.load_proposed_changes(session_id, organization_id=organization_id)
        with self._session_factory.begin() as session:
            stmt = select(AtlasProposedDeltaRecord).where(
                AtlasProposedDeltaRecord.session_id == session_id,
                AtlasProposedDeltaRecord.delta_id.in_(list(statuses_by_delta_id)),
            )
            if organization_id is not None:
                stmt = stmt.where(AtlasProposedDeltaRecord.organization_id == organization_id)
            rows = session.execute(stmt).scalars().all()
            now = _utcnow()
            for row in rows:
                delta_json = dict(row.delta_json or {})
                delta_json["status"] = statuses_by_delta_id[row.delta_id]
                row.delta_json = delta_json
                row.updated_at = now
        return self.load_proposed_changes(session_id, organization_id=organization_id)

    def create_apply_request(self, request: AtlasApplyRequestRecordModel) -> AtlasApplyRequestRecordModel:
        with self._session_factory.begin() as session:
            session.add(
                AtlasApplyRequestRecord(
                    apply_request_id=request.apply_request_id,
                    session_id=request.session_id,
                    organization_id=request.organization_id,
                    status=request.status,
                    delta_ids_json=request.delta_ids,
                    apply_note=request.apply_note,
                    confirmed_by_user_id=request.confirmed_by_user_id,
                    error=request.error,
                    created_at=request.created_at,
                    updated_at=request.updated_at,
                )
            )
        return request

    def latest_apply_request(
        self,
        session_id: str,
        *,
        organization_id: str | None = None,
    ) -> AtlasApplyRequestRecordModel | None:
        with self._session_factory() as session:
            stmt = (
                select(AtlasApplyRequestRecord)
                .where(AtlasApplyRequestRecord.session_id == session_id)
                .order_by(AtlasApplyRequestRecord.created_at.desc())
                .limit(1)
            )
            if organization_id is not None:
                stmt = stmt.where(AtlasApplyRequestRecord.organization_id == organization_id)
            row = session.execute(stmt).scalar_one_or_none()
            return None if row is None else _apply_request_from_record(row)

    def create_permission_request(self, request: AtlasPermissionRequest) -> AtlasPermissionRequest:
        with self._session_factory.begin() as session:
            session.add(
                AtlasPermissionRequestRecord(
                    request_id=request.request_id,
                    session_id=request.session_id,
                    organization_id=request.organization_id,
                    kind=request.kind,
                    status=request.status,
                    reason=request.reason,
                    risk_summary=request.risk_summary,
                    scope_ref_json=request.scope_ref,
                    delta_ids_json=request.delta_ids,
                    requested_actions_json=request.requested_actions,
                    decision_reason=request.decision_reason,
                    decided_by_user_id=request.decided_by_user_id,
                    created_at=request.created_at,
                    expires_at=request.expires_at,
                    decided_at=request.decided_at,
                )
            )
        return request

    def list_permission_requests(
        self,
        session_id: str,
        *,
        organization_id: str | None = None,
        status: str | None = None,
    ) -> list[AtlasPermissionRequest]:
        with self._session_factory() as session:
            stmt = (
                select(AtlasPermissionRequestRecord)
                .where(AtlasPermissionRequestRecord.session_id == session_id)
                .order_by(AtlasPermissionRequestRecord.created_at.asc())
            )
            if organization_id is not None:
                stmt = stmt.where(AtlasPermissionRequestRecord.organization_id == organization_id)
            if status is not None:
                stmt = stmt.where(AtlasPermissionRequestRecord.status == status)
            # When callers ask for pending requests they want the set that's
            # actually blocking. Expired pending rows are stale and should not
            # gate apply or session transitions.
            if status == "pending":
                now = _utcnow()
                stmt = stmt.where(
                    (AtlasPermissionRequestRecord.expires_at.is_(None))
                    | (AtlasPermissionRequestRecord.expires_at > now)
                )
            return [_permission_request_from_record(row) for row in session.execute(stmt).scalars().all()]

    def find_approved_apply_permission(
        self,
        session_id: str,
        delta_ids: list[str],
        *,
        organization_id: str | None = None,
    ) -> AtlasPermissionRequest | None:
        requested_delta_ids = set(delta_ids)
        if not requested_delta_ids:
            return None
        now = _utcnow()
        with self._session_factory() as session:
            stmt = (
                select(AtlasPermissionRequestRecord)
                .where(
                    AtlasPermissionRequestRecord.session_id == session_id,
                    AtlasPermissionRequestRecord.kind == "apply_deltas",
                    AtlasPermissionRequestRecord.status == "approved",
                    (AtlasPermissionRequestRecord.expires_at.is_(None))
                    | (AtlasPermissionRequestRecord.expires_at > now),
                )
                .order_by(AtlasPermissionRequestRecord.decided_at.desc().nullslast())
            )
            if organization_id is not None:
                stmt = stmt.where(AtlasPermissionRequestRecord.organization_id == organization_id)
            rows = session.execute(stmt).scalars().all()
            for row in rows:
                if set(row.delta_ids_json or []) == requested_delta_ids:
                    return _permission_request_from_record(row)
        return None

    def apply_permission_decisions(
        self,
        session_id: str,
        decisions: list[dict[str, object]],
        *,
        organization_id: str | None = None,
        decided_by_user_id: str | None = None,
    ) -> list[AtlasPermissionRequest]:
        if not decisions:
            return []
        decision_by_id = {
            str(item["request_id"]): item
            for item in decisions
        }
        now = _utcnow()

        def _load_rows(session: Session) -> list[AtlasPermissionRequestRecord]:
            stmt = select(AtlasPermissionRequestRecord).where(
                AtlasPermissionRequestRecord.session_id == session_id,
                AtlasPermissionRequestRecord.request_id.in_(list(decision_by_id)),
            )
            if organization_id is not None:
                stmt = stmt.where(AtlasPermissionRequestRecord.organization_id == organization_id)
            return list(session.execute(stmt).scalars().all())

        # Phase 1 — validate, and durably record expiry. Marking a stale
        # pending request as "expired" happens in its own committed
        # transaction so the write survives even though we raise afterwards.
        # (The pending-request queries already exclude expired rows via
        # expires_at, so semantics stay consistent either way; persisting the
        # status keeps the audit trail truthful.)
        expired_ids: list[str] = []
        already_decided: list[tuple[str, str]] = []
        with self._session_factory.begin() as session:
            rows = _load_rows(session)
            found_ids = {row.request_id for row in rows}
            unknown_ids = [request_id for request_id in decision_by_id if request_id not in found_ids]
            for row in rows:
                if row.status == "pending" and row.expires_at is not None and row.expires_at <= now:
                    row.status = "expired"
                    expired_ids.append(row.request_id)
                elif row.status != "pending":
                    already_decided.append((row.request_id, row.status))
        if unknown_ids:
            raise ValueError(
                "unknown permission request id(s) for this session: " + ", ".join(sorted(unknown_ids))
            )
        if expired_ids:
            raise ValueError(
                "permission request '" + "', '".join(expired_ids) + "' has expired"
            )
        if already_decided:
            request_id, status = already_decided[0]
            raise ValueError(
                f"permission request '{request_id}' is already {status} and cannot be changed"
            )

        # Phase 2 — every requested id is a known, unexpired pending request:
        # apply the decisions atomically.
        with self._session_factory.begin() as session:
            rows = _load_rows(session)
            for row in rows:
                if row.status != "pending":
                    raise ValueError(
                        f"permission request '{row.request_id}' is already {row.status} and cannot be changed"
                    )
                decision = decision_by_id[row.request_id]
                row.status = str(decision["decision"])
                row.decision_reason = str(decision.get("reason") or "") or None
                row.decided_by_user_id = decided_by_user_id
                row.decided_at = now
            updated = [_permission_request_from_record(row) for row in rows]
        return updated


def new_atlas_session_id() -> str:
    return f"atlas_session_{uuid4().hex}"


def new_atlas_message_id() -> str:
    return f"atlas_message_{uuid4().hex}"


def new_atlas_event_id() -> str:
    return f"atlas_event_{uuid4().hex}"


def new_atlas_review_decision_id() -> str:
    return f"atlas_review_{uuid4().hex}"


def new_atlas_apply_request_id() -> str:
    return f"atlas_apply_{uuid4().hex}"


def new_atlas_permission_request_id() -> str:
    return f"atlas_perm_{uuid4().hex}"
