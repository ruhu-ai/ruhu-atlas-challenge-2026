from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field, model_validator

from .specs import ToolSpec
from .types import ToolCall, ToolIntegrationJob, ToolResult

DeferredToolAction = Literal["complete", "wait_poll", "wait_webhook", "retry", "fail"]


class DeferredToolTransition(BaseModel):
    action: DeferredToolAction
    result: ToolResult | None = None
    external_job_id: str | None = None
    callback_correlation_id: str | None = None
    next_poll_at: datetime | None = None
    next_retry_at: datetime | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_transition(self) -> "DeferredToolTransition":
        if self.action == "complete" and self.result is None:
            raise ValueError("complete transition requires result")
        if self.action == "wait_poll" and self.next_poll_at is None:
            raise ValueError("wait_poll transition requires next_poll_at")
        if self.action == "retry" and self.next_retry_at is None:
            raise ValueError("retry transition requires next_retry_at")
        if self.action == "fail" and not (self.error or (self.result and self.result.error)):
            raise ValueError("fail transition requires an error")
        return self


@runtime_checkable
class DeferredToolExecutor(Protocol):
    def submit_deferred(
        self,
        spec: ToolSpec,
        call: ToolCall,
        job: ToolIntegrationJob,
    ) -> DeferredToolTransition: ...

    def poll_deferred(
        self,
        spec: ToolSpec,
        call: ToolCall,
        job: ToolIntegrationJob,
    ) -> DeferredToolTransition: ...

    def handle_deferred_callback(
        self,
        spec: ToolSpec,
        call: ToolCall,
        job: ToolIntegrationJob,
        *,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> DeferredToolTransition: ...
