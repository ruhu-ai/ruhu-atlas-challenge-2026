/**
 * Classifier eval card — WI-6.10.
 *
 * Pure presentational component that renders the per-step classifier
 * quality summary documented in
 * docs/pre-fill-intent-classifier-design/06-evaluation-spec.md
 * §Author-facing eval (canvas):
 *
 *   ┌─ Step: entry ──────────────────────────────────┐
 *   │  Classification quality (last 7 days)          │
 *   │  Macro-F1: 0.92  ↑ 0.04 vs production           │
 *   │  Unknown rate: 1.8%   ✓                         │
 *   │                                                  │
 *   │  Most confused:                                 │
 *   │   transfer_status ↔ kyc_help    7 turns         │
 *   │   close → unknown               12 turns        │
 *   │                                                  │
 *   │  [View 12 sample turns]                         │
 *   └─────────────────────────────────────────────────┘
 *
 * Data is passed in via props — the card does not fetch. Parent panels
 * the active agent step-properties panel owns the API call and wires the
 * result into this card.
 */

import { Card, CardContent, CardHeader, CardTitle } from '@/components/atoms/card'
import { Button } from '@/components/atoms/button'
import { ArrowDown, ArrowUp, ArrowRight, CheckCircle2, AlertTriangle } from 'lucide-react'

export type ConfusedPairDirection = 'symmetric' | 'a_to_b' | 'a_to_unknown'

export interface ClassifierEvalConfusedPair {
  intentA: string
  intentB: string
  count: number
  direction: ConfusedPairDirection
}

export interface ClassifierEvalSummary {
  stepId: string
  windowDays: number
  rowCount: number
  macroF1: number
  /** None when there's no prior production LoRA to compare against (cold-start). */
  macroF1DeltaVsProduction: number | null
  unknownRate: number
  /** Above this the unknown rate is rendered as a warning instead of a tick. */
  unknownRateWarningThreshold: number
  topConfusedPairs: ClassifierEvalConfusedPair[]
  sampleMisclassifiedCount: number
}

export interface ClassifierEvalCardProps {
  /** When null the card renders an empty/awaiting state. */
  summary: ClassifierEvalSummary | null
  loading?: boolean
  error?: string | null
  onViewSamples?: () => void
}

export function ClassifierEvalCard({
  summary,
  loading = false,
  error = null,
  onViewSamples,
}: ClassifierEvalCardProps) {
  return (
    <Card data-testid="classifier-eval-card" className="w-full">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">
          {summary
            ? `Classification quality (last ${summary.windowDays} days)`
            : 'Classification quality'}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {loading && <LoadingState />}
        {!loading && error && <ErrorState error={error} />}
        {!loading && !error && !summary && <EmptyState />}
        {!loading && !error && summary && (
          <PopulatedState summary={summary} onViewSamples={onViewSamples} />
        )}
      </CardContent>
    </Card>
  )
}

function LoadingState() {
  return (
    <div data-testid="eval-card-loading" className="text-muted-foreground">
      Loading classifier metrics…
    </div>
  )
}

function EmptyState() {
  return (
    <div data-testid="eval-card-empty" className="text-muted-foreground">
      No classifier eval data yet for this step. Eval reports populate after
      the next nightly run.
    </div>
  )
}

function ErrorState({ error }: { error: string }) {
  return (
    <div data-testid="eval-card-error" className="text-destructive flex items-start gap-2">
      <AlertTriangle className="h-4 w-4 mt-0.5 flex-shrink-0" />
      <span>{error}</span>
    </div>
  )
}

function PopulatedState({
  summary,
  onViewSamples,
}: {
  summary: ClassifierEvalSummary
  onViewSamples?: () => void
}) {
  return (
    <>
      <MacroF1Line summary={summary} />
      <UnknownRateLine summary={summary} />
      <ConfusedPairsBlock pairs={summary.topConfusedPairs} />
      {summary.sampleMisclassifiedCount > 0 && onViewSamples && (
        <Button
          variant="outline"
          size="sm"
          onClick={onViewSamples}
          data-testid="eval-card-view-samples"
        >
          View {summary.sampleMisclassifiedCount} sample turns
        </Button>
      )}
    </>
  )
}

function MacroF1Line({ summary }: { summary: ClassifierEvalSummary }) {
  const deltaText = renderDelta(summary.macroF1DeltaVsProduction)
  return (
    <div data-testid="eval-macro-f1" className="flex items-baseline gap-2">
      <span className="font-medium">Macro-F1:</span>
      <span>{formatF1(summary.macroF1)}</span>
      {deltaText && <span className={deltaTone(summary.macroF1DeltaVsProduction)}>{deltaText}</span>}
    </div>
  )
}

function UnknownRateLine({ summary }: { summary: ClassifierEvalSummary }) {
  const isOk = summary.unknownRate <= summary.unknownRateWarningThreshold
  return (
    <div data-testid="eval-unknown-rate" className="flex items-center gap-2">
      <span className="font-medium">Unknown rate:</span>
      <span>{formatPercent(summary.unknownRate)}</span>
      {isOk ? (
        <CheckCircle2
          className="h-4 w-4 text-emerald-600"
          aria-label="within unknown-rate target"
          data-testid="eval-unknown-rate-ok"
        />
      ) : (
        <AlertTriangle
          className="h-4 w-4 text-amber-600"
          aria-label="unknown rate above target"
          data-testid="eval-unknown-rate-warn"
        />
      )}
    </div>
  )
}

function ConfusedPairsBlock({ pairs }: { pairs: ClassifierEvalConfusedPair[] }) {
  if (pairs.length === 0) {
    return null
  }
  return (
    <div data-testid="eval-confused-pairs">
      <div className="font-medium mb-1">Most confused:</div>
      <ul className="space-y-1 ml-2">
        {pairs.map((pair, i) => (
          <li key={`${pair.intentA}-${pair.intentB}-${i}`} className="flex items-center justify-between gap-2">
            <span className="font-mono text-xs">
              {pair.intentA} {arrowFor(pair.direction)} {pair.intentB}
            </span>
            <span className="text-muted-foreground text-xs">{pair.count} turns</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ── helpers ─────────────────────────────────────────────────────────────

function formatF1(value: number): string {
  return value.toFixed(2)
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`
}

function renderDelta(delta: number | null): string | null {
  if (delta === null) {
    return null
  }
  const sign = delta > 0 ? '↑' : delta < 0 ? '↓' : '→'
  const magnitude = Math.abs(delta).toFixed(2)
  return `${sign} ${magnitude} vs production`
}

function deltaTone(delta: number | null): string {
  if (delta === null || delta === 0) {
    return 'text-muted-foreground'
  }
  return delta > 0 ? 'text-emerald-600' : 'text-destructive'
}

function arrowFor(direction: ConfusedPairDirection): string {
  if (direction === 'symmetric') {
    return '↔'
  }
  if (direction === 'a_to_unknown') {
    return '→'
  }
  return '→'
}

// Keep the lucide arrow icons importable so consumers can customise without
// re-importing from lucide directly.
export { ArrowDown, ArrowUp, ArrowRight }
