import {
  getVoiceDisconnectDecision,
  type VoiceSessionSnapshot,
} from '../voiceSessionLifecycle'

describe('getVoiceDisconnectDecision', () => {
  const activeSession: VoiceSessionSnapshot = {
    id: 'voice-session-1',
    roomName: 'voice-room-1',
    createdAtMs: 1_000,
  }

  it('handles the active session disconnect after the grace window', () => {
    expect(getVoiceDisconnectDecision({
      activeSession,
      disconnectedSession: activeSession,
      handledSessionId: null,
      nowMs: 3_500,
    })).toBe('handle')
  })

  it('ignores a duplicate disconnect for the same session', () => {
    expect(getVoiceDisconnectDecision({
      activeSession,
      disconnectedSession: activeSession,
      handledSessionId: activeSession.id,
      nowMs: 3_500,
    })).toBe('ignore_duplicate')
  })

  it('ignores an early disconnect inside the strict-mode grace window', () => {
    expect(getVoiceDisconnectDecision({
      activeSession,
      disconnectedSession: activeSession,
      handledSessionId: null,
      nowMs: 2_500,
    })).toBe('ignore_early_disconnect')
  })

  it('ignores a stale disconnect from a previously ended room', () => {
    expect(getVoiceDisconnectDecision({
      activeSession: {
        id: 'voice-session-2',
        roomName: 'voice-room-2',
        createdAtMs: 5_000,
      },
      disconnectedSession: activeSession,
      handledSessionId: null,
      nowMs: 7_500,
    })).toBe('ignore_stale_disconnect')
  })
})
