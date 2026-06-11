import {
  createBlankAgentDefinition,
  createBlankState,
  getAgentDefinitionWarnings,
  getStateWarnings,
} from './utils'
import type { AgentDefinition } from '@/types/agent-definition'

// Per the generic-step ADR, kind is derived from optional capability fields:
//   - action: presence of action_config
//   - capture: non-empty fact_requirements
//   - terminal: non-null terminal_disposition
//   - handoff: presence of handoff
//   - entry: state.id === definition.start_step_id
//   - conversation: default
describe('agent-definition artifact warnings', () => {
  it('reports agent-level follow-up handler issues', () => {
    const actionState = {
      ...createBlankState(),
      id: 'create_booking',
      name: 'Create Booking',
      artifact_type: 'booking',
      // Marker for action kind under field-presence derivation:
      action_config: { code: 'result = {}' },
    } as ReturnType<typeof createBlankState> & { action_config: { code: string } }
    const cancelState = {
      ...createBlankState(),
      id: 'cancel_booking',
      name: 'Cancel Booking',
    }
    const entryState = createBlankState()
    const definition: AgentDefinition = {
      ...createBlankAgentDefinition('Booking Agent'),
      steps: [entryState, actionState, cancelState],
      fact_schema: [
        {
          name: 'email',
          type: 'string',
          required: false,
          source_policy: 'deterministic_first',
          confidence_threshold: 0.8,
          conflict_policy: 'prefer_deterministic',
        },
      ],
      followup_handlers: [
        {
          artifact_type: 'ticket',
          followup_intent: 'cancel_request',
          target_step_id: 'missing_state',
          fact_requirements: [{ name: 'account_id' }],
        },
      ],
    }
    definition.start_step_id = entryState.id

    const warnings = getAgentDefinitionWarnings(definition)
    expect(warnings).toEqual(
      expect.arrayContaining([
        expect.stringContaining('references artifact type "ticket" which no action state produces'),
        expect.stringContaining('targets state "missing_state" which does not exist'),
        expect.stringContaining('requires fact "account_id" which is not declared'),
      ]),
    )
  })

  it('warns about inconsistent voice policy combinations on a state', () => {
    const actionState = {
      ...createBlankState(),
      activity_label: 'Checking the calendar',
      endpointing_ms: 300,
      slow_threshold_ms: 800,
      soft_timeout_ms: 1200,
      turn_eagerness: 'high' as const,
      interruptibility_policy: 'non_interruptible' as const,
      // Marker for action kind under field-presence derivation:
      action_config: { code: 'result = {}' },
    } as ReturnType<typeof createBlankState> & { action_config: { code: string } }

    const warnings = getStateWarnings(actionState)
    expect(warnings).toEqual(
      expect.arrayContaining([
        expect.stringContaining('endpointing_ms is below 400ms'),
        expect.stringContaining('soft_timeout_ms is greater than slow_threshold_ms'),
        expect.stringContaining('High turn eagerness combined with non_interruptible speech'),
      ]),
    )
  })
})
