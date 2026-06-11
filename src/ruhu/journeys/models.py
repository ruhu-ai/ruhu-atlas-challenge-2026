from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from ruhu.schemas import Channel, ConversationMode


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


JourneyDefinitionStatus = Literal["active", "archived"]
JourneyVersionStatus = Literal["draft", "published"]
JourneyStatus = Literal["open", "completed", "abandoned", "transferred", "failed"]
JourneyEventSource = Literal["runtime_rule", "manual", "import", "replay"]
JourneyAbandonmentOutcome = Literal["abandoned", "failed", "transferred"]
JourneyMergeReopenStatus = Literal["abandoned", "failed", "transferred"]
JourneyEventType = Literal[
    "journey_opened",
    "touchpoint_attached",
    "milestone_entered",
    "milestone_completed",
    "outcome_recorded",
    "journey_closed",
    "journey_reopened",
    "manual_annotation",
    "manual_override",
]
JourneyAnalyticsViewKind = Literal["funnel", "drop_off", "paths", "trends", "channel_mix"]
JourneyRuntimeJobKind = Literal[
    "definition_rebuild",
    "definition_replay",
    "journey_replay",
    "analytics_rebuild",
    "abandonment_sweep",
]
JourneyRuntimeJobStatus = Literal["queued", "running", "completed", "failed"]
JourneyPredicateKind = Literal[
    "conversation_started",
    "step_entered",
    "terminal_disposition",
    "fact_present",
    "fact_equals",
    "tool_succeeded",
    "tool_failed",
    "semantic_event",
    "realtime_event",
    "summary_primary_intent",
    "summary_tag",
    "summary_outcome",
    "summary_resolution_status",
]
JourneyReviewSeverity = Literal["error", "warning"]

JOURNEY_RUNTIME_JOB_KINDS: tuple[JourneyRuntimeJobKind, ...] = (
    "definition_rebuild",
    "definition_replay",
    "journey_replay",
    "analytics_rebuild",
    "abandonment_sweep",
)


class JourneyScope(BaseModel):
    agent_ids: list[str] = Field(default_factory=list)
    channel_filters: list[Channel] = Field(default_factory=list)
    conversation_mode_filters: list[ConversationMode] = Field(default_factory=list)


class SubjectKeyStrategy(BaseModel):
    kind: Literal["metadata_path", "fact_name", "channel_identity", "external_ref"]
    value: str
    fallback_kind: Literal["metadata_path", "fact_name", "channel_identity", "external_ref"] | None = None
    fallback_value: str | None = None

    @model_validator(mode="after")
    def validate_strategy(self) -> "SubjectKeyStrategy":
        if not self.value.strip():
            raise ValueError("subject key strategy value is required")
        if (self.fallback_kind is None) != (self.fallback_value is None):
            raise ValueError("fallback_kind and fallback_value must be set together")
        return self


class JourneyRulePredicate(BaseModel):
    kind: JourneyPredicateKind
    value: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_predicate(self) -> "JourneyRulePredicate":
        if self.kind in {"conversation_started"}:
            return self
        if not self.value:
            raise ValueError(f"{self.kind} predicate requires value")
        return self


class JourneyMilestoneRule(BaseModel):
    milestone_id: str
    name: str
    description: str | None = None
    order_index: int
    required: bool = True
    enter_when: list[JourneyRulePredicate] = Field(default_factory=list)
    complete_when: list[JourneyRulePredicate] = Field(default_factory=list)
    success_labels: list[str] = Field(default_factory=list)
    failure_labels: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_milestone(self) -> "JourneyMilestoneRule":
        if not self.milestone_id.strip():
            raise ValueError("milestone_id is required")
        if not self.name.strip():
            raise ValueError("milestone name is required")
        if self.order_index < 1:
            raise ValueError("milestone order_index must be >= 1")
        if not self.enter_when:
            raise ValueError("milestone enter_when is required")
        return self

    @property
    def is_checkpoint(self) -> bool:
        return not self.complete_when


class JourneyDefinitionRules(BaseModel):
    entry_rules: list[JourneyRulePredicate] = Field(default_factory=list)
    touchpoint_rules: list[JourneyRulePredicate] = Field(default_factory=list)
    milestones: list[JourneyMilestoneRule] = Field(default_factory=list)
    outcome_rules: dict[str, list[JourneyRulePredicate]] = Field(default_factory=dict)
    abandonment_policy: "JourneyAbandonmentPolicy" = Field(default_factory=lambda: JourneyAbandonmentPolicy())
    merge_policy: "JourneyMergePolicy" = Field(default_factory=lambda: JourneyMergePolicy())


class JourneyAbandonmentPolicy(BaseModel):
    inactive_after_seconds: int | None = Field(default=None, ge=1)
    close_as: JourneyAbandonmentOutcome = "abandoned"


class JourneyMergePolicy(BaseModel):
    reopen_closed_within_seconds: int | None = Field(default=None, ge=1)
    reopen_statuses: list[JourneyMergeReopenStatus] = Field(default_factory=list)


class JourneyDefinition(BaseModel):
    definition_id: str = Field(default_factory=lambda: str(uuid4()))
    organization_id: str | None = None
    slug: str
    name: str
    description: str | None = None
    subject_strategy: SubjectKeyStrategy
    scope: JourneyScope = Field(default_factory=JourneyScope)
    status: JourneyDefinitionStatus = "active"
    tags: list[str] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)
    current_draft_version_id: str | None = None
    current_published_version_id: str | None = None
    created_by_user_id: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def validate_definition(self) -> "JourneyDefinition":
        if not self.slug.strip():
            raise ValueError("journey definition slug is required")
        if not self.name.strip():
            raise ValueError("journey definition name is required")
        return self


class JourneyDefinitionVersion(BaseModel):
    definition_version_id: str = Field(default_factory=lambda: str(uuid4()))
    organization_id: str | None = None
    definition_id: str
    version_number: int
    status: JourneyVersionStatus = "draft"
    based_on_version_id: str | None = None
    rules: JourneyDefinitionRules
    compiled_rules: dict[str, Any] = Field(default_factory=dict)
    review_summary: dict[str, Any] = Field(default_factory=dict)
    created_by_user_id: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    published_at: datetime | None = None

    @model_validator(mode="after")
    def validate_version(self) -> "JourneyDefinitionVersion":
        if self.version_number < 1:
            raise ValueError("journey definition version_number must be >= 1")
        return self


class JourneyInstance(BaseModel):
    journey_id: str = Field(default_factory=lambda: str(uuid4()))
    organization_id: str
    definition_id: str
    definition_version_id: str
    subject_key: str
    subject_summary: dict[str, Any] = Field(default_factory=dict)
    status: JourneyStatus = "open"
    outcome: str | None = None
    current_milestone_id: str | None = None
    current_milestone_order: int | None = None
    milestone_path: list[str] = Field(default_factory=list)
    first_conversation_id: str | None = None
    latest_conversation_id: str | None = None
    first_agent_id: str | None = None
    first_agent_version_id: str | None = None
    latest_agent_id: str | None = None
    latest_agent_version_id: str | None = None
    started_at: datetime = Field(default_factory=_utcnow)
    last_activity_at: datetime = Field(default_factory=_utcnow)
    ended_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def validate_instance(self) -> "JourneyInstance":
        if not self.subject_key.strip():
            raise ValueError("journey subject_key is required")
        return self


class JourneyTouchpoint(BaseModel):
    touchpoint_id: str = Field(default_factory=lambda: str(uuid4()))
    organization_id: str
    journey_id: str
    conversation_id: str
    agent_id: str | None = None
    agent_version_id: str | None = None
    channel: Channel | None = None
    mode: ConversationMode | None = None
    entry_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=_utcnow)
    ended_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class JourneyEvent(BaseModel):
    journey_event_id: str = Field(default_factory=lambda: str(uuid4()))
    organization_id: str
    journey_id: str
    touchpoint_id: str | None = None
    conversation_id: str | None = None
    turn_trace_id: str | None = None
    realtime_event_id: str | None = None
    tool_invocation_id: str | None = None
    event_type: JourneyEventType
    milestone_id: str | None = None
    source: JourneyEventSource
    idempotency_key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=_utcnow)
    created_at: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def validate_event(self) -> "JourneyEvent":
        if not self.idempotency_key.strip():
            raise ValueError("journey event idempotency_key is required")
        return self


class JourneyAnalyticsSnapshot(BaseModel):
    snapshot_id: str = Field(default_factory=lambda: str(uuid4()))
    organization_id: str
    view_kind: JourneyAnalyticsViewKind
    definition_id: str | None = None
    definition_version_id: str | None = None
    period_start: datetime
    period_end: datetime
    granularity: str
    filter_key: str
    filters: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def validate_snapshot(self) -> "JourneyAnalyticsSnapshot":
        if self.period_end < self.period_start:
            raise ValueError("journey analytics period_end must be >= period_start")
        if not self.granularity.strip():
            raise ValueError("journey analytics granularity is required")
        if not self.filter_key.strip():
            raise ValueError("journey analytics filter_key is required")
        return self


class JourneyReviewItem(BaseModel):
    severity: JourneyReviewSeverity
    code: str
    message: str


class JourneyDefinitionReview(BaseModel):
    definition_id: str
    definition_version_id: str
    can_publish: bool
    blockers: list[JourneyReviewItem] = Field(default_factory=list)
    warnings: list[JourneyReviewItem] = Field(default_factory=list)
    validated_at: datetime = Field(default_factory=_utcnow)


class JourneyPublishReadiness(BaseModel):
    definition_id: str
    draft_version_id: str | None = None
    published_version_id: str | None = None
    can_publish: bool
    blockers: list[JourneyReviewItem] = Field(default_factory=list)
    warnings: list[JourneyReviewItem] = Field(default_factory=list)
    draft_review: JourneyDefinitionReview | None = None
    validated_at: datetime = Field(default_factory=_utcnow)


class JourneyRuntimeJob(BaseModel):
    job_id: str = Field(default_factory=lambda: f"jjob_{uuid4().hex}")
    organization_id: str
    kind: JourneyRuntimeJobKind
    definition_id: str | None = None
    journey_id: str | None = None
    status: JourneyRuntimeJobStatus = "queued"
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    attempt_count: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    submitted_at: datetime = Field(default_factory=_utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JourneyRuntimeKindMetrics(BaseModel):
    kind: JourneyRuntimeJobKind
    queued_jobs: int = 0
    running_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    recent_failures: int = 0
    last_failure_at: datetime | None = None
    last_success_at: datetime | None = None


class JourneyRuntimeAlert(BaseModel):
    code: str
    severity: JourneyReviewSeverity
    kind: JourneyRuntimeJobKind
    message: str
    recent_failures: int = 0
    threshold: int = 0
    window_seconds: int = 0
    last_failure_at: datetime | None = None


class JourneyRuntimeStatus(BaseModel):
    queued_jobs: int = 0
    running_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    embedded_worker_enabled: bool = True
    last_error: str | None = None
    job_metrics: list[JourneyRuntimeKindMetrics] = Field(default_factory=list)
    alerts: list[JourneyRuntimeAlert] = Field(default_factory=list)
    recent_jobs: list[JourneyRuntimeJob] = Field(default_factory=list)
