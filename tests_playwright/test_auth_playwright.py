from __future__ import annotations

from playwright.sync_api import expect

from ruhu.identity import ExternalIdentity


def test_invitation_magic_link_browser_journey_reaches_workspace_and_logout(page, auth_browser_harness) -> None:
    harness = auth_browser_harness
    harness.seed_organization()
    inviter = harness.save_user(
        user_id="user-admin",
        email="admin@example.com",
        display_name="Admin",
    )
    harness.add_membership(
        user_id=inviter.user_id,
        role="admin",
        is_account_owner=True,
    )
    issued = harness.auth_service.create_organization_invitation(
        organization_id="org-1",
        email="invitee@example.com",
        role="developer",
        invited_by_user_id=inviter.user_id,
        is_account_owner=False,
    )

    page.goto(f"{harness.base_url}/accept-invitation?token={issued.invitation_token}")
    expect(page.locator("#accept-title")).to_have_text("Join Acme Voice")
    expect(page.locator("#accept-email")).to_have_text("invitee@example.com")
    page.click("#accept-magic")
    expect(page.locator("#accept-sent")).to_be_visible()

    magic_link_token = harness.extract_dev_outbox_token(path="/auth/magic-link")
    page.goto(f"{harness.base_url}/auth/magic-link?token={magic_link_token}")
    page.wait_for_url(f"{harness.base_url}/app")
    expect(page.locator("#summary-organization")).to_have_text("Acme Voice")
    expect(page.locator("#profile-email")).to_have_value("invitee@example.com")

    page.click("#logout-button")
    page.wait_for_url(f"{harness.base_url}/login")
    expect(page.locator("#card-title")).to_have_text("Log in to Ruhu AI")


def test_google_invitation_browser_journey_reaches_workspace(page, auth_browser_harness) -> None:
    harness = auth_browser_harness
    harness.seed_organization()
    inviter = harness.save_user(
        user_id="user-admin",
        email="admin@example.com",
        display_name="Admin",
    )
    harness.add_membership(
        user_id=inviter.user_id,
        role="admin",
        is_account_owner=True,
    )
    issued = harness.auth_service.create_organization_invitation(
        organization_id="org-1",
        email="googleinvite@example.com",
        role="developer",
        invited_by_user_id=inviter.user_id,
        is_account_owner=False,
    )

    page.goto(f"{harness.base_url}/accept-invitation?token={issued.invitation_token}")
    expect(page.locator("#accept-google")).to_be_visible()
    page.click("#accept-google")
    page.wait_for_url(f"{harness.base_url}/app")
    expect(page.locator("#profile-email")).to_have_value("googleinvite@example.com")
    expect(page.locator("#identity-list")).to_contain_text("google")


def test_sso_login_browser_journey_jit_provisions_user(page, auth_browser_harness) -> None:
    harness = auth_browser_harness
    harness.seed_organization()
    harness.auth_service.save_enterprise_sso_configuration(
        organization_id="org-1",
        issuer_url="https://sso.acme.com",
        client_id="acme-client-id",
        client_secret_ref="env:ACME_SSO_SECRET",
        allowed_domains=["acme.com"],
        scopes=["openid", "profile", "email"],
        is_active=True,
        enforce_sso=True,
        jit_provisioning_enabled=True,
    )

    page.goto(f"{harness.base_url}/login")
    page.click("#sso-button")
    page.fill("#sso-email", "analyst@acme.com")
    page.click("#sso-submit")
    page.wait_for_url(f"{harness.base_url}/app")
    expect(page.locator("#profile-email")).to_have_value("analyst@acme.com")
    expect(page.locator("#summary-role")).to_contain_text("analyst")


def test_workspace_browser_journey_updates_profile_org_and_invitations(page, auth_browser_harness) -> None:
    harness = auth_browser_harness
    harness.seed_organization()
    admin = harness.save_user(
        user_id="user-admin",
        email="admin@example.com",
        display_name="Admin",
    )
    harness.add_membership(
        user_id=admin.user_id,
        role="admin",
        is_account_owner=True,
    )
    member = harness.save_user(
        user_id="user-member",
        email="member@example.com",
        display_name="Member",
    )
    harness.add_membership(user_id=member.user_id, role="developer")
    harness.add_browser_session(page, user_id=admin.user_id)

    page.goto(f"{harness.base_url}/app")
    expect(page.locator("#profile-email")).to_have_value("admin@example.com")

    page.fill("#profile-display-name", "Platform Admin")
    page.fill("#profile-preferences", '{"theme":"warm","landing":"workspace"}')
    page.click("#profile-save")
    expect(page.locator("#console-banner")).to_contain_text("Profile updated.")
    expect(page.locator("#summary-user")).to_have_text("Platform Admin")

    page.click('[data-section-link="organization"]')
    page.fill("#org-name", "Acme Voice Labs")
    page.fill("#org-settings", '{"support_email":"ops@acme.com"}')
    page.fill("#org-metadata", '{"industry":"healthcare"}')
    page.click("#organization-save")
    expect(page.locator("#console-banner")).to_contain_text("Organization settings updated.")
    expect(page.locator("#summary-organization")).to_have_text("Acme Voice Labs")

    page.click('[data-section-link="invitations"]')
    page.fill("#invite-email", "newuser@example.com")
    page.select_option("#invite-role", "analyst")
    page.click("#invite-create")
    expect(page.locator("#console-banner")).to_contain_text("Invitation created and emailed.")
    expect(page.locator("#invite-list")).to_contain_text("newuser@example.com")


def test_internal_admin_browser_journey_shows_identities_and_superuser_controls(page, auth_browser_harness) -> None:
    harness = auth_browser_harness
    harness.seed_organization()
    admin = harness.save_user(
        user_id="user-admin",
        email="admin@example.com",
        display_name="Admin",
        is_superuser=True,
    )
    harness.add_membership(
        user_id=admin.user_id,
        role="admin",
        is_account_owner=True,
    )
    target = harness.save_user(
        user_id="user-target",
        email="analyst@example.com",
        display_name="Analyst",
    )
    harness.add_membership(user_id=target.user_id, role="analyst")
    harness.auth_service.link_external_identity(
        ExternalIdentity(
            user_id=target.user_id,
            organization_id="org-1",
            provider_type="google",
            provider_key="google-oauth",
            subject="subject:analyst@example.com",
            email="analyst@example.com",
        )
    )
    harness.add_browser_session(page, user_id=admin.user_id)

    page.goto(f"{harness.base_url}/internal/admin")
    expect(page.locator("h1")).to_have_text("Ruhu Internal Admin")
    expect(page.locator("#admin-users")).to_contain_text("analyst@example.com")
    target_card = page.locator('[data-user-identities="user-target"]').locator("xpath=ancestor::article[1]")

    page.click('[data-user-identities="user-target"]')
    expect(page.locator("#user-identities-user-target")).to_contain_text("google")
    expect(page.locator("#user-identities-user-target")).to_contain_text("analyst@example.com")

    page.click('[data-promote-superuser="user-target"]')
    expect(page.locator("#admin-banner")).to_contain_text("User promoted to superuser.")
    expect(target_card).to_contain_text("Superuser")

    page.click('[data-revoke-superuser="user-target"]')
    expect(page.locator("#admin-banner")).to_contain_text("Superuser access revoked.")
    expect(target_card).not_to_contain_text("Superuser")
