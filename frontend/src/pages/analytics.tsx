/**
 * Analytics Dashboard Page - Enhanced
 *
 * Comprehensive data visualization for agent performance and conversation metrics.
 * Features real API integration, advanced charts, and export functionality.
 */

import { useState } from 'react'
import { toast } from 'sonner'
import { useQuery } from '@tanstack/react-query'
import { DashboardLayout } from '@/layouts/dashboard-layout'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/atoms/card'
import { MetricCard } from '@/components/molecules/metric-card'
import { Button } from '@/components/atoms/button'
import { Badge } from '@/components/atoms/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/atoms/tabs'
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import {
  Download,
  RefreshCw,
  Phone,
  Clock,
  TrendingUp,
  Activity,
  MessageSquare,
  Users,
  Zap,
  AlertCircle,
  CheckCircle,
  XCircle,
  Loader2,
} from 'lucide-react'
import { analyticsService } from '@/api/services/analytics.service'

/** Convert date range shorthand to days count */
function getDateRangeDays(range: string): number {
  switch (range) {
    case '24h': return 1
    case '7d':  return 7
    case '30d': return 30
    case '90d': return 90
    default:    return 30
  }
}

export default function AnalyticsPage() {
  const [dateRange, setDateRange] = useState('30d')
  const [selectedAgent, setSelectedAgent] = useState<string | undefined>(undefined)
  const [activeTab, setActiveTab] = useState('overview')

  const days = getDateRangeDays(dateRange)

  // Real-time dashboard stats
  const { data: dashboardStats, isLoading: statsLoading, refetch: refetchStats } = useQuery({
    queryKey: ['dashboard-stats', selectedAgent, dateRange],
    queryFn: () => analyticsService.getDashboardStats({ agent_id: selectedAgent }),
    refetchInterval: 30000, // Auto-refresh every 30 seconds
  })

  // Conversation volume trend
  const { data: conversationVolume, isLoading: volumeLoading } = useQuery({
    queryKey: ['conversation-volume', selectedAgent, dateRange],
    queryFn: () => analyticsService.getConversationVolume({ agent_id: selectedAgent }),
  })

  // Resolution rate trend
  const { data: resolutionTrend, isLoading: resolutionLoading } = useQuery({
    queryKey: ['resolution-trend', selectedAgent, dateRange],
    queryFn: () => analyticsService.getResolutionRateTrend({ agent_id: selectedAgent, days }),
  })

  // Topic distribution
  const { data: topicDistribution, isLoading: topicsLoading } = useQuery({
    queryKey: ['topic-distribution', selectedAgent, dateRange],
    queryFn: () => analyticsService.getTopicDistribution({ agent_id: selectedAgent, limit: 10 }),
  })

  // Sentiment breakdown
  const { data: sentimentBreakdown, isLoading: sentimentLoading } = useQuery({
    queryKey: ['sentiment-breakdown', selectedAgent, dateRange],
    queryFn: () => analyticsService.getSentimentBreakdown({ agent_id: selectedAgent }),
  })

  // Agent performance comparison
  const { data: agentComparison, isLoading: agentLoading } = useQuery({
    queryKey: ['agent-comparison'],
    queryFn: () => analyticsService.getAgentPerformanceComparison(),
  })

  // Conversations list
  const { data: conversations = [], isLoading: conversationsLoading } = useQuery({
    queryKey: ['conversations', selectedAgent],
    queryFn: () => analyticsService.listConversations({ agent_id: selectedAgent, limit: 100 }),
  })

  const handleExportCSV = async (dataType: string) => {
    try {
      const csvContent = await analyticsService.exportToCSV(dataType, { agent_id: selectedAgent })
      const blob = new Blob([csvContent], { type: 'text/csv' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${dataType}-${new Date().toISOString().split('T')[0]}.csv`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (error) {
      console.error('Export failed:', error)
      toast.error('Export failed. Please try again.')
    }
  }

  const handleRefresh = () => {
    refetchStats()
  }

  const formatDuration = (seconds: number | null) => {
    if (seconds === null) return 'N/A'
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins}m ${secs}s`
  }

  const formatNumber = (num: number | undefined) => {
    if (num === undefined) return '0'
    return num.toLocaleString()
  }

  const formatPercentage = (num: number | undefined) => {
    if (num === undefined) return '0%'
    return `${num.toFixed(1)}%`
  }

  // Prepare sentiment pie chart data
  const sentimentPieData = sentimentBreakdown
    ? [
        { name: 'Positive', value: sentimentBreakdown.positive, color: '#22c55e' },
        { name: 'Neutral', value: sentimentBreakdown.neutral, color: '#64748b' },
        { name: 'Negative', value: sentimentBreakdown.negative, color: '#ef4444' },
      ]
    : []

  return (
    <DashboardLayout>
      <div className="space-y-6">
        {/* Page Header */}
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-3xl font-bold tracking-tight">
                Analytics Dashboard
              </h1>
              {statsLoading && <Loader2 className="h-5 w-5 animate-spin text-primary" />}
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              Real-time performance metrics and conversation analytics
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={handleRefresh}>
              <RefreshCw className="mr-2 h-4 w-4" />
              Refresh
            </Button>
            <Select value="conversations" onValueChange={handleExportCSV}>
              <SelectTrigger className="w-40">
                <Download className="mr-2 h-4 w-4" />
                <SelectValue placeholder="Export" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="conversations">Conversations</SelectItem>
                <SelectItem value="metrics">Metrics</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        {/* Filters */}
        <Card className="glass-card">
          <CardContent className="p-4">
            <div className="flex flex-wrap gap-4">
              <div className="w-48">
                <Select value={dateRange} onValueChange={setDateRange}>
                  <SelectTrigger>
                    <SelectValue placeholder="Date range" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="24h">Last 24 Hours</SelectItem>
                    <SelectItem value="7d">Last 7 Days</SelectItem>
                    <SelectItem value="30d">Last 30 Days</SelectItem>
                    <SelectItem value="90d">Last 90 Days</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="w-48">
                <Select value={selectedAgent || 'all'} onValueChange={(v) => setSelectedAgent(v === 'all' ? undefined : v)}>
                  <SelectTrigger>
                    <SelectValue placeholder="Agent" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Agents</SelectItem>
                    {agentComparison?.map((agent) => (
                      <SelectItem key={agent.agent_id} value={agent.agent_id}>
                        {agent.agent_name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Real-Time Metrics Section */}
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Activity className="h-5 w-5 text-primary" />
            <h2 className="text-lg font-semibold">Real-Time Metrics</h2>
            <Badge variant="outline" className="ml-2">
              <div className="mr-1 h-2 w-2 rounded-full bg-green-500 animate-pulse" />
              Live
            </Badge>
          </div>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <MetricCard
              title="Active Conversations"
              value={formatNumber(dashboardStats?.active_conversations)}
              icon={<MessageSquare className="h-4 w-4 text-blue-600 dark:text-blue-400" />}
            />
            <MetricCard
              title="Active Voice Calls"
              value={formatNumber(dashboardStats?.active_voice_sessions)}
              icon={<Phone className="h-4 w-4 text-green-600 dark:text-green-400" />}
            />
            <MetricCard
              title="Today's Conversations"
              value={formatNumber(dashboardStats?.total_conversations_today)}
              icon={<TrendingUp className="h-4 w-4 text-purple-600 dark:text-purple-400" />}
            />
            <MetricCard
              title="Today's Voice Calls"
              value={formatNumber(dashboardStats?.total_voice_calls_today)}
              icon={<Phone className="h-4 w-4 text-primary" />}
            />
          </div>
        </div>

        {/* Performance Metrics Section */}
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Zap className="h-5 w-5 text-yellow-600 dark:text-yellow-400" />
            <h2 className="text-lg font-semibold">Performance Metrics</h2>
          </div>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <MetricCard
              title="Avg Response Time"
              value={`${Math.round(dashboardStats?.avg_response_time_ms || 0)}ms`}
              icon={<Clock className="h-4 w-4 text-orange-600 dark:text-orange-400" />}
            />
            <MetricCard
              title="Resolution Rate"
              value={formatPercentage(dashboardStats?.avg_resolution_rate)}
              icon={<CheckCircle className="h-4 w-4 text-green-600 dark:text-green-400" />}
            />
            <MetricCard
              title="Uptime"
              value={formatPercentage(dashboardStats?.uptime_percentage)}
              icon={<Activity className="h-4 w-4 text-cyan-600 dark:text-cyan-400" />}
            />
            <MetricCard
              title="Error Rate"
              value={formatPercentage(dashboardStats?.error_rate)}
              icon={<AlertCircle className="h-4 w-4 text-red-600 dark:text-red-400" />}
            />
          </div>
        </div>

        {/* Tabs for Different Views */}
        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="overview">
              <TrendingUp className="mr-2 h-4 w-4" />
              Overview
            </TabsTrigger>
            <TabsTrigger value="conversations">
              <MessageSquare className="mr-2 h-4 w-4" />
              Conversations
            </TabsTrigger>
            <TabsTrigger value="agents">
              <Users className="mr-2 h-4 w-4" />
              Agent Comparison
            </TabsTrigger>
          </TabsList>

          {/* Overview Tab */}
          <TabsContent value="overview" className="space-y-4 mt-4">
            {/* Charts Row 1 */}
            <div className="grid gap-4 lg:grid-cols-2">
              {/* Conversation Volume */}
              <Card className="glass-card">
                <CardHeader>
                  <CardTitle>Conversation Volume (24 Hours)</CardTitle>
                </CardHeader>
                <CardContent>
                  {volumeLoading ? (
                    <div className="flex h-[300px] items-center justify-center">
                      <Loader2 className="h-8 w-8 animate-spin text-primary" />
                    </div>
                  ) : (
                    <ResponsiveContainer width="100%" height={300}>
                      <LineChart data={conversationVolume?.hourly || []}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#333" />
                        <XAxis dataKey="timestamp" stroke="#888" tickFormatter={(val) => new Date(val).getHours() + ':00'} />
                        <YAxis stroke="#888" />
                        <Tooltip
                          contentStyle={{
                            backgroundColor: '#1a1a1a',
                            border: '1px solid #333',
                          }}
                          labelFormatter={(val) => new Date(val).toLocaleTimeString()}
                        />
                        <Legend />
                        <Line
                          type="monotone"
                          dataKey="value"
                          stroke="#818cf8"
                          strokeWidth={2}
                          name="Conversations"
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  )}
                </CardContent>
              </Card>

              {/* Resolution Rate Trend */}
              <Card className="glass-card">
                <CardHeader>
                  <CardTitle>Resolution Rate Trend (7 Days)</CardTitle>
                </CardHeader>
                <CardContent>
                  {resolutionLoading ? (
                    <div className="flex h-[300px] items-center justify-center">
                      <Loader2 className="h-8 w-8 animate-spin text-primary" />
                    </div>
                  ) : (
                    <ResponsiveContainer width="100%" height={300}>
                      <LineChart data={resolutionTrend || []}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#333" />
                        <XAxis dataKey="date" stroke="#888" />
                        <YAxis stroke="#888" />
                        <Tooltip
                          contentStyle={{
                            backgroundColor: '#1a1a1a',
                            border: '1px solid #333',
                          }}
                        />
                        <Legend />
                        <Line
                          type="monotone"
                          dataKey="resolved"
                          stroke="#22c55e"
                          strokeWidth={2}
                          name="Resolved"
                        />
                        <Line
                          type="monotone"
                          dataKey="unresolved"
                          stroke="#ef4444"
                          strokeWidth={2}
                          name="Unresolved"
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  )}
                </CardContent>
              </Card>
            </div>

            {/* Charts Row 2 */}
            <div className="grid gap-4 lg:grid-cols-2">
              {/* Topic Distribution */}
              <Card className="glass-card">
                <CardHeader>
                  <CardTitle>Topics Distribution</CardTitle>
                </CardHeader>
                <CardContent>
                  {topicsLoading ? (
                    <div className="flex h-[300px] items-center justify-center">
                      <Loader2 className="h-8 w-8 animate-spin text-primary" />
                    </div>
                  ) : (
                    <ResponsiveContainer width="100%" height={300}>
                      <BarChart data={topicDistribution || []}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#333" />
                        <XAxis dataKey="topic" stroke="#888" />
                        <YAxis stroke="#888" />
                        <Tooltip
                          contentStyle={{
                            backgroundColor: '#1a1a1a',
                            border: '1px solid #333',
                          }}
                        />
                        <Legend />
                        <Bar dataKey="count" fill="#818cf8" name="Count" />
                      </BarChart>
                    </ResponsiveContainer>
                  )}
                </CardContent>
              </Card>

              {/* Sentiment Distribution */}
              <Card className="glass-card">
                <CardHeader>
                  <CardTitle>Sentiment Distribution</CardTitle>
                </CardHeader>
                <CardContent>
                  {sentimentLoading ? (
                    <div className="flex h-[300px] items-center justify-center">
                      <Loader2 className="h-8 w-8 animate-spin text-primary" />
                    </div>
                  ) : (
                    <ResponsiveContainer width="100%" height={300}>
                      <PieChart>
                        <Pie
                          data={sentimentPieData}
                          cx="50%"
                          cy="50%"
                          labelLine={false}
                          label={({ name, percent }) =>
                            `${name}: ${(percent * 100).toFixed(0)}%`
                          }
                          outerRadius={100}
                          fill="#8884d8"
                          dataKey="value"
                        >
                          {sentimentPieData.map((entry, index) => (
                            <Cell key={`cell-${index}`} fill={entry.color} />
                          ))}
                        </Pie>
                        <Tooltip
                          contentStyle={{
                            backgroundColor: '#1a1a1a',
                            border: '1px solid #333',
                          }}
                        />
                      </PieChart>
                    </ResponsiveContainer>
                  )}
                </CardContent>
              </Card>
            </div>
          </TabsContent>

          {/* Conversations Tab */}
          <TabsContent value="conversations" className="space-y-4 mt-4">
            <Card className="glass-card">
              <CardHeader>
                <CardTitle>Conversation Analytics</CardTitle>
              </CardHeader>
              <CardContent>
                {conversationsLoading ? (
                  <div className="flex h-[400px] items-center justify-center">
                    <Loader2 className="h-8 w-8 animate-spin text-primary" />
                  </div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full">
                      <thead>
                        <tr className="border-b border-border text-left text-sm text-muted-foreground">
                          <th className="pb-3 font-medium">ID</th>
                          <th className="pb-3 font-medium">Channel</th>
                          <th className="pb-3 font-medium">Status</th>
                          <th className="pb-3 font-medium">Duration</th>
                          <th className="pb-3 font-medium">Sentiment</th>
                          <th className="pb-3 font-medium">Resolution</th>
                          <th className="pb-3 font-medium">Started At</th>
                        </tr>
                      </thead>
                      <tbody>
                        {conversations.slice(0, 20).map((conv) => (
                          <tr
                            key={conv.id}
                            className="border-b border-border last:border-0 hover:bg-accent/50"
                          >
                            <td className="py-3 font-mono text-xs">{conv.id.slice(0, 8)}</td>
                            <td className="py-3">
                              <Badge variant="outline">{conv.channel}</Badge>
                            </td>
                            <td className="py-3">
                              <Badge
                                variant={
                                  conv.status === 'completed' ? 'default' :
                                  conv.status === 'active' ? 'secondary' : 'destructive'
                                }
                              >
                                {conv.status}
                              </Badge>
                            </td>
                            <td className="py-3 text-sm text-muted-foreground">
                              {formatDuration(conv.duration_seconds)}
                            </td>
                            <td className="py-3">
                              {conv.sentiment_score !== null ? (
                                <span className={`text-sm ${
                                  conv.sentiment_score > 0.3 ? 'text-green-500' :
                                  conv.sentiment_score < -0.3 ? 'text-red-500' : 'text-gray-500'
                                }`}>
                                  {conv.sentiment_score > 0.3 ? '😊 Positive' :
                                   conv.sentiment_score < -0.3 ? '😞 Negative' : '😐 Neutral'}
                                </span>
                              ) : (
                                <span className="text-sm text-muted-foreground">N/A</span>
                              )}
                            </td>
                            <td className="py-3">
                              {conv.outcome === 'resolved' ? (
                                <CheckCircle className="h-4 w-4 text-green-500" />
                              ) : (
                                <XCircle className="h-4 w-4 text-gray-500" />
                              )}
                            </td>
                            <td className="py-3 text-sm text-muted-foreground">
                              {new Date(conv.started_at).toLocaleString()}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>

                    {conversations.length === 0 && (
                      <div className="py-12 text-center text-muted-foreground">
                        No conversations found
                      </div>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {/* Agent Comparison Tab */}
          <TabsContent value="agents" className="space-y-4 mt-4">
            <Card className="glass-card">
              <CardHeader>
                <CardTitle>Agent Performance Comparison</CardTitle>
              </CardHeader>
              <CardContent>
                {agentLoading ? (
                  <div className="flex h-[400px] items-center justify-center">
                    <Loader2 className="h-8 w-8 animate-spin text-primary" />
                  </div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full">
                      <thead>
                        <tr className="border-b border-border text-left text-sm text-muted-foreground">
                          <th className="pb-3 font-medium">Agent</th>
                          <th className="pb-3 font-medium">Conversations</th>
                          <th className="pb-3 font-medium">Avg Duration</th>
                          <th className="pb-3 font-medium">Avg Sentiment</th>
                          <th className="pb-3 font-medium">Resolution Rate</th>
                          <th className="pb-3 font-medium">Uptime</th>
                        </tr>
                      </thead>
                      <tbody>
                        {agentComparison?.map((agent) => (
                          <tr
                            key={agent.agent_id}
                            className="border-b border-border last:border-0 hover:bg-accent/50"
                          >
                            <td className="py-3 font-medium">{agent.agent_name}</td>
                            <td className="py-3 text-sm">{formatNumber(agent.total_conversations)}</td>
                            <td className="py-3 text-sm text-muted-foreground">
                              {formatDuration(Math.round(agent.avg_duration_seconds))}
                            </td>
                            <td className="py-3">
                              <span className={`text-sm ${
                                agent.avg_sentiment_score > 0.3 ? 'text-green-500' :
                                agent.avg_sentiment_score < -0.3 ? 'text-red-500' : 'text-gray-500'
                              }`}>
                                {agent.avg_sentiment_score.toFixed(2)}
                              </span>
                            </td>
                            <td className="py-3 text-sm">{formatPercentage(agent.resolution_rate)}</td>
                            <td className="py-3 text-sm">{formatPercentage(agent.uptime_percentage)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>

                    {(!agentComparison || agentComparison.length === 0) && (
                      <div className="py-12 text-center text-muted-foreground">
                        No agent data available
                      </div>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </DashboardLayout>
  )
}
