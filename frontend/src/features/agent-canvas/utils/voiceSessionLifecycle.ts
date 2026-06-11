export interface VoiceSessionSnapshot {
  id: string
  roomName: string
  createdAtMs: number
}

export interface VoiceDisconnectDecisionInput {
  activeSession: VoiceSessionSnapshot | null
  disconnectedSession: VoiceSessionSnapshot
  handledSessionId: string | null
  nowMs: number
  sessionEverConnected?: boolean
  strictModeGraceMs?: number
}

export type VoiceDisconnectDecision =
  | 'handle'
  | 'ignore_duplicate'
  | 'ignore_early_disconnect'
  | 'ignore_stale_disconnect'

export function getVoiceDisconnectDecision({
  activeSession,
  disconnectedSession,
  handledSessionId,
  nowMs,
  sessionEverConnected = false,
  strictModeGraceMs = 2000,
}: VoiceDisconnectDecisionInput): VoiceDisconnectDecision {
  if (handledSessionId === disconnectedSession.id) {
    return 'ignore_duplicate'
  }

  if (!sessionEverConnected && nowMs - disconnectedSession.createdAtMs < strictModeGraceMs) {
    return 'ignore_early_disconnect'
  }

  if (
    activeSession
    && (
      activeSession.id !== disconnectedSession.id
      || activeSession.roomName !== disconnectedSession.roomName
    )
  ) {
    return 'ignore_stale_disconnect'
  }

  return 'handle'
}
