/**
 * Atlas AI Service
 *
 * Talks to the backend Atlas API at src/ruhu/atlas_api.py.
 *
 * The backend model is: sessions (per agent + scope) → messages + turns +
 * events. Each turn proposes typed deltas, which require permission decisions
 * and an explicit apply step.
 *
 * Types here mirror the Pydantic models at src/ruhu/atlas_protocol.py and
 * src/ruhu/atlas_models.py. Keep field names and literal unions in sync.
 */

import { apiClient } from '../client'

// =============================================================================
// Literal unions (mirror src/ruhu/atlas_models.py)
// =============================================================================

export type AtlasScope = 'agent_authoring' | 'provisioning' | 'validation' | 'operations'

export type AtlasSessionStatus = 'active' | 'completed' | 'blocked' | 'archived'

export type AtlasMessageRole = 'user' | 'assistant' | 'system' | 'tool'

export type AtlasEventType =
  | 'start'
  | 'token'
  | 'tool_start'
  | 'tool_done'
  | 'permission_request'
  | 'progress'
  | 'complete'
  | 'error'

export type AtlasPermissionKind =
  | 'apply_deltas'
  | 'provision_resource'
  | 'execute_side_effecting_tool'
  | 'execute_code'
  | 'destructive_change'

export type AtlasPermissionStatus = 'pending' | 'approved' | 'denied' | 'expired'

export type AtlasNextAction =
  | 'ask_questions'
  | 'ready_to_review_changes'
  | 'ready_to_provision'
  | 'ready_to_validate'
  | 'complete'
  | 'blocked'

// =============================================================================
// Canonical delta + proposed_changes
//
// AtlasPermissionCard and atlas-shared.ts depend on these names — do not
// rename without updating those consumers.
// =============================================================================

export interface CanonicalAtlasDelta {
  delta_id: string
  operation: string
  change_type: string
  summary?: string | null
  payload: Record<string, unknown>
  status: 'pending' | 'approved' | 'rejected' | 'applied' | 'failed' | 'proposed' | 'superseded' | string
}

export interface CanonicalAtlasProposedChanges {
  agent_metadata_deltas: CanonicalAtlasDelta[]
  scenario_deltas: CanonicalAtlasDelta[]
  step_deltas: CanonicalAtlasDelta[]
  scenario_route_deltas: CanonicalAtlasDelta[]
  channel_policy_deltas: CanonicalAtlasDelta[]
  rule_deltas: CanonicalAtlasDelta[]
  knowledge_deltas: CanonicalAtlasDelta[]
  integration_binding_deltas: CanonicalAtlasDelta[]
}

export interface CanonicalAtlasPermissionRequest {
  request_id: string
  kind: AtlasPermissionKind | string
  status: AtlasPermissionStatus | string
  reason: string
  risk_summary?: string | null
  requested_actions: string[]
  delta_ids: string[]
  scope_ref: Record<string, unknown>
  created_at?: string | null
  expires_at?: string | null
}

// =============================================================================
// Sessions
// =============================================================================

export interface AtlasSessionStartRequest {
  scope: AtlasScope
  agent_id: string
  agent_version_id?: string
  scenario_id?: string
  step_id?: string
  initial_message?: string
}

export interface AtlasSessionResponse {
  session_id: string
  status: AtlasSessionStatus
  scope: AtlasScope
  agent_id: string
  agent_version_id: string | null
  created_by: string | null
  scenario_id: string | null
  step_id: string | null
  created_at: string
  updated_at: string
}

export interface AtlasSessionsPageResponse {
  sessions: AtlasSessionResponse[]
  total_count: number
  has_more: boolean
}

export interface AtlasArchiveSessionResponse {
  session_id: string
  status: AtlasSessionStatus
  archived_at: string
}

// =============================================================================
// Messages and events
// =============================================================================

export interface AtlasMessageItem {
  message_id: string
  role: AtlasMessageRole
  content: string
  sequence_number: number
  metadata: Record<string, unknown>
  created_at: string
}

export interface AtlasMessagesPageResponse {
  session_id: string
  messages: AtlasMessageItem[]
  has_more: boolean
  total_count: number
}

export interface AtlasEventEnvelope {
  event_id: string
  session_id: string
  sequence_number: number
  type: AtlasEventType
  created_at: string
  payload: Record<string, unknown>
}

export interface AtlasEventsPageResponse {
  session_id: string
  events: AtlasEventEnvelope[]
  has_more: boolean
  total_count: number
}

// =============================================================================
// Turn input
// =============================================================================

export interface AtlasSelectedContext {
  agent_id?: string
  agent_version_id?: string
  scenario_id?: string
  step_id?: string
  conversation_id?: string
  trace_id?: string
}

export type AtlasAttachmentKind =
  | 'document'
  | 'image'
  | 'spec'
  | 'transcript'
  | 'agent_document_json'
  | 'json_brief'
  | 'workflow_description'

export interface AtlasAttachmentInput {
  attachment_id: string
  kind: AtlasAttachmentKind
  display_name: string
  source_url?: string
  metadata?: Record<string, unknown>
}

export type AtlasAPIDiscoverySourceType =
  | 'openapi_url'
  | 'swagger_url'
  | 'postman_url'
  | 'website_url'
  | 'uploaded_spec'
  | 'uploaded_postman'
  | 'pasted_schema'
  | 'pasted_postman'

export interface AtlasAPIDiscoveryRequest {
  request_id: string
  source_type: AtlasAPIDiscoverySourceType
  source_value: string
  intent?: string
}

export interface AtlasReviewDecision {
  delta_id: string
  decision: 'approved' | 'rejected'
  note?: string | null
}

export interface AtlasApplyRequest {
  delta_ids: string[]
  apply_note?: string
  confirmed_by?: string
}

export interface AtlasPermissionDecision {
  request_id: string
  decision: 'approved' | 'denied'
  reason?: string
}

export interface BlockingQuestion {
  question_id: string
  question: string
  help_text?: string | null
  options?: string[] | null
  required: boolean
  target_ref?: string | null
}

export interface AtlasTurnRequest {
  session_id: string
  message?: string
  question_answers?: Record<string, unknown>
  selected_context?: AtlasSelectedContext
  attachments?: AtlasAttachmentInput[]
  api_discovery_requests?: AtlasAPIDiscoveryRequest[]
  review_decisions?: AtlasReviewDecision[]
  apply_request?: AtlasApplyRequest
  permission_decisions?: AtlasPermissionDecision[]
}

// =============================================================================
// Turn response
// =============================================================================

export interface AtlasDependency {
  key: string
  kind: 'integration' | 'tool' | 'knowledge' | 'rule' | 'agent' | 'scenario' | 'step' | 'channel_policy'
  display_name: string
  status: 'connected' | 'available' | 'requires_auth' | 'missing' | 'configured' | 'invalid'
  blocking: boolean
  reason?: string | null
  suggested_action?: string | null
  reference_ids: string[]
}

export interface AtlasBlocker {
  code: string
  message: string
  blocking: boolean
  reference_ids: string[]
}

export interface AtlasDerivedImpact {
  [key: string]: unknown
}

export interface AtlasValidationCheck {
  [key: string]: unknown
}

export interface AtlasValidationResult {
  blocking?: boolean
  errors: AtlasValidationCheck[]
  warnings: AtlasValidationCheck[]
}

export interface AtlasProvisioningCandidate {
  binding_key: string
  display_name: string
  tool_ref?: string | null
  requires_credentials: boolean
  missing_fields: string[]
  suggested_setup_action?: string | null
  provider_slug?: string | null
  setup_url?: string | null
  documentation_url?: string | null
}

export interface AtlasAPIDiscoveryResult {
  request_id: string
  status: 'not_run' | 'discovered' | 'unsupported' | 'failed'
  provider_name?: string | null
  candidate_tool_refs: string[]
  missing_auth_fields: string[]
  notes?: string | null
  spec_type: 'openapi' | 'swagger' | 'postman' | 'llm_parsed' | 'heuristic' | 'unknown'
  base_url?: string | null
  candidate_endpoints: Array<Record<string, unknown>>
  provisioning_candidates: AtlasProvisioningCandidate[]
  requires_review_before_provisioning: boolean
}

export interface AtlasAttachmentIngestionResult {
  attachment_id: string
  display_name: string
  kind: string
  mode: 'agent_document' | 'json_brief' | 'text_extracted' | 'attachment_bundle'
  extracted_characters: number
  chunk_count: number
  used_chunk_count: number
  quality_flags: string[]
  provenance: Record<string, unknown>
  truncated: boolean
  suggested_interpretation: 'review_as_authored_document' | 'review_as_partial_brief' | 'review_as_reference_only'
  blocking_questions: string[]
}

export interface AtlasProvisioningManifestItem {
  [key: string]: unknown
}

export interface AtlasReferences {
  [key: string]: unknown
}

export interface AtlasReviewState {
  approved_delta_ids: string[]
  rejected_delta_ids: string[]
  pending_delta_ids: string[]
  latest_apply_request_id?: string | null
}

export interface AtlasGeneratorInfo {
  [key: string]: unknown
}

export interface AtlasToolCall {
  [key: string]: unknown
}

/** Direct mirror of AtlasTurnResponse in atlas_protocol.py:387 */
export interface AtlasTurnResponse {
  session_id: string
  message: string
  next_action: AtlasNextAction
  generator: AtlasGeneratorInfo
  tool_calls: AtlasToolCall[]
  questions: BlockingQuestion[]
  dependencies: AtlasDependency[]
  blockers: AtlasBlocker[]
  proposed_changes: CanonicalAtlasProposedChanges
  derived_impact: AtlasDerivedImpact
  validation: AtlasValidationResult
  provisioning_manifest: AtlasProvisioningManifestItem[]
  api_discovery_results: AtlasAPIDiscoveryResult[]
  attachment_ingestion_results: AtlasAttachmentIngestionResult[]
  references: AtlasReferences
  review_state: AtlasReviewState
  pending_permission_requests: CanonicalAtlasPermissionRequest[]
}

/**
 * Alias kept so atlas-shared.ts and AtlasPermissionCard.tsx — which were
 * already targeting the backend turn shape — continue to compile without
 * a duplicate type definition.
 */
export type CanonicalAtlasTurnResponse = AtlasTurnResponse

// =============================================================================
// Apply / permission decisions
// =============================================================================

export interface AtlasApplyResponse {
  apply_request_id: string
  session_id: string
  status: 'pending' | 'rejected' | 'failed' | 'applied'
  error?: string | null
}

export interface AtlasPermissionDecisionResponse {
  session_id: string
  updated_requests: CanonicalAtlasPermissionRequest[]
}

// =============================================================================
// Agent enable toggle
// =============================================================================

export interface AtlasAgentEnabledResponse {
  agent_id: string
  atlas_enabled: boolean
}

// =============================================================================
// Readiness evaluation
// =============================================================================

export type AtlasReadinessScope = 'build' | 'validate' | 'fix' | 'operate'
export type AtlasReadinessProviderPolicy = 'google_only' | 'anthropic_only' | 'hybrid' | 'deterministic'
export type AtlasReadinessRunState =
  | 'created'
  | 'resolving_document'
  | 'generating_cases'
  | 'running_simulations'
  | 'running_voice_cases'
  | 'extracting_traces'
  | 'scoring'
  | 'proposing_deltas'
  | 'awaiting_review'
  | 'awaiting_permission'
  | 'applying_deltas'
  | 'rerunning_suite'
  | 'writing_report'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface AtlasReadinessRunRequest {
  agent_id?: string | null
  agent_version_id?: string | null
  workflow_brief?: string | null
  scope?: AtlasReadinessScope
  provider_policy?: AtlasReadinessProviderPolicy | null
  demo_case_set?: boolean
  voice_case_count?: number
  voice_audio_uri?: string | null
  voice_language?: string | null
  require_real_voice_io?: boolean
  cloud_evidence?: boolean
  case_limit?: number
  seed?: number | null
  reuse_case_set_id?: string | null
  max_estimated_cost_usd?: string | number | null
  max_wall_clock_seconds?: number
  paused_run_ttl_seconds?: number
}

export interface AtlasSyntheticTestProfile {
  profile_id: string
  locale: string
  channel: 'chat' | 'whatsapp' | 'voice'
  language_style: string
  emotional_state: string
  goal: string
  risk_tags: string[]
}

export interface AtlasReadinessCase {
  case_id: string
  test_profile: AtlasSyntheticTestProfile
  scenario_summary: string
  utterances: string[]
  expected_final_step_ids: string[]
  expected_facts: Record<string, unknown>
  fact_comparison_policy: 'exact' | 'capture_normalized' | 'subset'
  forbidden_reply_terms: string[]
  required_trace_events: string[]
  voice_input?: Record<string, unknown> | null
}

export interface AtlasReadinessScore {
  case_id: string
  passed: boolean
  score_source: 'deterministic' | 'hybrid' | 'llm_advisory'
  containment_score: number
  safety_score: number
  traceability_score: number
  voice_reliability_score?: number | null
  operational_readiness_score: number
  improvement_potential_score: number
  trajectory_score: number
  case_score: number
  failures: string[]
  blockers: string[]
  advisory_notes: string[]
}

export interface AtlasProviderInvocationMetadata {
  provider: string
  model: string
  role: string
  latency_ms: number
  prompt_tokens?: number | null
  completion_tokens?: number | null
  estimated_cost_usd?: string | null
  validation_outcome: 'valid' | 'invalid' | 'repaired' | 'blocked'
  fallback_reason?: string | null
  retry_count: number
  timeout_seconds: number
  cancelled: boolean
}

export interface AtlasReadinessReport {
  run_id: string
  agent_id: string | null
  before_scores: AtlasReadinessScore[]
  after_scores: AtlasReadinessScore[]
  proposed_changes: CanonicalAtlasProposedChanges
  publish_recommendation: 'publish' | 'do_not_publish' | 'needs_review'
  blockers: AtlasBlocker[]
  next_steps: string[]
  provider_invocations: AtlasProviderInvocationMetadata[]
  estimated_cost_usd?: string | null
  observed_cost_usd?: string | null
  narrative: Record<string, unknown>
  evidence: Record<string, unknown>
  score_breakdown: Record<string, unknown>
}

export interface AtlasReadinessRun {
  run_id: string
  organization_id?: string | null
  agent_id: string | null
  agent_version_id: string | null
  atlas_session_id: string | null
  scope: AtlasReadinessScope
  state: AtlasReadinessRunState
  provider_policy: AtlasReadinessProviderPolicy
  case_set_id: string | null
  document_hash: string | null
  policy_hash: string | null
  provider_config_hash: string | null
  request: AtlasReadinessRunRequest
  created_by_user_id?: string | null
  blocker_codes: string[]
  error?: string | null
  created_at: string
  updated_at: string
  completed_at?: string | null
}

export interface AtlasReadinessCaseSet {
  case_set_id: string
  organization_id?: string | null
  agent_id?: string | null
  seed?: number | null
  provider_policy: AtlasReadinessProviderPolicy
  cases: AtlasReadinessCase[]
  created_at: string
}

export interface AtlasReadinessRunSummary {
  run: AtlasReadinessRun
  case_set?: AtlasReadinessCaseSet | null
  report?: AtlasReadinessReport | null
}

export interface AtlasReadinessRunsPage {
  runs: AtlasReadinessRun[]
  has_more: boolean
  total_count: number
}

export interface AtlasReadinessProviderHealth {
  provider_policy: AtlasReadinessProviderPolicy
  gemini_configured: boolean
  anthropic_configured: boolean
  artifact_store_configured: boolean
  voice_harness: string
  warnings: string[]
}

export interface AtlasReadinessEvent {
  event_id: string
  run_id: string
  sequence_number: number
  type: string
  payload: Record<string, unknown>
  created_at: string
}

export interface AtlasReadinessEventsPage {
  run_id: string
  events: AtlasReadinessEvent[]
  has_more: boolean
  total_count: number
}

// =============================================================================
// SSE event callbacks
// =============================================================================

export interface AtlasEventCallbacks {
  onStart?: () => void
  onToken?: (text: string) => void
  onToolStart?: (name: string, payload: Record<string, unknown>) => void
  onToolDone?: (name: string, ok: boolean, payload: Record<string, unknown>) => void
  onPermissionRequest?: (payload: Record<string, unknown>) => void
  onProgress?: (payload: Record<string, unknown>) => void
  onComplete?: (payload: Record<string, unknown>) => void
  onError?: (err: Error) => void
  onEvent?: (event: AtlasEventEnvelope) => void
}

/** Typed HTTP error from the SSE stream — non-retryable status detection. */
class _StreamHttpError extends Error {
  constructor(public readonly status: number) {
    super(`SSE request failed: HTTP ${status}`)
  }
}

// =============================================================================
// Service
// =============================================================================

class AtlasService {
  // ---------- Sessions ----------

  /** POST /atlas/sessions */
  async startSession(req: AtlasSessionStartRequest): Promise<AtlasSessionResponse> {
    return apiClient.post<AtlasSessionResponse>('/atlas/sessions', req)
  }

  /** GET /atlas/sessions/{session_id} */
  async getSession(sessionId: string): Promise<AtlasSessionResponse> {
    return apiClient.get<AtlasSessionResponse>(`/atlas/sessions/${sessionId}`)
  }

  /** GET /atlas/sessions */
  async listSessions(
    opts: {
      agentId?: string
      scope?: AtlasScope
      status?: AtlasSessionStatus
      limit?: number
      offset?: number
    } = {},
  ): Promise<AtlasSessionsPageResponse> {
    const params = new URLSearchParams()
    if (opts.agentId) params.set('agent_id', opts.agentId)
    if (opts.scope) params.set('scope', opts.scope)
    if (opts.status) params.set('status', opts.status)
    if (opts.limit != null) params.set('limit', String(opts.limit))
    if (opts.offset != null) params.set('offset', String(opts.offset))
    const qs = params.toString()
    return apiClient.get<AtlasSessionsPageResponse>(`/atlas/sessions${qs ? `?${qs}` : ''}`)
  }

  /** POST /atlas/sessions/{session_id}/archive */
  async archiveSession(sessionId: string): Promise<AtlasArchiveSessionResponse> {
    return apiClient.post<AtlasArchiveSessionResponse>(`/atlas/sessions/${sessionId}/archive`, {})
  }

  // ---------- Messages and events ----------

  /** GET /atlas/sessions/{session_id}/messages */
  async listMessages(
    sessionId: string,
    opts: { beforeSequence?: number; limit?: number } = {},
  ): Promise<AtlasMessagesPageResponse> {
    const params = new URLSearchParams()
    if (opts.beforeSequence != null) params.set('before_sequence', String(opts.beforeSequence))
    if (opts.limit != null) params.set('limit', String(opts.limit))
    const qs = params.toString()
    return apiClient.get<AtlasMessagesPageResponse>(
      `/atlas/sessions/${sessionId}/messages${qs ? `?${qs}` : ''}`,
    )
  }

  /** GET /atlas/sessions/{session_id}/state */
  async getSessionState(sessionId: string): Promise<AtlasTurnResponse> {
    return apiClient.get<AtlasTurnResponse>(`/atlas/sessions/${sessionId}/state`)
  }

  /** GET /atlas/sessions/{session_id}/events */
  async listEvents(
    sessionId: string,
    opts: { afterSequence?: number; limit?: number } = {},
  ): Promise<AtlasEventsPageResponse> {
    const params = new URLSearchParams()
    if (opts.afterSequence != null) params.set('after_sequence', String(opts.afterSequence))
    if (opts.limit != null) params.set('limit', String(opts.limit))
    const qs = params.toString()
    return apiClient.get<AtlasEventsPageResponse>(
      `/atlas/sessions/${sessionId}/events${qs ? `?${qs}` : ''}`,
    )
  }

  /**
   * Subscribe to the SSE event stream for a session.
   *
   * The backend emits SSE messages of shape:
   *   id: <sequence_number>
   *   event: <AtlasEventType>
   *   data: <JSON-encoded AtlasEventEnvelope>
   *
   * The stream idles out after `idle_timeout_seconds` server-side and closes;
   * call `subscribeToEvents` again to resume from `afterSequence`.
   *
   * Pass an AbortSignal to cancel mid-stream.
   */
  async subscribeToEvents(
    sessionId: string,
    callbacks: AtlasEventCallbacks,
    opts: { afterSequence?: number; signal?: AbortSignal } = {},
  ): Promise<void> {
    const params = new URLSearchParams()
    if (opts.afterSequence != null) params.set('after_sequence', String(opts.afterSequence))
    const qs = params.toString()
    const url = `${apiClient.getBaseUrl()}/atlas/sessions/${sessionId}/events/stream${qs ? `?${qs}` : ''}`

    const csrfToken =
      document.cookie
        .split('; ')
        .find((row) => row.startsWith('csrf_token='))
        ?.split('=')[1] ?? ''

    const resp = await fetch(url, {
      method: 'GET',
      credentials: 'include',
      signal: opts.signal,
      headers: {
        Accept: 'text/event-stream',
        ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
        ...apiClient.getAuthHeader(),
      },
    })

    if (!resp.ok || !resp.body) {
      throw new _StreamHttpError(resp.status)
    }

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let currentEventType = ''
    let currentEventId: number | null = null
    let currentDataLines: string[] = []

    const dispatchBufferedEvent = () => {
      if (currentDataLines.length === 0) {
        currentEventType = ''
        currentEventId = null
        return
      }
      const data = currentDataLines.join('\n').trim()
      currentDataLines = []
      if (!data) {
        currentEventType = ''
        currentEventId = null
        return
      }
      try {
        const envelope = JSON.parse(data) as AtlasEventEnvelope
        const eventType = (envelope.type || currentEventType) as AtlasEventType
        if (
          currentEventId !== null &&
          (!Number.isFinite(envelope.sequence_number) || envelope.sequence_number <= 0)
        ) {
          envelope.sequence_number = currentEventId
        }
        callbacks.onEvent?.(envelope)
        this._dispatchEvent(eventType, envelope.payload, callbacks)
      } catch (err) {
        if (!(err instanceof SyntaxError)) throw err
      } finally {
        currentEventType = ''
        currentEventId = null
      }
    }

    try {
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (line.startsWith(':')) continue // keep-alive comment
          if (line === '') {
            dispatchBufferedEvent()
            continue
          }
          if (line.startsWith('id: ')) {
            const parsed = Number.parseInt(line.slice(4).trim(), 10)
            currentEventId = Number.isFinite(parsed) ? parsed : null
            continue
          }
          if (line.startsWith('event: ')) {
            currentEventType = line.slice(7).trim()
            continue
          }
          if (!line.startsWith('data:')) continue
          currentDataLines.push(line.slice(5).replace(/^ /, ''))
        }
      }
      dispatchBufferedEvent()
    } finally {
      reader.cancel().catch(() => {})
    }
  }

  private _dispatchEvent(
    type: AtlasEventType,
    payload: Record<string, unknown>,
    callbacks: AtlasEventCallbacks,
  ): void {
    switch (type) {
      case 'start':
        callbacks.onStart?.()
        break
      case 'token':
        callbacks.onToken?.(String(payload.text ?? ''))
        break
      case 'tool_start':
        callbacks.onToolStart?.(String(payload.name ?? ''), payload)
        break
      case 'tool_done':
        callbacks.onToolDone?.(String(payload.name ?? ''), Boolean(payload.ok ?? true), payload)
        break
      case 'permission_request':
        callbacks.onPermissionRequest?.(payload)
        break
      case 'progress':
        callbacks.onProgress?.(payload)
        break
      case 'complete':
        callbacks.onComplete?.(payload)
        break
      case 'error':
        callbacks.onError?.(new Error(String(payload.message ?? 'Atlas stream error')))
        break
    }
  }

  // ---------- Turns ----------

  /** POST /atlas/turns */
  async runTurn(req: AtlasTurnRequest): Promise<AtlasTurnResponse> {
    return apiClient.post<AtlasTurnResponse>('/atlas/turns', req)
  }

  // ---------- Readiness evaluation ----------

  /** POST /atlas/readiness/runs */
  async createReadinessRun(req: AtlasReadinessRunRequest): Promise<AtlasReadinessRunSummary> {
    return apiClient.post<AtlasReadinessRunSummary>('/atlas/readiness/runs', req)
  }

  /** GET /atlas/readiness/runs/{run_id} */
  async getReadinessRun(runId: string): Promise<AtlasReadinessRunSummary> {
    return apiClient.get<AtlasReadinessRunSummary>(`/atlas/readiness/runs/${runId}`)
  }

  /** GET /atlas/readiness/runs */
  async listReadinessRuns(
    opts: { agentId?: string; limit?: number; offset?: number } = {},
  ): Promise<AtlasReadinessRunsPage> {
    const params = new URLSearchParams()
    if (opts.agentId) params.set('agent_id', opts.agentId)
    if (opts.limit != null) params.set('limit', String(opts.limit))
    if (opts.offset != null) params.set('offset', String(opts.offset))
    const qs = params.toString()
    return apiClient.get<AtlasReadinessRunsPage>(`/atlas/readiness/runs${qs ? `?${qs}` : ''}`)
  }

  /** GET /atlas/readiness/provider-health */
  async getReadinessProviderHealth(providerPolicy?: AtlasReadinessProviderPolicy): Promise<AtlasReadinessProviderHealth> {
    const qs = providerPolicy ? `?provider_policy=${encodeURIComponent(providerPolicy)}` : ''
    return apiClient.get<AtlasReadinessProviderHealth>(`/atlas/readiness/provider-health${qs}`)
  }

  /** GET /atlas/readiness/runs/{run_id}/events */
  async listReadinessEvents(
    runId: string,
    opts: { afterSequence?: number; limit?: number } = {},
  ): Promise<AtlasReadinessEventsPage> {
    const params = new URLSearchParams()
    if (opts.afterSequence != null) params.set('after_sequence', String(opts.afterSequence))
    if (opts.limit != null) params.set('limit', String(opts.limit))
    const qs = params.toString()
    return apiClient.get<AtlasReadinessEventsPage>(
      `/atlas/readiness/runs/${runId}/events${qs ? `?${qs}` : ''}`,
    )
  }

  /** GET /atlas/readiness/runs/{run_id}/report */
  async getReadinessReport(runId: string): Promise<AtlasReadinessReport> {
    return apiClient.get<AtlasReadinessReport>(`/atlas/readiness/runs/${runId}/report`)
  }

  /** POST /atlas/readiness/runs/{run_id}/propose-deltas */
  async proposeReadinessDeltas(runId: string): Promise<AtlasReadinessRunSummary> {
    return apiClient.post<AtlasReadinessRunSummary>(`/atlas/readiness/runs/${runId}/propose-deltas`, {})
  }

  /** POST /atlas/readiness/runs/{run_id}/rerun */
  async rerunReadinessRun(runId: string): Promise<AtlasReadinessRunSummary> {
    return apiClient.post<AtlasReadinessRunSummary>(`/atlas/readiness/runs/${runId}/rerun`, {})
  }

  /** POST /atlas/readiness/runs/{run_id}/cancel */
  async cancelReadinessRun(runId: string): Promise<AtlasReadinessRunSummary> {
    return apiClient.post<AtlasReadinessRunSummary>(`/atlas/readiness/runs/${runId}/cancel`, {})
  }

  // ---------- Permissions and apply ----------

  /** POST /atlas/sessions/{session_id}/permission-decisions */
  async applyPermissionDecisions(
    sessionId: string,
    decisions: AtlasPermissionDecision[],
  ): Promise<AtlasPermissionDecisionResponse> {
    return apiClient.post<AtlasPermissionDecisionResponse>(
      `/atlas/sessions/${sessionId}/permission-decisions`,
      decisions,
    )
  }

  /** POST /atlas/sessions/{session_id}/apply */
  async applyChanges(sessionId: string, req: AtlasApplyRequest): Promise<AtlasApplyResponse> {
    return apiClient.post<AtlasApplyResponse>(`/atlas/sessions/${sessionId}/apply`, req)
  }

  // ---------- Agent enabled toggle ----------

  /** GET /atlas/agents/{agent_id}/enabled */
  async getEnabledStatus(agentId: string): Promise<AtlasAgentEnabledResponse> {
    return apiClient.get<AtlasAgentEnabledResponse>(`/atlas/agents/${agentId}/enabled`)
  }

  /** PUT /atlas/agents/{agent_id}/enabled */
  async setEnabledStatus(
    agentId: string,
    enabled: boolean,
  ): Promise<AtlasAgentEnabledResponse> {
    return apiClient.put<AtlasAgentEnabledResponse>(
      `/atlas/agents/${agentId}/enabled`,
      { atlas_enabled: enabled },
    )
  }
}

export const atlasService = new AtlasService()
