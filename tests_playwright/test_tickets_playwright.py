from __future__ import annotations

from datetime import datetime, timedelta, timezone

from playwright.sync_api import expect

from ruhu.db_models import ConversationRecord, RealtimeEventRecord, RealtimeSessionRecord
from ruhu.ticketing_providers import RemoteCase


def test_ticket_dashboard_browser_journey_support_cases_connections_and_external_cases(
    page,
    ticket_browser_harness,
) -> None:
    harness = ticket_browser_harness
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

    with harness.authorized_client(user_id=admin.user_id) as client:
        started = client.post(
            "/conversations",
            json={
                "agent_id": "sales_agent",
                "channel": "phone",
                "metadata": {
                    "participant_identity": "customer-123",
                    "participant_display": "Customer 123",
                },
            },
        )
        assert started.status_code == 200
        conversation_id = started.json()["conversation"]["conversation_id"]

        turn = client.post(
            f"/conversations/{conversation_id}/turns",
            json={
                "channel": "phone",
                "text": "I need support with a callback issue.",
            },
        )
        assert turn.status_code == 200

    now = datetime.now(timezone.utc)
    with harness.runtime_session_factory.begin() as session:
        record = session.get(ConversationRecord, conversation_id)
        assert record is not None
        record.status = "ended"
        record.outcome = "transferred"
        record.started_at = now - timedelta(minutes=3)
        record.ended_at = now - timedelta(minutes=1)
        record.updated_at = now - timedelta(minutes=1)
        record.metadata_json = {
            **dict(record.metadata_json or {}),
            "participant_display": "Customer 123",
            "participant_identity": "customer-123",
            "summary": "Callback issue requires manual follow-up",
            "sentiment_score": -0.42,
            "tags": ["support", "callback"],
        }
        session.add(
            RealtimeSessionRecord(
                organization_id="org-1",
                realtime_session_id="ticket-rt-session-1",
                parent_realtime_session_id=None,
                conversation_id=conversation_id,
                surface="voice",
                channel="phone",
                modality="audio",
                status="completed",
                provider="livekit",
                external_session_key="lk:ticket-room-1",
                provider_session_id="provider-ticket-session-1",
                participant_identity="customer-123",
                transport_metadata_json={"bridge": "pstn"},
                started_at=now - timedelta(minutes=3),
                last_seen_at=now - timedelta(minutes=2),
                ended_at=now - timedelta(minutes=1),
                created_at=now - timedelta(minutes=3),
                updated_at=now - timedelta(minutes=1),
            )
        )
        session.flush()
        session.add(
            RealtimeEventRecord(
                organization_id="org-1",
                event_id="ticket-rt-event-1",
                conversation_id=conversation_id,
                realtime_session_id="ticket-rt-session-1",
                conversation_sequence=201,
                family="message",
                name="user_accepted",
                causation_id=None,
                correlation_id=None,
                actor_type="user",
                actor_id="customer-123",
                visibility="surface",
                audiences_json=[],
                projection_policy_json={},
                payload_json={"text": "I need support with a callback issue.", "channel": "phone"},
                created_at=now - timedelta(minutes=3),
            )
        )
        session.add(
            RealtimeEventRecord(
                organization_id="org-1",
                event_id="ticket-rt-event-2",
                conversation_id=conversation_id,
                realtime_session_id="ticket-rt-session-1",
                conversation_sequence=202,
                family="message",
                name="assistant_published",
                causation_id=None,
                correlation_id=None,
                actor_type="assistant",
                actor_id="sales_agent",
                visibility="surface",
                audiences_json=[],
                projection_policy_json={},
                payload_json={"text": "I am escalating this so the team can follow up.", "channel": "phone"},
                created_at=now - timedelta(minutes=2, seconds=45),
            )
        )

    harness.add_browser_session(page, user_id=admin.user_id)

    page.goto(f"{harness.base_url}/tickets")
    expect(page.locator("h1")).to_have_text("Tickets")
    expect(page.locator("#tickets-table-body")).to_contain_text("Customer 123")

    page.locator(f'[data-conversation-id="{conversation_id}"]').click()
    expect(page.locator("#detail-title")).to_contain_text("Callback issue requires manual follow-up")
    expect(page.locator("#detail-transcript")).to_contain_text("callback issue")
    expect(page.locator("#detail-evidence")).to_contain_text("voice / phone")

    page.fill("#case-form-title", "Follow up callback issue")
    page.fill("#case-form-description", "Customer needs manual callback follow-up.")
    page.fill("#case-form-category", "callback")
    page.click("#support-case-create")
    expect(page.locator("#tickets-banner")).to_contain_text("Support case created.")
    expect(page.locator("#detail-cases")).to_contain_text("Follow up callback issue")
    page.click("#detail-close")

    page.select_option("#connection-provider", "jira")
    page.fill("#connection-display-name", "Acme Jira")
    page.fill("#connection-credentials-ref", "env:RUHU_JIRA_TOKEN")
    page.fill("#connection-base-url", "https://jira.example.com")
    page.fill("#connection-default-queue", "support")
    page.click("#connection-create")
    expect(page.locator("#tickets-banner")).to_contain_text("Ticketing connection created.")
    expect(page.locator("#connection-list")).to_contain_text("Acme Jira")

    page.fill("#connection-edit-display-name", "Acme Jira Prime")
    page.fill("#connection-edit-default-queue", "priority-support")
    page.click("#connection-edit-save")
    expect(page.locator("#tickets-banner")).to_contain_text("Connection updated.")
    expect(page.locator("#connection-list")).to_contain_text("Acme Jira Prime")

    page.click("#connection-disable")
    expect(page.locator("#tickets-banner")).to_contain_text("Connection disabled.")
    expect(page.locator("#connection-list")).to_contain_text("disabled")

    page.click("#connection-enable")
    expect(page.locator("#tickets-banner")).to_contain_text("Connection active.")
    expect(page.locator("#connection-list")).to_contain_text("active")

    page.click('[data-health-connection]')
    expect(page.locator("#tickets-banner")).to_contain_text("Connection health check completed.")
    expect(page.locator("#connection-activity-list")).to_contain_text("health_check")

    harness.remote_cases["jira-remote-existing"] = RemoteCase(
        external_case_id="jira-remote-existing",
        external_case_key="EXISTING-77",
        external_case_url="https://tickets.example.com/jira-remote-existing",
        external_case_status="Triaged",
        external_case_priority="high",
        payload={"title": "Existing callback escalation", "description": "Pre-existing linked issue"},
    )

    page.locator(f'[data-conversation-id="{conversation_id}"]').click()
    expect(page.locator("#detail-cases")).to_contain_text("Follow up callback issue")

    connection_value = page.locator("#external-connection-id").locator("option").nth(1).get_attribute("value")
    assert connection_value
    support_case_value = page.locator("#external-support-case-id").locator("option").nth(1).get_attribute("value")
    assert support_case_value
    page.select_option("#external-connection-id", connection_value)
    page.select_option("#external-support-case-id", support_case_value)
    page.fill("#external-title", "Escalated callback issue")
    page.fill("#external-description", "Create a linked Jira issue from the ticket view.")
    page.click("#external-case-create")
    expect(page.locator("#tickets-banner")).to_contain_text("External case created.")
    expect(page.locator("#detail-external")).to_contain_text("jira")
    expect(page.locator("#detail-external")).to_contain_text("JIRA-REMOTE-")

    page.fill("#external-search-query", "Existing callback")
    page.click("#external-search")
    expect(page.locator("#external-remote-search-results")).to_contain_text("EXISTING-77")
    page.click('[data-link-remote-case="jira-remote-existing"]')
    expect(page.locator("#tickets-banner")).to_contain_text("Existing external case linked.")
    expect(page.locator("#detail-external")).to_contain_text("EXISTING-77")

    comment_link = page.locator('[data-comment-link]').first
    comment_link_id = comment_link.get_attribute("data-comment-link")
    assert comment_link_id
    comment_link.click()
    page.fill(f"#external-comment-body-{comment_link_id}", "Shared the latest callback context.")
    page.click(f'[data-submit-comment="{comment_link_id}"]')
    expect(page.locator("#tickets-banner")).to_contain_text("External case comment sent.")

    transition_link = page.locator('[data-transition-link]').first
    transition_link_id = transition_link.get_attribute("data-transition-link")
    assert transition_link_id
    transition_link.click()
    page.fill(f"#external-transition-status-{transition_link_id}", "Done")
    page.click(f'[data-submit-transition="{transition_link_id}"]')
    expect(page.locator("#tickets-banner")).to_contain_text("External case transitioned.")
    expect(page.locator("#detail-external")).to_contain_text("Done")

    sync_link_id = page.locator('[data-sync-link]').first.get_attribute("data-sync-link")
    assert sync_link_id
    page.click(f'[data-sync-link="{sync_link_id}"]')
    expect(page.locator("#tickets-banner")).to_contain_text("External case synced.")

    harness.failures["fetch_case"] = 1
    page.click(f'[data-sync-link="{sync_link_id}"]')
    expect(page.locator("#tickets-banner")).to_contain_text("temporary fetch_case failure")
    page.click("#detail-close")
    page.click("#activity-refresh")
    expect(page.locator("#connection-activity-list")).to_contain_text("Retry: pending")
    page.click("#activity-process-retries")
    expect(page.locator("#tickets-banner")).to_contain_text("Processed 1 retry task(s).")
    expect(page.locator("#connection-activity-list")).to_contain_text("Retry: succeeded")

    page.locator(f'[data-conversation-id="{conversation_id}"]').click()

    note_button = page.locator('[data-note-case]').first
    note_button.click()
    note_case_id = note_button.get_attribute("data-note-case")
    assert note_case_id
    page.fill(f"#case-note-{note_case_id}", "Customer asked for a same-day callback.")
    page.click(f'[data-submit-note="{note_case_id}"]')
    expect(page.locator("#tickets-banner")).to_contain_text("Support case note saved.")
    expect(page.locator(f"#case-history-{note_case_id}")).to_contain_text("same-day callback")

    resolve_button = page.locator('[data-resolve-case]').first
    resolve_button.click()
    resolve_case_id = resolve_button.get_attribute("data-resolve-case")
    assert resolve_case_id
    page.fill(f"#case-resolution-type-{resolve_case_id}", "callback_completed")
    page.fill(f"#case-resolution-summary-{resolve_case_id}", "Callback completed successfully")
    page.click(f'[data-submit-resolve="{resolve_case_id}"]')
    expect(page.locator("#tickets-banner")).to_contain_text("Support case resolved.")
    expect(page.locator("#detail-cases")).to_contain_text("resolved")

    close_case_id = page.locator('[data-close-case]').first.get_attribute("data-close-case")
    assert close_case_id
    page.click(f'[data-close-case="{close_case_id}"]')
    expect(page.locator("#tickets-banner")).to_contain_text("Support case closed.")
    expect(page.locator("#detail-cases")).to_contain_text("closed")
