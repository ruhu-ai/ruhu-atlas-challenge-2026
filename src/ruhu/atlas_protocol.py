from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .atlas_models import (
    AtlasEventType,
    AtlasMessageRole,
    AtlasPermissionKind,
    AtlasPermissionStatus,
    AtlasScope,
    AtlasSessionStatus,
)


# Bump on any breaking change to the turn request/response contract so the
# frontend can detect server/client drift instead of failing on shape only
# (AR-5.3).
ATLAS_PROTOCOL_VERSION = "1.0"


AtlasNextAction = Literal[
    "ask_questions",
    "ready_to_review_changes",
    "ready_to_provision",
    "ready_to_validate",
    "complete",
    "blocked",
]


class AtlasSelectedContext(BaseModel):
    agent_id: str | None = None
    agent_version_id: str | None = None
    scenario_id: str | None = None
    step_id: str | None = None
    conversation_id: str | None = None
    trace_id: str | None = None


class BlockingQuestion(BaseModel):
    question_id: str
    question: str
    help_text: str | None = None
    options: list[str] | None = None
    required: bool = True
    target_ref: str | None = None


class AtlasDependency(BaseModel):
    key: str
    kind: Literal[
        "integration",
        "tool",
        "knowledge",
        "rule",
        "agent",
        "scenario",
        "step",
        "channel_policy",
    ]
    display_name: str
    status: Literal["connected", "available", "requires_auth", "missing", "configured", "invalid"]
    blocking: bool = False
    reason: str | None = None
    suggested_action: str | None = None
    reference_ids: list[str] = Field(default_factory=list)


class AtlasBlocker(BaseModel):
    code: str
    message: str
    blocking: bool = True
    reference_ids: list[str] = Field(default_factory=list)


AtlasAttachmentKind = Literal[
    "document",
    "image",
    "spec",
    "transcript",
    "agent_document_json",
    "json_brief",
    "workflow_description",
]


class AtlasAttachmentInput(BaseModel):
    attachment_id: str
    kind: AtlasAttachmentKind
    display_name: str
    source_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AtlasAttachmentIngestionResult(BaseModel):
    attachment_id: str
    display_name: str
    # AR-5.3: round-trip the input kind as the same Literal rather than a plain
    # str, so the type isn't lost between request and response.
    kind: AtlasAttachmentKind
    mode: Literal["agent_document", "json_brief", "text_extracted", "attachment_bundle"]
    extracted_characters: int = 0
    chunk_count: int = 0
    used_chunk_count: int = 0
    quality_flags: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    truncated: bool = False
    suggested_interpretation: Literal[
        "review_as_authored_document",
        "review_as_partial_brief",
        "review_as_reference_only",
    ]
    blocking_questions: list[str] = Field(default_factory=list)


class AtlasAPIDiscoveryRequest(BaseModel):
    request_id: str
    source_type: Literal[
        "openapi_url",
        "swagger_url",
        "postman_url",
        "website_url",
        "uploaded_spec",
        "uploaded_postman",
        "pasted_schema",
        "pasted_postman",
    ]
    source_value: str
    intent: str | None = None


class AtlasProvisioningCandidate(BaseModel):
    binding_key: str
    display_name: str
    tool_ref: str | None = None
    requires_credentials: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    suggested_setup_action: str | None = None
    provider_slug: str | None = None
    setup_url: str | None = None
    documentation_url: str | None = None


class AtlasAPIDiscoveryResult(BaseModel):
    request_id: str
    status: Literal["not_run", "discovered", "unsupported", "failed"]
    provider_name: str | None = None
    candidate_tool_refs: list[str] = Field(default_factory=list)
    missing_auth_fields: list[str] = Field(default_factory=list)
    notes: str | None = None
    spec_type: Literal["openapi", "swagger", "postman", "llm_parsed", "heuristic", "unknown"] = "unknown"
    base_url: str | None = None
    candidate_endpoints: list[dict[str, Any]] = Field(default_factory=list)
    provisioning_candidates: list[AtlasProvisioningCandidate] = Field(default_factory=list)
    requires_review_before_provisioning: bool = True


class AtlasPermissionDecision(BaseModel):
    request_id: str
    decision: Literal["approved", "denied"]
    reason: str | None = None


class AtlasPermissionRequestModel(BaseModel):
    request_id: str
    kind: AtlasPermissionKind
    status: AtlasPermissionStatus
    reason: str
    risk_summary: str | None = None
    scope_ref: dict[str, Any] = Field(default_factory=dict)
    delta_ids: list[str] = Field(default_factory=list)
    requested_actions: list[str] = Field(default_factory=list)
    created_at: datetime
    expires_at: datetime | None = None


class AtlasEventEnvelope(BaseModel):
    event_id: str
    session_id: str
    sequence_number: int
    type: AtlasEventType
    created_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


AtlasDeltaOperation = Literal["create", "update", "delete", "reorder", "move"]
AtlasDeltaStatus = Literal["proposed", "approved", "applied", "rejected", "superseded"]

# Per-family change_type vocabularies. These are the change types the apply
# machinery in AtlasCoordinator actually supports; an unknown change_type is a
# contract violation rejected at parse time rather than a runtime apply error.
AgentMetadataChangeType = Literal[
    "add_fact_schema_entry",
    "update_fact_schema_entry",
    "delete_fact_schema_entry",
    "reorder_fact_schema_entry",
]
ScenarioChangeType = Literal["rename_scenario"]
StepChangeType = Literal[
    "create_step",
    "delete_step",
    "reorder_step",
    "rename_step",
    "update_step_say",
    "set_step_handoff",
    "set_step_completion",
    "update_response_policy",
    "add_fact_requirement",
    "add_tool_binding",
    "add_guard",
    "add_step_transition",
    "update_step_transition",
    "delete_step_transition",
]
ScenarioRouteChangeType = Literal[
    "create_scenario_route",
    "update_scenario_route",
    "delete_scenario_route",
]
ChannelPolicyChangeType = Literal[
    "set_entry_scenario",
    "enable_channel",
    "disable_channel",
    "restrict_scenarios_for_channel",
    "update_ingress_policy",
]
IntegrationBindingChangeType = Literal[
    "provision_provider_template",
    "ingest_openapi_tools",
    "prepare_custom_oauth_connection",
    "bind_existing_connection",
    "reauthorize_connection",
    "repair_connection",
]


class AgentMetadataDelta(BaseModel):
    agent_id: str
    delta_id: str
    operation: AtlasDeltaOperation
    status: AtlasDeltaStatus = "proposed"
    change_type: AgentMetadataChangeType
    depends_on_delta_ids: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    summary: str


class ScenarioDelta(BaseModel):
    agent_id: str
    scenario_id: str | None = None
    delta_id: str
    operation: AtlasDeltaOperation
    status: AtlasDeltaStatus = "proposed"
    change_type: ScenarioChangeType
    depends_on_delta_ids: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    summary: str


class StepDelta(BaseModel):
    agent_id: str
    scenario_id: str
    step_id: str | None = None
    delta_id: str
    operation: AtlasDeltaOperation
    status: AtlasDeltaStatus = "proposed"
    change_type: StepChangeType
    depends_on_delta_ids: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    summary: str


class ScenarioRouteDelta(BaseModel):
    agent_id: str
    route_id: str | None = None
    delta_id: str
    operation: AtlasDeltaOperation
    status: AtlasDeltaStatus = "proposed"
    change_type: ScenarioRouteChangeType
    depends_on_delta_ids: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    summary: str


class ChannelPolicyDelta(BaseModel):
    agent_id: str
    delta_id: str
    operation: AtlasDeltaOperation
    status: AtlasDeltaStatus = "proposed"
    change_type: ChannelPolicyChangeType
    depends_on_delta_ids: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    summary: str


class RuleDelta(BaseModel):
    target_id: str | None = None
    delta_id: str
    operation: AtlasDeltaOperation
    status: AtlasDeltaStatus = "proposed"
    # Rule programs are an owned subsystem without a finalized Atlas change
    # vocabulary yet; the family is not applyable and validation blocks it.
    change_type: str
    depends_on_delta_ids: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    summary: str


class KnowledgeDelta(BaseModel):
    target_id: str | None = None
    delta_id: str
    operation: AtlasDeltaOperation
    status: AtlasDeltaStatus = "proposed"
    # Knowledge changes are an owned subsystem without a finalized Atlas
    # change vocabulary yet; the family is not applyable and validation blocks it.
    change_type: str
    depends_on_delta_ids: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    summary: str


class IntegrationBindingDelta(BaseModel):
    target_id: str | None = None
    delta_id: str
    operation: AtlasDeltaOperation
    status: AtlasDeltaStatus = "proposed"
    change_type: IntegrationBindingChangeType
    depends_on_delta_ids: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    summary: str


class AtlasProposedChanges(BaseModel):
    agent_metadata_deltas: list[AgentMetadataDelta] = Field(default_factory=list)
    scenario_deltas: list[ScenarioDelta] = Field(default_factory=list)
    step_deltas: list[StepDelta] = Field(default_factory=list)
    scenario_route_deltas: list[ScenarioRouteDelta] = Field(default_factory=list)
    channel_policy_deltas: list[ChannelPolicyDelta] = Field(default_factory=list)
    rule_deltas: list[RuleDelta] = Field(default_factory=list)
    knowledge_deltas: list[KnowledgeDelta] = Field(default_factory=list)
    integration_binding_deltas: list[IntegrationBindingDelta] = Field(default_factory=list)


class AtlasDerivedImpact(BaseModel):
    compiled_runtime_preview: dict[str, Any] = Field(default_factory=dict)
    affected_scenarios: list[str] = Field(default_factory=list)
    affected_steps: list[str] = Field(default_factory=list)
    possible_entry_scenario_changes: list[str] = Field(default_factory=list)
    possible_tool_execution_changes: list[str] = Field(default_factory=list)
    possible_publish_readiness_changes: list[str] = Field(default_factory=list)


class AtlasValidationCheck(BaseModel):
    code: str
    scope: Literal["agent", "scenario", "step", "compiled_runtime", "publish", "evaluation"]
    status: Literal["not_run", "passed", "failed", "warning"]
    message: str
    reference_ids: list[str] = Field(default_factory=list)


class AtlasValidationResult(BaseModel):
    status: Literal["not_run", "passed", "failed"] = "not_run"
    blocking: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checks: list[AtlasValidationCheck] = Field(default_factory=list)


class AtlasProvisioningManifestItem(BaseModel):
    agent_id: str
    provider: str
    tool_ref: str | None = None
    binding_target: str
    connection_id: str | None = None
    requires_credentials: bool = False
    connection_status: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    setup_action: str | None = None
    documentation_url: str | None = None
    blocking: bool = False
    notes: str | None = None


class AtlasReviewDecision(BaseModel):
    delta_id: str
    decision: Literal["approved", "rejected"]
    note: str | None = None


class AtlasApplyRequest(BaseModel):
    delta_ids: list[str] = Field(default_factory=list)
    apply_note: str | None = None
    confirmed_by: str | None = None


class AtlasReviewState(BaseModel):
    approved_delta_ids: list[str] = Field(default_factory=list)
    rejected_delta_ids: list[str] = Field(default_factory=list)
    pending_delta_ids: list[str] = Field(default_factory=list)
    latest_apply_request_id: str | None = None


class AtlasGeneratorInfo(BaseModel):
    mode: Literal["anthropic", "fallback"] = "fallback"
    model: str | None = None


class AtlasToolCall(BaseModel):
    name: str
    status: Literal["planned", "running", "completed", "failed", "skipped"] = "completed"
    reason: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)


class AtlasReferences(BaseModel):
    agent_ids: list[str] = Field(default_factory=list)
    agent_version_ids: list[str] = Field(default_factory=list)
    scenario_ids: list[str] = Field(default_factory=list)
    step_ids: list[str] = Field(default_factory=list)
    conversation_ids: list[str] = Field(default_factory=list)
    trace_ids: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)
    tool_refs: list[str] = Field(default_factory=list)


class AtlasSessionStartRequest(BaseModel):
    scope: AtlasScope
    agent_id: str
    agent_version_id: str | None = None
    scenario_id: str | None = None
    step_id: str | None = None
    initial_message: str | None = None


class AtlasSessionResponse(BaseModel):
    session_id: str
    status: AtlasSessionStatus
    scope: AtlasScope
    agent_id: str
    agent_version_id: str | None = None
    created_by: str | None = None
    scenario_id: str | None = None
    step_id: str | None = None
    created_at: datetime
    updated_at: datetime


class AtlasSessionsPageResponse(BaseModel):
    sessions: list[AtlasSessionResponse]
    total_count: int
    has_more: bool


class AtlasTurnRequest(BaseModel):
    protocol_version: str = ATLAS_PROTOCOL_VERSION
    session_id: str
    message: str | None = None
    question_answers: dict[str, Any] = Field(default_factory=dict)
    selected_context: AtlasSelectedContext | None = None
    attachments: list[AtlasAttachmentInput] = Field(default_factory=list)
    api_discovery_requests: list[AtlasAPIDiscoveryRequest] = Field(default_factory=list)
    review_decisions: list[AtlasReviewDecision] = Field(default_factory=list)
    apply_request: AtlasApplyRequest | None = None
    permission_decisions: list[AtlasPermissionDecision] = Field(default_factory=list)


class AtlasTurnResponse(BaseModel):
    protocol_version: str = ATLAS_PROTOCOL_VERSION
    session_id: str
    message: str
    next_action: AtlasNextAction
    generator: AtlasGeneratorInfo = Field(default_factory=AtlasGeneratorInfo)
    tool_calls: list[AtlasToolCall] = Field(default_factory=list)
    questions: list[BlockingQuestion] = Field(default_factory=list)
    dependencies: list[AtlasDependency] = Field(default_factory=list)
    blockers: list[AtlasBlocker] = Field(default_factory=list)
    proposed_changes: AtlasProposedChanges = Field(default_factory=AtlasProposedChanges)
    derived_impact: AtlasDerivedImpact = Field(default_factory=AtlasDerivedImpact)
    validation: AtlasValidationResult = Field(default_factory=AtlasValidationResult)
    provisioning_manifest: list[AtlasProvisioningManifestItem] = Field(default_factory=list)
    api_discovery_results: list[AtlasAPIDiscoveryResult] = Field(default_factory=list)
    attachment_ingestion_results: list[AtlasAttachmentIngestionResult] = Field(default_factory=list)
    references: AtlasReferences = Field(default_factory=AtlasReferences)
    review_state: AtlasReviewState = Field(default_factory=AtlasReviewState)
    pending_permission_requests: list[AtlasPermissionRequestModel] = Field(default_factory=list)


class AtlasMessageItem(BaseModel):
    message_id: str
    role: AtlasMessageRole
    content: str
    sequence_number: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AtlasMessagesPageResponse(BaseModel):
    session_id: str
    messages: list[AtlasMessageItem]
    has_more: bool
    total_count: int


class AtlasEventsPageResponse(BaseModel):
    session_id: str
    events: list[AtlasEventEnvelope]
    has_more: bool
    total_count: int


class AtlasAgentEnabledToggleRequest(BaseModel):
    atlas_enabled: bool


class AtlasAgentEnabledResponse(BaseModel):
    agent_id: str
    atlas_enabled: bool


class AtlasArchiveSessionResponse(BaseModel):
    session_id: str
    status: AtlasSessionStatus
    archived_at: datetime


class AtlasApplyResponse(BaseModel):
    apply_request_id: str
    session_id: str
    status: Literal["pending", "rejected", "failed", "applied"]
    error: str | None = None


class AtlasPermissionDecisionResponse(BaseModel):
    session_id: str
    updated_requests: list[AtlasPermissionRequestModel] = Field(default_factory=list)


class AtlasRolloutCounterRow(BaseModel):
    labels: dict[str, str] = Field(default_factory=dict)
    value: float


class AtlasRolloutPolicy(BaseModel):
    min_anthropic_generated_candidates: int
    min_reviewed_deltas: int
    min_apply_attempts: int
    min_anthropic_success_rate: float
    min_review_approval_rate: float
    min_apply_success_rate: float
    max_fallback_rate: float
    min_semantic_validation_pass_rate: float


class AtlasRolloutFamilySummary(BaseModel):
    family: str
    heuristic_enabled: bool
    generated_candidates: float = 0.0
    anthropic_generated_candidates: float = 0.0
    fallback_generated_candidates: float = 0.0
    filtered_candidates: float = 0.0
    approved_reviews: float = 0.0
    rejected_reviews: float = 0.0
    applied_deltas: float = 0.0
    failed_applies: float = 0.0
    rejected_applies: float = 0.0
    approval_rate: float | None = None
    apply_success_rate: float | None = None
    semantic_validation_pass_rate: float | None = None
    rollout_status: Literal["not_enough_data", "hold", "eligible_for_trial_retirement"] = "not_enough_data"
    rollout_reasons: list[str] = Field(default_factory=list)


class AtlasRolloutSummaryResponse(BaseModel):
    policy: AtlasRolloutPolicy
    heuristic_enabled_families: list[str] = Field(default_factory=list)
    family_summaries: list[AtlasRolloutFamilySummary] = Field(default_factory=list)
    generator_requests: list[AtlasRolloutCounterRow] = Field(default_factory=list)
    generator_fallbacks: list[AtlasRolloutCounterRow] = Field(default_factory=list)
    generated_delta_candidates: list[AtlasRolloutCounterRow] = Field(default_factory=list)
    filtered_deltas: list[AtlasRolloutCounterRow] = Field(default_factory=list)
    review_decisions: list[AtlasRolloutCounterRow] = Field(default_factory=list)
    apply_outcomes: list[AtlasRolloutCounterRow] = Field(default_factory=list)
