from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from ruhu.schemas import (
    Channel,
    ConversationState,
    Modality,
    RuntimeTurnResult,
    SimulationSource,
    SimulationTurnInput,
    TurnTrace,
)
from ruhu.tools.types import ToolInvocation


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


AssertionSeverity = Literal["blocker", "warning"]
SimulationAssertionKind = Literal[
    "final_step_equals",
    "final_step_one_of",
    "fact_equals",
    "fact_in",
    "fact_matches_regex",
    "fact_present",
    "fact_absent",
    "step_path_contains",
    "step_path_excludes",
    "tool_called",
    "tool_called_count_at_least",
    "tool_called_count_equals",
    "tool_not_called",
    "tool_status",
    "message_contains",
    "message_any_of",
    "message_not_contains",
    "pending_confirmation_required",
    "pending_confirmation_absent",
    "turn_count_equals",
    "turn_count_at_most",
    "latency_total_lt_ms",
    "latency_first_response_lt_ms",
]
EvaluationRunMode = Literal["manual_batch", "publish_gate", "ci"]
EvaluationRunSource = Literal["studio", "api", "worker", "cli"]
EvaluationRunStatus = Literal["queued", "running", "stopping", "stopped", "completed", "failed", "cancelled"]
EvaluationCaseStatus = Literal["passed", "failed", "skipped", "error"]


class EvaluationPolicyConfig(BaseModel):
    minimum_pass_rate_ratio: float = 1.0
    allow_warning_failures: bool = True
    max_qualified_run_age_hours: int | None = None


class SimulationAssertion(BaseModel):
    assertion_id: str = Field(default_factory=lambda: str(uuid4()))
    kind: SimulationAssertionKind
    severity: AssertionSeverity = "blocker"
    config: dict[str, Any] = Field(default_factory=dict)


class SimulationFixture(BaseModel):
    fixture_id: str = Field(default_factory=lambda: str(uuid4()))
    organization_id: str | None = None
    agent_id: str
    name: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    default_channel: Channel = "web_chat"
    default_modality: Modality = "text"
    starting_step_id: str | None = None
    starting_scenario_id: str | None = None
    seed_facts: dict[str, Any] = Field(default_factory=dict)
    turns: list[SimulationTurnInput] = Field(default_factory=list)
    assertions: list[SimulationAssertion] = Field(default_factory=list)
    is_active: bool = True
    gate_required: bool = True
    folder_path: str | None = Field(default=None, max_length=512)
    created_by_user_id: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @field_validator("folder_path", mode="before")
    @classmethod
    def _normalize_folder_path(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip("/")
        if not v:
            return None
        for part in v.split("/"):
            if not part or part in (".", "..") or "\x00" in part:
                raise ValueError(f"Invalid folder segment: {part!r}")
        return v


class FixtureValidationIssue(BaseModel):
    severity: AssertionSeverity = "warning"
    code: str
    message: str
    fixture_id: str
    assertion_id: str | None = None
    turn_id: str | None = None


FixtureReferenceIssue = FixtureValidationIssue


class AssertionResult(BaseModel):
    assertion_result_id: str = Field(default_factory=lambda: str(uuid4()))
    fixture_assertion_id: str | None = None
    kind: SimulationAssertionKind
    severity: AssertionSeverity
    passed: bool
    expected: dict[str, Any] = Field(default_factory=dict)
    actual: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class EvaluationCaseResult(BaseModel):
    case_result_id: str = Field(default_factory=lambda: str(uuid4()))
    evaluation_run_id: str
    fixture_id: str | None = None
    fixture_name: str
    conversation_id: str
    status: EvaluationCaseStatus
    final_step_id: str
    turn_count: int
    assertions_passed: int = 0
    assertions_failed: int = 0
    blocker_failures: int = 0
    warning_failures: int = 0
    duration_ms: int | None = None
    failure_summary: str | None = None
    actual_facts: dict[str, Any] = Field(default_factory=dict)
    assertion_results: list[AssertionResult] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None


class EvaluationRun(BaseModel):
    evaluation_run_id: str = Field(default_factory=lambda: str(uuid4()))
    organization_id: str | None = None
    agent_id: str
    agent_version_id: str
    mode: EvaluationRunMode
    source: EvaluationRunSource
    status: EvaluationRunStatus
    gate_eligible: bool = False
    fixture_count: int = 0
    passed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    pass_rate_ratio: float | None = None
    triggered_by_user_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    error_message: str | None = None
    qualified_at: datetime | None = None
    results: list[EvaluationCaseResult] = Field(default_factory=list)


class SimulationReplay(BaseModel):
    conversation: ConversationState
    start: RuntimeTurnResult
    turns: list[RuntimeTurnResult]
    traces: list[TurnTrace] = Field(default_factory=list)
    tool_invocations: list[ToolInvocation] = Field(default_factory=list)
    final_step_id: str
    final_facts: dict[str, Any] = Field(default_factory=dict)
    source: SimulationSource
    starting_step_id: str | None = None
    starting_scenario_id: str | None = None
    seed_facts: dict[str, Any] = Field(default_factory=dict)


class EvaluationCaseReview(BaseModel):
    run: EvaluationRun
    case_result: EvaluationCaseResult
    conversation: ConversationState
    traces: list[TurnTrace] = Field(default_factory=list)
    tool_invocations: list[ToolInvocation] = Field(default_factory=list)


class EvaluationRuntimeStatus(BaseModel):
    max_workers: int = 0
    queued_runs: int = 0
    running_runs: int = 0
    completed_runs: int = 0
    failed_runs: int = 0
    last_error: str | None = None
    active_run_ids: list[str] = Field(default_factory=list)
