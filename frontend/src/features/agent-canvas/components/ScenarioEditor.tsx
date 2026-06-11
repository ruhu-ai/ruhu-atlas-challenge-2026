/**
 * ScenarioEditor — scenario-first workflow authoring surface (v2).
 *
 * The scenario document is the only writable workflow source.
 * The server compiles it into the derived flow on save.
 */

import React, {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { CodeStepEditor } from './CodeStepEditor'
import {
  ArrowRight,
  GitBranch,
  Loader2,
  MessageSquare,
  Mic,
  PhoneForwarded,
  Plus,
  Sparkles,
  Trash2,
  XCircle,
  Zap,
} from 'lucide-react'

import { Button } from '@/components/atoms/button'
import { Input } from '@/components/atoms/input'
import { cn } from '@/lib/utils'
import { canvasService } from '@/api/services/canvas.service'
import { toolService, type ExternalToolCatalogItem } from '@/api/services/tools.service'
import type {
  ScenarioDocumentBody,
  ScenarioResources,
  ScenarioV2,
  StepV2,
  SayStep,
  CollectStep,
  DecideStep,
  DoStep,
  AiStep,
  HandoffStep,
  EndStep,
  OutcomeV2,
  OutcomeWhen,
  OutcomeNext,
  EventWhen,
  DoOperation,
  CodeOperation,
  ConditionOperator,
  EffectV2,
  CanvasVersion,
  CanvasNode,
  CanvasEdge,
} from '@/types/canvas'

const uuidv4 = () => crypto.randomUUID()

type StepKind = StepV2['kind']

const STEP_KINDS: Array<{
  kind: StepKind
  label: string
  subtitle: string
  icon: React.ElementType
}> = [
  { kind: 'say',     label: 'Say',     subtitle: 'Deterministic customer-facing copy',         icon: MessageSquare  },
  { kind: 'collect', label: 'Collect', subtitle: 'Ask, wait, retry, and capture input',         icon: Mic            },
  { kind: 'decide',  label: 'Decide',  subtitle: 'Branch on variables, events, or results',     icon: GitBranch      },
  { kind: 'do',      label: 'Do',      subtitle: 'Invoke one tool or orchestrate tools in code', icon: Zap            },
  { kind: 'ai',      label: 'AI',      subtitle: 'Use the model for reasoning or generation',   icon: Sparkles       },
  { kind: 'handoff', label: 'Handoff', subtitle: 'Transfer to a human or another queue',        icon: PhoneForwarded },
  { kind: 'end',     label: 'End',     subtitle: 'Close the journey and set the disposition',   icon: XCircle        },
]

const KIND_META = Object.fromEntries(STEP_KINDS.map((step) => [step.kind, step]))

const OPERATORS: Array<{ value: ConditionOperator; label: string }> = [
  { value: 'is_set', label: 'is set' },
  { value: 'is_not_set', label: 'is not set' },
  { value: 'equals', label: '=' },
  { value: 'not_equals', label: '≠' },
  { value: 'contains', label: 'contains' },
  { value: 'greater_than', label: '>' },
  { value: 'less_than', label: '<' },
]

const OUTCOME_WHEN_KINDS = [
  { value: 'default', label: 'Default' },
  { value: 'variable', label: 'Variable' },
  { value: 'event', label: 'Event' },
  { value: 'result', label: 'Result' },
  { value: 'attempts_exhausted', label: 'Attempts exhausted' },
]

const EVENT_TYPES: Array<{ value: EventWhen['event']; label: string }> = [
  { value: 'user_replied', label: 'User replied' },
  { value: 'no_input', label: 'No input' },
  { value: 'timeout', label: 'Timeout' },
  { value: 'upload_success', label: 'Upload success' },
  { value: 'upload_failed', label: 'Upload failed' },
]

const EMPTY_DOCUMENT: ScenarioDocumentBody = {
  version: '2.0',
  entry_scenario_id: '',
  variables: [],
  scenarios: [],
}

function makeScenario(order: number): ScenarioV2 {
  return {
    id: uuidv4(),
    key: '',
    name: `Scenario ${order + 1}`,
    summary: '',
    order,
    steps: [],
  }
}

function makeStep(kind: StepKind): StepV2 {
  const base = {
    id: uuidv4(),
    key: '',
    title: KIND_META[kind]?.label ?? kind,
    notes: '',
    outcomes: [],
    advanced: {},
  }

  switch (kind) {
    case 'say':
      return { ...base, kind: 'say', prompt: { mode: 'verbatim', text: '' } } as SayStep
    case 'collect':
      return {
        ...base,
        kind: 'collect',
        prompt: { mode: 'verbatim', text: '' },
        capture: { slot_names: [], entity_hints: [] },
        retry_policy: { max_attempts: 3 },
      } as CollectStep
    case 'decide':
      return { ...base, kind: 'decide', source: 'variable' } as DecideStep
    case 'do':
      return { ...base, kind: 'do', operation: { kind: 'tool', tool_ref: '', input: {} } } as DoStep
    case 'ai':
      return { ...base, kind: 'ai', prompt: { system: '', output_mode: 'text' } } as AiStep
    case 'handoff':
      return { ...base, kind: 'handoff', target: { type: 'queue', value: '' } } as HandoffStep
    case 'end':
      return { ...base, kind: 'end' } as EndStep
  }
}

function makeOutcome(): OutcomeV2 {
  return { id: uuidv4(), label: 'Outcome', when: { kind: 'variable', variable: '', operator: 'is_set' }, effects: [] }
}

function makeEffect(kind: EffectV2['kind']): EffectV2 {
  switch (kind) {
    case 'mark':
      return { kind: 'mark', name: '' }
    case 'set_variable':
      return { kind: 'set_variable', name: '', value: '' }
    case 'trigger':
      return { kind: 'trigger', name: '', payload: {} }
    case 'create_ticket':
      return { kind: 'create_ticket', queue: '', fields: {} }
    case 'transfer':
      return { kind: 'transfer', target: { type: 'queue', value: '' } }
  }
}

function splitCsv(value: string): string[] {
  return value
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)
}


interface ScenarioEditorSavedPayload {
  document: ScenarioDocumentBody
  version: CanvasVersion
  nodes: CanvasNode[]
  edges: CanvasEdge[]
}

export interface ScenarioEditorHandle {
  save: () => Promise<boolean>
  hasUnsavedChanges: () => boolean
  selectScenario: (id: string) => void
  addScenario: () => void
  deleteScenario: (id: string) => void
  updateScenarioResources: (scenarioId: string, resources: ScenarioResources) => void
}

export interface ScenarioEditorProps {
  canvasVersionId: string | undefined
  agentId?: string
  onDerivedFlowUpdated?: (payload: ScenarioEditorSavedPayload) => void
  onDirtyChange?: (dirty: boolean) => void
  onScenariosChange?: (scenarios: ScenarioV2[], selectedId: string | null, entryScenarioId: string) => void
}

export const ScenarioEditor = forwardRef<ScenarioEditorHandle, ScenarioEditorProps>(
  ({ canvasVersionId, agentId, onDerivedFlowUpdated, onDirtyChange, onScenariosChange }, ref) => {
    const queryClient = useQueryClient()
    const [selectedScenarioId, setSelectedScenarioId] = useState<string | null>(null)
    const [editingScenarioName, setEditingScenarioName] = useState<string | null>(null)
    const [document, setDocument] = useState<ScenarioDocumentBody>(EMPTY_DOCUMENT)
    const isDirtyRef = useRef(false)

    const setDirty = useCallback((nextDirty: boolean) => {
      isDirtyRef.current = nextDirty
      onDirtyChange?.(nextDirty)
    }, [onDirtyChange])

    const { data: serverDoc, isLoading, isError } = useQuery({
      queryKey: ['scenario-document', canvasVersionId],
      queryFn: () => canvasService.getScenarioDocument(canvasVersionId!),
      enabled: !!canvasVersionId,
      staleTime: 30_000,
    })
    const isValidAgentId = !!agentId && agentId !== 'new' && !agentId.includes('/')
    const { data: externalToolCatalog = [] } = useQuery({
      queryKey: ['agent-tool-catalog', agentId],
      queryFn: () => toolService.getCatalog(agentId!),
      enabled: isValidAgentId,
      staleTime: 30_000,
    })


    useEffect(() => {
      if (!serverDoc) return
      // Never overwrite unsaved local edits when serverDoc changes (e.g. background refetch)
      if (isDirtyRef.current) return
      const doc = serverDoc.document
      if (doc.scenarios.length === 0) {
        const first = makeScenario(0)
        setDocument({ ...doc, scenarios: [first], entry_scenario_id: first.id })
        setDirty(true)
        setSelectedScenarioId(first.id)
      } else {
        setDocument(doc)
        // Only auto-select once — don't override user's manual tab selection
        setSelectedScenarioId((prev) => prev ?? (doc.entry_scenario_id || doc.scenarios[0].id))
      }
    }, [serverDoc, setDirty])

    const loadDerivedFlow = useCallback(async (doc: ScenarioDocumentBody) => {
      if (!canvasVersionId) return
      const { version, nodes, edges } = await canvasService.loadCanvas(canvasVersionId)
      onDerivedFlowUpdated?.({ document: doc, version, nodes, edges })
    }, [canvasVersionId, onDerivedFlowUpdated])

    const saveMutation = useMutation({
      mutationFn: async (doc: ScenarioDocumentBody) => {
        const response = await canvasService.putScenarioDocument(canvasVersionId!, doc)
        await loadDerivedFlow(response.document)
        return response
      },
      onSuccess: (response) => {
        setDocument(response.document)
        setDirty(false)
        queryClient.invalidateQueries({ queryKey: ['scenario-document', canvasVersionId] })
        queryClient.invalidateQueries({ queryKey: ['agent', agentId] })
        queryClient.invalidateQueries({ queryKey: ['agent-deploy-readiness', agentId] })
        toast.success('Scenario flow saved')
      },
      onError: (error: Error) => {
        toast.error(`Scenario save failed: ${error.message}`)
      },
    })

    const handleSave = useCallback(async (): Promise<boolean> => {
      if (!canvasVersionId) return false
      // Skip save if document has no steps — backend rejects empty documents
      const hasSteps = document.scenarios.some((s) => s.steps && s.steps.length > 0)
      if (!hasSteps) {
        // Silently succeed — nothing to save yet
        return true
      }
      await saveMutation.mutateAsync(document)
      return true
    }, [canvasVersionId, document, saveMutation])

    const updateDocument = useCallback((updater: (previous: ScenarioDocumentBody) => ScenarioDocumentBody) => {
      setDocument((previous) => updater(previous))
      setDirty(true)
    }, [setDirty])

    const addScenario = useCallback(() => {
      const scenario = makeScenario(document.scenarios.length)
      updateDocument((previous) => ({
        ...previous,
        scenarios: [...previous.scenarios, scenario],
        entry_scenario_id: previous.entry_scenario_id || scenario.id,
      }))
      setSelectedScenarioId(scenario.id)
    }, [document.scenarios.length, updateDocument])

    const deleteScenario = useCallback((scenarioId: string) => {
      updateDocument((previous) => {
        const scenarios = previous.scenarios
          .filter((scenario) => scenario.id !== scenarioId)
          .map((scenario, index) => ({ ...scenario, order: index }))
        return {
          ...previous,
          scenarios,
          entry_scenario_id:
            previous.entry_scenario_id === scenarioId
              ? (scenarios[0]?.id ?? '')
              : previous.entry_scenario_id,
        }
      })
      setSelectedScenarioId((current) => {
        if (current !== scenarioId) return current
        const nextScenario = document.scenarios.find((scenario) => scenario.id !== scenarioId)
        return nextScenario?.id ?? null
      })
    }, [document.scenarios, updateDocument])

    useEffect(() => {
      onScenariosChange?.(document.scenarios, selectedScenarioId, document.entry_scenario_id)
    }, [document.scenarios, document.entry_scenario_id, selectedScenarioId, onScenariosChange])

    const updateScenario = useCallback((scenarioId: string, updater: (scenario: ScenarioV2) => ScenarioV2) => {
      updateDocument((previous) => ({
        ...previous,
        scenarios: previous.scenarios.map((scenario) => (
          scenario.id === scenarioId ? updater(scenario) : scenario
        )),
      }))
    }, [updateDocument])

    useImperativeHandle(ref, () => ({
      save: handleSave,
      hasUnsavedChanges: () => isDirtyRef.current,
      selectScenario: (id: string) => setSelectedScenarioId(id),
      addScenario,
      deleteScenario,
      updateScenarioResources: (scenarioId: string, resources: ScenarioResources) =>
        updateScenario(scenarioId, (s) => ({ ...s, resources })),
    }), [handleSave, addScenario, deleteScenario, updateScenario])

    const addStep = useCallback((scenarioId: string, kind: StepKind) => {
      updateScenario(scenarioId, (scenario) => ({
        ...scenario,
        steps: [...scenario.steps, makeStep(kind)],
      }))
    }, [updateScenario])

    const updateStep = useCallback((scenarioId: string, step: StepV2) => {
      updateScenario(scenarioId, (scenario) => ({
        ...scenario,
        steps: scenario.steps.map((existing) => existing.id === step.id ? step : existing),
      }))
    }, [updateScenario])

    const deleteStep = useCallback((scenarioId: string, stepId: string) => {
      updateScenario(scenarioId, (scenario) => ({
        ...scenario,
        steps: scenario.steps.filter((step) => step.id !== stepId),
      }))
    }, [updateScenario])

    const addOutcome = useCallback((scenarioId: string, stepId: string) => {
      updateScenario(scenarioId, (scenario) => ({
        ...scenario,
        steps: scenario.steps.map((step) => (
          step.id === stepId ? { ...step, outcomes: [...step.outcomes, makeOutcome()] } : step
        )),
      }))
    }, [updateScenario])

    const updateOutcome = useCallback((scenarioId: string, stepId: string, outcome: OutcomeV2) => {
      updateScenario(scenarioId, (scenario) => ({
        ...scenario,
        steps: scenario.steps.map((step) => (
          step.id === stepId
            ? { ...step, outcomes: step.outcomes.map((existing) => existing.id === outcome.id ? outcome : existing) }
            : step
        )),
      }))
    }, [updateScenario])

    const deleteOutcome = useCallback((scenarioId: string, stepId: string, outcomeId: string) => {
      updateScenario(scenarioId, (scenario) => ({
        ...scenario,
        steps: scenario.steps.map((step) => (
          step.id === stepId
            ? { ...step, outcomes: step.outcomes.filter((outcome) => outcome.id !== outcomeId) }
            : step
        )),
      }))
    }, [updateScenario])

    const scenarios = document.scenarios
    const selectedScenario = scenarios.find((scenario) => scenario.id === selectedScenarioId) ?? null
    const allSteps = useMemo(() => (
      document.scenarios.flatMap((scenario) => scenario.steps.map((step, idx) => ({
        scenarioId: scenario.id,
        scenarioName: scenario.name,
        stepId: step.id,
        stepTitle: step.title,
        stepKind: step.kind,
        stepIndex: idx,
      })))
    ), [document.scenarios])

    if (!canvasVersionId) {
      return (
        <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
          Save the agent first to start authoring scenarios.
        </div>
      )
    }

    if (isLoading) {
      return (
        <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading scenario flow…
        </div>
      )
    }

    if (isError) {
      return (
        <div className="flex h-full items-center justify-center text-sm text-destructive">
          Failed to load scenario document.
        </div>
      )
    }

    return (
      <div className="h-full overflow-y-auto bg-background text-foreground">
        <div className="mx-auto max-w-3xl space-y-5 px-6 py-6">
          {selectedScenario ? (
            <ScenarioPane
              scenario={selectedScenario}
              allScenarios={scenarios}
              allSteps={allSteps}
              scenarioVariables={document.variables.map((variable) => variable.name)}
              externalToolCatalog={externalToolCatalog}
              agentId={agentId}
              editingName={editingScenarioName === selectedScenario.id}
              onStartEditName={() => setEditingScenarioName(selectedScenario.id)}
              onEndEditName={() => setEditingScenarioName(null)}
              onRename={(name) => updateScenario(selectedScenario.id, (s) => ({ ...s, name }))}
              onPatch={(patch) => updateScenario(selectedScenario.id, (s) => ({ ...s, ...patch }))}
              onAddStep={(kind) => addStep(selectedScenario.id, kind)}
              onUpdateStep={(step) => updateStep(selectedScenario.id, step)}
              onDeleteStep={(stepId) => deleteStep(selectedScenario.id, stepId)}
              onAddOutcome={(stepId) => addOutcome(selectedScenario.id, stepId)}
              onUpdateOutcome={(stepId, outcome) => updateOutcome(selectedScenario.id, stepId, outcome)}
              onDeleteOutcome={(stepId, outcomeId) => deleteOutcome(selectedScenario.id, stepId, outcomeId)}
            />
          ) : (
            <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
              Select a scenario from the left panel.
            </div>
          )}
        </div>
      </div>
    )
  },
)

ScenarioEditor.displayName = 'ScenarioEditor'



interface ScenarioPaneProps {
  scenario: ScenarioV2
  allScenarios: ScenarioV2[]
  allSteps: Array<{ scenarioId: string; scenarioName: string; stepId: string; stepTitle: string; stepKind: StepV2['kind']; stepIndex: number }>
  scenarioVariables: string[]
  externalToolCatalog: ExternalToolCatalogItem[]
  agentId?: string
  editingName: boolean
  onStartEditName: () => void
  onEndEditName: () => void
  onRename: (name: string) => void
  onPatch: (patch: Partial<ScenarioV2>) => void
  onAddStep: (kind: StepKind) => void
  onUpdateStep: (step: StepV2) => void
  onDeleteStep: (stepId: string) => void
  onAddOutcome: (stepId: string) => void
  onUpdateOutcome: (stepId: string, outcome: OutcomeV2) => void
  onDeleteOutcome: (stepId: string, outcomeId: string) => void
}

const ScenarioPane: React.FC<ScenarioPaneProps> = ({
  scenario,
  allScenarios,
  allSteps,
  scenarioVariables,
  externalToolCatalog,
  agentId,
  editingName,
  onStartEditName,
  onEndEditName,
  onRename,
  onPatch,
  onAddStep,
  onUpdateStep,
  onDeleteStep,
  onAddOutcome,
  onUpdateOutcome,
  onDeleteOutcome,
}) => {
  return (
    <div className="rounded-3xl border border-border bg-card p-6 shadow-sm">
      {/* Scenario header */}
      <div className="border-b border-border pb-5">
        {editingName ? (
          <Input
            autoFocus
            defaultValue={scenario.name}
            className="h-auto border-0 px-0 text-2xl font-semibold text-foreground shadow-none focus-visible:ring-0"
            onBlur={(event) => { onRename(event.target.value); onEndEditName() }}
            onKeyDown={(event) => {
              if (event.key === 'Enter') { onRename((event.target as HTMLInputElement).value); onEndEditName() }
            }}
          />
        ) : (
          <button type="button" onClick={onStartEditName} className="text-left text-2xl font-semibold text-foreground hover:text-muted-foreground">
            {scenario.name}
          </button>
        )}
        <Input
          value={scenario.summary ?? ''}
          onChange={(event) => onPatch({ summary: event.target.value })}
          placeholder="Describe what this journey handles…"
          className="mt-2 h-8 border-0 px-0 text-sm text-muted-foreground shadow-none placeholder:text-muted-foreground/60 focus-visible:ring-0"
        />
      </div>

      <div className="mt-5">
        {scenario.steps.length === 0 && (
          <p className="py-6 text-center text-sm text-muted-foreground">No steps yet — add one below.</p>
        )}

        {scenario.steps.map((step, index) => (
          <React.Fragment key={step.id}>
            {index > 0 && (
              <div className="flex justify-center">
                <div className="h-6 w-px bg-border" />
              </div>
            )}
            <StepCard
              step={step}
              stepIndex={index}
              allScenarios={allScenarios}
              allSteps={allSteps}
              scenarioVariables={scenarioVariables}
              externalToolCatalog={externalToolCatalog}
              agentId={agentId}
              onUpdate={onUpdateStep}
              onDelete={() => onDeleteStep(step.id)}
              onAddOutcome={() => onAddOutcome(step.id)}
              onUpdateOutcome={(outcome) => onUpdateOutcome(step.id, outcome)}
              onDeleteOutcome={(outcomeId) => onDeleteOutcome(step.id, outcomeId)}
            />
          </React.Fragment>
        ))}
      </div>

      {/* Always-visible step type bar */}
      <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-border pt-4">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground/70">Add step</span>
        {STEP_KINDS.map((kind) => (
          <button
            key={kind.kind}
            type="button"
            onClick={() => onAddStep(kind.kind)}
            className="flex items-center gap-1.5 rounded-full border border-border bg-background px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <kind.icon className="h-3.5 w-3.5" />
            {kind.label}
          </button>
        ))}
      </div>
    </div>
  )
}

function getStepPreview(step: StepV2): string {
  switch (step.kind) {
    case 'say':     return step.prompt.text
    case 'collect': return step.prompt.text
    case 'decide':  return `Branch on ${step.source}${step.path ? ` → ${step.path}` : ''}`
    case 'do': {
      const op = step.operation
      if (op.kind === 'tool')     return `tool: ${op.tool_ref || '—'}`
      return `code (${(op as { language: string }).language})`
    }
    case 'ai':      return step.prompt.system
    case 'handoff': return `→ ${step.target.type}: ${step.target.value || '—'}`
    case 'end':     return step.closing_text ?? ''
  }
}

function getWhenLabel(when: OutcomeWhen): string | null {
  switch (when.kind) {
    case 'default':            return null
    case 'variable':           return `@${when.variable} ${when.operator}${when.value !== undefined ? ` ${String(when.value)}` : ''}`
    case 'event':              return `on: ${when.event}`
    case 'result':             return `result: ${when.source}${when.path ? when.path : ''}`
    case 'attempts_exhausted': return 'retries exhausted'
  }
}

function resolveNextLabel(
  next: OutcomeNext | undefined,
  allSteps: Array<{ stepId: string; stepTitle: string; stepIndex: number }>,
  allScenarios: ScenarioV2[],
): string | null {
  if (!next) return null
  if (next.end) return 'End'
  if (next.step_id) {
    const s = allSteps.find((s) => s.stepId === next.step_id)
    return s ? `${s.stepIndex + 1}. ${s.stepTitle}` : null
  }
  if (next.scenario_id) return allScenarios.find((s) => s.id === next.scenario_id)?.name ?? null
  return null
}

const ProceedTo: React.FC<{
  step: StepV2
  allSteps: Array<{ scenarioId: string; scenarioName: string; stepId: string; stepTitle: string; stepKind: StepV2['kind']; stepIndex: number }>
  allScenarios: ScenarioV2[]
  onUpdate: (step: StepV2) => void
}> = ({ step, allSteps, allScenarios, onUpdate }) => {
  const defaultOutcome = step.outcomes.find((o) => o.when.kind === 'default')
  const next = defaultOutcome?.next

  let currentValue = 'sequential'
  if (next?.end) currentValue = 'end'
  else if (next?.step_id) currentValue = `step:${next.step_id}`
  else if (next?.scenario_id) currentValue = `scenario:${next.scenario_id}`

  const handleChange = (value: string) => {
    const nonDefaultOutcomes = step.outcomes.filter((o) => o.when.kind !== 'default')
    const existingId = defaultOutcome?.id ?? uuidv4()

    if (value === 'sequential') {
      onUpdate({ ...step, outcomes: nonDefaultOutcomes })
      return
    }

    let nextValue: OutcomeNext
    if (value === 'end') nextValue = { end: true }
    else if (value.startsWith('step:')) nextValue = { step_id: value.slice(5) }
    else if (value.startsWith('scenario:')) nextValue = { scenario_id: value.slice(9) }
    else return

    const updated: OutcomeV2 = {
      id: existingId,
      label: 'Continue',
      when: { kind: 'default' },
      effects: defaultOutcome?.effects ?? [],
      next: nextValue,
    }
    onUpdate({ ...step, outcomes: [...nonDefaultOutcomes, updated] })
  }

  const otherSteps = allSteps.filter((s) => s.stepId !== step.id)

  return (
    <div className="flex items-center gap-3 rounded-xl border border-border bg-muted/20 px-4 py-2.5">
      <ArrowRight className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" />
      <span className="flex-shrink-0 text-xs font-medium text-muted-foreground">Proceed to</span>
      <select
        value={currentValue}
        onChange={(e) => handleChange(e.target.value)}
        className="flex-1 rounded-md border border-border bg-background px-2 py-1.5 text-sm text-foreground"
      >
        <option value="sequential">Next step (default)</option>
        {otherSteps.length > 0 && (
          <optgroup label="Jump to step">
            {otherSteps.map((s) => (
              <option key={s.stepId} value={`step:${s.stepId}`}>
                {s.scenarioName} → {s.stepIndex + 1}. {s.stepTitle}
              </option>
            ))}
          </optgroup>
        )}
        {allScenarios.length > 0 && (
          <optgroup label="Jump to scenario">
            {allScenarios.map((s) => (
              <option key={s.id} value={`scenario:${s.id}`}>
                {s.name}
              </option>
            ))}
          </optgroup>
        )}
        <option value="end">End conversation</option>
      </select>
    </div>
  )
}

interface StepCardProps {
  step: StepV2
  stepIndex: number
  allScenarios: ScenarioV2[]
  allSteps: Array<{ scenarioId: string; scenarioName: string; stepId: string; stepTitle: string; stepKind: StepV2['kind']; stepIndex: number }>
  scenarioVariables: string[]
  externalToolCatalog: ExternalToolCatalogItem[]
  agentId?: string
  onUpdate: (step: StepV2) => void
  onDelete: () => void
  onAddOutcome: () => void
  onUpdateOutcome: (outcome: OutcomeV2) => void
  onDeleteOutcome: (outcomeId: string) => void
}

const StepCard: React.FC<StepCardProps> = ({
  step,
  stepIndex,
  allScenarios,
  allSteps,
  scenarioVariables,
  externalToolCatalog,
  agentId,
  onUpdate,
  onDelete,
  onAddOutcome,
  onUpdateOutcome,
  onDeleteOutcome,
}) => {
  const meta = KIND_META[step.kind]
  const Icon = meta.icon
  const isTerminal = step.kind === 'handoff' || step.kind === 'end'
  // Auto-open in edit mode when the step title is still the default kind label (i.e. freshly created)
  const [isEditing, setIsEditing] = useState(() => step.title === meta.label)
  const preview = getStepPreview(step)
  const isTextStep = step.kind === 'say' || step.kind === 'collect' || step.kind === 'ai'

  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-card text-card-foreground shadow-sm">

      {/* ── Header — always visible ── */}
      <div
        className="flex cursor-pointer items-center gap-3 px-5 py-3.5 hover:bg-muted/40"
        onClick={() => !isEditing && setIsEditing(true)}
      >
        <span className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md bg-muted text-xs font-medium text-muted-foreground">
          {stepIndex + 1}
        </span>
        <Icon className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
        {isEditing ? (
          <Input
            value={step.title}
            onChange={(event) => onUpdate({ ...step, title: event.target.value })}
            onClick={(e) => e.stopPropagation()}
            className="h-7 flex-1 border-0 bg-transparent px-1 text-sm font-medium text-muted-foreground shadow-none focus-visible:ring-0"
            placeholder="Step title"
          />
        ) : (
          <span className="flex-1 truncate text-sm font-medium text-muted-foreground">{step.title}</span>
        )}
        {isEditing ? (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); setIsEditing(false) }}
            className="flex-shrink-0 rounded-md px-2 py-1 text-[11px] font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            Done
          </button>
        ) : (
          <span className="flex-shrink-0 text-[10px] text-muted-foreground/50">click to edit</span>
        )}
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onDelete() }}
          className="flex-shrink-0 text-muted-foreground/40 hover:text-rose-500"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* ── Read mode — compact document view ── */}
      {!isEditing && (
        <div
          className="cursor-pointer border-t border-border/50 px-5 pb-4 pt-3"
          onClick={() => setIsEditing(true)}
        >
          {preview && (
            <p className={cn(
              'text-sm leading-relaxed',
              isTextStep ? 'italic text-muted-foreground' : 'font-mono text-xs text-muted-foreground/80',
            )}>
              {preview.length > 180 ? `${preview.slice(0, 180)}…` : preview || <span className="not-italic text-muted-foreground/40">(empty)</span>}
            </p>
          )}

          {(() => {
            const defaultOutcome = step.outcomes.find((o) => o.when.kind === 'default')
            const conditionalOutcomes = step.outcomes.filter((o) => o.when.kind !== 'default')
            const proceedLabel = defaultOutcome?.next
              ? resolveNextLabel(defaultOutcome.next, allSteps, allScenarios) ?? 'End'
              : null

            return (
              <div className="mt-3 space-y-1.5">
                {/* Proceed-to line */}
                {!isTerminal && (
                  <div className="flex items-center gap-1.5 text-xs">
                    <ArrowRight className="h-3 w-3 flex-shrink-0 text-muted-foreground/40" />
                    <span className="text-muted-foreground/60">Proceed to</span>
                    <span className="font-medium text-foreground">
                      {proceedLabel ?? 'Next step'}
                    </span>
                  </div>
                )}
                {/* Conditional outcomes */}
                {conditionalOutcomes.map((outcome) => {
                  const whenLabel = getWhenLabel(outcome.when)
                  const destination = resolveNextLabel(outcome.next, allSteps, allScenarios)
                  return (
                    <div key={outcome.id} className="flex flex-wrap items-center gap-1.5 text-xs">
                      <GitBranch className="h-3 w-3 flex-shrink-0 text-amber-500/60" />
                      <span className="text-muted-foreground">{outcome.label}</span>
                      {whenLabel && (
                        <span className="rounded bg-amber-500/15 px-1.5 py-0.5 font-mono text-[10px] text-amber-600 dark:text-amber-300">
                          {whenLabel}
                        </span>
                      )}
                      {destination && (
                        <>
                          <span className="text-muted-foreground/40">→</span>
                          <span className="flex items-center gap-1 font-medium text-foreground">
                            <span className="h-1.5 w-1.5 rounded-full border border-border" />
                            {destination}
                          </span>
                        </>
                      )}
                    </div>
                  )
                })}
              </div>
            )
          })()}
        </div>
      )}

      {/* ── Edit mode — full form ── */}
      {isEditing && (
        <div className="space-y-4 border-t border-border px-5 py-4">
          <StepBody
            step={step}
            onUpdate={onUpdate}
            scenarioVariables={scenarioVariables}
            externalToolCatalog={externalToolCatalog}
            agentId={agentId}
          />

          {(() => {
            const conditionalOutcomes = step.outcomes.filter((o) => o.when.kind !== 'default')
            return conditionalOutcomes.length > 0 ? (
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <GitBranch className="h-3.5 w-3.5 text-amber-500" />
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Conditional branches</p>
                </div>
                {conditionalOutcomes.map((outcome, index) => (
                  <OutcomeRow
                    key={outcome.id}
                    outcome={outcome}
                    outcomeIndex={index}
                    allScenarios={allScenarios}
                    allSteps={allSteps}
                    onUpdate={onUpdateOutcome}
                    onDelete={() => onDeleteOutcome(outcome.id)}
                  />
                ))}
              </div>
            ) : null
          })()}

          {!isTerminal && (
            <>
              {step.kind !== 'say' && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 px-0 text-xs text-muted-foreground hover:bg-transparent hover:text-foreground"
                  onClick={onAddOutcome}
                >
                  <Plus className="mr-1 h-3 w-3" />
                  Add conditional branch
                </Button>
              )}

              <ProceedTo
                step={step}
                allSteps={allSteps}
                allScenarios={allScenarios}
                onUpdate={onUpdate}
              />
            </>
          )}

          {isTerminal && (
            <p className="rounded-xl border border-border bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
              {step.kind === 'handoff'
                ? 'Hands the customer to a person, queue, or phone number.'
                : 'Closes the journey and ends the conversation.'}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

const StepBody: React.FC<{
  step: StepV2
  onUpdate: (step: StepV2) => void
  scenarioVariables: string[]
  externalToolCatalog: ExternalToolCatalogItem[]
  agentId?: string
}> = ({ step, onUpdate, scenarioVariables, externalToolCatalog, agentId }) => {
  if (step.kind === 'say') {
    return (
      <PromptEditor
        label="Say"
        description="Authored copy delivered to the customer."
        value={step.prompt.text}
        placeholder="Welcome to Ruhu Bank. How can I help you today?"
        onChange={(text) => onUpdate({ ...step, prompt: { ...step.prompt, text } })}
      />
    )
  }

  if (step.kind === 'collect') {
    const capture = step.capture ?? { slot_names: [], entity_hints: [] }
    const retryPolicy = step.retry_policy ?? { max_attempts: 3 }
    return (
      <div className="space-y-3">
        <PromptEditor
          label="Collect"
          description="Ask the customer for input and define retry or no-input behavior."
          value={step.prompt.text}
          placeholder="Please share the account number you want me to look up."
          onChange={(text) => onUpdate({ ...step, prompt: { ...step.prompt, text } })}
        />
        <div className="grid gap-3 md:grid-cols-2">
          <FieldShell label="Capture slots">
            <Input
              value={capture.slot_names.join(', ')}
              onChange={(event) => onUpdate({
                ...step,
                capture: { ...capture, slot_names: splitCsv(event.target.value) },
              })}
              placeholder="account_number, phone_number"
              className="h-9 border-border bg-background text-sm text-foreground placeholder:text-muted-foreground/50"
            />
          </FieldShell>
          <FieldShell label="Entity hints">
            <Input
              value={capture.entity_hints.join(', ')}
              onChange={(event) => onUpdate({
                ...step,
                capture: { ...capture, entity_hints: splitCsv(event.target.value) },
              })}
              placeholder="account, msisdn, phone"
              className="h-9 border-border bg-background text-sm text-foreground placeholder:text-muted-foreground/50"
            />
          </FieldShell>
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          <FieldShell label="Max turns">
            <Input
              type="number"
              min={1}
              value={capture.max_turns ?? ''}
              onChange={(event) => onUpdate({
                ...step,
                capture: {
                  ...capture,
                  max_turns: event.target.value ? Number(event.target.value) : undefined,
                },
              })}
              className="h-9 border-border bg-background text-sm text-foreground"
            />
          </FieldShell>
          <FieldShell label="No-input timeout (s)">
            <Input
              type="number"
              min={1}
              value={capture.no_input_timeout_seconds ?? ''}
              onChange={(event) => onUpdate({
                ...step,
                capture: {
                  ...capture,
                  no_input_timeout_seconds: event.target.value ? Number(event.target.value) : undefined,
                },
              })}
              className="h-9 border-border bg-background text-sm text-foreground"
            />
          </FieldShell>
          <FieldShell label="Retry attempts">
            <Input
              type="number"
              min={1}
              value={retryPolicy.max_attempts}
              onChange={(event) => onUpdate({
                ...step,
                retry_policy: {
                  ...retryPolicy,
                  max_attempts: Number(event.target.value) || 1,
                },
              })}
              className="h-9 border-border bg-background text-sm text-foreground"
            />
          </FieldShell>
        </div>
        <FieldShell label="Reprompt text">
          <Input
            value={retryPolicy.reprompt_text ?? ''}
            onChange={(event) => onUpdate({
              ...step,
              retry_policy: {
                ...retryPolicy,
                reprompt_text: event.target.value || undefined,
              },
            })}
            placeholder="I didn’t catch that. Please say the account number again."
            className="h-9 border-border bg-background text-sm text-foreground placeholder:text-muted-foreground/50"
          />
        </FieldShell>
      </div>
    )
  }

  if (step.kind === 'decide') {
    return (
      <div className="grid gap-3 md:grid-cols-[0.8fr_1.2fr]">
        <FieldShell label="Decision source">
          <select
            value={step.source}
            onChange={(event) => onUpdate({ ...step, source: event.target.value as DecideStep['source'] })}
            className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground"
          >
            <option value="variable">Variable</option>
            <option value="event">Event</option>
            <option value="result">Result</option>
          </select>
        </FieldShell>
        <FieldShell label="Path (optional)">
          <Input
            value={step.path ?? ''}
            onChange={(event) => onUpdate({ ...step, path: event.target.value || undefined })}
            placeholder="$.data.status"
            className="h-9 border-border bg-background text-sm text-foreground placeholder:text-muted-foreground/50"
          />
        </FieldShell>
      </div>
    )
  }

  if (step.kind === 'do') {
    return (
      <DoStepBody
        step={step}
        onUpdate={onUpdate}
        scenarioVariables={scenarioVariables}
        externalToolCatalog={externalToolCatalog}
        agentId={agentId}
      />
    )
  }

  if (step.kind === 'ai') {
    return (
      <div className="space-y-3">
        <PromptEditor
          label="AI system prompt"
          description="Use the model for reasoning or dynamic response generation."
          value={step.prompt.system}
          placeholder="Classify whether the customer has completed identity verification."
          onChange={(text) => onUpdate({ ...step, prompt: { ...step.prompt, system: text } })}
        />
        <div className="grid gap-3 md:grid-cols-2">
          <FieldShell label="User template">
            <Input
              value={step.prompt.user_template ?? ''}
              onChange={(event) => onUpdate({
                ...step,
                prompt: { ...step.prompt, user_template: event.target.value || undefined },
              })}
              placeholder="Customer said: ${user_utterance}"
              className="h-9 border-border bg-background text-sm text-foreground placeholder:text-muted-foreground/50"
            />
          </FieldShell>
          <FieldShell label="Output mode">
            <select
              value={step.prompt.output_mode}
              onChange={(event) => onUpdate({
                ...step,
                prompt: { ...step.prompt, output_mode: event.target.value as AiStep['prompt']['output_mode'] },
              })}
              className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground"
            >
              <option value="text">Text</option>
              <option value="structured">Structured</option>
            </select>
          </FieldShell>
        </div>
        {step.prompt.output_mode === 'structured' && (
          <FieldShell label="Output schema (JSON)">
            <JsonField
              value={step.prompt.output_schema ?? {}}
              onChange={(output_schema) => onUpdate({
                ...step,
                prompt: { ...step.prompt, output_schema },
              })}
              placeholder='{"type":"object","properties":{"intent":{"type":"string"}}}'
            />
          </FieldShell>
        )}
      </div>
    )
  }

  if (step.kind === 'handoff') {
    return (
      <div className="rounded-2xl border border-rose-400/20 bg-rose-500/10 p-4">
        <div className="mb-3 flex items-center gap-2">
          <PhoneForwarded className="h-4 w-4 text-rose-300" />
          <p className="text-sm font-semibold text-foreground">Explicit handoff</p>
        </div>
        <div className="grid gap-3 md:grid-cols-[0.8fr_1.2fr]">
          <FieldShell label="Target type">
            <select
              value={step.target.type}
              onChange={(event) => onUpdate({
                ...step,
                target: { ...step.target, type: event.target.value as HandoffStep['target']['type'] },
              })}
              className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground"
            >
              <option value="queue">Queue</option>
              <option value="agent">Agent</option>
              <option value="phone_number">Phone number</option>
            </select>
          </FieldShell>
          <FieldShell label="Target value">
            <Input
              value={step.target.value}
              onChange={(event) => onUpdate({
                ...step,
                target: { ...step.target, value: event.target.value },
              })}
              placeholder="priority_support_queue"
              className="h-9 border-border bg-background text-sm text-foreground placeholder:text-muted-foreground/50"
            />
          </FieldShell>
        </div>
        <FieldShell label="Preamble" className="mt-3">
          <Input
            value={step.preamble ?? ''}
            onChange={(event) => onUpdate({ ...step, preamble: event.target.value || undefined })}
            placeholder="Let me connect you to a specialist."
            className="h-9 border-border bg-background text-sm text-foreground placeholder:text-muted-foreground/50"
          />
        </FieldShell>
      </div>
    )
  }

  if (step.kind === 'end') {
    return (
      <div className="grid gap-3 md:grid-cols-[1.3fr_0.7fr]">
        <FieldShell label="Closing text">
          <Input
            value={step.closing_text ?? ''}
            onChange={(event) => onUpdate({ ...step, closing_text: event.target.value || undefined })}
            placeholder="Thanks for calling. We’ve logged your request."
            className="h-9 border-border bg-background text-sm text-foreground placeholder:text-muted-foreground/50"
          />
        </FieldShell>
        <FieldShell label="Disposition">
          <select
            value={step.disposition ?? ''}
            onChange={(event) => onUpdate({ ...step, disposition: event.target.value || undefined })}
            className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground"
          >
            <option value="">None</option>
            <option value="resolved">Resolved</option>
            <option value="escalated">Escalated</option>
            <option value="abandoned">Abandoned</option>
          </select>
        </FieldShell>
      </div>
    )
  }

  return null
}

const PromptEditor: React.FC<{
  label: string
  description: string
  value: string
  placeholder: string
  onChange: (value: string) => void
}> = ({ label, description, value, placeholder, onChange }) => (
  <div>
    <div className="mb-2 flex items-center gap-2">
      <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className="text-xs text-muted-foreground/60">{description}</span>
    </div>
    <textarea
      value={value}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      rows={3}
      className="w-full resize-none rounded-2xl border border-border bg-background px-4 py-3 text-sm text-foreground placeholder:text-muted-foreground/50 focus:border-ring focus:outline-none"
    />
  </div>
)

const FieldShell: React.FC<{
  label: string
  className?: string
  children: React.ReactNode
}> = ({ label, className, children }) => (
  <div className={className}>
    <label className="mb-1 block text-xs font-medium text-muted-foreground">{label}</label>
    {children}
  </div>
)

const KeyValueEditor: React.FC<{
  label: string
  value: Record<string, unknown>
  onChange: (value: Record<string, unknown>) => void
}> = ({ label, value, onChange }) => {
  const pairs = Object.entries(value)

  const updatePairKey = (oldKey: string, newKey: string) => {
    const updated: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(value)) {
      updated[k === oldKey ? newKey : k] = v
    }
    onChange(updated)
  }

  const updatePairValue = (key: string, val: string) => {
    onChange({ ...value, [key]: val })
  }

  const removePair = (key: string) => {
     
    const { [key]: _removed, ...rest } = value
    onChange(rest)
  }

  const addPair = () => {
    let name = 'param'
    let suffix = 1
    while (name in value) { name = `param_${suffix++}` }
    onChange({ ...value, [name]: '' })
  }

  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <label className="text-xs font-medium text-muted-foreground">{label}</label>
        <button type="button" onClick={addPair} className="text-[11px] text-muted-foreground hover:text-foreground">
          + Add
        </button>
      </div>
      {pairs.length === 0 ? (
        <p className="text-[11px] text-muted-foreground/50">No parameters. Click + Add to define key/value pairs.</p>
      ) : (
        <div className="space-y-2">
          {pairs.map(([key, val]) => (
            <div key={key} className="flex items-center gap-2">
              <Input
                value={key}
                onChange={(event) => updatePairKey(key, event.target.value)}
                placeholder="key"
                className="h-8 border-border bg-background text-xs text-foreground placeholder:text-muted-foreground/50"
              />
              <Input
                value={String(val ?? '')}
                onChange={(event) => updatePairValue(key, event.target.value)}
                placeholder="value"
                className="h-8 border-border bg-background text-xs text-foreground placeholder:text-muted-foreground/50"
              />
              <button type="button" onClick={() => removePair(key)} className="flex-shrink-0 text-muted-foreground/50 hover:text-rose-500">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const DoStepBody: React.FC<{
  step: DoStep
  onUpdate: (step: StepV2) => void
  scenarioVariables: string[]
  externalToolCatalog: ExternalToolCatalogItem[]
  agentId?: string
}> = ({ step, onUpdate, scenarioVariables, externalToolCatalog, agentId }) => {
  const operation = step.operation
  const selectedToolMissing = operation.kind === 'tool'
    && !!operation.tool_ref
    && !externalToolCatalog.some((tool) => tool.ref === operation.tool_ref)
  const switchKind = (kind: DoOperation['kind']) => {
    const nextByKind: Record<DoOperation['kind'], DoOperation> = {
      tool: { kind: 'tool', tool_ref: '', input: {} },
      code: { kind: 'code', language: 'python', code: '', callable_tool_refs: [], callable_functions_code: '', input_schema: null },
    }
    onUpdate({ ...step, operation: nextByKind[kind] })
  }

  return (
    <div className="space-y-3">
      <FieldShell label="Operation type">
        <select
          value={operation.kind}
          onChange={(event) => switchKind(event.target.value as DoOperation['kind'])}
          className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground"
        >
          <option value="tool">Tool</option>
          <option value="code">Code</option>
        </select>
      </FieldShell>
      {operation.kind === 'tool' && (
        <div className="space-y-3">
          <FieldShell label="Tool">
            <select
              value={operation.tool_ref}
              onChange={(event) => onUpdate({ ...step, operation: { ...operation, tool_ref: event.target.value } })}
              className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground"
            >
              <option value="">Select tool</option>
              {selectedToolMissing && (
                <option value={operation.tool_ref}>
                  {operation.tool_ref}
                </option>
              )}
              {externalToolCatalog.map((tool) => (
                <option key={tool.ref} value={tool.ref}>
                  {tool.display_name} ({tool.provider})
                </option>
              ))}
            </select>
          </FieldShell>
          {operation.tool_ref && (
            <p className="text-xs text-muted-foreground">
              {externalToolCatalog.find((tool) => tool.ref === operation.tool_ref)?.description ?? 'Using stored tool reference.'}
            </p>
          )}
          <FieldShell label="Tool ref">
            <Input
              value={operation.tool_ref}
              onChange={(event) => onUpdate({ ...step, operation: { ...operation, tool_ref: event.target.value } })}
              className="h-9 border-border bg-background font-mono text-xs text-foreground"
            />
          </FieldShell>
          <KeyValueEditor
            label="Input parameters"
            value={operation.input ?? {}}
            onChange={(input) => onUpdate({ ...step, operation: { ...operation, input } })}
          />
        </div>
      )}
      {operation.kind === 'code' && (
        <CodeStepEditor
          code={operation.code}
          language={operation.language}
          callableToolRefs={operation.callable_tool_refs || []}
          callableFunctionsCode={operation.callable_functions_code || ''}
          inputSchema={operation.input_schema}
          agentId={agentId}
          scenarioVariables={scenarioVariables}
          onChange={(updates) => onUpdate({
            ...step,
            operation: { ...operation, ...updates } as CodeOperation,
          })}
        />
      )}
    </div>
  )
}

interface OutcomeRowProps {
  outcome: OutcomeV2
  outcomeIndex: number
  allScenarios: ScenarioV2[]
  allSteps: Array<{ scenarioId: string; scenarioName: string; stepId: string; stepTitle: string; stepKind: StepV2['kind']; stepIndex: number }>
  onUpdate: (outcome: OutcomeV2) => void
  onDelete: () => void
}

const needsValue = (operator: ConditionOperator) => (
  operator === 'equals'
  || operator === 'not_equals'
  || operator === 'contains'
  || operator === 'greater_than'
  || operator === 'less_than'
)

const OutcomeRow: React.FC<OutcomeRowProps> = ({
  outcome,
  outcomeIndex,
  allScenarios,
  allSteps,
  onUpdate,
  onDelete,
}) => {
  const when = outcome.when
  const next = outcome.next
  const updateWhen = (nextWhen: OutcomeWhen) => onUpdate({ ...outcome, when: nextWhen })
  const updateNext = (nextValue: OutcomeNext | undefined) => onUpdate({ ...outcome, next: nextValue })
  const updateEffects = (effects: EffectV2[]) => onUpdate({ ...outcome, effects })
  const nextType = next?.end ? 'end' : next?.step_id ? 'step' : next?.scenario_id ? 'scenario' : 'sequential'

  return (
    <div className="rounded-2xl border border-border bg-muted/30 p-4">
      <div className="flex items-center gap-2">
        <div className="flex h-7 w-7 items-center justify-center rounded-full bg-muted text-xs font-semibold text-muted-foreground">
          {outcomeIndex + 1}
        </div>
        <Input
          value={outcome.label}
          onChange={(event) => onUpdate({ ...outcome, label: event.target.value })}
          placeholder="Outcome label"
          className="h-9 border-border bg-background text-sm text-foreground placeholder:text-muted-foreground/50"
        />
        <select
          value={when.kind}
          onChange={(event) => {
            const kind = event.target.value as OutcomeWhen['kind']
            if (kind === 'default') updateWhen({ kind: 'default' })
            if (kind === 'variable') updateWhen({ kind: 'variable', variable: '', operator: 'is_set' })
            if (kind === 'event') updateWhen({ kind: 'event', event: 'user_replied' })
            if (kind === 'result') updateWhen({ kind: 'result', source: '', operator: 'is_set' })
            if (kind === 'attempts_exhausted') updateWhen({ kind: 'attempts_exhausted' })
          }}
          className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground"
        >
          {OUTCOME_WHEN_KINDS.map((kind) => (
            <option key={kind.value} value={kind.value}>{kind.label}</option>
          ))}
        </select>
        <button type="button" onClick={onDelete} className="text-muted-foreground/50 hover:text-rose-500">
          <Trash2 className="h-4 w-4" />
        </button>
      </div>

      <div className="mt-3 rounded-2xl border border-border bg-background/60 p-3">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">When</p>
        <div className="mt-2 space-y-2">
          {when.kind === 'default' && (
            <div className="flex items-center gap-2">
              <TokenChip label="Default path" tone="slate" />
              <span className="text-xs text-muted-foreground">Used when no earlier outcome matches.</span>
            </div>
          )}

          {when.kind === 'variable' && (
            <div className="grid gap-2 md:grid-cols-[1.1fr_0.8fr_0.8fr]">
              <Input
                value={when.variable}
                onChange={(event) => updateWhen({ ...when, variable: event.target.value })}
                placeholder="@customer_verified"
                className="h-9 border-border bg-background text-sm text-foreground placeholder:text-muted-foreground/50"
              />
              <select
                value={when.operator}
                onChange={(event) => updateWhen({ ...when, operator: event.target.value as ConditionOperator })}
                className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground"
              >
                {OPERATORS.map((operator) => (
                  <option key={operator.value} value={operator.value}>{operator.label}</option>
                ))}
              </select>
              {needsValue(when.operator) ? (
                <Input
                  value={String(when.value ?? '')}
                  onChange={(event) => updateWhen({ ...when, value: event.target.value })}
                  placeholder="value"
                  className="h-9 border-border bg-background text-sm text-foreground placeholder:text-muted-foreground/50"
                />
              ) : (
                <div className="flex items-center">
                  <TokenChip label={`@${when.variable || 'variable'}`} tone="amber" />
                </div>
              )}
            </div>
          )}

          {when.kind === 'event' && (
            <div className="flex items-center gap-2">
              <select
                value={when.event}
                onChange={(event) => updateWhen({ ...when, event: event.target.value as EventWhen['event'] })}
                className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground"
              >
                {EVENT_TYPES.map((eventType) => (
                  <option key={eventType.value} value={eventType.value}>{eventType.label}</option>
                ))}
              </select>
              <TokenChip label={`event:${when.event}`} tone="blue" />
            </div>
          )}

          {when.kind === 'result' && (
            <div className="grid gap-2 md:grid-cols-[1fr_0.9fr_0.8fr_0.8fr]">
              <Input
                value={when.source}
                onChange={(event) => updateWhen({ ...when, source: event.target.value })}
                placeholder="verification_step"
                className="h-9 border-border bg-background text-sm text-foreground placeholder:text-muted-foreground/50"
              />
              <Input
                value={when.path ?? ''}
                onChange={(event) => updateWhen({ ...when, path: event.target.value || undefined })}
                placeholder="$.status"
                className="h-9 border-border bg-background text-sm text-foreground placeholder:text-muted-foreground/50"
              />
              <select
                value={when.operator}
                onChange={(event) => updateWhen({ ...when, operator: event.target.value as ConditionOperator })}
                className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground"
              >
                {OPERATORS.map((operator) => (
                  <option key={operator.value} value={operator.value}>{operator.label}</option>
                ))}
              </select>
              {needsValue(when.operator) ? (
                <Input
                  value={String(when.value ?? '')}
                  onChange={(event) => updateWhen({ ...when, value: event.target.value })}
                  placeholder="value"
                  className="h-9 border-border bg-background text-sm text-foreground placeholder:text-muted-foreground/50"
                />
              ) : (
                <div className="flex items-center">
                  <TokenChip label={when.source || 'result'} tone="purple" />
                </div>
              )}
            </div>
          )}

          {when.kind === 'attempts_exhausted' && (
            <div className="flex items-center gap-2">
              <TokenChip label="attempts_exhausted" tone="rose" />
              <span className="text-xs text-muted-foreground">Used after the collect step reaches its retry limit.</span>
            </div>
          )}
        </div>
      </div>

      <div className="mt-3 rounded-2xl border border-border bg-background/60 p-3">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Then</p>
        <div className="mt-2 grid gap-2 md:grid-cols-[0.9fr_1.1fr]">
          <select
            value={nextType}
            onChange={(event) => {
              const value = event.target.value
              if (value === 'sequential') updateNext(undefined)
              if (value === 'scenario') updateNext({ scenario_id: '' })
              if (value === 'step') updateNext({ step_id: '' })
              if (value === 'end') updateNext({ end: true })
            }}
            className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground"
          >
            <option value="sequential">Next authored step</option>
            <option value="scenario">Jump to scenario</option>
            <option value="step">Jump to step</option>
            <option value="end">End conversation</option>
          </select>

          {nextType === 'scenario' && (
            <select
              value={next?.scenario_id ?? ''}
              onChange={(event) => updateNext({ scenario_id: event.target.value })}
              className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground"
            >
              <option value="">Select scenario</option>
              {allScenarios.map((scenario) => (
                <option key={scenario.id} value={scenario.id}>{scenario.name}</option>
              ))}
            </select>
          )}

          {nextType === 'step' && (
            <select
              value={next?.step_id ?? ''}
              onChange={(event) => updateNext({ step_id: event.target.value })}
              className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground"
            >
              <option value="">Select step</option>
              {allSteps.map((step) => (
                <option key={step.stepId} value={step.stepId}>
                  {step.scenarioName} → {step.stepIndex + 1}. {step.stepTitle}
                </option>
              ))}
            </select>
          )}

          {nextType === 'sequential' && (
            <div className="flex items-center gap-2 rounded-md border border-border bg-muted/30 px-3 text-sm text-muted-foreground">
              <ArrowRight className="h-4 w-4" />
              Fall through to the next authored step in this scenario.
            </div>
          )}

          {nextType === 'end' && (
            <div className="flex items-center gap-2 rounded-md border border-border bg-muted/30 px-3 text-sm text-muted-foreground">
              <XCircle className="h-4 w-4" />
              End the conversation after this outcome.
            </div>
          )}
        </div>
      </div>

      <EffectEditor effects={outcome.effects} onChange={updateEffects} />
    </div>
  )
}

// JsonField — controlled text input for JSON objects with inline error feedback.
// Tracks the raw typed text locally so the cursor never jumps; only calls onChange
// when the text parses to a valid object.
const JsonField: React.FC<{
  value: Record<string, unknown>
  onChange: (value: Record<string, unknown>) => void
  placeholder: string
}> = ({ value, onChange, placeholder }) => {
  const [text, setText] = useState(() => JSON.stringify(value ?? {}))
  const [hasError, setHasError] = useState(false)
  // Sync from outside only when the object reference changes (e.g. effect reset)
  const prevRef = useRef(value)
  useEffect(() => {
    if (prevRef.current !== value) {
      prevRef.current = value
      setText(JSON.stringify(value ?? {}))
      setHasError(false)
    }
  }, [value])

  const handleChange = (raw: string) => {
    setText(raw)
    const parsed = safeParseJson(raw)
    if (parsed !== null) {
      setHasError(false)
      prevRef.current = parsed
      onChange(parsed)
    } else {
      setHasError(true)
    }
  }

  return (
    <div>
      <Input
        value={text}
        onChange={(event) => handleChange(event.target.value)}
        placeholder={placeholder}
        className={cn(
          'h-9 text-sm text-foreground placeholder:text-muted-foreground/50',
          hasError
            ? 'border-rose-500/60 bg-rose-500/10 focus-visible:ring-rose-500/30'
            : 'border-border bg-background',
        )}
      />
      {hasError && (
        <p className="mt-1 text-[11px] text-rose-400">Invalid JSON — changes not saved until fixed.</p>
      )}
    </div>
  )
}

const EffectEditor: React.FC<{
  effects: EffectV2[]
  onChange: (effects: EffectV2[]) => void
}> = ({ effects, onChange }) => {
  const updateEffect = (index: number, effect: EffectV2) => {
    onChange(effects.map((existing, currentIndex) => currentIndex === index ? effect : existing))
  }

  return (
    <div className="mt-3 rounded-2xl border border-border bg-background/60 p-3">
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Effects</p>
        <div className="flex items-center gap-2">
          <select
            defaultValue=""
            onChange={(event) => {
              const kind = event.target.value as EffectV2['kind'] | ''
              if (!kind) return
              onChange([...effects, makeEffect(kind)])
              event.currentTarget.value = ''
            }}
            className="h-8 rounded-md border border-border bg-background px-2 text-xs text-foreground"
          >
            <option value="">Add effect</option>
            <option value="mark">Mark</option>
            <option value="trigger">Trigger</option>
            <option value="set_variable">Set variable</option>
            <option value="create_ticket">Create ticket</option>
            <option value="transfer">Transfer</option>
          </select>
        </div>
      </div>
      {effects.length === 0 ? (
        <p className="mt-2 text-xs text-muted-foreground">No side effects. This outcome only controls routing.</p>
      ) : (
        <div className="mt-3 space-y-3">
          {effects.map((effect, index) => (
            <div key={`${effect.kind}-${index}`} className="rounded-xl border border-border bg-muted/20 p-3">
              <div className="flex items-center gap-2">
                <EffectChip effect={effect} />
                <button
                  type="button"
                  onClick={() => onChange(effects.filter((_, currentIndex) => currentIndex !== index))}
                  className="ml-auto text-muted-foreground/50 hover:text-rose-500"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
              <div className="mt-3">
                {effect.kind === 'mark' && (
                  <Input
                    value={effect.name}
                    onChange={(event) => updateEffect(index, { ...effect, name: event.target.value })}
                    placeholder="info_confirmed"
                    className="h-9 border-border bg-background text-sm text-foreground placeholder:text-muted-foreground/50"
                  />
                )}
                {effect.kind === 'trigger' && (
                  <div className="grid gap-2 md:grid-cols-[0.9fr_1.1fr]">
                    <Input
                      value={effect.name}
                      onChange={(event) => updateEffect(index, { ...effect, name: event.target.value })}
                      placeholder="compliance_flag"
                      className="h-9 border-border bg-background text-sm text-foreground placeholder:text-muted-foreground/50"
                    />
                    <JsonField
                      value={effect.payload ?? {}}
                      onChange={(payload) => updateEffect(index, { ...effect, payload })}
                      placeholder='{"priority":"high"}'
                    />
                  </div>
                )}
                {effect.kind === 'set_variable' && (
                  <div className="grid gap-2 md:grid-cols-[0.9fr_1.1fr]">
                    <Input
                      value={effect.name}
                      onChange={(event) => updateEffect(index, { ...effect, name: event.target.value })}
                      placeholder="customer_segment"
                      className="h-9 border-border bg-background text-sm text-foreground placeholder:text-muted-foreground/50"
                    />
                    <Input
                      value={String(effect.value ?? '')}
                      onChange={(event) => updateEffect(index, { ...effect, value: event.target.value })}
                      placeholder="premium"
                      className="h-9 border-border bg-background text-sm text-foreground placeholder:text-muted-foreground/50"
                    />
                  </div>
                )}
                {effect.kind === 'create_ticket' && (
                  <div className="grid gap-2 md:grid-cols-[0.8fr_1.2fr]">
                    <Input
                      value={effect.queue ?? ''}
                      onChange={(event) => updateEffect(index, { ...effect, queue: event.target.value || undefined })}
                      placeholder="retentions_queue"
                      className="h-9 border-border bg-background text-sm text-foreground placeholder:text-muted-foreground/50"
                    />
                    <JsonField
                      value={effect.fields ?? {}}
                      onChange={(fields) => updateEffect(index, { ...effect, fields })}
                      placeholder='{"reason":"kyc_failed"}'
                    />
                  </div>
                )}
                {effect.kind === 'transfer' && (
                  <div className="grid gap-2 md:grid-cols-[0.8fr_1.2fr]">
                    <select
                      value={effect.target.type}
                      onChange={(event) => updateEffect(index, {
                        ...effect,
                        target: { ...effect.target, type: event.target.value as HandoffStep['target']['type'] },
                      })}
                      className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground"
                    >
                      <option value="queue">Queue</option>
                      <option value="agent">Agent</option>
                      <option value="phone_number">Phone number</option>
                    </select>
                    <Input
                      value={effect.target.value}
                      onChange={(event) => updateEffect(index, {
                        ...effect,
                        target: { ...effect.target, value: event.target.value },
                      })}
                      placeholder="priority_support_queue"
                      className="h-9 border-border bg-background text-sm text-foreground placeholder:text-muted-foreground/50"
                    />
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const EffectChip: React.FC<{ effect: EffectV2 }> = ({ effect }) => {
  if (effect.kind === 'mark') return <TokenChip label={`mark:${effect.name || 'flag'}`} tone="emerald" />
  if (effect.kind === 'trigger') return <TokenChip label={`trigger:${effect.name || 'event'}`} tone="blue" />
  if (effect.kind === 'set_variable') return <TokenChip label={`set @${effect.name || 'variable'}`} tone="amber" />
  if (effect.kind === 'create_ticket') return <TokenChip label={`ticket:${effect.queue || 'queue'}`} tone="purple" />
  return <TokenChip label={`transfer:${effect.target.value || effect.target.type}`} tone="rose" />
}

const TokenChip: React.FC<{
  label: string
  tone: 'amber' | 'blue' | 'emerald' | 'purple' | 'rose' | 'slate'
}> = ({ label, tone }) => {
  const toneClasses: Record<string, string> = {
    amber:   'bg-amber-500/15   text-amber-700   dark:text-amber-300',
    blue:    'bg-sky-500/15     text-sky-700     dark:text-sky-300',
    emerald: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
    purple:  'bg-fuchsia-500/15 text-fuchsia-700 dark:text-fuchsia-300',
    rose:    'bg-rose-500/15    text-rose-700    dark:text-rose-300',
    slate:   'bg-muted text-muted-foreground',
  }
  return (
    <span className={cn('inline-flex rounded-full px-2.5 py-1 text-[11px] font-medium', toneClasses[tone])}>
      {label}
    </span>
  )
}

function safeParseJson(value: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(value)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null
  } catch {
    return null
  }
}

export default ScenarioEditor
