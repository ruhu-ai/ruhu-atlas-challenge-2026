from __future__ import annotations

from typing import Protocol

from ..specs import ToolSpec
from ..types import ToolCall, ToolKind, ToolResult


class ToolExecutor(Protocol):
    kind: ToolKind

    def execute(self, spec: ToolSpec, call: ToolCall) -> ToolResult: ...

