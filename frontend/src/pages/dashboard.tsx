/**
 * Dashboard (Home) Page
 *
 * Platform KPIs, agent performance table, and resolution trend.
 * All data from GET /dashboard/stats (server-side aggregation).
 */

import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'
import { DashboardLayout } from '@/layouts/dashboard-layout'
import { MetricCard } from '@/components/molecules/metric-card'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/atoms/card'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import {
  MetricCardSkeleton,
  TableRowSkeleton,
  Skeleton,
} from '@/components/atoms/skeleton'
import { GitBranch, MessageSquare, CheckCircle2, Clock, Plus } from 'lucide-react'
import { analyticsService } from '@/api/services/analytics.service'
import { formatNumber, formatPercentage, formatDate } from '@/lib/utils'

/** Format seconds into "Xm Ys" display */
function formatHandleTime(seconds: number): string {
  if (seconds <= 0) return '0s'
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

export default function DashboardPage() {
  const navigate = useNavigate()

  const { data, isLoading } = useQuery({
    queryKey: ['home-dashboard-stats'],
    queryFn: () => analyticsService.getHomeDashboardStats({ days: 7 }),
    refetchInterval: 30_000,
  })

  return (
    <DashboardLayout>
      <div className="space-y-8">
        {/* Page Header */}
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Monitor your agents and key performance metrics
          </p>
        </div>

        {/* KPI Cards Grid */}
        {isLoading ? (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <MetricCardSkeleton />
            <MetricCardSkeleton />
            <MetricCardSkeleton />
            <MetricCardSkeleton />
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <MetricCard
              title="Total Agents"
              value={formatNumber(data?.total_agents ?? 0)}
              changeLabel="all time"
              icon={<GitBranch className="h-4 w-4" />}
            />

            <MetricCard
              title="Active Conversations"
              value={formatNumber(data?.active_conversations ?? 0)}
              changeLabel="right now"
              icon={<MessageSquare className="h-4 w-4" />}
            />

            <MetricCard
              title="Resolution Rate"
              value={formatPercentage(data?.resolution_rate ?? 0)}
              changeLabel="all ended conversations"
              icon={<CheckCircle2 className="h-4 w-4" />}
              sparklineColor="#34d399"
            />

            <MetricCard
              title="Avg Handle Time"
              value={formatHandleTime(data?.avg_handle_time_seconds ?? 0)}
              changeLabel="all ended conversations"
              icon={<Clock className="h-4 w-4" />}
              sparklineColor="#34d399"
            />
          </div>
        )}

        {/* Agent Performance Table */}
        <Card>
          <CardHeader>
            <CardTitle>Agent Performance</CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-border text-left text-xs uppercase tracking-wider text-muted-foreground">
                      <th className="pb-3 font-medium">Agent Name</th>
                      <th className="pb-3 font-medium">Status</th>
                      <th className="pb-3 font-medium">Conversations</th>
                      <th className="pb-3 font-medium">Active</th>
                      <th className="pb-3 font-medium">Avg Turns</th>
                      <th className="pb-3 font-medium">Avg Handle Time</th>
                      <th className="pb-3 font-medium">Resolution Rate</th>
                    </tr>
                  </thead>
                  <tbody>
                    <TableRowSkeleton columns={7} />
                    <TableRowSkeleton columns={7} />
                    <TableRowSkeleton columns={7} />
                    <TableRowSkeleton columns={7} />
                  </tbody>
                </table>
              </div>
            ) : !data?.agent_performance || data.agent_performance.length === 0 ? (
              <div className="flex h-52 flex-col items-center justify-center text-muted-foreground">
                <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-muted">
                  <GitBranch className="h-8 w-8" />
                </div>
                <p className="font-medium text-foreground">No agents yet</p>
                <p className="mt-1 text-sm">
                  Create your first agent to start tracking performance
                </p>
                <Button
                  className="mt-4"
                  size="sm"
                  onClick={() => navigate('/agents')}
                >
                  <Plus className="mr-1.5 h-4 w-4" />
                  Create Agent
                </Button>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-border text-left text-xs uppercase tracking-wider text-muted-foreground">
                      <th className="pb-3 font-medium">Agent Name</th>
                      <th className="pb-3 font-medium">Status</th>
                      <th className="pb-3 font-medium">Conversations</th>
                      <th className="pb-3 font-medium">Active</th>
                      <th className="pb-3 font-medium">Avg Turns</th>
                      <th className="pb-3 font-medium">Avg Handle Time</th>
                      <th className="pb-3 font-medium">Resolution Rate</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.agent_performance.map((agent) => (
                      <tr
                        key={agent.agent_id}
                        className="row-hover cursor-pointer border-b border-border/50 last:border-0"
                        onClick={() => navigate(`/agents/${agent.agent_id}/canvas`)}
                      >
                        <td className="py-3.5 text-sm font-medium">{agent.agent_name}</td>
                        <td className="py-3.5">
                          <Badge
                            variant={agent.status === 'published' ? 'success' : 'warning'}
                          >
                            {agent.status === 'published' ? 'Live' : 'Draft'}
                          </Badge>
                        </td>
                        <td className="py-3.5 font-mono text-sm text-muted-foreground">
                          {agent.conversation_count}
                        </td>
                        <td className="py-3.5 font-mono text-sm text-muted-foreground">
                          {agent.active_conversations}
                        </td>
                        <td className="py-3.5 font-mono text-sm text-muted-foreground">
                          {agent.avg_turns_per_conversation.toFixed(1)}
                        </td>
                        <td className="py-3.5 font-mono text-sm text-muted-foreground">
                          {formatHandleTime(agent.avg_handle_time_seconds)}
                        </td>
                        <td className="py-3.5 font-mono text-sm text-muted-foreground">
                          {agent.resolution_rate.toFixed(1)}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Resolution Rate Chart */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle>Resolution Rate — Last 7 Days</CardTitle>
              <div className="flex items-center gap-4 text-xs text-muted-foreground">
                <div className="flex items-center gap-1.5">
                  <span className="inline-block h-2.5 w-2.5 rounded-full bg-primary" />
                  Resolution Rate
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="inline-block h-2.5 w-2.5 rounded-full bg-muted-foreground/40" />
                  Target (80%)
                </div>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <Skeleton className="h-64 w-full rounded-lg" />
            ) : (
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={data?.resolution_trend ?? []} margin={{ top: 5, right: 10, left: -20, bottom: 0 }}>
                    <defs>
                      <linearGradient id="resolutionGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="hsl(14, 80%, 51%)" stopOpacity={0.3} />
                        <stop offset="100%" stopColor="hsl(14, 80%, 51%)" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(30, 2%, 16%)" vertical={false} />
                    <XAxis
                      dataKey="date"
                      tick={{ fill: 'hsl(30, 3%, 55%)', fontSize: 12 }}
                      axisLine={{ stroke: 'hsl(30, 2%, 16%)' }}
                      tickLine={false}
                    />
                    <YAxis
                      domain={[0, 100]}
                      tick={{ fill: 'hsl(30, 3%, 55%)', fontSize: 12 }}
                      axisLine={false}
                      tickLine={false}
                      tickFormatter={(v) => `${v}%`}
                    />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: 'hsl(30, 3%, 9%)',
                        border: '1px solid hsl(30, 2%, 16%)',
                        borderRadius: '8px',
                        fontSize: 12,
                      }}
                      labelStyle={{ color: 'hsl(30, 3%, 55%)' }}
                      formatter={(value: number, name: string) => [
                        `${value}%`,
                        name === 'rate' ? 'Resolution' : 'Target',
                      ]}
                    />
                    <Area
                      type="monotone"
                      dataKey="target"
                      stroke="hsl(30, 3%, 55%)"
                      strokeWidth={1}
                      strokeDasharray="4 4"
                      fill="none"
                      dot={false}
                    />
                    <Area
                      type="monotone"
                      dataKey="rate"
                      stroke="hsl(14, 80%, 51%)"
                      strokeWidth={2}
                      fill="url(#resolutionGrad)"
                      dot={{ r: 3, fill: 'hsl(14, 80%, 51%)', strokeWidth: 0 }}
                      activeDot={{ r: 5, fill: 'hsl(14, 80%, 51%)', strokeWidth: 2, stroke: 'hsl(30, 3%, 9%)' }}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </DashboardLayout>
  )
}
