from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import re
from typing import Literal

PhoneRouteChannel = Literal["phone", "sms", "whatsapp"]

_SUPPORTED_ROUTE_CHANNELS = {"phone", "sms", "whatsapp"}
_DEFAULT_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "phone": ("voice_inbound",),
    "sms": ("sms_inbound",),
    "whatsapp": ("whatsapp_inbound",),
}
_PHONE_NUMBER_METADATA_KEYS = (
    "to_number",
    "called_number",
    "dialed_number",
    "destination_number",
    "phone_number",
)
_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")
_NON_DIGIT_RE = re.compile(r"[^\d]")
_COUNTRY_PREFIXES = {
    "234": "NG",
    "254": "KE",
    "27": "ZA",
    "233": "GH",
    "251": "ET",
    "255": "TZ",
    "256": "UG",
    "221": "SN",
    "250": "RW",
    "265": "MW",
    "225": "CI",
    "237": "CM",
    "20": "EG",
    "212": "MA",
    "216": "TN",
    "971": "AE",
    "966": "SA",
    "974": "QA",
    "972": "IL",
    "973": "BH",
    "965": "KW",
    "968": "OM",
    "962": "JO",
}


@dataclass(slots=True, frozen=True)
class PhoneNumberRouteConfig:
    route_key: str
    phone_number: str
    agent_id: str
    channel: PhoneRouteChannel = "phone"
    organization_id: str | None = None
    provider: str | None = None
    provider_resource_id: str | None = None
    display_name: str | None = None
    country_code: str | None = None
    enabled: bool = True
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, object] = field(default_factory=dict)


def normalize_e164_number(value: str) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        raise ValueError("phone number must be a non-empty string")
    if candidate.startswith("00"):
        candidate = f"+{candidate[2:]}"
    if not candidate.startswith("+"):
        raise ValueError("phone number must include a leading + and country code")
    normalized = f"+{_NON_DIGIT_RE.sub('', candidate[1:])}"
    if not _E164_RE.fullmatch(normalized):
        raise ValueError("phone number must be valid E.164 format")
    return normalized


def is_valid_e164_number(value: str) -> bool:
    try:
        normalize_e164_number(value)
    except ValueError:
        return False
    return True


def detect_e164_country_code(value: str) -> str | None:
    normalized = normalize_e164_number(value)
    digits = normalized[1:]
    for prefix, country_code in sorted(_COUNTRY_PREFIXES.items(), key=lambda item: -len(item[0])):
        if digits.startswith(prefix):
            return country_code
    return None


def extract_phone_number_from_metadata(metadata: Mapping[str, object] | None) -> str | None:
    if not isinstance(metadata, Mapping):
        return None
    for key in _PHONE_NUMBER_METADATA_KEYS:
        raw_value = metadata.get(key)
        if not isinstance(raw_value, str) or not raw_value.strip():
            continue
        try:
            return normalize_e164_number(raw_value)
        except ValueError:
            continue
    return None


def parse_phone_number_routes(
    raw_routes: Mapping[str, Mapping[str, object]] | None,
) -> dict[str, PhoneNumberRouteConfig]:
    if not raw_routes:
        return {}
    parsed: dict[str, PhoneNumberRouteConfig] = {}
    seen_route_keys: set[str] = set()
    for raw_key, raw_value in raw_routes.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise ValueError("phone number routes must use non-empty string keys")
        if not isinstance(raw_value, Mapping):
            raise ValueError("phone number route config must be an object")
        route_key = raw_key.strip()
        if route_key in seen_route_keys:
            raise ValueError(f"duplicate phone number route key {route_key!r}")
        seen_route_keys.add(route_key)

        configured_number = raw_value.get("phone_number")
        if configured_number is None and route_key.startswith("+"):
            configured_number = route_key
        if not isinstance(configured_number, str) or not configured_number.strip():
            raise ValueError(f"phone number route {route_key!r} requires phone_number")
        phone_number = normalize_e164_number(configured_number)

        agent_id = str(raw_value.get("agent_id") or "").strip()
        if not agent_id:
            raise ValueError(f"phone number route {route_key!r} requires agent_id")

        raw_channel = str(raw_value.get("channel") or "phone").strip().lower()
        if raw_channel not in _SUPPORTED_ROUTE_CHANNELS:
            raise ValueError(f"phone number route {route_key!r} has unsupported channel {raw_channel!r}")

        raw_capabilities = raw_value.get("capabilities")
        capabilities: tuple[str, ...]
        if raw_capabilities is None:
            capabilities = _DEFAULT_CAPABILITIES[raw_channel]
        elif isinstance(raw_capabilities, (list, tuple)):
            normalized_capabilities: list[str] = []
            for item in raw_capabilities:
                if not isinstance(item, str) or not item.strip():
                    raise ValueError(
                        f"phone number route {route_key!r} capabilities must contain non-empty strings"
                    )
                normalized_capabilities.append(item.strip())
            capabilities = tuple(dict.fromkeys(normalized_capabilities))
        else:
            raise ValueError(f"phone number route {route_key!r} capabilities must be an array")

        raw_metadata = raw_value.get("metadata")
        if raw_metadata is None:
            metadata: dict[str, object] = {}
        elif isinstance(raw_metadata, Mapping):
            metadata = {str(key): value for key, value in raw_metadata.items()}
        else:
            raise ValueError(f"phone number route {route_key!r} metadata must be an object")

        explicit_country_code = raw_value.get("country_code")
        if explicit_country_code is not None and (
            not isinstance(explicit_country_code, str)
            or not explicit_country_code.strip()
            or len(explicit_country_code.strip()) != 2
        ):
            raise ValueError(f"phone number route {route_key!r} country_code must be a two-letter string")

        parsed[route_key] = PhoneNumberRouteConfig(
            route_key=route_key,
            phone_number=phone_number,
            agent_id=agent_id,
            channel=raw_channel,  # type: ignore[arg-type]
            organization_id=_optional_string(raw_value.get("organization_id")),
            provider=_optional_string(raw_value.get("provider")),
            provider_resource_id=_optional_string(raw_value.get("provider_resource_id")),
            display_name=_optional_string(raw_value.get("display_name")),
            country_code=(
                explicit_country_code.strip().upper()
                if isinstance(explicit_country_code, str) and explicit_country_code.strip()
                else detect_e164_country_code(phone_number)
            ),
            enabled=bool(raw_value.get("enabled", True)),
            capabilities=capabilities,
            metadata=metadata,
        )
    return parsed


def resolve_phone_number_route(
    routes: Mapping[str, PhoneNumberRouteConfig],
    *,
    phone_number: str,
    channel: PhoneRouteChannel = "phone",
    provider: str | None = None,
) -> PhoneNumberRouteConfig | None:
    normalized_number = normalize_e164_number(phone_number)
    normalized_provider = _optional_string(provider)
    matches = [
        route
        for route in routes.values()
        if route.enabled
        and route.channel == channel
        and route.phone_number == normalized_number
        and (normalized_provider is None or route.provider in {None, normalized_provider})
    ]
    if not matches:
        return None
    if normalized_provider is not None:
        exact_provider_matches = [route for route in matches if route.provider == normalized_provider]
        if exact_provider_matches:
            matches = exact_provider_matches
    if len(matches) > 1:
        raise ValueError(
            f"ambiguous phone number route for {normalized_number} channel={channel}"
            + ("" if normalized_provider is None else f" provider={normalized_provider}")
        )
    return matches[0]


def _optional_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate or None
