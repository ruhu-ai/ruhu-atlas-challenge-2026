"""
PEP 563 guard tests — prevent the "FastAPI treats Request/Response as query params" trap.

### The trap

When a module uses ``from __future__ import annotations``, all parameter annotations
become strings. FastAPI calls ``typing.get_type_hints()`` to resolve those strings
when inspecting dependency / route handler signatures. That resolver looks up names
in the function's ``__globals__`` (the module's globals) — NOT in the function's
enclosing scope.

If a factory function imports FastAPI types *inside its body* and then defines a
nested handler whose annotations reference those types, the nested handler's
``__globals__`` (still the module's globals) won't have them, and
``get_type_hints()`` either raises ``NameError`` or silently falls back to treating
the parameter as an unknown type — which FastAPI then maps to a Query parameter.

The bug manifested in ``rate_limit.py:make_org_rate_limiter`` — GET /conversations
returned 422 because ``Request`` and ``Response`` were being interpreted as
required query-string params. We fixed it by hoisting those imports to module
top-level. These tests guard against regressions and catch the same pattern
anywhere else in the codebase.

### What these tests check

For each known factory that returns a ``Depends(...)`` or an ``APIRouter``:
1. Instantiate the factory with innocuous arguments (mocks where needed)
2. For every dependency callable and every route endpoint, call
   ``typing.get_type_hints(fn)`` and assert it doesn't raise ``NameError``

If a future factory re-introduces the trap (local import of a type used in nested
annotations), one of these tests will fail loudly at CI time instead of at
first-request time in production.
"""
from __future__ import annotations

import typing
from unittest.mock import MagicMock

import pytest
from fastapi import APIRouter


def _assert_type_hints_resolvable(fn, label: str) -> None:
    """Fail with a clear message if typing.get_type_hints raises on *fn*."""
    try:
        typing.get_type_hints(fn)
    except NameError as exc:
        pytest.fail(
            f"{label}: typing.get_type_hints() failed — this is the PEP 563 "
            f"trap. Hoist the offending import from the factory body to the "
            f"module top level. Underlying error: {exc}"
        )


def _check_router_routes(router: APIRouter, source_label: str) -> None:
    """Verify every route endpoint + dependency in *router* has resolvable hints."""
    for route in router.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is not None:
            _assert_type_hints_resolvable(
                endpoint, f"{source_label}: endpoint {route.path!r}"
            )
        # Also check dependencies attached at router or route level
        dependencies = getattr(route, "dependencies", None) or []
        for idx, dep in enumerate(dependencies):
            callable_ = getattr(dep, "dependency", None)
            if callable_ is not None:
                _assert_type_hints_resolvable(
                    callable_, f"{source_label}: route {route.path!r} dep #{idx}"
                )


# ─── make_org_rate_limiter (the known case) ──────────────────────────────────

def test_make_org_rate_limiter_signature_resolves() -> None:
    """The fix that prompted this test file — guard against regression."""
    from ruhu.rate_limit import make_org_rate_limiter

    # With auth disabled shape (no billing_store) — still constructs the dep
    dep = make_org_rate_limiter(redis_url=None, billing_store=MagicMock(), bypass_secret=None)
    _assert_type_hints_resolvable(
        dep.dependency, "make_org_rate_limiter._rate_limit"
    )


# ─── build_conversations_router ──────────────────────────────────────────────

def test_build_conversations_router_signatures_resolve() -> None:
    from ruhu.conversations_router import build_conversations_router

    router = build_conversations_router(
        conversation_store=MagicMock(),
        trace_store=MagicMock(),
        agent_registry=MagicMock(),
        agent_summary_fn=lambda r, organization_id=None: None,
        get_organization_id=lambda r: None,
    )
    _check_router_routes(router, "build_conversations_router")


# ─── install_* factories that accept only mockable deps ──────────────────────
#
# These are the install_* functions that we can exercise without a real DB /
# runtime. They cover ~70% of extracted router code; the others (knowledge,
# billing, kpi, etc.) require heavier scaffolding and are left to the general
# build_default_app test in tests/test_api.py — which exercises all routes.

def test_install_notifications_router_signatures_resolve() -> None:
    from fastapi import FastAPI
    from ruhu.notifications_api import install_notifications_router

    app = FastAPI()
    install_notifications_router(app, notification_store=MagicMock())
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is not None:
            _assert_type_hints_resolvable(
                endpoint, f"install_notifications_router: endpoint {route.path!r}"
            )


def test_install_rules_router_signatures_resolve() -> None:
    from fastapi import FastAPI
    from ruhu.rules_api import install_rules_router

    app = FastAPI()
    install_rules_router(app, runtime=None)  # runtime=None is a valid default
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is not None:
            _assert_type_hints_resolvable(
                endpoint, f"install_rules_router: endpoint {route.path!r}"
            )


def test_install_knowledge_router_signatures_resolve() -> None:
    from fastapi import FastAPI
    from ruhu.knowledge_api import install_knowledge_router

    app = FastAPI()
    install_knowledge_router(
        app,
        runtime=None,
        resolve_organization_id=lambda request, org=None: "test-org",
    )
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is not None:
            _assert_type_hints_resolvable(
                endpoint, f"install_knowledge_router: endpoint {route.path!r}"
            )


def test_install_kpi_router_signatures_resolve() -> None:
    from fastapi import FastAPI
    from ruhu.kpi_api import install_kpi_router

    app = FastAPI()
    install_kpi_router(
        app,
        runtime=None,
        resolve_organization_id=lambda request, org=None: "test-org",
    )
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is not None:
            _assert_type_hints_resolvable(
                endpoint, f"install_kpi_router: endpoint {route.path!r}"
            )


# ─── Cross-module annotation check ───────────────────────────────────────────

def test_no_local_import_pep563_trap_in_known_factories() -> None:
    """Static check: the factories we know about must not locally-import a type
    that also appears in a nested function's annotation.

    This is the "belt and braces" static complement to the dynamic checks
    above. If typing.get_type_hints() happens to succeed for some reason (e.g.,
    because the name was accidentally available in globals), this check still
    catches the code-smell pattern.
    """
    import inspect
    import re

    from ruhu import rate_limit
    from ruhu import conversations_router

    for module in (rate_limit, conversations_router):
        source = inspect.getsource(module)
        # Heuristic: flag `from fastapi import ... Request, Response ...` that
        # appears INSIDE a function body (indented). The rate_limit.py bug
        # was exactly this shape.
        #
        # Match: indented `from fastapi import ...` lines
        indented_fastapi_imports = re.findall(
            r"^\s{4,}from fastapi import .*\b(Request|Response)\b",
            source,
            flags=re.MULTILINE,
        )
        assert not indented_fastapi_imports, (
            f"{module.__name__} has `from fastapi import ... Request/Response` "
            f"inside a function body. This is the PEP 563 trap that caused the "
            f"rate_limit.py 422 bug. Move those imports to module top-level."
        )
