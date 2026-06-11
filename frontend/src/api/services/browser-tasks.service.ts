import { apiClient } from '../client'

export type BrowserTaskState =
  | 'queued'
  | 'awaiting_approval'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type BrowserApprovalState =
  | 'not_required'
  | 'pending'
  | 'approved'
  | 'denied'
  | 'expired'
  | 'cancelled'

export type BrowserCredentialKind = 'password' | 'api_key' | 'oauth' | 'session' | 'mfa'
export type BrowserArtifactKind = 'screenshot' | 'result_json' | 'download' | 'action_log'

export interface BrowserCredentialRequirement {
  kind: BrowserCredentialKind
  name: string
  provider?: string | null
  auth_type?: string | null
  required: boolean
  description?: string | null
}

export interface BrowserTaskPack {
  pack_id: string
  version: string
  display_name: string
  description?: string | null
  allowed_domains: string[]
  start_url?: string | null
  performs_write: boolean
  input_schema: Record<string, unknown>
  result_schema: Record<string, unknown>
  credentials: BrowserCredentialRequirement[]
  execution_policy: {
    max_execution_seconds: number
    max_steps: number
    allow_downloads: boolean
    allow_uploads: boolean
    capture_screenshots: boolean
    retry_policy: {
      max_attempts: number
      retryable_error_kinds: string[]
    }
  }
  approval_policy: {
    approval_required: boolean
    approval_kinds: string[]
    approval_ttl_seconds: number
    require_reapproval_after_navigation: boolean
  }
  artifact_policy: {
    allowed_artifacts: BrowserArtifactKind[]
    retain_artifacts: boolean
    redact_sensitive_values: boolean
  }
  operator_policy: {
    operator_takeover_enabled: boolean
    operator_takeover_after_seconds?: number | null
    operator_message?: string | null
  }
}

export interface BrowserTask {
  task_id: string
  organization_id?: string | null
  agent_id?: string | null
  conversation_id: string
  title: string
  summary?: string | null
  requested_channel: string
  task_pack_id?: string | null
  task_pack_version?: string | null
  start_url?: string | null
  input_payload: Record<string, unknown>
  credential_refs: Record<string, string>
  state: BrowserTaskState
  approval_state: BrowserApprovalState
  current_approval_id?: string | null
  lease_owner?: string | null
  lease_expires_at?: string | null
  attempt_count: number
  result: Record<string, unknown>
  error?: string | null
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
  started_at?: string | null
  finished_at?: string | null
}

export interface BrowserApproval {
  approval_id: string
  task_id: string
  organization_id?: string | null
  conversation_id: string
  kind: string
  state: BrowserApprovalState
  prompt: string
  context: Record<string, unknown>
  decision_reason?: string | null
  requested_at: string
  expires_at?: string | null
  decided_at?: string | null
}

export interface BrowserTaskEvent {
  event_id: string
  task_id: string
  organization_id?: string | null
  conversation_id: string
  event_sequence: number
  event_type: string
  message: string
  metadata: Record<string, unknown>
  created_at: string
}

export interface BrowserTaskSnapshot {
  task: BrowserTask
  approval?: BrowserApproval | null
  recent_events: BrowserTaskEvent[]
}

export interface BrowserTaskCreatePayload {
  conversation_id: string
  organization_id?: string | null
  agent_id?: string | null
  title: string
  summary?: string | null
  requested_channel?: string
  task_pack_id?: string | null
  task_pack_version?: string | null
  start_url?: string | null
  input_payload?: Record<string, unknown>
  credential_refs?: Record<string, string>
  requires_approval?: boolean
  approval_kind?: string
  approval_prompt?: string | null
  approval_ttl_seconds?: number | null
  metadata?: Record<string, unknown>
}

export interface BrowserTaskInboxParams {
  organization_id?: string | null
  conversation_id?: string | null
  state?: BrowserTaskState | null
  approval_state?: BrowserApprovalState | null
  limit?: number
}

export const browserTasksService = {
  listTaskPacks(): Promise<BrowserTaskPack[]> {
    return apiClient.get('/internal/browser-task-packs')
  },

  listInbox(params: BrowserTaskInboxParams = {}): Promise<BrowserTaskSnapshot[]> {
    return apiClient.get('/internal/browser-task-inbox', {
      params: {
        organization_id: params.organization_id,
        conversation_id: params.conversation_id,
        state: params.state,
        approval_state: params.approval_state,
        limit: params.limit,
      },
    })
  },

  listConversationTasks(
    conversationId: string,
    organizationId?: string | null,
  ): Promise<BrowserTaskSnapshot[]> {
    return apiClient.get('/internal/browser-tasks', {
      params: { conversation_id: conversationId, organization_id: organizationId },
    })
  },

  createTask(payload: BrowserTaskCreatePayload): Promise<BrowserTaskSnapshot> {
    return apiClient.post('/internal/browser-tasks', payload)
  },

  approve(approvalId: string, reason?: string | null, organizationId?: string | null): Promise<BrowserTaskSnapshot> {
    return apiClient.post(
      `/internal/browser-tasks/approvals/${encodeURIComponent(approvalId)}/approve`,
      { reason: reason ?? null },
      { params: { organization_id: organizationId } },
    )
  },

  deny(approvalId: string, reason?: string | null, organizationId?: string | null): Promise<BrowserTaskSnapshot> {
    return apiClient.post(
      `/internal/browser-tasks/approvals/${encodeURIComponent(approvalId)}/deny`,
      { reason: reason ?? null },
      { params: { organization_id: organizationId } },
    )
  },

  cancel(taskId: string, reason: string, organizationId?: string | null): Promise<BrowserTaskSnapshot> {
    return apiClient.post(
      `/internal/browser-tasks/${encodeURIComponent(taskId)}/cancel`,
      { reason },
      { params: { organization_id: organizationId } },
    )
  },
}
