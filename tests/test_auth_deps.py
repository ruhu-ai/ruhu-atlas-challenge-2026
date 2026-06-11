"""
Tests for /Users/ijidailassa/projects/ruhu/src/ruhu/auth_deps.py.

Covers the four factory functions that replace the inline auth closures from
``api.py``. Each factory returns a FastAPI dependency callable parameterised
by ``auth_enabled`` (except ``make_internal_superuser_dep`` which always
enforces).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from ruhu.auth_deps import (
    make_author_context_dep,
    make_internal_superuser_dep,
    make_org_context_dep,
    make_reviewer_context_dep,
)


def _build_request(
    *,
    principal=None,
    role: str = "developer",
    is_superuser: bool = False,
):
    """Build a Mock request whose ``state.auth_context`` mirrors production shape."""
    request = MagicMock()
    if principal is None:
        ctx = None
    else:
        ctx = MagicMock()
        ctx.principal = principal
    request.state.auth_context = ctx
    return request


def _build_principal(*, role: str = "developer", is_superuser: bool = False):
    """Build a minimal fake principal that satisfies the role/superuser checks."""
    principal = MagicMock()
    principal.organization_role = role
    principal.organization_membership.role = role
    principal.organization_membership.is_account_owner = False
    principal.is_account_owner = False
    principal.user.is_superuser = is_superuser
    principal.is_superuser = is_superuser
    return principal


# ─── make_org_context_dep ────────────────────────────────────────────────────

class TestOrgContextDep:
    def test_returns_none_when_auth_disabled(self) -> None:
        """Dev/test mode (auth_enabled=False) → dep returns None regardless of request."""
        dep = make_org_context_dep(auth_enabled=False)
        result = dep(_build_request())
        assert result is None

    def test_returns_context_when_auth_enabled(self) -> None:
        """Auth enabled → dep returns whatever get_request_auth_context returns."""
        dep = make_org_context_dep(auth_enabled=True)
        principal = _build_principal()
        request = _build_request(principal=principal)
        result = dep(request)
        # get_request_auth_context returns request.state.auth_context
        assert result is request.state.auth_context


# ─── make_author_context_dep ─────────────────────────────────────────────────

class TestAuthorContextDep:
    def test_returns_none_when_auth_disabled(self) -> None:
        dep = make_author_context_dep(auth_enabled=False)
        result = dep(_build_request())
        assert result is None

    def test_accepts_developer_role(self) -> None:
        """Developer role is the minimum for author-level operations."""
        dep = make_author_context_dep(auth_enabled=True)
        principal = _build_principal(role="developer")
        request = _build_request(principal=principal)
        result = dep(request)
        assert result is not None

    def test_accepts_admin_role(self) -> None:
        """Admin meets the developer threshold."""
        dep = make_author_context_dep(auth_enabled=True)
        principal = _build_principal(role="admin")
        request = _build_request(principal=principal)
        result = dep(request)
        assert result is not None

    def test_rejects_analyst_role(self) -> None:
        """Analyst is below developer — dep must raise 403."""
        dep = make_author_context_dep(auth_enabled=True)
        principal = _build_principal(role="analyst")
        request = _build_request(principal=principal)
        with pytest.raises(HTTPException) as excinfo:
            dep(request)
        assert excinfo.value.status_code == 403


# ─── make_reviewer_context_dep ───────────────────────────────────────────────

class TestReviewerContextDep:
    def test_returns_none_when_auth_disabled(self) -> None:
        dep = make_reviewer_context_dep(auth_enabled=False)
        result = dep(_build_request())
        assert result is None

    def test_accepts_analyst_role(self) -> None:
        """Analyst is the minimum for reviewer-level operations."""
        dep = make_reviewer_context_dep(auth_enabled=True)
        principal = _build_principal(role="analyst")
        request = _build_request(principal=principal)
        result = dep(request)
        assert result is not None

    def test_accepts_developer_role(self) -> None:
        dep = make_reviewer_context_dep(auth_enabled=True)
        principal = _build_principal(role="developer")
        request = _build_request(principal=principal)
        result = dep(request)
        assert result is not None

    def test_rejects_missing_auth(self) -> None:
        """No principal on the request → dep raises 401."""
        dep = make_reviewer_context_dep(auth_enabled=True)
        request = _build_request(principal=None)
        with pytest.raises(HTTPException) as excinfo:
            dep(request)
        assert excinfo.value.status_code == 401


# ─── make_internal_superuser_dep ─────────────────────────────────────────────

class TestInternalSuperuserDep:
    def test_accepts_superuser(self) -> None:
        dep = make_internal_superuser_dep()
        principal = _build_principal(is_superuser=True)
        request = _build_request(principal=principal)
        result = dep(request)
        assert result is request.state.auth_context

    def test_rejects_non_superuser(self) -> None:
        """Admin role alone is not enough — the dep requires is_superuser=True."""
        dep = make_internal_superuser_dep()
        principal = _build_principal(role="admin", is_superuser=False)
        request = _build_request(principal=principal)
        with pytest.raises(HTTPException) as excinfo:
            dep(request)
        assert excinfo.value.status_code == 403

    def test_rejects_missing_principal(self) -> None:
        """No principal → dep raises 401 from require_authenticated_context."""
        dep = make_internal_superuser_dep()
        request = _build_request(principal=None)
        with pytest.raises(HTTPException) as excinfo:
            dep(request)
        # Either 401 (no auth_context at all) or 401 (principal=None) — either is fine
        assert excinfo.value.status_code == 401

    def test_does_not_honour_auth_disabled(self) -> None:
        """Unlike role deps, internal-superuser must gate even in dev mode.

        This is by design — internal admin endpoints expose cross-org state and
        should never be open to unauthenticated callers, regardless of
        auth_enabled.
        """
        # The factory doesn't take auth_enabled at all, so the behaviour is
        # identical regardless of environment. We verify that by confirming
        # a request with no principal still raises.
        dep = make_internal_superuser_dep()
        request = _build_request(principal=None)
        with pytest.raises(HTTPException):
            dep(request)
