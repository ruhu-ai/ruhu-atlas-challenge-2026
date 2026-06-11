import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { toolService } from '@/api/services/tools.service'
import { ActionConfigEditor, DEFAULT_ACTION_CONFIG } from './ActionConfigEditor'

jest.mock('@/api/services/tools.service', () => ({
  toolService: {
    getCallableCatalog: jest.fn(),
    testActionConfig: jest.fn(),
  },
}))

const getCallableCatalog = toolService.getCallableCatalog as jest.Mock
const testActionConfig = toolService.testActionConfig as jest.Mock

function renderEditor(agentId: string | null) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <ActionConfigEditor
        config={DEFAULT_ACTION_CONFIG}
        onChange={() => {}}
        agentId={agentId}
        stateId="answer_product"
        factSchema={['_last_user_text']}
      />
    </QueryClientProvider>,
  )
}

describe('ActionConfigEditor', () => {
  beforeEach(() => {
    getCallableCatalog.mockReset()
    testActionConfig.mockReset()
  })

  it('shows a save-first hint before the agent exists', () => {
    renderEditor(null)

    fireEvent.click(screen.getByRole('button', { name: 'Tools' }))

    expect(screen.getByText('Save the agent first to browse callable tools.')).toBeInTheDocument()
  })

  it('shows builtin tool callable aliases and the underlying tool ref', async () => {
    getCallableCatalog.mockResolvedValue({
      apis: [],
      integrations: [],
      builtin: [
        {
          tool_definition_id: 'builtin:knowledge.lookup',
          kind: 'builtin',
          ref: 'knowledge.lookup',
          function_name: 'lookup',
          callable_name: 'knowledge_lookup',
          display_name: 'Knowledge Lookup',
          description: 'Search the knowledge layer.',
          input_schema: {},
          read_only: true,
          provider_slug: 'builtin',
          connection_status: 'ready',
        },
      ],
    })

    renderEditor('agent_sales')
    fireEvent.click(screen.getByRole('button', { name: 'Tools' }))

    await waitFor(() => {
      expect(screen.getByText('Call as')).toBeInTheDocument()
    })
    expect(screen.getByText('knowledge_lookup(...)')).toBeInTheDocument()
    expect(screen.getByText('knowledge.lookup')).toBeInTheDocument()
  })
})
