"""Tests for the ``kind='code'`` and ``kind='composite'`` tool executors."""

from __future__ import annotations

import pytest

from ruhu.tools.executors.builtin import BuiltinExecutor
from ruhu.tools.executors.code import CodeExecutor
from ruhu.tools.executors.composite import CompositeExecutor
from ruhu.tools.registry import ToolRegistry
from ruhu.tools.runtime import ToolRuntime
from ruhu.tools.specs import ToolSpec
from ruhu.tools.types import ToolCall, ToolCaller


def _code_spec(*, body: str, ref: str = "calc.add_one", timeout_ms: int = 5_000) -> ToolSpec:
    return ToolSpec(
        ref=ref,
        kind="code",
        display_name="Test Code Callable",
        description="Exercises the CodeExecutor end-to-end through the sandbox.",
        timeout_ms=timeout_ms,
        executor_config={"code_body": body},
    )


def _caller() -> ToolCaller:
    return ToolCaller(channel="web_widget", tenant_id="org_test")


# ── CodeExecutor ────────────────────────────────────────────────────────────


def test_code_executor_success() -> None:
    spec = _code_spec(body="result = {'value': vars['n'] + 1}")
    call = ToolCall(tool_ref=spec.ref, args={"n": 41}, caller=_caller())

    result = CodeExecutor().execute(spec, call)

    assert result.status == "success"
    assert result.output == {"value": 42}


def test_code_executor_reads_args_via_vars() -> None:
    """Body reads individual inputs via ``vars['<key>']`` (consistent with
    per-step action_config convention). The full args dict is also exposed
    as ``vars['args']`` for bodies that want to iterate."""
    spec = _code_spec(body="result = {'direct': vars['x'], 'via_args_dict': vars['args']['x']}")
    call = ToolCall(tool_ref=spec.ref, args={"x": "hello"}, caller=_caller())

    result = CodeExecutor().execute(spec, call)

    assert result.status == "success"
    assert result.output == {"direct": "hello", "via_args_dict": "hello"}


def test_code_executor_empty_body_fails_gracefully() -> None:
    spec = _code_spec(body="")
    call = ToolCall(tool_ref=spec.ref, args={}, caller=_caller())

    result = CodeExecutor().execute(spec, call)

    assert result.status == "error"
    assert result.error is not None and "empty body" in result.error
    assert result.metadata.get("failure_kind") == "permanent_upstream_error"


def test_code_executor_python_exception() -> None:
    spec = _code_spec(body="result = {'v': 1 / 0}")
    call = ToolCall(tool_ref=spec.ref, args={}, caller=_caller())

    result = CodeExecutor().execute(spec, call)

    assert result.status == "error"
    assert result.error is not None


def test_code_executor_non_dict_result_wrapped() -> None:
    """A body that sets ``result`` to a non-dict gets wrapped under 'value'."""
    spec = _code_spec(body="result = vars['n'] * 2")
    call = ToolCall(tool_ref=spec.ref, args={"n": 5}, caller=_caller())

    result = CodeExecutor().execute(spec, call)

    assert result.status == "success"
    assert result.output == {"value": 10}


# ── CompositeExecutor ───────────────────────────────────────────────────────


def test_composite_executor_chains_sub_callables() -> None:
    # Sub-callable A: returns {'doubled': n * 2} for input {n}
    # Sub-callable B: returns {'plus_one': x + 1} for input {x}
    # Composite: $args.n → A.n, then A's 'doubled' → B.x
    sub_a = ToolSpec(
        ref="sub.a",
        kind="builtin",
        display_name="Sub A",
        description="Doubles the n input; used by composite test to chain outputs.",
        input_schema={
            "type": "object",
            "properties": {"n": {"type": "integer"}},
            "required": ["n"],
            "additionalProperties": False,
        },
        executor_config={},
    )
    sub_b = ToolSpec(
        ref="sub.b",
        kind="builtin",
        display_name="Sub B",
        description="Adds one to the x input; used by composite test to chain outputs.",
        input_schema={
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
            "additionalProperties": False,
        },
        executor_config={},
    )

    builtin_executor = BuiltinExecutor({
        "sub.a": lambda call, _spec: {"doubled": call.args["n"] * 2},
        "sub.b": lambda call, _spec: {"plus_one": call.args["x"] + 1},
    })

    composite_spec = ToolSpec(
        ref="calc.chain",
        kind="composite",
        display_name="Chain A then B",
        description="Chains sub.a and sub.b to exercise the composite executor wiring.",
        input_schema={
            "type": "object",
            "properties": {"n": {"type": "integer"}},
            "required": ["n"],
            "additionalProperties": False,
        },
        executor_config={
            "composite_steps": [
                {"ref": "sub.a", "args": {"n": "$args.n"}},
                {"ref": "sub.b", "args": {"x": "$prev.doubled"}},
            ],
        },
    )

    registry = ToolRegistry([sub_a, sub_b, composite_spec])
    runtime_holder: dict[str, ToolRuntime] = {}
    composite_executor = CompositeExecutor(runtime_provider=lambda: runtime_holder["rt"])
    runtime = ToolRuntime(
        registry,
        executors={"builtin": builtin_executor, "composite": composite_executor},
    )
    runtime_holder["rt"] = runtime

    result = runtime.invoke(
        ToolCall(tool_ref="calc.chain", args={"n": 5}, caller=_caller())
    )

    assert result.status == "success", f"expected success, got {result.status}: {result.error}"
    assert result.output == {"plus_one": 11}  # (5 * 2) + 1


def test_composite_executor_short_circuits_on_sub_failure() -> None:
    failing_spec = ToolSpec(
        ref="sub.fail",
        kind="builtin",
        display_name="Sub Fail",
        description="Always raises to exercise composite short-circuit failure propagation.",
        executor_config={},
    )

    def _boom(call, _spec):
        raise RuntimeError("sub-step blew up")

    builtin_executor = BuiltinExecutor({"sub.fail": _boom})

    composite_spec = ToolSpec(
        ref="calc.fails",
        kind="composite",
        display_name="Fails",
        description="Composite whose only step fails, used to test short-circuit propagation.",
        executor_config={"composite_steps": [{"ref": "sub.fail", "args": {}}]},
    )

    registry = ToolRegistry([failing_spec, composite_spec])
    runtime_holder: dict[str, ToolRuntime] = {}
    composite_executor = CompositeExecutor(runtime_provider=lambda: runtime_holder["rt"])
    runtime = ToolRuntime(
        registry,
        executors={"builtin": builtin_executor, "composite": composite_executor},
    )
    runtime_holder["rt"] = runtime

    result = runtime.invoke(
        ToolCall(tool_ref="calc.fails", args={}, caller=_caller())
    )

    assert result.status != "success"
    assert result.metadata.get("composite_sub_ref") == "sub.fail"


# ── output_mapping (compiler + apply) ───────────────────────────────────────


def test_compiler_extracts_output_mapping_from_metadata() -> None:
    """ToolSpecCompiler reads metadata.output_mapping into the spec."""
    from ruhu.tools.compiler import ToolSpecCompiler

    class _FakeDef:
        tool_definition_id = "td_test"
        tool_ref = "code.demo"
        kind = "code"
        display_name = "Demo Code Callable"
        description = "Demonstrates output_mapping wiring through the compiler unit."
        endpoint_path = None
        http_method = "POST"
        input_schema_json: dict | None = None
        output_schema_json: dict | None = None
        timeout_ms = 5_000
        metadata_json = {
            "code_body": "result = {'data': {'user': {'name': 'Ada'}}}",
            "output_mapping": {
                "customer_name": "$.data.user.name",
                "raw_data": "data",
                "noise": 123,  # non-string value — should be dropped
                "": "x",       # empty key — should be dropped
            },
        }

    spec = ToolSpecCompiler().compile(None, _FakeDef())  # type: ignore[arg-type]
    assert spec.kind == "code"
    assert spec.output_mapping == {
        "customer_name": "$.data.user.name",
        "raw_data": "data",
    }


def test_apply_tool_output_mapping_writes_facts() -> None:
    """The kernel helper extracts mapped values into working_facts."""
    from ruhu.kernel import ConversationKernel

    output = {"data": {"user": {"name": "Ada", "tier": "gold"}}, "id": "u_42"}
    working_facts: dict = {"existing": "keep_me"}

    ConversationKernel._apply_tool_output_mapping(
        output_mapping={
            "customer_name": "$.data.user.name",
            "tier": "$.data.user.tier",
            "user_id": "id",
            "missing": "$.no.such.path",  # silently skipped
        },
        output=output,
        working_facts=working_facts,
    )

    assert working_facts == {
        "existing": "keep_me",
        "customer_name": "Ada",
        "tier": "gold",
        "user_id": "u_42",
    }


def test_apply_tool_output_mapping_no_op_on_empty() -> None:
    from ruhu.kernel import ConversationKernel

    facts: dict = {"x": 1}
    ConversationKernel._apply_tool_output_mapping(
        output_mapping={},
        output={"a": 1},
        working_facts=facts,
    )
    assert facts == {"x": 1}


# ── CodeExecutor sub-callable bridge ────────────────────────────────────────


def _builtin_sub(ref: str, *, returns: dict[str, object]) -> ToolSpec:
    return ToolSpec(
        ref=ref,
        kind="builtin",
        display_name=f"sub {ref}",
        description="Builtin sub-callable used by CodeExecutor bridge tests.",
        input_schema={
            "type": "object",
            "additionalProperties": True,
        },
        executor_config={},
    )


def test_code_executor_invokes_sub_callable_via_bridge() -> None:
    sub = _builtin_sub("crm.get_user", returns={})
    code = ToolSpec(
        ref="code.fetch_user_profile",
        kind="code",
        display_name="Fetch user profile",
        description="Calls the CRM sub-callable through the CodeExecutor sandbox bridge.",
        input_schema={
            "type": "object",
            "properties": {"user_id": {"type": "string"}},
            "required": ["user_id"],
            "additionalProperties": False,
        },
        executor_config={
            "code_body": "result = get_user(user_id=vars.get('user_id'))"
        },
        callable_refs=["crm.get_user"],
    )
    builtin_executor = BuiltinExecutor(
        {"crm.get_user": lambda call, _spec: {"name": "Ada", "id": call.args["user_id"]}}
    )
    registry = ToolRegistry([sub, code])
    holder: dict[str, ToolRuntime] = {}
    runtime = ToolRuntime(
        registry,
        executors={
            "builtin": builtin_executor,
            "code": CodeExecutor(runtime_provider=lambda: holder["rt"]),
        },
    )
    holder["rt"] = runtime

    result = runtime.invoke(
        ToolCall(tool_ref="code.fetch_user_profile", args={"user_id": "u_42"}, caller=_caller())
    )

    assert result.status == "success", f"got {result.status}: {result.error}"
    assert result.output == {"name": "Ada", "id": "u_42"}
    assert result.metadata.get("code_callables_invoked") == ["crm.get_user"]


def test_code_executor_short_circuits_on_sub_failure() -> None:
    def _boom(call, _spec):
        raise RuntimeError("upstream blew up")

    sub = _builtin_sub("crm.get_user", returns={})
    code = ToolSpec(
        ref="code.fetch_user_profile",
        kind="code",
        display_name="Fetch user profile",
        description="Sub-callable raises; sandbox should propagate as code error.",
        input_schema={
            "type": "object",
            "properties": {"user_id": {"type": "string"}},
            "required": ["user_id"],
            "additionalProperties": False,
        },
        executor_config={
            "code_body": "result = get_user(user_id=vars.get('user_id'))"
        },
        callable_refs=["crm.get_user"],
    )
    builtin_executor = BuiltinExecutor({"crm.get_user": _boom})
    registry = ToolRegistry([sub, code])
    holder: dict[str, ToolRuntime] = {}
    runtime = ToolRuntime(
        registry,
        executors={
            "builtin": builtin_executor,
            "code": CodeExecutor(runtime_provider=lambda: holder["rt"]),
        },
    )
    holder["rt"] = runtime

    result = runtime.invoke(
        ToolCall(tool_ref="code.fetch_user_profile", args={"user_id": "u_42"}, caller=_caller())
    )

    assert result.status == "error"
    assert result.error is not None
    assert "get_user" in result.error and "crm.get_user" in result.error


def test_code_executor_blocks_oversized_sandbox_output() -> None:
    spec = _code_spec(body="result = {'blob': 'x' * 600000}")
    call = ToolCall(tool_ref=spec.ref, args={}, caller=_caller())

    result = CodeExecutor().execute(spec, call)

    assert result.status == "error"
    assert result.error == "rpc_payload_too_large"
    assert result.metadata.get("error_type") == "security_violation"


def test_code_executor_blocks_oversized_sub_callable_payload() -> None:
    sub = _builtin_sub("crm.get_user", returns={})
    code = ToolSpec(
        ref="code.too_large",
        kind="code",
        display_name="Oversized bridge payload",
        description="Attempts to send an oversized sub-callable payload through the sandbox bridge.",
        executor_config={
            "code_body": "result = get_user(blob='x' * 600000)"
        },
        callable_refs=["crm.get_user"],
    )
    builtin_executor = BuiltinExecutor({"crm.get_user": lambda call, _spec: {"ok": True}})
    registry = ToolRegistry([sub, code])
    holder: dict[str, ToolRuntime] = {}
    runtime = ToolRuntime(
        registry,
        executors={
            "builtin": builtin_executor,
            "code": CodeExecutor(runtime_provider=lambda: holder["rt"]),
        },
    )
    holder["rt"] = runtime

    result = runtime.invoke(ToolCall(tool_ref=code.ref, args={}, caller=_caller()))

    assert result.status == "error"
    assert result.error is not None and "rpc_payload_too_large" in result.error


def test_code_executor_depth_exceeded_at_entry() -> None:
    code = ToolSpec(
        ref="code.deep",
        kind="code",
        display_name="Deep code",
        description="Asserts the depth guard refuses entry once parent recursed enough.",
        executor_config={"code_body": "result = {'value': 1}"},
    )
    registry = ToolRegistry([code])
    holder: dict[str, ToolRuntime] = {}
    runtime = ToolRuntime(
        registry,
        executors={"code": CodeExecutor(runtime_provider=lambda: holder["rt"])},
    )
    holder["rt"] = runtime

    from ruhu.tools.executors.code import MAX_INVOCATION_DEPTH

    # Stamp the incoming call with a depth already at the ceiling.
    result = runtime.invoke(
        ToolCall(
            tool_ref="code.deep",
            args={},
            caller=_caller(),
            metadata={"_invocation_depth": MAX_INVOCATION_DEPTH},
        )
    )

    assert result.status == "error"
    assert result.metadata.get("error_type") == "invocation_depth_exceeded"


def test_code_executor_depth_guard_breaks_recursive_loop() -> None:
    """A→B→A loop: code.a calls code.b which calls code.a again. Without
    the depth guard this would blow Python's recursion limit; with it,
    the guard fails the deepest call and the failure propagates back up
    as ``invocation_depth_exceeded``."""
    # 60s timeout per call: depth-8 chain spawns 8 subprocesses (~0.5s each)
    # plus pipe RPC overhead, well over the 3s default. The test is about
    # the guard firing, not about timing.
    a = ToolSpec(
        ref="code.a",
        kind="code",
        display_name="A",
        description="Recursive partner with code.b for the depth guard test.",
        timeout_ms=60_000,
        executor_config={"code_body": "result = b()"},
        callable_refs=["code.b"],
    )
    b = ToolSpec(
        ref="code.b",
        kind="code",
        display_name="B",
        description="Recursive partner with code.a for the depth guard test.",
        timeout_ms=60_000,
        executor_config={"code_body": "result = a()"},
        callable_refs=["code.a"],
    )
    registry = ToolRegistry([a, b])
    holder: dict[str, ToolRuntime] = {}
    runtime = ToolRuntime(
        registry,
        executors={"code": CodeExecutor(runtime_provider=lambda: holder["rt"])},
    )
    holder["rt"] = runtime

    result = runtime.invoke(ToolCall(tool_ref="code.a", args={}, caller=_caller()))

    assert result.status == "error"
    assert result.error is not None
    # The deepest call surfaces "invocation_depth_exceeded" through the
    # ToolResult metadata; intermediate frames re-raise the message so we
    # only need the parent's metadata to confirm the guard fired.
    assert "MAX_INVOCATION_DEPTH" in result.error or result.metadata.get(
        "error_type"
    ) == "invocation_depth_exceeded"


def test_code_executor_alias_resolution_for_colliding_refs() -> None:
    """Two refs with the same last-segment (``crm.get_user``,
    ``banking.get_user``) need disambiguated aliases. Author pins one
    alias explicitly; the other gets the deterministic fallback."""
    crm = _builtin_sub("crm.get_user", returns={})
    bank = _builtin_sub("banking.get_user", returns={})
    code = ToolSpec(
        ref="code.merge_users",
        kind="code",
        display_name="Merge users",
        description="Calls both colliding sub-callables and merges their outputs.",
        input_schema={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"], "additionalProperties": False},
        executor_config={
            "code_body": (
                "crm_user = get_user(id=vars.get('id'))\n"
                "bank_user = banking_get_user(id=vars.get('id'))\n"
                "result = {'crm': crm_user['source'], 'bank': bank_user['source']}"
            )
        },
        callable_refs=["crm.get_user", "banking.get_user"],
        callable_aliases={"get_user": "crm.get_user"},
    )
    builtin_executor = BuiltinExecutor(
        {
            "crm.get_user": lambda call, _spec: {"source": "crm"},
            "banking.get_user": lambda call, _spec: {"source": "bank"},
        }
    )
    registry = ToolRegistry([crm, bank, code])
    holder: dict[str, ToolRuntime] = {}
    runtime = ToolRuntime(
        registry,
        executors={
            "builtin": builtin_executor,
            "code": CodeExecutor(runtime_provider=lambda: holder["rt"]),
        },
    )
    holder["rt"] = runtime

    result = runtime.invoke(
        ToolCall(tool_ref="code.merge_users", args={"id": "u_42"}, caller=_caller())
    )

    assert result.status == "success", f"got {result.status}: {result.error}"
    assert result.output == {"crm": "crm", "bank": "bank"}


def test_code_executor_callable_refs_without_runtime_provider_fails_loudly() -> None:
    code = ToolSpec(
        ref="code.fetch_user_profile",
        kind="code",
        display_name="Fetch user profile",
        description="Spec declares callable_refs but executor wasn't given a runtime_provider.",
        executor_config={
            "code_body": "result = get_user(user_id='u_42')"
        },
        callable_refs=["crm.get_user"],
    )

    # Direct call; no runtime needed because the executor refuses early.
    result = CodeExecutor().execute(
        spec=code,
        call=ToolCall(tool_ref=code.ref, args={}, caller=_caller()),
    )

    assert result.status == "error"
    assert result.metadata.get("error_type") == "code_executor_misconfigured"


# ── Spec validator ─────────────────────────────────────────────────────────


def test_tool_spec_rejects_callable_refs_on_non_code_kind() -> None:
    with pytest.raises(ValueError, match="callable_refs/callable_aliases"):
        ToolSpec(
            ref="api.something",
            kind="builtin",
            display_name="Builtin",
            description="Builtin kinds must not declare callable_refs — bridge is code-only.",
            callable_refs=["crm.get_user"],
        )


def test_tool_spec_rejects_alias_pointing_at_undeclared_ref() -> None:
    with pytest.raises(ValueError, match="not in callable_refs"):
        ToolSpec(
            ref="code.x",
            kind="code",
            display_name="X",
            description="Alias points at a ref that isn't declared in callable_refs.",
            executor_config={"code_body": "result = {}"},
            callable_refs=["crm.get_user"],
            callable_aliases={"get_user": "banking.get_user"},
        )


def test_tool_spec_rejects_invalid_alias_identifier() -> None:
    with pytest.raises(ValueError, match="must be a valid Python identifier"):
        ToolSpec(
            ref="code.x",
            kind="code",
            display_name="X",
            description="Alias is not a valid Python identifier — bridge would NameError.",
            executor_config={"code_body": "result = {}"},
            callable_refs=["crm.get_user"],
            callable_aliases={"_get_user": "crm.get_user"},
        )


# ── alias resolution helper ────────────────────────────────────────────────


def test_resolve_callable_aliases_falls_back_to_last_segment() -> None:
    from ruhu.tools.executors.code import resolve_callable_aliases

    out = resolve_callable_aliases(["crm.get_user", "banking.create_account"])
    assert out == {"get_user": "crm.get_user", "create_account": "banking.create_account"}


def test_resolve_callable_aliases_disambiguates_colliding_segments() -> None:
    from ruhu.tools.executors.code import resolve_callable_aliases

    out = resolve_callable_aliases(["crm.get_user", "banking.get_user"])
    # First one keeps the short name; second falls back to the underscored form.
    assert out["get_user"] == "crm.get_user"
    assert out["banking_get_user"] == "banking.get_user"


def test_resolve_callable_aliases_respects_explicit_pin() -> None:
    from ruhu.tools.executors.code import resolve_callable_aliases

    out = resolve_callable_aliases(
        ["crm.get_user", "banking.get_user"],
        explicit={"get_user": "banking.get_user"},
    )
    assert out["get_user"] == "banking.get_user"
    assert out["crm_get_user"] == "crm.get_user"


def test_resolve_callable_aliases_numeric_suffix_when_both_forms_pinned() -> None:
    """When both the short alias AND the underscored full ref are pinned,
    the numeric-suffix path kicks in. This is the only way to exercise
    that branch — without explicit pins, refs are unique enough that
    underscored forms collide-resolve cleanly."""
    from ruhu.tools.executors.code import resolve_callable_aliases

    out = resolve_callable_aliases(
        ["ns.act"],
        explicit={"act": "other.act", "ns_act": "third.ns_act"},
    )
    assert out["act"] == "other.act"
    assert out["ns_act"] == "third.ns_act"
    assert out["ns_act_2"] == "ns.act"


# ── Compiler round-trip ────────────────────────────────────────────────────


def test_compiler_extracts_callable_refs_and_aliases_from_metadata() -> None:
    from ruhu.tools.compiler import ToolSpecCompiler

    class _FakeDef:
        tool_definition_id = "td_test"
        tool_ref = "code.demo"
        kind = "code"
        display_name = "Demo Code Callable"
        description = "Compiler must round-trip callable_refs + callable_aliases."
        endpoint_path = None
        http_method = "POST"
        input_schema_json: dict | None = None
        output_schema_json: dict | None = None
        timeout_ms = 5_000
        metadata_json = {
            "code_body": "result = get_user(id='x')",
            "callable_refs": ["crm.get_user", "banking.get_user", "  ", 123],
            "callable_aliases": {
                "get_user": "crm.get_user",
                "banking_get_user": "banking.get_user",
                "": "ignored",
            },
        }

    spec = ToolSpecCompiler().compile(None, _FakeDef())  # type: ignore[arg-type]
    assert spec.callable_refs == ["crm.get_user", "banking.get_user"]
    assert spec.callable_aliases == {
        "get_user": "crm.get_user",
        "banking_get_user": "banking.get_user",
    }


def test_composite_executor_empty_steps_returns_error() -> None:
    empty_spec = ToolSpec(
        ref="calc.empty",
        kind="composite",
        display_name="Empty Composite",
        description="Composite with no steps configured; used to test the guard path.",
        executor_config={"composite_steps": []},
    )

    registry = ToolRegistry([empty_spec])
    runtime_holder: dict[str, ToolRuntime] = {}
    composite_executor = CompositeExecutor(runtime_provider=lambda: runtime_holder["rt"])
    runtime = ToolRuntime(registry, executors={"composite": composite_executor})
    runtime_holder["rt"] = runtime

    result = runtime.invoke(
        ToolCall(tool_ref="calc.empty", args={}, caller=_caller())
    )

    assert result.status == "error"
    assert result.error is not None and "no steps" in result.error
