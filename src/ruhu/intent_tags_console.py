from __future__ import annotations

from .ui_theme import app_theme_styles


def intent_tags_console_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Ruhu Intent &amp; Tags Console</title>
  <style>
""" + app_theme_styles() + """

    .shell {
      width: min(1600px, calc(100vw - 32px));
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
      display: grid;
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
      max-width: 88ch;
    }

    .toolbar {
      display: grid;
      grid-template-columns: 1.3fr 1fr auto;
      gap: 12px;
      align-items: end;
    }

    .layout {
      display: grid;
      grid-template-columns: 360px minmax(0, 1fr) 420px;
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
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 14px;
    }

    .section-head h2 {
      margin: 0;
    }

    .stack {
      display: grid;
      gap: 12px;
    }

    .subgrid {
      display: grid;
      gap: 12px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
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

    textarea {
      min-height: 92px;
      resize: vertical;
    }

    input:focus, select:focus, textarea:focus {
      outline: none;
      border-color: hsl(var(--ring));
      box-shadow: 0 0 0 4px rgba(var(--primary-rgb), 0.14);
    }

    button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      border: 0;
      border-radius: 999px;
      padding: 11px 16px;
      background: hsl(var(--primary));
      color: hsl(var(--primary-foreground));
      cursor: pointer;
      font-size: var(--text-sm);
      font-weight: 600;
      transition: transform 120ms ease, box-shadow 120ms ease;
    }

    button.secondary {
      background: transparent;
      color: hsl(var(--foreground));
      border: 1px solid hsl(var(--border));
    }

    button.mini {
      padding: 7px 11px;
      font-size: var(--text-xs);
    }

    button:hover:not(:disabled) {
      transform: translateY(-1px);
      box-shadow: 0 8px 24px rgba(var(--primary-rgb), 0.16);
    }

    button:disabled {
      opacity: 0.55;
      cursor: default;
      transform: none;
      box-shadow: none;
    }

    .button-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
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

    .status-banner[data-tone="error"] {
      background: rgba(220, 38, 38, 0.10);
      border-color: rgba(220, 38, 38, 0.18);
      color: rgb(185, 28, 28);
    }

    .list {
      display: grid;
      gap: 8px;
    }

    .item-button {
      width: 100%;
      padding: 12px;
      border-radius: 14px;
      border: 1px solid hsl(var(--border));
      background: hsl(var(--card));
      text-align: left;
      color: hsl(var(--foreground));
      display: grid;
      gap: 4px;
    }

    .item-button.active {
      border-color: rgba(var(--primary-rgb), 0.30);
      background: rgba(var(--primary-rgb), 0.08);
    }

    .item-title {
      font-size: var(--text-sm);
      font-weight: 600;
    }

    .item-subtitle, .muted {
      color: hsl(var(--muted-foreground));
      font-size: var(--text-xs);
      line-height: 1.5;
    }

    .empty {
      border: 1px dashed hsl(var(--border));
      border-radius: 16px;
      padding: 18px;
      font-size: var(--text-sm);
      color: hsl(var(--muted-foreground));
      text-align: center;
    }

    .metric-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }

    .metric {
      border: 1px solid hsl(var(--border));
      border-radius: 14px;
      padding: 12px;
      background: rgba(var(--primary-rgb), 0.04);
    }

    .metric .label {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: hsl(var(--muted-foreground));
      margin-bottom: 4px;
    }

    .metric .value {
      font-size: var(--text-lg);
      font-weight: 700;
    }

    .table {
      width: 100%;
      border-collapse: collapse;
      font-size: var(--text-sm);
    }

    .table th, .table td {
      border-bottom: 1px solid hsl(var(--border));
      padding: 8px 6px;
      text-align: left;
      vertical-align: top;
    }

    .table th {
      font-size: 11px;
      color: hsl(var(--muted-foreground));
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      border: 1px solid hsl(var(--border));
      padding: 4px 8px;
      font-size: 11px;
      color: hsl(var(--muted-foreground));
      background: hsl(var(--secondary));
    }

    .panel {
      border: 1px solid hsl(var(--border));
      border-radius: 16px;
      padding: 14px;
      background: rgba(var(--primary-rgb), 0.04);
    }

    pre {
      margin: 0;
      padding: 12px;
      border-radius: 12px;
      background: hsl(var(--secondary));
      overflow: auto;
      font-size: 12px;
      line-height: 1.5;
      font-family: var(--mono);
    }

    .split {
      display: grid;
      gap: 12px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    @media (max-width: 1260px) {
      .layout {
        grid-template-columns: 1fr;
      }
      .metric-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
      .toolbar {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div>
        <h1>Intent &amp; Tags Console</h1>
        <p>Govern the semantic taxonomy, inspect conversation summaries, resolve review items, and track semantic analytics from classifier events and stable summaries.</p>
      </div>
      <div class="toolbar">
        <label>
          Organization ID
          <input id="organization-id" value="" placeholder="organization id" />
        </label>
        <label>
          Agent ID
          <input id="agent-id" placeholder="optional agent override" />
        </label>
        <div class="button-row">
          <button id="refresh-all">Refresh</button>
        </div>
      </div>
      <div id="status-banner" class="status-banner">Ready.</div>
    </section>

    <section class="layout">
      <div class="stack">
        <section class="card">
          <div class="section-head">
            <h2>Taxonomy</h2>
            <button class="secondary mini" id="refresh-taxonomy">Reload</button>
          </div>
          <div class="stack">
            <div class="panel">
              <div class="section-head"><h2>Versions</h2></div>
              <form id="version-form" class="stack">
                <label>Name <input id="version-name" required /></label>
                <label>Notes <textarea id="version-notes"></textarea></label>
                <div class="button-row"><button type="submit">Create Version</button></div>
              </form>
              <div id="taxonomy-versions" class="list"></div>
            </div>

            <div class="panel">
              <div class="section-head"><h2>Intent Form</h2></div>
              <form id="intent-form" class="stack">
                <input type="hidden" id="intent-id" />
                <div class="subgrid">
                  <label>Name <input id="intent-name" required /></label>
                  <label>Display name <input id="intent-display-name" required /></label>
                </div>
                <div class="subgrid">
                  <label>Category <input id="intent-category" /></label>
                  <label>Taxonomy version <input id="intent-taxonomy-version-id" /></label>
                </div>
                <div class="subgrid">
                  <label>Priority <input id="intent-priority" type="number" min="0" value="0" /></label>
                  <label>Confidence threshold <input id="intent-threshold" type="number" min="0" max="1" step="0.01" value="0.7" /></label>
                </div>
                <label>Description <textarea id="intent-description"></textarea></label>
                <label>Example phrases (one per line)<textarea id="intent-example-phrases"></textarea></label>
                <div class="subgrid">
                  <label>Color <input id="intent-color" /></label>
                  <label>Icon <input id="intent-icon" /></label>
                </div>
                <div class="button-row">
                  <button type="submit">Save Intent</button>
                  <button type="button" class="secondary" id="intent-reset">Clear</button>
                </div>
              </form>
              <div id="intent-list" class="list"></div>
            </div>

            <div class="panel">
              <div class="section-head"><h2>Tag Form</h2></div>
              <form id="tag-form" class="stack">
                <input type="hidden" id="tag-id" />
                <div class="subgrid">
                  <label>Name <input id="tag-name" required /></label>
                  <label>Display name <input id="tag-display-name" required /></label>
                </div>
                <div class="subgrid">
                  <label>Kind
                    <select id="tag-kind">
                      <option value="goal_attribute">goal_attribute</option>
                      <option value="failure_reason">failure_reason</option>
                      <option value="blocker">blocker</option>
                      <option value="priority">priority</option>
                      <option value="risk">risk</option>
                      <option value="outcome_attribute">outcome_attribute</option>
                    </select>
                  </label>
                  <label>Apply scope
                    <select id="tag-apply-scope">
                      <option value="turn">turn</option>
                      <option value="conversation" selected>conversation</option>
                      <option value="both">both</option>
                    </select>
                  </label>
                </div>
                <div class="subgrid">
                  <label>Related intent ID <input id="tag-related-intent-id" /></label>
                  <label>Taxonomy version <input id="tag-taxonomy-version-id" /></label>
                </div>
                <label>Description <textarea id="tag-description"></textarea></label>
                <label>Rule config JSON<textarea id="tag-rule-config">{}</textarea></label>
                <div class="button-row">
                  <button type="submit">Save Tag</button>
                  <button type="button" class="secondary" id="tag-reset">Clear</button>
                </div>
              </form>
              <div id="tag-list" class="list"></div>
            </div>

            <div class="panel">
              <div class="section-head"><h2>Profiles</h2></div>
              <form id="profile-form" class="stack">
                <input type="hidden" id="profile-id" />
                <div class="subgrid">
                  <label>Adapter <input id="profile-adapter" value="ruhu-general" /></label>
                  <label>Taxonomy mode
                    <select id="profile-taxonomy-mode">
                      <option value="live">live</option>
                      <option value="pinned">pinned</option>
                      <option value="cached_live">cached_live</option>
                    </select>
                  </label>
                </div>
                <div class="subgrid">
                  <label>Agent ID <input id="profile-agent-id" /></label>
                  <label>Taxonomy version <input id="profile-taxonomy-version-id" /></label>
                </div>
                <label>Languages (comma separated)<input id="profile-languages" placeholder="en, fr" /></label>
                <div class="button-row">
                  <button type="submit">Save Profile</button>
                  <button type="button" class="secondary" id="profile-reset">Clear</button>
                </div>
              </form>
              <div id="profile-list" class="list"></div>
            </div>
          </div>
        </section>
      </div>

      <div class="stack">
        <section class="card">
          <div class="section-head">
            <h2>Analytics</h2>
            <button class="secondary mini" id="refresh-analytics">Reload</button>
          </div>
          <div id="analytics-metrics" class="metric-grid"></div>
          <div class="split">
            <div class="panel">
              <div class="section-head"><h2>Intents</h2></div>
              <div id="intent-analytics"></div>
            </div>
            <div class="panel">
              <div class="section-head"><h2>Tags</h2></div>
              <div id="tag-analytics"></div>
            </div>
          </div>
          <div class="panel">
            <div class="section-head"><h2>Outcomes</h2></div>
            <div id="outcome-analytics"></div>
          </div>
        </section>

        <section class="card">
          <div class="section-head">
            <h2>Review Queue</h2>
            <button class="secondary mini" id="refresh-reviews">Reload</button>
          </div>
          <div class="split">
            <div id="review-list" class="list"></div>
            <div class="stack">
              <div id="review-detail" class="empty">Select a review item to inspect or resolve it.</div>
              <form id="review-turn-form" class="panel" hidden>
                <div class="section-head"><h2>Turn Correction</h2></div>
                <div class="subgrid">
                  <label>Intent <input id="review-turn-intent-name" /></label>
                  <label>Confidence <input id="review-turn-confidence" type="number" min="0" max="1" step="0.01" /></label>
                </div>
                <div class="subgrid">
                  <label>Language <input id="review-turn-language" /></label>
                  <label>Response language <input id="review-turn-response-language" /></label>
                </div>
                <label>Tool route <input id="review-turn-tool-route" /></label>
                <label>Signals JSON<textarea id="review-turn-signals">{}</textarea></label>
                <label>Slots JSON<textarea id="review-turn-slots">{}</textarea></label>
                <label>Notes<textarea id="review-turn-notes"></textarea></label>
                <div class="button-row">
                  <button type="button" id="review-claim">Claim</button>
                  <button type="button" class="secondary" id="review-confirm">Confirm</button>
                  <button type="button" id="review-correct-turn">Correct</button>
                  <button type="button" class="secondary" id="review-dismiss-turn">Dismiss</button>
                </div>
              </form>
              <form id="review-summary-form" class="panel" hidden>
                <div class="section-head"><h2>Summary Correction</h2></div>
                <div class="subgrid">
                  <label>Primary intent <input id="review-summary-intent" /></label>
                  <label>Resolution status <input id="review-summary-resolution-status" /></label>
                </div>
                <div class="subgrid">
                  <label>Outcome <input id="review-summary-outcome" /></label>
                  <label>Requires human followup
                    <select id="review-summary-followup">
                      <option value="false">false</option>
                      <option value="true">true</option>
                    </select>
                  </label>
                </div>
                <label>Corrected tag definition IDs (comma separated)<input id="review-summary-tags" /></label>
                <label>Notes<textarea id="review-summary-notes"></textarea></label>
                <div class="button-row">
                  <button type="button" id="review-summary-claim">Claim</button>
                  <button type="button" class="secondary" id="review-summary-confirm">Confirm</button>
                  <button type="button" id="review-correct-summary">Correct</button>
                  <button type="button" class="secondary" id="review-dismiss-summary">Dismiss</button>
                </div>
              </form>
            </div>
          </div>
        </section>
      </div>

      <div class="stack">
        <section class="card">
          <div class="section-head">
            <h2>Summaries</h2>
            <button class="secondary mini" id="refresh-summaries">Reload</button>
          </div>
          <div id="summary-list" class="list"></div>
        </section>
        <section class="card">
          <div class="section-head">
            <h2>Summary Detail</h2>
            <button class="secondary mini" id="reload-selected-summary">Reload</button>
          </div>
          <div id="summary-detail" class="empty">Select a summary to inspect the final judgment, evidence, and tag assignments.</div>
        </section>
      </div>
    </section>
  </main>

  <script>
    const state = {
      selectedReview: null,
      selectedSummaryId: null,
      taxonomy: null,
    };

    const el = (id) => document.getElementById(id);

    function organizationId() {
      return el("organization-id").value.trim();
    }

    function agentId() {
      const value = el("agent-id").value.trim();
      return value || null;
    }

    function setStatus(message, tone = "") {
      const banner = el("status-banner");
      banner.textContent = message;
      banner.dataset.tone = tone;
    }

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }

    function parseJson(text, fallback) {
      const trimmed = String(text || "").trim();
      if (!trimmed) {
        return fallback;
      }
      return JSON.parse(trimmed);
    }

    function csvList(text) {
      return String(text || "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        headers: {
          "Content-Type": "application/json",
          ...(options.headers || {}),
        },
        ...options,
      });
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const payload = await response.json();
          detail = payload.detail || JSON.stringify(payload);
        } catch (error) {
          detail = await response.text();
        }
        throw new Error(detail || "request failed");
      }
      if (response.status === 204) {
        return null;
      }
      return response.json();
    }

    function params(extra = {}) {
      const query = new URLSearchParams({ organization_id: organizationId(), ...extra });
      const agent = agentId();
      if (agent) {
        query.set("agent_id", agent);
      }
      for (const [key, value] of Array.from(query.entries())) {
        if (value === "" || value == null) {
          query.delete(key);
        }
      }
      return query.toString();
    }

    function renderList(containerId, items, renderItem, emptyText) {
      const container = el(containerId);
      if (!items.length) {
        container.innerHTML = `<div class="empty">${escapeHtml(emptyText)}</div>`;
        return;
      }
      container.innerHTML = items.map(renderItem).join("");
    }

    function metricCard(label, value) {
      return `<div class="metric"><div class="label">${escapeHtml(label)}</div><div class="value">${escapeHtml(value)}</div></div>`;
    }

    function renderTable(headers, rows) {
      if (!rows.length) {
        return `<div class="empty">No data yet.</div>`;
      }
      return `
        <table class="table">
          <thead><tr>${headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("")}</tr></thead>
          <tbody>
            ${rows.map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join("")}</tr>`).join("")}
          </tbody>
        </table>
      `;
    }

    async function refreshTaxonomy() {
      const snapshot = await api(`/intent-tags/taxonomy?${params()}`);
      state.taxonomy = snapshot;

      renderList(
        "taxonomy-versions",
        snapshot.taxonomy_versions,
        (item) => `
          <button class="item-button" data-action="publish-version" data-id="${item.taxonomy_version_id}">
            <span class="item-title">${escapeHtml(item.name)}</span>
            <span class="item-subtitle">status=${escapeHtml(item.status)}${item.published_at ? ` · published=${escapeHtml(item.published_at)}` : ""}</span>
          </button>
        `,
        "No taxonomy versions yet."
      );

      renderList(
        "intent-list",
        snapshot.intents,
        (item) => `
          <button class="item-button" data-action="edit-intent" data-id="${item.intent_definition_id}">
            <span class="item-title">${escapeHtml(item.display_name)}</span>
            <span class="item-subtitle">${escapeHtml(item.name)} · threshold=${escapeHtml(item.confidence_threshold)} · priority=${escapeHtml(item.priority)}</span>
          </button>
        `,
        "No intent definitions yet."
      );

      renderList(
        "tag-list",
        snapshot.tags,
        (item) => `
          <button class="item-button" data-action="edit-tag" data-id="${item.tag_definition_id}">
            <span class="item-title">${escapeHtml(item.display_name)}</span>
            <span class="item-subtitle">${escapeHtml(item.name)} · ${escapeHtml(item.tag_kind)} · scope=${escapeHtml(item.apply_scope)}</span>
          </button>
        `,
        "No tag definitions yet."
      );

      renderList(
        "profile-list",
        snapshot.profiles,
        (item) => `
          <button class="item-button" data-action="edit-profile" data-id="${item.classifier_profile_id}">
            <span class="item-title">${escapeHtml(item.adapter_name)}</span>
            <span class="item-subtitle">${escapeHtml(item.taxonomy_mode)}${item.agent_id ? ` · agent=${escapeHtml(item.agent_id)}` : " · org default"}${item.catalog_cache_built_at ? ` · cache=${escapeHtml(item.catalog_cache_built_at)}` : ""}</span>
          </button>
          <div class="button-row" style="margin-top: 6px;">
            <button class="secondary mini" data-action="rebuild-profile" data-id="${item.classifier_profile_id}">Rebuild Cache</button>
          </div>
        `,
        "No classifier profiles yet."
      );
    }

    async function refreshAnalytics() {
      const analytics = await api(`/intent-tags/analytics?${params()}`);
      el("analytics-metrics").innerHTML = [
        metricCard("Intent defs", analytics.totals.intent_definitions || 0),
        metricCard("Tag defs", analytics.totals.tag_definitions || 0),
        metricCard("Turn events", analytics.totals.turn_events || 0),
        metricCard("Summaries", analytics.totals.conversation_summaries || 0),
        metricCard("Reviews", analytics.totals.review_items || 0),
        metricCard("Assignments", analytics.totals.tag_assignments || 0),
        metricCard("Profiles", analytics.totals.classifier_profiles || 0),
        metricCard("Versions", analytics.totals.taxonomy_versions || 0),
      ].join("");

      el("intent-analytics").innerHTML = renderTable(
        ["Intent", "Summaries", "Turns", "Corrected", "Low conf", "Reviews"],
        analytics.intent_rows.map((row) => [
          escapeHtml(row.display_name),
          escapeHtml(row.summary_count),
          escapeHtml(row.turn_event_count),
          escapeHtml(row.corrected_turn_count),
          escapeHtml(row.low_confidence_turn_count),
          escapeHtml(row.review_count),
        ]),
      );

      el("tag-analytics").innerHTML = renderTable(
        ["Tag", "Kind", "Assignments", "Validated", "Sources"],
        analytics.tag_rows.map((row) => [
          escapeHtml(row.display_name),
          escapeHtml(row.tag_kind),
          escapeHtml(row.assignment_count),
          escapeHtml(row.validated_count),
          `<code>${escapeHtml(JSON.stringify(row.assignment_source_counts))}</code>`,
        ]),
      );

      el("outcome-analytics").innerHTML = renderTable(
        ["Channel", "Outcome", "Resolution", "Count"],
        analytics.outcome_rows.map((row) => [
          escapeHtml(row.channel),
          escapeHtml(row.outcome || "unknown"),
          escapeHtml(row.resolution_status || "unknown"),
          escapeHtml(row.count),
        ]),
      );
    }

    async function refreshReviews() {
      const reviews = await api(`/intent-tags/reviews?${params({ limit: "200" })}`);
      renderList(
        "review-list",
        reviews,
        (item) => `
          <button class="item-button ${state.selectedReview && state.selectedReview.review_item.review_item_id === item.review_item.review_item_id ? "active" : ""}" data-action="select-review" data-id="${item.review_item.review_item_id}">
            <span class="item-title">${escapeHtml(item.target_kind)} · ${escapeHtml(item.effective_intent_name || item.summary_primary_intent_name || "unknown")}</span>
            <span class="item-subtitle">${escapeHtml(item.review_item.status)} · ${escapeHtml(item.review_item.review_kind)} · conversation=${escapeHtml(item.conversation_id || "unknown")}</span>
          </button>
        `,
        "No review items queued."
      );
      if (state.selectedReview) {
        const refreshed = reviews.find((item) => item.review_item.review_item_id === state.selectedReview.review_item.review_item_id);
        if (refreshed) {
          state.selectedReview = refreshed;
        }
      }
      renderSelectedReview();
    }

    async function refreshSummaries() {
      const summaries = await api(`/intent-tags/summaries?${params({ limit: "100" })}`);
      renderList(
        "summary-list",
        summaries,
        (item) => `
          <button class="item-button ${state.selectedSummaryId === item.summary.conversation_summary_id ? "active" : ""}" data-action="select-summary" data-id="${item.summary.conversation_summary_id}">
            <span class="item-title">${escapeHtml(item.effective_summary.primary_intent_name || "unknown_intent")}</span>
            <span class="item-subtitle">${escapeHtml(item.effective_summary.channel)} · ${escapeHtml(item.effective_summary.resolution_status || "unknown")} · tags=${escapeHtml(item.tag_names.join(", ") || "none")}</span>
          </button>
        `,
        "No summaries yet."
      );
      if (state.selectedSummaryId) {
        await loadSummaryDetail(state.selectedSummaryId);
      }
    }

    function fillIntentForm(item) {
      el("intent-id").value = item.intent_definition_id || "";
      el("intent-name").value = item.name || "";
      el("intent-display-name").value = item.display_name || "";
      el("intent-category").value = item.category || "";
      el("intent-taxonomy-version-id").value = item.taxonomy_version_id || "";
      el("intent-priority").value = item.priority ?? 0;
      el("intent-threshold").value = item.confidence_threshold ?? 0.7;
      el("intent-description").value = item.description || "";
      el("intent-example-phrases").value = (item.example_phrases || []).join("\\n");
      el("intent-color").value = item.color || "";
      el("intent-icon").value = item.icon || "";
    }

    function fillTagForm(item) {
      el("tag-id").value = item.tag_definition_id || "";
      el("tag-name").value = item.name || "";
      el("tag-display-name").value = item.display_name || "";
      el("tag-kind").value = item.tag_kind || "blocker";
      el("tag-apply-scope").value = item.apply_scope || "conversation";
      el("tag-related-intent-id").value = item.related_intent_id || "";
      el("tag-taxonomy-version-id").value = item.taxonomy_version_id || "";
      el("tag-description").value = item.description || "";
      el("tag-rule-config").value = JSON.stringify(item.rule_config || {}, null, 2);
    }

    function fillProfileForm(item) {
      el("profile-id").value = item.classifier_profile_id || "";
      el("profile-adapter").value = item.adapter_name || "ruhu-general";
      el("profile-taxonomy-mode").value = item.taxonomy_mode || "live";
      el("profile-agent-id").value = item.agent_id || "";
      el("profile-taxonomy-version-id").value = item.taxonomy_version_id || "";
      el("profile-languages").value = (item.supported_languages || []).join(", ");
    }

    function renderSelectedReview() {
      const detail = el("review-detail");
      const turnForm = el("review-turn-form");
      const summaryForm = el("review-summary-form");
      turnForm.hidden = true;
      summaryForm.hidden = true;
      if (!state.selectedReview) {
        detail.className = "empty";
        detail.innerHTML = "Select a review item to inspect or resolve it.";
        return;
      }
      const item = state.selectedReview;
      detail.className = "panel";
      detail.innerHTML = `
        <div class="stack">
          <div><strong>${escapeHtml(item.target_kind)} review</strong></div>
          <div class="muted">conversation=${escapeHtml(item.conversation_id || "unknown")} · status=${escapeHtml(item.review_item.status)} · kind=${escapeHtml(item.review_item.review_kind)}</div>
          <div class="muted">current=${escapeHtml(item.current_intent_name || "unknown")} · effective=${escapeHtml(item.effective_intent_name || item.summary_primary_intent_name || "unknown")}</div>
        </div>
      `;
      if (item.target_kind === "turn") {
        turnForm.hidden = false;
        el("review-turn-intent-name").value = item.effective_intent_name || item.current_intent_name || "";
        el("review-turn-confidence").value = "0.85";
        el("review-turn-language").value = "en";
        el("review-turn-response-language").value = "en";
        el("review-turn-tool-route").value = "";
        el("review-turn-signals").value = "{}";
        el("review-turn-slots").value = "{}";
        el("review-turn-notes").value = item.review_item.review_notes || "";
      } else {
        summaryForm.hidden = false;
        el("review-summary-intent").value = item.effective_intent_name || item.summary_primary_intent_name || "";
        el("review-summary-resolution-status").value = item.resolution_status || "";
        el("review-summary-outcome").value = item.outcome || "";
        el("review-summary-followup").value = "false";
        el("review-summary-tags").value = "";
        el("review-summary-notes").value = item.review_item.review_notes || "";
      }
    }

    async function loadSummaryDetail(summaryId) {
      if (!summaryId) {
        return;
      }
      state.selectedSummaryId = summaryId;
      const detail = await api(`/intent-tags/summaries/${summaryId}?${params()}`);
      const effective = detail.effective_summary.effective_summary;
      const base = detail.effective_summary.summary;
      const review = detail.effective_summary.review_item;
      const tagNames = detail.effective_summary.tag_assignments.map((assignment) => assignment.tag_definition_id);
      el("summary-detail").className = "stack";
      el("summary-detail").innerHTML = `
        <div class="panel">
          <div class="stack">
            <div><strong>Conversation</strong> ${escapeHtml(base.conversation_id)}</div>
            <div class="muted">base=${escapeHtml(base.primary_intent_name || "unknown")} · effective=${escapeHtml(effective.primary_intent_name || "unknown")} · status=${escapeHtml(effective.status)}</div>
            <div class="button-row">
              <span class="pill">channel=${escapeHtml(effective.channel)}</span>
              <span class="pill">resolution=${escapeHtml(effective.resolution_status || "unknown")}</span>
              <span class="pill">outcome=${escapeHtml(effective.outcome || "unknown")}</span>
            </div>
            <div class="muted">tags=${escapeHtml(tagNames.join(", ") || "none")}</div>
          </div>
        </div>
        <div class="panel">
          <div class="section-head"><h2>Summary Review</h2></div>
          <pre>${escapeHtml(JSON.stringify(review || null, null, 2))}</pre>
        </div>
        <div class="panel">
          <div class="section-head"><h2>Summary Payload</h2></div>
          <pre>${escapeHtml(JSON.stringify(effective.summary_payload || {}, null, 2))}</pre>
        </div>
        <div class="panel">
          <div class="section-head"><h2>Evidence</h2></div>
          <pre>${escapeHtml(JSON.stringify(effective.evidence_payload || {}, null, 2))}</pre>
        </div>
        <div class="panel">
          <div class="section-head"><h2>Turn Evidence</h2></div>
          ${detail.turn_evidence.length ? detail.turn_evidence.map((item) => `
            <div class="panel" style="margin-top: 8px;">
              <div><strong>${escapeHtml(item.effective_event.intent_name)}</strong> · confidence=${escapeHtml(item.effective_event.confidence)}</div>
              <div class="muted">base=${escapeHtml(item.event.intent_name)} · corrected=${escapeHtml(item.is_corrected)}</div>
              <pre>${escapeHtml(JSON.stringify(item.effective_event.decision_payload, null, 2))}</pre>
            </div>
          `).join("") : `<div class="empty">No turn evidence linked.</div>`}
        </div>
      `;
    }

    async function refreshAll() {
      setStatus("Refreshing intent-tags console…");
      try {
        await Promise.all([
          refreshTaxonomy(),
          refreshAnalytics(),
          refreshReviews(),
          refreshSummaries(),
        ]);
        setStatus("Intent-tags console updated.", "success");
      } catch (error) {
        setStatus(error.message || String(error), "error");
      }
    }

    el("refresh-all").addEventListener("click", refreshAll);
    el("refresh-taxonomy").addEventListener("click", refreshTaxonomy);
    el("refresh-analytics").addEventListener("click", refreshAnalytics);
    el("refresh-reviews").addEventListener("click", refreshReviews);
    el("refresh-summaries").addEventListener("click", refreshSummaries);
    el("reload-selected-summary").addEventListener("click", () => loadSummaryDetail(state.selectedSummaryId));

    el("version-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await api("/intent-tags/versions", {
          method: "POST",
          body: JSON.stringify({
            organization_id: organizationId(),
            name: el("version-name").value.trim(),
            notes: el("version-notes").value.trim() || null,
          }),
        });
        el("version-form").reset();
        await refreshTaxonomy();
        setStatus("Taxonomy version created.", "success");
      } catch (error) {
        setStatus(error.message || String(error), "error");
      }
    });

    el("intent-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const intentId = el("intent-id").value.trim();
        const payload = {
          organization_id: organizationId(),
          agent_id: agentId(),
          taxonomy_version_id: el("intent-taxonomy-version-id").value.trim() || null,
          name: el("intent-name").value.trim(),
          display_name: el("intent-display-name").value.trim(),
          description: el("intent-description").value.trim() || null,
          category: el("intent-category").value.trim() || null,
          example_phrases: el("intent-example-phrases").value.split("\\n").map((item) => item.trim()).filter(Boolean),
          confidence_threshold: Number(el("intent-threshold").value || 0.7),
          priority: Number(el("intent-priority").value || 0),
          color: el("intent-color").value.trim() || null,
          icon: el("intent-icon").value.trim() || null,
        };
        if (intentId) {
          await api(`/intent-tags/intents/${intentId}?${params()}`, { method: "PUT", body: JSON.stringify(payload) });
        } else {
          await api("/intent-tags/intents", { method: "POST", body: JSON.stringify(payload) });
        }
        el("intent-reset").click();
        await refreshTaxonomy();
        setStatus("Intent saved.", "success");
      } catch (error) {
        setStatus(error.message || String(error), "error");
      }
    });

    el("tag-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const tagId = el("tag-id").value.trim();
        const payload = {
          organization_id: organizationId(),
          agent_id: agentId(),
          taxonomy_version_id: el("tag-taxonomy-version-id").value.trim() || null,
          name: el("tag-name").value.trim(),
          display_name: el("tag-display-name").value.trim(),
          description: el("tag-description").value.trim() || null,
          tag_kind: el("tag-kind").value,
          apply_scope: el("tag-apply-scope").value,
          related_intent_id: el("tag-related-intent-id").value.trim() || null,
          rule_config: parseJson(el("tag-rule-config").value, {}),
        };
        if (tagId) {
          await api(`/intent-tags/tags/${tagId}?${params()}`, { method: "PUT", body: JSON.stringify(payload) });
        } else {
          await api("/intent-tags/tags", { method: "POST", body: JSON.stringify(payload) });
        }
        el("tag-reset").click();
        await refreshTaxonomy();
        setStatus("Tag saved.", "success");
      } catch (error) {
        setStatus(error.message || String(error), "error");
      }
    });

    el("profile-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const profileId = el("profile-id").value.trim();
        const payload = {
          organization_id: organizationId(),
          agent_id: el("profile-agent-id").value.trim() || null,
          adapter_name: el("profile-adapter").value.trim() || "ruhu-general",
          taxonomy_mode: el("profile-taxonomy-mode").value,
          taxonomy_version_id: el("profile-taxonomy-version-id").value.trim() || null,
          supported_languages: csvList(el("profile-languages").value),
        };
        if (profileId) {
          await api(`/intent-tags/profiles/${profileId}?${params()}`, { method: "PUT", body: JSON.stringify(payload) });
        } else {
          await api("/intent-tags/profiles", { method: "POST", body: JSON.stringify(payload) });
        }
        el("profile-reset").click();
        await refreshTaxonomy();
        setStatus("Classifier profile saved.", "success");
      } catch (error) {
        setStatus(error.message || String(error), "error");
      }
    });

    el("intent-reset").addEventListener("click", () => {
      el("intent-form").reset();
      el("intent-id").value = "";
      el("intent-threshold").value = "0.7";
      el("intent-priority").value = "0";
    });

    el("tag-reset").addEventListener("click", () => {
      el("tag-form").reset();
      el("tag-id").value = "";
      el("tag-rule-config").value = "{}";
      el("tag-apply-scope").value = "conversation";
      el("tag-kind").value = "goal_attribute";
    });

    el("profile-reset").addEventListener("click", () => {
      el("profile-form").reset();
      el("profile-id").value = "";
      el("profile-adapter").value = "ruhu-general";
      el("profile-taxonomy-mode").value = "live";
    });

    async function claimSelectedReview() {
      if (!state.selectedReview) {
        return;
      }
      await api(`/intent-tags/reviews/${state.selectedReview.review_item.review_item_id}/claim?${params()}`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      await refreshReviews();
    }

    async function resolveSelectedTurn(disposition) {
      if (!state.selectedReview) {
        return;
      }
      const payload = {
        disposition,
        review_notes: el("review-turn-notes").value.trim() || null,
      };
      if (disposition === "corrected") {
        payload.corrected_decision = {
          intent_name: el("review-turn-intent-name").value.trim(),
          confidence: Number(el("review-turn-confidence").value || 0.85),
          language: el("review-turn-language").value.trim() || "en",
          response_language: el("review-turn-response-language").value.trim() || "en",
          tool_route: el("review-turn-tool-route").value.trim() || null,
          signals: parseJson(el("review-turn-signals").value, {}),
          slots: parseJson(el("review-turn-slots").value, {}),
        };
      }
      await api(`/intent-tags/reviews/${state.selectedReview.review_item.review_item_id}/resolve-turn?${params()}`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      await Promise.all([refreshReviews(), refreshSummaries(), refreshAnalytics()]);
    }

    async function resolveSelectedSummary(disposition) {
      if (!state.selectedReview) {
        return;
      }
      const payload = {
        disposition,
        review_notes: el("review-summary-notes").value.trim() || null,
      };
      if (disposition === "corrected") {
        payload.corrected_fields = {
          primary_intent_name: el("review-summary-intent").value.trim() || null,
          resolution_status: el("review-summary-resolution-status").value.trim() || null,
          outcome: el("review-summary-outcome").value.trim() || null,
          requires_human_followup: el("review-summary-followup").value === "true",
        };
        payload.corrected_tag_definition_ids = csvList(el("review-summary-tags").value);
      }
      await api(`/intent-tags/reviews/${state.selectedReview.review_item.review_item_id}/resolve-summary?${params()}`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      await Promise.all([refreshReviews(), refreshSummaries(), refreshAnalytics()]);
    }

    el("review-claim").addEventListener("click", () => claimSelectedReview().catch((error) => setStatus(error.message || String(error), "error")));
    el("review-summary-claim").addEventListener("click", () => claimSelectedReview().catch((error) => setStatus(error.message || String(error), "error")));
    el("review-confirm").addEventListener("click", () => resolveSelectedTurn("confirmed").catch((error) => setStatus(error.message || String(error), "error")));
    el("review-dismiss-turn").addEventListener("click", () => resolveSelectedTurn("dismissed").catch((error) => setStatus(error.message || String(error), "error")));
    el("review-correct-turn").addEventListener("click", () => resolveSelectedTurn("corrected").catch((error) => setStatus(error.message || String(error), "error")));
    el("review-summary-confirm").addEventListener("click", () => resolveSelectedSummary("confirmed").catch((error) => setStatus(error.message || String(error), "error")));
    el("review-dismiss-summary").addEventListener("click", () => resolveSelectedSummary("dismissed").catch((error) => setStatus(error.message || String(error), "error")));
    el("review-correct-summary").addEventListener("click", () => resolveSelectedSummary("corrected").catch((error) => setStatus(error.message || String(error), "error")));

    document.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-action]");
      if (!button) {
        return;
      }
      const { action, id } = button.dataset;
      try {
        if (action === "publish-version") {
          await api(`/intent-tags/versions/${id}/publish?${params()}`, { method: "POST" });
          await refreshTaxonomy();
          setStatus("Taxonomy version published.", "success");
        } else if (action === "edit-intent") {
          const item = state.taxonomy.intents.find((candidate) => candidate.intent_definition_id === id);
          if (item) fillIntentForm(item);
        } else if (action === "edit-tag") {
          const item = state.taxonomy.tags.find((candidate) => candidate.tag_definition_id === id);
          if (item) fillTagForm(item);
        } else if (action === "edit-profile") {
          const item = state.taxonomy.profiles.find((candidate) => candidate.classifier_profile_id === id);
          if (item) fillProfileForm(item);
        } else if (action === "rebuild-profile") {
          await api(`/intent-tags/profiles/${id}/rebuild`, {
            method: "POST",
            body: JSON.stringify({ organization_id: organizationId(), agent_id: agentId() }),
          });
          await refreshTaxonomy();
          setStatus("Profile cache rebuilt.", "success");
        } else if (action === "select-review") {
          const reviews = await api(`/intent-tags/reviews?${params({ limit: "200" })}`);
          state.selectedReview = reviews.find((item) => item.review_item.review_item_id === id) || null;
          renderSelectedReview();
        } else if (action === "select-summary") {
          await loadSummaryDetail(id);
        }
      } catch (error) {
        setStatus(error.message || String(error), "error");
      }
    });

    refreshAll();
  </script>
</body>
</html>
"""
