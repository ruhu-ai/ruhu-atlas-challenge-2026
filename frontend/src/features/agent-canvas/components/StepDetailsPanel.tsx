import { Plus, Trash2 } from 'lucide-react'

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
import { Textarea } from '@/components/atoms/textarea'
import { useAgentDocument } from '@/features/agent-canvas/contexts/AgentDocumentContext'
import type {
  AgentGuardDef,
  AgentResponsePolicy,
  AgentScenario,
  AgentStep,
  AgentToolBinding,
} from '@/types/agent-document'

function csvToList(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

function listToCsv(values: string[] | undefined): string {
  return (values ?? []).join(', ')
}

function ensureResponsePolicy(policy: AgentResponsePolicy | undefined): AgentResponsePolicy {
  return {
    answer_directly_first: policy?.answer_directly_first ?? true,
    ask_clarifying_question_only_if_needed: policy?.ask_clarifying_question_only_if_needed ?? true,
    voice_style: policy?.voice_style ?? 'concise',
    direct_answer_prompt: policy?.direct_answer_prompt ?? '',
    render_with_llm: policy?.render_with_llm ?? true,
    deterministic_fallback_text: policy?.deterministic_fallback_text ?? '',
    response_max_sentences: policy?.response_max_sentences ?? null,
    include_recent_history: policy?.include_recent_history ?? true,
    include_known_facts: policy?.include_known_facts ?? true,
  }
}

function eventHintsToLines(eventHints: Record<string, string> | undefined): string {
  return Object.entries(eventHints ?? {})
    .map(([event, hint]) => `${event}=${hint}`)
    .join('\n')
}

function linesToEventHints(value: string): Record<string, string> {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .reduce<Record<string, string>>((acc, line) => {
      const [event, ...rest] = line.split('=')
      const key = event?.trim()
      const hint = rest.join('=').trim()
      if (key) acc[key] = hint
      return acc
    }, {})
}

export function StepDetailsPanel() {
  const {
    document,
    selectedScenarioId,
    selectedStepId,
    updateDocument,
    updateStep,
    setSelectedStepId,
    setStartScenarioId,
  } = useAgentDocument()

  const selectedScenario =
    document.scenarios.find((scenario) => scenario.id === selectedScenarioId)
    ?? document.scenarios[0]
    ?? null
  const selectedStep =
    selectedScenario?.steps.find((step) => step.id === selectedStepId)
    ?? selectedScenario?.steps[0]
    ?? null

  const updateSelectedStep = (updater: (step: AgentStep) => AgentStep) => {
    if (!selectedScenario || !selectedStep) return
    updateStep(selectedScenario.id, selectedStep.id, updater)
  }

  if (!selectedScenario || !selectedStep) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
        Select a step in the canvas to edit its details.
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
        <div>
          <h3 className="font-medium">Step Details</h3>
          <p className="text-xs text-muted-foreground">Advanced editing lives here. The canvas stays structural.</p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setStartScenarioId(selectedScenario.id)}
        >
          Make scenario start
        </Button>
      </div>
      <div className="flex-1 space-y-6 overflow-y-auto p-4">
        <div className="space-y-2">
          <Label htmlFor="step_id_panel">Step id</Label>
          <Input
            id="step_id_panel"
            value={selectedStep.id}
            onChange={(event) => {
              const nextId = event.target.value
              const previousId = selectedStep.id
              updateDocument((previous) => ({
                ...previous,
                scenarios: previous.scenarios.map((scenario: AgentScenario) => ({
                  ...scenario,
                  start_step_id: scenario.start_step_id === previousId ? nextId : scenario.start_step_id,
                  steps: scenario.steps.map((step) => (
                    step.id === previousId
                      ? { ...step, id: nextId }
                      : {
                          ...step,
                          transitions: step.transitions.map((transition) => (
                            transition.to_step_id === previousId
                              ? { ...transition, to_step_id: nextId }
                              : transition
                          )),
                        }
                  )),
                })),
              }))
              setSelectedStepId(nextId)
            }}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="step_name_panel">Step name</Label>
          <Input
            id="step_name_panel"
            value={selectedStep.name}
            onChange={(event) => updateSelectedStep((step) => ({ ...step, name: event.target.value }))}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="step_description_panel">Description</Label>
          <Input
            id="step_description_panel"
            value={selectedStep.description ?? ''}
            onChange={(event) => updateSelectedStep((step) => ({ ...step, description: event.target.value }))}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="step_say_panel">Say</Label>
          <Textarea
            id="step_say_panel"
            rows={4}
            value={selectedStep.say ?? ''}
            onChange={(event) => updateSelectedStep((step) => ({ ...step, say: event.target.value }))}
          />
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label>Workload class</Label>
            <Select
              value={selectedStep.workload_class ?? 'interactive'}
              onValueChange={(value: 'interactive' | 'deferred') =>
                updateSelectedStep((step) => ({ ...step, workload_class: value }))
              }
            >
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="interactive">Interactive</SelectItem>
                <SelectItem value="deferred">Deferred</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>Execution isolation</Label>
            <Select
              value={selectedStep.execution_isolation ?? 'subprocess'}
              onValueChange={(value: 'inline' | 'subprocess') =>
                updateSelectedStep((step) => ({ ...step, execution_isolation: value }))
              }
            >
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="subprocess">Subprocess</SelectItem>
                <SelectItem value="inline">Inline</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="space-y-3 rounded-lg border p-4">
          <div className="flex items-center justify-between">
            <div>
              <Label>Guards</Label>
              <p className="text-xs text-muted-foreground">Hard requirements checked before the step proceeds.</p>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                updateSelectedStep((step) => ({
                  ...step,
                  guards: [...(step.guards ?? []), { kind: 'channel_allowed', value: '', description: '' }],
                }))
              }
            >
              <Plus className="mr-2 h-4 w-4" />
              Add guard
            </Button>
          </div>
          {(selectedStep.guards ?? []).map((guard: AgentGuardDef, index) => (
            <div key={`${guard.kind}-${index}`} className="grid gap-3 rounded-lg border p-3 md:grid-cols-[160px_1fr_1fr_auto]">
              <Select
                value={guard.kind}
                onValueChange={(value: 'channel_allowed' | 'fact_required') =>
                  updateSelectedStep((step) => ({
                    ...step,
                    guards: (step.guards ?? []).map((item, itemIndex) => (
                      itemIndex === index ? { ...item, kind: value } : item
                    )),
                  }))
                }
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="channel_allowed">Channel allowed</SelectItem>
                  <SelectItem value="fact_required">Fact required</SelectItem>
                </SelectContent>
              </Select>
              <Input
                value={guard.value}
                onChange={(event) =>
                  updateSelectedStep((step) => ({
                    ...step,
                    guards: (step.guards ?? []).map((item, itemIndex) => (
                      itemIndex === index ? { ...item, value: event.target.value } : item
                    )),
                  }))
                }
                placeholder={guard.kind === 'channel_allowed' ? 'web_chat' : 'customer_email'}
              />
              <Input
                value={guard.description ?? ''}
                onChange={(event) =>
                  updateSelectedStep((step) => ({
                    ...step,
                    guards: (step.guards ?? []).map((item, itemIndex) => (
                      itemIndex === index ? { ...item, description: event.target.value } : item
                    )),
                  }))
                }
                placeholder="optional description"
              />
              <Button
                variant="ghost"
                size="icon"
                onClick={() =>
                  updateSelectedStep((step) => ({
                    ...step,
                    guards: (step.guards ?? []).filter((_, itemIndex) => itemIndex !== index),
                  }))
                }
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          ))}
        </div>

        <div className="grid gap-3 md:grid-cols-3">
          <Button
            variant={selectedStep.action_config ? 'primary' : 'outline'}
            onClick={() =>
              updateSelectedStep((step) => ({
                ...step,
                action_config: step.action_config ? null : { code: '' },
                completion: null,
                handoff: null,
              }))
            }
          >
            {selectedStep.action_config ? 'Remove action' : 'Add action'}
          </Button>
          <Button
            variant={selectedStep.completion ? 'primary' : 'outline'}
            onClick={() =>
              updateSelectedStep((step) => ({
                ...step,
                completion: step.completion ? null : { disposition: 'resolved', summary: '' },
                handoff: null,
                action_config: null,
                transitions: step.completion ? step.transitions : [],
              }))
            }
          >
            {selectedStep.completion ? 'Unset completion' : 'Add completion'}
          </Button>
          <Button
            variant={selectedStep.handoff ? 'primary' : 'outline'}
            onClick={() =>
              updateSelectedStep((step) => ({
                ...step,
                handoff: step.handoff ? null : { target_type: 'queue', target: '', summary: '' },
                completion: null,
                action_config: null,
                transitions: step.handoff ? step.transitions : [],
              }))
            }
          >
            {selectedStep.handoff ? 'Unset handoff' : 'Add handoff'}
          </Button>
        </div>

        {selectedStep.action_config && (
          <div className="space-y-2">
            <Label htmlFor="step_action_code_panel">Action code</Label>
            <Textarea
              id="step_action_code_panel"
              rows={8}
              value={selectedStep.action_config.code}
              onChange={(event) =>
                updateSelectedStep((step) => ({
                  ...step,
                  action_config: {
                    code: event.target.value,
                    callable_functions_code: step.action_config?.callable_functions_code ?? '',
                    callable_api_refs: step.action_config?.callable_api_refs ?? [],
                    callable_integrations: step.action_config?.callable_integrations ?? [],
                    callable_system_refs: step.action_config?.callable_system_refs ?? [],
                    input_schema: step.action_config?.input_schema ?? {},
                    timeout_seconds: step.action_config?.timeout_seconds ?? 30,
                  },
                }))
              }
            />
          </div>
        )}

        {selectedStep.completion && (
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="step_completion_disposition_panel">Completion disposition</Label>
              <Input
                id="step_completion_disposition_panel"
                value={selectedStep.completion.disposition}
                onChange={(event) =>
                  updateSelectedStep((step) => ({
                    ...step,
                    completion: {
                      disposition: event.target.value,
                      summary: step.completion?.summary ?? '',
                    },
                  }))
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="step_completion_summary_panel">Completion summary</Label>
              <Input
                id="step_completion_summary_panel"
                value={selectedStep.completion.summary ?? ''}
                onChange={(event) =>
                  updateSelectedStep((step) => ({
                    ...step,
                    completion: {
                      disposition: step.completion?.disposition ?? 'resolved',
                      summary: event.target.value,
                    },
                  }))
                }
              />
            </div>
          </div>
        )}

        {selectedStep.handoff && (
          <div className="grid gap-4 md:grid-cols-3">
            <div className="space-y-2">
              <Label>Handoff target type</Label>
              <Select
                value={selectedStep.handoff.target_type}
                onValueChange={(value) =>
                  updateSelectedStep((step) => ({
                    ...step,
                    handoff: {
                      target_type: value as 'queue' | 'agent' | 'phone_number',
                      target: step.handoff?.target ?? '',
                      summary: step.handoff?.summary ?? '',
                    },
                  }))
                }
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="queue">Queue</SelectItem>
                  <SelectItem value="agent">Agent</SelectItem>
                  <SelectItem value="phone_number">Phone number</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="step_handoff_target_panel">Handoff target</Label>
              <Input
                id="step_handoff_target_panel"
                value={selectedStep.handoff.target}
                onChange={(event) =>
                  updateSelectedStep((step) => ({
                    ...step,
                    handoff: {
                      target_type: step.handoff?.target_type ?? 'queue',
                      target: event.target.value,
                      summary: step.handoff?.summary ?? '',
                    },
                  }))
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="step_handoff_summary_panel">Handoff summary</Label>
              <Input
                id="step_handoff_summary_panel"
                value={selectedStep.handoff.summary ?? ''}
                onChange={(event) =>
                  updateSelectedStep((step) => ({
                    ...step,
                    handoff: {
                      target_type: step.handoff?.target_type ?? 'queue',
                      target: step.handoff?.target ?? '',
                      summary: event.target.value,
                    },
                  }))
                }
              />
            </div>
          </div>
        )}

        <div className="space-y-3 rounded-lg border p-4">
          <div className="flex items-center justify-between">
            <div>
              <Label>Fact requirements</Label>
              <p className="text-xs text-muted-foreground">Facts this step needs before it can continue.</p>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                updateSelectedStep((step) => ({
                  ...step,
                  fact_requirements: [...(step.fact_requirements ?? []), { name: '', purpose: '' }],
                }))
              }
            >
              <Plus className="mr-2 h-4 w-4" />
              Add fact
            </Button>
          </div>
          {(selectedStep.fact_requirements ?? []).map((requirement, index) => (
            <div key={`${requirement.name}-${index}`} className="grid gap-3 rounded-lg border p-3 md:grid-cols-[1fr_1.5fr_auto]">
              <Input
                value={requirement.name}
                onChange={(event) =>
                  updateSelectedStep((step) => ({
                    ...step,
                    fact_requirements: (step.fact_requirements ?? []).map((item, itemIndex) => (
                      itemIndex === index ? { ...item, name: event.target.value } : item
                    )),
                  }))
                }
                placeholder="fact name"
              />
              <Input
                value={requirement.purpose ?? ''}
                onChange={(event) =>
                  updateSelectedStep((step) => ({
                    ...step,
                    fact_requirements: (step.fact_requirements ?? []).map((item, itemIndex) => (
                      itemIndex === index ? { ...item, purpose: event.target.value } : item
                    )),
                  }))
                }
                placeholder="why it is needed"
              />
              <Button
                variant="ghost"
                size="icon"
                onClick={() =>
                  updateSelectedStep((step) => ({
                    ...step,
                    fact_requirements: (step.fact_requirements ?? []).filter((_, itemIndex) => itemIndex !== index),
                  }))
                }
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          ))}
        </div>

        <div className="space-y-2">
          <Label htmlFor="step_tool_refs_panel">Tool refs</Label>
          <Input
            id="step_tool_refs_panel"
            value={listToCsv((selectedStep.tool_policy ?? []).map((binding) => binding.ref))}
            onChange={(event) =>
              updateSelectedStep((step) => ({
                ...step,
                tool_policy: csvToList(event.target.value).map((ref): AgentToolBinding => ({
                  ref,
                  mode: 'optional',
                  invocation_strategy: 'never',
                  args: {},
                })),
              }))
            }
            placeholder="knowledge.lookup, test.submit_lead"
          />
        </div>

        <div className="space-y-4 rounded-lg border p-4">
          <div>
            <Label>Response policy</Label>
            <p className="text-xs text-muted-foreground">Per-step rendering controls in the canonical document.</p>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>Answer directly first</Label>
              <Select
                value={String(ensureResponsePolicy(selectedStep.response_policy).answer_directly_first)}
                onValueChange={(value) =>
                  updateSelectedStep((step) => ({
                    ...step,
                    response_policy: {
                      ...ensureResponsePolicy(step.response_policy),
                      answer_directly_first: value === 'true',
                    },
                  }))
                }
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="true">Yes</SelectItem>
                  <SelectItem value="false">No</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Clarify only if needed</Label>
              <Select
                value={String(ensureResponsePolicy(selectedStep.response_policy).ask_clarifying_question_only_if_needed)}
                onValueChange={(value) =>
                  updateSelectedStep((step) => ({
                    ...step,
                    response_policy: {
                      ...ensureResponsePolicy(step.response_policy),
                      ask_clarifying_question_only_if_needed: value === 'true',
                    },
                  }))
                }
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="true">Yes</SelectItem>
                  <SelectItem value="false">No</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Voice style</Label>
              <Select
                value={ensureResponsePolicy(selectedStep.response_policy).voice_style ?? 'concise'}
                onValueChange={(value: 'concise' | 'balanced' | 'detailed') =>
                  updateSelectedStep((step) => ({
                    ...step,
                    response_policy: {
                      ...ensureResponsePolicy(step.response_policy),
                      voice_style: value,
                    },
                  }))
                }
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="concise">Concise</SelectItem>
                  <SelectItem value="balanced">Balanced</SelectItem>
                  <SelectItem value="detailed">Detailed</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Render with LLM</Label>
              <Select
                value={String(ensureResponsePolicy(selectedStep.response_policy).render_with_llm)}
                onValueChange={(value) =>
                  updateSelectedStep((step) => ({
                    ...step,
                    response_policy: {
                      ...ensureResponsePolicy(step.response_policy),
                      render_with_llm: value === 'true',
                    },
                  }))
                }
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="true">Yes</SelectItem>
                  <SelectItem value="false">No</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Include recent history</Label>
              <Select
                value={String(ensureResponsePolicy(selectedStep.response_policy).include_recent_history)}
                onValueChange={(value) =>
                  updateSelectedStep((step) => ({
                    ...step,
                    response_policy: {
                      ...ensureResponsePolicy(step.response_policy),
                      include_recent_history: value === 'true',
                    },
                  }))
                }
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="true">Yes</SelectItem>
                  <SelectItem value="false">No</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Include known facts</Label>
              <Select
                value={String(ensureResponsePolicy(selectedStep.response_policy).include_known_facts)}
                onValueChange={(value) =>
                  updateSelectedStep((step) => ({
                    ...step,
                    response_policy: {
                      ...ensureResponsePolicy(step.response_policy),
                      include_known_facts: value === 'true',
                    },
                  }))
                }
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="true">Yes</SelectItem>
                  <SelectItem value="false">No</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="step_direct_answer_prompt_panel">Direct answer prompt</Label>
              <Textarea
                id="step_direct_answer_prompt_panel"
                rows={3}
                value={ensureResponsePolicy(selectedStep.response_policy).direct_answer_prompt ?? ''}
                onChange={(event) =>
                  updateSelectedStep((step) => ({
                    ...step,
                    response_policy: {
                      ...ensureResponsePolicy(step.response_policy),
                      direct_answer_prompt: event.target.value,
                    },
                  }))
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="step_deterministic_fallback_text_panel">Deterministic fallback text</Label>
              <Textarea
                id="step_deterministic_fallback_text_panel"
                rows={3}
                value={ensureResponsePolicy(selectedStep.response_policy).deterministic_fallback_text ?? ''}
                onChange={(event) =>
                  updateSelectedStep((step) => ({
                    ...step,
                    response_policy: {
                      ...ensureResponsePolicy(step.response_policy),
                      deterministic_fallback_text: event.target.value,
                    },
                  }))
                }
              />
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="step_response_max_sentences_panel">Response max sentences</Label>
            <Input
              id="step_response_max_sentences_panel"
              type="number"
              min="1"
              value={ensureResponsePolicy(selectedStep.response_policy).response_max_sentences ?? ''}
              onChange={(event) =>
                updateSelectedStep((step) => ({
                  ...step,
                  response_policy: {
                    ...ensureResponsePolicy(step.response_policy),
                    response_max_sentences: event.target.value ? Number(event.target.value) : null,
                  },
                }))
              }
              placeholder="Optional"
            />
          </div>
        </div>

        <div className="space-y-2">
          <Label htmlFor="step_event_hints_panel">Event hints</Label>
          <Textarea
            id="step_event_hints_panel"
            rows={4}
            value={eventHintsToLines(selectedStep.event_hints)}
            onChange={(event) =>
              updateSelectedStep((step) => ({
                ...step,
                event_hints: linesToEventHints(event.target.value),
              }))
            }
            placeholder={`user_confirmed=User clearly confirmed\ncustomer_declined=User declined the offer`}
          />
          <p className="text-xs text-muted-foreground">One `event=hint` pair per line.</p>
        </div>
      </div>
    </div>
  )
}
