from __future__ import annotations

from playwright.sync_api import Page, expect


def test_journey_studio_browser_supports_structured_rules_duplicate_and_archive(
    page: Page,
    ticket_browser_harness,
) -> None:
    harness = ticket_browser_harness
    page_errors: list[str] = []
    console_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on(
        "console",
        lambda message: console_errors.append(f"{message.type}: {message.text}")
        if message.type == "error"
        else None,
    )
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
        created = client.post(
            "/journey-definitions",
            json={
                "slug": "browser-journey",
                "name": "Browser Journey",
                "subject_strategy": {"kind": "fact_name", "value": "customer_id"},
                "scope": {"agent_ids": ["sales_agent"]},
                "tags": ["sales", "browser"],
            },
        )
        assert created.status_code == 200
        definition_id = created.json()["definition_id"]
        version = client.post(
            f"/journey-definitions/{definition_id}/versions",
            json={
                "rules": {
                    "entry_rules": [{"kind": "conversation_started", "value": None, "metadata": {}}],
                    "touchpoint_rules": [{"kind": "state_entered", "value": "handoff", "metadata": {}}],
                    "milestones": [],
                    "outcome_rules": {
                        "completed": [],
                        "abandoned": [],
                        "transferred": [],
                        "failed": [],
                    },
                    "abandonment_policy": {
                        "inactive_after_seconds": 3600,
                        "close_as": "abandoned",
                    },
                    "merge_policy": {
                        "reopen_closed_within_seconds": 900,
                        "reopen_statuses": ["abandoned", "failed"],
                    },
                }
            },
        )
        assert version.status_code == 200

    harness.add_browser_session(page, user_id=admin.user_id)

    page.goto(f"{harness.base_url}/studio")
    expect(page.locator("h1")).to_have_text("Ruhu Agent Studio")
    try:
        expect(page.locator("#journey-definition-list")).to_contain_text(
            "Browser Journey",
            timeout=20000,
        )
    except AssertionError as exc:
        debug_payload = page.evaluate(
            """async () => {
                try {
                    const response = await fetch('/journey-definitions', { credentials: 'same-origin' });
                    return {
                        status: response.status,
                        body: await response.text(),
                        studioStatus: document.getElementById('studio-status')?.textContent || null,
                        journeySummary: document.getElementById('journey-definition-summary')?.textContent || null,
                    };
                } catch (error) {
                    return {
                        error: String(error),
                        studioStatus: document.getElementById('studio-status')?.textContent || null,
                        journeySummary: document.getElementById('journey-definition-summary')?.textContent || null,
                    };
                }
            }"""
        )
        raise AssertionError(
            {
                **debug_payload,
                "pageErrors": page_errors,
                "consoleErrors": console_errors,
            }
        ) from exc
    expect(page.locator("#studio-status")).to_contain_text("Draft agent loaded.")
    expect(page.locator("#journey-definition-name")).to_have_value("Browser Journey")
    expect(page.locator('#journey-touchpoint-rules-editor select[data-field="kind"]').first).to_have_value("state_entered")
    expect(page.locator('#journey-touchpoint-rules-editor input[data-field="value"]').first).to_have_value("handoff")
    expect(page.locator("#journey-abandonment-seconds")).to_have_value("3600")
    expect(page.locator("#journey-merge-reopen-seconds")).to_have_value("900")
    expect(page.locator("#journey-merge-reopen-statuses")).to_have_value("abandoned, failed")

    page.fill('#journey-touchpoint-rules-editor input[data-field="value"]', "follow_up")

    page.click("#create-journey-version-button")
    expect(page.locator("#studio-status")).to_contain_text("Journey draft version created.")

    page.click("#duplicate-journey-definition-button")
    expect(page.locator("#studio-status")).to_contain_text("Duplicated journey definition as browser-journey-copy.")
    expect(page.locator("#journey-definition-name")).to_have_value("Browser Journey Copy")
    expect(page.locator("#journey-definition-slug")).to_have_value("browser-journey-copy")

    page.click("#archive-journey-definition-button")
    expect(page.locator("#studio-status")).to_contain_text("Archived journey definition browser-journey-copy.")
    expect(page.locator("#journey-definition-status")).to_have_value("archived")
    expect(page.locator("#journey-definition-list")).to_contain_text("archived")

    with harness.authorized_client(user_id=admin.user_id) as client:
        definitions_response = client.get("/journey-definitions")
        assert definitions_response.status_code == 200
        definitions = definitions_response.json()["definitions"]
        duplicate = next((item for item in definitions if item["slug"] == "browser-journey-copy"), None)
        assert duplicate is not None
        assert duplicate["status"] == "archived"
        assert duplicate["current_draft_version_id"] is not None

        source_versions_response = client.get(f"/journey-definitions/{definition_id}/versions")
        assert source_versions_response.status_code == 200
        source_versions = source_versions_response.json()["versions"]
        assert len(source_versions) == 2
        latest_source_version = max(source_versions, key=lambda item: item["version_number"])
        assert latest_source_version["rules"]["touchpoint_rules"] == [
            {
                "kind": "state_entered",
                "value": "follow_up",
                "metadata": {},
            }
        ]

        versions_response = client.get(f'/journey-definitions/{duplicate["definition_id"]}/versions')
        assert versions_response.status_code == 200
        versions = versions_response.json()["versions"]
        assert len(versions) == 1
        assert versions[0]["rules"]["touchpoint_rules"] == [
            {
                "kind": "state_entered",
                "value": "follow_up",
                "metadata": {},
            }
        ]
