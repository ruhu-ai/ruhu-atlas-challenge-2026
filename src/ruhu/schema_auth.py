"""Schema API authentication and RLS (Row-Level Security).

Handles organization/tenant isolation for schema endpoints.
Provides dependency injection for org_id extraction from request context.
"""

from typing import Optional
import logging

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ruhu.api_auth import get_request_auth_context, RequestAuthContext


logger = logging.getLogger(__name__)


class SchemaAuthContext:
    """Authentication context for schema APIs.

    Provides org_id and user_id from request context.
    Enforces RLS by filtering queries to organization_id.
    """

    def __init__(
        self,
        organization_id: str,
        user_id: Optional[str] = None,
        roles: list[str] | None = None,
    ):
        self.organization_id = organization_id
        self.user_id = user_id
        self.roles = roles or []

    def has_role(self, role: str) -> bool:
        """Check if user has a specific role."""
        return role in self.roles

    def is_admin(self) -> bool:
        """Check if user has admin role."""
        return self.has_role("admin") or self.has_role("org_admin")


async def get_schema_auth_context(
    request: Request,
    auth_context: Optional[RequestAuthContext] = Depends(get_request_auth_context),
) -> SchemaAuthContext:
    """Extract authentication context for schema APIs.

    Args:
        request: HTTP request
        auth_context: Authentication context from middleware

    Returns:
        SchemaAuthContext with org_id and user_id

    Raises:
        HTTPException: If not authenticated or org_id cannot be determined
    """
    # If no auth context, try to extract from request header or session
    if auth_context is None:
        # Fall back to header-based extraction (for testing/internal use)
        org_id = request.headers.get("X-Organization-ID")
        if not org_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing organization context",
            )
        return SchemaAuthContext(organization_id=org_id)

    # Extract organization from auth context
    # In production, this comes from JWT token claims
    org_id = getattr(auth_context, "organization_id", None)
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No organization in context",
        )

    user_id = getattr(auth_context, "user_id", None)
    roles = getattr(auth_context, "roles", [])

    return SchemaAuthContext(
        organization_id=org_id,
        user_id=user_id,
        roles=roles,
    )


async def require_schema_auth(
    auth: SchemaAuthContext = Depends(get_schema_auth_context),
) -> SchemaAuthContext:
    """Dependency: Require valid schema auth context."""
    if not auth.organization_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return auth


def rls_filter_org(auth: SchemaAuthContext) -> dict:
    """Generate RLS filter for organization scoping.

    Returns a filter dict that can be used in SQLModel queries:
    ```python
    filters = rls_filter_org(auth)
    query = query.where(**filters)
    ```
    """
    return {"organization_id": auth.organization_id}


async def check_resource_ownership(
    resource_org_id: str,
    auth: SchemaAuthContext,
) -> None:
    """Check that resource belongs to authenticated organization.

    Raises:
        HTTPException 404: If resource not found or belongs to different org
    """
    if resource_org_id != auth.organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found",
        )
