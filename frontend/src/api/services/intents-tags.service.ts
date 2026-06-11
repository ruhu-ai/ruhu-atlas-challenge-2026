import { apiClient } from '../client'
import type {
  ClassifierProfile,
  ClassifierProfileCreateRequest,
  ClassifierProfileUpdateRequest,
  AgentSummary,
  ConversationSummaryDetailReadModel,
  IntentDefinition,
  IntentDefinitionCreateRequest,
  IntentDefinitionUpdateRequest,
  IntentTagsAnalyticsReadModel,
  IntentTagsInsightsReadModel,
  ProfileRebuildRequest,
  ReviewClaimRequest,
  ReviewQueueRowReadModel,
  SemanticWebhookDispatchMode,
  SemanticWebhookDispatchResponse,
  SemanticWebhookTargetCreateRequest,
  SemanticWebhookTargetReadModel,
  SemanticWebhookTargetUpdateRequest,
  SummaryListItemReadModel,
  SummaryReviewResolutionRequest,
  TagDefinition,
  TagDefinitionCreateRequest,
  TagDefinitionUpdateRequest,
  TaxonomySnapshotReadModel,
  TaxonomyVersion,
  TaxonomyVersionCreateRequest,
  TurnClassificationEvent,
  TurnReviewResolutionRequest,
} from '@/types/intent-tags'

type QueryParams = Record<string, string | number | boolean | undefined | null>
const MAX_INTENT_TAGS_INSIGHTS_LIMIT = 500

export interface IntentTagsScopedParams {
  organization_id?: string
  agent_id?: string
}

class IntentTagsService {
  private readonly basePath = '/intent-tags'

  async listAgents(): Promise<AgentSummary[]> {
    const response = await apiClient.get<AgentSummary[]>('/agents')
    return Array.isArray(response) ? response : []
  }

  getTaxonomySnapshot(params?: IntentTagsScopedParams): Promise<TaxonomySnapshotReadModel> {
    return apiClient.get<TaxonomySnapshotReadModel>(`${this.basePath}/taxonomy`, {
      params: params as QueryParams | undefined,
    })
  }

  listTaxonomyVersions(params?: { organization_id?: string }): Promise<TaxonomyVersion[]> {
    return apiClient.get<TaxonomyVersion[]>(`${this.basePath}/versions`, { params })
  }

  createTaxonomyVersion(payload: TaxonomyVersionCreateRequest): Promise<TaxonomyVersion> {
    return apiClient.post<TaxonomyVersion>(`${this.basePath}/versions`, payload)
  }

  publishTaxonomyVersion(
    taxonomyVersionId: string,
    params?: { organization_id?: string }
  ): Promise<TaxonomyVersion> {
    return apiClient.post<TaxonomyVersion>(
      `${this.basePath}/versions/${taxonomyVersionId}/publish`,
      undefined,
      { params }
    )
  }

  listIntents(params?: {
    organization_id?: string
    agent_id?: string
    taxonomy_version_id?: string
    include_inactive?: boolean
  }): Promise<IntentDefinition[]> {
    return apiClient.get<IntentDefinition[]>(`${this.basePath}/intents`, { params: params as QueryParams })
  }

  createIntent(payload: IntentDefinitionCreateRequest): Promise<IntentDefinition> {
    return apiClient.post<IntentDefinition>(`${this.basePath}/intents`, payload)
  }

  updateIntent(
    intentDefinitionId: string,
    payload: IntentDefinitionUpdateRequest,
    params?: { organization_id?: string }
  ): Promise<IntentDefinition> {
    return apiClient.put<IntentDefinition>(`${this.basePath}/intents/${intentDefinitionId}`, payload, { params })
  }

  listTags(params?: {
    organization_id?: string
    agent_id?: string
    taxonomy_version_id?: string
    include_inactive?: boolean
  }): Promise<TagDefinition[]> {
    return apiClient.get<TagDefinition[]>(`${this.basePath}/tags`, { params: params as QueryParams })
  }

  createTag(payload: TagDefinitionCreateRequest): Promise<TagDefinition> {
    return apiClient.post<TagDefinition>(`${this.basePath}/tags`, payload)
  }

  updateTag(
    tagDefinitionId: string,
    payload: TagDefinitionUpdateRequest,
    params?: { organization_id?: string }
  ): Promise<TagDefinition> {
    return apiClient.put<TagDefinition>(`${this.basePath}/tags/${tagDefinitionId}`, payload, { params })
  }

  listProfiles(params?: {
    organization_id?: string
    agent_id?: string
    is_active?: boolean
  }): Promise<ClassifierProfile[]> {
    return apiClient.get<ClassifierProfile[]>(`${this.basePath}/profiles`, { params: params as QueryParams })
  }

  createProfile(payload: ClassifierProfileCreateRequest): Promise<ClassifierProfile> {
    return apiClient.post<ClassifierProfile>(`${this.basePath}/profiles`, payload)
  }

  updateProfile(
    classifierProfileId: string,
    payload: ClassifierProfileUpdateRequest,
    params?: { organization_id?: string }
  ): Promise<ClassifierProfile> {
    return apiClient.put<ClassifierProfile>(
      `${this.basePath}/profiles/${classifierProfileId}`,
      payload,
      { params }
    )
  }

  rebuildProfile(
    classifierProfileId: string,
    payload: ProfileRebuildRequest
  ): Promise<ClassifierProfile> {
    return apiClient.post<ClassifierProfile>(
      `${this.basePath}/profiles/${classifierProfileId}/rebuild`,
      payload
    )
  }

  listEvents(params?: {
    organization_id?: string
    conversation_id?: string
    intent_name?: string
    limit?: number
  }): Promise<TurnClassificationEvent[]> {
    return apiClient.get<TurnClassificationEvent[]>(`${this.basePath}/events`, { params: params as QueryParams })
  }

  listSummaries(params?: {
    organization_id?: string
    agent_id?: string
    conversation_id?: string
    status?: string
    limit?: number
  }): Promise<SummaryListItemReadModel[]> {
    return apiClient.get<SummaryListItemReadModel[]>(`${this.basePath}/summaries`, {
      params: params as QueryParams,
    })
  }

  getSummaryDetail(
    conversationSummaryId: string,
    params?: { organization_id?: string }
  ): Promise<ConversationSummaryDetailReadModel> {
    return apiClient.get<ConversationSummaryDetailReadModel>(
      `${this.basePath}/summaries/${conversationSummaryId}`,
      { params }
    )
  }

  listReviews(params?: {
    organization_id?: string
    agent_id?: string
    status?: string
    review_kind?: string
    claimed_by_user_id?: string
    limit?: number
  }): Promise<ReviewQueueRowReadModel[]> {
    return apiClient.get<ReviewQueueRowReadModel[]>(`${this.basePath}/reviews`, {
      params: params as QueryParams,
    })
  }

  claimReviewItem(
    reviewItemId: string,
    payload: ReviewClaimRequest,
    params?: { organization_id?: string }
  ) {
    return apiClient.post(`${this.basePath}/reviews/${reviewItemId}/claim`, payload, { params })
  }

  resolveTurnReview(
    reviewItemId: string,
    payload: TurnReviewResolutionRequest,
    params?: { organization_id?: string }
  ) {
    return apiClient.post(`${this.basePath}/reviews/${reviewItemId}/resolve-turn`, payload, { params })
  }

  resolveSummaryReview(
    reviewItemId: string,
    payload: SummaryReviewResolutionRequest,
    params?: { organization_id?: string }
  ) {
    return apiClient.post(`${this.basePath}/reviews/${reviewItemId}/resolve-summary`, payload, { params })
  }

  getAnalytics(params?: {
    organization_id?: string
    agent_id?: string
    limit?: number
  }): Promise<IntentTagsAnalyticsReadModel> {
    return apiClient.get<IntentTagsAnalyticsReadModel>(`${this.basePath}/analytics`, {
      params: params as QueryParams,
    })
  }

  getInsights(params?: {
    organization_id?: string
    agent_id?: string
    limit?: number
  }): Promise<IntentTagsInsightsReadModel> {
    const normalizedParams = {
      ...params,
      limit: Math.min(params?.limit ?? MAX_INTENT_TAGS_INSIGHTS_LIMIT, MAX_INTENT_TAGS_INSIGHTS_LIMIT),
    }
    return apiClient.get<IntentTagsInsightsReadModel>(`${this.basePath}/insights`, {
      params: normalizedParams as QueryParams,
    })
  }

  listWebhookTargets(params?: {
    organization_id?: string
    is_active?: boolean
  }): Promise<SemanticWebhookTargetReadModel[]> {
    return apiClient.get<SemanticWebhookTargetReadModel[]>(`${this.basePath}/webhook-targets`, {
      params: params as QueryParams,
    })
  }

  createWebhookTarget(
    payload: SemanticWebhookTargetCreateRequest
  ): Promise<SemanticWebhookTargetReadModel> {
    return apiClient.post<SemanticWebhookTargetReadModel>(`${this.basePath}/webhook-targets`, payload)
  }

  updateWebhookTarget(
    webhookTargetId: string,
    payload: SemanticWebhookTargetUpdateRequest,
    params?: { organization_id?: string }
  ): Promise<SemanticWebhookTargetReadModel> {
    return apiClient.put<SemanticWebhookTargetReadModel>(
      `${this.basePath}/webhook-targets/${webhookTargetId}`,
      payload,
      { params }
    )
  }

  deleteWebhookTarget(
    webhookTargetId: string,
    params?: { organization_id?: string }
  ): Promise<void> {
    return apiClient.delete<void>(`${this.basePath}/webhook-targets/${webhookTargetId}`, undefined, { params })
  }

  dispatchSemanticWebhooks(params?: {
    organization_id?: string
    conversation_id?: string
    mode?: SemanticWebhookDispatchMode
    limit?: number
  }): Promise<SemanticWebhookDispatchResponse> {
    return apiClient.post<SemanticWebhookDispatchResponse>(
      `${this.basePath}/webhooks/dispatch`,
      undefined,
      { params: params as QueryParams }
    )
  }
}

export const intentTagsService = new IntentTagsService()
