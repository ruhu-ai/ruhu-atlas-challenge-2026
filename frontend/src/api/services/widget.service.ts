/**
 * Widget Service
 *
 * Handles all widget-related API calls:
 * - Widget enable/disable for agents
 * - Widget config updates
 * - Publishable key management
 * - Embed code generation
 * - Widget analytics
 */

import { apiClient } from '../client'

// --- Publishable Keys ---

export interface PublishableKey {
  id: string
  key_prefix: string
  key_type?: 'publishable'
  agent_id: string
  allowed_origins: string[]
  is_active: boolean
  created_at: string
  last_used_at?: string
  usage_count?: number
}

export interface PublishableKeyCreated extends PublishableKey {
  /** Full key — only returned once at creation */
  key: string
}

export interface CreatePublishableKeyRequest {
  name: string
  agent_id: string
  environment: 'live' | 'test'
  allowed_origins?: string[]
}

// --- Widget Config ---

export interface WidgetConfigUpdate {
  widget_mode?: 'chat' | 'voice' | 'multimodal'
  widget_config?: Record<string, unknown>
}

export interface WidgetConfigResponse {
  agent_id: string
  is_widget_enabled: boolean
  widget_mode: string
  widget_config: Record<string, unknown>
}

// --- Widget Enable ---

export interface WidgetEnableResponse {
  agent_id: string
  is_widget_enabled: boolean
  widget_mode?: 'chat' | 'voice' | 'multimodal'
  embed_code?: string
  widget_url?: string
  publishable_key?: string
  publishable_key_prefix?: string
  key_prefix?: string
  message: string
}

// --- Embed Code ---

export interface EmbedCodeResponse {
  agent_id: string
  embed_code: string
  widget_url: string
  publishable_key_prefix: string
  key_placeholder?: string
  message?: string
}

// --- Widget Analytics ---

export interface WidgetAnalytics {
  period: string
  total_sessions: number
  total_messages: number
  total_voice_calls: number
  avg_session_duration_seconds: number
  avg_messages_per_session: number
  unique_visitors: number
  daily_breakdown: WidgetDailyMetric[]
  // Backend may return these instead of the flat fields above
  total_events?: number
  event_counts?: Record<string, number>
}

export interface WidgetDailyMetric {
  date: string
  sessions: number
  messages: number
  voice_calls: number
  unique_visitors: number
}

class WidgetService {
  // --- Widget Enable/Disable ---

  async enableWidget(agentId: string): Promise<WidgetEnableResponse> {
    return apiClient.post<WidgetEnableResponse>(
      `/agents/${agentId}/widget/enable`,
      {},
    )
  }

  async disableWidget(agentId: string): Promise<WidgetEnableResponse> {
    return apiClient.post<WidgetEnableResponse>(
      `/agents/${agentId}/widget/disable`,
      {},
    )
  }

  // --- Widget Config ---

  async updateWidgetConfig(
    agentId: string,
    data: WidgetConfigUpdate,
  ): Promise<WidgetConfigResponse> {
    return apiClient.patch<WidgetConfigResponse>(
      `/agents/${agentId}/widget-config`,
      data,
    )
  }

  // --- Embed Code ---

  async getEmbedCode(agentId: string): Promise<EmbedCodeResponse> {
    return apiClient.get<EmbedCodeResponse>(
      `/agents/${agentId}/embed-code`,
    )
  }

  // --- Publishable Keys ---

  async listPublishableKeys(agentId?: string): Promise<PublishableKey[]> {
    return apiClient.get<PublishableKey[]>(
      '/api-keys/publishable',
      agentId ? { params: { agent_id: agentId } } : undefined,
    )
  }

  async createPublishableKey(
    data: CreatePublishableKeyRequest,
  ): Promise<PublishableKeyCreated> {
    return apiClient.post<PublishableKeyCreated>(
      '/api-keys/publishable',
      data,
    )
  }

  async updateKeyOrigins(
    keyId: string,
    origins: string[],
  ): Promise<PublishableKey> {
    return apiClient.put<PublishableKey>(
      `/api-keys/${keyId}/allowed-origins`,
      { allowed_origins: origins },
    )
  }

  async revokeKey(keyId: string): Promise<{ message: string; key_id: string }> {
    return apiClient.delete<{ message: string; key_id: string }>(
      `/api-keys/${keyId}`,
    )
  }

  async deleteKeyPermanent(keyId: string): Promise<{ message: string; key_id: string }> {
    return apiClient.delete<{ message: string; key_id: string }>(
      `/api-keys/${keyId}/permanent`,
    )
  }

  // --- Analytics ---

  async getWidgetAnalytics(
    agentId: string,
    period: string = '7d',
  ): Promise<WidgetAnalytics> {
    return apiClient.get<WidgetAnalytics>(
      `/agents/${agentId}/widget-analytics`,
      { params: { period } },
    )
  }
}

export const widgetService = new WidgetService()
