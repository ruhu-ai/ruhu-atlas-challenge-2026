"""Org-scope resolution factories extracted from ``create_app()`` (RP-3.1 step 1).

These replace the closure block that lived inside ``create_app()``:
``_organization_id_for_context``, ``_user_id_for_context``,
``_organization_id_for_request``, ``_user_id_for_request``,
``_required_author_organization_id``,
``_knowledge_organization_id_for_request``,
``_kpi_organization_id_for_request``,
``_intent_tags_organization_id_for_request``,
``_journey_organization_id_for_request``,
``_tool_integration_organization_id_for_request``.

### Why factories, not plain functions

Same reasoning as ``auth_deps.py``: the resolvers are parameterised over
application-construction state — ``auth_enabled`` (derived from whether the
auth resolver/service were built; not a plain ``RuntimeSettings`` field) and
``bootstrap_organization_id`` (single-tenant dev/test fallback passed to
``build_default_app``).

### Usage inside ``create_app()``

``create_app()`` calls these factories at the position the closures used to
occupy and REBINDS the old local names to the factory outputs, so the 200+
downstream references inside ``create_app()`` are textually untouched.
Extracted routers (``routes/`` package) should call the factories directly
instead of accepting the resolver as a function parameter.

### Resolution order (shared contract)

1. Authenticated principal's organization_id (production + auth tests).
2. ``requested_organization_id`` (only resolvers that accept it).
3. ``bootstrap_organization_id`` (auth-disabled single-tenant installs).
4. Domain-specific fallback (knowledge only), then 401 for the
   required-tenant resolvers / ``None`` for the optional ones.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from fastapi import HTTPException, Request

from ..api_auth import RequestAuthContext
from ..auth_deps import make_org_context_dep

if TYPE_CHECKING:
    from ..knowledge import KnowledgeRuntime

__all__ = [
    "organization_id_for_context",
    "user_id_for_context",
    "make_organization_id_for_request",
    "make_user_id_for_request",
    "make_required_author_organization_id",
    "make_knowledge_organization_id_for_request",
    "make_kpi_organization_id_for_request",
    "make_intent_tags_organization_id_for_request",
    "make_journey_organization_id_for_request",
    "make_tool_integration_organization_id_for_request",
]


def organization_id_for_context(context: RequestAuthContext | None) -> str | None:
    if context is None or context.principal is None:
        return None
    return context.principal.organization.organization_id


def user_id_for_context(context: RequestAuthContext | None) -> str | None:
    if context is None or context.principal is None:
        return None
    return context.principal.user.user_id


def make_organization_id_for_request(
    *,
    auth_enabled: bool,
    bootstrap_organization_id: str | None,
) -> Callable[[Request], str | None]:
    """Optional tenant resolver: principal's org, else bootstrap, else None."""
    _require_org_context = make_org_context_dep(auth_enabled)

    def _organization_id_for_request(request: Request) -> str | None:
        scoped = organization_id_for_context(_require_org_context(request))
        if scoped is not None:
            return scoped
        return bootstrap_organization_id

    return _organization_id_for_request


def make_user_id_for_request(
    *,
    auth_enabled: bool,
) -> Callable[[Request], str | None]:
    """Optional user resolver: the authenticated principal's user_id or None."""
    _require_org_context = make_org_context_dep(auth_enabled)

    def _user_id_for_request(request: Request) -> str | None:
        return user_id_for_context(_require_org_context(request))

    return _user_id_for_request


def make_required_author_organization_id(
    *,
    bootstrap_organization_id: str | None,
) -> Callable[[RequestAuthContext | None], str]:
    """Resolve the tenant for an authenticated mutation endpoint.

    Order:
      1. Authenticated principal's organization_id (production + auth tests).
      2. ``bootstrap_organization_id`` passed to ``build_default_app``
         (single-tenant dev/test installs with auth disabled).
      3. Otherwise 401 — we refuse to write tenant-scoped data without a
         resolved tenant (principle: every request resolves to a real tenant).
    """

    def _required_author_organization_id(context: RequestAuthContext | None) -> str:
        scoped = organization_id_for_context(context) if context is not None else None
        if scoped is not None:
            return scoped
        if bootstrap_organization_id is not None:
            return bootstrap_organization_id
        raise HTTPException(status_code=401, detail="authentication required")

    return _required_author_organization_id


def make_knowledge_organization_id_for_request(
    *,
    auth_enabled: bool,
    bootstrap_organization_id: str | None,
    knowledge_runtime: KnowledgeRuntime | None = None,
) -> Callable[..., str]:
    """Required tenant resolver for /knowledge — adds the knowledge-runtime
    default as a final fallback for single-tenant installs."""
    _organization_id_for_request = make_organization_id_for_request(
        auth_enabled=auth_enabled,
        bootstrap_organization_id=bootstrap_organization_id,
    )

    def _knowledge_organization_id_for_request(
        request: Request,
        requested_organization_id: str | None = None,
    ) -> str:
        scoped_organization_id = _organization_id_for_request(request)
        if scoped_organization_id is not None:
            return scoped_organization_id
        if requested_organization_id:
            return requested_organization_id
        if bootstrap_organization_id is not None:
            return bootstrap_organization_id
        # Single-tenant installs can configure a default via the knowledge runtime.
        if knowledge_runtime is not None and knowledge_runtime.default_organization_id is not None:
            return knowledge_runtime.default_organization_id
        raise HTTPException(status_code=401, detail="authentication required")

    return _knowledge_organization_id_for_request


def _make_requested_org_resolver(
    *,
    auth_enabled: bool,
    bootstrap_organization_id: str | None,
) -> Callable[..., str]:
    """Required tenant resolver that also honours an explicit
    ``requested_organization_id`` argument (kpi, intent-tags)."""
    _organization_id_for_request = make_organization_id_for_request(
        auth_enabled=auth_enabled,
        bootstrap_organization_id=bootstrap_organization_id,
    )

    def _resolve(
        request: Request,
        requested_organization_id: str | None = None,
    ) -> str:
        scoped_organization_id = _organization_id_for_request(request)
        if scoped_organization_id is not None:
            return scoped_organization_id
        if requested_organization_id:
            return requested_organization_id
        if bootstrap_organization_id is not None:
            return bootstrap_organization_id
        raise HTTPException(status_code=401, detail="authentication required")

    return _resolve


def make_kpi_organization_id_for_request(
    *,
    auth_enabled: bool,
    bootstrap_organization_id: str | None,
) -> Callable[..., str]:
    return _make_requested_org_resolver(
        auth_enabled=auth_enabled,
        bootstrap_organization_id=bootstrap_organization_id,
    )


def make_intent_tags_organization_id_for_request(
    *,
    auth_enabled: bool,
    bootstrap_organization_id: str | None,
) -> Callable[..., str]:
    return _make_requested_org_resolver(
        auth_enabled=auth_enabled,
        bootstrap_organization_id=bootstrap_organization_id,
    )


def _make_required_org_resolver(
    *,
    auth_enabled: bool,
    bootstrap_organization_id: str | None,
) -> Callable[[Request], str]:
    """Required tenant resolver with no request-supplied override
    (journeys, tool-integration webhooks)."""
    _organization_id_for_request = make_organization_id_for_request(
        auth_enabled=auth_enabled,
        bootstrap_organization_id=bootstrap_organization_id,
    )

    def _resolve(request: Request) -> str:
        scoped = _organization_id_for_request(request)
        if scoped is not None:
            return scoped
        if bootstrap_organization_id is not None:
            return bootstrap_organization_id
        raise HTTPException(status_code=401, detail="authentication required")

    return _resolve


def make_journey_organization_id_for_request(
    *,
    auth_enabled: bool,
    bootstrap_organization_id: str | None,
) -> Callable[[Request], str]:
    return _make_required_org_resolver(
        auth_enabled=auth_enabled,
        bootstrap_organization_id=bootstrap_organization_id,
    )


def make_tool_integration_organization_id_for_request(
    *,
    auth_enabled: bool,
    bootstrap_organization_id: str | None,
) -> Callable[[Request], str]:
    return _make_required_org_resolver(
        auth_enabled=auth_enabled,
        bootstrap_organization_id=bootstrap_organization_id,
    )
