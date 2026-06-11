import { DisconnectReason } from 'livekit-client'
import {
  getVoiceDisconnectPolicy,
} from '../voiceDisconnectPolicy'

describe('getVoiceDisconnectPolicy', () => {
  it('treats explicit user hangup as a local terminal end', () => {
    expect(getVoiceDisconnectPolicy(undefined, true)).toEqual({
      kind: 'local',
      shouldEndSession: true,
      allowRetry: false,
      apiReason: 'user_hangup',
      userMessage: null,
    })
  })

  it('treats room deletion as a remote terminal end', () => {
    expect(getVoiceDisconnectPolicy(DisconnectReason.ROOM_DELETED)).toMatchObject({
      kind: 'remote',
      shouldEndSession: true,
      allowRetry: false,
      apiReason: 'room_deleted',
    })
  })

  it('treats signal close as recoverable', () => {
    expect(getVoiceDisconnectPolicy(DisconnectReason.SIGNAL_CLOSE)).toMatchObject({
      kind: 'transient',
      shouldEndSession: false,
      allowRetry: true,
      apiReason: null,
    })
  })
})
