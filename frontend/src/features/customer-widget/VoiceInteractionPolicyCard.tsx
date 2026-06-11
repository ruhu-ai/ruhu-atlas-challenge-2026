import type { WidgetVoiceInteractionPolicy } from './widget-types'

interface VoiceInteractionPolicyCardProps {
  policy: WidgetVoiceInteractionPolicy | null
}

export function VoiceInteractionPolicyCard({
  policy,
}: VoiceInteractionPolicyCardProps) {
  if (!policy) return null

  const rows: Array<[string, string | number]> = []
  if (policy.step_id) rows.push(['state_id', policy.step_id])
  if (policy.endpointing_ms != null) rows.push(['endpointing_ms', policy.endpointing_ms])
  if (policy.soft_timeout_ms != null) rows.push(['soft_timeout_ms', policy.soft_timeout_ms])
  if (policy.turn_eagerness) rows.push(['turn_eagerness', policy.turn_eagerness])
  if (policy.interruptibility_policy) {
    rows.push(['interruptibility_policy', policy.interruptibility_policy])
  }
  if (rows.length === 0) return null

  return (
    <details
      className="widget-status-banner"
      style={{ marginTop: 8, whiteSpace: 'normal' }}
      data-testid="voice-interaction-policy"
    >
      <summary style={{ cursor: 'pointer', fontWeight: 600 }}>
        Voice policy in force
      </summary>
      <div style={{ marginTop: 8, display: 'grid', gap: 4, fontFamily: 'monospace', fontSize: '0.72rem' }}>
        {rows.map(([label, value]) => (
          <div key={label} style={{ display: 'flex', gap: 8 }}>
            <span style={{ opacity: 0.72 }}>{label}</span>
            <span>{String(value)}</span>
          </div>
        ))}
      </div>
    </details>
  )
}
