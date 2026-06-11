export type JourneyDefinitionStatus = 'active' | 'archived';
export type JourneyVersionStatus = 'draft' | 'published';
export type JourneyStatus = 'open' | 'completed' | 'abandoned' | 'transferred' | 'failed';
export type JourneyEventSource = 'runtime_rule' | 'manual' | 'import' | 'replay';
export type JourneyEventType =
  | 'journey_opened'
  | 'touchpoint_attached'
  | 'milestone_entered'
  | 'milestone_completed'
  | 'outcome_recorded'
  | 'journey_closed'
  | 'journey_reopened'
  | 'manual_annotation'
  | 'manual_override';
export type JourneyPredicateKind =
  | 'conversation_started'
  | 'state_entered'
  | 'terminal_disposition'
  | 'fact_present'
  | 'fact_equals'
  | 'tool_succeeded'
  | 'tool_failed'
  | 'semantic_event'
  | 'realtime_event'
  | 'summary_primary_intent'
  | 'summary_tag'
  | 'summary_outcome'
  | 'summary_resolution_status';
export type JourneyRuntimeJobKind =
  | 'definition_rebuild'
  | 'definition_replay'
  | 'journey_replay'
  | 'analytics_rebuild'
  | 'abandonment_sweep';
export type JourneyRuntimeJobStatus = 'queued' | 'running' | 'completed' | 'failed';
export type JourneyReviewSeverity = 'error' | 'warning';
export type JourneyExecutionMode = 'sync' | 'async';

export interface JourneyScope {
  agent_ids: string[];
  channel_filters: string[];
  conversation_mode_filters: string[];
}

export interface SubjectKeyStrategy {
  kind: 'metadata_path' | 'fact_name' | 'channel_identity' | 'external_ref';
  value: string;
  fallback_kind?: 'metadata_path' | 'fact_name' | 'channel_identity' | 'external_ref' | null;
  fallback_value?: string | null;
}

export interface JourneyRulePredicate {
  kind: JourneyPredicateKind;
  value?: string | null;
  metadata: Record<string, unknown>;
}

export interface JourneyMilestoneRule {
  milestone_id: string;
  name: string;
  description?: string | null;
  order_index: number;
  required: boolean;
  enter_when: JourneyRulePredicate[];
  complete_when: JourneyRulePredicate[];
  success_labels: string[];
  failure_labels: string[];
}

export interface JourneyAbandonmentPolicy {
  inactive_after_seconds?: number | null;
  close_as: 'abandoned' | 'failed' | 'transferred';
}

export interface JourneyMergePolicy {
  reopen_closed_within_seconds?: number | null;
  reopen_statuses: Array<'abandoned' | 'failed' | 'transferred'>;
}

export interface JourneyDefinitionRules {
  entry_rules: JourneyRulePredicate[];
  touchpoint_rules: JourneyRulePredicate[];
  milestones: JourneyMilestoneRule[];
  outcome_rules: Record<string, JourneyRulePredicate[]>;
  abandonment_policy: JourneyAbandonmentPolicy;
  merge_policy: JourneyMergePolicy;
}

export interface JourneyDefinition {
  definition_id: string;
  organization_id?: string | null;
  slug: string;
  name: string;
  description?: string | null;
  subject_strategy: SubjectKeyStrategy;
  scope: JourneyScope;
  status: JourneyDefinitionStatus;
  tags: string[];
  settings: Record<string, unknown>;
  current_draft_version_id?: string | null;
  current_published_version_id?: string | null;
  created_by_user_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface JourneyDefinitionVersion {
  definition_version_id: string;
  organization_id?: string | null;
  definition_id: string;
  version_number: number;
  status: JourneyVersionStatus;
  based_on_version_id?: string | null;
  rules: JourneyDefinitionRules;
  compiled_rules: Record<string, unknown>;
  review_summary: Record<string, unknown>;
  created_by_user_id?: string | null;
  created_at: string;
  updated_at: string;
  published_at?: string | null;
}

export interface JourneyDefinitionSummary {
  definition_id: string;
  organization_id?: string | null;
  slug: string;
  name: string;
  description?: string | null;
  status: JourneyDefinitionStatus;
  current_draft_version_id?: string | null;
  current_published_version_id?: string | null;
  updated_at: string;
}

export interface JourneyDefinitionListResponse {
  definitions: JourneyDefinitionSummary[];
}

export interface JourneyDefinitionVersionListResponse {
  versions: JourneyDefinitionVersion[];
}

export interface JourneyReviewItem {
  severity: JourneyReviewSeverity;
  code: string;
  message: string;
}

export interface JourneyDefinitionReview {
  definition_id: string;
  definition_version_id: string;
  can_publish: boolean;
  blockers: JourneyReviewItem[];
  warnings: JourneyReviewItem[];
  validated_at: string;
}

export interface JourneyPublishReadiness {
  definition_id: string;
  draft_version_id?: string | null;
  published_version_id?: string | null;
  can_publish: boolean;
  blockers: JourneyReviewItem[];
  warnings: JourneyReviewItem[];
  draft_review?: JourneyDefinitionReview | null;
  validated_at: string;
}

export interface JourneyPublishReadinessResponse {
  definition: JourneyDefinition;
  draft_version?: JourneyDefinitionVersion | null;
  published_version?: JourneyDefinitionVersion | null;
  readiness: JourneyPublishReadiness;
}

export interface JourneyInstance {
  journey_id: string;
  organization_id: string;
  definition_id: string;
  definition_version_id: string;
  subject_key: string;
  subject_summary: Record<string, unknown>;
  status: JourneyStatus;
  outcome?: string | null;
  current_milestone_id?: string | null;
  current_milestone_order?: number | null;
  milestone_path: string[];
  first_conversation_id?: string | null;
  latest_conversation_id?: string | null;
  first_agent_id?: string | null;
  first_agent_version_id?: string | null;
  latest_agent_id?: string | null;
  latest_agent_version_id?: string | null;
  started_at: string;
  last_activity_at: string;
  ended_at?: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface JourneyTouchpoint {
  touchpoint_id: string;
  organization_id: string;
  journey_id: string;
  conversation_id: string;
  agent_id?: string | null;
  agent_version_id?: string | null;
  channel?: string | null;
  mode?: string | null;
  entry_reason?: string | null;
  metadata: Record<string, unknown>;
  started_at: string;
  ended_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface JourneyEvent {
  journey_event_id: string;
  organization_id: string;
  journey_id: string;
  touchpoint_id?: string | null;
  conversation_id?: string | null;
  turn_trace_id?: string | null;
  realtime_event_id?: string | null;
  tool_invocation_id?: string | null;
  event_type: JourneyEventType;
  milestone_id?: string | null;
  source: JourneyEventSource;
  idempotency_key: string;
  payload: Record<string, unknown>;
  occurred_at: string;
  created_at: string;
}

export interface JourneyInstanceSummary {
  journey_id: string;
  definition_id: string;
  definition_version_id: string;
  subject_key: string;
  status: JourneyStatus;
  outcome?: string | null;
  current_milestone_id?: string | null;
  current_milestone_order?: number | null;
  channels: string[];
  latest_agent_id?: string | null;
  started_at: string;
  last_activity_at: string;
  ended_at?: string | null;
}

export interface JourneyInstanceDetail {
  instance: JourneyInstance;
  definition?: JourneyDefinition | null;
  version?: JourneyDefinitionVersion | null;
  touchpoints: JourneyTouchpoint[];
  events: JourneyEvent[];
}

export interface JourneyInstanceListResponse {
  journeys: JourneyInstanceSummary[];
  total_count: number;
  page: number;
  page_size: number;
}

export interface JourneyTouchpointListResponse {
  touchpoints: JourneyTouchpoint[];
}

export interface JourneyEventListResponse {
  events: JourneyEvent[];
}

export interface JourneyAnnotationCreate {
  note: string;
  label?: string | null;
  metadata?: Record<string, unknown>;
}

export interface JourneyInstanceEvidenceResponse {
  journey_id: string;
  conversations: Array<Record<string, unknown>>;
  traces_by_conversation: Record<string, Array<Record<string, unknown>>>;
  realtime_events_by_conversation: Record<string, Array<Record<string, unknown>>>;
  tool_invocations_by_conversation: Record<string, Array<Record<string, unknown>>>;
}

export interface JourneyReplayFailure {
  journey_id: string;
  code: string;
  message: string;
}

export interface JourneyReplayResponse {
  journey_id: string;
  definition_id: string;
  definition_version_id: string;
  conversation_ids: string[];
  emitted_event_count: number;
  preserved_event_count: number;
}

export interface JourneyDefinitionReplayResponse {
  definition_id: string;
  total_candidates: number;
  replayed_journey_ids: string[];
  failures: JourneyReplayFailure[];
  emitted_event_count: number;
  preserved_event_count: number;
  discovered_conversation_count: number;
  discovered_subject_count: number;
}

export interface JourneyRuntimeJob {
  job_id: string;
  organization_id: string;
  kind: JourneyRuntimeJobKind;
  definition_id?: string | null;
  journey_id?: string | null;
  status: JourneyRuntimeJobStatus;
  worker_id?: string | null;
  lease_expires_at?: string | null;
  attempt_count: number;
  payload: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  error?: string | null;
  submitted_at: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface JourneyRuntimeKindMetrics {
  kind: JourneyRuntimeJobKind;
  queued_jobs: number;
  running_jobs: number;
  completed_jobs: number;
  failed_jobs: number;
  recent_failures: number;
  last_failure_at?: string | null;
  last_success_at?: string | null;
}

export interface JourneyRuntimeAlert {
  code: string;
  severity: JourneyReviewSeverity;
  kind: JourneyRuntimeJobKind;
  message: string;
  recent_failures: number;
  threshold: number;
  window_seconds: number;
  last_failure_at?: string | null;
}

export interface JourneyRuntimeStatus {
  queued_jobs: number;
  running_jobs: number;
  completed_jobs: number;
  failed_jobs: number;
  embedded_worker_enabled: boolean;
  last_error?: string | null;
  job_metrics: JourneyRuntimeKindMetrics[];
  alerts: JourneyRuntimeAlert[];
  recent_jobs: JourneyRuntimeJob[];
}

export interface JourneyFunnelStage {
  milestone_id: string;
  milestone_name: string;
  order_index: number;
  entered_count: number;
  completed_count: number;
  active_count: number;
  completion_rate: number;
}

export interface JourneyFunnelAnalysis {
  definition_id: string;
  definition_version_id: string;
  period_start?: string | null;
  period_end?: string | null;
  total_journeys: number;
  completed_journeys: number;
  stages: JourneyFunnelStage[];
}

export interface JourneyDropOffRow {
  milestone_id: string;
  milestone_name: string;
  drop_off_count: number;
  active_count: number;
  outcome_counts: Record<string, number>;
}

export interface JourneyDropOffAnalysis {
  definition_id: string;
  definition_version_id: string;
  period_start?: string | null;
  period_end?: string | null;
  rows: JourneyDropOffRow[];
}

export interface JourneyPathRow {
  path: string[];
  count: number;
}

export interface JourneyPathAnalysis {
  definition_id: string;
  definition_version_id: string;
  period_start?: string | null;
  period_end?: string | null;
  rows: JourneyPathRow[];
}

export interface JourneyTrendPoint {
  bucket_start: string;
  opened_count: number;
  completed_count: number;
  abandoned_count: number;
  transferred_count: number;
  failed_count: number;
}

export interface JourneyTrendAnalysis {
  definition_id?: string | null;
  definition_version_id?: string | null;
  period_start?: string | null;
  period_end?: string | null;
  granularity: string;
  points: JourneyTrendPoint[];
}

export interface JourneyChannelMixEntry {
  channel: string;
  journey_count: number;
  touchpoint_count: number;
}

export interface JourneyChannelMixAnalysis {
  definition_id?: string | null;
  definition_version_id?: string | null;
  period_start?: string | null;
  period_end?: string | null;
  rows: JourneyChannelMixEntry[];
}

export interface JourneyAnalyticsRebuildRequest {
  definition_id?: string | null;
  definition_version_id?: string | null;
  period_start?: string | null;
  period_end?: string | null;
  granularity?: string;
  channel?: string | null;
  agent_id?: string | null;
  execution_mode?: JourneyExecutionMode;
}

export interface JourneyAnalyticsRebuildResponse {
  definition_id?: string | null;
  definition_version_id?: string | null;
  period_start?: string | null;
  period_end?: string | null;
  rebuilt_views: string[];
  snapshot_count: number;
}

export interface JourneyAbandonmentSweepRequest {
  definition_id?: string | null;
  execution_mode?: JourneyExecutionMode;
}

export interface JourneyAbandonmentSweepResponse {
  definition_id?: string | null;
  abandoned_journey_ids: string[];
}

export interface JourneyDefinitionBundleEntry {
  definition: JourneyDefinition;
  versions: JourneyDefinitionVersion[];
}

export interface JourneyDefinitionBundle {
  schema_version: string;
  exported_at: string;
  definitions: JourneyDefinitionBundleEntry[];
}

export interface JourneyDefinitionImportRequest {
  bundle: JourneyDefinitionBundle;
  preserve_ids?: boolean;
}

export interface JourneyDefinitionImportResponse {
  imported_definition_ids: string[];
  imported_version_ids: string[];
}

export interface JourneyDefinitionCreate {
  slug: string;
  name: string;
  description?: string | null;
  subject_strategy: SubjectKeyStrategy;
  scope?: JourneyScope;
  tags?: string[];
  settings?: Record<string, unknown>;
}

export interface JourneyDefinitionUpdate {
  slug?: string | null;
  name?: string | null;
  description?: string | null;
  subject_strategy?: SubjectKeyStrategy | null;
  scope?: JourneyScope | null;
  status?: string | null;
  tags?: string[] | null;
  settings?: Record<string, unknown> | null;
}

export interface JourneyDefinitionVersionCreate {
  based_on_version_id?: string | null;
  rules: JourneyDefinitionRules;
}

export interface JourneyDefinitionVersionUpdate {
  rules?: JourneyDefinitionRules | null;
}

export interface JourneyDefinitionPublishRequest {
  definition_version_id?: string | null;
}

export interface JourneyReplayRequest {
  preserve_manual_events?: boolean;
  execution_mode?: JourneyExecutionMode;
}

export interface JourneyDefinitionRebuildRequest {
  definition_version_id?: string | null;
  preserve_manual_events?: boolean;
  execution_mode?: JourneyExecutionMode;
}

export interface JourneyAnalyticsQuery {
  definition_id?: string;
  definition_version_id?: string;
  period_start?: string;
  period_end?: string;
  granularity?: string;
  channel?: string;
  agent_id?: string;
}

export interface JourneyListQuery {
  definition_id?: string;
  status?: string;
  outcome?: string;
  subject_key?: string;
  started_after?: string;
  started_before?: string;
  channel?: string;
  agent_id?: string;
  page?: number;
  page_size?: number;
}
