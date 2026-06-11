/**
 * Inline styles for the widget Shadow DOM.
 *
 * These styles are injected directly into the Shadow DOM so the widget
 * is fully self-contained and doesn't depend on external CSS files.
 */

export const WIDGET_STYLES = `
/* === Reset & Base === */
*, ::before, ::after {
  box-sizing: border-box;
  border-width: 0;
  border-style: solid;
  border-color: #e5e7eb;
  margin: 0;
  padding: 0;
}

:host {
  line-height: 1.5;
  -webkit-text-size-adjust: 100%;
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
    "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  color: #1f2937;
  font-size: 14px;
}

button { cursor: pointer; background: transparent; font-family: inherit; border: none; }
input, textarea { font-family: inherit; font-size: inherit; }
img { display: block; max-width: 100%; height: auto; }

/* === Layout === */
.widget-root { position: fixed; z-index: 2147483647; }
.widget-root.bottom-right { bottom: 1.25rem; right: 1.25rem; }
.widget-root.bottom-left  { bottom: 1.25rem; left: 1.25rem;  }
.widget-root.top-right    { top: 1.25rem;    right: 1.25rem; }
.widget-root.top-left     { top: 1.25rem;    left: 1.25rem;  }

/* === FAB (Floating Action Button) === */
.widget-fab {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 14px 22px;
  border-radius: 9999px;
  color: #fff;
  font-weight: 500;
  font-size: 1rem;
  box-shadow: 0 8px 30px rgba(0,0,0,0.16);
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.widget-fab:hover { transform: scale(1.05); box-shadow: 0 12px 40px rgba(0,0,0,0.2); }
.widget-fab:active { transform: scale(0.97); }
.widget-fab svg { width: 22px; height: 22px; flex-shrink: 0; }

/* === Panel === */
.widget-panel {
  width: 380px;
  max-height: 600px;
  border-radius: 16px;
  box-shadow: 0 20px 60px rgba(0,0,0,0.2);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  background: #fff;
  animation: widget-slide-up 0.25s ease-out;
}
@media (max-width: 420px) {
  .widget-panel {
    width: calc(100vw - 2rem);
    max-height: calc(100vh - 6rem);
    border-radius: 12px;
  }
}

@keyframes widget-slide-up {
  from { opacity: 0; transform: translateY(16px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* Header */
.widget-header {
  padding: 14px 16px;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.widget-header-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex-shrink: 1;
}
.widget-header-avatar {
  width: 42px;
  height: 42px;
  border-radius: 50%;
  background: rgba(255,255,255,0.2);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  flex-shrink: 0;
}
.widget-header-avatar img { width: 100%; height: 100%; object-fit: cover; }
.widget-header-avatar svg { width: 20px; height: 20px; color: #fff; }
.widget-header-title {
  font-weight: 600;
  font-size: 1rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.widget-header-subtitle { font-size: 0.75rem; opacity: 0.8; margin-top: 1px; }
.widget-header-actions { display: flex; gap: 2px; flex-shrink: 0; }
.widget-header-btn {
  padding: 6px;
  border-radius: 8px;
  color: #fff;
  transition: background 0.15s;
}
.widget-header-btn:hover { background: rgba(255,255,255,0.2); }
.widget-header-btn svg { width: 17px; height: 17px; }

/* Mode Toggle — centered pill tabs (multimodal) */
.widget-mode-toggle {
  display: flex;
  gap: 2px;
  padding: 3px;
  background: rgba(0,0,0,0.18);
  border-radius: 9999px;
  flex-shrink: 0;
}
.widget-mode-btn {
  padding: 5px 14px;
  border-radius: 9999px;
  font-size: 0.8rem;
  font-weight: 500;
  color: rgba(255,255,255,0.7);
  transition: all 0.18s ease;
  letter-spacing: 0.01em;
}
.widget-mode-btn:hover { color: #fff; }
.widget-mode-btn.active {
  background: #fff;
  color: #111827;
  box-shadow: 0 1px 4px rgba(0,0,0,0.15);
}

/* Body / Content */
.widget-body {
  flex: 1;
  overflow-y: auto;
  padding: 0;
  min-height: 280px;
  max-height: 420px;
}

/* Footer */
.widget-footer {
  padding: 8px 20px;
  background: #f9fafb;
  border-top: 1px solid #e5e7eb;
  text-align: center;
  font-size: 0.7rem;
  color: #9ca3af;
}
.widget-footer a { color: #6b7280; font-weight: 500; text-decoration: none; }

/* === Chat Panel === */
.chat-messages {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 240px;
}

.chat-bubble {
  max-width: 85%;
  padding: 10px 14px;
  border-radius: 14px;
  font-size: 0.875rem;
  line-height: 1.45;
  word-wrap: break-word;
  animation: chat-fade-in 0.2s ease;
}
@keyframes chat-fade-in {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
}

.chat-bubble.user {
  align-self: flex-end;
  background: #f3f4f6;
  color: #1f2937;
  border-bottom-right-radius: 4px;
}
.chat-bubble.assistant {
  align-self: flex-start;
  background: transparent;
  color: #1f2937;
  padding-left: 0;
  padding-right: 0;
}
.widget-attachment-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 8px;
}
.widget-attachment-card {
  border: 1px solid rgba(209, 213, 219, 0.9);
  border-radius: 10px;
  background: rgba(255,255,255,0.75);
  padding: 8px 10px;
}
.widget-attachment-card__name {
  font-size: 0.78rem;
  font-weight: 600;
}
.widget-attachment-card__meta {
  font-size: 0.68rem;
  opacity: 0.65;
  margin-top: 2px;
}

/* Typing indicator */
.typing-indicator {
  display: flex;
  gap: 4px;
  padding: 12px 16px;
  align-self: flex-start;
}
.typing-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #9ca3af;
  animation: typing-bounce 1.4s infinite ease-in-out;
}
.typing-dot:nth-child(2) { animation-delay: 0.2s; }
.typing-dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes typing-bounce {
  0%, 80%, 100% { transform: translateY(0); }
  40% { transform: translateY(-6px); }
}

/* Chat input */
.chat-input-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid #e5e7eb;
  background: #fff;
}
.widget-pending-attachments {
  width: 100%;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.widget-pending-attachment {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  border-radius: 9999px;
  background: #f3f4f6;
  border: 1px solid #e5e7eb;
  font-size: 0.72rem;
  color: #374151;
}
.widget-pending-attachment__remove {
  font-size: 0.9rem;
  line-height: 1;
  color: #6b7280;
}
.chat-attach-btn {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  border: 1px solid #d1d5db;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #4b5563;
}
.chat-attach-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.chat-attach-btn svg { width: 18px; height: 18px; }
.chat-input {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid #d1d5db;
  border-radius: 10px;
  outline: none;
  font-size: 0.875rem;
  resize: none;
  max-height: 80px;
  line-height: 1.4;
  transition: border-color 0.15s;
}
.chat-input:focus { border-color: #6366f1; }
.chat-input::placeholder { color: #9ca3af; }

.chat-send-btn {
  padding: 8px;
  border-radius: 10px;
  color: #fff;
  transition: opacity 0.15s, transform 0.1s;
  flex-shrink: 0;
}
.chat-send-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.chat-send-btn:not(:disabled):hover { opacity: 0.9; }
.chat-send-btn:not(:disabled):active { transform: scale(0.93); }
.chat-send-btn svg { width: 18px; height: 18px; }

/* === Voice Panel === */

/* Transcript empty state */
.voice-transcript-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  flex: 1;
  gap: 12px;
  color: #9ca3af;
  font-size: 0.85rem;
  padding: 48px 24px;
  text-align: center;
  min-height: 200px;
}

/* Animated waveform bars (agent speaking indicator) */
.voice-wave-bars {
  display: flex;
  align-items: center;
  gap: 3px;
  height: 22px;
}
.voice-wave-bar {
  width: 3px;
  border-radius: 2px;
  background: currentColor;
  animation: voice-wave 0.8s ease-in-out infinite alternate;
}
.voice-wave-bar:nth-child(1) { animation-delay: 0s;    height: 60%; }
.voice-wave-bar:nth-child(2) { animation-delay: 0.15s; height: 100%; }
.voice-wave-bar:nth-child(3) { animation-delay: 0.3s;  height: 40%; }
.voice-wave-bar:nth-child(4) { animation-delay: 0.45s; height: 80%; }
.voice-wave-bar:nth-child(5) { animation-delay: 0.0s;  height: 55%; }
@keyframes voice-wave {
  from { transform: scaleY(0.4); }
  to   { transform: scaleY(1); }
}

/* Bottom control bar */
.voice-control-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  border-top: 1px solid #e5e7eb;
  background: #fff;
}

/* Mic button */
.voice-mic-btn {
  width: 44px;
  height: 44px;
  min-width: 44px;
  min-height: 44px;
  border-radius: 50%;
  border: none;
  padding: 0;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  transition: transform 0.1s, background 0.15s;
  background: #f3f4f6;
}
.voice-mic-btn:hover  { transform: scale(1.08); }
.voice-mic-btn:active { transform: scale(0.95); }
.voice-mic-btn svg    { width: 20px; height: 20px; }

.voice-mic-btn.active  { background: #f3f4f6; }
.voice-mic-btn.muted   { background: #ef4444; color: #fff !important; }

/* Pulsing ring when user is speaking */
.voice-mic-btn.speaking::after {
  content: '';
  position: absolute;
  inset: -4px;
  border-radius: 50%;
  border: 2px solid currentColor;
  opacity: 0.4;
  animation: voice-pulse 1.2s ease-out infinite;
}
@keyframes voice-pulse {
  0%   { transform: scale(1);   opacity: 0.4; }
  100% { transform: scale(1.6); opacity: 0; }
}

/* Duration label */
.voice-duration-badge {
  font-size: 0.85rem;
  font-weight: 500;
  color: #6b7280;
  font-variant-numeric: tabular-nums;
}

/* End call button */
.voice-end-call-btn {
  width: 44px;
  height: 44px;
  min-width: 44px;
  min-height: 44px;
  border-radius: 50%;
  border: none;
  padding: 0;
  cursor: pointer;
  background: #ef4444;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 2px 8px rgba(239,68,68,0.35);
  transition: transform 0.1s, box-shadow 0.1s;
}
.voice-end-call-btn:hover  { transform: scale(1.08); box-shadow: 0 4px 16px rgba(239,68,68,0.5); }
.voice-end-call-btn:active { transform: scale(0.95); }
.voice-end-call-btn svg    { width: 20px; height: 20px; }

/* Welcome / idle / ended states */
.widget-center-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  padding: 32px 24px;
  text-align: center;
}
.widget-center-icon {
  width: 72px;
  height: 72px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
}
.widget-center-icon svg { width: 36px; height: 36px; }
.widget-center-title { font-size: 1.05rem; font-weight: 600; color: #111827; }
.widget-center-subtitle { font-size: 0.85rem; color: #6b7280; max-width: 260px; }

/* Voice idle state — full-height centered layout matching Ruhu multimodal */
.widget-voice-idle {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  padding: 40px 28px 32px;
  text-align: center;
  height: 100%;
  min-height: 280px;
}
.widget-voice-idle-icon {
  width: 88px;
  height: 88px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 4px;
}
.widget-voice-idle-icon svg { width: 40px; height: 40px; }
.widget-voice-idle-title {
  font-size: 1.1rem;
  font-weight: 700;
  color: #111827;
  line-height: 1.3;
}
.widget-voice-idle-subtitle {
  font-size: 0.85rem;
  color: #6b7280;
  max-width: 240px;
  line-height: 1.5;
}

.widget-start-btn {
  width: 100%;
  padding: 13px;
  border-radius: 12px;
  font-weight: 600;
  font-size: 0.95rem;
  color: #fff;
  transition: transform 0.1s, box-shadow 0.1s;
  box-shadow: 0 4px 16px rgba(0,0,0,0.15);
  margin-top: 4px;
}
.widget-start-btn:hover { transform: scale(1.02); box-shadow: 0 6px 24px rgba(0,0,0,0.2); }
.widget-start-btn:active { transform: scale(0.98); }

/* Voice active — listening indicator shown before first transcript arrives */
.widget-voice-listening {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 32px 0;
  opacity: 0.7;
}
.widget-voice-listening-label {
  font-size: 0.8rem;
  color: #6b7280;
  letter-spacing: 0.03em;
}

/* ── Unified panel — voice status strip ───────────────────────────────────── */
.widget-voice-strip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  border-bottom: 1px solid;
  font-size: 0.78rem;
  flex-shrink: 0;
}
.widget-voice-strip-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
  transition: transform 0.2s;
}
.widget-voice-strip-dot.pulse { animation: voicePulse 1s ease-in-out infinite; }
.widget-voice-strip-dot.agent { animation: voicePulse 0.6s ease-in-out infinite; }
.widget-voice-strip-dot.user  { transform: scale(1.3); }
@keyframes voicePulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%       { opacity: 0.4; transform: scale(0.7); }
}
.widget-voice-strip-label { flex: 1; font-weight: 500; }
.widget-voice-strip-mute {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  cursor: pointer;
  opacity: 0.8;
  padding: 0;
  flex-shrink: 0;
}
.widget-voice-strip-mute svg { width: 14px; height: 14px; }
.widget-voice-strip-mute:hover { opacity: 1; }
/* ── Unified panel — mic button in input bar ──────────────────────────────── */
.widget-mic-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: 1.5px solid #d1d5db;
  background: transparent;
  color: #9ca3af;
  cursor: pointer;
  flex-shrink: 0;
  transition: color 0.2s, border-color 0.2s, background 0.2s;
  padding: 0;
}
.widget-mic-btn svg { width: 16px; height: 16px; }
.widget-mic-btn:hover:not(:disabled) { color: #6b7280; border-color: #9ca3af; }
.widget-mic-btn.active {
  border-width: 1.5px;
  animation: micActivePulse 2s ease-in-out infinite;
}
.widget-mic-btn.speaking {
  border-width: 2px;
  animation: micSpeakingPulse 0.5s ease-in-out infinite;
}
.widget-mic-btn.connecting { color: #9ca3af; border-color: #d1d5db; cursor: wait; }
.widget-mic-btn.connecting svg { animation: spin 1s linear infinite; }
@keyframes micActivePulse {
  0%, 100% { box-shadow: 0 0 0 0 currentColor; opacity: 1; }
  50%       { box-shadow: 0 0 0 4px transparent; opacity: 0.85; }
}
@keyframes micSpeakingPulse {
  0%, 100% { transform: scale(1); }
  50%       { transform: scale(1.08); }
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}
.widget-mic-btn:disabled { opacity: 0.4; cursor: not-allowed; }

/* ── End call button (X) — filled app-color circle with white X ───────────── */
.widget-end-call-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: none;
  /* background set via inline style with config.primaryColor */
  cursor: pointer;
  flex-shrink: 0;
  transition: opacity 0.15s, transform 0.1s;
  padding: 0;
}
.widget-end-call-btn svg { width: 15px; height: 15px; }
.widget-end-call-btn:hover { opacity: 0.85; }
.widget-end-call-btn:active { transform: scale(0.93); }

/* Error banner */
.widget-error {
  padding: 10px 16px;
  background: #fef2f2;
  color: #dc2626;
  font-size: 0.8rem;
  text-align: center;
  border-bottom: 1px solid #fecaca;
}

/* Interaction status banner — surfaces "what's happening now" to the user
   (spec 23 §Projected Activity / Status Trail). */
.widget-status-banner {
  padding: 8px 16px;
  background: #f1f5f9;
  color: #475569;
  font-size: 0.78rem;
  text-align: center;
  border-bottom: 1px solid #e2e8f0;
  line-height: 1.35;
}

.widget-confirmation-stack {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 16px;
  border-bottom: 1px solid #e5e7eb;
  background: #fff7ed;
}

.widget-confirmation-card {
  border: 1px solid #fed7aa;
  background: #fffbeb;
  color: #7c2d12;
  border-radius: 12px;
  padding: 12px;
}

.widget-confirmation-title {
  font-size: 0.78rem;
  font-weight: 700;
  margin-bottom: 4px;
}

.widget-confirmation-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}

.widget-confirmation-btn {
  padding: 8px 12px;
  border-radius: 8px;
  background: linear-gradient(135deg, #E64E20, #D44D00);
  color: #fff;
  font-size: 0.78rem;
  font-weight: 600;
}

.widget-confirmation-btn.secondary {
  background: #fff;
  color: #9a3412;
  border: 1px solid #fdba74;
}

.widget-confirmation-btn:disabled {
  opacity: 0.7;
  cursor: default;
}

.widget-browser-task-stack {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 16px;
  border-bottom: 1px solid #e5e7eb;
  background: #f8fafc;
}

.widget-browser-task-card {
  border: 1px solid #dbe3ea;
  background: #fff;
  border-radius: 10px;
  padding: 12px;
  color: #111827;
}

.widget-browser-task-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.widget-browser-task-title {
  font-size: 0.84rem;
  font-weight: 700;
}

.widget-browser-task-domain,
.widget-browser-task-meta,
.widget-browser-task-progress {
  font-size: 0.75rem;
  color: #64748b;
  line-height: 1.35;
}

.widget-browser-task-progress {
  margin-top: 8px;
}

.widget-browser-task-state {
  flex-shrink: 0;
  border-radius: 999px;
  background: #eef2ff;
  color: #3730a3;
  padding: 3px 8px;
  font-size: 0.68rem;
  font-weight: 700;
  text-transform: capitalize;
}

.widget-browser-task-approval {
  margin-top: 8px;
  border-radius: 8px;
  background: #fff7ed;
  color: #7c2d12;
  padding: 8px;
  font-size: 0.77rem;
  line-height: 1.35;
}

.widget-browser-task-artifacts {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.widget-browser-task-artifacts a,
.widget-browser-task-artifacts span {
  border-radius: 999px;
  border: 1px solid #dbe3ea;
  color: #334155;
  padding: 4px 8px;
  font-size: 0.72rem;
  text-decoration: none;
}

.widget-browser-task-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.widget-browser-task-btn {
  padding: 7px 10px;
  border-radius: 8px;
  background: #111827;
  color: #fff;
  font-size: 0.75rem;
  font-weight: 700;
  border: 1px solid #111827;
}

.widget-browser-task-btn.secondary {
  background: #fff;
  color: #334155;
  border-color: #cbd5e1;
}

.widget-browser-task-btn:disabled {
  opacity: 0.65;
  cursor: default;
}

/* Connecting spinner */
.widget-connecting-ring {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  border: 3px solid rgba(0,0,0,0.08);
  border-top-color: currentColor;
  animation: widget-spin 0.8s linear infinite;
}
@keyframes widget-spin {
  to { transform: rotate(360deg); }
}

/* Activity pill — agent status during tool/LLM steps */
.activity-pill {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  font-size: 0.72rem;
  color: rgba(0,0,0,0.45);
  line-height: 1.4;
}
.activity-pill[data-state="failed"] { color: #dc2626; }
.activity-pill[data-state="completed"] { color: rgba(0,0,0,0.35); }
.activity-pill__icon {
  flex-shrink: 0;
  width: 12px;
  height: 12px;
}
.activity-pill__icon--spin { animation: widget-spin 1s linear infinite; }
.activity-pill__icon--ok { color: #16a34a; }
.activity-pill__icon--err { color: #dc2626; }

/* ═══════════════════════════════════════════════════════════════════════════
   Dark theme — auto-applied when the host OS/browser prefers dark mode
   ═══════════════════════════════════════════════════════════════════════════ */
@media (prefers-color-scheme: dark) {
  :host {
    color: #e5e5e3;
  }

  /* Panel shell */
  .widget-panel {
    background: #141413;
    box-shadow: 0 20px 60px rgba(0,0,0,0.55);
  }

  /* Footer */
  .widget-footer {
    background: #1a1918;
    border-top-color: #2a2926;
    color: #6b6b68;
  }
  .widget-footer a { color: #8a8a86; }

  /* Chat area */
  .chat-messages { background: #141413; }

  .chat-bubble.user {
    background: #2a2926;
    color: #e5e5e3;
  }
  .chat-bubble.assistant {
    color: #e5e5e3;
  }

  .widget-attachment-card {
    border-color: #38383a;
    background: rgba(255,255,255,0.04);
  }

  .typing-dot { background: #6b6b68; }

  /* Input bar */
  .chat-input-bar {
    background: #1a1918;
    border-top-color: #2a2926;
  }
  .widget-pending-attachment {
    background: #2a2926;
    border-color: #38383a;
    color: #c5c5c1;
  }
  .widget-pending-attachment__remove { color: #8a8a86; }
  .chat-attach-btn {
    border-color: #38383a;
    color: #9ca3af;
  }
  .chat-input {
    background: #1e1d1c;
    border-color: #38383a;
    color: #e5e5e3;
  }
  .chat-input:focus { border-color: #6366f1; }
  .chat-input::placeholder { color: #6b6b68; }

  /* Mic button — colors come from inline style (primaryColor) so just adjust hover */
  .widget-mic-btn:hover:not(:disabled) {
    color: #b0b0ac;
    border-color: #b0b0ac;
  }

  /* Voice strip */
  .widget-voice-strip {
    border-bottom-color: #2a2926;
    background: #1a1918;
  }
  .widget-voice-strip-label { color: #c5c5c1; }
  .widget-voice-strip-mute { color: #9ca3af; }

  /* Voice panel */
  .voice-transcript-empty { color: #6b6b68; }

  /* Center/connecting state */
  .widget-center-title { color: #e5e5e3; }
  .widget-center-subtitle { color: #8a8a86; }
  .widget-connecting-ring { border-color: #2a2926; border-top-color: currentColor; }

  /* Error */
  .widget-error {
    background: #2c1515;
    border-color: #6b2020;
    color: #fca5a5;
  }

  /* Activity pill */
  .activity-pill { color: rgba(255,255,255,0.35); }
  .activity-pill[data-state="completed"] { color: rgba(255,255,255,0.28); }
}
`
