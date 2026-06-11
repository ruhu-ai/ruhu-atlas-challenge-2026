/**
 * Analytics Service
 *
 * API client for analytics, performance metrics, and conversation data.
 */

import { apiClient } from '../client'
import type {
  PerformanceMetric,
  PerformanceMetricCreate,
  AnalyticsEvent,
  AnalyticsEventCreate,
  Conversation,
  ConversationCreate,
  ConversationUpdate,
  VoiceSession,
  DashboardStats,
  HomeDashboardStats,
  ConversationVolumeData,
  ResolutionRateTrend,
  TopicDistribution,
  SentimentBreakdown,
  AgentPerformanceComparison,
  AnalyticsQueryParams,
} from '@/types/analytics'

const INSIGHTS_PATH = '/insights'
const CONVERSATIONS_PATH = '/conversations'
const VOICE_SESSIONS_PATH = '/voice-sessions'
const DASHBOARD_PATH = '/dashboard'

// Simple TTL cache to avoid redundant listConversations calls within a page load
let _conversationCache: { data: Conversation[]; ts: number; key: string } | null = null
const CACHE_TTL_MS = 5_000

function getCachedConversations(key: string): Conversation[] | null {
  if (_conversationCache && _conversationCache.key === key && Date.now() - _conversationCache.ts < CACHE_TTL_MS) {
    return _conversationCache.data
  }
  return null
}

function setCachedConversations(key: string, data: Conversation[]): void {
  _conversationCache = { data, ts: Date.now(), key }
}

export const analyticsService = {
  // ==================== Performance Metrics ====================

  /**
   * List performance metrics
   */
  async listPerformanceMetrics(params?: AnalyticsQueryParams): Promise<PerformanceMetric[]> {
    const searchParams = new URLSearchParams()

    if (params?.agent_id) searchParams.append('agent_id', params.agent_id)
    if (params?.metric_type) searchParams.append('metric_type', params.metric_type)
    if (params?.offset !== undefined) searchParams.append('skip', String(params.offset))
    if (params?.limit !== undefined) searchParams.append('limit', String(params.limit))

    const queryString = searchParams.toString()
    const url = queryString ? `${INSIGHTS_PATH}/metrics?${queryString}` : `${INSIGHTS_PATH}/metrics`

    const response = await apiClient.get<PerformanceMetric[]>(url)
    return response
  },

  /**
   * Create a performance metric
   */
  async createPerformanceMetric(data: PerformanceMetricCreate): Promise<PerformanceMetric> {
    const response = await apiClient.post<PerformanceMetric>(`${INSIGHTS_PATH}/metrics`, data)
    return response
  },

  // ==================== Analytics Events ====================

  /**
   * List analytics events
   */
  async listAnalyticsEvents(params?: AnalyticsQueryParams): Promise<AnalyticsEvent[]> {
    const searchParams = new URLSearchParams()

    if (params?.agent_id) searchParams.append('agent_id', params.agent_id)
    if (params?.event_type) searchParams.append('event_type', params.event_type)
    if (params?.offset !== undefined) searchParams.append('skip', String(params.offset))
    if (params?.limit !== undefined) searchParams.append('limit', String(params.limit))

    const queryString = searchParams.toString()
    const url = queryString ? `${INSIGHTS_PATH}/events?${queryString}` : `${INSIGHTS_PATH}/events`

    const response = await apiClient.get<AnalyticsEvent[]>(url)
    return response
  },

  /**
   * Create an analytics event
   */
  async createAnalyticsEvent(data: AnalyticsEventCreate): Promise<AnalyticsEvent> {
    const response = await apiClient.post<AnalyticsEvent>(`${INSIGHTS_PATH}/events`, data)
    return response
  },

  // ==================== Conversations ====================

  /**
   * List conversations with optional filters
   */
  async listConversations(params?: {
    agent_id?: string
    channel?: string
    outcome?: string
    days?: number
    sort_by?: 'started_at' | 'duration_seconds' | 'sentiment_score' | 'outcome' | 'message_count'
    sort_dir?: 'asc' | 'desc'
    offset?: number
    limit?: number
  }): Promise<Conversation[]> {
    const searchParams = new URLSearchParams()

    if (params?.agent_id) searchParams.append('agent_id', params.agent_id)
    if (params?.channel) searchParams.append('channel', params.channel)
    if (params?.outcome) searchParams.append('outcome', params.outcome)
    if (params?.days) searchParams.append('days', String(params.days))
    if (params?.sort_by) searchParams.append('sort_by', params.sort_by)
    if (params?.sort_dir) searchParams.append('sort_dir', params.sort_dir)
    if (params?.offset !== undefined) searchParams.append('skip', String(params.offset))
    if (params?.limit !== undefined) searchParams.append('limit', String(params.limit))

    const queryString = searchParams.toString()
    const cacheKey = queryString || '__all__'

    // Return cached data if available (avoids redundant fetches within a page load)
    const cached = getCachedConversations(cacheKey)
    if (cached) return cached

    const url = queryString ? `${CONVERSATIONS_PATH}?${queryString}` : CONVERSATIONS_PATH
    const response = await apiClient.get<Conversation[]>(url)
    setCachedConversations(cacheKey, response)
    return response
  },

  /**
   * Get a specific conversation
   */
  async getConversation(conversationId: string): Promise<Conversation> {
    const response = await apiClient.get<Conversation>(
      `${CONVERSATIONS_PATH}/${conversationId}`
    )
    return response
  },

  /**
   * Create a new conversation
   */
  async createConversation(data: ConversationCreate): Promise<Conversation> {
    const response = await apiClient.post<Conversation>(CONVERSATIONS_PATH, data)
    return response
  },

  /**
   * Update a conversation
   */
  async updateConversation(
    conversationId: string,
    updates: ConversationUpdate
  ): Promise<Conversation> {
    const response = await apiClient.patch<Conversation>(
      `${CONVERSATIONS_PATH}/${conversationId}`,
      updates
    )
    return response
  },

  /**
   * End a conversation
   */
  async endConversation(conversationId: string): Promise<Conversation> {
    const response = await apiClient.post<Conversation>(
      `${CONVERSATIONS_PATH}/${conversationId}/end`
    )
    return response
  },

  // ==================== Voice Sessions ====================

  /**
   * List voice sessions
   */
  async listVoiceSessions(params?: {
    status_filter?: string
    limit?: number
    offset?: number
  }): Promise<VoiceSession[]> {
    const searchParams = new URLSearchParams()

    if (params?.status_filter) searchParams.append('status_filter', params.status_filter)
    if (params?.limit !== undefined) searchParams.append('limit', String(params.limit))
    if (params?.offset !== undefined) searchParams.append('offset', String(params.offset))

    const queryString = searchParams.toString()
    const url = queryString ? `${VOICE_SESSIONS_PATH}?${queryString}` : VOICE_SESSIONS_PATH

    const response = await apiClient.get<VoiceSession[]>(url)
    return response
  },

  /**
   * Get active session count
   */
  async getActiveSessionCount(): Promise<{ active_sessions: number }> {
    const response = await apiClient.get<{ active_sessions: number }>(
      `${VOICE_SESSIONS_PATH}/active/count`
    )
    return response
  },

  // ==================== Home Dashboard (Server-Side Aggregation) ====================

  /**
   * Get home dashboard stats from the dedicated backend endpoint.
   * Returns KPIs, sparklines, agent performance, and resolution trend
   * all computed via SQL-level aggregation.
   */
  async getHomeDashboardStats(params?: { days?: number }): Promise<HomeDashboardStats> {
    const searchParams = new URLSearchParams()
    if (params?.days) searchParams.append('days', String(params.days))

    const queryString = searchParams.toString()
    const url = queryString ? `${DASHBOARD_PATH}/stats?${queryString}` : `${DASHBOARD_PATH}/stats`

    return apiClient.get<HomeDashboardStats>(url)
  },

  // ==================== Dashboard Analytics (Aggregated Data) ====================

  /**
   * Get dashboard statistics
   * This aggregates data from multiple sources for the dashboard overview
   */
  async getDashboardStats(_params?: {
    agent_id?: string
    start_date?: string
    end_date?: string
  }): Promise<DashboardStats> {
    // This method aggregates data from multiple endpoints
    // In production, this would ideally be a single backend endpoint

    try {
      const today = new Date().toISOString().split('T')[0]
      const activeSessionsPromise = this.getActiveSessionCount()
      const conversationsPromise = this.listConversations({ limit: 1000 })
      const voiceSessionsPromise = this.listVoiceSessions({ limit: 1000 })

      const [activeSessionsData, conversations, voiceSessions] = await Promise.all([
        activeSessionsPromise,
        conversationsPromise,
        voiceSessionsPromise,
      ])

      // Calculate today's conversations
      const todayConversations = conversations.filter(
        (c) => c.started_at.startsWith(today)
      )

      // Calculate today's voice calls
      const todayVoiceCalls = voiceSessions.filter(
        (v) => v.started_at.startsWith(today)
      )

      // Calculate sentiment distribution
      const sentimentCounts = conversations.reduce(
        (acc, c) => {
          if (c.sentiment_score === null) return acc
          if (c.sentiment_score > 0.3) acc.positive++
          else if (c.sentiment_score < -0.3) acc.negative++
          else acc.neutral++
          return acc
        },
        { positive: 0, neutral: 0, negative: 0 }
      )

      // Calculate channel distribution
      const channelCounts = conversations.reduce((acc, c) => {
        acc[c.channel] = (acc[c.channel] || 0) + 1
        return acc
      }, {} as Record<string, number>)

      // Calculate resolution rate
      const resolvedCount = conversations.filter(
        (c) => c.outcome === 'resolved'
      ).length
      const avgResolutionRate =
        conversations.length > 0 ? (resolvedCount / conversations.length) * 100 : 0

      // Calculate average duration for response time approximation
      const durations = conversations
        .filter((c) => c.duration_seconds !== null)
        .map((c) => c.duration_seconds!)
      const avgDuration =
        durations.length > 0
          ? durations.reduce((a, b) => a + b, 0) / durations.length
          : 0

      return {
        active_conversations: todayConversations.filter((c) => c.status === 'active').length,
        active_voice_sessions: activeSessionsData.active_sessions,
        total_conversations_today: todayConversations.length,
        total_voice_calls_today: todayVoiceCalls.length,
        avg_response_time_ms: avgDuration * 1000, // Convert to ms
        avg_resolution_rate: avgResolutionRate,
        sentiment_positive: sentimentCounts.positive,
        sentiment_neutral: sentimentCounts.neutral,
        sentiment_negative: sentimentCounts.negative,
        conversations_by_channel: channelCounts,
        uptime_percentage: 99.5, // Would come from health monitoring
        error_rate: 0.5, // Would come from error tracking
        avg_latency_ms: 250, // Would come from performance monitoring
      }
    } catch (error) {
      console.error('Failed to fetch dashboard stats:', error)
      throw error
    }
  },

  /**
   * Get conversation volume time series data
   */
  async getConversationVolume(params?: {
    agent_id?: string
    start_date?: string
    end_date?: string
  }): Promise<ConversationVolumeData> {
    const conversations = await this.listConversations({
      agent_id: params?.agent_id,
      limit: 1000,
    })

    // Group by hour, day, week
    const hourly = this.groupConversationsByHour(conversations)
    const daily = this.groupConversationsByDay(conversations)
    const weekly = this.groupConversationsByWeek(conversations)

    return { hourly, daily, weekly }
  },

  /**
   * Get resolution rate trend
   */
  async getResolutionRateTrend(params?: {
    agent_id?: string
    days?: number
  }): Promise<ResolutionRateTrend[]> {
    const conversations = await this.listConversations({
      agent_id: params?.agent_id,
      limit: 1000,
    })

    return this.calculateResolutionRateTrend(conversations, params?.days || 7)
  },

  /**
   * Get topic distribution
   */
  async getTopicDistribution(params?: {
    agent_id?: string
    limit?: number
  }): Promise<TopicDistribution[]> {
    const conversations = await this.listConversations({
      agent_id: params?.agent_id,
      limit: 1000,
    })

    const topicCounts = conversations.reduce((acc, c) => {
      if (c.topic_category) {
        acc[c.topic_category] = (acc[c.topic_category] || 0) + 1
      }
      return acc
    }, {} as Record<string, number>)

    const total = conversations.length
    const topics = Object.entries(topicCounts)
      .map(([topic, count]) => ({
        topic,
        count,
        percentage: (count / total) * 100,
      }))
      .sort((a, b) => b.count - a.count)
      .slice(0, params?.limit || 10)

    return topics
  },

  /**
   * Get sentiment breakdown
   */
  async getSentimentBreakdown(params?: {
    agent_id?: string
  }): Promise<SentimentBreakdown> {
    const conversations = await this.listConversations({
      agent_id: params?.agent_id,
      limit: 1000,
    })

    const counts = conversations.reduce(
      (acc, c) => {
        if (c.sentiment_score === null) return acc
        if (c.sentiment_score > 0.3) acc.positive++
        else if (c.sentiment_score < -0.3) acc.negative++
        else acc.neutral++
        return acc
      },
      { positive: 0, neutral: 0, negative: 0 }
    )

    return counts
  },

  /**
   * Get agent performance comparison
   */
  async getAgentPerformanceComparison(): Promise<AgentPerformanceComparison[]> {
    const conversations = await this.listConversations({ limit: 1000 })

    // Group by agent
    type AgentAggregate = {
      agent_id: string
      agent_name: string
      conversations: Conversation[]
      durations: number[]
      sentiments: number[]
      resolved: number
    }

    const agentData = conversations.reduce<Record<string, AgentAggregate>>((acc, c) => {
      if (!acc[c.agent_id]) {
        acc[c.agent_id] = {
          agent_id: c.agent_id,
          agent_name: `Agent ${c.agent_id.slice(0, 8)}`,
          conversations: [],
          durations: [],
          sentiments: [],
          resolved: 0,
        }
      }

      acc[c.agent_id].conversations.push(c)
      if (c.duration_seconds) acc[c.agent_id].durations.push(c.duration_seconds)
      if (c.sentiment_score !== null) acc[c.agent_id].sentiments.push(c.sentiment_score)
      if (c.outcome === 'resolved') acc[c.agent_id].resolved++

      return acc
    }, {})

    // Calculate performance metrics
    return Object.values(agentData).map((data) => ({
      agent_id: data.agent_id,
      agent_name: data.agent_name,
      total_conversations: data.conversations.length,
      avg_duration_seconds:
        data.durations.length > 0
          ? data.durations.reduce((a: number, b: number) => a + b, 0) / data.durations.length
          : 0,
      avg_sentiment_score:
        data.sentiments.length > 0
          ? data.sentiments.reduce((a: number, b: number) => a + b, 0) / data.sentiments.length
          : 0,
      resolution_rate:
        data.conversations.length > 0 ? (data.resolved / data.conversations.length) * 100 : 0,
      uptime_percentage: 99.5, // Would come from monitoring
    }))
  },

  // ==================== Helper Methods ====================

  groupConversationsByHour(conversations: Conversation[]) {
    const groups: Record<string, number> = {}

    conversations.forEach((c) => {
      const hour = c.started_at.substring(0, 13) // YYYY-MM-DDTHH
      groups[hour] = (groups[hour] || 0) + 1
    })

    return Object.entries(groups)
      .map(([timestamp, value]) => ({ timestamp: timestamp + ':00:00', value }))
      .sort((a, b) => a.timestamp.localeCompare(b.timestamp))
      .slice(-24) // Last 24 hours
  },

  groupConversationsByDay(conversations: Conversation[]) {
    const groups: Record<string, number> = {}

    conversations.forEach((c) => {
      const day = c.started_at.substring(0, 10) // YYYY-MM-DD
      groups[day] = (groups[day] || 0) + 1
    })

    return Object.entries(groups)
      .map(([timestamp, value]) => ({ timestamp: timestamp + 'T00:00:00', value }))
      .sort((a, b) => a.timestamp.localeCompare(b.timestamp))
      .slice(-30) // Last 30 days
  },

  groupConversationsByWeek(conversations: Conversation[]) {
    const groups: Record<string, number> = {}

    conversations.forEach((c) => {
      const date = new Date(c.started_at)
      const weekStart = new Date(date)
      weekStart.setDate(date.getDate() - date.getDay())
      const week = weekStart.toISOString().substring(0, 10)
      groups[week] = (groups[week] || 0) + 1
    })

    return Object.entries(groups)
      .map(([timestamp, value]) => ({ timestamp: timestamp + 'T00:00:00', value }))
      .sort((a, b) => a.timestamp.localeCompare(b.timestamp))
      .slice(-12) // Last 12 weeks
  },

  calculateResolutionRateTrend(conversations: Conversation[], days: number) {
    const now = new Date()
    const trends: ResolutionRateTrend[] = []

    for (let i = days - 1; i >= 0; i--) {
      const date = new Date(now)
      date.setDate(date.getDate() - i)
      const dateStr = date.toISOString().substring(0, 10)

      const dayConversations = conversations.filter(
        (c) => c.started_at.substring(0, 10) === dateStr
      )

      const resolved = dayConversations.filter((c) => c.outcome === 'resolved').length
      const unresolved = dayConversations.length - resolved
      const rate = dayConversations.length > 0 ? (resolved / dayConversations.length) * 100 : 0

      trends.push({ date: dateStr, resolved, unresolved, rate })
    }

    return trends
  },

  // ==================== CSV Export ====================

  /**
   * Export data to CSV format
   */
  async exportToCSV(dataType: string, filters?: AnalyticsQueryParams): Promise<string> {
    let data: unknown[] = []
    let headers: string[] = []

    switch (dataType) {
      case 'conversations':
        data = await this.listConversations(filters)
        headers = [
          'ID',
          'Agent ID',
          'Channel',
          'Status',
          'Sentiment',
          'Resolution',
          'Duration (s)',
          'Started At',
        ]
        break
      case 'metrics':
        data = await this.listPerformanceMetrics(filters)
        headers = ['ID', 'Metric Type', 'Metric Name', 'Value', 'Unit', 'Timestamp']
        break
      default:
        throw new Error(`Unknown data type: ${dataType}`)
    }

    // Generate CSV
    const csvRows = [headers.join(',')]

    data.forEach((item) => {
      const rowSource = item as Record<string, unknown>
      const row = headers.map((header) => {
        const key = header.toLowerCase().replace(/ /g, '_').replace(/\(.*\)/, '').trim()
        let value = rowSource[key] || ''

        // Handle special cases
        if (typeof value === 'object') {
          value = JSON.stringify(value).replace(/"/g, '""')
        }

        return `"${value}"`
      })
      csvRows.push(row.join(','))
    })

    return csvRows.join('\n')
  },
}
