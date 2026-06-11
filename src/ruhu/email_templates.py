from __future__ import annotations

import html as _html_lib
from dataclasses import dataclass

from .ui_theme import BACKGROUND_HEX, BORDER_HEX, MUTED_TEXT_HEX, PRIMARY_HEX, PRIMARY_HEX_HOVER, SURFACE_HEX, TEXT_HEX


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str
    html: str
    text: str


_URGENCY_BADGE_COLOR = {
    "critical": "#DC2626",  # red
    "action_required": "#D97706",  # amber
    "fyi": "#6B7280",  # gray
}


def render_notification_email(
    *,
    to_email: str,
    title: str,
    message: str,
    url: str | None,
    url_label: str,
    level: str,
    urgency: str,
) -> RenderedEmail:
    """Email rendition of an in-app notification.

    Used for notifications whose urgency triggers email delivery
    (typically ``critical`` or ``action_required``). Subject and body are
    derived from the notification's title + message; the call-to-action
    button is rendered only when ``url`` is provided.
    """
    safe_title = _html_lib.escape(title)
    safe_message = _html_lib.escape(message).replace("\n", "<br>")
    safe_url_label = _html_lib.escape(url_label)
    badge_color = _URGENCY_BADGE_COLOR.get(urgency, _URGENCY_BADGE_COLOR["fyi"])
    badge_label = _html_lib.escape(urgency.replace("_", " ").upper())

    cta_html = ""
    cta_text = ""
    if url:
        cta_html = f"""
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td align="center" style="padding:28px 0 8px;">
                    <a href="{_html_lib.escape(url, quote=True)}"
                       style="display:inline-block;background-color:{PRIMARY_HEX};color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;padding:14px 36px;border-radius:8px;">
                      {safe_url_label}
                    </a>
                  </td>
                </tr>
              </table>
              <p style="margin:0;font-size:13px;color:{MUTED_TEXT_HEX};text-align:center;">
                Or copy and paste this link:<br>
                <a href="{_html_lib.escape(url, quote=True)}" style="color:{PRIMARY_HEX};word-break:break-all;">{_html_lib.escape(url)}</a>
              </p>
"""
        cta_text = f"\n\n{url_label}: {url}"

    subject = title
    html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{safe_title}</title>
</head>
<body style="margin:0;padding:0;background-color:{BACKGROUND_HEX};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:{BACKGROUND_HEX};padding:40px 16px;">
    <tr>
      <td align="center">
        <table width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;">
          <tr>
            <td align="center" style="padding-bottom:24px;">
              <span style="font-size:28px;font-weight:700;color:{PRIMARY_HEX};letter-spacing:-0.5px;">Ruhu AI</span>
            </td>
          </tr>
          <tr>
            <td style="background:{SURFACE_HEX};border-radius:12px;padding:40px 48px;border:1px solid {BORDER_HEX};">
              <p style="margin:0 0 16px;text-align:center;">
                <span style="display:inline-block;padding:4px 12px;background-color:{badge_color};color:#ffffff;border-radius:4px;font-size:11px;font-weight:700;letter-spacing:0.05em;">
                  {badge_label}
                </span>
              </p>
              <h2 style="margin:0 0 16px;font-size:22px;font-weight:700;color:{TEXT_HEX};text-align:center;">
                {safe_title}
              </h2>
              <p style="margin:0;font-size:15px;color:#374151;line-height:1.6;text-align:center;">
                {safe_message}
              </p>
              {cta_html}
            </td>
          </tr>
          <tr>
            <td align="center" style="padding-top:24px;font-size:13px;color:#9ca3af;">
              <p style="margin:0;">You're receiving this because of your notification settings on Ruhu AI.</p>
              <p style="margin:8px 0 0;">© 2026 Ruhu, Inc. All rights reserved.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
""".strip()
    text = f"[{urgency.replace('_', ' ').upper()}] {title}\n\n{message}{cta_text}\n\n© 2026 Ruhu, Inc.".strip()
    assert to_email
    return RenderedEmail(subject=subject, html=html, text=text)


def render_organization_invitation_email(
    *,
    to_email: str,
    invited_by_name: str,
    organization_name: str,
    invitation_url: str,
    role: str,
) -> RenderedEmail:
    subject = f"{invited_by_name} invited you to join {organization_name} on Ruhu AI"
    html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Team Invitation</title>
</head>
<body style="margin:0;padding:0;background-color:{BACKGROUND_HEX};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:{BACKGROUND_HEX};padding:40px 16px;">
    <tr>
      <td align="center">
        <table width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;">
          <tr>
            <td align="center" style="padding-bottom:24px;">
              <span style="font-size:28px;font-weight:700;color:{PRIMARY_HEX};letter-spacing:-0.5px;">Ruhu AI</span>
            </td>
          </tr>
          <tr>
            <td style="background:{SURFACE_HEX};border-radius:12px;padding:40px 48px;border:1px solid {BORDER_HEX};">
              <h2 style="margin:0 0 16px;font-size:22px;font-weight:700;color:{TEXT_HEX};text-align:center;">
                You've been invited!
              </h2>
              <p style="margin:0 0 8px;font-size:15px;color:#374151;text-align:center;">
                <strong>{invited_by_name}</strong> has invited you to join
                <strong>{organization_name}</strong> on Ruhu AI Voice Agent Platform.
              </p>
              <p style="margin:20px 0 0;font-size:15px;color:#374151;text-align:center;">
                You will be added as a
                <span style="display:inline-block;padding:4px 12px;background-color:#FEF3EE;color:{PRIMARY_HEX};border-radius:4px;font-size:14px;font-weight:600;">
                  {role.upper()}
                </span>
              </p>
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td align="center" style="padding:28px 0 20px;">
                    <a href="{invitation_url}"
                       style="display:inline-block;background-color:{PRIMARY_HEX};color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;padding:14px 36px;border-radius:8px;">
                      Accept Invitation
                    </a>
                  </td>
                </tr>
              </table>
              <p style="margin:0;font-size:13px;color:{MUTED_TEXT_HEX};text-align:center;">
                Or copy and paste this link into your browser:<br>
                <a href="{invitation_url}" style="color:{PRIMARY_HEX};word-break:break-all;">{invitation_url}</a>
              </p>
              <p style="margin:20px 0 0;font-size:13px;color:{MUTED_TEXT_HEX};text-align:center;">
                This invitation will expire in 7 days.
              </p>
            </td>
          </tr>
          <tr>
            <td align="center" style="padding-top:24px;font-size:13px;color:#9ca3af;">
              <p style="margin:0;">If you didn't expect this invitation, you can safely ignore this email.</p>
              <p style="margin:8px 0 0;">© 2026 Ruhu, Inc. All rights reserved.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
""".strip()
    text = f"""
You've been invited to join {organization_name} on Ruhu AI!

{invited_by_name} has invited you to join their team as a {role}.

Accept your invitation by visiting this link:
{invitation_url}

This invitation will expire in 7 days.

If you didn't expect this invitation, you can safely ignore this email.

© 2026 Ruhu, Inc. All rights reserved.
""".strip()
    assert to_email
    return RenderedEmail(subject=subject, html=html, text=text)


def render_magic_link_email(
    *,
    to_email: str,
    magic_link_url: str,
) -> RenderedEmail:
    subject = "Your Ruhu AI sign-in link"
    html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sign in to Ruhu AI</title>
</head>
<body style="margin:0;padding:0;background-color:{BACKGROUND_HEX};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:{BACKGROUND_HEX};padding:40px 16px;">
    <tr>
      <td align="center">
        <table width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;">
          <tr>
            <td align="center" style="padding-bottom:24px;">
              <span style="font-size:28px;font-weight:700;color:{PRIMARY_HEX};letter-spacing:-0.5px;">Ruhu AI</span>
            </td>
          </tr>
          <tr>
            <td style="background:{SURFACE_HEX};border-radius:12px;padding:40px 48px;border:1px solid {BORDER_HEX};">
              <p style="margin:0 0 24px;font-size:15px;color:#374151;text-align:center;">
                Click the button below to sign in to Ruhu. This link expires in 15 minutes and can only be used once.
              </p>
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td align="center" style="padding-bottom:28px;">
                    <a href="{magic_link_url}"
                       style="display:inline-block;background-color:{PRIMARY_HEX};color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;padding:14px 36px;border-radius:8px;">
                      Sign in to Ruhu
                    </a>
                  </td>
                </tr>
              </table>
              <p style="margin:0;font-size:13px;color:{MUTED_TEXT_HEX};text-align:center;">
                Or copy and paste this link:<br>
                <a href="{magic_link_url}" style="color:{PRIMARY_HEX};word-break:break-all;">{magic_link_url}</a>
              </p>
            </td>
          </tr>
          <tr>
            <td align="center" style="padding-top:24px;font-size:13px;color:#9ca3af;">
              <p style="margin:0;">If you didn't request this link, you can safely ignore this email.</p>
              <p style="margin:8px 0 0;">© 2026 Ruhu, Inc. All rights reserved.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
""".strip()
    text = f"""
Sign in to Ruhu AI

Click this link to sign in (expires in 15 minutes):
{magic_link_url}

If you didn't request this, ignore this email.
""".strip()
    assert to_email
    return RenderedEmail(subject=subject, html=html, text=text)


def invitation_button_hover_color() -> str:
    return PRIMARY_HEX_HOVER


def render_close_account_email(
    *,
    to_email: str,
    organization_name: str,
    confirm_url: str,
) -> RenderedEmail:
    subject = f"Confirm account closure for {organization_name}"
    html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Confirm account closure</title>
</head>
<body style="margin:0;padding:0;background:{BACKGROUND_HEX};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:{BACKGROUND_HEX};padding:40px 16px;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="background:{SURFACE_HEX};border:1px solid {BORDER_HEX};border-radius:12px;padding:40px;">
        <tr><td style="padding-bottom:24px;">
          <h1 style="margin:0;font-size:22px;font-weight:600;color:{TEXT_HEX};">Confirm account closure</h1>
        </td></tr>
        <tr><td style="color:{MUTED_TEXT_HEX};font-size:14px;line-height:1.6;padding-bottom:20px;">
          You requested to close the <strong style="color:{TEXT_HEX};">{organization_name}</strong> account.<br><br>
          Clicking the button below will schedule your account for permanent deletion in <strong style="color:{TEXT_HEX};">30 days</strong>.
          During that window you can reactivate at any time.
        </td></tr>
        <tr><td style="padding-bottom:20px;">
          <a href="{confirm_url}" style="display:inline-block;padding:12px 24px;background:#DC2626;color:#ffffff;font-size:14px;font-weight:600;text-decoration:none;border-radius:8px;">Confirm closure</a>
        </td></tr>
        <tr><td style="color:{MUTED_TEXT_HEX};font-size:12px;">
          This link expires in 15 minutes. If you did not request this, ignore this email — no action will be taken.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
""".strip()
    text = f"""
Confirm account closure for {organization_name}

You requested to close this account. Click the link below to confirm (expires in 15 minutes):
{confirm_url}

During a 30-day grace period you can reactivate at any time.
If you did not request this, ignore this email.
""".strip()
    assert to_email
    return RenderedEmail(subject=subject, html=html, text=text)


def render_reactivate_account_email(
    *,
    to_email: str,
    organization_name: str,
    confirm_url: str,
) -> RenderedEmail:
    subject = f"Confirm reactivation for {organization_name}"
    html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Confirm reactivation</title>
</head>
<body style="margin:0;padding:0;background:{BACKGROUND_HEX};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:{BACKGROUND_HEX};padding:40px 16px;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="background:{SURFACE_HEX};border:1px solid {BORDER_HEX};border-radius:12px;padding:40px;">
        <tr><td style="padding-bottom:24px;">
          <h1 style="margin:0;font-size:22px;font-weight:600;color:{TEXT_HEX};">Confirm reactivation</h1>
        </td></tr>
        <tr><td style="color:{MUTED_TEXT_HEX};font-size:14px;line-height:1.6;padding-bottom:20px;">
          You requested to reactivate <strong style="color:{TEXT_HEX};">{organization_name}</strong>.<br><br>
          Clicking the button below will cancel the scheduled deletion and restore your account to full access.
        </td></tr>
        <tr><td style="padding-bottom:20px;">
          <a href="{confirm_url}" style="display:inline-block;padding:12px 24px;background:{PRIMARY_HEX};color:#ffffff;font-size:14px;font-weight:600;text-decoration:none;border-radius:8px;">Confirm reactivation</a>
        </td></tr>
        <tr><td style="color:{MUTED_TEXT_HEX};font-size:12px;">
          This link expires in 15 minutes. If you did not request this, ignore this email.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
""".strip()
    text = f"""
Confirm reactivation for {organization_name}

Click the link below to confirm reactivation (expires in 15 minutes):
{confirm_url}

This will cancel the scheduled deletion and restore your account.
If you did not request this, ignore this email.
""".strip()
    assert to_email
    return RenderedEmail(subject=subject, html=html, text=text)
