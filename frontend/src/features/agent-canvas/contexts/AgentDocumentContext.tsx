// Canonical agent state container.
//
// Both Document (TipTap) and Graph (AgentFlowGraph) read/write through
// useAgentDocument(). If you find yourself building a parallel state path
// (TanStack-Query-direct, local-only state, separate snapshot of the
// AgentDocument), you're orphaning one of the surfaces — that's how the
// Graph view got lost the first time. See Phase 2 of the canvas
// unification before adding any new state path.
import {
  type ReactNode,
  createContext,
  forwardRef,
  useCallback,
  useContext,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { agentDefinitionService } from '@/api/services/agent-definition.service'
import {
  createDefaultAgentDocument,
  createScenario,
  createStep,
  deriveStartScenarioId,
  normalizeDocument,
} from '@/features/agent-canvas/utils/agentDocumentFactory'
import type {
  AgentDocument,
  AgentScenario,
  AgentStep,
} from '@/types/agent-document'

// ────────────────────────────────────────────────────────────────────────────
// Context shape
// ────────────────────────────────────────────────────────────────────────────

export interface AgentDocumentContextValue {
  document: AgentDocument
  selectedScenarioId: string | null
  selectedStepId: string | null
  startScenarioId: string | null
  isLoading: boolean
  isError: boolean
  hasAgentId: boolean
  saving: boolean
  /** Reactive dirty flag. Flips true on any updateDocument; flips false on
   * save success (provided no edits raced the save). Reactive so consumers
   * re-render — pair with the imperative handle's `hasUnsavedChanges()` for
   * non-reactive reads. */
  isDirty: boolean
  updateDocument: (updater: (prev: AgentDocument) => AgentDocument) => void
  updateScenario: (scenarioId: string, updater: (scenario: AgentScenario) => AgentScenario) => void
  updateStep: (scenarioId: string, stepId: string, updater: (step: AgentStep) => AgentStep) => void
  addScenario: () => void
  deleteScenario: (scenarioId: string) => void
  addStep: (scenarioId: string) => void
  deleteStep: (scenarioId: string, stepId: string) => void
  setSelectedScenarioId: (id: string | null) => void
  setSelectedStepId: (id: string | null) => void
  setStartScenarioId: (id: string) => void
  /** Persist the in-memory document. Returns true on success.
   *
   * Save-while-typing semantics: if `updateDocument` runs between save start
   * and save success, the response is NOT absorbed (would clobber the new
   * edit) and `isDirty` remains true. On the next save attempt, the latest
   * state is sent. */
  save: () => Promise<boolean>
}

const AgentDocumentContext = createContext<AgentDocumentContextValue | null>(null)

export function useAgentDocument(): AgentDocumentContextValue {
  const value = useContext(AgentDocumentContext)
  if (value == null) {
    throw new Error('useAgentDocument must be used inside <AgentDocumentProvider>')
  }
  return value
}

// ────────────────────────────────────────────────────────────────────────────
// Provider — owns query, mutation, and mutation helpers
// ────────────────────────────────────────────────────────────────────────────

export interface AgentDocumentProviderHandle {
  save: () => Promise<boolean>
  hasUnsavedChanges: () => boolean
  getDocument: () => AgentDocument
  selectScenario: (id: string) => void
  addScenario: () => void
  deleteScenario: (id: string) => void
}

export interface AgentDocumentProviderProps {
  agentId: string | undefined
  onDirtyChange?: (dirty: boolean) => void
  onScenariosChange?: (
    scenarios: AgentScenario[],
    selectedId: string | null,
    startScenarioId: string | null,
  ) => void
  children: ReactNode
}

export const AgentDocumentProvider = forwardRef<AgentDocumentProviderHandle, AgentDocumentProviderProps>(
  ({ agentId, onDirtyChange, onScenariosChange, children }, ref) => {
    const queryClient = useQueryClient()
    // Two storage locations for the same logical "is dirty" — kept in sync
    // by `setDirty`. Justification: state for reactive consumers (context
    // value triggers re-render); ref for non-reactive reads inside the
    // server-load effect (gating that effect on a state value would create
    // a re-render loop with `setDocument`).
    const isDirtyRef = useRef(false)
    const [isDirty, setIsDirty] = useState(false)
    const [document, setDocument] = useState<AgentDocument>(createDefaultAgentDocument())
    // `documentRef` mirrors `document` synchronously inside the setDocument
    // updater. Used by `handleSave` so a synchronous `updateDocument(...) ;
    // save()` sequence saves the freshly-updated document — without this,
    // save's closure would capture stale state.
    const documentRef = useRef<AgentDocument>(document)
    // Increments on every updateDocument. Snapshotted at save start; if it
    // advanced by save success, edits raced the save — we leave isDirty=true
    // and don't clobber current state with the (now-stale) server response.
    const editCounterRef = useRef(0)
    const saveStartedAtCounterRef = useRef(0)
    const [selectedScenarioId, setSelectedScenarioId] = useState<string | null>(null)
    const [selectedStepId, setSelectedStepId] = useState<string | null>(null)

    const setDirty = useCallback((nextDirty: boolean) => {
      isDirtyRef.current = nextDirty
      setIsDirty(nextDirty)
      onDirtyChange?.(nextDirty)
    }, [onDirtyChange])

    const { data: serverDocument, isLoading, isError } = useQuery({
      queryKey: ['agent-document', agentId],
      queryFn: () => agentDefinitionService.getAgentDocument(agentId!),
      enabled: !!agentId,
      staleTime: 30_000,
    })

    useEffect(() => {
      if (serverDocument === undefined) return
      if (isDirtyRef.current) return
      const nextDocument = normalizeDocument(serverDocument ?? createDefaultAgentDocument())
      const nextScenario =
        nextDocument.scenarios.find((scenario) => scenario.id === selectedScenarioId)
        ?? nextDocument.scenarios.find((scenario) => scenario.id === deriveStartScenarioId(nextDocument))
        ?? nextDocument.scenarios[0]
      setDocument(nextDocument)
      documentRef.current = nextDocument
      setSelectedScenarioId((current) => current ?? deriveStartScenarioId(nextDocument) ?? nextDocument.scenarios[0]?.id ?? null)
      setSelectedStepId((current) => current ?? nextScenario?.start_step_id ?? nextScenario?.steps[0]?.id ?? null)
      setDirty(serverDocument == null)
    }, [selectedScenarioId, serverDocument, setDirty])

    const updateDocument = useCallback((updater: (previous: AgentDocument) => AgentDocument) => {
      editCounterRef.current += 1
      // Compute next from the synchronous documentRef mirror — NOT from the
      // setState updater, whose callback runs lazily at React's next commit.
      // Updating documentRef synchronously here means a `updateDocument(...);
      // save()` sequence saves the new state immediately.
      const next = normalizeDocument(updater(documentRef.current))
      documentRef.current = next
      setDocument(next)
      setDirty(true)
    }, [setDirty])

    const saveMutation = useMutation({
      mutationFn: async (nextDocument: AgentDocument) =>
        agentDefinitionService.updateAgentDocument(agentId!, nextDocument),
      onSuccess: (response) => {
        const racedDuringSave = editCounterRef.current !== saveStartedAtCounterRef.current
        if (!racedDuringSave) {
          // Safe to absorb server normalizations (trimmed strings, defaulted
          // fields) — no concurrent edits to clobber.
          setDocument(response)
          documentRef.current = response
          setDirty(false)
          toast.success('Agent document saved')
        } else {
          // User typed during save. Keep current document + isDirty=true so
          // the unsaved edit isn't silently dropped. Next save will pick it up.
          toast.info('Saved — your in-flight edits remain unsaved')
        }
        queryClient.invalidateQueries({ queryKey: ['agent-document', agentId] })
        queryClient.invalidateQueries({ queryKey: ['agents'] })
      },
      onError: (error: Error) => {
        toast.error(`Save failed: ${error.message}`)
      },
    })

    const handleSave = useCallback(async (): Promise<boolean> => {
      if (!agentId) return false
      // Snapshot the edit counter SYNCHRONOUSLY at save-call time. TanStack
      // Query's mutateAsync schedules mutationFn via a microtask, so any
      // snapshot taken inside mutationFn would run AFTER any synchronous
      // racing updateDocument calls — defeating the race detection.
      saveStartedAtCounterRef.current = editCounterRef.current
      try {
        // Read from documentRef so the freshly-updated state is saved even
        // if `document` state hasn't propagated through render yet.
        await saveMutation.mutateAsync(normalizeDocument(documentRef.current))
        return true
      } catch {
        return false
      }
    }, [agentId, saveMutation])

    useEffect(() => {
      onScenariosChange?.(document.scenarios, selectedScenarioId, deriveStartScenarioId(document))
    }, [document, onScenariosChange, selectedScenarioId])

    useEffect(() => {
      const scenario =
        document.scenarios.find((item) => item.id === selectedScenarioId)
        ?? document.scenarios[0]
        ?? null
      if (!scenario) {
        if (selectedStepId !== null) setSelectedStepId(null)
        return
      }
      const hasSelectedStep = scenario.steps.some((step) => step.id === selectedStepId)
      if (hasSelectedStep) return
      setSelectedStepId(scenario.start_step_id ?? scenario.steps[0]?.id ?? null)
    }, [document.scenarios, selectedScenarioId, selectedStepId])

    const updateScenario = useCallback((scenarioId: string, updater: (scenario: AgentScenario) => AgentScenario) => {
      updateDocument((previous) => ({
        ...previous,
        scenarios: previous.scenarios
          .map((scenario) => (scenario.id === scenarioId ? updater(scenario) : scenario))
          .map((scenario, index) => ({ ...scenario, order: index })),
      }))
    }, [updateDocument])

    const addScenario = useCallback(() => {
      const scenario = createScenario(document.scenarios.length)
      updateDocument((previous) => {
        const next = [...previous.scenarios, scenario].map((item, index) => ({ ...item, order: index }))
        return {
          ...previous,
          scenarios: next,
          start_scenario_id: previous.start_scenario_id || scenario.id,
        }
      })
      setSelectedScenarioId(scenario.id)
      setSelectedStepId(scenario.start_step_id)
    }, [document.scenarios.length, updateDocument])

    const deleteScenario = useCallback((scenarioId: string) => {
      updateDocument((previous) => {
        const remaining = previous.scenarios.filter((scenario) => scenario.id !== scenarioId)
        const nextScenarios = remaining.length > 0 ? remaining : [createScenario(0)]
        const nextStartScenarioId = nextScenarios.some((scenario) => scenario.id === previous.start_scenario_id)
          ? previous.start_scenario_id
          : nextScenarios[0].id
        return {
          ...previous,
          scenarios: nextScenarios.map((scenario, index) => ({ ...scenario, order: index })),
          scenario_routes: (previous.scenario_routes ?? []).filter(
            (route) => route.from_scenario_id !== scenarioId && route.to_scenario_id !== scenarioId,
          ),
          start_scenario_id: nextStartScenarioId,
        }
      })
    }, [updateDocument])

    const addStep = useCallback((scenarioId: string) => {
      const step = createStep(
        document.scenarios.find((scenario) => scenario.id === scenarioId)?.steps.length ?? 0,
      )
      updateScenario(scenarioId, (scenario) => {
        return { ...scenario, steps: [...scenario.steps, step] }
      })
      setSelectedScenarioId(scenarioId)
      setSelectedStepId(step.id)
    }, [document.scenarios, updateScenario])

    const deleteStep = useCallback((scenarioId: string, stepId: string) => {
      updateDocument((previous) => {
        const scenarios = previous.scenarios.map((scenario) => {
          if (scenario.id !== scenarioId) return scenario
          const remainingSteps = scenario.steps.filter((step) => step.id !== stepId)
          const nextSteps = remainingSteps.length > 0 ? remainingSteps : [createStep(0)]
          return {
            ...scenario,
            steps: nextSteps,
            start_step_id: nextSteps.some((step) => step.id === scenario.start_step_id)
              ? scenario.start_step_id
              : nextSteps[0].id,
          }
        }).map((scenario) => ({
          ...scenario,
          steps: scenario.steps.map((step) => ({
            ...step,
            transitions: step.transitions.filter((transition) => transition.to_step_id !== stepId),
          })),
        }))
        return { ...previous, scenarios }
      })
    }, [updateDocument])

    const updateStep = useCallback((scenarioId: string, stepId: string, updater: (step: AgentStep) => AgentStep) => {
      updateScenario(scenarioId, (scenario) => ({
        ...scenario,
        steps: scenario.steps.map((step) => (step.id === stepId ? updater(step) : step)),
      }))
    }, [updateScenario])

    const setStartScenarioId = useCallback((id: string) => {
      updateDocument((previous) => ({ ...previous, start_scenario_id: id }))
    }, [updateDocument])

    useImperativeHandle(ref, () => ({
      save: handleSave,
      hasUnsavedChanges: () => isDirtyRef.current,
      getDocument: () => normalizeDocument(document),
      selectScenario: (id: string) => setSelectedScenarioId(id),
      addScenario,
      deleteScenario,
    }), [addScenario, deleteScenario, document, handleSave])

    const startScenarioId = deriveStartScenarioId(document)

    const value = useMemo<AgentDocumentContextValue>(() => ({
      document,
      selectedScenarioId,
      startScenarioId,
      isLoading,
      isError,
      hasAgentId: !!agentId,
      saving: saveMutation.isPending,
      isDirty,
      updateDocument,
      updateScenario,
      updateStep,
      addScenario,
      deleteScenario,
      addStep,
      deleteStep,
      setSelectedScenarioId,
      selectedStepId,
      setSelectedStepId,
      setStartScenarioId,
      save: handleSave,
    }), [
      addScenario,
      addStep,
      deleteScenario,
      deleteStep,
      document,
      agentId,
      handleSave,
      isDirty,
      isError,
      isLoading,
      saveMutation.isPending,
      selectedScenarioId,
      selectedStepId,
      setStartScenarioId,
      startScenarioId,
      updateDocument,
      updateScenario,
      updateStep,
    ])

    return <AgentDocumentContext.Provider value={value}>{children}</AgentDocumentContext.Provider>
  },
)

AgentDocumentProvider.displayName = 'AgentDocumentProvider'
