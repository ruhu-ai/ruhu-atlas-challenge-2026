import { apiClient } from '../client';

export type MetricDirection = 'higher_is_better' | 'lower_is_better';
export type MetricUnit = 'percent' | 'score_100' | 'seconds' | 'usd';
export type ScopeKind =
  | 'organization'
  | 'agent'
  | 'workflow'
  | 'channel'
  | 'segment'
  | 'campaign'
  | 'custom';
export type RuntimeChannel = 'phone' | 'whatsapp' | 'web_chat' | 'web_widget' | 'browser';
export type GoalStatus =
  | 'draft'
  | 'active'
  | 'on_track'
  | 'at_risk'
  | 'stalled'
  | 'completed'
  | 'paused'
  | 'abandoned';
export type InsightStatus = 'open' | 'accepted' | 'dismissed' | 'superseded';
export type RecommendationStatus =
  | 'draft'
  | 'ready_for_review'
  | 'approved'
  | 'rejected'
  | 'execution_requested'
  | 'executed'
  | 'execution_failed'
  | 'superseded';

export interface MetricDefinition {
  metric_key: string;
  version: number;
  label: string;
  description: string;
  canonical_unit: MetricUnit;
  display_unit: string;
  value_kind: string;
  direction: MetricDirection;
  min_value: number | null;
  max_value: number | null;
  default_lookback_days: number;
  minimum_sample_size: number;
  baseline_strategy: string;
  eligibility_rule: string | null;
  calculation_notes: string | null;
  calculation_variant: string | null;
  requires_outcome_taxonomy: boolean;
  auto_measurable: boolean;
  measurement_sources: string[];
  contained_outcomes: string[];
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface MetricScope {
  scope_id: string;
  organization_id: string;
  scope_kind: ScopeKind;
  agent_id: string | null;
  workflow_id: string | null;
  channel: RuntimeChannel | null;
  segment_key: string | null;
  campaign_key: string | null;
  custom_scope: Record<string, unknown>;
  display_name: string | null;
  fingerprint: string;
  created_at: string;
}

export interface MetricObservation {
  observation_id: string;
  organization_id: string;
  metric_key: string;
  metric_definition_version: number;
  scope_id: string;
  observation_kind: string;
  value: number;
  sample_size: number;
  confidence: number;
  eligibility_count: number | null;
  excluded_count: number | null;
  period_start: string;
  period_end: string;
  lookback_days: number | null;
  quality_flags: string[];
  source_summary: Record<string, unknown>;
  calculation_version: string;
  created_at: string;
}

export interface BaselineSnapshot {
  baseline_snapshot_id: string;
  organization_id: string;
  goal_id: string | null;
  metric_key: string;
  scope_id: string;
  source_observation_id: string | null;
  value: number;
  sample_size: number;
  confidence: number;
  period_start: string;
  period_end: string;
  baseline_source: 'measured' | 'manual_override';
  baseline_reason: string | null;
  provenance: Record<string, unknown>;
  created_at: string;
}

export interface Goal {
  goal_id: string;
  organization_id: string;
  metric_key: string;
  scope_id: string;
  name: string;
  description: string | null;
  baseline_snapshot_id: string;
  target_value: number;
  status: GoalStatus;
  start_at: string;
  target_at: string;
  owner_user_id: string | null;
  metadata: Record<string, unknown>;
  latest_evaluation_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface GoalEvaluation {
  evaluation_id: string;
  organization_id: string;
  goal_id: string;
  observation_id: string;
  status: GoalStatus;
  progress_ratio: number;
  distance_to_target: number;
  delta_from_baseline: number;
  sample_size_sufficient: boolean;
  freshness_seconds: number | null;
  notes: string | null;
  created_at: string;
}

export interface InsightItem {
  insight_id: string;
  organization_id: string;
  goal_id: string | null;
  scope_id: string;
  metric_key: string;
  blocker_kind: string;
  title: string;
  summary: string;
  severity: number;
  occurrence_count: number;
  rank_score: number;
  evidence_bundle: Record<string, unknown>;
  status: InsightStatus;
  stale_after: string | null;
  created_at: string;
  updated_at: string;
}

export interface RecommendationCandidate {
  recommendation_id: string;
  organization_id: string;
  goal_id: string | null;
  scope_id: string;
  metric_key: string;
  insight_id: string | null;
  category: string;
  title: string;
  summary: string;
  rationale: string;
  projected_impact_min: number;
  projected_impact_max: number;
  projected_confidence: number;
  evidence_bundle: Record<string, unknown>;
  dependency_ids: string[];
  execution_template: Record<string, unknown> | null;
  status: RecommendationStatus;
  created_at: string;
  updated_at: string;
}

export interface ExecutionIntent {
  execution_intent_id: string;
  organization_id: string;
  recommendation_id: string;
  goal_id: string | null;
  adapter_kind: string;
  action_type: string;
  execution_mode: string;
  requested_by: string | null;
  requested_via: string;
  approved_payload: Record<string, unknown>;
  validation_snapshot: Record<string, unknown>;
  safety_level: string;
  reversibility: string;
  created_at: string;
}

export interface ExecutionResult {
  execution_result_id: string;
  organization_id: string;
  execution_intent_id: string;
  status: string;
  changed_object_refs: Array<Record<string, unknown>>;
  before_state_summary: Record<string, unknown>;
  after_state_summary: Record<string, unknown>;
  diff_artifact_ref: string | null;
  adapter_diagnostics: Record<string, unknown>;
  rollback_handle: Record<string, unknown> | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
}

export interface KPIExperiment {
  experiment_id: string;
  organization_id: string;
  goal_id: string | null;
  recommendation_id: string | null;
  name: string;
  hypothesis: string;
  status: string;
  primary_metric_key: string;
  scope_id: string;
  notes: string | null;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ImpactAssessment {
  assessment_id: string;
  organization_id: string;
  goal_id: string | null;
  recommendation_id: string | null;
  execution_intent_id: string | null;
  experiment_id: string | null;
  metric_key: string;
  scope_id: string;
  baseline_observation_id: string;
  comparison_observation_id: string;
  attribution_mode: string;
  attribution_confidence: string;
  observed_change: number;
  attributed_change: number | null;
  projected_impact_min: number | null;
  projected_impact_max: number | null;
  attainment_fraction: number | null;
  competing_changes: string[];
  notes: string | null;
  created_at: string;
}

export interface GoalSummaryReadModel {
  goal_id: string;
  name: string;
  metric_key: string;
  scope_id: string;
  status: GoalStatus;
  target_value: number;
  baseline_value: number;
  current_value: number | null;
  progress_ratio: number | null;
  latest_observation_at: string | null;
  latest_evaluation_at: string | null;
  open_insight_count: number;
  pending_recommendation_count: number;
}

export interface GoalDetailReadModel {
  goal: Goal;
  scope: MetricScope;
  baseline_snapshot: BaselineSnapshot;
  latest_observation: MetricObservation | null;
  latest_evaluation: GoalEvaluation | null;
  insights: InsightItem[];
  recommendations: RecommendationCandidate[];
  execution_intents: ExecutionIntent[];
  execution_results: ExecutionResult[];
  experiments: KPIExperiment[];
  impact_assessments: ImpactAssessment[];
}

export interface KPIScopeCreate {
  organization_id?: string;
  scope_kind: ScopeKind;
  agent_id?: string;
  workflow_id?: string;
  channel?: RuntimeChannel;
  segment_key?: string;
  campaign_key?: string;
  custom_scope?: Record<string, unknown>;
  display_name?: string;
}

export interface KPIBaselineCreate {
  organization_id?: string;
  metric_key: string;
  scope_id: string;
  goal_id?: string;
  observation_id?: string;
  manual_value?: number;
  manual_sample_size?: number;
  manual_confidence?: number;
  period_start?: string;
  period_end?: string;
  reason?: string;
  provenance?: Record<string, unknown>;
}

export interface KPIGoalCreate {
  organization_id?: string;
  metric_key: string;
  scope_id: string;
  name: string;
  target_value: number;
  target_at: string;
  description?: string;
  owner_user_id?: string;
  baseline_snapshot_id: string;
  start_at?: string;
  metadata?: Record<string, unknown>;
}

class KPIGoalService {
  async getMetricDefinitions(): Promise<MetricDefinition[]> {
    return apiClient.get<MetricDefinition[]>('/kpi/definitions');
  }

  async listScopes(params?: {
    organization_id?: string;
    scope_kind?: ScopeKind;
  }): Promise<MetricScope[]> {
    return apiClient.get<MetricScope[]>('/kpi/scopes', { params });
  }

  async createScope(payload: KPIScopeCreate): Promise<MetricScope> {
    return apiClient.post<MetricScope>('/kpi/scopes', payload);
  }

  async ensureAgentScope(agentId: string, displayName?: string): Promise<MetricScope> {
    return this.createScope({
      scope_kind: 'agent',
      agent_id: agentId,
      display_name: displayName,
    });
  }

  async refreshObservation(
    scopeId: string,
    metricKey: string,
    payload?: { organization_id?: string; lookback_days?: number; period_end?: string }
  ): Promise<MetricObservation> {
    return apiClient.post<MetricObservation>(
      `/kpi/scopes/${scopeId}/measurements/${metricKey}/refresh`,
      payload ?? {}
    );
  }

  async createBaseline(payload: KPIBaselineCreate): Promise<BaselineSnapshot> {
    return apiClient.post<BaselineSnapshot>('/kpi/baselines', payload);
  }

  async listGoals(params?: {
    organization_id?: string;
    scope_id?: string;
    status?: string;
  }): Promise<GoalSummaryReadModel[]> {
    return apiClient.get<GoalSummaryReadModel[]>('/kpi/goals', { params });
  }

  async createGoal(payload: KPIGoalCreate): Promise<Goal> {
    return apiClient.post<Goal>('/kpi/goals', payload);
  }

  async getGoal(goalId: string): Promise<GoalDetailReadModel> {
    return apiClient.get<GoalDetailReadModel>(`/kpi/goals/${goalId}`);
  }

  async listGoalEvaluations(goalId: string, limit = 100): Promise<GoalEvaluation[]> {
    return apiClient.get<GoalEvaluation[]>(`/kpi/goals/${goalId}/evaluations`, {
      params: { limit },
    });
  }

  async evaluateGoal(
    goalId: string,
    payload?: { organization_id?: string; observation_id?: string }
  ): Promise<GoalEvaluation> {
    return apiClient.post<GoalEvaluation>(`/kpi/goals/${goalId}/evaluate`, payload ?? {});
  }

  async listGoalInsights(
    goalId: string,
    params?: { organization_id?: string; status?: InsightStatus; limit?: number }
  ): Promise<InsightItem[]> {
    return apiClient.get<InsightItem[]>(`/kpi/goals/${goalId}/insights`, { params });
  }

  async generateGoalInsights(goalId: string, organization_id?: string): Promise<InsightItem[]> {
    const params = organization_id ? { organization_id } : undefined;
    return apiClient.post<InsightItem[]>(`/kpi/goals/${goalId}/insights/generate`, params ?? {});
  }

  async listGoalRecommendations(
    goalId: string,
    params?: { organization_id?: string; status?: RecommendationStatus; limit?: number }
  ): Promise<RecommendationCandidate[]> {
    return apiClient.get<RecommendationCandidate[]>(`/kpi/goals/${goalId}/recommendations`, {
      params,
    });
  }

  async generateGoalRecommendations(
    goalId: string,
    payload?: { organization_id?: string; insight_ids?: string[] }
  ): Promise<RecommendationCandidate[]> {
    return apiClient.post<RecommendationCandidate[]>(
      `/kpi/goals/${goalId}/recommendations/generate`,
      payload ?? {}
    );
  }

  async updateGoalStatus(
    goalId: string,
    status: 'active' | 'paused' | 'completed' | 'abandoned'
  ): Promise<Goal> {
    return apiClient.post<Goal>(`/kpi/goals/${goalId}/status`, { status });
  }

  async updateInsightStatus(insightId: string, status: InsightStatus): Promise<InsightItem> {
    return apiClient.post<InsightItem>(`/kpi/insights/${insightId}/status`, { status });
  }

  async updateRecommendationStatus(
    recommendationId: string,
    status: RecommendationStatus
  ): Promise<RecommendationCandidate> {
    return apiClient.post<RecommendationCandidate>(
      `/kpi/recommendations/${recommendationId}/status`,
      { status }
    );
  }
}

export const kpiGoalService = new KPIGoalService();
