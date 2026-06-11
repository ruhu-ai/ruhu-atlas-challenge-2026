/**
 * Code Execution Service
 *
 * Handles code validation, execution, and session management for canvas code nodes.
 */

import { apiClient } from '../client'

// ==================== Types ====================

export interface CodeValidationRequest {
  code: string
  language?: 'python'
}

export interface CodeValidationResponse {
  is_valid: boolean
  errors: string[]
  warnings: string[]
  validated_at: string
}

export interface CodeExecutionRequest {
  code: string
  context?: Record<string, unknown>
  input_data?: Record<string, unknown>
  timeout_seconds?: number
}

export interface CodeExecutionResponse {
  success: boolean
  result: any
  output: string
  error?: string
  execution_time_ms: number
  executed_at: string
}

export interface GlobalVariable {
  name: string
  value: any
  var_type: string
  description?: string
  is_readonly: boolean
  created_at: string
  updated_at: string
}

export interface GlobalVariableCreate {
  name: string
  value: any
  var_type?: string
  description?: string
  is_readonly?: boolean
}

export interface LifecycleHook {
  hook_type: 'initialization' | 'pre_node' | 'post_node' | 'post_conversation' | 'error_handler'
  code: string
  enabled: boolean
  description?: string
}

export interface CodeSession {
  session_id: string
  organization_id: string
  created_at: string
  is_active: boolean
  global_variables: Record<string, GlobalVariable>
  lifecycle_hooks: LifecycleHook[]
}

export interface CreateSessionRequest {
  initial_variables?: Record<string, GlobalVariableCreate>
  initialization_code?: string
}

export interface ExecuteNodeRequest {
  node_id: string
  code: string
  context?: Record<string, unknown>
  input_data?: Record<string, unknown>
}

export interface ExecuteNodeResponse {
  success: boolean
  result: any
  output: string
  error?: string
  execution_time_ms: number
  global_variables_updated: string[]
}

// ==================== Service ====================

class CodeExecutionService {
  /**
   * Validate Python code without executing it
   */
  async validateCode(request: CodeValidationRequest): Promise<CodeValidationResponse> {
    const response = await apiClient.post<CodeValidationResponse>(
      '/code-execution/validate',
      request
    )
    return response
  }

  /**
   * Execute code in a sandboxed environment
   */
  async executeCode(request: CodeExecutionRequest): Promise<CodeExecutionResponse> {
    const response = await apiClient.post<CodeExecutionResponse>(
      '/code-execution/execute',
      request
    )
    return response
  }

  // ==================== Session Management ====================

  /**
   * Create a new code execution session
   */
  async createSession(request: CreateSessionRequest = {}): Promise<CodeSession> {
    const response = await apiClient.post<CodeSession>('/code-execution/sessions', request)
    return response
  }

  /**
   * Get session details
   */
  async getSession(sessionId: string): Promise<CodeSession> {
    const response = await apiClient.get<CodeSession>(`/code-execution/sessions/${sessionId}`)
    return response
  }

  /**
   * End and cleanup a session
   */
  async endSession(sessionId: string, postConversationCode?: string): Promise<void> {
    await apiClient.delete(`/code-execution/sessions/${sessionId}`, {
      data: postConversationCode ? { post_conversation_code: postConversationCode } : undefined,
    })
  }

  /**
   * Execute code within a specific node context
   */
  async executeNode(
    sessionId: string,
    request: ExecuteNodeRequest
  ): Promise<ExecuteNodeResponse> {
    const response = await apiClient.post<ExecuteNodeResponse>(
      `/code-execution/sessions/${sessionId}/execute`,
      request
    )
    return response
  }

  // ==================== Global Variables ====================

  /**
   * Get all global variables for a session
   */
  async getGlobalVariables(sessionId: string): Promise<Record<string, GlobalVariable>> {
    const response = await apiClient.get<Record<string, GlobalVariable>>(
      `/code-execution/sessions/${sessionId}/variables`
    )
    return response
  }

  /**
   * Set or update a global variable
   */
  async setGlobalVariable(
    sessionId: string,
    variable: GlobalVariableCreate
  ): Promise<GlobalVariable> {
    const response = await apiClient.post<GlobalVariable>(
      `/code-execution/sessions/${sessionId}/variables`,
      variable
    )
    return response
  }

  /**
   * Delete a global variable
   */
  async deleteGlobalVariable(sessionId: string, variableName: string): Promise<void> {
    await apiClient.delete(`/code-execution/sessions/${sessionId}/variables/${variableName}`)
  }

  // ==================== Lifecycle Hooks ====================

  /**
   * Register a lifecycle hook
   */
  async registerHook(sessionId: string, hook: LifecycleHook): Promise<LifecycleHook> {
    const response = await apiClient.post<LifecycleHook>(
      `/code-execution/sessions/${sessionId}/hooks`,
      hook
    )
    return response
  }

  /**
   * Get all lifecycle hooks for a session
   */
  async getHooks(sessionId: string): Promise<LifecycleHook[]> {
    const response = await apiClient.get<LifecycleHook[]>(
      `/code-execution/sessions/${sessionId}/hooks`
    )
    return response
  }
}

export const codeExecutionService = new CodeExecutionService()
