import { Loader2 } from 'lucide-react'

import { Button } from '@/components/atoms/button'
import type {
  CanonicalAtlasDelta,
  CanonicalAtlasPermissionRequest,
  CanonicalAtlasProposedChanges,
} from '@/api/services/atlas.service'
import { formatShortTimestamp, titleCase } from './atlas-shared'

// Build a flat lookup map from delta_id → delta across every delta family in
// proposed_changes. Used by PermissionCard to render the actual changes a
// user is about to authorize, instead of an opaque count.
export function buildDeltaLookup(
  proposedChanges: CanonicalAtlasProposedChanges,
): Map<string, CanonicalAtlasDelta> {
  const lookup = new Map<string, CanonicalAtlasDelta>()
  const families: CanonicalAtlasDelta[][] = [
    proposedChanges.agent_metadata_deltas,
    proposedChanges.scenario_deltas,
    proposedChanges.step_deltas,
    proposedChanges.scenario_route_deltas,
    proposedChanges.channel_policy_deltas,
    proposedChanges.rule_deltas,
    proposedChanges.knowledge_deltas,
    proposedChanges.integration_binding_deltas,
  ]
  for (const family of families) {
    for (const delta of family) {
      lookup.set(delta.delta_id, delta)
    }
  }
  return lookup
}

export interface AtlasPermissionCardProps {
  requests: CanonicalAtlasPermissionRequest[]
  proposedChanges: CanonicalAtlasProposedChanges
  onApprove: (() => void) | null
  onReject: (() => void) | null
  isDeciding: boolean
}

export function AtlasPermissionCard({
  requests,
  proposedChanges,
  onApprove,
  onReject,
  isDeciding,
}: AtlasPermissionCardProps) {
  if (requests.length === 0) return null
  const deltaLookup = buildDeltaLookup(proposedChanges)
  return (
    <div className="space-y-2 rounded-md border border-border/70 bg-muted/20 px-3 py-3">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Pending Permission</p>
      {requests.map((request) => {
        const referencedDeltas = request.delta_ids
          .map((id) => deltaLookup.get(id))
          .filter((delta): delta is CanonicalAtlasDelta => delta !== undefined)
        const missingDeltaIds = request.delta_ids.filter((id) => !deltaLookup.has(id))
        return (
          <div key={request.request_id} className="space-y-2 rounded-md border border-border/70 bg-background/70 px-3 py-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm text-foreground">{request.reason}</p>
              <span className="rounded-full bg-muted px-2 py-1 text-[11px] font-medium text-muted-foreground">
                {titleCase(request.kind)} • {titleCase(request.status)}
              </span>
            </div>
            {request.risk_summary && <p className="text-xs text-muted-foreground">{request.risk_summary}</p>}
            <div className="grid grid-cols-2 gap-2 text-xs">
              <p className="text-muted-foreground">
                Requested actions: <span className="text-foreground">{request.requested_actions.length}</span>
              </p>
              <p className="text-muted-foreground">
                Impacted deltas: <span className="text-foreground">{request.delta_ids.length}</span>
              </p>
              {formatShortTimestamp(request.created_at) && (
                <p className="text-muted-foreground">
                  Requested: <span className="text-foreground">{formatShortTimestamp(request.created_at)}</span>
                </p>
              )}
              {formatShortTimestamp(request.expires_at) && (
                <p className="text-muted-foreground">
                  Expires: <span className="text-foreground">{formatShortTimestamp(request.expires_at)}</span>
                </p>
              )}
            </div>
            {referencedDeltas.length > 0 && (
              <div className="space-y-1.5 rounded-md border border-border/70 bg-muted/20 px-2 py-2">
                <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Changes you are approving
                </p>
                <ul className="space-y-1.5">
                  {referencedDeltas.map((delta) => (
                    <li
                      key={delta.delta_id}
                      className="flex flex-wrap items-start gap-2 rounded-md bg-background/70 px-2 py-1.5"
                    >
                      <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                        {delta.operation}
                      </span>
                      <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
                        {titleCase(delta.change_type)}
                      </span>
                      <p className="min-w-0 flex-1 text-xs text-foreground">{delta.summary || 'No summary provided'}</p>
                    </li>
                  ))}
                </ul>
                {missingDeltaIds.length > 0 && (
                  <p className="text-[11px] text-amber-600 dark:text-amber-400">
                    {missingDeltaIds.length} delta{missingDeltaIds.length === 1 ? '' : 's'} referenced by this
                    permission request were not found in the current proposed changes
                    ({missingDeltaIds.join(', ')}).
                  </p>
                )}
              </div>
            )}
            {referencedDeltas.length === 0 && request.delta_ids.length > 0 && (
              <p className="text-[11px] text-amber-600 dark:text-amber-400">
                This permission references {request.delta_ids.length} delta
                {request.delta_ids.length === 1 ? '' : 's'} that could not be loaded
                ({request.delta_ids.join(', ')}). Re-run the turn to refresh details before approving.
              </p>
            )}
            {request.requested_actions.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {request.requested_actions.map((action) => (
                  <span key={action} className="rounded-full bg-muted px-2 py-1 text-[11px] text-muted-foreground">
                    {titleCase(action)}
                  </span>
                ))}
              </div>
            )}
            {Object.keys(request.scope_ref).length > 0 && (
              <div className="rounded-md border border-border/70 bg-muted/20 px-2 py-1.5">
                <p className="text-[11px] font-medium text-muted-foreground">Scope context</p>
                <div className="mt-1 flex flex-wrap gap-2">
                  {Object.entries(request.scope_ref).map(([key, value]) => (
                    <span key={key} className="rounded-full bg-background px-2 py-1 text-[11px] text-muted-foreground">
                      {key}: <span className="text-foreground">{String(value)}</span>
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )
      })}
      <div className="flex gap-2 pt-1">
        {onApprove && (
          <Button onClick={onApprove} disabled={isDeciding} className="flex-1">
            {isDeciding ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            Approve Permission
          </Button>
        )}
        {onReject && (
          <Button onClick={onReject} disabled={isDeciding} variant="outline" className="flex-1">
            Reject
          </Button>
        )}
      </div>
    </div>
  )
}
