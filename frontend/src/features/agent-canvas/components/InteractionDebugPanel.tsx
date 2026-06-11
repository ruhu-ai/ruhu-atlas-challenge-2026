/**
 * Simulator debug panel for human-like interaction state (Spec 25 §Simulator Requirements).
 *
 * Renders a compact view of the runtime control state so authors can verify
 * that their agent definition produces the expected grounding, pending actions,
 * permission waits, commitment, repair, and status trail without having to
 * read final text alone.
 *
 * Polls `GET /conversations/{id}` while open.  Stops polling when collapsed.
 */
import { useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronUp, Clock } from 'lucide-react'
import { cn } from '@/lib/utils'
import { apiClient } from '@/api/client'
import type { ConversationTrace } from '@/api/services/voice-session.service'
import { ReasoningTimeline } from '@/features/agent-canvas/components/ReasoningTimeline'
import type {
  InterruptibilityPolicy,
  AgentDefinition,
  AgentDefinitionStep,
  TurnEagerness,
} from '@/types/agent-definition'

const DEBUG_POLL_INTERVAL_MS = 1_500
const DEBUG_POLL_TIMEOUT_MS = 8_000

interface PendingActionShape {
  action_id: string
  action_type?: string
  action_label?: string | null
  tool_ref?: string | null
  status: string
  started_at?: string
  last_progress_at?: string | null
  commitment?: Record<string, unknown>
  activity?: Record<string, unknown>
  user_visible_context?: Record<string, unknown>
}

interface PendingPermissionShape {
  request_id: string
  permission_kind: string
  target_ref?: string | null
  status: string
  started_at?: string
  expires_at?: string | null
  user_visible_context?: Record<string, unknown>
}

interface GroundingShape {
  acknowledged_fact_keys?: string[]
  acknowledged_requests?: string[]
  last_acknowledged_activity_id?: string | null
  last_user_visible_status?: string | null
  unresolved_points?: string[]
}

interface CommitmentShape {
  status?: string
  summary?: string | null
}

interface RepairShape {
  repair_kind: string
  target_ref?: string | null
  summary?: string | null
}

interface FocusShape {
  artifact_id?: string | null
  artifact_type?: string | null
  target_ref?: string | null
  set_at?: string | null
}

interface ArtifactShape {
  artifact_id?: string
  artifact_type?: string
  external_id?: string | null
  source_action_type?: string | null
  status?: string
  title?: string | null
  user_visible_fields?: Record<string, unknown>
  focusable?: boolean
  focus_priority?: number
  created_at?: string
  updated_at?: string
}

interface ConversationControlStateShape {
  pending_action?: PendingActionShape | null
  pending_permission?: PendingPermissionShape | null
  grounding?: GroundingShape
  active_repair?: RepairShape | null
  current_focus?: FocusShape | null
  active_artifacts?: ArtifactShape[]
}

interface ConversationShape {
  conversation_id: string
  step_id?: string
  channel?: string
  facts?: Record<string, unknown>
  control_state?: ConversationControlStateShape
}

interface RealtimeEventShape {
  family: string
  name: string
  payload?: Record<string, unknown>
  created_at?: string
}

interface EventInteractionDebugSnapshot {
  step_id?: string | null
  voice_interaction_policy?: {
    step_id?: string | null
    endpointing_ms?: number
    soft_timeout_ms?: number
    turn_eagerness?: TurnEagerness
    interruptibility_policy?: InterruptibilityPolicy
  } | null
}

interface TimelineItem {
  id: string
  timestamp: string | null
  label: string
  summary: string | null
  stateId: string | null
  voicePolicySummary: string | null
  narrationSummary: string | null
}

interface ResolvedPacing {
  channel: string
  locale: string
  slow_threshold_ms: number
  soft_timeout_ms: number
  endpointing_ms: number
  turn_eagerness: TurnEagerness
  interruptibility_policy: InterruptibilityPolicy
  allow_filler: boolean
  filter_backchannels: boolean
}

// Mirrors the backend channel defaults in `src/ruhu/interaction_pacing.py`
// (`_CHANNEL_DEFAULTS`).  Kept in sync manually — if the backend defaults
// change, update this table or expose a `/pacing-policy` endpoint and drop
// the client-side copy.
const CHANNEL_DEFAULT_PACING: Record<string, ResolvedPacing> = {
  phone: {
    channel: 'phone',
    locale: 'en',
    slow_threshold_ms: 1000,
    soft_timeout_ms: 800,
    endpointing_ms: 650,
    turn_eagerness: 'normal',
    interruptibility_policy: 'interruptible_except_policy',
    allow_filler: true,
    filter_backchannels: true,
  },
  voice: {
    channel: 'voice',
    locale: 'en',
    slow_threshold_ms: 1000,
    soft_timeout_ms: 800,
    endpointing_ms: 650,
    turn_eagerness: 'normal',
    interruptibility_policy: 'interruptible_except_policy',
    allow_filler: true,
    filter_backchannels: true,
  },
  web_widget: {
    channel: 'web_widget',
    locale: 'en',
    slow_threshold_ms: 1200,
    soft_timeout_ms: 800,
    endpointing_ms: 650,
    turn_eagerness: 'normal',
    interruptibility_policy: 'interruptible_except_policy',
    allow_filler: true,
    filter_backchannels: true,
  },
  web_chat: {
    channel: 'web_chat',
    locale: 'en',
    slow_threshold_ms: 1500,
    soft_timeout_ms: 1500,
    endpointing_ms: 650,
    turn_eagerness: 'low',
    interruptibility_policy: 'always_interruptible',
    allow_filler: false,
    filter_backchannels: false,
  },
  whatsapp: {
    channel: 'whatsapp',
    locale: 'en',
    slow_threshold_ms: 2000,
    soft_timeout_ms: 1800,
    endpointing_ms: 650,
    turn_eagerness: 'low',
    interruptibility_policy: 'always_interruptible',
    allow_filler: false,
    filter_backchannels: false,
  },
  browser: {
    channel: 'browser',
    locale: 'en',
    slow_threshold_ms: 1500,
    soft_timeout_ms: 1500,
    endpointing_ms: 650,
    turn_eagerness: 'normal',
    interruptibility_policy: 'always_interruptible',
    allow_filler: false,
    filter_backchannels: false,
  },
}

function resolvePacing(
  channel: string | undefined,
  state: AgentDefinitionStep | null,
): { resolved: ResolvedPacing; overrides: Partial<ResolvedPacing> } {
  const base =
    CHANNEL_DEFAULT_PACING[channel ?? ''] ?? CHANNEL_DEFAULT_PACING.web_chat
  const overrides: Partial<ResolvedPacing> = {}
  if (state) {
    if (state.slow_threshold_ms != null) overrides.slow_threshold_ms = state.slow_threshold_ms
    if (state.soft_timeout_ms != null) overrides.soft_timeout_ms = state.soft_timeout_ms
    if (state.endpointing_ms != null) overrides.endpointing_ms = state.endpointing_ms
    if (state.turn_eagerness != null) overrides.turn_eagerness = state.turn_eagerness
    if (state.interruptibility_policy != null) {
      overrides.interruptibility_policy = state.interruptibility_policy
    }
  }
  return { resolved: { ...base, ...overrides }, overrides }
}

interface InteractionDebugPanelProps {
  conversationId: string | null
  defaultOpen?: boolean
  /** Optional channel to use for pacing defaults (e.g. 'voice', 'web_chat').  If omitted, falls back to conversation.channel. */
  channel?: string
  /** Optional agent definition used to resolve per-state pacing overrides for the currently active state. */
  agentDefinition?: AgentDefinition | null
  /** Monotonically-increasing token (e.g. transcript length) the parent
   * updates after each turn. Triggers a one-shot refetch so the
   * collapsed-panel header badge stays current with the post-turn
   * step. Independent of the polling-while-open path below. */
  turnSequence?: number
}

export function InteractionDebugPanel({
  conversationId,
  defaultOpen = false,
  channel: explicitChannel,
  agentDefinition,
  turnSequence,
}: InteractionDebugPanelProps) {
  const [open, setOpen] = useState(defaultOpen)
  const [conversation, setConversation] = useState<ConversationShape | null>(null)
  const [realtimeEvents, setRealtimeEvents] = useState<RealtimeEventShape[]>([])
  // Per-turn reasoning traces (chosen action, guards, tool calls, latency).
  // Drives the Reasoning Timeline section. Same fetch cadence as the rest of
  // the panel — one-shot on mount/turn, polled while open.
  const [traces, setTraces] = useState<ConversationTrace[]>([])
  const [error, setError] = useState<string | null>(null)
  const [lastFetchedAt, setLastFetchedAt] = useState<Date | null>(null)

  // One-shot fetch: runs on mount, on conversationId change, and on
  // every turn (parent increments ``turnSequence``). Independent of the
  // ``open`` flag so the collapsed-panel header badge ("state: ...")
  // stays current even when the user hasn't expanded the debug
  // section. Does not start a polling loop — that's the next effect.
  useEffect(() => {
    if (!conversationId) return
    let cancelled = false
    const controller = new AbortController()

    const fetchOnce = async () => {
      try {
        const [conversationData, realtimeEventsData, tracesData] = await Promise.all([
          apiClient.get<ConversationShape>(
            `/conversations/${encodeURIComponent(conversationId)}`,
            { signal: controller.signal },
          ),
          apiClient.get<RealtimeEventShape[]>(
            `/conversations/${encodeURIComponent(conversationId)}/realtime-events`,
            { signal: controller.signal },
          ),
          apiClient.get<ConversationTrace[]>(
            `/conversations/${encodeURIComponent(conversationId)}/traces`,
            { signal: controller.signal },
          ),
        ])
        if (!cancelled) {
          setConversation(conversationData)
          setRealtimeEvents(Array.isArray(realtimeEventsData) ? realtimeEventsData : [])
          setTraces(Array.isArray(tracesData) ? tracesData : [])
          setError(null)
          setLastFetchedAt(new Date())
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'failed to load conversation state')
        }
      }
    }
    void fetchOnce()
    return () => {
      cancelled = true
      controller.abort()
    }
  }, [conversationId, turnSequence])

  // Live polling: only when the panel is open AND a conversation is
  // active. Provides sub-second freshness during active debugging
  // (catches in-flight tool actions, status trail updates, etc.).
  useEffect(() => {
    if (!open || !conversationId) return
    let cancelled = false
    let timeoutId: number | null = null
    let inFlightController: AbortController | null = null

    const poll = async () => {
      if (cancelled || inFlightController) return
      const controller = new AbortController()
      inFlightController = controller
      const timeout = window.setTimeout(() => controller.abort(), DEBUG_POLL_TIMEOUT_MS)
      try {
        const [conversationData, realtimeEventsData, tracesData] = await Promise.all([
          apiClient.get<ConversationShape>(
            `/conversations/${encodeURIComponent(conversationId)}`,
            { signal: controller.signal },
          ),
          apiClient.get<RealtimeEventShape[]>(
            `/conversations/${encodeURIComponent(conversationId)}/realtime-events`,
            { signal: controller.signal },
          ),
          apiClient.get<ConversationTrace[]>(
            `/conversations/${encodeURIComponent(conversationId)}/traces`,
            { signal: controller.signal },
          ),
        ])
        if (!cancelled) {
          setConversation(conversationData)
          setRealtimeEvents(Array.isArray(realtimeEventsData) ? realtimeEventsData : [])
          setTraces(Array.isArray(tracesData) ? tracesData : [])
          setError(null)
          setLastFetchedAt(new Date())
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'failed to load conversation state')
        }
      } finally {
        window.clearTimeout(timeout)
        if (inFlightController === controller) {
          inFlightController = null
        }
        if (!cancelled) {
          timeoutId = window.setTimeout(() => {
            void poll()
          }, DEBUG_POLL_INTERVAL_MS)
        }
      }
    }

    void poll()
    return () => {
      cancelled = true
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId)
      }
      inFlightController?.abort()
    }
  }, [open, conversationId])

  const latestNarrationEvent = useMemo<RealtimeEventShape | null>(() => {
    for (let index = realtimeEvents.length - 1; index >= 0; index -= 1) {
      const event = realtimeEvents[index]
      if (event.family === 'narration') {
        return event
      }
    }
    return null
  }, [realtimeEvents])

  const latestNarrationPayload = latestNarrationEvent?.payload ?? {}

  const latestAllowedClaimClasses = Array.isArray(latestNarrationPayload.allowed_claim_classes)
    ? latestNarrationPayload.allowed_claim_classes
        .filter((value): value is string => typeof value === 'string')
        .join(', ')
    : ''

  const control = conversation?.control_state
  const grounding = control?.grounding
  const pendingAction = control?.pending_action ?? null
  const pendingPermission = control?.pending_permission ?? null
  const repair = control?.active_repair ?? null
  const focus = control?.current_focus ?? null
  const activeArtifacts = control?.active_artifacts ?? []

  // Resolve pacing: channel preset + current-state authored overrides.
  // Mirrors the backend's pacing_policy_for_channel + per-state override
  // composition so the debug panel reflects what the runtime actually uses,
  // not a fixed preset.
  const effectiveChannel = explicitChannel || conversation?.channel || 'web_chat'
  const currentState = useMemo<AgentDefinitionStep | null>(() => {
    if (!agentDefinition || !conversation?.step_id) return null
    return agentDefinition.steps.find((s) => s.id === conversation.step_id) ?? null
  }, [agentDefinition, conversation?.step_id])
  const { resolved: resolvedPacing, overrides: pacingOverrides } = useMemo(
    () => resolvePacing(effectiveChannel, currentState),
    [effectiveChannel, currentState],
  )
  const resolvedVoiceInteractionPolicy = useMemo(
    () => ({
      step_id: conversation?.step_id ?? currentState?.id ?? null,
      endpointing_ms: resolvedPacing.endpointing_ms,
      soft_timeout_ms: resolvedPacing.soft_timeout_ms,
      turn_eagerness: resolvedPacing.turn_eagerness,
      interruptibility_policy: resolvedPacing.interruptibility_policy,
    }),
    [
      conversation?.step_id,
      currentState?.id,
      resolvedPacing.endpointing_ms,
      resolvedPacing.soft_timeout_ms,
      resolvedPacing.turn_eagerness,
      resolvedPacing.interruptibility_policy,
    ],
  )

  const groundedFactEntries = useMemo(() => {
    if (!grounding?.acknowledged_fact_keys?.length || !conversation?.facts) return []
    return grounding.acknowledged_fact_keys.map((key) => ({
      key,
      value: conversation.facts?.[key],
    }))
  }, [grounding?.acknowledged_fact_keys, conversation?.facts])

  const recentTimelineItems = useMemo<TimelineItem[]>(() => {
    return realtimeEvents
      .slice(-12)
      .reverse()
      .map((event, index) => {
        const payload = event.payload ?? {}
        const debugSnapshot = readEventInteractionDebugSnapshot(event)
        const eventVoicePolicy = debugSnapshot?.voice_interaction_policy
        const voicePolicySummary = eventVoicePolicy
          ? [
              typeof eventVoicePolicy.endpointing_ms === 'number'
                ? `${eventVoicePolicy.endpointing_ms}ms endpoint`
                : null,
              typeof eventVoicePolicy.turn_eagerness === 'string'
                ? eventVoicePolicy.turn_eagerness
                : null,
              typeof eventVoicePolicy.interruptibility_policy === 'string'
                ? eventVoicePolicy.interruptibility_policy
                : null,
            ]
              .filter(Boolean)
              .join(' · ') || null
          : [
              `${resolvedVoiceInteractionPolicy.endpointing_ms}ms endpoint`,
              resolvedVoiceInteractionPolicy.turn_eagerness,
              resolvedVoiceInteractionPolicy.interruptibility_policy,
            ].join(' · ')
        const narrationSummary =
          event.family === 'narration'
            ? [
                typeof payload.claimed_class === 'string'
                  ? `claim:${payload.claimed_class}`
                  : null,
                typeof payload.narrator_mode === 'string'
                  ? `mode:${payload.narrator_mode}`
                  : null,
                payload.fallback_used === true ? 'fallback' : null,
              ]
                .filter(Boolean)
                .join(' · ') || null
            : null
        return {
          id: `${event.family}:${event.name}:${event.created_at ?? index}`,
          timestamp: event.created_at ?? null,
          label: `${event.family}.${event.name}`,
          summary: summarizeRealtimeEvent(event),
          stateId: debugSnapshot?.step_id ?? conversation?.step_id ?? null,
          voicePolicySummary,
          narrationSummary,
        }
      })
  }, [
    conversation?.step_id,
    realtimeEvents,
    resolvedVoiceInteractionPolicy.endpointing_ms,
    resolvedVoiceInteractionPolicy.interruptibility_policy,
    resolvedVoiceInteractionPolicy.turn_eagerness,
  ])

  if (!conversationId) return null

  return (
    <div className="border-t border-border bg-muted/30">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-2 text-xs text-muted-foreground hover:text-foreground"
      >
        <div className="flex items-center gap-2">
          <span className="font-medium">Interaction state</span>
          {conversation?.step_id && (
            <span className="text-[11px] text-muted-foreground/80">
              state: <code>{conversation.step_id}</code>
            </span>
          )}
          {lastFetchedAt && open && (
            <span className="flex items-center gap-1 text-[10px] text-muted-foreground/60">
              <Clock className="h-3 w-3" />
              {lastFetchedAt.toLocaleTimeString()}
            </span>
          )}
        </div>
        {open ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
      </button>

      {open && (
        <div className="max-h-[340px] overflow-y-auto space-y-3 border-t border-border px-4 py-3 text-xs">
          {error && (
            <div className="rounded-md border border-rose-400/40 bg-rose-500/10 px-2 py-1.5 text-rose-300">
              {error}
            </div>
          )}

          {/* Reasoning timeline — Sierra-style per-turn evidence trail */}
          <Section title="Reasoning timeline" emptyLabel="No turns recorded yet.">
            {traces.length > 0 && (
              <ReasoningTimeline traces={traces} className="-mx-1.5 px-0" />
            )}
          </Section>

          {/* Grounded facts */}
          <Section title="Grounded facts" emptyLabel="No acknowledged facts yet.">
            {groundedFactEntries.length > 0 && (
              <ul className="space-y-1 font-mono text-[11px]">
                {groundedFactEntries.map(({ key, value }) => (
                  <li key={key} className="flex gap-2">
                    <span className="text-muted-foreground">{key}</span>
                    <span>= {formatValue(value)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          {/* Pending action */}
          <Section title="Pending action" emptyLabel="No action in flight.">
            {pendingAction && (
              <div className="space-y-1">
                <KV label="label">{pendingAction.action_label ?? pendingAction.action_type ?? '—'}</KV>
                <KV label="status">
                  <StatusTag status={pendingAction.status} />
                </KV>
                {pendingAction.tool_ref && <KV label="tool">{pendingAction.tool_ref}</KV>}
                {pendingAction.started_at && (
                  <KV label="started">{formatTs(pendingAction.started_at)}</KV>
                )}
                {pendingAction.commitment?.status != null && (
                  <KV label="commitment">{String(pendingAction.commitment.status)}</KV>
                )}
              </div>
            )}
          </Section>

          {/* Pending permission */}
          <Section title="Pending permission" emptyLabel="No permission wait.">
            {pendingPermission && (
              <div className="space-y-1">
                <KV label="kind">{pendingPermission.permission_kind}</KV>
                <KV label="status">
                  <StatusTag status={pendingPermission.status} />
                </KV>
                {pendingPermission.target_ref && (
                  <KV label="target">{pendingPermission.target_ref}</KV>
                )}
                {pendingPermission.expires_at && (
                  <KV label="expires">{formatTs(pendingPermission.expires_at)}</KV>
                )}
              </div>
            )}
          </Section>

          {/* Commitment state */}
          <Section title="Commitment" emptyLabel="No explicit commitment.">
            {pendingAction?.commitment && Object.keys(pendingAction.commitment).length > 0 && (
              <div className="space-y-1">
                {Object.entries(pendingAction.commitment as Record<string, unknown>).map(([k, v]) => (
                  <KV key={k} label={k}>
                    {formatValue(v)}
                  </KV>
                ))}
              </div>
            )}
          </Section>

          {/* Active repair */}
          <Section title="Active repair" emptyLabel="Not in repair.">
            {repair && (
              <div className="space-y-1">
                <KV label="kind">{repair.repair_kind}</KV>
                {repair.target_ref && <KV label="target">{repair.target_ref}</KV>}
                {repair.summary && <KV label="summary">{repair.summary}</KV>}
              </div>
            )}
          </Section>

          {/* Artifact focus */}
          <Section title="Artifact focus" emptyLabel="No artifact currently in focus.">
            {focus && (
              <div className="space-y-1">
                {focus.artifact_type && <KV label="type">{focus.artifact_type}</KV>}
                {focus.artifact_id && <KV label="id">{focus.artifact_id}</KV>}
                {focus.target_ref && <KV label="ref">{focus.target_ref}</KV>}
                {focus.set_at && <KV label="set_at">{formatTs(focus.set_at)}</KV>}
              </div>
            )}
          </Section>

          {/* Active artifacts — rich view */}
          <Section
            title={`Active artifacts${activeArtifacts.length > 0 ? ` (${activeArtifacts.length})` : ''}`}
            emptyLabel="No artifacts created yet."
          >
            {activeArtifacts.length > 0 && (
              <div className="space-y-2">
                {activeArtifacts.map((art, i) => {
                  const isFocused =
                    focus?.artifact_id && focus.artifact_id === art.artifact_id
                  return (
                    <div
                      key={art.artifact_id ?? i}
                      className={cn(
                        'rounded-md border px-2 py-1.5',
                        isFocused
                          ? 'border-primary/60 bg-primary/5'
                          : 'border-border/60 bg-background',
                      )}
                    >
                      <div className="mb-1 flex items-center gap-1.5">
                        <span className="font-mono text-[11px] text-muted-foreground">
                          {art.artifact_type ?? 'artifact'}
                        </span>
                        {art.status && <StatusTag status={art.status} />}
                        {isFocused && (
                          <span className="rounded bg-primary/15 px-1 py-0.5 text-[9px] font-medium uppercase tracking-wide text-primary">
                            focus
                          </span>
                        )}
                        {art.focusable === false && (
                          <span className="rounded bg-muted px-1 py-0.5 text-[9px] font-medium uppercase tracking-wide text-muted-foreground">
                            non-focusable
                          </span>
                        )}
                      </div>
                      <div className="space-y-0.5">
                        {art.title && <KV label="title">{art.title}</KV>}
                        {art.artifact_id && <KV label="id">{art.artifact_id}</KV>}
                        {art.external_id && <KV label="external">{art.external_id}</KV>}
                        {art.focus_priority !== undefined && art.focus_priority !== 100 && (
                          <KV label="priority">{art.focus_priority}</KV>
                        )}
                        {art.created_at && (
                          <KV label="created">{formatTs(art.created_at)}</KV>
                        )}
                        {art.updated_at && art.updated_at !== art.created_at && (
                          <KV label="updated">{formatTs(art.updated_at)}</KV>
                        )}
                        {art.user_visible_fields &&
                          Object.keys(art.user_visible_fields).length > 0 && (
                            <details className="mt-1">
                              <summary className="cursor-pointer text-[10px] text-muted-foreground hover:text-foreground">
                                user-visible fields
                              </summary>
                              <div className="mt-1 space-y-0.5 pl-2">
                                {Object.entries(art.user_visible_fields).map(([k, v]) => (
                                  <KV key={k} label={k}>
                                    {formatValue(v)}
                                  </KV>
                                ))}
                              </div>
                            </details>
                          )}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </Section>

          {/* Resolved pacing — channel default + authored state overrides */}
          <Section title="Resolved pacing">
            <div className="space-y-1">
              <KV label="channel">{resolvedPacing.channel}</KV>
              <KV label="locale">{resolvedPacing.locale}</KV>
              <PacingKV
                label="slow_threshold_ms"
                value={resolvedPacing.slow_threshold_ms}
                overridden={'slow_threshold_ms' in pacingOverrides}
              />
              <PacingKV
                label="soft_timeout_ms"
                value={resolvedPacing.soft_timeout_ms}
                overridden={'soft_timeout_ms' in pacingOverrides}
              />
              {resolvedPacing.endpointing_ms > 0 && (
                <PacingKV
                  label="endpointing_ms"
                  value={resolvedPacing.endpointing_ms}
                  overridden={'endpointing_ms' in pacingOverrides}
                />
              )}
              <PacingKV
                label="turn_eagerness"
                value={resolvedPacing.turn_eagerness}
                overridden={'turn_eagerness' in pacingOverrides}
              />
              <PacingKV
                label="interruptibility"
                value={resolvedPacing.interruptibility_policy}
                overridden={'interruptibility_policy' in pacingOverrides}
              />
              <KV label="filler">{resolvedPacing.allow_filler ? 'allowed' : 'off'}</KV>
              {Object.keys(pacingOverrides).length > 0 && (
                <p className="pt-1 text-[10px] text-amber-400/80">
                  Bold values are overridden by state{' '}
                  <code>{currentState?.id ?? conversation?.step_id}</code>.
                </p>
              )}
            </div>
          </Section>

          <Section title="Resolved voice_interaction_policy">
            <div className="space-y-1">
              <KV label="step_id">{resolvedVoiceInteractionPolicy.step_id ?? '—'}</KV>
              <KV label="endpointing_ms">{resolvedVoiceInteractionPolicy.endpointing_ms}</KV>
              <KV label="soft_timeout_ms">{resolvedVoiceInteractionPolicy.soft_timeout_ms}</KV>
              <KV label="turn_eagerness">{resolvedVoiceInteractionPolicy.turn_eagerness}</KV>
              <KV label="interruptibility_policy">
                {resolvedVoiceInteractionPolicy.interruptibility_policy}
              </KV>
            </div>
          </Section>

          <Section title="Latest narration" emptyLabel="No narration telemetry yet.">
            {latestNarrationEvent && (
              <div className="space-y-1">
                <KV label="event">{latestNarrationEvent.name}</KV>
                {latestNarrationEvent.created_at && (
                  <KV label="at">{formatTs(latestNarrationEvent.created_at)}</KV>
                )}
                {typeof latestNarrationPayload.response_mode === 'string' && (
                  <KV label="response_mode">{latestNarrationPayload.response_mode}</KV>
                )}
                {typeof latestNarrationPayload.claimed_class === 'string' && (
                  <KV label="claimed_class">{latestNarrationPayload.claimed_class}</KV>
                )}
                {latestAllowedClaimClasses && (
                  <KV label="allowed_claim_classes">{latestAllowedClaimClasses}</KV>
                )}
                {typeof latestNarrationPayload.narrator_mode === 'string' && (
                  <KV label="narrator_mode">{latestNarrationPayload.narrator_mode}</KV>
                )}
                {typeof latestNarrationPayload.fallback_used === 'boolean' && (
                  <KV label="fallback_used">
                    {latestNarrationPayload.fallback_used ? 'yes' : 'no'}
                  </KV>
                )}
                {typeof latestNarrationPayload.fallback_reason === 'string' && (
                  <KV label="fallback_reason">{latestNarrationPayload.fallback_reason}</KV>
                )}
              </div>
            )}
          </Section>

          <Section title="Recent interaction timeline" emptyLabel="No lifecycle or narration events yet.">
            {recentTimelineItems.length > 0 && (
              <div className="space-y-2">
                {recentTimelineItems.map((item) => (
                  <div
                    key={item.id}
                    className="rounded-md border border-border/60 bg-muted/20 px-2 py-1.5"
                  >
                    <div className="flex flex-wrap items-center gap-2 font-mono text-[11px]">
                      <span className="text-muted-foreground">
                        {item.timestamp ? formatTs(item.timestamp) : '—'}
                      </span>
                      <span className="font-semibold">{item.label}</span>
                      {item.stateId && (
                        <span className="text-muted-foreground">state:{item.stateId}</span>
                      )}
                    </div>
                    {item.summary && (
                      <p className="mt-1 text-[11px] text-foreground/90">{item.summary}</p>
                    )}
                    <div className="mt-1 space-y-0.5 font-mono text-[10px] text-muted-foreground">
                      {item.voicePolicySummary && <div>voice: {item.voicePolicySummary}</div>}
                      {item.narrationSummary && <div>narration: {item.narrationSummary}</div>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Section>
        </div>
      )}
    </div>
  )
}

// ─── helpers ───────────────────────────────────────────────────────────────

function Section({
  title,
  emptyLabel,
  children,
}: {
  title: string
  emptyLabel?: string
  children?: React.ReactNode
}) {
  const hasContent =
    children !== undefined && children !== null && children !== false && children !== ''
  return (
    <div className="rounded-md border border-border/60 bg-background px-2.5 py-2">
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </div>
      {hasContent ? (
        <div>{children}</div>
      ) : (
        <div className="text-[11px] text-muted-foreground/70">{emptyLabel ?? '—'}</div>
      )}
    </div>
  )
}

function KV({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2 font-mono text-[11px]">
      <span className="text-muted-foreground">{label}</span>
      <span>{children}</span>
    </div>
  )
}

function PacingKV({
  label,
  value,
  overridden,
}: {
  label: string
  value: string | number
  overridden: boolean
}) {
  return (
    <div className="flex gap-2 font-mono text-[11px]">
      <span className="text-muted-foreground">{label}</span>
      <span className={overridden ? 'font-semibold text-amber-300' : undefined}>
        {value}
        {overridden && <span className="ml-1 text-[9px] text-amber-300/80">(override)</span>}
      </span>
    </div>
  )
}

function StatusTag({ status }: { status: string }) {
  const tone = statusTone(status)
  return (
    <span
      className={cn(
        'inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium',
        tone,
      )}
    >
      {status}
    </span>
  )
}

function statusTone(status: string): string {
  switch (status) {
    case 'completed':
    case 'granted':
      return 'bg-emerald-500/15 text-emerald-400'
    case 'failed':
    case 'denied':
    case 'expired':
    case 'aborted':
      return 'bg-rose-500/15 text-rose-400'
    case 'cancelled':
    case 'cancelling':
      return 'bg-amber-500/15 text-amber-400'
    case 'completion_uncertain':
      return 'bg-purple-500/15 text-purple-300'
    case 'slow':
    case 'running':
    case 'starting':
    case 'waiting':
    default:
      return 'bg-muted text-muted-foreground'
  }
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'string') return v.length > 80 ? `${v.slice(0, 77)}…` : v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  try {
    return JSON.stringify(v)
  } catch {
    return String(v)
  }
}

function formatTs(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString()
  } catch {
    return iso
  }
}

function summarizeRealtimeEvent(event: RealtimeEventShape): string | null {
  const payload = event.payload ?? {}
  const stringValue = (key: string): string | null =>
    typeof payload[key] === 'string' && payload[key].trim() ? payload[key].trim() : null

  if (event.family === 'interaction') {
    return (
      stringValue('summary') ??
      stringValue('action_label') ??
      stringValue('permission_kind') ??
      stringValue('repair_kind') ??
      stringValue('target_ref') ??
      stringValue('status')
    )
  }
  if (event.family === 'artifact') {
    return (
      stringValue('title') ??
      stringValue('artifact_type') ??
      stringValue('followup_intent') ??
      stringValue('target_ref')
    )
  }
  if (event.family === 'grounding') {
    const keys = Array.isArray(payload.acknowledged_fact_keys)
      ? payload.acknowledged_fact_keys.filter((value): value is string => typeof value === 'string')
      : []
    return keys.length > 0 ? `acknowledged: ${keys.join(', ')}` : stringValue('last_user_visible_status')
  }
  if (event.family === 'narration') {
    return (
      stringValue('response_mode') ??
      stringValue('fallback_reason') ??
      stringValue('claimed_class')
    )
  }
  return stringValue('summary') ?? stringValue('status') ?? null
}

function readEventInteractionDebugSnapshot(
  event: RealtimeEventShape,
): EventInteractionDebugSnapshot | null {
  const payload = event.payload ?? {}
  const snapshot = payload.interaction_debug_snapshot
  if (!snapshot || typeof snapshot !== 'object' || Array.isArray(snapshot)) {
    return null
  }
  return snapshot as EventInteractionDebugSnapshot
}
