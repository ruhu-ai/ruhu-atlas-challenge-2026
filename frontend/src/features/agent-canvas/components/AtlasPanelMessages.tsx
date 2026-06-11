/**
 * Message-stream subcomponents for the Atlas AI panel — the session history
 * overlay, per-message bubbles with their turn-response cards, and the live
 * streaming preview. Extracted from AtlasAIPanel.tsx (RP-4.4); purely
 * presentational.
 */

import { Archive, History, Loader2, Plus, Sparkles, X } from 'lucide-react'

import { Button } from '@/components/atoms/button'
import { cn } from '@/lib/utils'

import type { AtlasSessionResponse, AtlasTurnResponse } from '@/api/services/atlas.service'

import { AtlasPermissionCard } from './AtlasPermissionCard'
import { titleCase } from './atlas-shared'
import type { DisplayMessage } from './atlas-panel-helpers'
import {
  APIDiscoveryResultsCard,
  AttachmentResultsCard,
  BlockersCard,
  BlockingQuestionsCard,
  DependenciesCard,
  ProposedChangesCard,
} from './AtlasPanelCards'

// =============================================================================
// History overlay
// =============================================================================

export function HistoryPanel(props: {
  sessions: AtlasSessionResponse[]
  isLoading: boolean
  currentSessionId: string | null
  onSelectSession: (id: string) => void
  onNewSession: () => void
  onArchiveSession: (id: string) => void
  onClose: () => void
}) {
  const { sessions, isLoading, currentSessionId, onSelectSession, onNewSession, onArchiveSession, onClose } =
    props
  return (
    <div className="absolute inset-0 bg-card z-10 flex flex-col">
      <div className="p-4 border-b bg-muted/50">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <History className="h-5 w-5 text-muted-foreground" />
            <h3 className="font-semibold">Session History</h3>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <div className="p-4">
        <Button onClick={onNewSession} className="w-full" variant="outline">
          <Plus className="h-4 w-4 mr-2" />
          New Session
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 pb-4">
        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : sessions.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground">
            <History className="h-12 w-12 mx-auto mb-2 opacity-30" />
            <p>No sessions yet</p>
            <p className="text-sm">Start chatting with Atlas!</p>
          </div>
        ) : (
          <div className="space-y-2">
            {sessions.map((session) => {
              const isActive = session.session_id === currentSessionId
              return (
                <div
                  key={session.session_id}
                  className={cn(
                    'p-3 rounded-lg border hover:bg-muted/50 cursor-pointer group',
                    isActive && 'border-primary/30 bg-primary/5',
                  )}
                  onClick={() => onSelectSession(session.session_id)}
                >
                  <div className="flex items-start justify-between">
                    <div className="flex-1 min-w-0">
                      <p className="font-medium text-sm truncate">{session.scope}</p>
                      <p className="text-xs text-muted-foreground mt-1">
                        {titleCase(session.status)} · {new Date(session.updated_at).toLocaleDateString()}
                      </p>
                    </div>
                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 w-7 p-0"
                        onClick={(e) => {
                          e.stopPropagation()
                          onArchiveSession(session.session_id)
                        }}
                        title="Archive"
                      >
                        <Archive className="h-3.5 w-3.5 text-muted-foreground" />
                      </Button>
                    </div>
                  </div>
                  {isActive && (
                    <span className="inline-block mt-2 text-xs bg-primary/10 text-primary px-2 py-0.5 rounded">
                      Active
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

// =============================================================================
// Per-message cards
// =============================================================================

function TurnResponseCards(props: {
  response: AtlasTurnResponse
  onAnswerQuestions: (answers: Record<string, string>) => void
  onApprove: () => void
  onReject: () => void
  onApproveChanges: () => void
  onRejectChanges: () => void
  onRequestApply: () => void
  isDeciding: boolean
  isReviewing: boolean
  isLatestTurn: boolean
}) {
  const {
    response,
    onAnswerQuestions,
    onApprove,
    onReject,
    onApproveChanges,
    onRejectChanges,
    onRequestApply,
    isDeciding,
    isReviewing,
    isLatestTurn,
  } = props
  const askForQuestions = response.next_action === 'ask_questions' && response.questions.length > 0
  return (
    <div className="space-y-2 pl-2">
      {askForQuestions && (
        <BlockingQuestionsCard questions={response.questions} onSubmit={onAnswerQuestions} />
      )}
      {response.blockers.length > 0 && <BlockersCard blockers={response.blockers} />}
      <DependenciesCard dependencies={response.dependencies} />
      <AttachmentResultsCard results={response.attachment_ingestion_results} />
      <APIDiscoveryResultsCard results={response.api_discovery_results} />
      <ProposedChangesCard
        response={response}
        isLatestTurn={isLatestTurn}
        isActing={isReviewing}
        onApproveChanges={onApproveChanges}
        onRejectChanges={onRejectChanges}
        onRequestApply={onRequestApply}
      />
      {response.pending_permission_requests.length > 0 && (
        <AtlasPermissionCard
          requests={response.pending_permission_requests}
          proposedChanges={response.proposed_changes}
          onApprove={isLatestTurn ? onApprove : null}
          onReject={isLatestTurn ? onReject : null}
          isDeciding={isDeciding}
        />
      )}
    </div>
  )
}

export function MessageBubble(props: {
  msg: DisplayMessage
  onAnswerQuestions: (answers: Record<string, string>) => void
  onApprove: (response: AtlasTurnResponse) => void
  onReject: (response: AtlasTurnResponse) => void
  onApproveChanges: (response: AtlasTurnResponse) => void
  onRejectChanges: (response: AtlasTurnResponse) => void
  onRequestApply: (response: AtlasTurnResponse) => void
  isDeciding: boolean
  isReviewing: boolean
  isLatestTurn: boolean
}) {
  const { msg } = props
  const { role } = msg
  return (
    <div className="space-y-3">
      <div className={cn('flex', role === 'user' ? 'justify-end' : 'justify-start')}>
        <div
          className={cn(
            'max-w-[85%] rounded-lg px-4 py-2',
            role === 'user'
              ? 'bg-primary text-white'
              : role === 'system'
                ? 'bg-amber-50 text-amber-900 border border-amber-200 dark:bg-amber-900/20 dark:text-amber-100 dark:border-amber-900/30'
                : 'bg-muted text-foreground',
          )}
        >
          {role === 'assistant' && (
            <div className="flex items-center gap-2 mb-1">
              <Sparkles className="h-3 w-3 text-purple-600" />
              <span className="text-xs font-medium text-purple-600">Atlas AI</span>
            </div>
          )}
          <p className="text-sm whitespace-pre-wrap">{msg.content}</p>
        </div>
      </div>

      {msg.turnResponse && (
        <TurnResponseCards
          response={msg.turnResponse}
          onAnswerQuestions={props.onAnswerQuestions}
          onApprove={() => props.onApprove(msg.turnResponse!)}
          onReject={() => props.onReject(msg.turnResponse!)}
          onApproveChanges={() => props.onApproveChanges(msg.turnResponse!)}
          onRejectChanges={() => props.onRejectChanges(msg.turnResponse!)}
          onRequestApply={() => props.onRequestApply(msg.turnResponse!)}
          isDeciding={props.isDeciding}
          isReviewing={props.isReviewing}
          isLatestTurn={props.isLatestTurn}
        />
      )}
    </div>
  )
}

// =============================================================================
// Streaming preview
// =============================================================================

export function StreamingPreview(props: {
  text: string
  tools: Array<{ name: string; status: 'running' | 'done' | 'error' }>
  showSpinner: boolean
}) {
  const { text, tools, showSpinner } = props
  if (!text && tools.length === 0 && !showSpinner) return null
  return (
    <div className="space-y-2">
      {text && (
        <div className="flex justify-start">
          <div className="max-w-[85%] rounded-lg px-4 py-2 bg-muted text-foreground">
            <div className="flex items-center gap-2 mb-1">
              <Sparkles className="h-3 w-3 text-purple-600" />
              <span className="text-xs font-medium text-purple-600">Atlas AI</span>
            </div>
            <p className="text-sm whitespace-pre-wrap">
              {text}
              <span className="inline-block w-1.5 h-3.5 bg-foreground/80 animate-pulse ml-0.5 align-baseline" />
            </p>
          </div>
        </div>
      )}
      {tools.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {tools.map((tool) => (
            <span
              key={tool.name}
              className={cn(
                'text-xs rounded-full px-2 py-1 inline-flex items-center gap-1',
                tool.status === 'running' && 'bg-muted text-muted-foreground',
                tool.status === 'done' && 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300',
                tool.status === 'error' && 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300',
              )}
            >
              {tool.status === 'running' && <Loader2 className="h-3 w-3 animate-spin" />}
              {titleCase(tool.name)}
            </span>
          ))}
        </div>
      )}
      {showSpinner && !text && tools.length === 0 && (
        <div className="flex items-center gap-2 text-muted-foreground text-sm">
          <Loader2 className="h-4 w-4 animate-spin" />
          Atlas is thinking...
        </div>
      )}
    </div>
  )
}
