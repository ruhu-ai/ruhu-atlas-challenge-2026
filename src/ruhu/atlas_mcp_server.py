from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any, TextIO

from .atlas_mcp_tools import ATLAS_MCP_TOOL_SCHEMAS
from .atlas_readiness_mcp import AtlasReadinessMCPAdapter, AtlasReadinessMCPContext
from .atlas_readiness_service import AtlasReadinessService
from .atlas_readiness_store import SQLAlchemyAtlasReadinessStore
from .atlas_store import SQLAlchemyAtlasStore
from .db import build_session_factory
from .registry import SQLAlchemyAgentRegistry


JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2024-11-05"


@dataclass(frozen=True)
class JsonRpcError:
    code: int
    message: str


class AtlasMCPJsonRpcServer:
    """JSON-RPC MCP server for Atlas readiness tools.

    The server deliberately exposes only the readiness adapter. All state
    changes still flow through Atlas typed deltas and permission gates.
    """

    def __init__(self, adapter: AtlasReadinessMCPAdapter) -> None:
        self._adapter = adapter

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [dict(tool) for tool in ATLAS_MCP_TOOL_SCHEMAS]

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        request_id = message.get("id")
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}

        if not method:
            return self._error_response(request_id, JsonRpcError(-32600, "invalid JSON-RPC request"))

        try:
            if method == "initialize":
                return self._response(
                    request_id,
                    {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": self._adapter.server_name, "version": "0.1.0"},
                    },
                )
            if method == "notifications/initialized":
                return None
            if method == "tools/list":
                return self._response(request_id, {"tools": self.tools})
            if method == "tools/call":
                return self._response(request_id, self._call_tool(params))
            return self._error_response(request_id, JsonRpcError(-32601, f"method not found: {method}"))
        except Exception as exc:  # noqa: BLE001 - JSON-RPC boundary turns all tool failures into protocol errors.
            return self._error_response(request_id, JsonRpcError(-32000, str(exc)))

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        tool_name = str(params.get("name") or "").strip()
        arguments = params.get("arguments")
        if not tool_name:
            raise ValueError("tools/call requires params.name")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise ValueError("tools/call params.arguments must be an object")
        result = self._adapter.call_tool(self._adapter.server_name, tool_name, arguments)
        return {
            "content": [{"type": "text", "text": json.dumps(result, sort_keys=True)}],
            "structuredContent": result,
            "isError": False,
        }

    @staticmethod
    def _response(request_id: object, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}

    @staticmethod
    def _error_response(request_id: object, error: JsonRpcError) -> dict[str, Any]:
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": request_id,
            "error": {"code": error.code, "message": error.message},
        }


def build_atlas_mcp_adapter_from_env() -> AtlasReadinessMCPAdapter:
    database_url = (os.getenv("RUHU_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        raise ValueError("RUHU_DATABASE_URL or DATABASE_URL is required for Atlas MCP server")
    tenant_id = (os.getenv("RUHU_ATLAS_MCP_TENANT_ID") or os.getenv("RUHU_ORGANIZATION_ID") or "").strip()
    if not tenant_id:
        raise ValueError("RUHU_ATLAS_MCP_TENANT_ID or RUHU_ORGANIZATION_ID is required for Atlas MCP server")
    user_id = (os.getenv("RUHU_ATLAS_MCP_USER_ID") or os.getenv("RUHU_USER_ID") or "atlas-mcp").strip()
    run_id = (os.getenv("RUHU_ATLAS_MCP_RUN_ID") or "").strip() or None
    scope = (os.getenv("RUHU_ATLAS_MCP_SCOPE") or "validate").strip() or "validate"
    grant_ids = tuple(
        value.strip()
        for value in (os.getenv("RUHU_ATLAS_MCP_PERMISSION_GRANTS") or "").split(",")
        if value.strip()
    )
    session_factory = build_session_factory(database_url)
    registry = SQLAlchemyAgentRegistry(session_factory)
    atlas_store = SQLAlchemyAtlasStore(session_factory)
    readiness_store = SQLAlchemyAtlasReadinessStore(session_factory)
    service = AtlasReadinessService(
        agent_registry=registry,
        atlas_store=atlas_store,
        readiness_store=readiness_store,
    )
    return AtlasReadinessMCPAdapter(
        service=service,
        agent_registry=registry,
        context=AtlasReadinessMCPContext(
            tenant_id=tenant_id,
            user_id=user_id,
            run_id=run_id,
            scope=scope,
            permission_grant_ids=grant_ids,
        ),
    )


def serve_stdio(
    adapter: AtlasReadinessMCPAdapter,
    *,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
) -> None:
    server = AtlasMCPJsonRpcServer(adapter)
    for line in input_stream:
        raw = line.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
            if not isinstance(message, dict):
                response = AtlasMCPJsonRpcServer._error_response(
                    None, JsonRpcError(-32600, "invalid JSON-RPC request")
                )
            else:
                response = server.handle_message(message)
        except json.JSONDecodeError as exc:
            response = AtlasMCPJsonRpcServer._error_response(None, JsonRpcError(-32700, f"parse error: {exc}"))
        if response is None:
            continue
        output_stream.write(json.dumps(response, separators=(",", ":")) + "\n")
        output_stream.flush()


def main() -> int:
    adapter = build_atlas_mcp_adapter_from_env()
    serve_stdio(adapter)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess smoke.
    raise SystemExit(main())
