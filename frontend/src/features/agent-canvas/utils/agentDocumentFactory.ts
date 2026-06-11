import type { AgentDocument, AgentScenario, AgentStep } from '@/types/agent-document'

const uuidv4 = () => crypto.randomUUID()

export function createStep(index: number): AgentStep {
  return {
    id: `step_${uuidv4()}`,
    name: `Step ${index + 1}`,
    transitions: [],
    say: '',
    description: '',
    fact_requirements: [],
    tool_policy: [],
    event_hints: {},
  }
}

function normalizeStep(step: AgentStep): AgentStep {
  return {
    ...step,
    transitions: (step.transitions ?? []).map((transition, index) => ({
      ...transition,
      priority: transition.priority ?? (index + 1) * 100,
    })),
    fact_requirements: step.fact_requirements ?? [],
    tool_policy: step.tool_policy ?? [],
    event_hints: step.event_hints ?? {},
  }
}

export function createScenario(index: number): AgentScenario {
  const firstStep = createStep(0)
  firstStep.name = 'Start'
  return {
    id: `scenario_${uuidv4()}`,
    name: `Scenario ${index + 1}`,
    start_step_id: firstStep.id,
    summary: '',
    order: index,
    steps: [firstStep],
    resources: {},
  }
}

export function createDefaultAgentDocument(): AgentDocument {
  const scenario = createScenario(0)
  return {
    version: '3.0',
    start_scenario_id: scenario.id,
    scenarios: [scenario],
    scenario_routes: [],
    fact_schema: [],
    agent_capability_manifest: {
      assistant_identity: '',
      capabilities: [],
      limitations: [],
    },
    metadata: {},
  }
}

export function normalizeDocument(document: AgentDocument): AgentDocument {
  if (document.scenarios.length === 0) return createDefaultAgentDocument()
  const normalizedScenarios = document.scenarios.map((scenario, index) => ({
    ...scenario,
    order: scenario.order ?? index,
    entry_channels: scenario.entry_channels ?? [],
    steps: (scenario.steps.length > 0 ? scenario.steps : [createStep(0)]).map(normalizeStep),
  }))
  const normalizedDocument: AgentDocument = {
    ...document,
    scenarios: normalizedScenarios.map((scenario) => ({
      ...scenario,
      start_step_id: scenario.steps.some((step) => step.id === scenario.start_step_id)
        ? scenario.start_step_id
        : scenario.steps[0].id,
    })),
    scenario_routes: document.scenario_routes ?? [],
  }
  if (normalizedDocument.scenarios.some((scenario) => scenario.id === normalizedDocument.start_scenario_id)) {
    return normalizedDocument
  }
  return {
    ...normalizedDocument,
    start_scenario_id: normalizedDocument.scenarios[0].id,
  }
}

export function deriveStartScenarioId(document: AgentDocument): string | null {
  return document.scenarios.some((scenario) => scenario.id === document.start_scenario_id)
    ? document.start_scenario_id
    : document.scenarios[0]?.id ?? null
}
