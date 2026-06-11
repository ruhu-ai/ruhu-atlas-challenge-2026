from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from ruhu.realtime import RealtimeEvent
from ruhu.schemas import ConversationState, TurnTrace
from ruhu.tools.types import ToolInvocation

from .models import (
    JourneyAnalyticsSnapshot,
    JourneyRuntimeJob,
    JourneyRuntimeStatus,
    JourneyDefinition,
    JourneyDefinitionReview,
    JourneyDefinitionRules,
    JourneyPublishReadiness,
    JourneyDefinitionVersion,
    JourneyInstance,
    JourneyScope,
    JourneyTouchpoint,
    JourneyEvent,
    SubjectKeyStrategy,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JourneyDefinitionCreate(BaseModel):
    slug: str
    name: str
    description: str | None = None
    subject_strategy: SubjectKeyStrategy
    scope: JourneyScope = Field(default_factory=JourneyScope)
    tags: list[str] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)


class JourneyDefinitionUpdate(BaseModel):
    slug: str | None = None
    name: str | None = None
    description: str | None = None
    subject_strategy: SubjectKeyStrategy | None = None
    scope: JourneyScope | None = None
    status: str | None = None
    tags: list[str] | None = None
    settings: dict[str, Any] | None = None


class JourneyDefinitionVersionCreate(BaseModel):
    based_on_version_id: str | None = None
    rules: JourneyDefinitionRules


class JourneyDefinitionVersionUpdate(BaseModel):
    rules: JourneyDefinitionRules | None = None


class JourneyDefinitionPublishRequest(BaseModel):
    definition_version_id: str | None = None


class JourneyDefinitionSummary(BaseModel):
    definition_id: str
    organization_id: str | None = None
    slug: str
    name: str
    description: str | None = None
    status: str
    current_draft_version_id: str | None = None
    current_published_version_id: str | None = None
    updated_at: datetime


class JourneyDefinitionListResponse(BaseModel):
    definitions: list[JourneyDefinitionSummary]


class JourneyDefinitionVersionListResponse(BaseModel):
    versions: list[JourneyDefinitionVersion]


class JourneyInstanceSummary(BaseModel):
    journey_id: str
    definition_id: str
    definition_version_id: str
    subject_key: str
    status: str
    outcome: str | None = None
    current_milestone_id: str | None = None
    current_milestone_order: int | None = None
    channels: list[str] = Field(default_factory=list)
    latest_agent_id: str | None = None
    started_at: datetime
    last_activity_at: datetime
    ended_at: datetime | None = None


class JourneyInstanceDetail(BaseModel):
    instance: JourneyInstance
    definition: JourneyDefinition | None = None
    version: JourneyDefinitionVersion | None = None
    touchpoints: list[JourneyTouchpoint] = Field(default_factory=list)
    events: list[JourneyEvent] = Field(default_factory=list)


class JourneyInstanceListResponse(BaseModel):
    journeys: list[JourneyInstanceSummary]
    total_count: int = 0
    page: int = 1
    page_size: int = 50


class JourneyTouchpointListResponse(BaseModel):
    touchpoints: list[JourneyTouchpoint]


class JourneyEventListResponse(BaseModel):
    events: list[JourneyEvent]


class JourneyAnalyticsSnapshotListResponse(BaseModel):
    snapshots: list[JourneyAnalyticsSnapshot]


class JourneyAnnotationCreate(BaseModel):
    note: str
    label: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class JourneyReplayRequest(BaseModel):
    preserve_manual_events: bool = True
    execution_mode: str = "sync"


class JourneyDefinitionRebuildRequest(BaseModel):
    definition_version_id: str | None = None
    preserve_manual_events: bool = True
    execution_mode: str = "sync"


class JourneyReplayFailure(BaseModel):
    journey_id: str
    code: str
    message: str


class JourneyReplayResponse(BaseModel):
    journey_id: str
    definition_id: str
    definition_version_id: str
    conversation_ids: list[str] = Field(default_factory=list)
    emitted_event_count: int = 0
    preserved_event_count: int = 0


class JourneyDefinitionReplayResponse(BaseModel):
    definition_id: str
    total_candidates: int = 0
    replayed_journey_ids: list[str] = Field(default_factory=list)
    failures: list[JourneyReplayFailure] = Field(default_factory=list)
    emitted_event_count: int = 0
    preserved_event_count: int = 0
    discovered_conversation_count: int = 0
    discovered_subject_count: int = 0


class JourneyAnalyticsRebuildRequest(BaseModel):
    definition_id: str | None = None
    definition_version_id: str | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    granularity: str = "day"
    channel: str | None = None
    agent_id: str | None = None
    execution_mode: str = "sync"


class JourneyAnalyticsRebuildResponse(BaseModel):
    definition_id: str | None = None
    definition_version_id: str | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    rebuilt_views: list[str] = Field(default_factory=list)
    snapshot_count: int = 0


class JourneyAbandonmentSweepRequest(BaseModel):
    definition_id: str | None = None
    execution_mode: str = "sync"


class JourneyAbandonmentSweepResponse(BaseModel):
    definition_id: str | None = None
    abandoned_journey_ids: list[str] = Field(default_factory=list)


class JourneyDefinitionBundleEntry(BaseModel):
    definition: JourneyDefinition
    versions: list[JourneyDefinitionVersion] = Field(default_factory=list)


class JourneyDefinitionBundle(BaseModel):
    schema_version: str = "journey_definition_bundle.v1"
    exported_at: datetime = Field(default_factory=_utcnow)
    definitions: list[JourneyDefinitionBundleEntry] = Field(default_factory=list)


class JourneyDefinitionImportRequest(BaseModel):
    bundle: JourneyDefinitionBundle
    preserve_ids: bool = False


class JourneyDefinitionImportResponse(BaseModel):
    imported_definition_ids: list[str] = Field(default_factory=list)
    imported_version_ids: list[str] = Field(default_factory=list)


class JourneyRuntimeJobResponse(BaseModel):
    job: JourneyRuntimeJob


class JourneyRuntimeStatusResponse(BaseModel):
    status: JourneyRuntimeStatus


class JourneyFunnelStage(BaseModel):
    milestone_id: str
    milestone_name: str
    order_index: int
    entered_count: int
    completed_count: int
    active_count: int = 0
    completion_rate: float = 0.0


class JourneyFunnelAnalysis(BaseModel):
    definition_id: str
    definition_version_id: str
    period_start: datetime | None = None
    period_end: datetime | None = None
    total_journeys: int = 0
    completed_journeys: int = 0
    stages: list[JourneyFunnelStage] = Field(default_factory=list)


class JourneyDropOffRow(BaseModel):
    milestone_id: str
    milestone_name: str
    drop_off_count: int = 0
    active_count: int = 0
    outcome_counts: dict[str, int] = Field(default_factory=dict)


class JourneyDropOffAnalysis(BaseModel):
    definition_id: str
    definition_version_id: str
    period_start: datetime | None = None
    period_end: datetime | None = None
    rows: list[JourneyDropOffRow] = Field(default_factory=list)


class JourneyPathRow(BaseModel):
    path: list[str] = Field(default_factory=list)
    count: int = 0


class JourneyPathAnalysis(BaseModel):
    definition_id: str
    definition_version_id: str
    period_start: datetime | None = None
    period_end: datetime | None = None
    rows: list[JourneyPathRow] = Field(default_factory=list)


class JourneyTrendPoint(BaseModel):
    bucket_start: datetime
    opened_count: int = 0
    completed_count: int = 0
    abandoned_count: int = 0
    transferred_count: int = 0
    failed_count: int = 0


class JourneyTrendAnalysis(BaseModel):
    definition_id: str | None = None
    definition_version_id: str | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    granularity: str
    points: list[JourneyTrendPoint] = Field(default_factory=list)


class JourneyChannelMixEntry(BaseModel):
    channel: str
    journey_count: int = 0
    touchpoint_count: int = 0


class JourneyChannelMixAnalysis(BaseModel):
    definition_id: str | None = None
    definition_version_id: str | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    rows: list[JourneyChannelMixEntry] = Field(default_factory=list)


class JourneyInstanceEvidenceResponse(BaseModel):
    journey_id: str
    conversations: list[ConversationState] = Field(default_factory=list)
    traces_by_conversation: dict[str, list[TurnTrace]] = Field(default_factory=dict)
    realtime_events_by_conversation: dict[str, list[RealtimeEvent]] = Field(default_factory=dict)
    tool_invocations_by_conversation: dict[str, list[ToolInvocation]] = Field(default_factory=dict)


class JourneyReviewResponse(BaseModel):
    definition: JourneyDefinition
    version: JourneyDefinitionVersion
    review: JourneyDefinitionReview


class JourneyPublishReadinessResponse(BaseModel):
    definition: JourneyDefinition
    draft_version: JourneyDefinitionVersion | None = None
    published_version: JourneyDefinitionVersion | None = None
    readiness: JourneyPublishReadiness
