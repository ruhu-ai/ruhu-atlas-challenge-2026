import type { PendingToolInvocation } from './types'

export function buildWidgetConversationEventsStreamUrl(
  conversationId: string,
  sessionToken: string,
  afterSequence: number,
): string {
  const streamUrl = new URL(
    `/api/v1/public/widget/sessions/${encodeURIComponent(conversationId)}/conversation-events`,
    window.location.origin,
  )
  streamUrl.search = new URLSearchParams({
    session_token: sessionToken,
    after_sequence: String(afterSequence),
  }).toString()
  return streamUrl.toString()
}

export function pendingInvocationSummary(invocation: PendingToolInvocation): string {
  const prompt = invocation.metadata?.confirmation_prompt
  if (typeof prompt === 'string' && prompt.trim()) return prompt
  if (invocation.error && invocation.error.trim()) return invocation.error
  if (invocation.decision_reason && invocation.decision_reason.trim()) return invocation.decision_reason
  if (invocation.reason && invocation.reason.trim()) return invocation.reason
  return invocation.tool_ref
}

export function getErrorMessage(error: unknown): string | null {
  if (!error || typeof error !== 'object') return null

  const candidate = error as {
    detail?: unknown
    message?: unknown
    response?: {
      data?: {
        detail?: unknown
      }
    }
  }

  if (typeof candidate.detail === 'string' && candidate.detail.trim()) {
    return candidate.detail
  }

  if (typeof candidate.response?.data?.detail === 'string' && candidate.response.data.detail.trim()) {
    return candidate.response.data.detail
  }

  if (typeof candidate.message === 'string' && candidate.message.trim()) {
    return candidate.message
  }

  return null
}
