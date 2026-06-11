from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request

from ruhu.atlas_api import build_atlas_router
from ruhu.atlas_models import AtlasSession
from ruhu.api import build_default_app
from ruhu.agent_document import AgentDocument, Scenario, Step
from ruhu.db import build_session_factory
from ruhu.registry import SQLAlchemyAgentRegistry
from ruhu.atlas_store import SQLAlchemyAtlasStore
from ruhu.tools.management import (
    APIConnectionStore,
    AgentToolBindingStore,
    ToolAgentAssignmentStore,
    ToolDefinitionStore,
)


@pytest.mark.asyncio
async def test_atlas_session_turn_and_history(postgres_database_url_factory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUHU_ATLAS_GENERATOR_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={
                "scope": "agent_authoring",
                "agent_id": "sales",
                "initial_message": "Help me understand this sales agent.",
            },
        )
        assert start.status_code == 200
        session = start.json()
        assert session["agent_id"] == "sales"
        assert session["status"] == "active"
        assert "created_by" in session

        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session["session_id"],
                "message": "Review the current start step.",
                "selected_context": {"step_id": "discover"},
                "attachments": [
                    {
                        "attachment_id": "att_1",
                        "kind": "workflow_description",
                        "display_name": "brief.md",
                        "metadata": {"extracted_characters": 120, "chunk_count": 1},
                    }
                ],
            },
        )
        assert turn.status_code == 200
        payload = turn.json()
        assert payload["session_id"] == session["session_id"]
        assert payload["message"]
        assert payload["next_action"] == "complete"
        assert payload["generator"] == {"mode": "fallback", "model": None}
        assert [item["name"] for item in payload["tool_calls"]] == [
            "ingest_attachments",
            "inspect_agent",
            "validate_publish",
        ]
        assert payload["references"]["agent_ids"] == ["sales"]
        assert "discover" in payload["references"]["step_ids"]
        assert payload["attachment_ingestion_results"][0]["mode"] == "text_extracted"
        # AR-5.3: the response carries a protocol version, and the attachment
        # kind round-trips as the typed value it was sent as.
        assert payload["protocol_version"] == "1.0"
        assert payload["attachment_ingestion_results"][0]["kind"] == "workflow_description"

        messages = await client.get(f"/atlas/sessions/{session['session_id']}/messages")
        assert messages.status_code == 200
        message_payload = messages.json()
        assert message_payload["total_count"] >= 3

        events = await client.get(f"/atlas/sessions/{session['session_id']}/events")
        assert events.status_code == 200
        event_payload = events.json()
        event_types = [item["type"] for item in event_payload["events"]]
        assert "tool_start" in event_types
        assert "tool_done" in event_types
        assert all(item["sequence_number"] > 0 for item in event_payload["events"])

        state = await client.get(f"/atlas/sessions/{session['session_id']}/state")
        assert state.status_code == 200
        state_payload = state.json()
        assert state_payload["session_id"] == session["session_id"]
        assert state_payload["references"]["agent_ids"] == ["sales"]
        assert state_payload["attachment_ingestion_results"] == []


@pytest.mark.asyncio
async def test_atlas_turn_handles_incomplete_draft_agent(postgres_database_url_factory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUHU_ATLAS_GENERATOR_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    database_url = postgres_database_url_factory()
    session_factory = build_session_factory(database_url)
    SQLAlchemyAgentRegistry(session_factory).create_agent_document(
        agent_id="blank_agent",
        agent_name="Blank Agent",
        organization_id="public",
        document=AgentDocument(
            start_scenario_id="draft",
            scenarios=[
                Scenario(
                    id="draft",
                    name="Draft",
                    start_step_id="start",
                    steps=[Step(id="start", name="Start")],
                )
            ],
        ),
    )
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "agent_authoring", "agent_id": "blank_agent"},
        )
        assert start.status_code == 200
        session = start.json()

        turn = await client.post(
            "/atlas/turns",
            json={"session_id": session["session_id"], "message": "hello"},
        )
        assert turn.status_code == 200
        payload = turn.json()
        assert payload["message"]
        assert "reviewed the agent document" not in payload["message"]
        assert payload["next_action"] == "complete"
        assert payload["tool_calls"] == []
        assert payload["blockers"] == []

        messages = await client.get(f"/atlas/sessions/{session['session_id']}/messages")
        assert messages.status_code == 200
        message_payload = messages.json()
        assert [item["role"] for item in message_payload["messages"]][-2:] == ["user", "assistant"]

        events = await client.get(f"/atlas/sessions/{session['session_id']}/events")
        assert events.status_code == 200
        event_payload = events.json()
        assert event_payload["total_count"] >= 3
        assert [item["type"] for item in event_payload["events"]] == ["start", "progress", "complete"]
        assert event_payload["events"][1]["payload"]["generator_mode"] == "fallback"
        assert event_payload["events"][1]["payload"]["generator_model"] is None
        assert event_payload["events"][2]["payload"]["generator_mode"] == "fallback"


@pytest.mark.asyncio
async def test_atlas_read_routes_require_resolved_organization(postgres_database_url_factory) -> None:
    from datetime import datetime, timezone

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    store = SQLAlchemyAtlasStore(session_factory)
    registry = SQLAlchemyAgentRegistry(session_factory)
    now = datetime.now(timezone.utc)
    store.create_session(
        AtlasSession(
            session_id="atlas_session_read_fail_closed",
            organization_id="public",
            scope="agent_authoring",
            status="active",
            agent_id="sales",
            created_at=now,
            updated_at=now,
        )
    )

    app = FastAPI()

    def _no_org(_request: Request):
        return None

    def _require_author(_request: Request):
        return None

    def _required_org(_context):
        raise HTTPException(status_code=401, detail="authentication required")

    app.include_router(
        build_atlas_router(
            agent_registry=registry,
            atlas_store=store,
            get_organization_id=_no_org,
            user_id_for_context=lambda _context: None,
            require_author_context=_require_author,
            required_author_organization_id=_required_org,
        )
    )

    paths = [
        "/atlas/sessions",
        "/atlas/sessions/atlas_session_read_fail_closed",
        "/atlas/sessions/atlas_session_read_fail_closed/messages",
        "/atlas/sessions/atlas_session_read_fail_closed/state",
        "/atlas/sessions/atlas_session_read_fail_closed/events",
        "/atlas/sessions/atlas_session_read_fail_closed/events/stream",
        "/atlas/agents/sales/enabled",
    ]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        for path in paths:
            response = await client.get(path)
            assert response.status_code == 401, path


@pytest.mark.asyncio
async def test_atlas_issue_review_returns_validation_findings(postgres_database_url_factory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUHU_ATLAS_GENERATOR_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "agent_authoring", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session = start.json()

        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session["session_id"],
                "message": "Review the agent and let me know what are the issues",
            },
        )
        assert turn.status_code == 200
        payload = turn.json()
        assert [item["name"] for item in payload["tool_calls"]] == [
            "inspect_agent",
            "validate_publish",
        ]
        assert all(not value for value in payload["proposed_changes"].values())
        assert "I reviewed the agent" in payload["message"]
        assert "responds in place" not in payload["message"]

        detail_turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session["session_id"],
                "message": "review the agent in detail",
            },
        )
        assert detail_turn.status_code == 200
        detail_payload = detail_turn.json()
        assert [item["name"] for item in detail_payload["tool_calls"]] == [
            "inspect_agent",
            "validate_publish",
        ]
        assert all(not value for value in detail_payload["proposed_changes"].values())
        assert "I reviewed the agent" in detail_payload["message"]
        assert "responds in place" not in detail_payload["message"]


@pytest.mark.asyncio
async def test_atlas_agent_enable_toggle_blocks_new_sessions(postgres_database_url_factory) -> None:
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        enabled = await client.get("/atlas/agents/sales/enabled")
        assert enabled.status_code == 200
        assert enabled.json()["atlas_enabled"] is True

        toggle = await client.put(
            "/atlas/agents/sales/enabled",
            json={"atlas_enabled": False},
        )
        assert toggle.status_code == 200
        assert toggle.json() == {"agent_id": "sales", "atlas_enabled": False}

        start = await client.post(
            "/atlas/sessions",
            json={"scope": "agent_authoring", "agent_id": "sales"},
        )
        assert start.status_code == 409
        assert start.json()["detail"] == "atlas is disabled for this agent"


@pytest.mark.asyncio
async def test_atlas_lists_sessions_for_agent_and_scope(postgres_database_url_factory) -> None:
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        author = await client.post(
            "/atlas/sessions",
            json={"scope": "agent_authoring", "agent_id": "sales"},
        )
        assert author.status_code == 200
        provision = await client.post(
            "/atlas/sessions",
            json={"scope": "provisioning", "agent_id": "sales"},
        )
        assert provision.status_code == 200

        listing = await client.get(
            "/atlas/sessions",
            params={"agent_id": "sales", "scope": "provisioning"},
        )
        assert listing.status_code == 200
        payload = listing.json()
        assert payload["total_count"] >= 1
        assert payload["sessions"][0]["scope"] == "provisioning"
        assert payload["sessions"][0]["agent_id"] == "sales"
        assert provision.json()["session_id"] in [item["session_id"] for item in payload["sessions"]]
        assert author.json()["session_id"] not in [item["session_id"] for item in payload["sessions"]]


@pytest.mark.asyncio
async def test_atlas_permission_and_apply_flow_is_explicit(postgres_database_url_factory) -> None:
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "agent_authoring", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]

        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": 'Rename this step to "Qualified lead"',
                "selected_context": {"step_id": "discover"},
            },
        )
        assert turn.status_code == 200
        payload = turn.json()
        assert payload["next_action"] == "ready_to_review_changes"
        delta_id = payload["proposed_changes"]["step_deltas"][0]["delta_id"]

        review_and_request = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "review_decisions": [{"delta_id": delta_id, "decision": "approved"}],
                "apply_request": {"delta_ids": [delta_id], "apply_note": "ship it"},
            },
        )
        assert review_and_request.status_code == 200
        payload = review_and_request.json()
        assert payload["next_action"] == "blocked"
        assert payload["pending_permission_requests"]
        assert payload["proposed_changes"]["step_deltas"][0]["status"] == "approved"
        pending_request = payload["pending_permission_requests"][0]
        request_id = pending_request["request_id"]
        # Permission requests must carry an expiration so stale pending rows
        # don't block apply forever.
        assert pending_request["expires_at"] is not None

        apply_while_pending = await client.post(
            f"/atlas/sessions/{session_id}/apply",
            json={"delta_ids": [delta_id], "apply_note": "ship it"},
        )
        assert apply_while_pending.status_code == 200
        assert apply_while_pending.json()["status"] == "rejected"

        decisions = await client.post(
            f"/atlas/sessions/{session_id}/permission-decisions",
            json=[{"request_id": request_id, "decision": "approved"}],
        )
        assert decisions.status_code == 200
        assert decisions.json()["updated_requests"][0]["status"] == "approved"

        apply_after_decision = await client.post(
            f"/atlas/sessions/{session_id}/apply",
            json={"delta_ids": [delta_id], "apply_note": "ship it"},
        )
        assert apply_after_decision.status_code == 200
        assert apply_after_decision.json()["status"] == "applied"
        assert apply_after_decision.json()["error"] is None

        # AR-3.6: re-applying an already-applied set is an idempotent success
        # (e.g. a client retry after a timed-out apply), not a spurious failure.
        repeat_apply = await client.post(
            f"/atlas/sessions/{session_id}/apply",
            json={"delta_ids": [delta_id], "apply_note": "ship it again"},
        )
        assert repeat_apply.status_code == 200
        assert repeat_apply.json()["status"] == "applied"
        assert repeat_apply.json()["error"] is None

        post_apply_turn = await client.post(
            "/atlas/turns",
            json={"session_id": session_id, "message": "What changed?"},
        )
        assert post_apply_turn.status_code == 200
        post_apply_payload = post_apply_turn.json()
        applied_delta = next(
            item for item in post_apply_payload["proposed_changes"]["step_deltas"] if item["delta_id"] == delta_id
        )
        assert applied_delta["status"] == "applied"

        document = await client.get("/agents/sales/agent-document")
        assert document.status_code == 200
        payload = document.json()["document"]
        updated_step = next(step for scenario in payload["scenarios"] for step in scenario["steps"] if step["id"] == "discover")
        assert updated_step["name"] == "Qualified lead"


def test_atlas_store_filters_expired_pending_permission_requests(postgres_database_url_factory) -> None:
    """`list_permission_requests(status="pending")` must hide rows whose
    expires_at is in the past so stale requests don't block apply forever.
    Rows with expires_at=None are still returned because they have no expiry
    condition to evaluate."""
    from datetime import datetime, timedelta, timezone

    from ruhu.atlas_models import AtlasPermissionRequest, AtlasSession
    from ruhu.atlas_store import SQLAlchemyAtlasStore

    database_url = postgres_database_url_factory()
    # Bootstrap schema by building the app once; the store reuses the DB.
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    store = SQLAlchemyAtlasStore(session_factory)

    org_id = "public"
    now = datetime.now(timezone.utc)
    session = AtlasSession(
        session_id="atlas_session_test_expiry",
        organization_id=org_id,
        scope="agent_authoring",
        status="active",
        agent_id="sales",
        created_at=now,
        updated_at=now,
    )
    store.create_session(session)

    expired_req = AtlasPermissionRequest(
        request_id="atlas_perm_expired",
        session_id=session.session_id,
        organization_id=org_id,
        kind="apply_deltas",
        status="pending",
        reason="expired-test",
        delta_ids=["d1"],
        created_at=now - timedelta(hours=48),
        expires_at=now - timedelta(hours=24),
    )
    fresh_req = AtlasPermissionRequest(
        request_id="atlas_perm_fresh",
        session_id=session.session_id,
        organization_id=org_id,
        kind="apply_deltas",
        status="pending",
        reason="fresh-test",
        delta_ids=["d2"],
        created_at=now,
        expires_at=now + timedelta(hours=24),
    )
    legacy_req = AtlasPermissionRequest(
        request_id="atlas_perm_legacy",
        session_id=session.session_id,
        organization_id=org_id,
        kind="apply_deltas",
        status="pending",
        reason="legacy-test",
        delta_ids=["d3"],
        created_at=now - timedelta(hours=1),
        expires_at=None,
    )
    store.create_permission_request(expired_req)
    store.create_permission_request(fresh_req)
    store.create_permission_request(legacy_req)

    pending = store.list_permission_requests(
        session.session_id,
        organization_id=org_id,
        status="pending",
    )
    pending_ids = {item.request_id for item in pending}
    assert "atlas_perm_fresh" in pending_ids
    assert "atlas_perm_legacy" in pending_ids
    assert "atlas_perm_expired" not in pending_ids

    # Without the status filter all rows still come through — expired filter
    # is scoped to the pending-blocking semantics.
    all_requests = store.list_permission_requests(
        session.session_id,
        organization_id=org_id,
    )
    all_ids = {item.request_id for item in all_requests}
    assert "atlas_perm_expired" in all_ids


def test_atlas_store_apply_permission_decisions_uses_authenticated_user_id(
    postgres_database_url_factory,
) -> None:
    """`apply_permission_decisions` must record the authenticated user_id
    passed by the API layer, ignoring any user identifier supplied in the
    request body. Drives the audit-trail safety property."""
    from datetime import datetime, timedelta, timezone

    from ruhu.atlas_models import AtlasPermissionRequest, AtlasSession
    from ruhu.atlas_store import SQLAlchemyAtlasStore

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    store = SQLAlchemyAtlasStore(session_factory)

    org_id = "public"
    now = datetime.now(timezone.utc)
    session = AtlasSession(
        session_id="atlas_session_decided_by",
        organization_id=org_id,
        scope="agent_authoring",
        status="active",
        agent_id="sales",
        created_at=now,
        updated_at=now,
    )
    store.create_session(session)
    store.create_permission_request(
        AtlasPermissionRequest(
            request_id="atlas_perm_decided_by",
            session_id=session.session_id,
            organization_id=org_id,
            kind="apply_deltas",
            status="pending",
            reason="decided-by-test",
            delta_ids=["d1"],
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
    )

    # Decision dicts intentionally include a stale "decided_by" — the store
    # must ignore it and use the authenticated user_id keyword instead.
    updated = store.apply_permission_decisions(
        session.session_id,
        [{"request_id": "atlas_perm_decided_by", "decision": "approved", "decided_by": "spoofed_user"}],
        organization_id=org_id,
        decided_by_user_id="real_user_42",
    )
    assert len(updated) == 1
    assert updated[0].decided_by_user_id == "real_user_42"


def test_atlas_store_rejects_expired_and_already_decided_permission_requests(
    postgres_database_url_factory,
) -> None:
    from datetime import datetime, timedelta, timezone

    from ruhu.atlas_models import AtlasPermissionRequest, AtlasSession
    from ruhu.atlas_store import SQLAlchemyAtlasStore

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    store = SQLAlchemyAtlasStore(session_factory)

    org_id = "public"
    now = datetime.now(timezone.utc)
    session = AtlasSession(
        session_id="atlas_session_permission_rejects",
        organization_id=org_id,
        scope="agent_authoring",
        status="active",
        agent_id="sales",
        created_at=now,
        updated_at=now,
    )
    store.create_session(session)
    store.create_permission_request(
        AtlasPermissionRequest(
            request_id="atlas_perm_expired_decision",
            session_id=session.session_id,
            organization_id=org_id,
            kind="apply_deltas",
            status="pending",
            reason="expired-decision-test",
            delta_ids=["d1"],
            created_at=now - timedelta(days=2),
            expires_at=now - timedelta(days=1),
        )
    )
    store.create_permission_request(
        AtlasPermissionRequest(
            request_id="atlas_perm_denied_decision",
            session_id=session.session_id,
            organization_id=org_id,
            kind="apply_deltas",
            status="denied",
            reason="denied-decision-test",
            delta_ids=["d2"],
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
    )

    with pytest.raises(ValueError, match="has expired"):
        store.apply_permission_decisions(
            session.session_id,
            [{"request_id": "atlas_perm_expired_decision", "decision": "approved"}],
            organization_id=org_id,
            decided_by_user_id="reviewer",
        )
    with pytest.raises(ValueError, match="already denied"):
        store.apply_permission_decisions(
            session.session_id,
            [{"request_id": "atlas_perm_denied_decision", "decision": "approved"}],
            organization_id=org_id,
            decided_by_user_id="reviewer",
        )


def test_atlas_store_finds_only_matching_unexpired_approved_apply_permission(
    postgres_database_url_factory,
) -> None:
    from datetime import datetime, timedelta, timezone

    from ruhu.atlas_models import AtlasPermissionRequest, AtlasSession
    from ruhu.atlas_store import SQLAlchemyAtlasStore

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    store = SQLAlchemyAtlasStore(session_factory)

    org_id = "public"
    now = datetime.now(timezone.utc)
    session = AtlasSession(
        session_id="atlas_session_approved_permission_lookup",
        organization_id=org_id,
        scope="agent_authoring",
        status="active",
        agent_id="sales",
        created_at=now,
        updated_at=now,
    )
    store.create_session(session)
    store.create_permission_request(
        AtlasPermissionRequest(
            request_id="atlas_perm_approved_exact",
            session_id=session.session_id,
            organization_id=org_id,
            kind="apply_deltas",
            status="approved",
            reason="approved exact",
            delta_ids=["d1", "d2"],
            created_at=now,
            expires_at=now + timedelta(hours=1),
            decided_at=now,
        )
    )
    store.create_permission_request(
        AtlasPermissionRequest(
            request_id="atlas_perm_approved_expired",
            session_id=session.session_id,
            organization_id=org_id,
            kind="apply_deltas",
            status="approved",
            reason="approved expired",
            delta_ids=["d3"],
            created_at=now,
            expires_at=now - timedelta(hours=1),
            decided_at=now,
        )
    )

    assert store.find_approved_apply_permission(
        session.session_id,
        ["d2", "d1"],
        organization_id=org_id,
    ) is not None
    assert store.find_approved_apply_permission(
        session.session_id,
        ["d1"],
        organization_id=org_id,
    ) is None
    assert store.find_approved_apply_permission(
        session.session_id,
        ["d3"],
        organization_id=org_id,
    ) is None


def test_atlas_store_apply_lock_rejects_concurrent_apply_for_same_session(
    postgres_database_url_factory,
) -> None:
    from ruhu.atlas_store import SQLAlchemyAtlasStore

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    store = SQLAlchemyAtlasStore(session_factory)

    with store.apply_lock("atlas_session_lock_test", organization_id="public"):
        with pytest.raises(ValueError, match="already in progress"):
            with store.apply_lock("atlas_session_lock_test", organization_id="public"):
                pass

    with store.apply_lock("atlas_session_lock_test", organization_id="public"):
        pass


def test_atlas_store_load_proposed_changes_quarantines_unparseable_row(
    postgres_database_url_factory,
) -> None:
    """AR-3.4: a drifted/corrupt delta row is skipped, not 500 for the session."""
    from datetime import datetime, timezone

    from ruhu.atlas_models import AtlasSession
    from ruhu.atlas_protocol import AtlasProposedChanges, StepDelta
    from ruhu.atlas_store import SQLAlchemyAtlasStore
    from ruhu.db_models import AtlasProposedDeltaRecord

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    store = SQLAlchemyAtlasStore(session_factory)

    now = datetime.now(timezone.utc)
    store.create_session(
        AtlasSession(
            session_id="atlas_session_quarantine",
            organization_id="org-a",
            scope="agent_authoring",
            status="active",
            agent_id="sales",
            created_at=now,
            updated_at=now,
        )
    )
    # One valid delta through the normal path.
    store.replace_proposed_changes(
        "atlas_session_quarantine",
        AtlasProposedChanges(
            step_deltas=[
                StepDelta(
                    agent_id="sales",
                    scenario_id="main",
                    step_id="start",
                    delta_id="delta_good",
                    operation="update",
                    change_type="rename_step",
                    payload={"name": "Renamed"},
                    summary="ok",
                )
            ]
        ),
        organization_id="org-a",
    )
    # Inject a corrupt sibling row (schema-drift simulation): a known family
    # whose stored JSON no longer satisfies the model.
    with session_factory.begin() as session:
        session.add(
            AtlasProposedDeltaRecord(
                delta_id="delta_corrupt",
                session_id="atlas_session_quarantine",
                organization_id="org-a",
                delta_family="step_deltas",
                delta_json={"delta_id": "delta_corrupt"},  # missing required fields
                created_at=now,
                updated_at=now,
            )
        )

    loaded = store.load_proposed_changes("atlas_session_quarantine", organization_id="org-a")
    ids = {d.delta_id for d in loaded.step_deltas}
    assert ids == {"delta_good"}  # corrupt row skipped, session still readable


def test_atlas_store_update_session_is_org_scoped_and_optimistic(
    postgres_database_url_factory,
) -> None:
    from datetime import datetime, timedelta, timezone

    from ruhu.atlas_models import AtlasSession
    from ruhu.atlas_store import SQLAlchemyAtlasStore

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    store = SQLAlchemyAtlasStore(session_factory)

    now = datetime.now(timezone.utc)
    session_model = AtlasSession(
        session_id="atlas_session_update_scoped",
        organization_id="org-a",
        scope="agent_authoring",
        status="active",
        agent_id="sales",
        scenario_id="discover",
        step_id="start",
        created_at=now,
        updated_at=now,
    )
    store.create_session(session_model)

    with pytest.raises(KeyError):
        store.update_session(
            session_model.model_copy(update={"status": "blocked", "updated_at": now + timedelta(seconds=1)}),
            organization_id="org-b",
            expected_updated_at=now,
        )
    with pytest.raises(ValueError, match="updated by another request"):
        store.update_session(
            session_model.model_copy(update={"status": "blocked", "updated_at": now + timedelta(seconds=1)}),
            organization_id="org-a",
            expected_updated_at=now - timedelta(seconds=1),
        )

    updated = store.update_session(
        session_model.model_copy(
            update={
                "status": "blocked",
                "scenario_id": "qualify",
                "step_id": "qualified",
                "updated_at": now + timedelta(seconds=2),
            }
        ),
        organization_id="org-a",
        expected_updated_at=now,
    )
    assert updated.status == "blocked"
    persisted = store.get_session(session_model.session_id, organization_id="org-a")
    assert persisted is not None
    assert persisted.scenario_id == "qualify"
    assert persisted.step_id == "qualified"


def test_atlas_store_update_session_status_preserves_selection_fields(
    postgres_database_url_factory,
) -> None:
    from datetime import datetime, timedelta, timezone

    from ruhu.atlas_models import AtlasSession
    from ruhu.atlas_store import SQLAlchemyAtlasStore

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    store = SQLAlchemyAtlasStore(session_factory)

    now = datetime.now(timezone.utc)
    session_model = AtlasSession(
        session_id="atlas_session_status_only",
        organization_id="org-a",
        scope="agent_authoring",
        status="active",
        agent_id="sales",
        scenario_id="discover",
        step_id="start",
        created_at=now,
        updated_at=now,
    )
    store.create_session(session_model)

    with pytest.raises(KeyError):
        store.update_session_status(
            session_model.session_id,
            "blocked",
            organization_id="org-b",
            updated_at=now + timedelta(seconds=1),
        )

    updated = store.update_session_status(
        session_model.session_id,
        "blocked",
        organization_id="org-a",
        updated_at=now + timedelta(seconds=2),
    )
    assert updated.status == "blocked"
    assert updated.scenario_id == "discover"
    assert updated.step_id == "start"
    persisted = store.get_session(session_model.session_id, organization_id="org-a")
    assert persisted is not None
    assert persisted.status == "blocked"
    assert persisted.scenario_id == "discover"
    assert persisted.step_id == "start"


def test_atlas_store_append_message_sequence_waits_for_session_lock(
    postgres_database_url_factory,
) -> None:
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from datetime import datetime, timezone

    from sqlalchemy import func, select

    from ruhu.atlas_models import AtlasMessage, AtlasSession
    from ruhu.atlas_store import SQLAlchemyAtlasStore, _advisory_lock_key

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    store = SQLAlchemyAtlasStore(session_factory)
    now = datetime.now(timezone.utc)
    session_model = AtlasSession(
        session_id="atlas_session_message_sequence_lock",
        organization_id="org-a",
        scope="agent_authoring",
        status="active",
        agent_id="sales",
        created_at=now,
        updated_at=now,
    )
    store.create_session(session_model)
    lock_key = _advisory_lock_key(f"atlas-sequence:atlas_messages:{session_model.session_id}")
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with session_factory.begin() as db_session:
            db_session.execute(select(func.pg_advisory_xact_lock(lock_key)))
            future = executor.submit(
                store.append_message,
                AtlasMessage(
                    message_id="atlas_message_locked_sequence",
                    session_id=session_model.session_id,
                    organization_id="org-a",
                    sequence_number=0,
                    role="user",
                    content="hello",
                    created_at=now,
                ),
            )
            with pytest.raises(TimeoutError):
                future.result(timeout=0.2)
        appended = future.result(timeout=5)
    finally:
        executor.shutdown(wait=True)

    assert appended.sequence_number == 1
    messages, total_count, _has_more = store.list_messages(session_model.session_id, organization_id="org-a")
    assert total_count == 1
    assert messages[0].message_id == "atlas_message_locked_sequence"


def test_atlas_store_append_event_sequence_waits_for_session_lock(
    postgres_database_url_factory,
) -> None:
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from datetime import datetime, timezone

    from sqlalchemy import func, select

    from ruhu.atlas_models import AtlasEvent, AtlasSession
    from ruhu.atlas_store import SQLAlchemyAtlasStore, _advisory_lock_key

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    store = SQLAlchemyAtlasStore(session_factory)
    now = datetime.now(timezone.utc)
    session_model = AtlasSession(
        session_id="atlas_session_event_sequence_lock",
        organization_id="org-a",
        scope="agent_authoring",
        status="active",
        agent_id="sales",
        created_at=now,
        updated_at=now,
    )
    store.create_session(session_model)
    lock_key = _advisory_lock_key(f"atlas-sequence:atlas_events:{session_model.session_id}")
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with session_factory.begin() as db_session:
            db_session.execute(select(func.pg_advisory_xact_lock(lock_key)))
            future = executor.submit(
                store.append_event,
                AtlasEvent(
                    event_id="atlas_event_locked_sequence",
                    session_id=session_model.session_id,
                    organization_id="org-a",
                    sequence_number=0,
                    type="start",
                    payload={},
                    created_at=now,
                ),
            )
            with pytest.raises(TimeoutError):
                future.result(timeout=0.2)
        appended = future.result(timeout=5)
    finally:
        executor.shutdown(wait=True)

    assert appended.sequence_number == 1
    events, total_count, _has_more = store.list_events(session_model.session_id, organization_id="org-a")
    assert total_count == 1
    assert events[0].event_id == "atlas_event_locked_sequence"


def test_atlas_store_append_message_rejects_wrong_organization(
    postgres_database_url_factory,
) -> None:
    from datetime import datetime, timezone

    from ruhu.atlas_models import AtlasMessage, AtlasSession
    from ruhu.atlas_store import SQLAlchemyAtlasStore

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    store = SQLAlchemyAtlasStore(session_factory)
    now = datetime.now(timezone.utc)
    session_model = AtlasSession(
        session_id="atlas_session_message_org_scope",
        organization_id="org-a",
        scope="agent_authoring",
        status="active",
        agent_id="sales",
        created_at=now,
        updated_at=now,
    )
    store.create_session(session_model)

    with pytest.raises(KeyError):
        store.append_message(
            AtlasMessage(
                message_id="atlas_message_wrong_org",
                session_id=session_model.session_id,
                organization_id="org-b",
                sequence_number=0,
                role="user",
                content="wrong org",
                created_at=now,
            )
        )

    messages, total_count, _has_more = store.list_messages(session_model.session_id, organization_id="org-a")
    assert messages == []
    assert total_count == 0


def test_atlas_store_cross_org_apply_permission_decisions_is_isolated(
    postgres_database_url_factory,
) -> None:
    """`apply_permission_decisions` must refuse to update rows belonging to
    a different organization, even if a caller knows the request_id."""
    from datetime import datetime, timedelta, timezone

    from ruhu.atlas_models import AtlasPermissionRequest, AtlasSession
    from ruhu.atlas_store import SQLAlchemyAtlasStore

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    store = SQLAlchemyAtlasStore(session_factory)

    now = datetime.now(timezone.utc)
    org_a = "org_alpha"
    org_b = "org_bravo"
    session_a = AtlasSession(
        session_id="atlas_session_org_a",
        organization_id=org_a,
        scope="agent_authoring",
        status="active",
        agent_id="sales",
        created_at=now,
        updated_at=now,
    )
    store.create_session(session_a)
    store.create_permission_request(
        AtlasPermissionRequest(
            request_id="atlas_perm_in_org_a",
            session_id=session_a.session_id,
            organization_id=org_a,
            kind="apply_deltas",
            status="pending",
            reason="cross-org-test",
            delta_ids=["d1"],
            created_at=now,
            expires_at=now,
        )
    )

    # Caller from org_b tries to approve org_a's request: the org filter
    # hides the row, so the id is treated as unknown and the call raises
    # (silent ignore would let a cross-org probe "succeed" with 200).
    with pytest.raises(ValueError, match="atlas_perm_in_org_a"):
        store.apply_permission_decisions(
            session_a.session_id,
            [{"request_id": "atlas_perm_in_org_a", "decision": "approved"}],
            organization_id=org_b,
            decided_by_user_id="org_b_admin",
        )

    # Sanity: row is still pending in org_a.
    rows = store.list_permission_requests(
        session_a.session_id,
        organization_id=org_a,
    )
    assert len(rows) == 1
    assert rows[0].status == "pending"
    assert rows[0].decided_by_user_id is None


def test_atlas_apply_binding_failure_reverts_document_and_reports(
    postgres_database_url_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The integration-binding apply saga: bindings are preflighted before any
    mutation, the document write happens first, and a binding failure reverts
    the document write and surfaces an honest error naming what executed.
    """
    from datetime import datetime, timedelta, timezone

    from ruhu.agent_document import AgentDocument
    from ruhu.atlas_coordinator import AtlasCoordinator
    from ruhu.atlas_coordinator import atlas_delta_payload_hash
    from ruhu.atlas_models import AtlasPermissionRequest, AtlasReviewDecisionRecord, AtlasSession
    from ruhu.atlas_protocol import (
        AtlasProposedChanges,
        IntegrationBindingDelta,
    )
    from ruhu.atlas_store import SQLAlchemyAtlasStore

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    store = SQLAlchemyAtlasStore(session_factory)
    registry = SQLAlchemyAgentRegistry(session_factory)
    coordinator = AtlasCoordinator(agent_registry=registry, atlas_store=store)

    org_id = "public"
    now = datetime.now(timezone.utc)
    session = AtlasSession(
        session_id="atlas_session_partial_apply",
        organization_id=org_id,
        scope="agent_authoring",
        status="active",
        agent_id="sales",
        created_at=now,
        updated_at=now,
    )
    store.create_session(session)

    binding_delta = IntegrationBindingDelta(
        delta_id="delta_partial_binding",
        target_id=session.agent_id,
        operation="update",
        change_type="bind_existing_connection",
        payload={
            "tool_ref": "demo.partial",
            "tool_definition_id": "tdef_demo_partial",
            "connection_id": "conn_demo_partial",
        },
        summary="Bind a connection with a side effect that survives status failure.",
    )
    store.replace_proposed_changes(
        session.session_id,
        AtlasProposedChanges(integration_binding_deltas=[binding_delta]),
        organization_id=org_id,
    )
    store.save_review_decisions(
        [
            AtlasReviewDecisionRecord(
                review_decision_id="atlas_review_partial",
                session_id=session.session_id,
                organization_id=org_id,
                delta_id=binding_delta.delta_id,
                decision="approved",
                delta_payload_hash=atlas_delta_payload_hash(binding_delta),
                note=None,
                decided_by_user_id="reviewer@acme.test",
                created_at=now,
            )
        ]
    )
    store.update_proposed_delta_statuses(
        session.session_id,
        {binding_delta.delta_id: "approved"},
        organization_id=org_id,
    )

    # Preflight is bypassed so the failure happens mid-binding-execution;
    # the saga must then revert the (already written) document and must not
    # mark any delta applied.
    monkeypatch.setattr(
        AtlasCoordinator,
        "_preflight_integration_binding_delta",
        lambda self, *, delta, organization_id: None,
    )

    def _exploding_binding(self, *, session, delta, organization_id):
        raise RuntimeError("simulated provider failure mid-apply")

    monkeypatch.setattr(
        AtlasCoordinator,
        "_apply_integration_binding_delta",
        _exploding_binding,
    )

    document_before = registry.get_agent_document("sales", organization_id=org_id)

    with pytest.raises(ValueError, match="reverted"):
        coordinator.apply_requested_deltas(
            session=session,
            delta_ids=[binding_delta.delta_id],
            organization_id=org_id,
        )

    # No delta was marked applied: the failing binding never executed.
    pending_changes = store.load_proposed_changes(
        session.session_id,
        organization_id=org_id,
    )
    pending_status = next(
        (delta.status for delta in pending_changes.integration_binding_deltas if delta.delta_id == binding_delta.delta_id),
        None,
    )
    assert pending_status == "approved", (
        "delta status must stay 'approved' when the binding apply failed; "
        f"got {pending_status!r}"
    )

    # The document write was reverted, so draft and integration state agree.
    document_after = registry.get_agent_document("sales", organization_id=org_id)
    assert document_after == document_before, (
        "draft document must be reverted when a binding delta fails mid-apply"
    )

    # Preflight alone must reject the same apply before any mutation when the
    # required stores are absent (this coordinator has no binding stores).
    monkeypatch.undo()
    with pytest.raises(ValueError, match="tool definition store"):
        coordinator.apply_requested_deltas(
            session=session,
            delta_ids=[binding_delta.delta_id],
            organization_id=org_id,
        )
    assert registry.get_agent_document("sales", organization_id=org_id) == document_before


@pytest.mark.asyncio
async def test_atlas_turn_is_not_idempotent_today(
    postgres_database_url_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Document the current (buggy) behaviour: submitting the same turn
    request twice creates duplicate user messages and event records. There
    is no client-supplied request_id or server-side dedupe.

    This test exists so that when idempotency is added (per the open gap
    in the Phase 5 plan) the failure here flags the exact spot to update —
    the new logic should drop or merge the duplicate instead of growing
    the message+event log linearly with each retry.
    """
    monkeypatch.delenv("RUHU_ATLAS_GENERATOR_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "agent_authoring", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]

        turn_payload = {
            "session_id": session_id,
            "message": "Tell me about this agent.",
            "selected_context": {"step_id": "discover"},
        }

        first = await client.post("/atlas/turns", json=turn_payload)
        assert first.status_code == 200
        second = await client.post("/atlas/turns", json=turn_payload)
        assert second.status_code == 200

        messages_resp = await client.get(f"/atlas/sessions/{session_id}/messages")
        assert messages_resp.status_code == 200
        user_messages = [
            item for item in messages_resp.json()["messages"] if item["role"] == "user"
        ]
        # Current behaviour: each submission appends an independent user
        # message. Once idempotency lands, this assertion changes to ==1.
        assert len(user_messages) == 2, (
            "If this dropped to 1 the idempotency gap is closed — update the "
            "assertion and adjust the docstring."
        )
        # Sanity: both messages carry the identical content the client sent,
        # confirming this is true duplication rather than a no-op retry.
        assert all(item["content"] == turn_payload["message"] for item in user_messages)


def test_atlas_session_creator_can_approve_own_permission_in_turn(
    postgres_database_url_factory,
) -> None:
    """Product decision: the safety contract is explicit user confirmation,
    not a second human. The session creator may approve permission requests
    for their own session, including via the `/atlas/turns`
    permission_decisions path that runs through `AtlasCoordinator.run_turn`."""
    from datetime import datetime, timedelta, timezone

    from ruhu.atlas_coordinator import AtlasCoordinator
    from ruhu.atlas_models import AtlasPermissionRequest, AtlasSession
    from ruhu.atlas_protocol import AtlasPermissionDecision, AtlasTurnRequest
    from ruhu.atlas_store import SQLAlchemyAtlasStore
    from ruhu.registry import SQLAlchemyAgentRegistry

    database_url = postgres_database_url_factory()
    # Bootstrap schema + load default agent fixtures.
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    store = SQLAlchemyAtlasStore(session_factory)
    registry = SQLAlchemyAgentRegistry(session_factory)
    coordinator = AtlasCoordinator(agent_registry=registry, atlas_store=store)

    org_id = "public"
    user_id = "alice@acme.test"
    now = datetime.now(timezone.utc)

    session = AtlasSession(
        session_id="atlas_session_self_approval",
        organization_id=org_id,
        scope="agent_authoring",
        status="active",
        agent_id="sales",
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    store.create_session(session)
    store.create_permission_request(
        AtlasPermissionRequest(
            request_id="atlas_perm_self_approval",
            session_id=session.session_id,
            organization_id=org_id,
            kind="apply_deltas",
            status="pending",
            reason="self-approval-test",
            delta_ids=["d1"],
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
    )

    approve_payload = AtlasTurnRequest(
        session_id=session.session_id,
        permission_decisions=[
            AtlasPermissionDecision(request_id="atlas_perm_self_approval", decision="approved")
        ],
    )
    coordinator.run_turn(
        session=session,
        payload=approve_payload,
        organization_id=org_id,
        user_id=user_id,
    )
    updated = store.list_permission_requests(
        session.session_id,
        organization_id=org_id,
        status="approved",
    )
    assert [item.request_id for item in updated] == ["atlas_perm_self_approval"]


@pytest.mark.asyncio
async def test_atlas_rollout_summary_reports_generator_review_and_apply_counters(
    postgres_database_url_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUHU_ATLAS_GENERATOR_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "agent_authoring", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]

        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": 'Rename this step to "Qualified lead"',
                "selected_context": {"step_id": "discover"},
            },
        )
        assert turn.status_code == 200
        delta_id = turn.json()["proposed_changes"]["step_deltas"][0]["delta_id"]

        review_turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "review_decisions": [{"delta_id": delta_id, "decision": "approved"}],
                "apply_request": {"delta_ids": [delta_id], "apply_note": "ship it"},
            },
        )
        assert review_turn.status_code == 200
        request_id = review_turn.json()["pending_permission_requests"][0]["request_id"]

        decisions = await client.post(
            f"/atlas/sessions/{session_id}/permission-decisions",
            json=[{"request_id": request_id, "decision": "approved"}],
        )
        assert decisions.status_code == 200

        apply_after_decision = await client.post(
            f"/atlas/sessions/{session_id}/apply",
            json={"delta_ids": [delta_id], "apply_note": "ship it"},
        )
        assert apply_after_decision.status_code == 200
        assert apply_after_decision.json()["status"] == "applied"

        # The HTTP endpoint is superuser-gated (cross-tenant counters); the
        # bootstrap client carries no principal, so it gets 401. Counter
        # assertions read the process-wide metrics via a coordinator instance.
        summary = await client.get("/atlas/admin/rollout-summary")
        assert summary.status_code == 401

        from ruhu.atlas_coordinator import AtlasCoordinator
        from ruhu.registry import SQLAlchemyAgentRegistry as _Registry
        from ruhu.atlas_store import SQLAlchemyAtlasStore as _Store

        session_factory = build_session_factory(database_url)
        coordinator = AtlasCoordinator(
            agent_registry=_Registry(session_factory),
            atlas_store=_Store(session_factory),
        )
        payload = coordinator.rollout_summary().model_dump(mode="json")
        assert payload["policy"] == {
            "min_anthropic_generated_candidates": 200,
            "min_reviewed_deltas": 50,
            "min_apply_attempts": 20,
            "min_anthropic_success_rate": 0.95,
            "min_review_approval_rate": 0.7,
            "min_apply_success_rate": 0.95,
            "max_fallback_rate": 0.1,
            "min_semantic_validation_pass_rate": 0.9,
        }
        assert "low_risk_updates" in payload["heuristic_enabled_families"]
        family_summary = next(item for item in payload["family_summaries"] if item["family"] == "low_risk_updates")
        assert family_summary["heuristic_enabled"] is True
        assert family_summary["generated_candidates"] >= 1
        assert family_summary["anthropic_generated_candidates"] == 0
        assert family_summary["fallback_generated_candidates"] >= 1
        assert family_summary["approved_reviews"] >= 1
        assert family_summary["applied_deltas"] >= 1
        assert family_summary["approval_rate"] is not None
        assert family_summary["apply_success_rate"] is not None
        assert family_summary["rollout_status"] == "not_enough_data"
        assert family_summary["rollout_reasons"]
        assert any(
            item["labels"] == {"provider": "anthropic", "model": "claude-3-7-sonnet-latest", "outcome": "success"}
            or item["labels"] == {"provider": "anthropic", "model": "claude-3-7-sonnet-latest", "outcome": "error"}
            or item["labels"] == {"provider": "anthropic", "model": "claude-3-7-sonnet-latest", "outcome": "empty"}
            or item["labels"] == {"provider": "anthropic", "model": "claude-3-7-sonnet-latest", "outcome": "parse_error"}
            for item in payload["generator_requests"]
        ) or any(item["labels"] == {"reason": "missing_api_key"} for item in payload["generator_fallbacks"])
        assert any(
            item["labels"] == {"mode": "fallback", "family": "low_risk_updates"} and item["value"] >= 1
            for item in payload["generated_delta_candidates"]
        )
        assert any(
            item["labels"] == {"family": "low_risk_updates", "decision": "approved"} and item["value"] >= 1
            for item in payload["review_decisions"]
        )
        assert any(
            item["labels"] == {"family": "low_risk_updates", "outcome": "applied"} and item["value"] >= 1
            for item in payload["apply_outcomes"]
        )


@pytest.mark.asyncio
async def test_atlas_turn_proposes_authored_deltas(postgres_database_url_factory) -> None:
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "agent_authoring", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]

        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": 'Rename this step to "Qualify lead" and change this step say to "Tell me about your team."',
                "selected_context": {"step_id": "discover"},
            },
        )
        assert turn.status_code == 200
        payload = turn.json()
        assert payload["next_action"] == "ready_to_review_changes"
        assert len(payload["proposed_changes"]["step_deltas"]) == 2
        rename_delta = next(
            item for item in payload["proposed_changes"]["step_deltas"] if item["change_type"] == "rename_step"
        )
        say_delta = next(
            item for item in payload["proposed_changes"]["step_deltas"] if item["change_type"] == "update_step_say"
        )
        assert rename_delta["payload"]["name"] == "Qualify lead"
        assert say_delta["payload"]["say"] == "Tell me about your team."
        assert payload["review_state"]["pending_delta_ids"]

        follow_up = await client.post(
            "/atlas/turns",
            json={"session_id": session_id, "message": "What changes are pending?"},
        )
        assert follow_up.status_code == 200
        follow_up_payload = follow_up.json()
        assert follow_up_payload["next_action"] == "ready_to_review_changes"
        assert sorted(follow_up_payload["review_state"]["pending_delta_ids"]) == sorted(
            [rename_delta["delta_id"], say_delta["delta_id"]]
        )


@pytest.mark.asyncio
async def test_atlas_heuristic_family_gating_filters_disabled_delta_families(
    postgres_database_url_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUHU_ATLAS_GENERATOR_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("RUHU_ATLAS_HEURISTIC_ENABLED_FAMILIES", "low_risk_updates")
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "agent_authoring", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]

        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": 'Rename this step to "Qualify lead" and add tool "crm.lookup"',
                "selected_context": {"step_id": "discover"},
            },
        )
        assert turn.status_code == 200
        payload = turn.json()
        assert payload["generator"] == {"mode": "fallback", "model": None}
        step_deltas = payload["proposed_changes"]["step_deltas"]
        assert [item["change_type"] for item in step_deltas] == ["rename_step"]
        assert step_deltas[0]["payload"]["name"] == "Qualify lead"


@pytest.mark.asyncio
async def test_atlas_provisioning_turn_parses_pasted_openapi_schema(postgres_database_url_factory) -> None:
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "provisioning", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]

        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": "Review this API for provisioning.",
                "api_discovery_requests": [
                    {
                        "request_id": "disc_1",
                        "source_type": "pasted_schema",
                        "source_value": """
{
  "openapi": "3.0.0",
  "info": {"title": "CRM API"},
  "servers": [{"url": "https://crm.example.com"}],
  "paths": {
    "/contacts": {
      "get": {"operationId": "listContacts", "summary": "List contacts"}
    },
    "/contacts/{id}": {
      "get": {"operationId": "getContact", "summary": "Get contact"}
    }
  },
  "components": {
    "securitySchemes": {
      "api_key": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
    }
  }
}
""",
                        "intent": "import CRM operations",
                    }
                ],
            },
        )
        assert turn.status_code == 200
        payload = turn.json()
        assert payload["next_action"] == "ready_to_provision"
        discovery = payload["api_discovery_results"][0]
        assert discovery["status"] == "discovered"
        assert discovery["spec_type"] == "openapi"
        assert discovery["provider_name"] == "CRM API"
        assert discovery["base_url"] == "https://crm.example.com"
        assert discovery["missing_auth_fields"] == ["api_key"]
        assert len(discovery["candidate_endpoints"]) == 2
        assert set(discovery["candidate_tool_refs"]) == {"listContacts", "getContact"}
        assert discovery["provisioning_candidates"]
        candidate = discovery["provisioning_candidates"][0]
        assert candidate["provider_slug"] == "custom_api"
        assert candidate["setup_url"] == "/settings/integrations?provider=custom_api&auth_type=api_key"


@pytest.mark.asyncio
async def test_atlas_provisioning_turn_fetches_remote_openapi_url(
    postgres_database_url_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        headers = {"content-type": "application/json"}
        text = """
{
  "openapi": "3.0.0",
  "info": {"title": "Billing API"},
  "servers": [{"url": "https://billing.example.com"}],
  "paths": {
    "/invoices": {
      "get": {"operationId": "listInvoices", "summary": "List invoices"}
    }
  }
}
"""

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(
        "ruhu.atlas_provisioning._fetch_remote_text",
        lambda *args, **kwargs: (_Response().text, _Response.headers.get("content-type")),
    )
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "provisioning", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]
        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": "Inspect this billing API.",
                "api_discovery_requests": [
                    {
                        "request_id": "disc_remote_openapi",
                        "source_type": "openapi_url",
                        "source_value": "https://example.com/openapi.json",
                    }
                ],
            },
        )
        assert turn.status_code == 200
        discovery = turn.json()["api_discovery_results"][0]
        assert discovery["status"] == "discovered"
        assert discovery["spec_type"] == "openapi"
        assert discovery["provider_name"] == "Billing API"
        assert discovery["base_url"] == "https://billing.example.com"
        assert discovery["candidate_tool_refs"] == ["listInvoices"]
        candidate = discovery["provisioning_candidates"][0]
        assert candidate["provider_slug"] is None
        assert candidate["setup_url"] is None
        assert candidate["documentation_url"] == "https://example.com/openapi.json"


@pytest.mark.asyncio
async def test_atlas_provisioning_turn_maps_discovered_provider_to_template_setup(
    postgres_database_url_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        headers = {"content-type": "application/json"}
        text = """
{
  "openapi": "3.0.0",
  "info": {"title": "HubSpot CRM API"},
  "servers": [{"url": "https://api.hubapi.com"}],
  "components": {
    "securitySchemes": {
      "oauth": {"type": "oauth2", "flows": {}}
    }
  },
  "paths": {
    "/crm/v3/objects/contacts": {
      "get": {"operationId": "crm.get_contact", "summary": "Get contact"}
    }
  }
}
"""

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(
        "ruhu.atlas_provisioning._fetch_remote_text",
        lambda *args, **kwargs: (_Response().text, _Response.headers.get("content-type")),
    )
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "provisioning", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]
        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": "Inspect this CRM API.",
                "api_discovery_requests": [
                    {
                        "request_id": "disc_remote_hubspot",
                        "source_type": "openapi_url",
                        "source_value": "https://developers.hubspot.com/openapi.json",
                    }
                ],
            },
        )
        assert turn.status_code == 200
        discovery = turn.json()["api_discovery_results"][0]
        assert discovery["status"] == "discovered"
        assert discovery["missing_auth_fields"] == ["oauth_authorization"]
        candidate = discovery["provisioning_candidates"][0]
        assert candidate["provider_slug"] == "hubspot"
        assert candidate["setup_url"] == "/settings/integrations?provider=hubspot"
        assert "HubSpot integration" in candidate["suggested_setup_action"]
        assert candidate["documentation_url"] == "https://developers.hubspot.com/openapi.json"


@pytest.mark.asyncio
async def test_atlas_provisioning_turn_parses_remote_docs_page(
    postgres_database_url_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        headers = {"content-type": "text/html; charset=utf-8"}
        text = """
<html>
  <head><title>Acme CRM Developer Docs</title></head>
  <body>
    <h1>Authentication</h1>
    <p>Use an API key in the Authorization header.</p>
    <code>GET /contacts</code>
    <code>POST /contacts/{id}/notes</code>
  </body>
</html>
"""

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(
        "ruhu.atlas_provisioning._fetch_remote_text",
        lambda *args, **kwargs: (_Response().text, _Response.headers.get("content-type")),
    )
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "provisioning", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]
        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": "Inspect these docs.",
                "api_discovery_requests": [
                    {
                        "request_id": "disc_remote_docs",
                        "source_type": "website_url",
                        "source_value": "https://docs.example.com/crm",
                    }
                ],
            },
        )
        assert turn.status_code == 200
        discovery = turn.json()["api_discovery_results"][0]
        assert discovery["status"] == "discovered"
        # No Anthropic key in test env → docs-page path falls back to the
        # regex heuristic and labels itself accordingly. (LLM-parsed
        # behaviour is covered in test_atlas_docs_parser.py.)
        assert discovery["spec_type"] == "heuristic"
        assert discovery["provider_name"] == "Acme CRM Developer Docs"
        assert discovery["base_url"] == "https://docs.example.com/crm"
        assert discovery["missing_auth_fields"] == ["api_key"]
        assert {item["path"] for item in discovery["candidate_endpoints"]} == {"/contacts", "/contacts/{id}/notes"}
        assert set(discovery["candidate_tool_refs"]) == {"get_contacts", "post_contacts_id_notes"}
        candidate = discovery["provisioning_candidates"][0]
        assert candidate["provider_slug"] == "custom_api"
        assert candidate["setup_url"] == "/settings/integrations?provider=custom_api&auth_type=api_key"
        assert candidate["documentation_url"] == "https://docs.example.com/crm"


@pytest.mark.asyncio
async def test_atlas_provisioning_turn_infers_bearer_auth_from_remote_openapi(
    postgres_database_url_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        headers = {"content-type": "application/yaml"}
        text = """
openapi: 3.0.0
info:
  title: Reporting API
servers:
  - url: https://reporting.example.com
components:
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
paths:
  /reports:
    get:
      operationId: listReports
      summary: List reports
"""

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(
        "ruhu.atlas_provisioning._fetch_remote_text",
        lambda *args, **kwargs: (_Response().text, _Response.headers.get("content-type")),
    )
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "provisioning", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]
        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": "Inspect this reporting API.",
                "api_discovery_requests": [
                    {
                        "request_id": "disc_remote_bearer_openapi",
                        "source_type": "openapi_url",
                        "source_value": "https://example.com/reporting.yaml",
                    }
                ],
            },
        )
        assert turn.status_code == 200
        discovery = turn.json()["api_discovery_results"][0]
        assert discovery["status"] == "discovered"
        assert discovery["missing_auth_fields"] == ["bearer_token"]
        candidate = discovery["provisioning_candidates"][0]
        assert candidate["missing_fields"] == ["bearer_token"]
        assert candidate["provider_slug"] == "custom_api"
        assert candidate["setup_url"] == "/settings/integrations?provider=custom_api&auth_type=bearer"


@pytest.mark.asyncio
async def test_atlas_provisioning_turn_infers_postman_auth_requirements(
    postgres_database_url_factory,
) -> None:
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "provisioning", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]
        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": "Review this collection for provisioning.",
                "api_discovery_requests": [
                    {
                        "request_id": "disc_postman_auth",
                        "source_type": "pasted_postman",
                        "source_value": """
{
  "info": {"name": "Support APIs"},
  "auth": {"type": "bearer"},
  "item": [
    {
      "name": "List tickets",
      "request": {
        "method": "GET",
        "url": {"raw": "https://support.example.com/tickets"}
      }
    }
  ]
}
""",
                    }
                ],
            },
        )
        assert turn.status_code == 200
        discovery = turn.json()["api_discovery_results"][0]
        assert discovery["status"] == "discovered"
        assert discovery["spec_type"] == "postman"
        assert discovery["missing_auth_fields"] == ["bearer_token"]
        assert discovery["candidate_endpoints"][0]["requires_auth"] is True
        candidate = discovery["provisioning_candidates"][0]
        assert candidate["requires_credentials"] is True
        assert candidate["missing_fields"] == ["bearer_token"]
        assert candidate["provider_slug"] == "custom_api"
        assert candidate["setup_url"] == "/settings/integrations?provider=custom_api&auth_type=bearer"


@pytest.mark.asyncio
async def test_atlas_provisioning_turn_rejects_unsupported_remote_content_type(
    postgres_database_url_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        headers = {"content-type": "image/png"}
        text = "not an image but should still be rejected by content type"

        def raise_for_status(self) -> None:
            return None

    def _raise_fetch_error(*args, **kwargs):
        raise ValueError("unsupported remote content type: image/png")

    monkeypatch.setattr("ruhu.atlas_provisioning._fetch_remote_text", _raise_fetch_error)
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "provisioning", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]
        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": "Inspect this remote source.",
                "api_discovery_requests": [
                    {
                        "request_id": "disc_remote_bad_type",
                        "source_type": "openapi_url",
                        "source_value": "https://example.com/image.png",
                    }
                ],
            },
        )
        assert turn.status_code == 200
        discovery = turn.json()["api_discovery_results"][0]
        assert discovery["status"] == "failed"
        assert "unsupported remote content type" in discovery["notes"]


@pytest.mark.asyncio
async def test_atlas_provisioning_turn_rejects_oversized_remote_content(
    postgres_database_url_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        headers = {"content-type": "application/json"}
        text = "x" * 1_000_001

        def raise_for_status(self) -> None:
            return None

    def _raise_fetch_error(*args, **kwargs):
        raise ValueError("remote content exceeds Atlas fetch limit (1000001 bytes > 1000000)")

    monkeypatch.setattr("ruhu.atlas_provisioning._fetch_remote_text", _raise_fetch_error)
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "provisioning", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]
        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": "Inspect this remote source.",
                "api_discovery_requests": [
                    {
                        "request_id": "disc_remote_too_large",
                        "source_type": "openapi_url",
                        "source_value": "https://example.com/huge.json",
                    }
                ],
            },
        )
        assert turn.status_code == 200
        discovery = turn.json()["api_discovery_results"][0]
        assert discovery["status"] == "failed"
        assert "exceeds Atlas fetch limit" in discovery["notes"]


def test_atlas_remote_fetch_rejects_private_and_internal_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    from ruhu.atlas_provisioning import _validate_remote_fetch_url

    with pytest.raises(ValueError, match="non-public address"):
        _validate_remote_fetch_url("http://169.254.169.254/latest/meta-data")
    with pytest.raises(ValueError, match="not allowed|internal"):
        _validate_remote_fetch_url("http://localhost:8010/health")

    def _fake_getaddrinfo(*args, **kwargs):
        return [(None, None, None, None, ("127.0.0.1", 443))]

    monkeypatch.setattr("ruhu.atlas_provisioning.socket.getaddrinfo", _fake_getaddrinfo)
    with pytest.raises(ValueError, match="non-public address"):
        _validate_remote_fetch_url("https://docs.example.com/openapi.json")


def test_atlas_remote_fetch_enforces_streaming_byte_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from ruhu.atlas_provisioning import _fetch_remote_text

    class _Response:
        status_code = 200
        headers = {"content-type": "application/json"}
        encoding = "utf-8"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self):
            yield b"x" * 600_000
            yield b"x" * 600_001

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def stream(self, *args, **kwargs):
            return _Response()

    monkeypatch.setattr("ruhu.atlas_provisioning._validate_remote_fetch_url", lambda url: url)
    monkeypatch.setattr("ruhu.atlas_provisioning.httpx.Client", _Client)
    # Pinning now fails closed (AR-1.2), so the host must resolve; pin it to a
    # public IP so the stream (and its byte-limit) is what the test exercises.
    monkeypatch.setattr(
        "ruhu.atlas_provisioning.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )

    with pytest.raises(ValueError, match="remote content exceeds Atlas fetch limit"):
        _fetch_remote_text("https://docs.example.com/openapi.json")


@pytest.mark.asyncio
async def test_atlas_provisioning_can_review_and_apply_provider_template_setup(
    postgres_database_url_factory,
    credential_cipher,
) -> None:
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "provisioning", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]
        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": "Set up this HubSpot API for the agent.",
                "api_discovery_requests": [
                    {
                        "request_id": "disc_hubspot_setup",
                        "source_type": "pasted_schema",
                        "source_value": """
{
  "openapi": "3.0.0",
  "info": {"title": "HubSpot CRM API"},
  "servers": [{"url": "https://api.hubapi.com"}],
  "components": {
    "securitySchemes": {
      "oauth": {"type": "oauth2", "flows": {}}
    }
  },
  "paths": {
    "/crm/v3/objects/contacts": {
      "get": {"operationId": "crm.get_contact", "summary": "Get contact"}
    }
  }
}
""",
                    }
                ],
            },
        )
        assert turn.status_code == 200
        payload = turn.json()
        assert payload["next_action"] == "ready_to_review_changes"
        delta = payload["proposed_changes"]["integration_binding_deltas"][0]
        assert delta["change_type"] == "provision_provider_template"
        delta_id = delta["delta_id"]

        review_turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "review_decisions": [{"delta_id": delta_id, "decision": "approved"}],
                "apply_request": {"delta_ids": [delta_id], "apply_note": "set up hubspot"},
            },
        )
        assert review_turn.status_code == 200
        request_id = review_turn.json()["pending_permission_requests"][0]["request_id"]

        decisions = await client.post(
            f"/atlas/sessions/{session_id}/permission-decisions",
            json=[{"request_id": request_id, "decision": "approved"}],
        )
        assert decisions.status_code == 200

        apply_after_decision = await client.post(
            f"/atlas/sessions/{session_id}/apply",
            json={"delta_ids": [delta_id], "apply_note": "set up hubspot"},
        )
        assert apply_after_decision.status_code == 200
        assert apply_after_decision.json()["status"] == "applied"

    session_factory = build_session_factory(database_url)
    connection_store = APIConnectionStore(session_factory, blob_cipher=credential_cipher)
    definition_store = ToolDefinitionStore(session_factory)
    assignment_store = ToolAgentAssignmentStore(session_factory)
    connections = connection_store.list_for_org("public")
    assert any(item.provider == "hubspot" for item in connections)
    assert definition_store.get_by_ref("public", "crm.get_contact") is not None
    assignments = assignment_store.list_for_agent("public", "sales")
    assigned_definition_ids = {item.tool_definition_id for item in assignments}
    assert any(
        definition.tool_definition_id in assigned_definition_ids
        for definition in definition_store.list_for_org("public")
        if definition.tool_ref == "crm.get_contact"
    )


@pytest.mark.asyncio
async def test_atlas_provisioning_can_review_and_apply_openapi_ingestion(
    postgres_database_url_factory,
    credential_cipher,
) -> None:
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "provisioning", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]
        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": "Import this API for the agent.",
                "api_discovery_requests": [
                    {
                        "request_id": "disc_import_billing",
                        "source_type": "pasted_schema",
                        "source_value": """
{
  "openapi": "3.0.0",
  "info": {"title": "Billing API"},
  "servers": [{"url": "https://billing.example.com"}],
  "paths": {
    "/invoices": {
      "get": {
        "operationId": "listInvoices",
        "summary": "List invoices",
        "responses": {"200": {"description": "ok"}}
      }
    }
  }
}
""",
                    }
                ],
            },
        )
        assert turn.status_code == 200
        payload = turn.json()
        assert payload["next_action"] == "ready_to_review_changes"
        delta = payload["proposed_changes"]["integration_binding_deltas"][0]
        assert delta["change_type"] == "ingest_openapi_tools"
        delta_id = delta["delta_id"]

        review_turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "review_decisions": [{"delta_id": delta_id, "decision": "approved"}],
                "apply_request": {"delta_ids": [delta_id], "apply_note": "import billing api"},
            },
        )
        assert review_turn.status_code == 200
        request_id = review_turn.json()["pending_permission_requests"][0]["request_id"]

        decisions = await client.post(
            f"/atlas/sessions/{session_id}/permission-decisions",
            json=[{"request_id": request_id, "decision": "approved"}],
        )
        assert decisions.status_code == 200

        apply_after_decision = await client.post(
            f"/atlas/sessions/{session_id}/apply",
            json={"delta_ids": [delta_id], "apply_note": "import billing api"},
        )
        assert apply_after_decision.status_code == 200
        assert apply_after_decision.json()["status"] == "applied"

    session_factory = build_session_factory(database_url)
    connection_store = APIConnectionStore(session_factory, blob_cipher=credential_cipher)
    definition_store = ToolDefinitionStore(session_factory)
    assignment_store = ToolAgentAssignmentStore(session_factory)
    connections = connection_store.list_for_org("public")
    assert any(item.provider == "openapi" for item in connections)
    imported = definition_store.get_by_ref("public", "billing_api.listinvoices")
    assert imported is not None
    assignments = assignment_store.list_for_agent("public", "sales")
    assert any(item.tool_definition_id == imported.tool_definition_id for item in assignments)


@pytest.mark.asyncio
async def test_atlas_provisioning_can_review_and_bind_existing_connection(
    postgres_database_url_factory,
    credential_cipher,
) -> None:
    database_url = postgres_database_url_factory()
    session_factory = build_session_factory(database_url)
    connection_store = APIConnectionStore(session_factory, blob_cipher=credential_cipher)
    definition_store = ToolDefinitionStore(session_factory)
    binding_store = AgentToolBindingStore(session_factory)
    assignment_store = ToolAgentAssignmentStore(session_factory)
    connection = connection_store.create(
        organization_id="public",
        display_name="HubSpot Sandbox",
        provider="hubspot",
        auth_type="oauth2",
        base_url="https://api.hubapi.com",
    )
    definition = definition_store.create(
        organization_id="public",
        connection_id=None,
        kind="integration",
        tool_ref="crm.lookup",
        function_name="lookup_contact",
        display_name="Lookup CRM Contact",
        description="Look up CRM contacts by email.",
        endpoint_path="/crm/v3/objects/contacts/search",
        http_method="POST",
        metadata={"template_slug": "hubspot"},
        read_only=True,
    )
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        existing = await client.get("/agents/sales/agent-document")
        assert existing.status_code == 200
        document = existing.json()["document"]
        discover = next(step for scenario in document["scenarios"] for step in scenario["steps"] if step["id"] == "discover")
        discover["tool_policy"].append(
            {
                "ref": "crm.lookup",
                "mode": "required",
                "invocation_strategy": "always",
                "timeout_ms": None,
                "event_name": "crm_lookup",
                "args": {},
            }
        )
        update = await client.put("/agents/sales/agent-document", json=document)
        assert update.status_code == 200

        start = await client.post(
            "/atlas/sessions",
            json={"scope": "provisioning", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]

        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": "Connect the existing HubSpot integration to the CRM lookup tool.",
            },
        )
        assert turn.status_code == 200
        payload = turn.json()
        assert payload["next_action"] == "ready_to_review_changes"
        delta = payload["proposed_changes"]["integration_binding_deltas"][0]
        assert delta["change_type"] == "bind_existing_connection"
        assert delta["payload"]["connection_id"] == connection.connection_id
        assert delta["payload"]["tool_definition_id"] == definition.tool_definition_id
        assert delta["payload"]["connection_display_name"] == "HubSpot Sandbox"
        assert delta["payload"]["setup_url"] == f"/settings/integrations?connection_id={connection.connection_id}"
        assert "crm.objects.contacts.read" in delta["payload"]["required_scopes"]
        delta_id = delta["delta_id"]

        review_turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "review_decisions": [{"delta_id": delta_id, "decision": "approved"}],
                "apply_request": {"delta_ids": [delta_id], "apply_note": "bind existing hubspot"},
            },
        )
        assert review_turn.status_code == 200
        request_id = review_turn.json()["pending_permission_requests"][0]["request_id"]

        decisions = await client.post(
            f"/atlas/sessions/{session_id}/permission-decisions",
            json=[{"request_id": request_id, "decision": "approved"}],
        )
        assert decisions.status_code == 200

        apply_after_decision = await client.post(
            f"/atlas/sessions/{session_id}/apply",
            json={"delta_ids": [delta_id], "apply_note": "bind existing hubspot"},
        )
        assert apply_after_decision.status_code == 200
        assert apply_after_decision.json()["status"] == "applied"

    binding = binding_store.get_override(agent_id="sales", tool_definition_id=definition.tool_definition_id)
    assert binding is not None
    assert binding.connection_id == connection.connection_id
    assignments = assignment_store.list_for_agent("public", "sales")
    assert any(item.tool_definition_id == definition.tool_definition_id for item in assignments)


def test_atlas_provisioning_rejects_cross_org_existing_connection_binding(
    postgres_database_url_factory,
    credential_cipher,
) -> None:
    from datetime import datetime, timezone

    from ruhu.atlas_coordinator import AtlasCoordinator
    from ruhu.atlas_coordinator import atlas_delta_payload_hash
    from ruhu.atlas_models import AtlasReviewDecisionRecord, AtlasSession
    from ruhu.atlas_protocol import AtlasProposedChanges, IntegrationBindingDelta
    from ruhu.atlas_store import SQLAlchemyAtlasStore

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    store = SQLAlchemyAtlasStore(session_factory)
    registry = SQLAlchemyAgentRegistry(session_factory)
    connection_store = APIConnectionStore(session_factory, blob_cipher=credential_cipher)
    definition_store = ToolDefinitionStore(session_factory)
    binding_store = AgentToolBindingStore(session_factory)
    foreign_connection = connection_store.create(
        organization_id="other-org",
        display_name="Foreign HubSpot",
        provider="hubspot",
        auth_type="oauth2",
        base_url="https://api.hubapi.com",
    )
    definition = definition_store.create(
        organization_id="public",
        connection_id=None,
        kind="integration",
        tool_ref="crm.lookup",
        function_name="lookup_contact",
        display_name="Lookup CRM Contact",
        description="Look up CRM contacts by email.",
        endpoint_path="/crm/v3/objects/contacts/search",
        http_method="POST",
        metadata={"template_slug": "hubspot"},
        read_only=True,
    )
    coordinator = AtlasCoordinator(
        agent_registry=registry,
        atlas_store=store,
        connection_store=connection_store,
        definition_store=definition_store,
        binding_store=binding_store,
    )

    now = datetime.now(timezone.utc)
    session = AtlasSession(
        session_id="atlas_session_cross_org_bind",
        organization_id="public",
        scope="provisioning",
        status="active",
        agent_id="sales",
        created_at=now,
        updated_at=now,
    )
    store.create_session(session)
    delta = IntegrationBindingDelta(
        delta_id="atlas_delta_cross_org_bind",
        target_id="sales",
        operation="update",
        change_type="bind_existing_connection",
        payload={
            "tool_definition_id": definition.tool_definition_id,
            "connection_id": foreign_connection.connection_id,
        },
        summary="Bind a foreign connection.",
    )
    store.replace_proposed_changes(
        session.session_id,
        AtlasProposedChanges(integration_binding_deltas=[delta]),
        organization_id="public",
    )
    store.save_review_decisions(
        [
            AtlasReviewDecisionRecord(
                review_decision_id="atlas_review_cross_org_bind",
                session_id=session.session_id,
                organization_id="public",
                delta_id=delta.delta_id,
                decision="approved",
                delta_payload_hash=atlas_delta_payload_hash(delta),
                note=None,
                decided_by_user_id="reviewer",
                created_at=now,
            )
        ]
    )
    store.update_proposed_delta_statuses(
        session.session_id,
        {delta.delta_id: "approved"},
        organization_id="public",
    )

    with pytest.raises(ValueError, match="unknown connection"):
        coordinator.apply_requested_deltas(
            session=session,
            delta_ids=[delta.delta_id],
            organization_id="public",
        )
    assert binding_store.get_override(agent_id="sales", tool_definition_id=definition.tool_definition_id) is None


@pytest.mark.asyncio
async def test_atlas_provisioning_can_review_and_apply_connection_reauthorization(
    postgres_database_url_factory,
    credential_cipher,
) -> None:
    database_url = postgres_database_url_factory()
    session_factory = build_session_factory(database_url)
    connection_store = APIConnectionStore(session_factory, blob_cipher=credential_cipher)
    definition_store = ToolDefinitionStore(session_factory)
    connection = connection_store.create(
        organization_id="public",
        display_name="HubSpot Sandbox",
        provider="hubspot",
        auth_type="oauth2",
        base_url="https://api.hubapi.com",
    )
    connection_store.update(
        connection.connection_id,
        status="needs_auth",
        error_message="oauth grant expired",
    )
    definition_store.create(
        organization_id="public",
        connection_id=connection.connection_id,
        kind="integration",
        tool_ref="crm.lookup",
        function_name="lookup_contact",
        display_name="Lookup CRM Contact",
        description="Look up CRM contacts by email.",
        endpoint_path="/crm/v3/objects/contacts/search",
        http_method="POST",
        metadata={"template_slug": "hubspot"},
        read_only=True,
    )
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        existing = await client.get("/agents/sales/agent-document")
        assert existing.status_code == 200
        document = existing.json()["document"]
        discover = next(step for scenario in document["scenarios"] for step in scenario["steps"] if step["id"] == "discover")
        discover["tool_policy"].append(
            {
                "ref": "crm.lookup",
                "mode": "required",
                "invocation_strategy": "always",
                "timeout_ms": None,
                "event_name": "crm_lookup",
                "args": {},
            }
        )
        update = await client.put("/agents/sales/agent-document", json=document)
        assert update.status_code == 200

        start = await client.post(
            "/atlas/sessions",
            json={"scope": "provisioning", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]

        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": "Re-authorize the HubSpot connection for this tool.",
            },
        )
        assert turn.status_code == 200
        payload = turn.json()
        assert payload["next_action"] == "ready_to_review_changes"
        delta = payload["proposed_changes"]["integration_binding_deltas"][0]
        assert delta["change_type"] == "reauthorize_connection"
        assert delta["payload"]["connection_id"] == connection.connection_id
        delta_id = delta["delta_id"]

        review_turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "review_decisions": [{"delta_id": delta_id, "decision": "approved"}],
                "apply_request": {"delta_ids": [delta_id], "apply_note": "reauthorize hubspot"},
            },
        )
        assert review_turn.status_code == 200
        request_id = review_turn.json()["pending_permission_requests"][0]["request_id"]

        decisions = await client.post(
            f"/atlas/sessions/{session_id}/permission-decisions",
            json=[{"request_id": request_id, "decision": "approved"}],
        )
        assert decisions.status_code == 200

        apply_after_decision = await client.post(
            f"/atlas/sessions/{session_id}/apply",
            json={"delta_ids": [delta_id], "apply_note": "reauthorize hubspot"},
        )
        assert apply_after_decision.status_code == 200
        assert apply_after_decision.json()["status"] == "applied"

    updated = connection_store.get(connection.connection_id)
    assert updated is not None
    assert updated.status == "needs_auth"
    assert updated.metadata_json["atlas_action_kind"] == "reauthorize_connection"
    assert updated.metadata_json["atlas_action_tool_ref"] == "crm.lookup"


def test_atlas_provisioning_rejects_cross_org_connection_reauthorization(
    postgres_database_url_factory,
    credential_cipher,
) -> None:
    from datetime import datetime, timezone

    from ruhu.atlas_coordinator import AtlasCoordinator
    from ruhu.atlas_coordinator import atlas_delta_payload_hash
    from ruhu.atlas_models import AtlasReviewDecisionRecord, AtlasSession
    from ruhu.atlas_protocol import AtlasProposedChanges, IntegrationBindingDelta
    from ruhu.atlas_store import SQLAlchemyAtlasStore

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    store = SQLAlchemyAtlasStore(session_factory)
    registry = SQLAlchemyAgentRegistry(session_factory)
    connection_store = APIConnectionStore(session_factory, blob_cipher=credential_cipher)
    definition_store = ToolDefinitionStore(session_factory)
    foreign_connection = connection_store.create(
        organization_id="other-org",
        display_name="Foreign HubSpot",
        provider="hubspot",
        auth_type="oauth2",
        base_url="https://api.hubapi.com",
    )
    coordinator = AtlasCoordinator(
        agent_registry=registry,
        atlas_store=store,
        connection_store=connection_store,
        definition_store=definition_store,
    )

    now = datetime.now(timezone.utc)
    session = AtlasSession(
        session_id="atlas_session_cross_org_reauth",
        organization_id="public",
        scope="provisioning",
        status="active",
        agent_id="sales",
        created_at=now,
        updated_at=now,
    )
    store.create_session(session)
    delta = IntegrationBindingDelta(
        delta_id="atlas_delta_cross_org_reauth",
        target_id="sales",
        operation="update",
        change_type="reauthorize_connection",
        payload={"connection_id": foreign_connection.connection_id, "tool_ref": "crm.lookup"},
        summary="Reauthorize a foreign connection.",
    )
    store.replace_proposed_changes(
        session.session_id,
        AtlasProposedChanges(integration_binding_deltas=[delta]),
        organization_id="public",
    )
    store.save_review_decisions(
        [
            AtlasReviewDecisionRecord(
                review_decision_id="atlas_review_cross_org_reauth",
                session_id=session.session_id,
                organization_id="public",
                delta_id=delta.delta_id,
                decision="approved",
                delta_payload_hash=atlas_delta_payload_hash(delta),
                note=None,
                decided_by_user_id="reviewer",
                created_at=now,
            )
        ]
    )
    store.update_proposed_delta_statuses(
        session.session_id,
        {delta.delta_id: "approved"},
        organization_id="public",
    )

    with pytest.raises(ValueError, match="unknown connection"):
        coordinator.apply_requested_deltas(
            session=session,
            delta_ids=[delta.delta_id],
            organization_id="public",
        )
    updated = connection_store.get(foreign_connection.connection_id)
    assert updated is not None
    assert updated.metadata_json == {}


@pytest.mark.asyncio
async def test_atlas_provisioning_can_review_and_apply_connection_repair(
    postgres_database_url_factory,
    credential_cipher,
) -> None:
    database_url = postgres_database_url_factory()
    session_factory = build_session_factory(database_url)
    connection_store = APIConnectionStore(session_factory, blob_cipher=credential_cipher)
    definition_store = ToolDefinitionStore(session_factory)
    connection = connection_store.create(
        organization_id="public",
        display_name="Billing API",
        provider="custom_api",
        auth_type="api_key",
        base_url="https://billing.example.com",
    )
    connection_store.update(
        connection.connection_id,
        status="error",
        error_message="credential mismatch",
    )
    definition_store.create(
        organization_id="public",
        connection_id=connection.connection_id,
        kind="integration",
        tool_ref="billing.lookup",
        function_name="lookup_invoice",
        display_name="Lookup Invoice",
        description="Look up an invoice by number.",
        endpoint_path="/invoices/{id}",
        http_method="GET",
        metadata={"template_slug": "custom_api"},
        read_only=True,
    )
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        existing = await client.get("/agents/sales/agent-document")
        assert existing.status_code == 200
        document = existing.json()["document"]
        discover = next(step for scenario in document["scenarios"] for step in scenario["steps"] if step["id"] == "discover")
        discover["tool_policy"].append(
            {
                "ref": "billing.lookup",
                "mode": "required",
                "invocation_strategy": "always",
                "timeout_ms": None,
                "event_name": "billing_lookup",
                "args": {},
            }
        )
        update = await client.put("/agents/sales/agent-document", json=document)
        assert update.status_code == 200

        start = await client.post(
            "/atlas/sessions",
            json={"scope": "provisioning", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]

        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": "Repair the billing connection before continuing.",
            },
        )
        assert turn.status_code == 200
        payload = turn.json()
        assert payload["next_action"] == "ready_to_review_changes"
        delta = payload["proposed_changes"]["integration_binding_deltas"][0]
        assert delta["change_type"] == "repair_connection"
        assert delta["payload"]["connection_id"] == connection.connection_id
        delta_id = delta["delta_id"]

        review_turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "review_decisions": [{"delta_id": delta_id, "decision": "approved"}],
                "apply_request": {"delta_ids": [delta_id], "apply_note": "repair billing"},
            },
        )
        assert review_turn.status_code == 200
        request_id = review_turn.json()["pending_permission_requests"][0]["request_id"]

        decisions = await client.post(
            f"/atlas/sessions/{session_id}/permission-decisions",
            json=[{"request_id": request_id, "decision": "approved"}],
        )
        assert decisions.status_code == 200

        apply_after_decision = await client.post(
            f"/atlas/sessions/{session_id}/apply",
            json={"delta_ids": [delta_id], "apply_note": "repair billing"},
        )
        assert apply_after_decision.status_code == 200
        assert apply_after_decision.json()["status"] == "applied"

    updated = connection_store.get(connection.connection_id)
    assert updated is not None
    assert updated.status == "error"
    assert updated.metadata_json["atlas_action_kind"] == "repair_connection"
    assert updated.metadata_json["atlas_action_tool_ref"] == "billing.lookup"


@pytest.mark.asyncio
async def test_atlas_provisioning_can_review_and_prepare_custom_oauth_connection(
    postgres_database_url_factory,
    credential_cipher,
) -> None:
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "provisioning", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]
        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": "Set up this OAuth API for the agent.",
                "api_discovery_requests": [
                    {
                        "request_id": "disc_custom_oauth",
                        "source_type": "pasted_schema",
                        "source_value": """
{
  "openapi": "3.0.0",
  "info": {"title": "Partner CRM API"},
  "servers": [{"url": "https://partner.example.com"}],
  "components": {
    "securitySchemes": {
      "oauth": {"type": "oauth2", "flows": {}}
    }
  },
  "paths": {
    "/contacts": {
      "get": {
        "operationId": "listContacts",
        "summary": "List contacts",
        "responses": {"200": {"description": "ok"}}
      }
    }
  }
}
""",
                    }
                ],
            },
        )
        assert turn.status_code == 200
        payload = turn.json()
        assert payload["next_action"] == "ready_to_review_changes"
        delta = payload["proposed_changes"]["integration_binding_deltas"][0]
        assert delta["change_type"] == "prepare_custom_oauth_connection"
        assert delta["payload"]["setup_url"] == "/settings/integrations?provider=custom_oauth"
        assert delta["payload"]["missing_fields"] == ["oauth_authorization"]
        delta_id = delta["delta_id"]

        review_turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "review_decisions": [{"delta_id": delta_id, "decision": "approved"}],
                "apply_request": {"delta_ids": [delta_id], "apply_note": "prepare oauth scaffold"},
            },
        )
        assert review_turn.status_code == 200
        request_id = review_turn.json()["pending_permission_requests"][0]["request_id"]

        decisions = await client.post(
            f"/atlas/sessions/{session_id}/permission-decisions",
            json=[{"request_id": request_id, "decision": "approved"}],
        )
        assert decisions.status_code == 200

        apply_after_decision = await client.post(
            f"/atlas/sessions/{session_id}/apply",
            json={"delta_ids": [delta_id], "apply_note": "prepare oauth scaffold"},
        )
        assert apply_after_decision.status_code == 200
        assert apply_after_decision.json()["status"] == "applied"

    session_factory = build_session_factory(database_url)
    connection_store = APIConnectionStore(session_factory, blob_cipher=credential_cipher)
    connections = connection_store.list_for_org("public")
    prepared = next(item for item in connections if item.provider == "custom_oauth")
    assert prepared.auth_type == "oauth2"
    assert prepared.status == "needs_auth"
    assert prepared.display_name == "Partner CRM API"
    assert prepared.base_url == "https://partner.example.com"
    assert prepared.metadata_json["atlas_scaffold"] == "custom_oauth"


@pytest.mark.asyncio
async def test_atlas_provisioning_turn_surfaces_missing_tool_binding_manifest(
    postgres_database_url_factory,
) -> None:
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        existing = await client.get("/agents/sales/agent-document")
        assert existing.status_code == 200
        document = existing.json()["document"]
        discover = next(step for scenario in document["scenarios"] for step in scenario["steps"] if step["id"] == "discover")
        discover["tool_policy"].append(
            {
                "ref": "crm.lookup",
                "mode": "required",
                "invocation_strategy": "always",
                "timeout_ms": None,
                "event_name": "crm_lookup",
                "args": {},
            }
        )
        update = await client.put("/agents/sales/agent-document", json=document)
        assert update.status_code == 200

        start = await client.post(
            "/atlas/sessions",
            json={"scope": "provisioning", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]

        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": "What dependencies are missing for this agent?",
                "selected_context": {"step_id": "discover"},
            },
        )
        assert turn.status_code == 200
        payload = turn.json()
        missing_dep = next(item for item in payload["dependencies"] if item["display_name"] == "crm.lookup")
        assert missing_dep["status"] == "missing"
        assert missing_dep["blocking"] is True
        assert "Create or import a tool definition" in missing_dep["suggested_action"]
        manifest_item = next(item for item in payload["provisioning_manifest"] if item["tool_ref"] == "crm.lookup")
        assert manifest_item["provider"] == "tooling"
        assert manifest_item["blocking"] is True
        assert manifest_item["requires_credentials"] is True
        assert manifest_item["connection_id"] is None
        assert manifest_item["missing_fields"] == ["tool_definition"]
        assert "Create or import a tool definition" in manifest_item["setup_action"]
        assert manifest_item["documentation_url"] == "/settings/integrations?tool_ref=crm.lookup"


@pytest.mark.asyncio
async def test_atlas_provisioning_turn_surfaces_reauth_and_connection_details(
    postgres_database_url_factory,
    credential_cipher,
) -> None:
    database_url = postgres_database_url_factory()
    session_factory = build_session_factory(database_url)
    connection_store = APIConnectionStore(session_factory, blob_cipher=credential_cipher)
    definition_store = ToolDefinitionStore(session_factory)
    connection = connection_store.create(
        organization_id="public",
        display_name="HubSpot Sandbox",
        provider="hubspot",
        auth_type="oauth2",
        base_url="https://api.hubapi.com",
    )
    connection_store.update(
        connection.connection_id,
        status="needs_auth",
        error_message="oauth grant expired",
    )
    definition = definition_store.create(
        organization_id="public",
        connection_id=connection.connection_id,
        kind="integration",
        tool_ref="crm.lookup",
        function_name="lookup_contact",
        display_name="Lookup CRM Contact",
        description="Look up an existing CRM contact by email address for routing and support workflows.",
        endpoint_path="/crm/v3/objects/contacts/search",
        http_method="POST",
        metadata={"template_slug": "hubspot"},
        read_only=True,
    )
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        existing = await client.get("/agents/sales/agent-document")
        assert existing.status_code == 200
        document = existing.json()["document"]
        discover = next(step for scenario in document["scenarios"] for step in scenario["steps"] if step["id"] == "discover")
        discover["tool_policy"].append(
            {
                "ref": "crm.lookup",
                "mode": "required",
                "invocation_strategy": "always",
                "timeout_ms": None,
                "event_name": "crm_lookup",
                "args": {},
            }
        )
        update = await client.put("/agents/sales/agent-document", json=document)
        assert update.status_code == 200

        start = await client.post(
            "/atlas/sessions",
            json={"scope": "provisioning", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]

        turn = await client.post(
            "/atlas/turns",
            json={"session_id": session_id, "message": "Review the CRM setup blockers."},
        )
        assert turn.status_code == 200
        payload = turn.json()
        assert payload["next_action"] == "ready_to_provision"
        dependency = next(item for item in payload["dependencies"] if item["key"] == "tool:crm.lookup")
        assert dependency["status"] == "requires_auth"
        assert dependency["blocking"] is True
        manifest_item = next(item for item in payload["provisioning_manifest"] if item["tool_ref"] == "crm.lookup")
        assert manifest_item["provider"] == "hubspot"
        assert manifest_item["connection_id"] == connection.connection_id
        assert manifest_item["connection_status"] == "requires_auth"
        assert manifest_item["requires_credentials"] is True
        assert manifest_item["missing_fields"] == ["oauth_authorization"]
        assert "authorize the HubSpot connection" in manifest_item["setup_action"]
        assert manifest_item["documentation_url"] == f"/settings/integrations?connection_id={connection.connection_id}"
        assert "Connection: HubSpot Sandbox" in manifest_item["notes"]
        assert "Base URL: https://api.hubapi.com" in manifest_item["notes"]


@pytest.mark.asyncio
async def test_atlas_can_review_and_apply_native_step_and_route_deltas(postgres_database_url_factory) -> None:
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        existing = await client.get("/agents/sales/agent-document")
        assert existing.status_code == 200
        document = existing.json()["document"]
        document["scenarios"].append(
            {
                "id": "follow_up",
                "name": "Follow Up",
                "start_step_id": "follow_up_start",
                "steps": [
                    {
                        "id": "follow_up_start",
                        "name": "Follow Up Start",
                        "transitions": [],
                        "description": None,
                        "say": "I'll pick this up from here.",
                        "guards": [],
                        "fact_requirements": [],
                        "tool_policy": [],
                        "action_config": None,
                        "response_policy": {
                            "answer_directly_first": True,
                            "ask_clarifying_question_only_if_needed": True,
                            "voice_style": "concise",
                            "direct_answer_prompt": None,
                            "render_with_llm": True,
                            "deterministic_fallback_text": None,
                            "response_max_sentences": None,
                            "include_recent_history": True,
                            "include_known_facts": True,
                        },
                        "workload_class": "interactive",
                        "execution_isolation": "subprocess",
                        "handoff": None,
                        "completion": {"disposition": "follow_up", "summary": "Follow-up branch completed."},
                    }
                ],
                "summary": None,
                "order": 1,
                "entry_channels": [],
                "resources": {},
            }
        )
        update = await client.put("/agents/sales/agent-document", json=document)
        assert update.status_code == 200

        start = await client.post(
            "/atlas/sessions",
            json={"scope": "agent_authoring", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]

        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": (
                    'Require fact "email" and add tool "crm.lookup" and add guard fact_required "email" '
                    'and set voice style to detailed and add a transition to "answer_pricing" on the outcome event '
                    '"follow_up_request" with description "User asks a follow-up question." '
                    'and add a scenario route to "follow_up" on the outcome event "follow_up_request" '
                    'with description "User asks a follow-up question."'
                ),
                "selected_context": {"step_id": "discover"},
            },
        )
        assert turn.status_code == 200
        payload = turn.json()
        assert payload["next_action"] == "ready_to_review_changes"
        step_deltas = payload["proposed_changes"]["step_deltas"]
        route_deltas = payload["proposed_changes"]["scenario_route_deltas"]
        assert {item["change_type"] for item in step_deltas} == {
            "add_fact_requirement",
            "add_tool_binding",
            "add_guard",
            "update_response_policy",
            "add_step_transition",
        }
        assert {item["change_type"] for item in route_deltas} == {"create_scenario_route"}
        delta_ids = [item["delta_id"] for item in step_deltas] + [item["delta_id"] for item in route_deltas]

        review_and_request = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "review_decisions": [{"delta_id": delta_id, "decision": "approved"} for delta_id in delta_ids],
                "apply_request": {"delta_ids": delta_ids, "apply_note": "apply the authored changes"},
            },
        )
        assert review_and_request.status_code == 200
        review_payload = review_and_request.json()
        assert review_payload["next_action"] == "blocked"
        request_id = review_payload["pending_permission_requests"][0]["request_id"]

        decisions = await client.post(
            f"/atlas/sessions/{session_id}/permission-decisions",
            json=[{"request_id": request_id, "decision": "approved"}],
        )
        assert decisions.status_code == 200

        apply_after_decision = await client.post(
            f"/atlas/sessions/{session_id}/apply",
            json={"delta_ids": delta_ids, "apply_note": "apply the authored changes"},
        )
        assert apply_after_decision.status_code == 200
        assert apply_after_decision.json()["status"] == "applied"

        updated_document = await client.get("/agents/sales/agent-document")
        assert updated_document.status_code == 200
        updated = updated_document.json()["document"]
        discover = next(step for scenario in updated["scenarios"] for step in scenario["steps"] if step["id"] == "discover")
        assert discover["response_policy"]["voice_style"] == "detailed"
        assert any(item["name"] == "email" for item in discover["fact_requirements"])
        assert any(item["ref"] == "crm.lookup" for item in discover["tool_policy"])
        assert any(item["kind"] == "fact_required" and item["value"] == "email" for item in discover["guards"])
        assert any(
            item["to_step_id"] == "answer_pricing"
            and item["when"]["kind"] == "outcome"
            and item["when"]["event"] == "follow_up_request"
            for item in discover["transitions"]
        )
        assert any(
            item["from_scenario_id"] == "main"
            and item["to_scenario_id"] == "follow_up"
            and item["when"]["kind"] == "outcome"
            and item["when"]["event"] == "follow_up_request"
            for item in updated["scenario_routes"]
        )


@pytest.mark.asyncio
async def test_atlas_can_update_delete_and_reorder_native_deltas(postgres_database_url_factory) -> None:
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        existing = await client.get("/agents/sales/agent-document")
        assert existing.status_code == 200
        document = existing.json()["document"]
        main = next(item for item in document["scenarios"] if item["id"] == "main")
        main["steps"].append(
            {
                "id": "temp_step",
                "name": "Temp Step",
                "transitions": [],
                "description": None,
                "say": "Temporary step",
                "guards": [],
                "fact_requirements": [],
                "tool_policy": [],
                "action_config": None,
                "response_policy": {
                    "answer_directly_first": True,
                    "ask_clarifying_question_only_if_needed": True,
                    "voice_style": "concise",
                    "direct_answer_prompt": None,
                    "render_with_llm": True,
                    "deterministic_fallback_text": None,
                    "response_max_sentences": None,
                    "include_recent_history": True,
                    "include_known_facts": True,
                },
                "workload_class": "interactive",
                "execution_isolation": "subprocess",
                "handoff": None,
                "completion": {"disposition": "temp", "summary": "Temporary end state."},
            }
        )
        document["scenarios"].append(
            {
                "id": "follow_up",
                "name": "Follow Up",
                "start_step_id": "follow_up_start",
                "steps": [
                    {
                        "id": "follow_up_start",
                        "name": "Follow Up Start",
                        "transitions": [],
                        "description": None,
                        "say": "Follow up path.",
                        "guards": [],
                        "fact_requirements": [],
                        "tool_policy": [],
                        "action_config": None,
                        "response_policy": {
                            "answer_directly_first": True,
                            "ask_clarifying_question_only_if_needed": True,
                            "voice_style": "concise",
                            "direct_answer_prompt": None,
                            "render_with_llm": True,
                            "deterministic_fallback_text": None,
                            "response_max_sentences": None,
                            "include_recent_history": True,
                            "include_known_facts": True,
                        },
                        "workload_class": "interactive",
                        "execution_isolation": "subprocess",
                        "handoff": None,
                        "completion": {"disposition": "follow_up", "summary": "Follow-up branch completed."},
                    }
                ],
                "summary": None,
                "order": 1,
                "entry_channels": [],
                "resources": {},
            }
        )
        document["scenario_routes"] = [
            {
                "id": "route_main_followup",
                "from_scenario_id": "main",
                "when": {
                    "kind": "outcome",
                    "event": "follow_up",
                    "description": "User asks a follow-up question.",
                },
                "to_scenario_id": "follow_up",
                "label": "follow_up",
                "priority": 50,
            },
            {
                "id": "route_followup_main",
                "from_scenario_id": "follow_up",
                "when": {
                    "kind": "outcome",
                    "event": "return_main",
                    "description": "User returns to the main flow.",
                },
                "to_scenario_id": "main",
                "label": "return_main",
                "priority": 50,
            },
        ]
        update = await client.put("/agents/sales/agent-document", json=document)
        assert update.status_code == 200

        start = await client.post(
            "/atlas/sessions",
            json={"scope": "agent_authoring", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]

        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": (
                    'add fact "company_size" of type string and require fact "company_size" '
                    'and move step "temp_step" before "answer_pricing" '
                    'and update transition "t_pricing" to "answer_product" on the outcome event "price_check" '
                    'with description "User asks about pricing." '
                    'and delete transition "t_demo" '
                    'and update scenario route "route_main_followup" to "follow_up" on the outcome event "needs_followup" '
                    'with description "User needs a follow-up." '
                    'and delete scenario route "route_followup_main"'
                ),
                "selected_context": {"step_id": "discover"},
            },
        )
        assert turn.status_code == 200
        payload = turn.json()
        assert payload["next_action"] == "ready_to_review_changes"
        agent_metadata_deltas = payload["proposed_changes"]["agent_metadata_deltas"]
        step_deltas = payload["proposed_changes"]["step_deltas"]
        assert {item["change_type"] for item in agent_metadata_deltas} == {
            "add_fact_schema_entry"
        }
        assert {item["change_type"] for item in step_deltas} == {
            "add_fact_requirement",
            "reorder_step",
            "update_step_transition",
            "delete_step_transition",
        }
        assert {item["change_type"] for item in payload["proposed_changes"]["scenario_route_deltas"]} == {
            "update_scenario_route",
            "delete_scenario_route",
        }
        add_fact_delta = next(item for item in agent_metadata_deltas if item["change_type"] == "add_fact_schema_entry")
        require_fact_delta = next(item for item in step_deltas if item["change_type"] == "add_fact_requirement")
        assert require_fact_delta["depends_on_delta_ids"] == [add_fact_delta["delta_id"]]
        delta_ids = (
            [item["delta_id"] for item in agent_metadata_deltas]
            + [item["delta_id"] for item in step_deltas]
            + [item["delta_id"] for item in payload["proposed_changes"]["scenario_route_deltas"]]
        )

        review_and_request = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "review_decisions": [{"delta_id": delta_id, "decision": "approved"} for delta_id in delta_ids],
                "apply_request": {"delta_ids": delta_ids, "apply_note": "apply the authored changes"},
            },
        )
        assert review_and_request.status_code == 200
        request_id = review_and_request.json()["pending_permission_requests"][0]["request_id"]

        decisions = await client.post(
            f"/atlas/sessions/{session_id}/permission-decisions",
            json=[{"request_id": request_id, "decision": "approved"}],
        )
        assert decisions.status_code == 200

        apply_after_decision = await client.post(
            f"/atlas/sessions/{session_id}/apply",
            json={"delta_ids": list(reversed(delta_ids)), "apply_note": "apply the authored changes"},
        )
        assert apply_after_decision.status_code == 200
        assert apply_after_decision.json()["status"] == "applied"

        updated_document = await client.get("/agents/sales/agent-document")
        assert updated_document.status_code == 200
        updated = updated_document.json()["document"]
        assert any(item["name"] == "company_size" for item in updated["fact_schema"])
        discover = next(step for scenario in updated["scenarios"] for step in scenario["steps"] if step["id"] == "discover")
        assert any(item["name"] == "company_size" for item in discover["fact_requirements"])
        assert any(
            item["id"] == "t_pricing"
            and item["to_step_id"] == "answer_product"
            and item["when"]["kind"] == "outcome"
            and item["when"]["event"] == "price_check"
            for item in discover["transitions"]
        )
        assert all(item["id"] != "t_demo" for item in discover["transitions"])
        main_steps = next(item["steps"] for item in updated["scenarios"] if item["id"] == "main")
        temp_index = next(index for index, item in enumerate(main_steps) if item["id"] == "temp_step")
        pricing_index = next(index for index, item in enumerate(main_steps) if item["id"] == "answer_pricing")
        assert temp_index < pricing_index
        assert any(
            item["id"] == "route_main_followup"
            and item["when"]["kind"] == "outcome"
            and item["when"]["event"] == "needs_followup"
            for item in updated["scenario_routes"]
        )
        assert all(item["id"] != "route_followup_main" for item in updated["scenario_routes"])


@pytest.mark.asyncio
async def test_atlas_can_delete_safe_step(postgres_database_url_factory) -> None:
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        existing = await client.get("/agents/sales/agent-document")
        assert existing.status_code == 200
        document = existing.json()["document"]
        main = next(item for item in document["scenarios"] if item["id"] == "main")
        main["steps"].append(
            {
                "id": "temp_step",
                "name": "Temp Step",
                "transitions": [],
                "description": None,
                "say": "Temporary step",
                "guards": [],
                "fact_requirements": [],
                "tool_policy": [],
                "action_config": None,
                "response_policy": {
                    "answer_directly_first": True,
                    "ask_clarifying_question_only_if_needed": True,
                    "voice_style": "concise",
                    "direct_answer_prompt": None,
                    "render_with_llm": True,
                    "deterministic_fallback_text": None,
                    "response_max_sentences": None,
                    "include_recent_history": True,
                    "include_known_facts": True,
                },
                "workload_class": "interactive",
                "execution_isolation": "subprocess",
                "handoff": None,
                "completion": {"disposition": "temp", "summary": "Temporary end state."},
            }
        )
        update = await client.put("/agents/sales/agent-document", json=document)
        assert update.status_code == 200

        start = await client.post(
            "/atlas/sessions",
            json={"scope": "agent_authoring", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]

        turn = await client.post(
            "/atlas/turns",
            json={"session_id": session_id, "message": 'delete step "temp_step"', "selected_context": {"step_id": "discover"}},
        )
        assert turn.status_code == 200
        payload = turn.json()
        assert payload["next_action"] == "ready_to_review_changes"
        delete_delta = next(item for item in payload["proposed_changes"]["step_deltas"] if item["change_type"] == "delete_step")

        review_and_request = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "review_decisions": [{"delta_id": delete_delta["delta_id"], "decision": "approved"}],
                "apply_request": {"delta_ids": [delete_delta["delta_id"]], "apply_note": "apply the authored changes"},
            },
        )
        assert review_and_request.status_code == 200
        request_id = review_and_request.json()["pending_permission_requests"][0]["request_id"]

        decisions = await client.post(
            f"/atlas/sessions/{session_id}/permission-decisions",
            json=[{"request_id": request_id, "decision": "approved"}],
        )
        assert decisions.status_code == 200

        apply_after_decision = await client.post(
            f"/atlas/sessions/{session_id}/apply",
            json={"delta_ids": [delete_delta["delta_id"]], "apply_note": "apply the authored changes"},
        )
        assert apply_after_decision.status_code == 200
        assert apply_after_decision.json()["status"] == "applied"

        updated_document = await client.get("/agents/sales/agent-document")
        assert updated_document.status_code == 200
        updated = updated_document.json()["document"]
        assert all(
            step["id"] != "temp_step"
            for scenario in updated["scenarios"]
            for step in scenario["steps"]
        )


@pytest.mark.asyncio
async def test_atlas_invalid_generated_delta_becomes_blocker_not_review_item(postgres_database_url_factory) -> None:
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "agent_authoring", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]

        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": 'require fact "company_size"',
                "selected_context": {"step_id": "discover"},
            },
        )
        assert turn.status_code == 200
        payload = turn.json()
        assert payload["proposed_changes"]["step_deltas"] == []
        assert payload["review_state"]["pending_delta_ids"] == []
        assert any(
            item["code"] == "atlas.invalid_proposed_change" and "company_size" in item["message"]
            for item in payload["blockers"]
        )


@pytest.mark.asyncio
async def test_atlas_event_stream_replays_stored_events(postgres_database_url_factory) -> None:
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "agent_authoring", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]

        turn = await client.post(
            "/atlas/turns",
            json={"session_id": session_id, "message": "Review the current draft."},
        )
        assert turn.status_code == 200

        async with client.stream(
            "GET",
            f"/atlas/sessions/{session_id}/events/stream",
            params={"idle_timeout_seconds": 0.2, "poll_interval_seconds": 0.05},
        ) as response:
            assert response.status_code == 200
            chunks: list[str] = []
            async for chunk in response.aiter_text():
                chunks.append(chunk)
            stream_text = "".join(chunks)

        assert "event: start" in stream_text
        assert "event: progress" in stream_text
        assert "event: complete" in stream_text


@pytest.mark.asyncio
async def test_atlas_session_is_isolated_across_orgs_through_auth(
    postgres_database_url_factory,
) -> None:
    """AR-5.2: through the real auth middleware, an org-2 principal gets 404 on
    an org-1 atlas session (every other atlas HTTP test runs auth-disabled)."""
    from tests.test_api import (
        _authorize_client,
        _build_authenticated_api_app,
        _seed_authenticated_api_store,
    )

    auth_database_url = postgres_database_url_factory()
    runtime_database_url = postgres_database_url_factory()
    _seed_authenticated_api_store(auth_database_url)

    # Seed an agent owned by org-1 in the runtime DB.
    registry = SQLAlchemyAgentRegistry(build_session_factory(runtime_database_url))
    registry.create_agent_document(
        agent_id="org1_agent",
        agent_name="Org1 Agent",
        organization_id="org-1",
        document=AgentDocument(
            start_scenario_id="s",
            scenarios=[
                Scenario(
                    id="s", name="S", start_step_id="start",
                    steps=[Step(id="start", name="Start")],
                )
            ],
        ),
    )

    app = _build_authenticated_api_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=runtime_database_url,
        auth_database_url=auth_database_url,
    )
    auth_service = app.state.auth_service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as org1, \
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as org2:
        _authorize_client(org1, auth_service=auth_service, user_id="user-admin", organization_id="org-1")
        _authorize_client(org2, auth_service=auth_service, user_id="user-org2-admin", organization_id="org-2")

        start = await org1.post("/atlas/sessions", json={"scope": "agent_authoring", "agent_id": "org1_agent"})
        assert start.status_code == 200, start.text
        session_id = start.json()["session_id"]

        # Same session id, org-2 principal → 404 (not 200, not 403-leak).
        cross = await org2.get(f"/atlas/sessions/{session_id}")
        assert cross.status_code == 404
        # org-1 can still read its own session.
        own = await org1.get(f"/atlas/sessions/{session_id}")
        assert own.status_code == 200


# ─── Archived-session guard ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_atlas_archived_session_rejects_turns_apply_and_permission_decisions(
    postgres_database_url_factory,
) -> None:
    """Archived sessions are read-only: POST /atlas/turns, /apply, and
    /permission-decisions must all return 409 with the stable detail code
    `atlas_session_archived`."""
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "agent_authoring", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]

        archived = await client.post(f"/atlas/sessions/{session_id}/archive")
        assert archived.status_code == 200
        assert archived.json()["status"] == "archived"

        turn = await client.post(
            "/atlas/turns",
            json={"session_id": session_id, "message": "hello"},
        )
        assert turn.status_code == 409
        assert turn.json()["detail"] == "atlas_session_archived"

        apply_resp = await client.post(
            f"/atlas/sessions/{session_id}/apply",
            json={"delta_ids": ["delta_1"]},
        )
        assert apply_resp.status_code == 409
        assert apply_resp.json()["detail"] == "atlas_session_archived"

        decisions = await client.post(
            f"/atlas/sessions/{session_id}/permission-decisions",
            json=[{"request_id": "atlas_perm_whatever", "decision": "approved"}],
        )
        assert decisions.status_code == 409
        assert decisions.json()["detail"] == "atlas_session_archived"

        # Read routes stay available on archived sessions.
        state = await client.get(f"/atlas/sessions/{session_id}/state")
        assert state.status_code == 200
        messages = await client.get(f"/atlas/sessions/{session_id}/messages")
        assert messages.status_code == 200


# ─── Permission-decision integrity (store) ───────────────────────────────────


def test_atlas_store_apply_permission_decisions_rejects_unknown_request_ids(
    postgres_database_url_factory,
) -> None:
    """Unknown / cross-org / non-matching request_ids must raise a ValueError
    that lists the unknown ids instead of being silently ignored."""
    from datetime import datetime, timedelta, timezone

    from ruhu.atlas_models import AtlasPermissionRequest, AtlasSession
    from ruhu.atlas_store import SQLAlchemyAtlasStore

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    store = SQLAlchemyAtlasStore(session_factory)

    org_id = "public"
    now = datetime.now(timezone.utc)
    session = AtlasSession(
        session_id="atlas_session_unknown_ids",
        organization_id=org_id,
        scope="agent_authoring",
        status="active",
        agent_id="sales",
        created_at=now,
        updated_at=now,
    )
    store.create_session(session)
    store.create_permission_request(
        AtlasPermissionRequest(
            request_id="atlas_perm_known",
            session_id=session.session_id,
            organization_id=org_id,
            kind="apply_deltas",
            status="pending",
            reason="unknown-id-test",
            delta_ids=["d1"],
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
    )

    # Entirely unknown id.
    with pytest.raises(ValueError, match="unknown permission request id"):
        store.apply_permission_decisions(
            session.session_id,
            [{"request_id": "atlas_perm_typo", "decision": "approved"}],
            organization_id=org_id,
            decided_by_user_id="reviewer",
        )

    # Mixed known + unknown — the error must name the unknown id and the
    # known request must NOT be decided as a side effect.
    with pytest.raises(ValueError, match="atlas_perm_typo"):
        store.apply_permission_decisions(
            session.session_id,
            [
                {"request_id": "atlas_perm_known", "decision": "approved"},
                {"request_id": "atlas_perm_typo", "decision": "approved"},
            ],
            organization_id=org_id,
            decided_by_user_id="reviewer",
        )
    still_pending = store.list_permission_requests(
        session.session_id,
        organization_id=org_id,
        status="pending",
    )
    assert [item.request_id for item in still_pending] == ["atlas_perm_known"]

    # A request that exists but belongs to another org is "unknown" from the
    # caller's perspective — the org filter hides it.
    with pytest.raises(ValueError, match="atlas_perm_known"):
        store.apply_permission_decisions(
            session.session_id,
            [{"request_id": "atlas_perm_known", "decision": "approved"}],
            organization_id="other-org",
            decided_by_user_id="reviewer",
        )


@pytest.mark.asyncio
async def test_atlas_permission_decisions_endpoint_returns_400_for_unknown_ids(
    postgres_database_url_factory,
) -> None:
    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "agent_authoring", "agent_id": "sales"},
        )
        assert start.status_code == 200
        session_id = start.json()["session_id"]

        decisions = await client.post(
            f"/atlas/sessions/{session_id}/permission-decisions",
            json=[{"request_id": "atlas_perm_does_not_exist", "decision": "approved"}],
        )
        assert decisions.status_code == 400
        assert "atlas_perm_does_not_exist" in decisions.json()["detail"]


def test_atlas_store_apply_permission_decisions_persists_expired_status(
    postgres_database_url_factory,
) -> None:
    """Deciding an expired pending request raises, AND the row's transition
    to status="expired" must survive the raise (previously the write was
    rolled back inside the same transaction)."""
    from datetime import datetime, timedelta, timezone

    from ruhu.atlas_models import AtlasPermissionRequest, AtlasSession
    from ruhu.atlas_store import SQLAlchemyAtlasStore

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    store = SQLAlchemyAtlasStore(session_factory)

    org_id = "public"
    now = datetime.now(timezone.utc)
    session = AtlasSession(
        session_id="atlas_session_expiry_durable",
        organization_id=org_id,
        scope="agent_authoring",
        status="active",
        agent_id="sales",
        created_at=now,
        updated_at=now,
    )
    store.create_session(session)
    store.create_permission_request(
        AtlasPermissionRequest(
            request_id="atlas_perm_expiry_durable",
            session_id=session.session_id,
            organization_id=org_id,
            kind="apply_deltas",
            status="pending",
            reason="expiry-durability-test",
            delta_ids=["d1"],
            created_at=now - timedelta(days=2),
            expires_at=now - timedelta(days=1),
        )
    )

    with pytest.raises(ValueError, match="has expired"):
        store.apply_permission_decisions(
            session.session_id,
            [{"request_id": "atlas_perm_expiry_durable", "decision": "approved"}],
            organization_id=org_id,
            decided_by_user_id="reviewer",
        )

    # The expiry must be durably recorded despite the raise.
    all_requests = store.list_permission_requests(
        session.session_id,
        organization_id=org_id,
    )
    by_id = {item.request_id: item for item in all_requests}
    assert by_id["atlas_perm_expiry_durable"].status == "expired"


# ─── Staff gate + self-approval (auth-enabled) ───────────────────────────────


def _build_auth_enabled_atlas_app(
    runtime_database_url: str,
    auth_database_url: str,
    *,
    admin_is_superuser: bool = False,
):
    """Auth-enabled app with two users in org-1: user-admin (admin role,
    optionally superuser) and user-analyst (analyst role, never superuser)."""
    from ruhu.identity import Organization, OrganizationMembership, User
    from ruhu.identity_sqlalchemy import SQLAlchemyIdentityStore
    from ruhu.runtime_config import RuntimeSettings

    identity_store = SQLAlchemyIdentityStore(build_session_factory(auth_database_url))
    identity_store.save_organization(Organization(organization_id="org-1", slug="acme", name="Acme"))
    identity_store.save_user(
        User(user_id="user-admin", email="admin@example.com", is_superuser=admin_is_superuser)
    )
    identity_store.save_user(User(user_id="user-analyst", email="analyst@example.com"))
    identity_store.add_organization_membership(
        OrganizationMembership(user_id="user-admin", organization_id="org-1", role="admin", is_account_owner=True)
    )
    identity_store.add_organization_membership(
        OrganizationMembership(user_id="user-analyst", organization_id="org-1", role="analyst")
    )

    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=runtime_database_url,
        auth_database_url=auth_database_url,
        auth_jwt_secret="0123456789abcdef0123456789abcdef",
        runtime_settings=RuntimeSettings(auth_allowed_redirect_origins=["http://testserver"]),
    )
    return app


def _bearer_headers(app, *, user_id: str, organization_id: str = "org-1") -> dict[str, str]:
    from ruhu.identity import SessionAuditContext

    issued = app.state.auth_service.issue_browser_session(
        user_id=user_id,
        organization_id=organization_id,
        audit=SessionAuditContext(),
    )
    return {"Authorization": f"Bearer {issued.access_token}"}


@pytest.mark.asyncio
async def test_atlas_rollout_summary_requires_internal_superuser(
    postgres_database_url_factory,
) -> None:
    """/atlas/admin/rollout-summary exposes process-wide cross-tenant
    counters: anonymous -> 401, authenticated non-superuser -> 403,
    superuser -> 200."""
    app = _build_auth_enabled_atlas_app(
        postgres_database_url_factory(),
        postgres_database_url_factory(),
        admin_is_superuser=True,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        anonymous = await client.get("/atlas/admin/rollout-summary")
        assert anonymous.status_code == 401

        non_staff = await client.get(
            "/atlas/admin/rollout-summary",
            headers=_bearer_headers(app, user_id="user-analyst"),
        )
        assert non_staff.status_code == 403

        staff = await client.get(
            "/atlas/admin/rollout-summary",
            headers=_bearer_headers(app, user_id="user-admin"),
        )
        assert staff.status_code == 200
        payload = staff.json()
        assert "policy" in payload
        assert "family_summaries" in payload


@pytest.mark.asyncio
async def test_atlas_session_creator_can_approve_own_permission_and_apply(
    postgres_database_url_factory,
) -> None:
    """End-to-end with real auth: the user who created the Atlas session
    proposes a change, requests apply permission, approves that permission
    THEMSELVES via POST /permission-decisions, and applies. The old
    four-eyes rule rejected the self-approval with 409; the contract is now
    explicit confirmation only."""
    runtime_database_url = postgres_database_url_factory()
    app = _build_auth_enabled_atlas_app(
        runtime_database_url,
        postgres_database_url_factory(),
    )
    # The agent fixtures seed under the bootstrap org; create an agent that
    # belongs to org-1 so the authenticated principal can author against it.
    # The document must pass publish validation, otherwise the proposed
    # rename delta is converted into a blocker instead of a review item.
    from ruhu.agent_document import OtherwiseCondition, StepCompletion, StepTransition
    from ruhu.registry import SQLAlchemyAgentRegistry

    SQLAlchemyAgentRegistry(build_session_factory(runtime_database_url)).create_agent_document(
        agent_id="org1_agent",
        agent_name="Org One Agent",
        organization_id="org-1",
        document=AgentDocument(
            start_scenario_id="main",
            scenarios=[
                Scenario(
                    id="main",
                    name="Main",
                    start_step_id="discover",
                    steps=[
                        Step(
                            id="discover",
                            name="Discover",
                            transitions=[
                                StepTransition(
                                    id="t_done",
                                    when=OtherwiseCondition(),
                                    to_step_id="done",
                                )
                            ],
                        ),
                        Step(
                            id="done",
                            name="Done",
                            completion=StepCompletion(disposition="resolved"),
                        ),
                    ],
                )
            ],
        ),
    )

    transport = httpx.ASGITransport(app=app)
    headers = _bearer_headers(app, user_id="user-admin")
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers=headers
    ) as client:
        start = await client.post(
            "/atlas/sessions",
            json={"scope": "agent_authoring", "agent_id": "org1_agent"},
        )
        assert start.status_code == 200
        session = start.json()
        session_id = session["session_id"]
        assert session["created_by"] == "user-admin"

        turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "message": 'Rename this step to "Qualified lead"',
                "selected_context": {"step_id": "discover"},
            },
        )
        assert turn.status_code == 200
        delta_id = turn.json()["proposed_changes"]["step_deltas"][0]["delta_id"]

        review_turn = await client.post(
            "/atlas/turns",
            json={
                "session_id": session_id,
                "review_decisions": [{"delta_id": delta_id, "decision": "approved"}],
                "apply_request": {"delta_ids": [delta_id], "apply_note": "ship it"},
            },
        )
        assert review_turn.status_code == 200
        request_id = review_turn.json()["pending_permission_requests"][0]["request_id"]

        # The session creator approves their own permission request.
        decisions = await client.post(
            f"/atlas/sessions/{session_id}/permission-decisions",
            json=[{"request_id": request_id, "decision": "approved"}],
        )
        assert decisions.status_code == 200
        updated = decisions.json()["updated_requests"]
        assert [item["status"] for item in updated] == ["approved"]

        apply_resp = await client.post(
            f"/atlas/sessions/{session_id}/apply",
            json={"delta_ids": [delta_id], "apply_note": "ship it"},
        )
        assert apply_resp.status_code == 200
        assert apply_resp.json()["status"] == "applied"
