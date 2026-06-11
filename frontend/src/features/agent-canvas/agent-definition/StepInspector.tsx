import { useState } from 'react'
import { Button } from '@/components/atoms/button'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
import { Switch } from '@/components/atoms/switch'
import { Textarea } from '@/components/atoms/textarea'
import type {
  ArtifactFollowupHandler,
  ConditionKind,
  FactDef,
  FactRequirement,
  GuardDef,
  InterruptibilityPolicy,
  AgentDefinition,
  AgentDefinitionStep,
  ToolBinding,
  Transition,
  TransitionBranchIntent,
  TurnEagerness,
} from '@/types/agent-definition'
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Info,
  Plus,
  Trash2,
  X,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  conditionLabel,
  createBlankToolBinding,
  createBlankTransition,
  DEFAULT_RESPONSE_POLICY,
  deriveStepKind,
  getStateWarnings,
  type DerivedStepKind,
} from './utils'
import { ToolRefCombobox } from './ToolRefCombobox'
import { ActionConfigEditor, DEFAULT_ACTION_CONFIG } from './ActionConfigEditor'

// ─── Label maps ──────────────────────────────────────────────────────────────

const STEP_KIND_DESCRIPTIONS: Record<DerivedStepKind, string> = {
  entry: 'Opening — routes the first turn into the correct working step',
  conversation: 'Interactive — answers, qualifies, triages, handles objections',
  capture: 'Collecting — gathers a small set of required facts from the user',
  action: 'Executing — runs an explicit tool operation with typed outcomes',
  handoff: 'Transferring — passes control to a human agent or external system',
  terminal: 'Ending — closes the conversation with a typed disposition',
}

const CONDITION_KIND_LABELS: Record<ConditionKind, string> = {
  otherwise: 'Always (fallback)',
  event: 'Intent detected',
  fact_present: 'Fact is present',
  fact_missing: 'Fact is missing',
  guard_failure: 'Guard failed',
  tool_outcome: 'Tool outcome',
}

const CONDITION_VALUE_PLACEHOLDERS: Partial<Record<ConditionKind, string>> = {
  event: 'e.g. booking_intent',
  fact_present: 'e.g. email',
  fact_missing: 'e.g. email',
  guard_failure: 'e.g. channel_allowed',
  tool_outcome: 'e.g. crm_lookup_success',
}

const TOOL_MODE_LABELS: Record<ToolBinding['mode'], string> = {
  required: 'Required — must run',
  allowed: 'Allowed — may run',
  optional: 'Optional — agent decides',
  blocked: 'Blocked — never run',
}

const TOOL_STRATEGY_LABELS: Record<ToolBinding['invocation_strategy'], string> = {
  always: 'Always',
  never: 'Never',
  on_missing_context: 'When context missing',
  on_low_confidence: 'When uncertain',
  latency_bounded: 'Fast-path only',
}

const GUARD_KIND_LABELS: Record<GuardDef['kind'], string> = {
  channel_allowed: 'Channel restriction',
  fact_required: 'Fact required',
}

const GUARD_VALUE_PLACEHOLDERS: Record<GuardDef['kind'], string> = {
  channel_allowed: 'e.g. voice, web_widget',
  fact_required: 'fact name, e.g. email',
}

const CONDITION_KINDS: ConditionKind[] = [
  'otherwise',
  'event',
  'fact_present',
  'fact_missing',
  'guard_failure',
  'tool_outcome',
]

// ─── Response mode ────────────────────────────────────────────────────────────
// Collapses the two response_policy booleans into a single author-facing concept.

type ResponseMode = 'direct' | 'balanced' | 'clarify'

const RESPONSE_MODE_POLICY: Record<
  ResponseMode,
  Pick<AgentDefinitionStep['response_policy'], 'answer_directly_first' | 'ask_clarifying_question_only_if_needed'>
> = {
  direct:   { answer_directly_first: true,  ask_clarifying_question_only_if_needed: true },
  balanced: { answer_directly_first: false, ask_clarifying_question_only_if_needed: true },
  clarify:  { answer_directly_first: false, ask_clarifying_question_only_if_needed: false },
}

function deriveResponseMode(policy: AgentDefinitionStep['response_policy']): ResponseMode {
  if (policy.answer_directly_first) return 'direct'
  if (policy.ask_clarifying_question_only_if_needed) return 'balanced'
  return 'clarify'
}

function createFactRequirement(name: string): FactRequirement {
  return {
    name,
    purpose: null,
  }
}

function factRequirementNames(state: AgentDefinitionStep): string[] {
  return (state.fact_requirements ?? []).map((item) => item.name).filter(Boolean)
}

function normalizeStateForInspector(state: AgentDefinitionStep): AgentDefinitionStep {
  return {
    ...state,
    accepted_inputs: state.accepted_inputs ?? [],
    event_hints: state.event_hints ?? {},
    fact_requirements: state.fact_requirements ?? [],
    tool_policy: state.tool_policy ?? [],
    response_policy: { ...DEFAULT_RESPONSE_POLICY, ...(state.response_policy ?? {}) },
    guards: state.guards ?? [],
    transitions: state.transitions ?? [],
    entry_response: state.entry_response ?? null,
    say_on_entry: state.say_on_entry ?? null,
    say_on_transition: state.say_on_transition ?? null,
    ask_for_fact: state.ask_for_fact ?? null,
    repair_response: state.repair_response ?? null,
    terminal_disposition: state.terminal_disposition ?? null,
  }
}

// ─── Preview generator ────────────────────────────────────────────────────────

function buildPreview(
  state: AgentDefinitionStep,
  availableStates: { id: string; name: string }[],
  kind: DerivedStepKind,
): string {
  const resolveName = (id: string) =>
    availableStates.find((s) => s.id === id)?.name ?? id

  const parts: string[] = []
  const kindLabel = kind.charAt(0).toUpperCase() + kind.slice(1)

  parts.push(
    `${kindLabel} step: ${state.name}`,
  )

  const facts = factRequirementNames(state)
  if (facts.length > 0) parts.push(`Requires: ${facts.join(', ')}.`)

  const actionToolAffordances = ((state as { tool_affordances?: string[] }).tool_affordances) ?? []
  if (kind === 'action' && actionToolAffordances.length > 0) {
    parts.push(`Runs: ${actionToolAffordances.join(', ')}.`)
  } else {
    const requiredTools = state.tool_policy.filter((b) => b.mode === 'required' && b.ref)
    const allowedTools = state.tool_policy.filter(
      (b) => (b.mode === 'allowed' || b.mode === 'optional') && b.ref,
    )
    if (requiredTools.length > 0) parts.push(`Runs: ${requiredTools.map((b) => b.ref).join(', ')}.`)
    if (allowedTools.length > 0) parts.push(`May use: ${allowedTools.map((b) => b.ref).join(', ')}.`)
  }

  const keyedTransitions = state.transitions.filter((t) => t.when.kind !== 'otherwise')
  const fallthrough = state.transitions.find((t) => t.when.kind === 'otherwise')
  if (keyedTransitions.length > 0) {
    const descriptions = keyedTransitions
      .slice(0, 3)
      .map((t) => `${resolveName(t.to)} on ${conditionLabel(t.when)}`)
    const more = keyedTransitions.length > 3 ? ` +${keyedTransitions.length - 3} more` : ''
    parts.push(`Transitions: ${descriptions.join('; ')}${more}.`)
  }
  if (fallthrough) parts.push(`Falls through to "${resolveName(fallthrough.to)}".`)
  if (state.transitions.length === 0 && kind !== 'terminal') parts.push('No transitions configured.')

  if (kind === 'terminal') {
    parts.push(`Closes conversation as: ${state.terminal_disposition || 'resolved'}.`)
  }
  if (kind === 'handoff') {
    parts.push('Transfers control to a human agent or external system.')
  }

  return parts.join(' ')
}

// ─── Props ────────────────────────────────────────────────────────────────────

interface StepInspectorProps {
  state: AgentDefinitionStep
  availableStates: { id: string; name: string }[]
  factSchema: FactDef[]
  onChange: (nextState: AgentDefinitionStep) => void
  onDelete: () => void
  canDelete: boolean
  agentId: string | null
  /** Agent definition context for cross-state validation (e.g. artifact-handler wiring). */
  agentDefinition?: Pick<AgentDefinition, 'steps' | 'fact_schema' | 'followup_handlers' | 'start_step_id'>
}

// ─── Component ────────────────────────────────────────────────────────────────

export function StepInspector({
  state: rawState,
  availableStates,
  factSchema,
  onChange,
  onDelete,
  canDelete,
  agentDefinition,
  agentId,
}: StepInspectorProps) {
  const state = normalizeStateForInspector(rawState)
  const kind = deriveStepKind(state, agentDefinition?.start_step_id)
  const [expandedTools, setExpandedTools] = useState<Set<number>>(new Set())
  const [customFact, setCustomFact] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)

  const update = (patch: Partial<AgentDefinitionStep>) => onChange({ ...state, ...patch })

  // Canonical mutation for the transitions array.
  // Invariants on every write:
  //   1. "otherwise" exits are sorted to the end.
  //   2. priority is derived from array position (position × 100) so the
  //      backend evaluation order matches what the author sees.
  const setTransitions = (next: Transition[]) => {
    const ordered = [
      ...next.filter((t) => t.when.kind !== 'otherwise'),
      ...next.filter((t) => t.when.kind === 'otherwise'),
    ]
    update({ transitions: ordered.map((t, i) => ({ ...t, priority: (i + 1) * 100 })) })
  }

  const updateTransition = (transitionId: string, patch: Partial<Transition>) => {
    setTransitions(state.transitions.map((t) => (t.id === transitionId ? { ...t, ...patch } : t)))
  }

  const updateToolBinding = (index: number, patch: Partial<ToolBinding>) => {
    update({
      tool_policy: state.tool_policy.map((b, i) => (i === index ? { ...b, ...patch } : b)),
    })
  }

  const updateGuard = (index: number, patch: Partial<GuardDef>) => {
    update({
      guards: state.guards.map((g, i) => (i === index ? { ...g, ...patch } : g)),
    })
  }

  const moveTransition = (id: string, direction: 'up' | 'down') => {
    const idx = state.transitions.findIndex((t) => t.id === id)
    if (idx === -1) return
    if (direction === 'up' && idx === 0) return
    if (direction === 'down' && idx === state.transitions.length - 1) return
    const next = [...state.transitions]
    const swapIdx = direction === 'up' ? idx - 1 : idx + 1
    ;[next[idx], next[swapIdx]] = [next[swapIdx], next[idx]]
    setTransitions(next)
  }

  const toggleToolAdvanced = (index: number) => {
    setExpandedTools((prev) => {
      const next = new Set(prev)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  const addFact = (name: string) => {
    const trimmed = name.trim()
    if (!trimmed || state.fact_requirements.some((item) => item.name === trimmed)) return
    update({ fact_requirements: [...state.fact_requirements, createFactRequirement(trimmed)] })
  }

  const removeFact = (name: string) => {
    update({
      fact_requirements: state.fact_requirements.filter((item) => item.name !== name),
    })
  }

  const updateFactRequirement = (
    factName: string,
    patch: Partial<FactRequirement>,
  ) => {
    const nextRequirements = state.fact_requirements.map((item) =>
      item.name === factName ? { ...item, ...patch } : item,
    )
    update({
      fact_requirements: nextRequirements,
    })
  }

  const warnings = getStateWarnings(state, agentDefinition as AgentDefinition | undefined)
  const currentFacts = factRequirementNames(state)
  const schemaFactNames = (factSchema ?? []).map((f) => f.name)
  const availableSchemaFacts = schemaFactNames.filter((n) => !currentFacts.includes(n))
  const otherStates = availableStates.filter((s) => s.id !== state.id)
  const responseMode = deriveResponseMode(state.response_policy)
  const hasAdvancedContent = kind === 'conversation' || kind === 'action'

  // ── Tool list (shared between action primary and conversation advanced) ──
  const ToolList = (
    <div className="space-y-3">
      {state.tool_policy.map((binding, index) => (
        <div key={index} className="space-y-2 rounded-md border border-border p-3">
          <ToolRefCombobox
            value={binding.ref}
            onChange={(ref) => updateToolBinding(index, { ref })}
            agentId={agentId}
            placeholder="e.g. knowledge.lookup, crm.create_contact"
          />

          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label className="text-[10px] text-muted-foreground">Mode</Label>
              <Select
                value={binding.mode}
                onValueChange={(v) => updateToolBinding(index, { mode: v as ToolBinding['mode'] })}
              >
                <SelectTrigger className="h-7 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(Object.keys(TOOL_MODE_LABELS) as ToolBinding['mode'][]).map((mode) => (
                    <SelectItem key={mode} value={mode}>
                      {TOOL_MODE_LABELS[mode]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1">
              <Label className="text-[10px] text-muted-foreground">When to run</Label>
              <Select
                value={binding.invocation_strategy}
                onValueChange={(v) =>
                  updateToolBinding(index, { invocation_strategy: v as ToolBinding['invocation_strategy'] })
                }
              >
                <SelectTrigger className="h-7 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(Object.keys(TOOL_STRATEGY_LABELS) as ToolBinding['invocation_strategy'][]).map(
                    (strategy) => (
                      <SelectItem key={strategy} value={strategy}>
                        {TOOL_STRATEGY_LABELS[strategy]}
                      </SelectItem>
                    ),
                  )}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Args */}
          <div className="space-y-1.5">
            <Label className="text-[10px] text-muted-foreground">Input mapping</Label>
            <div className="space-y-1.5">
              {Object.entries(binding.args).map(([key, value], argIndex) => (
                <div key={argIndex} className="flex items-center gap-1.5">
                  <Input
                    value={key}
                    onChange={(e) => {
                      const newArgs: Record<string, unknown> = {}
                      for (const [k, v] of Object.entries(binding.args)) {
                        newArgs[k === key ? e.target.value : k] = v
                      }
                      updateToolBinding(index, { args: newArgs })
                    }}
                    placeholder="arg name"
                    className="h-6 w-28 font-mono text-[11px]"
                  />
                  <span className="text-xs text-muted-foreground">←</span>
                  <Input
                    value={String(value)}
                    onChange={(e) =>
                      updateToolBinding(index, { args: { ...binding.args, [key]: e.target.value } })
                    }
                    placeholder="$fact.field or $turn.text"
                    className="h-6 flex-1 font-mono text-[11px]"
                  />
                  <button
                    onClick={() => {
                      const newArgs = { ...binding.args }
                      delete newArgs[key]
                      updateToolBinding(index, { args: newArgs })
                    }}
                    className="text-muted-foreground hover:text-rose-400"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              ))}
              <button
                onClick={() => updateToolBinding(index, { args: { ...binding.args, '': '' } })}
                className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
              >
                <Plus className="h-3 w-3" />
                Add input
              </button>
            </div>
            <p className="text-[10px] text-muted-foreground">
              Map tool inputs from facts ($fact.name) or the current turn ($turn.text, $turn.channel).
            </p>
          </div>

          {/* Advanced per-tool */}
          <button
            onClick={() => toggleToolAdvanced(index)}
            className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
          >
            {expandedTools.has(index) ? (
              <ChevronUp className="h-3 w-3" />
            ) : (
              <ChevronDown className="h-3 w-3" />
            )}
            Advanced
          </button>

          {expandedTools.has(index) && (
            <div className="space-y-2 rounded-md bg-muted/20 p-2">
              <div className="space-y-1">
                <Label className="text-[10px] text-muted-foreground">Event name override</Label>
                <Input
                  value={binding.event_name || ''}
                  onChange={(e) => updateToolBinding(index, { event_name: e.target.value || null })}
                  placeholder={binding.ref ? `auto: ${binding.ref.replace(/\./g, '_')}` : 'auto-derived from ref'}
                  className="h-6 font-mono text-[11px]"
                />
                <p className="text-[10px] text-muted-foreground">
                  Used in transition conditions as{' '}
                  <code className="text-[10px]">
                    tool_outcome:{binding.event_name || (binding.ref ? `${binding.ref.replace(/\./g, '_')}_success` : '…_success')}
                  </code>
                </p>
              </div>
              <div className="space-y-1">
                <Label className="text-[10px] text-muted-foreground">Timeout (ms)</Label>
                <Input
                  type="number"
                  value={binding.timeout_ms ?? ''}
                  onChange={(e) =>
                    updateToolBinding(index, {
                      timeout_ms: e.target.value ? Number(e.target.value) : null,
                    })
                  }
                  placeholder="default (3000ms)"
                  className="h-6 text-[11px]"
                />
              </div>
            </div>
          )}

          <Button
            variant="ghost"
            size="sm"
            onClick={() => update({ tool_policy: state.tool_policy.filter((_, i) => i !== index) })}
            className="px-0 text-rose-400 hover:text-rose-300"
          >
            Remove tool
          </Button>
        </div>
      ))}
      {state.tool_policy.length === 0 && (
        <p className="text-xs text-muted-foreground">No tools configured for this state.</p>
      )}
    </div>
  )

  // ── Guards (shared between conversation advanced and action advanced) ──
  const GuardsList = (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h5 className="text-xs font-medium text-muted-foreground">Guards</h5>
        <Button
          variant="outline"
          size="sm"
          onClick={() =>
            update({
              guards: [...state.guards, { kind: 'fact_required', value: '', description: '' }],
            })
          }
        >
          <Plus className="mr-1.5 h-3.5 w-3.5" />
          Add guard
        </Button>
      </div>

      {state.guards.map((guard, index) => (
        <div key={index} className="space-y-2 rounded-md border border-border p-3">
          <Select
            value={guard.kind}
            onValueChange={(v) => updateGuard(index, { kind: v as GuardDef['kind'], value: '' })}
          >
            <SelectTrigger className="h-7 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(GUARD_KIND_LABELS) as GuardDef['kind'][]).map((kind) => (
                <SelectItem key={kind} value={kind}>
                  {GUARD_KIND_LABELS[kind]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Input
            value={guard.value}
            onChange={(e) => updateGuard(index, { value: e.target.value })}
            placeholder={GUARD_VALUE_PLACEHOLDERS[guard.kind]}
            className="h-7 text-xs"
          />

          <Input
            value={guard.description || ''}
            onChange={(e) => updateGuard(index, { description: e.target.value })}
            placeholder="Optional description"
            className="h-7 text-xs"
          />

          <p className="flex items-center gap-1 text-[10px] text-muted-foreground">
            <Info className="h-3 w-3 shrink-0" />
            Configure failure behaviour via Transitions → Guard failed condition.
          </p>

          <Button
            variant="ghost"
            size="sm"
            onClick={() => update({ guards: state.guards.filter((_, i) => i !== index) })}
            className="px-0 text-rose-400 hover:text-rose-300"
          >
            Remove guard
          </Button>
        </div>
      ))}

      {state.guards.length === 0 && (
        <p className="text-xs text-muted-foreground">No guards configured.</p>
      )}
    </div>
  )

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
        <div>
          <h3 className="font-medium">State Inspector</h3>
          <p className="font-mono text-[10px] text-muted-foreground">{state.id.slice(0, 8)}…</p>
        </div>
        {canDelete && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onDelete}
            className="text-rose-400 hover:text-rose-300"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        )}
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto p-4">
        {/* Validation warnings */}
        {warnings.length > 0 && (
          <div className="space-y-1.5">
            {warnings.map((w, i) => (
              <div
                key={i}
                className="flex items-start gap-2 rounded-md border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground"
              >
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                {w}
              </div>
            ))}
          </div>
        )}

        {/* ── Overview (all kinds) ──────────────────────────────────────── */}
        <section className="space-y-3">
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">Step name</Label>
            <Input value={state.name} onChange={(e) => update({ name: e.target.value })} />
          </div>

          {/*
            Per docs/generic-state-redesign/01-generic-step-canvas-adr.md, authors
            no longer pick a step type — the runtime derives behavior from the
            optional capability fields (handoff, completion/terminal_disposition,
            action_config, fact_requirements). The kind shown here is derived
            and read-only; change the underlying capabilities to change the kind.
          */}
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">Derived kind</Label>
            <div className="flex h-8 items-center rounded-md border border-input bg-muted/40 px-3 text-sm capitalize">
              {kind}
            </div>
            <p className="text-[11px] text-muted-foreground">{STEP_KIND_DESCRIPTIONS[kind]}</p>
          </div>

        </section>

        <section className="space-y-3 border-t border-white/10 pt-4">
          <div>
            <h4 className="text-sm font-medium">Authored guidance</h4>
            <p className="text-xs text-muted-foreground">
              This is the state-local guidance layer the runtime passes into journey-aware prompting.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">Say on entry</Label>
            <Textarea
              value={state.say_on_entry || ''}
              rows={2}
              onChange={(e) => update({ say_on_entry: e.target.value || null })}
              placeholder="How this state should open or answer when the user lands here."
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">Say on transition</Label>
            <Textarea
              value={state.say_on_transition || ''}
              rows={2}
              onChange={(e) => update({ say_on_transition: e.target.value || null })}
              placeholder="How transitions into this state should be explained in user language."
            />
          </div>

          {kind === 'capture' && (
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">Ask for fact</Label>
              <Textarea
                value={state.ask_for_fact || ''}
                rows={2}
                onChange={(e) => update({ ask_for_fact: e.target.value || null })}
                placeholder="How to ask for the next required detail naturally."
              />
            </div>
          )}

          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">Repair response</Label>
            <Textarea
              value={state.repair_response || ''}
              rows={2}
              onChange={(e) => update({ repair_response: e.target.value || null })}
              placeholder="How this state should recover from confusion, hesitation, or pushback."
            />
          </div>
        </section>

        {/* ── Opening / handoff / closing message ───────────────────────── */}
        {(kind === 'entry' || kind === 'handoff' || kind === 'terminal') && (
          <section className="space-y-1.5 border-t border-white/10 pt-4">
            <Label className="text-xs text-muted-foreground">
              {kind === 'entry'
                ? 'Opening message'
                : kind === 'handoff'
                  ? 'Handoff message'
                  : 'Closing message'}
            </Label>
            <Textarea
              value={state.entry_response || ''}
              rows={3}
              onChange={(e) => update({ entry_response: e.target.value || null })}
              placeholder={
                kind === 'entry'
                  ? 'Greeting sent at the start of the conversation.'
                  : kind === 'handoff'
                    ? 'Message sent before handing off to a human agent.'
                    : 'Message sent when the conversation ends.'
              }
            />
            <p className="text-[11px] text-muted-foreground">
              {kind === 'entry'
                ? 'Sent once when the conversation opens, before the user speaks.'
                : kind === 'handoff'
                  ? 'Sent immediately when the agent transitions to this state.'
                  : 'Sent as the final message before the conversation closes.'}
            </p>
          </section>
        )}

        {/* ── Terminal disposition ───────────────────────────────────────── */}
        {kind === 'terminal' && (
          <section className="space-y-1.5 border-t border-white/10 pt-4">
            <Label className="text-xs text-muted-foreground">Closing disposition</Label>
            <Input
              value={state.terminal_disposition || ''}
              onChange={(e) => update({ terminal_disposition: e.target.value || null })}
              placeholder="resolved"
            />
            <p className="text-[11px] text-muted-foreground">
              How the conversation ended: resolved, transferred, abandoned, unsupported, error.
            </p>
          </section>
        )}

        {/* ── Facts to collect (capture only, primary) ───────────────────── */}
        {kind === 'capture' && (
          <section className="space-y-3 border-t border-white/10 pt-4">
            <h4 className="text-sm font-medium">Facts to collect</h4>

            {currentFacts.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {currentFacts.map((fact) => (
                  <span
                    key={fact}
                    className="flex items-center gap-1 rounded-md border border-border bg-secondary px-2 py-0.5 text-xs"
                  >
                    {fact}
                    <button
                      onClick={() => removeFact(fact)}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}

            {availableSchemaFacts.length > 0 && (
              <Select value="" onValueChange={(name) => addFact(name)}>
                <SelectTrigger className="h-7 text-xs">
                  <SelectValue placeholder="Add from agent schema..." />
                </SelectTrigger>
                <SelectContent>
                  {availableSchemaFacts.map((name) => (
                    <SelectItem key={name} value={name}>
                      {name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}

            <div className="flex gap-1.5">
              <Input
                value={customFact}
                onChange={(e) => setCustomFact(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    addFact(customFact)
                    setCustomFact('')
                  }
                }}
                placeholder="Custom fact name…"
                className="h-7 text-xs"
              />
              <Button
                variant="outline"
                size="sm"
                className="h-7 px-2"
                disabled={!customFact.trim() || currentFacts.includes(customFact.trim())}
                onClick={() => {
                  addFact(customFact)
                  setCustomFact('')
                }}
              >
                <Plus className="h-3 w-3" />
              </Button>
            </div>

            {factSchema.length === 0 && (
              <p className="flex items-center gap-1 text-[11px] text-muted-foreground">
                <Info className="h-3 w-3 shrink-0" />
                Define facts in the agent schema to add them here by name.
              </p>
            )}

            {state.fact_requirements.length > 0 && (
              <div className="space-y-3 rounded-md border border-white/10 bg-card/30 p-3">
                <p className="text-[11px] text-muted-foreground">
                  Add the user-facing purpose for each collected fact. Phrase the actual ask with the state-level Ask for fact field.
                </p>
                {state.fact_requirements.map((requirement) => (
                  <div key={requirement.name} className="space-y-2 rounded-md border border-white/10 p-3">
                    <div className="text-xs font-medium">{requirement.name}</div>
                    <div className="space-y-1.5">
                      <Label className="text-[10px] text-muted-foreground">Purpose</Label>
                      <Textarea
                        value={requirement.purpose || ''}
                        rows={2}
                        onChange={(e) =>
                          updateFactRequirement(requirement.name, { purpose: e.target.value || null })
                        }
                        placeholder="Why this fact is needed right now."
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

        {/* ── Response mode (conversation only, primary) ────────────────── */}
        {kind === 'conversation' && (
          <section className="space-y-1.5 border-t border-white/10 pt-4">
            <Label className="text-xs text-muted-foreground">Response mode</Label>
            <Select
              value={responseMode}
              onValueChange={(v) =>
                update({
                  response_policy: {
                    ...state.response_policy,
                    ...RESPONSE_MODE_POLICY[v as ResponseMode],
                  },
                })
              }
            >
              <SelectTrigger className="h-8 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="direct">Direct — answer without clarifying first</SelectItem>
                <SelectItem value="balanced">Balanced — answer, clarify only when needed</SelectItem>
                <SelectItem value="clarify">Clarify — ask before answering</SelectItem>
              </SelectContent>
            </Select>
          </section>
        )}

        {/* ── Action Config (action only) ──────────────────────────────── */}
        {kind === 'action' && (
          <section className="space-y-3 border-t border-white/10 pt-4">
            <h4 className="text-sm font-medium">Action Code</h4>
            <ActionConfigEditor
              config={(state as any).action_config ?? DEFAULT_ACTION_CONFIG}
              onChange={(actionConfig) => onChange({ ...state, action_config: actionConfig } as any)}
              agentId={agentId}
              stateId={state.id}
              factSchema={factSchema.map((f) => f.name)}
            />
          </section>
        )}

        {/* ── Artifact output (action only) ──────────────────────────────── */}
        {kind === 'action' && (
          <section className="space-y-2 border-t border-white/10 pt-4">
            <div>
              <h4 className="text-sm font-medium">Artifact output</h4>
              <p className="text-xs text-muted-foreground">
                If this action creates a durable object (booking, ticket, refund…), name its type here so later follow-up intents can target it.
              </p>
            </div>
            <Input
              value={state.artifact_type ?? ''}
              onChange={(e) =>
                update({ artifact_type: e.target.value ? e.target.value : null })
              }
              placeholder="e.g. booking, ticket, refund_request"
              className="h-8 text-sm"
            />
            <p className="text-[11px] text-muted-foreground">
              Register follow-up handlers for this type in the agent&apos;s Artifact Follow-ups section.
            </p>
          </section>
        )}

        {/* ── Interaction & pacing (action only) ─────────────────────────── */}
        {kind === 'action' && (
          <section className="space-y-3 border-t border-white/10 pt-4">
            <div>
              <h4 className="text-sm font-medium">Interaction &amp; pacing</h4>
              <p className="text-xs text-muted-foreground">
                How the runtime narrates this action to the user. Leave pacing blank to inherit the channel preset.
              </p>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">Activity label</Label>
              <Input
                value={state.activity_label ?? ''}
                onChange={(e) =>
                  update({ activity_label: e.target.value ? e.target.value : null })
                }
                placeholder="e.g. Checking the calendar"
                className="h-8 text-sm"
              />
              <p className="text-[11px] text-muted-foreground">
                Shown deterministically when work starts. Short, human phrase.
              </p>
            </div>

            <label className="flex items-center justify-between gap-3 rounded-md border border-white/10 bg-card/30 px-3 py-2">
              <div>
                <div className="text-sm">Publish status-trail item</div>
                <div className="text-[11px] text-muted-foreground">
                  Show a short-lived &quot;what&apos;s happening now&quot; entry to the user while this action is running.
                </div>
              </div>
              <Switch
                checked={state.publish_status_trail ?? false}
                onCheckedChange={(v) => update({ publish_status_trail: v })}
              />
            </label>

            <details className="group rounded-md border border-white/10 bg-card/30">
              <summary className="flex cursor-pointer list-none items-center justify-between px-3 py-2 text-sm">
                <span>Pacing overrides</span>
                <ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" />
              </summary>
              <div className="space-y-3 border-t border-white/10 px-3 py-3">
                <p className="text-[11px] text-muted-foreground">
                  Leave any field blank to inherit the channel/use-case default.
                </p>

                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                  <div className="space-y-1">
                    <Label className="text-[10px] text-muted-foreground">Slow threshold (ms)</Label>
                    <Input
                      type="number"
                      min={0}
                      value={state.slow_threshold_ms ?? ''}
                      onChange={(e) =>
                        update({
                          slow_threshold_ms: e.target.value === '' ? null : Number(e.target.value),
                        })
                      }
                      placeholder="1000–2000"
                      className="h-8 text-sm"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-[10px] text-muted-foreground">Soft timeout (ms)</Label>
                    <Input
                      type="number"
                      min={0}
                      value={state.soft_timeout_ms ?? ''}
                      onChange={(e) =>
                        update({
                          soft_timeout_ms: e.target.value === '' ? null : Number(e.target.value),
                        })
                      }
                      placeholder="2500–3000"
                      className="h-8 text-sm"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-[10px] text-muted-foreground">Endpointing (ms)</Label>
                    <Input
                      type="number"
                      min={0}
                      value={state.endpointing_ms ?? ''}
                      onChange={(e) =>
                        update({
                          endpointing_ms: e.target.value === '' ? null : Number(e.target.value),
                        })
                      }
                      placeholder="500–850"
                      className="h-8 text-sm"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div className="space-y-1">
                    <Label className="text-[10px] text-muted-foreground">Turn eagerness</Label>
                    <Select
                      value={state.turn_eagerness ?? 'inherit'}
                      onValueChange={(v) =>
                        update({
                          turn_eagerness: v === 'inherit' ? null : (v as TurnEagerness),
                        })
                      }
                    >
                      <SelectTrigger className="h-8 text-sm">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="inherit">Inherit from channel</SelectItem>
                        <SelectItem value="low">Low — wait longer</SelectItem>
                        <SelectItem value="normal">Normal</SelectItem>
                        <SelectItem value="high">High — claim the floor quickly</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1">
                    <Label className="text-[10px] text-muted-foreground">Interruptibility</Label>
                    <Select
                      value={state.interruptibility_policy ?? 'inherit'}
                      onValueChange={(v) =>
                        update({
                          interruptibility_policy:
                            v === 'inherit' ? null : (v as InterruptibilityPolicy),
                        })
                      }
                    >
                      <SelectTrigger className="h-8 text-sm">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="inherit">Inherit from channel</SelectItem>
                        <SelectItem value="always_interruptible">Always interruptible</SelectItem>
                        <SelectItem value="interruptible_except_policy">
                          Interruptible except policy/compliance speech
                        </SelectItem>
                        <SelectItem value="non_interruptible">Non-interruptible</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </div>
            </details>
          </section>
        )}

        {/* ── Capture authoring hints ──────────────────────────────────────── */}
        {kind === 'capture' && (
          <section className="space-y-3 border-t border-white/10 pt-4">
            <div>
              <h4 className="text-sm font-medium">Capture behaviour</h4>
              <p className="text-xs text-muted-foreground">
                Control capture-specific recovery behavior in addition to the authored guidance above.
              </p>
            </div>

            <label className="flex items-center justify-between gap-3 rounded-md border border-white/10 bg-card/30 px-3 py-2">
              <div>
                <div className="text-sm">Repair on repeated no-progress</div>
                <div className="text-[11px] text-muted-foreground">
                  If the user keeps missing the required fact, switch to an explicit repair response instead of re-asking.
                </div>
              </div>
              <Switch
                checked={state.repair_on_no_progress ?? false}
                onCheckedChange={(v) => update({ repair_on_no_progress: v })}
              />
            </label>
          </section>
        )}

        {/* ── Conversation authoring hints ─────────────────────────────────── */}
        {kind === 'conversation' && (
          <section className="space-y-3 border-t border-white/10 pt-4">
            <div>
              <h4 className="text-sm font-medium">Interaction shape</h4>
              <p className="text-xs text-muted-foreground">
                Tells the runtime and reviewers what kind of state this is, without changing the condition logic.
              </p>
            </div>

            <label className="flex items-center justify-between gap-3 rounded-md border border-white/10 bg-card/30 px-3 py-2">
              <div>
                <div className="text-sm">Commonly performs repair</div>
                <div className="text-[11px] text-muted-foreground">
                  This state often handles interruption, contradiction, or user confusion.
                </div>
              </div>
              <Switch
                checked={state.performs_repair ?? false}
                onCheckedChange={(v) => update({ performs_repair: v })}
              />
            </label>

            <label className="flex items-center justify-between gap-3 rounded-md border border-white/10 bg-card/30 px-3 py-2">
              <div>
                <div className="text-sm">Expects policy-blocked branches</div>
                <div className="text-[11px] text-muted-foreground">
                  This state is expected to produce confirmation/compliance/escalation branches.
                </div>
              </div>
              <Switch
                checked={state.expects_policy_blocks ?? false}
                onCheckedChange={(v) => update({ expects_policy_blocks: v })}
              />
            </label>
          </section>
        )}

        {/* ── Exits (all non-terminal states) ─────────────────────────── */}
        {kind !== 'terminal' && (
          <section className="space-y-3 border-t border-white/10 pt-4">
            <div className="flex items-center justify-between">
              <h4 className="text-sm font-medium">Transitions</h4>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  const defaultTarget = otherStates[0]?.id ?? state.id
                  setTransitions([...state.transitions, createBlankTransition(defaultTarget)])
                }}
              >
                <Plus className="mr-1.5 h-3.5 w-3.5" />
                Add transition
              </Button>
            </div>

            <div className="space-y-3">
              {state.transitions.map((transition) => (
                <div
                  key={transition.id}
                  className="space-y-2 rounded-md border border-border p-3"
                >
                  <div className="space-y-1">
                    <Label className="text-[10px] text-muted-foreground">Trigger</Label>
                    <Select
                      value={transition.when.kind}
                      onValueChange={(v) =>
                        updateTransition(transition.id, {
                          when: {
                            kind: v as ConditionKind,
                            value: v === 'otherwise' ? null : transition.when.value,
                          },
                        })
                      }
                    >
                      <SelectTrigger className="h-7 text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {CONDITION_KINDS.map((kind) => {
                          const fallbackTaken =
                            kind === 'otherwise' &&
                            transition.when.kind !== 'otherwise' &&
                            state.transitions.some(
                              (t) => t.id !== transition.id && t.when.kind === 'otherwise',
                            )
                          return (
                            <SelectItem key={kind} value={kind} disabled={fallbackTaken}>
                              {CONDITION_KIND_LABELS[kind]}
                              {fallbackTaken && (
                                <span className="ml-1 text-muted-foreground">(already set)</span>
                              )}
                            </SelectItem>
                          )
                        })}
                      </SelectContent>
                    </Select>
                  </div>

                  {transition.when.kind !== 'otherwise' && (
                    <div className="space-y-1">
                      <Label className="text-[10px] text-muted-foreground">Value</Label>
                      <Input
                        value={transition.when.value || ''}
                        onChange={(e) =>
                          updateTransition(transition.id, {
                            when: { ...transition.when, value: e.target.value },
                          })
                        }
                        placeholder={
                          CONDITION_VALUE_PLACEHOLDERS[transition.when.kind] ?? 'value'
                        }
                        className="h-7 font-mono text-xs"
                      />
                    </div>
                  )}

                  <div className="space-y-1">
                    <Label className="text-[10px] text-muted-foreground">Go to state</Label>
                    <Select
                      value={transition.to}
                      onValueChange={(v) => updateTransition(transition.id, { to: v })}
                    >
                      <SelectTrigger className="h-7 text-xs">
                        <SelectValue placeholder="Select state…" />
                      </SelectTrigger>
                      <SelectContent>
                        {availableStates.map((s) => (
                          <SelectItem key={s.id} value={s.id}>
                            {s.name}
                            {s.id === state.id && (
                              <span className="ml-1 text-muted-foreground">(self)</span>
                            )}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="space-y-1">
                    <Label className="text-[10px] text-muted-foreground">
                      Branch intent
                      <span className="ml-1 text-muted-foreground/70">(author hint, optional)</span>
                    </Label>
                    <Select
                      value={transition.branch_intent ?? 'unspecified'}
                      onValueChange={(v) =>
                        updateTransition(transition.id, {
                          branch_intent:
                            v === 'unspecified' ? null : (v as TransitionBranchIntent),
                        })
                      }
                    >
                      <SelectTrigger className="h-7 text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="unspecified">Unspecified</SelectItem>
                        <SelectItem value="continue">Continue — normal progress</SelectItem>
                        <SelectItem value="confirm">Confirm — user consent required</SelectItem>
                        <SelectItem value="ask_again">Ask again — re-prompt</SelectItem>
                        <SelectItem value="repair">Repair — recover from misunderstanding</SelectItem>
                        <SelectItem value="block">Block — policy/compliance stop</SelectItem>
                        <SelectItem value="escalate">Escalate — hand off / compliance route</SelectItem>
                      </SelectContent>
                    </Select>
                    {(transition.branch_intent === 'confirm' ||
                      transition.branch_intent === 'block' ||
                      transition.branch_intent === 'escalate') && (
                      <p className="text-[10px] text-amber-400/80">
                        This branch produces a pending interaction state (confirmation / policy /
                        escalation). It is not a silent hop.
                      </p>
                    )}
                  </div>

                  <div className="space-y-1">
                    <Label className="text-[10px] text-muted-foreground">Natural reason</Label>
                    <Textarea
                      value={transition.natural_reason || ''}
                      rows={2}
                      onChange={(e) =>
                        updateTransition(transition.id, {
                          natural_reason: e.target.value || null,
                        })
                      }
                      placeholder="User-facing explanation of why this branch exists."
                    />
                  </div>

                  <div className="space-y-1">
                    <Label className="text-[10px] text-muted-foreground">When to use</Label>
                    <Textarea
                      value={transition.when_to_use || ''}
                      rows={2}
                      onChange={(e) =>
                        updateTransition(transition.id, {
                          when_to_use: e.target.value || null,
                        })
                      }
                      placeholder="Describe when the runtime should prefer this route in user terms."
                    />
                  </div>

                  <div className="flex items-center gap-1">
                    <span className="flex-1 text-[10px] text-muted-foreground">
                      Evaluated top-to-bottom — fallback last
                    </span>
                    <button
                      disabled={state.transitions.indexOf(transition) === 0}
                      onClick={() => moveTransition(transition.id, 'up')}
                      className="rounded p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-30"
                      title="Move up"
                    >
                      <ChevronUp className="h-3.5 w-3.5" />
                    </button>
                    <button
                      disabled={
                        state.transitions.indexOf(transition) === state.transitions.length - 1
                      }
                      onClick={() => moveTransition(transition.id, 'down')}
                      className="rounded p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-30"
                      title="Move down"
                    >
                      <ChevronDown className="h-3.5 w-3.5" />
                    </button>
                  </div>

                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setTransitions(state.transitions.filter((t) => t.id !== transition.id))
                    }
                    className="px-0 text-rose-400 hover:text-rose-300"
                  >
                    Remove
                  </Button>
                </div>
              ))}

              {state.transitions.length === 0 && (
                <p className="text-xs text-muted-foreground">No transitions configured.</p>
              )}
            </div>
          </section>
        )}

        {/* ── Advanced ─────────────────────────────────────────────────── */}
        {hasAdvancedContent && (
          <section className="border-t border-white/10 pt-4">
            <button
              onClick={() => setShowAdvanced((v) => !v)}
              className={cn(
                'flex w-full items-center gap-2 text-xs text-muted-foreground hover:text-foreground',
              )}
            >
              {showAdvanced ? (
                <ChevronUp className="h-3.5 w-3.5" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5" />
              )}
              Advanced
            </button>

            {showAdvanced && (
              <div className="mt-4 space-y-5">
                {/* Guards (conversation + action) */}
                {GuardsList}

                {/* Conversation-only advanced fields */}
                {kind === 'conversation' && (
                  <>
                    {/* Required facts (context only, not primary for conversation) */}
                    <div className="space-y-2 border-t border-white/10 pt-4">
                      <Label className="text-xs text-muted-foreground">
                        Contextual facts
                      </Label>
                      <p className="text-[11px] text-muted-foreground">
                        Facts this state may reference. Collected by upstream capture states.
                      </p>

                      {currentFacts.length > 0 && (
                        <div className="flex flex-wrap gap-1.5">
                          {currentFacts.map((fact) => (
                            <span
                              key={fact}
                              className="flex items-center gap-1 rounded-md border border-border bg-secondary px-2 py-0.5 text-xs"
                            >
                              {fact}
                              <button
                                onClick={() => removeFact(fact)}
                                className="text-muted-foreground hover:text-foreground"
                              >
                                <X className="h-3 w-3" />
                              </button>
                            </span>
                          ))}
                        </div>
                      )}

                      {availableSchemaFacts.length > 0 && (
                        <Select value="" onValueChange={(name) => addFact(name)}>
                          <SelectTrigger className="h-7 text-xs">
                            <SelectValue placeholder="Add from agent schema..." />
                          </SelectTrigger>
                          <SelectContent>
                            {availableSchemaFacts.map((name) => (
                              <SelectItem key={name} value={name}>
                                {name}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      )}

                      <div className="flex gap-1.5">
                        <Input
                          value={customFact}
                          onChange={(e) => setCustomFact(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              addFact(customFact)
                              setCustomFact('')
                            }
                          }}
                          placeholder="Fact name…"
                          className="h-7 text-xs"
                        />
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-7 px-2"
                          disabled={!customFact.trim() || currentFacts.includes(customFact.trim())}
                          onClick={() => {
                            addFact(customFact)
                            setCustomFact('')
                          }}
                        >
                          <Plus className="h-3 w-3" />
                        </Button>
                      </div>
                    </div>

                    {/* Accepted inputs */}
                    <div className="space-y-1.5 border-t border-white/10 pt-4">
                      <Label className="text-xs text-muted-foreground">Accepted input types</Label>
                      <Input
                        value={state.accepted_inputs.join(', ')}
                        onChange={(e) =>
                          update({
                            accepted_inputs: e.target.value
                              .split(',')
                              .map((s) => s.trim())
                              .filter(Boolean),
                          })
                        }
                        placeholder="e.g. product question, pricing question"
                      />
                      <p className="text-[11px] text-muted-foreground">
                        Descriptive hint — not enforced by the runtime. Used in documentation and traces.
                      </p>
                    </div>

                    {/* Voice style */}
                    <div className="space-y-1.5 border-t border-white/10 pt-4">
                      <Label className="text-xs text-muted-foreground">Voice response length</Label>
                      <Select
                        value={state.response_policy.voice_style}
                        onValueChange={(v) =>
                          update({
                            response_policy: {
                              ...state.response_policy,
                              voice_style: v as AgentDefinitionStep['response_policy']['voice_style'],
                            },
                          })
                        }
                      >
                        <SelectTrigger className="h-8 text-sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="concise">Concise — short, direct answers</SelectItem>
                          <SelectItem value="balanced">Balanced — natural conversational length</SelectItem>
                          <SelectItem value="detailed">Detailed — thorough explanations</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>

                    {/* Direct answer prompt */}
                    <div className="space-y-1.5 border-t border-white/10 pt-4">
                      <Label className="text-xs text-muted-foreground">Response prompt override</Label>
                      <Textarea
                        value={state.response_policy.direct_answer_prompt || ''}
                        rows={3}
                        onChange={(e) =>
                          update({
                            response_policy: {
                              ...state.response_policy,
                              direct_answer_prompt: e.target.value || null,
                            },
                          })
                        }
                        placeholder="Optional override for the default response prompt."
                      />
                      <p className="text-[11px] text-muted-foreground">
                        Overrides the default response-generation prompt. Leave blank to use the
                        runtime default.
                      </p>
                    </div>

                    {/* Optional lookup tools */}
                    <div className="space-y-3 border-t border-white/10 pt-4">
                      <div className="flex items-center justify-between">
                        <h5 className="text-xs font-medium text-muted-foreground">Lookup tools</h5>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() =>
                            update({
                              tool_policy: [...state.tool_policy, createBlankToolBinding(kind)],
                            })
                          }
                        >
                          <Plus className="mr-1.5 h-3.5 w-3.5" />
                          Add tool
                        </Button>
                      </div>
                      {ToolList}
                    </div>
                  </>
                )}
              </div>
            )}
          </section>
        )}

        {/* ── Preview (all types) ───────────────────────────────────────── */}
        <section className="space-y-2 border-t border-white/10 pt-4">
          <h4 className="text-sm font-medium">Preview</h4>
          <p className="rounded-md bg-muted/30 p-3 text-xs leading-relaxed text-muted-foreground">
            {buildPreview(state, availableStates, kind)}
          </p>
        </section>
      </div>
    </div>
  )
}
