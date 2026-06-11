"""Auth pages + session/OAuth/me routes — extracted from api.py (RP-3.1 step 7).

Three builders mirror the original conditional structure in create_app()
(hazard H1 — the mounts must reproduce BOTH guards exactly):

- ``build_auth_sessions_router`` — the auth HTML pages (/login, /signup,
  /accept-invitation, /auth/magic-link, /auth/callback, /app, /account,
  /internal/admin) plus the session/magic-link/OAuth/invitation routes.
  Mounted under ``if auth_resolver is not None and effective_auth_service
  is not None:``.
- ``build_auth_me_router`` — GET /auth/me only. Registered UNCONDITIONALLY
  (outside both guards), exactly like the original inline route.
- ``build_auth_profile_router`` — PATCH /auth/me + GET
  /auth/external-identities. Mounted under ``if auth_enabled and
  effective_auth_service is not None and effective_identity_store is not
  None:``.

The auth DTOs and response builders still live in ``ruhu.api`` (they migrate
with the rest of the inline DTO block in a later step), so this module is
imported by ``create_app()`` AT THE MOUNT SITE rather than at api.py's module
top (hazard H7: DTO imports stay at this module's top for PEP 563).
``make_deliver_email`` is module-level because the organization/closure block
still inline in api.py shares the same email-delivery helper; api.py rebinds
``_deliver_email`` from it. No ``tags=`` / ``prefix=`` and unchanged handler
names (hazard H1).
"""
from __future__ import annotations

import os
import secrets
from typing import TYPE_CHECKING, Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

# DTOs and response builders at module top (hazard H7: PEP 563 annotations
# resolve against this module's globals).
from ..api import (
    AcceptInvitationRequest,
    ExternalIdentitySummary,
    InviteValidateResponse,
    LogoutRequest,
    MagicLinkRequest,
    MagicLinkRequestResponse,
    MagicLinkVerifyRequest,
    MeResponse,
    OAuthCallbackRequest,
    OAuthStartRequest,
    OAuthStartResponse,
    RefreshRequest,
    SessionResponse,
    UpdateSelfRequest,
    _build_email_delivery_summary,
    _build_external_identity_summary,
    _build_me_response,
    _build_session_response,
    _generic_magic_link_delivery_summary,
    _raise_http_for_auth_error,
    _remaining_cookie_max_age_seconds,
    _resolve_auth_redirect_origins,
    _resolve_public_auth_base_url,
)
from ..api_auth import (
    RequestAuthContext,
    extract_bearer_token,
    get_request_auth_context,
    require_authenticated_context,
)
from ..audit.events import ADMIN_INVITATION_ACCEPTED
from ..auth import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    ExternalAuthProfile,
)
from ..auth_ui import (
    accept_invitation_page_html,
    app_console_page_html,
    auth_callback_page_html,
    internal_admin_page_html,
    login_page_html,
    magic_link_callback_page_html,
    signup_page_html,
)
from ..email_templates import render_magic_link_email
from ..email_transport import EmailDeliveryResult, EmailMessage, EmailTransportError
from ..external_auth import (
    ExternalAuthError,
    build_authorization_url,
    exchange_code_for_tokens,
    fetch_discovery,
    fetch_userinfo,
    identity_from_claims,
    resolve_enterprise_sso_client_secret,
    resolve_google_credentials,
    validate_redirect_uri,
)
from ..identity import EnterpriseSSOConfiguration
from ..notifications.service import emit_notification
from ..session_http import (
    build_session_audit_context,
    clear_auth_cookies,
    extract_access_token_from_request,
    extract_refresh_token_from_request,
    request_uses_secure_cookies,
    set_auth_cookies,
)

if TYPE_CHECKING:
    from ..auth import AuthService
    from ..email_transport import EmailSender
    from ..identity import IdentityStore
    from ..runtime_config import RuntimeSettings


class ChallengeDemoLoginRequest(BaseModel):
    email: str
    password: str


def make_deliver_email(
    email_sender: "EmailSender | None",
) -> Callable[[EmailMessage], EmailDeliveryResult]:
    """Build the shared deliver-or-raise email helper.

    Module-level (not builder-internal) because the organization invitation
    and account-closure routes still inline in api.py use the same helper;
    create_app() rebinds ``_deliver_email`` from this factory.
    """

    def _deliver_email(message: EmailMessage) -> EmailDeliveryResult:
        if email_sender is None:
            raise HTTPException(status_code=503, detail="email delivery is not configured")
        try:
            return email_sender.send(message)
        except EmailTransportError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return _deliver_email


def build_auth_sessions_router(
    *,
    auth_service: "AuthService",
    identity_store: "IdentityStore",
    settings: "RuntimeSettings",
    email_sender: "EmailSender | None",
    notification_store,
    emit_semantic_audit_event: Callable[..., None],
    demo_agent_seeder: Callable[[str], None] | None = None,
) -> APIRouter:
    """Build the auth pages + session/OAuth/invitation router.

    Mounted only when ``auth_resolver is not None and effective_auth_service
    is not None`` — the same guard the inline block lived under.
    """
    router = APIRouter()
    _deliver_email = make_deliver_email(email_sender)

    @router.get("/login", response_class=HTMLResponse)
    def login_page(request: Request) -> Response:
        context = get_request_auth_context(request)
        if context.principal is not None:
            return RedirectResponse(url="/app", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        return HTMLResponse(login_page_html())

    @router.get("/signup", response_class=HTMLResponse)
    def signup_page(request: Request) -> Response:
        context = get_request_auth_context(request)
        if context.principal is not None:
            return RedirectResponse(url="/app", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        return HTMLResponse(signup_page_html())

    @router.get("/accept-invitation", response_class=HTMLResponse)
    def accept_invitation_page(request: Request) -> Response:
        context = get_request_auth_context(request)
        if context.principal is not None:
            return RedirectResponse(url="/app", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        return HTMLResponse(accept_invitation_page_html())

    @router.get("/auth/magic-link", response_class=HTMLResponse)
    def magic_link_callback_page() -> HTMLResponse:
        return HTMLResponse(magic_link_callback_page_html(success_redirect_path="/app"))

    @router.get("/auth/callback", response_class=HTMLResponse)
    def oauth_callback_page() -> HTMLResponse:
        return HTMLResponse(auth_callback_page_html(success_redirect_path="/app"))

    @router.get("/app", response_class=HTMLResponse)
    def workspace_page(request: Request) -> Response:
        context = get_request_auth_context(request)
        if context.principal is None:
            return RedirectResponse(url="/login", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        return HTMLResponse(app_console_page_html())

    @router.get("/account", response_class=HTMLResponse)
    def account_page(request: Request) -> Response:
        context = get_request_auth_context(request)
        if context.principal is None:
            return RedirectResponse(url="/login", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        return RedirectResponse(url="/app", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    @router.get("/internal/admin", response_class=HTMLResponse)
    def internal_admin_page(request: Request) -> Response:
        context = get_request_auth_context(request)
        principal = context.principal
        if principal is None:
            return RedirectResponse(url="/login", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        if not principal.user.is_superuser:
            return RedirectResponse(url="/app", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        return HTMLResponse(internal_admin_page_html())

    def _respond_with_browser_session(*, request: Request, response: Response, issued) -> MeResponse:
        principal = auth_service.authenticate_access_token(issued.access_token)
        now = auth_service.now_provider()
        set_auth_cookies(
            response,
            access_token=issued.access_token,
            refresh_token=issued.refresh_token,
            access_max_age_seconds=_remaining_cookie_max_age_seconds(
                expires_at=issued.session.expires_at,
                now=now,
            ),
            refresh_max_age_seconds=_remaining_cookie_max_age_seconds(
                expires_at=issued.refresh_family.expires_at,
                now=now,
            ),
            secure=request_uses_secure_cookies(request),
        )
        return _build_me_response(principal)

    @router.post("/auth/challenge-demo/login", response_model=MeResponse)
    def challenge_demo_login(
        payload: ChallengeDemoLoginRequest,
        request: Request,
        response: Response,
    ) -> MeResponse:
        enabled = os.getenv("RUHU_CHALLENGE_DEMO_LOGIN_ENABLED", "").strip().lower()
        expected_password = os.getenv("RUHU_CHALLENGE_DEMO_PASSWORD") or ""
        expected_email = (os.getenv("RUHU_CHALLENGE_DEMO_EMAIL") or "").strip().lower()
        if enabled not in {"1", "true", "yes", "on"} or not expected_password:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

        submitted_email = payload.email.strip().lower()
        password_matches = secrets.compare_digest(payload.password, expected_password)
        email_matches = not expected_email or secrets.compare_digest(submitted_email, expected_email)
        if not password_matches or not email_matches:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid demo credentials",
            )

        try:
            issued = auth_service.issue_demo_browser_session(
                email=submitted_email,
                display_name=os.getenv("RUHU_CHALLENGE_DEMO_DISPLAY_NAME") or "Challenge Judge",
                audit=build_session_audit_context(request),
            )
        except (AuthenticationError, AuthorizationError, ConflictError) as exc:
            _raise_http_for_auth_error(exc)
        if demo_agent_seeder is not None:
            demo_agent_seeder(issued.session.organization_id)
        return _respond_with_browser_session(request=request, response=response, issued=issued)

    def _validated_redirect_uri(redirect_uri: str | None) -> str:
        allowed_origins = _resolve_auth_redirect_origins(settings)
        try:
            return validate_redirect_uri(
                redirect_uri,
                frontend_url=settings.frontend_url,
                allowed_origins=allowed_origins,
                default_path="/auth/callback",
            )
        except ExternalAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/auth/refresh", response_model=MeResponse)
    def refresh_session(
        request: Request,
        response: Response,
        payload: RefreshRequest | None = None,
    ) -> MeResponse:
        refresh_token = extract_refresh_token_from_request(
            request,
            body_refresh_token=None if payload is None else payload.refresh_token,
        )
        if refresh_token is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="refresh token required",
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            issued = auth_service.refresh_browser_session(
                refresh_token,
                audit=build_session_audit_context(request),
            )
        except (AuthenticationError, AuthorizationError, ConflictError) as exc:
            _raise_http_for_auth_error(exc)
        return _respond_with_browser_session(request=request, response=response, issued=issued)

    @router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(
        request: Request,
        response: Response,
        payload: LogoutRequest | None = None,
    ) -> Response:
        access_token = extract_bearer_token(request.headers.get("Authorization"))
        if access_token is None and payload is not None:
            access_token = payload.access_token
        if access_token is None:
            access_token = extract_access_token_from_request(request)

        refresh_token = extract_refresh_token_from_request(
            request,
            body_refresh_token=None if payload is None else payload.refresh_token,
        )

        if refresh_token is not None:
            try:
                auth_service.revoke_browser_session(refresh_token=refresh_token)
            except (AuthenticationError, AuthorizationError):
                pass

        if access_token is not None:
            try:
                auth_service.revoke_browser_session(access_token=access_token)
            except (AuthenticationError, AuthorizationError):
                pass

        clear_auth_cookies(response, secure=request_uses_secure_cookies(request))
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    @router.get("/auth/invite/validate", response_model=InviteValidateResponse)
    def validate_invitation_token(token: str = "") -> InviteValidateResponse:
        safe_token = (token or "").strip()
        if not safe_token:
            return InviteValidateResponse(valid=False)
        try:
            invitation = auth_service.get_organization_invitation_by_token(safe_token)
        except (AuthenticationError, AuthorizationError, ConflictError):
            return InviteValidateResponse(valid=False)
        organization = identity_store.get_organization(invitation.organization_id)
        invited_by_name: str | None = None
        if invitation.invited_by_user_id:
            invited_by = identity_store.get_user(invitation.invited_by_user_id)
            if invited_by is not None:
                invited_by_name = invited_by.display_name or invited_by.email
        return InviteValidateResponse(
            valid=True,
            email=invitation.email,
            expires_at=invitation.expires_at,
            organization_name=None if organization is None else organization.name,
            invited_by_name=invited_by_name,
            role=invitation.role,
            is_account_owner=invitation.is_account_owner,
        )

    @router.post("/auth/magic-link/request", response_model=MagicLinkRequestResponse)
    def request_magic_link(
        payload: MagicLinkRequest,
        request: Request,
    ) -> MagicLinkRequestResponse:
        try:
            issued = auth_service.request_magic_link(
                email=payload.email,
                organization_id=payload.organization_id,
                invitation_token=payload.invitation_token,
            )
        except (AuthenticationError, AuthorizationError, ConflictError):
            return MagicLinkRequestResponse(
                message="If the sign-in request is valid, a sign-in link has been issued.",
                delivery=_generic_magic_link_delivery_summary(email_sender),
            )
        public_auth_base_url = _resolve_public_auth_base_url(settings=settings)
        rendered_email = render_magic_link_email(
            to_email=issued.challenge.email,
            magic_link_url=f"{public_auth_base_url}/auth/magic-link?token={issued.token}",
        )
        delivery = _deliver_email(
            EmailMessage(
                to_email=issued.challenge.email,
                subject=rendered_email.subject,
                html_content=rendered_email.html,
                text_content=rendered_email.text,
                metadata={
                    "kind": "magic_link",
                    "challenge_id": issued.challenge.challenge_id,
                },
            )
        )
        return MagicLinkRequestResponse(
            message="If the sign-in request is valid, a sign-in link has been issued.",
            delivery=_build_email_delivery_summary(delivery),
        )

    @router.post("/auth/magic-link/verify", response_model=MeResponse)
    def verify_magic_link(
        payload: MagicLinkVerifyRequest,
        request: Request,
        response: Response,
    ) -> MeResponse:
        try:
            issued = auth_service.verify_magic_link(
                token=payload.token,
                audit=build_session_audit_context(request),
            )
        except (AuthenticationError, AuthorizationError, ConflictError) as exc:
            _raise_http_for_auth_error(exc)
        return _respond_with_browser_session(request=request, response=response, issued=issued)

    @router.post("/auth/oauth/google/start", response_model=OAuthStartResponse)
    async def start_google_signin(payload: OAuthStartRequest) -> OAuthStartResponse:
        redirect_uri = _validated_redirect_uri(payload.redirect_uri)
        if payload.invitation_token:
            try:
                auth_service.get_organization_invitation_by_token(payload.invitation_token)
            except (AuthenticationError, AuthorizationError, ConflictError) as exc:
                _raise_http_for_auth_error(exc)
        try:
            client_id, _ = resolve_google_credentials(settings)
            discovery = await fetch_discovery("https://accounts.google.com")
        except ExternalAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        state_payload: dict[str, object] = {
            "provider": "google",
            "redirect_uri": redirect_uri,
        }
        if payload.invitation_token:
            state_payload["invitation_token"] = payload.invitation_token
        if payload.organization_id:
            state_payload["organization_id"] = payload.organization_id
        authorization_url = build_authorization_url(
            authorization_endpoint=discovery["authorization_endpoint"],
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=auth_service.create_oauth_state(payload=state_payload),
            scopes=["openid", "profile", "email"],
            login_hint=payload.email,
            prompt="select_account",
        )
        return OAuthStartResponse(authorization_url=authorization_url)

    @router.post("/auth/oauth/sso/start", response_model=OAuthStartResponse)
    async def start_enterprise_sso(payload: OAuthStartRequest) -> OAuthStartResponse:
        redirect_uri = _validated_redirect_uri(payload.redirect_uri)
        configuration: EnterpriseSSOConfiguration | None = None
        login_hint: str | None = None
        if payload.invitation_token:
            try:
                invitation = auth_service.get_organization_invitation_by_token(payload.invitation_token)
                configuration = auth_service.get_enterprise_sso_configuration_for_organization(
                    invitation.organization_id
                )
                login_hint = invitation.email
            except (AuthenticationError, AuthorizationError, ConflictError) as exc:
                _raise_http_for_auth_error(exc)
        elif payload.email:
            try:
                configuration = auth_service.find_active_enterprise_sso_configuration_by_email(
                    payload.email
                )
                login_hint = payload.email
            except (AuthenticationError, AuthorizationError, ConflictError) as exc:
                _raise_http_for_auth_error(exc)
        else:
            raise HTTPException(status_code=400, detail="work email or invitation token is required")

        if configuration is None or not configuration.is_active:
            raise HTTPException(
                status_code=404,
                detail="No enterprise SSO configuration found for this email domain",
            )

        try:
            discovery = await fetch_discovery(configuration.issuer_url)
        except ExternalAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        state_payload: dict[str, object] = {
            "provider": "oidc",
            "redirect_uri": redirect_uri,
            "organization_id": configuration.organization_id,
            "sso_configuration_id": configuration.sso_configuration_id,
        }
        if payload.invitation_token:
            state_payload["invitation_token"] = payload.invitation_token
        authorization_url = build_authorization_url(
            authorization_endpoint=discovery["authorization_endpoint"],
            client_id=configuration.client_id,
            redirect_uri=redirect_uri,
            state=auth_service.create_oauth_state(payload=state_payload),
            scopes=list(configuration.scopes),
            login_hint=login_hint,
            prompt="login",
        )
        return OAuthStartResponse(authorization_url=authorization_url)

    @router.post("/auth/oauth/callback", response_model=MeResponse)
    async def complete_oauth_signin(
        payload: OAuthCallbackRequest,
        request: Request,
        response: Response,
    ) -> MeResponse:
        redirect_uri = _validated_redirect_uri(payload.redirect_uri)
        try:
            state_claims = auth_service.decode_oauth_state(payload.state)
        except (AuthenticationError, AuthorizationError, ConflictError) as exc:
            _raise_http_for_auth_error(exc)

        if state_claims.get("redirect_uri") != redirect_uri:
            raise HTTPException(status_code=400, detail="OAuth redirect URI mismatch")

        provider = str(state_claims.get("provider") or "").strip()
        provider_type: str
        provider_key: str
        jit_organization_id: str | None = None
        organization_id = state_claims.get("organization_id")
        invitation_token = state_claims.get("invitation_token")
        configuration: EnterpriseSSOConfiguration | None = None

        try:
            if provider == "google":
                client_id, client_secret = resolve_google_credentials(settings)
                issuer_url = "https://accounts.google.com"
                provider_type = "google"
                provider_key = "google"
            elif provider == "oidc":
                sso_configuration_id = str(state_claims.get("sso_configuration_id") or "").strip()
                if not sso_configuration_id:
                    raise HTTPException(status_code=400, detail="Invalid SSO state payload")
                configuration = identity_store.get_enterprise_sso_configuration(sso_configuration_id)
                if configuration is None or not configuration.is_active:
                    raise HTTPException(status_code=403, detail="SSO configuration is inactive or missing")
                if organization_id and organization_id != configuration.organization_id:
                    raise HTTPException(status_code=400, detail="Invalid SSO state payload")
                issuer_url = configuration.issuer_url
                client_id = configuration.client_id
                client_secret = resolve_enterprise_sso_client_secret(configuration.client_secret_ref)
                provider_type = "oidc"
                provider_key = configuration.sso_configuration_id
                if configuration.jit_provisioning_enabled:
                    jit_organization_id = configuration.organization_id
            else:
                raise HTTPException(status_code=400, detail="Unsupported OAuth provider")

            discovery = await fetch_discovery(issuer_url)
            token_response = await exchange_code_for_tokens(
                token_endpoint=discovery["token_endpoint"],
                code=payload.code,
                redirect_uri=redirect_uri,
                client_id=client_id,
                client_secret=client_secret,
            )
            claims = await fetch_userinfo(
                userinfo_endpoint=discovery["userinfo_endpoint"],
                access_token=str(token_response["access_token"]),
            )
            identity_claims = identity_from_claims(claims)
        except ExternalAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if identity_claims.email is None:
            raise HTTPException(status_code=400, detail="Identity provider did not return email")

        if provider == "oidc":
            assert configuration is not None
            email_domain = identity_claims.email.split("@", 1)[1].strip().lower()
            allowed_domains = {item.strip().lower() for item in configuration.allowed_domains if item}
            if allowed_domains and email_domain not in allowed_domains:
                raise HTTPException(status_code=403, detail="Email domain is not allowed for this SSO configuration")

        profile = ExternalAuthProfile(
            subject=identity_claims.subject,
            email=identity_claims.email,
            email_verified=identity_claims.email_verified,
            display_name=identity_claims.display_name,
            avatar_url=identity_claims.avatar_url,
            claims=dict(identity_claims.claims),
        )
        try:
            issued = auth_service.authenticate_external_identity(
                provider_type=provider_type,
                provider_key=provider_key,
                profile=profile,
                organization_id=organization_id if isinstance(organization_id, str) else None,
                invitation_token=invitation_token if isinstance(invitation_token, str) else None,
                jit_organization_id=jit_organization_id,
                audit=build_session_audit_context(request),
            )
        except (AuthenticationError, AuthorizationError, ConflictError) as exc:
            _raise_http_for_auth_error(exc)
        return _respond_with_browser_session(request=request, response=response, issued=issued)

    @router.post("/auth/invitations/accept", response_model=MeResponse)
    def accept_invitation(
        payload: AcceptInvitationRequest,
        request: Request,
        response: Response,
    ) -> MeResponse:
        try:
            accepted_invitation = auth_service.get_organization_invitation_by_token(
                payload.invitation_token
            )
            issued = auth_service.accept_organization_invitation(
                invitation_token=payload.invitation_token,
                display_name=payload.display_name,
                timezone_name=payload.timezone,
                language=payload.language,
                audit=build_session_audit_context(request),
            )
        except (AuthenticationError, AuthorizationError, ConflictError) as exc:
            _raise_http_for_auth_error(exc)
        emit_notification(
            notification_store,
            organization_id=issued.session.organization_id,
            category="auth.invitation_accepted",
            title="A new member joined your organization",
            level="info",
            urgency="fyi",
            source_type="user",
            source_id=issued.session.user_id,
            payload={"user_id": issued.session.user_id},
        )
        emit_semantic_audit_event(
            request=request,
            event_type=ADMIN_INVITATION_ACCEPTED,
            organization_id=issued.session.organization_id,
            actor_id=issued.session.user_id,
            actor_session_id=issued.session.session_id,
            resource_type="organization_invitation",
            resource_id=accepted_invitation.invitation_id,
            detail={
                "email": accepted_invitation.email,
                "role": accepted_invitation.role,
                "accepted_by_user_id": issued.session.user_id,
                "accepted_via": "invitation_acceptance",
            },
        )
        return _respond_with_browser_session(request=request, response=response, issued=issued)

    @router.get("/auth/sessions", response_model=list[SessionResponse])
    def list_self_sessions(
        context: RequestAuthContext = Depends(require_authenticated_context),
    ) -> list[SessionResponse]:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        sessions = auth_service.list_user_sessions(
            user_id=principal.user.user_id,
            organization_id=principal.organization.organization_id,
        )
        return [
            _build_session_response(session, current_session_id=principal.session.session_id)
            for session in sessions
        ]

    @router.delete("/auth/sessions/current", status_code=status.HTTP_204_NO_CONTENT)
    def revoke_current_session(
        request: Request,
        response: Response,
        context: RequestAuthContext = Depends(require_authenticated_context),
    ) -> Response:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            auth_service.revoke_user_session(
                session_id=principal.session.session_id,
                user_id=principal.user.user_id,
                organization_id=principal.organization.organization_id,
            )
        except (AuthenticationError, AuthorizationError, ConflictError) as exc:
            _raise_http_for_auth_error(exc)
        clear_auth_cookies(response, secure=request_uses_secure_cookies(request))
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    @router.delete("/auth/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
    def revoke_self_session(
        session_id: str,
        request: Request,
        response: Response,
        context: RequestAuthContext = Depends(require_authenticated_context),
    ) -> Response:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        session = identity_store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="unknown session")
        if (
            session.user_id != principal.user.user_id
            or session.organization_id != principal.organization.organization_id
        ):
            raise HTTPException(status_code=404, detail="unknown session")
        try:
            auth_service.revoke_user_session(
                session_id=session_id,
                user_id=principal.user.user_id,
                organization_id=principal.organization.organization_id,
            )
        except (AuthenticationError, AuthorizationError, ConflictError) as exc:
            _raise_http_for_auth_error(exc)
        if session_id == principal.session.session_id:
            clear_auth_cookies(response, secure=request_uses_secure_cookies(request))
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    return router


def build_auth_me_router() -> APIRouter:
    """Build the unconditional GET /auth/me router.

    Registered OUTSIDE both auth guards, exactly like the original inline
    route — when auth is disabled the middleware never populates a principal
    and the handler 401s.
    """
    router = APIRouter()

    @router.get("/auth/me", response_model=MeResponse)
    def get_authenticated_principal(
        context: RequestAuthContext = Depends(require_authenticated_context),
    ) -> MeResponse:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return _build_me_response(principal)

    return router


def build_auth_profile_router(
    *,
    auth_service: "AuthService",
    identity_store: "IdentityStore",
) -> APIRouter:
    """Build the PATCH /auth/me + external-identities router.

    Mounted only when ``auth_enabled and effective_auth_service is not None
    and effective_identity_store is not None`` — the original second guard.
    """
    router = APIRouter()

    @router.patch("/auth/me", response_model=MeResponse)
    def update_authenticated_principal(
        payload: UpdateSelfRequest,
        context: RequestAuthContext = Depends(require_authenticated_context),
    ) -> MeResponse:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        updated_user = auth_service.update_user_profile(
            user_id=principal.user.user_id,
            display_name=payload.display_name,
            avatar_url=payload.avatar_url,
            timezone_name=payload.timezone,
            language=payload.language,
            preferences=payload.preferences,
        )
        refreshed_principal = principal.model_copy(update={"user": updated_user})
        return _build_me_response(refreshed_principal)

    @router.get("/auth/external-identities", response_model=list[ExternalIdentitySummary])
    def list_authenticated_external_identities(
        context: RequestAuthContext = Depends(require_authenticated_context),
    ) -> list[ExternalIdentitySummary]:
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return [
            _build_external_identity_summary(identity)
            for identity in identity_store.list_external_identities_for_user(principal.user.user_id)
        ]

    return router
