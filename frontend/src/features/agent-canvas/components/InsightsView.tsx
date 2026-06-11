import { useState, useMemo, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/atoms/card'
import { Input } from '@/components/atoms/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/atoms/table'
import {
  Loader2, RefreshCw, Sparkles, Wand2, Search,
  Phone, MessageSquare, Ticket, ChevronUp, ChevronDown, ChevronsUpDown,
  SmilePlus, Frown, Meh,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { AgentInsightRecommendation } from '@/api/services/agent.service'
import { analyticsService } from '@/api/services/analytics.service'
import type { Conversation } from '@/types/analytics'

type SortField = 'started_at' | 'duration_seconds' | 'sentiment_score' | 'outcome' | 'message_count'
type SortDir = 'asc' | 'desc'

interface InsightsViewProps {
  agentId: string
  recommendations: AgentInsightRecommendation[]
  loading: boolean
  creatingRecommendationId?: string | null
  onRefresh: () => void
  onCreateDraft: (recommendationId: string) => void
}

function severityBadgeClass(severity?: string | null): string {
  if (severity === 'critical' || severity === 'high') return 'border-red-500/30 text-red-300'
  if (severity === 'medium') return 'border-yellow-500/30 text-yellow-300'
  if (severity === 'low' || severity === 'info') return 'border-blue-500/30 text-blue-300'
  return 'border-border text-muted-foreground'
}

function SortIcon({ field, sortBy, sortDir }: { field: SortField; sortBy: SortField; sortDir: SortDir }) {
  if (field !== sortBy) return <ChevronsUpDown className="ml-1 h-3.5 w-3.5 text-muted-foreground/50" />
  return sortDir === 'asc'
    ? <ChevronUp className="ml-1 h-3.5 w-3.5" />
    : <ChevronDown className="ml-1 h-3.5 w-3.5" />
}

function SentimentCell({ score, endedAt }: { score: number | null; endedAt?: string | null }) {
  if (score === null) {
    if (endedAt) {
      const endedMs = new Date(endedAt).getTime()
      const twoMinAgo = Date.now() - 2 * 60 * 1000
      if (endedMs > twoMinAgo) {
        return <span className="text-xs text-muted-foreground italic">Analyzing...</span>
      }
    }
    return <span className="text-xs text-muted-foreground">—</span>
  }
  if (score > 0.3) {
    return (
      <span className="inline-flex items-center gap-1 text-sm font-medium text-emerald-600 dark:text-emerald-400">
        <SmilePlus className="h-4 w-4" />
        Happy
      </span>
    )
  }
  if (score < -0.3) {
    return (
      <span className="inline-flex items-center gap-1 text-sm font-medium text-red-600 dark:text-red-400">
        <Frown className="h-4 w-4" />
        Unhappy
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 text-sm font-medium text-amber-600 dark:text-amber-400">
      <Meh className="h-4 w-4" />
      Neutral
    </span>
  )
}

function ResolutionCell({ outcome }: { outcome: string | null }) {
  if (!outcome) return <span className="text-xs text-muted-foreground">—</span>
  const label = outcome.charAt(0).toUpperCase() + outcome.slice(1)
  const colorClass =
    outcome === 'resolved' ? 'text-emerald-600 dark:text-emerald-400'
    : outcome === 'transferred' ? 'text-blue-600 dark:text-blue-400'
    : outcome === 'escalated' ? 'text-amber-600 dark:text-amber-400'
    : outcome === 'abandoned' ? 'text-red-600 dark:text-red-400'
    : 'text-muted-foreground'
  const dotClass =
    outcome === 'resolved' ? 'bg-emerald-500'
    : outcome === 'transferred' ? 'bg-blue-500'
    : outcome === 'escalated' ? 'bg-amber-500'
    : outcome === 'abandoned' ? 'bg-red-500'
    : 'bg-muted-foreground'
  return (
    <span className={cn('inline-flex items-center gap-1.5 text-sm font-medium', colorClass)}>
      <span className={cn('h-1.5 w-1.5 rounded-full', dotClass)} />
      {label}
    </span>
  )
}

export function InsightsView({
  agentId,
  recommendations,
  loading,
  creatingRecommendationId,
  onRefresh,
  onCreateDraft,
}: InsightsViewProps) {
  const [ticketSearch, setTicketSearch] = useState('')
  const [channelFilter, setChannelFilter] = useState<'all' | 'voice' | 'chat'>('all')
  const [sortBy, setSortBy] = useState<SortField>('started_at')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  const handleSort = useCallback((field: SortField) => {
    setSortBy((prev) => {
      if (prev === field) {
        setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'))
        return field
      }
      setSortDir('desc')
      return field
    })
  }, [])

  // Fetch conversations — sort applied server-side
  const { data: conversations = [], isLoading: isLoadingConversations } = useQuery({
    queryKey: ['agent-conversations', agentId, channelFilter, sortBy, sortDir],
    queryFn: () =>
      analyticsService.listConversations({
        agent_id: agentId,
        channel: channelFilter !== 'all' ? channelFilter : undefined,
        sort_by: sortBy,
        sort_dir: sortDir,
        limit: 100,
      }),
    staleTime: 15_000,
  })

  // Client-side search filter
  const filteredConversations = useMemo(() => {
    if (!ticketSearch.trim()) return conversations
    const q = ticketSearch.toLowerCase()
    return conversations.filter((c: Conversation) =>
      c.outcome?.toLowerCase().includes(q) ||
      c.channel?.toLowerCase().includes(q) ||
      c.agent_name?.toLowerCase().includes(q) ||
      c.customer_id?.toLowerCase().includes(q) ||
      c.id.toLowerCase().includes(q)
    )
  }, [conversations, ticketSearch])

  const thClass = 'cursor-pointer select-none hover:text-foreground transition-colors'

  return (
    <div className="space-y-6">
      {/* Tickets Section */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-lg">Tickets</CardTitle>
              <CardDescription>Recent conversations handled by this agent.</CardDescription>
            </div>
            <span className="text-sm text-muted-foreground">
              {filteredConversations.length} ticket{filteredConversations.length !== 1 ? 's' : ''}
            </span>
          </div>
          <div className="flex items-center gap-3 pt-2">
            <div className="relative flex-1 max-w-sm">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Search tickets..."
                value={ticketSearch}
                onChange={(e) => setTicketSearch(e.target.value)}
                className="pl-9"
              />
            </div>
            <div className="flex gap-1">
              {(['all', 'voice', 'chat'] as const).map((ch) => (
                <Button
                  key={ch}
                  variant={channelFilter === ch ? 'primary' : 'outline'}
                  size="sm"
                  onClick={() => setChannelFilter(ch)}
                >
                  {ch === 'voice' && <Phone className="mr-1.5 h-3.5 w-3.5" />}
                  {ch === 'chat' && <MessageSquare className="mr-1.5 h-3.5 w-3.5" />}
                  {ch.charAt(0).toUpperCase() + ch.slice(1)}
                </Button>
              ))}
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {isLoadingConversations ? (
            <div className="flex flex-col items-center justify-center py-8">
              <Loader2 className="h-6 w-6 text-muted-foreground animate-spin mb-2" />
              <p className="text-sm text-muted-foreground">Loading conversations...</p>
            </div>
          ) : filteredConversations.length === 0 ? (
            <div className="rounded-md border border-dashed border-border p-8 text-center">
              <Ticket className="mx-auto mb-2 h-8 w-8 text-muted-foreground" />
              <p className="text-sm font-medium">No tickets found</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {ticketSearch || channelFilter !== 'all'
                  ? 'Try adjusting your search or filters.'
                  : 'Conversations will appear here as this agent interacts with users.'}
              </p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead
                    className={thClass}
                    onClick={() => handleSort('started_at')}
                  >
                    <span className="inline-flex items-center">
                      Created <SortIcon field="started_at" sortBy={sortBy} sortDir={sortDir} />
                    </span>
                  </TableHead>
                  <TableHead
                    className={thClass}
                    onClick={() => handleSort('sentiment_score')}
                  >
                    <span className="inline-flex items-center">
                      Sentiment <SortIcon field="sentiment_score" sortBy={sortBy} sortDir={sortDir} />
                    </span>
                  </TableHead>
                  <TableHead>Agent</TableHead>
                  <TableHead>From</TableHead>
                  <TableHead>Channel</TableHead>
                  <TableHead
                    className={thClass}
                    onClick={() => handleSort('outcome')}
                  >
                    <span className="inline-flex items-center">
                      Resolution <SortIcon field="outcome" sortBy={sortBy} sortDir={sortDir} />
                    </span>
                  </TableHead>
                  <TableHead
                    className={cn(thClass, 'text-right')}
                    onClick={() => handleSort('duration_seconds')}
                  >
                    <span className="inline-flex items-center justify-end w-full">
                      Duration <SortIcon field="duration_seconds" sortBy={sortBy} sortDir={sortDir} />
                    </span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredConversations.map((conv: Conversation) => {
                  const duration = conv.duration_seconds != null
                    ? conv.duration_seconds >= 60
                      ? `${Math.floor(conv.duration_seconds / 60)}m ${conv.duration_seconds % 60}s`
                      : `${conv.duration_seconds}s`
                    : '—'

                  const created = new Date(conv.started_at).toLocaleDateString(undefined, {
                    month: 'short',
                    day: 'numeric',
                    hour: '2-digit',
                    minute: '2-digit',
                  })

                  return (
                    <TableRow key={conv.id}>
                      <TableCell className="text-muted-foreground text-sm whitespace-nowrap">
                        {created}
                      </TableCell>
                      <TableCell>
                        <SentimentCell score={conv.sentiment_score} endedAt={conv.ended_at} />
                      </TableCell>
                      <TableCell className="text-sm text-foreground">
                        {conv.agent_name ?? <span className="text-muted-foreground">—</span>}
                      </TableCell>
                      <TableCell className="text-sm font-mono text-muted-foreground">
                        {conv.customer_id ?? '—'}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
                          {conv.channel === 'voice'
                            ? <Phone className="h-3.5 w-3.5" />
                            : <MessageSquare className="h-3.5 w-3.5" />}
                          {conv.channel.charAt(0).toUpperCase() + conv.channel.slice(1)}
                        </div>
                      </TableCell>
                      <TableCell>
                        <ResolutionCell outcome={conv.outcome} />
                      </TableCell>
                      <TableCell className="text-right text-sm text-muted-foreground tabular-nums">
                        {duration}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Insights to Changes */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Insights to changes</CardTitle>
          <CardDescription>
            Convert failing simulation insights into draft canvas changes.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="outline" onClick={onRefresh}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh recommendations
          </Button>
        </CardContent>
      </Card>

      {/* Recommendations */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Recommendations</CardTitle>
          <CardDescription>
            Create a draft from any recommendation. Drafts are never auto-published.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading recommendations...
            </div>
          ) : recommendations.length === 0 ? (
            <div className="rounded-md border border-dashed border-border p-8 text-center">
              <Sparkles className="mx-auto mb-2 h-8 w-8 text-muted-foreground" />
              <p className="text-sm font-medium">No recommendations yet</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Run simulations to generate insights and recommendations.
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {recommendations.map((item) => {
                const creating = creatingRecommendationId === item.recommendation_id
                return (
                  <div key={item.recommendation_id} className="rounded-md border border-border p-3">
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      <p className="text-sm font-medium">{item.title}</p>
                      <Badge variant="outline" className="capitalize">
                        {item.action_type.replace('_', ' ')}
                      </Badge>
                      {item.insight_severity && (
                        <Badge variant="outline" className={severityBadgeClass(item.insight_severity)}>
                          {item.insight_severity}
                        </Badge>
                      )}
                    </div>
                    <p className="text-sm text-muted-foreground">{item.description}</p>
                    {Object.keys(item.suggested_config_changes || {}).length > 0 && (
                      <pre className="mt-2 overflow-x-auto rounded-md bg-muted p-2 text-xs">
                        {JSON.stringify(item.suggested_config_changes, null, 2)}
                      </pre>
                    )}
                    <div className="mt-3 flex items-center justify-between">
                      <p className="text-xs text-muted-foreground">
                        Source: {item.source}
                      </p>
                      <Button
                        size="sm"
                        onClick={() => onCreateDraft(item.recommendation_id)}
                        disabled={creating}
                      >
                        {creating ? (
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        ) : (
                          <Wand2 className="mr-2 h-4 w-4" />
                        )}
                        Create draft
                      </Button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
