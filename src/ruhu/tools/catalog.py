"""ToolCatalogResolver — per-request extension point for custom tool lookup.

The built-in ToolRegistry is append-only and process-local; it holds only the
platform's built-in tool specs.  Customer-defined tools (HTTP endpoints, OAuth
integrations) live in the database and must be resolved per-request so that
organisation-level customisation works without restarting the process.

Usage
-----
Implement ToolCatalogResolver and pass the instance to ToolRuntime:

    class DBToolCatalogResolver:
        def resolve(self, tool_ref, *, organization_id):
            # DB lookup
            ...
        def list_for_organization(self, *, organization_id):
            # DB scan for org
            ...

    runtime = ToolRuntime(registry, catalog_resolver=DBToolCatalogResolver())

Builtin-first rule
------------------
ToolRuntime always checks the ToolRegistry first.  If the ref is found there the
catalog resolver is never called.  This guarantees that built-in tool refs
cannot be shadowed by customer definitions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .specs import ToolSpec

if TYPE_CHECKING:
    from .types import ToolCaller


@runtime_checkable
class ToolCatalogResolver(Protocol):
    """Protocol for resolving custom tool specs at request time.

    The optional ``caller`` parameter carries the actor context for audit
    emission when the resolver decrypts per-connection credentials.  When
    ``caller`` is ``None``, the resolver MUST NOT decrypt credentials —
    this is the list / preview path (UI fetching the tool catalog).  When
    ``caller`` is provided, implementations decrypt as needed and emit one
    ``credential.decrypted`` audit event per decrypt, keyed to the actor.
    """

    def resolve(
        self,
        tool_ref: str,
        *,
        organization_id: str | None,
        caller: "ToolCaller | None" = None,
    ) -> ToolSpec | None:
        """Return the ToolSpec for *tool_ref* scoped to *organization_id*, or None."""
        ...

    def list_for_organization(
        self,
        *,
        organization_id: str | None,
        caller: "ToolCaller | None" = None,
    ) -> list[ToolSpec]:
        """Return all custom tool specs visible to *organization_id*."""
        ...


class NullToolCatalogResolver:
    """No-op resolver — used when no custom tool backend is configured."""

    def resolve(
        self,
        tool_ref: str,
        *,
        organization_id: str | None,  # noqa: ARG002
        caller: "ToolCaller | None" = None,  # noqa: ARG002
    ) -> ToolSpec | None:
        return None

    def list_for_organization(
        self,
        *,
        organization_id: str | None,  # noqa: ARG002
        caller: "ToolCaller | None" = None,  # noqa: ARG002
    ) -> list[ToolSpec]:
        return []


class CompositeToolCatalogResolver:
    """Chains multiple ToolCatalogResolver instances.

    ``resolve()`` returns the first non-None result from the chain — resolvers
    are tried in the order they are passed to the constructor.

    ``list_for_organization()`` merges all resolvers and deduplicates by ref,
    with earlier resolvers taking priority (same builtin-first semantics used
    by ToolRuntime).

    The optional ``caller`` propagates unchanged to each sub-resolver so
    audit events attach to the right actor when any resolver decrypts
    credentials.
    """

    def __init__(self, *resolvers: ToolCatalogResolver) -> None:
        self._resolvers = list(resolvers)

    def resolve(
        self,
        tool_ref: str,
        *,
        organization_id: str | None,
        caller: "ToolCaller | None" = None,
    ) -> ToolSpec | None:
        for resolver in self._resolvers:
            spec = resolver.resolve(
                tool_ref, organization_id=organization_id, caller=caller
            )
            if spec is not None:
                return spec
        return None

    def list_for_organization(
        self,
        *,
        organization_id: str | None,
        caller: "ToolCaller | None" = None,
    ) -> list[ToolSpec]:
        seen: dict[str, ToolSpec] = {}
        for resolver in self._resolvers:
            for spec in resolver.list_for_organization(
                organization_id=organization_id, caller=caller
            ):
                if spec.ref not in seen:
                    seen[spec.ref] = spec
        return list(seen.values())
