"""Phone number registry + provider endpoints — extracted from api.py (RP-3.1 step 6).

Covers the /phone-numbers CRUD/bindings/routes surface, the
/phone-numbers/reconcile + /phone-numbers/audit operations endpoints, and
the Telnyx / Africa's Talking provider import, sync, and validation
endpoints. Registration order inside this router preserves the original
inline order (hazard H2: /phone-numbers/audit and /phone-numbers/reconcile
register before /phone-numbers/{phone_number_id}).

The phone request/response DTOs still live in ``ruhu.api`` (they migrate
with the rest of the inline DTO block in a later step), so this module is
imported by ``create_app()`` AT THE MOUNT SITE rather than at api.py's
module top. The ``*_state`` kwargs are zero-arg callables supplied by
create_app() that read ``app.state`` test seams (``telnyx_phone_provider``,
``telnyx_http_client``, ``at_phone_provider``) — that read stays
composition-side, like the journey tracker/runtime providers. No ``tags=``
/ ``prefix=`` and unchanged handler names (hazard H1).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

# DTOs at module top (hazard H7: PEP 563 return annotations resolve against
# this module's globals).
from ..api import (
    AfricasTalkingBindingStateResponse,
    AfricasTalkingBindingSyncRequest,
    AfricasTalkingBindingSyncResponse,
    AfricasTalkingCheckCallbackReachabilityRequest,
    AfricasTalkingCheckCallbackReachabilityResponse,
    AfricasTalkingPhoneNumberImportRequest,
    AfricasTalkingValidateCredentialsRequest,
    AfricasTalkingValidateCredentialsResponse,
    PhoneBindingReconciliationRequest,
    PhoneBindingReconciliationResponse,
    PhoneBindingReconciliationResultResponse,
    PhoneNumberBindingCreateRequest,
    PhoneNumberBindingUpdateRequest,
    PhoneNumberCreateRequest,
    PhoneNumberRouteCreateRequest,
    PhoneNumberRouteUpdateRequest,
    PhoneNumberUpdateRequest,
    TelnyxAvailableNumberResponse,
    TelnyxBindingSyncResponse,
    TelnyxPhoneNumberImportRequest,
    TelnyxPhoneNumberResponse,
    TelnyxVoiceSettingsResponse,
)
from ..api_auth import RequestAuthContext
from ..phone_number_audit import PhoneNumberAuditEvent, PhoneNumberAuditService
from ..phone_number_operations import (
    PhoneBindingReconciliationResult,
    PhoneBindingReconciliationSummary,
    PhoneNumberOperationsService,
)
from ..phone_number_registry import (
    PhoneNumber,
    PhoneNumberBinding,
    PhoneNumberDetail,
    PhoneNumberRegistryConflictError,
    PhoneNumberRegistryNotFoundError,
    PhoneNumberRegistryService,
    PhoneNumberRoute,
    _UNSET as PHONE_NUMBER_REGISTRY_UNSET,
)
from ..phone_number_service import (
    AfricasTalkingBindingSyncResult,
    PHONE_NUMBER_SERVICE_UNSET,
    PhoneNumberService,
    TelnyxBindingSyncResult,
)
from ..phone_provider_africastalking import (
    AfricasTalkingBindingSnapshot,
    AfricasTalkingPhoneProvider,
)
from ..phone_provider_telnyx import (
    TelnyxAvailablePhoneNumber,
    TelnyxPhoneNumberRecord,
    TelnyxPhoneProvider,
    TelnyxProviderError,
    TelnyxProviderNotFoundError,
    TelnyxProviderUnavailableError,
    TelnyxVoiceSettings,
)
from ..policy import require_organization_role
from ..session_http import build_session_audit_context

if TYPE_CHECKING:
    from ..registry import SQLAlchemyAgentRegistry
    from ..runtime_config import RuntimeSettings


def _build_telnyx_phone_provider_record_response(
    record: TelnyxPhoneNumberRecord,
) -> TelnyxPhoneNumberResponse:
    return TelnyxPhoneNumberResponse(
        provider_resource_id=record.provider_resource_id,
        phone_number=record.phone_number,
        country_code=record.country_code,
        status=record.status,
        phone_number_type=record.phone_number_type,
        connection_id=record.connection_id,
        connection_name=record.connection_name,
        customer_reference=record.customer_reference,
        messaging_profile_id=record.messaging_profile_id,
        messaging_profile_name=record.messaging_profile_name,
        billing_group_id=record.billing_group_id,
        emergency_enabled=record.emergency_enabled,
        emergency_status=record.emergency_status,
        call_forwarding_enabled=record.call_forwarding_enabled,
        inbound_call_screening=record.inbound_call_screening,
        hd_voice_enabled=record.hd_voice_enabled,
        source_type=record.source_type,
        purchased_at=record.purchased_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
        tags=list(record.tags),
    )


def _build_telnyx_voice_settings_response(
    settings: TelnyxVoiceSettings | None,
) -> TelnyxVoiceSettingsResponse | None:
    if settings is None:
        return None
    return TelnyxVoiceSettingsResponse(
        provider_resource_id=settings.provider_resource_id,
        connection_id=settings.connection_id,
        customer_reference=settings.customer_reference,
        translated_number=settings.translated_number,
        usage_payment_method=settings.usage_payment_method,
        inbound_call_screening=settings.inbound_call_screening,
        tech_prefix_enabled=settings.tech_prefix_enabled,
        call_forwarding_enabled=settings.call_forwarding_enabled,
        forwards_to=settings.forwards_to,
        forwarding_type=settings.forwarding_type,
        emergency_enabled=settings.emergency_enabled,
        emergency_status=settings.emergency_status,
        media_features=dict(settings.media_features),
    )


def _build_telnyx_binding_sync_response(
    result: TelnyxBindingSyncResult,
) -> TelnyxBindingSyncResponse:
    snapshot = result.provider_snapshot
    return TelnyxBindingSyncResponse(
        number=result.number,
        binding=result.binding,
        detail=result.detail,
        provider_number=_build_telnyx_phone_provider_record_response(snapshot.phone_number),
        voice_settings=_build_telnyx_voice_settings_response(snapshot.voice_settings),
        created_number=result.created_number,
        created_binding=result.created_binding,
    )


def _build_telnyx_available_number_response(
    number: TelnyxAvailablePhoneNumber,
) -> TelnyxAvailableNumberResponse:
    return TelnyxAvailableNumberResponse(
        phone_number=number.phone_number,
        country_code=number.country_code,
        phone_number_type=number.phone_number_type,
        locality=number.locality,
        region=number.region,
        features=list(number.features),
        monthly_cost=number.monthly_cost,
        upfront_cost=number.upfront_cost,
        currency=number.currency,
        quickship=number.quickship,
        reservable=number.reservable,
    )


def _build_africas_talking_binding_state_response(
    snapshot: AfricasTalkingBindingSnapshot,
) -> AfricasTalkingBindingStateResponse:
    return AfricasTalkingBindingStateResponse(
        provider_resource_id=snapshot.provider_resource_id,
        phone_number=snapshot.phone_number,
        account_username=snapshot.account_username,
        voice_callback_url=snapshot.voice_callback_url,
        events_callback_url=snapshot.events_callback_url,
        sip_trunk_target=snapshot.sip_trunk_target,
        sip_auth_required=snapshot.sip_auth_required,
        credentials_reference=snapshot.credentials_reference,
        ip_whitelist_confirmed=snapshot.ip_whitelist_confirmed,
        sip_forwarding_confirmed=snapshot.sip_forwarding_confirmed,
        configuration_confirmed=snapshot.configuration_confirmed,
        last_verified_at=snapshot.last_verified_at,
        notes=snapshot.notes,
        manual_requirements=list(snapshot.manual_requirements),
        recommended_actions=list(snapshot.recommended_actions),
    )


def _build_africas_talking_binding_sync_response(
    result: AfricasTalkingBindingSyncResult,
) -> AfricasTalkingBindingSyncResponse:
    return AfricasTalkingBindingSyncResponse(
        number=result.number,
        binding=result.binding,
        detail=result.detail,
        provider_binding=_build_africas_talking_binding_state_response(result.provider_snapshot),
        created_number=result.created_number,
        created_binding=result.created_binding,
    )


def _build_phone_binding_reconciliation_result_response(
    result: PhoneBindingReconciliationResult,
) -> PhoneBindingReconciliationResultResponse:
    return PhoneBindingReconciliationResultResponse(
        phone_number_id=result.phone_number_id,
        binding_id=result.binding_id,
        provider=result.provider,
        operation_status=result.operation_status,
        previous_verification_status=result.previous_verification_status,
        previous_health_status=result.previous_health_status,
        verification_status=result.verification_status,
        health_status=result.health_status,
        changed=result.changed,
        notification_emitted=result.notification_emitted,
        error=result.error,
        reconciled_at=result.reconciled_at,
    )


def _build_phone_binding_reconciliation_response(
    summary: PhoneBindingReconciliationSummary,
) -> PhoneBindingReconciliationResponse:
    return PhoneBindingReconciliationResponse(
        organization_id=summary.organization_id,
        processed_count=summary.processed_count,
        changed_count=summary.changed_count,
        failed_count=summary.failed_count,
        results=[
            _build_phone_binding_reconciliation_result_response(item)
            for item in summary.results
        ],
    )


def _raise_telnyx_http_error(exc: Exception) -> None:
    if isinstance(exc, TelnyxProviderUnavailableError):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(exc, TelnyxProviderNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, TelnyxProviderError):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise


def build_phone_numbers_router(
    *,
    phone_number_registry: PhoneNumberRegistryService | None,
    phone_number_audit_service: PhoneNumberAuditService | None,
    phone_number_operations_service: PhoneNumberOperationsService | None,
    notification_store,
    settings: "RuntimeSettings",
    agent_registry: "SQLAlchemyAgentRegistry",
    telnyx_provider_state: Callable[[], TelnyxPhoneProvider | None],
    telnyx_http_client_state: Callable[[], object | None],
    at_provider_state: Callable[[], AfricasTalkingPhoneProvider | None],
) -> APIRouter:
    """Build the phone-numbers + phone-providers router."""
    router = APIRouter()

    def _require_phone_number_registry() -> PhoneNumberRegistryService:
        if phone_number_registry is None:
            raise HTTPException(status_code=503, detail="phone number registry is not configured")
        return phone_number_registry

    def _validate_phone_route_agent(
        *,
        agent_id: str,
        organization_id: str,
    ) -> None:
        try:
            agent_registry.get_agent_registration(agent_id, organization_id=organization_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _build_phone_number_service() -> PhoneNumberService:
        configured_provider = telnyx_provider_state()
        telnyx_provider = configured_provider
        if telnyx_provider is None:
            telnyx_api_key = settings.telnyx_api_key
            if isinstance(telnyx_api_key, str) and telnyx_api_key.strip():
                telnyx_provider = TelnyxPhoneProvider(
                    api_key=telnyx_api_key,
                    base_url=settings.telnyx_api_base_url,
                    timeout_seconds=settings.telnyx_timeout_seconds,
                    http_client=telnyx_http_client_state(),
                )
        at_provider: AfricasTalkingPhoneProvider | None = at_provider_state()
        if at_provider is None:
            _at_key = settings.africastalking_api_key
            _at_user = settings.africastalking_username
            if _at_key and _at_user:
                at_provider = AfricasTalkingPhoneProvider(
                    api_key=_at_key,
                    username=_at_user,
                    sandbox=settings.africastalking_sandbox,
                    timeout_seconds=settings.africastalking_timeout_seconds,
                )
        return PhoneNumberService(
            registry=_require_phone_number_registry(),
            telnyx_provider=telnyx_provider,
            at_provider=at_provider,
        )

    def _require_phone_number_audit_service() -> PhoneNumberAuditService:
        if phone_number_audit_service is None:
            raise HTTPException(status_code=503, detail="phone number audit service is not configured")
        return phone_number_audit_service

    def _build_phone_number_operations_service() -> PhoneNumberOperationsService:
        if phone_number_operations_service is not None:
            return phone_number_operations_service
        return PhoneNumberOperationsService(
            registry=_require_phone_number_registry(),
            phone_number_service=_build_phone_number_service(),
            audit_service=phone_number_audit_service,
            notification_store=notification_store,
        )

    def _record_phone_audit_event(
        *,
        request: Request | None,
        context: RequestAuthContext | None,
        action: str,
        resource_type: str,
        summary: str,
        phone_number_id: str | None = None,
        resource_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        if phone_number_audit_service is None:
            return
        principal = None if context is None else context.principal
        audit_context = None if request is None else build_session_audit_context(request)
        _require_phone_number_audit_service().record_event(
            organization_id=(
                ""
                if principal is None
                else principal.organization.organization_id
            ),
            phone_number_id=phone_number_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            summary=summary,
            actor_type="system" if principal is None else "user",
            actor_user_id=None if principal is None else principal.user.user_id,
            payload=payload,
            ip_address=None if audit_context is None else audit_context.ip,
            user_agent=None if audit_context is None else audit_context.user_agent,
        )

    @router.get("/phone-numbers", response_model=list[PhoneNumber])
    def list_phone_numbers(
        status: str | None = Query(default=None),
        limit: int = Query(default=500, ge=1, le=2000),
        offset: int = Query(default=0, ge=0),
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> list[PhoneNumber]:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return _require_phone_number_registry().list_numbers(
            organization_id=principal.organization.organization_id,
            status=status,
            limit=limit,
            offset=offset,
        )

    @router.post("/phone-numbers", response_model=PhoneNumber, status_code=201)
    def create_phone_number(
        request: Request,
        payload: PhoneNumberCreateRequest,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> PhoneNumber:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            result = _require_phone_number_registry().create_number(
                organization_id=principal.organization.organization_id,
                e164_number=payload.e164_number,
                display_name=payload.display_name,
                ownership_mode=payload.ownership_mode,
                status=payload.status,
                metadata=payload.metadata,
            )
            _record_phone_audit_event(
                request=request,
                context=context,
                phone_number_id=result.phone_number_id,
                action="phone.number.created",
                resource_type="phone_number",
                resource_id=result.phone_number_id,
                summary="Phone number created",
                payload=result.model_dump(mode="json"),
            )
            return result
        except PhoneNumberRegistryConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/phone-numbers/audit", response_model=list[PhoneNumberAuditEvent])
    def list_phone_number_audit_events(
        phone_number_id: str | None = Query(default=None),
        resource_type: str | None = Query(default=None),
        resource_id: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> list[PhoneNumberAuditEvent]:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return _require_phone_number_audit_service().list_events(
            organization_id=principal.organization.organization_id,
            phone_number_id=phone_number_id,
            resource_type=resource_type,
            resource_id=resource_id,
            limit=limit,
        )

    @router.post("/phone-numbers/reconcile", response_model=PhoneBindingReconciliationResponse)
    async def reconcile_phone_number_bindings(
        request: Request,
        payload: PhoneBindingReconciliationRequest,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> PhoneBindingReconciliationResponse:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        audit_context = build_session_audit_context(request)
        summary = await _build_phone_number_operations_service().reconcile_bindings(
            organization_id=principal.organization.organization_id,
            provider=payload.provider,
            phone_number_id=payload.phone_number_id,
            binding_id=payload.binding_id,
            limit=payload.limit,
            actor_type="user",
            actor_user_id=principal.user.user_id,
            ip_address=audit_context.ip,
            user_agent=audit_context.user_agent,
        )
        return _build_phone_binding_reconciliation_response(summary)

    @router.get("/phone-numbers/{phone_number_id}", response_model=PhoneNumberDetail)
    def get_phone_number_detail(
        phone_number_id: str,
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> PhoneNumberDetail:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            return _require_phone_number_registry().get_number_detail(
                phone_number_id,
                organization_id=principal.organization.organization_id,
            )
        except PhoneNumberRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/phone-numbers/{phone_number_id}/bindings", response_model=list[PhoneNumberBinding])
    def list_phone_number_bindings(
        phone_number_id: str,
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> list[PhoneNumberBinding]:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            return _require_phone_number_registry().list_bindings(
                phone_number_id,
                organization_id=principal.organization.organization_id,
            )
        except PhoneNumberRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.patch("/phone-numbers/{phone_number_id}", response_model=PhoneNumber)
    def update_phone_number(
        request: Request,
        phone_number_id: str,
        payload: PhoneNumberUpdateRequest,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> PhoneNumber:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            result = _require_phone_number_registry().update_number(
                phone_number_id,
                organization_id=principal.organization.organization_id,
                display_name=(
                    payload.display_name
                    if "display_name" in payload.model_fields_set
                    else PHONE_NUMBER_REGISTRY_UNSET
                ),
                status=payload.status if "status" in payload.model_fields_set else PHONE_NUMBER_REGISTRY_UNSET,
                ownership_mode=(
                    payload.ownership_mode
                    if "ownership_mode" in payload.model_fields_set
                    else PHONE_NUMBER_REGISTRY_UNSET
                ),
                metadata=payload.metadata if "metadata" in payload.model_fields_set else PHONE_NUMBER_REGISTRY_UNSET,
            )
            _record_phone_audit_event(
                request=request,
                context=context,
                phone_number_id=result.phone_number_id,
                action="phone.number.updated",
                resource_type="phone_number",
                resource_id=result.phone_number_id,
                summary="Phone number updated",
                payload=result.model_dump(mode="json"),
            )
            return result
        except PhoneNumberRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/phone-numbers/{phone_number_id}/bindings", response_model=PhoneNumberBinding, status_code=201)
    def create_phone_number_binding(
        request: Request,
        phone_number_id: str,
        payload: PhoneNumberBindingCreateRequest,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> PhoneNumberBinding:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            result = _require_phone_number_registry().create_binding(
                phone_number_id=phone_number_id,
                organization_id=principal.organization.organization_id,
                channel=payload.channel,
                provider=payload.provider,
                provider_resource_id=payload.provider_resource_id,
                capabilities=payload.capabilities,
                verification_status=payload.verification_status,
                health_status=payload.health_status,
                is_active=payload.is_active,
                transport_metadata=payload.transport_metadata,
            )
            _record_phone_audit_event(
                request=request,
                context=context,
                phone_number_id=result.phone_number_id,
                action="phone.binding.created",
                resource_type="phone_number_binding",
                resource_id=result.binding_id,
                summary="Phone number binding created",
                payload=result.model_dump(mode="json"),
            )
            return result
        except PhoneNumberRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PhoneNumberRegistryConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.patch(
        "/phone-numbers/{phone_number_id}/bindings/{binding_id}",
        response_model=PhoneNumberBinding,
    )
    def update_phone_number_binding(
        request: Request,
        phone_number_id: str,
        binding_id: str,
        payload: PhoneNumberBindingUpdateRequest,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> PhoneNumberBinding:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            result = _require_phone_number_registry().update_binding(
                phone_number_id,
                binding_id,
                organization_id=principal.organization.organization_id,
                provider_resource_id=(
                    payload.provider_resource_id
                    if "provider_resource_id" in payload.model_fields_set
                    else PHONE_NUMBER_REGISTRY_UNSET
                ),
                capabilities=(
                    payload.capabilities
                    if "capabilities" in payload.model_fields_set
                    else PHONE_NUMBER_REGISTRY_UNSET
                ),
                verification_status=(
                    payload.verification_status
                    if "verification_status" in payload.model_fields_set
                    else PHONE_NUMBER_REGISTRY_UNSET
                ),
                health_status=(
                    payload.health_status
                    if "health_status" in payload.model_fields_set
                    else PHONE_NUMBER_REGISTRY_UNSET
                ),
                is_active=payload.is_active if "is_active" in payload.model_fields_set else PHONE_NUMBER_REGISTRY_UNSET,
                transport_metadata=(
                    payload.transport_metadata
                    if "transport_metadata" in payload.model_fields_set
                    else PHONE_NUMBER_REGISTRY_UNSET
                ),
            )
            _record_phone_audit_event(
                request=request,
                context=context,
                phone_number_id=result.phone_number_id,
                action="phone.binding.updated",
                resource_type="phone_number_binding",
                resource_id=result.binding_id,
                summary="Phone number binding updated",
                payload=result.model_dump(mode="json"),
            )
            return result
        except PhoneNumberRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PhoneNumberRegistryConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/phone-numbers/{phone_number_id}/routes", response_model=list[PhoneNumberRoute])
    def list_phone_number_routes(
        phone_number_id: str,
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> list[PhoneNumberRoute]:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            return _require_phone_number_registry().list_routes(
                phone_number_id,
                organization_id=principal.organization.organization_id,
            )
        except PhoneNumberRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/phone-numbers/{phone_number_id}/routes", response_model=PhoneNumberRoute, status_code=201)
    def create_phone_number_route(
        request: Request,
        response: Response,
        phone_number_id: str,
        payload: PhoneNumberRouteCreateRequest,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> PhoneNumberRoute:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        organization_id = principal.organization.organization_id
        _validate_phone_route_agent(agent_id=payload.agent_id, organization_id=organization_id)
        try:
            registry = _require_phone_number_registry()
            result = registry.create_or_replace_route(
                phone_number_id=phone_number_id,
                organization_id=organization_id,
                channel=payload.channel,
                agent_id=payload.agent_id,
                priority=payload.priority,
                enabled=payload.enabled,
                metadata=payload.metadata,
            )
            # Warn if another enabled route on the same number+channel has
            # the same priority — the tiebreaker (updated_at DESC) is
            # non-obvious and callers should set explicit priorities.
            if payload.enabled:
                existing = registry.list_routes(phone_number_id, organization_id=organization_id)
                collisions = [
                    r for r in existing
                    if r.route_id != result.route_id
                    and r.channel == payload.channel
                    and r.enabled
                    and r.priority == payload.priority
                ]
                if collisions:
                    response.headers["X-Route-Priority-Warning"] = (
                        f"Another enabled route on this number+channel shares priority {payload.priority}. "
                        "The most recently updated route will win. Set distinct priorities for deterministic ordering."
                    )
            _record_phone_audit_event(
                request=request,
                context=context,
                phone_number_id=result.phone_number_id,
                action="phone.route.created",
                resource_type="phone_number_route",
                resource_id=result.route_id,
                summary="Phone number route created",
                payload=result.model_dump(mode="json"),
            )
            return result
        except PhoneNumberRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PhoneNumberRegistryConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.patch("/phone-numbers/{phone_number_id}/routes/{route_id}", response_model=PhoneNumberRoute)
    def update_phone_number_route(
        request: Request,
        phone_number_id: str,
        route_id: str,
        payload: PhoneNumberRouteUpdateRequest,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> PhoneNumberRoute:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        organization_id = principal.organization.organization_id
        if payload.agent_id is not None:
            _validate_phone_route_agent(agent_id=payload.agent_id, organization_id=organization_id)
        try:
            result = _require_phone_number_registry().update_route(
                phone_number_id,
                route_id,
                organization_id=organization_id,
                agent_id=payload.agent_id if "agent_id" in payload.model_fields_set else PHONE_NUMBER_REGISTRY_UNSET,
                priority=payload.priority if "priority" in payload.model_fields_set else PHONE_NUMBER_REGISTRY_UNSET,
                enabled=payload.enabled if "enabled" in payload.model_fields_set else PHONE_NUMBER_REGISTRY_UNSET,
                metadata=payload.metadata if "metadata" in payload.model_fields_set else PHONE_NUMBER_REGISTRY_UNSET,
            )
            _record_phone_audit_event(
                request=request,
                context=context,
                phone_number_id=result.phone_number_id,
                action="phone.route.updated",
                resource_type="phone_number_route",
                resource_id=result.route_id,
                summary="Phone number route updated",
                payload=result.model_dump(mode="json"),
            )
            return result
        except PhoneNumberRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PhoneNumberRegistryConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/phone-providers/telnyx/available-numbers", response_model=list[TelnyxAvailableNumberResponse])
    async def list_telnyx_available_phone_numbers(
        country_code: str = Query(..., min_length=2, max_length=2),
        phone_number_type: str = Query(default="local"),
        national_destination_code: str | None = Query(default=None),
        locality: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> list[TelnyxAvailableNumberResponse]:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            numbers = await _build_phone_number_service().list_available_telnyx_numbers(
                country_code=country_code,
                phone_number_type=phone_number_type,
                national_destination_code=national_destination_code,
                locality=locality,
                limit=limit,
            )
        except Exception as exc:
            _raise_telnyx_http_error(exc)
            raise
        return [_build_telnyx_available_number_response(item) for item in numbers]

    @router.post("/phone-providers/telnyx/import", response_model=TelnyxBindingSyncResponse)
    async def import_telnyx_phone_number(
        request: Request,
        payload: TelnyxPhoneNumberImportRequest,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> TelnyxBindingSyncResponse:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            result = await _build_phone_number_service().import_telnyx_number(
                organization_id=principal.organization.organization_id,
                phone_number_id=payload.phone_number_id,
                phone_number=payload.phone_number,
                provider_resource_id=payload.provider_resource_id,
                display_name=payload.display_name,
                metadata=payload.metadata,
                channel=payload.channel,
            )
        except Exception as exc:
            _raise_telnyx_http_error(exc)
            raise
        _record_phone_audit_event(
            request=request,
            context=context,
            phone_number_id=result.number.phone_number_id,
            action="phone.provider.telnyx.imported",
            resource_type="phone_number_binding",
            resource_id=result.binding.binding_id,
            summary="Telnyx number imported into registry",
            payload={
                "created_number": result.created_number,
                "created_binding": result.created_binding,
                "provider_resource_id": result.binding.provider_resource_id,
            },
        )
        return _build_telnyx_binding_sync_response(result)

    @router.post(
        "/phone-numbers/{phone_number_id}/bindings/{binding_id}/providers/telnyx/sync",
        response_model=TelnyxBindingSyncResponse,
    )
    async def sync_telnyx_phone_number_binding(
        request: Request,
        phone_number_id: str,
        binding_id: str,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> TelnyxBindingSyncResponse:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            result = await _build_phone_number_service().sync_telnyx_binding(
                organization_id=principal.organization.organization_id,
                phone_number_id=phone_number_id,
                binding_id=binding_id,
            )
        except Exception as exc:
            _raise_telnyx_http_error(exc)
            raise
        _record_phone_audit_event(
            request=request,
            context=context,
            phone_number_id=result.number.phone_number_id,
            action="phone.provider.telnyx.synced",
            resource_type="phone_number_binding",
            resource_id=result.binding.binding_id,
            summary="Telnyx binding synced",
            payload={
                "provider_resource_id": result.binding.provider_resource_id,
                "health_status": result.binding.health_status,
                "verification_status": result.binding.verification_status,
            },
        )
        return _build_telnyx_binding_sync_response(result)

    @router.post("/phone-providers/africastalking/import", response_model=AfricasTalkingBindingSyncResponse)
    async def import_africas_talking_phone_number(
        request: Request,
        payload: AfricasTalkingPhoneNumberImportRequest,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> AfricasTalkingBindingSyncResponse:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            result = await _build_phone_number_service().import_africas_talking_number(
                organization_id=principal.organization.organization_id,
                phone_number=payload.phone_number,
                phone_number_id=payload.phone_number_id,
                provider_resource_id=payload.provider_resource_id,
                display_name=payload.display_name,
                metadata=payload.metadata,
                channel=payload.channel,
                account_username=payload.account_username,
                voice_callback_url=payload.voice_callback_url,
                events_callback_url=payload.events_callback_url,
                sip_trunk_target=payload.sip_trunk_target,
                sip_auth_required=payload.sip_auth_required,
                credentials_reference=payload.credentials_reference,
                ip_whitelist_confirmed=payload.ip_whitelist_confirmed,
                sip_forwarding_confirmed=payload.sip_forwarding_confirmed,
                configuration_confirmed=payload.configuration_confirmed,
                last_verified_at=payload.last_verified_at,
                notes=payload.notes,
            )
        except PhoneNumberRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PhoneNumberRegistryConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _record_phone_audit_event(
            request=request,
            context=context,
            phone_number_id=result.number.phone_number_id,
            action="phone.provider.africastalking.imported",
            resource_type="phone_number_binding",
            resource_id=result.binding.binding_id,
            summary="Africa's Talking number imported into registry",
            payload={
                "created_number": result.created_number,
                "created_binding": result.created_binding,
                "provider_resource_id": result.binding.provider_resource_id,
            },
        )
        return _build_africas_talking_binding_sync_response(result)

    @router.post(
        "/phone-numbers/{phone_number_id}/bindings/{binding_id}/providers/africastalking/sync",
        response_model=AfricasTalkingBindingSyncResponse,
    )
    async def sync_africas_talking_phone_number_binding(
        request: Request,
        phone_number_id: str,
        binding_id: str,
        payload: AfricasTalkingBindingSyncRequest,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> AfricasTalkingBindingSyncResponse:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            result = await _build_phone_number_service().sync_africas_talking_binding(
                organization_id=principal.organization.organization_id,
                phone_number_id=phone_number_id,
                binding_id=binding_id,
                provider_resource_id=(
                    payload.provider_resource_id
                    if "provider_resource_id" in payload.model_fields_set
                    else PHONE_NUMBER_SERVICE_UNSET
                ),
                account_username=(
                    payload.account_username
                    if "account_username" in payload.model_fields_set
                    else PHONE_NUMBER_SERVICE_UNSET
                ),
                voice_callback_url=(
                    payload.voice_callback_url
                    if "voice_callback_url" in payload.model_fields_set
                    else PHONE_NUMBER_SERVICE_UNSET
                ),
                events_callback_url=(
                    payload.events_callback_url
                    if "events_callback_url" in payload.model_fields_set
                    else PHONE_NUMBER_SERVICE_UNSET
                ),
                sip_trunk_target=(
                    payload.sip_trunk_target
                    if "sip_trunk_target" in payload.model_fields_set
                    else PHONE_NUMBER_SERVICE_UNSET
                ),
                sip_auth_required=(
                    payload.sip_auth_required
                    if "sip_auth_required" in payload.model_fields_set
                    else PHONE_NUMBER_SERVICE_UNSET
                ),
                credentials_reference=(
                    payload.credentials_reference
                    if "credentials_reference" in payload.model_fields_set
                    else PHONE_NUMBER_SERVICE_UNSET
                ),
                ip_whitelist_confirmed=(
                    payload.ip_whitelist_confirmed
                    if "ip_whitelist_confirmed" in payload.model_fields_set
                    else PHONE_NUMBER_SERVICE_UNSET
                ),
                sip_forwarding_confirmed=(
                    payload.sip_forwarding_confirmed
                    if "sip_forwarding_confirmed" in payload.model_fields_set
                    else PHONE_NUMBER_SERVICE_UNSET
                ),
                configuration_confirmed=(
                    payload.configuration_confirmed
                    if "configuration_confirmed" in payload.model_fields_set
                    else PHONE_NUMBER_SERVICE_UNSET
                ),
                last_verified_at=(
                    payload.last_verified_at
                    if "last_verified_at" in payload.model_fields_set
                    else PHONE_NUMBER_SERVICE_UNSET
                ),
                notes=payload.notes if "notes" in payload.model_fields_set else PHONE_NUMBER_SERVICE_UNSET,
            )
        except PhoneNumberRegistryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PhoneNumberRegistryConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _record_phone_audit_event(
            request=request,
            context=context,
            phone_number_id=result.number.phone_number_id,
            action="phone.provider.africastalking.synced",
            resource_type="phone_number_binding",
            resource_id=result.binding.binding_id,
            summary="Africa's Talking binding synced",
            payload={
                "provider_resource_id": result.binding.provider_resource_id,
                "health_status": result.binding.health_status,
                "verification_status": result.binding.verification_status,
            },
        )
        return _build_africas_talking_binding_sync_response(result)

    @router.post(
        "/phone-providers/africastalking/validate-credentials",
        response_model=AfricasTalkingValidateCredentialsResponse,
    )
    async def validate_africas_talking_credentials(
        payload: AfricasTalkingValidateCredentialsRequest,
        _context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> AfricasTalkingValidateCredentialsResponse:
        result = await _build_phone_number_service().validate_africas_talking_credentials(
            username=payload.username,
            api_key=payload.api_key,
        )
        return AfricasTalkingValidateCredentialsResponse(
            valid=result.valid,
            username=result.username,
            account_type=result.account_type,
            balance=result.balance,
            error=result.error,
        )

    @router.post(
        "/phone-providers/africastalking/check-callback-reachability",
        response_model=AfricasTalkingCheckCallbackReachabilityResponse,
    )
    async def check_africas_talking_callback_reachability(
        payload: AfricasTalkingCheckCallbackReachabilityRequest,
        _context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> AfricasTalkingCheckCallbackReachabilityResponse:
        result = await _build_phone_number_service().check_africas_talking_callback_reachability(
            payload.url,
        )
        return AfricasTalkingCheckCallbackReachabilityResponse(
            url=result.url,
            status=result.status,
            reachable=result.reachable,
            http_status_code=result.http_status_code,
            error=result.error,
        )

    return router
