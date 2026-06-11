from __future__ import annotations

import json


def widget_preview_html(agent_id: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Ruhu Widget Preview</title>
  <style>
    body {{
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(14, 165, 233, 0.12), transparent 32%),
        linear-gradient(180deg, #f8fafc 0%, #eef2ff 100%);
      color: #0f172a;
      min-height: 100vh;
    }}
    main {{
      width: min(960px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 48px 0 120px;
      display: grid;
      gap: 16px;
    }}
    .hero {{
      background: rgba(255,255,255,0.84);
      border: 1px solid rgba(148,163,184,0.22);
      border-radius: 20px;
      padding: 24px;
      box-shadow: 0 30px 60px rgba(15,23,42,0.08);
    }}
    .hero h1 {{
      margin: 0 0 8px;
      font-size: clamp(2rem, 4vw, 3.4rem);
      line-height: 1;
      letter-spacing: -0.04em;
    }}
    .hero p {{
      margin: 0;
      color: #475569;
      max-width: 70ch;
      line-height: 1.7;
    }}
    .card {{
      background: rgba(255,255,255,0.84);
      border: 1px solid rgba(148,163,184,0.22);
      border-radius: 20px;
      padding: 20px;
      box-shadow: 0 20px 40px rgba(15,23,42,0.06);
    }}
    code {{
      background: #e2e8f0;
      padding: 2px 6px;
      border-radius: 8px;
      font-size: 0.92em;
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <h1>Ruhu Widget Preview</h1>
      <p>
        This preview mounts the new public widget on the state-kernel runtime while preserving the
        old Ruhu shell layout: floating action button, gradient header, assistant bubbles, footer,
        and shadow-DOM isolation. It now also supports session resume, confirmation flows, and
        reconnect states. The current preview agent is <code>{agent_id}</code>.
      </p>
    </section>
    <section class="card">
      <strong>How it works</strong>
      <p>
        The script below opens a public widget session against the currently published agent, then
        sends user messages to the new kernel through a minimal public session API.
      </p>
    </section>
  </main>
  <script type="module">
    import * as LiveKit from '/widget-livekit-client.js';
    window.LiveKit = LiveKit;
  </script>
  <script
    src="/widget.js"
    data-agent-id="{agent_id}"
    data-company-name="Ruhu"
    data-button-text="Talk to us"
    data-primary-color="#E64E20"
    data-accent-color="#D44D00"
    data-position="bottom-right"
    data-show-powered-by="true"
    data-subtitle="Online now"
    data-welcome-message="Hi! Ask us anything about Ruhu."
  ></script>
</body>
</html>
"""


def widget_embed_script() -> str:
    styles = """
*, ::before, ::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

.widget-root { position: fixed; z-index: 2147483647; }
.widget-root.bottom-right { bottom: 1.25rem; right: 1.25rem; }
.widget-root.bottom-left  { bottom: 1.25rem; left: 1.25rem; }
.widget-root.top-right    { top: 1.25rem; right: 1.25rem; }
.widget-root.top-left     { top: 1.25rem; left: 1.25rem; }

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
  border: none;
  cursor: pointer;
}
.widget-fab:hover { transform: scale(1.05); box-shadow: 0 12px 40px rgba(0,0,0,0.2); }
.widget-fab svg { width: 22px; height: 22px; flex-shrink: 0; }

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
  to { opacity: 1; transform: translateY(0); }
}

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
  border: none;
  background: transparent;
  cursor: pointer;
}
.widget-header-btn:hover { background: rgba(255,255,255,0.2); }

.widget-body {
  flex: 1;
  overflow-y: auto;
  padding: 0;
  min-height: 280px;
  max-height: 420px;
  background: #fff;
}
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
}
.chat-bubble.user {
  align-self: flex-end;
  color: #fff;
  border-bottom-right-radius: 4px;
}
.chat-bubble.assistant {
  align-self: flex-start;
  background: #f3f4f6;
  color: #1f2937;
  border-bottom-left-radius: 4px;
}
.chat-bubble .time {
  font-size: 0.65rem;
  opacity: 0.6;
  margin-top: 4px;
}

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

.chat-input-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid #e5e7eb;
  background: #fff;
}
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
  font-family: inherit;
}
.chat-send-btn {
  padding: 8px;
  border-radius: 10px;
  color: #fff;
  border: none;
  cursor: pointer;
}
.chat-send-btn svg { width: 18px; height: 18px; }
.chat-send-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.widget-footer {
  padding: 8px 20px;
  background: #f9fafb;
  border-top: 1px solid #e5e7eb;
  text-align: center;
  font-size: 0.7rem;
  color: #9ca3af;
}
.widget-footer a { color: #6b7280; font-weight: 500; text-decoration: none; }

.widget-center-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding: 32px 24px;
  text-align: center;
  min-height: 280px;
}
.widget-center-title { font-size: 1.05rem; font-weight: 600; color: #111827; }
.widget-center-subtitle { font-size: 0.85rem; color: #6b7280; max-width: 260px; line-height: 1.5; }
.widget-error {
  margin: 12px 16px 0;
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(239,68,68,0.1);
  color: #991b1b;
  font-size: 0.8rem;
}
.widget-status-banner {
  margin: 12px 16px 0;
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(14,165,233,0.10);
  color: #0f172a;
  font-size: 0.8rem;
}
.widget-status-banner.offline {
  background: rgba(245,158,11,0.12);
  color: #92400e;
}
.widget-confirmation-card {
  margin: 12px 16px 0;
  padding: 12px;
  border: 1px solid rgba(148,163,184,0.24);
  border-radius: 12px;
  background: #fff7ed;
  display: grid;
  gap: 10px;
}
.widget-confirmation-title {
  font-size: 0.8rem;
  font-weight: 700;
  color: #9a3412;
  letter-spacing: 0.02em;
}
.widget-confirmation-actions {
  display: flex;
  gap: 8px;
}
.widget-confirmation-btn {
  padding: 8px 12px;
  border-radius: 10px;
  border: none;
  cursor: pointer;
  font-size: 0.8rem;
  font-weight: 600;
}
.widget-confirmation-btn.secondary {
  background: #fff;
  color: #475569;
  border: 1px solid #cbd5e1;
}
.widget-upload-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid #d1d5db;
  background: #fff;
  color: #334155;
  cursor: pointer;
  font-size: 0.78rem;
  font-weight: 600;
}
.widget-upload-btn:hover { background: #f8fafc; }
.widget-upload-btn[aria-busy="true"] {
  opacity: 0.7;
  cursor: progress;
}
.widget-file-input { display: none; }
.widget-attachments {
  margin: 12px 16px 0;
  display: grid;
  gap: 8px;
}
.widget-attachment-chip {
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid rgba(148,163,184,0.22);
  background: #f8fafc;
  display: grid;
  gap: 4px;
}
.widget-attachment-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 0.8rem;
  font-weight: 700;
  color: #0f172a;
}
.widget-attachment-status {
  font-size: 0.72rem;
  color: #475569;
}
.widget-attachment-summary {
  font-size: 0.74rem;
  color: #334155;
  line-height: 1.45;
}
.widget-attachment-link {
  color: #0f172a;
  font-size: 0.72rem;
  font-weight: 700;
  text-decoration: none;
}
.widget-browser-tasks {
  margin: 12px 16px 0;
  display: grid;
  gap: 10px;
}
.widget-task-card {
  padding: 12px;
  border-radius: 12px;
  border: 1px solid rgba(148,163,184,0.24);
  background: #f8fafc;
  display: grid;
  gap: 8px;
}
.widget-task-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 0.8rem;
  font-weight: 700;
  color: #0f172a;
}
.widget-task-state {
  padding: 2px 8px;
  border-radius: 9999px;
  background: #e2e8f0;
  color: #334155;
  font-size: 0.68rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.widget-task-summary,
.widget-task-event,
.widget-task-approval,
.widget-task-result {
  font-size: 0.74rem;
  color: #334155;
  line-height: 1.45;
}
.widget-task-artifacts {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.widget-task-artifact {
  font-size: 0.72rem;
  font-weight: 700;
  color: #0f172a;
  text-decoration: none;
  padding: 6px 8px;
  border-radius: 9999px;
  background: #fff;
  border: 1px solid #cbd5e1;
}
.widget-task-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.widget-task-btn {
  padding: 7px 10px;
  border-radius: 10px;
  border: none;
  cursor: pointer;
  font-size: 0.74rem;
  font-weight: 700;
}
.widget-task-btn.secondary {
  background: #fff;
  color: #475569;
  border: 1px solid #cbd5e1;
}
"""
    css_json = json.dumps(styles)
    template = """(() => {
  const CURRENT_SCRIPT = document.currentScript;
  const DATASET = CURRENT_SCRIPT?.dataset || {};
  let config = {
    agentId: DATASET.agentId || '',
    companyName: DATASET.companyName || 'Ruhu',
    buttonText: DATASET.buttonText || 'Talk to us',
    primaryColor: DATASET.primaryColor || '#E64E20',
    accentColor: DATASET.accentColor || '#D44D00',
    position: DATASET.position || 'bottom-right',
    showPoweredBy: DATASET.showPoweredBy !== 'false',
    welcomeMessage: DATASET.welcomeMessage || 'Hi! Ask us anything.',
    subtitle: DATASET.subtitle || 'Online',
    baseUrl: DATASET.baseUrl || window.location.origin,
    voiceEnabled: DATASET.voiceEnabled !== 'false',
  };

  const ChatIcon = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  `;
  const CloseIcon = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  `;
  const MinimizeIcon = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <polyline points="4 14 10 14 10 20" />
      <polyline points="20 10 14 10 14 4" />
      <line x1="14" y1="10" x2="21" y2="3" />
      <line x1="3" y1="21" x2="10" y2="14" />
    </svg>
  `;
  const SendIcon = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <line x1="22" y1="2" x2="11" y2="13" />
      <polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
  `;
  const MicIcon = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <line x1="12" y1="19" x2="12" y2="23" />
      <line x1="8" y1="23" x2="16" y2="23" />
    </svg>
  `;

  const container = document.createElement('div');
  container.className = 'ruhu-widget-host';
  document.body.appendChild(container);
  const shadowRoot = container.attachShadow({ mode: 'open' });
  const style = document.createElement('style');
  style.textContent = __CSS_JSON__;
  shadowRoot.appendChild(style);

  let isOpen = false;
  let sessionId = null;
  let sessionToken = null;
  let isSending = false;
  let errorMessage = '';
  let statusMessage = '';
  let isOnline = navigator.onLine;
  let pendingInvocations = [];
  let messages = [];
  let attachments = [];
  let browserTasks = [];
  let interactionStatusItems = [];
  let voiceActivity = null;
  let voiceSession = null;
  let voiceRoom = null;
  let isStartingVoice = false;
  let projectionStream = null;
  let projectionRetryTimer = null;
  let projectionRetryDelayMs = 1500;
  let isUploadingAttachment = false;
  let uploadProgressPercent = null;

  function storageKey() {
    return 'ruhu:widget:' + config.agentId;
  }

  function timestampLabel() {
    return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function persistState() {
    try {
      window.localStorage.setItem(storageKey(), JSON.stringify({
        sessionId,
        sessionToken,
        messages,
        voiceSession,
      }));
    } catch (error) {}
  }

  function clearPersistedState() {
    try {
      window.localStorage.removeItem(storageKey());
    } catch (error) {}
  }

  function restorePersistedState() {
    try {
      const raw = window.localStorage.getItem(storageKey());
      if (!raw) return;
      const payload = JSON.parse(raw);
      if (payload && typeof payload === 'object') {
        const restoredSessionId = typeof payload.sessionId === 'string' ? payload.sessionId : null;
        const restoredSessionToken = typeof payload.sessionToken === 'string' ? payload.sessionToken : null;
        if (restoredSessionId && restoredSessionToken) {
          sessionId = restoredSessionId;
          sessionToken = restoredSessionToken;
          messages = Array.isArray(payload.messages) ? payload.messages : [];
          voiceSession = payload.voiceSession && typeof payload.voiceSession === 'object' ? payload.voiceSession : null;
        }
      }
    } catch (error) {}
  }

  function withSessionToken(path) {
    if (!sessionToken || !path || path === '#') return path;
    try {
      const url = new URL(path, config.baseUrl);
      url.searchParams.set('session_token', sessionToken);
      return url.toString();
    } catch (error) {
      return path;
    }
  }

  async function fetchJson(path, options = {}) {
    const response = await fetch(config.baseUrl + path, {
      headers: {
        'Content-Type': 'application/json',
        ...(sessionToken ? { 'X-Ruhu-Widget-Session-Token': sessionToken } : {}),
        ...(options.headers || {}),
      },
      ...options,
    });
    const text = await response.text();
    let body = null;
    if (text) {
      try {
        body = JSON.parse(text);
      } catch (error) {
        body = text;
      }
    }
    if (!response.ok) {
      throw new Error((body && body.detail) || response.statusText || 'Request failed');
    }
    return body;
  }

  async function hydrateConfig() {
    try {
      const serverConfig = await fetchJson('/public/widget/config?agent_id=' + encodeURIComponent(config.agentId));
      config = {
        ...serverConfig,
        ...config,
      };
      render();
    } catch (error) {}
  }

  function appendMessages(items) {
    for (const message of items || []) {
      messages.push({
        role: message.role || 'assistant',
        text: message.text || '',
        time: timestampLabel(),
      });
    }
    persistState();
  }

  function currentVoiceStatus() {
    if (!voiceSession || !voiceSession.status) return null;
    return voiceSession.status;
  }

  function voiceStatusLabel() {
    const status = currentVoiceStatus();
    if (!status) return '';
    if (status === 'connected') return 'Voice connected';
    if (status === 'connecting') return 'Connecting voice…';
    if (status === 'reconnecting') return 'Reconnecting voice…';
    if (status === 'transport_ready') return 'Voice transport ready';
    if (status === 'disconnected') return 'Voice disconnected';
    if (status === 'error') return 'Voice error';
    return 'Voice active';
  }

  function voiceSessionIsActive() {
    const status = currentVoiceStatus();
    return status === 'transport_ready' || status === 'connecting' || status === 'connected' || status === 'reconnecting';
  }

  function shouldEnsureVoiceSession() {
    if (!voiceSession) return false;
    const status = currentVoiceStatus();
    if ((status === 'connected' || status === 'connecting') && voiceRoom) return false;
    if (status === 'reconnecting' && voiceRoom) return false;
    return true;
  }

  function buildVoiceSessionRequest() {
    const payload = { metadata: {} };
    if (!voiceSession || !voiceSession.realtimeSessionId) return payload;
    payload.realtime_session_id = voiceSession.realtimeSessionId;
    payload.resume_reason = currentVoiceStatus() === 'reconnecting' ? 'network_reconnect' : 'widget_resume';
    return payload;
  }

  function resolveLiveKitWebSdk() {
    return window.LiveKit || window.livekit || window.LivekitClient || null;
  }

  function clearVoiceConnectionState() {
    if (voiceRoom && typeof voiceRoom.disconnect === 'function') {
      try {
        voiceRoom.disconnect();
      } catch (error) {}
    }
    voiceRoom = null;
  }

  async function notifyVoiceDisconnected(reason) {
    if (!sessionId || !voiceSession || !voiceSession.realtimeSessionId) return;
    try {
      await fetchJson('/public/widget/sessions/' + encodeURIComponent(sessionId) + '/voice/disconnect', {
        method: 'POST',
        body: JSON.stringify({
          realtime_session_id: voiceSession.realtimeSessionId,
          reason: reason || 'widget_client_disconnected',
        }),
      });
    } catch (error) {}
  }

  async function disconnectVoiceSession(reason) {
    if (!voiceSession) return;
    clearVoiceConnectionState();
    await notifyVoiceDisconnected(reason || 'widget_user_disconnected');
    voiceSession = {
      ...voiceSession,
      status: 'disconnected',
      error: null,
    };
    statusMessage = '';
    persistState();
    render();
  }

  async function connectVoiceTransport() {
    if (!voiceSession || !voiceSession.transport) return;
    const sdk = resolveLiveKitWebSdk();
    if (!sdk || typeof sdk.Room !== 'function') {
      voiceSession = {
        ...voiceSession,
        status: 'transport_ready',
        error: 'LiveKit web client not loaded in host page.',
      };
      persistState();
      render();
      return;
    }
    try {
      clearVoiceConnectionState();
      voiceSession = { ...voiceSession, status: 'connecting', error: null };
      persistState();
      render();
      const room = new sdk.Room();
      voiceRoom = room;
      if (typeof room.on === 'function') {
        room.on('reconnecting', () => {
          if (voiceRoom !== room) return;
          voiceSession = { ...voiceSession, status: 'reconnecting', error: null };
          persistState();
          render();
        });
        room.on('reconnected', () => {
          if (voiceRoom !== room) return;
          voiceSession = { ...voiceSession, status: 'connected', error: null };
          persistState();
          render();
        });
        room.on('disconnected', () => {
          if (voiceRoom !== room) return;
          const shouldReconnect = !navigator.onLine;
          voiceRoom = null;
          voiceSession = {
            ...voiceSession,
            status: shouldReconnect ? 'reconnecting' : 'disconnected',
            error: null,
          };
          persistState();
          render();
          if (!shouldReconnect) {
            void notifyVoiceDisconnected('livekit_room_disconnected');
          }
        });
      }
      await room.connect(voiceSession.transport.url, voiceSession.transport.token);
      if (room.localParticipant && typeof room.localParticipant.setMicrophoneEnabled === 'function') {
        await room.localParticipant.setMicrophoneEnabled(true);
      }
      voiceSession = { ...voiceSession, status: 'connected', error: null };
      persistState();
      render();
    } catch (error) {
      clearVoiceConnectionState();
      voiceSession = {
        ...voiceSession,
        status: 'error',
        error: error && error.message ? error.message : 'Voice connection failed.',
      };
      persistState();
      render();
    }
  }

  async function ensureVoiceSession() {
    if (!config.voiceEnabled || !isOnline || isStartingVoice) return;
    const voiceStatus = currentVoiceStatus();
    if (voiceRoom && (voiceStatus === 'connecting' || voiceStatus === 'connected' || voiceStatus === 'reconnecting')) return;
    await ensureSession();
    if (!sessionId) return;
    isStartingVoice = true;
    errorMessage = '';
    statusMessage = 'Starting voice…';
    render();
    try {
      const requestPayload = buildVoiceSessionRequest();
      const response = await fetchJson('/public/widget/sessions/' + encodeURIComponent(sessionId) + '/voice', {
        method: 'POST',
        body: JSON.stringify(requestPayload),
      });
      voiceSession = {
        realtimeSessionId: response.realtime_session_id,
        transport: response.transport,
        status: 'transport_ready',
        resumed: Boolean(response.resumed),
        error: null,
      };
      pendingInvocations = response.pending_tool_invocations || pendingInvocations;
      statusMessage = response.resumed ? 'Voice resumed.' : 'Voice ready.';
      persistState();
      render();
      await connectVoiceTransport();
      if (voiceSession && voiceSession.status === 'connected') {
        statusMessage = '';
      }
    } catch (error) {
      voiceSession = voiceSession ? { ...voiceSession, status: 'error', error: error.message || 'Voice start failed.' } : null;
      errorMessage = error.message || 'Voice start failed.';
      statusMessage = '';
      persistState();
      render();
    } finally {
      isStartingVoice = false;
    }
  }

  function renderStatusBanner() {
    const notices = [];
    if (!isOnline) notices.push('<div class="widget-status-banner offline">Offline. Messages will resume when your connection returns.</div>');
    if (statusMessage) notices.push('<div class="widget-status-banner">' + escapeHtml(statusMessage) + '</div>');
    if (interactionStatusItems.length) notices.push('<div class="widget-status-banner">' + escapeHtml(interactionStatusItems.map((item) => item.summary || '').filter(Boolean).join(' · ')) + '</div>');
    if (errorMessage) notices.push('<div class="widget-error">' + escapeHtml(errorMessage) + '</div>');
    const voiceLabel = voiceStatusLabel();
    if (voiceLabel) notices.push('<div class="widget-status-banner">' + escapeHtml(voiceLabel) + '</div>');
    const voiceActivityText = voiceActivityLabel();
    if (voiceActivityText) notices.push('<div class="widget-status-banner">' + escapeHtml(voiceActivityText) + '</div>');
    if (voiceSession && voiceSession.error) notices.push('<div class="widget-error">' + escapeHtml(voiceSession.error) + '</div>');
    return notices.join('');
  }

  function renderPendingInvocations() {
    if (!pendingInvocations.length) return '';
    return pendingInvocations.map((invocation) => `
      <div class="widget-confirmation-card" data-invocation-id="${escapeHtml(invocation.invocation_id)}">
        <div class="widget-confirmation-title">Confirmation required</div>
        <div>${escapeHtml(invocation.metadata?.confirmation_prompt || invocation.error || invocation.decision_reason || invocation.tool_ref)}</div>
        <div class="widget-confirmation-actions">
          <button class="widget-confirmation-btn" data-action="confirm" data-invocation-id="${escapeHtml(invocation.invocation_id)}" style="background: linear-gradient(135deg, ${config.primaryColor}, ${config.accentColor}); color: #fff;">Confirm</button>
          <button class="widget-confirmation-btn secondary" data-action="cancel" data-invocation-id="${escapeHtml(invocation.invocation_id)}">Cancel</button>
        </div>
      </div>
    `).join('');
  }

  function renderAttachments() {
    if (!attachments.length) return '';
    return `
      <div class="widget-attachments">
        ${attachments.map((item) => `
          <div class="widget-attachment-chip" data-attachment-id="${escapeHtml(item.attachment.attachment_id)}">
            <div class="widget-attachment-title">
              <span>${escapeHtml(item.attachment.filename)}</span>
              ${item.attachment.scan_status === 'passed' ? `<a class="widget-attachment-link" href="${escapeAttribute(withSessionToken(config.baseUrl + '/public/widget/sessions/' + encodeURIComponent(sessionId) + '/attachments/' + encodeURIComponent(item.attachment.attachment_id) + '/download'))}" target="_blank" rel="noopener noreferrer">Download</a>` : ''}
            </div>
            <div class="widget-attachment-status">Scan: ${escapeHtml(item.attachment.scan_status)} · Extraction: ${escapeHtml(item.attachment.extraction_status)}</div>
            ${item.extraction?.summary ? `<div class="widget-attachment-summary">${escapeHtml(item.extraction.summary)}</div>` : ''}
            ${item.attachment.message ? `<div class="widget-attachment-summary">${escapeHtml(item.attachment.message)}</div>` : ''}
          </div>
        `).join('')}
      </div>
    `;
  }

  function renderBrowserTasks() {
    if (!browserTasks.length) return '';
    return `
      <div class="widget-browser-tasks">
        ${browserTasks.map((item) => {
          const latestEvent = item.recent_events?.length ? item.recent_events[item.recent_events.length - 1] : null;
          const approvalPending = item.approval && item.approval.state === 'pending';
          const cancellable = ['queued', 'running', 'awaiting_approval'].includes(item.task.state);
          const resultSummary = item.task.result?.summary || item.task.result?.message || '';
          const resultArtifacts = Array.isArray(item.task.result?.artifacts) ? item.task.result.artifacts : [];
          return `
            <div class="widget-task-card" data-task-id="${escapeHtml(item.task.task_id)}">
              <div class="widget-task-title">
                <span>${escapeHtml(item.task.title)}</span>
                <span class="widget-task-state">${escapeHtml(item.task.state)}</span>
              </div>
              ${item.task.summary ? `<div class="widget-task-summary">${escapeHtml(item.task.summary)}</div>` : ''}
              ${latestEvent ? `<div class="widget-task-event">${escapeHtml(latestEvent.message)}</div>` : ''}
              ${approvalPending ? `<div class="widget-task-approval">${escapeHtml(item.approval.prompt)}</div>` : ''}
              ${resultSummary ? `<div class="widget-task-result">${escapeHtml(resultSummary)}</div>` : ''}
              ${resultArtifacts.length ? `<div class="widget-task-artifacts">${resultArtifacts.map((artifact) => `<a class="widget-task-artifact" href="${escapeAttribute(withSessionToken(artifact.public_widget_download_url || artifact.download_url || '#'))}" target="_blank" rel="noopener noreferrer">${escapeHtml(artifact.filename || artifact.artifact_id || 'Artifact')}</a>`).join('')}</div>` : ''}
              <div class="widget-task-actions">
                ${approvalPending ? `<button class="widget-task-btn" data-task-action="approve" data-task-id="${escapeHtml(item.task.task_id)}" data-approval-id="${escapeHtml(item.approval.approval_id)}" style="background: linear-gradient(135deg, ${config.primaryColor}, ${config.accentColor}); color: #fff;">Approve</button>` : ''}
                ${approvalPending ? `<button class="widget-task-btn secondary" data-task-action="deny" data-task-id="${escapeHtml(item.task.task_id)}" data-approval-id="${escapeHtml(item.approval.approval_id)}">Deny</button>` : ''}
                ${cancellable ? `<button class="widget-task-btn secondary" data-task-action="cancel" data-task-id="${escapeHtml(item.task.task_id)}">Cancel</button>` : ''}
              </div>
            </div>
          `;
        }).join('')}
      </div>
    `;
  }

  function renderMessages() {
    if (messages.length === 0) {
      return `
        <div class="widget-center-state">
          <div class="widget-center-title">${escapeHtml(config.welcomeMessage)}</div>
          <div class="widget-center-subtitle">The new widget runs on the state-kernel runtime and resumes sessions automatically.</div>
        </div>
      `;
    }
    return `
      <div class="chat-messages">
        ${messages.map((message) => `
          <div class="chat-bubble ${message.role}" style="${message.role === 'user' ? `background: linear-gradient(135deg, ${config.primaryColor}, ${config.accentColor});` : ''}">
            <div>${escapeHtml(message.text)}</div>
            <div class="time">${escapeHtml(message.time)}</div>
          </div>
        `).join('')}
      </div>
    `;
  }

  function render() {
    const root = document.createElement('div');
    root.className = 'widget-root ' + config.position;

    if (!isOpen) {
      const fab = document.createElement('button');
      fab.className = 'widget-fab';
      fab.style.background = `linear-gradient(135deg, ${config.primaryColor}, ${config.accentColor})`;
      fab.innerHTML = ChatIcon + `<span>${escapeHtml(config.buttonText)}</span>`;
      fab.addEventListener('click', () => {
        isOpen = true;
        render();
        if (shouldEnsureVoiceSession()) {
          void ensureVoiceSession();
          return;
        }
        void ensureSession();
      });
      root.appendChild(fab);
      replaceRoot(root);
      return;
    }

    const panel = document.createElement('div');
    panel.className = 'widget-panel';
    panel.innerHTML = `
      <div class="widget-header" style="background: linear-gradient(135deg, ${config.primaryColor}, ${config.accentColor});">
        <div class="widget-header-left">
          <div class="widget-header-avatar">${ChatIcon}</div>
          <div>
            <div class="widget-header-title">${escapeHtml(config.companyName)}</div>
            <div class="widget-header-subtitle">${escapeHtml(currentVoiceStatus() ? voiceStatusLabel() : (sessionId ? config.subtitle : (isOnline ? 'Connecting...' : 'Offline')))}</div>
          </div>
        </div>
        <div class="widget-header-actions">
          ${config.voiceEnabled ? `<button class="widget-header-btn" data-action="voice" aria-label="${voiceSessionIsActive() ? 'End voice' : 'Start voice'}">${MicIcon}</button>` : ''}
          <button class="widget-header-btn" data-action="minimize" aria-label="Minimize">${MinimizeIcon}</button>
          <button class="widget-header-btn" data-action="close" aria-label="Close">${CloseIcon}</button>
        </div>
      </div>
      ${renderStatusBanner()}
      ${renderPendingInvocations()}
      ${renderBrowserTasks()}
      ${renderAttachments()}
      <div class="widget-body">${renderMessages()}</div>
      <div class="chat-input-bar">
        <label class="widget-upload-btn" aria-label="Upload attachment">
          <span>${isUploadingAttachment ? `Uploading${uploadProgressPercent !== null ? ` ${uploadProgressPercent}%` : ''}` : 'File'}</span>
          <input class="widget-file-input" type="file" />
        </label>
        <textarea class="chat-input" placeholder="Type a message..."></textarea>
        <button class="chat-send-btn" style="background: linear-gradient(135deg, ${config.primaryColor}, ${config.accentColor});" aria-label="Send" ${(!isOnline || isSending) ? 'disabled' : ''}>
          ${SendIcon}
        </button>
      </div>
      ${config.showPoweredBy ? '<div class="widget-footer">Powered by <a href="https://ruhu.ai" target="_blank" rel="noopener noreferrer">Ruhu</a></div>' : ''}
    `;

    panel.querySelector('[data-action="minimize"]').addEventListener('click', () => {
      isOpen = false;
      closeProjectionStream();
      void disconnectVoiceSession('widget_panel_minimized');
      render();
    });
    const voiceButton = panel.querySelector('[data-action="voice"]');
    if (voiceButton) {
      voiceButton.addEventListener('click', () => {
        if (voiceSessionIsActive()) {
          void disconnectVoiceSession('widget_voice_toggle_disconnected');
          return;
        }
        void ensureVoiceSession();
      });
    }
    panel.querySelector('[data-action="close"]').addEventListener('click', () => {
      isOpen = false;
      closeProjectionStream();
      void disconnectVoiceSession('widget_panel_closed');
      render();
    });

    panel.querySelectorAll('[data-action="confirm"]').forEach((button) => {
      button.addEventListener('click', async () => {
        const invocationId = button.getAttribute('data-invocation-id');
        if (!invocationId || !sessionId) return;
        try {
          errorMessage = '';
          statusMessage = 'Submitting confirmation…';
          render();
          const response = await fetchJson('/public/widget/sessions/' + encodeURIComponent(sessionId) + '/tool-invocations/' + encodeURIComponent(invocationId) + '/confirm', {
            method: 'POST',
          });
          appendMessages(response.messages || []);
          pendingInvocations = response.pending_tool_invocations || [];
          statusMessage = '';
          render();
        } catch (error) {
          errorMessage = error.message || 'Confirmation failed.';
          statusMessage = '';
          render();
        }
      });
    });

    panel.querySelectorAll('[data-action="cancel"]').forEach((button) => {
      button.addEventListener('click', async () => {
        const invocationId = button.getAttribute('data-invocation-id');
        if (!invocationId || !sessionId) return;
        try {
          errorMessage = '';
          statusMessage = 'Cancelling pending action…';
          render();
          const response = await fetchJson('/public/widget/sessions/' + encodeURIComponent(sessionId) + '/tool-invocations/' + encodeURIComponent(invocationId) + '/cancel', {
            method: 'POST',
          });
          appendMessages(response.messages || []);
          pendingInvocations = response.pending_tool_invocations || [];
          statusMessage = '';
          render();
        } catch (error) {
          errorMessage = error.message || 'Cancellation failed.';
          statusMessage = '';
          render();
        }
      });
    });

    panel.querySelectorAll('[data-task-action="approve"]').forEach((button) => {
      button.addEventListener('click', async () => {
        const taskId = button.getAttribute('data-task-id');
        const approvalId = button.getAttribute('data-approval-id');
        if (!taskId || !approvalId || !sessionId) return;
        try {
          errorMessage = '';
          statusMessage = 'Approving browser task…';
          render();
          const response = await fetchJson('/public/widget/sessions/' + encodeURIComponent(sessionId) + '/browser-tasks/' + encodeURIComponent(taskId) + '/approvals/' + encodeURIComponent(approvalId) + '/approve', {
            method: 'POST',
            body: JSON.stringify({}),
          });
          browserTasks = browserTasks.map((item) => item.task.task_id === response.task.task_id ? response : item);
          statusMessage = '';
          render();
        } catch (error) {
          errorMessage = error.message || 'Approval failed.';
          statusMessage = '';
          render();
        }
      });
    });

    panel.querySelectorAll('[data-task-action="deny"]').forEach((button) => {
      button.addEventListener('click', async () => {
        const taskId = button.getAttribute('data-task-id');
        const approvalId = button.getAttribute('data-approval-id');
        if (!taskId || !approvalId || !sessionId) return;
        try {
          errorMessage = '';
          statusMessage = 'Denying browser task…';
          render();
          const response = await fetchJson('/public/widget/sessions/' + encodeURIComponent(sessionId) + '/browser-tasks/' + encodeURIComponent(taskId) + '/approvals/' + encodeURIComponent(approvalId) + '/deny', {
            method: 'POST',
            body: JSON.stringify({}),
          });
          browserTasks = browserTasks.map((item) => item.task.task_id === response.task.task_id ? response : item);
          statusMessage = '';
          render();
        } catch (error) {
          errorMessage = error.message || 'Deny failed.';
          statusMessage = '';
          render();
        }
      });
    });

    panel.querySelectorAll('[data-task-action="cancel"]').forEach((button) => {
      button.addEventListener('click', async () => {
        const taskId = button.getAttribute('data-task-id');
        if (!taskId || !sessionId) return;
        try {
          errorMessage = '';
          statusMessage = 'Cancelling browser task…';
          render();
          const response = await fetchJson('/public/widget/sessions/' + encodeURIComponent(sessionId) + '/browser-tasks/' + encodeURIComponent(taskId) + '/cancel', {
            method: 'POST',
            body: JSON.stringify({}),
          });
          browserTasks = browserTasks.map((item) => item.task.task_id === response.task.task_id ? response : item);
          statusMessage = '';
          render();
        } catch (error) {
          errorMessage = error.message || 'Cancellation failed.';
          statusMessage = '';
          render();
        }
      });
    });

    const input = panel.querySelector('.chat-input');
    const send = panel.querySelector('.chat-send-btn');
    const fileInput = panel.querySelector('.widget-file-input');
    const uploadLabel = panel.querySelector('.widget-upload-btn');
    uploadLabel.setAttribute('aria-busy', isUploadingAttachment ? 'true' : 'false');
    const sendTurn = async () => {
      const text = input.value.trim();
      if (!text || isSending || !isOnline) return;
      isSending = true;
      errorMessage = '';
      statusMessage = '';
      messages.push({ role: 'user', text, time: timestampLabel() });
      persistState();
      input.value = '';
      render();
      try {
        await ensureSession();
        const response = await fetchJson('/public/widget/sessions/' + encodeURIComponent(sessionId) + '/messages', {
          method: 'POST',
          body: JSON.stringify({ text }),
        });
        appendMessages(response.messages || []);
        pendingInvocations = response.pending_tool_invocations || [];
      } catch (error) {
        errorMessage = error.message || 'Failed to send message.';
      } finally {
        isSending = false;
        render();
      }
    };
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        void sendTurn();
      }
    });
    send.addEventListener('click', () => void sendTurn());
    fileInput.addEventListener('change', async (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file || !sessionId || isUploadingAttachment) return;
      try {
        errorMessage = '';
        isUploadingAttachment = true;
        uploadProgressPercent = 0;
        statusMessage = 'Uploading ' + file.name + '…';
        render();
        const payload = await uploadAttachmentWithProgress(file);
        attachments = attachments.filter((item) => item.attachment.attachment_id !== payload.attachment.attachment_id);
        attachments.push(payload);
        statusMessage = 'Attachment uploaded. Processing…';
        render();
      } catch (error) {
        errorMessage = error.message || 'Attachment upload failed.';
        statusMessage = '';
        render();
      } finally {
        isUploadingAttachment = false;
        uploadProgressPercent = null;
        event.target.value = '';
      }
    });

    root.appendChild(panel);
    replaceRoot(root);
  }

  function replaceRoot(nextRoot) {
    const existing = shadowRoot.querySelector('.widget-root');
    if (existing) existing.remove();
    shadowRoot.appendChild(nextRoot);
  }

  async function syncPendingInvocations() {
    if (!sessionId) return;
    try {
      pendingInvocations = await fetchJson('/public/widget/sessions/' + encodeURIComponent(sessionId) + '/tool-invocations');
    } catch (error) {
      pendingInvocations = [];
    }
  }

  async function syncAttachments() {
    if (!sessionId) return;
    try {
      attachments = await fetchJson('/public/widget/sessions/' + encodeURIComponent(sessionId) + '/attachments');
    } catch (error) {
      attachments = [];
    }
  }

  async function syncBrowserTasks() {
    if (!sessionId) return;
    try {
      browserTasks = await fetchJson('/public/widget/sessions/' + encodeURIComponent(sessionId) + '/browser-tasks');
    } catch (error) {
      browserTasks = [];
    }
  }

  function applyProjectionSnapshot(snapshot) {
    if (!snapshot || typeof snapshot !== 'object') return;
    pendingInvocations = Array.isArray(snapshot.pending_tool_invocations) ? snapshot.pending_tool_invocations : [];
    attachments = Array.isArray(snapshot.attachments) ? snapshot.attachments : [];
    browserTasks = Array.isArray(snapshot.browser_tasks) ? snapshot.browser_tasks : [];
    interactionStatusItems = Array.isArray(snapshot.interaction_status) ? snapshot.interaction_status : [];
    voiceActivity = snapshot.voice_activity && typeof snapshot.voice_activity === 'object' ? snapshot.voice_activity : null;
    render();
  }

  function voiceActivityLabel() {
    if (!voiceActivity || !voiceActivity.name) return '';
    if (voiceActivity.name === 'assistant_speaking_started') return 'Assistant is speaking';
    if (voiceActivity.name === 'assistant_speaking_stopped') return 'Assistant finished speaking';
    if (voiceActivity.name === 'assistant_interrupted') return 'Assistant was interrupted';
    if (voiceActivity.name === 'user_barged_in') return 'You interrupted the assistant';
    if (voiceActivity.name === 'interruption_detected') return 'Interruption detected';
    return '';
  }

  async function syncProjectionSnapshot() {
    if (!sessionId) return;
    try {
      const snapshot = await fetchJson('/public/widget/sessions/' + encodeURIComponent(sessionId) + '/projection');
      applyProjectionSnapshot(snapshot);
      projectionRetryDelayMs = 1500;
    } catch (error) {}
  }

  function closeProjectionStream() {
    if (projectionRetryTimer) {
      clearTimeout(projectionRetryTimer);
      projectionRetryTimer = null;
    }
    if (projectionStream) {
      projectionStream.close();
      projectionStream = null;
    }
  }

  function connectProjectionStream() {
    if (!sessionId || typeof window.EventSource === 'undefined' || projectionStream) return;
    const stream = new window.EventSource(withSessionToken(
      config.baseUrl + '/public/widget/sessions/' + encodeURIComponent(sessionId) + '/events'
    ));
    stream.addEventListener('widget.snapshot', (event) => {
      try {
        const payload = JSON.parse(event.data);
        applyProjectionSnapshot(payload);
        statusMessage = '';
        projectionRetryDelayMs = 1500;
        render();
      } catch (error) {}
    });
    stream.addEventListener('error', () => {
      if (!isOpen || !isOnline) return;
      closeProjectionStream();
      statusMessage = 'Realtime updates paused. Retrying…';
      render();
      projectionRetryTimer = window.setTimeout(() => {
        projectionRetryTimer = null;
        void syncProjectionSnapshot().finally(() => connectProjectionStream());
      }, projectionRetryDelayMs);
      projectionRetryDelayMs = Math.min(projectionRetryDelayMs * 2, 12000);
    });
    projectionStream = stream;
  }

  function uploadAttachmentWithProgress(file) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open('POST', config.baseUrl + '/public/widget/sessions/' + encodeURIComponent(sessionId) + '/attachments?filename=' + encodeURIComponent(file.name));
      request.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
      if (sessionToken) request.setRequestHeader('X-Ruhu-Widget-Session-Token', sessionToken);
      request.upload.addEventListener('progress', (event) => {
        if (!event.lengthComputable) return;
        uploadProgressPercent = Math.max(1, Math.min(100, Math.round((event.loaded / event.total) * 100)));
        statusMessage = 'Uploading ' + file.name + ' ' + uploadProgressPercent + '%';
        render();
      });
      request.addEventListener('load', () => {
        try {
          const payload = request.responseText ? JSON.parse(request.responseText) : null;
          if (request.status < 200 || request.status >= 300) {
            reject(new Error((payload && payload.detail) || request.statusText || 'Upload failed'));
            return;
          }
          resolve(payload);
        } catch (error) {
          reject(new Error('Upload failed'));
        }
      });
      request.addEventListener('error', () => reject(new Error('Upload failed')));
      request.send(file);
    });
  }

  async function ensureSession() {
    if (sessionId) {
      try {
        const resumed = await fetchJson('/public/widget/sessions/' + encodeURIComponent(sessionId));
        sessionToken = resumed.session_token || sessionToken;
        pendingInvocations = resumed.pending_tool_invocations || [];
        if (!messages.length) {
          appendMessages(resumed.messages || []);
        }
        await syncProjectionSnapshot();
        if (isOpen) connectProjectionStream();
        render();
        return sessionId;
      } catch (error) {
        sessionId = null;
        sessionToken = null;
        pendingInvocations = [];
        attachments = [];
        browserTasks = [];
        messages = [];
        clearPersistedState();
      }
    }
    const response = await fetchJson('/public/widget/sessions', {
      method: 'POST',
      body: JSON.stringify({
        agent_id: config.agentId,
        channel: 'web_widget',
        conversation_id: sessionId,
      }),
    });
    sessionId = response.conversation_id;
    sessionToken = response.session_token || null;
    pendingInvocations = response.pending_tool_invocations || [];
    if (!messages.length) {
      appendMessages(response.messages || []);
    }
    persistState();
    await syncProjectionSnapshot();
    if (isOpen) connectProjectionStream();
    render();
    return sessionId;
  }

  async function handleReconnect() {
    try {
      await ensureSession();
    } finally {
      try {
        await syncPendingInvocations();
      } finally {
        try {
          await syncAttachments();
        } finally {
          try {
            await syncBrowserTasks();
          } finally {
            connectProjectionStream();
            if (shouldEnsureVoiceSession()) {
              void ensureVoiceSession();
            }
            render();
          }
        }
      }
    }
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function escapeAttribute(value) {
    return escapeHtml(value);
  }

  window.addEventListener('online', () => {
    isOnline = true;
    statusMessage = 'Connection restored.';
    void handleReconnect();
  });

  window.addEventListener('offline', () => {
    isOnline = false;
    statusMessage = '';
    closeProjectionStream();
    if (voiceSession && voiceSessionIsActive()) {
      voiceSession = { ...voiceSession, status: 'reconnecting', error: null };
      persistState();
    }
    render();
  });

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible' || !isOpen || !isOnline) return;
    void syncProjectionSnapshot().finally(() => {
      connectProjectionStream();
      if (shouldEnsureVoiceSession()) {
        void ensureVoiceSession();
      }
    });
  });

  restorePersistedState();
  render();
  void hydrateConfig().finally(() => {
    if (isOpen) {
      void ensureSession();
    }
    if (isOpen && isOnline && shouldEnsureVoiceSession()) {
      void ensureVoiceSession();
    }
  });
})();
"""
    return template.replace("__CSS_JSON__", css_json)
