import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useWidgetContext } from './WidgetProvider'
import type {
  ActivityItem,
  ChatMessage,
  SSEConversationStateEvent,
  WidgetAttachment,
} from './widget-types'
import { buildWidgetPublicPath, normalizeWidgetApiUrl } from './widgetApiUrl'

/**
 * Generate a stable, globally unique ID for a chat message.
 *
 * React keys must be unique across the entire list and stable for the
 * lifetime of an item. Earlier code used `Date.now()` + index combinations,
 * which collide when two messages arrive in the same millisecond — React
 * silently merges them, dropping one from the DOM. text-based keys also
 * caused remounts during streaming as the content grew character by
 * character. UUIDs avoid both classes of bug.
 */
function stableMessageId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  // Fallback for older runtimes that lack crypto.randomUUID — extremely
  // unlikely in our supported browsers, but keeps the widget loadable in
  // edge environments. Combine a high-resolution timestamp with random
  // entropy to make collisions vanishingly unlikely.
  return `msg-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}

interface PublicConversationEvent {
  event_id: string
  family: string
  name: string
  conversation_sequence: number
  payload?: Record<string, unknown>
  created_at: string
}

function buildConversationEventsReplayUrl(
  apiUrl: string,
  conversationId: string,
  afterSequence: number,
): string {
  const normalizedApiUrl = normalizeWidgetApiUrl(apiUrl)
  const replayUrl = new URL(
    buildWidgetPublicPath(
      normalizedApiUrl,
      `/public/widget/sessions/${encodeURIComponent(conversationId)}/conversation-events/replay`,
    ),
  )
  replayUrl.search = new URLSearchParams({
    after_sequence: String(afterSequence),
  }).toString()
  return replayUrl.toString()
}

function buildConversationEventsStreamUrl(
  apiUrl: string,
  conversationId: string,
  sessionToken: string,
  afterSequence: number,
): string {
  const normalizedApiUrl = normalizeWidgetApiUrl(apiUrl)
  const streamUrl = new URL(
    buildWidgetPublicPath(
      normalizedApiUrl,
      `/public/widget/sessions/${encodeURIComponent(conversationId)}/conversation-events`,
    ),
  )
  streamUrl.search = new URLSearchParams({
    session_token: sessionToken,
    after_sequence: String(afterSequence),
  }).toString()
  return streamUrl.toString()
}

interface UseWidgetChat {
  messages: ChatMessage[]
  activities: Map<string, ActivityItem>
  isTyping: boolean
  conversationState: SSEConversationStateEvent | null
  sendMessage: (
    text: string,
    attachments?: WidgetAttachment[],
    metadata?: Record<string, unknown>,
  ) => Promise<void>
  confirmPendingToolInvocation: (invocationId: string) => Promise<void>
  cancelPendingToolInvocation: (invocationId: string) => Promise<void>
  appendLocalUserMessage: (text: string, attachments?: WidgetAttachment[]) => void
  dismissActivity: (activityId: string) => void
  isConnected: boolean
}

function toChatMessages(messages: Array<{
  role?: string
  text?: string
  message_type?: string
  payload?: Record<string, unknown>
  attachments?: WidgetAttachment[]
}>): ChatMessage[] {
  return (messages || [])
    .filter((message) => (message.text || '').trim().length > 0 || (message.attachments || []).length > 0)
    .map((message) => ({
      // UUIDs not text-or-index — text-based keys re-key when content
      // updates (during streaming) which causes React to remount the
      // bubble and lose typing animation state. Index-based keys collide
      // when the bootstrap list and the live list share positions.
      id: stableMessageId(),
      role: (message.role === 'user' ? 'user' : message.role === 'system' ? 'system' : 'assistant') as 'user' | 'assistant' | 'system',
      content: message.text || '',
      timestamp: new Date(),
      done: true,
      attachments: Array.isArray(message.attachments) ? message.attachments : undefined,
      metadata: {
        ...(typeof (message as { message_type?: unknown }).message_type === 'string'
          ? { message_type: (message as { message_type?: string }).message_type }
          : {}),
        ...(
          (message as { payload?: unknown }).payload &&
          typeof (message as { payload?: unknown }).payload === 'object'
            ? { payload: (message as { payload?: Record<string, unknown> }).payload }
            : {}
        ),
      },
    }))
}

export function useWidgetChat(): UseWidgetChat {
  const {
    config,
    session,
    sessionToken,
    isConnected,
    sendMessage: sendContextMessage,
    confirmPendingToolInvocation: confirmContextInvocation,
    cancelPendingToolInvocation: cancelContextInvocation,
  } = useWidgetContext()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isTyping, setIsTyping] = useState(false)
  const sessionInitRef = useRef<string | null>(null)
  const conversationSequenceRef = useRef(0)
  const seenAssistantEventIdsRef = useRef<Set<string>>(new Set())
  const replayTimerRef = useRef<number | null>(null)
  const replayInFlightRef = useRef(false)
  // Count of assistant messages already rendered in the UI but not yet
  // ack'd by an SSE event for the same content. Tracks position-in-stream
  // (FIFO) rather than text — SSE delivers events in order, so the next
  // ``expectedAssistantEventCountRef`` SSE assistant events are duplicates
  // of already-rendered bootstrap/optimistic messages and should be skipped.
  //
  // Why FIFO and not text-keyed: an agent that legitimately responds with
  // the same string twice (e.g., "Got it." both turns) used to have its
  // second response silently dropped because text-based dedupe couldn't
  // distinguish "second delivery of message A" from "first delivery of
  // message B with identical text."
  const expectedAssistantEventCountRef = useRef(0)

  useEffect(() => {
    if (!session) {
      setMessages([])
      setIsTyping(false)
      sessionInitRef.current = null
      seenAssistantEventIdsRef.current.clear()
      expectedAssistantEventCountRef.current = 0
      return
    }
    // Only load initial messages once per conversation — subsequent updates come via sendMessage
    if (sessionInitRef.current === session.conversationId) return
    sessionInitRef.current = session.conversationId
    const initMessages = toChatMessages(session.messages)
    conversationSequenceRef.current = 0
    seenAssistantEventIdsRef.current.clear()
    // Pre-charge the dedupe counter only for resumed transcripts. Fresh
    // sessions can include a welcome message that is returned directly by the
    // session-create response but is not replayed as a conversation event.
    expectedAssistantEventCountRef.current = session.resumed
      ? initMessages.filter((message) => message.role !== 'user').length
      : 0
    setMessages(initMessages)
  }, [session])

  useEffect(() => {
    if (replayTimerRef.current) {
      window.clearTimeout(replayTimerRef.current)
      replayTimerRef.current = null
    }
    replayInFlightRef.current = false
    if (!session || !sessionToken || typeof window === 'undefined') {
      return
    }

    let cancelled = false
    let eventSource: EventSource | null = null

    const applyEvent = (event: PublicConversationEvent) => {
      const sequence = Number(event.conversation_sequence || 0)
      if (sequence <= conversationSequenceRef.current) {
        return
      }
      conversationSequenceRef.current = Math.max(
        conversationSequenceRef.current,
        sequence,
      )
      const payload = event.payload || {}
      if (event.family === 'voice') {
        if (event.name === 'assistant_speaking_started') {
          setIsTyping(true)
          return
        }
        if (event.name === 'assistant_speaking_stopped' || event.name === 'assistant_interrupted') {
          setIsTyping(false)
          return
        }
        return
      }
      if (event.family !== 'message' || event.name !== 'assistant_emitted') {
        return
      }
      const text = String(payload.text || '').trim()
      if (!text) return
      // Position-based dedupe: the next N assistant events delivered via
      // SSE are duplicates of bootstrap/optimistic messages we already
      // rendered. Subtract one and skip — irrespective of text. This
      // intentionally ignores content matching so an agent legitimately
      // sending the same text twice still surfaces both occurrences.
      if (expectedAssistantEventCountRef.current > 0) {
        expectedAssistantEventCountRef.current -= 1
        setIsTyping(false)
        return
      }
      const eventId = `evt_${event.conversation_sequence}`
      if (seenAssistantEventIdsRef.current.has(eventId)) {
        setIsTyping(false)
        return
      }
      seenAssistantEventIdsRef.current.add(eventId)
      setMessages((prev) => {
        return [
          ...prev,
          {
            id: eventId,
            role: 'assistant',
            content: text,
            timestamp: new Date(event.created_at),
            done: true,
            metadata: {
              ...(typeof payload.message_type === 'string' ? { message_type: payload.message_type } : {}),
              ...(payload.payload && typeof payload.payload === 'object'
                ? { payload: payload.payload as Record<string, unknown> }
                : {}),
            },
          },
        ]
      })
      setIsTyping(false)
    }

    if (typeof window.EventSource !== 'undefined') {
      try {
        const streamUrl = buildConversationEventsStreamUrl(
          config.apiUrl,
          session.conversationId,
          sessionToken,
          conversationSequenceRef.current,
        )
        eventSource = new window.EventSource(streamUrl, { withCredentials: false })
        eventSource.addEventListener('conversation.event', (message) => {
          if (cancelled) return
          try {
            const payload = JSON.parse(message.data) as PublicConversationEvent
            applyEvent(payload)
          } catch {
            // Ignore malformed stream payloads and wait for the next event.
          }
        })
        return () => {
          cancelled = true
          eventSource?.close()
        }
      } catch {
        eventSource = null
      }
    }

    const poll = async () => {
      if (cancelled || replayInFlightRef.current) return
      replayInFlightRef.current = true
      try {
        const url = buildConversationEventsReplayUrl(
          config.apiUrl,
          session.conversationId,
          conversationSequenceRef.current,
        )
        const response = await fetch(url, {
          method: 'GET',
          credentials: 'omit',
          headers: {
            'X-Ruhu-Widget-Session-Token': sessionToken,
          },
        })
        const body = (await response.json().catch(() => ([]))) as PublicConversationEvent[] | { detail?: string }
        if (!response.ok) {
          throw new Error(Array.isArray(body) ? 'Failed to replay widget events' : (body.detail || 'Failed to replay widget events'))
        }
        if (Array.isArray(body)) {
          for (const event of body) {
            applyEvent(event)
          }
        }
      } finally {
        replayInFlightRef.current = false
        if (!cancelled) {
          replayTimerRef.current = window.setTimeout(poll, 250)
        }
      }
    }

    void poll()
    return () => {
      cancelled = true
      if (replayTimerRef.current) {
        window.clearTimeout(replayTimerRef.current)
        replayTimerRef.current = null
      }
      replayInFlightRef.current = false
    }
  }, [config.apiUrl, session, sessionToken])

  const rememberOptimisticAssistantMessages = useCallback((messagesToRecord: Array<{ text?: string }>) => {
    if (messagesToRecord.length === 0) return
    // Count non-empty messages — the SSE side will deliver one assistant
    // event per emitted message, and we want to skip exactly that many
    // before rendering future events.
    const eligible = messagesToRecord.filter((m) => String(m.text || '').trim().length > 0)
    expectedAssistantEventCountRef.current += eligible.length
  }, [])

  const sendMessage = useCallback(async (
    text: string,
    attachments: WidgetAttachment[] = [],
    metadata: Record<string, unknown> = {},
  ) => {
    const cleaned = text.trim()
    const hasAttachments = attachments.length > 0
    if (!cleaned && !hasAttachments) return

    const localText = cleaned || (
      attachments.length === 1
        ? `Shared attachment: ${attachments[0].filename || 'attachment'}`
        : `Shared ${attachments.length} attachments`
    )

    // Track this message's id so we can flip its status on success/failure.
    const userMessageId = crypto.randomUUID()
    setMessages((prev) => [
      ...prev,
      {
        id: userMessageId,
        role: 'user',
        content: localText,
        timestamp: new Date(),
        done: true,
        attachments,
        status: 'sending',
      },
    ])
    setIsTyping(true)

    try {
      const result = await sendContextMessage(
        cleaned,
        attachments.map((attachment) => attachment.attachment_id),
        metadata,
      )
      // Promote the user message to 'sent' once the server has processed
      // the turn. Anything else (failure, mid-stream error) leaves it in
      // 'sending' or moves it to 'failed' via the catch block below.
      setMessages((prev) =>
        prev.map((m) => (m.id === userMessageId ? { ...m, status: 'sent' as const } : m)),
      )
      // Append the new messages returned by the streaming response directly so the
      // UI updates immediately, without waiting for the conversation events SSE.
      const assistantMessages = toChatMessages(result.newMessages).filter((message) => message.role !== 'user')
      if (assistantMessages.length > 0) {
        rememberOptimisticAssistantMessages(assistantMessages.map((message) => ({ text: message.content })))
        setMessages((prev) => [
          ...prev,
          ...assistantMessages
            .filter((m) => m.content.trim().length > 0 || (m.attachments || []).length > 0)
            .map((m) => ({
              id: stableMessageId(),
              role: m.role,
              content: m.content,
              timestamp: new Date(),
              done: true,
              attachments: m.attachments,
              metadata: {
                ...(m.metadata || {}),
              },
            })),
        ])
      }
    } catch (err) {
      // Mark the user's optimistic message as failed and surface the
      // human-readable reason. The UI uses ``status==='failed'`` to render
      // a retry affordance (see ChatMessage.status JSDoc). We deliberately
      // leave the message in the transcript so the user can see what they
      // tried to send and retry without retyping.
      const reason =
        err && typeof err === 'object' && 'message' in err && typeof (err as { message?: unknown }).message === 'string'
          ? String((err as { message?: unknown }).message)
          : 'Failed to send. Please try again.'
      setMessages((prev) =>
        prev.map((m) =>
          m.id === userMessageId
            ? { ...m, status: 'failed' as const, failureReason: reason }
            : m,
        ),
      )
      throw err
    } finally {
      setIsTyping(false)
    }
  }, [rememberOptimisticAssistantMessages, sendContextMessage])

  const appendLocalUserMessage = useCallback((text: string, attachments: WidgetAttachment[] = []) => {
    const cleaned = text.trim()
    if (!cleaned && attachments.length === 0) return
    setMessages((prev) => [
      ...prev,
      {
        id: crypto.randomUUID(),
        role: 'user',
        content: cleaned,
        timestamp: new Date(),
        done: true,
        attachments,
      },
    ])
  }, [])

  const appendAssistantMessages = useCallback((messagesToAppend: Array<{
    role?: 'user' | 'assistant' | 'system'
    text?: string
    message_type?: string
    payload?: Record<string, unknown>
    attachments?: WidgetAttachment[]
  }>) => {
    const normalized = toChatMessages(messagesToAppend)
    if (normalized.length === 0) return
    rememberOptimisticAssistantMessages(normalized.map((message) => ({ text: message.content })))
    setMessages((prev) => [
      ...prev,
      ...normalized
        .filter((m) => m.content.trim().length > 0 || (m.attachments || []).length > 0)
        .map((m) => ({
          id: stableMessageId(),
          role: m.role,
          content: m.content,
          timestamp: new Date(),
          done: true,
          attachments: m.attachments,
          metadata: m.metadata,
        })),
    ])
  }, [rememberOptimisticAssistantMessages])

  const confirmPendingToolInvocation = useCallback(async (invocationId: string) => {
    const result = await confirmContextInvocation(invocationId)
    appendAssistantMessages(result.messages || [])
    setIsTyping(false)
  }, [appendAssistantMessages, confirmContextInvocation])

  const cancelPendingToolInvocation = useCallback(async (invocationId: string) => {
    const result = await cancelContextInvocation(invocationId)
    appendAssistantMessages(result.messages || [])
    setIsTyping(false)
  }, [appendAssistantMessages, cancelContextInvocation])

  const activities = useMemo(() => new Map<string, ActivityItem>(), [])
  const dismissActivity = useCallback((_activityId: string) => undefined, [])

  return {
    messages,
    activities,
    isTyping,
    conversationState: null,
    sendMessage,
    confirmPendingToolInvocation,
    cancelPendingToolInvocation,
    appendLocalUserMessage,
    dismissActivity,
    isConnected,
  }
}
