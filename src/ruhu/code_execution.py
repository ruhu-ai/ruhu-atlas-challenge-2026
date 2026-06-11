"""Secure Python code execution for authored step code.

Runs authored code in a RestrictedPython sandbox inside a subprocess.
Tool calls from the sandbox are RPC'd back to the parent process for
execution via the ToolRuntime.

Design reference: docs/tooling-and-llm-redesign/Ruhu-Tooling-System-Redesign.md

Security model:
- RestrictedPython compiles code with restricted AST transformations
- Subprocess isolation with memory limits (128MB)
- Timeout enforcement (default 30s)
- No file system, network, or system command access
- Only safe builtins exposed
- Tool calls are RPC'd — the sandbox never gets direct HTTP access
"""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
import traceback
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

_MEMORY_LIMIT_BYTES = 128 * 1024 * 1024  # 128 MB
_MAX_RPC_PAYLOAD_BYTES = 512_000  # 500 KB


# ── Result dataclass ────────────────────────────────────────────────────────


@dataclass(slots=True)
class CodeExecutionResult:
    status: str  # "success" | "error" | "timeout" | "security_violation"
    output: dict[str, Any] | None = None  # the `result` variable from code
    variables_modified: dict[str, Any] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)
    error: str | None = None
    error_type: str | None = None


# ── Subprocess worker ───────────────────────────────────────────────────────


def _apply_resource_limits() -> None:
    """Apply OS-level resource limits to the subprocess (best-effort)."""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (_MEMORY_LIMIT_BYTES, _MEMORY_LIMIT_BYTES))
    except (ImportError, ValueError, OSError):
        pass


def _to_safe_value(value: Any, depth: int = 0) -> Any:
    """Coerce a value to a pickle-safe primitive/container type."""
    if depth > 5:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_to_safe_value(v, depth + 1) for v in value]
    if isinstance(value, tuple):
        return tuple(_to_safe_value(v, depth + 1) for v in value)
    if isinstance(value, dict):
        return {str(k): _to_safe_value(v, depth + 1) for k, v in value.items()}
    return str(value)


def _rpc_payload_size_bytes(payload: Any) -> int:
    try:
        raw = json.dumps(_to_safe_value(payload), separators=(",", ":"), default=str)
    except Exception:
        return _MAX_RPC_PAYLOAD_BYTES + 1
    return len(raw.encode("utf-8"))


def _rpc_payload_too_large(payload: Any) -> bool:
    return _rpc_payload_size_bytes(payload) > _MAX_RPC_PAYLOAD_BYTES


def _make_sandbox_callable(function_name: str, rpc_conn: Any) -> Any:
    """Create a synchronous function that RPCs to the parent for tool execution."""
    def fn(**kwargs):
        payload = {"type": "tool_call", "name": function_name, "args": kwargs}
        if _rpc_payload_too_large(payload):
            raise RuntimeError("rpc_payload_too_large")
        rpc_conn.send(payload)
        response = rpc_conn.recv()
        if response.get("error"):
            raise RuntimeError(f"Tool call failed: {response['error']}")
        return response.get("result", {})
    fn.__name__ = function_name
    return fn


def _run_restricted_inline(
    *,
    code: str,
    vars_dict: dict[str, Any],
    callable_function_names: list[str],
    tool_executor: Any = None,
) -> CodeExecutionResult:
    captured_output: list[str] = []
    try:
        from RestrictedPython import compile_restricted
        from RestrictedPython.Guards import safe_builtins, guarded_iter_unpack_sequence
        from RestrictedPython.Eval import default_guarded_getattr, default_guarded_getitem
    except ImportError:
        return CodeExecutionResult(
            status="security_violation",
            error="RestrictedPython is required but not installed",
        )

    try:
        restricted_builtins = dict(safe_builtins)

        def safe_print(*args, **_kwargs):
            captured_output.append(" ".join(str(a) for a in args))

        def make_inline_callable(function_name: str) -> Any:
            def fn(**kwargs):
                if tool_executor is None:
                    raise RuntimeError(f"No tool executor configured for {function_name}")
                return tool_executor(function_name, kwargs)
            fn.__name__ = function_name
            return fn

        restricted_builtins["print"] = safe_print
        restricted_builtins["_print_"] = safe_print

        import math, datetime as _dt, re as _re, uuid as _uuid, hashlib as _hashlib
        restricted_builtins["math"] = math
        restricted_builtins["datetime"] = _dt
        restricted_builtins["re"] = _re
        restricted_builtins["uuid"] = _uuid
        restricted_builtins["hashlib"] = _hashlib
        restricted_builtins["json"] = json

        shared_vars = dict(vars_dict)
        safe_globs: dict[str, Any] = {
            "__builtins__": restricted_builtins,
            "__name__": "__action_state__",
            "__doc__": None,
            "_getattr_": default_guarded_getattr,
            "_getitem_": default_guarded_getitem,
            "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
            "_getiter_": iter,
            "_write_": lambda x: x,
            "vars": shared_vars,
            "variables": shared_vars,
        }
        for fn_name in callable_function_names:
            safe_globs[fn_name] = make_inline_callable(fn_name)

        local_ns: dict[str, Any] = {}
        byte_code = compile_restricted(code, filename="<action_state>", mode="exec")
        if hasattr(byte_code, "errors") and byte_code.errors:
            return CodeExecutionResult(
                status="security_violation",
                error="; ".join(byte_code.errors),
            )

        compiled = byte_code.code if hasattr(byte_code, "code") else byte_code
        exec(compiled, safe_globs, local_ns)
        modified_vars = {
            k: _to_safe_value(v)
            for k, v in safe_globs.get("vars", {}).items()
            if k not in vars_dict or vars_dict.get(k) != v
        }
        return CodeExecutionResult(
            status="success",
            output=_to_safe_value(local_ns.get("result")),
            variables_modified=modified_vars,
            logs=captured_output,
        )
    except Exception as exc:
        return CodeExecutionResult(
            status="error",
            error=str(exc),
            error_type=type(exc).__name__,
            logs=captured_output,
        )


def _execute_code_worker(
    code: str,
    vars_dict: dict[str, Any],
    callable_function_names: list[str],
    child_conn: Any,
) -> None:
    """Run restricted code in a subprocess with resource limits.

    Communicates with parent via pipe:
    - Sends {"type": "tool_call", ...} for RPC tool calls
    - Receives {"result": ..., "error": ...} for tool results
    - Sends {"type": "done", ...} as final result
    """
    _apply_resource_limits()
    captured_output: list[str] = []

    try:
        from RestrictedPython import compile_restricted
        from RestrictedPython.Guards import safe_builtins, guarded_iter_unpack_sequence
        from RestrictedPython.Eval import default_guarded_getattr, default_guarded_getitem
    except ImportError:
        child_conn.send({
            "type": "done",
            "status": "security_violation",
            "error": "RestrictedPython is required but not installed",
        })
        child_conn.close()
        return

    try:
        restricted_builtins = dict(safe_builtins)

        def safe_print(*args, **_kwargs):
            captured_output.append(" ".join(str(a) for a in args))

        restricted_builtins["print"] = safe_print
        restricted_builtins["_print_"] = safe_print

        # Add safe stdlib modules
        import math, datetime as _dt, re as _re, uuid as _uuid, hashlib as _hashlib
        restricted_builtins["math"] = math
        restricted_builtins["datetime"] = _dt
        restricted_builtins["re"] = _re
        restricted_builtins["uuid"] = _uuid
        restricted_builtins["hashlib"] = _hashlib
        restricted_builtins["json"] = json

        shared_vars = dict(vars_dict)
        safe_globs: dict[str, Any] = {
            "__builtins__": restricted_builtins,
            "__name__": "__action_state__",
            "__doc__": None,
            "_getattr_": default_guarded_getattr,
            "_getitem_": default_guarded_getitem,
            "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
            "_getiter_": iter,
            "_write_": lambda x: x,
            "vars": shared_vars,
            "variables": shared_vars,
        }

        # Inject callable tool functions
        for fn_name in callable_function_names:
            safe_globs[fn_name] = _make_sandbox_callable(fn_name, child_conn)

        local_ns: dict[str, Any] = {}

        byte_code = compile_restricted(code, filename="<action_state>", mode="exec")
        if hasattr(byte_code, "errors") and byte_code.errors:
            child_conn.send({
                "type": "done",
                "status": "security_violation",
                "error": "; ".join(byte_code.errors),
            })
            return

        compiled = byte_code.code if hasattr(byte_code, "code") else byte_code
        exec(compiled, safe_globs, local_ns)

        # Collect modified variables
        modified_vars = {
            k: _to_safe_value(v)
            for k, v in safe_globs.get("vars", {}).items()
            if k not in vars_dict or vars_dict.get(k) != v
        }

        done_payload = {
            "type": "done",
            "status": "success",
            "output": _to_safe_value(local_ns.get("result")),
            "logs": captured_output,
            "vars": modified_vars,
        }
        if _rpc_payload_too_large(done_payload):
            child_conn.send({
                "type": "done",
                "status": "security_violation",
                "error": "rpc_payload_too_large",
                "error_type": "rpc_payload_too_large",
            })
        else:
            child_conn.send(done_payload)
    except Exception as e:
        child_conn.send({
            "type": "done",
            "status": "error",
            "error": str(e),
            "error_type": type(e).__name__,
        })
    finally:
        child_conn.close()


# ── Main executor ───────────────────────────────────────────────────────────


def execute_action_code(
    *,
    code: str,
    callable_functions_code: str = "",
    conversation_facts: dict[str, Any],
    callable_function_names: list[str],
    tool_executor: Any = None,  # callable: (function_name, kwargs) -> result
    timeout_seconds: float = 30.0,
) -> CodeExecutionResult:
    """Execute authored step code in a sandboxed subprocess.

    Parameters
    ----------
    code:
        The main Python code block from the step.
    callable_functions_code:
        Helper function definitions prepended to the code.
    conversation_facts:
        Current conversation facts — exposed as ``vars`` and ``variables``
        in the sandbox.
    callable_function_names:
        List of function names that should be callable from the code.
        Each call is RPC'd to ``tool_executor``.
    tool_executor:
        Callable ``(function_name: str, kwargs: dict) -> dict`` that
        executes tool calls. Called in the parent process when the
        sandbox makes an RPC call.
    timeout_seconds:
        Maximum execution time in seconds.
    """
    # Prepend helper functions
    full_code = f"{callable_functions_code}\n\n{code}" if callable_functions_code.strip() else code

    parent_conn, child_conn = mp.Pipe()

    process = mp.Process(
        target=_execute_code_worker,
        args=(full_code, _to_safe_value(conversation_facts), callable_function_names, child_conn),
        daemon=True,
    )
    process.start()
    child_conn.close()  # Parent doesn't use the child end

    try:
        deadline = timeout_seconds
        while True:
            if not parent_conn.poll(timeout=min(deadline, 1.0)):
                deadline -= 1.0
                if deadline <= 0:
                    process.kill()
                    process.join(timeout=2)
                    return CodeExecutionResult(
                        status="timeout",
                        error=f"Code execution exceeded {timeout_seconds}s timeout",
                    )
                if not process.is_alive():
                    return CodeExecutionResult(
                        status="error",
                        error="Code execution process exited unexpectedly",
                    )
                continue

            message = parent_conn.recv()
            if _rpc_payload_too_large(message):
                process.kill()
                process.join(timeout=2)
                return CodeExecutionResult(
                    status="security_violation",
                    error="rpc_payload_too_large",
                    error_type="rpc_payload_too_large",
                )

            if message.get("type") == "tool_call":
                # RPC from sandbox — execute tool in parent process
                fn_name = message.get("name", "")
                fn_args = message.get("args", {})
                try:
                    if tool_executor is not None:
                        result = tool_executor(fn_name, fn_args)
                        response = {"result": _to_safe_value(result)}
                        if _rpc_payload_too_large(response):
                            parent_conn.send({"error": "rpc_payload_too_large"})
                        else:
                            parent_conn.send(response)
                    else:
                        parent_conn.send({"error": f"No tool executor configured for {fn_name}"})
                except Exception as exc:
                    parent_conn.send({"error": str(exc)})

            elif message.get("type") == "done":
                process.join(timeout=2)
                status = message.get("status", "error")
                if status == "success":
                    return CodeExecutionResult(
                        status="success",
                        output=message.get("output"),
                        variables_modified=message.get("vars", {}),
                        logs=message.get("logs", []),
                    )
                return CodeExecutionResult(
                    status=status,
                    error=message.get("error"),
                    error_type=message.get("error_type"),
                    logs=message.get("logs", []),
                )
            else:
                log.warning("unexpected message from sandbox: %s", message.get("type"))

    except Exception as exc:
        process.kill()
        process.join(timeout=2)
        return CodeExecutionResult(
            status="error",
            error=f"Execution failed: {exc}",
            error_type=type(exc).__name__,
        )
    finally:
        parent_conn.close()


def execute_action_code_inline(
    *,
    code: str,
    callable_functions_code: str = "",
    conversation_facts: dict[str, Any],
    callable_function_names: list[str],
    tool_executor: Any = None,
) -> CodeExecutionResult:
    full_code = f"{callable_functions_code}\n\n{code}" if callable_functions_code.strip() else code
    return _run_restricted_inline(
        code=full_code,
        vars_dict=_to_safe_value(conversation_facts),
        callable_function_names=callable_function_names,
        tool_executor=tool_executor,
    )
