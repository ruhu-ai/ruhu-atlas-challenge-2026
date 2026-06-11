import type { DisconnectReason } from 'livekit-client'
import type { VoiceSessionSnapshot } from '@/features/agent-canvas/utils/voiceSessionLifecycle'

export interface UnifiedTestInterfaceProps {
  agentId: string
  agentName: string
  agentType: 'chat' | 'voice' | 'multimodal'
  agentStatus: string
  canvasVersionId?: string
  /** Fires whenever the active conversation id changes (session start, end,
   * or restart). Used by hosts that render adjacent surfaces over the same
   * conversation — e.g. the canvas-level Test surface's ReasoningTimelinePane.
   * Receives null when no active conversation. */
  onConversationIdChange?: (conversationId: string | null) => void
}

export interface TranscriptEntry {
  id: string
  text: string
  speaker: 'user' | 'agent'
  timestamp: Date
  source: 'chat' | 'voice'
  attachments?: PendingAttachment[]
  /** True while the agent message is being revealed word-by-word */
  isStreaming?: boolean
  /** True while a user voice segment is still partial (not yet finalised by STT) */
  isPartial?: boolean
}

export interface PublicConversationEvent {
  event_id: string
  family: string
  name: string
  conversation_sequence: number
  payload?: Record<string, unknown>
  created_at: string
}

export interface PendingAttachment {
  attachmentId: string
  filename: string
  mimeType: string
  sizeBytes: number
}

export interface PendingToolInvocation {
  invocation_id: string
  tool_ref: string
  status: string
  reason?: string
  decision_reason?: string | null
  error?: string | null
  metadata?: Record<string, unknown>
}

export interface ActiveVoiceSession extends VoiceSessionSnapshot {
  serverUrl: string
  token: string
  conversationId: string
}

export interface VoiceTranscriptEvent {
  id?: string
  text: string
  speaker: 'user' | 'agent'
  isFinal?: boolean
  segmentKey?: string
}

export interface HandleEndCallOptions {
  endedSession?: ActiveVoiceSession | null
  disconnectReason?: DisconnectReason
  initiatedByUser?: boolean
}

export type WidgetSessionTarget = 'draft' | 'published'

export type VoiceRoomConnectionState =
  | 'idle'
  | 'connecting'
  | 'live'
  | 'reconnecting'
  | 'recoverable_disconnect'

declare global {
  interface Window {
    __RUHU_E2E_MOCK_VOICE__?: {
      enabled?: boolean
      onRoomMounted?: (payload: { sessionId: string; roomName: string }) => void
      onSendText?: (payload: { sessionId: string; roomName: string; text: string; topic?: string }) => void
    }
  }
}
