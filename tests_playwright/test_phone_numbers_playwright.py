from __future__ import annotations

from playwright.sync_api import expect


def test_phone_number_dashboard_browser_reconciles_binding_and_shows_audit(
    page,
    ticket_browser_harness,
) -> None:
    harness = ticket_browser_harness
    harness.seed_organization()
    admin = harness.save_user(
        user_id="phone-admin",
        email="phone-admin@example.com",
        display_name="Phone Admin",
    )
    harness.add_membership(
        user_id=admin.user_id,
        role="admin",
        is_account_owner=True,
    )

    with harness.authorized_client(user_id=admin.user_id) as client:
        created_number = client.post(
            "/phone-numbers",
            json={
                "e164_number": "+2348012345678",
                "display_name": "Nigeria support line",
            },
        )
        assert created_number.status_code == 201
        phone_number_id = created_number.json()["phone_number_id"]

        created_binding = client.post(
            f"/phone-numbers/{phone_number_id}/bindings",
            json={
                "channel": "phone",
                "provider": "africastalking",
                "provider_resource_id": "+2348012345678",
                "verification_status": "verified",
                "health_status": "healthy",
                "transport_metadata": {
                    "africastalking": {
                        "provider_resource_id": "+2348012345678",
                        "phone_number": "+2348012345678",
                    }
                },
            },
        )
        assert created_binding.status_code == 201

    harness.add_browser_session(page, user_id=admin.user_id)

    page.goto(f"{harness.base_url}/dashboard")
    page.get_by_role("link", name="Phone Numbers").click()
    expect(page.get_by_text("Operator Actions")).to_be_visible()
    expect(page.get_by_role("heading", name="Nigeria support line")).to_be_visible()
    expect(page.get_by_role("button", name="Reconcile This Number")).to_be_visible()

    page.get_by_role("button", name="Reconcile This Number").click()

    expect(page.get_by_text("Audit Trail")).to_be_visible()
    expect(page.get_by_text("Phone binding reconciliation updated africastalking state")).to_be_visible()
    expect(page.get_by_text("manual required").first).to_be_visible()
    expect(page.get_by_text("misconfigured").first).to_be_visible()
