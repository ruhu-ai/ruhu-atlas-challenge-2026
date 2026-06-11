from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from ruhu.api import build_default_app
from ruhu.runtime_config import RuntimeSettings
from ruhu.schemas import ActionRecord, RenderedMessage, RuntimeTurnResult


def test_phone_final_transcript_commit_is_idempotent(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        runtime_settings = RuntimeSettings(
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            provider_shared_secret="livekit-provider-secret",
            internal_api_secret="internal-ops-secret",
        )
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_settings.database_url,
            runtime_settings=runtime_settings,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            start = await client.post(
                "/providers/livekit/phone/calls/start",
                json={"agent_id": "sales", "external_session_id": "call-rt-1"},
                headers={"X-Ruhu-Provider-Secret": "livekit-provider-secret"},
            )
            assert start.status_code == 200
            assert start.json()["realtime_session_id"]

            first = await client.post(
                "/providers/livekit/phone/calls/call-rt-1/transcripts",
                json={
                    "text": "I want to book a demo.",
                    "is_final": True,
                    "idempotency_key": "call-rt-1:seg-1",
                },
                headers={"X-Ruhu-Provider-Secret": "livekit-provider-secret"},
            )
            assert first.status_code == 200

            duplicate = await client.post(
                "/providers/livekit/phone/calls/call-rt-1/transcripts",
                json={
                    "text": "I want to book a demo.",
                    "is_final": True,
                    "idempotency_key": "call-rt-1:seg-1",
                },
                headers={"X-Ruhu-Provider-Secret": "livekit-provider-secret"},
            )
            assert duplicate.status_code == 200
            assert duplicate.json()["trace_id"] == first.json()["trace_id"]
            assert duplicate.json()["messages"] == first.json()["messages"]

        control_plane = app.state.realtime_control_plane
        sessions = control_plane.sessions.list_by_conversation("phone:call-rt-1")
        assert len(sessions) == 1
        assert sessions[0].provider == "livekit"

        events = control_plane.events.replay(conversation_id="phone:call-rt-1")
        event_names = [(event.family, event.name) for event in events]
        assert event_names.count(("voice", "final_transcript_observed")) == 1
        assert event_names.count(("message", "user_accepted")) == 1
        assert event_names.count(("message", "assistant_emitted")) >= 1

        outbox_entries = [
            entry for entry in control_plane.outbox.list_pending(limit=100) if entry.conversation_id == "phone:call-rt-1"
        ]
        assert outbox_entries

    asyncio.run(run())


def test_provider_phone_start_can_resolve_agent_from_configured_number_route(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        runtime_settings = RuntimeSettings(
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            provider_shared_secret="livekit-provider-secret",
            internal_api_secret="internal-ops-secret",
            phone_number_routes={
                "nigeria_demo_line": {
                    "phone_number": "+2348012345678",
                    "agent_id": "sales",
                    "organization_id": "org-demo",
                    "provider": "telnyx",
                    "display_name": "Nigeria demo line",
                }
            },
        )
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_settings.database_url,
            runtime_settings=runtime_settings,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            start = await client.post(
                "/providers/livekit/phone/calls/start",
                json={
                    "external_session_id": "call-routed-1",
                    "provider": "telnyx",
                    "metadata": {
                        "to_number": "+234 801 234 5678",
                        "from_number": "+14155550123",
                    },
                },
                headers={"X-Ruhu-Provider-Secret": "livekit-provider-secret"},
            )
            assert start.status_code == 200
            payload = start.json()
            assert payload["conversation_id"] == "phone:call-routed-1"
            assert payload["realtime_session_id"]

            transcript = await client.post(
                "/providers/livekit/phone/calls/call-routed-1/transcripts",
                json={
                    "text": "Tell me about pricing.",
                    "is_final": True,
                    "idempotency_key": "call-routed-1:seg-1",
                },
                headers={"X-Ruhu-Provider-Secret": "livekit-provider-secret"},
            )
            assert transcript.status_code == 200

        control_plane = app.state.realtime_control_plane
        sessions = control_plane.sessions.list_by_conversation("phone:call-routed-1")
        assert len(sessions) == 1
        assert sessions[0].organization_id == "org-demo"
        assert sessions[0].provider == "livekit"
        assert sessions[0].transport_metadata["transport_provider"] == "livekit"
        assert sessions[0].transport_metadata["telephony_provider"] == "telnyx"
        assert sessions[0].transport_metadata["phone_number_route_key"] == "nigeria_demo_line"
        assert sessions[0].transport_metadata["resolved_phone_number"] == "+2348012345678"
        assert app.state.phone_number_routes["nigeria_demo_line"].agent_id == "sales"
        transcript_events = [
            event
            for event in control_plane.events.replay(conversation_id="phone:call-routed-1")
            if event.family == "voice" and event.name == "final_transcript_observed"
        ]
        assert transcript_events
        event_metadata = transcript_events[-1].payload["metadata"]
        assert event_metadata["transport_provider"] == "livekit"
        assert event_metadata["telephony_provider"] == "telnyx"

    asyncio.run(run())


def test_realtime_outbox_claim_rejects_fresh_duplicate_claims(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        runtime_settings = RuntimeSettings(
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            provider_shared_secret="livekit-provider-secret",
            internal_api_secret="internal-ops-secret",
        )
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_settings.database_url,
            runtime_settings=runtime_settings,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            start = await client.post(
                "/providers/livekit/phone/calls/start",
                json={"agent_id": "sales", "external_session_id": "call-rt-claim"},
                headers={"X-Ruhu-Provider-Secret": "livekit-provider-secret"},
            )
            assert start.status_code == 200

        control_plane = app.state.realtime_control_plane
        entry = next(
            item
            for item in control_plane.outbox.list_pending(limit=100)
            if item.conversation_id == "phone:call-rt-claim"
        )
        first_claim = control_plane.outbox.claim(entry.outbox_id)
        assert first_claim is not None

        duplicate_claim = control_plane.outbox.claim(
            entry.outbox_id,
            claimed_at=first_claim.claimed_at + timedelta(seconds=1),
        )
        assert duplicate_claim is None

        reclaimed = control_plane.outbox.claim(
            entry.outbox_id,
            claimed_at=first_claim.claimed_at + timedelta(seconds=61),
        )
        assert reclaimed is not None

    asyncio.run(run())


def test_final_transcript_retry_reuses_accepted_event_after_processing_failure(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        runtime_settings = RuntimeSettings(
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            provider_shared_secret="livekit-provider-secret",
            internal_api_secret="internal-ops-secret",
        )
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_settings.database_url,
            runtime_settings=runtime_settings,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            start = await client.post(
                "/providers/livekit/phone/calls/start",
                json={"agent_id": "sales", "external_session_id": "call-rt-retry"},
                headers={"X-Ruhu-Provider-Secret": "livekit-provider-secret"},
            )
            assert start.status_code == 200
            realtime_session_id = start.json()["realtime_session_id"]

        control_plane = app.state.realtime_control_plane

        def _successful_turn() -> RuntimeTurnResult:
            return RuntimeTurnResult(
                turn_id="turn:call-rt-retry:seg-1",
                conversation_id="phone:call-rt-retry",
                step_before="intro",
                step_after="qualified",
                chosen_action=ActionRecord(type="stay", reason="test_resume"),
                emitted_messages=[RenderedMessage(text="Thanks, I can help with that.")],
                trace_id="trace-recovered",
            )

        try:
            control_plane.commit_final_transcript(
                conversation_id="phone:call-rt-retry",
                organization_id=None,
                realtime_session_id=realtime_session_id,
                text="Please call me back.",
                idempotency_key="call-rt-retry:seg-1",
                process_turn=lambda: (_ for _ in ()).throw(RuntimeError("synthetic turn failure")),
            )
        except RuntimeError as exc:
            assert str(exc) == "synthetic turn failure"
        else:
            raise AssertionError("expected the first transcript commit to fail")

        failed_record = control_plane.idempotency.load(
            organization_id=None,
            scope="voice.final_transcript",
            idempotency_key="call-rt-retry:seg-1",
        )
        assert failed_record is not None
        assert failed_record.result_event_id is not None
        assert failed_record.result_ref["_status"] == "failed"
        assert failed_record.result_ref["_failure"]["type"] == "RuntimeError"
        assert failed_record.result_ref["_failure"]["retryable"] is True

        recovered = control_plane.commit_final_transcript(
            conversation_id="phone:call-rt-retry",
            organization_id=None,
            realtime_session_id=realtime_session_id,
            text="Please call me back.",
            idempotency_key="call-rt-retry:seg-1",
            process_turn=_successful_turn,
        )
        assert recovered.duplicate is False
        assert recovered.turn_result is not None
        assert recovered.turn_result.trace_id == "trace-recovered"
        assert recovered.idempotency.result_ref["trace_id"] == "trace-recovered"

        duplicate = control_plane.commit_final_transcript(
            conversation_id="phone:call-rt-retry",
            organization_id=None,
            realtime_session_id=realtime_session_id,
            text="Please call me back.",
            idempotency_key="call-rt-retry:seg-1",
            process_turn=lambda: (_ for _ in ()).throw(AssertionError("duplicate should not re-run process_turn")),
        )
        assert duplicate.duplicate is True
        assert duplicate.turn_result is None
        assert duplicate.idempotency.result_ref["trace_id"] == "trace-recovered"

        events = control_plane.events.replay(conversation_id="phone:call-rt-retry")
        event_names = [(event.family, event.name) for event in events]
        assert event_names.count(("voice", "final_transcript_observed")) == 1

    asyncio.run(run())


def test_internal_operations_require_dedicated_internal_secret(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        runtime_settings = RuntimeSettings(
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            provider_shared_secret="livekit-provider-secret",
            internal_api_secret="internal-ops-secret",
        )
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_settings.database_url,
            runtime_settings=runtime_settings,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            unauthorized = await client.post("/agents:reload")
            assert unauthorized.status_code == 403

            with_provider_secret = await client.post(
                "/agents:reload",
                headers={"X-Ruhu-Internal-Secret": "livekit-provider-secret"},
            )
            assert with_provider_secret.status_code == 403

            with_internal_secret = await client.post(
                "/agents:reload",
                headers={"X-Ruhu-Internal-Secret": "internal-ops-secret"},
            )
            assert with_internal_secret.status_code == 200
            assert with_internal_secret.json()["agent_count"] >= 1

    asyncio.run(run())


def test_phone_partial_transcript_is_observed_but_not_committed(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        runtime_settings = RuntimeSettings(
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            provider_shared_secret="livekit-provider-secret",
            internal_api_secret="internal-ops-secret",
        )
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_settings.database_url,
            runtime_settings=runtime_settings,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            start = await client.post(
                "/providers/livekit/phone/calls/start",
                json={"agent_id": "sales", "external_session_id": "call-rt-2"},
                headers={"X-Ruhu-Provider-Secret": "livekit-provider-secret"},
            )
            assert start.status_code == 200

            partial = await client.post(
                "/providers/livekit/phone/calls/call-rt-2/transcripts",
                json={"text": "I want", "is_final": False},
                headers={"X-Ruhu-Provider-Secret": "livekit-provider-secret"},
            )
            assert partial.status_code == 200
            assert partial.json()["trace_id"] is None

        control_plane = app.state.realtime_control_plane
        events = control_plane.events.replay(conversation_id="phone:call-rt-2")
        event_names = [(event.family, event.name) for event in events]
        assert ("voice", "partial_transcript_observed") in event_names
        assert ("voice", "final_transcript_observed") not in event_names
        assert ("message", "user_accepted") not in event_names

    asyncio.run(run())


def test_runtime_rejects_shared_internal_and_provider_secrets(postgres_database_url_factory) -> None:
    agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
    database_url = postgres_database_url_factory()
    try:
        build_default_app(
            agent_root=agent_root_path,
            database_url=database_url,
            runtime_settings=RuntimeSettings(
                database_url=database_url,
                interpreter_name="sales",
                provider_shared_secret="shared-secret",
                internal_api_secret="shared-secret",
            ),
        )
    except ValueError as exc:
        assert "internal_api_secret must be distinct from provider_shared_secret" in str(exc)
    else:
        raise AssertionError("expected build_default_app() to reject shared internal/provider secrets")


def test_synthetic_whatsapp_ingress_requires_provider_secret_when_configured(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        database_url = postgres_database_url_factory()
        runtime_settings = RuntimeSettings(
            database_url=database_url,
            interpreter_name="sales",
            provider_shared_secret="synthetic-provider-secret",
        )
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=database_url,
            runtime_settings=runtime_settings,
        )

        body = {
            "agent_id": "sales",
            "external_session_id": "wa-rt-secret",
            "text": "Can you explain what the product does?",
        }
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            missing = await client.post("/channels/whatsapp/messages", json=body)
            assert missing.status_code == 403

            wrong = await client.post(
                "/channels/whatsapp/messages",
                json=body,
                headers={"X-Ruhu-Provider-Secret": "wrong-secret"},
            )
            assert wrong.status_code == 403

            accepted = await client.post(
                "/channels/whatsapp/messages",
                json=body,
                headers={"X-Ruhu-Provider-Secret": "synthetic-provider-secret"},
            )
            assert accepted.status_code == 200
            assert accepted.json()["conversation_id"] == "whatsapp:wa-rt-secret"

    asyncio.run(run())


def test_whatsapp_ingress_creates_provider_linked_session_and_inbound_event(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/channels/whatsapp/messages",
                json={
                    "agent_id": "sales",
                    "external_session_id": "wa-rt-1",
                    "text": "Can you explain what the product does?",
                    "idempotency_key": "wamid.1",
                    "provider": "meta_whatsapp",
                    "provider_session_id": "phone-number-id-1",
                    "participant_identity": "15551234567",
                },
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["conversation_id"] == "whatsapp:wa-rt-1"
            assert payload["realtime_session_id"]
            assert payload["messages"]

        control_plane = app.state.realtime_control_plane
        sessions = control_plane.sessions.list_by_conversation("whatsapp:wa-rt-1")
        assert len(sessions) == 1
        assert sessions[0].provider == "meta_whatsapp"
        assert sessions[0].provider_session_id == "phone-number-id-1"
        assert sessions[0].participant_identity == "15551234567"

        events = control_plane.events.replay(conversation_id="whatsapp:wa-rt-1")
        event_names = [(event.family, event.name) for event in events]
        assert ("session", "started") in event_names
        assert ("message", "inbound_observed") in event_names
        assert ("message", "user_accepted") in event_names

    asyncio.run(run())


def test_provider_cost_records_are_persisted_for_phone_runtime_events(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        runtime_settings = RuntimeSettings(
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            provider_shared_secret="livekit-provider-secret",
            internal_api_secret="internal-ops-secret",
        )
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_settings.database_url,
            runtime_settings=runtime_settings,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            start = await client.post(
                "/providers/livekit/phone/calls/start",
                json={
                    "agent_id": "sales",
                    "external_session_id": "call-rt-costs",
                    "metadata": {"provider_cost_usd": 0.11, "cost_type": "provider_session_start"},
                },
                headers={"X-Ruhu-Provider-Secret": "livekit-provider-secret"},
            )
            assert start.status_code == 200

            transcript = await client.post(
                "/providers/livekit/phone/calls/call-rt-costs/transcripts",
                json={
                    "text": "Tell me about pricing.",
                    "is_final": True,
                    "idempotency_key": "call-rt-costs:seg-1",
                    "metadata": {"provider_cost_usd": 0.03, "cost_type": "provider_turn_ingress"},
                },
                headers={"X-Ruhu-Provider-Secret": "livekit-provider-secret"},
            )
            assert transcript.status_code == 200

            costs = await client.get("/conversations/phone:call-rt-costs/provider-cost-records")
            assert costs.status_code == 200
            payload = costs.json()
            assert len(payload["items"]) == 2
            assert {item["cost_type"] for item in payload["items"]} == {
                "provider_session_start",
                "provider_turn_ingress",
            }
            assert round(sum(item["amount_usd"] for item in payload["items"]), 2) == 0.14

        control_plane = app.state.realtime_control_plane
        events = control_plane.events.replay(conversation_id="phone:call-rt-costs")
        event_names = [(event.family, event.name) for event in events]
        assert ("provider", "cost_recorded") in event_names

    asyncio.run(run())


def test_phone_disconnect_marks_session_and_blocks_further_transcripts(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        runtime_settings = RuntimeSettings(
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            provider_shared_secret="livekit-provider-secret",
        )
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_settings.database_url,
            runtime_settings=runtime_settings,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            start = await client.post(
                "/providers/livekit/phone/calls/start",
                json={"agent_id": "sales", "external_session_id": "call-rt-disconnect"},
                headers={"X-Ruhu-Provider-Secret": "livekit-provider-secret"},
            )
            assert start.status_code == 200

            disconnected = await client.post(
                "/providers/livekit/phone/calls/call-rt-disconnect/disconnect",
                json={"reason": "room_lost", "metadata": {"source": "livekit_webhook"}},
                headers={"X-Ruhu-Provider-Secret": "livekit-provider-secret"},
            )
            assert disconnected.status_code == 200
            disconnected_payload = disconnected.json()
            assert disconnected_payload["status"] == "disconnected"

            late_transcript = await client.post(
                "/providers/livekit/phone/calls/call-rt-disconnect/transcripts",
                json={"text": "I am still here", "is_final": True, "idempotency_key": "call-rt-disconnect:late-1"},
                headers={"X-Ruhu-Provider-Secret": "livekit-provider-secret"},
            )
            assert late_transcript.status_code == 409

        control_plane = app.state.realtime_control_plane
        sessions = control_plane.sessions.list_by_conversation("phone:call-rt-disconnect")
        assert len(sessions) == 1
        assert sessions[0].status == "disconnected"

        events = control_plane.events.replay(conversation_id="phone:call-rt-disconnect")
        event_names = [(event.family, event.name) for event in events]
        assert ("session", "disconnected") in event_names

    asyncio.run(run())


def test_reconcile_stale_voice_sessions_marks_them_disconnected(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        runtime_settings = RuntimeSettings(
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            provider_shared_secret="livekit-provider-secret",
            internal_api_secret="internal-ops-secret",
        )
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_settings.database_url,
            runtime_settings=runtime_settings,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            start = await client.post(
                "/providers/livekit/phone/calls/start",
                json={"agent_id": "sales", "external_session_id": "call-rt-stale"},
                headers={"X-Ruhu-Provider-Secret": "livekit-provider-secret"},
            )
            assert start.status_code == 200

            control_plane = app.state.realtime_control_plane
            session = control_plane.sessions.list_by_conversation("phone:call-rt-stale")[0]
            old_time = datetime.now(timezone.utc) - timedelta(minutes=15)
            session.last_seen_at = old_time
            session.updated_at = old_time
            control_plane.sessions.save(session)

            unauthorized_reconcile = await client.post(
                "/internal/realtime/voice-sessions/reconcile",
                json={"stale_seconds": 300, "provider": "livekit"},
            )
            assert unauthorized_reconcile.status_code == 403

            reconcile = await client.post(
                "/internal/realtime/voice-sessions/reconcile",
                json={"stale_seconds": 300, "provider": "livekit"},
                headers={"X-Ruhu-Internal-Secret": "internal-ops-secret"},
            )
            assert reconcile.status_code == 200
            reconcile_payload = reconcile.json()
            assert reconcile_payload["reconciled"] == 1
            assert reconcile_payload["sessions"][0]["status"] == "disconnected"

        control_plane = app.state.realtime_control_plane
        sessions = control_plane.sessions.list_by_conversation("phone:call-rt-stale")
        assert sessions[0].status == "disconnected"

        events = control_plane.events.replay(conversation_id="phone:call-rt-stale")
        matching = [
            event
            for event in events
            if event.family == "session" and event.name == "disconnected"
        ]
        assert matching
        assert matching[-1].payload.get("reason") == "reconciled_stale_voice_session"

    asyncio.run(run())
