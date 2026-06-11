from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from ruhu.tools.authorizer import DefaultToolAuthorizer
from ruhu.tools.executors.builtin import BuiltinExecutor
from ruhu.tools.executors.http import HttpExecutor
from ruhu.tools.executors.mcp import MCPExecutor
from ruhu.tools.registry import ToolRegistry
from ruhu.tools.runtime import ToolRuntime
from ruhu.tools.specs import ToolAnnotations, ToolSpec
from ruhu.tools.types import ToolCall, ToolCaller


def _spec(**overrides: object) -> ToolSpec:
    data = {
        "ref": "knowledge.lookup",
        "kind": "builtin",
        "display_name": "Knowledge Lookup",
        "description": "Search the configured knowledge source for relevant product facts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    }
    data.update(overrides)
    return ToolSpec.model_validate(data)


def test_runtime_executes_allowed_builtin_tool() -> None:
    spec = _spec()
    registry = ToolRegistry([spec])
    executor = BuiltinExecutor({spec.ref: lambda call, _spec: {"answer": call.args["query"].upper()}})
    runtime = ToolRuntime(registry, executors={"builtin": executor})

    result = runtime.invoke(
        ToolCall(tool_ref=spec.ref, args={"query": "pricing"}, caller=ToolCaller(channel="web_chat"))
    )

    assert result.status == "success"
    assert result.output["answer"] == "PRICING"
    invocation = runtime.store.load(result.invocation_id)
    assert invocation is not None
    assert invocation.status == "completed"


def test_runtime_returns_confirmation_required() -> None:
    spec = _spec(
        ref="crm.delete_contact",
        annotations=ToolAnnotations(destructive=True),
        confirmation="destructive_only",
    )
    registry = ToolRegistry([spec])
    executor = BuiltinExecutor({spec.ref: lambda call, _spec: {"deleted": True}})
    runtime = ToolRuntime(registry, executors={"builtin": executor})

    first = runtime.invoke(
        ToolCall(tool_ref=spec.ref, args={"query": "cust-1"}, caller=ToolCaller(channel="web_chat"))
    )

    assert first.status == "confirmation_required"
    second = runtime.confirm(first.invocation_id)
    assert second.status == "success"
    assert runtime.store.load(first.invocation_id).status == "completed"


def test_runtime_blocks_disallowed_tool() -> None:
    spec = _spec()
    registry = ToolRegistry([spec])
    runtime = ToolRuntime(
        registry,
        authorizer=DefaultToolAuthorizer(blocked_refs={spec.ref}),
        executors={"builtin": BuiltinExecutor({spec.ref: lambda call, _spec: {"ok": True}})},
    )

    result = runtime.invoke(
        ToolCall(tool_ref=spec.ref, args={"query": "pricing"}, caller=ToolCaller(channel="web_chat"))
    )

    assert result.status == "blocked"
    assert result.metadata["failure_kind"] == "authorization_denied"
    assert runtime.store.load(result.invocation_id).status == "blocked"


def test_runtime_marks_timeouts() -> None:
    spec = _spec(timeout_ms=10)
    registry = ToolRegistry([spec])

    def slow_handler(call, _spec):
        time.sleep(0.2)
        return {"ok": True}

    runtime = ToolRuntime(registry, executors={"builtin": BuiltinExecutor({spec.ref: slow_handler})})
    started = time.perf_counter()

    result = runtime.invoke(
        ToolCall(tool_ref=spec.ref, args={"query": "pricing"}, caller=ToolCaller(channel="web_chat"))
    )
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert result.status == "timeout"
    assert result.metadata["failure_kind"] == "timeout"
    assert elapsed_ms < 100
    assert runtime.store.load(result.invocation_id).status == "timed_out"


def test_runtime_strict_output_validation_fails_sync_execution() -> None:
    spec = _spec(
        output_schema={
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
            },
            "required": ["answer"],
            "additionalProperties": False,
        },
        output_validation_mode="strict",
    )
    registry = ToolRegistry([spec])
    executor = BuiltinExecutor({spec.ref: lambda call, _spec: {"answer": 123}})
    runtime = ToolRuntime(registry, executors={"builtin": executor})

    result = runtime.invoke(
        ToolCall(tool_ref=spec.ref, args={"query": "pricing"}, caller=ToolCaller(channel="web_chat"))
    )

    assert result.status == "error"
    assert result.metadata["failure_kind"] == "validation_error"
    assert result.metadata["error_type"] == "output_validation_error"


def test_http_executor_uses_configured_method_and_url() -> None:
    spec = _spec(
        ref="crm.lookup_http",
        kind="http",
        executor_config={"url": "https://example.com/customer", "method": "GET"},
    )
    registry = ToolRegistry([spec])

    class FakeResponse:
        status_code = 200
        text = "ok"

        @staticmethod
        def json():
            return {"customer": "abc"}

    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return FakeResponse()

    client = FakeClient()
    runtime = ToolRuntime(registry, executors={"http": HttpExecutor(client=client)})

    result = runtime.invoke(
        ToolCall(tool_ref=spec.ref, args={"query": "abc"}, caller=ToolCaller(channel="web_chat"))
    )

    assert result.status == "success"
    assert result.output["customer"] == "abc"
    assert client.calls[0][0] == "GET"
    assert client.calls[0][1] == "https://example.com/customer"


def test_http_executor_substitutes_path_parameters_and_removes_them_from_query() -> None:
    spec = _spec(
        ref="calendar.get_event_http",
        kind="http",
        executor_config={"url": "https://example.com/events/{event_id}", "method": "GET"},
        input_schema={
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "include_attendees": {"type": "boolean"},
            },
            "required": ["event_id"],
            "additionalProperties": False,
        },
    )
    registry = ToolRegistry([spec])

    class FakeResponse:
        status_code = 200
        text = "ok"

        @staticmethod
        def json():
            return {"id": "evt_123", "status": "confirmed"}

    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return FakeResponse()

    client = FakeClient()
    runtime = ToolRuntime(registry, executors={"http": HttpExecutor(client=client)})

    with patch("ruhu.tools.url_validator.socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]
        result = runtime.invoke(
            ToolCall(
                tool_ref=spec.ref,
                args={"event_id": "evt_123", "include_attendees": True},
                caller=ToolCaller(channel="web_chat"),
            )
        )

    assert result.status == "success"
    assert client.calls[0][0] == "GET"
    assert client.calls[0][1] == "https://example.com/events/evt_123"
    assert client.calls[0][2]["params"] == {"include_attendees": True}


def test_http_executor_renders_templated_request_parts() -> None:
    spec = _spec(
        ref="crm.search_http",
        kind="http",
        executor_config={
            "url": "https://example.com/customers/{customer_id}",
            "method": "POST",
            "headers": {"X-Base": "1"},
            "headers_template": {"X-Trace": "{{ call.invocation_id }}"},
            "query_template": {"view": "{{ args.view }}"},
            "body_template": {
                "customer_id": "{{ args.customer_id }}",
                "filters": {"segment": "{{ args.segment }}"},
            },
        },
        input_schema={
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "view": {"type": "string"},
                "segment": {"type": "string"},
            },
            "required": ["customer_id", "view", "segment"],
            "additionalProperties": False,
        },
    )
    registry = ToolRegistry([spec])

    class FakeResponse:
        status_code = 200
        text = "ok"

        @staticmethod
        def json():
            return {"ok": True}

    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return FakeResponse()

    client = FakeClient()
    runtime = ToolRuntime(registry, executors={"http": HttpExecutor(client=client)})

    with patch("ruhu.tools.url_validator.socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]
        result = runtime.invoke(
            ToolCall(
                tool_ref=spec.ref,
                args={"customer_id": "cus_123", "view": "full", "segment": "vip"},
                caller=ToolCaller(channel="web_chat"),
            )
        )

    assert result.status == "success"
    method, url, kwargs = client.calls[0]
    assert method == "POST"
    assert url == "https://example.com/customers/cus_123"
    assert kwargs["params"] == {"view": "full"}
    assert kwargs["json"] == {"customer_id": "cus_123", "filters": {"segment": "vip"}}
    assert kwargs["headers"]["X-Base"] == "1"
    assert "X-Trace" in kwargs["headers"]


def test_http_executor_rejects_malformed_url_placeholders() -> None:
    spec = _spec(
        ref="calendar.get_event_bad_url",
        kind="http",
        executor_config={"url": "https://example.com/events/{event-id}", "method": "GET"},
        input_schema={
            "type": "object",
            "properties": {"event_id": {"type": "string"}},
            "required": ["event_id"],
            "additionalProperties": False,
        },
    )
    registry = ToolRegistry([spec])

    class FakeClient:
        def request(self, method, url, **kwargs):
            raise AssertionError("client should not be called when url has unresolved placeholder")

    runtime = ToolRuntime(registry, executors={"http": HttpExecutor(client=FakeClient())})
    result = runtime.invoke(
        ToolCall(
            tool_ref=spec.ref,
            args={"event_id": "evt_123"},
            caller=ToolCaller(channel="web_chat"),
        )
    )
    assert result.status == "error"
    assert result.error is not None and "unresolved placeholder" in result.error


def test_http_executor_returns_error_for_non_2xx_json_response() -> None:
    spec = _spec(
        ref="crm.lookup_http_error",
        kind="http",
        executor_config={"url": "https://example.com/customer", "method": "POST"},
    )
    registry = ToolRegistry([spec])

    class FakeResponse:
        status_code = 500
        text = '{"error":"upstream failed"}'

        @staticmethod
        def json():
            return {"error": "upstream failed"}

    class FakeClient:
        def request(self, method, url, **kwargs):
            return FakeResponse()

    runtime = ToolRuntime(registry, executors={"http": HttpExecutor(client=FakeClient())})

    result = runtime.invoke(
        ToolCall(tool_ref=spec.ref, args={"query": "abc"}, caller=ToolCaller(channel="web_chat"))
    )

    assert result.status == "error"
    assert result.error == "http request failed with status 500: upstream failed"
    assert result.metadata == {
        "failure_kind": "transient_upstream_error",
        "error_type": "http_error",
        "http_status": 500,
        "error_response": {"error": "upstream failed"},
    }
    assert runtime.store.load(result.invocation_id).status == "failed"


def test_http_executor_returns_error_for_non_2xx_text_response() -> None:
    spec = _spec(
        ref="crm.lookup_http_bad_gateway",
        kind="http",
        executor_config={"url": "https://example.com/customer", "method": "POST"},
    )
    registry = ToolRegistry([spec])

    class FakeResponse:
        status_code = 502
        text = "bad gateway"

        @staticmethod
        def json():
            raise ValueError("not json")

    class FakeClient:
        def request(self, method, url, **kwargs):
            return FakeResponse()

    runtime = ToolRuntime(registry, executors={"http": HttpExecutor(client=FakeClient())})

    result = runtime.invoke(
        ToolCall(tool_ref=spec.ref, args={"query": "abc"}, caller=ToolCaller(channel="web_chat"))
    )

    assert result.status == "error"
    assert result.error == "http request failed with status 502: bad gateway"
    assert result.metadata == {
        "failure_kind": "transient_upstream_error",
        "error_type": "http_error",
        "http_status": 502,
        "error_response": {"text": "bad gateway"},
    }
    assert runtime.store.load(result.invocation_id).status == "failed"


def test_mcp_executor_normalizes_adapter_result() -> None:
    spec = _spec(
        ref="browser.lookup",
        kind="mcp",
        executor_config={"server_name": "browser", "tool_name": "lookup"},
    )
    registry = ToolRegistry([spec])

    class FakeAdapter:
        def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
            return {"server": server_name, "tool": tool_name, "arguments": arguments}

    runtime = ToolRuntime(registry, executors={"mcp": MCPExecutor(adapter=FakeAdapter())})

    result = runtime.invoke(
        ToolCall(tool_ref=spec.ref, args={"query": "pricing"}, caller=ToolCaller(channel="browser"))
    )

    assert result.status == "success"
    assert result.output["server"] == "browser"
    assert result.output["tool"] == "lookup"


def test_mcp_executor_supports_stdio_transport_without_adapter() -> None:
    server_script = """
import json
import sys

for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"capabilities": {}}}) + "\\n")
        sys.stdout.flush()
    elif method == "tools/call":
        sys.stdout.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": {
                        "content": [{"type": "text", "text": msg["params"]["arguments"]["query"]}],
                        "isError": False,
                    },
                }
            )
            + "\\n"
        )
        sys.stdout.flush()
    elif method == "tools/list":
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": []}}) + "\\n")
        sys.stdout.flush()
"""
    spec = _spec(
        ref="browser.lookup_stdio",
        kind="mcp",
        executor_config={
            "server_name": "browser",
            "tool_name": "lookup",
            "transport": "stdio",
            "command": sys.executable,
            "args": [
                "-c",
                server_script,
            ],
        },
    )
    registry = ToolRegistry([spec])
    runtime = ToolRuntime(registry, executors={"mcp": MCPExecutor()})

    result = runtime.invoke(
        ToolCall(
            tool_ref=spec.ref,
            args={"query": "pricing"},
            caller=ToolCaller(channel="browser"),
        )
    )

    assert result.status == "success"
    assert result.output["content_text"] == "pricing"


def test_stdio_mcp_timeout_drops_cached_connection() -> None:
    from ruhu.tools.mcp_client import MCPError, MCPServerConfig, mcp_manager

    server_script = r"""
import json
import sys
import time

for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"capabilities": {}}}) + "\n")
        sys.stdout.flush()
    elif method == "tools/call":
        time.sleep(60)
"""
    config = MCPServerConfig(
        name="timeout-test",
        transport="stdio",
        command=sys.executable,
        args=["-c", server_script],
        timeout=0.1,
    )
    mcp_manager.close_all()
    started = time.perf_counter()
    with pytest.raises(MCPError, match="timed out"):
        mcp_manager.call_tool(config, tool_name="hang", arguments={})
    elapsed = time.perf_counter() - started

    assert elapsed < 2.0
    assert "timeout-test" not in mcp_manager._connections  # noqa: SLF001
    mcp_manager.close_all()


def test_runtime_raises_for_unknown_invocation_confirmation() -> None:
    runtime = ToolRuntime(ToolRegistry())
    with pytest.raises(KeyError):
        runtime.confirm("missing")


def test_runtime_allows_cancelling_waiting_confirmation_invocation() -> None:
    spec = _spec(
        ref="crm.delete_contact",
        annotations=ToolAnnotations(destructive=True),
        confirmation="destructive_only",
    )
    registry = ToolRegistry([spec])
    runtime = ToolRuntime(
        registry,
        executors={"builtin": BuiltinExecutor({spec.ref: lambda call, _spec: {"deleted": True}})},
    )

    pending = runtime.invoke(
        ToolCall(tool_ref=spec.ref, args={"query": "cust-1"}, caller=ToolCaller(channel="web_chat"))
    )

    cancelled = runtime.cancel(pending.invocation_id, reason="user declined")

    assert cancelled.status == "cancelled"
    assert cancelled.error == "user declined"
    assert runtime.store.load(pending.invocation_id).status == "cancelled"


def test_runtime_rejects_cancelling_completed_invocation() -> None:
    spec = _spec()
    registry = ToolRegistry([spec])
    runtime = ToolRuntime(
        registry,
        executors={"builtin": BuiltinExecutor({spec.ref: lambda call, _spec: {"ok": True}})},
    )

    result = runtime.invoke(
        ToolCall(tool_ref=spec.ref, args={"query": "pricing"}, caller=ToolCaller(channel="web_chat"))
    )

    with pytest.raises(ValueError, match="cannot be cancelled"):
        runtime.cancel(result.invocation_id)


def test_runtime_expires_waiting_confirmation_before_confirm() -> None:
    spec = _spec(
        ref="crm.delete_contact",
        annotations=ToolAnnotations(destructive=True),
        confirmation="destructive_only",
    )
    registry = ToolRegistry([spec])
    runtime = ToolRuntime(
        registry,
        executors={"builtin": BuiltinExecutor({spec.ref: lambda call, _spec: {"deleted": True}})},
    )

    pending = runtime.invoke(
        ToolCall(
            tool_ref=spec.ref,
            args={"query": "cust-1"},
            caller=ToolCaller(channel="web_chat", conversation_id="conv-expiring"),
        )
    )
    invocation = runtime.store.load(pending.invocation_id)
    assert invocation is not None
    invocation.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    runtime.store.save(invocation)

    with pytest.raises(ValueError, match="expired"):
        runtime.confirm(pending.invocation_id)

    expired = runtime.store.load(pending.invocation_id)
    assert expired is not None
    assert expired.status == "timed_out"
    assert expired.error == "tool confirmation expired"


def test_runtime_list_conversation_invocations_expires_stale_confirmations() -> None:
    spec = _spec(
        ref="crm.delete_contact",
        annotations=ToolAnnotations(destructive=True),
        confirmation="destructive_only",
    )
    registry = ToolRegistry([spec])
    runtime = ToolRuntime(
        registry,
        executors={"builtin": BuiltinExecutor({spec.ref: lambda call, _spec: {"deleted": True}})},
    )

    pending = runtime.invoke(
        ToolCall(
            tool_ref=spec.ref,
            args={"query": "cust-1"},
            caller=ToolCaller(channel="web_chat", conversation_id="conv-expiring"),
        )
    )
    invocation = runtime.store.load(pending.invocation_id)
    assert invocation is not None
    invocation.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    runtime.store.save(invocation)

    items = runtime.list_conversation_invocations("conv-expiring")

    assert len(items) == 1
    assert items[0].status == "timed_out"
    assert items[0].error == "tool confirmation expired"
