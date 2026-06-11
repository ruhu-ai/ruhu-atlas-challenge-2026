/**
 * Phase 2a-base — VoicePicker tests.
 *
 * The picker is shared infrastructure (used in PersonaTab now and
 * 2b's per-language overrides later), so tests pin its public
 * contract: filter wiring, selection callback, preview play/pause,
 * and the subtle "preview cancels another in flight" guarantee.
 *
 * Audio is mocked at the global level — JSDOM doesn't implement
 * <audio> playback. We swap `window.Audio` for a stub that records
 * play/pause and never actually emits sound.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { VoicePicker } from '@/features/agent-canvas/components/VoicePicker'
import type { VoiceCatalogPage } from '@/types/agent-definition'

const mockListVoiceLibrary = jest.fn()
const mockVoicePreviewUrl = jest.fn()
const mockDeleteVoiceClone = jest.fn()

jest.mock('@/api/services/agent-definition.service', () => ({
  agentDefinitionService: {
    listVoiceLibrary: (...args: unknown[]) => mockListVoiceLibrary(...args),
    voicePreviewUrl: (...args: unknown[]) => mockVoicePreviewUrl(...args),
    deleteVoiceClone: (...args: unknown[]) => mockDeleteVoiceClone(...args),
  },
}))

// VoiceCloningWizard imports the recorder + service. We don't need to
// exercise its internals here — VoicePicker tests just need to verify
// that the Clone button renders and opens the wizard.
jest.mock('@/features/agent-canvas/components/VoiceCloningWizard', () => ({
  VoiceCloningWizard: ({ open }: { open: boolean }) =>
    open ? <div data-testid="voice-cloning-wizard-stub" /> : null,
}))

// ── Audio stub ──────────────────────────────────────────────────────────────
// JSDOM doesn't implement <audio>. We record play/pause calls.

type FakeAudioInstance = {
  play: jest.Mock
  pause: jest.Mock
  addEventListener: jest.Mock
  src: string
}

const fakeAudios: FakeAudioInstance[] = []

class FakeAudio {
  src: string
  play = jest.fn().mockResolvedValue(undefined)
  pause = jest.fn()
  addEventListener = jest.fn()
  constructor(src?: string) {
    this.src = src ?? ''
    fakeAudios.push(this as unknown as FakeAudioInstance)
  }
}

const FAKE_PAGE: VoiceCatalogPage = {
  voices: [
    {
      voice_id: 'en-US-Chirp3-HD-Kore',
      provider: 'vertex_gemini',
      display_name: 'Kore',
      language: 'en-US',
      gender: 'neutral',
      accent: 'American',
      description: 'Calm, measured.',
      sample_text: 'Hi.',
    },
    {
      voice_id: 'en-GB-Chirp3-HD-Aoede',
      provider: 'vertex_gemini',
      display_name: 'Aoede',
      language: 'en-GB',
      gender: 'female',
      accent: 'British',
      description: 'Crisp British.',
      sample_text: 'Good day.',
    },
  ],
  next_cursor: null,
  total_count: 2,
}

function withQueryClient(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return <QueryClientProvider client={client}>{node}</QueryClientProvider>
}

beforeEach(() => {
  jest.clearAllMocks()
  fakeAudios.length = 0
  ;(globalThis as unknown as { Audio: typeof Audio }).Audio = FakeAudio as unknown as typeof Audio
  mockListVoiceLibrary.mockResolvedValue(FAKE_PAGE)
  mockVoicePreviewUrl.mockImplementation(
    (id: string) => `/persona/voices/${id}/preview`,
  )
})

describe('VoicePicker — load and render', () => {
  it('lists voices from the library', async () => {
    render(
      withQueryClient(
        <VoicePicker selectedVoiceId="" onSelect={jest.fn()} />,
      ),
    )
    expect(await screen.findByText('Kore')).toBeInTheDocument()
    expect(screen.getByText('Aoede')).toBeInTheDocument()
  })

  it('marks the active selection', async () => {
    render(
      withQueryClient(
        <VoicePicker
          selectedVoiceId="en-US-Chirp3-HD-Kore"
          onSelect={jest.fn()}
        />,
      ),
    )
    const entry = await screen.findByTestId('voice-picker-entry-en-US-Chirp3-HD-Kore')
    expect(entry).toHaveAttribute('data-selected', 'true')
    // Selected entries don't show a Select button (Selected badge instead).
    expect(
      screen.queryByTestId('voice-picker-select-en-US-Chirp3-HD-Kore'),
    ).not.toBeInTheDocument()
  })

  it('shows an empty state when filters return no voices', async () => {
    mockListVoiceLibrary.mockResolvedValueOnce({
      voices: [],
      next_cursor: null,
      total_count: 0,
    })
    render(
      withQueryClient(
        <VoicePicker selectedVoiceId="" onSelect={jest.fn()} />,
      ),
    )
    expect(
      await screen.findByText(/No voices match these filters/i),
    ).toBeInTheDocument()
  })

  it('shows an error state when the catalog fetch fails', async () => {
    mockListVoiceLibrary.mockRejectedValueOnce(new Error('network'))
    render(
      withQueryClient(
        <VoicePicker selectedVoiceId="" onSelect={jest.fn()} />,
      ),
    )
    expect(
      await screen.findByText(/Failed to load voices/i),
    ).toBeInTheDocument()
  })
})

describe('VoicePicker — filters', () => {
  it('refetches with language filter when typed', async () => {
    render(
      withQueryClient(
        <VoicePicker selectedVoiceId="" onSelect={jest.fn()} />,
      ),
    )
    await screen.findByText('Kore')
    fireEvent.change(screen.getByTestId('voice-picker-language'), {
      target: { value: 'en-GB' },
    })
    await waitFor(() => {
      const lastCall = mockListVoiceLibrary.mock.calls.at(-1)
      expect(lastCall?.[0]?.language).toBe('en-GB')
    })
  })

  it('seeds the language filter from defaultLanguage prop', async () => {
    render(
      withQueryClient(
        <VoicePicker
          selectedVoiceId=""
          onSelect={jest.fn()}
          defaultLanguage="yo-NG"
        />,
      ),
    )
    expect(await screen.findByDisplayValue('yo-NG')).toBeInTheDocument()
    await waitFor(() => {
      const lastCall = mockListVoiceLibrary.mock.calls.at(-1)
      expect(lastCall?.[0]?.language).toBe('yo-NG')
    })
  })
})

describe('VoicePicker — selection', () => {
  it('fires onSelect with voice_id and entry', async () => {
    const onSelect = jest.fn()
    render(
      withQueryClient(
        <VoicePicker selectedVoiceId="" onSelect={onSelect} />,
      ),
    )
    const button = await screen.findByTestId(
      'voice-picker-select-en-GB-Chirp3-HD-Aoede',
    )
    fireEvent.click(button)
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect.mock.calls[0][0]).toBe('en-GB-Chirp3-HD-Aoede')
    expect(onSelect.mock.calls[0][1].display_name).toBe('Aoede')
  })
})

describe('VoicePicker — preview', () => {
  it('starts an audio element when preview is clicked', async () => {
    render(
      withQueryClient(
        <VoicePicker selectedVoiceId="" onSelect={jest.fn()} />,
      ),
    )
    const previewBtn = await screen.findByTestId(
      'voice-picker-preview-en-US-Chirp3-HD-Kore',
    )
    fireEvent.click(previewBtn)
    expect(fakeAudios).toHaveLength(1)
    expect(fakeAudios[0].src).toContain(
      '/persona/voices/en-US-Chirp3-HD-Kore/preview',
    )
    expect(fakeAudios[0].play).toHaveBeenCalled()
  })

  it('cancels an in-flight preview when a different one starts', async () => {
    render(
      withQueryClient(
        <VoicePicker selectedVoiceId="" onSelect={jest.fn()} />,
      ),
    )
    fireEvent.click(
      await screen.findByTestId('voice-picker-preview-en-US-Chirp3-HD-Kore'),
    )
    fireEvent.click(
      screen.getByTestId('voice-picker-preview-en-GB-Chirp3-HD-Aoede'),
    )
    expect(fakeAudios).toHaveLength(2)
    // First audio's pause was called when the second started.
    expect(fakeAudios[0].pause).toHaveBeenCalled()
    expect(fakeAudios[1].play).toHaveBeenCalled()
  })

  it('stops playback when the same preview is clicked twice', async () => {
    render(
      withQueryClient(
        <VoicePicker selectedVoiceId="" onSelect={jest.fn()} />,
      ),
    )
    const btn = await screen.findByTestId(
      'voice-picker-preview-en-US-Chirp3-HD-Kore',
    )
    fireEvent.click(btn)
    fireEvent.click(btn)
    expect(fakeAudios[0].pause).toHaveBeenCalled()
  })
})

// ── Phase 2a-cloning surface ────────────────────────────────────────────

describe('VoicePicker — voice cloning integration', () => {
  it('renders the Clone button by default', async () => {
    render(
      withQueryClient(
        <VoicePicker selectedVoiceId="" onSelect={jest.fn()} />,
      ),
    )
    expect(
      await screen.findByTestId('voice-picker-clone-button'),
    ).toBeInTheDocument()
  })

  it('hides the Clone button when enableCloning is false', async () => {
    render(
      withQueryClient(
        <VoicePicker
          selectedVoiceId=""
          onSelect={jest.fn()}
          enableCloning={false}
        />,
      ),
    )
    await screen.findByText('Kore')
    expect(
      screen.queryByTestId('voice-picker-clone-button'),
    ).not.toBeInTheDocument()
  })

  it('opens the cloning wizard when the Clone button is clicked', async () => {
    render(
      withQueryClient(
        <VoicePicker selectedVoiceId="" onSelect={jest.fn()} />,
      ),
    )
    fireEvent.click(await screen.findByTestId('voice-picker-clone-button'))
    expect(
      screen.getByTestId('voice-cloning-wizard-stub'),
    ).toBeInTheDocument()
  })

  it('renders the Cloned badge + delete action on cloned entries', async () => {
    mockListVoiceLibrary.mockResolvedValueOnce({
      voices: [
        {
          voice_id: 'vc_clone_1',
          provider: 'vertex_gemini_clone',
          display_name: 'CEO Voice',
          language: 'en-US',
          gender: 'neutral',
          accent: null,
          description: 'Custom clone created on 2026-05-09',
          sample_text: null,
        },
      ],
      next_cursor: null,
      total_count: 1,
    })
    render(
      withQueryClient(
        <VoicePicker selectedVoiceId="" onSelect={jest.fn()} />,
      ),
    )
    expect(await screen.findByText('CEO Voice')).toBeInTheDocument()
    expect(screen.getByText('Cloned')).toBeInTheDocument()
    expect(
      screen.getByTestId('voice-picker-delete-vc_clone_1'),
    ).toBeInTheDocument()
  })

  it('calls deleteVoiceClone when the delete button is clicked', async () => {
    mockListVoiceLibrary.mockResolvedValue({
      voices: [
        {
          voice_id: 'vc_clone_1',
          provider: 'vertex_gemini_clone',
          display_name: 'CEO Voice',
          language: 'en-US',
          gender: 'neutral',
          accent: null,
          description: null,
          sample_text: null,
        },
      ],
      next_cursor: null,
      total_count: 1,
    })
    mockDeleteVoiceClone.mockResolvedValue(undefined)
    render(
      withQueryClient(
        <VoicePicker selectedVoiceId="" onSelect={jest.fn()} />,
      ),
    )
    const deleteBtn = await screen.findByTestId(
      'voice-picker-delete-vc_clone_1',
    )
    fireEvent.click(deleteBtn)
    await waitFor(() => expect(mockDeleteVoiceClone).toHaveBeenCalledTimes(1))
    expect(mockDeleteVoiceClone).toHaveBeenCalledWith('vc_clone_1')
  })

  it('does NOT show delete on standard catalog entries', async () => {
    render(
      withQueryClient(
        <VoicePicker selectedVoiceId="" onSelect={jest.fn()} />,
      ),
    )
    await screen.findByText('Kore')
    expect(
      screen.queryByTestId('voice-picker-delete-en-US-Chirp3-HD-Kore'),
    ).not.toBeInTheDocument()
  })
})
