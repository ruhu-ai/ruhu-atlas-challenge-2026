"""
HTTP request/response DTOs extracted from ``api.py``.

**Purpose:** these Pydantic models describe the JSON shapes exchanged at the
HTTP boundary (request bodies and response envelopes). They are distinct from
``schemas.py`` which holds core runtime types (``ConversationState``,
``RuntimeTurn``) that travel through the kernel.

**Why the separation exists:** HTTP DTOs change on a different cadence than
runtime types. They also tend to couple to specific endpoints (e.g.
``DashboardStats`` is specific to the ``/dashboard/stats`` endpoint). Mixing
the two layers in one file doubles the churn and muddles which type is used
where.

**Migration note:** this module is being populated in batches. As of Batch 1,
it contains the dashboard / widget-session models. Additional batches (agents,
journeys, knowledge, KPI, billing, tool, rule, channel) will follow.

Closure-nested models (``CloseAccountRequest``, ``ConfirmActionRequest``, etc.)
intentionally remain inline in ``api.py`` — they reference endpoint-local
scope and extraction ROI is negative.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .schemas import (
    ActionRecord,
    ConversationControlState,
    ConversationMode,
    ConversationOutcome,
    ConversationStatus,
    FactDef,
    FactUpdate,
    AgentVersionStatus,
    GuardResultRecord,
    InteractionDebugActiveRepair,
    InteractionDebugPendingAction,
    InteractionDebugPendingPermission,
    RenderedMessage,
    RuntimeRulesTrace,
    SemanticEventRecord,
    StepCapabilities,
    ToolCallRecord,
)
from .agent_document import AgentDocument
from .persona import BehavioralPersona, CosmeticPersona, compose_persona_block
from .simulation_eval import EvaluationPolicyConfig


# ─── Agent summary (shared by dashboard, widget config, agent list) ────────────

class AgentSummary(BaseModel):
    id: str
    name: str
    version: str
    step_count: int
    description: str = ""
    agent_type: Literal["chat", "voice", "multimodal"] = "voice"
    llm_provider: Literal["openai", "anthropic", "gemini", "vertex", "vllm"] = "vertex"
    llm_model: str = "gemini-3-flash-preview"
    knowledge_base_count: int = 0
    has_draft_version: bool = False
    has_published_version: bool = False
    has_unpublished_changes: bool = False
    updated_at: datetime
    current_draft_version_id: str | None = None
    current_published_version_id: str | None = None
    is_widget_enabled: bool = False
    widget_mode: str = "multimodal"
    widget_config: dict[str, object] = Field(default_factory=dict)


# ─── Dashboard models ──────────────────────────────────────────────────────────

class DashboardPerformance(BaseModel):
    agent_id: str
    agent_name: str
    status: Literal["draft", "published"]
    conversation_count: int
    active_conversations: int
    resolution_rate: float
    avg_turns_per_conversation: float
    avg_handle_time_seconds: float


class DashboardResolutionPoint(BaseModel):
    date: str
    resolved: int
    total: int
    rate: float
    target: float = 80.0


class DashboardStats(BaseModel):
    total_agents: int
    active_conversations: int
    resolution_rate: float
    avg_handle_time_seconds: float
    agent_performance: list[DashboardPerformance]
    resolution_trend: list[DashboardResolutionPoint]


# ─── Batch 2: Agent CRUD models ────────────────────────────────────────────────

class AgentVersionSummary(BaseModel):
    version_id: str
    agent_id: str
    status: AgentVersionStatus
    version_number: int
    schema_version: str
    based_on_version_id: str | None = None
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    is_current_draft: bool = False
    is_current_published: bool = False


class AgentDraftCreateRequest(BaseModel):
    source_version_id: str | None = None


class AgentVersionTargetResponse(BaseModel):
    agent_id: str
    agent_name: str
    document: AgentDocument
    version: AgentVersionSummary


ClassifierStrategy = Literal["off", "main_llm", "prefill"]


class AgentClassifierConfig(BaseModel):
    """Per-agent classifier configuration.

    - ``off``: no intent classifier runs; the kernel routes only on facts/
      tool outcomes/``otherwise``. Authors who don't depend on
      ``intent_detected:*`` transitions can use this.
    - ``main_llm`` *(default)*: a frontier LLM (Vertex Gemini Flash)
      classifies each turn against the step's intent catalog. Accurate
      cold-start, slower and more expensive than a trained adapter.
    - ``prefill``: the small prefill-first classifier runs (Gemma/Qwen +
      registered LoRA). Fast and cheap. **Backend rejects this strategy
      unless a production-status LoRA exists for the agent and has passed
      eval.**
    """
    strategy: ClassifierStrategy = "main_llm"


class AgentClassifierConfigPatchRequest(BaseModel):
    strategy: ClassifierStrategy | None = None


class AgentLLMConfig(BaseModel):
    provider: Literal["openai", "anthropic", "gemini", "vertex", "vllm"] = "vertex"
    model: str = "gemini-3-flash-preview"
    temperature: float = Field(default=1.0, ge=0.0, le=2.0)
    classifier: AgentClassifierConfig = Field(default_factory=AgentClassifierConfig)


class AgentLLMConfigPatchRequest(BaseModel):
    provider: Literal["openai", "anthropic", "gemini", "vertex", "vllm"] | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    classifier: AgentClassifierConfigPatchRequest | None = None


class AgentVoiceConfig(BaseModel):
    voice_id: str = "en-US-Chirp3-HD-Kore"


class AgentVoiceConfigPatchRequest(BaseModel):
    voice_id: str | None = None


class AgentSettings(BaseModel):
    description: str = ""
    agent_type: Literal["chat", "voice", "multimodal"] = "voice"
    system_prompt: str = "You are a helpful AI voice assistant."
    llm_config: AgentLLMConfig = Field(default_factory=AgentLLMConfig)
    voice_config: AgentVoiceConfig = Field(default_factory=AgentVoiceConfig)
    knowledge_base_ids: list[str] = Field(default_factory=list)
    # Cosmetic persona — live-edit, applies on PATCH. Behavioural persona
    # (formality, restricted_topics) lives on AgentDocument.metadata.persona
    # and goes through draft → publish-review → publish.
    persona: CosmeticPersona | None = None
    # Set at clone time (api.py:13684 / api.py:14021).  Read-only from
    # the client's perspective — surfaced so the post-clone setup
    # checklist can recover template provenance when the user navigates
    # back to /agents/:id/setup without the ?template= query param.
    source_template_id: str | None = None

    def composed_system_prompt(
        self,
        *,
        behavioral: BehavioralPersona | None = None,
        company_name: str | None = None,
    ) -> str:
        """Return the persona-prefixed system prompt.

        Returns ``self.system_prompt`` byte-identically when ``self.persona``
        is ``None`` and ``behavioral`` is ``None`` — the contract that keeps
        existing agents (no persona configured) producing exactly the same
        prompts as before.
        """
        block = compose_persona_block(self.persona, behavioral, company_name)
        return f"{block}\n\n{self.system_prompt}".strip() if block else self.system_prompt


class AgentSettingsPatchRequest(BaseModel):
    description: str | None = None
    agent_type: Literal["chat", "voice", "multimodal"] | None = None
    system_prompt: str | None = None
    llm_config: AgentLLMConfigPatchRequest | None = None
    voice_config: AgentVoiceConfigPatchRequest | None = None
    knowledge_base_ids: list[str] | None = None
    persona: CosmeticPersona | None = None


# Re-export for callers that need to type-narrow on the strategy literal.
__all__ = [
    "AgentClassifierConfig",
    "AgentClassifierConfigPatchRequest",
    "AgentLLMConfig",
    "AgentLLMConfigPatchRequest",
    "AgentSettings",
    "AgentSettingsPatchRequest",
    "AgentSettingsResponse",
    "BehavioralPersona",
    "ClassifierStrategy",
    "CosmeticPersona",
]


class AgentSettingsResponse(BaseModel):
    agent_id: str
    settings: AgentSettings


class AgentCreateRequest(BaseModel):
    name: str
    settings: AgentSettings = Field(default_factory=AgentSettings)
    document: AgentDocument


class AgentMetadataPatchRequest(BaseModel):
    name: str | None = None


class AgentEvaluationPolicyPatchRequest(BaseModel):
    minimum_pass_rate_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    allow_warning_failures: bool | None = None
    max_qualified_run_age_hours: int | None = Field(default=None, ge=1)


class AgentEvaluationPolicyResponse(BaseModel):
    agent_id: str
    policy: EvaluationPolicyConfig


class AgentDocumentResponse(BaseModel):
    agent_id: str
    target: AgentVersionStatus
    document: AgentDocument


# ─── Batch 3: Step-native raw conversation DTOs ──────────────────────────────


class ConversationRuntimeResponse(BaseModel):
    conversation_id: str
    organization_id: str | None = None
    agent_id: str
    agent_version_id: str
    mode: ConversationMode
    channel: str | None = None
    status: ConversationStatus
    outcome: ConversationOutcome | None = None
    step_id: str
    scenario_id: str | None = None
    step_capabilities: StepCapabilities = Field(default_factory=StepCapabilities)
    missing_facts: list[str] = Field(default_factory=list)
    available_tool_refs: list[str] = Field(default_factory=list)
    transition_target_ids: list[str] = Field(default_factory=list)
    scripted_say: str | None = None
    facts: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)
    started_at: datetime
    ended_at: datetime | None = None
    updated_at: datetime
    control_state: ConversationControlState = Field(default_factory=ConversationControlState)


class TurnInteractionDebugVoicePolicyResponse(BaseModel):
    step_id: str
    channel: str
    endpointing_ms: int
    soft_timeout_ms: int
    turn_eagerness: str
    interruptibility_policy: str


class TurnInteractionDebugSnapshotResponse(BaseModel):
    step_id: str
    channel: str
    voice_interaction_policy: TurnInteractionDebugVoicePolicyResponse
    pending_action: InteractionDebugPendingAction | None = None
    pending_permission: InteractionDebugPendingPermission | None = None
    active_repair: InteractionDebugActiveRepair | None = None


class TurnExecutionResponse(BaseModel):
    turn_id: str
    conversation_id: str
    step_before: str
    step_after: str
    scenario_before: str | None = None
    scenario_after: str | None = None
    semantic_events: list[SemanticEventRecord] = Field(default_factory=list)
    fact_updates: list[FactUpdate] = Field(default_factory=list)
    chosen_action: ActionRecord
    emitted_messages: list[RenderedMessage] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    rules: RuntimeRulesTrace = Field(default_factory=RuntimeRulesTrace)
    trace_id: str
    latency_breakdown_ms: dict[str, int] = Field(default_factory=dict)
    interaction_debug_snapshot: TurnInteractionDebugSnapshotResponse | None = None


class StartConversationResponse(BaseModel):
    conversation: ConversationRuntimeResponse
    start: TurnExecutionResponse


class ConversationTraceResponse(BaseModel):
    """Per-turn trace surfaced by ``GET /conversations/{id}/traces``.

    Used by the canvas Reasoning Timeline (Sierra-style "what did the agent
    do this turn?" UX) and conversation postmortems. Fields beyond the
    original (step_before/after/emitted_messages) were added to expose the
    rich runtime evidence that ``TurnTrace`` already records but kept
    server-side: which guards passed, what action was chosen and why, what
    tools ran with what status/latency, where time went per turn.

    Stays a strict subset of ``TurnTrace`` — fields like ``model_outputs``
    that contain raw LLM input/output are deliberately omitted to keep
    the public surface small and PII-safe by default.
    """
    trace_id: str
    conversation_id: str
    turn_id: str
    step_before: str
    step_after: str
    event_type: str = ""
    emitted_messages: list[RenderedMessage] = Field(default_factory=list)
    # Reasoning-timeline fields. All default to safe empty values so older
    # consumers that just read step_before/after/emitted_messages keep
    # working unchanged.
    chosen_action: ActionRecord | None = None
    guard_results: list[GuardResultRecord] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    latency_breakdown_ms: dict[str, int] = Field(default_factory=dict)
    recorded_at: datetime


class RealtimeConversationEventResponse(BaseModel):
    event_id: str
    conversation_id: str
    realtime_session_id: str | None = None
    family: str
    name: str
    conversation_sequence: int
    actor_type: str | None = None
    actor_id: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
