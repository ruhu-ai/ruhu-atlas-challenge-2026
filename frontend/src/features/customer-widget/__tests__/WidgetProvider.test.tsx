import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { WidgetProvider, useWidgetContext } from '../WidgetProvider'
import type { WidgetConfig } from '../widget-types'

const mockFetch = jest.fn()

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

function Harness() {
  const { session, createSession, sendMessage, voiceInteractionPolicy, browserTasks } = useWidgetContext()

  return (
    <div>
      <button onClick={() => void createSession('multimodal')}>create</button>
      <button onClick={() => void sendMessage('hello')}>send</button>
      <div data-testid="state-id">{session?.stateId ?? ''}</div>
      <div data-testid="pending-count">{session?.pendingToolInvocations.length ?? 0}</div>
      <div data-testid="messages-count">{session?.messages.length ?? 0}</div>
      <div data-testid="messages-text">{session?.messages.map((message) => `${message.role}:${message.text}`).join('|') ?? ''}</div>
      <div data-testid="browser-task-count">{browserTasks.length}</div>
      <div data-testid="voice-policy-state">{voiceInteractionPolicy?.step_id ?? ''}</div>
      <div data-testid="voice-policy-interruptibility">
        {voiceInteractionPolicy?.interruptibility_policy ?? ''}
      </div>
    </div>
  )
}

describe('WidgetProvider', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    ;(global as typeof globalThis & { fetch: typeof fetch }).fetch = mockFetch as unknown as typeof fetch
    // Fallback for any fetch calls the individual tests don't queue responses
    // for — e.g. the WidgetProvider's background projection poll (spec 23 §
    // Projected Activity / Status Trail) fires every 2s while connected and
    // would otherwise consume the test's `mockResolvedValueOnce` queue.
    mockFetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        snapshot_id: 'snap-default',
        conversation_id: 'conv-default',
        pending_tool_invocations: [],
        attachments: [],
        browser_tasks: [],
        interaction_status: [],
        voice_activity: null,
        voice_interaction_policy: null,
      }),
    })
  })

  it('applies streamed done payload state to the widget session', async () => {
    const config: WidgetConfig = {
      agentId: 'sales_agent',
      apiUrl: 'http://widget.test/api/v1',
      mode: 'multimodal',
      position: 'bottom-right',
      primaryColor: '#E64E20',
      accentColor: '#D44D00',
      buttonText: 'Talk to us',
      companyName: 'Support',
      welcomeMessage: 'Hi',
      autoOpen: false,
      showPoweredBy: true,
      features: { browser_tasks: true },
      browserTaskRenderMode: 'summaries',
    }

    // URL-based routing — the WidgetProvider runs a background projection
    // poll on top of session/message calls, so sequential mockResolvedValueOnce
    // is order-fragile.  We branch on URL instead.
    const sessionResponse = {
      ok: true,
      status: 200,
      json: async () => ({
        conversation_id: 'conv-1',
        agent_id: 'sales_agent',
        step_id: 'state-initial',
        resumed: false,
        session_token: 'token-1',
        messages: [],
        pending_tool_invocations: [],
      }),
    }
    const messageSseResponse = () =>
      createSseResponse([
        { event: 'typing', data: { is_typing: true } },
        { event: 'message', data: { role: 'assistant', text: 'First reply' } },
        {
          event: 'done',
          data: {
            conversation_id: 'conv-1',
            step_after: 'state-after',
            trace_id: 'trace-1',
            pending_tool_invocations: [
              {
                invocation_id: 'tool-1',
                tool_ref: 'confirm_booking',
                status: 'pending_confirmation',
                reason: 'Need user approval',
              },
            ],
          },
        },
      ])
    mockFetch.mockImplementation(async (url: string) => {
      const u = String(url)
      if (u.includes('/sessions') && u.endsWith('/messages/stream')) {
        return messageSseResponse()
      }
      if (u.endsWith('/sessions')) {
        return sessionResponse
      }
      // Projection poll, heartbeat, events flush — benign default.
      return {
        ok: true,
        status: 200,
        json: async () => ({
          snapshot_id: 'snap-default',
          conversation_id: 'conv-1',
          pending_tool_invocations: [
            {
              invocation_id: 'tool-projection-1',
              tool_ref: 'sales.create_demo_lead',
              status: 'waiting_confirmation',
              reason: 'Need user approval',
            },
          ],
          attachments: [],
          browser_tasks: [
            {
              task_id: 'btask-1',
              title: 'Check billing portal',
              summary: 'Looking up invoice',
              state: 'running',
              approval_state: 'not_required',
              task_pack_id: 'invoice_lookup',
              task_pack_version: '1.0.0',
              task_pack_label: 'Invoice lookup',
              domain_label: 'portal.example.com',
              latest_progress: 'Searching portal',
              approval: null,
              artifacts: [],
              cancellable: true,
              show_live_snapshot: false,
              live_snapshot_artifact_id: null,
              updated_at: '2026-05-01T00:00:00Z',
            },
          ],
          interaction_status: [],
          voice_activity: null,
          voice_interaction_policy: {
            step_id: 'state-after',
            endpointing_ms: 650,
            soft_timeout_ms: 800,
            turn_eagerness: 'normal',
            interruptibility_policy: 'interruptible_except_policy',
          },
        }),
      }
    })

    render(
      <WidgetProvider config={config}>
        <Harness />
      </WidgetProvider>,
    )

    fireEvent.click(screen.getByText('create'))
    await waitFor(() => expect(screen.getByTestId('state-id')).toHaveTextContent('state-initial'))

    await act(async () => {
      fireEvent.click(screen.getByText('send'))
    })

    await waitFor(() => expect(screen.getByTestId('state-id')).toHaveTextContent('state-after'))
    await waitFor(() => expect(screen.getByTestId('pending-count')).toHaveTextContent('1'))
    expect(screen.getByTestId('messages-count')).toHaveTextContent('2')
    expect(screen.getByTestId('messages-text')).toHaveTextContent('user:hello|assistant:First reply')
    await waitFor(() => {
      expect(screen.getByTestId('voice-policy-state')).toHaveTextContent('state-after')
    })
    expect(screen.getByTestId('browser-task-count')).toHaveTextContent('1')
    expect(screen.getByTestId('voice-policy-interruptibility')).toHaveTextContent(
      'interruptible_except_policy',
    )
  })

  it('refreshes the transcript when a successful stream returns no parsed message event', async () => {
    const config: WidgetConfig = {
      agentId: 'sales_agent',
      apiUrl: 'http://widget.test/api/v1',
      mode: 'chat',
      position: 'bottom-right',
      primaryColor: '#E64E20',
      accentColor: '#D44D00',
      buttonText: 'Talk to us',
      companyName: 'Support',
      welcomeMessage: 'Hi',
      autoOpen: false,
      showPoweredBy: true,
    }

    const sessionResponse = {
      ok: true,
      status: 200,
      json: async () => ({
        conversation_id: 'conv-1',
        agent_id: 'sales_agent',
        step_id: 'state-initial',
        resumed: false,
        session_token: 'token-1',
        messages: [{ role: 'assistant', text: 'Welcome' }],
        pending_tool_invocations: [],
      }),
    }
    const refreshedSessionResponse = {
      ok: true,
      status: 200,
      json: async () => ({
        conversation_id: 'conv-1',
        agent_id: 'sales_agent',
        step_id: 'state-after',
        resumed: true,
        session_token: 'token-1',
        messages: [
          { role: 'assistant', text: 'Welcome' },
          { role: 'user', text: 'hello' },
          { role: 'assistant', text: 'Recovered reply' },
        ],
        pending_tool_invocations: [],
      }),
    }
    const doneOnlySseResponse = () =>
      createSseResponse([
        { event: 'typing', data: { is_typing: true } },
        {
          event: 'done',
          data: {
            conversation_id: 'conv-1',
            step_after: 'state-after',
            trace_id: 'trace-1',
            pending_tool_invocations: [],
          },
        },
      ])

    mockFetch.mockImplementation(async (url: string) => {
      const u = String(url)
      if (u.includes('/sessions') && u.endsWith('/messages/stream')) {
        return doneOnlySseResponse()
      }
      if (u.endsWith('/sessions/conv-1')) {
        return refreshedSessionResponse
      }
      if (u.endsWith('/sessions')) {
        return sessionResponse
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          snapshot_id: 'snap-default',
          conversation_id: 'conv-1',
          pending_tool_invocations: [],
          attachments: [],
          browser_tasks: [],
          interaction_status: [],
          voice_activity: null,
          voice_interaction_policy: null,
        }),
      }
    })

    render(
      <WidgetProvider config={config}>
        <Harness />
      </WidgetProvider>,
    )

    fireEvent.click(screen.getByText('create'))
    await waitFor(() => expect(screen.getByTestId('messages-text')).toHaveTextContent('assistant:Welcome'))

    await act(async () => {
      fireEvent.click(screen.getByText('send'))
    })

    await waitFor(() => {
      expect(screen.getByTestId('messages-text')).toHaveTextContent('assistant:Welcome|user:hello|assistant:Recovered reply')
    })
    expect(screen.getByTestId('state-id')).toHaveTextContent('state-after')
  })
})
