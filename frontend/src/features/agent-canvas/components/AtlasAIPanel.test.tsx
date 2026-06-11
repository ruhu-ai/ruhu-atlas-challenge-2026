import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

import type {
  AtlasSessionResponse,
  AtlasTurnResponse,
  CanonicalAtlasProposedChanges,
} from '@/api/services/atlas.service'

const mockAtlasService = {
  getEnabledStatus: jest.fn(),
  setEnabledStatus: jest.fn(),
  listSessions: jest.fn(),
  startSession: jest.fn(),
  getSession: jest.fn(),
  getSessionState: jest.fn(),
  listMessages: jest.fn(),
  listEvents: jest.fn(),
  archiveSession: jest.fn(),
  subscribeToEvents: jest.fn(),
  runTurn: jest.fn(),
  createReadinessRun: jest.fn(),
  getReadinessRun: jest.fn(),
  listReadinessRuns: jest.fn(),
  getReadinessProviderHealth: jest.fn(),
  listReadinessEvents: jest.fn(),
  getReadinessReport: jest.fn(),
  proposeReadinessDeltas: jest.fn(),
  rerunReadinessRun: jest.fn(),
  cancelReadinessRun: jest.fn(),
  applyPermissionDecisions: jest.fn(),
  applyChanges: jest.fn(),
}

jest.mock('@/api/services/atlas.service', () => ({
  atlasService: mockAtlasService,
}))

jest.mock('@/api/services/auth.service', () => ({
  authService: {
    getCurrentUser: jest.fn().mockResolvedValue({ user_id: 'initial-test-user' }),
    refresh: jest.fn().mockResolvedValue({ user_id: 'initial-test-user' }),
    logout: jest.fn().mockResolvedValue(undefined),
  },
}))

jest.mock('@/api/client', () => ({
  ApiError: class ApiError extends Error {
    constructor(message: string) {
      super(message)
    }
  },
  cancelAllRequests: jest.fn(),
}))

jest.mock('@/lib/query-client', () => ({
  queryClient: { clear: jest.fn() },
}))

import { useAuthStore } from '@/store/auth.store'
import { AtlasAIPanel } from './AtlasAIPanel'

const now = '2026-06-04T12:00:00.000Z'

function emptyProposedChanges(): CanonicalAtlasProposedChanges {
  return {
    agent_metadata_deltas: [],
    scenario_deltas: [],
    step_deltas: [],
    scenario_route_deltas: [],
    channel_policy_deltas: [],
    rule_deltas: [],
    knowledge_deltas: [],
    integration_binding_deltas: [],
  }
}

function session(createdBy: string | null): AtlasSessionResponse {
  return {
    session_id: 'atlas_session_test',
    status: 'active',
    scope: 'agent_authoring',
    agent_id: 'sales',
    agent_version_id: null,
    created_by: createdBy,
    scenario_id: null,
    step_id: null,
    created_at: now,
    updated_at: now,
  }
}

function baseTurnResponse(overrides: Partial<AtlasTurnResponse> = {}): AtlasTurnResponse {
  return {
    session_id: 'atlas_session_test',
    message: 'Atlas response',
    next_action: 'complete',
    generator: {},
    tool_calls: [],
    questions: [],
    dependencies: [],
    blockers: [],
    proposed_changes: emptyProposedChanges(),
    derived_impact: {},
    validation: { errors: [], warnings: [] },
    provisioning_manifest: [],
    api_discovery_results: [],
    attachment_ingestion_results: [],
    references: {},
    review_state: {
      approved_delta_ids: [],
      rejected_delta_ids: [],
      pending_delta_ids: [],
    },
    pending_permission_requests: [],
    ...overrides,
  }
}

function permissionTurnResponse(): AtlasTurnResponse {
  const proposedChanges = emptyProposedChanges()
  proposedChanges.step_deltas = [
    {
      delta_id: 'delta_step_1',
      operation: 'update',
      change_type: 'step',
      summary: 'Update the start step.',
      payload: {},
      status: 'approved',
    },
  ]
  return baseTurnResponse({
    proposed_changes: proposedChanges,
    review_state: {
      approved_delta_ids: ['delta_step_1'],
      rejected_delta_ids: [],
      pending_delta_ids: [],
    },
    pending_permission_requests: [
      {
        request_id: 'permission_1',
        kind: 'apply_deltas',
        status: 'pending',
        reason: 'Apply approved Atlas changes',
        risk_summary: 'Writes to the draft AgentDocument.',
        requested_actions: ['apply_deltas'],
        delta_ids: ['delta_step_1'],
        scope_ref: {},
        created_at: now,
        expires_at: now,
      },
    ],
  })
}

function setupAtlasMocks(createdBy: string | null = 'author-user') {
  mockAtlasService.getEnabledStatus.mockResolvedValue({ agent_id: 'sales', atlas_enabled: true })
  mockAtlasService.setEnabledStatus.mockResolvedValue({ agent_id: 'sales', atlas_enabled: true })
  mockAtlasService.listSessions.mockResolvedValue({ sessions: [], total_count: 0, has_more: false })
  mockAtlasService.startSession.mockResolvedValue(session(createdBy))
  mockAtlasService.getSession.mockResolvedValue(session(createdBy))
  mockAtlasService.getSessionState.mockResolvedValue(baseTurnResponse())
  mockAtlasService.listMessages.mockResolvedValue({
    session_id: 'atlas_session_test',
    messages: [],
    has_more: false,
    total_count: 0,
  })
  mockAtlasService.listEvents.mockResolvedValue({
    session_id: 'atlas_session_test',
    events: [],
    has_more: false,
    total_count: 0,
  })
  mockAtlasService.archiveSession.mockResolvedValue({
    session_id: 'atlas_session_test',
    status: 'archived',
    archived_at: now,
  })
  mockAtlasService.subscribeToEvents.mockResolvedValue(undefined)
  mockAtlasService.runTurn.mockResolvedValue(baseTurnResponse())
  mockAtlasService.createReadinessRun.mockResolvedValue({
    run: {
      run_id: 'readiness_1',
      agent_id: 'sales',
      agent_version_id: null,
      atlas_session_id: null,
      scope: 'validate',
      state: 'completed',
      provider_policy: 'deterministic',
      case_set_id: 'case_set_1',
      document_hash: 'hash',
      policy_hash: 'policy',
      provider_config_hash: 'provider',
      request: {},
      blocker_codes: [],
      created_at: now,
      updated_at: now,
      completed_at: now,
    },
    case_set: null,
    report: {
      run_id: 'readiness_1',
      agent_id: 'sales',
      before_scores: [],
      after_scores: [],
      proposed_changes: emptyProposedChanges(),
      publish_recommendation: 'publish',
      blockers: [],
      next_steps: [],
      provider_invocations: [],
      score_breakdown: { run_score: 1 },
    },
  })
  mockAtlasService.getReadinessRun.mockResolvedValue(null)
  mockAtlasService.listReadinessRuns.mockResolvedValue({ runs: [], has_more: false, total_count: 0 })
  mockAtlasService.getReadinessProviderHealth.mockResolvedValue({
    provider_policy: 'deterministic',
    gemini_configured: false,
    anthropic_configured: false,
    artifact_store_configured: true,
    voice_harness: 'DeterministicAtlasVoiceHarness',
    warnings: [],
  })
  mockAtlasService.listReadinessEvents.mockResolvedValue({ run_id: 'readiness_1', events: [], has_more: false, total_count: 0 })
  mockAtlasService.getReadinessReport.mockResolvedValue(null)
  mockAtlasService.proposeReadinessDeltas.mockResolvedValue(null)
  mockAtlasService.rerunReadinessRun.mockResolvedValue(null)
  mockAtlasService.cancelReadinessRun.mockResolvedValue(null)
  mockAtlasService.applyPermissionDecisions.mockResolvedValue({
    session_id: 'atlas_session_test',
    updated_requests: [
      {
        request_id: 'permission_1',
        kind: 'apply_deltas',
        status: 'approved',
        reason: 'Apply approved Atlas changes',
        risk_summary: null,
        requested_actions: ['apply_deltas'],
        delta_ids: ['delta_step_1'],
        scope_ref: {},
        created_at: now,
        expires_at: now,
      },
    ],
  })
  mockAtlasService.applyChanges.mockResolvedValue({
    apply_request_id: 'apply_1',
    session_id: 'atlas_session_test',
    status: 'applied',
    error: null,
  })
}

function renderPanel(userId: string | null = 'reviewer-user') {
  useAuthStore.setState({
    user: userId ? ({ user_id: userId } as never) : null,
    isAuthenticated: Boolean(userId),
    isInitialized: true,
  })
  render(<AtlasAIPanel isOpen onClose={jest.fn()} agentId="sales" />)
}

beforeEach(() => {
  jest.clearAllMocks()
  useAuthStore.setState({
    user: null,
    isAuthenticated: false,
    isInitialized: true,
    isLoading: false,
    isLoggingOut: false,
    error: null,
  })
})

it('does not submit duplicate Atlas turns when send fires twice before React state updates', async () => {
  setupAtlasMocks('author-user')
  renderPanel('reviewer-user')

  const input = screen.getByPlaceholderText('Describe what you want to change, paste an API URL, or attach docs...')
  fireEvent.change(input, { target: { value: 'Review the start step' } })
  const form = input.closest('form')
  expect(form).not.toBeNull()

  fireEvent.submit(form!)
  fireEvent.submit(form!)

  await waitFor(() => expect(mockAtlasService.runTurn).toHaveBeenCalledTimes(1))
  expect(mockAtlasService.startSession).toHaveBeenCalledTimes(1)
})

it('lets the session creator approve their own permission request and only approves/applies once', async () => {
  // Product decision: explicit confirmation is required, but NOT a second
  // human — the creator may approve permission requests for their own
  // session (the old four-eyes rule made apply impossible for
  // single-author orgs).
  setupAtlasMocks('current-user')
  mockAtlasService.runTurn.mockResolvedValueOnce(permissionTurnResponse())
  renderPanel('current-user')

  const input = screen.getByPlaceholderText('Describe what you want to change, paste an API URL, or attach docs...')
  fireEvent.change(input, { target: { value: 'Prepare an apply request' } })
  fireEvent.submit(input.closest('form')!)

  const approve = await screen.findByRole('button', { name: /approve permission/i })
  fireEvent.click(approve)
  fireEvent.click(approve)

  await waitFor(() => expect(mockAtlasService.applyPermissionDecisions).toHaveBeenCalledTimes(1))
  expect(mockAtlasService.applyPermissionDecisions).toHaveBeenCalledWith('atlas_session_test', [
    { request_id: 'permission_1', decision: 'approved' },
  ])
  await waitFor(() => expect(mockAtlasService.applyChanges).toHaveBeenCalledTimes(1))
  expect(mockAtlasService.applyChanges).toHaveBeenCalledWith('atlas_session_test', {
    delta_ids: ['delta_step_1'],
  })

  cleanup()
  jest.clearAllMocks()
  setupAtlasMocks('author-user')
  mockAtlasService.runTurn.mockResolvedValueOnce(permissionTurnResponse())
  renderPanel('reviewer-user')

  const reviewerInput = screen.getByPlaceholderText('Describe what you want to change, paste an API URL, or attach docs...')
  fireEvent.change(reviewerInput, { target: { value: 'Prepare an apply request' } })
  fireEvent.submit(reviewerInput.closest('form')!)

  const reviewerApprove = await screen.findByRole('button', { name: /approve permission/i })
  fireEvent.click(reviewerApprove)
  fireEvent.click(reviewerApprove)

  await waitFor(() => expect(mockAtlasService.applyPermissionDecisions).toHaveBeenCalledTimes(1))
  expect(mockAtlasService.applyPermissionDecisions).toHaveBeenCalledWith('atlas_session_test', [
    { request_id: 'permission_1', decision: 'approved' },
  ])
  await waitFor(() => expect(mockAtlasService.applyChanges).toHaveBeenCalledTimes(1))
  expect(mockAtlasService.applyChanges).toHaveBeenCalledWith('atlas_session_test', {
    delta_ids: ['delta_step_1'],
  })
})
