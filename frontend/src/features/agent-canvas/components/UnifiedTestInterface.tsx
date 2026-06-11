/**
 * Unified Test Interface
 *
 * Mirrors the real customer-facing widget experience:
 * - Single continuous conversation feed (chat + voice transcripts interleaved)
 * - Call button (phone/waveform icon) next to chat input to start voice call
 * - During active call: audio visualizer bars + controls replace input area
 * - Voice transcripts flow inline with chat messages (marked with mic icon)
 * - No mode toggle, no separate panels — one seamless experience
 *
 * Works for all agent types (chat, voice, multimodal).
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import { apiClient } from '@/api/client'
import { agentDefinitionService } from '@/api/services/agent-definition.service'
import type { AgentDefinition } from '@/types/agent-definition'
import { LiveKitRoom, RoomAudioRenderer } from '@livekit/components-react'
import '@livekit/components-styles'
import { DisconnectReason, type Room } from 'livekit-client'
import {
  Phone,
  PhoneOff,
  Mic,
  MicOff,
  Send,
  MessageSquare,
  AudioLines,
  Loader2,
  WifiOff,
  Paperclip,
  X,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useVoiceHealth } from '@/features/voice-session/hooks/useVoiceSessions'
import { getVoiceDisconnectDecision } from '@/features/agent-canvas/utils/voiceSessionLifecycle'
import { getVoiceDisconnectPolicy } from '@/features/agent-canvas/utils/voiceDisconnectPolicy'
import { InteractionDebugPanel } from '@/features/agent-canvas/components/InteractionDebugPanel'
import { toast } from 'sonner'
import { createLogger } from '@/utils/logger'
import type {
  ActiveVoiceSession,
  HandleEndCallOptions,
  PendingAttachment,
  PendingToolInvocation,
  PublicConversationEvent,
  TranscriptEntry,
  UnifiedTestInterfaceProps,
  VoiceRoomConnectionState,
  VoiceTranscriptEvent,
} from './unified-test/types'
import {
  buildWidgetConversationEventsStreamUrl,
  getErrorMessage,
  pendingInvocationSummary,
} from './unified-test/utils'
import { MessageBubble } from './unified-test/MessageBubble'
import {
  E2EVoiceRoomHarness,
  LiveKitRoomBridge,
  VoiceConnectionEvents,
  VoiceDataHandler,
} from './unified-test/voice-helpers'

const logger = createLogger({ prefix: '[UnifiedTest]' })
const AGENT_TRANSCRIPT_DUPLICATE_WINDOW_MS = 30_000
const PROJECTION_POLL_INTERVAL_MS = 2_000
const PROJECTION_POLL_TIMEOUT_MS = 8_000
const MESSAGE_STREAM_TIMEOUT_MS = 60_000

function normalizeTranscriptText(value: string): string {
  return String(value || '')
    .trim()
    .replace(/\s+/g, ' ')
    .toLowerCase()
}

// ==================== Main Component ====================

export function UnifiedTestInterface({
  agentId,
  agentName,
  agentType,
  agentStatus,
  canvasVersionId: _canvasVersionId,
  onConversationIdChange,
}: UnifiedTestInterfaceProps) {
  // Voice state
  const [voiceSession, setVoiceSession] = useState<ActiveVoiceSession | null>(null)
  const [isStartingCall, setIsStartingCall] = useState(false)

  // Chat state
  const [inputText, setInputText] = useState('')
  const [sharedConversationId, setSharedConversationId] = useState<string | null>(null)
  const [isUserTyping, setIsUserTyping] = useState(false)
  const [isAgentTyping, setIsAgentTyping] = useState(false)
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([])
  const [isUploadingAttachment, setIsUploadingAttachment] = useState(false)
  const [isPreparingChatSession, setIsPreparingChatSession] = useState(false)
  const [widgetSessionToken, setWidgetSessionToken] = useState<string | null>(null)
  const [widgetSessionConversationId, setWidgetSessionConversationId] = useState<string | null>(null)
  const [pendingToolInvocations, setPendingToolInvocations] = useState<PendingToolInvocation[]>([])
  const [pendingInvocationBusyId, setPendingInvocationBusyId] = useState<string | null>(null)

  // Unified transcript
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([])

  // Surface the active conversation id to hosts that render adjacent panels
  // (e.g. the canvas-level Test surface's ReasoningTimelinePane). Active id
  // is "widget session id if present, else shared id" — same precedence
  // used internally for InteractionDebugPanel at line 683.
  const activeConversationId = widgetSessionConversationId || sharedConversationId
  useEffect(() => {
    onConversationIdChange?.(activeConversationId)
  }, [activeConversationId, onConversationIdChange])

  // Refs
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const typingTimeoutRef = useRef<NodeJS.Timeout | null>(null)
  const handledDisconnectSessionIdRef = useRef<string | null>(null)
  const voiceSessionRef = useRef(voiceSession)
  const transcriptRef = useRef<TranscriptEntry[]>([])
  const liveKitRoomRef = useRef<Room | null>(null)
  const autoEnabledRoomRef = useRef<string | null>(null)
  const connectedVoiceSessionIdsRef = useRef(new Set<string>())
  const liveAgentTranscriptSegmentKeysRef = useRef(new Set<string>())
  const bootstrappedConversationIdsRef = useRef(new Set<string>())
  const lastConversationSequenceRef = useRef(0)
  // Guards against React StrictMode double-firing the session init effect
  const sessionInitiatedRef = useRef(false)
  // Stable counter for assistant message IDs — same position always maps to the
  // same ID so upsertTranscriptEntries deduplicates correctly on each render.
  const assistantMsgCountRef = useRef(0)
  const isSendingMessageRef = useRef(false)

  const [isRoomReady, setIsRoomReady] = useState(false)
  const [micEnabled, setMicEnabled] = useState(false)
  const [isMicLoading, setIsMicLoading] = useState(false)
  // Tracks whether the user explicitly muted so the auto-enable effect
  // doesn't undo a deliberate mute action.
  const userMutedRef = useRef(false)
  const [voiceRoomState, setVoiceRoomState] = useState<VoiceRoomConnectionState>('idle')
  const [voiceReconnectAttempt, setVoiceReconnectAttempt] = useState(0)

  // Hooks
  // Pause health polling while a call is active — it's irrelevant and produces log noise.
  // Use voiceSession directly here since isInCall hasn't been derived yet.
  const voiceHealth = useVoiceHealth({ enabled: !voiceSession })
  const isVoiceAvailable = Boolean(voiceHealth.data?.voice_available)

  // Keep ref in sync so callbacks don't need voiceSession as a dependency
  useEffect(() => { voiceSessionRef.current = voiceSession }, [voiceSession])
  useEffect(() => { transcriptRef.current = transcript }, [transcript])

  const handleRoomReady = useCallback((room: Room | null) => {
    liveKitRoomRef.current = room
    const ready = Boolean(room)
    setIsRoomReady(ready)
    if (!ready) {
      setMicEnabled(false)
      setIsMicLoading(false)
      userMutedRef.current = false
    } else if (window.__RUHU_E2E_MOCK_VOICE__?.enabled) {
      setVoiceRoomState('live')
    }
  }, [])

  // Delay LiveKit connection to survive React StrictMode's double-run of effects.
  // StrictMode runs: mount → cleanup → remount. Without the delay, LiveKit connects
  // on the first mount, disconnects on cleanup (firing onDisconnected), then tries
  // to reconnect with a stale state. The 100ms timeout is cancelled by the cleanup
  // on the first run, so LiveKit only connects on the stable second mount.
  const [liveKitConnect, setLiveKitConnect] = useState(false)
  const liveKitRoomKey = voiceSession ? `${voiceSession.roomName}:${voiceReconnectAttempt}` : null
  useEffect(() => {
    if (!liveKitRoomKey) {
      setLiveKitConnect(false)
      return
    }
    setLiveKitConnect(false)
    const t = setTimeout(() => setLiveKitConnect(true), 100)
    return () => clearTimeout(t)
  }, [liveKitRoomKey])

  const supportsVoice = agentType === 'voice' || agentType === 'multimodal'
  // Test mode should always support text chat + attachments, regardless of primary agent type.
  const supportsChat = true
  const isInCall = !!voiceSession

  // Fetch the agent definition for the currently selected runtime target so the
  // InteractionDebugPanel resolves pacing against the same version the
  // UnifiedTestInterface is exercising. Plain useEffect +
  // fetch rather than react-query so this component works in unit tests
  // that don't wire up a QueryClientProvider.  Soft-fail: if the definition isn't
  // reachable (permissions, not yet saved), the panel falls back to
  // channel-level pacing only.
  const [debugPanelAgentDefinition, setDebugPanelAgentDefinition] = useState<AgentDefinition | null>(null)
  useEffect(() => {
    if (!agentId || agentId === 'new') {
      setDebugPanelAgentDefinition(null)
      return
    }
    let cancelled = false
    agentDefinitionService
      .getAgentDefinition(agentId, 'draft')
      .then((response) => {
        if (!cancelled) setDebugPanelAgentDefinition(response?.definition ?? null)
      })
      .catch(() => {
        if (!cancelled) setDebugPanelAgentDefinition(null)
      })
    return () => {
      cancelled = true
    }
  }, [agentId])

  const upsertTranscriptEntries = useCallback((entries: TranscriptEntry[]) => {
    if (entries.length === 0) return
    setTranscript((prev) => {
      const byId = new Map(prev.map((item) => [item.id, item]))
      for (const entry of entries) {
        byId.set(entry.id, entry)
      }
      return Array.from(byId.values()).sort((a, b) => a.timestamp.getTime() - b.timestamp.getTime())
    })
  }, [])

  const startAgentTyping = useCallback(() => {
    setIsAgentTyping(true)
  }, [])

  const flushAgentTypingFrame = useCallback(async () => {
    await new Promise<void>((resolve) => {
      if (typeof window === 'undefined' || typeof window.requestAnimationFrame !== 'function') {
        resolve()
        return
      }
      window.requestAnimationFrame(() => resolve())
    })
  }, [])

  const stopAgentTyping = useCallback(() => {
    setIsAgentTyping(false)
  }, [])

  const handleStreamingComplete = useCallback((id: string) => {
    setTranscript((prev) =>
      prev.map((e) => (e.id === id ? { ...e, isStreaming: false } : e)),
    )
  }, [])

  // Clears the isPartial cursor on all agent entries — called when the agent
  // finishes speaking so the blinking cursor disappears.
  const clearAgentPartials = useCallback(() => {
    setTranscript((prev) =>
      prev.map((e) => (e.speaker === 'agent' ? { ...e, isPartial: false } : e)),
    )
  }, [])

  const clearLiveTranscriptRuntimeState = useCallback(() => {
    liveAgentTranscriptSegmentKeysRef.current.clear()
    stopAgentTyping()
  }, [stopAgentTyping])

  // Appends assistant messages returned by the endpoint. Send/confirm/cancel
  // responses return the current turn's messages, not full transcript history,
  // so do not slice by a global rendered count here.
  const appendAssistantMessages = useCallback((
    messages: Array<{ role?: string; text?: string }>,
    source: 'chat' | 'voice' = 'chat',
  ) => {
    const filtered = (messages || []).filter(
      (m) => (m.role || 'assistant') !== 'user' && (m.text || '').trim().length > 0,
    )
    const alreadySeen = assistantMsgCountRef.current
    const newMessages = filtered
    if (newMessages.length === 0) {
      stopAgentTyping()
      return
    }
    const entries = newMessages.map((m, i) => ({
      id: `agent_msg_${alreadySeen + i}`,
      text: m.text || '',
      speaker: 'agent' as const,
      timestamp: new Date(),
      source,
    }))
    assistantMsgCountRef.current = alreadySeen + newMessages.length
    upsertTranscriptEntries(entries)
    stopAgentTyping()
  }, [stopAgentTyping, upsertTranscriptEntries])

  const createWidgetChatSession = useCallback(async () => {
    const body = (await apiClient.post(`/agents/${encodeURIComponent(agentId)}/test-session`, {
      channel: 'web_widget',
      conversation_id: sharedConversationId || undefined,
      session_token: widgetSessionToken || undefined,
    })) as {
      conversation_id?: string
      session_token?: string
      messages?: Array<{ role?: string; text?: string }>
      pending_tool_invocations?: PendingToolInvocation[]
      detail?: string
    }
    return body
  }, [agentId, sharedConversationId, widgetSessionToken])

  const ensureWidgetChatSession = useCallback(async (): Promise<boolean> => {
    if (!supportsChat) return false
    if (widgetSessionConversationId && widgetSessionToken) return true
    if (isPreparingChatSession) return false
    setIsPreparingChatSession(true)
    try {
      const body = await createWidgetChatSession()
      if (!body.conversation_id || !body.session_token) {
        throw new Error('Widget chat session response is missing conversation/session token')
      }
      setWidgetSessionConversationId(body.conversation_id)
      setWidgetSessionToken(body.session_token)
      setSharedConversationId(body.conversation_id)
      setPendingToolInvocations(body.pending_tool_invocations || [])
      if (!bootstrappedConversationIdsRef.current.has(body.conversation_id)) {
        bootstrappedConversationIdsRef.current.add(body.conversation_id)
        appendAssistantMessages(body.messages || [], 'chat')
      }
      return true
    } catch (error) {
      toast.error(getErrorMessage(error) || 'Failed to initialize chat test session')
      return false
    } finally {
      setIsPreparingChatSession(false)
    }
  }, [
    createWidgetChatSession,
    appendAssistantMessages,
    isPreparingChatSession,
    supportsChat,
    widgetSessionConversationId,
    widgetSessionToken,
  ])

  const resetWidgetSessionState = useCallback(() => {
    setVoiceSession(null)
    setVoiceRoomState('idle')
    setVoiceReconnectAttempt(0)
    setMicEnabled(false)
    setPendingAttachments([])
    setPendingToolInvocations([])
    setPendingInvocationBusyId(null)
    setWidgetSessionConversationId(null)
    setWidgetSessionToken(null)
    setSharedConversationId(null)
    setTranscript([])
    setIsAgentTyping(false)
    clearLiveTranscriptRuntimeState()
    assistantMsgCountRef.current = 0
    bootstrappedConversationIdsRef.current.clear()
    lastConversationSequenceRef.current = 0
    sessionInitiatedRef.current = true
  }, [clearLiveTranscriptRuntimeState])

  const setRoomMicrophoneEnabled = useCallback(async (enabled: boolean, byUser = false) => {
    const room = liveKitRoomRef.current
    if (!room?.localParticipant) return false

    setIsMicLoading(true)
    try {
      if (enabled) {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        stream.getTracks().forEach(t => t.stop())
      }
      await room.localParticipant.setMicrophoneEnabled(enabled)
      setMicEnabled(enabled)
      // Track explicit user mute/unmute so the auto-enable effect doesn't
      // override a deliberate mute with its periodic re-enable logic.
      if (byUser) userMutedRef.current = !enabled
      return true
    } catch (err) {
      logger.error(`Failed to ${enabled ? 'enable' : 'disable'} microphone:`, err)
      return false
    } finally {
      setIsMicLoading(false)
    }
  }, [])

  const chatReady = isInCall || (Boolean(widgetSessionConversationId && widgetSessionToken) && !isPreparingChatSession)
  const canStartCall = supportsVoice && agentStatus === 'active' && !isInCall && !isStartingCall
  const voiceStatusMessage = !supportsVoice
    ? null
    : agentStatus !== 'active'
    ? 'Deploy agent to enable voice calls'
    : voiceHealth.isLoading
    ? 'Checking voice service health...'
    : voiceHealth.isError
    ? 'Voice service health check failed'
    : !isVoiceAvailable
    ? 'Voice service may be unavailable'
    : null

  // ---- Voice Transcript ----
  const handleVoiceTranscript = useCallback((entry: VoiceTranscriptEvent) => {
    const id = entry.id || `voice_${Date.now()}_${Math.random()}`
    const segmentKey = entry.segmentKey?.trim()
    const isFinal = Boolean(entry.isFinal)
    const isAgentTranscript = entry.speaker === 'agent'
    const isSegmentedTranscript = isAgentTranscript && segmentKey !== undefined

    if (isSegmentedTranscript) {
      if (isFinal) {
        liveAgentTranscriptSegmentKeysRef.current.delete(segmentKey)
      } else {
        liveAgentTranscriptSegmentKeysRef.current.add(segmentKey)
      }
    }

    // During voice calls, agent text streams in real-time via LiveKit
    // TranscriptionReceived events (synchronized with TTS audio playback).
    // SSE assistant_emitted is suppressed during calls to avoid duplicates.
    if (isAgentTranscript && isFinal) {
      stopAgentTyping()
    }

    if (isAgentTranscript && isFinal) {
      const normalizedIncoming = normalizeTranscriptText(entry.text)
      if (normalizedIncoming) {
        const nowMs = Date.now()
        const duplicateAgentEntry = transcriptRef.current.find((item) => {
          if (item.speaker !== 'agent' || item.id === id) return false
          if (normalizeTranscriptText(item.text) !== normalizedIncoming) return false
          return nowMs - item.timestamp.getTime() <= AGENT_TRANSCRIPT_DUPLICATE_WINDOW_MS
        })
        if (duplicateAgentEntry) {
          if (isSegmentedTranscript) {
            liveAgentTranscriptSegmentKeysRef.current.delete(segmentKey)
          }
          return
        }
      }
    }

    setTranscript(prev => {
      const existingIndex = prev.findIndex((item) => item.id === id)
      if (existingIndex === -1) {
        return [...prev, {
          id,
          text: entry.text,
          speaker: entry.speaker,
          timestamp: new Date(),
          source: 'voice',
          isPartial: !isFinal,
        }]
      }
      return prev.map((item) => (
        item.id === id
          ? { ...item, text: entry.text, timestamp: new Date(), isPartial: !isFinal }
          : item
      ))
    })
  }, [stopAgentTyping])

  useEffect(() => {
    lastConversationSequenceRef.current = 0
    assistantMsgCountRef.current = 0
  }, [widgetSessionConversationId])

  useEffect(() => {
    if (!widgetSessionConversationId || !widgetSessionToken) return
    let cancelled = false
    let timeoutId: number | null = null
    let inFlightController: AbortController | null = null
    let consecutiveFailures = 0

    const pollProjection = async () => {
      if (cancelled || inFlightController) return
      const controller = new AbortController()
      inFlightController = controller
      const timeout = window.setTimeout(() => controller.abort(), PROJECTION_POLL_TIMEOUT_MS)
      try {
        const response = await fetch(
          `/api/v1/public/widget/sessions/${encodeURIComponent(widgetSessionConversationId)}/projection`,
          {
            method: 'GET',
            credentials: 'include',
            signal: controller.signal,
            headers: {
              'X-Ruhu-Widget-Session-Token': widgetSessionToken,
            },
          },
        )
        if (!response.ok) {
          consecutiveFailures += 1
          if (consecutiveFailures === 1) {
            logger.warn('Unified test projection poll failed', {
              conversationId: widgetSessionConversationId,
              status: response.status,
            })
          }
          if (consecutiveFailures >= 3) {
            logger.warn('Unified test projection poll disabled after repeated failures', {
              conversationId: widgetSessionConversationId,
            })
            cancelled = true
          }
          return
        }
        const body = (await response.json().catch(() => ({}))) as {
          pending_tool_invocations?: PendingToolInvocation[]
        }
        if (cancelled) return
        consecutiveFailures = 0
        setPendingToolInvocations(Array.isArray(body.pending_tool_invocations) ? body.pending_tool_invocations : [])
      } catch {
        consecutiveFailures += 1
        if (consecutiveFailures === 1) {
          logger.warn('Unified test projection poll threw', {
            conversationId: widgetSessionConversationId,
          })
        }
        if (consecutiveFailures >= 3) {
          logger.warn('Unified test projection poll disabled after repeated exceptions', {
            conversationId: widgetSessionConversationId,
          })
          cancelled = true
        }
      } finally {
        window.clearTimeout(timeout)
        if (inFlightController === controller) {
          inFlightController = null
        }
        if (!cancelled) {
          timeoutId = window.setTimeout(() => {
            void pollProjection()
          }, PROJECTION_POLL_INTERVAL_MS)
        }
      }
    }

    void pollProjection()

    return () => {
      cancelled = true
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId)
      }
      inFlightController?.abort()
    }
  }, [widgetSessionConversationId, widgetSessionToken])

  useEffect(() => {
    if (!isInCall || !widgetSessionToken || !widgetSessionConversationId || typeof window === 'undefined') {
      return
    }

    // Position-based replay suppression: count how many agent messages are
    // already in the transcript; the SSE replay will redeliver them in
    // order, and we want to skip exactly that many irrespective of text.
    // Text-keyed suppression dropped legitimate same-text-twice messages.
    let replaySuppressionsRemaining = transcriptRef.current.filter(
      (entry) => entry.speaker === 'agent' && entry.text.trim().length > 0,
    ).length

    let cancelled = false
    const eventSource = new window.EventSource(
      buildWidgetConversationEventsStreamUrl(
        widgetSessionConversationId,
        widgetSessionToken,
        lastConversationSequenceRef.current,
      ),
      { withCredentials: false },
    )

    const handleEvent = (message: MessageEvent<string>) => {
      if (cancelled) return
      try {
        const event = JSON.parse(message.data) as PublicConversationEvent
        const sequence = Number(event.conversation_sequence || 0)

        if (sequence <= lastConversationSequenceRef.current) {
          return
        }
        lastConversationSequenceRef.current = sequence

        if (event.family === 'voice') {
          if (event.name === 'assistant_speaking_started') {
            // Agent has already started speaking audio — clear any pending typing dots.
            // The audio waveform is the visual indicator during voice calls, not the dots.
            stopAgentTyping()
            return
          }
          if (event.name === 'assistant_speaking_stopped' || event.name === 'assistant_interrupted') {
            // Agent finished speaking — remove the blinking cursor from all agent entries.
            stopAgentTyping()
            clearAgentPartials()
          }
          return
        }

        if (event.family !== 'message' || event.name !== 'assistant_emitted') {
          return
        }

        const text = String(event.payload?.text || '').trim()
        if (!text) return

        // FIFO position-based replay suppression: the next N agent SSE
        // events are duplicates of messages already in the transcript.
        // Count down without checking text — an agent legitimately
        // sending the same string twice should still surface both.
        if (replaySuppressionsRemaining > 0) {
          replaySuppressionsRemaining -= 1
          stopAgentTyping()
          return
        }

        // The sequence guard above (`sequence <= lastConversationSequenceRef.current`)
        // already protects against backend SSE redelivery of the same
        // event. We deliberately do NOT add a text-based "same string
        // within Ns" filter here — that was the bug we fixed.

        // During a voice call (or while one is being set up), agent text streams
        // in real-time via LiveKit TranscriptionReceived events (word-by-word,
        // synchronized with TTS audio). Suppress SSE text to avoid duplicates.
        // The isStartingCall check covers the window between widget session
        // creation (which triggers the greeting) and LiveKit room connection.
        if (isInCall || isStartingCall) {
          stopAgentTyping()
          return
        }

        upsertTranscriptEntries([
          {
            id: `evt_${sequence}`,
            text,
            speaker: 'agent',
            timestamp: new Date(event.created_at),
            source: 'chat',
            isStreaming: true,
          },
        ])
        stopAgentTyping()
      } catch (error) {
        logger.warn('Widget conversation event parse failed', {
          error,
          conversationId: widgetSessionConversationId,
        })
      }
    }

    eventSource.addEventListener('conversation.event', handleEvent as EventListener)

    return () => {
      cancelled = true
      eventSource.removeEventListener('conversation.event', handleEvent as EventListener)
      eventSource.close()
    }
  }, [
    isInCall,
    isStartingCall,
    clearAgentPartials,
    startAgentTyping,
    stopAgentTyping,
    upsertTranscriptEntries,
    widgetSessionConversationId,
    widgetSessionToken,
  ])

  // ---- Session init ----
  // Wait for testPublishableKey before initiating — the key is provisioned
  // async on mount and the widget session POST requires it.
  useEffect(() => {
    if (!supportsChat) return
    if (sessionInitiatedRef.current) return
    sessionInitiatedRef.current = true
    void ensureWidgetChatSession()
  }, [ensureWidgetChatSession, supportsChat])

  const handleStartCall = useCallback(async () => {
    if (isStartingCall) return
    if (!supportsVoice || agentStatus !== 'active' || isInCall) return

    setIsStartingCall(true)
    setVoiceRoomState('connecting')
    setVoiceReconnectAttempt(0)
    const createdAtMs = Date.now()

    try {
      const ready = await ensureWidgetChatSession()
      if (!ready) {
        throw new Error('Chat test session is not ready')
      }
      const conversationId = widgetSessionConversationId || sharedConversationId
      if (!conversationId || !widgetSessionToken) {
        throw new Error('Widget chat session is not ready')
      }
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      stream.getTracks().forEach(t => t.stop())
      const response = await fetch(`/api/v1/public/widget/sessions/${encodeURIComponent(conversationId)}/voice`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          'X-Ruhu-Widget-Session-Token': widgetSessionToken,
        },
        body: JSON.stringify({
          participant_identity: `reviewer-${agentId}`,
          participant_name: 'Reviewer',
        }),
      })
      const body = (await response.json().catch(() => ({}))) as {
        detail?: string
        realtime_session_id?: string
        transport?: {
          url?: string
          token?: string
          room_name?: string
        }
      }
      if (!response.ok) {
        throw new Error(body.detail || 'Failed to start voice call')
      }
      if (!body.realtime_session_id || !body.transport?.url || !body.transport?.token || !body.transport?.room_name) {
        throw new Error('Voice session response is incomplete')
      }
      handledDisconnectSessionIdRef.current = null
      connectedVoiceSessionIdsRef.current.delete(body.realtime_session_id)
      setVoiceSession({
        id: body.realtime_session_id,
        serverUrl: body.transport.url,
        token: body.transport.token,
        roomName: body.transport.room_name,
        conversationId,
        createdAtMs,
      })
    } catch (err: unknown) {
      logger.error('Failed to start call:', err)
      toast.error(getErrorMessage(err) || 'Failed to start voice call')
      voiceHealth.refetch()
    } finally {
      setIsStartingCall(false)
    }
  }, [
    agentId,
    agentStatus,
    ensureWidgetChatSession,
    isInCall,
    isStartingCall,
    sharedConversationId,
    supportsVoice,
    voiceHealth,
    widgetSessionConversationId,
    widgetSessionToken,
  ])

  const handleEndCall = useCallback(async ({
    endedSession,
    disconnectReason,
    initiatedByUser = false,
  }: HandleEndCallOptions = {}) => {
    const targetSession = endedSession ?? voiceSessionRef.current
    if (!targetSession) return

    const decision = initiatedByUser
      ? (
        handledDisconnectSessionIdRef.current === targetSession.id
          ? 'ignore_duplicate'
          : 'handle'
      )
      : getVoiceDisconnectDecision({
        activeSession: voiceSessionRef.current,
        disconnectedSession: targetSession,
        handledSessionId: handledDisconnectSessionIdRef.current,
        nowMs: Date.now(),
        sessionEverConnected: connectedVoiceSessionIdsRef.current.has(targetSession.id),
      })
    if (decision === 'ignore_early_disconnect') {
      logger.debug('Ignoring early disconnect (React Strict Mode)', { sessionId: targetSession.id })
      return
    }
    if (decision === 'ignore_duplicate') {
      return
    }
    if (decision === 'ignore_stale_disconnect') {
      logger.debug('Ignoring stale disconnect from previous voice room', {
        staleSessionId: targetSession.id,
        activeSessionId: voiceSessionRef.current?.id,
      })
      return
    }

    clearLiveTranscriptRuntimeState()

    const policy = getVoiceDisconnectPolicy(disconnectReason, initiatedByUser)
    if (policy.kind === 'transient' && policy.allowRetry) {
      logger.warn('Voice room disconnected with recoverable reason', {
        sessionId: targetSession.id,
        reason: disconnectReason,
      })
      liveKitRoomRef.current = null
      autoEnabledRoomRef.current = null
      setIsRoomReady(false)
      setMicEnabled(false)
      setIsMicLoading(false)
      setLiveKitConnect(false)
      setVoiceRoomState('recoverable_disconnect')
      if (policy.userMessage) {
        toast.error(policy.userMessage)
      }
      return
    }

    handledDisconnectSessionIdRef.current = targetSession.id
    connectedVoiceSessionIdsRef.current.delete(targetSession.id)
    try {
      await fetch(
        `/api/v1/public/widget/sessions/${encodeURIComponent(targetSession.conversationId)}/voice/disconnect`,
        {
          method: 'POST',
          credentials: 'include',
          headers: {
            'Content-Type': 'application/json',
            ...(widgetSessionToken ? { 'X-Ruhu-Widget-Session-Token': widgetSessionToken } : {}),
          },
          body: JSON.stringify({
            realtime_session_id: targetSession.id,
            reason: policy.apiReason || 'room_ended',
          }),
        },
      )
    } catch (error) {
      logger.error('Failed to end session:', error)
    }

    if (voiceSessionRef.current?.id === targetSession.id) {
      setVoiceSession(null)
      liveKitRoomRef.current = null
      autoEnabledRoomRef.current = null
      setIsRoomReady(false)
      setMicEnabled(false)
      setIsMicLoading(false)
      setVoiceRoomState('idle')
      if (policy.userMessage) {
        toast.error(policy.userMessage)
      }
    }
  }, [clearLiveTranscriptRuntimeState, widgetSessionToken])

  const disconnectLiveKitRoom = useCallback(async () => {
    const room = liveKitRoomRef.current
    if (!room) return

    try {
      const pubs = room.localParticipant?.trackPublications
      if (pubs) {
        for (const pub of pubs.values()) {
          if (pub.track) {
            await room.localParticipant.unpublishTrack(pub.track).catch(() => {})
          }
        }
      }
      await room.disconnect()
    } catch {
      // Suppress post-close errors
    }
  }, [])

  const handleVoiceBarEndCall = useCallback(async () => {
    const currentSession = voiceSessionRef.current
    await disconnectLiveKitRoom()
    await handleEndCall({ endedSession: currentSession, initiatedByUser: true })
  }, [disconnectLiveKitRoom, handleEndCall])

  const handleRetryVoiceConnection = useCallback(() => {
    if (!voiceSessionRef.current) return
    handledDisconnectSessionIdRef.current = null
    setVoiceRoomState('connecting')
    setVoiceReconnectAttempt(prev => prev + 1)
  }, [])

  const handleVoiceRoomConnected = useCallback(() => {
    if (voiceSessionRef.current?.id) {
      connectedVoiceSessionIdsRef.current.add(voiceSessionRef.current.id)
    }
    setVoiceRoomState('live')
  }, [])

  const handleVoiceRoomReconnecting = useCallback(() => {
    setVoiceRoomState('reconnecting')
  }, [])

  const handleVoiceRoomReconnected = useCallback(() => {
    setVoiceRoomState('live')
  }, [])

  const handleMicToggle = useCallback(async () => {
    await setRoomMicrophoneEnabled(!micEnabled, true)
  }, [micEnabled, setRoomMicrophoneEnabled])

  const handleLiveKitError = useCallback((error: Error) => {
    logger.error('LiveKit error:', error)
  }, [])

  const handleLiveKitDisconnect = useCallback((reason?: DisconnectReason) => {
    void handleEndCall({
      endedSession: voiceSessionRef.current,
      disconnectReason: reason,
    })
  }, [handleEndCall])

  // Cleanup on tab close
  useEffect(() => {
    const handleTabClose = () => {
      const session = voiceSessionRef.current
      if (session) {
        const url = `/api/v1/public/widget/sessions/${encodeURIComponent(session.conversationId)}/voice/disconnect`
        fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            realtime_session_id: session.id,
            reason: 'tab_closed',
          }),
          credentials: 'include',
          keepalive: true,
        }).catch(() => {})
      }
    }
    window.addEventListener('beforeunload', handleTabClose)
    return () => window.removeEventListener('beforeunload', handleTabClose)
  }, [])

  // ---- Chat Send ----
  const handleSendMessage = useCallback(async () => {
    const text = inputText.trim()
    const hasAttachments = pendingAttachments.length > 0
    if (!text && !hasAttachments) return
    if (isSendingMessageRef.current) {
      toast.error('A test message is still processing. Please wait for it to finish.')
      return
    }
    isSendingMessageRef.current = true
    let streamTimeoutId: number | null = null

    // Capture and clear input immediately for responsive feel
    setInputText('')
    setPendingAttachments([])
    setIsUserTyping(false)

    // Timestamp for the user message
    const userTimestamp = new Date()

    try {
      if (isInCall && pendingAttachments.length > 0) {
        toast.error('File attachments are disabled while a voice session is active')
        return
      }

      const outboundText = text || (
        pendingAttachments.length === 1
          ? `Please review the attached file "${pendingAttachments[0].filename}".`
          : `Please review the ${pendingAttachments.length} attached files.`
      )
      setTranscript(prev => [...prev, {
        // UUID — `user_${Date.now()}` collides if two messages are sent
        // within the same millisecond (rapid clicks, batched submits).
        id: (typeof crypto !== 'undefined' && crypto.randomUUID)
          ? `user_${crypto.randomUUID()}`
          : `user_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`,
        text: text || (
          pendingAttachments.length === 1
            ? `Shared attachment: ${pendingAttachments[0].filename}`
            : `Shared ${pendingAttachments.length} attachments`
        ),
        speaker: 'user',
        timestamp: userTimestamp,
        source: 'chat',
        attachments: pendingAttachments.length > 0 ? [...pendingAttachments] : undefined,
      }])
      startAgentTyping()
      await flushAgentTypingFrame()

      if (isInCall) {
        const room = liveKitRoomRef.current
        if (!room?.localParticipant || !isRoomReady) {
          throw new Error('Voice session is not ready for text input yet')
        }
        // For voice, we don't stop the typing indicator here — it's managed by
        // VoiceDataHandler's agent speaking events instead.
        await room.localParticipant.sendText(outboundText, { topic: 'lk.chat' })
        return
      }

      const ready = await ensureWidgetChatSession()
      if (!ready) return

      const conversationId = widgetSessionConversationId || sharedConversationId
      if (!conversationId || !widgetSessionToken) {
        throw new Error('Chat test session is not ready')
      }

      const streamController = new AbortController()
      streamTimeoutId = window.setTimeout(() => {
        streamController.abort()
      }, MESSAGE_STREAM_TIMEOUT_MS)

      const response = await fetch(
        `/api/v1/public/widget/sessions/${encodeURIComponent(conversationId)}/messages/stream`,
        {
          method: 'POST',
          credentials: 'include',
          signal: streamController.signal,
          headers: {
            'Content-Type': 'application/json',
            'X-Ruhu-Widget-Session-Token': widgetSessionToken,
          },
          body: JSON.stringify({
            text: outboundText,
            attachment_ids: pendingAttachments.map((attachment) => attachment.attachmentId),
            // Per-send idempotency key — the kernel dedupes by this so a
            // network retry of the same logical send is processed once.
            dedupe_key: (typeof crypto !== 'undefined' && crypto.randomUUID)
              ? crypto.randomUUID()
              : `unified-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`,
          }),
        },
      )
      if (!response.ok) {
        const errBody = (await response.json().catch(() => ({}))) as { detail?: string }
        let message = errBody.detail || 'Failed to send chat message'
        if (response.status === 429) {
          const retryAfter = response.headers.get('Retry-After')
          const retryAfterSeconds = retryAfter ? Number(retryAfter) : NaN
          if (Number.isFinite(retryAfterSeconds) && retryAfterSeconds > 0) {
            message = `Too many requests - please wait ${Math.ceil(retryAfterSeconds)}s before sending again.`
          }
        }
        throw new Error(message)
      }

      // Read the SSE stream line-by-line
      const reader = response.body?.getReader()
      if (!reader) throw new Error('No response stream available')
      const decoder = new TextDecoder()
      let buffer = ''
      const newMessages: Array<{ role?: string; text?: string }> = []
      let donePendingToolInvocations: PendingToolInvocation[] | null = null
      let currentEvent = ''

      outer: while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
          } else if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6)) as Record<string, unknown>
              if (currentEvent === 'typing') {
                // Backend controls typing state; we already set it true before the request,
                // but honour explicit false to clear it early on errors.
                if (data.is_typing === false) stopAgentTyping()
              } else if (currentEvent === 'message') {
                newMessages.push({
                  role: (data.role as string | undefined) ?? 'assistant',
                  text: (data.text as string | undefined) ?? '',
                })
              } else if (currentEvent === 'error') {
                throw new Error((data.detail as string | undefined) || 'Agent processing failed')
              } else if (currentEvent === 'done') {
                donePendingToolInvocations = Array.isArray(data.pending_tool_invocations)
                  ? (data.pending_tool_invocations as PendingToolInvocation[])
                  : []
                break outer
              }
            } catch (parseErr) {
              if (parseErr instanceof SyntaxError) continue
              throw parseErr
            }
          } else if (line === '') {
            currentEvent = ''
          }
        }
      }

      // appendAssistantMessages also calls stopAgentTyping internally
      appendAssistantMessages(newMessages, 'chat')
      if (donePendingToolInvocations !== null) {
        setPendingToolInvocations(donePendingToolInvocations)
      }
    } catch (error) {
      stopAgentTyping()
      if (error instanceof DOMException && error.name === 'AbortError') {
        toast.error('Agent response timed out. Please retry once the backend is responsive.')
      } else {
        toast.error(getErrorMessage(error) || 'Failed to send chat message')
      }
    } finally {
      isSendingMessageRef.current = false
      if (streamTimeoutId !== null) {
        window.clearTimeout(streamTimeoutId)
      }
    }
  }, [
    ensureWidgetChatSession,
    flushAgentTypingFrame,
    inputText,
    isRoomReady,
    isInCall,
    pendingAttachments,
    sharedConversationId,
    appendAssistantMessages,
    startAgentTyping,
    stopAgentTyping,
    widgetSessionConversationId,
    widgetSessionToken,
  ])

  const handleAttachmentFiles = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return
    if (isInCall) {
      toast.error('File attachments are disabled while a voice session is active')
      return
    }
    setIsUploadingAttachment(true)
    try {
      const ready = await ensureWidgetChatSession()
      if (!ready) return
      const conversationId = widgetSessionConversationId || sharedConversationId
      if (!conversationId || !widgetSessionToken) {
        throw new Error('Chat test session is not ready')
      }
      const uploaded = await Promise.all(
        Array.from(files).map(async (file) => {
          const response = await fetch(
            `/api/v1/public/widget/sessions/${encodeURIComponent(conversationId)}/attachments?filename=${encodeURIComponent(file.name)}`,
            {
              method: 'POST',
              credentials: 'include',
              headers: {
                'Content-Type': file.type || 'application/octet-stream',
                'X-Ruhu-Widget-Session-Token': widgetSessionToken,
              },
              body: file,
            },
          )
          const payload = (await response.json().catch(() => ({}))) as {
            detail?: string
            attachment?: {
              attachment_id?: string
              filename?: string | null
              content_type?: string | null
              size_bytes?: number | null
            }
          }
          if (!response.ok) {
            throw new Error(payload.detail || 'Attachment upload failed')
          }
          const attachment = payload.attachment
          if (!attachment?.attachment_id) {
            throw new Error('Attachment upload response missing attachment_id')
          }
          return {
            attachmentId: attachment.attachment_id,
            filename: attachment.filename || file.name,
            mimeType: attachment.content_type || file.type || 'application/octet-stream',
            sizeBytes: attachment.size_bytes || file.size,
          }
        })
      )
      setPendingAttachments(prev => [...prev, ...uploaded])
    } catch (err) {
      toast.error(getErrorMessage(err) || 'Failed to upload attachment')
    } finally {
      setIsUploadingAttachment(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }, [ensureWidgetChatSession, isInCall, sharedConversationId, widgetSessionConversationId, widgetSessionToken])

  const removePendingAttachment = useCallback((attachmentId: string) => {
    setPendingAttachments(prev => prev.filter(item => item.attachmentId !== attachmentId))
  }, [])

  const handleConfirmPendingInvocation = useCallback(async (invocationId: string) => {
    const conversationId = widgetSessionConversationId || sharedConversationId
    if (!conversationId || !widgetSessionToken || pendingInvocationBusyId) return
    setPendingInvocationBusyId(invocationId)
    try {
      const response = await fetch(
        `/api/v1/public/widget/sessions/${encodeURIComponent(conversationId)}/tool-invocations/${encodeURIComponent(invocationId)}/confirm`,
        {
          method: 'POST',
          credentials: 'include',
          headers: {
            'X-Ruhu-Widget-Session-Token': widgetSessionToken,
          },
        },
      )
      const body = (await response.json().catch(() => ({}))) as {
        detail?: string
        messages?: Array<{ role?: string; text?: string }>
        pending_tool_invocations?: PendingToolInvocation[]
      }
      if (!response.ok) {
        throw new Error(body.detail || 'Failed to confirm pending action')
      }
      appendAssistantMessages(body.messages || [], 'chat')
      setPendingToolInvocations(body.pending_tool_invocations || [])
    } catch (error) {
      toast.error(getErrorMessage(error) || 'Failed to confirm pending action')
    } finally {
      setPendingInvocationBusyId(null)
    }
  }, [
    appendAssistantMessages,
    pendingInvocationBusyId,
    sharedConversationId,
    widgetSessionConversationId,
    widgetSessionToken,
  ])

  const handleCancelPendingInvocation = useCallback(async (invocationId: string) => {
    const conversationId = widgetSessionConversationId || sharedConversationId
    if (!conversationId || !widgetSessionToken || pendingInvocationBusyId) return
    setPendingInvocationBusyId(invocationId)
    try {
      const response = await fetch(
        `/api/v1/public/widget/sessions/${encodeURIComponent(conversationId)}/tool-invocations/${encodeURIComponent(invocationId)}/cancel`,
        {
          method: 'POST',
          credentials: 'include',
          headers: {
            'X-Ruhu-Widget-Session-Token': widgetSessionToken,
          },
        },
      )
      const body = (await response.json().catch(() => ({}))) as {
        detail?: string
        messages?: Array<{ role?: string; text?: string }>
        pending_tool_invocations?: PendingToolInvocation[]
      }
      if (!response.ok) {
        throw new Error(body.detail || 'Failed to cancel pending action')
      }
      appendAssistantMessages(body.messages || [], 'chat')
      setPendingToolInvocations(body.pending_tool_invocations || [])
    } catch (error) {
      toast.error(getErrorMessage(error) || 'Failed to cancel pending action')
    } finally {
      setPendingInvocationBusyId(null)
    }
  }, [
    appendAssistantMessages,
    pendingInvocationBusyId,
    sharedConversationId,
    widgetSessionConversationId,
    widgetSessionToken,
  ])

  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setInputText(e.target.value)
    if (!isInCall && !isUserTyping && e.target.value.length > 0) setIsUserTyping(true)
    if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current)
    typingTimeoutRef.current = setTimeout(() => {
      setIsUserTyping(false)
    }, 1000)
  }, [isInCall, isUserTyping])

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void handleSendMessage() }
  }, [handleSendMessage])

  // Auto-scroll
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [transcript])

  useEffect(() => {
    if (!voiceSession || !liveKitConnect || !isRoomReady) return
    // Don't auto-enable if the user deliberately muted or mic is already on.
    if (userMutedRef.current || autoEnabledRoomRef.current === voiceSession.roomName || micEnabled || isMicLoading) return

    let cancelled = false
    const timer = setTimeout(async () => {
      if (cancelled) return
      const enabled = await setRoomMicrophoneEnabled(true)
      if (!cancelled && enabled) {
        autoEnabledRoomRef.current = voiceSession.roomName
      }
    }, 500)

    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [voiceSession, liveKitConnect, isRoomReady, micEnabled, isMicLoading, setRoomMicrophoneEnabled])

  // Cleanup
  useEffect(() => {
    return () => { if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current) }
  }, [])

  // Focus input
  useEffect(() => { inputRef.current?.focus() }, [])

  // ==================== Render ====================

  return (
    <div className="flex flex-col h-full rounded-lg border border-border overflow-hidden bg-background">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 bg-primary text-primary-foreground shrink-0">
        <div className="relative">
          <div className="w-9 h-9 rounded-full bg-primary-foreground/20 flex items-center justify-center">
            {supportsVoice ? (
              <Phone className="h-4 w-4" />
            ) : (
              <MessageSquare className="h-4 w-4" />
            )}
          </div>
          {(supportsChat ? chatReady : isInCall) && (
            <div className="absolute -bottom-0.5 -right-0.5 w-3 h-3 bg-emerald-400 rounded-full border-2 border-primary" />
          )}
        </div>
        <div className="flex-1 min-w-0 pr-6">
          <h4 className="font-medium text-sm truncate">{agentName}</h4>
          <p className="text-xs text-primary-foreground/70">
            {isInCall
              ? voiceRoomState === 'reconnecting'
                ? 'Reconnecting...'
                : voiceRoomState === 'recoverable_disconnect'
                ? 'Call disconnected'
                : 'In call'
              : supportsChat
              ? isPreparingChatSession ? 'Connecting...' : chatReady ? 'Online' : 'Offline'
              : agentStatus === 'active' ? 'Ready' : 'Not deployed'}
          </p>
        </div>
      </div>

      {/* Connection warning (chat) */}
      {supportsChat && !chatReady && !isPreparingChatSession && (
        <div className="flex items-center gap-2 px-4 py-2 bg-amber-500/10 border-b border-amber-500/20 shrink-0">
          <WifiOff className="h-3.5 w-3.5 text-amber-500" />
          <p className="text-xs text-amber-500">Chat test session is not connected</p>
        </div>
      )}

      {/* Conversation feed */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3 min-h-0">
        {transcript.length === 0 && (
          <div className="h-full" />
        )}

        {transcript.map(entry => (
          <MessageBubble key={entry.id} entry={entry} onStreamingComplete={handleStreamingComplete} />
        ))}
        {isAgentTyping && !isInCall && (
          <div className="flex justify-start">
            <div className="max-w-[85%] rounded-2xl rounded-bl-md bg-muted px-3 py-2">
              <div className="flex items-center gap-1">
                <div className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/70 [animation-delay:-0.3s]" />
                <div className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/70 [animation-delay:-0.15s]" />
                <div className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/70" />
              </div>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {pendingToolInvocations.length > 0 && (
        <div className="border-t border-amber-200 bg-amber-50 px-4 py-3 shrink-0">
          <div className="text-xs font-semibold text-amber-900 mb-2">Confirmation required</div>
          <div className="space-y-2">
            {pendingToolInvocations.map((invocation) => {
              const busy = pendingInvocationBusyId === invocation.invocation_id
              return (
                <div
                  key={invocation.invocation_id}
                  className="rounded-lg border border-amber-200 bg-white px-3 py-2"
                >
                  <div className="text-sm text-amber-950">
                    {pendingInvocationSummary(invocation)}
                  </div>
                  <div className="mt-2 flex gap-2">
                    <button
                      type="button"
                      onClick={() => void handleConfirmPendingInvocation(invocation.invocation_id)}
                      disabled={busy}
                      className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-60"
                    >
                      {busy ? 'Submitting…' : 'Confirm'}
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleCancelPendingInvocation(invocation.invocation_id)}
                      disabled={busy}
                      className="rounded-md border border-amber-300 px-3 py-1.5 text-xs font-medium text-amber-900 disabled:opacity-60"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      <InteractionDebugPanel
        conversationId={widgetSessionConversationId || sharedConversationId}
        channel={supportsVoice ? 'voice' : 'web_chat'}
        agentDefinition={debugPanelAgentDefinition}
        turnSequence={transcript.length}
      />

      {voiceSession && (
        <div className="hidden">
          {window.__RUHU_E2E_MOCK_VOICE__?.enabled ? (
            <E2EVoiceRoomHarness
              session={voiceSession}
              connect={liveKitConnect}
              onRoomReady={handleRoomReady}
              onConnected={handleVoiceRoomConnected}
              onDisconnected={handleLiveKitDisconnect}
            />
          ) : (
            <LiveKitRoom
              key={liveKitRoomKey ?? voiceSession.roomName}
              serverUrl={voiceSession.serverUrl}
              token={voiceSession.token}
              connect={liveKitConnect}
              audio={false}
              video={false}
              onDisconnected={handleLiveKitDisconnect}
              onError={handleLiveKitError}
            >
              <LiveKitRoomBridge onRoomReady={handleRoomReady} />
              <VoiceConnectionEvents
                onConnected={handleVoiceRoomConnected}
                onReconnecting={handleVoiceRoomReconnecting}
                onReconnected={handleVoiceRoomReconnected}
              />
              <VoiceDataHandler
                onAgentStateChange={(state) => {
                  if (state === 'speaking') {
                    startAgentTyping()
                  } else {
                    stopAgentTyping()
                  }
                }}
                onUserSpeaking={() => undefined}
                onTranscript={handleVoiceTranscript}
              />
              <RoomAudioRenderer />
            </LiveKitRoom>
          )}
        </div>
      )}

      {/* Bottom bar */}
      <div className="border-t border-border bg-card px-3 py-2.5 shrink-0">
        <input
          ref={fileInputRef as React.RefObject<HTMLInputElement>}
          type="file"
          multiple
          className="hidden"
          onChange={(event) => void handleAttachmentFiles(event.target.files)}
        />
        {voiceSession && voiceRoomState === 'reconnecting' && (
          <p className="text-xs text-amber-500 mb-2">Reconnecting voice call...</p>
        )}
        {voiceSession && voiceRoomState === 'recoverable_disconnect' && (
          <div className="mb-2 flex items-center justify-between gap-3">
            <p className="text-xs text-amber-500">Call disconnected. Reconnect to continue the same voice session.</p>
            <button
              type="button"
              onClick={handleRetryVoiceConnection}
              className="rounded-full border border-border px-3 py-1 text-xs font-medium text-foreground transition-colors hover:bg-muted"
            >
              Reconnect
            </button>
          </div>
        )}
        {pendingAttachments.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-2">
            {pendingAttachments.map((attachment) => (
              <div
                key={attachment.attachmentId}
                className="flex items-center gap-2 rounded-full border border-border bg-muted px-3 py-1 text-xs"
              >
                <span className="truncate max-w-[180px]">{attachment.filename}</span>
                <button
                  type="button"
                  onClick={() => removePendingAttachment(attachment.attachmentId)}
                  className="text-muted-foreground hover:text-foreground"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="flex items-center gap-2">

          {/* Input + right-side icons in a pill */}
          <div className="flex items-center gap-2 flex-1 bg-muted/50 rounded-full px-4 py-1.5">
            {supportsChat && (
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={!chatReady || isUploadingAttachment || isInCall}
                title="Attach file"
                className="shrink-0 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
              >
                {isUploadingAttachment ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Paperclip className="h-4 w-4" />
                )}
              </button>
            )}

            {supportsChat ? (
              <input
                ref={inputRef as React.RefObject<HTMLInputElement>}
                value={inputText}
                onChange={handleInputChange}
                onKeyDown={handleKeyDown}
                placeholder="Type a message..."
                disabled={!isInCall && !chatReady}
                className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground disabled:opacity-50 min-w-0"
              />
            ) : (
              <span className="flex-1 text-sm text-muted-foreground">
                {canStartCall
                  ? 'Press call to start'
                  : agentStatus !== 'active'
                  ? 'Deploy agent first'
                  : voiceHealth.isLoading
                  ? 'Checking voice service'
                  : 'Voice unavailable'}
              </span>
            )}

            {/* Mic toggle — shown during active call */}
            {voiceSession && (
              <button
                onClick={handleMicToggle}
                disabled={voiceRoomState !== 'live' || isMicLoading}
                title={micEnabled ? 'Mute microphone' : 'Unmute microphone'}
                className={cn(
                  'shrink-0 transition-colors disabled:opacity-50',
                  micEnabled ? 'text-foreground' : 'text-destructive'
                )}
              >
                {isMicLoading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : micEnabled ? (
                  <Mic className="h-4 w-4" />
                ) : (
                  <MicOff className="h-4 w-4" />
                )}
              </button>
            )}

            {/* End call button — filled primary circle with white X */}
            {voiceSession && (
              <button
                onClick={handleVoiceBarEndCall}
                disabled={isStartingCall}
                title="End call"
                className="shrink-0 h-8 w-8 rounded-full flex items-center justify-center bg-primary hover:opacity-85 active:scale-95 transition-all disabled:opacity-50"
              >
                <X className="h-3.5 w-3.5 text-primary-foreground stroke-[3]" />
              </button>
            )}

            {/* Right icon: send when text typed, call button otherwise */}
            {inputText.trim() && supportsChat ? (
              <button
                onClick={() => void handleSendMessage()}
                disabled={isInCall ? !isRoomReady : !chatReady}
                title="Send"
                className="h-8 w-8 shrink-0 rounded-full flex items-center justify-center bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
              >
                <Send className="h-3.5 w-3.5" />
              </button>
            ) : voiceSession && voiceRoomState === 'recoverable_disconnect' ? (
              <button
                onClick={handleRetryVoiceConnection}
                title="Reconnect voice call"
                className="h-8 w-8 shrink-0 rounded-full flex items-center justify-center bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
              >
                <AudioLines className="h-3.5 w-3.5" />
              </button>
            ) : supportsVoice && !voiceSession ? (
              <button
                onClick={handleStartCall}
                disabled={!canStartCall || isStartingCall}
                title={voiceStatusMessage ?? 'Start voice call'}
                className={cn(
                  'h-8 w-8 shrink-0 rounded-full flex items-center justify-center transition-colors',
                  canStartCall && !isStartingCall
                    ? 'bg-primary text-primary-foreground hover:bg-primary/90'
                    : 'bg-muted text-muted-foreground cursor-not-allowed'
                )}
              >
                {isStartingCall ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <AudioLines className="h-3.5 w-3.5" />
                )}
              </button>
            ) : null}
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="text-center py-1.5 text-[10px] text-muted-foreground border-t border-border bg-card shrink-0">
        Powered by Ruhu
      </div>
    </div>
  )
}
