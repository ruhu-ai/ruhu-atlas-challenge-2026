from __future__ import annotations

from ruhu.browser_tasks import (
    BrowserCredentialRequirement,
    BrowserTaskPack,
    BrowserTaskPackRegistry,
)
from ruhu.browser_tasks.service import BrowserTaskService
from ruhu.browser_tasks.store import InMemoryBrowserTaskStore
from ruhu.browser_tasks.tool import (
    BROWSER_TASK_CREATE_TOOL_REF,
    BrowserTaskCreateToolHandler,
    browser_task_create_tool_spec,
)
from ruhu.tools.executors.builtin import BuiltinExecutor
from ruhu.tools.registry import ToolRegistry
from ruhu.tools.runtime import ToolRuntime
from ruhu.tools.types import ToolCall, ToolCaller


def test_browser_task_create_tool_queues_governed_task() -> None:
    service = BrowserTaskService(
        InMemoryBrowserTaskStore(),
        task_pack_registry=BrowserTaskPackRegistry(
            [
                BrowserTaskPack(
                    pack_id="order_status_lookup",
                    version="1.0.0",
                    display_name="Order status lookup",
                    allowed_domains=["merchant.example.com"],
                    start_url="https://merchant.example.com/orders",
                    input_schema={
                        "type": "object",
                        "properties": {"order_id": {"type": "string"}},
                        "required": ["order_id"],
                        "additionalProperties": False,
                    },
                    credentials=[
                        BrowserCredentialRequirement(
                            kind="session",
                            name="merchant_session",
                            auth_type="browser_session",
                        )
                    ],
                )
            ]
        ),
    )
    executor = BuiltinExecutor({
        BROWSER_TASK_CREATE_TOOL_REF: BrowserTaskCreateToolHandler(service),
    })
    runtime = ToolRuntime(
        ToolRegistry([browser_task_create_tool_spec()]),
        executors={"builtin": executor},
    )

    result = runtime.invoke(
        ToolCall(
            tool_ref=BROWSER_TASK_CREATE_TOOL_REF,
            args={
                "task_pack_id": "order_status_lookup",
                "title": "Look up order",
                "input_payload": {"order_id": "ORDER-123"},
                "credential_refs": {"merchant_session": "connection:conn_123"},
            },
            caller=ToolCaller(
                channel="web_chat",
                conversation_id="conv_browser_tool",
                tenant_id="org_1",
                agent_id="agent_1",
            ),
        )
    )

    assert result.status == "success"
    assert result.output["task_pack_id"] == "order_status_lookup"
    assert result.output["state"] == "queued"
    assert result.output["approval_state"] == "not_required"
    assert result.output["operator_url"].startswith("/browser-tasks/")
    snapshot = service.get_snapshot(str(result.output["task_id"]), organization_id="org_1")
    assert snapshot.task.metadata["created_via"] == "tool_runtime"
    assert snapshot.task.metadata["tool_ref"] == BROWSER_TASK_CREATE_TOOL_REF


def test_browser_task_create_tool_returns_structured_error_for_invalid_pack_input() -> None:
    service = BrowserTaskService(
        InMemoryBrowserTaskStore(),
        task_pack_registry=BrowserTaskPackRegistry(
            [
                BrowserTaskPack(
                    pack_id="order_status_lookup",
                    version="1.0.0",
                    display_name="Order status lookup",
                    allowed_domains=["merchant.example.com"],
                    start_url="https://merchant.example.com/orders",
                    input_schema={
                        "type": "object",
                        "properties": {"order_id": {"type": "string"}},
                        "required": ["order_id"],
                        "additionalProperties": False,
                    },
                )
            ]
        ),
    )
    executor = BuiltinExecutor({
        BROWSER_TASK_CREATE_TOOL_REF: BrowserTaskCreateToolHandler(service),
    })
    runtime = ToolRuntime(
        ToolRegistry([browser_task_create_tool_spec()]),
        executors={"builtin": executor},
    )

    result = runtime.invoke(
        ToolCall(
            tool_ref=BROWSER_TASK_CREATE_TOOL_REF,
            args={
                "task_pack_id": "order_status_lookup",
                "title": "Look up order",
                "input_payload": {},
            },
            caller=ToolCaller(
                channel="web_chat",
                conversation_id="conv_browser_tool",
                tenant_id="org_1",
            ),
        )
    )

    assert result.status == "error"
    assert result.metadata["failure_kind"] == "validation_error"
    assert "browser task input does not match task pack schema" in (result.error or "")
