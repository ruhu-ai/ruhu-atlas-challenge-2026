import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { intentTagsService } from '@/api/services/intents-tags.service'
import type {
  ClassifierProfile,
  AgentSummary,
  ConversationSummaryDetailReadModel,
  IntentDefinition,
  IntentTagsAnalyticsReadModel,
  IntentTagsInsightsReadModel,
  ReviewQueueRowReadModel,
  SemanticWebhookDispatchMode,
  SemanticWebhookDispatchResponse,
  SemanticWebhookTargetReadModel,
  SummaryListItemReadModel,
  TagDefinition,
  TaxonomySnapshotReadModel,
} from '@/types/intent-tags'
import {
  emptyIntentForm,
  emptyProfileForm,
  emptyReviewResolutionState,
  emptyTagForm,
  emptyVersionForm,
  emptyWebhookForm,
  type IntentFormState,
  type ProfileFormState,
  type ReviewResolutionState,
  type ReviewStatusFilter,
  type SummaryStatusFilter,
  type TagFormState,
  type VersionFormState,
  type WebhookFormState,
  type WorkspaceTab,
} from '../utils/intent-tags-form-state'

export function useIntentTagsWorkspaceState(organizationId: string | undefined) {
  const [selectedTab, setSelectedTab] = useState<WorkspaceTab>('overview')
  const [selectedAgentId, setSelectedAgentId] = useState('all')
  const [agents, setAgents] = useState<AgentSummary[]>([])
  const [taxonomy, setTaxonomy] = useState<TaxonomySnapshotReadModel | null>(null)
  const [analytics, setAnalytics] = useState<IntentTagsAnalyticsReadModel | null>(null)
  const [insights, setInsights] = useState<IntentTagsInsightsReadModel | null>(null)
  const [reviews, setReviews] = useState<ReviewQueueRowReadModel[]>([])
  const [summaries, setSummaries] = useState<SummaryListItemReadModel[]>([])
  const [webhookTargets, setWebhookTargets] = useState<SemanticWebhookTargetReadModel[]>([])
  const [summaryStatusFilter, setSummaryStatusFilter] = useState<SummaryStatusFilter>('all')
  const [reviewStatusFilter, setReviewStatusFilter] = useState<ReviewStatusFilter>('all')
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [dispatchResult, setDispatchResult] = useState<SemanticWebhookDispatchResponse | null>(null)
  const [dispatchMode, setDispatchMode] = useState<SemanticWebhookDispatchMode>('both')
  const [dispatchConversationId, setDispatchConversationId] = useState('')

  const [versionDialogOpen, setVersionDialogOpen] = useState(false)
  const [intentDialogOpen, setIntentDialogOpen] = useState(false)
  const [tagDialogOpen, setTagDialogOpen] = useState(false)
  const [profileDialogOpen, setProfileDialogOpen] = useState(false)
  const [webhookDialogOpen, setWebhookDialogOpen] = useState(false)
  const [summaryDialogOpen, setSummaryDialogOpen] = useState(false)
  const [reviewDialogOpen, setReviewDialogOpen] = useState(false)

  const [editingIntent, setEditingIntent] = useState<IntentDefinition | null>(null)
  const [editingTag, setEditingTag] = useState<TagDefinition | null>(null)
  const [editingProfile, setEditingProfile] = useState<ClassifierProfile | null>(null)
  const [editingWebhookTarget, setEditingWebhookTarget] = useState<SemanticWebhookTargetReadModel | null>(null)
  const [selectedReview, setSelectedReview] = useState<ReviewQueueRowReadModel | null>(null)
  const [selectedSummary, setSelectedSummary] = useState<SummaryListItemReadModel | null>(null)
  const [summaryDetail, setSummaryDetail] = useState<ConversationSummaryDetailReadModel | null>(null)

  const [versionForm, setVersionForm] = useState<VersionFormState>(emptyVersionForm())
  const [intentForm, setIntentForm] = useState<IntentFormState>(emptyIntentForm())
  const [tagForm, setTagForm] = useState<TagFormState>(emptyTagForm())
  const [profileForm, setProfileForm] = useState<ProfileFormState>(emptyProfileForm())
  const [webhookForm, setWebhookForm] = useState<WebhookFormState>(emptyWebhookForm())
  const [reviewResolution, setReviewResolution] = useState<ReviewResolutionState>(
    emptyReviewResolutionState(null)
  )

  useEffect(() => {
    if (!organizationId) {
      return
    }
    void loadWorkspace({ includeAgents: true })
  }, [organizationId, selectedAgentId, summaryStatusFilter, reviewStatusFilter])

  useEffect(() => {
    if (!editingIntent) {
      setIntentForm(emptyIntentForm(selectedAgentId === 'all' ? undefined : selectedAgentId))
      return
    }
    setIntentForm({
      name: editingIntent.name,
      display_name: editingIntent.display_name,
      description: editingIntent.description ?? '',
      category: editingIntent.category ?? '',
      example_phrases: editingIntent.example_phrases.join('\n'),
      confidence_threshold: String(editingIntent.confidence_threshold),
      priority: String(editingIntent.priority),
      is_active: editingIntent.is_active,
      is_deprecated: editingIntent.is_deprecated,
      color: editingIntent.color ?? '',
      icon: editingIntent.icon ?? '',
      agent_id: editingIntent.agent_id ?? 'none',
      taxonomy_version_id: editingIntent.taxonomy_version_id ?? 'live',
      metadata_json: JSON.stringify(editingIntent.metadata ?? {}, null, 2),
    })
  }, [editingIntent, selectedAgentId])

  useEffect(() => {
    if (!editingTag) {
      setTagForm(emptyTagForm(selectedAgentId === 'all' ? undefined : selectedAgentId))
      return
    }
    setTagForm({
      name: editingTag.name,
      display_name: editingTag.display_name,
      description: editingTag.description ?? '',
      tag_kind: editingTag.tag_kind,
      category: editingTag.category ?? '',
      confidence_threshold: String(editingTag.confidence_threshold),
      apply_scope: editingTag.apply_scope,
      related_intent_id: editingTag.related_intent_id ?? 'none',
      is_active: editingTag.is_active,
      is_deprecated: editingTag.is_deprecated,
      color: editingTag.color ?? '',
      icon: editingTag.icon ?? '',
      agent_id: editingTag.agent_id ?? 'none',
      taxonomy_version_id: editingTag.taxonomy_version_id ?? 'live',
      rule_config_json: JSON.stringify(editingTag.rule_config ?? {}, null, 2),
      metadata_json: JSON.stringify(editingTag.metadata ?? {}, null, 2),
    })
  }, [editingTag, selectedAgentId])

  useEffect(() => {
    if (!editingProfile) {
      setProfileForm(emptyProfileForm(selectedAgentId === 'all' ? undefined : selectedAgentId))
      return
    }
    setProfileForm({
      adapter_name: editingProfile.adapter_name,
      agent_id: editingProfile.agent_id ?? 'none',
      supported_languages: editingProfile.supported_languages.join(', '),
      taxonomy_mode: editingProfile.taxonomy_mode,
      taxonomy_version_id: editingProfile.taxonomy_version_id ?? 'live',
      tool_catalog_json: JSON.stringify(editingProfile.tool_catalog ?? [], null, 2),
      policy_profile_json: JSON.stringify(editingProfile.policy_profile ?? {}, null, 2),
      profile_metadata_json: JSON.stringify(editingProfile.profile_metadata ?? {}, null, 2),
      is_active: editingProfile.is_active,
    })
  }, [editingProfile, selectedAgentId])

  useEffect(() => {
    if (!editingWebhookTarget) {
      setWebhookForm(emptyWebhookForm(selectedAgentId === 'all' ? undefined : selectedAgentId))
      return
    }
    setWebhookForm({
      name: editingWebhookTarget.name,
      url: editingWebhookTarget.url,
      agent_ids: editingWebhookTarget.agent_ids,
      channels: editingWebhookTarget.channels as WebhookFormState['channels'],
      signing_secret_ref: '',
      extra_headers_json: JSON.stringify(editingWebhookTarget.extra_headers ?? {}, null, 2),
      timeout_seconds: String(editingWebhookTarget.timeout_seconds),
      max_retries: String(editingWebhookTarget.max_retries),
      retry_backoff_seconds: String(editingWebhookTarget.retry_backoff_seconds),
      is_active: editingWebhookTarget.is_active,
    })
  }, [editingWebhookTarget, selectedAgentId])

  useEffect(() => {
    setReviewResolution(emptyReviewResolutionState(selectedReview))
  }, [selectedReview])

  async function loadWorkspace({ includeAgents = false }: { includeAgents?: boolean } = {}) {
    if (!organizationId) {
      return
    }

    const hasExistingData = Boolean(taxonomy || analytics || insights || summaries.length || reviews.length)
    if (hasExistingData) {
      setRefreshing(true)
    } else {
      setLoading(true)
    }
    setError(null)

    try {
      const agentId = selectedAgentId === 'all' ? undefined : selectedAgentId
      const reviewStatus = reviewStatusFilter === 'all' ? undefined : reviewStatusFilter
      const summaryStatus = summaryStatusFilter === 'all' ? undefined : summaryStatusFilter
      const [
        agentItems,
        taxonomySnapshot,
        analyticsSnapshot,
        reviewItems,
        summaryItems,
        webhookItems,
        insightsResult,
      ] = await Promise.all([
        includeAgents || agents.length === 0
          ? intentTagsService.listAgents()
          : Promise.resolve(agents),
        intentTagsService.getTaxonomySnapshot({
          organization_id: organizationId,
          agent_id: agentId,
        }),
        intentTagsService.getAnalytics({
          organization_id: organizationId,
          agent_id: agentId,
          limit: 2500,
        }),
        intentTagsService.listReviews({
          organization_id: organizationId,
          agent_id: agentId,
          status: reviewStatus,
          limit: 100,
        }),
        intentTagsService.listSummaries({
          organization_id: organizationId,
          agent_id: agentId,
          status: summaryStatus,
          limit: 100,
        }),
        intentTagsService.listWebhookTargets({
          organization_id: organizationId,
        }),
        intentTagsService
          .getInsights({
            organization_id: organizationId,
            agent_id: agentId,
            limit: 500,
          })
          .then((data) => ({ ok: true as const, data }))
          .catch((insightsError) => ({ ok: false as const, insightsError })),
      ])

      if (includeAgents || agents.length === 0) {
        setAgents(agentItems)
      }
      setTaxonomy(taxonomySnapshot)
      setAnalytics(analyticsSnapshot)
      if (insightsResult.ok) {
        setInsights(insightsResult.data)
      } else {
        setInsights((current) => current ?? { totals: {}, rows: [] })
        console.warn('Intent tags insights failed to load:', insightsResult.insightsError)
      }
      setReviews(reviewItems)
      setSummaries(summaryItems)
      setWebhookTargets(webhookItems)
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : 'Failed to load intent tags workspace'
      setError(message)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  async function refreshWorkspace(successMessage?: string) {
    await loadWorkspace()
    if (successMessage) {
      toast.success(successMessage)
    }
  }

  return {
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
    setBusyKey,
    dispatchResult,
    setDispatchResult,
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
    refreshWorkspace,
  }
}

export type IntentTagsWorkspaceState = ReturnType<typeof useIntentTagsWorkspaceState>
