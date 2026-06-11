import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Lightbulb,
  Loader2,
  RefreshCw,
  Sparkles,
  Target,
} from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/components/atoms/badge';
import { Button } from '@/components/atoms/button';
import { Card } from '@/components/atoms/card';
import { Skeleton } from '@/components/atoms/skeleton';
import { kpiGoalService } from '@/api/services/kpi-goals.service';
import { formatDate } from '@/lib/utils';
import {
  calculateGoalProgress,
  formatMetricDelta,
  formatMetricValue,
  getMetricDefinition,
  getMetricDisplayLabel,
  goalStatusTone,
  useKpiMetricDefinitions,
} from '@/features/kpi-goals/utils/metric-display';

export interface KPIGoalDetailProps {
  goalId: string;
  onBack?: () => void;
}

export function KPIGoalDetail({ goalId, onBack }: KPIGoalDetailProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: metricDefinitions = [] } = useKpiMetricDefinitions();

  const {
    data: detail,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ['kpi', 'goal-detail', goalId],
    queryFn: () => kpiGoalService.getGoal(goalId),
  });

  const evaluateMutation = useMutation({
    mutationFn: async () => {
      if (!detail) {
        throw new Error('Goal detail is not loaded.');
      }

      const observation =
        detail.latest_observation ??
        (await kpiGoalService.refreshObservation(detail.scope.scope_id, detail.goal.metric_key, {}));

      return kpiGoalService.evaluateGoal(goalId, {
        observation_id: observation.observation_id,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['kpi', 'goal-detail', goalId] });
      queryClient.invalidateQueries({ queryKey: ['kpi', 'goals'] });
      toast.success('Goal evaluation refreshed');
    },
    onError: (mutationError) => {
      toast.error(mutationError instanceof Error ? mutationError.message : 'Failed to evaluate goal');
    },
  });

  const generateInsightsMutation = useMutation({
    mutationFn: () => kpiGoalService.generateGoalInsights(goalId),
    onSuccess: (items) => {
      queryClient.invalidateQueries({ queryKey: ['kpi', 'goal-detail', goalId] });
      queryClient.invalidateQueries({ queryKey: ['kpi', 'goals'] });
      toast.success(`Generated ${items.length} insight${items.length === 1 ? '' : 's'}`);
    },
    onError: (mutationError) => {
      toast.error(mutationError instanceof Error ? mutationError.message : 'Failed to generate insights');
    },
  });

  const generateRecommendationsMutation = useMutation({
    mutationFn: () => kpiGoalService.generateGoalRecommendations(goalId),
    onSuccess: (items) => {
      queryClient.invalidateQueries({ queryKey: ['kpi', 'goal-detail', goalId] });
      queryClient.invalidateQueries({ queryKey: ['kpi', 'goals'] });
      toast.success(`Generated ${items.length} recommendation${items.length === 1 ? '' : 's'}`);
    },
    onError: (mutationError) => {
      toast.error(
        mutationError instanceof Error ? mutationError.message : 'Failed to generate recommendations'
      );
    },
  });

  const goalStatusMutation = useMutation({
    mutationFn: (status: 'active' | 'paused' | 'completed' | 'abandoned') =>
      kpiGoalService.updateGoalStatus(goalId, status),
    onSuccess: (updated) => {
      queryClient.invalidateQueries({ queryKey: ['kpi', 'goal-detail', goalId] });
      queryClient.invalidateQueries({ queryKey: ['kpi', 'goals'] });
      toast.success(`Goal ${updated.status.replace(/_/g, ' ')}`);
    },
    onError: (mutationError) => {
      toast.error(mutationError instanceof Error ? mutationError.message : 'Failed to update goal status');
    },
  });

  const insightStatusMutation = useMutation({
    mutationFn: ({ insightId, status }: { insightId: string; status: 'accepted' | 'dismissed' }) =>
      kpiGoalService.updateInsightStatus(insightId, status),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['kpi', 'goal-detail', goalId] });
    },
    onError: (mutationError) => {
      toast.error(mutationError instanceof Error ? mutationError.message : 'Failed to update insight');
    },
  });

  const recommendationStatusMutation = useMutation({
    mutationFn: ({ recommendationId, status }: { recommendationId: string; status: 'ready_for_review' | 'approved' | 'rejected' }) =>
      kpiGoalService.updateRecommendationStatus(recommendationId, status),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['kpi', 'goal-detail', goalId] });
    },
    onError: (mutationError) => {
      toast.error(mutationError instanceof Error ? mutationError.message : 'Failed to update recommendation');
    },
  });

  const handleBack = () => {
    if (onBack) {
      onBack();
      return;
    }
    navigate('/kpi-goals');
  };

  const model = useMemo(() => {
    if (!detail) {
      return null;
    }

    const definition = getMetricDefinition(metricDefinitions, detail.goal.metric_key);
    const currentValue = detail.latest_observation?.value ?? detail.baseline_snapshot.value;
    const progress = calculateGoalProgress(
      {
        baseline_value: detail.baseline_snapshot.value,
        target_value: detail.goal.target_value,
        current_value: currentValue,
        progress_ratio: detail.latest_evaluation?.progress_ratio ?? null,
      },
      definition
    );

    return {
      definition,
      currentValue,
      progress,
    };
  }, [detail, metricDefinitions]);

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-40" />
        <Skeleton className="h-24 w-full" />
        <div className="grid gap-4 md:grid-cols-3">
          <Skeleton className="h-28 w-full" />
          <Skeleton className="h-28 w-full" />
          <Skeleton className="h-28 w-full" />
        </div>
      </div>
    );
  }

  if (error || !detail || !model) {
    return (
      <Card className="p-6">
        <div className="flex items-start gap-3">
          <AlertCircle className="mt-0.5 h-5 w-5 text-destructive" />
          <div>
            <div className="font-medium">Failed to load KPI goal</div>
            <div className="mt-1 text-sm text-muted-foreground">
              {error instanceof Error ? error.message : 'The goal detail could not be loaded.'}
            </div>
            <div className="mt-4 flex gap-2">
              <Button onClick={() => refetch()}>Retry</Button>
              <Button onClick={handleBack} variant="outline">Back</Button>
            </div>
          </div>
        </div>
      </Card>
    );
  }

  const { goal, scope, baseline_snapshot, latest_observation, latest_evaluation, insights, recommendations } = detail;
  const { definition, currentValue, progress } = model;
  const metricLabel = getMetricDisplayLabel(definition, goal.metric_key);

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-3">
          <Button className="-ml-2" onClick={handleBack} size="sm" variant="ghost">
            <ArrowLeft className="mr-2 h-4 w-4" />
            Back to Goals
          </Button>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-3xl font-bold">{goal.name}</h1>
            <Badge variant={goalStatusTone(goal.status)}>{goal.status.replace(/_/g, ' ')}</Badge>
          </div>
          <div className="text-muted-foreground">{goal.description || metricLabel}</div>
          <div className="flex flex-wrap gap-4 text-sm text-muted-foreground">
            <span>{metricLabel}</span>
            <span>Scope: {scope.display_name || scope.scope_kind}</span>
            <span>Target by {formatDate(goal.target_at)}</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            disabled={evaluateMutation.isPending}
            onClick={() => evaluateMutation.mutate()}
            variant="outline"
          >
            {evaluateMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
            Evaluate
          </Button>
          <Button
            disabled={generateInsightsMutation.isPending}
            onClick={() => generateInsightsMutation.mutate()}
            variant="outline"
          >
            {generateInsightsMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
            Generate Insights
          </Button>
          <Button
            disabled={generateRecommendationsMutation.isPending}
            onClick={() => generateRecommendationsMutation.mutate()}
          >
            {generateRecommendationsMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Lightbulb className="mr-2 h-4 w-4" />}
            Generate Recommendations
          </Button>
          {goal.status === 'active' || goal.status === 'on_track' || goal.status === 'at_risk' || goal.status === 'stalled' ? (
            <Button
              disabled={goalStatusMutation.isPending}
              onClick={() => goalStatusMutation.mutate('paused')}
              variant="outline"
            >
              Pause
            </Button>
          ) : goal.status === 'paused' ? (
            <Button
              disabled={goalStatusMutation.isPending}
              onClick={() => goalStatusMutation.mutate('active')}
              variant="outline"
            >
              Resume
            </Button>
          ) : null}
          {goal.status !== 'completed' && goal.status !== 'abandoned' && (
            <Button
              disabled={goalStatusMutation.isPending}
              onClick={() => goalStatusMutation.mutate('completed')}
              variant="outline"
            >
              Complete
            </Button>
          )}
          {goal.status !== 'abandoned' && goal.status !== 'completed' && (
            <Button
              disabled={goalStatusMutation.isPending}
              onClick={() => goalStatusMutation.mutate('abandoned')}
              variant="outline"
            >
              Abandon
            </Button>
          )}
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <Card className="p-5">
          <div className="text-sm text-muted-foreground">Current / Target</div>
          <div className="mt-2 text-2xl font-bold">
            {formatMetricValue(currentValue, definition)} / {formatMetricValue(goal.target_value, definition)}
          </div>
          <div className="mt-2 text-xs text-muted-foreground">
            Baseline {formatMetricValue(baseline_snapshot.value, definition)}
          </div>
        </Card>
        <Card className="p-5">
          <div className="text-sm text-muted-foreground">Attainment</div>
          <div className="mt-2 text-2xl font-bold">{progress.toFixed(0)}%</div>
          <div className="mt-2 text-xs text-muted-foreground">
            {latest_evaluation
              ? `Distance to target ${formatMetricDelta(latest_evaluation.distance_to_target, definition)}`
              : 'No evaluation recorded yet'}
          </div>
        </Card>
        <Card className="p-5">
          <div className="text-sm text-muted-foreground">Insight Pressure</div>
          <div className="mt-2 text-2xl font-bold">
            {insights.length} / {recommendations.length}
          </div>
          <div className="mt-2 text-xs text-muted-foreground">
            insights / recommendations linked to this goal
          </div>
        </Card>
      </div>

      <Card className="p-6">
        <div className="space-y-3">
          <div>
            <h2 className="text-xl font-semibold">Latest Measurement</h2>
            <p className="text-sm text-muted-foreground">
              Baseline, latest observation, and latest evaluation from the KPI runtime.
            </p>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="rounded-lg border p-4">
              <div className="text-sm text-muted-foreground">Baseline Snapshot</div>
              <div className="mt-2 text-2xl font-semibold">
                {formatMetricValue(baseline_snapshot.value, definition)}
              </div>
              <div className="mt-2 text-sm text-muted-foreground">
                {baseline_snapshot.baseline_source.replace(/_/g, ' ')} • {baseline_snapshot.sample_size} samples
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                {formatDate(baseline_snapshot.period_start)} to {formatDate(baseline_snapshot.period_end)}
              </div>
            </div>
            <div className="rounded-lg border p-4">
              <div className="text-sm text-muted-foreground">Latest Observation</div>
              <div className="mt-2 text-2xl font-semibold">
                {latest_observation
                  ? formatMetricValue(latest_observation.value, definition)
                  : 'No observation'}
              </div>
              <div className="mt-2 text-sm text-muted-foreground">
                {latest_observation
                  ? `${latest_observation.sample_size} samples • ${Math.round(latest_observation.confidence * 100)}% confidence`
                  : 'Run an evaluation to produce one'}
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                {latest_observation ? `${formatDate(latest_observation.period_start)} to ${formatDate(latest_observation.period_end)}` : ''}
              </div>
            </div>
          </div>
          {latest_evaluation && (
            <div className="rounded-lg border p-4">
              <div className="flex flex-wrap items-center gap-2">
                <div className="font-medium">Latest Evaluation</div>
                <Badge variant={goalStatusTone(latest_evaluation.status)}>
                  {latest_evaluation.status.replace(/_/g, ' ')}
                </Badge>
              </div>
              <div className="mt-2 grid gap-3 md:grid-cols-3 text-sm">
                <div>
                  <div className="text-muted-foreground">Delta From Baseline</div>
                  <div className="mt-1 font-medium">
                    {formatMetricDelta(latest_evaluation.delta_from_baseline, definition)}
                  </div>
                </div>
                <div>
                  <div className="text-muted-foreground">Distance To Target</div>
                  <div className="mt-1 font-medium">
                    {formatMetricDelta(latest_evaluation.distance_to_target, definition)}
                  </div>
                </div>
                <div>
                  <div className="text-muted-foreground">Sample Sufficiency</div>
                  <div className="mt-1 font-medium">
                    {latest_evaluation.sample_size_sufficient ? 'Sufficient' : 'Insufficient'}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </Card>

      <Card className="p-6">
        <div className="space-y-4">
          <div>
            <h2 className="text-xl font-semibold">Insights</h2>
            <p className="text-sm text-muted-foreground">
              Every insight exists because this goal has a measurable blocker.
            </p>
          </div>
          {insights.length === 0 ? (
            <p className="text-sm text-muted-foreground">No insights generated for this goal yet.</p>
          ) : (
            <div className="space-y-3">
              {insights.map((insight) => (
                <div key={insight.insight_id} className="rounded-lg border p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <div className="font-medium">{insight.title}</div>
                      <Badge variant="outline">{insight.blocker_kind.replace(/_/g, ' ')}</Badge>
                      <Badge variant="secondary">{insight.status}</Badge>
                    </div>
                    {insight.status === 'open' && (
                      <div className="flex gap-2">
                        <Button
                          disabled={insightStatusMutation.isPending}
                          onClick={() => insightStatusMutation.mutate({ insightId: insight.insight_id, status: 'accepted' })}
                          size="sm"
                          variant="outline"
                        >
                          Accept
                        </Button>
                        <Button
                          disabled={insightStatusMutation.isPending}
                          onClick={() => insightStatusMutation.mutate({ insightId: insight.insight_id, status: 'dismissed' })}
                          size="sm"
                          variant="ghost"
                        >
                          Dismiss
                        </Button>
                      </div>
                    )}
                  </div>
                  <p className="mt-2 text-sm text-muted-foreground">{insight.summary}</p>
                  <div className="mt-3 flex flex-wrap gap-4 text-xs text-muted-foreground">
                    <span>Severity {insight.severity.toFixed(2)}</span>
                    <span>Occurrences {insight.occurrence_count}</span>
                    <span>Rank {insight.rank_score.toFixed(2)}</span>
                    <span>Updated {formatDate(insight.updated_at)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>

      <Card className="p-6">
        <div className="space-y-4">
          <div>
            <h2 className="text-xl font-semibold">Recommendations</h2>
            <p className="text-sm text-muted-foreground">
              Candidate interventions generated against the goal&apos;s current blockers.
            </p>
          </div>
          {recommendations.length === 0 ? (
            <p className="text-sm text-muted-foreground">No recommendations generated for this goal yet.</p>
          ) : (
            <div className="space-y-3">
              {recommendations.map((recommendation) => (
                <div key={recommendation.recommendation_id} className="rounded-lg border p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <div className="font-medium">{recommendation.title}</div>
                      <Badge variant="outline">{recommendation.category.replace(/_/g, ' ')}</Badge>
                      <Badge variant="secondary">{recommendation.status.replace(/_/g, ' ')}</Badge>
                    </div>
                    <div className="flex gap-2">
                      {recommendation.status === 'draft' && (
                        <Button
                          disabled={recommendationStatusMutation.isPending}
                          onClick={() => recommendationStatusMutation.mutate({ recommendationId: recommendation.recommendation_id, status: 'ready_for_review' })}
                          size="sm"
                          variant="outline"
                        >
                          Submit for Review
                        </Button>
                      )}
                      {recommendation.status === 'ready_for_review' && (
                        <>
                          <Button
                            disabled={recommendationStatusMutation.isPending}
                            onClick={() => recommendationStatusMutation.mutate({ recommendationId: recommendation.recommendation_id, status: 'approved' })}
                            size="sm"
                            variant="outline"
                          >
                            Approve
                          </Button>
                          <Button
                            disabled={recommendationStatusMutation.isPending}
                            onClick={() => recommendationStatusMutation.mutate({ recommendationId: recommendation.recommendation_id, status: 'rejected' })}
                            size="sm"
                            variant="ghost"
                          >
                            Reject
                          </Button>
                        </>
                      )}
                    </div>
                  </div>
                  <p className="mt-2 text-sm text-muted-foreground">{recommendation.summary}</p>
                  <p className="mt-2 text-sm">{recommendation.rationale}</p>
                  <div className="mt-3 flex flex-wrap gap-4 text-xs text-muted-foreground">
                    <span>
                      Projected impact {formatMetricDelta(recommendation.projected_impact_min, definition)} to{' '}
                      {formatMetricDelta(recommendation.projected_impact_max, definition)}
                    </span>
                    <span>Confidence {Math.round(recommendation.projected_confidence * 100)}%</span>
                    <span>Updated {formatDate(recommendation.updated_at)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>

      {(detail.execution_intents.length > 0 || detail.execution_results.length > 0) && (
        <Card className="p-6">
          <div className="space-y-4">
            <div>
              <h2 className="text-xl font-semibold">Execution History</h2>
              <p className="text-sm text-muted-foreground">
                Backend-tracked execution intent and result records for this goal.
              </p>
            </div>
            {detail.execution_intents.map((intent) => (
              <div key={intent.execution_intent_id} className="rounded-lg border p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Target className="h-4 w-4 text-muted-foreground" />
                  <div className="font-medium">{intent.action_type}</div>
                  <Badge variant="outline">{intent.execution_mode}</Badge>
                  <Badge variant="secondary">{intent.adapter_kind}</Badge>
                </div>
                <div className="mt-2 text-xs text-muted-foreground">
                  Requested {formatDate(intent.created_at)}
                </div>
              </div>
            ))}
            {detail.execution_results.map((result) => (
              <div key={result.execution_result_id} className="rounded-lg border p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <CheckCircle2 className="h-4 w-4 text-muted-foreground" />
                  <div className="font-medium">{result.status.replace(/_/g, ' ')}</div>
                </div>
                <div className="mt-2 text-xs text-muted-foreground">
                  Recorded {formatDate(result.created_at)}
                </div>
                {result.error_message && (
                  <div className="mt-2 text-sm text-destructive">{result.error_message}</div>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
