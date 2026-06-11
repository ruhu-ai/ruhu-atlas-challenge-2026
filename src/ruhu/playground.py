from __future__ import annotations

from .ui_theme import app_theme_styles


def playground_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Ruhu Playground</title>
  <style>
""" + app_theme_styles() + """

    .shell {
      width: min(1400px, calc(100vw - 32px));
      margin: 24px auto 40px;
      display: grid;
      gap: 16px;
    }

    .hero, .card {
      background: hsl(var(--card));
      color: hsl(var(--card-foreground));
      border: 1px solid hsl(var(--border));
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }

    .hero {
      padding: 24px;
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 16px;
    }

    .hero h1 {
      margin: 0 0 8px;
      font-size: clamp(var(--text-2xl), 3vw, 2.7rem);
      line-height: 1;
      letter-spacing: -0.04em;
    }

    .hero p {
      margin: 0;
      font-size: var(--text-sm);
      line-height: 1.6;
      color: hsl(var(--muted-foreground));
      max-width: 70ch;
    }

    .hero .status {
      font-family: var(--mono);
      font-size: var(--text-sm);
      color: hsl(var(--muted-foreground));
      white-space: nowrap;
    }

    .layout {
      display: grid;
      grid-template-columns: 360px 1fr 420px;
      gap: 16px;
      align-items: start;
    }

    .card {
      padding: 18px;
    }

    .card h2 {
      margin: 0 0 14px;
      font-size: var(--text-xs);
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: hsl(var(--muted-foreground));
    }

    .section-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
    }

    .section-head h2 {
      margin: 0;
    }

    .stack { display: grid; gap: 12px; }

    .button-group {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: flex-end;
    }

    label {
      display: grid;
      gap: 6px;
      font-size: var(--text-sm);
      color: hsl(var(--muted-foreground));
    }

    input, select, textarea, button {
      font: inherit;
    }

    input, select, textarea {
      width: 100%;
      padding: 12px 14px;
      border: 1px solid hsl(var(--input));
      border-radius: calc(var(--radius) + 2px);
      background: hsl(var(--card));
      color: hsl(var(--foreground));
      font-size: var(--text-sm);
    }

    input:focus, select:focus, textarea:focus {
      outline: none;
      border-color: hsl(var(--ring));
      box-shadow: 0 0 0 4px rgba(var(--primary-rgb), 0.14);
    }

    textarea {
      min-height: 110px;
      resize: vertical;
    }

    button {
      border: 0;
      border-radius: 999px;
      padding: 11px 16px;
      background: hsl(var(--primary));
      color: hsl(var(--primary-foreground));
      cursor: pointer;
      font-size: var(--text-sm);
      font-weight: 600;
      transition: transform 120ms ease, box-shadow 120ms ease, background 120ms ease;
    }

    button.secondary {
      background: transparent;
      color: hsl(var(--foreground));
      border: 1px solid hsl(var(--border));
    }

    button.secondary.subtle {
      padding: 8px 12px;
      font-size: var(--text-xs);
      font-weight: 500;
    }

    button:hover:not(:disabled) {
      transform: translateY(-1px);
      box-shadow: 0 8px 24px rgba(var(--primary-rgb), 0.16);
    }

    button:focus-visible {
      outline: none;
      box-shadow: 0 0 0 4px rgba(var(--primary-rgb), 0.18);
    }

    button:disabled {
      opacity: 0.55;
      cursor: default;
    }

    .row {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border-radius: 999px;
      padding: 8px 12px;
      background: rgba(var(--primary-rgb), 0.10);
      color: hsl(var(--primary));
      font-size: var(--text-sm);
      font-weight: 500;
    }

    .kv {
      display: grid;
      grid-template-columns: 110px 1fr;
      gap: 10px;
      font-size: var(--text-sm);
      align-items: start;
    }

    .kv .k {
      color: hsl(var(--muted-foreground));
      font-family: var(--mono);
      font-size: var(--text-xs);
    }

    .status-banner {
      border: 1px solid hsl(var(--border));
      border-radius: var(--radius);
      padding: 10px 12px;
      background: hsl(var(--secondary));
      color: hsl(var(--muted-foreground));
      font-size: var(--text-sm);
      line-height: 1.5;
    }

    .status-banner[data-tone="success"] {
      background: rgba(var(--primary-rgb), 0.10);
      border-color: rgba(var(--primary-rgb), 0.18);
      color: hsl(var(--primary));
    }

    .status-banner[data-tone="loading"] {
      background: hsl(var(--secondary));
      color: hsl(var(--foreground));
    }

    .status-banner[data-tone="error"] {
      background: rgba(220, 38, 38, 0.10);
      border-color: rgba(220, 38, 38, 0.18);
      color: rgb(185, 28, 28);
    }

    .messages {
      display: grid;
      gap: 10px;
      min-height: 420px;
      align-content: start;
    }

    .message {
      border: 1px solid hsl(var(--border));
      border-radius: calc(var(--radius) + 4px);
      padding: 12px 14px;
      background: hsl(var(--card));
      font-size: var(--text-sm);
      line-height: 1.6;
    }

    .message.user {
      background: rgba(var(--primary-rgb), 0.08);
      border-color: rgba(var(--primary-rgb), 0.18);
    }

    .message.system {
      background: hsl(var(--secondary));
      border-color: hsl(var(--border));
    }

    .message .meta {
      margin-bottom: 6px;
      color: hsl(var(--muted-foreground));
      font-size: var(--text-xs);
      font-family: var(--mono);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    .trace-list {
      display: grid;
      gap: 10px;
      max-height: 76vh;
      overflow: auto;
      padding-right: 4px;
    }

    .trace {
      border: 1px solid hsl(var(--border));
      border-radius: calc(var(--radius) + 4px);
      padding: 12px;
      background: hsl(var(--card));
      display: grid;
      gap: 10px;
    }

    .trace-details summary {
      cursor: pointer;
      font-family: var(--mono);
      font-size: var(--text-xs);
      color: hsl(var(--muted-foreground));
    }

    .trace-details[open] summary {
      margin-bottom: 8px;
    }

    .trace pre, .json-box {
      margin: 0;
      padding: 10px 12px;
      border-radius: var(--radius);
      background: hsl(var(--secondary));
      border: 1px solid hsl(var(--border));
      font-family: var(--mono);
      font-size: var(--text-xs);
      line-height: 1.45;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
    }

    .empty {
      color: hsl(var(--muted-foreground));
      font-style: italic;
    }

    @media (max-width: 1180px) {
      .layout { grid-template-columns: 1fr; }
      .messages { min-height: 260px; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div>
        <h1>Ruhu Runtime Playground</h1>
        <p>Start a conversation against any loaded agent, send turns, and inspect step transitions, emitted messages, semantic events, and trace records in one place.</p>
      </div>
      <div class="status" id="health-status">checking /ready</div>
    </section>

    <section class="layout">
      <aside class="card stack">
        <div>
          <h2>Session</h2>
          <div class="stack">
            <label>
              Agent
              <select id="agent-select" aria-label="Select an agent"></select>
            </label>
            <label>
              Channel
              <select id="channel-select" aria-label="Select a channel">
                <option value="phone">phone</option>
                <option value="whatsapp">whatsapp</option>
                <option value="web_chat">web_chat</option>
                <option value="web_widget">web_widget</option>
                <option value="browser">browser</option>
              </select>
            </label>
            <div class="row">
              <button id="start-button" type="button" disabled>Start Conversation</button>
              <button class="secondary" id="reload-agents-button" type="button">Reload Agents</button>
            </div>
            <div id="feedback-banner" class="status-banner" role="status" aria-live="polite" aria-atomic="true">
              Ready. Choose an agent and start a conversation.
            </div>
            <div class="kv">
              <div class="k">conversation</div>
              <div id="conversation-id" class="json-box">not started</div>
              <div class="k">step</div>
              <div id="state-pill" class="pill">idle</div>
              <div class="k">agent</div>
              <div id="agent-pill" class="pill">none</div>
            </div>
          </div>
        </div>

        <div>
          <h2>Turn</h2>
          <div class="stack">
            <label>
              User Text
              <textarea id="turn-text" aria-label="Turn text" placeholder="Type a turn like: Can you explain what the product does?"></textarea>
            </label>
            <div class="row">
              <button id="send-turn-button" type="button" disabled>Send Turn</button>
              <button class="secondary" id="refresh-traces-button" type="button" disabled>Refresh Traces</button>
            </div>
          </div>
        </div>

        <div>
          <div class="section-head">
            <h2>Conversation Snapshot</h2>
            <div class="button-group">
              <button class="secondary subtle" id="copy-conversation-button" type="button" disabled>Copy JSON</button>
            </div>
          </div>
          <pre id="conversation-json" class="json-box">No conversation yet.</pre>
        </div>
      </aside>

      <section class="card">
        <div class="section-head">
          <h2>Transcript</h2>
          <div class="button-group">
            <button class="secondary subtle" id="clear-transcript-button" type="button" disabled>Clear Transcript</button>
          </div>
        </div>
        <div id="messages" class="messages" aria-live="polite" aria-busy="false">
          <div class="empty">Start a conversation to see the entry response and subsequent turns.</div>
        </div>
      </section>

      <aside class="card">
        <div class="section-head">
          <h2>Trace Timeline</h2>
        </div>
        <div id="trace-list" class="trace-list" aria-live="polite" aria-busy="false">
          <div class="empty">Trace records will appear here after the first turn.</div>
        </div>
      </aside>
    </section>
  </main>

  <script>
    const PREFERENCES_KEY = "ruhu.playground.preferences.v1";

    const state = {
      agentId: null,
      agentName: null,
      preferredAgentId: null,
      conversationId: null,
      conversation: null,
      messages: [],
      traces: [],
    };

    const agentSelect = document.getElementById("agent-select");
    const channelSelect = document.getElementById("channel-select");
    const startButton = document.getElementById("start-button");
    const reloadAgentsButton = document.getElementById("reload-agents-button");
    const sendTurnButton = document.getElementById("send-turn-button");
    const refreshTracesButton = document.getElementById("refresh-traces-button");
    const turnText = document.getElementById("turn-text");
    const healthStatus = document.getElementById("health-status");
    const feedbackBanner = document.getElementById("feedback-banner");
    const conversationIdBox = document.getElementById("conversation-id");
    const statePill = document.getElementById("state-pill");
    const agentPill = document.getElementById("agent-pill");
    const conversationJson = document.getElementById("conversation-json");
    const copyConversationButton = document.getElementById("copy-conversation-button");
    const clearTranscriptButton = document.getElementById("clear-transcript-button");
    const messagesNode = document.getElementById("messages");
    const traceListNode = document.getElementById("trace-list");

    restorePreferences();

    async function request(path, options = {}) {
      const response = await fetch(path, {
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        ...options,
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(`${response.status} ${response.statusText}: ${text}`);
      }
      const contentType = response.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        return response.json();
      }
      return response.text();
    }

    function isButtonLoading(button) {
      return button.dataset.loading === "true";
    }

    function setButtonLoading(button, isLoading, loadingLabel) {
      if (!button.dataset.defaultLabel) {
        button.dataset.defaultLabel = button.textContent;
      }
      button.dataset.loading = isLoading ? "true" : "false";
      button.textContent = isLoading ? loadingLabel : button.dataset.defaultLabel;
      button.setAttribute("aria-busy", isLoading ? "true" : "false");
      renderControls();
    }

    async function runWithLoading(button, loadingLabel, action) {
      setButtonLoading(button, true, loadingLabel);
      try {
        return await action();
      } finally {
        setButtonLoading(button, false, loadingLabel);
      }
    }

    function renderControls() {
      const hasAgents = agentSelect.options.length > 0;
      startButton.disabled = !hasAgents || isButtonLoading(startButton);
      reloadAgentsButton.disabled = isButtonLoading(reloadAgentsButton);
      sendTurnButton.disabled = !state.conversationId || isButtonLoading(sendTurnButton);
      refreshTracesButton.disabled = !state.conversationId || isButtonLoading(refreshTracesButton);
      copyConversationButton.disabled = !state.conversation || isButtonLoading(copyConversationButton);
      clearTranscriptButton.disabled = !state.messages.length || isButtonLoading(clearTranscriptButton);
      messagesNode.setAttribute("aria-busy", isButtonLoading(sendTurnButton) ? "true" : "false");
      traceListNode.setAttribute(
        "aria-busy",
        isButtonLoading(refreshTracesButton) || isButtonLoading(startButton) ? "true" : "false",
      );
    }

    function setBanner(message, tone = "neutral") {
      feedbackBanner.textContent = message;
      feedbackBanner.dataset.tone = tone;
    }

    function persistPreferences() {
      try {
        localStorage.setItem(
          PREFERENCES_KEY,
          JSON.stringify({
            agentId: agentSelect.value || state.preferredAgentId || null,
            channel: channelSelect.value || null,
          }),
        );
      } catch (_error) {
        // Ignore storage failures for privacy-restricted browsers.
      }
    }

    function restorePreferences() {
      try {
        const raw = localStorage.getItem(PREFERENCES_KEY);
        if (!raw) return;
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") {
          if (typeof parsed.agentId === "string") {
            state.preferredAgentId = parsed.agentId;
          }
          if (
            typeof parsed.channel === "string"
            && Array.from(channelSelect.options).some((option) => option.value === parsed.channel)
          ) {
            channelSelect.value = parsed.channel;
          }
        }
      } catch (_error) {
        // Ignore malformed storage and continue with defaults.
      }
    }

    function renderConversation() {
      conversationIdBox.textContent = state.conversationId || "not started";
      agentPill.textContent = state.agentName || state.agentId || "none";
      statePill.textContent = state.conversation ? state.conversation.step_id : "idle";
      conversationJson.textContent = state.conversation
        ? JSON.stringify(state.conversation, null, 2)
        : "No conversation yet.";
      renderControls();
    }

    function renderMessages() {
      if (!state.messages.length) {
        messagesNode.innerHTML = '<div class="empty">Start a conversation to see the entry response and subsequent turns.</div>';
        renderControls();
        return;
      }
      messagesNode.innerHTML = state.messages.map((item) => {
        return `
          <article class="message ${item.kind}">
            <div class="meta">${item.kind}</div>
            <div>${escapeHtml(item.text)}</div>
          </article>
        `;
      }).join("");
      renderControls();
    }

    function renderTraces() {
      if (!state.traces.length) {
        traceListNode.innerHTML = '<div class="empty">Trace records will appear here after the first turn.</div>';
        return;
      }
      traceListNode.innerHTML = state.traces.map((trace) => {
        const events = (trace.semantic_events || []).map((event) => event.family + ":" + event.name);
        const factUpdates = (trace.fact_updates || []).map((fact) => `${fact.name}=${JSON.stringify(fact.value)}`);
        const observability = trace.decision_observability || {};
        const fallbackSummary = [
          `actual: ${
            observability.fallback_used === true
              ? "fallback used"
              : "no fallback"
          }`,
          `controller: ${observability.controller_of_record || "unknown"}`,
        ];
        if (observability.fallback_reason) {
          fallbackSummary.push(`reason: ${observability.fallback_reason}`);
        }
        return `
          <article class="trace">
            <div class="kv">
              <div class="k">turn</div><div>${escapeHtml(trace.turn_id)}</div>
              <div class="k">step</div><div>${escapeHtml(trace.step_before)} → ${escapeHtml(trace.step_after)}</div>
              <div class="k">action</div><div>${escapeHtml(trace.chosen_action.type)} · ${escapeHtml(trace.chosen_action.reason || "")}</div>
              <div class="k">fallback</div><div>${escapeHtml(fallbackSummary.join(" · "))}</div>
              <div class="k">events</div><div>${events.length ? escapeHtml(events.join(", ")) : "<span class=\\"empty\\">none</span>"}</div>
              <div class="k">facts</div><div>${factUpdates.length ? escapeHtml(factUpdates.join(", ")) : "<span class=\\"empty\\">none</span>"}</div>
            </div>
            <details class="trace-details">
              <summary>Raw trace JSON</summary>
              <pre>${escapeHtml(JSON.stringify(trace, null, 2))}</pre>
            </details>
          </article>
        `;
      }).join("");
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }

    async function loadHealth() {
      try {
        const payload = await request("/ready");
        healthStatus.textContent = `/ready ${payload.status}`;
      } catch (error) {
        healthStatus.textContent = `health error: ${error.message}`;
        setBanner(`Health check failed: ${error.message}`, "error");
      }
    }

    async function loadAgents() {
      const agents = await request("/agents");
      agentSelect.innerHTML = agents.map((agent) => {
        return `<option value="${escapeHtml(agent.id)}" data-name="${escapeHtml(agent.name)}">${escapeHtml(agent.name)} · ${escapeHtml(agent.version)}</option>`;
      }).join("");
      if (agents.length) {
        const preferredAgentId = state.preferredAgentId;
        if (preferredAgentId && agents.some((agent) => agent.id === preferredAgentId)) {
          agentSelect.value = preferredAgentId;
        }
        state.agentId = agentSelect.value;
        const selected = agents.find((agent) => agent.id === state.agentId);
        state.agentName = selected ? selected.name : state.agentId;
        state.preferredAgentId = state.agentId;
        setBanner(`Loaded ${agents.length} agent${agents.length === 1 ? "" : "s"}.`, "success");
      } else {
        state.agentId = null;
        state.agentName = null;
        setBanner("No agents are currently loaded.", "error");
      }
      persistPreferences();
      renderConversation();
    }

    async function refreshConversation() {
      if (!state.conversationId) return;
      state.conversation = await request(`/conversations/${state.conversationId}`);
      renderConversation();
    }

    async function refreshTraces() {
      if (!state.conversationId) return;
      state.traces = await request(`/conversations/${state.conversationId}/traces`);
      renderTraces();
    }

    async function startConversation() {
      const payload = await request("/conversations", {
        method: "POST",
        body: JSON.stringify({
          agent_id: agentSelect.value,
          channel: channelSelect.value,
        }),
      });
      state.agentId = agentSelect.value;
      state.agentName = agentSelect.options[agentSelect.selectedIndex]?.dataset.name || agentSelect.value;
      state.conversationId = payload.conversation.conversation_id;
      state.conversation = payload.conversation;
      state.messages = [];
      state.traces = [];
      const entryMessages = payload.start.emitted_messages || [];
      for (const message of entryMessages) {
        state.messages.push({ kind: "system", text: message.text });
      }
      setBanner(`Conversation ${state.conversationId} started on ${channelSelect.value}.`, "success");
      renderConversation();
      renderMessages();
      renderTraces();
      await refreshTraces();
    }

    async function sendTurn() {
      const text = turnText.value.trim();
      if (!text || !state.conversationId) return;
      state.messages.push({ kind: "user", text });
      renderMessages();
      turnText.value = "";

      const payload = await request(`/conversations/${state.conversationId}/turns`, {
        method: "POST",
        body: JSON.stringify({
          channel: channelSelect.value,
          text,
        }),
      });
      for (const message of payload.emitted_messages || []) {
        state.messages.push({ kind: "system", text: message.text });
      }
      await refreshConversation();
      await refreshTraces();
      renderMessages();
      setBanner(`Turn processed for ${state.conversationId}.`, "success");
    }

    async function copyConversationSnapshot() {
      if (!state.conversation) return;
      const value = JSON.stringify(state.conversation, null, 2);
      if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
        await navigator.clipboard.writeText(value);
      } else {
        const temporary = document.createElement("textarea");
        temporary.value = value;
        temporary.setAttribute("readonly", "true");
        temporary.style.position = "absolute";
        temporary.style.left = "-9999px";
        document.body.appendChild(temporary);
        temporary.select();
        const copied = document.execCommand("copy");
        document.body.removeChild(temporary);
        if (!copied) {
          throw new Error("clipboard is unavailable");
        }
      }
      setBanner("Conversation snapshot copied to the clipboard.", "success");
    }

    function clearTranscript() {
      state.messages = [];
      renderMessages();
      setBanner("Transcript view cleared. Conversation state is unchanged.", "neutral");
    }

    agentSelect.addEventListener("change", () => {
      state.agentId = agentSelect.value;
      state.preferredAgentId = state.agentId;
      state.agentName = agentSelect.options[agentSelect.selectedIndex]?.dataset.name || agentSelect.value;
      persistPreferences();
      renderConversation();
    });
    channelSelect.addEventListener("change", () => {
      persistPreferences();
      setBanner(`Channel preference saved as ${channelSelect.value}.`, "success");
    });
    startButton.addEventListener("click", () => runWithLoading(startButton, "Starting…", startConversation).catch(showError));
    sendTurnButton.addEventListener("click", () => runWithLoading(sendTurnButton, "Sending…", sendTurn).catch(showError));
    reloadAgentsButton.addEventListener("click", () => runWithLoading(reloadAgentsButton, "Reloading…", async () => {
      await request("/agents:reload", { method: "POST" });
      await loadAgents();
    }).catch(showError));
    refreshTracesButton.addEventListener("click", () => runWithLoading(refreshTracesButton, "Refreshing…", refreshTraces).catch(showError));
    copyConversationButton.addEventListener("click", () => runWithLoading(copyConversationButton, "Copying…", copyConversationSnapshot).catch(showError));
    clearTranscriptButton.addEventListener("click", () => clearTranscript());
    turnText.addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        runWithLoading(sendTurnButton, "Sending…", sendTurn).catch(showError);
      }
    });

    function showError(error) {
      setBanner(error.message || String(error), "error");
    }

    Promise.all([loadHealth(), loadAgents()])
      .then(() => {
        renderControls();
      })
      .catch(showError);
  </script>
</body>
</html>
"""
