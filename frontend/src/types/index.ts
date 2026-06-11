/**
 * Core type definitions for the Ruhu AI Voice Agent Platform
 */

export * from './intent-tags'
export * from './journeys'

// ============================================================================
// User & Auth Types
// ============================================================================

// Mirrors the backend's AuthenticatedOrganizationSummary (embedded in every User).
export interface UserOrganization {
  organization_id: string
  slug: string
  name: string
  domain: string | null
  icon_url: string | null
  role: string
  is_account_owner: boolean
}

// Mirrors the backend's AuthenticatedUserSummary + embedded organization from MeResponse.
// Field names intentionally match the backend — no aliasing.
export interface User {
  user_id: string
  email: string
  display_name: string | null
  avatar_url: string | null
  timezone: string
  language: string
  preferences: Record<string, unknown>
  is_superuser: boolean
  organization: UserOrganization
}

// Mirrors OrganizationMemberResponse from the backend.
export interface OrganizationMember {
  user_id: string
  email: string
  display_name: string | null
  avatar_url: string | null
  timezone: string
  language: string
  is_active: boolean
  deleted_at: string | null
  role: string
  is_account_owner: boolean
  joined_at: string
}

// OrganizationRole — the three valid roles in the backend
export type OrganizationRole = 'admin' | 'developer' | 'analyst'

// Mirrors SessionResponse from the backend (GET /auth/sessions)
export interface AuthSession {
  session_id: string
  user_id: string
  issued_at: string
  expires_at: string
  last_seen_at: string | null
  created_ip: string | null
  last_seen_ip: string | null
  user_agent: string | null
  revoked_at: string | null
  is_current: boolean
}

// Mirrors EnterpriseSSOConfigResponse from the backend
export interface EnterpriseSSOConfig {
  sso_configuration_id: string
  organization_id: string
  issuer_url: string
  client_id: string
  client_secret_ref: string
  allowed_domains: string[]
  scopes: string[]
  is_active: boolean
  enforce_sso: boolean
  jit_provisioning_enabled: boolean
}

// Mirrors EnterpriseSSOConfigUpsertRequest from the backend
export interface EnterpriseSSOConfigUpsert {
  issuer_url: string
  client_id: string
  client_secret_ref: string
  allowed_domains: string[]
  scopes: string[]
  is_active: boolean
  enforce_sso: boolean
  jit_provisioning_enabled: boolean
}

export interface AuthState {
  user: User | null
  isAuthenticated: boolean
}

// Mirrors ApiKeyPublicResponse from the backend (GET /api-keys)
export interface APIKeyPublic {
  key_id: string
  name: string
  key_prefix: string
  is_active: boolean
  created_at: string
  last_used_at: string | null
}

// Client-side issuance flow: backend returns metadata only; the frontend
// generates the plaintext key locally and carries it through this shape.
export interface APIKeyCreated extends APIKeyPublic {
  key: string
}

// Mirrors ClosureStatusResponse from the backend
export interface ClosureStatus {
  organization_id: string
  deletion_state: 'active' | 'scheduled' | 'deleting' | 'deleted' | 'cancelled'
  deletion_scheduled_for: string | null
  message: string
  status: string | null
}

// ============================================================================
// Agent Types
// ============================================================================

/**
 * Agent type:
 * - chat: Text-only agents for web chat, WhatsApp, SMS
 * - voice: Voice-only agents for phone calls
 * - multimodal: Unified agents handling both voice AND chat
 */
export type AgentType = 'chat' | 'voice' | 'multimodal'

export interface Agent {
  id: string
  name: string
  description: string
  status: 'draft' | 'published' | 'archived' | 'active' | 'inactive' | 'training' | 'deployed'
  agent_type: AgentType
  organization_id: string
  system_prompt: string
  model_config: ModelConfig
  voice_config: VoiceConfig
  llm_config?: Record<string, unknown>
  stt_config?: Record<string, unknown>
  tts_config?: Record<string, unknown>
  knowledge_base_ids?: string[]
  active_canvas_version_id?: string
  is_deployed?: boolean
  deployment_url?: string
  deployed_at?: string
  deployment_gate_enabled?: boolean
  min_pass_rate?: number
  min_simulation_runs?: number
  max_test_staleness_hours?: number
  is_widget_enabled?: boolean
  widget_mode?: 'chat' | 'voice' | 'multimodal'
  widget_config?: Record<string, unknown>
  total_conversations?: number
  total_messages?: number
  avg_response_time?: number
  success_rate?: number
  created_at: string
  updated_at: string
  created_by: string
}

export interface ModelConfig {
  llm_provider: string
  llm_model: string
  temperature: number
  max_tokens: number
  stt_provider: string
  stt_language: string
  tts_provider: string
  tts_voice_id: string
}

export interface VoiceConfig {
  voice_id: string
  speed: number
  pitch: number
  stability: number
  similarity_boost: number
}

// ============================================================================
// Conversation Types
// ============================================================================

export interface Conversation {
  id: string
  agent_id: string
  user_phone?: string
  start_time: string
  end_time?: string
  duration_ms?: number
  status: 'active' | 'completed' | 'failed'
  resolution_status: 'resolved' | 'escalated' | 'abandoned'
  sentiment: 'positive' | 'neutral' | 'negative'
  organization_id: string
}

export interface Message {
  id: string
  conversation_id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: string
  metadata?: Record<string, unknown>
  organization_id: string
}

// ============================================================================
// Canvas Types (React Flow)
// ============================================================================

export type NodeType =
  | 'start'
  | 'message'
  | 'ai'
  | 'condition'
  | 'code'
  | 'tool'
  | 'transfer'
  | 'closing'

export interface CustomNodeData {
  label: string
  type: NodeType
  config?: Record<string, unknown>
}

// ============================================================================
// Analytics Types
// ============================================================================

export interface AgentMetrics {
  agent_id: string
  agent_name: string
  total_calls: number
  active_calls: number
  avg_handle_time_ms: number
  resolution_rate: number
  escalation_rate: number
  satisfaction_score: number
  last_updated: string
}

export interface PlatformMetrics {
  total_agents: number
  active_calls: number
  total_calls_today: number
  avg_resolution_rate: number
  avg_handle_time_ms: number
  avg_satisfaction_score: number
}

export interface TimeSeriesDataPoint {
  timestamp: string
  value: number
}

// ============================================================================
// Organization Types
// ============================================================================

// Mirrors OrganizationProfileResponse from the backend (GET/PATCH /organization).
export interface Organization {
  organization_id: string
  slug: string
  name: string
  domain: string | null
  email: string | null
  phone: string | null
  icon_url: string | null
  description: string | null
  brand_color: string | null
  settings: Record<string, unknown>
  metadata: Record<string, unknown>
  role: string
  is_account_owner: boolean
}

// ============================================================================
// API Response Types
// ============================================================================

export interface ApiResponse<T> {
  data: T
  message?: string
  status: 'success' | 'error'
}

export interface PaginatedResponse<T> {
  data: T[]
  total: number
  page: number
  per_page: number
  total_pages: number
}

// ============================================================================
// Provider Types (Abstraction Layer)
// ============================================================================

export interface TranscriptionResult {
  text: string
  confidence: number
  language: string
  timestamps?: { word: string; start: number; end: number }[]
}

export interface LLMResponse {
  text: string
  model: string
  usage: {
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
  }
}

export interface TTSResult {
  audio_url: string
  duration_ms: number
  format: string
}

// ============================================================================
// Canvas Types - Export from canvas.ts
// ============================================================================

export * from './canvas'

// ============================================================================
// Billing Types - Export from billing.ts
// ============================================================================

export * from './billing'

// ============================================================================
// Re-export agent types
// ============================================================================

export type { AgentType as AgentModality } from './agent'
