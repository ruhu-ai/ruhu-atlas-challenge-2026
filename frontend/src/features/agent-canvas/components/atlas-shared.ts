// Shared helpers for the Atlas authoring surface. Pure data-transform
// functions extracted from AtlasAIPanel.tsx so sibling components
// (AtlasPermissionCard, AtlasResponseCard, etc.) can import them without
// pulling in the full panel module.
import type {
  CanonicalAtlasDelta,
  CanonicalAtlasProposedChanges,
  CanonicalAtlasTurnResponse,
} from '@/api/services/atlas.service'

export function allDeltas(proposedChanges: CanonicalAtlasProposedChanges): CanonicalAtlasDelta[] {
  return [
    ...proposedChanges.agent_metadata_deltas,
    ...proposedChanges.scenario_deltas,
    ...proposedChanges.step_deltas,
    ...proposedChanges.scenario_route_deltas,
    ...proposedChanges.channel_policy_deltas,
    ...proposedChanges.rule_deltas,
    ...proposedChanges.knowledge_deltas,
    ...proposedChanges.integration_binding_deltas,
  ]
}

export function mapDeltas(
  proposedChanges: CanonicalAtlasProposedChanges,
  map: (delta: CanonicalAtlasDelta) => CanonicalAtlasDelta,
): CanonicalAtlasProposedChanges {
  return {
    agent_metadata_deltas: proposedChanges.agent_metadata_deltas.map(map),
    scenario_deltas: proposedChanges.scenario_deltas.map(map),
    step_deltas: proposedChanges.step_deltas.map(map),
    scenario_route_deltas: proposedChanges.scenario_route_deltas.map(map),
    channel_policy_deltas: proposedChanges.channel_policy_deltas.map(map),
    rule_deltas: proposedChanges.rule_deltas.map(map),
    knowledge_deltas: proposedChanges.knowledge_deltas.map(map),
    integration_binding_deltas: proposedChanges.integration_binding_deltas.map(map),
  }
}

export function updateDeltaStatuses(
  response: CanonicalAtlasTurnResponse,
  deltaIds: string[],
  status: CanonicalAtlasDelta['status'],
): CanonicalAtlasTurnResponse {
  const deltaIdSet = new Set(deltaIds)
  return {
    ...response,
    proposed_changes: mapDeltas(response.proposed_changes, (delta) => (
      deltaIdSet.has(delta.delta_id)
        ? { ...delta, status }
        : delta
    )),
  }
}

export function hasActionableAtlasResponse(response: CanonicalAtlasTurnResponse): boolean {
  return (
    allDeltas(response.proposed_changes).length > 0 ||
    response.questions.length > 0 ||
    response.blockers.length > 0 ||
    response.dependencies.length > 0 ||
    response.pending_permission_requests.length > 0 ||
    response.provisioning_manifest.length > 0 ||
    response.api_discovery_results.length > 0 ||
    response.validation.blocking ||
    response.validation.errors.length > 0 ||
    response.validation.warnings.length > 0
  )
}

export function renderDeltaLabel(delta: CanonicalAtlasDelta): string {
  return delta.change_type.replace(/_/g, ' ')
}

export function renderProvisioningTarget(delta: CanonicalAtlasDelta): string | null {
  const toolRef = typeof delta.payload.tool_ref === 'string' ? delta.payload.tool_ref : ''
  const connectionName = typeof delta.payload.connection_display_name === 'string' ? delta.payload.connection_display_name : ''
  const displayName = typeof delta.payload.display_name === 'string' ? delta.payload.display_name : ''
  const stepId = typeof delta.payload.target_step_id === 'string' ? delta.payload.target_step_id : ''
  const scenarioId = typeof delta.payload.scenario_id === 'string' ? delta.payload.scenario_id : ''
  return toolRef || connectionName || displayName || stepId || scenarioId || null
}

export function formatShortTimestamp(value?: string | null): string | null {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function titleCase(value: string): string {
  return value.replace(/_/g, ' ')
}

export function summarizeDeltaStatuses(deltas: CanonicalAtlasDelta[]): Record<string, number> {
  return deltas.reduce<Record<string, number>>((current, delta) => {
    current[delta.status] = (current[delta.status] || 0) + 1
    return current
  }, {})
}
