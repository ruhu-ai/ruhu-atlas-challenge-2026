/**
 * useAtlasSession — Atlas conversation/session lifecycle for the panel:
 * enabled-status toggle, session history (list/select/archive/new), the
 * display-message list, and turn execution (POST /atlas/turns with a
 * parallel SSE subscription for live token/tool streaming).
 *
 * Extracted from AtlasAIPanel.tsx (RP-4.4). The returned `runTurn` is the
 * single seam every turn-triggering action goes through; `turnPostRef`
 * guards against duplicate POSTs before React state catches up.
 */

import { useState, useRef, useEffect, useCallback } from 'react'

import {
  atlasService,
  type AtlasSessionResponse,
  type AtlasAttachmentInput,
  type AtlasAPIDiscoveryRequest,
  type AtlasPermissionDecision,
  type AtlasReviewDecision,
} from '@/api/services/atlas.service'

import {
  type DisplayMessage,
  newDisplayMessageId,
  greetingMessage,
  isValidAgentId,
  backendMessageToDisplay,
  errorMessage,
  attachTurnState,
} from '../components/atlas-panel-helpers'

export interface RunTurnArgs {
  message?: string
  question_answers?: Record<string, string>
  permission_decisions?: AtlasPermissionDecision[]
  review_decisions?: AtlasReviewDecision[]
  apply_request?: { delta_ids: string[] }
  attachments?: AtlasAttachmentInput[]
  api_discovery_requests?: AtlasAPIDiscoveryRequest[]
}

export interface StreamingTool {
  name: string
  status: 'running' | 'done' | 'error'
}

export function useAtlasSession(args: {
  isOpen: boolean
  agentId?: string
  /** Clears the composer (input/files/pasted chunks) when a new session starts. */
  onResetComposer: () => void
}) {
  const { isOpen, agentId, onResetComposer } = args

  // Enabled status
  const [atlasEnabled, setAtlasEnabled] = useState(true)
  const [isTogglingEnabled, setIsTogglingEnabled] = useState(false)

  // History
  const [sessions, setSessions] = useState<AtlasSessionResponse[]>([])
  const [isLoadingHistory, setIsLoadingHistory] = useState(false)
  const [showHistory, setShowHistory] = useState(false)

  // Current session
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null)
  const [currentSession, setCurrentSession] = useState<AtlasSessionResponse | null>(null)

  // Messages
  const [messages, setMessages] = useState<DisplayMessage[]>([greetingMessage()])

  // Turn-in-flight state
  const [isRunningTurn, setIsRunningTurn] = useState(false)
  const [streamingText, setStreamingText] = useState('')
  const [streamingTools, setStreamingTools] = useState<StreamingTool[]>([])
  const streamAbortRef = useRef<AbortController | null>(null)
  const turnPostRef = useRef(false)

  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const lastEventSequenceRef = useRef<number | null>(null)

  // ===== Effects =====

  // On open or agent change: fetch enabled status + sessions
  useEffect(() => {
    if (!isOpen || !isValidAgentId(agentId)) return
    let cancelled = false

    atlasService
      .getEnabledStatus(agentId!)
      .then((res) => {
        if (!cancelled) setAtlasEnabled(res.atlas_enabled)
      })
      .catch((err) => console.error('Atlas: getEnabledStatus failed', err))

    setIsLoadingHistory(true)
    atlasService
      .listSessions({ agentId, limit: 20 })
      .then((res) => {
        if (!cancelled) setSessions(res.sessions)
      })
      .catch((err) => console.error('Atlas: listSessions failed', err))
      .finally(() => {
        if (!cancelled) setIsLoadingHistory(false)
      })

    return () => {
      cancelled = true
    }
  }, [isOpen, agentId])

  // Auto-scroll
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingText])

  // Abort any in-flight stream on close/unmount
  useEffect(() => {
    if (!isOpen) {
      streamAbortRef.current?.abort()
    }
    return () => streamAbortRef.current?.abort()
  }, [isOpen])

  // ===== Helpers =====

  const refreshHistory = useCallback(async () => {
    if (!isValidAgentId(agentId)) return
    setIsLoadingHistory(true)
    try {
      const res = await atlasService.listSessions({ agentId, limit: 20 })
      setSessions(res.sessions)
    } catch (err) {
      console.error('Atlas: listSessions failed', err)
    } finally {
      setIsLoadingHistory(false)
    }
  }, [agentId])

  const ensureSession = useCallback(async (): Promise<string | null> => {
    if (currentSessionId) return currentSessionId
    if (!isValidAgentId(agentId)) return null
    try {
      const session = await atlasService.startSession({
        scope: 'agent_authoring',
        agent_id: agentId!,
      })
      setCurrentSessionId(session.session_id)
      setCurrentSession(session)
      lastEventSequenceRef.current = null
      return session.session_id
    } catch (err) {
      console.error('Atlas: startSession failed', err)
      setMessages((prev) => [...prev, errorMessage(err)])
      return null
    }
  }, [agentId, currentSessionId])

  // Drive a single turn: ensure session, subscribe to SSE in parallel, POST /turns.
  const runTurn = useCallback(
    async (turnArgs: RunTurnArgs) => {
      if (turnPostRef.current) return false
      turnPostRef.current = true
      const sessionId = await ensureSession()
      if (!sessionId) {
        turnPostRef.current = false
        return false
      }

      // Cancel any existing stream first
      streamAbortRef.current?.abort()
      const abortCtrl = new AbortController()
      streamAbortRef.current = abortCtrl

      setIsRunningTurn(true)
      setStreamingText('')
      setStreamingTools([])

      // Fire-and-forget SSE subscription. We don't await it during the turn —
      // the POST /turns response carries the canonical result; the SSE stream
      // is just for live preview during generation.
      const ssePromise = atlasService
        .subscribeToEvents(
          sessionId,
          {
            onEvent: (event) => {
              if (Number.isFinite(event.sequence_number)) {
                lastEventSequenceRef.current = Math.max(
                  lastEventSequenceRef.current ?? 0,
                  event.sequence_number,
                )
              }
            },
            onToken: (text) => setStreamingText((prev) => prev + text),
            onToolStart: (name) =>
              setStreamingTools((prev) => [...prev, { name, status: 'running' }]),
            onToolDone: (name, ok) =>
              setStreamingTools((prev) =>
                prev.map((tool) =>
                  tool.name === name ? { ...tool, status: ok ? 'done' : 'error' } : tool,
                ),
              ),
            onError: (err) => console.warn('Atlas SSE error:', err),
          },
          {
            afterSequence: lastEventSequenceRef.current ?? undefined,
            signal: abortCtrl.signal,
          },
        )
        .catch(() => {
          /* SSE errors are non-fatal — the turn response is authoritative */
        })

      try {
        const response = await atlasService.runTurn({
          session_id: sessionId,
          message: turnArgs.message,
          question_answers: turnArgs.question_answers,
          permission_decisions: turnArgs.permission_decisions,
          review_decisions: turnArgs.review_decisions,
          apply_request: turnArgs.apply_request,
          attachments: turnArgs.attachments,
          api_discovery_requests: turnArgs.api_discovery_requests,
        })

        setMessages((prev) => [
          ...prev,
          {
            id: newDisplayMessageId('msg'),
            role: 'assistant',
            content: response.message,
            timestamp: new Date(),
            turnResponse: response,
          },
        ])
      } catch (err) {
        console.error('Atlas: runTurn failed', err)
        setMessages((prev) => [...prev, errorMessage(err)])
      } finally {
        abortCtrl.abort() // close SSE
        await ssePromise
        setIsRunningTurn(false)
        turnPostRef.current = false
        setStreamingText('')
        setStreamingTools([])
      }
      return true
    },
    [ensureSession],
  )

  // ===== Handlers =====

  const handleToggleEnabled = async () => {
    if (!isValidAgentId(agentId) || isTogglingEnabled) return
    setIsTogglingEnabled(true)
    try {
      const res = await atlasService.setEnabledStatus(agentId!, !atlasEnabled)
      setAtlasEnabled(res.atlas_enabled)
    } catch (err) {
      console.error('Atlas: setEnabledStatus failed', err)
    } finally {
      setIsTogglingEnabled(false)
    }
  }

  const handleShowHistory = async () => {
    setShowHistory(true)
    await refreshHistory()
  }

  const handleSelectSession = async (sessionId: string) => {
    try {
      const [session, res, state, events] = await Promise.all([
        atlasService.getSession(sessionId),
        atlasService.listMessages(sessionId, { limit: 50 }),
        atlasService.getSessionState(sessionId),
        atlasService.listEvents(sessionId, { limit: 500 }),
      ])
      const restored = res.messages.map(backendMessageToDisplay)
      setMessages(attachTurnState(restored.length > 0 ? restored : [greetingMessage()], state))
      setCurrentSessionId(sessionId)
      setCurrentSession(session)
      lastEventSequenceRef.current = Math.max(
        events.total_count,
        ...events.events.map((event) => event.sequence_number),
        0,
      )
      setShowHistory(false)
    } catch (err) {
      console.error('Atlas: listMessages failed', err)
    }
  }

  const handleNewSession = () => {
    setCurrentSessionId(null)
    setCurrentSession(null)
    lastEventSequenceRef.current = null
    setMessages([greetingMessage()])
    onResetComposer()
    setShowHistory(false)
  }

  const handleArchiveSession = async (sessionId: string) => {
    try {
      await atlasService.archiveSession(sessionId)
      if (currentSessionId === sessionId) {
        handleNewSession()
      }
      await refreshHistory()
    } catch (err) {
      console.error('Atlas: archiveSession failed', err)
    }
  }

  return {
    atlasEnabled,
    isTogglingEnabled,
    handleToggleEnabled,
    sessions,
    isLoadingHistory,
    showHistory,
    setShowHistory,
    handleShowHistory,
    handleSelectSession,
    handleNewSession,
    handleArchiveSession,
    refreshHistory,
    currentSessionId,
    setCurrentSessionId,
    currentSession,
    setCurrentSession,
    messages,
    setMessages,
    isRunningTurn,
    streamingText,
    streamingTools,
    turnPostRef,
    lastEventSequenceRef,
    messagesEndRef,
    runTurn,
  }
}
