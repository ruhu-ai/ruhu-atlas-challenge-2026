from __future__ import annotations

import json
import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .db_models import ConversationRecord, ConversationTurnRecord, TurnTraceRecord
from .schemas import (
    ClassifierTraceRecord,
    ConversationControlState,
    ConversationState,
    SemanticEventRecord,
    TurnLogEntry,
    TurnTrace,
)

if TYPE_CHECKING:
    from .tools.pii import TieredPiiScanner

_TRACE_PAYLOAD_HARD_CAP_BYTES = 256 * 1024
_TRACE_EXTENSION_KEY = "__trace_extensions__"
_FACT_METADATA_KEY = "__ruhu_fact_metadata__"
_STEP_MISSING_FACTS_KEY = "__ruhu_step_missing_facts__"
_CURSOR_REVISION_KEY = "__ruhu_cursor_revision__"
_RUNTIME_CURSOR_METADATA_KEYS = frozenset(
    {
        "__ruhu_current_step_id__",
        "__ruhu_current_scenario_id__",
        "__ruhu_step_capabilities__",
        "__ruhu_step_missing_facts__",
        "__ruhu_step_tool_refs__",
        "__ruhu_step_transition_targets__",
        "__ruhu_step_workload_class__",
        "__ruhu_step_execution_isolation__",
        "__ruhu_step_say__",
        "__ruhu_cursor_revision__",
    }
)


class _PayloadTooLarge(ValueError):
    pass


class TraceWriteFailed(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        reason: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.retryable = retryable


def _json_size_bytes(trace: TurnTrace) -> int:
    return len(trace.model_dump_json().encode("utf-8"))


def _enforce_payload_budget(
    trace: TurnTrace,
    *,
    hard_cap_bytes: int = _TRACE_PAYLOAD_HARD_CAP_BYTES,
) -> None:
    if _json_size_bytes(trace) <= hard_cap_bytes:
        return

    truncated_any = False

    if trace.normalized_observation is not None and trace.normalized_observation.metadata_summary:
        trace.normalized_observation.metadata_summary = {}
        trace.truncated_fields.append("normalized_observation.metadata_summary")
        truncated_any = True
        if _json_size_bytes(trace) <= hard_cap_bytes:
            return

    model_output_truncated = False
    for item in trace.model_outputs:
        if item.error:
            item.error = None
            model_output_truncated = True
    if model_output_truncated:
        trace.truncated_fields.append("model_outputs.errors")
        truncated_any = True
        if _json_size_bytes(trace) <= hard_cap_bytes:
            return

    message_truncated = False
    for item in trace.emitted_messages:
        if item.text:
            item.text = item.text[:256]
            message_truncated = True
    if message_truncated:
        trace.truncated_fields.append("emitted_messages.bodies")
        truncated_any = True
        if _json_size_bytes(trace) <= hard_cap_bytes:
            return

    tool_payload_truncated = False
    for item in trace.tool_calls:
        if item.payload:
            item.payload = {}
            tool_payload_truncated = True
    if tool_payload_truncated:
        trace.truncated_fields.append("tool_calls.payloads")
        truncated_any = True
        if _json_size_bytes(trace) <= hard_cap_bytes:
            return

    if truncated_any:
        try:
            from .observability.metrics import trace_write_truncations_total
            trace_write_truncations_total.inc()
        except Exception:
            pass
    raise _PayloadTooLarge("turn trace exceeds payload budget after truncation")


def _pack_trace_extensions(trace: TurnTrace) -> dict[str, Any]:
    return {
        "schema_version": trace.schema_version,
        "otel_trace_id": trace.otel_trace_id,
        "channel": trace.channel,
        "modality": trace.modality,
        "event_type": trace.event_type,
        "normalized_observation": (
            None if trace.normalized_observation is None else trace.normalized_observation.model_dump(mode="json")
        ),
        "guard_results": [item.model_dump(mode="json") for item in trace.guard_results],
        "model_outputs": [item.model_dump(mode="json") for item in trace.model_outputs],
        "truncated_fields": list(trace.truncated_fields),
        "error_kind": trace.error_kind,
        "decision_observability": trace.decision_observability.model_dump(mode="json"),
    }


def _merge_rules_payload(trace: TurnTrace) -> dict[str, Any]:
    payload = trace.rules.model_dump(mode="json")
    payload[_TRACE_EXTENSION_KEY] = _pack_trace_extensions(trace)
    return payload


class ConversationStore(Protocol):
    def load(self, conversation_id: str) -> ConversationState | None: ...

    def save(self, state: ConversationState, *, session: Session | None = None) -> None: ...

    def list_conversations(
        self,
        *,
        organization_id: str | None = None,
        agent_id: str | None = None,
        agent_version_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ConversationState]: ...


class TraceStore(Protocol):
    def append(self, trace: TurnTrace, *, session: Session | None = None) -> None: ...

    def all(
        self,
        *,
        organization_id: str | None = None,
        agent_id: str | None = None,
        agent_version_id: str | None = None,
    ) -> list[TurnTrace]: ...

    def by_conversation(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[TurnTrace]: ...


class DuplicateTurnError(RuntimeError):
    """Raised when a turn with the same (conversation_id, dedupe_key) was already committed."""

    def __init__(self, conversation_id: str, dedupe_key: str) -> None:
        super().__init__(f"duplicate turn for conversation {conversation_id}: {dedupe_key}")
        self.conversation_id = conversation_id
        self.dedupe_key = dedupe_key


class TurnLogStore(Protocol):
    def append(self, entry: TurnLogEntry, *, session: Session | None = None) -> TurnLogEntry: ...

    def by_conversation(self, conversation_id: str, *, organization_id: str | None = None) -> list[TurnLogEntry]: ...


class InMemoryConversationStore:
    def __init__(self) -> None:
        self._items: dict[str, ConversationState] = {}

    def load(self, conversation_id: str) -> ConversationState | None:
        item = self._items.get(conversation_id)
        return deepcopy(item) if item else None

    def save(self, state: ConversationState, *, session: Session | None = None) -> None:
        previous = self._items.get(state.conversation_id)
        if previous is None:
            self._items[state.conversation_id] = deepcopy(state)
            return
        merged = deepcopy(state)
        merged.facts, merged.metadata = _merge_conversation_fact_payload_for_save(
            existing_facts=previous.facts,
            existing_metadata=previous.metadata,
            incoming_facts=state.facts,
            incoming_metadata=state.metadata,
        )
        if _metadata_cursor_revision(previous.metadata) > _metadata_cursor_revision(state.metadata):
            merged.step_id = previous.step_id
            _preserve_runtime_cursor_metadata(merged.metadata, previous.metadata)
            _prune_missing_facts_metadata(merged.metadata, merged.facts)
        self._items[state.conversation_id] = merged

    def list_conversations(
        self,
        *,
        organization_id: str | None = None,
        agent_id: str | None = None,
        agent_version_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ConversationState]:
        items = self._items.values()
        if organization_id is not None:
            items = [item for item in items if item.organization_id == organization_id or item.organization_id is None]
        if agent_id is not None:
            items = [item for item in items if item.agent_id == agent_id]
        if agent_version_id is not None:
            items = [item for item in items if item.agent_version_id == agent_version_id]
        ordered = sorted(items, key=lambda item: item.updated_at)
        window = ordered[offset:] if limit is None else ordered[offset : offset + limit]
        return [deepcopy(item) for item in window]


class InMemoryTraceStore:
    def __init__(self) -> None:
        self._items: list[TurnTrace] = []

    def append(self, trace: TurnTrace, *, session: Session | None = None) -> None:
        self._items.append(deepcopy(trace))

    def all(
        self,
        *,
        organization_id: str | None = None,
        agent_id: str | None = None,
        agent_version_id: str | None = None,
    ) -> list[TurnTrace]:
        items = self._items
        if organization_id is not None:
            items = [item for item in items if item.organization_id == organization_id or item.organization_id is None]
        if agent_id is not None:
            items = [item for item in items if item.agent_id == agent_id]
        if agent_version_id is not None:
            items = [item for item in items if item.agent_version_id == agent_version_id]
        return deepcopy(items)

    def by_conversation(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[TurnTrace]:
        matched = [
            item
            for item in self._items
            if item.conversation_id == conversation_id
            and (organization_id is None or item.organization_id == organization_id)
        ]
        window = matched[offset:] if limit is None else matched[offset : offset + limit]
        return [deepcopy(item) for item in window]


class SQLAlchemyConversationStore:
    def __init__(self, session_factory: sessionmaker[Session], *, pii_scanner: Any | None = None) -> None:
        self._session_factory = session_factory
        self._pii_scanner: TieredPiiScanner | None = pii_scanner

    def load(self, conversation_id: str) -> ConversationState | None:
        with self._session_factory() as session:
            record = session.get(ConversationRecord, conversation_id)
            return None if record is None else _record_to_conversation(record)

    def save(self, state: ConversationState, *, session: Session | None = None) -> None:
        managed_session = session
        owns_session = managed_session is None
        if managed_session is None:
            managed_session = self._session_factory()
        try:
            record = managed_session.get(ConversationRecord, state.conversation_id)
            metadata_to_write = dict(state.metadata)
            facts_to_write = dict(state.facts)
            if record is not None:
                existing_metadata = dict(record.metadata_json or {})
                existing_cursor_revision = _metadata_cursor_revision(existing_metadata)
                incoming_cursor_revision = _metadata_cursor_revision(metadata_to_write)
                facts_to_write, metadata_to_write = _merge_conversation_fact_payload_for_save(
                    existing_facts=dict(record.facts_json or {}),
                    existing_metadata=existing_metadata,
                    incoming_facts=facts_to_write,
                    incoming_metadata=metadata_to_write,
                )
                preserve_existing_cursor = existing_cursor_revision > incoming_cursor_revision
                if preserve_existing_cursor:
                    _preserve_runtime_cursor_metadata(metadata_to_write, existing_metadata)
                    _prune_missing_facts_metadata(metadata_to_write, facts_to_write)
                step_id_to_write = record.step_id if preserve_existing_cursor else state.step_id
            else:
                step_id_to_write = state.step_id

            # COORD: pii-pipeline — scan facts for PII before writing
            if self._pii_scanner is not None:
                try:
                    scan = self._pii_scanner.scan_and_redact_dict(
                        facts_to_write,
                        context={
                            "field_context": "facts_json",
                            "conversation_id": state.conversation_id,
                            "organization_id": state.organization_id,
                        },
                    )
                    facts_to_write = scan.redacted_dict or facts_to_write
                except Exception:
                    pass  # Fail open: use original if scanning fails

            if record is None:
                managed_session.add(_conversation_to_record(state, facts=facts_to_write, metadata=metadata_to_write))
            else:
                record.organization_id = state.organization_id
                record.agent_id = state.agent_id
                record.agent_version_id = state.agent_version_id
                record.mode = state.mode
                record.channel = state.channel
                record.status = state.status
                record.outcome = state.outcome
                record.step_id = step_id_to_write
                record.facts_json = facts_to_write
                record.metadata_json = metadata_to_write
                record.control_state_json = state.control_state.model_dump(mode="json")
                record.processed_dedupe_keys_json = list(state.processed_dedupe_keys)
                record.started_at = state.started_at
                record.ended_at = state.ended_at
                record.updated_at = state.updated_at
                if record.created_at is None:
                    record.created_at = state.started_at
            if owns_session:
                managed_session.commit()
        finally:
            if owns_session:
                managed_session.close()

    def list_conversations(
        self,
        *,
        organization_id: str | None = None,
        agent_id: str | None = None,
        agent_version_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ConversationState]:
        # Pagination is pushed into SQL (RP-3.3): tie-break the updated_at
        # ordering by primary key so OFFSET/LIMIT windows are deterministic.
        statement = select(ConversationRecord).order_by(
            ConversationRecord.updated_at.asc(),
            ConversationRecord.conversation_id.asc(),
        )
        if organization_id is not None:
            statement = statement.where(
                or_(
                    ConversationRecord.organization_id == organization_id,
                    ConversationRecord.organization_id.is_(None),
                )
            )
        if agent_id is not None:
            statement = statement.where(ConversationRecord.agent_id == agent_id)
        if agent_version_id is not None:
            statement = statement.where(ConversationRecord.agent_version_id == agent_version_id)
        if offset:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_conversation(record) for record in records]


class SQLAlchemyTraceStore:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        hard_cap_bytes: int = _TRACE_PAYLOAD_HARD_CAP_BYTES,
        pii_scanner: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._hard_cap_bytes = hard_cap_bytes
        self._pii_scanner: TieredPiiScanner | None = pii_scanner

    def append(self, trace: TurnTrace, *, session: Session | None = None) -> None:
        trace = trace.model_copy(deep=True)
        try:
            _enforce_payload_budget(trace, hard_cap_bytes=self._hard_cap_bytes)
        except _PayloadTooLarge as exc:
            raise TraceWriteFailed(
                str(exc),
                reason="payload_too_large",
                retryable=False,
            ) from exc

        # COORD: pii-pipeline — scan trace fields for PII before writing
        tool_calls_to_write = [item.model_dump(mode="json") for item in trace.tool_calls]
        fact_updates_to_write = [item.model_dump(mode="json") for item in trace.fact_updates]
        messages_to_write = [item.model_dump(mode="json") for item in trace.emitted_messages]

        if self._pii_scanner is not None:
            try:
                # Scan tool calls
                if tool_calls_to_write:
                    tc_scan = self._pii_scanner.scan_and_redact_dict(
                        {"tool_calls": tool_calls_to_write},
                        context={
                            "field_context": "tool_calls_json",
                            "conversation_id": trace.conversation_id,
                            "organization_id": trace.organization_id,
                        },
                    )
                    tool_calls_to_write = tc_scan.redacted_dict.get("tool_calls", tool_calls_to_write) if tc_scan.redacted_dict else tool_calls_to_write
            except Exception:
                pass  # Fail open: use original if scanning fails

            try:
                # Scan fact updates
                if fact_updates_to_write:
                    fu_scan = self._pii_scanner.scan_and_redact_dict(
                        {"fact_updates": fact_updates_to_write},
                        context={
                            "field_context": "fact_updates_json",
                            "conversation_id": trace.conversation_id,
                            "organization_id": trace.organization_id,
                        },
                    )
                    fact_updates_to_write = fu_scan.redacted_dict.get("fact_updates", fact_updates_to_write) if fu_scan.redacted_dict else fact_updates_to_write
            except Exception:
                pass  # Fail open: use original if scanning fails

            try:
                # Scan emitted messages
                if messages_to_write:
                    msg_scan = self._pii_scanner.scan_and_redact_dict(
                        {"messages": messages_to_write},
                        context={
                            "field_context": "emitted_messages_json",
                            "conversation_id": trace.conversation_id,
                            "organization_id": trace.organization_id,
                        },
                    )
                    messages_to_write = msg_scan.redacted_dict.get("messages", messages_to_write) if msg_scan.redacted_dict else messages_to_write
            except Exception:
                pass  # Fail open: use original if scanning fails

        managed_session = session
        owns_session = managed_session is None
        if managed_session is None:
            managed_session = self._session_factory()
        try:
            record = managed_session.get(TurnTraceRecord, trace.trace_id)
            if record is None:
                managed_session.add(
                    _trace_to_record(
                        trace,
                        tool_calls=tool_calls_to_write,
                        fact_updates=fact_updates_to_write,
                        messages=messages_to_write,
                    )
                )
            else:
                record.conversation_id = trace.conversation_id
                record.organization_id = trace.organization_id
                record.turn_id = trace.turn_id
                record.agent_id = trace.agent_id
                record.agent_version_id = trace.agent_version_id
                record.step_before = trace.step_before
                record.step_after = trace.step_after
                record.semantic_events_json = [item.model_dump(mode="json") for item in trace.semantic_events]
                record.fact_updates_json = fact_updates_to_write
                record.chosen_action_json = trace.chosen_action.model_dump(mode="json")
                record.emitted_messages_json = messages_to_write
                record.tool_calls_json = tool_calls_to_write
                record.rules_json = _merge_rules_payload(trace)
                record.latency_breakdown_ms_json = dict(trace.latency_breakdown_ms)
                record.classifier_json = _project_classifier_trace(trace)
                record.recorded_at = trace.recorded_at
            if owns_session:
                managed_session.commit()
            try:
                from .observability.metrics import (
                    trace_write_success_total,
                    trace_write_truncations_total,
                    turn_error_total,
                )
                trace_write_success_total.inc()
                if trace.truncated_fields:
                    trace_write_truncations_total.inc()
                if trace.error_kind != "none":
                    turn_error_total.labels(error_kind=trace.error_kind).inc()
            except Exception:
                pass
        except TraceWriteFailed:
            raise
        except Exception as exc:
            if owns_session:
                managed_session.rollback()
            raise TraceWriteFailed(
                str(exc),
                reason="db_error",
                retryable=True,
            ) from exc
        finally:
            if owns_session:
                managed_session.close()

    def all(
        self,
        *,
        organization_id: str | None = None,
        agent_id: str | None = None,
        agent_version_id: str | None = None,
    ) -> list[TurnTrace]:
        statement = select(TurnTraceRecord).order_by(TurnTraceRecord.recorded_at.asc())
        if organization_id is not None:
            statement = statement.where(
                or_(
                    TurnTraceRecord.organization_id == organization_id,
                    TurnTraceRecord.organization_id.is_(None),
                )
            )
        if agent_id is not None:
            statement = statement.where(TurnTraceRecord.agent_id == agent_id)
        if agent_version_id is not None:
            statement = statement.where(TurnTraceRecord.agent_version_id == agent_version_id)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_trace(record) for record in records]

    def by_conversation(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[TurnTrace]:
        # Pagination is pushed into SQL (RP-3.3): tie-break the recorded_at
        # ordering by primary key so OFFSET/LIMIT windows are deterministic.
        statement = (
            select(TurnTraceRecord)
            .where(TurnTraceRecord.conversation_id == conversation_id)
            .order_by(TurnTraceRecord.recorded_at.asc(), TurnTraceRecord.trace_id.asc())
        )
        if organization_id is not None:
            statement = statement.where(TurnTraceRecord.organization_id == organization_id)
        if offset:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_trace(record) for record in records]


class InMemoryTurnLogStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, list[TurnLogEntry]] = {}
        self._dedupe: set[tuple[str, str]] = set()

    def append(self, entry: TurnLogEntry, *, session: Session | None = None) -> TurnLogEntry:
        with self._lock:
            key = (entry.conversation_id, entry.dedupe_key)
            if key in self._dedupe:
                raise DuplicateTurnError(entry.conversation_id, entry.dedupe_key)
            stored = entry.model_copy(deep=True)
            entries = self._entries.setdefault(entry.conversation_id, [])
            stored.seq = len(entries) + 1
            entries.append(stored)
            self._dedupe.add(key)
            return stored.model_copy(deep=True)

    def by_conversation(self, conversation_id: str, *, organization_id: str | None = None) -> list[TurnLogEntry]:
        with self._lock:
            entries = self._entries.get(conversation_id, [])
            return [
                item.model_copy(deep=True)
                for item in entries
                if organization_id is None or item.organization_id == organization_id
            ]


class SQLAlchemyTurnLogStore:
    """Append-only turn log with DB-enforced dedupe and total order.

    ``append`` must run inside the kernel's shared store transaction: it locks
    the conversation row (``FOR UPDATE``) to serialize concurrent commits for
    the same conversation, assigns ``seq`` from ``last_turn_seq``, and flushes
    so a ``UNIQUE (conversation_id, dedupe_key)`` violation surfaces here as
    :class:`DuplicateTurnError` rather than at commit.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def append(self, entry: TurnLogEntry, *, session: Session | None = None) -> TurnLogEntry:
        managed_session = session
        owns_session = managed_session is None
        if managed_session is None:
            managed_session = self._session_factory()
        try:
            conversation = managed_session.get(
                ConversationRecord,
                entry.conversation_id,
                with_for_update=True,
            )
            if conversation is None:
                raise KeyError(f"unknown conversation id: {entry.conversation_id}")
            next_seq = int(conversation.last_turn_seq or 0) + 1
            conversation.last_turn_seq = next_seq
            stored = entry.model_copy(deep=True)
            stored.seq = next_seq
            managed_session.add(_turn_log_entry_to_record(stored))
            try:
                managed_session.flush()
            except IntegrityError as exc:
                raise DuplicateTurnError(entry.conversation_id, entry.dedupe_key) from exc
            if owns_session:
                managed_session.commit()
            return stored
        except DuplicateTurnError:
            if owns_session:
                managed_session.rollback()
            raise
        finally:
            if owns_session:
                managed_session.close()

    def by_conversation(self, conversation_id: str, *, organization_id: str | None = None) -> list[TurnLogEntry]:
        statement = (
            select(ConversationTurnRecord)
            .where(ConversationTurnRecord.conversation_id == conversation_id)
            .order_by(ConversationTurnRecord.seq.asc())
        )
        if organization_id is not None:
            statement = statement.where(ConversationTurnRecord.organization_id == organization_id)
        with self._session_factory() as session:
            records = session.execute(statement).scalars().all()
        return [_record_to_turn_log_entry(record) for record in records]


def rebuild_conversation_state(
    turn_log_store: TurnLogStore,
    conversation_id: str,
) -> ConversationState | None:
    """Fold the turn log back into a conversation state.

    Every turn row snapshots the state it committed, so the fold is the
    snapshot of the highest ``seq``. Returns ``None`` for conversations with
    no committed turns (e.g. just initialized).
    """
    entries = turn_log_store.by_conversation(conversation_id)
    if not entries:
        return None
    return ConversationState.model_validate(entries[-1].state_after)


def _turn_log_entry_to_record(entry: TurnLogEntry) -> ConversationTurnRecord:
    return ConversationTurnRecord(
        turn_pk=entry.turn_pk,
        conversation_id=entry.conversation_id,
        organization_id=entry.organization_id,
        seq=entry.seq,
        turn_id=entry.turn_id,
        dedupe_key=entry.dedupe_key,
        trace_id=entry.trace_id,
        step_before=entry.step_before,
        step_after=entry.step_after,
        state_after_json=dict(entry.state_after),
        created_at=entry.created_at,
    )


def _record_to_turn_log_entry(record: ConversationTurnRecord) -> TurnLogEntry:
    return TurnLogEntry(
        turn_pk=record.turn_pk,
        conversation_id=record.conversation_id,
        organization_id=record.organization_id,
        seq=record.seq,
        turn_id=record.turn_id,
        dedupe_key=record.dedupe_key,
        trace_id=record.trace_id,
        step_before=record.step_before,
        step_after=record.step_after,
        state_after=dict(record.state_after_json or {}),
        created_at=record.created_at,
    )


def _merge_conversation_fact_payload_for_save(
    *,
    existing_facts: dict[str, Any],
    existing_metadata: dict[str, Any],
    incoming_facts: dict[str, Any],
    incoming_metadata: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge fact payloads so concurrent voice fragments cannot erase facts.

    Voice final transcripts can be processed in parallel. A later no-op turn
    may have loaded the conversation before an earlier turn committed a fact;
    saving that stale snapshot must not remove the already-committed fact.
    Fact metadata carries capture timestamps, so field-level merges can keep
    the newest captured value while still allowing intentional corrections.
    """
    existing_fact_metadata = _metadata_fact_map(existing_metadata)
    incoming_fact_metadata = _metadata_fact_map(incoming_metadata)
    merged_facts: dict[str, Any] = {}
    merged_fact_metadata: dict[str, Any] = {}
    for fact_name in sorted(set(existing_facts) | set(incoming_facts)):
        has_existing = fact_name in existing_facts
        has_incoming = fact_name in incoming_facts
        if has_existing and has_incoming:
            if _incoming_fact_is_at_least_as_new(
                incoming_fact_metadata.get(fact_name),
                existing_fact_metadata.get(fact_name),
            ):
                merged_facts[fact_name] = incoming_facts[fact_name]
                if fact_name in incoming_fact_metadata:
                    merged_fact_metadata[fact_name] = incoming_fact_metadata[fact_name]
                elif fact_name in existing_fact_metadata:
                    merged_fact_metadata[fact_name] = existing_fact_metadata[fact_name]
            else:
                merged_facts[fact_name] = existing_facts[fact_name]
                if fact_name in existing_fact_metadata:
                    merged_fact_metadata[fact_name] = existing_fact_metadata[fact_name]
                elif fact_name in incoming_fact_metadata:
                    merged_fact_metadata[fact_name] = incoming_fact_metadata[fact_name]
        elif has_incoming:
            merged_facts[fact_name] = incoming_facts[fact_name]
            if fact_name in incoming_fact_metadata:
                merged_fact_metadata[fact_name] = incoming_fact_metadata[fact_name]
        else:
            merged_facts[fact_name] = existing_facts[fact_name]
            if fact_name in existing_fact_metadata:
                merged_fact_metadata[fact_name] = existing_fact_metadata[fact_name]

    merged_metadata = dict(existing_metadata)
    merged_metadata.update(dict(incoming_metadata))
    if merged_fact_metadata:
        merged_metadata[_FACT_METADATA_KEY] = merged_fact_metadata
    elif _FACT_METADATA_KEY in merged_metadata:
        merged_metadata.pop(_FACT_METADATA_KEY, None)
    _prune_missing_facts_metadata(merged_metadata, merged_facts)
    return merged_facts, merged_metadata


def _metadata_fact_map(metadata: dict[str, Any]) -> dict[str, Any]:
    value = metadata.get(_FACT_METADATA_KEY)
    return dict(value) if isinstance(value, dict) else {}


def _incoming_fact_is_at_least_as_new(incoming: Any, existing: Any) -> bool:
    incoming_dt = _metadata_captured_at(incoming)
    existing_dt = _metadata_captured_at(existing)
    if incoming_dt is None or existing_dt is None:
        return True
    return incoming_dt >= existing_dt


def _metadata_captured_at(value: Any) -> datetime | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("captured_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _prune_missing_facts_metadata(metadata: dict[str, Any], facts: dict[str, Any]) -> None:
    missing = metadata.get(_STEP_MISSING_FACTS_KEY)
    if not isinstance(missing, list):
        return
    metadata[_STEP_MISSING_FACTS_KEY] = [item for item in missing if str(item) not in facts]


def _metadata_cursor_revision(metadata: dict[str, Any]) -> int:
    try:
        return int(metadata.get(_CURSOR_REVISION_KEY) or 0)
    except (TypeError, ValueError):
        return 0


def _preserve_runtime_cursor_metadata(target: dict[str, Any], existing: dict[str, Any]) -> None:
    for key in _RUNTIME_CURSOR_METADATA_KEYS:
        if key in existing:
            target[key] = existing[key]


def _conversation_to_record(
    state: ConversationState,
    *,
    facts: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ConversationRecord:
    return ConversationRecord(
        conversation_id=state.conversation_id,
        organization_id=state.organization_id,
        agent_id=state.agent_id,
        agent_version_id=state.agent_version_id,
        mode=state.mode,
        channel=state.channel,
        status=state.status,
        outcome=state.outcome,
        step_id=state.step_id,
        facts_json=facts if facts is not None else dict(state.facts),
        metadata_json=metadata if metadata is not None else dict(state.metadata),
        control_state_json=state.control_state.model_dump(mode="json"),
        processed_dedupe_keys_json=list(state.processed_dedupe_keys),
        last_event_sequence=0,
        started_at=state.started_at,
        ended_at=state.ended_at,
        created_at=state.started_at,
        updated_at=state.updated_at,
    )


def _record_to_conversation(record: ConversationRecord) -> ConversationState:
    # Deserialize control_state from JSON, falling back to empty for older records
    control_state_raw = record.control_state_json if hasattr(record, "control_state_json") else None
    control_state = (
        ConversationControlState.model_validate(control_state_raw)
        if control_state_raw
        else ConversationControlState()
    )
    return ConversationState(
        conversation_id=record.conversation_id,
        organization_id=record.organization_id,
        agent_id=record.agent_id,
        agent_version_id=record.agent_version_id,
        mode=record.mode,
        channel=record.channel,
        status=record.status,
        outcome=record.outcome,
        step_id=record.step_id,
        facts=dict(record.facts_json or {}),
        metadata=dict(record.metadata_json or {}),
        control_state=control_state,
        processed_dedupe_keys=list(record.processed_dedupe_keys_json or []),
        started_at=record.started_at,
        ended_at=record.ended_at,
        updated_at=record.updated_at,
    )


_CLASSIFIER_TRACE_PAYLOAD_KEY = "classifier_trace"
_CLASSIFIER_TRACE_FIELDS = frozenset(ClassifierTraceRecord.model_fields.keys())


def _project_classifier_trace(
    trace: TurnTrace,
) -> dict[str, Any] | None:
    """Project per-turn classifier observability for the ``classifier_json`` column.

    Source order:
    1. ``trace.classifier`` (preferred — set by the kernel/dispatcher when
       classifier metadata is plumbed through ``RuntimeTurnResult`` directly
       in Stage 3+).
    2. The first ``SemanticEventRecord`` with ``source == "classifier"`` whose
       ``payload[_CLASSIFIER_TRACE_PAYLOAD_KEY]`` is a dict (Stage 1–2 path —
       the interpreter stashes ``ClassificationResult`` fields under that key).

    Returns ``None`` when no classifier metadata is present, leaving
    ``classifier_json`` NULL so the column stays sparse for legacy turns.
    """
    if trace.classifier is not None:
        return trace.classifier.model_dump(mode="json")
    for event in trace.semantic_events:
        if event.source != "classifier":
            continue
        stash = event.payload.get(_CLASSIFIER_TRACE_PAYLOAD_KEY)
        if not isinstance(stash, dict):
            continue
        normalized = {key: stash[key] for key in _CLASSIFIER_TRACE_FIELDS if key in stash}
        if not normalized:
            continue
        try:
            return ClassifierTraceRecord.model_validate(normalized).model_dump(mode="json")
        except Exception:
            return None
    return None


def _trace_to_record(
    trace: TurnTrace,
    tool_calls: list[Any] | None = None,
    fact_updates: list[Any] | None = None,
    messages: list[Any] | None = None,
) -> TurnTraceRecord:
    return TurnTraceRecord(
        trace_id=trace.trace_id,
        conversation_id=trace.conversation_id,
        organization_id=trace.organization_id,
        turn_id=trace.turn_id,
        agent_id=trace.agent_id,
        agent_version_id=trace.agent_version_id,
        step_before=trace.step_before,
        step_after=trace.step_after,
        semantic_events_json=[item.model_dump(mode="json") for item in trace.semantic_events],
        fact_updates_json=fact_updates if fact_updates is not None else [item.model_dump(mode="json") for item in trace.fact_updates],
        chosen_action_json=trace.chosen_action.model_dump(mode="json"),
        emitted_messages_json=messages if messages is not None else [item.model_dump(mode="json") for item in trace.emitted_messages],
        tool_calls_json=tool_calls if tool_calls is not None else [item.model_dump(mode="json") for item in trace.tool_calls],
        rules_json=_merge_rules_payload(trace),
        latency_breakdown_ms_json=dict(trace.latency_breakdown_ms),
        classifier_json=_project_classifier_trace(trace),
        recorded_at=trace.recorded_at,
    )


def _record_to_trace(record: TurnTraceRecord) -> TurnTrace:
    rules_payload = _coerce_json_object(record.rules_json, field_name="rules_json")
    trace_extensions = rules_payload.pop(_TRACE_EXTENSION_KEY, {})
    if not isinstance(trace_extensions, dict):
        trace_extensions = {}
    return TurnTrace.model_validate(
        {
            "schema_version": trace_extensions.get("schema_version", 1),
            "trace_id": record.trace_id,
            "conversation_id": record.conversation_id,
            "organization_id": record.organization_id,
            "turn_id": record.turn_id,
            "agent_id": record.agent_id,
            "agent_version_id": record.agent_version_id,
            "otel_trace_id": trace_extensions.get("otel_trace_id"),
            "channel": trace_extensions.get("channel", ""),
            "modality": trace_extensions.get("modality", ""),
            "event_type": trace_extensions.get("event_type", ""),
            "normalized_observation": trace_extensions.get("normalized_observation"),
            "guard_results": trace_extensions.get("guard_results", []),
            "model_outputs": trace_extensions.get("model_outputs", []),
            "truncated_fields": trace_extensions.get("truncated_fields", []),
            "error_kind": trace_extensions.get("error_kind", "none"),
            "decision_observability": trace_extensions.get("decision_observability", {}),
            "step_before": record.step_before,
            "step_after": record.step_after,
            "semantic_events": _coerce_json_array(record.semantic_events_json, field_name="semantic_events_json"),
            "fact_updates": _coerce_json_array(record.fact_updates_json, field_name="fact_updates_json"),
            "chosen_action": _coerce_json_object(record.chosen_action_json, field_name="chosen_action_json"),
            "emitted_messages": _coerce_json_array(record.emitted_messages_json, field_name="emitted_messages_json"),
            "tool_calls": _coerce_json_array(record.tool_calls_json, field_name="tool_calls_json"),
            "rules": rules_payload,
            "latency_breakdown_ms": _coerce_json_object(
                record.latency_breakdown_ms_json,
                field_name="latency_breakdown_ms_json",
            ),
            "classifier": _parse_json_value(record.classifier_json),
            "recorded_at": record.recorded_at,
        }
    )


def _coerce_json_array(value: Any, *, field_name: str) -> list[Any]:
    parsed = _parse_json_value(value)
    if parsed is None:
        return []
    if isinstance(parsed, list):
        return list(parsed)
    raise ValueError(f"{field_name} expected a JSON array but received {type(parsed).__name__}")


def _coerce_json_object(value: Any, *, field_name: str) -> dict[str, Any]:
    parsed = _parse_json_value(value)
    if parsed is None:
        return {}
    if isinstance(parsed, dict):
        return dict(parsed)
    raise ValueError(f"{field_name} expected a JSON object but received {type(parsed).__name__}")


def _parse_json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return value
