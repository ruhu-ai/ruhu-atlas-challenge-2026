/**
 * Citations Service
 *
 * Backed by:
 *   GET  /conversations/:id/citations         — read citations for a conversation
 *   POST /conversations/:id/analysis-sweep    — fill the agent's analysis_schema
 */

import { apiClient } from '../client'

export type CitationSource =
  | 'deterministic'
  | 'classifier'
  | 'extractor'
  | 'tool'
  | 'llm_proposed'
  | 'user_confirmed'
  | 'system'

export interface Citation {
  fact_name: string
  value: unknown | null
  raw_value: unknown | null
  confidence: number | null
  source: CitationSource
  turn_id: string
  step_id: string | null
  transcript_span: [number, number] | null
  source_utterance: string | null
  source_ref: string | null
  evidence: string | null
  replaced_previous: boolean
}

export interface ConversationCitationsResponse {
  conversation_id: string
  citations: Citation[]
}

export interface AnalysisSweepResult {
  conversation_id: string
  variables_total: number
  variables_filled: string[]
  variables_skipped_existing: string[]
  variables_unfilled: string[]
}

export const citationsService = {
  list(conversationId: string): Promise<ConversationCitationsResponse> {
    return apiClient.get<ConversationCitationsResponse>(
      `/conversations/${conversationId}/citations`,
    )
  },
  runAnalysisSweep(conversationId: string): Promise<AnalysisSweepResult> {
    return apiClient.post<AnalysisSweepResult>(
      `/conversations/${conversationId}/analysis-sweep`,
      {},
    )
  },
}
