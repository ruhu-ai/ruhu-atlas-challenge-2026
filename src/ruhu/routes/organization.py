"""Organization/SSO/members + account-closure routes — extracted from api.py (RP-3.1 step 8a).

Two builders mirror the original layout inside ``create_app()`` (hazard H1 —
both mount under the same guard, ``if auth_enabled and
effective_tenant_identity_repositories is not None and
effective_identity_store is not None:``, at the exact positions the inline
blocks occupied):

- ``build_organization_router`` — /organization profile + auth/revoke-sessions,
  /auth/sso/config, /organization/invitations, /organization/members (incl.
  per-member sessions), and the user avatar pair (/auth/me/avatar behind the
  ``_multipart_support_available()`` check, /auth/avatars/{user_id}).
- ``build_account_closure_router`` — /organization/close-account,
  /organization/reactivate, /organization/confirm-action plus the
  action-token helpers. Mounted AFTER the phone-numbers router, where the
  inline block sat.

The tenant-scoped repository resolver (formerly the ``_tenant_repo_for_context``
closure in ``create_app()``) is built per-router via
``make_tenant_repo_for_context``; the deliver-or-raise email helper is shared
with the magic-link/invitation flows via ``auth_sessions.make_deliver_email``.

The org DTOs and response builders still live in ``ruhu.api`` (they migrate
with the rest of the inline DTO block in a later step), so this module is
imported by ``create_app()`` AT THE MOUNT SITE rather than at api.py's module
top (hazard H7: DTO imports stay at this module's top for PEP 563). No
``tags=`` / ``prefix=`` and unchanged handler names (hazard H1).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)

# DTOs and response builders at module top (hazard H7: PEP 563 annotations
# resolve against this module's globals).
from ..api import (
    CloseAccountRequest,
    ClosureStatusResponse,
    ConfirmActionRequest,
    CreateOrganizationInvitationRequest,
    CreateOrganizationInvitationResponse,
    CreateOrganizationMemberRequest,
    EnterpriseSSOConfigResponse,
    EnterpriseSSOConfigUpsertRequest,
    MeResponse,
    OrganizationInvitationResponse,
    OrganizationMemberResponse,
    OrganizationProfileResponse,
    OrganizationSessionRevocationResponse,
    SessionResponse,
    UpdateOrganizationMemberRequest,
    UpdateOrganizationRequest,
    _RESERVED_ORGANIZATION_SETTINGS_KEYS,
    _build_created_organization_invitation_response,
    _build_enterprise_sso_config_response,
    _build_me_response,
    _build_organization_invitation_response,
    _build_organization_member_response,
    _build_organization_profile_response,
    _build_organization_session_revocation_response,
    _build_session_response,
    _multipart_support_available,
    _raise_http_for_auth_error,
    _resolve_public_auth_base_url,
)
from ..api_auth import RequestAuthContext, require_authenticated_context
from ..audit.events import ADMIN_ROLE_CHANGED, ADMIN_USER_REMOVED
from ..auth import AuthenticationError, AuthorizationError, ConflictError
from ..email_templates import (
    render_close_account_email,
    render_organization_invitation_email,
    render_reactivate_account_email,
)
from ..email_transport import EmailMessage, EmailTransportError
from ..external_auth import ExternalAuthError, fetch_discovery
from ..identity import OrganizationMemberRecord, OrganizationMembership
from ..notifications.service import emit_notification
from ..policy import require_account_owner, require_organization_role
from ..session_http import clear_auth_cookies, request_uses_secure_cookies
from .auth_sessions import make_deliver_email

if TYPE_CHECKING:
    from ..auth import AuthService
    from ..email_transport import EmailSender
    from ..identity import IdentityStore
    from ..runtime_config import RuntimeSettings


def make_tenant_repo_for_context(
    tenant_identity_repositories,
) -> Callable[[RequestAuthContext], object]:
    """Tenant-scoped identity-repository resolver (formerly a closure in
    ``create_app()``). Shared by both builders in this module."""

    def _tenant_repo_for_context(context: RequestAuthContext):
        principal = context.principal
        if principal is None or tenant_identity_repositories is None:
            raise HTTPException(status_code=500, detail="tenant repository unavailable")
        return tenant_identity_repositories.for_scope(
            organization_id=principal.organization.organization_id,
        )

    return _tenant_repo_for_context


def build_organization_router(
    *,
    auth_service: "AuthService",
    identity_store: "IdentityStore",
    tenant_identity_repositories,
    settings: "RuntimeSettings",
    email_sender: "EmailSender | None",
    notification_store,
    auth_session_factory,
    emit_semantic_audit_event: Callable[..., None],
) -> APIRouter:
    """Build the organization profile/SSO/invitations/members router."""
    router = APIRouter()
    _deliver_email = make_deliver_email(email_sender)
    _tenant_repo_for_context = make_tenant_repo_for_context(tenant_identity_repositories)

    def _count_account_owners(context: RequestAuthContext) -> int:
        repo = _tenant_repo_for_context(context)
        return sum(1 for member in repo.list_organization_members() if member.membership.is_account_owner)

    @router.get("/organization", response_model=OrganizationProfileResponse)
    def get_organization_profile(
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> OrganizationProfileResponse:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        repo = _tenant_repo_for_context(context)
        organization = repo.get_organization()
        if organization is None:
            raise HTTPException(status_code=404, detail="unknown organization")
        refreshed_principal = principal.model_copy(update={"organization": organization})
        return _build_organization_profile_response(
            principal=refreshed_principal,
            settings=dict(organization.settings),
            metadata=dict(organization.metadata),
        )

    @router.patch("/organization", response_model=OrganizationProfileResponse)
    def update_organization_profile(
        payload: UpdateOrganizationRequest,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> OrganizationProfileResponse:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        repo = _tenant_repo_for_context(context)
        organization = repo.get_organization()
        if organization is None:
            raise HTTPException(status_code=404, detail="unknown organization")
        updates = payload.model_dump(exclude_none=True)
        settings_updates = updates.get("settings")
        if isinstance(settings_updates, dict):
            reserved_keys = sorted(_RESERVED_ORGANIZATION_SETTINGS_KEYS.intersection(settings_updates))
            if reserved_keys:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "reserved organization security settings must be changed through "
                        "/organization/auth/revoke-sessions"
                    ),
                )
        updated_organization = repo.save_organization(organization.model_copy(update=updates))
        refreshed_principal = principal.model_copy(update={"organization": updated_organization})
        return _build_organization_profile_response(
            principal=refreshed_principal,
            settings=dict(updated_organization.settings),
            metadata=dict(updated_organization.metadata),
        )

    @router.post("/organization/auth/revoke-sessions", response_model=OrganizationSessionRevocationResponse)
    def revoke_organization_sessions(
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> OrganizationSessionRevocationResponse:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        updated_organization = auth_service.revoke_organization_sessions(
            organization_id=principal.organization.organization_id,
        )
        return _build_organization_session_revocation_response(updated_organization)

    @router.get("/auth/sso/config", response_model=EnterpriseSSOConfigResponse | None)
    def get_enterprise_sso_config(
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> EnterpriseSSOConfigResponse | None:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        configuration = auth_service.get_enterprise_sso_configuration_for_organization(
            principal.organization.organization_id
        )
        if configuration is None:
            return None
        return _build_enterprise_sso_config_response(configuration)

    @router.put("/auth/sso/config", response_model=EnterpriseSSOConfigResponse)
    async def upsert_enterprise_sso_config(
        payload: EnterpriseSSOConfigUpsertRequest,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> EnterpriseSSOConfigResponse:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            await fetch_discovery(payload.issuer_url)
        except ExternalAuthError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid OIDC issuer: {str(exc)}") from exc

        requested_domains = {item.strip().lower() for item in payload.allowed_domains if item.strip()}
        for configuration in identity_store.list_enterprise_sso_configurations():
            if configuration.organization_id == principal.organization.organization_id:
                continue
            if not configuration.is_active:
                continue
            existing_domains = {item.strip().lower() for item in configuration.allowed_domains if item.strip()}
            overlap = sorted(requested_domains.intersection(existing_domains))
            if overlap:
                raise HTTPException(
                    status_code=409,
                    detail=f"Domain already claimed by another organization: {overlap[0]}",
                )

        try:
            configuration = auth_service.save_enterprise_sso_configuration(
                organization_id=principal.organization.organization_id,
                issuer_url=payload.issuer_url,
                client_id=payload.client_id,
                client_secret_ref=payload.client_secret_ref,
                allowed_domains=list(requested_domains),
                scopes=payload.scopes,
                is_active=payload.is_active,
                enforce_sso=payload.enforce_sso,
                jit_provisioning_enabled=payload.jit_provisioning_enabled,
            )
        except (AuthenticationError, AuthorizationError, ConflictError) as exc:
            _raise_http_for_auth_error(exc)
        return _build_enterprise_sso_config_response(configuration)

    @router.delete("/auth/sso/config", status_code=status.HTTP_204_NO_CONTENT)
    def disable_enterprise_sso_config(
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> Response:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        auth_service.disable_enterprise_sso_configuration(
            organization_id=principal.organization.organization_id
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/organization/invitations", response_model=list[OrganizationInvitationResponse])
    def list_organization_invitations(
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> list[OrganizationInvitationResponse]:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        now = auth_service.now_provider()
        invitations = auth_service.list_organization_invitations(
            organization_id=principal.organization.organization_id,
        )
        return [_build_organization_invitation_response(invitation, now=now) for invitation in invitations]

    @router.post("/organization/invitations", response_model=CreateOrganizationInvitationResponse)
    def create_organization_invitation(
        payload: CreateOrganizationInvitationRequest,
        request: Request,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> CreateOrganizationInvitationResponse:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        if payload.is_account_owner and not principal.is_account_owner:
            raise HTTPException(status_code=403, detail="account owner permission required")
        try:
            issued = auth_service.create_organization_invitation(
                organization_id=principal.organization.organization_id,
                email=payload.email,
                role=payload.role,
                is_account_owner=payload.is_account_owner,
                invited_by_user_id=principal.user.user_id,
            )
        except (AuthenticationError, AuthorizationError, ConflictError) as exc:
            _raise_http_for_auth_error(exc)
        public_auth_base_url = _resolve_public_auth_base_url(settings=settings)
        rendered_email = render_organization_invitation_email(
            to_email=issued.invitation.email,
            invited_by_name=principal.user.display_name or principal.user.email,
            organization_name=principal.organization.name,
            invitation_url=f"{public_auth_base_url}/accept-invitation?token={issued.invitation_token}",
            role=issued.invitation.role,
        )
        try:
            delivery = _deliver_email(
                EmailMessage(
                    to_email=issued.invitation.email,
                    subject=rendered_email.subject,
                    html_content=rendered_email.html,
                    text_content=rendered_email.text,
                    metadata={
                        "kind": "organization_invitation",
                        "invitation_id": issued.invitation.invitation_id,
                        "organization_id": issued.invitation.organization_id,
                    },
                )
            )
        except HTTPException:
            auth_service.revoke_organization_invitation(
                invitation_id=issued.invitation.invitation_id,
                organization_id=principal.organization.organization_id,
                revoked_by_user_id=principal.user.user_id,
            )
            raise
        return _build_created_organization_invitation_response(
            issued,
            now=auth_service.now_provider(),
            delivery=delivery,
        )

    @router.delete("/organization/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
    def revoke_organization_invitation(
        invitation_id: str,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> Response:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        invitation = identity_store.get_organization_invitation(invitation_id)
        if invitation is None or invitation.organization_id != principal.organization.organization_id:
            raise HTTPException(status_code=404, detail="unknown invitation")
        if invitation.is_account_owner and not principal.is_account_owner:
            raise HTTPException(status_code=403, detail="account owner permission required")
        try:
            auth_service.revoke_organization_invitation(
                invitation_id=invitation_id,
                organization_id=principal.organization.organization_id,
                revoked_by_user_id=principal.user.user_id,
            )
        except (AuthenticationError, AuthorizationError, ConflictError) as exc:
            _raise_http_for_auth_error(exc)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/organization/members", response_model=list[OrganizationMemberResponse])
    def list_organization_members(
        context: RequestAuthContext = Depends(require_organization_role("analyst")),
    ) -> list[OrganizationMemberResponse]:
        repo = _tenant_repo_for_context(context)
        return [_build_organization_member_response(record) for record in repo.list_organization_members()]

    @router.get("/organization/members/{user_id}/sessions", response_model=list[SessionResponse])
    def list_member_sessions(
        user_id: str,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> list[SessionResponse]:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        repo = _tenant_repo_for_context(context)
        membership = repo.get_organization_membership(user_id)
        if membership is None:
            raise HTTPException(status_code=404, detail="unknown organization member")
        sessions = auth_service.list_user_sessions(
            user_id=user_id,
            organization_id=principal.organization.organization_id,
        )
        return [
            _build_session_response(session, current_session_id=principal.session.session_id)
            for session in sessions
        ]

    @router.delete("/organization/members/{user_id}/sessions", status_code=status.HTTP_204_NO_CONTENT)
    def revoke_member_sessions(
        user_id: str,
        request: Request,
        response: Response,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> Response:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        repo = _tenant_repo_for_context(context)
        membership = repo.get_organization_membership(user_id)
        if membership is None:
            raise HTTPException(status_code=404, detail="unknown organization member")
        try:
            revoked_sessions = auth_service.revoke_user_sessions(
                user_id=user_id,
                organization_id=principal.organization.organization_id,
            )
        except (AuthenticationError, AuthorizationError, ConflictError) as exc:
            _raise_http_for_auth_error(exc)
        if any(session.session_id == principal.session.session_id for session in revoked_sessions):
            clear_auth_cookies(response, secure=request_uses_secure_cookies(request))
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    @router.post("/organization/members", response_model=OrganizationMemberResponse)
    def add_organization_member(
        payload: CreateOrganizationMemberRequest,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> OrganizationMemberResponse:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        if not payload.user_id and not payload.email:
            raise HTTPException(status_code=400, detail="user_id or email is required")
        if payload.is_account_owner and not principal.is_account_owner:
            raise HTTPException(status_code=403, detail="account owner permission required")
        repo = _tenant_repo_for_context(context)
        if payload.user_id:
            user = identity_store.get_user(payload.user_id)
        else:
            user = identity_store.get_user_by_email(payload.email or "")
        if user is None:
            raise HTTPException(status_code=404, detail="unknown user")
        existing_membership = repo.get_organization_membership(user.user_id)
        if existing_membership is not None:
            raise HTTPException(status_code=409, detail="user is already a member of the organization")
        membership = repo.add_organization_membership(
            OrganizationMembership(
                user_id=user.user_id,
                organization_id=principal.organization.organization_id,
                role=payload.role,
                is_account_owner=payload.is_account_owner,
            )
        )
        return _build_organization_member_response(
            OrganizationMemberRecord(user=user, membership=membership),
        )

    @router.patch("/organization/members/{user_id}", response_model=OrganizationMemberResponse)
    def update_organization_member(
        user_id: str,
        payload: UpdateOrganizationMemberRequest,
        request: Request,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> OrganizationMemberResponse:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        if payload.role is None and payload.is_account_owner is None:
            raise HTTPException(status_code=400, detail="at least one membership field must be provided")
        repo = _tenant_repo_for_context(context)
        existing = repo.get_organization_membership(user_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="unknown organization member")
        next_is_account_owner = (
            existing.is_account_owner if payload.is_account_owner is None else payload.is_account_owner
        )
        if next_is_account_owner != existing.is_account_owner:
            if not principal.is_account_owner:
                raise HTTPException(status_code=403, detail="account owner permission required")
            if existing.is_account_owner and not next_is_account_owner and _count_account_owners(context) <= 1:
                raise HTTPException(
                    status_code=409,
                    detail="organization must retain at least one account owner",
                )
        updated = repo.add_organization_membership(
            existing.model_copy(
                update={
                    "role": existing.role if payload.role is None else payload.role,
                    "is_account_owner": next_is_account_owner,
                }
            )
        )
        user = identity_store.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="unknown user")
        if payload.role is not None and payload.role != existing.role:
            emit_notification(
                notification_store,
                organization_id=principal.organization.organization_id,
                category="auth.role_changed",
                title=f"Your role was changed to {updated.role}",
                level="info",
                urgency="soon",
                user_id=user_id,
                source_type="user",
                source_id=user_id,
                payload={"user_id": user_id, "old_role": existing.role, "new_role": updated.role},
            )
            emit_semantic_audit_event(
                request=request,
                event_type=ADMIN_ROLE_CHANGED,
                organization_id=principal.organization.organization_id,
                actor_id=principal.user.user_id,
                actor_session_id=principal.session.session_id,
                resource_type="organization_membership",
                resource_id=user_id,
                detail={
                    "target_user_id": user_id,
                    "old_role": existing.role,
                    "new_role": updated.role,
                    "was_account_owner": existing.is_account_owner,
                    "is_account_owner": updated.is_account_owner,
                },
            )
        return _build_organization_member_response(
            OrganizationMemberRecord(user=user, membership=updated),
        )

    @router.delete("/organization/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
    def remove_organization_member(
        user_id: str,
        request: Request,
        context: RequestAuthContext = Depends(require_organization_role("admin")),
    ) -> Response:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        repo = _tenant_repo_for_context(context)
        membership = repo.get_organization_membership(user_id)
        if membership is None:
            raise HTTPException(status_code=404, detail="unknown organization member")
        if membership.is_account_owner:
            if not principal.is_account_owner:
                raise HTTPException(status_code=403, detail="account owner permission required")
            if _count_account_owners(context) <= 1:
                raise HTTPException(
                    status_code=409,
                    detail="organization must retain at least one account owner",
                )
        repo.remove_organization_membership(user_id)
        emit_semantic_audit_event(
            request=request,
            event_type=ADMIN_USER_REMOVED,
            organization_id=principal.organization.organization_id,
            actor_id=principal.user.user_id,
            actor_session_id=principal.session.session_id,
            resource_type="organization_membership",
            resource_id=user_id,
            detail={
                "target_user_id": user_id,
                "removed_role": membership.role,
                "removed_account_owner": membership.is_account_owner,
            },
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ── Avatar upload ──────────────────────────────────────────────────

    if _multipart_support_available():

        @router.post("/auth/me/avatar", response_model=MeResponse)
        async def upload_avatar(
            file: UploadFile = File(...),
            context: RequestAuthContext = Depends(require_authenticated_context),
        ) -> MeResponse:
            principal = context.principal
            if principal is None:
                raise HTTPException(status_code=401, detail="authentication required")
            content_type = (file.content_type or "").split(";")[0].strip().lower()
            if not content_type.startswith("image/"):
                raise HTTPException(status_code=422, detail="file must be an image")
            data = await file.read()
            if len(data) > 2 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="image must be smaller than 2 MB")
            now = datetime.now(timezone.utc)
            with auth_session_factory.begin() as session:
                from ..db_models import UserAvatarBlobRecord
                record = session.get(UserAvatarBlobRecord, principal.user.user_id)
                if record is None:
                    record = UserAvatarBlobRecord(user_id=principal.user.user_id)
                    session.add(record)
                record.content_type = content_type
                record.data = data
                record.updated_at = now
            avatar_url = f"/auth/avatars/{principal.user.user_id}"
            updated_user = auth_service.update_user_profile(
                user_id=principal.user.user_id,
                avatar_url=avatar_url,
            )
            refreshed = principal.model_copy(update={"user": updated_user})
            return _build_me_response(refreshed)

    @router.get("/auth/avatars/{user_id}")
    def get_avatar(user_id: str) -> Response:
        from ..db_models import UserAvatarBlobRecord
        with auth_session_factory() as session:
            record = session.get(UserAvatarBlobRecord, user_id)
        if record is None:
            raise HTTPException(status_code=404, detail="no avatar")
        return Response(content=record.data, media_type=record.content_type)

    return router


def build_account_closure_router(
    *,
    auth_service: "AuthService",
    tenant_identity_repositories,
    settings: "RuntimeSettings",
    email_sender: "EmailSender | None",
) -> APIRouter:
    """Build the /organization/close-account / reactivate / confirm-action
    router. CloseAccountRequest / ClosureStatusResponse / ConfirmActionRequest
    are defined at ``ruhu.api`` module scope so Pydantic v2's OpenAPI schema
    generator can resolve their ForwardRefs."""
    router = APIRouter()
    _deliver_email = make_deliver_email(email_sender)
    _tenant_repo_for_context = make_tenant_repo_for_context(tenant_identity_repositories)

    _CLOSURE_GRACE_DAYS = 30

    def _closure_action_token(
        *,
        user_id: str,
        org_id: str,
        org_name: str,
        action: str,
        reason: str | None = None,
    ) -> str:
        import jwt as _jwt
        now = datetime.now(timezone.utc)
        exp = int((now + timedelta(minutes=15)).timestamp())
        payload = {
            "sub": user_id,
            "org_id": org_id,
            "org_name": org_name,
            "action": action,
            "reason": reason,
            "exp": exp,
            "type": "action_confirm",
            "jti": str(uuid4()),
        }
        key, algorithm, headers = auth_service.jwt_codec.key_manager.signing_params()
        headers = {"typ": "JWT", **(headers or {})}
        return _jwt.encode(payload, key, algorithm=algorithm, headers=headers)

    def _verify_closure_action_token(token: str) -> dict:
        try:
            payload = auth_service.jwt_codec._decode_payload(token)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid or expired action token") from exc
        if payload.get("type") != "action_confirm":
            raise HTTPException(status_code=400, detail="invalid token type")
        return payload

    @router.post("/organization/close-account", response_model=ClosureStatusResponse)
    def close_account(
        payload: CloseAccountRequest,
        request: Request,
        context: RequestAuthContext = Depends(require_account_owner),
    ) -> ClosureStatusResponse:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        repo = _tenant_repo_for_context(context)
        organization = repo.get_organization()
        if organization is None:
            raise HTTPException(status_code=404, detail="unknown organization")
        if payload.confirm_org_name != organization.name:
            raise HTTPException(status_code=422, detail="organization name does not match")
        if organization.deletion_state not in ("active", "cancelled"):
            raise HTTPException(
                status_code=409,
                detail=f"organization is already in deletion state: {organization.deletion_state}",
            )
        token = _closure_action_token(
            user_id=principal.user.user_id,
            org_id=organization.organization_id,
            org_name=organization.name,
            action="close_account",
            reason=payload.reason,
        )
        public_auth_base_url = _resolve_public_auth_base_url(settings=settings)
        confirm_url = f"{public_auth_base_url}/confirm-action?token={token}"
        rendered = render_close_account_email(
            to_email=principal.user.email,
            organization_name=organization.name,
            confirm_url=confirm_url,
        )
        try:
            _deliver_email(
                EmailMessage(
                    to_email=principal.user.email,
                    subject=rendered.subject,
                    html_content=rendered.html,
                    text_content=rendered.text,
                )
            )
        except EmailTransportError:
            pass
        return ClosureStatusResponse(
            organization_id=organization.organization_id,
            deletion_state=organization.deletion_state,
            message="Confirmation email sent. Click the link to confirm closure.",
            status="confirmation_sent",
        )

    @router.post("/organization/reactivate", response_model=ClosureStatusResponse)
    def reactivate_account(
        request: Request,
        context: RequestAuthContext = Depends(require_account_owner),
    ) -> ClosureStatusResponse:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        repo = _tenant_repo_for_context(context)
        organization = repo.get_organization()
        if organization is None:
            raise HTTPException(status_code=404, detail="unknown organization")
        if organization.deletion_state != "scheduled":
            raise HTTPException(status_code=409, detail="organization is not scheduled for deletion")
        token = _closure_action_token(
            user_id=principal.user.user_id,
            org_id=organization.organization_id,
            org_name=organization.name,
            action="reactivate",
        )
        public_auth_base_url = _resolve_public_auth_base_url(settings=settings)
        confirm_url = f"{public_auth_base_url}/confirm-action?token={token}"
        rendered = render_reactivate_account_email(
            to_email=principal.user.email,
            organization_name=organization.name,
            confirm_url=confirm_url,
        )
        try:
            _deliver_email(
                EmailMessage(
                    to_email=principal.user.email,
                    subject=rendered.subject,
                    html_content=rendered.html,
                    text_content=rendered.text,
                )
            )
        except EmailTransportError:
            pass
        return ClosureStatusResponse(
            organization_id=organization.organization_id,
            deletion_state=organization.deletion_state,
            message="Confirmation email sent. Click the link to confirm reactivation.",
            status="confirmation_sent",
        )

    @router.post("/organization/confirm-action", response_model=ClosureStatusResponse)
    def confirm_account_action(
        payload: ConfirmActionRequest,
        context: RequestAuthContext = Depends(require_authenticated_context),
    ) -> ClosureStatusResponse:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        claims = _verify_closure_action_token(payload.token)
        action = claims.get("action")
        org_id = claims.get("org_id")
        if org_id != principal.organization.organization_id:
            raise HTTPException(status_code=403, detail="token is for a different organization")
        repo = _tenant_repo_for_context(context)
        organization = repo.get_organization()
        if organization is None:
            raise HTTPException(status_code=404, detail="unknown organization")
        now = datetime.now(timezone.utc)
        if action == "close_account":
            if organization.deletion_state not in ("active", "cancelled"):
                raise HTTPException(status_code=409, detail="organization is not eligible for closure")
            scheduled_for = now + timedelta(days=_CLOSURE_GRACE_DAYS)
            updated = repo.save_organization(organization.model_copy(update={
                "deletion_state": "scheduled",
                "deletion_scheduled_for": scheduled_for,
                "deletion_requested_at": now,
                "deletion_requested_by": principal.user.user_id,
            }))
            return ClosureStatusResponse(
                organization_id=updated.organization_id,
                deletion_state=updated.deletion_state,
                deletion_scheduled_for=updated.deletion_scheduled_for,
                message=f"Account closure confirmed. Data will be deleted on {scheduled_for.strftime('%B %-d, %Y')}.",
                status="confirmed",
            )
        elif action == "reactivate":
            if organization.deletion_state != "scheduled":
                raise HTTPException(status_code=409, detail="organization is not scheduled for deletion")
            updated = repo.save_organization(organization.model_copy(update={
                "deletion_state": "cancelled",
                "deletion_scheduled_for": None,
            }))
            return ClosureStatusResponse(
                organization_id=updated.organization_id,
                deletion_state=updated.deletion_state,
                message="Account reactivated. Scheduled deletion has been cancelled.",
                status="confirmed",
            )
        else:
            raise HTTPException(status_code=400, detail=f"unknown action: {action}")

    return router
