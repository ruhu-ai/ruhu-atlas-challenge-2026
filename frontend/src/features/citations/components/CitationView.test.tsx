/**
 * CitationView contract:
 *   1. Fetches citations on mount via citationsService.list.
 *   2. Renders each citation's name, value, source label, and source utterance.
 *   3. Clicking "Run analysis sweep" calls runAnalysisSweep then refetches.
 *   4. Empty state when no citations.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { CitationView } from './CitationView'

const mockList = jest.fn()
const mockSweep = jest.fn()

jest.mock('@/api/services/citations.service', () => ({
  citationsService: {
    list: (id: string) => mockList(id),
    runAnalysisSweep: (id: string) => mockSweep(id),
  },
}))

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
}

describe('CitationView', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('renders citations with grounded source utterances', async () => {
    mockList.mockResolvedValue({
      conversation_id: 'conv-1',
      citations: [
        {
          fact_name: 'email',
          value: 'jane@example.com',
          raw_value: 'jane@example.com',
          confidence: 1.0,
          source: 'deterministic',
          turn_id: 'turn-1',
          step_id: 'collect_contact',
          transcript_span: [10, 26],
          source_utterance: 'jane@example.com',
          source_ref: null,
          evidence: 'jane@example.com',
          replaced_previous: false,
        },
      ],
    })

    render(<CitationView conversationId="conv-1" />, { wrapper: makeWrapper() })

    await waitFor(() => expect(mockList).toHaveBeenCalledWith('conv-1'))
    expect(await screen.findByText('email')).toBeInTheDocument()
    expect(screen.getByText('jane@example.com')).toBeInTheDocument()
    // Source utterance rendered inside a blockquote with quotes.
    expect(screen.getByText('"jane@example.com"')).toBeInTheDocument()
    expect(screen.getByText('pattern match')).toBeInTheDocument()
    expect(screen.getByText('100%')).toBeInTheDocument()
  })

  it('shows empty state when no citations exist', async () => {
    mockList.mockResolvedValue({
      conversation_id: 'conv-2',
      citations: [],
    })

    render(<CitationView conversationId="conv-2" />, { wrapper: makeWrapper() })

    expect(await screen.findByText(/No citations yet/i)).toBeInTheDocument()
  })

  it('triggers analysis sweep and refetches citations', async () => {
    mockList.mockResolvedValueOnce({ conversation_id: 'conv-3', citations: [] })
    mockSweep.mockResolvedValue({
      conversation_id: 'conv-3',
      variables_total: 2,
      variables_filled: ['email', 'topic'],
      variables_skipped_existing: [],
      variables_unfilled: [],
    })
    mockList.mockResolvedValueOnce({
      conversation_id: 'conv-3',
      citations: [
        {
          fact_name: 'email',
          value: 'a@b.com',
          raw_value: 'a@b.com',
          confidence: 0.9,
          source: 'llm_proposed',
          turn_id: 't',
          step_id: null,
          transcript_span: null,
          source_utterance: 'a@b.com',
          source_ref: null,
          evidence: 'a@b.com',
          replaced_previous: false,
        },
      ],
    })

    render(<CitationView conversationId="conv-3" />, { wrapper: makeWrapper() })

    const sweepButton = await screen.findByRole('button', { name: /Run analysis sweep/i })
    await userEvent.click(sweepButton)

    await waitFor(() => expect(mockSweep).toHaveBeenCalledWith('conv-3'))
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2))
    expect(
      await screen.findByText(/Sweep filled/i),
    ).toBeInTheDocument()
  })

  it('shows a placeholder when no conversation is selected', () => {
    render(<CitationView conversationId={null} />, { wrapper: makeWrapper() })
    expect(screen.getByText(/Select a conversation/i)).toBeInTheDocument()
    expect(mockList).not.toHaveBeenCalled()
  })
})
