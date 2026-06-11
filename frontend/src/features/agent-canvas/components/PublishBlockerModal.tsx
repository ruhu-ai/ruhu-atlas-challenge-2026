/**
 * Publish Blocker Modal
 *
 * Surfaces structured publish-review blockers — most importantly, the
 * `tool.missing_runtime_spec` blocker that fires when a draft references
 * an org-scoped tool the customer hasn't configured yet. Each blocker
 * with `remediation` renders an actionable "Set up X" button that
 * deep-links into the right Integrations page (provenance-derived label).
 *
 * Replaces the previous toast-on-409 UX.
 *
 * See docs/templates/Template-Required-Tools-Onboarding-Spec.md §5.6.3.
 */

import { useNavigate } from 'react-router-dom'
import { AlertTriangle, ExternalLink } from 'lucide-react'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/atoms/dialog'
import { Button } from '@/components/atoms/button'
import type { PublishReviewItem } from '@/types/agent-definition'

export interface PublishBlockerModalProps {
  open: boolean
  blockers: PublishReviewItem[]
  agentId: string
  onClose: () => void
}

export function PublishBlockerModal({
  open,
  blockers,
  agentId,
  onClose,
}: PublishBlockerModalProps) {
  const navigate = useNavigate()

  if (blockers.length === 0) return null

  const firstActionable = blockers.find((b) => b.remediation?.url)

  const handlePrimary = () => {
    if (firstActionable?.remediation?.url) {
      navigate(firstActionable.remediation.url)
    }
    onClose()
  }

  const handleViewSetup = () => {
    navigate(`/agents/${agentId}/setup`)
    onClose()
  }

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-amber-600" />
            Can't publish yet
          </DialogTitle>
          <DialogDescription>
            {blockers.length === 1
              ? 'One issue is blocking publishing.'
              : `${blockers.length} issues are blocking publishing.`}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          {blockers.map((blocker, idx) => (
            <div
              key={`${blocker.code}-${idx}`}
              className="rounded-md border bg-card p-3"
            >
              <p className="text-sm font-medium">{blocker.message}</p>
              <code className="text-xs text-muted-foreground font-mono">
                {blocker.code}
              </code>
              {blocker.remediation && (
                <div className="mt-3 flex items-center gap-2">
                  <Button
                    size="sm"
                    onClick={() => {
                      navigate(blocker.remediation!.url)
                      onClose()
                    }}
                  >
                    {blocker.remediation.label}
                    <ExternalLink className="ml-1 h-3.5 w-3.5" />
                  </Button>
                  {blocker.remediation.documentation_url && (
                    <a
                      href={blocker.remediation.documentation_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs text-primary hover:underline inline-flex items-center gap-1"
                    >
                      Docs
                      <ExternalLink className="h-3 w-3" />
                    </a>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="flex items-center justify-between gap-3 pt-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <div className="flex gap-2">
            <Button variant="outline" onClick={handleViewSetup}>
              Open setup checklist
            </Button>
            {firstActionable && (
              <Button onClick={handlePrimary}>
                {firstActionable.remediation!.label}
              </Button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
