import type { AgentStep } from '@/types/agent-document'

export interface SidebarStepItem {
  id: string
  name: string
  summary: string
}

export function describeSidebarStep(step: Pick<AgentStep, 'action_config' | 'completion' | 'handoff' | 'fact_requirements'>): string {
  if (step.completion) return 'resolves the request'
  if (step.handoff) return 'routes to another destination'
  if (step.action_config) return 'runs code or tools'
  if ((step.fact_requirements?.length ?? 0) > 0) return 'waits for required details'
  return 'responds in place'
}
