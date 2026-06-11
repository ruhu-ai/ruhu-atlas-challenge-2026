/**
 * Voice Session Hooks
 *
 * TanStack Query hooks for managing voice sessions with LiveKit.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { voiceSessionService } from '@/api/services/voice-session.service'
import type {
  VoiceSessionCreate,
  VoiceSessionResponse,
  VoiceSessionStatus,
  VoiceSessionEnd,
} from '@/api/services/voice-session.service'

/**
 * Query keys for cache management
 */
export const voiceSessionKeys = {
  all: ['voice-sessions'] as const,
  lists: () => [...voiceSessionKeys.all, 'list'] as const,
  list: (filters: Record<string, unknown>) =>
    [...voiceSessionKeys.lists(), filters] as const,
  details: () => [...voiceSessionKeys.all, 'detail'] as const,
  detail: (id: string) => [...voiceSessionKeys.details(), id] as const,
  status: (id: string) => [...voiceSessionKeys.all, 'status', id] as const,
  activeCount: () => [...voiceSessionKeys.all, 'active-count'] as const,
}

/**
 * Check if voice system is ready to accept calls.
 * Use this to disable the call button when no workers are available.
 */
export function useVoiceHealth(options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: [...voiceSessionKeys.all, 'health'] as const,
    queryFn: () => voiceSessionService.checkHealth(),
    enabled: options?.enabled !== false,
    refetchInterval: 30000, // Re-check every 30 seconds
    staleTime: 10000,
  })
}

/**
 * Create a new voice session
 */
export function useCreateVoiceSession() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (request: VoiceSessionCreate) =>
      voiceSessionService.createSession(request),
    // Don't retry on 503 (no worker) — it won't help
    retry: (failureCount, error: any) => {
      const status = error?.status || error?.response?.status
      if (status === 503 || status === 429) return false
      return failureCount < 1
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: voiceSessionKeys.lists() })
      queryClient.invalidateQueries({ queryKey: voiceSessionKeys.activeCount() })
    },
  })
}

/**
 * Get voice session status
 */
export function useVoiceSessionStatus(sessionId: string | undefined, options?: {
  refetchInterval?: number
  enabled?: boolean
}) {
  return useQuery({
    queryKey: voiceSessionKeys.status(sessionId!),
    queryFn: () => voiceSessionService.getSessionStatus(sessionId!),
    enabled: !!sessionId && (options?.enabled !== false),
    refetchInterval: options?.refetchInterval,
  })
}

/**
 * End a voice session
 */
export function useEndVoiceSession() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ sessionId, request }: { sessionId: string; request?: VoiceSessionEnd }) =>
      voiceSessionService.endSession(sessionId, request),
    onSuccess: (_, variables) => {
      // Invalidate affected queries
      queryClient.invalidateQueries({ queryKey: voiceSessionKeys.status(variables.sessionId) })
      queryClient.invalidateQueries({ queryKey: voiceSessionKeys.lists() })
      queryClient.invalidateQueries({ queryKey: voiceSessionKeys.activeCount() })
    },
  })
}

/**
 * List voice sessions
 */
export function useVoiceSessions(params?: {
  status_filter?: 'active' | 'ended' | 'all'
  limit?: number
  offset?: number
}) {
  return useQuery({
    queryKey: voiceSessionKeys.list(params || {}),
    queryFn: () => voiceSessionService.listSessions(params),
  })
}

/**
 * Get active session count
 */
export function useActiveSessionCount() {
  return useQuery({
    queryKey: voiceSessionKeys.activeCount(),
    queryFn: () => voiceSessionService.getActiveCount(),
    refetchInterval: 30000, // Refresh every 30 seconds
  })
}
