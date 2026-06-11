from __future__ import annotations

from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from .db_models import (
    ConversationRecord,
    ExternalCaseLinkRecord,
    AgentRecord,
    RealtimeEventRecord,
    RealtimeSessionRecord,
    SupportCaseEventRecord,
    SupportCaseNoteRecord,
    SupportCaseRecord,
    TicketingActivityRecord,
    TicketingConnectionRecord,
    TurnTraceRecord,
)
from .ticketing_providers import (
    ProviderConnectionConfig,
    RemoteCase,
    TicketingAdapter,
    TicketingProviderError,
    WebhookSyncResult,
    build_ticketing_adapter,
)


TicketDashboardSortField = Literal["started_at", "duration_seconds", "sentiment_score", "outcome", "message_count"]
TicketDashboardSortDirection = Literal["asc", "desc"]
SupportCaseStatus = Literal[
    "open",
    "triaged",
    "in_progress",
    "waiting_customer",
    "waiting_internal",
    "resolved",
    "closed",
    "cancelled",
]
SupportCasePriority = Literal["low", "medium", "high", "urgent"]
SupportCaseSource = Literal["conversation_rule", "manual", "api", "import", "external_sync"]
SupportCaseNoteVisibility = Literal["internal", "customer_visible"]
ExternalTicketingProvider = Literal["zendesk", "freshdesk", "jira", "other"]
TicketingConnectionStatus = Literal["pending", "active", "disabled", "degraded", "error"]
ExternalCaseSyncStatus = Literal["linked", "pending_sync", "synced", "error"]
TimelineEntryKind = Literal["state_transition", "assistant_message", "tool_call", "fact_update", "semantic_event"]
TicketingRetryStatus = Literal["none", "pending", "in_progress", "succeeded", "exhausted"]


class TicketDashboardHandler(BaseModel):
    handler_id: str
    handler_name: str


class LinkedExternalCaseSummary(BaseModel):
    link_id: str
    provider: str
    external_case_key: str | None = None
    external_case_url: str | None = None
    external_case_status: str | None = None
    sync_status: str


class TicketDashboardSummary(BaseModel):
    total_count: int
    resolved_rate: float
    transferred_count: int
    average_duration_seconds: int


class TicketDashboardItem(BaseModel):
    conversation_id: str
    organization_id: str | None = None
    handler_id: str
    handler_name: str
    channel: str | None = None
    participant_display: str
    participant_ref: str | None = None
    status: str
    outcome: str | None = None
    outcome_reason: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: int
    message_count: int
    sentiment_score: float | None = None
    has_handoff: bool = False
    has_tool_failures: bool = False
    last_activity_at: datetime
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    linked_support_case_count: int = 0
    linked_external_cases: list[LinkedExternalCaseSummary] = Field(default_factory=list)


class TicketDashboardResponse(BaseModel):
    summary: TicketDashboardSummary
    handlers: list[TicketDashboardHandler] = Field(default_factory=list)
    items: list[TicketDashboardItem] = Field(default_factory=list)


class SupportCaseResolution(BaseModel):
    resolution_type: str
    summary: str
    details: str | None = None
    resolved_by_user_id: str
    resolved_at: datetime
    requires_follow_up: bool = False
    follow_up_at: datetime | None = None


class SupportCase(BaseModel):
    case_id: str
    organization_id: str
    case_number: str
    title: str
    description: str
    status: SupportCaseStatus
    priority: SupportCasePriority
    category: str
    source: SupportCaseSource
    primary_conversation_id: str | None = None
    related_conversation_ids: list[str] = Field(default_factory=list)
    created_by_user_id: str | None = None
    assigned_to_user_id: str | None = None
    assigned_team: str | None = None
    owning_agent_id: str | None = None
    participant_ref: str | None = None
    participant_display: str | None = None
    participant_email: str | None = None
    participant_phone: str | None = None
    tags: list[str] = Field(default_factory=list)
    custom_fields: dict[str, object] = Field(default_factory=dict)
    case_metadata: dict[str, object] = Field(default_factory=dict)
    resolution: SupportCaseResolution | None = None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None
    closed_at: datetime | None = None


class SupportCaseNote(BaseModel):
    note_id: str
    case_id: str
    organization_id: str
    author_user_id: str | None = None
    body: str
    visibility: SupportCaseNoteVisibility = "internal"
    created_at: datetime


class SupportCaseEvent(BaseModel):
    event_id: str
    case_id: str
    organization_id: str
    event_type: str
    actor_user_id: str | None = None
    details: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class TicketTranscriptEntry(BaseModel):
    entry_id: str
    role: Literal["user", "assistant", "system", "tool"]
    channel: str | None = None
    text: str
    source: str
    recorded_at: datetime
    metadata: dict[str, object] = Field(default_factory=dict)


class TicketEvidenceEntry(BaseModel):
    evidence_id: str
    kind: Literal["session", "tool_call", "fact_update", "semantic_event", "event"]
    label: str
    status: str | None = None
    detail: str | None = None
    recorded_at: datetime
    metadata: dict[str, object] = Field(default_factory=dict)


class TicketingConnection(BaseModel):
    connection_id: str
    organization_id: str
    provider: ExternalTicketingProvider
    display_name: str
    status: TicketingConnectionStatus
    auth_type: str
    credentials_ref: str | None = None
    provider_config: dict[str, object] = Field(default_factory=dict)
    field_mappings: dict[str, object] = Field(default_factory=dict)
    status_mappings: dict[str, object] = Field(default_factory=dict)
    priority_mappings: dict[str, object] = Field(default_factory=dict)
    default_queue: str | None = None
    created_at: datetime
    updated_at: datetime


class TicketingActivity(BaseModel):
    activity_id: str
    organization_id: str
    connection_id: str | None = None
    link_id: str | None = None
    provider: str
    direction: Literal["outbound", "inbound"]
    action: str
    status: str
    external_case_id: str | None = None
    attempt_count: int = 1
    duration_ms: int | None = None
    request: dict[str, object] = Field(default_factory=dict)
    response: dict[str, object] = Field(default_factory=dict)
    error_message: str | None = None
    retry_status: TicketingRetryStatus = "none"
    next_retry_at: datetime | None = None
    last_attempted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ExternalCaseComment(BaseModel):
    comment_id: str
    body: str
    visibility: str = "internal"
    created_at: datetime
    author_user_id: str | None = None


class ExternalCaseLink(BaseModel):
    link_id: str
    organization_id: str
    provider: ExternalTicketingProvider
    connection_id: str
    external_case_id: str
    external_case_key: str | None = None
    external_case_url: str | None = None
    external_case_status: str | None = None
    external_case_priority: str | None = None
    support_case_id: str | None = None
    conversation_id: str | None = None
    sync_status: ExternalCaseSyncStatus
    last_synced_at: datetime | None = None
    last_sync_error: str | None = None
    provider_payload_snapshot: dict[str, object] = Field(default_factory=dict)
    comments: list[ExternalCaseComment] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class TicketTimelineEntry(BaseModel):
    kind: TimelineEntryKind
    label: str
    detail: str | None = None
    recorded_at: datetime
    metadata: dict[str, object] = Field(default_factory=dict)


class TicketConversationDetail(BaseModel):
    conversation: TicketDashboardItem
    support_cases: list[SupportCase] = Field(default_factory=list)
    external_case_links: list[ExternalCaseLink] = Field(default_factory=list)
    transcript: list[TicketTranscriptEntry] = Field(default_factory=list)
    evidence: list[TicketEvidenceEntry] = Field(default_factory=list)
    timeline: list[TicketTimelineEntry] = Field(default_factory=list)


class TicketSystemService:
    _retryable_outbound_actions = {"health_check", "create_external_case", "add_comment", "transition_case", "sync_case"}
    _default_retry_attempts = 4
    _retry_claim_lease = timedelta(minutes=5)

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        adapter_builder: Callable[[ProviderConnectionConfig], TicketingAdapter] = build_ticketing_adapter,
    ) -> None:
        self._session_factory = session_factory
        self._adapter_builder = adapter_builder

    @property
    def session_factory(self) -> sessionmaker[Session]:
        return self._session_factory

    def get_dashboard(
        self,
        *,
        organization_id: str,
        q: str | None = None,
        handler_id: str | None = None,
        channel: str | None = None,
        outcome: str | None = None,
        days: int | None = 7,
        limit: int = 50,
        offset: int = 0,
        sort_by: TicketDashboardSortField = "started_at",
        sort_dir: TicketDashboardSortDirection = "desc",
    ) -> TicketDashboardResponse:
        with self._session_factory() as session:
            records = self._list_conversation_records(
                session,
                organization_id=organization_id,
                handler_id=handler_id,
                channel=channel,
                outcome=outcome,
                days=days,
            )
            agent_names = self._agent_name_map(session, organization_id=organization_id)
            traces_by_conversation = self._traces_by_conversation(
                session,
                conversation_ids=[record.conversation_id for record in records],
                organization_id=organization_id,
            )
            cases = self._list_support_case_records(session, organization_id=organization_id)
            external_links = self._list_external_case_link_records(session, organization_id=organization_id)

            items = [
                self._build_dashboard_item(
                    record,
                    agent_names=agent_names,
                    traces=traces_by_conversation.get(record.conversation_id, []),
                    support_cases=cases,
                    external_links=external_links,
                )
                for record in records
            ]
            normalized_query = _normalize_search_query(q)
            if normalized_query:
                items = [item for item in items if self._dashboard_item_matches_query(item, normalized_query)]
            items = self._sort_dashboard_items(items, sort_by=sort_by, sort_dir=sort_dir)
            # Summary is computed on all matched items so totals/rates reflect the
            # full filtered set, not just the current page.
            summary = self._build_dashboard_summary(items)
            handlers = [
                TicketDashboardHandler(handler_id=agent_id, handler_name=name)
                for agent_id, name in sorted(agent_names.items(), key=lambda item: item[1].lower())
            ]
            return TicketDashboardResponse(summary=summary, handlers=handlers, items=items[offset:offset + limit])

    def get_conversation_detail(
        self,
        *,
        organization_id: str,
        conversation_id: str,
    ) -> TicketConversationDetail | None:
        with self._session_factory() as session:
            record = session.get(ConversationRecord, conversation_id)
            if record is None or record.organization_id != organization_id:
                return None
            agent_names = self._agent_name_map(session, organization_id=organization_id)
            traces = self._traces_by_conversation(
                session,
                conversation_ids=[conversation_id],
                organization_id=organization_id,
            ).get(conversation_id, [])
            realtime_events = self._realtime_events_by_conversation(
                session,
                conversation_ids=[conversation_id],
                organization_id=organization_id,
            ).get(conversation_id, [])
            realtime_sessions = self._realtime_sessions_by_conversation(
                session,
                conversation_ids=[conversation_id],
                organization_id=organization_id,
            ).get(conversation_id, [])
            case_records = self._list_support_case_records(session, organization_id=organization_id)
            linked_cases = [
                _record_to_support_case(record)
                for record in case_records
                if record.primary_conversation_id == conversation_id
                or conversation_id in list(record.related_conversation_ids_json or [])
            ]
            link_records = [
                record
                for record in self._list_external_case_link_records(session, organization_id=organization_id)
                if record.conversation_id == conversation_id
            ]
            item = self._build_dashboard_item(
                record,
                agent_names=agent_names,
                traces=traces,
                support_cases=case_records,
                external_links=link_records,
            )
            return TicketConversationDetail(
                conversation=item,
                support_cases=linked_cases,
                external_case_links=[_record_to_external_case_link(record) for record in link_records],
                transcript=self._build_transcript(
                    record,
                    traces=traces,
                    realtime_events=realtime_events,
                ),
                evidence=self._build_evidence(
                    traces=traces,
                    realtime_events=realtime_events,
                    realtime_sessions=realtime_sessions,
                ),
                timeline=self._build_timeline(record, traces),
            )

    def create_support_case(
        self,
        *,
        organization_id: str,
        actor_user_id: str | None,
        title: str,
        description: str,
        priority: SupportCasePriority = "medium",
        category: str,
        source: SupportCaseSource = "manual",
        primary_conversation_id: str | None = None,
        related_conversation_ids: list[str] | None = None,
        assigned_to_user_id: str | None = None,
        assigned_team: str | None = None,
        owning_agent_id: str | None = None,
        participant_ref: str | None = None,
        participant_display: str | None = None,
        participant_email: str | None = None,
        participant_phone: str | None = None,
        tags: list[str] | None = None,
        custom_fields: dict[str, object] | None = None,
        case_metadata: dict[str, object] | None = None,
    ) -> SupportCase:
        now = datetime.now(timezone.utc)
        with self._session_factory.begin() as session:
            case = SupportCase(
                case_id=str(uuid4()),
                organization_id=organization_id,
                case_number=self._next_case_number(session, organization_id=organization_id, now=now),
                title=title.strip(),
                description=description.strip(),
                status="open",
                priority=priority,
                category=category.strip(),
                source=source,
                primary_conversation_id=primary_conversation_id,
                related_conversation_ids=list(related_conversation_ids or []),
                created_by_user_id=actor_user_id,
                assigned_to_user_id=assigned_to_user_id,
                assigned_team=assigned_team,
                owning_agent_id=owning_agent_id,
                participant_ref=participant_ref,
                participant_display=participant_display,
                participant_email=participant_email,
                participant_phone=participant_phone,
                tags=list(tags or []),
                custom_fields=dict(custom_fields or {}),
                case_metadata=dict(case_metadata or {}),
                created_at=now,
                updated_at=now,
            )
            session.add(_support_case_to_record(case))
            # Flush the parent case row before inserting related event rows so
            # the FK on support_case_events is satisfied in the same transaction.
            session.flush()
            self._append_case_event(
                session,
                case_id=case.case_id,
                organization_id=organization_id,
                event_type="case_created",
                actor_user_id=actor_user_id,
                details={"status": case.status, "priority": case.priority, "source": case.source},
                created_at=now,
            )
        return case

    def list_support_cases(
        self,
        *,
        organization_id: str,
        status: str | None = None,
        priority: str | None = None,
        category: str | None = None,
        assigned_to_user_id: str | None = None,
        assigned_team: str | None = None,
        source: str | None = None,
        conversation_id: str | None = None,
        q: str | None = None,
    ) -> list[SupportCase]:
        with self._session_factory() as session:
            records = self._list_support_case_records(session, organization_id=organization_id)
            if status:
                records = [record for record in records if record.status == status]
            if priority:
                records = [record for record in records if record.priority == priority]
            if category:
                records = [record for record in records if record.category == category]
            if assigned_to_user_id:
                records = [record for record in records if record.assigned_to_user_id == assigned_to_user_id]
            if assigned_team:
                records = [record for record in records if record.assigned_team == assigned_team]
            if source:
                records = [record for record in records if record.source == source]
            if conversation_id:
                records = [
                    record
                    for record in records
                    if record.primary_conversation_id == conversation_id
                    or conversation_id in list(record.related_conversation_ids_json or [])
                ]
            normalized_query = _normalize_search_query(q)
            if normalized_query:
                records = [record for record in records if self._support_case_matches_query(record, normalized_query)]
            records.sort(key=lambda item: (item.updated_at, item.case_number), reverse=True)
            return [_record_to_support_case(record) for record in records]

    def list_support_case_events(
        self,
        *,
        organization_id: str,
        case_id: str,
    ) -> list[SupportCaseEvent]:
        with self._session_factory() as session:
            statement = (
                select(SupportCaseEventRecord)
                .where(
                    SupportCaseEventRecord.organization_id == organization_id,
                    SupportCaseEventRecord.case_id == case_id,
                )
                .order_by(SupportCaseEventRecord.created_at.asc())
            )
            records = session.execute(statement).scalars().all()
        return [_record_to_support_case_event(record) for record in records]

    def get_support_case(self, *, organization_id: str, case_id: str) -> SupportCase | None:
        with self._session_factory() as session:
            record = session.get(SupportCaseRecord, case_id)
            if record is None or record.organization_id != organization_id:
                return None
            return _record_to_support_case(record)

    def update_support_case(
        self,
        *,
        organization_id: str,
        case_id: str,
        actor_user_id: str | None,
        updates: dict[str, object],
    ) -> SupportCase | None:
        allowed = {
            "title",
            "description",
            "status",
            "priority",
            "category",
            "assigned_to_user_id",
            "assigned_team",
            "participant_ref",
            "participant_display",
            "participant_email",
            "participant_phone",
            "tags",
            "custom_fields",
            "case_metadata",
            "related_conversation_ids",
        }
        changed_keys = sorted(set(updates).intersection(allowed))
        if not changed_keys:
            return self.get_support_case(organization_id=organization_id, case_id=case_id)
        with self._session_factory.begin() as session:
            record = session.get(SupportCaseRecord, case_id)
            if record is None or record.organization_id != organization_id:
                return None
            for key in changed_keys:
                value = updates[key]
                if key == "tags":
                    record.tags_json = list(value or [])
                elif key == "custom_fields":
                    record.custom_fields_json = dict(value or {})
                elif key == "case_metadata":
                    record.case_metadata_json = dict(value or {})
                elif key == "related_conversation_ids":
                    record.related_conversation_ids_json = list(value or [])
                else:
                    setattr(record, key, value)
            record.updated_at = datetime.now(timezone.utc)
            self._append_case_event(
                session,
                case_id=record.case_id,
                organization_id=organization_id,
                event_type="case_updated",
                actor_user_id=actor_user_id,
                details={"fields": changed_keys},
                created_at=record.updated_at,
            )
        return self.get_support_case(organization_id=organization_id, case_id=case_id)

    def list_support_case_notes(
        self,
        *,
        organization_id: str,
        case_id: str,
    ) -> list[SupportCaseNote]:
        with self._session_factory() as session:
            statement = (
                select(SupportCaseNoteRecord)
                .where(
                    SupportCaseNoteRecord.organization_id == organization_id,
                    SupportCaseNoteRecord.case_id == case_id,
                )
                .order_by(SupportCaseNoteRecord.created_at.asc())
            )
            records = session.execute(statement).scalars().all()
        return [_record_to_support_case_note(record) for record in records]

    def add_support_case_note(
        self,
        *,
        organization_id: str,
        case_id: str,
        author_user_id: str | None,
        body: str,
        visibility: SupportCaseNoteVisibility = "internal",
    ) -> SupportCaseNote | None:
        now = datetime.now(timezone.utc)
        with self._session_factory.begin() as session:
            case = session.get(SupportCaseRecord, case_id)
            if case is None or case.organization_id != organization_id:
                return None
            note = SupportCaseNote(
                note_id=str(uuid4()),
                case_id=case_id,
                organization_id=organization_id,
                author_user_id=author_user_id,
                body=body.strip(),
                visibility=visibility,
                created_at=now,
            )
            session.add(_support_case_note_to_record(note))
            case.updated_at = now
            self._append_case_event(
                session,
                case_id=case_id,
                organization_id=organization_id,
                event_type="note_added",
                actor_user_id=author_user_id,
                details={"visibility": visibility},
                created_at=now,
            )
        return note

    def resolve_support_case(
        self,
        *,
        organization_id: str,
        case_id: str,
        actor_user_id: str,
        resolution_type: str,
        summary: str,
        details: str | None = None,
        requires_follow_up: bool = False,
        follow_up_at: datetime | None = None,
    ) -> SupportCase | None:
        now = datetime.now(timezone.utc)
        with self._session_factory.begin() as session:
            record = session.get(SupportCaseRecord, case_id)
            if record is None or record.organization_id != organization_id:
                return None
            record.status = "resolved"
            record.resolved_at = now
            record.updated_at = now
            record.resolution_json = SupportCaseResolution(
                resolution_type=resolution_type,
                summary=summary.strip(),
                details=details,
                resolved_by_user_id=actor_user_id,
                resolved_at=now,
                requires_follow_up=requires_follow_up,
                follow_up_at=follow_up_at,
            ).model_dump(mode="json")
            self._append_case_event(
                session,
                case_id=case_id,
                organization_id=organization_id,
                event_type="case_resolved",
                actor_user_id=actor_user_id,
                details={"resolution_type": resolution_type, "requires_follow_up": requires_follow_up},
                created_at=now,
            )
        return self.get_support_case(organization_id=organization_id, case_id=case_id)

    def close_support_case(
        self,
        *,
        organization_id: str,
        case_id: str,
        actor_user_id: str | None,
    ) -> SupportCase | None:
        now = datetime.now(timezone.utc)
        with self._session_factory.begin() as session:
            record = session.get(SupportCaseRecord, case_id)
            if record is None or record.organization_id != organization_id:
                return None
            record.status = "closed"
            record.closed_at = now
            record.updated_at = now
            self._append_case_event(
                session,
                case_id=case_id,
                organization_id=organization_id,
                event_type="case_closed",
                actor_user_id=actor_user_id,
                details={},
                created_at=now,
            )
        return self.get_support_case(organization_id=organization_id, case_id=case_id)

    def create_connection(
        self,
        *,
        organization_id: str,
        provider: ExternalTicketingProvider,
        display_name: str,
        auth_type: str,
        credentials_ref: str | None = None,
        provider_config: dict[str, object] | None = None,
        field_mappings: dict[str, object] | None = None,
        status_mappings: dict[str, object] | None = None,
        priority_mappings: dict[str, object] | None = None,
        default_queue: str | None = None,
    ) -> TicketingConnection:
        now = datetime.now(timezone.utc)
        connection = TicketingConnection(
            connection_id=str(uuid4()),
            organization_id=organization_id,
            provider=provider,
            display_name=display_name.strip(),
            status="pending",
            auth_type=auth_type.strip(),
            credentials_ref=credentials_ref,
            provider_config=dict(provider_config or {}),
            field_mappings=dict(field_mappings or {}),
            status_mappings=dict(status_mappings or {}),
            priority_mappings=dict(priority_mappings or {}),
            default_queue=default_queue,
            created_at=now,
            updated_at=now,
        )
        with self._session_factory.begin() as session:
            session.add(_ticketing_connection_to_record(connection))
        return connection

    def list_connections(self, *, organization_id: str) -> list[TicketingConnection]:
        with self._session_factory() as session:
            statement = (
                select(TicketingConnectionRecord)
                .where(TicketingConnectionRecord.organization_id == organization_id)
                .order_by(TicketingConnectionRecord.created_at.asc())
            )
            records = session.execute(statement).scalars().all()
        return [_record_to_ticketing_connection(record) for record in records]

    def get_connection(self, *, organization_id: str, connection_id: str) -> TicketingConnection | None:
        with self._session_factory() as session:
            record = session.get(TicketingConnectionRecord, connection_id)
            if record is None or record.organization_id != organization_id:
                return None
            return _record_to_ticketing_connection(record)

    def get_connection_by_id(self, *, connection_id: str) -> TicketingConnection | None:
        with self._session_factory() as session:
            record = session.get(TicketingConnectionRecord, connection_id)
            if record is None:
                return None
            return _record_to_ticketing_connection(record)

    def list_connection_activity(
        self,
        *,
        organization_id: str,
        connection_id: str,
        limit: int = 100,
    ) -> list[TicketingActivity]:
        with self._session_factory() as session:
            statement = (
                select(TicketingActivityRecord)
                .where(
                    TicketingActivityRecord.organization_id == organization_id,
                    TicketingActivityRecord.connection_id == connection_id,
                )
                .order_by(TicketingActivityRecord.updated_at.desc(), TicketingActivityRecord.created_at.desc())
                .limit(limit)
            )
            records = session.execute(statement).scalars().all()
        return [_record_to_ticketing_activity(record) for record in records]

    def list_retry_queue(
        self,
        *,
        organization_id: str,
        connection_id: str | None = None,
        limit: int = 100,
    ) -> list[TicketingActivity]:
        with self._session_factory() as session:
            statement = (
                select(TicketingActivityRecord)
                .where(
                    TicketingActivityRecord.organization_id == organization_id,
                    TicketingActivityRecord.retry_status.in_(["pending", "exhausted"]),
                )
                .order_by(TicketingActivityRecord.next_retry_at.asc().nullslast(), TicketingActivityRecord.updated_at.desc())
                .limit(limit)
            )
            if connection_id:
                statement = statement.where(TicketingActivityRecord.connection_id == connection_id)
            records = session.execute(statement).scalars().all()
        return [_record_to_ticketing_activity(record) for record in records]

    def update_connection(
        self,
        *,
        organization_id: str,
        connection_id: str,
        updates: dict[str, object],
    ) -> TicketingConnection | None:
        allowed = {
            "display_name",
            "status",
            "auth_type",
            "credentials_ref",
            "provider_config",
            "field_mappings",
            "status_mappings",
            "priority_mappings",
            "default_queue",
        }
        with self._session_factory.begin() as session:
            record = session.get(TicketingConnectionRecord, connection_id)
            if record is None or record.organization_id != organization_id:
                return None
            for key, value in updates.items():
                if key not in allowed:
                    continue
                if key.endswith("_config") or key.endswith("_mappings"):
                    setattr(record, f"{key}_json" if not key.endswith("_json") else key, dict(value or {}))
                else:
                    setattr(record, key, value)
            record.updated_at = datetime.now(timezone.utc)
        return self.get_connection(organization_id=organization_id, connection_id=connection_id)

    def health_check_connection(
        self,
        *,
        organization_id: str,
        connection_id: str,
        queue_retry: bool = True,
    ) -> TicketingConnection | None:
        connection = self.get_connection(organization_id=organization_id, connection_id=connection_id)
        if connection is None:
            return None
        started_at = perf_counter()
        with self._session_factory.begin() as session:
            record = session.get(TicketingConnectionRecord, connection_id)
            if record is None or record.organization_id != organization_id:
                return None
            if not record.credentials_ref:
                record.status = "error"
                self._append_activity(
                    session,
                    organization_id=organization_id,
                    connection_id=connection_id,
                    link_id=None,
                    provider=record.provider,
                    direction="outbound",
                    action="health_check",
                    status="error",
                    external_case_id=None,
                    request={},
                    response={},
                    error_message="missing credentials_ref",
                    attempt_count=1,
                    duration_ms=0,
                    retry_status="none",
                    next_retry_at=None,
                    last_attempted_at=datetime.now(timezone.utc),
                )
            else:
                try:
                    result = self._adapter_for_connection(connection).health_check()
                    record.status = "active"
                    self._append_activity(
                        session,
                        organization_id=organization_id,
                        connection_id=connection_id,
                        link_id=None,
                        provider=record.provider,
                        direction="outbound",
                        action="health_check",
                        status="success",
                        external_case_id=None,
                        request={},
                        response=dict(result),
                        error_message=None,
                        attempt_count=1,
                        duration_ms=int((perf_counter() - started_at) * 1000),
                        retry_status="none",
                        next_retry_at=None,
                        last_attempted_at=datetime.now(timezone.utc),
                    )
                except TicketingProviderError as exc:
                    record.status = "error"
                    retry_status, next_retry_at = self._schedule_retry(
                        retryable=queue_retry and exc.retryable,
                        attempt_count=1,
                    )
                    self._append_activity(
                        session,
                        organization_id=organization_id,
                        connection_id=connection_id,
                        link_id=None,
                        provider=record.provider,
                        direction="outbound",
                        action="health_check",
                        status="error",
                        external_case_id=None,
                        request={},
                        response={},
                        error_message=str(exc),
                        attempt_count=1,
                        duration_ms=int((perf_counter() - started_at) * 1000),
                        retry_status=retry_status,
                        next_retry_at=next_retry_at,
                        last_attempted_at=datetime.now(timezone.utc),
                    )
            record.updated_at = datetime.now(timezone.utc)
        return self.get_connection(organization_id=organization_id, connection_id=connection_id)

    def create_external_case_link(
        self,
        *,
        organization_id: str,
        provider: ExternalTicketingProvider,
        connection_id: str,
        external_case_id: str | None = None,
        external_case_key: str | None = None,
        external_case_url: str | None = None,
        external_case_status: str | None = None,
        external_case_priority: str | None = None,
        support_case_id: str | None = None,
        conversation_id: str | None = None,
        provider_payload_snapshot: dict[str, object] | None = None,
        title: str | None = None,
        description: str | None = None,
        participant_email: str | None = None,
        participant_display: str | None = None,
        tags: list[str] | None = None,
        queue_retry: bool = True,
    ) -> ExternalCaseLink:
        now = datetime.now(timezone.utc)
        connection = self.get_connection(organization_id=organization_id, connection_id=connection_id)
        if connection is None:
            raise KeyError(connection_id)
        remote_case: RemoteCase | None = None
        resolved_external_case_id = (external_case_id or "").strip() or None
        started_at = perf_counter()
        if resolved_external_case_id is None:
            support_case = None if support_case_id is None else self.get_support_case(
                organization_id=organization_id,
                case_id=support_case_id,
            )
            if support_case is None and not title:
                raise ValueError("support_case_id or explicit title is required to create a remote case")
            adapter = self._adapter_for_connection(connection)
            try:
                remote_case = adapter.create_case(
                    title=(title or support_case.title if support_case else title or "").strip(),
                    description=(description or support_case.description if support_case else description or "").strip(),
                    priority=(
                        external_case_priority
                        or (support_case.priority if support_case is not None else None)
                    ),
                    status=external_case_status,
                    participant_email=participant_email or (support_case.participant_email if support_case else None),
                    participant_display=participant_display or (support_case.participant_display if support_case else None),
                    tags=tags or (support_case.tags if support_case else []),
                    metadata=provider_payload_snapshot,
                )
            except TicketingProviderError as exc:
                retry_status, next_retry_at = self._schedule_retry(
                    retryable=queue_retry and exc.retryable,
                    attempt_count=1,
                )
                with self._session_factory.begin() as session:
                    self._append_activity(
                        session,
                        organization_id=organization_id,
                        connection_id=connection_id,
                        link_id=None,
                        provider=provider,
                        direction="outbound",
                        action="create_external_case",
                        status="error",
                        external_case_id=None,
                        request={
                            "support_case_id": support_case_id,
                            "conversation_id": conversation_id,
                            "title": title,
                            "description": description,
                            "external_case_status": external_case_status,
                            "external_case_priority": external_case_priority,
                            "participant_email": participant_email,
                            "participant_display": participant_display,
                            "tags": list(tags or []),
                            "provider_payload_snapshot": dict(provider_payload_snapshot or {}),
                        },
                        response={},
                        error_message=str(exc),
                        attempt_count=1,
                        duration_ms=int((perf_counter() - started_at) * 1000),
                        retry_status=retry_status,
                        next_retry_at=next_retry_at,
                        last_attempted_at=datetime.now(timezone.utc),
                    )
                raise
            resolved_external_case_id = remote_case.external_case_id
            external_case_key = remote_case.external_case_key
            external_case_url = remote_case.external_case_url
            external_case_status = remote_case.external_case_status
            external_case_priority = remote_case.external_case_priority
            provider_payload_snapshot = remote_case.payload
        link = ExternalCaseLink(
            link_id=str(uuid4()),
            organization_id=organization_id,
            provider=provider,
            connection_id=connection_id,
            external_case_id=resolved_external_case_id or "",
            external_case_key=external_case_key,
            external_case_url=external_case_url,
            external_case_status=external_case_status,
            external_case_priority=external_case_priority,
            support_case_id=support_case_id,
            conversation_id=conversation_id,
            sync_status="linked",
            last_synced_at=None,
            last_sync_error=None,
            provider_payload_snapshot=dict(provider_payload_snapshot or {}),
            comments=[],
            created_at=now,
            updated_at=now,
        )
        with self._session_factory.begin() as session:
            session.add(_external_case_link_to_record(link))
            self._append_activity(
                session,
                organization_id=organization_id,
                connection_id=connection_id,
                link_id=link.link_id,
                provider=provider,
                direction="outbound",
                action="create_external_case" if remote_case is not None else "link_external_case",
                status="success",
                external_case_id=link.external_case_id,
                request={
                    "support_case_id": support_case_id,
                    "conversation_id": conversation_id,
                    "title": title,
                    "description": description,
                    "external_case_status": external_case_status,
                    "external_case_priority": external_case_priority,
                    "participant_email": participant_email,
                    "participant_display": participant_display,
                    "tags": list(tags or []),
                    "provider_payload_snapshot": dict(provider_payload_snapshot or {}),
                },
                response={} if remote_case is None else dict(remote_case.payload),
                error_message=None,
                attempt_count=1,
                duration_ms=int((perf_counter() - started_at) * 1000),
                retry_status="none",
                next_retry_at=None,
                last_attempted_at=datetime.now(timezone.utc),
            )
        return link

    def search_external_case_links(
        self,
        *,
        organization_id: str,
        provider: str | None = None,
        connection_id: str | None = None,
        conversation_id: str | None = None,
        support_case_id: str | None = None,
        q: str | None = None,
    ) -> list[ExternalCaseLink]:
        with self._session_factory() as session:
            records = self._list_external_case_link_records(session, organization_id=organization_id)
            if provider:
                records = [record for record in records if record.provider == provider]
            if connection_id:
                records = [record for record in records if record.connection_id == connection_id]
            if conversation_id:
                records = [record for record in records if record.conversation_id == conversation_id]
            if support_case_id:
                records = [record for record in records if record.support_case_id == support_case_id]
            normalized_query = _normalize_search_query(q)
            if normalized_query:
                records = [record for record in records if self._external_case_matches_query(record, normalized_query)]
            records.sort(key=lambda item: (item.updated_at, item.external_case_id), reverse=True)
            return [_record_to_external_case_link(record) for record in records]

    def search_remote_cases(
        self,
        *,
        organization_id: str,
        connection_id: str,
        query: str,
        limit: int = 20,
    ) -> list[ExternalCaseLink]:
        connection = self.get_connection(organization_id=organization_id, connection_id=connection_id)
        if connection is None:
            return []
        started_at = perf_counter()
        adapter = self._adapter_for_connection(connection)
        try:
            remote_cases = adapter.search_cases(query=query, limit=limit)
        except TicketingProviderError as exc:
            with self._session_factory.begin() as session:
                self._append_activity(
                    session,
                    organization_id=organization_id,
                    connection_id=connection_id,
                    link_id=None,
                    provider=connection.provider,
                    direction="outbound",
                    action="search_remote_cases",
                    status="error",
                    external_case_id=None,
                    request={"query": query, "limit": limit},
                    response={},
                    error_message=str(exc),
                    attempt_count=1,
                    duration_ms=int((perf_counter() - started_at) * 1000),
                    retry_status="none",
                    next_retry_at=None,
                    last_attempted_at=datetime.now(timezone.utc),
                )
            raise
        with self._session_factory.begin() as session:
            self._append_activity(
                session,
                organization_id=organization_id,
                connection_id=connection_id,
                link_id=None,
                provider=connection.provider,
                direction="outbound",
                action="search_remote_cases",
                status="success",
                external_case_id=None,
                request={"query": query, "limit": limit},
                response={"count": len(remote_cases)},
                error_message=None,
                attempt_count=1,
                duration_ms=int((perf_counter() - started_at) * 1000),
                retry_status="none",
                next_retry_at=None,
                last_attempted_at=datetime.now(timezone.utc),
            )
        return [
            ExternalCaseLink(
                link_id=f"remote:{connection_id}:{item.external_case_id}",
                organization_id=organization_id,
                provider=connection.provider,
                connection_id=connection_id,
                external_case_id=item.external_case_id,
                external_case_key=item.external_case_key,
                external_case_url=item.external_case_url,
                external_case_status=item.external_case_status,
                external_case_priority=item.external_case_priority,
                support_case_id=None,
                conversation_id=None,
                sync_status="synced",
                last_synced_at=None,
                last_sync_error=None,
                provider_payload_snapshot=dict(item.payload),
                comments=[],
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            for item in remote_cases
        ]

    def add_external_case_comment(
        self,
        *,
        organization_id: str,
        link_id: str,
        author_user_id: str | None,
        body: str,
        visibility: str = "internal",
        queue_retry: bool = True,
    ) -> ExternalCaseLink | None:
        link = self.get_external_case_link(organization_id=organization_id, link_id=link_id)
        if link is None:
            return None
        connection = self.get_connection(organization_id=organization_id, connection_id=link.connection_id)
        if connection is None:
            return None
        started_at = perf_counter()
        adapter = self._adapter_for_connection(connection)
        try:
            result = adapter.add_comment(
                external_case_id=link.external_case_id,
                body=body,
                visibility=visibility,
            )
        except TicketingProviderError as exc:
            retry_status, next_retry_at = self._schedule_retry(
                retryable=queue_retry and exc.retryable,
                attempt_count=1,
            )
            with self._session_factory.begin() as session:
                self._append_activity(
                    session,
                    organization_id=organization_id,
                    connection_id=link.connection_id,
                    link_id=link.link_id,
                    provider=link.provider,
                    direction="outbound",
                    action="add_comment",
                    status="error",
                    external_case_id=link.external_case_id,
                    request={"body": body.strip(), "visibility": visibility},
                    response={},
                    error_message=str(exc),
                    attempt_count=1,
                    duration_ms=int((perf_counter() - started_at) * 1000),
                    retry_status=retry_status,
                    next_retry_at=next_retry_at,
                    last_attempted_at=datetime.now(timezone.utc),
                )
            raise
        with self._session_factory.begin() as session:
            record = session.get(ExternalCaseLinkRecord, link_id)
            if record is None or record.organization_id != organization_id:
                return None
            snapshot = dict(record.provider_payload_snapshot_json or {})
            comments = list(snapshot.get("comments") or [])
            comments.append(
                ExternalCaseComment(
                    comment_id=str(uuid4()),
                    body=body.strip(),
                    visibility=visibility,
                    created_at=datetime.now(timezone.utc),
                    author_user_id=author_user_id,
                ).model_dump(mode="json")
            )
            snapshot["comments"] = comments
            snapshot["last_comment_result"] = dict(result)
            record.provider_payload_snapshot_json = snapshot
            record.sync_status = "synced"
            record.last_synced_at = datetime.now(timezone.utc)
            record.last_sync_error = None
            record.updated_at = datetime.now(timezone.utc)
            self._append_activity(
                session,
                organization_id=organization_id,
                connection_id=record.connection_id,
                link_id=record.link_id,
                provider=record.provider,
                direction="outbound",
                action="add_comment",
                status="success",
                external_case_id=record.external_case_id,
                request={"body": body.strip(), "visibility": visibility},
                response=dict(result),
                error_message=None,
                attempt_count=1,
                duration_ms=int((perf_counter() - started_at) * 1000),
                retry_status="none",
                next_retry_at=None,
                last_attempted_at=datetime.now(timezone.utc),
            )
        return self.get_external_case_link(organization_id=organization_id, link_id=link_id)

    def transition_external_case(
        self,
        *,
        organization_id: str,
        link_id: str,
        status_value: str,
        queue_retry: bool = True,
    ) -> ExternalCaseLink | None:
        link = self.get_external_case_link(organization_id=organization_id, link_id=link_id)
        if link is None:
            return None
        connection = self.get_connection(organization_id=organization_id, connection_id=link.connection_id)
        if connection is None:
            return None
        started_at = perf_counter()
        try:
            remote_case = self._adapter_for_connection(connection).transition_case(
                external_case_id=link.external_case_id,
                status_value=status_value,
            )
        except TicketingProviderError as exc:
            retry_status, next_retry_at = self._schedule_retry(
                retryable=queue_retry and exc.retryable,
                attempt_count=1,
            )
            with self._session_factory.begin() as session:
                self._append_activity(
                    session,
                    organization_id=organization_id,
                    connection_id=link.connection_id,
                    link_id=link.link_id,
                    provider=link.provider,
                    direction="outbound",
                    action="transition_case",
                    status="error",
                    external_case_id=link.external_case_id,
                    request={"status": status_value},
                    response={},
                    error_message=str(exc),
                    attempt_count=1,
                    duration_ms=int((perf_counter() - started_at) * 1000),
                    retry_status=retry_status,
                    next_retry_at=next_retry_at,
                    last_attempted_at=datetime.now(timezone.utc),
                )
            raise
        with self._session_factory.begin() as session:
            record = session.get(ExternalCaseLinkRecord, link_id)
            if record is None or record.organization_id != organization_id:
                return None
            record.external_case_status = remote_case.external_case_status or status_value.strip()
            record.external_case_priority = remote_case.external_case_priority
            record.external_case_url = remote_case.external_case_url
            record.external_case_key = remote_case.external_case_key
            record.provider_payload_snapshot_json = dict(remote_case.payload)
            record.sync_status = "synced"
            record.last_synced_at = datetime.now(timezone.utc)
            record.last_sync_error = None
            record.updated_at = datetime.now(timezone.utc)
            self._append_activity(
                session,
                organization_id=organization_id,
                connection_id=record.connection_id,
                link_id=record.link_id,
                provider=record.provider,
                direction="outbound",
                action="transition_case",
                status="success",
                external_case_id=record.external_case_id,
                request={"status": status_value},
                response=dict(remote_case.payload),
                error_message=None,
                attempt_count=1,
                duration_ms=int((perf_counter() - started_at) * 1000),
                retry_status="none",
                next_retry_at=None,
                last_attempted_at=datetime.now(timezone.utc),
            )
        return self.get_external_case_link(organization_id=organization_id, link_id=link_id)

    def sync_external_case(
        self,
        *,
        organization_id: str,
        link_id: str,
        queue_retry: bool = True,
    ) -> ExternalCaseLink | None:
        link = self.get_external_case_link(organization_id=organization_id, link_id=link_id)
        if link is None:
            return None
        connection = self.get_connection(organization_id=organization_id, connection_id=link.connection_id)
        if connection is None:
            return None
        now = datetime.now(timezone.utc)
        started_at = perf_counter()
        try:
            remote_case = self._adapter_for_connection(connection).fetch_case(link.external_case_id)
        except TicketingProviderError as exc:
            retry_status, next_retry_at = self._schedule_retry(
                retryable=queue_retry and exc.retryable,
                attempt_count=1,
            )
            with self._session_factory.begin() as session:
                self._append_activity(
                    session,
                    organization_id=organization_id,
                    connection_id=link.connection_id,
                    link_id=link.link_id,
                    provider=link.provider,
                    direction="outbound",
                    action="sync_case",
                    status="error",
                    external_case_id=link.external_case_id,
                    request={},
                    response={},
                    error_message=str(exc),
                    attempt_count=1,
                    duration_ms=int((perf_counter() - started_at) * 1000),
                    retry_status=retry_status,
                    next_retry_at=next_retry_at,
                    last_attempted_at=datetime.now(timezone.utc),
                )
            raise
        with self._session_factory.begin() as session:
            record = session.get(ExternalCaseLinkRecord, link_id)
            if record is None or record.organization_id != organization_id:
                return None
            if remote_case is None:
                record.sync_status = "error"
                record.last_synced_at = now
                record.last_sync_error = "remote case not found"
                self._append_activity(
                    session,
                    organization_id=organization_id,
                    connection_id=record.connection_id,
                    link_id=record.link_id,
                    provider=record.provider,
                    direction="outbound",
                    action="sync_case",
                    status="error",
                    external_case_id=record.external_case_id,
                    request={},
                    response={},
                    error_message="remote case not found",
                    attempt_count=1,
                    duration_ms=int((perf_counter() - started_at) * 1000),
                    retry_status="none",
                    next_retry_at=None,
                    last_attempted_at=datetime.now(timezone.utc),
                )
            else:
                record.external_case_key = remote_case.external_case_key
                record.external_case_url = remote_case.external_case_url
                record.external_case_status = remote_case.external_case_status
                record.external_case_priority = remote_case.external_case_priority
                record.provider_payload_snapshot_json = dict(remote_case.payload)
                record.sync_status = "synced"
                record.last_synced_at = now
                record.last_sync_error = None
                self._append_activity(
                    session,
                    organization_id=organization_id,
                    connection_id=record.connection_id,
                    link_id=record.link_id,
                    provider=record.provider,
                    direction="outbound",
                    action="sync_case",
                    status="success",
                    external_case_id=record.external_case_id,
                    request={},
                    response=dict(remote_case.payload),
                    error_message=None,
                    attempt_count=1,
                    duration_ms=int((perf_counter() - started_at) * 1000),
                    retry_status="none",
                    next_retry_at=None,
                    last_attempted_at=datetime.now(timezone.utc),
                )
            record.updated_at = now
        return self.get_external_case_link(organization_id=organization_id, link_id=link_id)

    def retry_activity(
        self,
        *,
        organization_id: str,
        activity_id: str,
    ) -> TicketingActivity | None:
        now = datetime.now(timezone.utc)
        activity = self._claim_retry_activity(
            organization_id=organization_id,
            activity_id=activity_id,
            claimed_at=now,
            force=True,
        )
        if activity is None:
            return None
        if activity.retry_status != "in_progress":
            return activity

        try:
            self._replay_activity(activity)
        except TicketingProviderError as exc:
            with self._session_factory.begin() as session:
                record = session.get(TicketingActivityRecord, activity_id)
                if record is None or record.organization_id != organization_id:
                    return None
                retry_status, next_retry_at = self._schedule_retry(
                    retryable=exc.retryable,
                    attempt_count=int(record.attempt_count or 1) + 1,
                )
                record.retry_status = retry_status
                record.next_retry_at = next_retry_at
                record.last_attempted_at = now
                record.attempt_count = int(record.attempt_count or 1) + 1
                record.status = "error"
                record.error_message = str(exc)
                record.updated_at = now
            return self.get_activity(organization_id=organization_id, activity_id=activity_id)

        with self._session_factory.begin() as session:
            record = session.get(TicketingActivityRecord, activity_id)
            if record is None or record.organization_id != organization_id:
                return None
            record.retry_status = "succeeded"
            record.next_retry_at = None
            record.last_attempted_at = now
            record.attempt_count = int(record.attempt_count or 1) + 1
            record.status = "retried"
            record.updated_at = now
            record.error_message = None
        return self.get_activity(organization_id=organization_id, activity_id=activity_id)

    def process_pending_retries(
        self,
        *,
        organization_id: str,
        connection_id: str | None = None,
        limit: int = 25,
        force: bool = False,
    ) -> list[TicketingActivity]:
        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            due_for_claim = or_(
                TicketingActivityRecord.next_retry_at.is_(None),
                TicketingActivityRecord.next_retry_at <= now,
            )
            statement = (
                select(TicketingActivityRecord.activity_id)
                .where(
                    TicketingActivityRecord.organization_id == organization_id,
                )
                .order_by(TicketingActivityRecord.next_retry_at.asc().nullslast(), TicketingActivityRecord.updated_at.asc())
                .limit(limit)
            )
            if connection_id:
                statement = statement.where(TicketingActivityRecord.connection_id == connection_id)
            if force:
                statement = statement.where(
                    or_(
                        TicketingActivityRecord.retry_status == "pending",
                        and_(
                            TicketingActivityRecord.retry_status == "in_progress",
                            due_for_claim,
                        ),
                    )
                )
            else:
                statement = statement.where(
                    or_(
                        and_(
                            TicketingActivityRecord.retry_status == "pending",
                            due_for_claim,
                        ),
                        and_(
                            TicketingActivityRecord.retry_status == "in_progress",
                            due_for_claim,
                        ),
                    )
                )
            activity_ids = list(session.execute(statement).scalars().all())
        results: list[TicketingActivity] = []
        for activity_id in activity_ids:
            retried = self.retry_activity(organization_id=organization_id, activity_id=activity_id)
            if retried is not None and retried.retry_status != "in_progress":
                results.append(retried)
        return results

    def get_activity(self, *, organization_id: str, activity_id: str) -> TicketingActivity | None:
        with self._session_factory() as session:
            record = session.get(TicketingActivityRecord, activity_id)
            if record is None or record.organization_id != organization_id:
                return None
            return _record_to_ticketing_activity(record)

    def get_external_case_link(self, *, organization_id: str, link_id: str) -> ExternalCaseLink | None:
        with self._session_factory() as session:
            record = session.get(ExternalCaseLinkRecord, link_id)
            if record is None or record.organization_id != organization_id:
                return None
            return _record_to_external_case_link(record)

    def _adapter_for_connection(self, connection: TicketingConnection) -> TicketingAdapter:
        return self._adapter_builder(
            ProviderConnectionConfig(
                connection_id=connection.connection_id,
                provider=connection.provider,
                auth_type=connection.auth_type,
                credentials_ref=connection.credentials_ref,
                provider_config=dict(connection.provider_config),
                field_mappings=dict(connection.field_mappings),
                status_mappings=dict(connection.status_mappings),
                priority_mappings=dict(connection.priority_mappings),
                default_queue=connection.default_queue,
            )
        )

    def _schedule_retry(
        self,
        *,
        retryable: bool,
        attempt_count: int,
    ) -> tuple[TicketingRetryStatus, datetime | None]:
        if not retryable:
            return "none", None
        if attempt_count >= self._default_retry_attempts:
            return "exhausted", None
        delay_seconds = min(30 * max(1, 2 ** max(0, attempt_count - 1)), 15 * 60)
        return "pending", datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)

    def _can_retry_activity(self, activity: TicketingActivity) -> bool:
        return (
            activity.direction == "outbound"
            and activity.action in self._retryable_outbound_actions
            and activity.connection_id is not None
            and activity.status in {"error", "retried"}
            and activity.retry_status in {"pending", "in_progress", "exhausted"}
        )

    def _claim_retry_activity(
        self,
        *,
        organization_id: str,
        activity_id: str,
        claimed_at: datetime,
        force: bool,
    ) -> TicketingActivity | None:
        with self._session_factory.begin() as session:
            record = session.execute(
                select(TicketingActivityRecord)
                .where(TicketingActivityRecord.activity_id == activity_id)
                .with_for_update()
            ).scalars().first()
            if record is None or record.organization_id != organization_id:
                return None
            activity = _record_to_ticketing_activity(record)
            if not self._can_retry_activity(activity):
                return activity
            lease_active = record.retry_status == "in_progress" and (
                record.next_retry_at is None or record.next_retry_at > claimed_at
            )
            pending_not_due = (
                record.retry_status == "pending"
                and not force
                and record.next_retry_at is not None
                and record.next_retry_at > claimed_at
            )
            if record.retry_status == "exhausted" and not force:
                return activity
            if lease_active or pending_not_due:
                return activity
            record.retry_status = "in_progress"
            record.next_retry_at = claimed_at + self._retry_claim_lease
            record.last_attempted_at = claimed_at
            record.updated_at = claimed_at
            session.flush()
            return _record_to_ticketing_activity(record)

    def _replay_activity(self, activity: TicketingActivity) -> None:
        if activity.action == "health_check":
            self.health_check_connection(
                organization_id=activity.organization_id,
                connection_id=activity.connection_id or "",
                queue_retry=False,
            )
            return
        if activity.action == "create_external_case":
            self.create_external_case_link(
                organization_id=activity.organization_id,
                provider=activity.provider,  # type: ignore[arg-type]
                connection_id=activity.connection_id or "",
                external_case_id=self._coerce_optional_str(activity.request.get("external_case_id")),
                external_case_key=self._coerce_optional_str(activity.request.get("external_case_key")),
                external_case_url=self._coerce_optional_str(activity.request.get("external_case_url")),
                external_case_status=self._coerce_optional_str(activity.request.get("external_case_status")),
                external_case_priority=self._coerce_optional_str(activity.request.get("external_case_priority")),
                support_case_id=self._coerce_optional_str(activity.request.get("support_case_id")),
                conversation_id=self._coerce_optional_str(activity.request.get("conversation_id")),
                provider_payload_snapshot=self._coerce_object_dict(activity.request.get("provider_payload_snapshot")),
                title=self._coerce_optional_str(activity.request.get("title")),
                description=self._coerce_optional_str(activity.request.get("description")),
                participant_email=self._coerce_optional_str(activity.request.get("participant_email")),
                participant_display=self._coerce_optional_str(activity.request.get("participant_display")),
                tags=self._coerce_string_list(activity.request.get("tags")),
                queue_retry=False,
            )
            return
        if activity.action == "add_comment":
            if activity.link_id is None:
                raise TicketingProviderError("missing external case link for retry", provider=activity.provider)
            self.add_external_case_comment(
                organization_id=activity.organization_id,
                link_id=activity.link_id,
                author_user_id=None,
                body=self._coerce_optional_str(activity.request.get("body")) or "",
                visibility=self._coerce_optional_str(activity.request.get("visibility")) or "internal",
                queue_retry=False,
            )
            return
        if activity.action == "transition_case":
            if activity.link_id is None:
                raise TicketingProviderError("missing external case link for retry", provider=activity.provider)
            self.transition_external_case(
                organization_id=activity.organization_id,
                link_id=activity.link_id,
                status_value=self._coerce_optional_str(activity.request.get("status")) or "",
                queue_retry=False,
            )
            return
        if activity.action == "sync_case":
            if activity.link_id is None:
                raise TicketingProviderError("missing external case link for retry", provider=activity.provider)
            self.sync_external_case(
                organization_id=activity.organization_id,
                link_id=activity.link_id,
                queue_retry=False,
            )
            return
        raise TicketingProviderError(
            f"unsupported retry action: {activity.action}",
            provider=activity.provider,
        )

    @staticmethod
    def _coerce_optional_str(value: object) -> str | None:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _coerce_object_dict(value: object) -> dict[str, object]:
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _coerce_string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                items.append(text)
        return items

    def _append_activity(
        self,
        session: Session,
        *,
        organization_id: str,
        connection_id: str | None,
        link_id: str | None,
        provider: str,
        direction: str,
        action: str,
        status: str,
        external_case_id: str | None,
        request: dict[str, object],
        response: dict[str, object],
        error_message: str | None,
        attempt_count: int,
        duration_ms: int | None,
        retry_status: TicketingRetryStatus,
        next_retry_at: datetime | None,
        last_attempted_at: datetime | None,
    ) -> TicketingActivity:
        activity = TicketingActivity(
            activity_id=str(uuid4()),
            organization_id=organization_id,
            connection_id=connection_id,
            link_id=link_id,
            provider=provider,
            direction=direction,  # type: ignore[arg-type]
            action=action,
            status=status,
            external_case_id=external_case_id,
            attempt_count=attempt_count,
            duration_ms=duration_ms,
            request=dict(request),
            response=dict(response),
            error_message=error_message,
            retry_status=retry_status,
            next_retry_at=next_retry_at,
            last_attempted_at=last_attempted_at,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(_ticketing_activity_to_record(activity))
        return activity

    def process_connection_webhook(
        self,
        *,
        connection_id: str,
        provider: str,
        payload: dict[str, object],
        headers: dict[str, str] | None = None,
    ) -> TicketingActivity | None:
        connection = self.get_connection_by_id(connection_id=connection_id)
        if connection is None or connection.provider != provider:
            return None
        organization_id = connection.organization_id
        started_at = perf_counter()
        adapter = self._adapter_for_connection(connection)
        try:
            result = adapter.parse_webhook(payload=payload, headers=headers)
        except TicketingProviderError as exc:
            with self._session_factory.begin() as session:
                return self._append_activity(
                    session,
                    organization_id=organization_id,
                    connection_id=connection_id,
                    link_id=None,
                    provider=provider,
                    direction="inbound",
                    action="webhook_parse",
                    status="error",
                    external_case_id=None,
                    request=dict(payload),
                    response={},
                    error_message=str(exc),
                    attempt_count=1,
                    duration_ms=int((perf_counter() - started_at) * 1000),
                    retry_status="none",
                    next_retry_at=None,
                    last_attempted_at=datetime.now(timezone.utc),
                )
        with self._session_factory.begin() as session:
            link_record = None
            if result.external_case_id:
                statement = (
                    select(ExternalCaseLinkRecord)
                    .where(
                        ExternalCaseLinkRecord.organization_id == organization_id,
                        ExternalCaseLinkRecord.connection_id == connection_id,
                        ExternalCaseLinkRecord.external_case_id == result.external_case_id,
                    )
                    .limit(1)
                )
                link_record = session.execute(statement).scalars().first()
            if link_record is not None:
                if result.external_case_key:
                    link_record.external_case_key = result.external_case_key
                if result.external_case_url:
                    link_record.external_case_url = result.external_case_url
                if result.external_case_status:
                    link_record.external_case_status = result.external_case_status
                if result.external_case_priority:
                    link_record.external_case_priority = result.external_case_priority
                snapshot = dict(link_record.provider_payload_snapshot_json or {})
                snapshot.update(dict(result.payload_snapshot))
                comments = list(snapshot.get("comments") or [])
                comments.extend(result.comments)
                snapshot["comments"] = comments
                link_record.provider_payload_snapshot_json = snapshot
                link_record.sync_status = "synced"
                link_record.last_synced_at = datetime.now(timezone.utc)
                link_record.last_sync_error = None
                link_record.updated_at = datetime.now(timezone.utc)
            activity = self._append_activity(
                session,
                organization_id=organization_id,
                connection_id=connection_id,
                link_id=None if link_record is None else link_record.link_id,
                provider=provider,
                direction="inbound",
                action=result.event_type,
                status="processed",
                external_case_id=result.external_case_id,
                request=dict(payload),
                response=dict(result.payload_snapshot),
                error_message=None,
                attempt_count=1,
                duration_ms=int((perf_counter() - started_at) * 1000),
                retry_status="none",
                next_retry_at=None,
                last_attempted_at=datetime.now(timezone.utc),
            )
        return activity

    def _list_conversation_records(
        self,
        session: Session,
        *,
        organization_id: str,
        handler_id: str | None,
        channel: str | None,
        outcome: str | None,
        days: int | None,
    ) -> list[ConversationRecord]:
        statement = select(ConversationRecord).where(ConversationRecord.organization_id == organization_id)
        if handler_id:
            statement = statement.where(ConversationRecord.agent_id == handler_id)
        if channel:
            statement = statement.where(ConversationRecord.channel == channel)
        if outcome:
            statement = statement.where(ConversationRecord.outcome == outcome)
        if days is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            statement = statement.where(ConversationRecord.started_at >= cutoff)
        statement = statement.order_by(ConversationRecord.started_at.desc())
        return session.execute(statement).scalars().all()

    def _agent_name_map(self, session: Session, *, organization_id: str) -> dict[str, str]:
        statement = select(AgentRecord).where(
            or_(
                AgentRecord.organization_id == organization_id,
                AgentRecord.organization_id.is_(None),
            )
        )
        records = session.execute(statement).scalars().all()
        # Shared agents remain visible to tenant dashboards, while tenant-owned
        # records override shared names when both exist for the same agent id.
        mapping: dict[str, str] = {}
        for record in sorted(records, key=lambda item: item.organization_id is not None):
            mapping[record.agent_id] = record.name
        return mapping

    def _traces_by_conversation(
        self,
        session: Session,
        *,
        conversation_ids: list[str],
        organization_id: str,
    ) -> dict[str, list[TurnTraceRecord]]:
        if not conversation_ids:
            return {}
        statement = (
            select(TurnTraceRecord)
            .where(
                TurnTraceRecord.organization_id == organization_id,
                TurnTraceRecord.conversation_id.in_(conversation_ids),
            )
            .order_by(TurnTraceRecord.recorded_at.asc())
        )
        items: dict[str, list[TurnTraceRecord]] = {}
        for record in session.execute(statement).scalars().all():
            items.setdefault(record.conversation_id, []).append(record)
        return items

    def _realtime_events_by_conversation(
        self,
        session: Session,
        *,
        conversation_ids: list[str],
        organization_id: str,
    ) -> dict[str, list[RealtimeEventRecord]]:
        if not conversation_ids:
            return {}
        statement = (
            select(RealtimeEventRecord)
            .where(
                RealtimeEventRecord.organization_id == organization_id,
                RealtimeEventRecord.conversation_id.in_(conversation_ids),
            )
            .order_by(RealtimeEventRecord.conversation_sequence.asc())
        )
        items: dict[str, list[RealtimeEventRecord]] = {}
        for record in session.execute(statement).scalars().all():
            items.setdefault(record.conversation_id, []).append(record)
        return items

    def _realtime_sessions_by_conversation(
        self,
        session: Session,
        *,
        conversation_ids: list[str],
        organization_id: str,
    ) -> dict[str, list[RealtimeSessionRecord]]:
        if not conversation_ids:
            return {}
        statement = (
            select(RealtimeSessionRecord)
            .where(
                RealtimeSessionRecord.organization_id == organization_id,
                RealtimeSessionRecord.conversation_id.in_(conversation_ids),
            )
            .order_by(RealtimeSessionRecord.started_at.asc())
        )
        items: dict[str, list[RealtimeSessionRecord]] = {}
        for record in session.execute(statement).scalars().all():
            items.setdefault(record.conversation_id, []).append(record)
        return items

    def _list_support_case_records(self, session: Session, *, organization_id: str) -> list[SupportCaseRecord]:
        statement = (
            select(SupportCaseRecord)
            .where(SupportCaseRecord.organization_id == organization_id)
            .order_by(SupportCaseRecord.updated_at.desc())
        )
        return session.execute(statement).scalars().all()

    def _list_external_case_link_records(self, session: Session, *, organization_id: str) -> list[ExternalCaseLinkRecord]:
        statement = (
            select(ExternalCaseLinkRecord)
            .where(ExternalCaseLinkRecord.organization_id == organization_id)
            .order_by(ExternalCaseLinkRecord.updated_at.desc())
        )
        return session.execute(statement).scalars().all()

    def _build_dashboard_item(
        self,
        record: ConversationRecord,
        *,
        agent_names: dict[str, str],
        traces: list[TurnTraceRecord],
        support_cases: list[SupportCaseRecord],
        external_links: list[ExternalCaseLinkRecord],
    ) -> TicketDashboardItem:
        participant_display, participant_ref = _participant_details(record)
        duration_seconds = _conversation_duration_seconds(record)
        message_count = _message_count(record, traces)
        summary = _conversation_summary(record, traces)
        linked_case_records = [
            case
            for case in support_cases
            if case.primary_conversation_id == record.conversation_id
            or record.conversation_id in list(case.related_conversation_ids_json or [])
        ]
        linked_external_records = [link for link in external_links if link.conversation_id == record.conversation_id]
        return TicketDashboardItem(
            conversation_id=record.conversation_id,
            organization_id=record.organization_id,
            handler_id=record.agent_id,
            handler_name=agent_names.get(record.agent_id, record.agent_id),
            channel=record.channel,
            participant_display=participant_display,
            participant_ref=participant_ref,
            status=record.status,
            outcome=record.outcome,
            outcome_reason=str((record.metadata_json or {}).get("outcome_reason") or "") or None,
            started_at=record.started_at,
            ended_at=record.ended_at,
            duration_seconds=duration_seconds,
            message_count=message_count,
            sentiment_score=_sentiment_score(record, traces),
            has_handoff=record.outcome == "transferred" or bool((record.metadata_json or {}).get("handoff_target")),
            has_tool_failures=_has_tool_failures(traces),
            last_activity_at=record.ended_at or record.updated_at,
            summary=summary,
            tags=list((record.metadata_json or {}).get("tags") or []),
            linked_support_case_count=len(linked_case_records),
            linked_external_cases=[
                LinkedExternalCaseSummary(
                    link_id=link.link_id,
                    provider=link.provider,
                    external_case_key=link.external_case_key,
                    external_case_url=link.external_case_url,
                    external_case_status=link.external_case_status,
                    sync_status=link.sync_status,
                )
                for link in linked_external_records[:3]
            ],
        )

    def _build_transcript(
        self,
        record: ConversationRecord,
        *,
        traces: list[TurnTraceRecord],
        realtime_events: list[RealtimeEventRecord],
    ) -> list[TicketTranscriptEntry]:
        items: list[TicketTranscriptEntry] = []
        for event in realtime_events:
            if event.family != "message":
                continue
            payload = dict(event.payload_json or {})
            text = str(payload.get("text") or "").strip()
            if not text:
                continue
            role = "assistant"
            if event.name == "user_accepted":
                role = "user"
            elif payload.get("role") == "system":
                role = "system"
            items.append(
                TicketTranscriptEntry(
                    entry_id=event.event_id,
                    role=role,  # type: ignore[arg-type]
                    channel=_as_optional_text(payload.get("channel")) or record.channel,
                    text=text,
                    source=f"realtime:{event.name}",
                    recorded_at=event.created_at,
                    metadata={
                        "conversation_sequence": event.conversation_sequence,
                        "trace_id": payload.get("trace_id"),
                        "turn_id": payload.get("turn_id"),
                    },
                )
            )
        if items:
            return items
        fallback: list[TicketTranscriptEntry] = []
        for trace in traces:
            for index, message in enumerate(list(trace.emitted_messages_json or [])):
                text = str(message.get("text") or "").strip()
                if not text:
                    continue
                fallback.append(
                    TicketTranscriptEntry(
                        entry_id=f"{trace.trace_id}:{index}",
                        role=str(message.get("role") or "assistant"),  # type: ignore[arg-type]
                        channel=record.channel,
                        text=text,
                        source="trace:emitted_message",
                        recorded_at=trace.recorded_at,
                        metadata={"trace_id": trace.trace_id, "turn_id": trace.turn_id},
                    )
                )
        return fallback

    def _build_evidence(
        self,
        *,
        traces: list[TurnTraceRecord],
        realtime_events: list[RealtimeEventRecord],
        realtime_sessions: list[RealtimeSessionRecord],
    ) -> list[TicketEvidenceEntry]:
        items: list[TicketEvidenceEntry] = []
        for session in realtime_sessions:
            items.append(
                TicketEvidenceEntry(
                    evidence_id=session.realtime_session_id,
                    kind="session",
                    label=f"{session.surface} / {session.channel}",
                    status=session.status,
                    detail=session.provider or session.participant_identity,
                    recorded_at=session.started_at,
                    metadata={
                        "provider": session.provider,
                        "external_session_key": session.external_session_key,
                        "provider_session_id": session.provider_session_id,
                    },
                )
            )
        for trace in traces:
            for index, tool_call in enumerate(list(trace.tool_calls_json or [])):
                items.append(
                    TicketEvidenceEntry(
                        evidence_id=f"{trace.trace_id}:tool:{index}",
                        kind="tool_call",
                        label=str(tool_call.get("tool_ref") or "tool"),
                        status=_as_optional_text(tool_call.get("status")),
                        detail=_as_optional_text(tool_call.get("reason")),
                        recorded_at=trace.recorded_at,
                        metadata=dict(tool_call.get("payload") or {}),
                    )
                )
            for index, fact_update in enumerate(list(trace.fact_updates_json or [])):
                items.append(
                    TicketEvidenceEntry(
                        evidence_id=f"{trace.trace_id}:fact:{index}",
                        kind="fact_update",
                        label=str(fact_update.get("name") or "fact"),
                        status=_as_optional_text(fact_update.get("source")),
                        detail=_stringify_scalar(fact_update.get("value")),
                        recorded_at=trace.recorded_at,
                        metadata={},
                    )
                )
            for index, semantic_event in enumerate(list(trace.semantic_events_json or [])):
                payload = semantic_event.get("payload")
                items.append(
                    TicketEvidenceEntry(
                        evidence_id=f"{trace.trace_id}:semantic:{index}",
                        kind="semantic_event",
                        label=f"{semantic_event.get('family') or 'event'}: {semantic_event.get('name') or 'unknown'}",
                        status=_as_optional_text(semantic_event.get("source")),
                        detail=_stringify_scalar(payload if not isinstance(payload, dict) else payload.get("value")),
                        recorded_at=trace.recorded_at,
                        metadata=dict(payload or {}) if isinstance(payload, dict) else {},
                    )
                )
        for event in realtime_events:
            if event.family == "conversation" and event.name == "step_changed":
                items.append(
                    TicketEvidenceEntry(
                        evidence_id=event.event_id,
                        kind="event",
                        label="step change",
                        status=None,
                        detail=f"{event.payload_json.get('step_before')} -> {event.payload_json.get('step_after')}",
                        recorded_at=event.created_at,
                        metadata={"sequence": event.conversation_sequence},
                    )
                )
        items.sort(key=lambda item: (item.recorded_at, item.kind, item.label))
        return items

    def _sort_dashboard_items(
        self,
        items: list[TicketDashboardItem],
        *,
        sort_by: TicketDashboardSortField,
        sort_dir: TicketDashboardSortDirection,
    ) -> list[TicketDashboardItem]:
        reverse = sort_dir == "desc"
        if sort_by == "duration_seconds":
            key = lambda item: item.duration_seconds
        elif sort_by == "sentiment_score":
            key = lambda item: -2.0 if item.sentiment_score is None else item.sentiment_score
        elif sort_by == "outcome":
            key = lambda item: item.outcome or ""
        elif sort_by == "message_count":
            key = lambda item: item.message_count
        else:
            key = lambda item: item.started_at
        return sorted(items, key=key, reverse=reverse)

    def _build_dashboard_summary(self, items: list[TicketDashboardItem]) -> TicketDashboardSummary:
        total = len(items)
        resolved = len([item for item in items if item.outcome == "resolved"])
        transferred = len([item for item in items if item.outcome == "transferred"])
        average_duration_seconds = (
            int(round(sum(item.duration_seconds for item in items) / total))
            if total
            else 0
        )
        resolved_rate = round((resolved / total) * 100, 1) if total else 0.0
        return TicketDashboardSummary(
            total_count=total,
            resolved_rate=resolved_rate,
            transferred_count=transferred,
            average_duration_seconds=average_duration_seconds,
        )

    def _dashboard_item_matches_query(self, item: TicketDashboardItem, normalized_query: str) -> bool:
        haystacks = [
            item.handler_name,
            item.handler_id,
            item.participant_display,
            item.participant_ref or "",
            item.summary or "",
            item.outcome or "",
            item.channel or "",
        ]
        return any(normalized_query in value.lower() for value in haystacks if value)

    def _support_case_matches_query(self, record: SupportCaseRecord, normalized_query: str) -> bool:
        haystacks = [
            record.case_number,
            record.title,
            record.description,
            record.participant_display or "",
            record.participant_ref or "",
            record.category,
        ]
        return any(normalized_query in value.lower() for value in haystacks if value)

    def _external_case_matches_query(self, record: ExternalCaseLinkRecord, normalized_query: str) -> bool:
        haystacks = [
            record.external_case_id,
            record.external_case_key or "",
            record.external_case_url or "",
            record.provider,
            record.external_case_status or "",
        ]
        return any(normalized_query in value.lower() for value in haystacks if value)

    def _build_timeline(
        self,
        conversation: ConversationRecord,
        traces: list[TurnTraceRecord],
    ) -> list[TicketTimelineEntry]:
        items = [
            TicketTimelineEntry(
                kind="state_transition",
                label=f"Conversation started in {conversation.step_id}",
                recorded_at=conversation.started_at,
                metadata={"channel": conversation.channel or "unknown"},
            )
        ]
        for trace in traces:
            items.append(
                TicketTimelineEntry(
                    kind="state_transition",
                    label=f"{trace.step_before} -> {trace.step_after}",
                    recorded_at=trace.recorded_at,
                    metadata={"turn_id": trace.turn_id},
                )
            )
            for message in list(trace.emitted_messages_json or []):
                text = str(message.get("text") or "").strip()
                if not text:
                    continue
                items.append(
                    TicketTimelineEntry(
                        kind="assistant_message",
                        label=text,
                        recorded_at=trace.recorded_at,
                        metadata={"turn_id": trace.turn_id},
                    )
                )
            for tool_call in list(trace.tool_calls_json or []):
                items.append(
                    TicketTimelineEntry(
                        kind="tool_call",
                        label=str(tool_call.get("tool_ref") or "tool"),
                        detail=str(tool_call.get("status") or ""),
                        recorded_at=trace.recorded_at,
                        metadata={"reason": str(tool_call.get("reason") or "")},
                    )
                )
            for fact_update in list(trace.fact_updates_json or []):
                items.append(
                    TicketTimelineEntry(
                        kind="fact_update",
                        label=str(fact_update.get("name") or "fact"),
                        detail=str(fact_update.get("value") or ""),
                        recorded_at=trace.recorded_at,
                    )
                )
            for semantic_event in list(trace.semantic_events_json or []):
                family = str(semantic_event.get("family") or "")
                name = str(semantic_event.get("name") or "")
                if family not in {"tool_outcome", "fact_missing", "uncertain_understanding"}:
                    continue
                items.append(
                    TicketTimelineEntry(
                        kind="semantic_event",
                        label=f"{family}: {name}",
                        recorded_at=trace.recorded_at,
                    )
                )
        items.sort(key=lambda item: (item.recorded_at, item.kind, item.label))
        return items

    def _next_case_number(self, session: Session, *, organization_id: str, now: datetime) -> str:
        prefix = f"CS-{now.year}-"
        statement = (
            select(SupportCaseRecord.case_number)
            .where(
                SupportCaseRecord.organization_id == organization_id,
                SupportCaseRecord.case_number.like(f"{prefix}%"),
            )
            .order_by(SupportCaseRecord.case_number.desc())
            .limit(1)
        )
        last_case_number = session.execute(statement).scalar_one_or_none()
        next_sequence = 1
        if last_case_number:
            try:
                next_sequence = int(last_case_number.rsplit("-", 1)[1]) + 1
            except (IndexError, ValueError):
                next_sequence = 1
        return f"{prefix}{next_sequence:06d}"

    def _append_case_event(
        self,
        session: Session,
        *,
        case_id: str,
        organization_id: str,
        event_type: str,
        actor_user_id: str | None,
        details: dict[str, object],
        created_at: datetime,
    ) -> None:
        session.add(
            SupportCaseEventRecord(
                event_id=str(uuid4()),
                case_id=case_id,
                organization_id=organization_id,
                event_type=event_type,
                actor_user_id=actor_user_id,
                details_json=dict(details),
                created_at=created_at,
            )
        )


def _normalize_search_query(value: str | None) -> str:
    return (value or "").strip().lower()


def _conversation_duration_seconds(record: ConversationRecord) -> int:
    end_time = record.ended_at or record.updated_at or record.started_at
    duration = end_time - record.started_at
    return max(int(duration.total_seconds()), 0)


def _message_count(record: ConversationRecord, traces: list[TurnTraceRecord]) -> int:
    metadata = dict(record.metadata_json or {})
    explicit = metadata.get("message_count")
    if isinstance(explicit, int):
        return explicit
    return len(traces)


_OUTCOME_SENTIMENT_PROXY: dict[str, float] = {
    "resolved": 0.65,
    "completed": 0.65,
    "closed": 0.55,
    "callback_scheduled": 0.25,
    "voicemail": 0.10,
    "follow_up_required": -0.10,
    "transferred": 0.00,
    "handoff": 0.00,
    "transfer": 0.00,
    "abandoned": -0.55,
    "failed": -0.65,
}


def _sentiment_score(record: ConversationRecord, traces: list[TurnTraceRecord] | None = None) -> float | None:
    # 1. Explicit value written by the agent runtime via facts or metadata takes priority.
    metadata = dict(record.metadata_json or {})
    value = metadata.get("sentiment_score")
    if isinstance(value, (int, float)):
        return float(value)
    facts = dict(record.facts_json or {})
    value = facts.get("sentiment_score")
    if isinstance(value, (int, float)):
        return float(value)

    # 2. Derive a proxy from the conversation outcome if the conversation has ended.
    #    Without a real sentiment pipeline this gives the sentiment column a meaningful
    #    signal instead of always showing "--".
    outcome = record.outcome
    if not outcome:
        return None  # Still active or ended without a known outcome.

    score = _OUTCOME_SENTIMENT_PROXY.get(outcome)
    if score is None:
        return None

    # Penalise for tool failures — they indicate a rough conversation.
    if traces and _has_tool_failures(traces):
        score = max(-1.0, score - 0.20)

    return round(score, 2)


def _has_tool_failures(traces: list[TurnTraceRecord]) -> bool:
    for trace in traces:
        for tool_call in list(trace.tool_calls_json or []):
            if str(tool_call.get("status") or "") in {"error", "blocked", "timeout", "cancelled"}:
                return True
    return False


def _conversation_summary(record: ConversationRecord, traces: list[TurnTraceRecord]) -> str | None:
    metadata = dict(record.metadata_json or {})
    for key in ("summary", "conversation_summary", "ticket_summary"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    facts = dict(record.facts_json or {})
    for key in ("summary", "topic", "intent"):
        value = facts.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for trace in reversed(traces):
        messages = list(trace.emitted_messages_json or [])
        if not messages:
            continue
        text = str(messages[-1].get("text") or "").strip()
        if text:
            return text
    return None


def _participant_details(record: ConversationRecord) -> tuple[str, str | None]:
    metadata = dict(record.metadata_json or {})
    facts = dict(record.facts_json or {})
    display_candidates = (
        metadata.get("participant_display"),
        metadata.get("participant_name"),
        metadata.get("customer_name"),
        metadata.get("participant_identity"),
        facts.get("name"),
        facts.get("email"),
        facts.get("phone"),
    )
    reference_candidates = (
        metadata.get("participant_ref"),
        metadata.get("participant_identity"),
        metadata.get("external_session_id"),
        metadata.get("customer_id"),
        facts.get("email"),
        facts.get("phone"),
    )
    display = next((str(value).strip() for value in display_candidates if str(value or "").strip()), None)
    reference = next((str(value).strip() for value in reference_candidates if str(value or "").strip()), None)
    return display or "Unknown participant", reference


def _as_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _stringify_scalar(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return str(value)
    return repr(value)


def _support_case_to_record(case: SupportCase) -> SupportCaseRecord:
    return SupportCaseRecord(
        case_id=case.case_id,
        organization_id=case.organization_id,
        case_number=case.case_number,
        title=case.title,
        description=case.description,
        status=case.status,
        priority=case.priority,
        category=case.category,
        source=case.source,
        primary_conversation_id=case.primary_conversation_id,
        related_conversation_ids_json=list(case.related_conversation_ids),
        created_by_user_id=case.created_by_user_id,
        assigned_to_user_id=case.assigned_to_user_id,
        assigned_team=case.assigned_team,
        owning_agent_id=case.owning_agent_id,
        participant_ref=case.participant_ref,
        participant_display=case.participant_display,
        participant_email=case.participant_email,
        participant_phone=case.participant_phone,
        tags_json=list(case.tags),
        custom_fields_json=dict(case.custom_fields),
        case_metadata_json=dict(case.case_metadata),
        resolution_json=None if case.resolution is None else case.resolution.model_dump(mode="json"),
        created_at=case.created_at,
        updated_at=case.updated_at,
        resolved_at=case.resolved_at,
        closed_at=case.closed_at,
    )


def _record_to_support_case(record: SupportCaseRecord) -> SupportCase:
    resolution = None
    if record.resolution_json:
        resolution = SupportCaseResolution.model_validate(dict(record.resolution_json))
    return SupportCase(
        case_id=record.case_id,
        organization_id=record.organization_id,
        case_number=record.case_number,
        title=record.title,
        description=record.description,
        status=record.status,
        priority=record.priority,
        category=record.category,
        source=record.source,
        primary_conversation_id=record.primary_conversation_id,
        related_conversation_ids=list(record.related_conversation_ids_json or []),
        created_by_user_id=record.created_by_user_id,
        assigned_to_user_id=record.assigned_to_user_id,
        assigned_team=record.assigned_team,
        owning_agent_id=record.owning_agent_id,
        participant_ref=record.participant_ref,
        participant_display=record.participant_display,
        participant_email=record.participant_email,
        participant_phone=record.participant_phone,
        tags=list(record.tags_json or []),
        custom_fields=dict(record.custom_fields_json or {}),
        case_metadata=dict(record.case_metadata_json or {}),
        resolution=resolution,
        created_at=record.created_at,
        updated_at=record.updated_at,
        resolved_at=record.resolved_at,
        closed_at=record.closed_at,
    )


def _record_to_support_case_event(record: SupportCaseEventRecord) -> SupportCaseEvent:
    return SupportCaseEvent(
        event_id=record.event_id,
        case_id=record.case_id,
        organization_id=record.organization_id,
        event_type=record.event_type,
        actor_user_id=record.actor_user_id,
        details=dict(record.details_json or {}),
        created_at=record.created_at,
    )


def _support_case_note_to_record(note: SupportCaseNote) -> SupportCaseNoteRecord:
    return SupportCaseNoteRecord(
        note_id=note.note_id,
        case_id=note.case_id,
        organization_id=note.organization_id,
        author_user_id=note.author_user_id,
        body=note.body,
        visibility=note.visibility,
        created_at=note.created_at,
    )


def _record_to_support_case_note(record: SupportCaseNoteRecord) -> SupportCaseNote:
    return SupportCaseNote(
        note_id=record.note_id,
        case_id=record.case_id,
        organization_id=record.organization_id,
        author_user_id=record.author_user_id,
        body=record.body,
        visibility=record.visibility,
        created_at=record.created_at,
    )


def _ticketing_activity_to_record(activity: TicketingActivity) -> TicketingActivityRecord:
    return TicketingActivityRecord(
        activity_id=activity.activity_id,
        organization_id=activity.organization_id,
        connection_id=activity.connection_id,
        link_id=activity.link_id,
        provider=activity.provider,
        direction=activity.direction,
        action=activity.action,
        status=activity.status,
        external_case_id=activity.external_case_id,
        attempt_count=activity.attempt_count,
        duration_ms=activity.duration_ms,
        request_json=dict(activity.request),
        response_json=dict(activity.response),
        error_message=activity.error_message,
        retry_status=activity.retry_status,
        next_retry_at=activity.next_retry_at,
        last_attempted_at=activity.last_attempted_at,
        created_at=activity.created_at,
        updated_at=activity.updated_at,
    )


def _record_to_ticketing_activity(record: TicketingActivityRecord) -> TicketingActivity:
    return TicketingActivity(
        activity_id=record.activity_id,
        organization_id=record.organization_id,
        connection_id=record.connection_id,
        link_id=record.link_id,
        provider=record.provider,
        direction=record.direction,  # type: ignore[arg-type]
        action=record.action,
        status=record.status,
        external_case_id=record.external_case_id,
        attempt_count=record.attempt_count,
        duration_ms=record.duration_ms,
        request=dict(record.request_json or {}),
        response=dict(record.response_json or {}),
        error_message=record.error_message,
        retry_status=record.retry_status,  # type: ignore[arg-type]
        next_retry_at=record.next_retry_at,
        last_attempted_at=record.last_attempted_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _ticketing_connection_to_record(connection: TicketingConnection) -> TicketingConnectionRecord:
    return TicketingConnectionRecord(
        connection_id=connection.connection_id,
        organization_id=connection.organization_id,
        provider=connection.provider,
        display_name=connection.display_name,
        status=connection.status,
        auth_type=connection.auth_type,
        credentials_ref=connection.credentials_ref,
        provider_config_json=dict(connection.provider_config),
        field_mappings_json=dict(connection.field_mappings),
        status_mappings_json=dict(connection.status_mappings),
        priority_mappings_json=dict(connection.priority_mappings),
        default_queue=connection.default_queue,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
    )


def _record_to_ticketing_connection(record: TicketingConnectionRecord) -> TicketingConnection:
    return TicketingConnection(
        connection_id=record.connection_id,
        organization_id=record.organization_id,
        provider=record.provider,
        display_name=record.display_name,
        status=record.status,
        auth_type=record.auth_type,
        credentials_ref=record.credentials_ref,
        provider_config=dict(record.provider_config_json or {}),
        field_mappings=dict(record.field_mappings_json or {}),
        status_mappings=dict(record.status_mappings_json or {}),
        priority_mappings=dict(record.priority_mappings_json or {}),
        default_queue=record.default_queue,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _external_case_link_to_record(link: ExternalCaseLink) -> ExternalCaseLinkRecord:
    return ExternalCaseLinkRecord(
        link_id=link.link_id,
        organization_id=link.organization_id,
        provider=link.provider,
        connection_id=link.connection_id,
        external_case_id=link.external_case_id,
        external_case_key=link.external_case_key,
        external_case_url=link.external_case_url,
        external_case_status=link.external_case_status,
        external_case_priority=link.external_case_priority,
        support_case_id=link.support_case_id,
        conversation_id=link.conversation_id,
        sync_status=link.sync_status,
        last_synced_at=link.last_synced_at,
        last_sync_error=link.last_sync_error,
        provider_payload_snapshot_json={
            **dict(link.provider_payload_snapshot),
            "comments": [comment.model_dump(mode="json") for comment in link.comments],
        },
        created_at=link.created_at,
        updated_at=link.updated_at,
    )


def _record_to_external_case_link(record: ExternalCaseLinkRecord) -> ExternalCaseLink:
    snapshot = dict(record.provider_payload_snapshot_json or {})
    raw_comments = list(snapshot.pop("comments", []) or [])
    return ExternalCaseLink(
        link_id=record.link_id,
        organization_id=record.organization_id,
        provider=record.provider,
        connection_id=record.connection_id,
        external_case_id=record.external_case_id,
        external_case_key=record.external_case_key,
        external_case_url=record.external_case_url,
        external_case_status=record.external_case_status,
        external_case_priority=record.external_case_priority,
        support_case_id=record.support_case_id,
        conversation_id=record.conversation_id,
        sync_status=record.sync_status,
        last_synced_at=record.last_synced_at,
        last_sync_error=record.last_sync_error,
        provider_payload_snapshot=snapshot,
        comments=[ExternalCaseComment.model_validate(item) for item in raw_comments],
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
