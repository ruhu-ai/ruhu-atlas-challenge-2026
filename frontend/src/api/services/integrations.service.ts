/**
 * Integrations API Service
 *
 * Client for managing third-party integrations (CRM, Calendar, Ticketing).
 * Supports OAuth flows, provider configuration, and agent assignments.
 */
import { apiClient } from '../client'

// ==================== Types ====================

export type ProviderType = 'crm' | 'calendar' | 'ticketing' | 'generic'
export type ProviderStatus = 'pending_auth' | 'connected' | 'error' | 'disconnected'

export interface IntegrationProvider {
  id: string
  organization_id: string
  provider_type: ProviderType
  provider_name: string
  display_name: string
  description?: string
  status: ProviderStatus
  is_active: boolean
  has_valid_token: boolean
  created_at: string
  updated_at: string
}

export interface ProviderConfigCreate {
  provider_type: ProviderType
  provider_name: string
  display_name: string
  description?: string
  config: Record<string, unknown>
  field_mappings?: Record<string, unknown>
}

export interface OAuthAuthorizationRequest {
  provider_id: string
  redirect_uri: string
}

export interface OAuthAuthorizationResponse {
  authorization_url: string
  state: string
}

export interface OAuthCallbackRequest {
  provider_id: string
  code: string
  state: string
  code_verifier?: string
}

export interface ProviderHealthResponse {
  provider_id: string
  provider_name: string
  is_healthy: boolean
  last_check: string
  error_message?: string
}

export interface AvailableProvidersResponse {
  providers: Record<string, string[]>
}

export interface AgentIntegration {
  id: string
  agent_id: string
  provider_id: string
  provider_type: ProviderType
  is_active: boolean
  config_overrides?: Record<string, unknown>
}

// Response from GET /integrations/agents/{agent_id}/integrations
export interface AgentIntegrationDetail {
  agent_integration_id: string
  agent_id: string
  provider_id: string
  provider_type: ProviderType
  provider_name: string
  display_name: string
  is_active: boolean
  provider_is_active: boolean
  provider_status: ProviderStatus
  has_valid_token: boolean
  created_at: string | null
}

export interface AgentIntegrationsResponse {
  agent_id: string
  integrations: AgentIntegrationDetail[]
  count: number
  function_calling_enabled: boolean
}

// Provider metadata for UI display
export interface ProviderMetadata {
  name: string
  displayName: string
  type: ProviderType
  icon: string
  color: string
  description: string
  capabilities: string[]
}

// Pre-defined provider metadata for UI
export const PROVIDER_METADATA: Record<string, ProviderMetadata> = {
  // CRM
  salesforce: {
    name: 'salesforce',
    displayName: 'Salesforce',
    type: 'crm',
    icon: '☁️',
    color: '#00A1E0',
    description: 'Connect to Salesforce CRM',
    capabilities: ['Look up customers', 'Create leads', 'Log activities', 'Update cases'],
  },
  hubspot: {
    name: 'hubspot',
    displayName: 'HubSpot',
    type: 'crm',
    icon: '🟠',
    color: '#FF7A59',
    description: 'Connect to HubSpot CRM',
    capabilities: ['Look up contacts', 'Create deals', 'Log activities', 'Manage tickets'],
  },
  zoho: {
    name: 'zoho',
    displayName: 'Zoho CRM',
    type: 'crm',
    icon: '📊',
    color: '#E42527',
    description: 'Connect to Zoho CRM',
    capabilities: ['Look up leads', 'Create contacts', 'Log calls', 'Manage deals'],
  },
  pipedrive: {
    name: 'pipedrive',
    displayName: 'Pipedrive',
    type: 'crm',
    icon: '💼',
    color: '#25292C',
    description: 'Connect to Pipedrive CRM',
    capabilities: ['Look up persons', 'Create deals', 'Log activities', 'Manage pipeline'],
  },
  // Calendar
  google_calendar: {
    name: 'google_calendar',
    displayName: 'Google Calendar',
    type: 'calendar',
    icon: '📅',
    color: '#4285F4',
    description: 'Connect to Google Calendar',
    capabilities: ['Check availability', 'Book appointments', 'Send invites', 'Manage events'],
  },
  microsoft_calendar: {
    name: 'microsoft_calendar',
    displayName: 'Microsoft 365',
    type: 'calendar',
    icon: '🟦',
    color: '#0078D4',
    description: 'Connect to Microsoft Outlook Calendar',
    capabilities: ['Check availability', 'Book appointments', 'Send invites', 'Manage events'],
  },
  // Ticketing
  zendesk: {
    name: 'zendesk',
    displayName: 'Zendesk',
    type: 'ticketing',
    icon: '🎫',
    color: '#03363D',
    description: 'Connect to Zendesk Support',
    capabilities: ['Create tickets', 'Update status', 'Add comments', 'Assign agents'],
  },
  freshdesk: {
    name: 'freshdesk',
    displayName: 'Freshdesk',
    type: 'ticketing',
    icon: '🔵',
    color: '#25C16F',
    description: 'Connect to Freshdesk',
    capabilities: ['Create tickets', 'Update status', 'Add notes', 'Track resolution'],
  },
  jira: {
    name: 'jira',
    displayName: 'Jira',
    type: 'ticketing',
    icon: '🔷',
    color: '#0052CC',
    description: 'Connect to Jira Service Management',
    capabilities: ['Create issues', 'Update status', 'Add comments', 'Link tickets'],
  },
}

// ==================== Service ====================

class IntegrationsServiceClass {
  // Track active OAuth flows for cleanup
  private activeOAuthCleanups: Map<string, () => void> = new Map()

  /**
   * List all configured providers for the organization.
   */
  async listProviders(providerType?: ProviderType): Promise<IntegrationProvider[]> {
    const params = providerType ? `?provider_type=${providerType}` : ''
    return apiClient.get<IntegrationProvider[]>(`/integrations/providers${params}`)
  }

  /**
   * Get available provider types from the backend.
   */
  async getAvailableProviders(): Promise<AvailableProvidersResponse> {
    return apiClient.get<AvailableProvidersResponse>('/integrations/providers/available')
  }

  /**
   * Create a new provider configuration (before OAuth).
   */
  async createProvider(data: ProviderConfigCreate): Promise<IntegrationProvider> {
    return apiClient.post<IntegrationProvider>('/integrations/providers', data)
  }

  /**
   * Delete a provider configuration.
   */
  async deleteProvider(providerId: string): Promise<void> {
    return apiClient.delete(`/integrations/providers/${providerId}`)
  }

  /**
   * Start OAuth authorization flow.
   */
  async startOAuth(request: OAuthAuthorizationRequest): Promise<OAuthAuthorizationResponse> {
    return apiClient.post<OAuthAuthorizationResponse>('/integrations/oauth/authorize', request)
  }

  /**
   * Handle OAuth callback (exchange code for tokens).
   */
  async handleOAuthCallback(request: OAuthCallbackRequest): Promise<{ message: string; provider_id: string }> {
    return apiClient.post('/integrations/oauth/callback', request)
  }

  /**
   * Check provider health.
   */
  async checkHealth(providerId: string): Promise<ProviderHealthResponse> {
    return apiClient.get<ProviderHealthResponse>(`/integrations/providers/${providerId}/health`)
  }

  /**
   * Assign a provider to an agent.
   */
  async assignProviderToAgent(agentId: string, providerId: string): Promise<{ message: string }> {
    return apiClient.post(`/integrations/agents/${agentId}/providers/${providerId}`, {})
  }

  /**
   * List integrations connected to a specific agent.
   * This returns only integrations assigned to this agent, not all org integrations.
   */
  async listAgentIntegrations(agentId: string): Promise<AgentIntegrationsResponse> {
    return apiClient.get<AgentIntegrationsResponse>(`/integrations/agents/${agentId}/integrations`)
  }

  /**
   * Get provider metadata for UI display.
   */
  getProviderMetadata(providerName: string): ProviderMetadata | undefined {
    return PROVIDER_METADATA[providerName]
  }

  /**
   * Get all providers by type for UI display.
   */
  getProvidersByType(type: ProviderType): ProviderMetadata[] {
    return Object.values(PROVIDER_METADATA).filter((p) => p.type === type)
  }

  /**
   * Get the OAuth redirect URI for the current environment.
   */
  getOAuthRedirectUri(): string {
    const baseUrl = window.location.origin
    return `${baseUrl}/integrations/oauth/callback`
  }

  /**
   * Abort a specific OAuth flow (cleanup interval and event listener)
   * Call this when a component unmounts during an OAuth flow
   */
  abortOAuthFlow(providerId: string): void {
    const cleanup = this.activeOAuthCleanups.get(providerId)
    if (cleanup) {
      cleanup()
    }
  }

  /**
   * Abort all active OAuth flows
   * Call this on logout or page navigation to prevent memory leaks
   */
  abortAllOAuthFlows(): void {
    this.activeOAuthCleanups.forEach((cleanup) => cleanup())
    this.activeOAuthCleanups.clear()
  }

  /**
   * Open OAuth popup and handle the flow.
   *
   * Security: Uses postMessage with origin validation for cross-window communication.
   * Cleanup: Properly cleans up event listeners and intervals on completion/cancellation.
   */
  async connectProvider(
    providerName: string,
    providerType: ProviderType,
    displayName: string
  ): Promise<IntegrationProvider> {
    // Step 1: Create provider config
    const provider = await this.createProvider({
      provider_type: providerType,
      provider_name: providerName,
      display_name: displayName,
      config: {}, // Platform OAuth credentials are used from backend env vars
    })

    // Step 2: Get OAuth authorization URL
    const redirectUri = this.getOAuthRedirectUri()
    console.debug('[OAuth] redirect_uri being sent:', redirectUri)
    let authResponse: OAuthAuthorizationResponse

    try {
      authResponse = await this.startOAuth({
        provider_id: provider.id,
        redirect_uri: redirectUri,
      })
      console.debug('[OAuth] authorization_url from backend:', authResponse.authorization_url)
    } catch (error) {
      // Clean up the created provider if OAuth fails to start
      await this.deleteProvider(provider.id).catch(() => {
        // Ignore cleanup errors
      })
      throw error
    }

    // Step 3: Open OAuth popup
    return new Promise((resolve, reject) => {
      const width = 600
      const height = 700
      const left = window.screenX + (window.outerWidth - width) / 2
      const top = window.screenY + (window.outerHeight - height) / 2

      const popup = window.open(
        authResponse.authorization_url,
        'oauth_popup',
        `width=${width},height=${height},left=${left},top=${top},scrollbars=yes,resizable=yes`
      )

      if (!popup) {
        // Clean up the created provider if popup fails
        this.deleteProvider(provider.id).catch(() => {})
        reject(new Error('Failed to open OAuth popup. Please allow popups for this site.'))
        return
      }

      let checkClosedInterval: ReturnType<typeof setInterval> | null = null
      let isCompleted = false

      // Cleanup function to prevent memory leaks
      const cleanup = () => {
        if (isCompleted) return
        isCompleted = true
        window.removeEventListener('message', handleMessage)
        if (checkClosedInterval) {
          clearInterval(checkClosedInterval)
          checkClosedInterval = null
        }
        // Remove from active cleanups
        this.activeOAuthCleanups.delete(provider.id)
      }

      // Register cleanup for external abort (e.g., component unmount)
      this.activeOAuthCleanups.set(provider.id, cleanup)

      // Listen for OAuth callback message
      const handleMessage = async (event: MessageEvent) => {
        // Security: Validate origin strictly
        if (event.origin !== window.location.origin) return
        if (isCompleted) return

        if (event.data?.type === 'oauth_callback') {
          cleanup()
          try { popup.close() } catch { /* COOP may block this — popup will close on its own */ }

          if (event.data.error) {
            cleanup()
            // Clean up the created provider on error
            await this.deleteProvider(provider.id).catch(() => {})
            reject(new Error(event.data.error_description || event.data.error))
            return
          }

          try {
            // Exchange code for tokens
            await this.handleOAuthCallback({
              provider_id: provider.id,
              code: event.data.code,
              state: event.data.state,
            })

            // Fetch updated provider with connected status
            const providers = await this.listProviders()
            const updatedProvider = providers.find((p) => p.id === provider.id)
            resolve(updatedProvider || { ...provider, status: 'connected' as ProviderStatus, is_active: true, has_valid_token: true })
          } catch (error) {
            cleanup()
            // Clean up the created provider on token exchange failure
            await this.deleteProvider(provider.id).catch(() => {})
            reject(error)
          }
        }
      }

      window.addEventListener('message', handleMessage)

      // Check if popup was closed without completing OAuth.
      // Wrapped in try-catch: COOP policy from external OAuth providers (e.g. Google)
      // can make popup.closed throw when the browsing context groups diverge.
      checkClosedInterval = setInterval(() => {
        try {
          if (popup.closed && !isCompleted) {
            cleanup()
            this.deleteProvider(provider.id).catch(() => {})
            reject(new Error('OAuth flow was cancelled'))
          }
        } catch {
          // COOP prevented reading popup.closed — ignore, the message listener will handle completion
        }
      }, 500)
    })
  }
}

export const integrationsService = new IntegrationsServiceClass()
