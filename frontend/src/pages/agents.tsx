import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { DashboardLayout } from '@/layouts/dashboard-layout'
import { Button } from '@/components/atoms/button'
import { Input } from '@/components/atoms/input'
import { Card, CardContent } from '@/components/atoms/card'
import { Badge } from '@/components/atoms/badge'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/atoms/alert-dialog'
import { agentDefinitionService } from '@/api/services/agent-definition.service'
import { formatDate } from '@/lib/utils'
import { AgentTypeSelector } from '@/components/molecules/AgentTypeSelector'
import type { AgentType, AgentSummary } from '@/types/agent-definition'
import {
  Plus,
  Search,
  MessageSquare,
  Mic,
  Layers,
  Network,
  Rocket,
  ClipboardCheck,
  Trash2,
  LayoutTemplate,
  Tag,
} from 'lucide-react'

type AgentFilter = 'all' | 'live' | 'draft'

function agentTypeIcon(type: AgentSummary['agent_type']) {
  if (type === 'chat') return MessageSquare
  if (type === 'multimodal') return Layers
  return Mic
}

function matchesFilter(agent: AgentSummary, filter: AgentFilter): boolean {
  if (filter === 'all') return true
  if (filter === 'live') return agent.has_published_version
  return agent.has_draft_version
}

function hasDraftChanges(agent: AgentSummary): boolean {
  return agent.has_unpublished_changes
}

export default function AgentsPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [searchQuery, setSearchQuery] = useState('')
  const [filter, setFilter] = useState<AgentFilter>('all')
  const [showTypeSelector, setShowTypeSelector] = useState(false)
  const [deletingAgentId, setDeletingAgentId] = useState<string | null>(null)

  const agentsQuery = useQuery({
    queryKey: ['agents-list'],
    queryFn: () => agentDefinitionService.listAgents(),
    staleTime: 15_000,
  })

  const deleteMutation = useMutation({
    mutationFn: (agentId: string) => agentDefinitionService.deleteAgent(agentId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agents-list'] })
      toast.success('Agent deleted')
    },
    onError: (error: Error) => {
      toast.error(`Delete failed: ${error.message}`)
    },
    onSettled: () => setDeletingAgentId(null),
  })

  const filteredAgents = useMemo(() => {
    const items = agentsQuery.data || []
    const query = searchQuery.trim().toLowerCase()
    return items.filter((agent) => {
      const matchesSearch =
        query.length === 0 ||
        agent.name.toLowerCase().includes(query) ||
        agent.description.toLowerCase().includes(query) ||
        agent.id.toLowerCase().includes(query)
      return matchesSearch && matchesFilter(agent, filter)
    })
  }, [filter, agentsQuery.data, searchQuery])

  const handleCreateAgent = (type: AgentType, documentIds?: string[]) => {
    navigate(
      `/agents/new/canvas?type=${type}`,
      documentIds && documentIds.length > 0 ? { state: { initialKbIds: documentIds } } : undefined,
    )
  }

  return (
    <DashboardLayout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold">Agents</h1>
            <p className="mt-1 text-muted-foreground">
              Agent definitions authored directly on the runtime contract.
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => navigate('/templates')}>
              <LayoutTemplate className="mr-2 h-4 w-4" />
              Browse Templates
            </Button>
            <Button onClick={() => setShowTypeSelector(true)}>
              <Plus className="mr-2 h-4 w-4" />
              Create Agent
            </Button>
          </div>
        </div>

        <Card>
          <CardContent className="p-4">
            <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
              <div className="relative flex-1 md:max-w-md">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  type="search"
                  placeholder="Search agents..."
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  className="pl-10"
                />
              </div>
              <div className="flex gap-2">
                <Button variant={filter === 'all' ? 'primary' : 'outline'} size="sm" onClick={() => setFilter('all')}>
                  All
                </Button>
                <Button variant={filter === 'live' ? 'primary' : 'outline'} size="sm" onClick={() => setFilter('live')}>
                  Live
                </Button>
                <Button variant={filter === 'draft' ? 'primary' : 'outline'} size="sm" onClick={() => setFilter('draft')}>
                  Draft
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>

        {agentsQuery.isLoading ? (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, index) => (
              <Card key={index} className="overflow-hidden">
                <CardContent className="space-y-4 p-6">
                  <div className="flex items-start justify-between">
                    <div className="flex-1 space-y-2">
                      <div className="h-5 w-36 animate-pulse rounded bg-muted" />
                      <div className="h-4 w-52 animate-pulse rounded bg-muted" />
                    </div>
                    <div className="h-6 w-14 animate-pulse rounded-full bg-muted" />
                  </div>
                  <div className="grid gap-2">
                    <div className="h-4 w-full animate-pulse rounded bg-muted" />
                    <div className="h-4 w-40 animate-pulse rounded bg-muted" />
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        ) : filteredAgents.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center justify-center py-16">
              <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-muted">
                {searchQuery || filter !== 'all' ? (
                  <Search className="h-8 w-8 text-muted-foreground" />
                ) : (
                  <Network className="h-8 w-8 text-muted-foreground" />
                )}
              </div>
              <h3 className="mb-1 text-lg font-medium text-foreground">
                {searchQuery || filter !== 'all' ? 'No matching agents' : 'Create your first agent'}
              </h3>
              <p className="mb-5 max-w-sm text-center text-sm text-muted-foreground">
                {searchQuery || filter !== 'all'
                  ? "Try adjusting your search or filters to find the right definition."
                  : 'Create an agent definition for chat, voice, or multimodal support.'}
              </p>
              {!searchQuery && filter === 'all' && (
                <Button onClick={() => setShowTypeSelector(true)}>
                  <Plus className="mr-1.5 h-4 w-4" />
                  Create Agent
                </Button>
              )}
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {filteredAgents.map((agent) => {
              const TypeIcon = agentTypeIcon(agent.agent_type)
              return (
                <Card key={agent.id} className="group relative overflow-hidden transition-shadow hover:shadow-md">
                  <CardContent className="space-y-4 p-6">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <h3 className="truncate font-semibold">{agent.name}</h3>
                        <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">
                          {agent.description || 'No description'}
                        </p>
                      </div>
                      <Badge
                        variant="outline"
                        className="flex items-center gap-1 border-blue-500/30 text-blue-300"
                      >
                        <TypeIcon className="h-3 w-3" />
                        {agent.agent_type}
                      </Badge>
                    </div>

                    <div className="flex flex-wrap items-center gap-2">
                      {agent.has_published_version && (
                        <Badge variant="outline" className="border-emerald-500/30 text-emerald-300">
                          Live
                        </Badge>
                      )}
                      {hasDraftChanges(agent) && (
                        <Badge variant="outline" className="border-amber-500/30 text-amber-300">
                          Draft changes
                        </Badge>
                      )}
                      {!agent.has_published_version && agent.has_draft_version && (
                        <Badge variant="outline" className="border-amber-500/30 text-amber-300">
                          Draft only
                        </Badge>
                      )}
                    </div>

                    <div className="space-y-1 text-xs text-muted-foreground">
                      <div className="flex justify-between gap-2">
                        <span>Updated</span>
                        <span>{formatDate(agent.updated_at)}</span>
                      </div>
                      <div className="flex justify-between gap-2">
                        <span>Definition</span>
                        <span className="truncate">{agent.id}</span>
                      </div>
                      <div className="flex justify-between gap-2">
                        <span>States</span>
                        <span>{agent.step_count}</span>
                      </div>
                      <div className="flex justify-between gap-2">
                        <span>Knowledge docs</span>
                        <span>{agent.knowledge_base_count}</span>
                      </div>
                      <div className="flex justify-between gap-2">
                        <span>Model</span>
                        <span className="truncate">{agent.llm_model}</span>
                      </div>
                    </div>

                    <div className="flex gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        className="flex-1"
                        onClick={() => navigate(`/agents/${agent.id}/canvas`)}
                      >
                        <Network className="mr-2 h-3.5 w-3.5" />
                        Open
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => navigate(`/agents/${agent.id}/canvas?view=testing`)}
                      >
                        <ClipboardCheck className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => navigate(`/agents/${agent.id}/canvas?view=releases`)}
                      >
                        <Rocket className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => navigate(`/agents/${agent.id}/analysis`)}
                        title="Analysis schema"
                      >
                        <Tag className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                        onClick={(e) => { e.stopPropagation(); setDeletingAgentId(agent.id) }}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              )
            })}
          </div>
        )}

        <AgentTypeSelector
          open={showTypeSelector}
          onOpenChange={setShowTypeSelector}
          onSelect={handleCreateAgent}
        />
      </div>

      <AlertDialog open={!!deletingAgentId} onOpenChange={(open) => { if (!open) setDeletingAgentId(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete agent?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete the agent and all its versions. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => { if (deletingAgentId) deleteMutation.mutate(deletingAgentId) }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </DashboardLayout>
  )
}
