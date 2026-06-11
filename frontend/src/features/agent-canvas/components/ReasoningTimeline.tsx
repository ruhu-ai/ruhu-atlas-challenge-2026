/**
 * ReasoningTimeline — Sierra-style per-turn evidence trail.
 *
 * Renders a vertically stacked timeline of what the kernel did each turn:
 * step transition, guards passed/failed, action chosen + reason, tools
 * called with status/latency, response rendered. The data is the same
 * `ConversationTrace` rows the backend now exposes from
 * `GET /conversations/{id}/traces` (see `ConversationTraceResponse`).
 *
 * Designed to be reusable across THREE homes (per the Phase 1B plan):
 *   1. Inside the existing InteractionDebugPanel (debug surface during
 *      Test mode) — present.
 *   2. The future canvas-level Test surface (Option C — third toggle next
 *      to Document/Graph) — same component, larger frame.
 *   3. The conversation detail page (`pages/calls.tsx`) for postmortem.
 *
 * Component contract:
 *   - Pure presentation. The host fetches traces via
 *     `voiceSessionService.getConversationTraces(conversationId)` and
 *     passes them in. No service calls or polling here.
 *   - Renders per-turn groups in chronological order.
 *   - Empty state ("No reasoning yet") when traces are empty.
 *   - Loading/error states are the host's job.
 */
import { memo, useMemo } from 'react'
import {
  ArrowRight,
  Check,
  Clock,
  MessageSquare,
  Sparkles,
  Wrench,
  X,
} from 'lucide-react'

import { cn } from '@/lib/utils'
import type { ConversationTrace } from '@/api/services/voice-session.service'

interface ReasoningTimelineProps {
  /** Traces in chronological order (oldest first). The component does not
   * re-sort — pass them already-ordered. */
  traces: ConversationTrace[]
  /** Optional className for the outer scroll container. */
  className?: string
  /** Optional empty-state message override. */
  emptyMessage?: string
}

export const ReasoningTimeline = memo(function ReasoningTimeline({
  traces,
  className,
  emptyMessage = 'No reasoning recorded yet — run a turn to see what the agent decided.',
}: ReasoningTimelineProps) {
  if (traces.length === 0) {
    return (
      <div className={cn('flex h-full items-center justify-center p-6', className)}>
        <p className="max-w-md text-center text-xs text-muted-foreground">
          {emptyMessage}
        </p>
      </div>
    )
  }

  return (
    <ol className={cn('space-y-3 px-3 py-2', className)}>
      {traces.map((trace, index) => (
        <TurnGroup key={trace.trace_id} trace={trace} turnIndex={index + 1} />
      ))}
    </ol>
  )
})

// ───────────────────────────────────────────────────────────────────────
// Per-turn group: header + ordered rows
// ───────────────────────────────────────────────────────────────────────

interface TurnGroupProps {
  trace: ConversationTrace
  turnIndex: number
}

function TurnGroup({ trace, turnIndex }: TurnGroupProps) {
  const totalLatency = useMemo(() => {
    const entries = Object.values(trace.latency_breakdown_ms ?? {})
    return entries.reduce((sum, ms) => sum + (Number.isFinite(ms) ? ms : 0), 0)
  }, [trace.latency_breakdown_ms])

  return (
    <li className="rounded-lg border border-border bg-card/40 p-2.5">
      <header className="mb-1.5 flex items-center justify-between gap-2">
        <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          Turn {turnIndex}
        </span>
        <time
          dateTime={trace.recorded_at}
          className="text-[10px] tabular-nums text-muted-foreground"
        >
          {formatTime(trace.recorded_at)}
          {totalLatency > 0 ? ` · ${totalLatency}ms` : ''}
        </time>
      </header>

      <ul className="space-y-1">
        <StepTransitionRow trace={trace} />
        {(trace.guard_results ?? []).map((guard, i) => (
          <GuardRow key={`${trace.trace_id}-guard-${i}`} guard={guard} />
        ))}
        {trace.chosen_action ? <ActionRow action={trace.chosen_action} /> : null}
        {(trace.tool_calls ?? []).map((call, i) => (
          <ToolRow
            key={`${trace.trace_id}-tool-${i}`}
            call={call}
            latencyMs={trace.latency_breakdown_ms?.[`tool:${call.tool_ref}`]}
          />
        ))}
        {(trace.emitted_messages ?? []).map((msg, i) => (
          <MessageRow
            key={`${trace.trace_id}-msg-${i}`}
            role={msg.role}
            text={msg.text}
          />
        ))}
      </ul>
    </li>
  )
}

// ───────────────────────────────────────────────────────────────────────
// Row primitives
// ───────────────────────────────────────────────────────────────────────

interface RowProps {
  icon: React.ReactNode
  label: React.ReactNode
  detail?: React.ReactNode
  tone?: 'success' | 'fail' | 'neutral'
}

function Row({ icon, label, detail, tone = 'neutral' }: RowProps) {
  return (
    <li className="flex items-start gap-1.5 text-[11px] leading-snug">
      <span
        className={cn(
          'mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full',
          tone === 'success' && 'bg-emerald-500/15 text-emerald-500',
          tone === 'fail' && 'bg-rose-500/15 text-rose-500',
          tone === 'neutral' && 'bg-muted text-muted-foreground',
        )}
      >
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <span className="text-foreground">{label}</span>
        {detail ? (
          <span className="ml-1.5 text-muted-foreground">{detail}</span>
        ) : null}
      </div>
    </li>
  )
}

function StepTransitionRow({ trace }: { trace: ConversationTrace }) {
  if (trace.step_before === trace.step_after) {
    return (
      <Row
        tone="neutral"
        icon={<ArrowRight className="h-2.5 w-2.5" />}
        label="Stayed at"
        detail={<code className="font-mono">{trace.step_after}</code>}
      />
    )
  }
  return (
    <Row
      tone="success"
      icon={<ArrowRight className="h-2.5 w-2.5" />}
      label="Transitioned"
      detail={
        <>
          <code className="font-mono">{trace.step_before}</code>
          {' → '}
          <code className="font-mono">{trace.step_after}</code>
        </>
      }
    />
  )
}

function GuardRow({
  guard,
}: {
  guard: NonNullable<ConversationTrace['guard_results']>[number]
}) {
  const labelText = `${guard.guard_kind}${guard.guard_value ? `:${guard.guard_value}` : ''}`
  return (
    <Row
      tone={guard.passed ? 'success' : 'fail'}
      icon={
        guard.passed ? <Check className="h-2.5 w-2.5" /> : <X className="h-2.5 w-2.5" />
      }
      label={guard.passed ? 'Guard passed' : 'Guard failed'}
      detail={
        <>
          <code className="font-mono">{labelText}</code>
          {guard.reason ? <span className="ml-1">— {guard.reason}</span> : null}
        </>
      }
    />
  )
}

function ActionRow({
  action,
}: {
  action: NonNullable<ConversationTrace['chosen_action']>
}) {
  return (
    <Row
      tone="success"
      icon={<Sparkles className="h-2.5 w-2.5" />}
      label={`Chose action: ${action.type}`}
      detail={action.reason ? <>— {action.reason}</> : null}
    />
  )
}

function ToolRow({
  call,
  latencyMs,
}: {
  call: NonNullable<ConversationTrace['tool_calls']>[number]
  latencyMs?: number
}) {
  const succeeded = call.status === 'success'
  const failed =
    call.status === 'blocked' || call.status === 'failed' || call.status === 'error'
  return (
    <Row
      tone={succeeded ? 'success' : failed ? 'fail' : 'neutral'}
      icon={<Wrench className="h-2.5 w-2.5" />}
      label="Tool"
      detail={
        <>
          <code className="font-mono">{call.tool_ref}</code>
          <span className="ml-1.5">{call.status}</span>
          {latencyMs != null ? (
            <span className="ml-1.5 inline-flex items-center gap-0.5 text-muted-foreground">
              <Clock className="h-2.5 w-2.5" />
              {latencyMs}ms
            </span>
          ) : null}
          {call.reason ? <span className="ml-1.5">— {call.reason}</span> : null}
        </>
      }
    />
  )
}

function MessageRow({
  role,
  text,
}: {
  role: 'assistant' | 'system'
  text: string
}) {
  const trimmed = text.trim()
  if (!trimmed) return null
  return (
    <Row
      tone="neutral"
      icon={<MessageSquare className="h-2.5 w-2.5" />}
      label={role === 'assistant' ? 'Replied' : 'System'}
      detail={
        <span className="line-clamp-2 italic text-muted-foreground">"{trimmed}"</span>
      }
    />
  )
}

// ───────────────────────────────────────────────────────────────────────
// Helpers
// ───────────────────────────────────────────────────────────────────────

function formatTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
