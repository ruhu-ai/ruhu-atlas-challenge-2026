/**
 * ActivityPill — ephemeral agent work-state indicator.
 *
 * Shown inline in the transcript while the agent is performing work.
 * This is not a chat message and is never persisted to conversation history.
 *
 * States:
 *   started / updated            → spinner + active work label
 *   retrying                     → retry icon + retry label
 *   waiting_for_confirmation     → paused / waiting state
 *   blocked                      → warning state
 *   completed                    → success state, auto-fades and expires
 *   failed                       → error state, persists until next user turn
 *
 * A short show delay prevents flash for very fast operations.
 */

import { useEffect, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  PauseCircle,
  RotateCcw,
  XCircle,
} from 'lucide-react'

export interface ActivityPillItem {
  activityId: string
  eventType:
    | 'started'
    | 'updated'
    | 'retrying'
    | 'waiting_for_confirmation'
    | 'blocked'
    | 'completed'
    | 'failed'
  label: string
  stepKind: string
  stepId: string
  toolName?: string
  startedAt?: string
  durationMs?: number
  retryCount?: number
  detail?: string
  actionLabel?: string
  invocationId?: string
}

const ACTIVE_ACTIVITY_STATES: ReadonlySet<ActivityPillItem['eventType']> = new Set([
  'started',
  'updated',
  'retrying',
  'waiting_for_confirmation',
])

export function hasActiveActivities(activities: Iterable<ActivityPillItem>): boolean {
  for (const activity of activities) {
    if (ACTIVE_ACTIVITY_STATES.has(activity.eventType)) {
      return true
    }
  }
  return false
}

interface Props {
  activity: ActivityPillItem
  onExpire?: (activityId: string) => void
  onConfirm?: (invocationId: string) => void
  onCancel?: (invocationId: string) => void
}

const SHOW_DELAY_MS = 500
const HIDE_AFTER_MS = 4000
const FADE_DURATION_MS = 600

function getPalette(eventType: ActivityPillItem['eventType']) {
  switch (eventType) {
    case 'completed':
      return { bg: 'rgba(22, 163, 74, 0.08)', border: 'rgba(22, 163, 74, 0.18)', fg: '#166534' }
    case 'failed':
      return { bg: 'rgba(220, 38, 38, 0.08)', border: 'rgba(220, 38, 38, 0.18)', fg: '#b91c1c' }
    case 'blocked':
      return { bg: 'rgba(217, 119, 6, 0.10)', border: 'rgba(217, 119, 6, 0.20)', fg: '#b45309' }
    case 'waiting_for_confirmation':
      return { bg: 'rgba(180, 83, 9, 0.10)', border: 'rgba(180, 83, 9, 0.20)', fg: '#92400e' }
    case 'retrying':
      return { bg: 'rgba(14, 116, 144, 0.10)', border: 'rgba(14, 116, 144, 0.20)', fg: '#0f766e' }
    default:
      return { bg: 'rgba(15, 23, 42, 0.04)', border: 'rgba(15, 23, 42, 0.08)', fg: 'rgba(15, 23, 42, 0.72)' }
  }
}

function renderIcon(eventType: ActivityPillItem['eventType']) {
  switch (eventType) {
    case 'completed':
      return <CheckCircle2 style={{ width: 14, height: 14, color: '#16a34a', flexShrink: 0 }} />
    case 'failed':
      return <XCircle style={{ width: 14, height: 14, color: '#dc2626', flexShrink: 0 }} />
    case 'blocked':
      return <AlertTriangle style={{ width: 14, height: 14, color: '#d97706', flexShrink: 0 }} />
    case 'waiting_for_confirmation':
      return <PauseCircle style={{ width: 14, height: 14, color: '#b45309', flexShrink: 0 }} />
    case 'retrying':
      return <RotateCcw style={{ width: 14, height: 14, color: '#0f766e', flexShrink: 0 }} />
    case 'updated':
    case 'started':
    default:
      return (
        <Loader2
          style={{ width: 14, height: 14, color: 'currentColor', flexShrink: 0 }}
          className="activity-pill__icon--spin animate-spin"
        />
      )
  }
}

export function ActivityPill({ activity, onExpire, onConfirm, onCancel }: Props) {
  const [visible, setVisible] = useState(
    !['started', 'updated', 'retrying'].includes(activity.eventType),
  )
  const [fading, setFading] = useState(false)

  useEffect(() => {
    setFading(false)
    if (['started', 'updated', 'retrying'].includes(activity.eventType)) {
      const t = setTimeout(() => setVisible(true), SHOW_DELAY_MS)
      return () => clearTimeout(t)
    }
    setVisible(true)
  }, [activity.eventType])

  useEffect(() => {
    if (activity.eventType !== 'completed') return

    const fadeTimer = setTimeout(() => setFading(true), HIDE_AFTER_MS)
    const expireTimer = setTimeout(() => {
      onExpire?.(activity.activityId)
    }, HIDE_AFTER_MS + FADE_DURATION_MS)

    return () => {
      clearTimeout(fadeTimer)
      clearTimeout(expireTimer)
    }
  }, [activity.activityId, activity.eventType, onExpire])

  if (!visible) return null

  const palette = getPalette(activity.eventType)
  const durationSec = activity.durationMs != null
    ? `${(activity.durationMs / 1000).toFixed(1)}s`
    : null
  const primaryText = activity.eventType === 'completed' && durationSec
    ? `${activity.label.replace(/\.\.\.$/, '')} · ${durationSec}`
    : activity.label
  const secondaryText = activity.detail?.trim() || ''

  return (
    <div
      className="activity-pill"
      data-state={activity.eventType}
      style={{
        display: 'flex',
        alignItems: secondaryText ? 'flex-start' : 'center',
        gap: '8px',
        width: 'fit-content',
        maxWidth: '85%',
        margin: '4px 0',
        padding: secondaryText ? '8px 12px' : '6px 12px',
        borderRadius: '999px',
        background: palette.bg,
        border: `1px solid ${palette.border}`,
        color: palette.fg,
        fontSize: '0.78rem',
        lineHeight: 1.35,
        opacity: fading ? 0 : 1,
        transition: fading ? `opacity ${FADE_DURATION_MS}ms ease` : undefined,
      }}
    >
      <div style={{ marginTop: secondaryText ? '2px' : 0 }}>
        {renderIcon(activity.eventType)}
      </div>
      <div style={{ minWidth: 0 }}>
        <div>{primaryText}</div>
        {secondaryText && (
          <div style={{ fontSize: '0.72rem', opacity: 0.82, marginTop: '2px' }}>
            {secondaryText}
          </div>
        )}
        {activity.actionLabel && (
          <div style={{ fontSize: '0.68rem', fontWeight: 600, marginTop: '4px' }}>
            {activity.actionLabel}
          </div>
        )}
        {activity.eventType === 'waiting_for_confirmation' && activity.invocationId && (
          <div style={{ display: 'flex', gap: '6px', marginTop: '6px' }}>
            {onConfirm && (
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); onConfirm(activity.invocationId!) }}
                style={{
                  padding: '2px 10px',
                  borderRadius: '6px',
                  border: '1px solid rgba(22, 163, 74, 0.3)',
                  background: 'rgba(22, 163, 74, 0.1)',
                  color: '#166534',
                  fontSize: '0.72rem',
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
              >
                Confirm
              </button>
            )}
            {onCancel && (
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); onCancel(activity.invocationId!) }}
                style={{
                  padding: '2px 10px',
                  borderRadius: '6px',
                  border: '1px solid rgba(220, 38, 38, 0.3)',
                  background: 'rgba(220, 38, 38, 0.08)',
                  color: '#b91c1c',
                  fontSize: '0.72rem',
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
              >
                Cancel
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
