"""CodeExecutor — runs author-written Python from a Library callable.

A ``kind='code'`` ToolDefinition stores its body in
``metadata_json.code_body``. The compiler folds that body into
``ToolSpec.executor_config['code_body']`` and sets ``ToolSpec.kind='code'``.
This executor reads it back and invokes the existing RestrictedPython
sandbox in a subprocess (``ruhu.code_execution.execute_action_code``).

Sandbox bindings exposed to the body:
- ``vars`` / ``variables`` — merged args dict. A binding's mapped
  ``$facts.<name>`` tokens reach the body as ``vars['<arg_key>']``.
  The full args dict is also available as ``vars['args']``.
- ``result = {...}`` — the value the body assigns becomes ``ToolResult.output``.
- Author-bound callables (e.g. ``get_user_profile``) when the spec declares
  ``callable_refs``: the executor wires each ref into the sandbox under an
  alias name; calling the alias invokes ``runtime.invoke()`` for the
  corresponding ref through the full authorization / rate-limit / audit
  pipeline. Sub-callable failures raise inside the sandbox so the body
  short-circuits the same way ``CompositeExecutor`` does.

Failure semantics mirror existing tool kinds: timeouts return a
``ToolResult.status='timeout'`` with a ``failure_kind='timeout'`` metadata
tag; sandbox errors return ``status='error'`` with the exception type.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from ruhu.code_execution import execute_action_code

from ..specs import ToolSpec
from ..types import ToolCall, ToolCaller, ToolResult

if TYPE_CHECKING:
    from ..runtime import ToolRuntime

log = logging.getLogger(__name__)


# Hard ceiling on nested code/composite callable invocations. The runtime
# stamps each sub-call's metadata with ``_invocation_depth = parent + 1``;
# when a CodeExecutor (or CompositeExecutor) sees the incoming call already
# at the limit, it refuses immediately with a permanent_upstream_error so
# we never hit Python's recursion limit at runtime. 8 is generous for
# real workflows (structured tool chains rarely exceed 3 levels) and tight
# enough that A→B→A loops fail fast.
MAX_INVOCATION_DEPTH = 8


def resolve_callable_aliases(
    refs: list[str],
    explicit: dict[str, str] | None = None,
) -> dict[str, str]:
    """Produce a deterministic ``alias -> ref`` map for the sandbox.

    The author can pin specific aliases via ``explicit`` (typically when
    two refs would otherwise collide on their last dot-segment). Any ref
    not pinned gets the segment after the last ``.`` as its default; if
    that name is already taken, the executor falls back to the underscored
    full ref (``crm.get_user`` → ``crm_get_user``). The same logic must
    run in the UI so the names the author types in the body match the
    names the executor binds — keep this function the single source of
    truth.
    """
    explicit = dict(explicit or {})
    used: set[str] = set(explicit.keys())
    out: dict[str, str] = dict(explicit)
    pinned_refs = set(explicit.values())
    for ref in refs:
        if ref in pinned_refs:
            continue
        segment = ref.rsplit(".", 1)[-1] or ref
        candidate = segment if segment.isidentifier() and not segment.startswith("_") else None
        if candidate is None or candidate in used:
            candidate = ref.replace(".", "_").replace("-", "_")
        if candidate in used:
            # Last-resort disambiguation: append a numeric suffix.
            base = candidate
            counter = 2
            while candidate in used:
                candidate = f"{base}_{counter}"
                counter += 1
        out[candidate] = ref
        used.add(candidate)
    return out


class _DepthExceeded(RuntimeError):
    """Raised when a sub-callable invocation would exceed
    ``MAX_INVOCATION_DEPTH``. Surfaced as a structured ToolResult error
    by the executor that catches it."""


class CodeExecutor:
    kind = "code"

    def __init__(
        self,
        runtime_provider: Callable[[], "ToolRuntime"] | None = None,
    ) -> None:
        """``runtime_provider`` is a zero-arg callable that returns the
        ``ToolRuntime`` instance. It's optional because most code bodies
        don't call other callables — for those we keep the simple
        no-bridge path. When the spec declares ``callable_refs``, the
        provider must be wired or those refs are unreachable."""
        self._runtime_provider = runtime_provider

    def execute(self, spec: ToolSpec, call: ToolCall) -> ToolResult:
        code_body = str(spec.executor_config.get("code_body") or "")
        if not code_body.strip():
            return ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="error",
                error="code callable has empty body",
                metadata={"failure_kind": "permanent_upstream_error", "error_type": "empty_code_body"},
            )

        # Refuse before we even compile if we're already at the depth
        # ceiling — this protects against runaway A→B→A loops where the
        # parent itself is the deepest call we'd accept.
        incoming_depth = int(call.metadata.get("_invocation_depth", 0) or 0)
        if incoming_depth >= MAX_INVOCATION_DEPTH:
            return ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="error",
                error=(
                    f"invocation depth {incoming_depth} reached MAX_INVOCATION_DEPTH "
                    f"={MAX_INVOCATION_DEPTH}; refusing to recurse further"
                ),
                metadata={
                    "failure_kind": "permanent_upstream_error",
                    "error_type": "invocation_depth_exceeded",
                },
            )

        timeout_seconds = max(0.5, (spec.timeout_ms or 30_000) / 1000.0)
        sandbox_vars: dict[str, Any] = {**call.args, "args": dict(call.args)}

        # Build the alias→ref map and the sub-call bridge if the spec
        # opts in. ``callable_refs`` without a wired ``runtime_provider``
        # is a configuration error we surface as a permanent failure so
        # the author sees it instead of getting a silent NameError in
        # the sandbox.
        alias_to_ref: dict[str, str] = {}
        bridge: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None
        if spec.callable_refs:
            if self._runtime_provider is None:
                return ToolResult(
                    invocation_id=call.invocation_id,
                    tool_ref=call.tool_ref,
                    status="error",
                    error=(
                        f"code callable {spec.ref!r} declares callable_refs but "
                        "CodeExecutor was constructed without a runtime_provider"
                    ),
                    metadata={
                        "failure_kind": "permanent_upstream_error",
                        "error_type": "code_executor_misconfigured",
                    },
                )
            alias_to_ref = resolve_callable_aliases(
                spec.callable_refs, spec.callable_aliases
            )
            runtime = self._runtime_provider()
            sub_depth = incoming_depth + 1

            def _bridge(alias: str, kwargs: dict[str, Any]) -> dict[str, Any]:
                target_ref = alias_to_ref.get(alias)
                if target_ref is None:
                    raise RuntimeError(
                        f"sandbox alias {alias!r} is not bound to a callable_ref"
                    )
                if sub_depth > MAX_INVOCATION_DEPTH:
                    raise _DepthExceeded(
                        f"invocation depth {sub_depth} exceeds MAX_INVOCATION_DEPTH "
                        f"={MAX_INVOCATION_DEPTH} for sub-callable {alias!r} ({target_ref!r})"
                    )
                sub_call = ToolCall(
                    tool_ref=target_ref,
                    args=dict(kwargs),
                    caller=ToolCaller(
                        channel=call.caller.channel,
                        conversation_id=call.caller.conversation_id,
                        step_id=call.caller.step_id,
                        agent_id=call.caller.agent_id,
                        tenant_id=call.caller.tenant_id,
                        user_id=call.caller.user_id,
                    ),
                    metadata={
                        **dict(call.metadata),
                        "_invocation_depth": sub_depth,
                        "code_parent_ref": call.tool_ref,
                        "code_parent_alias": alias,
                    },
                )
                sub_result = runtime.invoke(sub_call)
                if sub_result.status != "success":
                    # Surface both alias and ref so authors can correlate
                    # the failure with the line in their body and Library
                    # operators can correlate with the Library entry.
                    raise RuntimeError(
                        f"sub-callable {alias!r} ({target_ref!r}) failed: {sub_result.error}"
                    )
                return dict(sub_result.output)

            bridge = _bridge

        result = execute_action_code(
            code=code_body,
            conversation_facts=sandbox_vars,
            callable_function_names=list(alias_to_ref.keys()),
            tool_executor=bridge,
            timeout_seconds=timeout_seconds,
        )

        if result.status == "success":
            output = result.output if isinstance(result.output, dict) else {"value": result.output}
            metadata: dict[str, Any] = {
                "variables_modified": result.variables_modified,
                "logs": result.logs,
            }
            if alias_to_ref:
                metadata["code_callables_invoked"] = sorted(alias_to_ref.values())
            return ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="success",
                output=output,
                metadata=metadata,
            )
        if result.status == "timeout":
            return ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="timeout",
                error=result.error,
                metadata={"failure_kind": "timeout", "error_type": "timeout"},
            )
        if result.status == "security_violation":
            return ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="error",
                error=result.error,
                metadata={"failure_kind": "permanent_upstream_error", "error_type": "security_violation"},
            )
        # _DepthExceeded raised by the bridge ends up here as a generic
        # error from the sandbox subprocess; the original error type is
        # not preserved across the pipe, so we detect by message instead.
        error_type = result.error_type or "code_error"
        if result.error and "exceeds MAX_INVOCATION_DEPTH" in result.error:
            error_type = "invocation_depth_exceeded"
        return ToolResult(
            invocation_id=call.invocation_id,
            tool_ref=call.tool_ref,
            status="error",
            error=result.error,
            metadata={
                "failure_kind": "permanent_upstream_error",
                "error_type": error_type,
            },
        )
