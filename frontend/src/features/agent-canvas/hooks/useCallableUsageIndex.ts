import { useMemo } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'

import { agentDefinitionService } from '@/api/services/agent-definition.service'
import type { AgentDocument } from '@/types/agent-document'
import type { AgentSummary } from '@/types/agent-definition'

// ────────────────────────────────────────────────────────────────────────────
// Callable usage index — scans every agent document in the org for step
// `tool_policy` entries and returns a ref -> list of step references map.
//
// Client-side scan is a phase-1 compromise: fine for <20 agents, painful at
// scale. Replace with a backend `GET /api/callables/:ref/usage` endpoint
// once any single org crosses that threshold.
// ────────────────────────────────────────────────────────────────────────────

export interface CallableUsageRef {
  agentId: string
  agentName: string
  scenarioId: string
  scenarioName: string
  stepId: string
  stepName: string
  mode?: string
  /**
   * Args mapped on the calling binding (Decision 6). Keys are the callable's
   * input parameter names; values are either literals or `$facts.<name>`
   * tokens the runtime substitutes at call time. Surfaced here so the
   * Library detail panel can do a cross-ref view.
   */
  args?: Record<string, unknown>
}

export interface CallableUsageIndex {
  index: Map<string, CallableUsageRef[]>
  isLoading: boolean
  isError: boolean
  agentCount: number
  loadedCount: number
}

export function useCallableUsageIndex(): CallableUsageIndex {
  const agentsQuery = useQuery({
    queryKey: ['callable-usage-agents'],
    queryFn: () => agentDefinitionService.listAgents(),
    staleTime: 60_000,
  })

  const agents: AgentSummary[] = Array.isArray(agentsQuery.data) ? agentsQuery.data : []

  const docQueries = useQueries({
    queries: agents.map((agent) => ({
      queryKey: ['agent-document', agent.id],
      queryFn: async (): Promise<AgentDocument | null> =>
        (await agentDefinitionService.getAgentDocument(agent.id)) ?? null,
      staleTime: 60_000,
      enabled: !!agent.id,
    })),
  })

  const index = useMemo(() => {
    const map = new Map<string, CallableUsageRef[]>()
    docQueries.forEach((query, agentIndex) => {
      if (!query.data) return
      const agent = agents[agentIndex]
      if (!agent) return
      for (const scenario of query.data.scenarios) {
        for (const step of scenario.steps) {
          for (const binding of step.tool_policy ?? []) {
            if (!binding.ref) continue
            const existing = map.get(binding.ref)
            const entry: CallableUsageRef = {
              agentId: agent.id,
              agentName: agent.name,
              scenarioId: scenario.id,
              scenarioName: scenario.name,
              stepId: step.id,
              stepName: step.name,
              mode: binding.mode,
              args: binding.args,
            }
            if (existing) existing.push(entry)
            else map.set(binding.ref, [entry])
          }
        }
      }
    })
    return map
    // docQueries array identity changes every render — key on loaded counts instead.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agents, docQueries.map((q) => q.dataUpdatedAt).join(':')])

  const loadedCount = docQueries.filter((q) => q.data !== undefined).length
  const isLoading = agentsQuery.isLoading || docQueries.some((q) => q.isLoading)
  const isError = agentsQuery.isError || docQueries.some((q) => q.isError)

  return {
    index,
    isLoading,
    isError,
    agentCount: agents.length,
    loadedCount,
  }
}
