import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { DashboardLayout } from '@/layouts/dashboard-layout'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/atoms/card'
import { Label } from '@/components/atoms/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/atoms/select'
import { agentDefinitionService } from '@/api/services/agent-definition.service'
import { AgentRulesView } from '@/features/agent-canvas/agent-definition/AgentRulesView'
import type { SidebarStepItem } from '@/features/agent-canvas/agent-definition/AgentDefinitionWorkspace'
import { deriveStepKind } from '@/features/agent-canvas/agent-definition/utils'

export default function RulesPage() {
  const [selectedAgentId, setSelectedAgentId] = useState<string>('')

  const agentsQuery = useQuery({
    queryKey: ['rules-page-agents'],
    queryFn: () => agentDefinitionService.listAgents(),
    staleTime: 15_000,
  })

  useEffect(() => {
    if (selectedAgentId) return
    const firstAgentId = agentsQuery.data?.[0]?.id
    if (firstAgentId) {
      setSelectedAgentId(firstAgentId)
    }
  }, [agentsQuery.data, selectedAgentId])

  const agentDefinitionQuery = useQuery({
    queryKey: ['rules-page-agent-definition', selectedAgentId],
    queryFn: () => agentDefinitionService.getAgentDefinition(selectedAgentId, 'draft'),
    enabled: selectedAgentId.length > 0,
    staleTime: 5_000,
  })

  const agentName = agentDefinitionQuery.data?.definition.name || agentsQuery.data?.find((item) => item.id == selectedAgentId)?.name || selectedAgentId
  const steps: SidebarStepItem[] = useMemo(
    () =>
      (agentDefinitionQuery.data?.definition.steps || []).map((state) => ({
        id: state.id,
        name: state.name,
        kind: deriveStepKind(state, agentDefinitionQuery.data?.definition.start_step_id),
      })),
    [agentDefinitionQuery.data?.definition.steps],
  )
  const selectedStateId = agentDefinitionQuery.data?.definition.start_step_id || null

  return (
    <DashboardLayout>
      <div className="space-y-6">
        <Card>
          <CardHeader className="space-y-2">
            <CardTitle>Runtime Rules</CardTitle>
            <CardDescription>
              Manage typed rule definitions and bindings for each agent. This page uses only the `/api/rules/*`
              contract.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {agentsQuery.isLoading ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading agents...
              </div>
            ) : (agentsQuery.data || []).length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No agents found yet. Create an agent in Agent Canvas first, then attach rules.
              </p>
            ) : (
              <div className="space-y-3">
                <Label htmlFor="rules-agent-select">Agent</Label>
                <Select value={selectedAgentId} onValueChange={setSelectedAgentId}>
                  <SelectTrigger id="rules-agent-select" className="w-full md:max-w-xl">
                    <SelectValue placeholder="Select an agent" />
                  </SelectTrigger>
                  <SelectContent>
                    {(agentsQuery.data || []).map((agent) => (
                      <SelectItem key={agent.id} value={agent.id}>
                        {agent.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
          </CardContent>
        </Card>

        {selectedAgentId && !agentDefinitionQuery.isLoading ? (
          <AgentRulesView
            agentId={selectedAgentId}
            agentName={agentName}
            steps={steps}
            selectedStateId={selectedStateId}
          />
        ) : selectedAgentId ? (
          <Card>
            <CardContent className="pt-6">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading selected agent...
              </div>
            </CardContent>
          </Card>
        ) : null}
      </div>
    </DashboardLayout>
  )
}
