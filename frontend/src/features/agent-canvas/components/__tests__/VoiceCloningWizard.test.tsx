/**
 * Phase 2a-cloning — VoiceCloningWizard tests.
 *
 * The wizard is the audit-sensitive path of the cloning surface
 * (consent capture, audio submission). Tests pin the public contract:
 *
 * 1. Consent script displays the EXACT Google-required text.
 * 2. Submit button stays disabled until both display_name and a
 *    valid recording exist.
 * 3. Server errors map to specific user-readable copy by status code.
 * 4. Permission denial surfaces a clear message instead of silent
 *    failure.
 * 5. The submitted FormData carries the consent audio under the right
 *    field name.
 *
 * MediaRecorder is JSDOM-incompatible; we stub it. Audio capture
 * timing is out of scope for unit tests — the recorder lifecycle
 * (start/stop/duration cap) is best validated by the integration
 * Playwright suite, which lives outside this PR.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { VoiceCloningWizard } from '@/features/agent-canvas/components/VoiceCloningWizard'

/** Construct an error with the .status / .message shape that the
 * production ApiError class has. The wizard duck-types on this shape
 * (see VoiceCloningWizard.isApiError) so we don't need the real
 * ApiError class — which would pull in import.meta.env evaluation. */
function makeApiError(message: string, status: number): Error {
  const err = new Error(message) as Error & { status: number }
  err.status = status
  return err
}

const mockCloneVoice = jest.fn()
jest.mock('@/api/services/agent-definition.service', () => ({
  agentDefinitionService: {
    cloneVoice: (...args: unknown[]) => mockCloneVoice(...args),
  },
}))

// ── MediaRecorder stub ──────────────────────────────────────────────────────

interface FakeRecorderInstance {
  start: jest.Mock
  stop: jest.Mock
  state: 'inactive' | 'recording'
  mimeType: string
  addEventListener: jest.Mock
  _listeners: Record<string, Array<(event: Event) => void>>
  _emit: (type: string, event: Event) => void
}

const fakeRecorders: FakeRecorderInstance[] = []

class FakeMediaRecorder {
  state: 'inactive' | 'recording' = 'inactive'
  mimeType = 'audio/webm'
  _listeners: Record<string, Array<(event: Event) => void>> = {}
  start = jest.fn(() => {
    this.state = 'recording'
  })
  stop = jest.fn(() => {
    this.state = 'inactive'
    this._emit('stop', new Event('stop'))
  })
  addEventListener = jest.fn((type: string, listener: (event: Event) => void) => {
    if (!this._listeners[type]) this._listeners[type] = []
    this._listeners[type].push(listener)
  })
  _emit(type: string, event: Event) {
    const listeners = this._listeners[type] ?? []
    listeners.forEach((l) => l(event))
  }
  constructor() {
    fakeRecorders.push(this as unknown as FakeRecorderInstance)
  }
}

const mockGetUserMedia = jest.fn()

function withQueryClient(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return <QueryClientProvider client={client}>{node}</QueryClientProvider>
}

beforeEach(() => {
  jest.clearAllMocks()
  fakeRecorders.length = 0
  ;(globalThis as unknown as { MediaRecorder: typeof MediaRecorder }).MediaRecorder =
    FakeMediaRecorder as unknown as typeof MediaRecorder
  Object.defineProperty(globalThis.navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: mockGetUserMedia },
  })
  mockGetUserMedia.mockResolvedValue({
    getTracks: () => [{ stop: jest.fn() }],
  } as unknown as MediaStream)
})

// ── Tests ──────────────────────────────────────────────────────────────────

describe('VoiceCloningWizard — render', () => {
  it('shows the EXACT Google-required consent script', () => {
    render(
      withQueryClient(
        <VoiceCloningWizard open={true} onClose={jest.fn()} onCloned={jest.fn()} />,
      ),
    )
    const script = screen.getByTestId('voice-cloning-consent-script')
    expect(script.textContent).toBe(
      'I am the owner of this voice, and I consent to Google using this voice to create a synthetic voice model.',
    )
  })

  it('renders the start-recording button before any recording exists', () => {
    render(
      withQueryClient(
        <VoiceCloningWizard open={true} onClose={jest.fn()} onCloned={jest.fn()} />,
      ),
    )
    expect(screen.getByTestId('voice-cloning-start-record')).toBeInTheDocument()
  })

  it('disables submit until both name and recording exist', () => {
    render(
      withQueryClient(
        <VoiceCloningWizard open={true} onClose={jest.fn()} onCloned={jest.fn()} />,
      ),
    )
    expect(screen.getByTestId('voice-cloning-submit')).toBeDisabled()
  })
})

// ── Permission errors ─────────────────────────────────────────────────────

describe('VoiceCloningWizard — permission errors', () => {
  it('surfaces a clear error when mic permission is denied', async () => {
    const denied = new Error('denied')
    denied.name = 'NotAllowedError'
    mockGetUserMedia.mockRejectedValueOnce(denied)
    render(
      withQueryClient(
        <VoiceCloningWizard open={true} onClose={jest.fn()} onCloned={jest.fn()} />,
      ),
    )
    fireEvent.click(screen.getByTestId('voice-cloning-start-record'))
    expect(
      await screen.findByTestId('voice-cloning-permission-error'),
    ).toHaveTextContent(/Microphone permission was denied/i)
  })

  it('surfaces a clear error when no microphone is detected', async () => {
    const notFound = new Error('no mic')
    notFound.name = 'NotFoundError'
    mockGetUserMedia.mockRejectedValueOnce(notFound)
    render(
      withQueryClient(
        <VoiceCloningWizard open={true} onClose={jest.fn()} onCloned={jest.fn()} />,
      ),
    )
    fireEvent.click(screen.getByTestId('voice-cloning-start-record'))
    expect(
      await screen.findByTestId('voice-cloning-permission-error'),
    ).toHaveTextContent(/No microphone was detected/i)
  })
})

// ── Submission ────────────────────────────────────────────────────────────

async function _captureRecording() {
  // Click start, wait for the recorder to be created, then synthesise
  // a 'stop' event with non-trivial data so the wizard accepts the
  // recording. We patch Date.now to control the elapsed-time check.
  const realNow = Date.now
  let mockNow = 1_000_000
  Date.now = () => mockNow
  fireEvent.click(screen.getByTestId('voice-cloning-start-record'))
  await waitFor(() => expect(fakeRecorders.length).toBe(1))
  const recorder = fakeRecorders[0]
  // Push a non-empty data chunk so the resulting Blob isn't 0 bytes.
  ;(recorder._listeners.dataavailable ?? []).forEach((l) =>
    l(new MessageEvent('dataavailable', { data: new Blob(['x'.repeat(50)]) })),
  )
  // Advance time past MIN_AUDIO_MS (1500ms) so the duration check passes.
  mockNow += 2000
  // Now trigger stop.
  recorder.stop()
  Date.now = realNow
  // Wait for React to flush the captured-recording state. The submit
  // button stays disabled until recordedBlob is set; tests that don't
  // wait will fire submit while the button is still disabled.
  await waitFor(() =>
    expect(screen.getByTestId('voice-cloning-submit')).not.toBeDisabled(),
  )
}

describe('VoiceCloningWizard — submission', () => {
  it('submits FormData with display_name + language + consent audio', async () => {
    mockCloneVoice.mockResolvedValueOnce({
      clone_id: 'vc_test',
      provider: 'vertex_gemini',
      display_name: 'CEO Voice',
      language: 'en-US',
      created_at: '2026-05-09T12:00:00Z',
      estimated_cost_usd: 0.5,
    })
    const onCloned = jest.fn()
    const onClose = jest.fn()
    render(
      withQueryClient(
        <VoiceCloningWizard open={true} onClose={onClose} onCloned={onCloned} />,
      ),
    )
    fireEvent.change(screen.getByTestId('voice-cloning-display-name'), {
      target: { value: 'CEO Voice' },
    })
    await _captureRecording()

    fireEvent.click(screen.getByTestId('voice-cloning-submit'))

    await waitFor(() => expect(mockCloneVoice).toHaveBeenCalledTimes(1))
    const args = mockCloneVoice.mock.calls[0][0]
    expect(args.displayName).toBe('CEO Voice')
    expect(args.language).toBe('en-US')
    expect(args.consentAudio).toBeInstanceOf(Blob)
    expect(args.consentAudio.size).toBeGreaterThan(0)

    await waitFor(() => expect(onCloned).toHaveBeenCalledTimes(1))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('forwards agentId when provided', async () => {
    mockCloneVoice.mockResolvedValueOnce({
      clone_id: 'vc_x', provider: 'vertex_gemini',
      display_name: 'X', language: 'en-US',
      created_at: '2026-05-09T12:00:00Z', estimated_cost_usd: 0,
    })
    render(
      withQueryClient(
        <VoiceCloningWizard
          open={true}
          onClose={jest.fn()}
          onCloned={jest.fn()}
          agentId="agent-42"
        />,
      ),
    )
    fireEvent.change(screen.getByTestId('voice-cloning-display-name'), {
      target: { value: 'Test' },
    })
    await _captureRecording()
    fireEvent.click(screen.getByTestId('voice-cloning-submit'))
    await waitFor(() => expect(mockCloneVoice).toHaveBeenCalled())
    expect(mockCloneVoice.mock.calls[0][0].agentId).toBe('agent-42')
  })
})

// ── Server error mapping ──────────────────────────────────────────────────

describe('VoiceCloningWizard — server error mapping', () => {
  async function _setupAndSubmit() {
    render(
      withQueryClient(
        <VoiceCloningWizard open={true} onClose={jest.fn()} onCloned={jest.fn()} />,
      ),
    )
    fireEvent.change(screen.getByTestId('voice-cloning-display-name'), {
      target: { value: 'Test' },
    })
    await _captureRecording()
    fireEvent.click(screen.getByTestId('voice-cloning-submit'))
  }

  it('422 → consent rejection copy', async () => {
    mockCloneVoice.mockRejectedValueOnce(
      makeApiError('Google rejected the consent recording', 422),
    )
    await _setupAndSubmit()
    expect(
      await screen.findByTestId('voice-cloning-submit-error'),
    ).toHaveTextContent(/Google rejected/i)
  })

  it('413 → audio-too-large copy', async () => {
    mockCloneVoice.mockRejectedValueOnce(
      makeApiError('payload too large', 413),
    )
    await _setupAndSubmit()
    expect(
      await screen.findByTestId('voice-cloning-submit-error'),
    ).toHaveTextContent(/too large/i)
  })

  it('503 → service unavailable copy', async () => {
    mockCloneVoice.mockRejectedValueOnce(
      makeApiError('voice cloning unavailable', 503),
    )
    await _setupAndSubmit()
    expect(
      await screen.findByTestId('voice-cloning-submit-error'),
    ).toHaveTextContent(/temporarily unavailable/i)
  })

  it('404 → agent-not-found copy', async () => {
    mockCloneVoice.mockRejectedValueOnce(
      makeApiError('unknown agent', 404),
    )
    await _setupAndSubmit()
    expect(
      await screen.findByTestId('voice-cloning-submit-error'),
    ).toHaveTextContent(/agent.*could not be found/i)
  })

  it('401/403 → unauthorized copy', async () => {
    mockCloneVoice.mockRejectedValueOnce(
      makeApiError('forbidden', 403),
    )
    await _setupAndSubmit()
    expect(
      await screen.findByTestId('voice-cloning-submit-error'),
    ).toHaveTextContent(/permission/i)
  })

  it('unknown error → generic copy', async () => {
    mockCloneVoice.mockRejectedValueOnce(new Error('boom'))
    await _setupAndSubmit()
    expect(
      await screen.findByTestId('voice-cloning-submit-error'),
    ).toHaveTextContent(/Voice cloning failed/i)
  })
})
