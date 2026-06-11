"""Axis 2 of the publish-gate gradient: tool unavailable graceful degradation.

When ``ToolRuntime.invoke()`` is asked to run a ref that doesn't
resolve to any registered or org-configured tool, it must return a
structured error result instead of raising ``KeyError``. The kernel
then routes the error via the existing ``tool_outcome`` transition
system and renders the LLM-driven fallback message — the conversation
continues instead of crashing.

Pairs with Axis 1 (per-tool required/optional flag) per
docs/templates/Template-Required-Tools-Onboarding-Spec.md.
"""
from __future__ import annotations

from ruhu.tools.executors.builtin import BuiltinExecutor
from ruhu.tools.registry import ToolRegistry
from ruhu.tools.runtime import ToolRuntime
from ruhu.tools.types import ToolCall, ToolCaller


def _runtime() -> ToolRuntime:
    """Empty runtime — no built-in specs, no catalog resolver."""
    registry = ToolRegistry([])
    executor = BuiltinExecutor({})
    return ToolRuntime(registry, executors={"builtin": executor})


def _call(ref: str = "ghost.tool") -> ToolCall:
    return ToolCall.model_validate(
        {
            "tool_ref": ref,
            "args": {},
            "caller": ToolCaller(
                channel="web_chat",
                conversation_id="conv-1",
                tenant_id="org-1",
                agent_id="agent-1",
            ),
        }
    )


class TestToolUnavailable:
    def test_invoke_unknown_tool_returns_error_not_keyerror(self) -> None:
        # Pre-Axis-2: KeyError("ghost.tool") propagated up the call
        # stack and crashed the conversation. Now: structured error.
        runtime = _runtime()
        result = runtime.invoke(_call("ghost.tool"))
        assert result.status == "error"
        assert result.tool_ref == "ghost.tool"
        assert "tool not configured" in (result.error or "").lower()

    def test_unavailable_metadata_carries_failure_kind(self) -> None:
        # The kernel + observability layer reads metadata.failure_kind
        # to differentiate this from upstream/validation failures.
        # Authors who wrote `when: tool_outcome:tool_unavailable`
        # transitions can route on it explicitly; everyone else falls
        # through to the existing `error` outcome handler.
        runtime = _runtime()
        result = runtime.invoke(_call("ghost.tool"))
        assert result.metadata.get("failure_kind") == "tool_unavailable"
        assert result.metadata.get("error_type") == "tool_unavailable"
        assert result.metadata.get("tool_ref") == "ghost.tool"

    def test_unavailable_invocation_persisted_for_audit_trail(self) -> None:
        # Ops debugging "why did the conversation degrade?" need to
        # find the unavailable attempt in the per-conversation
        # invocation list. Persistence is best-effort but normally
        # works — the in-memory store always succeeds.
        runtime = _runtime()
        runtime.invoke(_call("ghost.tool"))
        invocations = runtime.list_conversation_invocations("conv-1", organization_id="org-1")
        unavailable = [i for i in invocations if i.tool_ref == "ghost.tool"]
        assert len(unavailable) == 1
        assert unavailable[0].status == "failed"
        assert unavailable[0].metadata.get("failure_kind") == "tool_unavailable"
