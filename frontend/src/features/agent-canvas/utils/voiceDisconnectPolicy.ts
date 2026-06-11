import { DisconnectReason } from 'livekit-client'

export type VoiceDisconnectKind = 'local' | 'remote' | 'transient'
export type VoiceDisconnectReasonCode = DisconnectReason | undefined

export interface VoiceDisconnectPolicy {
  kind: VoiceDisconnectKind
  shouldEndSession: boolean
  allowRetry: boolean
  apiReason: string | null
  userMessage: string | null
}

export function getVoiceDisconnectPolicy(
  reason?: VoiceDisconnectReasonCode,
  initiatedByUser = false,
): VoiceDisconnectPolicy {
  if (initiatedByUser || reason === DisconnectReason.CLIENT_INITIATED) {
    return {
      kind: 'local',
      shouldEndSession: true,
      allowRetry: false,
      apiReason: 'user_hangup',
      userMessage: null,
    }
  }

  switch (reason) {
    case DisconnectReason.ROOM_DELETED:
      return {
        kind: 'remote',
        shouldEndSession: true,
        allowRetry: false,
        apiReason: 'room_deleted',
        userMessage: 'The voice room was closed.',
      }
    case DisconnectReason.ROOM_CLOSED:
      return {
        kind: 'remote',
        shouldEndSession: true,
        allowRetry: false,
        apiReason: 'room_closed',
        userMessage: 'The voice call ended because the room closed.',
      }
    case DisconnectReason.PARTICIPANT_REMOVED:
      return {
        kind: 'remote',
        shouldEndSession: true,
        allowRetry: false,
        apiReason: 'participant_removed',
        userMessage: 'You were removed from the voice room.',
      }
    case DisconnectReason.DUPLICATE_IDENTITY:
      return {
        kind: 'remote',
        shouldEndSession: true,
        allowRetry: false,
        apiReason: 'duplicate_identity',
        userMessage: 'This voice session was replaced by another connection.',
      }
    case DisconnectReason.SERVER_SHUTDOWN:
      return {
        kind: 'remote',
        shouldEndSession: true,
        allowRetry: false,
        apiReason: 'server_shutdown',
        userMessage: 'The voice service shut down the room.',
      }
    case DisconnectReason.USER_UNAVAILABLE:
      return {
        kind: 'remote',
        shouldEndSession: true,
        allowRetry: false,
        apiReason: 'user_unavailable',
        userMessage: 'The remote participant was unavailable.',
      }
    case DisconnectReason.USER_REJECTED:
      return {
        kind: 'remote',
        shouldEndSession: true,
        allowRetry: false,
        apiReason: 'user_rejected',
        userMessage: 'The remote participant rejected the call.',
      }
    case DisconnectReason.SIP_TRUNK_FAILURE:
      return {
        kind: 'remote',
        shouldEndSession: true,
        allowRetry: false,
        apiReason: 'sip_trunk_failure',
        userMessage: 'The call ended because the voice transport failed.',
      }
    case DisconnectReason.SIGNAL_CLOSE:
    case DisconnectReason.JOIN_FAILURE:
    case DisconnectReason.STATE_MISMATCH:
    case DisconnectReason.MIGRATION:
    case DisconnectReason.CONNECTION_TIMEOUT:
    case DisconnectReason.MEDIA_FAILURE:
    case DisconnectReason.UNKNOWN_REASON:
    default:
      return {
        kind: 'transient',
        shouldEndSession: false,
        allowRetry: true,
        apiReason: null,
        userMessage: 'The call disconnected. Reconnect to continue the same voice session.',
      }
  }
}
