from __future__ import annotations

import asyncio

import httpx
from fastapi import FastAPI

from ruhu.attachments.runtime import AttachmentRuntime
from ruhu.attachments.service import AttachmentService
from ruhu.attachments.store import InMemoryAttachmentStore
from ruhu.browser_tasks import (
    BrowserCredentialRequirement,
    BrowserTaskPack,
    BrowserTaskPackRegistry,
    builtin_browser_task_packs,
)
from ruhu.browser_tasks.service import BrowserTaskService
from ruhu.browser_tasks.store import InMemoryBrowserTaskStore
from ruhu.browser_tasks_api import install_browser_task_router


def test_browser_task_operator_api_tracks_progress_and_artifacts() -> None:
    async def run() -> None:
        attachment_runtime = AttachmentRuntime(
            service=AttachmentService(InMemoryAttachmentStore(), max_file_bytes=1024 * 1024),
            max_workers=1,
        )
        browser_task_service = BrowserTaskService(InMemoryBrowserTaskStore())
        app = FastAPI()
        install_browser_task_router(
            app,
            browser_task_service=browser_task_service,
            attachment_runtime=attachment_runtime,
            authorize_request=lambda _request: None,
        )

        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                created = await client.post(
                    "/internal/browser-tasks",
                    json={
                        "conversation_id": "conv_operator_1",
                        "title": "Verify invoice",
                        "summary": "Open billing portal and confirm invoice state.",
                        "requires_approval": True,
                        "approval_prompt": "Approve billing access.",
                        "approval_ttl_seconds": 600,
                    },
                )
                assert created.status_code == 200
                task_payload = created.json()
                task_id = task_payload["task"]["task_id"]
                assert task_payload["approval"]["expires_at"] is not None
                browser_task_service.approve(
                    approval_id=task_payload["approval"]["approval_id"],
                    organization_id=None,
                )

                listed = await client.get("/internal/browser-tasks", params={"conversation_id": "conv_operator_1"})
                assert listed.status_code == 200
                assert listed.json()[0]["task"]["task_id"] == task_id

                progress = await client.post(
                    f"/internal/browser-tasks/{task_id}/progress",
                    json={
                        "event_type": "browser.navigating",
                        "message": "Opening billing portal.",
                        "state": "running",
                    },
                )
                assert progress.status_code == 200
                assert progress.json()["task"]["state"] == "running"

                artifact = await client.post(
                    f"/internal/browser-tasks/{task_id}/artifacts",
                    params={"filename": "invoice.txt", "kind": "result_bundle"},
                    content=b"invoice artifact",
                    headers={"Content-Type": "text/plain"},
                )
                assert artifact.status_code == 200
                artifact_payload = artifact.json()
                artifact_id = artifact_payload["artifact"]["artifact_id"]
                assert artifact_payload["public_widget_download_url"].endswith(
                    f"/public/widget/sessions/conv_operator_1/artifacts/{artifact_id}/download"
                )

                completed = await client.post(
                    f"/internal/browser-tasks/{task_id}/complete",
                    json={"message": "Invoice verified.", "result": {"summary": "Invoice is paid."}},
                )
                assert completed.status_code == 200
                assert completed.json()["task"]["state"] == "completed"
                assert completed.json()["task"]["result"]["summary"] == "Invoice is paid."
                assert completed.json()["task"]["result"]["artifacts"][0]["artifact_id"] == artifact_id
                assert [event["event_sequence"] for event in completed.json()["recent_events"]] == [1, 2, 3, 4, 5]

                progress_after_complete = await client.post(
                    f"/internal/browser-tasks/{task_id}/progress",
                    json={
                        "event_type": "browser.navigating",
                        "message": "Opening billing portal.",
                        "state": "running",
                    },
                )
                assert progress_after_complete.status_code == 409
                assert progress_after_complete.json()["detail"] == "browser task is already completed"

                downloaded = await client.get(
                    f"/internal/browser-tasks/artifacts/{artifact_id}/download"
                )
                assert downloaded.status_code == 200
                assert downloaded.headers["x-ruhu-artifact-id"] == artifact_id
                assert downloaded.content == b"invoice artifact"
        finally:
            attachment_runtime.shutdown()

    asyncio.run(run())


def test_browser_task_operator_api_lists_task_packs() -> None:
    async def run() -> None:
        browser_task_service = BrowserTaskService(
            InMemoryBrowserTaskStore(),
            task_pack_registry=BrowserTaskPackRegistry(builtin_browser_task_packs()),
        )
        app = FastAPI()
        install_browser_task_router(
            app,
            browser_task_service=browser_task_service,
            attachment_runtime=None,
            authorize_request=lambda _request: None,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/internal/browser-task-packs")

        assert response.status_code == 200
        payload = response.json()
        by_id = {item["pack_id"]: item for item in payload}
        assert by_id["invoice_lookup"]["credentials"][0]["kind"] == "session"
        assert by_id["appointment_reschedule"]["approval_policy"]["approval_kinds"] == ["change_confirmation"]

    asyncio.run(run())


def test_browser_task_operator_api_lists_inbox_with_filters() -> None:
    async def run() -> None:
        browser_task_service = BrowserTaskService(InMemoryBrowserTaskStore())
        app = FastAPI()
        install_browser_task_router(
            app,
            browser_task_service=browser_task_service,
            attachment_runtime=None,
            authorize_request=lambda _request: None,
        )
        browser_task_service.create_task(
            conversation_id="conv_inbox_1",
            organization_id="org_1",
            title="Queued task",
        )
        browser_task_service.create_task(
            conversation_id="conv_inbox_2",
            organization_id="org_1",
            title="Approval task",
            requires_approval=True,
        )
        browser_task_service.create_task(
            conversation_id="conv_inbox_3",
            organization_id="org_2",
            title="Other org task",
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(
                "/internal/browser-task-inbox",
                params={"organization_id": "org_1", "approval_state": "pending"},
            )

        assert response.status_code == 200
        payload = response.json()
        assert len(payload) == 1
        assert payload[0]["task"]["title"] == "Approval task"
        assert payload[0]["approval"]["state"] == "pending"

    asyncio.run(run())


def test_browser_task_operator_api_approves_denies_and_cancels() -> None:
    async def run() -> None:
        browser_task_service = BrowserTaskService(InMemoryBrowserTaskStore())
        app = FastAPI()
        install_browser_task_router(
            app,
            browser_task_service=browser_task_service,
            attachment_runtime=None,
            authorize_request=lambda _request: None,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            pending = await client.post(
                "/internal/browser-tasks",
                json={
                    "conversation_id": "conv_operator_decisions",
                    "organization_id": "org_1",
                    "title": "Verify invoice",
                    "requires_approval": True,
                    "approval_prompt": "Approve billing access.",
                },
            )
            assert pending.status_code == 200
            approval_id = pending.json()["approval"]["approval_id"]

            approved = await client.post(
                f"/internal/browser-tasks/approvals/{approval_id}/approve",
                params={"organization_id": "org_1"},
                json={"reason": "approved by operator"},
            )
            assert approved.status_code == 200
            assert approved.json()["task"]["state"] == "queued"
            assert approved.json()["approval"]["state"] == "approved"

            cancel = await client.post(
                f"/internal/browser-tasks/{approved.json()['task']['task_id']}/cancel",
                params={"organization_id": "org_1"},
                json={"reason": "customer stopped the request"},
            )
            assert cancel.status_code == 200
            assert cancel.json()["task"]["state"] == "cancelled"
            assert cancel.json()["task"]["error"] == "customer stopped the request"

            pending_deny = await client.post(
                "/internal/browser-tasks",
                json={
                    "conversation_id": "conv_operator_deny",
                    "organization_id": "org_1",
                    "title": "Verify invoice",
                    "requires_approval": True,
                    "approval_prompt": "Approve billing access.",
                },
            )
            deny_approval_id = pending_deny.json()["approval"]["approval_id"]
            denied = await client.post(
                f"/internal/browser-tasks/approvals/{deny_approval_id}/deny",
                params={"organization_id": "org_1"},
                json={"reason": "not allowed"},
            )
            assert denied.status_code == 200
            assert denied.json()["task"]["state"] == "cancelled"
            assert denied.json()["approval"]["state"] == "denied"

    asyncio.run(run())


def test_browser_task_artifact_upload_rejects_payloads_above_runtime_limit() -> None:
    async def run() -> None:
        attachment_runtime = AttachmentRuntime(
            service=AttachmentService(InMemoryAttachmentStore(), max_file_bytes=8),
            max_workers=1,
        )
        browser_task_service = BrowserTaskService(InMemoryBrowserTaskStore())
        app = FastAPI()
        install_browser_task_router(
            app,
            browser_task_service=browser_task_service,
            attachment_runtime=attachment_runtime,
            authorize_request=lambda _request: None,
        )

        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                created = await client.post(
                    "/internal/browser-tasks",
                    json={
                        "conversation_id": "conv_operator_limit",
                        "title": "Verify invoice",
                    },
                )
                assert created.status_code == 200
                task_id = created.json()["task"]["task_id"]

                artifact = await client.post(
                    f"/internal/browser-tasks/{task_id}/artifacts",
                    params={"filename": "invoice.txt", "kind": "result_bundle"},
                    content=b"123456789",
                    headers={"Content-Type": "text/plain"},
                )
                assert artifact.status_code == 413
                assert artifact.json()["detail"] == "artifact exceeds limit of 8 bytes"
        finally:
            attachment_runtime.shutdown()

    asyncio.run(run())


def test_browser_task_create_accepts_task_pack_execution_fields() -> None:
    async def run() -> None:
        browser_task_service = BrowserTaskService(
            InMemoryBrowserTaskStore(),
            task_pack_registry=BrowserTaskPackRegistry(
                [
                    BrowserTaskPack(
                        pack_id="lookup_order",
                        version="1.0.0",
                        display_name="Lookup order",
                        allowed_domains=["merchant.example"],
                        start_url="https://merchant.example/orders",
                        credentials=[
                            BrowserCredentialRequirement(
                                kind="oauth",
                                name="merchant_connection",
                            )
                        ],
                    )
                ]
            ),
        )
        app = FastAPI()
        install_browser_task_router(
            app,
            browser_task_service=browser_task_service,
            attachment_runtime=None,
            authorize_request=lambda _request: None,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            created = await client.post(
                "/internal/browser-tasks",
                json={
                    "conversation_id": "conv_pack_api",
                    "title": "Lookup order",
                    "task_pack_id": "lookup_order",
                    "input_payload": {"order_id": "ord_123"},
                    "credential_refs": {"merchant_connection": "connection:conn_123"},
                },
            )
            assert created.status_code == 200
            task = created.json()["task"]
            assert task["task_pack_id"] == "lookup_order"
            assert task["task_pack_version"] == "1.0.0"
            assert task["start_url"] == "https://merchant.example/orders"
            assert task["input_payload"] == {"order_id": "ord_123"}
            assert task["credential_refs"] == {"merchant_connection": "connection:conn_123"}

            rejected = await client.post(
                "/internal/browser-tasks",
                json={
                    "conversation_id": "conv_pack_api",
                    "title": "Lookup order",
                    "task_pack_id": "lookup_order",
                    "start_url": "https://evil.example/orders",
                    "credential_refs": {"merchant_connection": "connection:conn_123"},
                },
            )
            assert rejected.status_code == 400
            assert rejected.json()["detail"] == "start_url must match the browser task pack allowed domains"

    asyncio.run(run())


def test_browser_task_worker_claim_lease_and_release_api() -> None:
    async def run() -> None:
        browser_task_service = BrowserTaskService(InMemoryBrowserTaskStore())
        app = FastAPI()
        install_browser_task_router(
            app,
            browser_task_service=browser_task_service,
            attachment_runtime=None,
            authorize_request=lambda _request: None,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            created = await client.post(
                "/internal/browser-tasks",
                json={
                    "conversation_id": "conv_worker_api",
                    "organization_id": "org_1",
                    "title": "Lookup order",
                },
            )
            assert created.status_code == 200
            task_id = created.json()["task"]["task_id"]

            claimed = await client.post(
                "/internal/browser-tasks/claim",
                json={"worker_id": "worker_1", "organization_id": "org_1", "lease_seconds": 30},
            )
            assert claimed.status_code == 200
            assert claimed.json()["task"]["task_id"] == task_id
            assert claimed.json()["task"]["state"] == "running"
            assert claimed.json()["task"]["lease_owner"] == "worker_1"
            assert claimed.json()["task"]["lease_expires_at"] is not None

            empty_claim = await client.post(
                "/internal/browser-tasks/claim",
                json={"worker_id": "worker_2", "organization_id": "org_1"},
            )
            assert empty_claim.status_code == 200
            assert empty_claim.json() is None

            wrong_renewal = await client.post(
                f"/internal/browser-tasks/{task_id}/lease",
                params={"organization_id": "org_1"},
                json={"worker_id": "worker_2", "lease_seconds": 60},
            )
            assert wrong_renewal.status_code == 409
            assert wrong_renewal.json()["detail"] == "browser task lease is not held by this worker"

            renewed = await client.post(
                f"/internal/browser-tasks/{task_id}/lease",
                params={"organization_id": "org_1"},
                json={"worker_id": "worker_1", "lease_seconds": 60},
            )
            assert renewed.status_code == 200
            assert renewed.json()["task"]["lease_owner"] == "worker_1"

            released = await client.post(
                f"/internal/browser-tasks/{task_id}/release",
                params={"organization_id": "org_1"},
                json={"worker_id": "worker_1"},
            )
            assert released.status_code == 200
            assert released.json()["task"]["state"] == "queued"
            assert released.json()["task"]["lease_owner"] is None

    asyncio.run(run())


def test_browser_task_worker_request_api_returns_bounded_contract() -> None:
    async def run() -> None:
        browser_task_service = BrowserTaskService(
            InMemoryBrowserTaskStore(),
            task_pack_registry=BrowserTaskPackRegistry(
                [
                    BrowserTaskPack(
                        pack_id="lookup_order",
                        version="1.0.0",
                        display_name="Lookup order",
                        allowed_domains=["merchant.example"],
                        start_url="https://merchant.example/orders",
                    )
                ]
            ),
        )
        app = FastAPI()
        install_browser_task_router(
            app,
            browser_task_service=browser_task_service,
            attachment_runtime=None,
            authorize_request=lambda _request: None,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            created = await client.post(
                "/internal/browser-tasks",
                json={
                    "conversation_id": "conv_worker_request_api",
                    "organization_id": "org_1",
                    "title": "Lookup order",
                    "task_pack_id": "lookup_order",
                    "input_payload": {"order_id": "ord_123"},
                },
            )
            assert created.status_code == 200
            task_id = created.json()["task"]["task_id"]

            claimed = await client.post(
                "/internal/browser-tasks/claim",
                json={"worker_id": "worker_1", "organization_id": "org_1"},
            )
            assert claimed.status_code == 200

            wrong_worker = await client.post(
                f"/internal/browser-tasks/{task_id}/worker-request",
                params={"organization_id": "org_1"},
                json={"worker_id": "worker_2"},
            )
            assert wrong_worker.status_code == 409
            assert wrong_worker.json()["detail"] == "browser task lease is not held by this worker"

            worker_request = await client.post(
                f"/internal/browser-tasks/{task_id}/worker-request",
                params={"organization_id": "org_1"},
                json={"worker_id": "worker_1"},
            )
            assert worker_request.status_code == 200
            payload = worker_request.json()
            assert payload["task_id"] == task_id
            assert payload["pack_id"] == "lookup_order"
            assert payload["start_url"] == "https://merchant.example/orders"
            assert payload["input"] == {"order_id": "ord_123"}
            assert payload["policy"]["allowed_domains"] == ["merchant.example"]

    asyncio.run(run())


def test_browser_task_worker_progress_and_result_api() -> None:
    async def run() -> None:
        browser_task_service = BrowserTaskService(InMemoryBrowserTaskStore())
        app = FastAPI()
        install_browser_task_router(
            app,
            browser_task_service=browser_task_service,
            attachment_runtime=None,
            authorize_request=lambda _request: None,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            created = await client.post(
                "/internal/browser-tasks",
                json={
                    "conversation_id": "conv_worker_progress_api",
                    "organization_id": "org_1",
                    "title": "Lookup order",
                },
            )
            assert created.status_code == 200
            task_id = created.json()["task"]["task_id"]

            claimed = await client.post(
                "/internal/browser-tasks/claim",
                json={"worker_id": "worker_1", "organization_id": "org_1"},
            )
            assert claimed.status_code == 200

            mismatched = await client.post(
                f"/internal/browser-tasks/{task_id}/worker-progress",
                params={"organization_id": "org_1"},
                json={
                    "worker_id": "worker_1",
                    "progress": {
                        "task_id": "wrong_task",
                        "event_sequence": 1,
                        "phase": "navigating",
                        "message": "Opening merchant portal.",
                    },
                },
            )
            assert mismatched.status_code == 400
            assert mismatched.json()["detail"] == "progress task_id does not match URL task_id"

            progress = await client.post(
                f"/internal/browser-tasks/{task_id}/worker-progress",
                params={"organization_id": "org_1"},
                json={
                    "worker_id": "worker_1",
                    "progress": {
                        "task_id": task_id,
                        "event_sequence": 1,
                        "phase": "navigating",
                        "message": "Opening merchant portal.",
                    },
                },
            )
            assert progress.status_code == 200
            assert progress.json()["recent_events"][-1]["event_type"] == "browser.worker_navigating"

            result = await client.post(
                f"/internal/browser-tasks/{task_id}/worker-result",
                params={"organization_id": "org_1"},
                json={
                    "worker_id": "worker_1",
                    "result": {
                        "task_id": task_id,
                        "success": True,
                        "summary": "Order found.",
                        "output": {"order_status": "fulfilled"},
                    },
                },
            )
            assert result.status_code == 200
            assert result.json()["task"]["state"] == "completed"
            assert result.json()["task"]["lease_owner"] is None
            assert result.json()["task"]["result"]["summary"] == "Order found."
            assert result.json()["task"]["result"]["order_status"] == "fulfilled"

            progress_after_complete = await client.post(
                f"/internal/browser-tasks/{task_id}/worker-progress",
                params={"organization_id": "org_1"},
                json={
                    "worker_id": "worker_1",
                    "progress": {
                        "task_id": task_id,
                        "event_sequence": 2,
                        "phase": "acting",
                        "message": "Trying to continue after completion.",
                    },
                },
            )
            assert progress_after_complete.status_code == 409
            assert progress_after_complete.json()["detail"] == "browser task is already completed"

    asyncio.run(run())


def test_browser_task_runtime_status_and_sweep_routes_are_worker_process_aware() -> None:
    """The runtime drains in the worker process (browser_tasks.tick); the
    status route reads the jobs table and the sweep route uses the service."""

    async def run() -> None:
        from datetime import timedelta

        from ruhu.browser_tasks import BROWSER_TASKS_JOB_TYPE
        from ruhu.browser_tasks.store import InMemoryBrowserTaskStore
        from ruhu.jobs import InMemoryJobStore, Job

        store = InMemoryBrowserTaskStore()
        browser_task_service = BrowserTaskService(store)
        jobs_store = InMemoryJobStore()
        app = FastAPI()
        install_browser_task_router(
            app,
            browser_task_service=browser_task_service,
            attachment_runtime=None,
            jobs_store=jobs_store,
            authorize_request=lambda _request: None,
        )

        stale = browser_task_service.create_task(
            conversation_id="conv_runtime_routes",
            organization_id=None,
            title="Verify invoice",
            requires_approval=True,
            approval_ttl_seconds=60,
        )
        store.save_approval(
            stale.approval.model_copy(
                update={"expires_at": stale.approval.requested_at - timedelta(seconds=1)}
            )
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            status_before = await client.get("/internal/browser-task-runtime/status")
            assert status_before.status_code == 200
            assert status_before.json() == {
                "scheduled": False,
                "last_tick_at": None,
                "last_tick_status": None,
                "last_error": None,
            }

            jobs_store.enqueue(Job(job_type=BROWSER_TASKS_JOB_TYPE, max_attempts=1))
            status_after = await client.get("/internal/browser-task-runtime/status")
            assert status_after.status_code == 200
            assert status_after.json()["scheduled"] is True

            swept = await client.post("/internal/browser-task-runtime/sweep", json={})
            assert swept.status_code == 200
            assert swept.json() == {"expired_approvals": 1}

            swept_again = await client.post("/internal/browser-task-runtime/sweep", json={})
            assert swept_again.status_code == 200
            assert swept_again.json() == {"expired_approvals": 0}

    asyncio.run(run())
