import { Node, mergeAttributes } from '@tiptap/core'
import { ReactNodeViewRenderer } from '@tiptap/react'
import { EventHintsNodeView } from './EventHintsNodeView'
import { EventHintNodeView } from './EventHintNodeView'
import {
  ActionSummaryNodeView,
  ScenarioNodeView,
  StepNodeView,
  ToolBindingNodeView,
  ToolPolicyNodeView,
  TransitionNodeView,
} from './node-views'

// Schema for the Ruhu document editor.
//
// Hierarchy:
//   doc
//   └─ scenario+
//      ├─ scenarioName
//      └─ step+
//         ├─ stepName
//         └─ (sayBlock | directAnswerBlock | actionSummary | eventHints | transition)*
//
// Most nodes use React NodeViews for the surrounding chrome (icons, badges,
// pills) and expose their editable text via NodeViewContent.

export const Scenario = Node.create({
  name: 'scenario',
  group: 'block',
  content: 'scenarioName step+',
  defining: true,
  isolating: true,
  addAttributes() {
    return {
      scenarioId: { default: '' },
      isStart: { default: false },
    }
  },
  parseHTML() {
    return [{ tag: 'section[data-scenario]' }]
  },
  renderHTML({ HTMLAttributes }) {
    return ['section', mergeAttributes(HTMLAttributes, { 'data-scenario': '' }), 0]
  },
  addNodeView() {
    return ReactNodeViewRenderer(ScenarioNodeView)
  },
})

export const ScenarioName = Node.create({
  name: 'scenarioName',
  content: 'text*',
  defining: true,
  // Names are inline-editable but not draggable/splittable.
  marks: '',
  parseHTML() {
    return [{ tag: 'h1[data-scenario-name]' }]
  },
  renderHTML({ HTMLAttributes }) {
    return ['h1', mergeAttributes(HTMLAttributes, { 'data-scenario-name': '' }), 0]
  },
})

export const Step = Node.create({
  name: 'step',
  group: 'block',
  content: 'stepName stepBody*',
  defining: true,
  isolating: true,
  addAttributes() {
    return {
      stepId: { default: '' },
      isStart: { default: false },
    }
  },
  parseHTML() {
    return [{ tag: 'div[data-step]' }]
  },
  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes, { 'data-step': '' }), 0]
  },
  addNodeView() {
    return ReactNodeViewRenderer(StepNodeView)
  },
})

export const StepName = Node.create({
  name: 'stepName',
  content: 'text*',
  defining: true,
  marks: '',
  parseHTML() {
    return [{ tag: 'h2[data-step-name]' }]
  },
  renderHTML({ HTMLAttributes }) {
    return ['h2', mergeAttributes(HTMLAttributes, { 'data-step-name': '' }), 0]
  },
})

export const SayBlock = Node.create({
  name: 'sayBlock',
  group: 'stepBody',
  content: 'text*',
  defining: true,
  marks: '',
  parseHTML() {
    return [{ tag: 'div[data-say-block]' }]
  },
  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes, { 'data-say-block': '' }), 0]
  },
})

export const DirectAnswerBlock = Node.create({
  name: 'directAnswerBlock',
  group: 'stepBody',
  content: 'text*',
  defining: true,
  marks: '',
  parseHTML() {
    return [{ tag: 'div[data-direct-answer]' }]
  },
  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes, { 'data-direct-answer': '' }), 0]
  },
})

// Action summary — read-only summary of action_config metadata
// (Python code body itself is authored elsewhere). The node-view
// renders the typed ref arrays as clickable chips that deep-link to
// the matching Library entry, so an author can jump straight from a
// step's action block to the tool's detail page.
export const ActionSummary = Node.create({
  name: 'actionSummary',
  group: 'stepBody',
  atom: true,
  defining: true,
  selectable: false,
  draggable: false,
  addAttributes() {
    return {
      summary: { default: '' },
      // Library callable refs the action_config can invoke. Each
      // becomes a clickable chip in the node-view.
      toolRefs: { default: [] as string[] },
      apiRefs: { default: [] as string[] },
      integrationRefs: { default: [] as string[] },
    }
  },
  parseHTML() {
    return [{ tag: 'div[data-action-summary]' }]
  },
  renderHTML({ node, HTMLAttributes }) {
    return [
      'div',
      mergeAttributes(HTMLAttributes, {
        'data-action-summary': '',
        'data-summary': String(node.attrs.summary ?? ''),
        'data-tool-refs': JSON.stringify(node.attrs.toolRefs ?? []),
        'data-api-refs': JSON.stringify(node.attrs.apiRefs ?? []),
        'data-integration-refs': JSON.stringify(node.attrs.integrationRefs ?? []),
      }),
    ]
  },
  addNodeView() {
    return ReactNodeViewRenderer(ActionSummaryNodeView)
  },
})

// Tool-policy block — wraps the per-step list of tool bindings the
// agent is allowed to invoke from this step. One ``toolBinding`` chip
// per ``tool_policy[]`` entry. The block exists as a separate group
// from ActionSummary because tool_policy is a distinct authoring
// concern (declarative bindings) from action_config (inline Python
// code that calls tools).
export const ToolPolicy = Node.create({
  name: 'toolPolicy',
  group: 'stepBody',
  content: 'toolBinding+',
  defining: true,
  isolating: true,
  parseHTML() {
    return [{ tag: 'div[data-tool-policy]' }]
  },
  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes, { 'data-tool-policy': '' }), 0]
  },
  addNodeView() {
    return ReactNodeViewRenderer(ToolPolicyNodeView)
  },
})

// One callable binding inside a ``toolPolicy`` block. Rendered as a
// clickable chip in the node-view: clicking the ref jumps to the
// Library entry; the trailing × removes the binding.
export const ToolBinding = Node.create({
  name: 'toolBinding',
  atom: true,
  selectable: false,
  draggable: false,
  addAttributes() {
    return {
      ref: { default: '' },
      mode: { default: 'allowed' },
    }
  },
  parseHTML() {
    return [{ tag: 'div[data-tool-binding]' }]
  },
  renderHTML({ node, HTMLAttributes }) {
    return [
      'div',
      mergeAttributes(HTMLAttributes, {
        'data-tool-binding': '',
        'data-ref': String(node.attrs.ref ?? ''),
        'data-mode': String(node.attrs.mode ?? 'allowed'),
      }),
    ]
  },
  addNodeView() {
    return ReactNodeViewRenderer(ToolBindingNodeView)
  },
})

export const EventHints = Node.create({
  name: 'eventHints',
  group: 'stepBody',
  content: 'eventHint+',
  defining: true,
  isolating: true,
  parseHTML() {
    return [{ tag: 'div[data-event-hints]' }]
  },
  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes, { 'data-event-hints': '' }), 0]
  },
  addNodeView() {
    return ReactNodeViewRenderer(EventHintsNodeView)
  },
})

export const EventHint = Node.create({
  name: 'eventHint',
  content: 'text*',
  defining: true,
  marks: '',
  addAttributes() {
    return {
      hintKey: { default: '' },
    }
  },
  parseHTML() {
    return [{ tag: 'div[data-event-hint]' }]
  },
  renderHTML({ node, HTMLAttributes }) {
    return [
      'div',
      mergeAttributes(HTMLAttributes, {
        'data-event-hint': '',
        'data-hint-key': String(node.attrs.hintKey ?? ''),
      }),
      0,
    ]
  },
  addNodeView() {
    return ReactNodeViewRenderer(EventHintNodeView)
  },
})

export const Transition = Node.create({
  name: 'transition',
  group: 'stepBody',
  content: 'text*',
  defining: true,
  marks: '',
  draggable: true,
  addAttributes() {
    return {
      transitionId: { default: '' },
      whenKind: { default: 'otherwise' },
      // Primary identifier — kind-specific:
      //   outcome → event, fact_present/fact_missing → fact_name,
      //   tool_outcome → outcome, guard_failure → guard_id,
      //   otherwise / all_required_facts_present → '' (unused).
      whenValue: { default: '' },
      // OutcomeCondition.description — only populated when whenKind = 'outcome'.
      whenDescription: { default: '' },
      toStepId: { default: '' },
      toStepName: { default: '' },
      priority: { default: 100 },
    }
  },
  parseHTML() {
    return [{ tag: 'div[data-transition]' }]
  },
  renderHTML({ node, HTMLAttributes }) {
    return [
      'div',
      mergeAttributes(HTMLAttributes, {
        'data-transition': '',
        'data-transition-id': String(node.attrs.transitionId ?? ''),
        'data-when-kind': String(node.attrs.whenKind ?? 'otherwise'),
        'data-when-value': String(node.attrs.whenValue ?? ''),
        'data-when-description': String(node.attrs.whenDescription ?? ''),
        'data-to-step-id': String(node.attrs.toStepId ?? ''),
        'data-to-step-name': String(node.attrs.toStepName ?? ''),
        'data-priority': String(node.attrs.priority ?? 100),
      }),
      0,
    ]
  },
  addNodeView() {
    return ReactNodeViewRenderer(TransitionNodeView)
  },
})

import { SlashCommands } from './slash-extension'

export const documentExtensions = [
  Scenario,
  ScenarioName,
  Step,
  StepName,
  SayBlock,
  DirectAnswerBlock,
  ActionSummary,
  ToolPolicy,
  ToolBinding,
  EventHints,
  EventHint,
  Transition,
  SlashCommands,
]
