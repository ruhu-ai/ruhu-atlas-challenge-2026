from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .notifications.service import emit_notification
from .notifications.store import NotificationStore
from .phone_number_audit import PhoneNumberAuditService
from .phone_number_registry import PhoneNumberBinding, PhoneNumberRegistryService
from .phone_number_service import PhoneNumberService
from .phone_provider_telnyx import (
    TelnyxProviderError,
    TelnyxProviderNotFoundError,
    TelnyxProviderUnavailableError,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


@dataclass(slots=True, frozen=True)
class PhoneBindingReconciliationResult:
    phone_number_id: str
    binding_id: str
    provider: str
    operation_status: str
    previous_verification_status: str
    previous_health_status: str
    verification_status: str
    health_status: str
    changed: bool
    notification_emitted: bool = False
    error: str | None = None
    reconciled_at: str = field(default_factory=_utcnow_iso)


@dataclass(slots=True, frozen=True)
class PhoneBindingReconciliationSummary:
    organization_id: str
    processed_count: int
    changed_count: int
    failed_count: int
    results: list[PhoneBindingReconciliationResult] = field(default_factory=list)


class PhoneNumberOperationsService:
    def __init__(
        self,
        *,
        registry: PhoneNumberRegistryService,
        phone_number_service: PhoneNumberService,
        audit_service: PhoneNumberAuditService | None = None,
        notification_store: NotificationStore | None = None,
    ) -> None:
        self._registry = registry
        self._phone_number_service = phone_number_service
        self._audit_service = audit_service
        self._notification_store = notification_store

    async def reconcile_bindings(
        self,
        *,
        organization_id: str,
        provider: str | None = None,
        phone_number_id: str | None = None,
        binding_id: str | None = None,
        limit: int = 50,
        actor_type: str = "system",
        actor_user_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> PhoneBindingReconciliationSummary:
        bindings = self._registry.list_bindings_for_organization(
            organization_id=organization_id,
            provider=provider,
            phone_number_id=phone_number_id,
            active_only=True,
            limit=limit,
        )
        if binding_id is not None:
            bindings = [item for item in bindings if item.binding_id == binding_id]

        results: list[PhoneBindingReconciliationResult] = []
        for binding in bindings:
            results.append(
                await self._reconcile_binding(
                    organization_id=organization_id,
                    binding=binding,
                    actor_type=actor_type,
                    actor_user_id=actor_user_id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            )

        summary = PhoneBindingReconciliationSummary(
            organization_id=organization_id,
            processed_count=len(results),
            changed_count=sum(1 for item in results if item.changed),
            failed_count=sum(1 for item in results if item.operation_status == "failed"),
            results=results,
        )
        if self._audit_service is not None:
            self._audit_service.record_event(
                organization_id=organization_id,
                action="phone.reconciliation.run",
                resource_type="phone_reconciliation",
                summary=f"Reconciled {summary.processed_count} active phone bindings",
                actor_type=actor_type,
                actor_user_id=actor_user_id,
                payload={
                    "provider": provider,
                    "phone_number_id": phone_number_id,
                    "binding_id": binding_id,
                    "processed_count": summary.processed_count,
                    "changed_count": summary.changed_count,
                    "failed_count": summary.failed_count,
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )
        return summary

    async def _reconcile_binding(
        self,
        *,
        organization_id: str,
        binding: PhoneNumberBinding,
        actor_type: str,
        actor_user_id: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> PhoneBindingReconciliationResult:
        previous_verification_status = binding.verification_status
        previous_health_status = binding.health_status
        error: str | None = None
        operation_status = "unchanged"

        try:
            if binding.provider == "telnyx":
                sync_result = await self._phone_number_service.sync_telnyx_binding(
                    organization_id=organization_id,
                    phone_number_id=binding.phone_number_id,
                    binding_id=binding.binding_id,
                )
                current_binding = sync_result.binding
            elif binding.provider == "africastalking":
                sync_result = await self._phone_number_service.sync_africas_talking_binding(
                    organization_id=organization_id,
                    phone_number_id=binding.phone_number_id,
                    binding_id=binding.binding_id,
                )
                current_binding = sync_result.binding
            else:
                current_binding = self._mark_binding_reconciliation(
                    binding=binding,
                    verification_status=binding.verification_status,
                    health_status=binding.health_status,
                    error=f"unsupported provider for reconciliation: {binding.provider}",
                )
                error = f"unsupported provider for reconciliation: {binding.provider}"
                operation_status = "failed"
        except TelnyxProviderNotFoundError as exc:
            error = str(exc)
            current_binding = self._mark_binding_reconciliation(
                binding=binding,
                verification_status="failed",
                health_status="misconfigured",
                error=error,
            )
            operation_status = "failed"
        except TelnyxProviderUnavailableError as exc:
            error = str(exc)
            current_binding = self._mark_binding_reconciliation(
                binding=binding,
                verification_status=binding.verification_status,
                health_status=_degraded_or_existing(binding.health_status),
                error=error,
            )
            operation_status = "failed"
        except TelnyxProviderError as exc:
            error = str(exc)
            current_binding = self._mark_binding_reconciliation(
                binding=binding,
                verification_status=binding.verification_status,
                health_status=_degraded_or_existing(binding.health_status),
                error=error,
            )
            operation_status = "failed"
        except ValueError as exc:
            error = str(exc)
            current_binding = self._registry.get_binding(
                binding.phone_number_id,
                binding.binding_id,
                organization_id=organization_id,
            )
            current_binding = self._mark_binding_reconciliation(
                binding=current_binding,
                verification_status=current_binding.verification_status,
                health_status=current_binding.health_status,
                error=error,
            )
            operation_status = "failed"

        if operation_status != "failed":
            current_binding = self._mark_binding_reconciliation(
                binding=current_binding,
                verification_status=current_binding.verification_status,
                health_status=current_binding.health_status,
                error=None,
            )

        changed = (
            current_binding.verification_status != previous_verification_status
            or current_binding.health_status != previous_health_status
        )
        if operation_status != "failed" and changed:
            operation_status = "updated"

        notification_emitted = self._emit_reconciliation_notification(
            organization_id=organization_id,
            binding=current_binding,
            previous_verification_status=previous_verification_status,
            previous_health_status=previous_health_status,
            changed=changed,
        )
        if self._audit_service is not None and (changed or operation_status == "failed"):
            self._audit_service.record_event(
                organization_id=organization_id,
                phone_number_id=current_binding.phone_number_id,
                action="phone.binding.reconciled",
                resource_type="phone_number_binding",
                resource_id=current_binding.binding_id,
                summary=_build_reconciliation_summary(
                    binding=current_binding,
                    operation_status=operation_status,
                ),
                actor_type=actor_type,
                actor_user_id=actor_user_id,
                payload={
                    "provider": current_binding.provider,
                    "previous_verification_status": previous_verification_status,
                    "previous_health_status": previous_health_status,
                    "verification_status": current_binding.verification_status,
                    "health_status": current_binding.health_status,
                    "changed": changed,
                    "error": error,
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )

        return PhoneBindingReconciliationResult(
            phone_number_id=current_binding.phone_number_id,
            binding_id=current_binding.binding_id,
            provider=current_binding.provider,
            operation_status=operation_status,
            previous_verification_status=previous_verification_status,
            previous_health_status=previous_health_status,
            verification_status=current_binding.verification_status,
            health_status=current_binding.health_status,
            changed=changed,
            notification_emitted=notification_emitted,
            error=error,
        )

    def _mark_binding_reconciliation(
        self,
        *,
        binding: PhoneNumberBinding,
        verification_status: str,
        health_status: str,
        error: str | None,
    ) -> PhoneNumberBinding:
        reconciliation = {
            "last_reconciled_at": _utcnow_iso(),
            "status": "error" if error else "ok",
            "error": error,
        }
        transport_metadata = dict(binding.transport_metadata)
        transport_metadata["reconciliation"] = reconciliation
        return self._registry.update_binding(
            binding.phone_number_id,
            binding.binding_id,
            organization_id=binding.organization_id,
            verification_status=verification_status,
            health_status=health_status,
            transport_metadata=transport_metadata,
        )

    def _emit_reconciliation_notification(
        self,
        *,
        organization_id: str,
        binding: PhoneNumberBinding,
        previous_verification_status: str,
        previous_health_status: str,
        changed: bool,
    ) -> bool:
        if self._notification_store is None or not changed:
            return False
        if binding.health_status in {"degraded", "misconfigured"} or binding.verification_status in {
            "failed",
            "manual_required",
        }:
            emit_notification(
                self._notification_store,
                organization_id=organization_id,
                category="phone.binding_attention_required",
                level="warning",
                urgency="high",
                title=f"Phone binding needs attention: {binding.provider}",
                message=(
                    f"Binding {binding.binding_id} moved from {previous_health_status}/{previous_verification_status} "
                    f"to {binding.health_status}/{binding.verification_status}."
                ),
                url="/phone-numbers",
                url_label="Review phone numbers",
                source_type="phone_number_binding",
                source_id=binding.binding_id,
                payload={
                    "phone_number_id": binding.phone_number_id,
                    "binding_id": binding.binding_id,
                    "provider": binding.provider,
                    "previous_health_status": previous_health_status,
                    "previous_verification_status": previous_verification_status,
                    "health_status": binding.health_status,
                    "verification_status": binding.verification_status,
                },
            )
            return True
        emit_notification(
            self._notification_store,
            organization_id=organization_id,
            category="phone.binding_recovered",
            level="info",
            urgency="medium",
            title=f"Phone binding recovered: {binding.provider}",
            message=(
                f"Binding {binding.binding_id} is now {binding.health_status}/{binding.verification_status}."
            ),
            url="/phone-numbers",
            url_label="Review phone numbers",
            source_type="phone_number_binding",
            source_id=binding.binding_id,
            payload={
                "phone_number_id": binding.phone_number_id,
                "binding_id": binding.binding_id,
                "provider": binding.provider,
                "previous_health_status": previous_health_status,
                "previous_verification_status": previous_verification_status,
                "health_status": binding.health_status,
                "verification_status": binding.verification_status,
            },
        )
        return True


def _degraded_or_existing(current_health_status: str) -> str:
    if current_health_status == "misconfigured":
        return current_health_status
    return "degraded"


def _build_reconciliation_summary(
    *,
    binding: PhoneNumberBinding,
    operation_status: str,
) -> str:
    if operation_status == "failed":
        return f"Phone binding reconciliation failed for {binding.provider}"
    if operation_status == "updated":
        return f"Phone binding reconciliation updated {binding.provider} state"
    return f"Phone binding reconciliation completed for {binding.provider}"
