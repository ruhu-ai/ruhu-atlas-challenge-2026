from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ruhu.db_models import ToolInvocationRecord

from .types import ToolInvocation

if TYPE_CHECKING:
    from .pii import TieredPiiScanner


class ToolInvocationStore(Protocol):
    def load(self, invocation_id: str, *, organization_id: str | None = None) -> ToolInvocation | None: ...

    def save(self, invocation: ToolInvocation) -> None: ...

    def all(self, *, organization_id: str | None = None) -> list[ToolInvocation]: ...

    def by_conversation(self, conversation_id: str, *, organization_id: str | None = None) -> list[ToolInvocation]: ...


class InMemoryToolInvocationStore:
    def __init__(self) -> None:
        self._items: dict[str, ToolInvocation] = {}

    def load(self, invocation_id: str, *, organization_id: str | None = None) -> ToolInvocation | None:
        item = self._items.get(invocation_id)
        if item is None:
            return None
        if organization_id is not None and item.caller.tenant_id != organization_id:
            return None
        return item.model_copy(deep=True)

    def save(self, invocation: ToolInvocation) -> None:
        self._items[invocation.invocation_id] = invocation.model_copy(deep=True)

    def all(self, *, organization_id: str | None = None) -> list[ToolInvocation]:
        return [
            deepcopy(item)
            for _, item in sorted(self._items.items())
            if organization_id is None or item.caller.tenant_id == organization_id
        ]

    def by_conversation(self, conversation_id: str, *, organization_id: str | None = None) -> list[ToolInvocation]:
        return [
            deepcopy(item)
            for item in self._items.values()
            if item.caller.conversation_id == conversation_id
            and (organization_id is None or item.caller.tenant_id == organization_id)
        ]


class SQLAlchemyToolInvocationStore:
    def __init__(self, session_factory: sessionmaker[Session], *, pii_scanner: Any | None = None) -> None:
        self._session_factory = session_factory
        self._pii_scanner: TieredPiiScanner | None = pii_scanner

    def load(self, invocation_id: str, *, organization_id: str | None = None) -> ToolInvocation | None:
        with self._session_factory() as session:
            record = session.get(ToolInvocationRecord, invocation_id)
            if record is None:
                return None
            if organization_id is not None and record.organization_id != organization_id:
                return None
            return _record_to_invocation(record)

    def save(self, invocation: ToolInvocation) -> None:
        with self._session_factory() as session:
            # Scan and redact args + output if PII scanner is configured
            args = dict(invocation.args)
            output = dict(invocation.output)
            if self._pii_scanner is not None and invocation.status == "completed":
                try:
                    args_scan = self._pii_scanner.scan_and_redact_dict(
                        args,
                        context={
                            "field_context": "tool_invocation_args",
                            "tool_ref": invocation.tool_ref,
                            "organization_id": invocation.caller.tenant_id,
                        },
                    )
                    args = args_scan.redacted_dict or args
                except Exception:
                    pass  # Fail open: use original if scanning fails

                try:
                    output_scan = self._pii_scanner.scan_and_redact_dict(
                        output,
                        context={
                            "field_context": "tool_invocation_output",
                            "tool_ref": invocation.tool_ref,
                            "organization_id": invocation.caller.tenant_id,
                        },
                    )
                    output = output_scan.redacted_dict or output
                except Exception:
                    pass  # Fail open: use original if scanning fails

            record = session.get(ToolInvocationRecord, invocation.invocation_id)
            if record is None:
                session.add(_invocation_to_record(invocation, args=args, output=output))
            else:
                record.organization_id = invocation.caller.tenant_id
                record.tool_ref = invocation.tool_ref
                record.executor_kind = invocation.executor_kind
                record.status = invocation.status
                record.caller_json = invocation.caller.model_dump(mode="json")
                record.args_json = args
                record.dedupe_key = invocation.dedupe_key
                record.decision = invocation.decision
                record.decision_reason = invocation.decision_reason
                record.output_json = output
                record.error = invocation.error
                record.latency_ms = invocation.latency_ms
                record.metadata_json = dict(invocation.metadata)
                record.created_at = invocation.created_at
                record.updated_at = invocation.updated_at
                record.expires_at = invocation.expires_at
            session.commit()

    def all(self, *, organization_id: str | None = None) -> list[ToolInvocation]:
        statement = select(ToolInvocationRecord).order_by(ToolInvocationRecord.created_at.asc())
        if organization_id is not None:
            statement = statement.where(ToolInvocationRecord.organization_id == organization_id)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_invocation(record) for record in records]

    def by_conversation(self, conversation_id: str, *, organization_id: str | None = None) -> list[ToolInvocation]:
        statement = (
            select(ToolInvocationRecord)
            .where(ToolInvocationRecord.caller_json["conversation_id"].as_string() == conversation_id)
            .order_by(ToolInvocationRecord.created_at.asc())
        )
        if organization_id is not None:
            statement = statement.where(ToolInvocationRecord.organization_id == organization_id)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_invocation(record) for record in records]


def _invocation_to_record(
    invocation: ToolInvocation,
    *,
    args: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
) -> ToolInvocationRecord:
    return ToolInvocationRecord(
        invocation_id=invocation.invocation_id,
        organization_id=invocation.caller.tenant_id,
        tool_ref=invocation.tool_ref,
        executor_kind=invocation.executor_kind,
        status=invocation.status,
        caller_json=invocation.caller.model_dump(mode="json"),
        args_json=args if args is not None else dict(invocation.args),
        dedupe_key=invocation.dedupe_key,
        decision=invocation.decision,
        decision_reason=invocation.decision_reason,
        output_json=output if output is not None else dict(invocation.output),
        error=invocation.error,
        latency_ms=invocation.latency_ms,
        metadata_json=dict(invocation.metadata),
        created_at=invocation.created_at,
        updated_at=invocation.updated_at,
        expires_at=invocation.expires_at,
    )


def _record_to_invocation(record: ToolInvocationRecord) -> ToolInvocation:
    return ToolInvocation.model_validate(
        {
            "invocation_id": record.invocation_id,
            "tool_ref": record.tool_ref,
            "executor_kind": record.executor_kind,
            "status": record.status,
            "caller": dict(record.caller_json or {}),
            "args": dict(record.args_json or {}),
            "dedupe_key": record.dedupe_key,
            "decision": record.decision,
            "decision_reason": record.decision_reason,
            "output": dict(record.output_json or {}),
            "error": record.error,
            "latency_ms": record.latency_ms,
            "metadata": dict(record.metadata_json or {}),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "expires_at": record.expires_at,
        }
    )
