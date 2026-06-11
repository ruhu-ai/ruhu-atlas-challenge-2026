"""Extracted routers from the api.py monolith (RP-3.1, in progress).

Every new endpoint goes in a module under this package, never inline in
api.py — the line-budget ratchet enforces that api.py only shrinks. Each
module exposes a ``build_X_router(*, deps...) -> APIRouter`` factory
(``conversations_router.py`` is the template); auth dependencies come from
``ruhu.auth_deps`` factories called inside the builders, and org-scope
resolution from ``ruhu.services.org_scope`` factories.

Schema neutrality (blueprint hazard H1): handler function names are
operation ids — never rename them when moving a route, and never add
``tags=`` or ``prefix=`` to a router whose inline block had none.
"""
