import { Activity, AlertTriangle, ArrowLeft, RefreshCw, Sparkles } from 'lucide-react';
import { Badge } from '@/components/atoms/badge';
import { Button } from '@/components/atoms/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/atoms/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/atoms/table';
import { cn } from '@/lib/utils';
import type { JourneyInstanceDetail, JourneyRuntimeStatus } from '@/types/journeys';
import { alertVariant, formatDateTime, journeyStatusVariant, summarizePayload } from '../utils/journey-helpers';

export function EmptyState({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <Card>
      <CardContent className="flex min-h-[220px] flex-col items-center justify-center gap-3 text-center">
        <div className="rounded-full bg-muted p-3">
          <Sparkles className="h-5 w-5 text-muted-foreground" />
        </div>
        <div className="space-y-1">
          <p className="font-medium">{title}</p>
          <p className="max-w-lg text-sm text-muted-foreground">{description}</p>
        </div>
      </CardContent>
    </Card>
  );
}

export function RuntimeOverview({
  runtime,
  onSweepAbandonment,
  isSweeping,
}: {
  runtime?: JourneyRuntimeStatus;
  onSweepAbandonment: () => void;
  isSweeping: boolean;
}) {
  if (!runtime) {
    return (
      <Card className="border-dashed">
        <CardContent className="py-6 text-sm text-muted-foreground">Loading journey runtime status…</CardContent>
      </Card>
    );
  }

  return (
    <Card className="overflow-hidden">
      <CardHeader className="pb-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <CardTitle className="flex items-center gap-2 text-xl">
              <Activity className="h-5 w-5 text-primary" />
              Journey Runtime
            </CardTitle>
            <CardDescription>
              {runtime.embedded_worker_enabled ? 'Embedded worker mode' : 'External worker mode'} with persisted lease-based jobs.
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={onSweepAbandonment} isLoading={isSweeping}>
              <RefreshCw className="mr-2 h-4 w-4" />
              Sweep abandonment
            </Button>
            <Badge variant={runtime.failed_jobs > 0 ? 'destructive' : 'success'}>
              {runtime.failed_jobs > 0 ? 'Needs attention' : 'Healthy'}
            </Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-3 md:grid-cols-4">
          <div className="rounded-xl border bg-background/80 p-4">
            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Queued</p>
            <p className="mt-2 text-3xl font-semibold">{runtime.queued_jobs}</p>
          </div>
          <div className="rounded-xl border bg-background/80 p-4">
            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Running</p>
            <p className="mt-2 text-3xl font-semibold">{runtime.running_jobs}</p>
          </div>
          <div className="rounded-xl border bg-background/80 p-4">
            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Completed</p>
            <p className="mt-2 text-3xl font-semibold">{runtime.completed_jobs}</p>
          </div>
          <div className="rounded-xl border bg-background/80 p-4">
            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Failed</p>
            <p className="mt-2 text-3xl font-semibold text-red-600">{runtime.failed_jobs}</p>
          </div>
        </div>

        {runtime.alerts.length > 0 && (
          <div className="space-y-2">
            {runtime.alerts.map((alert) => (
              <div
                key={alert.code}
                className={cn(
                  'flex items-start gap-3 rounded-xl border px-4 py-3',
                  alert.severity === 'error'
                    ? 'border-red-200 bg-red-50/80 text-red-900'
                    : 'border-amber-200 bg-amber-50/80 text-amber-900',
                )}
              >
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <Badge variant={alertVariant(alert)}>{alert.kind}</Badge>
                    <span className="text-xs uppercase tracking-[0.18em]">{alert.severity}</span>
                  </div>
                  <p className="text-sm font-medium">{alert.message}</p>
                </div>
              </div>
            ))}
          </div>
        )}

        {runtime.recent_jobs.length > 0 && (
          <div className="overflow-x-auto rounded-xl border bg-background/85">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Job</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Target</TableHead>
                  <TableHead>Submitted</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runtime.recent_jobs.slice(0, 5).map((job) => (
                  <TableRow key={job.job_id}>
                    <TableCell className="font-mono text-xs">{job.kind}</TableCell>
                    <TableCell>
                      <Badge variant={job.status === 'failed' ? 'destructive' : job.status === 'completed' ? 'success' : 'outline'}>
                        {job.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {job.definition_id || job.journey_id || 'system'}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">{formatDateTime(job.submitted_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function JourneyInstanceDetailView({
  detail,
  onBack,
  onReplay,
  isReplaying,
}: {
  detail: JourneyInstanceDetail;
  onBack: () => void;
  onReplay: () => void;
  isReplaying: boolean;
}) {
  const instance = detail.instance;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <Button variant="outline" onClick={onBack}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to Journey List
        </Button>
        <Button onClick={onReplay} isLoading={isReplaying}>
          <RefreshCw className="mr-2 h-4 w-4" />
          Replay Journey
        </Button>
      </div>

      <Card className="overflow-hidden">
        <CardHeader>
          <div className="flex items-start justify-between gap-4">
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <Badge variant={journeyStatusVariant(instance.status)}>
                  {instance.status}
                </Badge>
                {instance.outcome && <Badge variant="outline">{instance.outcome}</Badge>}
              </div>
              <CardTitle>{instance.subject_key}</CardTitle>
              <CardDescription>
                Definition {detail.definition?.name || instance.definition_id} · current milestone {instance.current_milestone_id || '—'}
              </CardDescription>
            </div>
            <div className="grid gap-2 text-right text-sm text-muted-foreground">
              <p>Started: {formatDateTime(instance.started_at)}</p>
              <p>Last activity: {formatDateTime(instance.last_activity_at)}</p>
              <p>Ended: {formatDateTime(instance.ended_at)}</p>
            </div>
          </div>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-4">
          <div className="rounded-xl border bg-background/85 p-4">
            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Definition</p>
            <p className="mt-2 text-sm font-medium">{detail.definition?.slug || instance.definition_id}</p>
          </div>
          <div className="rounded-xl border bg-background/85 p-4">
            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Version</p>
            <p className="mt-2 text-sm font-medium">v{detail.version?.version_number || '—'}</p>
          </div>
          <div className="rounded-xl border bg-background/85 p-4">
            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Milestone Path</p>
            <p className="mt-2 text-sm font-medium">{instance.milestone_path.join(' → ') || '—'}</p>
          </div>
          <div className="rounded-xl border bg-background/85 p-4">
            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Agents</p>
            <p className="mt-2 text-sm font-medium">{instance.latest_agent_id || instance.first_agent_id || '—'}</p>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Touchpoints</CardTitle>
            <CardDescription>Conversations attached to this journey projection.</CardDescription>
          </CardHeader>
          <CardContent>
            {detail.touchpoints.length === 0 ? (
              <p className="text-sm text-muted-foreground">No touchpoints recorded.</p>
            ) : (
              <div className="overflow-x-auto rounded-xl border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Conversation</TableHead>
                      <TableHead>Channel</TableHead>
                      <TableHead>Agent</TableHead>
                      <TableHead>Started</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {detail.touchpoints.map((touchpoint) => (
                      <TableRow key={touchpoint.touchpoint_id}>
                        <TableCell className="font-mono text-xs">{touchpoint.conversation_id}</TableCell>
                        <TableCell>{touchpoint.channel || '—'}</TableCell>
                        <TableCell>{touchpoint.agent_id || '—'}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">{formatDateTime(touchpoint.started_at)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Events</CardTitle>
            <CardDescription>Derived and manual events that shaped the journey state.</CardDescription>
          </CardHeader>
          <CardContent>
            {detail.events.length === 0 ? (
              <p className="text-sm text-muted-foreground">No events recorded.</p>
            ) : (
              <div className="space-y-3">
                {detail.events.map((event) => (
                  <div key={event.journey_event_id} className="rounded-xl border p-4">
                    <div className="flex items-start justify-between gap-4">
                      <div className="space-y-2">
                        <div className="flex items-center gap-2">
                          <Badge variant="outline">{event.event_type}</Badge>
                          <Badge variant="secondary">{event.source}</Badge>
                          {event.milestone_id && <Badge variant="info">{event.milestone_id}</Badge>}
                        </div>
                        <p className="text-sm text-muted-foreground">{summarizePayload(event.payload)}</p>
                      </div>
                      <p className="text-xs text-muted-foreground">{formatDateTime(event.occurred_at)}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
