export type LifecycleCategory =
  | 'activity'
  | 'interrupt'
  | 'repair'
  | 'policy'
  | 'permission'
  | 'grounding'
  | 'capture'
  | 'narration'
  | 'artifact'
  | null

const LIFECYCLE_EVENT_CATEGORY: Record<string, LifecycleCategory> = {
  activity_started: 'activity',
  activity_slow: 'activity',
  activity_completed: 'activity',
  activity_failed: 'activity',
  activity_cancel_requested: 'interrupt',
  activity_cancelled: 'interrupt',
  activity_completion_uncertain: 'activity',
  interrupt_acknowledged: 'interrupt',
  capture_complete: 'capture',
  repair_required: 'repair',
  policy_blocked: 'policy',
  permission_requested: 'permission',
  permission_resolved: 'permission',
  status_trail_updated: 'activity',
  narration_rendered: 'narration',
  narration_regenerated: 'narration',
  narration_fallback: 'narration',
}

const ARTIFACT_EVENT_NAMES = new Set([
  'created',
  'updated',
  'focused',
  'resolved',
  'followup_handler_selected',
  'resolution_ambiguous',
  'explicit_id_missing',
])

export function classifyLifecycle(family: string, eventName: string): LifecycleCategory {
  if (family === 'artifact' && ARTIFACT_EVENT_NAMES.has(eventName)) {
    return 'artifact'
  }
  if (family === 'grounding') {
    return 'grounding'
  }
  return LIFECYCLE_EVENT_CATEGORY[eventName] ?? null
}
