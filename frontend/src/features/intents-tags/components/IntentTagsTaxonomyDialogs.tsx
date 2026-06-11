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
  IntentDefinition,
  TagDefinition,
  TaxonomySnapshotReadModel,
} from '@/types/intent-tags'
import { titleCaseFromSnake } from '../utils/intent-tags-helpers'
import {
  TAG_KIND_OPTIONS,
  type IntentFormState,
  type TagFormState,
  type VersionFormState,
} from '../utils/intent-tags-form-state'
import { FieldShell, TaxonomyEditorDialog } from './IntentTagsPrimitives'

export function IntentTagsVersionDialog({
  open,
  onOpenChange,
  versionForm,
  setVersionForm,
  submitting,
  onSubmit,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  versionForm: VersionFormState
  setVersionForm: Dispatch<SetStateAction<VersionFormState>>
  submitting: boolean
  onSubmit: () => void
}) {
  return (
    <TaxonomyEditorDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Create taxonomy version"
      description="Create a named version snapshot before editing or publishing."
      submitLabel="Create version"
      submitting={submitting}
      onSubmit={onSubmit}
    >
      <FieldShell label="Version name">
        <Input
          value={versionForm.name}
          onChange={(event) => setVersionForm((current) => ({ ...current, name: event.target.value }))}
          placeholder="April launch taxonomy"
        />
      </FieldShell>
      <FieldShell label="Notes">
        <Textarea
          value={versionForm.notes}
          onChange={(event) => setVersionForm((current) => ({ ...current, notes: event.target.value }))}
          placeholder="Scope, review notes, or release intent"
        />
      </FieldShell>
    </TaxonomyEditorDialog>
  )
}

export function IntentTagsIntentDialog({
  open,
  onOpenChange,
  editingIntent,
  intentForm,
  setIntentForm,
  agents,
  taxonomy,
  submitting,
  onSubmit,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  editingIntent: IntentDefinition | null
  intentForm: IntentFormState
  setIntentForm: Dispatch<SetStateAction<IntentFormState>>
  agents: AgentSummary[]
  taxonomy: TaxonomySnapshotReadModel | null
  submitting: boolean
  onSubmit: () => void
}) {
  return (
    <TaxonomyEditorDialog
      open={open}
      onOpenChange={onOpenChange}
      title={editingIntent ? 'Edit intent' : 'Create intent'}
      description="Maintain the taxonomy entries used for turn classification and summary rollups."
      submitLabel={editingIntent ? 'Save intent' : 'Create intent'}
      submitting={submitting}
      onSubmit={onSubmit}
    >
      <div className="grid gap-4 md:grid-cols-2">
        <FieldShell label="Machine name">
          <Input
            disabled={Boolean(editingIntent)}
            value={intentForm.name}
            onChange={(event) => setIntentForm((current) => ({ ...current, name: event.target.value }))}
            placeholder="refund_request"
          />
        </FieldShell>
        <FieldShell label="Display name">
          <Input
            value={intentForm.display_name}
            onChange={(event) => setIntentForm((current) => ({ ...current, display_name: event.target.value }))}
            placeholder="Refund request"
          />
        </FieldShell>
        <FieldShell label="Agent">
          <Select
            value={intentForm.agent_id}
            onValueChange={(value) => setIntentForm((current) => ({ ...current, agent_id: value }))}
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
        <FieldShell label="Taxonomy version">
          <Select
            value={intentForm.taxonomy_version_id}
            onValueChange={(value) =>
              setIntentForm((current) => ({ ...current, taxonomy_version_id: value }))
            }
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="live">Live taxonomy</SelectItem>
              {(taxonomy?.taxonomy_versions ?? []).map((version) => (
                <SelectItem key={version.taxonomy_version_id} value={version.taxonomy_version_id}>
                  {version.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FieldShell>
        <FieldShell label="Category">
          <Input
            value={intentForm.category}
            onChange={(event) => setIntentForm((current) => ({ ...current, category: event.target.value }))}
            placeholder="billing"
          />
        </FieldShell>
        <FieldShell label="Priority">
          <Input
            type="number"
            min={0}
            value={intentForm.priority}
            onChange={(event) => setIntentForm((current) => ({ ...current, priority: event.target.value }))}
          />
        </FieldShell>
        <FieldShell label="Confidence threshold">
          <Input
            type="number"
            min={0}
            max={1}
            step="0.01"
            value={intentForm.confidence_threshold}
            onChange={(event) =>
              setIntentForm((current) => ({ ...current, confidence_threshold: event.target.value }))
            }
          />
        </FieldShell>
        <FieldShell label="Color">
          <Input
            value={intentForm.color}
            onChange={(event) => setIntentForm((current) => ({ ...current, color: event.target.value }))}
            placeholder="#0f766e"
          />
        </FieldShell>
      </div>
      <FieldShell label="Description">
        <Textarea
          value={intentForm.description}
          onChange={(event) => setIntentForm((current) => ({ ...current, description: event.target.value }))}
          placeholder="When the customer asks for a refund or credit"
        />
      </FieldShell>
      <FieldShell label="Example phrases" description="One phrase per line.">
        <Textarea
          value={intentForm.example_phrases}
          onChange={(event) => setIntentForm((current) => ({ ...current, example_phrases: event.target.value }))}
          placeholder={'I need a refund\nPlease reverse this charge'}
        />
      </FieldShell>
      <FieldShell label="Metadata JSON">
        <Textarea
          value={intentForm.metadata_json}
          onChange={(event) => setIntentForm((current) => ({ ...current, metadata_json: event.target.value }))}
          className="min-h-[140px] font-mono text-xs"
        />
      </FieldShell>
      <div className="grid gap-4 md:grid-cols-2">
        <label className="flex items-center gap-3 rounded-xl border border-border/60 px-4 py-3">
          <Checkbox
            checked={intentForm.is_active}
            onCheckedChange={(checked) =>
              setIntentForm((current) => ({ ...current, is_active: checked === true }))
            }
          />
          <div>
            <p className="text-sm font-medium">Active</p>
            <p className="text-xs text-muted-foreground">Include in live taxonomy resolution</p>
          </div>
        </label>
        <label className="flex items-center gap-3 rounded-xl border border-border/60 px-4 py-3">
          <Checkbox
            checked={intentForm.is_deprecated}
            onCheckedChange={(checked) =>
              setIntentForm((current) => ({ ...current, is_deprecated: checked === true }))
            }
          />
          <div>
            <p className="text-sm font-medium">Deprecated</p>
            <p className="text-xs text-muted-foreground">Keep for history without promoting new use</p>
          </div>
        </label>
      </div>
    </TaxonomyEditorDialog>
  )
}

export function IntentTagsTagDialog({
  open,
  onOpenChange,
  editingTag,
  tagForm,
  setTagForm,
  agents,
  taxonomy,
  submitting,
  onSubmit,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  editingTag: TagDefinition | null
  tagForm: TagFormState
  setTagForm: Dispatch<SetStateAction<TagFormState>>
  agents: AgentSummary[]
  taxonomy: TaxonomySnapshotReadModel | null
  submitting: boolean
  onSubmit: () => void
}) {
  return (
    <TaxonomyEditorDialog
      open={open}
      onOpenChange={onOpenChange}
      title={editingTag ? 'Edit tag' : 'Create tag'}
      description="Configure deterministic tag assignment rules and summary-level labels."
      submitLabel={editingTag ? 'Save tag' : 'Create tag'}
      submitting={submitting}
      onSubmit={onSubmit}
    >
      <div className="grid gap-4 md:grid-cols-2">
        <FieldShell label="Machine name">
          <Input
            disabled={Boolean(editingTag)}
            value={tagForm.name}
            onChange={(event) => setTagForm((current) => ({ ...current, name: event.target.value }))}
            placeholder="requires_human_followup"
          />
        </FieldShell>
        <FieldShell label="Display name">
          <Input
            value={tagForm.display_name}
            onChange={(event) => setTagForm((current) => ({ ...current, display_name: event.target.value }))}
            placeholder="Requires human follow-up"
          />
        </FieldShell>
        <FieldShell label="Tag kind">
          <Select
            value={tagForm.tag_kind}
            onValueChange={(value) =>
              setTagForm((current) => ({ ...current, tag_kind: value as TagDefinition['tag_kind'] }))
            }
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TAG_KIND_OPTIONS.map((tagKind) => (
                <SelectItem key={tagKind} value={tagKind}>
                  {titleCaseFromSnake(tagKind)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FieldShell>
        <FieldShell label="Apply scope">
          <Select
            value={tagForm.apply_scope}
            onValueChange={(value) =>
              setTagForm((current) => ({ ...current, apply_scope: value as TagDefinition['apply_scope'] }))
            }
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="turn">Turn</SelectItem>
              <SelectItem value="conversation">Conversation</SelectItem>
              <SelectItem value="both">Both</SelectItem>
            </SelectContent>
          </Select>
        </FieldShell>
        <FieldShell label="Agent">
          <Select
            value={tagForm.agent_id}
            onValueChange={(value) => setTagForm((current) => ({ ...current, agent_id: value }))}
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
        <FieldShell label="Category">
          <Input
            value={tagForm.category}
            onChange={(event) => setTagForm((current) => ({ ...current, category: event.target.value }))}
          />
        </FieldShell>
        <FieldShell label="Related intent">
          <Select
            value={tagForm.related_intent_id}
            onValueChange={(value) =>
              setTagForm((current) => ({ ...current, related_intent_id: value }))
            }
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">No related intent</SelectItem>
              {(taxonomy?.intents ?? []).map((intent) => (
                <SelectItem key={intent.intent_definition_id} value={intent.intent_definition_id}>
                  {intent.display_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FieldShell>
        <FieldShell label="Confidence threshold">
          <Input
            type="number"
            min={0}
            max={1}
            step="0.01"
            value={tagForm.confidence_threshold}
            onChange={(event) =>
              setTagForm((current) => ({ ...current, confidence_threshold: event.target.value }))
            }
          />
        </FieldShell>
        <FieldShell label="Color">
          <Input
            value={tagForm.color}
            onChange={(event) => setTagForm((current) => ({ ...current, color: event.target.value }))}
          />
        </FieldShell>
      </div>
      <FieldShell label="Description">
        <Textarea
          value={tagForm.description}
          onChange={(event) => setTagForm((current) => ({ ...current, description: event.target.value }))}
        />
      </FieldShell>
      <FieldShell label="Rule config JSON">
        <Textarea
          value={tagForm.rule_config_json}
          onChange={(event) => setTagForm((current) => ({ ...current, rule_config_json: event.target.value }))}
          className="min-h-[140px] font-mono text-xs"
        />
      </FieldShell>
      <FieldShell label="Metadata JSON">
        <Textarea
          value={tagForm.metadata_json}
          onChange={(event) => setTagForm((current) => ({ ...current, metadata_json: event.target.value }))}
          className="min-h-[140px] font-mono text-xs"
        />
      </FieldShell>
      <div className="grid gap-4 md:grid-cols-2">
        <label className="flex items-center gap-3 rounded-xl border border-border/60 px-4 py-3">
          <Checkbox
            checked={tagForm.is_active}
            onCheckedChange={(checked) =>
              setTagForm((current) => ({ ...current, is_active: checked === true }))
            }
          />
          <div>
            <p className="text-sm font-medium">Active</p>
            <p className="text-xs text-muted-foreground">Emit assignments in live runs</p>
          </div>
        </label>
        <label className="flex items-center gap-3 rounded-xl border border-border/60 px-4 py-3">
          <Checkbox
            checked={tagForm.is_deprecated}
            onCheckedChange={(checked) =>
              setTagForm((current) => ({ ...current, is_deprecated: checked === true }))
            }
          />
          <div>
            <p className="text-sm font-medium">Deprecated</p>
            <p className="text-xs text-muted-foreground">Preserve history without new promotion</p>
          </div>
        </label>
      </div>
    </TaxonomyEditorDialog>
  )
}
