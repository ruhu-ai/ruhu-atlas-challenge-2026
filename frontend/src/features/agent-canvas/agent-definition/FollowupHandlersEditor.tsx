/**
 * Agent-level editor for `AgentDefinition.followup_handlers`.
 *
 * Each handler routes a follow-up intent on an artifact type to a target
 * state.  Example: (artifact_type=booking, followup_intent=cancel) →
 * cancel_booking state.  Specs 20 / 21.
 *
 * Sits in the agent-settings panel when no state is selected.
 */
import { useMemo, useState } from 'react'
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
import { ChevronDown, ChevronUp, Plus, Trash2, AlertTriangle } from 'lucide-react'
import type {
  ArtifactFollowupHandler,
  FactDef,
  AgentDefinition,
  AgentDefinitionStep,
} from '@/types/agent-definition'
import { deriveStepKind } from './utils'

interface FollowupHandlersEditorProps {
  agentDefinition: AgentDefinition
  onChange: (handlers: ArtifactFollowupHandler[]) => void
}

function emptyHandler(): ArtifactFollowupHandler {
  return {
    artifact_type: '',
    followup_intent: '',
    target_step_id: '',
    fact_requirements: [],
  }
}

function normalizeHandler(handler: ArtifactFollowupHandler): ArtifactFollowupHandler {
  return {
    ...handler,
    artifact_type: handler.artifact_type ?? '',
    followup_intent: handler.followup_intent ?? '',
    target_step_id: handler.target_step_id ?? '',
    fact_requirements: handler.fact_requirements ?? [],
  }
}

export function FollowupHandlersEditor({ agentDefinition, onChange }: FollowupHandlersEditorProps) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const handlers = (agentDefinition.followup_handlers ?? []).map((handler) => normalizeHandler(handler))

  // Suggest artifact types already declared on action states.
  const knownArtifactTypes = useMemo(() => {
    const set = new Set<string>()
    for (const state of agentDefinition.steps) {
      if (state.artifact_type) set.add(state.artifact_type)
    }
    return [...set].sort()
  }, [agentDefinition.steps])

  const targetStateChoices: AgentDefinitionStep[] = useMemo(
    () => agentDefinition.steps.filter((s) => deriveStepKind(s, agentDefinition.start_step_id) !== 'entry'),
    [agentDefinition.steps, agentDefinition.start_step_id],
  )

  const factChoices: FactDef[] = agentDefinition.fact_schema ?? []

  const updateHandler = (index: number, patch: Partial<ArtifactFollowupHandler>) => {
    const next = handlers.map((h, i) => (i === index ? normalizeHandler({ ...h, ...patch }) : h))
    onChange(next)
  }

  const addHandler = () => {
    onChange([...handlers, emptyHandler()])
    setExpanded((prev) => new Set(prev).add(handlers.length))
  }

  const removeHandler = (index: number) => {
    onChange(handlers.filter((_, i) => i !== index))
    setExpanded((prev) => {
      const next = new Set<number>()
      for (const i of prev) {
        if (i < index) next.add(i)
        else if (i > index) next.add(i - 1)
      }
      return next
    })
  }

  const toggleExpanded = (index: number) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  const toggleFact = (index: number, factName: string) => {
    const existing = handlers[index]?.fact_requirements ?? []
    const current = new Map(existing.map((item) => [item.name, item]))
    if (current.has(factName)) current.delete(factName)
    else current.set(factName, { name: factName })
    updateHandler(index, { fact_requirements: [...current.values()] })
  }

  return (
    <section className="space-y-3 rounded-lg border border-white/10 bg-card/30 p-4">
      <div className="flex items-center justify-between">
        <div>
          <h4 className="text-sm font-medium">Artifact follow-ups</h4>
          <p className="text-xs text-muted-foreground">
            Route user intents on created artifacts (&quot;cancel it&quot;, &quot;reschedule&quot;) to the states that handle them.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={addHandler}>
          <Plus className="mr-1.5 h-3.5 w-3.5" />
          Add handler
        </Button>
      </div>

      {handlers.length === 0 && (
        <p className="text-xs text-muted-foreground">
          No follow-up handlers yet. Add one to let follow-up intents on artifacts (
          {knownArtifactTypes.length > 0
            ? knownArtifactTypes.map((t) => `"${t}"`).join(', ')
            : 'add an artifact_type to an action state first'}
          ) target the right state.
        </p>
      )}

      {handlers.length > 0 && (
        <div className="space-y-2">
          {handlers.map((handler, index) => {
            const warnings = getHandlerWarnings(handler, agentDefinition)
            const targetState = targetStateChoices.find((s) => s.id === handler.target_step_id)
            const isOpen = expanded.has(index)
            const summary =
              handler.artifact_type && handler.followup_intent
                ? `${handler.artifact_type} · ${handler.followup_intent}${targetState ? ` → ${targetState.name}` : ''}`
                : 'Untitled handler'
            return (
              <div
                key={index}
                className="rounded-md border border-white/10 bg-background"
              >
                <button
                  type="button"
                  onClick={() => toggleExpanded(index)}
                  className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left"
                >
                  <div className="flex min-w-0 flex-1 items-center gap-2">
                    <span className="truncate text-sm">{summary}</span>
                    {warnings.length > 0 && (
                      <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-400" />
                    )}
                  </div>
                  {isOpen ? (
                    <ChevronUp className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <ChevronDown className="h-4 w-4 text-muted-foreground" />
                  )}
                </button>

                {isOpen && (
                  <div className="space-y-3 border-t border-white/10 px-3 py-3">
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <div className="space-y-1">
                        <Label className="text-xs text-muted-foreground">Artifact type</Label>
                        <Input
                          list={`artifact-types-${index}`}
                          value={handler.artifact_type}
                          onChange={(e) =>
                            updateHandler(index, { artifact_type: e.target.value })
                          }
                          placeholder="e.g. booking"
                          className="h-8 text-sm"
                        />
                        <datalist id={`artifact-types-${index}`}>
                          {knownArtifactTypes.map((t) => (
                            <option key={t} value={t} />
                          ))}
                        </datalist>
                      </div>

                      <div className="space-y-1">
                        <Label className="text-xs text-muted-foreground">Follow-up intent</Label>
                        <Input
                          value={handler.followup_intent}
                          onChange={(e) =>
                            updateHandler(index, { followup_intent: e.target.value })
                          }
                          placeholder="e.g. cancel, reschedule"
                          className="h-8 text-sm"
                        />
                      </div>
                    </div>

                    <div className="space-y-1">
                      <Label className="text-xs text-muted-foreground">Target state</Label>
                      <Select
                        value={handler.target_step_id || ''}
                        onValueChange={(v) => updateHandler(index, { target_step_id: v })}
                      >
                        <SelectTrigger className="h-8 text-sm">
                          <SelectValue placeholder="Select state…" />
                        </SelectTrigger>
                        <SelectContent>
                          {targetStateChoices.map((s) => (
                            <SelectItem key={s.id} value={s.id}>
                              {s.name} ({deriveStepKind(s, agentDefinition.start_step_id)})
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>

                    <div className="space-y-1">
                      <Label className="text-xs text-muted-foreground">Required facts</Label>
                      {factChoices.length === 0 ? (
                        <p className="text-[11px] text-muted-foreground">
                          No facts declared on this agent. Add them to <code>fact_schema</code> first.
                        </p>
                      ) : (
                        <div className="flex flex-wrap gap-1.5">
                          {factChoices.map((fact) => {
                            const checked = (handler.fact_requirements ?? []).some((item) => item.name === fact.name)
                            return (
                              <button
                                key={fact.name}
                                type="button"
                                onClick={() => toggleFact(index, fact.name)}
                                className={`rounded-full border px-2 py-0.5 text-[11px] transition ${
                                  checked
                                    ? 'border-primary bg-primary/15 text-primary'
                                    : 'border-white/15 bg-card/50 text-muted-foreground hover:border-white/30'
                                }`}
                              >
                                {fact.name}
                              </button>
                            )
                          })}
                        </div>
                      )}
                      <p className="text-[10px] text-muted-foreground">
                        The handler only fires when all selected facts are already satisfied in the conversation.
                      </p>
                    </div>

                    {warnings.length > 0 && (
                      <ul className="space-y-1 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-300">
                        {warnings.map((w, i) => (
                          <li key={i} className="flex items-start gap-1.5">
                            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                            <span>{w}</span>
                          </li>
                        ))}
                      </ul>
                    )}

                    <div className="flex justify-end">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => removeHandler(index)}
                        className="text-rose-400 hover:text-rose-300"
                      >
                        <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                        Remove handler
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}

function getHandlerWarnings(
  handler: ArtifactFollowupHandler,
  agentDefinition: AgentDefinition,
): string[] {
  const warnings: string[] = []
  if (!handler.artifact_type.trim()) {
    warnings.push('Missing artifact type.')
  } else {
    const producers = agentDefinition.steps.filter((s) => s.artifact_type === handler.artifact_type)
    if (producers.length === 0) {
      warnings.push(
        `No action state produces artifact type "${handler.artifact_type}" — this handler can never fire.`,
      )
    }
  }
  if (!handler.followup_intent.trim()) {
    warnings.push('Missing follow-up intent.')
  }
  if (!handler.target_step_id.trim()) {
    warnings.push('Pick a target state.')
  } else if (!agentDefinition.steps.some((s) => s.id === handler.target_step_id)) {
    warnings.push(
      `Target state "${handler.target_step_id}" does not exist — the handler is broken.`,
    )
  }
  const factNames = new Set((agentDefinition.fact_schema ?? []).map((f) => f.name))
  for (const requirement of handler.fact_requirements ?? []) {
    if (!factNames.has(requirement.name)) {
      warnings.push(
        `Required fact "${requirement.name}" is not declared in the agent's fact schema.`,
      )
    }
  }
  return warnings
}
