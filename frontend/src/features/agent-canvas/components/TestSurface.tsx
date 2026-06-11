/**
 * TestSurface — canvas-level Test view (Phase 1C / Sierra Option C).
 *
 * Mounted as the third toggle next to Document and Graph. Two-pane layout:
 * chat (left, ~480px) over `<UnifiedTestInterface />`, reasoning timeline
 * (right, fills) over `<ReasoningTimelinePane />`. Both panes share the
 * same `conversationId`, lifted out of UnifiedTestInterface via its
 * onConversationIdChange callback.
 *
 * Why this surface exists alongside the existing Test modal:
 *   - The modal is phone-shaped (384×600), good for "does this work" sanity
 *     checks. Reasoning timeline is cramped inside it (a debug-panel
 *     section at the bottom of an already-narrow column).
 *   - This surface gives reasoning the room to be a primary view — the
 *     "what did my agent do, and why" answer is the headline content,
 *     not a debug aid.
 *
 * Reads live AgentDocument state from <AgentDocumentProvider /> upstream.
 * Phase 2 was the prerequisite for this: without it, switching here
 * mid-edit would test the LAST-SAVED agent definition, not the in-progress
 * unsaved edits — bad UX. With Phase 2 the provider holds the live
 * unsaved state and the test session uses it.
 */
import { useState } from 'react'

import { ReasoningTimelinePane } from '@/features/agent-canvas/components/ReasoningTimelinePane'
import { UnifiedTestInterface } from '@/features/agent-canvas/components/UnifiedTestInterface'
import type { AgentSettings } from '@/types/agent-definition'

interface TestSurfaceProps {
  agentId: string
  agentName: string
  agentType: AgentSettings['agent_type']
  agentStatus: 'active' | 'draft'
}

export function TestSurface({
  agentId,
  agentName,
  agentType,
  agentStatus,
}: TestSurfaceProps) {
  const [conversationId, setConversationId] = useState<string | null>(null)

  return (
    <div className="flex h-full">
      {/* Chat (~480px) */}
      <div className="flex w-[480px] shrink-0 flex-col overflow-hidden border-r border-white/10">
        <UnifiedTestInterface
          agentId={agentId}
          agentName={agentName}
          agentType={agentType}
          agentStatus={agentStatus}
          onConversationIdChange={setConversationId}
        />
      </div>

      {/* Reasoning timeline (fills the remaining width) */}
      <div className="min-w-0 flex-1">
        <ReasoningTimelinePane conversationId={conversationId} />
      </div>
    </div>
  )
}
