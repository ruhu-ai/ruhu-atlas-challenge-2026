from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ruhu.tools.integration_runtime import ToolIntegrationRuntime
from ruhu.tools.registry import ToolRegistry
from ruhu.tools.runtime import ToolRuntime
from ruhu.tools.specs import ToolSpec
from ruhu.tools.store import InMemoryToolInvocationStore
from ruhu.tools.types import ToolCall, ToolCaller, ToolResult


def _deferred_spec(**overrides: object) -> ToolSpec:
    data = {
        "ref": "crm.bulk_import_contacts",
        "kind": "builtin",
        "display_name": "Bulk Import Contacts",
        "description": "Submit a long-running bulk import job to the external CRM.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source_uri": {"type": "string"},
            },
            "required": ["source_uri"],
            "additionalProperties": False,
        },
        "executor_config": {
            "execution_mode": "deferred",
            "resolution_mode": "webhook",
            "deferred_queue": "crm",
            "max_attempts": 3,
        },
    }
    data.update(overrides)
    return ToolSpec.model_validate(data)


def test_runtime_submits_deferred_job_and_marks_invocation_queued() -> None:
    spec = _deferred_spec()
    invocation_store = InMemoryToolInvocationStore()
    integration_runtime = ToolIntegrationRuntime(invocation_store=invocation_store)
    runtime = ToolRuntime(
        ToolRegistry([spec]),
        store=invocation_store,
        integration_runtime=integration_runtime,
    )

    result = runtime.invoke(
        ToolCall(
            tool_ref=spec.ref,
            args={"source_uri": "gs://demo/import.csv"},
            caller=ToolCaller(channel="web_chat", tenant_id="org_123"),
        )
    )

    assert result.status == "success"
    assert result.metadata["deferred"] is True
    invocation = runtime.store.load(result.invocation_id)
    assert invocation is not None
    assert invocation.status == "queued"
    job = integration_runtime.load_job_for_invocation(result.invocation_id)
    assert job is not None
    assert job.status == "queued"
    assert job.queue_name == "crm"
    assert job.payload["tool_call"]["args"] == {"source_uri": "gs://demo/import.csv"}
    assert invocation.metadata["integration_job_id"] == job.job_id


def test_runtime_completes_deferred_job_and_syncs_invocation() -> None:
    spec = _deferred_spec()
    invocation_store = InMemoryToolInvocationStore()
    integration_runtime = ToolIntegrationRuntime(invocation_store=invocation_store)
    runtime = ToolRuntime(
        ToolRegistry([spec]),
        store=invocation_store,
        integration_runtime=integration_runtime,
    )

    submitted = runtime.invoke(
        ToolCall(
            tool_ref=spec.ref,
            args={"source_uri": "gs://demo/import.csv"},
            caller=ToolCaller(channel="web_chat", tenant_id="org_123"),
        )
    )
    job = integration_runtime.load_job_for_invocation(submitted.invocation_id)
    assert job is not None

    integration_runtime.complete_job(
        job.job_id,
        ToolResult(
            invocation_id=submitted.invocation_id,
            tool_ref=spec.ref,
            status="success",
            output={"imported": 42},
            latency_ms=1800,
            metadata={"provider_job_id": "ext_123"},
        ),
    )

    invocation = runtime.store.load(submitted.invocation_id)
    assert invocation is not None
    assert invocation.status == "completed"
    assert invocation.output == {"imported": 42}
    assert invocation.metadata["deferred_completed"] is True
    loaded_result = runtime.load_result(submitted.invocation_id)
    assert loaded_result is not None
    assert loaded_result.status == "success"
    assert loaded_result.output == {"imported": 42}


def test_runtime_cancels_queued_deferred_job() -> None:
    spec = _deferred_spec()
    invocation_store = InMemoryToolInvocationStore()
    integration_runtime = ToolIntegrationRuntime(invocation_store=invocation_store)
    runtime = ToolRuntime(
        ToolRegistry([spec]),
        store=invocation_store,
        integration_runtime=integration_runtime,
    )

    submitted = runtime.invoke(
        ToolCall(
            tool_ref=spec.ref,
            args={"source_uri": "gs://demo/import.csv"},
            caller=ToolCaller(channel="web_chat", tenant_id="org_123"),
        )
    )

    cancelled = runtime.cancel(submitted.invocation_id, reason="cancelled by user")

    assert cancelled.status == "cancelled"
    invocation = runtime.store.load(submitted.invocation_id)
    assert invocation is not None
    assert invocation.status == "cancelled"
    job = integration_runtime.load_job_for_invocation(submitted.invocation_id)
    assert job is not None
    assert job.status == "cancelled"


def test_integration_runtime_claims_retry_and_poll_ready_jobs() -> None:
    spec = _deferred_spec(executor_config={"execution_mode": "deferred", "resolution_mode": "polling"})
    invocation_store = InMemoryToolInvocationStore()
    integration_runtime = ToolIntegrationRuntime(invocation_store=invocation_store)
    runtime = ToolRuntime(
        ToolRegistry([spec]),
        store=invocation_store,
        integration_runtime=integration_runtime,
    )

    submitted = runtime.invoke(
        ToolCall(
            tool_ref=spec.ref,
            args={"source_uri": "gs://demo/import.csv"},
            caller=ToolCaller(channel="web_chat", tenant_id="org_123"),
        )
    )
    job = integration_runtime.load_job_for_invocation(submitted.invocation_id)
    assert job is not None

    lease = datetime.now(timezone.utc) + timedelta(seconds=30)
    claimed = integration_runtime.claim_next_job(worker_id="worker-1", lease_expires_at=lease)
    assert claimed is not None
    assert claimed.job_id == job.job_id
    assert claimed.status == "running"
    assert claimed.attempt_count == 1

    waiting = integration_runtime.mark_waiting_poll(
        job.job_id,
        next_poll_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        external_job_id="provider_1",
    )
    assert waiting.status == "waiting_poll"
    assert waiting.external_job_id == "provider_1"

    reclaimed = integration_runtime.claim_next_job(
        worker_id="worker-2",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    assert reclaimed is not None
    assert reclaimed.job_id == job.job_id
    assert reclaimed.worker_id == "worker-2"
