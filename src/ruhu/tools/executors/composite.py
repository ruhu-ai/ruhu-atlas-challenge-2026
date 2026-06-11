"""CompositeExecutor — runs a Library callable that chains other callables.

A ``kind='composite'`` ToolDefinition stores a list of steps in
``metadata_json.composite_steps``. Each step is ``{"ref": str, "args":
{input_key: arg_expr}}`` where ``arg_expr`` is either:

- ``"$args.<key>"``  — pulls from the incoming call's args
- ``"$prev.<path>"`` — pulls from the previous step's output (dotted path)
- a literal value (str / number / bool / dict / list)

Steps run sequentially. The last step's output becomes the composite's
output. Any sub-step failure short-circuits with the failure propagated.

The executor takes a ``ToolRuntime`` reference at construction time (via
a factory — the runtime isn't known when the module is imported). It
invokes sub-callables through ``runtime.invoke(ToolCall(...))`` so the
full authorization / rate-limit / audit pipeline applies to each leg.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from ..specs import ToolSpec
from ..types import ToolCall, ToolCaller, ToolResult

if TYPE_CHECKING:
    from ..runtime import ToolRuntime

log = logging.getLogger(__name__)


class CompositeExecutor:
    kind = "composite"

    def __init__(self, runtime_provider: Callable[[], "ToolRuntime"]) -> None:
        """``runtime_provider`` is a zero-arg callable that returns the
        ``ToolRuntime`` instance. Deferred lookup avoids an import cycle
        (the runtime is constructed with executors in its init)."""
        self._runtime_provider = runtime_provider

    def execute(self, spec: ToolSpec, call: ToolCall) -> ToolResult:
        steps_raw = spec.executor_config.get("composite_steps") or []
        if not isinstance(steps_raw, list) or not steps_raw:
            return ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="error",
                error="composite callable has no steps",
                metadata={"failure_kind": "permanent_upstream_error", "error_type": "empty_composite"},
            )

        runtime = self._runtime_provider()
        prev_output: dict[str, Any] = {}

        for index, step in enumerate(steps_raw):
            if not isinstance(step, dict):
                return self._fail(call, f"composite step {index} is not an object")
            sub_ref = str(step.get("ref") or "").strip()
            if not sub_ref:
                return self._fail(call, f"composite step {index} missing 'ref'")
            sub_args_raw = step.get("args") if isinstance(step.get("args"), dict) else {}
            try:
                sub_args = self._resolve_args(sub_args_raw, incoming_args=call.args, prev_output=prev_output)
            except ValueError as exc:
                return self._fail(call, f"composite step {index} ({sub_ref}): {exc}")

            sub_call = ToolCall(
                tool_ref=sub_ref,
                args=sub_args,
                caller=ToolCaller(
                    channel=call.caller.channel,
                    conversation_id=call.caller.conversation_id,
                    step_id=call.caller.step_id,
                    agent_id=call.caller.agent_id,
                    tenant_id=call.caller.tenant_id,
                    user_id=call.caller.user_id,
                ),
                metadata={**dict(call.metadata), "composite_parent_ref": call.tool_ref, "composite_step_index": index},
            )
            sub_result = runtime.invoke(sub_call)
            if sub_result.status != "success":
                return ToolResult(
                    invocation_id=call.invocation_id,
                    tool_ref=call.tool_ref,
                    status=sub_result.status,
                    error=f"sub-step {index} ({sub_ref}) failed: {sub_result.error}",
                    metadata={
                        **dict(sub_result.metadata),
                        "composite_step_index": index,
                        "composite_sub_ref": sub_ref,
                    },
                )
            prev_output = dict(sub_result.output)

        return ToolResult(
            invocation_id=call.invocation_id,
            tool_ref=call.tool_ref,
            status="success",
            output=prev_output,
            metadata={"composite_steps_run": len(steps_raw)},
        )

    @staticmethod
    def _fail(call: ToolCall, message: str) -> ToolResult:
        return ToolResult(
            invocation_id=call.invocation_id,
            tool_ref=call.tool_ref,
            status="error",
            error=message,
            metadata={"failure_kind": "permanent_upstream_error", "error_type": "composite_misconfigured"},
        )

    @staticmethod
    def _resolve_args(
        raw: dict[str, Any],
        *,
        incoming_args: dict[str, Any],
        prev_output: dict[str, Any],
    ) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        for key, value in raw.items():
            if isinstance(value, str):
                resolved[key] = CompositeExecutor._resolve_scalar(value, incoming_args, prev_output)
            else:
                resolved[key] = value
        return resolved

    @staticmethod
    def _resolve_scalar(value: str, incoming_args: dict[str, Any], prev_output: dict[str, Any]) -> Any:
        if value.startswith("$args."):
            return incoming_args.get(value[len("$args."):])
        if value.startswith("$prev."):
            return CompositeExecutor._traverse(prev_output, value[len("$prev."):])
        return value

    @staticmethod
    def _traverse(source: dict[str, Any], path: str) -> Any:
        current: Any = source
        for part in path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current
