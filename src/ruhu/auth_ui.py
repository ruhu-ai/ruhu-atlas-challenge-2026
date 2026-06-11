from __future__ import annotations

import json
from html import escape

from .ui_theme import app_theme_styles


_GOOGLE_ICON = """
<svg viewBox="0 0 533.5 544.3" aria-hidden="true" class="provider-icon">
  <path fill="#4285F4" d="M533.5 278.4c0-18.5-1.5-37.1-4.7-55.3H272.1v104.8h147.7c-6.1 33.8-25.4 63.7-53.9 82.7v68h87c51-47 80.6-116.3 80.6-200.2z" />
  <path fill="#34A853" d="M272.1 544.3c73.5 0 135.5-24.1 180.6-65.4l-87-68c-24.2 16.5-55.3 25.9-93.6 25.9-71.9 0-132.8-48.6-154.6-113.9H27.9v71.1c46.3 92 139.2 150.3 244.2 150.3z" />
  <path fill="#FBBC04" d="M117.5 322.9c-11.4-33.8-11.4-70.4 0-104.2V147.6H27.9c-38.7 77.3-38.7 171.8 0 249.1l89.6-73.8z" />
  <path fill="#EA4335" d="M272.1 107.7c40.4-.6 79.5 14.8 109.3 43.1l81.5-81.5C405.2 24.9 339.4-.2 272.1 0 167.1 0 74.2 58.3 27.9 150.3l89.6 71.1C139.3 156.3 200.2 107.7 272.1 107.7z" />
</svg>
""".strip()


_KEY_ICON = """
<svg viewBox="0 0 24 24" aria-hidden="true" class="provider-icon">
  <path fill="currentColor" d="M7 14a5 5 0 1 1 4.9 6H11a5 5 0 0 1-4-6Zm5-3a3 3 0 1 0 0 6h.59l3.7-3.7V12h2v2h-2v2h-2v-1.17l-1.41 1.41A3 3 0 0 0 12 11Z" />
</svg>
""".strip()


_MAIL_ICON = """
<svg viewBox="0 0 24 24" aria-hidden="true" class="provider-icon">
  <path fill="currentColor" d="M4 5h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Zm0 2v.01L12 12l8-4.99V7H4Zm16 10V9.3l-7.47 4.67a1 1 0 0 1-1.06 0L4 9.3V17h16Z" />
</svg>
""".strip()


_ARROW_LEFT_ICON = """
<svg viewBox="0 0 24 24" aria-hidden="true" class="inline-icon">
  <path fill="currentColor" d="m10.83 19.03l-1.41 1.41L1.38 12.4l8.04-8.04 1.41 1.41L5.2 11.4H23v2H5.2l5.63 5.63Z" />
</svg>
""".strip()


_SPINNER_ICON = """
<svg viewBox="0 0 24 24" aria-hidden="true" class="spinner">
  <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-opacity="0.22" stroke-width="4"></circle>
  <path fill="currentColor" d="M22 12a10 10 0 0 0-10-10v4a6 6 0 0 1 6 6h4Z"></path>
</svg>
""".strip()


_ALERT_ICON = """
<svg viewBox="0 0 24 24" aria-hidden="true" class="status-icon">
  <path fill="currentColor" d="M12 2 1 21h22L12 2Zm1 15h-2v-2h2v2Zm0-4h-2V9h2v4Z" />
</svg>
""".strip()


_ERROR_CIRCLE_ICON = """
<svg viewBox="0 0 24 24" aria-hidden="true" class="status-icon">
  <path fill="currentColor" d="M12 2a10 10 0 1 0 10 10A10.01 10.01 0 0 0 12 2Zm4.24 12.83-1.41 1.41L12 13.41l-2.83 2.83-1.41-1.41L10.59 12 7.76 9.17l1.41-1.41L12 10.59l2.83-2.83 1.41 1.41L13.41 12Z" />
</svg>
""".strip()


_CHECK_ICON = """
<svg viewBox="0 0 24 24" aria-hidden="true" class="status-icon">
  <path fill="currentColor" d="M12 2a10 10 0 1 0 10 10A10.01 10.01 0 0 0 12 2Zm-1.11 14.48L6.4 11.99l1.41-1.41 3.08 3.08 5.3-5.3 1.41 1.41Z" />
</svg>
""".strip()


_AUTH_PAGE_CSS = """
    .auth-page {
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 3rem 1rem;
    }

    .auth-shell {
      width: 100%;
      max-width: 28rem;
    }

    .auth-brand {
      margin-bottom: 2rem;
      text-align: center;
    }

    .auth-brand h1 {
      margin: 0;
      font-size: 1.875rem;
      font-weight: 700;
      color: hsl(var(--primary));
      letter-spacing: -0.03em;
    }

    .auth-brand p {
      margin: 0.5rem 0 0;
      font-size: var(--text-sm);
      color: hsl(var(--muted-foreground));
    }

    .auth-card {
      background: hsl(var(--card));
      color: hsl(var(--card-foreground));
      border: 1px solid hsl(var(--border));
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      overflow: hidden;
    }

    .auth-card-header {
      padding: 1.5rem 1.5rem 0.5rem;
    }

    .auth-card-header.tight {
      padding-bottom: 0;
    }

    .auth-card-title {
      margin: 0;
      font-size: 1.5rem;
      font-weight: 700;
      text-align: center;
      letter-spacing: -0.03em;
    }

    .auth-card-description {
      margin: 0.5rem 0 0;
      font-size: var(--text-sm);
      color: hsl(var(--muted-foreground));
      text-align: center;
      line-height: 1.55;
    }

    .auth-card-content {
      padding: 1rem 1.5rem 1.5rem;
    }

    .stack {
      display: grid;
      gap: 0.75rem;
    }

    .field {
      display: grid;
      gap: 0.5rem;
    }

    .field label {
      font-size: var(--text-sm);
      color: hsl(var(--card-foreground));
      font-weight: 500;
    }

    .input {
      width: 100%;
      border: 1px solid hsl(var(--input));
      border-radius: calc(var(--radius) - 0.1rem);
      padding: 0.75rem 0.875rem;
      background: hsl(var(--card));
      color: hsl(var(--foreground));
      font: inherit;
      font-size: var(--text-sm);
    }

    .input[readonly] {
      background: hsl(var(--secondary));
    }

    .input:focus {
      outline: none;
      border-color: hsl(var(--ring));
      box-shadow: 0 0 0 4px rgba(var(--primary-rgb), 0.14);
    }

    .button {
      width: 100%;
      border: 1px solid transparent;
      border-radius: calc(var(--radius) - 0.1rem);
      padding: 0.75rem 1rem;
      background: hsl(var(--primary));
      color: hsl(var(--primary-foreground));
      font: inherit;
      font-size: var(--text-sm);
      font-weight: 600;
      cursor: pointer;
      transition: transform 120ms ease, box-shadow 120ms ease, background 120ms ease;
    }

    .button:hover:not(:disabled) {
      transform: translateY(-1px);
      box-shadow: 0 8px 24px rgba(var(--primary-rgb), 0.16);
    }

    .button:focus-visible {
      outline: none;
      box-shadow: 0 0 0 4px rgba(var(--primary-rgb), 0.18);
    }

    .button:disabled {
      opacity: 0.6;
      cursor: default;
      transform: none;
      box-shadow: none;
    }

    .button.outline {
      background: transparent;
      color: hsl(var(--foreground));
      border-color: hsl(var(--border));
    }

    .button-row {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 0.75rem;
      width: 100%;
    }

    .provider-icon {
      width: 1.125rem;
      height: 1.125rem;
      flex: none;
    }

    .inline-icon {
      width: 0.875rem;
      height: 0.875rem;
      flex: none;
    }

    .spinner {
      width: 1rem;
      height: 1rem;
      animation: spin 1s linear infinite;
      flex: none;
    }

    .status-icon {
      width: 1.5rem;
      height: 1.5rem;
      flex: none;
    }

    @keyframes spin {
      from { transform: rotate(0deg); }
      to { transform: rotate(360deg); }
    }

    .error-banner {
      margin-bottom: 1rem;
      padding: 0.75rem;
      border-radius: calc(var(--radius) - 0.2rem);
      background: rgba(220, 38, 38, 0.08);
      color: rgb(185, 28, 28);
      font-size: var(--text-sm);
      line-height: 1.5;
    }

    .muted-copy {
      font-size: var(--text-sm);
      color: hsl(var(--muted-foreground));
      line-height: 1.6;
      text-align: center;
    }

    .tiny-copy {
      font-size: var(--text-xs);
      color: hsl(var(--muted-foreground));
      line-height: 1.6;
      text-align: center;
    }

    .back-link {
      border: 0;
      background: none;
      padding: 0;
      margin: 0;
      font: inherit;
      display: inline-flex;
      width: 100%;
      align-items: center;
      justify-content: center;
      gap: 0.375rem;
      color: hsl(var(--muted-foreground));
      cursor: pointer;
      text-decoration: none;
      font-size: var(--text-sm);
    }

    .back-link:hover {
      color: hsl(var(--foreground));
    }

    .hidden {
      display: none !important;
    }

    .auth-loading {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 0.5rem;
      font-size: var(--text-sm);
      color: hsl(var(--muted-foreground));
    }

    .invite-hero {
      text-align: center;
      margin-bottom: 1rem;
    }

    .invite-hero .emoji {
      font-size: 2.25rem;
      line-height: 1;
      margin-bottom: 0.5rem;
    }

    .invite-details {
      border-radius: calc(var(--radius) - 0.1rem);
      background: hsl(var(--accent));
      padding: 1rem;
      display: grid;
      gap: 0.5rem;
      margin-bottom: 1rem;
    }

    .invite-detail {
      display: flex;
      justify-content: space-between;
      gap: 0.75rem;
      font-size: var(--text-sm);
    }

    .invite-detail .label {
      color: hsl(var(--muted-foreground));
    }

    .invite-detail .value {
      font-weight: 500;
      color: hsl(var(--foreground));
      text-transform: capitalize;
      text-align: right;
    }

    .status-shell {
      display: flex;
      min-height: 100vh;
      align-items: center;
      justify-content: center;
      padding: 1rem;
    }

    .status-card {
      width: 100%;
      max-width: 26rem;
      border: 1px solid hsl(var(--border));
      background: hsl(var(--card));
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      padding: 1.5rem;
      text-align: center;
    }

    .status-avatar {
      width: 3rem;
      height: 3rem;
      border-radius: 999px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      margin: 0 auto 1rem;
      background: rgba(220, 38, 38, 0.08);
      color: rgb(185, 28, 28);
    }

    .status-card h1 {
      margin: 0 0 0.5rem;
      font-size: 1.25rem;
      letter-spacing: -0.02em;
    }

    .status-card p {
      margin: 0 0 1.5rem;
      font-size: var(--text-sm);
      color: hsl(var(--muted-foreground));
      line-height: 1.6;
    }
"""


_CONSOLE_PAGE_CSS = """
    .console-page {
      min-height: 100vh;
      padding: 2rem 1rem 3rem;
    }

    .console-shell {
      max-width: 1120px;
      margin: 0 auto;
      display: grid;
      gap: 1rem;
    }

    .console-header,
    .console-panel,
    .console-summary {
      background: hsl(var(--card));
      border: 1px solid hsl(var(--border));
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }

    .console-header {
      padding: 1.25rem 1.5rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      flex-wrap: wrap;
    }

    .console-brand h1 {
      margin: 0;
      font-size: 1.5rem;
      letter-spacing: -0.03em;
      color: hsl(var(--primary));
    }

    .console-brand p {
      margin: 0.35rem 0 0;
      color: hsl(var(--muted-foreground));
      font-size: var(--text-sm);
    }

    .console-actions {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      flex-wrap: wrap;
    }

    .console-button {
      border: 1px solid hsl(var(--border));
      border-radius: calc(var(--radius) - 0.1rem);
      background: hsl(var(--secondary));
      color: hsl(var(--secondary-foreground));
      padding: 0.65rem 0.9rem;
      font: inherit;
      font-size: var(--text-sm);
      font-weight: 600;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 0.4rem;
    }

    .console-button.primary {
      background: hsl(var(--primary));
      color: hsl(var(--primary-foreground));
      border-color: transparent;
    }

    .console-button.danger {
      background: rgba(220, 38, 38, 0.1);
      color: rgb(185, 28, 28);
      border-color: rgba(220, 38, 38, 0.2);
    }

    .console-button.linkish {
      background: transparent;
    }

    .console-nav {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
    }

    .console-nav a {
      border: 1px solid hsl(var(--border));
      border-radius: 999px;
      padding: 0.55rem 0.9rem;
      text-decoration: none;
      font-size: var(--text-sm);
      color: hsl(var(--muted-foreground));
      background: hsl(var(--card));
    }

    .console-nav a.active {
      border-color: transparent;
      background: hsl(var(--primary));
      color: hsl(var(--primary-foreground));
    }

    .console-banner {
      padding: 0.85rem 1rem;
      border-radius: calc(var(--radius) - 0.1rem);
      font-size: var(--text-sm);
      line-height: 1.55;
    }

    .console-banner.info {
      background: rgba(var(--primary-rgb), 0.1);
      color: hsl(var(--foreground));
    }

    .console-banner.success {
      background: rgba(16, 185, 129, 0.12);
      color: rgb(4, 120, 87);
    }

    .console-banner.error {
      background: rgba(220, 38, 38, 0.08);
      color: rgb(185, 28, 28);
    }

    .console-grid {
      display: grid;
      gap: 1rem;
      grid-template-columns: 290px minmax(0, 1fr);
      align-items: start;
    }

    .console-summary,
    .console-panel {
      padding: 1.25rem;
    }

    .console-summary {
      display: grid;
      gap: 1rem;
      position: sticky;
      top: 1.25rem;
    }

    .summary-kicker {
      font-size: var(--text-xs);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: hsl(var(--muted-foreground));
    }

    .summary-title {
      margin: 0.2rem 0 0;
      font-size: 1.2rem;
      letter-spacing: -0.02em;
    }

    .summary-subtitle {
      margin: 0.35rem 0 0;
      font-size: var(--text-sm);
      color: hsl(var(--muted-foreground));
      line-height: 1.6;
    }

    .summary-list {
      display: grid;
      gap: 0.65rem;
    }

    .summary-item {
      display: grid;
      gap: 0.15rem;
      padding: 0.75rem;
      border-radius: calc(var(--radius) - 0.15rem);
      background: hsl(var(--accent));
    }

    .summary-item .label {
      font-size: var(--text-xs);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: hsl(var(--muted-foreground));
    }

    .summary-item .value {
      font-size: var(--text-sm);
      color: hsl(var(--foreground));
      font-weight: 600;
      overflow-wrap: anywhere;
    }

    .console-main {
      display: grid;
      gap: 1rem;
    }

    .console-section.hidden {
      display: none !important;
    }

    .console-panel h2 {
      margin: 0;
      font-size: 1.2rem;
      letter-spacing: -0.02em;
    }

    .console-panel p {
      margin: 0.45rem 0 0;
      font-size: var(--text-sm);
      color: hsl(var(--muted-foreground));
      line-height: 1.6;
    }

    .panel-stack,
    .list-stack,
    .identity-list,
    .session-list,
    .member-list,
    .invite-list,
    .admin-list {
      display: grid;
      gap: 0.9rem;
      margin-top: 1rem;
    }

    .grid-two {
      display: grid;
      gap: 0.85rem;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .textarea {
      min-height: 7rem;
      resize: vertical;
    }

    .identity-card,
    .session-card,
    .member-card,
    .invite-card,
    .admin-card {
      border: 1px solid hsl(var(--border));
      border-radius: calc(var(--radius) - 0.15rem);
      padding: 1rem;
      background: hsl(var(--background));
      display: grid;
      gap: 0.75rem;
    }

    .card-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 0.75rem;
      flex-wrap: wrap;
    }

    .card-title {
      margin: 0;
      font-size: 1rem;
      font-weight: 700;
    }

    .card-meta {
      margin: 0.15rem 0 0;
      color: hsl(var(--muted-foreground));
      font-size: var(--text-sm);
      line-height: 1.55;
    }

    .badge-row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
    }

    .badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 999px;
      padding: 0.3rem 0.65rem;
      font-size: var(--text-xs);
      font-weight: 700;
      background: hsl(var(--secondary));
      color: hsl(var(--secondary-foreground));
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }

    .section-actions,
    .inline-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 0.6rem;
      align-items: center;
    }

    .empty-state {
      padding: 1rem;
      border-radius: calc(var(--radius) - 0.15rem);
      background: hsl(var(--accent));
      color: hsl(var(--muted-foreground));
      font-size: var(--text-sm);
    }

    .member-sessions {
      display: grid;
      gap: 0.5rem;
      padding-top: 0.5rem;
      border-top: 1px solid hsl(var(--border));
    }

    .session-inline {
      padding: 0.65rem 0.75rem;
      border-radius: calc(var(--radius) - 0.2rem);
      background: hsl(var(--accent));
      font-size: var(--text-sm);
      color: hsl(var(--foreground));
    }

    .footnote {
      color: hsl(var(--muted-foreground));
      font-size: var(--text-xs);
      line-height: 1.6;
      margin-top: 0.75rem;
    }

    @media (max-width: 920px) {
      .console-grid {
        grid-template-columns: 1fr;
      }

      .console-summary {
        position: static;
      }

      .grid-two {
        grid-template-columns: 1fr;
      }
    }
"""


_COMMON_SCRIPT = """
function parseErrorMessage(payload, fallback) {
  if (payload && typeof payload.detail === "string" && payload.detail.trim()) {
    return payload.detail.trim();
  }
  if (payload && typeof payload.message === "string" && payload.message.trim()) {
    return payload.message.trim();
  }
  return fallback;
}

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function postJSON(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(payload),
  });
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    data = null;
  }
  if (!response.ok) {
    throw new Error(parseErrorMessage(data, "Request failed"));
  }
  return data;
}

function setButtonBusy(button, isBusy, busyLabel) {
  if (!(button instanceof HTMLButtonElement)) {
    return;
  }
  if (!button.dataset.defaultLabel) {
    button.dataset.defaultLabel = button.innerHTML;
  }
  button.disabled = isBusy;
  button.innerHTML = isBusy
    ? '<span class="button-row">' + __SPINNER__ + '<span>' + busyLabel + '</span></span>'
    : button.dataset.defaultLabel;
}
"""


def _base_html(*, title: str, body_html: str, script: str) -> str:
    script_tag = f"<script>{script}</script>" if script else ""
    return (
        "<!DOCTYPE html>"
        "<html lang=\"en\">"
        "<head>"
        "<meta charset=\"utf-8\" />"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />"
        f"<title>{escape(title)}</title>"
        f"<style>{app_theme_styles()}{_AUTH_PAGE_CSS}</style>"
        "</head>"
        f"<body>{body_html}{script_tag}</body>"
        "</html>"
    )


def _console_base_html(*, title: str, body_html: str, script: str) -> str:
    script_tag = f"<script>{script}</script>" if script else ""
    return (
        "<!DOCTYPE html>"
        "<html lang=\"en\">"
        "<head>"
        "<meta charset=\"utf-8\" />"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />"
        f"<title>{escape(title)}</title>"
        f"<style>{app_theme_styles()}{_AUTH_PAGE_CSS}{_CONSOLE_PAGE_CSS}</style>"
        "</head>"
        f"<body>{body_html}{script_tag}</body>"
        "</html>"
    )


def login_page_html() -> str:
    body = f"""
<main class="auth-page">
  <div class="auth-shell">
    <div class="auth-brand">
      <h1>Ruhu AI</h1>
      <p>Conversation Agent Platform</p>
    </div>
    <section class="auth-card">
      <header class="auth-card-header">
        <h2 id="card-title" class="auth-card-title">Log in to Ruhu AI</h2>
      </header>
      <div class="auth-card-content">
        <div id="error-banner" class="error-banner hidden" role="alert"></div>

        <div id="step-main" class="stack">
          <button id="google-button" class="button outline" type="button">
            <span class="button-row">{_GOOGLE_ICON}<span>Continue with Google</span></span>
          </button>
          <button id="magic-button" class="button outline" type="button">
            <span class="button-row">{_MAIL_ICON}<span>Continue with Magic Link</span></span>
          </button>
          <button id="sso-button" class="button outline" type="button">
            <span class="button-row">{_KEY_ICON}<span>Continue with SSO</span></span>
          </button>
        </div>

        <form id="step-magic-link-email" class="stack hidden">
          <div class="field">
            <label for="magic-email">Email Address</label>
            <input id="magic-email" class="input" type="email" placeholder="you@company.com" autocomplete="email" required />
          </div>
          <button id="magic-submit" class="button" type="submit">Send sign-in link</button>
          <button id="magic-back" class="back-link" type="button">{_ARROW_LEFT_ICON}<span>Back</span></button>
        </form>

        <div id="step-magic-link-sent" class="stack hidden">
          <p class="muted-copy">
            We sent a sign-in link to
            <span id="sent-email" style="font-weight:600;color:hsl(var(--foreground));"></span>.
            Check your inbox and click the link to sign in.
          </p>
          <p class="tiny-copy">The link expires in 15 minutes.</p>
          <button id="magic-resend" class="button outline" type="button">Resend link</button>
          <a class="back-link" href="/login">{_ARROW_LEFT_ICON}<span>Back to sign in</span></a>
        </div>

        <form id="step-sso-email" class="stack hidden">
          <div class="field">
            <label for="sso-email">Work Email</label>
            <input id="sso-email" class="input" type="email" placeholder="you@company.com" autocomplete="email" required />
          </div>
          <button id="sso-submit" class="button" type="submit">Continue with SSO</button>
          <button id="sso-back" class="back-link" type="button">{_ARROW_LEFT_ICON}<span>Back</span></button>
        </form>
      </div>
    </section>
  </div>
</main>
"""
    script = """
const titleByStep = {
  main: "Log in to Ruhu AI",
  "magic-link-email": "Continue with Magic Link",
  "magic-link-sent": "Check your email",
  "sso-email": "Continue with SSO",
};

const steps = {
  main: document.getElementById("step-main"),
  "magic-link-email": document.getElementById("step-magic-link-email"),
  "magic-link-sent": document.getElementById("step-magic-link-sent"),
  "sso-email": document.getElementById("step-sso-email"),
};

const cardTitle = document.getElementById("card-title");
const errorBanner = document.getElementById("error-banner");
const googleButton = document.getElementById("google-button");
const magicButton = document.getElementById("magic-button");
const ssoButton = document.getElementById("sso-button");
const magicSubmit = document.getElementById("magic-submit");
const ssoSubmit = document.getElementById("sso-submit");
const sentEmail = document.getElementById("sent-email");
const magicEmail = document.getElementById("magic-email");
const ssoEmail = document.getElementById("sso-email");

""" + _COMMON_SCRIPT.replace("__SPINNER__", json.dumps(_SPINNER_ICON)) + """
function setError(message) {
  if (message) {
    errorBanner.textContent = message;
    errorBanner.classList.remove("hidden");
  } else {
    errorBanner.textContent = "";
    errorBanner.classList.add("hidden");
  }
}

function setStep(stepName) {
  Object.entries(steps).forEach(([name, element]) => {
    if (!element) {
      return;
    }
    element.classList.toggle("hidden", name !== stepName);
  });
  if (cardTitle && titleByStep[stepName]) {
    cardTitle.textContent = titleByStep[stepName];
  }
}

async function startGoogle() {
  setError(null);
  setButtonBusy(googleButton, true, "Starting Google sign-in...");
  try {
    const response = await postJSON("/auth/oauth/google/start", {});
    window.location.assign(response.authorization_url);
  } catch (error) {
    setError(error instanceof Error ? error.message : "Failed to start Google sign-in");
    setButtonBusy(googleButton, false, "");
  }
}

async function requestMagicLink(event) {
  event.preventDefault();
  setError(null);
  const email = magicEmail.value.trim();
  if (!email) {
    return;
  }
  setButtonBusy(magicSubmit, true, "Sending sign-in link...");
  try {
    await postJSON("/auth/magic-link/request", { email });
    sentEmail.textContent = email;
    setStep("magic-link-sent");
  } catch (error) {
    setError(error instanceof Error ? error.message : "Failed to send sign-in link");
  } finally {
    setButtonBusy(magicSubmit, false, "");
  }
}

async function startSSO(event) {
  event.preventDefault();
  setError(null);
  const email = ssoEmail.value.trim();
  if (!email) {
    return;
  }
  setButtonBusy(ssoSubmit, true, "Starting SSO sign-in...");
  try {
    const response = await postJSON("/auth/oauth/sso/start", {
      email,
    });
    window.location.assign(response.authorization_url);
  } catch (error) {
    setError(error instanceof Error ? error.message : "Failed to start SSO sign-in");
    setButtonBusy(ssoSubmit, false, "");
  }
}

googleButton?.addEventListener("click", startGoogle);
magicButton?.addEventListener("click", () => {
  setError(null);
  magicEmail.value = "";
  setStep("magic-link-email");
  magicEmail.focus();
});
ssoButton?.addEventListener("click", () => {
  setError(null);
  ssoEmail.value = "";
  setStep("sso-email");
  ssoEmail.focus();
});
document.getElementById("step-magic-link-email")?.addEventListener("submit", requestMagicLink);
document.getElementById("step-sso-email")?.addEventListener("submit", startSSO);
document.getElementById("magic-back")?.addEventListener("click", () => { setError(null); setStep("main"); });
document.getElementById("sso-back")?.addEventListener("click", () => { setError(null); setStep("main"); });
document.getElementById("magic-resend")?.addEventListener("click", () => {
  setError(null);
  magicEmail.value = sentEmail.textContent || "";
  setStep("magic-link-email");
  magicEmail.focus();
});
"""
    return _base_html(title="Log in to Ruhu AI", body_html=body, script=script)


def signup_page_html() -> str:
    body = f"""
<main class="auth-page">
  <div class="auth-shell">
    <div class="auth-brand">
      <h1>Ruhu AI</h1>
      <p>Invitation-only signup</p>
    </div>
    <section class="auth-card">
      <header class="auth-card-header">
        <h2 id="signup-title" class="auth-card-title">Validating invitation</h2>
      </header>
      <div class="auth-card-content stack">
        <div id="signup-error" class="error-banner hidden" role="alert"></div>

        <div id="signup-loading" class="auth-loading">
          {_SPINNER_ICON}
          <span>Verifying invite token...</span>
        </div>

        <div id="signup-email-field" class="field hidden">
          <label for="signup-email">Invited Email</label>
          <input id="signup-email" class="input" type="email" readonly />
        </div>

        <div id="signup-ready" class="stack hidden">
          <button id="signup-google" class="button outline" type="button">
            <span class="button-row">{_GOOGLE_ICON}<span>Continue with Google</span></span>
          </button>
          <button id="signup-magic" class="button outline" type="button">
            <span class="button-row">{_MAIL_ICON}<span>Send me a sign-in link</span></span>
          </button>
          <button id="signup-sso" class="button outline" type="button">
            <span class="button-row">{_KEY_ICON}<span>Continue with SSO</span></span>
          </button>
        </div>

        <p id="signup-sent" class="muted-copy hidden">
          We sent a sign-in link to <span id="signup-sent-email" style="font-weight:600;color:hsl(var(--foreground));"></span>.
          The link expires in 15 minutes.
        </p>

        <a id="signup-back" class="back-link hidden" href="/login">{_ARROW_LEFT_ICON}<span>Back to sign in</span></a>
      </div>
    </section>
  </div>
</main>
"""
    script = """
const signupTitle = document.getElementById("signup-title");
const signupError = document.getElementById("signup-error");
const signupLoading = document.getElementById("signup-loading");
const signupEmailField = document.getElementById("signup-email-field");
const signupEmail = document.getElementById("signup-email");
const signupReady = document.getElementById("signup-ready");
const signupSent = document.getElementById("signup-sent");
const signupSentEmail = document.getElementById("signup-sent-email");
const signupBack = document.getElementById("signup-back");
const signupGoogle = document.getElementById("signup-google");
const signupMagic = document.getElementById("signup-magic");
const signupSSO = document.getElementById("signup-sso");
let inviteToken = "";
let inviteEmail = "";

""" + _COMMON_SCRIPT.replace("__SPINNER__", json.dumps(_SPINNER_ICON)) + """
function setSignupError(message) {
  if (message) {
    signupError.textContent = message;
    signupError.classList.remove("hidden");
  } else {
    signupError.textContent = "";
    signupError.classList.add("hidden");
  }
}

function setSignupStep(stepName) {
  signupLoading.classList.toggle("hidden", stepName !== "loading");
  signupEmailField.classList.toggle("hidden", !(stepName === "ready" || stepName === "sent"));
  signupReady.classList.toggle("hidden", stepName !== "ready");
  signupSent.classList.toggle("hidden", stepName !== "sent");
  signupBack.classList.toggle("hidden", !(stepName === "invalid" || stepName === "sent"));

  if (stepName === "loading") signupTitle.textContent = "Validating invitation";
  if (stepName === "invalid") signupTitle.textContent = "Invite not valid";
  if (stepName === "ready") signupTitle.textContent = "Complete your signup";
  if (stepName === "sent") signupTitle.textContent = "Check your email";
}

function currentInviteToken() {
  const params = new URLSearchParams(window.location.search);
  return (params.get("invite") || params.get("token") || "").trim();
}

async function validateInvite() {
  inviteToken = currentInviteToken();
  if (!inviteToken) {
    setSignupStep("invalid");
    setSignupError("This signup link is invalid or missing an invite token.");
    return;
  }

  try {
    const response = await fetch("/auth/invite/validate?token=" + encodeURIComponent(inviteToken), {
      credentials: "same-origin",
    });
    const payload = await response.json();
    if (!response.ok || !payload.valid || !payload.email) {
      setSignupStep("invalid");
      setSignupError("This invite token is invalid, expired, or already used.");
      return;
    }
    inviteEmail = payload.email;
    signupEmail.value = inviteEmail;
    setSignupStep("ready");
  } catch (error) {
    setSignupStep("invalid");
    setSignupError(error instanceof Error ? error.message : "Failed to validate invite token.");
  } finally {
    window.history.replaceState({}, "", "/signup");
  }
}

async function signupWithGoogle() {
  setSignupError(null);
  setButtonBusy(signupGoogle, true, "Starting Google sign-in...");
  try {
    const response = await postJSON("/auth/oauth/google/start", {
      invitation_token: inviteToken,
    });
    window.location.assign(response.authorization_url);
  } catch (error) {
    setSignupError(error instanceof Error ? error.message : "Failed to start Google sign-in");
    setButtonBusy(signupGoogle, false, "");
  }
}

async function signupWithMagicLink() {
  setSignupError(null);
  setButtonBusy(signupMagic, true, "Sending sign-in link...");
  try {
    await postJSON("/auth/magic-link/request", {
      email: inviteEmail,
      invitation_token: inviteToken,
    });
    signupSentEmail.textContent = inviteEmail;
    setSignupStep("sent");
  } catch (error) {
    setSignupError(error instanceof Error ? error.message : "Failed to send sign-in link");
  } finally {
    setButtonBusy(signupMagic, false, "");
  }
}

async function signupWithSSO() {
  setSignupError(null);
  setButtonBusy(signupSSO, true, "Starting SSO sign-in...");
  try {
    const response = await postJSON("/auth/oauth/sso/start", {
      invitation_token: inviteToken,
    });
    window.location.assign(response.authorization_url);
  } catch (error) {
    setSignupError(error instanceof Error ? error.message : "Failed to start SSO sign-in");
    setButtonBusy(signupSSO, false, "");
  }
}

signupGoogle?.addEventListener("click", signupWithGoogle);
signupMagic?.addEventListener("click", signupWithMagicLink);
signupSSO?.addEventListener("click", signupWithSSO);
setSignupStep("loading");
validateInvite();
"""
    return _base_html(title="Complete your signup", body_html=body, script=script)


def accept_invitation_page_html() -> str:
    body = f"""
<main class="auth-page">
  <div class="auth-shell">
    <section class="auth-card">
      <header class="auth-card-header tight">
        <div class="invite-hero">
          <div class="emoji">🎙️</div>
          <h2 id="accept-title" class="auth-card-title">Validating invitation</h2>
          <p id="accept-description" class="auth-card-description">Loading invitation details...</p>
        </div>
      </header>
      <div class="auth-card-content">
        <div id="accept-error" class="error-banner hidden" role="alert"></div>

        <div id="accept-loading" class="auth-loading">
          {_SPINNER_ICON}
          <span>Loading invitation...</span>
        </div>

        <div id="accept-details" class="invite-details hidden">
          <div class="invite-detail">
            <span class="label">Email:</span>
            <span id="accept-email" class="value"></span>
          </div>
          <div class="invite-detail">
            <span class="label">Role:</span>
            <span id="accept-role" class="value"></span>
          </div>
        </div>

        <div id="accept-ready" class="stack hidden">
          <button id="accept-google" class="button outline" type="button">
            <span class="button-row">{_GOOGLE_ICON}<span>Continue with Google</span></span>
          </button>
          <button id="accept-magic" class="button outline" type="button">
            <span class="button-row">{_MAIL_ICON}<span>Send me a sign-in link</span></span>
          </button>
          <button id="accept-sso" class="button outline" type="button">
            <span class="button-row">{_KEY_ICON}<span>Continue with SSO</span></span>
          </button>
        </div>

        <p id="accept-sent" class="muted-copy hidden">
          We sent a sign-in link to <span id="accept-sent-email" style="font-weight:600;color:hsl(var(--foreground));"></span>.
          Check your inbox and click the link to sign in.
        </p>

        <a id="accept-back" class="back-link hidden" href="/login">Go to Login</a>
      </div>
    </section>
  </div>
</main>
"""
    script = """
const acceptTitle = document.getElementById("accept-title");
const acceptDescription = document.getElementById("accept-description");
const acceptError = document.getElementById("accept-error");
const acceptLoading = document.getElementById("accept-loading");
const acceptDetails = document.getElementById("accept-details");
const acceptReady = document.getElementById("accept-ready");
const acceptSent = document.getElementById("accept-sent");
const acceptBack = document.getElementById("accept-back");
const acceptEmail = document.getElementById("accept-email");
const acceptRole = document.getElementById("accept-role");
const acceptSentEmail = document.getElementById("accept-sent-email");
const acceptGoogle = document.getElementById("accept-google");
const acceptMagic = document.getElementById("accept-magic");
const acceptSSO = document.getElementById("accept-sso");
let invitationToken = "";
let invitationEmail = "";

""" + _COMMON_SCRIPT.replace("__SPINNER__", json.dumps(_SPINNER_ICON)) + """
function setAcceptError(message) {
  if (message) {
    acceptError.textContent = message;
    acceptError.classList.remove("hidden");
  } else {
    acceptError.textContent = "";
    acceptError.classList.add("hidden");
  }
}

function setAcceptStep(stepName) {
  acceptLoading.classList.toggle("hidden", stepName !== "loading");
  acceptDetails.classList.toggle("hidden", !(stepName === "ready" || stepName === "sent"));
  acceptReady.classList.toggle("hidden", stepName !== "ready");
  acceptSent.classList.toggle("hidden", stepName !== "sent");
  acceptBack.classList.toggle("hidden", !(stepName === "invalid" || stepName === "sent"));
}

function currentToken() {
  const params = new URLSearchParams(window.location.search);
  return (params.get("token") || params.get("invite") || "").trim();
}

async function loadInvitation() {
  invitationToken = currentToken();
  if (!invitationToken) {
    setAcceptStep("invalid");
    acceptTitle.textContent = "Invalid Invitation";
    acceptDescription.textContent = "Invalid invitation link";
    setAcceptError("Invalid invitation link");
    return;
  }

  try {
    const response = await fetch("/auth/invite/validate?token=" + encodeURIComponent(invitationToken), {
      credentials: "same-origin",
    });
    const payload = await response.json();
    if (!response.ok || !payload.valid || !payload.email) {
      setAcceptStep("invalid");
      acceptTitle.textContent = "Invalid Invitation";
      acceptDescription.textContent = "This invitation has expired or has already been used";
      setAcceptError("This invitation has expired or has already been used");
      return;
    }
    invitationEmail = payload.email;
    acceptEmail.textContent = payload.email;
    acceptRole.textContent = payload.role || "member";
    acceptSentEmail.textContent = payload.email;
    acceptTitle.textContent = payload.organization_name ? "Join " + payload.organization_name : "Join Ruhu";
    acceptDescription.textContent = payload.invited_by_name
      ? payload.invited_by_name + " has invited you to join their team"
      : "You have been invited to join the team";
    setAcceptStep("ready");
  } catch (error) {
    setAcceptStep("invalid");
    acceptTitle.textContent = "Invalid Invitation";
    acceptDescription.textContent = "Failed to load invitation";
    setAcceptError(error instanceof Error ? error.message : "Failed to load invitation");
  } finally {
    window.history.replaceState({}, document.title, window.location.pathname);
  }
}

async function continueWithGoogle() {
  setAcceptError(null);
  setButtonBusy(acceptGoogle, true, "Starting Google sign-in...");
  try {
    const response = await postJSON("/auth/oauth/google/start", {
      invitation_token: invitationToken,
    });
    window.location.assign(response.authorization_url);
  } catch (error) {
    setAcceptError(error instanceof Error ? error.message : "Failed to start Google sign-in");
    setButtonBusy(acceptGoogle, false, "");
  }
}

async function sendMagicLink() {
  setAcceptError(null);
  setButtonBusy(acceptMagic, true, "Sending sign-in link...");
  try {
    await postJSON("/auth/magic-link/request", {
      email: invitationEmail,
      invitation_token: invitationToken,
    });
    setAcceptStep("sent");
  } catch (error) {
    setAcceptError(error instanceof Error ? error.message : "Failed to send sign-in link");
  } finally {
    setButtonBusy(acceptMagic, false, "");
  }
}

async function continueWithSSO() {
  setAcceptError(null);
  setButtonBusy(acceptSSO, true, "Starting SSO sign-in...");
  try {
    const response = await postJSON("/auth/oauth/sso/start", {
      invitation_token: invitationToken,
    });
    window.location.assign(response.authorization_url);
  } catch (error) {
    setAcceptError(error instanceof Error ? error.message : "Failed to start SSO sign-in");
    setButtonBusy(acceptSSO, false, "");
  }
}

acceptGoogle?.addEventListener("click", continueWithGoogle);
acceptMagic?.addEventListener("click", sendMagicLink);
acceptSSO?.addEventListener("click", continueWithSSO);
setAcceptStep("loading");
loadInvitation();
"""
    return _base_html(title="Accept Invitation", body_html=body, script=script)


def magic_link_callback_page_html(*, success_redirect_path: str) -> str:
    body = f"""
<main class="status-shell">
  <section class="status-card">
    <div id="magic-error-shell" class="hidden">
      <div class="status-avatar">{_ALERT_ICON}</div>
      <p id="magic-error-message" style="margin-top:0;font-weight:500;color:hsl(var(--foreground));"></p>
      <a href="/login" class="back-link" style="justify-content:center;">Back to sign in</a>
    </div>
    <div id="magic-loading-shell">
      <div style="display:flex;justify-content:center;margin-bottom:1rem;">{_SPINNER_ICON}</div>
      <p style="margin:0;font-size:var(--text-sm);color:hsl(var(--muted-foreground));">Signing you in…</p>
    </div>
  </section>
</main>
"""
    script = """
const magicErrorShell = document.getElementById("magic-error-shell");
const magicLoadingShell = document.getElementById("magic-loading-shell");
const magicErrorMessage = document.getElementById("magic-error-message");
const params = new URLSearchParams(window.location.search);
const token = (params.get("token") || "").trim();
const successRedirectPath = __SUCCESS_REDIRECT__;

function showMagicError(message) {
  magicErrorMessage.textContent = message;
  magicErrorShell.classList.remove("hidden");
  magicLoadingShell.classList.add("hidden");
}

if (!token) {
  showMagicError("Invalid sign-in link. Please request a new one.");
} else {
  window.history.replaceState({}, document.title, window.location.pathname);
  postJSON("/auth/magic-link/verify", { token })
    .then(() => {
      window.location.replace(successRedirectPath);
    })
    .catch((error) => {
      showMagicError(error instanceof Error ? error.message : "Invalid or expired sign-in link.");
    });
}
""".replace("__SUCCESS_REDIRECT__", json.dumps(success_redirect_path))
    return _base_html(
        title="Signing in to Ruhu AI",
        body_html=body,
        script=_COMMON_SCRIPT.replace("__SPINNER__", json.dumps(_SPINNER_ICON)) + script,
    )


def auth_callback_page_html(*, success_redirect_path: str) -> str:
    body = f"""
<main class="status-shell">
  <section class="status-card">
    <div id="oauth-error-shell" class="hidden">
      <div class="status-avatar">{_ERROR_CIRCLE_ICON}</div>
      <h1>Sign-in Failed</h1>
      <p id="oauth-error-message"></p>
      <a href="/login" class="button" style="display:inline-flex;width:auto;text-decoration:none;">Back to Login</a>
    </div>
    <div id="oauth-loading-shell">
      <div style="display:flex;justify-content:center;margin-bottom:1rem;color:hsl(var(--primary));">{_SPINNER_ICON}</div>
      <h1>Completing Sign-in</h1>
      <p>Please wait...</p>
    </div>
  </section>
</main>
"""
    script = """
const oauthErrorShell = document.getElementById("oauth-error-shell");
const oauthLoadingShell = document.getElementById("oauth-loading-shell");
const oauthErrorMessage = document.getElementById("oauth-error-message");
const searchParams = new URLSearchParams(window.location.search);
const code = searchParams.get("code");
const state = searchParams.get("state");
const providerError = searchParams.get("error");
const providerErrorDescription = searchParams.get("error_description");
const successRedirectPath = __SUCCESS_REDIRECT__;

function showOAuthError(message) {
  oauthErrorMessage.textContent = message;
  oauthErrorShell.classList.remove("hidden");
  oauthLoadingShell.classList.add("hidden");
}

if (providerError) {
  showOAuthError(
    providerErrorDescription ||
      (providerError === "access_denied" ? "Authorization was denied." : "Authentication failed.")
  );
} else if (!code || !state) {
  showOAuthError("Missing authentication response parameters.");
} else {
  const payload = {
    code,
    state,
    redirect_uri: window.location.origin + "/auth/callback",
  };
  window.history.replaceState({}, document.title, window.location.pathname);
  postJSON("/auth/oauth/callback", payload)
    .then(() => {
      window.location.replace(successRedirectPath);
    })
    .catch((error) => {
      showOAuthError(error instanceof Error ? error.message : "Authentication failed");
    });
}
""".replace("__SUCCESS_REDIRECT__", json.dumps(success_redirect_path))
    return _base_html(
        title="Completing Sign-in",
        body_html=body,
        script=_COMMON_SCRIPT.replace("__SPINNER__", json.dumps(_SPINNER_ICON)) + script,
    )


def app_console_page_html() -> str:
    body = f"""
<main class="console-page">
  <div class="console-shell">
    <header class="console-header">
      <div class="console-brand">
        <h1>Ruhu AI</h1>
        <p>Account, sessions, organization settings, members, and invitations.</p>
      </div>
      <div class="console-actions">
        <a class="console-button linkish" href="/tickets">Open Tickets</a>
        <a class="console-button linkish" href="/playground">Open Playground</a>
        <button id="logout-button" class="console-button danger" type="button">Sign out</button>
      </div>
    </header>

    <nav class="console-nav">
      <a href="#profile" data-section-link="profile" class="active">Profile</a>
      <a href="#sessions" data-section-link="sessions">Sessions</a>
      <a href="#organization" data-section-link="organization">Organization</a>
      <a href="#members" data-section-link="members">Members</a>
      <a href="#invitations" data-section-link="invitations">Invitations</a>
      <a href="/internal/admin" id="admin-link" class="hidden">Internal Admin</a>
    </nav>

    <div id="console-banner" class="console-banner info hidden" role="status"></div>

    <div class="console-grid">
      <aside class="console-summary">
        <div>
          <div class="summary-kicker">Workspace</div>
          <h2 id="summary-user" class="summary-title">Loading account…</h2>
          <p id="summary-subtitle" class="summary-subtitle">Checking your current session and organization context.</p>
        </div>

        <div class="summary-list">
          <div class="summary-item">
            <span class="label">Organization</span>
            <span id="summary-organization" class="value">-</span>
          </div>
          <div class="summary-item">
            <span class="label">Role</span>
            <span id="summary-role" class="value">-</span>
          </div>
          <div class="summary-item">
            <span class="label">Session</span>
            <span id="summary-session" class="value">-</span>
          </div>
          <div class="summary-item">
            <span class="label">Linked Identities</span>
            <span id="summary-identities" class="value">-</span>
          </div>
        </div>

        <p class="footnote">
          Account owners and admins can update organization settings, invitations, and member access from this shell.
        </p>
      </aside>

      <div class="console-main">
        <section id="section-profile" class="console-section console-panel">
          <h2>Profile</h2>
          <p>Update your personal account details and review linked sign-in identities.</p>

          <form id="profile-form" class="panel-stack">
            <div class="grid-two">
              <div class="field">
                <label for="profile-email">Email Address</label>
                <input id="profile-email" class="input" type="email" readonly />
              </div>
              <div class="field">
                <label for="profile-display-name">Display Name</label>
                <input id="profile-display-name" class="input" type="text" />
              </div>
            </div>
            <div class="grid-two">
              <div class="field">
                <label for="profile-avatar-url">Avatar URL</label>
                <input id="profile-avatar-url" class="input" type="url" />
              </div>
              <div class="field">
                <label for="profile-timezone">Timezone</label>
                <input id="profile-timezone" class="input" type="text" />
              </div>
              <div class="field">
                <label for="profile-language">Language</label>
                <input id="profile-language" class="input" type="text" />
              </div>
            </div>
            <div class="field">
              <label for="profile-preferences">Preferences JSON</label>
              <textarea id="profile-preferences" class="input textarea" placeholder='{{"theme":"warm"}}'></textarea>
            </div>
            <div class="section-actions">
              <button id="profile-save" class="console-button primary" type="submit">Save profile</button>
            </div>
          </form>

          <div class="identity-list" id="identity-list"></div>
        </section>

        <section id="section-sessions" class="console-section console-panel hidden">
          <h2>Sessions</h2>
          <p>Review your active device sessions and revoke them selectively.</p>
          <div class="section-actions">
            <button id="refresh-sessions" class="console-button" type="button">Refresh sessions</button>
          </div>
          <div class="session-list" id="session-list"></div>
        </section>

        <section id="section-organization" class="console-section console-panel hidden">
          <h2>Organization Settings</h2>
          <p>Manage the workspace profile, security cutover, and SSO posture.</p>

          <form id="organization-form" class="panel-stack">
            <div class="grid-two">
              <div class="field">
                <label for="org-name">Organization Name</label>
                <input id="org-name" class="input" type="text" />
              </div>
              <div class="field">
                <label for="org-domain">Domain</label>
                <input id="org-domain" class="input" type="text" />
              </div>
            </div>
            <div class="grid-two">
              <div class="field">
                <label for="org-email">Email</label>
                <input id="org-email" class="input" type="email" />
              </div>
              <div class="field">
                <label for="org-phone">Phone</label>
                <input id="org-phone" class="input" type="text" />
              </div>
            </div>
            <div class="grid-two">
              <div class="field">
                <label for="org-icon-url">Icon URL</label>
                <input id="org-icon-url" class="input" type="url" />
              </div>
              <div class="field">
                <label for="org-brand-color">Brand Color</label>
                <input id="org-brand-color" class="input" type="text" />
              </div>
            </div>
            <div class="field">
              <label for="org-description">Description</label>
              <textarea id="org-description" class="input textarea"></textarea>
            </div>
            <div class="grid-two">
              <div class="field">
                <label for="org-settings">Settings JSON</label>
                <textarea id="org-settings" class="input textarea" placeholder='{{"support_email":"ops@company.com"}}'></textarea>
              </div>
              <div class="field">
                <label for="org-metadata">Metadata JSON</label>
                <textarea id="org-metadata" class="input textarea" placeholder='{{"industry":"healthcare"}}'></textarea>
              </div>
            </div>
            <div class="section-actions">
              <button id="organization-save" class="console-button primary" type="submit">Save organization</button>
              <button id="organization-revoke" class="console-button danger" type="button">Revoke all org sessions</button>
            </div>
          </form>

          <div class="console-panel" style="padding:1rem;margin-top:1rem;">
            <h2 style="font-size:1rem;">Enterprise SSO</h2>
            <p>Configure OIDC SSO and enforcement for this organization.</p>
            <form id="sso-form" class="panel-stack">
              <div class="grid-two">
                <div class="field">
                  <label for="sso-issuer-url">Issuer URL</label>
                  <input id="sso-issuer-url" class="input" type="url" />
                </div>
                <div class="field">
                  <label for="sso-client-id">Client ID</label>
                  <input id="sso-client-id" class="input" type="text" />
                </div>
              </div>
              <div class="grid-two">
                <div class="field">
                  <label for="sso-client-secret-ref">Client Secret Ref</label>
                  <input id="sso-client-secret-ref" class="input" type="text" placeholder="env:RUHU_SSO_CLIENT_SECRET__ORG" />
                </div>
                <div class="field">
                  <label for="sso-allowed-domains">Allowed Domains</label>
                  <input id="sso-allowed-domains" class="input" type="text" placeholder="acme.com, subsidiary.com" />
                </div>
              </div>
              <div class="field">
                <label for="sso-scopes">Scopes</label>
                <input id="sso-scopes" class="input" type="text" placeholder="openid, profile, email" />
              </div>
              <div class="grid-two">
                <label class="field"><span>Active</span><input id="sso-active" type="checkbox" /></label>
                <label class="field"><span>Enforce SSO</span><input id="sso-enforce" type="checkbox" /></label>
              </div>
              <label class="field"><span>JIT Provisioning</span><input id="sso-jit" type="checkbox" /></label>
              <div class="section-actions">
                <button id="sso-save" class="console-button primary" type="submit">Save SSO</button>
                <button id="sso-disable" class="console-button" type="button">Disable SSO</button>
              </div>
            </form>
          </div>
        </section>

        <section id="section-members" class="console-section console-panel hidden">
          <h2>Members</h2>
          <p>Review organization members, update their role, and manage their active sessions.</p>
          <div class="section-actions">
            <button id="refresh-members" class="console-button" type="button">Refresh members</button>
          </div>
          <div class="member-list" id="member-list"></div>
        </section>

        <section id="section-invitations" class="console-section console-panel hidden">
          <h2>Invitations</h2>
          <p>Create and revoke team invitations for Google, magic-link, and SSO onboarding.</p>
          <form id="invitation-form" class="panel-stack">
            <div class="grid-two">
              <div class="field">
                <label for="invite-email">Email</label>
                <input id="invite-email" class="input" type="email" required />
              </div>
              <div class="field">
                <label for="invite-role">Role</label>
                <select id="invite-role" class="input">
                  <option value="admin">Admin</option>
                  <option value="developer" selected>Developer</option>
                  <option value="analyst">Analyst</option>
                </select>
              </div>
            </div>
            <label class="field"><span>Invite as account owner</span><input id="invite-owner" type="checkbox" /></label>
            <div class="section-actions">
              <button id="invite-create" class="console-button primary" type="submit">Create invitation</button>
            </div>
          </form>
          <p id="invite-form-note" class="footnote hidden">Only organization admins can create invitations.</p>
          <div class="invite-list" id="invite-list"></div>
        </section>
      </div>
    </div>
  </div>
</main>
"""
    script = """
const state = {
  me: null,
  organization: null,
  identities: [],
  sessions: [],
  members: [],
  invitations: [],
  ssoConfig: null,
};

const sections = ["profile", "sessions", "organization", "members", "invitations"];
const navLinks = Array.from(document.querySelectorAll("[data-section-link]"));
const banner = document.getElementById("console-banner");
const adminLink = document.getElementById("admin-link");

async function requestJSON(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
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
    throw new Error(parseErrorMessage(payload, "Request failed"));
  }
  return payload;
}

function showBanner(message, tone) {
  if (!message) {
    banner.textContent = "";
    banner.className = "console-banner info hidden";
    return;
  }
  banner.textContent = message;
  banner.className = "console-banner " + tone;
}

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatDate(value) {
  if (!value) return "Unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

function parseJSONObject(value, fallbackLabel) {
  const trimmed = String(value || "").trim();
  if (!trimmed) {
    return {};
  }
  let parsed = null;
  try {
    parsed = JSON.parse(trimmed);
  } catch (_error) {
    throw new Error(fallbackLabel + " must be valid JSON.");
  }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error(fallbackLabel + " must be a JSON object.");
  }
  return parsed;
}

function currentSection() {
  const hashValue = window.location.hash.replace(/^#/, "");
  return sections.includes(hashValue) ? hashValue : "profile";
}

function showSection(sectionName) {
  sections.forEach((name) => {
    const section = document.getElementById("section-" + name);
    if (section) {
      section.classList.toggle("hidden", name !== sectionName);
    }
  });
  navLinks.forEach((link) => {
    link.classList.toggle("active", link.dataset.sectionLink === sectionName);
  });
}

function canManageOrganization() {
  return Boolean(
    state.me &&
    state.me.organization &&
    (state.me.organization.role === "admin" || state.me.organization.is_account_owner)
  );
}

function updateSummary() {
  if (!state.me) return;
  const summaryOrganization = state.organization || state.me.organization;
  document.getElementById("summary-user").textContent =
    state.me.user.display_name || state.me.user.email;
  document.getElementById("summary-subtitle").textContent =
    state.me.user.email + " • " + summaryOrganization.name;
  document.getElementById("summary-organization").textContent = summaryOrganization.name;
  document.getElementById("summary-role").textContent =
    state.me.organization.role + (state.me.organization.is_account_owner ? " / account owner" : "");
  document.getElementById("summary-session").textContent = state.me.session_id;
  document.getElementById("summary-identities").textContent = String(state.identities.length);
  adminLink.classList.toggle("hidden", !state.me.user.is_superuser);
}

function populateProfileForm() {
  if (!state.me) return;
  document.getElementById("profile-email").value = state.me.user.email || "";
  document.getElementById("profile-display-name").value = state.me.user.display_name || "";
  document.getElementById("profile-avatar-url").value = state.me.user.avatar_url || "";
  document.getElementById("profile-timezone").value = state.me.user.timezone || "";
  document.getElementById("profile-language").value = state.me.user.language || "";
  document.getElementById("profile-preferences").value = JSON.stringify(state.me.user.preferences || {}, null, 2);
}

function renderIdentities() {
  const list = document.getElementById("identity-list");
  if (!list) return;
  if (!state.identities.length) {
    list.innerHTML = '<div class="empty-state">No linked external identities yet.</div>';
    return;
  }
  list.innerHTML = state.identities
    .map((identity) => `
      <article class="identity-card">
        <div class="card-head">
          <div>
            <h3 class="card-title">${escapeHTML(identity.provider_type)}</h3>
            <p class="card-meta">${escapeHTML(identity.email || "No email returned")} • ${escapeHTML(identity.provider_key)}</p>
          </div>
          <div class="badge-row"><span class="badge">Linked</span></div>
        </div>
      </article>
    `)
    .join("");
}

function renderSessions() {
  const list = document.getElementById("session-list");
  if (!list) return;
  if (!state.sessions.length) {
    list.innerHTML = '<div class="empty-state">No active sessions found.</div>';
    return;
  }
  list.innerHTML = state.sessions
    .map((session) => `
      <article class="session-card">
        <div class="card-head">
          <div>
            <h3 class="card-title">${session.is_current ? "Current session" : "Session " + escapeHTML(session.session_id)}</h3>
            <p class="card-meta">Issued ${formatDate(session.issued_at)} • Last seen ${formatDate(session.last_seen_at || session.issued_at)}</p>
          </div>
          <div class="badge-row">
            ${session.is_current ? '<span class="badge">Current</span>' : ""}
            ${session.revoked_at ? '<span class="badge">Revoked</span>' : ""}
          </div>
        </div>
        <div class="summary-list">
          <div class="summary-item"><span class="label">IP</span><span class="value">${escapeHTML(session.last_seen_ip || session.created_ip || "Unknown")}</span></div>
          <div class="summary-item"><span class="label">User agent</span><span class="value">${escapeHTML(session.user_agent || "Unknown")}</span></div>
        </div>
        <div class="inline-actions">
          <button class="console-button ${session.is_current ? "danger" : ""}" type="button" data-revoke-session="${escapeHTML(session.session_id)}">
            ${session.is_current ? "Sign out current session" : "Revoke session"}
          </button>
        </div>
      </article>
    `)
    .join("");
}

function populateOrganizationForm() {
  if (!state.organization) return;
  const organization = state.organization;
  const canManage = canManageOrganization();
  document.getElementById("org-name").value = organization.name || "";
  document.getElementById("org-domain").value = organization.domain || "";
  document.getElementById("org-email").value = organization.email || "";
  document.getElementById("org-phone").value = organization.phone || "";
  document.getElementById("org-icon-url").value = organization.icon_url || "";
  document.getElementById("org-brand-color").value = organization.brand_color || "";
  document.getElementById("org-description").value = organization.description || "";
  document.getElementById("org-settings").value = JSON.stringify(organization.settings || {}, null, 2);
  document.getElementById("org-metadata").value = JSON.stringify(organization.metadata || {}, null, 2);
  document.getElementById("organization-form").querySelectorAll("input, textarea, button").forEach((element) => {
    if (element.id === "organization-revoke") {
      element.disabled = !canManage;
      return;
    }
    if (element.id === "organization-save") {
      element.disabled = !canManage;
      return;
    }
    element.disabled = !canManage;
  });
}

function populateSSOForm() {
  const canManage = canManageOrganization();
  const config = state.ssoConfig;
  document.getElementById("sso-issuer-url").value = config?.issuer_url || "";
  document.getElementById("sso-client-id").value = config?.client_id || "";
  document.getElementById("sso-client-secret-ref").value = config?.client_secret_ref || "";
  document.getElementById("sso-allowed-domains").value = (config?.allowed_domains || []).join(", ");
  document.getElementById("sso-scopes").value = (config?.scopes || ["openid", "profile", "email"]).join(", ");
  document.getElementById("sso-active").checked = Boolean(config?.is_active ?? true);
  document.getElementById("sso-enforce").checked = Boolean(config?.enforce_sso ?? false);
  document.getElementById("sso-jit").checked = Boolean(config?.jit_provisioning_enabled ?? true);
  document.getElementById("sso-form").querySelectorAll("input, button").forEach((element) => {
    element.disabled = !canManage;
  });
}

function renderMembers() {
  const list = document.getElementById("member-list");
  if (!list) return;
  if (!state.members.length) {
    list.innerHTML = '<div class="empty-state">No members found.</div>';
    return;
  }
  const canManage = canManageOrganization();
  list.innerHTML = state.members
    .map((member) => `
      <article class="member-card" data-member-id="${escapeHTML(member.user_id)}">
        <div class="card-head">
          <div>
            <h3 class="card-title">${escapeHTML(member.display_name || member.email)}</h3>
            <p class="card-meta">${escapeHTML(member.email)} • Joined ${formatDate(member.joined_at)}</p>
          </div>
          <div class="badge-row">
            <span class="badge">${escapeHTML(member.role)}</span>
            ${member.is_account_owner ? '<span class="badge">Account owner</span>' : ""}
          </div>
        </div>
        <div class="grid-two">
          <div class="field">
            <label>Role</label>
            <select class="input" data-member-role="${escapeHTML(member.user_id)}" ${canManage ? "" : "disabled"}>
              <option value="admin" ${member.role === "admin" ? "selected" : ""}>Admin</option>
              <option value="developer" ${member.role === "developer" ? "selected" : ""}>Developer</option>
              <option value="analyst" ${member.role === "analyst" ? "selected" : ""}>Analyst</option>
            </select>
          </div>
          <label class="field">
            <span>Account owner</span>
            <input type="checkbox" data-member-owner="${escapeHTML(member.user_id)}" ${member.is_account_owner ? "checked" : ""} ${canManage ? "" : "disabled"} />
          </label>
        </div>
        <div class="inline-actions">
          <button class="console-button primary" type="button" data-member-save="${escapeHTML(member.user_id)}" ${canManage ? "" : "disabled"}>Save access</button>
          <button class="console-button" type="button" data-member-sessions="${escapeHTML(member.user_id)}" ${canManage ? "" : "disabled"}>Show sessions</button>
          <button class="console-button danger" type="button" data-member-revoke-sessions="${escapeHTML(member.user_id)}" ${canManage ? "" : "disabled"}>Revoke sessions</button>
          <button class="console-button danger" type="button" data-member-remove="${escapeHTML(member.user_id)}" ${canManage ? "" : "disabled"}>Remove member</button>
        </div>
        <div class="member-sessions hidden" id="member-sessions-${escapeHTML(member.user_id)}"></div>
      </article>
    `)
    .join("");
}

function renderInvitations() {
  const list = document.getElementById("invite-list");
  if (!list) return;
  if (!canManageOrganization()) {
    list.innerHTML = '<div class="empty-state">Only organization admins can manage invitations.</div>';
    return;
  }
  if (!state.invitations.length) {
    list.innerHTML = '<div class="empty-state">No invitations yet.</div>';
    return;
  }
  list.innerHTML = state.invitations
    .map((invitation) => `
      <article class="invite-card">
        <div class="card-head">
          <div>
            <h3 class="card-title">${escapeHTML(invitation.email)}</h3>
            <p class="card-meta">Role ${escapeHTML(invitation.role)} • Expires ${formatDate(invitation.expires_at)}</p>
          </div>
          <div class="badge-row">
            <span class="badge">${escapeHTML(invitation.status)}</span>
            ${invitation.is_account_owner ? '<span class="badge">Account owner</span>' : ""}
          </div>
        </div>
        <div class="inline-actions">
          <button class="console-button danger" type="button" data-invitation-revoke="${escapeHTML(invitation.invitation_id)}">
            Revoke
          </button>
        </div>
      </article>
    `)
    .join("");
}

function updateInvitationFormState() {
  const form = document.getElementById("invitation-form");
  const note = document.getElementById("invite-form-note");
  if (!form || !note) return;
  const canManage = canManageOrganization();
  form.querySelectorAll("input, select, button").forEach((element) => {
    element.disabled = !canManage;
  });
  note.classList.toggle("hidden", canManage);
}

async function loadMe() {
  state.me = await requestJSON("/auth/me");
  populateProfileForm();
}

async function loadIdentities() {
  state.identities = await requestJSON("/auth/external-identities");
  renderIdentities();
}

async function loadSessions() {
  state.sessions = await requestJSON("/auth/sessions");
  renderSessions();
}

async function loadOrganization() {
  state.organization = await requestJSON("/organization");
  populateOrganizationForm();
  updateInvitationFormState();
}

async function loadSSOConfig() {
  if (!canManageOrganization()) {
    state.ssoConfig = null;
    populateSSOForm();
    return;
  }
  state.ssoConfig = await requestJSON("/auth/sso/config");
  populateSSOForm();
}

async function loadMembers() {
  state.members = await requestJSON("/organization/members");
  renderMembers();
}

async function loadInvitations() {
  updateInvitationFormState();
  if (!canManageOrganization()) {
    state.invitations = [];
    renderInvitations();
    return;
  }
  state.invitations = await requestJSON("/organization/invitations");
  renderInvitations();
}

async function bootstrap() {
  showBanner(null, "info");
  await loadMe();
  await loadIdentities();
  updateSummary();
  await Promise.all([loadSessions(), loadOrganization(), loadMembers()]);
  await loadSSOConfig();
  await loadInvitations();
  showSection(currentSection());
}

document.getElementById("profile-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const preferences = parseJSONObject(
      document.getElementById("profile-preferences").value,
      "Profile preferences"
    );
    state.me = await requestJSON("/auth/me", {
      method: "PATCH",
      body: JSON.stringify({
        display_name: document.getElementById("profile-display-name").value.trim() || null,
        avatar_url: document.getElementById("profile-avatar-url").value.trim() || null,
        timezone: document.getElementById("profile-timezone").value.trim() || null,
        language: document.getElementById("profile-language").value.trim() || null,
        preferences,
      }),
    });
    populateProfileForm();
    updateSummary();
    showBanner("Profile updated.", "success");
  } catch (error) {
    showBanner(error instanceof Error ? error.message : "Failed to save profile.", "error");
  }
});

document.getElementById("organization-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const settings = parseJSONObject(
      document.getElementById("org-settings").value,
      "Organization settings"
    );
    const metadata = parseJSONObject(
      document.getElementById("org-metadata").value,
      "Organization metadata"
    );
    state.organization = await requestJSON("/organization", {
      method: "PATCH",
      body: JSON.stringify({
        name: document.getElementById("org-name").value.trim() || null,
        domain: document.getElementById("org-domain").value.trim() || null,
        email: document.getElementById("org-email").value.trim() || null,
        phone: document.getElementById("org-phone").value.trim() || null,
        icon_url: document.getElementById("org-icon-url").value.trim() || null,
        description: document.getElementById("org-description").value.trim() || null,
        brand_color: document.getElementById("org-brand-color").value.trim() || null,
        settings,
        metadata,
      }),
    });
    state.me.organization = { ...state.me.organization, ...state.organization };
    populateOrganizationForm();
    updateSummary();
    showBanner("Organization settings updated.", "success");
  } catch (error) {
    showBanner(error instanceof Error ? error.message : "Failed to save organization settings.", "error");
  }
});

document.getElementById("organization-revoke")?.addEventListener("click", async () => {
  try {
    const response = await requestJSON("/organization/auth/revoke-sessions", { method: "POST" });
    showBanner("Organization sessions revoked at " + formatDate(response.auth_revoked_after), "success");
  } catch (error) {
    showBanner(error instanceof Error ? error.message : "Failed to revoke organization sessions.", "error");
  }
});

document.getElementById("sso-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    state.ssoConfig = await requestJSON("/auth/sso/config", {
      method: "PUT",
      body: JSON.stringify({
        issuer_url: document.getElementById("sso-issuer-url").value.trim(),
        client_id: document.getElementById("sso-client-id").value.trim(),
        client_secret_ref: document.getElementById("sso-client-secret-ref").value.trim(),
        allowed_domains: document.getElementById("sso-allowed-domains").value.split(",").map((item) => item.trim()).filter(Boolean),
        scopes: document.getElementById("sso-scopes").value.split(",").map((item) => item.trim()).filter(Boolean),
        is_active: document.getElementById("sso-active").checked,
        enforce_sso: document.getElementById("sso-enforce").checked,
        jit_provisioning_enabled: document.getElementById("sso-jit").checked,
      }),
    });
    populateSSOForm();
    showBanner("SSO configuration saved.", "success");
  } catch (error) {
    showBanner(error instanceof Error ? error.message : "Failed to save SSO configuration.", "error");
  }
});

document.getElementById("sso-disable")?.addEventListener("click", async () => {
  try {
    await requestJSON("/auth/sso/config", { method: "DELETE" });
    state.ssoConfig = null;
    populateSSOForm();
    showBanner("SSO configuration disabled.", "success");
  } catch (error) {
    showBanner(error instanceof Error ? error.message : "Failed to disable SSO.", "error");
  }
});

document.getElementById("invitation-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await requestJSON("/organization/invitations", {
      method: "POST",
      body: JSON.stringify({
        email: document.getElementById("invite-email").value.trim(),
        role: document.getElementById("invite-role").value,
        is_account_owner: document.getElementById("invite-owner").checked,
      }),
    });
    document.getElementById("invitation-form").reset();
    await loadInvitations();
    showBanner("Invitation created and emailed.", "success");
  } catch (error) {
    showBanner(error instanceof Error ? error.message : "Failed to create invitation.", "error");
  }
});

document.getElementById("refresh-sessions")?.addEventListener("click", async () => {
  await loadSessions();
  showBanner("Sessions refreshed.", "info");
});

document.getElementById("refresh-members")?.addEventListener("click", async () => {
  await loadMembers();
  showBanner("Members refreshed.", "info");
});

document.getElementById("logout-button")?.addEventListener("click", async () => {
  try {
    await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
  } finally {
    window.location.assign("/login");
  }
});

document.body.addEventListener("click", async (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;

  const revokeSessionId = target.getAttribute("data-revoke-session");
  if (revokeSessionId) {
    const isCurrent = state.sessions.some((item) => item.session_id === revokeSessionId && item.is_current);
    try {
      await fetch(
        isCurrent ? "/auth/sessions/current" : "/auth/sessions/" + encodeURIComponent(revokeSessionId),
        { method: "DELETE", credentials: "same-origin" }
      );
      if (isCurrent) {
        window.location.assign("/login");
        return;
      }
      await loadSessions();
      showBanner("Session revoked.", "success");
    } catch (_error) {
      showBanner("Failed to revoke session.", "error");
    }
    return;
  }

  const inviteId = target.getAttribute("data-invitation-revoke");
  if (inviteId) {
    try {
      await fetch("/organization/invitations/" + encodeURIComponent(inviteId), {
        method: "DELETE",
        credentials: "same-origin",
      });
      await loadInvitations();
      showBanner("Invitation revoked.", "success");
    } catch (_error) {
      showBanner("Failed to revoke invitation.", "error");
    }
    return;
  }

  const memberId = target.getAttribute("data-member-save");
  if (memberId) {
    try {
      await requestJSON("/organization/members/" + encodeURIComponent(memberId), {
        method: "PATCH",
        body: JSON.stringify({
          role: document.querySelector('[data-member-role="' + CSS.escape(memberId) + '"]').value,
          is_account_owner: document.querySelector('[data-member-owner="' + CSS.escape(memberId) + '"]').checked,
        }),
      });
      await loadMembers();
      showBanner("Member access updated.", "success");
    } catch (error) {
      showBanner(error instanceof Error ? error.message : "Failed to update member access.", "error");
    }
    return;
  }

  const memberSessionsId = target.getAttribute("data-member-sessions");
  if (memberSessionsId) {
    try {
      const sessions = await requestJSON("/organization/members/" + encodeURIComponent(memberSessionsId) + "/sessions");
      const shell = document.getElementById("member-sessions-" + memberSessionsId);
      if (shell) {
        shell.classList.remove("hidden");
        shell.innerHTML = sessions.length
          ? sessions.map((session) => '<div class="session-inline">' + escapeHTML(session.user_agent || "Unknown device") + ' • ' + escapeHTML(formatDate(session.last_seen_at || session.issued_at)) + '</div>').join("")
          : '<div class="empty-state">No active sessions.</div>';
      }
    } catch (error) {
      showBanner(error instanceof Error ? error.message : "Failed to load member sessions.", "error");
    }
    return;
  }

  const memberRevokeId = target.getAttribute("data-member-revoke-sessions");
  if (memberRevokeId) {
    try {
      await fetch("/organization/members/" + encodeURIComponent(memberRevokeId) + "/sessions", {
        method: "DELETE",
        credentials: "same-origin",
      });
      showBanner("Member sessions revoked.", "success");
    } catch (_error) {
      showBanner("Failed to revoke member sessions.", "error");
    }
    return;
  }

  const memberRemoveId = target.getAttribute("data-member-remove");
  if (memberRemoveId) {
    try {
      await fetch("/organization/members/" + encodeURIComponent(memberRemoveId), {
        method: "DELETE",
        credentials: "same-origin",
      });
      await loadMembers();
      showBanner("Member removed from organization.", "success");
    } catch (_error) {
      showBanner("Failed to remove member.", "error");
    }
  }
});

window.addEventListener("hashchange", () => showSection(currentSection()));
bootstrap().catch((error) => {
  showBanner(error instanceof Error ? error.message : "Failed to load workspace.", "error");
});
"""
    return _console_base_html(
        title="Ruhu AI Workspace",
        body_html=body,
        script=_COMMON_SCRIPT.replace("__SPINNER__", json.dumps(_SPINNER_ICON)) + script,
    )


def internal_admin_page_html() -> str:
    body = f"""
<main class="console-page">
  <div class="console-shell">
    <header class="console-header">
      <div class="console-brand">
        <h1>Ruhu Internal Admin</h1>
        <p>Platform diagnostics, tenant inspection, and superuser controls.</p>
      </div>
      <div class="console-actions">
        <a class="console-button linkish" href="/app">Back to workspace</a>
      </div>
    </header>

    <div id="admin-banner" class="console-banner info hidden" role="status"></div>

    <section class="console-panel">
      <h2>Platform Health</h2>
      <p>Current auth, email, and database health for the running service.</p>
      <div class="admin-list" id="admin-health"></div>
    </section>

    <section class="console-panel">
      <h2>Auth Diagnostics</h2>
      <p>Safe signing diagnostics for the live JWT configuration.</p>
      <div class="admin-list" id="admin-auth-diagnostics"></div>
    </section>

    <section class="console-panel">
      <h2>Classifier Diagnostics</h2>
      <p>Hosted classifier settings, adapter activity, fallback counts, and recent cost telemetry.</p>
      <div class="admin-list" id="admin-classifier-diagnostics"></div>
    </section>

    <section class="console-panel">
      <h2>Organizations</h2>
      <div class="admin-list" id="admin-organizations"></div>
    </section>

    <section class="console-panel">
      <h2>Users</h2>
      <div class="admin-list" id="admin-users"></div>
    </section>
  </div>
</main>
"""
    script = """
const adminBanner = document.getElementById("admin-banner");

async function adminRequest(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
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
  if (response.status === 403) {
    window.location.assign("/app");
    throw new Error("internal admin access required");
  }
  if (!response.ok) {
    throw new Error(parseErrorMessage(payload, "Request failed"));
  }
  return payload;
}

function showAdminBanner(message, tone) {
  if (!message) {
    adminBanner.textContent = "";
    adminBanner.className = "console-banner info hidden";
    return;
  }
  adminBanner.textContent = message;
  adminBanner.className = "console-banner " + tone;
}

function formatAdminValue(value) {
  if (Array.isArray(value)) return value.join(", ");
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function renderObjectPanel(containerId, objectValue) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const entries = Object.entries(objectValue || {});
  container.innerHTML = entries.map(([key, value]) => `
    <article class="admin-card">
      <div class="card-head">
        <div>
          <h3 class="card-title">${escapeHTML(key)}</h3>
          <p class="card-meta">${escapeHTML(formatAdminValue(value))}</p>
        </div>
      </div>
    </article>
  `).join("");
}

function renderOrganizations(items) {
  const container = document.getElementById("admin-organizations");
  if (!container) return;
  if (!items.length) {
    container.innerHTML = '<div class="empty-state">No organizations found.</div>';
    return;
  }
  container.innerHTML = items.map((item) => `
    <article class="admin-card">
      <div class="card-head">
        <div>
          <h3 class="card-title">${escapeHTML(item.name)}</h3>
          <p class="card-meta">${escapeHTML(item.slug)} • ${item.member_count} members • Created ${new Date(item.created_at).toLocaleString()}</p>
        </div>
        <div class="badge-row">
          <span class="badge">${item.is_active ? "active" : "inactive"}</span>
        </div>
      </div>
    </article>
  `).join("");
}

function renderUsers(items) {
  const container = document.getElementById("admin-users");
  if (!container) return;
  if (!items.length) {
    container.innerHTML = '<div class="empty-state">No users found.</div>';
    return;
  }
  container.innerHTML = items.map((item) => `
    <article class="admin-card">
      <div class="card-head">
        <div>
          <h3 class="card-title">${escapeHTML(item.display_name || item.email)}</h3>
          <p class="card-meta">${escapeHTML(item.email)} • Last login ${escapeHTML(item.last_login_at || "Never")}</p>
        </div>
        <div class="badge-row">
          ${item.is_superuser ? '<span class="badge">Superuser</span>' : ""}
          <span class="badge">${item.is_active ? "active" : "inactive"}</span>
        </div>
      </div>
      <div class="inline-actions">
        <button class="console-button" type="button" data-user-identities="${escapeHTML(item.user_id)}">Linked identities</button>
        <button class="console-button primary" type="button" data-promote-superuser="${escapeHTML(item.user_id)}">Promote</button>
        <button class="console-button danger" type="button" data-revoke-superuser="${escapeHTML(item.user_id)}">Revoke</button>
      </div>
      <div class="member-sessions hidden" id="user-identities-${escapeHTML(item.user_id)}"></div>
    </article>
  `).join("");
}

async function loadAdmin() {
  const [health, diagnostics, classifierDiagnostics, organizations, users] = await Promise.all([
    adminRequest("/internal/platform/health"),
    adminRequest("/internal/auth/diagnostics"),
    adminRequest("/internal/intent-tags/classifier/diagnostics"),
    adminRequest("/internal/organizations"),
    adminRequest("/internal/users"),
  ]);
  renderObjectPanel("admin-health", health);
  renderObjectPanel("admin-auth-diagnostics", diagnostics);
  renderObjectPanel("admin-classifier-diagnostics", classifierDiagnostics);
  renderOrganizations(organizations);
  renderUsers(users);
}

document.body.addEventListener("click", async (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const promoteId = target.getAttribute("data-promote-superuser");
  const identitiesId = target.getAttribute("data-user-identities");
  if (identitiesId) {
    try {
      const identities = await adminRequest("/internal/users/" + encodeURIComponent(identitiesId) + "/external-identities");
      const shell = document.getElementById("user-identities-" + identitiesId);
      if (shell) {
        shell.classList.remove("hidden");
        shell.innerHTML = identities.length
          ? identities.map((identity) => '<div class="session-inline">' + escapeHTML(identity.provider_type) + ' • ' + escapeHTML(identity.email || identity.subject || "unknown") + '</div>').join("")
          : '<div class="empty-state">No linked external identities.</div>';
      }
    } catch (error) {
      showAdminBanner(error instanceof Error ? error.message : "Failed to load linked identities.", "error");
    }
    return;
  }
  if (promoteId) {
    try {
      await adminRequest("/internal/users/" + encodeURIComponent(promoteId) + "/promote-superuser", { method: "POST" });
      await loadAdmin();
      showAdminBanner("User promoted to superuser.", "success");
    } catch (error) {
      showAdminBanner(error instanceof Error ? error.message : "Failed to promote user.", "error");
    }
    return;
  }
  const revokeId = target.getAttribute("data-revoke-superuser");
  if (revokeId) {
    try {
      await adminRequest("/internal/users/" + encodeURIComponent(revokeId) + "/revoke-superuser", { method: "POST" });
      await loadAdmin();
      showAdminBanner("Superuser access revoked.", "success");
    } catch (error) {
      showAdminBanner(error instanceof Error ? error.message : "Failed to revoke superuser.", "error");
    }
  }
});

loadAdmin().catch((error) => {
  showAdminBanner(error instanceof Error ? error.message : "Failed to load internal admin console.", "error");
});
"""
    return _console_base_html(
        title="Ruhu Internal Admin",
        body_html=body,
        script=_COMMON_SCRIPT.replace("__SPINNER__", json.dumps(_SPINNER_ICON)) + script,
    )
