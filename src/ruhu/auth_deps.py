"""
FastAPI dependency factories for authenticated route protection.

These factories replace four closures that used to live inside ``create_app()``:
``_require_org_context``, ``_require_runtime_author_context``,
``_require_runtime_reviewer_context``, ``_require_internal_superuser``.

### Why factories, not plain dependencies

Three of the four variants need to know whether authentication is enabled at
all — when ``auth_enabled=False`` (dev/testing with no JWT secret configured),
the deps short-circuit to ``None`` and let the handler fall back to a bootstrap
organization_id.

``auth_enabled`` is derived at application-construction time from whether
``auth_resolver`` and ``auth_service`` were successfully built. It is not a
simple ``RuntimeSettings`` field, so we can't make these module-level deps
that read settings directly — they need to be parameterised.

### Usage inside ``create_app()``

```python
from .auth_deps import (
    make_org_context_dep,
    make_author_context_dep,
    make_reviewer_context_dep,
    make_internal_superuser_dep,
)

auth_enabled = auth_resolver is not None and effective_auth_service is not None

_require_org_context = make_org_context_dep(auth_enabled)
_require_runtime_author_context = make_author_context_dep(auth_enabled)
_require_runtime_reviewer_context = make_reviewer_context_dep(auth_enabled)
_require_internal_superuser = make_internal_superuser_dep()
```

Call sites continue to use ``Depends(_require_runtime_author_context)`` — only
the definitions move, not the 61 references across ``api.py``.

### Reuse from extracted routers

Router builders (``build_conversations_router``, future ``build_agents_router``,
etc.) can call these factories directly instead of accepting the dep as a
parameter. That removes the "pass auth closure as a function parameter" anti-
pattern that's showing up in extracted routers.
"""
from __future__ import annotations

from typing import Callable

from fastapi import HTTPException, Request

from .api_auth import (
    RequestAuthContext,
    get_request_auth_context,
    require_authenticated_context,
)
from .policy import require_organization_role

# Public re-exports for type hints (callers can annotate as RequestAuthContext | None).
__all__ = [
    "make_org_context_dep",
    "make_author_context_dep",
    "make_reviewer_context_dep",
    "make_internal_superuser_dep",
]


def make_org_context_dep(
    auth_enabled: bool,
) -> Callable[[Request], RequestAuthContext | None]:
    """Return a dependency that resolves the optional authenticated context.

    - Auth disabled: returns ``None`` — the handler must fall back to a
      bootstrap organization_id.
    - Auth enabled: returns the request's ``RequestAuthContext`` (may still be
      None if the middleware didn't populate it; public routes use this to
      let protected handlers enforce auth explicitly).
    """
    def _require_org_context(request: Request) -> RequestAuthContext | None:
        if not auth_enabled:
            return None
        return get_request_auth_context(request)

    return _require_org_context


def make_author_context_dep(
    auth_enabled: bool,
) -> Callable[[Request], RequestAuthContext | None]:
    """Return a dependency that enforces the ``developer`` role when auth is enabled.

    Dev/test mode (auth disabled) short-circuits to ``None``. In production,
    insufficient role raises 403 via ``require_organization_role``.
    """
    _inner = require_organization_role("developer")

    def _require_runtime_author_context(request: Request) -> RequestAuthContext | None:
        if not auth_enabled:
            return None
        return _inner(request)

    return _require_runtime_author_context


def make_reviewer_context_dep(
    auth_enabled: bool,
) -> Callable[[Request], RequestAuthContext | None]:
    """Return a dependency that enforces the ``analyst`` role when auth is enabled.

    Same short-circuit semantics as the author variant, lower role threshold.
    """
    _inner = require_organization_role("analyst")

    def _require_runtime_reviewer_context(request: Request) -> RequestAuthContext | None:
        if not auth_enabled:
            return None
        return _inner(request)

    return _require_runtime_reviewer_context


def make_internal_superuser_dep() -> Callable[[Request], RequestAuthContext]:
    """Return a dependency that requires a PG superuser-equivalent principal.

    Unlike the role-based deps above, this one does NOT honour ``auth_enabled``
    — internal admin endpoints must be superuser-gated at all times, even in
    dev mode, because they expose org-crossing state (platform health,
    cross-org diagnostics, etc.).
    """
    def _require_internal_superuser(request: Request) -> RequestAuthContext:
        context = require_authenticated_context(request)
        principal = context.principal
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        if not principal.user.is_superuser:
            raise HTTPException(
                status_code=403, detail="internal admin access required"
            )
        return context

    return _require_internal_superuser
