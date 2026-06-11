import type { JSONContent } from '@tiptap/core'
import type {
  AgentDocument,
  AgentScenario,
  AgentStep,
  AgentStepCondition,
  AgentStepTransition,
} from '@/types/agent-document'

// Build the right Condition union member from the editor's flat
// (kind, value, description) attrs. Mirrors the backend Pydantic
// discriminated union — each kind owns a specific field name, NOT a
// generic ``value``. ``value`` lingers as an optional alias for
// callers that still read it (e.g. ScenarioLanesCanvas) but new
// authoring populates the kind-specific field.
function buildCondition(
  kind: string,
  rawValue: string,
  rawDescription: string,
): AgentStepCondition {
  const value = rawValue.trim()
  const description = rawDescription.trim()
  switch (kind) {
    case 'outcome':
      return {
        kind: 'outcome',
        event: value,
        description,
      }
    case 'tool_outcome':
      return { kind: 'tool_outcome', outcome: value }
    case 'fact_present':
      return { kind: 'fact_present', fact_name: value }
    case 'fact_missing':
      return { kind: 'fact_missing', fact_name: value }
    case 'fact_equals':
      return { kind: 'fact_equals', fact_name: value, value: '' }
    case 'guard_failure':
      return { kind: 'guard_failure', guard_id: value }
    case 'all_required_facts_present':
      return { kind: 'all_required_facts_present' }
    case 'attachment_present':
      return { kind: 'attachment_present' }
    case 'view_ready':
      return { kind: 'view_ready' }
    case 'otherwise':
    default:
      return { kind: 'otherwise' }
  }
}

// Phase 1 round-trip strategy:
//
// The editor only exposes a subset of AgentDocument fields (names, say,
// direct_answer, event hints, transition labels). Everything else
// (action_config, tool_policy, response_policy advanced flags, fact_schema,
// metadata, scenario_routes, attachment conditions, etc.) is preserved
// verbatim from the originally-loaded document.
//
// To do that we keep a copy of the original AgentDocument indexed by
// scenario id and step id, then merge the user's text edits on top of it.
// Structural changes (add/remove scenarios, steps, transitions) are
// out of scope for Phase 1 and are deferred to Phase 2 — but the merger
// still tolerates the editor doc producing a different shape than the
// original by treating missing entries as authored insertions/deletions.

interface BaselineIndex {
  scenarioById: Map<string, AgentScenario>
  stepByPath: Map<string, AgentStep>
  transitionByPath: Map<string, AgentStepTransition>
}

function buildBaselineIndex(doc: AgentDocument): BaselineIndex {
  const scenarioById = new Map<string, AgentScenario>()
  const stepByPath = new Map<string, AgentStep>()
  const transitionByPath = new Map<string, AgentStepTransition>()
  for (const scenario of doc.scenarios) {
    scenarioById.set(scenario.id, scenario)
    for (const step of scenario.steps) {
      stepByPath.set(`${scenario.id}/${step.id}`, step)
      for (const transition of step.transitions) {
        transitionByPath.set(
          `${scenario.id}/${step.id}/${transition.id}`,
          transition,
        )
      }
    }
  }
  return { scenarioById, stepByPath, transitionByPath }
}

function nodeText(node: JSONContent | undefined): string {
  if (!node || !Array.isArray(node.content)) return ''
  let out = ''
  for (const child of node.content) {
    if (child.type === 'text' && typeof child.text === 'string') {
      out += child.text
    }
  }
  return out
}

function findChild(node: JSONContent, type: string): JSONContent | undefined {
  if (!Array.isArray(node.content)) return undefined
  return node.content.find((child) => child.type === type)
}

function filterChildren(node: JSONContent, type: string): JSONContent[] {
  if (!Array.isArray(node.content)) return []
  return node.content.filter((child) => child.type === type)
}

export function editorJSONToDocument(
  editorDoc: JSONContent,
  baseline: AgentDocument,
): AgentDocument {
  const idx = buildBaselineIndex(baseline)
  const seenScenarioIds = new Set<string>()

  const scenarios: AgentScenario[] = []

  for (const scenarioNode of filterChildren(editorDoc, 'scenario')) {
    const scenarioId = String(scenarioNode.attrs?.scenarioId ?? '')
    if (!scenarioId) continue
    seenScenarioIds.add(scenarioId)
    const baseScenario = idx.scenarioById.get(scenarioId)
    const editedName = nodeText(findChild(scenarioNode, 'scenarioName')).trim()

    const seenStepIds = new Set<string>()
    const steps: AgentStep[] = []

    for (const stepNode of filterChildren(scenarioNode, 'step')) {
      const stepId = String(stepNode.attrs?.stepId ?? '')
      if (!stepId) continue
      seenStepIds.add(stepId)
      const baseStep = idx.stepByPath.get(`${scenarioId}/${stepId}`)
      const editedStepName = nodeText(findChild(stepNode, 'stepName')).trim()

      const sayText = nodeText(findChild(stepNode, 'sayBlock'))
      const directAnswerText = nodeText(findChild(stepNode, 'directAnswerBlock'))

      const eventHintsBlock = findChild(stepNode, 'eventHints')
      const editedHints: Record<string, string> = {}
      if (eventHintsBlock) {
        for (const hintNode of filterChildren(eventHintsBlock, 'eventHint')) {
          const key = String(hintNode.attrs?.hintKey ?? '').trim()
          if (!key) continue
          editedHints[key] = nodeText(hintNode).trim()
        }
      }

      // Tool policy round-trip: rebuild the step's tool_policy from the
      // ``toolPolicy`` block's ``toolBinding`` chips. Bindings the user
      // hasn't touched (no toolPolicy block in the editor doc — empty
      // case) fall back to the baseline so we never silently drop them.
      const toolPolicyBlock = findChild(stepNode, 'toolPolicy')
      let editedToolPolicy: AgentStep['tool_policy'] | null = null
      if (toolPolicyBlock) {
        editedToolPolicy = []
        for (const bindingNode of filterChildren(toolPolicyBlock, 'toolBinding')) {
          const ref = String(bindingNode.attrs?.ref ?? '').trim()
          if (!ref) continue
          const mode = String(bindingNode.attrs?.mode ?? 'allowed')
          editedToolPolicy.push({ ref, mode })
        }
      }

      const seenTransitionIds = new Set<string>()
      const transitions: AgentStepTransition[] = []
      for (const transitionNode of filterChildren(stepNode, 'transition')) {
        const transitionId = String(transitionNode.attrs?.transitionId ?? '')
        if (!transitionId) continue
        seenTransitionIds.add(transitionId)
        const baseTransition = idx.transitionByPath.get(
          `${scenarioId}/${stepId}/${transitionId}`,
        )
        const editedLabel = nodeText(transitionNode).trim()
        if (baseTransition) {
          // Editor attrs are authoritative for structural fields so changes
          // through the condition picker (Phase 2.2) propagate. Fall back
          // to the baseline value when an attr is empty/missing.
          const editorWhenKind = String(transitionNode.attrs?.whenKind ?? '')
          const editorWhenValue = String(transitionNode.attrs?.whenValue ?? '')
          const editorWhenDescription = String(transitionNode.attrs?.whenDescription ?? '')
          const editorToStepId = String(transitionNode.attrs?.toStepId ?? '')
          transitions.push({
            ...baseTransition,
            when: editorWhenKind
              ? buildCondition(editorWhenKind, editorWhenValue, editorWhenDescription)
              : baseTransition.when,
            to_step_id: editorToStepId || baseTransition.to_step_id,
            label: editedLabel.length > 0 ? editedLabel : null,
            priority: Number(transitionNode.attrs?.priority ?? baseTransition.priority ?? 100),
          })
        } else {
          // Authored fresh in this session.
          transitions.push({
            id: transitionId,
            when: buildCondition(
              String(transitionNode.attrs?.whenKind ?? 'otherwise'),
              String(transitionNode.attrs?.whenValue ?? ''),
              String(transitionNode.attrs?.whenDescription ?? ''),
            ),
            to_step_id: String(transitionNode.attrs?.toStepId ?? ''),
            label: editedLabel.length > 0 ? editedLabel : null,
            priority: Number(transitionNode.attrs?.priority ?? 100),
          })
        }
      }
      // Append any baseline transitions the editor didn't render so we
      // never silently lose data. (Won't happen in normal Phase 1 flow.)
      if (baseStep) {
        for (const baseTransition of baseStep.transitions) {
          if (!seenTransitionIds.has(baseTransition.id)) {
            transitions.push(baseTransition)
          }
        }
      }

      // Re-derive priorities from editor-doc order so drag-to-reorder
      // affects kernel evaluation order. Otherwise transitions are pinned
      // last regardless of authored position.
      const ordered = [
        ...transitions.filter((t) => t.when.kind !== 'otherwise'),
        ...transitions.filter((t) => t.when.kind === 'otherwise'),
      ]
      const orderedTransitions = ordered.map((t, idx) => ({
        ...t,
        priority: (idx + 1) * 100,
      }))

      // Build the step, preserving baseline fields the editor doesn't expose.
      const resolvedToolPolicy = editedToolPolicy ?? baseStep?.tool_policy ?? []
      const merged: AgentStep = baseStep
        ? {
            ...baseStep,
            name: editedStepName.length > 0 ? editedStepName : baseStep.name,
            say: sayText.trim().length > 0 ? sayText : null,
            response_policy: {
              ...(baseStep.response_policy ?? {}),
              direct_answer_prompt:
                directAnswerText.trim().length > 0 ? directAnswerText : null,
            },
            event_hints: editedHints,
            tool_policy: resolvedToolPolicy,
            transitions: orderedTransitions,
          }
        : {
            id: stepId,
            name: editedStepName,
            transitions: orderedTransitions,
            say: sayText.trim().length > 0 ? sayText : null,
            response_policy: {
              direct_answer_prompt:
                directAnswerText.trim().length > 0 ? directAnswerText : null,
            },
            event_hints: editedHints,
            tool_policy: resolvedToolPolicy,
          }
      steps.push(merged)
    }

    // Append any baseline steps the editor didn't render (Phase-2 safety net).
    if (baseScenario) {
      for (const baseStep of baseScenario.steps) {
        if (!seenStepIds.has(baseStep.id)) {
          steps.push(baseStep)
        }
      }
    }

    // Per-scenario start step: prefer the step whose isStart attr is true.
    let startStepId =
      steps.find((step) => {
        const stepNode = filterChildren(scenarioNode, 'step').find(
          (node) => node.attrs?.stepId === step.id,
        )
        return Boolean(stepNode?.attrs?.isStart)
      })?.id
    if (!startStepId) {
      startStepId = baseScenario?.start_step_id ?? steps[0]?.id ?? ''
    }

    const merged: AgentScenario = baseScenario
      ? {
          ...baseScenario,
          name: editedName.length > 0 ? editedName : baseScenario.name,
          steps,
          start_step_id: startStepId,
        }
      : {
          id: scenarioId,
          name: editedName,
          start_step_id: startStepId,
          steps,
        }
    scenarios.push(merged)
  }

  // Append any baseline scenarios the editor didn't render.
  for (const baseScenario of baseline.scenarios) {
    if (!seenScenarioIds.has(baseScenario.id)) {
      scenarios.push(baseScenario)
    }
  }

  // Document-level start scenario: prefer the editor's isStart attr.
  let startScenarioId = baseline.start_scenario_id
  for (const scenarioNode of filterChildren(editorDoc, 'scenario')) {
    if (scenarioNode.attrs?.isStart) {
      startScenarioId = String(scenarioNode.attrs?.scenarioId ?? '')
    }
  }
  if (!startScenarioId && scenarios.length > 0) {
    startScenarioId = scenarios[0].id
  }

  return {
    ...baseline,
    scenarios,
    start_scenario_id: startScenarioId,
  }
}
