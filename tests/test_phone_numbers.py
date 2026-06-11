from __future__ import annotations

import pytest

from ruhu.phone_numbers import (
    detect_e164_country_code,
    extract_phone_number_from_metadata,
    is_valid_e164_number,
    normalize_e164_number,
    parse_phone_number_routes,
    resolve_phone_number_route,
)


def test_normalize_e164_number_canonicalizes_formatted_input() -> None:
    assert normalize_e164_number("+234 801-234-5678") == "+2348012345678"
    assert normalize_e164_number("0044 20 7946 0018") == "+442079460018"
    assert is_valid_e164_number("+1 (555) 123-4567") is True


def test_normalize_e164_number_rejects_local_numbers() -> None:
    with pytest.raises(ValueError):
        normalize_e164_number("08012345678")


def test_detect_e164_country_code_uses_known_prefixes() -> None:
    assert detect_e164_country_code("+2348012345678") == "NG"
    assert detect_e164_country_code("+971501234567") == "AE"
    assert detect_e164_country_code("+12025550123") is None


def test_extract_phone_number_from_metadata_uses_called_number_fields() -> None:
    metadata = {
        "from_number": "+14155550123",
        "to_number": "+234 801 234 5678",
    }

    assert extract_phone_number_from_metadata(metadata) == "+2348012345678"


def test_parse_phone_number_routes_builds_canonical_route_configs() -> None:
    routes = parse_phone_number_routes(
        {
            "sales_main": {
                "phone_number": "+234 801 234 5678",
                "agent_id": "sales_agent",
                "organization_id": "org-demo",
                "provider": "telnyx",
                "display_name": "Nigeria Sales",
                "metadata": {"region": "lagos"},
            },
            "wa_main": {
                "phone_number": "+2348012345678",
                "agent_id": "sales_agent",
                "channel": "whatsapp",
                "provider": "meta_whatsapp",
            },
        }
    )

    assert routes["sales_main"].phone_number == "+2348012345678"
    assert routes["sales_main"].country_code == "NG"
    assert routes["sales_main"].capabilities == ("voice_inbound",)
    assert routes["sales_main"].metadata == {"region": "lagos"}
    assert routes["wa_main"].channel == "whatsapp"
    assert routes["wa_main"].capabilities == ("whatsapp_inbound",)


def test_resolve_phone_number_route_detects_ambiguous_provider_matches() -> None:
    routes = parse_phone_number_routes(
        {
            "telnyx_sales": {
                "phone_number": "+15551234567",
                "agent_id": "sales_agent",
                "provider": "telnyx",
            },
            "twilio_sales": {
                "phone_number": "+15551234567",
                "agent_id": "sales_agent",
                "provider": "twilio",
            },
        }
    )

    with pytest.raises(ValueError):
        resolve_phone_number_route(routes, phone_number="+1 555 123 4567", channel="phone")

    assert (
        resolve_phone_number_route(
            routes,
            phone_number="+1 555 123 4567",
            channel="phone",
            provider="telnyx",
        ).route_key
        == "telnyx_sales"
    )
