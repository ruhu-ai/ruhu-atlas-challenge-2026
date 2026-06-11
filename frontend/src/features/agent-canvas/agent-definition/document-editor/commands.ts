import type { Editor } from '@tiptap/core'
import type { Node as PMNode } from '@tiptap/pm/model'

// Editor-command helpers used by the hover toolbars.
//
// All write paths go through chained Tiptap commands so the editor's
// undo/redo and onUpdate plumbing stay coherent. Every helper that creates
// a new entity mints a unique short id (mirroring the runtime convention
// in templates, e.g. `t_clarify_back`, `step_discover`, `scenario_main`).

function shortId(prefix: string): string {
  return `${prefix}_${crypto.randomUUID().slice(0, 8)}`
}

export function buildBlankStep(name = 'New step') {
  const stepId = shortId('step')
  return {
    type: 'step' as const,
    attrs: { stepId, isStart: false },
    content: [
      {
        type: 'stepName' as const,
        content: name ? [{ type: 'text' as const, text: name }] : [],
      },
    ],
  }
}

export function buildBlankScenario(name = 'New scenario') {
  const scenarioId = shortId('scenario')
  const blankStep = buildBlankStep('Entry')
  // The first step in a new scenario IS its start step.
  blankStep.attrs.isStart = true
  return {
    type: 'scenario' as const,
    attrs: { scenarioId, isStart: false },
    content: [
      {
        type: 'scenarioName' as const,
        content: name ? [{ type: 'text' as const, text: name }] : [],
      },
      blankStep,
    ],
  }
}

export function buildBlankTransition(toStepId: string, toStepName: string) {
  return {
    type: 'transition' as const,
    attrs: {
      transitionId: shortId('t'),
      whenKind: 'otherwise',
      whenValue: '',
      toStepId,
      toStepName,
      priority: 100,
    },
    content: [],
  }
}

// ─── Add operations ─────────────────────────────────────────────────────

export function addScenarioAtEnd(editor: Editor): void {
  const { state } = editor
  const docSize = state.doc.content.size
  editor.chain().focus().insertContentAt(docSize, buildBlankScenario()).run()
}

export function addStepInScenario(editor: Editor, scenarioPos: number): void {
  const { state } = editor
  const scenarioNode = state.doc.nodeAt(scenarioPos)
  if (!scenarioNode || scenarioNode.type.name !== 'scenario') return
  // Insert right before the closing tag of the scenario node.
  const insertAt = scenarioPos + scenarioNode.nodeSize - 1
  editor.chain().focus().insertContentAt(insertAt, buildBlankStep()).run()
}

export function addTransitionInStep(editor: Editor, stepPos: number): void {
  const { state } = editor
  const stepNode = state.doc.nodeAt(stepPos)
  if (!stepNode || stepNode.type.name !== 'step') return
  // Default: transition routes back to this same step (otherwise → self).
  const stepId = String(stepNode.attrs.stepId ?? '')
  let stepName = ''
  stepNode.content.forEach((child) => {
    if (child.type.name === 'stepName') stepName = child.textContent
  })
  const insertAt = stepPos + stepNode.nodeSize - 1
  editor
    .chain()
    .focus()
    .insertContentAt(insertAt, buildBlankTransition(stepId, stepName || stepId))
    .run()
}

// ─── Delete operations ──────────────────────────────────────────────────

export function deleteNodeAt(editor: Editor, pos: number): void {
  const { state } = editor
  const node = state.doc.nodeAt(pos)
  if (!node) return
  editor
    .chain()
    .focus()
    .deleteRange({ from: pos, to: pos + node.nodeSize })
    .run()
}

// ─── Mark-as-start operations (mutate attrs across siblings) ────────────

export function setScenarioAsStart(editor: Editor, scenarioId: string): void {
  const tr = editor.state.tr
  editor.state.doc.descendants((node, pos) => {
    if (node.type.name !== 'scenario') return false
    const isStart = node.attrs.scenarioId === scenarioId
    if (Boolean(node.attrs.isStart) !== isStart) {
      tr.setNodeAttribute(pos, 'isStart', isStart)
    }
    return false // don't recurse into scenario children
  })
  if (tr.docChanged) editor.view.dispatch(tr)
}

export function setStepAsStartInScenario(
  editor: Editor,
  scenarioPos: number,
  stepId: string,
): void {
  const { state } = editor
  const scenarioNode = state.doc.nodeAt(scenarioPos)
  if (!scenarioNode || scenarioNode.type.name !== 'scenario') return
  const tr = state.tr
  scenarioNode.forEach((child: PMNode, offset: number) => {
    if (child.type.name !== 'step') return
    const childPos = scenarioPos + 1 + offset
    const isStart = child.attrs.stepId === stepId
    if (Boolean(child.attrs.isStart) !== isStart) {
      tr.setNodeAttribute(childPos, 'isStart', isStart)
    }
  })
  if (tr.docChanged) editor.view.dispatch(tr)
}

// ─── Find ancestor scenario position for a given child position ─────────

export function findScenarioPos(editor: Editor, innerPos: number): number | null {
  const { state } = editor
  const $pos = state.doc.resolve(innerPos)
  for (let depth = $pos.depth; depth >= 0; depth--) {
    if ($pos.node(depth).type.name === 'scenario') {
      return $pos.before(depth)
    }
  }
  return null
}

// ─── Step body authoring (Phase 2.3) ────────────────────────────────────
//
// Each step holds at most one sayBlock, one directAnswerBlock, and one
// eventHints container. These helpers detect whether the relevant block
// already exists and only insert when it doesn't.

export function stepHasChild(editor: Editor, stepPos: number, childType: string): boolean {
  const node = editor.state.doc.nodeAt(stepPos)
  if (!node || node.type.name !== 'step') return false
  let found = false
  node.content.forEach((child) => {
    if (child.type.name === childType) found = true
  })
  return found
}

// Insert helpers always place the block right after the stepName and before
// any transitions, so the canonical step body order is:
//   stepName · sayBlock? · directAnswerBlock? · actionSummary? · eventHints? · transition*
function stepBodyInsertPos(editor: Editor, stepPos: number, preferredOffset = 1): number {
  // preferredOffset = 1 means: just after stepName (the first child).
  // Callers can supply a higher offset to insert later in the body.
  const node = editor.state.doc.nodeAt(stepPos)
  if (!node) return stepPos + 1
  let offset = 0
  let inserted = 0
  let landingPos = stepPos + 1 // start right inside the step's content
  node.content.forEach((child, _, idx) => {
    if (idx === preferredOffset) return
    if (idx < preferredOffset) {
      offset += child.nodeSize
      inserted = idx + 1
      landingPos = stepPos + 1 + offset
    }
  })
  // Fallback: if preferredOffset overshoots, insert before the closing tag.
  if (inserted < preferredOffset) {
    return stepPos + node.nodeSize - 1
  }
  return landingPos
}

export function addSayInStep(editor: Editor, stepPos: number): void {
  if (stepHasChild(editor, stepPos, 'sayBlock')) return
  const insertAt = stepBodyInsertPos(editor, stepPos, 1)
  editor
    .chain()
    .focus()
    .insertContentAt(insertAt, { type: 'sayBlock', content: [] })
    .run()
}

export function addDirectAnswerInStep(editor: Editor, stepPos: number): void {
  if (stepHasChild(editor, stepPos, 'directAnswerBlock')) return
  // Insert after sayBlock if present, else right after stepName.
  const node = editor.state.doc.nodeAt(stepPos)
  if (!node) return
  let offset = node.firstChild?.nodeSize ?? 0 // skip stepName
  if (node.maybeChild(1)?.type.name === 'sayBlock') {
    offset += node.maybeChild(1)!.nodeSize
  }
  const insertAt = stepPos + 1 + offset
  editor
    .chain()
    .focus()
    .insertContentAt(insertAt, { type: 'directAnswerBlock', content: [] })
    .run()
}

function shortHintKey(prefix = 'new_intent'): string {
  return `${prefix}_${crypto.randomUUID().slice(0, 6)}`
}

// Rename a hint's key, propagating the change to every transition in the
// document whose `whenKind=event` and `whenValue=intent_detected:<oldKey>`.
// All updates land in a single transaction so the editor's undo stack treats
// this as one operation.
export function renameEventHint(
  editor: Editor,
  hintNodePos: number,
  oldKey: string,
  newKey: string,
): { ok: boolean; reason?: string } {
  if (newKey === oldKey) return { ok: true }
  if (!newKey.trim()) return { ok: false, reason: 'Hint key cannot be empty' }
  // Reject collisions with other hints at any scope.
  let collision = false
  editor.state.doc.descendants((node, pos) => {
    if (
      node.type.name === 'eventHint'
      && node.attrs?.hintKey === newKey
      && pos !== hintNodePos
    ) {
      collision = true
      return false
    }
    return true
  })
  if (collision) return { ok: false, reason: `Hint @${newKey} already exists` }

  const oldEventValue = `intent_detected:${oldKey}`
  const newEventValue = `intent_detected:${newKey}`
  const tr = editor.state.tr
  tr.setNodeAttribute(hintNodePos, 'hintKey', newKey)
  editor.state.doc.descendants((node, pos) => {
    if (
      node.type.name === 'transition'
      && node.attrs?.whenKind === 'event'
      && node.attrs?.whenValue === oldEventValue
    ) {
      tr.setNodeAttribute(pos, 'whenValue', newEventValue)
    }
    return true
  })
  editor.view.dispatch(tr)
  return { ok: true }
}

// Insert an empty ``toolBinding`` chip into the step's ``toolPolicy``
// block — creating the block when none exists. The chip starts in
// "pick a callable" state and the ToolBindingNodeView renders a
// Library picker until the ``ref`` attr is filled. Mirrors the
// addEventHintToStep pattern: create container if missing, otherwise
// append to existing container.
export function addToolBindingToStep(editor: Editor, stepPos: number): void {
  const node = editor.state.doc.nodeAt(stepPos)
  if (!node || node.type.name !== 'step') return

  // Find an existing toolPolicy block to append to.
  let policyPos: number | null = null
  let policyNode: { nodeSize: number } | null = null
  let cursor = stepPos + 1
  node.content.forEach((child) => {
    if (child.type.name === 'toolPolicy') {
      policyPos = cursor
      policyNode = child as { nodeSize: number }
    }
    cursor += child.nodeSize
  })

  const newBinding = {
    type: 'toolBinding',
    attrs: { ref: '', mode: 'allowed' },
  }

  if (policyPos != null && policyNode != null) {
    const insertAt = policyPos + (policyNode as { nodeSize: number }).nodeSize - 1
    editor.chain().focus().insertContentAt(insertAt, newBinding).run()
    return
  }

  // Fresh toolPolicy block. Place it after eventHints / actionSummary
  // / directAnswerBlock / sayBlock but before transitions, matching
  // the canonical step body order:
  //   stepName · sayBlock? · directAnswerBlock? · actionSummary? ·
  //     toolPolicy? · eventHints? · transition*
  let firstTransitionIdx = node.childCount
  node.content.forEach((child, _, idx) => {
    if (child.type.name === 'transition' && idx < firstTransitionIdx) {
      firstTransitionIdx = idx
    }
  })
  let offset = 0
  for (let i = 0; i < firstTransitionIdx; i++) {
    offset += node.maybeChild(i)?.nodeSize ?? 0
  }
  const insertAt = stepPos + 1 + offset
  editor
    .chain()
    .focus()
    .insertContentAt(insertAt, {
      type: 'toolPolicy',
      content: [newBinding],
    })
    .run()
}

export function addEventHintToStep(editor: Editor, stepPos: number): void {
  const node = editor.state.doc.nodeAt(stepPos)
  if (!node || node.type.name !== 'step') return

  // If an eventHints block exists, append a new eventHint to it.
  let hintsPos: number | null = null
  let hintsNode = null
  let cursor = stepPos + 1
  node.content.forEach((child) => {
    if (child.type.name === 'eventHints') {
      hintsPos = cursor
      hintsNode = child
    }
    cursor += child.nodeSize
  })

  const newHint = {
    type: 'eventHint',
    attrs: { hintKey: shortHintKey() },
    content: [],
  }

  if (hintsPos != null && hintsNode != null) {
    const insertAt = hintsPos + (hintsNode as { nodeSize: number }).nodeSize - 1
    editor.chain().focus().insertContentAt(insertAt, newHint).run()
    return
  }

  // Otherwise, create a new eventHints container with a single hint.
  // Place it after sayBlock / directAnswerBlock / actionSummary, before
  // transitions.
  let offset = 0
  let firstTransitionIdx = node.childCount
  node.content.forEach((child, _, idx) => {
    if (child.type.name === 'transition' && idx < firstTransitionIdx) {
      firstTransitionIdx = idx
    }
  })
  for (let i = 0; i < firstTransitionIdx; i++) {
    offset += node.maybeChild(i)?.nodeSize ?? 0
  }
  const insertAt = stepPos + 1 + offset
  editor
    .chain()
    .focus()
    .insertContentAt(insertAt, {
      type: 'eventHints',
      content: [newHint],
    })
    .run()
}
