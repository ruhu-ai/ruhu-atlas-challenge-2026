/**
 * useAtlasReadiness — readiness-evaluation state for the Atlas panel:
 * recent runs + provider health (loaded on open), starting validate/fix
 * runs, and selecting/rerunning/cancelling existing runs. Run outcomes are
 * surfaced as system messages; a fix run that produced an Atlas session
 * adopts that session so its proposed deltas render inline.
 *
 * Extracted from AtlasAIPanel.tsx (RP-4.4). Session adoption and message
 * appends are threaded in from useAtlasSession.
 */

import { useEffect, useState, useCallback, type Dispatch, type MutableRefObject, type SetStateAction } from 'react'

import {
  atlasService,
  type AtlasSessionResponse,
  type AtlasReadinessRun,
  type AtlasReadinessRunSummary,
  type AtlasReadinessProviderHealth,
  type AtlasReadinessProviderPolicy,
  type AtlasReadinessScore,
} from '@/api/services/atlas.service'

import {
  type DisplayMessage,
  newDisplayMessageId,
  errorMessage,
  isValidAgentId,
  runScorePercent,
} from '../components/atlas-panel-helpers'

export type AtlasEvaluationMode = 'chat' | 'voice' | 'cases'

const BLOCKER_FIX_HINTS: Record<string, string> = {
  completion: 'Add or repair terminal paths so each case reaches a clear support outcome.',
  traceability: 'Capture and preserve required facts, tool outcomes, and handoff decisions in the trace.',
  improvement_potential: 'Ask Atlas to propose typed fixes for the weak paths found during evaluation.',
  containment: 'Tighten transitions and fallback behavior so the agent stays inside the intended workflow.',
  safety: 'Remove unsafe claims and add safer response policies or escalation paths.',
  handoff: 'Add a human handoff path for explicit escalation requests and unresolved cases.',
  voice_reliability: 'Check the voice fixture, STT confidence, entity preservation, and TTS artifact output.',
}

function formatCaseFailure(score: AtlasReadinessScore, index: number): string {
  const issue = score.failures[0] || score.blockers[0] || score.advisory_notes[0] || 'readiness check failed'
  const blockers = score.blockers.length ? ` [${score.blockers.join(', ')}]` : ''
  return `${index + 1}. ${score.case_id}: ${issue}${blockers}`
}

function buildEvaluationSummary(summary: AtlasReadinessRunSummary, label: string, mode: AtlasEvaluationMode | 'readiness'): string {
  const report = summary.report
  if (!report) return `${label} finished: ${summary.run.state}.`

  const score = typeof report.score_breakdown?.run_score === 'number'
    ? Math.round(report.score_breakdown.run_score * 100)
    : null
  const lines = [
    `${label} finished: ${report.publish_recommendation}${score !== null ? ` (${score}% run score).` : '.'}`,
  ]

  if (report.before_scores.length > 0) {
    const failedScores = report.before_scores.filter((item) => !item.passed || item.failures.length || item.blockers.length)
    const failedCount = failedScores.length
    lines.push(
      failedCount > 0
        ? `${failedCount} of ${report.before_scores.length} case(s) need attention.`
        : `All ${report.before_scores.length} case(s) passed the readiness checks.`,
    )

    if (failedScores.length > 0) {
      lines.push('', 'Main failures:')
      failedScores.slice(0, 3).forEach((scoreItem, index) => {
        lines.push(formatCaseFailure(scoreItem, index))
      })
    }
  }

  if (report.blockers.length > 0) {
    lines.push('', 'What to fix:')
    report.blockers.slice(0, 4).forEach((blocker, index) => {
      const hint = BLOCKER_FIX_HINTS[blocker.code] || blocker.message
      lines.push(`${index + 1}. ${blocker.code}: ${hint}`)
    })
  }

  if (mode === 'voice') {
    const voiceScores = report.before_scores.filter((item) => typeof item.voice_reliability_score === 'number')
    const lowestVoice = voiceScores.reduce<AtlasReadinessScore | null>((lowest, item) => {
      if (!lowest) return item
      return (item.voice_reliability_score ?? 1) < (lowest.voice_reliability_score ?? 1) ? item : lowest
    }, null)
    if (lowestVoice) {
      lines.push(
        '',
        `Voice reliability: ${Math.round((lowestVoice.voice_reliability_score ?? 0) * 100)}% on ${lowestVoice.case_id}.`,
      )
    }
  }

  const nextStep = report.next_steps[0]
  if (nextStep) {
    lines.push('', `Recommended next action: ${nextStep}`)
  } else if (report.publish_recommendation !== 'publish') {
    lines.push('', 'Recommended next action: ask Atlas to propose typed fixes for these readiness failures.')
  }

  return lines.join('\n')
}

export function useAtlasReadiness(args: {
  isOpen: boolean
  agentId?: string
  setMessages: Dispatch<SetStateAction<DisplayMessage[]>>
  setCurrentSessionId: Dispatch<SetStateAction<string | null>>
  setCurrentSession: Dispatch<SetStateAction<AtlasSessionResponse | null>>
  lastEventSequenceRef: MutableRefObject<number | null>
  refreshHistory: () => Promise<void>
  providerPolicy: AtlasReadinessProviderPolicy
  demoCaseSet: boolean
}) {
  const {
    isOpen,
    agentId,
    setMessages,
    setCurrentSessionId,
    setCurrentSession,
    lastEventSequenceRef,
    refreshHistory,
    providerPolicy,
    demoCaseSet,
  } = args

  const [isRunningReadiness, setIsRunningReadiness] = useState(false)
  const [readinessSummary, setReadinessSummary] = useState<AtlasReadinessRunSummary | null>(null)
  const [readinessRuns, setReadinessRuns] = useState<AtlasReadinessRun[]>([])
  const [readinessProviderHealth, setReadinessProviderHealth] = useState<AtlasReadinessProviderHealth | null>(null)
  const [isLoadingReadinessRuns, setIsLoadingReadinessRuns] = useState(false)
  const [readinessActionRunId, setReadinessActionRunId] = useState<string | null>(null)
  const [activeEvaluationLabel, setActiveEvaluationLabel] = useState<string | null>(null)

  // On open or agent change: fetch recent runs + provider health
  useEffect(() => {
    if (!isOpen || !isValidAgentId(agentId)) return
    let cancelled = false

    setIsLoadingReadinessRuns(true)
    atlasService
      .listReadinessRuns({ agentId, limit: 5 })
      .then((res) => {
        if (!cancelled) setReadinessRuns(res.runs)
      })
      .catch((err) => console.error('Atlas: listReadinessRuns failed', err))
      .finally(() => {
        if (!cancelled) setIsLoadingReadinessRuns(false)
      })

    atlasService
      .getReadinessProviderHealth(providerPolicy)
      .then((res) => {
        if (!cancelled) setReadinessProviderHealth(res)
      })
      .catch((err) => console.error('Atlas: readiness provider health failed', err))

    return () => {
      cancelled = true
    }
  }, [isOpen, agentId, providerPolicy])

  const refreshReadinessRuns = useCallback(async () => {
    if (!isValidAgentId(agentId)) return
    setIsLoadingReadinessRuns(true)
    try {
      const res = await atlasService.listReadinessRuns({ agentId, limit: 5 })
      setReadinessRuns(res.runs)
    } catch (err) {
      console.error('Atlas: listReadinessRuns failed', err)
    } finally {
      setIsLoadingReadinessRuns(false)
    }
  }, [agentId])

  const runReadinessRequest = async (
    request: Parameters<typeof atlasService.createReadinessRun>[0],
    options: { label: string; mode?: AtlasEvaluationMode | 'readiness' },
  ) => {
    const summary = await atlasService.createReadinessRun(request)
    setReadinessSummary(summary)
    setMessages((prev) => [
      ...prev,
      {
        id: newDisplayMessageId('readiness'),
        role: 'system',
        content: buildEvaluationSummary(summary, options.label, options.mode || 'readiness'),
        timestamp: new Date(),
      },
    ])
    if (summary.run.atlas_session_id) {
      const [session, state] = await Promise.all([
        atlasService.getSession(summary.run.atlas_session_id),
        atlasService.getSessionState(summary.run.atlas_session_id),
      ])
      setCurrentSessionId(session.session_id)
      setCurrentSession(session)
      lastEventSequenceRef.current = null
      setMessages((prev) => [
        ...prev,
        {
          id: newDisplayMessageId('readiness-deltas'),
          role: 'assistant',
          content: state.message,
          timestamp: new Date(),
          turnResponse: state,
        },
      ])
    }
    await refreshHistory()
    await refreshReadinessRuns()
  }

  const handleRunEvaluation = async (
    mode: AtlasEvaluationMode,
    options: { voiceAudioUri?: string | null } = {},
  ) => {
    if (!isValidAgentId(agentId) || isRunningReadiness) return
    const label =
      mode === 'cases'
        ? 'case evaluation'
        : mode === 'voice'
          ? 'voice evaluation'
          : 'chat evaluation'
    setIsRunningReadiness(true)
    setActiveEvaluationLabel(label)
    try {
      const voiceMode = mode === 'voice'
      const caseLimit = mode === 'cases' ? 5 : voiceMode ? 2 : 3
      const voiceCaseCount = voiceMode ? 1 : 0
      const estimatedBudgetUsd = ((caseLimit * 0.05) + (voiceCaseCount * 0.02)).toFixed(2)
      await runReadinessRequest(
        {
          agent_id: agentId!,
          scope: 'validate',
          provider_policy: 'google_only',
          demo_case_set: mode === 'cases',
          case_limit: caseLimit,
          voice_case_count: voiceCaseCount,
          voice_audio_uri: voiceMode
            ? options.voiceAudioUri || 'gs://ruhu-readiness-fixtures/payment.wav'
            : null,
          voice_language: voiceMode ? 'en-US' : null,
          require_real_voice_io: false,
          max_estimated_cost_usd: estimatedBudgetUsd,
          cloud_evidence: true,
        },
        {
          label:
            mode === 'cases'
              ? 'Case evaluation'
              : mode === 'voice'
                ? 'Voice evaluation'
                : 'Chat evaluation',
          mode,
        },
      )
    } catch (err) {
      console.error('Atlas: evaluation run failed', err)
      setMessages((prev) => [...prev, errorMessage(err)])
    } finally {
      setIsRunningReadiness(false)
      setActiveEvaluationLabel(null)
    }
  }

  const handleRunReadiness = async (scope: 'validate' | 'fix') => {
    if (!isValidAgentId(agentId) || isRunningReadiness) return
    setIsRunningReadiness(true)
    setActiveEvaluationLabel(scope === 'fix' ? 'fix evaluation' : 'readiness evaluation')
    try {
      const caseLimit = scope === 'fix' ? 4 : 3
      const estimatedBudgetUsd =
        providerPolicy === 'deterministic'
          ? '0'
          : (caseLimit * 0.05).toFixed(2)
      await runReadinessRequest(
        {
          agent_id: agentId!,
          scope,
          provider_policy: providerPolicy,
          demo_case_set: demoCaseSet,
          case_limit: caseLimit,
          voice_case_count: 0,
          max_estimated_cost_usd: estimatedBudgetUsd,
          cloud_evidence: true,
        },
        { label: `Readiness ${scope}`, mode: 'readiness' },
      )
    } catch (err) {
      console.error('Atlas: readiness run failed', err)
      setMessages((prev) => [...prev, errorMessage(err)])
    } finally {
      setIsRunningReadiness(false)
      setActiveEvaluationLabel(null)
    }
  }

  const handleSelectReadinessRun = async (runId: string) => {
    if (readinessActionRunId) return
    setReadinessActionRunId(runId)
    try {
      const summary = await atlasService.getReadinessRun(runId)
      setReadinessSummary(summary)
    } catch (err) {
      console.error('Atlas: get readiness run failed', err)
      setMessages((prev) => [...prev, errorMessage(err)])
    } finally {
      setReadinessActionRunId(null)
    }
  }

  const handleRerunReadiness = async (runId: string) => {
    if (readinessActionRunId || isRunningReadiness) return
    setReadinessActionRunId(runId)
    try {
      const summary = await atlasService.rerunReadinessRun(runId)
      setReadinessSummary(summary)
      await refreshReadinessRuns()
      const score = runScorePercent(summary)
      setMessages((prev) => [
        ...prev,
        {
          id: newDisplayMessageId('readiness-rerun'),
          role: 'system',
          content:
            `Readiness rerun finished: ${summary.report?.publish_recommendation ?? summary.run.state}` +
            (score !== null ? ` (${score}% run score).` : '.'),
          timestamp: new Date(),
        },
      ])
    } catch (err) {
      console.error('Atlas: rerun readiness failed', err)
      setMessages((prev) => [...prev, errorMessage(err)])
    } finally {
      setReadinessActionRunId(null)
    }
  }

  const handleCancelReadiness = async (runId: string) => {
    if (readinessActionRunId) return
    setReadinessActionRunId(runId)
    try {
      const summary = await atlasService.cancelReadinessRun(runId)
      setReadinessSummary(summary)
      await refreshReadinessRuns()
    } catch (err) {
      console.error('Atlas: cancel readiness failed', err)
      setMessages((prev) => [...prev, errorMessage(err)])
    } finally {
      setReadinessActionRunId(null)
    }
  }

  return {
    isRunningReadiness,
    activeEvaluationLabel,
    readinessSummary,
    readinessRuns,
    readinessProviderHealth,
    isLoadingReadinessRuns,
    readinessActionRunId,
    refreshReadinessRuns,
    handleRunEvaluation,
    handleRunReadiness,
    handleSelectReadinessRun,
    handleRerunReadiness,
    handleCancelReadiness,
  }
}
