/**
 * Channels Service
 *
 * Handles channel configuration API calls for connecting agents to
 * communication channels like WhatsApp, SMS, etc.
 */

import { apiClient } from '../client'

// Types for channel configuration
export interface ChannelConfig {
  id: string
  channel_type: 'whatsapp' | 'sms' | 'voice' | 'chat'
  is_enabled: boolean
  phone_number_id?: string
  verify_token?: string
  created_at?: string
  updated_at?: string
}

export interface WhatsAppConfigCreate {
  agent_id: string
  access_token: string
  phone_number_id: string
  app_secret: string
  verify_token?: string
  is_enabled?: boolean
}

export interface WhatsAppConfigUpdate {
  agent_id?: string
  access_token?: string
  phone_number_id?: string
  app_secret?: string
  verify_token?: string
  is_enabled?: boolean
}

export interface WhatsAppConfigResponse {
  id: string
  organization_id: string
  agent_id?: string
  agent_name?: string
  channel_type: string
  is_enabled: boolean
  phone_number_id: string
  verify_token: string
  display_phone_number?: string
  verified_name?: string
  created_at?: string
  updated_at?: string
}

// Embedded Signup OAuth types
export interface OAuthPhoneNumber {
  phone_number_id: string
  display_phone_number: string
  verified_name: string
  quality_rating: string
  waba_id: string
  business_name: string
}

export interface OAuthExchangeResponse {
  session_key: string
  phone_numbers: OAuthPhoneNumber[]
}

export interface OAuthSelectRequest {
  session_key: string
  agent_id: string
  phone_number_id: string
}

class ChannelsService {
  /**
   * Get all channels configured for a specific agent
   */
  async getAgentChannels(agentId: string): Promise<ChannelConfig[]> {
    const response = await apiClient.get<ChannelConfig[]>(`/agents/${agentId}/channels`)
    return response
  }

  /**
   * Create WhatsApp configuration for an agent
   */
  async createWhatsAppConfig(config: WhatsAppConfigCreate): Promise<WhatsAppConfigResponse> {
    const response = await apiClient.post<WhatsAppConfigResponse>(
      '/webhooks/whatsapp/config',
      config
    )
    return response
  }

  /**
   * Update WhatsApp configuration
   */
  async updateWhatsAppConfig(config: WhatsAppConfigUpdate): Promise<WhatsAppConfigResponse> {
    const response = await apiClient.patch<WhatsAppConfigResponse>(
      '/webhooks/whatsapp/config',
      config
    )
    return response
  }

  /**
   * Delete WhatsApp configuration
   */
  async deleteWhatsAppConfig(): Promise<void> {
    await apiClient.delete('/webhooks/whatsapp/config')
  }

  /**
   * Exchange a Meta OAuth code for a list of WABA phone numbers (Embedded Signup step 1).
   */
  async exchangeOAuthCode(code: string, agentId: string): Promise<OAuthExchangeResponse> {
    return apiClient.post<OAuthExchangeResponse>('/webhooks/whatsapp/oauth/exchange', {
      code,
      agent_id: agentId,
    })
  }

  /**
   * Finalise Embedded Signup by selecting a phone number (step 2).
   * Returns the persisted WhatsApp config including verify_token.
   */
  async selectPhoneNumber(req: OAuthSelectRequest): Promise<WhatsAppConfigResponse> {
    return apiClient.post<WhatsAppConfigResponse>('/webhooks/whatsapp/oauth/select', req)
  }

  /**
   * Test WhatsApp connection
   */
  async testWhatsAppConnection(): Promise<{ status: string; message: string; phone_number_id?: string }> {
    const response = await apiClient.post<{ status: string; message: string; phone_number_id?: string }>(
      '/webhooks/whatsapp/config/test',
      {}
    )
    return response
  }

  /**
   * Get available channel types based on agent type
   */
  getAvailableChannels(agentType: 'chat' | 'voice' | 'multimodal'): string[] {
    switch (agentType) {
      case 'chat':
        return ['whatsapp', 'sms']
      case 'voice':
        return ['voice']
      case 'multimodal':
        return ['whatsapp', 'sms', 'voice']
      default:
        return []
    }
  }
}

export const channelsService = new ChannelsService()
