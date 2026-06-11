/**
 * Atlas AI Panel — slide-in copilot for agent authoring.
 *
 * Wires to the backend Atlas API (sessions/turns/events/permissions/apply
 * model defined in src/ruhu/atlas_api.py). Each chat creates a session under
 * scope='agent_authoring', sends turns via POST /atlas/turns, and streams
 * live tokens/tool-events from GET /atlas/sessions/{id}/events/stream.
 *
 * Turn responses may contain typed delta proposals (ProposedChangesCard),
 * permission requests (AtlasPermissionCard), and blocking questions
 * (BlockingQuestionsCard). Approval routes through the permission-decisions
 * endpoint, then the apply endpoint commits the deltas.
 *
 * Decomposed in RP-4.4: non-rendering logic lives in
 * hooks/useAtlasSession, hooks/useAtlasTurnActions, hooks/useAtlasReadiness
 * and hooks/useAtlasComposer; pure helpers in atlas-panel-helpers.ts; card
 * and message subcomponents in AtlasPanelCards.tsx / AtlasPanelMessages.tsx.
 */

import type { FormEvent } from 'react'
import {
  Sparkles,
  Send,
  Loader2,
  Lightbulb,
  X,
  Paperclip,
  Link,
  MessageSquare,
  Mic,
  ListChecks,
} from 'lucide-react'

import { Button } from '@/components/atoms/button'
import { cn } from '@/lib/utils'

import { useAtlasComposer } from '../hooks/useAtlasComposer'
import { useAtlasSession } from '../hooks/useAtlasSession'
import { useAtlasTurnActions } from '../hooks/useAtlasTurnActions'
import { useAtlasReadiness, type AtlasEvaluationMode } from '../hooks/useAtlasReadiness'

import {
  SUGGESTED_PROMPTS,
  isValidAgentId,
  isUrl,
  buildPastedChipLabel,
  newDisplayMessageId,
} from './atlas-panel-helpers'
import { HistoryPanel, MessageBubble, StreamingPreview } from './AtlasPanelMessages'

// =============================================================================
// Props
// =============================================================================

interface AtlasAIPanelProps {
  isOpen: boolean
  onClose: () => void
  agentId?: string
}

function parseEvaluateCommand(value: string): { mode: AtlasEvaluationMode | null; voiceAudioUri?: string } | null {
  const trimmed = value.trim()
  if (!trimmed.startsWith('/evaluate')) return null
  const parts = trimmed.split(/\s+/).filter(Boolean)
  const rawMode = parts[1]?.toLowerCase()
  const voiceAudioUri = parts.find((part) => part.startsWith('gs://'))
  if (rawMode === 'chat' || rawMode === 'voice' || rawMode === 'cases') {
    return { mode: rawMode, voiceAudioUri }
  }
  return { mode: null, voiceAudioUri }
}

// =============================================================================
// Main panel
// =============================================================================

export function AtlasAIPanel({ isOpen, onClose, agentId }: AtlasAIPanelProps) {
  // Attachments / long-paste
  const {
    input,
    setInput,
    selectedFiles,
    setSelectedFiles,
    pastedChunks,
    setPastedChunks,
    fileInputRef,
    handleAttachmentChange,
    removeSelectedFile,
    removePastedChunk,
    handlePaste,
    resetComposer,
  } = useAtlasComposer()

  // Session lifecycle + history + messages + streaming
  const {
    atlasEnabled,
    isTogglingEnabled,
    handleToggleEnabled,
    sessions,
    isLoadingHistory,
    showHistory,
    setShowHistory,
    handleShowHistory,
    handleSelectSession,
    handleNewSession,
    handleArchiveSession,
    refreshHistory,
    currentSessionId,
    setCurrentSessionId,
    setCurrentSession,
    messages,
    setMessages,
    isRunningTurn,
    streamingText,
    streamingTools,
    turnPostRef,
    lastEventSequenceRef,
    messagesEndRef,
    runTurn,
  } = useAtlasSession({ isOpen, agentId, onResetComposer: resetComposer })

  // Turn actions: send / answer questions / review deltas / permissions + apply
  const {
    isDecidingPermissions,
    isReviewingChanges,
    handleSend,
    handleAnswerQuestions,
    handleReviewChanges,
    handleRequestApply,
    handleApprovePermissions,
    handleRejectPermissions,
  } = useAtlasTurnActions({
    agentId,
    isRunningTurn,
    runTurn,
    turnPostRef,
    setMessages,
    currentSessionId,
    input,
    setInput,
    selectedFiles,
    setSelectedFiles,
    pastedChunks,
    setPastedChunks,
  })

  // Readiness evaluation runs
  const {
    isRunningReadiness,
    activeEvaluationLabel,
    handleRunEvaluation,
  } = useAtlasReadiness({
    isOpen,
    agentId,
    setMessages,
    setCurrentSessionId,
    setCurrentSession,
    lastEventSequenceRef,
    refreshHistory,
    providerPolicy: 'deterministic',
    demoCaseSet: false,
  })

  // ===== Render =====

  if (!isOpen) return null
  const latestTurnMessageId = [...messages].reverse().find((msg) => msg.turnResponse)?.id ?? null
  const evaluateCommand = parseEvaluateCommand(input)
  const showEvaluateChips = Boolean(evaluateCommand)

  const runEvaluateMode = async (mode: AtlasEvaluationMode) => {
    if (isRunningReadiness) return
    const parsed = parseEvaluateCommand(input)
    const command = `/evaluate ${mode}${mode === 'voice' && parsed?.voiceAudioUri ? ` ${parsed.voiceAudioUri}` : ''}`
    setInput('')
    setMessages((prev) => [
      ...prev,
      {
        id: newDisplayMessageId('msg'),
        role: 'user',
        content: command,
        timestamp: new Date(),
      },
    ])
    await handleRunEvaluation(mode, { voiceAudioUri: parsed?.voiceAudioUri })
  }

  const handleComposerSubmit = async (event?: FormEvent) => {
    event?.preventDefault()
    const parsed = parseEvaluateCommand(input)
    if (parsed) {
      if (parsed.mode) {
        await runEvaluateMode(parsed.mode)
      }
      return
    }
    await handleSend()
  }

  return (
    <div className="fixed top-0 right-0 w-[480px] h-full bg-card border-l border-border shadow-xl flex flex-col z-50">
      {/* Header */}
      <div className="p-4 border-b bg-gradient-to-br from-primary to-primary/80 text-white">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkles className="h-5 w-5" />
            <h3 className="font-semibold">Atlas</h3>
          </div>
          <div className="flex items-center gap-2">
            {isValidAgentId(agentId) && (
              <button
                onClick={handleShowHistory}
                className="text-sm text-white/80 hover:text-white transition-colors"
              >
                History
              </button>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={onClose}
              className="text-white hover:bg-card/20 -mr-2"
            >
              <X className="h-4 w-4" />
            </Button>
          </div>
        </div>
        <p className="text-sm text-primary-foreground/80 mt-1">
          Your AI-powered agent building copilot
        </p>
      </div>

      {/* History overlay */}
      {showHistory && (
        <HistoryPanel
          sessions={sessions}
          isLoading={isLoadingHistory}
          currentSessionId={currentSessionId}
          onSelectSession={handleSelectSession}
          onNewSession={handleNewSession}
          onArchiveSession={handleArchiveSession}
          onClose={() => setShowHistory(false)}
        />
      )}

      {/* Unsaved-agent gate */}
      {!isValidAgentId(agentId) && !showHistory && (
        <div className="flex-1 flex flex-col items-center justify-center p-8 text-center">
          <div className="w-16 h-16 rounded-full bg-muted flex items-center justify-center mb-4">
            <Sparkles className="h-8 w-8 text-muted-foreground" />
          </div>
          <h4 className="font-medium text-foreground mb-2">Save your agent first</h4>
          <p className="text-sm text-muted-foreground">
            Atlas builds workflows for a specific agent. Save this agent to start using Atlas.
          </p>
        </div>
      )}

      {/* Disabled state */}
      {!atlasEnabled && isValidAgentId(agentId) && !showHistory && (
        <div className="flex-1 flex flex-col items-center justify-center p-8 text-center">
          <div className="w-16 h-16 rounded-full bg-muted flex items-center justify-center mb-4">
            <Sparkles className="h-8 w-8 text-muted-foreground" />
          </div>
          <h4 className="font-medium text-foreground mb-2">Atlas is disabled</h4>
          <p className="text-sm text-muted-foreground mb-4">
            Atlas AI assistant is currently disabled for this agent. Enable it to get help building
            your agent.
          </p>
          <Button onClick={handleToggleEnabled} disabled={isTogglingEnabled}>
            {isTogglingEnabled ? (
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
            ) : (
              <Sparkles className="h-4 w-4 mr-2" />
            )}
            Enable Atlas
          </Button>
        </div>
      )}

      {/* Messages + cards */}
      {atlasEnabled && isValidAgentId(agentId) && !showHistory && (
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.map((msg) => (
            <MessageBubble
              key={msg.id}
              msg={msg}
              onAnswerQuestions={handleAnswerQuestions}
              onApprove={handleApprovePermissions}
              onReject={handleRejectPermissions}
              onApproveChanges={(response) => handleReviewChanges(response, 'approved')}
              onRejectChanges={(response) => handleReviewChanges(response, 'rejected')}
              onRequestApply={handleRequestApply}
              isDeciding={isDecidingPermissions}
              isReviewing={isReviewingChanges}
              isLatestTurn={msg.id === latestTurnMessageId}
            />
          ))}

          <StreamingPreview text={streamingText} tools={streamingTools} showSpinner={isRunningTurn} />

          {isRunningReadiness && (
            <div className="flex items-center gap-2 rounded-md border border-border/70 bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Running {activeEvaluationLabel || 'evaluation'}...
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>
      )}

      {/* Suggested prompts */}
      {atlasEnabled && isValidAgentId(agentId) && !showHistory && messages.length <= 1 && (
        <div className="px-4 py-2 border-t bg-muted/50">
          <p className="text-xs text-muted-foreground mb-2 flex items-center gap-1">
            <Lightbulb className="h-3 w-3" />
            Suggested:
          </p>
          <div className="flex flex-wrap gap-2">
            {SUGGESTED_PROMPTS.map((prompt) => (
              <Button
                key={prompt}
                variant="outline"
                size="sm"
                className="text-xs"
                onClick={() => setInput(prompt)}
              >
                {prompt}
              </Button>
            ))}
          </div>
        </div>
      )}

      {/* Input */}
      {atlasEnabled && isValidAgentId(agentId) && !showHistory && (
        <form onSubmit={handleComposerSubmit} className="p-4 border-t bg-card">
          <input
            ref={fileInputRef}
            type="file"
            accept=".json,.yaml,.yml,.txt,.md,.pdf,.docx,image/*"
            multiple
            className="hidden"
            onChange={handleAttachmentChange}
          />
          {(selectedFiles.length > 0 || pastedChunks.length > 0) && (
            <div className="mb-3 flex flex-wrap gap-2">
              {selectedFiles.map((file) => (
                <button
                  key={`${file.name}-${file.size}-${file.lastModified}`}
                  type="button"
                  onClick={() => removeSelectedFile(file)}
                  className="inline-flex max-w-full items-center gap-1 rounded-md border border-border bg-muted px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground"
                  title="Remove attachment"
                >
                  <Paperclip className="h-3 w-3 flex-shrink-0" />
                  <span className="truncate">{file.name}</span>
                  <X className="h-3 w-3 flex-shrink-0" />
                </button>
              ))}
              {pastedChunks.map((chunk) => (
                <button
                  key={chunk.id}
                  type="button"
                  onClick={() => removePastedChunk(chunk.id)}
                  className="inline-flex max-w-full items-center gap-1 rounded-md border border-border bg-muted px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground"
                  title="Remove pasted content"
                >
                  <span className="truncate">{buildPastedChipLabel(chunk)}</span>
                  <X className="h-3 w-3 flex-shrink-0" />
                </button>
              ))}
            </div>
          )}
          {showEvaluateChips && (
            <div className="mb-3 flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => runEvaluateMode('chat')}
                disabled={isRunningReadiness}
                className="h-8 gap-1.5"
              >
                <MessageSquare className="h-3.5 w-3.5" />
                Chat
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => runEvaluateMode('voice')}
                disabled={isRunningReadiness}
                className="h-8 gap-1.5"
              >
                <Mic className="h-3.5 w-3.5" />
                Voice
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => runEvaluateMode('cases')}
                disabled={isRunningReadiness}
                className="h-8 gap-1.5"
              >
                <ListChecks className="h-3.5 w-3.5" />
                Cases
              </Button>
            </div>
          )}
          <div className="flex gap-2">
            <div className="relative flex-1">
              {isUrl(input) && <Link className="absolute left-2 top-2.5 h-4 w-4 text-primary" />}
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onPaste={handlePaste}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    handleComposerSubmit()
                  } else if (e.key === 'Backspace' && !input && pastedChunks.length > 0) {
                    e.preventDefault()
                    setPastedChunks((prev) => prev.slice(0, -1))
                  }
                }}
                placeholder="Ask Atlas or type /evaluate..."
                rows={2}
                disabled={isRunningTurn}
                className={cn(
                  'w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary disabled:opacity-50',
                  isUrl(input) && 'pl-8',
                )}
              />
            </div>
            <Button
              type="button"
              variant="outline"
              onClick={() => fileInputRef.current?.click()}
              disabled={isRunningTurn}
              size="sm"
              aria-label="Attach file"
            >
              <Paperclip className="h-4 w-4" />
            </Button>
            <Button
              type="submit"
              disabled={(!input.trim() && selectedFiles.length === 0 && pastedChunks.length === 0) || isRunningTurn}
              size="sm"
            >
              {isRunningTurn ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}
            </Button>
          </div>
        </form>
      )}
    </div>
  )
}

export { ApplyResultBanner } from './AtlasPanelCards'
