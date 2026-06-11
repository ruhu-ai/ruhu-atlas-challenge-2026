from __future__ import annotations

from .ui_theme import app_theme_styles


def kpi_console_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Ruhu KPI Console</title>
  <style>
""" + app_theme_styles() + """

    .shell {
      width: min(1560px, calc(100vw - 32px));
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
      gap: 16px;
      align-items: end;
    }

    .hero h1 {
      margin: 0 0 8px;
      font-size: clamp(var(--text-2xl), 3vw, 2.8rem);
      line-height: 1;
      letter-spacing: -0.04em;
    }

    .hero p {
      margin: 0;
      color: hsl(var(--muted-foreground));
      max-width: 76ch;
      font-size: var(--text-sm);
      line-height: 1.6;
    }

    .hero .links {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }

    .layout {
      display: grid;
      grid-template-columns: 320px minmax(0, 1fr) 360px;
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

    input:focus, select:focus, textarea:focus {
      outline: none;
      border-color: hsl(var(--ring));
      box-shadow: 0 0 0 4px rgba(var(--primary-rgb), 0.14);
    }

    button, a.button-link {
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
      text-decoration: none;
      transition: transform 120ms ease, box-shadow 120ms ease, background 120ms ease;
    }

    button.secondary, a.button-link.secondary {
      background: transparent;
      color: hsl(var(--foreground));
      border: 1px solid hsl(var(--border));
    }

    button.ghost {
      background: transparent;
      color: hsl(var(--muted-foreground));
      border: 1px dashed hsl(var(--border));
    }

    button.mini {
      padding: 7px 11px;
      font-size: var(--text-xs);
      font-weight: 600;
    }

    button:hover:not(:disabled), a.button-link:hover {
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

    .kv-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
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

    .metric .hint {
      margin-top: 4px;
      font-size: var(--text-xs);
      color: hsl(var(--muted-foreground));
    }

    .detail-block {
      border: 1px solid hsl(var(--border));
      border-radius: 16px;
      padding: 14px;
      display: grid;
      gap: 10px;
    }

    .detail-row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
    }

    .detail-row strong {
      font-size: var(--text-sm);
    }

    .pill-row {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }

    .pill {
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      background: rgba(var(--primary-rgb), 0.10);
      color: hsl(var(--primary));
    }

    .code {
      font-family: var(--mono);
      font-size: var(--text-xs);
      white-space: pre-wrap;
      word-break: break-word;
      border: 1px solid hsl(var(--border));
      border-radius: 12px;
      padding: 10px;
      background: hsl(var(--secondary));
    }

    @media (max-width: 1120px) {
      .layout {
        grid-template-columns: 1fr;
      }
      .subgrid, .kv-grid {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div>
        <h1>KPI Goals Console</h1>
        <p>Measure outcome KPIs from canonical runtime data, review the current gap, and generate evidence-backed insights and recommendations without coupling KPI logic to execution adapters.</p>
      </div>
      <div class="links">
        <a class="button-link secondary" href="/playground">Playground</a>
      </div>
    </section>

    <div id="status" class="status-banner">Ready.</div>

    <section class="layout">
      <aside class="stack">
        <div class="card">
          <div class="section-head">
            <h2>Workspace</h2>
            <button id="refresh-all" class="secondary">Refresh</button>
          </div>
          <div class="stack">
            <label>
              Organization
              <input id="organization-id" value="" placeholder="organization id" />
            </label>
          </div>
        </div>

        <div class="card">
          <div class="section-head">
            <h2>Goals</h2>
            <span id="goal-count" class="muted">0 goals</span>
          </div>
          <div id="goal-list" class="list"></div>
        </div>

        <div class="card">
          <div class="section-head">
            <h2>Scopes</h2>
            <span id="scope-count" class="muted">0 scopes</span>
          </div>
          <div id="scope-list" class="list"></div>
        </div>
      </aside>

      <main class="stack">
        <div class="card">
          <div class="section-head">
            <h2>Goal Detail</h2>
            <div class="button-row">
              <button id="refresh-metric" class="secondary">Refresh metric</button>
              <button id="evaluate-goal" class="secondary">Evaluate</button>
              <button id="generate-insights" class="secondary">Generate insights</button>
              <button id="generate-recommendations" class="secondary">Generate recs</button>
            </div>
          </div>
          <div id="goal-detail" class="empty">Select a goal to inspect its baseline, current measurement, insights, recommendations, and impact history.</div>
        </div>
      </main>

      <aside class="stack">
        <div class="card">
          <div class="section-head">
            <h2>Create Scope</h2>
          </div>
          <div class="stack">
            <label>
              Scope kind
              <select id="scope-kind">
                <option value="organization">organization</option>
                <option value="channel" selected>channel</option>
                <option value="workflow">workflow</option>
                <option value="agent">agent</option>
                <option value="segment">segment</option>
                <option value="campaign">campaign</option>
                <option value="custom">custom</option>
              </select>
            </label>
            <label>
              Display name
              <input id="scope-display-name" placeholder="Website widget" />
            </label>
            <label>
              Channel
              <select id="scope-channel">
                <option value="">none</option>
                <option value="phone">phone</option>
                <option value="whatsapp">whatsapp</option>
                <option value="web_chat">web_chat</option>
                <option value="web_widget" selected>web_widget</option>
                <option value="browser">browser</option>
              </select>
            </label>
            <label>
              Workflow id
              <input id="scope-workflow-id" placeholder="projected-workflow-id" />
            </label>
            <label>
              Agent id
              <input id="scope-agent-id" placeholder="agent-123" />
            </label>
            <button id="create-scope">Create scope</button>
          </div>
        </div>

        <div class="card">
          <div class="section-head">
            <h2>Manual Observation</h2>
          </div>
          <div class="stack">
            <label>
              Metric
              <select id="observation-metric"></select>
            </label>
            <label>
              Scope
              <select id="observation-scope"></select>
            </label>
            <div class="subgrid">
              <label>
                Value
                <input id="observation-value" type="number" step="0.01" value="55" />
              </label>
              <label>
                Sample size
                <input id="observation-sample-size" type="number" step="1" value="25" />
              </label>
            </div>
            <div class="subgrid">
              <label>
                Confidence
                <input id="observation-confidence" type="number" step="0.01" min="0" max="1" value="0.8" />
              </label>
              <label>
                Lookback days
                <input id="observation-lookback-days" type="number" step="1" min="1" value="30" />
              </label>
            </div>
            <button id="create-observation">Record observation</button>
          </div>
        </div>

        <div class="card">
          <div class="section-head">
            <h2>Create Goal</h2>
          </div>
          <div class="stack">
            <label>
              Name
              <input id="goal-name" placeholder="Reduce transfer rate" />
            </label>
            <label>
              Metric
              <select id="goal-metric"></select>
            </label>
            <label>
              Scope
              <select id="goal-scope"></select>
            </label>
            <div class="subgrid">
              <label>
                Target value
                <input id="goal-target-value" type="number" step="0.01" value="25" />
              </label>
              <label>
                Target in days
                <input id="goal-target-days" type="number" step="1" min="1" value="30" />
              </label>
            </div>
            <button id="create-goal">Create goal</button>
          </div>
        </div>
      </aside>
    </section>
  </div>

  <script>
    const state = {
      definitions: [],
      scopes: [],
      goals: [],
      selectedGoalId: null,
    };

    const elements = {
      status: document.getElementById("status"),
      organizationId: document.getElementById("organization-id"),
      goalList: document.getElementById("goal-list"),
      goalCount: document.getElementById("goal-count"),
      scopeList: document.getElementById("scope-list"),
      scopeCount: document.getElementById("scope-count"),
      goalDetail: document.getElementById("goal-detail"),
      observationMetric: document.getElementById("observation-metric"),
      observationScope: document.getElementById("observation-scope"),
      goalMetric: document.getElementById("goal-metric"),
      goalScope: document.getElementById("goal-scope"),
    };

    function toneStatus(message, tone = "info") {
      elements.status.textContent = message;
      elements.status.dataset.tone = tone;
    }

    function currentOrg() {
      return elements.organizationId.value.trim();
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
          if (payload && payload.detail) {
            detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail);
          }
        } catch (error) {
          // Ignore JSON parse failures for non-JSON errors.
        }
        throw new Error(detail);
      }
      if (response.status === 204) {
        return null;
      }
      return response.json();
    }

    function setSelectOptions(select, items, getValue, getLabel) {
      const previous = select.value;
      select.innerHTML = "";
      for (const item of items) {
        const option = document.createElement("option");
        option.value = getValue(item);
        option.textContent = getLabel(item);
        select.appendChild(option);
      }
      if (items.length === 0) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "No options available";
        select.appendChild(option);
      }
      if (previous && Array.from(select.options).some((option) => option.value === previous)) {
        select.value = previous;
      }
    }

    function renderGoalList() {
      elements.goalCount.textContent = `${state.goals.length} goal${state.goals.length === 1 ? "" : "s"}`;
      if (!state.goals.length) {
        elements.goalList.innerHTML = '<div class="empty">No goals yet.</div>';
        return;
      }
      elements.goalList.innerHTML = "";
      for (const goal of state.goals) {
        const button = document.createElement("button");
        button.className = "item-button";
        if (goal.goal_id === state.selectedGoalId) {
          button.classList.add("active");
        }
        button.innerHTML = `
          <div class="item-title">${goal.name}</div>
          <div class="item-subtitle">${goal.metric_key} · ${goal.status}</div>
          <div class="item-subtitle">baseline ${formatValue(goal.baseline_value)} → target ${formatValue(goal.target_value)}</div>
        `;
        button.addEventListener("click", () => {
          state.selectedGoalId = goal.goal_id;
          renderGoalList();
          loadGoalDetail(goal.goal_id);
        });
        elements.goalList.appendChild(button);
      }
    }

    function renderScopeList() {
      elements.scopeCount.textContent = `${state.scopes.length} scope${state.scopes.length === 1 ? "" : "s"}`;
      if (!state.scopes.length) {
        elements.scopeList.innerHTML = '<div class="empty">No scopes yet.</div>';
        return;
      }
      elements.scopeList.innerHTML = "";
      for (const scope of state.scopes) {
        const card = document.createElement("div");
        card.className = "detail-block";
        card.innerHTML = `
          <div class="detail-row">
            <strong>${scope.display_name || scope.scope_kind}</strong>
            <span class="muted">${scope.scope_kind}</span>
          </div>
          <div class="muted">${scope.channel || scope.workflow_id || scope.agent_id || scope.scope_id}</div>
          <div class="code">${scope.scope_id}</div>
        `;
        elements.scopeList.appendChild(card);
      }
    }

    function formatValue(value) {
      if (value === null || value === undefined) {
        return "—";
      }
      if (Math.abs(value) >= 100) {
        return Number(value).toFixed(1);
      }
      return Number(value).toFixed(2);
    }

    function formatTimestamp(value) {
      if (!value) {
        return "—";
      }
      return new Date(value).toLocaleString();
    }

    function metricLabel(metricKey) {
      const definition = state.definitions.find((item) => item.metric_key === metricKey);
      return definition ? definition.label : metricKey;
    }

    function appendJsonDetails(parent, label, payload) {
      if (payload === null || payload === undefined) {
        return;
      }
      if (Array.isArray(payload) && payload.length === 0) {
        return;
      }
      if (!Array.isArray(payload) && typeof payload === "object" && Object.keys(payload).length === 0) {
        return;
      }
      const details = document.createElement("details");
      details.className = "detail-block";
      const summary = document.createElement("summary");
      summary.innerHTML = `<strong>${label}</strong>`;
      details.appendChild(summary);
      const code = document.createElement("div");
      code.className = "code";
      code.textContent = JSON.stringify(payload, null, 2);
      details.appendChild(code);
      parent.appendChild(details);
    }

    async function loadDefinitions() {
      state.definitions = await api("/kpi/definitions");
      const options = state.definitions.map((definition) => ({
        value: definition.metric_key,
        label: `${definition.label} (${definition.metric_key})`,
      }));
      setSelectOptions(elements.observationMetric, options, (item) => item.value, (item) => item.label);
      setSelectOptions(elements.goalMetric, options, (item) => item.value, (item) => item.label);
    }

    async function loadScopes() {
      state.scopes = await api(`/kpi/scopes?organization_id=${encodeURIComponent(currentOrg())}`);
      setSelectOptions(
        elements.observationScope,
        state.scopes,
        (scope) => scope.scope_id,
        (scope) => `${scope.display_name || scope.scope_kind} (${scope.scope_id.slice(0, 8)})`,
      );
      setSelectOptions(
        elements.goalScope,
        state.scopes,
        (scope) => scope.scope_id,
        (scope) => `${scope.display_name || scope.scope_kind} (${scope.scope_id.slice(0, 8)})`,
      );
      renderScopeList();
    }

    async function loadGoals() {
      state.goals = await api(`/kpi/goals?organization_id=${encodeURIComponent(currentOrg())}`);
      if (!state.selectedGoalId && state.goals.length) {
        state.selectedGoalId = state.goals[0].goal_id;
      }
      if (state.selectedGoalId && !state.goals.some((goal) => goal.goal_id === state.selectedGoalId)) {
        state.selectedGoalId = state.goals.length ? state.goals[0].goal_id : null;
      }
      renderGoalList();
      if (state.selectedGoalId) {
        await loadGoalDetail(state.selectedGoalId);
      } else {
        elements.goalDetail.innerHTML = '<div class="empty">Select a goal to inspect its baseline, current measurement, insights, recommendations, and impact history.</div>';
      }
    }

    async function refreshAll() {
      toneStatus("Refreshing KPI workspace…");
      try {
        await Promise.all([loadDefinitions(), loadScopes(), loadGoals()]);
        toneStatus("KPI workspace refreshed.", "success");
      } catch (error) {
        toneStatus(error.message || String(error), "error");
      }
    }

    function actionButton(label, onClick, className = "mini secondary") {
      const button = document.createElement("button");
      button.className = className;
      button.textContent = label;
      button.addEventListener("click", onClick);
      return button;
    }

    async function loadGoalDetail(goalId) {
      try {
        const detail = await api(`/kpi/goals/${goalId}?organization_id=${encodeURIComponent(currentOrg())}`);
        const support = await api(`/kpi/scopes/${detail.scope.scope_id}/measurement-support?organization_id=${encodeURIComponent(currentOrg())}`);
        const currentSupport = support.find((item) => item.metric_key === detail.goal.metric_key);
        const container = document.createElement("div");
        container.className = "stack";
        container.innerHTML = `
          <div class="kv-grid">
            <div class="metric">
              <div class="label">Metric</div>
              <div class="value">${metricLabel(detail.goal.metric_key)}</div>
              <div class="hint">${detail.scope.display_name || detail.scope.scope_kind}</div>
            </div>
            <div class="metric">
              <div class="label">Status</div>
              <div class="value">${detail.goal.status}</div>
              <div class="hint">Target by ${formatTimestamp(detail.goal.target_at)}</div>
            </div>
            <div class="metric">
              <div class="label">Baseline</div>
              <div class="value">${formatValue(detail.baseline_snapshot.value)}</div>
              <div class="hint">${detail.baseline_snapshot.baseline_source}</div>
            </div>
            <div class="metric">
              <div class="label">Current</div>
              <div class="value">${formatValue(detail.latest_observation && detail.latest_observation.value)}</div>
              <div class="hint">${detail.latest_observation ? formatTimestamp(detail.latest_observation.period_end) : "No current observation"}</div>
            </div>
            <div class="metric">
              <div class="label">Progress</div>
              <div class="value">${formatValue(detail.latest_evaluation && detail.latest_evaluation.progress_ratio)}</div>
              <div class="hint">${detail.latest_evaluation ? detail.latest_evaluation.status : "No evaluation yet"}</div>
            </div>
            <div class="metric">
              <div class="label">Measurement support</div>
              <div class="value">${currentSupport && currentSupport.supported ? "Supported" : "Blocked"}</div>
              <div class="hint">${currentSupport && currentSupport.reason ? currentSupport.reason : "Canonical runtime data is available."}</div>
            </div>
          </div>
        `;

        const supportBlock = document.createElement("div");
        supportBlock.className = "detail-block";
        supportBlock.innerHTML = `<div class="detail-row"><strong>Measurement support matrix</strong><span class="muted">${support.filter((item) => item.supported).length}/${support.length} measurable now</span></div>`;
        for (const item of support) {
          const row = document.createElement("div");
          row.className = "detail-row";
          row.innerHTML = `
            <div>
              <strong>${metricLabel(item.metric_key)}</strong>
              <div class="muted">${item.metric_key}</div>
            </div>
            <div class="muted">${item.supported ? "supported" : "blocked"}</div>
          `;
          if (item.reason) {
            const reason = document.createElement("div");
            reason.className = "muted";
            reason.textContent = item.reason;
            row.appendChild(reason);
          }
          supportBlock.appendChild(row);
        }
        container.appendChild(supportBlock);

        const insightsBlock = document.createElement("div");
        insightsBlock.className = "detail-block";
        const insightsHead = document.createElement("div");
        insightsHead.className = "detail-row";
        insightsHead.innerHTML = `<strong>Insights</strong><span class="muted">${detail.insights.length} items</span>`;
        insightsBlock.appendChild(insightsHead);
        if (!detail.insights.length) {
          insightsBlock.appendChild(document.createRange().createContextualFragment('<div class="empty">No insights yet.</div>'));
        }
        for (const insight of detail.insights) {
          const item = document.createElement("div");
          item.className = "detail-block";
          item.innerHTML = `
            <div class="detail-row">
              <strong>${insight.title}</strong>
              <span class="muted">${insight.status}</span>
            </div>
            <div>${insight.summary}</div>
            <div class="pill-row">
              <span class="pill">${insight.blocker_kind}</span>
              <span class="pill">rank ${formatValue(insight.rank_score)}</span>
              <span class="pill">count ${insight.occurrence_count}</span>
            </div>
          `;
          appendJsonDetails(item, "Evidence bundle", insight.evidence_bundle);
          const actions = document.createElement("div");
          actions.className = "button-row";
          actions.appendChild(actionButton("Accept", () => updateInsightStatus(insight.insight_id, "accepted")));
          actions.appendChild(actionButton("Dismiss", () => updateInsightStatus(insight.insight_id, "dismissed")));
          item.appendChild(actions);
          insightsBlock.appendChild(item);
        }
        container.appendChild(insightsBlock);

        const recommendationsBlock = document.createElement("div");
        recommendationsBlock.className = "detail-block";
        const recommendationsHead = document.createElement("div");
        recommendationsHead.className = "detail-row";
        recommendationsHead.innerHTML = `<strong>Recommendations</strong><span class="muted">${detail.recommendations.length} items</span>`;
        recommendationsBlock.appendChild(recommendationsHead);
        if (!detail.recommendations.length) {
          recommendationsBlock.appendChild(document.createRange().createContextualFragment('<div class="empty">No recommendations yet.</div>'));
        }
        for (const recommendation of detail.recommendations) {
          const item = document.createElement("div");
          item.className = "detail-block";
          item.innerHTML = `
            <div class="detail-row">
              <strong>${recommendation.title}</strong>
              <span class="muted">${recommendation.status}</span>
            </div>
            <div>${recommendation.summary}</div>
            <div class="pill-row">
              <span class="pill">${recommendation.category}</span>
              <span class="pill">${formatValue(recommendation.projected_impact_min)} to ${formatValue(recommendation.projected_impact_max)}</span>
              <span class="pill">confidence ${formatValue(recommendation.projected_confidence)}</span>
            </div>
          `;
          appendJsonDetails(item, "Evidence bundle", recommendation.evidence_bundle);
          appendJsonDetails(item, "Execution template", recommendation.execution_template || {});
          const actions = document.createElement("div");
          actions.className = "button-row";
          actions.appendChild(actionButton("Approve", () => updateRecommendationStatus(recommendation.recommendation_id, "approved")));
          actions.appendChild(actionButton("Reject", () => updateRecommendationStatus(recommendation.recommendation_id, "rejected")));
          actions.appendChild(actionButton("Request execution", () => updateRecommendationStatus(recommendation.recommendation_id, "execution_requested")));
          actions.appendChild(actionButton("Preview", () => previewRecommendationExecution(recommendation.recommendation_id)));
          if (["approved", "execution_requested", "execution_failed"].includes(recommendation.status)) {
            actions.appendChild(actionButton("Apply", () => applyRecommendationExecution(recommendation.recommendation_id)));
          }
          item.appendChild(actions);
          recommendationsBlock.appendChild(item);
        }
        container.appendChild(recommendationsBlock);

        const executionBlock = document.createElement("div");
        executionBlock.className = "detail-block";
        executionBlock.innerHTML = `<div class="detail-row"><strong>Execution</strong><span class="muted">${detail.execution_intents.length} intents · ${detail.execution_results.length} results</span></div>`;
        if (!detail.execution_intents.length) {
          executionBlock.appendChild(document.createRange().createContextualFragment('<div class="empty">No execution requests yet.</div>'));
        } else {
          const latestResultByIntent = new Map();
          for (const result of detail.execution_results) {
            latestResultByIntent.set(result.execution_intent_id, result);
          }
          for (const intent of detail.execution_intents) {
            const latestResult = latestResultByIntent.get(intent.execution_intent_id);
            const item = document.createElement("div");
            item.className = "detail-block";
            item.innerHTML = `
              <div class="detail-row">
                <strong>${intent.action_type}</strong>
                <span class="muted">${intent.execution_mode}</span>
              </div>
              <div class="pill-row">
                <span class="pill">${intent.adapter_kind}</span>
                <span class="pill">${intent.safety_level}</span>
                <span class="pill">${intent.reversibility}</span>
                <span class="pill">${latestResult ? latestResult.status : "pending_result"}</span>
              </div>
              <div class="muted">Requested via ${intent.requested_via} · ${formatTimestamp(intent.created_at)}</div>
            `;
            appendJsonDetails(item, "Approved payload", intent.approved_payload || {});
            appendJsonDetails(item, "Validation snapshot", intent.validation_snapshot || {});
            if (latestResult) {
              appendJsonDetails(item, "Execution diagnostics", latestResult.adapter_diagnostics || {});
              appendJsonDetails(item, "Changed objects", latestResult.changed_object_refs || []);
              appendJsonDetails(item, "Before state", latestResult.before_state_summary || {});
              appendJsonDetails(item, "After state", latestResult.after_state_summary || {});
              appendJsonDetails(item, "Rollback handle", latestResult.rollback_handle || {});
              if (latestResult.error_message) {
                const message = document.createElement("div");
                message.className = "muted";
                message.textContent = `${latestResult.error_code || "execution_error"}: ${latestResult.error_message}`;
                item.appendChild(message);
              }
            }
            executionBlock.appendChild(item);
          }
        }
        container.appendChild(executionBlock);

        const experimentsBlock = document.createElement("div");
        experimentsBlock.className = "detail-block";
        experimentsBlock.innerHTML = `<div class="detail-row"><strong>Experiments</strong><span class="muted">${detail.experiments.length} items</span></div>`;
        if (!detail.experiments.length) {
          experimentsBlock.appendChild(document.createRange().createContextualFragment('<div class="empty">No experiments yet.</div>'));
        } else {
          for (const experiment of detail.experiments) {
            const item = document.createElement("div");
            item.className = "detail-block";
            item.innerHTML = `
              <div class="detail-row">
                <strong>${experiment.name}</strong>
                <span class="muted">${experiment.status}</span>
              </div>
              <div>${experiment.hypothesis}</div>
              <div class="pill-row">
                <span class="pill">${metricLabel(experiment.primary_metric_key)}</span>
                <span class="pill">${detail.scope.display_name || detail.scope.scope_kind}</span>
              </div>
              <div class="muted">Started ${formatTimestamp(experiment.started_at)} · Ended ${formatTimestamp(experiment.ended_at)}</div>
            `;
            if (experiment.notes) {
              const notes = document.createElement("div");
              notes.className = "muted";
              notes.textContent = experiment.notes;
              item.appendChild(notes);
            }
            experimentsBlock.appendChild(item);
          }
        }
        container.appendChild(experimentsBlock);

        const impactsBlock = document.createElement("div");
        impactsBlock.className = "detail-block";
        impactsBlock.innerHTML = `<div class="detail-row"><strong>Impact Assessments</strong><span class="muted">${detail.impact_assessments.length} items</span></div>`;
        if (!detail.impact_assessments.length) {
          impactsBlock.appendChild(document.createRange().createContextualFragment('<div class="empty">No impact assessments yet.</div>'));
        } else {
          for (const assessment of detail.impact_assessments) {
            const item = document.createElement("div");
            item.className = "detail-block";
            item.innerHTML = `
              <div class="detail-row">
                <strong>${assessment.attribution_mode}</strong>
                <span class="muted">${assessment.attribution_confidence}</span>
              </div>
              <div>Observed change: ${formatValue(assessment.observed_change)} · Attributed: ${formatValue(assessment.attributed_change)}</div>
              <div class="muted">Projected ${formatValue(assessment.projected_impact_min)} to ${formatValue(assessment.projected_impact_max)} · attainment ${formatValue(assessment.attainment_fraction)}</div>
              <div class="muted">${assessment.notes || "No notes."}</div>
            `;
            appendJsonDetails(item, "Competing changes", assessment.competing_changes || []);
            impactsBlock.appendChild(item);
          }
        }
        container.appendChild(impactsBlock);

        elements.goalDetail.innerHTML = "";
        elements.goalDetail.appendChild(container);
      } catch (error) {
        elements.goalDetail.innerHTML = `<div class="empty">${error.message || String(error)}</div>`;
      }
    }

    async function createScope() {
      toneStatus("Creating scope…");
      try {
        await api("/kpi/scopes", {
          method: "POST",
          body: JSON.stringify({
            organization_id: currentOrg(),
            scope_kind: document.getElementById("scope-kind").value,
            display_name: document.getElementById("scope-display-name").value.trim() || null,
            channel: document.getElementById("scope-channel").value || null,
            workflow_id: document.getElementById("scope-workflow-id").value.trim() || null,
            agent_id: document.getElementById("scope-agent-id").value.trim() || null,
          }),
        });
        await loadScopes();
        toneStatus("Scope created.", "success");
      } catch (error) {
        toneStatus(error.message || String(error), "error");
      }
    }

    async function createObservation() {
      toneStatus("Recording observation…");
      const lookbackDays = Number(document.getElementById("observation-lookback-days").value || 30);
      const periodEnd = new Date();
      const periodStart = new Date(periodEnd.getTime() - lookbackDays * 24 * 60 * 60 * 1000);
      try {
        await api("/kpi/observations", {
          method: "POST",
          body: JSON.stringify({
            organization_id: currentOrg(),
            metric_key: elements.observationMetric.value,
            scope_id: elements.observationScope.value,
            value: Number(document.getElementById("observation-value").value),
            sample_size: Number(document.getElementById("observation-sample-size").value),
            confidence: Number(document.getElementById("observation-confidence").value),
            period_start: periodStart.toISOString(),
            period_end: periodEnd.toISOString(),
            observation_kind: "manual_entry",
            lookback_days: lookbackDays,
            source_summary: {
              sources: ["manual_entry"],
              recorded_via: "kpi_console",
            },
          }),
        });
        await loadGoals();
        toneStatus("Observation recorded.", "success");
      } catch (error) {
        toneStatus(error.message || String(error), "error");
      }
    }

    async function createGoal() {
      toneStatus("Creating goal…");
      const targetDays = Number(document.getElementById("goal-target-days").value || 30);
      const targetAt = new Date(Date.now() + targetDays * 24 * 60 * 60 * 1000);
      try {
        const goal = await api("/kpi/goals", {
          method: "POST",
          body: JSON.stringify({
            organization_id: currentOrg(),
            metric_key: elements.goalMetric.value,
            scope_id: elements.goalScope.value,
            name: document.getElementById("goal-name").value.trim(),
            target_value: Number(document.getElementById("goal-target-value").value),
            target_at: targetAt.toISOString(),
          }),
        });
        state.selectedGoalId = goal.goal_id;
        await loadGoals();
        toneStatus("Goal created.", "success");
      } catch (error) {
        toneStatus(error.message || String(error), "error");
      }
    }

    async function refreshMetric() {
      if (!state.selectedGoalId) {
        toneStatus("Select a goal first.", "error");
        return;
      }
      const goal = state.goals.find((item) => item.goal_id === state.selectedGoalId);
      if (!goal) {
        toneStatus("Selected goal is no longer available.", "error");
        return;
      }
      toneStatus("Refreshing measured observation…");
      try {
        await api(`/kpi/scopes/${goal.scope_id}/measurements/${goal.metric_key}/refresh`, {
          method: "POST",
          body: JSON.stringify({
            organization_id: currentOrg(),
          }),
        });
        await loadGoals();
        toneStatus("Measured observation refreshed.", "success");
      } catch (error) {
        toneStatus(error.message || String(error), "error");
      }
    }

    async function evaluateGoal() {
      if (!state.selectedGoalId) {
        toneStatus("Select a goal first.", "error");
        return;
      }
      toneStatus("Evaluating goal…");
      try {
        await api(`/kpi/goals/${state.selectedGoalId}/evaluate`, {
          method: "POST",
          body: JSON.stringify({ organization_id: currentOrg() }),
        });
        await loadGoals();
        toneStatus("Goal evaluated.", "success");
      } catch (error) {
        toneStatus(error.message || String(error), "error");
      }
    }

    async function generateInsights() {
      if (!state.selectedGoalId) {
        toneStatus("Select a goal first.", "error");
        return;
      }
      toneStatus("Generating insights…");
      try {
        await api(`/kpi/goals/${state.selectedGoalId}/insights/generate?organization_id=${encodeURIComponent(currentOrg())}`, {
          method: "POST",
        });
        await loadGoalDetail(state.selectedGoalId);
        await loadGoals();
        toneStatus("Insights generated.", "success");
      } catch (error) {
        toneStatus(error.message || String(error), "error");
      }
    }

    async function generateRecommendations() {
      if (!state.selectedGoalId) {
        toneStatus("Select a goal first.", "error");
        return;
      }
      toneStatus("Generating recommendations…");
      try {
        await api(`/kpi/goals/${state.selectedGoalId}/recommendations/generate`, {
          method: "POST",
          body: JSON.stringify({ organization_id: currentOrg() }),
        });
        await loadGoalDetail(state.selectedGoalId);
        await loadGoals();
        toneStatus("Recommendations generated.", "success");
      } catch (error) {
        toneStatus(error.message || String(error), "error");
      }
    }

    async function updateInsightStatus(insightId, status) {
      toneStatus(`Updating insight to ${status}…`);
      try {
        await api(`/kpi/insights/${insightId}/status`, {
          method: "POST",
          body: JSON.stringify({
            organization_id: currentOrg(),
            status,
          }),
        });
        await loadGoalDetail(state.selectedGoalId);
        await loadGoals();
        toneStatus(`Insight marked ${status}.`, "success");
      } catch (error) {
        toneStatus(error.message || String(error), "error");
      }
    }

    async function updateRecommendationStatus(recommendationId, status) {
      toneStatus(`Updating recommendation to ${status}…`);
      try {
        await api(`/kpi/recommendations/${recommendationId}/status`, {
          method: "POST",
          body: JSON.stringify({
            organization_id: currentOrg(),
            status,
          }),
        });
        await loadGoalDetail(state.selectedGoalId);
        await loadGoals();
        toneStatus(`Recommendation marked ${status}.`, "success");
      } catch (error) {
        toneStatus(error.message || String(error), "error");
      }
    }

    async function previewRecommendationExecution(recommendationId) {
      toneStatus("Requesting preview…");
      try {
        const intent = await api(`/kpi/recommendations/${recommendationId}/execution-intents`, {
          method: "POST",
          body: JSON.stringify({
            organization_id: currentOrg(),
            execution_mode: "preview",
            requested_via: "kpi_console",
          }),
        });
        await api(`/kpi/execution-intents/${intent.execution_intent_id}/preview?organization_id=${encodeURIComponent(currentOrg())}`, {
          method: "POST",
        });
        await loadGoalDetail(state.selectedGoalId);
        await loadGoals();
        toneStatus("Preview execution completed.", "success");
      } catch (error) {
        toneStatus(error.message || String(error), "error");
      }
    }

    async function applyRecommendationExecution(recommendationId) {
      toneStatus("Requesting apply execution…");
      try {
        const intent = await api(`/kpi/recommendations/${recommendationId}/execution-intents`, {
          method: "POST",
          body: JSON.stringify({
            organization_id: currentOrg(),
            execution_mode: "apply",
            requested_via: "kpi_console",
          }),
        });
        await api(`/kpi/execution-intents/${intent.execution_intent_id}/apply?organization_id=${encodeURIComponent(currentOrg())}`, {
          method: "POST",
        });
        await loadGoalDetail(state.selectedGoalId);
        await loadGoals();
        toneStatus("Apply execution completed.", "success");
      } catch (error) {
        toneStatus(error.message || String(error), "error");
      }
    }

    document.getElementById("refresh-all").addEventListener("click", refreshAll);
    document.getElementById("create-scope").addEventListener("click", createScope);
    document.getElementById("create-observation").addEventListener("click", createObservation);
    document.getElementById("create-goal").addEventListener("click", createGoal);
    document.getElementById("refresh-metric").addEventListener("click", refreshMetric);
    document.getElementById("evaluate-goal").addEventListener("click", evaluateGoal);
    document.getElementById("generate-insights").addEventListener("click", generateInsights);
    document.getElementById("generate-recommendations").addEventListener("click", generateRecommendations);

    refreshAll();
  </script>
</body>
</html>
"""
