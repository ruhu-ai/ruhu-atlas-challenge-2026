/**
 * WidgetUnifiedPanel — Single-screen multimodal UI for the embeddable widget.
 *
 * Chat messages and voice transcripts flow in ONE continuous stream.
 * The input bar has a mic button to start/end voice alongside text.
 * No tab switching — truly unified like the UnifiedTestInterface.
 */

import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { useWidgetChat } from './useWidgetChat'
import { useWidgetVoice } from './useWidgetVoice'
import { useWidgetContext } from './WidgetProvider'
import type {
  ArtifactDisambiguationCandidate,
  ChatMessage,
  WidgetAttachment,
  WidgetBrowserTaskProjection,
  WidgetInteractionStatusItem,
  WidgetVoiceActivity,
} from './widget-types'
import { ActivityPill, hasActiveActivities } from '@/features/chat/components/ActivityPill'
import { ArtifactDisambiguationCard } from './ArtifactDisambiguationCard'
import { PendingToolInvocationsCard } from './PendingToolInvocationsCard'
import { VoiceInteractionPolicyCard } from './VoiceInteractionPolicyCard'

interface PendingAttachment extends WidgetAttachment {
  originalFile?: File
}

type TimelineItem =
    | {
      kind: 'chat'
      id: string
      role: 'user' | 'assistant'
      content: string
      timestamp: Date
      done: boolean
      attachments?: WidgetAttachment[]
      metadata?: ChatMessage['metadata']
    }
  | {
      kind: 'voice'
      id: string
      speaker: 'user' | 'agent'
      text: string
      timestamp: Date
      isFinal: boolean
    }

// ── SVG icons ───────────────────────────────────────────────────────────────

// Shown when idle — click to start a voice call
const AudioWaveIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="2"  y1="10" x2="2"  y2="14" />
    <line x1="6"  y1="6"  x2="6"  y2="18" />
    <line x1="10" y1="3"  x2="10" y2="21" />
    <line x1="14" y1="6"  x2="14" y2="18" />
    <line x1="18" y1="9"  x2="18" y2="15" />
    <line x1="22" y1="11" x2="22" y2="13" />
  </svg>
)

// Shown during active call (unmuted) — click to mute
const MicIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z" />
    <path d="M19 10v2a7 7 0 01-14 0v-2" />
    <line x1="12" y1="19" x2="12" y2="23" />
    <line x1="8" y1="23" x2="16" y2="23" />
  </svg>
)

// Shown during active call (muted) — click to unmute
const MicOffIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="1" y1="1" x2="23" y2="23" />
    <path d="M9 9v3a3 3 0 005.12 2.12M15 9.34V4a3 3 0 00-5.94-.6" />
    <path d="M17 16.95A7 7 0 015 12v-2m14 0v2c0 .64-.09 1.27-.26 1.86" />
    <line x1="12" y1="19" x2="12" y2="23" />
    <line x1="8" y1="23" x2="16" y2="23" />
  </svg>
)

const SendIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="22" y1="2" x2="11" y2="13" />
    <polygon points="22 2 15 22 11 13 2 9 22 2" />
  </svg>
)

const PaperclipIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21.44 11.05 12.25 20.24a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66L9.4 17.43a2 2 0 01-2.83-2.83l8.49-8.48" />
  </svg>
)

const SpinnerIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 2v4M12 18v4m-6.93-2.07 2.83-2.83M16.1 7.9l2.83-2.83M2 12h4M18 12h4m-4.93 6.93-2.83-2.83M7.9 7.9 5.07 5.07" />
  </svg>
)

const XIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
    <line x1="18" y1="6" x2="6" y2="18" />
    <line x1="6" y1="6" x2="18" y2="18" />
  </svg>
)

// Spec 23 §Voice Mechanics — deterministic mapping from voice lifecycle event
// name to a short user-safe label.  Mirrors the logic in the vanilla JS
// widget_embed_script to keep the React widget and embed surface in sync.
function voiceActivityLabel(event: WidgetVoiceActivity | null): string | null {
  if (!event?.name) return null
  switch (event.name) {
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
}

function buildStatusBannerText(
  items: WidgetInteractionStatusItem[],
  voice: WidgetVoiceActivity | null,
): string | null {
  const parts: string[] = []
  for (const item of items) {
    if (item.summary) parts.push(item.summary)
  }
  const voiceText = voiceActivityLabel(voice)
  if (voiceText) parts.push(voiceText)
  if (parts.length === 0) return null
  return parts.join(' · ')
}

function WidgetBrowserTasksSummary({
  tasks,
  busyTaskId,
  onApprove,
  onDeny,
  onCancel,
}: {
  tasks: WidgetBrowserTaskProjection[]
  busyTaskId: string | null
  onApprove: (task: WidgetBrowserTaskProjection) => void
  onDeny: (task: WidgetBrowserTaskProjection) => void
  onCancel: (task: WidgetBrowserTaskProjection) => void
}) {
  if (!tasks.length) return null
  return (
    <div className="widget-browser-task-stack">
      {tasks.map((task) => {
        const approvalPending = task.approval?.state === 'pending'
        const busy = busyTaskId === task.task_id
        return (
          <div className="widget-browser-task-card" key={task.task_id}>
            <div className="widget-browser-task-head">
              <div>
                <div className="widget-browser-task-title">{task.title}</div>
                {task.domain_label && (
                  <div className="widget-browser-task-domain">{task.domain_label}</div>
                )}
              </div>
              <span className="widget-browser-task-state">{task.state.replace(/_/g, ' ')}</span>
            </div>
            {task.latest_progress && (
              <div className="widget-browser-task-progress">{task.latest_progress}</div>
            )}
            {approvalPending && task.approval && (
              <div className="widget-browser-task-approval">
                <div>{task.approval.prompt}</div>
                {task.approval.credential_labels.length > 0 && (
                  <div className="widget-browser-task-meta">
                    {task.approval.credential_labels.join(' · ')}
                  </div>
                )}
                {task.approval.expires_at && (
                  <div className="widget-browser-task-meta">
                    Expires {new Date(task.approval.expires_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </div>
                )}
              </div>
            )}
            {task.artifacts.length > 0 && (
              <div className="widget-browser-task-artifacts">
                {task.artifacts.slice(0, 3).map((artifact) => (
                  artifact.public_widget_download_url ? (
                    <a
                      key={artifact.artifact_id}
                      href={artifact.public_widget_download_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {artifact.filename || 'Artifact'}
                    </a>
                  ) : (
                    <span key={artifact.artifact_id}>{artifact.filename || 'Artifact'}</span>
                  )
                ))}
              </div>
            )}
            {(approvalPending || task.cancellable) && (
              <div className="widget-browser-task-actions">
                {approvalPending && (
                  <>
                    <button
                      type="button"
                      className="widget-browser-task-btn"
                      onClick={() => onApprove(task)}
                      disabled={busy}
                    >
                      Allow once
                    </button>
                    <button
                      type="button"
                      className="widget-browser-task-btn secondary"
                      onClick={() => onDeny(task)}
                      disabled={busy}
                    >
                      Deny
                    </button>
                  </>
                )}
                {task.cancellable && (
                  <button
                    type="button"
                    className="widget-browser-task-btn secondary"
                    onClick={() => onCancel(task)}
                    disabled={busy}
                  >
                    Cancel
                  </button>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── Component ────────────────────────────────────────────────────────────────

export function WidgetUnifiedPanel() {
  const {
    config,
    session,
    createSession,
    endSession,
    error,
    uploadAttachment,
    interactionStatus = [],
    browserTasks = [],
    approveBrowserTask,
    denyBrowserTask,
    cancelBrowserTask,
    voiceActivity = null,
    voiceInteractionPolicy = null,
  } = useWidgetContext()
  const {
    messages,
    activities,
    isTyping,
    conversationState,
    sendMessage,
    confirmPendingToolInvocation,
    cancelPendingToolInvocation,
    appendLocalUserMessage,
    dismissActivity,
    isConnected,
  } = useWidgetChat()
  const {
    callState,
    isMuted,
    audioLevel,
    isAgentSpeaking,
    transcripts,
    voiceError,
    startCall,
    endCall,
    sendText,
    sendAttachmentFile,
    toggleMute,
  } = useWidgetVoice()

  const [inputText, setInputText] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [isUploadingAttachment, setIsUploadingAttachment] = useState(false)
  const [busyInvocationId, setBusyInvocationId] = useState<string | null>(null)
  const [busyBrowserTaskId, setBusyBrowserTaskId] = useState<string | null>(null)
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([])

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const conversationEnded = conversationState?.status === 'ended'
  const shouldUseVoiceIngress = callState === 'active'
  const voiceIsActive = callState === 'active' || callState === 'connecting'
  const assistantVoiceActive = voiceIsActive && (isTyping || isAgentSpeaking)

  // Merge chat messages + voice transcripts into one sorted timeline
  const timeline = useMemo((): TimelineItem[] => {
    const chatItems: TimelineItem[] = messages.map((m) => ({
      kind: 'chat',
      id: m.id,
      role: m.role as 'user' | 'assistant',
      content: m.content,
      timestamp: m.timestamp,
      done: m.done,
      attachments: m.attachments,
      metadata: m.metadata,
    }))
    const voiceItems: TimelineItem[] = transcripts
      .filter((t) => t.speaker === 'user' || !t.isFinal)
      .map((t) => ({
        kind: 'voice',
        id: t.id,
        speaker: t.speaker,
        text: t.text,
        timestamp: t.timestamp,
        isFinal: t.isFinal,
      }))
    return [...chatItems, ...voiceItems].sort(
      (a, b) => a.timestamp.getTime() - b.timestamp.getTime(),
    )
  }, [messages, transcripts])

  // Auto-scroll on new content
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [timeline, activities, isTyping])

  // Auto-create session
  useEffect(() => {
    if (!session) createSession('multimodal')
  }, [session, createSession])

  // ── Send text message ──────────────────────────────────────────────────────

  const defaultAttachmentPrompt = useCallback((attachments: WidgetAttachment[]) => {
    if (attachments.length === 1) {
      return `Please review the attached file "${attachments[0].filename || 'attachment'}" and respond naturally.`
    }
    return `Please review the ${attachments.length} attached files and respond naturally.`
  }, [])

  const localAttachmentMessage = useCallback((attachments: WidgetAttachment[]) => {
    if (attachments.length === 1) {
      return `Shared attachment: ${attachments[0].filename || 'attachment'}`
    }
    return `Shared ${attachments.length} attachments.`
  }, [])

  const handleSend = useCallback(async () => {
    const text = inputText.trim()
    const hasAttachments = pendingAttachments.length > 0
    if ((!text && !hasAttachments) || isSending || conversationEnded) return
    setInputText('')
    setIsSending(true)
    try {
      const outboundText = text || defaultAttachmentPrompt(pendingAttachments)
      const localMessageText = text || localAttachmentMessage(pendingAttachments)

      if (shouldUseVoiceIngress) {
        const imageAttachments = pendingAttachments.filter((attachment) =>
          (attachment.kind === 'image' || String(attachment.content_type || '').startsWith('image/')) &&
          attachment.originalFile,
        )
        for (const attachment of imageAttachments) {
          if (attachment.originalFile) {
            await sendAttachmentFile(attachment.originalFile, attachment)
          }
        }
        await sendText(outboundText, {
          attachmentIds: pendingAttachments.map((attachment) => attachment.attachment_id),
        })
        appendLocalUserMessage(localMessageText, pendingAttachments)
        setPendingAttachments([])
      } else {
        await sendMessage(outboundText, pendingAttachments)
        setPendingAttachments([])
      }
    } catch {
      setInputText(text)
    } finally {
      setIsSending(false)
      inputRef.current?.focus()
    }
  }, [
    appendLocalUserMessage,
    defaultAttachmentPrompt,
    conversationEnded,
    inputText,
    isSending,
    localAttachmentMessage,
    pendingAttachments,
    sendMessage,
    sendAttachmentFile,
    sendText,
    shouldUseVoiceIngress,
  ])

  const handleArtifactSelection = useCallback(async (candidate: ArtifactDisambiguationCandidate) => {
    if (isSending || conversationEnded) return
    setIsSending(true)
    try {
      await sendMessage(candidate.reply_text || candidate.title, [], {
        artifact_id: candidate.artifact_id,
      })
    } finally {
      setIsSending(false)
    }
  }, [conversationEnded, isSending, sendMessage])

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

  const handleApproveBrowserTask = useCallback(async (task: WidgetBrowserTaskProjection) => {
    if (!task.approval || busyBrowserTaskId) return
    setBusyBrowserTaskId(task.task_id)
    try {
      await approveBrowserTask(task.task_id, task.approval.approval_id)
    } finally {
      setBusyBrowserTaskId(null)
    }
  }, [approveBrowserTask, busyBrowserTaskId])

  const handleDenyBrowserTask = useCallback(async (task: WidgetBrowserTaskProjection) => {
    if (!task.approval || busyBrowserTaskId) return
    setBusyBrowserTaskId(task.task_id)
    try {
      await denyBrowserTask(task.task_id, task.approval.approval_id)
    } finally {
      setBusyBrowserTaskId(null)
    }
  }, [busyBrowserTaskId, denyBrowserTask])

  const handleCancelBrowserTask = useCallback(async (task: WidgetBrowserTaskProjection) => {
    if (busyBrowserTaskId || !task.cancellable) return
    setBusyBrowserTaskId(task.task_id)
    try {
      await cancelBrowserTask(task.task_id)
    } finally {
      setBusyBrowserTaskId(null)
    }
  }, [busyBrowserTaskId, cancelBrowserTask])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleSend()
      }
    },
    [handleSend],
  )

  // ── Attachments ────────────────────────────────────────────────────────────

  const handleAttachmentFiles = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0 || isUploadingAttachment) return
      setIsUploadingAttachment(true)
      try {
        const results = await Promise.allSettled(
          Array.from(files).map(async (file): Promise<PendingAttachment> => {
            const r = await uploadAttachment(file, callState === 'active' ? 'voice' : 'widget')
            return {
              attachment_id: r.attachment_id,
              source: r.source,
              kind: r.kind,
              filename: r.filename,
              content_type: r.content_type,
              size_bytes: r.size_bytes,
              scan_status: r.scan_status,
              extraction_status: r.extraction_status,
              originalFile: file,
            }
          }),
        )
        const uploaded = results
          .filter(
            (r): r is PromiseFulfilledResult<PendingAttachment> =>
              r.status === 'fulfilled',
          )
          .map((r) => r.value)
        if (uploaded.length > 0) {
          setPendingAttachments((prev) => [...prev, ...uploaded])
        }
      } finally {
        setIsUploadingAttachment(false)
        if (fileInputRef.current) fileInputRef.current.value = ''
      }
    },
    [callState, isUploadingAttachment, uploadAttachment],
  )

  const removePendingAttachment = useCallback((id: string) => {
    setPendingAttachments((prev) => prev.filter((a) => a.attachment_id !== id))
  }, [])

  // ── Voice / Mic ────────────────────────────────────────────────────────────

  const handleMicClick = useCallback(async () => {
    if (conversationEnded) return
    if (callState === 'idle') {
      await startCall()
    } else if (callState === 'active') {
      toggleMute()   // mute/unmute while on call; "End" button in strip ends the call
    }
  }, [callState, conversationEnded, startCall, toggleMute])

  const handleRestartConversation = useCallback(async () => {
    setInputText('')
    setPendingAttachments([])
    if (callState === 'active') {
      await endCall()
    }
    await endSession()
    await createSession('multimodal')
  }, [callState, createSession, endCall, endSession])

  const formatTime = (date: Date) =>
    date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

  // ── Connecting splash ──────────────────────────────────────────────────────

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

  // ── Render ─────────────────────────────────────────────────────────────────

  // Combine interaction-status summaries + voice lifecycle into a single banner.
  // Voice-strip already surfaces "Agent speaking" / "Connecting" while a call is
  // active, so suppress the redundant voice activity label in that case.
  const statusBannerText = buildStatusBannerText(
    interactionStatus,
    voiceIsActive ? null : voiceActivity,
  )

  return (
    <>
      {error && <div className="widget-error">{error}</div>}
      {voiceError && <div className="widget-error">{voiceError}</div>}
      {statusBannerText && (
        <div className="widget-status-banner" role="status">
          {statusBannerText}
        </div>
      )}
      <PendingToolInvocationsCard
        invocations={session?.pendingToolInvocations || []}
        onConfirm={handleConfirmInvocation}
        onCancel={handleCancelInvocation}
        busyInvocationId={busyInvocationId}
      />
      <VoiceInteractionPolicyCard policy={voiceInteractionPolicy} />
      <WidgetBrowserTasksSummary
        tasks={browserTasks}
        busyTaskId={busyBrowserTaskId}
        onApprove={handleApproveBrowserTask}
        onDeny={handleDenyBrowserTask}
        onCancel={handleCancelBrowserTask}
      />

      {/* Voice active status strip */}
      {voiceIsActive && (
        <div
          className="widget-voice-strip"
          style={{
            background: `${config.primaryColor}12`,
            borderColor: `${config.primaryColor}28`,
          }}
        >
          <div
              className={`widget-voice-strip-dot${
              callState === 'connecting'
                ? ' pulse'
                : assistantVoiceActive
                  ? ' agent'
                  : audioLevel > 15
                    ? ' user'
                    : ''
            }`}
            style={{ background: config.primaryColor }}
          />
          <span className="widget-voice-strip-label" style={{ color: config.primaryColor }}>
            {callState === 'connecting'
              ? 'Connecting voice...'
              : assistantVoiceActive
                ? 'Agent speaking'
                : audioLevel > 15
                  ? 'You are speaking'
                  : 'Listening...'}
          </span>
        </div>
      )}

      {/* Unified message stream */}
      <div className="widget-body">
        <div className="chat-messages">
          {timeline.map((item) => {
            const isUser =
              item.kind === 'chat' ? item.role === 'user' : item.speaker === 'user'
            const text = item.kind === 'chat' ? item.content : item.text
            const isVoicePartial = item.kind === 'voice' && !item.isFinal

            return (
              <div
                key={item.id}
                className={`chat-bubble ${isUser ? 'user' : 'assistant'}`}
                style={undefined}
              >
                <div style={isVoicePartial ? { opacity: 0.8, fontStyle: 'italic' } : undefined}>
                  {text}
                  {isVoicePartial ? '…' : ''}
                </div>
                {item.kind === 'chat' &&
                  item.attachments &&
                  item.attachments.length > 0 && (
                    <div className="widget-attachment-list">
                      {item.attachments.map((att) => (
                        <div key={att.attachment_id} className="widget-attachment-card">
                          <div className="widget-attachment-card__name">
                            {att.filename || 'Attachment'}
                          </div>
                          <div className="widget-attachment-card__meta">
                            {(att.kind || 'file').toUpperCase()}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                {item.kind === 'chat' && (
                  <ArtifactDisambiguationCard
                    message={{
                      id: item.id,
                      role: item.role,
                      content: item.content,
                      timestamp: item.timestamp,
                      done: item.done,
                      attachments: item.attachments,
                      metadata: item.metadata,
                    }}
                    onSelect={handleArtifactSelection}
                  />
                )}
                <div
                  style={{
                    fontSize: '0.65rem',
                    opacity: 0.6,
                    marginTop: '4px',
                    textAlign: isUser ? 'right' : 'left',
                  }}
                >
                  {formatTime(item.timestamp)}
                </div>
              </div>
            )
          })}

          {/* Activity pills */}
          {Array.from(activities.values()).map((activity) => (
            <ActivityPill
              key={activity.activityId}
              activity={activity}
              onExpire={dismissActivity}
            />
          ))}

          {/* Typing indicator */}
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

      {/* Unified input bar */}
      <div className="chat-input-bar">
        {conversationEnded && (
          <div
            style={{
              width: '100%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: '12px',
              padding: '10px 12px',
              borderRadius: '14px',
              background: '#fff7ed',
              border: '1px solid #fdba74',
              color: '#9a3412',
              marginBottom: '10px',
            }}
          >
            <div>
              <div style={{ fontSize: '0.9rem', fontWeight: 700 }}>Conversation ended</div>
              <div style={{ fontSize: '0.78rem', opacity: 0.85 }}>
                Start a new conversation to continue.
              </div>
            </div>
            <button
              type="button"
              className="chat-send-btn"
              style={{
                background: `linear-gradient(135deg, ${config.primaryColor}, ${config.accentColor})`,
                flexShrink: 0,
              }}
              onClick={() => void handleRestartConversation()}
            >
              New chat
            </button>
          </div>
        )}

        {pendingAttachments.length > 0 && (
          <div className="widget-pending-attachments">
            {pendingAttachments.map((att) => (
              <div key={att.attachment_id} className="widget-pending-attachment">
                <span>{att.filename || 'Attachment'}</span>
                <button
                  type="button"
                  className="widget-pending-attachment__remove"
                  onClick={() => removePendingAttachment(att.attachment_id)}
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

        {/* Attach */}
        <button
          type="button"
          className="chat-attach-btn"
          onClick={() => fileInputRef.current?.click()}
          disabled={conversationEnded || !isConnected || isUploadingAttachment}
          aria-label="Attach a document"
        >
          {isUploadingAttachment ? SpinnerIcon : PaperclipIcon}
        </button>

        {/* Text input */}
        <textarea
          ref={inputRef}
          className="chat-input"
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            conversationEnded
              ? 'Conversation ended'
              : callState === 'connecting'
              ? 'Connecting...'
              : callState === 'active'
                ? 'Type, speak, or share files...'
                : 'Type a message...'
          }
          rows={1}
          disabled={conversationEnded || !isConnected || callState === 'connecting'}
        />

        {/* Mic button */}
        <button
          type="button"
          className={`widget-mic-btn${
            callState === 'connecting'
              ? ' connecting'
              : callState === 'active'
                ? audioLevel > 15
                  ? ' speaking'
                  : ' active'
                : ''
          }`}
          style={{ color: config.primaryColor, borderColor: config.primaryColor }}
          onClick={() => void handleMicClick()}
          disabled={conversationEnded || callState === 'connecting' || !isConnected}
          aria-label={
            callState === 'active'
              ? isMuted ? 'Unmute microphone' : 'Mute microphone'
              : 'Start voice call'
          }
        >
          {callState === 'connecting'
            ? SpinnerIcon
            : callState === 'active'
              ? isMuted ? MicOffIcon : MicIcon
              : AudioWaveIcon}
        </button>

        {/* End call — only visible during an active or connecting call */}
        {(callState === 'active' || callState === 'connecting') && (
          <button
            type="button"
            className="widget-end-call-btn"
            style={{ background: config.primaryColor }}
            onClick={() => void endCall()}
            aria-label="End call"
          >
            {XIcon}
          </button>
        )}

        {/* Send */}
        <button
          className="chat-send-btn"
          style={{
            background: `linear-gradient(135deg, ${config.primaryColor}, ${config.accentColor})`,
          }}
          onClick={handleSend}
          disabled={
            conversationEnded
            || (!inputText.trim() && pendingAttachments.length === 0)
            || isSending
            || !isConnected
            || isUploadingAttachment
          }
          aria-label="Send message"
        >
          {SendIcon}
        </button>
      </div>
    </>
  )
}
