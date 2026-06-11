from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import nullcontext
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Literal
from uuid import uuid4

import structlog.contextvars

logger = logging.getLogger(__name__)

from .journey_context import (
    build_authored_step_guidance,
    build_journey_context,
    build_pending_fact_contexts,
    build_transition_narrative,
    normalized_fact_requirements,
    fact_requirement_names,
)
from .interpreter import SemanticInterpreter
from .interaction_pacing import is_voice_backchannel, pacing_policy_for_channel, phrase_for
from .observability.tracing import get_current_otel_trace_id
from .response_generation import (
    ResponseGenerationContext,
    ResponseGenerationRequest,
    ResponseGenerator,
    build_response_generator_from_env,
)
from .state_summary import summarize_step
from .schemas import (
    ActiveRepairSummary,
    ActionOutcomeSummary,
    CaptureRuntimeState,
    ConversationRuntimeProjection,
    ConversationArtifact,
    ConversationFocus,
    DialogueMessage,
    InteractionStatusItem,
    KnowledgeGroundingPolicy,
    NarrationContract,
    PendingActionState,
    PendingActionSummary,
    PendingPermissionState,
    PendingPermissionSummary,
    RecentDialogueMessage,
    RepairContext,
    RenderContext,
    RenderOutput,
    ResponseConstraintSet,
    RetrievalChunk,
    RuntimeControlSummary,
    RuntimeActivityStatus,
    RuntimeProjection,
    RuntimeStepSummary,
    StatusTrailSummaryItem,
    TurnInterpretationSummary,
    UserContractSummary,
)
from .rules import (
    PendingRuleConfirmation,
    RuleEngine,
    RuleEvaluationContext,
    RuleMatch,
    RuleStageDecision,
    RuleTrace,
    RuntimeRulesTrace,
)
from .rules_resolver import RuleProgramResolver
from .schemas import (
    ActionRecord,
    AllRequiredFactsPresentCondition,
    AttachmentPresentCondition,
    Channel,
    Condition,
    ConversationState,
    FactEqualsCondition,
    FactMissingCondition,
    FactPresentCondition,
    FactRequirement,
    FactUpdate,
    GuardDef,
    GuardFailureCondition,
    InteractionDebugActiveRepair,
    InteractionDebugPendingAction,
    InteractionDebugPendingPermission,
    InteractionDebugSnapshot,
    InteractionDebugVoicePolicy,
    ModelOutputRecord,
    JourneyContext,
    NormalizedObservationRecord,
    OtherwiseCondition,
    OutcomeCondition,
    ProactiveTrigger,
    RenderedMessage,
    RuntimeTurn,
    RuntimeTurnResult,
    SemanticEventRecord,
    ToolBinding,
    ToolCallRecord,
    ToolOutcomeCondition,
    ToolOutcomeRecord,
    TurnDecisionObservability,
    TurnLogEntry,
    TurnTrace,
    ViewReadyCondition,
)
from .stores import InMemoryConversationStore, InMemoryTraceStore, InMemoryTurnLogStore
from .stores import ConversationStore, DuplicateTurnError, TraceStore, TurnLogStore
from .tools.runtime import ToolRuntime
from .realtime.bridge import KernelRealtimeBridge
from .capture import FactCandidate, FactPipeline, build_default_fact_pipeline
from .capture.comparison import fact_value_equals
from .capture.confirmation import PENDING_FACTS_METADATA_KEY, resolve_pending_confirmations
from .capture.storage import StorageRouter
from .agent_document import (
    AgentDocument,
    CompiledAgentDocument,
    ScenarioRoute,
    Step,
    StepRuntimeEntry,
    StepTransition,
    build_step_runtime_entry,
    compile_agent_document,
    step_capability_flags,
    select_start_scenario_id,
)
from .tools.types import ToolCall, ToolCaller, ToolInvocation, ToolResult

_MARKDOWN_HEADING_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s+")
_MARKDOWN_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MARKDOWN_EMPHASIS_RE = re.compile(r"[*_`]+")
_WHITESPACE_RE = re.compile(r"\s+")
ACCOUNT_ID_HINT_RE = re.compile(
    r"\b(?:account|acct)\s*(?:id|number|#)?\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9_\-]{3,63})\b",
    re.IGNORECASE,
)
ID_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{3,63}$")
_TERMINAL_OUTCOME_ALIASES = {
    "resolved": "resolved",
    "completed": "resolved",
    "closed": "resolved",
    "transferred": "transferred",
    "handoff": "transferred",
    "transfer": "transferred",
    "abandoned": "abandoned",
    "failed": "failed",
    "voicemail": "voicemail",
    "callback_scheduled": "callback_scheduled",
    "follow_up_required": "follow_up_required",
}
_PENDING_RULE_CONFIRMATIONS_METADATA_KEY = "_pending_rule_confirmations"
_CAPTURE_MAX_TURNS = 10  # Max turns before attempts exhausted
_CAPTURE_MAX_NO_PROGRESS = 3  # Max consecutive turns with no new facts
_STATUS_TRAIL_COMPLETION_TTL_MS = 30_000
_RESPONSE_CONTEXT_METADATA_KEY = "__ruhu_response_generation"
_CLASSIFIER_SEMANTIC_EVENTS_METADATA_KEY = "__ruhu_classifier_semantic_events"
_CLASSIFIER_METADATA_METADATA_KEY = "__ruhu_classifier_metadata"
_PENDING_ACTION_STATUS_PATTERNS = (
    r"\bwhat(?:'s| is)? happening\b",
    r"\bwhat(?:'s| is)? going on\b",
    r"\bcan you let me know\b",
    r"\bwhat(?:'s| is) the status\b",
    r"\bstatus update\b",
    r"\bany update\b",
    r"\bare you still there\b",
    r"\bdid it go through\b",
    r"\bdid it work\b",
    r"\bdid it finish\b",
    r"\bis it done\b",
)
_RUNTIME_MODEL_METADATA_KEY = "__ruhu_runtime_model__"
_AGENT_NAME_METADATA_KEY = "__ruhu_agent_name__"
_CURRENT_STEP_ID_METADATA_KEY = "__ruhu_current_step_id__"
_CURRENT_SCENARIO_ID_METADATA_KEY = "__ruhu_current_scenario_id__"
_CURSOR_REVISION_METADATA_KEY = "__ruhu_cursor_revision__"
_LAST_SCENARIO_ROUTE_METADATA_KEY = "__ruhu_last_scenario_route__"
_STEP_CAPABILITIES_METADATA_KEY = "__ruhu_step_capabilities__"
_STEP_MISSING_FACTS_METADATA_KEY = "__ruhu_step_missing_facts__"
_STEP_TOOL_REFS_METADATA_KEY = "__ruhu_step_tool_refs__"
_STEP_TRANSITION_TARGETS_METADATA_KEY = "__ruhu_step_transition_targets__"
_STEP_SCRIPTED_SAY_METADATA_KEY = "__ruhu_step_say__"
_STEP_WORKLOAD_CLASS_METADATA_KEY = "__ruhu_step_workload_class__"
_STEP_EXECUTION_ISOLATION_METADATA_KEY = "__ruhu_step_execution_isolation__"

_CAPTURE_BRIDGE_PATTERNS = (
    r"\bhold on\b",
    r"\bhang on\b",
    r"\bone sec(?:ond)?\b",
    r"\bjust a sec(?:ond)?\b",
    r"\bgive me (?:a )?sec(?:ond)?\b",
    r"\bjust a moment\b",
    r"\bone moment\b",
    r"\bi(?:'ll| will) share\b",
    r"\bi(?:'ll| will) send\b",
    r"\blet me (?:grab|get|find|check|pull up)\b",
    r"\bi(?:'m| am) (?:getting|looking for|finding)\b",
    r"\bsending it\b",
)


@dataclass
class _MoveCommitResult:
    conversation: ConversationState
    fact_updates: list[FactUpdate]
    tool_result: ToolResult | None = None
    resolved_args: dict[str, object] | None = None


def _coerce_artifact_metadata(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def _is_final_user_text_turn(turn: RuntimeTurn) -> bool:
    return turn.event_type in {"user_message", "user_final_transcript"} and bool(turn.text)


class ConversationKernel:
    """Canonical step-native kernel with in-memory stores.

    This is the first usable implementation pass:
    - validated agent document access
    - in-memory conversation persistence
    - deterministic fact extraction
    - heuristic semantic-event normalization
    - explicit transition selection
    - per-turn trace emission
    """

    def __init__(
        self,
        conversation_store: ConversationStore | None = None,
        trace_store: TraceStore | None = None,
        turn_log_store: TurnLogStore | None = None,
        interpreter: SemanticInterpreter | None = None,
        tool_runtime: ToolRuntime | None = None,
        realtime_bridge: KernelRealtimeBridge | None = None,
        rule_engine: RuleEngine | None = None,
        rule_program_resolver: RuleProgramResolver | None = None,
        response_generator: ResponseGenerator | None = None,
        response_generation_context_resolver: (
            Callable[[ConversationState, Step | None, RuntimeTurn], ResponseGenerationContext | None] | None
        ) = None,
        field_extractor: object | None = None,
        fact_pipeline: FactPipeline | None = None,
        capture_storage_router: StorageRouter | None = None,
    ) -> None:
        self._conversation_store = conversation_store or InMemoryConversationStore()
        self._trace_store = trace_store or InMemoryTraceStore()
        self._turn_log_store = turn_log_store or InMemoryTurnLogStore()
        self._interpreter = interpreter or SemanticInterpreter()
        self._tool_runtime = tool_runtime
        self._realtime_bridge = realtime_bridge
        self._rule_engine = rule_engine or RuleEngine()
        self._rule_program_resolver = rule_program_resolver
        self._dialogue_generator = response_generator or build_response_generator_from_env()
        self._response_generation_context_resolver = response_generation_context_resolver
        # Optional LLM-based attachment field extractor (FieldExtractorLLM protocol).
        # When set, the kernel calls it during view_ready event processing to populate
        # conversation facts from attachment inline_text.
        self._field_extractor = field_extractor
        self._fact_pipeline = fact_pipeline or build_default_fact_pipeline(field_extractor)
        self._capture_storage_router = capture_storage_router or StorageRouter()

    @property
    def trace_store(self) -> TraceStore:
        return self._trace_store

    @property
    def conversation_store(self) -> ConversationStore:
        return self._conversation_store

    @property
    def turn_log_store(self) -> TurnLogStore:
        return self._turn_log_store

    @property
    def tool_runtime(self) -> ToolRuntime | None:
        return self._tool_runtime

    def initialize_conversation(
        self,
        conversation_id: str,
        *,
        agent_document: AgentDocument,
        agent_id: str | None = None,
        agent_name: str | None = None,
        agent_version_id: str | None = None,
        mode: str = "live",
        organization_id: str | None = None,
        starting_step_id: str | None = None,
        starting_scenario_id: str | None = None,
        seed_facts: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ConversationState:
        compiled_agent_document = compile_agent_document(agent_document)
        existing = self._conversation_store.load(conversation_id)
        if existing is not None:
            raise ValueError(f"conversation already exists: {conversation_id}")
        resolved_agent_id = agent_id
        if not resolved_agent_id:
            raise ValueError("agent_id is required for agent-document runtime conversations")
        resolved_agent_name = agent_name or resolved_agent_id
        resolved_scenario_id = select_start_scenario_id(
            compiled_agent_document,
            requested_scenario_id=starting_scenario_id,
            channel=str((metadata or {}).get("channel") or "") or None,
        )
        if starting_step_id is not None:
            resolved_state_id = starting_step_id
        else:
            resolved_state_id = compiled_agent_document.scenario_by_id(resolved_scenario_id).start_step_id
        compiled_agent_document.step_by_id(resolved_state_id)
        runtime_entry = self._build_step_runtime_entry(
            agent_document=compiled_agent_document,
            current_step_id=resolved_state_id,
            facts=seed_facts,
        )
        conversation_metadata = deepcopy(metadata or {})
        conversation_metadata[_AGENT_NAME_METADATA_KEY] = resolved_agent_name
        if runtime_entry is not None:
            self._write_step_runtime_metadata(conversation_metadata, runtime_entry)
            conversation_metadata[_CURSOR_REVISION_METADATA_KEY] = 0
        state = ConversationState(
            conversation_id=conversation_id,
            organization_id=organization_id,
            agent_id=resolved_agent_id,
            agent_version_id=agent_version_id or f"local:{resolved_agent_id}",
            mode=mode,  # type: ignore[arg-type]
            channel=None,
            step_id=resolved_state_id,
            facts=dict(seed_facts or {}),
            metadata=conversation_metadata,
            updated_at=datetime.now(timezone.utc),
        )
        self._conversation_store.save(state)
        return state

    def load_conversation(self, conversation_id: str) -> ConversationState | None:
        return self._conversation_store.load(conversation_id)

    def start_conversation(
        self,
        conversation_id: str,
        *,
        agent_document: AgentDocument,
        agent_id: str | None = None,
        agent_name: str | None = None,
        agent_version_id: str | None = None,
        mode: str = "live",
        channel: Channel = "web_chat",
        organization_id: str | None = None,
        starting_step_id: str | None = None,
        starting_scenario_id: str | None = None,
        seed_facts: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> RuntimeTurnResult:
        conversation = self.initialize_conversation(
            conversation_id,
            agent_document=agent_document,
            agent_id=agent_id,
            agent_name=agent_name,
            agent_version_id=agent_version_id,
            mode=mode,
            organization_id=organization_id,
            starting_step_id=starting_step_id,
            starting_scenario_id=starting_scenario_id,
            seed_facts=seed_facts,
            metadata={**dict(metadata or {}), "channel": channel},
        )
        conversation.channel = channel
        self._conversation_store.save(conversation)
        if self._realtime_bridge is not None:
            self._realtime_bridge.record_conversation_started(conversation, channel=channel)
        return self.process_turn(
            conversation_id=conversation_id,
            turn=RuntimeTurn(
                turn_id=f"{conversation_id}:start",
                dedupe_key=f"{conversation_id}:start",
                channel=channel,
                modality="event",
                event_type="system_event",
                received_at=datetime.now(timezone.utc),
            ),
            agent_document=agent_document,
            agent_id=agent_id,
            agent_name=agent_name,
        )

    def load_step_runtime_entry(
        self,
        conversation_id: str,
        *,
        agent_document: AgentDocument,
    ) -> StepRuntimeEntry | None:
        compiled_agent_document = compile_agent_document(agent_document)
        conversation = self._conversation_store.load(conversation_id)
        if conversation is None:
            return None
        current_step_id = str(
            conversation.metadata.get(_CURRENT_STEP_ID_METADATA_KEY) or conversation.step_id
        )
        return self._build_step_runtime_entry(
            agent_document=compiled_agent_document,
            current_step_id=current_step_id,
            facts=conversation.facts,
        )

    def _build_step_runtime_entry(
        self,
        *,
        agent_document: CompiledAgentDocument | AgentDocument | None,
        current_step_id: str,
        facts: dict[str, object] | None = None,
        pending_action: bool = False,
        pending_permission: bool = False,
        active_repair: bool = False,
    ) -> StepRuntimeEntry | None:
        if agent_document is None:
            return None
        return build_step_runtime_entry(
            agent_document,
            current_step_id=current_step_id,
            facts=facts,
            pending_action=pending_action,
            pending_permission=pending_permission,
            active_repair=active_repair,
        )

    def _write_step_runtime_metadata(
        self,
        metadata: dict[str, object],
        runtime_entry: StepRuntimeEntry,
    ) -> None:
        metadata[_RUNTIME_MODEL_METADATA_KEY] = "step"
        metadata[_CURRENT_STEP_ID_METADATA_KEY] = runtime_entry.current_step_id
        metadata[_CURRENT_SCENARIO_ID_METADATA_KEY] = runtime_entry.current_scenario_id
        metadata[_STEP_CAPABILITIES_METADATA_KEY] = {
            "collects_missing_details": runtime_entry.collects_missing_details,
            "uses_tooling": runtime_entry.uses_tooling,
            "hands_off": runtime_entry.hands_off,
            "completes": runtime_entry.completes,
        }
        metadata[_STEP_MISSING_FACTS_METADATA_KEY] = list(runtime_entry.missing_facts)
        metadata[_STEP_TOOL_REFS_METADATA_KEY] = list(runtime_entry.available_tool_refs)
        metadata[_STEP_TRANSITION_TARGETS_METADATA_KEY] = list(runtime_entry.transition_target_ids)
        metadata[_STEP_WORKLOAD_CLASS_METADATA_KEY] = runtime_entry.workload_class
        metadata[_STEP_EXECUTION_ISOLATION_METADATA_KEY] = runtime_entry.execution_isolation
        if runtime_entry.scripted_say:
            metadata[_STEP_SCRIPTED_SAY_METADATA_KEY] = runtime_entry.scripted_say
        else:
            metadata.pop(_STEP_SCRIPTED_SAY_METADATA_KEY, None)

    def _step_cursor_revision(self, conversation: ConversationState) -> int:
        raw = conversation.metadata.get(_CURSOR_REVISION_METADATA_KEY)
        if isinstance(raw, bool):
            return int(raw)
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float):
            return int(raw)
        if isinstance(raw, str) and raw.isdigit():
            return int(raw)
        return 0

    def process_turn(
        self,
        conversation_id: str,
        turn: RuntimeTurn,
        *,
        agent_document: AgentDocument,
        agent_id: str | None = None,
        agent_name: str | None = None,
        organization_id: str | None = None,
        on_first_sentence: object | None = None,
    ) -> RuntimeTurnResult:
        compiled_agent_document = compile_agent_document(agent_document)
        resolved_agent_id = agent_id or conversation_id
        with structlog.contextvars.bound_contextvars(
            conversation_id=conversation_id,
            agent_id=resolved_agent_id,
            turn_id=turn.turn_id,
            organization_id=organization_id,
        ):
            conversation = self._conversation_store.load(conversation_id)
            if conversation is None:
                raise KeyError(f"unknown conversation id: {conversation_id}")
            try:
                compiled_agent_document.step_by_id(conversation.step_id)
            except KeyError as exc:
                raise KeyError(
                    f"conversation step '{conversation.step_id}' is not part of the active agent document"
                ) from exc
            result = self._process_step_turn(
                conversation=conversation,
                agent_document=compiled_agent_document,
                turn=turn,
            )
        conversation = self._conversation_store.load(conversation_id)
        if conversation is not None:
            runtime_entry = self._build_step_runtime_entry(
                agent_document=compiled_agent_document,
                current_step_id=conversation.step_id,
                facts=conversation.facts,
            )
            if runtime_entry is not None:
                metadata = deepcopy(conversation.metadata)
                self._write_step_runtime_metadata(metadata, runtime_entry)
                conversation.metadata = metadata
                self._conversation_store.save(conversation)
        return result

    def _process_step_turn(
        self,
        *,
        conversation: ConversationState,
        agent_document: CompiledAgentDocument,
        turn: RuntimeTurn,
    ) -> RuntimeTurnResult:
        step_before = conversation.step_id
        if turn.dedupe_key in conversation.processed_dedupe_keys:
            return self._duplicate_result(conversation, turn)

        current_step_id = conversation.step_id
        working_facts = dict(conversation.facts)
        cursor_revision_before = self._step_cursor_revision(conversation)
        current_scenario_id = agent_document.scenario_for_step_id(current_step_id).id
        scenario_handoff_count = 0
        scenario_routing_rule_id: str | None = None
        semantic_events, fact_updates, decision_observability = self._understand_step_turn(
            agent_document=agent_document,
            current_step_id=current_step_id,
            conversation=conversation,
            turn=turn,
        )
        for update in fact_updates:
            working_facts[update.name] = update.value

        guard_events = self._evaluate_guards(
            agent_document.step_by_id(current_step_id).guards,
            turn,
            {**conversation.facts, **{u.name: u.value for u in fact_updates}},
        )
        semantic_events.extend(guard_events)
        consumed_routing_event_keys: set[str] = set()

        emitted_messages: list[RenderedMessage] = []
        tool_calls: list[ToolCallRecord] = []
        chosen_action = ActionRecord(type="stay", reason="step_runtime_noop")
        entered_step_ids: list[str] = [current_step_id] if turn.event_type == "system_event" else []
        max_hops = 20

        for _ in range(max_hops):
            step = agent_document.step_by_id(current_step_id)
            runtime_entry = build_step_runtime_entry(
                agent_document,
                current_step_id=current_step_id,
                facts=working_facts,
            )
            entering_this_turn = current_step_id in entered_step_ids
            if entering_this_turn and runtime_entry.scripted_say:
                emitted_messages.append(RenderedMessage(text=runtime_entry.scripted_say))

            if step.completion is not None:
                chosen_action = ActionRecord(type="end", reason=step.completion.disposition)
                break

            if step.handoff is not None:
                chosen_action = ActionRecord(type="handoff", reason=step.handoff.target_type)
                break

            if runtime_entry.collects_missing_details:
                if not emitted_messages:
                    if runtime_entry.scripted_say:
                        emitted_messages.append(RenderedMessage(text=runtime_entry.scripted_say))
                    else:
                        emitted_messages.append(
                            RenderedMessage(
                                text=self._step_missing_fact_prompt(
                                    step=step,
                                    missing_facts=runtime_entry.missing_facts,
                                )
                            )
                        )
                chosen_action = ActionRecord(
                    type="ask_missing",
                    reason="step_missing_required_facts",
                    payload={"missing_facts": list(runtime_entry.missing_facts)},
                )
                break

            if runtime_entry.uses_tooling and step.action_config is not None:
                action, new_messages, tool_calls = self._execute_step_action_config(
                    conversation=conversation,
                    step=step,
                    turn=turn,
                    working_facts=working_facts,
                    semantic_events=semantic_events,
                    tool_calls=tool_calls,
                )
                chosen_action = action
                emitted_messages.extend(new_messages)
            elif step.tool_policy:
                action, new_messages, tool_calls = self._execute_step_tool_policy(
                    conversation=conversation,
                    agent_document=agent_document,
                    step=step,
                    turn=turn,
                    working_facts=working_facts,
                    semantic_events=semantic_events,
                    tool_calls=tool_calls,
                )
                if action.type != "stay":
                    chosen_action = action
                    emitted_messages.extend(new_messages)

            routing_semantic_events = [
                event for event in semantic_events if event.key not in consumed_routing_event_keys
            ]
            matched_transition = self._matched_step_transition(
                step=step,
                semantic_events=routing_semantic_events,
                working_facts=working_facts,
                turn=turn,
            )
            if matched_transition is not None and matched_transition.to_step_id != current_step_id:
                matched_when = matched_transition.when
                if isinstance(matched_when, OutcomeCondition):
                    # Outcome edges fire from a single ``routing.outcome_resolved``
                    # event per turn — consume it so a second outcome edge in
                    # this multi-hop pass can't double-fire.
                    consumed_routing_event_keys.add("routing:outcome_resolved")
                elif isinstance(matched_when, GuardFailureCondition):
                    consumed_routing_event_keys.add(f"guard_failure:{matched_when.guard_id}")
                elif isinstance(matched_when, ToolOutcomeCondition):
                    consumed_routing_event_keys.add(f"tool_outcome:{matched_when.outcome}")
                current_step_id = matched_transition.to_step_id
                entered_step_ids.append(current_step_id)
                # Cross-scenario step transitions: keep current_scenario_id in sync
                # with the destination step. The kernel's step_by_id resolves
                # globally so navigation succeeds either way, but trace fields
                # and the scenario-route gate downstream rely on this id being
                # the scenario the *current step* belongs to.
                new_scenario_id = agent_document.scenario_for_step_id(current_step_id).id
                if new_scenario_id != current_scenario_id:
                    current_scenario_id = new_scenario_id
                    scenario_handoff_count += 1
                if chosen_action.type == "stay":
                    chosen_action = ActionRecord(
                        type="transition",
                        reason=f"step_transition:{matched_transition.id}",
                        payload={
                            "to_step_id": current_step_id,
                            "scenario_id": current_scenario_id,
                        },
                    )
                continue

            matched_scenario_route = self._matched_scenario_route(
                agent_document=agent_document,
                current_scenario_id=current_scenario_id,
                semantic_events=routing_semantic_events,
                working_facts=working_facts,
                turn=turn,
            )
            if matched_scenario_route is not None:
                matched_when = matched_scenario_route.when
                if isinstance(matched_when, OutcomeCondition):
                    consumed_routing_event_keys.add("routing:outcome_resolved")
                elif isinstance(matched_when, GuardFailureCondition):
                    consumed_routing_event_keys.add(f"guard_failure:{matched_when.guard_id}")
                elif isinstance(matched_when, ToolOutcomeCondition):
                    consumed_routing_event_keys.add(f"tool_outcome:{matched_when.outcome}")
                if scenario_handoff_count >= 1:
                    chosen_action = ActionRecord(
                        type="reply" if emitted_messages else "stay",
                        reason="step_runtime_scenario_handoff_cap_reached",
                        payload={
                            "from_scenario_id": current_scenario_id,
                            "blocked_target_scenario_id": matched_scenario_route.to_scenario_id,
                            "scenario_routing_rule_id": matched_scenario_route.id,
                        },
                    )
                    break

                scenario_handoff_count += 1
                current_scenario_id = matched_scenario_route.to_scenario_id
                scenario_routing_rule_id = matched_scenario_route.id
                current_step_id = agent_document.scenario_by_id(current_scenario_id).start_step_id
                entered_step_ids.append(current_step_id)
                chosen_action = ActionRecord(
                    type="transition",
                    reason=f"scenario_route:{matched_scenario_route.id}",
                    payload={
                        "to_scenario_id": current_scenario_id,
                        "to_step_id": current_step_id,
                        "scenario_routing_rule_id": matched_scenario_route.id,
                    },
                )
                continue

            if chosen_action.type == "stay":
                if not emitted_messages:
                    generic_fallback = self._generic_intent_response(
                        agent_document=agent_document,
                        semantic_events=semantic_events,
                    )
                    step_fallback = generic_fallback or self._step_response_fallback(step=step)
                    if step_fallback:
                        emitted_messages.append(RenderedMessage(text=step_fallback))
                chosen_action = ActionRecord(
                    type="reply" if emitted_messages else "stay",
                    reason="step_runtime_yield",
                )
            break

        conversation.step_id = current_step_id
        conversation.facts = working_facts
        conversation.updated_at = datetime.now(timezone.utc)
        conversation.channel = turn.channel
        if turn.text and turn.event_type in {"user_message", "user_final_transcript"}:
            recent_texts = [
                str(item)
                for item in conversation.metadata.get("__ruhu_capture_recent_user_texts__", [])
                if str(item).strip()
            ]
            recent_texts.append(turn.text.strip())
            conversation.metadata["__ruhu_capture_recent_user_texts__"] = recent_texts[-8:]
        self._apply_step_conversation_lifecycle(
            conversation=conversation,
            agent_document=agent_document,
            step_after=current_step_id,
        )
        runtime_entry = build_step_runtime_entry(
            agent_document,
            current_step_id=current_step_id,
            facts=working_facts,
        )
        self._write_step_runtime_metadata(conversation.metadata, runtime_entry)
        conversation.metadata[_CURSOR_REVISION_METADATA_KEY] = (
            cursor_revision_before + scenario_handoff_count
        )
        if scenario_routing_rule_id is not None:
            conversation.metadata[_LAST_SCENARIO_ROUTE_METADATA_KEY] = scenario_routing_rule_id
        else:
            conversation.metadata.pop(_LAST_SCENARIO_ROUTE_METADATA_KEY, None)
        conversation.processed_dedupe_keys = [*conversation.processed_dedupe_keys, turn.dedupe_key][-100:]

        trace_id = str(uuid4())
        result = RuntimeTurnResult(
            turn_id=turn.turn_id,
            conversation_id=conversation.conversation_id,
            step_before=step_before,
            step_after=current_step_id,
            semantic_events=semantic_events,
            fact_updates=fact_updates,
            chosen_action=chosen_action,
            emitted_messages=emitted_messages,
            tool_calls=tool_calls,
            trace_id=trace_id,
            latency_breakdown_ms={"total": 0},
        )
        trace = TurnTrace(
            trace_id=trace_id,
            conversation_id=conversation.conversation_id,
            organization_id=conversation.organization_id,
            turn_id=turn.turn_id,
            agent_id=conversation.agent_id,
            agent_version_id=conversation.agent_version_id,
            otel_trace_id=get_current_otel_trace_id(),
            channel=turn.channel or "",
            modality=turn.modality or "",
            event_type=turn.event_type or "",
            normalized_observation=self._normalize_turn_observation(turn),
            decision_observability=decision_observability,
            step_before=step_before,
            step_after=current_step_id,
            semantic_events=semantic_events,
            fact_updates=fact_updates,
            chosen_action=chosen_action,
            emitted_messages=emitted_messages,
            tool_calls=tool_calls,
            latency_breakdown_ms={"total": 0},
            recorded_at=conversation.updated_at,
        )
        return self._commit_turn(
            conversation=conversation,
            turn=turn,
            result=result,
            trace=trace,
            dedupe_key=turn.dedupe_key,
        )

    def _commit_turn(
        self,
        *,
        conversation: ConversationState,
        turn: RuntimeTurn,
        result: RuntimeTurnResult,
        trace: TurnTrace,
        dedupe_key: str,
    ) -> RuntimeTurnResult:
        """Atomically commit a processed turn.

        The turn-log row, trace, and conversation snapshot are written in one
        transaction. The turn log's ``UNIQUE (conversation_id, dedupe_key)``
        constraint is the authoritative duplicate guard: when a concurrent
        request committed the same dedupe key after our in-memory fast-path
        check passed, the whole transaction rolls back (no state, trace, or
        turn row from this attempt) and the turn reports as a duplicate
        exactly like the fast path. Realtime projection happens only after a
        successful commit.
        """
        turn_log_entry = TurnLogEntry(
            conversation_id=conversation.conversation_id,
            organization_id=conversation.organization_id,
            turn_id=result.turn_id,
            dedupe_key=dedupe_key,
            trace_id=result.trace_id,
            step_before=result.step_before,
            step_after=result.step_after,
            state_after=conversation.model_dump(mode="json"),
            created_at=conversation.updated_at,
        )
        try:
            with self._shared_store_transaction() as shared_session:
                self._append_turn_log(turn_log_entry, shared_session=shared_session)
                self._append_trace(trace, shared_session=shared_session)
                self._save_conversation(conversation, shared_session=shared_session)
        except DuplicateTurnError:
            logger.info(
                "duplicate turn dropped at commit",
                extra={
                    "conversation_id": conversation.conversation_id,
                    "turn_id": result.turn_id,
                    "dedupe_key": dedupe_key,
                },
            )
            return self._duplicate_result(conversation, turn)
        self._record_realtime_turn(conversation=conversation, turn=turn, result=result)
        return result

    def _record_realtime_turn(
        self,
        *,
        conversation: ConversationState,
        turn: RuntimeTurn,
        result: RuntimeTurnResult,
    ) -> None:
        """Project a completed turn to realtime without invalidating the turn.

        The trace and conversation state are already durable before this is
        called. Realtime projection feeds widgets/voice, but it must not turn
        an accepted transcript into a failed transcript if the projection bus
        has a transient database/outbox/socket problem.
        """
        if self._realtime_bridge is None:
            return
        try:
            self._realtime_bridge.record_turn(
                conversation=conversation,
                turn=turn,
                result=result,
            )
        except Exception:
            logger.exception(
                "realtime turn projection failed after turn commit",
                extra={
                    "conversation_id": conversation.conversation_id,
                    "turn_id": turn.turn_id,
                    "trace_id": result.trace_id,
                },
            )

    def _understand_step_turn(
        self,
        *,
        agent_document: CompiledAgentDocument,
        current_step_id: str,
        conversation: ConversationState,
        turn: RuntimeTurn,
    ) -> tuple[list[SemanticEventRecord], list[FactUpdate], TurnDecisionObservability]:
        step = agent_document.step_by_id(current_step_id)
        semantic_events: list[SemanticEventRecord] = []
        fact_updates: list[FactUpdate] = []
        decision_observability = TurnDecisionObservability()
        if turn.event_type == "system_event":
            decision_observability.controller_of_record = "system_event"
            decision_observability.intent_source = "system_event"
            semantic_events.append(
                SemanticEventRecord(
                    family="system",
                    name="session_started",
                    source="system",
                    confidence=1.0,
                )
            )
            return semantic_events, fact_updates, decision_observability
        if turn.event_type == "tool_callback":
            decision_observability.controller_of_record = "tool_callback"
            decision_observability.intent_source = "tool_callback"
            tool_name = str(turn.metadata.get("event_name") or turn.metadata.get("tool_name") or "tool").replace(".", "_")
            outcome = str(turn.metadata.get("outcome", "success"))
            semantic_events.append(
                SemanticEventRecord(
                    family="tool_outcome",
                    name=f"{tool_name}_{outcome}",
                    source="tool",
                    confidence=1.0,
                    payload=dict(turn.metadata),
                )
            )
            return semantic_events, fact_updates, decision_observability

        text = (turn.text or "").strip()
        classifier_metadata = self._classifier_metadata_from_turn(turn)
        fact_names = [requirement.name for requirement in step.fact_requirements]
        if fact_names:
            existing_for_capture = dict(conversation.facts)
            pending_resolution = resolve_pending_confirmations(
                text=text,
                pending_items=list(conversation.metadata.get(PENDING_FACTS_METADATA_KEY, [])),
                turn_id=turn.turn_id,
            )
            if pending_resolution.resolved:
                metadata = deepcopy(conversation.metadata)
                metadata[PENDING_FACTS_METADATA_KEY] = pending_resolution.pending_items
                conversation.metadata = metadata
            if pending_resolution.candidates:
                confirmation_result = self._fact_pipeline.process_candidates(
                    candidates=pending_resolution.candidates,
                    turn_id=turn.turn_id,
                    step=step,
                    agent_document=agent_document,
                    existing_facts=existing_for_capture,
                    existing_fact_metadata=dict(conversation.metadata.get("__ruhu_fact_metadata__", {})),
                    conversation_id=conversation.conversation_id,
                    organization_id=conversation.organization_id,
                )
                if confirmation_result.new_fact_metadata:
                    metadata = deepcopy(conversation.metadata)
                    fact_metadata = dict(metadata.get("__ruhu_fact_metadata__", {}))
                    fact_metadata.update(confirmation_result.new_fact_metadata)
                    metadata["__ruhu_fact_metadata__"] = fact_metadata
                    conversation.metadata = metadata
                self._route_capture_storage_writes(
                    conversation=conversation,
                    turn=turn,
                    storage_writes=confirmation_result.storage_writes,
                )
                for update in confirmation_result.updates:
                    existing_for_capture[update.name] = update.value
                    fact_updates.append(update)
                    semantic_events.append(
                        SemanticEventRecord(
                            family="fact_updated",
                            name=update.name,
                            source=update.source,
                            confidence=update.confidence,
                        )
                    )
            skip_normal_capture = pending_resolution.resolved and not pending_resolution.candidates
        else:
            existing_for_capture = dict(conversation.facts)
            skip_normal_capture = False
        if fact_names and not skip_normal_capture:
            capture_result = self._fact_pipeline.extract(
                text=text,
                turn_id=turn.turn_id,
                step=step,
                agent_document=agent_document,
                existing_facts=existing_for_capture,
                existing_fact_metadata=dict(conversation.metadata.get("__ruhu_fact_metadata__", {})),
                classifier_entity_slots=classifier_metadata.get("entity_slots"),
                conversation_id=conversation.conversation_id,
                organization_id=conversation.organization_id,
                transcript_context="\n".join(
                    str(item)
                    for item in conversation.metadata.get("__ruhu_capture_recent_user_texts__", [])
                    if str(item).strip()
                ),
            )
            capture_updates = capture_result.updates
            if capture_result.new_fact_metadata:
                metadata = deepcopy(conversation.metadata)
                fact_metadata = dict(metadata.get("__ruhu_fact_metadata__", {}))
                fact_metadata.update(capture_result.new_fact_metadata)
                metadata["__ruhu_fact_metadata__"] = fact_metadata
                conversation.metadata = metadata
            self._route_capture_storage_writes(
                conversation=conversation,
                turn=turn,
                storage_writes=capture_result.storage_writes,
            )
            if capture_result.needs_confirmation:
                metadata = deepcopy(conversation.metadata)
                pending = list(metadata.get(PENDING_FACTS_METADATA_KEY, []))
                pending.extend(item.model_dump() for item in capture_result.needs_confirmation)
                metadata[PENDING_FACTS_METADATA_KEY] = pending
                conversation.metadata = metadata
                for pending_update in capture_result.needs_confirmation:
                    semantic_events.append(
                        SemanticEventRecord(
                            family="fact_needs_confirmation",
                            name=pending_update.name,
                            source=pending_update.source,
                            confidence=pending_update.confidence,
                            payload={
                                "pending_id": pending_update.pending_id,
                                "reason": pending_update.reason,
                            },
                        )
                    )
            for update in capture_updates:
                fact_updates.append(update)
                semantic_events.append(
                    SemanticEventRecord(
                        family="fact_updated",
                        name=update.name,
                        source=update.source,
                        confidence=update.confidence,
                    )
                )
        # The pre-classified path is the prefill classifier, or any
        # upstream that wrote events into turn metadata via
        # `_CLASSIFIER_SEMANTIC_EVENTS_METADATA_KEY`. Otherwise the
        # in-process SemanticInterpreter handles the turn.
        pre_classified = self._classifier_semantic_events_from_turn(turn)
        if pre_classified:
            semantic_events.extend(pre_classified)
            decision_observability.controller_of_record = "preclassified_classifier"
            decision_observability.intent_source = "preclassified_classifier"
            if classifier_metadata.get("fallback_applied") is True:
                decision_observability.fallback_used = True
                decision_observability.fallback_reason = self._normalize_turn_fallback_reason(
                    classifier_metadata.get("fallback_reason")
                )
        else:
            interpreter_events = self._interpreter_semantic_events_for_step(
                conversation=conversation,
                agent_document=agent_document,
                step=step,
                fact_updates=fact_updates,
                turn=turn,
            )
            # Edge-owned-outcomes contract: the workflow-routing classifier
            # emits exactly one of these per turn:
            #
            #   family="routing", name="outcome_resolved"     → success
            #   family="routing", name="classifier_unavailable" → degraded
            #
            # A successful outcome edge means routing has a candidate. A
            # classifier_unavailable signal means we degrade to deterministic
            # transitions only (the kernel falls through to ``OtherwiseCondition``).
            has_outcome_resolved = any(
                event.family == "routing" and event.name == "outcome_resolved"
                for event in interpreter_events
            )
            unavailable_event = next(
                (
                    event
                    for event in interpreter_events
                    if event.family == "routing" and event.name == "classifier_unavailable"
                ),
                None,
            )
            if has_outcome_resolved:
                semantic_events.extend(interpreter_events)
                decision_observability.controller_of_record = "interpreter_classifier"
                decision_observability.intent_source = "interpreter_transition_choice"
            else:
                # Classifier didn't pick a routable outcome. Record a clean
                # unknown rather than reaching for the deleted LLM cascade.
                if turn.text and turn.text.strip():
                    decision_observability.fallback_used = True
                    decision_observability.controller_of_record = "no_outcome_resolved"
                    decision_observability.intent_source = "none"
                    if unavailable_event is not None:
                        # Preserve the unavailable signal on the trace so
                        # operators can see why classification didn't run.
                        # The event is system-side — never reaches user text
                        # (the kernel only emits routing.outcome_resolved
                        # to drive transitions, not classifier_unavailable).
                        semantic_events.append(unavailable_event)
                        reason_payload = unavailable_event.payload or {}
                        reason = str(reason_payload.get("reason") or "classifier_unavailable")
                        strategy = str(reason_payload.get("strategy") or "")
                        decision_observability.fallback_reason = f"classifier_unavailable:{reason}"
                        decision_observability.degraded_mode = "classifier_unavailable"
                        logger.warning(
                            "classifier unavailable for step %s/%s (strategy=%s, reason=%s)",
                            conversation.agent_id,
                            step.id,
                            strategy or "unknown",
                            reason,
                        )
                    else:
                        decision_observability.fallback_reason = (
                            self._normalize_turn_fallback_reason(
                                classifier_metadata.get("fallback_reason"),
                                default="no_outcome_resolved",
                            )
                            if classifier_metadata.get("fallback_applied") is True
                            else "no_outcome_resolved"
                        )
                        decision_observability.degraded_mode = "classifier_no_outcome"

        for requirement in step.fact_requirements:
            if requirement.name not in {update.name for update in fact_updates} and requirement.name not in conversation.facts:
                semantic_events.append(
                    SemanticEventRecord(
                        family="fact_missing",
                        name=requirement.name,
                        source="deterministic",
                        confidence=1.0,
                    )
                )
        if text and not any(
            event.family == "routing" and event.name == "outcome_resolved"
            for event in semantic_events
        ):
            semantic_events.append(
                SemanticEventRecord(
                    family="uncertain_understanding",
                    name="fallback_text",
                    source="classifier",
                    confidence=0.3,
                )
            )
        if decision_observability.controller_of_record is None:
            decision_observability.controller_of_record = "deterministic_only"
        if decision_observability.intent_source is None:
            decision_observability.intent_source = "none"
        return semantic_events, fact_updates, decision_observability

    def _matched_step_transition(
        self,
        *,
        step: Step,
        semantic_events: list[SemanticEventRecord],
        working_facts: dict[str, object],
        turn: RuntimeTurn | None = None,
    ) -> StepTransition | None:
        event_keys = {event.key for event in semantic_events}
        chosen_outcome_event = self._chosen_outcome_event(semantic_events)
        for transition in sorted(step.transitions, key=lambda item: (item.priority, item.id)):
            if self._condition_matches(
                transition.when,
                event_keys,
                working_facts,
                turn,
                step=step,
                chosen_outcome_event=chosen_outcome_event,
            ):
                return transition
        return None

    def _matched_scenario_route(
        self,
        *,
        agent_document: CompiledAgentDocument,
        current_scenario_id: str,
        semantic_events: list[SemanticEventRecord],
        working_facts: dict[str, object],
        turn: RuntimeTurn | None = None,
    ) -> ScenarioRoute | None:
        event_keys = {event.key for event in semantic_events}
        chosen_outcome_event = self._chosen_outcome_event(semantic_events)
        routes = sorted(
            (
                route
                for route in agent_document.scenario_routes
                if route.from_scenario_id == current_scenario_id
            ),
            key=lambda item: (item.priority, item.id),
        )
        for route in routes:
            if self._condition_matches(
                route.when,
                event_keys,
                working_facts,
                turn,
                chosen_outcome_event=chosen_outcome_event,
            ):
                return route
        return None

    @staticmethod
    def _chosen_outcome_event(semantic_events: list[SemanticEventRecord]) -> str | None:
        """Extract the workflow-routing classifier's choice for this turn.

        Reads the most recent ``family="routing", name="outcome_resolved"``
        event and returns its ``payload["event"]``. Returns ``None`` when
        the classifier produced no event (off / unavailable / out-of-catalog
        all funnel here so ``OutcomeCondition`` simply won't fire).
        """
        for event in reversed(semantic_events):
            if event.family == "routing" and event.name == "outcome_resolved":
                payload = event.payload or {}
                value = payload.get("event")
                if isinstance(value, str) and value:
                    return value
                return None
        return None

    def _step_missing_fact_prompt(self, *, step: Step, missing_facts: list[str]) -> str:
        if not missing_facts:
            return "Could you share that detail?"
        missing_fact = missing_facts[0]
        normalized_name = missing_fact.replace("_", " ")
        for requirement in step.fact_requirements:
            if requirement.name != missing_fact:
                continue
            purpose = str(requirement.purpose or "").strip()
            if purpose:
                return f"Could you share your {normalized_name}? I need it {purpose}."
            break
        return f"Could you share your {normalized_name}?"

    def _step_response_fallback(self, *, step: Step) -> str | None:
        direct_answer_prompt = str(step.response_policy.direct_answer_prompt or "").strip()
        if direct_answer_prompt:
            return direct_answer_prompt
        if step.fact_requirements:
            return self._step_missing_fact_prompt(
                step=step,
                missing_facts=[requirement.name for requirement in step.fact_requirements],
            )
        return None

    def _execute_step_action_config(
        self,
        *,
        conversation: ConversationState,
        step: Step,
        turn: RuntimeTurn,
        working_facts: dict[str, object],
        semantic_events: list[SemanticEventRecord],
        tool_calls: list[ToolCallRecord],
    ) -> tuple[ActionRecord, list[RenderedMessage], list[ToolCallRecord]]:
        from .code_execution import execute_action_code, execute_action_code_inline
        from .tools.callable_aliases import build_action_config_callable_bindings

        config = step.action_config
        if config is None:
            return ActionRecord(type="stay", reason="no_step_action_config"), [], tool_calls

        internal_var_names = {
            "_last_user_text",
            "_turn_channel",
            "_turn_modality",
            "_step_id",
            "_conversation_id",
            "_organization_id",
        }
        sandbox_facts = dict(working_facts)
        sandbox_facts.update(
            {
                "_last_user_text": turn.text,
                "_turn_channel": turn.channel,
                "_turn_modality": turn.modality,
                "_step_id": step.id,
                "_conversation_id": conversation.conversation_id,
                "_organization_id": conversation.organization_id,
            }
        )

        bindings = build_action_config_callable_bindings(
            callable_api_refs=config.callable_api_refs,
            callable_system_refs=config.callable_system_refs,
            callable_integrations=config.callable_integrations,
        )
        category_names = set(bindings.integration_aliases)

        def tool_executor(fn_name: str, kwargs: dict) -> dict:
            if self._tool_runtime is None:
                return {"error": f"tool runtime not configured for {fn_name}"}
            if fn_name in category_names:
                integration_category = bindings.integration_aliases[fn_name]
                action = kwargs.get("action")
                if not action:
                    return {"error": f"integration '{fn_name}' requires an 'action' argument"}
                target_ref = f"{integration_category}.{action}"
                forwarded = {k: v for k, v in kwargs.items() if k != "action"}
            else:
                target_ref = bindings.direct_call_aliases.get(fn_name, fn_name)
                forwarded = kwargs

            call = ToolCall(
                tool_ref=target_ref,
                args=forwarded,
                caller=ToolCaller(
                    channel=turn.channel,
                    conversation_id=conversation.conversation_id,
                    step_id=step.id,
                    agent_id=conversation.agent_id,
                    tenant_id=conversation.organization_id,
                ),
            )
            try:
                result = self._tool_runtime.invoke(call)
                tool_calls.append(
                    ToolCallRecord(
                        invocation_id=result.invocation_id,
                        tool_ref=target_ref,
                        status=self._tool_result_record_status(result),
                        reason="step_action_tool_call",
                        payload={
                            "args": dict(forwarded),
                            "output": dict(result.output),
                            "metadata": dict(result.metadata),
                            "error": result.error,
                        },
                    )
                )
                if result.status == "success":
                    return dict(result.output)
                return {"error": result.error or result.status, "status": result.status}
            except KeyError:
                return {"error": f"tool '{target_ref}' is not registered"}
            except Exception as exc:
                return {"error": str(exc)}

        if step.execution_isolation == "inline":
            exec_result = execute_action_code_inline(
                code=config.code,
                callable_functions_code=config.callable_functions_code,
                conversation_facts=sandbox_facts,
                callable_function_names=bindings.callable_names,
                tool_executor=tool_executor,
            )
        else:
            exec_result = execute_action_code(
                code=config.code,
                callable_functions_code=config.callable_functions_code,
                conversation_facts=sandbox_facts,
                callable_function_names=bindings.callable_names,
                tool_executor=tool_executor,
                timeout_seconds=config.timeout_seconds,
            )

        for key, value in exec_result.variables_modified.items():
            if key in internal_var_names:
                continue
            working_facts[key] = value

        result_dict = exec_result.output if isinstance(exec_result.output, dict) else {}
        result_status = str(result_dict.get("status", exec_result.status))
        if exec_result.status == "success":
            semantic_events.append(
                SemanticEventRecord(
                    family="tool_outcome",
                    name=f"action_code_{result_status}",
                    source="tool",
                    confidence=1.0,
                    payload=result_dict,
                )
            )
            used_knowledge_lookup = (
                "hits" in result_dict
                or "context_block" in result_dict
                or "retrieval_mode" in result_dict
            )
            if used_knowledge_lookup:
                # Prefer LLM synthesis (the dialogue generator reads the
                # retrieved chunks via latest_action_outcome.user_visible_fields
                # and writes a real answer). Fall back to the deterministic
                # sentence extractor only if the generator is unavailable or
                # produces nothing usable.
                llm_text = self._render_knowledge_response_with_llm(
                    conversation=conversation,
                    step=step,
                    turn=turn,
                    result_dict=result_dict,
                    working_facts=working_facts,
                    semantic_events=semantic_events,
                )
                if llm_text:
                    emitted_messages = [RenderedMessage(text=llm_text)]
                else:
                    fallback_text = self._knowledge_runtime_reply(
                        result_output=result_dict,
                        user_text=turn.text or "",
                        failure_fallback_text=step.response_policy.deterministic_fallback_text,
                    )
                    emitted_messages = [RenderedMessage(text=fallback_text)] if fallback_text else []
            else:
                fallback = result_dict.get("message") or result_dict.get("summary")
                emitted_messages = (
                    [RenderedMessage(text=str(fallback).strip())]
                    if isinstance(fallback, str) and str(fallback).strip()
                    else []
                )
            return (
                ActionRecord(type="run_tool", reason=f"step_action_config:{result_status}"),
                emitted_messages,
                tool_calls,
            )

        semantic_events.append(
            SemanticEventRecord(
                family="tool_outcome",
                name=f"action_code_{exec_result.status}",
                source="tool",
                confidence=1.0,
                payload={"error": exec_result.error},
            )
        )
        emitted_messages = (
            [RenderedMessage(text=exec_result.error.strip())]
            if isinstance(exec_result.error, str) and exec_result.error.strip()
            else []
        )
        return (
            ActionRecord(type="run_tool", reason=f"step_action_config:{exec_result.status}"),
            emitted_messages,
            tool_calls,
        )

    def _execute_step_tool_policy(
        self,
        *,
        conversation: ConversationState,
        agent_document: CompiledAgentDocument,
        step: Step,
        turn: RuntimeTurn,
        working_facts: dict[str, object],
        semantic_events: list[SemanticEventRecord],
        tool_calls: list[ToolCallRecord],
    ) -> tuple[ActionRecord, list[RenderedMessage], list[ToolCallRecord]]:
        binding = self._select_tool_to_run(list(step.tool_policy or []))
        if binding is None or binding.invocation_strategy not in {"always", "on_missing_context"}:
            return ActionRecord(type="stay", reason="no_step_tool_policy"), [], tool_calls
        if self._tool_runtime is None:
            return ActionRecord(type="stay", reason="tool_runtime_not_configured"), [], tool_calls

        resolved_args = self._resolve_tool_args(
            binding,
            turn=turn,
            working_facts=working_facts,
            step_id=step.id,
            conversation=conversation,
        )
        call = ToolCall(
            tool_ref=binding.ref,
            args=resolved_args,
            caller=ToolCaller(
                channel=turn.channel,
                conversation_id=conversation.conversation_id,
                step_id=step.id,
                agent_id=conversation.agent_id,
                tenant_id=conversation.organization_id,
            ),
            dedupe_key=f"{turn.dedupe_key}:{step.id}:{binding.ref}",
            metadata={"binding_event_name": binding.event_name or binding.ref.replace(".", "_")},
        )
        try:
            result = self._tool_runtime.invoke(call)
        except Exception as exc:
            semantic_events.append(
                SemanticEventRecord(
                    family="tool_outcome",
                    name=f"{binding.ref.replace('.', '_')}_error",
                    source="tool",
                    confidence=1.0,
                    payload={"error": str(exc)},
                )
            )
            return (
                ActionRecord(type="run_tool", reason=f"step_tool_policy:{binding.ref}:error"),
                [RenderedMessage(text=str(exc))] if str(exc).strip() else [],
                tool_calls,
            )

        tool_calls.append(
            ToolCallRecord(
                invocation_id=result.invocation_id,
                tool_ref=binding.ref,
                status=self._tool_result_record_status(result),
                reason="step_tool_policy",
                payload={
                    "args": dict(resolved_args),
                    "output": dict(result.output),
                    "metadata": dict(result.metadata),
                    "error": result.error,
                },
            )
        )

        outcome_name = (
            binding.event_name
            or result.metadata.get("binding_event_name")
            or binding.ref.replace(".", "_")
        )
        semantic_events.append(
            SemanticEventRecord(
                family="tool_outcome",
                name=f"{outcome_name}_{result.status}",
                source="tool",
                confidence=1.0,
                payload=dict(result.output),
            )
        )

        # Apply the tool's output_mapping (declared on ToolDefinition.metadata
        # as {fact_name: extraction_expr}) to write facts back into the
        # working set. Failures here are silent — a misconfigured mapping
        # should not break the conversation; the tool result still routes.
        if result.status == "success":
            try:
                spec = self._tool_runtime.get_spec(
                    binding.ref,
                    organization_id=conversation.organization_id,
                    caller=call.caller,
                )
                mapped_values = self._resolve_tool_output_mapping(
                    output_mapping=spec.output_mapping,
                    output=result.output,
                )
                capture_result = self._fact_pipeline.process_candidates(
                    candidates=[
                        FactCandidate(
                            fact_name=fact_name,
                            raw_value=value,
                            source="tool",
                            evidence=None,
                            confidence=1.0,
                            source_ref=result.invocation_id,
                        )
                        for fact_name, value in mapped_values.items()
                    ],
                    turn_id=turn.turn_id,
                    step=step,
                    agent_document=agent_document,
                    existing_facts=working_facts,
                    existing_fact_metadata=dict(conversation.metadata.get("__ruhu_fact_metadata__", {})),
                    conversation_id=conversation.conversation_id,
                    organization_id=conversation.organization_id,
                )
                for update in capture_result.updates:
                    working_facts[update.name] = update.value
                    semantic_events.append(
                        SemanticEventRecord(
                            family="fact_updated",
                            name=update.name,
                            source=update.source,
                            confidence=update.confidence,
                        )
                    )
                if capture_result.new_fact_metadata:
                    fact_metadata = dict(conversation.metadata.get("__ruhu_fact_metadata__", {}))
                    fact_metadata.update(capture_result.new_fact_metadata)
                    conversation.metadata["__ruhu_fact_metadata__"] = fact_metadata
                self._route_capture_storage_writes(
                    conversation=conversation,
                    turn=turn,
                    storage_writes=capture_result.storage_writes,
                )
            except Exception:
                logger.debug("output_mapping resolution skipped for %s", binding.ref, exc_info=True)

        emitted_messages: list[RenderedMessage] = []
        payload = dict(result.output)
        used_knowledge_lookup = (
            binding.ref == "knowledge.lookup"
            or "hits" in payload
            or "context_block" in payload
            or "retrieval_mode" in payload
        )
        if used_knowledge_lookup:
            # Same LLM-first synthesis as the action_config path. Falls back
            # to the deterministic extractor only if the dialogue generator
            # is missing or its output is empty.
            llm_text = self._render_knowledge_response_with_llm(
                conversation=conversation,
                step=step,
                turn=turn,
                result_dict=payload,
                working_facts=working_facts,
                semantic_events=semantic_events,
            )
            if llm_text:
                emitted_messages.append(RenderedMessage(text=llm_text))
            else:
                fallback_text = self._knowledge_runtime_reply(
                    result_output=payload,
                    user_text=turn.text or "",
                    failure_fallback_text=step.response_policy.deterministic_fallback_text,
                )
                if fallback_text:
                    emitted_messages.append(RenderedMessage(text=fallback_text))
        else:
            fallback = payload.get("message") or payload.get("summary")
            if isinstance(fallback, str) and fallback.strip():
                emitted_messages.append(RenderedMessage(text=fallback.strip()))

        return (
            ActionRecord(type="run_tool", reason=f"step_tool_policy:{binding.ref}:{result.status}"),
            emitted_messages,
            tool_calls,
        )

    @staticmethod
    def _resolve_tool_output_mapping(
        *,
        output_mapping: dict[str, str],
        output: dict[str, object],
    ) -> dict[str, object]:
        if not output_mapping or not isinstance(output, dict):
            return {}
        resolved: dict[str, object] = {}
        for fact_name, expr in output_mapping.items():
            if not fact_name or not isinstance(expr, str):
                continue
            if expr.startswith("$."):
                value: object | None = output
                for part in expr[2:].split("."):
                    if isinstance(value, dict):
                        value = value.get(part)
                    else:
                        value = None
                        break
            else:
                value = output.get(expr)
            if value is None:
                continue
            resolved[fact_name] = value
        return resolved

    @staticmethod
    def _apply_tool_output_mapping(
        *,
        output_mapping: dict[str, str],
        output: dict[str, object],
        working_facts: dict[str, object],
    ) -> None:
        """Apply a tool's ``output_mapping`` to its result, mutating
        ``working_facts`` in place. Each mapping entry is
        ``fact_name → expr``. Expressions starting with ``$.`` are dotted
        paths into the result; everything else is a top-level key. Values
        that resolve to None are skipped — a missing path should not
        clear an existing fact."""
        working_facts.update(
            ConversationKernel._resolve_tool_output_mapping(
                output_mapping=output_mapping,
                output=output,
            )
        )

    def _route_capture_storage_writes(
        self,
        *,
        conversation: ConversationState,
        turn: RuntimeTurn,
        storage_writes: dict[str, dict[str, FactUpdate]],
    ) -> None:
        if not storage_writes:
            return
        result = self._capture_storage_router.apply(
            storage_writes=storage_writes,
            conversation_metadata=conversation.metadata,
            turn_metadata=turn.metadata,
        )
        self._observe_capture_storage_routing(result.routed_counts)

    @staticmethod
    def _observe_capture_storage_routing(routed_counts: dict[str, int]) -> None:
        if not routed_counts:
            return
        try:
            from .observability.metrics import capture_storage_writes_total

            for scope, count in routed_counts.items():
                capture_storage_writes_total.labels(scope=scope).inc(count)
        except Exception:
            pass

    def _apply_step_conversation_lifecycle(
        self,
        *,
        conversation: ConversationState,
        agent_document: CompiledAgentDocument,
        step_after: str,
    ) -> None:
        step = agent_document.step_by_id(step_after)
        if step.handoff is not None:
            conversation.status = "ended"
            conversation.outcome = "transferred"
            if conversation.ended_at is None:
                conversation.ended_at = conversation.updated_at
            return
        if step.completion is not None:
            conversation.status = "ended"
            conversation.outcome = self._normalize_conversation_outcome(step.completion.disposition)
            if conversation.ended_at is None:
                conversation.ended_at = conversation.updated_at
            return
        conversation.status = "active"
        conversation.outcome = None
        conversation.ended_at = None

    def confirm_tool_invocation(
        self,
        conversation_id: str,
        agent_document: AgentDocument,
        invocation_id: str,
        *,
        agent_name: str | None = None,
    ) -> RuntimeTurnResult:
        compiled_agent_document = compile_agent_document(agent_document)
        conversation = self._conversation_store.load(conversation_id)
        if conversation is None:
            raise KeyError(conversation_id)
        pending_rule_confirmation = self._get_pending_rule_confirmation(
            conversation=conversation,
            confirmation_token=invocation_id,
        )
        if pending_rule_confirmation is not None:
            return self._confirm_pending_rule_confirmation_for_step(
                conversation=conversation,
                agent_document=compiled_agent_document,
                pending_confirmation=pending_rule_confirmation,
            )
        # Clear pending action if this programmatic confirmation matches
        pending_action = self._get_pending_action(conversation)
        if pending_action and pending_action.action_id == invocation_id:
            self._clear_pending_action(conversation)
        if self._tool_runtime is None:
            raise RuntimeError("tool runtime is not configured")
        invocation = self._tool_runtime.store.load(
            invocation_id,
            organization_id=conversation.organization_id,
        )
        if (
            invocation is None
            or invocation.caller.conversation_id != conversation_id
            or invocation.caller.tenant_id != conversation.organization_id
        ):
            raise KeyError(invocation_id)
        current_step = compiled_agent_document.step_by_id(conversation.step_id)
        callback_turn = RuntimeTurn(
            turn_id=f"{invocation_id}:confirm",
            dedupe_key=f"{invocation_id}:confirm",
            channel=invocation.caller.channel,
            modality="event",
            event_type="tool_callback",
            metadata={"invocation_id": invocation_id, "decision": "confirm"},
            received_at=datetime.now(timezone.utc),
        )
        result = self._tool_runtime.confirm(invocation_id)
        return self._process_step_tool_runtime_result(
            conversation=conversation,
            agent_document=compiled_agent_document,
            step_before=conversation.step_id,
            action_step=current_step,
            turn=callback_turn,
            turn_id=f"{invocation_id}:confirm",
            dedupe_key=f"{invocation_id}:confirm",
            semantic_events=[],
            fact_updates=[],
            working_facts=dict(conversation.facts),
            binding=self._select_tool_to_run(list(current_step.tool_policy or [])),
            result=result,
            resolved_args=dict(invocation.args),
        )

    def cancel_tool_invocation(
        self,
        conversation_id: str,
        agent_document: AgentDocument,
        invocation_id: str,
        *,
        agent_name: str | None = None,
    ) -> RuntimeTurnResult:
        compiled_agent_document = compile_agent_document(agent_document)
        conversation = self._conversation_store.load(conversation_id)
        if conversation is None:
            raise KeyError(conversation_id)
        pending_rule_confirmation = self._pop_pending_rule_confirmation(
            conversation=conversation,
            confirmation_token=invocation_id,
        )
        if pending_rule_confirmation is not None:
            self._clear_pending_permission(conversation)
            current_step = compiled_agent_document.step_by_id(conversation.step_id)
            return self._process_completed_step_turn(
                conversation=conversation,
                agent_document=compiled_agent_document,
                turn=RuntimeTurn(
                    turn_id=f"{invocation_id}:cancel",
                    dedupe_key=f"{invocation_id}:cancel",
                    channel=pending_rule_confirmation.channel or conversation.channel or "web_chat",
                    modality="event",
                    event_type="tool_callback",
                    metadata={"confirmation_token": invocation_id, "decision": "cancel", "confirmation_kind": "rule"},
                    received_at=datetime.now(timezone.utc),
                ),
                turn_id=f"{invocation_id}:cancel",
                dedupe_key=f"{invocation_id}:cancel",
                step_before=current_step.id,
                step_after=current_step.id,
                semantic_events=[
                    self._build_rule_tool_semantic_event(
                        name="tool_confirmation_cancelled",
                        tool_ref=pending_rule_confirmation.tool_ref,
                        code="rule_confirmation_cancelled",
                        terminal_match=None,
                    ),
                    SemanticEventRecord(
                        family="interaction",
                        name="permission_resolved",
                        source="system",
                        confidence=1.0,
                        payload={
                            "request_id": invocation_id,
                            "permission_kind": "rule_confirmation",
                            "resolution": "denied",
                        },
                    ),
                ],
                fact_updates=[],
                chosen_action=ActionRecord(
                    type="run_tool",
                    reason="rule_confirmation_cancelled",
                    payload={"tool": pending_rule_confirmation.tool_ref, "confirmation_token": invocation_id},
                ),
                emitted_messages=[],
                tool_calls=[
                    ToolCallRecord(
                        invocation_id=invocation_id,
                        tool_ref=pending_rule_confirmation.tool_ref,
                        status="cancelled",
                        reason="rule_confirmation_cancelled",
                        payload={"confirmation_token": invocation_id, "confirmation_kind": "rule"},
                    )
                ],
                working_facts=dict(conversation.facts),
                rules=RuntimeRulesTrace(),
            )
        if self._tool_runtime is None:
            raise RuntimeError("tool runtime is not configured")
        invocation = self._tool_runtime.store.load(
            invocation_id,
            organization_id=conversation.organization_id,
        )
        if (
            invocation is None
            or invocation.caller.conversation_id != conversation_id
            or invocation.caller.tenant_id != conversation.organization_id
        ):
            raise KeyError(invocation_id)
        callback_turn = RuntimeTurn(
            turn_id=f"{invocation_id}:cancel",
            dedupe_key=f"{invocation_id}:cancel",
            channel=invocation.caller.channel,
            modality="event",
            event_type="tool_callback",
            metadata={"invocation_id": invocation_id, "decision": "cancel"},
            received_at=datetime.now(timezone.utc),
        )
        result = self._tool_runtime.cancel(invocation_id)
        current_step = compiled_agent_document.step_by_id(conversation.step_id)
        return self._process_step_tool_runtime_result(
            conversation=conversation,
            agent_document=compiled_agent_document,
            step_before=conversation.step_id,
            action_step=current_step,
            turn=callback_turn,
            turn_id=f"{invocation_id}:cancel",
            dedupe_key=f"{invocation_id}:cancel",
            semantic_events=[],
            fact_updates=[],
            working_facts=dict(conversation.facts),
            binding=self._select_tool_to_run(list(current_step.tool_policy or [])),
            result=result,
            resolved_args=dict(invocation.args),
        )

    def reconcile_tool_invocation_result(
        self,
        conversation_id: str,
        agent_document: AgentDocument,
        invocation_id: str,
        *,
        agent_name: str | None = None,
    ) -> RuntimeTurnResult:
        compiled_agent_document = compile_agent_document(agent_document)
        conversation = self._conversation_store.load(conversation_id)
        if conversation is None:
            raise KeyError(conversation_id)
        if self._tool_runtime is None:
            raise RuntimeError("tool runtime is not configured")
        invocation = self._tool_runtime.store.load(
            invocation_id,
            organization_id=conversation.organization_id,
        )
        if (
            invocation is None
            or invocation.caller.conversation_id != conversation_id
            or invocation.caller.tenant_id != conversation.organization_id
        ):
            raise KeyError(invocation_id)
        result = self._tool_runtime.load_result(invocation_id, organization_id=conversation.organization_id)
        if result is None:
            raise KeyError(invocation_id)
        current_step = compiled_agent_document.step_by_id(conversation.step_id)
        if not current_step.tool_policy:
            raise ValueError("tool callback reconciliation requires a tool-execution step")
        pending_action = self._get_pending_action(conversation)
        if pending_action is not None and pending_action.action_id == invocation_id:
            self._clear_pending_action(conversation)
            self._clear_pending_permission(conversation)
        callback_turn = RuntimeTurn(
            turn_id=f"{invocation_id}:callback",
            dedupe_key=f"{invocation_id}:callback",
            channel=invocation.caller.channel,
            modality="event",
            event_type="tool_callback",
            metadata={
                "invocation_id": invocation_id,
                "tool_ref": invocation.tool_ref,
                "tool_name": invocation.tool_ref,
                "outcome": result.status,
                "output": dict(result.output),
                "error": result.error,
            },
            received_at=datetime.now(timezone.utc),
        )
        return self._process_step_tool_runtime_result(
            conversation=conversation,
            agent_document=compiled_agent_document,
            step_before=conversation.step_id,
            action_step=current_step,
            turn=callback_turn,
            turn_id=f"{invocation_id}:callback",
            dedupe_key=f"{invocation_id}:callback",
            semantic_events=[],
            fact_updates=[],
            working_facts=dict(conversation.facts),
            binding=self._select_tool_to_run(list(current_step.tool_policy or [])),
            result=result,
            resolved_args=dict(invocation.args),
        )

    def project_tool_invocation_progress(
        self,
        conversation_id: str,
        agent_document: AgentDocument,
        invocation_id: str,
        *,
        agent_name: str | None = None,
    ) -> RuntimeTurnResult | None:
        compiled_agent_document = compile_agent_document(agent_document)
        conversation = self._conversation_store.load(conversation_id)
        if conversation is None:
            raise KeyError(conversation_id)
        if self._tool_runtime is None:
            raise RuntimeError("tool runtime is not configured")
        invocation = self._tool_runtime.store.load(
            invocation_id,
            organization_id=conversation.organization_id,
        )
        if (
            invocation is None
            or invocation.caller.conversation_id != conversation_id
            or invocation.caller.tenant_id != conversation.organization_id
        ):
            raise KeyError(invocation_id)
        pending_action = self._get_pending_action(conversation)
        if pending_action is None or pending_action.action_id != invocation_id:
            return None

        projected_status = self._pending_action_status_for_tool_invocation(invocation)
        if projected_status is None:
            return None

        status_changed = pending_action.status != projected_status
        payload_changed = self._sync_pending_action_progress(
            conversation=conversation,
            pending_action=pending_action,
            invocation=invocation,
            projected_status=projected_status,
        )
        if not status_changed and not payload_changed:
            return None

        conversation.updated_at = datetime.now(timezone.utc)
        current_step = compiled_agent_document.step_by_id(conversation.step_id)
        semantic_events: list[SemanticEventRecord] = []
        self._append_interaction_event(
            semantic_events,
            name="activity_progressed",
            payload={
                "action_id": pending_action.action_id,
                "tool_ref": pending_action.tool_ref,
                "pending_status": pending_action.status,
                "invocation_status": invocation.status,
                "publish_status_trail": bool(pending_action.metadata.get("publish_status_trail")),
                **(
                    {"external_job_id": pending_action.metadata.get("external_job_id")}
                    if pending_action.metadata.get("external_job_id")
                    else {}
                ),
                **(
                    {"integration_resolution_mode": pending_action.metadata.get("integration_resolution_mode")}
                    if pending_action.metadata.get("integration_resolution_mode")
                    else {}
                ),
            },
        )
        status_trail_items = self._projected_status_trail_items(conversation, semantic_events)
        if status_trail_items:
            self._append_interaction_event(
                semantic_events,
                name="status_trail_updated",
                payload={"items": status_trail_items},
            )

        with self._shared_store_transaction() as shared_session:
            self._save_conversation(conversation, shared_session=shared_session)

        synthetic_turn = RuntimeTurn(
            turn_id=f"{invocation_id}:progress:{pending_action.status}",
            dedupe_key=f"{invocation_id}:progress:{pending_action.status}",
            channel=invocation.caller.channel,
            modality="event",
            event_type="system_event",
            metadata={
                "invocation_id": invocation_id,
                "tool_ref": invocation.tool_ref,
                "projected_status": pending_action.status,
            },
            received_at=conversation.updated_at,
        )
        result = RuntimeTurnResult(
            turn_id=synthetic_turn.turn_id,
            conversation_id=conversation.conversation_id,
            step_before=conversation.step_id,
            step_after=conversation.step_id,
            semantic_events=semantic_events,
            fact_updates=[],
            chosen_action=ActionRecord(
                type="stay",
                reason="pending_action_progress_projected",
                payload={"action_id": invocation_id, "status": pending_action.status},
            ),
            emitted_messages=[],
            tool_calls=[],
            trace_id=str(uuid4()),
            latency_breakdown_ms={"total": 0},
            interaction_debug_snapshot=self._interaction_debug_snapshot_for_step(
                conversation=conversation,
                step=current_step,
                channel=invocation.caller.channel,
            ),
        )
        self._record_realtime_turn(
            conversation=conversation,
            turn=synthetic_turn,
            result=result,
        )
        return result

    @staticmethod
    def _pending_action_status_for_tool_invocation(
        invocation: ToolInvocation,
    ) -> str | None:
        status = invocation.status
        if status in {
            "queued",
            "running",
            "waiting_poll",
            "waiting_webhook",
            "retry_scheduled",
        }:
            return status
        return None

    @staticmethod
    def _pending_action_commitment_for_status(
        *,
        label: str,
        projected_status: str,
    ) -> tuple[str, str]:
        if projected_status == "queued":
            return "pending_external", f"{label} is queued and waiting to start."
        if projected_status == "running":
            return "pending_external", f"{label} is now running."
        if projected_status == "waiting_poll":
            return "pending_external", f"{label} is running and I’ll keep checking for progress."
        if projected_status == "waiting_webhook":
            return "pending_external", f"{label} is running and I’m waiting for the provider to call back."
        if projected_status == "retry_scheduled":
            return "failed_retryable", f"{label} is delayed, and I’ll retry it shortly."
        return "pending_external", f"{label} is still in progress."

    def _sync_pending_action_progress(
        self,
        *,
        conversation: ConversationState,
        pending_action: PendingActionState,
        invocation: ToolInvocation,
        projected_status: str,
    ) -> bool:
        changed = False
        now = datetime.now(timezone.utc)
        label = pending_action.action_label or pending_action.tool_ref or pending_action.action_type or "that request"
        if pending_action.status != projected_status:
            pending_action.status = projected_status
            changed = True
        pending_action.last_progress_at = now

        commitment_status, commitment_summary = self._pending_action_commitment_for_status(
            label=label,
            projected_status=projected_status,
        )
        if pending_action.commitment.get("status") != commitment_status or pending_action.commitment.get("summary") != commitment_summary:
            pending_action.commitment = {
                "status": commitment_status,
                "summary": commitment_summary,
            }
            changed = True

        integration_job_id = invocation.metadata.get("integration_job_id")
        integration_resolution_mode = invocation.metadata.get("integration_resolution_mode")
        external_job_id = invocation.metadata.get("external_job_id")
        callback_correlation_id = invocation.metadata.get("callback_correlation_id")
        next_poll_at = invocation.metadata.get("next_poll_at")
        next_retry_at = invocation.metadata.get("next_retry_at")
        progress_keys = {
            "integration_job_id": integration_job_id,
            "integration_status": invocation.status,
            "integration_resolution_mode": integration_resolution_mode,
            "external_job_id": external_job_id,
            "callback_correlation_id": callback_correlation_id,
            "next_poll_at": next_poll_at,
            "next_retry_at": next_retry_at,
        }
        for key, value in progress_keys.items():
            if pending_action.metadata.get(key) != value:
                if value is None:
                    pending_action.metadata.pop(key, None)
                else:
                    pending_action.metadata[key] = value
                changed = True

        if projected_status == "retry_scheduled":
            self._set_active_repair(
                conversation=conversation,
                repair_kind="provider_uncertainty_repair",
                target_ref=pending_action.tool_ref or pending_action.action_id,
                summary=commitment_summary,
            )
        elif (
            conversation.control_state.active_repair is not None
            and conversation.control_state.active_repair.repair_kind == "provider_uncertainty_repair"
            and conversation.control_state.active_repair.target_ref in {pending_action.tool_ref, pending_action.action_id}
        ):
            self._clear_active_repair(conversation)
        return changed

    def _duplicate_result(self, conversation: ConversationState, turn: RuntimeTurn) -> RuntimeTurnResult:
        return RuntimeTurnResult(
            turn_id=turn.turn_id,
            conversation_id=conversation.conversation_id,
            step_before=conversation.step_id,
            step_after=conversation.step_id,
            chosen_action=ActionRecord(type="stay", reason="duplicate_dedupe_key"),
            emitted_messages=[],
            trace_id=str(uuid4()),
            latency_breakdown_ms={"total": 0},
        )

    def _invoke_step_tool(
        self,
        *,
        conversation: ConversationState,
        agent_document: CompiledAgentDocument,
        step_before: str,
        action_step: Step,
        turn: RuntimeTurn,
        semantic_events: list[SemanticEventRecord],
        fact_updates: list[FactUpdate],
        working_facts: dict[str, object],
        rules: RuntimeRulesTrace | None = None,
        resolved_args_override: dict[str, object] | None = None,
        trace_semantic_events: list[SemanticEventRecord] | None = None,
    ) -> RuntimeTurnResult:
        binding = self._select_tool_to_run(list(action_step.tool_policy or []))
        if binding is None:
            fallback_msg = "I'm not able to perform that action right now. How else can I help?"
            return self._process_completed_step_turn(
                conversation=conversation,
                agent_document=agent_document,
                turn=turn,
                turn_id=turn.turn_id,
                dedupe_key=turn.dedupe_key,
                step_before=step_before,
                step_after=action_step.id,
                semantic_events=semantic_events,
                fact_updates=fact_updates,
                chosen_action=ActionRecord(type="stay", reason="step_action_without_tool"),
                emitted_messages=[RenderedMessage(text=fallback_msg)],
                tool_calls=[],
                working_facts=working_facts,
                rules=rules,
                trace_semantic_events=trace_semantic_events,
            )

        resolved_args = (
            dict(resolved_args_override)
            if resolved_args_override is not None
            else self._resolve_tool_args(
                binding,
                turn=turn,
                working_facts=working_facts,
                step_id=action_step.id,
                conversation=conversation,
            )
        )

        rules = self._evaluate_before_tool_rules(
            conversation=conversation,
            turn=turn,
            binding=binding,
            resolved_args=resolved_args,
            existing_rules=rules,
        )
        stage_decision = rules.evaluations[-1] if rules and rules.evaluations else None
        if (
            stage_decision is not None
            and stage_decision.stage == "before_tool"
            and stage_decision.terminal_effect is not None
        ):
            terminal_match = self._terminal_rule_match(stage_decision)
            terminal_effect = stage_decision.terminal_effect
            semantic_events_with_rule = list(semantic_events)
            if terminal_effect.kind in {"block", "suppress_tool"}:
                semantic_events_with_rule.append(
                    self._build_rule_tool_semantic_event(
                        name="tool_blocked" if terminal_effect.kind == "block" else "tool_suppressed",
                        tool_ref=binding.ref,
                        code=terminal_effect.code,
                        terminal_match=terminal_match,
                    )
                )
                emitted_messages = []
                if terminal_effect.message:
                    emitted_messages.append(RenderedMessage(text=terminal_effect.message))
                return self._process_completed_step_turn(
                    conversation=conversation,
                    agent_document=agent_document,
                    turn=turn,
                    turn_id=turn.turn_id,
                    dedupe_key=turn.dedupe_key,
                    step_before=step_before,
                    step_after=action_step.id,
                    semantic_events=semantic_events_with_rule,
                    fact_updates=fact_updates,
                    chosen_action=ActionRecord(
                        type="run_tool",
                        reason=f"rule_{'blocked' if terminal_effect.kind == 'block' else 'suppressed'}:{terminal_effect.code}",
                        payload={"tool": binding.ref},
                    ),
                    emitted_messages=emitted_messages,
                    tool_calls=[
                        ToolCallRecord(
                            tool_ref=binding.ref,
                            status="blocked",
                            reason=f"rule_{'blocked' if terminal_effect.kind == 'block' else 'suppressed'}:{terminal_effect.code}",
                            payload=self._rule_tool_call_payload(
                                terminal_match=terminal_match,
                                tool_ref=binding.ref,
                            ),
                        )
                    ],
                    working_facts=working_facts,
                    rules=rules,
                    trace_semantic_events=trace_semantic_events,
                )
            if terminal_effect.kind == "require_confirmation":
                pending_confirmation = self._create_pending_rule_confirmation(
                    conversation=conversation,
                    turn=turn,
                    step_id=action_step.id,
                    binding=binding,
                    resolved_args=resolved_args,
                    terminal_match=terminal_match,
                )
                self._set_pending_permission(
                    conversation=conversation,
                    request_id=pending_confirmation.confirmation_token,
                    permission_kind="rule_confirmation",
                    target_ref=binding.ref,
                    user_visible_context={"code": terminal_effect.code, "tool_ref": binding.ref},
                )
                semantic_events_with_rule.append(
                    self._build_rule_tool_semantic_event(
                        name="tool_confirmation_required",
                        tool_ref=binding.ref,
                        code=terminal_effect.code,
                        terminal_match=terminal_match,
                    )
                )
                self._append_interaction_event(
                    semantic_events_with_rule,
                    name="permission_requested",
                    payload={
                        "step_id": action_step.id,
                        "tool_ref": binding.ref,
                        "confirmation_token": pending_confirmation.confirmation_token,
                        "permission_kind": "rule_confirmation",
                    },
                )
                emitted_messages = []
                if terminal_effect.message:
                    emitted_messages.append(RenderedMessage(text=terminal_effect.message))
                payload = self._rule_tool_call_payload(
                    terminal_match=terminal_match,
                    tool_ref=binding.ref,
                )
                payload.update(
                    {
                        "confirmation_token": pending_confirmation.confirmation_token,
                        "confirmation_kind": "rule",
                    }
                )
                return self._process_completed_step_turn(
                    conversation=conversation,
                    agent_document=agent_document,
                    turn=turn,
                    turn_id=turn.turn_id,
                    dedupe_key=turn.dedupe_key,
                    step_before=step_before,
                    step_after=action_step.id,
                    semantic_events=semantic_events_with_rule,
                    fact_updates=fact_updates,
                    chosen_action=ActionRecord(
                        type="run_tool",
                        reason=f"rule_confirmation_required:{terminal_effect.code}",
                        payload={"tool": binding.ref, "confirmation_token": pending_confirmation.confirmation_token},
                    ),
                    emitted_messages=emitted_messages,
                    tool_calls=[
                        ToolCallRecord(
                            invocation_id=pending_confirmation.confirmation_token,
                            tool_ref=binding.ref,
                            status="confirmation_required",
                            reason=f"rule_confirmation_required:{terminal_effect.code}",
                            payload=payload,
                        )
                    ],
                    working_facts=working_facts,
                    rules=rules,
                    trace_semantic_events=trace_semantic_events,
                )

        if self._tool_runtime is None:
            return self._process_completed_step_turn(
                conversation=conversation,
                agent_document=agent_document,
                turn=turn,
                turn_id=turn.turn_id,
                dedupe_key=turn.dedupe_key,
                step_before=step_before,
                step_after=action_step.id,
                semantic_events=semantic_events,
                fact_updates=fact_updates,
                chosen_action=ActionRecord(
                    type="run_tool",
                    reason=f"tool_requested:{binding.ref}",
                    payload={"tool": binding.ref},
                ),
                emitted_messages=[RenderedMessage(text="I'm unable to complete that action at the moment. Can I help with something else?")],
                tool_calls=[
                    ToolCallRecord(
                        tool_ref=binding.ref,
                        status="requested",
                        reason="tool_runtime_not_configured",
                        payload={"strategy": binding.invocation_strategy},
                    )
                ],
                working_facts=working_facts,
                rules=rules,
                trace_semantic_events=trace_semantic_events,
            )

        call = ToolCall(
            tool_ref=binding.ref,
            args=resolved_args,
            caller=ToolCaller(
                channel=turn.channel,
                conversation_id=conversation.conversation_id,
                step_id=action_step.id,
                agent_id=conversation.agent_id,
                tenant_id=conversation.organization_id,
            ),
            dedupe_key=f"{turn.dedupe_key}:{action_step.id}:{binding.ref}",
            metadata={"binding_event_name": binding.event_name or binding.ref.replace(".", "_")},
        )
        self._append_interaction_event(
            semantic_events,
            name="activity_started",
            payload={
                "step_id": action_step.id,
                "tool_ref": binding.ref,
                "action_type": "tool_invocation",
            },
        )
        result = self._tool_runtime.invoke(call)
        return self._process_step_tool_runtime_result(
            conversation=conversation,
            agent_document=agent_document,
            step_before=step_before,
            action_step=action_step,
            turn=turn,
            turn_id=turn.turn_id,
            dedupe_key=turn.dedupe_key,
            semantic_events=semantic_events,
            fact_updates=fact_updates,
            working_facts=working_facts,
            binding=binding,
            result=result,
            rules=rules,
            resolved_args=resolved_args,
            trace_semantic_events=trace_semantic_events,
        )

    def _process_step_tool_runtime_result(
        self,
        *,
        conversation: ConversationState,
        agent_document: CompiledAgentDocument,
        step_before: str,
        action_step: Step,
        turn: RuntimeTurn,
        turn_id: str,
        dedupe_key: str,
        semantic_events: list[SemanticEventRecord],
        fact_updates: list[FactUpdate],
        working_facts: dict[str, object],
        binding: ToolBinding | None,
        result: ToolResult,
        rules: RuntimeRulesTrace | None = None,
        resolved_args: dict[str, object] | None = None,
        trace_semantic_events: list[SemanticEventRecord] | None = None,
    ) -> RuntimeTurnResult:
        accumulated_rules = rules.model_copy(deep=True) if rules is not None else RuntimeRulesTrace()
        tool_calls = [
            ToolCallRecord(
                invocation_id=result.invocation_id,
                tool_ref=result.tool_ref,
                status=self._tool_result_record_status(result),
                reason=f"tool_runtime:{result.status}",
                payload={
                    "output": dict(result.output),
                    "error": result.error,
                    "metadata": dict(result.metadata),
                },
            )
        ]
        emitted_messages: list[RenderedMessage] = []
        updated_semantic_events = list(semantic_events)
        updated_fact_updates = list(fact_updates)
        updated_working_facts = dict(working_facts)
        accumulated_rules = self._evaluate_after_tool_rules(
            conversation=conversation,
            turn=turn,
            step_id=action_step.id,
            result=result,
            resolved_args=resolved_args,
            existing_rules=accumulated_rules,
            working_facts=working_facts,
        )
        stage_decision = accumulated_rules.evaluations[-1] if accumulated_rules.evaluations else None
        if stage_decision is not None and stage_decision.stage == "after_tool" and stage_decision.terminal_effect is not None:
            terminal_match = self._terminal_rule_match(stage_decision)
            terminal_effect = stage_decision.terminal_effect
            updated_semantic_events.append(
                self._build_rule_tool_semantic_event(
                    name="tool_result_blocked",
                    tool_ref=result.tool_ref,
                    code=terminal_effect.code,
                    terminal_match=terminal_match,
                )
            )
            message_text = terminal_effect.message or (
                phrase_for("policy_blocked", channel=turn.channel or "web_chat", seed=terminal_effect.code)
                or "I can’t continue with that until the required policy step is resolved."
            )
            emitted_messages = [RenderedMessage(text=message_text)] if message_text else []
            return self._process_completed_step_turn(
                conversation=conversation,
                agent_document=agent_document,
                turn=turn,
                turn_id=turn_id,
                dedupe_key=dedupe_key,
                step_before=step_before,
                step_after=action_step.id,
                semantic_events=updated_semantic_events,
                fact_updates=fact_updates,
                chosen_action=ActionRecord(
                    type="run_tool",
                    reason=f"rule_blocked:{terminal_effect.code}",
                    payload={"tool": result.tool_ref, "invocation_id": result.invocation_id},
                ),
                emitted_messages=emitted_messages,
                tool_calls=tool_calls,
                working_facts=working_facts,
                rules=accumulated_rules,
                trace_semantic_events=trace_semantic_events,
            )

        if self._is_deferred_tool_result(result):
            pending_label = action_step.name or (binding.ref if binding is not None else result.tool_ref)
            pending = self._create_pending_action(
                conversation=conversation,
                action_type="tool_invocation",
                tool_ref=result.tool_ref,
                action_label=binding.ref if binding is not None else result.tool_ref,
                activity_label=action_step.name,
                activity_guidance=action_step.say,
                invocation_id=result.invocation_id,
                metadata={
                    "resolved_args": dict(resolved_args or {}),
                    "publish_status_trail": True,
                    "integration_job_id": result.metadata.get("integration_job_id"),
                    "integration_resolution_mode": result.metadata.get("integration_resolution_mode"),
                },
                initial_status="queued",
                commitment_status="pending_external",
                commitment_summary=f"{pending_label} is queued and running in the background.",
            )
            queued_text = (
                f"I've started {pending.action_label or pending.tool_ref or 'that request'}, "
                "and I'll keep it moving in the background."
            )
            return self._process_completed_step_turn(
                conversation=conversation,
                agent_document=agent_document,
                turn=turn,
                turn_id=turn_id,
                dedupe_key=dedupe_key,
                step_before=step_before,
                step_after=action_step.id,
                semantic_events=updated_semantic_events,
                fact_updates=updated_fact_updates,
                chosen_action=ActionRecord(
                    type="run_tool",
                    reason=f"tool_deferred:{result.tool_ref}",
                    payload={
                        "tool": result.tool_ref,
                        "invocation_id": result.invocation_id,
                        "integration_job_id": result.metadata.get("integration_job_id"),
                    },
                ),
                emitted_messages=[RenderedMessage(text=queued_text)],
                tool_calls=tool_calls,
                working_facts=updated_working_facts,
                rules=accumulated_rules,
                trace_semantic_events=trace_semantic_events,
            )

        if result.status == "confirmation_required":
            confirmation_label = binding.ref if binding is not None else result.tool_ref
            if (turn.channel in ("phone", "voice") or turn.modality == "audio") and result.invocation_id:
                try:
                    confirmed_result = self._tool_runtime.confirm(result.invocation_id)
                    return self._process_step_tool_runtime_result(
                        conversation=conversation,
                        agent_document=agent_document,
                        step_before=step_before,
                        action_step=action_step,
                        turn=turn,
                        turn_id=turn_id,
                        dedupe_key=dedupe_key,
                        semantic_events=semantic_events,
                        fact_updates=fact_updates,
                        working_facts=working_facts,
                        binding=binding,
                        result=confirmed_result,
                        rules=rules,
                        resolved_args=resolved_args,
                        trace_semantic_events=trace_semantic_events,
                    )
                except Exception:
                    pass

            self._create_pending_action(
                conversation=conversation,
                action_type="tool_confirmation",
                tool_ref=result.tool_ref,
                action_label=confirmation_label,
                activity_label=action_step.name,
                activity_guidance=action_step.say,
                invocation_id=result.invocation_id,
                metadata={
                    "resolved_args": dict(resolved_args or {}),
                    "publish_status_trail": True,
                },
            )
            self._append_interaction_event(
                updated_semantic_events,
                name="permission_requested",
                payload={
                    "step_id": action_step.id,
                    "tool_ref": result.tool_ref,
                    "invocation_id": result.invocation_id,
                    "permission_kind": "tool_confirmation",
                },
            )
            confirmation_prompt: str | None = None
            if self._tool_runtime is not None:
                spec = self._tool_runtime.get_spec(
                    result.tool_ref,
                    organization_id=conversation.organization_id,
                    caller=ToolCaller(
                        channel=turn.channel,
                        conversation_id=conversation.conversation_id,
                        tenant_id=conversation.organization_id,
                        user_id=conversation.metadata.get("user_id"),
                        agent_id=conversation.agent_id,
                        step_id=action_step.id,
                    ),
                )
                if spec:
                    confirmation_prompt = spec.confirmation_prompt
            if confirmation_prompt:
                emitted_messages.append(RenderedMessage(text=confirmation_prompt))
            else:
                emitted_messages.append(
                    RenderedMessage(
                        text=f"Shall I go ahead and {confirmation_label.replace('.', ' ').replace('_', ' ')}?"
                    )
                )
            return self._process_completed_step_turn(
                conversation=conversation,
                agent_document=agent_document,
                turn=turn,
                turn_id=turn_id,
                dedupe_key=dedupe_key,
                step_before=step_before,
                step_after=action_step.id,
                semantic_events=updated_semantic_events,
                fact_updates=updated_fact_updates,
                chosen_action=ActionRecord(
                    type="run_tool",
                    reason=f"tool_confirmation_required:{result.tool_ref}",
                    payload={"tool": result.tool_ref, "invocation_id": result.invocation_id},
                ),
                emitted_messages=emitted_messages,
                tool_calls=tool_calls,
                working_facts=updated_working_facts,
                rules=accumulated_rules,
                trace_semantic_events=trace_semantic_events,
            )

        if result.status == "success":
            self._clear_active_repair(conversation)
            self._append_interaction_event(
                updated_semantic_events,
                name="activity_completed",
                payload={
                    "step_id": action_step.id,
                    "tool_ref": result.tool_ref,
                    "invocation_id": result.invocation_id,
                },
            )
            for key, value in dict(result.output.get("facts") or {}).items():
                updated_fact_updates.append(
                    FactUpdate(name=key, value=value, source="system", confidence=1.0)
                )
                updated_working_facts[key] = value

            knowledge_output = dict(result.output) if isinstance(result.output, dict) else None
            raw_tool_message = result.output.get("message")
            knowledge_source_text = self._knowledge_render_source_text(knowledge_output)
            display_source_text = knowledge_source_text or (
                raw_tool_message.strip() if isinstance(raw_tool_message, str) and raw_tool_message.strip() else ""
            )
            if display_source_text:
                if knowledge_source_text:
                    emitted_messages.append(
                        RenderedMessage(
                            text=self._knowledge_runtime_reply(
                                result_output=knowledge_output,
                                user_text=turn.text or "",
                                failure_fallback_text=action_step.response_policy.deterministic_fallback_text,
                            )
                        )
                    )
                else:
                    emitted_messages.append(RenderedMessage(text=display_source_text))
        elif result.status in ("error", "timeout", "blocked"):
            self._append_interaction_event(
                updated_semantic_events,
                name="activity_failed",
                payload={
                    "step_id": action_step.id,
                    "tool_ref": result.tool_ref,
                    "invocation_id": result.invocation_id,
                    "status": result.status,
                    "error": result.error,
                },
            )
        if result.status in ("error", "timeout", "blocked") and not emitted_messages:
            tool_label = (binding.ref if binding else result.tool_ref).replace(".", " ").replace("_", " ")
            emitted_messages.append(
                RenderedMessage(
                    text=f"I wasn't able to complete that action ({tool_label}). Let me know if you'd like to try again."
                )
            )

        updated_semantic_events.append(
            SemanticEventRecord(
                family="tool_outcome",
                name=f"{self._tool_event_name(binding, result.tool_ref)}_{self._tool_result_event_suffix(result.status)}",
                source="tool",
                confidence=1.0,
                payload={
                    "invocation_id": result.invocation_id,
                    "tool_ref": result.tool_ref,
                    "status": result.status,
                    "error": result.error,
                },
            )
        )

        step_after = action_step.id
        chosen_action = ActionRecord(
            type="run_tool",
            reason=f"tool_runtime:{result.status}",
            payload={"tool": result.tool_ref, "invocation_id": result.invocation_id},
        )
        scenario_routing_rule_id: str | None = None
        scenario_handoff_count = 0

        matched_transition = self._matched_step_transition(
            step=action_step,
            semantic_events=updated_semantic_events,
            working_facts=updated_working_facts,
            turn=turn,
        )
        if matched_transition is not None and matched_transition.to_step_id != action_step.id:
            step_after = matched_transition.to_step_id
            chosen_action = ActionRecord(
                type="transition",
                reason=f"step_transition:{matched_transition.id}",
                payload={
                    "to_step_id": step_after,
                    "scenario_id": agent_document.scenario_for_step_id(action_step.id).id,
                },
            )
        else:
            current_scenario_id = agent_document.scenario_for_step_id(action_step.id).id
            matched_scenario_route = self._matched_scenario_route(
                agent_document=agent_document,
                current_scenario_id=current_scenario_id,
                semantic_events=updated_semantic_events,
                working_facts=updated_working_facts,
                turn=turn,
            )
            if matched_scenario_route is not None:
                scenario_handoff_count = 1
                scenario_routing_rule_id = matched_scenario_route.id
                step_after = agent_document.scenario_by_id(matched_scenario_route.to_scenario_id).start_step_id
                chosen_action = ActionRecord(
                    type="transition",
                    reason=f"scenario_route:{matched_scenario_route.id}",
                    payload={
                        "to_scenario_id": matched_scenario_route.to_scenario_id,
                        "to_step_id": step_after,
                        "scenario_routing_rule_id": matched_scenario_route.id,
                    },
                )

        final_step = agent_document.step_by_id(step_after)
        if step_after != action_step.id:
            runtime_entry = build_step_runtime_entry(
                agent_document,
                current_step_id=step_after,
                facts=updated_working_facts,
            )
            if runtime_entry.scripted_say:
                emitted_messages.append(RenderedMessage(text=runtime_entry.scripted_say))
        if final_step.handoff is not None:
            chosen_action = ActionRecord(
                type="handoff",
                reason=final_step.handoff.target_type,
                payload=dict(chosen_action.payload),
            )
            if final_step.handoff.summary and not emitted_messages:
                emitted_messages.append(RenderedMessage(text=final_step.handoff.summary))
        elif final_step.completion is not None:
            chosen_action = ActionRecord(
                type="end",
                reason=final_step.completion.disposition,
                payload=dict(chosen_action.payload),
            )
            if final_step.completion.summary and not emitted_messages:
                emitted_messages.append(RenderedMessage(text=final_step.completion.summary))

        return self._process_completed_step_turn(
            conversation=conversation,
            agent_document=agent_document,
            turn=turn,
            turn_id=turn_id,
            dedupe_key=dedupe_key,
            step_before=step_before,
            step_after=step_after,
            semantic_events=updated_semantic_events,
            fact_updates=updated_fact_updates,
            chosen_action=chosen_action,
            emitted_messages=emitted_messages,
            tool_calls=tool_calls,
            working_facts=updated_working_facts,
            rules=accumulated_rules,
            trace_semantic_events=trace_semantic_events,
            scenario_routing_rule_id=scenario_routing_rule_id,
            scenario_handoff_count=scenario_handoff_count,
        )

    def _confirm_pending_rule_confirmation_for_step(
        self,
        *,
        conversation: ConversationState,
        agent_document: CompiledAgentDocument,
        pending_confirmation: PendingRuleConfirmation,
    ) -> RuntimeTurnResult:
        if self._tool_runtime is None:
            raise RuntimeError("tool runtime is not configured")
        if pending_confirmation.conversation_id != conversation.conversation_id:
            raise KeyError(pending_confirmation.confirmation_token)
        if conversation.step_id != pending_confirmation.step_id:
            raise ValueError("conversation no longer matches the pending rule confirmation step")
        action_step = agent_document.step_by_id(conversation.step_id)
        binding = self._select_tool_to_run(list(action_step.tool_policy or []))
        if binding is None or binding.ref != pending_confirmation.tool_ref:
            raise ValueError("step tool no longer matches pending rule confirmation")
        self._pop_pending_rule_confirmation(
            conversation=conversation,
            confirmation_token=pending_confirmation.confirmation_token,
        )
        self._clear_pending_permission(conversation)

        callback_turn = RuntimeTurn(
            turn_id=f"{pending_confirmation.confirmation_token}:confirm",
            dedupe_key=f"{pending_confirmation.confirmation_token}:confirm",
            channel=pending_confirmation.channel or conversation.channel or "web_chat",
            modality="event",
            event_type="tool_callback",
            metadata={
                "confirmation_token": pending_confirmation.confirmation_token,
                "confirmation_kind": "rule",
                "confirmed_rule_binding_ids": [pending_confirmation.binding_id],
                "original_event_type": pending_confirmation.event_type,
            },
            received_at=datetime.now(timezone.utc),
        )
        return self._invoke_step_tool(
            conversation=conversation,
            agent_document=agent_document,
            step_before=conversation.step_id,
            action_step=action_step,
            turn=callback_turn.model_copy(
                update={"event_type": pending_confirmation.event_type or "tool_callback"}
            ),
            semantic_events=[
                SemanticEventRecord(
                    family="interaction",
                    name="permission_resolved",
                    source="system",
                    confidence=1.0,
                    payload={
                        "request_id": pending_confirmation.confirmation_token,
                        "permission_kind": "rule_confirmation",
                        "resolution": "granted",
                        "publish_status_trail": False,
                    },
                )
            ],
            fact_updates=[],
            working_facts=dict(conversation.facts),
            rules=RuntimeRulesTrace(),
            resolved_args_override=pending_confirmation.resolved_args_json,
        )

    @staticmethod
    def _is_heading_like_knowledge_sentence(sentence: str) -> bool:
        text = _WHITESPACE_RE.sub(" ", str(sentence or "")).strip()
        if not text:
            return True
        lowered = text.lower()
        if text.endswith("?") and re.match(r"^(what|who|how|why|where|when|which)\b", lowered):
            return True
        if len(text.split()) <= 5 and not re.search(
            r"\b(is|are|can|will|helps|support|supports|provides|lets|allow|allows)\b",
            lowered,
        ):
            return True
        return False

    @staticmethod
    def _is_spoken_knowledge_candidate(sentence: str) -> bool:
        text = _WHITESPACE_RE.sub(" ", str(sentence or "")).strip()
        if len(text) < 28:
            return False
        if text.count(" ") < 4:
            return False
        if "#" in text or "```" in text or "|" in text:
            return False
        if text[0].islower():
            return False
        if text.endswith(('"', "'", ",", ";", ":")):
            return False
        return bool(re.search(r"[A-Za-z]", text))

    def _voice_safe_knowledge_fallback(
        self,
        *,
        raw_message: str,
        result_output: dict[str, object] | None,
        user_text: str = "",
    ) -> str:
        candidates: list[str] = []
        candidates.extend(self._extract_voice_safe_knowledge_candidates(raw_message))

        payload = result_output or {}
        hits = payload.get("hits")
        if isinstance(hits, list):
            for hit in hits:
                if not isinstance(hit, dict):
                    continue
                for key in ("summary", "snippet"):
                    candidates.extend(
                        self._extract_voice_safe_knowledge_candidates(
                            str(hit.get(key) or "")
                        )
                    )

        best = self._best_knowledge_fallback_candidate(
            user_text=user_text,
            candidates=candidates,
        )
        if best:
            best = re.sub(r"^\d+\.\s+[^:]{1,60}:\s+", "", best).strip()
            best = re.sub(r"^[A-Z][A-Za-z0-9\s&/-]{1,60}:\s+", "", best).strip()
            if len(best) > 240:
                return best[:237].rstrip() + "..."
            return best

        return "I found information about that, but I couldn't turn it into a clean spoken answer just now."

    @staticmethod
    def _knowledge_lookup_grade(result_output: dict[str, object] | None) -> str | None:
        payload = result_output or {}
        evaluation = payload.get("evaluation")
        if isinstance(evaluation, dict):
            grade = evaluation.get("grade")
            if isinstance(grade, str) and grade.strip():
                return grade.strip().lower()
        facts = payload.get("facts")
        if isinstance(facts, dict):
            grade = facts.get("knowledge_lookup_grade")
            if isinstance(grade, str) and grade.strip():
                return grade.strip().lower()
        return None

    @staticmethod
    def _normalize_knowledge_score(
        top_hit_score: float | None,
        grade: str | None,
    ) -> float:
        """Map Ruhu's absolute hybrid retrieval score (or coarse grade)
        to Google's 0–1 ``dynamic_retrieval_threshold`` scale.

        The absolute score from ``KnowledgeService._evaluate_lookup_hits``
        treats ``< 3.0`` as the "weak" floor and ``>= 3.0`` as broadly
        passable. This helper produces a 0–1 normalization calibrated on
        those breakpoints:

        - score == 0 → 0.0
        - score == 3.0 → 0.7 (matches Google's default threshold)
        - score >= 6.0 → 1.0 (saturation)
        - linear interpolation between

        When the absolute score is missing, falls back to the categorical
        grade: ``pass`` → 1.0, ``weak`` → 0.4, ``fail`` → 0.0. Returns
        the max of the score-derived and grade-derived value so neither
        signal is silently dropped.
        """
        score_norm = 0.0
        if isinstance(top_hit_score, (int, float)):
            value = float(top_hit_score)
            if value <= 0:
                score_norm = 0.0
            elif value <= 3.0:
                score_norm = (value / 3.0) * 0.7
            elif value >= 6.0:
                score_norm = 1.0
            else:
                score_norm = 0.7 + (value - 3.0) / 3.0 * 0.3

        grade_norm = 0.0
        if isinstance(grade, str):
            normalized = grade.strip().lower()
            if normalized == "pass":
                grade_norm = 1.0
            elif normalized == "weak":
                grade_norm = 0.4

        return max(score_norm, grade_norm)

    @staticmethod
    def _retrieval_evidence_from_result(
        result_output: dict[str, object] | None,
        grade: str | None,
    ) -> list[RetrievalChunk]:
        """Project ``knowledge.lookup`` result hits into
        ``RetrievalChunk`` records the renderer can consume + score
        post-hoc grounding against."""
        payload = result_output or {}
        hits = payload.get("hits")
        if not isinstance(hits, list):
            return []
        out: list[RetrievalChunk] = []
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            text_parts = [
                str(hit.get("snippet") or "").strip(),
                str(hit.get("summary") or "").strip(),
            ]
            chunk_text = " ".join(part for part in text_parts if part).strip()
            if not chunk_text:
                continue
            raw_score = hit.get("score")
            try:
                score = float(raw_score) if raw_score is not None else 0.0
            except (TypeError, ValueError):
                score = 0.0
            normalized = ConversationKernel._normalize_knowledge_score(score, grade)
            out.append(
                RetrievalChunk(
                    text=chunk_text,
                    document_id=str(hit.get("document_id") or ""),
                    chunk_id=str(hit.get("chunk_id") or ""),
                    title=(str(hit.get("title")) if hit.get("title") is not None else None),
                    score=score,
                    normalized_score=normalized,
                )
            )
        return out

    @staticmethod
    def _resolve_grounding_policy(
        step: Step,
        *,
        in_knowledge_path: bool = False,
    ) -> KnowledgeGroundingPolicy:
        """Read the step's effective ``KnowledgeGroundingPolicy``.

        When called from the knowledge-render path (i.e. immediately
        after a ``knowledge.lookup`` action ran), the runtime applies an
        auto-default: if the author hasn't explicitly opted in or out
        (``mode == "off"``), grounding is treated as ``required``. This
        mirrors Google Vertex AI's pattern where attaching a data store
        makes grounding implicit — authors don't have to remember to set
        a second flag in addition to wiring up the retrieval tool. The
        explicit author setting (``preferred`` / ``off``) always wins, so
        opting *out* on a specific step is still possible.

        Older serialised agent documents that predate the field
        round-trip cleanly through Pydantic's defaults; this helper
        centralises the access so call sites stay terse.
        """
        policy = getattr(step.response_policy, "knowledge_grounding", None)
        if not isinstance(policy, KnowledgeGroundingPolicy):
            policy = KnowledgeGroundingPolicy()
        if in_knowledge_path and policy.mode == "off":
            # Author hasn't expressed an opinion AND we're in the
            # knowledge-render path → auto-promote to required.
            policy = policy.model_copy(update={"mode": "required"})
        return policy

    @staticmethod
    def _knowledge_follow_up_queries(result_output: dict[str, object] | None) -> list[str]:
        payload = result_output or {}
        evaluation = payload.get("evaluation")
        if not isinstance(evaluation, dict):
            return []
        queries = evaluation.get("follow_up_queries")
        if not isinstance(queries, list):
            return []
        return [str(item).strip() for item in queries if str(item).strip()]

    def _render_knowledge_response_with_llm(
        self,
        *,
        conversation: ConversationState,
        step: Step,
        turn: RuntimeTurn,
        result_dict: dict[str, object],
        working_facts: dict[str, object],
        semantic_events: list[SemanticEventRecord],
    ) -> str | None:
        """Synthesize a knowledge.lookup answer through the LLM dialogue
        generator with Google's grounding-pattern gates applied.

        Two gates wrap the LLM call:

        1. **Pre-call gate** (this method): if
           ``response_policy.knowledge_grounding.mode != "off"`` and the
           retrieval evaluation is below threshold, refuse to call the
           LLM. Returns ``None`` (caller falls back to the deterministic
           ``_knowledge_runtime_reply``) for ``mode="preferred"``;
           returns the deterministic fallback string for
           ``mode="required"`` so the kernel never lets a free-text LLM
           answer through for grounding-required steps.
        2. **Post-call gate** (``response_generation.render_from_context``):
           if the rendered text doesn't ground sufficiently against the
           retrieved chunks, the renderer returns ``None`` and we fall
           through to the deterministic reply.

        Returns ``None`` when the generator is not configured, the step
        opted out (``render_with_llm=False``), the pre-call gate denies,
        or rendering produced no text. Returns the deterministic
        fallback string verbatim when ``mode="required"`` and the gate
        denies — the caller short-circuits without re-rendering.
        """
        logger.debug(
            "[knowledge-llm] entering: dialog_gen=%s render_with_llm=%s step=%s",
            self._dialogue_generator is not None,
            step.response_policy.render_with_llm,
            step.id,
        )
        if self._dialogue_generator is None:
            return None
        if not step.response_policy.render_with_llm:
            return None

        # ``in_knowledge_path=True`` flips the auto-default: when the
        # author hasn't explicitly set a mode and the kernel reached
        # this method, knowledge.lookup ran on this step → grounding is
        # implicit (required). Mirrors Vertex AI's data-store pattern.
        policy = self._resolve_grounding_policy(step, in_knowledge_path=True)
        grade = self._knowledge_lookup_grade(result_dict)
        retrieval_evidence = self._retrieval_evidence_from_result(result_dict, grade)
        top_normalized = (
            max((ev.normalized_score or 0.0) for ev in retrieval_evidence)
            if retrieval_evidence
            else 0.0
        )

        # Pre-call gate. ``mode="off"`` preserves today's behavior so
        # existing agents are unaffected.
        if policy.mode != "off":
            from .observability.metrics import (
                knowledge_grounding_gate_total,
                knowledge_grounding_score,
            )

            knowledge_grounding_score.labels(
                phase="pre_call", mode=policy.mode,
            ).observe(top_normalized)

            below_threshold = (
                grade == "fail"
                or grade is None
                or top_normalized < policy.min_relevance
            )
            if below_threshold:
                reason = (
                    "grade_fail" if grade == "fail"
                    else "empty_evidence" if grade is None
                    else "below_threshold"
                )
                knowledge_grounding_gate_total.labels(
                    phase="pre_call",
                    decision="blocked",
                    mode=policy.mode,
                    reason=reason,
                ).inc()
                self._append_narration_event(
                    semantic_events,
                    name="narration_fallback",
                    payload={
                        "response_mode": "answer_question",
                        "narrator_mode": "deterministic",
                        "fallback_used": True,
                        "fallback_reason": "grounding_pre_gate",
                        "grounding_mode": policy.mode,
                        "grade": grade or "absent",
                        "top_normalized_score": round(top_normalized, 4),
                        "min_relevance": policy.min_relevance,
                    },
                )
                if policy.mode == "required":
                    # Return the resolved deterministic fallback verbatim;
                    # the caller treats a non-None string as the final
                    # answer for this turn.
                    return (
                        policy.deterministic_fallback_text
                        or step.response_policy.deterministic_fallback_text
                        or self._knowledge_runtime_reply(
                            result_output=result_dict,
                            user_text=turn.text or "",
                        )
                    )
                # ``mode="preferred"`` — fall through to caller's
                # deterministic reply.
                return None

            knowledge_grounding_gate_total.labels(
                phase="pre_call",
                decision="allowed",
                mode=policy.mode,
                reason="passed",
            ).inc()

        outcome = ActionOutcomeSummary(
            status="success",
            action_type="knowledge_lookup",
            tool_ref="knowledge.lookup",
            user_visible_fields=self._knowledge_outcome_user_visible_fields(result_dict),
        )
        try:
            rendered_text, _ctx, used_render = self._render_text_from_context(
                conversation=conversation,
                state=step,
                turn=turn,
                response_mode="answer_question",
                response_directive=(
                    "Answer the user's question grounded in the knowledge_context "
                    "from the latest action result. Do not invent facts that aren't "
                    "in the context. If the context doesn't cover the question, say "
                    "so plainly and offer to help with what you can."
                ),
                working_facts=working_facts,
                latest_action_outcome=outcome,
                semantic_events=semantic_events,
                grounding_policy=policy,
                retrieval_evidence=retrieval_evidence,
                retrieval_grade=(grade or "absent"),  # type: ignore[arg-type]
            )
        except Exception as exc:
            logger.debug("knowledge LLM rendering failed: %s", exc, exc_info=True)
            return None
        if used_render and rendered_text.strip():
            return rendered_text.strip()
        # Post-call gate fired or render returned empty. For
        # ``mode="required"`` we must not let the caller's chatty
        # ``_knowledge_runtime_reply`` default leak through; emit the
        # configured deterministic fallback verbatim instead.
        if policy.mode == "required":
            return (
                policy.deterministic_fallback_text
                or step.response_policy.deterministic_fallback_text
                or self._knowledge_runtime_reply(
                    result_output=result_dict,
                    user_text=turn.text or "",
                )
            )
        return None

    def _knowledge_runtime_reply(
        self,
        *,
        result_output: dict[str, object] | None,
        user_text: str,
        failure_fallback_text: str | None = None,
    ) -> str:
        payload = result_output or {}
        grade = self._knowledge_lookup_grade(payload)
        if grade == "fail":
            authored_fallback = str(failure_fallback_text or "").strip()
            if authored_fallback:
                return authored_fallback
            follow_up_queries = self._knowledge_follow_up_queries(payload)
            if follow_up_queries:
                return (
                    "I couldn't find a grounded answer in the knowledge base yet. "
                    f"Could you narrow it to something like {follow_up_queries[0]}?"
                )
            return "I couldn't find a grounded answer in the knowledge base yet. Could you narrow the question a bit?"

        knowledge_source_text = self._knowledge_render_source_text(payload)
        if grade == "weak":
            cautious_answer = self._voice_safe_knowledge_fallback(
                raw_message=knowledge_source_text,
                result_output=payload,
                user_text=user_text,
            )
            follow_up_queries = self._knowledge_follow_up_queries(payload)
            if cautious_answer:
                softened = cautious_answer[0].lower() + cautious_answer[1:]
            else:
                softened = "I found only a partial grounded answer."
            if follow_up_queries:
                return f"From what I found, {softened} Could you narrow it to {follow_up_queries[0]}?"
            return f"From what I found, {softened}"

        return self._voice_safe_knowledge_fallback(
            raw_message=knowledge_source_text,
            result_output=payload,
            user_text=user_text,
        )

    @staticmethod
    def _knowledge_render_source_text(result_output: dict[str, object] | None) -> str:
        payload = result_output or {}
        # Prefer `message` (clean doc content) over `context_block` (formatted
        # for LLM consumption with a "Question: <user text>\n1. <doc>: ..."
        # preamble). The deterministic candidate extractor used to pick the
        # preamble and emit the user's question back as the answer.
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        context_block = payload.get("context_block")
        if isinstance(context_block, str) and context_block.strip():
            # Strip the "Question: ..." preamble line if present, so it can't
            # be picked up as a candidate sentence by the fallback extractor.
            cleaned = re.sub(
                r"^\s*Question:\s*[^\n]*\n+", "", context_block, count=1
            )
            return cleaned.strip()
        top_hit = payload.get("top_hit")
        if isinstance(top_hit, dict):
            for key in ("summary", "snippet"):
                value = top_hit.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    @staticmethod
    def _knowledge_outcome_user_visible_fields(result_output: dict[str, object] | None) -> dict[str, object]:
        """Build the grounding payload the dialogue generator sees.

        Only ``knowledge_context`` (the retrieved chunks) is user-facing.
        Diagnostic fields like ``retrieval_mode``, evaluation grade, and
        source title belong on the trace, not on ``user_visible_fields`` —
        when those keys flow into ``must_mention`` the LLM recites them
        verbatim ("based on our standard lookup which passed with a high
        grade", "based on our ruhu-sales-knowledge document").
        """
        payload = result_output or {}
        context_block = payload.get("context_block")
        if isinstance(context_block, str) and context_block.strip():
            return {"knowledge_context": context_block.strip()}
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return {"knowledge_context": message.strip()}
        return {}

    def _extract_voice_safe_knowledge_candidates(self, raw_text: str) -> list[str]:
        text = str(raw_text or "").strip()
        if not text:
            return []
        text = _MARKDOWN_CODE_FENCE_RE.sub(" ", text)
        text = _MARKDOWN_LINK_RE.sub(r"\1", text)
        text = _MARKDOWN_EMPHASIS_RE.sub("", text)
        text = re.sub(r"(?m)(^|\s)#{1,6}\s+", " ", text)
        text = _WHITESPACE_RE.sub(" ", text).strip()
        if not text or "#" in text:
            return []
        sentences = [
            part.strip()
            for part in re.split(r"(?<=[.!?])\s+", text)
            if part.strip()
        ]
        return [
            cleaned
            for sentence in sentences
            if not sentence.endswith("?")
            and not self._is_heading_like_knowledge_sentence(sentence)
            and not self._is_internal_knowledge_sentence(sentence)
            and self._is_spoken_knowledge_candidate(sentence)
            and (cleaned := re.sub(r"^\d+\.\s+[^:]{1,60}:\s+", "", sentence).strip())
        ]

    @staticmethod
    def _knowledge_query_keywords(user_text: str) -> set[str]:
        return ConversationKernel._topic_tokens(user_text)

    @staticmethod
    def _is_internal_knowledge_sentence(sentence: str) -> bool:
        lowered = _WHITESPACE_RE.sub(" ", str(sentence or "").lower()).strip()
        if not lowered:
            return True
        internal_markers = (
            "transitions connect steps",
            "tool outcomes",
            "system capabilities",
            "workflow questions",
            "connected providers",
            "oauth or api key",
            "knowledge.lookup",
            "knowledge base for sales agents",
            "sample knowledge document",
            "cover the common product and pricing questions",
            "edit freely",
            "placeholder",
            "before shipping",
            "this structure means",
            "branch intent",
            "step goal",
        )
        return any(marker in lowered for marker in internal_markers)

    def _best_knowledge_fallback_candidate(
        self,
        *,
        user_text: str,
        candidates: list[str],
    ) -> str:
        keywords = self._knowledge_query_keywords(user_text)
        lowered_user_text = _WHITESPACE_RE.sub(" ", str(user_text or "").lower()).strip()
        wants_procedural_or_detail_answer = bool(
            re.search(r"\b(how|detail|details|more|else|explain|build|setup|configure|create)\b", lowered_user_text)
        )
        procedural_pattern = re.compile(
            r"\b(build|create|configure|connect|trigger|condition|workflow|integration|step|steps|use|deploy|test)\b"
        )
        best_candidate = ""
        best_score = -10_000
        seen: set[str] = set()
        for candidate in candidates:
            normalized = _WHITESPACE_RE.sub(" ", str(candidate or "")).strip()
            if not normalized:
                continue
            dedupe_key = normalized.lower()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            lowered = normalized.lower()
            candidate_tokens = self._topic_tokens(normalized)
            overlap_tokens = keywords.intersection(candidate_tokens)
            procedural_matches = len(procedural_pattern.findall(lowered))
            score = 0
            if self._is_internal_knowledge_sentence(normalized):
                score -= 8
            if keywords:
                overlap = len(overlap_tokens)
                score += overlap * 4
                coverage = overlap / max(len(keywords), 1)
                score += int(round(coverage * 6))
                if overlap == 0:
                    score -= 5
                elif overlap == 1 and len(keywords) >= 3:
                    score -= 1
            if wants_procedural_or_detail_answer:
                if re.match(r"^[A-Z][A-Za-z0-9-]*(?:\s+[A-Z][A-Za-z0-9-]*)?\s+is\s+(?:a|an)\b", normalized):
                    score -= 7
                    if procedural_matches < 2:
                        score -= 3
                if procedural_matches:
                    score += min(procedural_matches, 3) * 3
                if re.match(r"^(?:You|Teams|Users)\s+can\b", normalized):
                    score += 3
            if len(normalized) > 220:
                score -= 1
            if score > best_score:
                best_score = score
                best_candidate = normalized
        return best_candidate

    def _process_completed_step_turn(
        self,
        *,
        conversation: ConversationState,
        agent_document: CompiledAgentDocument,
        turn: RuntimeTurn,
        turn_id: str,
        dedupe_key: str,
        step_before: str,
        step_after: str,
        semantic_events: list[SemanticEventRecord],
        fact_updates: list[FactUpdate],
        chosen_action: ActionRecord,
        emitted_messages: list[RenderedMessage],
        tool_calls: list[ToolCallRecord],
        working_facts: dict[str, object],
        rules: RuntimeRulesTrace | None = None,
        decision_observability: TurnDecisionObservability | None = None,
        model_outputs: list[ModelOutputRecord] | None = None,
        trace_semantic_events: list[SemanticEventRecord] | None = None,
        scenario_routing_rule_id: str | None = None,
        scenario_handoff_count: int = 0,
    ) -> RuntimeTurnResult:
        evaluated_rules = rules.model_copy(deep=True) if rules is not None else RuntimeRulesTrace()
        effective_action = chosen_action.model_copy(deep=True)
        effective_messages = [message.model_copy(deep=True) for message in emitted_messages]
        effective_semantic_events = [event.model_copy(deep=True) for event in semantic_events]
        if trace_semantic_events:
            effective_semantic_events.extend(
                event.model_copy(deep=True) for event in trace_semantic_events
            )
        effective_rules, effective_action, effective_messages = self._apply_before_response_rules(
            conversation=conversation,
            turn=turn,
            step_id=step_after,
            chosen_action=effective_action,
            emitted_messages=effective_messages,
            working_facts=working_facts,
            existing_rules=evaluated_rules,
        )
        effective_rules, effective_action, effective_messages = self._apply_before_emit_rules(
            conversation=conversation,
            turn=turn,
            step_id=step_after,
            chosen_action=effective_action,
            emitted_messages=effective_messages,
            working_facts=working_facts,
            existing_rules=effective_rules,
        )

        update_loop_counter_after_turn(
            conversation,
            step_before=step_before,
            step_after=step_after,
        )
        conversation.step_id = step_after
        conversation.facts = working_facts
        conversation.updated_at = datetime.now(timezone.utc)
        conversation.channel = turn.channel
        self._apply_step_conversation_lifecycle(
            conversation=conversation,
            agent_document=agent_document,
            step_after=step_after,
        )
        status_trail_items = self._projected_status_trail_items(conversation, effective_semantic_events)
        if status_trail_items:
            self._append_interaction_event(
                effective_semantic_events,
                name="status_trail_updated",
                payload={"items": status_trail_items},
            )
        runtime_entry = build_step_runtime_entry(
            agent_document,
            current_step_id=step_after,
            facts=working_facts,
            pending_action=conversation.control_state.pending_action is not None,
            pending_permission=conversation.control_state.pending_permission is not None,
            active_repair=conversation.control_state.active_repair is not None,
        )
        self._write_step_runtime_metadata(conversation.metadata, runtime_entry)
        cursor_revision_before = self._step_cursor_revision(conversation)
        conversation.metadata[_CURSOR_REVISION_METADATA_KEY] = (
            cursor_revision_before + scenario_handoff_count
        )
        if scenario_routing_rule_id is not None:
            conversation.metadata[_LAST_SCENARIO_ROUTE_METADATA_KEY] = scenario_routing_rule_id
        else:
            conversation.metadata.pop(_LAST_SCENARIO_ROUTE_METADATA_KEY, None)
        conversation.processed_dedupe_keys = [*conversation.processed_dedupe_keys, dedupe_key][-100:]

        trace_id = str(uuid4())
        result = RuntimeTurnResult(
            turn_id=turn_id,
            conversation_id=conversation.conversation_id,
            step_before=step_before,
            step_after=step_after,
            semantic_events=effective_semantic_events,
            fact_updates=fact_updates,
            chosen_action=effective_action,
            emitted_messages=effective_messages,
            tool_calls=tool_calls,
            rules=effective_rules,
            trace_id=trace_id,
            latency_breakdown_ms={"total": 0},
            interaction_debug_snapshot=self._interaction_debug_snapshot_for_step(
                conversation=conversation,
                step=agent_document.step_by_id(step_after),
                channel=turn.channel,
            ),
        )
        trace = TurnTrace(
            trace_id=trace_id,
            conversation_id=conversation.conversation_id,
            organization_id=conversation.organization_id,
            turn_id=turn_id,
            agent_id=conversation.agent_id,
            agent_version_id=conversation.agent_version_id,
            otel_trace_id=get_current_otel_trace_id(),
            channel=turn.channel or "",
            modality=turn.modality or "",
            event_type=turn.event_type or "",
            normalized_observation=self._normalize_turn_observation(turn),
            model_outputs=[
                *(model_outputs or []),
                *self._model_outputs_from_latency(result.latency_breakdown_ms),
            ],
            decision_observability=(
                decision_observability.model_copy(deep=True)
                if decision_observability is not None
                else TurnDecisionObservability()
            ),
            step_before=step_before,
            step_after=step_after,
            semantic_events=effective_semantic_events,
            fact_updates=fact_updates,
            chosen_action=effective_action,
            emitted_messages=effective_messages,
            tool_calls=tool_calls,
            rules=result.rules.model_copy(deep=True),
            latency_breakdown_ms={"total": 0},
            recorded_at=conversation.updated_at,
        )
        return self._commit_turn(
            conversation=conversation,
            turn=turn,
            result=result,
            trace=trace,
            dedupe_key=dedupe_key,
        )

    def _interaction_debug_snapshot_for_step(
        self,
        *,
        conversation: ConversationState,
        step: Step,
        channel: str,
    ) -> InteractionDebugSnapshot:
        pacing = pacing_policy_for_channel(channel)
        voice_policy = InteractionDebugVoicePolicy(
            step_id=step.id,
            endpointing_ms=pacing.endpointing_ms,
            soft_timeout_ms=pacing.soft_timeout_ms,
            turn_eagerness=pacing.turn_eagerness,
            interruptibility_policy=pacing.interruptibility_policy,
        )
        pending_action: InteractionDebugPendingAction | None = None
        pending_permission: InteractionDebugPendingPermission | None = None
        active_repair: InteractionDebugActiveRepair | None = None
        control = getattr(conversation, "control_state", None)
        if control is not None:
            if control.pending_action is not None:
                pending_action = InteractionDebugPendingAction(
                    action_id=control.pending_action.action_id,
                    action_type=control.pending_action.action_type,
                    status=control.pending_action.status,
                    action_label=control.pending_action.action_label,
                    target_ref=control.pending_action.target_ref,
                )
            if control.pending_permission is not None:
                pending_permission = InteractionDebugPendingPermission(
                    request_id=control.pending_permission.request_id,
                    permission_kind=control.pending_permission.permission_kind,
                    status=control.pending_permission.status,
                    target_ref=control.pending_permission.target_ref,
                )
            if control.active_repair is not None:
                active_repair = InteractionDebugActiveRepair(
                    repair_kind=control.active_repair.repair_kind,
                    target_ref=control.active_repair.target_ref,
                    summary=control.active_repair.summary,
                )
        return InteractionDebugSnapshot(
            step_id=step.id,
            channel=channel,
            voice_interaction_policy=voice_policy,
            pending_action=pending_action,
            pending_permission=pending_permission,
            active_repair=active_repair,
        )

    def _shared_store_transaction(self):
        conversation_session_factory = getattr(self._conversation_store, "_session_factory", None)
        trace_session_factory = getattr(self._trace_store, "_session_factory", None)
        if conversation_session_factory is None or trace_session_factory is None:
            return nullcontext(None)
        if conversation_session_factory is not trace_session_factory:
            return nullcontext(None)
        return conversation_session_factory.begin()

    def _normalize_turn_observation(self, turn: RuntimeTurn) -> NormalizedObservationRecord:
        text = (turn.text or "").strip()
        attachment_ids: list[str] = []
        for attachment in turn.attachments:
            attachment_id = getattr(attachment, "attachment_id", None)
            if isinstance(attachment_id, str) and attachment_id:
                attachment_ids.append(attachment_id)
        return NormalizedObservationRecord(
            channel=turn.channel or "",
            modality=turn.modality or "",
            event_type=turn.event_type or "",
            text_present=bool(text),
            redacted_text=text or None,
            attachment_ids=attachment_ids,
        )

    def _model_outputs_from_latency(self, latency_breakdown_ms: dict[str, int]) -> list[ModelOutputRecord]:
        total = latency_breakdown_ms.get("total")
        if total is None:
            return []
        return [ModelOutputRecord(stage="kernel_total", latency_ms=int(total))]

    def _append_trace(self, trace: TurnTrace, *, shared_session: object | None) -> None:
        if shared_session is None:
            self._trace_store.append(trace)
            self._observe_turn_decision(trace)
            return
        try:
            self._trace_store.append(trace, session=shared_session)
        except TypeError:
            self._trace_store.append(trace)
        self._observe_turn_decision(trace)

    def _append_turn_log(self, entry: TurnLogEntry, *, shared_session: object | None) -> None:
        if shared_session is None:
            self._turn_log_store.append(entry)
            return
        self._turn_log_store.append(entry, session=shared_session)

    @staticmethod
    def _observe_turn_decision(trace: TurnTrace) -> None:
        try:
            from .observability.metrics import (
                turn_classifier_fallback_total,
                turn_controller_of_record_total,
            )

            controller = trace.decision_observability.controller_of_record or "unknown"
            turn_controller_of_record_total.labels(controller=controller).inc()
            if trace.decision_observability.fallback_used:
                turn_classifier_fallback_total.labels(
                    controller=controller,
                    reason=trace.decision_observability.fallback_reason or "unknown",
                ).inc()
        except Exception:
            pass

    def _save_conversation(
        self,
        conversation: ConversationState,
        *,
        shared_session: object | None,
    ) -> None:
        if shared_session is None:
            self._conversation_store.save(conversation)
            return
        try:
            self._conversation_store.save(conversation, session=shared_session)
        except TypeError:
            self._conversation_store.save(conversation)

    def _evaluate_turn_ingress_rules(
        self,
        *,
        conversation: ConversationState,
        turn: RuntimeTurn,
        organization_id: str | None,
    ) -> RuntimeRulesTrace:
        return self._evaluate_rule_stage(
            conversation=conversation,
            turn=turn,
            stage="turn_ingress",
            facts=dict(conversation.facts),
            organization_id=organization_id,
        )

    def _evaluate_before_tool_rules(
        self,
        *,
        conversation: ConversationState,
        turn: RuntimeTurn,
        binding: ToolBinding,
        resolved_args: dict[str, object],
        existing_rules: RuntimeRulesTrace | None,
    ) -> RuntimeRulesTrace:
        return self._evaluate_rule_stage(
            conversation=conversation,
            turn=turn,
            stage="before_tool",
            facts=dict(conversation.facts),
            existing_rules=existing_rules,
            tool_ref=binding.ref,
            tool_args=resolved_args,
            metadata=self._build_before_tool_rule_metadata(conversation=conversation, turn=turn),
        )

    def _evaluate_after_tool_rules(
        self,
        *,
        conversation: ConversationState,
        turn: RuntimeTurn,
        step_id: str,
        result: ToolResult,
        resolved_args: dict[str, object] | None,
        existing_rules: RuntimeRulesTrace | None,
        working_facts: dict[str, object],
    ) -> RuntimeRulesTrace:
        return self._evaluate_rule_stage(
            conversation=conversation,
            turn=turn,
            stage="after_tool",
            step_id=step_id,
            facts=dict(working_facts),
            existing_rules=existing_rules,
            tool_ref=result.tool_ref,
            tool_args=resolved_args,
            tool_outcome=result.status,
            metadata=self._build_after_tool_rule_metadata(result=result),
        )

    def _evaluate_rule_stage(
        self,
        *,
        conversation: ConversationState,
        turn: RuntimeTurn,
        stage: str,
        facts: dict[str, object],
        existing_rules: RuntimeRulesTrace | None = None,
        organization_id: str | None = None,
        step_id: str | None = None,
        tool_ref: str | None = None,
        tool_args: dict[str, object] | None = None,
        tool_outcome: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> RuntimeRulesTrace:
        base_rules = existing_rules.model_copy(deep=True) if existing_rules is not None else RuntimeRulesTrace()
        if self._rule_program_resolver is None:
            return base_rules

        resolved_organization_id = conversation.organization_id or organization_id
        resolved_step_id = step_id or conversation.step_id
        try:
            program = self._rule_program_resolver.resolve(
                organization_id=resolved_organization_id,
                agent_id=conversation.agent_id,
                step_id=resolved_step_id,
                channel=turn.channel,
                event_type=turn.event_type,
                tool_ref=tool_ref,
            )
            decision = self._rule_engine.evaluate(
                program,
                RuleEvaluationContext(
                    stage=stage,  # type: ignore[arg-type]
                    conversation={
                        "organization_id": resolved_organization_id,
                        "conversation_id": conversation.conversation_id,
                        "agent_id": conversation.agent_id,
                        "step_id": resolved_step_id,
                        "channel": turn.channel,
                        "turn_count": len(conversation.processed_dedupe_keys),
                    },
                    turn={
                        "event_type": turn.event_type,
                        "text": turn.text,
                        "metadata": dict(turn.metadata),
                    },
                    tool={
                        "ref": tool_ref,
                        "args": dict(tool_args or {}),
                        "outcome": tool_outcome,
                    },
                    facts=facts,
                    metadata=self._build_rule_metadata(
                        conversation=conversation,
                        turn=turn,
                        extra=metadata,
                    ),
                ),
            )
            base_rules.append_decision(stage=stage, decision=decision)  # type: ignore[arg-type]
            return base_rules
        except Exception as exc:  # pragma: no cover - defensive fail-open path
            logger.warning(
                "kernel: rule evaluation failed (stage=%s, conv=%s, agent=%s, state=%s): %s",
                stage,
                conversation.conversation_id,
                conversation.agent_id,
                resolved_step_id,
                exc,
                exc_info=True,
            )
            base_rules.evaluations.append(
                RuleStageDecision(
                    stage=stage,  # type: ignore[arg-type]
                    traces=[
                        RuleTrace(
                            binding_id=f"runtime.{stage}",
                            rule_id=f"runtime.{stage}",
                            revision=0,
                            outcome="error",
                            mode="enforce",
                            detail=str(exc),
                        )
                    ],
                )
            )
            return base_rules

    def _build_rule_metadata(
        self,
        *,
        conversation: ConversationState,
        turn: RuntimeTurn,
        extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        metadata = dict(conversation.metadata)
        metadata.update(turn.metadata)
        if extra:
            metadata.update(extra)
        return metadata

    def _build_before_tool_rule_metadata(
        self,
        *,
        conversation: ConversationState,
        turn: RuntimeTurn,
    ) -> dict[str, object]:
        metadata: dict[str, object] = {}
        metadata["tool_execution_count"] = self._tool_execution_count(conversation)
        metadata["confirmed_rule_binding_ids"] = self._confirmed_rule_binding_ids(turn)
        return metadata

    def _build_after_tool_rule_metadata(self, *, result: ToolResult) -> dict[str, object]:
        metadata: dict[str, object] = {
            "tool_error": result.error,
            "tool_status": result.status,
        }
        output_message = result.output.get("message")
        if isinstance(output_message, str) and output_message.strip():
            metadata["tool_output_message"] = output_message.strip()
        return metadata

    def _apply_before_response_rules(
        self,
        *,
        conversation: ConversationState,
        turn: RuntimeTurn,
        step_id: str,
        chosen_action: ActionRecord,
        emitted_messages: list[RenderedMessage],
        working_facts: dict[str, object],
        existing_rules: RuntimeRulesTrace,
    ) -> tuple[RuntimeRulesTrace, ActionRecord, list[RenderedMessage]]:
        if not emitted_messages:
            return existing_rules, chosen_action, emitted_messages

        evaluated_rules = self._evaluate_rule_stage(
            conversation=conversation,
            turn=turn,
            stage="before_response",
            step_id=step_id,
            facts=dict(working_facts),
            existing_rules=existing_rules,
            metadata={
                "candidate_response_text": self._joined_message_text(emitted_messages),
                "candidate_response_reason": chosen_action.reason,
            },
        )
        stage_decision = evaluated_rules.evaluations[-1] if evaluated_rules.evaluations else None
        if stage_decision is None or stage_decision.stage != "before_response" or stage_decision.terminal_effect is None:
            return evaluated_rules, chosen_action, emitted_messages

        blocked_action = self._rule_blocked_action(chosen_action=chosen_action, code=stage_decision.terminal_effect.code)
        replacement_messages = self._rule_blocked_messages(stage_decision.terminal_effect.message)
        return evaluated_rules, blocked_action, replacement_messages

    def _apply_before_emit_rules(
        self,
        *,
        conversation: ConversationState,
        turn: RuntimeTurn,
        step_id: str,
        chosen_action: ActionRecord,
        emitted_messages: list[RenderedMessage],
        working_facts: dict[str, object],
        existing_rules: RuntimeRulesTrace,
    ) -> tuple[RuntimeRulesTrace, ActionRecord, list[RenderedMessage]]:
        if not emitted_messages:
            return existing_rules, chosen_action, emitted_messages

        evaluated_rules = self._evaluate_rule_stage(
            conversation=conversation,
            turn=turn,
            stage="before_emit",
            step_id=step_id,
            facts=dict(working_facts),
            existing_rules=existing_rules,
            metadata={
                "emitted_message_text": self._joined_message_text(emitted_messages),
                "emitted_message_count": len(emitted_messages),
            },
        )
        stage_decision = evaluated_rules.evaluations[-1] if evaluated_rules.evaluations else None
        if stage_decision is None or stage_decision.stage != "before_emit" or stage_decision.terminal_effect is None:
            return evaluated_rules, chosen_action, emitted_messages

        blocked_action = self._rule_blocked_action(chosen_action=chosen_action, code=stage_decision.terminal_effect.code)
        replacement_messages = self._rule_blocked_messages(stage_decision.terminal_effect.message)
        return evaluated_rules, blocked_action, replacement_messages

    def _tool_execution_count(self, conversation: ConversationState) -> int:
        if self._tool_runtime is None:
            return 0
        return len(
            self._tool_runtime.store.by_conversation(
                conversation.conversation_id,
                organization_id=conversation.organization_id,
            )
        )

    @staticmethod
    def _confirmed_rule_binding_ids(turn: RuntimeTurn) -> list[str]:
        return [
            str(item)
            for item in list(turn.metadata.get("confirmed_rule_binding_ids") or [])
            if str(item).strip()
        ]

    @staticmethod
    def _joined_message_text(messages: list[RenderedMessage]) -> str:
        return "\n\n".join(message.text.strip() for message in messages if message.text.strip())

    @staticmethod
    def _rule_blocked_messages(message: str | None) -> list[RenderedMessage]:
        if not message:
            return []
        return [RenderedMessage(text=message)]

    @staticmethod
    def _rule_blocked_action(*, chosen_action: ActionRecord, code: str) -> ActionRecord:
        payload = dict(chosen_action.payload)
        payload.setdefault("original_reason", chosen_action.reason)
        return chosen_action.model_copy(
            update={
                "reason": f"rule_blocked:{code}",
                "payload": payload,
            }
        )

    @staticmethod
    def _terminal_rule_match(stage_decision: RuleStageDecision) -> RuleMatch | None:
        if not stage_decision.matched_rules:
            return None
        return stage_decision.matched_rules[-1]

    @staticmethod
    def _rule_tool_call_payload(*, terminal_match: RuleMatch | None, tool_ref: str) -> dict[str, object]:
        payload: dict[str, object] = {"tool_ref": tool_ref}
        if terminal_match is not None:
            payload.update(
                {
                    "binding_id": terminal_match.binding_id,
                    "rule_id": terminal_match.rule_id,
                    "rule_revision": terminal_match.revision,
                }
            )
        return payload

    @staticmethod
    def _build_rule_tool_semantic_event(
        *,
        name: str,
        tool_ref: str,
        code: str,
        terminal_match: RuleMatch | None,
    ) -> SemanticEventRecord:
        payload = {"code": code, "tool_ref": tool_ref}
        if terminal_match is not None:
            payload.update(
                {
                    "binding_id": terminal_match.binding_id,
                    "rule_id": terminal_match.rule_id,
                    "rule_revision": terminal_match.revision,
                }
            )
        return SemanticEventRecord(
            family="rule",
            name=name,
            source="system",
            confidence=1.0,
            payload=payload,
        )

    def _create_pending_rule_confirmation(
        self,
        *,
        conversation: ConversationState,
        turn: RuntimeTurn,
        step_id: str,
        binding: ToolBinding,
        resolved_args: dict[str, object],
        terminal_match: RuleMatch | None,
    ) -> PendingRuleConfirmation:
        if terminal_match is None:
            raise ValueError("rule confirmation requires a matched binding")
        confirmation = PendingRuleConfirmation(
            confirmation_token=str(uuid4()),
            conversation_id=conversation.conversation_id,
            step_id=step_id,
            tool_ref=binding.ref,
            resolved_args_json=dict(resolved_args),
            binding_id=terminal_match.binding_id,
            rule_id=terminal_match.rule_id,
            rule_revision=terminal_match.revision,
            channel=turn.channel,
            event_type=turn.event_type,
        )
        pending = {
            str(token): dict(item)
            for token, item in dict(conversation.metadata.get(_PENDING_RULE_CONFIRMATIONS_METADATA_KEY) or {}).items()
        }
        pending[confirmation.confirmation_token] = confirmation.model_dump(mode="json")
        conversation.metadata[_PENDING_RULE_CONFIRMATIONS_METADATA_KEY] = pending
        return confirmation

    def _get_pending_rule_confirmation(
        self,
        *,
        conversation: ConversationState,
        confirmation_token: str,
    ) -> PendingRuleConfirmation | None:
        pending = {
            str(token): dict(item)
            for token, item in dict(conversation.metadata.get(_PENDING_RULE_CONFIRMATIONS_METADATA_KEY) or {}).items()
        }
        payload = pending.get(confirmation_token)
        if payload is None:
            return None
        return PendingRuleConfirmation.model_validate(payload)

    def _pop_pending_rule_confirmation(
        self,
        *,
        conversation: ConversationState,
        confirmation_token: str,
    ) -> PendingRuleConfirmation | None:
        pending = {
            str(token): dict(item)
            for token, item in dict(conversation.metadata.get(_PENDING_RULE_CONFIRMATIONS_METADATA_KEY) or {}).items()
        }
        payload = pending.pop(confirmation_token, None)
        if not pending:
            conversation.metadata.pop(_PENDING_RULE_CONFIRMATIONS_METADATA_KEY, None)
        else:
            conversation.metadata[_PENDING_RULE_CONFIRMATIONS_METADATA_KEY] = pending
        if payload is None:
            return None
        return PendingRuleConfirmation.model_validate(payload)

    # ── Pending action lifecycle (Phase 6) ──────────────────────────────

    def _create_pending_action(
        self,
        *,
        conversation: ConversationState,
        action_type: str,
        tool_ref: str | None = None,
        action_label: str | None = None,
        activity_label: str | None = None,
        activity_guidance: str | None = None,
        invocation_id: str | None = None,
        metadata: dict[str, object] | None = None,
        initial_status: str = "confirmation_required",
        commitment_status: str = "pending_external",
        commitment_summary: str | None = None,
    ) -> PendingActionState:
        """Create a pending action and store it in conversation control_state."""
        action_id = invocation_id or str(uuid4())
        resolved_label = activity_label or action_label or tool_ref or action_type
        resolved_metadata = dict(metadata or {})
        pending = PendingActionState(
            action_id=action_id,
            action_type=action_type,
            status=initial_status,
            tool_ref=tool_ref,
            action_label=resolved_label,
            target_ref=tool_ref,
            activity={
                "activity_type": action_type,
                "label": resolved_label,
                "guidance": activity_guidance,
            },
            commitment={
                "status": commitment_status,
                "summary": commitment_summary or f"Awaiting confirmation for {resolved_label}",
            },
            metadata=resolved_metadata,
        )
        self._clear_active_repair(conversation)
        conversation.control_state.pending_action = pending
        if initial_status == "confirmation_required":
            self._set_pending_permission(
                conversation=conversation,
                request_id=action_id,
                permission_kind="tool_confirmation",
                target_ref=tool_ref,
                user_visible_context={
                    "action_label": resolved_label,
                    "publish_status_trail": bool(resolved_metadata.get("publish_status_trail")),
                },
            )
        return pending

    def _get_pending_action(
        self, conversation: ConversationState
    ) -> PendingActionState | None:
        """Get the current pending action, if any."""
        return conversation.control_state.pending_action

    def _clear_pending_action(self, conversation: ConversationState) -> None:
        """Clear the pending action from conversation control_state."""
        conversation.control_state.pending_action = None

    def _set_pending_permission(
        self,
        *,
        conversation: ConversationState,
        request_id: str,
        permission_kind: str,
        target_ref: str | None = None,
        user_visible_context: dict[str, object] | None = None,
    ) -> None:
        conversation.control_state.pending_permission = PendingPermissionState(
            request_id=request_id,
            permission_kind=permission_kind,
            target_ref=target_ref,
            status="waiting",
            user_visible_context=dict(user_visible_context or {}),
        )

    def _clear_pending_permission(self, conversation: ConversationState) -> None:
        conversation.control_state.pending_permission = None

    def _set_active_repair(
        self,
        *,
        conversation: ConversationState,
        repair_kind: str,
        target_ref: str | None = None,
        summary: str | None = None,
    ) -> None:
        conversation.control_state.active_repair = RepairContext(
            repair_kind=repair_kind,
            target_ref=target_ref,
            summary=summary,
        )

    def _clear_active_repair(self, conversation: ConversationState) -> None:
        conversation.control_state.active_repair = None

    @staticmethod
    def _classify_confirmation_intent(text: str) -> str:
        """Classify user text as confirm, cancel, or unclear.

        Returns one of: "confirm", "cancel", "unclear".

        This is a fast deterministic classifier — no LLM needed for the
        common case.  The patterns are intentionally broad to handle
        voice transcription noise (e.g., "yeah sure" → confirm).
        """
        normalized = text.strip().lower()
        if not normalized:
            return "unclear"

        question_like_prefixes = (
            "can i ",
            "if i ",
            "will that",
            "is that",
            "should i ",
            "am i ",
            "do you need",
        )

        confirm_patterns = {
            "yes", "yeah", "yep", "yup", "sure", "ok", "okay",
            "confirm", "go ahead", "do it", "please", "proceed",
            "absolutely", "correct", "that's right", "right",
            "create", "submit", "book", "send",
        }
        cancel_patterns = {
            "no", "nah", "nope", "cancel", "stop", "don't",
            "never mind", "nevermind", "forget it", "not now",
            "actually no",
        }

        # Check exact match first
        if normalized in confirm_patterns:
            return "confirm"
        if normalized in cancel_patterns:
            return "cancel"

        if "?" in normalized or normalized.startswith(question_like_prefixes):
            return "unclear"

        # Check phrase match with word boundaries — confirm has lower priority
        # to avoid false positives on sentences like "no, I don't want that"
        # and to avoid matching short tokens inside unrelated words
        # (for example, "no" inside "now").
        for pattern in cancel_patterns:
            if re.search(rf"\b{re.escape(pattern)}\b", normalized):
                return "cancel"
        for pattern in confirm_patterns:
            if re.search(rf"\b{re.escape(pattern)}\b", normalized):
                return "confirm"

        return "unclear"

    @staticmethod
    def _is_pending_action_status_query(text: str) -> bool:
        normalized = " ".join(text.strip().lower().split())
        if not normalized:
            return False
        return any(re.search(pattern, normalized) for pattern in _PENDING_ACTION_STATUS_PATTERNS)

    @staticmethod
    def _pending_action_status_fallback_text(pending: PendingActionState) -> str:
        label = pending.action_label or pending.tool_ref or pending.action_type or "that request"
        if pending.status == "confirmation_required":
            return f"I'm waiting for your confirmation before I continue with {label}."
        if pending.status == "starting":
            return f"I'm starting {label} now."
        if pending.status == "queued":
            return f"{label} is queued, and I'm waiting for it to start."
        if pending.status == "waiting_poll":
            return f"{label} is running, and I'm checking back for progress."
        if pending.status == "waiting_webhook":
            return f"{label} is running, and I'm waiting for the provider to call back."
        if pending.status == "retry_scheduled":
            return f"{label} is delayed, and I'll retry it shortly."
        if pending.status == "slow":
            return f"I'm still working on {label}. It's taking a little longer than expected."
        if pending.status == "cancelling":
            return f"I'm trying to stop {label} now."
        if pending.status == "completion_uncertain":
            return "I asked to stop that, and I'm still confirming whether it completed."
        return f"I'm still working on {label} now."

    @staticmethod
    def _explicit_artifact_id_from_turn(turn: RuntimeTurn) -> str | None:
        metadata = turn.metadata if isinstance(turn.metadata, dict) else {}
        direct = metadata.get("artifact_id")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        nested = metadata.get("artifact_ref")
        if isinstance(nested, dict):
            value = nested.get("artifact_id")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _artifact_followup_resolution_message(resolution: dict[str, object]) -> str:
        status = str(resolution.get("status") or "")
        artifact_type = str(resolution.get("artifact_type") or "item").replace("_", " ")
        if status == "ambiguous":
            return f"I found more than one matching {artifact_type}. Which one do you mean?"
        if status == "explicit_id_missing":
            return f"I couldn't find that {artifact_type}."
        if status == "missing_fact_requirements":
            missing = [str(item).replace("_", " ") for item in list(resolution.get("missing_facts") or []) if str(item)]
            if not missing:
                return f"I need a bit more information before I can update that {artifact_type}."
            if len(missing) == 1:
                return f"I need your {missing[0]} before I can update that {artifact_type}."
            return f"I need {', '.join(missing[:-1])}, and {missing[-1]} before I can update that {artifact_type}."
        return ""

    @staticmethod
    def _artifact_followup_resolution_structured_message(
        resolution: dict[str, object],
    ) -> tuple[str | None, dict[str, object]]:
        if str(resolution.get("status") or "") != "ambiguous":
            return None, {}
        candidates_raw = resolution.get("candidates")
        if not isinstance(candidates_raw, list) or not candidates_raw:
            return None, {}
        candidates: list[dict[str, object]] = []
        for candidate in candidates_raw:
            if not isinstance(candidate, dict):
                continue
            artifact_id = candidate.get("artifact_id")
            title = candidate.get("title")
            if not isinstance(artifact_id, str) or not artifact_id.strip():
                continue
            candidates.append(
                {
                    "artifact_id": artifact_id.strip(),
                    "artifact_type": str(candidate.get("artifact_type") or resolution.get("artifact_type") or "item"),
                    "title": str(title or "Untitled item"),
                    "status": str(candidate.get("status") or "active"),
                    "external_id": str(candidate.get("external_id")) if candidate.get("external_id") else None,
                    "reply_text": f"{resolution.get('followup_intent') or 'select'} {title or 'that one'}",
                }
            )
        if not candidates:
            return None, {}
        return (
            "artifact_disambiguation",
            {
                "artifact_type": str(resolution.get("artifact_type") or "item"),
                "followup_intent": str(resolution.get("followup_intent") or ""),
                "candidates": candidates,
            },
        )

    @staticmethod
    def _artifact_event_payload(
        artifact: ConversationArtifact,
        *,
        source: str,
        extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "artifact_id": artifact.artifact_id,
            "artifact_type": artifact.artifact_type,
            "status": artifact.status,
            "external_id": artifact.external_id,
            "source": source,
        }
        if extra:
            payload.update(extra)
        return payload

    @staticmethod
    def _append_artifact_event(
        events: list[SemanticEventRecord],
        *,
        name: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        events.append(
            SemanticEventRecord(
                family="artifact",
                name=name,
                source="system",
                confidence=1.0,
                payload=dict(payload or {}),
            )
        )

    def _set_current_focus(
        self,
        *,
        conversation: ConversationState,
        artifact: ConversationArtifact,
        semantic_events: list[SemanticEventRecord],
        source: str,
        force: bool = False,
    ) -> None:
        # Doc 22 simplification: most-recent-created artifact is always the focus.
        # No focusable/focus_priority gating; force flag preserved for explicit overrides.
        current = conversation.control_state.current_focus
        if current is not None and current.artifact_id == artifact.artifact_id:
            return
        conversation.control_state.current_focus = ConversationFocus(
            artifact_id=artifact.artifact_id,
            artifact_type=artifact.artifact_type,
            set_at=datetime.now(timezone.utc),
        )
        self._append_artifact_event(
            semantic_events,
            name="focused",
            payload=self._artifact_event_payload(artifact, source=source),
        )

    @staticmethod
    def _focused_artifact(conversation: ConversationState) -> ConversationArtifact | None:
        current = conversation.control_state.current_focus
        if current is None or not current.artifact_id:
            return None
        for artifact in conversation.control_state.active_artifacts:
            if artifact.artifact_id == current.artifact_id:
                return artifact
        return None

    # ── Capture runtime (Phase 7) ──────────────────────────────────────

    def _get_capture_runtime(
        self, conversation: ConversationState, step_id: str
    ) -> CaptureRuntimeState:
        """Get or create CaptureRuntimeState for a detail-collection step."""
        runtime = conversation.control_state.capture_runtime.get(step_id)
        if runtime is None:
            runtime = CaptureRuntimeState()
            conversation.control_state.capture_runtime[step_id] = runtime
        return runtime

    @staticmethod
    def _step_fact_requirements(step: Step) -> list[FactRequirement]:
        return normalized_fact_requirements(step)

    @classmethod
    def _step_fact_requirement_names(cls, step: Step) -> list[str]:
        return fact_requirement_names(step)

    @classmethod
    def _missing_fact_requirements(
        cls,
        step: Step,
        accepted_facts: dict[str, object],
    ) -> list[str]:
        accepted = set(accepted_facts)
        return [
            fact_name
            for fact_name in cls._step_fact_requirement_names(step)
            if fact_name not in accepted
        ]

    def _is_bridge_appropriate_capture_turn(
        *,
        text: str,
        missing_facts: list[str],
    ) -> bool:
        normalized = " ".join(text.strip().lower().split())
        if not normalized or not missing_facts:
            return False
        return any(re.search(pattern, normalized) for pattern in _CAPTURE_BRIDGE_PATTERNS)

    def _safe_fallback_text_for_response_mode(
        self,
        *,
        state: Step,
        response_mode: str,
        conversation: ConversationState,
    ) -> str:
        deterministic = (state.response_policy.deterministic_fallback_text or "").strip()
        if deterministic:
            return deterministic
        if response_mode == "clarify":
            return "Could you say a bit more about what you need?"
        if response_mode == "transition_bridge":
            return "Let me help with that."
        if response_mode == "status_explanation":
            return self._fallback_text_for_action_answer(state, conversation)
        if response_mode == "answer_question":
            if state.action_config is not None:
                return self._fallback_text_for_action_answer(state, conversation)
            return "Let me help with that."
        if response_mode == "acknowledge":
            return "Okay."
        return "Let me help with that."

    def _resolve_tool_args(
        self,
        binding: ToolBinding,
        *,
        turn: RuntimeTurn,
        working_facts: dict[str, object],
        step_id: str,
        conversation: ConversationState,
    ) -> dict[str, object]:
        return {
            key: self._resolve_tool_arg_value(
                value,
                turn=turn,
                working_facts=working_facts,
                step_id=step_id,
                conversation=conversation,
            )
            for key, value in binding.args.items()
        }

    def _resolve_tool_arg_value(
        self,
        value: object,
        *,
        turn: RuntimeTurn,
        working_facts: dict[str, object],
        step_id: str,
        conversation: ConversationState,
    ) -> object:
        if isinstance(value, str) and value.startswith("$fact."):
            return working_facts.get(value.split(".", 1)[1])
        if value == "$turn.text":
            return turn.text
        if value == "$turn.channel":
            return turn.channel
        if value == "$turn.modality":
            return turn.modality
        if value == "$state.id":
            return step_id
        if value == "$conversation.id":
            return conversation.conversation_id
        if value == "$organization.id":
            return conversation.organization_id
        if isinstance(value, list):
            return [
                self._resolve_tool_arg_value(
                    item,
                    turn=turn,
                    working_facts=working_facts,
                    step_id=step_id,
                    conversation=conversation,
                )
                for item in value
            ]
        if isinstance(value, dict):
            return {
                key: self._resolve_tool_arg_value(
                    item,
                    turn=turn,
                    working_facts=working_facts,
                    step_id=step_id,
                    conversation=conversation,
                )
                for key, item in value.items()
            }
        return value

    @staticmethod
    def _tool_result_record_status(result: ToolResult) -> str:
        if ConversationKernel._is_deferred_tool_result(result):
            return "requested"
        mapping = {
            "success": "success",
            "confirmation_required": "confirmation_required",
            "blocked": "blocked",
            "timeout": "timeout",
            "error": "error",
            "cancelled": "cancelled",
        }
        return mapping[result.status]

    @staticmethod
    def _tool_result_event_suffix(status: str) -> str:
        mapping = {
            "success": "success",
            "confirmation_required": "confirmation_required",
            "blocked": "blocked",
            "timeout": "timeout",
            "error": "error",
            "cancelled": "cancelled",
        }
        return mapping[status]

    @staticmethod
    def _is_deferred_tool_result(result: ToolResult) -> bool:
        if result.status != "success":
            return False
        return bool(result.metadata.get("deferred")) and str(result.metadata.get("integration_status") or "") in {
            "queued",
            "running",
            "waiting_poll",
            "waiting_webhook",
            "retry_scheduled",
        }

    @staticmethod
    def _tool_event_name(binding: ToolBinding | None, tool_ref: str) -> str:
        if binding is not None and binding.event_name:
            return binding.event_name
        return tool_ref.replace(".", "_")

    @staticmethod
    def _format_capability_list(capabilities: list[str]) -> str:
        cleaned = [str(item).strip() for item in capabilities if str(item).strip()]
        if not cleaned:
            return ""
        if len(cleaned) == 1:
            return cleaned[0]
        if len(cleaned) == 2:
            return f"{cleaned[0]} and {cleaned[1]}"
        return f"{', '.join(cleaned[:-1])}, and {cleaned[-1]}"

    @staticmethod
    def _capability_phrase(capability: str) -> str:
        text = str(capability or "").strip()
        if not text:
            return ""
        lowered = text.lower()
        replacements = (
            ("answer ", ""),
            ("help with ", ""),
            ("help book ", "booking "),
            ("book ", "booking "),
        )
        for prefix, replacement in replacements:
            if lowered.startswith(prefix):
                remainder = text[len(prefix):].strip()
                if not remainder:
                    return ""
                return f"{replacement}{remainder}".strip()
        return text

    def _spoken_capability_list(self, capabilities: list[str]) -> str:
        spoken = [
            phrase
            for phrase in (self._capability_phrase(item) for item in capabilities)
            if phrase
        ]
        return self._format_capability_list(spoken)

    @staticmethod
    def _step_capabilities(step: Step) -> dict[str, bool]:
        return step_capability_flags(step)

    def _supports_open_intent_classification(self, step: Step) -> bool:
        return not any(self._step_capabilities(step).values())

    @staticmethod
    def _step_summary(step: Step) -> str:
        if step.description and step.description.strip():
            return step.description.strip()
        if step.say and step.say.strip():
            return step.say.strip()
        return step.name

    def _interpreter_semantic_events_for_step(
        self,
        *,
        conversation: ConversationState,
        agent_document: AgentDocument,
        step: Step,
        fact_updates: list[FactUpdate],
        turn: RuntimeTurn,
    ) -> list[SemanticEventRecord]:
        try:
            return self._interpreter.interpret(
                agent_document=agent_document,
                step=step,
                agent_id=conversation.agent_id,
                agent_name=conversation.agent_id,
                conversation_facts={**conversation.facts, **{u.name: u.value for u in fact_updates}},
                turn=turn,
            )
        except Exception:
            return []

    @staticmethod
    def _classifier_semantic_events_from_turn(turn: RuntimeTurn) -> list[SemanticEventRecord]:
        metadata = turn.metadata if isinstance(turn.metadata, dict) else {}
        raw_events = metadata.get(_CLASSIFIER_SEMANTIC_EVENTS_METADATA_KEY)
        if not isinstance(raw_events, list):
            return []
        events: list[SemanticEventRecord] = []
        for item in raw_events:
            if not isinstance(item, dict):
                continue
            try:
                events.append(SemanticEventRecord.model_validate(item))
            except Exception:
                continue
        return events

    @staticmethod
    def _classifier_metadata_from_turn(turn: RuntimeTurn) -> dict[str, object]:
        metadata = turn.metadata if isinstance(turn.metadata, dict) else {}
        raw = metadata.get(_CLASSIFIER_METADATA_METADATA_KEY)
        return dict(raw) if isinstance(raw, dict) else {}

    @staticmethod
    def _normalize_turn_fallback_reason(raw_reason: object, *, default: str) -> str:
        if isinstance(raw_reason, dict):
            category = raw_reason.get("category")
            if isinstance(category, str) and category.strip():
                return category.strip()
        if isinstance(raw_reason, str) and raw_reason.strip():
            return raw_reason.strip()
        return default

    @staticmethod
    def _extract_required_id_facts(text: str, fact_names: list[str]) -> dict[str, str]:
        if not text:
            return {}

        id_fact_names = [name for name in fact_names if name.endswith("_id")]
        if not id_fact_names:
            return {}

        hinted = ACCOUNT_ID_HINT_RE.search(text)
        if hinted:
            value = hinted.group(1).strip()
            if "account_id" in id_fact_names:
                return {"account_id": value}
            return {id_fact_names[0]: value}

        candidate = text.strip()
        if "@" in candidate:
            return {}
        if ID_VALUE_RE.fullmatch(candidate):
            if len(id_fact_names) == 1:
                return {id_fact_names[0]: candidate}
            if "account_id" in id_fact_names:
                return {"account_id": candidate}

        return {}

    def _evaluate_guards(
        self,
        guards: list[GuardDef],
        turn: RuntimeTurn,
        working_facts: dict[str, object],
    ) -> list[SemanticEventRecord]:
        results: list[SemanticEventRecord] = []
        for guard in guards:
            if guard.kind == "channel_allowed":
                allowed_channels = {item.strip() for item in guard.value.split(",") if item.strip()}
                if turn.channel not in allowed_channels:
                    results.append(
                        SemanticEventRecord(
                            family="guard_failure",
                            name=f"channel_not_allowed:{turn.channel}",
                            source="guard",
                            confidence=1.0,
                            payload={"guard": guard.kind, "allowed": sorted(allowed_channels)},
                        )
                    )
            elif guard.kind == "fact_required":
                if guard.value not in working_facts:
                    results.append(
                        SemanticEventRecord(
                            family="guard_failure",
                            name=f"missing_required_fact:{guard.value}",
                            source="guard",
                            confidence=1.0,
                            payload={"guard": guard.kind, "fact": guard.value},
                        )
                    )
        return results

    def _condition_matches(
        self,
        condition: Condition,
        event_keys: set[str],
        working_facts: dict[str, object],
        turn: "RuntimeTurn | None" = None,
        *,
        step: Step | None = None,
        chosen_outcome_event: str | None = None,
        active_pending_tool_ref: str | None = None,
    ) -> bool:
        """Per-condition match against the current routing context.

        Edge-owned-outcomes contract:

        - ``OutcomeCondition`` fires iff the workflow-routing classifier
          chose this edge's ``event`` for this turn (``chosen_outcome_event``
          is the value extracted from the most recent
          ``family="routing", name="outcome_resolved"`` semantic event).
        - ``ToolOutcomeCondition`` fires when the kernel sees the matching
          ``tool_outcome:<outcome>`` event key. ``tool_ref`` (when set)
          must match the active pending tool the kernel is resolving for
          this multi-hop step; ``None`` is allowed only when the step has
          a single tool binding (validated upstream on ``Step``).
        - All other condition kinds map to the same semantics they had
          before the migration; only their field names changed.
        """
        if isinstance(condition, OtherwiseCondition):
            return True
        if isinstance(condition, OutcomeCondition):
            return chosen_outcome_event is not None and chosen_outcome_event == condition.event
        if isinstance(condition, FactPresentCondition):
            return condition.fact_name in working_facts
        if isinstance(condition, FactEqualsCondition):
            if condition.fact_name not in working_facts:
                return False
            return fact_value_equals(working_facts[condition.fact_name], condition.expected)
        if isinstance(condition, FactMissingCondition):
            return condition.fact_name not in working_facts
        if isinstance(condition, AllRequiredFactsPresentCondition):
            if step is None:
                return False
            required_fact_names = self._step_fact_requirement_names(step)
            return bool(required_fact_names) and all(
                fact_name in working_facts for fact_name in required_fact_names
            )
        if isinstance(condition, GuardFailureCondition):
            return f"guard_failure:{condition.guard_id}" in event_keys
        if isinstance(condition, ToolOutcomeCondition):
            if f"tool_outcome:{condition.outcome}" not in event_keys:
                return False
            if condition.tool_ref is None:
                # Step-level validator already enforced this is unambiguous
                # (single tool binding); accept the outcome as-is.
                return True
            # Tool-ref scoped: the active pending tool must match. None
            # of the kernel's tool execution paths today carry a tool_ref
            # in the matching context, so absent that piece this fires
            # whenever the named tool is the only candidate. The richer
            # ``active_pending_tool_ref`` argument is plumbed through for
            # the future scoped-dispatch case.
            if active_pending_tool_ref is not None:
                return active_pending_tool_ref == condition.tool_ref
            return True
        if isinstance(condition, AttachmentPresentCondition):
            if turn is None or not turn.attachments:
                return False
            if condition.any_of_kinds is not None:
                attachment_kinds = {a.kind for a in turn.attachments}
                if not attachment_kinds.intersection(condition.any_of_kinds):
                    return False
            if condition.all_of_kinds is not None:
                attachment_kinds = {a.kind for a in turn.attachments}
                if not set(condition.all_of_kinds).issubset(attachment_kinds):
                    return False
            return True
        if isinstance(condition, ViewReadyCondition):
            # Production path: event key emitted by _understand_turn.
            if f"view_ready:{condition.view_kind}" in event_keys:
                if condition.any_of_kinds is not None and turn is not None:
                    attachment_kinds = {a.kind for a in turn.attachments}
                    if not attachment_kinds.intersection(condition.any_of_kinds):
                        return False
                return True
            # Metadata path: turn passed directly (worker dispatch / unit tests).
            if turn is not None and turn.event_type == "system_event":
                meta = turn.metadata
                if (
                    meta.get("system_event_kind") == "view_ready"
                    and meta.get("view_kind") == condition.view_kind
                ):
                    if condition.any_of_kinds is not None:
                        attachment_kinds = {a.kind for a in turn.attachments}
                        if not attachment_kinds.intersection(condition.any_of_kinds):
                            return False
                    return True
            return False
        return False

    def _agent_capability_fallback(self, agent_document: AgentDocument) -> str:
        manifest = agent_document.agent_capability_manifest
        if manifest is None:
            return "I'm here to help answer questions and guide you through the next step."
        identity = (manifest.assistant_identity or "").strip()
        capabilities = self._spoken_capability_list(manifest.capabilities[:3])
        if identity and capabilities:
            return f"{identity} I can help with {capabilities}."
        if identity:
            return identity
        if capabilities:
            return f"I can help with {capabilities}."
        return "I'm here to help answer questions and guide you through the next step."

    def _activity_orientation_fallback(self, agent_document: AgentDocument) -> str:
        manifest = agent_document.agent_capability_manifest
        if manifest is not None and manifest.capabilities:
            capabilities = self._spoken_capability_list(manifest.capabilities[:3])
            if capabilities:
                return f"I'm here to help with {capabilities}."
        return "I'm here to help with your questions and the next step in this conversation."

    def _audio_check_fallback(self) -> str:
        return "Yes, I can hear you. How can I help?"

    def _generic_intent_response(
        self,
        *,
        agent_document: AgentDocument,
        semantic_events: list[SemanticEventRecord],
    ) -> str | None:
        """Framework-side handler for universal outcomes the kernel ships with.

        ``classifier.prompt.UNIVERSAL_OUTCOMES`` makes ``audio_check``,
        ``agent_identity_question``, ``agent_capability_question``, and
        ``activity_status_question`` part of every step's classifier
        catalog. When the classifier picks one but the author hasn't
        wired a transition for it (so ``transition_id`` is ``None`` on
        the routing event), we emit the framework's stock reply instead
        of letting the conversation stall on ``otherwise``.
        """
        chosen = self._chosen_outcome_event(semantic_events)
        if chosen == "audio_check":
            return self._audio_check_fallback()
        if chosen == "agent_identity_question":
            return self._agent_capability_fallback(agent_document)
        if chosen == "agent_capability_question":
            return self._agent_capability_fallback(agent_document)
        if chosen == "activity_status_question":
            return self._activity_orientation_fallback(agent_document)
        return None

    @staticmethod
    def _normalize_conversation_outcome(terminal_disposition: str | None) -> str | None:
        raw = (terminal_disposition or "").strip().lower()
        if not raw:
            return None
        return _TERMINAL_OUTCOME_ALIASES.get(raw)

    def _select_tool_to_run(self, tool_policy: list[ToolBinding]) -> ToolBinding | None:
        required = [binding for binding in tool_policy if binding.mode == "required"]
        if required:
            return required[0]
        allowed = [binding for binding in tool_policy if binding.mode in {"allowed", "optional"}]
        return allowed[0] if allowed else None

    # ── Action config execution (Phase 5 of runtime redesign) ──────────

    # ── Response context builder (Phase 3 of runtime redesign) ─────────

    def _build_response_context(
        self,
        *,
        conversation: ConversationState,
        state: Step,
        turn: RuntimeTurn,
        response_mode: str = "answer_question",
        response_directive: str | None = None,
        working_facts: dict[str, object] | None = None,
        missing_facts: list[str] | None = None,
        latest_action_outcome: ActionOutcomeSummary | None = None,
        previous_step: Step | None = None,
        transition_reason_code: str | None = None,
        transition_intent: str | None = None,
        transition_natural_reason: str | None = None,
        transition_narrative: TransitionNarrative | None = None,
        topic_freshness: str = "unknown",
        grounding_policy: KnowledgeGroundingPolicy | None = None,
        retrieval_evidence: list[RetrievalChunk] | None = None,
        retrieval_grade: Literal["pass", "weak", "fail", "absent"] = "absent",
    ) -> RenderContext:
        """Build a structured rendering context from the current turn state.

        The kernel calls this after semantic planning to pass bounded,
        curated context to the LLM renderer.
        """
        facts = self._curate_prompt_facts(
            self._filter_narration_facts(
                working_facts or conversation.facts,
                dict(conversation.metadata.get("__ruhu_fact_metadata__", {})),
            )
        )
        gen_context = self._resolve_response_generation_context(
            conversation=conversation, state=state, turn=turn,
        )
        system_prompt = gen_context.system_prompt if gen_context else None

        # Build recent messages from trace store. Topic shifts force a fresh
        # answer path by dropping stale assistant answer context.
        recent = self._messages_for_topic_freshness(
            self._recent_dialogue_messages(conversation.conversation_id, limit=8),
            topic_freshness=topic_freshness,
            response_mode=response_mode,
        )

        fact_requirement_names = self._step_fact_requirement_names(state)

        # Compute missing facts for steps that require additional details
        if missing_facts is None and state.fact_requirements:
            all_facts = set(working_facts or conversation.facts)
            missing_facts = [f for f in fact_requirement_names if f not in all_facts]
        missing_facts = missing_facts or []

        # Constraints: don't re-ask for facts we already have
        collected_fact_names = list((working_facts or conversation.facts).keys())
        constraints = ResponseConstraintSet(
            do_not_ask_for=collected_fact_names,
            must_not_claim=[],
        )
        if latest_action_outcome and latest_action_outcome.user_visible_fields:
            constraints.must_mention = list(latest_action_outcome.user_visible_fields.keys())
            constraints.must_not_claim = list(latest_action_outcome.user_visible_fields.keys())

        if missing_facts:
            constraints.response_max_sentences = 2
        elif turn.channel in {"phone", "voice"}:
            constraints.response_max_sentences = 3

        # Artifact context for grounded follow-up references (doc 19)
        focused_artifact = None
        active_artifact_count = 0
        pending_action_summary = None
        pending_permission_summary = None
        grounding_summary: dict[str, object] = {}
        commitment_summary = None
        active_repair = None
        policy_outcome = None
        status_trail_summary: list[dict[str, object]] = []
        narrator_mode = "llm" if self._dialogue_generator is not None else "deterministic"
        pacing = pacing_policy_for_channel(
            turn.channel,
            overrides=self._interaction_pacing_overrides_for_step(state),
        )
        latency_budget_ms = pacing.soft_timeout_ms
        control = getattr(conversation, "control_state", None)
        if control is not None:
            active_artifact_count = len(control.active_artifacts)
            if control.pending_action is not None:
                pending_action_summary = {
                    "action_id": control.pending_action.action_id,
                    "action_type": control.pending_action.action_type,
                    "status": control.pending_action.status,
                    "tool_ref": control.pending_action.tool_ref,
                    "action_label": control.pending_action.action_label,
                    "target_ref": control.pending_action.target_ref,
                }
                commitment_summary = dict(control.pending_action.commitment or {}) or None
            if control.pending_permission is not None:
                pending_permission_summary = {
                    "request_id": control.pending_permission.request_id,
                    "permission_kind": control.pending_permission.permission_kind,
                    "status": control.pending_permission.status,
                    "target_ref": control.pending_permission.target_ref,
                }
                policy_outcome = f"{control.pending_permission.permission_kind}:{control.pending_permission.status}"
            grounding_summary = {
                "acknowledged_fact_keys": list(control.grounding.acknowledged_fact_keys),
                "acknowledged_requests": list(control.grounding.acknowledged_requests),
                "last_acknowledged_activity_id": control.grounding.last_acknowledged_activity_id,
                "last_user_visible_status": control.grounding.last_user_visible_status,
                "unresolved_points": list(control.grounding.unresolved_points),
            }
            if control.active_repair is not None:
                active_repair = {
                    "repair_kind": control.active_repair.repair_kind,
                    "target_ref": control.active_repair.target_ref,
                    "summary": control.active_repair.summary,
                }
            status_trail_summary = self._status_trail_summary(conversation)
            if control.current_focus and control.current_focus.artifact_id:
                for art in control.active_artifacts:
                    if art.artifact_id == control.current_focus.artifact_id:
                        # Only expose prompt-eligible fields (doc 19)
                        focused_artifact = {
                            "artifact_type": art.artifact_type,
                            "status": art.status,
                            "title": art.title,
                            **art.user_visible_fields,
                        }
                        break
        runtime_projection = self._build_runtime_projection(
            conversation=conversation,
            state=state,
            missing_facts=missing_facts,
            recent_messages=recent,
            policy_outcome=policy_outcome,
        )
        turn_interpretation = self._interpret_control_intent(
            state=state,
            turn=turn,
            working_facts=working_facts or conversation.facts,
            missing_facts=missing_facts,
            runtime_projection=runtime_projection,
        )
        fallback_text = (
            self._safe_fallback_text_for_response_mode(
                state=state,
                response_mode=response_mode,
                conversation=conversation,
            )
        )
        narration_contract = self._select_narration_contract(
            state=state,
            turn_interpretation=turn_interpretation,
            runtime_projection=runtime_projection,
            response_mode=response_mode,
            fallback_text=fallback_text,
            latest_action_outcome=latest_action_outcome or ActionOutcomeSummary(),
            constraints=constraints,
        )
        response_constraints = self._constraints_for_narration_contract(
            base_constraints=constraints,
            narration_contract=narration_contract,
            missing_facts=missing_facts,
        )
        conversation_runtime_projection = self._assemble_conversation_runtime_projection(
            runtime_projection=runtime_projection,
            turn_interpretation=turn_interpretation,
            narration_contract=narration_contract,
        )
        try:
            journey_context = self._build_journey_context(
                conversation=conversation,
                agent_document=None,
                state=state,
                turn=turn,
                previous_step=previous_step,
                transition_reason_code=transition_reason_code,
                transition_intent=transition_intent,
                transition_natural_reason=transition_natural_reason,
                topic_freshness=topic_freshness,
            )
        except Exception:
            journey_context = JourneyContext(
                current_step_id=state.id,
                current_step_capabilities=self._step_capabilities(state),
                current_step_name=state.name,
                current_step_purpose=summarize_step(state),
                previous_step_id=previous_step.id if previous_step is not None else None,
                previous_step_name=previous_step.name if previous_step is not None else None,
                transition_reason_code=transition_reason_code,
                transition_intent=transition_intent,
                transition_natural_reason=transition_natural_reason,
                current_user_text=turn.text,
                topic_freshness=topic_freshness,  # type: ignore[arg-type]
                pending_facts=build_pending_fact_contexts(
                    state,
                    working_facts or conversation.facts,
                    triggered_by=transition_intent or transition_reason_code,
                    triggered_in_step=previous_step.id if previous_step is not None else None,
                ),
                authored_guidance=build_authored_step_guidance(state),
            )

        return RenderContext(
            conversation_id=conversation.conversation_id,
            organization_id=conversation.organization_id,
            agent_id=conversation.agent_id,
            response_mode=response_mode,
            journey=journey_context,
            response_directive=response_directive,
            channel=turn.channel,
            fallback_text=narration_contract.template_response or fallback_text,
            system_prompt=system_prompt,
            voice_style=state.response_policy.voice_style,
            facts=facts,
            recent_messages=recent,
            latest_action_outcome=latest_action_outcome or ActionOutcomeSummary(),
            pending_action_summary=pending_action_summary,
            pending_permission_summary=pending_permission_summary,
            grounding_summary=grounding_summary,
            commitment_summary=commitment_summary,
            active_repair=active_repair,
            policy_outcome=policy_outcome,
            allowed_claim_classes=self._allowed_claim_classes_for_narration_contract(
                narration_contract,
            ),
            narrator_mode=narrator_mode,
            latency_budget_ms=latency_budget_ms,
            transition_narrative=transition_narrative,
            status_trail_summary=status_trail_summary,
            metadata={
                "resolved_pacing": pacing.model_dump(mode="json"),
                "runtime_projection": runtime_projection.model_dump(mode="json"),
                "conversation_runtime_projection": (
                    conversation_runtime_projection.model_dump(mode="json")
                ),
            },
            focused_artifact=focused_artifact,
            active_artifact_count=active_artifact_count,
            constraints=response_constraints,
            retrieval_evidence=list(retrieval_evidence or []),
            grounding_policy=grounding_policy,
            retrieval_grade=retrieval_grade,
        )

    def _build_runtime_projection(
        self,
        *,
        conversation: ConversationState,
        state: Step,
        missing_facts: list[str],
        recent_messages: list[DialogueMessage],
        policy_outcome: str | None,
    ) -> RuntimeProjection:
        control = conversation.control_state
        pending_action = self._pending_action_runtime_summary(control.pending_action)
        pending_permission = self._pending_permission_runtime_summary(control.pending_permission)
        active_repair = self._active_repair_runtime_summary(control.active_repair)
        status_trail_summary = self._typed_status_trail_summary(conversation)
        waiting_on = self._runtime_waiting_on(
            state=state,
            missing_facts=missing_facts,
            pending_action=pending_action,
            pending_permission=pending_permission,
        )
        allowed_user_moves = self._allowed_user_moves_for_projection(
            state=state,
            missing_facts=missing_facts,
            pending_action=pending_action,
            pending_permission=pending_permission,
        )
        runtime_activity_status = self._runtime_activity_status_for_projection(
            waiting_on=waiting_on,
            pending_action=pending_action,
            pending_permission=pending_permission,
        )
        projected_recent_messages = [
            RecentDialogueMessage(role=message.role, text=message.text)
            for message in recent_messages[-5:]
        ]
        return RuntimeProjection(
            step=RuntimeStepSummary(
                step_id=state.id,
                step_capabilities=self._step_capabilities(state),
                step_name=state.name,
                step_goal=summarize_step(state),
                step_purpose=summarize_step(state),
            ),
            control=RuntimeControlSummary(
                pending_action=pending_action,
                pending_permission=pending_permission,
                active_repair=active_repair,
                policy_outcome=policy_outcome,
                runtime_activity_status=runtime_activity_status,
                status_trail_summary=status_trail_summary,
            ),
            user_contract=UserContractSummary(
                waiting_on=waiting_on,
                allowed_user_moves=allowed_user_moves,
            ),
            recent_messages=projected_recent_messages,
        )

    @staticmethod
    def _runtime_activity_status_for_projection(
        *,
        waiting_on: str,
        pending_action: PendingActionSummary | None,
        pending_permission: PendingPermissionSummary | None,
    ) -> RuntimeActivityStatus:
        if pending_permission is not None and pending_permission.status == "waiting":
            return "waiting_for_confirmation"
        if pending_action is not None:
            if pending_action.status == "confirmation_required":
                return "waiting_for_confirmation"
            if pending_action.status in {
                "queued",
                "starting",
                "running",
                "waiting_poll",
                "waiting_webhook",
                "cancelling",
                "completion_uncertain",
            }:
                return "running"
            if pending_action.status in {"slow", "retry_scheduled"}:
                return "slow"
            if pending_action.status == "completed":
                return "completed"
            if pending_action.status == "failed":
                return "failed"
            if pending_action.status == "cancelled":
                return "cancelled"
        if waiting_on.startswith("user_"):
            return "waiting_for_user"
        return "idle"

    def _interpret_control_intent(
        self,
        *,
        state: Step,
        turn: RuntimeTurn,
        working_facts: dict[str, object],
        missing_facts: list[str],
        runtime_projection: RuntimeProjection,
    ) -> TurnInterpretationSummary:
        text = (turn.text or "").strip()
        if not text:
            return TurnInterpretationSummary()

        pending_action = runtime_projection.control.pending_action
        pending_permission = runtime_projection.control.pending_permission
        if pending_action is not None or pending_permission is not None:
            confirmation_intent = self._classify_confirmation_intent(text)
            if confirmation_intent == "confirm":
                return TurnInterpretationSummary(detected_control_intent="confirm")
            if confirmation_intent == "cancel":
                return TurnInterpretationSummary(detected_control_intent="cancel")
            if self._is_pending_action_status_query(text):
                return TurnInterpretationSummary(detected_control_intent="ask_status")

        if missing_facts:
            if self._looks_like_direct_capture_response(text=text, missing_facts=missing_facts):
                return TurnInterpretationSummary(detected_control_intent="provide_requested_value")
            if self._is_bridge_appropriate_capture_turn(text=text, missing_facts=missing_facts):
                return TurnInterpretationSummary(
                    detected_control_intent="unclear",
                    bridge_appropriate=True,
                )

        normalized = " ".join(text.lower().split())
        if re.search(r"\b(?:repeat|say that again|come again)\b", normalized):
            return TurnInterpretationSummary(detected_control_intent="ask_repeat")
        if re.search(r"\b(?:actually|instead|different question)\b", normalized):
            return TurnInterpretationSummary(detected_control_intent="topic_shift")
        if "?" in text or re.search(r"\b(?:why|how|what do you mean|can you explain)\b", normalized):
            return TurnInterpretationSummary(detected_control_intent="ask_clarification")
        return TurnInterpretationSummary(detected_control_intent="unclear")

    @staticmethod
    def _looks_like_direct_capture_response(*, text: str, missing_facts: list[str]) -> bool:
        normalized = " ".join(text.strip().lower().split())
        if not normalized or not missing_facts:
            return False
        if "," in text or ";" in text or "\n" in text:
            return True
        labelish = "|".join(re.escape(name.replace("_", " ")) for name in missing_facts)
        if labelish and re.search(rf"\b(?:{labelish})\b\s*(?:is|=|:|-)\s*", normalized):
            return True
        return len(normalized) <= 100 and "?" not in normalized

    def _select_narration_contract(
        self,
        *,
        state: Step,
        turn_interpretation: TurnInterpretationSummary,
        runtime_projection: RuntimeProjection,
        response_mode: str,
        fallback_text: str | None,
        latest_action_outcome: ActionOutcomeSummary,
        constraints: ResponseConstraintSet,
    ) -> NarrationContract:
        llm_available = bool(self._dialogue_generator is not None and state.response_policy.render_with_llm)
        narration_mode = "templated"
        if llm_available:
            if response_mode == "acknowledge" and turn_interpretation.bridge_appropriate:
                narration_mode = "llm_bridged"
            elif response_mode in {"answer_question", "clarify", "status_explanation"}:
                narration_mode = "llm_only"

        template_response = fallback_text
        if response_mode == "status_explanation" and latest_action_outcome.summary:
            template_response = latest_action_outcome.summary

        pending_action = runtime_projection.control.pending_action
        must_not_imply_completion = bool(
            pending_action is not None
            and pending_action.status not in {"completed", "failed", "cancelled"}
            and response_mode in {"status_explanation", "acknowledge", "clarify", "answer_question"}
        )

        must_acknowledge: list[str] = []
        if turn_interpretation.bridge_appropriate:
            must_acknowledge.append("cooperative_stall")
        if turn_interpretation.detected_control_intent == "ask_status":
            must_acknowledge.append("status_request")

        return NarrationContract(
            response_mode=response_mode,
            narration_mode=narration_mode,
            template_response=template_response,
            must_acknowledge=must_acknowledge,
            must_not_imply_completion=must_not_imply_completion,
            must_not_repeat_prompt=(
                response_mode == "acknowledge" and turn_interpretation.bridge_appropriate
            ),
            constraints=constraints.model_copy(),
        )

    @staticmethod
    def _constraints_for_narration_contract(
        *,
        base_constraints: ResponseConstraintSet,
        narration_contract: NarrationContract,
        missing_facts: list[str],
    ) -> ResponseConstraintSet:
        merged = base_constraints.model_copy(deep=True)
        if narration_contract.must_not_repeat_prompt:
            for fact_name in missing_facts:
                if fact_name not in merged.do_not_ask_for:
                    merged.do_not_ask_for.append(fact_name)
        if narration_contract.must_not_imply_completion:
            for claim in ("completion", "completed", "committed"):
                if claim not in merged.must_not_claim:
                    merged.must_not_claim.append(claim)
        return merged

    def _allowed_claim_classes_for_narration_contract(
        self,
        narration_contract: NarrationContract,
    ) -> list[str]:
        allowed = self._allowed_claim_classes_for_response_mode(
            narration_contract.response_mode,
        )
        if not narration_contract.must_not_imply_completion:
            return allowed
        return [claim for claim in allowed if claim != "success"] or ["partial"]

    @staticmethod
    def _assemble_conversation_runtime_projection(
        *,
        runtime_projection: RuntimeProjection,
        turn_interpretation: TurnInterpretationSummary,
        narration_contract: NarrationContract,
    ) -> ConversationRuntimeProjection:
        return ConversationRuntimeProjection(
            runtime=runtime_projection,
            turn_interpretation=turn_interpretation,
            narration=narration_contract,
        )

    @staticmethod
    def _pending_action_runtime_summary(
        pending_action: PendingActionState | None,
    ) -> PendingActionSummary | None:
        if pending_action is None:
            return None
        return PendingActionSummary(
            action_id=pending_action.action_id,
            action_type=pending_action.action_type,
            status=pending_action.status,
            tool_ref=pending_action.tool_ref,
            action_label=pending_action.action_label,
            target_ref=pending_action.target_ref,
        )

    @staticmethod
    def _pending_permission_runtime_summary(
        pending_permission: PendingPermissionState | None,
    ) -> PendingPermissionSummary | None:
        if pending_permission is None:
            return None
        return PendingPermissionSummary(
            request_id=pending_permission.request_id,
            permission_kind=pending_permission.permission_kind,
            status=pending_permission.status,
            target_ref=pending_permission.target_ref,
        )

    @staticmethod
    def _active_repair_runtime_summary(
        active_repair: RepairContext | None,
    ) -> ActiveRepairSummary | None:
        if active_repair is None:
            return None
        return ActiveRepairSummary(
            repair_kind=active_repair.repair_kind,
            target_ref=active_repair.target_ref,
            summary=active_repair.summary,
        )

    def _typed_status_trail_summary(
        self,
        conversation: ConversationState,
    ) -> list[StatusTrailSummaryItem]:
        return [
            StatusTrailSummaryItem(
                item_type=str(item["item_type"]),
                summary=str(item["summary"]),
                source_ref=str(item["source_ref"]) if item.get("source_ref") is not None else None,
            )
            for item in self._status_trail_summary(conversation)
        ]

    @staticmethod
    def _runtime_waiting_on(
        *,
        state: Step,
        missing_facts: list[str],
        pending_action: PendingActionSummary | None,
        pending_permission: PendingPermissionSummary | None,
    ) -> str:
        if pending_permission is not None and pending_permission.status == "waiting":
            return "user_confirmation"
        if pending_action is not None:
            if pending_action.status == "confirmation_required":
                return "user_confirmation"
            if pending_action.status in {
                "queued",
                "starting",
                "running",
                "waiting_poll",
                "waiting_webhook",
                "slow",
                "retry_scheduled",
                "cancelling",
            }:
                return "runtime_execution"
            if pending_action.status == "completion_uncertain":
                return "runtime_reconciliation"
        if missing_facts:
            return f"user_fact:{missing_facts[0]}"
        return "user_message"

    @staticmethod
    def _allowed_user_moves_for_projection(
        *,
        state: Step,
        missing_facts: list[str],
        pending_action: PendingActionSummary | None,
        pending_permission: PendingPermissionSummary | None,
    ) -> list[str]:
        if pending_permission is not None and pending_permission.status == "waiting":
            return ["confirm", "cancel", "ask_status", "ask_clarification"]
        if pending_action is not None:
            if pending_action.status == "confirmation_required":
                return ["confirm", "cancel", "ask_status", "ask_clarification"]
            if pending_action.status in {
                "queued",
                "starting",
                "running",
                "waiting_poll",
                "waiting_webhook",
                "slow",
                "retry_scheduled",
                "completion_uncertain",
            }:
                return ["ask_status", "cancel", "ask_clarification"]
            if pending_action.status == "cancelling":
                return ["ask_status", "ask_clarification"]
        if missing_facts:
            return ["provide_requested_value", "ask_clarification", "interrupt"]
        return ["provide_input", "ask_clarification", "topic_shift"]

    @staticmethod
    def _interaction_pacing_overrides_for_step(step: Step) -> dict[str, object]:
        # The generic AgentDocument Step intentionally drops the legacy
        # state-graph pacing fields (slow_threshold_ms, soft_timeout_ms,
        # endpointing_ms, turn_eagerness, interruptibility_policy). Read
        # defensively so this still works for documents that never authored
        # them — same pattern as api.py's interaction_pacing_overrides path.
        overrides: dict[str, object] = {}
        for field in (
            "slow_threshold_ms",
            "soft_timeout_ms",
            "endpointing_ms",
            "turn_eagerness",
            "interruptibility_policy",
        ):
            value = getattr(step, field, None)
            if value is not None:
                overrides[field] = value
        return overrides

    @staticmethod
    def _default_claim_class_for_response_mode(response_mode: str) -> str:
        mapping = {
            "confirm_success": "success",
            "explain_failure": "failure",
            "tool_error_fallback": "failure",
            "tool_confirmation_required": "policy",
            "status_explanation": "pending",
            "policy_blocked": "policy",
            "repair_response": "repair",
            "completion_uncertain": "uncertain",
            "ask_missing_fact": "pending",
            "activity_started": "pending",
            "activity_completed": "success",
            "activity_failed": "failure",
            "interrupt_acknowledged": "repair",
            "transition_bridge": "partial",
            "clarify": "pending",
            "acknowledge": "partial",
            "entry": "partial",
            "answer_question": "partial",
            "handoff": "policy",
            "close": "partial",
        }
        return mapping.get(response_mode, "partial")

    def _allowed_claim_classes_for_response_mode(self, response_mode: str) -> list[str]:
        claim_class = self._default_claim_class_for_response_mode(response_mode)
        if response_mode == "answer_question":
            return ["partial", "pending", "repair"]
        if response_mode == "acknowledge":
            return ["partial", "repair", "pending"]
        if response_mode == "status_explanation":
            return ["pending", "uncertain", "repair", "partial"]
        if response_mode == "close":
            return ["partial", "policy"]
        if response_mode == "entry":
            return ["partial", "pending"]
        return [claim_class]

    def _coerce_render_output(
        self,
        rendered: object,
        *,
        response_ctx: RenderContext,
    ) -> RenderOutput | None:
        if isinstance(rendered, RenderOutput):
            text = rendered.text.strip()
            if not text:
                return None
            if text != rendered.text:
                return rendered.model_copy(update={"text": text})
            return rendered
        if isinstance(rendered, str):
            text = rendered.strip()
            if not text:
                return None
            return RenderOutput(
                text=text,
                claimed_class=self._default_claim_class_for_response_mode(response_ctx.response_mode),
            )
        return None

    def _validate_render_output(
        self,
        output: RenderOutput,
        *,
        response_ctx: RenderContext,
    ) -> RenderOutput | None:
        allowed = response_ctx.allowed_claim_classes or [
            self._default_claim_class_for_response_mode(response_ctx.response_mode)
        ]
        if output.claimed_class in allowed:
            return output
        fallback_text = (response_ctx.fallback_text or "").strip()
        if not fallback_text:
            return None
        return RenderOutput(
            text=fallback_text,
            claimed_class=allowed[0],
        )

    @staticmethod
    def _append_interaction_event(
        events: list[SemanticEventRecord],
        *,
        name: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        events.append(
            SemanticEventRecord(
                family="interaction",
                name=name,
                source="system",
                confidence=1.0,
                payload=dict(payload or {}),
            )
        )

    @staticmethod
    def _append_grounding_updated_event(
        events: list[SemanticEventRecord],
        *,
        acknowledged_fact_keys: list[str],
    ) -> None:
        if not acknowledged_fact_keys:
            return
        events.append(
            SemanticEventRecord(
                family="grounding",
                name="updated",
                source="system",
                confidence=1.0,
                payload={"acknowledged_fact_keys": list(acknowledged_fact_keys)},
            )
        )

    @staticmethod
    def _append_narration_event(
        events: list[SemanticEventRecord],
        *,
        name: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        events.append(
            SemanticEventRecord(
                family="narration",
                name=name,
                source="system",
                confidence=1.0,
                payload=dict(payload or {}),
            )
        )

    @staticmethod
    def _merge_acknowledged_fact_keys(
        conversation: ConversationState,
        output: RenderOutput,
    ) -> list[str]:
        if not output.acknowledged_fact_keys:
            return []
        grounding = conversation.control_state.grounding
        new_keys: list[str] = []
        for key in output.acknowledged_fact_keys:
            if key in conversation.facts and key not in grounding.acknowledged_fact_keys:
                grounding.acknowledged_fact_keys.append(key)
                new_keys.append(key)
        return new_keys

    def _render_from_context_output(
        self,
        *,
        conversation: ConversationState,
        state: Step,
        turn: RuntimeTurn,
        response_ctx: RenderContext,
        semantic_events: list[SemanticEventRecord] | None = None,
        on_first_sentence: object | None = None,
    ) -> RenderOutput | None:
        if self._dialogue_generator is None:
            if semantic_events is not None:
                self._append_narration_event(
                    semantic_events,
                    name="narration_fallback",
                    payload={
                        "response_mode": response_ctx.response_mode,
                        "narrator_mode": response_ctx.narrator_mode,
                        "allowed_claim_classes": list(response_ctx.allowed_claim_classes),
                        "fallback_used": True,
                        "fallback_reason": "generator_unavailable",
                    },
                )
            return None
        gen_context = self._resolve_response_generation_context(
            conversation=conversation,
            state=state,
            turn=turn,
        )
        rendered = self._dialogue_generator.render_from_context(
            response_ctx,
            provider=gen_context.provider if gen_context else None,
            model=gen_context.model if gen_context else None,
            on_first_sentence=on_first_sentence if callable(on_first_sentence) else None,
        )
        output = self._coerce_render_output(rendered, response_ctx=response_ctx)
        validation_fallback_reason: str | None = None
        if output is None and semantic_events is not None:
            self._append_narration_event(
                semantic_events,
                name="narration_fallback",
                payload={
                    "response_mode": response_ctx.response_mode,
                    "narrator_mode": response_ctx.narrator_mode,
                    "allowed_claim_classes": list(response_ctx.allowed_claim_classes),
                    "fallback_used": True,
                    "fallback_reason": "empty_render_output",
                },
            )
        if output is not None:
            allowed = response_ctx.allowed_claim_classes or [
                self._default_claim_class_for_response_mode(response_ctx.response_mode)
            ]
            if output.claimed_class not in allowed:
                validation_fallback_reason = "claim_class_invalid"
            output = self._validate_render_output(output, response_ctx=response_ctx)
        if output is not None:
            if semantic_events is not None and validation_fallback_reason is not None:
                self._append_narration_event(
                    semantic_events,
                    name="narration_fallback",
                    payload={
                        "response_mode": response_ctx.response_mode,
                        "narrator_mode": response_ctx.narrator_mode,
                        "allowed_claim_classes": list(response_ctx.allowed_claim_classes),
                        "fallback_used": True,
                        "fallback_reason": validation_fallback_reason,
                    },
                )
            new_grounding_keys = self._merge_acknowledged_fact_keys(conversation, output)
            if semantic_events is not None and new_grounding_keys:
                self._append_grounding_updated_event(
                    semantic_events,
                    acknowledged_fact_keys=new_grounding_keys,
                )
            if semantic_events is not None:
                self._append_narration_event(
                    semantic_events,
                    name="narration_rendered",
                    payload={
                        "response_mode": response_ctx.response_mode,
                        "narrator_mode": response_ctx.narrator_mode,
                        "claimed_class": output.claimed_class,
                        "allowed_claim_classes": list(response_ctx.allowed_claim_classes),
                        "acknowledged_fact_keys": list(output.acknowledged_fact_keys),
                        "fallback_used": validation_fallback_reason is not None,
                    },
                )
        return output

    def _render_text_from_context(
        self,
        *,
        conversation: ConversationState,
        state: Step,
        turn: RuntimeTurn,
        response_mode: str,
        response_directive: str | None = None,
        working_facts: dict[str, object] | None = None,
        missing_facts: list[str] | None = None,
        latest_action_outcome: ActionOutcomeSummary | None = None,
        semantic_events: list[SemanticEventRecord] | None = None,
        on_first_sentence: object | None = None,
        fallback_text_override: str | None = None,
        previous_step: Step | None = None,
        transition_reason_code: str | None = None,
        transition_intent: str | None = None,
        transition_natural_reason: str | None = None,
        transition_narrative: TransitionNarrative | None = None,
        topic_freshness: str = "unknown",
        grounding_policy: KnowledgeGroundingPolicy | None = None,
        retrieval_evidence: list[RetrievalChunk] | None = None,
        retrieval_grade: Literal["pass", "weak", "fail", "absent"] = "absent",
    ) -> tuple[str, RenderContext, bool]:
        response_ctx = self._build_response_context(
            conversation=conversation,
            state=state,
            turn=turn,
            response_mode=response_mode,
            response_directive=response_directive,
            working_facts=working_facts,
            missing_facts=missing_facts,
            latest_action_outcome=latest_action_outcome,
            previous_step=previous_step,
            transition_reason_code=transition_reason_code,
            transition_intent=transition_intent,
            transition_natural_reason=transition_natural_reason,
            transition_narrative=transition_narrative,
            topic_freshness=topic_freshness,
            grounding_policy=grounding_policy,
            retrieval_evidence=retrieval_evidence,
            retrieval_grade=retrieval_grade,
        )
        fallback_text = (
            fallback_text_override.strip()
            if isinstance(fallback_text_override, str) and fallback_text_override.strip()
            else (response_ctx.fallback_text or "").strip()
        )
        try:
            render_output = self._render_from_context_output(
                conversation=conversation,
                state=state,
                turn=turn,
                response_ctx=response_ctx,
                semantic_events=semantic_events,
                on_first_sentence=on_first_sentence,
            )
        except Exception as exc:
            if semantic_events is not None:
                self._append_narration_event(
                    semantic_events,
                    name="narration_fallback",
                    payload={
                        "response_mode": response_ctx.response_mode,
                        "narrator_mode": response_ctx.narrator_mode,
                        "allowed_claim_classes": list(response_ctx.allowed_claim_classes),
                        "fallback_used": True,
                        "fallback_reason": "render_exception",
                        "error_type": type(exc).__name__,
                    },
                )
            render_output = None
        if render_output is not None and render_output.text.strip():
            return render_output.text, response_ctx, True
        return fallback_text, response_ctx, False

    @staticmethod
    def _topic_tokens(text: str) -> set[str]:
        stop_words = {
            "a", "an", "and", "are", "about", "all", "am", "any", "be", "but", "by",
            "can", "could", "did", "do", "does", "for", "from", "get", "got", "had",
            "has", "have", "hello", "help", "here", "how", "i", "if", "in", "into",
            "is", "it", "just", "kind", "let", "like", "me", "more", "my", "need",
            "now", "of", "okay", "on", "or", "please", "question", "really", "so",
            "tell", "that", "the", "their", "them", "there", "these", "they", "this",
            "to", "understand", "want", "what", "when", "where", "which", "who",
            "why", "with", "would", "you", "your",
        }
        normalized = re.findall(r"[a-z0-9]+", (text or "").lower())
        tokens: set[str] = set()
        for raw_token in normalized:
            if raw_token in stop_words:
                continue
            token = raw_token
            for suffix in ("ing", "ers", "ies", "ied", "ed", "es", "s"):
                if len(token) > 4 and token.endswith(suffix):
                    token = token[: -len(suffix)]
                    break
            if len(token) >= 3 and token not in stop_words:
                tokens.add(token)
        return tokens

    @staticmethod
    def _topic_similarity(current_text: str, previous_text: str) -> float:
        current_tokens = ConversationKernel._topic_tokens(current_text)
        previous_tokens = ConversationKernel._topic_tokens(previous_text)
        if not current_tokens or not previous_tokens:
            return 0.0
        overlap = current_tokens.intersection(previous_tokens)
        union = current_tokens.union(previous_tokens)
        return len(overlap) / len(union) if union else 0.0

    def _detect_topic_freshness(
        self,
        *,
        conversation: ConversationState,
        turn: RuntimeTurn,
    ) -> str:
        text = (turn.text or "").strip().lower()
        if not text:
            return "unknown"
        obvious_shift_markers = (
            "what about",
            "how about",
            "instead",
            "another question",
            "different question",
        )
        if any(marker in text for marker in obvious_shift_markers):
            return "topic_shift"
        if len(text.split()) <= 3:
            return "unknown"

        recent_messages = self._recent_dialogue_messages(conversation.conversation_id, limit=6)
        recent_assistant = next(
            (message.text for message in reversed(recent_messages) if message.role == "assistant"),
            "",
        )
        current_tokens = self._topic_tokens(text)
        previous_tokens = self._topic_tokens(recent_assistant)
        if not current_tokens or not previous_tokens:
            return "unknown"
        similarity = self._topic_similarity(text, recent_assistant)
        if similarity == 0.0:
            return "topic_shift"
        if similarity >= 0.35:
            return "same_topic"
        if similarity <= 0.12:
            return "topic_shift"
        if current_tokens.intersection(previous_tokens):
            return "mixed"
        return "unknown"

    @staticmethod
    def _user_facing_transition_reason(reason: str | None) -> str | None:
        normalized = _WHITESPACE_RE.sub(" ", str(reason or "")).strip()
        if not normalized:
            return None
        lowered = normalized.lower()
        if lowered.startswith("the user is asking about "):
            return None
        if lowered.startswith("we are still working through "):
            return None
        if lowered.startswith("we should continue with "):
            return None
        if lowered.startswith("action code ") and lowered.endswith(" happened."):
            return None
        if lowered.startswith("we already have the "):
            return None
        return normalized

    def _render_transition_preamble(
        self,
        *,
        conversation: ConversationState,
        from_step: Step,
        to_step: Step,
        turn: RuntimeTurn,
        working_facts: dict[str, object],
        semantic_events: list[SemanticEventRecord],
        transition_reason_code: str | None = None,
        transition_intent: str | None = None,
        transition_natural_reason: str | None = None,
    ) -> str | None:
        narrative = build_transition_narrative(
            from_step=from_step,
            to_step=to_step,
            reason_code=transition_reason_code,
            transition_intent=transition_intent,
            natural_reason=self._user_facing_transition_reason(transition_natural_reason),
            bridge_required=True,
        )
        sanitized_reason = self._user_facing_transition_reason(transition_natural_reason)
        fallback = (
            sanitized_reason
            or f"Let me help with {to_step.name.lower()}."
        )
        rendered_text, _response_ctx, used_render = self._render_text_from_context(
            conversation=conversation,
            state=to_step,
            turn=turn,
            response_mode="transition_bridge",
            response_directive=(
                "Explain briefly why the conversation is moving here before the next request or action."
            ),
            working_facts=working_facts,
            semantic_events=semantic_events,
            fallback_text_override=fallback,
            previous_step=from_step,
            transition_reason_code=transition_reason_code,
            transition_intent=transition_intent,
            transition_natural_reason=sanitized_reason,
            transition_narrative=narrative,
        )
        return rendered_text if used_render and rendered_text.strip() else fallback

    def _status_trail_summary(self, conversation: ConversationState) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        control = conversation.control_state
        publish_pending_status = False
        if control.pending_action is not None:
            publish_pending_status = bool(control.pending_action.metadata.get("publish_status_trail"))
        elif control.pending_permission is not None:
            publish_pending_status = bool(
                control.pending_permission.user_visible_context.get("publish_status_trail")
            )
        if control.pending_action is not None and publish_pending_status:
            label = (
                control.pending_action.action_label
                or control.pending_action.tool_ref
                or control.pending_action.action_type
            )
            items.append(
                {
                    "item_type": "activity",
                    "summary": f"{label}: {control.pending_action.status}",
                    "source_ref": control.pending_action.action_id,
                }
            )
        if control.pending_permission is not None and publish_pending_status:
            label = control.pending_permission.user_visible_context.get("action_label") or control.pending_permission.permission_kind
            items.append(
                {
                    "item_type": "permission",
                    "summary": f"{label}: {control.pending_permission.status}",
                    "source_ref": control.pending_permission.request_id,
                }
            )
        if control.active_repair is not None:
            items.append(
                {
                    "item_type": "repair",
                    "summary": control.active_repair.summary or control.active_repair.repair_kind,
                    "source_ref": control.active_repair.target_ref,
                }
            )
        return items

    def _projected_status_trail_items(
        self,
        conversation: ConversationState,
        semantic_events: list[SemanticEventRecord],
    ) -> list[dict[str, object]]:
        items: dict[str, InteractionStatusItem] = {}
        control = conversation.control_state
        publish_pending_status = False
        if control.pending_action is not None:
            publish_pending_status = bool(control.pending_action.metadata.get("publish_status_trail"))
        elif control.pending_permission is not None:
            publish_pending_status = bool(
                control.pending_permission.user_visible_context.get("publish_status_trail")
            )
        if control.pending_action is not None and publish_pending_status:
            label = (
                control.pending_action.action_label
                or control.pending_action.tool_ref
                or control.pending_action.action_type
            )
            item_id = f"activity:{control.pending_action.action_id}"
            items[item_id] = InteractionStatusItem(
                item_id=item_id,
                item_type="activity",
                summary=f"{label}: {control.pending_action.status}",
                started_at=control.pending_action.started_at,
                source_ref=control.pending_action.action_id,
            )
        if control.pending_permission is not None and publish_pending_status:
            label = (
                control.pending_permission.user_visible_context.get("action_label")
                or control.pending_permission.permission_kind
            )
            item_id = f"permission:{control.pending_permission.request_id}"
            items[item_id] = InteractionStatusItem(
                item_id=item_id,
                item_type="permission",
                summary=f"{label}: {control.pending_permission.status}",
                started_at=control.pending_permission.started_at,
                source_ref=control.pending_permission.request_id,
            )
        if control.active_repair is not None:
            repair_ref = control.active_repair.target_ref or control.active_repair.repair_kind
            item_id = f"repair:{repair_ref}"
            items[item_id] = InteractionStatusItem(
                item_id=item_id,
                item_type="repair",
                summary=control.active_repair.summary or control.active_repair.repair_kind,
                source_ref=control.active_repair.target_ref,
            )
        for event in semantic_events:
            item = self._status_trail_item_from_semantic_event(event)
            if item is not None:
                items[item.item_id] = item
        return [item.model_dump(mode="json") for item in items.values()]

    def _status_trail_item_from_semantic_event(
        self,
        event: SemanticEventRecord,
    ) -> InteractionStatusItem | None:
        if event.family != "interaction":
            return None
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(milliseconds=_STATUS_TRAIL_COMPLETION_TTL_MS)
        payload = event.payload or {}
        if event.name in {"activity_completed", "activity_failed", "permission_resolved"} and not payload.get(
            "publish_status_trail", False
        ):
            return None
        if event.name == "activity_completed":
            source_ref = str(payload.get("invocation_id") or payload.get("tool_ref") or payload.get("step_id") or "activity")
            label = str(payload.get("tool_ref") or payload.get("step_id") or "activity")
            return InteractionStatusItem(
                item_id=f"activity:{source_ref}",
                item_type="activity",
                summary=f"{label}: completed",
                started_at=now,
                expires_at=expires_at,
                source_ref=source_ref,
            )
        if event.name == "activity_failed":
            source_ref = str(payload.get("invocation_id") or payload.get("tool_ref") or payload.get("step_id") or "activity")
            label = str(payload.get("tool_ref") or payload.get("step_id") or "activity")
            return InteractionStatusItem(
                item_id=f"activity:{source_ref}",
                item_type="activity",
                summary=f"{label}: failed",
                started_at=now,
                expires_at=expires_at,
                source_ref=source_ref,
            )
        if event.name == "permission_resolved":
            source_ref = str(payload.get("request_id") or "permission")
            permission_kind = str(payload.get("permission_kind") or "permission")
            resolution = str(payload.get("resolution") or "resolved")
            return InteractionStatusItem(
                item_id=f"permission:{source_ref}",
                item_type="permission",
                summary=f"{permission_kind}: {resolution}",
                started_at=now,
                expires_at=expires_at,
                source_ref=source_ref,
            )
        return None

    @staticmethod
    def _curate_prompt_facts(facts: dict[str, object]) -> dict[str, object]:
        """Select a bounded, prompt-safe subset of conversation facts."""
        curated: dict[str, object] = {}
        for key, value in facts.items():
            # Skip internal keys
            if key.startswith("_"):
                continue
            # Skip large values
            str_value = str(value)
            if len(str_value) > 500:
                continue
            curated[key] = value
            if len(curated) >= 30:
                break
        return curated

    @staticmethod
    def _filter_narration_facts(
        facts: dict[str, object],
        fact_metadata: dict[str, object],
    ) -> dict[str, object]:
        """Apply per-fact narration visibility before building render context."""
        visible: dict[str, object] = {}
        for name, value in facts.items():
            metadata = fact_metadata.get(name)
            if isinstance(metadata, dict):
                storage_policy = metadata.get("storage_policy")
                if isinstance(storage_policy, dict) and storage_policy.get("expose_to_narration") is False:
                    continue
            visible[name] = value
        return visible

    def _recent_dialogue_messages(
        self,
        conversation_id: str,
        *,
        limit: int = 8,
    ) -> list[DialogueMessage]:
        """Build bounded recent dialogue from turn traces."""
        try:
            traces = self._trace_store.list(conversation_id, limit=limit * 2)
        except Exception:
            return []
        messages: list[DialogueMessage] = []
        for trace in traces:
            trace_data = trace if isinstance(trace, dict) else (
                trace.model_dump() if hasattr(trace, "model_dump") else {}
            )
            # Extract user text from the turn
            turn_text = None
            turn_data = trace_data.get("turn")
            if isinstance(turn_data, dict):
                turn_text = turn_data.get("text")
            if turn_text and isinstance(turn_text, str) and turn_text.strip():
                messages.append(DialogueMessage(role="user", text=turn_text.strip()))
            # Extract assistant messages from emitted_messages
            emitted = trace_data.get("emitted_messages")
            if isinstance(emitted, list):
                for msg in emitted:
                    msg_text = msg.get("text") if isinstance(msg, dict) else (
                        getattr(msg, "text", None)
                    )
                    if msg_text and isinstance(msg_text, str) and msg_text.strip():
                        messages.append(DialogueMessage(role="assistant", text=msg_text.strip()))
        # Return most recent, capped
        return messages[-limit:] if len(messages) > limit else messages

    def _generate_direct_answer(
        self,
        *,
        conversation: ConversationState,
        state: Step,
        turn: RuntimeTurn,
        fallback_text: str,
        on_first_sentence: object | None = None,
    ) -> tuple[str | None, ResponseGenerationContext | None]:
        if self._dialogue_generator is None or not turn.text:
            return None, None
        context = self._resolve_response_generation_context(
            conversation=conversation,
            state=state,
            turn=turn,
        )
        if context is None:
            return None, None
        request = ResponseGenerationRequest(
            conversation_id=conversation.conversation_id,
            organization_id=conversation.organization_id,
            agent_id=conversation.agent_id,
            agent_version_id=conversation.agent_version_id,
            step_id=state.id,
            step_name=state.name,
            step_summary=summarize_step(state),
            channel=turn.channel,
            event_type=turn.event_type,
            user_text=turn.text,
            fallback_text=fallback_text,
            context=context,
        )
        try:
            generated = self._dialogue_generator.generate(
                request,
                on_first_sentence=on_first_sentence if callable(on_first_sentence) else None,
            )
        except Exception:
            return None, context
        if not isinstance(generated, str) or not generated.strip():
            return None, context
        return generated.strip(), context

    @staticmethod
    def _messages_for_topic_freshness(
        recent_messages: list[DialogueMessage],
        *,
        topic_freshness: str,
        response_mode: str,
    ) -> list[DialogueMessage]:
        if response_mode != "answer_question" or topic_freshness != "topic_shift":
            return recent_messages
        # A fresh answer should not anchor on the last assistant answer from the
        # previous topic. Keep lightweight user continuity only.
        return [message for message in recent_messages if message.role != "assistant"]

    def _resolve_response_generation_context(
        self,
        *,
        conversation: ConversationState,
        state: Step,
        turn: RuntimeTurn,
    ) -> ResponseGenerationContext | None:
        if self._response_generation_context_resolver is not None:
            try:
                resolved = self._response_generation_context_resolver(conversation, state, turn)
            except Exception:
                resolved = None
            if resolved is not None:
                return resolved
        payload = turn.metadata.get(_RESPONSE_CONTEXT_METADATA_KEY)
        if not isinstance(payload, dict):
            return None
        provider = payload.get("provider")
        model = payload.get("model")
        system_prompt = payload.get("system_prompt")
        metadata_payload = payload.get("metadata")
        metadata: dict[str, object] = {}
        if isinstance(metadata_payload, dict):
            metadata = {str(key): value for key, value in metadata_payload.items()}
        return ResponseGenerationContext(
            provider=None if provider is None else str(provider),
            model=None if model is None else str(model),
            system_prompt=None if system_prompt is None else str(system_prompt),
            metadata=metadata,
        )

    def _build_journey_context(
        self,
        *,
        conversation: ConversationState,
        agent_document: AgentDocument | None,
        state: Step,
        turn: RuntimeTurn,
        semantic_events: list[SemanticEventRecord] | None = None,
        previous_step: Step | None = None,
        transition_reason_code: str | None = None,
        transition_intent: str | None = None,
        transition_natural_reason: str | None = None,
        journey_summary: str | None = None,
        topic_freshness: str = "unknown",
    ) -> JourneyContext:
        return build_journey_context(
            conversation=conversation,
            agent_document=agent_document,
            step=state,
            turn=turn,
            semantic_events=semantic_events,
            previous_step=previous_step,
            transition_reason_code=transition_reason_code,
            transition_intent=transition_intent,
            transition_natural_reason=transition_natural_reason,
            journey_summary=journey_summary,
            topic_freshness=topic_freshness,
            recent_tool_outcomes=self._project_recent_tool_outcomes(conversation),
        )

    def _project_recent_tool_outcomes(
        self, conversation: ConversationState
    ) -> list[ToolOutcomeRecord]:
        """Project recent ``ToolInvocation`` entries into LLM-safe records.

        Reads from ``self._tool_runtime.list_conversation_invocations`` —
        the authoritative source. Falls back to an empty list when no
        tool runtime is configured.
        Each record is sanitized via ``sanitize_tool_outcome_for_llm``
        before being placed in the LLM context.
        """
        if self._tool_runtime is None:
            return []
        try:
            invocations = self._tool_runtime.list_conversation_invocations(
                conversation.conversation_id,
                organization_id=conversation.organization_id,
            )
        except Exception:
            # Tool-runtime failures must never block move selection.
            return []
        if not invocations:
            return []

        # Most recent first; cap at TOOL_OUTCOME_HISTORY_MAX.
        sorted_invocations = sorted(
            invocations, key=lambda i: i.updated_at, reverse=True
        )[:TOOL_OUTCOME_HISTORY_MAX]

        records: list[ToolOutcomeRecord] = []
        for inv in sorted_invocations:
            outcome_status = _map_tool_invocation_status_to_outcome(inv.status)
            truncated_output, was_truncated = _truncate_output_data(
                dict(inv.output or {}),
                budget_bytes=TOOL_OUTCOME_OUTPUT_BYTES_BUDGET,
            )
            summary = (
                f"{inv.tool_ref} → {outcome_status}"
                + (f" ({inv.error})" if inv.error else "")
            )
            if was_truncated:
                summary += " [output truncated]"
            record = ToolOutcomeRecord(
                tool_name=inv.tool_ref,
                invocation_id=inv.invocation_id,
                invoked_at=inv.created_at,
                completed_at=(
                    inv.updated_at
                    if outcome_status not in {"pending"}
                    else None
                ),
                status=outcome_status,  # type: ignore[arg-type]
                output_summary=summary,
                output_data=truncated_output,
                error_kind=(
                    None if outcome_status in {"success", "pending"} else outcome_status
                ),
            )
            records.append(sanitize_tool_outcome_for_llm(record))
        return records

    def _tool_refs_for_step(step: Step) -> list[str]:
        refs: list[str] = []
        for ref in list(step.tool_affordances or []):
            ref_text = str(ref or "").strip()
            if ref_text and ref_text not in refs:
                refs.append(ref_text)
        for binding in list(step.tool_policy or []):
            ref_value = binding.get("ref") if isinstance(binding, dict) else getattr(binding, "ref", "")
            ref_text = str(ref_value or "").strip()
            if ref_text and ref_text not in refs:
                refs.append(ref_text)
        return refs


    def _fallback_text_for_action_answer(
        self,
        state: Step,
        conversation: ConversationState,
    ) -> str:
        pending = conversation.control_state.pending_action
        if pending is not None and pending.action_label:
            label = str(pending.action_label).strip().rstrip(".")
            if label:
                return f"I'm still {label.lower()}."
        tool_refs = self._tool_refs_for_step(state)
        if "calendar.create_event" in tool_refs:
            return "I'm checking on the demo booking now."
        if "crm.submit_lead" in tool_refs:
            return "I'm checking on the demo request now."
        if "knowledge.lookup" in tool_refs:
            return "Let me explain that."
        return (
            state.response_policy.deterministic_fallback_text
            or "Let me help with that."
        )



# ─────────────────────────────────────────────────────────────────────────────
# WI-9 of doc 36: tool-outcome projection helpers (specs 35 / 34 §626-639).
#
# Pure utility functions — no coupling to the kernel instance, no coupling
# to the move-selection master flag.  P1 contract:
#   - helpers exist and are callable in isolation
#   - 5-record cap enforced
#   - byte budget enforced via in-place truncation
#   - sanitization stub returns input unchanged (real PII redaction in P3)
#   - NO production call site invokes them in P1; they are wired in P3 when
#     the LLM context starts including ``recent_tool_outcomes``.
# ─────────────────────────────────────────────────────────────────────────────


TOOL_OUTCOME_HISTORY_MAX = 5
"""Cap on records returned by ``build_tool_outcome_context`` (doc 35 §4)."""

TOOL_OUTCOME_OUTPUT_BYTES_BUDGET = 8 * 1024
"""Default byte budget for ``ToolOutcomeRecord.output_data`` (8 KiB)."""


_TOOL_CALL_STATUS_TO_OUTCOME: dict[str, str] = {
    "requested": "pending",
    "running": "pending",
    "confirmation_required": "pending",
    "success": "success",
    "blocked": "failed",
    "timeout": "timeout",
    "error": "failed",
    "cancelled": "failed",
}


def _coerce_tool_outcome_status(status: str) -> str:
    return _TOOL_CALL_STATUS_TO_OUTCOME.get(status, "failed")


_TOOL_INVOCATION_STATUS_TO_OUTCOME: dict[str, str] = {
    "pending": "pending",
    "waiting_confirmation": "pending",
    "queued": "pending",
    "running": "pending",
    "waiting_poll": "pending",
    "waiting_webhook": "pending",
    "retry_scheduled": "pending",
    "completed": "success",
    "failed": "failed",
    "blocked": "failed",
    "cancelled": "failed",
    "timed_out": "timeout",
    "dead_lettered": "failed",
}


def _map_tool_invocation_status_to_outcome(status: str) -> str:
    """Map runtime ``ToolInvocationStatus`` → LLM-safe ``ToolOutcomeRecord.status``.

    This is the runtime-truth equivalent of ``_coerce_tool_outcome_status``
    (which maps the trace-time ``ToolCallRecord`` status enum).  Used by
    ``ConversationKernel._project_recent_tool_outcomes`` per doc 39 WI-4.
    """
    return _TOOL_INVOCATION_STATUS_TO_OUTCOME.get(status, "failed")


def _truncate_output_data(
    data: dict[str, object],
    *,
    budget_bytes: int,
) -> tuple[dict[str, object], bool]:
    """Truncate ``data`` to ``budget_bytes`` of JSON-serialized size.

    Returns the (possibly empty) truncated dict and a ``truncated`` flag.
    P1 strategy: if serialization exceeds the budget, drop fields one at a
    time from the end until under budget.  If still over budget after all
    fields are dropped, return ``{}`` and ``True``.
    """
    import json as _json

    if not data:
        return {}, False
    encoded = _json.dumps(data, default=str).encode("utf-8")
    if len(encoded) <= budget_bytes:
        return dict(data), False
    # Iteratively drop trailing keys until the payload fits.
    keys = list(data.keys())
    truncated = dict(data)
    while keys:
        keys.pop()
        truncated = {k: data[k] for k in keys}
        encoded = _json.dumps(truncated, default=str).encode("utf-8") if truncated else b"{}"
        if len(encoded) <= budget_bytes:
            return truncated, True
    return {}, True


def build_tool_outcome_context(
    tool_calls: list[ToolCallRecord],
    *,
    history_max: int = TOOL_OUTCOME_HISTORY_MAX,
    output_bytes_budget: int = TOOL_OUTCOME_OUTPUT_BYTES_BUDGET,
) -> list[ToolOutcomeRecord]:
    """Project recent ``ToolCallRecord`` entries into LLM-safe outcome records.

    Caps the result at ``history_max`` (most recent first) and truncates each
    record's ``output_data`` to ``output_bytes_budget``.  Pure function — no
    coupling to feature flags, kernel state, or persistence layers.

    P1: defined and tested but **not invoked** by any production code path.
    P3+ wires this into the move-selection LLM context so the LLM can
    reference actual tool results when the user asks about them.
    """
    if not tool_calls:
        return []
    # Most recent first — input is in temporal order, so reverse before cap.
    selected = list(reversed(tool_calls))[:history_max]
    now = datetime.now(timezone.utc)
    records: list[ToolOutcomeRecord] = []
    for call in selected:
        outcome_status = _coerce_tool_outcome_status(call.status)
        truncated_output, was_truncated = _truncate_output_data(
            call.payload, budget_bytes=output_bytes_budget
        )
        summary = (
            f"{call.tool_ref} → {outcome_status}"
            + (f" ({call.reason})" if call.reason else "")
        )
        if was_truncated:
            summary += " [output truncated]"
        records.append(
            ToolOutcomeRecord(
                tool_name=call.tool_ref,
                invocation_id=call.invocation_id or f"unknown:{call.tool_ref}",
                invoked_at=now,  # Real timestamp lands when wired in P3
                completed_at=None if outcome_status == "pending" else now,
                status=outcome_status,  # type: ignore[arg-type]
                output_summary=summary,
                output_data=truncated_output,
                error_kind=None if outcome_status in {"success", "pending"} else outcome_status,
            )
        )
    return records


def sanitize_tool_outcome_for_llm(record: ToolOutcomeRecord) -> ToolOutcomeRecord:
    """Apply PII/safety redaction before LLM exposure (P3+).

    P1 stub: returns the input unchanged with ``pii_redacted=False``.  When
    the PII pipeline is wired in P3, this function will inspect
    ``output_data`` and either redact specific fields or empty the dict
    entirely (setting ``pii_redacted=True``) per the existing PII policy.
    """
    return record.model_copy(update={"pii_redacted": False})


_LOOP_COUNTER_METADATA_KEY = "__ruhu_loop_counter__"


def update_loop_counter_after_turn(
    conversation: ConversationState,
    *,
    step_before: str,
    step_after: str,
) -> None:
    """Mutate ``conversation.metadata`` to reflect the per-step turn count.

    Rules:
      - If ``step_before == step_after`` (turn stayed in the same step), the
        counter for that step is incremented by 1.
      - Otherwise (step changed), the counter for ``step_before`` is
        cleared and the counter for ``step_after`` is initialised to 0.

    The metadata mutation is in place; callers are expected to subsequently
    persist the conversation through the existing ``ConversationStore``.
    """
    counter_raw = conversation.metadata.get(_LOOP_COUNTER_METADATA_KEY)
    counter: dict[str, int] = (
        {str(k): int(v) for k, v in counter_raw.items() if isinstance(v, int)}
        if isinstance(counter_raw, dict)
        else {}
    )

    if step_before == step_after:
        counter[step_after] = counter.get(step_after, 0) + 1
    else:
        counter.pop(step_before, None)
        counter[step_after] = 0

    conversation.metadata[_LOOP_COUNTER_METADATA_KEY] = counter
