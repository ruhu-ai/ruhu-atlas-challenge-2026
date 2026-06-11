/**
 * Analytics Types
 *
 * TypeScript types matching backend schemas for analytics,
 * performance metrics, and conversation data.
 */

// ==================== Performance Metrics Types ====================

export interface PerformanceMetric {
  id: string
  organization_id: string
  agent_id: string | null
  metric_type: string
  metric_name: string
  value: number
  unit: string | null
  timestamp: string
  tags: Record<string, unknown> | null
  metadata: Record<string, unknown> | null
  created_at: string
}

export interface PerformanceMetricCreate {
  agent_id?: string | null
  metric_type: string
  metric_name: string
  value: number
  unit?: string | null
  timestamp?: string
  tags?: Record<string, unknown> | null
  metadata?: Record<string, unknown> | null
}

// ==================== Analytics Event Types ====================

export interface AnalyticsEvent {
  id: string
  organization_id: string
  agent_id: string | null
  event_type: string
  event_name: string
  event_data: Record<string, unknown>
  timestamp: string
  user_id: string | null
  session_id: string | null
  created_at: string
}

export interface AnalyticsEventCreate {
  agent_id?: string | null
  event_type: string
  event_name: string
  event_data: Record<string, unknown>
  timestamp?: string
  user_id?: string | null
  session_id?: string | null
}

// ==================== Conversation Analytics Types ====================

export interface Conversation {
  id: string
  organization_id: string
  agent_id: string
  agent_name: string | null   // joined from agents table
  session_id: string
  customer_id: string | null  // caller/user identifier ("From" field)
  channel: string             // voice | chat | email
  status: string              // active | completed | failed
  sentiment_score: number | null
  quality_score: number | null
  outcome: string | null      // resolved | transferred | abandoned | failed | …
  resolution_status: 'resolved' | 'escalated' | 'abandoned' | 'unresolved' | null
  topic_category: string | null
  outcome_metadata: Record<string, unknown>
  tags: string[]
  context: Record<string, unknown>
  summary: string | null
  started_at: string
  ended_at: string | null
  duration_seconds: number | null
  message_count: number
  avg_response_time: number | null
  recording_url: string | null
  created_at: string
  updated_at: string
}

export interface ConversationCreate {
  organization_id: string
  agent_id: string
  user_id?: string | null
  channel: string
  status?: string
  metadata?: Record<string, unknown> | null
}

export interface ConversationUpdate {
  status?: string
  sentiment_score?: number | null
  quality_score?: number | null
  outcome?: string | null
  outcome_metadata?: Record<string, unknown>
  ended_at?: string | null
  duration_seconds?: number | null
  message_count?: number
  summary?: string | null
  tags?: string[]
}

// ==================== Voice Session Analytics Types ====================

export interface VoiceSession {
  id: string
  organization_id: string
  agent_id: string
  user_id: string
  room_name: string
  livekit_room_sid: string | null
  status: string // active, ended
  started_at: string
  ended_at: string | null
  duration_seconds: number | null
  session_metadata: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

// ==================== Dashboard Analytics Types ====================

export interface DashboardStats {
  // Real-time metrics
  active_conversations: number
  active_voice_sessions: number
  total_conversations_today: number
  total_voice_calls_today: number
  avg_response_time_ms: number
  avg_resolution_rate: number

  // Sentiment distribution
  sentiment_positive: number
  sentiment_neutral: number
  sentiment_negative: number

  // Channel distribution
  conversations_by_channel: Record<string, number>

  // Performance
  uptime_percentage: number
  error_rate: number
  avg_latency_ms: number
}

// ==================== Home Dashboard Types (GET /dashboard/stats) ====================

export interface DashboardAgentPerformance {
  agent_id: string
  agent_name: string
  status: 'draft' | 'published'
  conversation_count: number
  active_conversations: number
  resolution_rate: number
  avg_turns_per_conversation: number
  avg_handle_time_seconds: number
}

export interface DashboardResolutionPoint {
  date: string
  resolved: number
  total: number
  rate: number
  target: number
}

export interface HomeDashboardStats {
  total_agents: number
  active_conversations: number
  resolution_rate: number
  avg_handle_time_seconds: number
  agent_performance: DashboardAgentPerformance[]
  resolution_trend: DashboardResolutionPoint[]
}

export interface TimeSeriesDataPoint {
  timestamp: string
  value: number
  label?: string
}

export interface ConversationVolumeData {
  hourly: TimeSeriesDataPoint[]
  daily: TimeSeriesDataPoint[]
  weekly: TimeSeriesDataPoint[]
}

export interface ResolutionRateTrend {
  date: string
  resolved: number
  unresolved: number
  rate: number
}

export interface TopicDistribution {
  topic: string
  count: number
  percentage: number
}

export interface SentimentBreakdown {
  positive: number
  neutral: number
  negative: number
}

export interface AgentPerformanceComparison {
  agent_id: string
  agent_name: string
  total_conversations: number
  avg_duration_seconds: number
  avg_sentiment_score: number
  resolution_rate: number
  uptime_percentage: number
}

// ==================== Analytics Query Parameters ====================

export interface AnalyticsQueryParams {
  agent_id?: string
  start_date?: string
  end_date?: string
  channel?: string
  metric_type?: string
  event_type?: string
  category?: string
  status_filter?: string
  limit?: number
  offset?: number
}

// ==================== CSV Export Types ====================

export interface CSVExportParams {
  data_type: 'insights' | 'conversations' | 'metrics' | 'recommendations'
  filters?: AnalyticsQueryParams
  columns?: string[]
}

export interface CSVExportResponse {
  filename: string
  csv_content: string
  row_count: number
  generated_at: string
}
