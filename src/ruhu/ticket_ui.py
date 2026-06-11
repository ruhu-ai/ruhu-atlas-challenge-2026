"""Server-rendered tickets console page (explicitly kept — RP-3.4 decision).

Status (2026-06-11, RP-3.4 sub-step C): **kept and documented**, per the
deletion-list option "retire or explicitly keep-and-document".

Evidence reviewed at decision time:

- Live consumer: ``ticketing_api.install_ticketing_router`` mounts
  ``GET /tickets`` (auth-enabled deployments only) and renders
  ``tickets_page_html()``. The route is part of the auth-enabled OpenAPI
  surface, so deleting this module is a schema-changing operation — not
  allowed under the remediation program's schema-neutral gates.
- Functional successor exists: the React SPA serves ``/tickets``
  (``frontend/src/pages/tickets.tsx``) against the same
  ``/api/tickets/dashboard`` API, so this page is redundant for product
  users reaching the app through the frontend.
- Retirement coupling: this page redirects unauthenticated users to the
  server-rendered ``/login`` (``auth_ui.py``). It belongs to the same
  server-rendered console family as ``routes/console_pages.py`` and should
  be retired together with ``auth_ui.py`` in one deliberate,
  schema-changing commit once the React replacements are ratified.
"""

from __future__ import annotations

from .ui_theme import app_theme_styles


def tickets_page_html() -> str:
    return (
        """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Tickets | Ruhu AI</title>
    <style>
"""
        + app_theme_styles()
        + """

      body {
        margin: 0;
        background:
          radial-gradient(circle at top left, rgba(var(--primary-rgb), 0.14), transparent 36%),
          radial-gradient(circle at top right, rgba(180, 83, 9, 0.10), transparent 28%),
          hsl(var(--background));
        color: hsl(var(--foreground));
      }

      .tickets-page {
        min-height: 100vh;
        padding: 2rem 1.25rem 3rem;
      }

      .tickets-shell {
        width: min(1320px, 100%);
        margin: 0 auto;
        display: grid;
        gap: 1.5rem;
      }

      .tickets-header {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 1rem;
      }

      .tickets-title {
        margin: 0;
        font-size: 2rem;
        font-weight: 700;
        letter-spacing: -0.04em;
      }

      .tickets-subtitle {
        margin: 0.5rem 0 0;
        color: hsl(var(--muted-foreground));
        font-size: var(--text-sm);
      }

      .tickets-actions {
        display: inline-flex;
        gap: 0.75rem;
      }

      .tickets-button,
      .tickets-button-inline {
        appearance: none;
        border: 1px solid hsl(var(--border));
        border-radius: var(--radius);
        background: hsl(var(--card));
        color: hsl(var(--foreground));
        padding: 0.7rem 1rem;
        font: inherit;
        text-decoration: none;
        cursor: pointer;
      }

      .tickets-button-inline {
        padding: 0.45rem 0.75rem;
        font-size: 0.82rem;
      }

      .tickets-button.primary,
      .tickets-button-inline.primary {
        background: hsl(var(--primary));
        color: hsl(var(--primary-foreground));
        border-color: transparent;
      }

      .tickets-button.subtle,
      .tickets-button-inline.subtle {
        background: transparent;
      }

      .tickets-banner {
        border-radius: var(--radius);
        border: 1px solid hsl(var(--border));
        background: hsl(var(--card));
        padding: 0.9rem 1rem;
        font-size: var(--text-sm);
      }

      .tickets-banner.hidden {
        display: none;
      }

      .tickets-banner.error {
        border-color: rgba(185, 28, 28, 0.3);
        background: rgba(220, 38, 38, 0.08);
        color: rgb(185, 28, 28);
      }

      .tickets-banner.success {
        border-color: rgba(21, 128, 61, 0.28);
        background: rgba(34, 197, 94, 0.10);
        color: rgb(21, 128, 61);
      }

      .tickets-cards {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 1rem;
      }

      .tickets-card {
        background: hsl(var(--card));
        border: 1px solid hsl(var(--border));
        border-radius: var(--radius);
        padding: 1rem 1.1rem;
        box-shadow: var(--shadow);
      }

      .tickets-card .label {
        display: block;
        color: hsl(var(--muted-foreground));
        font-size: var(--text-xs);
        text-transform: uppercase;
        letter-spacing: 0.08em;
      }

      .tickets-card .value {
        display: block;
        margin-top: 0.5rem;
        font-size: 1.85rem;
        font-weight: 700;
        letter-spacing: -0.04em;
      }

      .tickets-controls {
        display: grid;
        grid-template-columns: minmax(0, 2fr) repeat(4, minmax(0, 1fr));
        gap: 0.75rem;
        align-items: end;
      }

      .tickets-lower {
        display: grid;
        grid-template-columns: minmax(0, 1.7fr) minmax(320px, 0.95fr);
        gap: 1rem;
      }

      .field {
        display: grid;
        gap: 0.4rem;
      }

      .field label {
        font-size: var(--text-xs);
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: hsl(var(--muted-foreground));
      }

      .tickets-input,
      .tickets-textarea,
      .tickets-select {
        width: 100%;
        border: 1px solid hsl(var(--input));
        border-radius: calc(var(--radius) - 0.1rem);
        padding: 0.8rem 0.9rem;
        font: inherit;
        background: hsl(var(--card));
        color: hsl(var(--foreground));
        box-sizing: border-box;
      }

      .tickets-textarea {
        min-height: 96px;
        resize: vertical;
      }

      .tickets-panel {
        background: hsl(var(--card));
        border: 1px solid hsl(var(--border));
        border-radius: var(--radius);
        box-shadow: var(--shadow);
        overflow: hidden;
      }

      .tickets-panel-body {
        padding: 1rem;
        display: grid;
        gap: 1rem;
      }

      .tickets-panel-head {
        padding: 1rem;
        border-bottom: 1px solid hsl(var(--border));
      }

      .tickets-panel-head h2,
      .tickets-panel-head h3 {
        margin: 0;
        font-size: 1rem;
      }

      .tickets-panel-head p {
        margin: 0.35rem 0 0;
        color: hsl(var(--muted-foreground));
        font-size: var(--text-sm);
      }

      .tickets-table-wrap {
        overflow-x: auto;
      }

      table {
        width: 100%;
        border-collapse: collapse;
      }

      th,
      td {
        padding: 0.95rem 1rem;
        text-align: left;
        border-bottom: 1px solid hsl(var(--border));
        font-size: var(--text-sm);
        vertical-align: top;
      }

      th {
        color: hsl(var(--muted-foreground));
        font-size: var(--text-xs);
        text-transform: uppercase;
        letter-spacing: 0.08em;
      }

      tbody tr {
        cursor: pointer;
      }

      tbody tr:hover {
        background: rgba(var(--primary-rgb), 0.05);
      }

      .subtle {
        color: hsl(var(--muted-foreground));
      }

      .tiny {
        font-size: var(--text-xs);
      }

      .outcome-badge {
        display: inline-flex;
        align-items: center;
        border-radius: 999px;
        padding: 0.2rem 0.55rem;
        font-size: 0.72rem;
        font-weight: 600;
        text-transform: capitalize;
        border: 1px solid transparent;
      }

      .status-badge {
        display: inline-flex;
        align-items: center;
        border-radius: 999px;
        padding: 0.2rem 0.55rem;
        font-size: 0.72rem;
        font-weight: 600;
        border: 1px solid rgba(var(--primary-rgb), 0.16);
        background: rgba(var(--primary-rgb), 0.08);
      }

      .outcome-resolved {
        background: rgba(34, 197, 94, 0.12);
        color: rgb(21, 128, 61);
        border-color: rgba(34, 197, 94, 0.25);
      }

      .outcome-transferred {
        background: rgba(245, 158, 11, 0.12);
        color: rgb(180, 83, 9);
        border-color: rgba(245, 158, 11, 0.28);
      }

      .outcome-failed,
      .outcome-abandoned {
        background: rgba(220, 38, 38, 0.12);
        color: rgb(185, 28, 28);
        border-color: rgba(220, 38, 38, 0.26);
      }

      .sentiment {
        font-weight: 600;
      }

      .sentiment-positive {
        color: rgb(21, 128, 61);
      }

      .sentiment-negative {
        color: rgb(185, 28, 28);
      }

      .sentiment-neutral {
        color: rgb(161, 98, 7);
      }

      .tickets-empty {
        padding: 2rem;
        text-align: center;
        color: hsl(var(--muted-foreground));
      }

      .tickets-detail {
        position: fixed;
        inset: 0;
        display: flex;
        justify-content: flex-end;
        background: rgba(15, 23, 42, 0.18);
        z-index: 50;
      }

      .tickets-detail.hidden {
        display: none;
      }

      .tickets-detail-panel {
        width: min(760px, 100%);
        height: 100%;
        background: hsl(var(--card));
        border-left: 1px solid hsl(var(--border));
        box-shadow: -12px 0 30px rgba(15, 23, 42, 0.16);
        padding: 1.25rem;
        overflow-y: auto;
        display: grid;
        gap: 1rem;
      }

      .detail-head {
        display: flex;
        justify-content: space-between;
        gap: 1rem;
      }

      .detail-head h2 {
        margin: 0;
        font-size: 1.3rem;
        letter-spacing: -0.03em;
      }

      .detail-section {
        border: 1px solid hsl(var(--border));
        border-radius: var(--radius);
        padding: 1rem;
        display: grid;
        gap: 0.9rem;
      }

      .detail-section h3 {
        margin: 0;
        font-size: 0.95rem;
      }

      .detail-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 0.75rem;
      }

      .detail-grid .meta {
        display: grid;
        gap: 0.2rem;
      }

      .detail-grid .meta .label {
        font-size: var(--text-xs);
        color: hsl(var(--muted-foreground));
        text-transform: uppercase;
        letter-spacing: 0.08em;
      }

      .timeline,
      .transcript-list,
      .evidence-list,
      .connection-list,
      .activity-list,
      .case-history-list {
        display: grid;
        gap: 0.75rem;
      }

      .timeline-item,
      .transcript-item,
      .evidence-item,
      .connection-card,
      .activity-item,
      .case-history-item,
      .list-card {
        border: 1px solid hsl(var(--border));
        border-radius: calc(var(--radius) - 0.1rem);
        padding: 0.8rem;
        background: rgba(var(--primary-rgb), 0.02);
      }

      .timeline-item .kind,
      .transcript-item .kind,
      .evidence-item .kind,
      .activity-item .kind,
      .case-history-item .kind {
        display: inline-block;
        margin-bottom: 0.25rem;
        font-size: 0.7rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: hsl(var(--muted-foreground));
      }

      .transcript-item .body,
      .case-history-item .body {
        white-space: pre-wrap;
      }

      .stack {
        display: grid;
        gap: 0.75rem;
      }

      .inline-actions {
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem;
      }

      .split {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 0.75rem;
      }

      .form-card {
        border: 1px dashed hsl(var(--border));
        border-radius: calc(var(--radius) - 0.1rem);
        padding: 0.9rem;
        display: grid;
        gap: 0.75rem;
      }

      .form-card h4 {
        margin: 0;
        font-size: 0.9rem;
      }

      .hidden {
        display: none !important;
      }

      @media (max-width: 1120px) {
        .tickets-lower {
          grid-template-columns: 1fr;
        }
      }

      @media (max-width: 980px) {
        .tickets-cards {
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }

        .tickets-controls {
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }
      }

      @media (max-width: 720px) {
        .tickets-page {
          padding-inline: 0.8rem;
        }

        .tickets-header {
          flex-direction: column;
        }

        .tickets-actions {
          width: 100%;
        }

        .tickets-actions .tickets-button {
          flex: 1;
          text-align: center;
        }

        .tickets-cards,
        .tickets-controls,
        .detail-grid,
        .split {
          grid-template-columns: 1fr;
        }
      }
    </style>
  </head>
  <body>
    <main class="tickets-page">
      <div class="tickets-shell">
        <header class="tickets-header">
          <div>
            <h1 class="tickets-title">Tickets</h1>
            <p class="tickets-subtitle">Recent conversations handled by your agents.</p>
          </div>
          <div class="tickets-actions">
            <a class="tickets-button" href="/app">Workspace</a>
            <a class="tickets-button primary" href="/playground">Open Playground</a>
          </div>
        </header>

        <div id="tickets-banner" class="tickets-banner hidden"></div>

        <section class="tickets-cards">
          <article class="tickets-card">
            <span class="label">Total</span>
            <span id="metric-total" class="value">0</span>
          </article>
          <article class="tickets-card">
            <span class="label">Resolved</span>
            <span id="metric-resolved" class="value">0%</span>
          </article>
          <article class="tickets-card">
            <span class="label">Transferred</span>
            <span id="metric-transferred" class="value">0</span>
          </article>
          <article class="tickets-card">
            <span class="label">Avg Duration</span>
            <span id="metric-duration" class="value">0s</span>
          </article>
        </section>

        <section class="tickets-controls">
          <div class="field">
            <label for="filter-search">Search tickets</label>
            <input id="filter-search" class="tickets-input" type="search" placeholder="Search tickets..." />
          </div>
          <div class="field">
            <label for="filter-handler">Agent</label>
            <select id="filter-handler" class="tickets-select">
              <option value="">All agents</option>
            </select>
          </div>
          <div class="field">
            <label for="filter-channel">Channel</label>
            <select id="filter-channel" class="tickets-select">
              <option value="">All</option>
              <option value="phone">Voice</option>
              <option value="web_chat">Chat</option>
              <option value="web_widget">Widget</option>
              <option value="whatsapp">WhatsApp</option>
            </select>
          </div>
          <div class="field">
            <label for="filter-outcome">Outcome</label>
            <select id="filter-outcome" class="tickets-select">
              <option value="">All outcomes</option>
              <option value="resolved">Resolved</option>
              <option value="transferred">Transferred</option>
              <option value="abandoned">Abandoned</option>
              <option value="failed">Failed</option>
            </select>
          </div>
          <div class="field">
            <label for="filter-days">Time window</label>
            <select id="filter-days" class="tickets-select">
              <option value="1">Last 24 hours</option>
              <option value="7" selected>Last 7 days</option>
              <option value="30">Last 30 days</option>
              <option value="90">Last 90 days</option>
            </select>
          </div>
        </section>

        <section class="tickets-panel tickets-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Created</th>
                <th>Sentiment</th>
                <th>Agent</th>
                <th>From</th>
                <th>Channel</th>
                <th>Resolution</th>
                <th>Duration</th>
              </tr>
            </thead>
            <tbody id="tickets-table-body">
              <tr><td colspan="7" class="tickets-empty">Loading tickets…</td></tr>
            </tbody>
          </table>
        </section>

        <section class="tickets-lower">
          <div class="tickets-panel">
            <div class="tickets-panel-head">
              <h2>Ticketing Connections</h2>
              <p>Manage Zendesk, Freshdesk, and Jira connections plus outbound activity.</p>
            </div>
            <div class="tickets-panel-body">
              <div id="connection-access-note" class="subtle tiny hidden"></div>
              <div class="connection-list" id="connection-list">
                <div class="tickets-empty">Loading connections…</div>
              </div>
              <form id="connection-create-form" class="form-card">
                <h4>Create connection</h4>
                <div class="split">
                  <div class="field">
                    <label for="connection-provider">Provider</label>
                    <select id="connection-provider" class="tickets-select">
                      <option value="zendesk">Zendesk</option>
                      <option value="freshdesk">Freshdesk</option>
                      <option value="jira">Jira</option>
                    </select>
                  </div>
                  <div class="field">
                    <label for="connection-auth-type">Auth type</label>
                    <select id="connection-auth-type" class="tickets-select">
                      <option value="api_token">API token</option>
                      <option value="bearer">Bearer</option>
                    </select>
                  </div>
                </div>
                <div class="field">
                  <label for="connection-display-name">Display name</label>
                  <input id="connection-display-name" class="tickets-input" type="text" placeholder="Acme Zendesk" />
                </div>
                <div class="field">
                  <label for="connection-credentials-ref">Credentials ref</label>
                  <input id="connection-credentials-ref" class="tickets-input" type="text" placeholder="env:RUHU_ZENDESK_TOKEN" />
                </div>
                <div class="split">
                  <div class="field">
                    <label for="connection-base-url">Base URL</label>
                    <input id="connection-base-url" class="tickets-input" type="url" placeholder="https://acme.zendesk.com" />
                  </div>
                  <div class="field">
                    <label for="connection-default-queue">Default queue</label>
                    <input id="connection-default-queue" class="tickets-input" type="text" placeholder="support" />
                  </div>
                </div>
                <button id="connection-create" class="tickets-button primary" type="submit">Create connection</button>
              </form>
              <form id="connection-edit-form" class="form-card hidden">
                <h4>Edit selected connection</h4>
                <div class="field">
                  <label for="connection-edit-display-name">Display name</label>
                  <input id="connection-edit-display-name" class="tickets-input" type="text" />
                </div>
                <div class="split">
                  <div class="field">
                    <label for="connection-edit-status">Status</label>
                    <select id="connection-edit-status" class="tickets-select">
                      <option value="pending">Pending</option>
                      <option value="active">Active</option>
                      <option value="disabled">Disabled</option>
                      <option value="degraded">Degraded</option>
                      <option value="error">Error</option>
                    </select>
                  </div>
                  <div class="field">
                    <label for="connection-edit-default-queue">Default queue</label>
                    <input id="connection-edit-default-queue" class="tickets-input" type="text" />
                  </div>
                </div>
                <div class="field">
                  <label for="connection-edit-credentials-ref">Credentials ref</label>
                  <input id="connection-edit-credentials-ref" class="tickets-input" type="text" />
                </div>
                <div class="field">
                  <label for="connection-edit-base-url">Base URL</label>
                  <input id="connection-edit-base-url" class="tickets-input" type="url" />
                </div>
                <div class="inline-actions">
                  <button id="connection-edit-save" class="tickets-button-inline primary" type="submit">Save connection</button>
                  <button id="connection-disable" class="tickets-button-inline" type="button">Disable</button>
                  <button id="connection-enable" class="tickets-button-inline" type="button">Enable</button>
                </div>
              </form>
            </div>
          </div>

          <div class="tickets-panel">
            <div class="tickets-panel-head">
              <h2>Connection Activity</h2>
              <p>Outbound sync attempts, health checks, and inbound webhook processing.</p>
            </div>
            <div class="tickets-panel-body">
              <div class="field">
                <label for="activity-connection-id">Selected connection</label>
                <select id="activity-connection-id" class="tickets-select">
                  <option value="">Pick a connection</option>
                </select>
              </div>
              <div class="inline-actions">
                <button id="activity-refresh" class="tickets-button-inline" type="button">Refresh activity</button>
                <button id="activity-health" class="tickets-button-inline primary" type="button">Run health check</button>
                <button id="activity-process-retries" class="tickets-button-inline" type="button">Process retries</button>
              </div>
              <div class="activity-list" id="connection-activity-list">
                <div class="tickets-empty">Select a connection to inspect activity.</div>
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>

    <aside id="tickets-detail" class="tickets-detail hidden" aria-hidden="true">
      <div class="tickets-detail-panel">
        <div class="detail-head">
          <div>
            <h2 id="detail-title">Conversation detail</h2>
            <p id="detail-subtitle" class="tickets-subtitle">Loading…</p>
          </div>
          <button id="detail-close" class="tickets-button" type="button">Close</button>
        </div>

        <section class="detail-section">
          <h3>Overview</h3>
          <div id="detail-overview" class="detail-grid"></div>
        </section>

        <section class="detail-section">
          <h3>Transcript</h3>
          <div id="detail-transcript" class="transcript-list"></div>
        </section>

        <section class="detail-section">
          <h3>Evidence</h3>
          <div id="detail-evidence" class="evidence-list"></div>
        </section>

        <section class="detail-section">
          <h3>Support Case Actions</h3>
          <form id="support-case-create-form" class="form-card">
            <h4>Create support case</h4>
            <div class="field">
              <label for="case-form-title">Title</label>
              <input id="case-form-title" class="tickets-input" type="text" placeholder="Follow up with customer" />
            </div>
            <div class="field">
              <label for="case-form-description">Description</label>
              <textarea id="case-form-description" class="tickets-textarea" placeholder="Summarize the issue or follow-up needed."></textarea>
            </div>
            <div class="split">
              <div class="field">
                <label for="case-form-category">Category</label>
                <input id="case-form-category" class="tickets-input" type="text" placeholder="handoff" />
              </div>
              <div class="field">
                <label for="case-form-priority">Priority</label>
                <select id="case-form-priority" class="tickets-select">
                  <option value="low">Low</option>
                  <option value="medium" selected>Medium</option>
                  <option value="high">High</option>
                  <option value="urgent">Urgent</option>
                </select>
              </div>
            </div>
            <button id="support-case-create" class="tickets-button primary" type="submit">Create support case</button>
          </form>
        </section>

        <section class="detail-section">
          <h3>Support Cases</h3>
          <div id="detail-cases" class="stack"></div>
        </section>

        <section class="detail-section">
          <h3>External Cases</h3>
          <form id="external-case-create-form" class="form-card">
            <h4>Create remote case</h4>
            <div class="split">
              <div class="field">
                <label for="external-connection-id">Connection</label>
                <select id="external-connection-id" class="tickets-select">
                  <option value="">Pick a connection</option>
                </select>
              </div>
              <div class="field">
                <label for="external-support-case-id">Support case</label>
                <select id="external-support-case-id" class="tickets-select">
                  <option value="">None</option>
                </select>
              </div>
            </div>
            <div class="field">
              <label for="external-title">Title override</label>
              <input id="external-title" class="tickets-input" type="text" placeholder="Leave empty to use the support case title" />
            </div>
            <div class="field">
              <label for="external-description">Description override</label>
              <textarea id="external-description" class="tickets-textarea" placeholder="Leave empty to use the support case description"></textarea>
            </div>
            <button id="external-case-create" class="tickets-button primary" type="submit">Create external case</button>
          </form>
          <form id="external-case-link-form" class="form-card">
            <h4>Link existing remote case</h4>
            <div class="split">
              <div class="field">
                <label for="external-search-query">Search query</label>
                <input id="external-search-query" class="tickets-input" type="text" placeholder="Case key, title, or external id" />
              </div>
              <div class="field">
                <label for="external-search-limit">Result limit</label>
                <select id="external-search-limit" class="tickets-select">
                  <option value="5">5</option>
                  <option value="10" selected>10</option>
                  <option value="20">20</option>
                </select>
              </div>
            </div>
            <div class="inline-actions">
              <button id="external-search" class="tickets-button-inline" type="submit">Search remote cases</button>
            </div>
            <div id="external-remote-search-results" class="stack">
              <div class="subtle">Search a selected connection to link an existing remote case.</div>
            </div>
          </form>
          <div id="detail-external" class="stack"></div>
        </section>

        <section class="detail-section">
          <h3>Timeline</h3>
          <div id="detail-timeline" class="timeline"></div>
        </section>
      </div>
    </aside>

    <script>
      const state = {
        dashboard: null,
        selectedConversationId: null,
        selectedDetail: null,
        connections: [],
        selectedConnectionId: "",
        connectionAccess: "unknown",
        remoteSearchResults: [],
      };

      const controls = {
        search: document.getElementById("filter-search"),
        handler: document.getElementById("filter-handler"),
        channel: document.getElementById("filter-channel"),
        outcome: document.getElementById("filter-outcome"),
        days: document.getElementById("filter-days"),
      };

      function escapeHTML(value) {
        return String(value ?? "")
          .replaceAll("&", "&amp;")
          .replaceAll("<", "&lt;")
          .replaceAll(">", "&gt;")
          .replaceAll('"', "&quot;")
          .replaceAll("'", "&#39;");
      }

      function showBanner(message, tone = "error") {
        const banner = document.getElementById("tickets-banner");
        if (!message) {
          banner.textContent = "";
          banner.className = "tickets-banner hidden";
          return;
        }
        banner.textContent = message;
        banner.className = "tickets-banner " + tone;
      }

      async function requestJSON(url, options = {}) {
        const requestOptions = {
          method: options.method || "GET",
          credentials: "same-origin",
          headers: { ...(options.headers || {}) },
        };
        if (options.body !== undefined) {
          requestOptions.headers["Content-Type"] = "application/json";
          requestOptions.body = JSON.stringify(options.body);
        }
        const response = await fetch(url, requestOptions);
        let payload = null;
        try {
          payload = await response.json();
        } catch (_error) {
          payload = null;
        }
        if (response.status === 401) {
          window.location.assign("/login");
          throw new Error("authentication required");
        }
        if (!response.ok) {
          throw new Error((payload && payload.detail) || "Request failed");
        }
        return payload;
      }

      function durationLabel(seconds) {
        const value = Number(seconds || 0);
        if (!value) return "0s";
        if (value >= 60) {
          const minutes = Math.floor(value / 60);
          const remaining = value % 60;
          return remaining ? `${minutes}m ${remaining}s` : `${minutes}m`;
        }
        return `${value}s`;
      }

      function createdLabel(value) {
        if (!value) return "--";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return String(value);
        const now = new Date();
        const isToday = date.toDateString() === now.toDateString();
        if (isToday) {
          return "Today, " + date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        }
        return date.toLocaleDateString([], { day: "numeric", month: "short" }) + ", " +
          date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      }

      function prettyJson(value) {
        if (!value || (typeof value === "object" && !Object.keys(value).length)) return "";
        return JSON.stringify(value, null, 2);
      }

      function sentimentLabel(score) {
        if (score == null || score === undefined) return '<span class="subtle">--</span>';
        if (score > 0.3) return '<span class="sentiment sentiment-positive">Positive</span>';
        if (score < -0.3) return '<span class="sentiment sentiment-negative">Negative</span>';
        return '<span class="sentiment sentiment-neutral">Neutral</span>';
      }

      function outcomeBadge(value) {
        if (!value) return '<span class="subtle">--</span>';
        return `<span class="outcome-badge outcome-${escapeHTML(value)}">${escapeHTML(value)}</span>`;
      }

      function statusBadge(value) {
        if (!value) return '<span class="subtle">--</span>';
        return `<span class="status-badge">${escapeHTML(String(value).replaceAll("_", " "))}</span>`;
      }

      function currentFilters() {
        const params = new URLSearchParams();
        if (controls.search.value.trim()) params.set("q", controls.search.value.trim());
        if (controls.handler.value) params.set("handler_id", controls.handler.value);
        if (controls.channel.value) params.set("channel", controls.channel.value);
        if (controls.outcome.value) params.set("outcome", controls.outcome.value);
        if (controls.days.value) params.set("days", controls.days.value);
        return params;
      }

      function renderHandlers(handlers) {
        const selected = controls.handler.value;
        controls.handler.innerHTML = '<option value="">All agents</option>' + handlers.map((item) =>
          `<option value="${escapeHTML(item.handler_id)}"${item.handler_id === selected ? " selected" : ""}>${escapeHTML(item.handler_name)}</option>`
        ).join("");
      }

      function renderSummary(summary) {
        document.getElementById("metric-total").textContent = String(summary.total_count || 0);
        document.getElementById("metric-resolved").textContent = `${Number(summary.resolved_rate || 0).toFixed(1)}%`;
        document.getElementById("metric-transferred").textContent = String(summary.transferred_count || 0);
        document.getElementById("metric-duration").textContent = durationLabel(summary.average_duration_seconds);
      }

      function renderRows(items) {
        const body = document.getElementById("tickets-table-body");
        if (!items.length) {
          body.innerHTML = '<tr><td colspan="7" class="tickets-empty">No tickets matched the current filters.</td></tr>';
          return;
        }
        body.innerHTML = items.map((item) => `
          <tr data-conversation-id="${escapeHTML(item.conversation_id)}">
            <td>${escapeHTML(createdLabel(item.started_at))}</td>
            <td>${sentimentLabel(item.sentiment_score)}</td>
            <td>
              <div>${escapeHTML(item.handler_name)}</div>
              <div class="subtle">${escapeHTML(item.handler_id)}</div>
            </td>
            <td>
              <div>${escapeHTML(item.participant_display)}</div>
              <div class="subtle">${escapeHTML(item.participant_ref || "--")}</div>
            </td>
            <td>${escapeHTML(item.channel || "--")}</td>
            <td>${outcomeBadge(item.outcome)}</td>
            <td>${escapeHTML(durationLabel(item.duration_seconds))}</td>
          </tr>
        `).join("");
      }

      function renderConnectionSelects() {
        const options = ['<option value="">Pick a connection</option>'].concat(
          state.connections.map((item) =>
            `<option value="${escapeHTML(item.connection_id)}"${item.connection_id === state.selectedConnectionId ? " selected" : ""}>${escapeHTML(item.display_name)} (${escapeHTML(item.provider)})</option>`
          )
        ).join("");
        document.getElementById("activity-connection-id").innerHTML = options;
        document.getElementById("external-connection-id").innerHTML = options;
      }

      function selectedConnection() {
        return state.connections.find((item) => item.connection_id === state.selectedConnectionId) || null;
      }

      function populateConnectionEditor() {
        const form = document.getElementById("connection-edit-form");
        const connection = selectedConnection();
        if (state.connectionAccess !== "allowed" || !connection) {
          form.classList.add("hidden");
          return;
        }
        form.classList.remove("hidden");
        document.getElementById("connection-edit-display-name").value = connection.display_name || "";
        document.getElementById("connection-edit-status").value = connection.status || "pending";
        document.getElementById("connection-edit-default-queue").value = connection.default_queue || "";
        document.getElementById("connection-edit-credentials-ref").value = connection.credentials_ref || "";
        document.getElementById("connection-edit-base-url").value = (connection.provider_config && connection.provider_config.base_url) || "";
      }

      function renderConnections() {
        const target = document.getElementById("connection-list");
        if (state.connectionAccess === "forbidden") {
          target.innerHTML = '<div class="tickets-empty">Ticketing connections are admin-only.</div>';
          document.getElementById("connection-access-note").textContent = "Ticketing connections are only available to organization admins.";
          document.getElementById("connection-access-note").classList.remove("hidden");
          return;
        }
        document.getElementById("connection-access-note").classList.add("hidden");
        if (!state.connections.length) {
          target.innerHTML = '<div class="tickets-empty">No ticketing connections configured yet.</div>';
          return;
        }
        target.innerHTML = state.connections.map((item) => `
          <article class="connection-card" data-connection-id="${escapeHTML(item.connection_id)}">
            <div><strong>${escapeHTML(item.display_name)}</strong></div>
            <div class="subtle">${escapeHTML(item.provider)} • ${escapeHTML(item.auth_type)} • ${escapeHTML(item.status)}</div>
            <div class="subtle tiny">${escapeHTML(item.credentials_ref || "No credentials ref")}</div>
            <div class="inline-actions">
              <button class="tickets-button-inline" type="button" data-select-connection="${escapeHTML(item.connection_id)}">Inspect</button>
              <button class="tickets-button-inline primary" type="button" data-health-connection="${escapeHTML(item.connection_id)}">Health check</button>
            </div>
          </article>
        `).join("");
      }

      function renderConnectionActivity(items) {
        const target = document.getElementById("connection-activity-list");
        if (!items.length) {
          target.innerHTML = '<div class="tickets-empty">No activity recorded yet.</div>';
          return;
        }
        target.innerHTML = items.map((item) => `
          <article class="activity-item">
            <span class="kind">${escapeHTML(item.direction)} • ${escapeHTML(item.action)}</span>
            <div><strong>${escapeHTML(item.status)}</strong>${item.external_case_id ? ` • ${escapeHTML(item.external_case_id)}` : ""}</div>
            ${item.retry_status && item.retry_status !== "none" ? `<div class="subtle tiny">Retry: ${escapeHTML(item.retry_status)}${item.next_retry_at ? ` • next ${escapeHTML(createdLabel(item.next_retry_at))}` : ""}${item.attempt_count ? ` • attempts ${escapeHTML(String(item.attempt_count))}` : ""}</div>` : ""}
            ${item.error_message ? `<div class="subtle">${escapeHTML(item.error_message)}</div>` : ""}
            ${item.request && Object.keys(item.request).length ? `<pre class="subtle tiny">${escapeHTML(prettyJson(item.request))}</pre>` : ""}
            ${item.response && Object.keys(item.response).length ? `<pre class="subtle tiny">${escapeHTML(prettyJson(item.response))}</pre>` : ""}
            ${(item.retry_status === "pending" || item.retry_status === "exhausted") ? `<div class="inline-actions"><button class="tickets-button-inline" type="button" data-retry-activity="${escapeHTML(item.activity_id)}">Retry now</button></div>` : ""}
            <div class="subtle tiny">${escapeHTML(createdLabel(item.created_at))}${item.duration_ms ? ` • ${escapeHTML(String(item.duration_ms))}ms` : ""}</div>
          </article>
        `).join("");
      }

      async function loadConnections({ preserveSelection = true } = {}) {
        try {
          const payload = await requestJSON("/ticketing/connections");
          state.connectionAccess = "allowed";
          if (!preserveSelection) {
            state.selectedConnectionId = "";
          }
          state.connections = payload || [];
          if (!state.selectedConnectionId && state.connections.length) {
            state.selectedConnectionId = state.connections[0].connection_id;
          }
          renderConnections();
          renderConnectionSelects();
          populateConnectionEditor();
          if (state.selectedConnectionId) {
            await loadConnectionActivity(state.selectedConnectionId);
          } else {
            renderConnectionActivity([]);
          }
        } catch (error) {
          if (error instanceof Error && /403|admin|forbidden/i.test(error.message)) {
            state.connectionAccess = "forbidden";
            state.connections = [];
            renderConnections();
            renderConnectionActivity([]);
            return;
          }
          state.connections = [];
          renderConnections();
          renderConnectionActivity([]);
        }
      }

      async function loadConnectionActivity(connectionId) {
        if (!connectionId) {
          renderConnectionActivity([]);
          return;
        }
        state.selectedConnectionId = connectionId;
        renderConnectionSelects();
        populateConnectionEditor();
        try {
          const activity = await requestJSON(`/ticketing/connections/${encodeURIComponent(connectionId)}/activity`);
          renderConnectionActivity(activity || []);
        } catch (error) {
          renderConnectionActivity([]);
          throw error;
        }
      }

      async function runConnectionHealth(connectionId) {
        await requestJSON(`/ticketing/connections/${encodeURIComponent(connectionId)}/health-check`, { method: "POST" });
        await loadConnections();
        showBanner("Connection health check completed.", "success");
      }

      async function retryTicketingActivity(activityId) {
        await requestJSON(`/ticketing/activities/${encodeURIComponent(activityId)}/retry`, { method: "POST" });
        if (state.selectedConnectionId) {
          await loadConnectionActivity(state.selectedConnectionId);
        }
        showBanner("Ticketing activity retried.", "success");
      }

      async function processRetryQueue() {
        const processed = await requestJSON("/ticketing/activities/process-retries", {
          method: "POST",
          body: {
            connection_id: state.selectedConnectionId || null,
            limit: 25,
            force: true,
          },
        });
        if (state.selectedConnectionId) {
          await loadConnectionActivity(state.selectedConnectionId);
        }
        showBanner(`Processed ${Array.isArray(processed) ? processed.length : 0} retry task(s).`, "success");
      }

      async function createConnection() {
        const provider = document.getElementById("connection-provider").value;
        const authType = document.getElementById("connection-auth-type").value;
        const displayName = document.getElementById("connection-display-name").value.trim();
        const credentialsRef = document.getElementById("connection-credentials-ref").value.trim();
        const baseUrl = document.getElementById("connection-base-url").value.trim();
        const defaultQueue = document.getElementById("connection-default-queue").value.trim();
        const created = await requestJSON("/ticketing/connections", {
          method: "POST",
          body: {
            provider,
            display_name: displayName,
            auth_type: authType,
            credentials_ref: credentialsRef || null,
            provider_config: baseUrl ? { base_url: baseUrl } : {},
            default_queue: defaultQueue || null,
          },
        });
        document.getElementById("connection-create-form").reset();
        state.selectedConnectionId = created.connection_id;
        await loadConnections({ preserveSelection: true });
        showBanner("Ticketing connection created.", "success");
      }

      async function saveConnectionEdit() {
        const connection = selectedConnection();
        if (!connection) {
          throw new Error("choose a connection first");
        }
        await requestJSON(`/ticketing/connections/${encodeURIComponent(connection.connection_id)}`, {
          method: "PATCH",
          body: {
            display_name: document.getElementById("connection-edit-display-name").value.trim(),
            status: document.getElementById("connection-edit-status").value,
            credentials_ref: document.getElementById("connection-edit-credentials-ref").value.trim() || null,
            provider_config: document.getElementById("connection-edit-base-url").value.trim()
              ? { ...(connection.provider_config || {}), base_url: document.getElementById("connection-edit-base-url").value.trim() }
              : { ...(connection.provider_config || {}) },
            default_queue: document.getElementById("connection-edit-default-queue").value.trim() || null,
          },
        });
        await loadConnections({ preserveSelection: true });
        showBanner("Connection updated.", "success");
      }

      async function setConnectionStatus(statusValue) {
        const connection = selectedConnection();
        if (!connection) {
          throw new Error("choose a connection first");
        }
        await requestJSON(`/ticketing/connections/${encodeURIComponent(connection.connection_id)}`, {
          method: "PATCH",
          body: { status: statusValue },
        });
        await loadConnections({ preserveSelection: true });
        showBanner(`Connection ${statusValue}.`, "success");
      }

      function renderTranscript(items) {
        const target = document.getElementById("detail-transcript");
        if (!items.length) {
          target.innerHTML = '<div class="subtle">No transcript entries available.</div>';
          return;
        }
        target.innerHTML = items.map((item) => `
          <article class="transcript-item">
            <span class="kind">${escapeHTML(item.role)} • ${escapeHTML(item.source)}</span>
            <div class="body">${escapeHTML(item.text || "--")}</div>
            <div class="subtle tiny">${escapeHTML(createdLabel(item.recorded_at))}${item.channel ? ` • ${escapeHTML(item.channel)}` : ""}</div>
          </article>
        `).join("");
      }

      function renderEvidence(items) {
        const target = document.getElementById("detail-evidence");
        if (!items.length) {
          target.innerHTML = '<div class="subtle">No evidence captured yet.</div>';
          return;
        }
        target.innerHTML = items.map((item) => `
          <article class="evidence-item">
            <span class="kind">${escapeHTML(item.kind)}</span>
            <div><strong>${escapeHTML(item.label)}</strong>${item.status ? ` • ${escapeHTML(item.status)}` : ""}</div>
            ${item.detail ? `<div class="subtle">${escapeHTML(item.detail)}</div>` : ""}
            ${item.metadata && Object.keys(item.metadata).length ? `<pre class="subtle tiny">${escapeHTML(prettyJson(item.metadata))}</pre>` : ""}
            <div class="subtle tiny">${escapeHTML(createdLabel(item.recorded_at))}</div>
          </article>
        `).join("");
      }

      function renderSupportCaseSelect(cases) {
        const target = document.getElementById("external-support-case-id");
        const options = ['<option value="">None</option>'].concat(
          (cases || []).map((item) => `<option value="${escapeHTML(item.case_id)}">${escapeHTML(item.case_number)} • ${escapeHTML(item.title)}</option>`)
        );
        target.innerHTML = options.join("");
      }

      function renderTimeline(items) {
        const target = document.getElementById("detail-timeline");
        if (!items.length) {
          target.innerHTML = '<div class="subtle">No detail timeline available.</div>';
          return;
        }
        target.innerHTML = items.map((item) => `
          <article class="timeline-item">
            <span class="kind">${escapeHTML(item.kind.replaceAll("_", " "))}</span>
            <div>${escapeHTML(item.label)}</div>
            ${item.detail ? `<div class="subtle">${escapeHTML(item.detail)}</div>` : ""}
            <div class="subtle tiny">${escapeHTML(createdLabel(item.recorded_at))}</div>
          </article>
        `).join("");
      }

      async function loadCaseHistory(caseId) {
        const [notes, events] = await Promise.all([
          requestJSON(`/support-cases/${encodeURIComponent(caseId)}/notes`),
          requestJSON(`/support-cases/${encodeURIComponent(caseId)}/events`),
        ]);
        return { notes: notes || [], events: events || [] };
      }

      function renderCaseHistory(history) {
        const entries = []
          .concat((history.events || []).map((item) => ({ kind: "event", recorded_at: item.created_at, label: item.event_type, body: prettyJson(item.details || {}) })))
          .concat((history.notes || []).map((item) => ({ kind: "note", recorded_at: item.created_at, label: item.visibility, body: item.body })))
          .sort((left, right) => new Date(left.recorded_at).getTime() - new Date(right.recorded_at).getTime());
        if (!entries.length) {
          return '<div class="subtle">No case history yet.</div>';
        }
        return entries.map((entry) => `
          <article class="case-history-item">
            <span class="kind">${escapeHTML(entry.kind)} • ${escapeHTML(entry.label)}</span>
            ${entry.body ? `<div class="body">${escapeHTML(entry.body)}</div>` : ""}
            <div class="subtle tiny">${escapeHTML(createdLabel(entry.recorded_at))}</div>
          </article>
        `).join("");
      }

      function renderRemoteSearchResults(items) {
        const target = document.getElementById("external-remote-search-results");
        if (!items.length) {
          target.innerHTML = '<div class="subtle">No remote cases matched the current query.</div>';
          return;
        }
        target.innerHTML = items.map((item) => `
          <article class="list-card">
            <div><strong>${escapeHTML(item.external_case_key || item.external_case_id)}</strong></div>
            <div class="subtle">${escapeHTML(item.provider)}${item.external_case_status ? ` • ${escapeHTML(item.external_case_status)}` : ""}</div>
            ${item.external_case_url ? `<div class="subtle tiny">${escapeHTML(item.external_case_url)}</div>` : ""}
            <div class="inline-actions">
              <button class="tickets-button-inline primary" type="button" data-link-remote-case="${escapeHTML(item.external_case_id)}">Link case</button>
            </div>
          </article>
        `).join("");
      }

      async function renderCases(cases) {
        const target = document.getElementById("detail-cases");
        if (!cases.length) {
          target.innerHTML = '<div class="subtle">No support cases linked yet.</div>';
          renderSupportCaseSelect([]);
          return;
        }
        renderSupportCaseSelect(cases);
        const histories = await Promise.all(cases.map((item) => loadCaseHistory(item.case_id)));
        target.innerHTML = cases.map((item, index) => `
          <article class="list-card" data-case-id="${escapeHTML(item.case_id)}">
            <div><strong>${escapeHTML(item.case_number)} • ${escapeHTML(item.title)}</strong></div>
            <div class="subtle">${escapeHTML(item.status)} • ${escapeHTML(item.priority)} • ${escapeHTML(item.category)}</div>
            <div class="subtle tiny">${escapeHTML(item.description || "")}</div>
            <div class="inline-actions">
              <button class="tickets-button-inline" type="button" data-note-case="${escapeHTML(item.case_id)}">Add note</button>
              <button class="tickets-button-inline" type="button" data-resolve-case="${escapeHTML(item.case_id)}">Resolve</button>
              <button class="tickets-button-inline" type="button" data-close-case="${escapeHTML(item.case_id)}">Close</button>
            </div>
            <div class="form-card hidden" id="case-note-form-${escapeHTML(item.case_id)}">
              <h4>Add note</h4>
              <div class="field">
                <label for="case-note-${escapeHTML(item.case_id)}">Note</label>
                <textarea id="case-note-${escapeHTML(item.case_id)}" class="tickets-textarea" placeholder="Internal follow-up note"></textarea>
              </div>
              <button class="tickets-button-inline primary" type="button" data-submit-note="${escapeHTML(item.case_id)}">Save note</button>
            </div>
            <div class="form-card hidden" id="case-resolve-form-${escapeHTML(item.case_id)}">
              <h4>Resolve case</h4>
              <div class="split">
                <div class="field">
                  <label for="case-resolution-type-${escapeHTML(item.case_id)}">Resolution type</label>
                  <input id="case-resolution-type-${escapeHTML(item.case_id)}" class="tickets-input" type="text" placeholder="resolved" />
                </div>
                <div class="field">
                  <label for="case-resolution-summary-${escapeHTML(item.case_id)}">Summary</label>
                  <input id="case-resolution-summary-${escapeHTML(item.case_id)}" class="tickets-input" type="text" placeholder="Issue resolved" />
                </div>
              </div>
              <button class="tickets-button-inline primary" type="button" data-submit-resolve="${escapeHTML(item.case_id)}">Confirm resolve</button>
            </div>
            <div class="case-history-list" id="case-history-${escapeHTML(item.case_id)}">
              ${renderCaseHistory(histories[index])}
            </div>
          </article>
        `).join("");
      }

      function renderExternalCases(links) {
        const target = document.getElementById("detail-external");
        if (!links.length) {
          target.innerHTML = '<div class="subtle">No external cases linked yet.</div>';
          return;
        }
        target.innerHTML = links.map((item) => `
          <article class="list-card">
            <div><strong>${escapeHTML(item.provider)} • ${escapeHTML(item.external_case_key || item.external_case_id)}</strong></div>
            <div class="subtle">${escapeHTML(item.sync_status)}${item.external_case_status ? ` • ${escapeHTML(item.external_case_status)}` : ""}</div>
            ${item.external_case_url ? `<div><a href="${escapeHTML(item.external_case_url)}" target="_blank" rel="noreferrer">Open remote case</a></div>` : ""}
            <div class="inline-actions">
              <button class="tickets-button-inline" type="button" data-sync-link="${escapeHTML(item.link_id)}">Sync now</button>
              <button class="tickets-button-inline" type="button" data-comment-link="${escapeHTML(item.link_id)}">Comment</button>
              <button class="tickets-button-inline" type="button" data-transition-link="${escapeHTML(item.link_id)}">Transition</button>
            </div>
            <div class="form-card hidden" id="external-comment-form-${escapeHTML(item.link_id)}">
              <h4>Add external comment</h4>
              <div class="field">
                <label for="external-comment-body-${escapeHTML(item.link_id)}">Comment</label>
                <textarea id="external-comment-body-${escapeHTML(item.link_id)}" class="tickets-textarea" placeholder="Internal note for the external case"></textarea>
              </div>
              <button class="tickets-button-inline primary" type="button" data-submit-comment="${escapeHTML(item.link_id)}">Send comment</button>
            </div>
            <div class="form-card hidden" id="external-transition-form-${escapeHTML(item.link_id)}">
              <h4>Transition external case</h4>
              <div class="field">
                <label for="external-transition-status-${escapeHTML(item.link_id)}">Status</label>
                <input id="external-transition-status-${escapeHTML(item.link_id)}" class="tickets-input" type="text" value="${escapeHTML(item.external_case_status || "")}" />
              </div>
              <button class="tickets-button-inline primary" type="button" data-submit-transition="${escapeHTML(item.link_id)}">Apply transition</button>
            </div>
          </article>
        `).join("");
      }

      async function renderDetail(detail) {
        state.selectedDetail = detail;
        document.getElementById("detail-title").textContent = detail.conversation.summary || detail.conversation.participant_display;
        document.getElementById("detail-subtitle").textContent = detail.conversation.conversation_id;

        document.getElementById("detail-overview").innerHTML = [
          ["Agent", detail.conversation.handler_name],
          ["From", detail.conversation.participant_display],
          ["Channel", detail.conversation.channel || "--"],
          ["Outcome", detail.conversation.outcome || "--"],
          ["Started", createdLabel(detail.conversation.started_at)],
          ["Duration", durationLabel(detail.conversation.duration_seconds)],
        ].map(([label, value]) => `
          <div class="meta">
            <span class="label">${escapeHTML(label)}</span>
            <span>${escapeHTML(value)}</span>
          </div>
        `).join("");

        renderTranscript(detail.transcript || []);
        renderEvidence(detail.evidence || []);
        await renderCases(detail.support_cases || []);
        renderExternalCases(detail.external_case_links || []);
        renderRemoteSearchResults(state.remoteSearchResults);
        renderTimeline(detail.timeline || []);

        document.getElementById("case-form-title").value = detail.conversation.summary || `${detail.conversation.participant_display} follow-up`;
        document.getElementById("case-form-description").value = detail.conversation.summary || "";
        const shell = document.getElementById("tickets-detail");
        shell.classList.remove("hidden");
        shell.setAttribute("aria-hidden", "false");
      }

      async function loadDashboard() {
        showBanner("");
        const payload = await requestJSON("/api/tickets/dashboard?" + currentFilters().toString());
        state.dashboard = payload;
        renderHandlers(payload.handlers || []);
        renderSummary(payload.summary || {});
        renderRows(payload.items || []);
      }

      async function loadDetail(conversationId) {
        const detail = await requestJSON("/api/tickets/conversations/" + encodeURIComponent(conversationId));
        state.selectedConversationId = conversationId;
        await renderDetail(detail);
      }

      async function createSupportCaseFromDetail() {
        if (!state.selectedConversationId) {
          throw new Error("select a conversation first");
        }
        await requestJSON("/support-cases", {
          method: "POST",
          body: {
            title: document.getElementById("case-form-title").value.trim(),
            description: document.getElementById("case-form-description").value.trim(),
            priority: document.getElementById("case-form-priority").value,
            category: document.getElementById("case-form-category").value.trim() || "follow_up",
            source: "manual",
            primary_conversation_id: state.selectedConversationId,
          },
        });
        await Promise.all([loadDashboard(), loadDetail(state.selectedConversationId)]);
        showBanner("Support case created.", "success");
      }

      async function addCaseNote(caseId) {
        const body = document.getElementById(`case-note-${caseId}`).value.trim();
        await requestJSON(`/support-cases/${encodeURIComponent(caseId)}/notes`, {
          method: "POST",
          body: { body, visibility: "internal" },
        });
        await loadDetail(state.selectedConversationId);
        showBanner("Support case note saved.", "success");
      }

      async function resolveCase(caseId) {
        const resolutionType = document.getElementById(`case-resolution-type-${caseId}`).value.trim() || "resolved";
        const summary = document.getElementById(`case-resolution-summary-${caseId}`).value.trim() || "Resolved";
        await requestJSON(`/support-cases/${encodeURIComponent(caseId)}/resolve`, {
          method: "POST",
          body: {
            resolution_type: resolutionType,
            summary,
          },
        });
        await Promise.all([loadDashboard(), loadDetail(state.selectedConversationId)]);
        showBanner("Support case resolved.", "success");
      }

      async function closeCase(caseId) {
        await requestJSON(`/support-cases/${encodeURIComponent(caseId)}/close`, { method: "POST" });
        await Promise.all([loadDashboard(), loadDetail(state.selectedConversationId)]);
        showBanner("Support case closed.", "success");
      }

      async function createExternalCase() {
        if (!state.selectedConversationId) {
          throw new Error("select a conversation first");
        }
        const connectionId = document.getElementById("external-connection-id").value;
        if (!connectionId) {
          throw new Error("choose a connection first");
        }
        const connection = state.connections.find((item) => item.connection_id === connectionId);
        if (!connection) {
          throw new Error("unknown connection");
        }
        await requestJSON("/ticketing/external-cases", {
          method: "POST",
          body: {
            provider: connection.provider,
            connection_id: connectionId,
            support_case_id: document.getElementById("external-support-case-id").value || null,
            conversation_id: state.selectedConversationId,
            title: document.getElementById("external-title").value.trim() || null,
            description: document.getElementById("external-description").value.trim() || null,
          },
        });
        document.getElementById("external-case-create-form").reset();
        renderConnectionSelects();
        await Promise.all([loadConnections(), loadDetail(state.selectedConversationId)]);
        showBanner("External case created.", "success");
      }

      async function searchRemoteCases() {
        const connectionId = document.getElementById("external-connection-id").value;
        if (!connectionId) {
          throw new Error("choose a connection first");
        }
        const query = document.getElementById("external-search-query").value.trim();
        if (!query) {
          throw new Error("enter a remote-case query");
        }
        const limit = document.getElementById("external-search-limit").value;
        const results = await requestJSON(
          `/ticketing/connections/${encodeURIComponent(connectionId)}/remote-search?q=${encodeURIComponent(query)}&limit=${encodeURIComponent(limit)}`
        );
        state.remoteSearchResults = results || [];
        renderRemoteSearchResults(state.remoteSearchResults);
      }

      async function linkExistingRemoteCase(externalCaseId) {
        if (!state.selectedConversationId) {
          throw new Error("select a conversation first");
        }
        const connectionId = document.getElementById("external-connection-id").value;
        if (!connectionId) {
          throw new Error("choose a connection first");
        }
        const connection = state.connections.find((item) => item.connection_id === connectionId);
        const remote = state.remoteSearchResults.find((item) => item.external_case_id === externalCaseId);
        if (!connection || !remote) {
          throw new Error("unknown remote case");
        }
        await requestJSON("/ticketing/external-cases", {
          method: "POST",
          body: {
            provider: connection.provider,
            connection_id: connectionId,
            external_case_id: remote.external_case_id,
            external_case_key: remote.external_case_key,
            external_case_url: remote.external_case_url,
            external_case_status: remote.external_case_status,
            external_case_priority: remote.external_case_priority,
            support_case_id: document.getElementById("external-support-case-id").value || null,
            conversation_id: state.selectedConversationId,
            provider_payload_snapshot: remote.provider_payload_snapshot || {},
          },
        });
        await Promise.all([loadConnections(), loadDetail(state.selectedConversationId)]);
        showBanner("Existing external case linked.", "success");
      }

      async function syncExternalCase(linkId) {
        await requestJSON(`/ticketing/external-cases/${encodeURIComponent(linkId)}/sync`, { method: "POST" });
        await Promise.all([loadConnections(), loadDetail(state.selectedConversationId)]);
        showBanner("External case synced.", "success");
      }

      async function addExternalCaseComment(linkId) {
        const body = document.getElementById(`external-comment-body-${linkId}`).value.trim();
        await requestJSON(`/ticketing/external-cases/${encodeURIComponent(linkId)}/comment`, {
          method: "POST",
          body: { body, visibility: "internal" },
        });
        await Promise.all([loadConnections(), loadDetail(state.selectedConversationId)]);
        showBanner("External case comment sent.", "success");
      }

      async function transitionExternalCase(linkId) {
        const statusValue = document.getElementById(`external-transition-status-${linkId}`).value.trim();
        await requestJSON(`/ticketing/external-cases/${encodeURIComponent(linkId)}/transition`, {
          method: "POST",
          body: { status: statusValue },
        });
        await Promise.all([loadConnections(), loadDetail(state.selectedConversationId)]);
        showBanner("External case transitioned.", "success");
      }

      let reloadHandle = null;
      function scheduleReload() {
        window.clearTimeout(reloadHandle);
        reloadHandle = window.setTimeout(() => {
          loadDashboard().catch((error) => showBanner(error instanceof Error ? error.message : "Failed to load tickets."));
        }, 160);
      }

      controls.search.addEventListener("input", scheduleReload);
      controls.handler.addEventListener("change", scheduleReload);
      controls.channel.addEventListener("change", scheduleReload);
      controls.outcome.addEventListener("change", scheduleReload);
      controls.days.addEventListener("change", scheduleReload);

      document.getElementById("tickets-table-body").addEventListener("click", (event) => {
        const row = event.target.closest("[data-conversation-id]");
        if (!(row instanceof HTMLElement)) return;
        const conversationId = row.getAttribute("data-conversation-id");
        if (!conversationId) return;
        loadDetail(conversationId).catch((error) => showBanner(error instanceof Error ? error.message : "Failed to load detail."));
      });

      document.getElementById("detail-close").addEventListener("click", () => {
        const shell = document.getElementById("tickets-detail");
        shell.classList.add("hidden");
        shell.setAttribute("aria-hidden", "true");
      });

      document.getElementById("support-case-create-form").addEventListener("submit", (event) => {
        event.preventDefault();
        createSupportCaseFromDetail().catch((error) => showBanner(error instanceof Error ? error.message : "Failed to create support case."));
      });

      document.getElementById("external-case-create-form").addEventListener("submit", (event) => {
        event.preventDefault();
        createExternalCase().catch((error) => showBanner(error instanceof Error ? error.message : "Failed to create external case."));
      });

      document.getElementById("external-case-link-form").addEventListener("submit", (event) => {
        event.preventDefault();
        searchRemoteCases().catch((error) => showBanner(error instanceof Error ? error.message : "Failed to search remote cases."));
      });

      document.getElementById("detail-cases").addEventListener("click", (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        const noteCaseId = target.getAttribute("data-note-case");
        if (noteCaseId) {
          document.getElementById(`case-note-form-${noteCaseId}`).classList.toggle("hidden");
          return;
        }
        const resolveCaseId = target.getAttribute("data-resolve-case");
        if (resolveCaseId) {
          document.getElementById(`case-resolve-form-${resolveCaseId}`).classList.toggle("hidden");
          return;
        }
        const submitNoteCaseId = target.getAttribute("data-submit-note");
        if (submitNoteCaseId) {
          addCaseNote(submitNoteCaseId).catch((error) => showBanner(error instanceof Error ? error.message : "Failed to save note."));
          return;
        }
        const submitResolveCaseId = target.getAttribute("data-submit-resolve");
        if (submitResolveCaseId) {
          resolveCase(submitResolveCaseId).catch((error) => showBanner(error instanceof Error ? error.message : "Failed to resolve case."));
          return;
        }
        const closeCaseId = target.getAttribute("data-close-case");
        if (closeCaseId) {
          closeCase(closeCaseId).catch((error) => showBanner(error instanceof Error ? error.message : "Failed to close case."));
        }
      });

      document.getElementById("detail-external").addEventListener("click", (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        const syncLinkId = target.getAttribute("data-sync-link");
        if (syncLinkId) {
          syncExternalCase(syncLinkId).catch((error) => showBanner(error instanceof Error ? error.message : "Failed to sync external case."));
          return;
        }
        const commentLinkId = target.getAttribute("data-comment-link");
        if (commentLinkId) {
          document.getElementById(`external-comment-form-${commentLinkId}`).classList.toggle("hidden");
          return;
        }
        const transitionLinkId = target.getAttribute("data-transition-link");
        if (transitionLinkId) {
          document.getElementById(`external-transition-form-${transitionLinkId}`).classList.toggle("hidden");
          return;
        }
        const submitCommentLinkId = target.getAttribute("data-submit-comment");
        if (submitCommentLinkId) {
          addExternalCaseComment(submitCommentLinkId).catch((error) => showBanner(error instanceof Error ? error.message : "Failed to comment on external case."));
          return;
        }
        const submitTransitionLinkId = target.getAttribute("data-submit-transition");
        if (submitTransitionLinkId) {
          transitionExternalCase(submitTransitionLinkId).catch((error) => showBanner(error instanceof Error ? error.message : "Failed to transition external case."));
        }
      });

      document.getElementById("external-remote-search-results").addEventListener("click", (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        const externalCaseId = target.getAttribute("data-link-remote-case");
        if (externalCaseId) {
          linkExistingRemoteCase(externalCaseId).catch((error) => showBanner(error instanceof Error ? error.message : "Failed to link existing remote case."));
        }
      });

      document.getElementById("connection-list").addEventListener("click", (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        const connectionId = target.getAttribute("data-select-connection");
        if (connectionId) {
          loadConnectionActivity(connectionId).catch((error) => showBanner(error instanceof Error ? error.message : "Failed to load activity."));
          return;
        }
        const healthConnectionId = target.getAttribute("data-health-connection");
        if (healthConnectionId) {
          runConnectionHealth(healthConnectionId).catch((error) => showBanner(error instanceof Error ? error.message : "Failed to run health check."));
        }
      });

      document.getElementById("activity-connection-id").addEventListener("change", (event) => {
        loadConnectionActivity(event.target.value).catch((error) => showBanner(error instanceof Error ? error.message : "Failed to load activity."));
      });

      document.getElementById("activity-refresh").addEventListener("click", () => {
        loadConnectionActivity(document.getElementById("activity-connection-id").value).catch((error) => showBanner(error instanceof Error ? error.message : "Failed to load activity."));
      });

      document.getElementById("activity-health").addEventListener("click", () => {
        const connectionId = document.getElementById("activity-connection-id").value;
        if (!connectionId) {
          showBanner("Choose a connection before running a health check.");
          return;
        }
        runConnectionHealth(connectionId).catch((error) => showBanner(error instanceof Error ? error.message : "Failed to run health check."));
      });

      document.getElementById("activity-process-retries").addEventListener("click", () => {
        processRetryQueue().catch((error) => showBanner(error instanceof Error ? error.message : "Failed to process retry queue."));
      });

      document.getElementById("connection-activity-list").addEventListener("click", (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        const activityId = target.getAttribute("data-retry-activity");
        if (activityId) {
          retryTicketingActivity(activityId).catch((error) => showBanner(error instanceof Error ? error.message : "Failed to retry ticketing activity."));
        }
      });

      document.getElementById("connection-create-form").addEventListener("submit", (event) => {
        event.preventDefault();
        createConnection().catch((error) => showBanner(error instanceof Error ? error.message : "Failed to create connection."));
      });

      document.getElementById("connection-edit-form").addEventListener("submit", (event) => {
        event.preventDefault();
        saveConnectionEdit().catch((error) => showBanner(error instanceof Error ? error.message : "Failed to update connection."));
      });

      document.getElementById("connection-disable").addEventListener("click", () => {
        setConnectionStatus("disabled").catch((error) => showBanner(error instanceof Error ? error.message : "Failed to disable connection."));
      });

      document.getElementById("connection-enable").addEventListener("click", () => {
        setConnectionStatus("active").catch((error) => showBanner(error instanceof Error ? error.message : "Failed to enable connection."));
      });

      Promise.all([
        loadDashboard(),
        loadConnections(),
      ]).catch((error) => {
        showBanner(error instanceof Error ? error.message : "Failed to load tickets.");
      });
    </script>
  </body>
</html>
"""
    )
