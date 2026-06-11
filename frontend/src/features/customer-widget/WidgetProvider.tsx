import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type {
  RenderedMessage,
  WidgetAttachment,
  WidgetAttachmentUploadResponse,
  WidgetBrowserTaskProjection,
  WidgetConfig,
  WidgetError,
  WidgetInteractionStatusItem,
  WidgetMessageResponse,
  WidgetProjectionResponse,
  WidgetSession,
  WidgetSessionResponse,
  WidgetVoiceActivity,
  WidgetVoiceInteractionPolicy,
  WidgetVoiceDisconnectResponse,
  WidgetVoiceSessionResponse,
} from './widget-types'
import { buildWidgetPublicPath, normalizeWidgetApiUrl } from './widgetApiUrl'

interface WidgetContextValue {
  config: WidgetConfig
  session: WidgetSession | null
  sessionToken: string | null
  anonymousId: string
  isConnected: boolean
  error: string | null
  createSession: (channel: 'chat' | 'voice' | 'multimodal') => Promise<void>
  endSession: () => Promise<void>
  sendMessage: (
    text: string,
    attachmentIds?: string[],
    metadata?: Record<string, unknown>,
  ) => Promise<{ message_id: string; newMessages: RenderedMessage[] }>
  confirmPendingToolInvocation: (invocationId: string) => Promise<WidgetMessageResponse>
  cancelPendingToolInvocation: (invocationId: string) => Promise<WidgetMessageResponse>
  approveBrowserTask: (taskId: string, approvalId: string) => Promise<WidgetBrowserTaskProjection>
  denyBrowserTask: (taskId: string, approvalId: string) => Promise<WidgetBrowserTaskProjection>
  cancelBrowserTask: (taskId: string) => Promise<WidgetBrowserTaskProjection>
  uploadAttachment: (file: File, channel?: 'widget' | 'voice') => Promise<WidgetAttachmentUploadResponse['attachment']>
  startVoice: () => Promise<WidgetVoiceSessionResponse['transport'] & { realtime_session_id: string }>
  endVoice: () => Promise<void>
  updateToken: (token: string) => void
  clearError: () => void
  trackEvent: (eventType: string, eventData?: Record<string, unknown>) => void
  /** Runtime-owned "what's happening now" status items (spec 23).  Polled while connected. */
  interactionStatus: WidgetInteractionStatusItem[]
  /** Widget-safe browser task projections for this conversation, if enabled by server config. */
  browserTasks: WidgetBrowserTaskProjection[]
  /** Latest voice lifecycle event for the active conversation, if any. */
  voiceActivity: WidgetVoiceActivity | null
  /** Exact resolved voice policy block from the active/recent voice transport. */
  voiceInteractionPolicy: WidgetVoiceInteractionPolicy | null
}

const WidgetContext = createContext<WidgetContextValue | null>(null)

export function useWidgetContext(): WidgetContextValue {
  const ctx = useContext(WidgetContext)
  if (!ctx) throw new Error('useWidgetContext must be used within WidgetProvider')
  return ctx
}

function normalizeError(raw: unknown): WidgetError {
  const candidate = raw as {
    detail?: unknown
    error?: unknown
    message?: unknown
    retry_after?: unknown
  }
  // retry_after may live at the top level (rate-limit body) or inside a
  // nested ``detail`` object (FastAPI HTTPException default shape).
  const topLevelRetry = typeof candidate?.retry_after === 'number'
    ? candidate.retry_after
    : undefined
  const detail = candidate?.detail
  if (typeof detail === 'string') {
    return {
      error: 'request_failed',
      message: detail,
      ...(topLevelRetry !== undefined ? { retry_after: topLevelRetry } : {}),
    }
  }
  if (typeof detail === 'object' && detail !== null) {
    const detailObj = detail as { error?: unknown; message?: unknown; detail?: unknown; retry_after?: unknown }
    const detailRetry = typeof detailObj.retry_after === 'number'
      ? detailObj.retry_after
      : topLevelRetry
    const message =
      (typeof detailObj.message === 'string' && detailObj.message) ||
      (typeof detailObj.detail === 'string' && detailObj.detail) ||
      'Request failed'
    return {
      error: String(detailObj.error || 'request_failed'),
      message: String(message),
      ...(detailRetry !== undefined ? { retry_after: detailRetry } : {}),
    }
  }
  return {
    error: String(candidate?.error || 'request_failed'),
    message: String(candidate?.message || 'Request failed'),
    ...(topLevelRetry !== undefined ? { retry_after: topLevelRetry } : {}),
  }
}

function toWidgetSession(payload: WidgetSessionResponse): WidgetSession {
  return {
    conversationId: payload.conversation_id,
    sessionToken: payload.session_token || '',
    stateId: payload.step_id ?? null,
    messages: normalizeRenderedMessages(payload.messages || []),
    resumed: payload.resumed,
    pendingToolInvocations: payload.pending_tool_invocations || [],
    agentId: payload.agent_id,
  }
}

function normalizeRenderedMessages(
  messages: Array<{
    role?: string
    text?: string
    message_type?: string
    payload?: Record<string, unknown>
    attachments?: WidgetAttachment[]
  }>,
): RenderedMessage[] {
  return messages.map((message) => ({
    role: message.role === 'user' ? 'user' : message.role === 'system' ? 'system' : 'assistant',
    text: message.text || '',
    message_type: typeof message.message_type === 'string' ? message.message_type : undefined,
    payload: message.payload && typeof message.payload === 'object' ? message.payload : undefined,
    attachments: Array.isArray(message.attachments) ? message.attachments : undefined,
  }))
}

function getOrCreateAnonymousId(): string {
  const key = 'ruhu_widget_anon_id'
  try {
    const existing = window.localStorage.getItem(key)
    if (existing) return existing
  } catch {
    // Ignore storage failures.
  }
  const next = crypto.randomUUID()
  try {
    window.localStorage.setItem(key, next)
  } catch {
    // Ignore storage failures.
  }
  return next
}

interface WidgetProviderProps {
  config: WidgetConfig
  children: ReactNode
}

export function WidgetProvider({ config, children }: WidgetProviderProps) {
  const apiBase = normalizeWidgetApiUrl(config.apiUrl)
  const normalizedConfig = useMemo(
    () => ({
      ...config,
      apiUrl: apiBase,
    }),
    [config, apiBase],
  )
  const [session, setSession] = useState<WidgetSession | null>(null)
  const [sessionToken, setSessionToken] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [anonymousId] = useState(getOrCreateAnonymousId)
  const [interactionStatus, setInteractionStatus] = useState<WidgetInteractionStatusItem[]>(
    [],
  )
  const [browserTasks, setBrowserTasks] = useState<WidgetBrowserTaskProjection[]>([])
  const [voiceActivity, setVoiceActivity] = useState<WidgetVoiceActivity | null>(null)
  const [voiceInteractionPolicy, setVoiceInteractionPolicy] = useState<WidgetVoiceInteractionPolicy | null>(null)
  const sessionRef = useRef<WidgetSession | null>(null)
  const tokenRef = useRef<string | null>(null)
  const activeVoiceSessionIdRef = useRef<string | null>(null)

  // Analytics event buffer — flushed every 10 s or when it reaches 20 events.
  const eventQueueRef = useRef<Array<{ event_type: string; event_data: Record<string, unknown>; occurred_at: string }>>([])
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const updateToken = useCallback((token: string) => {
    tokenRef.current = token
    setSessionToken(token)
  }, [])

  const clearError = useCallback(() => setError(null), [])

  // Flush queued analytics events to the backend (fire-and-forget).
  const flushEvents = useCallback(() => {
    const conversation = sessionRef.current
    const token = tokenRef.current
    if (!conversation || !token || eventQueueRef.current.length === 0) return
    const batch = eventQueueRef.current.splice(0)
    const url = buildWidgetPublicPath(
      apiBase,
      `/public/widget/sessions/${encodeURIComponent(conversation.conversationId)}/events`,
    )
    fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Ruhu-Widget-Session-Token': token,
      },
      body: JSON.stringify({ events: batch }),
      credentials: 'omit',
      keepalive: true,
    }).catch(() => undefined)
  }, [apiBase])

  const trackEvent = useCallback((eventType: string, eventData: Record<string, unknown> = {}) => {
    eventQueueRef.current.push({
      event_type: eventType,
      event_data: eventData,
      occurred_at: new Date().toISOString(),
    })
    if (eventQueueRef.current.length >= 20) {
      if (flushTimerRef.current !== null) {
        clearTimeout(flushTimerRef.current)
        flushTimerRef.current = null
      }
      flushEvents()
      return
    }
    if (flushTimerRef.current === null) {
      flushTimerRef.current = setTimeout(() => {
        flushTimerRef.current = null
        flushEvents()
      }, 10_000)
    }
  }, [flushEvents])

  const isConnected = Boolean(sessionRef.current && tokenRef.current)

  // 30-second heartbeat — keeps WidgetSessionRecord.last_activity_at fresh and
  // prevents the server expiry sweep from closing an active session.
  useEffect(() => {
    const interval = setInterval(() => {
      const conversation = sessionRef.current
      const token = tokenRef.current
      if (!conversation || !token) return
      fetch(
        buildWidgetPublicPath(
          apiBase,
          `/public/widget/sessions/${encodeURIComponent(conversation.conversationId)}/heartbeat`,
        ),
        {
          method: 'POST',
          headers: { 'X-Ruhu-Widget-Session-Token': token },
          credentials: 'omit',
        },
      ).catch(() => undefined)
    }, 30_000)
    return () => clearInterval(interval)
  }, [apiBase])

  // Poll projection snapshot for interaction_status + voice_activity while the
  // session is active (spec 23 §Projected Activity / Status Trail).  The
  // snapshot is deliberately shallow so a 2 s cadence is cheap; the backend
  // also supports the /events SSE stream if finer push-ness becomes needed.
  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      const conversation = sessionRef.current
      const token = tokenRef.current
      if (!conversation || !token) return
      try {
        const response = await fetch(
          buildWidgetPublicPath(
            apiBase,
            `/public/widget/sessions/${encodeURIComponent(conversation.conversationId)}/projection`,
          ),
          {
            method: 'GET',
            headers: { 'X-Ruhu-Widget-Session-Token': token },
            credentials: 'omit',
          },
        )
        if (!response.ok) return
        const payload = (await response.json()) as WidgetProjectionResponse
        if (cancelled) return
        if (sessionRef.current && payload.conversation_id === sessionRef.current.conversationId) {
          const nextPending = Array.isArray(payload.pending_tool_invocations)
            ? payload.pending_tool_invocations
            : []
          const currentPending = sessionRef.current.pendingToolInvocations || []
          const changed =
            nextPending.length !== currentPending.length ||
            nextPending.some((item, index) => {
              const current = currentPending[index]
              return (
                !current ||
                current.invocation_id !== item.invocation_id ||
                current.status !== item.status
              )
            })
          if (changed) {
            const nextSession = {
              ...sessionRef.current,
              pendingToolInvocations: nextPending,
            }
            sessionRef.current = nextSession
            setSession(nextSession)
          }
        }
        setInteractionStatus(
          Array.isArray(payload.interaction_status) ? payload.interaction_status : [],
        )
        setBrowserTasks(
          normalizedConfig.features?.browser_tasks === true &&
            normalizedConfig.browserTaskRenderMode !== 'hidden' &&
            Array.isArray(payload.browser_tasks)
            ? payload.browser_tasks
            : [],
        )
        setVoiceActivity(payload.voice_activity ?? null)
        setVoiceInteractionPolicy(payload.voice_interaction_policy ?? null)
      } catch {
        // Swallow errors — the projection is non-critical; the next tick retries.
      }
    }
    // Fire once immediately so the first banner render isn't delayed by a full tick.
    void poll()
    const interval = setInterval(poll, 2_000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [apiBase, normalizedConfig.features?.browser_tasks, normalizedConfig.browserTaskRenderMode, session?.conversationId, sessionToken])

  const sessionHeaders = useCallback((headers?: Record<string, string>) => {
    const merged: Record<string, string> = {
      ...(headers || {}),
    }
    if (tokenRef.current) {
      merged['X-Ruhu-Widget-Session-Token'] = tokenRef.current
    }
    return merged
  }, [])

  const requestJson = useCallback(async <T,>(path: string, options: RequestInit = {}): Promise<T> => {
    const response = await fetch(buildWidgetPublicPath(apiBase, `/public/widget${path}`), {
      ...options,
      // Public widget requests intentionally skip cookie CSRF: authentication is
      // via publishable key/session-token headers and browser credentials stay omitted.
      credentials: 'omit',
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      const widgetError = normalizeError(payload)
      setError(widgetError.message)
      if (response.status === 401 || response.status === 403 || response.status === 404) {
        setSession(null)
        setSessionToken(null)
        tokenRef.current = null
        sessionRef.current = null
        setInteractionStatus([])
        setBrowserTasks([])
        setVoiceActivity(null)
        setVoiceInteractionPolicy(null)
      }
      throw widgetError
    }
    return payload as T
  }, [apiBase])

  const createSession = useCallback(async (_channel: 'chat' | 'voice' | 'multimodal') => {
    setError(null)
    const body: Record<string, unknown> = {
      agent_id: config.agentId,
      target: 'published',
      channel: 'web_widget',
      conversation_id: sessionRef.current?.conversationId || undefined,
      session_token: tokenRef.current || undefined,
      anonymous_id: anonymousId,
    }
    if (config.publishableKey) {
      body.publishable_key = config.publishableKey
    }
    const payload = await requestJson<WidgetSessionResponse>('/sessions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    })
    const nextSession = toWidgetSession(payload)
    sessionRef.current = nextSession
    setSession(nextSession)
    updateToken(nextSession.sessionToken)
    trackEvent('session_start', { resumed: payload.resumed })
  }, [anonymousId, config.agentId, config.publishableKey, requestJson, updateToken, trackEvent])

  const endSession = useCallback(async () => {
    if (!sessionRef.current) return
    if (activeVoiceSessionIdRef.current) {
      await requestJson<WidgetVoiceDisconnectResponse>(
        `/sessions/${encodeURIComponent(sessionRef.current.conversationId)}/voice/disconnect`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...sessionHeaders(),
          },
          body: JSON.stringify({
            realtime_session_id: activeVoiceSessionIdRef.current,
            reason: 'widget_session_ended',
          }),
        },
      ).catch(() => undefined)
      activeVoiceSessionIdRef.current = null
    }
    // Flush analytics before clearing session state.
    flushEvents()
    if (flushTimerRef.current !== null) {
      clearTimeout(flushTimerRef.current)
      flushTimerRef.current = null
    }
    // Notify backend that the session has ended.
    const conversationId = sessionRef.current.conversationId
    await requestJson(`/sessions/${encodeURIComponent(conversationId)}/end`, {
      method: 'POST',
      headers: sessionHeaders(),
    }).catch(() => undefined)
    setSession(null)
    setSessionToken(null)
    tokenRef.current = null
    sessionRef.current = null
    setError(null)
    setInteractionStatus([])
    setBrowserTasks([])
    setVoiceActivity(null)
    setVoiceInteractionPolicy(null)
  }, [flushEvents, requestJson, sessionHeaders])

  const sendMessage = useCallback(async (
    text: string,
    attachmentIds: string[] = [],
    metadata: Record<string, unknown> = {},
  ) => {
    if (!sessionRef.current) throw new Error('No active session')
    const outbound = text.trim() || (
      attachmentIds.length === 1
        ? 'Please review the attached file and respond naturally.'
        : 'Please review the attached files and respond naturally.'
    )
    const conversationId = sessionRef.current.conversationId
    // Per-send idempotency key. The backend uses this to dedupe at-least-once
    // delivery (network retries, double-tap on send) — same key arriving twice
    // means "the same logical send"; different keys mean "two distinct sends
    // even if the text happens to be identical." Generated fresh per call —
    // we don't currently auto-retry, so each invocation is a new logical send.
    const dedupeKey = (typeof crypto !== 'undefined' && crypto.randomUUID)
      ? crypto.randomUUID()
      : `widget-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
    const previousMessages = [...sessionRef.current.messages]
    const submittedUserMessage = normalizeRenderedMessages([{ role: 'user', text: outbound }])[0]
    const response = await fetch(
      buildWidgetPublicPath(
        apiBase,
        `/public/widget/sessions/${encodeURIComponent(conversationId)}/messages/stream`,
      ),
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...sessionHeaders(),
        },
        body: JSON.stringify({
          text: outbound,
          attachment_ids: attachmentIds,
          metadata,
          dedupe_key: dedupeKey,
        }),
        // Same public-widget threat model as requestJson: header-bound widget
        // credentials, no ambient cookies, plus backend origin validation.
        credentials: 'omit',
      },
    )
    if (!response.ok) {
      const errPayload = await response.json().catch(() => ({}))
      const widgetError = normalizeError(errPayload)
      // Honour the Retry-After header on rate-limited responses. The header
      // is the canonical source per RFC 9110 §10.2.3 — body may not carry
      // it for some intermediate proxies. We surface it to callers so the
      // UI can render a "wait Ns" affordance instead of allowing immediate
      // retry that would just hit the same limit.
      if (response.status === 429) {
        const headerRetry = response.headers.get('Retry-After')
        if (headerRetry) {
          const parsed = Number(headerRetry)
          if (Number.isFinite(parsed) && parsed > 0) {
            widgetError.retry_after = parsed
          }
        }
        widgetError.error = widgetError.error || 'rate_limited'
        const seconds = widgetError.retry_after
        if (seconds !== undefined) {
          widgetError.message = `Too many requests — please wait ${Math.ceil(seconds)}s before sending again.`
        }
      }
      setError(widgetError.message)
      if (response.status === 401 || response.status === 403 || response.status === 404) {
        setSession(null)
        setSessionToken(null)
        tokenRef.current = null
        sessionRef.current = null
        setInteractionStatus([])
        setBrowserTasks([])
        setVoiceActivity(null)
        setVoiceInteractionPolicy(null)
      }
      throw widgetError
    }

    // Read SSE stream and collect new messages
    const reader = response.body?.getReader()
    const collectedMessages: Array<{
      role?: 'user' | 'assistant' | 'system'
      text?: string
      message_type?: string
      payload?: Record<string, unknown>
      attachments?: WidgetAttachment[]
    }> = []
    let donePayload: {
      step_after?: string | null
      trace_id?: string | null
      pending_tool_invocations?: WidgetSession['pendingToolInvocations']
    } | null = null

    if (reader) {
      const decoder = new TextDecoder()
      let buffer = ''
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
              if (currentEvent === 'message') {
                collectedMessages.push({
                  role: data.role === 'user' || data.role === 'system' ? data.role : 'assistant',
                  text: (data.text as string | undefined) ?? '',
                  message_type: typeof data.message_type === 'string' ? data.message_type : undefined,
                  payload: data.payload && typeof data.payload === 'object'
                    ? (data.payload as Record<string, unknown>)
                    : undefined,
                  attachments: Array.isArray(data.attachments)
                    ? (data.attachments as WidgetAttachment[])
                    : undefined,
                })
              } else if (currentEvent === 'error') {
                const widgetError = normalizeError(data)
                setError(widgetError.message)
                throw widgetError
              } else if (currentEvent === 'done') {
                donePayload = {
                  step_after: typeof data.step_after === 'string' || data.step_after === null
                    ? (data.step_after as string | null)
                    : null,
                  trace_id: typeof data.trace_id === 'string' || data.trace_id === null
                    ? (data.trace_id as string | null)
                    : null,
                  pending_tool_invocations: Array.isArray(data.pending_tool_invocations)
                    ? (data.pending_tool_invocations as WidgetSession['pendingToolInvocations'])
                    : [],
                }
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
    }

    const newMessages = normalizeRenderedMessages(collectedMessages)
    let returnedMessages = newMessages.filter((message) => message.role !== 'user')

    if (returnedMessages.length === 0 && donePayload?.trace_id) {
      try {
        const refreshedPayload = await requestJson<WidgetSessionResponse>(
          `/sessions/${encodeURIComponent(conversationId)}`,
          { headers: sessionHeaders() },
        )
        const refreshedSession = toWidgetSession(refreshedPayload)
        const transcriptDelta = refreshedSession.messages.slice(previousMessages.length)
        returnedMessages = transcriptDelta.filter((message) =>
          message.role !== 'user' &&
          ((message.text || '').trim().length > 0 || (message.attachments || []).length > 0),
        )
        sessionRef.current = refreshedSession
        setSession(refreshedSession)
        return { message_id: crypto.randomUUID(), newMessages: returnedMessages }
      } catch {
        // If transcript refresh fails, still commit the submitted user message
        // below; the live event stream/replay path may still catch up.
      }
    }

    const current = sessionRef.current
    if (current) {
      const nextMessages = [...current.messages]
      if (submittedUserMessage) {
        nextMessages.push(submittedUserMessage)
      }
      nextMessages.push(...newMessages)
      const nextSession = {
        ...current,
        messages: nextMessages,
        stateId: donePayload?.step_after ?? current.stateId ?? null,
        pendingToolInvocations: donePayload?.pending_tool_invocations ?? current.pendingToolInvocations,
      }
      sessionRef.current = nextSession
      setSession(nextSession)
    }
    return { message_id: crypto.randomUUID(), newMessages: returnedMessages }
  }, [apiBase, requestJson, sessionHeaders])

  const confirmPendingToolInvocation = useCallback(async (invocationId: string) => {
    if (!sessionRef.current) throw new Error('No active session')
    const conversationId = sessionRef.current.conversationId
    const payload = await requestJson<WidgetMessageResponse>(
      `/sessions/${encodeURIComponent(conversationId)}/tool-invocations/${encodeURIComponent(invocationId)}/confirm`,
      {
        method: 'POST',
        headers: sessionHeaders(),
      },
    )
    const current = sessionRef.current
    if (current) {
      const nextSession = {
        ...current,
        messages: [...current.messages, ...normalizeRenderedMessages(payload.messages || [])],
        stateId: payload.step_after ?? current.stateId ?? null,
        pendingToolInvocations: payload.pending_tool_invocations || [],
      }
      sessionRef.current = nextSession
      setSession(nextSession)
    }
    return payload
  }, [requestJson, sessionHeaders])

  const cancelPendingToolInvocation = useCallback(async (invocationId: string) => {
    if (!sessionRef.current) throw new Error('No active session')
    const conversationId = sessionRef.current.conversationId
    const payload = await requestJson<WidgetMessageResponse>(
      `/sessions/${encodeURIComponent(conversationId)}/tool-invocations/${encodeURIComponent(invocationId)}/cancel`,
      {
        method: 'POST',
        headers: sessionHeaders(),
      },
    )
    const current = sessionRef.current
    if (current) {
      const nextSession = {
        ...current,
        messages: [...current.messages, ...normalizeRenderedMessages(payload.messages || [])],
        stateId: payload.step_after ?? current.stateId ?? null,
        pendingToolInvocations: payload.pending_tool_invocations || [],
      }
      sessionRef.current = nextSession
      setSession(nextSession)
    }
    return payload
  }, [requestJson, sessionHeaders])

  const replaceBrowserTask = useCallback((task: WidgetBrowserTaskProjection) => {
    setBrowserTasks((current) => current.map((item) => item.task_id === task.task_id ? task : item))
    return task
  }, [])

  const approveBrowserTask = useCallback(async (taskId: string, approvalId: string) => {
    if (!sessionRef.current) throw new Error('No active session')
    const task = await requestJson<WidgetBrowserTaskProjection>(
      `/sessions/${encodeURIComponent(sessionRef.current.conversationId)}/browser-tasks/${encodeURIComponent(taskId)}/approvals/${encodeURIComponent(approvalId)}/approve`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...sessionHeaders(),
        },
        body: JSON.stringify({ reason: 'approved from widget' }),
      },
    )
    return replaceBrowserTask(task)
  }, [replaceBrowserTask, requestJson, sessionHeaders])

  const denyBrowserTask = useCallback(async (taskId: string, approvalId: string) => {
    if (!sessionRef.current) throw new Error('No active session')
    const task = await requestJson<WidgetBrowserTaskProjection>(
      `/sessions/${encodeURIComponent(sessionRef.current.conversationId)}/browser-tasks/${encodeURIComponent(taskId)}/approvals/${encodeURIComponent(approvalId)}/deny`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...sessionHeaders(),
        },
        body: JSON.stringify({ reason: 'denied from widget' }),
      },
    )
    return replaceBrowserTask(task)
  }, [replaceBrowserTask, requestJson, sessionHeaders])

  const cancelBrowserTask = useCallback(async (taskId: string) => {
    if (!sessionRef.current) throw new Error('No active session')
    const task = await requestJson<WidgetBrowserTaskProjection>(
      `/sessions/${encodeURIComponent(sessionRef.current.conversationId)}/browser-tasks/${encodeURIComponent(taskId)}/cancel`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...sessionHeaders(),
        },
        body: JSON.stringify({ reason: 'cancelled from widget' }),
      },
    )
    return replaceBrowserTask(task)
  }, [replaceBrowserTask, requestJson, sessionHeaders])

  const uploadAttachment = useCallback(async (
    file: File,
    _channel: 'widget' | 'voice' = 'widget',
  ): Promise<WidgetAttachmentUploadResponse['attachment']> => {
    if (!sessionRef.current) throw new Error('No active session')
    const response = await fetch(
      buildWidgetPublicPath(
        apiBase,
        `/public/widget/sessions/${encodeURIComponent(sessionRef.current.conversationId)}/attachments?filename=${encodeURIComponent(file.name)}`,
      ),
      {
        method: 'POST',
        headers: {
          'Content-Type': file.type || 'application/octet-stream',
          ...sessionHeaders(),
        },
        body: file,
        credentials: 'omit',
      },
    )
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      const widgetError = normalizeError(payload)
      setError(widgetError.message)
      throw widgetError
    }
    return (payload as WidgetAttachmentUploadResponse).attachment
  }, [apiBase, sessionHeaders])

  const startVoice = useCallback(async () => {
    if (!sessionRef.current) throw new Error('No active session')
    const payload = await requestJson<WidgetVoiceSessionResponse>(
      `/sessions/${encodeURIComponent(sessionRef.current.conversationId)}/voice`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...sessionHeaders(),
        },
        body: JSON.stringify({
          participant_identity: anonymousId,
          participant_name: 'Widget Visitor',
        }),
      },
    )
    activeVoiceSessionIdRef.current = payload.realtime_session_id
    const transportPolicy =
      payload.transport?.metadata &&
      typeof payload.transport.metadata === 'object' &&
      payload.transport.metadata.voice_interaction_policy &&
      typeof payload.transport.metadata.voice_interaction_policy === 'object'
        ? (payload.transport.metadata.voice_interaction_policy as WidgetVoiceInteractionPolicy)
        : null
    if (transportPolicy) {
      setVoiceInteractionPolicy(transportPolicy)
    }
    return {
      ...payload.transport,
      realtime_session_id: payload.realtime_session_id,
    }
  }, [anonymousId, requestJson, sessionHeaders])

  const endVoice = useCallback(async () => {
    if (!sessionRef.current || !activeVoiceSessionIdRef.current) return
    await requestJson<WidgetVoiceDisconnectResponse>(
      `/sessions/${encodeURIComponent(sessionRef.current.conversationId)}/voice/disconnect`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...sessionHeaders(),
        },
        body: JSON.stringify({
          realtime_session_id: activeVoiceSessionIdRef.current,
          reason: 'widget_client_disconnected',
        }),
      },
    ).catch(() => undefined)
    activeVoiceSessionIdRef.current = null
  }, [requestJson, sessionHeaders])

  const value = useMemo<WidgetContextValue>(() => ({
    config: normalizedConfig,
    session,
    sessionToken,
    anonymousId,
    isConnected,
    error,
    createSession,
    endSession,
    sendMessage,
    confirmPendingToolInvocation,
    cancelPendingToolInvocation,
    approveBrowserTask,
    denyBrowserTask,
    cancelBrowserTask,
    uploadAttachment,
    startVoice,
    endVoice,
    updateToken,
    clearError,
    trackEvent,
    interactionStatus,
    browserTasks,
    voiceActivity,
    voiceInteractionPolicy,
  }), [
    anonymousId,
    approveBrowserTask,
    cancelBrowserTask,
    clearError,
    normalizedConfig,
    createSession,
    cancelPendingToolInvocation,
    denyBrowserTask,
    endSession,
    endVoice,
    error,
    browserTasks,
    interactionStatus,
    isConnected,
    sendMessage,
    confirmPendingToolInvocation,
    session,
    sessionToken,
    startVoice,
    trackEvent,
    updateToken,
    uploadAttachment,
    voiceActivity,
    voiceInteractionPolicy,
  ])

  return <WidgetContext.Provider value={value}>{children}</WidgetContext.Provider>
}
