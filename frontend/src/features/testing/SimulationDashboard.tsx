/**
 * Evaluation Dashboard
 *
 * Displays evaluation statistics with pass rate, passed/failed counts,
 * and test case table.
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/atoms/card'
import { Button } from '@/components/atoms/button'
import { Badge } from '@/components/atoms/badge'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/atoms/dialog'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/atoms/table'
import {
  CheckCircle2,
  XCircle,
  Clock,
  Play,
  RefreshCw,
  ChevronDown,
  Plus,
  Trash2,
} from 'lucide-react'
import { testingService, type TestRunProgress } from '@/api/services/testing.service'
import { cn } from '@/lib/utils'

interface SimulationDashboardProps {
  agentId: string
  agentName?: string
}

// ── Create Test Case Dialog ────────────────────────────────────────────────────
// First-principles form: a test case is just a name + a sequence of user messages.
// Everything else (type, category, validation) defaults to sensible values.

interface Turn {
  id: number
  userMessage: string
}

function CreateTestCaseDialog({
  agentId,
  open,
  onClose,
  onCreated,
}: {
  agentId: string
  open: boolean
  onClose: () => void
  onCreated: () => void
}) {
  const [name, setName] = useState('')
  const [turns, setTurns] = useState<Turn[]>([{ id: 1, userMessage: '' }])
  const nextId = useRef(2)

  const mutation = useMutation({
    mutationFn: () =>
      testingService.createTestCase({
        agent_id: agentId,
        name: name.trim(),
        test_type: 'functional',
        category: 'simulation',
        priority: 'medium',
        is_automated: true,
        input_messages: turns
          .filter((t) => t.userMessage.trim())
          .map((t, i) => ({ role: 'user', content: t.userMessage.trim(), turn: i + 1 })),
        success_criteria: { min_messages: turns.filter((t) => t.userMessage.trim()).length },
        validation_rules: [],
      }),
    onSuccess: () => {
      setName('')
      setTurns([{ id: 1, userMessage: '' }])
      nextId.current = 2
      onCreated()
      onClose()
    },
  })

  const addTurn = () => {
    setTurns((prev) => [...prev, { id: nextId.current++, userMessage: '' }])
  }

  const removeTurn = (id: number) => {
    setTurns((prev) => prev.filter((t) => t.id !== id))
  }

  const updateTurn = (id: number, value: string) => {
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, userMessage: value } : t)))
  }

  const valid = name.trim().length > 0 && turns.some((t) => t.userMessage.trim())

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>New test case</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-1.5">
            <Label htmlFor="tc-name">Name</Label>
            <Input
              id="tc-name"
              placeholder="e.g. Book a meeting — happy path"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
            />
          </div>

          <div className="space-y-2">
            <Label>User messages (conversation turns)</Label>
            <p className="text-xs text-muted-foreground">
              Enter what the user says in each turn. The agent's responses are evaluated at runtime.
            </p>
            {turns.map((turn, i) => (
              <div key={turn.id} className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground w-6 shrink-0">#{i + 1}</span>
                <Input
                  placeholder={`User message ${i + 1}`}
                  value={turn.userMessage}
                  onChange={(e) => updateTurn(turn.id, e.target.value)}
                />
                {turns.length > 1 && (
                  <button
                    type="button"
                    onClick={() => removeTurn(turn.id)}
                    className="text-muted-foreground hover:text-destructive shrink-0"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                )}
              </div>
            ))}
            <Button type="button" variant="ghost" size="sm" onClick={addTurn} className="gap-1">
              <Plus className="h-3.5 w-3.5" />
              Add turn
            </Button>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={!valid || mutation.isPending}
          >
            {mutation.isPending ? 'Creating…' : 'Create test case'}
          </Button>
        </DialogFooter>

        {mutation.isError && (
          <p className="text-sm text-destructive mt-1">
            Failed to create: {(mutation.error as Error).message}
          </p>
        )}
      </DialogContent>
    </Dialog>
  )
}

function formatTimeAgo(dateString: string | undefined): string {
  if (!dateString) return 'Never'

  const date = new Date(dateString)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffSec = Math.floor(diffMs / 1000)
  const diffMin = Math.floor(diffSec / 60)
  const diffHour = Math.floor(diffMin / 60)
  const diffDay = Math.floor(diffHour / 24)

  if (diffSec < 60) return 'just now'
  if (diffMin < 60) return `${diffMin} min ago`
  if (diffHour < 24) return `${diffHour} hour${diffHour > 1 ? 's' : ''} ago`
  if (diffDay < 7) return `${diffDay} day${diffDay > 1 ? 's' : ''} ago`
  return date.toLocaleDateString()
}

function getStatusColor(status: string): string {
  switch (status) {
    case 'passed':
      return 'bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border-emerald-500/30'
    case 'failed':
      return 'bg-red-500/20 text-red-600 dark:text-red-400 border-red-500/30'
    case 'not_run':
    default:
      return 'bg-muted text-muted-foreground border-border'
  }
}

function getStatusIcon(status: string) {
  switch (status) {
    case 'passed':
      return <CheckCircle2 className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
    case 'failed':
      return <XCircle className="h-4 w-4 text-red-600 dark:text-red-400" />
    case 'not_run':
    default:
      return <Clock className="h-4 w-4 text-muted-foreground" />
  }
}

function toPercent(passRateRatio: number | undefined): number {
  return (passRateRatio ?? 0) * 100
}

const MAX_POLL_MS = 30 * 60 * 1000 // 30 minutes

export function SimulationDashboard({ agentId, agentName }: SimulationDashboardProps) {
  const queryClient = useQueryClient()
  const [isRunningAll, setIsRunningAll] = useState(false)
  const [stuckRun, setStuckRun] = useState(false)
  const [showCreateDialog, setShowCreateDialog] = useState(false)
  const pollStartRef = useRef<number | null>(null)

  // Fetch simulation dashboard data
  const { data: dashboard, isLoading, error, refetch } = useQuery({
    queryKey: ['simulation-dashboard', agentId],
    queryFn: () => testingService.getSimulationDashboard(agentId),
    refetchInterval: isRunningAll ? 5000 : false, // Poll while running
  })

  const [activeRunId, setActiveRunId] = useState<string | null>(null)

  // Poll progress for active run
  const { data: runProgress } = useQuery({
    queryKey: ['test-run-progress', activeRunId],
    queryFn: () => testingService.getTestRunProgress(activeRunId!),
    enabled: !!activeRunId && isRunningAll,
    refetchInterval: 3000,
  })

  const stopPolling = useCallback(() => {
    setIsRunningAll(false)
    setActiveRunId(null)
    pollStartRef.current = null
    queryClient.invalidateQueries({ queryKey: ['simulation-dashboard', agentId] })
  }, [agentId, queryClient])

  // Stop polling when run completes or when max poll duration exceeded
  useEffect(() => {
    if (!isRunningAll) return

    if (runProgress && !['running', 'stopping'].includes(runProgress.status)) {
      stopPolling()
      return
    }

    // Guard against background task dying without updating status
    if (pollStartRef.current && Date.now() - pollStartRef.current > MAX_POLL_MS) {
      setStuckRun(true)
      stopPolling()
    }
  }, [runProgress, isRunningAll, stopPolling])

  // Run all tests mutation
  const runAllMutation = useMutation({
    mutationFn: () => testingService.runAllEvaluations(agentId),
    onSuccess: (data) => {
      setIsRunningAll(true)
      setStuckRun(false)
      setActiveRunId(data.test_run_id)
      pollStartRef.current = Date.now()
    },
  })

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (error || !dashboard) {
    return (
      <Card className="glass-card">
        <CardContent className="flex flex-col items-center justify-center py-12">
          <XCircle className="h-12 w-12 text-red-600 dark:text-red-400 mb-4" />
          <h3 className="text-lg font-semibold mb-2">Failed to load evaluation data</h3>
          <p className="text-sm text-muted-foreground mb-4">
            {error instanceof Error ? error.message : 'Unknown error'}
          </p>
          <Button variant="outline" onClick={() => refetch()}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Retry
          </Button>
        </CardContent>
      </Card>
    )
  }

  const { agent_stats, test_cases, recent_runs } = dashboard

  return (
    <div className="space-y-6">
      {/* Header with Agent Name and Actions */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="h-10 w-10 rounded-full bg-gradient-to-br from-primary to-primary/70 flex items-center justify-center">
            <span className="text-white font-semibold text-sm">
              {(agentName || agent_stats.agent_name).charAt(0).toUpperCase()}
            </span>
          </div>
          <div>
            <h2 className="text-lg font-semibold">{agentName || agent_stats.agent_name}</h2>
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
              <span>{agent_stats.last_run_at ? formatTimeAgo(agent_stats.last_run_at) : 'No runs yet'}</span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => refetch()}
            disabled={isRunningAll}
          >
            <RefreshCw className={cn("h-4 w-4 mr-2", isRunningAll && "animate-spin")} />
            Refresh
          </Button>
          <Button
            size="sm"
            onClick={() => runAllMutation.mutate()}
            disabled={isRunningAll || runAllMutation.isPending || test_cases.length === 0}
            title={test_cases.length === 0 ? 'Add test cases before running' : undefined}
            className="bg-primary hover:bg-primary/90"
          >
            <Play className="h-4 w-4 mr-2" />
            {isRunningAll ? 'Running...' : 'Run Evaluation Suite'}
          </Button>
        </div>
      </div>

      {/* Stuck run warning — shown when background task dies without completing */}
      {stuckRun && (
        <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <span className="font-medium">Evaluation timed out.</span>
          The background task did not complete within 30 minutes. Please try running the suite again. If it keeps happening, check the backend logs.
        </div>
      )}

      {/* Progress bar during batch run */}
      {isRunningAll && runProgress && (
        <div className="w-full">
          <div className="flex items-center justify-between text-sm text-muted-foreground mb-1">
            <span>Running tests: {runProgress.completed_count}/{runProgress.total_count}</span>
            <span>{runProgress.passed_count} passed, {runProgress.failed_count} failed</span>
          </div>
          <div className="h-2 rounded-full bg-muted overflow-hidden">
            <div
              className="h-full rounded-full bg-primary transition-all duration-500"
              style={{ width: `${runProgress.total_count > 0 ? (runProgress.completed_count / runProgress.total_count) * 100 : 0}%` }}
            />
          </div>
        </div>
      )}

      {/* Run summary cards */}
      {(() => {
        // Use the latest completed run for the header stats so stale historical
        // failures don't drag down the visible pass rate.
        const latestRun = dashboard.recent_runs.find((r) => r.status === 'completed')
        const latestPassRate = latestRun
          ? Math.round(toPercent(latestRun.pass_rate))
          : null
        const latestPassed = latestRun?.passed_count ?? 0
        const latestFailed = latestRun?.failed_count ?? 0
        const latestTotal = latestRun?.total_test_cases ?? 0
        const hasRuns = agent_stats.total_simulations > 0

        return (
          <div className="grid gap-4 md:grid-cols-3">
            {/* Pass Rate Card */}
            <Card className="bg-card border-border">
              <CardContent className="p-6">
                <div className="text-sm font-medium text-muted-foreground mb-2">Pass Rate</div>
                <div className="text-5xl font-bold text-foreground mb-2">
                  {latestPassRate !== null ? `${latestPassRate}%` : hasRuns ? `${Math.round(agent_stats.pass_rate)}%` : '—'}
                </div>
                <div className="text-sm text-muted-foreground">
                  {latestRun
                    ? `Latest run · ${latestTotal} test cases`
                    : 'No runs yet'}
                </div>
              </CardContent>
            </Card>

            {/* Simulations Passed Card */}
            <Card className="bg-card border-border">
              <CardContent className="p-6">
                <div className="text-sm font-medium text-muted-foreground mb-2">Evaluations passed</div>
                <div className="text-5xl font-bold text-emerald-600 dark:text-emerald-400 mb-2">
                  {latestRun ? latestPassed : agent_stats.simulations_passed}
                </div>
                <div className="text-sm text-muted-foreground">
                  {latestRun ? 'In latest run' : 'All conditions met'}
                </div>
              </CardContent>
            </Card>

            {/* Simulations Failed Card */}
            <Card className="bg-card border-border">
              <CardContent className="p-6">
                <div className="text-sm font-medium text-muted-foreground mb-2">Evaluations failed</div>
                <div className="text-5xl font-bold text-foreground mb-2">
                  {latestRun ? latestFailed : agent_stats.simulations_failed}
                </div>
                <div className="text-sm text-muted-foreground">
                  {latestRun ? 'In latest run' : 'Failure conditions met'}
                </div>
              </CardContent>
            </Card>
          </div>
        )
      })()}

      <CreateTestCaseDialog
        agentId={agentId}
        open={showCreateDialog}
        onClose={() => setShowCreateDialog(false)}
        onCreated={() => queryClient.invalidateQueries({ queryKey: ['simulation-dashboard', agentId] })}
      />

      {/* Test Cases Table */}
      <Card className="glass-card">
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-base">Test results</CardTitle>
          <Button variant="outline" size="sm" className="gap-1.5" onClick={() => setShowCreateDialog(true)}>
            <Plus className="h-3.5 w-3.5" />
            Add test case
          </Button>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow className="border-border hover:bg-transparent">
                <TableHead className="text-muted-foreground">Test case</TableHead>
                <TableHead className="text-muted-foreground">Status</TableHead>
                <TableHead className="text-muted-foreground text-right">Pass rate</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {test_cases.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={3} className="text-center py-8 text-muted-foreground">
                    No test cases yet. Click "Add test case" above to create one.
                  </TableCell>
                </TableRow>
              ) : (
                test_cases.map((testCase) => (
                  <TableRow key={testCase.test_case_id} className="border-border">
                    <TableCell className="font-medium">{testCase.test_case_name}</TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className={cn('flex items-center gap-1 w-fit', getStatusColor(testCase.status))}
                      >
                        {getStatusIcon(testCase.status)}
                        {testCase.status === 'passed' ? 'Passed' :
                         testCase.status === 'failed' ? 'Failed' : 'Not run'}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      <span className={cn(
                        'font-semibold',
                        testCase.pass_rate >= 80 ? 'text-emerald-600 dark:text-emerald-400' :
                        testCase.pass_rate >= 50 ? 'text-yellow-600 dark:text-yellow-400' :
                        testCase.total_runs === 0 ? 'text-muted-foreground' : 'text-red-600 dark:text-red-400'
                      )}>
                        {testCase.total_runs === 0 ? '-' : `${Math.round(testCase.pass_rate)}%`}
                      </span>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Recent Runs */}
      {recent_runs.length > 0 && (
        <Card className="glass-card">
          <CardHeader>
            <CardTitle className="text-base">Recent runs</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {recent_runs.slice(0, 5).map((run) => (
                <div
                  key={run.id}
                  className="flex items-center justify-between p-3 rounded-lg bg-muted border border-border"
                >
                  <div className="flex items-center gap-3">
                    {run.status === 'completed' && run.failed_count === 0 ? (
                      <CheckCircle2 className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />
                    ) : run.status === 'completed' && run.failed_count > 0 ? (
                      <XCircle className="h-5 w-5 text-red-600 dark:text-red-400" />
                    ) : run.status === 'running' ? (
                      <RefreshCw className="h-5 w-5 text-blue-600 dark:text-blue-400 animate-spin" />
                    ) : (
                      <Clock className="h-5 w-5 text-muted-foreground" />
                    )}
                    <div>
                      <div className="font-medium text-sm">{run.run_name}</div>
                      <div className="text-xs text-muted-foreground">
                        {formatTimeAgo(run.started_at)}
                      </div>
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-sm font-medium">
                      {run.passed_count}/{run.total_test_cases} passed
                    </div>
                    {run.pass_rate != null && (
                      <div className={cn(
                        'text-xs',
                        toPercent(run.pass_rate) >= 80 ? 'text-emerald-600 dark:text-emerald-400' :
                        toPercent(run.pass_rate) >= 50 ? 'text-yellow-600 dark:text-yellow-400' : 'text-red-600 dark:text-red-400'
                      )}>
                        {Math.round(toPercent(run.pass_rate))}% pass rate
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
