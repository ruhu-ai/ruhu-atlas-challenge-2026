import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, RefreshCw, Shield, ShieldAlert, ShieldCheck } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/atoms/card'
import { Checkbox } from '@/components/atoms/checkbox'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import { Textarea } from '@/components/atoms/textarea'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/atoms/tabs'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
import { runtimeRulesService } from '@/api/services/runtime-rules.service'
import type {
  RuleDecision,
  RuleBindingDocument,
  RuleBindingMode,
  RuleBindingScope,
  RuleChannel,
  RuleDefinitionOrganizationScope,
  RuleDefinitionRevisionDocument,
  RuleEffect,
  RuleEvaluationContext,
  RuleDefinitionSummary,
  RuleProgram,
  RuleProgramResolutionInput,
  RuleRevisionBody,
  RuleStage,
} from '@/types/runtime-rules'
import type { SidebarStepItem } from './AgentDefinitionWorkspace'

const STAGE_LABELS: Record<RuleStage, string> = {
  turn_ingress: 'Turn ingress',
  before_tool: 'Before tool',
  after_tool: 'After tool',
  before_response: 'Before response',
  before_emit: 'Before emit',
}

const MODE_LABELS: Record<RuleBindingMode, string> = {
  enforce: 'Enforce',
  shadow: 'Shadow',
  disabled: 'Disabled',
}

const CHANNEL_OPTIONS: Array<{ value: RuleChannel; label: string }> = [
  { value: 'phone', label: 'Phone' },
  { value: 'whatsapp', label: 'WhatsApp' },
  { value: 'web_chat', label: 'Web chat' },
  { value: 'web_widget', label: 'Web widget' },
  { value: 'browser', label: 'Browser' },
]

type RuleFormState = {
  ruleId: string
  revision: number
  mode: RuleBindingMode
  order: number
  stateIds: string[]
  channels: RuleChannel[]
  toolRefsText: string
  eventTypesText: string
  confirmBroadScope: boolean
}

type ResolutionChannel = RuleChannel | 'any'

type RuleResolutionFormState = {
  stateId: string
  channel: ResolutionChannel
  eventType: string
  toolRef: string
}

type RuleEvaluationFormState = {
  stage: RuleStage
  conversationId: string
  turnCount: number
  turnText: string
  turnMetadataText: string
  toolOutcome: string
  toolArgsText: string
  factsText: string
  metadataText: string
  currentHour: string
  currentDay: string
}

type RuleDefinitionFormState = {
  ruleId: string
  organizationScope: RuleDefinitionOrganizationScope
  stage: RuleStage
  name: string
  summary: string
  tagsText: string
  predicateText: string
  effectText: string
  metadataText: string
}

interface AgentRulesViewProps {
  agentId: string
  agentName: string
  steps: SidebarStepItem[]
  selectedStateId?: string | null
}

function formatDateTime(value?: string | null): string {
  if (!value) return 'n/a'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'n/a'
  return date.toLocaleString()
}

function parseCsvValues(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

function scopeFromForm(agentId: string, form: RuleFormState): RuleBindingScope {
  return {
    agent_ids: [agentId],
    step_ids: [...form.stateIds],
    channels: [...form.channels],
    tool_refs: parseCsvValues(form.toolRefsText),
    event_types: parseCsvValues(form.eventTypesText),
  }
}

function isBroadScope(scope: RuleBindingScope): boolean {
  return (
    scope.channels.length === 0 ||
    scope.agent_ids.length === 0 ||
    scope.step_ids.length === 0 ||
    scope.tool_refs.length === 0 ||
    scope.event_types.length === 0
  )
}

function createInitialForm(selectedStateId?: string | null): RuleFormState {
  return {
    ruleId: '',
    revision: 1,
    mode: 'shadow',
    order: 100,
    stateIds: selectedStateId ? [selectedStateId] : [],
    channels: [],
    toolRefsText: '',
    eventTypesText: '',
    confirmBroadScope: false,
  }
}

function formatScopeSummary(scope: RuleBindingScope, statesById: Map<string, SidebarStepItem>): string[] {
  const items: string[] = []
  if (scope.agent_ids.length > 0) {
    items.push(`agent:${scope.agent_ids.join(', ')}`)
  }
  if (scope.step_ids.length > 0) {
    const stateNames = scope.step_ids.map((stateId: string) => statesById.get(stateId)?.name || stateId)
    items.push(`steps:${stateNames.join(', ')}`)
  }
  if (scope.channels.length > 0) {
    items.push(`channels:${scope.channels.join(', ')}`)
  }
  if (scope.tool_refs.length > 0) {
    items.push(`tools:${scope.tool_refs.join(', ')}`)
  }
  if (scope.event_types.length > 0) {
    items.push(`events:${scope.event_types.join(', ')}`)
  }
  if (items.length === 1 && items[0]?.startsWith('agent:')) {
    items.push('broad within this agent')
  }
  return items
}

function definitionDisplayRevision(definition: RuleDefinitionSummary): number {
  return definition.published_revision ?? definition.latest_revision
}

function createInitialResolutionForm(selectedStateId?: string | null): RuleResolutionFormState {
  return {
    stateId: selectedStateId || '',
    channel: 'any',
    eventType: '',
    toolRef: '',
  }
}

function createInitialEvaluationForm(): RuleEvaluationFormState {
  return {
    stage: 'turn_ingress',
    conversationId: '',
    turnCount: 1,
    turnText: '',
    turnMetadataText: '{}',
    toolOutcome: '',
    toolArgsText: '{}',
    factsText: '{}',
    metadataText: '{}',
    currentHour: '',
    currentDay: '',
  }
}

function createInitialDefinitionForm(selectedDefinition?: RuleDefinitionSummary | null): RuleDefinitionFormState {
  return {
    ruleId: selectedDefinition?.rule_id || '',
    organizationScope: 'organization',
    stage: selectedDefinition?.stage || 'turn_ingress',
    name: selectedDefinition?.name || '',
    summary: '',
    tagsText: '',
    predicateText: formatJson({ kind: 'match', path: 'context.channel', operator: 'eq', value: 'web_chat' }),
    effectText: formatJson({ kind: 'trace', code: 'rule.trace' }),
    metadataText: '{}',
  }
}

function parseJsonRecord(value: string, label: string): Record<string, unknown> {
  const normalized = value.trim()
  if (!normalized) return {}
  let parsed: unknown
  try {
    parsed = JSON.parse(normalized)
  } catch {
    throw new Error(`${label} must be valid JSON`)
  }
  if (parsed === null || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error(`${label} must be a JSON object`)
  }
  return parsed as Record<string, unknown>
}

function formatEffect(effect?: RuleEffect | null): string {
  if (!effect) return 'none'
  const code = typeof effect.code === 'string' && effect.code ? effect.code : null
  return code ? `${effect.kind}:${code}` : effect.kind
}

function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2)
}

function parseRuleEffect(value: string): RuleEffect {
  const parsed = parseJsonRecord(value, 'Effect')
  if (typeof parsed.kind !== 'string' || parsed.kind.trim().length === 0) {
    throw new Error('Effect JSON must include non-empty string field "kind"')
  }
  return parsed as RuleEffect
}

function parseDefinitionBody(form: RuleDefinitionFormState): RuleRevisionBody {
  const predicate = parseJsonRecord(form.predicateText, 'Predicate')
  if (!predicate.kind || typeof predicate.kind !== 'string') {
    throw new Error('Predicate JSON must include a "kind" field (e.g. "match", "all", "any", "not")')
  }
  return {
    name: form.name.trim(),
    summary: form.summary.trim(),
    stage: form.stage,
    predicate,
    effect: parseRuleEffect(form.effectText),
    tags: parseCsvValues(form.tagsText),
    metadata: parseJsonRecord(form.metadataText, 'Definition metadata'),
  }
}

export function AgentRulesView({ agentId, agentName, steps: states, selectedStateId }: AgentRulesViewProps) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [stageFilter, setStageFilter] = useState<'all' | RuleStage>('all')
  const [selectedRuleId, setSelectedRuleId] = useState<string | null>(null)
  const [editingBindingId, setEditingBindingId] = useState<string | null>(null)
  const [form, setForm] = useState<RuleFormState>(() => createInitialForm(selectedStateId))
  const [resolutionForm, setResolutionForm] = useState<RuleResolutionFormState>(() => createInitialResolutionForm(selectedStateId))
  const [evaluationForm, setEvaluationForm] = useState<RuleEvaluationFormState>(() => createInitialEvaluationForm())
  const [resolvedProgram, setResolvedProgram] = useState<RuleProgram | null>(null)
  const [evaluationResult, setEvaluationResult] = useState<RuleDecision | null>(null)
  const [runtimeTab, setRuntimeTab] = useState<'preview' | 'evaluate'>('preview')
  const [definitionForm, setDefinitionForm] = useState<RuleDefinitionFormState>(() => createInitialDefinitionForm())
  const [definitionRevisionInput, setDefinitionRevisionInput] = useState('1')
  const [loadedDefinitionRevision, setLoadedDefinitionRevision] = useState<RuleDefinitionRevisionDocument | null>(null)

  const definitionsQuery = useQuery({
    queryKey: ['runtime-rules-definitions'],
    queryFn: () => runtimeRulesService.listDefinitions({ organization_scope: 'all', limit: 200 }),
    staleTime: 15_000,
  })

  const bindingsQuery = useQuery({
    queryKey: ['runtime-rules-bindings', agentId],
    queryFn: () => runtimeRulesService.listBindings({ organization_scope: 'all', agent_id: agentId, limit: 200 }),
    enabled: !!agentId,
    staleTime: 5_000,
  })

  const definitions = definitionsQuery.data || []
  const publishedDefinitions = useMemo(
    () => definitions.filter((item) => item.published_revision != null),
    [definitions],
  )

  const definitionsById = useMemo(() => new Map(definitions.map((item) => [item.rule_id, item])), [definitions])
  const statesById = useMemo(() => new Map(states.map((state) => [state.id, state])), [states])

  const filteredDefinitions = useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase()
    return publishedDefinitions.filter((item) => {
      if (stageFilter !== 'all' && item.stage !== stageFilter) return false
      if (!normalizedSearch) return true
      const haystack = [item.rule_id, item.name, ...item.tags].join(' ').toLowerCase()
      return haystack.includes(normalizedSearch)
    })
  }, [publishedDefinitions, search, stageFilter])

  const activeBindings = useMemo(() => {
    const items = [...(bindingsQuery.data || [])]
    items.sort((left, right) => {
      if (left.mode !== right.mode) {
        const modeOrder: RuleBindingMode[] = ['enforce', 'shadow', 'disabled']
        return modeOrder.indexOf(left.mode) - modeOrder.indexOf(right.mode)
      }
      if (left.order !== right.order) return left.order - right.order
      return left.rule_id.localeCompare(right.rule_id)
    })
    return items
  }, [bindingsQuery.data])

  const selectedDefinition =
    (selectedRuleId ? definitionsById.get(selectedRuleId) : null) ||
    (publishedDefinitions.length > 0 ? publishedDefinitions[0] : null)

  const editingBinding =
    (editingBindingId ? activeBindings.find((item) => item.binding_id === editingBindingId) : null) || null

  const applyDefinitionRevisionToForm = (revision: RuleDefinitionRevisionDocument) => {
    setLoadedDefinitionRevision(revision)
    setDefinitionRevisionInput(String(revision.revision))
    setDefinitionForm({
      ruleId: revision.rule_id,
      organizationScope: revision.organization_id ? 'organization' : 'system',
      stage: revision.stage,
      name: revision.name,
      summary: revision.summary,
      tagsText: revision.tags.join(', '),
      predicateText: formatJson(revision.predicate),
      effectText: formatJson(revision.effect),
      metadataText: formatJson(revision.metadata),
    })
  }

  // No auto-selection — all cards render equally until the user explicitly picks one.

  useEffect(() => {
    if (editingBinding) return
    if (!selectedDefinition) {
      setForm(createInitialForm(selectedStateId))
      return
    }
    setForm((current) => ({
      ...current,
      ruleId: selectedDefinition.rule_id,
      revision: definitionDisplayRevision(selectedDefinition),
    }))
  }, [editingBinding, selectedDefinition, selectedStateId])

  useEffect(() => {
    if (!selectedStateId) return
    setResolutionForm((current) => (current.stateId ? current : { ...current, stateId: selectedStateId }))
  }, [selectedStateId])

  useEffect(() => {
    if (!selectedDefinition) return
    setDefinitionForm((current) => {
      if (current.ruleId) return current
      return {
        ...current,
        ruleId: selectedDefinition.rule_id,
        stage: selectedDefinition.stage,
        name: selectedDefinition.name,
      }
    })
  }, [selectedDefinition])

  const createBindingMutation = useMutation({
    mutationFn: async (currentForm: RuleFormState) =>
      runtimeRulesService.createBinding({
        organization_scope: 'organization',
        binding_id: crypto.randomUUID(),
        rule_id: currentForm.ruleId,
        revision: currentForm.revision,
        mode: currentForm.mode,
        order: currentForm.order,
        scope: scopeFromForm(agentId, currentForm),
        confirm_broad_scope: currentForm.confirmBroadScope,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['runtime-rules-bindings', agentId] })
      setEditingBindingId(null)
      setForm(createInitialForm(selectedStateId))
      toast.success('Rule binding created')
    },
    onError: (error: Error) => {
      toast.error(`Failed to create rule binding: ${error.message}`)
    },
  })

  const updateBindingMutation = useMutation({
    mutationFn: async (payload: { bindingId: string; form: RuleFormState }) =>
      runtimeRulesService.updateBinding(payload.bindingId, {
        revision: payload.form.revision,
        mode: payload.form.mode,
        order: payload.form.order,
        scope: scopeFromForm(agentId, payload.form),
        confirm_broad_scope: payload.form.confirmBroadScope,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['runtime-rules-bindings', agentId] })
      setEditingBindingId(null)
      setForm(createInitialForm(selectedStateId))
      toast.success('Rule binding updated')
    },
    onError: (error: Error) => {
      toast.error(`Failed to update rule binding: ${error.message}`)
    },
  })

  const disableBindingMutation = useMutation({
    mutationFn: async (binding: RuleBindingDocument) =>
      runtimeRulesService.updateBinding(binding.binding_id, { mode: 'disabled' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['runtime-rules-bindings', agentId] })
      toast.success('Rule binding disabled')
    },
    onError: (error: Error) => {
      toast.error(`Failed to disable rule binding: ${error.message}`)
    },
  })

  const resolveProgramMutation = useMutation({
    mutationFn: async (payload: RuleProgramResolutionInput) => runtimeRulesService.resolveProgram(payload),
    onSuccess: (program) => {
      setResolvedProgram(program)
      setEvaluationResult(null)
    },
    onError: (error: Error) => {
      toast.error(`Failed to resolve effective rule program: ${error.message}`)
    },
  })

  const evaluateProgramMutation = useMutation({
    mutationFn: async (payload: { scope: RuleProgramResolutionInput; context: RuleEvaluationContext }) => {
      const program = await runtimeRulesService.resolveProgram(payload.scope)
      const decision = await runtimeRulesService.evaluateProgram({
        program,
        context: payload.context,
      })
      return { program, decision }
    },
    onSuccess: ({ program, decision }) => {
      setResolvedProgram(program)
      setEvaluationResult(decision)
    },
    onError: (error: Error) => {
      toast.error(`Dry-run evaluation failed: ${error.message}`)
    },
  })

  const loadDefinitionRevisionMutation = useMutation({
    mutationFn: async (payload: { ruleId: string; revision: number }) =>
      runtimeRulesService.getDefinitionRevision(payload.ruleId, payload.revision, { organization_scope: 'all' }),
    onSuccess: (revision) => {
      applyDefinitionRevisionToForm(revision)
      setSelectedRuleId(revision.rule_id)
      toast.success(`Loaded ${revision.rule_id}@${revision.revision}`)
    },
    onError: (error: Error) => {
      toast.error(`Failed to load rule revision: ${error.message}`)
    },
  })

  const createDefinitionMutation = useMutation({
    mutationFn: async (formState: RuleDefinitionFormState) => {
      const body = parseDefinitionBody(formState)
      if (!formState.ruleId.trim()) {
        throw new Error('Rule id is required')
      }
      return runtimeRulesService.createDefinition({
        ...body,
        rule_id: formState.ruleId.trim(),
        organization_scope: formState.organizationScope,
      })
    },
    onSuccess: (revision) => {
      applyDefinitionRevisionToForm(revision)
      setSelectedRuleId(revision.rule_id)
      void queryClient.invalidateQueries({ queryKey: ['runtime-rules-definitions'] })
      toast.success(`Created ${revision.rule_id}@${revision.revision}`)
    },
    onError: (error: Error) => {
      toast.error(`Failed to create definition: ${error.message}`)
    },
  })

  const updateDefinitionDraftMutation = useMutation({
    mutationFn: async (payload: { ruleId: string; revision: number; body: RuleRevisionBody }) =>
      runtimeRulesService.updateDefinitionRevision(payload.ruleId, payload.revision, payload.body),
    onSuccess: (revision) => {
      applyDefinitionRevisionToForm(revision)
      void queryClient.invalidateQueries({ queryKey: ['runtime-rules-definitions'] })
      toast.success(`Saved draft ${revision.rule_id}@${revision.revision}`)
    },
    onError: (error: Error) => {
      toast.error(`Failed to save draft: ${error.message}`)
    },
  })

  const createDefinitionRevisionMutation = useMutation({
    mutationFn: async (payload: { ruleId: string; body: RuleRevisionBody }) =>
      runtimeRulesService.createDefinitionRevision(payload.ruleId, payload.body),
    onSuccess: (revision) => {
      applyDefinitionRevisionToForm(revision)
      setSelectedRuleId(revision.rule_id)
      void queryClient.invalidateQueries({ queryKey: ['runtime-rules-definitions'] })
      toast.success(`Created draft revision ${revision.rule_id}@${revision.revision}`)
    },
    onError: (error: Error) => {
      toast.error(`Failed to create revision: ${error.message}`)
    },
  })

  const publishDefinitionRevisionMutation = useMutation({
    mutationFn: async (payload: { ruleId: string; revision: number }) =>
      runtimeRulesService.publishDefinitionRevision(payload.ruleId, payload.revision),
    onSuccess: (revision) => {
      applyDefinitionRevisionToForm(revision)
      void queryClient.invalidateQueries({ queryKey: ['runtime-rules-definitions'] })
      toast.success(`Published ${revision.rule_id}@${revision.revision}`)
    },
    onError: (error: Error) => {
      toast.error(`Failed to publish revision: ${error.message}`)
    },
  })

  const retireDefinitionRevisionMutation = useMutation({
    mutationFn: async (payload: { ruleId: string; revision: number }) =>
      runtimeRulesService.retireDefinitionRevision(payload.ruleId, payload.revision),
    onSuccess: (revision) => {
      applyDefinitionRevisionToForm(revision)
      void queryClient.invalidateQueries({ queryKey: ['runtime-rules-definitions'] })
      toast.success(`Retired ${revision.rule_id}@${revision.revision}`)
    },
    onError: (error: Error) => {
      toast.error(`Failed to retire revision: ${error.message}`)
    },
  })

  const formScope = useMemo(() => scopeFromForm(agentId, form), [form, agentId])
  const needsBroadScopeConfirmation = isBroadScope(formScope)
  const isMutating =
    createBindingMutation.isPending || updateBindingMutation.isPending || disableBindingMutation.isPending
  const isDefinitionMutating =
    loadDefinitionRevisionMutation.isPending ||
    createDefinitionMutation.isPending ||
    updateDefinitionDraftMutation.isPending ||
    createDefinitionRevisionMutation.isPending ||
    publishDefinitionRevisionMutation.isPending ||
    retireDefinitionRevisionMutation.isPending

  const resolvedRuleIndex = useMemo(() => {
    const entries =
      resolvedProgram?.library.rules.map((rule) => [`${rule.rule_id}@${rule.revision}`, rule] as const) || []
    return new Map(entries)
  }, [resolvedProgram])

  const resolvedBindingsByStage = useMemo(() => {
    const groups = new Map<RuleStage, Array<{ binding: RuleProgram['bindings'][number]; effect: string; name: string; summary: string }>>()
    for (const binding of resolvedProgram?.bindings || []) {
      const rule = resolvedRuleIndex.get(`${binding.rule_id}@${binding.revision}`)
      if (!rule) continue
      const items = groups.get(rule.stage) || []
      items.push({
        binding,
        effect: formatEffect(rule.effect),
        name: rule.name,
        summary: rule.summary,
      })
      groups.set(rule.stage, items)
    }
    return groups
  }, [resolvedProgram, resolvedRuleIndex])

  const activeRuntimeScope = useMemo<RuleProgramResolutionInput>(
    () => ({
      agent_id: agentId,
      state_id: resolutionForm.stateId || undefined,
      channel: resolutionForm.channel === 'any' ? undefined : resolutionForm.channel,
      event_type: resolutionForm.eventType.trim() || undefined,
      tool_ref: resolutionForm.toolRef.trim() || undefined,
    }),
    [agentId, resolutionForm],
  )

  const handleToggleState = (stateId: string, checked: boolean) => {
    setForm((current) => ({
      ...current,
      stateIds: checked
        ? Array.from(new Set([...current.stateIds, stateId]))
        : current.stateIds.filter((item) => item !== stateId),
    }))
  }

  const handleToggleChannel = (channel: RuleChannel, checked: boolean) => {
    setForm((current) => ({
      ...current,
      channels: checked
        ? Array.from(new Set([...current.channels, channel]))
        : current.channels.filter((item) => item !== channel),
    }))
  }

  const startCreateBinding = (definition: RuleDefinitionSummary) => {
    setEditingBindingId(null)
    setSelectedRuleId(definition.rule_id)
    setForm({
      ...createInitialForm(selectedStateId),
      ruleId: definition.rule_id,
      revision: definitionDisplayRevision(definition),
    })
  }

  const startEditBinding = (binding: RuleBindingDocument) => {
    setEditingBindingId(binding.binding_id)
    setSelectedRuleId(binding.rule_id)
    setForm({
      ruleId: binding.rule_id,
      revision: binding.revision,
      mode: binding.mode,
      order: binding.order,
      stateIds: [...binding.scope.step_ids],
      channels: [...binding.scope.channels],
      toolRefsText: binding.scope.tool_refs.join(', '),
      eventTypesText: binding.scope.event_types.join(', '),
      confirmBroadScope: !isBroadScope(binding.scope),
    })
  }

  const resetComposer = () => {
    setEditingBindingId(null)
    setForm(
      selectedDefinition
        ? {
            ...createInitialForm(selectedStateId),
            ruleId: selectedDefinition.rule_id,
            revision: definitionDisplayRevision(selectedDefinition),
          }
        : createInitialForm(selectedStateId),
    )
  }

  const handleResolveProgram = async () => {
    await resolveProgramMutation.mutateAsync(activeRuntimeScope)
  }

  const handleEvaluateProgram = async () => {
    try {
      const context: RuleEvaluationContext = {
        stage: evaluationForm.stage,
        conversation: {
          conversation_id: evaluationForm.conversationId.trim() || undefined,
          agent_id: agentId,
          step_id: resolutionForm.stateId || undefined,
          channel: resolutionForm.channel === 'any' ? undefined : resolutionForm.channel,
          turn_count: Math.max(0, Number(evaluationForm.turnCount) || 0),
        },
        turn: {
          event_type: resolutionForm.eventType.trim() || undefined,
          text: evaluationForm.turnText.trim() || undefined,
          metadata: parseJsonRecord(evaluationForm.turnMetadataText, 'Turn metadata'),
        },
        tool: {
          ref: resolutionForm.toolRef.trim() || undefined,
          outcome: evaluationForm.toolOutcome.trim() || undefined,
          args: parseJsonRecord(evaluationForm.toolArgsText, 'Tool args'),
        },
        facts: parseJsonRecord(evaluationForm.factsText, 'Facts'),
        metadata: parseJsonRecord(evaluationForm.metadataText, 'Runtime metadata'),
        time: {
          current_hour: evaluationForm.currentHour.trim() ? Number(evaluationForm.currentHour) : undefined,
          current_day: evaluationForm.currentDay.trim() || undefined,
        },
      }
      await evaluateProgramMutation.mutateAsync({
        scope: activeRuntimeScope,
        context,
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown error'
      toast.error(message)
    }
  }

  const loadRevision = async (ruleId: string, revision: number) => {
    if (!ruleId.trim()) {
      toast.error('Rule id is required to load a revision')
      return
    }
    if (!Number.isFinite(revision) || revision < 1) {
      toast.error('Revision must be a positive number')
      return
    }
    await loadDefinitionRevisionMutation.mutateAsync({ ruleId: ruleId.trim(), revision: Math.floor(revision) })
  }

  const handleCreateDefinition = async () => {
    await createDefinitionMutation.mutateAsync(definitionForm)
  }

  const handleSaveDefinitionDraft = async () => {
    if (!loadedDefinitionRevision) {
      toast.error('Load a draft revision first')
      return
    }
    if (loadedDefinitionRevision.status !== 'draft') {
      toast.error('Only draft revisions can be updated')
      return
    }
    await updateDefinitionDraftMutation.mutateAsync({
      ruleId: loadedDefinitionRevision.rule_id,
      revision: loadedDefinitionRevision.revision,
      body: parseDefinitionBody(definitionForm),
    })
  }

  const handleCreateNextRevision = async () => {
    const ruleId = definitionForm.ruleId.trim()
    if (!ruleId) {
      toast.error('Rule id is required to create a revision')
      return
    }
    await createDefinitionRevisionMutation.mutateAsync({
      ruleId,
      body: parseDefinitionBody(definitionForm),
    })
  }

  const handlePublishLoadedRevision = async () => {
    if (!loadedDefinitionRevision) {
      toast.error('Load a draft revision before publishing')
      return
    }
    if (loadedDefinitionRevision.status !== 'draft') {
      toast.error('Only draft revisions can be published')
      return
    }
    await publishDefinitionRevisionMutation.mutateAsync({
      ruleId: loadedDefinitionRevision.rule_id,
      revision: loadedDefinitionRevision.revision,
    })
  }

  const handleRetireLoadedRevision = async () => {
    if (!loadedDefinitionRevision) {
      toast.error('Load a published revision before retiring')
      return
    }
    if (loadedDefinitionRevision.status !== 'published') {
      toast.error('Only published revisions can be retired')
      return
    }
    await retireDefinitionRevisionMutation.mutateAsync({
      ruleId: loadedDefinitionRevision.rule_id,
      revision: loadedDefinitionRevision.revision,
    })
  }

  const resetDefinitionForm = () => {
    setLoadedDefinitionRevision(null)
    setDefinitionRevisionInput('1')
    setDefinitionForm(createInitialDefinitionForm(selectedDefinition))
  }

  const submit = async () => {
    if (!form.ruleId) {
      toast.error('Select a published rule definition first')
      return
    }
    if (needsBroadScopeConfirmation && !form.confirmBroadScope) {
      toast.error('Broad scope must be explicitly confirmed before saving the binding')
      return
    }
    if (editingBindingId) {
      await updateBindingMutation.mutateAsync({ bindingId: editingBindingId, form })
      return
    }
    await createBindingMutation.mutateAsync(form)
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Agent Rules</CardTitle>
          <CardDescription>
            Bind published rules to <span className="font-medium text-foreground">{agentName}</span>. Definitions are
            global or org-scoped, but bindings here are pinned to the current agent definition.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <Badge variant="outline" className="border-sky-500/30 text-sky-200">
            Agent scope: {agentId}
          </Badge>
          {selectedStateId && (
            <Badge variant="outline" className="border-violet-500/30 text-violet-200">
              Selected state: {statesById.get(selectedStateId)?.name || selectedStateId}
            </Badge>
          )}
          <div className="rounded-md border border-border/70 bg-card px-3 py-2 text-xs text-muted-foreground">
            Rule mutations require admin role. Non-admin users can inspect active bindings but will get `403` on save.
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(360px,0.85fr)]">
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <CardTitle className="text-lg">Published Definitions</CardTitle>
                  <CardDescription>
                    Browse published runtime rules and attach them to this agent.
                  </CardDescription>
                </div>
                <Button
                  variant="outline"
                  onClick={() => {
                    void definitionsQuery.refetch()
                    void bindingsQuery.refetch()
                  }}
                  disabled={definitionsQuery.isFetching || bindingsQuery.isFetching}
                >
                  {definitionsQuery.isFetching || bindingsQuery.isFetching ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <RefreshCw className="mr-2 h-4 w-4" />
                  )}
                  Refresh
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_200px]">
                <Input
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search by rule id, name, or tag"
                />
                <Select value={stageFilter} onValueChange={(value) => setStageFilter(value as 'all' | RuleStage)}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All stages</SelectItem>
                    {Object.entries(STAGE_LABELS).map(([value, label]) => (
                      <SelectItem key={value} value={value}>
                        {label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {definitionsQuery.isLoading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading rule definitions...
                </div>
              ) : filteredDefinitions.length === 0 ? (
                <p className="text-sm text-muted-foreground">No published rules matched the current filters.</p>
              ) : (
                <div className="space-y-3">
                  {filteredDefinitions.map((definition) => {
                    const isSelected = definition.rule_id === selectedRuleId
                    return (
                      <div
                        key={definition.rule_id}
                        className="rounded-md border border-border bg-card p-4"
                      >
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div className="space-y-2">
                            <div className="flex flex-wrap items-center gap-2">
                              <p className="font-medium">{definition.name}</p>
                              <Badge variant="outline">{STAGE_LABELS[definition.stage]}</Badge>
                              <Badge variant="outline">
                                rev {definitionDisplayRevision(definition)}
                              </Badge>
                              <Badge
                                variant="outline"
                                className={definition.organization_id ? 'border-amber-500/30 text-amber-300' : 'border-emerald-500/30 text-emerald-300'}
                              >
                                {definition.organization_id ? 'Org' : 'System'}
                              </Badge>
                            </div>
                            <p className="text-xs text-muted-foreground">{definition.rule_id}</p>
                            {definition.tags.length > 0 && (
                              <div className="flex flex-wrap gap-2">
                                {definition.tags.map((tag) => (
                                  <Badge key={tag} variant="outline" className="text-xs">
                                    {tag}
                                  </Badge>
                                ))}
                              </div>
                            )}
                          </div>
                          <Button variant="outline" size="sm" onClick={() => startCreateBinding(definition)}>
                            Bind Rule
                          </Button>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Definition Studio</CardTitle>
              <CardDescription>
                Create and manage rule definition revisions without leaving this agent authoring route.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_180px_160px]">
                <div className="space-y-1.5">
                  <Label htmlFor="rule-definition-id">Rule id</Label>
                  <Input
                    id="rule-definition-id"
                    value={definitionForm.ruleId}
                    onChange={(event) =>
                      setDefinitionForm((current) => ({ ...current, ruleId: event.target.value.trim() }))
                    }
                    placeholder="customer.data.redact"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>Organization scope</Label>
                  <Select
                    value={definitionForm.organizationScope}
                    onValueChange={(value) =>
                      setDefinitionForm((current) => ({
                        ...current,
                        organizationScope: value as RuleDefinitionOrganizationScope,
                      }))
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="organization">Organization</SelectItem>
                      <SelectItem value="system">System</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="rule-definition-revision">Revision</Label>
                  <Input
                    id="rule-definition-revision"
                    type="number"
                    min={1}
                    value={definitionRevisionInput}
                    onChange={(event) => setDefinitionRevisionInput(event.target.value)}
                  />
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="outline"
                  onClick={() => void loadRevision(definitionForm.ruleId, Number(definitionRevisionInput))}
                  disabled={isDefinitionMutating}
                >
                  {loadDefinitionRevisionMutation.isPending ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <RefreshCw className="mr-2 h-4 w-4" />
                  )}
                  Load Revision
                </Button>
                <Button
                  variant="outline"
                  onClick={() =>
                    selectedDefinition
                      ? void loadRevision(selectedDefinition.rule_id, selectedDefinition.latest_revision)
                      : toast.error('Select a rule definition first')
                  }
                  disabled={!selectedDefinition || isDefinitionMutating}
                >
                  Load Latest Selected
                </Button>
                <Button
                  variant="outline"
                  onClick={() =>
                    selectedDefinition?.published_revision
                      ? void loadRevision(selectedDefinition.rule_id, selectedDefinition.published_revision)
                      : toast.error('Selected rule has no published revision')
                  }
                  disabled={!selectedDefinition?.published_revision || isDefinitionMutating}
                >
                  Load Published
                </Button>
              </div>

              {loadedDefinitionRevision ? (
                <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-xs text-muted-foreground">
                  <Badge variant="outline">{loadedDefinitionRevision.rule_id}</Badge>
                  <Badge variant="outline">rev {loadedDefinitionRevision.revision}</Badge>
                  <Badge
                    variant="outline"
                    className={
                      loadedDefinitionRevision.status === 'published'
                        ? 'border-emerald-500/30 text-emerald-300'
                        : loadedDefinitionRevision.status === 'retired'
                          ? 'border-muted text-muted-foreground'
                        : 'border-amber-500/30 text-amber-300'
                    }
                  >
                    {loadedDefinitionRevision.status}
                  </Badge>
                  <span>updated {formatDateTime(loadedDefinitionRevision.created_at)}</span>
                  {loadedDefinitionRevision.published_at && (
                    <span>published {formatDateTime(loadedDefinitionRevision.published_at)}</span>
                  )}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">
                  No revision loaded. Create a new definition or load an existing revision.
                </p>
              )}

              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="rule-definition-name">Name</Label>
                  <Input
                    id="rule-definition-name"
                    value={definitionForm.name}
                    onChange={(event) =>
                      setDefinitionForm((current) => ({ ...current, name: event.target.value }))
                    }
                    placeholder="Redact PII Before Emit"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>Stage</Label>
                  <Select
                    value={definitionForm.stage}
                    onValueChange={(value) =>
                      setDefinitionForm((current) => ({ ...current, stage: value as RuleStage }))
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {Object.entries(STAGE_LABELS).map(([value, label]) => (
                        <SelectItem key={value} value={value}>
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="rule-definition-summary">Summary</Label>
                <Input
                  id="rule-definition-summary"
                  value={definitionForm.summary}
                  onChange={(event) =>
                    setDefinitionForm((current) => ({ ...current, summary: event.target.value }))
                  }
                  placeholder="Prevent PII leakage on outbound messages."
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="rule-definition-tags">Tags</Label>
                <Input
                  id="rule-definition-tags"
                  value={definitionForm.tagsText}
                  onChange={(event) =>
                    setDefinitionForm((current) => ({ ...current, tagsText: event.target.value }))
                  }
                  placeholder="privacy, compliance"
                />
                <p className="text-xs text-muted-foreground">Comma-separated tags.</p>
              </div>

              <div className="grid gap-4 xl:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="rule-definition-predicate">Predicate JSON</Label>
                  <Textarea
                    id="rule-definition-predicate"
                    value={definitionForm.predicateText}
                    onChange={(event) =>
                      setDefinitionForm((current) => ({ ...current, predicateText: event.target.value }))
                    }
                    className="min-h-[150px] font-mono text-xs"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="rule-definition-effect">Effect JSON</Label>
                  <Textarea
                    id="rule-definition-effect"
                    value={definitionForm.effectText}
                    onChange={(event) =>
                      setDefinitionForm((current) => ({ ...current, effectText: event.target.value }))
                    }
                    className="min-h-[150px] font-mono text-xs"
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="rule-definition-metadata">Metadata JSON</Label>
                <Textarea
                  id="rule-definition-metadata"
                  value={definitionForm.metadataText}
                  onChange={(event) =>
                    setDefinitionForm((current) => ({ ...current, metadataText: event.target.value }))
                  }
                  className="min-h-[120px] font-mono text-xs"
                />
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <Button onClick={() => void handleCreateDefinition()} disabled={isDefinitionMutating}>
                  {createDefinitionMutation.isPending ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <Shield className="mr-2 h-4 w-4" />
                  )}
                  Create Definition
                </Button>
                <Button
                  variant="outline"
                  onClick={() => void handleSaveDefinitionDraft()}
                  disabled={isDefinitionMutating || loadedDefinitionRevision?.status !== 'draft'}
                >
                  Save Loaded Draft
                </Button>
                <Button variant="outline" onClick={() => void handleCreateNextRevision()} disabled={isDefinitionMutating}>
                  Create Next Draft
                </Button>
                <Button
                  variant="outline"
                  onClick={() => void handlePublishLoadedRevision()}
                  disabled={isDefinitionMutating || loadedDefinitionRevision?.status !== 'draft'}
                >
                  Publish Loaded Draft
                </Button>
                <Button
                  variant="outline"
                  onClick={() => void handleRetireLoadedRevision()}
                  disabled={isDefinitionMutating || loadedDefinitionRevision?.status !== 'published'}
                >
                  Retire Loaded Revision
                </Button>
                <Button variant="ghost" onClick={resetDefinitionForm} disabled={isDefinitionMutating}>
                  Reset Editor
                </Button>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Active Agent Bindings</CardTitle>
              <CardDescription>
                Enforcement order and scope for rules currently attached to this agent definition.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {bindingsQuery.isLoading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading active bindings...
                </div>
              ) : activeBindings.length === 0 ? (
                <div className="rounded-md border border-dashed border-border p-6 text-sm text-muted-foreground">
                  No rules are currently bound to this agent.
                </div>
              ) : (
                <div className="space-y-3">
                  {activeBindings.map((binding) => {
                    const definition = definitionsById.get(binding.rule_id)
                    const scopeSummary = formatScopeSummary(binding.scope, statesById)
                    return (
                      <div key={binding.binding_id} className="rounded-md border border-border bg-card p-4">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div className="space-y-2">
                            <div className="flex flex-wrap items-center gap-2">
                              <p className="font-medium">{definition?.name || binding.rule_id}</p>
                              <Badge
                                variant="outline"
                                className={
                                  binding.mode === 'enforce'
                                    ? 'border-emerald-500/30 text-emerald-300'
                                    : binding.mode === 'shadow'
                                      ? 'border-sky-500/30 text-sky-200'
                                      : 'border-muted text-muted-foreground'
                                }
                              >
                                {MODE_LABELS[binding.mode]}
                              </Badge>
                              <Badge variant="outline">
                                {definition ? STAGE_LABELS[definition.stage] : `rev ${binding.revision}`}
                              </Badge>
                              <Badge variant="outline">order {binding.order}</Badge>
                            </div>
                            <p className="text-xs text-muted-foreground">
                              {binding.rule_id}@{binding.revision} · updated {formatDateTime(binding.updated_at)}
                            </p>
                            <div className="flex flex-wrap gap-2">
                              {scopeSummary.map((item) => (
                                <Badge key={item} variant="outline" className="text-xs">
                                  {item}
                                </Badge>
                              ))}
                            </div>
                          </div>
                          <div className="flex flex-wrap items-center gap-2">
                            <Button variant="outline" onClick={() => startEditBinding(binding)}>
                              Edit
                            </Button>
                            <Button
                              variant="outline"
                              onClick={() => disableBindingMutation.mutate(binding)}
                              disabled={disableBindingMutation.isPending || binding.mode === 'disabled'}
                            >
                              Disable
                            </Button>
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        <Card className="h-fit">
          <CardHeader>
            <CardTitle className="text-lg">
              {editingBinding ? 'Edit Binding' : 'Bind Published Rule'}
            </CardTitle>
            <CardDescription>
              {editingBinding
                ? 'Adjust mode, order, and scope for the selected binding.'
                : 'Choose scope and enforcement mode for the selected published rule.'}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            {selectedDefinition ? (
              <div className="rounded-md border border-border bg-card p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-medium">{selectedDefinition.name}</p>
                  <Badge variant="outline">{STAGE_LABELS[selectedDefinition.stage]}</Badge>
                  <Badge variant="outline">rev {form.revision}</Badge>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">{form.ruleId || selectedDefinition.rule_id}</p>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">Select a published rule from the definition list first.</p>
            )}

            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Mode</Label>
                <Select
                  value={form.mode}
                  onValueChange={(value) => setForm((current) => ({ ...current, mode: value as RuleBindingMode }))}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="shadow">Shadow</SelectItem>
                    <SelectItem value="enforce">Enforce</SelectItem>
                    <SelectItem value="disabled">Disabled</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="rule-order">Order</Label>
                <Input
                  id="rule-order"
                  type="number"
                  min={1}
                  value={form.order}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      order: Math.max(1, Number(event.target.value) || 1),
                    }))
                  }
                />
              </div>
            </div>

            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <Label>State scope</Label>
                {selectedStateId && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setForm((current) => ({
                        ...current,
                        stateIds: current.stateIds.includes(selectedStateId)
                          ? current.stateIds
                          : [...current.stateIds, selectedStateId],
                      }))
                    }
                  >
                    Use selected state
                  </Button>
                )}
              </div>
              <div className="space-y-2 rounded-md border border-border bg-card p-3">
                {states.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No states exist on this agent yet.</p>
                ) : (
                  states.map((state) => (
                    <label key={state.id} className="flex items-center gap-2 text-sm">
                      <Checkbox
                        checked={form.stateIds.includes(state.id)}
                        onCheckedChange={(checked) => handleToggleState(state.id, checked === true)}
                      />
                      <span className="flex-1">{state.name}</span>
                    </label>
                  ))
                )}
              </div>
            </div>

            <div className="space-y-3">
              <Label>Channel scope</Label>
              <div className="grid gap-2 sm:grid-cols-2">
                {CHANNEL_OPTIONS.map((option) => (
                  <label key={option.value} className="flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm">
                    <Checkbox
                      checked={form.channels.includes(option.value)}
                      onCheckedChange={(checked) => handleToggleChannel(option.value, checked === true)}
                    />
                    <span>{option.label}</span>
                  </label>
                ))}
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="rule-tool-refs">Tool refs</Label>
              <Input
                id="rule-tool-refs"
                value={form.toolRefsText}
                onChange={(event) => setForm((current) => ({ ...current, toolRefsText: event.target.value }))}
                placeholder="billing_lookup, schedule_transfer"
              />
              <p className="text-xs text-muted-foreground">Comma-separated. Leave empty to match any tool.</p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="rule-event-types">Event types</Label>
              <Input
                id="rule-event-types"
                value={form.eventTypesText}
                onChange={(event) => setForm((current) => ({ ...current, eventTypesText: event.target.value }))}
                placeholder="message, tool_result"
              />
              <p className="text-xs text-muted-foreground">Comma-separated. Leave empty to match any event type.</p>
            </div>

            <div
              className={`rounded-md border px-3 py-3 text-sm ${
                needsBroadScopeConfirmation
                  ? 'border-amber-500/30 bg-amber-500/5 text-amber-100'
                  : 'border-emerald-500/30 bg-emerald-500/5 text-emerald-100'
              }`}
            >
              <div className="flex items-start gap-2">
                {needsBroadScopeConfirmation ? (
                  <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
                ) : (
                  <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
                )}
                <div className="space-y-2">
                  <p className="font-medium">
                    {needsBroadScopeConfirmation
                      ? 'This binding has broad scope and requires explicit confirmation.'
                      : 'This binding is fully narrowed across agent, state, channel, tool, and event type.'}
                  </p>
                  <label className="flex items-center gap-2 text-sm">
                    <Checkbox
                      checked={form.confirmBroadScope}
                      onCheckedChange={(checked) =>
                        setForm((current) => ({ ...current, confirmBroadScope: checked === true }))
                      }
                    />
                    <span>I understand and want to save this scope.</span>
                  </label>
                </div>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <Button onClick={() => void submit()} disabled={!selectedDefinition || isMutating}>
                {isMutating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Shield className="mr-2 h-4 w-4" />}
                {editingBinding ? 'Update Binding' : 'Create Binding'}
              </Button>
              <Button variant="outline" onClick={resetComposer} disabled={isMutating}>
                Reset
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Effective Program Preview</CardTitle>
          <CardDescription>
            Resolve the exact runtime rule program for this agent scope, then dry-run a single-stage evaluation against
            editable context before you publish.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <div className="space-y-1.5">
              <Label>State</Label>
              <Select
                value={resolutionForm.stateId || '__any__'}
                onValueChange={(value) =>
                  setResolutionForm((current) => ({
                    ...current,
                    stateId: value === '__any__' ? '' : value,
                  }))
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__any__">Any state</SelectItem>
                  {states.map((state) => (
                    <SelectItem key={state.id} value={state.id}>
                      {state.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label>Channel</Label>
              <Select
                value={resolutionForm.channel}
                onValueChange={(value) =>
                  setResolutionForm((current) => ({
                    ...current,
                    channel: value as ResolutionChannel,
                  }))
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="any">Any channel</SelectItem>
                  {CHANNEL_OPTIONS.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="runtime-rule-event-type">Event type</Label>
              <Input
                id="runtime-rule-event-type"
                value={resolutionForm.eventType}
                onChange={(event) =>
                  setResolutionForm((current) => ({ ...current, eventType: event.target.value }))
                }
                placeholder="message"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="runtime-rule-tool-ref">Tool ref</Label>
              <Input
                id="runtime-rule-tool-ref"
                value={resolutionForm.toolRef}
                onChange={(event) =>
                  setResolutionForm((current) => ({ ...current, toolRef: event.target.value }))
                }
                placeholder="billing_lookup"
              />
            </div>
          </div>

          <Tabs value={runtimeTab} onValueChange={(value) => setRuntimeTab(value as 'preview' | 'evaluate')}>
            <TabsList className="grid w-full max-w-md grid-cols-2">
              <TabsTrigger value="preview">Resolved Program</TabsTrigger>
              <TabsTrigger value="evaluate">Dry-run Evaluate</TabsTrigger>
            </TabsList>

            <TabsContent value="preview" className="space-y-4">
              <div className="flex flex-wrap items-center gap-2">
                <Button onClick={() => void handleResolveProgram()} disabled={resolveProgramMutation.isPending}>
                  {resolveProgramMutation.isPending ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <RefreshCw className="mr-2 h-4 w-4" />
                  )}
                  Resolve Effective Program
                </Button>
                <Badge variant="outline">agent:{agentId}</Badge>
                {activeRuntimeScope.step_id && <Badge variant="outline">state:{activeRuntimeScope.step_id}</Badge>}
                {activeRuntimeScope.channel && <Badge variant="outline">channel:{activeRuntimeScope.channel}</Badge>}
                {activeRuntimeScope.event_type && <Badge variant="outline">event:{activeRuntimeScope.event_type}</Badge>}
                {activeRuntimeScope.tool_ref && <Badge variant="outline">tool:{activeRuntimeScope.tool_ref}</Badge>}
              </div>

              {!resolvedProgram ? (
                <div className="rounded-md border border-dashed border-border p-6 text-sm text-muted-foreground">
                  Resolve the effective program to inspect exactly which published rules apply to the current agent
                  scope.
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="grid gap-3 md:grid-cols-3">
                    <div className="rounded-md border border-border p-4">
                      <p className="text-xs uppercase tracking-wide text-muted-foreground">Library</p>
                      <p className="mt-2 text-sm font-medium">{resolvedProgram.library.library_id}</p>
                      <p className="mt-1 text-xs text-muted-foreground">{resolvedProgram.library.version}</p>
                    </div>
                    <div className="rounded-md border border-border p-4">
                      <p className="text-xs uppercase tracking-wide text-muted-foreground">Rules</p>
                      <p className="mt-2 text-2xl font-semibold">{resolvedProgram.library.rules.length}</p>
                    </div>
                    <div className="rounded-md border border-border p-4">
                      <p className="text-xs uppercase tracking-wide text-muted-foreground">Bindings</p>
                      <p className="mt-2 text-2xl font-semibold">{resolvedProgram.bindings.length}</p>
                    </div>
                  </div>

                  {Array.from(resolvedBindingsByStage.entries()).length === 0 ? (
                    <p className="text-sm text-muted-foreground">No bindings matched the current scope.</p>
                  ) : (
                    <div className="space-y-4">
                      {Array.from(resolvedBindingsByStage.entries()).map(([stage, items]) => (
                        <div key={stage} className="space-y-3 rounded-md border border-border p-4">
                          <div className="flex items-center gap-2">
                            <p className="font-medium">{STAGE_LABELS[stage]}</p>
                            <Badge variant="outline">{items.length}</Badge>
                          </div>
                          <div className="space-y-3">
                            {items.map((item) => (
                              <div key={item.binding.binding_id} className="rounded-md border border-border/70 bg-card p-3">
                                <div className="flex flex-wrap items-center gap-2">
                                  <p className="font-medium">{item.name}</p>
                                  <Badge variant="outline">{MODE_LABELS[item.binding.mode]}</Badge>
                                  <Badge variant="outline">order {item.binding.order}</Badge>
                                  <Badge variant="outline">{item.effect}</Badge>
                                </div>
                                <p className="mt-1 text-sm text-muted-foreground">{item.summary}</p>
                                <p className="mt-2 text-xs text-muted-foreground">
                                  {item.binding.rule_id}@{item.binding.revision} · {item.binding.binding_id}
                                </p>
                              </div>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  <details className="rounded-md border border-border bg-card p-4">
                    <summary className="cursor-pointer text-sm font-medium">Program JSON</summary>
                    <pre className="mt-3 overflow-x-auto text-xs text-muted-foreground">{formatJson(resolvedProgram)}</pre>
                  </details>
                </div>
              )}
            </TabsContent>

            <TabsContent value="evaluate" className="space-y-4">
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <div className="space-y-1.5">
                  <Label>Stage</Label>
                  <Select
                    value={evaluationForm.stage}
                    onValueChange={(value) =>
                      setEvaluationForm((current) => ({ ...current, stage: value as RuleStage }))
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {Object.entries(STAGE_LABELS).map(([value, label]) => (
                        <SelectItem key={value} value={value}>
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="runtime-rule-conversation-id">Conversation id</Label>
                  <Input
                    id="runtime-rule-conversation-id"
                    value={evaluationForm.conversationId}
                    onChange={(event) =>
                      setEvaluationForm((current) => ({ ...current, conversationId: event.target.value }))
                    }
                    placeholder="Optional trace identifier"
                  />
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="runtime-rule-turn-count">Turn count</Label>
                  <Input
                    id="runtime-rule-turn-count"
                    type="number"
                    min={0}
                    value={evaluationForm.turnCount}
                    onChange={(event) =>
                      setEvaluationForm((current) => ({
                        ...current,
                        turnCount: Math.max(0, Number(event.target.value) || 0),
                      }))
                    }
                  />
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="runtime-rule-tool-outcome">Tool outcome</Label>
                  <Input
                    id="runtime-rule-tool-outcome"
                    value={evaluationForm.toolOutcome}
                    onChange={(event) =>
                      setEvaluationForm((current) => ({ ...current, toolOutcome: event.target.value }))
                    }
                    placeholder="success"
                  />
                </div>
              </div>

              <div className="grid gap-4 xl:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="runtime-rule-turn-text">Turn text</Label>
                  <Textarea
                    id="runtime-rule-turn-text"
                    value={evaluationForm.turnText}
                    onChange={(event) =>
                      setEvaluationForm((current) => ({ ...current, turnText: event.target.value }))
                    }
                    placeholder="User said..."
                    className="min-h-[120px]"
                  />
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label htmlFor="runtime-rule-current-hour">Current hour</Label>
                    <Input
                      id="runtime-rule-current-hour"
                      type="number"
                      min={0}
                      max={23}
                      value={evaluationForm.currentHour}
                      onChange={(event) =>
                        setEvaluationForm((current) => ({ ...current, currentHour: event.target.value }))
                      }
                      placeholder="UTC hour"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="runtime-rule-current-day">Current day</Label>
                    <Input
                      id="runtime-rule-current-day"
                      value={evaluationForm.currentDay}
                      onChange={(event) =>
                        setEvaluationForm((current) => ({ ...current, currentDay: event.target.value }))
                      }
                      placeholder="Monday"
                    />
                  </div>
                </div>
              </div>

              <div className="grid gap-4 xl:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="runtime-rule-turn-metadata">Turn metadata JSON</Label>
                  <Textarea
                    id="runtime-rule-turn-metadata"
                    value={evaluationForm.turnMetadataText}
                    onChange={(event) =>
                      setEvaluationForm((current) => ({ ...current, turnMetadataText: event.target.value }))
                    }
                    className="min-h-[120px] font-mono text-xs"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="runtime-rule-tool-args">Tool args JSON</Label>
                  <Textarea
                    id="runtime-rule-tool-args"
                    value={evaluationForm.toolArgsText}
                    onChange={(event) =>
                      setEvaluationForm((current) => ({ ...current, toolArgsText: event.target.value }))
                    }
                    className="min-h-[120px] font-mono text-xs"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="runtime-rule-facts">Facts JSON</Label>
                  <Textarea
                    id="runtime-rule-facts"
                    value={evaluationForm.factsText}
                    onChange={(event) =>
                      setEvaluationForm((current) => ({ ...current, factsText: event.target.value }))
                    }
                    className="min-h-[120px] font-mono text-xs"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="runtime-rule-metadata">Runtime metadata JSON</Label>
                  <Textarea
                    id="runtime-rule-metadata"
                    value={evaluationForm.metadataText}
                    onChange={(event) =>
                      setEvaluationForm((current) => ({ ...current, metadataText: event.target.value }))
                    }
                    className="min-h-[120px] font-mono text-xs"
                  />
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <Button onClick={() => void handleEvaluateProgram()} disabled={evaluateProgramMutation.isPending}>
                  {evaluateProgramMutation.isPending ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <Shield className="mr-2 h-4 w-4" />
                  )}
                  Run Dry Evaluation
                </Button>
                <Button
                  variant="outline"
                  onClick={() => {
                    setEvaluationForm(createInitialEvaluationForm())
                    setEvaluationResult(null)
                  }}
                  disabled={evaluateProgramMutation.isPending}
                >
                  Reset Context
                </Button>
              </div>

              {!evaluationResult ? (
                <div className="rounded-md border border-dashed border-border p-6 text-sm text-muted-foreground">
                  Run a dry evaluation to see traces, matched rules, and the terminal effect for the selected stage.
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="grid gap-3 md:grid-cols-3">
                    <div className="rounded-md border border-border p-4">
                      <p className="text-xs uppercase tracking-wide text-muted-foreground">Traces</p>
                      <p className="mt-2 text-2xl font-semibold">{evaluationResult.traces.length}</p>
                    </div>
                    <div className="rounded-md border border-border p-4">
                      <p className="text-xs uppercase tracking-wide text-muted-foreground">Matched rules</p>
                      <p className="mt-2 text-2xl font-semibold">{evaluationResult.matched_rules.length}</p>
                    </div>
                    <div className="rounded-md border border-border p-4">
                      <p className="text-xs uppercase tracking-wide text-muted-foreground">Terminal effect</p>
                      <p className="mt-2 text-sm font-medium">{formatEffect(evaluationResult.terminal_effect)}</p>
                    </div>
                  </div>

                  <div className="space-y-3">
                    <p className="text-sm font-medium">Matched rules</p>
                    {evaluationResult.matched_rules.length === 0 ? (
                      <p className="text-sm text-muted-foreground">No rules matched this evaluation context.</p>
                    ) : (
                      evaluationResult.matched_rules.map((match) => (
                        <div key={`${match.binding_id}-${match.rule_id}`} className="rounded-md border border-border bg-card p-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="font-medium">{match.rule_name}</p>
                            <Badge variant="outline">{MODE_LABELS[match.mode]}</Badge>
                            <Badge variant="outline">{formatEffect(match.effect)}</Badge>
                          </div>
                          <p className="mt-1 text-xs text-muted-foreground">
                            {match.rule_id}@{match.revision} · binding {match.binding_id}
                          </p>
                        </div>
                      ))
                    )}
                  </div>

                  <div className="space-y-3">
                    <p className="text-sm font-medium">Trace outcomes</p>
                    {evaluationResult.traces.length === 0 ? (
                      <p className="text-sm text-muted-foreground">No trace records were emitted.</p>
                    ) : (
                      evaluationResult.traces.map((trace) => (
                        <div key={`${trace.binding_id}-${trace.rule_id}-${trace.revision}`} className="rounded-md border border-border bg-card p-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="font-medium">{trace.rule_id}</p>
                            <Badge variant="outline">{trace.outcome}</Badge>
                            <Badge variant="outline">{MODE_LABELS[trace.mode]}</Badge>
                            {trace.effect_kind && <Badge variant="outline">{trace.effect_kind}</Badge>}
                          </div>
                          {trace.detail && <p className="mt-1 text-sm text-muted-foreground">{trace.detail}</p>}
                        </div>
                      ))
                    )}
                  </div>

                  <details className="rounded-md border border-border bg-card p-4">
                    <summary className="cursor-pointer text-sm font-medium">Evaluation JSON</summary>
                    <pre className="mt-3 overflow-x-auto text-xs text-muted-foreground">{formatJson(evaluationResult)}</pre>
                  </details>
                </div>
              )}
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  )
}
