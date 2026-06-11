from __future__ import annotations

from typing import Literal

from fastapi import Depends, HTTPException, Request, status

from .api_auth import RequestAuthContext, require_authenticated_context
from .permissions import ROLE_PERMISSIONS, Permission


OrganizationRole = Literal["analyst", "developer", "admin"]

_ORGANIZATION_ROLE_ORDER: dict[OrganizationRole, int] = {
    "analyst": 10,
    "developer": 20,
    "admin": 30,
}


def has_minimum_organization_role(context: RequestAuthContext, required_role: OrganizationRole) -> bool:
    principal = context.principal
    if principal is None:
        return False
    if principal.is_account_owner:
        return True
    return _ORGANIZATION_ROLE_ORDER[principal.organization_role] >= _ORGANIZATION_ROLE_ORDER[required_role]


def require_organization_role(required_role: OrganizationRole):
    def dependency(request: Request) -> RequestAuthContext:
        context = require_authenticated_context(request)
        if not has_minimum_organization_role(context, required_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{required_role} role required for organization access",
            )
        return context

    return dependency


def require_permissions(*perms: Permission):
    """FastAPI dependency factory.

    Grants access if the principal has ANY of the listed permissions (OR semantics).
    Use ``require_permissions(A, B)`` where A *or* B is sufficient; nest multiple
    calls if both are required.

    Superusers bypass all checks. Account owners are granted admin-level permissions.

    Usage::

        @router.get("/agents/{id}/audit")
        async def get_audit(
            ctx = require_permissions(Permission.AGENT_AUDIT),
        ): ...
    """

    async def _check(
        ctx: RequestAuthContext = Depends(require_authenticated_context),
    ) -> RequestAuthContext:
        if ctx.principal is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")
        if ctx.principal.is_superuser:
            return ctx

        role = ctx.principal.organization_role
        if ctx.principal.is_account_owner:
            role = "admin"

        granted = ROLE_PERMISSIONS.get(role or "", frozenset())
        if not any(p in granted for p in perms):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "insufficient_permissions",
                    "required_any": [p.value for p in perms],
                    "role": role,
                },
            )
        return ctx

    return Depends(_check)


def require_account_owner(request: Request) -> RequestAuthContext:
    context = require_authenticated_context(request)
    principal = context.principal
    if principal is None or not principal.is_account_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="account owner permission required",
        )
    return context
