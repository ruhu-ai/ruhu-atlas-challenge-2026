import { render, screen, waitFor } from '@testing-library/react'

const mockGet = jest.fn()

jest.mock('@/api/client', () => ({
  apiClient: {
    get: (...args: unknown[]) => mockGet(...args),
  },
}))

describe('InteractionDebugPanel', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockGet.mockImplementation(async (endpoint: string) => {
      if (endpoint === '/conversations/conv-1') {
        return {
          conversation_id: 'conv-1',
          step_id: 'current_state',
          channel: 'web_chat',
          facts: { email: 'ada@example.com' },
          control_state: {
            grounding: {
              acknowledged_fact_keys: ['email'],
            },
          },
        }
      }
      if (endpoint === '/conversations/conv-1/realtime-events') {
        return [
          {
            family: 'narration',
            name: 'narration_rendered',
            created_at: '2026-04-16T10:00:00Z',
            payload: {
              response_mode: 'activity_completed',
              claimed_class: 'success',
              narrator_mode: 'llm',
              fallback_used: false,
              interaction_debug_snapshot: {
                step_id: 'historic_state',
                voice_interaction_policy: {
                  step_id: 'historic_state',
                  endpointing_ms: 500,
                  soft_timeout_ms: 700,
                  turn_eagerness: 'high',
                  interruptibility_policy: 'interruptible_except_policy',
                },
              },
            },
          },
        ]
      }
      if (endpoint === '/conversations/conv-1/traces') {
        // Reasoning timeline pulls from this endpoint. Empty array is fine
        // for this test — the timeline section just shows its empty state.
        return []
      }
      throw new Error(`unexpected endpoint ${endpoint}`)
    })
  })

  it('renders the recent timeline using historical interaction debug snapshots from events', async () => {
    const { InteractionDebugPanel } = await import('./InteractionDebugPanel')

    render(<InteractionDebugPanel conversationId="conv-1" defaultOpen />)

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith('/conversations/conv-1', expect.objectContaining({
        signal: expect.any(AbortSignal),
      }))
      expect(mockGet).toHaveBeenCalledWith('/conversations/conv-1/realtime-events', expect.objectContaining({
        signal: expect.any(AbortSignal),
      }))
      expect(screen.getByText(/claim:success · mode:llm/)).toBeInTheDocument()
    })

    expect(screen.getByText('Recent interaction timeline')).toBeInTheDocument()
    expect(screen.getByText('narration.narration_rendered')).toBeInTheDocument()
    expect(screen.getByText(/state:historic_state/)).toBeInTheDocument()
    expect(screen.getByText(/voice: 500ms endpoint · high · interruptible_except_policy/)).toBeInTheDocument()
    expect(screen.getByText(/narration: claim:success · mode:llm/)).toBeInTheDocument()
  })
})
