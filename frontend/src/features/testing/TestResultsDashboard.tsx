/**
 * Test Results Dashboard
 *
 * Display test results with metrics, transcripts, and analysis.
 * View past test runs and compare performance.
 */

import { useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/atoms/card'
import { Button } from '@/components/atoms/button'
import { Badge } from '@/components/atoms/badge'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/atoms/tabs'
import {
  CheckCircle,
  XCircle,
  Clock,
  Download,
  FileText,
  TrendingUp,
  TrendingDown,
  BarChart3,
  MessageSquare,
} from 'lucide-react'
import { TestRun, TestScenario } from './types'
import { Line, LineChart, Bar, BarChart, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'

interface TestResultsDashboardProps {
  testRuns: TestRun[]
  scenarios: TestScenario[]
  onExportResults: (runId: string) => void
  onViewTranscript: (runId: string) => void
}

export function TestResultsDashboard({
  testRuns,
  scenarios,
  onExportResults,
  onViewTranscript,
}: TestResultsDashboardProps) {
  const [selectedRun, setSelectedRun] = useState<TestRun | null>(null)

  // Calculate summary metrics
  const safeTestRuns = Array.isArray(testRuns) ? testRuns : []
  const totalRuns = safeTestRuns.length
  const passedRuns = safeTestRuns.filter((r) => r.status === 'completed' && r.metadata.failedSteps === 0).length
  const failedRuns = safeTestRuns.filter((r) => r.status === 'failed' || r.metadata.failedSteps > 0).length
  const passRate = totalRuns > 0 ? Math.round((passedRuns / totalRuns) * 100) : 0
  const avgDuration = safeTestRuns.length > 0
    ? Math.round(safeTestRuns.reduce((sum, r) => sum + (r.duration || 0), 0) / safeTestRuns.length)
    : 0

  // Prepare chart data
  const trendData = safeTestRuns.slice(-10).map((run, index) => ({
    name: `Run ${index + 1}`,
    passed: run.metadata.passedSteps,
    failed: run.metadata.failedSteps,
    duration: run.duration || 0,
  }))

  const getScenarioName = (scenarioId: string) => {
    return scenarios.find((s) => s.id === scenarioId)?.name || 'Unknown'
  }

  return (
    <div className="space-y-6">
      {/* Summary Cards */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card className="glass-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Test Runs</CardTitle>
            <BarChart3 className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{totalRuns}</div>
            <p className="text-xs text-muted-foreground">Across all scenarios</p>
          </CardContent>
        </Card>

        <Card className="glass-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Pass Rate</CardTitle>
            {passRate >= 80 ? (
              <TrendingUp className="h-4 w-4 text-green-500" />
            ) : (
              <TrendingDown className="h-4 w-4 text-red-500" />
            )}
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{passRate}%</div>
            <p className="text-xs text-muted-foreground">
              {passedRuns} passed, {failedRuns} failed
            </p>
          </CardContent>
        </Card>

        <Card className="glass-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Avg Duration</CardTitle>
            <Clock className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{Math.round(avgDuration / 1000)}s</div>
            <p className="text-xs text-muted-foreground">Per test run</p>
          </CardContent>
        </Card>

        <Card className="glass-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Scenarios Tested</CardTitle>
            <FileText className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {new Set(safeTestRuns.map((r) => r.scenarioId)).size}
            </div>
            <p className="text-xs text-muted-foreground">Unique scenarios</p>
          </CardContent>
        </Card>
      </div>

      <Tabs defaultValue="overview" className="space-y-4">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="history">Test History</TabsTrigger>
          <TabsTrigger value="trends">Trends</TabsTrigger>
        </TabsList>

        {/* Overview Tab */}
        <TabsContent value="overview" className="space-y-4">
          {/* Pass/Fail Chart */}
          <Card className="glass-card">
            <CardHeader>
              <CardTitle>Test Results Trend</CardTitle>
              <CardDescription>Pass/fail rate over last 10 runs</CardDescription>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={trendData}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                  <XAxis dataKey="name" className="fill-muted-foreground" stroke="currentColor" />
                  <YAxis className="fill-muted-foreground" stroke="currentColor" />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: 'hsl(var(--card))',
                      border: '1px solid hsl(var(--border))',
                      borderRadius: '8px',
                      color: 'hsl(var(--foreground))',
                    }}
                  />
                  <Legend />
                  <Bar dataKey="passed" fill="#10B981" name="Passed Steps" />
                  <Bar dataKey="failed" fill="#EF4444" name="Failed Steps" />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          {/* Duration Chart */}
          <Card className="glass-card">
            <CardHeader>
              <CardTitle>Performance Trend</CardTitle>
              <CardDescription>Test execution duration over time</CardDescription>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={250}>
                <LineChart data={trendData}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                  <XAxis dataKey="name" className="fill-muted-foreground" stroke="currentColor" />
                  <YAxis className="fill-muted-foreground" stroke="currentColor" />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: 'hsl(var(--card))',
                      border: '1px solid hsl(var(--border))',
                      borderRadius: '8px',
                      color: 'hsl(var(--foreground))',
                    }}
                  />
                  <Line
                    type="monotone"
                    dataKey="duration"
                    stroke="#6366F1"
                    strokeWidth={2}
                    dot={{ fill: '#6366F1' }}
                    name="Duration (ms)"
                  />
                </LineChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Test History Tab */}
        <TabsContent value="history" className="space-y-4">
          {safeTestRuns.length === 0 ? (
            <Card className="glass-card">
              <CardContent className="flex flex-col items-center justify-center py-12">
                <FileText className="h-12 w-12 text-muted-foreground mb-4" />
                <h3 className="text-lg font-semibold mb-2">No test runs yet</h3>
                <p className="text-sm text-muted-foreground">
                  Run your first test scenario to see results here
                </p>
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-3">
              {testRuns
                .sort((a, b) => new Date(b.startedAt || 0).getTime() - new Date(a.startedAt || 0).getTime())
                .map((run) => (
                  <Card
                    key={run.id}
                    className={`glass-card cursor-pointer transition-all hover:border-primary/50 ${
                      selectedRun?.id === run.id ? 'border-primary bg-primary/5' : ''
                    }`}
                    onClick={() => setSelectedRun(run)}
                  >
                    <CardContent className="p-4">
                      <div className="flex items-center justify-between">
                        <div className="flex-1">
                          <div className="flex items-center gap-3 mb-2">
                            <h4 className="font-medium">{getScenarioName(run.scenarioId)}</h4>
                            {run.status === 'completed' && run.metadata.failedSteps === 0 ? (
                              <Badge className="bg-green-500/20 text-green-600 dark:text-green-400 border-green-500/30">
                                <CheckCircle className="mr-1 h-3 w-3" />
                                Passed
                              </Badge>
                            ) : (
                              <Badge className="bg-red-500/20 text-red-600 dark:text-red-400 border-red-500/30">
                                <XCircle className="mr-1 h-3 w-3" />
                                Failed
                              </Badge>
                            )}
                          </div>
                          <div className="flex items-center gap-4 text-sm text-muted-foreground">
                            <span>
                              {run.metadata.passedSteps}/{run.metadata.totalSteps} passed
                            </span>
                            <span>{Math.round((run.duration || 0) / 1000)}s</span>
                            <span>
                              {run.startedAt && new Date(run.startedAt).toLocaleString()}
                            </span>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={(e) => {
                              e.stopPropagation()
                              onViewTranscript(run.id)
                            }}
                          >
                            <MessageSquare className="mr-2 h-4 w-4" />
                            Transcript
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={(e) => {
                              e.stopPropagation()
                              onExportResults(run.id)
                            }}
                          >
                            <Download className="mr-2 h-4 w-4" />
                            Export
                          </Button>
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                ))}
            </div>
          )}
        </TabsContent>

        {/* Trends Tab */}
        <TabsContent value="trends" className="space-y-4">
          <Card className="glass-card">
            <CardHeader>
              <CardTitle>Performance Insights</CardTitle>
              <CardDescription>Analysis of test execution patterns</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {failedRuns > 0 ? (
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="p-4 rounded-lg border border-border bg-card/50">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium">Failure Rate</span>
                      <XCircle className="h-4 w-4 text-red-600 dark:text-red-400" />
                    </div>
                    <p className="text-2xl font-bold text-red-600 dark:text-red-400">
                      {Math.round((failedRuns / totalRuns) * 100)}%
                    </p>
                    <p className="text-xs text-muted-foreground mt-1">
                      {failedRuns} of {totalRuns} runs failed
                    </p>
                  </div>
                  <div className="p-4 rounded-lg border border-border bg-card/50">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium">Avg Duration</span>
                      <Clock className="h-4 w-4 text-muted-foreground" />
                    </div>
                    <p className="text-2xl font-bold">
                      {Math.round(avgDuration / 1000)}s
                    </p>
                    <p className="text-xs text-muted-foreground mt-1">
                      Average test execution time
                    </p>
                  </div>
                </div>
              ) : totalRuns > 0 ? (
                <div className="flex flex-col items-center justify-center py-8 text-center">
                  <CheckCircle className="h-12 w-12 text-emerald-500 mb-4" />
                  <h3 className="text-lg font-semibold mb-1">All tests passing</h3>
                  <p className="text-sm text-muted-foreground">
                    {passedRuns} runs completed successfully with a {passRate}% pass rate
                  </p>
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center py-8 text-center">
                  <BarChart3 className="h-12 w-12 text-muted-foreground mb-4" />
                  <h3 className="text-lg font-semibold mb-1">No test data yet</h3>
                  <p className="text-sm text-muted-foreground">
                    Run test scenarios to see performance insights
                  </p>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}
