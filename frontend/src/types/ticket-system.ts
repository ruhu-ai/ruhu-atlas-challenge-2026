export interface TicketDashboardHandler {
  handler_id: string
  handler_name: string
}

export interface TicketDashboardSummary {
  total_count: number
  resolved_rate: number
  transferred_count: number
  average_duration_seconds: number
}

export interface LinkedExternalCaseSummary {
  link_id: string
  provider: string
  external_case_id: string
  external_case_key: string | null
  external_case_url: string | null
  external_case_status: string | null
  sync_status: string
}

export interface TicketDashboardItem {
  conversation_id: string
  organization_id: string | null
  handler_id: string
  handler_name: string
  channel: string | null
  participant_display: string
  participant_ref: string | null
  status: string
  outcome: string | null
  outcome_reason: string | null
  started_at: string
  ended_at: string | null
  duration_seconds: number
  message_count: number
  sentiment_score: number | null
  has_handoff: boolean
  has_tool_failures: boolean
  last_activity_at: string
  summary: string | null
  tags: string[]
  linked_support_case_count: number
  linked_external_cases: LinkedExternalCaseSummary[]
}

export interface TicketDashboardResponse {
  summary: TicketDashboardSummary
  handlers: TicketDashboardHandler[]
  items: TicketDashboardItem[]
}

export interface TicketTranscriptEntry {
  entry_id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  channel: string | null
  text: string
  source: string
  recorded_at: string
  metadata: Record<string, unknown>
}

export interface TicketEvidenceEntry {
  evidence_id: string
  kind: 'session' | 'tool_call' | 'fact_update' | 'semantic_event' | 'event'
  label: string
  status: string | null
  detail: string | null
  recorded_at: string
  metadata: Record<string, unknown>
}

export interface TicketTimelineEntry {
  kind: 'state_transition' | 'assistant_message' | 'tool_call' | 'fact_update' | 'semantic_event'
  label: string
  detail: string | null
  recorded_at: string
  metadata: Record<string, unknown>
}

export interface TicketSupportCase {
  case_id: string
  organization_id: string
  case_number: string
  title: string
  description: string
  status: string
  priority: string
  category: string
  source: string
  primary_conversation_id: string | null
  related_conversation_ids: string[]
  created_by_user_id: string | null
  assigned_to_user_id: string | null
  assigned_team: string | null
  owning_agent_id: string | null
  participant_ref: string | null
  participant_display: string | null
  participant_email: string | null
  participant_phone: string | null
  tags: string[]
  custom_fields: Record<string, unknown>
  case_metadata: Record<string, unknown>
  resolution: Record<string, unknown> | null
  created_at: string
  updated_at: string
  resolved_at: string | null
  closed_at: string | null
}

export interface ExternalCaseLink {
  link_id: string
  organization_id: string
  provider: string
  connection_id: string
  external_case_id: string
  external_case_key: string | null
  external_case_url: string | null
  external_case_status: string | null
  external_case_priority: string | null
  support_case_id: string | null
  conversation_id: string | null
  sync_status: string
  last_synced_at: string | null
  last_sync_error: string | null
  provider_payload_snapshot: Record<string, unknown>
  comments: Array<Record<string, unknown>>
  created_at: string
  updated_at: string
}

export interface TicketConversationDetail {
  conversation: TicketDashboardItem
  support_cases: TicketSupportCase[]
  external_case_links: ExternalCaseLink[]
  transcript: TicketTranscriptEntry[]
  evidence: TicketEvidenceEntry[]
  timeline: TicketTimelineEntry[]
}

export interface TicketDashboardQueryParams {
  [key: string]: string | number | boolean | undefined | null
  q?: string
  handler_id?: string
  channel?: string
  outcome?: string
  days?: number
  sort_by?: 'started_at' | 'duration_seconds' | 'sentiment_score' | 'outcome' | 'message_count'
  sort_dir?: 'asc' | 'desc'
  limit?: number
  offset?: number
}
