from __future__ import annotations

import hmac
import hashlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from ruhu.tools.deferred import DeferredToolTransition
from ruhu.tools.executors.builtin import BuiltinExecutor
from ruhu.tools.executors.http import HttpExecutor
from ruhu.tools.integration_runtime import ToolIntegrationRuntime
from ruhu.tools.integration_worker import ToolIntegrationWorkerRuntime
from ruhu.tools.registry import ToolRegistry
from ruhu.tools.runtime import ToolRuntime
from ruhu.tools.specs import ToolSpec
from ruhu.tools.store import InMemoryToolInvocationStore
from ruhu.tools.types import ToolCall, ToolCaller, ToolIntegrationJob, ToolResult


class DemoWebhookDeferredHandler:
    def submit(self, call, spec, job: ToolIntegrationJob) -> DeferredToolTransition:
        return DeferredToolTransition(
            action="wait_webhook",
            external_job_id=f"provider-{job.invocation_id}",
            callback_correlation_id=f"cb-{job.invocation_id}",
            metadata={"provider": "demo"},
        )

    def poll(self, call, spec, job: ToolIntegrationJob) -> DeferredToolTransition:
        return DeferredToolTransition(
            action="wait_poll",
            next_poll_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )

    def handle_callback(self, call, spec, job: ToolIntegrationJob, *, payload, headers=None) -> DeferredToolTransition:
        status = str(payload.get("status") or "completed").lower()
        if status not in {"completed", "success"}:
            return DeferredToolTransition(action="fail", error=str(payload.get("error") or "provider reported failure"))
        return DeferredToolTransition(
            action="complete",
            result=ToolResult(
                invocation_id=call.invocation_id,
                tool_ref=call.tool_ref,
                status="success",
                output={"provider_job_id": job.external_job_id, "accepted": True},
                metadata={"provider": "demo"},
            ),
        )


class FailingDeferredHandler:
    def submit(self, call, spec, job: ToolIntegrationJob) -> DeferredToolTransition:
        raise RuntimeError("provider temporarily unavailable")

    def poll(self, call, spec, job: ToolIntegrationJob) -> DeferredToolTransition:
        raise AssertionError("poll should not be called in this test")

    def handle_callback(self, call, spec, job: ToolIntegrationJob, *, payload, headers=None) -> DeferredToolTransition:
        raise AssertionError("callback should not be called in this test")


def _worker_spec() -> ToolSpec:
    return ToolSpec.model_validate(
        {
            "ref": "crm.bulk_import_contacts",
            "kind": "builtin",
            "display_name": "Bulk Import Contacts",
            "description": "Submit a long-running bulk import job to the external CRM.",
            "input_schema": {
                "type": "object",
                "properties": {"source_uri": {"type": "string"}},
                "required": ["source_uri"],
                "additionalProperties": False,
            },
            "executor_config": {
                "execution_mode": "deferred",
                "resolution_mode": "webhook",
                "deferred_queue": "crm",
            },
        }
    )


def test_tool_integration_worker_processes_webhook_completion() -> None:
    spec = _worker_spec()
    invocation_store = InMemoryToolInvocationStore()
    executor = BuiltinExecutor(deferred_handlers={spec.ref: DemoWebhookDeferredHandler()})
    runtime = ToolRuntime(
        ToolRegistry([spec]),
        store=invocation_store,
        executors={"builtin": executor},
        integration_runtime=ToolIntegrationRuntime(invocation_store=invocation_store),
    )
    worker = ToolIntegrationWorkerRuntime(
        tool_runtime=runtime,
        integration_runtime=runtime.integration_runtime,
        embedded_worker_enabled=False,
    )

    submitted = runtime.invoke(
        ToolCall(
            tool_ref=spec.ref,
            args={"source_uri": "gs://demo/import.csv"},
            caller=ToolCaller(channel="web_chat", tenant_id="org_123", conversation_id="conv_123"),
        )
    )
    initial_job = runtime.integration_runtime.load_job_for_invocation(submitted.invocation_id)
    assert initial_job is not None
    assert initial_job.status == "queued"

    processed = worker.process_available_jobs_once(max_jobs=1)
    assert len(processed) == 1
    waiting_job = runtime.integration_runtime.load_job_for_invocation(submitted.invocation_id)
    assert waiting_job is not None
    assert waiting_job.status == "waiting_webhook"
    assert waiting_job.callback_correlation_id == f"cb-{submitted.invocation_id}"

    webhook_result = worker.process_webhook_callback(
        waiting_job.callback_correlation_id,
        payload={"status": "completed"},
    )

    assert webhook_result.job.status == "completed"
    assert webhook_result.result.status == "success"
    invocation = runtime.store.load(submitted.invocation_id)
    assert invocation is not None
    assert invocation.status == "completed"
    assert invocation.output["accepted"] is True

    replayed = worker.process_webhook_callback(
        waiting_job.callback_correlation_id,
        payload={"status": "completed"},
    )
    assert replayed.replayed is True
    assert replayed.job.status == "completed"
    assert replayed.result.status == "success"


def test_tool_integration_worker_schedules_retry_backoff_on_worker_exception() -> None:
    spec = _worker_spec()
    spec.executor_config["max_attempts"] = 3
    invocation_store = InMemoryToolInvocationStore()
    executor = BuiltinExecutor(deferred_handlers={spec.ref: FailingDeferredHandler()})
    runtime = ToolRuntime(
        ToolRegistry([spec]),
        store=invocation_store,
        executors={"builtin": executor},
        integration_runtime=ToolIntegrationRuntime(invocation_store=invocation_store),
    )
    worker = ToolIntegrationWorkerRuntime(
        tool_runtime=runtime,
        integration_runtime=runtime.integration_runtime,
        embedded_worker_enabled=False,
    )

    submitted = runtime.invoke(
        ToolCall(
            tool_ref=spec.ref,
            args={"source_uri": "gs://demo/import.csv"},
            caller=ToolCaller(channel="web_chat", tenant_id="org_123", conversation_id="conv_123"),
        )
    )

    processed = worker.process_available_jobs_once(max_jobs=1)
    assert len(processed) == 1
    retry_job = runtime.integration_runtime.load_job_for_invocation(submitted.invocation_id)
    assert retry_job is not None
    assert retry_job.status == "retry_scheduled"
    assert retry_job.next_retry_at is not None
    assert retry_job.next_retry_at > datetime.now(timezone.utc)
    assert retry_job.error == "provider temporarily unavailable"


def test_tool_integration_worker_processes_http_webhook_completion() -> None:
    spec = ToolSpec.model_validate(
        {
            "ref": "crm.async_import_http",
            "kind": "http",
            "display_name": "Async CRM Import",
            "description": "Submit and reconcile an asynchronous CRM import over HTTP.",
            "input_schema": {
                "type": "object",
                "properties": {"source_uri": {"type": "string"}},
                "required": ["source_uri"],
                "additionalProperties": False,
            },
            "executor_config": {
                "execution_mode": "deferred",
                "resolution_mode": "webhook",
                "provider": "demo_http",
                "deferred": {
                    "submit": {
                        "url": "https://example.com/imports",
                        "method": "POST",
                        "status_path": "status",
                        "pending_values": ["accepted"],
                        "external_job_id_path": "job.id",
                        "callback_correlation_id_path": "callback.id",
                    },
                    "callback": {
                        "status_path": "status",
                        "success_values": ["completed"],
                        "failure_values": ["failed"],
                        "result_path": "result",
                    },
                },
            },
        }
    )

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict[str, object]) -> None:
            self.status_code = status_code
            self._payload = payload
            self.text = str(payload)

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict[str, object]]] = []

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            if url == "https://example.com/imports":
                return FakeResponse(
                    202,
                    {"status": "accepted", "job": {"id": "provider-job-1"}, "callback": {"id": "cb-http-1"}},
                )
            raise AssertionError(f"unexpected URL {url}")

    invocation_store = InMemoryToolInvocationStore()
    client = FakeClient()
    runtime = ToolRuntime(
        ToolRegistry([spec]),
        store=invocation_store,
        executors={"http": HttpExecutor(client=client)},
        integration_runtime=ToolIntegrationRuntime(invocation_store=invocation_store),
    )
    worker = ToolIntegrationWorkerRuntime(
        tool_runtime=runtime,
        integration_runtime=runtime.integration_runtime,
        embedded_worker_enabled=False,
    )

    with patch("ruhu.tools.url_validator.socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]
        submitted = runtime.invoke(
            ToolCall(
                tool_ref=spec.ref,
                args={"source_uri": "gs://demo/import.csv"},
                caller=ToolCaller(channel="web_chat", tenant_id="org_123", conversation_id="conv_123"),
            )
        )
        processed = worker.process_available_jobs_once(max_jobs=1)

    assert submitted.metadata["deferred"] is True
    assert len(processed) == 1
    waiting_job = runtime.integration_runtime.load_job_for_invocation(submitted.invocation_id)
    assert waiting_job is not None
    assert waiting_job.status == "waiting_webhook"
    assert waiting_job.external_job_id == "provider-job-1"
    assert waiting_job.callback_correlation_id == "cb-http-1"

    webhook_result = worker.process_webhook_callback(
        "cb-http-1",
        payload={"status": "completed", "result": {"imported": 7}},
    )

    assert webhook_result.job.status == "completed"
    assert webhook_result.result.output == {"imported": 7}
    invocation = runtime.store.load(submitted.invocation_id)
    assert invocation is not None
    assert invocation.status == "completed"
    assert invocation.output == {"imported": 7}


def test_tool_integration_worker_verifies_http_webhook_signature() -> None:
    spec = ToolSpec.model_validate(
        {
            "ref": "crm.signed_webhook_http",
            "kind": "http",
            "display_name": "Signed CRM Import",
            "description": "Reconcile an asynchronous CRM import from a signed webhook callback.",
            "input_schema": {
                "type": "object",
                "properties": {"source_uri": {"type": "string"}},
                "required": ["source_uri"],
                "additionalProperties": False,
            },
            "executor_config": {
                "execution_mode": "deferred",
                "resolution_mode": "webhook",
                "deferred": {
                    "submit": {
                        "url": "https://example.com/imports",
                        "method": "POST",
                        "status_path": "status",
                        "pending_values": ["accepted"],
                        "callback_correlation_id_path": "callback.id",
                    },
                    "callback": {
                        "status_path": "status",
                        "success_values": ["completed"],
                        "result_path": "result",
                        "verification": {
                            "mode": "hmac_sha256",
                            "header": "X-Signature",
                            "secret": "super-secret",
                            "prefix": "sha256=",
                        },
                    },
                },
            },
        }
    )

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict[str, object]) -> None:
            self.status_code = status_code
            self._payload = payload
            self.text = str(payload)

        def json(self):
            return self._payload

    class FakeClient:
        def request(self, method, url, **kwargs):
            if url == "https://example.com/imports":
                return FakeResponse(
                    202,
                    {"status": "accepted", "callback": {"id": "cb-signed-1"}},
                )
            raise AssertionError(f"unexpected URL {url}")

    invocation_store = InMemoryToolInvocationStore()
    runtime = ToolRuntime(
        ToolRegistry([spec]),
        store=invocation_store,
        executors={"http": HttpExecutor(client=FakeClient())},
        integration_runtime=ToolIntegrationRuntime(invocation_store=invocation_store),
    )
    worker = ToolIntegrationWorkerRuntime(
        tool_runtime=runtime,
        integration_runtime=runtime.integration_runtime,
        embedded_worker_enabled=False,
    )

    with patch("ruhu.tools.url_validator.socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]
        submitted = runtime.invoke(
            ToolCall(
                tool_ref=spec.ref,
                args={"source_uri": "gs://demo/import.csv"},
                caller=ToolCaller(channel="web_chat", tenant_id="org_123", conversation_id="conv_123"),
            )
        )
        worker.process_available_jobs_once(max_jobs=1)

    payload = {"status": "completed", "result": {"imported": 4}}
    raw_body = json.dumps({"payload": payload}, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(b"super-secret", raw_body, hashlib.sha256).hexdigest()
    webhook_result = worker.process_webhook_callback(
        "cb-signed-1",
        payload=payload,
        headers={"X-Signature": f"sha256={signature}"},
        raw_body=raw_body,
    )

    assert webhook_result.result.status == "success"
    assert webhook_result.result.output == {"imported": 4}


def test_tool_integration_worker_polls_http_job_to_completion() -> None:
    spec = ToolSpec.model_validate(
        {
            "ref": "crm.async_poll_http",
            "kind": "http",
            "display_name": "Polling CRM Import",
            "description": "Submit and reconcile an asynchronous CRM import by polling HTTP.",
            "input_schema": {
                "type": "object",
                "properties": {"source_uri": {"type": "string"}},
                "required": ["source_uri"],
                "additionalProperties": False,
            },
            "executor_config": {
                "execution_mode": "deferred",
                "resolution_mode": "polling",
                "deferred": {
                    "submit": {
                        "url": "https://example.com/imports",
                        "method": "POST",
                        "status_path": "status",
                        "pending_values": ["accepted"],
                        "external_job_id_path": "job.id",
                        "poll_interval_seconds": 1,
                    },
                    "poll": {
                        "url": "https://example.com/imports/{external_job_id}",
                        "method": "GET",
                        "status_path": "status",
                        "pending_values": ["running"],
                        "success_values": ["completed"],
                        "failure_values": ["failed"],
                        "result_path": "result",
                        "poll_interval_seconds": 1,
                    },
                },
            },
        }
    )

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict[str, object]) -> None:
            self.status_code = status_code
            self._payload = payload
            self.text = str(payload)

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self) -> None:
            self.poll_count = 0

        def request(self, method, url, **kwargs):
            if url == "https://example.com/imports":
                return FakeResponse(202, {"status": "accepted", "job": {"id": "provider-job-2"}})
            if url == "https://example.com/imports/provider-job-2":
                self.poll_count += 1
                if self.poll_count == 1:
                    return FakeResponse(200, {"status": "running"})
                return FakeResponse(200, {"status": "completed", "result": {"imported": 11}})
            raise AssertionError(f"unexpected URL {url}")

    invocation_store = InMemoryToolInvocationStore()
    client = FakeClient()
    runtime = ToolRuntime(
        ToolRegistry([spec]),
        store=invocation_store,
        executors={"http": HttpExecutor(client=client)},
        integration_runtime=ToolIntegrationRuntime(invocation_store=invocation_store),
    )
    worker = ToolIntegrationWorkerRuntime(
        tool_runtime=runtime,
        integration_runtime=runtime.integration_runtime,
        embedded_worker_enabled=False,
    )

    with patch("ruhu.tools.url_validator.socket.getaddrinfo") as mock_gai:
        mock_gai.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]
        submitted = runtime.invoke(
            ToolCall(
                tool_ref=spec.ref,
                args={"source_uri": "gs://demo/import.csv"},
                caller=ToolCaller(channel="web_chat", tenant_id="org_123", conversation_id="conv_123"),
            )
        )
        first = worker.process_available_jobs_once(max_jobs=1)
        assert len(first) == 1
        waiting_job = runtime.integration_runtime.load_job_for_invocation(submitted.invocation_id)
        assert waiting_job is not None
        assert waiting_job.status == "waiting_poll"

        waiting_job.next_poll_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        runtime.integration_runtime.store.save(waiting_job)
        second = worker.process_available_jobs_once(max_jobs=1)
        assert len(second) == 1
        waiting_job = runtime.integration_runtime.load_job_for_invocation(submitted.invocation_id)
        assert waiting_job is not None
        assert waiting_job.status == "waiting_poll"

        waiting_job.next_poll_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        runtime.integration_runtime.store.save(waiting_job)
        third = worker.process_available_jobs_once(max_jobs=1)

    assert len(third) == 1
    invocation = runtime.store.load(submitted.invocation_id)
    assert invocation is not None
    assert invocation.status == "completed"
    assert invocation.output == {"imported": 11}
