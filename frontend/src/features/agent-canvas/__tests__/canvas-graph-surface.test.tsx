/**
 * Canvas Graph surface contract.
 *
 * This test pins the ElevenLabs-style flow graph as the canonical Graph
 * view. If you replace `AgentFlowGraph`, swap its node/edge styling away
 * from the rounded-2xl 280-wide cards, or unmount the AgentDocumentProvider
 * that feeds it — this test fails. That's intentional: it's the contract
 * that prevents the orphaning that already happened once.
 *
 * If you're refactoring and need to update the assertions, do so deliberately
 * and update the comment in AgentFlowGraph.tsx + AgentDocumentContext.tsx
 * along with it.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'

import { AgentDocumentProvider } from '@/features/agent-canvas/contexts/AgentDocumentContext'
import { AgentFlowGraph } from '@/features/agent-canvas/components/AgentFlowGraph'

const mockGetAgentDocument = jest.fn()

jest.mock('@/api/services/agent-definition.service', () => ({
  agentDefinitionService: {
    getAgentDocument: (...args: unknown[]) => mockGetAgentDocument(...args),
    updateAgentDocument: jest.fn(),
  },
}))

// dagre is CJS and breaks under ts-jest's default-import handling. We
// don't care about layout positions in this contract test — only that
// the nodes render — so a minimal stub is enough.
jest.mock('dagre', () => {
  class FakeGraph {
    private nodes = new Map<string, { width: number; height: number; x: number; y: number }>()
    setDefaultEdgeLabel() {}
    setGraph() {}
    setNode(id: string, attrs: { width: number; height: number }) {
      this.nodes.set(id, { ...attrs, x: 0, y: 0 })
    }
    setEdge() {}
    node(id: string) {
      return this.nodes.get(id)
    }
  }
  return {
    __esModule: true,
    default: {
      graphlib: { Graph: FakeGraph },
      layout: () => {},
    },
    graphlib: { Graph: FakeGraph },
    layout: () => {},
  }
})

const FAKE_DOCUMENT = {
  version: '3.0',
  start_scenario_id: 'main',
  scenarios: [
    {
      id: 'main',
      name: 'Main',
      start_step_id: 'entry',
      order: 0,
      entry_channels: [],
      resources: {},
      flow_layout: {},
      steps: [
        {
          id: 'entry',
          name: 'Welcome step',
          transitions: [
            {
              id: 't0',
              when: { kind: 'otherwise' },
              to_step_id: 'closeout',
              priority: 100,
            },
          ],
        },
        {
          id: 'closeout',
          name: 'Closeout step',
          completion: { disposition: 'resolved' },
          transitions: [],
        },
      ],
    },
  ],
  scenario_routes: [],
  fact_schema: [],
  agent_capability_manifest: null,
  metadata: {},
}

function renderGraph() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <AgentDocumentProvider agentId="agent-1">
          <AgentFlowGraph />
        </AgentDocumentProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe('canvas graph surface — ElevenLabs-style contract', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockGetAgentDocument.mockResolvedValue(FAKE_DOCUMENT)
  })

  it('renders step cards with the canonical 280px / rounded-2xl style', async () => {
    const { container } = renderGraph()

    // Step names land in the DOM once the document loads. Two steps —
    // both should render.
    await waitFor(() => {
      expect(screen.getByText('Welcome step')).toBeInTheDocument()
    })
    expect(screen.getByText('Closeout step')).toBeInTheDocument()

    // Visual contract: nodes use rounded-2xl + w-[280px]. If a future
    // refactor changes these classes, update the comment header in
    // AgentFlowGraph.tsx and this test together.
    const cards = container.querySelectorAll('.rounded-2xl.w-\\[280px\\]')
    expect(cards.length).toBeGreaterThanOrEqual(2)
  })

  it('renders the start step badge on the entry node', async () => {
    renderGraph()
    await waitFor(() => {
      expect(screen.getByText('Welcome step')).toBeInTheDocument()
    })
    // The entry step gets a "Start" pill rendered by StepNode.
    expect(screen.getByText('Start')).toBeInTheDocument()
  })

  it('labels steps by mode (entry / completion / conversational)', async () => {
    renderGraph()
    await waitFor(() => {
      expect(screen.getByText('Welcome step')).toBeInTheDocument()
    })
    // Entry step's mode label.
    expect(screen.getByText('Entry')).toBeInTheDocument()
    // Closeout step has `completion` set → Completion mode.
    expect(screen.getByText('Completion')).toBeInTheDocument()
  })
})
