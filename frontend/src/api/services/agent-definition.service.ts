import { ApiError, apiClient } from '../client'
import type { AgentDocument } from '@/types/agent-document'
import type {
  AgentCreateRequest,
  AgentDefinition,
  AgentDefinitionStep,
  AgentDefinitionTargetResponse,
  BehavioralPersona,
  CosmeticPersona,
  EvaluationRun,
  EvaluationRunCreateRequest,
  AgentDraftCreateRequest,
  AgentEvaluationPolicyResponse,
  AgentPublishReadiness,
  AgentValidationReport,
  AgentVersionDiff,
  AgentMetadataPatchRequest,
  AgentOperationalMetrics,
  AgentReplayResponse,
  AgentSettingsPatchRequest,
  AgentSettingsResponse,
  AgentSummary,
  AgentVersionSummary,
  AgentVersionTargetResponse,
  AgentWidgetConfig,
  AgentVersionStatus,
  FactDef,
  SimulationFixture,
  SimulationFixtureCreateRequest,
  SimulationTurnInput,
  ToolBinding,
  Transition,
  VoiceCatalogPage,
  VoiceCloneCreatedResponse,
  VoiceLibraryFilters,
} from '@/types/agent-definition'
import { DEFAULT_BEHAVIORAL_PERSONA } from '@/types/agent-definition'

// ─── Backend ↔ canvas adapters ───────────────────────────────────────────────
// Backend stores the agent as nested AgentDocument.scenarios[].steps[].
// The canvas authors against a single flat AgentDefinition.steps[] with
// author-time metadata that has no direct backend equivalent. These
// adapters convert between the two shapes.
//
// Per docs/generic-state-redesign/01-generic-step-canvas-adr.md, step kind
// is derived on demand by deriveStepKind() — there is no authored step type.
// The adapters preserve only the optional capability fields (handoff,
// terminal_disposition, action_config, fact_requirements).

function stepToCanvasStep(step: any): AgentDefinitionStep {
  const transitions: Transition[] = (Array.isArray(step?.transitions) ? step.transitions : []).map(
    (t: any) => ({
      id: t.id,
      when: t.when ?? { kind: 'otherwise' },
      to: t.to_step_id ?? t.to ?? '',
      natural_reason: t.label ?? null,
      when_to_use: null,
      priority: typeof t.priority === 'number' ? t.priority : 100,
      branch_intent: null,
    }),
  )

  return {
    id: step?.id ?? '',
    name: step?.name ?? step?.id ?? '',
    accepted_inputs: [],
    event_hints: step?.event_hints ?? {},
    fact_requirements: step?.fact_requirements ?? [],
    tool_policy: step?.tool_policy ?? [],
    response_policy: step?.response_policy ?? {
      answer_directly_first: false,
      ask_clarifying_question_only_if_needed: false,
      voice_style: 'balanced',
    },
    guards: step?.guards ?? [],
    transitions,
    say_on_entry: step?.say ?? null,
    terminal_disposition: step?.completion?.disposition ?? null,
  }
}

function adaptToAgentDefinition(raw: any): AgentDefinitionTargetResponse {
  const document = raw?.document ?? {}
  const scenarios: any[] = Array.isArray(document?.scenarios) ? document.scenarios : []

  const startScenarioId = document?.start_scenario_id
  const startScenario = scenarios.find((s) => s?.id === startScenarioId) ?? scenarios[0]
  const startStepId: string = startScenario?.start_step_id ?? ''

  const steps: AgentDefinitionStep[] = scenarios.flatMap((scenario) =>
    (Array.isArray(scenario?.steps) ? scenario.steps : []).map((step: any) => stepToCanvasStep(step)),
  )

  const definition: AgentDefinition = {
    id: raw?.agent_id ?? '',
    name: raw?.agent_name ?? '',
    version: document?.version ?? '3.0',
    start_step_id: startStepId,
    steps,
    fact_schema: Array.isArray(document?.fact_schema) ? document.fact_schema : [],
    followup_handlers: document?.metadata?.followup_handlers ?? undefined,
    agent_capability_manifest: document?.agent_capability_manifest ?? null,
  }

  const v = raw?.version ?? {}
  const version: AgentVersionSummary = {
    version_id: v.version_id ?? '',
    agent_id: raw?.agent_id ?? '',
    status: v.status ?? 'draft',
    version_number: typeof v.version_number === 'number' ? v.version_number : 0,
    schema_version: v.schema_version ?? '',
    based_on_version_id: v.based_on_version_id ?? null,
    published_at: v.published_at ?? null,
    created_at: v.created_at ?? '',
    updated_at: v.updated_at ?? '',
    is_current_draft: !!v.is_current_draft,
    is_current_published: !!v.is_current_published,
  }

  return { definition, version }
}

// Reverse adapter — canvas-flat AgentDefinitionStep back to backend Step shape.
// Lossy: AgentDefinitionStep has fields with no Step equivalent
// (accepted_inputs, say_on_transition, ask_for_fact, repair_response,
// activity_label, etc.) — they're dropped. Round-tripping a step through
// stepToCanvasStep → canvasStepToStep loses authored detail beyond the core
// fields.
function canvasStepToStep(step: AgentDefinitionStep): any {
  const transitions = (step.transitions ?? []).map((t) => ({
    id: t.id,
    when: t.when,
    to_step_id: t.to,
    label: t.natural_reason ?? null,
    priority: typeof t.priority === 'number' ? t.priority : 100,
  }))
  const out: any = {
    id: step.id,
    name: step.name,
    transitions,
    description: null,
    say: step.say_on_entry ?? null,
    guards: step.guards ?? [],
    fact_requirements: step.fact_requirements ?? [],
    tool_policy: step.tool_policy ?? [],
    action_config: null,
    response_policy: step.response_policy,
    event_hints: step.event_hints ?? {},
    workload_class: 'interactive',
    execution_isolation: 'subprocess',
  }
  // Terminal kind is signaled by a non-null terminal_disposition (which the
  // forward adapter pulls from step.completion.disposition).
  if (step.terminal_disposition) {
    out.completion = { disposition: step.terminal_disposition, summary: null }
  }
  return out
}

// Whole-document mutate helper. The backend doesn't expose granular
// step CRUD — every authored change goes through PUT
// /agents/{id}/agent-document with the entire document. This helper fetches
// the current draft, applies the mutator client-side, PUTs the result, then
// re-reads the version target so the caller still gets an
// AgentDefinitionTargetResponse.
async function mutateAgentDocumentInternal(
  agentId: string,
  mutator: (doc: any) => any,
): Promise<AgentDefinitionTargetResponse> {
  const current = await apiClient.get<{ document: any }>(`/agents/${agentId}/agent-document`, {
    params: { target: 'draft' },
  })
  const nextDoc = mutator(current?.document ?? {})
  await apiClient.put<unknown>(`/agents/${agentId}/agent-document`, nextDoc)
  const refreshed = await apiClient.get<unknown>(`/agents/${agentId}`, {
    params: { target: 'draft' },
  })
  return adaptToAgentDefinition(refreshed)
}

// Mutate a single step inside a document by id (across all scenarios).
function mapStepInDocument(doc: any, stepId: string, fn: (step: any) => any | null): any {
  const scenarios = (doc?.scenarios ?? []).map((scenario: any) => {
    const steps = (scenario?.steps ?? [])
      .map((step: any) => (step?.id === stepId ? fn(step) : step))
      .filter((step: any) => step != null)
    return { ...scenario, steps }
  })
  return { ...doc, scenarios }
}

class AgentDefinitionService {
  async listAgents(): Promise<AgentSummary[]> {
    const response = await apiClient.get<AgentSummary[]>('/agents')
    return Array.isArray(response) ? response : []
  }

  async createAgent(payload: AgentCreateRequest): Promise<AgentVersionTargetResponse> {
    return apiClient.post<AgentVersionTargetResponse>('/agents', payload)
  }

  async getAgent(agentId: string, target: AgentVersionStatus = 'draft'): Promise<AgentVersionTargetResponse> {
    return apiClient.get<AgentVersionTargetResponse>(`/agents/${agentId}`, {
      params: { target },
    })
  }

  /**
   * Fetch the agent and adapt the response into the canvas-flat
   * AgentDefinition shape (single steps[] list, with author-time fields).
   * Use this when authoring on the canvas; use getAgent() when you need
   * the raw nested AgentDocument shape.
   */
  async getAgentDefinition(
    agentId: string,
    target: AgentVersionStatus = 'draft',
  ): Promise<AgentDefinitionTargetResponse> {
    const raw = await apiClient.get<unknown>(`/agents/${agentId}`, {
      params: { target },
    })
    return adaptToAgentDefinition(raw)
  }

  /**
   * Save the entire canvas-flat AgentDefinition back to the backend. Wraps
   * mutateAgentDocumentInternal — converts AgentDefinition → AgentDocument
   * shape (single scenario containing all steps) and replaces the document.
   *
   * Lossy on read (canvas-flat shape collapses scenarios into one), so
   * round-tripping a multi-scenario document through this method consolidates
   * it. Use updateAgentDocument() if you need to preserve scenario structure.
   */
  async updateAgentDefinition(
    agentId: string,
    definition: AgentDefinition,
  ): Promise<AgentDefinitionTargetResponse> {
    return mutateAgentDocumentInternal(agentId, (doc) => {
      const scenarios: any[] = Array.isArray(doc?.scenarios) ? doc.scenarios : []
      const startScenarioId = doc?.start_scenario_id ?? scenarios[0]?.id
      const updatedScenarios = scenarios.map((scenario: any) =>
        scenario?.id === startScenarioId
          ? {
              ...scenario,
              start_step_id: definition.start_step_id,
              steps: definition.steps.map(canvasStepToStep),
            }
          : scenario,
      )
      return {
        ...doc,
        scenarios: updatedScenarios,
        fact_schema: definition.fact_schema,
        agent_capability_manifest:
          definition.agent_capability_manifest ?? doc?.agent_capability_manifest ?? null,
      }
    })
  }

  /**
   * Replace the current draft's fact_schema with the given list. Wraps
   * mutateAgentDocumentInternal — fetches the draft, swaps fact_schema,
   * writes back.
   */
  async replaceAgentFacts(
    agentId: string,
    factSchema: FactDef[],
  ): Promise<AgentDefinitionTargetResponse> {
    return mutateAgentDocumentInternal(agentId, (doc) => ({ ...doc, fact_schema: factSchema }))
  }

  /**
   * Append a new step to the start scenario. Canvas-flat input: the helper
   * converts AgentDefinitionStep → backend Step shape before writing.
   */
  async addAgentStep(
    agentId: string,
    step: AgentDefinitionStep,
  ): Promise<AgentDefinitionTargetResponse> {
    return mutateAgentDocumentInternal(agentId, (doc) => {
      const scenarios: any[] = Array.isArray(doc?.scenarios) ? doc.scenarios : []
      const targetScenarioId = doc?.start_scenario_id ?? scenarios[0]?.id
      const updated = scenarios.map((scenario: any) =>
        scenario?.id === targetScenarioId
          ? { ...scenario, steps: [...(scenario.steps ?? []), canvasStepToStep(step)] }
          : scenario,
      )
      return { ...doc, scenarios: updated }
    })
  }

  /** Replace a single step in place. Canvas-flat input. */
  async replaceAgentStep(
    agentId: string,
    stepId: string,
    step: AgentDefinitionStep,
  ): Promise<AgentDefinitionTargetResponse> {
    return mutateAgentDocumentInternal(agentId, (doc) =>
      mapStepInDocument(doc, stepId, () => canvasStepToStep(step)),
    )
  }

  /**
   * Delete a step + strip any sibling transitions targeting it. The orphaned
   * transitions would otherwise fail validation on the next read.
   */
  async deleteAgentStep(agentId: string, stepId: string): Promise<AgentDefinitionTargetResponse> {
    return mutateAgentDocumentInternal(agentId, (doc) => {
      const stripped = mapStepInDocument(doc, stepId, () => null)
      const scenarios = (stripped?.scenarios ?? []).map((scenario: any) => ({
        ...scenario,
        steps: (scenario?.steps ?? []).map((step: any) => ({
          ...step,
          transitions: (step?.transitions ?? []).filter((t: any) => t?.to_step_id !== stepId),
        })),
      }))
      return { ...stripped, scenarios }
    })
  }

  /** Replace just the transitions array on a step. Canvas-flat Transition[]. */
  async replaceAgentStepTransitions(
    agentId: string,
    stepId: string,
    transitions: Transition[],
  ): Promise<AgentDefinitionTargetResponse> {
    const stepTransitions = transitions.map((t) => ({
      id: t.id,
      when: t.when,
      to_step_id: t.to,
      label: t.natural_reason ?? null,
      priority: typeof t.priority === 'number' ? t.priority : 100,
    }))
    return mutateAgentDocumentInternal(agentId, (doc) =>
      mapStepInDocument(doc, stepId, (step) => ({ ...step, transitions: stepTransitions })),
    )
  }

  /** Replace just the tool_policy array on a step. */
  async replaceAgentStepToolPolicy(
    agentId: string,
    stepId: string,
    toolPolicy: ToolBinding[],
  ): Promise<AgentDefinitionTargetResponse> {
    return mutateAgentDocumentInternal(agentId, (doc) =>
      mapStepInDocument(doc, stepId, (step) => ({ ...step, tool_policy: toolPolicy })),
    )
  }

  async getAgentValidation(agentId: string, target: AgentVersionStatus = 'draft'): Promise<AgentValidationReport> {
    return apiClient.get<AgentValidationReport>(`/agents/${agentId}/validation`, {
      params: { target },
    })
  }

  async getAgentSettings(agentId: string): Promise<AgentSettingsResponse> {
    return apiClient.get<AgentSettingsResponse>(`/agents/${agentId}/settings`)
  }

  async getAgentDocument(agentId: string): Promise<AgentDocument | null> {
    try {
      const response = await apiClient.get<{
        agent_id: string
        target: AgentVersionStatus
        document: AgentDocument
      }>(`/agents/${agentId}/agent-document`)
      return response.document
    } catch (error: unknown) {
      if (error instanceof ApiError && error.status === 404) return null
      throw error
    }
  }

  async updateAgentDocument(
    agentId: string,
    document: AgentDocument,
  ): Promise<AgentDocument> {
    const response = await apiClient.put<{
      agent_id: string
      target: AgentVersionStatus
      document: AgentDocument
    }>(`/agents/${agentId}/agent-document`, document)
    return response.document
  }

  async updateAgentSettings(
    agentId: string,
    payload: AgentSettingsPatchRequest,
  ): Promise<AgentSettingsResponse> {
    return apiClient.patch<AgentSettingsResponse>(`/agents/${agentId}/settings`, payload)
  }

  /** Update the cosmetic persona only. Live-edit, applies immediately. */
  async updateCosmeticPersona(
    agentId: string,
    persona: CosmeticPersona | null,
  ): Promise<AgentSettingsResponse> {
    return apiClient.patch<AgentSettingsResponse>(`/agents/${agentId}/settings`, { persona })
  }

  /** Read the behavioural persona from the draft agent document.
   * Returns the default values when no persona is authored. */
  async getBehavioralPersona(
    agentId: string,
  ): Promise<{ persona: BehavioralPersona; document: AgentDocument | null }> {
    const document = await this.getAgentDocument(agentId)
    const raw = (document?.metadata?.persona ?? null) as Partial<BehavioralPersona> | null
    const persona: BehavioralPersona = raw
      ? {
          formality: raw.formality ?? DEFAULT_BEHAVIORAL_PERSONA.formality,
          emoji_policy: raw.emoji_policy ?? DEFAULT_BEHAVIORAL_PERSONA.emoji_policy,
          restricted_topics: Array.isArray(raw.restricted_topics) ? raw.restricted_topics : [],
          // Phase 2c — fall back to the schema default for agents whose
          // documents predate the topic_enforcement field.
          topic_enforcement: raw.topic_enforcement ?? DEFAULT_BEHAVIORAL_PERSONA.topic_enforcement,
          // Phase 2a-base — voice fields. Defaults preserve byte-identical
          // behaviour for agents whose documents predate them.
          voice_provider: raw.voice_provider ?? DEFAULT_BEHAVIORAL_PERSONA.voice_provider,
          voice_id: raw.voice_id ?? DEFAULT_BEHAVIORAL_PERSONA.voice_id,
          voice_speed: typeof raw.voice_speed === 'number'
            ? raw.voice_speed
            : DEFAULT_BEHAVIORAL_PERSONA.voice_speed,
          voice_monthly_budget_cents: raw.voice_monthly_budget_cents ?? null,
          // Phase 2b — multi-language fields. All defaults preserve
          // byte-identical Phase 2a behaviour for agents whose
          // documents predate them (English-only, no auto-switch).
          primary_language: raw.primary_language ?? DEFAULT_BEHAVIORAL_PERSONA.primary_language,
          allowed_languages: Array.isArray(raw.allowed_languages)
            ? raw.allowed_languages
            : [...DEFAULT_BEHAVIORAL_PERSONA.allowed_languages],
          auto_switch_language: raw.auto_switch_language ?? DEFAULT_BEHAVIORAL_PERSONA.auto_switch_language,
          language_switch_confidence_threshold:
            typeof raw.language_switch_confidence_threshold === 'number'
              ? raw.language_switch_confidence_threshold
              : DEFAULT_BEHAVIORAL_PERSONA.language_switch_confidence_threshold,
          language_switch_min_chars:
            typeof raw.language_switch_min_chars === 'number'
              ? raw.language_switch_min_chars
              : DEFAULT_BEHAVIORAL_PERSONA.language_switch_min_chars,
          language_switch_debounce_turns:
            typeof raw.language_switch_debounce_turns === 'number'
              ? raw.language_switch_debounce_turns
              : DEFAULT_BEHAVIORAL_PERSONA.language_switch_debounce_turns,
          language_switch_policy:
            raw.language_switch_policy ?? DEFAULT_BEHAVIORAL_PERSONA.language_switch_policy,
          unsupported_language_policy:
            raw.unsupported_language_policy ?? DEFAULT_BEHAVIORAL_PERSONA.unsupported_language_policy,
          voice_id_overrides:
            raw.voice_id_overrides && typeof raw.voice_id_overrides === 'object'
              ? { ...raw.voice_id_overrides }
              : {},
          locale_code: raw.locale_code ?? DEFAULT_BEHAVIORAL_PERSONA.locale_code,
          cultural_calendar_enabled: !!raw.cultural_calendar_enabled,
        }
      : { ...DEFAULT_BEHAVIORAL_PERSONA }
    return { persona, document }
  }

  /** Write the behavioural persona to the draft agent document. The caller
   * passes the current draft document so we don't squash concurrent edits to
   * sibling fields (scenarios, fact_schema, etc.). */
  async updateBehavioralPersona(
    agentId: string,
    persona: BehavioralPersona,
    document: AgentDocument,
  ): Promise<AgentDocument> {
    const next: AgentDocument = {
      ...document,
      metadata: {
        ...(document.metadata ?? {}),
        persona,
      },
    }
    return this.updateAgentDocument(agentId, next)
  }

  async updateAgentMetadata(
    agentId: string,
    payload: AgentMetadataPatchRequest,
  ): Promise<AgentVersionTargetResponse> {
    return apiClient.patch<AgentVersionTargetResponse>(`/agents/${agentId}/metadata`, payload)
  }

  /** Phase 2a-base — fetch a page of voices from the configured
   * provider. Cached on the API edge for 5 minutes; the picker UI
   * still re-fetches when filters change. */
  async listVoiceLibrary(
    filters?: VoiceLibraryFilters,
    cursor?: string,
    limit = 50,
  ): Promise<VoiceCatalogPage> {
    const params: Record<string, string | number | undefined> = { limit }
    if (filters?.language) params.language = filters.language
    if (filters?.gender) params.gender = filters.gender
    if (filters?.accent) params.accent = filters.accent
    if (cursor) params.cursor = cursor
    return apiClient.get<VoiceCatalogPage>('/persona/voices/library', { params })
  }

  /** Returns the URL the picker UI passes directly to an <audio> element
   * for preview playback. The api endpoint streams MP3 bytes and sets
   * a 24h Cache-Control so repeat clicks don't re-synthesize. */
  voicePreviewUrl(voiceId: string): string {
    // URL-encode the voice_id since it contains hyphens and may
    // eventually contain provider-specific characters.
    return `/persona/voices/${encodeURIComponent(voiceId)}/preview`
  }

  /** Phase 2a-cloning — submit a voice cloning request. Wizard ALWAYS
   * uploads the consent recording; reference audio is optional and
   * defaults to None server-side (the consent recording itself can
   * also serve as the reference clip). Server enforces a 1MB hard
   * cap + MIME allowlist; the wizard pre-validates locally for fast
   * feedback. */
  async cloneVoice(payload: {
    displayName: string
    language: string
    agentId?: string | null
    consentAudio: Blob
    referenceAudio?: Blob | null
  }): Promise<VoiceCloneCreatedResponse> {
    const formData = new FormData()
    formData.append('display_name', payload.displayName)
    formData.append('language', payload.language)
    if (payload.agentId) {
      formData.append('agent_id', payload.agentId)
    }
    formData.append('consent_audio', payload.consentAudio)
    if (payload.referenceAudio) {
      formData.append('reference_audio', payload.referenceAudio)
    }
    return apiClient.post<VoiceCloneCreatedResponse>(
      '/persona/voices/clone',
      formData,
    )
  }

  /** Soft-delete a tenant clone. Idempotent on the server (204
   * regardless of whether the row existed). The clone's encrypted
   * key + consent audio remain in the DB for the seven-year
   * compliance retention window. */
  async deleteVoiceClone(cloneId: string): Promise<void> {
    await apiClient.delete<void>(
      `/persona/voices/clones/${encodeURIComponent(cloneId)}`,
    )
  }

  /** Phase 2d — upload a persona avatar image. The server validates
   * format / size / dimensions / MIME-vs-magic-bytes / EXIF strip;
   * the wizard pre-validates client-side for fast feedback. Returns
   * the relative URL the picker UI should set on
   * ``CosmeticPersona.avatar_url``. */
  async uploadPersonaAvatar(
    agentId: string,
    file: File,
  ): Promise<{
    agent_id: string
    avatar_url: string
    content_type: string
    width: number
    height: number
    updated_at: string
  }> {
    const formData = new FormData()
    formData.append('file', file)
    return apiClient.post(
      `/agents/${encodeURIComponent(agentId)}/persona/avatar`,
      formData,
    )
  }

  /** Returns the relative URL the <img> tag points at. Same shape
   * the server returns from uploadPersonaAvatar; provided as a
   * helper so callers can build the URL without re-uploading. */
  personaAvatarUrl(agentId: string): string {
    return `/agents/${encodeURIComponent(agentId)}/persona/avatar`
  }

  async listAgentVersions(agentId: string): Promise<AgentVersionSummary[]> {
    const response = await apiClient.get<AgentVersionSummary[]>(`/agents/${agentId}/versions`)
    return Array.isArray(response) ? response : []
  }

  async getAgentDiff(
    agentId: string,
    sourceVersionId?: string,
    againstVersionId?: string,
  ): Promise<AgentVersionDiff> {
    return apiClient.get<AgentVersionDiff>(`/agents/${agentId}/diff`, {
      params: {
        ...(sourceVersionId ? { source_version_id: sourceVersionId } : {}),
        ...(againstVersionId ? { against_version_id: againstVersionId } : {}),
      },
    })
  }

  async getAgentPublishReview(agentId: string): Promise<AgentPublishReadiness> {
    return apiClient.get<AgentPublishReadiness>(`/agents/${agentId}/publish-review`)
  }

  async getAgentEvaluationPolicy(agentId: string): Promise<AgentEvaluationPolicyResponse> {
    return apiClient.get<AgentEvaluationPolicyResponse>(`/agents/${agentId}/evaluation-policy`)
  }

  async updateAgentEvaluationPolicy(
    agentId: string,
    payload: {
      minimum_pass_rate_ratio?: number
      allow_warning_failures?: boolean
      max_qualified_run_age_hours?: number | null
    },
  ): Promise<AgentEvaluationPolicyResponse> {
    return apiClient.patch<AgentEvaluationPolicyResponse>(`/agents/${agentId}/evaluation-policy`, payload)
  }

  async replayAgent(
    agentId: string,
    payload: {
      utterances?: string[]
      turns?: SimulationTurnInput[]
      channel?: string
      starting_step_id?: string | null
      starting_scenario_id?: string | null
      seed_facts?: Record<string, unknown>
    },
  ): Promise<AgentReplayResponse> {
    return apiClient.post<AgentReplayResponse>(`/agents/${agentId}/replay`, payload)
  }

  async listSimulationFixtures(
    agentId: string,
    params?: { is_active?: boolean; gate_required?: boolean },
  ): Promise<SimulationFixture[]> {
    const response = await apiClient.get<SimulationFixture[]>(`/agents/${agentId}/simulation-fixtures`, { params })
    return Array.isArray(response) ? response : []
  }

  async createSimulationFixture(
    agentId: string,
    payload: SimulationFixtureCreateRequest,
  ): Promise<SimulationFixture> {
    return apiClient.post<SimulationFixture>(`/agents/${agentId}/simulation-fixtures`, payload)
  }

  async createEvaluationRun(
    agentId: string,
    payload: EvaluationRunCreateRequest,
  ): Promise<EvaluationRun> {
    return apiClient.post<EvaluationRun>(`/agents/${agentId}/evaluation-runs`, payload)
  }

  async listEvaluationRuns(
    agentId: string,
    params?: { agent_version_id?: string; gate_eligible?: boolean },
  ): Promise<EvaluationRun[]> {
    const response = await apiClient.get<EvaluationRun[]>(`/agents/${agentId}/evaluation-runs`, { params })
    return Array.isArray(response) ? response : []
  }

  async stopEvaluationRun(evaluationRunId: string): Promise<EvaluationRun> {
    return apiClient.post<EvaluationRun>(`/evaluation-runs/${evaluationRunId}/stop`, {})
  }

  async getLatestQualifiedRun(
    agentId: string,
    agentVersionId?: string,
  ): Promise<EvaluationRun> {
    return apiClient.get<EvaluationRun>(`/agents/${agentId}/latest-qualified-run`, {
      params: agentVersionId ? { agent_version_id: agentVersionId } : undefined,
    })
  }

  async getMetrics(agentId: string, agentVersionId?: string): Promise<AgentOperationalMetrics> {
    return apiClient.get<AgentOperationalMetrics>(`/agents/${agentId}/metrics`, {
      params: agentVersionId ? { agent_version_id: agentVersionId } : undefined,
    })
  }

  async getWidgetConfig(agentId: string): Promise<AgentWidgetConfig> {
    return apiClient.get<AgentWidgetConfig>('/public/widget/config', {
      params: { agent_id: agentId },
    })
  }

  async createAgentDraft(
    agentId: string,
    sourceVersionId?: string,
  ): Promise<AgentVersionTargetResponse> {
    const payload: AgentDraftCreateRequest = {
      source_version_id: sourceVersionId ?? null,
    }
    return apiClient.post<AgentVersionTargetResponse>(`/agents/${agentId}/draft`, payload)
  }

  async deleteAgent(agentId: string): Promise<void> {
    return apiClient.delete<void>(`/agents/${agentId}`)
  }

  async publishAgent(agentId: string): Promise<AgentVersionTargetResponse> {
    return apiClient.post<AgentVersionTargetResponse>(`/agents/${agentId}/publish`)
  }

  async unpublishAgent(agentId: string): Promise<AgentVersionTargetResponse> {
    return apiClient.post<AgentVersionTargetResponse>(`/agents/${agentId}/unpublish`)
  }
}

export const agentDefinitionService = new AgentDefinitionService()
