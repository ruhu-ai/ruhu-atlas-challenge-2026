import { useQuery, type UseQueryResult } from '@tanstack/react-query';

import {
  kpiGoalService,
  type GoalSummaryReadModel,
  type MetricDefinition,
} from '@/api/services/kpi-goals.service';

export type { MetricDefinition as KPIMetricDefinition } from '@/api/services/kpi-goals.service';

export function useKpiMetricDefinitions(): UseQueryResult<MetricDefinition[]> {
  return useQuery<MetricDefinition[]>({
    queryKey: ['kpi', 'definitions'],
    queryFn: () => kpiGoalService.getMetricDefinitions(),
    staleTime: 5 * 60_000,
  });
}

export function getMetricDefinition(
  definitions: readonly MetricDefinition[] | undefined,
  metricKey: string | undefined,
): MetricDefinition | undefined {
  if (!definitions || !metricKey) {
    return undefined;
  }

  return definitions.find((definition) => definition.metric_key === metricKey);
}

function formatCanonicalValue(value: number, definition?: MetricDefinition): string {
  const unit = definition?.canonical_unit ?? definition?.display_unit;

  switch (unit) {
    case 'percent':
      return `${value.toFixed(1)}%`;
    case 'usd':
      return `$${value.toFixed(2)}`;
    case 'seconds':
      return `${value.toFixed(1)} sec`;
    case 'score_100':
      return `${value.toFixed(1)} pts`;
    case undefined:
      return value.toFixed(1);
    default:
      return `${value.toFixed(1)} ${definition?.display_unit ?? unit}`.trim();
  }
}

export function formatMetricValue(value: number, definition?: MetricDefinition): string {
  return formatCanonicalValue(value, definition);
}

export function formatMetricDelta(value: number, definition?: MetricDefinition): string {
  const sign = value > 0 ? '+' : value < 0 ? '-' : '';
  return `${sign}${formatCanonicalValue(Math.abs(value), definition)}`;
}

export function isLowerBetter(definition?: MetricDefinition): boolean {
  return definition?.direction === 'lower_is_better';
}

export function isImprovementDelta(value: number, definition?: MetricDefinition): boolean {
  return isLowerBetter(definition) ? value < 0 : value > 0;
}

export function getMetricDisplayLabel(
  definition?: MetricDefinition,
  metricKey?: string,
): string {
  return (
    definition?.label ||
    (metricKey || '').replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
  );
}

export function getNumericDomainBounds(
  values: readonly number[],
  definition?: MetricDefinition,
): [number, number] {
  const safeValues = values.filter((value) => Number.isFinite(value));

  if (safeValues.length === 0) {
    return [0, 100];
  }

  const minValue = Math.min(...safeValues);
  const maxValue = Math.max(...safeValues);
  const range = maxValue - minValue;
  const padding = range > 0 ? range * 0.15 : maxValue > 0 ? maxValue * 0.15 : 1;

  const computedMin = Math.max(0, minValue - padding);
  const computedMax = maxValue + padding;

  if (definition?.canonical_unit === 'percent') {
    return [definition.min_value ?? 0, definition.max_value ?? 100];
  }

  return [definition?.min_value ?? computedMin, definition?.max_value ?? computedMax];
}

export function normalizeProgressRatio(value: number | null | undefined): number {
  if (value == null || !Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, Math.min(value * 100, 100));
}

export function calculateGoalProgress(
  goal: Pick<GoalSummaryReadModel, 'baseline_value' | 'target_value' | 'current_value' | 'progress_ratio'>,
  definition?: MetricDefinition,
): number {
  if (goal.progress_ratio != null && Number.isFinite(goal.progress_ratio)) {
    return normalizeProgressRatio(goal.progress_ratio);
  }

  if (goal.current_value == null || !Number.isFinite(goal.current_value)) {
    return 0;
  }

  const baseline = goal.baseline_value;
  const target = goal.target_value;
  const current = goal.current_value;
  const denominator = Math.abs(target - baseline);

  if (denominator === 0) {
    return current === target ? 100 : 0;
  }

  const rawRatio = isLowerBetter(definition)
    ? (baseline - current) / (baseline - target)
    : (current - baseline) / (target - baseline);

  return normalizeProgressRatio(rawRatio);
}

export function goalStatusTone(status: string): 'default' | 'success' | 'warning' | 'destructive' | 'secondary' {
  switch (status) {
    case 'completed':
    case 'on_track':
      return 'success';
    case 'at_risk':
    case 'stalled':
      return 'warning';
    case 'abandoned':
      return 'destructive';
    case 'active':
      return 'default';
    default:
      return 'secondary';
  }
}
