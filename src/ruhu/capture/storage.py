from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ruhu.schemas import FactUpdate

TURN_CAPTURE_METADATA_KEY = "__ruhu_turn_captures__"
TOOL_CONTEXT_METADATA_KEY = "__ruhu_tool_context__"


@dataclass(slots=True)
class StorageRoutingResult:
    routed_counts: dict[str, int] = field(default_factory=dict)


class StorageRouter:
    """Route non-conversation capture writes into supported runtime stores."""

    def apply(
        self,
        *,
        storage_writes: dict[str, dict[str, FactUpdate]],
        conversation_metadata: dict[str, Any],
        turn_metadata: dict[str, Any],
    ) -> StorageRoutingResult:
        result = StorageRoutingResult()
        for scope, updates_by_name in storage_writes.items():
            if not updates_by_name:
                continue
            if scope == "turn":
                turn_store = dict(turn_metadata.get(TURN_CAPTURE_METADATA_KEY, {}))
                for name, update in updates_by_name.items():
                    turn_store[name] = update.model_dump(mode="json")
                turn_metadata[TURN_CAPTURE_METADATA_KEY] = turn_store
                result.routed_counts[scope] = result.routed_counts.get(scope, 0) + len(updates_by_name)
                continue
            if scope == "tool_context":
                tool_context = dict(conversation_metadata.get(TOOL_CONTEXT_METADATA_KEY, {}))
                for name, update in updates_by_name.items():
                    tool_context[name] = update.value
                conversation_metadata[TOOL_CONTEXT_METADATA_KEY] = tool_context
                result.routed_counts[scope] = result.routed_counts.get(scope, 0) + len(updates_by_name)
                continue
            if scope == "audit_only":
                result.routed_counts[scope] = result.routed_counts.get(scope, 0) + len(updates_by_name)
                continue
            raise ValueError(f"unsupported capture storage scope: {scope}")
        return result
