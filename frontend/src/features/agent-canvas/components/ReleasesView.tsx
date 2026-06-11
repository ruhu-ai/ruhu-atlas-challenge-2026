import { useMemo, useState } from 'react'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/atoms/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/atoms/dialog'
import { Label } from '@/components/atoms/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/atoms/table'
import { Textarea } from '@/components/atoms/textarea'
import { RefreshCw, Rocket, RotateCcw, Loader2, History, Activity, ChevronLeft, ChevronRight, AlertTriangle } from 'lucide-react'
import type {
  AgentRelease,
  AgentReleaseTimelineEvent,
  ReleaseHealthResponse,
  ReleaseStrategy,
} from '@/api/services/agent.service'

interface ReleaseCanvasOption {
  id: string
  name: string
  version_number: number
  status: string
}

interface TimelineActorOption {
  id: string
  label: string
}

interface ReleasesViewProps {
  canvasVersions: ReleaseCanvasOption[]
  selectedCanvasVersionId?: string | null
  onSelectCanvasVersion: (canvasVersionId: string) => void
  releases: AgentRelease[]
  timelineEvents: AgentReleaseTimelineEvent[]
  timelineTotalEvents: number
  timelineLimit: number
  timelineOffset: number
  timelineHasMore: boolean
  timelineEventTypeFilter: string
  timelineReleaseFilter: string
  timelineActorFilter: string
  timelineActorOptions: TimelineActorOption[]
  onTimelineEventTypeFilterChange: (eventType: string) => void
  onTimelineReleaseFilterChange: (releaseId: string) => void
  onTimelineActorFilterChange: (actorId: string) => void
  onTimelinePrevPage: () => void
  onTimelineNextPage: () => void
  selectedReleaseId?: string | null
  onSelectRelease: (releaseId: string) => void
  health?: ReleaseHealthResponse | null
  loading: boolean
  timelineLoading: boolean
  healthLoading: boolean
  isCreating: boolean
  actionInProgressReleaseId?: string | null
  onRefresh: () => void
  onRefreshHealth: () => void
  onCreateRelease: (strategy: ReleaseStrategy) => void
  onPromoteRelease: (release: AgentRelease, reason?: string) => void
  onRollbackRelease: (release: AgentRelease, reason: string) => void
}

type PendingAction =
  | { type: 'promote'; release: AgentRelease }
  | { type: 'rollback'; release: AgentRelease }
  | null

const TIMELINE_EVENT_TYPE_OPTIONS = [
  'created',
  'promoted',
  'held',
  'superseded',
  'rolled_back',
  'restored',
  'rollback_restored_previous',
  'failed',
]

function formatDateTime(value?: string | null): string {
  if (!value) return 'n/a'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'n/a'
  return date.toLocaleString()
}

function statusBadgeClass(status: string): string {
  if (status === 'active') return 'border-emerald-500/30 text-emerald-400'
  if (status === 'deploying') return 'border-blue-500/30 text-blue-400'
  if (status === 'held') return 'border-yellow-500/30 text-yellow-300'
  if (status === 'superseded') return 'border-slate-500/30 text-slate-300'
  if (status === 'rolled_back') return 'border-red-500/30 text-red-400'
  if (status === 'failed') return 'border-red-500/30 text-red-400'
  return 'border-border text-muted-foreground'
}

export function ReleasesView({
  canvasVersions,
  selectedCanvasVersionId,
  onSelectCanvasVersion,
  releases,
  timelineEvents,
  timelineTotalEvents,
  timelineLimit,
  timelineOffset,
  timelineHasMore,
  timelineEventTypeFilter,
  timelineReleaseFilter,
  timelineActorFilter,
  timelineActorOptions,
  onTimelineEventTypeFilterChange,
  onTimelineReleaseFilterChange,
  onTimelineActorFilterChange,
  onTimelinePrevPage,
  onTimelineNextPage,
  selectedReleaseId,
  onSelectRelease,
  health,
  loading,
  timelineLoading,
  healthLoading,
  isCreating,
  actionInProgressReleaseId,
  onRefresh,
  onRefreshHealth,
  onCreateRelease,
  onPromoteRelease,
  onRollbackRelease,
}: ReleasesViewProps) {
  const [strategy, setStrategy] = useState<ReleaseStrategy>('canary')
  const [pendingAction, setPendingAction] = useState<PendingAction>(null)
  const [reason, setReason] = useState('')

  const summary = useMemo(() => {
    return {
      total: releases.length,
      active: releases.filter((release) => release.status === 'active').length,
      deploying: releases.filter((release) => release.status === 'deploying').length,
      rolledBack: releases.filter((release) => release.status === 'rolled_back').length,
    }
  }, [releases])

  const selectedRelease = releases.find((release) => release.id === selectedReleaseId) || null

  const openActionDialog = (type: 'promote' | 'rollback', release: AgentRelease) => {
    setPendingAction({ type, release })
    setReason('')
  }

  const confirmAction = () => {
    if (!pendingAction) return
    if (pendingAction.type === 'promote') {
      onPromoteRelease(pendingAction.release, reason || undefined)
      setPendingAction(null)
      return
    }

    if (!reason.trim()) return
    onRollbackRelease(pendingAction.release, reason.trim())
    setPendingAction(null)
  }

  const canConfirmAction =
    pendingAction?.type === 'promote'
      ? true
      : pendingAction?.type === 'rollback'
        ? reason.trim().length > 0
        : false
  const timelineStart = timelineTotalEvents === 0 ? 0 : timelineOffset + 1
  const timelineEnd = timelineOffset + timelineEvents.length

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Release orchestration</CardTitle>
          <CardDescription>
            Create an immediate/canary/progressive release and use promote or rollback controls.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-4">
            <div className="rounded-md border border-border p-3">
              <p className="text-xs text-muted-foreground">Total releases</p>
              <p className="text-xl font-semibold">{summary.total}</p>
            </div>
            <div className="rounded-md border border-border p-3">
              <p className="text-xs text-muted-foreground">Active</p>
              <p className="text-xl font-semibold text-emerald-400">{summary.active}</p>
            </div>
            <div className="rounded-md border border-border p-3">
              <p className="text-xs text-muted-foreground">Deploying</p>
              <p className="text-xl font-semibold text-blue-400">{summary.deploying}</p>
            </div>
            <div className="rounded-md border border-border p-3">
              <p className="text-xs text-muted-foreground">Rolled back</p>
              <p className="text-xl font-semibold text-red-400">{summary.rolledBack}</p>
            </div>
          </div>

          <div className="flex flex-col gap-3 rounded-md border border-border p-3">
            <div>
              <p className="text-sm font-medium">Create release</p>
              <p className="text-xs text-muted-foreground">
                Select the published canvas version and rollout strategy.
              </p>
            </div>
            <div className="grid gap-2 md:grid-cols-3">
              <Select
                value={selectedCanvasVersionId || 'none'}
                onValueChange={(value) => {
                  if (value !== 'none') onSelectCanvasVersion(value)
                }}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select canvas version" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none" disabled>
                    Select canvas version
                  </SelectItem>
                  {canvasVersions.map((version) => (
                    <SelectItem key={version.id} value={version.id}>
                      v{version.version_number} - {version.name} ({version.status})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={strategy} onValueChange={(value) => setStrategy(value as ReleaseStrategy)}>
                <SelectTrigger>
                  <SelectValue placeholder="Strategy" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="immediate">Immediate</SelectItem>
                  <SelectItem value="canary">Canary</SelectItem>
                  <SelectItem value="progressive">Progressive</SelectItem>
                </SelectContent>
              </Select>
              <div className="flex gap-2">
                <Button
                  onClick={() => onCreateRelease(strategy)}
                  disabled={!selectedCanvasVersionId || isCreating}
                >
                  {isCreating ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <Rocket className="mr-2 h-4 w-4" />
                  )}
                  Create
                </Button>
                <Button variant="outline" onClick={onRefresh}>
                  <RefreshCw className="mr-2 h-4 w-4" />
                  Refresh
                </Button>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Release history</CardTitle>
          <CardDescription>
            Current rollout state with promote/rollback controls.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading releases...
            </div>
          ) : releases.length === 0 ? (
            <div className="rounded-md border border-dashed border-border p-8 text-center">
              <History className="mx-auto mb-2 h-8 w-8 text-muted-foreground" />
              <p className="text-sm font-medium">No releases yet</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Create your first release to start a canary/progressive rollout history.
              </p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Release</TableHead>
                  <TableHead>Strategy</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Traffic</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {releases.map((release) => {
                  const actionPending = actionInProgressReleaseId === release.id
                  const canPromote = !['rolled_back', 'failed'].includes(release.status) && release.traffic_percent < 100
                  const canRollback = release.status !== 'rolled_back'
                  return (
                    <TableRow
                      key={release.id}
                      className={selectedReleaseId === release.id ? 'bg-muted/40' : ''}
                      onClick={() => onSelectRelease(release.id)}
                    >
                      <TableCell className="font-mono text-xs">{release.id.slice(0, 8)}</TableCell>
                      <TableCell className="capitalize">{release.strategy}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className={statusBadgeClass(release.status)}>
                          {release.status.replace(/_/g, ' ')}
                        </Badge>
                      </TableCell>
                      <TableCell>{release.traffic_percent}%</TableCell>
                      <TableCell>{formatDateTime(release.created_at)}</TableCell>
                      <TableCell>
                        <div className="flex items-center justify-end gap-2">
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={!canPromote || actionPending}
                            onClick={(event) => {
                              event.stopPropagation()
                              openActionDialog('promote', release)
                            }}
                          >
                            {actionPending ? (
                              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <Rocket className="mr-1 h-3.5 w-3.5" />
                            )}
                            Promote
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={!canRollback || actionPending}
                            onClick={(event) => {
                              event.stopPropagation()
                              openActionDialog('rollback', release)
                            }}
                          >
                            <RotateCcw className="mr-1 h-3.5 w-3.5" />
                            Rollback
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-lg">Live health</CardTitle>
              <CardDescription>
                Health indicators for the selected release.
              </CardDescription>
            </div>
            <Button variant="outline" size="sm" onClick={onRefreshHealth} disabled={!selectedReleaseId}>
              <RefreshCw className="mr-2 h-4 w-4" />
              Refresh Health
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {!selectedRelease ? (
            <p className="text-sm text-muted-foreground">Select a release to view health indicators.</p>
          ) : healthLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading health...
            </div>
          ) : !health ? (
            <p className="text-sm text-muted-foreground">No health snapshot available.</p>
          ) : (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline" className={statusBadgeClass(health.status)}>
                  <Activity className="mr-1 h-3 w-3" />
                  {health.status.replace(/_/g, ' ')}
                </Badge>
                {health.health_score != null && (
                  <Badge
                    variant="outline"
                    className={
                      health.health_score >= 70
                        ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400'
                        : health.health_score >= 40
                          ? 'border-yellow-500/30 bg-yellow-500/10 text-yellow-300'
                          : 'border-red-500/30 bg-red-500/10 text-red-400'
                    }
                  >
                    Score: {health.health_score}
                  </Badge>
                )}
                {health.risk_level && (
                  <Badge
                    variant="outline"
                    className={
                      health.risk_level === 'low'
                        ? 'border-emerald-500/30 text-emerald-400'
                        : health.risk_level === 'medium'
                          ? 'border-yellow-500/30 text-yellow-300'
                          : 'border-red-500/30 text-red-400'
                    }
                  >
                    Risk: {health.risk_level}
                  </Badge>
                )}
                <span className="text-xs text-muted-foreground">
                  Updated {formatDateTime(health.updated_at)}
                </span>
              </div>

              {health.rollback_recommended && (
                <div className="flex items-center gap-2 rounded-md border border-red-500/30 bg-red-500/10 p-2 text-sm text-red-400">
                  <AlertTriangle className="h-4 w-4 shrink-0" />
                  <span>Rollback recommended - health score is below threshold.</span>
                </div>
              )}

              {health.held_reason && (
                <div className="rounded-md border border-border bg-muted/40 p-2 text-sm text-muted-foreground">
                  <span className="font-medium">Held reason:</span> {health.held_reason}
                </div>
              )}

              <div className="space-y-1">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground">Traffic rollout</span>
                  <span className="font-medium">{health.traffic_percent}%</span>
                </div>
                <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-blue-500 transition-all"
                    style={{ width: `${Math.min(health.traffic_percent, 100)}%` }}
                  />
                </div>
              </div>

              {health.latest_simulation_run && (
                <div className="rounded border border-border p-2 text-xs text-muted-foreground">
                  <div className="flex items-center justify-between">
                    <span>Latest simulation pass rate</span>
                    <span className="font-medium text-foreground">
                      {health.latest_simulation_run.pass_rate_percent ?? 'n/a'}%
                    </span>
                  </div>
                  {health.latest_simulation_run.passed_count != null && (
                    <div className="mt-1 flex items-center gap-3">
                      <span className="text-emerald-400">
                        {health.latest_simulation_run.passed_count} passed
                      </span>
                      <span className="text-red-400">
                        {health.latest_simulation_run.failed_count} failed
                      </span>
                      <span>
                        {health.latest_simulation_run.total_test_cases} total
                      </span>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Event timeline</CardTitle>
          <CardDescription>
            Chronological release events persisted in audit history.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="mb-4 grid gap-2 md:grid-cols-4">
            <Select value={timelineEventTypeFilter} onValueChange={onTimelineEventTypeFilterChange}>
              <SelectTrigger>
                <SelectValue placeholder="Filter by event type" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All event types</SelectItem>
                {TIMELINE_EVENT_TYPE_OPTIONS.map((eventType) => (
                  <SelectItem key={eventType} value={eventType}>
                    {eventType.replace(/_/g, ' ')}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={timelineReleaseFilter} onValueChange={onTimelineReleaseFilterChange}>
              <SelectTrigger>
                <SelectValue placeholder="Filter by release" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All releases</SelectItem>
                {releases.map((release) => (
                  <SelectItem key={release.id} value={release.id}>
                    {release.id.slice(0, 8)} ({release.status})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={timelineActorFilter} onValueChange={onTimelineActorFilterChange}>
              <SelectTrigger>
                <SelectValue placeholder="Filter by actor" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All actors</SelectItem>
                {timelineActorOptions.map((actor) => (
                  <SelectItem key={actor.id} value={actor.id}>
                    {actor.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="flex items-center justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={onTimelinePrevPage}
                disabled={timelineLoading || timelineOffset === 0}
              >
                <ChevronLeft className="mr-1 h-4 w-4" />
                Prev
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={onTimelineNextPage}
                disabled={timelineLoading || !timelineHasMore}
              >
                Next
                <ChevronRight className="ml-1 h-4 w-4" />
              </Button>
            </div>
          </div>
          <div className="mb-3 text-xs text-muted-foreground">
            Showing {timelineStart}-{timelineEnd} of {timelineTotalEvents} events (page size {timelineLimit})
          </div>
          {timelineLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading timeline...
            </div>
          ) : timelineEvents.length === 0 ? (
            <p className="text-sm text-muted-foreground">No release events recorded yet.</p>
          ) : (
            <div className="space-y-3">
              {timelineEvents.map((event) => (
                <div key={event.id} className="rounded-md border border-border p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline" className="capitalize">
                      {event.event_type.replace(/_/g, ' ')}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      {formatDateTime(event.created_at)}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      Release {event.release_id.slice(0, 8)}
                    </span>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    Strategy {event.release.strategy}, status {event.release.status}, traffic {event.release.traffic_percent}%
                  </div>
                  {(event.actor?.display_name || event.actor?.email || event.actor_id) && (
                    <div className="mt-1 text-xs text-muted-foreground">
                      Actor:{' '}
                      {event.actor?.display_name || event.actor?.email
                        ? `${event.actor?.display_name || 'Unknown'}${event.actor?.email ? ` (${event.actor.email})` : ''}`
                        : event.actor_id?.slice(0, 8)}
                    </div>
                  )}
                  {Boolean(event.metadata_json?.reason) && (
                    <div className="mt-1 text-xs text-muted-foreground">
                      Reason: {String(event.metadata_json.reason)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog
        open={!!pendingAction}
        onOpenChange={(open) => {
          if (!open) {
            setPendingAction(null)
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {pendingAction?.type === 'promote' ? 'Promote release' : 'Rollback release'}
            </DialogTitle>
            <DialogDescription>
              {pendingAction?.type === 'promote'
                ? 'Promote to the next rollout step. You can optionally attach a reason.'
                : 'Rollback requires a reason and will restore the most recent prior stable release when available.'}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="release-action-reason">Reason {pendingAction?.type === 'rollback' ? '(required)' : '(optional)'}</Label>
            <Textarea
              id="release-action-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Enter reason..."
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setPendingAction(null)}>
              Cancel
            </Button>
            <Button onClick={confirmAction} disabled={!canConfirmAction}>
              Confirm
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
