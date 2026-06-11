from __future__ import annotations

from typing import Any, Protocol

from ..mcp_client import MCPServerConfig, MCPError, mcp_manager
from ..specs import ToolSpec
from ..types import ToolCall, ToolResult


class MCPAdapter(Protocol):
    def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class MCPExecutor:
    kind = "mcp"

    def __init__(self, adapter: MCPAdapter | None = None) -> None:
        self._adapter = adapter

    def execute(self, spec: ToolSpec, call: ToolCall) -> ToolResult:
        config = dict(spec.executor_config)
        server_name = str(config.get("server_name") or "")
        if not server_name:
            raise ValueError("mcp tool requires executor_config.server_name")
        tool_name = str(config.get("tool_name") or spec.executor_key or spec.ref)
        if self._adapter is not None:
            result = self._adapter.call_tool(server_name, tool_name, call.args)
        else:
            try:
                result = mcp_manager.call_tool(
                    MCPServerConfig.from_executor_config(config),
                    tool_name=tool_name,
                    arguments=call.args,
                )
            except MCPError as exc:
                return ToolResult(
                    invocation_id=call.invocation_id,
                    tool_ref=call.tool_ref,
                    status="error",
                    error=str(exc),
                    metadata={"server_name": server_name, "tool_name": tool_name},
                )
        return ToolResult(
            invocation_id=call.invocation_id,
            tool_ref=call.tool_ref,
            status="success",
            output=dict(result),
            metadata={
                "server_name": server_name,
                "tool_name": tool_name,
                "transport": str(config.get("transport") or "stdio"),
            },
        )
