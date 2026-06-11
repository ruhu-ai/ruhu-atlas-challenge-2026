export type RuntimeChannel = 'phone' | 'whatsapp' | 'web_chat' | 'web_widget' | 'browser'
export type TaxonomyMode = 'live' | 'pinned' | 'cached_live'
export type TaxonomyVersionStatus = 'draft' | 'published' | 'deprecated'
export type ReviewStatus = 'pending' | 'in_review' | 'resolved' | 'dismissed'
export type ReviewDisposition = 'confirmed' | 'corrected' | 'dismissed' | 'needs_followup'
export type ReviewKind =
  | 'low_confidence_turn'
  | 'policy_violation_turn'
  | 'summary_correction'
  | 'tag_correction'
  | 'manual_flag'
export type SummaryStatus = 'draft' | 'final' | 'corrected' | 'superseded'
export type SummaryResolutionStatus =
  | 'resolved'
  | 'follow_up_required'
  | 'escalated'
  | 'abandoned'
  | 'failed'
  | 'unresolved'
  | 'unknown'
export type TagKind =
  | 'goal_attribute'
  | 'failure_reason'
  | 'blocker'
  | 'priority'
  | 'risk'
  | 'outcome_attribute'
export type TagApplyScope = 'turn' | 'conversation' | 'both'
export type TagAssignmentScope = 'turn' | 'conversation'
export type TagAssignmentSource =
  | 'deterministic_rule'
  | 'summary_rollup'
  | 'operator_manual'
  | 'review_correction'
  | 'backfill_model'
export type ClassificationSourceKind =
  | 'runtime'
  | 'turn_trace'
  | 'realtime_event'
  | 'manual_preview'
  | 'backfill'
export type SemanticWebhookDispatchMode = 'fanout' | 'deliver' | 'both'

export interface AgentSummary {
  id: string
  name: string
  version: string
  step_count: number
  current_draft_version_id: string | null
  current_published_version_id: string | null
}

export interface TaxonomyVersion {
  taxonomy_version_id: string
  organization_id: string
  name: string
  status: TaxonomyVersionStatus
  notes: string | null
  published_at: string | null
  created_at: string
  updated_at: string
}

export interface IntentDefinition {
  intent_definition_id: string
  organization_id: string
  agent_id: string | null
  taxonomy_version_id: string | null
  name: string
  display_name: string
  description: string | null
  category: string | null
  example_phrases: string[]
  confidence_threshold: number
  priority: number
  is_active: boolean
  is_deprecated: boolean
  color: string | null
  icon: string | null
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface TagDefinition {
  tag_definition_id: string
  organization_id: string
  agent_id: string | null
  taxonomy_version_id: string | null
  name: string
  display_name: string
  description: string | null
  tag_kind: TagKind
  category: string | null
  confidence_threshold: number
  apply_scope: TagApplyScope
  related_intent_id: string | null
  is_active: boolean
  is_deprecated: boolean
  color: string | null
  icon: string | null
  rule_config: Record<string, unknown>
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface ClassifierProfile {
  classifier_profile_id: string
  organization_id: string
  agent_id: string | null
  adapter_name: string
  supported_languages: string[]
  taxonomy_mode: TaxonomyMode
  taxonomy_version_id: string | null
  intent_catalog: Array<Record<string, unknown>>
  tool_catalog: Array<Record<string, unknown>>
  catalog_cache_built_at: string | null
  policy_profile: Record<string, unknown>
  profile_metadata: Record<string, unknown>
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface ClassificationReviewItem {
  review_item_id: string
  organization_id: string
  classification_event_id: string | null
  conversation_summary_id: string | null
  status: ReviewStatus
  review_kind: ReviewKind
  review_disposition: ReviewDisposition | null
  review_notes: string | null
  corrected_payload: Record<string, unknown>
  claimed_by_user_id: string | null
  claimed_at: string | null
  reviewed_by_user_id: string | null
  reviewed_at: string | null
  corrected_conversation_summary_id: string | null
  created_at: string
  updated_at: string
}

export interface ConversationSemanticContext {
  organization_id: string
  conversation_id: string
  agent_id: string | null
  agent_version_id: string | null
  channel: RuntimeChannel | null
  status: string | null
  outcome: string | null
  metadata: Record<string, unknown>
  started_at: string | null
  ended_at: string | null
}

export interface ConversationSemanticSummary {
  conversation_summary_id: string
  organization_id: string
  agent_id: string | null
  agent_version_id: string | null
  conversation_id: string
  summary_version: number
  status: SummaryStatus
  primary_intent_name: string | null
  secondary_intents: Array<Record<string, unknown>>
  resolution_status: SummaryResolutionStatus | null
  outcome: string | null
  final_language: string | null
  response_language: string | null
  channel: RuntimeChannel
  requires_human_followup: boolean
  requires_review: boolean
  summary_payload: Record<string, unknown>
  evidence_payload: Record<string, unknown>
  generated_from_event_count: number
  last_event_created_at: string | null
  created_at: string
  updated_at: string
}

export interface TagAssignment {
  tag_assignment_id: string
  organization_id: string
  conversation_id: string
  classification_event_id: string | null
  conversation_summary_id: string | null
  tag_definition_id: string
  assignment_scope: TagAssignmentScope
  assignment_source: TagAssignmentSource
  confidence: number | null
  reason_text: string | null
  evidence_payload: Record<string, unknown>
  is_validated: boolean
  validated_by_user_id: string | null
  validated_at: string | null
  created_at: string
}

export interface TurnClassificationDecision {
  intent_name: string
  confidence: number
  language: string
  response_language: string
  tool_route: string | null
  slots: Record<string, unknown>
  signals: Record<string, unknown>
}

export interface TurnClassificationEvent extends TurnClassificationDecision {
  classification_event_id: string
  organization_id: string
  agent_id: string | null
  agent_version_id: string | null
  classifier_profile_id: string | null
  conversation_id: string
  turn_trace_id: string | null
  realtime_event_id: string | null
  channel: RuntimeChannel
  provider: string | null
  source_kind: ClassificationSourceKind
  adapter_name: string
  model_version: string
  taxonomy_mode: TaxonomyMode
  taxonomy_version_id: string | null
  request_payload: Record<string, unknown>
  context_payload: Record<string, unknown>
  decision_payload: Record<string, unknown>
  created_at: string
}

export interface TaxonomySnapshotReadModel {
  taxonomy_versions: TaxonomyVersion[]
  intents: IntentDefinition[]
  tags: TagDefinition[]
  profiles: ClassifierProfile[]
}

export interface IntentAnalyticsRowReadModel {
  intent_name: string
  display_name: string
  summary_count: number
  turn_event_count: number
  corrected_turn_count: number
  low_confidence_turn_count: number
  review_count: number
  human_followup_count: number
}

export interface TagAnalyticsRowReadModel {
  tag_definition_id: string
  tag_name: string
  display_name: string
  tag_kind: string
  assignment_count: number
  validated_count: number
  turn_assignment_count: number
  conversation_assignment_count: number
  assignment_source_counts: Record<string, number>
}

export interface SummaryOutcomeDistributionReadModel {
  channel: string
  outcome: string | null
  resolution_status: string | null
  count: number
}

export interface SemanticInsightRowReadModel {
  insight_key: string
  blocker_kind: string
  title: string
  summary: string
  agent_id: string | null
  primary_intent_name: string | null
  tag_definition_id: string | null
  tag_name: string | null
  tag_kind: string | null
  resolution_status: string | null
  outcome: string | null
  requires_human_followup: boolean
  occurrence_count: number
  coverage_ratio: number
  example_conversation_ids: string[]
}

export interface IntentTagsInsightsReadModel {
  totals: Record<string, number>
  rows: SemanticInsightRowReadModel[]
}

export interface ReviewQueueRowReadModel {
  review_item: ClassificationReviewItem
  conversation_id: string | null
  target_kind: string
  channel: string | null
  current_intent_name: string | null
  effective_intent_name: string | null
  summary_primary_intent_name: string | null
  resolution_status: string | null
  outcome: string | null
}

export interface SummaryListItemReadModel {
  summary: ConversationSemanticSummary
  effective_summary: ConversationSemanticSummary
  is_corrected: boolean
  review_item: ClassificationReviewItem | null
  tag_assignments: TagAssignment[]
  tag_names: string[]
}

export interface TurnClassificationEvidenceReadModel {
  event: TurnClassificationEvent
  effective_event: TurnClassificationEvent
  review_item: ClassificationReviewItem | null
  is_corrected: boolean
  tag_assignments: TagAssignment[]
}

export interface EffectiveConversationSummaryReadModel {
  summary: ConversationSemanticSummary
  effective_summary: ConversationSemanticSummary
  tag_assignments: TagAssignment[]
  review_item: ClassificationReviewItem | null
  is_corrected: boolean
}

export interface ConversationSummaryDetailReadModel {
  conversation_context: ConversationSemanticContext | null
  effective_summary: EffectiveConversationSummaryReadModel
  turn_evidence: TurnClassificationEvidenceReadModel[]
}

export interface IntentTagsAnalyticsReadModel {
  totals: Record<string, number>
  review_status_counts: Record<string, number>
  intent_rows: IntentAnalyticsRowReadModel[]
  tag_rows: TagAnalyticsRowReadModel[]
  outcome_rows: SummaryOutcomeDistributionReadModel[]
  insight_rows: SemanticInsightRowReadModel[]
}

export interface SemanticWebhookTargetReadModel {
  webhook_target_id: string
  organization_id: string
  name: string
  url: string
  event_name: string
  agent_ids: string[]
  channels: string[]
  extra_headers: Record<string, string>
  timeout_seconds: number
  max_retries: number
  retry_backoff_seconds: number
  is_active: boolean
  has_signing_secret: boolean
  signing_secret_source: string
  last_attempt_at: string | null
  last_success_at: string | null
  last_failure_at: string | null
  consecutive_failure_count: number
  last_error: string | null
  created_at: string
  updated_at: string
}

export interface SemanticWebhookDispatchResponse {
  publication_attempted: number
  publication_fanned_out: number
  publication_skipped: number
  publication_failed: number
  delivery_attempted: number
  delivery_delivered: number
  delivery_failed: number
  delivery_retried: number
  delivery_skipped: number
}

export interface IntentDefinitionCreateRequest {
  organization_id?: string
  agent_id?: string | null
  taxonomy_version_id?: string | null
  name: string
  display_name: string
  description?: string | null
  category?: string | null
  example_phrases?: string[]
  confidence_threshold?: number
  priority?: number
  is_active?: boolean
  is_deprecated?: boolean
  color?: string | null
  icon?: string | null
  metadata?: Record<string, unknown>
}

export interface IntentDefinitionUpdateRequest {
  display_name?: string
  description?: string | null
  category?: string | null
  example_phrases?: string[]
  confidence_threshold?: number
  priority?: number
  is_active?: boolean
  is_deprecated?: boolean
  color?: string | null
  icon?: string | null
  metadata?: Record<string, unknown>
  taxonomy_version_id?: string | null
}

export interface TagDefinitionCreateRequest {
  organization_id?: string
  agent_id?: string | null
  taxonomy_version_id?: string | null
  name: string
  display_name: string
  description?: string | null
  tag_kind: TagKind
  category?: string | null
  confidence_threshold?: number
  apply_scope?: TagApplyScope
  related_intent_id?: string | null
  is_active?: boolean
  is_deprecated?: boolean
  color?: string | null
  icon?: string | null
  rule_config?: Record<string, unknown>
  metadata?: Record<string, unknown>
}

export interface TagDefinitionUpdateRequest {
  display_name?: string
  description?: string | null
  tag_kind?: TagKind
  category?: string | null
  confidence_threshold?: number
  apply_scope?: TagApplyScope
  related_intent_id?: string | null
  is_active?: boolean
  is_deprecated?: boolean
  color?: string | null
  icon?: string | null
  rule_config?: Record<string, unknown>
  metadata?: Record<string, unknown>
  taxonomy_version_id?: string | null
}

export interface ClassifierProfileCreateRequest {
  organization_id?: string
  agent_id?: string | null
  adapter_name: string
  supported_languages?: string[]
  taxonomy_mode?: TaxonomyMode
  taxonomy_version_id?: string | null
  tool_catalog?: Array<Record<string, unknown>>
  policy_profile?: Record<string, unknown>
  profile_metadata?: Record<string, unknown>
  is_active?: boolean
}

export interface ClassifierProfileUpdateRequest {
  agent_id?: string | null
  adapter_name?: string
  supported_languages?: string[]
  taxonomy_mode?: TaxonomyMode
  taxonomy_version_id?: string | null
  tool_catalog?: Array<Record<string, unknown>>
  policy_profile?: Record<string, unknown>
  profile_metadata?: Record<string, unknown>
  is_active?: boolean
}

export interface ProfileRebuildRequest {
  organization_id?: string
  agent_id?: string | null
  live_tool_catalog?: Array<Record<string, unknown>>
}

export interface TaxonomyVersionCreateRequest {
  organization_id?: string
  name: string
  notes?: string | null
}

export interface SemanticWebhookTargetCreateRequest {
  organization_id?: string
  name: string
  url: string
  agent_ids?: string[]
  channels?: RuntimeChannel[]
  signing_secret_ref?: string | null
  extra_headers?: Record<string, string>
  timeout_seconds?: number
  max_retries?: number
  retry_backoff_seconds?: number
  is_active?: boolean
}

export interface SemanticWebhookTargetUpdateRequest {
  name?: string
  url?: string
  agent_ids?: string[]
  channels?: RuntimeChannel[]
  signing_secret_ref?: string | null
  extra_headers?: Record<string, string>
  timeout_seconds?: number
  max_retries?: number
  retry_backoff_seconds?: number
  is_active?: boolean
}

export interface ReviewClaimRequest {
  user_id?: string | null
}

export interface TurnReviewResolutionRequest {
  user_id?: string | null
  disposition: ReviewDisposition
  corrected_decision?: TurnClassificationDecision | null
  review_notes?: string | null
}

export interface SummaryReviewResolutionRequest {
  user_id?: string | null
  disposition: ReviewDisposition
  corrected_fields?: Record<string, unknown>
  corrected_tag_definition_ids?: string[]
  review_notes?: string | null
}
