import { Loader2 } from 'lucide-react'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Card } from '@/components/atoms/card'
import { Input } from '@/components/atoms/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/atoms/table'
import type {
  ReviewQueueRowReadModel,
  SemanticWebhookDispatchMode,
  SemanticWebhookDispatchResponse,
  SemanticWebhookTargetReadModel,
  SummaryListItemReadModel,
} from '@/types/intent-tags'
import {
  formatDateTime,
  formatRelativeNumber,
  statusVariant,
  titleCaseFromSnake,
} from '../utils/intent-tags-helpers'
import {
  REVIEW_STATUS_OPTIONS,
  SUMMARY_STATUS_OPTIONS,
  type ReviewStatusFilter,
  type SummaryStatusFilter,
} from '../utils/intent-tags-form-state'
import { FieldShell } from './IntentTagsPrimitives'

export function IntentTagsReviewsTab({
  reviews,
  reviewStatusFilter,
  onReviewStatusFilterChange,
  busyKey,
  onClaimReview,
  onResolveReview,
}: {
  reviews: ReviewQueueRowReadModel[]
  reviewStatusFilter: ReviewStatusFilter
  onReviewStatusFilterChange: (value: ReviewStatusFilter) => void
  busyKey: string | null
  onClaimReview: (row: ReviewQueueRowReadModel) => void
  onResolveReview: (row: ReviewQueueRowReadModel) => void
}) {
  return (
    <Card className="p-5">
      <div className="mb-4 flex items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Review queue</h2>
          <p className="text-sm text-muted-foreground">
            Claim low-confidence or summary-correction work and resolve it with explicit dispositions.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={reviewStatusFilter} onValueChange={(value) => onReviewStatusFilterChange(value as ReviewStatusFilter)}>
            <SelectTrigger className="w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {REVIEW_STATUS_OPTIONS.map((status) => (
                <SelectItem key={status} value={status}>
                  {status === 'all' ? 'All statuses' : titleCaseFromSnake(status)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Review</TableHead>
            <TableHead>Conversation</TableHead>
            <TableHead>Current</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Claimed</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {reviews.map((row) => (
            <TableRow key={row.review_item.review_item_id}>
              <TableCell>
                <div className="space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant={statusVariant(row.review_item.review_kind)}>
                      {titleCaseFromSnake(row.review_item.review_kind)}
                    </Badge>
                    <Badge variant="outline">{titleCaseFromSnake(row.target_kind)}</Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Created {formatDateTime(row.review_item.created_at)}
                  </p>
                </div>
              </TableCell>
              <TableCell className="font-mono text-xs">{row.conversation_id ?? 'Unknown'}</TableCell>
              <TableCell>
                <div className="space-y-1 text-sm">
                  <p>{row.current_intent_name ?? row.summary_primary_intent_name ?? 'No intent'}</p>
                  <p className="text-xs text-muted-foreground">
                    {row.outcome ? `Outcome ${titleCaseFromSnake(row.outcome)}` : 'No outcome'}
                  </p>
                </div>
              </TableCell>
              <TableCell>
                <div className="space-y-1">
                  <Badge variant={statusVariant(row.review_item.status)}>
                    {titleCaseFromSnake(row.review_item.status)}
                  </Badge>
                  {row.review_item.review_disposition ? (
                    <p className="text-xs text-muted-foreground">
                      {titleCaseFromSnake(row.review_item.review_disposition)}
                    </p>
                  ) : null}
                </div>
              </TableCell>
              <TableCell className="text-sm text-muted-foreground">
                {row.review_item.claimed_by_user_id ?? 'Unclaimed'}
              </TableCell>
              <TableCell className="text-right">
                <div className="flex justify-end gap-2">
                  {row.review_item.status !== 'resolved' ? (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => onClaimReview(row)}
                      disabled={busyKey === `claim-review-${row.review_item.review_item_id}`}
                    >
                      {busyKey === `claim-review-${row.review_item.review_item_id}` ? (
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      ) : null}
                      Claim
                    </Button>
                  ) : null}
                  <Button size="sm" onClick={() => onResolveReview(row)}>
                    Resolve
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  )
}

export function IntentTagsSummariesTab({
  summaries,
  summaryStatusFilter,
  onSummaryStatusFilterChange,
  onOpenSummary,
}: {
  summaries: SummaryListItemReadModel[]
  summaryStatusFilter: SummaryStatusFilter
  onSummaryStatusFilterChange: (value: SummaryStatusFilter) => void
  onOpenSummary: (item: SummaryListItemReadModel) => void
}) {
  return (
    <Card className="p-5">
      <div className="mb-4 flex items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Conversation summaries</h2>
          <p className="text-sm text-muted-foreground">
            Final semantic rollups with corrections, evidence, and tag assignments.
          </p>
        </div>
        <Select value={summaryStatusFilter} onValueChange={(value) => onSummaryStatusFilterChange(value as SummaryStatusFilter)}>
          <SelectTrigger className="w-[180px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SUMMARY_STATUS_OPTIONS.map((status) => (
              <SelectItem key={status} value={status}>
                {status === 'all' ? 'All summaries' : titleCaseFromSnake(status)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-3">
        {summaries.map((item) => (
          <button
            key={item.summary.conversation_summary_id}
            type="button"
            onClick={() => onOpenSummary(item)}
            className="w-full rounded-2xl border border-border/60 p-4 text-left transition-colors hover:bg-muted/30"
          >
            <div className="flex items-start justify-between gap-4">
              <div className="space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-medium">
                    {item.effective_summary.primary_intent_name ?? 'No primary intent'}
                  </p>
                  <Badge variant={statusVariant(item.effective_summary.status)}>
                    {titleCaseFromSnake(item.effective_summary.status)}
                  </Badge>
                  {item.is_corrected ? <Badge variant="warning">Corrected</Badge> : null}
                  {item.effective_summary.requires_human_followup ? (
                    <Badge variant="warning">Follow-up</Badge>
                  ) : null}
                </div>
                <p className="font-mono text-xs text-muted-foreground">
                  {item.summary.conversation_id}
                </p>
                <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                  <span>{titleCaseFromSnake(item.effective_summary.channel)}</span>
                  {item.effective_summary.outcome ? (
                    <span>{titleCaseFromSnake(item.effective_summary.outcome)}</span>
                  ) : null}
                  {item.effective_summary.resolution_status ? (
                    <span>{titleCaseFromSnake(item.effective_summary.resolution_status)}</span>
                  ) : null}
                  <span>{formatRelativeNumber(item.tag_assignments.length)} tags</span>
                </div>
              </div>
              <div className="text-right text-sm">
                <p className="font-medium">{formatDateTime(item.effective_summary.updated_at)}</p>
                <p className="text-xs text-muted-foreground">
                  {item.tag_names.slice(0, 2).join(', ') || 'No tags'}
                </p>
              </div>
            </div>
          </button>
        ))}
      </div>
    </Card>
  )
}

export function IntentTagsWebhooksTab({
  webhookTargets,
  canWrite,
  busyKey,
  dispatchMode,
  onDispatchModeChange,
  dispatchConversationId,
  onDispatchConversationIdChange,
  dispatchResult,
  onDispatchWebhooks,
  onAddTarget,
  onEditTarget,
  onDeleteTarget,
}: {
  webhookTargets: SemanticWebhookTargetReadModel[]
  canWrite: boolean
  busyKey: string | null
  dispatchMode: SemanticWebhookDispatchMode
  onDispatchModeChange: (value: SemanticWebhookDispatchMode) => void
  dispatchConversationId: string
  onDispatchConversationIdChange: (value: string) => void
  dispatchResult: SemanticWebhookDispatchResponse | null
  onDispatchWebhooks: () => void
  onAddTarget: () => void
  onEditTarget: (target: SemanticWebhookTargetReadModel) => void
  onDeleteTarget: (target: SemanticWebhookTargetReadModel) => void
}) {
  return (
    <div className="grid gap-6 xl:grid-cols-[0.9fr,1.1fr]">
      <Card className="p-5">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">Dispatch controls</h2>
            <p className="text-sm text-muted-foreground">
              Force publication fanout or delivery retries against the outbox.
            </p>
          </div>
          <Badge variant={canWrite ? 'success' : 'secondary'}>
            {canWrite ? 'Writable' : 'Read only'}
          </Badge>
        </div>
        <div className="space-y-4">
          <FieldShell label="Mode">
            <Select value={dispatchMode} onValueChange={(value) => onDispatchModeChange(value as SemanticWebhookDispatchMode)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="both">Fanout and deliver</SelectItem>
                <SelectItem value="fanout">Fanout only</SelectItem>
                <SelectItem value="deliver">Delivery only</SelectItem>
              </SelectContent>
            </Select>
          </FieldShell>

          <FieldShell
            label="Optional conversation id"
            description="Leave blank to process the whole organization outbox scope."
          >
            <Input
              value={dispatchConversationId}
              onChange={(event) => onDispatchConversationIdChange(event.target.value)}
              placeholder="web_widget:conversation-id"
            />
          </FieldShell>

          <Button
            className="w-full"
            disabled={!canWrite || busyKey === 'dispatch-webhooks'}
            onClick={onDispatchWebhooks}
          >
            {busyKey === 'dispatch-webhooks' ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : null}
            Run dispatch
          </Button>

          {dispatchResult ? (
            <div className="rounded-2xl border border-border/60 bg-muted/25 p-4 text-sm">
              <div className="grid gap-2 sm:grid-cols-2">
                <span>Fanned out: {formatRelativeNumber(dispatchResult.publication_fanned_out)}</span>
                <span>Delivered: {formatRelativeNumber(dispatchResult.delivery_delivered)}</span>
                <span>Retried: {formatRelativeNumber(dispatchResult.delivery_retried)}</span>
                <span>Failed: {formatRelativeNumber(dispatchResult.delivery_failed)}</span>
              </div>
            </div>
          ) : null}
        </div>
      </Card>

      <Card className="p-5">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">Webhook targets</h2>
            <p className="text-sm text-muted-foreground">
              `semantic_summary.finalized` subscribers with delivery state and secret source.
            </p>
          </div>
          {canWrite ? (
            <Button size="sm" onClick={onAddTarget}>
              Add target
            </Button>
          ) : null}
        </div>
        <div className="space-y-3">
          {webhookTargets.map((target) => (
            <div key={target.webhook_target_id} className="rounded-2xl border border-border/60 p-4">
              <div className="flex items-start justify-between gap-4">
                <div className="space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-medium">{target.name}</p>
                    <Badge variant={target.is_active ? 'success' : 'secondary'}>
                      {target.is_active ? 'Active' : 'Inactive'}
                    </Badge>
                    <Badge variant="outline">{titleCaseFromSnake(target.signing_secret_source)}</Badge>
                  </div>
                  <p className="text-sm text-muted-foreground">{target.url}</p>
                  <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                    <span>{target.agent_ids.length ? target.agent_ids.join(', ') : 'All agents'}</span>
                    <span>{target.channels.length ? target.channels.join(', ') : 'All channels'}</span>
                    <span>{target.max_retries} retries</span>
                    <span>{target.timeout_seconds}s timeout</span>
                  </div>
                  <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                    <span>Last success {formatDateTime(target.last_success_at)}</span>
                    {target.last_error ? <span>Error: {target.last_error}</span> : null}
                  </div>
                </div>
                {canWrite ? (
                  <div className="flex flex-wrap gap-2">
                    <Button variant="outline" size="sm" onClick={() => onEditTarget(target)}>
                      Edit
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => onDeleteTarget(target)}
                      disabled={busyKey === `delete-webhook-${target.webhook_target_id}`}
                    >
                      {busyKey === `delete-webhook-${target.webhook_target_id}` ? (
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      ) : null}
                      Delete
                    </Button>
                  </div>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}
