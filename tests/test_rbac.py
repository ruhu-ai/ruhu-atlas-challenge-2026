"""
Tests for Phase 5: Fine-grained RBAC.

Covers:
  - Permission enum values
  - ROLE_PERMISSIONS role hierarchy
  - require_permissions() FastAPI dependency factory
"""
from __future__ import annotations

import anyio
import pytest
from fastapi import HTTPException

from ruhu.api_auth import RequestAuthContext
from ruhu.auth import AuthService, JWTCodec
from ruhu.identity import InMemoryIdentityStore, Organization, OrganizationMembership, User
from ruhu.permissions import Permission, ROLE_PERMISSIONS
from ruhu.policy import require_permissions

TEST_HS256_SECRET = "0123456789abcdef0123456789abcdef"


# ── helpers ────────────────────────────────────────────────────────────────────

def _build_context(
    *,
    role: str,
    is_account_owner: bool = False,
    is_superuser: bool = False,
) -> RequestAuthContext:
    store = InMemoryIdentityStore()
    store.save_user(
        User(user_id="user-1", email="user@example.com", is_superuser=is_superuser)
    )
    store.save_organization(
        Organization(organization_id="org-1", slug="acme", name="Acme")
    )
    store.add_organization_membership(
        OrganizationMembership(
            user_id="user-1",
            organization_id="org-1",
            role=role,
            is_account_owner=is_account_owner,
        )
    )
    service = AuthService(
        identity_store=store, jwt_codec=JWTCodec(secret=TEST_HS256_SECRET)
    )
    issued = service.issue_session(user_id="user-1", organization_id="org-1")
    principal = service.authenticate_access_token(issued.access_token)
    return RequestAuthContext(principal=principal)


def _dep_fn(dep):
    """Unwrap Depends() to get the inner callable."""
    return dep.dependency


# ── Permission enum ────────────────────────────────────────────────────────────

class TestPermissionEnum:
    def test_string_values_are_namespaced(self) -> None:
        assert Permission.AGENT_READ == "agent:read"
        assert Permission.AGENT_EDIT == "agent:edit"
        assert Permission.AUDIT_READ == "audit:read"
        assert Permission.BILLING_MANAGE == "billing:manage"

    def test_all_permissions_are_unique(self) -> None:
        values = [p.value for p in Permission]
        assert len(values) == len(set(values))


# ── ROLE_PERMISSIONS mapping ───────────────────────────────────────────────────

class TestRolePermissions:
    def test_analyst_has_read_only_subset(self) -> None:
        granted = ROLE_PERMISSIONS["analyst"]
        assert Permission.AGENT_READ in granted
        assert Permission.CONVERSATION_READ in granted
        assert Permission.KNOWLEDGE_READ in granted
        assert Permission.KPI_READ in granted
        assert Permission.RULE_READ in granted
        assert Permission.ORG_READ in granted

    def test_analyst_cannot_write(self) -> None:
        granted = ROLE_PERMISSIONS["analyst"]
        assert Permission.AGENT_EDIT not in granted
        assert Permission.AGENT_DELETE not in granted
        assert Permission.MEMBER_INVITE not in granted
        assert Permission.BILLING_MANAGE not in granted

    def test_developer_includes_analyst_permissions(self) -> None:
        analyst = ROLE_PERMISSIONS["analyst"]
        developer = ROLE_PERMISSIONS["developer"]
        assert analyst.issubset(developer)

    def test_developer_adds_write_permissions(self) -> None:
        granted = ROLE_PERMISSIONS["developer"]
        assert Permission.AGENT_EDIT in granted
        assert Permission.AGENT_PUBLISH in granted
        assert Permission.CONVERSATION_REPLAY in granted
        assert Permission.TOOL_INVOKE in granted
        assert Permission.KNOWLEDGE_MANAGE in granted

    def test_developer_cannot_delete_or_manage_members(self) -> None:
        granted = ROLE_PERMISSIONS["developer"]
        assert Permission.AGENT_DELETE not in granted
        assert Permission.MEMBER_INVITE not in granted
        assert Permission.MEMBER_REMOVE not in granted
        assert Permission.TOOL_MANAGE not in granted

    def test_admin_includes_developer_permissions(self) -> None:
        developer = ROLE_PERMISSIONS["developer"]
        admin = ROLE_PERMISSIONS["admin"]
        assert developer.issubset(admin)

    def test_admin_adds_destructive_permissions(self) -> None:
        granted = ROLE_PERMISSIONS["admin"]
        assert Permission.AGENT_DELETE in granted
        assert Permission.AGENT_AUDIT in granted
        assert Permission.CONVERSATION_DELETE in granted
        assert Permission.TOOL_MANAGE in granted
        assert Permission.MEMBER_INVITE in granted
        assert Permission.MEMBER_REMOVE in granted
        assert Permission.ORG_UPDATE in granted
        assert Permission.BILLING_READ in granted
        assert Permission.AUDIT_READ in granted


# ── require_permissions() ──────────────────────────────────────────────────────

class TestRequirePermissions:
    def _call(self, dep_obj, ctx: RequestAuthContext) -> RequestAuthContext:
        """Invoke the inner async check with a known context."""
        fn = _dep_fn(dep_obj)

        def run():
            return anyio.run(fn, ctx)

        return run()

    def test_unauthenticated_raises_401(self) -> None:
        dep = require_permissions(Permission.AGENT_READ)
        ctx = RequestAuthContext(principal=None)
        with pytest.raises(HTTPException) as exc_info:
            self._call(dep, ctx)
        assert exc_info.value.status_code == 401

    def test_superuser_bypasses_all_checks(self) -> None:
        dep = require_permissions(Permission.BILLING_MANAGE)
        ctx = _build_context(role="analyst", is_superuser=True)
        result = self._call(dep, ctx)
        assert result.principal is not None

    def test_account_owner_gets_admin_permissions(self) -> None:
        dep = require_permissions(Permission.AUDIT_READ)
        ctx = _build_context(role="analyst", is_account_owner=True)
        result = self._call(dep, ctx)
        assert result.principal is not None

    def test_analyst_granted_analyst_permission(self) -> None:
        dep = require_permissions(Permission.AGENT_READ)
        ctx = _build_context(role="analyst")
        result = self._call(dep, ctx)
        assert result.principal is not None

    def test_analyst_denied_developer_permission(self) -> None:
        dep = require_permissions(Permission.AGENT_EDIT)
        ctx = _build_context(role="analyst")
        with pytest.raises(HTTPException) as exc_info:
            self._call(dep, ctx)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["code"] == "insufficient_permissions"
        assert "agent:edit" in exc_info.value.detail["required_any"]

    def test_developer_denied_admin_permission(self) -> None:
        dep = require_permissions(Permission.AGENT_DELETE)
        ctx = _build_context(role="developer")
        with pytest.raises(HTTPException) as exc_info:
            self._call(dep, ctx)
        assert exc_info.value.status_code == 403

    def test_admin_granted_audit_read(self) -> None:
        dep = require_permissions(Permission.AUDIT_READ)
        ctx = _build_context(role="admin")
        result = self._call(dep, ctx)
        assert result.principal is not None

    def test_or_semantics_either_permission_suffices(self) -> None:
        # analyst has AGENT_READ but not AGENT_EDIT; require_permissions(A, B)
        # grants access if ANY is in role's set.
        dep = require_permissions(Permission.AGENT_EDIT, Permission.AGENT_READ)
        ctx = _build_context(role="analyst")
        result = self._call(dep, ctx)
        assert result.principal is not None

    def test_or_semantics_both_absent_raises_403(self) -> None:
        dep = require_permissions(Permission.BILLING_MANAGE, Permission.MEMBER_INVITE)
        ctx = _build_context(role="analyst")
        with pytest.raises(HTTPException) as exc_info:
            self._call(dep, ctx)
        assert exc_info.value.status_code == 403
        detail = exc_info.value.detail
        assert "billing:manage" in detail["required_any"]
        assert "member:invite" in detail["required_any"]

    def test_403_detail_includes_role(self) -> None:
        dep = require_permissions(Permission.AGENT_DELETE)
        ctx = _build_context(role="developer")
        with pytest.raises(HTTPException) as exc_info:
            self._call(dep, ctx)
        assert exc_info.value.detail["role"] == "developer"
