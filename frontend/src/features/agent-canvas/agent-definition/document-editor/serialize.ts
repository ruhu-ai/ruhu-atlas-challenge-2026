import type {
  AgentDocument,
  AgentScenario,
  AgentStep,
  AgentStepTransition,
} from '@/types/agent-document'

// Structured action-config summary. The chips render as clickable
// ``Link``s to the matching Library entry (?callable_id=<ref>), so an
// author working on a step can jump straight from the step body to
// the tool's detail page. Plain-text fallback is preserved for empty
// states and for backward compat with serialized docs that haven't
// been re-saved yet.
interface ActionSummaryAttrs {
  summary: string
  toolRefs: string[]
  apiRefs: string[]
  integrationRefs: string[]
}

function summarizeAction(step: AgentStep): ActionSummaryAttrs | null {
  const action = step.action_config
  if (!action) return null
  const toolRefs = (Array.isArray(action.callable_system_refs) ? action.callable_system_refs : [])
    .filter((ref): ref is string => typeof ref === 'string' && ref.trim() !== '')
  const integrationRefs = (Array.isArray(action.callable_integrations) ? action.callable_integrations : [])
    .filter((ref): ref is string => typeof ref === 'string' && ref.trim() !== '')
  const apiRefs = (Array.isArray(action.callable_api_refs) ? action.callable_api_refs : [])
    .filter((ref): ref is string => typeof ref === 'string' && ref.trim() !== '')
  const parts: string[] = []
  if (toolRefs.length > 0) parts.push(`tools: ${toolRefs.join(', ')}`)
  if (integrationRefs.length > 0) parts.push(`integrations: ${integrationRefs.join(', ')}`)
  if (apiRefs.length > 0) parts.push(`apis: ${apiRefs.join(', ')}`)
  const summary = parts.length > 0 ? `Runs Python code · ${parts.join(' · ')}` : 'Runs Python code'
  return { summary, toolRefs, apiRefs, integrationRefs }
}

interface TextNode {
  type: 'text'
  text: string
}

interface BlockNode {
  type: string
  attrs?: Record<string, unknown>
  content?: Array<BlockNode | TextNode>
}

function textOrEmpty(text: string | null | undefined): TextNode[] {
  const value = (text ?? '').trim()
  return value.length > 0 ? [{ type: 'text', text: value }] : []
}

// Pull the kind-specific identifier off a Condition. The picker
// surfaces this single string field as the "Event token" / "Fact name"
// / "Outcome code" / "Guard id" input depending on kind. Returns ``''``
// for the no-payload kinds (otherwise / all_required_facts_present).
function primaryValueForCondition(condition: AgentStepTransition['when'] | undefined): string {
  if (!condition) return ''
  switch (condition.kind) {
    case 'outcome':
      return condition.event ?? ''
    case 'fact_present':
    case 'fact_missing':
    case 'fact_equals':
      return condition.fact_name ?? ''
    case 'tool_outcome':
      return condition.outcome ?? ''
    case 'guard_failure':
      return condition.guard_id ?? ''
    default:
      return ''
  }
}

function descriptionForCondition(condition: AgentStepTransition['when'] | undefined): string {
  if (!condition || condition.kind !== 'outcome') return ''
  return condition.description ?? ''
}

function transitionNode(
  transition: AgentStepTransition,
  toStepName: string,
): BlockNode {
  return {
    type: 'transition',
    attrs: {
      transitionId: transition.id,
      whenKind: transition.when?.kind ?? 'otherwise',
      whenValue: primaryValueForCondition(transition.when),
      whenDescription: descriptionForCondition(transition.when),
      toStepId: transition.to_step_id ?? '',
      toStepName,
      priority: transition.priority ?? 100,
    },
    content: textOrEmpty(transition.label ?? ''),
  }
}

function stepNode(step: AgentStep, scenario: AgentScenario): BlockNode {
  const stepNamesById = new Map(scenario.steps.map((s) => [s.id, s.name || s.id]))
  const body: BlockNode[] = []

  if (step.say && step.say.trim().length > 0) {
    body.push({ type: 'sayBlock', content: textOrEmpty(step.say) })
  }

  const directAnswer = step.response_policy?.direct_answer_prompt
  if (directAnswer && directAnswer.trim().length > 0) {
    body.push({ type: 'directAnswerBlock', content: textOrEmpty(directAnswer) })
  }

  const actionSummary = summarizeAction(step)
  if (actionSummary) {
    body.push({
      type: 'actionSummary',
      attrs: {
        summary: actionSummary.summary,
        toolRefs: actionSummary.toolRefs,
        apiRefs: actionSummary.apiRefs,
        integrationRefs: actionSummary.integrationRefs,
      },
    })
  }

  // tool_policy bindings — Library callables this step is allowed
  // to invoke. Rendered as clickable chips with delete affordance;
  // round-trips through deserialize.ts:editorJSONToDocument so the
  // structural data on the step survives editor save.
  const toolPolicy = step.tool_policy ?? []
  if (toolPolicy.length > 0) {
    body.push({
      type: 'toolPolicy',
      content: toolPolicy.map((binding) => ({
        type: 'toolBinding',
        attrs: {
          ref: binding.ref ?? '',
          mode: binding.mode ?? 'allowed',
        },
      })),
    })
  }

  const hints = Object.entries(step.event_hints ?? {})
  if (hints.length > 0) {
    body.push({
      type: 'eventHints',
      content: hints.map(([key, description]) => ({
        type: 'eventHint',
        attrs: { hintKey: key },
        content: textOrEmpty(description),
      })),
    })
  }

  for (const transition of step.transitions) {
    body.push(
      transitionNode(transition, stepNamesById.get(transition.to_step_id) ?? transition.to_step_id),
    )
  }

  return {
    type: 'step',
    attrs: {
      stepId: step.id,
      isStart: step.id === scenario.start_step_id,
    },
    content: [
      {
        type: 'stepName',
        content: textOrEmpty(step.name || step.id),
      },
      ...body,
    ],
  }
}

function scenarioNode(
  scenario: AgentScenario,
  startScenarioId: string | null,
): BlockNode {
  return {
    type: 'scenario',
    attrs: {
      scenarioId: scenario.id,
      isStart: scenario.id === startScenarioId,
    },
    content: [
      {
        type: 'scenarioName',
        content: textOrEmpty(scenario.name || scenario.id),
      },
      ...scenario.steps.map((step) => stepNode(step, scenario)),
    ],
  }
}

export function documentToEditorJSON(doc: AgentDocument): BlockNode {
  return {
    type: 'doc',
    content: doc.scenarios.map((scenario) =>
      scenarioNode(scenario, doc.start_scenario_id ?? null),
    ),
  }
}
