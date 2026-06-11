from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from .atlas_protocol import AtlasBlocker, AtlasProposedChanges


AtlasReadinessScope = Literal["build", "validate", "fix", "operate"]
AtlasReadinessProviderPolicy = Literal["google_only", "anthropic_only", "hybrid", "deterministic"]
# AR-4.5: only states the run loop can actually enter. The after-apply rerun
# flow (awaiting_permission / applying_deltas / rerunning_suite) was never
# implemented — `rerun` starts a fresh run reusing the case set instead — so
# those aspirational states are removed rather than left as a dead interface.
AtlasReadinessRunState = Literal[
    "created",
    "resolving_document",
    "generating_cases",
    "running_simulations",
    "running_voice_cases",
    "extracting_traces",
    "scoring",
    "proposing_deltas",
    "awaiting_review",
    "writing_report",
    "completed",
    "failed",
    "cancelled",
]
AtlasReadinessPublishRecommendation = Literal["publish", "do_not_publish", "needs_review"]


def new_atlas_readiness_run_id() -> str:
    return f"atlas_readiness_run_{uuid4().hex}"


def new_atlas_readiness_event_id() -> str:
    return f"atlas_readiness_event_{uuid4().hex}"


def new_atlas_readiness_case_set_id() -> str:
    return f"atlas_readiness_case_set_{uuid4().hex}"


def new_atlas_readiness_case_id() -> str:
    return f"atlas_readiness_case_{uuid4().hex}"


class AtlasReadinessRunRequest(BaseModel):
    agent_id: str | None = None
    agent_version_id: str | None = None
    workflow_brief: str | None = None
    scope: AtlasReadinessScope = "validate"
    provider_policy: AtlasReadinessProviderPolicy | None = None
    demo_case_set: bool = False
    voice_case_count: int = Field(default=0, ge=0)
    voice_audio_uri: str | None = None
    voice_language: str | None = None
    require_real_voice_io: bool = False
    cloud_evidence: bool = True
    case_limit: int = Field(default=12, ge=1, le=100)
    seed: int | None = None
    reuse_case_set_id: str | None = None
    max_estimated_cost_usd: Decimal | None = None
    max_wall_clock_seconds: int = Field(default=900, ge=1)
    paused_run_ttl_seconds: int = Field(default=1_209_600, ge=1)

    @field_validator("workflow_brief")
    @classmethod
    def _clean_workflow_brief(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("voice_audio_uri", "voice_language")
    @classmethod
    def _clean_optional_voice_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class AtlasSyntheticTestProfile(BaseModel):
    profile_id: str
    locale: str
    channel: Literal["chat", "whatsapp", "voice"]
    language_style: str
    emotional_state: str
    goal: str
    risk_tags: list[str] = Field(default_factory=list)


# Caps on a single evaluation case so a runaway (or hostile) MCP orchestrator
# cannot submit thousands of turns / megabytes of text per call.
_MAX_CASE_UTTERANCES = 50
_MAX_UTTERANCE_CHARS = 4000


class AtlasReadinessCase(BaseModel):
    case_id: str
    test_profile: AtlasSyntheticTestProfile
    scenario_summary: str
    utterances: list[str] = Field(max_length=_MAX_CASE_UTTERANCES)
    expected_final_step_ids: list[str] = Field(default_factory=list)
    expected_facts: dict[str, object] = Field(default_factory=dict)
    fact_comparison_policy: Literal["exact", "capture_normalized", "subset"] = "capture_normalized"
    forbidden_reply_terms: list[str] = Field(default_factory=list)
    required_trace_events: list[str] = Field(default_factory=list)
    voice_input: dict[str, object] | None = None

    @field_validator("utterances")
    @classmethod
    def _cap_utterance_length(cls, value: list[str]) -> list[str]:
        for utterance in value:
            if len(utterance) > _MAX_UTTERANCE_CHARS:
                raise ValueError(
                    f"utterance exceeds {_MAX_UTTERANCE_CHARS} characters"
                )
        return value


class AtlasReadinessTrace(BaseModel):
    trace_id: str = Field(default_factory=lambda: f"atlas_readiness_trace_{uuid4().hex}")
    case_id: str
    conversation_id: str
    final_step_id: str
    completion_status: str | None = None
    step_path: list[str] = Field(default_factory=list)
    extracted_facts: dict[str, object] = Field(default_factory=dict)
    tool_calls: list[dict[str, object]] = Field(default_factory=list)
    replies: list[str] = Field(default_factory=list)
    handoff_decisions: list[dict[str, object]] = Field(default_factory=list)
    voice_metrics: dict[str, object] = Field(default_factory=dict)


class AtlasReadinessScore(BaseModel):
    case_id: str
    passed: bool
    score_source: Literal["deterministic", "hybrid", "llm_advisory"]
    containment_score: float = Field(ge=0.0, le=1.0)
    safety_score: float = Field(ge=0.0, le=1.0)
    traceability_score: float = Field(ge=0.0, le=1.0)
    voice_reliability_score: float | None = Field(default=None, ge=0.0, le=1.0)
    operational_readiness_score: float = Field(ge=0.0, le=1.0)
    improvement_potential_score: float = Field(ge=0.0, le=1.0)
    trajectory_score: float = Field(ge=0.0, le=1.0)
    case_score: float = Field(ge=0.0, le=1.0)
    failures: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    advisory_notes: list[str] = Field(default_factory=list)


class AtlasTraceRepairPlan(BaseModel):
    case_id: str
    diagnosis_facts: list[str] = Field(default_factory=list)
    failed_categories: list[
        Literal[
            "containment",
            "safety",
            "traceability",
            "voice_reliability",
            "operational_readiness",
            "trajectory",
            "improvement_potential",
        ]
    ] = Field(default_factory=list)
    target_delta_families: list[
        Literal[
            "agent_metadata_deltas",
            "scenario_deltas",
            "step_deltas",
            "scenario_route_deltas",
        ]
    ] = Field(default_factory=list)
    rationale_summary: str | None = None
    evidence_trace_event_ids: list[str] = Field(default_factory=list)
    blocker_reason: str | None = None


class AtlasProviderInvocationMetadata(BaseModel):
    provider: str
    model: str
    role: str
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    estimated_cost_usd: Decimal | None = None
    validation_outcome: Literal["valid", "invalid", "repaired", "blocked"]
    fallback_reason: str | None = None
    retry_count: int = 0
    timeout_seconds: float
    cancelled: bool = False


class AtlasVoiceArtifact(BaseModel):
    artifact_id: str = Field(default_factory=lambda: f"atlas_voice_artifact_{uuid4().hex}")
    run_id: str
    case_id: str
    provider: str
    artifact_type: Literal["stt_transcript", "tts_audio", "voice_metrics"]
    uri: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AtlasReadinessCancelled(RuntimeError):
    """Raised by a cancellation token when an in-flight run was cancelled.

    A dedicated type so the run loop classifies cancellation by isinstance
    rather than substring-matching exception text (which misfires on unrelated
    errors that merely mention "cancelled").
    """


class AtlasReadinessRunTerminal(RuntimeError):
    """Raised when a state update would move a run out of a terminal state.

    Guards against a cancelled/completed/failed run being silently
    un-terminated by an in-flight transition that raced past cancellation.
    """


class AtlasReadinessBudgetExceeded(ValueError):
    """Raised when an estimated or observed cost exceeds the run's ceiling.

    Subclasses ValueError so the API maps it to 400 (a client-adjustable
    condition: lower the case count or raise the ceiling).
    """


class AtlasReadinessTimeoutExceeded(ValueError):
    """Raised when a run exceeds its ``max_wall_clock_seconds`` budget.

    Subclasses ValueError → 400; the run record is also persisted as ``failed``
    with a ``timeout_exceeded`` blocker for polling.
    """


class AtlasCancellationToken(Protocol):
    def is_cancelled(self) -> bool: ...
    def throw_if_cancelled(self) -> None: ...


class SimpleAtlasCancellationToken:
    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    def throw_if_cancelled(self) -> None:
        if self._cancelled:
            raise AtlasReadinessCancelled("atlas readiness run cancelled")


class AtlasReadinessReport(BaseModel):
    run_id: str
    agent_id: str | None
    before_scores: list[AtlasReadinessScore]
    after_scores: list[AtlasReadinessScore] = Field(default_factory=list)
    proposed_changes: AtlasProposedChanges = Field(default_factory=AtlasProposedChanges)
    publish_recommendation: AtlasReadinessPublishRecommendation
    blockers: list[AtlasBlocker] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    provider_invocations: list[AtlasProviderInvocationMetadata] = Field(default_factory=list)
    estimated_cost_usd: Decimal | None = None
    observed_cost_usd: Decimal | None = None
    narrative: dict[str, object] = Field(default_factory=dict)
    evidence: dict[str, object] = Field(default_factory=dict)
    score_breakdown: dict[str, object] = Field(default_factory=dict)

    @property
    def before_pass_rate(self) -> float:
        if not self.before_scores:
            return 0.0
        return round(sum(1 for score in self.before_scores if score.passed) / len(self.before_scores), 4)

    @property
    def after_pass_rate(self) -> float | None:
        if not self.after_scores:
            return None
        return round(sum(1 for score in self.after_scores if score.passed) / len(self.after_scores), 4)


class AtlasReadinessEvent(BaseModel):
    event_id: str
    run_id: str
    sequence_number: int
    type: str
    payload: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class AtlasReadinessRun(BaseModel):
    run_id: str
    organization_id: str | None = None
    agent_id: str | None = None
    agent_version_id: str | None = None
    atlas_session_id: str | None = None
    scope: AtlasReadinessScope
    state: AtlasReadinessRunState
    provider_policy: AtlasReadinessProviderPolicy
    case_set_id: str | None = None
    document_hash: str | None = None
    policy_hash: str | None = None
    provider_config_hash: str | None = None
    request: AtlasReadinessRunRequest
    created_by_user_id: str | None = None
    blocker_codes: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class AtlasReadinessCaseSet(BaseModel):
    case_set_id: str
    organization_id: str | None = None
    agent_id: str | None = None
    seed: int | None = None
    provider_policy: AtlasReadinessProviderPolicy
    cases: list[AtlasReadinessCase]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AtlasReadinessRunSummary(BaseModel):
    run: AtlasReadinessRun
    case_set: AtlasReadinessCaseSet | None = None
    report: AtlasReadinessReport | None = None


class AtlasReadinessRunsPage(BaseModel):
    runs: list[AtlasReadinessRun]
    has_more: bool
    total_count: int


class AtlasReadinessProviderHealth(BaseModel):
    provider_policy: AtlasReadinessProviderPolicy
    gemini_configured: bool
    anthropic_configured: bool
    artifact_store_configured: bool
    voice_harness: str
    warnings: list[str] = Field(default_factory=list)


class AtlasReadinessEventsPage(BaseModel):
    run_id: str
    events: list[AtlasReadinessEvent]
    has_more: bool
    total_count: int
