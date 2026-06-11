/**
 * Voice Session Service
 *
 * Handles voice session management with LiveKit integration.
 * Provides methods to create, manage, and end voice sessions.
 */

import { apiClient } from '../client'

/**
 * Voice session creation request
 */
export interface VoiceSessionCreate {
  agent_id: string
  conversation_id?: string
  canvas_version_id?: string
  metadata?: Record<string, unknown>
}

/**
 * Voice session response with LiveKit connection info
 */
export interface VoiceSessionResponse {
  id: string
  organization_id: string | null
  agent_id: string
  agent_name: string
  conversation_id: string
  canvas_version_id?: string | null
  room_name: string | null
  status: 'active' | 'disconnected' | 'ended' | 'errored'
  started_at: string
  ended_at: string | null
  duration_seconds: number | null
  access_token?: string
  connection_url?: string
  metadata?: Record<string, unknown>
}

/**
 * Voice session status
 */
export interface VoiceSessionStatus {
  id: string
  room_name: string | null
  status: 'active' | 'disconnected' | 'ended' | 'errored'
  num_participants: number
  participants: Array<{
    identity: string | null
    name: string | null
    joined_at: string | null
  }>
  started_at: string
  duration_seconds: number | null
}

/**
 * End session request
 */
export interface VoiceSessionEnd {
  reason?: string
}

export interface VoiceHealthStatus {
  voice_available: boolean
  livekit_reachable: boolean
  mock: boolean
}

/**
 * Per-turn trace from `GET /conversations/{id}/traces`. Backed by
 * `ConversationTraceResponse` on the server. Used by the canvas Reasoning
 * Timeline (Sierra-style "what did the agent do?" surface) and conversation
 * postmortems.
 *
 * Older fields (step_before/after, emitted_messages) and reasoning fields
 * (chosen_action, guard_results, tool_calls, latency_breakdown_ms) are all
 * exposed. Reasoning fields default to safe empties on older trace records.
 */
export interface ConversationTrace {
  trace_id: string
  conversation_id: string
  turn_id: string
  step_before: string
  step_after: string
  event_type?: string
  emitted_messages: Array<{
    role: 'assistant' | 'system'
    text: string
  }>
  chosen_action?: {
    type: string
    reason: string
    payload?: Record<string, unknown>
  } | null
  guard_results?: Array<{
    guard_kind: string
    guard_value: string
    passed: boolean
    reason?: string | null
  }>
  tool_calls?: Array<{
    invocation_id?: string | null
    tool_ref: string
    status: string
    reason?: string | null
  }>
  latency_breakdown_ms?: Record<string, number>
  recorded_at: string
}

/** @deprecated kept for back-compat with voice-session callers — alias of ConversationTrace. */
export type VoiceTurnTrace = ConversationTrace

export interface RealtimeConversationEvent {
  event_id: string
  conversation_id: string
  realtime_session_id: string | null
  family: string
  name: string
  conversation_sequence: number
  actor_type: string | null
  actor_id: string | null
  payload: Record<string, unknown>
  created_at: string
}

class VoiceSessionService {
  /**
   * Check if the voice system is ready to accept calls.
   */
  async checkHealth(): Promise<VoiceHealthStatus> {
    try {
      return await apiClient.get<VoiceHealthStatus>('/voice-sessions/health')
    } catch (error) {
      if (
        error instanceof Error &&
        (error.message === 'authentication required' || error.message.includes('401'))
      ) {
        return {
          voice_available: false,
          livekit_reachable: false,
          mock: false,
        }
      }
      throw error
    }
  }

  /**
   * Create a new voice session
   *
   * @param request - Session creation parameters
   * @returns Voice session with LiveKit connection details
   */
  async createSession(request: VoiceSessionCreate): Promise<VoiceSessionResponse> {
    return apiClient.post<VoiceSessionResponse>('/voice-sessions', request)
  }

  /**
   * Get voice session status
   *
   * @param sessionId - Session ID
   * @returns Current session status
   */
  async getSessionStatus(sessionId: string): Promise<VoiceSessionStatus> {
    return apiClient.get<VoiceSessionStatus>(`/voice-sessions/${sessionId}`)
  }

  /**
   * Fetch conversation turn traces for a voice session's conversation.
   */
  async getConversationTraces(conversationId: string): Promise<VoiceTurnTrace[]> {
    return apiClient.get<VoiceTurnTrace[]>(`/conversations/${conversationId}/traces`)
  }

  /**
   * Fetch realtime events for a voice session's conversation.
   */
  async getConversationRealtimeEvents(conversationId: string): Promise<RealtimeConversationEvent[]> {
    return apiClient.get<RealtimeConversationEvent[]>(`/conversations/${conversationId}/realtime-events`)
  }

  /**
   * End a voice session
   *
   * @param sessionId - Session ID to end
   * @param request - Optional end reason
   */
  async endSession(sessionId: string, request?: VoiceSessionEnd): Promise<void> {
    return apiClient.delete(`/voice-sessions/${sessionId}`, request)
  }

  /**
   * List voice sessions
   *
   * @param params - Filter parameters
   * @returns List of voice sessions
   */
  async listSessions(params?: {
    status_filter?: 'active' | 'ended' | 'all'
    limit?: number
    offset?: number
  }): Promise<VoiceSessionResponse[]> {
    return apiClient.get<VoiceSessionResponse[]>('/voice-sessions', { params })
  }

  /**
   * Get active session count
   *
   * @returns Count of active sessions
   */
  async getActiveCount(): Promise<{ active_sessions: number }> {
    return apiClient.get<{ active_sessions: number }>('/voice-sessions/active/count')
  }

}

export const voiceSessionService = new VoiceSessionService()
