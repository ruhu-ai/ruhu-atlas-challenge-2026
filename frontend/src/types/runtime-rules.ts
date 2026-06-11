export type RuleStage =
  | 'turn_ingress'
  | 'before_response'
  | 'before_tool'
  | 'after_tool'
  | 'before_emit'

export type RuleBindingMode = 'enforce' | 'shadow' | 'disabled'
export type RulesOrganizationScope = 'system' | 'organization' | 'all'
export type RuleChannel = 'phone' | 'whatsapp' | 'web_chat' | 'web_widget' | 'browser'
export type RuleRevisionStatus = 'draft' | 'published' | 'retired'

export interface RuleBindingScope {
  channels: RuleChannel[]
  agent_ids: string[]
  step_ids: string[]
  tool_refs: string[]
  event_types: string[]
}

export interface RuleDefinitionSummary {
  rule_id: string
  organization_id?: string | null
  latest_revision: number
  latest_status: RuleRevisionStatus
  published_revision?: number | null
  name: string
  stage: RuleStage
  tags: string[]
}

export interface RuleDefinitionListResponse {
  items: RuleDefinitionSummary[]
  next_cursor?: string | null
}

export type RuleDefinitionOrganizationScope = 'organization' | 'system'

export interface RuleRevisionBody {
  name: string
  summary: string
  stage: RuleStage
  predicate: RulePredicate
  effect: RuleEffect
  tags: string[]
  metadata: Record<string, unknown>
}

export interface RuleDefinitionCreateRequest extends RuleRevisionBody {
  rule_id: string
  organization_scope: RuleDefinitionOrganizationScope
}

export interface RuleDefinitionRevisionDocument extends RuleRevisionBody {
  organization_id?: string | null
  rule_id: string
  revision: number
  status: RuleRevisionStatus
  created_at: string
  created_by_user_id?: string | null
  published_at?: string | null
}

export interface RuleBindingDocument {
  binding_id: string
  organization_id?: string | null
  rule_id: string
  revision: number
  mode: RuleBindingMode
  order: number
  scope: RuleBindingScope
  metadata: Record<string, unknown>
  created_at: string
  created_by_user_id?: string | null
  updated_at: string
  updated_by_user_id?: string | null
}

export interface RuleBindingListResponse {
  items: RuleBindingDocument[]
}

export interface RuleBindingCreateRequest {
  organization_scope: 'organization' | 'system'
  binding_id: string
  rule_id: string
  revision: number
  mode: RuleBindingMode
  order: number
  scope: RuleBindingScope
  metadata?: Record<string, unknown>
  confirm_broad_scope: boolean
}

export interface RuleBindingUpdateRequest {
  revision?: number
  mode?: RuleBindingMode
  order?: number
  scope?: RuleBindingScope
  metadata?: Record<string, unknown>
  confirm_broad_scope?: boolean
}

export type RulePredicate = Record<string, unknown>

export interface RuleEffect {
  kind: string
  code?: string
  message?: string | null
  tool_ref?: string | null
  [key: string]: unknown
}

export interface RuleDefinition {
  rule_id: string
  revision: number
  name: string
  summary: string
  stage: RuleStage
  predicate: RulePredicate
  effect: RuleEffect
  tags: string[]
  metadata: Record<string, unknown>
}

export interface RuleBinding {
  binding_id: string
  rule_id: string
  revision: number
  mode: RuleBindingMode
  order: number
  scope: RuleBindingScope
  metadata: Record<string, unknown>
}

export interface RuleLibrary {
  library_id: string
  version: string
  rules: RuleDefinition[]
}

export interface RuleProgram {
  library: RuleLibrary
  bindings: RuleBinding[]
}

export interface RuleProgramResolutionInput {
  organization_id?: string | null
  agent_id?: string | null
  step_id?: string | null
  channel?: RuleChannel | null
  event_type?: string | null
  tool_ref?: string | null
}

export interface RuleConversationContext {
  organization_id?: string | null
  conversation_id?: string | null
  agent_id?: string | null
  step_id?: string | null
  channel?: RuleChannel | null
  turn_count: number
}

export interface RuleTurnContext {
  event_type?: string | null
  text?: string | null
  text_length?: number | null
  metadata: Record<string, unknown>
}

export interface RuleToolContext {
  ref?: string | null
  args: Record<string, unknown>
  outcome?: string | null
}

export interface RuleTimeContext {
  current_hour?: number | null
  current_day?: string | null
}

export interface RuleEvaluationContext {
  stage: RuleStage
  conversation: RuleConversationContext
  turn: RuleTurnContext
  tool: RuleToolContext
  facts: Record<string, unknown>
  metadata: Record<string, unknown>
  time: RuleTimeContext
}

export interface RuleEvaluationRequest {
  program: RuleProgram
  context: RuleEvaluationContext
}

export interface RuleTrace {
  binding_id: string
  rule_id: string
  revision: number
  outcome: 'skipped' | 'no_match' | 'matched' | 'shadow_match' | 'error'
  mode: RuleBindingMode
  effect_kind?: string | null
  detail?: string | null
}

export interface RuleMatch {
  binding_id: string
  rule_id: string
  revision: number
  rule_name: string
  mode: RuleBindingMode
  effect: RuleEffect
}

export interface RuleDecision {
  traces: RuleTrace[]
  matched_rules: RuleMatch[]
  terminal_effect?: RuleEffect | null
}

export interface ComposeAmbiguity {
  code: string
  message: string
  hint?: string | null
}

export interface ComposeBindingScope {
  channels: RuleChannel[]
  agent_ids: string[]
  scenario_ids: string[]
  step_ids: string[]
  tool_refs: string[]
  event_types: string[]
}

export interface ComposePolicyProposal {
  outcome: 'ready' | 'needs_clarification' | 'unsupported'
  summary: string
  rule_body?: RuleRevisionBody | null
  expression?: string | null
  binding_scope: ComposeBindingScope
  affected_tags: string[]
  ambiguities: ComposeAmbiguity[]
  example_match?: string | null
  example_no_match?: string | null
}

export interface ComposePolicyRequest {
  text: string
  rule_id_hint?: string | null
  suggested_tags?: string[]
}

export interface ComposeSaveDraftRequest {
  rule_id: string
  organization_scope?: 'organization' | 'system'
  rule_body: RuleRevisionBody
  suggested_binding_scope?: ComposeBindingScope | null
}
