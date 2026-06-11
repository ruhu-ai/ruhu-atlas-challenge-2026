import type {
  ClassifierProfile,
  ReviewDisposition,
  ReviewQueueRowReadModel,
  RuntimeChannel,
  TagDefinition,
} from '@/types/intent-tags'

export type WorkspaceTab = 'overview' | 'taxonomy' | 'reviews' | 'summaries' | 'webhooks'

export type IntentFormState = {
  name: string
  display_name: string
  description: string
  category: string
  example_phrases: string
  confidence_threshold: string
  priority: string
  is_active: boolean
  is_deprecated: boolean
  color: string
  icon: string
  agent_id: string
  taxonomy_version_id: string
  metadata_json: string
}

export type TagFormState = {
  name: string
  display_name: string
  description: string
  tag_kind: TagDefinition['tag_kind']
  category: string
  confidence_threshold: string
  apply_scope: TagDefinition['apply_scope']
  related_intent_id: string
  is_active: boolean
  is_deprecated: boolean
  color: string
  icon: string
  agent_id: string
  taxonomy_version_id: string
  rule_config_json: string
  metadata_json: string
}

export type ProfileFormState = {
  adapter_name: string
  agent_id: string
  supported_languages: string
  taxonomy_mode: ClassifierProfile['taxonomy_mode']
  taxonomy_version_id: string
  tool_catalog_json: string
  policy_profile_json: string
  profile_metadata_json: string
  is_active: boolean
}

export type VersionFormState = {
  name: string
  notes: string
}

export type WebhookFormState = {
  name: string
  url: string
  agent_ids: string[]
  channels: RuntimeChannel[]
  signing_secret_ref: string
  extra_headers_json: string
  timeout_seconds: string
  max_retries: string
  retry_backoff_seconds: string
  is_active: boolean
}

export type ReviewResolutionState = {
  disposition: ReviewDisposition
  review_notes: string
  corrected_decision_json: string
  corrected_fields_json: string
  corrected_tag_definition_ids: string
}

export const RUNTIME_CHANNEL_OPTIONS: RuntimeChannel[] = [
  'phone',
  'whatsapp',
  'web_chat',
  'web_widget',
  'browser',
]

export const TAG_KIND_OPTIONS: TagDefinition['tag_kind'][] = [
  'goal_attribute',
  'failure_reason',
  'blocker',
  'priority',
  'risk',
  'outcome_attribute',
]

export const SUMMARY_STATUS_OPTIONS = ['all', 'draft', 'final', 'corrected', 'superseded'] as const
export const REVIEW_STATUS_OPTIONS = ['all', 'pending', 'in_review', 'resolved', 'dismissed'] as const

export type SummaryStatusFilter = (typeof SUMMARY_STATUS_OPTIONS)[number]
export type ReviewStatusFilter = (typeof REVIEW_STATUS_OPTIONS)[number]

export function emptyIntentForm(agentId?: string): IntentFormState {
  return {
    name: '',
    display_name: '',
    description: '',
    category: '',
    example_phrases: '',
    confidence_threshold: '0.7',
    priority: '0',
    is_active: true,
    is_deprecated: false,
    color: '',
    icon: '',
    agent_id: agentId ?? 'none',
    taxonomy_version_id: 'live',
    metadata_json: '{}',
  }
}

export function emptyTagForm(agentId?: string): TagFormState {
  return {
    name: '',
    display_name: '',
    description: '',
    tag_kind: 'blocker',
    category: '',
    confidence_threshold: '0.6',
    apply_scope: 'conversation',
    related_intent_id: 'none',
    is_active: true,
    is_deprecated: false,
    color: '',
    icon: '',
    agent_id: agentId ?? 'none',
    taxonomy_version_id: 'live',
    rule_config_json: '{}',
    metadata_json: '{}',
  }
}

export function emptyProfileForm(agentId?: string): ProfileFormState {
  return {
    adapter_name: 'ruhu-general',
    agent_id: agentId ?? 'none',
    supported_languages: 'en',
    taxonomy_mode: 'live',
    taxonomy_version_id: 'live',
    tool_catalog_json: '[]',
    policy_profile_json: '{}',
    profile_metadata_json: '{}',
    is_active: true,
  }
}

export function emptyVersionForm(): VersionFormState {
  return {
    name: '',
    notes: '',
  }
}

export function emptyWebhookForm(agentId?: string): WebhookFormState {
  return {
    name: '',
    url: '',
    agent_ids: agentId && agentId !== 'all' ? [agentId] : [],
    channels: [],
    signing_secret_ref: '',
    extra_headers_json: '{}',
    timeout_seconds: '5',
    max_retries: '5',
    retry_backoff_seconds: '5',
    is_active: true,
  }
}

export function emptyReviewResolutionState(row: ReviewQueueRowReadModel | null): ReviewResolutionState {
  const correctedDecision =
    row?.target_kind === 'turn'
      ? {
          intent_name: row.effective_intent_name || row.current_intent_name || 'unknown_intent',
          confidence: 0.9,
          language: 'en',
          response_language: 'en',
          tool_route: null,
          slots: {},
          signals: {},
        }
      : {}

  const correctedFields =
    row?.target_kind === 'summary'
      ? {
          primary_intent_name: row.effective_intent_name || row.current_intent_name || null,
          outcome: row.outcome || null,
          resolution_status: row.resolution_status || null,
        }
      : {}

  return {
    disposition: 'confirmed',
    review_notes: '',
    corrected_decision_json: JSON.stringify(correctedDecision, null, 2),
    corrected_fields_json: JSON.stringify(correctedFields, null, 2),
    corrected_tag_definition_ids: '',
  }
}
