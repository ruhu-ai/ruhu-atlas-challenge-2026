from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from ruhu.api import build_default_app
from ruhu.runtime_config import RuntimeSettings
from tests.test_api import _authorize_client, _private_key_pem, _seed_authenticated_api_store


def _build_authenticated_phone_registry_app(
    *,
    agent_root: Path,
    database_url: str,
    auth_database_url: str,
):
    return build_default_app(
        agent_root=agent_root,
        database_url=database_url,
        interpreter_name="sales",
        runtime_settings=RuntimeSettings(
            auth_database_url=auth_database_url,
            auth_jwt_private_key_pem=_private_key_pem(),
            auth_jwt_active_kid="kid-phone-registry-tests",
            auth_allowed_redirect_origins=["http://testserver"],
            journey_runtime_embedded_worker_enabled=False,
        ),
    )


def test_phone_number_registry_admin_can_manage_numbers_bindings_and_routes(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)
        app = _build_authenticated_phone_registry_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            auth_database_url=auth_database_url,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=app.state.auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )

            created_number = await client.post(
                "/phone-numbers",
                json={
                    "e164_number": "+234 801 234 5678",
                    "display_name": "Nigeria demo line",
                    "metadata": {"source": "manual_import"},
                },
            )
            assert created_number.status_code == 201
            number_payload = created_number.json()
            phone_number_id = number_payload["phone_number_id"]
            assert number_payload["organization_id"] == "org-1"
            assert number_payload["e164_number"] == "+2348012345678"
            assert number_payload["country_code"] == "NG"

            listed_numbers = await client.get("/phone-numbers")
            assert listed_numbers.status_code == 200
            assert [item["phone_number_id"] for item in listed_numbers.json()] == [phone_number_id]

            initial_detail = await client.get(f"/phone-numbers/{phone_number_id}")
            assert initial_detail.status_code == 200
            assert initial_detail.json()["bindings"] == []
            assert initial_detail.json()["routes"] == []

            created_binding = await client.post(
                f"/phone-numbers/{phone_number_id}/bindings",
                json={
                    "channel": "phone",
                    "provider": "telnyx",
                    "provider_resource_id": "tn-number-1",
                    "verification_status": "verified",
                    "health_status": "healthy",
                    "transport_metadata": {"transport_provider": "livekit"},
                },
            )
            assert created_binding.status_code == 201
            binding_payload = created_binding.json()
            binding_id = binding_payload["binding_id"]
            assert binding_payload["capabilities"] == ["voice_inbound"]

            listed_bindings = await client.get(f"/phone-numbers/{phone_number_id}/bindings")
            assert listed_bindings.status_code == 200
            assert [item["binding_id"] for item in listed_bindings.json()] == [binding_id]

            primary_route = await client.post(
                f"/phone-numbers/{phone_number_id}/routes",
                json={
                    "channel": "phone",
                    "agent_id": "sales",
                    "priority": 100,
                    "metadata": {"purpose": "sales_primary"},
                },
            )
            assert primary_route.status_code == 201
            primary_route_id = primary_route.json()["route_id"]
            assert primary_route.json()["enabled"] is True

            candidate_route = await client.post(
                f"/phone-numbers/{phone_number_id}/routes",
                json={
                    "channel": "phone",
                    "agent_id": "sales",
                    "priority": 50,
                    "enabled": False,
                    "metadata": {"purpose": "candidate"},
                },
            )
            assert candidate_route.status_code == 201
            candidate_route_id = candidate_route.json()["route_id"]
            assert candidate_route.json()["enabled"] is False

            listed_routes = await client.get(f"/phone-numbers/{phone_number_id}/routes")
            assert listed_routes.status_code == 200
            routes_by_id = {item["route_id"]: item for item in listed_routes.json()}
            assert routes_by_id[primary_route_id]["enabled"] is True
            assert routes_by_id[candidate_route_id]["enabled"] is False

            promoted_route = await client.patch(
                f"/phone-numbers/{phone_number_id}/routes/{candidate_route_id}",
                json={"enabled": True, "priority": 25, "metadata": {"purpose": "sales_primary_v2"}},
            )
            assert promoted_route.status_code == 200
            assert promoted_route.json()["enabled"] is True
            assert promoted_route.json()["priority"] == 25

            updated_binding = await client.patch(
                f"/phone-numbers/{phone_number_id}/bindings/{binding_id}",
                json={
                    "health_status": "degraded",
                    "transport_metadata": {
                        "transport_provider": "livekit",
                        "sip_trunk_id": "trunk-1",
                    },
                },
            )
            assert updated_binding.status_code == 200
            assert updated_binding.json()["health_status"] == "degraded"
            assert updated_binding.json()["transport_metadata"]["sip_trunk_id"] == "trunk-1"

            updated_number = await client.patch(
                f"/phone-numbers/{phone_number_id}",
                json={
                    "display_name": "Nigeria primary line",
                    "metadata": {"source": "ops_console"},
                },
            )
            assert updated_number.status_code == 200
            assert updated_number.json()["display_name"] == "Nigeria primary line"

            final_detail = await client.get(f"/phone-numbers/{phone_number_id}")
            assert final_detail.status_code == 200
            final_payload = final_detail.json()
            assert final_payload["number"]["display_name"] == "Nigeria primary line"
            assert final_payload["number"]["metadata"] == {"source": "ops_console"}
            assert len(final_payload["bindings"]) == 1
            assert final_payload["bindings"][0]["binding_id"] == binding_id
            assert final_payload["bindings"][0]["health_status"] == "degraded"
            final_routes_by_id = {item["route_id"]: item for item in final_payload["routes"]}
            # Both routes remain enabled; the candidate (priority=25) wins over the
            # primary (priority=100) when resolve_route picks by priority ASC.
            assert final_routes_by_id[primary_route_id]["enabled"] is True
            assert final_routes_by_id[candidate_route_id]["enabled"] is True
            assert final_routes_by_id[candidate_route_id]["metadata"] == {"purpose": "sales_primary_v2"}

    asyncio.run(run())


def test_phone_number_registry_enforces_role_boundaries_and_agent_validation(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)
        app = _build_authenticated_phone_registry_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            auth_database_url=auth_database_url,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as admin_client:
            _authorize_client(
                admin_client,
                auth_service=app.state.auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )

            created_number = await admin_client.post(
                "/phone-numbers",
                json={"e164_number": "+14155550123", "display_name": "US demo line"},
            )
            assert created_number.status_code == 201
            phone_number_id = created_number.json()["phone_number_id"]

            duplicate_number = await admin_client.post(
                "/phone-numbers",
                json={"e164_number": "+1 415 555 0123"},
            )
            assert duplicate_number.status_code == 409

            invalid_route = await admin_client.post(
                f"/phone-numbers/{phone_number_id}/routes",
                json={"agent_id": "missing_agent"},
            )
            assert invalid_route.status_code == 404

            created_binding = await admin_client.post(
                f"/phone-numbers/{phone_number_id}/bindings",
                json={
                    "channel": "phone",
                    "provider": "africastalking",
                    "provider_resource_id": "+14155550123",
                    "verification_status": "verified",
                    "health_status": "healthy",
                    "transport_metadata": {
                        "africastalking": {
                            "provider_resource_id": "+14155550123",
                            "phone_number": "+14155550123",
                            "account_username": "sandbox",
                            "voice_callback_url": "trunk:livekit.example.test",
                            "credentials_reference": "ops/africastalking/main",
                            "ip_whitelist_confirmed": True,
                            "sip_forwarding_confirmed": True,
                            "configuration_confirmed": True,
                            "manual_requirements": [],
                        }
                    },
                },
            )
            assert created_binding.status_code == 201

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as analyst_client:
            _authorize_client(
                analyst_client,
                auth_service=app.state.auth_service,
                user_id="user-analyst",
                organization_id="org-1",
            )

            forbidden_create = await analyst_client.post(
                "/phone-numbers",
                json={"e164_number": "+447700900123"},
            )
            assert forbidden_create.status_code == 403

            listed_numbers = await analyst_client.get("/phone-numbers")
            assert listed_numbers.status_code == 200
            assert listed_numbers.json()[0]["phone_number_id"] == phone_number_id

            detail = await analyst_client.get(f"/phone-numbers/{phone_number_id}")
            assert detail.status_code == 200

            bindings = await analyst_client.get(f"/phone-numbers/{phone_number_id}/bindings")
            assert bindings.status_code == 200
            assert bindings.json()[0]["provider"] == "africastalking"

            routes = await analyst_client.get(f"/phone-numbers/{phone_number_id}/routes")
            assert routes.status_code == 200
            assert routes.json() == []

            audit = await analyst_client.get("/phone-numbers/audit", params={"phone_number_id": phone_number_id})
            assert audit.status_code == 200
            actions = [item["action"] for item in audit.json()]
            assert "phone.number.created" in actions
            assert "phone.binding.created" in actions

    asyncio.run(run())


def test_phone_number_reconciliation_updates_binding_writes_audit_and_emits_notification(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)
        app = _build_authenticated_phone_registry_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            auth_database_url=auth_database_url,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=app.state.auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )

            created_number = await client.post(
                "/phone-numbers",
                json={
                    "e164_number": "+2348012345678",
                    "display_name": "Nigeria support line",
                },
            )
            assert created_number.status_code == 201
            phone_number_id = created_number.json()["phone_number_id"]

            created_binding = await client.post(
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
            binding_id = created_binding.json()["binding_id"]

            reconciled = await client.post(
                "/phone-numbers/reconcile",
                json={"phone_number_id": phone_number_id},
            )
            assert reconciled.status_code == 200
            payload = reconciled.json()
            assert payload["processed_count"] == 1
            assert payload["changed_count"] == 1
            assert payload["failed_count"] == 0
            assert payload["results"][0]["binding_id"] == binding_id
            assert payload["results"][0]["operation_status"] == "updated"
            assert payload["results"][0]["verification_status"] == "manual_required"
            assert payload["results"][0]["health_status"] == "misconfigured"
            assert payload["results"][0]["notification_emitted"] is True

            detail = await client.get(f"/phone-numbers/{phone_number_id}")
            assert detail.status_code == 200
            binding_payload = detail.json()["bindings"][0]
            assert binding_payload["verification_status"] == "manual_required"
            assert binding_payload["health_status"] == "misconfigured"
            assert binding_payload["transport_metadata"]["reconciliation"]["status"] == "ok"

            audit = await client.get("/phone-numbers/audit", params={"phone_number_id": phone_number_id})
            assert audit.status_code == 200
            actions = [item["action"] for item in audit.json()]
            assert "phone.binding.reconciled" in actions
            reconcile_event = next(item for item in audit.json() if item["action"] == "phone.binding.reconciled")
            assert reconcile_event["resource_id"] == binding_id

            notifications = app.state.notification_store.list_for_user(
                "org-1",
                "user-admin",
                limit=10,
                include_expired=True,
            )
            assert any(item.category == "phone.binding_attention_required" for item in notifications)

    asyncio.run(run())


def test_internal_phone_number_route_resolve_returns_registry_route(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        runtime_settings = RuntimeSettings(
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            internal_api_secret="internal-ops-secret",
            journey_runtime_embedded_worker_enabled=False,
        )
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_settings.database_url,
            runtime_settings=runtime_settings,
        )
        registry = app.state.phone_number_registry
        number = registry.create_number(
            organization_id="org-demo",
            e164_number="+2348012345678",
            display_name="Nigeria support line",
            metadata={"source": "registry_seed"},
        )
        binding = registry.create_binding(
            phone_number_id=number.phone_number_id,
            organization_id="org-demo",
            channel="phone",
            provider="telnyx",
            provider_resource_id="tn-number-2",
            verification_status="verified",
            health_status="healthy",
        )
        route = registry.create_or_replace_route(
            phone_number_id=number.phone_number_id,
            organization_id="org-demo",
            channel="phone",
            agent_id="sales",
            metadata={"route_source": "database"},
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            unauthorized = await client.post(
                "/internal/phone-number-routes/resolve",
                json={"phone_number": "+2348012345678", "provider": "telnyx"},
                headers={"X-Ruhu-Internal-Secret": "wrong-secret"},
            )
            assert unauthorized.status_code == 403

            resolved = await client.post(
                "/internal/phone-number-routes/resolve",
                json={"phone_number": "+234 801 234 5678", "provider": "telnyx"},
                headers={"X-Ruhu-Internal-Secret": "internal-ops-secret"},
            )
            assert resolved.status_code == 200
            payload = resolved.json()
            assert payload["route_key"] == route.route_id
            assert payload["phone_number"] == "+2348012345678"
            assert payload["organization_id"] == "org-demo"
            assert payload["provider"] == "telnyx"
            assert payload["provider_resource_id"] == "tn-number-2"
            assert payload["display_name"] == "Nigeria support line"
            assert payload["country_code"] == "NG"
            assert payload["metadata"]["phone_number_id"] == number.phone_number_id
            assert payload["metadata"]["binding_id"] == binding.binding_id
            assert payload["metadata"]["route_source"] == "database"

    asyncio.run(run())


def test_provider_phone_start_prefers_db_registry_route_over_env_config(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        runtime_settings = RuntimeSettings(
            database_url=postgres_database_url_factory(),
            interpreter_name="sales",
            provider_shared_secret="livekit-provider-secret",
            journey_runtime_embedded_worker_enabled=False,
            phone_number_routes={
                "env_route": {
                    "phone_number": "+2348012345678",
                    "agent_id": "sales",
                    "organization_id": "org-env",
                    "provider": "africastalking",
                    "display_name": "Env fallback line",
                }
            },
        )
        app = build_default_app(
            agent_root=agent_root_path,
            database_url=runtime_settings.database_url,
            runtime_settings=runtime_settings,
        )
        registry = app.state.phone_number_registry
        number = registry.create_number(
            organization_id="org-db",
            e164_number="+2348012345678",
            display_name="DB-routed line",
        )
        binding = registry.create_binding(
            phone_number_id=number.phone_number_id,
            organization_id="org-db",
            channel="phone",
            provider="telnyx",
            provider_resource_id="tn-number-db-1",
            verification_status="verified",
            health_status="healthy",
        )
        route = registry.create_or_replace_route(
            phone_number_id=number.phone_number_id,
            organization_id="org-db",
            channel="phone",
            agent_id="sales",
            metadata={"route_source": "database"},
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            start = await client.post(
                "/providers/livekit/phone/calls/start",
                json={
                    "external_session_id": "call-db-route-1",
                    "provider": "telnyx",
                    "metadata": {
                        "to_number": "+234 801 234 5678",
                        "from_number": "+14155550123",
                    },
                },
                headers={"X-Ruhu-Provider-Secret": "livekit-provider-secret"},
            )
            assert start.status_code == 200

        control_plane = app.state.realtime_control_plane
        sessions = control_plane.sessions.list_by_conversation("phone:call-db-route-1")
        assert len(sessions) == 1
        session = sessions[0]
        assert session.organization_id == "org-db"
        assert session.provider == "livekit"
        assert app.state.phone_number_routes["env_route"].organization_id == "org-env"
        assert session.transport_metadata["transport_provider"] == "livekit"
        assert session.transport_metadata["telephony_provider"] == "telnyx"
        assert session.transport_metadata["phone_number_route_key"] == route.route_id
        assert session.transport_metadata["phone_number_route_key"] != "env_route"
        assert session.transport_metadata["resolved_phone_number"] == "+2348012345678"
        assert session.transport_metadata["provider_resource_id"] == "tn-number-db-1"
        assert session.transport_metadata["phone_number_id"] == number.phone_number_id
        assert session.transport_metadata["binding_id"] == binding.binding_id
        assert session.transport_metadata["route_source"] == "database"

    asyncio.run(run())


# ── Phone number list pagination (Issue 5) ────────────────────────────────────

def test_list_phone_numbers_pagination_limit_and_offset(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)
        app = _build_authenticated_phone_registry_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            auth_database_url=auth_database_url,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=app.state.auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )

            # Create 5 phone numbers
            numbers = []
            for i in range(5):
                resp = await client.post(
                    "/phone-numbers",
                    json={"e164_number": f"+23480100{i:05d}", "display_name": f"Test line {i}"},
                )
                assert resp.status_code == 201
                numbers.append(resp.json()["phone_number_id"])

            # Default list should return all 5
            all_resp = await client.get("/phone-numbers")
            assert all_resp.status_code == 200
            assert len(all_resp.json()) == 5

            # limit=2 should return exactly 2
            limited_resp = await client.get("/phone-numbers", params={"limit": 2})
            assert limited_resp.status_code == 200
            assert len(limited_resp.json()) == 2

            # limit=2, offset=2 should return the next 2
            page2_resp = await client.get("/phone-numbers", params={"limit": 2, "offset": 2})
            assert page2_resp.status_code == 200
            assert len(page2_resp.json()) == 2

            # Pages should be disjoint
            page1_ids = {n["phone_number_id"] for n in limited_resp.json()}
            page2_ids = {n["phone_number_id"] for n in page2_resp.json()}
            assert page1_ids.isdisjoint(page2_ids)

            # limit=2, offset=4 → only 1 remaining
            page3_resp = await client.get("/phone-numbers", params={"limit": 2, "offset": 4})
            assert page3_resp.status_code == 200
            assert len(page3_resp.json()) == 1

    asyncio.run(run())


def test_list_phone_numbers_status_filter(postgres_database_url_factory) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)
        app = _build_authenticated_phone_registry_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            auth_database_url=auth_database_url,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=app.state.auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )

            # Create 2 active and 1 suspended number
            active_resp = await client.post(
                "/phone-numbers",
                json={"e164_number": "+2348011111111", "display_name": "Active 1"},
            )
            assert active_resp.status_code == 201

            active_resp2 = await client.post(
                "/phone-numbers",
                json={"e164_number": "+2348022222222", "display_name": "Active 2"},
            )
            assert active_resp2.status_code == 201

            suspended_resp = await client.post(
                "/phone-numbers",
                json={"e164_number": "+2348033333333", "display_name": "To suspend"},
            )
            assert suspended_resp.status_code == 201
            suspended_id = suspended_resp.json()["phone_number_id"]

            # Suspend the third number
            patch_resp = await client.patch(
                f"/phone-numbers/{suspended_id}",
                json={"status": "suspended"},
            )
            assert patch_resp.status_code == 200

            # Filter by status=active → 2 results
            active_filter = await client.get("/phone-numbers", params={"status": "active"})
            assert active_filter.status_code == 200
            active_numbers = active_filter.json()
            assert len(active_numbers) == 2
            assert all(n["status"] == "active" for n in active_numbers)

            # Filter by status=suspended → 1 result
            suspended_filter = await client.get("/phone-numbers", params={"status": "suspended"})
            assert suspended_filter.status_code == 200
            suspended_numbers = suspended_filter.json()
            assert len(suspended_numbers) == 1
            assert suspended_numbers[0]["phone_number_id"] == suspended_id

    asyncio.run(run())
