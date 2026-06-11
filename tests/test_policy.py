from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from ruhu.api_auth import RequestAuthContext
from ruhu.auth import AuthService, JWTCodec
from ruhu.identity import InMemoryIdentityStore, Organization, OrganizationMembership, User
from ruhu.policy import has_minimum_organization_role, require_account_owner, require_organization_role

TEST_HS256_SECRET = "0123456789abcdef0123456789abcdef"


def _build_context(*, role: str, is_account_owner: bool = False) -> RequestAuthContext:
    store = InMemoryIdentityStore()
    store.save_user(User(user_id="user-1", email="user@example.com"))
    store.save_organization(Organization(organization_id="org-1", slug="acme", name="Acme"))
    store.add_organization_membership(
        OrganizationMembership(
            user_id="user-1",
            organization_id="org-1",
            role=role,
            is_account_owner=is_account_owner,
        )
    )
    service = AuthService(identity_store=store, jwt_codec=JWTCodec(secret=TEST_HS256_SECRET))
    issued = service.issue_session(user_id="user-1", organization_id="org-1")
    principal = service.authenticate_access_token(issued.access_token)
    return RequestAuthContext(principal=principal)


def _build_request(context: RequestAuthContext):
    return SimpleNamespace(state=SimpleNamespace(auth_context=context))


def test_org_role_ordering_matches_expected_capabilities() -> None:
    analyst = _build_context(role="analyst")
    developer = _build_context(role="developer")
    admin = _build_context(role="admin")

    assert has_minimum_organization_role(analyst, "analyst") is True
    assert has_minimum_organization_role(analyst, "developer") is False
    assert has_minimum_organization_role(developer, "analyst") is True
    assert has_minimum_organization_role(developer, "developer") is True
    assert has_minimum_organization_role(developer, "admin") is False
    assert has_minimum_organization_role(admin, "developer") is True
    assert has_minimum_organization_role(admin, "admin") is True


def test_account_owner_bypasses_org_role_ordering() -> None:
    context = _build_context(role="analyst", is_account_owner=True)
    assert has_minimum_organization_role(context, "admin") is True


def test_require_organization_role_rejects_anonymous_request() -> None:
    dependency = require_organization_role("developer")
    request = _build_request(RequestAuthContext())
    with pytest.raises(HTTPException) as exc_info:
        dependency(request)
    assert exc_info.value.status_code == 401


def test_require_organization_role_rejects_lower_privilege_role() -> None:
    dependency = require_organization_role("developer")
    request = _build_request(_build_context(role="analyst"))
    with pytest.raises(HTTPException) as exc_info:
        dependency(request)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "developer role required for organization access"


def test_require_account_owner_rejects_non_owner() -> None:
    request = _build_request(_build_context(role="admin", is_account_owner=False))
    with pytest.raises(HTTPException) as exc_info:
        require_account_owner(request)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "account owner permission required"


def test_require_account_owner_accepts_owner_flag() -> None:
    context = _build_context(role="analyst", is_account_owner=True)
    request = _build_request(context)
    resolved = require_account_owner(request)
    assert resolved.principal is not None
    assert resolved.principal.is_account_owner is True
