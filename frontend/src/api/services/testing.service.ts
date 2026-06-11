/**
 * Testing Service
 *
 * Handles all testing-related API calls for test cases and test runs.
 */

import { apiClient } from '../client'

// Test Case Types
export interface TestCase {
  id: string
  organization_id: string
  agent_id: string
  created_by?: string
  name: string
  description?: string
  test_type: string
  category: string
  tags: string[]
  test_scenario: Record<string, unknown>
  input_messages: Array<Record<string, unknown>>
  expected_outputs: Array<Record<string, unknown>>
  success_criteria: Record<string, unknown>
  validation_rules: Array<Record<string, unknown>>
  priority: string
  is_active: boolean
  is_automated: boolean
  total_runs: number
  successful_runs: number
  failed_runs: number
  last_run_at?: string
  created_at: string
  updated_at: string
}

export interface CreateTestCaseRequest {
  agent_id: string
  name: string
  description?: string
  test_type: string
  category: string
  tags?: string[]
  test_scenario?: Record<string, unknown>
  input_messages?: Array<Record<string, unknown>>
  expected_outputs?: Array<Record<string, unknown>>
  success_criteria?: Record<string, unknown>
  validation_rules?: Array<Record<string, unknown>>
  priority?: string
  is_automated?: boolean
}

export interface UpdateTestCaseRequest {
  name?: string
  description?: string
  tags?: string[]
  test_scenario?: Record<string, unknown>
  input_messages?: Array<Record<string, unknown>>
  expected_outputs?: Array<Record<string, unknown>>
  success_criteria?: Record<string, unknown>
  validation_rules?: Array<Record<string, unknown>>
  priority?: string
  is_active?: boolean
  is_automated?: boolean
}

// Test Run Types
export interface TestRun {
  id: string
  organization_id: string
  agent_id: string
  test_case_id?: string
  triggered_by?: string
  run_name: string
  run_type: string
  agent_version?: string
  canvas_version_id?: string
  test_config: Record<string, unknown>
  started_at: string
  completed_at?: string
  duration_ms?: number
  status: string
  total_test_cases: number
  passed_count: number
  failed_count: number
  skipped_count: number
  pass_rate?: number // 0..1 ratio (backend canonical storage)
  environment: string
  error_message?: string
  error_details: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface CreateTestRunRequest {
  agent_id: string
  test_case_id?: string
  run_name: string
  run_type: string
  agent_version?: string
  canvas_version_id?: string
  test_config?: Record<string, unknown>
  environment?: string
}

// ==================== Evaluation Dashboard Types ====================

export interface AgentSimulationStats {
  agent_id: string
  agent_name: string
  total_simulations: number
  simulations_passed: number
  simulations_failed: number
  pass_rate: number // 0-100 percentage
  last_run_at?: string
  avg_response_time_ms?: number
  total_test_cases: number
  active_test_cases: number
}

export interface TestCaseSimulationStats {
  test_case_id: string
  test_case_name: string
  status: 'passed' | 'failed' | 'not_run'
  total_runs: number
  successful_runs: number
  failed_runs: number
  pass_rate: number // 0-100 percentage
  last_run_at?: string
  avg_duration_ms?: number
}

export interface SimulationDashboard {
  agent_stats: AgentSimulationStats
  test_cases: TestCaseSimulationStats[]
  recent_runs: TestRun[]
}

export interface BatchTestExecutionRequest {
  agent_id: string
  canvas_version_id?: string
  test_case_ids?: string[]
  run_name: string
  environment?: string
  parallel?: boolean
  max_parallel?: number
}

export interface BatchTestExecutionResponse {
  test_run_id: string
  status: string
  total_test_cases: number
  started_at: string
  message: string
}

export interface TestRunProgress {
  test_run_id: string
  status: string
  total_count: number
  completed_count: number
  passed_count: number
  failed_count: number
  elapsed_ms: number
}

export interface LatestQualifiedRunSummary {
  id: string
  run_name?: string
  canvas_version_id?: string
  completed_at: string
  pass_rate: number
  pass_rate_percent: number
  total_test_cases: number
  passed_count: number
  failed_count: number
}

export interface LatestQualifiedRunResponse {
  agent_id: string
  canvas_version_id?: string
  gate_enabled: boolean
  qualified: boolean
  latest_run?: LatestQualifiedRunSummary | null
  latest_qualified_run?: LatestQualifiedRunSummary | null
  blocking_reasons: string[]
  checks: Array<Record<string, unknown>>
  evaluated_at: string
}

class TestingService {
  // ==================== Test Cases ====================

  /**
   * Get all test cases
   */
  async getTestCases(params?: {
    agent_id?: string
    status_filter?: string
    skip?: number
    limit?: number
  }): Promise<TestCase[]> {
    // Filter out undefined values to avoid sending "undefined" as a string
    const cleanParams: Record<string, string> = {}
    if (params) {
      Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          cleanParams[key] = String(value)
        }
      })
    }

    const response = await apiClient.get<TestCase[]>('/testing/cases', {
      params: cleanParams,
    })
    return Array.isArray(response) ? response : []
  }

  /**
   * Get a single test case by ID
   */
  async getTestCase(id: string): Promise<TestCase> {
    const response = await apiClient.get<TestCase>(`/testing/cases/${id}`)
    return response
  }

  /**
   * Create a new test case
   */
  async createTestCase(data: CreateTestCaseRequest): Promise<TestCase> {
    const response = await apiClient.post<TestCase>('/testing/cases', data)
    return response
  }

  /**
   * Update an existing test case
   */
  async updateTestCase(id: string, data: UpdateTestCaseRequest): Promise<TestCase> {
    const response = await apiClient.patch<TestCase>(
      `/testing/cases/${id}`,
      data
    )
    return response
  }

  /**
   * Delete a test case
   */
  async deleteTestCase(id: string): Promise<void> {
    await apiClient.delete(`/testing/cases/${id}`)
  }

  /**
   * Duplicate a test case
   */
  async duplicateTestCase(id: string, name: string): Promise<TestCase> {
    // Get the original test case
    const originalTestCase = await this.getTestCase(id)

    // Create a new test case with the same data but different name
    const duplicateData: CreateTestCaseRequest = {
      agent_id: originalTestCase.agent_id,
      name: name,
      description: originalTestCase.description,
      test_type: originalTestCase.test_type,
      category: originalTestCase.category,
      tags: originalTestCase.tags,
      test_scenario: originalTestCase.test_scenario,
      input_messages: originalTestCase.input_messages,
      expected_outputs: originalTestCase.expected_outputs,
      success_criteria: originalTestCase.success_criteria,
      validation_rules: originalTestCase.validation_rules,
      priority: originalTestCase.priority,
      is_automated: originalTestCase.is_automated,
    }

    return await this.createTestCase(duplicateData)
  }

  // ==================== Test Runs ====================

  /**
   * Get all test runs
   */
  async getTestRuns(params?: {
    test_case_id?: string
    status_filter?: string
    skip?: number
    limit?: number
  }): Promise<TestRun[]> {
    // Filter out undefined values to avoid sending "undefined" as a string
    const cleanParams: Record<string, string> = {}
    if (params) {
      Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          cleanParams[key] = String(value)
        }
      })
    }

    const response = await apiClient.get<TestRun[]>('/testing/runs', {
      params: cleanParams,
    })
    return Array.isArray(response) ? response : []
  }

  /**
   * Get a single test run by ID
   */
  async getTestRun(id: string): Promise<TestRun> {
    const response = await apiClient.get<TestRun>(`/testing/runs/${id}`)
    return response
  }

  /**
   * Create and start a new test run
   */
  async createTestRun(data: CreateTestRunRequest): Promise<TestRun> {
    const response = await apiClient.post<TestRun>('/testing/runs', data)
    return response
  }

  /**
   * Execute a test run
   */
  async executeTestRun(id: string): Promise<TestRun> {
    const response = await apiClient.post<TestRun>(
      `/testing/runs/${id}/execute`,
      {}
    )
    return response
  }

  /**
   * Stop a running test
   */
  async stopTestRun(id: string): Promise<TestRun> {
    const response = await apiClient.post<TestRun>(
      `/testing/runs/${id}/stop`,
      {}
    )
    return response
  }

  /**
   * Run a test case (create and execute)
   */
  async runTestCase(testCaseId: string): Promise<TestRun> {
    const testCase = await this.getTestCase(testCaseId)

    // Create test run
    const runData: CreateTestRunRequest = {
      agent_id: testCase.agent_id,
      test_case_id: testCaseId,
      run_name: `Run ${testCase.name} - ${new Date().toISOString()}`,
      run_type: 'manual',
      environment: 'test',
    }

    const testRun = await this.createTestRun(runData)

    // Execute it
    return await this.executeTestRun(testRun.id)
  }

  // ==================== Evaluation Dashboard ====================

  /**
   * Get aggregated simulation stats for an agent
   */
  async getAgentSimulationStats(agentId: string): Promise<AgentSimulationStats> {
    const response = await apiClient.get<AgentSimulationStats>(
      `/testing/agents/${agentId}/simulation-stats`
    )
    return response
  }

  /**
   * Get complete simulation dashboard data for an agent
   */
  async getSimulationDashboard(agentId: string): Promise<SimulationDashboard> {
    const response = await apiClient.get<SimulationDashboard>(
      `/testing/agents/${agentId}/simulation-dashboard`
    )
    // Guard against HTML fallback or missing fields when endpoint doesn't exist
    if (!response || typeof response !== 'object' || !response.agent_stats) {
      return {
        agent_stats: {
          agent_id: agentId,
          agent_name: agentId,
          total_simulations: 0,
          simulations_passed: 0,
          simulations_failed: 0,
          pass_rate: 0,
          total_test_cases: 0,
          active_test_cases: 0,
        },
        test_cases: [],
        recent_runs: [],
      }
    }
    return {
      ...response,
      test_cases: Array.isArray(response.test_cases) ? response.test_cases : [],
      recent_runs: Array.isArray(response.recent_runs) ? response.recent_runs : [],
    }
  }

  /**
   * Get latest qualification status for simulation gates on an agent/canvas version.
   */
  async getLatestQualifiedRun(
    agentId: string,
    canvasVersionId?: string
  ): Promise<LatestQualifiedRunResponse> {
    const params = canvasVersionId ? { canvas_version_id: canvasVersionId } : undefined
    const response = await apiClient.get<LatestQualifiedRunResponse>(
      `/testing/agents/${agentId}/latest-qualified-run`,
      { params }
    )
    if (!response || typeof response !== 'object' || !('qualified' in response)) {
      return {
        agent_id: agentId,
        gate_enabled: false,
        qualified: false,
        blocking_reasons: [],
        checks: [],
        evaluated_at: new Date().toISOString(),
      }
    }
    return {
      ...response,
      blocking_reasons: Array.isArray(response.blocking_reasons) ? response.blocking_reasons : [],
      checks: Array.isArray(response.checks) ? response.checks : [],
    }
  }

  /**
   * Execute batch evaluations (multiple test cases at once)
   */
  async executeBatchTests(request: BatchTestExecutionRequest): Promise<BatchTestExecutionResponse> {
    const response = await apiClient.post<BatchTestExecutionResponse>(
      '/testing/runs/batch',
      request
    )
    return response
  }

  /**
   * Run all active test cases for an agent (evaluation run)
   */
  async runAllEvaluations(agentId: string, runName?: string): Promise<BatchTestExecutionResponse> {
    return this.executeBatchTests({
      agent_id: agentId,
      run_name: runName || `All Evaluations - ${new Date().toISOString()}`,
      environment: 'test',
      parallel: false,
      max_parallel: 1,
    })
  }

  /**
   * Get real-time progress for a running test
   */
  async getTestRunProgress(runId: string): Promise<TestRunProgress> {
    const response = await apiClient.get<TestRunProgress>(
      `/testing/runs/${runId}/progress`
    )
    if (!response || typeof response !== 'object' || !('status' in response)) {
      return {
        test_run_id: runId,
        status: 'unknown',
        total_count: 0,
        completed_count: 0,
        passed_count: 0,
        failed_count: 0,
        elapsed_ms: 0,
      }
    }
    return response
  }
}

export const testingService = new TestingService()
