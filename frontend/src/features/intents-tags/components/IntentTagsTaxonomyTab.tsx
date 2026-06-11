import { Loader2 } from 'lucide-react'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Card } from '@/components/atoms/card'
import type {
  ClassifierProfile,
  IntentDefinition,
  TagDefinition,
  TaxonomySnapshotReadModel,
  TaxonomyVersion,
} from '@/types/intent-tags'
import {
  formatDateTime,
  formatPercent,
  statusVariant,
  titleCaseFromSnake,
} from '../utils/intent-tags-helpers'

export function IntentTagsTaxonomyTab({
  taxonomy,
  canWrite,
  busyKey,
  onCreateVersion,
  onPublishVersion,
  onAddIntent,
  onEditIntent,
  onAddTag,
  onEditTag,
  onAddProfile,
  onEditProfile,
  onRebuildProfile,
}: {
  taxonomy: TaxonomySnapshotReadModel | null
  canWrite: boolean
  busyKey: string | null
  onCreateVersion: () => void
  onPublishVersion: (version: TaxonomyVersion) => void
  onAddIntent: () => void
  onEditIntent: (intent: IntentDefinition) => void
  onAddTag: () => void
  onEditTag: (tagDefinition: TagDefinition) => void
  onAddProfile: () => void
  onEditProfile: (profile: ClassifierProfile) => void
  onRebuildProfile: (profile: ClassifierProfile) => void
}) {
  return (
    <div className="grid gap-6 xl:grid-cols-[0.95fr,1.05fr]">
      <Card className="p-5">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">Taxonomy versions</h2>
            <p className="text-sm text-muted-foreground">
              Draft and published version snapshots for the current organization.
            </p>
          </div>
          {canWrite ? (
            <Button size="sm" onClick={onCreateVersion}>
              Create version
            </Button>
          ) : null}
        </div>
        <div className="space-y-3">
          {(taxonomy?.taxonomy_versions ?? []).map((version) => (
            <div key={version.taxonomy_version_id} className="rounded-2xl border border-border/60 p-4">
              <div className="flex items-start justify-between gap-4">
                <div className="space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-medium">{version.name}</p>
                    <Badge variant={statusVariant(version.status)}>{titleCaseFromSnake(version.status)}</Badge>
                  </div>
                  {version.notes ? <p className="text-sm text-muted-foreground">{version.notes}</p> : null}
                  <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                    <span>Created {formatDateTime(version.created_at)}</span>
                    {version.published_at ? <span>Published {formatDateTime(version.published_at)}</span> : null}
                  </div>
                </div>
                {canWrite && version.status !== 'published' ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => onPublishVersion(version)}
                    disabled={busyKey === `publish-version-${version.taxonomy_version_id}`}
                  >
                    {busyKey === `publish-version-${version.taxonomy_version_id}` ? (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : null}
                    Publish
                  </Button>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      </Card>

      <div className="space-y-6">
        <Card className="p-5">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold tracking-tight">Intents</h2>
              <p className="text-sm text-muted-foreground">
                Runtime intent definitions available to the classifier profile.
              </p>
            </div>
            {canWrite ? (
              <Button size="sm" onClick={onAddIntent}>
                Add intent
              </Button>
            ) : null}
          </div>
          <div className="space-y-3">
            {(taxonomy?.intents ?? []).map((intent) => (
              <div key={intent.intent_definition_id} className="rounded-2xl border border-border/60 p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-medium">{intent.display_name}</p>
                      <Badge variant={intent.is_active ? 'success' : 'secondary'}>
                        {intent.is_active ? 'Active' : 'Inactive'}
                      </Badge>
                      {intent.is_deprecated ? <Badge variant="destructive">Deprecated</Badge> : null}
                    </div>
                    <p className="text-sm text-muted-foreground">{intent.name}</p>
                    <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                      <span>Priority {intent.priority}</span>
                      <span>Threshold {formatPercent(intent.confidence_threshold)}</span>
                      {intent.category ? <span>{intent.category}</span> : null}
                      {intent.agent_id ? <span>{intent.agent_id}</span> : null}
                    </div>
                  </div>
                  {canWrite ? (
                    <Button variant="outline" size="sm" onClick={() => onEditIntent(intent)}>
                      Edit
                    </Button>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card className="p-5">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold tracking-tight">Tags</h2>
              <p className="text-sm text-muted-foreground">
                Deterministic labels for blockers, outcomes, risks, and priorities.
              </p>
            </div>
            {canWrite ? (
              <Button size="sm" onClick={onAddTag}>
                Add tag
              </Button>
            ) : null}
          </div>
          <div className="space-y-3">
            {(taxonomy?.tags ?? []).map((tagDefinition) => (
              <div key={tagDefinition.tag_definition_id} className="rounded-2xl border border-border/60 p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-medium">{tagDefinition.display_name}</p>
                      <Badge variant={statusVariant(tagDefinition.tag_kind)}>
                        {titleCaseFromSnake(tagDefinition.tag_kind)}
                      </Badge>
                      <Badge variant={tagDefinition.is_active ? 'success' : 'secondary'}>
                        {tagDefinition.is_active ? 'Active' : 'Inactive'}
                      </Badge>
                    </div>
                    <p className="text-sm text-muted-foreground">{tagDefinition.name}</p>
                    <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                      <span>Scope {titleCaseFromSnake(tagDefinition.apply_scope)}</span>
                      <span>Threshold {formatPercent(tagDefinition.confidence_threshold)}</span>
                      {tagDefinition.category ? <span>{tagDefinition.category}</span> : null}
                      {tagDefinition.related_intent_id ? <span>Intent link {tagDefinition.related_intent_id}</span> : null}
                    </div>
                  </div>
                  {canWrite ? (
                    <Button variant="outline" size="sm" onClick={() => onEditTag(tagDefinition)}>
                      Edit
                    </Button>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card className="p-5">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold tracking-tight">Classifier profiles</h2>
              <p className="text-sm text-muted-foreground">
                Managed adapter binding, taxonomy mode, and runtime cache state.
              </p>
            </div>
            {canWrite ? (
              <Button size="sm" onClick={onAddProfile}>
                Add profile
              </Button>
            ) : null}
          </div>
          <div className="space-y-3">
            {(taxonomy?.profiles ?? []).map((profile) => (
              <div key={profile.classifier_profile_id} className="rounded-2xl border border-border/60 p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-medium">{profile.adapter_name}</p>
                      <Badge variant={profile.is_active ? 'success' : 'secondary'}>
                        {profile.is_active ? 'Active' : 'Inactive'}
                      </Badge>
                      <Badge variant="outline">{titleCaseFromSnake(profile.taxonomy_mode)}</Badge>
                    </div>
                    <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                      {profile.agent_id ? <span>{profile.agent_id}</span> : <span>Global</span>}
                      <span>{profile.supported_languages.join(', ') || 'No language pin'}</span>
                      <span>{profile.intent_catalog.length} intents cached</span>
                      <span>{profile.tool_catalog.length} tools cached</span>
                    </div>
                    {profile.catalog_cache_built_at ? (
                      <p className="text-xs text-muted-foreground">
                        Cache built {formatDateTime(profile.catalog_cache_built_at)}
                      </p>
                    ) : null}
                  </div>
                  {canWrite ? (
                    <div className="flex flex-wrap gap-2">
                      <Button variant="outline" size="sm" onClick={() => onEditProfile(profile)}>
                        Edit
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => onRebuildProfile(profile)}
                        disabled={busyKey === `rebuild-profile-${profile.classifier_profile_id}`}
                      >
                        {busyKey === `rebuild-profile-${profile.classifier_profile_id}` ? (
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        ) : null}
                        Rebuild
                      </Button>
                    </div>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  )
}
