from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, declarative_mixin, mapped_column


class Base(DeclarativeBase):
    pass


@declarative_mixin
class OptionalTenantScopeMixin:
    organization_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)


@declarative_mixin
class RequiredTenantScopeMixin:
    organization_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)


class ConversationRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_org_created", "organization_id", text("created_at DESC")),
    )

    conversation_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_version_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="live")
    channel: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    step_id: Mapped[str] = mapped_column(String(255), nullable=False)
    facts_json: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    processed_dedupe_keys_json: Mapped[list] = mapped_column(JSON, default=list)
    control_state_json: Mapped[dict] = mapped_column(JSON, default=dict)
    last_event_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Monotonic per-conversation turn counter for the conversation_turns log.
    # Incremented under a row lock so concurrent turn commits serialize;
    # distinct from last_event_sequence, which the realtime projection owns.
    last_turn_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TurnTraceRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "turn_traces"
    __table_args__ = (
        Index("ix_turn_traces_org_recorded", "organization_id", text("recorded_at DESC")),
    )

    trace_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    turn_id: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    step_before: Mapped[str] = mapped_column(String(255), nullable=False)
    step_after: Mapped[str] = mapped_column(String(255), nullable=False)
    semantic_events_json: Mapped[list] = mapped_column(JSON, default=list)
    fact_updates_json: Mapped[list] = mapped_column(JSON, default=list)
    chosen_action_json: Mapped[dict] = mapped_column(JSON, default=dict)
    emitted_messages_json: Mapped[list] = mapped_column(JSON, default=list)
    tool_calls_json: Mapped[list] = mapped_column(JSON, default=list)
    rules_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    latency_breakdown_ms_json: Mapped[dict] = mapped_column(JSON, default=dict)
    classifier_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class ConversationTurnRecord(OptionalTenantScopeMixin, Base):
    """Append-only per-turn event log.

    One row per accepted turn. ``UNIQUE (conversation_id, dedupe_key)`` is the
    authoritative duplicate-turn guard (the in-memory ``processed_dedupe_keys``
    check is only a fast path), and ``UNIQUE (conversation_id, seq)`` pins the
    total order. ``state_after_json`` snapshots the conversation state the turn
    committed, so the current conversation row is always reconstructible from
    the log (see ``stores.rebuild_conversation_state``).
    """

    __tablename__ = "conversation_turns"
    __table_args__ = (
        UniqueConstraint("conversation_id", "dedupe_key", name="uq_conversation_turns_dedupe"),
        UniqueConstraint("conversation_id", "seq", name="uq_conversation_turns_seq"),
        Index("ix_conversation_turns_org_created", "organization_id", text("created_at DESC")),
    )

    turn_pk: Mapped[str] = mapped_column(String(255), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    turn_id: Mapped[str] = mapped_column(String(255), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(512), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    step_before: Mapped[str] = mapped_column(String(255), nullable=False)
    step_after: Mapped[str] = mapped_column(String(255), nullable=False)
    state_after_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RealtimeSessionRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "realtime_sessions"

    realtime_session_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    parent_realtime_session_id: Mapped[str | None] = mapped_column(String(255), ForeignKey("realtime_sessions.realtime_session_id", ondelete="SET NULL"), nullable=True, index=True)
    conversation_id: Mapped[str] = mapped_column(String(255), ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False, index=True)
    surface: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    modality: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    external_session_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    provider_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    participant_identity: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    transport_metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RealtimeEventRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "realtime_events"
    __table_args__ = (
        UniqueConstraint("conversation_id", "conversation_sequence", name="uq_realtime_events_conversation_sequence"),
        Index("ix_realtime_events_org_created", "organization_id", text("created_at DESC")),
    )

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(255), ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=False, index=True)
    realtime_session_id: Mapped[str | None] = mapped_column(String(255), ForeignKey("realtime_sessions.realtime_session_id", ondelete="SET NULL"), nullable=True, index=True)
    conversation_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    family: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    causation_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    actor_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="surface")
    audiences_json: Mapped[list] = mapped_column(JSON, default=list)
    projection_policy_json: Mapped[dict] = mapped_column(JSON, default=dict)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class RealtimeIdempotencyKeyRecord(Base):
    __tablename__ = "realtime_idempotency_keys"
    __table_args__ = (
        # Uniqueness over (organization_id, scope, idempotency_key), with
        # NULL org collapsed into the empty-string partition so untenanted
        # rows still dedupe per (scope, idempotency_key).  Matches the
        # functional unique index created in migration 0043.
        Index(
            "uq_realtime_idempotency_keys_org_scope_key",
            func.coalesce(text("organization_id"), text("''")),
            "scope",
            "idempotency_key",
            unique=True,
        ),
    )

    key_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    scope: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String(255), ForeignKey("conversations.conversation_id", ondelete="SET NULL"), nullable=True, index=True)
    result_event_id: Mapped[str | None] = mapped_column(String(255), ForeignKey("realtime_events.event_id", ondelete="SET NULL"), nullable=True, index=True)
    result_ref_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class RealtimeOutboxRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "realtime_outbox"

    outbox_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    conversation_id: Mapped[str | None] = mapped_column(String(255), ForeignKey("conversations.conversation_id", ondelete="CASCADE"), nullable=True, index=True)
    event_id: Mapped[str] = mapped_column(String(255), ForeignKey("realtime_events.event_id", ondelete="CASCADE"), nullable=False, index=True)
    topic: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True, default="pending")
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProviderCostRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "provider_cost_records"

    cost_record_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    conversation_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("conversations.conversation_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    realtime_session_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("realtime_sessions.realtime_session_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    turn_trace_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("turn_traces.trace_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    tool_invocation_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("tool_invocations.invocation_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    cost_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    amount_usd: Mapped[float] = mapped_column(Float, nullable=False)
    reference_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ToolInvocationRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "tool_invocations"

    invocation_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    tool_ref: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    executor_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    caller_json: Mapped[dict] = mapped_column(JSON, default=dict)
    args_json: Mapped[dict] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class ToolIntegrationJobRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "tool_integration_jobs"
    __table_args__ = (
        UniqueConstraint("invocation_id", name="uq_tool_integration_jobs_invocation_id"),
    )

    job_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    invocation_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("tool_invocations.invocation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tool_ref: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    executor_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    resolution_mode: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    queue_name: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    external_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    callback_correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class AgentRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "agents"

    agent_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict)
    current_draft_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    current_published_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # ── Widget configuration (migration 0036) ────────────────────────────────
    is_widget_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    widget_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="multimodal")
    widget_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class AgentVersionRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version_number", name="uq_agent_versions_agent_version_number"),
    )

    version_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agents.agent_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    based_on_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    agent_document_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class AgentTemplateStorageRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "agent_templates"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_agent_templates_slug"),
    )

    template_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(String(128), nullable=False, default="general", index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    agent_document_json: Mapped[dict] = mapped_column(JSON, default=dict)
    default_agent_settings: Mapped[dict] = mapped_column(JSON, default=dict)
    required_tools_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False, server_default="[]")
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    is_featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# Legacy import alias kept so callers can migrate gradually while the storage
# layer itself uses agent/workflow terminology.
AgentTemplateRecord = AgentTemplateStorageRecord


class AtlasAgentPolicyRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "atlas_agent_policies"

    agent_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agents.agent_id", ondelete="CASCADE"),
        primary_key=True,
    )
    atlas_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    updated_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasSessionRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "atlas_sessions"
    __table_args__ = (
        Index("ix_atlas_sessions_org_updated", "organization_id", text("updated_at DESC")),
        Index("ix_atlas_sessions_agent_updated", "agent_id", text("updated_at DESC")),
    )

    session_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agents.agent_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_version_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("agent_versions.version_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    scope: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    scenario_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    step_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("conversations.conversation_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    trace_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("turn_traces.trace_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    atlas_enabled_snapshot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasMessageRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "atlas_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence_number", name="uq_atlas_messages_session_sequence"),
        Index("ix_atlas_messages_session_created", "session_id", text("created_at DESC")),
    )

    message_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("atlas_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class AtlasEventRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "atlas_events"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence_number", name="uq_atlas_events_session_sequence"),
        Index("ix_atlas_events_session_created", "session_id", text("created_at DESC")),
    )

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("atlas_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class AtlasReviewDecisionRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "atlas_review_decisions"
    __table_args__ = (
        Index("ix_atlas_review_decisions_session_created", "session_id", text("created_at DESC")),
    )

    review_decision_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("atlas_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    delta_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    delta_payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class AtlasProposedDeltaRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "atlas_proposed_deltas"
    __table_args__ = (
        Index("ix_atlas_proposed_deltas_session_created", "session_id", text("created_at DESC")),
        Index("ix_atlas_proposed_deltas_session_family", "session_id", "delta_family"),
    )

    delta_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("atlas_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    delta_family: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    delta_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasApplyRequestRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "atlas_apply_requests"
    __table_args__ = (
        Index("ix_atlas_apply_requests_session_created", "session_id", text("created_at DESC")),
    )

    apply_request_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("atlas_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    delta_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    apply_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AtlasPermissionRequestRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "atlas_permission_requests"
    __table_args__ = (
        Index("ix_atlas_permission_requests_session_created", "session_id", text("created_at DESC")),
        Index("ix_atlas_permission_requests_session_status", "session_id", "status"),
    )

    request_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("atlas_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    risk_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope_ref_json: Mapped[dict] = mapped_column(JSON, default=dict)
    delta_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    requested_actions_json: Mapped[list] = mapped_column(JSON, default=list)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class AtlasReadinessRunRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "atlas_readiness_runs"
    __table_args__ = (
        Index("ix_atlas_readiness_runs_org_created", "organization_id", text("created_at DESC")),
        Index("ix_atlas_readiness_runs_agent_created", "agent_id", text("created_at DESC")),
    )

    run_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("agents.agent_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    agent_version_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("agent_versions.version_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    atlas_session_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("atlas_sessions.session_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    scope: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider_policy: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    case_set_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    document_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    policy_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_config_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    blocker_codes_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class AtlasReadinessEventRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "atlas_readiness_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence_number", name="uq_atlas_readiness_events_run_sequence"),
        Index("ix_atlas_readiness_events_run_created", "run_id", text("created_at ASC")),
    )

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("atlas_readiness_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class AtlasReadinessCaseSetRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "atlas_readiness_case_sets"
    __table_args__ = (
        Index("ix_atlas_readiness_case_sets_org_created", "organization_id", text("created_at DESC")),
        Index("ix_atlas_readiness_case_sets_agent_created", "agent_id", text("created_at DESC")),
    )

    case_set_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    provider_policy: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    cases_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class AtlasReadinessCaseRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "atlas_readiness_cases"
    __table_args__ = (
        Index("ix_atlas_readiness_cases_case_set", "case_set_id"),
    )

    readiness_case_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    case_set_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("atlas_readiness_case_sets.case_set_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("atlas_readiness_runs.run_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    case_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class AtlasReadinessTraceSnapshotRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "atlas_readiness_trace_snapshots"

    trace_snapshot_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("atlas_readiness_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    case_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    trace_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class AtlasReadinessScoreRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "atlas_readiness_scores"

    score_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("atlas_readiness_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    case_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    case_score: Mapped[float] = mapped_column(Float, nullable=False)
    score_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class AtlasReadinessReportRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "atlas_readiness_reports"

    report_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("atlas_readiness_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    publish_recommendation: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    report_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class AtlasModelInvocationRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "atlas_model_invocations"
    __table_args__ = (
        Index("ix_atlas_model_invocations_run_created", "run_id", text("created_at DESC")),
    )

    invocation_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("atlas_readiness_runs.run_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class AtlasVoiceArtifactRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "atlas_voice_artifacts"

    artifact_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("atlas_readiness_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    case_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class AtlasReadinessApplyLockRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "atlas_readiness_apply_locks"
    __table_args__ = (
        UniqueConstraint("agent_id", "draft_version_id", name="uq_atlas_readiness_apply_locks_agent_draft"),
    )

    lock_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("atlas_readiness_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    draft_version_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class PhoneNumberRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "phone_numbers"
    __table_args__ = (
        UniqueConstraint("e164_number", name="uq_phone_numbers_e164"),
    )

    phone_number_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    e164_number: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    ownership_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PhoneNumberBindingRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "phone_number_bindings"
    __table_args__ = (
        UniqueConstraint("provider", "provider_resource_id", name="uq_phone_number_bindings_provider_resource"),
    )

    binding_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    phone_number_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("phone_numbers.phone_number_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    provider_resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    capabilities_json: Mapped[list] = mapped_column(JSON, default=list)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    health_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    transport_metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PhoneNumberRouteRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "phone_number_routes"
    __table_args__ = (
        # Composite index used by resolve_route() — covers phone_number_id + channel filter,
        # enabled filter, and priority sort in a single index scan.
        Index(
            "ix_phone_number_routes_resolve",
            "phone_number_id",
            "channel",
            "enabled",
            "priority",
        ),
    )

    route_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    phone_number_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("phone_numbers.phone_number_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agents.agent_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PhoneNumberAuditRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "phone_number_audit_events"

    audit_event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    phone_number_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("phone_numbers.phone_number_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    summary: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class SimulationFixtureRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "simulation_fixtures"

    fixture_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agents.agent_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags_json: Mapped[list] = mapped_column(JSON, default=list)
    default_channel: Mapped[str] = mapped_column(String(64), nullable=False)
    default_modality: Mapped[str] = mapped_column(String(32), nullable=False)
    starting_step_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    starting_scenario_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    seed_facts_json: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    gate_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    folder_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SimulationFixtureTurnRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "simulation_fixture_turns"
    __table_args__ = (
        UniqueConstraint("fixture_id", "order_index", name="uq_simulation_fixture_turns_fixture_order"),
    )

    fixture_turn_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    fixture_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("simulation_fixtures.fixture_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    modality: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class SimulationFixtureAssertionRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "simulation_fixture_assertions"
    __table_args__ = (
        UniqueConstraint("fixture_id", "order_index", name="uq_simulation_fixture_assertions_fixture_order"),
    )

    fixture_assertion_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    fixture_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("simulation_fixtures.fixture_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    assertion_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)


class EvaluationRunRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "evaluation_runs"

    evaluation_run_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agents.agent_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_version_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_versions.version_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    gate_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    fixture_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pass_rate_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    triggered_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    qualified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict)


class EvaluationCaseResultRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "evaluation_case_results"

    case_result_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    evaluation_run_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("evaluation_runs.evaluation_run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fixture_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("simulation_fixtures.fixture_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    fixture_name: Mapped[str] = mapped_column(String(255), nullable=False)
    conversation_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("conversations.conversation_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    final_state: Mapped[str] = mapped_column(String(255), nullable=False)
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assertions_passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assertions_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocker_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual_facts_json: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvaluationAssertionResultRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "evaluation_assertion_results"

    assertion_result_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    case_result_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("evaluation_case_results.case_result_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fixture_assertion_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("simulation_fixture_assertions.fixture_assertion_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    assertion_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    expected_json: Mapped[dict] = mapped_column(JSON, default=dict)
    actual_json: Mapped[dict] = mapped_column(JSON, default=dict)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JourneyDefinitionRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "journey_definitions"
    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_journey_definitions_org_slug"),
    )

    definition_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_strategy_json: Mapped[dict] = mapped_column(JSON, default=dict)
    scope_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    tags_json: Mapped[list] = mapped_column(JSON, default=list)
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict)
    current_draft_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    current_published_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JourneyDefinitionVersionRecord(OptionalTenantScopeMixin, Base):
    __tablename__ = "journey_definition_versions"
    __table_args__ = (
        UniqueConstraint("definition_id", "version_number", name="uq_journey_definition_versions_number"),
    )

    definition_version_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    definition_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("journey_definitions.definition_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    based_on_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    rules_json: Mapped[dict] = mapped_column(JSON, default=dict)
    compiled_rules_json: Mapped[dict] = mapped_column(JSON, default=dict)
    review_summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class JourneyInstanceRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "journey_instances"
    __table_args__ = (
        Index(
            "uq_journey_instances_open_subject",
            "organization_id",
            "definition_id",
            "subject_key",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

    journey_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    definition_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("journey_definitions.definition_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    definition_version_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("journey_definition_versions.definition_version_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    subject_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    subject_summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    current_milestone_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    current_milestone_order: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    milestone_path_json: Mapped[list] = mapped_column(JSON, default=list)
    first_conversation_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("conversations.conversation_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    latest_conversation_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("conversations.conversation_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    first_agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    first_agent_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    latest_agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    latest_agent_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JourneyTouchpointRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "journey_touchpoints"
    __table_args__ = (
        UniqueConstraint("journey_id", "conversation_id", name="uq_journey_touchpoints_journey_conversation"),
    )

    touchpoint_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    journey_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("journey_instances.journey_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    agent_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    channel: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    mode: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    entry_reason: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JourneyEventRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "journey_events"
    __table_args__ = (
        UniqueConstraint("journey_id", "idempotency_key", name="uq_journey_events_journey_idempotency"),
    )

    journey_event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    journey_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("journey_instances.journey_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    touchpoint_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("journey_touchpoints.touchpoint_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    conversation_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("conversations.conversation_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    turn_trace_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("turn_traces.trace_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    realtime_event_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("realtime_events.event_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    tool_invocation_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("tool_invocations.invocation_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    milestone_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JourneyAnalyticsSnapshotRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "journey_analytics_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "view_kind",
            "definition_id",
            "definition_version_id",
            "period_start",
            "period_end",
            "granularity",
            "filter_key",
            name="uq_journey_analytics_snapshots_scope",
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    view_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    definition_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    definition_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    granularity: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    filter_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    filters_json: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JourneyRuntimeJobRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "journey_runtime_jobs"

    job_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    definition_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("journey_definitions.definition_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    journey_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("journey_instances.journey_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    live_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SupportCaseRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "support_cases"
    __table_args__ = (
        UniqueConstraint("organization_id", "case_number", name="uq_support_cases_org_case_number"),
    )

    case_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    case_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    primary_conversation_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("conversations.conversation_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    related_conversation_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    created_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    assigned_to_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    assigned_team: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    owning_agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    participant_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    participant_display: Mapped[str | None] = mapped_column(String(255), nullable=True)
    participant_email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    participant_phone: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    tags_json: Mapped[list] = mapped_column(JSON, default=list)
    custom_fields_json: Mapped[dict] = mapped_column(JSON, default=dict)
    case_metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    resolution_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class SupportCaseNoteRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "support_case_notes"

    note_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("support_cases.case_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="internal")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SupportCaseEventRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "support_case_events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("support_cases.case_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class TicketingConnectionRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "ticketing_connections"

    connection_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    auth_type: Mapped[str] = mapped_column(String(64), nullable=False)
    credentials_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    field_mappings_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status_mappings_json: Mapped[dict] = mapped_column(JSON, default=dict)
    priority_mappings_json: Mapped[dict] = mapped_column(JSON, default=dict)
    default_queue: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExternalCaseLinkRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "external_case_links"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "connection_id",
            "external_case_id",
            name="uq_external_case_links_connection_case",
        ),
    )

    link_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    connection_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("ticketing_connections.connection_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_case_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    external_case_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    external_case_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_case_status: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    external_case_priority: Mapped[str | None] = mapped_column(String(64), nullable=True)
    support_case_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("support_cases.case_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    conversation_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("conversations.conversation_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sync_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_payload_snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TicketingActivityRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "ticketing_activity"

    activity_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    connection_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("ticketing_connections.connection_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    link_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("external_case_links.link_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    external_case_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_json: Mapped[dict] = mapped_column(JSON, default=dict)
    response_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_status: Mapped[str] = mapped_column(String(32), nullable=False, default="none", index=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IdentityUserRecord(Base):
    __tablename__ = "identity_users"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str] = mapped_column(String(32), nullable=False)
    preferences_json: Mapped[dict] = mapped_column(JSON, default=dict)
    is_superuser: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IdentityOrganizationRecord(Base):
    __tablename__ = "identity_organizations"

    organization_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    icon_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand_color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Account closure state (migration 0024)
    deletion_state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")
    deletion_scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deletion_requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class IdentityOrganizationMembershipRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "identity_org_memberships"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    is_account_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuthSessionRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "auth_sessions"

    session_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_seen_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RefreshTokenFamilyRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "auth_refresh_families"

    family_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    current_token_id: Mapped[str] = mapped_column(String(255), nullable=False)
    current_token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    compromised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExternalIdentityRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "identity_external_identities"
    __table_args__ = (
        UniqueConstraint("provider_type", "provider_key", "subject", name="uq_external_identity_provider_subject"),
    )

    external_identity_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    provider_type: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    claims_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EnterpriseSSOConfigurationRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "identity_enterprise_sso_configurations"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_identity_enterprise_sso_configuration_org"),
    )

    sso_configuration_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    issuer_url: Mapped[str] = mapped_column(Text, nullable=False)
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    client_secret_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    allowed_domains_json: Mapped[list] = mapped_column(JSON, default=list)
    scopes_json: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    enforce_sso: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    jit_provisioning_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrganizationInvitationRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "identity_org_invitations"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_identity_org_invitation_token_hash"),
    )

    invitation_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    is_account_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    invited_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)


class AuthChallengeRecord(Base):
    __tablename__ = "identity_auth_challenges"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_identity_auth_challenge_token_hash"),
    )

    challenge_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    organization_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    invitation_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ── User avatar blobs ──────────────────────────────────────────────────────────
class UserAvatarBlobRecord(Base):
    __tablename__ = "identity_user_avatars"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ── Organisation API keys ──────────────────────────────────────────────────────
class ApiKeyRecord(RequiredTenantScopeMixin, Base):
    """Organisation-scoped API key.

    key_type='secret'      — server-to-server secret key (existing behaviour).
    key_type='publishable' — browser-embeddable key bound to a single agent,
                             with an allowed_origins list for origin validation.
    """

    __tablename__ = "identity_api_keys"
    __table_args__ = (
        Index("ix_identity_api_keys_org_type_agent", "organization_id", "key_type", "agent_id"),
    )

    key_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Publishable-key extensions (migration 0032) ───────────────────────────
    # Existing secret-key rows receive server defaults; no backfill required.
    key_type: Mapped[str] = mapped_column(String(32), nullable=False, default="secret")
    # SET NULL: revoking the key survives agent deletion; operator must disable explicitly.
    agent_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("agents.agent_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # JSON list of allowed origin strings, e.g. ["https://example.com"].
    # Empty list means all origins are allowed (permissive default for secret keys).
    allowed_origins: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    environment: Mapped[str] = mapped_column(String(16), nullable=False, default="live")


# ── Custom tool connections & definitions ─────────────────────────────────────


class APIConnectionRecord(RequiredTenantScopeMixin, Base):
    """A configured integration connection scoped to an organisation.

    Credentials are stored Fernet-encrypted in *credentials_enc*.
    OAuth access/refresh tokens live in *oauth_token_json* and are updated
    out-of-band by the token-refresh worker.
    """

    __tablename__ = "api_connections"
    __table_args__ = (
        UniqueConstraint("organization_id", "display_name", name="uq_api_connections_org_name"),
    )

    connection_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    auth_type: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Per-connection OAuth URL overrides. When set, used instead of the
    # provider's default URLs from OAUTH_PROVIDERS. Enables per-tenant
    # endpoints (Zendesk subdomains) and fully custom OAuth providers
    # (self-hosted GitLab, etc.) without per-provider backend code.
    auth_url_override: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    token_url_override: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Per-connection OAuth client credentials. Required for providers
    # where each customer must register their own OAuth app (Zendesk
    # per-subdomain, Custom OAuth). When null, OAuthFlowManager falls
    # back to the platform's per-provider env credentials.
    # client_secret_enc is Fernet-encrypted (CredentialCipher) — same
    # encryption scheme as credentials_enc.
    oauth_client_id_override: Mapped[str | None] = mapped_column(String(512), nullable=True)
    oauth_client_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    credentials_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_token_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # Encrypted counterparts introduced in migration 0049.  During phase 1 of
    # the credential-encryption rollout the store dual-writes both columns so
    # rollback stays simple.  Migration 0050 drops the plaintext columns.
    # Blob format is documented in ``src/ruhu/tools/cipher.py``.
    credentials_ct: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    oauth_token_ct: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # UTC timestamp when the current OAuth access token expires.
    # NULL for non-OAuth connections or when expiry is unknown.
    # Indexed so the token-refresh worker can efficiently find expiring tokens.
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True, default="active")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Refresh-attempt telemetry: drives exponential backoff in the token
    # refresher. ``refresh_failure_count`` resets to 0 on every successful
    # refresh; ``last_refresh_attempt_at`` is set on every attempt
    # (success OR failure) so the backoff window is measured from the
    # most recent try, not from token expiry.
    refresh_failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_refresh_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ToolDefinitionRecord(RequiredTenantScopeMixin, Base):
    """A callable tool operation scoped to an organisation.

    Three kinds:
    - ``custom_api`` — customer-owned HTTP endpoint, managed on /tools page
    - ``integration`` — auto-created from provider template (HubSpot, Calendar, etc.)
    - ``system`` — built-in platform capability (knowledge search), no connection

    *connection_id* is nullable for system capabilities which have no external
    connection.  For custom_api and integration tools, it references the
    authenticated gateway via FK to ``api_connections``.
    """

    __tablename__ = "tool_definitions"
    __table_args__ = (
        UniqueConstraint("organization_id", "tool_ref", name="uq_tool_definitions_org_ref"),
    )

    tool_definition_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    connection_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("api_connections.connection_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # Tool kind: "api" | "integration" | "builtin" | "code" | "composite" | "mcp"
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="api", index=True)
    tool_ref: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Callable function name in code sandbox, e.g. "create_crm_contact"
    function_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    http_method: Mapped[str] = mapped_column(String(16), nullable=False, default="POST")
    input_schema_json: Mapped[dict] = mapped_column(JSON, default=dict)
    output_schema_json: Mapped[dict] = mapped_column(JSON, default=dict)
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=5000)
    # Safe for LLM auto-use during interactive step execution (read-only operations)
    read_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ToolAgentAssignmentRecord(RequiredTenantScopeMixin, Base):
    """Maps a tool definition to a specific agent.

    *tool_definition_id* FK uses CASCADE: deleting a definition removes all
    its agent assignments automatically.
    """

    __tablename__ = "tool_agent_assignments"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "agent_id",
            "tool_definition_id",
            name="uq_tool_agent_assignments_org_agent_tool",
        ),
    )

    assignment_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    tool_definition_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("tool_definitions.tool_definition_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentToolBindingRecord(RequiredTenantScopeMixin, Base):
    """Per-agent connection override for a tool.

    When an org has multiple connections for the same provider (e.g. two
    HubSpot accounts), this record overrides which connection a specific
    agent uses for a given tool.  If no binding exists, the tool uses its
    default ``connection_id``.
    """

    __tablename__ = "agent_tool_bindings"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "agent_id",
            "tool_definition_id",
            name="uq_agent_tool_bindings_org_agent_tool",
        ),
    )

    binding_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agents.agent_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tool_definition_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("tool_definitions.tool_definition_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The overriding connection for this agent+tool combination
    connection_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("api_connections.connection_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ── Widget sessions (migration 0033) ──────────────────────────────────────────
class WidgetSessionRecord(RequiredTenantScopeMixin, Base):
    """First-class persistence for a customer widget session.

    A session wraps exactly one conversation but is a distinct runtime object:
    the conversation is durable audit history; the session is an ephemeral
    context that carries an expiring bearer token, visitor metadata, and usage
    counters for billing.

    session_token_hash stores SHA-256(hex) of the bearer token.
    The plain token is never persisted — it lives only in HTTPS response bodies
    and the browser's in-memory state.

    voice_duration_seconds is additive: one widget session may contain
    multiple voice calls (user disconnects and reconnects in the same session).
    """

    __tablename__ = "widget_sessions"
    __table_args__ = (
        Index("ix_widget_sessions_org_anonymous_id",         "organization_id", "anonymous_id"),
        Index("ix_widget_sessions_org_status_last_activity", "organization_id", "status", "last_activity_at"),
    )

    # ── Primary key ───────────────────────────────────────────────────────────
    session_id: Mapped[str] = mapped_column(String(255), primary_key=True)

    # ── Core references ───────────────────────────────────────────────────────
    conversation_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    publishable_key_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("identity_api_keys.key_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Visitor identity ──────────────────────────────────────────────────────
    anonymous_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Request context (captured once at session creation) ───────────────────
    origin: Mapped[str | None] = mapped_column(String(2083), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)  # IPv6-safe (max 45)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Session metadata ──────────────────────────────────────────────────────
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="chat")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)

    # ── Authentication ────────────────────────────────────────────────────────
    # SHA-256 hex digest (64 chars).  Unique so the auth path can do a fast
    # point-lookup without knowing the session_id or conversation_id first.
    session_token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    # NULL means the session token does not expire.
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # ── Usage counters ────────────────────────────────────────────────────────
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    voice_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Lifecycle timestamps ──────────────────────────────────────────────────
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ── Widget analytics events (migration 0034) ───────────────────────────────────
class WidgetEventRecord(RequiredTenantScopeMixin, Base):
    """Append-only analytics events emitted by the customer widget runtime.

    agent_id and conversation_id are denormalised from the widget session at
    insert time so that aggregation queries avoid joining through
    widget_sessions → conversations.

    occurred_at is the client-supplied event timestamp; created_at is the
    server receipt time.  Both are stored to allow clock-skew detection.
    """

    __tablename__ = "widget_events"
    __table_args__ = (
        # Per-agent time-series aggregation (analytics dashboard)
        Index("ix_widget_events_org_agent_type_occurred",
              "organization_id", "agent_id", "event_type", "occurred_at"),
        # Org-wide time-series rollup
        Index("ix_widget_events_org_occurred", "organization_id", "occurred_at"),
    )

    # ── Primary key ───────────────────────────────────────────────────────────
    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)

    # ── References ────────────────────────────────────────────────────────────
    session_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("widget_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Denormalised — do not join through widget_sessions for analytics queries.
    conversation_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    # ── Event payload ─────────────────────────────────────────────────────────
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    event_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # ── Timestamps ────────────────────────────────────────────────────────────
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ── Organisation deletion / closure state ─────────────────────────────────────
# These are stored as nullable columns on the existing identity_organizations
# table (added via migration 0024).  The ORM model is declared separately so
# the mapper can be updated without touching IdentityOrganizationRecord.
# They are read back by _to_organization() in identity_sqlalchemy.py.


class ClassifierLoraRecord(OptionalTenantScopeMixin, Base):
    """Registry row for a single LoRA artifact (WI-6.5).

    Resolution order at runtime
    (``ruhu.classifier.registry.resolve_lora``): per-step → per-agent → None.
    "At most one production row per (organization, agent_id, step_id)" is
    enforced by ``registry.promote_to_production`` at the application
    level rather than via partial unique index (SQLite parity).
    """

    __tablename__ = "classifier_loras"
    __table_args__ = (
        Index(
            "ix_classifier_loras_resolution",
            "organization_id",
            "agent_id",
            "step_id",
            "status",
        ),
    )

    lora_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agents.agent_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    lora_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    model_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="candidate",
        server_default="candidate",
        index=True,
    )
    eval_score_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )



# ── Voice cloning (Phase 2a-cloning) ───────────────────────────────────────────
class VoiceCloneRecord(RequiredTenantScopeMixin, Base):
    """Tenant-scoped cloned voice. Created via the
    POST /persona/voices/clone endpoint, surfaced in the catalog
    response alongside Vertex Gemini static voices.

    Cloning keys live in two places:

    * voice_cloning_key_enc — the opaque token Google returned,
      AES-GCM encrypted with AAD b"voiceclone:" + organization_id +
      b"|" + clone_id. The plaintext key never appears in the DB.
    * consent_audio_blob — the consent recording, retained for
      seven years per docs/persona/phase-2.md Track 2a-cloning
      compliance section. Compliance retention can require producing
      this on regulator request, so we store it server-side rather
      than ephemerally on the wizard step.

    Soft-delete via deleted_at so audit trails survive deletion.
    Hard-delete is the responsibility of a separate retention sweep
    (not in this PR).
    """

    __tablename__ = "voice_clones"
    __table_args__ = (
        Index(
            "ix_voice_clones_org_active",
            "organization_id",
            "deleted_at",
        ),
        Index(
            "ix_voice_clones_org_agent",
            "organization_id",
            "agent_id",
        ),
    )

    clone_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Provider key. vertex_gemini for now; left as a column rather
    # than a hard literal so 2a-paid follow-ups (if reintroduced) can
    # share the table.
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="vertex_gemini")
    # Optional: clones can be agent-scoped (e.g. "Maya's voice") or
    # organization-wide ("Acme Corp's brand voice"). NULL means org-wide.
    agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    voice_cloning_key_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    consent_audio_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    consent_audio_mime: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True,
    )



# ── Persona avatar (Phase 2d) ─────────────────────────────────────────────────
class PersonaAvatarBlobRecord(RequiredTenantScopeMixin, Base):
    """Tenant-scoped persona avatar bytes.

    Stored alongside the agent settings; one row per agent.
    EXIF-stripped + re-encoded by persona_avatar.process_avatar_upload
    before persistence — the bytes here are NOT the user-supplied
    bytes. Replacement on a new upload is in-place; the previous
    blob is overwritten (no soft-delete because the avatar is
    cosmetic, not compliance-relevant; the audit trail captures
    the change).
    """

    __tablename__ = "persona_avatar_blobs"
    __table_args__ = (
        Index("ix_persona_avatar_blobs_org", "organization_id"),
    )

    agent_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
