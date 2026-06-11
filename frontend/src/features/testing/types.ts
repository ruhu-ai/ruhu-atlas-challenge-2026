/**
 * Testing & QA Types
 *
 * Type definitions for test scenarios, test runs, and test results
 */

export interface TestScenario {
  id: string
  name: string
  description: string
  category: 'functional' | 'performance' | 'conversation' | 'edge-case'
  agentId?: string
  steps: TestStep[]
  expectedOutcomes: ExpectedOutcome[]
  createdAt: Date
  updatedAt: Date
  createdBy: string
  tags: string[]
}

export interface TestStep {
  id: string
  order: number
  type: 'user-message' | 'wait' | 'assert' | 'action'
  content: string
  metadata?: Record<string, unknown>
}

export interface ExpectedOutcome {
  id: string
  description: string
  type: 'contains' | 'equals' | 'matches' | 'duration' | 'custom'
  target: string
  value: any
}

export interface TestRun {
  id: string
  scenarioId: string
  agentId: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  startedAt?: Date
  completedAt?: Date
  duration?: number
  results: TestResult[]
  metadata: TestRunMetadata
}

export interface TestResult {
  id: string
  stepId: string
  status: 'passed' | 'failed' | 'skipped'
  message?: string
  actualValue?: any
  expectedValue?: any
  timestamp: Date
  duration?: number
}

export interface TestRunMetadata {
  totalSteps: number
  passedSteps: number
  failedSteps: number
  skippedSteps: number
  averageResponseTime?: number
  transcriptUrl?: string
  audioUrl?: string
}

export interface TestSuite {
  id: string
  name: string
  description: string
  scenarios: string[] // scenario IDs
  createdAt: Date
  updatedAt: Date
}

export interface ScenarioTemplate {
  id: string
  name: string
  description: string
  category: string
  template: Partial<TestScenario>
  isPublic: boolean
}
