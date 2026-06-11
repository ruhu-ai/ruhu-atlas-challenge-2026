import { Activity, ArrowUpRight, Bot, ExternalLink, Tag, Target, Wand2 } from 'lucide-react'
import { Badge } from '@/components/atoms/badge'
import { Card } from '@/components/atoms/card'
import type {
  IntentTagsAnalyticsReadModel,
  IntentTagsInsightsReadModel,
  SemanticWebhookTargetReadModel,
  TaxonomySnapshotReadModel,
} from '@/types/intent-tags'
import {
  formatPercent,
  formatRelativeNumber,
  statusVariant,
  titleCaseFromSnake,
} from '../utils/intent-tags-helpers'
import { MetricCard } from './IntentTagsPrimitives'

export function IntentTagsOverviewTab({
  taxonomy,
  analytics,
  insights,
  webhookTargets,
}: {
  taxonomy: TaxonomySnapshotReadModel | null
  analytics: IntentTagsAnalyticsReadModel | null
  insights: IntentTagsInsightsReadModel | null
  webhookTargets: SemanticWebhookTargetReadModel[]
}) {
  return (
    <>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="Intents"
          value={formatRelativeNumber(taxonomy?.intents.length)}
          detail="Effective definitions in the selected scope"
          icon={Target}
        />
        <MetricCard
          label="Tags"
          value={formatRelativeNumber(taxonomy?.tags.length)}
          detail="Deterministic and summary-level labels"
          icon={Tag}
          tone="info"
        />
        <MetricCard
          label="Profiles"
          value={formatRelativeNumber(taxonomy?.profiles.length)}
          detail="Live classifier adapter profiles"
          icon={Bot}
          tone="warning"
        />
        <MetricCard
          label="Webhooks"
          value={formatRelativeNumber(webhookTargets.filter((target) => target.is_active).length)}
          detail="Active publication targets"
          icon={ExternalLink}
          tone="success"
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-[1.2fr,0.8fr]">
        <Card className="p-5">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold tracking-tight">Semantic insights</h2>
              <p className="text-sm text-muted-foreground">
                Ranked blockers and repeat failure patterns from conversation summaries.
              </p>
            </div>
            <Badge variant="outline">{formatRelativeNumber(insights?.rows.length)}</Badge>
          </div>
          <div className="space-y-3">
            {(insights?.rows ?? []).slice(0, 6).map((row) => (
              <div
                key={row.insight_key}
                className="rounded-2xl border border-border/60 bg-muted/25 p-4"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-medium">{row.title}</p>
                      <Badge variant={statusVariant(row.blocker_kind)}>{titleCaseFromSnake(row.blocker_kind)}</Badge>
                      {row.requires_human_followup ? <Badge variant="warning">Human follow-up</Badge> : null}
                    </div>
                    <p className="text-sm text-muted-foreground">{row.summary}</p>
                  </div>
                  <div className="text-right text-sm">
                    <p className="font-medium">{formatRelativeNumber(row.occurrence_count)}</p>
                    <p className="text-muted-foreground">{formatPercent(row.coverage_ratio)} coverage</p>
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
                  {row.primary_intent_name ? <span>Intent: {row.primary_intent_name}</span> : null}
                  {row.tag_name ? <span>Tag: {row.tag_name}</span> : null}
                  {row.outcome ? <span>Outcome: {row.outcome}</span> : null}
                  {row.resolution_status ? <span>Resolution: {row.resolution_status}</span> : null}
                </div>
              </div>
            ))}
            {insights && insights.rows.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
                No insight rows have been generated yet for this scope.
              </div>
            ) : null}
          </div>
        </Card>

        <div className="space-y-6">
          <Card className="p-5">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold tracking-tight">Outcome distribution</h2>
                <p className="text-sm text-muted-foreground">
                  Final summary outcome counts by channel and resolution.
                </p>
              </div>
              <Activity className="h-4 w-4 text-muted-foreground" />
            </div>
            <div className="space-y-3">
              {(analytics?.outcome_rows ?? []).slice(0, 8).map((row, index) => (
                <div key={`${row.channel}-${row.outcome}-${row.resolution_status}-${index}`} className="flex items-center justify-between rounded-xl border border-border/60 px-3 py-2">
                  <div>
                    <p className="text-sm font-medium">{titleCaseFromSnake(row.outcome ?? 'unknown')}</p>
                    <p className="text-xs text-muted-foreground">
                      {titleCaseFromSnake(row.channel)} · {titleCaseFromSnake(row.resolution_status ?? 'unknown')}
                    </p>
                  </div>
                  <Badge variant="outline">{formatRelativeNumber(row.count)}</Badge>
                </div>
              ))}
            </div>
          </Card>

          <Card className="p-5">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold tracking-tight">Review backlog</h2>
                <p className="text-sm text-muted-foreground">
                  Live queue distribution by review status.
                </p>
              </div>
              <Wand2 className="h-4 w-4 text-muted-foreground" />
            </div>
            <div className="space-y-3">
              {Object.entries(analytics?.review_status_counts ?? {}).map(([status, count]) => (
                <div key={status} className="flex items-center justify-between rounded-xl border border-border/60 px-3 py-2">
                  <Badge variant={statusVariant(status)}>{titleCaseFromSnake(status)}</Badge>
                  <span className="text-sm font-medium">{formatRelativeNumber(count)}</span>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card className="p-5">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold tracking-tight">Top intents</h2>
              <p className="text-sm text-muted-foreground">Summary and event volume by intent.</p>
            </div>
            <ArrowUpRight className="h-4 w-4 text-muted-foreground" />
          </div>
          <div className="space-y-3">
            {(analytics?.intent_rows ?? []).slice(0, 8).map((row) => (
              <div key={row.intent_name} className="rounded-xl border border-border/60 p-3">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <p className="font-medium">{row.display_name}</p>
                    <p className="text-xs text-muted-foreground">{row.intent_name}</p>
                  </div>
                  <Badge variant="outline">{formatRelativeNumber(row.turn_event_count)} turns</Badge>
                </div>
                <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
                  <span>{formatRelativeNumber(row.summary_count)} summaries</span>
                  <span>{formatRelativeNumber(row.review_count)} reviews</span>
                  <span>{formatRelativeNumber(row.low_confidence_turn_count)} low-confidence</span>
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card className="p-5">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold tracking-tight">Top tags</h2>
              <p className="text-sm text-muted-foreground">Assignment intensity and validation mix.</p>
            </div>
            <Tag className="h-4 w-4 text-muted-foreground" />
          </div>
          <div className="space-y-3">
            {(analytics?.tag_rows ?? []).slice(0, 8).map((row) => (
              <div key={row.tag_definition_id} className="rounded-xl border border-border/60 p-3">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <p className="font-medium">{row.display_name}</p>
                    <p className="text-xs text-muted-foreground">
                      {row.tag_name} · {titleCaseFromSnake(row.tag_kind)}
                    </p>
                  </div>
                  <Badge variant="outline">{formatRelativeNumber(row.assignment_count)} assignments</Badge>
                </div>
                <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
                  <span>{formatRelativeNumber(row.turn_assignment_count)} turn</span>
                  <span>{formatRelativeNumber(row.conversation_assignment_count)} conversation</span>
                  <span>{formatRelativeNumber(row.validated_count)} validated</span>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </>
  )
}
