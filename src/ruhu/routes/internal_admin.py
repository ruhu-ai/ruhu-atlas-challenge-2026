"""Internal platform admin routes — extracted from api.py (RP-3.1 step 7).

Covers the superuser-gated /internal/platform/health, /internal/auth and
/internal/intent-tags diagnostics, /internal/organizations, and the
/internal/users surface (list, external identities, promote/revoke
superuser). The two diagnostics closures (``_internal_auth_diagnostics``,
``_internal_intent_tags_classifier_diagnostics``) move here with their
captures threaded as explicit kwargs (settings, identity store, intent-tags
runtime, provider-cost store, jobs store).

Mounted under the same guard as the auth profile router: ``if auth_enabled
and effective_auth_service is not None and effective_identity_store is not
None:``. The superuser dependency is built via the ``auth_deps`` factory
INSIDE the builder (blueprint DI guidance). The DTOs and summary builders
still live in ``ruhu.api``, so this module is imported by ``create_app()``
AT THE MOUNT SITE. No ``tags=`` / ``prefix=`` and unchanged handler names
(hazard H1).
"""
from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Depends, HTTPException

# DTOs and helpers at module top (hazard H7: PEP 563 annotations resolve
# against this module's globals).
from ..api import (
    ExternalIdentitySummary,
    InternalAuthDiagnosticsResponse,
    InternalIntentTagsClassifierDiagnosticsResponse,
    InternalOrganizationSummary,
    InternalPlatformHealthResponse,
    InternalUserSummary,
    _build_external_identity_summary,
    _build_internal_user_summary,
    _classifier_api_key_source,
    _jwt_text_source,
    _jwt_verification_source,
    _raise_http_for_auth_error,
)
from ..api_auth import RequestAuthContext
from ..auth import AuthenticationError, AuthorizationError, ConflictError
from ..auth_deps import make_internal_superuser_dep
from ..conversation_sweep import SWEEP_JOB_TYPE
from ..email_transport import DevOutboxEmailSender, RetryingEmailSender
from ..analytics_tagging.webhooks import WEBHOOK_DISPATCH_JOB_TYPE
from ..jobs import recurring_tick_status
from ..sentiment_worker import SENTIMENT_JOB_TYPE

if TYPE_CHECKING:
    from ..auth import AuthService
    from ..email_transport import EmailSender
    from ..identity import IdentityStore
    from ..analytics_tagging import IntentTagsRuntime
    from ..jobs import JobStore
    from ..runtime_config import RuntimeSettings


def build_internal_admin_router(
    *,
    auth_enabled: bool,
    auth_service: "AuthService",
    identity_store: "IdentityStore",
    settings: "RuntimeSettings",
    email_sender: "EmailSender | None",
    intent_tags_runtime: "IntentTagsRuntime | None",
    provider_cost_store,
    jobs_store: "JobStore",
) -> APIRouter:
    """Build the /internal/* platform admin router."""
    router = APIRouter()
    _require_internal_superuser = make_internal_superuser_dep()

    def _internal_auth_diagnostics() -> InternalAuthDiagnosticsResponse:
        if auth_service is None:
            return InternalAuthDiagnosticsResponse(
                auth_enabled=False,
                environment=settings.environment,
                asymmetric_required=settings.auth_require_asymmetric_tokens,
                signing_material_source=_jwt_text_source(
                    inline_value=settings.auth_jwt_private_key_pem,
                    file_path=settings.auth_jwt_private_key_path,
                    secret_version=settings.auth_jwt_private_key_secret_version,
                ),
                verification_jwks_source=_jwt_verification_source(
                    inline_value=settings.auth_jwt_verification_jwks,
                    file_path=settings.auth_jwt_verification_jwks_path,
                    secret_version=settings.auth_jwt_verification_jwks_secret_version,
                    rs256_enabled=False,
                ),
            )
        key_manager = auth_service.jwt_codec.key_manager
        public_jwks = auth_service.jwt_codec.public_jwks()
        published_kids = sorted(
            kid
            for kid in (
                item.get("kid")
                for item in public_jwks.get("keys", [])
                if isinstance(item, dict)
            )
            if isinstance(kid, str) and kid
        )
        verification_algorithms = sorted(
            {
                *(item.algorithm for item in key_manager.verification_keys),
                *(() if key_manager.hs256_secret is None else ("HS256",)),
            }
        )
        verification_kids = sorted(
            {
                item.kid
                for item in key_manager.verification_keys
                if isinstance(item.kid, str) and item.kid
            }
        )
        return InternalAuthDiagnosticsResponse(
            auth_enabled=True,
            environment=settings.environment,
            issuer=auth_service.jwt_codec.issuer,
            asymmetric_required=settings.auth_require_asymmetric_tokens,
            signing_algorithm=key_manager.signing_algorithm,
            active_kid=key_manager.signing_kid,
            hs256_fallback_enabled=key_manager.hs256_secret is not None,
            signing_material_source=_jwt_text_source(
                inline_value=settings.auth_jwt_private_key_pem,
                file_path=settings.auth_jwt_private_key_path,
                secret_version=settings.auth_jwt_private_key_secret_version,
            ),
            verification_jwks_source=_jwt_verification_source(
                inline_value=settings.auth_jwt_verification_jwks,
                file_path=settings.auth_jwt_verification_jwks_path,
                secret_version=settings.auth_jwt_verification_jwks_secret_version,
                rs256_enabled=key_manager.rs256_enabled,
            ),
            verification_algorithms=verification_algorithms,
            verification_kids=verification_kids,
            published_jwks_kids=published_kids,
        )

    def _internal_intent_tags_classifier_diagnostics() -> InternalIntentTagsClassifierDiagnosticsResponse:
        organization_ids: list[str] = []
        if identity_store is not None:
            organization_ids.extend(
                organization.organization_id
                for organization in identity_store.list_organizations()
            )
        deduped_organization_ids = list(dict.fromkeys(organization_ids))

        active_profiles = []
        recent_events = []
        if intent_tags_runtime is not None:
            for organization_id in deduped_organization_ids:
                active_profiles.extend(
                    intent_tags_runtime.profile_service.list_profiles(
                        organization_id,
                        is_active=True,
                    )
                )
                recent_events.extend(
                    intent_tags_runtime.store.list_classification_events(
                        organization_id,
                        limit=50,
                    )
                )
        recent_events.sort(key=lambda item: (item.created_at, item.classification_event_id), reverse=True)
        recent_events = recent_events[:200]

        adapter_counts: Counter[str] = Counter()
        for profile in active_profiles:
            adapter_counts[profile.adapter_name] += 1

        recent_model_counts: Counter[str] = Counter()
        recent_failure_category_counts: Counter[str] = Counter()
        recent_hosted_event_count = 0
        recent_fallback_count = 0
        for event in recent_events:
            recent_model_counts[event.model_version] += 1
            metadata = event.context_payload.get("classifier_metadata")
            if not isinstance(metadata, dict):
                continue
            if metadata.get("driver") == "hosted_http":
                recent_hosted_event_count += 1
            if metadata.get("fallback_applied"):
                recent_fallback_count += 1
                failure = metadata.get("fallback_reason")
                if isinstance(failure, dict):
                    category = failure.get("category")
                    if isinstance(category, str) and category.strip():
                        recent_failure_category_counts[category.strip()] += 1

        cost_records = []
        if provider_cost_store is not None:
            cost_records = provider_cost_store.list_records(
                provider="intent_tags_classifier",
                limit=200,
            )
        cost_type_counts: Counter[str] = Counter()
        cost_total_usd = 0.0
        for record in cost_records:
            cost_type_counts[record.cost_type] += 1
            cost_total_usd += float(record.amount_usd)
        # Background workers run in the worker process (ruhu.worker); status
        # is derived from their recurring tick jobs, not in-process threads.
        webhook_tick_status = recurring_tick_status(jobs_store, WEBHOOK_DISPATCH_JOB_TYPE)
        sweep_tick_status = recurring_tick_status(jobs_store, SWEEP_JOB_TYPE)
        sentiment_tick_status = recurring_tick_status(jobs_store, SENTIMENT_JOB_TYPE)

        return InternalIntentTagsClassifierDiagnosticsResponse(
            runtime_enabled=intent_tags_runtime is not None,
            hosted_classifier_enabled=bool(settings.intent_tags_classifier_base_url),
            hosted_base_url=settings.intent_tags_classifier_base_url,
            hosted_api_key_source=_classifier_api_key_source(
                inline_value=settings.intent_tags_classifier_api_key,
                secret_version=settings.intent_tags_classifier_api_key_secret_version,
            ),
            hosted_timeout_seconds=settings.intent_tags_classifier_timeout_seconds,
            hosted_max_retries=settings.intent_tags_classifier_max_retries,
            hosted_retry_backoff_seconds=(
                settings.intent_tags_classifier_retry_backoff_seconds
            ),
            default_interpreter_name=settings.interpreter_name,
            agent_interpreters=dict(settings.agent_interpreters),
            active_profile_count=len(active_profiles),
            active_profile_adapter_counts=dict(adapter_counts),
            recent_event_count=len(recent_events),
            recent_hosted_event_count=recent_hosted_event_count,
            recent_fallback_count=recent_fallback_count,
            recent_model_counts=dict(recent_model_counts),
            recent_failure_category_counts=dict(recent_failure_category_counts),
            recent_cost_record_count=len(cost_records),
            recent_cost_total_usd=round(cost_total_usd, 6),
            recent_cost_type_counts=dict(cost_type_counts),
            semantic_summary_webhook_worker_enabled=(
                settings.semantic_summary_webhook_worker_enabled
            ),
            semantic_summary_webhook_interval_seconds=(
                settings.semantic_summary_webhook_interval_seconds
            ),
            semantic_summary_webhook_batch_size=(
                settings.semantic_summary_webhook_batch_size
            ),
            semantic_summary_webhook_worker_running=(
                webhook_tick_status.scheduled
            ),
            semantic_summary_webhook_worker_last_error=(
                webhook_tick_status.last_error
            ),
            semantic_summary_webhook_worker_last_result=(
                webhook_tick_status.model_dump()
            ),
            conversation_sweep_worker_enabled=(
                settings.conversation_sweep_worker_enabled
            ),
            conversation_sweep_interval_seconds=(
                settings.conversation_sweep_interval_seconds
            ),
            conversation_sweep_idle_timeout_seconds=(
                settings.conversation_sweep_idle_timeout_seconds
            ),
            conversation_sweep_batch_size=(
                settings.conversation_sweep_batch_size
            ),
            conversation_sweep_worker_running=sweep_tick_status.scheduled,
            conversation_sweep_worker_last_error=sweep_tick_status.last_error,
            conversation_sweep_worker_last_result=sweep_tick_status.model_dump(),
            sentiment_worker_enabled=settings.sentiment_worker_enabled,
            sentiment_worker_running=sentiment_tick_status.scheduled,
            sentiment_worker_last_error=sentiment_tick_status.last_error,
            sentiment_worker_last_result=sentiment_tick_status.model_dump(),
        )

    @router.get("/internal/platform/health", response_model=InternalPlatformHealthResponse)
    def get_internal_platform_health(
        context: RequestAuthContext = Depends(_require_internal_superuser),
    ) -> InternalPlatformHealthResponse:
        del context
        email_transport: Literal["smtp", "dev_outbox", "none"] = "none"
        email_retry_enabled = False
        if email_sender is not None:
            if isinstance(email_sender, DevOutboxEmailSender):
                email_transport = "dev_outbox"
            else:
                email_transport = "smtp"
            email_retry_enabled = isinstance(email_sender, RetryingEmailSender)
        return InternalPlatformHealthResponse(
            status="ok" if auth_enabled else "degraded",
            auth_enabled=auth_enabled,
            runtime_database_configured=settings.database_url is not None,
            auth_database_configured=settings.auth_database_url is not None,
            email_transport=email_transport,
            email_retry_enabled=email_retry_enabled,
            organization_count=len(identity_store.list_organizations()),
            user_count=len(identity_store.list_users()),
        )

    @router.get("/internal/auth/diagnostics", response_model=InternalAuthDiagnosticsResponse)
    def get_internal_auth_diagnostics(
        context: RequestAuthContext = Depends(_require_internal_superuser),
    ) -> InternalAuthDiagnosticsResponse:
        del context
        return _internal_auth_diagnostics()

    @router.get(
        "/internal/intent-tags/classifier/diagnostics",
        response_model=InternalIntentTagsClassifierDiagnosticsResponse,
    )
    def get_internal_intent_tags_classifier_diagnostics(
        context: RequestAuthContext = Depends(_require_internal_superuser),
    ) -> InternalIntentTagsClassifierDiagnosticsResponse:
        del context
        return _internal_intent_tags_classifier_diagnostics()

    @router.get("/internal/organizations", response_model=list[InternalOrganizationSummary])
    def list_internal_organizations(
        context: RequestAuthContext = Depends(_require_internal_superuser),
    ) -> list[InternalOrganizationSummary]:
        del context
        organizations = identity_store.list_organizations()
        return [
            InternalOrganizationSummary(
                organization_id=organization.organization_id,
                slug=organization.slug,
                name=organization.name,
                is_active=organization.is_active and organization.deleted_at is None,
                member_count=len(identity_store.list_organization_members(organization.organization_id)),
                created_at=organization.created_at,
            )
            for organization in organizations
        ]

    @router.get("/internal/users", response_model=list[InternalUserSummary])
    def list_internal_users(
        context: RequestAuthContext = Depends(_require_internal_superuser),
    ) -> list[InternalUserSummary]:
        del context
        return [_build_internal_user_summary(user) for user in identity_store.list_users()]

    @router.get("/internal/users/{user_id}/external-identities", response_model=list[ExternalIdentitySummary])
    def list_internal_user_external_identities(
        user_id: str,
        context: RequestAuthContext = Depends(_require_internal_superuser),
    ) -> list[ExternalIdentitySummary]:
        del context
        user = identity_store.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="unknown user")
        return [
            _build_external_identity_summary(identity)
            for identity in identity_store.list_external_identities_for_user(user_id)
        ]

    @router.post("/internal/users/{user_id}/promote-superuser", response_model=InternalUserSummary)
    def promote_internal_user_to_superuser(
        user_id: str,
        context: RequestAuthContext = Depends(_require_internal_superuser),
    ) -> InternalUserSummary:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            user = auth_service.set_user_superuser(
                target_user_id=user_id,
                enabled=True,
                actor_user_id=principal.user.user_id,
            )
        except (AuthenticationError, AuthorizationError, ConflictError) as exc:
            _raise_http_for_auth_error(exc)
        return _build_internal_user_summary(user)

    @router.post("/internal/users/{user_id}/revoke-superuser", response_model=InternalUserSummary)
    def revoke_internal_user_superuser(
        user_id: str,
        context: RequestAuthContext = Depends(_require_internal_superuser),
    ) -> InternalUserSummary:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            user = auth_service.set_user_superuser(
                target_user_id=user_id,
                enabled=False,
                actor_user_id=principal.user.user_id,
            )
        except (AuthenticationError, AuthorizationError, ConflictError) as exc:
            _raise_http_for_auth_error(exc)
        return _build_internal_user_summary(user)

    return router
