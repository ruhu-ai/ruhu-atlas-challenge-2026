/**
 * Canvas Types
 *
 * TypeScript definitions for Agent Canvas entities.
 */

// ==================== Node Types ====================

// Re-export canonical types from the single source of truth.
export { NODE_TYPE as NODE_TYPES, PALETTE_NODE_TYPES, CODE_CAPABLE_TYPES as CODE_CAPABLE_NODES } from './nodeTypes'
export type { NodeType } from './nodeTypes'

// Local aliases used by this file's helpers.
import { NODE_TYPE, CODE_CAPABLE_TYPES, type NodeType as _NodeType } from './nodeTypes'

export type CodeCapableNodeType = (typeof CODE_CAPABLE_TYPES)[number]

// ==================== Code Node Configuration ====================

export interface CodeNodeConfig {
  code: string
  language: 'python'
  timeout_seconds?: number
  input_schema?: Record<string, unknown>
  output_variable?: string
  enable_globals?: boolean
}

export interface ScriptNodeConfig extends CodeNodeConfig {
  script_name?: string
  description?: string
}

export interface TransformNodeConfig {
  transform_code: string
  input_mapping?: Record<string, string>
  output_mapping?: Record<string, string>
}

export interface ConditionNodeConfig {
  condition_expression: string
  true_branch_label?: string
  false_branch_label?: string
}

// ==================== Global Variables ====================

export interface GlobalVariable {
  name: string
  value: any
  var_type: 'string' | 'number' | 'boolean' | 'object' | 'array'
  description?: string
  is_readonly: boolean
  created_at: string
  updated_at: string
}

// ==================== Lifecycle Hooks ====================

export interface LifecycleHooks {
  initialization_code?: string
  post_conversation_code?: string
  error_handler_code?: string
}

// ==================== Canvas Entities ====================

export interface CanvasVersion {
  id: string
  organization_id: string
  agent_id: string
  name: string
  description?: string
  version_number: number
  status: string
  authoring_mode: 'scenario' | 'flow'
  canvas_data: Record<string, unknown>
  viewport: {
    x: number
    y: number
    zoom: number
  }
  published_at?: string
  published_by?: string
  created_at: string
  updated_at: string
}

export interface CanvasScenarioMetadata {
  id: string
  root_step_id: string
  name: string
  summary?: string
  linked_test_case_ids?: string[]
  template_key?: string
  status?: string
  order_index?: number
}

export interface CanvasNode {
  id: string
  organization_id: string
  canvas_version_id: string
  template_id?: string
  node_type: string
  label: string
  description?: string
  position_x: number
  position_y: number
  config: Record<string, unknown>
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
  validation_rules: Record<string, unknown>
  is_enabled: boolean
  created_at: string
  updated_at: string
}

export interface CanvasEdge {
  id: string
  organization_id: string
  canvas_version_id: string
  source_node_id: string
  target_node_id: string
  edge_type: string
  label?: string
  config: Record<string, unknown>
  condition?: string
  condition_type?: string
  is_enabled: boolean
  created_at: string
  updated_at: string
}

export interface NodeTemplate {
  id: string
  organization_id: string
  name: string
  description?: string
  category: string
  node_type: string
  icon?: string
  default_config: Record<string, unknown>
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
  validation_rules: Record<string, unknown>
  is_public: boolean
  is_active: boolean
  usage_count: number
  created_at: string
  updated_at: string
}

export interface CreateCanvasVersionData {
  organization_id: string
  agent_id: string
  name: string
  description?: string
  version_number: number
  authoring_mode?: 'scenario' | 'flow'
  canvas_data?: Record<string, unknown>
  viewport?: {
    x: number
    y: number
    zoom: number
  }
}

export interface UpdateCanvasVersionData {
  name?: string
  description?: string
  status?: string
  canvas_data?: Record<string, unknown>
  viewport?: {
    x: number
    y: number
    zoom: number
  }
}

export interface CanvasDiffFieldChange {
  from?: unknown
  to?: unknown
}

export type CanvasDiffChangedEntry = string | Record<string, CanvasDiffFieldChange>

export interface CanvasVersionDiffSummary {
  added_nodes?: number
  removed_nodes?: number
  changed_nodes?: number
  added_edges?: number
  removed_edges?: number
  changed_edges?: number
}

export interface CanvasVersionMetadataDiff {
  changed_keys?: CanvasDiffChangedEntry[]
}

export interface CanvasVersionCanvasDataDiff {
  added_keys?: string[]
  removed_keys?: string[]
  changed_keys?: string[]
}

export interface CanvasWorkflowChangedNode {
  id?: string
  changes?: Record<string, CanvasDiffFieldChange>
  [key: string]: unknown
}

export interface CanvasWorkflowChangedEdge {
  id?: string
  changes?: Record<string, CanvasDiffFieldChange>
  [key: string]: unknown
}

export interface CanvasWorkflowNodeSnapshot {
  id?: string
  node_type?: string
  label?: string
  [key: string]: unknown
}

export interface CanvasWorkflowEdgeSnapshot {
  id?: string
  source?: string
  target?: string
  label?: string
  [key: string]: unknown
}

export interface CanvasVersionWorkflowDiff {
  added_nodes?: CanvasWorkflowNodeSnapshot[]
  removed_nodes?: CanvasWorkflowNodeSnapshot[]
  changed_nodes?: CanvasWorkflowChangedNode[]
  added_edges?: CanvasWorkflowEdgeSnapshot[]
  removed_edges?: CanvasWorkflowEdgeSnapshot[]
  changed_edges?: CanvasWorkflowChangedEdge[]
}

export interface CanvasVersionDiffResponse {
  source_canvas_version_id: string
  against_canvas_version_id: string
  agent_id: string
  summary: CanvasVersionDiffSummary
  metadata_diff: CanvasVersionMetadataDiff
  canvas_data_diff: CanvasVersionCanvasDataDiff
  workflow_diff: CanvasVersionWorkflowDiff
  generated_at: string
  cache_hit: boolean
}

export interface CanvasVersionRevertResponse {
  source_canvas_version_id: string
  draft_canvas_version_id: string
  draft_version_number: number
  draft_status: string
  reason?: string
}

export interface CreateCanvasNodeData {
  canvas_version_id: string
  template_id?: string
  node_type: string
  label: string
  description?: string
  position_x?: number
  position_y?: number
  config?: Record<string, unknown>
  input_schema?: Record<string, unknown>
  output_schema?: Record<string, unknown>
  validation_rules?: Record<string, unknown>
}

export interface UpdateCanvasNodeData {
  label?: string
  description?: string
  position_x?: number
  position_y?: number
  config?: Record<string, unknown>
  input_schema?: Record<string, unknown>
  output_schema?: Record<string, unknown>
  validation_rules?: Record<string, unknown>
  is_enabled?: boolean
}

export interface CreateCanvasEdgeData {
  canvas_version_id: string
  source_node_id: string
  target_node_id: string
  edge_type?: string
  label?: string
  config?: Record<string, unknown>
  condition?: string
  condition_type?: string
}

export interface SaveCanvasRequest {
  name?: string
  description?: string
  canvas_data?: Record<string, unknown>
  viewport?: {
    x: number
    y: number
    zoom: number
  }
  nodes: Array<
    Omit<CreateCanvasNodeData, 'canvas_version_id'> &
    Partial<Pick<CanvasNode, 'id' | 'template_id' | 'description' | 'input_schema' | 'output_schema' | 'validation_rules' | 'is_enabled'>>
  >
  edges: Array<
    Omit<CreateCanvasEdgeData, 'canvas_version_id'> &
    Partial<Pick<CanvasEdge, 'id' | 'is_enabled'>>
  >
}

export interface SaveCanvasResponse {
  version: CanvasVersion
  nodes: CanvasNode[]
  edges: CanvasEdge[]
  node_id_map: Record<string, string>
}

// ==================== Scenario Document v2 Types ====================

// ---------------------------------------------------------------------------
// Condition operators
// ---------------------------------------------------------------------------

export type ConditionOperator =
  | 'is_set'
  | 'is_not_set'
  | 'equals'
  | 'not_equals'
  | 'contains'
  | 'greater_than'
  | 'less_than'

// ---------------------------------------------------------------------------
// Effects — side effects fired when an outcome is taken
// ---------------------------------------------------------------------------

export interface SetVariableEffect {
  kind: 'set_variable'
  name: string
  value: unknown
}

export interface MarkEffect {
  kind: 'mark'
  name: string
}

export interface TriggerEffect {
  kind: 'trigger'
  name: string
  payload?: Record<string, unknown>
}

export interface CreateTicketEffect {
  kind: 'create_ticket'
  queue?: string
  fields?: Record<string, unknown>
}

export interface TransferEffect {
  kind: 'transfer'
  target: HandoffTarget
}

export type EffectV2 =
  | SetVariableEffect
  | MarkEffect
  | TriggerEffect
  | CreateTicketEffect
  | TransferEffect

// ---------------------------------------------------------------------------
// Outcome when-conditions
// ---------------------------------------------------------------------------

export interface DefaultWhen {
  kind: 'default'
}

export interface VariableWhen {
  kind: 'variable'
  variable: string
  operator: ConditionOperator
  value?: unknown
}

export interface EventWhen {
  kind: 'event'
  event: 'user_replied' | 'no_input' | 'timeout' | 'upload_success' | 'upload_failed'
}

export interface ResultWhen {
  kind: 'result'
  source: string
  path?: string
  operator: ConditionOperator
  value?: unknown
}

export interface AttemptsExhaustedWhen {
  kind: 'attempts_exhausted'
}

export type OutcomeWhen =
  | DefaultWhen
  | VariableWhen
  | EventWhen
  | ResultWhen
  | AttemptsExhaustedWhen

// ---------------------------------------------------------------------------
// Outcome next — routing after an outcome fires
// ---------------------------------------------------------------------------

export interface OutcomeNext {
  scenario_id?: string
  step_id?: string
  end?: boolean
}

// ---------------------------------------------------------------------------
// Outcome — one named exit path from a step
// ---------------------------------------------------------------------------

export interface OutcomeV2 {
  id: string
  label: string
  when: OutcomeWhen
  effects: EffectV2[]
  next?: OutcomeNext
}

// ---------------------------------------------------------------------------
// Retry policy — for collect steps
// ---------------------------------------------------------------------------

export interface RetryPolicy {
  max_attempts: number
  reprompt_text?: string
  on_exhausted_outcome_id?: string
}

// ---------------------------------------------------------------------------
// Shared sub-objects
// ---------------------------------------------------------------------------

export interface Prompt {
  mode: 'verbatim' | 'template'
  text: string
}

export interface CaptureConfig {
  slot_names: string[]
  entity_hints: string[]
  max_turns?: number
  no_input_timeout_seconds?: number
}

export interface AiPrompt {
  system: string
  user_template?: string
  output_mode: 'text' | 'structured'
  output_schema?: Record<string, unknown>
}

export interface HandoffTarget {
  type: 'queue' | 'agent' | 'phone_number'
  value: string
}

// ---------------------------------------------------------------------------
// Do-step operations
// ---------------------------------------------------------------------------

export interface ToolOperation {
  kind: 'tool'
  tool_ref: string
  input?: Record<string, unknown>
}

export interface CodeOperation {
  kind: 'code'
  language: 'python'
  code: string
  callable_tool_refs: string[]
  callable_functions_code: string
  input_schema: Record<string, unknown> | null
}

export type DoOperation = ToolOperation | CodeOperation

// ---------------------------------------------------------------------------
// Step kinds (discriminated union on 'kind')
// ---------------------------------------------------------------------------

export interface _BaseStep {
  id: string
  key: string
  title: string
  notes?: string
  outcomes: OutcomeV2[]
  advanced: Record<string, unknown>
}

export interface SayStep extends _BaseStep {
  kind: 'say'
  prompt: Prompt
}

export interface CollectStep extends _BaseStep {
  kind: 'collect'
  prompt: Prompt
  capture?: CaptureConfig
  retry_policy?: RetryPolicy
}

export interface DecideStep extends _BaseStep {
  kind: 'decide'
  source: 'variable' | 'event' | 'result'
  path?: string
}

export interface DoStep extends _BaseStep {
  kind: 'do'
  operation: DoOperation
}

export interface AiStep extends _BaseStep {
  kind: 'ai'
  prompt: AiPrompt
}

export interface HandoffStep extends _BaseStep {
  kind: 'handoff'
  target: HandoffTarget
  preamble?: string
}

export interface EndStep extends _BaseStep {
  kind: 'end'
  closing_text?: string
  disposition?: string
}

export type StepV2 = SayStep | CollectStep | DecideStep | DoStep | AiStep | HandoffStep | EndStep

// ---------------------------------------------------------------------------
// Document-level variable definitions
// ---------------------------------------------------------------------------

export interface VariableDef {
  name: string
  type: 'string' | 'number' | 'boolean' | 'object' | 'array'
  description?: string
  required?: boolean
}

// ---------------------------------------------------------------------------
// Attached resources
// ---------------------------------------------------------------------------

export interface ScenarioResources {
  policy_ids: string[]
  tool_refs: string[]
  linked_test_case_ids: string[]
}

// ---------------------------------------------------------------------------
// Scenario — one named section of the conversation
// ---------------------------------------------------------------------------

export interface ScenarioV2 {
  id: string
  key: string
  name: string
  summary?: string
  order: number
  steps: StepV2[]
  resources?: ScenarioResources
}

// ---------------------------------------------------------------------------
// ScenarioDocumentBody — the JSONB payload
// ---------------------------------------------------------------------------

export interface ScenarioDocumentBody {
  version: string
  entry_scenario_id: string
  variables: VariableDef[]
  scenarios: ScenarioV2[]
}

export interface ScenarioDocumentResponse {
  canvas_version_id: string
  authoring_mode: string
  document: ScenarioDocumentBody
}

export interface CreateNodeTemplateData {
  name: string
  description?: string
  category: string
  node_type: string
  icon?: string
  default_config?: Record<string, unknown>
  input_schema?: Record<string, unknown>
  output_schema?: Record<string, unknown>
  validation_rules?: Record<string, unknown>
  is_public?: boolean
}

// ==================== Canvas Data with Code Execution ====================

/**
 * Extended canvas data structure supporting code execution features
 */
export interface CanvasData {
  // Standard canvas data
  nodes?: Record<string, unknown>
  edges?: Record<string, unknown>

  // Code execution support
  global_variables?: GlobalVariable[]
  lifecycle_hooks?: LifecycleHooks

  // Metadata
  version?: string
  last_validated?: string
}

// ==================== Code Node Helpers ====================

/**
 * Check if a node type supports code execution
 */
export function isCodeCapableNode(nodeType: string): boolean {
  return (CODE_CAPABLE_TYPES as readonly string[]).includes(nodeType)
}

/**
 * Get default config for code node type
 */
export function getDefaultCodeNodeConfig(nodeType: string): Partial<CodeNodeConfig> {
  switch (nodeType) {
    case NODE_TYPE.CODE:
      return {
        code: '# Enter your Python code here\nresult = input_data\n',
        language: 'python',
        timeout_seconds: 30,
        enable_globals: true,
      }
    default:
      return {}
  }
}
