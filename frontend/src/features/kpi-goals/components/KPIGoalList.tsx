import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { AlertCircle, Plus, Search, Target } from 'lucide-react';

import { Badge } from '@/components/atoms/badge';
import { Button } from '@/components/atoms/button';
import { Card } from '@/components/atoms/card';
import { Input } from '@/components/atoms/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select';
import { MetricCardSkeleton } from '@/components/atoms/skeleton';
import { kpiGoalService } from '@/api/services/kpi-goals.service';
import {
  calculateGoalProgress,
  formatMetricValue,
  getMetricDefinition,
  getMetricDisplayLabel,
  goalStatusTone,
  useKpiMetricDefinitions,
} from '@/features/kpi-goals/utils/metric-display';

export interface KPIGoalListProps {
  agentId?: string;
}

export function KPIGoalList({ agentId }: KPIGoalListProps) {
  const navigate = useNavigate();
  const { data: metricDefinitions = [] } = useKpiMetricDefinitions();
  const [searchQuery, setSearchQuery] = useState('');
  const [metricFilter, setMetricFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');

  const { data: goals = [], isLoading, error, refetch } = useQuery({
    queryKey: ['kpi', 'goals', 'list', agentId ?? 'all'],
    queryFn: async () => {
      if (!agentId) {
        return kpiGoalService.listGoals();
      }

      const scope = await kpiGoalService.ensureAgentScope(agentId);
      return kpiGoalService.listGoals({ scope_id: scope.scope_id });
    },
  });

  const filteredGoals = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();

    return goals
      .filter((goal) => metricFilter === 'all' || goal.metric_key === metricFilter)
      .filter((goal) => statusFilter === 'all' || goal.status === statusFilter)
      .filter((goal) => {
        if (!query) {
          return true;
        }

        return (
          goal.name.toLowerCase().includes(query) ||
          goal.metric_key.toLowerCase().includes(query)
        );
      })
      .sort((left, right) => {
        if (right.open_insight_count !== left.open_insight_count) {
          return right.open_insight_count - left.open_insight_count;
        }
        if (right.pending_recommendation_count !== left.pending_recommendation_count) {
          return right.pending_recommendation_count - left.pending_recommendation_count;
        }
        return left.name.localeCompare(right.name);
      });
  }, [goals, metricFilter, searchQuery, statusFilter]);

  const metricOptions = useMemo(() => {
    return [...metricDefinitions].sort((left, right) => left.label.localeCompare(right.label));
  }, [metricDefinitions]);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <MetricCardSkeleton />
        <MetricCardSkeleton />
        <MetricCardSkeleton />
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
          <h2 className="text-2xl font-bold tracking-tight">Insights</h2>
          <p className="text-muted-foreground">Direct view of goal state from the KPI runtime.</p>
        </div>
        <Button onClick={() => navigate('/kpi-goals?tab=create')}>
          <Plus className="mr-2 h-4 w-4" />
          Create Goal
        </Button>
      </div>

      <Card className="p-4">
        <div className="flex flex-col gap-3 lg:flex-row">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-10"
              placeholder="Search by goal name or metric key"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
            />
          </div>
          <Select value={metricFilter} onValueChange={setMetricFilter}>
            <SelectTrigger className="w-full lg:w-64">
              <SelectValue placeholder="All metrics" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All metrics</SelectItem>
              {metricOptions.map((definition) => (
                <SelectItem key={definition.metric_key} value={definition.metric_key}>
                  {definition.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="w-full lg:w-52">
              <SelectValue placeholder="All statuses" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="draft">Draft</SelectItem>
              <SelectItem value="active">Active</SelectItem>
              <SelectItem value="on_track">On Track</SelectItem>
              <SelectItem value="at_risk">At Risk</SelectItem>
              <SelectItem value="stalled">Stalled</SelectItem>
              <SelectItem value="completed">Completed</SelectItem>
              <SelectItem value="paused">Paused</SelectItem>
              <SelectItem value="abandoned">Abandoned</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </Card>

      {filteredGoals.length === 0 ? (
        <Card className="p-8">
          <div className="flex flex-col items-center justify-center space-y-4 text-center">
            <Target className="h-12 w-12 text-muted-foreground" />
            <div>
              <h3 className="font-semibold">No goals found</h3>
              <p className="text-sm text-muted-foreground">
                Create a goal to start measuring blockers and recommendations against a real target.
              </p>
            </div>
          </div>
        </Card>
      ) : (
        <div className="space-y-3">
          {filteredGoals.map((goal) => {
            const metricDefinition = getMetricDefinition(metricDefinitions, goal.metric_key);
            const progress = calculateGoalProgress(goal, metricDefinition);
            const currentValue = goal.current_value ?? goal.baseline_value;

            return (
              <button
                key={goal.goal_id}
                className="w-full rounded-xl border bg-card p-5 text-left transition-shadow hover:shadow-md"
                onClick={() => navigate(`/kpi-goals/${goal.goal_id}`)}
                type="button"
              >
                <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-lg font-semibold">{goal.name}</h3>
                      <Badge variant={goalStatusTone(goal.status)}>{goal.status.replace(/_/g, ' ')}</Badge>
                    </div>
                    <div className="text-sm text-muted-foreground">
                      {getMetricDisplayLabel(metricDefinition, goal.metric_key)}
                    </div>
                    <div className="flex flex-wrap items-center gap-4 text-sm">
                      <span>
                        Baseline {formatMetricValue(goal.baseline_value, metricDefinition)}
                      </span>
                      <span>
                        Current {formatMetricValue(currentValue, metricDefinition)}
                      </span>
                      <span>
                        Target {formatMetricValue(goal.target_value, metricDefinition)}
                      </span>
                    </div>
                  </div>
                  <div className="grid shrink-0 grid-cols-3 gap-4 text-sm lg:min-w-[300px]">
                    <div>
                      <div className="text-muted-foreground">Attainment</div>
                      <div className="mt-1 text-xl font-semibold">{progress.toFixed(0)}%</div>
                    </div>
                    <div>
                      <div className="text-muted-foreground">Insights</div>
                      <div className="mt-1 text-xl font-semibold">{goal.open_insight_count}</div>
                    </div>
                    <div>
                      <div className="text-muted-foreground">Recommendations</div>
                      <div className="mt-1 text-xl font-semibold">{goal.pending_recommendation_count}</div>
                    </div>
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
