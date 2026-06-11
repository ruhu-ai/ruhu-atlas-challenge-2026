import type { AgentDocument, AgentStep, AgentStepTransition } from '@/types/agent-document'

export type AgentVersionStatus = 'draft' | 'published'
export type ToolMode = 'allowed' | 'blocked' | 'required' | 'optional'
export type Channel = 'web_chat' | 'web_widget' | 'voice' | 'sms' | 'email'
export type Modality = 'text' | 'audio'
export type ToolInvocationStrategy =
  | 'always'
  | 'never'
  | 'on_missing_context'
  | 'on_low_confidence'
  | 'latency_bounded'
export type ConditionKind =
  | 'event'
  | 'fact_present'
  | 'fact_missing'
  | 'guard_failure'
  | 'tool_outcome'
  | 'otherwise'
export type ValidationSeverity = 'error' | 'warning'

export type TurnEagerness = 'low' | 'normal' | 'high'
export type InterruptibilityPolicy =
  | 'always_interruptible'
  | 'interruptible_except_policy'
  | 'non_interruptible'

export interface FactDef {
  name: string
  type: string
  required: boolean
  source_policy: 'deterministic_only' | 'deterministic_first' | 'model_allowed'
  confidence_threshold: number
  conflict_policy: 'prefer_deterministic' | 'prefer_latest_high_confidence' | 'require_confirmation'
}

export interface FactRequirement {
  name: string
  purpose?: string | null
}

export interface ToolBinding {
  ref: string
  mode: ToolMode
  invocation_strategy: ToolInvocationStrategy
  timeout_ms?: number | null
  event_name?: string | null
  args: Record<string, unknown>
}

export interface GuardDef {
  kind: 'channel_allowed' | 'fact_required'
  value: string
  description?: string | null
}

export interface ResponsePolicy {
  answer_directly_first: boolean
  ask_clarifying_question_only_if_needed: boolean
  voice_style: 'concise' | 'balanced' | 'detailed'
  direct_answer_prompt?: string | null
}

export interface Condition {
  kind: ConditionKind
  value?: string | null
}

export type TransitionBranchIntent =
  | 'continue'
  | 'confirm'
  | 'ask_again'
  | 'repair'
  | 'block'
  | 'escalate'

export interface AgentCapabilityManifest {
  assistant_identity?: string | null
  capabilities: string[]
  limitations?: string[]
}

// ─── Canvas-flat shape ───────────────────────────────────────────────────────
// The canvas authors against a single flat list of steps with author-time
// metadata that has no direct backend equivalent (accepted_inputs,
// say_on_transition, ask_for_fact, repair_response, activity_label, etc.).
// This shape is produced by the agentDefinitionService adapter from the
// nested AgentDocument.scenarios[].steps[] returned by the backend.
//
// Per docs/generic-state-redesign/01-generic-step-canvas-adr.md, step kind
// is no longer authored — derive on demand via deriveStepKind() from the
// optional capability fields below (terminal_disposition, fact_requirements,
// and the schema-only handoff/action_config fields preserved on the step).

export interface Transition {
  id: string
  when: Condition
  /** Local step id (canvas-side; backend uses to_step_id on StepTransition). */
  to: string
  /** Author-facing branch reason. Runtime routing is driven by `when` and `to`. */
  reason_code?: string
  natural_reason?: string | null
  when_to_use?: string | null
  priority: number
  /**
   * Spec 25 §Transition Editor Changes — author-facing metadata about the
   * narrative intent of this branch. Does not replace the condition.
   */
  branch_intent?: TransitionBranchIntent | null
}

export interface AgentDefinitionStep {
  id: string
  name: string
  accepted_inputs: string[]
  event_hints: Record<string, string>
  fact_requirements: FactRequirement[]
  tool_policy: ToolBinding[]
  response_policy: ResponsePolicy
  guards: GuardDef[]
  transitions: Transition[]
  entry_response?: string | null
  terminal_disposition?: string | null
  artifact_type?: string | null
  say_on_entry?: string | null
  say_on_transition?: string | null
  ask_for_fact?: string | null
  repair_response?: string | null
  activity_label?: string | null
  slow_threshold_ms?: number | null
  soft_timeout_ms?: number | null
  endpointing_ms?: number | null
  turn_eagerness?: TurnEagerness | null
  interruptibility_policy?: InterruptibilityPolicy | null
  publish_status_trail?: boolean
  repair_on_no_progress?: boolean
  // Conversation-step author metadata
  performs_repair?: boolean
  expects_policy_blocks?: boolean
}

/**
 * Routes a follow-up intent on an artifact type to a target step.
 * Agent-level registration — `agent.followup_handlers` is the single source
 * of truth for artifact follow-up routing (spec docs 20 / 21).
 */
export interface ArtifactFollowupHandler {
  artifact_type: string
  followup_intent: string
  target_step_id: string
  fact_requirements: FactRequirement[]
}

export interface AgentDefinition {
  id: string
  name: string
  version: string
  start_step_id: string
  steps: AgentDefinitionStep[]
  fact_schema: FactDef[]
  followup_handlers?: ArtifactFollowupHandler[]
  agent_capability_manifest?: AgentCapabilityManifest | null
}

export interface AgentDefinitionTargetResponse {
  definition: AgentDefinition
  version: AgentVersionSummary
}

export interface AgentFactsReplaceRequest {
  fact_schema: FactDef[]
}

export interface AgentSummary {
  id: string
  name: string
  version: string
  step_count: number
  description: string
  agent_type: AgentType
  llm_provider: AgentLLMProvider
  llm_model: string
  knowledge_base_count: number
  has_draft_version: boolean
  has_published_version: boolean
  has_unpublished_changes: boolean
  updated_at: string
  current_draft_version_id?: string | null
  current_published_version_id?: string | null
}

export interface AgentVersionSummary {
  version_id: string
  agent_id: string
  status: AgentVersionStatus
  version_number: number
  schema_version: string
  based_on_version_id?: string | null
  published_at?: string | null
  created_at: string
  updated_at: string
  is_current_draft: boolean
  is_current_published: boolean
}

export interface AgentVersionTargetResponse {
  agent_id: string
  agent_name: string
  document: AgentDocument
  version: AgentVersionSummary
}

export interface AgentCreateRequest {
  name: string
  settings: AgentSettings
  document: AgentDocument
}

export type AgentType = 'chat' | 'voice' | 'multimodal'
export type AgentLLMProvider = 'openai' | 'anthropic' | 'gemini' | 'vertex' | 'vllm'
/**
 * Per-agent intent classifier strategy.
 *
 * - `off`: skip classification; the kernel routes only on facts/tool
 *   outcomes/`otherwise`.
 * - `main_llm` *(default for new agents)*: a frontier LLM (Vertex Gemini
 *   Flash) classifies each turn against the step's intent catalog.
 *   Cold-start safe.
 * - `prefill`: small prefill-first classifier (Gemma/Qwen + production
 *   LoRA). Backend rejects this strategy unless a production-status
 *   LoRA exists for the agent and has passed eval.
 */
export type AgentClassifierStrategy = 'off' | 'main_llm' | 'prefill'

export interface AgentClassifierConfig {
  strategy: AgentClassifierStrategy
}

export interface AgentLLMConfig {
  provider: AgentLLMProvider
  model: string
  temperature: number
  classifier: AgentClassifierConfig
}

export interface AgentVoiceConfig {
  voice_id: string
}

/** Cosmetic persona — live-edit identity surface. PATCH on
 * /agents/:id/settings applies immediately, no publish required.
 * Mirrors `ruhu.persona.CosmeticPersona`. */
export type PersonaPronouns = 'she/her' | 'he/him' | 'they/them' | 'custom'

export interface CosmeticPersona {
  persona_name?: string | null
  pronouns?: PersonaPronouns | null
  pronouns_custom?: string | null
  avatar_url?: string | null
  role_title?: string | null
  greeting_template?: string | null
  signoff_template?: string | null
  /** Phase 2b — per-language persona name overrides. Keys are BCP-47
   * language tags ("yo" → "Mayowa"); values follow persona_name
   * validation. Branding for markets where the canonical name doesn't
   * translate well. Live-edit (no publish required). */
  persona_name_overrides?: Record<string, string>
}

/** Behavioural persona — versioned. Lives on AgentDocument.metadata.persona
 * and goes through draft → publish-review → publish. Mirrors
 * `ruhu.persona.BehavioralPersona`. */
export type PersonaFormality = 'formal' | 'neutral' | 'casual'
export type PersonaEmojiPolicy = 'never' | 'sparingly' | 'encouraged'

/** Phase 2c topic-enforcement modes. Mirrors
 * `ruhu.persona.TopicEnforcementPolicy`.
 *
 * - `off` — no detection, no logging (the prompt still asks the LLM to
 *   avoid topics, but no post-render guard).
 * - `log_only` — canary mode. Detection runs and decisions are audited,
 *   but the response goes out unmodified. Default for new agents per
 *   decision [README 2-1]; flipped to `block_and_retry` after a 7-day
 *   per-tenant rollout.
 * - `block_and_retry` — full enforcement. Violating responses are
 *   retried once with a stronger constraint; on second violation a
 *   deterministic deflection is emitted. */
export type TopicEnforcementPolicy = 'off' | 'log_only' | 'block_and_retry'

/** Phase 2b language-routing modes. Mirrors
 * `ruhu.persona.AutoSwitchMode`. */
export type AutoSwitchMode = 'off' | 'log_only' | 'on'

/** Phase 2b — how the agent responds to a language change once
 * stability gates pass. Mirrors `ruhu.persona.LanguageSwitchPolicy`. */
export type LanguageSwitchPolicy =
  | 'mirror_user'
  | 'lock_to_primary'
  | 'gradual_revert'

/** Phase 2b — how the agent responds when the user speaks a language
 * NOT in `allowed_languages`. Mirrors
 * `ruhu.persona.UnsupportedLanguagePolicy`. */
export type UnsupportedLanguagePolicy =
  | 'stay_in_primary'
  | 'explain_and_offer'
  | 'escalate_to_human'

/** Phase 2a-base — voice subsystem types. Mirror
 * `ruhu.voice.protocol.VoiceGender` and the catalog response shape
 * exposed by `GET /persona/voices/library`. */
export type VoiceGender = 'male' | 'female' | 'neutral'

export interface VoiceCatalogEntry {
  voice_id: string
  provider: string
  display_name: string
  language: string
  gender: VoiceGender
  accent: string | null
  description: string | null
  sample_text: string | null
}

export interface VoiceCatalogPage {
  voices: VoiceCatalogEntry[]
  next_cursor: string | null
  total_count: number | null
}

export interface VoiceLibraryFilters {
  language?: string
  gender?: VoiceGender
  accent?: string
}

/** Phase 2a-cloning — response from POST /persona/voices/clone.
 * The plaintext cloning key never appears in any client-bound payload;
 * `clone_id` is the stable handle the picker uses to reference the
 * clone afterwards. */
export interface VoiceCloneCreatedResponse {
  clone_id: string
  provider: string
  display_name: string
  language: string
  created_at: string
  estimated_cost_usd: number
}

/** Domain error codes the cloning wizard distinguishes for UX. */
export type VoiceCloneErrorKind =
  | 'consent_rejected'        // Google rejected the consent statement (422)
  | 'audio_too_large'         // 1MB cap exceeded (413)
  | 'audio_invalid_format'    // MIME not in allowlist (422)
  | 'unauthorized'            // 401 / 403 — not admin
  | 'agent_not_found'         // bogus agent_id (404)
  | 'service_unavailable'     // 503 — Google or store down
  | 'unknown'                 // anything else

export interface BehavioralPersona {
  formality: PersonaFormality
  emoji_policy: PersonaEmojiPolicy
  restricted_topics: string[]
  topic_enforcement: TopicEnforcementPolicy
  voice_provider: string
  voice_id: string
  voice_speed: number
  voice_monthly_budget_cents: number | null
  /** Phase 2b — language fields. Defaults match `BehavioralPersona`
   * Python defaults so existing agents see no behaviour change. */
  primary_language: string
  allowed_languages: string[]
  auto_switch_language: AutoSwitchMode
  language_switch_confidence_threshold: number
  language_switch_min_chars: number
  language_switch_debounce_turns: number
  language_switch_policy: LanguageSwitchPolicy
  unsupported_language_policy: UnsupportedLanguagePolicy
  /** Per-language voice overrides. Keys are BCP-47 language tags;
   * values are voice IDs from the Vertex Gemini catalog (or, for
   * tenants on 2a-cloning, clone IDs). */
  voice_id_overrides: Record<string, string>
  locale_code: string
  cultural_calendar_enabled: boolean
}

export const DEFAULT_BEHAVIORAL_PERSONA: BehavioralPersona = {
  formality: 'neutral',
  emoji_policy: 'sparingly',
  restricted_topics: [],
  topic_enforcement: 'log_only',
  voice_provider: 'vertex_gemini',
  voice_id: 'en-US-Chirp3-HD-Kore',
  voice_speed: 1.0,
  voice_monthly_budget_cents: null,
  primary_language: 'en',
  allowed_languages: ['en'],
  auto_switch_language: 'off',
  language_switch_confidence_threshold: 0.8,
  language_switch_min_chars: 10,
  language_switch_debounce_turns: 1,
  language_switch_policy: 'mirror_user',
  unsupported_language_policy: 'explain_and_offer',
  voice_id_overrides: {},
  locale_code: 'en-US',
  cultural_calendar_enabled: false,
}

export interface AgentSettings {
  description: string
  agent_type: AgentType
  system_prompt: string
  llm_config: AgentLLMConfig
  voice_config: AgentVoiceConfig
  knowledge_base_ids: string[]
  /** Cosmetic persona — null when unset. Behavioural persona lives on the
   * agent document's metadata, not here. */
  persona?: CosmeticPersona | null
  /** Source template ID, set at clone time. Used by the post-clone
   * setup checklist to recover template provenance when ?template=
   * URL param is missing. Read-only from the client's perspective. */
  source_template_id?: string | null
}

export interface AgentSettingsPatchRequest {
  description?: string
  agent_type?: AgentType
  system_prompt?: string
  llm_config?: Partial<AgentLLMConfig> & {
    classifier?: Partial<AgentClassifierConfig>
  }
  voice_config?: Partial<AgentVoiceConfig>
  knowledge_base_ids?: string[]
  persona?: CosmeticPersona | null
}

export interface AgentSettingsResponse {
  agent_id: string
  settings: AgentSettings
}

export interface AgentWidgetConfig {
  agent_id: string
  company_name: string
  button_text: string
  primary_color: string
  accent_color: string
  position: 'bottom-right' | 'bottom-left' | 'top-right' | 'top-left'
  show_powered_by: boolean
  welcome_message: string
  subtitle: string
}

export interface AgentMetadataPatchRequest {
  name?: string
}

export interface AgentDraftCreateRequest {
  source_version_id?: string | null
}

export interface AgentValidationIssue {
  severity: ValidationSeverity
  code: string
  message: string
  scenario_id?: string | null
  step_id?: string | null
  transition_id?: string | null
  route_id?: string | null
  fact_name?: string | null
  tool_ref?: string | null
}

export interface AgentValidationReport {
  valid: boolean
  error_count: number
  warning_count: number
  issues: AgentValidationIssue[]
}

export interface AgentMetadataChange {
  field: string
  before?: unknown
  after?: unknown
}

export interface AgentFactChange {
  name: string
  status: string
  before?: FactDef | null
  after?: FactDef | null
}

export interface AgentTransitionChange {
  transition_id: string
  status: string
  before?: AgentStepTransition | null
  after?: AgentStepTransition | null
}

export interface AgentToolBindingChange {
  ref: string
  status: string
  before?: ToolBinding | null
  after?: ToolBinding | null
}

export interface AgentStepChange {
  step_id: string
  status: string
  before?: AgentStep | null
  after?: AgentStep | null
  changed_fields: string[]
  transition_changes: AgentTransitionChange[]
  tool_policy_changes: AgentToolBindingChange[]
}

export interface AgentDiffSummary {
  added_steps: number
  removed_steps: number
  changed_steps: number
  added_facts: number
  removed_facts: number
  changed_facts: number
  added_transitions: number
  removed_transitions: number
  changed_transitions: number
  added_tool_bindings: number
  removed_tool_bindings: number
  changed_tool_bindings: number
}

export interface AgentVersionDiff {
  agent_id: string
  source_version_id: string
  against_version_id: string
  metadata_changes: AgentMetadataChange[]
  fact_changes: AgentFactChange[]
  step_changes: AgentStepChange[]
  summary: AgentDiffSummary
}

export interface PublishReviewRemediation {
  kind: string
  tool_ref?: string | null
  url: string
  label: string
  documentation_url?: string | null
}

export interface PublishReviewItem {
  severity: string
  code: string
  message: string
  remediation?: PublishReviewRemediation | null
}

export interface AgentPublishReadiness {
  agent_id: string
  draft_version_id: string
  published_version_id?: string | null
  can_publish: boolean
  blockers: PublishReviewItem[]
  warnings: PublishReviewItem[]
  validation: AgentValidationReport
  diff?: AgentVersionDiff | null
  available_tools: string[]
  missing_tools: string[]
}

export interface EvaluationPolicyConfig {
  minimum_pass_rate_ratio: number
  allow_warning_failures: boolean
  max_qualified_run_age_hours?: number | null
}

export interface AgentEvaluationPolicyResponse {
  agent_id: string
  policy: EvaluationPolicyConfig
}

export interface SimulationTurnInput {
  turn_id?: string | null
  dedupe_key?: string | null
  event_type?: string
  modality?: Modality
  text?: string | null
  metadata?: Record<string, unknown>
}

export interface SimulationAssertion {
  assertion_id?: string
  kind: string
  severity?: 'blocker' | 'warning'
  config?: Record<string, unknown>
}

export interface SimulationFixture {
  fixture_id: string
  organization_id?: string | null
  agent_id: string
  name: string
  description?: string | null
  tags: string[]
  default_channel: Channel
  default_modality: Modality
  starting_step_id?: string | null
  starting_scenario_id?: string | null
  seed_facts: Record<string, unknown>
  turns: SimulationTurnInput[]
  assertions: SimulationAssertion[]
  is_active: boolean
  gate_required: boolean
  created_by_user_id?: string | null
  created_at: string
  updated_at: string
}

export interface SimulationFixtureCreateRequest {
  fixture_id?: string | null
  name: string
  description?: string | null
  tags?: string[]
  default_channel?: Channel
  default_modality?: Modality
  starting_step_id?: string | null
  starting_scenario_id?: string | null
  seed_facts?: Record<string, unknown>
  turns?: SimulationTurnInput[]
  assertions?: SimulationAssertion[]
  is_active?: boolean
  gate_required?: boolean
}

export interface EvaluationRunCaseResult {
  case_result_id: string
  fixture_id?: string | null
  fixture_name: string
  conversation_id: string
  status: 'passed' | 'failed' | 'skipped' | 'error'
  final_step_id: string
  turn_count: number
  assertions_passed: number
  assertions_failed: number
  blocker_failures: number
  warning_failures: number
  duration_ms?: number | null
  failure_summary?: string | null
  actual_facts: Record<string, unknown>
  started_at: string
  completed_at?: string | null
}

export interface EvaluationRun {
  evaluation_run_id: string
  organization_id?: string | null
  agent_id: string
  agent_version_id: string
  mode: 'manual_batch' | 'publish_gate' | 'ci'
  source: 'studio' | 'api' | 'worker' | 'cli'
  status: 'queued' | 'running' | 'stopping' | 'stopped' | 'completed' | 'failed' | 'cancelled'
  gate_eligible: boolean
  fixture_count: number
  passed_count: number
  failed_count: number
  skipped_count: number
  pass_rate_ratio?: number | null
  triggered_by_user_id?: string | null
  started_at?: string | null
  completed_at?: string | null
  duration_ms?: number | null
  error_message?: string | null
  qualified_at?: string | null
  results: EvaluationRunCaseResult[]
}

export interface EvaluationRunCreateRequest {
  fixture_ids?: string[]
  agent_version_id?: string | null
  mode?: 'manual_batch' | 'publish_gate' | 'ci'
  source?: 'studio' | 'api' | 'worker' | 'cli'
  gate_eligible?: boolean
  minimum_pass_rate_ratio?: number | null
  allow_warning_failures?: boolean | null
  execution_mode?: 'async' | 'sync'
}

export interface AgentLatencyStats {
  count: number
  average_ms: number
  p95_ms: number
  max_ms: number
}

export interface AgentOperationalMetrics {
  agent_id: string
  agent_version_id?: string | null
  conversation_count: number
  trace_count: number
  avg_turns_per_conversation: number
  total_latency: AgentLatencyStats
  state_entries: Record<string, number>
  transition_counts: Record<string, number>
  action_counts: Record<string, number>
  tool_status_counts: Record<string, number>
}

export interface RuntimeTurnResultSummary {
  turn_id: string
  step_before: string
  step_after: string
  emitted_messages: Array<{ role: 'assistant' | 'system'; text: string }>
  trace_id: string
  latency_breakdown_ms: Record<string, number>
}

export interface AgentReplayResponse {
  simulation: {
    final_step_id: string
    final_facts: Record<string, unknown>
    turns: RuntimeTurnResultSummary[]
  }
  metrics: AgentOperationalMetrics
}
