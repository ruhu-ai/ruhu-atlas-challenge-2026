"""Composite -> Code codegen prototype.

Goal: prove (or disprove) that we can faithfully translate a
``kind='composite'`` ToolDefinition into ``kind='code'`` Python and run it
through CodeExecutor with the same observable behaviour as CompositeExecutor.

This is a decision-grade prototype, not production code. Run with:

    PYTHONPATH=src python scripts/prototype_composite_to_code.py

Three representative cases:

1. Linear chain with $args + $prev (the canonical "Fetch User Profile"
   shape).
2. Sub-step failure short-circuit (verifies error propagation + metadata).
3. Mixed args: literals + $args + $prev with dotted-path traversal.

The prototype reports per-case:
- status match (success/error)
- output match (deep-equal)
- metadata diff (which keys baseline carries that codegen does not)

Findings inform whether the redesign target is 5 kinds (composite stays) or
4 kinds (composite collapses to code).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Allow running from repo root without -m gymnastics.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ruhu.code_execution import execute_action_code_inline  # noqa: E402
from ruhu.tools.executors.builtin import BuiltinExecutor  # noqa: E402
from ruhu.tools.executors.code import CodeExecutor  # noqa: E402
from ruhu.tools.executors.composite import CompositeExecutor  # noqa: E402
from ruhu.tools.registry import ToolRegistry  # noqa: E402
from ruhu.tools.runtime import ToolRuntime  # noqa: E402
from ruhu.tools.specs import ToolSpec  # noqa: E402
from ruhu.tools.types import ToolCall, ToolCaller, ToolResult  # noqa: E402


# ── Codegen ────────────────────────────────────────────────────────────────

# Maps the composite step's `ref` to a Python identifier safe for the
# RestrictedPython sandbox. Refs can contain dots ("calendar.create_event"),
# which are illegal in Python identifiers, so we normalise to underscores
# and prefix to avoid keyword collisions.
def _ref_to_identifier(ref: str) -> str:
    return "call_" + ref.replace(".", "_").replace("-", "_")


def _arg_expr_to_python(expr: Any) -> str:
    """Translate a composite arg expression into a Python expression string.

    - "$args.<key>"   -> vars.get('<key>')
    - "$prev.<path>"  -> chained .get() with None-safe traversal
    - any other type  -> repr(<literal>)
    """
    if isinstance(expr, str):
        if expr.startswith("$args."):
            key = expr[len("$args."):]
            return f"vars.get({key!r})"
        if expr.startswith("$prev."):
            path = expr[len("$prev."):]
            parts = path.split(".")
            # Build (((prev_state or {}).get('a') or {}).get('b')).get('last')
            # — None-safe across missing intermediate keys.
            inner = "prev_state"
            for idx, part in enumerate(parts):
                if idx < len(parts) - 1:
                    inner = f"({inner} or {{}}).get({part!r})"
                else:
                    inner = f"({inner} or {{}}).get({part!r})"
            return inner
    return repr(expr)


def composite_to_code(steps: list[dict[str, Any]]) -> tuple[str, dict[str, str]]:
    """Generate a Python code body equivalent to the composite step list.

    Returns ``(body, ident_to_ref)`` where ``ident_to_ref`` maps each
    sandbox-bound identifier to the original tool ref (so the executor
    bridge can route invocations back to ``runtime.invoke``).
    """
    ident_to_ref: dict[str, str] = {}
    lines: list[str] = ["prev_state = {}", ""]

    for index, step in enumerate(steps):
        ref = str(step.get("ref") or "").strip()
        if not ref:
            raise ValueError(f"step {index} missing ref")
        ident = _ref_to_identifier(ref)
        ident_to_ref[ident] = ref
        args_dict = step.get("args") if isinstance(step.get("args"), dict) else {}
        kw_pairs = ", ".join(
            f"{key}={_arg_expr_to_python(value)}" for key, value in args_dict.items()
        )
        # Sub-call: any failure raises in the sandbox, which CodeExecutor
        # surfaces as status='error' just like composite short-circuit.
        lines.append(f"# step {index}: {ref}")
        lines.append(f"prev_state = {ident}({kw_pairs})")
        lines.append("")

    lines.append("result = prev_state")
    return "\n".join(lines), ident_to_ref


# ── Wiring: CodeExecutor with sub-callable bridge ──────────────────────────


class _CodeExecutorWithCallables(CodeExecutor):
    """Prototype-only subclass that wires sub-callable names to a runtime.

    Production CodeExecutor passes ``tool_executor=None``; this proves the
    seam exists. The sub-callable bridge converts ``runtime.invoke()``
    results into either a dict (success path) or a raised exception
    (failure path) so the sandbox short-circuits naturally.
    """

    def __init__(self, runtime_provider, ident_to_ref: dict[str, str]) -> None:
        self._runtime_provider = runtime_provider
        self._ident_to_ref = ident_to_ref

    def execute(self, spec: ToolSpec, call: ToolCall) -> ToolResult:
        code_body = str(spec.executor_config.get("code_body") or "")
        runtime = self._runtime_provider()

        def _bridge(ident: str, kwargs: dict[str, Any]) -> dict[str, Any]:
            real_ref = self._ident_to_ref.get(ident, ident)
            sub_call = ToolCall(
                tool_ref=real_ref,
                args=dict(kwargs),
                caller=ToolCaller(
                    channel=call.caller.channel,
                    conversation_id=call.caller.conversation_id,
                    step_id=call.caller.step_id,
                    agent_id=call.caller.agent_id,
                    tenant_id=call.caller.tenant_id,
                    user_id=call.caller.user_id,
                ),
                metadata={**dict(call.metadata), "code_parent_ref": call.tool_ref},
            )
            sub_result = runtime.invoke(sub_call)
            if sub_result.status != "success":
                raise RuntimeError(
                    f"sub-callable {real_ref} failed: {sub_result.error}"
                )
            return dict(sub_result.output)

        sandbox_vars: dict[str, Any] = {**call.args, "args": dict(call.args)}
        result = execute_action_code_inline(
            code=code_body,
            conversation_facts=sandbox_vars,
            callable_function_names=list(self._ident_to_ref.keys()),
            tool_executor=_bridge,
        )

        if result.status == "success":
            output = result.output if isinstance(result.output, dict) else {"value": result.output}
            return ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="success",
                output=output,
                metadata={"code_callables_invoked": len(self._ident_to_ref)},
            )
        return ToolResult(
            invocation_id=call.invocation_id,
            tool_ref=call.tool_ref,
            status="error",
            error=result.error,
            metadata={
                "failure_kind": "permanent_upstream_error",
                "error_type": result.error_type or "code_error",
            },
        )


# ── Test harness ───────────────────────────────────────────────────────────


def _caller() -> ToolCaller:
    return ToolCaller(channel="web_widget", tenant_id="org_test")


def _build_runtime(
    sub_specs: list[ToolSpec],
    composite_spec: ToolSpec,
    code_spec: ToolSpec,
    sub_handlers: dict[str, Any],
    code_ident_to_ref: dict[str, str],
) -> ToolRuntime:
    builtin_executor = BuiltinExecutor(sub_handlers)
    registry = ToolRegistry([*sub_specs, composite_spec, code_spec])
    holder: dict[str, ToolRuntime] = {}
    composite_executor = CompositeExecutor(runtime_provider=lambda: holder["rt"])
    code_executor = _CodeExecutorWithCallables(
        runtime_provider=lambda: holder["rt"],
        ident_to_ref=code_ident_to_ref,
    )
    runtime = ToolRuntime(
        registry,
        executors={
            "builtin": builtin_executor,
            "composite": composite_executor,
            "code": code_executor,
        },
    )
    holder["rt"] = runtime
    return runtime


def _diff_results(baseline: ToolResult, codegen: ToolResult) -> dict[str, Any]:
    return {
        "status_match": baseline.status == codegen.status,
        "baseline_status": baseline.status,
        "codegen_status": codegen.status,
        "baseline_error": baseline.error,
        "codegen_error": codegen.error,
        "output_match": baseline.output == codegen.output,
        "baseline_output": baseline.output,
        "codegen_output": codegen.output,
        "metadata_keys_only_in_baseline": sorted(
            set(baseline.metadata) - set(codegen.metadata)
        ),
        "metadata_keys_only_in_codegen": sorted(
            set(codegen.metadata) - set(baseline.metadata)
        ),
    }


# ── Cases ──────────────────────────────────────────────────────────────────


def case_1_linear_chain() -> dict[str, Any]:
    """Canonical "Fetch User Profile" shape: $args.user_id -> sub.fetch ->
    $prev.profile.tier -> sub.classify."""
    sub_fetch = ToolSpec(
        ref="sub.fetch_profile",
        kind="builtin",
        display_name="Fetch profile",
        description="Returns a nested profile dict for the case-1 prototype.",
        input_schema={
            "type": "object",
            "properties": {"user_id": {"type": "string"}},
            "required": ["user_id"],
            "additionalProperties": False,
        },
        executor_config={},
    )
    sub_classify = ToolSpec(
        ref="sub.classify",
        kind="builtin",
        display_name="Classify tier",
        description="Maps tier string to a label for the case-1 prototype.",
        input_schema={
            "type": "object",
            "properties": {"tier": {"type": "string"}},
            "required": ["tier"],
            "additionalProperties": False,
        },
        executor_config={},
    )
    handlers = {
        "sub.fetch_profile": lambda call, _spec: {
            "profile": {"name": "Ada", "tier": "gold"},
            "id": call.args["user_id"],
        },
        "sub.classify": lambda call, _spec: {
            "label": f"VIP-{call.args['tier'].upper()}",
        },
    }
    steps = [
        {"ref": "sub.fetch_profile", "args": {"user_id": "$args.user_id"}},
        {"ref": "sub.classify", "args": {"tier": "$prev.profile.tier"}},
    ]
    case1_schema = {
        "type": "object",
        "properties": {"user_id": {"type": "string"}},
        "required": ["user_id"],
        "additionalProperties": False,
    }
    composite = ToolSpec(
        ref="case1.composite",
        kind="composite",
        display_name="Case 1 (composite)",
        description="Linear chain — composite baseline for the prototype.",
        input_schema=case1_schema,
        executor_config={"composite_steps": steps},
    )
    code_body, ident_to_ref = composite_to_code(steps)
    code = ToolSpec(
        ref="case1.code",
        kind="code",
        display_name="Case 1 (code)",
        description="Linear chain — codegen output for the prototype.",
        input_schema=case1_schema,
        executor_config={"code_body": code_body},
    )
    runtime = _build_runtime(
        sub_specs=[sub_fetch, sub_classify],
        composite_spec=composite,
        code_spec=code,
        sub_handlers=handlers,
        code_ident_to_ref=ident_to_ref,
    )
    baseline = runtime.invoke(
        ToolCall(tool_ref="case1.composite", args={"user_id": "u_42"}, caller=_caller())
    )
    codegen = runtime.invoke(
        ToolCall(tool_ref="case1.code", args={"user_id": "u_42"}, caller=_caller())
    )
    return {"name": "case_1_linear_chain", "code_body": code_body, **_diff_results(baseline, codegen)}


def case_2_short_circuit_failure() -> dict[str, Any]:
    """Sub-step failure: composite returns status='error' with metadata
    naming the failing step. Codegen should also fail; metadata equivalence
    is the open question."""
    def _boom(call, _spec):
        raise RuntimeError("upstream blew up")

    sub_ok = ToolSpec(
        ref="sub.ok",
        kind="builtin",
        display_name="OK step",
        description="First step succeeds; second is the failure case.",
        input_schema={
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
            "additionalProperties": False,
        },
        executor_config={},
    )
    sub_fail = ToolSpec(
        ref="sub.fail",
        kind="builtin",
        display_name="Failing step",
        description="Always raises to exercise short-circuit metadata.",
        input_schema={
            "type": "object",
            "properties": {"y": {"type": "integer"}},
            "required": ["y"],
            "additionalProperties": False,
        },
        executor_config={},
    )
    handlers = {
        "sub.ok": lambda call, _spec: {"x": call.args.get("x", 0) + 1},
        "sub.fail": _boom,
    }
    steps = [
        {"ref": "sub.ok", "args": {"x": "$args.x"}},
        {"ref": "sub.fail", "args": {"y": "$prev.x"}},
    ]
    case2_schema = {
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
        "additionalProperties": False,
    }
    composite = ToolSpec(
        ref="case2.composite",
        kind="composite",
        display_name="Case 2 (composite)",
        description="Short-circuit failure — composite baseline for the prototype.",
        input_schema=case2_schema,
        executor_config={"composite_steps": steps},
    )
    code_body, ident_to_ref = composite_to_code(steps)
    code = ToolSpec(
        ref="case2.code",
        kind="code",
        display_name="Case 2 (code)",
        description="Short-circuit failure — codegen output for the prototype.",
        input_schema=case2_schema,
        executor_config={"code_body": code_body},
    )
    runtime = _build_runtime(
        sub_specs=[sub_ok, sub_fail],
        composite_spec=composite,
        code_spec=code,
        sub_handlers=handlers,
        code_ident_to_ref=ident_to_ref,
    )
    baseline = runtime.invoke(
        ToolCall(tool_ref="case2.composite", args={"x": 3}, caller=_caller())
    )
    codegen = runtime.invoke(
        ToolCall(tool_ref="case2.code", args={"x": 3}, caller=_caller())
    )
    return {"name": "case_2_short_circuit_failure", "code_body": code_body, **_diff_results(baseline, codegen)}


def case_3_mixed_args() -> dict[str, Any]:
    """Literals + $args + $prev with dotted-path traversal."""
    sub_lookup = ToolSpec(
        ref="sub.lookup",
        kind="builtin",
        display_name="Lookup",
        description="Returns nested data for the mixed-args case.",
        input_schema={
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
            "additionalProperties": False,
        },
        executor_config={},
    )
    sub_format = ToolSpec(
        ref="sub.format",
        kind="builtin",
        display_name="Format",
        description="Formats a message with literal + dynamic args.",
        input_schema={
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "name": {"type": "string"},
                "size": {"type": "integer"},
                "flag": {"type": "boolean"},
            },
            "required": ["channel", "name", "size", "flag"],
            "additionalProperties": False,
        },
        executor_config={},
    )
    handlers = {
        "sub.lookup": lambda call, _spec: {
            "data": {"company": {"name": "Acme", "size": 250}}
        },
        "sub.format": lambda call, _spec: {
            "message": (
                f"channel={call.args['channel']}; "
                f"name={call.args['name']}; "
                f"size={call.args['size']}; "
                f"flag={call.args['flag']}"
            )
        },
    }
    steps = [
        {"ref": "sub.lookup", "args": {"q": "$args.query"}},
        {
            "ref": "sub.format",
            "args": {
                "channel": "email",
                "flag": True,
                "name": "$prev.data.company.name",
                "size": "$prev.data.company.size",
            },
        },
    ]
    case3_schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }
    composite = ToolSpec(
        ref="case3.composite",
        kind="composite",
        display_name="Case 3 (composite)",
        description="Mixed args — composite baseline for the prototype.",
        input_schema=case3_schema,
        executor_config={"composite_steps": steps},
    )
    code_body, ident_to_ref = composite_to_code(steps)
    code = ToolSpec(
        ref="case3.code",
        kind="code",
        display_name="Case 3 (code)",
        description="Mixed args — codegen output for the prototype.",
        input_schema=case3_schema,
        executor_config={"code_body": code_body},
    )
    runtime = _build_runtime(
        sub_specs=[sub_lookup, sub_format],
        composite_spec=composite,
        code_spec=code,
        sub_handlers=handlers,
        code_ident_to_ref=ident_to_ref,
    )
    baseline = runtime.invoke(
        ToolCall(tool_ref="case3.composite", args={"query": "acme"}, caller=_caller())
    )
    codegen = runtime.invoke(
        ToolCall(tool_ref="case3.code", args={"query": "acme"}, caller=_caller())
    )
    return {"name": "case_3_mixed_args", "code_body": code_body, **_diff_results(baseline, codegen)}


# ── Reporter ───────────────────────────────────────────────────────────────


def _print_case(report: dict[str, Any]) -> None:
    name = report["name"]
    print(f"\n========== {name} ==========")
    print(f"  status: baseline={report['baseline_status']:<8s} codegen={report['codegen_status']:<8s} match={report['status_match']}")
    if report["baseline_error"]:
        print(f"    baseline error: {report['baseline_error']}")
    if report["codegen_error"]:
        print(f"    codegen error:  {report['codegen_error']}")
    print(f"  output match: {report['output_match']}")
    if not report["output_match"]:
        print(f"    baseline: {report['baseline_output']}")
        print(f"    codegen:  {report['codegen_output']}")
    if report["metadata_keys_only_in_baseline"]:
        print(f"  metadata only in baseline: {report['metadata_keys_only_in_baseline']}")
    if report["metadata_keys_only_in_codegen"]:
        print(f"  metadata only in codegen:  {report['metadata_keys_only_in_codegen']}")
    print("  --- generated code body ---")
    for line in report["code_body"].splitlines():
        print(f"    {line}")


def main() -> int:
    reports = [
        case_1_linear_chain(),
        case_2_short_circuit_failure(),
        case_3_mixed_args(),
    ]
    for report in reports:
        _print_case(report)

    all_status_match = all(r["status_match"] for r in reports)
    all_output_match = all(r["output_match"] for r in reports)
    print("\n========== verdict ==========")
    print(f"  all status match:  {all_status_match}")
    print(f"  all output match:  {all_output_match}")
    if not all_status_match or not all_output_match:
        print("  -> codegen does NOT preserve composite semantics; keep composite kind")
        return 1
    metadata_gaps = sorted(
        {
            key
            for r in reports
            for key in r["metadata_keys_only_in_baseline"]
        }
    )
    if metadata_gaps:
        print(f"  metadata keys lost in codegen path: {metadata_gaps}")
        print("  -> functional equivalence holds; metadata audit fields would need to be re-emitted by code executor")
    else:
        print("  -> full semantic + metadata equivalence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
