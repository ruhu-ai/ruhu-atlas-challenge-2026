/**
 * Widget — Main widget shell component.
 *
 * Mode routing:
 *   chat       → WidgetChatPanel (text only)
 *   voice      → WidgetVoicePanel (voice only)
 *   multimodal → WidgetUnifiedPanel (text + voice in one stream, mic in input bar)
 *
 * Renders:
 * 1. Closed: Floating Action Button (FAB)
 * 2. Open: Panel with header, content, optional footer
 */

import { useState, useEffect, useCallback } from 'react'
import { useWidgetContext } from './WidgetProvider'
import { WidgetChatPanel } from './WidgetChatPanel'
import { WidgetVoicePanel } from './WidgetVoicePanel'
import { WidgetUnifiedPanel } from './WidgetUnifiedPanel'

// SVG icons (inline — no external deps)
const PhoneIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07 19.5 19.5 0 01-6-6 19.79 19.79 0 01-3.07-8.67A2 2 0 014.11 2h3a2 2 0 012 1.72 12.84 12.84 0 00.7 2.81 2 2 0 01-.45 2.11L8.09 9.91a16 16 0 006 6l1.27-1.27a2 2 0 012.11-.45 12.84 12.84 0 002.81.7A2 2 0 0122 16.92z" />
  </svg>
)

const ChatIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" />
  </svg>
)

const CloseIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="18" y1="6" x2="6" y2="18" />
    <line x1="6" y1="6" x2="18" y2="18" />
  </svg>
)

const MinimizeIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="4 14 10 14 10 20" />
    <polyline points="20 10 14 10 14 4" />
    <line x1="14" y1="10" x2="21" y2="3" />
    <line x1="3" y1="21" x2="10" y2="14" />
  </svg>
)

export function Widget() {
  const { config, session } = useWidgetContext()
  const [isOpen, setIsOpen] = useState(config.autoOpen)

  useEffect(() => {
    if (config.autoOpen) setIsOpen(true)
  }, [config.autoOpen])

  useEffect(() => {
    const onOpen = () => setIsOpen(true)
    const onClose = () => setIsOpen(false)
    window.addEventListener('ruhu-widget-open', onOpen)
    window.addEventListener('ruhu-widget-close', onClose)
    return () => {
      window.removeEventListener('ruhu-widget-open', onOpen)
      window.removeEventListener('ruhu-widget-close', onClose)
    }
  }, [])

  // Minimize/close only hides the panel — the server session is kept alive so
  // conversation history is preserved when the widget is reopened. The session
  // expires naturally via server-side inactivity timeout.
  const handleClose = useCallback(() => {
    setIsOpen(false)
  }, [])

  // --- Closed state: FAB ---
  if (!isOpen) {
    return (
      <div className={`widget-root ${config.position}`}>
        <button
          className="widget-fab"
          style={{ background: `linear-gradient(135deg, ${config.primaryColor}, ${config.accentColor})` }}
          onClick={() => setIsOpen(true)}
          aria-label={config.buttonText}
        >
          {config.mode === 'voice' ? PhoneIcon : ChatIcon}
          <span>{config.buttonText}</span>
        </button>
      </div>
    )
  }

  // --- Open state: Panel ---
  return (
    <div className={`widget-root ${config.position}`}>
      <div className="widget-panel">

        {/* ── Header ── */}
        <div
          className="widget-header"
          style={{ background: `linear-gradient(135deg, ${config.primaryColor}, ${config.accentColor})` }}
        >
          {/* Left: avatar + name */}
          <div className="widget-header-left">
            <div className="widget-header-avatar">
              {config.companyLogo ? (
                <img src={config.companyLogo} alt={config.companyName} />
              ) : (
                config.mode === 'voice' ? PhoneIcon : ChatIcon
              )}
            </div>
            <div>
              <div className="widget-header-title">{config.companyName}</div>
              <div className="widget-header-subtitle">
                {session ? 'Online' : 'Connecting...'}
              </div>
            </div>
          </div>

          {/* Right: actions */}
          <div className="widget-header-actions">
            <button className="widget-header-btn" onClick={handleClose} aria-label="Minimize">
              {MinimizeIcon}
            </button>
            <button className="widget-header-btn" onClick={handleClose} aria-label="Close">
              {CloseIcon}
            </button>
          </div>
        </div>

        {/* ── Content ── */}
        {config.mode === 'multimodal' && <WidgetUnifiedPanel />}
        {config.mode === 'chat' && <WidgetChatPanel />}
        {config.mode === 'voice' && <WidgetVoicePanel />}

        {/* ── Footer ── */}
        {config.showPoweredBy && (
          <div className="widget-footer">
            Powered by{' '}
            <a href="https://ruhu.ai" target="_blank" rel="noopener noreferrer">
              Ruhu
            </a>
          </div>
        )}
      </div>
    </div>
  )
}
