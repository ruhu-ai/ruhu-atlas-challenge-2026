import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/atoms/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/atoms/dialog'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/atoms/table'
import { Textarea } from '@/components/atoms/textarea'
import { Checkbox } from '@/components/atoms/checkbox'
import { Loader2, Play, RefreshCw, Square, Plus } from 'lucide-react'
import { agentDefinitionService } from '@/api/services/agent-definition.service'
import type {
  EvaluationRun,
  AgentEvaluationPolicyResponse,
  AgentOperationalMetrics,
  AgentReplayResponse,
  SimulationFixture,
} from '@/types/agent-definition'

interface AgentEvaluationViewProps {
  agentId: string
  agentName: string
}

function formatDateTime(value?: string | null): string {
  if (!value) return 'n/a'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'n/a'
  return date.toLocaleString()
}

function formatPercent(value?: number | null): string {
  if (value == null) return 'n/a'
  return `${Math.round(value * 100)}%`
}

function runStatusTone(status: EvaluationRun['status']): string {
  if (status === 'completed') return 'border-emerald-500/30 text-emerald-300'
  if (status === 'running' || status === 'queued' || status === 'stopping') {
    return 'border-blue-500/30 text-blue-300'
  }
  if (status === 'failed' || status === 'cancelled') return 'border-red-500/30 text-red-300'
  return 'border-border text-muted-foreground'
}

export function AgentEvaluationView({ agentId, agentName }: AgentEvaluationViewProps) {
  const queryClient = useQueryClient()
  const [minimumPassRate, setMinimumPassRate] = useState('100')
  const [allowWarningFailures, setAllowWarningFailures] = useState(true)
  const [maxQualifiedRunAgeHours, setMaxQualifiedRunAgeHours] = useState('')
  const [isFixtureDialogOpen, setIsFixtureDialogOpen] = useState(false)
  const [fixtureName, setFixtureName] = useState('')
  const [fixtureDescription, setFixtureDescription] = useState('')
  const [fixtureTurns, setFixtureTurns] = useState('')
  const [fixtureTags, setFixtureTags] = useState('')
  const [fixtureGateRequired, setFixtureGateRequired] = useState(true)
  const [fixtureActive, setFixtureActive] = useState(true)
  const [replayTurns, setReplayTurns] = useState('')

  const policyQuery = useQuery({
    queryKey: ['agent-definition-evaluation-policy', agentId],
    queryFn: () => agentDefinitionService.getAgentEvaluationPolicy(agentId),
    staleTime: 10_000,
  })

  const fixturesQuery = useQuery({
    queryKey: ['agent-definition-simulation-fixtures', agentId],
    queryFn: () => agentDefinitionService.listSimulationFixtures(agentId),
    staleTime: 10_000,
  })

  const runsQuery = useQuery({
    queryKey: ['agent-definition-evaluation-runs', agentId],
    queryFn: () => agentDefinitionService.listEvaluationRuns(agentId),
    staleTime: 5_000,
    refetchInterval: (query) => {
      const runs = query.state.data || []
      return runs.some((run) => run.status === 'queued' || run.status === 'running' || run.status === 'stopping')
        ? 5_000
        : false
    },
  })

  const runs = runsQuery.data || []
  const hasQualifiedRun = useMemo(
    () => runs.some((run) => Boolean(run.qualified_at)),
    [runs],
  )

  const latestQualifiedRunQuery = useQuery({
    queryKey: ['agent-definition-latest-qualified-run', agentId],
    queryFn: async () => {
      try {
        return await agentDefinitionService.getLatestQualifiedRun(agentId)
      } catch (error) {
        const message = error instanceof Error ? error.message : ''
        if (message.includes('no qualified evaluation run found') || message.includes('HTTP 404')) {
          return null
        }
        throw error
      }
    },
    enabled: hasQualifiedRun,
    retry: false,
    staleTime: 10_000,
  })

  const metricsQuery = useQuery({
    queryKey: ['agent-definition-metrics', agentId],
    queryFn: () => agentDefinitionService.getMetrics(agentId),
    staleTime: 10_000,
  })

  const updatePolicyMutation = useMutation({
    mutationFn: async () => {
      return agentDefinitionService.updateAgentEvaluationPolicy(agentId, {
        minimum_pass_rate_ratio: Math.max(0, Math.min(1, (Number(minimumPassRate) || 0) / 100)),
        allow_warning_failures: allowWarningFailures,
        max_qualified_run_age_hours: maxQualifiedRunAgeHours.trim()
          ? Number(maxQualifiedRunAgeHours)
          : null,
      })
    },
    onSuccess: (response: AgentEvaluationPolicyResponse) => {
      queryClient.setQueryData(['agent-definition-evaluation-policy', agentId], response)
      toast.success('Evaluation policy saved')
    },
    onError: (error: Error) => {
      toast.error(`Failed to save evaluation policy: ${error.message}`)
    },
  })

  const createFixtureMutation = useMutation({
    mutationFn: async () => {
      const turns = fixtureTurns
        .split('\n')
        .map((item) => item.trim())
        .filter(Boolean)
        .map((text) => ({ text, event_type: 'user_message', modality: 'text' as const }))

      return agentDefinitionService.createSimulationFixture(agentId, {
        name: fixtureName.trim(),
        description: fixtureDescription.trim() || null,
        tags: fixtureTags
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean),
        turns,
        is_active: fixtureActive,
        gate_required: fixtureGateRequired,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agent-definition-simulation-fixtures', agentId] })
      toast.success('Simulation fixture created')
      setIsFixtureDialogOpen(false)
      setFixtureName('')
      setFixtureDescription('')
      setFixtureTurns('')
      setFixtureTags('')
      setFixtureGateRequired(true)
      setFixtureActive(true)
    },
    onError: (error: Error) => {
      toast.error(`Failed to create fixture: ${error.message}`)
    },
  })

  const createRunMutation = useMutation({
    mutationFn: async () => {
      return agentDefinitionService.createEvaluationRun(agentId, {
        mode: 'manual_batch',
        source: 'studio',
        gate_eligible: true,
        execution_mode: 'async',
      })
    },
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: ['agent-definition-evaluation-runs', agentId] })
      queryClient.invalidateQueries({ queryKey: ['agent-definition-latest-qualified-run', agentId] })
      toast.success(`Evaluation run ${run.evaluation_run_id.slice(0, 8)} started`)
    },
    onError: (error: Error) => {
      toast.error(`Failed to start evaluation run: ${error.message}`)
    },
  })

  const stopRunMutation = useMutation({
    mutationFn: async (evaluationRunId: string) => agentDefinitionService.stopEvaluationRun(evaluationRunId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agent-definition-evaluation-runs', agentId] })
      toast.success('Stop requested for evaluation run')
    },
    onError: (error: Error) => {
      toast.error(`Failed to stop evaluation run: ${error.message}`)
    },
  })

  const replayMutation = useMutation({
    mutationFn: async () => {
      const utterances = replayTurns
        .split('\n')
        .map((item) => item.trim())
        .filter(Boolean)
      return agentDefinitionService.replayAgent(agentId, { utterances, channel: 'web_chat' })
    },
    onError: (error: Error) => {
      toast.error(`Replay failed: ${error.message}`)
    },
  })

  useEffect(() => {
    if (!policyQuery.data) return
    setMinimumPassRate(String(Math.round(policyQuery.data.policy.minimum_pass_rate_ratio * 100)))
    setAllowWarningFailures(policyQuery.data.policy.allow_warning_failures)
    setMaxQualifiedRunAgeHours(
      policyQuery.data.policy.max_qualified_run_age_hours == null
        ? ''
        : String(policyQuery.data.policy.max_qualified_run_age_hours),
    )
  }, [policyQuery.data])

  const fixtures = fixturesQuery.data || []
  const latestQualifiedRun = latestQualifiedRunQuery.data || null
  const metrics = metricsQuery.data
  const latestReplay = replayMutation.data || null

  const fixtureSummary = useMemo(() => ({
    total: fixtures.length,
    active: fixtures.filter((fixture) => fixture.is_active).length,
    gateRequired: fixtures.filter((fixture) => fixture.gate_required).length,
  }), [fixtures])

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Agent Evaluation Policy</CardTitle>
          <CardDescription>
            Define what qualifies this agent definition for publishing.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-3">
            <div className="space-y-2">
              <Label htmlFor="agent-min-pass-rate">Minimum pass rate (%)</Label>
              <Input
                id="agent-min-pass-rate"
                type="number"
                min={0}
                max={100}
                step={1}
                value={minimumPassRate}
                onChange={(event) => setMinimumPassRate(event.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="agent-max-qualified-run-age">Qualified run freshness (hours)</Label>
              <Input
                id="agent-max-qualified-run-age"
                type="number"
                min={1}
                step={1}
                value={maxQualifiedRunAgeHours}
                onChange={(event) => setMaxQualifiedRunAgeHours(event.target.value)}
                placeholder="Optional"
              />
            </div>
            <div className="flex items-end">
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={allowWarningFailures}
                  onCheckedChange={(checked) => setAllowWarningFailures(checked === true)}
                />
                Allow warning-only failures
              </label>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button onClick={() => updatePolicyMutation.mutate()} disabled={updatePolicyMutation.isPending || policyQuery.isLoading}>
              {updatePolicyMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Save Policy
            </Button>
            <Button variant="outline" onClick={() => policyQuery.refetch()}>
              <RefreshCw className="mr-2 h-4 w-4" />
              Refresh
            </Button>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Evaluation Fixtures</CardTitle>
            <CardDescription>
              Stored conversation fixtures for {agentName}.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="rounded-md border border-border p-3">
                <p className="text-xs text-muted-foreground">Total</p>
                <p className="text-lg font-semibold">{fixtureSummary.total}</p>
              </div>
              <div className="rounded-md border border-border p-3">
                <p className="text-xs text-muted-foreground">Active</p>
                <p className="text-lg font-semibold">{fixtureSummary.active}</p>
              </div>
              <div className="rounded-md border border-border p-3">
                <p className="text-xs text-muted-foreground">Gate required</p>
                <p className="text-lg font-semibold">{fixtureSummary.gateRequired}</p>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <Button variant="outline" onClick={() => setIsFixtureDialogOpen(true)}>
                <Plus className="mr-2 h-4 w-4" />
                Add Fixture
              </Button>
              <Button variant="outline" onClick={() => fixturesQuery.refetch()}>
                <RefreshCw className="mr-2 h-4 w-4" />
                Refresh
              </Button>
            </div>

            {fixturesQuery.isLoading ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading fixtures...
              </div>
            ) : fixtures.length === 0 ? (
              <p className="text-sm text-muted-foreground">No fixtures yet. Add one before running evaluations.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Turns</TableHead>
                    <TableHead>Gate</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {fixtures.map((fixture: SimulationFixture) => (
                    <TableRow key={fixture.fixture_id}>
                      <TableCell className="font-medium">{fixture.name}</TableCell>
                      <TableCell>{fixture.turns.length}</TableCell>
                      <TableCell>{fixture.gate_required ? 'required' : 'optional'}</TableCell>
                      <TableCell>
                        <Badge variant="outline">{fixture.is_active ? 'active' : 'inactive'}</Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Run Evaluations</CardTitle>
            <CardDescription>
              Execute gate-eligible evaluation runs against the current draft.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                onClick={() => createRunMutation.mutate()}
                disabled={createRunMutation.isPending || fixtures.length === 0}
              >
                {createRunMutation.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Play className="mr-2 h-4 w-4" />
                )}
                Run Evaluation
              </Button>
              <Button variant="outline" onClick={() => runsQuery.refetch()}>
                <RefreshCw className="mr-2 h-4 w-4" />
                Refresh Runs
              </Button>
            </div>

            {latestQualifiedRun && (
              <div className="rounded-md border border-emerald-500/20 bg-emerald-500/5 p-4">
                <p className="text-sm font-medium text-emerald-200">Latest qualified run</p>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
                  <Badge variant="outline" className="border-emerald-500/30 text-emerald-300">
                    {formatPercent(latestQualifiedRun.pass_rate_ratio)}
                  </Badge>
                  <span>{latestQualifiedRun.passed_count}/{latestQualifiedRun.fixture_count} passed</span>
                  <span className="text-muted-foreground">{formatDateTime(latestQualifiedRun.completed_at)}</span>
                </div>
              </div>
            )}
            {!latestQualifiedRun && !latestQualifiedRunQuery.isFetching && (
              <p className="text-sm text-muted-foreground">
                No qualified run yet. Run an evaluation and meet policy thresholds to establish one.
              </p>
            )}

            {runsQuery.isLoading ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading evaluation runs...
              </div>
            ) : runs.length === 0 ? (
              <p className="text-sm text-muted-foreground">No evaluation runs yet.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Status</TableHead>
                    <TableHead>Pass rate</TableHead>
                    <TableHead>Fixtures</TableHead>
                    <TableHead>Completed</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {runs.slice(0, 8).map((run: EvaluationRun) => (
                    <TableRow key={run.evaluation_run_id}>
                      <TableCell>
                        <Badge variant="outline" className={runStatusTone(run.status)}>
                          {run.status}
                        </Badge>
                      </TableCell>
                      <TableCell>{formatPercent(run.pass_rate_ratio)}</TableCell>
                      <TableCell>{run.fixture_count}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {formatDateTime(run.completed_at || run.started_at)}
                      </TableCell>
                      <TableCell className="text-right">
                        {(run.status === 'queued' || run.status === 'running') && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => stopRunMutation.mutate(run.evaluation_run_id)}
                            disabled={stopRunMutation.isPending}
                          >
                            <Square className="mr-2 h-3.5 w-3.5" />
                            Stop
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Operational Metrics</CardTitle>
            <CardDescription>
              Runtime telemetry for this agent definition.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {metricsQuery.isLoading ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading metrics...
              </div>
            ) : !metrics ? (
              <p className="text-sm text-muted-foreground">No metrics available yet.</p>
            ) : (
              <>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  <div className="rounded-md border border-border p-3">
                    <p className="text-xs text-muted-foreground">Conversations</p>
                    <p className="text-lg font-semibold">{metrics.conversation_count}</p>
                  </div>
                  <div className="rounded-md border border-border p-3">
                    <p className="text-xs text-muted-foreground">Traces</p>
                    <p className="text-lg font-semibold">{metrics.trace_count}</p>
                  </div>
                  <div className="rounded-md border border-border p-3">
                    <p className="text-xs text-muted-foreground">Avg turns / conversation</p>
                    <p className="text-lg font-semibold">{metrics.avg_turns_per_conversation.toFixed(1)}</p>
                  </div>
                  <div className="rounded-md border border-border p-3">
                    <p className="text-xs text-muted-foreground">P95 latency</p>
                    <p className="text-lg font-semibold">{metrics.total_latency.p95_ms} ms</p>
                  </div>
                </div>
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-2">
                    <p className="text-sm font-medium">State entries</p>
                    {Object.keys(metrics.state_entries).length === 0 ? (
                      <p className="text-sm text-muted-foreground">No state entry data yet.</p>
                    ) : (
                      Object.entries(metrics.state_entries).slice(0, 8).map(([stateId, count]) => (
                        <div key={stateId} className="flex items-center justify-between rounded-md border border-border px-3 py-2 text-sm">
                          <span className="truncate">{stateId}</span>
                          <span>{count}</span>
                        </div>
                      ))
                    )}
                  </div>
                  <div className="space-y-2">
                    <p className="text-sm font-medium">Tool status counts</p>
                    {Object.keys(metrics.tool_status_counts).length === 0 ? (
                      <p className="text-sm text-muted-foreground">No tool data yet.</p>
                    ) : (
                      Object.entries(metrics.tool_status_counts).slice(0, 8).map(([status, count]) => (
                        <div key={status} className="flex items-center justify-between rounded-md border border-border px-3 py-2 text-sm">
                          <span className="truncate">{status}</span>
                          <span>{count}</span>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Quick Replay</CardTitle>
            <CardDescription>
              Run a manual multi-turn replay against the current draft.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="agent-replay-turns">User turns</Label>
              <Textarea
                id="agent-replay-turns"
                rows={8}
                value={replayTurns}
                onChange={(event) => setReplayTurns(event.target.value)}
                placeholder={'Hello\nI need help with my order\nMy email is jane@example.com'}
              />
            </div>
            <div className="flex items-center gap-2">
              <Button
                onClick={() => replayMutation.mutate()}
                disabled={replayMutation.isPending || replayTurns.trim().length === 0}
              >
                {replayMutation.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Play className="mr-2 h-4 w-4" />
                )}
                Replay Draft
              </Button>
            </div>

            {latestReplay && (
              <ReplayResultCard replay={latestReplay} />
            )}
          </CardContent>
        </Card>
      </div>

      <Dialog open={isFixtureDialogOpen} onOpenChange={setIsFixtureDialogOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>New Simulation Fixture</DialogTitle>
            <DialogDescription>
              Create a reusable conversation fixture for this agent definition.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="fixture-name">Name</Label>
                <Input id="fixture-name" value={fixtureName} onChange={(event) => setFixtureName(event.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="fixture-tags">Tags</Label>
                <Input
                  id="fixture-tags"
                  value={fixtureTags}
                  onChange={(event) => setFixtureTags(event.target.value)}
                  placeholder="billing, happy-path"
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="fixture-description">Description</Label>
              <Textarea
                id="fixture-description"
                rows={2}
                value={fixtureDescription}
                onChange={(event) => setFixtureDescription(event.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="fixture-turns">User turns</Label>
              <Textarea
                id="fixture-turns"
                rows={8}
                value={fixtureTurns}
                onChange={(event) => setFixtureTurns(event.target.value)}
                placeholder={'Hi\nI need to update my shipping address\nMy email is jane@example.com'}
              />
            </div>
            <div className="flex flex-wrap items-center gap-6">
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={fixtureActive}
                  onCheckedChange={(checked) => setFixtureActive(checked === true)}
                />
                Active
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={fixtureGateRequired}
                  onCheckedChange={(checked) => setFixtureGateRequired(checked === true)}
                />
                Required for publish gate
              </label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsFixtureDialogOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => createFixtureMutation.mutate()}
              disabled={createFixtureMutation.isPending || fixtureName.trim().length === 0 || fixtureTurns.trim().length === 0}
            >
              {createFixtureMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Create Fixture
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function ReplayResultCard({ replay }: { replay: AgentReplayResponse }) {
  return (
    <div className="space-y-4 rounded-md border border-border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-medium">Replay result</p>
        <Badge variant="outline">{replay.simulation.final_step_id}</Badge>
      </div>
      <div className="space-y-2">
        {replay.simulation.turns.map((turn) => (
          <div key={turn.turn_id} className="rounded-md border border-border p-3">
            <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
              <span>
                {turn.step_before} → {turn.step_after}
              </span>
              <span>{Object.values(turn.latency_breakdown_ms || {}).reduce((sum, value) => sum + value, 0)} ms</span>
            </div>
            {turn.emitted_messages.length > 0 && (
              <div className="mt-2 space-y-1">
                {turn.emitted_messages.map((message, index) => (
                  <p key={`${turn.turn_id}-${index}`} className="text-sm text-foreground/90">
                    {message.text}
                  </p>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="rounded-md border border-border bg-muted/20 p-3">
        <p className="text-xs text-muted-foreground">Final facts</p>
        <pre className="mt-2 overflow-x-auto text-xs text-foreground/80">
          {JSON.stringify(replay.simulation.final_facts, null, 2)}
        </pre>
      </div>
    </div>
  )
}
