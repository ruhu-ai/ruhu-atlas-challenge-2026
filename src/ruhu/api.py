from __future__ import annotations

import asyncio
import ast
import os
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from .attachments import (
    AttachmentRef,
)
from .attachments.view_ready_worker import AttachmentViewReadyWorker
from .audit.emitter import emit_audit_event
from .observability.logging import RequestIDMiddleware, configure_structlog
from .observability.http_middleware import MetricsMiddleware
from .observability.metrics import make_metrics_app
from .rate_limit import PublicRateLimitMiddleware, WidgetSessionRateLimitMiddleware, make_org_rate_limiter
from .db_async import init_async_engine, close_async_engine
from .services.kernel_executor import build_kernel_executor, run_in_kernel_executor
from .api_auth import (
    AuthContextMiddleware,
    AuthContextResolver,
    get_request_auth_context,
)
from .auth import (
    AuthService,
    AuthenticatedPrincipal,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    IssuedOrganizationInvitation,
)
from .auth_runtime import (
    PersistentAuthRuntime,
    build_persistent_auth_runtime,
)
from .browser_tasks_api import install_browser_task_router
from sqlalchemy import or_ as _sa_or_, func as _sa_func
from sqlalchemy.orm import sessionmaker as _sessionmaker
from .composition import (
    ComposedRuntime,
    _enforce_secret_boundary_policy,
    build_runtime,
)
from .services.api_services import ApiServices
from .db import build_session_factory, resolve_database_url
from .email_transport import (
    DevOutboxEmailSender,
    EmailDeliveryResult,
    EmailSender,
    RetryingEmailSender,
    build_email_sender,
)
from .agent_review import (
    AgentOperationalMetrics,
    PublishReviewRemediation,
)
from .identity import (
    AuthSession,
    EnterpriseSSOConfiguration,
    OrganizationInvitation,
    OrganizationMemberRecord,
    OrganizationRole,
)
from .journeys import (
    InMemoryJourneyDefinitionStore,
    InMemoryJourneyInstanceStore,
    JourneyDefinition,
    JourneyRuntime,
    JourneyService,
    SQLAlchemyJourneyDefinitionStore,
    SQLAlchemyJourneyInstanceStore,
    SQLAlchemyJourneyRuntimeJobStore,
    wire_journey_runtime_integration,
)
from .analytics_tagging import build_intent_tags_runtime
from .intent_tags_api import SemanticWebhookDispatchResponse, install_intent_tags_router
from .analytics_tagging.adapters import build_intent_tags_classifier_registry
from .analytics_tagging.runtime_integration import IntentTagsRuntimeIntegrator
from .analytics_tagging.webhooks import SemanticSummaryWebhookDispatcher
from .kernel import ConversationKernel
from .kpi import build_kpi_runtime
from .kpi_api import install_kpi_router
from .livekit_adapter import (
    LiveKitAdapterConfig,
    LiveKitAgentsUnavailableError,
    LiveKitDispatchClient,
    LiveKitPhoneAdapter,
    LiveKitRoomRuntimeClient,
    LiveKitTokenIssuer,
)
from .knowledge_api import install_knowledge_router
from .conversations_router import build_conversations_router
from .auth_deps import (
    make_author_context_dep,
    make_reviewer_context_dep,
)
from .ticket_system import TicketSystemService
from .ticketing_api import install_ticketing_router
from .provider_costs import ProviderCostRecord, SQLAlchemyProviderCostStore, build_provider_cost_records
from .provider_integrations import (
    assistant_texts,
    fetch_whatsapp_meta_media,
    extract_whatsapp_meta_messages,
    extract_whatsapp_meta_phone_number_id,
    extract_whatsapp_meta_statuses,
    match_whatsapp_meta_verify_token,
    parse_whatsapp_meta_channels,
    provider_secret_is_valid,
    verify_whatsapp_meta_signature,
)
from .provider_projection import MetaWhatsAppProjectionDispatcher
from .realtime import (
    RealtimeEvent,
    RealtimeSession,
)
from .registry import AgentVersionSnapshot
from .notifications_api import install_notifications_router
from .billing_api import install_billing_router
from .notifications.store import SQLAlchemyNotificationStore, InMemoryNotificationStore
from .sentiment_worker import ConversationSentimentWorker
from .phone_number_audit import PhoneNumberAuditService
from .phone_numbers import (
    PhoneNumberRouteConfig,
    extract_phone_number_from_metadata,
    parse_phone_number_routes,
    resolve_phone_number_route,
)
from .phone_number_registry import (
    PhoneBindingChannel,
    PhoneBindingHealthStatus,
    PhoneBindingVerificationStatus,
    PhoneNumber,
    PhoneNumberBinding,
    PhoneNumberDetail,
    PhoneNumberOwnershipMode,
    PhoneNumberRegistryService,
    PhoneNumberStatus,
)
from .rules_api import install_rules_router
from .tools_api import install_tools_router
from .runtime_config import RuntimeSettings
from .schemas import (
    Channel,
    ConversationState,
    FactDef,
    AgentVersionStatus,
    Modality,
    RenderedMessage,
    RuntimeTurn,
    RuntimeTurnEventType,
    SimulationRun,
    SimulationSource,
    SimulationTurnInput,
    ToolBinding,
    TurnTrace,
    Transition,
)
from .session_http import (
    build_session_audit_context,
)
from .jobs import InMemoryJobStore
from .simulation_eval import (
    EvaluationCaseReview,
    EvaluationCaseResult,
    EvaluationRuntime,
    EvaluationRuntimeStatus,
    EvaluationRun,
    EvaluationService,
    SimulationAssertion,
    SimulationFixtureBundle,
    SimulationFixture,
    SQLAlchemyEvaluationRunStore,
    SQLAlchemySimulationFixtureStore,
    InMemoryEvaluationRunStore,
    InMemorySimulationFixtureStore,
    validate_fixture,
)
from .tenant import TenantIdentityRepositoryFactory
from .tools.callable_aliases import callable_name_for_ref
from .tools.integration_worker import ToolIntegrationWorkerRuntime
from .tools.types import ToolIntegrationJob, ToolInvocation
from .widget_projection_api import install_widget_projection_router
from .event_sourcing.observability_api import install_observability_router
from .event_sourcing.webhook_api import install_webhook_api

logger = logging.getLogger(__name__)


def _multipart_support_available() -> bool:
    try:
        from python_multipart import __version__  # noqa: F401

        return True
    except ImportError:
        try:
            from multipart.multipart import parse_options_header  # type: ignore[import-untyped]

            return callable(parse_options_header)
        except ImportError:
            return False


# Phase C Batch 1: dashboard models live in ``api_models``.
# Import them here because route declarations use bare class references.
from .api_models import (  # noqa: E402 — deliberate late import after module setup
    DashboardPerformance,
    DashboardResolutionPoint,
    DashboardStats,
)


# Phase C Batch 2: Agent CRUD models moved to ``api_models``; the agents-core
# DTOs are consumed by ``routes.agents`` since RP-3.1 step 10.
from .api_models import (  # noqa: E402
    AgentLLMConfig,
    AgentLLMConfigPatchRequest,
    AgentVoiceConfig,
    AgentVoiceConfigPatchRequest,
    ConversationTraceResponse,
    ConversationRuntimeResponse,
    RealtimeConversationEventResponse,
    TurnInteractionDebugSnapshotResponse,
    TurnInteractionDebugVoicePolicyResponse,
    TurnExecutionResponse,
)
from .routes.console_pages import build_console_pages_router
from .routes.health import build_health_router
from .routes.journeys import build_journeys_router
from .services.agent_presentation import (
    make_agent_evaluation_policy,
    make_agent_settings,
    make_agent_summary,
    make_build_agent_publish_review,
    make_resolve_missing_tool_remediation,
    make_resolve_optional_tool_refs,
    make_resolved_agent_settings,
    make_validate_classifier_strategy,
    make_version_summary_by_id,
)
from .services.org_scope import (
    make_intent_tags_organization_id_for_request,
    make_knowledge_organization_id_for_request,
    make_kpi_organization_id_for_request,
    make_organization_id_for_request,
    make_required_author_organization_id,
    make_tool_integration_organization_id_for_request,
    make_user_id_for_request,
    organization_id_for_context,
    user_id_for_context,
)
from .services.channel_ingress import (
    ChannelIngressService,
    ChannelTurnResponse,
    LiveKitTransportResponse,
    ProviderPhoneBridgeResponse,
)
from .services.conversation_turns import ConversationTurnService
from .services.widget_sessions import WidgetSessionAccessService
from .services.conversation_responses import (
    conversation_runtime_response,
    conversation_trace_response,
    normalize_realtime_payload,
    realtime_conversation_event_response,
    turn_execution_response,
    turn_interaction_debug_snapshot_response,
)
from .agent_document import (
    AgentDocument,
    Step,
    build_step_runtime_entry,
    compile_agent_document,
    step_capability_flags,
)


class WidgetSessionCreateRequest(BaseModel):
    agent_id: str
    target: AgentVersionStatus = "published"
    channel: Channel = "web_widget"
    conversation_id: str | None = None
    session_token: str | None = None
    anonymous_id: str | None = None
    # Required publishable key — anchors every widget session to a tenant.
    # Clients pass the pk_live_/pk_test_ token; backend hashes it for lookup.
    publishable_key: str = Field(..., min_length=1)


class CanvasTestSessionCreateRequest(BaseModel):
    conversation_id: str | None = None
    session_token: str | None = None
    anonymous_id: str | None = None
    channel: Channel = "web_widget"


class WidgetTranscriptMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    text: str
    message_type: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    attachments: list[AttachmentRef] = Field(default_factory=list)


class WidgetSessionResponse(BaseModel):
    conversation_id: str
    agent_id: str
    step_id: str | None = None
    resumed: bool = False
    session_token: str | None = None
    messages: list[WidgetTranscriptMessage] = Field(default_factory=list)
    pending_tool_invocations: list[ToolInvocation] = Field(default_factory=list)


class WidgetMessageRequest(BaseModel):
    text: str
    attachment_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)
    # Idempotency key supplied by the client (typically a per-send UUID).
    # When the same key arrives twice — retries on flaky networks, double
    # taps, browser back/forward — the kernel skips the duplicate via
    # ``conversation.processed_dedupe_keys`` (kernel.py:505).
    # When omitted, the server derives a deterministic key from the
    # message content + attachment ids so identical-content retries within
    # a single conversation still dedupe; legitimate "send the same text
    # twice" cases must override by supplying distinct nonces client-side.
    dedupe_key: str | None = None


class WidgetMessageResponse(BaseModel):
    conversation_id: str
    step_after: str | None = None
    messages: list[RenderedMessage] = Field(default_factory=list)
    trace_id: str
    pending_tool_invocations: list[ToolInvocation] = Field(default_factory=list)


class WidgetVoiceSessionRequest(BaseModel):
    participant_identity: str | None = None
    participant_name: str | None = None
    realtime_session_id: str | None = None
    resume_reason: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class WidgetVoiceSessionResponse(BaseModel):
    conversation_id: str
    realtime_session_id: str
    resumed: bool = False
    step_after: str | None = None
    transport: LiveKitTransportResponse
    pending_tool_invocations: list[ToolInvocation] = Field(default_factory=list)


class WidgetVoiceDisconnectRequest(BaseModel):
    realtime_session_id: str | None = None
    reason: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class WidgetVoiceDisconnectResponse(BaseModel):
    disconnected: bool = False
    session: "ProviderSessionLifecycleResponse | None" = None


class WidgetConfigResponse(BaseModel):
    agent_id: str
    widget_mode: str = "multimodal"
    company_name: str = "Ruhu"
    button_text: str = "Talk to us"
    primary_color: str = "#E64E20"
    accent_color: str = "#D44D00"
    position: Literal["bottom-right", "bottom-left", "top-right", "top-left"] = "bottom-right"
    show_powered_by: bool = True
    welcome_message: str = "Hi! Ask us anything."
    subtitle: str = "Online"
    # Persona surface for the customer-facing widget. Only the cosmetic fields
    # that are safe to expose unauthenticated land here — ``role_title``,
    # ``signoff_template``, and behavioural fields stay server-side.
    persona_name: str | None = None
    pronouns: str | None = None
    avatar_url: str | None = None
    greeting_template: str | None = None


# ── Per-agent widget configuration schemas ────────────────────────────────────

class WidgetConfigFields(BaseModel):
    """Strict schema for the ``widget_config`` JSONB blob.  Rejects unknown keys."""

    model_config = ConfigDict(extra="forbid")

    position: Literal["bottom-right", "bottom-left", "top-right", "top-left"] | None = None
    primary_color: str | None = Field(default=None, max_length=20)
    accent_color: str | None = Field(default=None, max_length=20)
    button_text: str | None = Field(default=None, max_length=50)
    company_name: str | None = Field(default=None, max_length=100)
    company_logo: str | None = Field(default=None, max_length=512)
    welcome_message: str | None = Field(default=None, max_length=500)
    auto_open: bool | None = None
    show_powered_by: bool | None = None


class WidgetConfigUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    widget_mode: Literal["chat", "voice", "multimodal"] | None = None
    widget_config: dict[str, object] | None = None


class WidgetConfigReadResponse(BaseModel):
    agent_id: str
    is_widget_enabled: bool
    widget_mode: str
    widget_config: dict[str, object]


class WidgetEnableResponse(BaseModel):
    agent_id: str
    is_widget_enabled: bool
    widget_mode: str | None = None
    embed_code: str | None = None
    widget_url: str | None = None
    publishable_key: str | None = None
    publishable_key_prefix: str | None = None
    message: str


class EmbedCodeResponse(BaseModel):
    agent_id: str
    embed_code: str
    widget_url: str
    publishable_key_prefix: str
    message: str | None = None


# ── Voice library schemas (Phase 2a-base) ─────────────────────────────────────
#
# These mirror the voice/ subsystem's catalog types and are exposed at
# ``GET /persona/voices/library`` so the picker UI can render the available
# voices. The wire shape is intentionally a 1:1 of VoiceCatalogPage so we
# don't have a translation layer to keep in sync.

class VoiceCatalogEntryResponse(BaseModel):
    voice_id: str
    provider: str
    display_name: str
    language: str
    gender: str
    accent: str | None = None
    description: str | None = None
    sample_text: str | None = None


class VoiceCatalogPageResponse(BaseModel):
    voices: list[VoiceCatalogEntryResponse]
    next_cursor: str | None = None
    total_count: int | None = None


# ── Voice cloning schemas (Phase 2a-cloning) ──────────────────────────────────

class VoiceCloneCreatedResponse(BaseModel):
    """Returned by ``POST /persona/voices/clone`` after successful cloning.

    The plaintext cloning key never appears in this response — the
    server-side store is the authoritative source. Use ``clone_id`` as
    the picker's stable handle for the clone.
    """

    clone_id: str
    provider: str
    display_name: str
    language: str
    created_at: datetime
    estimated_cost_usd: float


# ── Publishable API key schemas ───────────────────────────────────────────────

class PublishableKeyPublicResponse(BaseModel):
    key_id: str
    name: str
    key_prefix: str
    key_type: str
    agent_id: str | None
    allowed_origins: list[str]
    environment: str
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None = None


class PublishableKeyCreatedResponse(PublishableKeyPublicResponse):
    key: str  # plain-text token — shown once, never stored


class CreatePublishableKeyRequest(BaseModel):
    name: str
    agent_id: str
    allowed_origins: list[str] = Field(default_factory=list)
    environment: str = "live"


class UpdateAllowedOriginsRequest(BaseModel):
    allowed_origins: list[str]


# ── Widget analytics schemas ──────────────────────────────────────────────────

class WidgetEventIngestItem(BaseModel):
    event_type: str = Field(..., min_length=1, max_length=128)
    event_data: dict[str, object] = Field(default_factory=dict)
    occurred_at: datetime | None = None  # client-supplied; server uses now() if absent


class WidgetEventBatchRequest(BaseModel):
    events: list[WidgetEventIngestItem] = Field(..., min_length=1, max_length=50)


class WidgetAnalyticsSummary(BaseModel):
    agent_id: str
    period_start: datetime
    period_end: datetime
    total_sessions: int
    total_events: int
    event_counts: dict[str, int]


class AgentReplayRequest(BaseModel):
    turns: list[SimulationTurnInput] = Field(default_factory=list)
    utterances: list[str] = Field(default_factory=list)
    channel: Channel = "web_chat"
    conversation_id: str | None = None
    agent_version_id: str | None = None
    starting_step_id: str | None = None
    starting_scenario_id: str | None = None
    seed_facts: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)


class AgentReplayResponse(BaseModel):
    simulation: SimulationRun
    metrics: AgentOperationalMetrics


class SimulationFixtureCreateRequest(BaseModel):
    fixture_id: str | None = None
    name: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    default_channel: Channel = "web_chat"
    default_modality: Modality = "text"
    starting_step_id: str | None = None
    starting_scenario_id: str | None = None
    seed_facts: dict[str, object] = Field(default_factory=dict)
    turns: list[SimulationTurnInput] = Field(default_factory=list)
    assertions: list[SimulationAssertion] = Field(default_factory=list)
    is_active: bool = True
    gate_required: bool = True
    folder_path: str | None = None


class SimulationFixturePatchRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    default_channel: Channel | None = None
    default_modality: Modality | None = None
    starting_step_id: str | None = None
    starting_scenario_id: str | None = None
    seed_facts: dict[str, object] | None = None
    turns: list[SimulationTurnInput] | None = None
    assertions: list[SimulationAssertion] | None = None
    is_active: bool | None = None
    gate_required: bool | None = None
    folder_path: str | None = None


class FixtureFolderInfo(BaseModel):
    folder_path: str | None = None
    fixture_count: int


class FixtureFolderMoveRequest(BaseModel):
    fixture_ids: list[str]
    folder_path: str | None = None


class FixtureFolderRenameRequest(BaseModel):
    from_path: str
    to_path: str


class SimulationFixtureImportRequest(BaseModel):
    bundle: SimulationFixtureBundle
    replace_existing: bool = True
    assign_new_ids: bool = False
    activate_imported: bool | None = None


class SimulationFixtureImportResult(BaseModel):
    created_count: int = 0
    updated_count: int = 0
    imported_fixtures: list[SimulationFixture] = Field(default_factory=list)


class EvaluationRunCreateRequest(BaseModel):
    fixture_ids: list[str] = Field(default_factory=list)
    agent_version_id: str | None = None
    mode: Literal["manual_batch", "publish_gate", "ci"] = "manual_batch"
    source: Literal["studio", "api", "worker", "cli"] = "api"
    gate_eligible: bool = False
    minimum_pass_rate_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    allow_warning_failures: bool | None = None
    execution_mode: Literal["async", "sync"] = "async"

class ChannelSessionStartRequest(BaseModel):
    agent_id: str
    external_session_id: str
    provider: str | None = None
    provider_session_id: str | None = None
    participant_identity: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class ChannelMessageIngressRequest(BaseModel):
    agent_id: str | None = None
    external_session_id: str
    text: str
    idempotency_key: str | None = None
    provider: str | None = None
    provider_session_id: str | None = None
    participant_identity: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class PhoneTranscriptIngressRequest(BaseModel):
    agent_id: str | None = None
    text: str
    is_final: bool = True
    idempotency_key: str | None = None
    provider: str | None = None
    provider_session_id: str | None = None
    participant_identity: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class LiveKitVoiceTranscriptIngressRequest(BaseModel):
    text: str
    is_final: bool = True
    idempotency_key: str | None = None
    provider_session_id: str | None = None
    participant_identity: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class LiveKitVoiceMessageIngressRequest(BaseModel):
    text: str
    attachment_ids: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None
    provider_session_id: str | None = None
    participant_identity: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class LiveKitVoiceSignalRequest(BaseModel):
    signal: Literal[
        "assistant_speaking_started",
        "assistant_speaking_stopped",
        "assistant_interrupted",
        "assistant_resumed",
        "user_barged_in",
    ]
    reason: str | None = None
    provider_session_id: str | None = None
    participant_identity: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class LiveKitVoiceSignalResponse(BaseModel):
    conversation_id: str
    realtime_session_id: str
    signal: str
    status: Literal["active", "disconnected", "ended", "errored"]
    recorded_names: list[str] = Field(default_factory=list)
    conversation_sequence: int | None = None
    updated_at: datetime


class LiveKitVoiceAssistantAckRequest(BaseModel):
    stage: Literal["resolved", "started", "completed", "interrupted"]
    reason: str | None = None
    idempotency_key: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class LiveKitVoiceAssistantOutput(BaseModel):
    delivery_id: str
    conversation_id: str
    conversation_sequence: int
    text: str
    trace_id: str | None = None
    turn_id: str | None = None
    source_event_id: str | None = None


class LiveKitVoiceAssistantAckResponse(BaseModel):
    conversation_id: str
    realtime_session_id: str
    delivery_id: str
    stage: Literal["resolved", "started", "completed", "interrupted"]
    recorded_name: str
    status: Literal["active", "disconnected", "ended", "errored"]
    duplicate: bool = False
    conversation_sequence: int | None = None
    updated_at: datetime


class ProviderWebhookAck(BaseModel):
    status: Literal["ok", "ignored"] = "ok"
    processed_messages: int = 0
    processed_media_messages: int = 0
    processed_statuses: int = 0
    delivered_messages: int = 0


class ToolIntegrationWebhookRequest(BaseModel):
    payload: dict[str, object] = Field(default_factory=dict)


class ToolIntegrationWebhookResponse(BaseModel):
    status: Literal["ok"] = "ok"
    job_id: str
    invocation_id: str
    job_status: str
    conversation_id: str | None = None
    kernel_turn_applied: bool = False
    step_after: str | None = None
    replayed: bool = False


class ToolIntegrationJobSummaryResponse(BaseModel):
    job_id: str
    invocation_id: str
    conversation_id: str | None = None
    organization_id: str | None = None
    provider: str
    tool_ref: str
    executor_kind: str
    resolution_mode: str
    status: str
    queue_name: str
    attempt_count: int
    max_attempts: int
    external_job_id: str | None = None
    callback_correlation_id: str | None = None
    submitted_at: datetime
    last_progress_at: datetime | None = None
    next_poll_at: datetime | None = None
    next_retry_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class ToolIntegrationJobDetailResponse(ToolIntegrationJobSummaryResponse):
    args: dict[str, object] = Field(default_factory=dict)
    result: dict[str, object] | None = None


class ToolIntegrationJobListResponse(BaseModel):
    items: list[ToolIntegrationJobSummaryResponse] = Field(default_factory=list)
    counts_by_status: dict[str, int] = Field(default_factory=dict)
    counts_by_provider_status: dict[str, dict[str, int]] = Field(default_factory=dict)


class ProviderSessionLifecycleRequest(BaseModel):
    reason: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class ProviderSessionLifecycleResponse(BaseModel):
    conversation_id: str
    realtime_session_id: str
    channel: Channel
    provider: str | None = None
    status: Literal["active", "disconnected", "ended", "errored"]
    ended_at: datetime | None = None
    updated_at: datetime


class ProviderDispatchResponse(BaseModel):
    attempted: int = 0
    delivered: int = 0
    failed: int = 0
    retried: int = 0
    skipped: int = 0


class VoiceSessionReconcileRequest(BaseModel):
    stale_seconds: int = Field(default=300, ge=1, le=86_400)
    provider: str | None = None
    limit: int = Field(default=100, ge=1, le=1000)


class VoiceSessionReconcileResponse(BaseModel):
    reconciled: int
    sessions: list[ProviderSessionLifecycleResponse] = Field(default_factory=list)


class VoiceSessionCreateRequest(BaseModel):
    agent_id: str
    conversation_id: str | None = None
    canvas_version_id: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class VoiceSessionEndRequest(BaseModel):
    reason: str | None = None


class VoiceSessionParticipant(BaseModel):
    identity: str | None = None
    name: str | None = None
    joined_at: str | None = None


class VoiceSessionStatusResponse(BaseModel):
    id: str
    room_name: str | None = None
    status: str
    num_participants: int = 0
    participants: list[VoiceSessionParticipant] = Field(default_factory=list)
    started_at: datetime
    duration_seconds: int | None = None


class VoiceSessionResponse(BaseModel):
    id: str
    organization_id: str | None = None
    agent_id: str
    agent_name: str
    conversation_id: str
    canvas_version_id: str | None = None
    room_name: str
    status: str
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: int | None = None
    access_token: str
    connection_url: str
    metadata: dict[str, object] = Field(default_factory=dict)


class VoiceSessionSummaryResponse(BaseModel):
    id: str
    agent_id: str
    agent_name: str
    conversation_id: str
    room_name: str | None = None
    status: str
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: int | None = None


class VoiceHealthResponse(BaseModel):
    voice_available: bool
    livekit_reachable: bool
    mock: bool


class ProviderPhoneCallStartRequest(BaseModel):
    agent_id: str | None = None
    organization_id: str | None = None
    external_session_id: str
    provider: str | None = None
    provider_session_id: str | None = None
    participant_identity: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class PhoneNumberCreateRequest(BaseModel):
    e164_number: str
    display_name: str | None = None
    ownership_mode: PhoneNumberOwnershipMode = "imported"
    status: PhoneNumberStatus = "active"
    metadata: dict[str, object] = Field(default_factory=dict)


class PhoneNumberUpdateRequest(BaseModel):
    display_name: str | None = None
    status: PhoneNumberStatus | None = None
    ownership_mode: PhoneNumberOwnershipMode | None = None
    metadata: dict[str, object] | None = None


class PhoneNumberBindingCreateRequest(BaseModel):
    channel: PhoneBindingChannel
    provider: str
    provider_resource_id: str | None = None
    capabilities: list[str] | None = None
    verification_status: PhoneBindingVerificationStatus = "unverified"
    health_status: PhoneBindingHealthStatus = "unknown"
    is_active: bool = True
    transport_metadata: dict[str, object] = Field(default_factory=dict)


class PhoneNumberBindingUpdateRequest(BaseModel):
    provider_resource_id: str | None = None
    capabilities: list[str] | None = None
    verification_status: PhoneBindingVerificationStatus | None = None
    health_status: PhoneBindingHealthStatus | None = None
    is_active: bool | None = None
    transport_metadata: dict[str, object] | None = None


class PhoneNumberRouteCreateRequest(BaseModel):
    channel: PhoneBindingChannel = "phone"
    agent_id: str
    priority: int = Field(default=100, ge=0, le=10_000)
    enabled: bool = True
    metadata: dict[str, object] = Field(default_factory=dict)


class PhoneNumberRouteUpdateRequest(BaseModel):
    agent_id: str | None = None
    priority: int | None = Field(default=None, ge=0, le=10_000)
    enabled: bool | None = None
    metadata: dict[str, object] | None = None


class InternalPhoneNumberRouteResolveRequest(BaseModel):
    phone_number: str
    channel: PhoneBindingChannel = "phone"
    provider: str | None = None


class InternalPhoneNumberRouteResolveResponse(BaseModel):
    route_key: str
    phone_number: str
    agent_id: str
    channel: PhoneBindingChannel
    organization_id: str | None = None
    provider: str | None = None
    provider_resource_id: str | None = None
    display_name: str | None = None
    country_code: str | None = None
    enabled: bool = True
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)


class TelnyxPhoneNumberImportRequest(BaseModel):
    phone_number_id: str | None = None
    provider_resource_id: str | None = None
    phone_number: str | None = None
    display_name: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    channel: PhoneBindingChannel = "phone"

    @model_validator(mode="after")
    def _validate_identifiers(self) -> "TelnyxPhoneNumberImportRequest":
        if not self.provider_resource_id and not self.phone_number:
            raise ValueError("provider_resource_id or phone_number is required")
        return self


class TelnyxPhoneNumberResponse(BaseModel):
    provider_resource_id: str
    phone_number: str
    country_code: str | None = None
    status: str | None = None
    phone_number_type: str | None = None
    connection_id: str | None = None
    connection_name: str | None = None
    customer_reference: str | None = None
    messaging_profile_id: str | None = None
    messaging_profile_name: str | None = None
    billing_group_id: str | None = None
    emergency_enabled: bool | None = None
    emergency_status: str | None = None
    call_forwarding_enabled: bool | None = None
    inbound_call_screening: str | None = None
    hd_voice_enabled: bool | None = None
    source_type: str | None = None
    purchased_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    tags: list[str] = Field(default_factory=list)


class TelnyxVoiceSettingsResponse(BaseModel):
    provider_resource_id: str
    connection_id: str | None = None
    customer_reference: str | None = None
    translated_number: str | None = None
    usage_payment_method: str | None = None
    inbound_call_screening: str | None = None
    tech_prefix_enabled: bool | None = None
    call_forwarding_enabled: bool | None = None
    forwards_to: str | None = None
    forwarding_type: str | None = None
    emergency_enabled: bool | None = None
    emergency_status: str | None = None
    media_features: dict[str, object] = Field(default_factory=dict)


class TelnyxBindingSyncResponse(BaseModel):
    number: PhoneNumber
    binding: PhoneNumberBinding
    detail: PhoneNumberDetail
    provider_number: TelnyxPhoneNumberResponse
    voice_settings: TelnyxVoiceSettingsResponse | None = None
    created_number: bool = False
    created_binding: bool = False


class TelnyxAvailableNumberResponse(BaseModel):
    phone_number: str
    country_code: str | None = None
    phone_number_type: str | None = None
    locality: str | None = None
    region: str | None = None
    features: list[str] = Field(default_factory=list)
    monthly_cost: str | None = None
    upfront_cost: str | None = None
    currency: str | None = None
    quickship: bool | None = None
    reservable: bool | None = None


class AfricasTalkingPhoneNumberImportRequest(BaseModel):
    phone_number_id: str | None = None
    phone_number: str
    provider_resource_id: str | None = None
    display_name: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    channel: PhoneBindingChannel = "phone"
    account_username: str | None = None
    voice_callback_url: str | None = None
    events_callback_url: str | None = None
    sip_trunk_target: str | None = None
    sip_auth_required: bool = True
    credentials_reference: str | None = None
    ip_whitelist_confirmed: bool = False
    sip_forwarding_confirmed: bool = False
    configuration_confirmed: bool = False
    last_verified_at: str | None = None
    notes: str | None = None


class AfricasTalkingBindingSyncRequest(BaseModel):
    provider_resource_id: str | None = None
    account_username: str | None = None
    voice_callback_url: str | None = None
    events_callback_url: str | None = None
    sip_trunk_target: str | None = None
    sip_auth_required: bool | None = None
    credentials_reference: str | None = None
    ip_whitelist_confirmed: bool | None = None
    sip_forwarding_confirmed: bool | None = None
    configuration_confirmed: bool | None = None
    last_verified_at: str | None = None
    notes: str | None = None


class AfricasTalkingBindingStateResponse(BaseModel):
    provider_resource_id: str
    phone_number: str
    account_username: str | None = None
    voice_callback_url: str | None = None
    events_callback_url: str | None = None
    sip_trunk_target: str | None = None
    sip_auth_required: bool = True
    credentials_reference: str | None = None
    ip_whitelist_confirmed: bool = False
    sip_forwarding_confirmed: bool = False
    configuration_confirmed: bool = False
    last_verified_at: str | None = None
    notes: str | None = None
    manual_requirements: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)


class AfricasTalkingBindingSyncResponse(BaseModel):
    number: PhoneNumber
    binding: PhoneNumberBinding
    detail: PhoneNumberDetail
    provider_binding: AfricasTalkingBindingStateResponse
    created_number: bool = False
    created_binding: bool = False


class AfricasTalkingValidateCredentialsRequest(BaseModel):
    username: str
    api_key: str


class AfricasTalkingValidateCredentialsResponse(BaseModel):
    valid: bool
    username: str
    account_type: str | None = None
    balance: str | None = None
    error: str | None = None


class AfricasTalkingCheckCallbackReachabilityRequest(BaseModel):
    url: str


class AfricasTalkingCheckCallbackReachabilityResponse(BaseModel):
    url: str
    status: str
    reachable: bool
    http_status_code: int | None = None
    error: str | None = None


class PhoneBindingReconciliationRequest(BaseModel):
    provider: str | None = None
    phone_number_id: str | None = None
    binding_id: str | None = None
    limit: int = Field(default=50, ge=1, le=500)


class PhoneBindingReconciliationResultResponse(BaseModel):
    phone_number_id: str
    binding_id: str
    provider: str
    operation_status: str
    previous_verification_status: str
    previous_health_status: str
    verification_status: str
    health_status: str
    changed: bool
    notification_emitted: bool = False
    error: str | None = None
    reconciled_at: str


class PhoneBindingReconciliationResponse(BaseModel):
    organization_id: str
    processed_count: int
    changed_count: int
    failed_count: int
    results: list[PhoneBindingReconciliationResultResponse] = Field(default_factory=list)


class StartConversationRequest(BaseModel):
    agent_id: str
    agent_version_id: str | None = None
    conversation_id: str | None = None
    channel: Channel = "web_chat"
    modality: Modality = "text"
    starting_step_id: str | None = None
    starting_scenario_id: str | None = None
    seed_facts: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)
    simulation_source: SimulationSource | None = None

class ProviderCostListResponse(BaseModel):
    items: list[ProviderCostRecord] = Field(default_factory=list)


class TurnRequest(BaseModel):
    turn_id: str | None = None
    dedupe_key: str | None = None
    channel: Channel = "web_chat"
    modality: Modality = "text"
    event_type: RuntimeTurnEventType = "user_message"
    text: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class LogoutRequest(BaseModel):
    access_token: str | None = None
    refresh_token: str | None = None


class MagicLinkRequest(BaseModel):
    email: str
    organization_id: str | None = None
    invitation_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("invitation_token", "invite_token"),
    )


class MagicLinkRequestResponse(BaseModel):
    message: str
    delivery: "EmailDeliverySummary"


class MagicLinkVerifyRequest(BaseModel):
    token: str


class OAuthStartRequest(BaseModel):
    redirect_uri: str | None = None
    email: str | None = None
    invitation_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("invitation_token", "invite_token"),
    )
    organization_id: str | None = None


class OAuthStartResponse(BaseModel):
    authorization_url: str


class OAuthCallbackRequest(BaseModel):
    code: str
    state: str
    redirect_uri: str | None = None


class InviteValidateResponse(BaseModel):
    valid: bool
    email: str | None = None
    expires_at: datetime | None = None
    organization_name: str | None = None
    invited_by_name: str | None = None
    role: str | None = None
    is_account_owner: bool = False


class EnterpriseSSOConfigUpsertRequest(BaseModel):
    issuer_url: str
    client_id: str
    client_secret_ref: str
    allowed_domains: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=lambda: ["openid", "profile", "email"])
    is_active: bool = True
    enforce_sso: bool = False
    jit_provisioning_enabled: bool = True


class EnterpriseSSOConfigResponse(BaseModel):
    sso_configuration_id: str
    organization_id: str
    issuer_url: str
    client_id: str
    client_secret_ref: str
    allowed_domains: list[str]
    scopes: list[str]
    is_active: bool
    enforce_sso: bool
    jit_provisioning_enabled: bool


class AuthenticatedUserSummary(BaseModel):
    user_id: str
    email: str
    display_name: str | None = None
    avatar_url: str | None = None
    timezone: str
    language: str
    preferences: dict[str, object] = Field(default_factory=dict)
    is_superuser: bool = False


class AuthenticatedOrganizationSummary(BaseModel):
    organization_id: str
    slug: str
    name: str
    domain: str | None = None
    email: str | None = None
    phone: str | None = None
    icon_url: str | None = None
    description: str | None = None
    brand_color: str | None = None
    role: str
    is_account_owner: bool


class MeResponse(BaseModel):
    user: AuthenticatedUserSummary
    organization: AuthenticatedOrganizationSummary
    session_id: str
    expires_at: datetime


class SessionResponse(BaseModel):
    session_id: str
    user_id: str
    issued_at: datetime
    expires_at: datetime
    last_seen_at: datetime | None = None
    created_ip: str | None = None
    last_seen_ip: str | None = None
    user_agent: str | None = None
    revoked_at: datetime | None = None
    is_current: bool = False


class ApiKeyPublicResponse(BaseModel):
    key_id: str
    name: str
    key_prefix: str
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None = None


class CreateApiKeyRequest(BaseModel):
    name: str
    key_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    key_prefix: str = Field(min_length=12, max_length=32, pattern=r"^[A-Za-z0-9_]+$")


class OrganizationProfileResponse(BaseModel):
    organization_id: str
    slug: str
    name: str
    domain: str | None = None
    email: str | None = None
    phone: str | None = None
    icon_url: str | None = None
    description: str | None = None
    brand_color: str | None = None
    settings: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)
    role: str
    is_account_owner: bool


class OrganizationMemberResponse(BaseModel):
    user_id: str
    email: str
    display_name: str | None = None
    avatar_url: str | None = None
    timezone: str
    language: str
    is_active: bool
    deleted_at: datetime | None = None
    role: str
    is_account_owner: bool
    joined_at: datetime


class OrganizationSessionRevocationResponse(BaseModel):
    organization_id: str
    auth_revoked_after_epoch: int
    auth_revoked_after: datetime


class OrganizationInvitationResponse(BaseModel):
    invitation_id: str
    email: str
    role: str
    is_account_owner: bool
    invited_by_user_id: str
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None = None
    accepted_by_user_id: str | None = None
    revoked_at: datetime | None = None
    revoked_by_user_id: str | None = None
    status: Literal["pending", "accepted", "revoked", "expired"]


class CreateOrganizationInvitationRequest(BaseModel):
    email: str
    role: OrganizationRole = "developer"
    is_account_owner: bool = False


class CreateOrganizationInvitationResponse(OrganizationInvitationResponse):
    delivery: "EmailDeliverySummary"


class EmailDeliverySummary(BaseModel):
    transport: Literal["smtp", "dev_outbox"]
    delivery_id: str | None = None
    status: Literal["sent", "queued", "failed"] = "sent"
    dev_outbox_entry_id: str | None = None


class UpdateOrganizationRequest(BaseModel):
    name: str | None = None
    domain: str | None = None
    email: str | None = None
    phone: str | None = None
    icon_url: str | None = None
    description: str | None = None
    brand_color: str | None = None
    settings: dict[str, object] | None = None
    metadata: dict[str, object] | None = None


class UpdateSelfRequest(BaseModel):
    display_name: str | None = None
    avatar_url: str | None = None
    timezone: str | None = None
    language: str | None = None
    preferences: dict[str, object] | None = None


class CreateOrganizationMemberRequest(BaseModel):
    user_id: str | None = None
    email: str | None = None
    role: OrganizationRole = "developer"
    is_account_owner: bool = False


class UpdateOrganizationMemberRequest(BaseModel):
    role: OrganizationRole | None = None
    is_account_owner: bool | None = None


class AcceptInvitationRequest(BaseModel):
    invitation_token: str
    display_name: str | None = None
    timezone: str = "UTC"
    language: str = "en"


class ExternalIdentitySummary(BaseModel):
    external_identity_id: str
    provider_type: str
    provider_key: str
    email: str | None = None
    organization_id: str
    created_at: datetime
    updated_at: datetime


class InternalPlatformHealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    auth_enabled: bool
    runtime_database_configured: bool
    auth_database_configured: bool
    email_transport: Literal["smtp", "dev_outbox", "none"]
    email_retry_enabled: bool
    organization_count: int
    user_count: int


class InternalAuthDiagnosticsResponse(BaseModel):
    auth_enabled: bool
    environment: str
    issuer: str | None = None
    asymmetric_required: bool
    signing_algorithm: str | None = None
    active_kid: str | None = None
    hs256_fallback_enabled: bool = False
    signing_material_source: Literal["inline", "path", "secret_manager", "none"]
    verification_jwks_source: Literal["inline", "path", "secret_manager", "embedded_active_key", "none"]
    verification_algorithms: list[str] = Field(default_factory=list)
    verification_kids: list[str] = Field(default_factory=list)
    published_jwks_kids: list[str] = Field(default_factory=list)


class InternalIntentTagsClassifierDiagnosticsResponse(BaseModel):
    runtime_enabled: bool
    hosted_classifier_enabled: bool
    hosted_base_url: str | None = None
    hosted_api_key_source: Literal["env", "secret_manager", "none"]
    hosted_timeout_seconds: float
    hosted_max_retries: int
    hosted_retry_backoff_seconds: float
    default_interpreter_name: str | None = None
    agent_interpreters: dict[str, str] = Field(default_factory=dict)
    active_profile_count: int = 0
    active_profile_adapter_counts: dict[str, int] = Field(default_factory=dict)
    recent_event_count: int = 0
    recent_hosted_event_count: int = 0
    recent_fallback_count: int = 0
    recent_model_counts: dict[str, int] = Field(default_factory=dict)
    recent_failure_category_counts: dict[str, int] = Field(default_factory=dict)
    recent_cost_record_count: int = 0
    recent_cost_total_usd: float = 0.0
    recent_cost_type_counts: dict[str, int] = Field(default_factory=dict)
    semantic_summary_webhook_worker_enabled: bool = False
    semantic_summary_webhook_interval_seconds: float = 0.0
    semantic_summary_webhook_batch_size: int = 0
    semantic_summary_webhook_worker_running: bool = False
    semantic_summary_webhook_worker_last_error: str | None = None
    semantic_summary_webhook_worker_last_result: dict[str, object] = Field(default_factory=dict)
    conversation_sweep_worker_enabled: bool = False
    conversation_sweep_interval_seconds: float = 0.0
    conversation_sweep_idle_timeout_seconds: float = 0.0
    conversation_sweep_batch_size: int = 0
    conversation_sweep_worker_running: bool = False
    conversation_sweep_worker_last_error: str | None = None
    conversation_sweep_worker_last_result: dict[str, object] = Field(default_factory=dict)
    sentiment_worker_enabled: bool = False
    sentiment_worker_running: bool = False
    sentiment_worker_last_error: str | None = None
    sentiment_worker_last_result: dict[str, object] = Field(default_factory=dict)


class InternalOrganizationSummary(BaseModel):
    organization_id: str
    slug: str
    name: str
    is_active: bool
    member_count: int
    created_at: datetime


class InternalUserSummary(BaseModel):
    user_id: str
    email: str
    display_name: str | None = None
    is_superuser: bool
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime


_RESERVED_ORGANIZATION_SETTINGS_KEYS = frozenset(
    {
        "auth_revoked_after_epoch",
        "auth_revoked_after",
    }
)


def _build_me_response(principal: AuthenticatedPrincipal) -> MeResponse:
    return MeResponse(
        user=AuthenticatedUserSummary(
            user_id=principal.user.user_id,
            email=principal.user.email,
            display_name=principal.user.display_name,
            avatar_url=principal.user.avatar_url,
            timezone=principal.user.timezone,
            language=principal.user.language,
            preferences=dict(principal.user.preferences),
            is_superuser=principal.user.is_superuser,
        ),
        organization=AuthenticatedOrganizationSummary(
            organization_id=principal.organization.organization_id,
            slug=principal.organization.slug,
            name=principal.organization.name,
            domain=principal.organization.domain,
            email=principal.organization.email,
            phone=principal.organization.phone,
            icon_url=principal.organization.icon_url,
            description=principal.organization.description,
            brand_color=principal.organization.brand_color,
            role=principal.organization_role,
            is_account_owner=principal.is_account_owner,
        ),
        session_id=principal.session.session_id,
        expires_at=principal.session.expires_at,
    )


def _build_session_response(
    session: AuthSession,
    *,
    current_session_id: str | None = None,
) -> SessionResponse:
    return SessionResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        issued_at=session.issued_at,
        expires_at=session.expires_at,
        last_seen_at=session.last_seen_at,
        created_ip=session.created_ip,
        last_seen_ip=session.last_seen_ip,
        user_agent=session.user_agent,
        revoked_at=session.revoked_at,
        is_current=current_session_id == session.session_id,
    )


def _build_organization_profile_response(
    *,
    principal: AuthenticatedPrincipal,
    settings: dict[str, object],
    metadata: dict[str, object],
) -> OrganizationProfileResponse:
    return OrganizationProfileResponse(
        organization_id=principal.organization.organization_id,
        slug=principal.organization.slug,
        name=principal.organization.name,
        domain=principal.organization.domain,
        email=principal.organization.email,
        phone=principal.organization.phone,
        icon_url=principal.organization.icon_url,
        description=principal.organization.description,
        brand_color=principal.organization.brand_color,
        settings=settings,
        metadata=metadata,
        role=principal.organization_role,
        is_account_owner=principal.is_account_owner,
    )


def _build_organization_member_response(record: OrganizationMemberRecord) -> OrganizationMemberResponse:
    return OrganizationMemberResponse(
        user_id=record.user.user_id,
        email=record.user.email,
        display_name=record.user.display_name,
        avatar_url=record.user.avatar_url,
        timezone=record.user.timezone,
        language=record.user.language,
        is_active=record.user.is_active,
        deleted_at=record.user.deleted_at,
        role=record.membership.role,
        is_account_owner=record.membership.is_account_owner,
        joined_at=record.membership.created_at,
    )


def _build_external_identity_summary(identity) -> ExternalIdentitySummary:
    return ExternalIdentitySummary(
        external_identity_id=identity.external_identity_id,
        provider_type=identity.provider_type,
        provider_key=identity.provider_key,
        email=identity.email,
        organization_id=identity.organization_id,
        created_at=identity.created_at,
        updated_at=identity.updated_at,
    )


def _build_internal_user_summary(user) -> InternalUserSummary:
    return InternalUserSummary(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        is_superuser=user.is_superuser,
        is_active=user.is_active and user.deleted_at is None,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


def _jwt_text_source(
    *,
    inline_value: str | None,
    file_path: Path | None,
    secret_version: str | None,
) -> Literal["inline", "path", "secret_manager", "none"]:
    if secret_version is not None:
        return "secret_manager"
    if file_path is not None:
        return "path"
    if inline_value is not None:
        return "inline"
    return "none"


def _classifier_api_key_source(
    *,
    inline_value: str | None,
    secret_version: str | None,
) -> Literal["env", "secret_manager", "none"]:
    if secret_version is not None:
        return "secret_manager"
    if inline_value is not None:
        return "env"
    return "none"


def _jwt_verification_source(
    *,
    inline_value: str | None,
    file_path: Path | None,
    secret_version: str | None,
    rs256_enabled: bool,
) -> Literal["inline", "path", "secret_manager", "embedded_active_key", "none"]:
    direct_source = _jwt_text_source(
        inline_value=inline_value,
        file_path=file_path,
        secret_version=secret_version,
    )
    if direct_source != "none":
        return direct_source
    if rs256_enabled:
        return "embedded_active_key"
    return "none"


def _enforce_auth_signing_policy(
    *,
    settings: RuntimeSettings,
    auth_enabled: bool,
    auth_service: AuthService | None,
) -> None:
    # Production/staging must never boot with auth disabled. Previously, an
    # unset JWT secret → no auth_service built → ``auth_enabled=False`` →
    # this function short-circuited silently, and every route that skipped
    # the ``require_authenticated_context`` dependency became public.
    # Fail loudly so the misconfiguration is visible at startup, not at
    # first request.
    if settings.environment in {"staging", "production"} and not auth_enabled:
        raise ValueError(
            "auth is not configured (no JWT secret or asymmetric keys resolved) but "
            f"environment is {settings.environment!r}; set RUHU_AUTH_JWT_SECRET or "
            "RUHU_AUTH_JWT_PRIVATE_KEY_* to enable token signing before booting"
        )
    if not auth_enabled or auth_service is None:
        return
    if not settings.auth_require_asymmetric_tokens:
        return
    key_manager = auth_service.jwt_codec.key_manager
    if not key_manager.rs256_enabled:
        raise ValueError(
            "RS256 signing material is required when asymmetric token signing is enabled"
        )
    if key_manager.hs256_secret is not None:
        raise ValueError(
            "HS256 fallback must not be configured when asymmetric token signing is enabled"
        )


def _organization_invitation_status(invitation: OrganizationInvitation, *, now: datetime) -> str:
    if invitation.accepted_at is not None:
        return "accepted"
    if invitation.revoked_at is not None:
        return "revoked"
    if invitation.expires_at <= now:
        return "expired"
    return "pending"


def _build_organization_invitation_response(
    invitation: OrganizationInvitation,
    *,
    now: datetime,
) -> OrganizationInvitationResponse:
    return OrganizationInvitationResponse(
        invitation_id=invitation.invitation_id,
        email=invitation.email,
        role=invitation.role,
        is_account_owner=invitation.is_account_owner,
        invited_by_user_id=invitation.invited_by_user_id,
        created_at=invitation.created_at,
        expires_at=invitation.expires_at,
        accepted_at=invitation.accepted_at,
        accepted_by_user_id=invitation.accepted_by_user_id,
        revoked_at=invitation.revoked_at,
        revoked_by_user_id=invitation.revoked_by_user_id,
        status=_organization_invitation_status(invitation, now=now),
    )


def _build_created_organization_invitation_response(
    issued: IssuedOrganizationInvitation,
    *,
    now: datetime,
    delivery: EmailDeliveryResult,
) -> CreateOrganizationInvitationResponse:
    invitation = issued.invitation
    return CreateOrganizationInvitationResponse(
        invitation_id=invitation.invitation_id,
        email=invitation.email,
        role=invitation.role,
        is_account_owner=invitation.is_account_owner,
        invited_by_user_id=invitation.invited_by_user_id,
        created_at=invitation.created_at,
        expires_at=invitation.expires_at,
        accepted_at=invitation.accepted_at,
        accepted_by_user_id=invitation.accepted_by_user_id,
        revoked_at=invitation.revoked_at,
        revoked_by_user_id=invitation.revoked_by_user_id,
        status=_organization_invitation_status(invitation, now=now),
        delivery=_build_email_delivery_summary(delivery),
    )


def _build_email_delivery_summary(delivery: EmailDeliveryResult) -> EmailDeliverySummary:
    return EmailDeliverySummary(
        transport=delivery.transport,
        delivery_id=delivery.delivery_id,
        status=delivery.status,
        dev_outbox_entry_id=delivery.outbox_entry_id,
    )


def _generic_magic_link_delivery_summary(sender: EmailSender | None) -> EmailDeliverySummary:
    transport: Literal["smtp", "dev_outbox"] = "smtp"
    if isinstance(sender, DevOutboxEmailSender):
        transport = "dev_outbox"
    elif isinstance(sender, RetryingEmailSender):
        transport = sender.transport
    return EmailDeliverySummary(
        transport=transport,
        status="queued",
    )


def _build_organization_session_revocation_response(organization) -> OrganizationSessionRevocationResponse:
    revoked_after_epoch = organization.settings.get("auth_revoked_after_epoch")
    revoked_after = organization.settings.get("auth_revoked_after")
    if not isinstance(revoked_after_epoch, int):
        raise HTTPException(status_code=500, detail="organization auth cutoff was not persisted")
    if not isinstance(revoked_after, str):
        raise HTTPException(status_code=500, detail="organization auth cutoff was not persisted")
    return OrganizationSessionRevocationResponse(
        organization_id=organization.organization_id,
        auth_revoked_after_epoch=revoked_after_epoch,
        auth_revoked_after=datetime.fromisoformat(revoked_after),
    )


def _build_enterprise_sso_config_response(
    configuration: EnterpriseSSOConfiguration,
) -> EnterpriseSSOConfigResponse:
    return EnterpriseSSOConfigResponse(
        sso_configuration_id=configuration.sso_configuration_id,
        organization_id=configuration.organization_id,
        issuer_url=configuration.issuer_url,
        client_id=configuration.client_id,
        client_secret_ref=configuration.client_secret_ref,
        allowed_domains=list(configuration.allowed_domains),
        scopes=list(configuration.scopes),
        is_active=configuration.is_active,
        enforce_sso=configuration.enforce_sso,
        jit_provisioning_enabled=configuration.jit_provisioning_enabled,
    )


def _remaining_cookie_max_age_seconds(*, expires_at: datetime, now: datetime) -> int:
    return max(0, int((expires_at - now).total_seconds()))


# Widget session token + origin helpers moved to
# ruhu.services.widget_sessions (RP-3.1 step 13).


def _livekit_transport_payload(transport: object) -> dict[str, object]:
    if hasattr(transport, "as_dict"):
        payload = getattr(transport, "as_dict")()
        if isinstance(payload, Mapping):
            return dict(payload)
    if isinstance(transport, Mapping):
        return dict(transport)
    payload: dict[str, object] = {}
    for key in (
        "provider",
        "url",
        "room_name",
        "token",
        "participant_identity",
        "agent_name",
        "sdk_version_target",
        "voice_mode",
        "dispatch_strategy",
        "dispatch",
        "metadata",
    ):
        value = getattr(transport, key, None)
        if value is not None:
            payload[key] = value
    return payload


def _resolve_auth_redirect_origins(settings: RuntimeSettings) -> list[str]:
    origins = {item.rstrip("/") for item in settings.auth_allowed_redirect_origins if item}
    if settings.frontend_url:
        parsed = urlparse(settings.frontend_url)
        if parsed.scheme and parsed.netloc:
            origins.add(f"{parsed.scheme}://{parsed.netloc}")
    return sorted(origins)


def _resolve_public_auth_base_url(*, settings: RuntimeSettings) -> str:
    if settings.frontend_url:
        return settings.frontend_url.rstrip("/")
    allowed_origins = _resolve_auth_redirect_origins(settings)
    if len(allowed_origins) == 1:
        return allowed_origins[0]
    raise HTTPException(
        status_code=503,
        detail="frontend_url or a single allowed auth redirect origin must be configured for public auth links",
    )


def _raise_http_for_auth_error(exc: AuthenticationError | AuthorizationError | ConflictError) -> None:
    if isinstance(exc, ConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, AuthorizationError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=str(exc),
        headers={"WWW-Authenticate": "Bearer"},
    ) from exc


# ── Agent Templates ────────────────────────────────────────────────────────────


def _agent_id_from_name(name: str) -> str:
    """Convert a display name to a URL-safe agent_id with a random suffix."""
    import re as _re
    slug = _re.sub(r"[^a-z0-9]+", "_", name.lower().strip()).strip("_")
    suffix = uuid4().hex[:8]
    return f"{slug[:40]}_{suffix}"


class AgentTemplateDefaultSettings(BaseModel):
    system_prompt: str = "You are a helpful AI voice assistant."
    agent_type: Literal["chat", "voice", "multimodal"] = "voice"


class TemplateRequiredTool(BaseModel):
    """Onboarding metadata for an external tool the template's agent document
    references.

    NOT the runtime requirement contract — that lives in the agent document
    itself (step.tool_policy[].ref / step.tool_affordances[]). This
    record exists purely to give the gallery, post-clone checklist,
    and publish-review remediation a human-readable framing for each
    external tool the user must configure.

    See docs/templates/Template-Required-Tools-Onboarding-Spec.md.

    ``required`` (Axis 1 of the relax-the-publish-gate plan) is true
    when missing the tool blocks publish — typical for tools on the
    critical path (entry-point lookups, lead capture).  False when
    the tool is on a conditional branch (alternative resolution
    paths, secondary features) — its absence becomes a publish-time
    *warning* but not a *blocker*, so customers can ship narrower
    versions of a template without setting up every integration.
    Defaults to true so omitted requirement metadata keeps publish
    gating strict unless a template explicitly marks a tool optional.
    """

    tool_ref: str
    display_name: str
    description: str
    category: str
    provider_hints: list[str] = Field(default_factory=list)
    setup_url_path: str
    documentation_url: str | None = None
    required: bool = True


class AgentTemplateResponse(BaseModel):
    template_id: str
    organization_id: str | None = None
    name: str
    slug: str
    description: str
    category: str
    tags: list[str]
    default_agent_settings: AgentTemplateDefaultSettings
    required_tools: list[TemplateRequiredTool] = Field(default_factory=list)
    step_count: int
    tool_types: list[str]
    is_published: bool
    is_featured: bool
    usage_count: int
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentTemplateDetailResponse(AgentTemplateResponse):
    agent_document_json: dict


class AgentTemplateListResponse(BaseModel):
    templates: list[AgentTemplateResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class CloneAgentTemplateRequest(BaseModel):
    agent_name: str
    system_prompt: str | None = None
    agent_type: Literal["chat", "voice", "multimodal"] | None = None


class CloneAgentTemplateResponse(BaseModel):
    agent_id: str
    agent_name: str
    template_id: str
    template_name: str
    created_at: datetime
    message: str


class AgentTemplateCreateRequest(BaseModel):
    name: str
    slug: str
    description: str = ""
    category: str = "general"
    tags: list[str] = Field(default_factory=list)
    agent_document_json: dict
    default_agent_settings: AgentTemplateDefaultSettings = Field(
        default_factory=AgentTemplateDefaultSettings
    )
    required_tools: list[TemplateRequiredTool] = Field(default_factory=list)
    is_published: bool = False
    is_featured: bool = False

    @model_validator(mode="after")
    def validate_document_payload(self) -> "AgentTemplateCreateRequest":
        self.agent_document_json = AgentDocument.model_validate(
            dict(self.agent_document_json)
        ).model_dump(mode="json")
        return self


class AgentTemplatePatchRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    is_published: bool | None = None
    is_featured: bool | None = None


class SaveAgentAsTemplateRequest(BaseModel):
    name: str
    slug: str
    description: str = ""
    category: str = "general"
    tags: list[str] = Field(default_factory=list)
    is_published: bool = False

class TemplateRequiredToolWithSatisfaction(TemplateRequiredTool):
    """``TemplateRequiredTool`` enriched with org-aware satisfaction state.

    ``satisfied`` is omitted (None on the wire) for unauthenticated
    callers — gallery-preview UX still gets the static metadata but
    no leakage of which orgs have which integrations configured.
    """

    satisfied: bool | None = None


class AgentTemplateRequiredToolsResponse(BaseModel):
    template_id: str
    tools: list[TemplateRequiredToolWithSatisfaction]
    all_required_satisfied: bool | None = None


# ── Account closure DTOs ────────────────────────────────────────────────────
# Hoisted from inside `create_app()`'s closure so Pydantic v2's OpenAPI schema
# generator can resolve their ForwardRefs. Closure-nested BaseModel classes
# fail `get_definitions()` with PydanticUserError because the type adapter
# can't be fully built outside the enclosing function's scope.

class CloseAccountRequest(BaseModel):
    confirm_org_name: str
    reason: str | None = None


class ClosureStatusResponse(BaseModel):
    organization_id: str
    deletion_state: str
    deletion_scheduled_for: datetime | None = None
    message: str
    status: str | None = None


class ConfirmActionRequest(BaseModel):
    token: str


class SQLAlchemyAgentTemplateStore:
    """Persistence layer for agent templates."""

    def __init__(self, session_factory: _sessionmaker) -> None:
        self._sf = session_factory

    @staticmethod
    def _derive_meta(agent_document_json: dict) -> tuple[int, list[str]]:
        try:
            document = AgentDocument.model_validate(agent_document_json)
            step_count = len(document.steps)
            tool_types: set[str] = set()
            for step in document.steps:
                for tp in step.tool_policy:
                    ref = tp.ref
                    if ref and "." in ref:
                        tool_types.add(ref.split(".")[0])
            return step_count, sorted(tool_types)
        except Exception:
            return 0, []

    @staticmethod
    def _record_to_response(r: object) -> "AgentTemplateResponse":
        normalized_agent_document_json = dict(getattr(r, "agent_document_json", None) or {})
        step_count, tool_types = SQLAlchemyAgentTemplateStore._derive_meta(normalized_agent_document_json)
        das = dict(r.default_agent_settings or {})  # type: ignore[union-attr]
        required_tools_raw = list(getattr(r, "required_tools_json", None) or [])
        required_tools = [
            TemplateRequiredTool(**entry) for entry in required_tools_raw
            if isinstance(entry, dict)
        ]
        return AgentTemplateResponse(
            template_id=r.template_id,  # type: ignore[union-attr]
            organization_id=r.organization_id,  # type: ignore[union-attr]
            name=r.name,  # type: ignore[union-attr]
            slug=r.slug,  # type: ignore[union-attr]
            description=r.description,  # type: ignore[union-attr]
            category=r.category,  # type: ignore[union-attr]
            tags=list(r.tags or []),  # type: ignore[union-attr]
            default_agent_settings=AgentTemplateDefaultSettings(**das),
            required_tools=required_tools,
            step_count=step_count,
            tool_types=tool_types,
            is_published=r.is_published,  # type: ignore[union-attr]
            is_featured=r.is_featured,  # type: ignore[union-attr]
            usage_count=r.usage_count or 0,  # type: ignore[union-attr]
            created_by=r.created_by,  # type: ignore[union-attr]
            created_at=r.created_at,  # type: ignore[union-attr]
            updated_at=r.updated_at,  # type: ignore[union-attr]
        )

    @staticmethod
    def _record_to_detail(r: object) -> "AgentTemplateDetailResponse":
        base = SQLAlchemyAgentTemplateStore._record_to_response(r)
        agent_document_json = dict(getattr(r, "agent_document_json", None) or {})
        return AgentTemplateDetailResponse(
            **base.model_dump(),
            agent_document_json=agent_document_json,
        )

    def list_templates(
        self,
        *,
        organization_id: str | None,
        category: str | None = None,
        agent_type: str | None = None,
        is_featured: bool | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> AgentTemplateListResponse:
        import math
        from .db_models import AgentTemplateRecord
        with self._sf.begin() as session:
            q = session.query(AgentTemplateRecord).filter(
                _sa_or_(
                    AgentTemplateRecord.organization_id.is_(None),
                    AgentTemplateRecord.organization_id == organization_id,
                ),
                _sa_or_(
                    AgentTemplateRecord.is_published.is_(True),
                    AgentTemplateRecord.organization_id == organization_id,
                ),
            )
            if category:
                q = q.filter(AgentTemplateRecord.category == category)
            if is_featured is not None:
                q = q.filter(AgentTemplateRecord.is_featured == is_featured)
            if search:
                s = f"%{search.lower()}%"
                q = q.filter(
                    _sa_or_(
                        _sa_func.lower(AgentTemplateRecord.name).like(s),
                        _sa_func.lower(AgentTemplateRecord.description).like(s),
                    )
                )
            q = q.order_by(
                AgentTemplateRecord.is_featured.desc(),
                AgentTemplateRecord.usage_count.desc(),
                AgentTemplateRecord.created_at.desc(),
            )
            total = q.count()
            records = q.offset((page - 1) * page_size).limit(page_size).all()
            templates = [self._record_to_response(r) for r in records]
        if agent_type:
            templates = [t for t in templates if t.default_agent_settings.agent_type == agent_type]
        total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 1
        return AgentTemplateListResponse(
            templates=templates,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    def get_template_detail(
        self,
        template_id: str,
        *,
        organization_id: str | None,
    ) -> AgentTemplateDetailResponse | None:
        from .db_models import AgentTemplateRecord
        with self._sf.begin() as session:
            record = session.query(AgentTemplateRecord).filter(
                AgentTemplateRecord.template_id == template_id,
                _sa_or_(
                    AgentTemplateRecord.organization_id.is_(None),
                    AgentTemplateRecord.organization_id == organization_id,
                ),
            ).first()
            if record is None:
                return None
            return self._record_to_detail(record)

    def get_template_snapshot(
        self,
        template_id: str,
        *,
        organization_id: str | None,
    ) -> tuple[dict, dict, str] | None:
        """Returns (agent_document_json, default_agent_settings, template_name) or None."""
        from .db_models import AgentTemplateRecord
        with self._sf.begin() as session:
            record = session.query(AgentTemplateRecord).filter(
                AgentTemplateRecord.template_id == template_id,
                _sa_or_(
                    AgentTemplateRecord.organization_id.is_(None),
                    AgentTemplateRecord.organization_id == organization_id,
                ),
            ).first()
            if record is None:
                return None
            return (
                dict(record.agent_document_json or {}),
                dict(record.default_agent_settings or {}),
                record.name,
            )

    def create_template(
        self,
        *,
        data: AgentTemplateCreateRequest,
        organization_id: str | None,
        created_by: str | None,
    ) -> AgentTemplateDetailResponse:
        from .db_models import AgentTemplateRecord
        now = datetime.now(timezone.utc)
        template_id = f"gtpl_{uuid4().hex[:20]}"
        record = AgentTemplateRecord(
            template_id=template_id,
            organization_id=organization_id,
            name=data.name,
            slug=data.slug,
            description=data.description,
            category=data.category,
            tags=list(data.tags),
            agent_document_json=AgentDocument.model_validate(data.agent_document_json).model_dump(mode="json"),
            default_agent_settings=data.default_agent_settings.model_dump(mode="json"),
            required_tools_json=[entry.model_dump(mode="json") for entry in data.required_tools],
            is_published=data.is_published,
            is_featured=data.is_featured,
            usage_count=0,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        with self._sf.begin() as session:
            session.add(record)
            result = self._record_to_detail(record)
        return result

    def patch_template(
        self,
        template_id: str,
        patch: AgentTemplatePatchRequest,
        *,
        organization_id: str | None,
    ) -> AgentTemplateDetailResponse | None:
        from .db_models import AgentTemplateRecord
        now = datetime.now(timezone.utc)
        with self._sf.begin() as session:
            record = session.query(AgentTemplateRecord).filter(
                AgentTemplateRecord.template_id == template_id,
                _sa_or_(
                    AgentTemplateRecord.organization_id.is_(None),
                    AgentTemplateRecord.organization_id == organization_id,
                ),
            ).first()
            if record is None:
                return None
            if patch.name is not None:
                record.name = patch.name
            if patch.description is not None:
                record.description = patch.description
            if patch.category is not None:
                record.category = patch.category
            if patch.tags is not None:
                record.tags = list(patch.tags)
            if patch.is_published is not None:
                record.is_published = patch.is_published
            if patch.is_featured is not None:
                record.is_featured = patch.is_featured
            record.updated_at = now
            return self._record_to_detail(record)

    def delete_template(
        self,
        template_id: str,
        *,
        organization_id: str | None,
    ) -> bool:
        from .db_models import AgentTemplateRecord
        with self._sf.begin() as session:
            record = session.query(AgentTemplateRecord).filter(
                AgentTemplateRecord.template_id == template_id,
                _sa_or_(
                    AgentTemplateRecord.organization_id.is_(None),
                    AgentTemplateRecord.organization_id == organization_id,
                ),
            ).first()
            if record is None:
                return False
            session.delete(record)
        return True

    def increment_usage_count(self, template_id: str) -> None:
        from .db_models import AgentTemplateRecord
        with self._sf.begin() as session:
            record = session.query(AgentTemplateRecord).filter(
                AgentTemplateRecord.template_id == template_id,
            ).first()
            if record is not None:
                record.usage_count = (record.usage_count or 0) + 1

    def upsert_system_template(
        self,
        *,
        template_id: str,
        name: str,
        slug: str,
        description: str,
        category: str,
        tags: list[str],
        agent_document_json: dict,
        default_agent_settings: dict,
        required_tools: list[dict] | None = None,
        is_published: bool = True,
        is_featured: bool = False,
    ) -> None:
        from .db_models import AgentTemplateRecord
        now = datetime.now(timezone.utc)
        required_tools_payload = list(required_tools or [])
        with self._sf.begin() as session:
            existing = session.query(AgentTemplateRecord).filter(
                AgentTemplateRecord.template_id == template_id,
            ).first()
            normalized_agent_document_json = AgentDocument.model_validate(
                dict(agent_document_json)
            ).model_dump(mode="json")
            if existing is None:
                record = AgentTemplateRecord(
                    template_id=template_id,
                    organization_id=None,
                    name=name,
                    slug=slug,
                    description=description,
                    category=category,
                    tags=tags,
                    agent_document_json=normalized_agent_document_json,
                    default_agent_settings=default_agent_settings,
                    required_tools_json=required_tools_payload,
                    is_published=is_published,
                    is_featured=is_featured,
                    usage_count=0,
                    created_by=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
            else:
                existing.name = name
                existing.slug = slug
                existing.description = description
                existing.category = category
                existing.tags = tags
                existing.agent_document_json = normalized_agent_document_json
                existing.default_agent_settings = default_agent_settings
                existing.required_tools_json = required_tools_payload
                existing.is_published = is_published
                existing.updated_at = now


class TemplateRequiredToolsValidationError(ValueError):
    """Raised when a template's required_tools metadata diverges from
    the actual tool refs in its agent document.

    Per Template-Required-Tools-Onboarding-Spec §3.3 / §5.2 — the
    authored agent document remains the operational source of truth for required tools;
    ``required_tools`` is metadata.  This validator enforces
    bidirectional consistency between the two so the onboarding UI
    and publish-review remediation never drift.

    The error carries a list of named codes so callers can render
    structured error messages:

      - ``template.required_tools.missing_metadata:<ref>``  agent document
        references an external tool ref that has no metadata entry.
      - ``template.required_tools.stale_metadata:<ref>``    metadata
        declares a tool ref that the agent document no longer references.
      - ``template.required_tools.builtin_in_metadata:<ref>``  metadata
        declares a built-in tool ref (e.g. ``knowledge.lookup``) that
        does not belong in the onboarding checklist.
    """

    def __init__(self, codes: list[str]) -> None:
        self.codes = list(codes)
        super().__init__("; ".join(codes))


def _collect_agent_document_tool_refs(agent_document_json: dict) -> set[str]:
    """Walk an agent document and collect every tool ref it references."""
    refs: set[str] = set()
    try:
        document = AgentDocument.model_validate(agent_document_json)
    except Exception:
        return refs
    for step in document.steps:
        for tp in step.tool_policy:
            if tp.ref:
                refs.add(tp.ref)
        if step.action_config is None:
            continue
        for ref in step.action_config.callable_system_refs:
            if ref:
                refs.add(ref)
        for ref in step.action_config.callable_api_refs:
            if ref:
                refs.add(ref)
        refs.update(_collect_action_config_integration_refs(step.action_config))
    return refs


def _collect_action_config_integration_refs(action_config: Any) -> set[str]:
    """Collect integration refs from action code using runtime callable aliases.

    ``callable_integrations`` exposes sanitized Python callable names at
    execution time, e.g. ``crm-system`` is called as ``crm_system(...)``.
    Parse the code as Python instead of string-splitting so single quotes,
    spacing, and multiple calls are handled consistently.
    """
    integrations = [ref for ref in action_config.callable_integrations or [] if ref]
    if not integrations:
        return set()

    refs: set[str] = set()
    alias_to_integration = {
        callable_name_for_ref(integration): integration for integration in integrations
    }
    seen_integrations: set[str] = set()
    try:
        tree = ast.parse(action_config.code or "")
    except SyntaxError:
        return set(integrations)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        integration = alias_to_integration.get(node.func.id)
        if integration is None:
            continue
        seen_integrations.add(integration)
        action_name: str | None = None
        for keyword in node.keywords:
            if keyword.arg == "action" and isinstance(keyword.value, ast.Constant):
                if isinstance(keyword.value.value, str) and keyword.value.value:
                    action_name = keyword.value.value
                break
        refs.add(f"{integration}.{action_name}" if action_name else integration)

    refs.update(integration for integration in integrations if integration not in seen_integrations)
    return refs


def validate_template_required_tools(
    *,
    agent_document_json: dict,
    required_tools: list[dict],
    builtin_refs: set[str],
) -> None:
    """Enforce bidirectional consistency between the template's authored agent document
    tool references and its ``required_tools`` metadata.

    Single shared entry point — used by both ``_seed_agent_templates``
    (system templates seeded from JSON) and the admin create/update
    endpoints (user-authored templates).  Keeping the logic in one
    place per reviewer note prevents the invariant from diverging
    between the two surfaces.

    ``builtin_refs`` is the set of refs registered in the platform's
    built-in :class:`ToolRegistry` — refs in this set are
    runtime-resolvable without org configuration and do NOT belong
    in the onboarding checklist (see spec §3.4).

    Raises :class:`TemplateRequiredToolsValidationError` listing every
    invariant violation; never silently corrects.
    """
    agent_refs = _collect_agent_document_tool_refs(agent_document_json)
    metadata_refs = {
        entry.get("tool_ref")
        for entry in required_tools
        if isinstance(entry, dict) and isinstance(entry.get("tool_ref"), str)
    }
    metadata_refs.discard(None)

    external_agent_refs = agent_refs - builtin_refs
    codes: list[str] = []

    for ref in sorted(external_agent_refs - metadata_refs):
        codes.append(f"template.required_tools.missing_metadata:{ref}")
    for ref in sorted(metadata_refs - external_agent_refs):
        if ref in builtin_refs:
            codes.append(f"template.required_tools.builtin_in_metadata:{ref}")
        else:
            codes.append(f"template.required_tools.stale_metadata:{ref}")

    if codes:
        raise TemplateRequiredToolsValidationError(codes)


def _current_builtin_tool_refs(kernel: ConversationKernel) -> set[str]:
    """Single source of truth for built-in vs external tool classification.

    Per spec §3.4: a tool ref is "built-in" iff it is registered in the
    process-local :class:`ToolRegistry`.  External refs live in the
    org-scoped :class:`ToolDefinitionStore` and require customer setup.
    """
    if kernel.tool_runtime is None:
        return set()
    return {spec.ref for spec in kernel.tool_runtime.list_specs()}


def _auto_derive_required_tools(
    *,
    agent_document_json: dict,
    builtin_refs: set[str],
) -> list[TemplateRequiredTool]:
    """Generate placeholder ``TemplateRequiredTool`` entries for every
    external tool ref the agent document references (spec §5.9 — save-as-template
    UX).  The author is expected to refine display_name / description /
    setup_url_path afterwards via PATCH.

    ``setup_url_path`` uses the agent-relative convention (no leading
    slash) — consumers resolve it against the current agent's canvas
    root: ``/agents/{agent_id}/{setup_url_path}``.
    """
    refs = sorted(_collect_agent_document_tool_refs(agent_document_json) - builtin_refs)
    return [
        TemplateRequiredTool(
            tool_ref=ref,
            display_name=ref,
            description=f"Configure the {ref} tool for this template.",
            category=ref.split(".")[0] if "." in ref else "general",
            provider_hints=[],
            setup_url_path=f"canvas?view=library&tool_ref={ref}",
        )
        for ref in refs
    ]


def _resolve_setup_url(*, agent_id: str, template_setup_url_path: str) -> str:
    """Resolve a template's setup_url_path against the current agent.

    Agent-relative paths (no leading slash) are prepended with
    ``/agents/{agent_id}/`` — these are paths that target the agent's
    own canvas (e.g. the Integrations tab).  Absolute paths (leading
    slash) are returned as-is, supporting templates that point at
    org-wide pages like /tools or /settings.
    """
    if template_setup_url_path.startswith("/"):
        return template_setup_url_path
    return f"/agents/{agent_id}/{template_setup_url_path}"


def _seed_agent_templates(
    template_store: "SQLAlchemyAgentTemplateStore",
    *,
    builtin_tool_refs: set[str],
) -> None:
    """Load system agent templates from JSON files and upsert into the store.

    Templates live in ``src/ruhu/templates/system/*.json``.  Each file is a
    self-contained template definition with ``template_id``, ``name``,
    ``slug``, ``description``, ``category``, ``tags``, ``is_featured``,
    ``default_agent_settings``, ``required_tools``, and ``agent_document``.
    This keeps shipped templates as data (easy to diff, localise, version)
    rather than as hand-written Python code.

    Templates failing the required-tools consistency validator are
    skipped with a loud warning — never crashed (avoid blocking boot
    on a template authoring mistake).  Authors will see the same
    validation surface via the admin endpoints (which return 422
    rather than skipping silently).
    """
    templates_dir = Path(__file__).resolve().parent / "templates" / "system"
    if not templates_dir.is_dir():
        return
    for template_path in sorted(templates_dir.glob("*.json")):
        try:
            payload = json.loads(template_path.read_text())
        except Exception as exc:
            logger.warning("Failed to parse agent template %s: %s", template_path.name, exc)
            continue
        required_tools_payload = list(payload.get("required_tools") or [])
        try:
            validate_template_required_tools(
                agent_document_json=dict(payload["agent_document"]),
                required_tools=required_tools_payload,
                builtin_refs=builtin_tool_refs,
            )
        except TemplateRequiredToolsValidationError as exc:
            logger.warning(
                "Auto-deriving required_tools for agent template %s after consistency failure: %s",
                payload.get("template_id", template_path.stem),
                "; ".join(exc.codes),
            )
            required_tools_payload = [
                entry.model_dump(mode="json")
                for entry in _auto_derive_required_tools(
                    agent_document_json=dict(payload["agent_document"]),
                    builtin_refs=builtin_tool_refs,
                )
            ]
        try:
            template_store.upsert_system_template(
                template_id=str(payload["template_id"]),
                name=str(payload["name"]),
                slug=str(payload["slug"]),
                description=str(payload.get("description") or ""),
                category=str(payload.get("category") or "general"),
                tags=list(payload.get("tags") or []),
                default_agent_settings=dict(payload.get("default_agent_settings") or {}),
                agent_document_json=dict(payload["agent_document"]),
                required_tools=required_tools_payload,
                is_featured=bool(payload.get("is_featured", False)),
            )
        except Exception as exc:
            logger.warning(
                "Failed to seed agent template %s: %s",
                payload.get("template_id", template_path.stem),
                exc,
            )


def build_default_app(
    *,
    agent_root: str | Path,
    database_url: str | None = None,
    interpreter_name: str | None = None,
    agent_interpreters: dict[str, str] | None = None,
    model_path: str | Path | None = None,
    auth_resolver: AuthContextResolver | None = None,
    auth_database_url: str | None = None,
    auth_jwt_secret: str | None = None,
    auth_jwt_issuer: str | None = None,
    auth_jwt_private_key_pem: str | None = None,
    auth_jwt_private_key_path: str | Path | None = None,
    auth_jwt_private_key_secret_version: str | None = None,
    auth_jwt_active_kid: str | None = None,
    auth_jwt_verification_jwks: str | None = None,
    auth_jwt_verification_jwks_path: str | Path | None = None,
    auth_jwt_verification_jwks_secret_version: str | None = None,
    runtime_settings: RuntimeSettings | None = None,
    bootstrap_organization_id: str | None = None,
) -> FastAPI:
    settings = runtime_settings or RuntimeSettings.from_env()
    email_sender = build_email_sender(settings)
    agent_seed_root_path = Path(agent_root).resolve()
    resolved_database_url = resolve_database_url(
        database_url=database_url if database_url is not None else settings.database_url,
    )
    resolved_auth_database_url = (
        auth_database_url if auth_database_url is not None else settings.auth_database_url
    )
    if resolved_auth_database_url is None:
        resolved_auth_database_url = resolved_database_url
    resolved_auth_jwt_secret = auth_jwt_secret if auth_jwt_secret is not None else settings.auth_jwt_secret
    resolved_auth_jwt_issuer = auth_jwt_issuer if auth_jwt_issuer is not None else settings.auth_jwt_issuer
    resolved_auth_jwt_private_key_pem = (
        auth_jwt_private_key_pem
        if auth_jwt_private_key_pem is not None
        else settings.auth_jwt_private_key_pem
    )
    resolved_auth_jwt_private_key_path = (
        Path(auth_jwt_private_key_path)
        if auth_jwt_private_key_path is not None
        else settings.auth_jwt_private_key_path
    )
    resolved_auth_jwt_private_key_secret_version = (
        auth_jwt_private_key_secret_version
        if auth_jwt_private_key_secret_version is not None
        else settings.auth_jwt_private_key_secret_version
    )
    resolved_auth_jwt_active_kid = (
        auth_jwt_active_kid
        if auth_jwt_active_kid is not None
        else settings.auth_jwt_active_kid
    )
    resolved_auth_jwt_verification_jwks = (
        auth_jwt_verification_jwks
        if auth_jwt_verification_jwks is not None
        else settings.auth_jwt_verification_jwks
    )
    resolved_auth_jwt_verification_jwks_path = (
        Path(auth_jwt_verification_jwks_path)
        if auth_jwt_verification_jwks_path is not None
        else settings.auth_jwt_verification_jwks_path
    )
    resolved_auth_jwt_verification_jwks_secret_version = (
        auth_jwt_verification_jwks_secret_version
        if auth_jwt_verification_jwks_secret_version is not None
        else settings.auth_jwt_verification_jwks_secret_version
    )
    persistent_auth_runtime: PersistentAuthRuntime | None = None
    # RP-3.2: pure construction lives in the composition root (data → llm →
    # kernel), including secret-boundary enforcement and the opt-in agent
    # bootstrap. Seeding and startup side effects (pricing catalog, template
    # seeding, async engine init, knowledge startup) stay below, in their
    # original order.
    rt = build_runtime(
        settings=settings,
        database_url=resolved_database_url,
        agent_seed_root=agent_seed_root_path,
        bootstrap_organization_id=bootstrap_organization_id,
        interpreter_name=interpreter_name,
        agent_interpreters=agent_interpreters,
        model_path=model_path,
        audit_router=None,
    )
    data = rt.data
    runtime_session_factory = data.session_factory
    knowledge_runtime = data.knowledge_runtime
    billing_service = data.billing_service
    agent_registry = data.agent_registry
    tool_runtime = rt.tool_runtime
    kernel = rt.kernel
    auth_session_factory = (
        runtime_session_factory
        if resolved_auth_database_url == resolved_database_url
        else build_session_factory(
            resolved_auth_database_url,
            pool_size=settings.sync_db_pool_size,
            max_overflow=settings.sync_db_max_overflow,
            pool_recycle=settings.sync_db_pool_recycle,
            pool_timeout=settings.sync_db_pool_timeout,
            statement_timeout_ms=settings.sync_db_statement_timeout_ms,
        )
    )
    auth_signing_enabled = (
        resolved_auth_jwt_secret is not None
        or resolved_auth_jwt_private_key_pem is not None
        or resolved_auth_jwt_private_key_path is not None
        or resolved_auth_jwt_private_key_secret_version is not None
    )
    if auth_resolver is None and auth_signing_enabled:
        persistent_auth_runtime = build_persistent_auth_runtime(
            session_factory=auth_session_factory,
            secret=resolved_auth_jwt_secret,
            issuer=resolved_auth_jwt_issuer,
            private_key_pem=resolved_auth_jwt_private_key_pem,
            private_key_path=resolved_auth_jwt_private_key_path,
            private_key_secret_version=resolved_auth_jwt_private_key_secret_version,
            active_kid=resolved_auth_jwt_active_kid,
            verification_jwks=resolved_auth_jwt_verification_jwks,
            verification_jwks_path=resolved_auth_jwt_verification_jwks_path,
            verification_jwks_secret_version=resolved_auth_jwt_verification_jwks_secret_version,
            require_asymmetric_tokens=settings.auth_require_asymmetric_tokens,
            open_signup_domains=settings.auth_open_signup_domains,
        )
    # Tests, CLI flows, and in-process ASGI clients may bypass lifespan startup.
    # Eager startup keeps the seeded knowledge base and async DB engine available
    # for grounded answers, readiness probes, and async route handlers.
    init_async_engine(
        resolved_database_url,
        pool_size=settings.async_db_pool_size,
        max_overflow=settings.async_db_max_overflow,
        pool_recycle=settings.async_db_pool_recycle,
        pool_timeout=settings.async_db_pool_timeout,
        statement_timeout_ms=settings.async_db_statement_timeout_ms,
    )
    knowledge_runtime.startup()
    _template_store = SQLAlchemyAgentTemplateStore(runtime_session_factory)
    # H4: rt.builtin_tool_refs was snapshotted before browser_task.create
    # registration — template seeding keeps consuming exactly that set.
    _seed_agent_templates(_template_store, builtin_tool_refs=rt.builtin_tool_refs)
    kpi_runtime = build_kpi_runtime(
        session_factory=runtime_session_factory,
        agent_registry=agent_registry,
    )
    intent_tags_runtime = build_intent_tags_runtime(
        session_factory=runtime_session_factory,
        default_adapter_name=(
            "hosted"
            if settings.intent_tags_classifier_base_url
            else "ruhu-general"
        ),
    )
    notification_store = SQLAlchemyNotificationStore(runtime_session_factory)
    billing_service.seed_pricing_catalog()
    ticket_system_service = TicketSystemService(runtime_session_factory)
    provider_cost_store = SQLAlchemyProviderCostStore(runtime_session_factory)
    phone_number_registry = PhoneNumberRegistryService(runtime_session_factory)
    phone_number_audit_service = PhoneNumberAuditService(runtime_session_factory)

    journey_definition_store = SQLAlchemyJourneyDefinitionStore(runtime_session_factory)
    journey_instance_store = SQLAlchemyJourneyInstanceStore(runtime_session_factory)
    journey_runtime_job_store = SQLAlchemyJourneyRuntimeJobStore(runtime_session_factory)

    def _journey_review_agent_documents(
        definition: JourneyDefinition,
        organization_id: str | None,
    ) -> tuple[list[AgentDocument], list[str]]:
        agent_documents: list[AgentDocument] = []
        missing_agent_ids: list[str] = []
        for agent_id in definition.scope.agent_ids:
            try:
                registration = agent_registry.get_agent_registration(agent_id, organization_id=organization_id)
            except KeyError:
                missing_agent_ids.append(agent_id)
                continue
            version_id = registration.current_draft_version_id or registration.current_published_version_id
            if version_id is None:
                missing_agent_ids.append(agent_id)
                continue
            snapshot = agent_registry.get_version_snapshot(
                version_id,
                organization_id=organization_id,
            )
            agent_documents.append(
                snapshot.agent_document.model_copy(
                    update={
                        "metadata": {
                            **dict(snapshot.agent_document.metadata),
                            "agent_id": snapshot.agent_id,
                            "agent_name": snapshot.name,
                        }
                    }
                )
            )
        return agent_documents, missing_agent_ids

    journey_service = JourneyService(
        journey_definition_store,
        journey_instance_store,
        agent_resolver=_journey_review_agent_documents,
        available_tool_refs_provider=lambda: [spec.ref for spec in tool_runtime.list_specs()],
    )
    simulation_fixture_store = SQLAlchemySimulationFixtureStore(runtime_session_factory)
    evaluation_run_store = SQLAlchemyEvaluationRunStore(runtime_session_factory)
    evaluation_service = EvaluationService(kernel=kernel, run_store=evaluation_run_store)
    evaluation_runtime = EvaluationRuntime(
        service=evaluation_service,
        max_workers=settings.simulation_eval_workers,
    )
    # RP-3.1 step 18 (RP-3.2 finale): everything constructed above and beyond
    # the ComposedRuntime travels as one frozen ApiServices bundle.
    api_services = ApiServices(
        kpi_runtime=kpi_runtime,
        intent_tags_runtime=intent_tags_runtime,
        notification_store=notification_store,
        ticket_system_service=ticket_system_service,
        provider_cost_store=provider_cost_store,
        phone_number_registry=phone_number_registry,
        phone_number_audit_service=phone_number_audit_service,
        journey_definition_store=journey_definition_store,
        journey_instance_store=journey_instance_store,
        journey_runtime_job_store=journey_runtime_job_store,
        journey_service=journey_service,
        simulation_fixture_store=simulation_fixture_store,
        evaluation_run_store=evaluation_run_store,
        evaluation_service=evaluation_service,
        evaluation_runtime=evaluation_runtime,
        template_store=_template_store,
        email_sender=email_sender,
        auth_resolver=auth_resolver if persistent_auth_runtime is None else persistent_auth_runtime.auth_resolver,
        auth_service=None if persistent_auth_runtime is None else persistent_auth_runtime.auth_service,
        identity_store=None if persistent_auth_runtime is None else persistent_auth_runtime.identity_store,
        tenant_identity_repositories=(
            None if persistent_auth_runtime is None else persistent_auth_runtime.tenant_repositories
        ),
        auth_session_factory=auth_session_factory,
    )
    app = create_app(
        rt,
        api_services,
        # H10: the resolved non-auth fields (database_url, interpreter_name,
        # classifier_model_path, agent_interpreters) are already baked into
        # the settings carried by the composition root.
        settings=replace(
            data.settings,
            auth_database_url=resolved_auth_database_url,
            auth_jwt_secret=resolved_auth_jwt_secret,
            auth_jwt_issuer=resolved_auth_jwt_issuer,
            auth_jwt_private_key_pem=resolved_auth_jwt_private_key_pem,
            auth_jwt_private_key_path=resolved_auth_jwt_private_key_path,
            auth_jwt_private_key_secret_version=resolved_auth_jwt_private_key_secret_version,
            auth_jwt_active_kid=resolved_auth_jwt_active_kid,
            auth_jwt_verification_jwks=resolved_auth_jwt_verification_jwks,
            auth_jwt_verification_jwks_path=resolved_auth_jwt_verification_jwks_path,
            auth_jwt_verification_jwks_secret_version=resolved_auth_jwt_verification_jwks_secret_version,
        ),
        bootstrap_organization_id=bootstrap_organization_id,
        agent_seed_root=agent_seed_root_path,
    )
    return app


def create_app(
    runtime: ComposedRuntime,
    services: ApiServices | None = None,
    *,
    settings: RuntimeSettings | None = None,
    bootstrap_organization_id: str | None = None,
    agent_seed_root: Path | None = None,
) -> FastAPI:
    """Assemble the FastAPI app from a composed runtime + service bundle.

    RP-3.1 step 18 (RP-3.2 finale): the former 26-keyword signature collapsed
    onto two objects.  ``runtime`` carries the kernel host (data → llm →
    kernel; see ``ruhu.composition``); ``services`` carries everything
    ``build_default_app`` constructs on top (see
    ``ruhu.services.api_services``).  Direct callers (tests, the OpenAPI
    export) build a sparse runtime via ``composition.build_minimal_runtime``
    and an ``ApiServices`` with only the fields they exercise — every omitted
    service keeps the in-memory fallback the old per-keyword defaults had.

    When ``services.template_store``/``runtime.data.connection_store`` are
    None (direct-caller path), audit events from credential decrypts log the
    "no audit router" warning until a router is wired in some other way —
    identical to the old optional-kwarg behavior.
    """
    services = services if services is not None else ApiServices()
    # Rebind the retired keyword parameters onto locals so the assembly body
    # below stays textually unchanged (the RP-3.1 rebinding convention).
    kernel = runtime.kernel
    agent_registry = runtime.data.agent_registry
    tool_backend = runtime.tool_backend
    knowledge_runtime = runtime.data.knowledge_runtime
    live_eval_runtime = runtime.data.live_eval_runtime
    rules_runtime = runtime.data.rules_runtime
    attachment_runtime = runtime.data.attachment_runtime
    browser_task_service = runtime.browser_task_service
    realtime_control_plane = runtime.data.realtime_control_plane
    jobs_store = runtime.data.jobs_store
    billing_service = runtime.data.billing_service
    billing_store = runtime.data.billing_store
    connection_store = runtime.data.connection_store
    runtime_session_factory = runtime.data.session_factory
    kpi_runtime = services.kpi_runtime
    intent_tags_runtime = services.intent_tags_runtime
    notification_store = services.notification_store
    ticket_system_service = services.ticket_system_service
    provider_cost_store = services.provider_cost_store
    phone_number_registry = services.phone_number_registry
    phone_number_audit_service = services.phone_number_audit_service
    phone_number_operations_service = services.phone_number_operations_service
    journey_definition_store = services.journey_definition_store
    journey_instance_store = services.journey_instance_store
    journey_runtime_job_store = services.journey_runtime_job_store
    journey_service = services.journey_service
    journey_runtime = services.journey_runtime
    simulation_fixture_store = services.simulation_fixture_store
    evaluation_run_store = services.evaluation_run_store
    evaluation_service = services.evaluation_service
    evaluation_runtime = services.evaluation_runtime
    template_store = services.template_store
    email_sender = services.email_sender
    auth_resolver = services.auth_resolver
    auth_service = services.auth_service
    identity_store = services.identity_store
    tenant_identity_repositories = services.tenant_identity_repositories
    auth_session_factory = services.auth_session_factory
    runtime_settings = settings

    managed_evaluation_runtime: EvaluationRuntime | None = evaluation_runtime
    managed_journey_runtime: JourneyRuntime | None = journey_runtime
    managed_tool_integration_worker: ToolIntegrationWorkerRuntime | None = None
    managed_sentiment_worker: ConversationSentimentWorker | None = None
    # Live (continuous) evaluation runtime — built only when explicitly
    # opted in via RUHU_LIVE_EVAL_ENABLED. None in dev/test by default
    # so the foundation can land without altering background-thread
    # behaviour for existing deployments.
    managed_live_eval_runtime = live_eval_runtime

    @asynccontextmanager
    async def _lifespan(_: FastAPI):
        # Async DB engine — initialised first so startup tasks can use async sessions.
        if effective_runtime_settings.database_url:
            init_async_engine(
                effective_runtime_settings.database_url,
                pool_size=effective_runtime_settings.async_db_pool_size,
                max_overflow=effective_runtime_settings.async_db_max_overflow,
                pool_recycle=effective_runtime_settings.async_db_pool_recycle,
                pool_timeout=effective_runtime_settings.async_db_pool_timeout,
                statement_timeout_ms=effective_runtime_settings.async_db_statement_timeout_ms,
            )

        if knowledge_runtime is not None:
            knowledge_runtime.startup()
        if managed_journey_runtime is not None:
            managed_journey_runtime.startup()
        if managed_tool_integration_worker is not None:
            # No-op unless RUHU_TOOL_INTEGRATION_EMBEDDED_WORKER_ENABLED opts
            # into legacy in-API threads; jobs drain in the worker process
            # (tool_integration.tick) by default.
            managed_tool_integration_worker.startup()
        if managed_live_eval_runtime is not None:
            managed_live_eval_runtime.start()

        # ── Phase 1 scalability: PgNotify dispatcher + cleanup tasks ──
        from .realtime.pg_notify import PgNotifyDispatcher
        from .realtime.service import run_stale_session_reconciler, run_outbox_cleanup

        _pg_notify_dispatcher = PgNotifyDispatcher(
            direct_url=os.getenv("RUHU_PG_DIRECT_URL", ""),
        )
        await _pg_notify_dispatcher.start()
        app.state.pg_notify_dispatcher = _pg_notify_dispatcher

        # Explicit kernel thread pool (A1). RP-2.4: no longer the loop's
        # default executor — kernel turn sites read it from app.state.
        _kernel_executor = build_kernel_executor(effective_runtime_settings)
        app.state.kernel_executor = _kernel_executor

        _cleanup_stop_event = asyncio.Event()
        _stale_session_task = asyncio.create_task(
            run_stale_session_reconciler(
                realtime_control_plane,
                stop_event=_cleanup_stop_event,
            )
        ) if realtime_control_plane is not None else None
        _outbox_cleanup_task = asyncio.create_task(
            run_outbox_cleanup(
                realtime_control_plane.outbox,
                stop_event=_cleanup_stop_event,
            )
        ) if realtime_control_plane is not None else None

        # Start audit flusher if audit system is wired up
        _audit_flusher_task = None
        _audit_stop_event = None
        if hasattr(app.state, "audit_router"):
            import asyncio as _aio
            from ruhu.audit.flusher import run_audit_flusher as _run_flusher
            _audit_stop_event = _aio.Event()
            _audit_flusher_task = _aio.create_task(
                _run_flusher(
                    _audit_queue,
                    _audit_store,
                    stop_event=_audit_stop_event,
                )
            )

        # Initialize event sourcing: register projection handlers
        if hasattr(app.state, "event_bus"):
            bootstrap_event_handlers(app.state.event_bus)

        try:
            yield
        finally:
            # Stop audit flusher (drain remaining events).  Bound the wait so
            # a slow DB cannot hang shutdown indefinitely — pod schedulers
            # send SIGKILL past their own grace period anyway, so we'd rather
            # force a clean cancel than block.
            if _audit_flusher_task is not None and _audit_stop_event is not None:
                _audit_stop_event.set()
                try:
                    await asyncio.wait_for(asyncio.shield(_audit_flusher_task), timeout=30.0)
                except asyncio.TimeoutError:
                    import logging as _logging
                    _logging.getLogger(__name__).warning(
                        "audit flusher drain exceeded 30s; cancelling (some events may be dropped)"
                    )
                    _audit_flusher_task.cancel()
                    try:
                        await _audit_flusher_task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass

            # Phase 1 cleanup: signal stop, then drain-with-timeout before
            # cancelling. Bare .cancel() on stale_session_task /
            # outbox_cleanup_task discards buffered writes to
            # realtime_sessions / realtime_outbox mid-commit.
            _cleanup_stop_event.set()
            for _task, _name in (
                (_stale_session_task, "stale_session_reconciler"),
                (_outbox_cleanup_task, "outbox_cleanup"),
            ):
                if _task is None:
                    continue
                try:
                    await asyncio.wait_for(asyncio.shield(_task), timeout=10.0)
                except asyncio.TimeoutError:
                    import logging as _logging
                    _logging.getLogger(__name__).warning(
                        "%s did not finish in 10s; cancelling", _name
                    )
                    _task.cancel()
                    try:
                        await _task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                except asyncio.CancelledError:
                    pass

            await _pg_notify_dispatcher.stop()
            # wait=True lets in-flight kernel turns finish and persist traces.
            # No shutdown timeout in 3.11/3.12; pod grace period bounds it.
            app.state.kernel_executor = None
            _kernel_executor.shutdown(wait=True)

            if managed_live_eval_runtime is not None:
                managed_live_eval_runtime.stop()
            if managed_tool_integration_worker is not None:
                managed_tool_integration_worker.shutdown()
            if managed_journey_runtime is not None:
                managed_journey_runtime.shutdown()
            if managed_evaluation_runtime is not None:
                managed_evaluation_runtime.shutdown()
            if attachment_runtime is not None:
                attachment_runtime.shutdown()
            if knowledge_runtime is not None:
                knowledge_runtime.shutdown()
            await close_async_engine()

    app = FastAPI(title="Ruhu Agent Runtime", version="0.1.0", lifespan=_lifespan)

    _atlas_store = None
    _atlas_readiness_store = None
    _atlas_readiness_artifact_store = None
    if runtime_session_factory is not None:
        from .atlas_store import SQLAlchemyAtlasStore as _SQLAlchemyAtlasStore
        from .atlas_readiness_store import SQLAlchemyAtlasReadinessStore as _SQLAlchemyAtlasReadinessStore

        _atlas_store = _SQLAlchemyAtlasStore(runtime_session_factory)
        _atlas_readiness_store = _SQLAlchemyAtlasReadinessStore(runtime_session_factory)
        app.state.atlas_store = _atlas_store
        app.state.atlas_readiness_store = _atlas_readiness_store

    # ── Audit system ─────────────────────────────────────────────────────────
    import asyncio as _asyncio
    from ruhu.audit.store import InMemoryAuditStore as _InMemoryAuditStore
    from ruhu.audit.store import SQLAlchemyAuditStore as _SQLAlchemyAuditStore
    from ruhu.audit.router import AuditEventRouter as _AuditEventRouter

    _audit_queue: _asyncio.Queue = _asyncio.Queue(maxsize=50_000)
    if runtime_session_factory is not None:
        _audit_store = _SQLAlchemyAuditStore(runtime_session_factory)
    else:
        _audit_store = _InMemoryAuditStore()
    _audit_router_instance = _AuditEventRouter(store=_audit_store, queue=_audit_queue)
    app.state.audit_router = _audit_router_instance
    app.state.audit_store = _audit_store
    # Late-bind the audit router into the shared credential store built
    # before the app existed.  From this point on, every
    # ``credential.decrypted`` read lands in the audit trail.  Only runs
    # on the ``build_default_app`` path — when ``create_app`` is called
    # directly the caller is expected to wire audit separately (tests
    # typically don't care about this telemetry).
    if connection_store is not None:
        connection_store.set_audit_router(_audit_router_instance)
        app.state.connection_store = connection_store
    if browser_task_service is not None:
        browser_task_service.audit_router = _audit_router_instance

    effective_runtime_settings = runtime_settings or RuntimeSettings.from_env()
    if runtime_session_factory is not None:
        try:
            from .blob_store import build_blob_store_from_settings as _build_blob_store_from_settings

            _atlas_readiness_artifact_store = _build_blob_store_from_settings(effective_runtime_settings)
        except Exception:
            logger.exception("atlas readiness artifact store initialization failed")
        app.state.atlas_readiness_artifact_store = _atlas_readiness_artifact_store
    _enforce_secret_boundary_policy(effective_runtime_settings)
    whatsapp_meta_channels = parse_whatsapp_meta_channels(effective_runtime_settings.whatsapp_meta_channels)
    phone_number_routes = parse_phone_number_routes(effective_runtime_settings.phone_number_routes)
    whatsapp_projection_dispatcher = (
        None
        if realtime_control_plane is None
        else MetaWhatsAppProjectionDispatcher(
            control_plane=realtime_control_plane,
            configs=whatsapp_meta_channels,
        )
    )
    livekit_phone_adapter_config = LiveKitAdapterConfig.from_settings(effective_runtime_settings)
    livekit_token_issuer = None if livekit_phone_adapter_config is None else LiveKitTokenIssuer(livekit_phone_adapter_config)
    livekit_dispatch_client = (
        None if livekit_phone_adapter_config is None else LiveKitDispatchClient(livekit_phone_adapter_config)
    )
    livekit_room_runtime_client = (
        None if livekit_phone_adapter_config is None else LiveKitRoomRuntimeClient(livekit_phone_adapter_config)
    )
    effective_jobs_store = jobs_store or InMemoryJobStore()
    effective_simulation_fixture_store = simulation_fixture_store or InMemorySimulationFixtureStore()
    effective_evaluation_run_store = evaluation_run_store or InMemoryEvaluationRunStore()
    effective_journey_definition_store = journey_definition_store or InMemoryJourneyDefinitionStore()
    effective_journey_instance_store = journey_instance_store or InMemoryJourneyInstanceStore()

    def _journey_review_agent_documents(
        definition: JourneyDefinition,
        organization_id: str | None,
    ) -> tuple[list[AgentDocument], list[str]]:
        agent_documents: list[AgentDocument] = []
        missing_agent_ids: list[str] = []
        for agent_id in definition.scope.agent_ids:
            try:
                registration = agent_registry.get_agent_registration(agent_id, organization_id=organization_id)
            except KeyError:
                missing_agent_ids.append(agent_id)
                continue
            version_id = registration.current_draft_version_id or registration.current_published_version_id
            if version_id is None:
                missing_agent_ids.append(agent_id)
                continue
            snapshot = agent_registry.get_version_snapshot(
                version_id,
                organization_id=organization_id,
            )
            agent_documents.append(
                snapshot.agent_document.model_copy(
                    update={
                        "metadata": {
                            **dict(snapshot.agent_document.metadata),
                            "agent_id": snapshot.agent_id,
                            "agent_name": snapshot.name,
                        }
                    }
                )
            )
        return agent_documents, missing_agent_ids

    effective_journey_service = journey_service or JourneyService(
        effective_journey_definition_store,
        effective_journey_instance_store,
        agent_resolver=_journey_review_agent_documents,
        available_tool_refs_provider=(
            lambda: [] if kernel.tool_runtime is None else [spec.ref for spec in kernel.tool_runtime.list_specs()]
        ),
    )
    effective_evaluation_service = evaluation_service or EvaluationService(
        kernel=kernel,
        run_store=effective_evaluation_run_store,
    )
    effective_evaluation_runtime = managed_evaluation_runtime or EvaluationRuntime(
        service=effective_evaluation_service,
        max_workers=effective_runtime_settings.simulation_eval_workers,
    )
    managed_evaluation_runtime = effective_evaluation_runtime
    intent_tags_classifier_registry = build_intent_tags_classifier_registry(
        default_interpreter_name=effective_runtime_settings.interpreter_name,
        agent_interpreters=effective_runtime_settings.agent_interpreters,
        model_path=effective_runtime_settings.classifier_model_path,
        hosted_classifier_base_url=effective_runtime_settings.intent_tags_classifier_base_url,
        hosted_classifier_api_key=effective_runtime_settings.intent_tags_classifier_api_key,
        hosted_classifier_api_key_secret_version=(
            effective_runtime_settings.intent_tags_classifier_api_key_secret_version
        ),
        hosted_classifier_timeout_seconds=effective_runtime_settings.intent_tags_classifier_timeout_seconds,
        hosted_classifier_max_retries=effective_runtime_settings.intent_tags_classifier_max_retries,
        hosted_classifier_retry_backoff_seconds=(
            effective_runtime_settings.intent_tags_classifier_retry_backoff_seconds
        ),
    )
    journey_tracker = wire_journey_runtime_integration(
        kernel=kernel,
        definition_store=effective_journey_definition_store,
        instance_store=effective_journey_instance_store,
        realtime_control_plane=realtime_control_plane,
    )
    effective_journey_runtime = managed_journey_runtime or JourneyRuntime(
        service=effective_journey_service,
        tracker=journey_tracker,
        max_workers=effective_runtime_settings.journey_runtime_workers,
        job_store=journey_runtime_job_store,
        embedded_worker_enabled=effective_runtime_settings.journey_runtime_embedded_worker_enabled,
        poll_interval_seconds=effective_runtime_settings.journey_runtime_poll_interval_seconds,
        job_lease_seconds=effective_runtime_settings.journey_runtime_job_lease_seconds,
        job_heartbeat_interval_seconds=(
            effective_runtime_settings.journey_runtime_job_heartbeat_interval_seconds
        ),
        failure_alert_threshold=effective_runtime_settings.journey_runtime_failure_alert_threshold,
        failure_alert_window_seconds=(
            effective_runtime_settings.journey_runtime_failure_alert_window_seconds
        ),
        # The abandonment scheduler belongs to the worker process
        # (journey_runtime.tick in ruhu.worker) — never a thread in the API.
        abandonment_sweep_enabled=False,
        abandonment_sweep_interval_seconds=effective_runtime_settings.journey_abandonment_sweep_interval_seconds,
    )
    managed_journey_runtime = effective_journey_runtime
    if kernel.tool_runtime is not None and kernel.tool_runtime.integration_runtime is not None:
        # Always constructed — the integration-webhook route processes
        # callbacks through it — but its embedded thread loop is legacy
        # opt-in; jobs drain in the worker process (tool_integration.tick)
        # by default.
        managed_tool_integration_worker = ToolIntegrationWorkerRuntime(
            tool_runtime=kernel.tool_runtime,
            integration_runtime=kernel.tool_runtime.integration_runtime,
            embedded_worker_enabled=(
                effective_runtime_settings.tool_integration_embedded_worker_enabled
            ),
        )
    intent_tags_integrator = (
        None
        if intent_tags_runtime is None
        else IntentTagsRuntimeIntegrator(
            intent_tags_runtime,
            classifier_registry=intent_tags_classifier_registry,
            realtime_control_plane=realtime_control_plane,
            provider_cost_store=provider_cost_store,
        )
    )
    semantic_summary_webhook_dispatcher = (
        None
        if intent_tags_runtime is None or realtime_control_plane is None
        else SemanticSummaryWebhookDispatcher(
            control_plane=realtime_control_plane,
            webhook_service=intent_tags_runtime.webhook_service,
        )
    )
    # View-ready and sentiment run in the worker process (view_ready.tick /
    # sentiment.tick) by default; the embedded flags below keep an opt-in
    # legacy seam that constructs the single-pass objects in the API process
    # for direct driving (there is no in-API thread loop any more).
    managed_attachment_view_ready_worker: AttachmentViewReadyWorker | None = None
    if (
        runtime_session_factory is not None
        and kernel is not None
        and agent_registry is not None
        and effective_runtime_settings.attachments_view_ready_worker_enabled
        and effective_runtime_settings.attachments_view_ready_embedded_worker_enabled
    ):
        managed_attachment_view_ready_worker = AttachmentViewReadyWorker(
            session_factory=runtime_session_factory,
            kernel=kernel,
            agent_registry=agent_registry,
            batch_size=effective_runtime_settings.attachments_view_ready_worker_batch_size,
        )
    if (
        runtime_session_factory is not None
        and effective_runtime_settings.sentiment_worker_enabled
        and effective_runtime_settings.sentiment_embedded_worker_enabled
        and effective_runtime_settings.sentiment_worker_llm_base_url
        and effective_runtime_settings.sentiment_worker_llm_api_key
    ):
        managed_sentiment_worker = ConversationSentimentWorker(
            session_factory=runtime_session_factory,
            llm_base_url=effective_runtime_settings.sentiment_worker_llm_base_url,
            llm_api_key=effective_runtime_settings.sentiment_worker_llm_api_key,
            model=effective_runtime_settings.sentiment_worker_model,
            batch_size=effective_runtime_settings.sentiment_worker_batch_size,
            max_attempts=effective_runtime_settings.sentiment_worker_max_attempts,
            backoff_base_seconds=effective_runtime_settings.sentiment_worker_backoff_base_seconds,
            timeout_seconds=effective_runtime_settings.sentiment_worker_timeout_seconds,
        )
    # The browser task runtime lives in the worker process — ruhu.worker
    # composes it and drains tasks via browser_tasks.tick; the API only
    # holds the service for CRUD/approval routes.
    effective_notification_store = notification_store or InMemoryNotificationStore()
    app.state.runtime_settings = effective_runtime_settings
    app.state.whatsapp_meta_channels = whatsapp_meta_channels
    app.state.phone_number_routes = phone_number_routes
    app.state.notification_store = effective_notification_store
    if phone_number_registry is not None:
        app.state.phone_number_registry = phone_number_registry
    if phone_number_audit_service is not None:
        app.state.phone_number_audit_service = phone_number_audit_service
    if phone_number_operations_service is not None:
        app.state.phone_number_operations_service = phone_number_operations_service
    app.state.livekit_phone_adapter_config = livekit_phone_adapter_config
    app.state.livekit_token_issuer = livekit_token_issuer
    app.state.livekit_dispatch_client = livekit_dispatch_client
    app.state.livekit_room_runtime_client = livekit_room_runtime_client
    if whatsapp_projection_dispatcher is not None:
        app.state.whatsapp_projection_dispatcher = whatsapp_projection_dispatcher
    if kernel.tool_runtime is not None:
        app.state.tool_runtime = kernel.tool_runtime
        if kernel.tool_runtime.integration_runtime is not None:
            app.state.tool_integration_runtime = kernel.tool_runtime.integration_runtime
    if managed_tool_integration_worker is not None:
        app.state.tool_integration_worker = managed_tool_integration_worker
    if tool_backend is not None:
        app.state.tool_backend = tool_backend
    if knowledge_runtime is not None:
        app.state.knowledge_runtime = knowledge_runtime
    if kpi_runtime is not None:
        app.state.kpi_runtime = kpi_runtime
    if managed_live_eval_runtime is not None:
        app.state.live_eval_runtime = managed_live_eval_runtime
    if intent_tags_runtime is not None:
        app.state.intent_tags_runtime = intent_tags_runtime
    if semantic_summary_webhook_dispatcher is not None:
        app.state.semantic_summary_webhook_dispatcher = semantic_summary_webhook_dispatcher
    if managed_attachment_view_ready_worker is not None:
        app.state.attachment_view_ready_worker = managed_attachment_view_ready_worker
    if managed_sentiment_worker is not None:
        app.state.sentiment_worker = managed_sentiment_worker
    if rules_runtime is not None:
        app.state.rules_runtime = rules_runtime
    if attachment_runtime is not None:
        app.state.attachment_runtime = attachment_runtime
    if browser_task_service is not None:
        app.state.browser_task_service = browser_task_service
    if ticket_system_service is not None:
        app.state.ticket_system_service = ticket_system_service
    if realtime_control_plane is not None:
        app.state.realtime_control_plane = realtime_control_plane
    if provider_cost_store is not None:
        app.state.provider_cost_store = provider_cost_store
    if effective_journey_definition_store is not None:
        app.state.journey_definition_store = effective_journey_definition_store
    if effective_journey_instance_store is not None:
        app.state.journey_instance_store = effective_journey_instance_store
    if effective_journey_service is not None:
        app.state.journey_service = effective_journey_service
    app.state.journey_tracker = journey_tracker
    app.state.journey_runtime = effective_journey_runtime
    app.state.simulation_fixture_store = effective_simulation_fixture_store
    app.state.evaluation_run_store = effective_evaluation_run_store
    app.state.evaluation_service = effective_evaluation_service
    app.state.evaluation_runtime = effective_evaluation_runtime
    if auth_resolver is not None:
        app.state.auth_context_resolver = auth_resolver
        # Audit middleware is registered BEFORE AuthContextMiddleware so that
        # in the Starlette stack it runs AFTER auth context is populated.
        # (Starlette processes middleware in reverse registration order.)
        if hasattr(app.state, "audit_router"):
            from ruhu.audit.middleware import AuditMiddleware as _AuditMW
            app.add_middleware(_AuditMW, router=app.state.audit_router)
        app.add_middleware(AuthContextMiddleware, resolver=auth_resolver)
        app.state.auth_service = auth_resolver.auth_service if auth_service is None else auth_service
    elif auth_service is not None:
        app.state.auth_service = auth_service
    effective_auth_service = getattr(app.state, "auth_service", None)
    if effective_auth_service is not None:
        app.state.jwt_codec = effective_auth_service.jwt_codec
    effective_identity_store = identity_store
    if effective_identity_store is None and effective_auth_service is not None:
        effective_identity_store = effective_auth_service.identity_store
    if effective_identity_store is not None:
        app.state.identity_store = effective_identity_store
    effective_tenant_identity_repositories = tenant_identity_repositories
    if effective_tenant_identity_repositories is None and effective_identity_store is not None:
        effective_tenant_identity_repositories = TenantIdentityRepositoryFactory(identity_store=effective_identity_store)
    if effective_tenant_identity_repositories is not None:
        app.state.tenant_identity_repositories = effective_tenant_identity_repositories
    auth_enabled = auth_resolver is not None and effective_auth_service is not None
    _enforce_auth_signing_policy(
        settings=effective_runtime_settings,
        auth_enabled=auth_enabled,
        auth_service=effective_auth_service,
    )
    effective_email_sender = email_sender
    if auth_enabled and effective_email_sender is None:
        effective_email_sender = DevOutboxEmailSender()
    if effective_email_sender is not None:
        app.state.email_sender = effective_email_sender
        if isinstance(effective_email_sender, DevOutboxEmailSender):
            app.state.email_outbox = effective_email_sender.entries
        if isinstance(effective_email_sender, RetryingEmailSender):
            app.state.email_delivery_sender = effective_email_sender
    # Org-level rate limiter (Phase 5): create early so install_* routers can wire it in.
    # Tier-aware (Phase 2), endpoint hard caps (Phase 3A), admin bypass (Phase 3B).
    # Only active when auth is enabled — the limiter depends on require_authenticated_context.
    _org_rate_limiter = (
        make_org_rate_limiter(
            effective_runtime_settings.redis_url,
            billing_store=billing_store,
            bypass_secret=effective_runtime_settings.internal_api_secret,
        )
        if auth_enabled
        else None
    )
    app.state.org_rate_limiter = _org_rate_limiter

    if billing_service is not None and billing_store is not None:
        install_billing_router(
            app,
            billing_service=billing_service,
            billing_store=billing_store,
            stripe_secret_key=effective_runtime_settings.stripe_secret_key,
            stripe_webhook_secret=effective_runtime_settings.stripe_webhook_secret,
            billing_mode=effective_runtime_settings.stripe_billing_mode,
            frontend_url=effective_runtime_settings.frontend_url,
            email_sender=effective_email_sender,
            identity_store=effective_identity_store,
            rate_limiter=_org_rate_limiter,
        )

    # Auth dependencies: built via factories from ruhu.auth_deps so extracted
    # routers (conversations_router, future agents_router, etc.) can call the
    # same factories instead of accepting the dep as a function parameter.
    _require_runtime_author_context = make_author_context_dep(auth_enabled)
    _require_runtime_reviewer_context = make_reviewer_context_dep(auth_enabled)
    # Org-scope resolution: built via factories from ruhu.services.org_scope
    # (RP-3.1 step 1) so extracted routers can call the same factories. The
    # old local names are rebound to the factory outputs so downstream
    # references inside create_app() stay textually unchanged.
    _organization_id_for_context = organization_id_for_context
    _user_id_for_context = user_id_for_context
    _organization_id_for_request = make_organization_id_for_request(
        auth_enabled=auth_enabled,
        bootstrap_organization_id=bootstrap_organization_id,
    )
    _user_id_for_request = make_user_id_for_request(auth_enabled=auth_enabled)
    _required_author_organization_id = make_required_author_organization_id(
        bootstrap_organization_id=bootstrap_organization_id,
    )
    _knowledge_organization_id_for_request = make_knowledge_organization_id_for_request(
        auth_enabled=auth_enabled,
        bootstrap_organization_id=bootstrap_organization_id,
        knowledge_runtime=knowledge_runtime,
    )
    _kpi_organization_id_for_request = make_kpi_organization_id_for_request(
        auth_enabled=auth_enabled,
        bootstrap_organization_id=bootstrap_organization_id,
    )
    _intent_tags_organization_id_for_request = make_intent_tags_organization_id_for_request(
        auth_enabled=auth_enabled,
        bootstrap_organization_id=bootstrap_organization_id,
    )

    def _fallback_intent_catalog_from_step(step: Step) -> list[dict[str, object]]:
        # Source the analytics fallback catalog from the same per-step
        # outcome catalog the workflow classifier sees, so analytics
        # tagging stays aligned with workflow routing without re-deriving
        # vocabulary from the now-removed ``event_hints`` field.
        from ruhu.classifier.prompt import outcome_catalog_for_step

        catalog: list[dict[str, object]] = []
        for priority, (name, description) in enumerate(
            outcome_catalog_for_step(step).items(), start=1
        ):
            normalized_name = str(name or "").strip()
            normalized_description = str(description or "").strip()
            if not normalized_name:
                continue
            catalog.append(
                {
                    "id": f"step:{step.id}:{normalized_name}",
                    "name": normalized_name,
                    "display_name": normalized_name.replace("_", " ").title(),
                    "description": normalized_description,
                    "category": "step_hint",
                    "confidence_threshold": 0.5,
                    "priority": max(1, 100 - priority),
                    "example_phrases": [],
                }
            )
        return catalog

    def _effective_preclassification_profile(
        *,
        conversation: ConversationState,
        agent_document: AgentDocument,
        step: Step,
        agent_id: str,
        organization_id: str,
    ):
        settings = _agent_settings(agent_id, organization_id=organization_id)
        # Preclass only runs for the dedicated prefill backend. ``main_llm``
        # is handled inline by the kernel's ``StrategyAwareInterpreter``;
        # ``off`` skips classification entirely. Returning None here lets the
        # ``IntentTagsRuntimeIntegrator`` invoke the hosted intent_tags
        # classifier itself in its post-turn projection (which is also where
        # provider-cost records get persisted).
        if settings.llm_config.classifier.strategy != "prefill":
            return None

        resolved_profile = intent_tags_integrator.runtime.profile_service.resolve_profile(
            organization_id,
            agent_id=conversation.agent_id,
        )
        if not resolved_profile.effective_intent_catalog:
            resolved_profile = resolved_profile.model_copy(
                update={
                    "effective_intent_catalog": _fallback_intent_catalog_from_step(step),
                }
            )

        adapter_name = (resolved_profile.adapter_name or "").strip()
        if adapter_name in {"", "ruhu-general", "kernel-semantics"}:
            adapter_name = (
                "hosted"
                if intent_tags_integrator.classifier_registry.hosted_classifier is not None
                else "gemma_local"
            )
            resolved_profile = resolved_profile.model_copy(update={"adapter_name": adapter_name})

        return resolved_profile

    # Turn-processing service (RP-3.1 step 11) — every kernel call site now
    # flows through ConversationTurnService; the old local names are rebound
    # to the service's bound methods so downstream references (routes,
    # ingress flows, the tool-integration worker hook) stay textually
    # unchanged. ``_agent_settings`` is bound here, ahead of the agent
    # presentation block, because the service threads the SAME resolver.
    _agent_settings = make_agent_settings(agent_registry=agent_registry)
    # H6 (resolved in RP-3.1 step 13): the single ``_widget_config``
    # implementation lives in ruhu.services.widget_config; the turn service
    # wraps it for ``company_name_lookup`` and the public-widget config
    # router takes the same callable. Imported at the construction site
    # because the service module imports the WidgetConfigResponse DTO from
    # this module (the routes-module convention).
    from .services.widget_config import make_widget_config

    _widget_config = make_widget_config(runtime_session_factory=runtime_session_factory)
    turn_service = ConversationTurnService(
        kernel=kernel,
        agent_registry=agent_registry,
        intent_tags_integrator=intent_tags_integrator,
        agent_settings_resolver=_agent_settings,
        # The service preserves the swallow-to-None semantics around this
        # call exactly.
        company_name_lookup=lambda agent_id: _widget_config(agent_id).company_name,
        preclassification_profile_resolver=_effective_preclassification_profile,
    )
    _process_turn_with_intent_tags = turn_service.process_turn
    _confirm_tool_invocation_with_intent_tags = turn_service.confirm_tool_invocation
    _cancel_tool_invocation_with_intent_tags = turn_service.cancel_tool_invocation
    _reconcile_tool_invocation_result_with_intent_tags = (
        turn_service.reconcile_tool_invocation_result
    )
    _project_tool_invocation_progress_with_intent_tags = (
        turn_service.project_tool_invocation_progress
    )

    def _handle_tool_integration_job_transition(job) -> None:
        if kernel.tool_runtime is None:
            return
        invocation = kernel.tool_runtime.store.load(job.invocation_id)
        if invocation is None or not invocation.caller.conversation_id:
            return
        conversation = kernel.load_conversation(invocation.caller.conversation_id)
        if conversation is None:
            return
        try:
            snapshot = agent_registry.get_version_snapshot(
                conversation.agent_version_id,
                organization_id=conversation.organization_id,
            )
        except KeyError:
            return
        try:
            if job.status in {"completed", "failed", "cancelled", "dead_lettered"}:
                _reconcile_tool_invocation_result_with_intent_tags(
                    invocation.caller.conversation_id,
                    job.invocation_id,
                )
                return
            _project_tool_invocation_progress_with_intent_tags(
                invocation.caller.conversation_id,
                job.invocation_id,
            )
        except Exception:
            logger.exception(
                "tool integration job projection failed",
                extra={
                    "job_id": job.job_id,
                    "invocation_id": job.invocation_id,
                    "conversation_id": invocation.caller.conversation_id,
                    "job_status": job.status,
                },
            )

    if managed_tool_integration_worker is not None:
        managed_tool_integration_worker.on_job_transition = _handle_tool_integration_job_transition

    _tool_integration_organization_id_for_request = make_tool_integration_organization_id_for_request(
        auth_enabled=auth_enabled,
        bootstrap_organization_id=bootstrap_organization_id,
    )

    def _provider_for_tool_integration_job(job: ToolIntegrationJob) -> str:
        provider = job.metadata.get("provider")
        if isinstance(provider, str) and provider.strip():
            return provider.strip()
        spec_payload = dict(job.payload.get("tool_spec") or {})
        executor_config = dict(spec_payload.get("executor_config") or {})
        candidate = executor_config.get("provider")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        return job.executor_kind

    def _conversation_id_for_tool_integration_job(job: ToolIntegrationJob) -> str | None:
        payload_call = dict(job.payload.get("tool_call") or {})
        caller = dict(payload_call.get("caller") or {})
        conversation_id = caller.get("conversation_id")
        if isinstance(conversation_id, str) and conversation_id.strip():
            return conversation_id.strip()
        return None

    def _tool_integration_job_summary(job: ToolIntegrationJob) -> ToolIntegrationJobSummaryResponse:
        return ToolIntegrationJobSummaryResponse(
            job_id=job.job_id,
            invocation_id=job.invocation_id,
            conversation_id=_conversation_id_for_tool_integration_job(job),
            organization_id=job.organization_id,
            provider=_provider_for_tool_integration_job(job),
            tool_ref=job.tool_ref,
            executor_kind=job.executor_kind,
            resolution_mode=job.resolution_mode,
            status=job.status,
            queue_name=job.queue_name,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            external_job_id=job.external_job_id,
            callback_correlation_id=job.callback_correlation_id,
            submitted_at=job.submitted_at,
            last_progress_at=job.last_progress_at,
            next_poll_at=job.next_poll_at,
            next_retry_at=job.next_retry_at,
            finished_at=job.finished_at,
            error=job.error,
            metadata=dict(job.metadata),
        )

    def _tool_integration_job_detail(job: ToolIntegrationJob) -> ToolIntegrationJobDetailResponse:
        payload_call = dict(job.payload.get("tool_call") or {})
        summary = _tool_integration_job_summary(job)
        return ToolIntegrationJobDetailResponse(
            **summary.model_dump(mode="json"),
            args=dict(payload_call.get("args") or {}),
            result=None if job.result is None else dict(job.result),
        )

    def _tool_integration_counts_by_provider_status(
        jobs: list[ToolIntegrationJob],
    ) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = {}
        for job in jobs:
            provider = _provider_for_tool_integration_job(job)
            provider_counts = counts.setdefault(provider, {})
            provider_counts[job.status] = provider_counts.get(job.status, 0) + 1
        return counts


    # Agent presentation helpers (RP-3.1 step 10) — moved to
    # ruhu.services.agent_presentation; the old local name is rebound so
    # downstream references inside create_app() stay textually unchanged.
    _agent_summary = make_agent_summary(agent_registry=agent_registry)

    def _available_tool_refs(*, organization_id: str | None = None) -> list[str]:
        if kernel.tool_runtime is None:
            return []
        return [
            spec.ref
            for spec in kernel.tool_runtime.list_for_agent(
                organization_id=organization_id,
            )
        ]

    def _pending_tool_invocations(
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[ToolInvocation]:
        if kernel.tool_runtime is None:
            return []
        return [
            invocation
            for invocation in kernel.tool_runtime.list_conversation_invocations(
                conversation_id,
                organization_id=organization_id,
            )
            if invocation.status == "waiting_confirmation"
        ]

    def _emit_semantic_audit_event(
        *,
        request: Request,
        event_type: str,
        organization_id: str,
        actor_id: str | None,
        actor_session_id: str | None,
        resource_type: str,
        resource_id: str | None,
        detail: dict[str, object] | None = None,
    ) -> None:
        router = getattr(app.state, "audit_router", None)
        if router is None:
            return
        audit_context = build_session_audit_context(request)
        emit_audit_event(
            router,
            event_type=event_type,
            organization_id=organization_id,
            actor_id=actor_id,
            actor_ip=audit_context.ip,
            actor_session_id=actor_session_id,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail,
        )

    def _assistant_history(conversation_id: str, *, organization_id: str | None = None) -> list[RenderedMessage]:
        history: list[RenderedMessage] = []
        for trace in kernel.trace_store.by_conversation(conversation_id, organization_id=organization_id):
            history.extend(trace.emitted_messages)
        return history

    def _widget_messages_from_rendered(
        messages: list[RenderedMessage],
    ) -> list[WidgetTranscriptMessage]:
        return [
            WidgetTranscriptMessage(
                role=message.role,
                text=message.text,
                message_type=message.message_type,
                payload=dict(message.payload),
            )
            for message in messages
        ]

    def _widget_transcript_history(
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[WidgetTranscriptMessage]:
        history: list[WidgetTranscriptMessage] = []
        for trace in kernel.trace_store.by_conversation(conversation_id, organization_id=organization_id):
            observation = trace.normalized_observation
            if observation is not None and observation.event_type in {"user_message", "user_final_transcript"}:
                text = observation.redacted_text or ""
                attachments = _attachment_refs_for_history(
                    observation.attachment_ids or [],
                    organization_id=organization_id,
                )
                if text.strip() or attachments:
                    history.append(
                        WidgetTranscriptMessage(
                            role="user",
                            text=text,
                            attachments=attachments,
                        )
                    )
            for message in trace.emitted_messages:
                history.extend(_widget_messages_from_rendered([message]))
        return history

    def _attachment_refs_for_history(
        attachment_ids: list[str],
        *,
        organization_id: str | None,
    ) -> list[AttachmentRef]:
        if attachment_runtime is None or not attachment_ids:
            return []
        refs: list[AttachmentRef] = []
        seen: set[str] = set()
        for attachment_id in attachment_ids:
            normalized = str(attachment_id or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            try:
                ref = attachment_runtime.service.materialize_ref(
                    attachment_id=normalized,
                    organization_id=organization_id,
                )
            except Exception:
                ref = None
            if ref is not None:
                refs.append(ref)
        return refs

    def _build_widget_embed_code(
        publishable_key_prefix: str,
        agent_id: str,
        *,
        widget_script_url: str = "/widget/widget.js",
    ) -> str:
        return (
            "<!-- Ruhu Widget -->\n"
            "<script\n"
            f'  src="{widget_script_url}"\n'
            f'  data-agent-id="{agent_id}"\n'
            f'  data-publishable-key="{publishable_key_prefix}..."\n'
            "></script>"
        )

    def _is_livekit_voice_session(
        session: RealtimeSession,
        *,
        conversation_id: str,
        channel: Channel,
    ) -> bool:
        return (
            session.conversation_id == conversation_id
            and session.provider == "livekit"
            and session.channel == channel
            and session.modality == "audio"
            and session.surface == "voice"
        )

    def _list_livekit_voice_sessions(
        conversation_id: str,
        *,
        channel: Channel,
        active_only: bool | None = True,
    ) -> list[RealtimeSession]:
        if realtime_control_plane is None:
            return []
        sessions = realtime_control_plane.sessions.list_by_conversation(conversation_id)
        candidates = [
            session
            for session in sessions
            if _is_livekit_voice_session(
                session,
                conversation_id=conversation_id,
                channel=channel,
            )
            and (
                active_only is None
                or (session.status == "active" if active_only else session.status != "active")
            )
        ]
        candidates.sort(
            key=lambda session: (
                session.last_seen_at or session.updated_at,
                session.created_at,
            ),
            reverse=True,
        )
        return candidates

    def _latest_livekit_voice_session(
        conversation_id: str,
        *,
        channel: Channel,
        active_only: bool = True,
    ) -> RealtimeSession | None:
        candidates = _list_livekit_voice_sessions(
            conversation_id,
            channel=channel,
            active_only=active_only,
        )
        return None if not candidates else candidates[0]

    def _load_livekit_voice_session(
        conversation_id: str,
        *,
        channel: Channel,
        realtime_session_id: str | None,
    ) -> RealtimeSession | None:
        if realtime_control_plane is None or realtime_session_id is None or not realtime_session_id.strip():
            return None
        session = realtime_control_plane.sessions.load(realtime_session_id.strip())
        if session is None:
            return None
        if not _is_livekit_voice_session(session, conversation_id=conversation_id, channel=channel):
            return None
        return session

    def _voice_transport_metadata(
        *,
        base_session: RealtimeSession | None,
        request_metadata: dict[str, object],
    ) -> dict[str, object]:
        merged: dict[str, object] = {}
        if base_session is not None:
            merged.update(
                {
                    key: value
                    for key, value in dict(base_session.transport_metadata).items()
                    if key not in {"room_name", "dispatch", "provider_session_id"}
                }
            )
        merged.update(dict(request_metadata))
        return merged

    def _disconnect_superseded_widget_voice_sessions(
        *,
        conversation_id: str,
        keep_session_id: str | None,
        replacement_session_id: str | None,
        requested_session_id: str | None,
        replacement_reason: str,
    ) -> list[RealtimeSession]:
        if realtime_control_plane is None:
            return []
        disconnected: list[RealtimeSession] = []
        for candidate in _list_livekit_voice_sessions(
            conversation_id,
            channel="web_widget",
            active_only=True,
        ):
            if keep_session_id is not None and candidate.realtime_session_id == keep_session_id:
                continue
            updated = realtime_control_plane.disconnect_session(
                candidate.realtime_session_id,
                reason="voice_session_replaced",
                metadata={
                    "replacement_realtime_session_id": replacement_session_id,
                    "requested_realtime_session_id": requested_session_id,
                    "replacement_reason": replacement_reason,
                },
            )
            if updated is None:
                continue
            realtime_control_plane.record_voice_lifecycle_event(
                updated.realtime_session_id,
                name="interrupted",
                payload={
                    "reason": "voice_session_replaced",
                    "replacement_realtime_session_id": replacement_session_id,
                    "requested_realtime_session_id": requested_session_id,
                    "replacement_reason": replacement_reason,
                },
            )
            realtime_control_plane.record_voice_lifecycle_event(
                updated.realtime_session_id,
                name="session_replaced",
                payload={
                    "reason": "voice_session_replaced",
                    "replacement_realtime_session_id": replacement_session_id,
                    "requested_realtime_session_id": requested_session_id,
                    "replacement_reason": replacement_reason,
                },
            )
            disconnected.append(updated)
        return disconnected



    def _channel_conversation_id(channel: Channel, external_session_id: str) -> str:
        normalized = external_session_id.strip()
        if not normalized:
            raise HTTPException(status_code=400, detail="external_session_id is required")
        return f"{channel}:{normalized}"

    def _assistant_texts(messages: list[RenderedMessage]) -> list[str]:
        return assistant_texts(messages)

    def _require_provider_secret(provided_secret: str | None) -> None:
        expected_secret = effective_runtime_settings.provider_shared_secret
        if expected_secret is None or not expected_secret.strip():
            raise HTTPException(status_code=503, detail="provider bridge is not configured")
        if not provider_secret_is_valid(expected_secret, provided_secret):
            raise HTTPException(status_code=403, detail="invalid provider secret")

    def _configured_internal_api_secret() -> str | None:
        candidate = effective_runtime_settings.internal_api_secret
        if candidate is None or not candidate.strip():
            return None
        return candidate.strip()

    def _require_internal_api_access(request: Request) -> None:
        context = get_request_auth_context(request)
        principal = context.principal
        if principal is not None and principal.user.is_superuser:
            return
        expected_secret = _configured_internal_api_secret()
        if expected_secret is None:
            raise HTTPException(status_code=503, detail="internal API is not configured")
        provided_secret = request.headers.get("X-Ruhu-Internal-Secret")
        if not provider_secret_is_valid(expected_secret, provided_secret):
            raise HTTPException(status_code=403, detail="invalid internal API secret")

    # Widget session-token verification (RP-3.1 step 13) — the access check
    # moved to ruhu.services.widget_sessions; the public-widget,
    # test-session, and widget-projection surfaces all share this instance.
    widget_session_access = WidgetSessionAccessService(
        auth_session_factory=auth_session_factory,
    )

    def _resolve_live_agent_snapshot(
        agent_id: str,
        *,
        organization_id: str | None = None,
    ) -> AgentVersionSnapshot:
        try:
            version_id = agent_registry.resolve_version_id(
                agent_id,
                target="published",
                organization_id=organization_id,
            )
            return agent_registry.get_version_snapshot(version_id, organization_id=organization_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _realtime_surface_for_channel(channel: Channel) -> str:
        if channel == "phone":
            return "voice"
        if channel == "whatsapp":
            return "external_channel"
        if channel == "web_widget":
            return "public_widget"
        if channel == "browser":
            return "browser_projection"
        return "internal_chat"

    def _resolve_ingress_idempotency_key(
        explicit_key: str | None,
        metadata: dict[str, object],
    ) -> str | None:
        if explicit_key and explicit_key.strip():
            return explicit_key.strip()
        for candidate_key in ("idempotency_key", "message_id", "event_id", "transcript_id", "segment_id", "chunk_id"):
            candidate = metadata.get(candidate_key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None

    def _resolve_provider_phone_route(
        payload: ProviderPhoneCallStartRequest,
    ) -> PhoneNumberRouteConfig | None:
        called_number = extract_phone_number_from_metadata(payload.metadata)
        if called_number is None:
            return None
        try:
            if phone_number_registry is not None:
                resolved = phone_number_registry.resolve_route(
                    phone_number=called_number,
                    channel="phone",
                    provider=payload.provider,
                )
                if resolved is not None:
                    return resolved
            return resolve_phone_number_route(
                phone_number_routes,
                phone_number=called_number,
                channel="phone",
                provider=payload.provider,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    def _load_livekit_phone_session(external_session_id: str) -> RealtimeSession | None:
        if realtime_control_plane is None:
            return None
        conversation_id = _channel_conversation_id("phone", external_session_id)
        return realtime_control_plane.sessions.load_by_external_key(
            conversation_id=conversation_id,
            provider="livekit",
            external_session_key=external_session_id,
        )

    def _build_provider_phone_metadata(
        *,
        incoming_metadata: dict[str, object] | None,
        telephony_provider: str | None = None,
        resolved_route: PhoneNumberRouteConfig | None = None,
        session: RealtimeSession | None = None,
    ) -> dict[str, object]:
        metadata: dict[str, object] = {}
        if session is not None:
            metadata.update(dict(session.transport_metadata))
        metadata.update(dict(incoming_metadata or {}))
        metadata["provider"] = "livekit"
        metadata["transport_provider"] = "livekit"

        for candidate in (
            telephony_provider,
            metadata.get("telephony_provider"),
            None if resolved_route is None else resolved_route.provider,
        ):
            if isinstance(candidate, str) and candidate.strip():
                metadata["telephony_provider"] = candidate.strip()
                break

        if resolved_route is not None:
            for key, value in resolved_route.metadata.items():
                metadata.setdefault(str(key), value)
            metadata.setdefault("phone_number_route_key", resolved_route.route_key)
            metadata.setdefault("resolved_phone_number", resolved_route.phone_number)
            if resolved_route.provider_resource_id:
                metadata.setdefault("provider_resource_id", resolved_route.provider_resource_id)
            if resolved_route.display_name:
                metadata.setdefault("phone_number_display_name", resolved_route.display_name)
            if resolved_route.country_code:
                metadata.setdefault("phone_country_code", resolved_route.country_code)

        resolved_number = extract_phone_number_from_metadata(metadata)
        if resolved_number is not None:
            metadata.setdefault("resolved_phone_number", resolved_number)
        return metadata

    def _build_session_lifecycle_response(session: RealtimeSession) -> ProviderSessionLifecycleResponse:
        return ProviderSessionLifecycleResponse(
            conversation_id=session.conversation_id,
            realtime_session_id=session.realtime_session_id,
            channel=session.channel,
            provider=session.provider,
            status=session.status,
            ended_at=session.ended_at,
            updated_at=session.updated_at,
        )

    def _transition_realtime_session_by_id(
        *,
        realtime_session_id: str,
        target: Literal["disconnected", "ended", "errored"],
        reason: str | None,
        metadata: dict[str, object],
    ) -> ProviderSessionLifecycleResponse:
        if realtime_control_plane is None:
            raise HTTPException(status_code=503, detail="realtime control plane is not configured")
        session = realtime_control_plane.sessions.load(realtime_session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="unknown provider session")
        if target == "disconnected":
            updated = realtime_control_plane.disconnect_session(
                realtime_session_id,
                reason=reason,
                metadata=metadata,
            )
        elif target == "errored":
            updated = realtime_control_plane.error_session(
                realtime_session_id,
                reason=reason,
                metadata=metadata,
            )
        else:
            updated = realtime_control_plane.end_session(
                realtime_session_id,
                reason=reason,
                metadata=metadata,
            )
        if updated is None:
            raise HTTPException(status_code=404, detail="unknown provider session")
        _record_provider_costs(
            conversation_id=updated.conversation_id,
            organization_id=updated.organization_id,
            realtime_session_id=updated.realtime_session_id,
            provider=updated.provider,
            payload=metadata,
            default_cost_type=f"provider_session_{target}",
        )
        return _build_session_lifecycle_response(updated)

    def _ensure_realtime_session(
        *,
        conversation_id: str,
        organization_id: str | None,
        channel: Channel,
        external_session_id: str,
        provider: str | None,
        provider_session_id: str | None,
        participant_identity: str | None,
        metadata: dict[str, object],
        allow_new_on_inactive: bool = False,
    ) -> RealtimeSession | None:
        if realtime_control_plane is None:
            return None
        resolved_provider = (provider or "internal_http").strip() or "internal_http"
        session = realtime_control_plane.sessions.load_by_external_key(
            conversation_id=conversation_id,
            provider=resolved_provider,
            external_session_key=external_session_id,
        )
        if session is None or (allow_new_on_inactive and session.status != "active"):
            parent_realtime_session_id = None if session is None else session.realtime_session_id
            return realtime_control_plane.create_session(
                conversation_id=conversation_id,
                organization_id=organization_id,
                surface=_realtime_surface_for_channel(channel),
                channel=channel,
                modality="audio" if channel == "phone" else "text",
                provider=resolved_provider,
                external_session_key=external_session_id,
                provider_session_id=provider_session_id,
                participant_identity=participant_identity,
                transport_metadata=metadata,
                parent_realtime_session_id=parent_realtime_session_id,
            )
        return realtime_control_plane.touch_session(
            session.realtime_session_id,
            provider_session_id=provider_session_id,
            participant_identity=participant_identity,
            metadata=metadata,
        )


    def _transition_provider_session(
        *,
        channel: Channel,
        external_session_id: str,
        provider: str,
        target: Literal["disconnected", "ended", "errored"],
        reason: str | None,
        metadata: dict[str, object],
    ) -> ProviderSessionLifecycleResponse:
        if realtime_control_plane is None:
            raise HTTPException(status_code=503, detail="realtime control plane is not configured")
        conversation_id = _channel_conversation_id(channel, external_session_id)
        session = realtime_control_plane.sessions.load_by_external_key(
            conversation_id=conversation_id,
            provider=provider,
            external_session_key=external_session_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="unknown provider session")
        if target == "disconnected":
            updated = realtime_control_plane.disconnect_session(
                session.realtime_session_id,
                reason=reason,
                metadata=metadata,
            )
        elif target == "errored":
            updated = realtime_control_plane.error_session(
                session.realtime_session_id,
                reason=reason,
                metadata=metadata,
            )
        else:
            updated = realtime_control_plane.end_session(
                session.realtime_session_id,
                reason=reason,
                metadata=metadata,
            )
        if updated is None:
            raise HTTPException(status_code=404, detail="unknown provider session")
        _record_provider_costs(
            conversation_id=updated.conversation_id,
            organization_id=updated.organization_id,
            realtime_session_id=updated.realtime_session_id,
            provider=updated.provider,
            payload=metadata,
            default_cost_type=f"provider_session_{target}",
        )
        return _build_session_lifecycle_response(updated)

    def _record_inbound_observation(
        *,
        conversation_id: str,
        organization_id: str | None,
        realtime_session_id: str | None,
        channel: Channel,
        modality: Modality,
        text: str | None,
        metadata: dict[str, object],
        idempotency_key: str | None,
    ) -> None:
        if realtime_control_plane is None:
            return
        payload: dict[str, object] = {
            "channel": channel,
            "modality": modality,
            "provider": metadata.get("provider"),
            "transport_provider": metadata.get("transport_provider"),
            "telephony_provider": metadata.get("telephony_provider"),
            "metadata": dict(metadata),
            "idempotency_key": idempotency_key,
        }
        if text is not None:
            payload["text"] = text
        realtime_control_plane.events.append(
            conversation_id=conversation_id,
            organization_id=organization_id,
            realtime_session_id=realtime_session_id,
            family="message",
            name="inbound_observed",
            payload=payload,
            actor_type="user",
            visibility="internal",
            outbox_topic="conversation_projection",
        )





    def _record_provider_costs(
        *,
        conversation_id: str | None,
        organization_id: str | None,
        realtime_session_id: str | None,
        provider: str | None,
        payload: dict[str, object] | None,
        default_cost_type: str,
        turn_trace_id: str | None = None,
        tool_invocation_id: str | None = None,
    ) -> list[ProviderCostRecord]:
        if provider_cost_store is None or provider is None:
            return []
        records = build_provider_cost_records(
            provider=provider,
            payload=payload,
            organization_id=organization_id,
            conversation_id=conversation_id,
            realtime_session_id=realtime_session_id,
            turn_trace_id=turn_trace_id,
            tool_invocation_id=tool_invocation_id,
            default_cost_type=default_cost_type,
        )
        if not records:
            return []
        provider_cost_store.save_all(records)
        if realtime_control_plane is not None:
            for record in records:
                realtime_control_plane.events.append(
                    conversation_id=record.conversation_id,
                    organization_id=record.organization_id,
                    realtime_session_id=record.realtime_session_id,
                    family="provider",
                    name="cost_recorded",
                    payload={
                        "provider": record.provider,
                        "cost_type": record.cost_type,
                        "amount_usd": record.amount_usd,
                        "reference_key": record.reference_key,
                        "turn_trace_id": record.turn_trace_id,
                        "tool_invocation_id": record.tool_invocation_id,
                    },
                    actor_type="system",
                    visibility="internal",
                    outbox_topic="conversation_projection",
                )
        return records

    def _ensure_live_channel_conversation(
        *,
        channel: Channel,
        external_session_id: str,
        agent_id: str,
        organization_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ConversationState:
        conversation_id = _channel_conversation_id(channel, external_session_id)
        existing = kernel.load_conversation(conversation_id)
        if existing is not None:
            return existing
        snapshot = _resolve_live_agent_snapshot(agent_id, organization_id=organization_id)
        conversation = kernel.initialize_conversation(
            conversation_id,
            agent_document=snapshot.agent_document,
            agent_id=snapshot.agent_id,
            agent_name=snapshot.name,
            agent_version_id=snapshot.version_id,
            mode="live",
            organization_id=organization_id,
            metadata=dict(metadata or {}),
        )
        conversation.channel = channel
        kernel.conversation_store.save(conversation)
        return conversation

    # Channel ingress service (RP-3.1 step 11) — session start + message
    # ingestion for live channels moved to ruhu.services.channel_ingress;
    # the channels (step 14) and providers-livekit (step 15) routers call
    # the service's bound methods directly. The snapshot/session/
    # observation/cost helpers above stay in api.py (other routes use them
    # too) and are threaded explicitly (blueprint closure-capture hazard).
    channel_ingress_service = ChannelIngressService(
        turns=turn_service,
        realtime_control_plane=realtime_control_plane,
        resolve_live_agent_snapshot=_resolve_live_agent_snapshot,
        ensure_realtime_session=_ensure_realtime_session,
        record_inbound_observation=_record_inbound_observation,
        record_provider_costs=_record_provider_costs,
        channel_conversation_id=_channel_conversation_id,
        pending_tool_invocations=_pending_tool_invocations,
        assistant_texts=_assistant_texts,
        assistant_history=_assistant_history,
        # ``_build_runtime_turn_from_metadata`` is defined further down in
        # create_app(); the lambda late-binds it (definition order is not
        # call order).
        build_runtime_turn=lambda **kwargs: _build_runtime_turn_from_metadata(**kwargs),
        resolve_ingress_idempotency_key=_resolve_ingress_idempotency_key,
    )
    livekit_phone_adapter = LiveKitPhoneAdapter(
        config=livekit_phone_adapter_config,
        require_provider_secret=_require_provider_secret,
        start_live_channel_session=channel_ingress_service.start_channel_session,
        process_live_channel_message=channel_ingress_service.process_live_channel_message,
        transition_provider_session=_transition_provider_session,
        assistant_texts=_assistant_texts,
        token_issuer=livekit_token_issuer,
    )
    app.state.livekit_phone_adapter = livekit_phone_adapter

    def _resolve_agent_snapshot(
        request: Request,
        agent_id: str,
        *,
        target: AgentVersionStatus,
        agent_version_id: str | None = None,
    ) -> tuple[AgentVersionSnapshot, str | None]:
        scoped_organization_id = _organization_id_for_request(request)
        try:
            if agent_version_id is not None:
                snapshot = agent_registry.get_version_snapshot(
                    agent_version_id,
                    organization_id=scoped_organization_id,
                )
                if snapshot.agent_id != agent_id:
                    raise HTTPException(status_code=409, detail="agent_version_id belongs to a different agent")
            else:
                version_id = agent_registry.resolve_version_id(
                    agent_id,
                    target=target,
                    organization_id=scoped_organization_id,
                )
                snapshot = agent_registry.get_version_snapshot(
                    version_id,
                    organization_id=scoped_organization_id,
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return snapshot, scoped_organization_id or snapshot.organization_id

    # Agent presentation resolvers (RP-3.1 step 10) — moved to
    # ruhu.services.agent_presentation; the old local names are rebound so
    # downstream references (router mounts, ingress closures) stay
    # textually unchanged.
    _version_summary_by_id = make_version_summary_by_id(agent_registry=agent_registry)

    def _fixture_validation_payload(fixture: SimulationFixture, snapshot: AgentVersionSnapshot) -> list[dict[str, object | None]]:
        return [issue.model_dump(mode="json") for issue in validate_fixture(snapshot, fixture)]

    _agent_evaluation_policy = make_agent_evaluation_policy(agent_registry=agent_registry)
    # ``_agent_settings`` is bound earlier (turn-service construction, step 11).
    _resolved_agent_settings = make_resolved_agent_settings(agent_settings=_agent_settings)
    _validate_classifier_strategy = make_validate_classifier_strategy(
        runtime_session_factory=runtime_session_factory,
    )

    # Template-provenance publish-review helpers: built via factories from
    # ruhu.services.agent_presentation (RP-3.1 step 3); the old local names
    # are rebound so downstream references stay textually unchanged.
    _resolve_optional_tool_refs = make_resolve_optional_tool_refs(
        agent_registry=agent_registry,
        template_store=template_store,
    )
    _resolve_missing_tool_remediation = make_resolve_missing_tool_remediation(
        agent_registry=agent_registry,
        template_store=template_store,
        resolve_setup_url=_resolve_setup_url,
    )

    _build_agent_publish_review = make_build_agent_publish_review(
        agent_registry=agent_registry,
        version_summary_by_id=_version_summary_by_id,
        available_tool_refs=_available_tool_refs,
        resolve_optional_tool_refs=_resolve_optional_tool_refs,
        resolve_missing_tool_remediation=_resolve_missing_tool_remediation,
        simulation_fixture_store=effective_simulation_fixture_store,
        evaluation_service=effective_evaluation_service,
        agent_evaluation_policy=_agent_evaluation_policy,
        readiness_store=_atlas_readiness_store,
    )

    # Health + console routers (RP-3.1 step 2) — mounted at the exact position
    # the inline blocks occupied (hazard H2: registration order is contract).
    app.include_router(
        build_health_router(
            runtime_session_factory=runtime_session_factory,
            settings=effective_runtime_settings,
            jwt_codec_provider=lambda: (
                effective_auth_service.jwt_codec
                if effective_auth_service is not None
                else None
            ),
        )
    )
    app.include_router(build_console_pages_router(auth_enabled=auth_enabled))

    # Public widget-config router (RP-3.1 step 13, blueprint group 17) —
    # mounted at the exact position the inline route occupied (hazard H2).
    # The session-surface widget router mounts further down, where the
    # inline /public/widget/sessions block sat. The widget DTOs live in
    # this module, so import at the mount site rather than api.py's
    # module top.
    from .routes.public_widget import (
        build_public_widget_config_router,
        build_public_widget_router,
    )

    app.include_router(build_public_widget_config_router(widget_config=_widget_config))

    if auth_resolver is not None and effective_auth_service is not None:
        # Auth pages + session/OAuth/invitation routes (RP-3.1 step 7). The
        # auth DTOs live in this module, so import at the mount site rather
        # than api.py's module top.
        from .routes.auth_sessions import build_auth_sessions_router

        app.include_router(
            build_auth_sessions_router(
                auth_service=effective_auth_service,
                identity_store=effective_identity_store,
                settings=effective_runtime_settings,
                email_sender=effective_email_sender,
                notification_store=effective_notification_store,
                emit_semantic_audit_event=_emit_semantic_audit_event,
            )
        )

    # GET /auth/me registers unconditionally (auth-disabled apps 401 via the
    # require_authenticated_context dependency), exactly like the original
    # inline route between the two auth guards.
    from .routes.auth_sessions import build_auth_me_router

    app.include_router(build_auth_me_router())

    if auth_enabled and effective_auth_service is not None and effective_identity_store is not None:
        from .routes.auth_sessions import build_auth_profile_router
        from .routes.internal_admin import build_internal_admin_router

        app.include_router(
            build_auth_profile_router(
                auth_service=effective_auth_service,
                identity_store=effective_identity_store,
            )
        )
        app.include_router(
            build_internal_admin_router(
                auth_enabled=auth_enabled,
                auth_service=effective_auth_service,
                identity_store=effective_identity_store,
                settings=effective_runtime_settings,
                email_sender=effective_email_sender,
                intent_tags_runtime=intent_tags_runtime,
                provider_cost_store=provider_cost_store,
                jobs_store=effective_jobs_store,
            )
        )

    if auth_enabled and effective_tenant_identity_repositories is not None and effective_identity_store is not None:
        # Organization/SSO/members + API-keys routers (RP-3.1 step 8a) —
        # mounted at the exact positions the inline blocks occupied. The
        # tenant-scoped repo resolver and the deliver-or-raise email helper
        # (shared with the magic-link/invitation flows) are built inside the
        # builders from the threaded kwargs.
        from .routes.api_keys import build_api_keys_router
        from .routes.organization import build_organization_router

        app.include_router(
            build_organization_router(
                auth_service=effective_auth_service,
                identity_store=effective_identity_store,
                tenant_identity_repositories=effective_tenant_identity_repositories,
                settings=effective_runtime_settings,
                email_sender=effective_email_sender,
                notification_store=effective_notification_store,
                auth_session_factory=auth_session_factory,
                emit_semantic_audit_event=_emit_semantic_audit_event,
            )
        )
        app.include_router(
            build_api_keys_router(
                auth_session_factory=auth_session_factory,
                agent_registry=agent_registry,
            )
        )

        # ── Per-agent widget configuration ────────────────────────────────
        # Widget enable/disable + widget-config/embed-code routers (RP-3.1
        # step 8b) — mounted at the exact positions the two inline blocks
        # occupied (the persona router sits between them, as the inline code
        # did). Hazard H6 (resolved in step 13): the embed-code snippet
        # builder stays in create_app() and threads in as an explicit
        # kwarg; the public widget-config projection now lives in
        # ruhu.services.widget_config.
        from .routes.widget_admin import (
            build_widget_admin_router,
            build_widget_config_router,
        )

        app.include_router(
            build_widget_admin_router(
                agent_registry=agent_registry,
                runtime_session_factory=runtime_session_factory,
                auth_session_factory=auth_session_factory,
                settings=effective_runtime_settings,
                build_widget_embed_code=_build_widget_embed_code,
            )
        )

        # ── Voice library + cloning + persona avatars ──────────────────
        # Persona router (RP-3.1 step 8b) — voice catalog/preview, voice
        # clones, and the persona avatar pair, at the exact position the
        # inline block occupied.
        from .routes.persona import build_persona_router

        app.include_router(
            build_persona_router(
                agent_registry=agent_registry,
                runtime_session_factory=runtime_session_factory,
                provider_cost_store=provider_cost_store,
                emit_semantic_audit_event=_emit_semantic_audit_event,
            )
        )

        app.include_router(
            build_widget_config_router(
                agent_registry=agent_registry,
                runtime_session_factory=runtime_session_factory,
                auth_session_factory=auth_session_factory,
                build_widget_embed_code=_build_widget_embed_code,
            )
        )

        # ── Phone Number Registry ─────────────────────────────────────────
        # Phone-numbers + providers router (RP-3.1 step 6) — mounted at the
        # exact position the inline block occupied (hazard H2:
        # /phone-numbers/audit and /phone-numbers/reconcile register before
        # /phone-numbers/{phone_number_id}). The provider/http-client test
        # seams are resolved per-request from app.state via zero-arg
        # callables — that read stays composition-side, like the journey
        # tracker/runtime providers.
        from .routes.phone_numbers import build_phone_numbers_router

        app.include_router(
            build_phone_numbers_router(
                phone_number_registry=phone_number_registry,
                phone_number_audit_service=phone_number_audit_service,
                phone_number_operations_service=phone_number_operations_service,
                notification_store=effective_notification_store,
                settings=effective_runtime_settings,
                agent_registry=agent_registry,
                telnyx_provider_state=lambda: getattr(app.state, "telnyx_phone_provider", None),
                telnyx_http_client_state=lambda: getattr(app.state, "telnyx_http_client", None),
                at_provider_state=lambda: getattr(app.state, "at_phone_provider", None),
            )
        )

        # ── Account closure ────────────────────────────────────────────────
        # Account-closure router (RP-3.1 step 8a) — mounted after the
        # phone-numbers router, the exact position the inline block occupied.
        from .routes.organization import build_account_closure_router

        app.include_router(
            build_account_closure_router(
                auth_service=effective_auth_service,
                tenant_identity_repositories=effective_tenant_identity_repositories,
                settings=effective_runtime_settings,
                email_sender=effective_email_sender,
            )
        )

    # Voice-session lifecycle + LiveKit webhook router (RP-3.1 step 12,
    # blueprint group 12) — mounted at the exact position the inline block
    # occupied (hazard H2: /voice-sessions/health and
    # /voice-sessions/active/count register before
    # /voice-sessions/{session_id}). LiveKit runtime clients are resolved
    # per-request from app.state via zero-arg callables — that read stays
    # composition-side, like the phone-numbers provider seams. The pure
    # voice-policy/dispatch helpers live in the router module; the
    # public-widget voice routes import them directly (step 13).
    from .routes.voice_sessions import build_voice_sessions_router

    app.include_router(
        build_voice_sessions_router(
            kernel=kernel,
            agent_registry=agent_registry,
            realtime_control_plane=realtime_control_plane,
            auth_enabled=auth_enabled,
            bootstrap_organization_id=bootstrap_organization_id,
            resolve_live_agent_snapshot=_resolve_live_agent_snapshot,
            livekit_phone_adapter_config=livekit_phone_adapter_config,
            livekit_phone_adapter_config_state=lambda: getattr(app.state, "livekit_phone_adapter_config", None),
            livekit_room_runtime_client_state=lambda: getattr(app.state, "livekit_room_runtime_client", None),
            livekit_token_issuer_state=lambda: getattr(app.state, "livekit_token_issuer", None),
            livekit_dispatch_client_state=lambda: getattr(app.state, "livekit_dispatch_client", None),
        )
    )

    # Agents CRUD router (RP-3.1 step 10, blueprint group 13) — mounted at
    # the exact position the inline block occupied (hazard H2). The journeys
    # mount below keeps its original position between the CRUD block and the
    # authoring block.
    from .routes.agents import (
        build_agent_authoring_router,
        build_agents_reload_router,
        build_agents_router,
    )

    app.include_router(
        build_agents_router(
            agent_registry=agent_registry,
            auth_enabled=auth_enabled,
            bootstrap_organization_id=bootstrap_organization_id,
            resolve_agent_snapshot=_resolve_agent_snapshot,
            agent_summary=_agent_summary,
            validate_classifier_strategy=_validate_classifier_strategy,
        )
    )

    # Journeys router (RP-3.1 step 5) — mounted at the exact position the
    # inline block occupied (hazard H2: /journey-definitions/export and
    # /journey-definitions/import register before
    # /journey-definitions/{definition_id}). Tracker/runtime are resolved
    # per-request from app.state via providers — that read stays
    # composition-side, like _journey_review_agent_documents.
    app.include_router(
        build_journeys_router(
            journey_service=effective_journey_service,
            journey_instance_store=effective_journey_instance_store,
            journey_tracker_provider=lambda: getattr(app.state, "journey_tracker", None),
            journey_runtime_provider=lambda: getattr(app.state, "journey_runtime", None),
            kernel=kernel,
            realtime_control_plane=realtime_control_plane,
            auth_enabled=auth_enabled,
            bootstrap_organization_id=bootstrap_organization_id,
        )
    )

    # Agent authoring router (RP-3.1 step 10) — settings, evaluation policy,
    # agent document, versions, diff/publish-review/audit, draft/publish/
    # unpublish. Mounted at the exact position the inline block occupied
    # (hazard H2: GET/PUT /agents/{agent_id}/agent-document register before
    # PUT /agents/{agent_id}). test-session stays inline below — it is
    # group 18 (SYNC-KERNEL, blueprint step 13).
    app.include_router(
        build_agent_authoring_router(
            agent_registry=agent_registry,
            auth_enabled=auth_enabled,
            bootstrap_organization_id=bootstrap_organization_id,
            agent_evaluation_policy=_agent_evaluation_policy,
            agent_settings=_agent_settings,
            resolved_agent_settings=_resolved_agent_settings,
            validate_classifier_strategy=_validate_classifier_strategy,
            build_agent_publish_review=_build_agent_publish_review,
            version_summary_by_id=_version_summary_by_id,
            notification_store=effective_notification_store,
        )
    )

    # Canvas test-session + internal phone-route + conversations-lifecycle
    # routers (RP-3.1 step 13, blueprint group 18) — three builders because
    # the inline routes sat at three registration positions (hazard H2);
    # each mounts at the exact position its inline block occupied. Imported
    # at the mount site: the lifecycle DTOs live in this module.
    from .routes.conversations_lifecycle import (
        build_conversations_lifecycle_router,
        build_internal_phone_routes_router,
        build_test_session_router,
    )

    app.include_router(
        build_test_session_router(
            kernel=kernel,
            agent_registry=agent_registry,
            widget_session_access=widget_session_access,
            auth_enabled=auth_enabled,
            widget_transcript_history=_widget_transcript_history,
            widget_messages_from_rendered=_widget_messages_from_rendered,
            pending_tool_invocations=_pending_tool_invocations,
        )
    )

    # /agents:reload router (RP-3.1 step 10) — mounted at the exact position
    # the inline route occupied (hazard H2).
    app.include_router(
        build_agents_reload_router(
            agent_registry=agent_registry,
            agent_seed_root=agent_seed_root,
            require_internal_api_access=_require_internal_api_access,
        )
    )

    app.include_router(
        build_internal_phone_routes_router(
            require_internal_api_access=_require_internal_api_access,
            phone_number_registry=phone_number_registry,
            phone_number_routes=phone_number_routes,
        )
    )

    def _build_runtime_turn_from_metadata(
        *,
        turn_id: str,
        dedupe_key: str,
        channel: Any,
        modality: Any,
        event_type: Any,
        text: str | None,
        metadata: dict[str, Any] | None,
        attachments: list[AttachmentRef] | None = None,
    ) -> RuntimeTurn:
        """Build a RuntimeTurn and normalize adapter attachment metadata.

        Provider adapters may serialize ``attachment_refs`` in metadata before
        this helper creates the first-class ``RuntimeTurn.attachments`` list. An
        explicit ``attachments`` argument takes precedence.
        """
        extracted_refs, cleaned_metadata = _extract_attachments_from_metadata(metadata)
        final_attachments = list(attachments) if attachments else extracted_refs
        return RuntimeTurn(
            turn_id=turn_id,
            dedupe_key=dedupe_key,
            channel=channel,
            modality=modality,
            event_type=event_type,
            text=text,
            attachments=final_attachments,
            metadata=cleaned_metadata,
            received_at=datetime.now(timezone.utc),
        )

    def _extract_attachments_from_metadata(
        metadata: dict[str, Any] | None,
    ) -> tuple[list[AttachmentRef], dict[str, Any]]:
        """Pull serialized ``attachment_refs`` out of a metadata dict.

        Simulation replay and channel adapters serialize refs into the turn
        ``metadata`` field before this helper moves them onto
        ``RuntimeTurn.attachments`` and returns the cleaned metadata.

        ``attachment_ids`` are preserved in metadata as debug/trace hints
        per the spec.
        """
        if not metadata:
            return [], dict(metadata or {})
        raw_refs = metadata.get("attachment_refs")
        cleaned = {k: v for k, v in metadata.items() if k != "attachment_refs"}
        if not isinstance(raw_refs, list):
            return [], cleaned
        materialized: list[AttachmentRef] = []
        for item in raw_refs:
            if isinstance(item, AttachmentRef):
                materialized.append(item)
            elif isinstance(item, dict):
                try:
                    materialized.append(AttachmentRef.model_validate(item))
                except Exception:
                    # Malformed entry — drop rather than crash the turn.
                    # Kernel will never see this ref; callers must use the
                    # first-class field for reliable delivery.
                    continue
        return materialized, cleaned

    def _resolve_conversation_attachment_refs(
        *,
        conversation_id: str,
        organization_id: str | None,
        attachment_ids: list[str] | None,
    ) -> tuple[list[str], list[AttachmentRef]]:
        """Materialize AttachmentRef objects for a turn's attachment IDs.

        Returns (normalized_ids, refs).  Callers pass the typed ``refs`` list
        directly to ``RuntimeTurn.attachments`` (spec §3). IDs are returned
        separately for trace persistence (spec §12) and metadata logging.
        """
        normalized_ids: list[str] = []
        attachment_refs: list[AttachmentRef] = []
        if not attachment_ids:
            return normalized_ids, attachment_refs
        if attachment_runtime is None:
            raise HTTPException(status_code=503, detail="attachment runtime is not configured")
        seen_attachment_ids: set[str] = set()
        for attachment_id in attachment_ids:
            normalized_attachment_id = str(attachment_id or "").strip()
            if not normalized_attachment_id or normalized_attachment_id in seen_attachment_ids:
                continue
            projection = attachment_runtime.service.get_projection(
                attachment_id=normalized_attachment_id,
                organization_id=organization_id,
            )
            if projection is None or projection.attachment.conversation_id != conversation_id:
                raise HTTPException(status_code=404, detail=f"unknown attachment id: {normalized_attachment_id}")
            if (
                projection.attachment.scan_status != "passed"
                or projection.attachment.extraction_status == "pending"
            ):
                try:
                    projection = attachment_runtime.service.process_attachment(
                        attachment_id=normalized_attachment_id,
                        organization_id=organization_id,
                    )
                except Exception as exc:
                    raise HTTPException(
                        status_code=409,
                        detail=f"attachment is not ready: {normalized_attachment_id}",
                    ) from exc
            seen_attachment_ids.add(normalized_attachment_id)
            normalized_ids.append(normalized_attachment_id)
            attachment_ref = attachment_runtime.service.materialize_ref(
                attachment_id=normalized_attachment_id,
                organization_id=organization_id,
            )
            if attachment_ref is None:
                raise HTTPException(status_code=404, detail=f"unknown attachment id: {normalized_attachment_id}")
            attachment_refs.append(attachment_ref)
        return normalized_ids, attachment_refs

    # Public-widget sessions router (RP-3.1 step 13, blueprint group 17 —
    # the fattest group) — mounted at the exact position the inline block
    # occupied (hazard H2). SYNC-KERNEL: the message POST and SSE stream
    # routes call turn_service.process_turn / aprocess_turn directly — no
    # turn logic remains in any route. The attachment/transcript/
    # voice-session helpers above stay in api.py (shared with the
    # still-inline channel/provider groups, blueprint steps 14-16) and
    # thread in as explicit kwargs; LiveKit clients + the pg-notify
    # dispatcher resolve per-request from app.state via zero-arg callables.
    app.include_router(
        build_public_widget_router(
            kernel=kernel,
            agent_registry=agent_registry,
            realtime_control_plane=realtime_control_plane,
            turn_service=turn_service,
            widget_session_access=widget_session_access,
            auth_session_factory=auth_session_factory,
            auth_enabled=auth_enabled,
            widget_transcript_history=_widget_transcript_history,
            widget_messages_from_rendered=_widget_messages_from_rendered,
            pending_tool_invocations=_pending_tool_invocations,
            resolve_conversation_attachment_refs=_resolve_conversation_attachment_refs,
            load_livekit_voice_session=_load_livekit_voice_session,
            latest_livekit_voice_session=_latest_livekit_voice_session,
            disconnect_superseded_widget_voice_sessions=_disconnect_superseded_widget_voice_sessions,
            voice_transport_metadata=_voice_transport_metadata,
            build_session_lifecycle_response=_build_session_lifecycle_response,
            livekit_token_issuer_state=lambda: getattr(app.state, "livekit_token_issuer", None),
            livekit_dispatch_client_state=lambda: getattr(app.state, "livekit_dispatch_client", None),
            pg_notify_dispatcher_state=lambda: getattr(app.state, "pg_notify_dispatcher", None),
        )
    )

    # Widget-analytics summary (RP-3.1 step 9, blueprint group 16): the
    # async-converted router replaces the inline route at this exact
    # registration position. The ingest route moved into the
    # public-widget router (step 13).
    from .routes.widget_analytics import build_widget_analytics_router

    app.include_router(
        build_widget_analytics_router(
            agent_registry=agent_registry,
        )
    )

    # Conversations-lifecycle router (RP-3.1 step 13, blueprint group 18) —
    # mounted at the exact position the inline block occupied (hazard H2).
    # SYNC-KERNEL: start/simulate call kernel.start_conversation directly
    # and replay drives turns through turn_service.process_turn.
    app.include_router(
        build_conversations_lifecycle_router(
            kernel=kernel,
            turn_service=turn_service,
            auth_enabled=auth_enabled,
            resolve_agent_snapshot=_resolve_agent_snapshot,
            build_runtime_turn_from_metadata=_build_runtime_turn_from_metadata,
        )
    )

    # Simulation fixtures + evaluation runs routers (RP-3.1 step 4) — mounted
    # at the exact position the inline blocks occupied (hazard H2). Imported
    # at the mount site: the request DTOs still live in this module.
    from .routes.evaluation_runs import build_evaluation_runs_router
    from .routes.simulation_fixtures import build_simulation_fixtures_router

    app.include_router(
        build_simulation_fixtures_router(
            simulation_fixture_store=effective_simulation_fixture_store,
            resolve_agent_snapshot=_resolve_agent_snapshot,
            auth_enabled=auth_enabled,
            bootstrap_organization_id=bootstrap_organization_id,
        )
    )
    app.include_router(
        build_evaluation_runs_router(
            simulation_fixture_store=effective_simulation_fixture_store,
            evaluation_service=effective_evaluation_service,
            evaluation_runtime=effective_evaluation_runtime,
            evaluation_run_store=effective_evaluation_run_store,
            agent_registry=agent_registry,
            resolve_agent_snapshot=_resolve_agent_snapshot,
            agent_evaluation_policy=_agent_evaluation_policy,
            auth_enabled=auth_enabled,
            bootstrap_organization_id=bootstrap_organization_id,
        )
    )

    # Phase 5: conversations + dashboard routes are now in conversations_router.py
    # with org-level rate limiting applied at the router dependency level.
    # Phase C Batch 1 cleanup: DTOs now imported directly from api_models
    # inside the router, so we don't pass them as parameters anymore.
    if _atlas_store is not None:
        from .atlas_api import build_atlas_router as _build_atlas_router
        from .tools.management import AgentToolBindingStore as _AgentToolBindingStore
        from .tools.management import ToolDefinitionStore as _ToolDefinitionStore

        app.include_router(
            _build_atlas_router(
                agent_registry=agent_registry,
                atlas_store=_atlas_store,
                tool_runtime=kernel.tool_runtime,
                connection_store=connection_store,
                definition_store=(
                    None if runtime_session_factory is None else _ToolDefinitionStore(runtime_session_factory)
                ),
                binding_store=(
                    None if runtime_session_factory is None else _AgentToolBindingStore(runtime_session_factory)
                ),
                conversation_store=kernel.conversation_store,
                trace_store=kernel.trace_store,
                readiness_store=_atlas_readiness_store,
                readiness_artifact_store=_atlas_readiness_artifact_store,
                get_organization_id=_organization_id_for_request,
                user_id_for_context=_user_id_for_context,
                require_author_context=_require_runtime_author_context,
                required_author_organization_id=_required_author_organization_id,
                rate_limiter=_org_rate_limiter,
            )
        )

    app.include_router(
        build_conversations_router(
            conversation_store=kernel.conversation_store,
            trace_store=kernel.trace_store,
            agent_registry=agent_registry,
            agent_summary_fn=_agent_summary,
            get_organization_id=_organization_id_for_request,
            rate_limiter=_org_rate_limiter,
        )
    )

    from .routes.channels import build_channels_router

    app.include_router(
        build_channels_router(
            kernel=kernel,
            agent_registry=agent_registry,
            turn_service=turn_service,
            channel_ingress=channel_ingress_service,
            realtime_control_plane=realtime_control_plane,
            provider_cost_store=provider_cost_store,
            whatsapp_meta_channels=whatsapp_meta_channels,
            attachment_runtime=attachment_runtime,
            semantic_summary_webhook_dispatcher=semantic_summary_webhook_dispatcher,
            effective_runtime_settings=effective_runtime_settings,
            auth_enabled=auth_enabled,
            organization_id_for_request=_organization_id_for_request,
            require_provider_secret=_require_provider_secret,
            require_internal_api_access=_require_internal_api_access,
            configured_internal_api_secret=_configured_internal_api_secret,
            ensure_live_channel_conversation=_ensure_live_channel_conversation,
            ensure_realtime_session=_ensure_realtime_session,
            record_inbound_observation=_record_inbound_observation,
            record_provider_costs=_record_provider_costs,
            provider_http_client_state=lambda: getattr(app.state, "provider_http_client", None),
        )
    )








    @app.post("/providers/intent-tags/webhooks/dispatch", response_model=SemanticWebhookDispatchResponse)
    def dispatch_semantic_summary_webhooks_provider(
        organization_id: str | None = Query(default=None),
        conversation_id: str | None = Query(default=None),
        mode: str = Query(default="both", pattern="^(fanout|deliver|both)$"),
        limit: int = Query(default=100, ge=1, le=1000),
        x_ruhu_provider_secret: str | None = Header(default=None, alias="X-Ruhu-Provider-Secret"),
    ) -> SemanticWebhookDispatchResponse:
        _require_provider_secret(x_ruhu_provider_secret)
        if semantic_summary_webhook_dispatcher is None:
            raise HTTPException(status_code=503, detail="semantic summary webhook dispatcher is not configured")
        result = semantic_summary_webhook_dispatcher.run_pending(
            organization_id=organization_id,
            conversation_id=conversation_id,
            limit=limit,
            mode=mode,
        )
        return SemanticWebhookDispatchResponse(
            publication_attempted=result.publication_attempted,
            publication_fanned_out=result.publication_fanned_out,
            publication_skipped=result.publication_skipped,
            publication_failed=result.publication_failed,
            delivery_attempted=result.delivery_attempted,
            delivery_delivered=result.delivery_delivered,
            delivery_failed=result.delivery_failed,
            delivery_retried=result.delivery_retried,
            delivery_skipped=result.delivery_skipped,
        )


    # Providers-LiveKit router (RP-3.1 step 15, blueprint group 20 — the
    # LAST route extraction) — mounted at the exact position the inline
    # block occupied (hazard H2). SYNC-KERNEL: the voice transcript/message
    # ingestion routes call channel_ingress.process_session_message directly
    # (site 7). The voice-signal helpers moved into the router (nothing
    # else here used them); the phone-route/session/lifecycle/attachment
    # helpers stay in api.py (shared with the LiveKitPhoneAdapter
    # construction above) and thread in as explicit kwargs. Imported at the
    # mount site, not at module top: the provider DTOs still live in this
    # module, so a top-of-file import of routes.providers_livekit would be
    # circular.
    from .routes.providers_livekit import build_providers_livekit_router

    app.include_router(
        build_providers_livekit_router(
            kernel=kernel,
            realtime_control_plane=realtime_control_plane,
            channel_ingress=channel_ingress_service,
            livekit_phone_adapter=livekit_phone_adapter,
            livekit_phone_adapter_config=livekit_phone_adapter_config,
            require_provider_secret=_require_provider_secret,
            require_internal_api_access=_require_internal_api_access,
            resolve_provider_phone_route=_resolve_provider_phone_route,
            load_livekit_phone_session=_load_livekit_phone_session,
            build_provider_phone_metadata=_build_provider_phone_metadata,
            transition_realtime_session_by_id=_transition_realtime_session_by_id,
            build_session_lifecycle_response=_build_session_lifecycle_response,
            resolve_conversation_attachment_refs=_resolve_conversation_attachment_refs,
            resolve_ingress_idempotency_key=_resolve_ingress_idempotency_key,
        )
    )

    from .routes.conversation_runtime import build_conversation_runtime_router

    app.include_router(
        build_conversation_runtime_router(
            kernel=kernel,
            agent_registry=agent_registry,
            turn_service=turn_service,
            realtime_control_plane=realtime_control_plane,
            provider_cost_store=provider_cost_store,
            runtime_session_factory=runtime_session_factory,
            organization_id_for_request=_organization_id_for_request,
            tool_integration_organization_id_for_request=_tool_integration_organization_id_for_request,
            build_runtime_turn_from_metadata=_build_runtime_turn_from_metadata,
        )
    )













    # ── Agent Template endpoints ──────────────────────────────────────────────
    if template_store is not None:
        # Imported at the mount site, not at module top: the template DTOs
        # still live in this module (they migrate at blueprint step 10), so a
        # top-of-file import of routes.agent_templates would be circular.
        from .routes.agent_templates import build_agent_templates_router

        app.include_router(
            build_agent_templates_router(
                template_store=template_store,
                agent_registry=agent_registry,
                kernel=kernel,
                runtime_session_factory=runtime_session_factory,
                auth_enabled=auth_enabled,
                bootstrap_organization_id=bootstrap_organization_id,
            )
        )

    install_knowledge_router(
        app,
        runtime=knowledge_runtime,
        resolve_organization_id=_knowledge_organization_id_for_request,
        rate_limiter=_org_rate_limiter,
    )
    install_kpi_router(
        app,
        runtime=kpi_runtime,
        resolve_organization_id=_kpi_organization_id_for_request,
        rate_limiter=_org_rate_limiter,
    )
    # Continuous-evaluation read API — only mounted when live eval is
    # enabled. Without the runtime, the endpoints have nothing to serve;
    # a 404 from "route doesn't exist" is the right signal that the
    # operator hasn't opted in, not "the system is broken."
    if managed_live_eval_runtime is not None:
        from .live_eval_api import install_live_eval_router
        install_live_eval_router(
            app,
            runtime=managed_live_eval_runtime,
            rate_limiter=_org_rate_limiter,
        )
    install_intent_tags_router(
        app,
        runtime=intent_tags_runtime,
        resolve_organization_id=_intent_tags_organization_id_for_request,
        resolve_user_id=_user_id_for_request,
        require_read_access=_require_runtime_reviewer_context,
        require_write_access=_require_runtime_author_context,
        semantic_webhook_dispatcher=semantic_summary_webhook_dispatcher,
    )
    install_rules_router(
        app,
        runtime=rules_runtime,
        rate_limiter=_org_rate_limiter,
    )
    install_notifications_router(
        app,
        notification_store=effective_notification_store,
    )
    install_browser_task_router(
        app,
        browser_task_service=browser_task_service,
        attachment_runtime=attachment_runtime,
        jobs_store=effective_jobs_store,
        authorize_request=_require_internal_api_access,
    )
    install_ticketing_router(
        app,
        ticket_system_service=ticket_system_service,
        auth_enabled=auth_enabled,
    )

    # Schema API routes (KPI, Intent Tags, Attachments) with event sourcing.
    # Keep this optional: some deployments/tests do not install the schema
    # event-sourcing extras (for example ``sqlmodel``), and those should not
    # prevent the core runtime/API from booting.
    try:
        from ruhu.event_sourcing.event_bus import get_event_bus
        from .schema_routers import install_schema_routers
        from .event_sourcing.bootstrap import bootstrap_event_handlers

        schema_event_bus = get_event_bus()
        install_schema_routers(
            app,
            resolve_organization_id=_kpi_organization_id_for_request,
            event_bus=schema_event_bus,
        )
    except ImportError as exc:
        logger.warning("schema_router_install_skipped", extra={"error": str(exc)})

    # Audit API routes
    if hasattr(app.state, "audit_store"):
        from ruhu.audit.api import build_audit_router as _build_audit_router
        _audit_api_router = _build_audit_router(app.state.audit_store)
        app.include_router(_audit_api_router)

    install_widget_projection_router(
        app,
        attachment_runtime=attachment_runtime,
        browser_task_service=browser_task_service,
        realtime_control_plane=realtime_control_plane,
        load_conversation=kernel.load_conversation,
        list_pending_tool_invocations=lambda conversation_id: _pending_tool_invocations(
            conversation_id,
            organization_id=(
                None
                if kernel.load_conversation(conversation_id) is None
                else kernel.load_conversation(conversation_id).organization_id
            ),
        ),
        authorize_conversation_request=lambda request, conversation: widget_session_access.require_public_widget_session_access(
            request,
            conversation,
        ),
    )

    # Install observability endpoints for event sourcing metrics
    install_observability_router(app)

    # Install webhook management API
    install_webhook_api(app)

    if runtime_session_factory is not None:
        from .tools.management import (
            AgentToolBindingStore,
            APIConnectionStore,
            CredentialCipher,
            ToolAgentAssignmentStore,
            ToolDefinitionStore,
        )
        from .tools.oauth import OAuthFlowManager
        _tool_cipher: CredentialCipher | None = None
        if effective_runtime_settings.tool_credentials_encryption_key:
            _tool_cipher = CredentialCipher(effective_runtime_settings.tool_credentials_encryption_key)
        _oauth_manager: OAuthFlowManager | None = None
        if effective_runtime_settings.tool_oauth_redirect_base_url:
            # Fail-fast: an OAuth callback without a state cipher is exploitable
            # (attacker-forgeable connection_id/organization_id in the `state`
            # parameter → tokens misrouted).  Require the encryption key before
            # accepting any OAuth configuration.
            if _tool_cipher is None:
                raise RuntimeError(
                    "Tool OAuth is configured (RUHU_TOOL_OAUTH_REDIRECT_BASE_URL) "
                    "but RUHU_TOOL_CREDENTIALS_ENCRYPTION_KEY is not set. "
                    "Generate a Fernet key and set it before enabling OAuth."
                )
        # Phase-2: reuse the shared AEAD cipher + APIConnectionStore
        # passed into ``create_app`` so the tool runtime, tool-router, and
        # OAuth flow all share one instance and one audit trail.  When
        # ``create_app`` is called without a store (e.g. custom test
        # harness), fall back to a freshly-built pair so the router still
        # works — audit events from this path will then log the
        # "no audit router" warning until someone wires one in.
        if connection_store is None:
            from .tools.cipher import FernetCipher as _BlobFernetCipher
            try:
                _fallback_blob_cipher = _BlobFernetCipher.from_env()
            except ValueError:
                from cryptography.fernet import Fernet as _DevFernet
                _fallback_blob_cipher = _BlobFernetCipher(
                    primary=_DevFernet.generate_key().decode()
                )
            from .tools.management import APIConnectionStore as _APIConnectionStore
            _effective_connection_store = _APIConnectionStore(
                runtime_session_factory,
                blob_cipher=_fallback_blob_cipher,
                legacy_cipher=cipher,
                audit_router=_audit_router_instance,
            )
        else:
            _effective_connection_store = connection_store
        _effective_blob_cipher = _effective_connection_store.blob_cipher
        if effective_runtime_settings.tool_oauth_redirect_base_url:
            _oauth_manager = OAuthFlowManager(
                runtime_session_factory,
                cipher=_tool_cipher,
                blob_cipher=_effective_blob_cipher,
                redirect_base_url=effective_runtime_settings.tool_oauth_redirect_base_url,
                audit_router=_audit_router_instance,
            )
            # Wire 401-retry on the HTTP executor: when an outbound
            # tool call hits 401, the executor calls back into the
            # OAuth manager to force-refresh and retries once. Late
            # binding because the runtime is constructed earlier in
            # the boot sequence than the OAuth manager.
            from .tools.oauth_providers import get_client_credentials as _get_oauth_creds
            _http_executor = kernel.tool_runtime.get_executor("http")
            if _http_executor is not None and hasattr(_http_executor, "set_on_unauthorized"):
                def _on_unauthorized(request_config: dict[str, Any]) -> dict[str, str] | None:
                    connection_id = request_config.get("connection_id")
                    organization_id = request_config.get("organization_id")
                    if not connection_id or not organization_id:
                        return None
                    new_tokens = _oauth_manager.force_refresh_sync(
                        connection_id=str(connection_id),
                        organization_id=str(organization_id),
                        get_credentials=lambda provider: _get_oauth_creds(provider, effective_runtime_settings),
                    )
                    access_token = (new_tokens or {}).get("access_token")
                    if not access_token:
                        return None
                    return {"Authorization": f"Bearer {access_token}"}
                _http_executor.set_on_unauthorized(_on_unauthorized)
        install_tools_router(
            app,
            connection_store=_effective_connection_store,
            definition_store=ToolDefinitionStore(runtime_session_factory),
            assignment_store=ToolAgentAssignmentStore(runtime_session_factory),
            binding_store=AgentToolBindingStore(runtime_session_factory),
            tool_runtime=kernel.tool_runtime,
            cipher=_tool_cipher,
            oauth_manager=_oauth_manager,
            settings=effective_runtime_settings,
        )

    # ── React SPA static serving ──────────────────────────────────────────────
    # Serve the built React frontend from frontend/dist.  Static assets (JS/CSS
    # bundles) are mounted at /assets; all other unmatched paths fall through to
    # index.html so the client-side router handles navigation.
    _frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    _frontend_assets = _frontend_dist / "assets"
    _frontend_index = _frontend_dist / "index.html"
    if _frontend_assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_frontend_assets)), name="spa-assets")

    if _frontend_index.is_file():
        @app.get("/{path:path}", response_class=HTMLResponse, include_in_schema=False)
        def spa_fallback(path: str) -> HTMLResponse:
            return HTMLResponse(_frontend_index.read_text())

    # ── Observability + Rate limiting ─────────────────────────────────────────
    # Middleware stack (LIFO — last registered = outermost, i.e. first to see request):
    #
    #   Request
    #     → RequestIDMiddleware             (assign/propagate X-Request-ID)
    #     → MetricsMiddleware               (record HTTP counts + latency)
    #     → PublicRateLimitMiddleware       (IP-keyed guard for /auth/* and /public/*)
    #     → WidgetSessionRateLimitMiddleware (session-token-keyed guard for /public/widget/*)
    #     → AuthContextMiddleware           (verify JWT, populate request.state.auth_context)
    #     → Router
    #
    # Registration order here (outermost registered last):
    configure_structlog(environment=effective_runtime_settings.environment)
    app.mount("/metrics", make_metrics_app())

    # Innermost rate-limit layer first: per-widget-session bucket. IP-shared
    # bots cannot starve legitimate sessions on the same IP — each session
    # token has its own quota. Fails open if no token is present (the route
    # itself rejects unauthenticated widget calls via the session-token
    # check inside WidgetSessionAccessService.require_public_widget_session_access).
    app.add_middleware(
        WidgetSessionRateLimitMiddleware,
        redis_url=effective_runtime_settings.redis_url,
    )
    app.add_middleware(
        PublicRateLimitMiddleware,
        redis_url=effective_runtime_settings.redis_url,
        trusted_proxy_cidrs=effective_runtime_settings.rate_limit_trusted_proxy_cidrs,
    )
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RequestIDMiddleware)

    return app
