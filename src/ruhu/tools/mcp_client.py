from __future__ import annotations

import hashlib
import json
import selectors
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any

import httpx


JSONRPC_VERSION = "2.0"


class MCPError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    name: str
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0

    @classmethod
    def from_executor_config(cls, data: dict[str, Any]) -> "MCPServerConfig":
        transport = str(data.get("transport") or "stdio").strip().lower()
        return cls(
            name=str(data.get("server_name") or ""),
            transport=transport,
            command=None if data.get("command") is None else str(data.get("command")),
            args=[str(value) for value in list(data.get("args") or [])],
            env=None
            if not isinstance(data.get("env"), dict)
            else {str(key): str(value) for key, value in dict(data.get("env") or {}).items()},
            cwd=None if data.get("cwd") is None else str(data.get("cwd")),
            url=None if data.get("url") is None else str(data.get("url")),
            headers={}
            if not isinstance(data.get("headers"), dict)
            else {str(key): str(value) for key, value in dict(data.get("headers") or {}).items()},
            timeout=float(data.get("timeout") or 30.0),
        )


class _MCPConnection:
    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def list_tools(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def close(self) -> None:
        return None


class _MCPStdioConnection(_MCPConnection):
    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._request_id = 0
        self._lock = threading.Lock()
        self._process = self._launch_process()
        self._initialize()

    def _launch_process(self) -> subprocess.Popen[str]:
        if not self._config.command:
            raise MCPError("mcp stdio transport requires command")
        try:
            return subprocess.Popen(
                [self._config.command, *self._config.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=self._config.cwd,
                env=self._config.env,
            )
        except OSError as exc:
            raise MCPError(f"failed to launch MCP server: {exc}") from exc

    def _initialize(self) -> None:
        self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ruhu", "version": "0.1.0"},
            },
        )
        self._send_notification("notifications/initialized")

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._send_request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )
        return _normalize_tool_result(result)

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._send_request("tools/list")
        tools = result.get("tools") if isinstance(result, dict) else None
        return list(tools) if isinstance(tools, list) else []

    def close(self) -> None:
        with self._lock:
            self._terminate_process_unlocked()

    def _terminate_process_unlocked(self) -> None:
        if self._process.poll() is not None:
            return
        try:
            if self._process.stdin is not None:
                self._process.stdin.close()
            self._process.terminate()
            self._process.wait(timeout=2.0)
        except Exception:
            try:
                self._process.kill()
            except Exception:
                pass

    def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload = {"jsonrpc": JSONRPC_VERSION, "method": method}
        if params is not None:
            payload["params"] = params
        self._write_line(payload)

    def _send_request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        with self._lock:
            self._request_id += 1
            payload = {
                "jsonrpc": JSONRPC_VERSION,
                "id": self._request_id,
                "method": method,
            }
            if params is not None:
                payload["params"] = params
            self._write_line(payload)
            line = self._read_line()
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MCPError(f"invalid MCP response: {line!r}") from exc
        if "error" in message:
            raise MCPError(str(message["error"]))
        return message.get("result")

    def _write_line(self, payload: dict[str, Any]) -> None:
        if self._process.stdin is None:
            raise MCPError("MCP process stdin is unavailable")
        self._process.stdin.write(json.dumps(payload) + "\n")
        self._process.stdin.flush()

    def _read_line(self) -> str:
        if self._process.stdout is None:
            raise MCPError("MCP process stdout is unavailable")
        try:
            selector = selectors.DefaultSelector()
            selector.register(self._process.stdout, selectors.EVENT_READ)
            events = selector.select(timeout=max(0.001, self._config.timeout))
        finally:
            try:
                selector.close()
            except Exception:
                pass
        if not events:
            self._terminate_process_unlocked()
            raise MCPError(f"MCP server timed out after {self._config.timeout:.3f}s")
        line = self._process.stdout.readline()
        if not line:
            stderr = ""
            if self._process.stderr is not None and self._process.poll() is not None:
                try:
                    stderr = self._process.stderr.read().strip()
                except Exception:
                    stderr = ""
            raise MCPError(f"MCP server closed the connection{': ' + stderr if stderr else ''}")
        return line


class _MCPHttpConnection(_MCPConnection):
    def __init__(self, config: MCPServerConfig) -> None:
        if not config.url:
            raise MCPError("mcp http transport requires url")
        self._config = config
        self._request_id = 0
        self._client = httpx.Client(timeout=config.timeout, headers=dict(config.headers))
        self._initialize()

    def _initialize(self) -> None:
        self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ruhu", "version": "0.1.0"},
            },
        )

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._send_request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )
        return _normalize_tool_result(result)

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._send_request("tools/list")
        tools = result.get("tools") if isinstance(result, dict) else None
        return list(tools) if isinstance(tools, list) else []

    def close(self) -> None:
        self._client.close()

    def _send_request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._request_id += 1
        payload = {
            "jsonrpc": JSONRPC_VERSION,
            "id": self._request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        response = self._client.post(str(self._config.url), json=payload)
        response.raise_for_status()
        message = response.json()
        if "error" in message:
            raise MCPError(str(message["error"]))
        return message.get("result")


class MCPClientManager:
    def __init__(self) -> None:
        self._connections: dict[str, _MCPConnection] = {}
        self._config_hashes: dict[str, str] = {}
        self._lock = threading.Lock()

    def call_tool(
        self,
        config: MCPServerConfig,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        connection = self._get_connection(config)
        try:
            return connection.call_tool(tool_name, arguments)
        except MCPError:
            self._drop_connection(config.name)
            raise

    def list_tools(self, config: MCPServerConfig) -> list[dict[str, Any]]:
        connection = self._get_connection(config)
        try:
            return connection.list_tools()
        except MCPError:
            self._drop_connection(config.name)
            raise

    def close_all(self) -> None:
        with self._lock:
            for connection in self._connections.values():
                connection.close()
            self._connections.clear()
            self._config_hashes.clear()

    def _drop_connection(self, name: str) -> None:
        with self._lock:
            connection = self._connections.pop(name, None)
            self._config_hashes.pop(name, None)
        if connection is not None:
            connection.close()

    def _get_connection(self, config: MCPServerConfig) -> _MCPConnection:
        if not config.name:
            raise MCPError("mcp server_name is required")
        config_hash = self._hash_config(config)
        with self._lock:
            current = self._connections.get(config.name)
            if current is not None and self._config_hashes.get(config.name) == config_hash:
                return current
            if current is not None:
                current.close()
            connection = self._connect(config)
            self._connections[config.name] = connection
            self._config_hashes[config.name] = config_hash
            return connection

    @staticmethod
    def _connect(config: MCPServerConfig) -> _MCPConnection:
        transport = config.transport.strip().lower()
        if transport == "stdio":
            return _MCPStdioConnection(config)
        if transport in {"sse", "http"}:
            return _MCPHttpConnection(config)
        raise MCPError(f"unsupported MCP transport: {config.transport}")

    @staticmethod
    def _hash_config(config: MCPServerConfig) -> str:
        raw = json.dumps(
            {
                "name": config.name,
                "transport": config.transport,
                "command": config.command,
                "args": config.args,
                "env": config.env,
                "cwd": config.cwd,
                "url": config.url,
                "headers": config.headers,
                "timeout": config.timeout,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_tool_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            normalized = dict(structured)
            content = result.get("content")
            if isinstance(content, list):
                text_parts: list[str] = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_parts.append(str(item.get("text") or ""))
                    elif isinstance(item, dict):
                        text_parts.append(json.dumps(item))
                    else:
                        text_parts.append(str(item))
                if text_parts:
                    normalized["content_text"] = "\n".join(part for part in text_parts if part)
            return normalized
        content = result.get("content")
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(str(item.get("text") or ""))
                elif isinstance(item, dict):
                    text_parts.append(json.dumps(item))
                else:
                    text_parts.append(str(item))
            if text_parts:
                normalized = dict(result)
                normalized["content_text"] = "\n".join(part for part in text_parts if part)
                return normalized
        return dict(result)
    return {"content": result}


mcp_manager = MCPClientManager()
