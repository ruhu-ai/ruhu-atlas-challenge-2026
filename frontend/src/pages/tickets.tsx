/**
 * Global Tickets page — all conversations across all handlers.
 *
 * Features:
 * - Filterable by handler, channel, outcome, time period
 * - Sortable by created, sentiment, outcome, duration
 * - Searchable by participant and handler
 * - Click a row to view the full conversation transcript
 */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge } from '@/components/atoms/badge'
import { Input } from '@/components/atoms/input'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/atoms/table'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/atoms/select'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from '@/components/atoms/dialog'
import {
  Tabs, TabsList, TabsTrigger, TabsContent,
} from '@/components/atoms/tabs'
import {
  Loader2, Search, Phone, MessageSquare, Ticket,
  ChevronUp, ChevronDown, ChevronsUpDown,
  SmilePlus, Frown, Meh, User, Bot,
  ChevronLeft, ChevronRight, Activity,
} from 'lucide-react'
import { Button } from '@/components/atoms/button'
import { ReasoningTimeline } from '@/features/agent-canvas/components/ReasoningTimeline'
import { useConversationTraces } from '@/features/agent-canvas/hooks/useConversationTraces'
import { cn } from '@/lib/utils'
import { DashboardLayout } from '@/layouts/dashboard-layout'
import { ticketSystemService } from '@/api/services/ticket-system.service'
import {
  type TicketConversationDetail,
  type TicketDashboardItem,
  type TicketDashboardResponse,
  type TicketDashboardQueryParams,
} from '@/types/ticket-system'

type SortField = 'started_at' | 'duration_seconds' | 'sentiment_score' | 'outcome' | 'message_count'
type SortDir = 'asc' | 'desc'
type ChannelFilter = 'all' | 'voice' | 'phone' | 'chat' | 'widget' | 'whatsapp'
type OutcomeFilter = 'all' | 'resolved' | 'transferred' | 'abandoned' | 'failed'
type TimePeriod = 'all' | '1' | '7' | '30' | '90'

function SortIcon({ field, sortBy, sortDir }: { field: SortField; sortBy: SortField; sortDir: SortDir }) {
  if (field !== sortBy) return <ChevronsUpDown className="ml-1 h-3.5 w-3.5 text-muted-foreground/50" />
  return sortDir === 'asc'
    ? <ChevronUp className="ml-1 h-3.5 w-3.5" />
    : <ChevronDown className="ml-1 h-3.5 w-3.5" />
}

function SentimentCell({ score, endedAt }: { score?: number | null; endedAt?: string | null }) {
  if (score == null) {
    if (endedAt) {
      const endedMs = new Date(endedAt).getTime()
      const twoMinAgo = Date.now() - 2 * 60 * 1000
      if (endedMs > twoMinAgo) {
        return <span className="text-muted-foreground text-xs italic">Analyzing...</span>
      }
    }
    return <span className="text-muted-foreground">--</span>
  }
  if (score > 0.1) return <span className="inline-flex items-center gap-1 text-green-400"><SmilePlus className="h-4 w-4" /> Positive</span>
  if (score < -0.1) return <span className="inline-flex items-center gap-1 text-red-400"><Frown className="h-4 w-4" /> Negative</span>
  return <span className="inline-flex items-center gap-1 text-yellow-400"><Meh className="h-4 w-4" /> Neutral</span>
}

function ChannelIcon({ channel }: { channel?: string | null }) {
  if (channel === 'voice' || channel === 'phone') return <Phone className="h-3.5 w-3.5 mr-1" />
  return <MessageSquare className="h-3.5 w-3.5 mr-1" />
}

function OutcomeBadge({ outcome }: { outcome?: string | null }) {
  if (!outcome) {
    return <span className="text-muted-foreground">--</span>
  }
  const colors: Record<string, string> = {
    resolved: 'bg-green-500/15 text-green-400 border-green-500/30',
    transferred: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/30',
    abandoned: 'bg-red-500/15 text-red-400 border-red-500/30',
    failed: 'bg-red-500/15 text-red-400 border-red-500/30',
    callback_scheduled: 'bg-purple-500/15 text-purple-400 border-purple-500/30',
    follow_up_required: 'bg-orange-500/15 text-orange-400 border-orange-500/30',
    voicemail: 'bg-slate-500/15 text-slate-400 border-slate-500/30',
  }
  const label = outcome.replace(/_/g, ' ')
  return (
    <Badge variant="outline" className={cn('text-xs capitalize', colors[outcome] || '')}>
      {label}
    </Badge>
  )
}

function formatDuration(seconds?: number | null): string {
  if (seconds == null || seconds === 0) return '0s'
  if (seconds >= 60) {
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return s > 0 ? `${m}m ${s}s` : `${m}m`
  }
  return `${seconds}s`
}

function formatDate(dateStr?: string | null): string {
  if (!dateStr) return '--'
  const d = new Date(dateStr)
  const now = new Date()
  const isToday = d.toDateString() === now.toDateString()
  if (isToday) {
    return 'Today, ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }
  return d.toLocaleDateString([], { day: 'numeric', month: 'short' }) + ', ' +
    d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function formatFullDate(dateStr?: string | null): string {
  if (!dateStr) return ''
  return new Date(dateStr).toLocaleString([], {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

function TranscriptPanel({
  detail,
  isLoading,
  onClose,
}: {
  detail: TicketConversationDetail | null
  isLoading: boolean
  onClose: () => void
}) {
  const conversation = detail?.conversation
  const transcript = detail?.transcript ?? []

  // Postmortem reasoning fetch — one-shot, no polling. Tickets surface is
  // historical-only, so the conversation is by definition not changing
  // anymore. (Live polling is the calls.tsx job.)
  const { traces: reasoningTraces, error: reasoningError } = useConversationTraces(
    conversation?.conversation_id ?? null,
    { singleFetch: true },
  )

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Ticket className="h-5 w-5" />
            Conversation Detail
          </DialogTitle>
        </DialogHeader>

        {isLoading || !conversation ? (
          <div className="flex items-center justify-center py-10">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <Tabs defaultValue="transcript" className="mt-4">
            <TabsList className="w-full justify-start">
              <TabsTrigger value="transcript" className="gap-1.5">
                <MessageSquare className="h-3.5 w-3.5" />
                Transcript
              </TabsTrigger>
              <TabsTrigger value="reasoning" className="gap-1.5">
                <Activity className="h-3.5 w-3.5" />
                Reasoning
              </TabsTrigger>
            </TabsList>

            <TabsContent value="transcript">
            <div className="mt-4 space-y-2 text-sm border-b pb-4 mb-4">
              <div className="flex justify-between">
                <span className="text-muted-foreground">Agent</span>
                <span className="font-medium">{conversation.handler_name || '--'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">From</span>
                <span>{conversation.participant_display || conversation.participant_ref || '--'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Channel</span>
                <span className="inline-flex items-center capitalize">
                  <ChannelIcon channel={conversation.channel ?? undefined} />
                  {conversation.channel || 'chat'}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Started</span>
                <span>{formatFullDate(conversation.started_at)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Duration</span>
                <span>{formatDuration(conversation.duration_seconds)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Resolution</span>
                <OutcomeBadge outcome={conversation.outcome} />
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Sentiment</span>
                <SentimentCell score={conversation.sentiment_score} />
              </div>
            </div>

            {transcript.length === 0 ? (
              <div className="text-center py-10 text-muted-foreground text-sm">
                No messages found for this conversation.
              </div>
            ) : (
              <div className="space-y-3">
                {transcript.map((msg) => (
                  <div
                    key={msg.entry_id}
                    className={cn(
                      'flex gap-2',
                      msg.role === 'user' ? 'justify-end' : 'justify-start',
                    )}
                  >
                    {msg.role !== 'user' && (
                      <div className="h-7 w-7 rounded-full bg-primary/10 flex items-center justify-center shrink-0 mt-1">
                        <Bot className="h-3.5 w-3.5 text-primary" />
                      </div>
                    )}
                    <div
                      className={cn(
                        'max-w-[80%] rounded-lg px-3 py-2 text-sm',
                        msg.role === 'user'
                          ? 'bg-primary text-primary-foreground'
                          : 'bg-muted',
                      )}
                    >
                      <p className="whitespace-pre-wrap">{msg.text}</p>
                      <p className={cn(
                        'text-[10px] mt-1',
                        msg.role === 'user'
                          ? 'text-primary-foreground/60 text-right'
                          : 'text-muted-foreground',
                      )}>
                        {new Date(msg.recorded_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </p>
                    </div>
                    {msg.role === 'user' && (
                      <div className="h-7 w-7 rounded-full bg-primary flex items-center justify-center shrink-0 mt-1">
                        <User className="h-3.5 w-3.5 text-primary-foreground" />
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
            </TabsContent>

            <TabsContent value="reasoning" className="mt-4">
              {reasoningError ? (
                <div className="rounded-md border border-rose-400/40 bg-rose-500/10 p-3 text-xs text-rose-300">
                  Couldn't load reasoning: {reasoningError}
                </div>
              ) : (
                <ReasoningTimeline
                  traces={reasoningTraces}
                  emptyMessage="No reasoning recorded for this conversation."
                />
              )}
            </TabsContent>
          </Tabs>
        )}
      </DialogContent>
    </Dialog>
  )
}

const PAGE_SIZE = 50

export default function TicketsPage() {
  const [searchQuery, setSearchQuery] = useState('')
  const [channelFilter, setChannelFilter] = useState<ChannelFilter>('all')
  const [handlerFilter, setHandlerFilter] = useState<string>('all')
  const [outcomeFilter, setOutcomeFilter] = useState<OutcomeFilter>('all')
  const [timePeriod, setTimePeriod] = useState<TimePeriod>('7')
  const [sortBy, setSortBy] = useState<SortField>('started_at')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [page, setPage] = useState(1)
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null)

  const dashboardQuery = useQuery<TicketDashboardResponse>({
    queryKey: [
      'global-tickets',
      handlerFilter,
      channelFilter,
      outcomeFilter,
      timePeriod,
      sortBy,
      sortDir,
      searchQuery.trim(),
      page,
    ],
    queryFn: () => ticketSystemService.getDashboard(buildDashboardQuery()),
  })

  const detailQuery = useQuery<TicketConversationDetail | null>({
    queryKey: ['ticket-detail', selectedConversationId],
    queryFn: () =>
      selectedConversationId
        ? ticketSystemService.getConversationDetail(selectedConversationId)
        : Promise.resolve(null),
    enabled: !!selectedConversationId,
  })

  const items = dashboardQuery.data?.items ?? []
  const handlers = dashboardQuery.data?.handlers ?? []

  function buildDashboardQuery(): TicketDashboardQueryParams {
    const trimmedSearch = searchQuery.trim()
    return {
      q: trimmedSearch || undefined,
      handler_id: handlerFilter !== 'all' ? handlerFilter : undefined,
      channel: channelFilter !== 'all' ? channelFilter : undefined,
      outcome: outcomeFilter !== 'all' ? outcomeFilter : undefined,
      days: timePeriod !== 'all' ? Number(timePeriod) : undefined,
      sort_by: sortBy,
      sort_dir: sortDir,
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
    }
  }

  function resetPage() {
    setPage(1)
  }

  function toggleSort(field: SortField) {
    if (sortBy === field) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortBy(field)
      setSortDir('desc')
    }
    resetPage()
  }

  const stats = dashboardQuery.data?.summary
    ? {
      total: dashboardQuery.data.summary.total_count,
      resolvedRate: dashboardQuery.data.summary.resolved_rate,
      transferred: dashboardQuery.data.summary.transferred_count,
      avgDuration: formatDuration(Math.round(dashboardQuery.data.summary.average_duration_seconds)),
    }
    : null

  const periodLabels: Record<TimePeriod, string> = {
    all: 'All time',
    '1': 'Last 24h',
    '7': 'Last 7 days',
    '30': 'Last 30 days',
    '90': 'Last 90 days',
  }

  return (
    <DashboardLayout noPadding>
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <Ticket className="h-6 w-6" />
            Tickets
          </h1>
          <p className="text-muted-foreground mt-1">
            Recent conversations handled by your agents.
          </p>
        </div>
        {stats && (
          <div className="flex items-center gap-4 text-sm">
            <div className="text-center">
              <div className="text-lg font-semibold">{stats.total}</div>
              <div className="text-muted-foreground text-xs">Total</div>
            </div>
            <div className="text-center">
              <div className="text-lg font-semibold text-green-400">{stats.resolvedRate.toFixed(1)}%</div>
              <div className="text-muted-foreground text-xs">Resolved</div>
            </div>
            <div className="text-center">
              <div className="text-lg font-semibold text-yellow-400">{stats.transferred}</div>
              <div className="text-muted-foreground text-xs">Transferred</div>
            </div>
            <div className="text-center">
              <div className="text-lg font-semibold">{stats.avgDuration}</div>
              <div className="text-muted-foreground text-xs">Avg Duration</div>
            </div>
          </div>
        )}
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search tickets..."
            value={searchQuery}
            onChange={(e) => { setSearchQuery(e.target.value); resetPage() }}
            className="pl-9"
          />
        </div>

        <Select value={handlerFilter} onValueChange={(v) => { setHandlerFilter(v); resetPage() }}>
          <SelectTrigger className="w-48">
            <SelectValue placeholder="All handlers" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All handlers</SelectItem>
            {handlers.map((handler) => (
              <SelectItem key={handler.handler_id} value={handler.handler_id}>
                {handler.handler_name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={channelFilter} onValueChange={(v) => { setChannelFilter(v as ChannelFilter); resetPage() }}>
          <SelectTrigger className="w-32">
            <SelectValue placeholder="Channel" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All</SelectItem>
            <SelectItem value="voice">Voice</SelectItem>
            <SelectItem value="phone">Phone</SelectItem>
            <SelectItem value="chat">Chat</SelectItem>
            <SelectItem value="widget">Widget</SelectItem>
            <SelectItem value="whatsapp">WhatsApp</SelectItem>
          </SelectContent>
        </Select>

        <Select value={outcomeFilter} onValueChange={(v) => { setOutcomeFilter(v as OutcomeFilter); resetPage() }}>
          <SelectTrigger className="w-36">
            <SelectValue placeholder="Resolution" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All outcomes</SelectItem>
            <SelectItem value="resolved">Resolved</SelectItem>
            <SelectItem value="transferred">Transferred</SelectItem>
            <SelectItem value="abandoned">Abandoned</SelectItem>
            <SelectItem value="failed">Failed</SelectItem>
          </SelectContent>
        </Select>

        <Select value={timePeriod} onValueChange={(v) => { setTimePeriod(v as TimePeriod); resetPage() }}>
          <SelectTrigger className="w-36">
            <SelectValue placeholder="Time period" />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(periodLabels).map(([value, label]) => (
              <SelectItem key={value} value={value}>{label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {dashboardQuery.isLoading ? (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-20 text-muted-foreground">
          No tickets found.
        </div>
      ) : (
        <div className="border rounded-lg overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="cursor-pointer select-none" onClick={() => toggleSort('started_at')}>
                  <span className="inline-flex items-center">Created <SortIcon field="started_at" sortBy={sortBy} sortDir={sortDir} /></span>
                </TableHead>
                <TableHead className="cursor-pointer select-none" onClick={() => toggleSort('sentiment_score')}>
                  <span className="inline-flex items-center">Sentiment <SortIcon field="sentiment_score" sortBy={sortBy} sortDir={sortDir} /></span>
                </TableHead>
                <TableHead>Agent</TableHead>
                <TableHead>From</TableHead>
                <TableHead>Channel</TableHead>
                <TableHead className="cursor-pointer select-none" onClick={() => toggleSort('outcome')}>
                  <span className="inline-flex items-center">Resolution <SortIcon field="outcome" sortBy={sortBy} sortDir={sortDir} /></span>
                </TableHead>
                <TableHead className="cursor-pointer select-none text-right" onClick={() => toggleSort('duration_seconds')}>
                  <span className="inline-flex items-center justify-end">Duration <SortIcon field="duration_seconds" sortBy={sortBy} sortDir={sortDir} /></span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((conv: TicketDashboardItem) => (
                <TableRow
                  key={conv.conversation_id}
                  className="hover:bg-muted/50 cursor-pointer"
                  onClick={() => {
                    setSelectedConversationId(conv.conversation_id)
                  }}
                >
                  <TableCell className="text-sm">{formatDate(conv.started_at)}</TableCell>
                  <TableCell><SentimentCell score={conv.sentiment_score} endedAt={conv.ended_at} /></TableCell>
                  <TableCell className="text-sm">{conv.handler_name || '--'}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">{conv.participant_display || conv.participant_ref || '--'}</TableCell>
                  <TableCell>
                    <span className="inline-flex items-center text-sm text-muted-foreground capitalize">
                      <ChannelIcon channel={conv.channel ?? undefined} />
                      {conv.channel || 'chat'}
                    </span>
                  </TableCell>
                  <TableCell><OutcomeBadge outcome={conv.outcome} /></TableCell>
                  <TableCell className="text-sm text-right text-muted-foreground">{formatDuration(conv.duration_seconds)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {(() => {
        const total = dashboardQuery.data?.summary.total_count ?? 0
        const totalPages = Math.ceil(total / PAGE_SIZE)
        if (totalPages <= 1) return null
        return (
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>
              {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)} of {total}
            </span>
            <div className="flex items-center gap-1">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1 || dashboardQuery.isFetching}
                onClick={() => setPage((p) => p - 1)}
              >
                <ChevronLeft className="h-4 w-4" />
                Previous
              </Button>
              <span className="px-3">Page {page} of {totalPages}</span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPages || dashboardQuery.isFetching}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        )
      })()}

      {selectedConversationId && (
        <TranscriptPanel
          detail={detailQuery.data ?? null}
          isLoading={detailQuery.isLoading}
          onClose={() => setSelectedConversationId(null)}
        />
      )}

    </div>
    </DashboardLayout>
  )
}
