/**
 * ReasoningTimelinePane — scrollable Reasoning Timeline panel for the
 * canvas-level Test surface.
 *
 * Polls `GET /conversations/{id}/traces` while a conversation is active,
 * renders the existing presentational `<ReasoningTimeline />` underneath
 * a stable header. When no conversation is active (the user hasn't sent
 * a message yet), the component renders its own "start a conversation"
 * empty state rather than the bare timeline empty state — context matters
 * for first-impression UX.
 *
 * Pure on the host's side: takes a conversationId and nothing else. The
 * Test surface owns the conversationId state (lifted out of
 * UnifiedTestInterface via the onConversationIdChange callback).
 */
import { Activity, Loader2, MessageSquareWarning } from 'lucide-react'

import { cn } from '@/lib/utils'
import { ReasoningTimeline } from '@/features/agent-canvas/components/ReasoningTimeline'
import { useConversationTraces } from '@/features/agent-canvas/hooks/useConversationTraces'

interface ReasoningTimelinePaneProps {
  /** Active conversation id from the adjacent chat surface. Null when
   * no conversation has been started yet. */
  conversationId: string | null
  className?: string
}

export function ReasoningTimelinePane({
  conversationId,
  className,
}: ReasoningTimelinePaneProps) {
  const { traces, isLoading, error } = useConversationTraces(conversationId)

  return (
    <div className={cn('flex h-full flex-col', className)}>
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-white/10 bg-card/40 px-4 py-2">
        <div className="flex items-center gap-2">
          <Activity className="h-3.5 w-3.5 text-muted-foreground" />
          <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Reasoning
          </h3>
          {traces.length > 0 && (
            <span className="text-[10px] tabular-nums text-muted-foreground">
              · {traces.length} turn{traces.length === 1 ? '' : 's'}
            </span>
          )}
        </div>
        {isLoading && conversationId ? (
          <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" aria-label="refreshing" />
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {error ? (
          <div className="flex h-full items-center justify-center p-6">
            <div className="flex max-w-sm items-start gap-2 rounded-md border border-rose-400/40 bg-rose-500/10 p-3 text-xs text-rose-300">
              <MessageSquareWarning className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>Couldn't load reasoning: {error}</span>
            </div>
          </div>
        ) : !conversationId ? (
          <div className="flex h-full items-center justify-center p-6">
            <p className="max-w-sm text-center text-xs text-muted-foreground">
              Send your agent a message in the chat to see the per-turn reasoning trail —
              step transitions, guards, tool calls, and replies.
            </p>
          </div>
        ) : (
          <ReasoningTimeline traces={traces} />
        )}
      </div>
    </div>
  )
}
