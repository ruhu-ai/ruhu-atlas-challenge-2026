import type { UseQueryResult } from '@tanstack/react-query';
import { Loader2, RefreshCw } from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
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
import type {
  JourneyChannelMixAnalysis,
  JourneyChannelMixEntry,
  JourneyDropOffAnalysis,
  JourneyDropOffRow,
  JourneyFunnelAnalysis,
  JourneyPathAnalysis,
  JourneyPathRow,
  JourneyTrendAnalysis,
  JourneyTrendPoint,
} from '@/types/journeys';
import { formatShortDate, summarizeMap } from '../utils/journey-helpers';
import { EmptyState } from './JourneyRuntimeViews';

type JourneyAnalyticsTabProps = {
  selectedDefinitionId: string | null;
  selectedDefinitionName?: string;
  funnelQuery: UseQueryResult<JourneyFunnelAnalysis>;
  dropOffQuery: UseQueryResult<JourneyDropOffAnalysis>;
  pathsQuery: UseQueryResult<JourneyPathAnalysis>;
  trendsQuery: UseQueryResult<JourneyTrendAnalysis>;
  channelMixQuery: UseQueryResult<JourneyChannelMixAnalysis>;
  onRebuildAnalytics: (definitionId: string) => void;
  isRebuilding: boolean;
};

export function JourneyAnalyticsTab({
  selectedDefinitionId,
  selectedDefinitionName,
  funnelQuery,
  dropOffQuery,
  pathsQuery,
  trendsQuery,
  channelMixQuery,
  onRebuildAnalytics,
  isRebuilding,
}: JourneyAnalyticsTabProps) {
  if (!selectedDefinitionId) {
    return (
      <EmptyState
        title="Select a definition first"
        description="Definition-scoped analytics require a Journey definition selection from the Definitions tab."
      />
    );
  }

  const trendChartData = (trendsQuery.data?.points || []).map((point: JourneyTrendPoint) => ({
    label: formatShortDate(point.bucket_start),
    opened: point.opened_count,
    completed: point.completed_count,
    abandoned: point.abandoned_count,
  }));

  return (
    <>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h2 className="text-xl font-semibold">{selectedDefinitionName || selectedDefinitionId}</h2>
          <p className="text-sm text-muted-foreground">
            Definition-scoped analytics and snapshot rebuilds for the selected Journey definition.
          </p>
        </div>
        <Button
          variant="outline"
          onClick={() => onRebuildAnalytics(selectedDefinitionId)}
          isLoading={isRebuilding}
        >
          <RefreshCw className="mr-2 h-4 w-4" />
          Rebuild Analytics
        </Button>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardContent className="pt-6">
            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Definition</p>
            <p className="mt-2 text-lg font-semibold">{selectedDefinitionName || selectedDefinitionId}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6">
            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Journeys</p>
            <p className="mt-2 text-lg font-semibold">{funnelQuery.data?.total_journeys ?? '—'}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6">
            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Completed</p>
            <p className="mt-2 text-lg font-semibold">{funnelQuery.data?.completed_journeys ?? '—'}</p>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
        <Card>
          <CardHeader>
            <CardTitle>Funnel</CardTitle>
            <CardDescription>Milestone entry and completion across the selected definition.</CardDescription>
          </CardHeader>
          <CardContent>
            {!funnelQuery.data ? (
              <div className="flex min-h-[240px] items-center justify-center">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : (
              <div className="overflow-x-auto rounded-xl border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Milestone</TableHead>
                      <TableHead>Entered</TableHead>
                      <TableHead>Completed</TableHead>
                      <TableHead>Active</TableHead>
                      <TableHead>Completion</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {funnelQuery.data.stages.map((stage) => (
                      <TableRow key={stage.milestone_id}>
                        <TableCell>
                          <div>
                            <p className="font-medium">{stage.milestone_name}</p>
                            <p className="text-xs text-muted-foreground">#{stage.order_index}</p>
                          </div>
                        </TableCell>
                        <TableCell>{stage.entered_count}</TableCell>
                        <TableCell>{stage.completed_count}</TableCell>
                        <TableCell>{stage.active_count}</TableCell>
                        <TableCell>{stage.completion_rate.toFixed(1)}%</TableCell>
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
            <CardTitle>Channel Mix</CardTitle>
            <CardDescription>Journey and touchpoint volume by channel.</CardDescription>
          </CardHeader>
          <CardContent>
            {!channelMixQuery.data ? (
              <div className="flex min-h-[240px] items-center justify-center">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : channelMixQuery.data.rows.length === 0 ? (
              <p className="text-sm text-muted-foreground">No channel mix data yet.</p>
            ) : (
              <div className="space-y-4">
                <div className="h-[220px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={channelMixQuery.data.rows.map((row: JourneyChannelMixEntry) => ({
                      channel: row.channel,
                      journeys: row.journey_count,
                      touchpoints: row.touchpoint_count,
                    }))}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="channel" />
                      <YAxis />
                      <Tooltip />
                      <Legend />
                      <Bar dataKey="journeys" fill="#2563eb" radius={[6, 6, 0, 0]} />
                      <Bar dataKey="touchpoints" fill="#14b8a6" radius={[6, 6, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
                <div className="overflow-x-auto rounded-xl border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Channel</TableHead>
                        <TableHead>Journeys</TableHead>
                        <TableHead>Touchpoints</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {channelMixQuery.data.rows.map((row) => (
                        <TableRow key={row.channel}>
                          <TableCell>{row.channel}</TableCell>
                          <TableCell>{row.journey_count}</TableCell>
                          <TableCell>{row.touchpoint_count}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Card>
          <CardHeader>
            <CardTitle>Trends</CardTitle>
            <CardDescription>Journey opens, completions, and abandonment across time buckets.</CardDescription>
          </CardHeader>
          <CardContent>
            {!trendsQuery.data ? (
              <div className="flex min-h-[280px] items-center justify-center">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : trendChartData.length === 0 ? (
              <p className="text-sm text-muted-foreground">No trend data yet.</p>
            ) : (
              <div className="h-[280px]">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={trendChartData}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="label" />
                    <YAxis />
                    <Tooltip />
                    <Legend />
                    <Line type="monotone" dataKey="opened" stroke="#2563eb" strokeWidth={2} dot={false} />
                    <Line type="monotone" dataKey="completed" stroke="#16a34a" strokeWidth={2} dot={false} />
                    <Line type="monotone" dataKey="abandoned" stroke="#dc2626" strokeWidth={2} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Drop-off</CardTitle>
              <CardDescription>Where journeys stall or terminate before completion.</CardDescription>
            </CardHeader>
            <CardContent>
              {!dropOffQuery.data ? (
                <div className="flex min-h-[220px] items-center justify-center">
                  <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                </div>
              ) : dropOffQuery.data.rows.length === 0 ? (
                <p className="text-sm text-muted-foreground">No drop-off rows yet.</p>
              ) : (
                <div className="space-y-3">
                  {dropOffQuery.data.rows.map((row: JourneyDropOffRow) => (
                    <div key={row.milestone_id} className="rounded-xl border p-4">
                      <div className="flex items-start justify-between gap-4">
                        <div>
                          <p className="font-medium">{row.milestone_name}</p>
                          <p className="text-xs text-muted-foreground">{row.milestone_id}</p>
                        </div>
                        <Badge variant="destructive">{row.drop_off_count} drop-offs</Badge>
                      </div>
                      <p className="mt-3 text-sm text-muted-foreground">
                        outcomes {summarizeMap(row.outcome_counts)} · active {row.active_count}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Top Paths</CardTitle>
              <CardDescription>Most common milestone progressions.</CardDescription>
            </CardHeader>
            <CardContent>
              {!pathsQuery.data ? (
                <div className="flex min-h-[220px] items-center justify-center">
                  <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                </div>
              ) : pathsQuery.data.rows.length === 0 ? (
                <p className="text-sm text-muted-foreground">No path data yet.</p>
              ) : (
                <div className="space-y-3">
                  {pathsQuery.data.rows.slice(0, 6).map((row: JourneyPathRow, index: number) => (
                    <div key={`${row.path.join('>')}-${index}`} className="rounded-xl border p-4">
                      <div className="flex items-center justify-between gap-4">
                        <p className="font-medium">{row.path.join(' → ') || 'Empty path'}</p>
                        <Badge variant="outline">{row.count}</Badge>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </>
  );
}
