/**
 * ReasoningTimeline contract.
 *
 * Pins the rendering invariants for the per-turn evidence timeline so a
 * future restyle can't silently drop the reasoning rows that make this
 * surface useful (step transition, guards, action, tools, messages).
 */
import { render, screen } from '@testing-library/react'

import { ReasoningTimeline } from '@/features/agent-canvas/components/ReasoningTimeline'
import type { ConversationTrace } from '@/api/services/voice-session.service'

function makeTrace(overrides: Partial<ConversationTrace> = {}): ConversationTrace {
  return {
    trace_id: 't1',
    conversation_id: 'c1',
    turn_id: 'turn-1',
    step_before: 'discover',
    step_after: 'product_qa',
    event_type: 'user_message',
    emitted_messages: [],
    chosen_action: null,
    guard_results: [],
    tool_calls: [],
    latency_breakdown_ms: {},
    recorded_at: '2026-05-09T10:00:00.000Z',
    ...overrides,
  }
}

describe('ReasoningTimeline', () => {
  it('renders empty-state copy when given no traces', () => {
    render(<ReasoningTimeline traces={[]} />)
    expect(screen.getByText(/No reasoning recorded yet/i)).toBeInTheDocument()
  })

  it('renders a step transition row with both step ids', () => {
    render(<ReasoningTimeline traces={[makeTrace()]} />)
    expect(screen.getByText(/Transitioned/)).toBeInTheDocument()
    expect(screen.getByText('discover')).toBeInTheDocument()
    expect(screen.getByText('product_qa')).toBeInTheDocument()
  })

  it('renders a "Stayed at" row when step_before equals step_after', () => {
    render(
      <ReasoningTimeline
        traces={[makeTrace({ step_before: 'discover', step_after: 'discover' })]}
      />,
    )
    expect(screen.getByText(/Stayed at/)).toBeInTheDocument()
  })

  it('renders chosen_action with type and reason', () => {
    render(
      <ReasoningTimeline
        traces={[
          makeTrace({
            chosen_action: {
              type: 'run_tool',
              reason: 'product_question_asked',
            },
          }),
        ]}
      />,
    )
    expect(screen.getByText(/Chose action: run_tool/)).toBeInTheDocument()
    expect(screen.getByText(/product_question_asked/)).toBeInTheDocument()
  })

  it('renders tool calls with ref, status, and latency from latency_breakdown_ms', () => {
    render(
      <ReasoningTimeline
        traces={[
          makeTrace({
            tool_calls: [
              { tool_ref: 'knowledge.lookup', status: 'success' },
            ],
            latency_breakdown_ms: { 'tool:knowledge.lookup': 180, total: 220 },
          }),
        ]}
      />,
    )
    expect(screen.getByText('knowledge.lookup')).toBeInTheDocument()
    expect(screen.getByText('success')).toBeInTheDocument()
    expect(screen.getByText('180ms')).toBeInTheDocument()
  })

  it('renders guard results with pass/fail tone via row label', () => {
    render(
      <ReasoningTimeline
        traces={[
          makeTrace({
            guard_results: [
              { guard_kind: 'fact_present', guard_value: 'email', passed: true },
              { guard_kind: 'rate_limit', guard_value: 'org', passed: false, reason: 'over quota' },
            ],
          }),
        ]}
      />,
    )
    expect(screen.getByText(/Guard passed/)).toBeInTheDocument()
    expect(screen.getByText(/Guard failed/)).toBeInTheDocument()
    expect(screen.getByText(/over quota/)).toBeInTheDocument()
  })

  it('renders emitted assistant messages truncated to two lines', () => {
    render(
      <ReasoningTimeline
        traces={[
          makeTrace({
            emitted_messages: [{ role: 'assistant', text: 'Booking your demo now.' }],
          }),
        ]}
      />,
    )
    expect(screen.getByText(/Replied/)).toBeInTheDocument()
    expect(screen.getByText(/Booking your demo now\./)).toBeInTheDocument()
  })

  it('numbers turns starting at 1 in the order received', () => {
    const traces: ConversationTrace[] = [
      makeTrace({ trace_id: 'a', turn_id: 'first' }),
      makeTrace({ trace_id: 'b', turn_id: 'second' }),
      makeTrace({ trace_id: 'c', turn_id: 'third' }),
    ]
    render(<ReasoningTimeline traces={traces} />)
    expect(screen.getByText('Turn 1')).toBeInTheDocument()
    expect(screen.getByText('Turn 2')).toBeInTheDocument()
    expect(screen.getByText('Turn 3')).toBeInTheDocument()
  })
})
