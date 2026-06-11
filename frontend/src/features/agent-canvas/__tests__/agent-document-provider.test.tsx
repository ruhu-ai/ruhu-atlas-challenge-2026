/**
 * AgentDocumentProvider contract.
 *
 * Pins the Phase 2 unification invariants so a future refactor can't
 * silently break them:
 *   1. updateDocument flips isDirty=true.
 *   2. save() success clears isDirty when no edits raced the save.
 *   3. save() success keeps isDirty=true when an edit raced the save —
 *      never silently drops the unsaved edit.
 *   4. updateDocument is reflected in the value returned to all consumers
 *      (the "two state worlds" failure mode that orphaned the Graph view
 *      stays closed at the data layer).
 *   5. Save reads the freshly-updated document, not a stale closure
 *      (synchronous updateDocument(...) ; save() must save the new state).
 */
import { ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import {
  AgentDocumentProvider,
  useAgentDocument,
} from '@/features/agent-canvas/contexts/AgentDocumentContext'
import type { AgentDocument } from '@/types/agent-document'

const mockGetAgentDocument = jest.fn()
const mockUpdateAgentDocument = jest.fn()

jest.mock('@/api/services/agent-definition.service', () => ({
  agentDefinitionService: {
    getAgentDocument: (...args: unknown[]) => mockGetAgentDocument(...args),
    updateAgentDocument: (...args: unknown[]) => mockUpdateAgentDocument(...args),
  },
}))

const SEED_DOCUMENT: AgentDocument = {
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
      steps: [
        {
          id: 'entry',
          name: 'Entry',
          transitions: [],
        },
      ],
    },
  ],
  scenario_routes: [],
  fact_schema: [],
  agent_capability_manifest: null,
  metadata: {},
} as AgentDocument

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <AgentDocumentProvider agentId="agent-1">
        {children}
      </AgentDocumentProvider>
    </QueryClientProvider>
  )
  return Wrapper
}

describe('AgentDocumentProvider — Phase 2 unification contract', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockGetAgentDocument.mockResolvedValue(SEED_DOCUMENT)
  })

  it('updateDocument flips isDirty true; the new state is visible to consumers', async () => {
    const { result } = renderHook(() => useAgentDocument(), { wrapper: makeWrapper() })

    await waitFor(() => {
      expect(result.current.document.scenarios[0]?.steps[0]?.name).toBe('Entry')
      expect(result.current.isDirty).toBe(false)
    })

    act(() => {
      result.current.updateDocument((prev) => ({
        ...prev,
        scenarios: prev.scenarios.map((scenario) => ({
          ...scenario,
          steps: scenario.steps.map((step) =>
            step.id === 'entry' ? { ...step, name: 'Welcome' } : step,
          ),
        })),
      }))
    })

    expect(result.current.isDirty).toBe(true)
    expect(result.current.document.scenarios[0]?.steps[0]?.name).toBe('Welcome')
  })

  it('save() success clears isDirty when no edits raced the save', async () => {
    mockUpdateAgentDocument.mockImplementation(async (_id: string, doc: AgentDocument) => doc)

    const { result } = renderHook(() => useAgentDocument(), { wrapper: makeWrapper() })

    await waitFor(() => expect(result.current.document.scenarios.length).toBe(1))

    act(() => {
      result.current.updateDocument((prev) => ({
        ...prev,
        scenarios: prev.scenarios.map((scenario) => ({
          ...scenario,
          steps: scenario.steps.map((step) => ({ ...step, name: 'Updated' })),
        })),
      }))
    })
    expect(result.current.isDirty).toBe(true)

    let saveResult: boolean | undefined
    await act(async () => {
      saveResult = await result.current.save()
    })

    expect(saveResult).toBe(true)
    expect(mockUpdateAgentDocument).toHaveBeenCalledTimes(1)
    expect(result.current.isDirty).toBe(false)
  })

  it('save() success keeps isDirty true when an edit raced the save', async () => {
    // Yield one microtask inside the mutation. That gap is where the test's
    // synchronous "race" updateDocument call lands — bumping the edit
    // counter past the save's snapshot, which is what the contract checks.
    mockUpdateAgentDocument.mockImplementation(async (_id: string, doc: AgentDocument) => {
      await Promise.resolve()
      return doc
    })

    const { result } = renderHook(() => useAgentDocument(), { wrapper: makeWrapper() })

    await waitFor(() => expect(result.current.document.scenarios.length).toBe(1))

    await act(async () => {
      result.current.updateDocument((prev) => ({
        ...prev,
        scenarios: prev.scenarios.map((s) => ({
          ...s,
          steps: s.steps.map((step) => ({ ...step, name: 'First edit' })),
        })),
      }))
      const savePromise = result.current.save()
      // Race: synchronously update again while mutationFn is paused on
      // its `await Promise.resolve()`. Bumps editCounter past the snapshot.
      result.current.updateDocument((prev) => ({
        ...prev,
        scenarios: prev.scenarios.map((s) => ({
          ...s,
          steps: s.steps.map((step) => ({ ...step, name: 'Second edit (raced)' })),
        })),
      }))
      await savePromise
    })

    // Contract: isDirty stays true because a fresh edit happened during
    // the save — clearing dirty would silently drop it.
    expect(result.current.isDirty).toBe(true)
    // Current document reflects the SECOND edit, not the save response.
    expect(result.current.document.scenarios[0]?.steps[0]?.name).toBe(
      'Second edit (raced)',
    )
  })

  it('save() reads the freshly-updated document, not a stale closure', async () => {
    // Holder object so TS doesn't narrow `savedDocument` to `null` after
    // initialization — closure mutations aren't tracked by TS.
    const savedRef: { value: AgentDocument | null } = { value: null }
    mockUpdateAgentDocument.mockImplementation(async (_id: string, doc: AgentDocument) => {
      savedRef.value = doc
      return doc
    })

    const { result } = renderHook(() => useAgentDocument(), { wrapper: makeWrapper() })

    await waitFor(() => expect(result.current.document.scenarios.length).toBe(1))

    // Synchronous updateDocument followed immediately by save — the save
    // MUST persist the new state, not the closure-captured pre-update one.
    await act(async () => {
      result.current.updateDocument((prev) => ({
        ...prev,
        scenarios: prev.scenarios.map((s) => ({
          ...s,
          steps: s.steps.map((step) => ({ ...step, name: 'Synchronous edit' })),
        })),
      }))
      await result.current.save()
    })

    expect(savedRef.value?.scenarios[0]?.steps[0]?.name).toBe('Synchronous edit')
  })

  it('save() failure surfaces false; isDirty remains true', async () => {
    mockUpdateAgentDocument.mockImplementation(async () => {
      throw new Error('network down')
    })

    const { result } = renderHook(() => useAgentDocument(), { wrapper: makeWrapper() })

    await waitFor(() => expect(result.current.document.scenarios.length).toBe(1))

    act(() => {
      result.current.updateDocument((prev) => ({
        ...prev,
        scenarios: prev.scenarios.map((s) => ({
          ...s,
          steps: s.steps.map((step) => ({ ...step, name: 'Will fail' })),
        })),
      }))
    })

    let saveResult: boolean | undefined
    await act(async () => {
      saveResult = await result.current.save()
    })

    expect(saveResult).toBe(false)
    expect(result.current.isDirty).toBe(true)
  })
})
