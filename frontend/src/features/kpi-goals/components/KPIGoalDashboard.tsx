import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { AlertCircle, ArrowRight, Sparkles, Target } from 'lucide-react';

import { Button } from '@/components/atoms/button';
import { Card } from '@/components/atoms/card';
import { Badge } from '@/components/atoms/badge';
import { MetricCardSkeleton, Skeleton } from '@/components/atoms/skeleton';
import {
  kpiGoalService,
  type GoalSummaryReadModel,
} from '@/api/services/kpi-goals.service';
import {
  calculateGoalProgress,
  formatMetricValue,
  getMetricDefinition,
  getMetricDisplayLabel,
  goalStatusTone,
  useKpiMetricDefinitions,
} from '@/features/kpi-goals/utils/metric-display';

export interface KPIGoalDashboardProps {
  agentId?: string;
}

export function KPIGoalDashboard({ agentId }: KPIGoalDashboardProps) {
  const navigate = useNavigate();
  const { data: metricDefinitions = [] } = useKpiMetricDefinitions();

  const { data: goals = [], isLoading, error, refetch } = useQuery({
    queryKey: ['kpi', 'goals', 'dashboard', agentId ?? 'all'],
    queryFn: async () => {
      if (!agentId) {
        return kpiGoalService.listGoals();
      }

      const scope = await kpiGoalService.ensureAgentScope(agentId);
      return kpiGoalService.listGoals({ scope_id: scope.scope_id });
    },
  });

  const stats = useMemo(() => {
    const activeStatuses = new Set(['active', 'on_track', 'at_risk', 'stalled']);
    const openGoals = goals.filter((goal) => activeStatuses.has(goal.status)).length;
    const goalsWithRisk = goals.filter((goal) => goal.status === 'at_risk' || goal.status === 'stalled').length;
    const totalInsights = goals.reduce((sum, goal) => sum + goal.open_insight_count, 0);
    const totalRecommendations = goals.reduce((sum, goal) => sum + goal.pending_recommendation_count, 0);

    const progressValues = goals
      .map((goal) => calculateGoalProgress(goal, getMetricDefinition(metricDefinitions, goal.metric_key)))
      .filter((value) => Number.isFinite(value));

    const averageProgress =
      progressValues.length > 0
        ? progressValues.reduce((sum, value) => sum + value, 0) / progressValues.length
        : 0;

    return {
      totalGoals: goals.length,
      openGoals,
      goalsWithRisk,
      totalInsights,
      totalRecommendations,
      averageProgress,
    };
  }, [goals, metricDefinitions]);

  const focusGoals = useMemo(() => {
    return [...goals]
      .sort((left, right) => {
        if (right.open_insight_count !== left.open_insight_count) {
          return right.open_insight_count - left.open_insight_count;
        }
        if (right.pending_recommendation_count !== left.pending_recommendation_count) {
          return right.pending_recommendation_count - left.pending_recommendation_count;
        }
        return (right.progress_ratio ?? 0) - (left.progress_ratio ?? 0);
      })
      .slice(0, 5);
  }, [goals]);

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div className="space-y-2">
            <Skeleton className="h-7 w-48" />
            <Skeleton className="h-4 w-80" />
          </div>
          <Skeleton className="h-10 w-40 rounded-md" />
        </div>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <MetricCardSkeleton />
          <MetricCardSkeleton />
          <MetricCardSkeleton />
          <MetricCardSkeleton />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <Card className="p-8">
        <div className="flex flex-col items-center justify-center space-y-4 text-center">
          <AlertCircle className="h-12 w-12 text-destructive" />
          <p className="text-muted-foreground">Failed to load KPI goals.</p>
          <Button onClick={() => refetch()}>Retry</Button>
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">KPI Goals</h2>
          <p className="text-muted-foreground">
            Goal progress, measurable blockers, and recommendation pressure from the new KPI runtime.
          </p>
        </div>
        <Button onClick={() => navigate('/kpi-goals?tab=create')}>
          <Target className="mr-2 h-4 w-4" />
          Create Goal
        </Button>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card className="p-5">
          <div className="text-sm text-muted-foreground">Total Goals</div>
          <div className="mt-2 text-3xl font-bold">{stats.totalGoals}</div>
          <div className="mt-2 text-xs text-muted-foreground">{stats.openGoals} currently in motion</div>
        </Card>
        <Card className="p-5">
          <div className="text-sm text-muted-foreground">Average Attainment</div>
          <div className="mt-2 text-3xl font-bold">{stats.averageProgress.toFixed(0)}%</div>
          <div className="mt-2 text-xs text-muted-foreground">Normalized from each goal&apos;s baseline and target</div>
        </Card>
        <Card className="p-5">
          <div className="text-sm text-muted-foreground">Open Insights</div>
          <div className="mt-2 text-3xl font-bold">{stats.totalInsights}</div>
          <div className="mt-2 text-xs text-muted-foreground">{stats.goalsWithRisk} goals need intervention</div>
        </Card>
        <Card className="p-5">
          <div className="text-sm text-muted-foreground">Pending Recommendations</div>
          <div className="mt-2 text-3xl font-bold">{stats.totalRecommendations}</div>
          <div className="mt-2 text-xs text-muted-foreground">Generated against measurable goal gaps</div>
        </Card>
      </div>

      <Card className="overflow-hidden">
        <div className="border-b p-4">
          <h3 className="font-semibold">Focus Goals</h3>
          <p className="text-sm text-muted-foreground">
            Ranked by open insight pressure and recommendation backlog.
          </p>
        </div>

        {focusGoals.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-4 px-8 py-12 text-center">
            <div className="rounded-2xl bg-muted p-4">
              <Sparkles className="h-8 w-8 text-muted-foreground" />
            </div>
            <div>
              <h3 className="font-medium">No KPI goals yet</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                Create a goal from a measured baseline to start generating insights and recommendations.
              </p>
            </div>
          </div>
        ) : (
          <div className="divide-y">
            {focusGoals.map((goal: GoalSummaryReadModel) => {
              const metricDefinition = getMetricDefinition(metricDefinitions, goal.metric_key);
              const progress = calculateGoalProgress(goal, metricDefinition);

              return (
                <button
                  key={goal.goal_id}
                  className="flex w-full items-center justify-between gap-4 px-4 py-4 text-left transition-colors hover:bg-muted/40"
                  onClick={() => navigate(`/kpi-goals/${goal.goal_id}`)}
                  type="button"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <div className="font-medium">{goal.name}</div>
                      <Badge variant={goalStatusTone(goal.status)}>{goal.status.replace(/_/g, ' ')}</Badge>
                    </div>
                    <div className="mt-1 text-sm text-muted-foreground">
                      {getMetricDisplayLabel(metricDefinition, goal.metric_key)}
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-4 text-sm">
                      <span>
                        {formatMetricValue(goal.current_value ?? goal.baseline_value, metricDefinition)} /{' '}
                        {formatMetricValue(goal.target_value, metricDefinition)}
                      </span>
                      <span>{progress.toFixed(0)}% attained</span>
                      <span>{goal.open_insight_count} insights</span>
                      <span>{goal.pending_recommendation_count} recommendations</span>
                    </div>
                  </div>
                  <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                </button>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
