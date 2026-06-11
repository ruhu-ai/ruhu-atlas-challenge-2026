/**
 * CitationView
 *
 * Drops onto any conversation detail surface. Shows extracted variables with
 * their grounded source utterance, confidence, and provenance — i.e. every
 * fact captured during the call OR filled by the post-call analysis sweep.
 */

import React from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, Sparkles, FileText, AlertCircle } from 'lucide-react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/atoms/card'
import { Button } from '@/components/atoms/button'
import { Badge } from '@/components/atoms/badge'
import {
  citationsService,
  type Citation,
  type CitationSource,
} from '@/api/services/citations.service'

interface CitationViewProps {
  conversationId: string | null
  /**
   * Called when the user clicks a citation. Hosts can scroll the transcript
   * to the source turn (and optionally apply a highlight overlay).
   */
  onCitationFocus?: (citation: Citation) => void
}

function sourceLabel(source: CitationSource): string {
  switch (source) {
    case 'deterministic':
      return 'pattern match'
    case 'classifier':
      return 'classifier'
    case 'llm_proposed':
      return 'AI inferred'
    case 'tool':
      return 'tool'
    case 'user_confirmed':
      return 'confirmed'
    case 'extractor':
      return 'extractor'
    case 'system':
      return 'system'
  }
}

function sourceVariant(source: CitationSource): 'default' | 'secondary' | 'outline' {
  if (source === 'deterministic' || source === 'user_confirmed') return 'default'
  if (source === 'llm_proposed') return 'outline'
  return 'secondary'
}

function confidenceLabel(confidence: number | null): string {
  if (confidence === null || confidence === undefined) return ''
  const pct = Math.round(confidence * 100)
  return `${pct}%`
}

function renderValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

export function CitationView({ conversationId, onCitationFocus }: CitationViewProps) {
  const queryClient = useQueryClient()

  const citationsQuery = useQuery({
    queryKey: ['conversation-citations', conversationId],
    queryFn: () => citationsService.list(conversationId!),
    enabled: Boolean(conversationId),
  })

  const sweepMutation = useMutation({
    mutationFn: () => citationsService.runAnalysisSweep(conversationId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['conversation-citations', conversationId] })
    },
  })

  if (!conversationId) {
    return (
      <Card>
        <CardContent className="pt-6 text-sm text-muted-foreground">
          Select a conversation to see its citations.
        </CardContent>
      </Card>
    )
  }

  const citations = citationsQuery.data?.citations ?? []

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold flex items-center gap-2">
            <FileText className="h-4 w-4" />
            Citations
          </h3>
          <p className="text-xs text-muted-foreground">
            Every variable extracted from this conversation, grounded to the
            exact utterance.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => sweepMutation.mutate()}
          disabled={sweepMutation.isPending}
        >
          {sweepMutation.isPending ? (
            <RefreshCw className="h-3.5 w-3.5 mr-1 animate-spin" />
          ) : (
            <Sparkles className="h-3.5 w-3.5 mr-1" />
          )}
          Run analysis sweep
        </Button>
      </div>

      {sweepMutation.isError && (
        <Card>
          <CardContent className="pt-6 flex items-start gap-2 text-sm text-destructive">
            <AlertCircle className="h-4 w-4 mt-0.5" />
            <span>Sweep failed. Try again — the conversation must have a published agent with an analysis schema.</span>
          </CardContent>
        </Card>
      )}

      {sweepMutation.data && (
        <Card>
          <CardContent className="pt-6 text-sm">
            Sweep filled <span className="font-semibold">{sweepMutation.data.variables_filled.length}</span> of {sweepMutation.data.variables_total} variables.
            {sweepMutation.data.variables_unfilled.length > 0 && (
              <span className="ml-2 text-muted-foreground">
                Unfilled: {sweepMutation.data.variables_unfilled.join(', ')}
              </span>
            )}
          </CardContent>
        </Card>
      )}

      {citationsQuery.isLoading ? (
        <Card>
          <CardContent className="pt-6 text-sm text-muted-foreground">Loading citations…</CardContent>
        </Card>
      ) : citations.length === 0 ? (
        <Card>
          <CardContent className="pt-6 text-sm text-muted-foreground">
            No citations yet for this conversation. Run the analysis sweep, or
            check that the agent's fact and analysis schemas are configured.
          </CardContent>
        </Card>
      ) : (
        citations.map((citation) => (
          <Card
            key={`${citation.fact_name}-${citation.turn_id}`}
            className={onCitationFocus ? 'cursor-pointer hover:bg-muted/40 transition' : ''}
            onClick={() => onCitationFocus?.(citation)}
          >
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold flex items-center justify-between gap-2">
                <span>{citation.fact_name}</span>
                <div className="flex items-center gap-2">
                  <Badge variant={sourceVariant(citation.source)}>
                    {sourceLabel(citation.source)}
                  </Badge>
                  {citation.confidence !== null && (
                    <span className="text-xs text-muted-foreground font-normal">
                      {confidenceLabel(citation.confidence)}
                    </span>
                  )}
                </div>
              </CardTitle>
            </CardHeader>
            <CardContent className="pt-0 space-y-2">
              <div className="text-sm">
                <span className="text-muted-foreground">Value: </span>
                <span className="font-mono">{renderValue(citation.value)}</span>
              </div>
              {citation.source_utterance && (
                <blockquote className="text-sm border-l-2 border-primary/40 pl-3 italic text-muted-foreground">
                  "{citation.source_utterance}"
                </blockquote>
              )}
              <div className="text-xs text-muted-foreground">
                Turn {citation.turn_id}
                {citation.step_id && <> · step {citation.step_id}</>}
                {citation.replaced_previous && <> · replaced earlier value</>}
              </div>
            </CardContent>
          </Card>
        ))
      )}
    </div>
  )
}
