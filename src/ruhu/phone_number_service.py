from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger(__name__)

from .phone_provider_africastalking import (
    AfricasTalkingBindingSnapshot,
    AfricasTalkingCallbackReachabilityResult,
    AfricasTalkingCredentialValidationResult,
    AfricasTalkingPhoneProvider,
    africas_talking_binding_projection,
    build_africas_talking_snapshot,
    derive_africas_talking_binding_state,
    parse_africas_talking_snapshot,
)
from .phone_number_registry import (
    PhoneBindingChannel,
    PhoneBindingHealthStatus,
    PhoneBindingVerificationStatus,
    PhoneNumber,
    PhoneNumberBinding,
    PhoneNumberDetail,
    PhoneNumberRegistryService,
)
from .phone_numbers import normalize_e164_number
from .phone_provider_telnyx import (
    TelnyxAvailablePhoneNumber,
    TelnyxPhoneNumberSnapshot,
    TelnyxPhoneProvider,
    TelnyxProviderUnavailableError,
)


_SERVICE_UNSET = object()
PHONE_NUMBER_SERVICE_UNSET = _SERVICE_UNSET


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True, frozen=True)
class TelnyxBindingSyncResult:
    number: PhoneNumber
    binding: PhoneNumberBinding
    detail: PhoneNumberDetail
    provider_snapshot: TelnyxPhoneNumberSnapshot
    created_number: bool = False
    created_binding: bool = False


@dataclass(slots=True, frozen=True)
class AfricasTalkingBindingSyncResult:
    number: PhoneNumber
    binding: PhoneNumberBinding
    detail: PhoneNumberDetail
    provider_snapshot: AfricasTalkingBindingSnapshot
    created_number: bool = False
    created_binding: bool = False


class PhoneNumberService:
    def __init__(
        self,
        *,
        registry: PhoneNumberRegistryService,
        telnyx_provider: TelnyxPhoneProvider | None = None,
        at_provider: AfricasTalkingPhoneProvider | None = None,
    ) -> None:
        self._registry = registry
        self._telnyx_provider = telnyx_provider
        self._at_provider = at_provider or AfricasTalkingPhoneProvider()

    async def import_telnyx_number(
        self,
        *,
        organization_id: str,
        phone_number_id: str | None = None,
        phone_number: str | None = None,
        provider_resource_id: str | None = None,
        display_name: str | None = None,
        metadata: dict[str, object] | None = None,
        channel: PhoneBindingChannel = "phone",
    ) -> TelnyxBindingSyncResult:
        snapshot = await self._require_telnyx_provider().lookup_phone_number(
            provider_resource_id=provider_resource_id,
            phone_number=phone_number,
        )
        resolved_phone_number = snapshot.phone_number.phone_number
        if phone_number is not None and normalize_e164_number(phone_number) != resolved_phone_number:
            raise ValueError("provided phone_number does not match the Telnyx number")
        target_number, created_number = self._resolve_import_target_number(
            organization_id=organization_id,
            phone_number_id=phone_number_id,
            resolved_phone_number=resolved_phone_number,
            display_name=display_name,
            metadata=metadata,
            snapshot=snapshot,
        )
        binding, created_binding = self._sync_telnyx_binding_for_number(
            number=target_number,
            snapshot=snapshot,
            channel=channel,
        )
        refreshed_number = self._registry.get_number(
            target_number.phone_number_id,
            organization_id=organization_id,
        )
        detail = self._registry.get_number_detail(
            target_number.phone_number_id,
            organization_id=organization_id,
        )
        return TelnyxBindingSyncResult(
            number=refreshed_number,
            binding=binding,
            detail=detail,
            provider_snapshot=snapshot,
            created_number=created_number,
            created_binding=created_binding,
        )

    async def sync_telnyx_binding(
        self,
        *,
        organization_id: str,
        phone_number_id: str,
        binding_id: str,
    ) -> TelnyxBindingSyncResult:
        number = self._registry.get_number(phone_number_id, organization_id=organization_id)
        binding = self._registry.get_binding(phone_number_id, binding_id, organization_id=organization_id)
        if binding.provider != "telnyx":
            raise ValueError("binding is not a Telnyx binding")
        if binding.provider_resource_id is None:
            raise ValueError("binding does not have a Telnyx provider_resource_id")
        snapshot = await self._require_telnyx_provider().lookup_phone_number(
            provider_resource_id=binding.provider_resource_id,
        )
        if snapshot.phone_number.phone_number != number.e164_number:
            mismatch_metadata = dict(binding.transport_metadata)
            mismatch_metadata["telnyx"] = {
                **_telnyx_binding_projection(snapshot),
                "last_synced_at": _utcnow_iso(),
                "mismatch": {
                    "expected_phone_number": number.e164_number,
                    "provider_phone_number": snapshot.phone_number.phone_number,
                },
            }
            self._registry.update_binding(
                phone_number_id,
                binding_id,
                organization_id=organization_id,
                verification_status="failed",
                health_status="misconfigured",
                transport_metadata=mismatch_metadata,
            )
            raise ValueError("Telnyx provider resource does not match the canonical phone number")
        synced_number = self._sync_number_from_telnyx(
            number=number,
            snapshot=snapshot,
            display_name=number.display_name,
            extra_metadata=None,
        )
        synced_binding, _ = self._sync_telnyx_binding_for_number(
            number=synced_number,
            snapshot=snapshot,
            channel=binding.channel,
            binding_id=binding.binding_id,
        )
        detail = self._registry.get_number_detail(phone_number_id, organization_id=organization_id)
        return TelnyxBindingSyncResult(
            number=self._registry.get_number(phone_number_id, organization_id=organization_id),
            binding=synced_binding,
            detail=detail,
            provider_snapshot=snapshot,
            created_number=False,
            created_binding=False,
        )

    async def list_available_telnyx_numbers(
        self,
        *,
        country_code: str,
        phone_number_type: str = "local",
        national_destination_code: str | None = None,
        locality: str | None = None,
        limit: int = 20,
    ) -> list[TelnyxAvailablePhoneNumber]:
        return await self._require_telnyx_provider().list_available_phone_numbers(
            country_code=country_code,
            phone_number_type=phone_number_type,
            national_destination_code=national_destination_code,
            locality=locality,
            limit=limit,
        )

    async def import_africas_talking_number(
        self,
        *,
        organization_id: str,
        phone_number: str,
        phone_number_id: str | None = None,
        provider_resource_id: str | None = None,
        display_name: str | None = None,
        metadata: dict[str, object] | None = None,
        channel: PhoneBindingChannel = "phone",
        account_username: str | None = None,
        voice_callback_url: str | None = None,
        events_callback_url: str | None = None,
        sip_trunk_target: str | None = None,
        sip_auth_required: bool = True,
        credentials_reference: str | None = None,
        ip_whitelist_confirmed: bool = False,
        sip_forwarding_confirmed: bool = False,
        configuration_confirmed: bool = False,
        last_verified_at: str | None = None,
        notes: str | None = None,
    ) -> AfricasTalkingBindingSyncResult:
        snapshot = build_africas_talking_snapshot(
            phone_number=phone_number,
            provider_resource_id=provider_resource_id,
            account_username=account_username,
            voice_callback_url=voice_callback_url,
            events_callback_url=events_callback_url,
            sip_trunk_target=sip_trunk_target,
            sip_auth_required=sip_auth_required,
            credentials_reference=credentials_reference,
            ip_whitelist_confirmed=ip_whitelist_confirmed,
            sip_forwarding_confirmed=sip_forwarding_confirmed,
            configuration_confirmed=configuration_confirmed,
            last_verified_at=last_verified_at,
            notes=notes,
        )
        target_number, created_number = self._resolve_africas_talking_import_target_number(
            organization_id=organization_id,
            phone_number_id=phone_number_id,
            display_name=display_name,
            metadata=metadata,
            snapshot=snapshot,
        )
        binding, created_binding = self._sync_africas_talking_binding_for_number(
            number=target_number,
            snapshot=snapshot,
            channel=channel,
        )
        refreshed_number = self._registry.get_number(
            target_number.phone_number_id,
            organization_id=organization_id,
        )
        detail = self._registry.get_number_detail(
            target_number.phone_number_id,
            organization_id=organization_id,
        )
        return AfricasTalkingBindingSyncResult(
            number=refreshed_number,
            binding=binding,
            detail=detail,
            provider_snapshot=snapshot,
            created_number=created_number,
            created_binding=created_binding,
        )

    async def sync_africas_talking_binding(
        self,
        *,
        organization_id: str,
        phone_number_id: str,
        binding_id: str,
        provider_resource_id: str | None | object = _SERVICE_UNSET,
        account_username: str | None | object = _SERVICE_UNSET,
        voice_callback_url: str | None | object = _SERVICE_UNSET,
        events_callback_url: str | None | object = _SERVICE_UNSET,
        sip_trunk_target: str | None | object = _SERVICE_UNSET,
        sip_auth_required: bool | object = _SERVICE_UNSET,
        credentials_reference: str | None | object = _SERVICE_UNSET,
        ip_whitelist_confirmed: bool | object = _SERVICE_UNSET,
        sip_forwarding_confirmed: bool | object = _SERVICE_UNSET,
        configuration_confirmed: bool | object = _SERVICE_UNSET,
        last_verified_at: str | None | object = _SERVICE_UNSET,
        notes: str | None | object = _SERVICE_UNSET,
    ) -> AfricasTalkingBindingSyncResult:
        number = self._registry.get_number(phone_number_id, organization_id=organization_id)
        binding = self._registry.get_binding(phone_number_id, binding_id, organization_id=organization_id)
        if binding.provider != "africastalking":
            raise ValueError("binding is not an Africa's Talking binding")
        current_snapshot = parse_africas_talking_snapshot(
            binding.transport_metadata.get("africastalking"),
            phone_number=number.e164_number,
            provider_resource_id=binding.provider_resource_id or number.e164_number,
        )
        snapshot = build_africas_talking_snapshot(
            phone_number=number.e164_number,
            provider_resource_id=_resolve_manual_sync_value(provider_resource_id, current_snapshot.provider_resource_id),
            account_username=_resolve_manual_sync_value(account_username, current_snapshot.account_username),
            voice_callback_url=_resolve_manual_sync_value(voice_callback_url, current_snapshot.voice_callback_url),
            events_callback_url=_resolve_manual_sync_value(events_callback_url, current_snapshot.events_callback_url),
            sip_trunk_target=_resolve_manual_sync_value(sip_trunk_target, current_snapshot.sip_trunk_target),
            sip_auth_required=_resolve_manual_sync_value(sip_auth_required, current_snapshot.sip_auth_required),
            credentials_reference=_resolve_manual_sync_value(
                credentials_reference,
                current_snapshot.credentials_reference,
            ),
            ip_whitelist_confirmed=_resolve_manual_sync_value(
                ip_whitelist_confirmed,
                current_snapshot.ip_whitelist_confirmed,
            ),
            sip_forwarding_confirmed=_resolve_manual_sync_value(
                sip_forwarding_confirmed,
                current_snapshot.sip_forwarding_confirmed,
            ),
            configuration_confirmed=_resolve_manual_sync_value(
                configuration_confirmed,
                current_snapshot.configuration_confirmed,
            ),
            last_verified_at=_resolve_manual_sync_value(last_verified_at, current_snapshot.last_verified_at),
            notes=_resolve_manual_sync_value(notes, current_snapshot.notes),
        )
        # Probe the callback URL if it's an HTTP(S) endpoint (skip SIP trunk
        # addresses like "trunk:livekit.example.test").
        callback_url = snapshot.voice_callback_url
        if (
            callback_url
            and callback_url.lower().startswith(("http://", "https://"))
            and self._at_provider is not None
        ):
            probe = await self._at_provider.check_callback_reachability(callback_url)
            if not probe.reachable:
                reason = probe.error or probe.status
                snapshot = dataclasses.replace(
                    snapshot,
                    notes=f"{snapshot.notes or ''}\n⚠ Callback URL check: {reason}".strip(),
                )
                log.warning(
                    "at_callback_unreachable",
                    phone_number_id=phone_number_id,
                    url=callback_url,
                    reason=reason,
                )
        synced_number = self._sync_number_from_africas_talking(
            number=number,
            snapshot=snapshot,
            display_name=number.display_name,
            extra_metadata=None,
        )
        synced_binding, _ = self._sync_africas_talking_binding_for_number(
            number=synced_number,
            snapshot=snapshot,
            channel=binding.channel,
            binding_id=binding.binding_id,
        )
        detail = self._registry.get_number_detail(phone_number_id, organization_id=organization_id)
        return AfricasTalkingBindingSyncResult(
            number=self._registry.get_number(phone_number_id, organization_id=organization_id),
            binding=synced_binding,
            detail=detail,
            provider_snapshot=snapshot,
            created_number=False,
            created_binding=False,
        )

    async def validate_africas_talking_credentials(
        self,
        *,
        username: str,
        api_key: str,
    ) -> AfricasTalkingCredentialValidationResult:
        """Verify that the supplied Africa's Talking (username, api_key) pair is valid.

        Calls the AT User Data API and returns a result indicating whether
        the credentials are accepted.  Never raises — check ``.valid``.
        """
        return await self._at_provider.validate_credentials(
            username=username,
            api_key=api_key,
        )

    async def check_africas_talking_callback_reachability(
        self,
        url: str,
    ) -> AfricasTalkingCallbackReachabilityResult:
        """Probe ``url`` to verify it is publicly reachable as a callback endpoint.

        Returns a result with ``status='reachable'`` if the URL responds with a
        non-5xx HTTP status.  Never raises — check ``.reachable``.
        """
        return await self._at_provider.check_callback_reachability(url)

    def _resolve_import_target_number(
        self,
        *,
        organization_id: str,
        phone_number_id: str | None,
        resolved_phone_number: str,
        display_name: str | None,
        metadata: dict[str, object] | None,
        snapshot: TelnyxPhoneNumberSnapshot,
    ) -> tuple[PhoneNumber, bool]:
        if phone_number_id is not None:
            existing = self._registry.get_number(phone_number_id, organization_id=organization_id)
            if existing.e164_number != resolved_phone_number:
                raise ValueError("phone_number_id does not match the Telnyx phone number")
            synced = self._sync_number_from_telnyx(
                number=existing,
                snapshot=snapshot,
                display_name=display_name,
                extra_metadata=metadata,
            )
            return synced, False
        existing = self._registry.find_number_by_e164(
            organization_id=organization_id,
            e164_number=resolved_phone_number,
        )
        if existing is not None:
            synced = self._sync_number_from_telnyx(
                number=existing,
                snapshot=snapshot,
                display_name=display_name,
                extra_metadata=metadata,
            )
            return synced, False
        created = self._registry.create_number(
            organization_id=organization_id,
            e164_number=resolved_phone_number,
            display_name=_resolve_display_name(display_name, snapshot=snapshot),
            ownership_mode="provider_managed",
            status=_map_telnyx_number_status(snapshot.phone_number.status),
            metadata=_merge_number_metadata(
                {},
                snapshot=snapshot,
                extra_metadata=metadata,
            ),
        )
        return created, True

    def _sync_number_from_telnyx(
        self,
        *,
        number: PhoneNumber,
        snapshot: TelnyxPhoneNumberSnapshot,
        display_name: str | None,
        extra_metadata: dict[str, object] | None,
    ) -> PhoneNumber:
        return self._registry.update_number(
            number.phone_number_id,
            organization_id=number.organization_id,
            display_name=_resolve_display_name(display_name, snapshot=snapshot, fallback=number.display_name),
            status=_map_telnyx_number_status(snapshot.phone_number.status),
            ownership_mode="provider_managed",
            metadata=_merge_number_metadata(number.metadata, snapshot=snapshot, extra_metadata=extra_metadata),
        )

    def _sync_telnyx_binding_for_number(
        self,
        *,
        number: PhoneNumber,
        snapshot: TelnyxPhoneNumberSnapshot,
        channel: PhoneBindingChannel,
        binding_id: str | None = None,
    ) -> tuple[PhoneNumberBinding, bool]:
        verification_status, health_status, capabilities = _derive_telnyx_binding_state(snapshot)
        current_binding = None
        if binding_id is not None:
            current_binding = self._registry.get_binding(
                number.phone_number_id,
                binding_id,
                organization_id=number.organization_id,
            )
        else:
            current_binding = _find_matching_binding(
                self._registry.list_bindings(number.phone_number_id, organization_id=number.organization_id),
                provider="telnyx",
                channel=channel,
                provider_resource_id=snapshot.phone_number.provider_resource_id,
            )
        transport_metadata = _merge_binding_transport_metadata(
            {} if current_binding is None else current_binding.transport_metadata,
            snapshot=snapshot,
        )
        if current_binding is None:
            binding = self._registry.create_binding(
                phone_number_id=number.phone_number_id,
                organization_id=number.organization_id,
                channel=channel,
                provider="telnyx",
                provider_resource_id=snapshot.phone_number.provider_resource_id,
                capabilities=capabilities,
                verification_status=verification_status,
                health_status=health_status,
                is_active=True,
                transport_metadata=transport_metadata,
            )
            return binding, True
        binding = self._registry.update_binding(
            number.phone_number_id,
            current_binding.binding_id,
            organization_id=number.organization_id,
            provider_resource_id=snapshot.phone_number.provider_resource_id,
            capabilities=capabilities,
            verification_status=verification_status,
            health_status=health_status,
            is_active=True,
            transport_metadata=transport_metadata,
        )
        return binding, False

    def _require_telnyx_provider(self) -> TelnyxPhoneProvider:
        if self._telnyx_provider is None:
            raise TelnyxProviderUnavailableError("Telnyx provider is not configured")
        return self._telnyx_provider

    def _resolve_africas_talking_import_target_number(
        self,
        *,
        organization_id: str,
        phone_number_id: str | None,
        display_name: str | None,
        metadata: dict[str, object] | None,
        snapshot: AfricasTalkingBindingSnapshot,
    ) -> tuple[PhoneNumber, bool]:
        if phone_number_id is not None:
            existing = self._registry.get_number(phone_number_id, organization_id=organization_id)
            if existing.e164_number != snapshot.phone_number:
                raise ValueError("phone_number_id does not match the Africa's Talking phone number")
            synced = self._sync_number_from_africas_talking(
                number=existing,
                snapshot=snapshot,
                display_name=display_name,
                extra_metadata=metadata,
            )
            return synced, False
        existing = self._registry.find_number_by_e164(
            organization_id=organization_id,
            e164_number=snapshot.phone_number,
        )
        if existing is not None:
            synced = self._sync_number_from_africas_talking(
                number=existing,
                snapshot=snapshot,
                display_name=display_name,
                extra_metadata=metadata,
            )
            return synced, False
        created = self._registry.create_number(
            organization_id=organization_id,
            e164_number=snapshot.phone_number,
            display_name=_resolve_manual_display_name(display_name),
            ownership_mode="provider_managed",
            status="active",
            metadata=_merge_africas_talking_number_metadata(
                {},
                snapshot=snapshot,
                extra_metadata=metadata,
            ),
        )
        return created, True

    def _sync_number_from_africas_talking(
        self,
        *,
        number: PhoneNumber,
        snapshot: AfricasTalkingBindingSnapshot,
        display_name: str | None,
        extra_metadata: dict[str, object] | None,
    ) -> PhoneNumber:
        return self._registry.update_number(
            number.phone_number_id,
            organization_id=number.organization_id,
            display_name=_resolve_manual_display_name(display_name, fallback=number.display_name),
            status="active",
            ownership_mode="provider_managed",
            metadata=_merge_africas_talking_number_metadata(
                number.metadata,
                snapshot=snapshot,
                extra_metadata=extra_metadata,
            ),
        )

    def _sync_africas_talking_binding_for_number(
        self,
        *,
        number: PhoneNumber,
        snapshot: AfricasTalkingBindingSnapshot,
        channel: PhoneBindingChannel,
        binding_id: str | None = None,
    ) -> tuple[PhoneNumberBinding, bool]:
        verification_status, health_status, capabilities = derive_africas_talking_binding_state(snapshot)
        if binding_id is not None:
            current_binding = self._registry.get_binding(
                number.phone_number_id,
                binding_id,
                organization_id=number.organization_id,
            )
        else:
            current_binding = _find_matching_binding(
                self._registry.list_bindings(number.phone_number_id, organization_id=number.organization_id),
                provider="africastalking",
                channel=channel,
                provider_resource_id=snapshot.provider_resource_id,
            )
        transport_metadata = _merge_africas_talking_binding_transport_metadata(
            {} if current_binding is None else current_binding.transport_metadata,
            snapshot=snapshot,
        )
        if current_binding is None:
            binding = self._registry.create_binding(
                phone_number_id=number.phone_number_id,
                organization_id=number.organization_id,
                channel=channel,
                provider="africastalking",
                provider_resource_id=snapshot.provider_resource_id,
                capabilities=capabilities,
                verification_status=verification_status,
                health_status=health_status,
                is_active=True,
                transport_metadata=transport_metadata,
            )
            return binding, True
        binding = self._registry.update_binding(
            number.phone_number_id,
            current_binding.binding_id,
            organization_id=number.organization_id,
            provider_resource_id=snapshot.provider_resource_id,
            capabilities=capabilities,
            verification_status=verification_status,
            health_status=health_status,
            is_active=True,
            transport_metadata=transport_metadata,
        )
        return binding, False


def _find_matching_binding(
    bindings: list[PhoneNumberBinding],
    *,
    provider: str,
    channel: PhoneBindingChannel,
    provider_resource_id: str | None,
) -> PhoneNumberBinding | None:
    exact_matches = [
        binding
        for binding in bindings
        if binding.provider == provider
        and binding.channel == channel
        and provider_resource_id is not None
        and binding.provider_resource_id == provider_resource_id
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    provider_matches = [
        binding
        for binding in bindings
        if binding.provider == provider and binding.channel == channel
    ]
    if len(provider_matches) <= 1:
        return provider_matches[0] if provider_matches else None
    raise ValueError(f"ambiguous {provider} binding for phone number")


def _map_telnyx_number_status(status: str | None) -> str:
    normalized = (status or "").strip().lower()
    if normalized == "active":
        return "active"
    if normalized in {"pending", "reserved", "porting", "port_in_pending"}:
        return "draft"
    if normalized in {"suspended", "blocked"}:
        return "suspended"
    if normalized in {"released", "deleted"}:
        return "archived"
    return "draft"


def _resolve_display_name(
    display_name: str | None,
    *,
    snapshot: TelnyxPhoneNumberSnapshot,
    fallback: str | None = None,
) -> str | None:
    for candidate in (
        display_name,
        snapshot.phone_number.connection_name,
        fallback,
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _resolve_manual_display_name(
    display_name: str | None,
    *,
    fallback: str | None = None,
) -> str | None:
    for candidate in (display_name, fallback):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _merge_number_metadata(
    current: dict[str, object],
    *,
    snapshot: TelnyxPhoneNumberSnapshot,
    extra_metadata: dict[str, object] | None,
) -> dict[str, object]:
    merged = dict(current)
    if extra_metadata:
        merged.update({str(key): value for key, value in extra_metadata.items()})
    merged["telnyx"] = {
        "provider_resource_id": snapshot.phone_number.provider_resource_id,
        "status": snapshot.phone_number.status,
        "phone_number_type": snapshot.phone_number.phone_number_type,
        "country_code": snapshot.phone_number.country_code,
        "connection_id": snapshot.phone_number.connection_id,
        "connection_name": snapshot.phone_number.connection_name,
        "source_type": snapshot.phone_number.source_type,
        "last_synced_at": _utcnow_iso(),
    }
    return merged


def _merge_binding_transport_metadata(
    current: dict[str, object],
    *,
    snapshot: TelnyxPhoneNumberSnapshot,
) -> dict[str, object]:
    merged = dict(current)
    merged["telnyx"] = {
        **_telnyx_binding_projection(snapshot),
        "last_synced_at": _utcnow_iso(),
    }
    return merged


def _merge_africas_talking_number_metadata(
    current: dict[str, object],
    *,
    snapshot: AfricasTalkingBindingSnapshot,
    extra_metadata: dict[str, object] | None,
) -> dict[str, object]:
    merged = dict(current)
    if extra_metadata:
        merged.update({str(key): value for key, value in extra_metadata.items()})
    merged["africastalking"] = {
        "provider_resource_id": snapshot.provider_resource_id,
        "account_username": snapshot.account_username,
        "voice_callback_url": snapshot.voice_callback_url,
        "events_callback_url": snapshot.events_callback_url,
        "sip_trunk_target": snapshot.sip_trunk_target,
        "last_verified_at": snapshot.last_verified_at,
        "last_synced_at": _utcnow_iso(),
    }
    return merged


def _merge_africas_talking_binding_transport_metadata(
    current: dict[str, object],
    *,
    snapshot: AfricasTalkingBindingSnapshot,
) -> dict[str, object]:
    merged = dict(current)
    merged["africastalking"] = {
        **africas_talking_binding_projection(snapshot),
        "last_synced_at": _utcnow_iso(),
    }
    return merged


def _telnyx_binding_projection(snapshot: TelnyxPhoneNumberSnapshot) -> dict[str, object]:
    phone_number = snapshot.phone_number
    voice_settings = snapshot.voice_settings
    return {
        "provider_resource_id": phone_number.provider_resource_id,
        "phone_number": phone_number.phone_number,
        "status": phone_number.status,
        "phone_number_type": phone_number.phone_number_type,
        "connection_id": phone_number.connection_id,
        "connection_name": phone_number.connection_name,
        "customer_reference": phone_number.customer_reference,
        "messaging_profile_id": phone_number.messaging_profile_id,
        "messaging_profile_name": phone_number.messaging_profile_name,
        "billing_group_id": phone_number.billing_group_id,
        "hd_voice_enabled": phone_number.hd_voice_enabled,
        "emergency_enabled": phone_number.emergency_enabled,
        "emergency_status": phone_number.emergency_status,
        "tags": list(phone_number.tags),
        "voice_settings": None if voice_settings is None else {
            "connection_id": voice_settings.connection_id,
            "customer_reference": voice_settings.customer_reference,
            "translated_number": voice_settings.translated_number,
            "usage_payment_method": voice_settings.usage_payment_method,
            "inbound_call_screening": voice_settings.inbound_call_screening,
            "tech_prefix_enabled": voice_settings.tech_prefix_enabled,
            "call_forwarding_enabled": voice_settings.call_forwarding_enabled,
            "forwards_to": voice_settings.forwards_to,
            "forwarding_type": voice_settings.forwarding_type,
            "emergency_enabled": voice_settings.emergency_enabled,
            "emergency_status": voice_settings.emergency_status,
            "media_features": dict(voice_settings.media_features),
        },
    }


def _derive_telnyx_binding_state(
    snapshot: TelnyxPhoneNumberSnapshot,
) -> tuple[PhoneBindingVerificationStatus, PhoneBindingHealthStatus, list[str]]:
    normalized_status = (snapshot.phone_number.status or "").strip().lower()
    effective_connection_id = snapshot.phone_number.connection_id or (
        None if snapshot.voice_settings is None else snapshot.voice_settings.connection_id
    )
    if normalized_status == "active" and effective_connection_id:
        return "verified", "healthy", ["voice_inbound"]
    if normalized_status == "active":
        return "manual_required", "misconfigured", ["voice_inbound"]
    if normalized_status in {"pending", "reserved", "porting", "port_in_pending"}:
        return "pending", "degraded", ["voice_inbound"]
    if normalized_status in {"suspended", "blocked", "released", "deleted"}:
        return "failed", "misconfigured", ["voice_inbound"]
    return "manual_required", "unknown", ["voice_inbound"]


def _resolve_manual_sync_value(value: object, current: object) -> object:
    if value is _SERVICE_UNSET:
        return current
    return value
