"""Per-agent tool policy — open-by-default granular RBAC.

An ``AgentToolPolicy`` answers: "Is tool *ref* enabled for agent *agent_id*
in organisation *org_id*?"

Open-by-default semantics:
  - If no policy row exists for a (org, agent, tool_ref) tuple → **allowed**.
  - A row with ``enabled=False`` → **denied**.
  - A row with ``enabled=True`` → **allowed** (explicit override).

This design avoids the support-ticket storm that closed-by-default causes:
newly registered tools work immediately for every agent, and operators only
need to intervene when they want to *restrict* a specific tool.

Two implementations:

1. ``InMemoryAgentToolPolicy`` — for tests and single-process dev.  Stores
   policies in a dict keyed by ``(org_id, agent_id, tool_ref)``.

2. ``CachedAgentToolPolicy`` — wraps any ``AgentToolPolicyBackend`` (e.g.
   a SQLAlchemy query) with a TTL cache so per-request DB lookups are
   avoided.  Cache is keyed ``(org_id, agent_id, tool_ref)`` with a
   configurable TTL (default 60 s).
"""
from __future__ import annotations

import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class AgentToolPolicyBackend(Protocol):
    """Backend protocol for loading per-agent tool policy rows."""

    def is_tool_enabled(
        self,
        *,
        organization_id: str,
        agent_id: str,
        tool_ref: str,
    ) -> bool | None:
        """Return True/False for an explicit policy, or None for no policy (open default)."""
        ...


class InMemoryAgentToolPolicy:
    """Dict-backed policy for tests and dev.

    Only stores explicit deny/allow entries.  Missing keys → allowed (open default).
    """

    def __init__(self) -> None:
        self._policies: dict[tuple[str, str, str], bool] = {}

    def set_policy(
        self,
        *,
        organization_id: str,
        agent_id: str,
        tool_ref: str,
        enabled: bool,
    ) -> None:
        self._policies[(organization_id, agent_id, tool_ref)] = enabled

    def remove_policy(
        self,
        *,
        organization_id: str,
        agent_id: str,
        tool_ref: str,
    ) -> None:
        self._policies.pop((organization_id, agent_id, tool_ref), None)

    def is_tool_enabled(
        self,
        *,
        organization_id: str,
        agent_id: str,
        tool_ref: str,
    ) -> bool | None:
        return self._policies.get((organization_id, agent_id, tool_ref))


class CachedAgentToolPolicy:
    """TTL-cached wrapper around any ``AgentToolPolicyBackend``.

    Avoids per-request database lookups.  Cache entries expire after
    ``ttl_seconds`` (default 60).  Thread-safe for use under asyncio
    (single-writer model — no lock needed for dict reads/writes in CPython).
    """

    def __init__(
        self,
        backend: AgentToolPolicyBackend,
        *,
        ttl_seconds: float = 60.0,
    ) -> None:
        self._backend = backend
        self._ttl = max(1.0, float(ttl_seconds))
        self._cache: dict[tuple[str, str, str], tuple[bool | None, float]] = {}

    def is_tool_enabled(
        self,
        *,
        organization_id: str,
        agent_id: str,
        tool_ref: str,
    ) -> bool | None:
        key = (organization_id, agent_id, tool_ref)
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached is not None:
            value, stored_at = cached
            if now - stored_at < self._ttl:
                return value
        value = self._backend.is_tool_enabled(
            organization_id=organization_id,
            agent_id=agent_id,
            tool_ref=tool_ref,
        )
        self._cache[key] = (value, now)
        return value

    def invalidate(
        self,
        *,
        organization_id: str,
        agent_id: str,
        tool_ref: str,
    ) -> None:
        self._cache.pop((organization_id, agent_id, tool_ref), None)

    def invalidate_all(self) -> None:
        self._cache.clear()
