import { describe, expect, test } from '@jest/globals'
import type { AgentDocument, AgentStepTransition } from '@/types/agent-document'

// Re-export the internals we want to test by importing the module's
// private helpers indirectly: serialize → produces TipTap-shaped attrs;
// deserialize → consumes the same attrs and rebuilds Conditions. We
// don't have direct access to ``buildCondition``, but we exercise it
// through a minimal end-to-end shape: build a one-transition document,
// extract the transition node attrs, push them back through
// `documentFromEditor` and assert the discriminated-union member is
// preserved.

import type { JSONContent } from '@tiptap/core'

import { documentToEditorJSON } from './serialize'
import { editorJSONToDocument } from './deserialize'

function _doc(transition: AgentStepTransition): AgentDocument {
  return {
    version: 'v1',
    start_scenario_id: 'main',
    scenarios: [
      {
        id: 'main',
        name: 'Main',
        start_step_id: 'entry',
        steps: [
          {
            id: 'entry',
            name: 'Entry',
            transitions: [transition],
          },
        ],
      },
    ],
  } as AgentDocument
}

function _roundTrip(doc: AgentDocument): AgentDocument {
  const editorDoc = documentToEditorJSON(doc) as unknown as JSONContent
  return editorJSONToDocument(editorDoc, doc)
}

describe('condition round-trip — outcome', () => {
  test('preserves event + description on a clean round trip', () => {
    const original = _doc({
      id: 't_product',
      to_step_id: 'entry',
      when: {
        kind: 'outcome',
        event: 'product_question',
        description: 'The user asks what Ruhu is or what the product does.',
      },
    })
    const out = _roundTrip(original)
    const when = out.scenarios[0].steps[0].transitions[0].when
    expect(when.kind).toBe('outcome')
    if (when.kind !== 'outcome') return
    expect(when.event).toBe('product_question')
    expect(when.description).toBe(
      'The user asks what Ruhu is or what the product does.',
    )
  })
})

describe('condition round-trip — fact_present', () => {
  test('preserves fact_name', () => {
    const out = _roundTrip(
      _doc({
        id: 't_email',
        to_step_id: 'entry',
        when: { kind: 'fact_present', fact_name: 'email' },
      }),
    )
    const when = out.scenarios[0].steps[0].transitions[0].when
    expect(when.kind).toBe('fact_present')
    if (when.kind !== 'fact_present') return
    expect(when.fact_name).toBe('email')
  })
})

describe('condition round-trip — tool_outcome', () => {
  test('preserves outcome code', () => {
    const out = _roundTrip(
      _doc({
        id: 't_tool_ok',
        to_step_id: 'entry',
        when: { kind: 'tool_outcome', outcome: 'action_code_success' },
      }),
    )
    const when = out.scenarios[0].steps[0].transitions[0].when
    expect(when.kind).toBe('tool_outcome')
    if (when.kind !== 'tool_outcome') return
    expect(when.outcome).toBe('action_code_success')
  })
})

describe('condition round-trip — guard_failure', () => {
  test('preserves guard_id', () => {
    const out = _roundTrip(
      _doc({
        id: 't_guard',
        to_step_id: 'entry',
        when: { kind: 'guard_failure', guard_id: 'channel_allowed' },
      }),
    )
    const when = out.scenarios[0].steps[0].transitions[0].when
    expect(when.kind).toBe('guard_failure')
    if (when.kind !== 'guard_failure') return
    expect(when.guard_id).toBe('channel_allowed')
  })
})

describe('condition round-trip — all_required_facts_present', () => {
  test('survives without any payload', () => {
    const out = _roundTrip(
      _doc({
        id: 't_facts',
        to_step_id: 'entry',
        when: { kind: 'all_required_facts_present' },
      }),
    )
    const when = out.scenarios[0].steps[0].transitions[0].when
    expect(when.kind).toBe('all_required_facts_present')
  })
})

describe('condition round-trip — otherwise', () => {
  test('survives without any payload', () => {
    const out = _roundTrip(
      _doc({
        id: 't_other',
        to_step_id: 'entry',
        when: { kind: 'otherwise' },
      }),
    )
    const when = out.scenarios[0].steps[0].transitions[0].when
    expect(when.kind).toBe('otherwise')
  })
})

describe('condition round-trip — multiple transitions on one step', () => {
  test('preserves each kind independently', () => {
    const out = _roundTrip({
      version: 'v1',
      start_scenario_id: 'main',
      scenarios: [
        {
          id: 'main',
          name: 'Main',
          start_step_id: 'entry',
          steps: [
            {
              id: 'entry',
              name: 'Entry',
              transitions: [
                {
                  id: 't_outcome',
                  to_step_id: 'entry',
                  when: {
                    kind: 'outcome',
                    event: 'pricing_question',
                    description: 'User asks about pricing or plans.',
                  },
                },
                {
                  id: 't_facts',
                  to_step_id: 'entry',
                  when: { kind: 'all_required_facts_present' },
                },
                {
                  id: 't_other',
                  to_step_id: 'entry',
                  when: { kind: 'otherwise' },
                },
              ],
            },
          ],
        },
      ],
    } as AgentDocument)

    const transitions = out.scenarios[0].steps[0].transitions
    const byId = new Map(transitions.map((t) => [t.id, t.when]))
    expect(byId.get('t_outcome')?.kind).toBe('outcome')
    expect(byId.get('t_facts')?.kind).toBe('all_required_facts_present')
    expect(byId.get('t_other')?.kind).toBe('otherwise')
  })
})

// ── tool_policy chip round-trip (Phase 5b) ─────────────────────────────────

describe('tool_policy round-trip', () => {
  test('preserves a single tool_policy binding through serialize → deserialize', () => {
    const out = _roundTrip(
      _doc({
        id: 't_other',
        to_step_id: 'entry',
        when: { kind: 'otherwise' },
      }),
    )
    // Inject tool_policy on the original document's entry step before
    // round-tripping. The _doc helper above doesn't seed it; build a
    // doc with tool_policy directly.
    const original = {
      version: 'v1',
      start_scenario_id: 'main',
      scenarios: [
        {
          id: 'main',
          name: 'Main',
          start_step_id: 'entry',
          steps: [
            {
              id: 'entry',
              name: 'Entry',
              transitions: [
                { id: 't_other', to_step_id: 'entry', when: { kind: 'otherwise' as const } },
              ],
              tool_policy: [
                { ref: 'calendar.create_event', mode: 'allowed' },
              ],
            },
          ],
        },
      ],
    } as AgentDocument
    const out2 = _roundTrip(original)
    const policy = out2.scenarios[0].steps[0].tool_policy ?? []
    expect(policy).toHaveLength(1)
    expect(policy[0].ref).toBe('calendar.create_event')
    expect(policy[0].mode).toBe('allowed')

    // Sanity: the otherwise transition the helper round-trips also
    // survives unchanged.
    expect(out.scenarios[0].steps[0].transitions[0].when.kind).toBe('otherwise')
  })

  test('preserves multiple tool_policy bindings with mixed modes', () => {
    const original = {
      version: 'v1',
      start_scenario_id: 'main',
      scenarios: [
        {
          id: 'main',
          name: 'Main',
          start_step_id: 'entry',
          steps: [
            {
              id: 'entry',
              name: 'Entry',
              transitions: [
                { id: 't1', to_step_id: 'entry', when: { kind: 'otherwise' as const } },
              ],
              tool_policy: [
                { ref: 'calendar.create_event', mode: 'allowed' },
                { ref: 'crm.create_lead', mode: 'required' },
                { ref: 'slack.notify', mode: 'optional' },
              ],
            },
          ],
        },
      ],
    } as AgentDocument
    const out = _roundTrip(original)
    const policy = out.scenarios[0].steps[0].tool_policy ?? []
    expect(policy).toHaveLength(3)
    expect(policy.map((b) => b.ref)).toEqual([
      'calendar.create_event',
      'crm.create_lead',
      'slack.notify',
    ])
    expect(policy.map((b) => b.mode)).toEqual(['allowed', 'required', 'optional'])
  })

  test('step with no tool_policy survives unchanged', () => {
    const original = {
      version: 'v1',
      start_scenario_id: 'main',
      scenarios: [
        {
          id: 'main',
          name: 'Main',
          start_step_id: 'entry',
          steps: [
            {
              id: 'entry',
              name: 'Entry',
              transitions: [
                { id: 't1', to_step_id: 'entry', when: { kind: 'otherwise' as const } },
              ],
            },
          ],
        },
      ],
    } as AgentDocument
    const out = _roundTrip(original)
    const policy = out.scenarios[0].steps[0].tool_policy ?? []
    expect(policy).toHaveLength(0)
  })
})
