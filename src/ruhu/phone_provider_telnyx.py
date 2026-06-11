from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from .phone_numbers import normalize_e164_number


def _optional_string(value: object | None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate or None


def _optional_bool(value: object | None) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _string_list(value: object | None) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            normalized.append(item.strip())
    return normalized


def _object(value: object | None) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


class TelnyxProviderError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class TelnyxProviderUnavailableError(TelnyxProviderError):
    pass


class TelnyxProviderNotFoundError(TelnyxProviderError):
    pass


@dataclass(slots=True, frozen=True)
class TelnyxPhoneNumberRecord:
    provider_resource_id: str
    phone_number: str
    country_code: str | None = None
    status: str | None = None
    phone_number_type: str | None = None
    connection_id: str | None = None
    connection_name: str | None = None
    customer_reference: str | None = None
    messaging_profile_id: str | None = None
    messaging_profile_name: str | None = None
    billing_group_id: str | None = None
    emergency_enabled: bool | None = None
    emergency_status: str | None = None
    call_forwarding_enabled: bool | None = None
    inbound_call_screening: str | None = None
    hd_voice_enabled: bool | None = None
    source_type: str | None = None
    purchased_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    tags: list[str] = field(default_factory=list)
    raw: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class TelnyxVoiceSettings:
    provider_resource_id: str
    connection_id: str | None = None
    customer_reference: str | None = None
    translated_number: str | None = None
    usage_payment_method: str | None = None
    inbound_call_screening: str | None = None
    tech_prefix_enabled: bool | None = None
    call_forwarding_enabled: bool | None = None
    forwards_to: str | None = None
    forwarding_type: str | None = None
    emergency_enabled: bool | None = None
    emergency_status: str | None = None
    media_features: dict[str, object] = field(default_factory=dict)
    raw: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class TelnyxPhoneNumberSnapshot:
    phone_number: TelnyxPhoneNumberRecord
    voice_settings: TelnyxVoiceSettings | None = None


@dataclass(slots=True, frozen=True)
class TelnyxAvailablePhoneNumber:
    phone_number: str
    country_code: str | None = None
    phone_number_type: str | None = None
    locality: str | None = None
    region: str | None = None
    features: list[str] = field(default_factory=list)
    monthly_cost: str | None = None
    upfront_cost: str | None = None
    currency: str | None = None
    quickship: bool | None = None
    reservable: bool | None = None
    raw: dict[str, object] = field(default_factory=dict)


class TelnyxPhoneProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.telnyx.com/v2",
        timeout_seconds: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client

    async def lookup_phone_number(
        self,
        *,
        provider_resource_id: str | None = None,
        phone_number: str | None = None,
    ) -> TelnyxPhoneNumberSnapshot:
        if provider_resource_id is None and phone_number is None:
            raise ValueError("provider_resource_id or phone_number is required")
        normalized_phone_number = None if phone_number is None else normalize_e164_number(phone_number)
        if provider_resource_id is not None:
            record = await self.get_phone_number(provider_resource_id)
            if normalized_phone_number is not None and record.phone_number != normalized_phone_number:
                raise ValueError("provided phone_number does not match Telnyx resource")
        else:
            assert normalized_phone_number is not None
            record = await self.find_phone_number(normalized_phone_number)
        voice_settings = await self.get_phone_number_voice_settings(record.provider_resource_id)
        return TelnyxPhoneNumberSnapshot(phone_number=record, voice_settings=voice_settings)

    async def get_phone_number(self, provider_resource_id: str) -> TelnyxPhoneNumberRecord:
        resource_id = _optional_string(provider_resource_id)
        if resource_id is None:
            raise ValueError("provider_resource_id is required")
        payload = await self._request_json("GET", f"/phone_numbers/{resource_id}")
        return _parse_phone_number_record(payload.get("data"))

    async def find_phone_number(self, phone_number: str) -> TelnyxPhoneNumberRecord:
        normalized_phone_number = normalize_e164_number(phone_number)
        payload = await self._request_json(
            "GET",
            "/phone_numbers",
            params={
                "filter[phone_number]": normalized_phone_number,
                "page[size]": 1,
            },
        )
        records = payload.get("data")
        if not isinstance(records, list) or not records:
            raise TelnyxProviderNotFoundError(f"Telnyx phone number not found: {normalized_phone_number}", status_code=404)
        return _parse_phone_number_record(records[0])

    async def get_phone_number_voice_settings(self, provider_resource_id: str) -> TelnyxVoiceSettings | None:
        resource_id = _optional_string(provider_resource_id)
        if resource_id is None:
            raise ValueError("provider_resource_id is required")
        try:
            payload = await self._request_json("GET", f"/phone_numbers/{resource_id}/voice")
        except TelnyxProviderNotFoundError:
            return None
        return _parse_voice_settings(payload.get("data"))

    async def list_available_phone_numbers(
        self,
        *,
        country_code: str,
        phone_number_type: str = "local",
        national_destination_code: str | None = None,
        locality: str | None = None,
        limit: int = 20,
    ) -> list[TelnyxAvailablePhoneNumber]:
        normalized_country = _optional_string(country_code)
        if normalized_country is None:
            raise ValueError("country_code is required")
        params: dict[str, object] = {
            "filter[country_code]": normalized_country,
            "filter[phone_number_type]": phone_number_type.strip() if phone_number_type.strip() else "local",
            "filter[features]": "voice",
            "filter[limit]": max(1, min(limit, 100)),
        }
        if national_destination_code and national_destination_code.strip():
            params["filter[national_destination_code]"] = national_destination_code.strip()
        if locality and locality.strip():
            params["filter[locality]"] = locality.strip()
        payload = await self._request_json("GET", "/available_phone_numbers", params=params)
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        return [_parse_available_phone_number(item) for item in data if isinstance(item, dict)]

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if not self._api_key:
            raise TelnyxProviderUnavailableError("Telnyx API key is not configured")
        owns_client = self._http_client is None
        http_client = self._http_client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout_seconds,
            follow_redirects=False,
        )
        try:
            response = await http_client.request(
                method,
                path,
                params=params,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Accept": "application/json",
                },
            )
            if response.status_code == 404:
                raise TelnyxProviderNotFoundError("Telnyx resource was not found", status_code=404)
            if response.status_code >= 400:
                detail = response.text.strip() or f"status={response.status_code}"
                raise TelnyxProviderError(
                    f"Telnyx request failed: {detail[:200]}",
                    status_code=response.status_code,
                )
            payload = response.json()
            if not isinstance(payload, dict):
                raise TelnyxProviderError("Telnyx response must be a JSON object")
            return {str(key): value for key, value in payload.items()}
        except httpx.HTTPError as exc:
            raise TelnyxProviderError(f"Telnyx request failed: {exc}") from exc
        finally:
            if owns_client:
                await http_client.aclose()


def _parse_phone_number_record(value: object | None) -> TelnyxPhoneNumberRecord:
    payload = _object(value)
    resource_id = _optional_string(payload.get("id"))
    phone_number = _optional_string(payload.get("phone_number"))
    if resource_id is None or phone_number is None:
        raise TelnyxProviderError("Telnyx phone number response is missing id or phone_number")
    return TelnyxPhoneNumberRecord(
        provider_resource_id=resource_id,
        phone_number=normalize_e164_number(phone_number),
        country_code=_optional_string(payload.get("country_iso_alpha2")),
        status=_optional_string(payload.get("status")),
        phone_number_type=_optional_string(payload.get("phone_number_type")),
        connection_id=_optional_string(payload.get("connection_id")),
        connection_name=_optional_string(payload.get("connection_name")),
        customer_reference=_optional_string(payload.get("customer_reference")),
        messaging_profile_id=_optional_string(payload.get("messaging_profile_id")),
        messaging_profile_name=_optional_string(payload.get("messaging_profile_name")),
        billing_group_id=_optional_string(payload.get("billing_group_id")),
        emergency_enabled=_optional_bool(payload.get("emergency_enabled")),
        emergency_status=_optional_string(payload.get("emergency_status")),
        call_forwarding_enabled=_optional_bool(payload.get("call_forwarding_enabled")),
        inbound_call_screening=_optional_string(payload.get("inbound_call_screening")),
        hd_voice_enabled=_optional_bool(payload.get("hd_voice_enabled")),
        source_type=_optional_string(payload.get("source_type")),
        purchased_at=_optional_string(payload.get("purchased_at")),
        created_at=_optional_string(payload.get("created_at")),
        updated_at=_optional_string(payload.get("updated_at")),
        tags=_string_list(payload.get("tags")),
        raw=payload,
    )


def _parse_voice_settings(value: object | None) -> TelnyxVoiceSettings:
    payload = _object(value)
    resource_id = _optional_string(payload.get("id"))
    if resource_id is None:
        raise TelnyxProviderError("Telnyx voice settings response is missing id")
    call_forwarding = _object(payload.get("call_forwarding"))
    emergency = _object(payload.get("emergency"))
    return TelnyxVoiceSettings(
        provider_resource_id=resource_id,
        connection_id=_optional_string(payload.get("connection_id")),
        customer_reference=_optional_string(payload.get("customer_reference")),
        translated_number=_optional_string(payload.get("translated_number")),
        usage_payment_method=_optional_string(payload.get("usage_payment_method")),
        inbound_call_screening=_optional_string(payload.get("inbound_call_screening")),
        tech_prefix_enabled=_optional_bool(payload.get("tech_prefix_enabled")),
        call_forwarding_enabled=_optional_bool(call_forwarding.get("call_forwarding_enabled")),
        forwards_to=_optional_string(call_forwarding.get("forwards_to")),
        forwarding_type=_optional_string(call_forwarding.get("forwarding_type")),
        emergency_enabled=_optional_bool(emergency.get("emergency_enabled")),
        emergency_status=_optional_string(emergency.get("emergency_status")),
        media_features=_object(payload.get("media_features")),
        raw=payload,
    )


def _parse_available_phone_number(value: dict[str, object]) -> TelnyxAvailablePhoneNumber:
    cost_information = _object(value.get("cost_information"))
    region_information = value.get("region_information")
    locality = None
    region = None
    country_code = None
    if isinstance(region_information, list):
        for item in region_information:
            if not isinstance(item, dict):
                continue
            region_type = _optional_string(item.get("region_type"))
            region_name = _optional_string(item.get("region_name"))
            if region_type == "country_code" and region_name is not None:
                country_code = region_name
            elif region_type in {"locality", "city"} and region_name is not None and locality is None:
                locality = region_name
            elif region_name is not None and region is None:
                region = region_name
    features: list[str] = []
    raw_features = value.get("features")
    if isinstance(raw_features, list):
        for item in raw_features:
            if not isinstance(item, dict):
                continue
            feature_name = _optional_string(item.get("name"))
            if feature_name is not None:
                features.append(feature_name)
    return TelnyxAvailablePhoneNumber(
        phone_number=normalize_e164_number(str(value.get("phone_number") or "")),
        country_code=country_code,
        phone_number_type=_optional_string(value.get("phone_number_type")),
        locality=locality,
        region=region,
        features=features,
        monthly_cost=_optional_string(cost_information.get("monthly_cost")),
        upfront_cost=_optional_string(cost_information.get("upfront_cost")),
        currency=_optional_string(cost_information.get("currency")),
        quickship=_optional_bool(value.get("quickship")),
        reservable=_optional_bool(value.get("reservable")),
        raw={str(key): item for key, item in value.items()},
    )
