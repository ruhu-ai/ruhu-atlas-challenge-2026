import { toast } from 'sonner'
import { useAuthStore } from '@/store/auth.store'
import { intentTagsService } from '@/api/services/intents-tags.service'
import type {
  ClassifierProfile,
  ReviewQueueRowReadModel,
  SemanticWebhookTargetReadModel,
  SummaryListItemReadModel,
  TaxonomyVersion,
} from '@/types/intent-tags'
import {
  parseCommaList,
  parseJsonArrayOfObjects,
  parseJsonObject,
  parseJsonStringMap,
} from '../utils/intent-tags-helpers'
import { emptyVersionForm } from '../utils/intent-tags-form-state'
import type { IntentTagsWorkspaceState } from './useIntentTagsWorkspaceState'

export function useIntentTagsActions(workspace: IntentTagsWorkspaceState) {
  const { user } = useAuthStore()
  const organizationId = user?.organization.organization_id

  const {
    setBusyKey,
    refreshWorkspace,
    versionForm,
    setVersionForm,
    setVersionDialogOpen,
    intentForm,
    editingIntent,
    setEditingIntent,
    setIntentDialogOpen,
    tagForm,
    editingTag,
    setEditingTag,
    setTagDialogOpen,
    profileForm,
    editingProfile,
    setEditingProfile,
    setProfileDialogOpen,
    webhookForm,
    editingWebhookTarget,
    setEditingWebhookTarget,
    setWebhookDialogOpen,
    dispatchConversationId,
    dispatchMode,
    setDispatchResult,
    reviewResolution,
    selectedReview,
    setSelectedReview,
    setReviewDialogOpen,
    setSelectedSummary,
    setSummaryDialogOpen,
    setSummaryDetail,
  } = workspace

  async function handleCreateVersion() {
    if (!organizationId) {
      return
    }
    setBusyKey('create-version')
    try {
      await intentTagsService.createTaxonomyVersion({
        organization_id: organizationId,
        name: versionForm.name.trim(),
        notes: versionForm.notes.trim() || null,
      })
      setVersionDialogOpen(false)
      setVersionForm(emptyVersionForm())
      await refreshWorkspace('Taxonomy version created')
    } catch (actionError) {
      toast.error(actionError instanceof Error ? actionError.message : 'Failed to create version')
    } finally {
      setBusyKey(null)
    }
  }

  async function handlePublishVersion(version: TaxonomyVersion) {
    if (!organizationId) {
      return
    }
    setBusyKey(`publish-version-${version.taxonomy_version_id}`)
    try {
      await intentTagsService.publishTaxonomyVersion(version.taxonomy_version_id, {
        organization_id: organizationId,
      })
      await refreshWorkspace(`Published ${version.name}`)
    } catch (actionError) {
      toast.error(actionError instanceof Error ? actionError.message : 'Failed to publish version')
    } finally {
      setBusyKey(null)
    }
  }

  async function handleSaveIntent() {
    if (!organizationId) {
      return
    }
    setBusyKey('save-intent')
    try {
      const payload = {
        organization_id: organizationId,
        agent_id: intentForm.agent_id === 'none' ? null : intentForm.agent_id,
        taxonomy_version_id: intentForm.taxonomy_version_id === 'live' ? null : intentForm.taxonomy_version_id,
        name: intentForm.name.trim(),
        display_name: intentForm.display_name.trim(),
        description: intentForm.description.trim() || null,
        category: intentForm.category.trim() || null,
        example_phrases: intentForm.example_phrases
          .split('\n')
          .map((item) => item.trim())
          .filter(Boolean),
        confidence_threshold: Number(intentForm.confidence_threshold),
        priority: Number(intentForm.priority),
        is_active: intentForm.is_active,
        is_deprecated: intentForm.is_deprecated,
        color: intentForm.color.trim() || null,
        icon: intentForm.icon.trim() || null,
        metadata: parseJsonObject(intentForm.metadata_json, 'Intent metadata'),
      }

      if (editingIntent) {
        await intentTagsService.updateIntent(editingIntent.intent_definition_id, payload, {
          organization_id: organizationId,
        })
      } else {
        await intentTagsService.createIntent(payload)
      }

      setIntentDialogOpen(false)
      setEditingIntent(null)
      await refreshWorkspace(editingIntent ? 'Intent updated' : 'Intent created')
    } catch (actionError) {
      toast.error(actionError instanceof Error ? actionError.message : 'Failed to save intent')
    } finally {
      setBusyKey(null)
    }
  }

  async function handleSaveTag() {
    if (!organizationId) {
      return
    }
    setBusyKey('save-tag')
    try {
      const payload = {
        organization_id: organizationId,
        agent_id: tagForm.agent_id === 'none' ? null : tagForm.agent_id,
        taxonomy_version_id: tagForm.taxonomy_version_id === 'live' ? null : tagForm.taxonomy_version_id,
        name: tagForm.name.trim(),
        display_name: tagForm.display_name.trim(),
        description: tagForm.description.trim() || null,
        tag_kind: tagForm.tag_kind,
        category: tagForm.category.trim() || null,
        confidence_threshold: Number(tagForm.confidence_threshold),
        apply_scope: tagForm.apply_scope,
        related_intent_id: tagForm.related_intent_id === 'none' ? null : tagForm.related_intent_id,
        is_active: tagForm.is_active,
        is_deprecated: tagForm.is_deprecated,
        color: tagForm.color.trim() || null,
        icon: tagForm.icon.trim() || null,
        rule_config: parseJsonObject(tagForm.rule_config_json, 'Tag rule config'),
        metadata: parseJsonObject(tagForm.metadata_json, 'Tag metadata'),
      }

      if (editingTag) {
        await intentTagsService.updateTag(editingTag.tag_definition_id, payload, {
          organization_id: organizationId,
        })
      } else {
        await intentTagsService.createTag(payload)
      }

      setTagDialogOpen(false)
      setEditingTag(null)
      await refreshWorkspace(editingTag ? 'Tag updated' : 'Tag created')
    } catch (actionError) {
      toast.error(actionError instanceof Error ? actionError.message : 'Failed to save tag')
    } finally {
      setBusyKey(null)
    }
  }

  async function handleSaveProfile() {
    if (!organizationId) {
      return
    }
    setBusyKey('save-profile')
    try {
      const payload = {
        organization_id: organizationId,
        agent_id: profileForm.agent_id === 'none' ? null : profileForm.agent_id,
        adapter_name: profileForm.adapter_name.trim(),
        supported_languages: parseCommaList(profileForm.supported_languages),
        taxonomy_mode: profileForm.taxonomy_mode,
        taxonomy_version_id:
          profileForm.taxonomy_mode === 'pinned' && profileForm.taxonomy_version_id !== 'live'
            ? profileForm.taxonomy_version_id
            : null,
        tool_catalog: parseJsonArrayOfObjects(profileForm.tool_catalog_json, 'Tool catalog'),
        policy_profile: parseJsonObject(profileForm.policy_profile_json, 'Policy profile'),
        profile_metadata: parseJsonObject(profileForm.profile_metadata_json, 'Profile metadata'),
        is_active: profileForm.is_active,
      }

      if (editingProfile) {
        await intentTagsService.updateProfile(editingProfile.classifier_profile_id, payload, {
          organization_id: organizationId,
        })
      } else {
        await intentTagsService.createProfile(payload)
      }

      setProfileDialogOpen(false)
      setEditingProfile(null)
      await refreshWorkspace(editingProfile ? 'Classifier profile updated' : 'Classifier profile created')
    } catch (actionError) {
      toast.error(actionError instanceof Error ? actionError.message : 'Failed to save profile')
    } finally {
      setBusyKey(null)
    }
  }

  async function handleRebuildProfile(profile: ClassifierProfile) {
    if (!organizationId) {
      return
    }
    setBusyKey(`rebuild-profile-${profile.classifier_profile_id}`)
    try {
      await intentTagsService.rebuildProfile(profile.classifier_profile_id, {
        organization_id: organizationId,
        agent_id: profile.agent_id,
        live_tool_catalog: profile.tool_catalog,
      })
      await refreshWorkspace(`Rebuilt ${profile.adapter_name}`)
    } catch (actionError) {
      toast.error(actionError instanceof Error ? actionError.message : 'Failed to rebuild profile cache')
    } finally {
      setBusyKey(null)
    }
  }

  async function handleSaveWebhookTarget() {
    if (!organizationId) {
      return
    }
    setBusyKey('save-webhook')
    try {
      const payload = {
        organization_id: organizationId,
        name: webhookForm.name.trim(),
        url: webhookForm.url.trim(),
        agent_ids: webhookForm.agent_ids,
        channels: webhookForm.channels,
        signing_secret_ref: webhookForm.signing_secret_ref.trim() || null,
        extra_headers: parseJsonStringMap(webhookForm.extra_headers_json, 'Extra headers'),
        timeout_seconds: Number(webhookForm.timeout_seconds),
        max_retries: Number(webhookForm.max_retries),
        retry_backoff_seconds: Number(webhookForm.retry_backoff_seconds),
        is_active: webhookForm.is_active,
      }

      if (editingWebhookTarget) {
        await intentTagsService.updateWebhookTarget(editingWebhookTarget.webhook_target_id, payload, {
          organization_id: organizationId,
        })
      } else {
        await intentTagsService.createWebhookTarget(payload)
      }

      setWebhookDialogOpen(false)
      setEditingWebhookTarget(null)
      await refreshWorkspace(editingWebhookTarget ? 'Webhook target updated' : 'Webhook target created')
    } catch (actionError) {
      toast.error(actionError instanceof Error ? actionError.message : 'Failed to save webhook target')
    } finally {
      setBusyKey(null)
    }
  }

  async function handleDeleteWebhookTarget(target: SemanticWebhookTargetReadModel) {
    if (!organizationId) {
      return
    }
    setBusyKey(`delete-webhook-${target.webhook_target_id}`)
    try {
      await intentTagsService.deleteWebhookTarget(target.webhook_target_id, {
        organization_id: organizationId,
      })
      await refreshWorkspace(`Deleted ${target.name}`)
    } catch (actionError) {
      toast.error(actionError instanceof Error ? actionError.message : 'Failed to delete webhook target')
    } finally {
      setBusyKey(null)
    }
  }

  async function handleDispatchWebhooks() {
    if (!organizationId) {
      return
    }
    setBusyKey('dispatch-webhooks')
    try {
      const result = await intentTagsService.dispatchSemanticWebhooks({
        organization_id: organizationId,
        conversation_id: dispatchConversationId.trim() || undefined,
        mode: dispatchMode,
        limit: 200,
      })
      setDispatchResult(result)
      await refreshWorkspace('Semantic summary dispatch completed')
    } catch (actionError) {
      toast.error(actionError instanceof Error ? actionError.message : 'Failed to dispatch webhooks')
    } finally {
      setBusyKey(null)
    }
  }

  async function handleClaimReview(row: ReviewQueueRowReadModel) {
    if (!organizationId) {
      return
    }
    setBusyKey(`claim-review-${row.review_item.review_item_id}`)
    try {
      await intentTagsService.claimReviewItem(
        row.review_item.review_item_id,
        { user_id: user?.user_id ?? null },
        { organization_id: organizationId }
      )
      await refreshWorkspace('Review item claimed')
    } catch (actionError) {
      toast.error(actionError instanceof Error ? actionError.message : 'Failed to claim review')
    } finally {
      setBusyKey(null)
    }
  }

  async function handleResolveReview() {
    if (!organizationId || !selectedReview) {
      return
    }
    setBusyKey(`resolve-review-${selectedReview.review_item.review_item_id}`)
    try {
      if (selectedReview.target_kind === 'turn') {
        await intentTagsService.resolveTurnReview(
          selectedReview.review_item.review_item_id,
          {
            user_id: user?.user_id ?? null,
            disposition: reviewResolution.disposition,
            corrected_decision:
              reviewResolution.disposition === 'corrected'
                ? (() => {
                    const parsed = parseJsonObject(
                      reviewResolution.corrected_decision_json,
                      'Corrected decision'
                    )
                    return {
                      intent_name: String(parsed.intent_name ?? ''),
                      confidence: Number(parsed.confidence ?? 0),
                      language: String(parsed.language ?? ''),
                      response_language: String(parsed.response_language ?? ''),
                      tool_route: parsed.tool_route == null ? null : String(parsed.tool_route),
                      slots:
                        parsed.slots && typeof parsed.slots === 'object' && !Array.isArray(parsed.slots)
                          ? (parsed.slots as Record<string, unknown>)
                          : {},
                      signals:
                        parsed.signals && typeof parsed.signals === 'object' && !Array.isArray(parsed.signals)
                          ? (parsed.signals as Record<string, unknown>)
                          : {},
                    }
                  })()
                : null,
            review_notes: reviewResolution.review_notes.trim() || null,
          },
          { organization_id: organizationId }
        )
      } else {
        await intentTagsService.resolveSummaryReview(
          selectedReview.review_item.review_item_id,
          {
            user_id: user?.user_id ?? null,
            disposition: reviewResolution.disposition,
            corrected_fields:
              reviewResolution.disposition === 'corrected'
                ? parseJsonObject(reviewResolution.corrected_fields_json, 'Corrected summary fields')
                : {},
            corrected_tag_definition_ids:
              reviewResolution.disposition === 'corrected'
                ? parseCommaList(reviewResolution.corrected_tag_definition_ids)
                : [],
            review_notes: reviewResolution.review_notes.trim() || null,
          },
          { organization_id: organizationId }
        )
      }

      setReviewDialogOpen(false)
      setSelectedReview(null)
      await refreshWorkspace('Review resolved')
    } catch (actionError) {
      toast.error(actionError instanceof Error ? actionError.message : 'Failed to resolve review')
    } finally {
      setBusyKey(null)
    }
  }

  async function handleOpenSummary(summary: SummaryListItemReadModel) {
    if (!organizationId) {
      return
    }
    setSelectedSummary(summary)
    setSummaryDialogOpen(true)
    setSummaryDetail(null)
    setBusyKey(`summary-detail-${summary.summary.conversation_summary_id}`)
    try {
      const detail = await intentTagsService.getSummaryDetail(summary.summary.conversation_summary_id, {
        organization_id: organizationId,
      })
      setSummaryDetail(detail)
    } catch (actionError) {
      toast.error(actionError instanceof Error ? actionError.message : 'Failed to load summary detail')
    } finally {
      setBusyKey(null)
    }
  }

  return {
    handleCreateVersion,
    handlePublishVersion,
    handleSaveIntent,
    handleSaveTag,
    handleSaveProfile,
    handleRebuildProfile,
    handleSaveWebhookTarget,
    handleDeleteWebhookTarget,
    handleDispatchWebhooks,
    handleClaimReview,
    handleResolveReview,
    handleOpenSummary,
  }
}

export type IntentTagsActions = ReturnType<typeof useIntentTagsActions>
