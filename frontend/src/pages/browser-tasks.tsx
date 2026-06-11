import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, CheckCircle2, Clock, RefreshCw, ShieldCheck, XCircle } from 'lucide-react'
import { toast } from 'sonner'

import { browserTasksService, type BrowserTaskSnapshot, type BrowserTaskState } from '@/api/services/browser-tasks.service'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Card, CardContent } from '@/components/atoms/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
import { DashboardLayout } from '@/layouts/dashboard-layout'
import { cn } from '@/lib/utils'

type StatusFilter = 'all' | BrowserTaskState | 'pending_approval'

const statusOptions: Array<{ value: StatusFilter; label: string }> = [
  { value: 'all', label: 'All tasks' },
  { value: 'pending_approval', label: 'Needs approval' },
  { value: 'queued', label: 'Queued' },
  { value: 'running', label: 'Running' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'cancelled', label: 'Cancelled' },
]

function statusTone(state: string): string {
  switch (state) {
    case 'running':
      return 'border-sky-500/30 bg-sky-500/10 text-sky-500'
    case 'completed':
      return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-500'
    case 'failed':
      return 'border-rose-500/30 bg-rose-500/10 text-rose-500'
    case 'cancelled':
      return 'border-muted-foreground/30 bg-muted text-muted-foreground'
    case 'awaiting_approval':
      return 'border-amber-500/30 bg-amber-500/10 text-amber-500'
    default:
      return 'border-border bg-muted text-muted-foreground'
  }
}

function formatTime(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function latestEvent(snapshot: BrowserTaskSnapshot): string {
  const event = snapshot.recent_events.at(-1)
  return event?.message || snapshot.task.summary || snapshot.task.title
}

function approvalDetail(approval: BrowserTaskSnapshot['approval']): string | null {
  if (!approval) return null
  const context = approval.context || {}
  const pack = typeof context.task_pack_display_name === 'string' ? context.task_pack_display_name : null
  const domains = Array.isArray(context.allowed_domains) ? context.allowed_domains.filter((item): item is string => typeof item === 'string') : []
  const credentials = Array.isArray(context.credential_refs) ? context.credential_refs.length : 0
  const write = context.performs_write === true ? 'write action' : 'read-only access'
  const expires = approval.expires_at ? `expires ${formatTime(approval.expires_at)}` : 'no expiration'
  return [
    approval.kind.replace(/_/g, ' '),
    pack,
    domains.length ? `domains: ${domains.join(', ')}` : null,
    credentials ? `${credentials} credential ${credentials === 1 ? 'ref' : 'refs'}` : null,
    write,
    expires,
  ].filter(Boolean).join(' · ')
}

export default function BrowserTasksPage() {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<StatusFilter>('all')

  const inboxParams = useMemo(() => {
    if (status === 'pending_approval') return { approval_state: 'pending' as const, limit: 100 }
    if (status === 'all') return { limit: 100 }
    return { state: status, limit: 100 }
  }, [status])

  const { data: snapshots = [], isLoading, isFetching } = useQuery({
    queryKey: ['browser-task-inbox', inboxParams],
    queryFn: () => browserTasksService.listInbox(inboxParams),
    refetchInterval: status === 'running' || status === 'pending_approval' ? 5000 : false,
  })

  const { data: taskPacks = [] } = useQuery({
    queryKey: ['browser-task-packs'],
    queryFn: () => browserTasksService.listTaskPacks(),
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['browser-task-inbox'] })
  }

  const approveMutation = useMutation({
    mutationFn: (approvalId: string) => browserTasksService.approve(approvalId, 'approved from operator inbox'),
    onSuccess: () => {
      toast.success('Browser task approved')
      invalidate()
    },
    onError: () => toast.error('Failed to approve browser task'),
  })

  const denyMutation = useMutation({
    mutationFn: (approvalId: string) => browserTasksService.deny(approvalId, 'denied from operator inbox'),
    onSuccess: () => {
      toast.success('Browser task denied')
      invalidate()
    },
    onError: () => toast.error('Failed to deny browser task'),
  })

  const cancelMutation = useMutation({
    mutationFn: (taskId: string) => browserTasksService.cancel(taskId, 'cancelled from operator inbox'),
    onSuccess: () => {
      toast.success('Browser task cancelled')
      invalidate()
    },
    onError: () => toast.error('Failed to cancel browser task'),
  })

  return (
    <DashboardLayout>
      <div className="space-y-5">
        <div className="flex flex-col gap-3 border-b border-border pb-4 md:flex-row md:items-center md:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Browser Tasks</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Monitor browser work, approvals, artifacts, and task-pack execution.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Select value={status} onValueChange={(value) => setStatus(value as StatusFilter)}>
              <SelectTrigger className="w-[180px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {statusOptions.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button variant="outline" size="sm" onClick={invalidate}>
              <RefreshCw className={cn('mr-2 h-4 w-4', isFetching && 'animate-spin')} />
              Refresh
            </Button>
          </div>
        </div>

        <div className="grid gap-3 md:grid-cols-4">
          <Metric label="Task packs" value={taskPacks.length} icon={<ShieldCheck className="h-4 w-4" />} />
          <Metric label="Visible tasks" value={snapshots.length} icon={<Activity className="h-4 w-4" />} />
          <Metric
            label="Awaiting approval"
            value={snapshots.filter((item) => item.approval?.state === 'pending').length}
            icon={<Clock className="h-4 w-4" />}
          />
          <Metric
            label="Active"
            value={snapshots.filter((item) => ['queued', 'running', 'awaiting_approval'].includes(item.task.state)).length}
            icon={<CheckCircle2 className="h-4 w-4" />}
          />
        </div>

        <div className="space-y-3">
          {isLoading ? (
            <div className="rounded-md border border-border p-5 text-sm text-muted-foreground">Loading browser tasks...</div>
          ) : snapshots.length === 0 ? (
            <div className="rounded-md border border-border p-5 text-sm text-muted-foreground">No browser tasks match this filter.</div>
          ) : (
            snapshots.map((snapshot) => (
              <TaskRow
                key={snapshot.task.task_id}
                snapshot={snapshot}
                onApprove={(approvalId) => approveMutation.mutate(approvalId)}
                onDeny={(approvalId) => denyMutation.mutate(approvalId)}
                onCancel={(taskId) => cancelMutation.mutate(taskId)}
                busy={approveMutation.isPending || denyMutation.isPending || cancelMutation.isPending}
              />
            ))
          )}
        </div>
      </div>
    </DashboardLayout>
  )
}

function Metric({ label, value, icon }: { label: string; value: number; icon: React.ReactNode }) {
  return (
    <div className="rounded-md border border-border bg-card px-4 py-3">
      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span>{label}</span>
        {icon}
      </div>
      <div className="mt-2 text-2xl font-semibold">{value}</div>
    </div>
  )
}

function TaskRow({
  snapshot,
  onApprove,
  onDeny,
  onCancel,
  busy,
}: {
  snapshot: BrowserTaskSnapshot
  onApprove: (approvalId: string) => void
  onDeny: (approvalId: string) => void
  onCancel: (taskId: string) => void
  busy: boolean
}) {
  const { task, approval } = snapshot
  const approvalPending = approval?.state === 'pending'
  const cancellable = ['queued', 'running', 'awaiting_approval'].includes(task.state)
  const artifacts = Array.isArray(task.result?.artifacts) ? task.result.artifacts : []

  return (
    <Card className="rounded-md">
      <CardContent className="p-4">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="truncate text-sm font-semibold">{task.title}</h2>
              <Badge variant="outline" className={cn('capitalize', statusTone(task.state))}>
                {task.state.replace(/_/g, ' ')}
              </Badge>
              {approvalPending && (
                <Badge variant="outline" className="border-amber-500/30 bg-amber-500/10 text-amber-500">
                  Approval needed
                </Badge>
              )}
            </div>
            <div className="mt-2 text-sm text-muted-foreground">{latestEvent(snapshot)}</div>
            <div className="mt-3 grid gap-2 text-xs text-muted-foreground md:grid-cols-4">
              <span>Task {task.task_id}</span>
              <span>Conversation {task.conversation_id}</span>
              <span>Pack {task.task_pack_id || 'manual'}</span>
              <span>Updated {formatTime(task.updated_at)}</span>
            </div>
            {artifacts.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {artifacts.map((artifact, index) => {
                  const item = artifact as Record<string, unknown>
                  const label = String(item.filename || item.artifact_id || `Artifact ${index + 1}`)
                  const href = typeof item.internal_download_url === 'string' ? item.internal_download_url : null
                  return href ? (
                    <a key={label} href={href} className="text-xs font-medium text-primary hover:underline">
                      {label}
                    </a>
                  ) : (
                    <span key={label} className="text-xs text-muted-foreground">{label}</span>
                  )
                })}
              </div>
            )}
            {approvalPending && (
              <div className="mt-3 rounded-md border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
                {approval?.prompt}
                <div className="mt-1 text-muted-foreground">{approvalDetail(approval)}</div>
              </div>
            )}
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            {approvalPending && approval && (
              <>
                <Button size="sm" onClick={() => onApprove(approval.approval_id)} disabled={busy}>
                  <CheckCircle2 className="mr-2 h-4 w-4" />
                  Approve
                </Button>
                <Button size="sm" variant="outline" onClick={() => onDeny(approval.approval_id)} disabled={busy}>
                  <XCircle className="mr-2 h-4 w-4" />
                  Deny
                </Button>
              </>
            )}
            {cancellable && (
              <Button size="sm" variant="outline" onClick={() => onCancel(task.task_id)} disabled={busy}>
                Cancel
              </Button>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
