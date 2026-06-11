/**
 * WidgetVoicePanel — Voice call UI for the embeddable widget.
 *
 * Uses:
 * - useWidgetVoice hook (lazy-loaded LiveKit)
 * - WidgetProvider context for session management
 * - Inline CSS classes from widget-styles.ts (no Tailwind dependency)
 */

import { useEffect, useRef } from 'react'
import { useWidgetVoice } from './useWidgetVoice'
import { useWidgetContext } from './WidgetProvider'

const MicIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z" />
    <path d="M19 10v2a7 7 0 01-14 0v-2" />
    <line x1="12" y1="19" x2="12" y2="23" />
    <line x1="8" y1="23" x2="16" y2="23" />
  </svg>
)

const MicOffIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="1" y1="1" x2="23" y2="23" />
    <path d="M9 9v3a3 3 0 005.12 2.12M15 9.34V4a3 3 0 00-5.94-.6" />
    <path d="M17 16.95A7 7 0 015 12v-2m14 0v2c0 .64-.09 1.27-.26 1.86" />
    <line x1="12" y1="19" x2="12" y2="23" />
    <line x1="8" y1="23" x2="16" y2="23" />
  </svg>
)

const PhoneOffIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ transform: 'rotate(135deg)' }}>
    <path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07 19.5 19.5 0 01-6-6 19.79 19.79 0 01-3.07-8.67A2 2 0 014.11 2h3a2 2 0 012 1.72 12.84 12.84 0 00.7 2.81 2 2 0 01-.45 2.11L8.09 9.91a16 16 0 006 6l1.27-1.27a2 2 0 012.11-.45 12.84 12.84 0 002.81.7A2 2 0 0122 16.92z" />
  </svg>
)

export function WidgetVoicePanel() {
  const { config, session, createSession, error } = useWidgetContext()
  const {
    callState,
    isMuted,
    audioLevel,
    callDuration,
    isAgentSpeaking,
    transcripts,
    voiceError,
    startCall,
    endCall,
    toggleMute,
  } = useWidgetVoice()

  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Auto-create session if needed
  useEffect(() => {
    if (!session) {
      createSession(config.mode === 'multimodal' ? 'multimodal' : 'voice')
    }
  }, [session, createSession, config.mode])

  // Auto-scroll to bottom on new transcripts
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [transcripts])

  const formatDuration = (seconds: number): string => {
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  const formatTime = (date: Date) =>
    date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

  // No session yet — show connecting
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
      {voiceError && <div className="widget-error">{voiceError}</div>}

      {/* Idle — ready to start call */}
      {callState === 'idle' && (
        <div className="widget-body">
          <div className="widget-voice-idle">
            <div className="widget-voice-idle-icon" style={{ background: `${config.primaryColor}18` }}>
              <svg viewBox="0 0 24 24" fill="none" stroke={config.primaryColor} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                <path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07 19.5 19.5 0 01-6-6 19.79 19.79 0 01-3.07-8.67A2 2 0 014.11 2h3a2 2 0 012 1.72 12.84 12.84 0 00.7 2.81 2 2 0 01-.45 2.11L8.09 9.91a16 16 0 006 6l1.27-1.27a2 2 0 012.11-.45 12.84 12.84 0 002.81.7A2 2 0 0122 16.92z" />
              </svg>
            </div>
            <div className="widget-voice-idle-title">{config.welcomeMessage}</div>
            <div className="widget-voice-idle-subtitle">Click below to start a voice conversation</div>
            <button
              className="widget-start-btn"
              style={{ background: `linear-gradient(135deg, ${config.primaryColor}, ${config.accentColor})` }}
              onClick={startCall}
            >
              Start Call
            </button>
          </div>
        </div>
      )}

      {/* Connecting */}
      {callState === 'connecting' && (
        <div className="widget-body">
          <div className="widget-center-state">
            <div className="widget-connecting-ring" style={{ color: config.primaryColor }} />
            <div className="widget-center-title">Connecting...</div>
            <div className="widget-center-subtitle">Please wait while we connect you</div>
          </div>
        </div>
      )}

      {/* Active call — transcript view */}
      {callState === 'active' && (
        <>
          <div className="widget-body">
            <div className="chat-messages">
              {transcripts.length === 0 && (
                <div className="widget-voice-listening">
                  <div className="typing-indicator">
                    <div className="typing-dot" />
                    <div className="typing-dot" />
                    <div className="typing-dot" />
                  </div>
                  <div className="widget-voice-listening-label">Listening...</div>
                </div>
              )}
              {transcripts.map((t) => (
                <div
                  key={t.id}
                  className={`chat-bubble ${t.speaker === 'user' ? 'user' : 'assistant'}`}
                  style={undefined}
                >
                  <div
                    style={
                      t.isFinal
                        ? undefined
                        : { opacity: 0.8, fontStyle: 'italic' }
                    }
                  >
                    {t.text}
                    {!t.isFinal ? '…' : ''}
                  </div>
                  <div style={{ fontSize: '0.65rem', opacity: 0.6, marginTop: '4px', textAlign: t.speaker === 'user' ? 'right' : 'left' }}>
                    {formatTime(t.timestamp)}
                  </div>
                </div>
              ))}
              <div ref={messagesEndRef} />
            </div>
          </div>

          {/* Voice control bar */}
          <div className="voice-control-bar">
            {/* End call (left) */}
            <button className="voice-end-call-btn" onClick={endCall} aria-label="End call">
              {PhoneOffIcon}
            </button>

            {/* Duration */}
            <span className="voice-duration-badge">{formatDuration(callDuration)}</span>

            {/* Mute */}
            <button
              className={`voice-mic-btn${isMuted ? ' muted' : audioLevel > 15 ? ' speaking' : ' active'}`}
              style={!isMuted ? { color: config.primaryColor } : undefined}
              onClick={toggleMute}
              aria-label={isMuted ? 'Unmute microphone' : 'Mute microphone'}
            >
              {isMuted ? MicOffIcon : MicIcon}
            </button>
          </div>
        </>
      )}

      {/* Ended */}
      {callState === 'ended' && (
        <div className="widget-body">
          <div className="widget-center-state">
            <div className="widget-center-icon" style={{ background: '#dcfce7' }}>
              <svg viewBox="0 0 24 24" fill="none" stroke="#16a34a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12" />
              </svg>
            </div>
            <div className="widget-center-title">Call Ended</div>
            <div className="widget-center-subtitle">Thank you for calling. We hope we were helpful!</div>
          </div>
        </div>
      )}
    </>
  )
}
