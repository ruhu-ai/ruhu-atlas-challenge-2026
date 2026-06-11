/**
 * ReasoningTimelinePane contract.
 *
 * The right-side pane of the canvas Test surface. Pins:
 *   1. Empty-state copy when no conversation has been started yet
 *      (conversationId = null). This is the first thing the user sees on
 *      Test mode entry — wrong copy here is wrong first-impression UX.
 *   2. The pane fetches `/conversations/{id}/traces` and renders the
 *      timeline once traces arrive.
 *   3. Header shows the turn count once traces are present.
 */
import { render, screen, waitFor } from '@testing-library/react'

import { ReasoningTimelinePane } from '@/features/agent-canvas/components/ReasoningTimelinePane'

const mockGet = jest.fn()

jest.mock('@/api/client', () => ({
  apiClient: {
    get: (...args: unknown[]) => mockGet(...args),
  },
}))

describe('ReasoningTimelinePane', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('renders the start-a-conversation empty state when conversationId is null', () => {
    render(<ReasoningTimelinePane conversationId={null} />)
    expect(
      screen.getByText(/Send your agent a message in the chat/i),
    ).toBeInTheDocument()
    // No fetch should have been issued yet.
    expect(mockGet).not.toHaveBeenCalled()
  })

  it('fetches and renders traces when given a conversationId', async () => {
    mockGet.mockResolvedValueOnce([
      {
        trace_id: 't1',
        conversation_id: 'conv-1',
        turn_id: 'turn-1',
        step_before: 'discover',
        step_after: 'product_qa',
        emitted_messages: [],
        chosen_action: { type: 'run_tool', reason: 'product_question_asked' },
        guard_results: [],
        tool_calls: [{ tool_ref: 'knowledge.lookup', status: 'success' }],
        latency_breakdown_ms: { 'tool:knowledge.lookup': 180 },
        recorded_at: '2026-05-09T10:00:00.000Z',
      },
    ])

    render(<ReasoningTimelinePane conversationId="conv-1" />)

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith(
        '/conversations/conv-1/traces',
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      )
    })
    await waitFor(() => {
      expect(screen.getByText('product_qa')).toBeInTheDocument()
    })
    expect(screen.getByText(/Chose action: run_tool/)).toBeInTheDocument()
    expect(screen.getByText('knowledge.lookup')).toBeInTheDocument()
    // Header shows the turn count once traces arrive.
    expect(screen.getByText(/· 1 turn/)).toBeInTheDocument()
  })

  it('surfaces fetch errors as a visible inline error', async () => {
    mockGet.mockRejectedValueOnce(new Error('network down'))

    render(<ReasoningTimelinePane conversationId="conv-1" />)

    await waitFor(() => {
      expect(screen.getByText(/Couldn't load reasoning/i)).toBeInTheDocument()
      expect(screen.getByText(/network down/)).toBeInTheDocument()
    })
  })
})
