/**
 * Pure helpers for the Atlas AI panel — display-message construction,
 * attachment conversion, API-discovery request building, and readiness
 * formatting. Extracted from AtlasAIPanel.tsx (RP-4.4) so the panel
 * component and its hooks share one implementation.
 */

import { ApiError } from '@/api/client'

import type {
  AtlasAPIDiscoveryRequest,
  AtlasAttachmentInput,
  AtlasMessageItem,
  AtlasReadinessRunSummary,
  AtlasTurnResponse,
} from '@/api/services/atlas.service'

import { hasActionableAtlasResponse } from './atlas-shared'

export interface DisplayMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: Date
  /** When set, the message is the assistant's response to a turn and the
   * panel will render cards (questions/deltas/permissions/blockers) below it. */
  turnResponse?: AtlasTurnResponse
}

let displayMessageIdCounter = 0

export function newDisplayMessageId(prefix: string): string {
  displayMessageIdCounter += 1
  return `${prefix}-${Date.now()}-${displayMessageIdCounter}`
}

export const SUGGESTED_PROMPTS = [
  'Build a customer support workflow',
  'Add a knowledge base lookup',
  'Validate this agent for production',
]

export const LONG_PASTE_THRESHOLD = 2500
const MAX_ATTACHMENT_TEXT_CHARS = 12000
const MAX_CONTEXT_ATTACHMENT_CHARS = 18000

const GREETING_CONTENT =
  "Hi! I'm Atlas. Describe what you want to change about this agent and I'll propose the edits."

export function isValidAgentId(id?: string): boolean {
  if (!id) return false
  if (id === 'new') return false
  return true
}

export function greetingMessage(): DisplayMessage {
  return {
    id: 'greeting',
    role: 'assistant',
    content: GREETING_CONTENT,
    timestamp: new Date(),
  }
}

export function formatReadinessState(state: string): string {
  return state.replace(/_/g, ' ')
}

export function formatRunDate(value?: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

export function runScorePercent(summary?: AtlasReadinessRunSummary | null): number | null {
  const score = summary?.report?.score_breakdown?.run_score
  return typeof score === 'number' ? Math.round(score * 100) : null
}

export function backendMessageToDisplay(msg: AtlasMessageItem): DisplayMessage {
  const role: DisplayMessage['role'] =
    msg.role === 'user' ? 'user' : msg.role === 'assistant' ? 'assistant' : 'system'
  return {
    id: msg.message_id,
    role,
    content: msg.content,
    timestamp: new Date(msg.created_at),
  }
}

export function errorMessage(err: unknown): DisplayMessage {
  const detail =
    err instanceof ApiError && typeof err.detail === 'object' && err.detail !== null && 'detail' in err.detail
      ? String((err.detail as { detail?: unknown }).detail)
      : err instanceof Error
        ? err.message
        : 'unknown error'
  return {
    id: newDisplayMessageId('err'),
    role: 'system',
    content: `Atlas had trouble with that: ${detail}`,
    timestamp: new Date(),
  }
}

export function isUrl(value: string): boolean {
  try {
    const parsed = new URL(value.trim())
    return parsed.protocol === 'http:' || parsed.protocol === 'https:'
  } catch {
    return false
  }
}

function classifyAttachmentKind(file: File): AtlasAttachmentInput['kind'] {
  const name = file.name.toLowerCase()
  if (file.type.startsWith('image/')) return 'image'
  if (name.endsWith('.json')) return 'json_brief'
  if (name.endsWith('.yaml') || name.endsWith('.yml')) return 'spec'
  if (name.endsWith('.md') || name.endsWith('.txt')) return 'document'
  if (name.endsWith('.pdf') || name.endsWith('.docx')) return 'document'
  return 'document'
}

function canReadFileAsText(file: File): boolean {
  const name = file.name.toLowerCase()
  return (
    file.type.startsWith('text/') ||
    file.type.includes('json') ||
    name.endsWith('.json') ||
    name.endsWith('.yaml') ||
    name.endsWith('.yml') ||
    name.endsWith('.md') ||
    name.endsWith('.txt')
  )
}

export async function fileToAttachment(file: File): Promise<AtlasAttachmentInput> {
  let text: string | null = null
  if (canReadFileAsText(file) && file.size <= 250_000) {
    text = await file.text()
  }
  const truncated = Boolean(text && text.length > MAX_ATTACHMENT_TEXT_CHARS)
  const clipped = text ? text.slice(0, MAX_ATTACHMENT_TEXT_CHARS) : undefined
  return {
    attachment_id: `local_file_${Date.now()}_${Math.random().toString(36).slice(2)}`,
    kind: classifyAttachmentKind(file),
    display_name: file.name,
    metadata: {
      filename: file.name,
      mime_type: file.type || 'application/octet-stream',
      size_bytes: file.size,
      source: 'browser_file',
      ...(clipped ? { text: clipped } : {}),
      extracted_characters: text?.length ?? 0,
      chunk_count: text ? 1 : 0,
      used_chunk_count: clipped ? 1 : 0,
      truncated,
      quality_flags: text ? (truncated ? ['text_truncated'] : []) : ['binary_or_unread_text'],
    },
  }
}

export function pastedChunkToAttachment(chunk: { id: number; content: string }): AtlasAttachmentInput {
  const truncated = chunk.content.length > MAX_ATTACHMENT_TEXT_CHARS
  return {
    attachment_id: `pasted_${chunk.id}`,
    kind: 'workflow_description',
    display_name: `Pasted content ${chunk.id}`,
    metadata: {
      source: 'large_paste',
      text: chunk.content.slice(0, MAX_ATTACHMENT_TEXT_CHARS),
      extracted_characters: chunk.content.length,
      chunk_count: 1,
      used_chunk_count: 1,
      truncated,
      quality_flags: truncated ? ['text_truncated'] : [],
    },
  }
}

export function appendAttachmentContext(message: string, attachments: AtlasAttachmentInput[]): string {
  let remaining = MAX_CONTEXT_ATTACHMENT_CHARS
  const sections: string[] = []
  for (const attachment of attachments) {
    const text = typeof attachment.metadata?.text === 'string' ? attachment.metadata.text.trim() : ''
    if (!text || remaining <= 0) continue
    const clipped = text.slice(0, remaining)
    remaining -= clipped.length
    sections.push(`Attachment: ${attachment.display_name}\n${clipped}`)
  }
  if (sections.length === 0) return message
  return `${message}\n\n${sections.join('\n\n')}`
}

export function buildApiDiscoveryRequests(message: string): AtlasAPIDiscoveryRequest[] {
  const trimmed = message.trim()
  if (!isUrl(trimmed)) return []
  return [
    {
      request_id: `api_discovery_${Date.now()}`,
      source_type: 'openapi_url',
      source_value: trimmed,
      intent: 'Discover API endpoints for Atlas provisioning.',
    },
  ]
}

export function buildPastedChipLabel(chunk: { id: number; content: string }): string {
  const firstLine = chunk.content.trim().split(/\r?\n/, 1)[0] || 'Pasted content'
  return `${firstLine.slice(0, 42)} (${chunk.content.length.toLocaleString()} chars)`
}

export function attachTurnState(messages: DisplayMessage[], state: AtlasTurnResponse): DisplayMessage[] {
  if (!hasActionableAtlasResponse(state)) return messages.length > 0 ? messages : [greetingMessage()]
  const next = [...messages]
  for (let index = next.length - 1; index >= 0; index -= 1) {
    if (next[index].role === 'assistant') {
      next[index] = { ...next[index], turnResponse: state }
      return next
    }
  }
  return [
    ...next,
    {
      id: newDisplayMessageId('state'),
      role: 'assistant',
      content: state.message,
      timestamp: new Date(),
      turnResponse: state,
    },
  ]
}
