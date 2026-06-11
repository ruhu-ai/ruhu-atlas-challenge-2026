/**
 * Card subcomponents rendered below Atlas assistant messages — blocking
 * questions, blockers, dependencies, attachment/API-discovery results,
 * proposed delta changes, and the apply-status banner. Extracted from
 * AtlasAIPanel.tsx (RP-4.4); purely presentational.
 */

import { useState, type FormEvent } from 'react'
import { Check, Loader2 } from 'lucide-react'

import { Button } from '@/components/atoms/button'
import { cn } from '@/lib/utils'

import type {
  AtlasAPIDiscoveryResult,
  AtlasAttachmentIngestionResult,
  AtlasBlocker,
  AtlasDependency,
  AtlasTurnResponse,
  BlockingQuestion,
  CanonicalAtlasDelta,
  CanonicalAtlasProposedChanges,
} from '@/api/services/atlas.service'

import { allDeltas, titleCase, renderDeltaLabel } from './atlas-shared'

// =============================================================================
// Blocking questions card
// =============================================================================

export function BlockingQuestionsCard(props: {
  questions: BlockingQuestion[]
  onSubmit: (answers: Record<string, string>) => void
}) {
  const { questions, onSubmit } = props
  const [answers, setAnswers] = useState<Record<string, string>>({})

  const allRequiredAnswered = questions
    .filter((q) => q.required)
    .every((q) => Boolean(answers[q.question_id]?.trim()))

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!allRequiredAnswered) return
    onSubmit(answers)
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="space-y-3 rounded-md border border-border/70 bg-muted/20 px-3 py-3"
    >
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Atlas needs a few answers
      </p>
      {questions.map((q) => (
        <div key={q.question_id} className="space-y-1.5">
          <label className="text-sm text-foreground block">
            {q.question}
            {q.required && <span className="text-red-500 ml-0.5">*</span>}
          </label>
          {q.help_text && (
            <p className="text-xs text-muted-foreground">{q.help_text}</p>
          )}
          {q.options && q.options.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {q.options.map((option) => {
                const isSelected = answers[q.question_id] === option
                return (
                  <button
                    type="button"
                    key={option}
                    onClick={() =>
                      setAnswers((prev) => ({ ...prev, [q.question_id]: option }))
                    }
                    className={cn(
                      'rounded-full border px-3 py-1 text-xs transition-colors',
                      isSelected
                        ? 'border-primary bg-primary text-primary-foreground'
                        : 'border-border bg-background hover:bg-muted',
                    )}
                  >
                    {option}
                  </button>
                )
              })}
            </div>
          ) : (
            <input
              type="text"
              value={answers[q.question_id] ?? ''}
              onChange={(e) =>
                setAnswers((prev) => ({ ...prev, [q.question_id]: e.target.value }))
              }
              className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              placeholder="Type your answer..."
            />
          )}
        </div>
      ))}
      <Button type="submit" disabled={!allRequiredAnswered} className="w-full">
        Submit answers
      </Button>
    </form>
  )
}

// =============================================================================
// Blockers card
// =============================================================================

export function BlockersCard(props: { blockers: AtlasBlocker[] }) {
  return (
    <div className="space-y-2 rounded-md border border-red-200 bg-red-50 dark:border-red-900/30 dark:bg-red-900/10 px-3 py-3">
      <p className="text-xs font-medium uppercase tracking-wide text-red-700 dark:text-red-400">
        Blocked
      </p>
      <ul className="space-y-1.5">
        {props.blockers.map((blocker) => (
          <li key={blocker.code} className="text-sm text-red-900 dark:text-red-200">
            <span className="font-mono text-xs rounded bg-red-100 dark:bg-red-900/40 px-1.5 py-0.5 mr-1.5">
              {blocker.code}
            </span>
            {blocker.message}
          </li>
        ))}
      </ul>
    </div>
  )
}

// =============================================================================
// Dependencies card
// =============================================================================

export function DependenciesCard(props: { dependencies: AtlasDependency[] }) {
  if (props.dependencies.length === 0) return null
  return (
    <div className="space-y-2 rounded-md border border-border/70 bg-muted/20 px-3 py-3">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Dependencies
      </p>
      <ul className="space-y-1.5">
        {props.dependencies.map((dep) => (
          <li
            key={dep.key}
            className={cn(
              'rounded-md border bg-background px-2 py-1.5 text-sm',
              dep.blocking && 'border-amber-300 bg-amber-50 dark:bg-amber-900/10',
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium">{dep.display_name}</span>
              <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
                {titleCase(dep.kind)} · {titleCase(dep.status)}
              </span>
            </div>
            {dep.reason && <p className="text-xs text-muted-foreground mt-0.5">{dep.reason}</p>}
            {dep.suggested_action && (
              <p className="text-xs text-amber-700 dark:text-amber-400 mt-0.5">
                {dep.suggested_action}
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

export function APIDiscoveryResultsCard(props: { results: AtlasAPIDiscoveryResult[] }) {
  if (props.results.length === 0) return null
  return (
    <div className="space-y-2 rounded-md border border-border/70 bg-muted/20 px-3 py-3">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        API Discovery
      </p>
      {props.results.map((result) => (
        <div key={result.request_id} className="rounded-md border border-border/70 bg-background px-2 py-1.5 text-sm">
          <div className="flex items-center justify-between gap-2">
            <span className="font-medium">{result.provider_name || result.base_url || result.request_id}</span>
            <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
              {titleCase(result.status)} · {titleCase(result.spec_type)}
            </span>
          </div>
          {result.notes && <p className="mt-1 text-xs text-muted-foreground">{result.notes}</p>}
          {result.candidate_endpoints.length > 0 && (
            <p className="mt-1 text-xs text-muted-foreground">
              {result.candidate_endpoints.length} endpoint{result.candidate_endpoints.length === 1 ? '' : 's'} discovered
            </p>
          )}
        </div>
      ))}
    </div>
  )
}

export function AttachmentResultsCard(props: { results: AtlasAttachmentIngestionResult[] }) {
  if (props.results.length === 0) return null
  return (
    <div className="space-y-2 rounded-md border border-border/70 bg-muted/20 px-3 py-3">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Attachments
      </p>
      {props.results.map((result) => (
        <div key={result.attachment_id} className="rounded-md border border-border/70 bg-background px-2 py-1.5 text-sm">
          <div className="flex items-center justify-between gap-2">
            <span className="font-medium">{result.display_name}</span>
            <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
              {titleCase(result.kind)}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {result.extracted_characters.toLocaleString()} chars · {titleCase(result.suggested_interpretation)}
          </p>
        </div>
      ))}
    </div>
  )
}

// =============================================================================
// Proposed changes (deltas) card
// =============================================================================

const DELTA_FAMILY_LABELS: Array<[keyof CanonicalAtlasProposedChanges, string]> = [
  ['agent_metadata_deltas', 'Agent metadata'],
  ['scenario_deltas', 'Scenarios'],
  ['step_deltas', 'Steps'],
  ['scenario_route_deltas', 'Routes'],
  ['channel_policy_deltas', 'Channel policies'],
  ['rule_deltas', 'Rules'],
  ['knowledge_deltas', 'Knowledge'],
  ['integration_binding_deltas', 'Integrations'],
]

export function ProposedChangesCard(props: {
  response: AtlasTurnResponse
  isLatestTurn: boolean
  isActing: boolean
  onApproveChanges: () => void
  onRejectChanges: () => void
  onRequestApply: () => void
}) {
  const { response, isLatestTurn, isActing, onApproveChanges, onRejectChanges, onRequestApply } = props
  const total = allDeltas(response.proposed_changes).length
  if (total === 0) return null
  const approved = new Set(response.review_state.approved_delta_ids ?? [])
  const rejected = new Set(response.review_state.rejected_delta_ids ?? [])
  const pending = allDeltas(response.proposed_changes).filter(
    (delta) => !approved.has(delta.delta_id) && !rejected.has(delta.delta_id) && delta.status !== 'applied',
  )
  const approvedCount = approved.size
  const rejectedCount = rejected.size
  const pendingPermission = response.pending_permission_requests.length > 0
  const canRequestApply = approvedCount > 0 && pending.length === 0 && rejectedCount === 0 && !pendingPermission

  return (
    <div className="space-y-2 rounded-md border border-border/70 bg-muted/20 px-3 py-3">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Proposed changes ({total})
      </p>
      {DELTA_FAMILY_LABELS.map(([key, label]) => {
        const deltas = response.proposed_changes[key]
        if (deltas.length === 0) return null
        return (
          <div key={key} className="space-y-1">
            <p className="text-[11px] text-muted-foreground">{label} ({deltas.length})</p>
            <ul className="space-y-1">
              {deltas.map((delta) => (
                <DeltaRow key={delta.delta_id} delta={delta} />
              ))}
            </ul>
          </div>
          )
      })}
      <div className="flex flex-wrap gap-2 text-[11px] text-muted-foreground">
        <span>{pending.length} pending review</span>
        <span>{approvedCount} approved</span>
        <span>{rejectedCount} rejected</span>
      </div>
      {isLatestTurn && (
        <div className="flex flex-wrap gap-2 pt-1">
          {pending.length > 0 && (
            <>
              <Button onClick={onApproveChanges} disabled={isActing} size="sm">
                {isActing ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> : <Check className="mr-2 h-3.5 w-3.5" />}
                Approve changes
              </Button>
              <Button onClick={onRejectChanges} disabled={isActing} size="sm" variant="outline">
                Reject changes
              </Button>
            </>
          )}
          {canRequestApply && (
            <Button onClick={onRequestApply} disabled={isActing} size="sm">
              {isActing ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> : null}
              Request apply permission
            </Button>
          )}
          {pendingPermission && (
            <span className="rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-xs text-amber-900 dark:border-amber-900/30 dark:bg-amber-900/10 dark:text-amber-200">
              Apply permission requested
            </span>
          )}
        </div>
      )}
    </div>
  )
}

function DeltaRow({ delta }: { delta: CanonicalAtlasDelta }) {
  return (
    <li className="flex items-start gap-2 rounded-md bg-background/70 px-2 py-1.5">
      <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {delta.operation}
      </span>
      <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
        {renderDeltaLabel(delta)}
      </span>
      <p className="min-w-0 flex-1 text-xs text-foreground">
        {delta.summary || 'No summary provided'}
      </p>
    </li>
  )
}

// =============================================================================
// Apply status banner
// =============================================================================

export function ApplyResultBanner(props: { status: 'applied' | 'pending' | 'rejected' | 'failed'; error?: string | null }) {
  if (props.status === 'applied') {
    return (
      <div className="rounded-md border border-green-200 bg-green-50 dark:border-green-900/30 dark:bg-green-900/10 px-3 py-2 text-sm text-green-800 dark:text-green-200">
        Changes applied to the agent draft.
      </div>
    )
  }
  return (
    <div className="rounded-md border border-amber-200 bg-amber-50 dark:border-amber-900/30 dark:bg-amber-900/10 px-3 py-2 text-sm text-amber-900 dark:text-amber-200">
      Apply {props.status}{props.error ? `: ${props.error}` : '.'}
    </div>
  )
}
