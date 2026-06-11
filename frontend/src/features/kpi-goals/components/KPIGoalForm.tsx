import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useForm } from 'react-hook-form';
import { Loader2 } from 'lucide-react';

import { agentService } from '@/api/services/agent.service';
import { kpiGoalService } from '@/api/services/kpi-goals.service';
import { Badge } from '@/components/atoms/badge';
import { Button } from '@/components/atoms/button';
import { Card } from '@/components/atoms/card';
import { Input } from '@/components/atoms/input';
import { Label } from '@/components/atoms/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select';
import { Switch } from '@/components/atoms/switch';
import { Textarea } from '@/components/atoms/textarea';
import { formatDate } from '@/lib/utils';
import type { Agent } from '@/types';
import {
  formatMetricValue,
  useKpiMetricDefinitions,
} from '@/features/kpi-goals/utils/metric-display';

interface FormData {
  name: string;
  description: string;
  metric_key: string;
  target_value: number;
  target_at: string;
  agent_id: string;
  lookback_days: number;
  manual_baseline_value?: number;
}

export interface KPIGoalFormProps {
  agentId?: string;
  onSuccess?: () => void;
}

const LOOKBACK_OPTIONS = [
  { label: 'Last 7 days', value: 7 },
  { label: 'Last 14 days', value: 14 },
  { label: 'Last 30 days', value: 30 },
  { label: 'Last 60 days', value: 60 },
  { label: 'Last 90 days', value: 90 },
];

export function KPIGoalForm({ agentId, onSuccess }: KPIGoalFormProps) {
  const [useManualBaseline, setUseManualBaseline] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const { data: metricDefinitions = [], isLoading: definitionsLoading } = useKpiMetricDefinitions();
  const {
    data: agents = [],
    isLoading: agentsLoading,
  } = useQuery({
    queryKey: ['agents'],
    queryFn: () => agentService.getAllAgents(),
    staleTime: 60_000,
  });

  const {
    register,
    watch,
    handleSubmit,
    setValue,
    formState: { errors },
  } = useForm<FormData>({
    defaultValues: {
      name: '',
      description: '',
      metric_key: 'deflection_rate',
      target_at: new Date(Date.now() + 90 * 24 * 60 * 60 * 1000).toISOString().split('T')[0],
      agent_id: agentId ?? '',
      lookback_days: 30,
    },
  });

  const selectedMetricKey = watch('metric_key');
  const selectedAgentId = watch('agent_id');
  const selectedLookbackDays = Number(watch('lookback_days') ?? 30);
  const manualBaselineValue = watch('manual_baseline_value');

  const availableAgents = useMemo(() => {
    return [...agents]
      .filter((candidate: Agent) => candidate.status !== 'archived')
      .sort((left, right) => left.name.localeCompare(right.name));
  }, [agents]);

  const selectedAgent = availableAgents.find((candidate) => candidate.id === selectedAgentId);
  const selectedMetricDefinition = metricDefinitions.find(
    (definition) => definition.metric_key === selectedMetricKey
  );

  const metricNotAutoMeasurable = selectedMetricDefinition != null && !selectedMetricDefinition.auto_measurable;

  const baselineMeasurementQuery = useQuery({
    queryKey: ['kpi', 'baseline-measurement', selectedAgentId, selectedMetricKey, selectedLookbackDays],
    queryFn: async () => {
      if (!selectedAgentId || !selectedMetricKey) {
        return null;
      }

      const scope = await kpiGoalService.ensureAgentScope(selectedAgentId, selectedAgent?.name);
      const observation = await kpiGoalService.refreshObservation(scope.scope_id, selectedMetricKey, {
        lookback_days: selectedLookbackDays,
      });

      return { scope, observation };
    },
    enabled:
      Boolean(selectedAgentId) &&
      Boolean(selectedMetricKey) &&
      selectedLookbackDays > 0 &&
      !useManualBaseline &&
      !metricNotAutoMeasurable,
    retry: (failureCount, error) => {
      if (error instanceof Error && error.message.includes('cannot be measured yet')) return false;
      return failureCount < 2;
    },
  });

  const measuredObservation = baselineMeasurementQuery.data?.observation ?? null;
  const measuredScope = baselineMeasurementQuery.data?.scope ?? null;
  const measuredBaselineIsReliable = measuredObservation
    ? measuredObservation.sample_size >= (selectedMetricDefinition?.minimum_sample_size ?? 0)
    : false;
  const effectiveBaselineValue = useManualBaseline
    ? manualBaselineValue
    : measuredObservation?.value;

  const onSubmit = async (data: FormData) => {
    try {
      setIsSubmitting(true);
      setSubmitError(null);

      if (!selectedMetricDefinition) {
        setSubmitError('Metric definition is not available yet.');
        return;
      }

      if (!data.agent_id) {
        setSubmitError('Select an agent before creating a goal.');
        return;
      }

      const scope =
        measuredScope ??
        (await kpiGoalService.ensureAgentScope(data.agent_id, selectedAgent?.name));

      let baselineSnapshotId: string;

      if (useManualBaseline) {
        if (effectiveBaselineValue == null || Number.isNaN(effectiveBaselineValue)) {
          setSubmitError('Enter a valid manual baseline value.');
          return;
        }

        const baseline = await kpiGoalService.createBaseline({
          metric_key: data.metric_key,
          scope_id: scope.scope_id,
          manual_value: effectiveBaselineValue,
          manual_sample_size: 0,
          manual_confidence: 1,
          reason: 'manual baseline entered during goal creation',
        });
        baselineSnapshotId = baseline.baseline_snapshot_id;
      } else {
        if (!measuredObservation) {
          setSubmitError('Measured baseline is not ready yet.');
          return;
        }

        if (!measuredBaselineIsReliable) {
          setSubmitError(
            `Measured baseline requires at least ${selectedMetricDefinition.minimum_sample_size} samples for ${selectedMetricDefinition.label}.`
          );
          return;
        }

        const baseline = await kpiGoalService.createBaseline({
          metric_key: data.metric_key,
          scope_id: scope.scope_id,
          observation_id: measuredObservation.observation_id,
          provenance: {
            source: 'goal_creation_form',
            lookback_days: selectedLookbackDays,
          },
        });
        baselineSnapshotId = baseline.baseline_snapshot_id;
      }

      await kpiGoalService.createGoal({
        metric_key: data.metric_key,
        scope_id: scope.scope_id,
        name: data.name.trim(),
        description: data.description.trim() || undefined,
        target_value: Number(data.target_value),
        target_at: new Date(data.target_at).toISOString(),
        baseline_snapshot_id: baselineSnapshotId,
      });

      onSuccess?.();
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to create goal.';
      setSubmitError(message);
    } finally {
      setIsSubmitting(false);
    }
  };

  const targetValidationMessage = (() => {
    if (effectiveBaselineValue == null || !selectedMetricDefinition) {
      return null;
    }

    return selectedMetricDefinition.direction === 'lower_is_better'
      ? `Target must be lower than ${formatMetricValue(effectiveBaselineValue, selectedMetricDefinition)}`
      : `Target must be higher than ${formatMetricValue(effectiveBaselineValue, selectedMetricDefinition)}`;
  })();

  return (
    <Card className="p-6">
      <form className="space-y-6" onSubmit={handleSubmit(onSubmit)}>
        <div>
          <h2 className="text-2xl font-bold">Create KPI Goal</h2>
          <p className="text-muted-foreground">
            Create the goal from a measured scope and baseline. The backend owns the metric definition and goal semantics.
          </p>
        </div>

        <div className="space-y-2">
          <Label htmlFor="name">Goal Name</Label>
          <Input
            id="name"
            {...register('name', { required: 'Goal name is required' })}
            placeholder="Increase resolution rate for support agent"
          />
          {errors.name && <p className="text-sm text-destructive">{errors.name.message}</p>}
        </div>

        <div className="space-y-2">
          <Label htmlFor="description">Description</Label>
          <Textarea
            id="description"
            rows={3}
            {...register('description')}
            placeholder="Short operational context for this target."
          />
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label>Agent</Label>
            <Select
              disabled={agentsLoading || Boolean(agentId)}
              value={selectedAgentId || undefined}
              onValueChange={(value) => setValue('agent_id', value, { shouldValidate: true, shouldDirty: true })}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select an agent" />
              </SelectTrigger>
              <SelectContent>
                {availableAgents.map((agent) => (
                  <SelectItem key={agent.id} value={agent.id}>
                    {agent.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label>Metric</Label>
            <Select
              disabled={definitionsLoading}
              value={selectedMetricKey}
              onValueChange={(value) => setValue('metric_key', value, { shouldValidate: true, shouldDirty: true })}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select a metric" />
              </SelectTrigger>
              <SelectContent>
                {metricDefinitions.map((definition) => (
                  <SelectItem key={definition.metric_key} value={definition.metric_key}>
                    {definition.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {selectedMetricDefinition && (
              <div className="flex flex-wrap gap-2">
                <Badge variant="outline">{selectedMetricDefinition.display_unit}</Badge>
                <Badge variant={selectedMetricDefinition.direction === 'lower_is_better' ? 'warning' : 'success'}>
                  {selectedMetricDefinition.direction === 'lower_is_better' ? 'Lower is better' : 'Higher is better'}
                </Badge>
              </div>
            )}
          </div>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label>Baseline Window</Label>
            <Select
              value={String(selectedLookbackDays)}
              onValueChange={(value) =>
                setValue('lookback_days', Number(value), { shouldValidate: true, shouldDirty: true })
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {LOOKBACK_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={String(option.value)}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="target_at">Target Date</Label>
            <Input
              id="target_at"
              type="date"
              {...register('target_at', { required: 'Target date is required' })}
            />
            {errors.target_at && <p className="text-sm text-destructive">{errors.target_at.message}</p>}
          </div>
        </div>

        <div className="space-y-4 rounded-xl border border-border bg-muted/20 p-4">
          <div className="flex items-center justify-between gap-4">
            <div>
              <h3 className="font-semibold">Baseline</h3>
              <p className="text-sm text-muted-foreground">
                Use measured observation data by default. Manual entry is explicit.
              </p>
            </div>
            <div className="flex items-center gap-3">
              <Label htmlFor="manual-baseline">Manual baseline</Label>
              <Switch id="manual-baseline" checked={useManualBaseline} onCheckedChange={setUseManualBaseline} />
            </div>
          </div>

          {useManualBaseline ? (
            <div className="space-y-2">
              <Label htmlFor="manual_baseline_value">Manual Baseline Value</Label>
              <Input
                id="manual_baseline_value"
                type="number"
                step={selectedMetricDefinition?.canonical_unit === 'usd' ? '0.01' : '0.1'}
                {...register('manual_baseline_value', { valueAsNumber: true })}
              />
            </div>
          ) : (
            <div className="rounded-lg border bg-background p-4">
              {!selectedAgentId ? (
                <p className="text-sm text-muted-foreground">Select an agent to measure the baseline.</p>
              ) : baselineMeasurementQuery.isLoading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Measuring baseline from runtime data
                </div>
              ) : baselineMeasurementQuery.error ? (
                metricNotAutoMeasurable ? (
                  <div className="space-y-2">
                    <p className="text-sm text-muted-foreground">
                      This metric requires external data (e.g. surveys) and cannot be measured automatically. Enable <strong>Manual baseline</strong> to enter a value.
                    </p>
                  </div>
                ) : (
                  <p className="text-sm text-destructive">Failed to measure a baseline for this agent and metric.</p>
                )
              ) : measuredObservation ? (
                <div className="space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="text-3xl font-semibold">
                      {formatMetricValue(measuredObservation.value, selectedMetricDefinition)}
                    </div>
                    <Badge variant={measuredBaselineIsReliable ? 'success' : 'warning'}>
                      {measuredBaselineIsReliable ? 'Reliable sample' : 'Low sample'}
                    </Badge>
                    <Badge variant="outline">{measuredObservation.sample_size} samples</Badge>
                    <Badge variant="info">{Math.round(measuredObservation.confidence * 100)}% confidence</Badge>
                  </div>
                  <p className="text-sm text-muted-foreground">
                    Observed from {formatDate(measuredObservation.period_start)} to {formatDate(measuredObservation.period_end)}
                  </p>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">No measured baseline is available yet.</p>
              )}
            </div>
          )}
        </div>

        <div className="space-y-2">
          <Label htmlFor="target_value">Target Value</Label>
          <Input
            id="target_value"
            type="number"
            step={selectedMetricDefinition?.canonical_unit === 'usd' ? '0.01' : '0.1'}
            {...register('target_value', {
              required: 'Target value is required',
              valueAsNumber: true,
              validate: (value) => {
                if (!selectedMetricDefinition || effectiveBaselineValue == null || Number.isNaN(value)) {
                  return true;
                }

                if (
                  selectedMetricDefinition.direction === 'lower_is_better' &&
                  value >= effectiveBaselineValue
                ) {
                  return `Target must be lower than ${formatMetricValue(effectiveBaselineValue, selectedMetricDefinition)}`;
                }

                if (
                  selectedMetricDefinition.direction === 'higher_is_better' &&
                  value <= effectiveBaselineValue
                ) {
                  return `Target must be higher than ${formatMetricValue(effectiveBaselineValue, selectedMetricDefinition)}`;
                }

                return true;
              },
            })}
          />
          {errors.target_value && <p className="text-sm text-destructive">{errors.target_value.message}</p>}
          {!errors.target_value && targetValidationMessage && (
            <p className="text-xs text-muted-foreground">{targetValidationMessage}</p>
          )}
        </div>

        {submitError && (
          <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
            {submitError}
          </div>
        )}

        <Button
          className="w-full"
          disabled={
            isSubmitting ||
            definitionsLoading ||
            agentsLoading ||
            !selectedAgentId ||
            (!useManualBaseline && baselineMeasurementQuery.isLoading) ||
            (!useManualBaseline && metricNotAutoMeasurable)
          }
          type="submit"
        >
          {isSubmitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          Create Goal
        </Button>
      </form>
    </Card>
  );
}
