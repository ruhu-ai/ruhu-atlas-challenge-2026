/**
 * Shared types for the production widget contract used by the Ruhu frontend.
 */

// --- Widget Configuration ---

export interface WidgetConfig {
  /** Backing agent identifier used by public widget endpoints. */
  agentId: string
  /** Base API URL used by the embed runtime (defaults to script origin). */
  apiUrl: string
  /** Optional publishable key (pk_live_/pk_test_) for origin validation. */
  publishableKey?: string
  /** Widget mode. */
  mode: 'chat' | 'voice' | 'multimodal'
  /** Widget position on page. */
  position: 'bottom-right' | 'bottom-left' | 'top-right' | 'top-left'
  /** Primary brand color. */
  primaryColor: string
  /** Accent/gradient color. */
  accentColor: string
  /** FAB button text. */
  buttonText: string
  /** Company display name. */
  companyName: string
  /** Optional logo URL. */
  companyLogo?: string
  /** Welcome message shown when widget opens. */
  welcomeMessage: string
  /** Auto-open widget on page load. */
  autoOpen: boolean
  /** Show "Powered by Ruhu" footer. */
  showPoweredBy: boolean
  /** Optional feature flags resolved from server-side widget config. */
  features?: {
    browser_tasks?: boolean
    [key: string]: boolean | undefined
  }
  browserTaskRenderMode?: 'hidden' | 'summaries' | 'full'
  browserTaskApprovalMode?: 'none' | 'explicit' | 'operator_only'
  browserTaskShowLiveSnapshot?: boolean
  browserTaskMaxVisibleArtifacts?: number
}

export const DEFAULT_WIDGET_CONFIG: Omit<WidgetConfig, 'agentId' | 'apiUrl'> = {
  mode: 'multimodal',
  position: 'bottom-right',
  primaryColor: '#E64E20',
  accentColor: '#D44D00',
  buttonText: 'Talk to us',
  companyName: 'Support',
  welcomeMessage: 'Hi! How can I help you today?',
  autoOpen: false,
  showPoweredBy: true,
}

// --- Session ---

export interface WidgetSession {
  conversationId: string
  sessionToken: string
  stateId?: string | null
  messages: RenderedMessage[]
  resumed: boolean
  pendingToolInvocations: ToolInvocation[]
  agentId: string
}

// --- Chat Messages ---

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: Date
  done: boolean
  metadata?: Record<string, unknown>
  attachments?: WidgetAttachment[]
  /**
   * Delivery status for user-authored messages. Assistant/system messages
   * leave this undefined (they're always considered "sent" once the SSE
   * event fires).
   *
   *  - ``sending``: optimistically rendered, awaiting server ack
   *  - ``sent``:    server accepted the message and processing succeeded
   *  - ``failed``:  the send raised — UI should surface a retry affordance
   */
  status?: 'sending' | 'sent' | 'failed'
  /**
   * Populated on ``status='failed'`` only — the human-readable reason
   * (e.g., "Rate limit exceeded — retry in 30s") that the retry UI shows
   * alongside the message bubble.
   */
  failureReason?: string
}

export interface ArtifactDisambiguationCandidate {
  artifact_id: string
  artifact_type: string
  title: string
  status: string
  external_id?: string | null
  reply_text?: string | null
}

export interface RenderedMessage {
  role: 'user' | 'assistant' | 'system'
  text: string
  message_type?: string
  payload?: Record<string, unknown>
  attachments?: WidgetAttachment[]
}

export interface WidgetAttachment {
  attachment_id: string
  conversation_id?: string | null
  message_id?: string | null
  source: string
  kind: string
  filename?: string | null
  content_type?: string | null
  size_bytes?: number | null
  scan_status: string
  extraction_status: string
  trust_tier?: string
  available_views?: string[]
  inline_text?: string | null
  extracted_text?: string | null
  structured_data?: Record<string, unknown>
  metadata?: Record<string, unknown>
  policy?: Record<string, unknown>
  created_at?: string | null
  conversation_scope?: string | null
}

export interface WidgetAttachmentUploadResponse {
  attachment: {
    attachment_id: string
    filename: string
    kind: string
    source: string
    content_type: string
    size_bytes: number
    scan_status: string
    extraction_status: string
  }
  extraction?: {
    extraction_status?: string
    summary?: string
    structured_data?: Record<string, unknown>
  }
}

export interface ToolInvocation {
  invocation_id: string
  tool_ref: string
  status: string
  reason: string
  decision_reason?: string | null
  error?: string | null
  metadata?: Record<string, unknown>
  payload?: Record<string, unknown>
}

export interface WidgetSessionResponse {
  conversation_id: string
  agent_id: string
  step_id?: string | null
  resumed: boolean
  session_token: string | null
  messages: RenderedMessage[]
  pending_tool_invocations: ToolInvocation[]
}

export interface WidgetMessageResponse {
  conversation_id: string
  step_after: string | null
  messages: RenderedMessage[]
  trace_id: string
  pending_tool_invocations: ToolInvocation[]
}

/**
 * Short-lived interaction status projected to the widget (spec 23 §Projected
 * Activity / Status Trail).  Used to render "what's happening right now"
 * banners — pending actions, permission waits, active repairs.
 */
export interface WidgetInteractionStatusItem {
  item_id: string
  item_type: 'activity' | 'permission' | 'repair' | 'policy'
  summary: string
  source_ref?: string | null
  started_at?: string
  expires_at?: string | null
}

/**
 * Latest voice-pipeline lifecycle event projected to the widget (spec 23
 * §Voice Mechanics).  Drives "assistant speaking" / "interrupted" banners.
 */
export interface WidgetVoiceActivity {
  name: string
  payload: Record<string, unknown>
  created_at: string
}

/**
 * Exact resolved voice-interaction policy block projected from the active voice
 * transport metadata for this conversation.
 */
export interface WidgetVoiceInteractionPolicy {
  step_id?: string | null
  endpointing_ms?: number | null
  soft_timeout_ms?: number | null
  turn_eagerness?: 'low' | 'normal' | 'high' | null
  interruptibility_policy?:
    | 'always_interruptible'
    | 'interruptible_except_policy'
    | 'non_interruptible'
    | null
}

export interface WidgetBrowserApprovalProjection {
  approval_id: string
  kind: string
  state: string
  prompt: string
  expires_at?: string | null
  task_pack_label?: string | null
  domain_label?: string | null
  performs_write: boolean
  approval_kind?: string | null
  credential_labels: string[]
}

export interface WidgetBrowserArtifactProjection {
  artifact_id: string
  filename?: string | null
  kind?: string | null
  content_type?: string | null
  public_widget_download_url?: string | null
}

export interface WidgetBrowserTaskProjection {
  task_id: string
  title: string
  summary?: string | null
  state: string
  approval_state: string
  task_pack_id?: string | null
  task_pack_version?: string | null
  task_pack_label?: string | null
  domain_label?: string | null
  latest_progress?: string | null
  approval?: WidgetBrowserApprovalProjection | null
  artifacts: WidgetBrowserArtifactProjection[]
  cancellable: boolean
  show_live_snapshot: boolean
  live_snapshot_artifact_id?: string | null
  updated_at: string
}

export interface WidgetProjectionResponse {
  snapshot_id: string
  conversation_id: string
  pending_tool_invocations: ToolInvocation[]
  attachments: WidgetAttachmentProjection[]
  browser_tasks: WidgetBrowserTaskProjection[]
  /** Runtime-owned "what's happening now" items (spec 23).  May be empty. */
  interaction_status?: WidgetInteractionStatusItem[]
  /** Latest voice lifecycle event for this conversation, if any. */
  voice_activity?: WidgetVoiceActivity | null
  /** Exact active voice transport policy block, if a voice session is active/recent. */
  voice_interaction_policy?: WidgetVoiceInteractionPolicy | null
  /** Names of components whose fetch failed; their fields fell back to empty/null and should not be treated as authoritative. */
  degraded_components?: string[]
}

export interface WidgetAttachmentProjection {
  attachment: WidgetAttachment
  extraction?: {
    extraction_status?: string
    summary?: string
    structured_data?: Record<string, unknown>
  }
}

export interface LiveKitTransport {
  provider: 'livekit'
  url: string
  room_name: string
  token: string
  participant_identity: string
  agent_name?: string
  sdk_version_target?: string
  voice_mode?: string
  dispatch_strategy?: string
  dispatch?: Record<string, unknown>
  metadata?: Record<string, unknown>
}

export interface WidgetVoiceSessionResponse {
  conversation_id: string
  realtime_session_id: string
  resumed: boolean
  state_after: string | null
  transport: LiveKitTransport
  pending_tool_invocations: ToolInvocation[]
}

export interface WidgetVoiceDisconnectRequest {
  realtime_session_id?: string | null
  reason?: string | null
  metadata?: Record<string, unknown>
}

export interface WidgetVoiceDisconnectResponse {
  disconnected: boolean
}

export interface WidgetConfigResponse {
  agent_id: string
  widget_mode?: 'chat' | 'voice' | 'multimodal'
  company_name: string
  button_text: string
  primary_color: string
  accent_color: string
  position: 'bottom-right' | 'bottom-left' | 'top-right' | 'top-left'
  show_powered_by: boolean
  welcome_message: string
  subtitle: string
  features?: {
    browser_tasks?: boolean
    [key: string]: boolean | undefined
  }
  browser_task_render_mode?: 'hidden' | 'summaries' | 'full'
  browser_task_approval_mode?: 'none' | 'explicit' | 'operator_only'
  browser_task_show_live_snapshot?: boolean
  browser_task_max_visible_artifacts?: number
}

export interface SSEEventEnvelope {
  event: 'widget.snapshot' | 'heartbeat'
  id?: string
  data?: Record<string, unknown> | string
}

export interface SSETypingEvent {
  is_typing: boolean
}

export interface SSETokenRefreshEvent {
  session_token: string
  expires_at: string
}

export type ConversationStatus = 'active' | 'ended'

export interface SSEConversationStateEvent {
  status: ConversationStatus
  ended_at?: string | null
  disposition?: string | null
  end_message_text?: string | null
}

// --- Voice ---

export interface VoiceCredentials {
  url: string
  room_name: string
  token: string
}

export type CallState = 'idle' | 'connecting' | 'active' | 'ended'

// --- Errors ---

export interface WidgetError {
  error: string
  message: string
  retry_after?: number
}

export type WidgetErrorCode =
  | 'invalid_key'
  | 'origin_blocked'
  | 'agent_unpublished'
  | 'session_expired'
  | 'rate_limited'
  | 'message_too_large'
  | 'server_error'
  | 'agent_offline'

// --- Activity Events ---

export interface ActivityItem {
  activityId: string
  eventType:
    | 'started'
    | 'updated'
    | 'retrying'
    | 'waiting_for_confirmation'
    | 'blocked'
    | 'completed'
    | 'failed'
  label: string
  stepKind: string
  stepId: string
  toolName?: string
  startedAt?: string
  durationMs?: number
  retryCount?: number
  detail?: string
  actionLabel?: string
}

export interface SSEActivityEvent {
  activity_id: string
  event_type:
    | 'started'
    | 'updated'
    | 'retrying'
    | 'waiting_for_confirmation'
    | 'blocked'
    | 'completed'
    | 'failed'
  label: string
  step_kind: string
  step_id: string
  tool_name?: string
  started_at?: string
  duration_ms?: number
  retry_count?: number
  detail?: string
  action_label?: string
}

// --- Analytics Events ---

export interface WidgetAnalyticsEvent {
  event_type: string
  event_data: Record<string, unknown>
  timestamp?: string
}
