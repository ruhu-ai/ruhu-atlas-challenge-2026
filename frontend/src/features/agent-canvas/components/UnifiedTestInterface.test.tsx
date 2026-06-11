import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { apiClient } from '@/api/client'

const mockCreateSessionMutateAsync = jest.fn()
const mockEndSessionMutateAsync = jest.fn()
const mockVoiceHealthRefetch = jest.fn()
const mockUseVoiceHealth = jest.fn()
const mockFetch = jest.fn()
const mockGetAgentDefinition = jest.fn()
const mockApiPost = apiClient.post as jest.Mock

jest.mock('@livekit/components-react', () => ({
  LiveKitRoom: ({ children }: { children: ReactNode }) => <>{children}</>,
  RoomAudioRenderer: () => null,
  useRoomContext: () => null,
  useLocalParticipant: () => ({ localParticipant: null }),
}))

jest.mock('@livekit/components-styles', () => ({}))

// InteractionDebugPanel imports apiClient, which reads import.meta.env at
// module load.  Jest's CommonJS runtime cannot evaluate import.meta, so stub
// apiClient and the request-cancel helper here.
jest.mock('@/api/client', () => ({
  apiClient: {
    get: jest.fn().mockResolvedValue({}),
    post: jest.fn().mockResolvedValue({}),
    put: jest.fn().mockResolvedValue({}),
    patch: jest.fn().mockResolvedValue({}),
    delete: jest.fn().mockResolvedValue({}),
  },
  cancelAllRequests: jest.fn(),
}))

// UnifiedTestInterface fetches the selected runtime target agent definition
// on mount so the InteractionDebugPanel can show step-level pacing overrides.
// The tests don't care about definition contents — soft-fail so nothing crashes.
jest.mock('@/api/services/agent-definition.service', () => ({
  agentDefinitionService: {
    getAgentDefinition: (...args: unknown[]) => mockGetAgentDefinition(...args),
  },
}))

jest.mock('@/utils/logger', () => ({
  createLogger: () => ({
    debug: jest.fn(),
    log: jest.fn(),
    info: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
  }),
}))

jest.mock('@/features/voice-session/hooks/useVoiceSessions', () => ({
  useCreateVoiceSession: () => ({
    mutateAsync: mockCreateSessionMutateAsync,
    isPending: false,
  }),
  useEndVoiceSession: () => ({
    mutateAsync: mockEndSessionMutateAsync,
    isPending: false,
  }),
  useVoiceHealth: () => mockUseVoiceHealth(),
}))

const { UnifiedTestInterface } = require('./UnifiedTestInterface') as typeof import('./UnifiedTestInterface')

function createSseResponse(events: Array<{ event: string; data: Record<string, unknown> }>) {
  const payload = events
    .map(({ event, data }) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
    .join('')
  let consumed = false
  return {
    ok: true,
    status: 200,
    body: {
      getReader() {
        return {
          async read() {
            if (consumed) {
              return { done: true, value: undefined }
            }
            consumed = true
            return { done: false, value: new TextEncoder().encode(payload) }
          },
        }
      },
    },
    json: async () => ({}),
  }
}

describe('UnifiedTestInterface', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    ;(global as typeof globalThis & { fetch: typeof fetch }).fetch = mockFetch as unknown as typeof fetch
    mockGetAgentDefinition.mockResolvedValue({ definition: null })
    mockApiPost.mockResolvedValue({
      conversation_id: 'conv-1',
      session_token: 'session-token-1',
      messages: [],
      pending_tool_invocations: [],
    })
    mockFetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        conversation_id: 'conv-1',
        session_token: 'session-token-1',
        messages: [],
        pending_tool_invocations: [],
      }),
    })
    mockVoiceHealthRefetch.mockResolvedValue({
      data: { voice_available: true, livekit_reachable: true, mock: false },
    })
    mockUseVoiceHealth.mockReturnValue({
      data: { voice_available: true, livekit_reachable: true, mock: false },
      isLoading: false,
      isError: false,
      refetch: mockVoiceHealthRefetch,
    })
  })

  it('disables voice call start when voice transport is unavailable', async () => {
    mockUseVoiceHealth.mockReturnValue({
      data: { voice_available: false, livekit_reachable: false, mock: false },
      isLoading: false,
      isError: false,
      refetch: mockVoiceHealthRefetch,
    })

    render(
      <UnifiedTestInterface
        agentId="agent-1"
        agentName="Support Agent"
        agentType="voice"
        agentStatus="active"
      />,
    )

    const button = screen.getByTitle('Voice service may be unavailable')
    expect(button).toBeInTheDocument()
    expect(button).toBeEnabled()
  })

  it('allows voice call start when transport is available even if admin reachability is degraded', async () => {
    mockUseVoiceHealth.mockReturnValue({
      data: { voice_available: true, livekit_reachable: false, mock: false },
      isLoading: false,
      isError: false,
      refetch: mockVoiceHealthRefetch,
    })

    render(
      <UnifiedTestInterface
        agentId="agent-1"
        agentName="Support Agent"
        agentType="voice"
        agentStatus="active"
      />,
    )

    expect(screen.getByTitle('Start voice call')).toBeEnabled()
  })

  it('supports chat + attachments in multimodal test mode', async () => {
    render(
      <UnifiedTestInterface
        agentId="agent-1"
        agentName="Support Agent"
        agentType="multimodal"
        agentStatus="active"
      />,
    )

    expect(screen.getByPlaceholderText('Type a message...')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTitle('Attach file')).toBeEnabled())
    expect(screen.getByTitle('Start voice call')).toBeEnabled()
  })

  it('renders pending confirmation and submits confirm in the preview surface', async () => {
    mockFetch.mockImplementation(async (url: string, options?: RequestInit) => {
      const target = String(url)
      if (target.endsWith('/agents/agent-1/test-session')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            conversation_id: 'conv-1',
            session_token: 'session-token-1',
            messages: [],
            pending_tool_invocations: [],
          }),
        }
      }
      if (target.includes('/public/widget/sessions/conv-1/projection')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            pending_tool_invocations: [
              {
                invocation_id: 'invoke-1',
                tool_ref: 'sales.create_demo_lead',
                status: 'waiting_confirmation',
                metadata: {
                  confirmation_prompt: 'I have your email. Confirm and I’ll create the demo request now.',
                },
              },
            ],
          }),
        }
      }
      if (target.includes('/tool-invocations/invoke-1/confirm')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            messages: [{ role: 'assistant', text: 'Your demo request has been submitted!' }],
            pending_tool_invocations: [],
          }),
        }
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          conversation_id: 'conv-1',
          session_token: 'session-token-1',
          messages: [],
          pending_tool_invocations: [],
        }),
      }
    })

    render(
      <UnifiedTestInterface
        agentId="agent-1"
        agentName="Support Agent"
        agentType="multimodal"
        agentStatus="active"
      />,
    )

    await waitFor(() => {
      expect(screen.getByText('Confirmation required')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/tool-invocations/invoke-1/confirm'),
        expect.objectContaining({ method: 'POST' }),
      )
    })
  })

  it('always tests the current canvas draft version', async () => {
    render(
      <UnifiedTestInterface
        agentId="agent-1"
        agentName="Support Agent"
        agentType="multimodal"
        agentStatus="active"
      />,
    )

    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith('/agents/agent-1/test-session', {
        channel: 'web_widget',
        conversation_id: undefined,
        session_token: undefined,
      })
    })

    expect(mockGetAgentDefinition).toHaveBeenCalledWith('agent-1', 'draft')
    expect(screen.queryByRole('button', { name: 'Published' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Draft Preview' })).not.toBeInTheDocument()
  })

  it('renders every assistant reply returned by chat sends after a bootstrapped greeting', async () => {
    mockApiPost.mockResolvedValue({
      conversation_id: 'conv-1',
      session_token: 'session-token-1',
      messages: [{ role: 'assistant', text: 'Welcome' }],
      pending_tool_invocations: [],
    })
    mockFetch.mockImplementation(async (url: string, options?: RequestInit) => {
      const target = String(url)
      if (target.includes('/messages/stream')) {
        const body = JSON.parse(String(options?.body || '{}')) as { text?: string }
        const text = body.text === 'first' ? 'First reply' : 'Second reply'
        return createSseResponse([
          { event: 'typing', data: { is_typing: true } },
          { event: 'message', data: { role: 'assistant', text } },
          {
            event: 'done',
            data: {
              conversation_id: 'conv-1',
              step_after: 'discover',
              trace_id: `trace-${body.text || 'turn'}`,
              pending_tool_invocations: [],
            },
          },
        ])
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          conversation_id: 'conv-1',
          session_token: 'session-token-1',
          messages: [],
          pending_tool_invocations: [],
          attachments: [],
          browser_tasks: [],
          interaction_status: [],
        }),
      }
    })

    render(
      <UnifiedTestInterface
        agentId="agent-1"
        agentName="Sales Agent"
        agentType="multimodal"
        agentStatus="active"
      />,
    )

    await waitFor(() => expect(screen.getByText('Welcome')).toBeInTheDocument())

    const input = screen.getByPlaceholderText('Type a message...')
    fireEvent.change(input, { target: { value: 'first' } })
    fireEvent.click(screen.getByTitle('Send'))
    await waitFor(() => expect(screen.getByText('First reply')).toBeInTheDocument())

    fireEvent.change(input, { target: { value: 'second' } })
    fireEvent.click(screen.getByTitle('Send'))
    await waitFor(() => expect(screen.getByText('Second reply')).toBeInTheDocument())
  })
})
