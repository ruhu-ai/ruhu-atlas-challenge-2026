from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from .atlas_mcp_tools import ATLAS_MCP_TOOL_NAMES
from .atlas_readiness_mcp import AtlasReadinessMCPAdapter
from .atlas_readiness_models import AtlasCancellationToken
from .tools.mcp_client import MCPServerConfig, mcp_manager


@dataclass(frozen=True)
class AtlasADKToolCall:
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AtlasADKRunnerLimits:
    max_tool_calls: int = 16
    max_wall_clock_seconds: float = 60.0


@dataclass(frozen=True)
class AtlasADKRunnerResult:
    status: str
    tool_results: list[dict[str, Any]]
    blocker: str | None = None


class AtlasADKToolAdapter(Protocol):
    server_name: str

    def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...
    def list_tools(self) -> list[dict[str, Any]]: ...


class AtlasInProcessMCPToolAdapter:
    def __init__(self, adapter: AtlasReadinessMCPAdapter) -> None:
        self._adapter = adapter
        self.server_name = adapter.server_name

    def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._adapter.call_tool(server_name, tool_name, arguments)

    def list_tools(self) -> list[dict[str, Any]]:
        from .atlas_mcp_tools import ATLAS_MCP_TOOL_SCHEMAS

        return [dict(tool) for tool in ATLAS_MCP_TOOL_SCHEMAS]


class AtlasExternalMCPToolAdapter:
    """ADK tool adapter that crosses the MCP JSON-RPC boundary."""

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self.server_name = config.name

    def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if server_name and server_name != self.server_name:
            raise ValueError(f"unknown Atlas readiness MCP server: {server_name}")
        return mcp_manager.call_tool(self._config, tool_name=tool_name, arguments=arguments)

    def list_tools(self) -> list[dict[str, Any]]:
        return mcp_manager.list_tools(self._config)


class AtlasADKReadinessRunner:
    """Guarded ADK-style orchestration loop for Atlas readiness.

    Google ADK can own planning, but all platform operations must go through
    MCP tools. This runner is the local safety envelope for that contract.
    """

    def __init__(
        self,
        *,
        mcp_adapter: AtlasReadinessMCPAdapter | AtlasADKToolAdapter | None = None,
        mcp_server_config: MCPServerConfig | None = None,
        limits: AtlasADKRunnerLimits | None = None,
    ) -> None:
        if mcp_adapter is not None and mcp_server_config is not None:
            raise ValueError("provide either mcp_adapter or mcp_server_config, not both")
        if mcp_server_config is not None:
            self._tool_adapter: AtlasADKToolAdapter = AtlasExternalMCPToolAdapter(mcp_server_config)
        elif isinstance(mcp_adapter, AtlasReadinessMCPAdapter):
            self._tool_adapter = AtlasInProcessMCPToolAdapter(mcp_adapter)
        elif mcp_adapter is not None:
            self._tool_adapter = mcp_adapter
        else:
            raise ValueError("AtlasADKReadinessRunner requires an MCP adapter or server config")
        self._limits = limits or AtlasADKRunnerLimits()

    def run_tool_plan(
        self,
        tool_calls: list[AtlasADKToolCall],
        *,
        cancellation_token: AtlasCancellationToken | None = None,
    ) -> AtlasADKRunnerResult:
        started = time.monotonic()
        results: list[dict[str, Any]] = []
        if len(tool_calls) > self._limits.max_tool_calls:
            return AtlasADKRunnerResult(status="failed", tool_results=[], blocker="runaway_orchestrator")
        available_tools = self._available_tool_names()
        for index, call in enumerate(tool_calls, start=1):
            if cancellation_token is not None:
                cancellation_token.throw_if_cancelled()
            if index > self._limits.max_tool_calls:
                return AtlasADKRunnerResult(status="failed", tool_results=results, blocker="runaway_orchestrator")
            if time.monotonic() - started > self._limits.max_wall_clock_seconds:
                return AtlasADKRunnerResult(status="failed", tool_results=results, blocker="timeout_exceeded")
            if call.tool_name not in available_tools:
                return AtlasADKRunnerResult(status="failed", tool_results=results, blocker=f"tool_not_allowed:{call.tool_name}")
            result = self._tool_adapter.call_tool(
                self._tool_adapter.server_name,
                call.tool_name,
                call.arguments,
            )
            results.append({"tool_name": call.tool_name, "result": result})
            if isinstance(result, dict) and result.get("status") == "blocked":
                return AtlasADKRunnerResult(
                    status="blocked",
                    tool_results=results,
                    blocker=str(result.get("error") or "tool_blocked"),
                )
        return AtlasADKRunnerResult(status="completed", tool_results=results)

    def _available_tool_names(self) -> frozenset[str]:
        tools = self._tool_adapter.list_tools()
        names = frozenset(str(tool.get("name") or "") for tool in tools if isinstance(tool, dict))
        return names & ATLAS_MCP_TOOL_NAMES
