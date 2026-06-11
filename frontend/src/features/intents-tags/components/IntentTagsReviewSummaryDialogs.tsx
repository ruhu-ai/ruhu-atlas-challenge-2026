import type { Dispatch, SetStateAction } from 'react'
import { Loader2 } from 'lucide-react'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Card } from '@/components/atoms/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/atoms/dialog'
import { Input } from '@/components/atoms/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
import { Separator } from '@/components/atoms/separator'
import { Textarea } from '@/components/atoms/textarea'
import type {
  ConversationSummaryDetailReadModel,
  ReviewDisposition,
  ReviewQueueRowReadModel,
  SummaryListItemReadModel,
} from '@/types/intent-tags'
import {
  formatDateTime,
  formatPercent,
  formatRelativeNumber,
  statusVariant,
  titleCaseFromSnake,
} from '../utils/intent-tags-helpers'
import type { ReviewResolutionState } from '../utils/intent-tags-form-state'
import { FieldShell } from './IntentTagsPrimitives'

export function IntentTagsReviewResolutionDialog({
  open,
  onOpenChange,
  onCancel,
  selectedReview,
  reviewResolution,
  setReviewResolution,
  busyKey,
  onSubmit,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCancel: () => void
  selectedReview: ReviewQueueRowReadModel | null
  reviewResolution: ReviewResolutionState
  setReviewResolution: Dispatch<SetStateAction<ReviewResolutionState>>
  busyKey: string | null
  onSubmit: () => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>Resolve review</DialogTitle>
          <DialogDescription>
            Apply a final disposition and optional correction payload for the selected review item.
          </DialogDescription>
        </DialogHeader>
        {selectedReview ? (
          <div className="space-y-4">
            <div className="rounded-2xl border border-border/60 bg-muted/25 p-4 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={statusVariant(selectedReview.review_item.review_kind)}>
                  {titleCaseFromSnake(selectedReview.review_item.review_kind)}
                </Badge>
                <Badge variant="outline">{titleCaseFromSnake(selectedReview.target_kind)}</Badge>
                <span className="text-muted-foreground">
                  {selectedReview.conversation_id ?? 'Unknown conversation'}
                </span>
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <FieldShell label="Disposition">
                <Select
                  value={reviewResolution.disposition}
                  onValueChange={(value) =>
                    setReviewResolution((current) => ({
                      ...current,
                      disposition: value as ReviewDisposition,
                    }))
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="confirmed">Confirmed</SelectItem>
                    <SelectItem value="corrected">Corrected</SelectItem>
                    <SelectItem value="dismissed">Dismissed</SelectItem>
                    <SelectItem value="needs_followup">Needs follow-up</SelectItem>
                  </SelectContent>
                </Select>
              </FieldShell>
              <FieldShell label="Review notes">
                <Input
                  value={reviewResolution.review_notes}
                  onChange={(event) =>
                    setReviewResolution((current) => ({
                      ...current,
                      review_notes: event.target.value,
                    }))
                  }
                  placeholder="Optional operator notes"
                />
              </FieldShell>
            </div>

            {selectedReview.target_kind === 'turn' && reviewResolution.disposition === 'corrected' ? (
              <FieldShell
                label="Corrected decision JSON"
                description="Provide the full corrected turn classification payload."
              >
                <Textarea
                  value={reviewResolution.corrected_decision_json}
                  onChange={(event) =>
                    setReviewResolution((current) => ({
                      ...current,
                      corrected_decision_json: event.target.value,
                    }))
                  }
                  className="min-h-[220px] font-mono text-xs"
                />
              </FieldShell>
            ) : null}

            {selectedReview.target_kind === 'summary' && reviewResolution.disposition === 'corrected' ? (
              <div className="space-y-4">
                <FieldShell label="Corrected summary fields JSON">
                  <Textarea
                    value={reviewResolution.corrected_fields_json}
                    onChange={(event) =>
                      setReviewResolution((current) => ({
                        ...current,
                        corrected_fields_json: event.target.value,
                      }))
                    }
                    className="min-h-[200px] font-mono text-xs"
                  />
                </FieldShell>
                <FieldShell label="Corrected tag definition ids" description="Comma-separated ids.">
                  <Input
                    value={reviewResolution.corrected_tag_definition_ids}
                    onChange={(event) =>
                      setReviewResolution((current) => ({
                        ...current,
                        corrected_tag_definition_ids: event.target.value,
                      }))
                    }
                    placeholder="tag-id-1, tag-id-2"
                  />
                </FieldShell>
              </div>
            ) : null}
          </div>
        ) : null}
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            Cancel
          </Button>
          <Button
            onClick={onSubmit}
            disabled={!selectedReview || busyKey === `resolve-review-${selectedReview?.review_item.review_item_id}`}
          >
            {selectedReview && busyKey === `resolve-review-${selectedReview.review_item.review_item_id}` ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : null}
            Resolve review
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function IntentTagsSummaryDetailDialog({
  open,
  onOpenChange,
  selectedSummary,
  summaryDetail,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedSummary: SummaryListItemReadModel | null
  summaryDetail: ConversationSummaryDetailReadModel | null
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] max-w-5xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Summary detail</DialogTitle>
          <DialogDescription>
            Effective summary payload, correction status, and turn-level evidence.
          </DialogDescription>
        </DialogHeader>
        {!summaryDetail || !selectedSummary ? (
          <div className="flex items-center justify-center gap-3 py-12 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
            <span>Loading summary detail…</span>
          </div>
        ) : (
          <div className="space-y-6">
            <div className="grid gap-4 md:grid-cols-3">
              <Card className="p-4">
                <p className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Primary intent</p>
                <p className="mt-2 text-xl font-semibold">
                  {summaryDetail.effective_summary.effective_summary.primary_intent_name ?? 'Unknown'}
                </p>
              </Card>
              <Card className="p-4">
                <p className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Outcome</p>
                <p className="mt-2 text-xl font-semibold">
                  {titleCaseFromSnake(summaryDetail.effective_summary.effective_summary.outcome)}
                </p>
              </Card>
              <Card className="p-4">
                <p className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Resolution</p>
                <p className="mt-2 text-xl font-semibold">
                  {titleCaseFromSnake(summaryDetail.effective_summary.effective_summary.resolution_status)}
                </p>
              </Card>
            </div>

            <Card className="p-5">
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <Badge variant={statusVariant(summaryDetail.effective_summary.effective_summary.status)}>
                  {titleCaseFromSnake(summaryDetail.effective_summary.effective_summary.status)}
                </Badge>
                {summaryDetail.effective_summary.is_corrected ? (
                  <Badge variant="warning">Corrected</Badge>
                ) : null}
                {summaryDetail.effective_summary.review_item ? (
                  <Badge variant="outline">
                    Review {titleCaseFromSnake(summaryDetail.effective_summary.review_item.status)}
                  </Badge>
                ) : null}
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <p className="text-sm font-medium">Context</p>
                  <div className="rounded-2xl border border-border/60 bg-muted/25 p-4 text-sm">
                    <div className="space-y-1 text-muted-foreground">
                      <p>Conversation {summaryDetail.effective_summary.effective_summary.conversation_id}</p>
                      <p>Channel {titleCaseFromSnake(summaryDetail.effective_summary.effective_summary.channel)}</p>
                      <p>Updated {formatDateTime(summaryDetail.effective_summary.effective_summary.updated_at)}</p>
                      {summaryDetail.conversation_context?.agent_id ? (
                        <p>Agent {summaryDetail.conversation_context.agent_id}</p>
                      ) : null}
                    </div>
                  </div>
                </div>
                <div className="space-y-2">
                  <p className="text-sm font-medium">Assigned tags</p>
                  <div className="flex flex-wrap gap-2 rounded-2xl border border-border/60 bg-muted/25 p-4">
                    {selectedSummary.tag_names.length ? (
                      selectedSummary.tag_names.map((tagName) => (
                        <Badge key={tagName} variant="outline">
                          {tagName}
                        </Badge>
                      ))
                    ) : (
                      <p className="text-sm text-muted-foreground">No summary tags assigned.</p>
                    )}
                  </div>
                </div>
              </div>
              <Separator className="my-4" />
              <div className="grid gap-4 xl:grid-cols-2">
                <div className="space-y-2">
                  <p className="text-sm font-medium">Summary payload</p>
                  <pre className="overflow-x-auto rounded-2xl border border-border/60 bg-muted/25 p-4 text-xs">
                    {JSON.stringify(summaryDetail.effective_summary.effective_summary.summary_payload, null, 2)}
                  </pre>
                </div>
                <div className="space-y-2">
                  <p className="text-sm font-medium">Evidence payload</p>
                  <pre className="overflow-x-auto rounded-2xl border border-border/60 bg-muted/25 p-4 text-xs">
                    {JSON.stringify(summaryDetail.effective_summary.effective_summary.evidence_payload, null, 2)}
                  </pre>
                </div>
              </div>
            </Card>

            <Card className="p-5">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h3 className="font-semibold tracking-tight">Turn evidence</h3>
                  <p className="text-sm text-muted-foreground">
                    Effective turn classifications included in the summary rollup.
                  </p>
                </div>
                <Badge variant="outline">{formatRelativeNumber(summaryDetail.turn_evidence.length)}</Badge>
              </div>
              <div className="space-y-3">
                {summaryDetail.turn_evidence.map((evidence) => (
                  <div key={evidence.event.classification_event_id} className="rounded-2xl border border-border/60 p-4">
                    <div className="flex items-start justify-between gap-4">
                      <div className="space-y-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="font-medium">{evidence.effective_event.intent_name}</p>
                          <Badge variant="outline">
                            {formatPercent(evidence.effective_event.confidence)}
                          </Badge>
                          {evidence.is_corrected ? <Badge variant="warning">Corrected</Badge> : null}
                        </div>
                        <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                          <span>{evidence.effective_event.channel}</span>
                          <span>{evidence.effective_event.adapter_name}</span>
                          <span>{formatDateTime(evidence.effective_event.created_at)}</span>
                        </div>
                        {evidence.tag_assignments.length ? (
                          <div className="mt-2 flex flex-wrap gap-2">
                            {evidence.tag_assignments.map((assignment) => (
                              <Badge key={assignment.tag_assignment_id} variant="outline">
                                {assignment.tag_definition_id}
                              </Badge>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
