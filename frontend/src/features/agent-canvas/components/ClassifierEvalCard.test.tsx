import { render, screen, fireEvent } from '@testing-library/react'

import {
  ClassifierEvalCard,
  ClassifierEvalSummary,
} from './ClassifierEvalCard'

const baseSummary: ClassifierEvalSummary = {
  stepId: 'entry',
  windowDays: 7,
  rowCount: 412,
  macroF1: 0.92,
  macroF1DeltaVsProduction: 0.04,
  unknownRate: 0.018,
  unknownRateWarningThreshold: 0.05,
  topConfusedPairs: [
    { intentA: 'transfer_status', intentB: 'kyc_help', count: 7, direction: 'symmetric' },
    { intentA: 'close', intentB: 'unknown', count: 12, direction: 'a_to_unknown' },
  ],
  sampleMisclassifiedCount: 19,
}

describe('ClassifierEvalCard', () => {
  it('renders the populated state with macro-F1, delta, and unknown rate', () => {
    render(<ClassifierEvalCard summary={baseSummary} />)
    expect(screen.getByText(/Classification quality \(last 7 days\)/i)).toBeInTheDocument()
    expect(screen.getByTestId('eval-macro-f1').textContent).toContain('0.92')
    expect(screen.getByTestId('eval-macro-f1').textContent).toMatch(/↑\s*0\.04 vs production/)
    expect(screen.getByTestId('eval-unknown-rate').textContent).toContain('1.8%')
  })

  it('renders an OK indicator when unknown rate is below threshold', () => {
    render(<ClassifierEvalCard summary={baseSummary} />)
    expect(screen.getByTestId('eval-unknown-rate-ok')).toBeInTheDocument()
    expect(screen.queryByTestId('eval-unknown-rate-warn')).toBeNull()
  })

  it('renders a warning indicator when unknown rate exceeds threshold', () => {
    const overThreshold = { ...baseSummary, unknownRate: 0.12 }
    render(<ClassifierEvalCard summary={overThreshold} />)
    expect(screen.getByTestId('eval-unknown-rate-warn')).toBeInTheDocument()
    expect(screen.queryByTestId('eval-unknown-rate-ok')).toBeNull()
  })

  it('renders a downward arrow with destructive tone when macro-F1 regressed', () => {
    const regressed = { ...baseSummary, macroF1DeltaVsProduction: -0.03 }
    render(<ClassifierEvalCard summary={regressed} />)
    expect(screen.getByTestId('eval-macro-f1').textContent).toMatch(/↓\s*0\.03 vs production/)
  })

  it('omits the delta line when no production baseline exists', () => {
    const coldStart = { ...baseSummary, macroF1DeltaVsProduction: null }
    render(<ClassifierEvalCard summary={coldStart} />)
    const macroLine = screen.getByTestId('eval-macro-f1')
    expect(macroLine.textContent).not.toContain('vs production')
  })

  it('renders symmetric and directional confused pairs', () => {
    render(<ClassifierEvalCard summary={baseSummary} />)
    const block = screen.getByTestId('eval-confused-pairs')
    expect(block.textContent).toContain('transfer_status ↔ kyc_help')
    expect(block.textContent).toContain('close → unknown')
    expect(block.textContent).toContain('7 turns')
    expect(block.textContent).toContain('12 turns')
  })

  it('hides the confused-pairs block when there are no pairs', () => {
    const empty = { ...baseSummary, topConfusedPairs: [] }
    render(<ClassifierEvalCard summary={empty} />)
    expect(screen.queryByTestId('eval-confused-pairs')).toBeNull()
  })

  it('shows the View samples button and fires the callback', () => {
    const onViewSamples = jest.fn()
    render(<ClassifierEvalCard summary={baseSummary} onViewSamples={onViewSamples} />)
    const button = screen.getByTestId('eval-card-view-samples')
    expect(button.textContent).toContain('View 19 sample turns')
    fireEvent.click(button)
    expect(onViewSamples).toHaveBeenCalledTimes(1)
  })

  it('hides the View samples button when no callback is provided', () => {
    render(<ClassifierEvalCard summary={baseSummary} />)
    expect(screen.queryByTestId('eval-card-view-samples')).toBeNull()
  })

  it('hides the View samples button when sample count is zero', () => {
    const noSamples = { ...baseSummary, sampleMisclassifiedCount: 0 }
    const onViewSamples = jest.fn()
    render(<ClassifierEvalCard summary={noSamples} onViewSamples={onViewSamples} />)
    expect(screen.queryByTestId('eval-card-view-samples')).toBeNull()
  })

  it('renders a loading state when loading is true', () => {
    render(<ClassifierEvalCard summary={null} loading />)
    expect(screen.getByTestId('eval-card-loading')).toBeInTheDocument()
    expect(screen.queryByTestId('eval-macro-f1')).toBeNull()
  })

  it('renders an empty state when summary is null and not loading', () => {
    render(<ClassifierEvalCard summary={null} />)
    expect(screen.getByTestId('eval-card-empty')).toBeInTheDocument()
  })

  it('renders an error state when error is set', () => {
    render(<ClassifierEvalCard summary={null} error="failed to load eval data" />)
    expect(screen.getByTestId('eval-card-error').textContent).toContain(
      'failed to load eval data',
    )
  })

  it('error state takes precedence over an empty summary', () => {
    render(<ClassifierEvalCard summary={null} error="boom" />)
    expect(screen.getByTestId('eval-card-error')).toBeInTheDocument()
    expect(screen.queryByTestId('eval-card-empty')).toBeNull()
  })

  it('loading state takes precedence over error', () => {
    render(<ClassifierEvalCard summary={null} loading error="ignored while loading" />)
    expect(screen.getByTestId('eval-card-loading')).toBeInTheDocument()
    expect(screen.queryByTestId('eval-card-error')).toBeNull()
  })
})
