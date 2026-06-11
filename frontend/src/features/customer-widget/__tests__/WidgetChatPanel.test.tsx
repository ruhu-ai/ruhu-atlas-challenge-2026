import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { WidgetChatPanel } from '../WidgetChatPanel'

const mockUseWidgetContext = jest.fn()
const mockUseWidgetChat = jest.fn()

jest.mock('../WidgetProvider', () => ({
  useWidgetContext: () => mockUseWidgetContext(),
}))

jest.mock('../useWidgetChat', () => ({
  useWidgetChat: () => mockUseWidgetChat(),
}))

describe('WidgetChatPanel artifact disambiguation', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockUseWidgetContext.mockReturnValue({
      config: {
        mode: 'chat',
        primaryColor: '#E64E20',
        accentColor: '#D44D00',
      },
      session: {
        conversationId: 'conv-1',
        pendingToolInvocations: [],
      },
      createSession: jest.fn(),
      confirmPendingToolInvocation: jest.fn(),
      cancelPendingToolInvocation: jest.fn(),
      error: null,
      uploadAttachment: jest.fn(),
      voiceInteractionPolicy: {
        state_id: 'book_demo',
        endpointing_ms: 650,
        soft_timeout_ms: 800,
        turn_eagerness: 'normal',
        interruptibility_policy: 'interruptible_except_policy',
      },
    })
  })

  it('renders artifact choices and sends the selected artifact id back through chat', async () => {
    const sendMessage = jest.fn().mockResolvedValue(undefined)
    mockUseWidgetChat.mockReturnValue({
      messages: [
        {
          id: 'assistant-1',
          role: 'assistant',
          content: 'I found more than one matching booking. Which one do you mean?',
          timestamp: new Date('2026-04-16T10:00:00Z'),
          done: true,
          metadata: {
            message_type: 'artifact_disambiguation',
            payload: {
              candidates: [
                {
                  artifact_id: 'art-1',
                  artifact_type: 'booking',
                  title: 'Demo with Ada',
                  status: 'confirmed',
                },
                {
                  artifact_id: 'art-2',
                  artifact_type: 'booking',
                  title: 'Demo with Tayo',
                  status: 'confirmed',
                },
              ],
            },
          },
        },
      ],
      activities: new Map(),
      isTyping: false,
      sendMessage,
      dismissActivity: jest.fn(),
      isConnected: true,
    })

    render(<WidgetChatPanel />)

    expect(screen.getByText('Voice policy in force')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Demo with Ada/ }))

    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledWith('Demo with Ada', [], { artifact_id: 'art-1' })
    })
  })

  it('renders pending confirmation controls and submits confirmation', async () => {
    const confirmPendingToolInvocation = jest.fn().mockResolvedValue(undefined)
    mockUseWidgetContext.mockReturnValue({
      config: {
        mode: 'chat',
        primaryColor: '#E64E20',
        accentColor: '#D44D00',
      },
      session: {
        conversationId: 'conv-1',
        pendingToolInvocations: [
          {
            invocation_id: 'invoke-1',
            tool_ref: 'sales.create_demo_lead',
            status: 'waiting_confirmation',
            reason: 'Need user approval',
            metadata: {
              confirmation_prompt: 'I have your email. Confirm and I’ll create the demo request now.',
            },
          },
        ],
      },
      createSession: jest.fn(),
      confirmPendingToolInvocation,
      cancelPendingToolInvocation: jest.fn(),
      error: null,
      uploadAttachment: jest.fn(),
      voiceInteractionPolicy: null,
    })
    mockUseWidgetChat.mockReturnValue({
      messages: [],
      activities: new Map(),
      isTyping: false,
      sendMessage: jest.fn(),
      confirmPendingToolInvocation,
      cancelPendingToolInvocation: jest.fn(),
      dismissActivity: jest.fn(),
      isConnected: true,
    })

    render(<WidgetChatPanel />)

    expect(screen.getByText('Confirmation required')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() => {
      expect(confirmPendingToolInvocation).toHaveBeenCalledWith('invoke-1')
    })
  })

  it('allows sending attachments without typed text', async () => {
    const sendMessage = jest.fn().mockResolvedValue(undefined)
    const uploadAttachment = jest.fn().mockResolvedValue({
      attachment_id: 'att-1',
      source: 'public_widget',
      kind: 'text',
      filename: 'notes.txt',
      content_type: 'text/plain',
      size_bytes: 12,
      scan_status: 'passed',
      extraction_status: 'ready',
    })
    mockUseWidgetContext.mockReturnValue({
      config: {
        mode: 'chat',
        primaryColor: '#E64E20',
        accentColor: '#D44D00',
      },
      session: {
        conversationId: 'conv-1',
        pendingToolInvocations: [],
      },
      createSession: jest.fn(),
      confirmPendingToolInvocation: jest.fn(),
      cancelPendingToolInvocation: jest.fn(),
      error: null,
      uploadAttachment,
      voiceInteractionPolicy: null,
    })
    mockUseWidgetChat.mockReturnValue({
      messages: [],
      activities: new Map(),
      isTyping: false,
      sendMessage,
      confirmPendingToolInvocation: jest.fn(),
      cancelPendingToolInvocation: jest.fn(),
      dismissActivity: jest.fn(),
      isConnected: true,
    })

    const { container } = render(<WidgetChatPanel />)
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['hello'], 'notes.txt', { type: 'text/plain' })

    fireEvent.change(fileInput, { target: { files: [file] } })

    await waitFor(() => {
      expect(screen.getByText('notes.txt')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledWith('', [
        expect.objectContaining({ attachment_id: 'att-1' }),
      ])
    })
  })
})
