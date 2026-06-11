import { render, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import { AgentEvaluationView } from './AgentEvaluationView'
import { agentDefinitionService } from '@/api/services/agent-definition.service'

jest.mock('@/api/services/agent-definition.service', () => ({
  agentDefinitionService: {
    getAgentEvaluationPolicy: jest.fn(),
    listSimulationFixtures: jest.fn(),
    listEvaluationRuns: jest.fn(),
    getLatestQualifiedRun: jest.fn(),
    getMetrics: jest.fn(),
    updateEvaluationPolicy: jest.fn(),
    createSimulationFixture: jest.fn(),
    createEvaluationRun: jest.fn(),
    stopEvaluationRun: jest.fn(),
    replay: jest.fn(),
  },
}))

const mockAgentDefinitionService = agentDefinitionService as jest.Mocked<typeof agentDefinitionService>

function renderWithQueryClient(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      {ui}
    </QueryClientProvider>,
  )
}

describe('AgentEvaluationView latest-qualified-run behavior', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockAgentDefinitionService.getAgentEvaluationPolicy.mockResolvedValue({
      agent_id: 'agent-1',
      policy: {
        minimum_pass_rate_ratio: 1,
        allow_warning_failures: true,
        max_qualified_run_age_hours: null,
      },
    })
    mockAgentDefinitionService.listSimulationFixtures.mockResolvedValue([])
    mockAgentDefinitionService.getMetrics.mockResolvedValue({
      agent_id: 'agent-1',
      agent_version_id: null,
      conversation_count: 0,
      trace_count: 0,
      avg_turns_per_conversation: 0,
      total_latency: { count: 0, average_ms: 0, p95_ms: 0, max_ms: 0 },
      state_entries: {},
      transition_counts: {},
      action_counts: {},
      tool_status_counts: {},
    })
  })

  it('does not fetch latest qualified run when no qualified runs exist', async () => {
    mockAgentDefinitionService.listEvaluationRuns.mockResolvedValue([
      {
        evaluation_run_id: 'run-1',
        agent_id: 'agent-1',
        agent_version_id: 'version-1',
        mode: 'manual_batch',
        source: 'studio',
        status: 'completed',
        gate_eligible: true,
        fixture_count: 1,
        passed_count: 1,
        failed_count: 0,
        skipped_count: 0,
        pass_rate_ratio: 1,
        qualified_at: null,
        results: [],
      },
    ])

    renderWithQueryClient(<AgentEvaluationView agentId="agent-1" agentName="Support Agent" />)

    await waitFor(() => expect(mockAgentDefinitionService.listEvaluationRuns).toHaveBeenCalled())
    expect(mockAgentDefinitionService.getLatestQualifiedRun).not.toHaveBeenCalled()
  })

  it('fetches latest qualified run when a qualified run exists', async () => {
    mockAgentDefinitionService.listEvaluationRuns.mockResolvedValue([
      {
        evaluation_run_id: 'run-2',
        agent_id: 'agent-1',
        agent_version_id: 'version-1',
        mode: 'manual_batch',
        source: 'studio',
        status: 'completed',
        gate_eligible: true,
        fixture_count: 1,
        passed_count: 1,
        failed_count: 0,
        skipped_count: 0,
        pass_rate_ratio: 1,
        qualified_at: '2026-04-11T00:00:00Z',
        results: [],
      },
    ])
    mockAgentDefinitionService.getLatestQualifiedRun.mockResolvedValue({
      evaluation_run_id: 'run-2',
      agent_id: 'agent-1',
      agent_version_id: 'version-1',
      mode: 'manual_batch',
      source: 'studio',
      status: 'completed',
      gate_eligible: true,
      fixture_count: 1,
      passed_count: 1,
      failed_count: 0,
      skipped_count: 0,
      pass_rate_ratio: 1,
      qualified_at: '2026-04-11T00:00:00Z',
      completed_at: '2026-04-11T00:00:00Z',
      results: [],
    })

    renderWithQueryClient(<AgentEvaluationView agentId="agent-1" agentName="Support Agent" />)

    await waitFor(() => expect(mockAgentDefinitionService.getLatestQualifiedRun).toHaveBeenCalledWith('agent-1'))
  })
})
