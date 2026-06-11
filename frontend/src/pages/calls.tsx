/**
 * Live Calls Page
 *
 * Monitors active voice sessions and call history in real time.
 * Active sessions refresh every 5 s (list) / 3 s (detail); ended
 * sessions are fetched once and not polled.
 */

import { useState, useEffect, useCallback } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { DashboardLayout } from '@/layouts/dashboard-layout'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/atoms/card'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/atoms/dialog'
import { Tabs, TabsList, TabsTrigger } from '@/components/atoms/tabs'
import { Separator } from '@/components/atoms/separator'
import {
  Activity,
  Calendar,
  ChevronRight,
  Clock,
  Copy,
  Check,
  MessageSquare,
  Phone,
  PhoneOff,
  Radio,
  User,
  XCircle,
} from 'lucide-react'
import { voiceSessionService } from '@/api/services/voice-session.service'
import type {
  VoiceSessionResponse,
  VoiceTurnTrace,
  RealtimeConversationEvent,
} from '@/api/services/voice-session.service'
import { ReasoningTimeline } from '@/features/agent-canvas/components/ReasoningTimeline'
import { CitationView } from '@/features/citations'
import { classifyLifecycle, type LifecycleCategory } from './calls.lifecycle'
import { cn } from '@/lib/utils'
import { toast } from 'sonner'

// ─── Types ────────────────────────────────────────────────────────────────────

type StatusFilter = 'all' | 'active' | 'ended'

type TimelineItem = {
  id: string
  kind: 'trace' | 'event'
  timestamp: string
  title: string
  detail: string | null
  /** Classification of the lifecycle event family, if applicable.  Null for non-lifecycle events. */
  lifecycle: LifecycleCategory
}

function lifecycleBadgeClasses(category: LifecycleCategory): string {
  switch (category) {
    case 'activity':
      return 'bg-sky-500/15 text-sky-400 border-sky-500/30'
    case 'interrupt':
      return 'bg-amber-500/15 text-amber-400 border-amber-500/30'
    case 'repair':
      return 'bg-purple-500/15 text-purple-300 border-purple-500/30'
    case 'policy':
      return 'bg-rose-500/15 text-rose-400 border-rose-500/30'
    case 'permission':
      return 'bg-cyan-500/15 text-cyan-300 border-cyan-500/30'
    case 'grounding':
      return 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
    case 'capture':
      return 'bg-teal-500/15 text-teal-300 border-teal-500/30'
    case 'narration':
      return 'bg-indigo-500/15 text-indigo-300 border-indigo-500/30'
    case 'artifact':
      return 'bg-fuchsia-500/15 text-fuchsia-300 border-fuchsia-500/30'
    default:
      return ''
  }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '—'
  if (seconds < 60) return `${seconds}s`
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`
}

function formatDateTime(dateString: string | null | undefined): string {
  if (!dateString) return '—'
  const d = new Date(dateString)
  if (Number.isNaN(d.getTime())) return dateString
  const now = new Date()
  const isToday = d.toDateString() === now.toDateString()
  if (isToday) {
    return 'Today, ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  }
  return (
    d.toLocaleDateString([], { day: 'numeric', month: 'short' }) +
    ', ' +
    d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  )
}

function truncateId(id: string): string {
  if (id.length <= 16) return id
  return `${id.slice(0, 8)}…${id.slice(-4)}`
}

function summarizeEventPayload(payload: Record<string, unknown> | undefined): string | null {
  if (!payload) return null
  const deliveryId = typeof payload.delivery_id === 'string' ? payload.delivery_id : null
  const traceId = typeof payload.trace_id === 'string' ? payload.trace_id : null
  const turnId = typeof payload.turn_id === 'string' ? payload.turn_id : null
  const reason = typeof payload.reason === 'string' ? payload.reason : null
  const stage = typeof payload.stage === 'string' ? payload.stage : null
  const conversationSequence =
    typeof payload.conversation_sequence === 'number' ? payload.conversation_sequence : null
  if (deliveryId || stage) {
    const parts = [
      stage ? `stage=${stage}` : null,
      deliveryId ? `delivery=${deliveryId}` : null,
      conversationSequence !== null ? `seq=${conversationSequence}` : null,
      traceId ? `trace=${traceId}` : null,
      turnId ? `turn=${turnId}` : null,
      reason ? `reason=${reason}` : null,
    ].filter(Boolean)
    if (parts.length > 0) return parts.join(' · ')
  }
  const directText = ['text', 'message', 'reason', 'status', 'signal']
    .map((key) => payload[key])
    .find((value) => typeof value === 'string' && (value as string).length > 0)
  if (typeof directText === 'string') return directText
  const compact = JSON.stringify(payload)
  if (!compact || compact === '{}') return null
  return compact.length > 160 ? `${compact.slice(0, 157)}…` : compact
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  if (status === 'active') {
    return (
      <Badge className="bg-emerald-500/20 text-emerald-700 dark:text-emerald-400 border-emerald-500/30">
        <Activity className="mr-1 h-3 w-3 animate-pulse" />
        Live
      </Badge>
    )
  }
  const tone =
    status === 'errored'
      ? 'border-rose-500/40 text-rose-400'
      : status === 'disconnected'
        ? 'border-amber-500/40 text-amber-400'
        : 'border-border/60 text-muted-foreground'
  return (
    <Badge variant="outline" className={cn('text-xs capitalize', tone)}>
      {status}
    </Badge>
  )
}

function SessionCardSkeleton() {
  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-center justify-between">
          <div className="flex-1 space-y-2">
            <div className="flex items-center gap-4">
              <div className="h-4 w-40 animate-pulse rounded bg-muted" />
              <div className="h-5 w-12 animate-pulse rounded bg-muted" />
            </div>
            <div className="flex items-center gap-6">
              <div className="h-3 w-24 animate-pulse rounded bg-muted" />
              <div className="h-3 w-32 animate-pulse rounded bg-muted" />
              <div className="h-3 w-16 animate-pulse rounded bg-muted" />
            </div>
          </div>
          <div className="h-4 w-4 animate-pulse rounded bg-muted" />
        </div>
      </CardContent>
    </Card>
  )
}

function CopyId({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = useCallback(() => {
    void navigator.clipboard.writeText(value).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }, [value])
  return (
    <div className="flex items-center gap-1.5">
      <span className="font-medium text-foreground">{label}:</span>
      <span className="font-mono text-xs">{truncateId(value)}</span>
      <button
        type="button"
        onClick={handleCopy}
        className="rounded p-0.5 text-muted-foreground hover:text-foreground transition-colors"
        title={`Copy ${label}`}
      >
        {copied ? <Check className="h-3 w-3 text-emerald-400" /> : <Copy className="h-3 w-3" />}
      </button>
    </div>
  )
}

function SessionCard({
  session,
  isActive,
  duration,
  onSelect,
  onEndCall,
  isEndingCall,
}: {
  session: VoiceSessionResponse
  isActive: boolean
  duration: string
  onSelect: () => void
  onEndCall?: () => void
  isEndingCall?: boolean
}) {
  return (
    <Card
      key={session.id}
      data-testid={`call-session-card-${session.id}`}
      className={cn(
        'cursor-pointer transition-colors',
        isActive
          ? 'border-emerald-500/30 bg-emerald-50 hover:border-emerald-500/50 dark:bg-emerald-500/5'
          : 'hover:border-border/80 dark:hover:border-white/10',
      )}
      onClick={onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onSelect()
        }
      }}
    >
      <CardContent className="p-4">
        <div className="flex items-center justify-between gap-4">
          <div className="flex-1 min-w-0 space-y-2">
            <div className="flex items-center gap-3 flex-wrap">
              <span className="font-medium text-foreground truncate">{session.agent_name}</span>
              <StatusBadge status={session.status} />
            </div>
            <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm text-muted-foreground">
              <div className="flex items-center gap-1.5">
                <Calendar className="h-3.5 w-3.5 shrink-0" />
                {formatDateTime(session.started_at)}
              </div>
              {!isActive && session.ended_at && (
                <div className="flex items-center gap-1.5">
                  <PhoneOff className="h-3.5 w-3.5 shrink-0" />
                  {formatDateTime(session.ended_at)}
                </div>
              )}
              {isActive && session.room_name && (
                <div className="flex items-center gap-1.5">
                  <Phone className="h-3.5 w-3.5 shrink-0" />
                  <span className="font-mono text-xs">{session.room_name}</span>
                </div>
              )}
              <div className="flex items-center gap-1.5">
                <Clock className="h-3.5 w-3.5 shrink-0" />
                <span className={cn('tabular-nums', isActive && 'text-emerald-700 dark:text-emerald-400 font-mono')}>
                  {duration}
                </span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {isActive && onEndCall && (
              <Button
                size="sm"
                variant="outline"
                className="border-rose-500/40 text-rose-400 hover:bg-rose-500/10 hover:border-rose-500/60"
                disabled={isEndingCall}
                onClick={(e) => {
                  e.stopPropagation()
                  onEndCall()
                }}
              >
                <XCircle className="mr-1.5 h-3.5 w-3.5" />
                {isEndingCall ? 'Ending…' : 'End'}
              </Button>
            )}
            <ChevronRight
              className={cn(
                'h-4 w-4 shrink-0',
                isActive ? 'text-emerald-700/70 dark:text-emerald-400/70' : 'text-muted-foreground',
              )}
            />
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function CallsPage() {
  const queryClient = useQueryClient()
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [timelineFilter, setTimelineFilter] =
    useState<'all' | 'lifecycle' | 'traces'>('all')
  const [selectedSession, setSelectedSession] = useState<VoiceSessionResponse | null>(null)

  // Tick every second to live-update active call durations.
  // Only runs when there are active sessions in the current view.
  const [now, setNow] = useState(Date.now())

  const { data: sessions = [], isLoading } = useQuery({
    queryKey: ['voice-sessions', statusFilter],
    queryFn: () => voiceSessionService.listSessions({ status_filter: statusFilter, limit: 50, offset: 0 }),
    refetchInterval: 5000,
  })

  const { data: activeCount } = useQuery({
    queryKey: ['voice-sessions-active-count'],
    queryFn: () => voiceSessionService.getActiveCount(),
    refetchInterval: 3000,
  })

  const hasActiveSessions = sessions.some((s) => s.status === 'active')

  // Keep the selected session in sync with the live list
  useEffect(() => {
    if (!selectedSession) return
    const latest = sessions.find((s) => s.id === selectedSession.id)
    if (latest) setSelectedSession(latest)
  }, [sessions, selectedSession])

  // Live duration ticker — only active when there are active sessions
  useEffect(() => {
    if (!hasActiveSessions) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [hasActiveSessions])

  const endSessionMutation = useMutation({
    mutationFn: (sessionId: string) => voiceSessionService.endSession(sessionId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['voice-sessions'] }),
        queryClient.invalidateQueries({ queryKey: ['voice-sessions-active-count'] }),
      ])
      toast.success('Call ended')
      // If the ended session is the one being viewed, close the detail dialog
      setSelectedSession((prev) => {
        const updated = sessions.find((s) => s.id === prev?.id)
        return updated ?? null
      })
    },
    onError: (error: Error) => toast.error(error.message || 'Unable to end call'),
  })

  const handleEndCall = useCallback(
    (session: VoiceSessionResponse) => {
      if (!window.confirm(`End the call "${session.agent_name}"?`)) return
      endSessionMutation.mutate(session.id)
    },
    [endSessionMutation],
  )

  const getSessionDuration = useCallback(
    (session: VoiceSessionResponse) => {
      if (session.status === 'active' && session.started_at) {
        const elapsed = Math.max(0, Math.floor((now - new Date(session.started_at).getTime()) / 1000))
        return formatDuration(elapsed)
      }
      return formatDuration(session.duration_seconds)
    },
    [now],
  )

  const activeSessions = sessions.filter((s) => s.status === 'active')
  const endedSessions = sessions.filter((s) => s.status !== 'active')
  const selectedSessionId = selectedSession?.id ?? null
  const selectedConversationId = selectedSession?.conversation_id ?? null
  const isSelectedSessionActive = selectedSession?.status === 'active'

  // ── Detail panel queries ──────────────────────────────────────────────────

  const { data: selectedSessionStatus, isLoading: isLoadingSessionStatus } = useQuery({
    queryKey: ['voice-session-status', selectedSessionId],
    queryFn: () => voiceSessionService.getSessionStatus(selectedSessionId!),
    enabled: !!selectedSessionId,
    refetchInterval: isSelectedSessionActive ? 3000 : false,
  })

  const { data: conversationTraces = [], isLoading: isLoadingTraces } = useQuery({
    queryKey: ['voice-session-conversation-traces', selectedConversationId],
    queryFn: () => voiceSessionService.getConversationTraces(selectedConversationId!),
    enabled: !!selectedConversationId,
    refetchInterval: isSelectedSessionActive ? 3000 : false,
  })

  const { data: conversationEvents = [], isLoading: isLoadingEvents } = useQuery({
    queryKey: ['voice-session-conversation-events', selectedConversationId],
    queryFn: () => voiceSessionService.getConversationRealtimeEvents(selectedConversationId!),
    enabled: !!selectedConversationId,
    refetchInterval: isSelectedSessionActive ? 3000 : false,
  })

  // Chronological order (oldest first) — easier to follow conversation flow
  const timeline: TimelineItem[] = [
    ...conversationTraces.map((trace: VoiceTurnTrace): TimelineItem => ({
      id: `trace-${trace.trace_id}`,
      kind: 'trace',
      timestamp: trace.recorded_at,
      title: `${trace.step_before} → ${trace.step_after}`,
      detail: trace.emitted_messages?.[0]?.text ?? null,
      lifecycle: null,
    })),
    ...conversationEvents.map((event: RealtimeConversationEvent): TimelineItem => ({
      id: `event-${event.event_id}`,
      kind: 'event',
      timestamp: event.created_at,
      title: `${event.family}.${event.name}`,
      detail: summarizeEventPayload(event.payload),
      lifecycle: classifyLifecycle(event.family, event.name),
    })),
  ].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime())

  const filteredTimeline =
    timelineFilter === 'lifecycle'
      ? timeline.filter((item) => item.lifecycle !== null)
      : timelineFilter === 'traces'
        ? timeline.filter((item) => item.kind === 'trace')
        : timeline

  const lifecycleEventCount = timeline.filter((item) => item.lifecycle !== null).length

  const isLoadingTimeline = isLoadingTraces || isLoadingEvents

  // ── Empty state copy varies by filter ───────────────────────────────────

  const emptyMessage =
    statusFilter === 'active'
      ? 'No active calls right now.'
      : statusFilter === 'ended'
        ? 'No call history yet.'
        : 'No voice sessions yet.'
  const emptySubMessage =
    statusFilter === 'active'
      ? 'Start a session from any agent canvas to see it here.'
      : 'Voice sessions will appear here once a call has been made.'

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <DashboardLayout>
      <div className="space-y-6">

        {/* Header */}
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Live Calls</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Monitor active voice sessions and review call history.
            </p>
          </div>
          <div className="flex items-center gap-3">
            {/* Active session counter */}
            <div className="flex items-center gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5">
              <Activity className="h-4 w-4 text-emerald-600 dark:text-emerald-400 animate-pulse" />
              <span className="text-sm font-medium text-emerald-700 dark:text-emerald-300">
                {activeCount?.active_sessions ?? 0} active
              </span>
            </div>
          </div>
        </div>

        {/* Status filter */}
        <Tabs value={statusFilter} onValueChange={(v) => setStatusFilter(v as StatusFilter)}>
          <TabsList>
            <TabsTrigger value="all">All Sessions</TabsTrigger>
            <TabsTrigger value="active">
              Active
              {(activeCount?.active_sessions ?? 0) > 0 && (
                <span className="ml-1.5 inline-flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-emerald-500/20 px-1 text-[10px] font-medium text-emerald-400">
                  {activeCount!.active_sessions}
                </span>
              )}
            </TabsTrigger>
            <TabsTrigger value="ended">Ended</TabsTrigger>
          </TabsList>
        </Tabs>

        {/* Skeleton while first loading */}
        {isLoading && (
          <div className="space-y-2">
            <SessionCardSkeleton />
            <SessionCardSkeleton />
            <SessionCardSkeleton />
          </div>
        )}

        {/* Active calls */}
        {!isLoading && activeSessions.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Phone className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
              <h2 className="text-sm font-medium text-emerald-700 dark:text-emerald-300">
                Active calls · {activeSessions.length}
              </h2>
            </div>
            <div className="space-y-2">
              {activeSessions.map((session) => (
                <SessionCard
                  key={session.id}
                  session={session}
                  isActive
                  duration={getSessionDuration(session)}
                  onSelect={() => setSelectedSession(session)}
                  onEndCall={() => handleEndCall(session)}
                  isEndingCall={
                    endSessionMutation.isPending && endSessionMutation.variables === session.id
                  }
                />
              ))}
            </div>
          </div>
        )}

        {/* Divider between sections when both are visible */}
        {!isLoading && activeSessions.length > 0 && endedSessions.length > 0 && (
          <Separator />
        )}

        {/* Call history */}
        {!isLoading && endedSessions.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <PhoneOff className="h-4 w-4 text-muted-foreground" />
              <h2 className="text-sm font-medium text-muted-foreground">
                Call history · {endedSessions.length}
              </h2>
            </div>
            <div className="space-y-2">
              {endedSessions.map((session) => (
                <SessionCard
                  key={session.id}
                  session={session}
                  isActive={false}
                  duration={getSessionDuration(session)}
                  onSelect={() => setSelectedSession(session)}
                />
              ))}
            </div>
          </div>
        )}

        {/* Empty state */}
        {!isLoading && sessions.length === 0 && (
          <Card>
            <CardContent className="flex flex-col items-center justify-center py-16">
              <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-muted">
                <Phone className="h-8 w-8 text-muted-foreground" />
              </div>
              <h3 className="text-base font-medium">{emptyMessage}</h3>
              <p className="mt-1 text-sm text-muted-foreground text-center max-w-sm">
                {emptySubMessage}
              </p>
            </CardContent>
          </Card>
        )}
      </div>

      {/* Session detail dialog */}
      <Dialog open={!!selectedSession} onOpenChange={(open) => { if (!open) setSelectedSession(null) }}>
        <DialogContent
          className="max-h-[90vh] overflow-y-auto sm:max-w-3xl"
          data-testid="calls-session-detail-drawer"
          aria-describedby={undefined}
        >
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Phone className="h-4 w-4" />
              {selectedSession?.agent_name ?? 'Session Details'}
            </DialogTitle>
            <DialogDescription>
              Conversation timeline and realtime events for this session.
            </DialogDescription>
          </DialogHeader>

          {selectedSession && (
            <div className="space-y-4">

              {/* Session metadata */}
              <Card>
                <CardContent className="pt-4 pb-4">
                  <div className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
                    <CopyId value={selectedSession.id} label="Session" />
                    <CopyId value={selectedSession.conversation_id} label="Conversation" />
                    <div className="flex items-center gap-1.5">
                      <span className="font-medium text-foreground">Status:</span>
                      <StatusBadge status={selectedSession.status} />
                    </div>
                    {selectedSession.room_name && (
                      <div className="flex items-center gap-1.5 text-muted-foreground">
                        <span className="font-medium text-foreground">Room:</span>
                        <span className="font-mono text-xs">{selectedSession.room_name}</span>
                      </div>
                    )}
                    <div className="flex items-center gap-1.5 text-muted-foreground">
                      <span className="font-medium text-foreground">Started:</span>
                      {formatDateTime(selectedSession.started_at)}
                    </div>
                    <div className="flex items-center gap-1.5 text-muted-foreground">
                      <span className="font-medium text-foreground">Duration:</span>
                      {getSessionDuration(selectedSession)}
                    </div>
                  </div>
                  {selectedSession.status === 'active' && (
                    <div className="mt-4 flex justify-end">
                      <Button
                        variant="outline"
                        size="sm"
                        className="border-rose-500/40 text-rose-400 hover:bg-rose-500/10"
                        disabled={endSessionMutation.isPending}
                        onClick={() => handleEndCall(selectedSession)}
                      >
                        <XCircle className="mr-1.5 h-3.5 w-3.5" />
                        {endSessionMutation.isPending ? 'Ending…' : 'End Call'}
                      </Button>
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* LiveKit participants */}
              <Card>
                <CardHeader className="pb-2 pt-4">
                  <CardTitle className="text-sm flex items-center gap-2">
                    <Radio className="h-4 w-4" />
                    Participants
                  </CardTitle>
                </CardHeader>
                <CardContent className="pb-4">
                  {isLoadingSessionStatus ? (
                    <div className="h-4 w-48 animate-pulse rounded bg-muted" />
                  ) : selectedSessionStatus ? (
                    <div className="space-y-2 text-sm">
                      <div className="flex items-center gap-2 text-muted-foreground">
                        <User className="h-3.5 w-3.5" />
                        <span>
                          {selectedSessionStatus.num_participants} participant
                          {selectedSessionStatus.num_participants !== 1 ? 's' : ''} in room
                        </span>
                      </div>
                      {selectedSessionStatus.participants.length > 0 && (
                        <div className="mt-2 space-y-1">
                          {selectedSessionStatus.participants.map((p, i) => (
                            <div
                              key={`${p.identity ?? 'participant'}-${i}`}
                              className="flex items-center gap-2 rounded-lg border border-border/60 bg-muted/30 px-3 py-2 text-sm"
                            >
                              <User className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                              <span className="font-medium">{p.name ?? p.identity ?? 'Unknown'}</span>
                              {p.identity && p.name && p.name !== p.identity && (
                                <span className="text-xs text-muted-foreground">({p.identity})</span>
                              )}
                              {p.joined_at && (
                                <span className="ml-auto text-xs text-muted-foreground">
                                  Joined {formatDateTime(p.joined_at)}
                                </span>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                      {selectedSessionStatus.num_participants === 0 && (
                        <p className="text-sm text-muted-foreground">No participants currently in room.</p>
                      )}
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">Participant data unavailable.</p>
                  )}
                </CardContent>
              </Card>

              {/* Conversation timeline */}
              <Card>
                <CardHeader className="pb-2 pt-4">
                  <CardTitle className="text-sm flex items-center gap-2">
                    <MessageSquare className="h-4 w-4" />
                    Conversation Timeline
                    {isSelectedSessionActive && (
                      <Badge variant="outline" className="ml-auto border-emerald-500/40 text-emerald-400 text-xs">
                        Live
                      </Badge>
                    )}
                  </CardTitle>
                </CardHeader>
                <CardContent className="pb-4">
                  {timeline.length > 0 && (
                    <div className="mb-3 flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => setTimelineFilter('all')}
                        className={`rounded px-2 py-1 text-xs ${
                          timelineFilter === 'all'
                            ? 'bg-primary text-primary-foreground'
                            : 'bg-muted text-muted-foreground hover:text-foreground'
                        }`}
                      >
                        All ({timeline.length})
                      </button>
                      <button
                        type="button"
                        onClick={() => setTimelineFilter('lifecycle')}
                        className={`rounded px-2 py-1 text-xs ${
                          timelineFilter === 'lifecycle'
                            ? 'bg-primary text-primary-foreground'
                            : 'bg-muted text-muted-foreground hover:text-foreground'
                        }`}
                        disabled={lifecycleEventCount === 0}
                        title="Activity, repair, policy, permission, grounding, narration events"
                      >
                        Lifecycle ({lifecycleEventCount})
                      </button>
                      <button
                        type="button"
                        onClick={() => setTimelineFilter('traces')}
                        className={`rounded px-2 py-1 text-xs ${
                          timelineFilter === 'traces'
                            ? 'bg-primary text-primary-foreground'
                            : 'bg-muted text-muted-foreground hover:text-foreground'
                        }`}
                      >
                        Turns ({conversationTraces.length})
                      </button>
                    </div>
                  )}
                  {isLoadingTimeline && (
                    <div className="space-y-2">
                      <div className="h-12 animate-pulse rounded-lg bg-muted" />
                      <div className="h-12 animate-pulse rounded-lg bg-muted" />
                      <div className="h-12 animate-pulse rounded-lg bg-muted" />
                    </div>
                  )}
                  {!isLoadingTimeline && timeline.length === 0 && (
                    <p className="text-sm text-muted-foreground">
                      No timeline data yet for this conversation.
                    </p>
                  )}
                  {!isLoadingTimeline &&
                    timeline.length > 0 &&
                    filteredTimeline.length === 0 && (
                      <p className="text-sm text-muted-foreground">
                        No events match the current filter.
                      </p>
                    )}
                  {!isLoadingTimeline && filteredTimeline.length > 0 && (
                    <div className="space-y-2" data-testid="calls-session-timeline">
                      {filteredTimeline.map((item) => (
                        <div
                          key={item.id}
                          data-testid="calls-timeline-item"
                          className="rounded-lg border border-border bg-card/80 p-3"
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div className="flex items-center gap-2 min-w-0">
                              <Badge
                                variant={item.kind === 'trace' ? 'default' : 'secondary'}
                                className={`shrink-0 text-xs ${
                                  item.lifecycle
                                    ? lifecycleBadgeClasses(item.lifecycle)
                                    : ''
                                }`}
                              >
                                {item.lifecycle
                                  ? item.lifecycle
                                  : item.kind === 'trace'
                                    ? 'Trace'
                                    : 'Event'}
                              </Badge>
                              <span className="text-sm font-medium truncate">{item.title}</span>
                            </div>
                            <span className="shrink-0 text-xs text-muted-foreground">
                              {formatDateTime(item.timestamp)}
                            </span>
                          </div>
                          {item.detail && (
                            <p className="mt-1.5 text-sm text-muted-foreground break-words pl-[4.5rem]">
                              {item.detail}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* Reasoning Timeline — Sierra-style per-turn evidence trail.
                  During an active call, the conversationTraces query above
                  is already polling every 3s (refetchInterval) so this
                  surface updates live. After the call ends, polling stops
                  and the timeline becomes a static postmortem view. */}
              <Card data-testid="calls-reasoning-card">
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-sm">
                    <Activity className="h-4 w-4 text-muted-foreground" />
                    Reasoning
                    {isSelectedSessionActive && (
                      <span
                        className="relative flex h-1.5 w-1.5"
                        title="Live — updating every 3s"
                        aria-label="Live polling"
                      >
                        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                        <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
                      </span>
                    )}
                  </CardTitle>
                </CardHeader>
                <CardContent className="pt-0">
                  <ReasoningTimeline
                    traces={conversationTraces}
                    emptyMessage={
                      isSelectedSessionActive
                        ? 'Waiting for the first turn — reasoning will appear as the agent processes input.'
                        : 'This call has no recorded turns.'
                    }
                  />
                </CardContent>
              </Card>

              <CitationView conversationId={selectedConversationId} />
            </div>
          )}
        </DialogContent>
      </Dialog>
    </DashboardLayout>
  )
}
