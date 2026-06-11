from __future__ import annotations

from ruhu.email_templates import render_magic_link_email, render_organization_invitation_email


def test_magic_link_email_uses_old_layout_with_new_theme_accent() -> None:
    rendered = render_magic_link_email(
        to_email="user@example.com",
        magic_link_url="https://app.example.com/auth/magic-link?token=abc123",
    )
    assert rendered.subject == "Your Ruhu AI sign-in link"
    assert "Ruhu AI" in rendered.html
    assert "Sign in to Ruhu" in rendered.html
    assert "#D14118" in rendered.html
    assert "auth/magic-link?token=abc123" in rendered.html
    assert "expires in 15 minutes" in rendered.text


def test_invitation_email_uses_old_invite_structure() -> None:
    rendered = render_organization_invitation_email(
        to_email="invitee@example.com",
        invited_by_name="Ijidai",
        organization_name="Acme Voice",
        invitation_url="https://app.example.com/accept-invitation?token=invite123",
        role="developer",
    )
    assert rendered.subject == "Ijidai invited you to join Acme Voice on Ruhu AI"
    assert "You've been invited!" in rendered.html
    assert "Accept Invitation" in rendered.html
    assert "DEVELOPER" in rendered.html
    assert "#D14118" in rendered.html
    assert "accept-invitation?token=invite123" in rendered.text
