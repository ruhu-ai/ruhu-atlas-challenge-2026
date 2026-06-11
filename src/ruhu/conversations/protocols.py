"""Protocols for async conversation and trace stores.

These protocols are the seam between the Redis hot-store layer and the Postgres
archive layer.  Implementations must be runtime-checkable so that tests can
pass in simple mock classes without inheriting from a concrete base.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ruhu.schemas import ConversationState, TurnTrace


class OptimisticLockError(Exception):
    """Another writer updated this conversation since it was loaded.

    Callers should reload the latest state from the store and retry the turn.
    """


@runtime_checkable
class AsyncConversationStore(Protocol):
    async def load(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> ConversationState | None:
        """Return the current state or None if the conversation does not exist."""
        ...

    async def save(self, state: ConversationState) -> None:
        """Persist state.

        Raises:
            OptimisticLockError: if the stored version has advanced past
                ``state.version`` since the caller loaded it.
        """
        ...

    async def list_conversations(
        self,
        *,
        organization_id: str | None = None,
    ) -> list[ConversationState]:
        """Return all active conversations, optionally scoped to an organisation."""
        ...


@runtime_checkable
class AsyncTraceStore(Protocol):
    async def append(self, trace: TurnTrace) -> None:
        """Record a completed turn trace."""
        ...

    async def by_conversation(
        self,
        conversation_id: str,
        *,
        organization_id: str | None = None,
    ) -> list[TurnTrace]:
        """Return all traces for a conversation, oldest-first."""
        ...
