import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Lightbulb,
  Loader2,
  RefreshCw,
  Sparkles,
  Target,
} from 'lucide-react'

import { kpiGoalService } from '@/api/services/kpi-goals.service'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/atoms/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/atoms/select'
import { DashboardLayout } from '@/layouts/dashboard-layout'
import { formatDate } from '@/lib/utils'
import {
  calculateGoalProgress,
  formatMetricDelta,
  formatMetricValue,
  getMetricDefinition,
  getMetricDisplayLabel,
  goalStatusTone,
  useKpiMetricDefinitions,
} from '@/features/kpi-goals/utils/metric-display'

function rankWeight(status: string): number {
  switch (status) {
    case 'stalled':
      return 5
    case 'at_risk':
      return 4
    case 'active':
      return 3
    case 'on_track':
      return 2
    case 'draft':
      return 1
    default:
      return 0
  }
}

export default function InsightsEnhancedPage() {
  const [selectedGoalId, setSelectedGoalId] = useState<string>('all')
  const queryClient = useQueryClient()
  const { data: metricDefinitions = [] } = useKpiMetricDefinitions()
  const { data: goalSummaries = [], isLoading: goalsLoading } = useQuery({
    queryKey: ['kpi', 'goals', 'insights-page'],
    queryFn: () => kpiGoalService.listGoals(),
  })
  const safeGoalSummaries = Array.isArray(goalSummaries) ? goalSummaries : []

  const rankedGoals = useMemo(() => {
    return [...safeGoalSummaries].sort((left, right) => {
      if (rankWeight(right.status) !== rankWeight(left.status)) {
        return rankWeight(right.status) - rankWeight(left.status)
      }
      if (right.open_insight_count !== left.open_insight_count) {
        return right.open_insight_count - left.open_insight_count
      }
      if (right.pending_recommendation_count !== left.pending_recommendation_count) {
        return right.pending_recommendation_count - left.pending_recommendation_count
      }
      return (left.progress_ratio ?? 0) - (right.progress_ratio ?? 0)
    })
  }, [safeGoalSummaries])

  const effectiveGoalId = selectedGoalId === 'all' ? rankedGoals[0]?.goal_id : selectedGoalId

  const { data: selectedGoalDetail, isLoading: detailLoading } = useQuery({
    queryKey: ['kpi', 'goal-detail', effectiveGoalId ?? 'none', 'insights-page'],
    queryFn: () => kpiGoalService.getGoal(effectiveGoalId as string),
    enabled: Boolean(effectiveGoalId),
  })

  const evaluateMutation = useMutation({
    mutationFn: async () => {
      if (!selectedGoalDetail) {
        throw new Error('Select a goal before evaluating it.')
      }

      const observation =
        selectedGoalDetail.latest_observation ??
        (await kpiGoalService.refreshObservation(
          selectedGoalDetail.scope.scope_id,
          selectedGoalDetail.goal.metric_key,
          {}
        ))

      return kpiGoalService.evaluateGoal(selectedGoalDetail.goal.goal_id, {
        observation_id: observation.observation_id,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['kpi', 'goals'] })
      queryClient.invalidateQueries({ queryKey: ['kpi', 'goal-detail'] })
      toast.success('Goal evaluation refreshed')
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : 'Failed to evaluate goal')
    },
  })

  const generateInsightsMutation = useMutation({
    mutationFn: async () => {
      if (!selectedGoalDetail) {
        throw new Error('Select a goal before generating insights.')
      }
      return kpiGoalService.generateGoalInsights(selectedGoalDetail.goal.goal_id)
    },
    onSuccess: (items) => {
      queryClient.invalidateQueries({ queryKey: ['kpi', 'goals'] })
      queryClient.invalidateQueries({ queryKey: ['kpi', 'goal-detail'] })
      toast.success(`Generated ${items.length} insight${items.length === 1 ? '' : 's'}`)
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : 'Failed to generate insights')
    },
  })

  const generateRecommendationsMutation = useMutation({
    mutationFn: async () => {
      if (!selectedGoalDetail) {
        throw new Error('Select a goal before generating recommendations.')
      }
      return kpiGoalService.generateGoalRecommendations(selectedGoalDetail.goal.goal_id)
    },
    onSuccess: (items) => {
      queryClient.invalidateQueries({ queryKey: ['kpi', 'goals'] })
      queryClient.invalidateQueries({ queryKey: ['kpi', 'goal-detail'] })
      toast.success(`Generated ${items.length} recommendation${items.length === 1 ? '' : 's'}`)
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : 'Failed to generate recommendations')
    },
  })

  const selectedMetricDefinition = selectedGoalDetail
    ? getMetricDefinition(metricDefinitions, selectedGoalDetail.goal.metric_key)
    : undefined

  const portfolioStats = useMemo(() => {
    const activeGoals = rankedGoals.filter((goal) =>
      ['active', 'on_track', 'at_risk', 'stalled'].includes(goal.status)
    )
    const riskGoals = rankedGoals.filter((goal) => ['at_risk', 'stalled'].includes(goal.status))
    const openInsights = rankedGoals.reduce((sum, goal) => sum + goal.open_insight_count, 0)
    const pendingRecommendations = rankedGoals.reduce(
      (sum, goal) => sum + goal.pending_recommendation_count,
      0
    )

    return {
      activeGoals: activeGoals.length,
      riskGoals: riskGoals.length,
      openInsights,
      pendingRecommendations,
    }
  }, [rankedGoals])

  const isLoading = goalsLoading || (Boolean(effectiveGoalId) && detailLoading)

  return (
    <DashboardLayout>
      <div className="space-y-6">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div>
            <h1 className="flex items-center gap-2 text-2xl font-bold">
              <Lightbulb className="h-6 w-6 text-primary" />
              Insights
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Goal-linked insights and recommendations driven directly by KPI runtime state.
            </p>
          </div>
          <div className="flex items-center gap-3 flex-wrap">
            <Select value={selectedGoalId} onValueChange={setSelectedGoalId}>
              <SelectTrigger className="w-64">
                <SelectValue placeholder="Top Goal" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Top Priority Goal</SelectItem>
                {rankedGoals.map((goal) => (
                  <SelectItem key={goal.goal_id} value={goal.goal_id}>
                    {goal.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        ) : rankedGoals.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center justify-center py-16 text-center">
              <Target className="mb-4 h-12 w-12 text-muted-foreground" />
              <h3 className="mb-2 text-lg font-semibold">No KPI goals yet</h3>
              <p className="max-w-md text-sm text-muted-foreground">
                Insights derive from KPI goals. Create measured goals first, then generate
                insights and recommendations against those targets.
              </p>
            </CardContent>
          </Card>
        ) : (
          <>
            <div className="grid gap-4 md:grid-cols-4">
              <Card>
                <CardContent className="flex items-center gap-3 p-5">
                  <div className="rounded-full bg-primary/10 p-2 text-primary">
                    <Target className="h-4 w-4" />
                  </div>
                  <div>
                    <p className="text-xs uppercase tracking-wide text-muted-foreground">Active Goals</p>
                    <p className="text-2xl font-bold">{portfolioStats.activeGoals}</p>
                  </div>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="flex items-center gap-3 p-5">
                  <div className="rounded-full bg-amber-500/10 p-2 text-amber-500">
                    <AlertTriangle className="h-4 w-4" />
                  </div>
                  <div>
                    <p className="text-xs uppercase tracking-wide text-muted-foreground">Risk Goals</p>
                    <p className="text-2xl font-bold">{portfolioStats.riskGoals}</p>
                  </div>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="flex items-center gap-3 p-5">
                  <div className="rounded-full bg-blue-500/10 p-2 text-blue-500">
                    <Sparkles className="h-4 w-4" />
                  </div>
                  <div>
                    <p className="text-xs uppercase tracking-wide text-muted-foreground">Open Insights</p>
                    <p className="text-2xl font-bold">{portfolioStats.openInsights}</p>
                  </div>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="flex items-center gap-3 p-5">
                  <div className="rounded-full bg-emerald-500/10 p-2 text-emerald-500">
                    <Lightbulb className="h-4 w-4" />
                  </div>
                  <div>
                    <p className="text-xs uppercase tracking-wide text-muted-foreground">Recommendations</p>
                    <p className="text-2xl font-bold">{portfolioStats.pendingRecommendations}</p>
                  </div>
                </CardContent>
              </Card>
            </div>

            <div className="grid gap-6 lg:grid-cols-5">
              <div className="space-y-3 lg:col-span-2">
                <div className="flex items-center justify-between">
                  <h2 className="text-base font-semibold">Insight Queue</h2>
                  <span className="text-xs text-muted-foreground">
                    Ranked by goal risk, insight pressure, and backlog
                  </span>
                </div>

                {rankedGoals.map((goal) => {
                  const metricDefinition = getMetricDefinition(metricDefinitions, goal.metric_key)
                  const progress = calculateGoalProgress(goal, metricDefinition)
                  const isSelected = effectiveGoalId === goal.goal_id

                  return (
                    <button
                      key={goal.goal_id}
                      type="button"
                      onClick={() => setSelectedGoalId(goal.goal_id)}
                      className={`w-full rounded-xl border bg-card px-5 py-4 text-left transition-shadow hover:shadow-md ${
                        isSelected ? 'border-primary/40 shadow-sm' : 'border-border/60'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-4">
                        <div className="min-w-0 space-y-1.5">
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge variant={goalStatusTone(goal.status)}>
                              {goal.status.replace(/_/g, ' ')}
                            </Badge>
                            <span className="text-xs text-muted-foreground">
                              {getMetricDisplayLabel(metricDefinition, goal.metric_key)}
                            </span>
                          </div>
                          <h3 className="text-sm font-semibold leading-snug">{goal.name}</h3>
                        </div>
                        <div className="text-right">
                          <p className="text-[10px] uppercase tracking-widest text-muted-foreground">
                            Attainment
                          </p>
                          <p className="text-lg font-bold tabular-nums">{progress.toFixed(0)}%</p>
                        </div>
                      </div>

                      <div className="mt-3 flex flex-wrap gap-4 text-sm text-muted-foreground">
                        <span>
                          {formatMetricValue(goal.current_value ?? goal.baseline_value, metricDefinition)} /{' '}
                          {formatMetricValue(goal.target_value, metricDefinition)}
                        </span>
                        <span>{goal.open_insight_count} insights</span>
                        <span>{goal.pending_recommendation_count} recommendations</span>
                      </div>
                    </button>
                  )
                })}
              </div>

              <div className="lg:col-span-3">
                {!selectedGoalDetail ? (
                  <Card>
                    <CardContent className="py-16 text-center text-sm text-muted-foreground">
                      Select a goal to inspect its latest evaluation, insights, and recommendations.
                    </CardContent>
                  </Card>
                ) : (
                  <div className="space-y-4">
                    <Card>
                      <CardHeader className="pb-2">
                        <CardTitle className="flex items-center justify-between gap-4 text-base">
                          <span>{selectedGoalDetail.goal.name}</span>
                          <div className="flex flex-wrap gap-2">
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => evaluateMutation.mutate()}
                              disabled={evaluateMutation.isPending}
                            >
                              {evaluateMutation.isPending ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                              ) : (
                                <RefreshCw className="mr-2 h-4 w-4" />
                              )}
                              Evaluate
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => generateInsightsMutation.mutate()}
                              disabled={generateInsightsMutation.isPending}
                            >
                              {generateInsightsMutation.isPending ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                              ) : (
                                <Sparkles className="mr-2 h-4 w-4" />
                              )}
                              Generate Insights
                            </Button>
                            <Button
                              size="sm"
                              onClick={() => generateRecommendationsMutation.mutate()}
                              disabled={generateRecommendationsMutation.isPending}
                            >
                              {generateRecommendationsMutation.isPending ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                              ) : (
                                <Lightbulb className="mr-2 h-4 w-4" />
                              )}
                              Generate Recommendations
                            </Button>
                          </div>
                        </CardTitle>
                      </CardHeader>
                      <CardContent className="space-y-4">
                        <div className="grid gap-4 md:grid-cols-3">
                          <div className="rounded-lg border border-border/50 p-4">
                            <p className="text-xs uppercase tracking-wide text-muted-foreground">
                              Current / Target
                            </p>
                            <p className="mt-2 text-xl font-bold">
                              {formatMetricValue(
                                selectedGoalDetail.latest_observation?.value ??
                                  selectedGoalDetail.baseline_snapshot.value,
                                selectedMetricDefinition
                              )}
                              {' / '}
                              {formatMetricValue(
                                selectedGoalDetail.goal.target_value,
                                selectedMetricDefinition
                              )}
                            </p>
                          </div>
                          <div className="rounded-lg border border-border/50 p-4">
                            <p className="text-xs uppercase tracking-wide text-muted-foreground">
                              Baseline
                            </p>
                            <p className="mt-2 text-xl font-bold">
                              {formatMetricValue(
                                selectedGoalDetail.baseline_snapshot.value,
                                selectedMetricDefinition
                              )}
                            </p>
                            <p className="mt-1 text-xs text-muted-foreground">
                              {selectedGoalDetail.baseline_snapshot.sample_size} samples
                            </p>
                          </div>
                          <div className="rounded-lg border border-border/50 p-4">
                            <p className="text-xs uppercase tracking-wide text-muted-foreground">
                              Last Evaluation
                            </p>
                            <p className="mt-2 text-xl font-bold">
                              {selectedGoalDetail.latest_evaluation
                                ? selectedGoalDetail.latest_evaluation.status.replace(/_/g, ' ')
                                : 'Not evaluated'}
                            </p>
                            <p className="mt-1 text-xs text-muted-foreground">
                              {selectedGoalDetail.latest_evaluation
                                ? formatDate(selectedGoalDetail.latest_evaluation.created_at)
                                : 'Run evaluation'}
                            </p>
                          </div>
                        </div>

                        {selectedGoalDetail.latest_evaluation && (
                          <div className="rounded-lg border border-border/50 p-4 text-sm">
                            <div className="flex flex-wrap gap-4">
                              <span>
                                Delta from baseline{' '}
                                <strong>
                                  {formatMetricDelta(
                                    selectedGoalDetail.latest_evaluation.delta_from_baseline,
                                    selectedMetricDefinition
                                  )}
                                </strong>
                              </span>
                              <span>
                                Distance to target{' '}
                                <strong>
                                  {formatMetricDelta(
                                    selectedGoalDetail.latest_evaluation.distance_to_target,
                                    selectedMetricDefinition
                                  )}
                                </strong>
                              </span>
                              <span>
                                Sample{' '}
                                <strong>
                                  {selectedGoalDetail.latest_evaluation.sample_size_sufficient
                                    ? 'sufficient'
                                    : 'insufficient'}
                                </strong>
                              </span>
                            </div>
                          </div>
                        )}
                      </CardContent>
                    </Card>

                    <div className="grid gap-4 xl:grid-cols-2">
                      <Card>
                        <CardHeader className="pb-2">
                          <CardTitle className="text-base">Goal Insights</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-3">
                          {selectedGoalDetail.insights.length === 0 ? (
                            <p className="text-sm text-muted-foreground">
                              No insights generated for this goal yet.
                            </p>
                          ) : (
                            selectedGoalDetail.insights.map((insight) => (
                              <div
                                key={insight.insight_id}
                                className="space-y-2 rounded-xl border border-border/60 px-4 py-3"
                              >
                                <div className="flex flex-wrap items-center gap-2">
                                  <Badge variant="outline">
                                    {insight.blocker_kind.replace(/_/g, ' ')}
                                  </Badge>
                                  <Badge variant="secondary">{insight.status}</Badge>
                                </div>
                                <h3 className="text-sm font-semibold">{insight.title}</h3>
                                <p className="text-sm text-muted-foreground">{insight.summary}</p>
                                <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
                                  <span>Severity {insight.severity.toFixed(2)}</span>
                                  <span>Occurrences {insight.occurrence_count}</span>
                                  <span>Rank {insight.rank_score.toFixed(2)}</span>
                                </div>
                              </div>
                            ))
                          )}
                        </CardContent>
                      </Card>

                      <Card>
                        <CardHeader className="pb-2">
                          <CardTitle className="text-base">Recommendations</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-3">
                          {selectedGoalDetail.recommendations.length === 0 ? (
                            <p className="text-sm text-muted-foreground">
                              No recommendations generated for this goal yet.
                            </p>
                          ) : (
                            selectedGoalDetail.recommendations.map((recommendation) => (
                              <div
                                key={recommendation.recommendation_id}
                                className="space-y-2 rounded-xl border border-border/60 px-4 py-3"
                              >
                                <div className="flex items-start justify-between gap-3">
                                  <div className="space-y-1">
                                    <Badge variant="outline">
                                      {recommendation.category.replace(/_/g, ' ')}
                                    </Badge>
                                    <h3 className="text-sm font-semibold">{recommendation.title}</h3>
                                  </div>
                                  <span className="inline-flex items-center gap-1 text-xs text-green-500">
                                    <CheckCircle2 className="h-3.5 w-3.5" />
                                    {recommendation.status.replace(/_/g, ' ')}
                                  </span>
                                </div>
                                <p className="text-sm text-muted-foreground">
                                  {recommendation.summary}
                                </p>
                                <p className="text-xs text-muted-foreground/80">
                                  <ArrowRight className="mr-1 inline h-3 w-3" />
                                  {recommendation.rationale}
                                </p>
                                <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
                                  <span>
                                    Projected impact{' '}
                                    {formatMetricDelta(
                                      recommendation.projected_impact_min,
                                      selectedMetricDefinition
                                    )}{' '}
                                    to{' '}
                                    {formatMetricDelta(
                                      recommendation.projected_impact_max,
                                      selectedMetricDefinition
                                    )}
                                  </span>
                                  <span>
                                    Confidence {Math.round(recommendation.projected_confidence * 100)}%
                                  </span>
                                </div>
                              </div>
                            ))
                          )}
                        </CardContent>
                      </Card>
                    </div>

                    {(selectedGoalDetail.execution_intents.length > 0 ||
                      selectedGoalDetail.execution_results.length > 0) && (
                      <Card>
                        <CardHeader className="pb-2">
                          <CardTitle className="text-base">Execution Trail</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-3">
                          {selectedGoalDetail.execution_intents.map((intent) => (
                            <div
                              key={intent.execution_intent_id}
                              className="rounded-lg border border-border/50 p-3 text-sm"
                            >
                              <div className="font-medium">
                                {intent.action_type} · {intent.execution_mode}
                              </div>
                              <div className="mt-1 text-xs text-muted-foreground">
                                Requested {formatDate(intent.created_at)}
                              </div>
                            </div>
                          ))}
                          {selectedGoalDetail.execution_results.map((result) => (
                            <div
                              key={result.execution_result_id}
                              className="rounded-lg border border-border/50 p-3 text-sm"
                            >
                              <div className="font-medium">{result.status.replace(/_/g, ' ')}</div>
                              <div className="mt-1 text-xs text-muted-foreground">
                                Recorded {formatDate(result.created_at)}
                              </div>
                            </div>
                          ))}
                        </CardContent>
                      </Card>
                    )}

                    {(selectedGoalDetail.experiments.length > 0 ||
                      selectedGoalDetail.impact_assessments.length > 0) && (
                      <Card>
                        <CardHeader className="pb-2">
                          <CardTitle className="text-base">Validation Layer</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-3 text-sm">
                          {selectedGoalDetail.experiments.map((experiment) => (
                            <div
                              key={experiment.experiment_id}
                              className="rounded-lg border border-border/50 p-3"
                            >
                              <div className="font-medium">{experiment.name}</div>
                              <div className="mt-1 text-muted-foreground">{experiment.hypothesis}</div>
                            </div>
                          ))}
                          {selectedGoalDetail.impact_assessments.map((assessment) => (
                            <div
                              key={assessment.assessment_id}
                              className="rounded-lg border border-border/50 p-3"
                            >
                              <div className="font-medium">
                                Observed change{' '}
                                {formatMetricDelta(
                                  assessment.observed_change,
                                  selectedMetricDefinition
                                )}
                              </div>
                              <div className="mt-1 text-muted-foreground">
                                Attribution {assessment.attribution_confidence.replace(/_/g, ' ')}
                              </div>
                            </div>
                          ))}
                        </CardContent>
                      </Card>
                    )}
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </DashboardLayout>
  )
}
