from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from ruhu.api import build_default_app
from ruhu.phone_provider_telnyx import TelnyxPhoneProvider
from ruhu.runtime_config import RuntimeSettings
from tests.test_api import _authorize_client, _private_key_pem, _seed_authenticated_api_store


def _build_authenticated_telnyx_app(
    *,
    agent_root: Path,
    database_url: str,
    auth_database_url: str,
    telnyx_api_key: str | None,
):
    return build_default_app(
        agent_root=agent_root,
        database_url=database_url,
        interpreter_name="sales",
        runtime_settings=RuntimeSettings(
            auth_database_url=auth_database_url,
            auth_jwt_private_key_pem=_private_key_pem(),
            auth_jwt_active_kid="kid-telnyx-tests",
            auth_allowed_redirect_origins=["http://testserver"],
            telnyx_api_key=telnyx_api_key,
            journey_runtime_embedded_worker_enabled=False,
        ),
    )


def test_telnyx_phone_provider_parses_lookup_and_available_number_payloads() -> None:
    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/phone_numbers/1293384261075731499"):
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "id": "1293384261075731499",
                            "phone_number": "+2348012345678",
                            "country_iso_alpha2": "NG",
                            "status": "active",
                            "phone_number_type": "local",
                            "connection_id": "1293384261075731400",
                            "connection_name": "lagos-sales",
                            "customer_reference": "ops-ref-1",
                            "messaging_profile_id": "profile-1",
                            "messaging_profile_name": "regional-customers",
                            "billing_group_id": "group-1",
                            "emergency_enabled": True,
                            "emergency_status": "active",
                            "call_forwarding_enabled": False,
                            "inbound_call_screening": "disabled",
                            "hd_voice_enabled": True,
                            "source_type": "number_order",
                            "purchased_at": "2026-04-01T10:00:00Z",
                            "created_at": "2026-04-01T10:00:00Z",
                            "updated_at": "2026-04-02T10:00:00Z",
                            "tags": ["sales", "nigeria"],
                        }
                    },
                )
            if request.url.path.endswith("/phone_numbers/1293384261075731499/voice"):
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "id": "1293384261075731499",
                            "connection_id": "1293384261075731400",
                            "customer_reference": "ops-ref-1",
                            "translated_number": "+2348012345678",
                            "usage_payment_method": "pay-per-minute",
                            "inbound_call_screening": "disabled",
                            "tech_prefix_enabled": False,
                            "call_forwarding": {
                                "call_forwarding_enabled": False,
                                "forwarding_type": "always",
                            },
                            "emergency": {
                                "emergency_enabled": True,
                                "emergency_status": "active",
                            },
                            "media_features": {
                                "rtp_auto_adjust_enabled": True,
                            },
                        }
                    },
                )
            if request.url.path.endswith("/available_phone_numbers"):
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "phone_number": "+2348099990001",
                                "phone_number_type": "local",
                                "quickship": True,
                                "reservable": True,
                                "features": [{"name": "voice"}, {"name": "sms"}],
                                "cost_information": {
                                    "monthly_cost": "10.00",
                                    "upfront_cost": "1.00",
                                    "currency": "USD",
                                },
                                "region_information": [
                                    {"region_type": "country_code", "region_name": "NG"},
                                    {"region_type": "locality", "region_name": "Lagos"},
                                ],
                            }
                        ]
                    },
                )
            return httpx.Response(404, json={"errors": [{"detail": "not found"}]})

        provider_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.telnyx.com/v2",
        )
        provider = TelnyxPhoneProvider(api_key="telnyx-test-key", http_client=provider_client)
        snapshot = await provider.lookup_phone_number(provider_resource_id="1293384261075731499")
        assert snapshot.phone_number.phone_number == "+2348012345678"
        assert snapshot.phone_number.country_code == "NG"
        assert snapshot.phone_number.connection_name == "lagos-sales"
        assert snapshot.phone_number.tags == ["sales", "nigeria"]
        assert snapshot.voice_settings is not None
        assert snapshot.voice_settings.connection_id == "1293384261075731400"
        assert snapshot.voice_settings.media_features["rtp_auto_adjust_enabled"] is True

        numbers = await provider.list_available_phone_numbers(country_code="NG", limit=1)
        assert len(numbers) == 1
        assert numbers[0].phone_number == "+2348099990001"
        assert numbers[0].country_code == "NG"
        assert numbers[0].locality == "Lagos"
        assert numbers[0].features == ["voice", "sms"]
        await provider_client.aclose()

    asyncio.run(run())


def test_telnyx_phone_provider_api_import_sync_and_available_lookup(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)
        app = _build_authenticated_telnyx_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            auth_database_url=auth_database_url,
            telnyx_api_key="telnyx-test-key",
        )

        provider_state = {"degraded": False}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/available_phone_numbers"):
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "phone_number": "+2348099990001",
                                "phone_number_type": "local",
                                "quickship": True,
                                "reservable": True,
                                "features": [{"name": "voice"}],
                                "cost_information": {
                                    "monthly_cost": "10.00",
                                    "upfront_cost": "1.00",
                                    "currency": "USD",
                                },
                                "region_information": [
                                    {"region_type": "country_code", "region_name": "NG"},
                                    {"region_type": "locality", "region_name": "Lagos"},
                                ],
                            }
                        ]
                    },
                )
            if request.url.path.endswith("/phone_numbers/1293384261075731499"):
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "id": "1293384261075731499",
                            "phone_number": "+2348012345678",
                            "country_iso_alpha2": "NG",
                            "status": "active",
                            "phone_number_type": "local",
                            "connection_id": None if provider_state["degraded"] else "1293384261075731400",
                            "connection_name": "lagos-sales",
                            "customer_reference": "ops-ref-1",
                            "messaging_profile_id": "profile-1",
                            "messaging_profile_name": "regional-customers",
                            "billing_group_id": "group-1",
                            "emergency_enabled": True,
                            "emergency_status": "active",
                            "call_forwarding_enabled": False,
                            "inbound_call_screening": "disabled",
                            "hd_voice_enabled": True,
                            "source_type": "number_order",
                            "tags": ["sales", "nigeria"],
                        }
                    },
                )
            if request.url.path.endswith("/phone_numbers/1293384261075731499/voice"):
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "id": "1293384261075731499",
                            "connection_id": None if provider_state["degraded"] else "1293384261075731400",
                            "customer_reference": "ops-ref-1",
                            "translated_number": "+2348012345678",
                            "usage_payment_method": "pay-per-minute",
                            "inbound_call_screening": "disabled",
                            "tech_prefix_enabled": False,
                            "call_forwarding": {
                                "call_forwarding_enabled": False,
                                "forwarding_type": "always",
                            },
                            "emergency": {
                                "emergency_enabled": True,
                                "emergency_status": "active",
                            },
                            "media_features": {
                                "rtp_auto_adjust_enabled": True,
                            },
                        }
                    },
                )
            return httpx.Response(404, json={"errors": [{"detail": "not found"}]})

        app.state.telnyx_http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.telnyx.com/v2",
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=app.state.auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )

            available = await client.get(
                "/phone-providers/telnyx/available-numbers",
                params={"country_code": "NG", "limit": 1},
            )
            assert available.status_code == 200
            available_payload = available.json()
            assert len(available_payload) == 1
            assert available_payload[0]["phone_number"] == "+2348099990001"

            imported = await client.post(
                "/phone-providers/telnyx/import",
                json={
                    "provider_resource_id": "1293384261075731499",
                    "display_name": "Nigeria sales line",
                    "metadata": {"source": "telnyx_import"},
                },
            )
            assert imported.status_code == 200
            imported_payload = imported.json()
            assert imported_payload["created_number"] is True
            assert imported_payload["created_binding"] is True
            assert imported_payload["number"]["e164_number"] == "+2348012345678"
            assert imported_payload["number"]["ownership_mode"] == "provider_managed"
            assert imported_payload["binding"]["provider"] == "telnyx"
            assert imported_payload["binding"]["provider_resource_id"] == "1293384261075731499"
            assert imported_payload["binding"]["verification_status"] == "verified"
            assert imported_payload["binding"]["health_status"] == "healthy"
            assert imported_payload["binding"]["transport_metadata"]["telnyx"]["connection_id"] == "1293384261075731400"
            assert imported_payload["provider_number"]["connection_name"] == "lagos-sales"
            assert imported_payload["voice_settings"]["connection_id"] == "1293384261075731400"

            provider_state["degraded"] = True
            synced = await client.post(
                f"/phone-numbers/{imported_payload['number']['phone_number_id']}/bindings/{imported_payload['binding']['binding_id']}/providers/telnyx/sync"
            )
            assert synced.status_code == 200
            synced_payload = synced.json()
            assert synced_payload["created_number"] is False
            assert synced_payload["created_binding"] is False
            assert synced_payload["binding"]["verification_status"] == "manual_required"
            assert synced_payload["binding"]["health_status"] == "misconfigured"
            assert synced_payload["binding"]["transport_metadata"]["telnyx"]["voice_settings"]["connection_id"] is None

        await app.state.telnyx_http_client.aclose()

    asyncio.run(run())


def test_telnyx_phone_provider_api_returns_503_when_not_configured(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)
        app = _build_authenticated_telnyx_app(
            agent_root=agent_root_path,
            database_url=postgres_database_url_factory(),
            auth_database_url=auth_database_url,
            telnyx_api_key=None,
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            _authorize_client(
                client,
                auth_service=app.state.auth_service,
                user_id="user-admin",
                organization_id="org-1",
            )
            imported = await client.post(
                "/phone-providers/telnyx/import",
                json={"phone_number": "+2348012345678"},
            )
            assert imported.status_code == 503

    asyncio.run(run())
