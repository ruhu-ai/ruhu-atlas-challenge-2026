export interface AgentFactDef {
  name: string
  type: string
  required?: boolean
}

export interface AgentCapabilityManifest {
  assistant_identity: string
  capabilities: string[]
  limitations: string[]
}

// Edge-owned outcomes: discriminated union over `kind`. Mirrors the
// backend Pydantic union in `ruhu.schemas` (`OutcomeCondition`,
// `FactPresentCondition`, ...). Each member carries its own typed
// payload — `OutcomeCondition` owns the stable `event` token + the
// LLM-evaluated `description`; fact / guard / tool / view variants
// carry kind-specific identifiers; `OtherwiseCondition` is the sole
// no-payload catch-all.
//
// `value` is preserved as an optional alias for callers that haven't
// migrated yet (canvas + flow-graph views still read it). New code
// should read the kind-specific field instead.

// Each member also exposes ``value?: string | null`` as a deprecated
// read-only alias so legacy renderers (ScenarioLanesCanvas,
// AgentFlowGraph) keep compiling during the canvas migration. New
// authoring code should use the kind-specific field — never ``value``.

export interface OutcomeCondition {
  kind: 'outcome'
  /** Stable event token (e.g. `product_question`). Analytics + training key. */
  event: string
  /** LLM-evaluated meaning (≥8 chars). */
  description: string
  /** @deprecated Use ``event``. */
  value?: string | null
}

export interface FactPresentCondition {
  kind: 'fact_present'
  fact_name: string
  /** Optional value match — when set, the fact must equal this value. */
  value?: string | null
}

export interface FactEqualsCondition {
  kind: 'fact_equals'
  fact_name: string
  value: string
}

export interface FactMissingCondition {
  kind: 'fact_missing'
  fact_name: string
  /** @deprecated Use ``fact_name``. */
  value?: string | null
}

export interface AllRequiredFactsPresentCondition {
  kind: 'all_required_facts_present'
  /** @deprecated No payload — always nullish. */
  value?: string | null
}

export interface GuardFailureCondition {
  kind: 'guard_failure'
  guard_id: string
  /** @deprecated Use ``guard_id``. */
  value?: string | null
}

export interface ToolOutcomeCondition {
  kind: 'tool_outcome'
  outcome: string
  /** Optional disambiguation when the step has multiple tool bindings. */
  tool_ref?: string | null
  /** @deprecated Use ``outcome``. */
  value?: string | null
}

export interface AttachmentPresentCondition {
  kind: 'attachment_present'
  any_of_kinds?: string[] | null
  all_of_kinds?: string[] | null
  /** @deprecated No primary payload. */
  value?: string | null
}

export interface ViewReadyCondition {
  kind: 'view_ready'
  view_kind?: string | null
  /** @deprecated No primary payload. */
  value?: string | null
}

export interface OtherwiseCondition {
  kind: 'otherwise'
  /** @deprecated No payload. */
  value?: string | null
}

export type AgentStepCondition =
  | OutcomeCondition
  | FactPresentCondition
  | FactEqualsCondition
  | FactMissingCondition
  | AllRequiredFactsPresentCondition
  | GuardFailureCondition
  | ToolOutcomeCondition
  | AttachmentPresentCondition
  | ViewReadyCondition
  | OtherwiseCondition

export interface AgentStepTransition {
  id: string
  when: AgentStepCondition
  to_step_id: string
  label?: string | null
  priority?: number
}

export interface AgentScenarioRoute {
  id: string
  from_scenario_id: string
  when: AgentStepCondition
  to_scenario_id: string
  label?: string | null
  priority?: number
}

export interface AgentFactRequirement {
  name: string
  purpose?: string | null
}

export interface AgentToolBinding {
  ref: string
  mode?: string
  invocation_strategy?: string
  timeout_ms?: number | null
  event_name?: string | null
  args?: Record<string, unknown>
}

export interface AgentGuardDef {
  kind: 'channel_allowed' | 'fact_required'
  value: string
  description?: string | null
}

export interface AgentActionConfig {
  code: string
  callable_functions_code?: string
  callable_api_refs?: string[]
  callable_integrations?: string[]
  callable_system_refs?: string[]
  input_schema?: Record<string, unknown>
  timeout_seconds?: number
}

export interface AgentResponsePolicy {
  answer_directly_first?: boolean
  ask_clarifying_question_only_if_needed?: boolean
  voice_style?: 'concise' | 'balanced' | 'detailed'
  direct_answer_prompt?: string | null
  render_with_llm?: boolean
  deterministic_fallback_text?: string | null
  response_max_sentences?: number | null
  include_recent_history?: boolean
  include_known_facts?: boolean
}

export interface AgentStepCompletion {
  disposition: string
  summary?: string | null
}

export interface AgentStepHandoff {
  target_type: 'queue' | 'agent' | 'phone_number'
  target: string
  summary?: string | null
}

export interface AgentStep {
  id: string
  name: string
  transitions: AgentStepTransition[]
  description?: string | null
  say?: string | null
  guards?: AgentGuardDef[]
  fact_requirements?: AgentFactRequirement[]
  tool_policy?: AgentToolBinding[]
  action_config?: AgentActionConfig | null
  response_policy?: AgentResponsePolicy
  event_hints?: Record<string, string>
  workload_class?: 'interactive' | 'deferred'
  execution_isolation?: 'inline' | 'subprocess'
  handoff?: AgentStepHandoff | null
  completion?: AgentStepCompletion | null
}

export interface AgentScenario {
  id: string
  name: string
  start_step_id: string
  steps: AgentStep[]
  summary?: string | null
  order?: number
  entry_channels?: string[]
  resources?: Record<string, unknown>
  // Persisted positions for the flow view, keyed by step id. Optional —
  // when absent or for steps not in the map, the flow falls back to
  // dagre auto-layout. Authored only via the flow view's drag handler.
  flow_layout?: Record<string, { x: number; y: number }>
}

export type AnalysisVariableType = 'string' | 'number' | 'boolean' | 'category' | 'array'
export type AnalysisVariableSource = 'transcript' | 'facts'

export interface AnalysisVariableDef {
  name: string
  type: AnalysisVariableType
  description: string
  categories?: string[] | null
  source?: AnalysisVariableSource
  extract_when?: string | null
}

export interface AgentDocument {
  version: string
  start_scenario_id: string
  scenarios: AgentScenario[]
  scenario_routes?: AgentScenarioRoute[]
  fact_schema?: AgentFactDef[]
  analysis_schema?: AnalysisVariableDef[]
  agent_capability_manifest?: AgentCapabilityManifest | null
  metadata?: Record<string, unknown>
}

export interface AgentDocumentResponse {
  agent_id: string
  target: string
  document: AgentDocument
}
