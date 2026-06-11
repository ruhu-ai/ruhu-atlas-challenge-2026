from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .atlas_mcp_tools import ATLAS_MCP_TOOL_NAMES
from .tools.mcp_client import MCPServerConfig


class AtlasGoogleADKUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class AtlasGoogleADKBundle:
    agent: Any
    toolset: Any


def build_atlas_google_adk_mcp_toolset(mcp_server_config: MCPServerConfig) -> Any:
    """Build a Google ADK MCP toolset for the Atlas readiness MCP server.

    Imports are intentionally local so the base Ruhu runtime does not require
    ADK unless the competition/Google integration path is enabled.
    """

    try:
        from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
        from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams, StdioServerParameters
    except Exception as exc:  # noqa: BLE001 - optional integration boundary.
        raise AtlasGoogleADKUnavailable(
            "google-adk with MCP support is required for the Atlas Google ADK bridge"
        ) from exc

    if mcp_server_config.transport != "stdio":
        raise ValueError("Atlas Google ADK bridge currently requires stdio MCP transport")
    if not mcp_server_config.command:
        raise ValueError("Atlas Google ADK bridge requires MCP stdio command")
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=mcp_server_config.command,
                args=list(mcp_server_config.args),
                env=mcp_server_config.env,
                cwd=mcp_server_config.cwd,
            ),
            timeout=mcp_server_config.timeout,
        ),
        tool_filter=sorted(ATLAS_MCP_TOOL_NAMES),
    )


def build_atlas_google_adk_agent(
    *,
    mcp_server_config: MCPServerConfig,
    model: str = "gemini-2.5-flash",
    name: str = "ruhu_atlas_readiness_agent",
) -> AtlasGoogleADKBundle:
    try:
        from google.adk.agents import Agent
    except Exception as exc:  # noqa: BLE001 - optional integration boundary.
        raise AtlasGoogleADKUnavailable("google-adk is required for the Atlas Google ADK bridge") from exc

    toolset = build_atlas_google_adk_mcp_toolset(mcp_server_config)
    agent = Agent(
        name=name,
        model=model,
        instruction=(
            "You are Ruhu Atlas. Plan bounded readiness work only through the "
            "provided MCP tools. Never mutate an AgentDocument directly; use "
            "typed deltas and review-required patch tools. Treat Google "
            "Speech-to-Text and Text-to-Speech as evaluation-only voice paths."
        ),
        tools=[toolset],
    )
    return AtlasGoogleADKBundle(agent=agent, toolset=toolset)
