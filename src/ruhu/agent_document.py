from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .persona import BehavioralPersona
from .schemas import (
    ActionConfig,
    AgentCapabilityManifest,
    Condition,
    FactDef,
    FactRequirement,
    GuardDef,
    OtherwiseCondition,
    OutcomeCondition,
    ResponsePolicy,
    ToolBinding,
    ToolOutcomeCondition,
)

StepWorkloadClass = Literal["interactive", "deferred"]
StepExecutionIsolation = Literal["inline", "subprocess"]


class StepCompletion(BaseModel):
    disposition: str
    summary: str | None = None


class StepHandoff(BaseModel):
    target_type: Literal["queue", "agent", "phone_number"]
    target: str
    summary: str | None = None


class StepTransition(BaseModel):
    id: str
    when: Condition
    to_step_id: str
    label: str | None = None
    priority: int = 100


class ScenarioRoute(BaseModel):
    id: str
    from_scenario_id: str
    when: Condition
    to_scenario_id: str
    label: str | None = None
    priority: int = 100


class Step(BaseModel):
    id: str
    name: str
    transitions: list[StepTransition] = Field(default_factory=list)
    description: str | None = None
    say: str | None = None
    guards: list[GuardDef] = Field(default_factory=list)
    fact_requirements: list[FactRequirement] = Field(default_factory=list)
    tool_policy: list[ToolBinding] = Field(default_factory=list)
    action_config: ActionConfig | None = None
    response_policy: ResponsePolicy = Field(default_factory=ResponsePolicy)
    workload_class: StepWorkloadClass = "interactive"
    execution_isolation: StepExecutionIsolation = "subprocess"
    handoff: StepHandoff | None = None
    completion: StepCompletion | None = None
    # Per-step gate on classifier confidence. When set, outcome events
    # whose classifier confidence < threshold are suppressed and the
    # turn is treated as ``unknown`` (no transition fires; kernel falls
    # through to the step's ``otherwise`` edge if any).
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    # Per-step opt-in for fact injection into the classifier prompt SUFFIX
    # (WI-6.12). Names must reference declared fact_schema entries on the
    # owning AgentDocument; the prompt assembler reads this list and
    # appends the named facts to the *suffix* (never the cached prefix) so
    # prefix-cache hits survive.
    classifier_uses_facts: list[str] | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "Step":
        if self.completion is not None and self.handoff is not None:
            raise ValueError("step cannot declare both completion and handoff")
        if self.completion is not None and self.action_config is not None:
            raise ValueError("completion step cannot declare action_config")
        if self.handoff is not None and self.action_config is not None:
            raise ValueError("handoff step cannot declare action_config")
        self._validate_transitions()
        return self

    def _validate_transitions(self) -> None:
        """Enforce per-step routing invariants.

        - Transition ids must be unique within the step (the kernel keys
          routing decisions on them, so duplicates would silently mask).
        - At most one ``OtherwiseCondition`` transition (the terminal
          fallback; multiple would race).
        - ``OutcomeCondition.event`` strings must be unique within the
          step (the prefill classifier's ``guided_choice`` FSM expects a
          set, not a multiset; duplicates would shadow each other).
        - When the step has multiple tool bindings, every
          ``ToolOutcomeCondition`` must name its ``tool_ref`` so the
          kernel knows which pending tool's outcome the edge consumes.
        """
        seen_ids: set[str] = set()
        seen_outcome_events: set[str] = set()
        otherwise_count = 0
        tool_refs = {binding.ref for binding in self.tool_policy}

        for transition in self.transitions:
            if transition.id in seen_ids:
                raise ValueError(
                    f"step {self.id!r}: duplicate transition id {transition.id!r}"
                )
            seen_ids.add(transition.id)

            when = transition.when
            if isinstance(when, OutcomeCondition):
                if when.event in seen_outcome_events:
                    raise ValueError(
                        f"step {self.id!r}: duplicate OutcomeCondition.event "
                        f"{when.event!r} (each outcome event must map to exactly "
                        "one transition within a step)"
                    )
                seen_outcome_events.add(when.event)
            elif isinstance(when, OtherwiseCondition):
                otherwise_count += 1
                if otherwise_count > 1:
                    raise ValueError(
                        f"step {self.id!r}: at most one ``otherwise`` transition "
                        "is allowed per step"
                    )
            elif isinstance(when, ToolOutcomeCondition):
                if when.tool_ref is None and len(tool_refs) > 1:
                    raise ValueError(
                        f"step {self.id!r}: transition {transition.id!r} uses "
                        "ToolOutcomeCondition without ``tool_ref`` but the step "
                        f"has {len(tool_refs)} tool bindings — set ``tool_ref`` "
                        "explicitly so the routing target is unambiguous"
                    )
                if when.tool_ref is not None and when.tool_ref not in tool_refs:
                    raise ValueError(
                        f"step {self.id!r}: transition {transition.id!r} references "
                        f"tool {when.tool_ref!r} which is not in the step's "
                        "``tool_policy``"
                    )


class Scenario(BaseModel):
    id: str
    name: str
    start_step_id: str
    steps: list[Step] = Field(default_factory=list)
    summary: str | None = None
    order: int = 0
    entry_channels: list[str] = Field(default_factory=list)
    resources: dict[str, Any] = Field(default_factory=dict)
    # Persisted positions for the canvas flow view, keyed by step id.
    # Authored only when a user explicitly drags a card; otherwise the
    # frontend falls back to dagre auto-layout. Kernel ignores this.
    flow_layout: dict[str, dict[str, float]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_start_step(self) -> "Scenario":
        step_ids = {step.id for step in self.steps}
        if not step_ids:
            raise ValueError("scenario must contain at least one step")
        if self.start_step_id not in step_ids:
            raise ValueError("scenario start_step_id must reference a local step")
        return self


AnalysisVariableType = Literal["string", "number", "boolean", "category", "array"]
AnalysisVariableSource = Literal["transcript", "facts"]


class AnalysisVariableDef(BaseModel):
    """A variable extracted from the conversation for post-call reporting.

    Whereas ``FactDef`` captures values turn-by-turn to drive the conversation,
    ``AnalysisVariableDef`` declares variables that the analysis sweep fills at
    end-of-conversation (or on demand). Both flow through the same capture
    pipeline, so every analysis variable becomes a citation with the same
    grounding guarantees as a regular fact.
    """

    name: str
    type: AnalysisVariableType
    description: str
    categories: list[str] | None = None
    source: AnalysisVariableSource = "transcript"
    extract_when: str | None = None

    @model_validator(mode="after")
    def _validate_categories(self) -> "AnalysisVariableDef":
        if self.type == "category" and not self.categories:
            raise ValueError("category type requires non-empty categories")
        if self.type != "category" and self.categories:
            raise ValueError("categories may only be set when type='category'")
        return self


class AgentDocument(BaseModel):
    """Authored agent definition.

    ``metadata`` is a free-form bag, but two reserved keys exist by convention:

    * ``metadata["persona"]`` — :class:`ruhu.persona.BehavioralPersona` payload
      (formality, emoji policy, restricted-topic guidance). Versioned with the
      document, so it goes through draft → publish-review → publish. Read with
      :meth:`behavioral_persona`.
    """

    version: str = "3.0"
    start_scenario_id: str
    scenarios: list[Scenario] = Field(default_factory=list)
    scenario_routes: list[ScenarioRoute] = Field(default_factory=list)
    fact_schema: list[FactDef] = Field(default_factory=list)
    analysis_schema: list[AnalysisVariableDef] = Field(default_factory=list)
    agent_capability_manifest: AgentCapabilityManifest | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def behavioral_persona(self) -> BehavioralPersona | None:
        """Return the behavioural persona authored in ``metadata["persona"]``.

        Returns ``None`` when no persona is authored, or when the stored payload
        fails validation (defensive — callers must treat persona as optional
        anyway). Validation errors are silent here because the publish-review
        gate is the right place to surface them; the runtime should not crash
        on a bad persona blob.
        """
        raw = self.metadata.get("persona")
        if raw is None:
            return None
        if isinstance(raw, BehavioralPersona):
            return raw
        try:
            return BehavioralPersona.model_validate(raw)
        except Exception:
            return None

    @property
    def steps(self) -> list[Step]:
        return [step for scenario in self.scenarios for step in scenario.steps]

    @property
    def step_ids(self) -> set[str]:
        return {step.id for step in self.steps}

    @property
    def start_scenario(self) -> Scenario:
        return self.scenario_by_id(self.start_scenario_id)

    @property
    def start_step_id(self) -> str:
        return self.start_scenario.start_step_id

    @property
    def scenario_ids(self) -> set[str]:
        return {scenario.id for scenario in self.scenarios}

    def scenario_by_id(self, scenario_id: str) -> Scenario:
        for scenario in self.scenarios:
            if scenario.id == scenario_id:
                return scenario
        raise KeyError(scenario_id)

    def step_by_id(self, step_id: str) -> Step:
        for step in self.steps:
            if step.id == step_id:
                return step
        raise KeyError(step_id)

    def scenario_for_step_id(self, step_id: str) -> Scenario:
        for scenario in self.scenarios:
            if any(step.id == step_id for step in scenario.steps):
                return scenario
        raise KeyError(step_id)

    @model_validator(mode="after")
    def validate_document(self) -> "AgentDocument":
        scenario_ids = [scenario.id for scenario in self.scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("scenario ids must be unique")

        steps = self.steps
        if not steps:
            raise ValueError("agent document must contain at least one step")

        if self.start_scenario_id not in set(scenario_ids):
            raise ValueError("start_scenario_id must reference an existing scenario")

        step_ids = [step.id for step in steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step ids must be unique across the agent document")

        route_ids = [route.id for route in self.scenario_routes]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError("scenario route ids must be unique")

        for step in steps:
            for transition in step.transitions:
                if transition.to_step_id not in set(step_ids):
                    raise ValueError(
                        f"step {step.id} transition {transition.id} points to unknown step {transition.to_step_id}"
                    )
                # Cross-scenario step transitions are allowed: the kernel's
                # multi-hop loop updates current_scenario_id when it lands on
                # a step in a different scenario (see kernel._process_step_turn).

        for route in self.scenario_routes:
            if route.from_scenario_id not in set(scenario_ids):
                raise ValueError(
                    f"scenario route {route.id} points from unknown scenario {route.from_scenario_id}"
                )
            if route.to_scenario_id not in set(scenario_ids):
                raise ValueError(
                    f"scenario route {route.id} points to unknown scenario {route.to_scenario_id}"
                )
        return self


class AgentValidationIssue(BaseModel):
    severity: Literal["error", "warning"]
    code: str
    message: str
    scenario_id: str | None = None
    step_id: str | None = None
    transition_id: str | None = None
    route_id: str | None = None
    fact_name: str | None = None
    tool_ref: str | None = None


class AgentValidationReport(BaseModel):
    valid: bool
    error_count: int
    warning_count: int
    issues: list[AgentValidationIssue] = Field(default_factory=list)


class StepRuntimeEntry(BaseModel):
    current_step_id: str
    current_scenario_id: str
    pending_execution: bool = False
    active_repair: bool = False
    collects_missing_details: bool = False
    uses_tooling: bool = False
    hands_off: bool = False
    completes: bool = False
    missing_facts: list[str] = Field(default_factory=list)
    available_tool_refs: list[str] = Field(default_factory=list)
    transition_target_ids: list[str] = Field(default_factory=list)
    scripted_say: str | None = None
    workload_class: StepWorkloadClass = "interactive"
    execution_isolation: StepExecutionIsolation = "subprocess"


@dataclass(frozen=True)
class CompiledStep:
    step: Step
    scenario_id: str
    transition_target_ids: tuple[str, ...]
    available_tool_refs: tuple[str, ...]
    fact_requirement_names: tuple[str, ...]
    scripted_say: str | None
    collects_missing_details: bool
    uses_tooling: bool
    hands_off: bool
    completes: bool
    workload_class: StepWorkloadClass
    execution_isolation: StepExecutionIsolation

    def __getattr__(self, name: str) -> Any:
        return getattr(self.step, name)


@dataclass(frozen=True)
class CompiledScenario:
    scenario: Scenario
    steps: tuple[CompiledStep, ...]

    def __getattr__(self, name: str) -> Any:
        return getattr(self.scenario, name)


@dataclass(frozen=True)
class CompiledAgentDocument:
    document: AgentDocument
    scenarios: tuple[CompiledScenario, ...]
    steps: tuple[CompiledStep, ...]
    _scenario_by_id: dict[str, CompiledScenario]
    _step_by_id: dict[str, CompiledStep]
    _scenario_by_step_id: dict[str, CompiledScenario]

    @property
    def version(self) -> str:
        return self.document.version

    @property
    def start_scenario_id(self) -> str:
        return self.document.start_scenario_id

    @property
    def start_scenario(self) -> CompiledScenario:
        return self.scenario_by_id(self.start_scenario_id)

    @property
    def start_step_id(self) -> str:
        return self.start_scenario.start_step_id

    @property
    def fact_schema(self) -> list[FactDef]:
        return self.document.fact_schema

    @property
    def analysis_schema(self) -> list[AnalysisVariableDef]:
        return self.document.analysis_schema

    @property
    def agent_capability_manifest(self) -> AgentCapabilityManifest | None:
        return self.document.agent_capability_manifest

    @property
    def metadata(self) -> dict[str, Any]:
        return self.document.metadata

    @property
    def scenario_routes(self) -> list[ScenarioRoute]:
        return self.document.scenario_routes

    @property
    def scenario_ids(self) -> set[str]:
        return set(self._scenario_by_id)

    @property
    def step_ids(self) -> set[str]:
        return set(self._step_by_id)

    def scenario_by_id(self, scenario_id: str) -> CompiledScenario:
        try:
            return self._scenario_by_id[scenario_id]
        except KeyError as exc:
            raise KeyError(scenario_id) from exc

    def step_by_id(self, step_id: str) -> CompiledStep:
        try:
            return self._step_by_id[step_id]
        except KeyError as exc:
            raise KeyError(step_id) from exc

    def scenario_for_step_id(self, step_id: str) -> CompiledScenario:
        try:
            return self._scenario_by_step_id[step_id]
        except KeyError as exc:
            raise KeyError(step_id) from exc


def step_capability_flags(step_like: Step | CompiledStep) -> dict[str, bool]:
    if isinstance(step_like, CompiledStep):
        return {
            "collects_missing_details": step_like.collects_missing_details,
            "uses_tooling": step_like.uses_tooling,
            "hands_off": step_like.hands_off,
            "completes": step_like.completes,
        }
    return {
        "collects_missing_details": bool(step_like.fact_requirements),
        "uses_tooling": bool(
            step_like.action_config is not None
            or any(binding.mode != "blocked" for binding in step_like.tool_policy)
        ),
        "hands_off": step_like.handoff is not None,
        "completes": step_like.completion is not None,
    }


def compile_agent_document(document: AgentDocument | CompiledAgentDocument) -> CompiledAgentDocument:
    if isinstance(document, CompiledAgentDocument):
        return document

    compiled_scenarios: list[CompiledScenario] = []
    step_by_id: dict[str, CompiledStep] = {}
    scenario_by_id: dict[str, CompiledScenario] = {}
    scenario_by_step_id: dict[str, CompiledScenario] = {}

    for scenario in document.scenarios:
        compiled_steps: list[CompiledStep] = []
        for step in scenario.steps:
            compiled_step = CompiledStep(
                step=step,
                scenario_id=scenario.id,
                transition_target_ids=tuple(transition.to_step_id for transition in step.transitions),
                available_tool_refs=tuple(
                    binding.ref
                    for binding in step.tool_policy
                    if binding.mode != "blocked" and binding.ref
                ),
                fact_requirement_names=tuple(requirement.name for requirement in step.fact_requirements),
                scripted_say=step.say.strip() if step.say and step.say.strip() else None,
                collects_missing_details=bool(step.fact_requirements),
                uses_tooling=bool(
                    step.action_config is not None
                    or any(binding.mode != "blocked" for binding in step.tool_policy)
                ),
                hands_off=step.handoff is not None,
                completes=step.completion is not None,
                workload_class=step.workload_class,
                execution_isolation=step.execution_isolation,
            )
            compiled_steps.append(compiled_step)
            step_by_id[step.id] = compiled_step
        compiled_scenario = CompiledScenario(scenario=scenario, steps=tuple(compiled_steps))
        compiled_scenarios.append(compiled_scenario)
        scenario_by_id[scenario.id] = compiled_scenario
        for compiled_step in compiled_steps:
            scenario_by_step_id[compiled_step.id] = compiled_scenario

    return CompiledAgentDocument(
        document=document,
        scenarios=tuple(compiled_scenarios),
        steps=tuple(step_by_id.values()),
        _scenario_by_id=scenario_by_id,
        _step_by_id=step_by_id,
        _scenario_by_step_id=scenario_by_step_id,
    )


def validate_agent_document(document: AgentDocument) -> AgentValidationReport:
    issues: list[AgentValidationIssue] = []
    fact_names = {fact.name for fact in document.fact_schema}
    step_ids = document.step_ids
    transition_ids: dict[str, str] = {}
    reachable = _reachable_step_ids(document)
    seen_route_ids: set[str] = set()

    for scenario in document.scenarios:
        for step in scenario.steps:
            if step.id not in reachable:
                issues.append(
                    AgentValidationIssue(
                        severity="warning",
                        code="step.unreachable",
                        message="Step is unreachable from the start step.",
                        scenario_id=scenario.id,
                        step_id=step.id,
                    )
                )

            if (step.completion is not None or step.handoff is not None) and step.transitions:
                issues.append(
                    AgentValidationIssue(
                        severity="error",
                        code="step.terminal_with_transitions",
                        message="Completion or handoff steps must not declare outgoing transitions.",
                        scenario_id=scenario.id,
                        step_id=step.id,
                    )
                )
            if step.completion is None and step.handoff is None and not step.transitions:
                issues.append(
                    AgentValidationIssue(
                        severity="error",
                        code="step.non_terminal_without_transition",
                        message="Non-terminal steps must declare at least one transition, completion, or handoff before publish.",
                        scenario_id=scenario.id,
                        step_id=step.id,
                    )
                )

            if step.action_config is not None and any(req.name not in fact_names for req in step.fact_requirements):
                missing = next(req.name for req in step.fact_requirements if req.name not in fact_names)
                issues.append(
                    AgentValidationIssue(
                        severity="error",
                        code="step.fact_missing_definition",
                        message=f"Step requires undefined fact '{missing}'.",
                        scenario_id=scenario.id,
                        step_id=step.id,
                        fact_name=missing,
                    )
                )

            seen_tool_refs: set[str] = set()
            for binding in step.tool_policy:
                if binding.ref in seen_tool_refs:
                    issues.append(
                        AgentValidationIssue(
                            severity="warning",
                            code="step.duplicate_tool_binding",
                            message=f"Tool '{binding.ref}' is bound more than once in the same step.",
                            scenario_id=scenario.id,
                            step_id=step.id,
                            tool_ref=binding.ref,
                        )
                    )
                seen_tool_refs.add(binding.ref)

            for requirement in step.fact_requirements:
                if requirement.name not in fact_names:
                    issues.append(
                        AgentValidationIssue(
                            severity="error",
                            code="step.fact_missing_definition",
                            message=f"Step requires undefined fact '{requirement.name}'.",
                            scenario_id=scenario.id,
                            step_id=step.id,
                            fact_name=requirement.name,
                        )
                    )

            for transition in step.transitions:
                owner = transition_ids.get(transition.id)
                if owner is not None:
                    issues.append(
                        AgentValidationIssue(
                            severity="error",
                            code="transition.duplicate_id",
                            message=f"Transition id '{transition.id}' is duplicated across steps '{owner}' and '{step.id}'.",
                            scenario_id=scenario.id,
                            step_id=step.id,
                            transition_id=transition.id,
                        )
                    )
                transition_ids[transition.id] = step.id
                if transition.to_step_id not in step_ids:
                    issues.append(
                        AgentValidationIssue(
                            severity="error",
                            code="transition.unknown_target",
                            message=f"Transition points to unknown step '{transition.to_step_id}'.",
                            scenario_id=scenario.id,
                            step_id=step.id,
                            transition_id=transition.id,
                        )
                    )
                # Cross-scenario transitions are valid (see kernel handling).

    for fact_def in document.fact_schema:
        if fact_def.storage_policy.scope == "workflow":
            issues.append(
                AgentValidationIssue(
                    severity="error",
                    code="fact.workflow_storage_unavailable",
                    message=(
                        "FactDef storage_policy.scope='workflow' requires a workflow "
                        "state store, which is not enabled in this runtime."
                    ),
                    fact_name=fact_def.name,
                )
            )

    for route in sorted(document.scenario_routes, key=lambda item: (item.priority, item.id)):
        if route.id in seen_route_ids:
            issues.append(
                AgentValidationIssue(
                    severity="error",
                    code="scenario_route.duplicate_id",
                    message=f"Scenario route id '{route.id}' is duplicated.",
                    scenario_id=route.from_scenario_id,
                    route_id=route.id,
                )
            )
        seen_route_ids.add(route.id)
        if route.from_scenario_id not in document.scenario_ids:
            issues.append(
                AgentValidationIssue(
                    severity="error",
                    code="scenario_route.unknown_source",
                    message=f"Scenario route points from unknown scenario '{route.from_scenario_id}'.",
                    scenario_id=route.from_scenario_id,
                    route_id=route.id,
                )
            )
        if route.to_scenario_id not in document.scenario_ids:
            issues.append(
                AgentValidationIssue(
                    severity="error",
                    code="scenario_route.unknown_target",
                    message=f"Scenario route points to unknown scenario '{route.to_scenario_id}'.",
                    scenario_id=route.from_scenario_id,
                    route_id=route.id,
                )
            )

    channel_owners: dict[str, str] = {}
    for scenario in document.scenarios:
        for channel in scenario.entry_channels:
            owner = channel_owners.get(channel)
            if owner is not None and owner != scenario.id:
                issues.append(
                    AgentValidationIssue(
                        severity="error",
                        code="scenario.entry_channel_conflict",
                        message=f"Channel '{channel}' is assigned to more than one entry scenario.",
                        scenario_id=scenario.id,
                    )
                )
            channel_owners[channel] = scenario.id

    if not any(step.completion is not None or step.handoff is not None for step in document.steps):
        issues.append(
            AgentValidationIssue(
                severity="warning",
                code="document.exit_path_missing",
                message="Agent document has no completion or handoff step.",
            )
        )

    error_count = sum(1 for issue in issues if issue.severity == "error")
    warning_count = sum(1 for issue in issues if issue.severity == "warning")
    return AgentValidationReport(
        valid=error_count == 0,
        error_count=error_count,
        warning_count=warning_count,
        issues=issues,
    )


def build_step_runtime_entry(
    document: AgentDocument | CompiledAgentDocument,
    *,
    current_step_id: str,
    facts: dict[str, Any] | None = None,
    pending_action: bool = False,
    pending_permission: bool = False,
    active_repair: bool = False,
) -> StepRuntimeEntry:
    compiled_document = compile_agent_document(document)
    facts = facts or {}
    step = compiled_document.step_by_id(current_step_id)
    scenario = compiled_document.scenario_for_step_id(current_step_id)
    missing_facts = [fact_name for fact_name in step.fact_requirement_names if fact_name not in facts]

    return StepRuntimeEntry(
        current_step_id=step.id,
        current_scenario_id=scenario.id,
        pending_execution=bool(pending_permission or pending_action),
        active_repair=active_repair,
        collects_missing_details=bool(missing_facts),
        uses_tooling=step.uses_tooling,
        hands_off=step.hands_off,
        completes=step.completes,
        missing_facts=missing_facts,
        available_tool_refs=list(step.available_tool_refs),
        transition_target_ids=list(step.transition_target_ids),
        scripted_say=step.scripted_say,
        workload_class=step.workload_class,
        execution_isolation=step.execution_isolation,
    )


def _reachable_step_ids(document: AgentDocument) -> set[str]:
    visited: set[str] = set()
    queue = [document.start_step_id]
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        step = document.step_by_id(current)
        for transition in step.transitions:
            if transition.to_step_id not in visited:
                queue.append(transition.to_step_id)
        current_scenario_id = document.scenario_for_step_id(current).id
        for route in document.scenario_routes:
            if route.from_scenario_id == current_scenario_id:
                target_step_id = document.scenario_by_id(route.to_scenario_id).start_step_id
                if target_step_id not in visited:
                    queue.append(target_step_id)
    return visited


def select_start_scenario_id(
    document: AgentDocument | CompiledAgentDocument,
    *,
    requested_scenario_id: str | None = None,
    channel: str | None = None,
) -> str:
    if requested_scenario_id is not None:
        scenario = document.scenario_by_id(requested_scenario_id)
        if channel and scenario.entry_channels and channel not in scenario.entry_channels:
            raise ValueError(
                f"scenario '{requested_scenario_id}' does not allow channel '{channel}'"
            )
        return requested_scenario_id
    if channel:
        for scenario in sorted(document.scenarios, key=lambda item: (item.order, item.id)):
            if channel in scenario.entry_channels:
                return scenario.id
    return document.start_scenario_id
