from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal, Union
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from .attachments.models import AttachmentRef
from .rules import RuntimeRulesTrace


Channel = Literal["phone", "whatsapp", "web_chat", "web_widget", "browser"]
Modality = Literal["text", "audio", "image", "file", "mixed", "event"]
AgentVersionStatus = Literal["draft", "published"]
ConversationMode = Literal["live", "simulation"]
ConversationStatus = Literal["active", "ended"]
ConversationOutcome = Literal[
    "resolved",
    "transferred",
    "abandoned",
    "failed",
    "voicemail",
    "callback_scheduled",
    "follow_up_required",
]
RuntimeTurnEventType = Literal[
    "user_message",
    "user_final_transcript",
    "user_partial_transcript",
    "timeout",
    "no_input",
    "upload_success",
    "upload_failed",
    "tool_callback",
    "system_event",
]
SimulationSource = Literal["interactive", "replay", "evaluation"]
ToolMode = Literal["allowed", "blocked", "required", "optional"]
ToolInvocationStrategy = Literal[
    "always",
    "never",
    "on_missing_context",
    "on_low_confidence",
    "latency_bounded",
]
ActionType = Literal["reply", "ask_missing", "run_tool", "handoff", "end", "transition", "stay"]
# Internal marker for valid Condition kinds. Exposed for serialization
# debug; the canonical surface is the ``Condition`` discriminated union
# below. ``"event"`` and per-step ``event_hints`` were removed during the
# edge-owned outcomes migration — workflow routing now lives on the
# transition's ``OutcomeCondition``.
ConditionKind = Literal[
    "outcome",
    "fact_present",
    "fact_equals",
    "fact_missing",
    "all_required_facts_present",
    "guard_failure",
    "tool_outcome",
    "attachment_present",
    "view_ready",
    "otherwise",
]
# Stable token format for OutcomeCondition.event. Slug-like. Anchored so
# legacy ``intent_detected:foo`` / ``foo:bar`` shapes are rejected at parse
# time.
_OUTCOME_EVENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,60}$")
ValidationSeverity = Literal["error", "warning"]
ErrorKind = Literal[
    "none",
    "llm_error",
    "llm_timeout",
    "llm_rate_limited",
    "tool_timeout",
    "tool_rate_limited",
    "tool_error",
    "guard_rejected",
    "validation_error",
    "runtime_conflict",
    "ingest_duplicate",
    "kernel_panic",
]


class FactStoragePolicy(BaseModel):
    scope: Literal["turn", "conversation", "workflow", "tool_context", "audit_only"] = "conversation"
    retention: Literal["ephemeral", "conversation", "workflow", "audit_90d", "do_not_store"] = "conversation"
    sensitivity: Literal["public", "personal", "confidential", "secret"] = "personal"
    expose_to_narration: bool = True
    allow_tool_use: bool = True
    audit_raw_policy: Literal["hash", "plaintext_if_enabled", "redact"] = "hash"

    @model_validator(mode="after")
    def validate_policy(self) -> "FactStoragePolicy":
        if self.sensitivity == "secret":
            self.audit_raw_policy = "redact"
        return self


class ArbitrationRule(BaseModel):
    kind: Literal[
        "prefer_user_confirmed",
        "prefer_authoritative_tool",
        "prefer_exact_validator",
        "prefer_classifier_over_llm",
        "prefer_highest_confidence",
        "prefer_latest",
        "require_confirmation_on_disagreement",
    ]
    config: dict[str, Any] = Field(default_factory=dict)


class FactDef(BaseModel):
    name: str
    type: str
    required: bool = False
    source_policy: Literal[
        "deterministic_only",
        "deterministic_first",
        "model_allowed",
    ] = "deterministic_first"
    confidence_threshold: float = 0.8
    conflict_policy: Literal[
        "prefer_deterministic",
        "prefer_latest_high_confidence",
        "require_confirmation",
    ] = "prefer_deterministic"
    storage_policy: FactStoragePolicy = Field(default_factory=FactStoragePolicy)
    allowed_sources: set[Literal[
        "deterministic",
        "classifier",
        "extractor",
        "tool",
        "llm_proposed",
        "user_confirmed",
        "system",
    ]] = Field(
        default_factory=lambda: {
            "deterministic",
            "classifier",
            "tool",
            "llm_proposed",
            "user_confirmed",
            "system",
        }
    )
    arbitration_rules: list[ArbitrationRule] = Field(
        default_factory=lambda: [
            ArbitrationRule(kind="prefer_user_confirmed"),
            ArbitrationRule(kind="prefer_authoritative_tool"),
            ArbitrationRule(kind="prefer_exact_validator"),
            ArbitrationRule(kind="prefer_classifier_over_llm"),
            ArbitrationRule(kind="prefer_highest_confidence"),
        ]
    )
    capture_aliases: list[str] = Field(default_factory=list)
    entity_hints: list[str] = Field(default_factory=list)
    pattern: str | None = None
    validator_config: dict[str, Any] = Field(default_factory=dict)
    llm_confidence_default: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def compile_capture_policy(self) -> "FactDef":
        if self.source_policy == "deterministic_only":
            self.allowed_sources = {"deterministic", "user_confirmed", "system"}
        elif self.source_policy == "model_allowed":
            self.allowed_sources.add("llm_proposed")
        if self.storage_policy.sensitivity == "secret":
            self.storage_policy.audit_raw_policy = "redact"
            self.allowed_sources.discard("llm_proposed")
        return self


class ToolBinding(BaseModel):
    ref: str
    mode: ToolMode = "optional"
    invocation_strategy: ToolInvocationStrategy = "never"
    timeout_ms: int | None = None
    event_name: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)


class GuardDef(BaseModel):
    kind: Literal["channel_allowed", "fact_required"]
    value: str
    description: str | None = None


GroundingMode = Literal["off", "preferred", "required"]


class KnowledgeGroundingPolicy(BaseModel):
    """Per-step grounding contract for the dialogue generator.

    Implements Google Vertex AI's documented grounding pattern (see
    docs.cloud.google.com/vertex-ai/generative-ai/docs/grounding/overview)
    so steps that depend on knowledge retrieval cannot fabricate when
    retrieval fails or returns weak results:

    1. **Pre-call retrieval gate** — only call the LLM if the top hit's
       normalized relevance score ≥ ``min_relevance`` (Google's
       ``dynamic_retrieval_threshold`` analog; default 0.7).
    2. **Strict-grounded system instruction** — when grounding is active,
       prefix the system prompt with Google's verbatim Gemini-3 strict
       instruction so the model is told to use *only* the provided
       context.
    3. **Post-call grounding check** — score the rendered text against
       retrieved chunks; reject below ``min_grounding_score`` (Google's
       ``citation_threshold`` analog; default 0.6).

    The default (``mode="off"``) preserves today's behavior for every
    step that hasn't opted in — non-breaking for existing agents.
    """

    mode: GroundingMode = "off"
    """Grounding posture for this step.

    - ``off``: no grounding gate (current behavior).
    - ``preferred``: use grounding when retrieval is above threshold;
      when below, drop to deterministic reply (no LLM call).
    - ``required``: same as ``preferred`` but the deterministic reply
      is mandatory whenever grounding fails — the LLM never sees an
      ungrounded prompt for this step.
    """

    min_relevance: float = Field(0.7, ge=0.0, le=1.0)
    """Pre-call threshold — Google's ``dynamic_retrieval_threshold``
    analog. The top retrieval hit's normalized score (0–1) must meet
    this for the LLM call to proceed."""

    min_grounding_score: float = Field(0.6, ge=0.0, le=1.0)
    """Post-call threshold — Google's ``citation_threshold`` analog.
    The rendered text's grounding-overlap score against retrieved
    chunks must meet this; below it, the gate fires and the
    deterministic fallback is emitted."""

    post_call_check: Literal["off", "heuristic", "llm"] = "heuristic"
    """How the post-call grounding score is computed.

    - ``off``: skip the post-call check.
    - ``heuristic``: token-overlap between rendered text and retrieved
      chunks (zero-latency, deterministic).
    - ``llm``: secondary LLM grading call (higher accuracy, doubles
      latency — reserved for compliance-sensitive deployments).
    """

    strict_system_instruction: bool = True
    """Inject Google's verbatim Gemini-3 strict-grounded preamble into
    the system prompt when ``mode != "off"``. Authors should leave this
    on; turn it off only for steps where the LLM legitimately needs to
    paraphrase or summarize beyond the retrieved facts."""

    deterministic_fallback_text: str | None = None
    """Per-grounding deterministic fallback. Resolution order:
    ``knowledge_grounding.deterministic_fallback_text`` →
    ``ResponsePolicy.deterministic_fallback_text`` → built-in safe text.
    """


class RetrievalChunk(BaseModel):
    """One retrieved knowledge chunk plumbed through to the renderer.

    Carried on ``RenderContext.retrieval_evidence`` so the dialogue
    generator can (a) include the chunks in the strict-grounded user
    context block, (b) compute the post-call grounding score against
    them.
    """

    text: str
    document_id: str
    chunk_id: str
    title: str | None = None
    score: float
    normalized_score: float | None = None


class ResponsePolicy(BaseModel):
    answer_directly_first: bool = True
    ask_clarifying_question_only_if_needed: bool = True
    voice_style: Literal["concise", "balanced", "detailed"] = "concise"
    direct_answer_prompt: str | None = None
    # Phase 1 rendering controls (doc 10 / doc 18)
    render_with_llm: bool = True
    deterministic_fallback_text: str | None = None
    response_max_sentences: int | None = None
    include_recent_history: bool = True
    include_known_facts: bool = True
    knowledge_grounding: KnowledgeGroundingPolicy = Field(
        default_factory=KnowledgeGroundingPolicy,
    )


class OutcomeCondition(BaseModel):
    """LLM-evaluated workflow-routing condition (replaces the legacy
    ``event`` + ``intent_detected:*`` two-stage indirection).

    ``event`` is the stable analytics/training/trace token (e.g.
    ``pricing_question``, ``info_confirmed``). It is the same string the
    classifier picks from the per-step catalog; renaming it invalidates
    historical traces, evals, and any LoRA trained on the old label.

    ``description`` is what the LLM sees as the natural-language meaning of
    this branch — author writes prose, not field references.
    """

    kind: Literal["outcome"] = "outcome"
    event: str = Field(min_length=3, max_length=60)
    description: str = Field(min_length=8)

    @model_validator(mode="after")
    def validate_outcome(self) -> "OutcomeCondition":
        if not _OUTCOME_EVENT_PATTERN.match(self.event):
            raise ValueError(
                f"OutcomeCondition.event {self.event!r} must match "
                f"{_OUTCOME_EVENT_PATTERN.pattern} (slug-like; no namespacing colons)"
            )
        return self


class FactPresentCondition(BaseModel):
    kind: Literal["fact_present"] = "fact_present"
    fact_name: str = Field(min_length=1)


class FactEqualsCondition(BaseModel):
    """Deterministic equality check on a recorded fact value.

    ``expected`` is intentionally untyped — fact values are JSON-shaped and
    compared with stdlib ``==`` semantics. Validators on the fact schema
    enforce typing upstream of routing.
    """

    kind: Literal["fact_equals"] = "fact_equals"
    fact_name: str = Field(min_length=1)
    expected: Any


class FactMissingCondition(BaseModel):
    kind: Literal["fact_missing"] = "fact_missing"
    fact_name: str = Field(min_length=1)


class AllRequiredFactsPresentCondition(BaseModel):
    kind: Literal["all_required_facts_present"] = "all_required_facts_present"


class GuardFailureCondition(BaseModel):
    kind: Literal["guard_failure"] = "guard_failure"
    guard_id: str = Field(min_length=1)


class ToolOutcomeCondition(BaseModel):
    """Deterministic transition fired by the kernel after a tool returns.

    ``tool_ref`` selects which pending tool's outcome this edge consumes.
    ``None`` is allowed only when the owning step has at most one tool
    binding (validated at the ``Step`` level so the dispatch is
    unambiguous); otherwise the author must name the tool explicitly.

    ``outcome`` is a slug-shaped string. The kernel emits four canonical
    outcomes for built-in tool execution (``success``, ``failure``,
    ``timeout``, ``blocked``); authors with ``action_config.code``
    blocks emit custom domain codes (``action_code_approved_refund``,
    ``action_code_doc_required``, …) that the kernel forwards through
    the ``tool_outcome:<value>`` event key. Both shapes are valid here.
    """

    kind: Literal["tool_outcome"] = "tool_outcome"
    tool_ref: str | None = None
    outcome: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9_]+$")


class AttachmentPresentCondition(BaseModel):
    kind: Literal["attachment_present"] = "attachment_present"
    any_of_kinds: list[str] | None = None
    all_of_kinds: list[str] | None = None


class ViewReadyCondition(BaseModel):
    kind: Literal["view_ready"] = "view_ready"
    view_kind: str = Field(min_length=1)
    any_of_kinds: list[str] | None = None
    all_of_kinds: list[str] | None = None


class OtherwiseCondition(BaseModel):
    """Terminal fallback. At most one per Step (validated on Step)."""

    kind: Literal["otherwise"] = "otherwise"


# Discriminated by ``kind``. Pydantic resolves the right model at parse
# time, so callers always see a fully-typed instance.
Condition = Annotated[
    Union[
        OutcomeCondition,
        FactPresentCondition,
        FactEqualsCondition,
        FactMissingCondition,
        AllRequiredFactsPresentCondition,
        GuardFailureCondition,
        ToolOutcomeCondition,
        AttachmentPresentCondition,
        ViewReadyCondition,
        OtherwiseCondition,
    ],
    Field(discriminator="kind"),
]


class Transition(BaseModel):
    id: str
    when: Condition
    to: str
    reason_code: str = ''
    natural_reason: str | None = None
    when_to_use: str | None = None
    priority: int = 100
    # Spec 25 §Transition Editor Changes — author-facing metadata for
    # narrative intent of this branch.  Does not replace the runtime
    # condition.  Simulator/renderer may use it as a hint when the branch
    # fires; kernel does not alter control flow based on it.
    branch_intent: Literal[
        "continue",
        "confirm",
        "ask_again",
        "repair",
        "block",
        "escalate",
    ] | None = None

class ArtifactFollowupHandler(BaseModel):
    """Routes a follow-up intent on an artifact type to a target step.

    Doc 20/21: `artifact_followup_handlers` is the single source of truth for
    artifact follow-up routing. Handler selects the target step; the
    target step owns the behavior.
    """

    artifact_type: str
    followup_intent: str
    target_step_id: str
    fact_requirements: list[FactRequirement] = Field(default_factory=list)


class FieldCaptureConfig(BaseModel):
    """Declares a fact to extract from an attachment view text payload.

    Used in exported workflow-step attachment metadata. When a ``view_ready`` system_event
    fires and the current step has capture configs, the kernel extracts the
    declared facts from the attachment's ``inline_text`` and stores them in
    the conversation's working facts.

    Fields
    ------
    fact:
        The fact name to write (must exist in the agent's ``fact_schema``).
    hint:
        Optional extraction hint sent to the LLM extractor (e.g. "Full
        legal name of the applicant").  Ignored for deterministic fields.
    required:
        If True, the kernel emits a warning log when the field is absent.
        Does not block extraction or transition.
    """

    fact: str
    hint: str | None = None
    required: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Move-selection schemas (specs 30 / 31 / 33 / 34, WI-1 of doc 36).
#
# These types describe LLM-selected moves and runtime validation outcomes.
# In P1 they are schema-only — runtime stages remain stubbed and gated behind
# the master flag (see WI-3, WI-4).  Production behavior is unchanged until
# move selection is enabled per-agent and per-step in P2+.
# ─────────────────────────────────────────────────────────────────────────────


MOVES_PER_TURN_MAX = 3
"""Hard cap on moves in a single ``MoveSequence`` (spec 31)."""


class MoveType(StrEnum):
    """Conversational moves the LLM may select (spec 31, doc 34 §142-155).

    The first 9 are the original move vocabulary from doc 31.  The last 3 are
    the narrowed social-register additions from doc 35 / doc 34 §152-154.
    """

    ANSWER = "answer"
    CLARIFY = "clarify"
    ACKNOWLEDGE = "acknowledge"
    PAUSE = "pause"
    REPAIR = "repair"
    SMALLTALK_AND_RETURN = "smalltalk_and_return"
    ASK_FOR_MISSING_INFO = "ask_for_missing_info"
    PROPOSE_TRANSITION = "propose_transition"
    PROPOSE_TOOL_USE = "propose_tool_use"
    APOLOGIZE = "apologize"
    THANK = "thank"
    CONFIRM_UNDERSTANDING = "confirm_understanding"


_STRUCTURAL_COMMIT_MOVES: frozenset[MoveType] = frozenset(
    {MoveType.PROPOSE_TRANSITION, MoveType.PROPOSE_TOOL_USE}
)
"""Moves that, if accepted, commit a structural change (state or tool)."""


class TransitionReasonCode(StrEnum):
    """Bounded vocabulary for ``ProposedTransition.reason_code`` (doc 29)."""

    USER_PROVIDED_REQUESTED_FACT = "user_provided_requested_fact"
    USER_CHANGED_TOPIC = "user_changed_topic"
    USER_REQUESTED_HELP = "user_requested_help"
    USER_INDICATED_COMPLETION = "user_indicated_completion"
    USER_CORRECTED_ASSISTANT = "user_corrected_assistant"
    USER_REFERENCED_ARTIFACT = "user_referenced_artifact"
    RECOVERY_FROM_LOOP = "recovery_from_loop"


class ProposedTransition(BaseModel):
    """Structured transition proposal returned by the LLM (doc 29 / doc 31)."""

    target_step_id: str
    reason_code: TransitionReasonCode
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    triggering_signals: list[str] = Field(default_factory=list)


class TransitionProposalPolicy(BaseModel):
    """Per-step policy bounding LLM transition proposals (doc 31)."""

    enabled: bool = True
    additional_targets: list[str] = Field(default_factory=list)
    blocked_targets: list[str] = Field(default_factory=list)
    require_recovery_signal: bool = False
    proposal_minimum_confidence: float = Field(default=0.7, ge=0.0, le=1.0)


class MoveSelection(BaseModel):
    """A single LLM-selected move for the current turn (spec 31)."""

    move_type: MoveType
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    extracted_facts: dict[str, Any] = Field(default_factory=dict)
    proposed_transition: ProposedTransition | None = None
    target_tool_name: str | None = None
    response_plan: str | None = None
    references: dict[str, Any] = Field(default_factory=dict)


class MoveSequence(BaseModel):
    """Multi-move turn (doc 35 / doc 34 §199-203).

    Up to ``MOVES_PER_TURN_MAX`` moves rendered as one user-visible utterance.
    Validation: at most one structural commit move (``propose_transition`` or
    ``propose_tool_use``) per sequence — the runtime accepts the first valid
    structural commit and rejects any later ones (doc 34 §559-562).
    """

    moves: list[MoveSelection] = Field(min_length=1, max_length=MOVES_PER_TURN_MAX)
    combined_response_plan: str
    sequence_rationale: str

    @model_validator(mode="after")
    def validate_sequence(self) -> "MoveSequence":
        structural_count = sum(
            1 for m in self.moves if m.move_type in _STRUCTURAL_COMMIT_MOVES
        )
        if structural_count > 1:
            raise ValueError(
                "MoveSequence may contain at most one structural commit move "
                "(propose_transition or propose_tool_use); "
                f"got {structural_count}"
            )
        return self


class ValidationOutcome(StrEnum):
    """Outcome of runtime validation against an LLM move (spec 31)."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ACCEPTED_WITH_FALLBACK = "accepted_with_fallback"


class ValidationResult(BaseModel):
    """Runtime decision on a selected move (spec 31)."""

    outcome: ValidationOutcome
    committed_move_type: MoveType
    rejection_reason: str | None = None
    committed_step_id: str | None = None
    committed_tool_name: str | None = None
    accepted_facts: dict[str, Any] = Field(default_factory=dict)
    response_constraints: dict[str, Any] = Field(default_factory=dict)


class MoveSelectionPolicy(BaseModel):
    """Per-step opt-in and tuning for LLM move selection (doc 33)."""

    enabled: bool = False
    allowed_move_types: list[MoveType] = Field(default_factory=list)
    latency_mode: Literal["auto", "llm_always", "deterministic_preferred"] = "auto"
    short_circuit_policy: Literal[
        "aggressive", "latency_only", "disabled"
    ] = "latency_only"


class MoveSelectionDefaults(BaseModel):
    """Per-agent overrides for kernel-level move-selection thresholds.

    P6 of doc 45 (WI-3).  Promotes the previously hard-coded
    per-step-type loop thresholds (and future tuning knobs) to a
    agent-scoped block so individual products can tune without forking
    the runtime.

    Resolution order (highest precedence first):

      1. ``step.move_selection_policy.<field>`` (per-step — when added)
      2. ``agent.experimental_runtime_policy.move_selection_defaults.<field>``
      3. Kernel constants (per-step-capability profile)

    All fields are optional; absent fields fall through to the kernel
    constants defined in ``ruhu.kernel``.
    """

    loop_threshold_by_step_profile: dict[str, int] | None = None


class ExperimentalRuntimePolicy(BaseModel):
    """Per-agent opt-in for experimental runtime features (doc 33)."""

    llm_move_selection_enabled: bool = False
    move_selection_defaults: MoveSelectionDefaults | None = None


class ToolOutcomeRecord(BaseModel):
    """LLM-safe record of a recent tool invocation outcome (doc 35 / doc 34 §237-246).

    ``output_summary`` is required even when ``output_data`` is suppressed for
    PII or size — the summary is what the LLM uses for natural rendering.
    """

    tool_name: str
    invocation_id: str
    invoked_at: datetime
    completed_at: datetime | None = None
    status: Literal["pending", "success", "failed", "partial", "timeout"]
    output_summary: str
    output_data: dict[str, Any] = Field(default_factory=dict)
    error_kind: str | None = None
    pii_redacted: bool = False


class ProactiveTrigger(StrEnum):
    """Runtime-dispatched triggers for proactive move selection (doc 35).

    V1 narrows to pending-action lifecycle only; long-silence and scheduled
    triggers are deferred to a later phase (doc 33 P4+).
    """

    PENDING_ACTION_PROGRESS = "pending_action_progress"
    PENDING_ACTION_SLOW = "pending_action_slow"
    PENDING_ACTION_COMPLETE = "pending_action_complete"
    PENDING_ACTION_FAILED = "pending_action_failed"


class MoveSelectionOutput(BaseModel):
    """Top-level structured output from the move-selection LLM call (doc 34 §283-286).

    Exactly one of ``selection`` or ``sequence`` must be set.  This is the
    boundary type between the response-generation parser and the kernel
    validation pipeline.
    """

    selection: MoveSelection | None = None
    sequence: MoveSequence | None = None

    @model_validator(mode="after")
    def validate_xor(self) -> "MoveSelectionOutput":
        has_selection = self.selection is not None
        has_sequence = self.sequence is not None
        if has_selection == has_sequence:
            raise ValueError(
                "MoveSelectionOutput must set exactly one of "
                "'selection' or 'sequence' (XOR)"
            )
        return self


class MoveSelectionReplayRecord(BaseModel):
    """Recorded fixture for move-selection replay (doc 36 WI-6).

    P1 holds the schema and fixture format only — the kernel's recording hook
    is wired but inert (no production call site invokes it).  P2+ uses these
    records to drive deterministic replay of LLM move selection in CI without
    requiring a live LLM.
    """

    turn_id: str
    input_context_hash: str
    move_selection_output: MoveSelectionOutput
    validation_result: ValidationResult | None = None
    committed_deltas: dict[str, Any] = Field(default_factory=dict)


class MoveSelectionContext(BaseModel):
    """Decision surface passed to the move-selection LLM call (doc 34).

    P1 holds a minimal but typed shape so the kernel stub from WI-4 can return
    a concrete object instead of an implicit dict.  P2+ will populate richer
    fields (artifacts, repair, persona, etc.) as the call site goes live.
    """

    current_step_id: str
    current_step_name: str = ""
    current_step_capabilities: "StepCapabilities" = Field(
        default_factory=lambda: StepCapabilities()
    )
    current_step_goal: str
    current_user_text: str = ""
    allowed_move_types: list[MoveType] = Field(default_factory=list)
    transition_targets: list[str] = Field(default_factory=list)
    transition_target_summaries: dict[str, str] = Field(default_factory=dict)
    event_hints: dict[str, str] = Field(default_factory=dict)
    tool_affordances: list[str] = Field(default_factory=list)
    required_execution_facts: list[str] = Field(default_factory=list)
    accepted_facts: dict[str, Any] = Field(default_factory=dict)
    missing_facts: list[str] = Field(default_factory=list)
    recent_tool_outcomes: list[ToolOutcomeRecord] = Field(default_factory=list)
    pending_action_summary: str | None = None
    recent_turn_summaries: list[str] = Field(default_factory=list)
    repair_context_summary: str | None = None
    policy_constraints: dict[str, Any] = Field(default_factory=dict)
    journey_context: JourneyContext | None = None


class AuthoredStepGuidance(BaseModel):
    say_on_entry: str | None = None
    say_on_transition: str | None = None
    ask_for_fact: str | None = None
    repair_response: str | None = None


class FactRequirement(BaseModel):
    name: str
    purpose: str | None = None


class PendingFactContext(BaseModel):
    purpose: str
    triggered_by: str | None = None
    triggered_in_step: str | None = None
    ask_for_fact: str | None = None


class AgentCapabilityManifest(BaseModel):
    assistant_identity: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class StepCapabilities(BaseModel):
    collects_missing_details: bool = False
    uses_tooling: bool = False
    hands_off: bool = False
    completes: bool = False


class RouteBranch(BaseModel):
    target_step_id: str
    target_step_capabilities: StepCapabilities = Field(default_factory=StepCapabilities)
    target_step_name: str | None = None
    target_step_summary: str | None = None
    branch_reason_code: str | None = None
    branch_natural_reason: str | None = None
    branch_when_to_use: str | None = None
    required_fact_names: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)


class TransitionNarrative(BaseModel):
    from_step_id: str | None = None
    to_step_id: str
    reason_code: str | None = None
    transition_intent: str | None = None
    natural_reason: str | None = None
    bridge_required: bool = True


class JourneyContext(BaseModel):
    conversation_phase: str | None = None
    current_step_id: str
    current_step_capabilities: StepCapabilities = Field(default_factory=StepCapabilities)
    current_step_name: str | None = None
    current_step_purpose: str | None = None
    previous_step_id: str | None = None
    previous_step_name: str | None = None
    transition_reason_code: str | None = None
    transition_intent: str | None = None
    transition_natural_reason: str | None = None
    journey_summary: str | None = None
    current_user_text: str | None = None
    topic_freshness: Literal["same_topic", "topic_shift", "mixed", "unknown"] = "unknown"
    pending_facts: dict[str, PendingFactContext] = Field(default_factory=dict)
    pending_action_summary: str | None = None
    recent_tool_outcomes: list[ToolOutcomeRecord] = Field(default_factory=list)
    route_horizon: list[RouteBranch] = Field(default_factory=list)
    authored_guidance: AuthoredStepGuidance | None = None
    agent_capability_manifest: AgentCapabilityManifest | None = None


# ─────────────────────────────────────────────────────────────────────────────


class RuntimeTurn(BaseModel):
    turn_id: str
    dedupe_key: str
    channel: Channel
    modality: Modality
    event_type: RuntimeTurnEventType
    text: str | None = None
    # Attachment refs carried by this turn (e.g. view_ready system_event).
    # Populated by the view-ready worker so the kernel can access inline_text
    # without a second DB read.
    attachments: list[AttachmentRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    received_at: datetime

    @model_validator(mode="after")
    def validate_turn(self) -> "RuntimeTurn":
        if self.event_type == "upload_success" and not self.attachments:
            raise ValueError(
                "upload_success turn must carry at least one attachment"
            )
        return self


class SimulationTurnInput(BaseModel):
    turn_id: str | None = None
    dedupe_key: str | None = None
    event_type: RuntimeTurnEventType = "user_message"
    modality: Modality = "text"
    text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SemanticEventRecord(BaseModel):
    family: str
    name: str
    source: Literal[
        "deterministic",
        "classifier",
        "extractor",
        "tool",
        "guard",
        "system",
        "llm_proposed",
        "user_confirmed",
    ]
    confidence: float | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.family}:{self.name}"


class FactUpdate(BaseModel):
    name: str
    value: Any
    source: Literal[
        "deterministic",
        "classifier",
        "extractor",
        "tool",
        "user_confirmed",
        "system",
        # P2 of doc 33: facts proposed by the LLM move-selection call.
        # Treated as low-priority (deterministic facts dominate on conflict).
        "llm_proposed",
    ]
    confidence: float | None = None
    raw_value: Any | None = None
    evidence: str | None = None
    source_ref: str | None = None
    outcome: Literal["accepted", "stored_audit_only", "stored_audit_only_redacted"] | None = None
    turn_id: str | None = None
    replaced_previous: bool = False


class PendingFactUpdate(BaseModel):
    pending_id: str
    name: str
    proposed_value: Any
    raw_value: Any | None = None
    source: Literal[
        "deterministic",
        "classifier",
        "extractor",
        "tool",
        "user_confirmed",
        "system",
        "llm_proposed",
    ]
    confidence: float | None = None
    evidence: str | None = None
    source_ref: str | None = None
    reason: Literal["below_threshold", "conflict_requires_confirmation"]
    previous_value: Any | None = None
    previous_metadata: dict[str, Any] | None = None
    audit_row_id: str | None = None
    status: Literal["pending", "confirmed", "rejected", "expired"] = "pending"
    turn_id: str
    expires_at: str | None = None


class ToolCallRecord(BaseModel):
    invocation_id: str | None = None
    tool_ref: str
    status: Literal[
        "requested",
        "running",
        "confirmation_required",
        "success",
        "blocked",
        "timeout",
        "error",
        "cancelled",
    ]
    reason: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ActionRecord(BaseModel):
    type: ActionType
    reason: str
    payload: dict[str, Any] = Field(default_factory=dict)


class RenderedMessage(BaseModel):
    role: Literal["assistant", "system"] = "assistant"
    text: str
    message_type: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class GuardResultRecord(BaseModel):
    guard_kind: str
    guard_value: str
    passed: bool
    reason: str | None = None


class ModelOutputRecord(BaseModel):
    stage: str
    provider: str | None = None
    model: str | None = None
    model_version: str | None = None
    temperature: float | None = None
    seed: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    error: str | None = None


class NormalizedObservationRecord(BaseModel):
    channel: str = ""
    modality: str = ""
    event_type: str = ""
    text_present: bool = False
    redacted_text: str | None = None
    attachment_ids: list[str] = Field(default_factory=list)
    metadata_summary: dict[str, Any] = Field(default_factory=dict)


class InteractionDebugVoicePolicy(BaseModel):
    step_id: str
    endpointing_ms: int
    soft_timeout_ms: int
    turn_eagerness: Literal["low", "normal", "high"]
    interruptibility_policy: Literal[
        "always_interruptible",
        "interruptible_except_policy",
        "non_interruptible",
    ]


class InteractionDebugPendingAction(BaseModel):
    action_id: str
    action_type: str
    status: str
    action_label: str | None = None
    target_ref: str | None = None


class InteractionDebugPendingPermission(BaseModel):
    request_id: str
    permission_kind: str
    status: str
    target_ref: str | None = None


class InteractionDebugActiveRepair(BaseModel):
    repair_kind: str
    target_ref: str | None = None
    summary: str | None = None


class InteractionDebugSnapshot(BaseModel):
    step_id: str
    channel: str
    voice_interaction_policy: InteractionDebugVoicePolicy
    pending_action: InteractionDebugPendingAction | None = None
    pending_permission: InteractionDebugPendingPermission | None = None
    active_repair: InteractionDebugActiveRepair | None = None


class RuntimeTurnResult(BaseModel):
    turn_id: str
    conversation_id: str
    step_before: str
    step_after: str
    semantic_events: list[SemanticEventRecord] = Field(default_factory=list)
    fact_updates: list[FactUpdate] = Field(default_factory=list)
    chosen_action: ActionRecord
    emitted_messages: list[RenderedMessage] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    rules: RuntimeRulesTrace = Field(default_factory=RuntimeRulesTrace)
    trace_id: str
    latency_breakdown_ms: dict[str, int] = Field(default_factory=dict)
    interaction_debug_snapshot: InteractionDebugSnapshot | None = None


class TurnDecisionObservability(BaseModel):
    controller_of_record: str | None = None
    intent_source: str | None = None
    fallback_used: bool = False
    fallback_reason: str | None = None
    degraded_mode: str | None = None


# ── Response context (LLM rendering contract) ────────────────────────────────

ResponseMode = Literal[
    "entry",
    "ask_missing_fact",
    "answer_question",
    "transition_bridge",
    "status_explanation",
    "confirm_success",
    "explain_failure",
    "tool_error_fallback",
    "tool_confirmation_required",
    "handoff",
    "close",
    "clarify",
    "acknowledge",
    "activity_started",
    "activity_completed",
    "activity_failed",
    "interrupt_acknowledged",
    "repair_response",
    "policy_blocked",
    "completion_uncertain",
]

NarrationMode = Literal[
    "templated",
    "llm_bridged",
    "llm_only",
]

DetectedControlIntent = Literal[
    "none",
    "confirm",
    "cancel",
    "ask_status",
    "ask_repeat",
    "ask_clarification",
    "provide_requested_value",
    "interrupt",
    "topic_shift",
    "unclear",
]

RuntimeActivityStatus = Literal[
    "idle",
    "waiting_for_user",
    "waiting_for_confirmation",
    "running",
    "slow",
    "completed",
    "failed",
    "cancelled",
]


class DialogueMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    text: str


class RecentDialogueMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    text: str
    timestamp: datetime | None = None


class ActionOutcomeSummary(BaseModel):
    status: Literal["none", "success", "failure", "partial", "confirmation_required"] = "none"
    action_type: str | None = None
    tool_ref: str | None = None
    summary: str | None = None
    user_visible_fields: dict[str, Any] = Field(default_factory=dict)
    retryable: bool | None = None


class ResponseConstraintSet(BaseModel):
    must_mention: list[str] = Field(default_factory=list)
    must_not_claim: list[str] = Field(default_factory=list)
    do_not_ask_for: list[str] = Field(default_factory=list)
    response_max_sentences: int | None = None


RenderClaimClass = Literal[
    "success",
    "partial",
    "pending",
    "uncertain",
    "failure",
    "repair",
    "policy",
]


class PendingActionSummary(BaseModel):
    action_id: str
    action_type: str
    status: Literal[
        "confirmation_required",
        "queued",
        "starting",
        "running",
        "waiting_poll",
        "waiting_webhook",
        "retry_scheduled",
        "slow",
        "cancelling",
        "completed",
        "cancelled",
        "completion_uncertain",
        "failed",
    ]
    tool_ref: str | None = None
    action_label: str | None = None
    target_ref: str | None = None


class PendingPermissionSummary(BaseModel):
    request_id: str
    permission_kind: str
    status: Literal["waiting", "granted", "denied", "aborted", "expired"]
    target_ref: str | None = None


class ActiveRepairSummary(BaseModel):
    repair_kind: Literal[
        "repeat_acknowledgment",
        "interrupt_acknowledged",
        "late_result_reconciliation",
        "contradiction_repair",
        "no_progress_repair",
        "provider_uncertainty_repair",
    ]
    target_ref: str | None = None
    summary: str | None = None


class StatusTrailSummaryItem(BaseModel):
    item_type: Literal["activity", "permission", "repair"]
    summary: str
    source_ref: str | None = None


class RuntimeStepSummary(BaseModel):
    step_id: str
    step_capabilities: StepCapabilities = Field(default_factory=StepCapabilities)
    step_name: str
    step_goal: str
    step_purpose: str | None = None


class RuntimeControlSummary(BaseModel):
    pending_action: PendingActionSummary | None = None
    pending_permission: PendingPermissionSummary | None = None
    active_repair: ActiveRepairSummary | None = None
    policy_outcome: str | None = None
    runtime_activity_status: RuntimeActivityStatus = "idle"
    status_trail_summary: list[StatusTrailSummaryItem] = Field(default_factory=list)


class UserContractSummary(BaseModel):
    waiting_on: str
    allowed_user_moves: list[str] = Field(default_factory=list)


class TurnInterpretationSummary(BaseModel):
    detected_control_intent: DetectedControlIntent = "none"
    detected_domain_intent: str | None = None
    bridge_appropriate: bool = False


class NarrationContract(BaseModel):
    response_mode: ResponseMode
    narration_mode: NarrationMode
    should_respond: bool = True
    silence_is_correct: bool = False
    template_response: str | None = None
    must_acknowledge: list[str] = Field(default_factory=list)
    must_not_imply_completion: bool = False
    must_not_repeat_prompt: bool = False
    constraints: ResponseConstraintSet = Field(default_factory=ResponseConstraintSet)


class RuntimeProjection(BaseModel):
    step: RuntimeStepSummary
    control: RuntimeControlSummary
    user_contract: UserContractSummary
    recent_messages: list[RecentDialogueMessage] = Field(default_factory=list, max_length=5)


class ConversationRuntimeProjection(BaseModel):
    runtime: RuntimeProjection
    turn_interpretation: TurnInterpretationSummary
    narration: NarrationContract


class RenderContext(BaseModel):
    """Journey-first rendering context passed to the LLM renderer.

    The renderer consumes runtime truth plus ``JourneyContext``. It does not
    receive a state-local payload that then optionally embeds journey data.
    """

    conversation_id: str
    organization_id: str | None = None
    agent_id: str
    response_mode: ResponseMode
    journey: JourneyContext
    response_directive: str | None = None
    channel: str = "web_chat"
    fallback_text: str | None = None
    system_prompt: str | None = None
    voice_style: str | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    recent_messages: list[DialogueMessage] = Field(default_factory=list)
    latest_action_outcome: ActionOutcomeSummary = Field(
        default_factory=ActionOutcomeSummary,
    )
    pending_action_summary: dict[str, Any] | None = None
    pending_permission_summary: dict[str, Any] | None = None
    grounding_summary: dict[str, Any] = Field(default_factory=dict)
    commitment_summary: dict[str, Any] | None = None
    active_repair: dict[str, Any] | None = None
    policy_outcome: str | None = None
    status_trail_summary: list[dict[str, Any]] = Field(default_factory=list)
    allowed_claim_classes: list[RenderClaimClass] = Field(default_factory=list)
    narrator_mode: Literal["llm", "deterministic"] = "llm"
    latency_budget_ms: int | None = None
    transition_narrative: TransitionNarrative | None = None
    focused_artifact: dict[str, Any] | None = None
    active_artifact_count: int = 0
    constraints: ResponseConstraintSet = Field(default_factory=ResponseConstraintSet)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # ── Knowledge grounding (Google Vertex AI grounding pattern) ─────────
    # Populated by the kernel when the step's `response_policy.
    # knowledge_grounding.mode != "off"`. The renderer reads these to
    # build the strict-grounded User Context block + run the post-call
    # grounding-overlap check. Empty list + ``mode == "off"`` is the
    # legacy non-grounded path.
    retrieval_evidence: list[RetrievalChunk] = Field(default_factory=list)
    grounding_policy: KnowledgeGroundingPolicy | None = None
    retrieval_grade: Literal["pass", "weak", "fail", "absent"] = "absent"


class RenderOutput(BaseModel):
    """Canonical renderer output for context-driven response generation.

    This separates renderer phrasing from runtime truth validation:

    - `text` is the user-visible phrasing
    - `claimed_class` is checked against runtime commitment/claim rules
    - `acknowledged_fact_keys` updates grounding only after successful emission
    """

    text: str
    claimed_class: RenderClaimClass
    acknowledged_fact_keys: list[str] = Field(default_factory=list)


# ── Runtime control state (docs 17, 18, 19) ──────────────────────────────────


class ConversationArtifact(BaseModel):
    """Durable runtime object representing a real-world business object.

    Created by the runtime after successful actions. Persists across turns
    so follow-up requests ("cancel it") can target a concrete object.

    Design reference: docs/tooling-and-llm-redesign/19-artifact-model-spec.md
    Doc 22 cleanup: allowed_followups removed (validate against artifact_followup_handlers).
    """

    artifact_id: str
    artifact_type: str  # "booking", "ticket", "refund_request", etc.
    external_id: str | None = None
    source_action_type: str | None = None
    status: str  # artifact-type-specific: "confirmed", "cancelled", etc.
    title: str | None = None
    user_visible_fields: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class PendingActionState(BaseModel):
    """Execution lifecycle state for an in-flight action.

    Separate from artifacts: artifacts represent durable business objects,
    pending actions represent control-plane execution state.
    """

    action_id: str
    action_type: str
    status: Literal[
        "confirmation_required",
        "queued",
        "starting",
        "running",
        "waiting_poll",
        "waiting_webhook",
        "retry_scheduled",
        "slow",
        "cancelling",
        "completed",
        "cancelled",
        "completion_uncertain",
        "failed",
    ]
    tool_ref: str | None = None
    artifact_id: str | None = None
    action_label: str | None = None
    target_ref: str | None = None
    retryable: bool | None = None
    resumable: bool = True
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_progress_at: datetime | None = None
    activity: dict[str, Any] = Field(default_factory=dict)
    commitment: dict[str, Any] = Field(default_factory=dict)
    user_visible_context: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GroundingState(BaseModel):
    """Tracks which facts/requests were already made explicit to the user."""

    acknowledged_fact_keys: list[str] = Field(default_factory=list)
    acknowledged_requests: list[str] = Field(default_factory=list)
    last_acknowledged_activity_id: str | None = None
    last_user_visible_status: str | None = None
    unresolved_points: list[str] = Field(default_factory=list)
    # P3 of doc 33 (WI-1, WI-2): committed social tone and paused-reprompt
    # markers set by the LLM-selected ``apologize`` and ``pause`` moves.
    # Both are single-turn (cleared on the next user-text turn).
    committed_social_tone: Literal["neutral", "apologetic"] = "neutral"
    suppress_reprompt: bool = False


class CommitmentState(BaseModel):
    """Tracks how strongly the runtime can commit to a user-visible claim."""

    status: Literal[
        "confirmed",
        "probable",
        "pending_external",
        "completion_uncertain",
        "failed_retryable",
        "failed_terminal",
    ] = "probable"
    summary: str | None = None


class PendingPermissionState(BaseModel):
    """Visible pending interaction state for confirmation/policy waits."""

    request_id: str
    permission_kind: str
    target_ref: str | None = None
    status: Literal["waiting", "granted", "denied", "aborted", "expired"] = "waiting"
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    user_visible_context: dict[str, Any] = Field(default_factory=dict)


class RepairContext(BaseModel):
    """Tracks why the runtime is currently producing a repair/recovery response."""

    repair_kind: Literal[
        "repeat_acknowledgment",
        "interrupt_acknowledged",
        "late_result_reconciliation",
        "contradiction_repair",
        "no_progress_repair",
        "provider_uncertainty_repair",
    ]
    target_ref: str | None = None
    summary: str | None = None


class InteractionPacingPolicy(BaseModel):
    """Channel/use-case pacing rules for human-like interaction."""

    channel: str = "web_chat"
    locale: str = "en"
    slow_threshold_ms: int = 1500
    soft_timeout_ms: int = 2500
    endpointing_ms: int = 650
    filler_repeat_gap_ms: int = 3500
    turn_eagerness: Literal["low", "normal", "high"] = "normal"
    interruptibility_policy: Literal[
        "always_interruptible",
        "interruptible_except_policy",
        "non_interruptible",
    ] = "interruptible_except_policy"
    allow_filler: bool = True
    filter_backchannels: bool = True
    max_fillers_per_pending_action: int = 3


class InteractionStatusItem(BaseModel):
    """Short-lived projected status item for realtime/session surfaces."""

    item_id: str
    item_type: Literal["activity", "permission", "repair", "policy"]
    summary: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    source_ref: str | None = None


class CaptureRuntimeState(BaseModel):
    """Runtime bookkeeping for information gathering while a step is collecting details.

    Tracks slot status, progress, and prompt deduplication to prevent
    repetitive or brittle detail-collection loops.
    """

    slot_status: dict[str, str] = Field(default_factory=dict)
    no_progress_count: int = 0
    turn_count: int = 0
    last_prompt_fingerprint: str | None = None


class ConversationFocus(BaseModel):
    """Tracks the current conversation focus for reference resolution."""

    artifact_id: str | None = None
    artifact_type: str | None = None
    step_id: str | None = None
    topic: str | None = None
    set_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SuspendedFrame(BaseModel):
    """A suspended conversation context that can be resumed later."""

    frame_id: str
    step_id: str
    reason: str
    facts_snapshot: dict[str, Any] = Field(default_factory=dict)
    suspended_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class InterruptPolicy(BaseModel):
    """Controls which interrupts are allowed from a given state."""

    allowed_intents: list[str] = Field(default_factory=list)
    blocked_intents: list[str] = Field(default_factory=list)
    allow_all: bool = True


class ConversationControlState(BaseModel):
    """Runtime control state that extends ConversationState.

    Persisted as part of conversation metadata. Validated back into
    typed models on load — storage may temporarily serialize as JSON.
    """

    current_focus: ConversationFocus | None = None
    active_artifacts: list[ConversationArtifact] = Field(default_factory=list)
    suspended_frames: list[SuspendedFrame] = Field(default_factory=list)
    current_topic: str | None = None
    pending_action: PendingActionState | None = None
    pending_permission: PendingPermissionState | None = None
    capture_runtime: dict[str, CaptureRuntimeState] = Field(default_factory=dict)
    grounding: GroundingState = Field(default_factory=GroundingState)
    active_repair: RepairContext | None = None


# ── Action config (tooling redesign) ─────────────────────────────────────────


class ActionConfig(BaseModel):
    """Configuration for step-side code execution.

    Canonical execution path for tool-backed or code-backed step behavior. The code block runs in a
    RestrictedPython sandbox with callable functions injected from the
    APIs and Tools tabs.
    """

    code: str = ""
    callable_functions_code: str = ""
    callable_api_refs: list[str] = Field(default_factory=list)
    callable_integrations: list[str] = Field(default_factory=list)
    callable_system_refs: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = 30.0


# ── Conversation state ───────────────────────────────────────────────────────


class ConversationState(BaseModel):
    conversation_id: str
    organization_id: str | None = None
    agent_id: str
    agent_version_id: str
    mode: ConversationMode = "live"
    channel: Channel | None = None
    status: ConversationStatus = "active"
    outcome: ConversationOutcome | None = None
    step_id: str
    facts: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    processed_dedupe_keys: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime | None = None
    updated_at: datetime
    # Optimistic concurrency version — incremented on every Redis CAS write.
    # Default 0 ensures existing snapshots without this field deserialise safely.
    version: int = 0
    # Runtime control state — persisted as JSON, validated on load.
    control_state: ConversationControlState = Field(default_factory=ConversationControlState)

class TurnLogEntry(BaseModel):
    """One row of the append-only per-conversation turn log.

    ``seq`` is assigned by the turn-log store at commit time under the
    conversation row lock; callers leave it unset. ``state_after`` is the full
    conversation-state snapshot the turn committed, so the conversation row is
    always a fold of its turn log.
    """

    turn_pk: str = Field(default_factory=lambda: str(uuid4()))
    conversation_id: str
    organization_id: str | None = None
    seq: int | None = None
    turn_id: str
    dedupe_key: str
    trace_id: str | None = None
    step_before: str
    step_after: str
    state_after: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ClassifierTraceRecord(BaseModel):
    """Per-turn structured trace of the prefill-first classifier call.

    Populated when a turn is classified by the prefill classifier (or its
    documented main_llm fallback). Mirrors the shape of ``ClassificationResult``
    in ``ruhu.classifier.protocol`` plus the model identity.

    Schema documented in
    docs/pre-fill-intent-classifier-design/02-architecture-spec.md
    §Trace records.

    All fields are optional so classifier metadata can be populated sparsely
    when only ``backend``, ``model``, ``chosen_label``, and ``confidence`` are
    available.
    """

    backend: Literal["transformers", "vllm", "vertex_gemini", "unavailable"] | None = None
    model: str | None = None
    lora_name: str | None = None
    chosen_label: str | None = None
    confidence: float | None = None
    decode_logprobs: dict[str, float] = Field(default_factory=dict)
    cache_hit: bool | None = None
    prefill_tokens: int | None = None
    decode_tokens: int | None = None
    elapsed_ms: int | None = None
    error: str | None = None


class TurnTrace(BaseModel):
    schema_version: int = 1
    trace_id: str
    conversation_id: str
    organization_id: str | None = None
    turn_id: str
    agent_id: str
    agent_version_id: str | None = None
    otel_trace_id: str | None = None
    channel: str = ""
    modality: str = ""
    event_type: str = ""
    normalized_observation: NormalizedObservationRecord | None = None
    guard_results: list[GuardResultRecord] = Field(default_factory=list)
    model_outputs: list[ModelOutputRecord] = Field(default_factory=list)
    truncated_fields: list[str] = Field(default_factory=list)
    error_kind: ErrorKind = "none"
    decision_observability: TurnDecisionObservability = Field(default_factory=TurnDecisionObservability)
    step_before: str
    step_after: str
    semantic_events: list[SemanticEventRecord] = Field(default_factory=list)
    fact_updates: list[FactUpdate] = Field(default_factory=list)
    chosen_action: ActionRecord
    emitted_messages: list[RenderedMessage] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    rules: RuntimeRulesTrace = Field(default_factory=RuntimeRulesTrace)
    latency_breakdown_ms: dict[str, int] = Field(default_factory=dict)
    classifier: ClassifierTraceRecord | None = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class SimulationRun(BaseModel):
    start: RuntimeTurnResult
    turns: list[RuntimeTurnResult]
    final_step_id: str
    final_facts: dict[str, Any]


class AgentDefinitionValidationIssue(BaseModel):
    severity: ValidationSeverity
    code: str
    message: str
    step_id: str | None = None
    transition_id: str | None = None
    fact_name: str | None = None
    tool_ref: str | None = None


class AgentDefinitionValidationReport(BaseModel):
    agent_id: str
    agent_name: str
    valid: bool
    error_count: int
    warning_count: int
    issues: list[AgentDefinitionValidationIssue] = Field(default_factory=list)
