/**
 * Agent Analysis page contract:
 *   1. Loads the agent draft document on mount.
 *   2. Renders existing analysis_schema variables.
 *   3. Empty state when no variables exist.
 *   4. Add variable → variable form appears.
 *   5. Save → PUT /agent-document with the new schema (and a category validation).
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Routes, Route } from 'react-router-dom'

import AgentAnalysisPage from '../agent-analysis'

const mockGet = jest.fn()
const mockPut = jest.fn()

jest.mock('@/api/client', () => ({
  apiClient: {
    get: (...args: unknown[]) => mockGet(...args),
    put: (...args: unknown[]) => mockPut(...args),
  },
}))

jest.mock('@/layouts/dashboard-layout', () => ({
  DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/agents/agent-1/analysis']}>
        <Routes>
          <Route path="/agents/:id/analysis" element={children} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('AgentAnalysisPage', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('renders existing analysis variables from the draft document', async () => {
    mockGet.mockResolvedValue({
      agent_id: 'agent-1',
      target: 'draft',
      document: {
        version: '3.0',
        start_scenario_id: 'main',
        scenarios: [],
        analysis_schema: [
          { name: 'customer_intent', type: 'string', description: 'Why they called' },
        ],
      },
    })

    render(<AgentAnalysisPage />, { wrapper: makeWrapper() })

    await waitFor(() =>
      expect(screen.getByDisplayValue('customer_intent')).toBeInTheDocument(),
    )
    expect(screen.getByDisplayValue('Why they called')).toBeInTheDocument()
  })

  it('shows empty state and adds a new blank variable on click', async () => {
    mockGet.mockResolvedValue({
      agent_id: 'agent-1',
      target: 'draft',
      document: {
        version: '3.0',
        start_scenario_id: 'main',
        scenarios: [],
        analysis_schema: [],
      },
    })

    render(<AgentAnalysisPage />, { wrapper: makeWrapper() })

    expect(await screen.findByText(/No variables yet/i)).toBeInTheDocument()
    await userEvent.click(
      screen.getByRole('button', { name: /Add your first variable/i }),
    )
    // A blank "Name" input now appears.
    expect(screen.getByLabelText('Name')).toBeInTheDocument()
  })

  it('blocks save when a variable is missing a name and surfaces the error', async () => {
    mockGet.mockResolvedValue({
      agent_id: 'agent-1',
      target: 'draft',
      document: {
        version: '3.0',
        start_scenario_id: 'main',
        scenarios: [],
        analysis_schema: [],
      },
    })

    render(<AgentAnalysisPage />, { wrapper: makeWrapper() })

    await userEvent.click(
      await screen.findByRole('button', { name: /Add your first variable/i }),
    )
    // Don't fill the name — try to save.
    await userEvent.click(screen.getByRole('button', { name: /Save schema/i }))
    expect(await screen.findByText(/Every variable needs a name/i)).toBeInTheDocument()
    expect(mockPut).not.toHaveBeenCalled()
  })

  it('saves a valid schema via PUT /agent-document', async () => {
    mockGet.mockResolvedValue({
      agent_id: 'agent-1',
      target: 'draft',
      document: {
        version: '3.0',
        start_scenario_id: 'main',
        scenarios: [],
        analysis_schema: [],
      },
    })
    mockPut.mockResolvedValue({ ok: true })

    render(<AgentAnalysisPage />, { wrapper: makeWrapper() })

    await userEvent.click(
      await screen.findByRole('button', { name: /Add your first variable/i }),
    )
    await userEvent.type(screen.getByLabelText('Name'), 'topic')
    await userEvent.type(screen.getByLabelText('Description'), 'conversation topic')
    await userEvent.click(screen.getByRole('button', { name: /Save schema/i }))

    await waitFor(() =>
      expect(mockPut).toHaveBeenCalledWith(
        '/agents/agent-1/agent-document',
        expect.objectContaining({
          analysis_schema: [
            expect.objectContaining({ name: 'topic', type: 'string' }),
          ],
        }),
      ),
    )
    expect(await screen.findByText(/Analysis schema saved/i)).toBeInTheDocument()
  })
})
