import type { Edge, Node } from 'reactflow'
import { MarkerType } from 'reactflow'
import type {
  AgentDefinition,
  AgentDefinitionStep,
  Condition,
  FactDef,
  ResponsePolicy,
  ToolBinding,
  Transition,
} from '@/types/agent-definition'

/**
 * Derived step kind — classifies a canvas state by the fields present on it.
 *
 * Per docs/generic-state-redesign/01-generic-step-canvas-adr.md, authors no
 * longer pick a step type. The runtime (and canvas tooling) derives behavior
 * from optional capabilities (handoff, completion, action_config, fact
 * requirements). This union is an internal classifier used for canvas-side
 * styling and validation only — it is NOT an authored field.
 */
export type DerivedStepKind = 'entry' | 'conversation' | 'capture' | 'action' | 'handoff' | 'terminal'

export function deriveStepKind(step: AgentDefinitionStep, startStepId?: string): DerivedStepKind {
  const action_config = (step as { action_config?: unknown }).action_config
  const handoff = (step as { handoff?: unknown }).handoff
  if (handoff) return 'handoff'
  if (step.terminal_disposition) return 'terminal'
  if (action_config) return 'action'
  if (step.fact_requirements && step.fact_requirements.length > 0) return 'capture'
  if (startStepId !== undefined && step.id === startStepId) return 'entry'
  return 'conversation'
}

export const DEFAULT_RESPONSE_POLICY: ResponsePolicy = {
  answer_directly_first: true,
  ask_clarifying_question_only_if_needed: true,
  voice_style: 'concise',
  direct_answer_prompt: null,
}

function normalizeTransition(transition: Transition): Transition {
  return {
    ...transition,
    when: transition.when ?? createEmptyCondition(),
    natural_reason: transition.natural_reason ?? null,
    when_to_use: transition.when_to_use ?? null,
  }
}

function normalizeState(state: AgentDefinitionStep): AgentDefinitionStep {
  return {
    ...state,
    accepted_inputs: state.accepted_inputs ?? [],
    event_hints: state.event_hints ?? {},
    fact_requirements: state.fact_requirements ?? [],
    tool_policy: state.tool_policy ?? [],
    response_policy: { ...DEFAULT_RESPONSE_POLICY, ...(state.response_policy ?? {}) },
    guards: state.guards ?? [],
    transitions: (state.transitions ?? []).map(normalizeTransition),
    entry_response: state.entry_response ?? null,
    say_on_entry: state.say_on_entry ?? null,
    say_on_transition: state.say_on_transition ?? null,
    ask_for_fact: state.ask_for_fact ?? null,
    repair_response: state.repair_response ?? null,
    terminal_disposition: state.terminal_disposition ?? null,
  }
}

function normalizeAgentDefinition(definition: AgentDefinition): AgentDefinition {
  return {
    ...definition,
    steps: (definition.steps ?? []).map((step) => normalizeState(step)),
    fact_schema: definition.fact_schema ?? [],
    followup_handlers: definition.followup_handlers ?? [],
  }
}

export function createEmptyCondition(): Condition {
  return { kind: 'otherwise', value: null }
}

export function createBlankTransition(targetStateId: string): Transition {
  return {
    id: crypto.randomUUID(),
    when: createEmptyCondition(),
    to: targetStateId,
    natural_reason: null,
    when_to_use: null,
    priority: 100,
  }
}

export function createBlankToolBinding(stateKind?: DerivedStepKind): ToolBinding {
  // Action steps execute a single explicit operation — default to required + always.
  // All other step kinds allow optional supplementary tools — default to allowed + on_missing_context.
  if (stateKind === 'action') {
    return {
      ref: '',
      mode: 'required',
      invocation_strategy: 'always',
      timeout_ms: null,
      event_name: null,
      args: {},
    }
  }
  return {
    ref: '',
    mode: 'allowed',
    invocation_strategy: 'on_missing_context',
    timeout_ms: null,
    event_name: null,
    args: {},
  }
}

export function createBlankFact(): FactDef {
  return {
    name: '',
    type: 'string',
    required: false,
    source_policy: 'deterministic_first',
    confidence_threshold: 0.8,
    conflict_policy: 'prefer_deterministic',
  }
}

/**
 * Creates a blank step. Authors no longer pick a typed state — the kind is
 * derived from the optional capability fields you set afterward
 * (terminal_disposition for 'terminal', fact_requirements for 'capture',
 * action_config for 'action', handoff for 'handoff').
 */
export function createBlankState(): AgentDefinitionStep {
  const id = crypto.randomUUID()
  return {
    id,
    name: 'New Step',
    accepted_inputs: [],
    event_hints: {},
    fact_requirements: [],
    tool_policy: [],
    response_policy: { ...DEFAULT_RESPONSE_POLICY },
    guards: [],
    transitions: [],
    entry_response: null,
    say_on_entry: null,
    say_on_transition: null,
    ask_for_fact: null,
    repair_response: null,
    terminal_disposition: null,
  }
}

export function createBlankAgentDefinition(name: string): AgentDefinition {
  const entry = createBlankState()
  entry.name = 'Start'
  // entry has no special capability fields — its 'entry' kind is derived from
  // matching definition.start_step_id

  const terminal = createBlankState()
  terminal.name = 'Complete'
  terminal.terminal_disposition = 'resolved'

  entry.transitions = [createBlankTransition(terminal.id)]

  return {
    id: `${slugify(name || 'untitled-agent')}_${crypto.randomUUID().slice(0, 8)}`,
    name: name || 'Untitled Agent',
    version: '1.0.0',
    start_step_id: entry.id,
    steps: [entry, terminal],
    fact_schema: [],
  }
}

export function agentStateCardTone(kind: DerivedStepKind): string {
  switch (kind) {
    case 'entry':
      return 'border-blue-500/30 bg-blue-500/10'
    case 'conversation':
      return 'border-slate-500/30 bg-slate-500/10'
    case 'capture':
      return 'border-amber-500/30 bg-amber-500/10'
    case 'action':
      return 'border-emerald-500/30 bg-emerald-500/10'
    case 'handoff':
      return 'border-rose-500/30 bg-rose-500/10'
    case 'terminal':
      return 'border-violet-500/30 bg-violet-500/10'
  }
}

export function conditionLabel(condition: Condition): string {
  if (condition.kind === 'otherwise') return 'otherwise'
  if (!condition.value) return condition.kind
  return `${condition.kind}:${condition.value}`
}

// ─── Shared validation ────────────────────────────────────────────────────────
// Used by both the StepInspector (inline warnings) and projectAgentDefinitionToFlow
// (card badge). Keep in sync — one source of truth.

export function getStateWarnings(
  state: AgentDefinitionStep,
  definition?: AgentDefinition,
): string[] {
  const warnings: string[] = []
  const actionConfig = (state as { action_config?: { code?: string } }).action_config
  const kind = deriveStepKind(state, definition?.start_step_id)
  if (kind === 'action' && !(actionConfig?.code || '').trim()) {
    warnings.push('Action steps must define Action Code before they can run.')
  }
  if (kind === 'capture' && state.fact_requirements.length === 0) {
    warnings.push('Capture steps must declare the facts they collect.')
  }
  if (!state.say_on_entry?.trim()) {
    warnings.push('Add say_on_entry so this step has an authored opening/entry utterance.')
  }
  if (!state.say_on_transition?.trim() && kind !== 'entry') {
    warnings.push('Add say_on_transition so transitions into this step feel intentional.')
  }
  if (kind === 'capture' && !state.ask_for_fact?.trim()) {
    warnings.push('Add ask_for_fact so capture requests sound natural and justified.')
  }
  if (!state.repair_response?.trim()) {
    warnings.push('Add repair_response so this step can recover gracefully.')
  }
  if (kind === 'entry' && state.tool_policy.length > 0) {
    warnings.push('Entry steps should not run tools. Move tools to a working step.')
  }
  const otherwiseCount = state.transitions.filter((t) => t.when.kind === 'otherwise').length
  if (otherwiseCount > 1) {
    warnings.push('Only one fallback ("otherwise") transition is allowed. Remove the extra.')
  }
  if (kind !== 'terminal' && state.transitions.length === 0) {
    warnings.push('This step has no transitions — the conversation will stall here.')
  }
  // ── Spec 25 §Validation Rules — human-like interaction warnings ──────────
  if (kind === 'action' && !state.activity_label?.trim()) {
    warnings.push(
      'Action has no activity label. The user will hear silence when work starts — add a short label like "Checking the calendar".',
    )
  }
  const hasPacingOverride =
    state.slow_threshold_ms != null ||
    state.soft_timeout_ms != null ||
    state.endpointing_ms != null ||
    state.turn_eagerness != null ||
    state.interruptibility_policy != null
  const likelySlowAction =
    kind === 'action' &&
    (((actionConfig?.code || '').trim().length > 0) || state.activity_label != null)
  if (likelySlowAction && !hasPacingOverride && !state.publish_status_trail) {
    // Not an error — authors may legitimately accept channel defaults.
    // Warn only if the action likely does externally-blocking work and
    // no pacing signal is configured at all.
  }
  if (state.endpointing_ms != null && state.endpointing_ms < 400) {
    warnings.push(
      'endpointing_ms is below 400ms. Voice sessions may cut the user off before they finish speaking.',
    )
  }
  if (state.endpointing_ms != null && state.endpointing_ms > 1500) {
    warnings.push(
      'endpointing_ms is above 1500ms. Voice sessions may feel laggy before the agent takes the floor.',
    )
  }
  if (state.soft_timeout_ms != null && state.soft_timeout_ms < 400) {
    warnings.push(
      'soft_timeout_ms is below 400ms. The agent may fall back before the narration/runtime has a realistic chance to respond.',
    )
  }
  if (
    state.soft_timeout_ms != null &&
    state.slow_threshold_ms != null &&
    state.soft_timeout_ms > state.slow_threshold_ms
  ) {
    warnings.push(
      'soft_timeout_ms is greater than slow_threshold_ms. The system would wait longer to fallback than to declare the work slow.',
    )
  }
  if (
    state.turn_eagerness === 'high' &&
    state.interruptibility_policy === 'non_interruptible'
  ) {
    warnings.push(
      'High turn eagerness combined with non_interruptible speech is likely to feel aggressive in voice conversations.',
    )
  }
  if (state.turn_eagerness === 'low' && state.endpointing_ms != null && state.endpointing_ms < 500) {
    warnings.push(
      'Low turn eagerness combined with endpointing_ms below 500ms sends mixed signals about how quickly voice should claim the floor.',
    )
  }
  const hasPolicyOrEscalateBranch = state.transitions.some(
    (t) => t.branch_intent === 'block' || t.branch_intent === 'escalate',
  )
  if (hasPolicyOrEscalateBranch && kind === 'conversation' && !state.expects_policy_blocks) {
    warnings.push(
      'This step has a block/escalate branch but is not marked as expecting policy blocks. Toggle "Expects policy-blocked branches" in the inspector.',
    )
  }
  const hasConfirmBranch = state.transitions.some((t) => t.branch_intent === 'confirm')
  if (hasConfirmBranch && kind === 'action') {
    if (!state.say_on_transition?.trim()) {
      warnings.push(
        'This action has a confirmation branch but no say_on_transition guidance. Add guidance so the runtime can explain what the user is confirming.',
      )
    }
  }
  const blockTransitionsWithoutIntent = state.transitions.filter(
    (t) => t.when.kind === 'guard_failure' && !t.branch_intent,
  )
  if (blockTransitionsWithoutIntent.length > 0) {
    warnings.push(
      'Guard-failure transitions have no branch intent set. Mark them as block, escalate, or repair so the UI and renderer know what kind of branch they are.',
    )
  }
  if (definition) {
    const stateById = new Map(definition.steps.map((item) => [item.id, item]))
    const missingNarrativeTransitions = state.transitions.filter((transition) => {
      const target = stateById.get(transition.to)
      if (!target) return false
      const targetKind = deriveStepKind(target, definition.start_step_id)
      if (!['capture', 'action', 'handoff'].includes(targetKind)) return false
      return !transition.natural_reason?.trim() || !transition.when_to_use?.trim()
    })
    if (missingNarrativeTransitions.length > 0) {
      warnings.push(
        'High-value transitions into capture, action, or handoff steps should define both a natural reason and a when-to-use description.',
      )
    }
  }
  // ── Artifact warnings (need definition context for cross-state checks) ───
  if (definition && kind === 'action' && state.artifact_type?.trim()) {
    const handlers = definition.followup_handlers ?? []
    const hasHandler = handlers.some((h) => h.artifact_type === state.artifact_type)
    if (!hasHandler) {
      warnings.push(
        `This action produces artifact type "${state.artifact_type}" but no follow-up handler targets it — later intents like "cancel it" will have nothing to route to.`,
      )
    }
  }
  return warnings
}

/**
 * Agent-level warnings that don't fit on a single state.  Used by validation
 * surfaces that display cross-cutting issues (handler orphans, broken
 * target_step_id references, etc.).
 */
export function getAgentDefinitionWarnings(definition: AgentDefinition): string[] {
  definition = normalizeAgentDefinition(definition)
  const warnings: string[] = []
  const stateIds = new Set(definition.steps.map((s) => s.id))
  const producedArtifactTypes = new Set(
    definition.steps
      .filter((s) => deriveStepKind(s, definition.start_step_id) === 'action' && s.artifact_type?.trim())
      .map((s) => s.artifact_type as string),
  )
  const factNames = new Set(definition.fact_schema.map((f) => f.name))
  for (const handler of definition.followup_handlers ?? []) {
    const label =
      handler.artifact_type && handler.followup_intent
        ? `${handler.artifact_type}/${handler.followup_intent}`
        : 'handler'
    if (!handler.artifact_type?.trim()) {
      warnings.push(`A follow-up handler is missing its artifact type.`)
      continue
    }
    if (!handler.followup_intent?.trim()) {
      warnings.push(`Handler "${label}" is missing a follow-up intent.`)
    }
    if (!handler.target_step_id?.trim()) {
      warnings.push(`Handler "${label}" has no target state.`)
    } else if (!stateIds.has(handler.target_step_id)) {
      warnings.push(
        `Handler "${label}" targets state "${handler.target_step_id}" which does not exist.`,
      )
    }
    if (!producedArtifactTypes.has(handler.artifact_type)) {
      warnings.push(
        `Handler "${label}" references artifact type "${handler.artifact_type}" which no action state produces — the handler can never fire.`,
      )
    }
    for (const requirement of handler.fact_requirements ?? []) {
      if (!factNames.has(requirement.name)) {
        warnings.push(
          `Handler "${label}" requires fact "${requirement.name}" which is not declared in the agent's fact schema.`,
        )
      }
    }
  }
  return warnings
}

export function projectAgentDefinitionToFlow(definition: AgentDefinition): { nodes: Node[]; edges: Edge[] } {
  definition = normalizeAgentDefinition(definition)
  // ── BFS layer assignment ───────────────────────────────────────────────────
  // Each state is placed in the leftmost layer reachable from the entry state.
  // States within the same layer keep the original definition.steps order
  // so the layout is deterministic and stable across re-renders.
  const stateById = new Map(definition.steps.map((s) => [s.id, s]))
  const layerOf = new Map<string, number>()

  if (stateById.has(definition.start_step_id)) {
    layerOf.set(definition.start_step_id, 0)
    const queue: string[] = [definition.start_step_id]
    let qi = 0
    while (qi < queue.length) {
      const id = queue[qi++]
      const state = stateById.get(id)!
      const layer = layerOf.get(id)!
      for (const t of state.transitions) {
        if (stateById.has(t.to) && !layerOf.has(t.to)) {
          layerOf.set(t.to, layer + 1)
          queue.push(t.to)
        }
      }
    }
  }

  // Build the layer columns. Iterate definition.steps in definition order so that
  // within each layer, states appear top-to-bottom in their original sequence.
  const maxLayer = layerOf.size > 0 ? Math.max(...layerOf.values()) : -1
  const layers: string[][] = Array.from({ length: Math.max(maxLayer + 1, 0) }, () => [])
  for (const state of definition.steps) {
    const layer = layerOf.get(state.id)
    if (layer !== undefined) layers[layer].push(state.id)
  }

  // Unreachable states go in a final "disconnected" column so they are always
  // visible but clearly separated from the main flow.
  const unreachable = definition.steps.filter((s) => !layerOf.has(s.id))
  if (unreachable.length > 0) {
    layers.push(unreachable.map((s) => s.id))
  }

  // ── Node positions ─────────────────────────────────────────────────────────
  const X_LAYER = 360   // horizontal distance between columns
  const Y_STEP  = 210   // vertical distance between nodes in the same column
  const X_OFFSET = 80
  const Y_OFFSET = 80

  const positionOf = new Map<string, { x: number; y: number }>()
  layers.forEach((ids, layerIdx) => {
    ids.forEach((id, rowIdx) => {
      positionOf.set(id, {
        x: X_OFFSET + layerIdx * X_LAYER,
        y: Y_OFFSET + rowIdx * Y_STEP,
      })
    })
  })

  // ── Nodes ──────────────────────────────────────────────────────────────────
  const nodes = definition.steps.map((state) => ({
    id: state.id,
    type: 'stateCard',
    position: positionOf.get(state.id) ?? { x: X_OFFSET, y: Y_OFFSET },
    data: {
      state,
      isEntryState: state.id === definition.start_step_id,
      hasWarnings: getStateWarnings(state, definition).length > 0,
    },
    draggable: false,
  } satisfies Node))

  // ── Edges ──────────────────────────────────────────────────────────────────
  const transitionEdges = definition.steps.flatMap((state) =>
    state.transitions.map((transition) => ({
      id: transition.id,
      source: state.id,
      target: transition.to,
      label: conditionLabel(transition.when),
      type: 'smoothstep',
      markerEnd: { type: MarkerType.ArrowClosed, color: '#94a3b8' },
      style: { stroke: '#94a3b8' },
      data: { transition },
    } satisfies Edge)),
  )

  // Follow-up handler edges: artifact-producing action state → handler target.
  // Rendered dashed and in a distinct colour so they are visually separable
  // from deterministic transitions.  These do not drive runtime control flow
  // directly; the runtime resolves them on a follow-up intent turn.
  const handlerEdges: Edge[] = []
  for (const handler of definition.followup_handlers ?? []) {
    if (!handler.target_step_id) continue
    if (!stateById.has(handler.target_step_id)) continue
    const producers = definition.steps.filter(
      (s) => deriveStepKind(s, definition.start_step_id) === 'action' && s.artifact_type === handler.artifact_type,
    )
    for (const producer of producers) {
      const edgeId = `followup:${producer.id}:${handler.artifact_type}:${handler.followup_intent}:${handler.target_step_id}`
      handlerEdges.push({
        id: edgeId,
        source: producer.id,
        target: handler.target_step_id,
        label: `${handler.artifact_type} · ${handler.followup_intent}`,
        type: 'smoothstep',
        markerEnd: { type: MarkerType.ArrowClosed, color: '#d946ef' },
        style: { stroke: '#d946ef', strokeDasharray: '5 4' },
        labelStyle: { fill: '#d946ef', fontSize: 10 },
        labelBgStyle: { fill: 'rgba(217, 70, 239, 0.08)' },
        data: { handler, kind: 'followup' },
      } satisfies Edge)
    }
  }

  return { nodes, edges: [...transitionEdges, ...handlerEdges] }
}

// ─── Action-state helpers ─────────────────────────────────────────────────────

export type OutcomeSuffix = 'success' | 'error' | 'timeout'

export interface ToolSection {
  bindingIndex: number
  binding: ToolBinding
  outcomes: Record<OutcomeSuffix, Transition | null>
}

export interface ActionViewData {
  toolSections: ToolSection[]
  otherExits: Transition[]
}

/** Derives the event name base for a tool binding. */
export function toolEventBase(binding: ToolBinding): string {
  return binding.event_name ?? binding.ref.replace(/\./g, '_')
}

/** Creates a pre-wired tool_outcome transition for a given suffix. */
export function createToolOutcomeTransition(
  base: string,
  suffix: OutcomeSuffix,
  targetStateId: string,
): Transition {
  return {
    id: crypto.randomUUID(),
    when: { kind: 'tool_outcome', value: `${base}_${suffix}` },
    to: targetStateId,
    priority: 100,
  }
}

/**
 * Derives the task-shaped view for an action state: maps each tool binding to
 * its three conventional outcome transitions, and collects all other exits that
 * are not owned by any tool binding.
 */
export function deriveActionView(state: AgentDefinitionStep): ActionViewData {
  const toolSections: ToolSection[] = state.tool_policy.map((binding, bindingIndex) => {
    const base = toolEventBase(binding)
    return {
      bindingIndex,
      binding,
      outcomes: {
        success:
          state.transitions.find(
            (t) => t.when.kind === 'tool_outcome' && t.when.value === `${base}_success`,
          ) ?? null,
        error:
          state.transitions.find(
            (t) => t.when.kind === 'tool_outcome' && t.when.value === `${base}_error`,
          ) ?? null,
        timeout:
          state.transitions.find(
            (t) => t.when.kind === 'tool_outcome' && t.when.value === `${base}_timeout`,
          ) ?? null,
      },
    }
  })

  const ownedIds = new Set(
    toolSections.flatMap((s) =>
      (Object.values(s.outcomes) as (Transition | null)[])
        .filter((t): t is Transition => t !== null)
        .map((t) => t.id),
    ),
  )
  const otherExits = state.transitions.filter((t) => !ownedIds.has(t.id))

  return { toolSections, otherExits }
}

/**
 * Renames convention-derived outcome transitions when a tool binding's ref
 * changes. Only renames if the binding has no custom event_name override, and
 * only renames exact ${oldBase}_success/error/timeout matches.
 */
export function syncToolOutcomeTransitions(
  state: AgentDefinitionStep,
  bindingIndex: number,
  newRef: string,
): AgentDefinitionStep {
  const binding = state.tool_policy[bindingIndex]
  if (!binding || binding.event_name != null) return state
  const oldBase = binding.ref.replace(/\./g, '_')
  const newBase = newRef.replace(/\./g, '_')
  if (oldBase === newBase) return state
  const SUFFIXES: OutcomeSuffix[] = ['success', 'error', 'timeout']
  const updatedTransitions = state.transitions.map((t) => {
    if (t.when.kind !== 'tool_outcome') return t
    for (const suffix of SUFFIXES) {
      if (t.when.value === `${oldBase}_${suffix}`) {
        return { ...t, when: { kind: 'tool_outcome' as const, value: `${newBase}_${suffix}` } }
      }
    }
    return t
  })
  return { ...state, transitions: updatedTransitions }
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
}
