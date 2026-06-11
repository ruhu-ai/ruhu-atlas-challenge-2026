import {
  AlertCircle,
  ClipboardList,
  GitBranch,
  Link2,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/atoms/tabs'
import { Button } from '@/components/atoms/button'
import { Card, CardContent } from '@/components/atoms/card'
import { Badge } from '@/components/atoms/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
import { useAuthStore } from '@/store/auth.store'
import { formatRelativeNumber, titleCaseFromSnake } from '../utils/intent-tags-helpers'
import { emptyVersionForm, type WorkspaceTab } from '../utils/intent-tags-form-state'
import { useIntentTagsWorkspaceState } from '../hooks/useIntentTagsWorkspaceState'
import { useIntentTagsActions } from '../hooks/useIntentTagsActions'
import { IntentTagsOverviewTab } from './IntentTagsOverviewTab'
import { IntentTagsTaxonomyTab } from './IntentTagsTaxonomyTab'
import {
  IntentTagsReviewsTab,
  IntentTagsSummariesTab,
  IntentTagsWebhooksTab,
} from './IntentTagsOperationsTabs'
import {
  IntentTagsIntentDialog,
  IntentTagsTagDialog,
  IntentTagsVersionDialog,
} from './IntentTagsTaxonomyDialogs'
import {
  IntentTagsProfileDialog,
  IntentTagsWebhookDialog,
} from './IntentTagsProfileWebhookDialogs'
import {
  IntentTagsReviewResolutionDialog,
  IntentTagsSummaryDetailDialog,
} from './IntentTagsReviewSummaryDialogs'

export function IntentTagsWorkspace() {
  const { user } = useAuthStore()
  const organizationId = user?.organization.organization_id
  const organizationRole = user?.organization.role ?? 'analyst'
  const canWrite = organizationRole === 'admin' || organizationRole === 'developer'

  const workspace = useIntentTagsWorkspaceState(organizationId)
  const actions = useIntentTagsActions(workspace)

  const {
    selectedTab,
    setSelectedTab,
    selectedAgentId,
    setSelectedAgentId,
    agents,
    taxonomy,
    analytics,
    insights,
    reviews,
    summaries,
    webhookTargets,
    summaryStatusFilter,
    setSummaryStatusFilter,
    reviewStatusFilter,
    setReviewStatusFilter,
    loading,
    refreshing,
    error,
    busyKey,
    dispatchResult,
    dispatchMode,
    setDispatchMode,
    dispatchConversationId,
    setDispatchConversationId,
    versionDialogOpen,
    setVersionDialogOpen,
    intentDialogOpen,
    setIntentDialogOpen,
    tagDialogOpen,
    setTagDialogOpen,
    profileDialogOpen,
    setProfileDialogOpen,
    webhookDialogOpen,
    setWebhookDialogOpen,
    summaryDialogOpen,
    setSummaryDialogOpen,
    reviewDialogOpen,
    setReviewDialogOpen,
    editingIntent,
    setEditingIntent,
    editingTag,
    setEditingTag,
    editingProfile,
    setEditingProfile,
    editingWebhookTarget,
    setEditingWebhookTarget,
    selectedReview,
    setSelectedReview,
    selectedSummary,
    setSelectedSummary,
    summaryDetail,
    setSummaryDetail,
    versionForm,
    setVersionForm,
    intentForm,
    setIntentForm,
    tagForm,
    setTagForm,
    profileForm,
    setProfileForm,
    webhookForm,
    setWebhookForm,
    reviewResolution,
    setReviewResolution,
    loadWorkspace,
  } = workspace

  if (!organizationId) {
    return (
      <Card className="p-8">
        <div className="flex items-center justify-center gap-3 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span>Loading organization context…</span>
        </div>
      </Card>
    )
  }

  if (loading) {
    return (
      <Card className="p-12">
        <div className="flex items-center justify-center gap-3 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span>Loading semantic operations workspace…</span>
        </div>
      </Card>
    )
  }

  if (error) {
    return (
      <Card className="border-destructive/30 p-8">
        <div className="space-y-4">
          <div className="flex items-start gap-3">
            <AlertCircle className="mt-0.5 h-5 w-5 text-destructive" />
            <div className="space-y-1">
              <h2 className="font-semibold">Intent tags workspace failed to load</h2>
              <p className="text-sm text-muted-foreground">{error}</p>
            </div>
          </div>
          <Button onClick={() => void loadWorkspace({ includeAgents: true })}>Retry</Button>
        </div>
      </Card>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary">Semantic control plane</Badge>
            <Badge variant="outline">{titleCaseFromSnake(organizationRole)}</Badge>
            {selectedAgentId !== 'all' ? (
              <Badge variant="outline">
                {agents.find((agent) => agent.id === selectedAgentId)?.name ?? selectedAgentId}
              </Badge>
            ) : null}
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">Intent Tags</h1>
          <p className="text-sm text-muted-foreground">
            Operate the live taxonomy, hosted classifier profiles, review workflow,
            semantic summaries, and webhook delivery from one backend-aligned workspace.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <Select value={selectedAgentId} onValueChange={setSelectedAgentId}>
            <SelectTrigger className="w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All agents</SelectItem>
              {agents.map((agent) => (
                <SelectItem key={agent.id} value={agent.id}>
                  {agent.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            onClick={() => void loadWorkspace({ includeAgents: true })}
            disabled={refreshing}
          >
            {refreshing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
            Refresh
          </Button>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">Turn events</p>
            <p className="mt-1 text-2xl font-semibold">{formatRelativeNumber(analytics?.totals.turn_events)}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">Summaries</p>
            <p className="mt-1 text-2xl font-semibold">{formatRelativeNumber(analytics?.totals.conversation_summaries)}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">Open reviews</p>
            <p className="mt-1 text-2xl font-semibold">{formatRelativeNumber(analytics?.review_status_counts.pending)}</p>
          </CardContent>
        </Card>
      </div>

      <Tabs value={selectedTab} onValueChange={(value) => setSelectedTab(value as WorkspaceTab)}>
        <TabsList className="grid w-full grid-cols-5">
          <TabsTrigger value="overview" className="gap-2">
            <Sparkles className="h-4 w-4" />
            Overview
          </TabsTrigger>
          <TabsTrigger value="taxonomy" className="gap-2">
            <GitBranch className="h-4 w-4" />
            Taxonomy
          </TabsTrigger>
          <TabsTrigger value="reviews" className="gap-2">
            <ShieldCheck className="h-4 w-4" />
            Reviews
          </TabsTrigger>
          <TabsTrigger value="summaries" className="gap-2">
            <ClipboardList className="h-4 w-4" />
            Summaries
          </TabsTrigger>
          <TabsTrigger value="webhooks" className="gap-2">
            <Link2 className="h-4 w-4" />
            Webhooks
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-6 pt-6">
          <IntentTagsOverviewTab
            taxonomy={taxonomy}
            analytics={analytics}
            insights={insights}
            webhookTargets={webhookTargets}
          />
        </TabsContent>

        <TabsContent value="taxonomy" className="space-y-6 pt-6">
          <IntentTagsTaxonomyTab
            taxonomy={taxonomy}
            canWrite={canWrite}
            busyKey={busyKey}
            onCreateVersion={() => {
              setVersionForm(emptyVersionForm())
              setVersionDialogOpen(true)
            }}
            onPublishVersion={(version) => void actions.handlePublishVersion(version)}
            onAddIntent={() => {
              setEditingIntent(null)
              setIntentDialogOpen(true)
            }}
            onEditIntent={(intent) => {
              setEditingIntent(intent)
              setIntentDialogOpen(true)
            }}
            onAddTag={() => {
              setEditingTag(null)
              setTagDialogOpen(true)
            }}
            onEditTag={(tagDefinition) => {
              setEditingTag(tagDefinition)
              setTagDialogOpen(true)
            }}
            onAddProfile={() => {
              setEditingProfile(null)
              setProfileDialogOpen(true)
            }}
            onEditProfile={(profile) => {
              setEditingProfile(profile)
              setProfileDialogOpen(true)
            }}
            onRebuildProfile={(profile) => void actions.handleRebuildProfile(profile)}
          />
        </TabsContent>

        <TabsContent value="reviews" className="space-y-6 pt-6">
          <IntentTagsReviewsTab
            reviews={reviews}
            reviewStatusFilter={reviewStatusFilter}
            onReviewStatusFilterChange={setReviewStatusFilter}
            busyKey={busyKey}
            onClaimReview={(row) => void actions.handleClaimReview(row)}
            onResolveReview={(row) => {
              setSelectedReview(row)
              setReviewDialogOpen(true)
            }}
          />
        </TabsContent>

        <TabsContent value="summaries" className="space-y-6 pt-6">
          <IntentTagsSummariesTab
            summaries={summaries}
            summaryStatusFilter={summaryStatusFilter}
            onSummaryStatusFilterChange={setSummaryStatusFilter}
            onOpenSummary={(item) => void actions.handleOpenSummary(item)}
          />
        </TabsContent>

        <TabsContent value="webhooks" className="space-y-6 pt-6">
          <IntentTagsWebhooksTab
            webhookTargets={webhookTargets}
            canWrite={canWrite}
            busyKey={busyKey}
            dispatchMode={dispatchMode}
            onDispatchModeChange={setDispatchMode}
            dispatchConversationId={dispatchConversationId}
            onDispatchConversationIdChange={setDispatchConversationId}
            dispatchResult={dispatchResult}
            onDispatchWebhooks={() => void actions.handleDispatchWebhooks()}
            onAddTarget={() => {
              setEditingWebhookTarget(null)
              setWebhookDialogOpen(true)
            }}
            onEditTarget={(target) => {
              setEditingWebhookTarget(target)
              setWebhookDialogOpen(true)
            }}
            onDeleteTarget={(target) => void actions.handleDeleteWebhookTarget(target)}
          />
        </TabsContent>
      </Tabs>

      <IntentTagsVersionDialog
        open={versionDialogOpen}
        onOpenChange={setVersionDialogOpen}
        versionForm={versionForm}
        setVersionForm={setVersionForm}
        submitting={busyKey === 'create-version'}
        onSubmit={() => void actions.handleCreateVersion()}
      />

      <IntentTagsIntentDialog
        open={intentDialogOpen}
        onOpenChange={(open) => {
          setIntentDialogOpen(open)
          if (!open) {
            setEditingIntent(null)
          }
        }}
        editingIntent={editingIntent}
        intentForm={intentForm}
        setIntentForm={setIntentForm}
        agents={agents}
        taxonomy={taxonomy}
        submitting={busyKey === 'save-intent'}
        onSubmit={() => void actions.handleSaveIntent()}
      />

      <IntentTagsTagDialog
        open={tagDialogOpen}
        onOpenChange={(open) => {
          setTagDialogOpen(open)
          if (!open) {
            setEditingTag(null)
          }
        }}
        editingTag={editingTag}
        tagForm={tagForm}
        setTagForm={setTagForm}
        agents={agents}
        taxonomy={taxonomy}
        submitting={busyKey === 'save-tag'}
        onSubmit={() => void actions.handleSaveTag()}
      />

      <IntentTagsProfileDialog
        open={profileDialogOpen}
        onOpenChange={(open) => {
          setProfileDialogOpen(open)
          if (!open) {
            setEditingProfile(null)
          }
        }}
        editingProfile={editingProfile}
        profileForm={profileForm}
        setProfileForm={setProfileForm}
        agents={agents}
        taxonomy={taxonomy}
        submitting={busyKey === 'save-profile'}
        onSubmit={() => void actions.handleSaveProfile()}
      />

      <IntentTagsWebhookDialog
        open={webhookDialogOpen}
        onOpenChange={(open) => {
          setWebhookDialogOpen(open)
          if (!open) {
            setEditingWebhookTarget(null)
          }
        }}
        editingWebhookTarget={editingWebhookTarget}
        webhookForm={webhookForm}
        setWebhookForm={setWebhookForm}
        agents={agents}
        submitting={busyKey === 'save-webhook'}
        onSubmit={() => void actions.handleSaveWebhookTarget()}
      />

      <IntentTagsReviewResolutionDialog
        open={reviewDialogOpen}
        onOpenChange={(open) => {
          setReviewDialogOpen(open)
          if (!open) {
            setSelectedReview(null)
          }
        }}
        onCancel={() => setReviewDialogOpen(false)}
        selectedReview={selectedReview}
        reviewResolution={reviewResolution}
        setReviewResolution={setReviewResolution}
        busyKey={busyKey}
        onSubmit={() => void actions.handleResolveReview()}
      />

      <IntentTagsSummaryDetailDialog
        open={summaryDialogOpen}
        onOpenChange={(open) => {
          setSummaryDialogOpen(open)
          if (!open) {
            setSelectedSummary(null)
            setSummaryDetail(null)
          }
        }}
        selectedSummary={selectedSummary}
        summaryDetail={summaryDetail}
      />
    </div>
  )
}
