from __future__ import annotations

from typing import Any

from ruhu.tools.specs import ToolAnnotations, ToolFailureMode, ToolSpec
from ruhu.tools.types import ToolCall, ToolResult

from .service import BrowserTaskService

BROWSER_TASK_CREATE_TOOL_REF = "browser_task.create"


def browser_task_create_tool_spec() -> ToolSpec:
    return ToolSpec(
        ref=BROWSER_TASK_CREATE_TOOL_REF,
        kind="builtin",
        display_name="Create browser task",
        description=(
            "Creates a governed browser task from a bounded task pack so a worker can execute "
            "browser-only workflow steps with approval, artifacts, and operator oversight."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "task_pack_id": {"type": "string", "minLength": 1},
                "title": {"type": "string", "minLength": 1},
                "summary": {"type": "string"},
                "task_pack_version": {"type": "string"},
                "start_url": {"type": "string"},
                "input_payload": {"type": "object", "additionalProperties": True},
                "credential_refs": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "approval_prompt": {"type": "string"},
                "approval_ttl_seconds": {"type": "integer", "minimum": 15, "maximum": 86400},
                "metadata": {"type": "object", "additionalProperties": True},
            },
            "required": ["task_pack_id", "title"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "task_pack_id": {"type": "string"},
                "task_pack_version": {"type": "string"},
                "state": {"type": "string"},
                "approval_state": {"type": "string"},
                "current_approval_id": {"type": "string"},
                "conversation_id": {"type": "string"},
                "operator_url": {"type": "string"},
            },
            "required": ["task_id", "state", "approval_state", "conversation_id", "operator_url"],
            "additionalProperties": True,
        },
        annotations=ToolAnnotations(read_only=False, destructive=False, side_effect_free=False, idempotent=False),
        timeout_ms=5_000,
        confirmation="never",
        auth_mode="none",
        executor_key=BROWSER_TASK_CREATE_TOOL_REF,
        allowed_channels=["phone", "whatsapp", "web_chat", "web_widget", "browser"],
        tags=["browser_task", "workflow", "operator"],
        purpose="Create bounded browser work when APIs are unavailable and a governed browser worker is required.",
        when_to_use=[
            "Use when a configured task pack must interact with a browser-only internal or partner portal.",
            "Use when the browser task can run asynchronously with approval and operator visibility.",
        ],
        when_not_to_use=[
            "Do not use for open-ended web browsing or arbitrary navigation outside a task pack.",
            "Do not use when a stable API or normal integration tool is available for the same work.",
        ],
        failure_modes=[
            ToolFailureMode(
                kind="authorization_denied",
                description="The requested browser task pack is not enabled for this organization or agent.",
                retryable=False,
            ),
            ToolFailureMode(
                kind="validation_error",
                description="The browser task input payload or credential references do not match the task pack.",
                retryable=False,
            ),
            ToolFailureMode(
                kind="permanent_upstream_error",
                description="The browser task service is not configured or the task pack cannot be found.",
                retryable=False,
            ),
        ],
    )


class BrowserTaskCreateToolHandler:
    def __init__(self, service: BrowserTaskService) -> None:
        self._service = service

    def __call__(self, call: ToolCall, _spec: ToolSpec) -> ToolResult:
        conversation_id = call.caller.conversation_id
        if not conversation_id:
            return self._error(call, "browser tasks require a conversation_id", "validation_error")
        try:
            snapshot = self._service.create_task(
                conversation_id=conversation_id,
                organization_id=call.caller.tenant_id,
                agent_id=call.caller.agent_id,
                title=str(call.args["title"]),
                summary=_optional_str(call.args.get("summary")),
                requested_channel=call.caller.channel,
                task_pack_id=str(call.args["task_pack_id"]),
                task_pack_version=_optional_str(call.args.get("task_pack_version")),
                start_url=_optional_str(call.args.get("start_url")),
                input_payload=_dict_arg(call.args.get("input_payload")),
                credential_refs=_str_dict_arg(call.args.get("credential_refs")),
                requires_approval=False,
                approval_prompt=_optional_str(call.args.get("approval_prompt")),
                approval_ttl_seconds=_optional_int(call.args.get("approval_ttl_seconds")),
                metadata={
                    **_dict_arg(call.args.get("metadata")),
                    "created_via": "tool_runtime",
                    "tool_invocation_id": call.invocation_id,
                    "tool_ref": call.tool_ref,
                },
            )
        except KeyError as exc:
            return self._error(call, f"unknown browser task pack: {exc}", "permanent_upstream_error")
        except ValueError as exc:
            return self._error(call, str(exc), "validation_error")

        task = snapshot.task
        output: dict[str, Any] = {
            "task_id": task.task_id,
            "task_pack_id": task.task_pack_id,
            "task_pack_version": task.task_pack_version,
            "state": task.state,
            "approval_state": task.approval_state,
            "current_approval_id": task.current_approval_id,
            "conversation_id": task.conversation_id,
            "operator_url": f"/browser-tasks/{task.task_id}",
        }
        return ToolResult(
            invocation_id=call.invocation_id,
            tool_ref=call.tool_ref,
            status="success",
            output={key: value for key, value in output.items() if value is not None},
            metadata={"browser_task_id": task.task_id},
        )

    @staticmethod
    def _error(call: ToolCall, message: str, failure_kind: str) -> ToolResult:
        return ToolResult(
            invocation_id=call.invocation_id,
            tool_ref=call.tool_ref,
            status="error",
            error=message,
            metadata={"failure_kind": failure_kind, "error_type": "browser_task_create_failed"},
        )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _dict_arg(value: Any) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _str_dict_arg(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}
