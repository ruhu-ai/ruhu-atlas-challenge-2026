import { useQuery } from '@tanstack/react-query'
import { testingService, type TestCase } from '@/api/services/testing.service'
import { agentService, type AgentInsightRecommendation } from '@/api/services/agent.service'

export interface ScenarioEditorData {
  testCases: TestCase[]
  simulationDashboard: Awaited<ReturnType<typeof testingService.getSimulationDashboard>> | undefined
  recommendations: AgentInsightRecommendation[]
}

export function useScenarioEditorData(agentId: string | undefined): ScenarioEditorData {
  const { data: testCases = [] } = useQuery({
    queryKey: ['test-cases', agentId],
    queryFn: () => testingService.getTestCases({ agent_id: agentId, limit: 250 }),
    enabled: !!agentId,
    staleTime: 15_000,
  })

  const { data: simulationDashboard } = useQuery({
    queryKey: ['simulation-dashboard', agentId],
    queryFn: () => testingService.getSimulationDashboard(agentId!),
    enabled: !!agentId,
    staleTime: 15_000,
  })

  const { data: insightRecommendationsResponse } = useQuery({
    queryKey: ['agent-insight-recommendations', agentId],
    queryFn: () => agentService.getInsightRecommendations(agentId!),
    enabled: !!agentId,
    staleTime: 15_000,
  })

  return {
    testCases,
    simulationDashboard,
    recommendations: (insightRecommendationsResponse?.recommendations || []) as AgentInsightRecommendation[],
  }
}
