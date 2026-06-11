import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FileJson, GitBranch, Loader2, Plus, Save, Rocket, Play, Sparkles } from 'lucide-react'
import { toast } from 'sonner'
import { DashboardLayout } from '@/layouts/dashboard-layout'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/atoms/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/atoms/dialog'
import { AgentCanvasSidebar, type SidebarView } from '@/features/agent-canvas/components/AgentCanvasSidebar'
import { AgentSettingsPanel } from '@/features/agent-canvas/components/AgentSettingsPanel'
import { SupportingDocsView } from '@/features/agent-canvas/components/SupportingDocsView'
import { UnifiedTestInterface } from '@/features/agent-canvas/components/UnifiedTestInterface'
import { WidgetSettingsContent } from '@/pages/widget-settings'
import { knowledgeBaseService } from '@/api/services/knowledge-base.service'
import type { KnowledgeDocument } from '@/api/services/knowledge-base.service'
import { agentDefinitionService } from '@/api/services/agent-definition.service'
import {
  AgentDefinitionWorkspace,
  type AuthoringSurface,
  type SidebarScenarioItem,
  type AgentDefinitionWorkspaceHandle,
  type AgentDefinitionWorkspaceSidebarData,
} from '@/features/agent-canvas/agent-definition/AgentDefinitionWorkspace'
import { AgentDocumentProvider } from '@/features/agent-canvas/contexts/AgentDocumentContext'
import { AtlasAIPanel } from '@/features/agent-canvas/components/AtlasAIPanel'
import { AgentPublishView } from '@/features/agent-canvas/agent-definition/AgentPublishView'
import { AgentVersionsView } from '@/features/agent-canvas/agent-definition/AgentVersionsView'
import { AgentEvaluationView } from '@/features/agent-canvas/agent-definition/AgentEvaluationView'
import { AgentRulesView } from '@/features/agent-canvas/agent-definition/AgentRulesView'
import { AgentIntegrationsTab } from '@/features/agent-canvas/components/AgentIntegrationsTab'
import { LibraryView } from '@/features/agent-canvas/components/LibraryView'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/atoms/tabs'
import { PersonaTab } from '@/features/agent-canvas/components/PersonaTab'
import { PublishBlockerModal } from '@/features/agent-canvas/components/PublishBlockerModal'
import type { AgentSettings, AgentSummary } from '@/types/agent-definition'
import { buildInitialAgentSettings, defaultAgentSettings } from '@/features/agent-canvas/utils/agentDefaults'

const SUPPORTED_VIEWS: SidebarView[] = [
  'canvas',
  'persona',
  'rules',
  'supporting-docs',
  'library',
  'widget',
  'testing',
  'releases',
  'versions',
]

function defaultAgentName(agentType: AgentSettings['agent_type']): string {
  if (agentType === 'chat') return 'Untitled Chat Agent'
  if (agentType === 'multimodal') return 'Untitled Multimodal Agent'
  return 'Untitled Voice Agent'
}

function formatUpdatedLabel(date?: string | null): string {
  if (!date) return 'Never'
  const value = new Date(date)
  if (Number.isNaN(value.getTime())) return 'Never'
  return value.toLocaleString()
}

function areAgentSettingsEqual(left: AgentSettings, right: AgentSettings): boolean {
  return JSON.stringify(left) === JSON.stringify(right)
}

export default function CleanAgentCanvasLayout() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const isNewRoute = id === 'new'
  const requestedAgentType = (searchParams.get('type') as AgentSettings['agent_type'] | null) || 'voice'
  const requestedView = (searchParams.get('view') as SidebarView | null) || 'canvas'
  const requestedSurface: AuthoringSurface = (() => {
    const value = searchParams.get('surface')
    if (value === 'document' || value === 'graph' || value === 'test') return value
    // Document is the default — it's the editing surface (TipTap, scenarios
    // + steps). Graph is read-only viz that navigates back to Document on
    // click; landing users on the read-only surface forces an extra click
    // before they can do anything actionable.
    return 'document'
  })()
  const initialKbIds = isNewRoute
    ? ((location.state as { initialKbIds?: string[] } | null)?.initialKbIds ?? [])
    : []
  const initialAgentSettings = buildInitialAgentSettings(requestedAgentType, initialKbIds)

  const [sidebarView, setSidebarView] = useState<SidebarView>('canvas')
  const [authoringSurface, setAuthoringSurface] = useState<AuthoringSurface>(requestedSurface)
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(isNewRoute ? null : id ?? null)
  const previousRouteIdRef = useRef<string | null>(null)
  const [sidebarAgents, setSidebarAgents] = useState<AgentSummary[]>([])
  const [sidebarAgentName, setSidebarAgentName] = useState<string | null>(null)
  const [sidebarScenarios, setSidebarScenarios] = useState<SidebarScenarioItem[]>([])
  const [sidebarSelectedScenarioId, setSidebarSelectedScenarioId] = useState<string | null>(null)
  const [sidebarAgentFactCount, setSidebarAgentFactCount] = useState(0)
  const [hasUnsavedAgentChanges, setHasUnsavedAgentChanges] = useState(false)
  const [agentSettingsDraft, setAgentSettingsDraft] = useState<AgentSettings>(
    defaultAgentSettings(requestedAgentType),
  )
  const [agentSettingsDirty, setAgentSettingsDirty] = useState(false)
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null)
  const [againstVersionId, setAgainstVersionId] = useState<string | null>(null)
  const workspaceRef = useRef<AgentDefinitionWorkspaceHandle | null>(null)
  const [isTestDialogOpen, setIsTestDialogOpen] = useState(false)
  // Atlas slide-in panel — AI copilot for authoring (API discovery, code
  // suggestions, workflow generation). The panel itself is overlay-shaped
  // (isOpen/onClose), so we toggle here and render alongside the layout.
  const [isAtlasOpen, setIsAtlasOpen] = useState(false)

  useEffect(() => {
    if (isNewRoute) {
      if (previousRouteIdRef.current && previousRouteIdRef.current !== 'new') {
        setSelectedAgentId(null)
      }
    } else if (id && id !== selectedAgentId) {
      setSelectedAgentId(id)
    }
    previousRouteIdRef.current = id ?? null
  }, [id, isNewRoute, selectedAgentId])

  useEffect(() => {
    if (SUPPORTED_VIEWS.includes(requestedView) && requestedView !== sidebarView) {
      setSidebarView(requestedView)
    }
  }, [requestedView, sidebarView])

  useEffect(() => {
    if (requestedSurface !== authoringSurface) {
      setAuthoringSurface(requestedSurface)
    }
  }, [authoringSurface, requestedSurface])

  const [agentNameDraft, setAgentNameDraft] = useState<string | null>(null)
  const [agentNameDirty, setAgentNameDirty] = useState(false)

  // Shared with AgentDefinitionWorkspace + AgentDocumentEditor via the same
  // query key — React Query dedupes the actual fetch.
  const agentDocumentQuery = useQuery({
    queryKey: ['agent-document', selectedAgentId],
    queryFn: () => agentDefinitionService.getAgentDocument(selectedAgentId!),
    enabled: !!selectedAgentId,
    staleTime: 5_000,
  })

  // Steps from the start scenario — used by AgentRulesView's step picker.
  const rulesViewSteps = useMemo(() => {
    const doc = agentDocumentQuery.data
    if (!doc) return []
    const startScenario =
      doc.scenarios.find((s) => s.id === doc.start_scenario_id) ?? doc.scenarios[0]
    if (!startScenario) return []
    return startScenario.steps.map((step) => ({ id: step.id, name: step.name || step.id }))
  }, [agentDocumentQuery.data])

  const agentMetaQuery = useQuery({
    queryKey: ['agent-meta', selectedAgentId],
    queryFn: () => agentDefinitionService.getAgentDefinition(selectedAgentId!, 'draft'),
    enabled: !!selectedAgentId,
    staleTime: 30_000,
  })

  const activeAgentSummary = useMemo(
    () => sidebarAgents.find((agent) => agent.id === selectedAgentId) || null,
    [selectedAgentId, sidebarAgents],
  )
  const agentName = agentNameDraft ?? activeAgentSummary?.name ?? sidebarAgentName ?? agentMetaQuery.data?.definition?.name ?? defaultAgentName(agentSettingsDraft.agent_type)
  const hasUnsavedChanges = hasUnsavedAgentChanges || agentSettingsDirty || agentNameDirty

  // Sync the name draft when agent summary, draft agent name, or meta query loads.
  useEffect(() => {
    if (agentNameDirty) return
    const resolvedName = activeAgentSummary?.name ?? sidebarAgentName ?? agentMetaQuery.data?.definition?.name
    if (resolvedName) {
      setAgentNameDraft(resolvedName)
    }
  }, [activeAgentSummary, sidebarAgentName, agentMetaQuery.data, agentNameDirty])

  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => {
      if (!hasUnsavedChanges) return
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [hasUnsavedChanges])

  const confirmDiscard = useCallback(
    (message = 'You have unsaved changes. Discard them and continue?'): boolean => {
      if (!hasUnsavedChanges) return true
      return window.confirm(message)
    },
    [hasUnsavedChanges],
  )

  const settingsQuery = useQuery({
    queryKey: ['agent-settings', selectedAgentId],
    queryFn: () => agentDefinitionService.getAgentSettings(selectedAgentId!),
    enabled: !!selectedAgentId,
    staleTime: 10_000,
  })

  useEffect(() => {
    if (!selectedAgentId) {
      setAgentSettingsDraft((current) => {
        if (areAgentSettingsEqual(current, initialAgentSettings)) return current
        return initialAgentSettings
      })
      setAgentSettingsDirty(false)
      return
    }

    if (agentSettingsDirty) return
    if (!settingsQuery.data) return

    setAgentSettingsDraft((current) => {
      if (areAgentSettingsEqual(current, settingsQuery.data?.settings)) return current
      return settingsQuery.data!.settings
    })
    setAgentSettingsDirty(false)
  }, [initialAgentSettings, agentSettingsDirty, selectedAgentId, settingsQuery.data])

  const buildCanvasUrl = useCallback(
    (agentId: string | null, view: SidebarView = sidebarView) => {
      const params = new URLSearchParams()
      if (view !== 'canvas') {
        params.set('view', view)
      }
      const query = params.toString()
      if (!agentId) {
        const typeParams = new URLSearchParams(params)
        if (requestedAgentType) {
          typeParams.set('type', requestedAgentType)
        }
        const newQuery = typeParams.toString()
        return `/agents/new/canvas${newQuery ? `?${newQuery}` : ''}`
      }
      return `/agents/${agentId}/canvas${query ? `?${query}` : ''}`
    },
    [requestedAgentType, sidebarView],
  )

  const versionsQuery = useQuery({
    queryKey: ['agent-versions', selectedAgentId],
    queryFn: () => agentDefinitionService.listAgentVersions(selectedAgentId!),
    enabled: !!selectedAgentId,
    staleTime: 5_000,
  })

  const publishReviewQuery = useQuery({
    queryKey: ['agent-publish-review', selectedAgentId],
    queryFn: () => agentDefinitionService.getAgentPublishReview(selectedAgentId!),
    enabled: !!selectedAgentId,
    staleTime: 5_000,
  })

  const diffQuery = useQuery({
    queryKey: ['agent-version-diff', selectedAgentId, selectedVersionId, againstVersionId],
    queryFn: () => agentDefinitionService.getAgentDiff(selectedAgentId!, selectedVersionId!, againstVersionId || undefined),
    enabled: !!selectedAgentId && !!selectedVersionId,
    staleTime: 5_000,
  })

  useEffect(() => {
    const versions = versionsQuery.data || []
    if (versions.length === 0) {
      setSelectedVersionId(null)
      return
    }
    if (selectedVersionId && versions.some((item) => item.version_id === selectedVersionId)) {
      return
    }
    const preferred = versions.find((item) => item.is_current_draft) || versions[versions.length - 1]
    setSelectedVersionId(preferred?.version_id || null)
  }, [selectedVersionId, versionsQuery.data])

  const dataSourcesQuery = useQuery({
    queryKey: ['knowledge-documents', 'published'],
    queryFn: () => knowledgeBaseService.listDocuments({ status: 'published', limit: 100 }),
    enabled: sidebarView === 'supporting-docs',
    staleTime: 30_000,
  })

  const updateSettingsMutation = useMutation({
    mutationFn: async (settings: AgentSettings) => {
      if (!selectedAgentId) throw new Error('No agent definition selected')
      return agentDefinitionService.updateAgentSettings(selectedAgentId, settings)
    },
    onSuccess: (response) => {
      queryClient.setQueryData(['agent-settings', response.agent_id], response)
      setAgentSettingsDraft(response.settings)
      setAgentSettingsDirty(false)
    },
  })

  const [publishBlockerOpen, setPublishBlockerOpen] = useState(false)

  const publishMutation = useMutation({
    mutationFn: async () => {
      if (!selectedAgentId) throw new Error('No agent definition selected')
      return agentDefinitionService.publishAgent(selectedAgentId)
    },
    onSuccess: (response) => {
      queryClient.invalidateQueries({ queryKey: ['agent-versions', response.agent_id] })
      queryClient.invalidateQueries({ queryKey: ['agent-publish-review', response.agent_id] })
      queryClient.invalidateQueries({ queryKey: ['agents-list'] })
      toast.success('Agent published')
    },
    onError: (error: Error) => {
      // 409 from the publish endpoint means publish-review rejected
      // (e.g. tool.missing_runtime_spec) — open the structured modal
      // immediately using whatever blockers we already have cached
      // from the most recent publish-review query, then refetch in
      // the background so the modal updates if state changed since.
      const cachedBlockers = publishReviewQuery.data?.blockers ?? []
      if (cachedBlockers.length > 0) {
        setPublishBlockerOpen(true)
      } else {
        // Cached state has no blockers but publish 409'd anyway —
        // refetch and only then decide whether to show the modal or
        // fall back to a toast.
        void publishReviewQuery.refetch().then((res) => {
          const fresh = res.data?.blockers ?? []
          if (fresh.length > 0) {
            setPublishBlockerOpen(true)
          } else {
            toast.error(`Publish failed: ${error.message}`)
          }
        })
        return
      }
      // Background refresh — keeps modal contents accurate if the
      // user already configured a tool in another tab.
      void publishReviewQuery.refetch()
    },
  })

  const unpublishMutation = useMutation({
    mutationFn: async () => {
      if (!selectedAgentId) throw new Error('No agent definition selected')
      return agentDefinitionService.unpublishAgent(selectedAgentId)
    },
    onSuccess: (response) => {
      queryClient.invalidateQueries({ queryKey: ['agent-versions', response.agent_id] })
      queryClient.invalidateQueries({ queryKey: ['agent-publish-review', response.agent_id] })
      queryClient.invalidateQueries({ queryKey: ['agents-list'] })
      toast.success('Agent reverted to draft')
    },
    onError: (error: Error) => {
      toast.error(`Unpublish failed: ${error.message}`)
    },
  })

  const createDraftMutation = useMutation({
    mutationFn: async (sourceVersionId: string) => {
      if (!selectedAgentId) throw new Error('No agent definition selected')
      return agentDefinitionService.createAgentDraft(selectedAgentId, sourceVersionId)
    },
    onSuccess: (response) => {
      queryClient.invalidateQueries({ queryKey: ['agent-versions', response.agent_id] })
      queryClient.invalidateQueries({ queryKey: ['agent-publish-review', response.agent_id] })
      toast.success('Draft created from selected version')
    },
    onError: (error: Error) => {
      toast.error(`Failed to create draft: ${error.message}`)
    },
  })

  const handleSelectedAgentIdChange = useCallback(
    (agentId: string | null) => {
      if (agentId === selectedAgentId) return
      if (!confirmDiscard()) return
      setSelectedAgentId(agentId)
      setAgentSettingsDirty(false)
      setHasUnsavedAgentChanges(false)
      navigate(buildCanvasUrl(agentId), { replace: false })
    },
    [buildCanvasUrl, confirmDiscard, navigate, selectedAgentId],
  )

  const handleSelectAgentDefinition = useCallback(() => {
    workspaceRef.current?.selectState(null)
  }, [])

  const handleSidebarAgentSelect = useCallback(
    (agentId: string) => {
      if (agentId === selectedAgentId) {
        workspaceRef.current?.selectState(null)
        return
      }
      handleSelectedAgentIdChange(agentId)
    },
    [handleSelectedAgentIdChange, selectedAgentId],
  )

  const handleSidebarViewChange = useCallback(
    (view: SidebarView) => {
      setSidebarView(view)
      const nextParams = new URLSearchParams(searchParams)
      if (view === 'canvas') {
        nextParams.delete('view')
      } else {
        nextParams.set('view', view)
      }
      if (!selectedAgentId && requestedAgentType) {
        nextParams.set('type', requestedAgentType)
      } else if (selectedAgentId) {
        nextParams.delete('type')
      }
      setSearchParams(nextParams, { replace: true })
    },
    [requestedAgentType, searchParams, selectedAgentId, setSearchParams],
  )

  const handleAuthoringSurfaceChange = useCallback(
    (next: AuthoringSurface) => {
      if (next === authoringSurface) return
      // Switching TO Test does not discard anything — Test reads the live
      // in-memory AgentDocument from AgentDocumentProvider, so unsaved
      // edits ARE what gets tested. Skip the "discard?" prompt.
      const isLossyTransition = next !== 'test'
      if (
        isLossyTransition &&
        !confirmDiscard('You have unsaved changes. Switch authoring surface and discard them?')
      ) {
        return
      }
      setAuthoringSurface(next)
      const nextParams = new URLSearchParams(searchParams)
      // Strip the param when the user picks the default surface so URLs
      // stay clean. Tracks whatever the URL parser above treats as default.
      if (next === 'document') {
        nextParams.delete('surface')
      } else {
        nextParams.set('surface', next)
      }
      setSearchParams(nextParams, { replace: true })
    },
    [authoringSurface, confirmDiscard, searchParams, setSearchParams],
  )

  const handleSidebarDataChange = useCallback((payload: AgentDefinitionWorkspaceSidebarData) => {
    setSidebarAgents(payload.agents)
    setSidebarAgentName(payload.selectedAgentName)
    setSidebarScenarios(payload.scenarios)
    setSidebarSelectedScenarioId(payload.selectedScenarioId)
    setSidebarAgentFactCount(payload.factCount)
  }, [])

  const handleCreateAgent = useCallback(async () => {
    const createdAgentId = await workspaceRef.current?.createAgent()
    if (!createdAgentId) return
    setSelectedAgentId(createdAgentId)
    navigate(buildCanvasUrl(createdAgentId), { replace: true })
  }, [buildCanvasUrl, navigate])

  const handleSave = useCallback(async () => {
    let currentAgentId = selectedAgentId
    if (!currentAgentId) {
      const createdAgentId = await workspaceRef.current?.createAgent()
      if (!createdAgentId) return
      currentAgentId = createdAgentId
      setSelectedAgentId(createdAgentId)
      navigate(buildCanvasUrl(createdAgentId), { replace: true })
    }

    try {
      if (agentNameDirty && agentNameDraft && currentAgentId) {
        await agentDefinitionService.updateAgentMetadata(currentAgentId, { name: agentNameDraft })
        setAgentNameDirty(false)
        queryClient.invalidateQueries({ queryKey: ["agents-list"] })
      }
      if (agentSettingsDirty) {
        await updateSettingsMutation.mutateAsync(agentSettingsDraft)
      }
      if (workspaceRef.current?.hasUnsavedChanges()) {
        const saved = await workspaceRef.current.save()
        if (!saved) return
      }
      toast.success('Agent definition saved')
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown error'
      toast.error(`Save failed: ${message}`)
    }
  }, [agentNameDirty, agentNameDraft, agentSettingsDirty, agentSettingsDraft, buildCanvasUrl, navigate, queryClient, selectedAgentId, updateSettingsMutation])

  const handleBack = useCallback(() => {
    if (!confirmDiscard('You have unsaved changes. Leave this authoring session?')) return
    navigate('/agents')
  }, [confirmDiscard, navigate])

  const currentVersionStatus = useMemo(() => {
    const versions = versionsQuery.data || []
    const hasPublished = versions.some((version) => version.is_current_published)
    const hasDraft = versions.some((version) => version.is_current_draft)
    if (!hasPublished) return 'Draft'
    // Published version exists — show "Live" unless the draft has unpublished changes.
    const summary = activeAgentSummary
    if (summary && summary.has_unpublished_changes) return 'Draft'
    if (hasDraft && !hasPublished) return 'Draft'
    return 'Live'
  }, [versionsQuery.data, activeAgentSummary])

  const mainContent = useMemo(() => {
    if (sidebarView === 'canvas') {
      return (
        <AgentDefinitionWorkspace
          ref={workspaceRef}
          agentName={agentName}
          selectedAgentId={selectedAgentId}
          onSelectedAgentIdChange={handleSelectedAgentIdChange}
          onSidebarDataChange={handleSidebarDataChange}
          onDirtyChange={setHasUnsavedAgentChanges}
          autoCreate={isNewRoute}
          surface={authoringSurface}
          agentType={agentSettingsDraft.agent_type}
          agentSettingsPanel={
            <AgentSettingsPanel
              settings={agentSettingsDraft}
              onChange={(next) => {
                setAgentSettingsDraft(next)
                setAgentSettingsDirty(true)
              }}
              agentName={agentName}
              onNameChange={(name) => {
                setAgentNameDraft(name)
                setAgentNameDirty(true)
              }}
              status={currentVersionStatus === 'Live' ? 'published' : 'draft'}
              onStatusChange={(newStatus) => {
                if (newStatus === 'published' && currentVersionStatus !== 'Live') {
                  publishMutation.mutate()
                } else if (newStatus === 'draft' && currentVersionStatus === 'Live') {
                  unpublishMutation.mutate()
                }
              }}
              onOpenPersonaTab={() => handleSidebarViewChange('persona')}
            />
          }
        />
      )
    }

    if (!selectedAgentId) {
      return (
        <div className="flex h-full items-center justify-center p-8">
          <Card className="max-w-lg border-border bg-card/80">
            <CardHeader>
              <CardTitle>Create an agent definition first</CardTitle>
              <CardDescription>
                Workflow authoring, knowledge, versions, evaluation, and publish all operate on a concrete
                agent definition.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Button onClick={() => void handleCreateAgent()}>
                <Plus className="mr-2 h-4 w-4" />
                Create Agent Definition
              </Button>
            </CardContent>
          </Card>
        </div>
      )
    }

    if (sidebarView === 'supporting-docs') {
      return (
        <SupportingDocsView
          dataSources={(dataSourcesQuery.data || []) as KnowledgeDocument[]}
          loading={dataSourcesQuery.isLoading}
          agentId={selectedAgentId}
          selectedIds={agentSettingsDraft.knowledge_base_ids}
          onSelectionChange={(ids) => {
            setAgentSettingsDraft((current) => ({ ...current, knowledge_base_ids: ids }))
            setAgentSettingsDirty(true)
          }}
          onSave={() => void handleSave()}
          saving={updateSettingsMutation.isPending}
        />
      )
    }

    if (sidebarView === 'rules') {
      return (
        <AgentRulesView
          agentId={selectedAgentId}
          agentName={agentName}
          steps={rulesViewSteps}
          selectedStateId={null}
        />
      )
    }

    if (sidebarView === 'persona') {
      return <PersonaTab agentId={selectedAgentId} />
    }

    if (sidebarView === 'widget') {
      return (
        <div className="h-full overflow-y-auto p-6">
          <WidgetSettingsContent hideBackButton />
        </div>
      )
    }

    if (sidebarView === 'testing') {
      return <AgentEvaluationView agentId={selectedAgentId} agentName={agentName} />
    }

    if (sidebarView === 'releases') {
      return (
        <AgentPublishView
          versions={versionsQuery.data || []}
          review={publishReviewQuery.data || null}
          loadingVersions={versionsQuery.isLoading}
          loadingReview={publishReviewQuery.isLoading}
          publishing={publishMutation.isPending}
          onRefresh={() => {
            void versionsQuery.refetch()
            void publishReviewQuery.refetch()
          }}
          onPublish={() => publishMutation.mutate()}
        />
      )
    }

    if (sidebarView === 'library') {
      // Library is the unified surface for everything tool-shaped:
      //
      // - Tools tab: author callable tools (Custom APIs / Integration-
      //   templated / System / Code blocks). Tools are first-class,
      //   reusable resources
      //   referenced from steps, not embedded inside scenarios.
      // - Connections tab: manage provider OAuth + API-key connections
      //   that integration-templated tools depend on. Same UI as the
      //   former /integrations page, just relocated as a sibling tab.
      return (
        <div className="h-full overflow-y-auto p-6">
          <Tabs defaultValue="tools" className="space-y-4">
            <TabsList>
              <TabsTrigger value="tools">Tools</TabsTrigger>
              <TabsTrigger value="connections">Connections</TabsTrigger>
            </TabsList>
            <TabsContent value="tools" className="mt-0">
              <LibraryView />
            </TabsContent>
            <TabsContent value="connections" className="mt-0">
              <AgentIntegrationsTab agentId={selectedAgentId} agentName={agentName} />
            </TabsContent>
          </Tabs>
        </div>
      )
    }

    if (sidebarView === 'versions') {
      return (
        <AgentVersionsView
          versions={versionsQuery.data || []}
          selectedVersionId={selectedVersionId}
          againstVersionId={againstVersionId}
          diff={diffQuery.data || null}
          loadingVersions={versionsQuery.isLoading}
          loadingDiff={diffQuery.isLoading}
          creatingDraft={createDraftMutation.isPending}
          onSelectVersion={setSelectedVersionId}
          onSelectAgainstVersion={setAgainstVersionId}
          onRefresh={() => {
            void versionsQuery.refetch()
            void diffQuery.refetch()
          }}
          onCreateDraft={(versionId) => createDraftMutation.mutate(versionId)}
        />
      )
    }

    return null
  }, [
    agentName,
    agentSettingsDraft,
    authoringSurface,
    createDraftMutation,
    dataSourcesQuery.data,
    dataSourcesQuery.isLoading,
    diffQuery,
    handleCreateAgent,
    handleSave,
    handleSelectedAgentIdChange,
    handleSidebarDataChange,
    publishMutation,
    publishReviewQuery,
    rulesViewSteps,
    selectedAgentId,
    selectedVersionId,
    sidebarView,
    updateSettingsMutation.isPending,
    versionsQuery,
    againstVersionId,
  ])

  return (
    <DashboardLayout noPadding>
      {selectedAgentId && (
        <PublishBlockerModal
          open={publishBlockerOpen}
          blockers={publishReviewQuery.data?.blockers ?? []}
          agentId={selectedAgentId}
          onClose={() => setPublishBlockerOpen(false)}
        />
      )}
      <div className="flex h-[calc(100vh-4rem)]">
        <AgentCanvasSidebar
          activeView={sidebarView}
          onViewChange={handleSidebarViewChange}
          onBack={handleBack}
          supportedViews={SUPPORTED_VIEWS}
          isNewAgent={!selectedAgentId}
          agents={selectedAgentId ? sidebarAgents.filter(g => g.id === selectedAgentId) : sidebarAgents}
          selectedAgentId={selectedAgentId}
          onSelectAgent={handleSidebarAgentSelect}
          agentScenarios={sidebarScenarios}
          selectedScenarioId={sidebarSelectedScenarioId}
          onSelectScenario={(scenarioId) => workspaceRef.current?.scrollToScenario(scenarioId)}
          agentFactCount={sidebarAgentFactCount}
        />

        <AgentDocumentProvider agentId={selectedAgentId ?? undefined}>
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="border-b border-white/10 bg-card/70 px-6 py-2.5">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={handleSelectAgentDefinition}
                    className="truncate text-left text-xl font-semibold transition-colors hover:text-foreground/80"
                  >
                    {agentName}
                  </button>
                  <Badge
                    variant="outline"
                    className={currentVersionStatus === 'Live'
                      ? 'border-emerald-500/30 text-emerald-300'
                      : 'border-amber-500/30 text-amber-300'
                    }
                  >
                    {currentVersionStatus}
                  </Badge>
                  {hasUnsavedChanges && (
                    <Badge variant="outline" className="border-border text-muted-foreground">
                      Unsaved changes
                    </Badge>
                  )}
                </div>
                {!selectedAgentId && (
                  <p className="mt-1 text-sm text-muted-foreground">
                    Create a new agent definition to begin authoring.
                  </p>
                )}
                {selectedAgentId && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    Settings updated {formatUpdatedLabel(settingsQuery.dataUpdatedAt ? new Date(settingsQuery.dataUpdatedAt).toISOString() : null)}
                  </p>
                )}
              </div>

              <div className="flex flex-wrap items-center gap-2">
                {sidebarView === 'canvas' && selectedAgentId && (
                  <div className="flex items-center rounded-lg border border-white/10 bg-card/40 p-0.5 text-xs">
                    <button
                      type="button"
                      onClick={() => handleAuthoringSurfaceChange('document')}
                      aria-pressed={authoringSurface === 'document'}
                      className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 transition-colors ${
                        authoringSurface === 'document'
                          ? 'bg-primary/15 text-foreground'
                          : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      <FileJson className="h-3.5 w-3.5" />
                      Document
                    </button>
                    <button
                      type="button"
                      onClick={() => handleAuthoringSurfaceChange('graph')}
                      aria-pressed={authoringSurface === 'graph'}
                      className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 transition-colors ${
                        authoringSurface === 'graph'
                          ? 'bg-primary/15 text-foreground'
                          : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      <GitBranch className="h-3.5 w-3.5" />
                      Graph
                    </button>
                    <button
                      type="button"
                      onClick={() => handleAuthoringSurfaceChange('test')}
                      aria-pressed={authoringSurface === 'test'}
                      className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 transition-colors ${
                        authoringSurface === 'test'
                          ? 'bg-primary/15 text-foreground'
                          : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      <Play className="h-3.5 w-3.5" />
                      Test
                    </button>
                  </div>
                )}

                {selectedAgentId && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setIsAtlasOpen(true)}
                    className="text-muted-foreground hover:text-foreground"
                    title="Atlas — AI copilot for authoring"
                  >
                    <Sparkles className="mr-1.5 h-3.5 w-3.5" />
                    Atlas
                  </Button>
                )}

                {selectedAgentId && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setIsTestDialogOpen(true)}
                    className="text-muted-foreground hover:text-foreground"
                  >
                    <Play className="mr-1.5 h-3.5 w-3.5" />
                    Test
                  </Button>
                )}

                <div className="mx-1 h-5 w-px bg-white/10" />

                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void handleSave()}
                  disabled={updateSettingsMutation.isPending}
                  className="border-white/10 text-xs"
                >
                  {updateSettingsMutation.isPending ? (
                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Save className="mr-1.5 h-3.5 w-3.5" />
                  )}
                  Save
                </Button>
                <Button
                  size="sm"
                  onClick={() => publishMutation.mutate()}
                  disabled={!selectedAgentId || publishMutation.isPending || !publishReviewQuery.data?.can_publish}
                  className="bg-emerald-600 hover:bg-emerald-700 text-xs shadow-sm"
                >
                  {publishMutation.isPending ? (
                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Rocket className="mr-1.5 h-3.5 w-3.5" />
                  )}
                  Publish
                </Button>
              </div>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-hidden">{mainContent}</div>
        </div>
        </AgentDocumentProvider>
      </div>

      {/* Atlas slide-in — AI copilot. Self-managed isOpen/onClose. */}
      {selectedAgentId && (
        <AtlasAIPanel
          isOpen={isAtlasOpen}
          onClose={() => setIsAtlasOpen(false)}
          agentId={selectedAgentId}
        />
      )}

      {/* Test Dialog — mirrors the real widget experience */}
      <Dialog open={isTestDialogOpen} onOpenChange={setIsTestDialogOpen}>
        {isTestDialogOpen && (
          <DialogContent className="max-w-sm h-[600px] p-0 gap-0 overflow-hidden [&>button]:text-primary-foreground [&>button]:opacity-80 [&>button:hover]:opacity-100">
            <DialogHeader className="sr-only">
              <DialogTitle>Test Agent</DialogTitle>
              <DialogDescription>Test your agent via chat or voice</DialogDescription>
            </DialogHeader>
            {selectedAgentId ? (
              <UnifiedTestInterface
                agentId={selectedAgentId}
                agentName={agentName}
                agentType={agentSettingsDraft.agent_type}
                agentStatus={activeAgentSummary?.has_published_version ? 'active' : 'draft'}
              />
            ) : (
              <div className="flex items-center justify-center h-full text-muted-foreground">
                <p className="text-sm">Agent ID required for testing</p>
              </div>
            )}
          </DialogContent>
        )}
      </Dialog>
    </DashboardLayout>
  )
}
