import type { Dispatch, SetStateAction } from 'react'
import { Checkbox } from '@/components/atoms/checkbox'
import { Input } from '@/components/atoms/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
import { Textarea } from '@/components/atoms/textarea'
import type {
  AgentSummary,
  ClassifierProfile,
  SemanticWebhookTargetReadModel,
  TaxonomySnapshotReadModel,
} from '@/types/intent-tags'
import { titleCaseFromSnake } from '../utils/intent-tags-helpers'
import {
  RUNTIME_CHANNEL_OPTIONS,
  type ProfileFormState,
  type WebhookFormState,
} from '../utils/intent-tags-form-state'
import { FieldShell, TaxonomyEditorDialog } from './IntentTagsPrimitives'

export function IntentTagsProfileDialog({
  open,
  onOpenChange,
  editingProfile,
  profileForm,
  setProfileForm,
  agents,
  taxonomy,
  submitting,
  onSubmit,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  editingProfile: ClassifierProfile | null
  profileForm: ProfileFormState
  setProfileForm: Dispatch<SetStateAction<ProfileFormState>>
  agents: AgentSummary[]
  taxonomy: TaxonomySnapshotReadModel | null
  submitting: boolean
  onSubmit: () => void
}) {
  return (
    <TaxonomyEditorDialog
      open={open}
      onOpenChange={onOpenChange}
      title={editingProfile ? 'Edit classifier profile' : 'Create classifier profile'}
      description="Bind an agent and taxonomy mode to a runtime or hosted classifier adapter."
      submitLabel={editingProfile ? 'Save profile' : 'Create profile'}
      submitting={submitting}
      onSubmit={onSubmit}
    >
      <div className="grid gap-4 md:grid-cols-2">
        <FieldShell label="Adapter name">
          <Input
            value={profileForm.adapter_name}
            onChange={(event) =>
              setProfileForm((current) => ({ ...current, adapter_name: event.target.value }))
            }
            placeholder="ruhu-general or gemma_local"
          />
        </FieldShell>
        <FieldShell label="Agent">
          <Select
            value={profileForm.agent_id}
            onValueChange={(value) => setProfileForm((current) => ({ ...current, agent_id: value }))}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">Global</SelectItem>
              {agents.map((agent) => (
                <SelectItem key={agent.id} value={agent.id}>
                  {agent.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FieldShell>
        <FieldShell label="Supported languages" description="Comma-separated language codes.">
          <Input
            value={profileForm.supported_languages}
            onChange={(event) =>
              setProfileForm((current) => ({ ...current, supported_languages: event.target.value }))
            }
            placeholder="en, fr"
          />
        </FieldShell>
        <FieldShell label="Taxonomy mode">
          <Select
            value={profileForm.taxonomy_mode}
            onValueChange={(value) =>
              setProfileForm((current) => ({
                ...current,
                taxonomy_mode: value as ClassifierProfile['taxonomy_mode'],
              }))
            }
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="live">Live</SelectItem>
              <SelectItem value="pinned">Pinned version</SelectItem>
              <SelectItem value="cached_live">Cached live</SelectItem>
            </SelectContent>
          </Select>
        </FieldShell>
        <FieldShell label="Pinned taxonomy version">
          <Select
            value={profileForm.taxonomy_version_id}
            onValueChange={(value) =>
              setProfileForm((current) => ({ ...current, taxonomy_version_id: value }))
            }
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="live">No pinned version</SelectItem>
              {(taxonomy?.taxonomy_versions ?? []).map((version) => (
                <SelectItem key={version.taxonomy_version_id} value={version.taxonomy_version_id}>
                  {version.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FieldShell>
        <label className="flex items-center gap-3 rounded-xl border border-border/60 px-4 py-3">
          <Checkbox
            checked={profileForm.is_active}
            onCheckedChange={(checked) =>
              setProfileForm((current) => ({ ...current, is_active: checked === true }))
            }
          />
          <div>
            <p className="text-sm font-medium">Active</p>
            <p className="text-xs text-muted-foreground">Resolve for runtime integration</p>
          </div>
        </label>
      </div>
      <FieldShell label="Tool catalog JSON">
        <Textarea
          value={profileForm.tool_catalog_json}
          onChange={(event) =>
            setProfileForm((current) => ({ ...current, tool_catalog_json: event.target.value }))
          }
          className="min-h-[120px] font-mono text-xs"
        />
      </FieldShell>
      <FieldShell label="Policy profile JSON">
        <Textarea
          value={profileForm.policy_profile_json}
          onChange={(event) =>
            setProfileForm((current) => ({ ...current, policy_profile_json: event.target.value }))
          }
          className="min-h-[120px] font-mono text-xs"
        />
      </FieldShell>
      <FieldShell label="Profile metadata JSON">
        <Textarea
          value={profileForm.profile_metadata_json}
          onChange={(event) =>
            setProfileForm((current) => ({ ...current, profile_metadata_json: event.target.value }))
          }
          className="min-h-[120px] font-mono text-xs"
        />
      </FieldShell>
    </TaxonomyEditorDialog>
  )
}

export function IntentTagsWebhookDialog({
  open,
  onOpenChange,
  editingWebhookTarget,
  webhookForm,
  setWebhookForm,
  agents,
  submitting,
  onSubmit,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  editingWebhookTarget: SemanticWebhookTargetReadModel | null
  webhookForm: WebhookFormState
  setWebhookForm: Dispatch<SetStateAction<WebhookFormState>>
  agents: AgentSummary[]
  submitting: boolean
  onSubmit: () => void
}) {
  return (
    <TaxonomyEditorDialog
      open={open}
      onOpenChange={onOpenChange}
      title={editingWebhookTarget ? 'Edit webhook target' : 'Create webhook target'}
      description="Subscribe downstream systems to semantic summary finalized events."
      submitLabel={editingWebhookTarget ? 'Save target' : 'Create target'}
      submitting={submitting}
      onSubmit={onSubmit}
    >
      <div className="grid gap-4 md:grid-cols-2">
        <FieldShell label="Name">
          <Input
            value={webhookForm.name}
            onChange={(event) => setWebhookForm((current) => ({ ...current, name: event.target.value }))}
          />
        </FieldShell>
        <FieldShell label="Endpoint URL">
          <Input
            value={webhookForm.url}
            onChange={(event) => setWebhookForm((current) => ({ ...current, url: event.target.value }))}
            placeholder="https://ops.example.com/semantic"
          />
        </FieldShell>
        <FieldShell label="Signing secret reference">
          <Input
            value={webhookForm.signing_secret_ref}
            onChange={(event) =>
              setWebhookForm((current) => ({ ...current, signing_secret_ref: event.target.value }))
            }
            placeholder="env:SEMANTIC_WEBHOOK_SECRET"
          />
        </FieldShell>
        <FieldShell label="Timeout seconds">
          <Input
            type="number"
            min={1}
            max={120}
            step="0.5"
            value={webhookForm.timeout_seconds}
            onChange={(event) =>
              setWebhookForm((current) => ({ ...current, timeout_seconds: event.target.value }))
            }
          />
        </FieldShell>
        <FieldShell label="Max retries">
          <Input
            type="number"
            min={0}
            max={25}
            value={webhookForm.max_retries}
            onChange={(event) =>
              setWebhookForm((current) => ({ ...current, max_retries: event.target.value }))
            }
          />
        </FieldShell>
        <FieldShell label="Retry backoff seconds">
          <Input
            type="number"
            min={0}
            max={3600}
            step="0.5"
            value={webhookForm.retry_backoff_seconds}
            onChange={(event) =>
              setWebhookForm((current) => ({ ...current, retry_backoff_seconds: event.target.value }))
            }
          />
        </FieldShell>
      </div>

      <FieldShell label="Allowed agents">
        <div className="grid gap-2 md:grid-cols-2">
          {agents.map((agent) => {
            const checked = webhookForm.agent_ids.includes(agent.id)
            return (
              <label
                key={agent.id}
                className="flex items-center gap-3 rounded-xl border border-border/60 px-4 py-3"
              >
                <Checkbox
                  checked={checked}
                  onCheckedChange={(nextChecked) =>
                    setWebhookForm((current) => ({
                      ...current,
                      agent_ids:
                        nextChecked === true
                          ? [...current.agent_ids, agent.id]
                          : current.agent_ids.filter((item) => item !== agent.id),
                    }))
                  }
                />
                <span className="text-sm">{agent.name}</span>
              </label>
            )
          })}
        </div>
      </FieldShell>

      <FieldShell label="Allowed channels">
        <div className="grid gap-2 md:grid-cols-3">
          {RUNTIME_CHANNEL_OPTIONS.map((channel) => {
            const checked = webhookForm.channels.includes(channel)
            return (
              <label
                key={channel}
                className="flex items-center gap-3 rounded-xl border border-border/60 px-4 py-3"
              >
                <Checkbox
                  checked={checked}
                  onCheckedChange={(nextChecked) =>
                    setWebhookForm((current) => ({
                      ...current,
                      channels:
                        nextChecked === true
                          ? [...current.channels, channel]
                          : current.channels.filter((item) => item !== channel),
                    }))
                  }
                />
                <span className="text-sm">{titleCaseFromSnake(channel)}</span>
              </label>
            )
          })}
        </div>
      </FieldShell>

      <FieldShell label="Extra headers JSON">
        <Textarea
          value={webhookForm.extra_headers_json}
          onChange={(event) =>
            setWebhookForm((current) => ({ ...current, extra_headers_json: event.target.value }))
          }
          className="min-h-[120px] font-mono text-xs"
        />
      </FieldShell>

      <label className="flex items-center gap-3 rounded-xl border border-border/60 px-4 py-3">
        <Checkbox
          checked={webhookForm.is_active}
          onCheckedChange={(checked) =>
            setWebhookForm((current) => ({ ...current, is_active: checked === true }))
          }
        />
        <div>
          <p className="text-sm font-medium">Active</p>
          <p className="text-xs text-muted-foreground">Deliver publication events to this target</p>
        </div>
      </label>
    </TaxonomyEditorDialog>
  )
}
