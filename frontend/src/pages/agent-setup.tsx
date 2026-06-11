/**
 * Agent setup page — post-clone onboarding checklist.
 *
 * Route: /agents/:id/setup?template=<template_id>
 *
 * The `template` query param carries provenance from the clone-time
 * navigation. When absent (e.g. the user navigated to /setup later
 * from a bookmark or by typing the URL), we fall back to
 * `agent_settings.source_template_id` from agent settings, which is
 * also set at clone time.
 */
import { useParams, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'

import { SetupChecklist } from '@/features/agent-canvas/components/SetupChecklist'
import { agentDefinitionService } from '@/api/services/agent-definition.service'

export default function AgentSetupPage() {
  const { id: agentId } = useParams<{ id: string }>()
  const [searchParams] = useSearchParams()
  const templateIdFromQuery = searchParams.get('template')

  const { data: agentResponse } = useQuery({
    queryKey: ['agent', agentId, 'draft'],
    queryFn: () => (agentId ? agentDefinitionService.getAgent(agentId, 'draft') : null),
    enabled: Boolean(agentId),
  })
  const agentName = agentResponse?.agent_name

  // Fallback provenance lookup — when ?template= is absent, read
  // source_template_id from agent settings (set at clone time).
  const { data: settingsResponse, isLoading: settingsLoading } = useQuery({
    queryKey: ['agent-settings', agentId],
    queryFn: () => (agentId ? agentDefinitionService.getAgentSettings(agentId) : null),
    enabled: Boolean(agentId) && !templateIdFromQuery,
  })
  const templateIdFromSettings = settingsResponse?.settings?.source_template_id ?? null
  const templateId = templateIdFromQuery || templateIdFromSettings

  if (!agentId) return null

  return (
    <SetupChecklist
      agentId={agentId}
      agentName={agentName}
      templateId={templateId}
      provenanceLoading={!templateIdFromQuery && settingsLoading}
    />
  )
}
