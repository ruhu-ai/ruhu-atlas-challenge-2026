from __future__ import annotations

from typing import Any, Callable, Protocol

from ..deferred import DeferredToolTransition
from ..specs import ToolSpec
from ..types import ToolCall, ToolIntegrationJob, ToolResult

BuiltinHandler = Callable[[ToolCall, ToolSpec], dict[str, Any] | ToolResult]


class BuiltinDeferredHandler(Protocol):
    def submit(
        self,
        call: ToolCall,
        spec: ToolSpec,
        job: ToolIntegrationJob,
    ) -> DeferredToolTransition: ...

    def poll(
        self,
        call: ToolCall,
        spec: ToolSpec,
        job: ToolIntegrationJob,
    ) -> DeferredToolTransition: ...

    def handle_callback(
        self,
        call: ToolCall,
        spec: ToolSpec,
        job: ToolIntegrationJob,
        *,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> DeferredToolTransition: ...


class BuiltinExecutor:
    kind = "builtin"

    def __init__(
        self,
        handlers: dict[str, BuiltinHandler] | None = None,
        *,
        deferred_handlers: dict[str, BuiltinDeferredHandler] | None = None,
    ) -> None:
        self._handlers: dict[str, BuiltinHandler] = dict(handlers or {})
        self._deferred_handlers: dict[str, BuiltinDeferredHandler] = dict(deferred_handlers or {})

    def register(self, key: str, handler: BuiltinHandler) -> None:
        self._handlers[key] = handler

    def register_deferred(self, key: str, handler: BuiltinDeferredHandler) -> None:
        self._deferred_handlers[key] = handler

    def execute(self, spec: ToolSpec, call: ToolCall) -> ToolResult:
        handler = self._handlers.get(spec.executor_key or spec.ref)
        if handler is None:
            raise KeyError(f"no builtin handler registered for {spec.executor_key or spec.ref}")
        result = handler(call, spec)
        if isinstance(result, ToolResult):
            return result
        return ToolResult(
            invocation_id=call.invocation_id,
            tool_ref=call.tool_ref,
            status="success",
            output=dict(result),
        )

    def submit_deferred(
        self,
        spec: ToolSpec,
        call: ToolCall,
        job: ToolIntegrationJob,
    ) -> DeferredToolTransition:
        handler = self._deferred_handlers.get(spec.executor_key or spec.ref)
        if handler is None:
            raise KeyError(f"no deferred builtin handler registered for {spec.executor_key or spec.ref}")
        return handler.submit(call, spec, job)

    def poll_deferred(
        self,
        spec: ToolSpec,
        call: ToolCall,
        job: ToolIntegrationJob,
    ) -> DeferredToolTransition:
        handler = self._deferred_handlers.get(spec.executor_key or spec.ref)
        if handler is None:
            raise KeyError(f"no deferred builtin handler registered for {spec.executor_key or spec.ref}")
        return handler.poll(call, spec, job)

    def handle_deferred_callback(
        self,
        spec: ToolSpec,
        call: ToolCall,
        job: ToolIntegrationJob,
        *,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> DeferredToolTransition:
        handler = self._deferred_handlers.get(spec.executor_key or spec.ref)
        if handler is None:
            raise KeyError(f"no deferred builtin handler registered for {spec.executor_key or spec.ref}")
        try:
            return handler.handle_callback(
                call,
                spec,
                job,
                payload=payload,
                headers=headers,
                raw_body=raw_body,
            )
        except TypeError as exc:
            if "raw_body" not in str(exc):
                raise
            return handler.handle_callback(call, spec, job, payload=payload, headers=headers)
