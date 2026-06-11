from __future__ import annotations

from fastapi import HTTPException, Request, status
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .auth import AuthService, AuthenticatedPrincipal, AuthenticationError, AuthorizationError
from .db import tenant_db_context
from .session_http import (
    ACCESS_TOKEN_COOKIE_NAME,
    build_session_audit_context,
    clear_auth_cookies,
    extract_access_token_from_request,
    request_uses_secure_cookies,
)


AUTH_MANAGED_SESSION_PATHS = frozenset(
    {
        "/auth/refresh",
        "/auth/logout",
        "/auth/invitations/accept",
        "/auth/invite/validate",
        "/auth/magic-link/request",
        "/auth/magic-link/verify",
        "/auth/oauth/google/start",
        "/auth/oauth/sso/start",
        "/auth/oauth/callback",
    }
)


class TenantTarget(BaseModel):
    organization_id: str | None = None


class RequestAuthContext(BaseModel):
    principal: AuthenticatedPrincipal | None = None
    tenant: TenantTarget = Field(default_factory=TenantTarget)
    access_token: str | None = None

    @property
    def is_authenticated(self) -> bool:
        return self.principal is not None


def extract_bearer_token(authorization_header: str | None) -> str | None:
    if authorization_header is None:
        return None
    scheme, _, token = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip() or None


class AuthContextResolver:
    def __init__(
        self,
        *,
        auth_service: AuthService,
        organization_header: str = "X-Ruhu-Organization-Id",
    ) -> None:
        self.auth_service = auth_service
        self.organization_header = organization_header

    def resolve(self, request: Request) -> RequestAuthContext:
        access_token = extract_bearer_token(request.headers.get("Authorization"))
        if access_token is None:
            access_token = extract_access_token_from_request(request)
        tenant = TenantTarget(
            organization_id=request.headers.get(self.organization_header),
        )
        if access_token is None:
            return RequestAuthContext(tenant=tenant)

        principal = self.auth_service.authenticate_access_token(access_token)
        # Session activity recording is debounced (5 min) and non-blocking.
        # It updates last_seen_at for session freshness tracking but should
        # not add latency to every authenticated request.
        touched_session = self.auth_service.record_session_activity(
            principal.session.session_id,
            audit=build_session_audit_context(request),
        )
        if touched_session is not None:
            principal = principal.model_copy(update={"session": touched_session})
        principal = self._apply_tenant_target(principal, tenant)
        effective_tenant = TenantTarget(
            organization_id=principal.organization.organization_id,
        )
        return RequestAuthContext(principal=principal, tenant=effective_tenant, access_token=access_token)

    @staticmethod
    def _apply_tenant_target(
        principal: AuthenticatedPrincipal,
        tenant: TenantTarget,
    ) -> AuthenticatedPrincipal:
        if tenant.organization_id is not None and tenant.organization_id != principal.organization.organization_id:
            raise AuthorizationError("access token is not scoped to the requested organization")
        return principal


class AuthContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, resolver: AuthContextResolver) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.resolver = resolver

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.url.path in AUTH_MANAGED_SESSION_PATHS:
            request.state.auth_context = RequestAuthContext()
            return await call_next(request)
        try:
            request.state.auth_context = self.resolver.resolve(request)
        except AuthenticationError as exc:
            response = JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": str(exc)},
                headers={"WWW-Authenticate": "Bearer"},
            )
            # Clear the expired/invalid access token cookie so the browser
            # stops replaying it. But do NOT clear the refresh token cookie —
            # the client needs it to call /auth/refresh and obtain a new
            # access token. Clearing both cookies here was causing immediate
            # session loss: by the time the frontend's 401 handler tried to
            # refresh, the browser had already deleted the refresh cookie.
            secure = request_uses_secure_cookies(request)
            response.delete_cookie(
                key=ACCESS_TOKEN_COOKIE_NAME,
                path="/",
                httponly=True,
                secure=secure,
                samesite="lax",
            )
            return response
        except AuthorizationError as exc:
            return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": str(exc)})
        context = request.state.auth_context
        principal = context.principal
        if principal is None:
            return await call_next(request)
        with tenant_db_context(
            organization_id=principal.organization.organization_id,
            user_id=principal.user.user_id,
            is_superuser=principal.is_superuser,
        ):
            return await call_next(request)


def get_request_auth_context(request: Request) -> RequestAuthContext:
    context = getattr(request.state, "auth_context", None)
    if context is None:
        return RequestAuthContext()
    return context


def require_authenticated_context(request: Request) -> RequestAuthContext:
    context = get_request_auth_context(request)
    if context.principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return context
