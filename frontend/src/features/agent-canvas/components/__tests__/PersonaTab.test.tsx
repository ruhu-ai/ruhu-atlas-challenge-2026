/**
 * Persona tab — frontend contract for the Phase 1 Persona Studio surface.
 *
 * The tests pin three things:
 *
 * 1. Identity (cosmetic) saves go through `updateCosmeticPersona`. PATCH
 *    semantics are verified by checking the exact payload — the user
 *    rejected a previous "no migration shims" / "clean implementation"
 *    pattern, so partial drafts must serialise to nullable fields, not
 *    silently dropped keys.
 *
 * 2. Behaviour saves go through `updateBehavioralPersona` against the
 *    *current draft document*. The frontend has to read the doc first; if
 *    we squash siblings (`scenarios`, `fact_schema`) we lose work. The
 *    test verifies both arguments.
 *
 * 3. Validation rules are enforced client-side. Persona name with
 *    `<script>` rejects, non-HTTPS avatar rejects — and these block save.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'sonner'

import { PersonaTab } from '@/features/agent-canvas/components/PersonaTab'

const mockGetAgentSettings = jest.fn()
const mockGetBehavioralPersona = jest.fn()
const mockUpdateCosmeticPersona = jest.fn()
const mockUpdateBehavioralPersona = jest.fn()
const mockUploadPersonaAvatar = jest.fn()

jest.mock('@/api/services/agent-definition.service', () => ({
  agentDefinitionService: {
    getAgentSettings: (...args: unknown[]) => mockGetAgentSettings(...args),
    getBehavioralPersona: (...args: unknown[]) => mockGetBehavioralPersona(...args),
    updateCosmeticPersona: (...args: unknown[]) => mockUpdateCosmeticPersona(...args),
    updateBehavioralPersona: (...args: unknown[]) => mockUpdateBehavioralPersona(...args),
    uploadPersonaAvatar: (...args: unknown[]) => mockUploadPersonaAvatar(...args),
  },
}))

const FAKE_AGENT_ID = 'agent-123'

const FAKE_DOCUMENT = {
  version: '3.0',
  start_scenario_id: 'main',
  scenarios: [
    {
      id: 'main',
      name: 'Main',
      start_step_id: 'entry',
      order: 0,
      entry_channels: [],
      resources: {},
      flow_layout: {},
      steps: [{ id: 'entry', name: 'Welcome', transitions: [] }],
    },
  ],
  scenario_routes: [],
  fact_schema: [],
  metadata: {},
}

function withQueryClient(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return (
    <QueryClientProvider client={client}>
      {node}
      <Toaster />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  jest.clearAllMocks()
  mockGetAgentSettings.mockResolvedValue({
    agent_id: FAKE_AGENT_ID,
    // agent_type=voice so the Voice subsection renders. Tests that need
    // a chat-only agent override this in their own beforeEach.
    settings: { persona: null, agent_type: 'voice' },
  })
  mockGetBehavioralPersona.mockResolvedValue({
    persona: {
      formality: 'neutral',
      emoji_policy: 'sparingly',
      restricted_topics: [],
      topic_enforcement: 'log_only',
      voice_provider: 'vertex_gemini',
      voice_id: 'en-US-Chirp3-HD-Kore',
      voice_speed: 1.0,
      voice_monthly_budget_cents: null,
      // Phase 2b — multi-language defaults match BehavioralPersona schema.
      primary_language: 'en',
      allowed_languages: ['en'],
      auto_switch_language: 'off',
      language_switch_confidence_threshold: 0.8,
      language_switch_min_chars: 10,
      language_switch_debounce_turns: 1,
      language_switch_policy: 'mirror_user',
      unsupported_language_policy: 'explain_and_offer',
      voice_id_overrides: {},
      locale_code: 'en-US',
      cultural_calendar_enabled: false,
    },
    document: FAKE_DOCUMENT,
  })
  mockUpdateCosmeticPersona.mockResolvedValue({
    agent_id: FAKE_AGENT_ID,
    settings: { persona: null },
  })
  mockUpdateBehavioralPersona.mockResolvedValue(FAKE_DOCUMENT)
  mockUploadPersonaAvatar.mockReset()
})

describe('PersonaTab — render and load', () => {
  it('shows both Identity and Behaviour sections after loading', async () => {
    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    expect(await screen.findByTestId('persona-identity-section')).toBeInTheDocument()
    expect(screen.getByTestId('persona-behavior-section')).toBeInTheDocument()
  })

  it('disables save buttons when nothing has changed', async () => {
    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    const cosmeticSave = await screen.findByTestId('persona-save-cosmetic')
    expect(cosmeticSave).toBeDisabled()
    const behavioralSave = screen.getByTestId('persona-save-behavioral')
    expect(behavioralSave).toBeDisabled()
  })
})

describe('PersonaTab — cosmetic identity save', () => {
  it('PATCHes only persona on the settings endpoint with nulls for unset fields', async () => {
    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    const nameInput = await screen.findByLabelText('Persona name')
    fireEvent.change(nameInput, { target: { value: 'Maya' } })

    const save = screen.getByTestId('persona-save-cosmetic')
    await waitFor(() => expect(save).not.toBeDisabled())
    fireEvent.click(save)

    await waitFor(() => expect(mockUpdateCosmeticPersona).toHaveBeenCalledTimes(1))
    expect(mockUpdateCosmeticPersona).toHaveBeenCalledWith(FAKE_AGENT_ID, {
      persona_name: 'Maya',
      pronouns: null,
      pronouns_custom: null,
      avatar_url: null,
      role_title: null,
      greeting_template: null,
      signoff_template: null,
    })
  })

  it('blocks save when persona_name contains a disallowed character', async () => {
    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    const nameInput = await screen.findByLabelText('Persona name')
    fireEvent.change(nameInput, { target: { value: '<script>' } })

    const save = screen.getByTestId('persona-save-cosmetic')
    expect(save).toBeDisabled()
    expect(screen.getByText('Contains disallowed character')).toBeInTheDocument()
    expect(mockUpdateCosmeticPersona).not.toHaveBeenCalled()
  })

  it('blocks save when avatar_url is not HTTPS', async () => {
    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    // Phase 2d: the field is now "Avatar" (single input + Upload button).
    const avatar = await screen.findByLabelText('Avatar')
    fireEvent.change(avatar, { target: { value: 'http://example.com/face.png' } })

    const save = screen.getByTestId('persona-save-cosmetic')
    expect(save).toBeDisabled()
    expect(screen.getByText('Avatar URL must start with https://')).toBeInTheDocument()
  })
})

describe('PersonaTab — behavioural save', () => {
  it('PUTs the agent document with metadata.persona, preserving siblings', async () => {
    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))

    const topicInput = await screen.findByTestId('persona-topic-input')
    fireEvent.change(topicInput, { target: { value: 'competitor pricing' } })
    fireEvent.click(screen.getByTestId('persona-topic-add'))

    const save = screen.getByTestId('persona-save-behavioral')
    await waitFor(() => expect(save).not.toBeDisabled())
    fireEvent.click(save)

    await waitFor(() => expect(mockUpdateBehavioralPersona).toHaveBeenCalledTimes(1))
    const [agentId, persona, document] = mockUpdateBehavioralPersona.mock.calls[0]
    expect(agentId).toBe(FAKE_AGENT_ID)
    expect(persona).toEqual({
      formality: 'neutral',
      emoji_policy: 'sparingly',
      restricted_topics: ['competitor pricing'],
      // Phase 2c: topic_enforcement defaults to log_only on new personas
      // and the saved payload includes it explicitly so the kernel always
      // sees a valid mode (no more silent "best-effort" assumption).
      topic_enforcement: 'log_only',
      // Phase 2a-base: voice fields are part of the behavioural payload
      // so the Voice picker selection persists alongside formality etc.
      voice_provider: 'vertex_gemini',
      voice_id: 'en-US-Chirp3-HD-Kore',
      voice_speed: 1.0,
      voice_monthly_budget_cents: null,
      // Phase 2b: multi-language fields persist alongside topic + voice.
      primary_language: 'en',
      allowed_languages: ['en'],
      auto_switch_language: 'off',
      language_switch_confidence_threshold: 0.8,
      language_switch_min_chars: 10,
      language_switch_debounce_turns: 1,
      language_switch_policy: 'mirror_user',
      unsupported_language_policy: 'explain_and_offer',
      voice_id_overrides: {},
      locale_code: 'en-US',
      cultural_calendar_enabled: false,
    })
    // The document is required so siblings (scenarios, fact_schema)
    // survive — squashing them would corrupt the agent definition.
    expect(document).toBe(FAKE_DOCUMENT)
  })

  it('saves the picked topic_enforcement policy on the behavioural payload', async () => {
    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))

    // Add a topic so the Save button enables for the policy change.
    const topicInput = await screen.findByTestId('persona-topic-input')
    fireEvent.change(topicInput, { target: { value: 'pricing' } })
    fireEvent.click(screen.getByTestId('persona-topic-add'))

    // Switch enforcement mode via the select. Radix renders the trigger
    // as a button with role=combobox; fireEvent.change on the underlying
    // hidden select element is the supported testing-library escape hatch.
    const select = screen.getByTestId('persona-topic-enforcement-select')
    fireEvent.click(select)
    const option = await screen.findByRole('option', { name: /Block & retry/i })
    fireEvent.click(option)

    const save = screen.getByTestId('persona-save-behavioral')
    fireEvent.click(save)

    await waitFor(() => expect(mockUpdateBehavioralPersona).toHaveBeenCalled())
    const [, persona] = mockUpdateBehavioralPersona.mock.calls[0]
    expect(persona.topic_enforcement).toBe('block_and_retry')
  })

  it('shows mode-specific caption that reflects the active policy', async () => {
    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    // Default starts at log_only, so the canary caption is visible.
    expect(
      await screen.findByText(/Log only — observable, but does not block/i),
    ).toBeInTheDocument()
    // Strong-wording marketing-critical copy must NOT appear yet.
    expect(
      screen.queryByText(/Block & retry — violating responses do not reach/i),
    ).not.toBeInTheDocument()
  })

  it('shows the Pending publish badge once behaviour is dirty', async () => {
    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    await screen.findByTestId('persona-behavior-section')
    expect(screen.getByText('Draft')).toBeInTheDocument()

    const topicInput = screen.getByTestId('persona-topic-input')
    fireEvent.change(topicInput, { target: { value: 'pricing' } })
    fireEvent.click(screen.getByTestId('persona-topic-add'))

    expect(screen.getByText('Pending publish')).toBeInTheDocument()
  })

  it('rejects topics with disallowed characters and prevents add', async () => {
    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    const topicInput = await screen.findByTestId('persona-topic-input')
    fireEvent.change(topicInput, { target: { value: 'bad <script>' } })
    expect(screen.getByText('Contains disallowed character')).toBeInTheDocument()
    expect(screen.getByTestId('persona-topic-add')).toBeDisabled()
  })
})

describe('PersonaTab — Voice subsection (Phase 2a-base)', () => {
  it('renders for voice agents', async () => {
    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    expect(await screen.findByTestId('persona-voice-section')).toBeInTheDocument()
    expect(screen.getByTestId('persona-voice-speed')).toBeInTheDocument()
  })

  it('hides for chat-only agents', async () => {
    mockGetAgentSettings.mockResolvedValueOnce({
      agent_id: FAKE_AGENT_ID,
      settings: { persona: null, agent_type: 'chat' },
    })
    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    // Wait for behaviour section to mount so we know data has loaded.
    await screen.findByTestId('persona-behavior-section')
    expect(screen.queryByTestId('persona-voice-section')).not.toBeInTheDocument()
  })

  it('voice_speed slider updates the dirty state and saved payload', async () => {
    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    const slider = await screen.findByTestId('persona-voice-speed')
    fireEvent.change(slider, { target: { value: '1.2' } })

    const save = screen.getByTestId('persona-save-behavioral')
    await waitFor(() => expect(save).not.toBeDisabled())
    fireEvent.click(save)

    await waitFor(() => expect(mockUpdateBehavioralPersona).toHaveBeenCalled())
    const [, persona] = mockUpdateBehavioralPersona.mock.calls[0]
    expect(persona.voice_speed).toBeCloseTo(1.2, 5)
  })
})

describe('PersonaTab — Languages subsection (Phase 2b)', () => {
  it('renders the Languages section', async () => {
    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    expect(
      await screen.findByTestId('persona-languages-section'),
    ).toBeInTheDocument()
  })

  it('hides stability controls when auto-switch is off', async () => {
    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    await screen.findByTestId('persona-languages-section')
    // Default = off; sliders shouldn't render.
    expect(screen.queryByTestId('persona-confidence-threshold')).not.toBeInTheDocument()
    expect(screen.queryByTestId('persona-debounce-turns')).not.toBeInTheDocument()
  })

  it('shows stability controls when auto-switch flips on', async () => {
    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    await screen.findByTestId('persona-languages-section')

    // Open the auto-switch select and choose On.
    fireEvent.click(screen.getByTestId('persona-auto-switch'))
    const onOption = await screen.findByRole('option', {
      name: /On — match user/i,
    })
    fireEvent.click(onOption)

    expect(
      await screen.findByTestId('persona-confidence-threshold'),
    ).toBeInTheDocument()
    expect(screen.getByTestId('persona-debounce-turns')).toBeInTheDocument()
  })

  it('saves the picked language fields on the behavioural payload', async () => {
    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    await screen.findByTestId('persona-languages-section')

    // Switch primary language to Yoruba.
    fireEvent.click(screen.getByTestId('persona-primary-language'))
    const yoOption = await screen.findByRole('option', { name: 'Yoruba' })
    fireEvent.click(yoOption)

    // Toggle cultural calendar.
    fireEvent.click(screen.getByTestId('persona-cultural-calendar'))

    const save = screen.getByTestId('persona-save-behavioral')
    await waitFor(() => expect(save).not.toBeDisabled())
    fireEvent.click(save)

    await waitFor(() => expect(mockUpdateBehavioralPersona).toHaveBeenCalled())
    const [, persona] = mockUpdateBehavioralPersona.mock.calls[0]
    expect(persona.primary_language).toBe('yo')
    expect(persona.cultural_calendar_enabled).toBe(true)
  })

  it('adds and removes allowed languages', async () => {
    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    await screen.findByTestId('persona-languages-section')

    // Add Yoruba via the "Add a language…" select.
    fireEvent.click(screen.getByTestId('persona-add-language'))
    const yoOption = await screen.findByRole('option', { name: 'Yoruba' })
    fireEvent.click(yoOption)

    // The chip should now be visible.
    expect(
      await screen.findByTestId('persona-allowed-language-yo'),
    ).toBeInTheDocument()

    // Click the chip to remove (won't remove the last one — but yo can go).
    fireEvent.click(screen.getByTestId('persona-allowed-language-yo'))
    expect(
      screen.queryByTestId('persona-allowed-language-yo'),
    ).not.toBeInTheDocument()
  })

  it('does not allow removing the last allowed language', async () => {
    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    await screen.findByTestId('persona-languages-section')

    const enChip = screen.getByTestId('persona-allowed-language-en')
    fireEvent.click(enChip)
    // Still there — last-one removal is blocked.
    expect(screen.getByTestId('persona-allowed-language-en')).toBeInTheDocument()
  })
})

describe('PersonaTab — avatar upload (Phase 2d)', () => {
  // The Identity section exposes a hidden file input + visible Upload button.
  // Tests pin: success path, the two client-side gates (size + MIME), and
  // the server-error mapping (413 / 422). Server-side validation is covered
  // by tests/test_persona_avatar.py — these tests just verify the UI wiring.

  function makeFakeFile({
    name = 'avatar.png',
    type = 'image/png',
    size = 1024,
  }: { name?: string; type?: string; size?: number } = {}): File {
    const file = new File(['x'], name, { type })
    // jsdom's File ignores the constructor's content for size; override.
    Object.defineProperty(file, 'size', { value: size })
    return file
  }

  it('uploads a valid PNG and writes the returned URL into Avatar', async () => {
    mockUploadPersonaAvatar.mockResolvedValue({
      agent_id: FAKE_AGENT_ID,
      avatar_url: `/agents/${FAKE_AGENT_ID}/persona/avatar`,
      content_type: 'image/png',
      width: 512,
      height: 512,
      updated_at: '2026-05-09T00:00:00Z',
    })

    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    const fileInput = (await screen.findByTestId(
      'persona-avatar-upload-input',
    )) as HTMLInputElement

    const file = makeFakeFile({ size: 200 * 1024 })
    fireEvent.change(fileInput, { target: { files: [file] } })

    await waitFor(() => expect(mockUploadPersonaAvatar).toHaveBeenCalledTimes(1))
    expect(mockUploadPersonaAvatar).toHaveBeenCalledWith(FAKE_AGENT_ID, file)

    // The Avatar text input picks up the server-returned URL on success
    // so the next "Save identity" persists it on the persona.
    const avatar = (await screen.findByLabelText('Avatar')) as HTMLInputElement
    await waitFor(() =>
      expect(avatar.value).toBe(`/agents/${FAKE_AGENT_ID}/persona/avatar`),
    )
  })

  it('rejects files larger than 2MB without hitting the network', async () => {
    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    const fileInput = (await screen.findByTestId(
      'persona-avatar-upload-input',
    )) as HTMLInputElement

    const oversized = makeFakeFile({ size: 3 * 1024 * 1024 })
    fireEvent.change(fileInput, { target: { files: [oversized] } })

    expect(
      await screen.findByText(/Image is too large\. Max 2MB/i),
    ).toBeInTheDocument()
    expect(mockUploadPersonaAvatar).not.toHaveBeenCalled()
  })

  it('rejects unsupported MIME types client-side (e.g. SVG)', async () => {
    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    const fileInput = (await screen.findByTestId(
      'persona-avatar-upload-input',
    )) as HTMLInputElement

    const svg = makeFakeFile({
      name: 'evil.svg',
      type: 'image/svg+xml',
      size: 1024,
    })
    fireEvent.change(fileInput, { target: { files: [svg] } })

    expect(
      await screen.findByText(/Unsupported format\. Use JPEG, PNG, or WebP\./i),
    ).toBeInTheDocument()
    expect(mockUploadPersonaAvatar).not.toHaveBeenCalled()
  })

  it('shows the size-cap message when the server returns 413', async () => {
    mockUploadPersonaAvatar.mockRejectedValue(
      Object.assign(new Error('payload too large'), { status: 413 }),
    )

    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    const fileInput = (await screen.findByTestId(
      'persona-avatar-upload-input',
    )) as HTMLInputElement

    // Within client-side cap so the request actually fires.
    const file = makeFakeFile({ size: 1.5 * 1024 * 1024 })
    fireEvent.change(fileInput, { target: { files: [file] } })

    await waitFor(() => expect(mockUploadPersonaAvatar).toHaveBeenCalled())
    expect(
      await screen.findByText(/Image is too large\. Max 2MB/i),
    ).toBeInTheDocument()
  })

  it('shows the validation message when the server returns 422 (e.g. MIME-vs-magic mismatch)', async () => {
    mockUploadPersonaAvatar.mockRejectedValue(
      Object.assign(
        new Error('Declared MIME does not match decoded image format.'),
        { status: 422 },
      ),
    )

    render(withQueryClient(<PersonaTab agentId={FAKE_AGENT_ID} />))
    const fileInput = (await screen.findByTestId(
      'persona-avatar-upload-input',
    )) as HTMLInputElement

    // Declared as PNG but presumably some other content — server rejects.
    const file = makeFakeFile({ size: 100 * 1024 })
    fireEvent.change(fileInput, { target: { files: [file] } })

    await waitFor(() => expect(mockUploadPersonaAvatar).toHaveBeenCalled())
    expect(
      await screen.findByText(/Declared MIME does not match/i),
    ).toBeInTheDocument()
  })
})
