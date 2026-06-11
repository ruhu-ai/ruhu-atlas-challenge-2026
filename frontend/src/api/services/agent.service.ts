/**
 * Agent Service
 *
 * Handles all agent-related API calls with provider abstraction.
 */

import { apiClient } from '../client'
import type { Agent } from '@/types'

export interface DeploymentGateConfig {
  deployment_gate_enabled: boolean
  min_pass_rate: number
  min_simulation_runs: number
  max_test_staleness_hours: number
}

export interface DeployReadinessCheck {
  check: string
  passed: boolean
  detail: string
  actual?: any
  expected?: any
  blocking: boolean
}

export interface RuntimeTelemetrySummary {
  lookback_days: number
  has_recent_activity: boolean
  step_transition_count: number
  conversation_summary_count: number
  avg_step_latency_ms?: number | null
  tool_error_count: number
  channels: string[]
  last_event_at?: string | null
  canvas_version_id?: string | null
}

export interface DeployReadinessResponse {
  agent_id: string
  canvas_version_id?: string
  gate_enabled: boolean
  passed: boolean
  blocking_reasons: string[]
  checks: DeployReadinessCheck[]
  latest_simulation_run?: ReleaseSimulationRun
  evaluated_at: string
  gate_config: DeploymentGateConfig
  runtime_telemetry?: RuntimeTelemetrySummary | null
}

export interface ReleaseSimulationRun {
  pass_rate_percent?: number | null
  passed_count?: number | null
  failed_count?: number | null
  total_test_cases?: number | null
  [key: string]: unknown
}

export interface AgentSimulationRequest {
  test_case_ids?: string[]
  run_name?: string
  environment?: string
  parallel?: boolean
  max_parallel?: number
  canvas_version_id?: string
}

export interface AgentSimulationResponse {
  test_run_id: string
  status: string
  total_test_cases: number
  started_at: string
  message: string
}

export type ReleaseStrategy = 'immediate' | 'canary' | 'progressive'

export interface AgentRelease {
  id: string
  organization_id: string
  agent_id: string
  canvas_version_id: string
  strategy: ReleaseStrategy
  status: string
  traffic_percent: number
  created_by?: string | null
  metadata_json: Record<string, unknown>
  created_at: string
  promoted_at?: string | null
  rolled_back_at?: string | null
}

export interface CreateReleaseRequest {
  canvas_version_id: string
  strategy: ReleaseStrategy
  initial_traffic_percent?: number
  metadata_json?: Record<string, unknown>
}

export interface PromoteReleaseRequest {
  target_traffic_percent?: number
  reason?: string
}

export interface RollbackReleaseRequest {
  reason?: string
}

export interface ReleaseHealthResponse {
  release_id: string
  agent_id: string
  canvas_version_id: string
  status: string
  traffic_percent: number
  checks: Record<string, unknown>
  latest_simulation_run?: ReleaseSimulationRun | null
  updated_at: string
  health_score?: number | null
  risk_level?: string | null
  rollback_recommended?: boolean
  held_reason?: string | null
}

export interface ReleaseTimelineReleaseSnapshot {
  id: string
  canvas_version_id: string
  strategy: ReleaseStrategy
  status: string
  traffic_percent: number
}

export interface ReleaseTimelineActor {
  id: string
  email?: string | null
  display_name?: string | null
}

export interface AgentReleaseTimelineEvent {
  id: string
  organization_id: string
  release_id: string
  event_type: string
  actor_id?: string | null
  actor?: ReleaseTimelineActor | null
  metadata_json: Record<string, unknown>
  created_at: string
  release: ReleaseTimelineReleaseSnapshot
}

export interface AgentReleaseTimelineResponse {
  agent_id: string
  total_events: number
  limit: number
  offset: number
  has_more: boolean
  events: AgentReleaseTimelineEvent[]
}

export interface ReleaseTimelineActorOption {
  id: string
  email?: string | null
  display_name?: string | null
}

export interface AgentReleaseTimelineActorsResponse {
  agent_id: string
  total_actors: number
  limit: number
  offset: number
  has_more: boolean
  actors: ReleaseTimelineActorOption[]
}

export interface GetReleaseTimelineParams {
  limit?: number
  offset?: number
  event_types?: string[]
  release_id?: string
  actor_id?: string
}

export interface GetReleaseTimelineActorsParams {
  limit?: number
  offset?: number
  search?: string
}

export interface AgentInsightRecommendation {
  recommendation_id: string
  insight_id?: string | null
  agent_id: string
  insight_title?: string | null
  insight_severity?: string | null
  scenario_ids?: string[]
  test_case_ids?: string[]
  target_node_type?: string | null
  title: string
  description: string
  action_type: string
  priority: number
  status: string
  implementation_steps: Array<Record<string, unknown>>
  expected_impact: Record<string, unknown>
  suggested_config_changes: Record<string, unknown>
  source: string
  created_at?: string | null
}

export interface AgentInsightRecommendationsResponse {
  agent_id: string
  recommendations: AgentInsightRecommendation[]
}

export interface DraftFromRecommendationResponse {
  recommendation_id: string
  action_id: string
  action_status: string
  agent_id: string
  source_canvas_version_id: string
  draft_canvas_version_id: string
  draft_version_number: number
  draft_status: string
  applied_patch_summary: Record<string, unknown>
}

class AgentService {
  /**
   * Get all agents for the current organization
   */
  async getAllAgents(_params?: { page?: number; per_page?: number }): Promise<Agent[]> {
    const response = await apiClient.get<unknown>('/agents')

    if (!Array.isArray(response)) return []

    return (response as Array<Record<string, unknown>>).map((g) => ({
      id: g.id as string,
      name: g.name as string,
      status: g.has_published_version ? 'published' : 'draft',
      description: (g.description as string) ?? '',
      agent_type: (g.agent_type as string) ?? 'voice',
    })) as Agent[]
  }

  /**
   * Get a single agent by ID
   */
  async getAgentById(id: string): Promise<Agent> {
    const response = await apiClient.get<Agent>(`/agents/${id}`)
    return response
  }

  /**
   * Create a new agent
   */
  async createAgent(data: Partial<Agent>): Promise<Agent> {
    const response = await apiClient.post<Agent>('/agents', data)
    return response
  }

  /**
   * Update an existing agent
   */
  async updateAgent(id: string, data: Partial<Agent>): Promise<Agent> {
    const response = await apiClient.patch<Agent>(`/agents/${id}`, data)
    return response
  }

  /**
   * Delete an agent
   */
  async deleteAgent(id: string): Promise<void> {
    await apiClient.delete(`/agents/${id}`)
  }

  /**
   * Publish an agent (change status to published)
   */
  async publishAgent(id: string): Promise<Agent> {
    const response = await apiClient.post<Agent>(
      `/agents/${id}/publish`,
      {}
    )
    return response
  }

  /**
   * Archive an agent
   */
  async archiveAgent(id: string): Promise<Agent> {
    const response = await apiClient.post<Agent>(
      `/agents/${id}/archive`,
      {}
    )
    return response
  }

  /**
   * Deploy an agent (change status to active/deployed)
   */
  async deployAgent(id: string): Promise<Agent> {
    const response = await apiClient.post<Agent>(
      `/agents/${id}/deploy`,
      {}
    )
    return response
  }

  /**
   * Evaluate whether an agent can be deployed (includes quality gates).
   */
  async getDeployReadiness(
    id: string,
    canvasVersionId?: string
  ): Promise<DeployReadinessResponse> {
    const response = await apiClient.get<DeployReadinessResponse>(
      `/agents/${id}/deploy-readiness`,
      {
        params: canvasVersionId ? { canvas_version_id: canvasVersionId } : undefined,
      }
    )
    return response
  }

  /**
   * Update deployment quality gate settings for an agent.
   */
  async updateDeploymentGates(
    id: string,
    data: Partial<DeploymentGateConfig>
  ): Promise<DeploymentGateConfig> {
    const response = await apiClient.put<DeploymentGateConfig>(
      `/agents/${id}/deployment-gates`,
      data
    )
    return response
  }

  /**
   * Start a simulation run for an agent.
   */
  async simulateAgent(
    id: string,
    data: AgentSimulationRequest
  ): Promise<AgentSimulationResponse> {
    const response = await apiClient.post<AgentSimulationResponse>(
      `/agents/${id}/simulate`,
      data
    )
    return response
  }

  /**
   * Create a release for an agent.
   */
  async createRelease(id: string, data: CreateReleaseRequest): Promise<AgentRelease> {
    const response = await apiClient.post<AgentRelease>(`/agents/${id}/releases`, data)
    return response
  }

  /**
   * List release history for an agent.
   */
  async listReleases(id: string): Promise<AgentRelease[]> {
    const response = await apiClient.get<AgentRelease[]>(`/agents/${id}/releases`)
    return response
  }

  /**
   * Promote a release (increase traffic).
   */
  async promoteRelease(
    id: string,
    releaseId: string,
    data: PromoteReleaseRequest = {}
  ): Promise<AgentRelease> {
    const response = await apiClient.post<AgentRelease>(
      `/agents/${id}/releases/${releaseId}/promote`,
      data
    )
    return response
  }

  /**
   * Roll back a release.
   */
  async rollbackRelease(
    id: string,
    releaseId: string,
    data: RollbackReleaseRequest = {}
  ): Promise<AgentRelease> {
    const response = await apiClient.post<AgentRelease>(
      `/agents/${id}/releases/${releaseId}/rollback`,
      data
    )
    return response
  }

  /**
   * Get release health summary.
   */
  async getReleaseHealth(id: string, releaseId: string): Promise<ReleaseHealthResponse> {
    const response = await apiClient.get<ReleaseHealthResponse>(
      `/agents/${id}/releases/${releaseId}/health`
    )
    return response
  }

  /**
   * Get event-level release timeline for an agent.
   */
  async getReleaseTimeline(
    id: string,
    params: GetReleaseTimelineParams = {}
  ): Promise<AgentReleaseTimelineResponse> {
    const queryParams: Record<string, string | number> = {}
    if (params.limit !== undefined) queryParams.limit = params.limit
    if (params.offset !== undefined) queryParams.offset = params.offset
    if (params.release_id) queryParams.release_id = params.release_id
    if (params.actor_id) queryParams.actor_id = params.actor_id
    if (params.event_types && params.event_types.length > 0) {
      queryParams.event_types = params.event_types.join(',')
    }

    const response = await apiClient.get<AgentReleaseTimelineResponse>(
      `/agents/${id}/releases/timeline`,
      { params: queryParams }
    )
    return response
  }

  /**
   * Get actor options for release timeline filtering.
   */
  async getReleaseTimelineActors(
    id: string,
    params: GetReleaseTimelineActorsParams = {}
  ): Promise<AgentReleaseTimelineActorsResponse> {
    const queryParams: Record<string, string | number> = {}
    if (params.limit !== undefined) queryParams.limit = params.limit
    if (params.offset !== undefined) queryParams.offset = params.offset
    if (params.search) queryParams.search = params.search

    const response = await apiClient.get<AgentReleaseTimelineActorsResponse>(
      `/agents/${id}/releases/timeline/actors`,
      { params: queryParams }
    )
    return response
  }

  /**
   * List insight recommendations for an agent (Phase 3).
   */
  async getInsightRecommendations(id: string): Promise<AgentInsightRecommendationsResponse> {
    const response = await apiClient.get<AgentInsightRecommendationsResponse>(
      `/agents/${id}/insights/recommendations`
    )
    return response
  }

  /**
   * Create a draft canvas version from recommendation (Phase 3).
   */
  async createDraftFromRecommendation(
    id: string,
    recommendationId: string
  ): Promise<DraftFromRecommendationResponse> {
    const response = await apiClient.post<DraftFromRecommendationResponse>(
      `/agents/${id}/canvas/recommendations/${recommendationId}/create-draft`,
      {}
    )
    return response
  }
}

export const agentService = new AgentService()
