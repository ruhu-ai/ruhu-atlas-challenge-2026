from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx

from ruhu.api import build_default_app
from ruhu.phone_provider_africastalking import (
    AfricasTalkingPhoneProvider,
    build_africas_talking_snapshot,
    derive_africas_talking_binding_state,
)
from ruhu.runtime_config import RuntimeSettings
from tests.test_api import _authorize_client, _private_key_pem, _seed_authenticated_api_store


def _build_authenticated_africas_talking_app(
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
            auth_jwt_active_kid="kid-africastalking-tests",
            auth_allowed_redirect_origins=["http://testserver"],
            journey_runtime_embedded_worker_enabled=False,
        ),
    )


def test_africas_talking_binding_snapshot_derives_manual_requirements_and_health() -> None:
    degraded_snapshot = build_africas_talking_snapshot(
        phone_number="+2348012345678",
        account_username="sandbox",
        voice_callback_url="trunk:livekit.example.test",
        credentials_reference="ops/africastalking/main",
    )
    degraded_verification, degraded_health, degraded_capabilities = derive_africas_talking_binding_state(
        degraded_snapshot
    )
    assert degraded_verification == "manual_required"
    assert degraded_health == "degraded"
    assert degraded_capabilities == ["voice_inbound"]
    assert degraded_snapshot.manual_requirements == [
        "confirm_sip_forwarding",
        "confirm_ip_whitelist",
        "confirm_provider_configuration",
    ]
    assert degraded_snapshot.recommended_actions == [
        "configure_events_callback_url",
        "record_sip_trunk_target",
        "record_last_verified_at",
    ]

    misconfigured_snapshot = build_africas_talking_snapshot(phone_number="+2348012345678")
    misconfigured_verification, misconfigured_health, _ = derive_africas_talking_binding_state(
        misconfigured_snapshot
    )
    assert misconfigured_verification == "manual_required"
    assert misconfigured_health == "misconfigured"
    assert "set_account_username" in misconfigured_snapshot.manual_requirements
    assert "configure_voice_callback_url" in misconfigured_snapshot.manual_requirements
    assert "record_sip_credentials_reference" in misconfigured_snapshot.manual_requirements


def test_africas_talking_phone_provider_api_import_and_sync_manual_state(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)
        app = _build_authenticated_africas_talking_app(
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

            imported = await client.post(
                "/phone-providers/africastalking/import",
                json={
                    "phone_number": "+234 801 234 5678",
                    "display_name": "Nigeria support line",
                    "account_username": "sandbox",
                    "voice_callback_url": "trunk:livekit.example.test",
                    "credentials_reference": "ops/africastalking/main",
                    "metadata": {"source": "manual_at_import"},
                },
            )
            assert imported.status_code == 200
            imported_payload = imported.json()
            assert imported_payload["created_number"] is True
            assert imported_payload["created_binding"] is True
            assert imported_payload["number"]["e164_number"] == "+2348012345678"
            assert imported_payload["number"]["ownership_mode"] == "provider_managed"
            assert imported_payload["binding"]["provider"] == "africastalking"
            assert imported_payload["binding"]["provider_resource_id"] == "+2348012345678"
            assert imported_payload["binding"]["verification_status"] == "manual_required"
            assert imported_payload["binding"]["health_status"] == "degraded"
            assert imported_payload["provider_binding"]["manual_requirements"] == [
                "confirm_sip_forwarding",
                "confirm_ip_whitelist",
                "confirm_provider_configuration",
            ]
            assert imported_payload["provider_binding"]["recommended_actions"] == [
                "configure_events_callback_url",
                "record_sip_trunk_target",
                "record_last_verified_at",
            ]

            synced = await client.post(
                f"/phone-numbers/{imported_payload['number']['phone_number_id']}/bindings/{imported_payload['binding']['binding_id']}/providers/africastalking/sync",
                json={
                    "events_callback_url": "https://ops.example.test/africastalking/events",
                    "sip_trunk_target": "trunk:livekit.example.test",
                    "ip_whitelist_confirmed": True,
                    "sip_forwarding_confirmed": True,
                    "configuration_confirmed": True,
                    "last_verified_at": "2026-04-11T10:30:00Z",
                    "notes": "Operator confirmed dashboard setup",
                },
            )
            assert synced.status_code == 200
            synced_payload = synced.json()
            assert synced_payload["created_number"] is False
            assert synced_payload["created_binding"] is False
            assert synced_payload["binding"]["verification_status"] == "verified"
            assert synced_payload["binding"]["health_status"] == "healthy"
            assert synced_payload["binding"]["transport_metadata"]["africastalking"]["manual_requirements"] == []
            assert synced_payload["provider_binding"]["recommended_actions"] == []
            assert synced_payload["provider_binding"]["notes"] == "Operator confirmed dashboard setup"

    asyncio.run(run())


def test_africas_talking_phone_provider_api_import_tracks_misconfigured_manual_setup(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)
        app = _build_authenticated_africas_talking_app(
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

            imported = await client.post(
                "/phone-providers/africastalking/import",
                json={"phone_number": "+2348012345678"},
            )
            assert imported.status_code == 200
            payload = imported.json()
            assert payload["binding"]["provider"] == "africastalking"
            assert payload["binding"]["verification_status"] == "manual_required"
            assert payload["binding"]["health_status"] == "misconfigured"
            assert "set_account_username" in payload["provider_binding"]["manual_requirements"]
            assert "configure_voice_callback_url" in payload["provider_binding"]["manual_requirements"]
            assert "record_sip_credentials_reference" in payload["provider_binding"]["manual_requirements"]

    asyncio.run(run())


# ── AfricasTalkingPhoneProvider.validate_credentials ─────────────────────────

def _mock_http_response(*, status_code: int, body: bytes = b"{}") -> MagicMock:
    """Build a minimal httpx.Response-like mock."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = body
    resp.json.return_value = __import__("json").loads(body)
    return resp


def test_validate_credentials_returns_valid_true_on_200() -> None:
    async def run():
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_mock_http_response(
            status_code=200,
            body=b'{"UserData": {"balance": "KES 100.00", "type": "Sandbox"}}',
        ))
        provider = AfricasTalkingPhoneProvider(http_client=mock_client)
        return await provider.validate_credentials(username="sandbox", api_key="test-key")

    result = asyncio.run(run())

    assert result.valid is True
    assert result.username == "sandbox"
    assert result.account_type == "Sandbox"
    assert result.balance == "KES 100.00"
    assert result.error is None


def test_validate_credentials_returns_valid_false_on_401() -> None:
    async def run():
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_mock_http_response(status_code=401))
        provider = AfricasTalkingPhoneProvider(http_client=mock_client)
        return await provider.validate_credentials(username="sandbox", api_key="bad-key")

    result = asyncio.run(run())

    assert result.valid is False
    assert "401" in result.error


def test_validate_credentials_returns_valid_false_on_403() -> None:
    async def run():
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_mock_http_response(status_code=403))
        provider = AfricasTalkingPhoneProvider(http_client=mock_client)
        return await provider.validate_credentials(username="sandbox", api_key="forbidden-key")

    result = asyncio.run(run())

    assert result.valid is False
    assert "403" in result.error


def test_validate_credentials_returns_valid_false_on_timeout() -> None:
    import httpx as _httpx

    async def run():
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=_httpx.TimeoutException("timed out"))
        provider = AfricasTalkingPhoneProvider(http_client=mock_client)
        return await provider.validate_credentials(username="sandbox", api_key="test-key")

    result = asyncio.run(run())

    assert result.valid is False
    assert "timed out" in result.error.lower() or "timeout" in result.error.lower()


def test_validate_credentials_rejects_empty_username() -> None:
    async def run() -> None:
        provider = AfricasTalkingPhoneProvider()
        result = await provider.validate_credentials(username="", api_key="test-key")
        return result

    result = asyncio.run(run())

    assert result.valid is False
    assert "username" in result.error.lower()


def test_validate_credentials_rejects_empty_api_key() -> None:
    async def run() -> None:
        provider = AfricasTalkingPhoneProvider()
        result = await provider.validate_credentials(username="sandbox", api_key="   ")
        return result

    result = asyncio.run(run())

    assert result.valid is False
    assert "api_key" in result.error.lower()


# ── AfricasTalkingPhoneProvider.check_callback_reachability ──────────────────

def test_check_callback_reachability_returns_reachable_on_200_head() -> None:
    async def run():
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.head = AsyncMock(return_value=_mock_http_response(status_code=200))
        provider = AfricasTalkingPhoneProvider(http_client=mock_client)
        return await provider.check_callback_reachability("https://ops.example.test/callback")

    result = asyncio.run(run())

    assert result.reachable is True
    assert result.status == "reachable"
    assert result.http_status_code == 200
    assert result.error is None


def test_check_callback_reachability_falls_back_to_get_on_405() -> None:
    """Server disallows HEAD — provider retries with GET and succeeds."""
    async def run():
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.head = AsyncMock(return_value=_mock_http_response(status_code=405))
        mock_client.get = AsyncMock(return_value=_mock_http_response(status_code=200))
        provider = AfricasTalkingPhoneProvider(http_client=mock_client)
        return await provider.check_callback_reachability("https://ops.example.test/callback")

    result = asyncio.run(run())

    assert result.reachable is True
    assert result.http_status_code == 200


def test_check_callback_reachability_returns_unreachable_on_5xx() -> None:
    async def run():
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.head = AsyncMock(return_value=_mock_http_response(status_code=500))
        provider = AfricasTalkingPhoneProvider(http_client=mock_client)
        return await provider.check_callback_reachability("https://broken.example.test/cb")

    result = asyncio.run(run())

    assert result.reachable is False
    assert result.status == "unreachable"
    assert result.http_status_code == 500


def test_check_callback_reachability_returns_timeout_on_timeout() -> None:
    import httpx as _httpx

    async def run():
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.head = AsyncMock(side_effect=_httpx.TimeoutException("timed out"))
        provider = AfricasTalkingPhoneProvider(http_client=mock_client)
        return await provider.check_callback_reachability("https://slow.example.test/cb")

    result = asyncio.run(run())

    assert result.reachable is False
    assert result.status == "timeout"


def test_check_callback_reachability_returns_error_for_empty_url() -> None:
    async def run() -> None:
        provider = AfricasTalkingPhoneProvider()
        result = await provider.check_callback_reachability("")
        return result

    result = asyncio.run(run())

    assert result.reachable is False
    assert result.status == "error"
    assert result.error is not None


def test_check_callback_reachability_treats_3xx_as_reachable() -> None:
    """3xx redirects mean the server is responding — treat as reachable."""
    async def run():
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.head = AsyncMock(return_value=_mock_http_response(status_code=301))
        provider = AfricasTalkingPhoneProvider(http_client=mock_client)
        return await provider.check_callback_reachability("https://redirecting.example.test/cb")

    result = asyncio.run(run())

    assert result.reachable is True
    assert result.status == "reachable"


# ── API endpoints: validate-credentials + check-callback-reachability ─────────

def test_africastalking_validate_credentials_endpoint_returns_result(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)
        app = _build_authenticated_africas_talking_app(
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

            # This will call the real AT API; we just verify the shape of the
            # response — it will be valid=False because credentials are fake.
            response = await client.post(
                "/phone-providers/africastalking/validate-credentials",
                json={"username": "fake-user", "api_key": "fake-key"},
            )
            assert response.status_code == 200
            payload = response.json()
            assert "valid" in payload
            assert "username" in payload
            assert payload["username"] == "fake-user"

    asyncio.run(run())


def test_africastalking_check_callback_reachability_endpoint_returns_result(
    postgres_database_url_factory,
) -> None:
    async def run() -> None:
        agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
        auth_database_url = postgres_database_url_factory()
        _seed_authenticated_api_store(auth_database_url)
        app = _build_authenticated_africas_talking_app(
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

            response = await client.post(
                "/phone-providers/africastalking/check-callback-reachability",
                json={"url": "https://httpbin.org/status/200"},
            )
            assert response.status_code == 200
            payload = response.json()
            assert "reachable" in payload
            assert "url" in payload
            assert "status" in payload

    asyncio.run(run())
