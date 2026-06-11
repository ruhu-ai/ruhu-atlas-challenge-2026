from __future__ import annotations

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from ..db_models import Base, RequiredTenantScopeMixin


class IntentTagTaxonomyVersionRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "intent_tag_taxonomy_versions"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_intent_tag_taxonomy_versions_org_name"),
    )

    taxonomy_version_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class IntentDefinitionRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "intent_definitions"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "agent_id",
            "taxonomy_version_id",
            "name",
            name="uq_intent_definitions_scope_name",
        ),
    )

    intent_definition_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("agents.agent_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    taxonomy_version_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("intent_tag_taxonomy_versions.taxonomy_version_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    example_phrases_json: Mapped[list] = mapped_column(JSON, default=list)
    confidence_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    priority: Mapped[int] = mapped_column(nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True, index=True)
    is_deprecated: Mapped[bool] = mapped_column(nullable=False, default=False, index=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class TagDefinitionRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "tag_definitions"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "agent_id",
            "taxonomy_version_id",
            "name",
            name="uq_tag_definitions_scope_name",
        ),
    )

    tag_definition_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("agents.agent_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    taxonomy_version_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("intent_tag_taxonomy_versions.taxonomy_version_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tag_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    confidence_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.6)
    apply_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="conversation")
    related_intent_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("intent_definitions.intent_definition_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True, index=True)
    is_deprecated: Mapped[bool] = mapped_column(nullable=False, default=False, index=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rule_config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class IntentTagClassifierProfileRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "intent_tag_classifier_profiles"

    classifier_profile_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("agents.agent_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    adapter_name: Mapped[str] = mapped_column(String(255), nullable=False)
    supported_languages_json: Mapped[list] = mapped_column(JSON, default=list)
    taxonomy_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="live")
    taxonomy_version_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("intent_tag_taxonomy_versions.taxonomy_version_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    intent_catalog_json: Mapped[list] = mapped_column(JSON, default=list)
    tool_catalog_json: Mapped[list] = mapped_column(JSON, default=list)
    catalog_cache_built_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    policy_profile_json: Mapped[dict] = mapped_column(JSON, default=dict)
    profile_metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True, index=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class IntentTagClassificationEventRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "intent_tag_classification_events"

    classification_event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("agents.agent_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    agent_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    classifier_profile_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("intent_tag_classifier_profiles.classifier_profile_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    conversation_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
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
    channel: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False, default="runtime")
    adapter_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_version: Mapped[str] = mapped_column(String(255), nullable=False)
    taxonomy_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    taxonomy_version_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("intent_tag_taxonomy_versions.taxonomy_version_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    request_payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    context_payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    decision_payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    intent_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    language: Mapped[str] = mapped_column(String(32), nullable=False)
    response_language: Mapped[str] = mapped_column(String(32), nullable=False)
    tool_route: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    slots_json: Mapped[dict] = mapped_column(JSON, default=dict)
    signals_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class IntentTagReviewItemRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "intent_tag_review_items"

    review_item_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    classification_event_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("intent_tag_classification_events.classification_event_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    conversation_summary_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    review_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    review_disposition: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    claimed_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    claimed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    reviewed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    corrected_conversation_summary_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("intent_tag_conversation_summaries.conversation_summary_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class IntentTagConversationSummaryRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "intent_tag_conversation_summaries"
    __table_args__ = (
        Index(
            "uq_intent_tag_conversation_summaries_active_final",
            "conversation_id",
            "summary_version",
            unique=True,
            postgresql_where=text("status = 'final'"),
        ),
    )

    conversation_summary_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("agents.agent_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    agent_version_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    conversation_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    summary_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    primary_intent_name: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    secondary_intents_json: Mapped[list] = mapped_column(JSON, default=list)
    resolution_status: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    final_language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    response_language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    channel: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    requires_human_followup: Mapped[bool] = mapped_column(nullable=False, default=False, index=True)
    requires_review: Mapped[bool] = mapped_column(nullable=False, default=False, index=True)
    summary_payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    generated_from_event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_event_created_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class IntentTagAssignmentRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "intent_tag_assignments"

    tag_assignment_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    classification_event_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("intent_tag_classification_events.classification_event_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    conversation_summary_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("intent_tag_conversation_summaries.conversation_summary_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    tag_definition_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("tag_definitions.tag_definition_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    assignment_scope: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    assignment_source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    is_validated: Mapped[bool] = mapped_column(nullable=False, default=False, index=True)
    validated_by_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    validated_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class IntentTagSemanticWebhookTargetRecord(RequiredTenantScopeMixin, Base):
    __tablename__ = "intent_tag_semantic_webhook_targets"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_intent_tag_semantic_webhook_targets_org_name"),
    )

    webhook_target_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    event_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True, default="semantic_summary.finalized")
    agent_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    channels_json: Mapped[list] = mapped_column(JSON, default=list)
    signing_secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    extra_headers_json: Mapped[dict] = mapped_column(JSON, default=dict)
    timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    retry_backoff_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True, index=True)
    last_attempt_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
