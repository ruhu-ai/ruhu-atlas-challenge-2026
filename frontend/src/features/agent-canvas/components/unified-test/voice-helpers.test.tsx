import { act, render } from '@testing-library/react'
import { RoomEvent } from 'livekit-client'

const mockUseRoomContext = jest.fn()

jest.mock('@livekit/components-react', () => ({
  useRoomContext: () => mockUseRoomContext(),
}))

jest.mock('sonner', () => ({
  toast: {
    error: jest.fn(),
  },
}))

import { VoiceDataHandler } from './voice-helpers'

describe('VoiceDataHandler', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('treats any non-local active speaker as the agent speaker', () => {
    const handlers = new Map<string, (...args: any[]) => void>()
    const fakeRoom = {
      localParticipant: {
        sid: 'local-sid',
        identity: 'web_widget:conv-1:local',
      },
      on: jest.fn((event: string, handler: (...args: any[]) => void) => {
        handlers.set(event, handler)
      }),
      off: jest.fn(),
    }
    mockUseRoomContext.mockReturnValue(fakeRoom)

    const onAgentStateChange = jest.fn()

    render(
      <VoiceDataHandler
        onAgentStateChange={onAgentStateChange}
        onUserSpeaking={jest.fn()}
        onTranscript={jest.fn()}
      />,
    )

    const activeSpeakersHandler = handlers.get(RoomEvent.ActiveSpeakersChanged)
    expect(activeSpeakersHandler).toBeDefined()

    act(() => {
      activeSpeakersHandler?.([
        {
          sid: 'remote-sid',
          identity: 'web_widget:conv-1:remote',
        },
      ])
    })

    expect(onAgentStateChange).toHaveBeenCalledWith('speaking')
  })
})
