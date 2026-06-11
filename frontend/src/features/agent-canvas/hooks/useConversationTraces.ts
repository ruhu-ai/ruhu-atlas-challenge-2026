/**
 * useConversationTraces — polled fetch of `GET /conversations/{id}/traces`.
 *
 * Used by the canvas Test surface and (in a follow-up) the calls postmortem
 * page. Centralises the polling cadence + abort-on-unmount semantics so
 * adjacent surfaces don't each invent their own polling logic.
 *
 * Cadence:
 *   - Initial fetch on mount + whenever conversationId changes.
 *   - Polls every `intervalMs` (default 1500ms — same cadence as
 *     InteractionDebugPanel) while the hook is active and conversationId
 *     is non-null.
 *   - In-flight fetches are aborted on unmount or conversation change so
 *     a stale response can't overwrite the current conversation's data.
 *
 * Returns `{ traces, isLoading, error }`. Empty array (not null) when no
 * conversation is active — callers render the timeline's own empty state.
 */
import { useEffect, useRef, useState } from 'react'

import { apiClient } from '@/api/client'
import type { ConversationTrace } from '@/api/services/voice-session.service'

const DEFAULT_POLL_INTERVAL_MS = 1_500
const REQUEST_TIMEOUT_MS = 8_000

export interface UseConversationTracesResult {
  traces: ConversationTrace[]
  isLoading: boolean
  error: string | null
}

export function useConversationTraces(
  conversationId: string | null,
  options: {
    intervalMs?: number
    enabled?: boolean
    /** When true, fetch once on mount/conversation-change and stop. Used by
     * postmortem surfaces (e.g. Tickets detail dialog) where the
     * conversation is historical and won't change. */
    singleFetch?: boolean
  } = {},
): UseConversationTracesResult {
  const { intervalMs = DEFAULT_POLL_INTERVAL_MS, enabled = true, singleFetch = false } = options
  const [traces, setTraces] = useState<ConversationTrace[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Track the latest conversationId for the polling loop without retriggering
  // the effect on every closure capture — the effect re-runs only when the
  // conversationId itself changes.
  const cancelledRef = useRef(false)

  useEffect(() => {
    if (!enabled || !conversationId) {
      setTraces([])
      setError(null)
      setIsLoading(false)
      return
    }

    cancelledRef.current = false
    let timeoutId: number | null = null
    let inFlight: AbortController | null = null

    const tick = async () => {
      if (cancelledRef.current || inFlight) return
      const controller = new AbortController()
      inFlight = controller
      const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
      setIsLoading(true)
      try {
        const data = await apiClient.get<ConversationTrace[]>(
          `/conversations/${encodeURIComponent(conversationId)}/traces`,
          { signal: controller.signal },
        )
        if (!cancelledRef.current) {
          setTraces(Array.isArray(data) ? data : [])
          setError(null)
        }
      } catch (err) {
        if (cancelledRef.current) return
        if (err instanceof Error && err.name === 'AbortError') return
        setError(err instanceof Error ? err.message : 'failed to load traces')
      } finally {
        window.clearTimeout(timeout)
        if (inFlight === controller) inFlight = null
        if (!cancelledRef.current) {
          setIsLoading(false)
          // Single-fetch mode: do NOT schedule a follow-up tick. Used by
          // postmortem surfaces.
          if (!singleFetch) {
            timeoutId = window.setTimeout(() => {
              void tick()
            }, intervalMs)
          }
        }
      }
    }

    void tick()

    return () => {
      cancelledRef.current = true
      if (timeoutId !== null) window.clearTimeout(timeoutId)
      inFlight?.abort()
    }
  }, [conversationId, enabled, intervalMs, singleFetch])

  return { traces, isLoading, error }
}
