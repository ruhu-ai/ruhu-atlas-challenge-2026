import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useWidgetChat } from '../useWidgetChat'

const mockSendContextMessage = jest.fn()
const mockConfirmContextInvocation = jest.fn()
const mockCancelContextInvocation = jest.fn()
const mockUseWidgetContext = jest.fn()

jest.mock('../WidgetProvider', () => ({
  useWidgetContext: () => mockUseWidgetContext(),
}))

class FakeEventSource {
  static instances: FakeEventSource[] = []

  listeners = new Map<string, Array<(event: MessageEvent<string>) => void>>()

  constructor(public url: string) {
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: (event: MessageEvent<string>) => void) {
    const existing = this.listeners.get(type) || []
    existing.push(listener)
    this.listeners.set(type, existing)
  }

  emit(type: string, data: Record<string, unknown>) {
    const event = { data: JSON.stringify(data) } as MessageEvent<string>
    for (const listener of this.listeners.get(type) || []) {
      listener(event)
    }
  }

  close() {}
}

function Harness() {
  const { messages, sendMessage, confirmPendingToolInvocation } = useWidgetChat()

  return (
    <div>
      <button onClick={() => void sendMessage('hello')}>send</button>
      <button onClick={() => void confirmPendingToolInvocation('inv-1')}>confirm</button>
      <div data-testid="messages">
        {messages.map((message) => `${message.role}:${message.content}`).join('|')}
      </div>
    </div>
  )
}

describe('useWidgetChat', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    FakeEventSource.instances = []
    mockSendContextMessage.mockResolvedValue({
      message_id: 'msg-1',
      newMessages: [{ role: 'assistant', text: 'Repeated reply' }],
    })
    mockConfirmContextInvocation.mockResolvedValue({
      conversation_id: 'conv-1',
      state_after: 'state-confirmed',
      messages: [{ role: 'assistant', text: 'Confirmed reply' }],
      trace_id: 'trace-confirmed',
      pending_tool_invocations: [],
    })
    mockCancelContextInvocation.mockResolvedValue({
      conversation_id: 'conv-1',
      state_after: 'state-cancelled',
      messages: [],
      trace_id: 'trace-cancelled',
      pending_tool_invocations: [],
    })
    mockUseWidgetContext.mockReturnValue({
      config: { apiUrl: 'http://widget.test/api/v1' },
      session: {
        conversationId: 'conv-1',
        sessionToken: 'token-1',
        stateId: 'state-initial',
        messages: [],
        resumed: false,
        pendingToolInvocations: [],
        agentId: 'sales_agent',
      },
      sessionToken: 'token-1',
      isConnected: true,
      sendMessage: mockSendContextMessage,
      confirmPendingToolInvocation: mockConfirmContextInvocation,
      cancelPendingToolInvocation: mockCancelContextInvocation,
    })
    Object.defineProperty(window, 'EventSource', {
      writable: true,
      value: FakeEventSource,
    })
  })

  it('keeps repeated assistant messages from distinct events while suppressing the optimistic duplicate', async () => {
    render(<Harness />)

    await act(async () => {
      fireEvent.click(screen.getByText('send'))
    })

    await waitFor(() => expect(screen.getByTestId('messages')).toHaveTextContent('assistant:Repeated reply'))

    const eventSource = FakeEventSource.instances[0]
    expect(eventSource.url).toContain('/public/widget/sessions/conv-1/conversation-events')

    act(() => {
      eventSource.emit('conversation.event', {
        event_id: 'event-1',
        family: 'message',
        name: 'assistant_emitted',
        conversation_sequence: 1,
        payload: { text: 'Repeated reply' },
        created_at: '2026-04-12T10:00:00Z',
      })
    })

    expect(screen.getByTestId('messages').textContent).toBe('user:hello|assistant:Repeated reply')

    act(() => {
      eventSource.emit('conversation.event', {
        event_id: 'event-2',
        family: 'message',
        name: 'assistant_emitted',
        conversation_sequence: 2,
        payload: { text: 'Repeated reply' },
        created_at: '2026-04-12T10:00:01Z',
      })
    })

    await waitFor(() => {
      expect(screen.getByTestId('messages').textContent).toBe(
        'user:hello|assistant:Repeated reply|assistant:Repeated reply',
      )
    })
  })

  it('suppresses the event-stream duplicate after confirming a pending tool invocation', async () => {
    render(<Harness />)

    await act(async () => {
      fireEvent.click(screen.getByText('confirm'))
    })

    await waitFor(() => expect(screen.getByTestId('messages')).toHaveTextContent('assistant:Confirmed reply'))

    const eventSource = FakeEventSource.instances[0]
    expect(eventSource.url).toContain('/public/widget/sessions/conv-1/conversation-events')

    act(() => {
      eventSource.emit('conversation.event', {
        event_id: 'event-confirm-1',
        family: 'message',
        name: 'assistant_emitted',
        conversation_sequence: 1,
        payload: { text: 'Confirmed reply' },
        created_at: '2026-04-12T10:00:02Z',
      })
    })

    await waitFor(() => {
      expect(screen.getByTestId('messages').textContent).toBe('assistant:Confirmed reply')
    })
  })

  it('does not let a fresh-session welcome message suppress the first replayed assistant event', async () => {
    mockUseWidgetContext.mockReturnValue({
      config: { apiUrl: 'http://widget.test/api/v1' },
      session: {
        conversationId: 'conv-1',
        sessionToken: 'token-1',
        stateId: 'state-initial',
        messages: [{ role: 'assistant', text: 'Welcome' }],
        resumed: false,
        pendingToolInvocations: [],
        agentId: 'sales_agent',
      },
      sessionToken: 'token-1',
      isConnected: true,
      sendMessage: mockSendContextMessage,
      confirmPendingToolInvocation: mockConfirmContextInvocation,
      cancelPendingToolInvocation: mockCancelContextInvocation,
    })

    render(<Harness />)

    const eventSource = FakeEventSource.instances[0]
    act(() => {
      eventSource.emit('conversation.event', {
        event_id: 'event-live-1',
        family: 'message',
        name: 'assistant_emitted',
        conversation_sequence: 1,
        payload: { text: 'Live reply' },
        created_at: '2026-04-12T10:00:03Z',
      })
    })

    await waitFor(() => {
      expect(screen.getByTestId('messages').textContent).toBe('assistant:Welcome|assistant:Live reply')
    })
  })
})
