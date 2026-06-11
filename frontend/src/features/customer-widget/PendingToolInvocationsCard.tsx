import type { ToolInvocation } from './widget-types'

interface Props {
  invocations: ToolInvocation[]
  onConfirm: (invocationId: string) => Promise<void> | void
  onCancel: (invocationId: string) => Promise<void> | void
  busyInvocationId?: string | null
}

function invocationSummary(invocation: ToolInvocation): string {
  const prompt = invocation.metadata?.confirmation_prompt
  if (typeof prompt === 'string' && prompt.trim()) return prompt
  if (invocation.error && invocation.error.trim()) return invocation.error
  if (invocation.decision_reason && invocation.decision_reason.trim()) return invocation.decision_reason
  if (invocation.reason && invocation.reason.trim()) return invocation.reason
  return invocation.tool_ref
}

export function PendingToolInvocationsCard({
  invocations,
  onConfirm,
  onCancel,
  busyInvocationId = null,
}: Props) {
  if (!invocations.length) return null

  return (
    <div className="widget-confirmation-stack">
      {invocations.map((invocation) => {
        const isBusy = busyInvocationId === invocation.invocation_id
        return (
          <div
            key={invocation.invocation_id}
            className="widget-confirmation-card"
            data-invocation-id={invocation.invocation_id}
          >
            <div className="widget-confirmation-title">Confirmation required</div>
            <div>{invocationSummary(invocation)}</div>
            <div className="widget-confirmation-actions">
              <button
                type="button"
                className="widget-confirmation-btn"
                disabled={isBusy}
                onClick={() => void onConfirm(invocation.invocation_id)}
              >
                {isBusy ? 'Submitting…' : 'Confirm'}
              </button>
              <button
                type="button"
                className="widget-confirmation-btn secondary"
                disabled={isBusy}
                onClick={() => void onCancel(invocation.invocation_id)}
              >
                Cancel
              </button>
            </div>
          </div>
        )
      })}
    </div>
  )
}
