from __future__ import annotations

from ruhu.db import build_session_factory
from ruhu.phone_number_registry import PhoneNumberRegistryService
from ruhu.phone_number_worker import main


def test_phone_number_worker_reconcile_once_updates_active_bindings(
    postgres_database_url_factory,
    capsys,
) -> None:
    database_url = postgres_database_url_factory()
    session_factory = build_session_factory(database_url)
    registry = PhoneNumberRegistryService(session_factory)

    number = registry.create_number(
        organization_id="org-1",
        e164_number="+2348012345678",
        display_name="Nigeria support line",
    )
    binding = registry.create_binding(
        phone_number_id=number.phone_number_id,
        organization_id="org-1",
        channel="phone",
        provider="africastalking",
        provider_resource_id="+2348012345678",
        verification_status="verified",
        health_status="healthy",
        transport_metadata={
            "africastalking": {
                "provider_resource_id": "+2348012345678",
                "phone_number": "+2348012345678",
            }
        },
    )

    exit_code = main(
        [
            "reconcile-once",
            "--database-url",
            database_url,
            "--organization-id",
            "org-1",
            "--json",
        ]
    )
    output = capsys.readouterr().out
    refreshed = registry.get_binding(number.phone_number_id, binding.binding_id, organization_id="org-1")

    assert exit_code == 0
    assert '"processed_count": 1' in output
    assert refreshed.verification_status == "manual_required"
    assert refreshed.health_status == "misconfigured"
    assert refreshed.transport_metadata["reconciliation"]["status"] == "ok"
