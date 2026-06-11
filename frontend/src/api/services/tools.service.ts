/**
 * Tools API Service - Client for custom tools/APIs management.
 *
 * Provides endpoints for:
 * - API Connection CRUD
 * - Tool definition CRUD
 * - Tool testing
 * - Execution logs
 * - Agent-tool assignments
 */
import { apiClient } from '../client';

// ==================== Types ====================

export type AuthType = 'none' | 'api_key' | 'bearer' | 'basic' | 'oauth2' | 'mtls';
export type ConnectionStatus = 'active' | 'inactive' | 'error';
export type ToolType = 'http' | 'built_in' | 'composite' | 'mcp';
export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
export type ExecutionStatus = 'success' | 'error' | 'timeout' | 'rate_limited' | 'validation_error';
export type TriggerSource = 'llm' | 'workflow' | 'manual_test' | 'webhook';
export type RateLimitScope = 'global' | 'organization' | 'agent' | 'conversation';

// Retry configuration
export interface RetryConfig {
  max_retries: number;
  base_delay_ms: number;
  max_delay_ms: number;
  jitter_factor: number;
}

// Rate limit configuration
export interface RateLimitConfig {
  max_requests: number;
  window_seconds: number;
  scope: RateLimitScope;
}

// Tool annotations (MCP-compatible)
export interface ToolAnnotations {
  readOnlyHint: boolean;
  destructive: boolean;
  requiresConfirmation: boolean;
  idempotent: boolean;
  sideEffectFree: boolean;
  openWorldHint: boolean;
}

// Voice feedback configuration
export interface VoiceFeedback {
  executing?: string;
  success?: string;
  error?: string;
  timeout?: string;
  auto_retry?: boolean;
  max_silent_retries?: number;
}

// Tool example
export interface ToolExample {
  user_message: string;
  tool_call: Record<string, unknown>;
  expected_output?: string;
}

// JSON Schema for tool input
export interface InputSchema {
  type: 'object';
  properties: Record<string, unknown>;
  required: string[];
  additionalProperties: boolean;
}

// ==================== API Connection Types ====================

export interface APIConnection {
  id: string;
  organization_id: string;
  created_by?: string;
  name: string;
  slug: string;
  description?: string;
  icon?: string;
  base_url: string;
  auth_type: AuthType;
  default_headers: Record<string, string>;
  timeout_seconds: number;
  retry_config: RetryConfig;
  rate_limit_config?: RateLimitConfig;
  webhook_signature_header: string;
  status: ConnectionStatus;
  last_health_check_at?: string;
  last_success_at?: string;
  last_error_at?: string;
  last_error_message?: string;
  total_requests: number;
  total_errors: number;
  avg_latency_ms?: number;
  created_at: string;
  updated_at: string;
}

export interface APIConnectionCreate {
  name: string;
  slug: string;
  description?: string;
  icon?: string;
  base_url: string;
  auth_type: AuthType;
  auth_config: Record<string, unknown>;
  default_headers?: Record<string, string>;
  timeout_seconds?: number;
  retry_config?: Partial<RetryConfig>;
  rate_limit_config?: RateLimitConfig;
  webhook_secret?: string;
  webhook_signature_header?: string;
}

export interface APIConnectionUpdate {
  name?: string;
  description?: string;
  icon?: string;
  base_url?: string;
  auth_type?: AuthType;
  auth_config?: Record<string, unknown>;
  default_headers?: Record<string, string>;
  timeout_seconds?: number;
  retry_config?: Partial<RetryConfig>;
  rate_limit_config?: RateLimitConfig;
  webhook_secret?: string;
  webhook_signature_header?: string;
  status?: ConnectionStatus;
}

export interface APIConnectionListResponse {
  items: APIConnection[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

export interface ConnectionHealthCheckResponse {
  connection_id: string;
  connection_name: string;
  healthy: boolean;
  latency_ms?: number;
  error_message?: string;
  checked_at: string;
}

// ==================== Tool Types ====================

export interface Tool {
  id: string;
  organization_id: string;
  api_connection_id?: string;
  created_by?: string;
  name: string;
  display_name: string;
  description: string;
  category?: string;
  version: string;
  deprecated: boolean;
  deprecation_message?: string;
  successor_tool_id?: string;
  tool_type: ToolType;
  http_method?: HttpMethod;
  endpoint_path?: string;
  input_schema: InputSchema;
  output_schema?: Record<string, unknown>;
  output_mapping?: Record<string, string>;
  response_template?: string;
  request_body_template?: string;
  request_headers?: Record<string, string>;
  query_params_template?: Record<string, string>;
  annotations: ToolAnnotations;
  voice_feedback?: VoiceFeedback;
  timeout_seconds?: number;
  cache_ttl_seconds?: number;
  examples?: ToolExample[];
  agent_ids?: string[];
  is_active: boolean;
  invocation_count: number;
  success_count: number;
  failure_count: number;
  avg_latency_ms?: number;
  reliability_score: number;
  last_invoked_at?: string;
  created_at: string;
  updated_at: string;
  api_connection?: {
    id: string;
    name: string;
    slug: string;
    icon?: string;
    status: ConnectionStatus;
  };
}

export interface ToolCreate {
  name: string;
  display_name: string;
  description: string;
  api_connection_id?: string;
  category?: string;
  tool_type?: ToolType;
  http_method?: HttpMethod;
  endpoint_path?: string;
  input_schema?: InputSchema;
  output_schema?: Record<string, unknown>;
  output_mapping?: Record<string, string>;
  response_template?: string;
  request_body_template?: string;
  request_headers?: Record<string, string>;
  query_params_template?: Record<string, string>;
  annotations?: Partial<ToolAnnotations>;
  voice_feedback?: Partial<VoiceFeedback>;
  timeout_seconds?: number;
  cache_ttl_seconds?: number;
  examples?: ToolExample[];
  agent_ids?: string[];
}

export interface ToolUpdate {
  display_name?: string;
  description?: string;
  category?: string;
  http_method?: HttpMethod;
  endpoint_path?: string;
  input_schema?: InputSchema;
  output_schema?: Record<string, unknown>;
  output_mapping?: Record<string, string>;
  response_template?: string;
  request_body_template?: string;
  request_headers?: Record<string, string>;
  query_params_template?: Record<string, string>;
  annotations?: Partial<ToolAnnotations>;
  voice_feedback?: Partial<VoiceFeedback>;
  timeout_seconds?: number;
  cache_ttl_seconds?: number;
  examples?: ToolExample[];
  agent_ids?: string[];
  is_active?: boolean;
  deprecated?: boolean;
  deprecation_message?: string;
  successor_tool_id?: string;
}

export interface ToolListResponse {
  items: Tool[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

export interface ToolForLLM {
  type: 'function';
  function: {
    name: string;
    description: string;
    parameters: InputSchema;
  };
}

export interface ExternalToolCatalogItem {
  ref: string;
  provider: 'builtin' | 'integration' | 'custom' | 'mcp';
  namespace: string;
  function_name: string;
  display_name: string;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema?: Record<string, unknown> | null;
  annotations: Record<string, unknown>;
  capability_group?: string | null;
  tags: string[];
  is_active: boolean;
  auth_status: string;
}

// ==================== Tool Test Types ====================

export interface ToolTestRequest {
  input_params: Record<string, unknown>;
  mock_response?: Record<string, unknown>;
  dry_run?: boolean;
}

export interface ToolTestResponse {
  success: boolean;
  tool_id: string;
  tool_name: string;
  input_params: Record<string, unknown>;
  validation_errors: string[];
  request_preview?: {
    method: HttpMethod;
    url: string;
    headers: Record<string, string>;
    params: Record<string, unknown>;
  };
  execution_log_id?: string;
  status?: ExecutionStatus;
  status_code?: number;
  latency_ms?: number;
  output_data?: Record<string, unknown>;
  formatted_response?: string;
  error_message?: string;
}

// ==================== Execution Log Types ====================

export interface ToolExecutionLog {
  id: string;
  organization_id: string;
  tool_id: string;
  agent_id?: string;
  conversation_id?: string;
  triggered_by: TriggerSource;
  tool_call_id?: string;
  input_params: Record<string, unknown>;
  output_data?: Record<string, unknown>;
  formatted_response?: string;
  status: ExecutionStatus;
  status_code?: number;
  error_type?: string;
  error_message?: string;
  error_code?: string;
  retryable?: boolean;
  user_visible?: boolean;
  suggested_action?: string;
  latency_ms: number;
  retry_count: number;
  cache_hit: boolean;
  trace_id?: string;
  span_id?: string;
  contains_pii: boolean;
  pii_redacted: boolean;
  created_at: string;
}

export interface ToolExecutionLogListResponse {
  items: ToolExecutionLog[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

// ==================== Tool Statistics Types ====================

export interface ToolStatistics {
  tool_id: string;
  tool_name: string;
  period_start: string;
  period_end: string;
  total_invocations: number;
  successful_invocations: number;
  failed_invocations: number;
  success_rate: number;
  avg_latency_ms: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  p99_latency_ms: number;
  cache_hit_rate: number;
  error_breakdown: Record<string, number>;
  trigger_breakdown: Record<string, number>;
}

// ==================== Agent Tool Assignment Types ====================

export interface AgentToolAssignment {
  id: string;
  organization_id: string;
  agent_id: string;
  tool_id: string;
  config_overrides: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface AgentToolAssignmentCreate {
  agent_id: string;
  tool_id: string;
  config_overrides?: Record<string, unknown>;
}

// ==================== Filter Parameters ====================

export interface ConnectionFilterParams {
  status?: ConnectionStatus;
  page?: number;
  page_size?: number;
}

export interface ToolFilterParams {
  connection_id?: string;
  category?: string;
  tool_type?: ToolType;
  is_active?: boolean;
  agent_id?: string;
  page?: number;
  page_size?: number;
}

export interface ExecutionLogFilterParams {
  status?: ExecutionStatus;
  triggered_by?: TriggerSource;
  agent_id?: string;
  conversation_id?: string;
  page?: number;
  page_size?: number;
}

// ==================== Tooling Redesign Types ====================

export type ToolKind = 'api' | 'integration' | 'builtin' | 'code' | 'composite' | 'mcp';

export interface ToolDefinition {
  tool_definition_id: string;
  organization_id: string;
  connection_id: string | null;
  kind: ToolKind;
  tool_ref: string;
  function_name: string | null;
  display_name: string;
  description: string;
  endpoint_path: string | null;
  http_method: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  timeout_ms: number;
  read_only: boolean;
  enabled: boolean;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface CustomToolAciDraft {
  display_name: string;
  description?: string;
  http_method: string;
  endpoint_path?: string;
  read_only?: boolean;
  purpose?: string;
  use_when?: string;
  avoid_when?: string;
}

export interface CustomToolAciDraftWarning {
  code: string;
  message: string;
}

function normalizeAciText(value: string | undefined | null): string | null {
  const trimmed = String(value ?? '').trim();
  return trimmed.length > 0 ? trimmed : null;
}

export function buildCustomToolMetadata(draft: CustomToolAciDraft): Record<string, unknown> {
  const displayName = normalizeAciText(draft.display_name) ?? 'Custom API';
  const description = normalizeAciText(draft.description);
  const method = normalizeAciText(draft.http_method)?.toUpperCase() ?? 'POST';
  const endpointPath = normalizeAciText(draft.endpoint_path) ?? '/';
  const readOnly = Boolean(draft.read_only);
  const explicitPurpose = normalizeAciText(draft.purpose);
  const explicitUseWhen = normalizeAciText(draft.use_when);
  const explicitAvoidWhen = normalizeAciText(draft.avoid_when);

  const purpose = explicitPurpose
    ?? description
    ?? `Call ${displayName} through ${method} ${endpointPath} when the agent needs live external system behavior.`;

  const useWhen = explicitUseWhen
    ?? (readOnly
      ? `Use when the agent needs live ${displayName} data from ${method} ${endpointPath} before answering.`
      : `Use when the agent must trigger a live ${displayName} action through ${method} ${endpointPath}.`);

  const avoidWhen = explicitAvoidWhen
    ?? (readOnly
      ? 'Do not use when internal knowledge, cached state, or an already-known answer is sufficient.'
      : 'Do not use when a read-only lookup or internal knowledge answer would be sufficient.');

  return {
    purpose,
    when_to_use: [useWhen],
    when_not_to_use: [avoidWhen],
    failure_modes: [
      {
        kind: 'transient_upstream_error',
        description: `${displayName} is temporarily unavailable, rate limited, or timing out.`,
        retryable: true,
      },
      {
        kind: 'permanent_upstream_error',
        description: `${displayName} rejected the request because the credentials, arguments, or endpoint contract are invalid.`,
        retryable: false,
      },
    ],
    output_validation_mode: 'warn',
    _aci: {
      purpose_source: explicitPurpose ? 'author' : 'scaffold',
      when_to_use_source: explicitUseWhen ? 'author' : 'scaffold',
      when_not_to_use_source: explicitAvoidWhen ? 'author' : 'scaffold',
      scaffolding_used: !(explicitPurpose && explicitUseWhen && explicitAvoidWhen),
    },
  };
}

export function getCustomToolAciDraftWarnings(draft: CustomToolAciDraft): CustomToolAciDraftWarning[] {
  const warnings: CustomToolAciDraftWarning[] = [];
  const method = normalizeAciText(draft.http_method)?.toUpperCase() ?? 'POST';
  const readOnly = Boolean(draft.read_only);

  if (!normalizeAciText(draft.purpose)) {
    warnings.push({
      code: 'purpose_scaffolded',
      message: 'Purpose is blank. Ruhu will scaffold it from the description and endpoint shape.',
    });
  }
  if (!normalizeAciText(draft.use_when)) {
    warnings.push({
      code: 'use_when_scaffolded',
      message: '“Use when” guidance is blank. Ruhu will scaffold a default model hint.',
    });
  }
  if (!normalizeAciText(draft.avoid_when) && !readOnly && method !== 'GET') {
    warnings.push({
      code: 'avoid_when_recommended',
      message: 'Add an “avoid when” warning for write actions so the model has a clearer misuse boundary.',
    });
  }
  return warnings;
}

export function getCustomToolAciStatus(metadata: Record<string, unknown> | null | undefined): {
  label: string;
  variant: 'scaffolded' | 'authored';
} {
  const aci = (metadata?._aci ?? null) as Record<string, unknown> | null;
  const scaffoldingUsed = Boolean(aci?.scaffolding_used);
  if (scaffoldingUsed) {
    return { label: 'guided defaults', variant: 'scaffolded' };
  }
  return { label: 'author guided', variant: 'authored' };
}

export interface ToolDefinitionListResponse {
  items: ToolDefinition[];
}

export interface AgentToolBinding {
  binding_id: string;
  organization_id: string;
  agent_id: string;
  tool_definition_id: string;
  connection_id: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface AgentToolBindingListResponse {
  items: AgentToolBinding[];
}

export interface CallableCatalogItem {
  tool_definition_id: string;
  kind: ToolKind;
  ref: string;
  function_name: string | null;
  callable_name: string;
  display_name: string;
  description: string;
  http_method?: string | null;
  endpoint_path?: string | null;
  input_schema: Record<string, unknown>;
  read_only: boolean;
  provider_slug?: string | null;
  connection_status?: string | null;
}

export interface CallableCatalogResponse {
  apis: CallableCatalogItem[];
  integrations: CallableCatalogItem[];
  builtin: CallableCatalogItem[];
}

// ==================== Provider Template Types ====================

export interface ProviderTemplateStarterTool {
  ref: string;
  function_name: string;
  display_name: string;
  description: string;
  read_only: boolean;
}

export interface ProviderTemplate {
  slug: string;
  display_name: string;
  category: string;
  icon: string;
  auth_type: string;
  base_url: string;
  capabilities: string[];
  starter_tools: ProviderTemplateStarterTool[];
  has_oauth: boolean;
  /** Placeholder keys the frontend must collect before calling setupProvider
   *  (e.g., `["subdomain"]` for Zendesk). Empty means zero-config setup. */
  required_config: string[];
  /** True when the template has no preset URLs — frontend should show a
   *  full URL form (auth URL, token URL, API base) instead of placeholder fields. */
  requires_custom_urls: boolean;
}

export interface ProviderSetupResponse {
  connection_id: string;
  provider_slug: string;
  status: string;
  tools_created: number;
  oauth_start_url?: string | null;
}

// ==================== Service Classes ====================

const BASE_URL = '/tools';
const CONNECTIONS_URL = `${BASE_URL}/connections`;

/**
 * API Connection Service - Manage external API connections.
 */
class APIConnectionServiceClass {
  /**
   * Create a new API connection.
   */
  async create(data: APIConnectionCreate): Promise<APIConnection> {
    return apiClient.post<APIConnection>(CONNECTIONS_URL, data);
  }

  /**
   * Get an API connection by ID.
   */
  async get(connectionId: string): Promise<APIConnection> {
    return apiClient.get<APIConnection>(`${CONNECTIONS_URL}/${connectionId}`);
  }

  /**
   * List API connections with filtering.
   */
  async list(params?: ConnectionFilterParams): Promise<APIConnectionListResponse> {
    const queryParams = new URLSearchParams();

    if (params?.status) queryParams.append('status_filter', params.status);
    if (params?.page) queryParams.append('page', String(params.page));
    if (params?.page_size) queryParams.append('page_size', String(params.page_size));

    const query = queryParams.toString();
    return apiClient.get<APIConnectionListResponse>(`${CONNECTIONS_URL}${query ? `?${query}` : ''}`);
  }

  /**
   * Update an API connection.
   */
  async update(connectionId: string, data: APIConnectionUpdate): Promise<APIConnection> {
    return apiClient.patch<APIConnection>(`${CONNECTIONS_URL}/${connectionId}`, data);
  }

  /**
   * Delete an API connection.
   */
  async delete(connectionId: string): Promise<void> {
    return apiClient.delete(`${CONNECTIONS_URL}/${connectionId}`);
  }

  /**
   * Check connection health.
   */
  async healthCheck(connectionId: string): Promise<ConnectionHealthCheckResponse> {
    return apiClient.post<ConnectionHealthCheckResponse>(
      `${CONNECTIONS_URL}/${connectionId}/health-check`,
      {}
    );
  }
}

/**
 * Tool Service - Manage tool definitions.
 */
class ToolServiceClass {
  /**
   * Create a new tool.
   */
  async create(data: ToolCreate): Promise<Tool> {
    return apiClient.post<Tool>(BASE_URL, data);
  }

  /**
   * Get a tool by ID.
   */
  async get(toolId: string): Promise<Tool> {
    return apiClient.get<Tool>(`${BASE_URL}/${toolId}`);
  }

  /**
   * List tools with filtering.
   */
  async list(params?: ToolFilterParams): Promise<ToolListResponse> {
    const queryParams = new URLSearchParams();

    if (params?.connection_id) queryParams.append('connection_id', params.connection_id);
    if (params?.category) queryParams.append('category', params.category);
    if (params?.tool_type) queryParams.append('tool_type', params.tool_type);
    if (params?.is_active !== undefined) queryParams.append('is_active', String(params.is_active));
    if (params?.agent_id) queryParams.append('agent_id', params.agent_id);
    if (params?.page) queryParams.append('page', String(params.page));
    if (params?.page_size) queryParams.append('page_size', String(params.page_size));

    const query = queryParams.toString();
    return apiClient.get<ToolListResponse>(`${BASE_URL}${query ? `?${query}` : ''}`);
  }

  /**
   * Update a tool.
   */
  async update(toolId: string, data: ToolUpdate): Promise<Tool> {
    return apiClient.patch<Tool>(`${BASE_URL}/${toolId}`, data);
  }

  /**
   * Delete a tool.
   */
  async delete(toolId: string): Promise<void> {
    return apiClient.delete(`${BASE_URL}/${toolId}`);
  }

  /**
   * Test a tool.
   */
  async test(toolId: string, request: ToolTestRequest): Promise<ToolTestResponse> {
    return apiClient.post<ToolTestResponse>(`${BASE_URL}/${toolId}/test`, request);
  }

  /**
   * Get tool in LLM function calling format.
   */
  async getForLLM(toolId: string): Promise<ToolForLLM> {
    return apiClient.get<ToolForLLM>(`${BASE_URL}/${toolId}/for-llm`);
  }

  /**
   * Get tool statistics.
   */
  async getStatistics(toolId: string, days?: number): Promise<ToolStatistics> {
    const query = days ? `?days=${days}` : '';
    return apiClient.get<ToolStatistics>(`${BASE_URL}/${toolId}/statistics${query}`);
  }

  /**
   * List execution logs for a tool.
   */
  async listExecutionLogs(
    toolId: string,
    params?: ExecutionLogFilterParams
  ): Promise<ToolExecutionLogListResponse> {
    const queryParams = new URLSearchParams();

    if (params?.status) queryParams.append('status_filter', params.status);
    if (params?.triggered_by) queryParams.append('triggered_by', params.triggered_by);
    if (params?.agent_id) queryParams.append('agent_id', params.agent_id);
    if (params?.conversation_id) queryParams.append('conversation_id', params.conversation_id);
    if (params?.page) queryParams.append('page', String(params.page));
    if (params?.page_size) queryParams.append('page_size', String(params.page_size));

    const query = queryParams.toString();
    return apiClient.get<ToolExecutionLogListResponse>(
      `${BASE_URL}/${toolId}/logs${query ? `?${query}` : ''}`
    );
  }

  /**
   * Get a single execution log.
   */
  async getExecutionLog(logId: string): Promise<ToolExecutionLog> {
    return apiClient.get<ToolExecutionLog>(`${BASE_URL}/logs/${logId}`);
  }

  /**
   * List tools available to a specific agent.
   */
  async listForAgent(agentId: string, includeGlobal = true): Promise<Tool[]> {
    const query = `?include_global=${includeGlobal}`;
    const response = await apiClient.get<{ items: Tool[] }>(`${BASE_URL}/agents/${agentId}/tools${query}`);
    return response.items ?? [];
  }

  /**
   * Get the unified external tool catalog for an agent.
   */
  async getCatalog(agentId: string): Promise<ExternalToolCatalogItem[]> {
    return apiClient.get<ExternalToolCatalogItem[]>(
      `${BASE_URL}/agents/${agentId}/tool-catalog`
    );
  }

  /**
   * Assign a tool to an agent.
   */
  async assignToAgent(data: AgentToolAssignmentCreate): Promise<AgentToolAssignment> {
    return apiClient.post<AgentToolAssignment>(`${BASE_URL}/assignments`, data);
  }

  /**
   * Remove tool assignment from agent.
   */
  async removeAssignment(assignmentId: string): Promise<void> {
    return apiClient.delete(`${BASE_URL}/assignments/${assignmentId}`);
  }

  /**
   * Confirm a pending tool invocation.
   */
  async confirmInvocation(invocationId: string): Promise<unknown> {
    return apiClient.post(`${BASE_URL}/invocations/${invocationId}/confirm`);
  }

  /**
   * Cancel a pending tool invocation.
   */
  async cancelInvocation(invocationId: string): Promise<unknown> {
    return apiClient.post(`${BASE_URL}/invocations/${invocationId}/cancel`);
  }

  // ── Tooling Redesign API methods ──────────────────────────────────

  /**
   * List tool definitions with optional kind filter.
   */
  async listDefinitions(params?: { kind?: ToolKind; enabled_only?: boolean }): Promise<ToolDefinition[]> {
    const query = new URLSearchParams();
    if (params?.kind) query.set('kind', params.kind);
    if (params?.enabled_only !== undefined) query.set('enabled_only', String(params.enabled_only));
    const qs = query.toString();
    const response = await apiClient.get<ToolDefinitionListResponse>(
      `/api/tools/definitions${qs ? `?${qs}` : ''}`
    );
    return response.items ?? [];
  }

  async createDefinition(payload: Partial<ToolDefinition> & {
    kind: ToolKind;
    tool_ref: string;
    display_name: string;
    description?: string;
  }): Promise<ToolDefinition> {
    return apiClient.post<ToolDefinition>('/api/tools/definitions', payload);
  }

  async updateDefinition(
    toolDefinitionId: string,
    payload: Partial<ToolDefinition>,
  ): Promise<ToolDefinition> {
    return apiClient.patch<ToolDefinition>(
      `/api/tools/definitions/${encodeURIComponent(toolDefinitionId)}`,
      payload,
    );
  }

  /**
   * Delete a tool definition. The backend rejects deletes of system /
   * built-in callables — callers should gate the UI affordance on the
   * tool's kind. Usage-aware confirmation (warning when steps still
   * reference the tool) lives in the caller.
   */
  async deleteDefinition(toolDefinitionId: string): Promise<void> {
    await apiClient.delete(
      `/api/tools/definitions/${encodeURIComponent(toolDefinitionId)}`,
    );
  }

  /**
   * Get the callable catalog for an agent (grouped by kind).
   */
  async getCallableCatalog(agentId: string): Promise<CallableCatalogResponse> {
    return apiClient.get<CallableCatalogResponse>(
      `/api/agents/${agentId}/callable-catalog`
    );
  }

  /**
   * List per-agent connection overrides.
   */
  async listAgentBindings(agentId: string): Promise<AgentToolBinding[]> {
    const response = await apiClient.get<AgentToolBindingListResponse>(
      `/api/agents/${agentId}/tool-bindings`
    );
    return response.items ?? [];
  }

  /**
   * Create or update a per-agent connection override.
   */
  async createAgentBinding(
    agentId: string,
    data: { tool_definition_id: string; connection_id: string; enabled?: boolean }
  ): Promise<AgentToolBinding> {
    return apiClient.post<AgentToolBinding>(
      `/api/agents/${agentId}/tool-bindings`,
      data
    );
  }

  /**
   * Remove a per-agent connection override.
   */
  async deleteAgentBinding(agentId: string, bindingId: string): Promise<void> {
    return apiClient.delete(`/api/agents/${agentId}/tool-bindings/${bindingId}`);
  }

  // ── Provider Templates ────────────────────────────────────────────

  /**
   * List all known provider templates.
   */
  async listProviderTemplates(): Promise<ProviderTemplate[]> {
    return apiClient.get<ProviderTemplate[]>('/api/tools/provider-templates');
  }

  /**
   * Set up a provider from a template (creates connection + starter tools).
   *
   * For templates with `required_config` (e.g., Zendesk subdomain), pass
   * `template_config: { subdomain: "acme" }` — the backend substitutes
   * these into the OAuth URLs and base URL.
   *
   * For `custom_oauth` templates, pass `auth_url_override`, `token_url_override`,
   * and `base_url` directly so the backend can use a fully custom OAuth flow.
   */
  async setupProvider(
    slug: string,
    data?: {
      display_name?: string;
      base_url?: string;
      auth_url_override?: string;
      token_url_override?: string;
      template_config?: Record<string, string>;
    }
  ): Promise<ProviderSetupResponse> {
    return apiClient.post<ProviderSetupResponse>(
      `/api/tools/provider-templates/${slug}/setup`,
      data ?? {}
    );
  }

  /**
   * Test action-config code in a sandbox (no conversation needed).
   *
   * The action-config sandbox endpoint has not been reintroduced for the
   * agent-document editor. Throw a clear error so the editor's catch block
   * surfaces something meaningful instead of letting the request hit the SPA
   * fallback and parse HTML as a result. Re-enable once the backend ships a
   * step-scoped action-config sandbox endpoint.
   */
  async testActionConfig(
    _agentId: string,
    _stateId: string,
    _data: {
      code: string;
      callable_functions_code?: string;
      callable_api_refs?: string[];
      callable_integrations?: string[];
      callable_system_refs?: string[];
      test_facts?: Record<string, unknown>;
      timeout_seconds?: number;
    }
  ): Promise<ActionConfigTestResult> {
    throw new Error(
      'Action-config testing is unavailable: the sandbox endpoint was removed during the agent-definition migration and has no replacement yet.'
    );
  }
}

export interface ActionConfigTestResult {
  status: 'success' | 'error' | 'timeout' | 'security_violation';
  output: Record<string, unknown> | null;
  variables_modified: Record<string, unknown>;
  logs: string[];
  error: string | null;
  error_type: string | null;
}

// Export singleton instances
export const apiConnectionService = new APIConnectionServiceClass();
export const toolService = new ToolServiceClass();
