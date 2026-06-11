/**
 * LibraryView UX contract (Step 3 of the Library redesign).
 *
 * Pins the user-facing behaviour we care about:
 *   - The 6 categories render: All / Mine / API / Integrations / Built-in / MCP
 *   - "Mine" combines code + composite kinds
 *   - "+ New callable" defaults to creating a code-kind callable
 *   - Composite creation is gated behind ADVANCED_KINDS_ENABLED
 *   - "Used by N steps" badge surfaces the usage index
 *   - Inline display-name edit calls the update mutation
 *
 * If you remove or rename any of these the test fails — that's intentional.
 * These are the contracts the redesign promised; quietly breaking them is
 * exactly what the test exists to prevent.
 *
 * Radix component note: Tabs activates on ``onMouseDown`` (with button=0)
 * and DropdownMenu activates on ``onPointerDown``. Plain ``fireEvent.click``
 * is a no-op for these primitives in jsdom — use the helpers below.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'

import type { ToolDefinition } from '@/api/services/tools.service'

const mockListDefinitions = jest.fn()
const mockCreateDefinition = jest.fn()
const mockUpdateDefinition = jest.fn()
const mockDeleteDefinition = jest.fn()
const mockApiGet = jest.fn()

jest.mock('@/api/client', () => ({
  apiClient: {
    get: (...args: unknown[]) => mockApiGet(...args),
    post: jest.fn().mockResolvedValue({}),
    put: jest.fn().mockResolvedValue({}),
    patch: jest.fn().mockResolvedValue({}),
    delete: jest.fn().mockResolvedValue({}),
  },
  cancelAllRequests: jest.fn(),
}))

jest.mock('@/api/services/tools.service', () => ({
  toolService: {
    listDefinitions: (...args: unknown[]) => mockListDefinitions(...args),
    createDefinition: (...args: unknown[]) => mockCreateDefinition(...args),
    updateDefinition: (...args: unknown[]) => mockUpdateDefinition(...args),
    deleteDefinition: (...args: unknown[]) => mockDeleteDefinition(...args),
  },
}))

const mockUseCallableUsageIndex = jest.fn()
jest.mock('@/features/agent-canvas/hooks/useCallableUsageIndex', () => ({
  useCallableUsageIndex: (...args: unknown[]) => mockUseCallableUsageIndex(...args),
}))

// Mocked at suite level so each test can flip ADVANCED_KINDS_ENABLED. The
// production module reads ``import.meta.env`` which ts-jest can't parse;
// mocking it sidesteps that and gives the tests a clean knob.
const mockFeatureFlags = { ADVANCED_KINDS_ENABLED: false }
jest.mock('@/utils/feature-flags', () => mockFeatureFlags)

// CodeEditor is lazy-loaded — stub out so Suspense resolves immediately.
jest.mock('@/components/molecules/code-editor', () => ({
  CodeEditor: () => null,
}))

function buildTool(overrides: Partial<ToolDefinition>): ToolDefinition {
  return {
    tool_definition_id: 'td_default',
    organization_id: 'org_test',
    connection_id: null,
    kind: 'code',
    tool_ref: 'code.default',
    function_name: null,
    display_name: 'Default Code Callable',
    description: 'Default fixture',
    endpoint_path: null,
    http_method: 'POST',
    input_schema: {},
    output_schema: {},
    timeout_ms: 5000,
    read_only: false,
    enabled: true,
    metadata: {},
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function renderLibrary() {
  // Lazy-import here so the jest.mocks above register first.
  const { LibraryView } = require('@/features/agent-canvas/components/LibraryView')
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <MemoryRouter initialEntries={['/library']}>
      <QueryClientProvider client={queryClient}>
        <LibraryView />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

// Radix Tabs activates the trigger on ``onMouseDown`` with primary button.
function selectTab(name: RegExp | string) {
  const tab = screen.getByRole('tab', { name })
  fireEvent.mouseDown(tab, { button: 0 })
}

// Radix DropdownMenu opens on ``onPointerDown``. jsdom's PointerEvent
// support is patchy; the keyboard path (``Enter``/``ArrowDown``) is the
// most reliable trigger for tests and exercises the same onOpenToggle
// codepath as a click would.
function openDropdown(label: string) {
  const trigger = screen.getByLabelText(label)
  trigger.focus()
  fireEvent.keyDown(trigger, { key: 'Enter' })
}

beforeEach(() => {
  mockListDefinitions.mockReset()
  mockCreateDefinition.mockReset()
  mockUpdateDefinition.mockReset()
  mockDeleteDefinition.mockReset()
  mockApiGet.mockReset()
  mockUseCallableUsageIndex.mockReset()
  mockFeatureFlags.ADVANCED_KINDS_ENABLED = false

  mockApiGet.mockResolvedValue({ items: [] })
  mockUseCallableUsageIndex.mockReturnValue({
    index: new Map(),
    isLoading: false,
    loadedCount: 0,
    agentCount: 0,
  })
})

describe('LibraryView — categories', () => {
  it('renders the 6 filter chips: All, Mine, API, Integrations, Built-in, MCP', async () => {
    mockListDefinitions.mockResolvedValue([])
    renderLibrary()

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: 'All' })).toBeInTheDocument()
    })
    expect(screen.getByRole('tab', { name: 'Mine' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'API' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Integrations' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Built-in' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'MCP' })).toBeInTheDocument()
  })

  it('Mine filter shows code + composite, hides API/integration/builtin', async () => {
    const tools: ToolDefinition[] = [
      buildTool({
        tool_definition_id: 'td_code',
        kind: 'code',
        tool_ref: 'code.fetch_user',
        display_name: 'Fetch User',
      }),
      buildTool({
        tool_definition_id: 'td_composite',
        kind: 'composite',
        tool_ref: 'composite.upsell',
        display_name: 'Upsell flow',
      }),
      buildTool({
        tool_definition_id: 'td_api',
        kind: 'api',
        connection_id: 'conn_acme',
        tool_ref: 'api.acme_charge',
        display_name: 'Acme Charge',
      }),
      buildTool({
        tool_definition_id: 'td_integration',
        kind: 'integration',
        connection_id: 'conn_calendar',
        tool_ref: 'calendar.create_event',
        display_name: 'Create Calendar Event',
      }),
      buildTool({
        tool_definition_id: 'td_builtin',
        kind: 'builtin',
        tool_ref: 'knowledge.lookup',
        display_name: 'Knowledge Lookup',
      }),
    ]
    mockListDefinitions.mockResolvedValue(tools)
    renderLibrary()

    await waitFor(() => {
      expect(screen.getByText('Fetch User')).toBeInTheDocument()
    })

    selectTab('Mine')

    await waitFor(() => {
      expect(screen.queryByText('Acme Charge')).not.toBeInTheDocument()
    })
    expect(screen.getByText('Fetch User')).toBeInTheDocument()
    expect(screen.getByText('Upsell flow')).toBeInTheDocument()
    expect(screen.queryByText('Create Calendar Event')).not.toBeInTheDocument()
    expect(screen.queryByText('Knowledge Lookup')).not.toBeInTheDocument()
  })
})

describe('LibraryView — single primary creation flow', () => {
  it('"+ New callable" creates a code-kind callable by default', async () => {
    mockListDefinitions.mockResolvedValue([])
    mockCreateDefinition.mockResolvedValue(
      buildTool({ tool_definition_id: 'td_new', kind: 'code', tool_ref: 'code.untitled_x' }),
    )
    renderLibrary()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /New callable/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /New callable/i }))

    await waitFor(() => {
      expect(mockCreateDefinition).toHaveBeenCalled()
    })
    const payload = mockCreateDefinition.mock.calls[0][0]
    expect(payload.kind).toBe('code')
  })

  it('hides the composite creation entry when ADVANCED_KINDS_ENABLED is false (default)', async () => {
    mockListDefinitions.mockResolvedValue([])
    renderLibrary()

    await waitFor(() => {
      expect(screen.getByLabelText('More callable creation options')).toBeInTheDocument()
    })

    openDropdown('More callable creation options')

    await waitFor(() => {
      expect(screen.getByText(/New code callable/)).toBeInTheDocument()
    })
    expect(screen.queryByText(/New composite \(advanced\)/i)).not.toBeInTheDocument()
  })

  it('shows the composite creation entry when ADVANCED_KINDS_ENABLED is true', async () => {
    mockFeatureFlags.ADVANCED_KINDS_ENABLED = true
    mockListDefinitions.mockResolvedValue([])
    renderLibrary()

    await waitFor(() => {
      expect(screen.getByLabelText('More callable creation options')).toBeInTheDocument()
    })

    openDropdown('More callable creation options')

    await waitFor(() => {
      expect(screen.getByText(/New composite \(advanced\)/i)).toBeInTheDocument()
    })
  })
})

describe('LibraryView — used-by badge', () => {
  it('renders "Used by N steps" when the usage index has bindings for the ref', async () => {
    const tool = buildTool({
      tool_definition_id: 'td_used',
      kind: 'code',
      tool_ref: 'code.fetch_user',
      display_name: 'Fetch User',
    })
    mockListDefinitions.mockResolvedValue([tool])
    mockUseCallableUsageIndex.mockReturnValue({
      index: new Map([
        [
          'code.fetch_user',
          [
            { agentId: 'agent_a', stepId: 'step_1' },
            { agentId: 'agent_a', stepId: 'step_2' },
          ],
        ],
      ]),
      isLoading: false,
      loadedCount: 1,
      agentCount: 1,
    })
    renderLibrary()

    await waitFor(() => {
      expect(screen.getByText('Fetch User')).toBeInTheDocument()
    })
    expect(screen.getByText(/Used by 2 steps/)).toBeInTheDocument()
  })
})

describe('LibraryView — inline editable display name', () => {
  it('clicking the title swaps to an input and saving calls updateDefinition with display_name', async () => {
    const tool = buildTool({
      tool_definition_id: 'td_name',
      kind: 'code',
      tool_ref: 'code.fetch_user',
      display_name: 'Fetch User',
    })
    mockListDefinitions.mockResolvedValue([tool])
    mockUpdateDefinition.mockResolvedValue(tool)
    renderLibrary()

    // Open the detail page by clicking the row.
    await waitFor(() => {
      expect(screen.getByText('Fetch User')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Fetch User'))

    // Detail-page editable title button.
    const editTitleButton = await screen.findByRole('button', {
      name: /Edit display name/i,
    })
    fireEvent.click(editTitleButton)

    const input = await screen.findByLabelText('Display name')
    fireEvent.change(input, { target: { value: 'Fetch User Profile' } })
    // Press Enter to commit (saves and exits edit mode).
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => {
      expect(mockUpdateDefinition).toHaveBeenCalledWith(
        'td_name',
        expect.objectContaining({ display_name: 'Fetch User Profile' }),
      )
    })
  })

  it('does not show an editable title for framework-managed kinds (built-in)', async () => {
    const tool = buildTool({
      tool_definition_id: 'td_builtin',
      kind: 'builtin',
      tool_ref: 'knowledge.lookup',
      display_name: 'Knowledge Lookup',
      read_only: true,
    })
    mockListDefinitions.mockResolvedValue([tool])
    renderLibrary()

    await waitFor(() => {
      expect(screen.getByText('Knowledge Lookup')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Knowledge Lookup'))

    // Detail page open — verify no edit affordance for the title.
    await screen.findByRole('button', { name: 'Library' }) // back button
    expect(
      screen.queryByRole('button', { name: /Edit display name/i }),
    ).not.toBeInTheDocument()
  })
})

describe('LibraryView — source badges', () => {
  it('shows "via {connection name}" for connection-bound callables', async () => {
    mockApiGet.mockResolvedValue({
      items: [
        {
          connection_id: 'conn_calendar',
          display_name: 'Google Calendar',
          provider: 'google_calendar',
          base_url: 'https://www.googleapis.com',
        },
      ],
    })
    const tool = buildTool({
      tool_definition_id: 'td_int',
      kind: 'integration',
      connection_id: 'conn_calendar',
      tool_ref: 'calendar.create_event',
      display_name: 'Create Calendar Event',
    })
    mockListDefinitions.mockResolvedValue([tool])
    renderLibrary()

    await waitFor(() => {
      expect(screen.getByText('Create Calendar Event')).toBeInTheDocument()
    })
    const row = screen.getByText('Create Calendar Event').closest('button')
    expect(row).not.toBeNull()
    expect(within(row as HTMLElement).getByText(/via Google Calendar/)).toBeInTheDocument()
  })
})

describe('LibraryView — Callable functions tab', () => {
  it('lists eligible kinds (code/composite/builtin), excludes self, and persists ref toggles', async () => {
    const own = buildTool({
      tool_definition_id: 'td_self',
      kind: 'code',
      tool_ref: 'code.fetch_user',
      display_name: 'Fetch User',
    })
    const sibling = buildTool({
      tool_definition_id: 'td_sibling',
      kind: 'code',
      tool_ref: 'code.classify_tier',
      display_name: 'Classify Tier',
    })
    const builtin = buildTool({
      tool_definition_id: 'td_builtin',
      kind: 'builtin',
      tool_ref: 'knowledge.lookup',
      display_name: 'Knowledge Lookup',
      read_only: true,
    })
    const apiTool = buildTool({
      tool_definition_id: 'td_api',
      kind: 'api',
      connection_id: 'conn_x',
      tool_ref: 'api.charge',
      display_name: 'Charge',
    })
    mockListDefinitions.mockResolvedValue([own, sibling, builtin, apiTool])
    mockUpdateDefinition.mockResolvedValue(own)
    renderLibrary()

    await waitFor(() => {
      expect(screen.getByText('Fetch User')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Fetch User'))

    // Switch to "Callable functions" tab.
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: 'Callable functions' })).toBeInTheDocument()
    })
    selectTab('Callable functions')

    // Sibling code + builtin appear; api row does NOT (api belongs to APIs tab); self is excluded.
    await waitFor(() => {
      expect(screen.getByText('Classify Tier')).toBeInTheDocument()
    })
    expect(screen.getByText('Knowledge Lookup')).toBeInTheDocument()
    expect(screen.queryByText('Charge')).not.toBeInTheDocument()
    // Self ('Fetch User') still shows in the panel header but not as a candidate row checkbox.
    const checkbox = screen.queryByRole('checkbox', { name: 'Bind code.fetch_user' })
    expect(checkbox).not.toBeInTheDocument()

    // Tick a candidate and save.
    const siblingCheckbox = screen.getByRole('checkbox', { name: 'Bind code.classify_tier' })
    fireEvent.click(siblingCheckbox)
    // Computed alias chip appears once selected.
    await waitFor(() => {
      expect(screen.getByText(/Call as/i)).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Save callable bindings' }))

    await waitFor(() => {
      expect(mockUpdateDefinition).toHaveBeenCalledWith(
        'td_self',
        expect.objectContaining({
          metadata: expect.objectContaining({
            callable_refs: ['code.classify_tier'],
          }),
        }),
      )
    })
  })
})

describe('LibraryView — APIs tab', () => {
  it('groups eligible api/integration kinds by connection and persists toggles', async () => {
    mockApiGet.mockResolvedValue({
      items: [
        {
          connection_id: 'conn_calendar',
          display_name: 'Google Calendar',
          provider: 'google_calendar',
        },
        {
          connection_id: 'conn_acme',
          display_name: 'Acme Bank',
          provider: 'custom',
        },
      ],
    })
    const own = buildTool({
      tool_definition_id: 'td_self',
      kind: 'code',
      tool_ref: 'code.merge',
      display_name: 'Merge user',
    })
    const cal = buildTool({
      tool_definition_id: 'td_cal',
      kind: 'integration',
      connection_id: 'conn_calendar',
      tool_ref: 'calendar.create_event',
      display_name: 'Create Event',
    })
    const charge = buildTool({
      tool_definition_id: 'td_charge',
      kind: 'api',
      connection_id: 'conn_acme',
      tool_ref: 'api.charge',
      display_name: 'Charge',
    })
    const codeOther = buildTool({
      tool_definition_id: 'td_code_other',
      kind: 'code',
      tool_ref: 'code.helper',
      display_name: 'Helper',
    })
    mockListDefinitions.mockResolvedValue([own, cal, charge, codeOther])
    mockUpdateDefinition.mockResolvedValue(own)
    renderLibrary()

    await waitFor(() => {
      expect(screen.getByText('Merge user')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Merge user'))

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: 'APIs' })).toBeInTheDocument()
    })
    selectTab('APIs')

    await waitFor(() => {
      expect(screen.getByText('Google Calendar')).toBeInTheDocument()
    })
    expect(screen.getByText('Acme Bank')).toBeInTheDocument()
    expect(screen.getByText('Create Event')).toBeInTheDocument()
    expect(screen.getByText('Charge')).toBeInTheDocument()
    // Code candidates do NOT appear in the APIs tab.
    expect(screen.queryByText('Helper')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('checkbox', { name: 'Bind calendar.create_event' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save API bindings' }))

    await waitFor(() => {
      expect(mockUpdateDefinition).toHaveBeenCalledWith(
        'td_self',
        expect.objectContaining({
          metadata: expect.objectContaining({
            callable_refs: ['calendar.create_event'],
          }),
        }),
      )
    })
  })

  it('preselects refs already on metadata.callable_refs and shows the computed alias', async () => {
    mockApiGet.mockResolvedValue({
      items: [
        {
          connection_id: 'conn_acme',
          display_name: 'Acme Bank',
          provider: 'custom',
        },
      ],
    })
    const own = buildTool({
      tool_definition_id: 'td_self',
      kind: 'code',
      tool_ref: 'code.merge',
      display_name: 'Merge user',
      metadata: { callable_refs: ['api.charge'] },
    })
    const charge = buildTool({
      tool_definition_id: 'td_charge',
      kind: 'api',
      connection_id: 'conn_acme',
      tool_ref: 'api.charge',
      display_name: 'Charge',
    })
    mockListDefinitions.mockResolvedValue([own, charge])
    renderLibrary()

    await waitFor(() => {
      expect(screen.getByText('Merge user')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Merge user'))

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: 'APIs' })).toBeInTheDocument()
    })
    selectTab('APIs')

    const checkbox = await screen.findByRole('checkbox', { name: 'Bind api.charge' })
    expect(checkbox).toBeChecked()
    // Default alias for ``api.charge`` is the last segment ``charge``;
    // the row should render a "Call as charge(...)" chip. Find the
    // closest chip-like element that contains the full alias phrase.
    const matches = screen.getAllByText((_, el) => {
      if (!el) return false
      const text = el.textContent ?? ''
      return /Call as\s*charge\s*\(\.\.\.\)/i.test(text)
    })
    expect(matches.length).toBeGreaterThan(0)
  })
})
