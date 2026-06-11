import {
  forwardRef,
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/atoms/card'
import { Button } from '@/components/atoms/button'
import { AlertTriangle, Loader2, Plus } from 'lucide-react'
import { agentDefinitionService } from '@/api/services/agent-definition.service'
import type {
  ArtifactFollowupHandler,
  AgentSummary,
  AgentDefinition,
} from '@/types/agent-definition'
import type { AgentDocumentEditorHandle } from './AgentDocumentEditor'
import { FollowupHandlersEditor } from './FollowupHandlersEditor'
import { AgentFlowGraph } from '@/features/agent-canvas/components/AgentFlowGraph'
import { TestSurface } from '@/features/agent-canvas/components/TestSurface'

// Tiptap is heavy (~280KB). Only load it when the user opens Document mode.
const AgentDocumentEditor = lazy(() =>
  import('./AgentDocumentEditor').then((m) => ({ default: m.AgentDocumentEditor })),
)
import {
  createBlankAgentDefinition,
  createBlankState,
  getAgentDefinitionWarnings,
} from './utils'

export interface SidebarScenarioItem {
  id: string
  name: string
  isStart: boolean
}

/** Used by external rule/policy pickers that need a flat step list. */
export interface SidebarStepItem {
  id: string
  name: string
}

export interface AgentDefinitionWorkspaceSidebarData {
  agents: AgentSummary[]
  selectedAgentId: string | null
  selectedAgentName: string | null
  scenarios: SidebarScenarioItem[]
  selectedScenarioId: string | null
  factCount: number
}

export interface AgentDefinitionWorkspaceHandle {
  save: () => Promise<boolean>
  hasUnsavedChanges: () => boolean
  addStep: () => void
  selectState: (stateId: string | null) => void
  createAgent: () => Promise<string | null>
  scrollToScenario: (scenarioId: string) => void
}

/** Canvas authoring surfaces. The third value 'test' opens the canvas-level
 * Test surface — chat on the left, ReasoningTimeline on the right. The
 * Document and Graph surfaces remain authoring surfaces; Test is a runtime
 * inspection surface that reads the live in-memory AgentDocument from
 * AgentDocumentProvider (so unsaved edits are testable without saving). */
export type AuthoringSurface = 'document' | 'graph' | 'test'

interface AgentDefinitionWorkspaceProps {
  agentName: string
  selectedAgentId: string | null
  onSelectedAgentIdChange: (agentId: string | null) => void
  onSidebarDataChange: (payload: AgentDefinitionWorkspaceSidebarData) => void
  onDirtyChange: (dirty: boolean) => void
  agentSettingsPanel: ReactNode
  /** When true, automatically create a new agent instead of showing the picker. */
  autoCreate?: boolean
  /** Which authoring surface to render. Defaults to 'graph'. */
  surface?: AuthoringSurface
  /** Used by the Test surface to instantiate the right runtime (chat /
   * voice / multimodal). The workspace can't derive this from `draft`
   * because AgentDefinition doesn't carry agent_type — it lives on the
   * settings panel above. */
  agentType?: 'chat' | 'voice' | 'multimodal'
}


export const AgentDefinitionWorkspace = forwardRef<AgentDefinitionWorkspaceHandle, AgentDefinitionWorkspaceProps>(
  (
    {
      agentName,
      selectedAgentId,
      onSelectedAgentIdChange,
      onSidebarDataChange,
      onDirtyChange,
      agentSettingsPanel,
      autoCreate = false,
      surface = 'document',
      agentType = 'voice',
    },
    ref,
  ) => {
    const queryClient = useQueryClient()
    const [draft, setDraft] = useState<AgentDefinition | null>(null)
    const [selectedStepId, setSelectedStepId] = useState<string | null>(null)
    const [dirty, setDirty] = useState(false)
    const [documentDirty, setDocumentDirty] = useState(false)
    const documentEditorRef = useRef<AgentDocumentEditorHandle | null>(null)
    const autoCreateInFlightRef = useRef(false)

    const agentsQuery = useQuery({
      queryKey: ['agents-list'],
      queryFn: () => agentDefinitionService.listAgents(),
      staleTime: 15_000,
    })

    const agentDefinitionQuery = useQuery({
      queryKey: ['agent-definition', selectedAgentId, 'draft'],
      queryFn: () => agentDefinitionService.getAgentDefinition(selectedAgentId!, 'draft'),
      enabled: !!selectedAgentId,
      staleTime: 5_000,
    })

    // Sidebar scenario list comes from the full AgentDocument. Dedups with
    // the AgentDocumentEditor's identical query key when document mode is open.
    const agentDocumentQuery = useQuery({
      queryKey: ['agent-document', selectedAgentId],
      queryFn: () => agentDefinitionService.getAgentDocument(selectedAgentId!),
      enabled: !!selectedAgentId,
      staleTime: 5_000,
    })

    const [selectedScenarioId, setSelectedScenarioId] = useState<string | null>(null)

    // When the document loads, default the selected scenario to the start.
    useEffect(() => {
      const doc = agentDocumentQuery.data
      if (!doc) return
      setSelectedScenarioId((current) => {
        if (current && doc.scenarios.some((s) => s.id === current)) return current
        return doc.start_scenario_id || doc.scenarios[0]?.id || null
      })
    }, [agentDocumentQuery.data])

    const saveAgentMutation = useMutation({
      mutationFn: async (definition: AgentDefinition) => {
        if (!selectedAgentId) throw new Error('No agent definition selected')
        return agentDefinitionService.updateAgentDefinition(selectedAgentId, definition)
      },
      onSuccess: (response) => {
        setDraft(response.definition)
        setDirty(false)
        queryClient.invalidateQueries({ queryKey: ['agents-list'] })
        queryClient.invalidateQueries({ queryKey: ['agent-definition', response.definition.id] })
      },
    })

    const createAgentMutation = useMutation({
      mutationFn: async () => {
        const definition = createBlankAgentDefinition(agentName)
        definition.name = agentName || definition.name
        // createAgent expects AgentCreateRequest{name, settings, document}.
        // The canvas only has the canvas-flat AgentDefinition; convert by
        // wrapping the steps in a single scenario and using empty settings.
        return agentDefinitionService.createAgent({
          name: definition.name,
          settings: {} as never,
          document: {
            scenarios: [
              {
                id: 'main',
                name: 'Main',
                start_step_id: definition.start_step_id,
                steps: definition.steps.map((step) => ({
                  id: step.id,
                  name: step.name,
                  transitions: step.transitions.map((t) => ({
                    id: t.id,
                    when: t.when,
                    to_step_id: t.to,
                    label: t.natural_reason ?? null,
                    priority: t.priority,
                  })),
                  say: step.say_on_entry ?? null,
                  guards: step.guards ?? [],
                  fact_requirements: step.fact_requirements ?? [],
                  tool_policy: step.tool_policy ?? [],
                })),
              },
            ],
            start_scenario_id: 'main',
            fact_schema: definition.fact_schema,
          } as never,
        })
      },
      onSuccess: (response) => {
        // createAgent returns the wire-shape AgentVersionTargetResponse, not
        // the canvas-flat one. Drop the result and refetch via getAgentDefinition.
        setDraft(null)
        setSelectedStepId(null)
        setDirty(false)
        onSelectedAgentIdChange(response.agent_id)
        queryClient.invalidateQueries({ queryKey: ['agents-list'] })
        autoCreateInFlightRef.current = false
      },
      onError: () => {
        autoCreateInFlightRef.current = false
      },
    })

    useEffect(() => {
      const agents = agentsQuery.data || []
      const doc = agentDocumentQuery.data
      const scenarios: SidebarScenarioItem[] = doc
        ? doc.scenarios.map((s) => ({
            id: s.id,
            name: s.name || s.id,
            isStart: s.id === doc.start_scenario_id,
          }))
        : []
      onSidebarDataChange({
        agents,
        selectedAgentId,
        selectedAgentName: draft?.name || doc?.scenarios[0]?.name || null,
        scenarios,
        selectedScenarioId,
        factCount: draft?.fact_schema.length || doc?.fact_schema?.length || 0,
      })
    }, [
      draft,
      agentsQuery.data,
      agentDocumentQuery.data,
      onSidebarDataChange,
      selectedAgentId,
      selectedScenarioId,
    ])

    useEffect(() => {
      onDirtyChange(surface === 'document' ? documentDirty : dirty)
    }, [dirty, documentDirty, onDirtyChange, surface])

    useEffect(() => {
      if (!agentDefinitionQuery.data || dirty) return
      setDraft(agentDefinitionQuery.data.definition)
      setSelectedStepId((current) => {
        if (!current) return null
        return agentDefinitionQuery.data.definition.steps.some((state) => state.id === current) ? current : null
      })
    }, [dirty, agentDefinitionQuery.data])

    useEffect(() => {
      if (!selectedAgentId) {
        setDraft(null)
        setSelectedStepId(null)
        setDirty(false)
        return
      }
      if (draft && draft.id !== selectedAgentId) {
        setDraft(null)
        setSelectedStepId(null)
        setDirty(false)
      }
    }, [draft, selectedAgentId])

    const agentWarnings = useMemo(() => {
      if (!draft) return []
      return getAgentDefinitionWarnings(draft)
    }, [draft])

    const updateDraft = useCallback((nextAgentDefinition: AgentDefinition) => {
      setDraft(nextAgentDefinition)
      setDirty(true)
    }, [])

    const handleFollowupHandlersChange = useCallback(
      (nextHandlers: ArtifactFollowupHandler[]) => {
        if (!draft) return
        updateDraft({ ...draft, followup_handlers: nextHandlers })
      },
      [draft, updateDraft],
    )

    const addStep = useCallback(() => {
      if (!draft) return
      const nextState = createBlankState()
      updateDraft({
        ...draft,
        steps: [...draft.steps, nextState],
      })
      setSelectedStepId(nextState.id)
    }, [draft, updateDraft])

    const save = useCallback(async () => {
      if (surface === 'document') {
        const editor = documentEditorRef.current
        if (!editor) return false
        return editor.save()
      }
      if (!draft) return false
      try {
        await saveAgentMutation.mutateAsync(draft)
        return true
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Unknown error'
        toast.error(`Agent definition save failed: ${message}`)
        return false
      }
    }, [draft, saveAgentMutation, surface])

    const createAgent = useCallback(async (): Promise<string | null> => {
      try {
        const response = await createAgentMutation.mutateAsync()
        return response.agent_id
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Unknown error'
        toast.error(`Agent definition creation failed: ${message}`)
        return null
      }
    }, [createAgentMutation])

    useEffect(() => {
      if (!autoCreate) return
      if (selectedAgentId || draft) return
      if (createAgentMutation.isPending || autoCreateInFlightRef.current) return

      autoCreateInFlightRef.current = true
      void createAgent()
    }, [autoCreate, createAgent, createAgentMutation.isPending, draft, selectedAgentId])

    const scrollToScenario = useCallback((scenarioId: string) => {
      setSelectedScenarioId(scenarioId)
      // In document mode, ask the editor to scroll. In graph mode this is a
      // no-op for now — graph view doesn't yet support scenario switching.
      documentEditorRef.current?.scrollToScenario(scenarioId)
    }, [])

    useImperativeHandle(
      ref,
      () => ({
        save,
        hasUnsavedChanges: () =>
          surface === 'document' ? documentDirty : dirty,
        addStep,
        selectState: (stateId: string | null) => {
          setSelectedStepId(stateId)
        },
        createAgent,
        scrollToScenario,
      }),
      [addStep, createAgent, dirty, documentDirty, save, scrollToScenario, surface],
    )

    if (agentsQuery.isLoading && !agentsQuery.data) {
      return (
        <div className="flex h-full items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      )
    }

    if (!selectedAgentId && !draft) {
      if (autoCreate) {
        return (
          <div className="flex h-full items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        )
      }
      return (
        <div className="flex h-full items-center justify-center p-6">
          <Card className="max-w-xl border-border bg-card/80">
            <CardHeader>
              <CardTitle>Choose or create an agent definition</CardTitle>
              <CardDescription>
                The new authoring surface edits agent states and explicit exits directly. Scenario and
                step authoring is no longer the source of truth here.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                {(agentsQuery.data || []).map((agent) => (
                  <button
                    key={agent.id}
                    onClick={() => onSelectedAgentIdChange(agent.id)}
                    className="flex w-full items-center justify-between rounded-lg border border-border p-3 text-left transition-colors hover:bg-muted/40"
                  >
                    <div>
                      <p className="font-medium">{agent.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {agent.step_count} states · {agent.version}
                      </p>
                    </div>
                    <span className="text-xs text-muted-foreground">{agent.id}</span>
                  </button>
                ))}
                {(agentsQuery.data || []).length === 0 && (
                  <p className="text-sm text-muted-foreground">No agent definitions exist yet.</p>
                )}
              </div>
                <Button onClick={() => void createAgent()} disabled={createAgentMutation.isPending}>
                {createAgentMutation.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Plus className="mr-2 h-4 w-4" />
                )}
                Create definition
              </Button>
            </CardContent>
          </Card>
        </div>
      )
    }

    if ((agentDefinitionQuery.isLoading || createAgentMutation.isPending) && !draft) {
      return (
        <div className="flex h-full items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      )
    }

    if (!draft) {
      return (
        <div className="flex h-full items-center justify-center p-6">
          <Card className="border-border bg-card/80">
            <CardContent className="p-6 text-center">
              <p className="text-sm text-muted-foreground">
                The selected agent definition could not be loaded. Pick another definition or create a new one.
              </p>
            </CardContent>
          </Card>
        </div>
      )
    }

    if (surface === 'document') {
      return (
        <div className="flex h-full">
          <div className="flex-1 min-w-0">
            {selectedAgentId ? (
              <Suspense
                fallback={
                  <div className="flex h-full items-center justify-center">
                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                  </div>
                }
              >
                <AgentDocumentEditor
                  ref={documentEditorRef}
                  agentId={selectedAgentId}
                  onDirtyChange={setDocumentDirty}
                />
              </Suspense>
            ) : (
              <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
                Select an agent to edit its document.
              </div>
            )}
          </div>
          <div className="w-80 border-l border-white/10 overflow-y-auto">
            {agentSettingsPanel}
          </div>
        </div>
      )
    }

    if (surface === 'test') {
      // Canvas Test surface (Phase 1C). Two-pane chat + ReasoningTimelinePane.
      // Reads live AgentDocument via the AgentDocumentProvider mounted upstream
      // in CleanAgentCanvasLayout — so unsaved edits are testable without
      // saving first. Right-side settings panel deliberately omitted: this
      // surface owns the entire canvas width for chat + reasoning, settings
      // belong on the authoring surfaces.
      if (!selectedAgentId) {
        return (
          <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
            Select an agent to test it.
          </div>
        )
      }
      return (
        <TestSurface
          agentId={selectedAgentId}
          agentName={agentName}
          agentType={agentType}
          agentStatus="draft"
        />
      )
    }

    return (
      <div className="flex h-full">
        <div className="flex-1 min-w-0">
          <AgentFlowGraph />
        </div>
        <div className="w-80 border-l border-white/10 overflow-y-auto">
          <div className="flex h-full flex-col overflow-hidden">
            <div className="flex-1 overflow-y-auto">
              {agentSettingsPanel}
              {agentWarnings.length > 0 && (
                <div className="border-t border-white/10 p-4">
                  <div className="rounded-lg border border-amber-500/25 bg-amber-500/10 p-4">
                    <div className="mb-2 flex items-center gap-2">
                      <AlertTriangle className="h-4 w-4 text-amber-300" />
                      <h4 className="text-sm font-medium text-amber-100">Agent warnings</h4>
                    </div>
                    <ul className="space-y-1 text-xs text-amber-50/90">
                      {agentWarnings.map((warning, index) => (
                        <li key={`${warning}-${index}`} className="flex items-start gap-2">
                          <span className="mt-[5px] h-1.5 w-1.5 shrink-0 rounded-full bg-amber-300" />
                          <span>{warning}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}
              <div className="border-t border-white/10 p-4">
                <FollowupHandlersEditor
                  agentDefinition={draft}
                  onChange={handleFollowupHandlersChange}
                />
              </div>
            </div>
          </div>
        </div>
      </div>
    )
  },
)

AgentDefinitionWorkspace.displayName = 'AgentDefinitionWorkspace'
