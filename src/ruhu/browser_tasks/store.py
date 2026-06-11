from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from .models import BrowserApproval, BrowserOperatorCommand, BrowserTask, BrowserTaskEvent, new_id, utc_now
from .sqlalchemy_models import (
    BrowserApprovalRecord,
    BrowserOperatorCommandRecord,
    BrowserTaskEventRecord,
    BrowserTaskPackAccessRecord,
    BrowserTaskRecord,
)


class BrowserTaskStore(Protocol):
    def save_task(self, task: BrowserTask) -> BrowserTask: ...

    def get_task(self, task_id: str, *, organization_id: str | None = None) -> BrowserTask | None: ...

    def list_tasks(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[BrowserTask]: ...

    def list_recent_tasks(
        self,
        *,
        organization_id: str | None = None,
        conversation_id: str | None = None,
        state: str | None = None,
        approval_state: str | None = None,
        limit: int = 50,
    ) -> list[BrowserTask]: ...

    def claim_next_queued_task(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        organization_id: str | None = None,
        now: datetime,
    ) -> BrowserTask | None: ...

    def renew_task_lease(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_seconds: int,
        organization_id: str | None = None,
        now: datetime,
    ) -> BrowserTask | None: ...

    def release_task_lease(
        self,
        task_id: str,
        *,
        worker_id: str,
        organization_id: str | None = None,
        now: datetime,
    ) -> BrowserTask | None: ...

    def save_approval(self, approval: BrowserApproval) -> BrowserApproval: ...

    def get_approval(
        self,
        approval_id: str,
        *,
        organization_id: str | None = None,
    ) -> BrowserApproval | None: ...

    def get_task_approval(self, task_id: str, *, organization_id: str | None = None) -> BrowserApproval | None: ...

    def list_expired_pending_approvals(
        self,
        *,
        now: datetime,
        organization_id: str | None = None,
        limit: int = 100,
    ) -> list[BrowserApproval]: ...

    def save_event(self, event: BrowserTaskEvent) -> BrowserTaskEvent: ...

    def list_events(self, task_id: str, *, organization_id: str | None = None) -> list[BrowserTaskEvent]: ...

    def save_operator_command(self, command: BrowserOperatorCommand) -> BrowserOperatorCommand: ...

    def get_operator_command(
        self,
        command_id: str,
        *,
        organization_id: str | None = None,
    ) -> BrowserOperatorCommand | None: ...

    def list_operator_commands(
        self,
        task_id: str,
        *,
        organization_id: str | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> list[BrowserOperatorCommand]: ...

    def list_allowed_task_pack_ids(
        self,
        *,
        organization_id: str | None = None,
        agent_id: str | None = None,
    ) -> set[str] | None: ...

    def replace_allowed_task_pack_ids(
        self,
        *,
        organization_id: str | None = None,
        agent_id: str | None = None,
        pack_ids: set[str] | None,
    ) -> set[str] | None: ...


class InMemoryBrowserTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, BrowserTask] = {}
        self._task_ids_by_conversation: dict[str, list[str]] = {}
        self._approvals: dict[str, BrowserApproval] = {}
        self._approval_id_by_task: dict[str, str] = {}
        self._events: dict[str, list[BrowserTaskEvent]] = {}
        self._operator_commands: dict[str, BrowserOperatorCommand] = {}
        self._allowed_pack_ids: dict[tuple[str | None, str | None], set[str]] = {}

    def save_task(self, task: BrowserTask) -> BrowserTask:
        stored = task.model_copy(deep=True)
        existing = self._tasks.get(stored.task_id)
        self._tasks[stored.task_id] = stored
        if existing is None:
            self._task_ids_by_conversation.setdefault(stored.conversation_id, []).append(stored.task_id)
        return stored.model_copy(deep=True)

    def get_task(self, task_id: str, *, organization_id: str | None = None) -> BrowserTask | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        if organization_id is not None and task.organization_id != organization_id:
            return None
        return task.model_copy(deep=True)

    def list_tasks(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[BrowserTask]:
        result: list[BrowserTask] = []
        for task_id in self._task_ids_by_conversation.get(conversation_id, []):
            task = self._tasks[task_id]
            if organization_id is not None and task.organization_id != organization_id:
                continue
            result.append(task.model_copy(deep=True))
        result.sort(key=lambda item: (item.created_at, item.task_id))
        return result

    def list_recent_tasks(
        self,
        *,
        organization_id: str | None = None,
        conversation_id: str | None = None,
        state: str | None = None,
        approval_state: str | None = None,
        limit: int = 50,
    ) -> list[BrowserTask]:
        result: list[BrowserTask] = []
        for task in self._tasks.values():
            if organization_id is not None and task.organization_id != organization_id:
                continue
            if conversation_id is not None and task.conversation_id != conversation_id:
                continue
            if state is not None and task.state != state:
                continue
            if approval_state is not None and task.approval_state != approval_state:
                continue
            result.append(task.model_copy(deep=True))
        result.sort(key=lambda item: (item.created_at, item.task_id), reverse=True)
        return result[: max(1, min(limit, 200))]

    def claim_next_queued_task(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        organization_id: str | None = None,
        now: datetime,
    ) -> BrowserTask | None:
        expires_at = now + timedelta(seconds=lease_seconds)
        candidates = sorted(self._tasks.values(), key=lambda item: (item.created_at, item.task_id))
        for task in candidates:
            if organization_id is not None and task.organization_id != organization_id:
                continue
            if task.state not in {"queued", "running"}:
                continue
            if task.state == "running" and (
                task.lease_expires_at is None or task.lease_expires_at > now
            ):
                continue
            if task.state == "queued" and task.lease_expires_at is not None and task.lease_expires_at > now:
                continue
            claimed = task.model_copy(
                update={
                    "state": "running",
                    "lease_owner": worker_id,
                    "lease_expires_at": expires_at,
                    "attempt_count": task.attempt_count + 1,
                    "started_at": task.started_at or now,
                    "updated_at": now,
                }
            )
            self._tasks[claimed.task_id] = claimed
            return claimed.model_copy(deep=True)
        return None

    def renew_task_lease(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_seconds: int,
        organization_id: str | None = None,
        now: datetime,
    ) -> BrowserTask | None:
        task = self.get_task(task_id, organization_id=organization_id)
        if task is None or task.state != "running" or task.lease_owner != worker_id:
            return None
        renewed = task.model_copy(
            update={
                "lease_expires_at": now + timedelta(seconds=lease_seconds),
                "updated_at": now,
            }
        )
        self._tasks[task_id] = renewed
        return renewed.model_copy(deep=True)

    def release_task_lease(
        self,
        task_id: str,
        *,
        worker_id: str,
        organization_id: str | None = None,
        now: datetime,
    ) -> BrowserTask | None:
        task = self.get_task(task_id, organization_id=organization_id)
        if task is None or task.state != "running" or task.lease_owner != worker_id:
            return None
        released = task.model_copy(
            update={
                "state": "queued",
                "lease_owner": None,
                "lease_expires_at": None,
                "updated_at": now,
            }
        )
        self._tasks[task_id] = released
        return released.model_copy(deep=True)

    def save_approval(self, approval: BrowserApproval) -> BrowserApproval:
        stored = approval.model_copy(deep=True)
        self._approvals[stored.approval_id] = stored
        self._approval_id_by_task[stored.task_id] = stored.approval_id
        return stored.model_copy(deep=True)

    def get_approval(self, approval_id: str, *, organization_id: str | None = None) -> BrowserApproval | None:
        approval = self._approvals.get(approval_id)
        if approval is None:
            return None
        if organization_id is not None and approval.organization_id != organization_id:
            return None
        return approval.model_copy(deep=True)

    def get_task_approval(self, task_id: str, *, organization_id: str | None = None) -> BrowserApproval | None:
        approval_id = self._approval_id_by_task.get(task_id)
        if approval_id is None:
            return None
        return self.get_approval(approval_id, organization_id=organization_id)

    def list_expired_pending_approvals(
        self,
        *,
        now: datetime,
        organization_id: str | None = None,
        limit: int = 100,
    ) -> list[BrowserApproval]:
        result: list[BrowserApproval] = []
        for approval in self._approvals.values():
            if organization_id is not None and approval.organization_id != organization_id:
                continue
            if approval.state != "pending" or approval.expires_at is None or approval.expires_at > now:
                continue
            result.append(approval.model_copy(deep=True))
        result.sort(key=lambda item: (item.expires_at or item.requested_at, item.approval_id))
        return result[: max(1, min(limit, 500))]

    def save_event(self, event: BrowserTaskEvent) -> BrowserTaskEvent:
        stored = event.model_copy(deep=True)
        existing = next((item for item in self._events.get(stored.task_id, []) if item.event_id == stored.event_id), None)
        if existing is not None:
            return existing.model_copy(deep=True)
        if stored.event_sequence <= 0:
            latest = max((item.event_sequence for item in self._events.get(stored.task_id, [])), default=0)
            stored = stored.model_copy(update={"event_sequence": latest + 1})
        self._events.setdefault(stored.task_id, []).append(stored)
        return stored.model_copy(deep=True)

    def list_events(self, task_id: str, *, organization_id: str | None = None) -> list[BrowserTaskEvent]:
        result = self._events.get(task_id, [])
        if organization_id is not None:
            result = [event for event in result if event.organization_id == organization_id]
        return [event.model_copy(deep=True) for event in sorted(result, key=lambda item: (item.event_sequence, item.created_at, item.event_id))]

    def save_operator_command(self, command: BrowserOperatorCommand) -> BrowserOperatorCommand:
        stored = command.model_copy(deep=True)
        self._operator_commands[stored.command_id] = stored
        return stored.model_copy(deep=True)

    def get_operator_command(
        self,
        command_id: str,
        *,
        organization_id: str | None = None,
    ) -> BrowserOperatorCommand | None:
        command = self._operator_commands.get(command_id)
        if command is None:
            return None
        if organization_id is not None and command.organization_id != organization_id:
            return None
        return command.model_copy(deep=True)

    def list_operator_commands(
        self,
        task_id: str,
        *,
        organization_id: str | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> list[BrowserOperatorCommand]:
        result = []
        for command in self._operator_commands.values():
            if command.task_id != task_id:
                continue
            if organization_id is not None and command.organization_id != organization_id:
                continue
            if state is not None and command.state != state:
                continue
            result.append(command.model_copy(deep=True))
        result.sort(key=lambda item: (item.created_at, item.command_id))
        return result[: max(1, min(limit, 200))]

    def list_allowed_task_pack_ids(
        self,
        *,
        organization_id: str | None = None,
        agent_id: str | None = None,
    ) -> set[str] | None:
        value = self._allowed_pack_ids.get((organization_id, agent_id))
        return None if value is None else set(value)

    def replace_allowed_task_pack_ids(
        self,
        *,
        organization_id: str | None = None,
        agent_id: str | None = None,
        pack_ids: set[str] | None,
    ) -> set[str] | None:
        key = (organization_id, agent_id)
        if pack_ids is None:
            self._allowed_pack_ids.pop(key, None)
            return None
        self._allowed_pack_ids[key] = set(pack_ids)
        return set(pack_ids)


class SQLAlchemyBrowserTaskStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save_task(self, task: BrowserTask) -> BrowserTask:
        with self._session_factory() as session:
            record = session.get(BrowserTaskRecord, task.task_id)
            if record is None:
                session.add(_task_to_record(task))
            else:
                record.organization_id = task.organization_id
                record.agent_id = task.agent_id
                record.conversation_id = task.conversation_id
                record.title = task.title
                record.summary = task.summary
                record.requested_channel = task.requested_channel
                record.task_pack_id = task.task_pack_id
                record.task_pack_version = task.task_pack_version
                record.start_url = task.start_url
                record.input_json = dict(task.input_payload)
                record.credential_refs_json = dict(task.credential_refs)
                record.state = task.state
                record.approval_state = task.approval_state
                record.current_approval_id = task.current_approval_id
                record.lease_owner = task.lease_owner
                record.lease_expires_at = task.lease_expires_at
                record.operator_takeover_owner_id = task.operator_takeover_owner_id
                record.operator_takeover_expires_at = task.operator_takeover_expires_at
                record.attempt_count = task.attempt_count
                record.metadata_json = dict(task.metadata)
                record.result_json = dict(task.result)
                record.error = task.error
                record.created_at = task.created_at
                record.updated_at = task.updated_at
                record.started_at = task.started_at
                record.finished_at = task.finished_at
            session.commit()
        return task.model_copy(deep=True)

    def get_task(self, task_id: str, *, organization_id: str | None = None) -> BrowserTask | None:
        with self._session_factory() as session:
            record = session.get(BrowserTaskRecord, task_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            return _record_to_task(record)

    def list_tasks(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[BrowserTask]:
        statement = (
            select(BrowserTaskRecord)
            .where(BrowserTaskRecord.conversation_id == conversation_id)
            .order_by(BrowserTaskRecord.created_at.asc())
        )
        if organization_id is not None:
            statement = statement.where(BrowserTaskRecord.organization_id == organization_id)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_task(record) for record in records]

    def list_recent_tasks(
        self,
        *,
        organization_id: str | None = None,
        conversation_id: str | None = None,
        state: str | None = None,
        approval_state: str | None = None,
        limit: int = 50,
    ) -> list[BrowserTask]:
        statement = select(BrowserTaskRecord).order_by(
            BrowserTaskRecord.created_at.desc(),
            BrowserTaskRecord.task_id.desc(),
        )
        if organization_id is not None:
            statement = statement.where(BrowserTaskRecord.organization_id == organization_id)
        if conversation_id is not None:
            statement = statement.where(BrowserTaskRecord.conversation_id == conversation_id)
        if state is not None:
            statement = statement.where(BrowserTaskRecord.state == state)
        if approval_state is not None:
            statement = statement.where(BrowserTaskRecord.approval_state == approval_state)
        statement = statement.limit(max(1, min(limit, 200)))
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_task(record) for record in records]

    def claim_next_queued_task(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        organization_id: str | None = None,
        now: datetime,
    ) -> BrowserTask | None:
        expires_at = now + timedelta(seconds=lease_seconds)
        statement = (
            select(BrowserTaskRecord)
            .where(BrowserTaskRecord.state.in_(["queued", "running"]))
            .where(
                or_(
                    BrowserTaskRecord.state == "queued",
                    BrowserTaskRecord.lease_expires_at.is_(None),
                    BrowserTaskRecord.lease_expires_at <= now,
                )
            )
            .order_by(BrowserTaskRecord.created_at.asc(), BrowserTaskRecord.task_id.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if organization_id is not None:
            statement = statement.where(BrowserTaskRecord.organization_id == organization_id)
        with self._session_factory() as session:
            record = session.execute(statement).scalar_one_or_none()
            if record is None:
                return None
            record.state = "running"
            record.lease_owner = worker_id
            record.lease_expires_at = expires_at
            record.attempt_count = int(record.attempt_count or 0) + 1
            record.started_at = record.started_at or now
            record.updated_at = now
            task = _record_to_task(record)
            session.commit()
        return task

    def renew_task_lease(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_seconds: int,
        organization_id: str | None = None,
        now: datetime,
    ) -> BrowserTask | None:
        with self._session_factory() as session:
            record = session.get(BrowserTaskRecord, task_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            if record.state != "running" or record.lease_owner != worker_id:
                return None
            record.lease_expires_at = now + timedelta(seconds=lease_seconds)
            record.updated_at = now
            task = _record_to_task(record)
            session.commit()
        return task

    def release_task_lease(
        self,
        task_id: str,
        *,
        worker_id: str,
        organization_id: str | None = None,
        now: datetime,
    ) -> BrowserTask | None:
        with self._session_factory() as session:
            record = session.get(BrowserTaskRecord, task_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            if record.state != "running" or record.lease_owner != worker_id:
                return None
            record.state = "queued"
            record.lease_owner = None
            record.lease_expires_at = None
            record.updated_at = now
            task = _record_to_task(record)
            session.commit()
        return task

    def save_approval(self, approval: BrowserApproval) -> BrowserApproval:
        with self._session_factory() as session:
            record = session.get(BrowserApprovalRecord, approval.approval_id)
            if record is None:
                session.add(_approval_to_record(approval))
            else:
                record.organization_id = approval.organization_id
                record.task_id = approval.task_id
                record.conversation_id = approval.conversation_id
                record.kind = approval.kind
                record.state = approval.state
                record.prompt = approval.prompt
                record.context_json = dict(approval.context)
                record.decision_reason = approval.decision_reason
                record.requested_at = approval.requested_at
                record.expires_at = approval.expires_at
                record.decided_at = approval.decided_at
            session.commit()
        return approval.model_copy(deep=True)

    def get_approval(self, approval_id: str, *, organization_id: str | None = None) -> BrowserApproval | None:
        with self._session_factory() as session:
            record = session.get(BrowserApprovalRecord, approval_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            return _record_to_approval(record)

    def get_task_approval(self, task_id: str, *, organization_id: str | None = None) -> BrowserApproval | None:
        statement = select(BrowserApprovalRecord).where(BrowserApprovalRecord.task_id == task_id)
        if organization_id is not None:
            statement = statement.where(BrowserApprovalRecord.organization_id == organization_id)
        with self._session_factory() as session:
            record = session.execute(statement).scalar_one_or_none()
        return None if record is None else _record_to_approval(record)

    def list_expired_pending_approvals(
        self,
        *,
        now: datetime,
        organization_id: str | None = None,
        limit: int = 100,
    ) -> list[BrowserApproval]:
        statement = (
            select(BrowserApprovalRecord)
            .where(BrowserApprovalRecord.state == "pending")
            .where(BrowserApprovalRecord.expires_at.is_not(None))
            .where(BrowserApprovalRecord.expires_at <= now)
            .order_by(BrowserApprovalRecord.expires_at.asc(), BrowserApprovalRecord.approval_id.asc())
            .limit(max(1, min(limit, 500)))
        )
        if organization_id is not None:
            statement = statement.where(BrowserApprovalRecord.organization_id == organization_id)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_approval(record) for record in records]

    def save_event(self, event: BrowserTaskEvent) -> BrowserTaskEvent:
        with self._session_factory() as session:
            record = session.get(BrowserTaskEventRecord, event.event_id)
            if record is None:
                event_to_save = event
                if event_to_save.event_sequence <= 0:
                    latest = session.execute(
                        select(func.max(BrowserTaskEventRecord.event_sequence)).where(
                            BrowserTaskEventRecord.task_id == event.task_id
                        )
                    ).scalar_one()
                    event_to_save = event_to_save.model_copy(update={"event_sequence": int(latest or 0) + 1})
                session.add(_event_to_record(event_to_save))
            else:
                event_to_save = _record_to_event(record)
                record.organization_id = event.organization_id
                record.task_id = event.task_id
                record.conversation_id = event.conversation_id
                record.event_sequence = event.event_sequence or record.event_sequence
                record.event_type = event.event_type
                record.message = event.message
                record.metadata_json = dict(event.metadata)
                record.created_at = event.created_at
            session.commit()
        return event_to_save.model_copy(deep=True)

    def list_events(self, task_id: str, *, organization_id: str | None = None) -> list[BrowserTaskEvent]:
        statement = (
            select(BrowserTaskEventRecord)
            .where(BrowserTaskEventRecord.task_id == task_id)
            .order_by(BrowserTaskEventRecord.event_sequence.asc(), BrowserTaskEventRecord.created_at.asc())
        )
        if organization_id is not None:
            statement = statement.where(BrowserTaskEventRecord.organization_id == organization_id)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_event(record) for record in records]

    def save_operator_command(self, command: BrowserOperatorCommand) -> BrowserOperatorCommand:
        with self._session_factory() as session:
            record = session.get(BrowserOperatorCommandRecord, command.command_id)
            if record is None:
                session.add(_operator_command_to_record(command))
            else:
                record.organization_id = command.organization_id
                record.task_id = command.task_id
                record.conversation_id = command.conversation_id
                record.operator_id = command.operator_id
                record.command_type = command.command_type
                record.payload_json = dict(command.payload)
                record.state = command.state
                record.created_at = command.created_at
                record.delivered_at = command.delivered_at
                record.error = command.error
            session.commit()
        return command.model_copy(deep=True)

    def get_operator_command(
        self,
        command_id: str,
        *,
        organization_id: str | None = None,
    ) -> BrowserOperatorCommand | None:
        with self._session_factory() as session:
            record = session.get(BrowserOperatorCommandRecord, command_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            return _record_to_operator_command(record)

    def list_operator_commands(
        self,
        task_id: str,
        *,
        organization_id: str | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> list[BrowserOperatorCommand]:
        statement = (
            select(BrowserOperatorCommandRecord)
            .where(BrowserOperatorCommandRecord.task_id == task_id)
            .order_by(BrowserOperatorCommandRecord.created_at.asc(), BrowserOperatorCommandRecord.command_id.asc())
            .limit(max(1, min(limit, 200)))
        )
        if organization_id is not None:
            statement = statement.where(BrowserOperatorCommandRecord.organization_id == organization_id)
        if state is not None:
            statement = statement.where(BrowserOperatorCommandRecord.state == state)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_operator_command(record) for record in records]

    def list_allowed_task_pack_ids(
        self,
        *,
        organization_id: str | None = None,
        agent_id: str | None = None,
    ) -> set[str] | None:
        statement = select(BrowserTaskPackAccessRecord).where(
            BrowserTaskPackAccessRecord.organization_id.is_(organization_id)
            if organization_id is None
            else BrowserTaskPackAccessRecord.organization_id == organization_id
        )
        if agent_id is None:
            statement = statement.where(BrowserTaskPackAccessRecord.agent_id.is_(None))
        else:
            statement = statement.where(BrowserTaskPackAccessRecord.agent_id == agent_id)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        if not records:
            return None
        return {record.pack_id for record in records}

    def replace_allowed_task_pack_ids(
        self,
        *,
        organization_id: str | None = None,
        agent_id: str | None = None,
        pack_ids: set[str] | None,
    ) -> set[str] | None:
        now = utc_now()
        select_existing = select(BrowserTaskPackAccessRecord).where(
            BrowserTaskPackAccessRecord.organization_id.is_(organization_id)
            if organization_id is None
            else BrowserTaskPackAccessRecord.organization_id == organization_id
        )
        if agent_id is None:
            select_existing = select_existing.where(BrowserTaskPackAccessRecord.agent_id.is_(None))
        else:
            select_existing = select_existing.where(BrowserTaskPackAccessRecord.agent_id == agent_id)
        with self._session_factory() as session:
            for record in session.execute(select_existing).scalars().all():
                session.delete(record)
            if pack_ids is not None:
                for pack_id in sorted(pack_ids):
                    session.add(
                        BrowserTaskPackAccessRecord(
                            access_id=new_id("btpa"),
                            organization_id=organization_id,
                            agent_id=agent_id,
                            pack_id=pack_id,
                            created_at=now,
                            updated_at=now,
                        )
                    )
            session.commit()
        return None if pack_ids is None else set(pack_ids)


def _task_to_record(task: BrowserTask) -> BrowserTaskRecord:
    return BrowserTaskRecord(
        task_id=task.task_id,
        organization_id=task.organization_id,
        agent_id=task.agent_id,
        conversation_id=task.conversation_id,
        title=task.title,
        summary=task.summary,
        requested_channel=task.requested_channel,
        task_pack_id=task.task_pack_id,
        task_pack_version=task.task_pack_version,
        start_url=task.start_url,
        input_json=dict(task.input_payload),
        credential_refs_json=dict(task.credential_refs),
        state=task.state,
        approval_state=task.approval_state,
        current_approval_id=task.current_approval_id,
        lease_owner=task.lease_owner,
        lease_expires_at=task.lease_expires_at,
        operator_takeover_owner_id=task.operator_takeover_owner_id,
        operator_takeover_expires_at=task.operator_takeover_expires_at,
        attempt_count=task.attempt_count,
        metadata_json=dict(task.metadata),
        result_json=dict(task.result),
        error=task.error,
        created_at=task.created_at,
        updated_at=task.updated_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
    )


def _record_to_task(record: BrowserTaskRecord) -> BrowserTask:
    return BrowserTask.model_validate(
        {
            "task_id": record.task_id,
            "organization_id": record.organization_id,
            "agent_id": record.agent_id,
            "conversation_id": record.conversation_id,
            "title": record.title,
            "summary": record.summary,
            "requested_channel": record.requested_channel,
            "task_pack_id": record.task_pack_id,
            "task_pack_version": record.task_pack_version,
            "start_url": record.start_url,
            "input_payload": dict(record.input_json or {}),
            "credential_refs": dict(record.credential_refs_json or {}),
            "state": record.state,
            "approval_state": record.approval_state,
            "current_approval_id": record.current_approval_id,
            "lease_owner": record.lease_owner,
            "lease_expires_at": record.lease_expires_at,
            "operator_takeover_owner_id": record.operator_takeover_owner_id,
            "operator_takeover_expires_at": record.operator_takeover_expires_at,
            "attempt_count": record.attempt_count,
            "metadata": dict(record.metadata_json or {}),
            "result": dict(record.result_json or {}),
            "error": record.error,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
        }
    )


def _approval_to_record(approval: BrowserApproval) -> BrowserApprovalRecord:
    return BrowserApprovalRecord(
        approval_id=approval.approval_id,
        task_id=approval.task_id,
        organization_id=approval.organization_id,
        conversation_id=approval.conversation_id,
        kind=approval.kind,
        state=approval.state,
        prompt=approval.prompt,
        context_json=dict(approval.context),
        decision_reason=approval.decision_reason,
        requested_at=approval.requested_at,
        expires_at=approval.expires_at,
        decided_at=approval.decided_at,
    )


def _record_to_approval(record: BrowserApprovalRecord) -> BrowserApproval:
    return BrowserApproval.model_validate(
        {
            "approval_id": record.approval_id,
            "task_id": record.task_id,
            "organization_id": record.organization_id,
            "conversation_id": record.conversation_id,
            "kind": record.kind,
            "state": record.state,
            "prompt": record.prompt,
            "context": dict(record.context_json or {}),
            "decision_reason": record.decision_reason,
            "requested_at": record.requested_at,
            "expires_at": record.expires_at,
            "decided_at": record.decided_at,
        }
    )


def _event_to_record(event: BrowserTaskEvent) -> BrowserTaskEventRecord:
    return BrowserTaskEventRecord(
        event_id=event.event_id,
        task_id=event.task_id,
        organization_id=event.organization_id,
        conversation_id=event.conversation_id,
        event_sequence=event.event_sequence,
        event_type=event.event_type,
        message=event.message,
        metadata_json=dict(event.metadata),
        created_at=event.created_at,
    )


def _operator_command_to_record(command: BrowserOperatorCommand) -> BrowserOperatorCommandRecord:
    return BrowserOperatorCommandRecord(
        command_id=command.command_id,
        task_id=command.task_id,
        organization_id=command.organization_id,
        conversation_id=command.conversation_id,
        operator_id=command.operator_id,
        command_type=command.command_type,
        payload_json=dict(command.payload),
        state=command.state,
        created_at=command.created_at,
        delivered_at=command.delivered_at,
        error=command.error,
    )


def _record_to_operator_command(record: BrowserOperatorCommandRecord) -> BrowserOperatorCommand:
    return BrowserOperatorCommand.model_validate(
        {
            "command_id": record.command_id,
            "task_id": record.task_id,
            "organization_id": record.organization_id,
            "conversation_id": record.conversation_id,
            "operator_id": record.operator_id,
            "command_type": record.command_type,
            "payload": dict(record.payload_json or {}),
            "state": record.state,
            "created_at": record.created_at,
            "delivered_at": record.delivered_at,
            "error": record.error,
        }
    )


def _record_to_event(record: BrowserTaskEventRecord) -> BrowserTaskEvent:
    return BrowserTaskEvent.model_validate(
        {
            "event_id": record.event_id,
            "task_id": record.task_id,
            "organization_id": record.organization_id,
            "conversation_id": record.conversation_id,
            "event_sequence": record.event_sequence,
            "event_type": record.event_type,
            "message": record.message,
            "metadata": dict(record.metadata_json or {}),
            "created_at": record.created_at,
        }
    )
