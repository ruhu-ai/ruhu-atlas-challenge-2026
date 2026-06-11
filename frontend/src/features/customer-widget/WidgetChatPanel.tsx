/**
 * WidgetChatPanel — Text chat UI for the embeddable widget.
 *
 * Features:
 * - Message list with auto-scroll
 * - User bubbles (right) and agent bubbles (left)
 * - Text input with Enter to send, Shift+Enter for newline
 * - Typing indicator while agent is generating
 * - Uses useWidgetChat hook for SSE connection
 */

import { useState, useRef, useEffect, useCallback } from 'react'
import { useWidgetChat } from './useWidgetChat'
import { useWidgetContext } from './WidgetProvider'
import type {
  ArtifactDisambiguationCandidate,
  ChatMessage,
  WidgetAttachment,
  WidgetInteractionStatusItem,
  WidgetVoiceActivity,
} from './widget-types'
import { ActivityPill, hasActiveActivities } from '@/features/chat/components/ActivityPill'
import { ArtifactDisambiguationCard } from './ArtifactDisambiguationCard'
import { PendingToolInvocationsCard } from './PendingToolInvocationsCard'
import { VoiceInteractionPolicyCard } from './VoiceInteractionPolicyCard'

type PendingAttachment = WidgetAttachment

// Spec 23 §Projected Activity / Status Trail — fold interaction-status
// summaries and the latest voice lifecycle event into a single one-line
// banner.  The chat panel is text-only, so voice labels still show here when
// there is no separate voice strip claiming them.
function buildChatStatusBannerText(
  items: WidgetInteractionStatusItem[],
  voice: WidgetVoiceActivity | null,
): string | null {
  const parts: string[] = []
  for (const item of items) {
    if (item.summary) parts.push(item.summary)
  }
  if (voice?.name) {
    const voiceLabel = (() => {
      switch (voice.name) {
        case 'assistant_speaking_started':
          return 'Assistant is speaking'
        case 'assistant_speaking_stopped':
          return 'Assistant finished speaking'
        case 'assistant_interrupted':
          return 'Assistant was interrupted'
        case 'user_barged_in':
          return 'You interrupted the assistant'
        case 'interruption_detected':
          return 'Interruption detected'
        default:
          return null
      }
    })()
    if (voiceLabel) parts.push(voiceLabel)
  }
  if (parts.length === 0) return null
  return parts.join(' · ')
}

export function WidgetChatPanel() {
  const {
    config,
    session,
    createSession,
    error,
    uploadAttachment,
    interactionStatus = [],
    voiceActivity = null,
    voiceInteractionPolicy = null,
  } = useWidgetContext()
  const statusBannerText = buildChatStatusBannerText(interactionStatus, voiceActivity)
  const {
    messages,
    activities,
    isTyping,
    sendMessage,
    confirmPendingToolInvocation,
    cancelPendingToolInvocation,
    dismissActivity,
    isConnected,
  } = useWidgetChat()
  const [inputText, setInputText] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [isUploadingAttachment, setIsUploadingAttachment] = useState(false)
  const [busyInvocationId, setBusyInvocationId] = useState<string | null>(null)
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([])
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, activities, isTyping])

  // Auto-create session if needed
  useEffect(() => {
    if (!session) {
      createSession(config.mode === 'multimodal' ? 'multimodal' : 'chat')
    }
  }, [session, createSession, config.mode])

  const handleSend = useCallback(async () => {
    const text = inputText.trim()
    const hasAttachments = pendingAttachments.length > 0
    if ((!text && !hasAttachments) || isSending) return

    setInputText('')
    setIsSending(true)
    try {
      await sendMessage(text, pendingAttachments)
      setPendingAttachments([])
    } catch {
      // sendMessage already marked the optimistic message as failed and
      // surfaced an error toast — swallow here so the unhandled-rejection
      // doesn't bubble to the global handler.
    } finally {
      setIsSending(false)
      inputRef.current?.focus()
    }
  }, [inputText, isSending, pendingAttachments, sendMessage])

  /**
   * Resend the original content of a previously-failed user message.
   * Replays the same text + attachments + metadata so the kernel sees a
   * legitimate retry. The dedupe key is regenerated on the WidgetProvider
   * side per-call, so the kernel processes this as a new logical send
   * (the prior failed attempt never reached the kernel — by definition
   * it failed at the HTTP layer).
   */
  const handleRetryFailed = useCallback(async (msg: ChatMessage) => {
    if (msg.role !== 'user' || msg.status !== 'failed' || isSending) return
    setIsSending(true)
    try {
      await sendMessage(msg.content, msg.attachments ?? [])
    } catch {
      // sendMessage already updates message status — no extra handling.
    } finally {
      setIsSending(false)
    }
  }, [isSending, sendMessage])

  const handleAttachmentFiles = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0 || isUploadingAttachment) return
    setIsUploadingAttachment(true)
    try {
      const results = await Promise.allSettled(
        Array.from(files).map(async (file): Promise<PendingAttachment> => {
          const result = await uploadAttachment(file)
          return {
            attachment_id: result.attachment_id,
            source: result.source,
            kind: result.kind,
            filename: result.filename,
            content_type: result.content_type,
            size_bytes: result.size_bytes,
            scan_status: result.scan_status,
            extraction_status: result.extraction_status,
          }
        }),
      )
      const uploaded = results
        .filter((result): result is PromiseFulfilledResult<PendingAttachment> => result.status === 'fulfilled')
        .map((result) => result.value)
      if (uploaded.length > 0) {
        setPendingAttachments((prev) => [...prev, ...uploaded])
      }
      // WidgetProvider already surfaces user-facing errors per failed upload.
    } finally {
      setIsUploadingAttachment(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }, [isUploadingAttachment, uploadAttachment])

  const removePendingAttachment = useCallback((attachmentId: string) => {
    setPendingAttachments((prev) => prev.filter((item) => item.attachment_id !== attachmentId))
  }, [])

  const handleArtifactSelection = useCallback(async (candidate: ArtifactDisambiguationCandidate) => {
    if (isSending) return
    setIsSending(true)
    try {
      await sendMessage(candidate.reply_text || candidate.title, [], {
        artifact_id: candidate.artifact_id,
      })
    } finally {
      setIsSending(false)
    }
  }, [isSending, sendMessage])

  const handleConfirmInvocation = useCallback(async (invocationId: string) => {
    if (busyInvocationId) return
    setBusyInvocationId(invocationId)
    try {
      await confirmPendingToolInvocation(invocationId)
    } finally {
      setBusyInvocationId(null)
    }
  }, [busyInvocationId, confirmPendingToolInvocation])

  const handleCancelInvocation = useCallback(async (invocationId: string) => {
    if (busyInvocationId) return
    setBusyInvocationId(invocationId)
    try {
      await cancelPendingToolInvocation(invocationId)
    } finally {
      setBusyInvocationId(null)
    }
  }, [busyInvocationId, cancelPendingToolInvocation])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleSend()
      }
    },
    [handleSend],
  )

  const formatTime = (date: Date) => {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }

  // Welcome state — no messages yet
  if (!session) {
    return (
      <div className="widget-body">
        <div className="widget-center-state">
          <div className="widget-connecting-ring" style={{ color: config.primaryColor }} />
          <div className="widget-center-title">Connecting...</div>
        </div>
      </div>
    )
  }

  return (
    <>
      {error && <div className="widget-error">{error}</div>}
      {statusBannerText && (
        <div className="widget-status-banner" role="status">
          {statusBannerText}
        </div>
      )}
      <PendingToolInvocationsCard
        invocations={session.pendingToolInvocations || []}
        onConfirm={handleConfirmInvocation}
        onCancel={handleCancelInvocation}
        busyInvocationId={busyInvocationId}
      />
      <VoiceInteractionPolicyCard policy={voiceInteractionPolicy} />
      <div className="widget-body">
        <div className="chat-messages">
          {messages.map((msg) => {
            const isFailedUserMessage = msg.role === 'user' && msg.status === 'failed'
            return (
              <div
                key={msg.id}
                className={`chat-bubble ${msg.role}${isFailedUserMessage ? ' chat-bubble--failed' : ''}`}
                style={
                  isFailedUserMessage
                    ? { opacity: 0.7, borderLeft: '3px solid #d93b3b', paddingLeft: '8px' }
                    : undefined
                }
              >
                <div>{msg.content}</div>
                {msg.attachments && msg.attachments.length > 0 && (
                  <div className="widget-attachment-list">
                    {msg.attachments.map((attachment) => (
                      <div key={attachment.attachment_id} className="widget-attachment-card">
                        <div className="widget-attachment-card__name">
                          {attachment.filename || 'Attachment'}
                        </div>
                        <div className="widget-attachment-card__meta">
                          {(attachment.kind || 'file').toUpperCase()}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
                <ArtifactDisambiguationCard
                  message={msg}
                  onSelect={handleArtifactSelection}
                />
                {isFailedUserMessage && (
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px',
                      marginTop: '6px',
                      fontSize: '0.7rem',
                      color: '#d93b3b',
                    }}
                  >
                    <span aria-hidden="true">⚠</span>
                    <span style={{ flex: 1 }}>
                      {msg.failureReason || 'Failed to send.'}
                    </span>
                    <button
                      type="button"
                      onClick={() => { void handleRetryFailed(msg) }}
                      disabled={isSending}
                      aria-label="Retry sending message"
                      style={{
                        background: 'transparent',
                        border: '1px solid #d93b3b',
                        color: '#d93b3b',
                        borderRadius: '4px',
                        padding: '2px 8px',
                        fontSize: '0.7rem',
                        cursor: isSending ? 'not-allowed' : 'pointer',
                      }}
                    >
                      Retry
                    </button>
                  </div>
                )}
                <div
                  style={{
                    fontSize: '0.65rem',
                    opacity: 0.6,
                    marginTop: '4px',
                    textAlign: msg.role === 'user' ? 'right' : 'left',
                  }}
                >
                  {formatTime(msg.timestamp)}
                </div>
              </div>
            )
          })}

          {Array.from(activities.values()).map((activity) => (
            <ActivityPill
              key={activity.activityId}
              activity={activity}
              onExpire={dismissActivity}
            />
          ))}

          {isTyping && !hasActiveActivities(activities.values()) && (
            <div className="typing-indicator">
              <div className="typing-dot" />
              <div className="typing-dot" />
              <div className="typing-dot" />
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Input bar */}
      <div className="chat-input-bar">
        {pendingAttachments.length > 0 && (
          <div className="widget-pending-attachments">
            {pendingAttachments.map((attachment) => (
              <div key={attachment.attachment_id} className="widget-pending-attachment">
                <span>{attachment.filename || 'Attachment'}</span>
                <button
                  type="button"
                  className="widget-pending-attachment__remove"
                  onClick={() => removePendingAttachment(attachment.attachment_id)}
                  aria-label="Remove attachment"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
        <input
          ref={fileInputRef}
          type="file"
          style={{ display: 'none' }}
          multiple
          onChange={(e) => void handleAttachmentFiles(e.target.files)}
        />
        <button
          type="button"
          className="chat-attach-btn"
          onClick={() => fileInputRef.current?.click()}
          disabled={!isConnected || isUploadingAttachment}
          aria-label="Attach a document"
        >
          {isUploadingAttachment ? (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2v4" />
              <path d="M12 18v4" />
              <path d="m4.93 4.93 2.83 2.83" />
              <path d="m16.24 16.24 2.83 2.83" />
              <path d="M2 12h4" />
              <path d="M18 12h4" />
              <path d="m4.93 19.07 2.83-2.83" />
              <path d="m16.24 7.76 2.83-2.83" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21.44 11.05 12.25 20.24a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66L9.4 17.43a2 2 0 0 1-2.83-2.83l8.49-8.48" />
            </svg>
          )}
        </button>
        <textarea
          ref={inputRef}
          className="chat-input"
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type a message..."
          rows={1}
          disabled={!isConnected}
        />
        <button
          className="chat-send-btn"
          style={{
            background: `linear-gradient(135deg, ${config.primaryColor}, ${config.accentColor})`,
          }}
          onClick={handleSend}
          disabled={(!inputText.trim() && pendingAttachments.length === 0) || isSending || !isConnected || isUploadingAttachment}
          aria-label="Send message"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="22" y1="2" x2="11" y2="13" />
            <polygon points="22 2 15 22 11 13 2 9 22 2" />
          </svg>
        </button>
      </div>
    </>
  )
}
